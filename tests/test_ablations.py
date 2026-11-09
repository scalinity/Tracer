"""Ablations A1 to A9 (docs/neural/spec.md §8.8): input cuts that no output can see, arms with the same number of
gradient steps, the job specifications of the core and full sets, test scoring only through the lock, and the
collected record."""

import json
from pathlib import Path

import numpy as np
import pytest
import torch

from src.models.tracernet import features as F
from src.models.tracernet.net import TracerNet, TracerNetConfig
from src.neural import ablations as A, devlib, train as T

CONFIG = T.load_config()
TINY = {"epoch_samples": 2048, "batch": 512, "schema_samples": 20000, "max_epochs": 10 ** 6, "patience": 10 ** 6}
REPLICATES = {"train": [0, 1], "val": [0, 1], "test": [0, 1], "test_ood": [0, 1]}


def batch(cfg: TracerNetConfig, n: int = 8, seed: int = 0) -> dict:
    g = torch.Generator().manual_seed(seed)
    out = {"cat": torch.stack([torch.randint(0, size, (n,), generator=g) for _, size, _ in cfg.vocab], 1),
           "tests": torch.rand(n, cfg.n_tests, generator=g),
           "num": torch.randn(n, sum(k for _, k in cfg.num_groups), generator=g),
           "fingerprint": torch.randn(n, cfg.n_fingerprint, generator=g),
           "tokens": torch.randn(n, 16, cfg.token_features, generator=g),
           "token_mask": torch.rand(n, 16, generator=g) > 0.3,
           "tick": torch.full((n,), 15.0), "window": torch.full((n,), 120.0)}
    for key, channels in cfg.series_channels:
        out[key] = torch.randn(n, channels, 36, generator=g)
    return out


def perturbed(b: dict, cut: dict) -> dict:
    c = {k: v.clone() for k, v in b.items()}
    cols = [F.NUM_INDEX[name] for name in cut.get("num", ())]
    c["num"][:, cols] += 5.0
    if "fingerprint" in cut.get("branches", ()):
        c["fingerprint"] += 3.0
    if "series" in cut.get("branches", ()):
        for key in ("series_lab", "series_unit", "series_route"):
            c[key] += 2.0
    if "tokens" in cut.get("branches", ()):
        c["tokens"] += 4.0
        c["token_mask"] = ~c["token_mask"]
    return c


@pytest.mark.parametrize("rung", ["L", "A", "B", "C"])
@pytest.mark.parametrize("arm", ["no_context", "no_fingerprint", "no_roster", "no_aggregates"])
def test_a_cut_input_cannot_change_any_output_before_or_after_training(rung, arm):
    cut = A.a6_cuts(CONFIG)[arm]
    torch.manual_seed(0)
    model = T.mask_inputs(TracerNet(TracerNetConfig(rung=rung, num_groups=F.num_groups())), cut)
    b = batch(model.config)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-2, weight_decay=1e-2)
    for step in range(2):
        model.eval()
        with torch.no_grad():
            assert torch.equal(model(b)["probs"], model(perturbed(b, cut))["probs"]), (rung, arm, step)
        model.train()
        loss = model(b)["p_miss"].sum() + model(perturbed(b, cut))["p_miss"].sum()
        opt.zero_grad()
        loss.backward()
        opt.step()
    kept = {k: v.clone() for k, v in b.items()}
    kept["num"][:, F.NUM_INDEX["silence"]] += 5.0
    model.eval()
    with torch.no_grad():
        assert not torch.equal(model(b)["probs"], model(kept)["probs"])


def test_a6_cuts_follow_spec_7_4_groups():
    cuts = A.a6_cuts(CONFIG)
    aggregates = {n for n, g, _ in F.NUM_COLUMNS if g in ("batch_mates", "route_flow", "unit_flow", "lab_flow")}
    assert set(cuts["no_aggregates"]["num"]) == aggregates and "branches" not in cuts["no_aggregates"]
    assert set(cuts["no_roster"]["num"]) == {"round_interval", "no_round", "staff_this_shift", "tube_lines",
                                              "courier_routes", "roster_missing"}
    kept = {n for n, _, _ in F.NUM_COLUMNS} - set(cuts["no_context"]["num"])
    assert {"silence", "t_over_window", "log_window", "ordered", "to_shift_change_h", "route_tube"} <= kept
    assert cuts["no_context"]["branches"] == ["fingerprint", "series", "tokens"]


