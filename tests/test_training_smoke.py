"""TracerNet's training loop on a tiny store (docs/neural/spec.md §7.7, §15.2, §17 items 7 and 8): two CPU epochs
in under 60 seconds with a falling loss, a resumed run equal to an uninterrupted one, bit-reproducible CPU training,
shuffled labels at chance, the rung-selection rule of §7.9 and the search's write-back of its best values."""

import csv
import json
import time
from pathlib import Path

import numpy as np
import pytest
import torch

from src.models.tracernet import features as F
from src.neural import devlib, ensemble, hpo, samplers as S, train as T

TINY = {"epoch_samples": 4096, "batch": 512, "max_epochs": 2, "schema_samples": 20000}
REPLICATES = {"train": [0, 1], "val": [0, 1], "test": [0, 1], "test_ood": [0, 1]}


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    """Two train scenarios (example and outage-hold) and one val scenario (weekend-050), one scored replicate each."""
    d = tmp_path_factory.mktemp("tiny")
    out = devlib.build(d, indices=(0, 1, 7), checked=False, workers=2, replicates=REPLICATES)
    store_dir = out["store_dir"]
    return {"dir": d, "store": store_dir, "table": T.load_train_table(store_dir), "config": T.load_config()}


def run(tiny, name, **kw):
    d = tiny["dir"]
    overrides = TINY | kw.pop("overrides", {})
    return T.train(tiny["config"], rung=kw.pop("rung", "A"), seed=kw.pop("seed", 0), store_dir=tiny["store"],
                   out_dir=d / "runs", runs_csv=d / "runs.csv", device="cpu", workers=1, run_id=name,
                   overrides=overrides, table=tiny["table"], cache_root=d / "cache", features_root=d / "features",
                   log=lambda *_: None, **kw)


@pytest.fixture(scope="module")
def plain(tiny):
    started = time.perf_counter()
    r = run(tiny, "plain")
    return r, time.perf_counter() - started


def weights(run_dir, name="last.pt"):
    return torch.load(run_dir / name, map_location="cpu", weights_only=True)


def same_state(a: dict, b: dict) -> bool:
    return a.keys() == b.keys() and all(torch.equal(a[k], b[k]) for k in a)


def test_two_cpu_epochs_run_in_under_60_seconds_and_the_loss_falls(tiny, plain):
    r, seconds = plain
    assert seconds < 60 and r.wall_s < 60
    assert r.status == "finished" and r.epochs == 2 and r.device == "cpu"
    first, second = r.history
    assert second["train_total"] < first["train_total"]
    assert all(np.isfinite(h["val_sel"]) for h in r.history)
    assert r.best_val_sel == min(h["val_sel"] for h in r.history)
    run_dir = tiny["dir"] / "runs" / "plain"
    for name in ("config.yaml", "metrics.jsonl", "env.json", "feature_schema.json", "last.pt", "best.pt", "result.json"):
        assert (run_dir / name).exists(), name
    lines = [json.loads(x) for x in (run_dir / "metrics.jsonl").read_text().splitlines()]
    assert [x["epoch"] for x in lines] == [1, 2]
    env = json.loads((run_dir / "env.json").read_text())
    assert {"python", "torch", "device", "freeze_hash", "git_sha"} <= set(env)
    rows = list(csv.DictReader((tiny["dir"] / "runs.csv").open()))
    assert any(row["run_id"] == "plain" and row["status"] == "finished" for row in rows)


def test_cpu_training_is_bit_reproducible(tiny, plain):
    again = run(tiny, "plain-again")
    a, b = weights(tiny["dir"] / "runs" / "plain"), weights(tiny["dir"] / "runs" / "plain-again")
    assert same_state(a["model"], b["model"])
    assert [h["val_sel"] for h in plain[0].history] == [h["val_sel"] for h in again.history]
    other = run(tiny, "plain-seed-1", seed=1)
    assert not same_state(a["model"], weights(tiny["dir"] / "runs" / "plain-seed-1")["model"])
    assert other.status == "finished"


