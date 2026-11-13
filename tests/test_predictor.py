"""TracerNet's predictor and artifact (docs/neural/spec.md §10.1, §10.3, §10.4, §17 item 9): loading refuses another
policy or feature-schema version; scores are calibrated probabilities with the record's columns and identities,
bit-identical on CPU; alerts follow a knob or a budget tuned from alert volume; the ensemble's logit is
differentiable through every member."""

import json
import shutil

import numpy as np
import polars as pl
import pytest
import torch

from src.models.tracernet import features as F
from src.models.tracernet.predictor import COLUMNS, History, Predictor, tune_knob, weight_schemes
from src.neural import devlib, ensemble, train as T
from src.pipeline import store

REPLICATES = {"train": [0, 1], "val": [0, 1, 2], "test": [0, 1], "test_ood": [0, 1]}
TINY = {"epoch_samples": 4096, "batch": 512, "max_epochs": 1, "schema_samples": 20000}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """A tiny store (two train scenarios, one val scenario with two scored replicates), three rung-A seeds and the
    calibrated artifact built from them on CPU."""
    d = tmp_path_factory.mktemp("pred")
    store_dir = devlib.build(d, indices=(0, 1, 7), checked=False, workers=2, replicates=REPLICATES)["store_dir"]
    config = T.load_config()
    table = T.load_train_table(store_dir)
    runs = []
    for seed in (0, 1, 2):
        r = T.train(config, rung="A", seed=seed, store_dir=store_dir, out_dir=d / "runs", run_id=f"A-{seed}",
                    runs_csv=d / "runs.csv", device="cpu", workers=1, overrides=TINY, table=table,
                    cache_root=d / "cache", features_root=d / "features", log=lambda *_: None)
        runs.append(r.run_dir)
    manifest = ensemble.build(config, runs, store_dir=store_dir, out_dir=d / "artifact", table=table, workers=1,
                              cache_root=d / "cache", device="cpu", log=lambda *_: None)
    con = store.open_observed_v2(store_dir)
    run_rows = con.execute("select run, role, split from runs order by run").fetchall()
    con.close()
    val_scored = [r for r, role, split in run_rows if split == "val" and role == "scored"]
    val_history = [r for r, role, split in run_rows if split == "val" and role == "history"]
    return {"dir": d, "store": store_dir, "artifact": d / "artifact", "manifest": manifest, "table": table,
            "val_scored": val_scored, "val_history": val_history}


@pytest.fixture(scope="module")
def predictor(built):
    return Predictor.load(built["artifact"])


@pytest.fixture(scope="module")
def risk(built, predictor):
    return predictor.score(built["store"], built["val_scored"], history=History(), workers=1)


def test_the_artifact_has_every_file_and_stays_under_10_mb(built):
    files = ("model.pt", "config.yaml", "feature_schema.json", "bins.json", "calibration.json", "manifest.json",
             "MODEL_CARD.md")
    assert all((built["artifact"] / f).exists() for f in files)
    assert sum((built["artifact"] / f).stat().st_size for f in files) <= 10e6
    m = json.loads((built["artifact"] / "manifest.json").read_text())
    for key in ("version", "created", "git_sha", "policy_hash", "library_sha256", "splits_sha256", "store_hash",
                "feature_schema_sha256", "seeds", "versions", "freeze_hash", "training_devices", "training_time_s",
                "validation", "notice"):
        assert key in m, key
    assert m["version"] == "tracernet-v1" and m["seeds"] == [0, 1, 2] and m["development"]
    assert m["validation"]["ece_after"] <= m["validation"]["ece_before"] + 1e-9
    assert "Synthetic" in (built["artifact"] / "MODEL_CARD.md").read_text()


def test_loading_under_a_different_policy_hash_refuses(built, tmp_path):
    bad = tmp_path / "artifact"
    shutil.copytree(built["artifact"], bad)
    m = json.loads((bad / "manifest.json").read_text())
    (bad / "manifest.json").write_text(json.dumps(m | {"policy_hash": "0123456789abcdef"}))
    with pytest.raises(RuntimeError, match="policy"):
        Predictor.load(bad)


def test_a_feature_schema_version_mismatch_refuses(built, tmp_path):
    bad = tmp_path / "artifact"
    shutil.copytree(built["artifact"], bad)
    s = json.loads((bad / "feature_schema.json").read_text())
    (bad / "feature_schema.json").write_text(json.dumps(s | {"version": "tracernet-features-0"}))
    with pytest.raises(RuntimeError, match="feature schema"):
        Predictor.load(bad)