# --- Equal steps on a tiny store ---------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    """Two train scenarios, one validation and one test scenario, one scored replicate each."""
    d = tmp_path_factory.mktemp("tiny")
    out = devlib.build(d, indices=(0, 1, 7, 12), checked=False, workers=2, replicates=REPLICATES)
    return {"dir": d, "store": out["store_dir"], "table": T.load_train_table(out["store_dir"])}


def train_arm(tiny, name, **overrides):
    d = tiny["dir"]
    return T.train(CONFIG, rung="A", seed=0, store_dir=tiny["store"], out_dir=d / "runs", runs_csv=d / "runs.csv",
                   device="cpu", workers=1, run_id=name, overrides=TINY | {"max_steps": 9} | overrides,
                   table=tiny["table"], cache_root=d / "cache", features_root=d / "features", log=lambda *_: None)


def test_arms_take_the_same_number_of_gradient_steps(tiny):
    cut = A.a6_cuts(CONFIG)["no_context"]
    runs = {"plain": train_arm(tiny, "plain"), "cut": train_arm(tiny, "cut", input_mask=cut),
            "subset": train_arm(tiny, "subset", train_subset={"scenarios": ["dev-00-example"]})}
    assert {k: (r.steps, r.status) for k, r in runs.items()} == {k: (9, "finished") for k in runs}
    best = torch.load(Path(runs["cut"].run_dir) / "best.pt", weights_only=True)["model"]
    cfg = T.load_member(runs["cut"].run_dir)[0].config
    first = sum(d for _, _, d in cfg.vocab) + cfg.tests_dim
    cols = [first + F.NUM_INDEX[c] for c in cut["num"]]
    assert torch.count_nonzero(best["encoder.0.weight"][:, cols]) == 0
    assert torch.count_nonzero(best["fusion.0.weight"][:, cfg.width:cfg.width + cfg.fingerprint_dim]) == 0
    assert torch.count_nonzero(best["encoder.0.weight"]) > 0


def test_a_training_subset_keeps_only_its_scenarios_and_replicates(tiny):
    t = tiny["table"]
    rows = T.S.scored_rows(t, "train")
    kept = T.subset_rows(t, rows, {"scenarios": ["dev-00-example"], "replicates": [1]})
    names = [t.names["scenarios"][s] for s in np.asarray(t.a["scenario"])[kept]]
    assert 0 < len(kept) < len(rows) and set(names) == {"dev-00-example"}
    assert set(np.asarray(t.a["replicate"])[kept].tolist()) == {1}
    with pytest.raises(RuntimeError, match="lacks"):
        T.subset_rows(t, rows, {"scenarios": ["dev-99-missing"]})


# --- Job specifications ------------------------------------------------------------------------------------------------

IDS = [f"S{i:04d}" for i in range(900)]
CELLS = {s: {"split": "train", "stratum": "edge" if i < 90 else ("normal" if i < 400 else "stressed")}
         for i, s in enumerate(IDS)}


def jobs_of(set_name="core", cells=CELLS):
    return A.make_jobs(set_name, config=CONFIG, rung="B", store_dir=Path("data/store-v2"),
                       eval_store=Path("data/store-v2"), train_ids=IDS, cells=cells, a1_store=Path("data/store-v2-a1"))