def test_a_run_interrupted_after_epoch_1_and_resumed_equals_an_uninterrupted_run(tiny, plain):
    paused = run(tiny, "resumed", stop_after_epochs=1)
    assert paused.status == "paused" and paused.epochs == 1
    with pytest.raises(RuntimeError):
        run(tiny, "resumed")   # an existing run needs resume=True
    done = run(tiny, "resumed", resume=True)
    assert done.status == "finished" and done.epochs == 2
    a, b = weights(tiny["dir"] / "runs" / "plain"), weights(tiny["dir"] / "runs" / "resumed")
    assert same_state(a["model"], b["model"])
    assert a["state"]["step"] == b["state"]["step"] and a["state"]["samples"] == b["state"]["samples"]
    opt_a, opt_b = a["optimizer"]["state"], b["optimizer"]["state"]
    assert opt_a.keys() == opt_b.keys()
    assert all(torch.equal(opt_a[k][s], opt_b[k][s]) for k in opt_a for s in ("exp_avg", "exp_avg_sq"))
    assert [h["val_sel"] for h in plain[0].history] == [h["val_sel"] for h in done.history]
    assert same_state(weights(tiny["dir"] / "runs" / "plain", "best.pt")["model"],
                      weights(tiny["dir"] / "runs" / "resumed", "best.pt")["model"])


@pytest.fixture(scope="module")
def null_store(tmp_path_factory):
    """One train scenario and two val scenarios with 8 scored replicates each: about 1,900 val specimens that
    breach in the shuffled world, enough for the AUROC to sit within about 0.01 of its mean."""
    d = tmp_path_factory.mktemp("null")
    reps = {"train": [0, 1], "val": list(range(9)), "test": [0, 1], "test_ood": [0, 1]}
    return devlib.build(d, indices=(0, 7, 8), checked=False, workers=4, replicates=reps)["store_dir"]


def val_predictions(t, run_dir, max_tick=240):
    model, _ = T.load_member(run_dir)
    schema = F.load_schema(run_dir / "feature_schema.json")
    rows, ticks = S.all_ticks(t, S.scored_rows(t, "val"), max_tick)
    with S.Workers(t, schema, 4, cache_root=run_dir.parent.parent / "cache") as pool:
        return T.predict_chunks(model, T.tick_chunks(t, pool, rows, ticks, 16384, 8), T.input_keys("A"), "cpu")


def test_shuffled_labels_give_validation_auroc_near_one_half(null_store, tmp_path):
    """Spec §17 item 7. Within each run every specimen's collection-to-receipt time is another specimen's, so an
    outcome says nothing about its inputs beyond its tick and window; everything else is rebuilt from the shuffled
    scans. A network trained on it ranks validation's (shuffled) outcomes no better than chance among specimens at
    the same tick of the same window. Pairs are compared within (window, tick) strata because the masked head gives
    every specimen of a stratum the same structural risk; pooled over strata, that structure alone ranks well."""
    config = T.load_config()
    real = T.load_train_table(null_store)
    shuffled = T.shuffled_table(real, seed=0)
    assert np.array_equal(shuffled.collected, real.collected) and not np.array_equal(
        np.nan_to_num(shuffled.received), np.nan_to_num(real.received))
    for name, table in (("shuffled", shuffled), ("real", real)):
        r = T.train(config, rung="A", seed=0, store_dir=null_store, out_dir=tmp_path / "runs", run_id=name,
                    runs_csv=tmp_path / "runs.csv", device="cpu", workers=1, table=table,
                    overrides=TINY | {"epoch_samples": 16384, "max_epochs": 3}, cache_root=tmp_path / "cache",
                    features_root=tmp_path / "features", log=lambda *_: None)
        assert r.status == "finished"
    p = val_predictions(shuffled, tmp_path / "runs" / "shuffled")
    strata = p["window"] * 10_000 + p["tick"]
    assert len(np.unique(p["specimen_index"][p["breach"]])) > 1500
    auc = T.stratified_auroc(p["breach"], p["p_miss"], strata)
    pooled = T.auroc(p["breach"], p["p_miss"])
    q = val_predictions(real, tmp_path / "runs" / "real")
    real_auc = T.stratified_auroc(q["breach"], q["p_miss"], q["window"] * 10_000 + q["tick"])
    print(f"shuffled labels: AUROC within strata {auc:.4f}, pooled {pooled:.4f}; real labels within strata "
          f"{real_auc:.4f}")
    assert auc == pytest.approx(0.5, abs=0.02), auc
    assert pooled > 0.8          # the masked head's structure alone, pooled over strata
    assert real_auc > 0.65