def test_scores_are_calibrated_probabilities_for_every_tick_with_the_record_columns(built, predictor, risk):
    assert risk.columns == list(COLUMNS)
    t = built["table"]
    rows = np.concatenate([t.rows_of_run(r) for r in built["val_scored"]])
    rows = rows[np.asarray(t.a["scored"])[rows]]
    assert risk.height == int(t.n_ticks(rows).sum()) > 0
    first = risk.group_by("specimen_id").agg(pl.col("tick_min").min())
    assert (first["tick_min"] == 5).all()
    for col in ("p_miss", "p_late", "p_never", "p_on_time"):
        assert risk[col].min() >= 0 and risk[col].max() <= 1
    pm, pl_, pn = (risk[c].to_numpy() for c in ("p_miss", "p_late", "p_never"))
    assert np.allclose(pl_ + pn, pm, atol=1e-12, rtol=0)
    assert np.array_equal(risk["p_on_time"].to_numpy(), 1 - pm)
    assert (risk["ensemble_sd"] >= 0).all() and risk["ensemble_sd"].max() > 0
    w = weight_schemes()["default"]
    assert np.allclose(risk["harm_default"].to_numpy(), pm * risk["tier"].replace_strict(w).to_numpy())
    assert np.array_equal(risk["harm_equal"].to_numpy(), pm)
    assert risk["specimen_id"].str.contains(r"^SP-r[0-9]+-[0-9]{6}$").all()
    assert set(risk["route"].unique()) <= {"tube", "hand_carry"} and set(risk["tier"].unique()) <= {1, 2, 3}
    assert (risk["model_version"] == "tracernet-v1").all() and (risk["policy_hash"] == predictor.policy_hash).all()
    assert (risk["tick_min"] <= risk["window_min"]).all()
    q50, q90 = risk["arrival_q50_min"].drop_nulls(), risk["arrival_q90_min"].drop_nulls()
    assert len(q50) and (q90.to_numpy() >= q50.to_numpy() - 1e-9).all()
    # at the last tick of a window every remaining receipt is late
    last = risk.filter(pl.col("tick_min") == pl.col("window_min"))
    assert len(last) == 0 or np.allclose(last["p_miss"].to_numpy(), 1.0)


def test_cpu_scoring_is_bit_identical_across_calls_and_worker_counts(built, predictor, risk):
    again = predictor.score(built["store"], built["val_scored"], history=History(), workers=1)
    assert again.equals(risk)
    parallel = predictor.score(built["store"], built["val_scored"], history=History(), workers=2)
    assert parallel.equals(risk)


def test_the_history_runs_feed_the_fingerprint(built, predictor, risk):
    explicit = predictor.score(built["store"], built["val_scored"][:1], history=History(runs=tuple(built["val_history"])),
                               workers=1)
    assert explicit.equals(risk.filter(pl.col("run") == built["val_scored"][0]))
    with pytest.raises(ValueError):
        predictor.score(built["store"], built["val_scored"], history=History(store_dir=built["dir"]))


def test_alerts_respect_a_knob(predictor, risk):
    knob = 0.5
    a = predictor.alerts(risk, budget_per_shift=6, weights="default", knob=knob)
    w = weight_schemes()["default"]
    harm = risk.with_columns(h=pl.col("p_miss") * pl.col("tier").replace_strict(w, return_dtype=pl.Float64))
    expected = harm.filter(pl.col("h") >= knob).group_by("specimen_id").agg(pl.col("tick_min").min())
    got = dict(zip(a["specimen_id"].to_list(), a["tick_min"].to_list()))
    assert got == dict(zip(expected["specimen_id"].to_list(), expected["tick_min"].to_list()))
    assert (a["harm"] >= knob).all() and (a["knob"] == knob).all() and (a["method"] == "tracernet-v1").all()
    assert (a["warning_min"] == a["window_min"] - a["tick_min"]).all()
    assert (a["alert_id"] == a["specimen_id"] + "@" + a["tick_min"].cast(pl.Utf8)).all()
    assert a["notice"].str.contains("Synthetic").all()
    equal = predictor.alerts(risk, budget_per_shift=6, weights="equal", knob=knob)
    assert set(equal["specimen_id"]) == set(risk.filter(pl.col("p_miss") >= knob)["specimen_id"])