def test_the_core_set_has_three_seeds_per_required_arm_and_equal_steps():
    jobs, not_run, a1 = jobs_of()
    by = {}
    for j in jobs:
        by.setdefault((j["ablation"], j["arm"]), []).append(j["seed"])
    assert by == {**{("A1", a): [0, 1, 2] for a in ("100x8", "200x4", "400x2", "800x1")},
                  ("A2", "with_edge"): [0, 1, 2], ("A2", "without_edge"): [0, 1, 2],
                  **{("A5", r): [0, 1, 2] for r in "LABC"}, ("A5", "ensemble"): [None],
                  **{("A6", a): [0, 1, 2] for a in ("no_context", "no_fingerprint", "no_roster", "no_aggregates")}}
    steps = A.step_plan(CONFIG)
    trained = [j for j in jobs if j["train"] is not None]
    assert {j["train"]["overrides"]["max_steps"] for j in trained} == {steps["steps"]}
    assert all(j["train"]["overrides"]["patience"] >= 10 ** 6 for j in trained)
    assert {j["rung"] for j in jobs if j["ablation"] in ("A1", "A2", "A6")} == {"B"}
    assert next(j for j in jobs if j["id"] == "A5-ensemble")["members"] == ["A5-B-s0", "A5-B-s1", "A5-B-s2"]
    assert set(not_run) == {"A3", "A4", "A7", "A8", "A9"} and "not in the core set" in not_run["A7"]
    assert all(why.startswith("not run") for why in not_run.values())


def test_a1_is_the_same_800_scenario_replicates_in_nested_sets():
    jobs, _, a1 = jobs_of()
    arms = {j["arm"]: j["train"]["overrides"]["train_subset"] for j in jobs if j["ablation"] == "A1" and j["seed"] == 0}
    assert {a: len(s["scenarios"]) * len(s["replicates"]) for a, s in arms.items()} == dict.fromkeys(arms, 800)
    assert set(arms["100x8"]["scenarios"]) <= set(arms["200x4"]["scenarios"]) <= set(arms["400x2"]["scenarios"]) \
        <= set(arms["800x1"]["scenarios"])
    assert sorted(collections_count(a1["replicates"].values()).items()) == [(1, 400), (2, 200), (4, 100), (8, 100)]
    assert all(j["train"]["store"] == "data/store-v2-a1" for j in jobs if j["ablation"] == "A1")


def collections_count(values):
    out = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def test_a2_arms_are_the_same_size_one_with_the_edge_stratum_one_without():
    jobs, _, _ = jobs_of()
    arms = {j["arm"]: set(j["train"]["overrides"]["train_subset"]["scenarios"]) for j in jobs
            if j["ablation"] == "A2" and j["seed"] == 0}
    edge = {s for s, c in CELLS.items() if c["stratum"] == "edge"}
    assert len(arms["with_edge"]) == len(arms["without_edge"]) == 900 - 90
    assert edge <= arms["with_edge"] and not edge & arms["without_edge"]


def test_a2_without_scenario_cells_or_with_test_cells_is_refused():
    _, not_run, _ = jobs_of(cells=None)
    assert not_run["A2"].startswith("blocked") and "scenario cells" in not_run["A2"]
    with pytest.raises(A.AblationError, match="train and val only"):
        A.check_cells({"S1": {"split": "test", "stratum": "edge"}})


def test_the_full_set_adds_the_loss_arms_at_one_seed():
    jobs, not_run, _ = jobs_of("all")
    a7 = {j["arm"]: j["train"]["overrides"] for j in jobs if j["ablation"] == "A7"}
    assert [j["seed"] for j in jobs if j["ablation"] == "A7"] == [0, 0, 0]
    assert (a7["nll_only"]["lam"], a7["nll_only"]["eta"]) == (0.0, 0.0)
    assert a7["plus_cross_entropy"]["lam"] > 0 and a7["plus_cross_entropy"]["eta"] == 0.0
    assert a7["plus_ranking"]["lam"] > 0 and a7["plus_ranking"]["eta"] > 0
    assert set(not_run) == {"A3", "A4", "A8", "A9"}


# --- Scoring through the lock ------------------------------------------------------------------------------------------