def test_the_schedule_warms_up_then_follows_a_cosine():
    lrs = [T.lr_at(s, 1.0, 100, 3) for s in range(110)]
    assert lrs[:3] == pytest.approx([1 / 3, 2 / 3, 1.0]) and lrs[3] == pytest.approx(1.0)
    assert all(a >= b for a, b in zip(lrs[3:], lrs[4:])) and lrs[100] == pytest.approx(0.0) and lrs[109] == 0.0


def test_the_selection_rule_takes_the_simpler_rung_within_one_percent_then_checks_warning():
    vs = {"A": 0.1000, "B": 0.0995, "C": 0.0900}
    assert ensemble.choose_rung(vs, 0.01) == "C"
    assert ensemble.choose_rung({"A": 0.1000, "B": 0.0995, "C": 0.0992}, 0.01) == "A"
    assert ensemble.choose_rung({"A": 0.1200, "B": 0.1000, "C": 0.0995}, 0.01) == "B"
    assert ensemble.choose_rung({"L": 0.01, "A": 0.1, "B": 0.2}, 0.01, ladder=("A", "B", "C")) == "A"
    warnings = {"A": 40.0, "C": 34.0}
    rec = ensemble.apply_warning_check("C", lambda rung: warnings[rung], budget=6, margin=5)
    assert rec["chosen"] == "A" and rec["warning"]["below_a_min"] == pytest.approx(6.0)
    warnings["C"] = 36.0
    assert ensemble.apply_warning_check("C", lambda rung: warnings[rung], budget=6, margin=5)["chosen"] == "C"
    stub = ensemble.apply_warning_check("C", lambda rung: None, budget=6, margin=5)
    assert stub["chosen"] == "C" and stub["warning"]["status"] == "not run"


def test_the_search_writes_its_best_values_into_the_config_and_keeps_its_comments(tmp_path):
    path = tmp_path / "neural.yaml"
    path.write_text(T.CONFIG_PATH.read_text())
    best = {"learning_rate": 0.0012, "weight_decay": 3e-05, "dropout": 0.15, "width": 192, "temporal_channels": 48,
            "lam": 0.5, "eta": 0.1, "batch": 4096}
    hpo.write_best(best, path)
    hpo.write_best(best | {"weight_decay": 1e-06}, path)
    config = T.load_config(path)
    assert config["search"]["best"] == best | {"weight_decay": 1e-06}
    tcfg = T.training_config(config)
    assert (tcfg["width"], tcfg["weight_decay"], tcfg["patience"]) == (192, 1e-06, 4)
    assert config["simulation"] == T.load_config()["simulation"]
    assert "# spec §7.7" in path.read_text()


# --- The integrity review's findings (F006, F007) ------------------------------------------------------------------

def table_copy(t, **arrays):
    a = {k: np.array(v) for k, v in t.a.items()}
    a.update(arrays)
    return F.Table(a, t.names, json.loads(json.dumps(t.rosters)), dict(t.meta), {k: np.array(v) for k, v in t.hist.items()})