@pytest.fixture(scope="module")
def history_risk(built, predictor):
    return predictor.score(built["store"], built["val_history"], history=History(), workers=1)


def test_alerts_respect_a_budget_tuned_from_alert_volume(predictor, risk, history_risk):
    """The knob is tuned on the scenario's history (replicate 0) and applied to its scored replicates (F008); on
    the history it always meets the budget, with the no-alert knob when no knob that alerts does (F004)."""
    shifts = 21 * history_risk["run"].n_unique()
    w = weight_schemes()["default"]
    alerting = 0
    for budget in (1, 4, 6):
        knob = tune_knob(history_risk, budget, w)
        a = predictor.alerts(risk, budget_per_shift=budget, weights="default", tune_on=history_risk)
        assert (a["knob"] == knob).all()
        on_history = predictor.alerts(history_risk, budget_per_shift=budget, weights="default", knob=knob)
        assert len(on_history) / shifts <= budget
        if knob <= max(w.values()):   # a knob that alerts meets the budget: the most aggressive such knob
            alerting += 1
            looser = predictor.alerts(history_risk, budget_per_shift=budget, weights="default", knob=knob * 0.98)
            assert len(looser) / shifts > budget
        else:                          # only the knob above every attainable harm meets it: no alerts
            assert len(a) == 0
    assert alerting >= 1
    with pytest.raises(ValueError):
        predictor.alerts(risk, budget_per_shift=6)


def test_the_ensemble_logit_is_differentiable_through_every_member(built, predictor):
    t = built["table"]
    rows = t.rows_of_run(built["val_scored"][0])[:64]
    rows = rows[t.n_ticks(rows) > 0]
    batch = F.features_for_run(t, built["val_scored"][0], rows, np.full(len(rows), 5.0), predictor.schema)
    z = predictor.ensemble_logit_miss(batch)
    z.sum().backward()
    for m in predictor.members:
        assert any(p.grad is not None and p.grad.abs().sum() > 0 for p in m.parameters())
        m.zero_grad()
    with torch.no_grad():
        x = {k: torch.from_numpy(np.ascontiguousarray(batch[k])) for k in predictor.keys}
        mean = torch.stack([m(x)["p_miss"].double() for m in predictor.members]).mean(0)
    assert torch.allclose(torch.sigmoid(z.detach().double()), mean, atol=1e-6)
    inputs = predictor.embed(batch)
    assert len(inputs) == 3 and set(predictor.input_groups()) >= {"silence", "window", "lab_history"}
    out = predictor.logit_miss_embedded(inputs, x["tick"], x["window"], temperature=True)
    assert out.shape == (len(rows),) and torch.isfinite(out).all()


# --- The integrity review's findings (F001, F003, F004, F008) ------------------------------------------------------

@pytest.fixture(scope="module")
def test_store(tmp_path_factory):
    """A tiny store with a test scenario, for the lock."""
    d = tmp_path_factory.mktemp("pred-test")
    store_dir = devlib.build(d, indices=(0, 22), checked=False, workers=2, replicates=REPLICATES)["store_dir"]
    con = store.open_observed_v2(store_dir)
    runs = [r for (r,) in con.execute("select run from runs where split = 'test' and role = 'scored' order by run")
            .fetchall()]
    con.close()
    return store_dir, runs


def test_f001_the_predictor_scores_a_locked_split_only_with_the_run_the_lock_recorded(predictor, test_store, tmp_path):
    """F001: no test prediction without a recorded lock: none at all, a plain mapping, or a run recorded for the
    other locked split are refused; the run the lock recorded for test lets scoring go ahead."""
    from src.pipeline import neural_eval as ne

    store_dir, runs = test_store
    with pytest.raises(RuntimeError, match="test lock"):
        predictor.score(store_dir, runs[:1], workers=1)
    with pytest.raises(RuntimeError, match="test lock"):
        predictor.score(store_dir, runs[:1], workers=1, test_run={"split": "test", "status": "first"})
    files = {"lock_path": tmp_path / "test_lock.json", "runs_path": tmp_path / "test_runs.jsonl"}
    hashes = ne.lock_record(store_dir, [], artifact_dir=predictor.artifact_dir)
    other = ne.guard_test_run("test_ood", hashes, **files)
    with pytest.raises(ne.LockError, match="test_ood, not test"):
        predictor.score(store_dir, runs[:1], workers=1, test_run=other)
    run = ne.guard_test_run("test", hashes, **files)
    risk = predictor.score(store_dir, runs[:1], workers=1, test_run=run)
    assert risk.height > 0 and set(risk["run"].unique()) == {runs[0]}