def test_an_arm_scores_test_only_after_the_main_lock_and_through_its_own(tiny, tmp_path):
    root = tmp_path / "ablations"
    job = {"id": "A6-no_fingerprint-s0", "ablation": "A6", "arm": "no_fingerprint", "seed": 0, "rung": "A",
           "train": {"store": str(tiny["store"]), "overrides": TINY | {"max_steps": 6,
                                                                       "input_mask": {"branches": ["fingerprint"]}}},
           "members": ["A6-no_fingerprint-s0"], "evaluate": ["val", "test"], "eval_store": str(tiny["store"]),
           "budgets_per_shift": [6], "budgets_per_100": [30], "resamples": 50}
    A._write(A.paths(root)["jobs"] / f"{job['id']}.json", job)
    lock, runs = tmp_path / "test_lock.json", tmp_path / "test_runs.jsonl"
    kw = dict(root=root, device="cpu", workers=1, main_lock=lock, test_runs=runs, out=lambda *_: None)
    with pytest.raises(A.AblationError, match="main test lock"):
        A.run_arm(job["id"], **kw)
    result = json.loads((root / "arms" / job["id"] / "result.json").read_text())
    assert result["train"]["steps"] == 6 and "val" in result and "test" not in result and not runs.exists()
    lock.write_text("{}")
    result = A.run_arm(job["id"], stage="score", **kw)
    logged = [json.loads(x) for x in runs.read_text().splitlines()]
    assert "test" in result and [e["status"] for e in logged if "status" in e][0] == "first"
    assert (root / "arms" / job["id"] / "test_lock.json").exists()
    assert result["test"]["evaluation"].endswith("eval/test/evaluation.json")
    A.run_arm(job["id"], stage="score", **kw)            # scored with this artifact already: no second look
    assert [json.loads(x) for x in runs.read_text().splitlines()] == logged


# --- Collecting --------------------------------------------------------------------------------------------------------

def test_collect_summarizes_seeds_checks_steps_and_records_every_ablation(tmp_path):
    root, p = tmp_path / "ablations", A.paths(tmp_path / "ablations")
    jobs = [{"id": f"A6-{arm}-s{s}", "ablation": "A6", "arm": arm, "seed": s} for arm in ("no_context",) for s in (0, 1)]
    jobs += [{"id": f"A5-B-s{s}", "ablation": "A5", "arm": "B", "seed": s} for s in (0, 1)]
    for j in jobs:
        A._write(p["jobs"] / f"{j['id']}.json", j)
        A._write(p["arms"] / j["id"] / "result.json",
                 {"train": {"steps": 100, "best_val_sel": 0.1 + 0.01 * j["seed"], "call_s": 1800},
                  "val": {"capped_mean_warning_min": 10.0 + j["seed"]}, "test": {"capped_mean_warning_min": 9.0}})
    A._write(p["index"], {"set": "core", "rung": "B", "steps": 100, "time_box_h": 6, "jobs": [j["id"] for j in jobs],
                          "not_run": {"A3": "not run: why", "A2": "blocked: no cells"}})
    control = {"settings": {"headline": "6/shift"}, "differences": {"6/shift": {"3-2-1": {"analytic_local": {
        "median_warning_all_breaches_min": {"point": 0.0, "low": 0.0, "high": 0.0},
        "capped_mean_warning_min": {"point": 7.5, "low": 2.0, "high": 12.0}}}}}}
    (tmp_path / "control.json").write_text(json.dumps(control))
    doc = A.collect(root=root, out_path=tmp_path / "ablations.json", control_path=tmp_path / "control.json",
                    out=lambda *_: None)
    assert doc["steps"] == 100 and doc["steps_equal"] and doc["compute"]["hours"] == pytest.approx(2.0)
    a6 = doc["ablations"]["A6"]
    assert a6["status"] == "done" and list(a6["arms"]) == ["full", "no_context"]
    assert a6["arms"]["no_context"]["val"]["val_sel"] == {"mean": pytest.approx(0.105), "min": 0.1, "max": 0.11, "n": 2}
    assert a6["negative_control"]["leakage_suspected"] and "treated as leakage" in a6["notes"][-1]
    assert doc["ablations"]["A3"] == {"question": "Did round 2 help?", "required": False, "status": "not run",
                                      "reason": "not run: why"}
    assert doc["ablations"]["A2"]["status"] == "not run" and doc["ablations"]["A2"]["required"]