def test_f006_the_worker_cache_key_covers_every_array_the_rosters_and_a_transform(tiny, tmp_path):
    """F006: the cache the workers memory-map is keyed by the table's whole content. A receipt changed at a row the
    old key never sampled, a roster, a history fingerprint and the shuffled-label control each give another key
    and another cache, and the workers then read the changed content."""
    t = tiny["table"]
    base = S.table_digest(t)
    assert S.table_digest(table_copy(t)) == base
    received = np.array(t.a["received"])
    i = next(i for i in range(1, len(received)) if i % 97 and np.isfinite(received[i]))
    received[i] += 60.0
    changed = table_copy(t, received=received)
    assert S.table_digest(changed) != base
    roster = table_copy(t)
    first = next(iter(roster.rosters))
    roster.rosters[first] = {**roster.rosters[first], "shift_pattern": "changed"}
    fingerprint = table_copy(t)
    fingerprint.hist["h_lab"][0, 0] += 1.0
    shuffled = T.shuffled_table(t, seed=0)
    keys = [S.table_digest(x) for x in (changed, roster, fingerprint, shuffled)]
    assert base not in keys and len(set(keys)) == 4
    assert shuffled.meta.get("transform") == "shuffled-label control, seed 0"
    old, new = S.cached_table_dir(t, tmp_path), S.cached_table_dir(changed, tmp_path)
    assert old != new
    loaded = F.Table.load(new)
    assert loaded.received[i] == received[i] != F.Table.load(old).received[i]


def fake_runs(tmp_path, monkeypatch, val_sel):
    """Run folders for rungs with the given val_sel; loading a run's result or predictor reads nothing else."""
    runs = {}
    for rung, v in val_sel.items():
        (tmp_path / rung).mkdir()
        runs[rung] = tmp_path / rung
    results = {str(d): {"run_id": f"{r}-s0", "rung": r, "best_val_sel": val_sel[r], "best_epoch": 1, "epochs": 2,
                        "status": "finished", "seed": 0, "device": "cpu", "store": {"store_hash": "h"}}
               for r, d in runs.items()}
    monkeypatch.setattr(ensemble.T, "load_result", lambda d: results[str(d)])
    monkeypatch.setattr(ensemble, "predict_fn_of", lambda d: Path(d).name)   # the rung stands for its predictor
    return runs


def test_f007_selection_fails_loudly_rather_than_keep_b_or_c_with_the_warning_check_unrun(tmp_path, monkeypatch):
    """F007: when the lowest val_sel picks rung B or C, the warning check against rung A must run: with no
    warning for either rung (or no rung-A run), selection raises and writes nothing; with both, the rule of §7.9
    applies and is recorded."""
    config = T.load_config()
    runs = fake_runs(tmp_path, monkeypatch, {"A": 0.12, "B": 0.10})
    out = tmp_path / "selection.json"
    with pytest.raises(RuntimeError, match="warning check against rung A did not run"):
        ensemble.select(config, runs, out_path=out, median_warning=lambda fn: None, log=lambda *_: None)
    with pytest.raises(RuntimeError, match="no median warning for rung A"):
        ensemble.select(config, {"B": runs["B"]}, out_path=out, median_warning=lambda fn: 40.0, log=lambda *_: None)
    assert not out.exists()
    rec = ensemble.select(config, runs, out_path=out, median_warning={"A": 40.0, "B": 30.0}.get, log=lambda *_: None)
    assert rec["by_val_sel"] == "B" and rec["chosen"] == "A" and rec["warning_check"]["status"] == "run"
    assert json.loads(out.read_text())["chosen"] == "A"


def test_f007_the_select_command_uses_the_drivers_validation_warning_check(tiny, tmp_path, monkeypatch):
    """F007: `select` (both command routes call select_command) runs the evaluation driver's warning check on the
    store it selects for, instead of leaving it unrun."""
    from src.pipeline import neural_eval as ne

    asked = []

    def driver(store_dir, **kw):
        asked.append(Path(store_dir))
        return {"A": 40.0, "B": 38.0}.get
    monkeypatch.setattr(ne, "median_warning", driver, raising=False)
    runs = fake_runs(tmp_path, monkeypatch, {"A": 0.12, "B": 0.10})
    out = tmp_path / "selection.json"
    assert ensemble.select_command(store_dir=tiny["store"], runs=[runs["A"], runs["B"]], selection_path=out,
                                   out=lambda *_: None) == 0
    rec = json.loads(out.read_text())
    assert asked == [Path(tiny["store"])] and rec["chosen"] == "B" and rec["warning_check"]["status"] == "run"