def test_r1_a_recorded_run_scores_only_the_artifact_and_store_it_recorded(predictor, test_store, tmp_path):
    """Re-review R1: a genuine run recorded for another artifact refuses, as does a predictor not loaded from an
    artifact or a history override on a locked split."""
    from src.pipeline import neural_eval as ne
    from src.models.tracernet.predictor import Predictor

    store_dir, runs = test_store
    files = {"lock_path": tmp_path / "test_lock.json", "runs_path": tmp_path / "test_runs.jsonl"}
    run = ne.guard_test_run("test", ne.lock_record(store_dir, []), **files)     # recorded with no artifact
    with pytest.raises(ne.LockError, match="not the one this test run recorded"):
        predictor.score(store_dir, runs[:1], workers=1, test_run=run)
    bare = Predictor(predictor.members, predictor.schema, predictor.calibration)
    with pytest.raises(RuntimeError, match="loaded from an artifact"):
        bare.score(store_dir, runs[:1], workers=1, test_run=run)
    with pytest.raises(RuntimeError, match="own history replicate"):
        predictor.score(store_dir, runs[:1], workers=1, test_run=run, history=History(runs=(runs[0],)))


def test_f003_an_artifact_file_that_differs_from_its_manifest_refuses_to_load(built, tmp_path):
    """F003: compatible weights swapped in under an unchanged manifest, a missing file, or a manifest that lists no
    hashes all refuse, in Predictor.load and in the lock record."""
    from src.pipeline import neural_eval as ne

    bad = tmp_path / "artifact"
    shutil.copytree(built["artifact"], bad)
    w = torch.load(bad / "model.pt", map_location="cpu", weights_only=True)
    first = next(iter(w["members"][0]))
    w["members"][0][first] = w["members"][0][first] + 0.01
    torch.save(w, bad / "model.pt")
    with pytest.raises(RuntimeError, match="model.pt .* does not match"):
        Predictor.load(bad)
    with pytest.raises(RuntimeError, match="model.pt .* does not match"):
        ne.lock_record(built["store"], [], artifact_dir=bad)
    shutil.copy(built["artifact"] / "model.pt", bad / "model.pt")
    assert Predictor.load(bad).manifest == built["manifest"]
    (bad / "bins.json").unlink()
    with pytest.raises(RuntimeError, match="missing"):
        Predictor.load(bad)
    shutil.copy(built["artifact"] / "bins.json", bad / "bins.json")
    m = json.loads((bad / "manifest.json").read_text())
    (bad / "manifest.json").write_text(json.dumps({k: v for k, v in m.items() if k != "files"}))
    with pytest.raises(RuntimeError, match="records no SHA-256"):
        Predictor.load(bad)


def history_frame(scenario, run, replicate, n, p_miss, prefix, tier=1):
    """Risk rows of n specimens of one run, one tick each (5 minutes) with the given p_miss."""
    return pl.DataFrame({
        "specimen_id": [f"SP-r{run}-{prefix}{i:05d}" for i in range(n)], "run": [run] * n,
        "scenario_id": [scenario] * n, "replicate": [replicate] * n, "tick_min": [5] * n,
        "at": ["2026-01-05T08:00"] * n, "window_min": [60] * n, "tier": [tier] * n, "p_miss": [p_miss] * n,
        "p_never": [0.0] * n}, schema_overrides={"scenario_id": pl.Utf8, "replicate": pl.Int64})


def test_f004_tune_knob_takes_the_no_alert_knob_when_even_the_largest_weight_is_over_budget():
    """F004: 50 history specimens all reach p_miss 1 in tier 1 (harm 3 under 3-2-1), 50 alerts in 21 shifts at
    any knob that alerts, against a budget of 1 per shift: the knob is just above 3 and alerts on nothing."""
    risk = history_frame("x", 100, 0, 50, 1.0, "a")
    w = weight_schemes()["default"]
    knob = tune_knob(risk, 1, w)
    assert knob == np.nextafter(max(w.values()), np.inf)
    harm = risk["p_miss"].to_numpy() * np.array([w[t] for t in risk["tier"]])
    assert (harm >= knob).sum() == 0
    assert tune_knob(risk, 3, w) < 1e-6          # 50 alerts in 21 shifts meet 3 per shift: the lowest knob