def test_f007_the_validation_warning_scores_val_alone_and_runs_the_driver(tiny, plain):
    """F007: the driver's check scores only the val runs with the rung's predictor and returns the median warning
    over all breaches at 6 alerts per shift under the 3-2-1 weights."""
    from src.pipeline import neural_eval as ne
    from src.pipeline import store

    r, _ = plain
    asked = []
    predict = ensemble.predict_fn_of(r.run_dir)

    def spy(store_dir, runs, **kw):
        asked.extend(runs)
        return predict(store_dir, runs, **kw)
    w = ne.median_warning(tiny["store"], workers=1)(spy)
    con = store.open_observed_v2(tiny["store"])
    val = {run for (run,) in con.execute("select run from runs where split = 'val'").fetchall()}
    con.close()
    assert set(asked) == val and isinstance(w, float) and 0 <= w <= 24 * 60


def test_val_sel_samples_a_fixed_set_of_specimens_above_its_limit(tiny):
    """The val_sel subset keeps at most `limit` specimens, the same ones on every call, chosen by a salted hash."""
    from src.neural import samplers as S
    t = tiny["table"]
    every = S.val_sel_rows(t, "val", limit=10 ** 9)
    some = S.val_sel_rows(t, "val", limit=len(every) // 3)
    assert len(some) == len(every) // 3 and set(some) <= set(every)
    assert np.array_equal(some, S.val_sel_rows(t, "val", limit=len(every) // 3))


def test_a_fast_first_epoch_is_replanned_to_the_target_length_once():
    """Spec §7.7 wants epochs of about epoch_minutes; the start-up benchmark can underestimate speed many times
    over, so a first epoch under half the target resizes the rest (capped at one full draw), once, and only for a
    run whose size was computed (no fixed size, sample cap or time box)."""
    from types import SimpleNamespace
    import numpy as np
    tcfg = {"epoch_minutes": 10, "epoch_samples": None, "batch": 2048, "max_epochs": 30}

    def fresh():
        return {"epoch_samples": 2_486_298, "steps_per_epoch": 1215, "planned_steps": 36_450,
                "specimens_per_epoch": 900_000, "mean_ticks_per_specimen": 2.76}
    sampler = SimpleNamespace(rows=np.arange(16_000_000), k=4, specimens=900_000)
    plan = fresh()
    assert T.replan_after_first_epoch(plan, sampler, tcfg, 31.0, 2_486_298, None, None, lambda *_: None)
    assert plan["epoch_samples"] == int(2_486_298 * 600 / 31.0) and plan["planned_steps"] == 30 * plan["steps_per_epoch"]
    assert sampler.specimens == plan["specimens_per_epoch"] and plan["replanned"]["before"]["epoch_samples"] == 2_486_298
    assert not T.replan_after_first_epoch(plan, sampler, tcfg, 31.0, plan["epoch_samples"], None, None, lambda *_: None)
    small = SimpleNamespace(rows=np.arange(1000), k=4, specimens=0)
    plan = fresh()
    T.replan_after_first_epoch(plan, small, tcfg, 1.0, 2_486_298, None, None, lambda *_: None)
    assert "replanned" not in plan or plan["epoch_samples"] <= 4000
    for kw in ({"max_samples": 1_500_000, "time_box_s": None}, {"max_samples": None, "time_box_s": 600.0}):
        plan = fresh()
        assert not T.replan_after_first_epoch(plan, sampler, tcfg, 31.0, 2_486_298, kw["max_samples"], kw["time_box_s"],
                                              lambda *_: None)
    plan = fresh()
    assert not T.replan_after_first_epoch(plan, sampler, tcfg | {"epoch_samples": 4096}, 31.0, 4096, None, None,
                                          lambda *_: None)
    assert not T.replan_after_first_epoch(fresh(), sampler, tcfg, 400.0, 2_486_298, None, None, lambda *_: None)