def test_f008_alerts_tune_each_scenario_on_its_own_history_and_refuse_rows_that_are_not_history(predictor):
    """F008: scenario x's history has 40 specimens at p_miss 0.9 and y's 10 at 0.5. Tuned per scenario at 1 alert
    per shift (21 per run), x gets the no-alert knob and y alerts on all 10; pooled, x would spend y's budget and
    raise 40 alerts in 21 shifts. Scored rows are refused as tuning rows, and so is a scenario without history."""
    history = pl.concat([history_frame("x", 100, 0, 40, 0.9, "x"), history_frame("y", 110, 0, 10, 0.5, "y")])
    scored = pl.concat([history_frame("x", 101, 1, 40, 0.9, "x"), history_frame("y", 111, 1, 10, 0.5, "y")])
    a = predictor.alerts(scored, budget_per_shift=1, weights="equal", tune_on=history)
    per = {s: a.filter(pl.col("scenario_id") == s) for s in ("x", "y")}
    assert per["x"].height == 0 and per["y"].height == 10
    assert per["y"]["knob"].max() <= 0.5
    for s in ("x", "y"):
        h = history.filter(pl.col("scenario_id") == s)
        assert predictor.alerts(h, budget_per_shift=1, weights="equal", tune_on=h).height <= 21, s
    with pytest.raises(ValueError, match="not history"):
        predictor.alerts(scored, budget_per_shift=1, weights="equal", tune_on=scored)
    with pytest.raises(ValueError, match="no history rows for scenario y"):
        predictor.alerts(scored, budget_per_shift=1, weights="equal", tune_on=history.filter(pl.col("scenario_id") == "x"))
    v1 = history_frame(None, 3, None, 30, 0.9, "v")
    assert predictor.alerts(v1, budget_per_shift=1, weights="equal", tune_on=v1).height == 0   # 30 in 21 shifts
    late = v1.with_columns(pl.lit(25, dtype=pl.Int64).alias("run"))
    with pytest.raises(ValueError, match="runs 0 to 23"):
        predictor.alerts(late, budget_per_shift=1, weights="equal", tune_on=late)


def test_r4_the_v1_store_keeps_its_legacy_knob_on_saturated_risks():
    """Re-review R4: on the v1 store (no scenario) tuning keeps v1's knob, the largest weight, as the legacy
    evaluation does; a v2 scenario gets the knob just above it."""
    from src.models.tracernet import predictor as P

    rows = pl.DataFrame({"specimen_id": [f"s{i}" for i in range(50)], "run": [0] * 50, "tier": [1] * 50,
                         "p_miss": [1.0] * 50})
    scheme = {1: 1.0, 2: 1.0, 3: 1.0}
    assert P.tune_knob(rows, 1.0, scheme, no_alert=False) == 1.0
    assert P.tune_knob(rows, 1.0, scheme) > 1.0
    v1 = rows.with_columns(scenario_id=pl.lit(None, dtype=pl.Utf8), replicate=pl.lit(None, dtype=pl.Int64))
    assert P.tune_by_scenario(v1, [""], 1.0, scheme) == {"": 1.0}


def test_r5_a_p_miss_that_is_not_a_probability_is_refused():
    """Re-review R5: harm clips only float32 rounding; a value further outside [0, 1], or not finite, refuses."""
    from src.models.tracernet import predictor as P

    scheme = {1: 1.0, 2: 1.0, 3: 3.0}
    ok = pl.DataFrame({"tier": [3, 3], "p_miss": [1.0 + 1e-7, -1e-7]})
    assert P._with_harm(ok, scheme)["harm"].to_list() == [3.0, 0.0]
    for bad in (-0.2, 1.2, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="not a probability"):
            P._with_harm(pl.DataFrame({"tier": [3], "p_miss": [bad]}), scheme)


def test_an_artifact_can_be_named_for_its_version(built):
    """The v2 artifacts are built as tracernet-v2 (calibrate --version) without changing the configuration."""
    runs = sorted((built["dir"] / "runs").glob("A-*"))
    manifest = ensemble.build(T.load_config(), runs, store_dir=built["store"], out_dir=built["dir"] / "artifact-v2",
                              table=built["table"], workers=1, cache_root=built["dir"] / "cache", device="cpu",
                              version="tracernet-v2", log=lambda *_: None)
    assert manifest["version"] == "tracernet-v2"
    assert Predictor.load(built["dir"] / "artifact-v2").version == "tracernet-v2"
