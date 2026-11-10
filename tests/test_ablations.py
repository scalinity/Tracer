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
from src.pipeline import store
from src.scenarios import freeze, runner

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
    assert result["val"]["scenarios_scored"] == 1                  # the scoring share of a one-scenario set
    result = A.run_arm(job["id"], stage="score", splits=("val",), **kw)    # --evaluate val: test is not tried
    assert "test" not in result and not runs.exists()
    lock.write_text("{}")
    result = A.run_arm(job["id"], stage="score", **kw)
    logged = [json.loads(x) for x in runs.read_text().splitlines()]
    assert "test" in result and [e["status"] for e in logged if "status" in e][0] == "first"
    assert (root / "arms" / job["id"] / "test_lock.json").exists()
    assert result["test"]["evaluation"].endswith("eval/test/evaluation.json")
    A.run_arm(job["id"], stage="score", **kw)            # scored with this artifact already: no second look
    assert [json.loads(x) for x in runs.read_text().splitlines()] == logged


def test_a_cached_score_on_another_selection_is_rescored_on_val_and_refused_on_test(tiny, tmp_path):
    root = tmp_path / "ablations"
    job = {"id": "A6-no_roster-s0", "ablation": "A6", "arm": "no_roster", "seed": 0, "rung": "A",
           "train": {"store": str(tiny["store"]), "overrides": TINY | {"max_steps": 6}},
           "members": ["A6-no_roster-s0"], "evaluate": ["val", "test"], "eval_store": str(tiny["store"]),
           "budgets_per_shift": [6], "budgets_per_100": [30], "resamples": 50}
    A._write(A.paths(root)["jobs"] / f"{job['id']}.json", job)
    lock, runs = tmp_path / "test_lock.json", tmp_path / "test_runs.jsonl"
    lock.write_text("{}")
    kw = dict(root=root, device="cpu", workers=1, main_lock=lock, test_runs=runs, out=lambda *_: None)
    first = A.run_arm(job["id"], **kw)
    assert first["val"]["score_share"] == CONFIG["ablations"]["score_share"] and first["test"]["selection_sha256"]
    whole = tmp_path / "neural.yaml"
    whole.write_text(T.CONFIG_PATH.read_text().replace("score_share: 0.25", "score_share: 1.0"))
    with pytest.raises(A.AblationError, match="another scenario selection"):
        A.run_arm(job["id"], stage="score", config_path=whole, **kw)
    again = json.loads((root / "arms" / job["id"] / "result.json").read_text())
    assert again["val"]["selection_sha256"] != first["val"]["selection_sha256"] and again["val"]["score_share"] == 1.0
    assert again["test"] == first["test"]                # the test look stands until a relock reason is given
    record = A.scoring_record({"x": first, "y": again})
    assert record["val"]["same_selection"] is False and record["test"]["same_selection"] is True


def test_arms_are_scored_on_one_fixed_salted_share_of_each_set(tiny):
    config = {"ablations": {**CONFIG["ablations"], "score_share": 0.5, "score_salt": "a-salt"}}
    chosen = A.score_scenarios(tiny["store"], "train", config)
    ids = A.train_scenarios(tiny["store"])
    assert chosen == sorted(A._salted(ids, "a-salt")[:1]) == A.score_scenarios(tiny["store"], "train", config)
    assert A.score_scenarios(tiny["store"], "train", {"ablations": {**config["ablations"], "score_share": 1.0}}) is None
    with pytest.raises(A.AblationError, match="no test_ood scenarios"):
        A.score_scenarios(tiny["store"], "test_ood", config)


def test_a_risk_table_refuses_scenarios_outside_its_set_before_loading_the_artifact(tiny, tmp_path):
    from src.pipeline import neural_eval as ne
    for scenarios, message in ((["nope"], "not among"), ([], "empty")):
        with pytest.raises(ValueError, match=message):
            ne.risk_table("val", tmp_path / "no-artifact", store_dir=tiny["store"], out_dir=tmp_path / "risk",
                          scenarios=scenarios)


# --- Collecting --------------------------------------------------------------------------------------------------------

def test_collect_summarizes_seeds_checks_steps_and_records_every_ablation(tmp_path):
    root, p = tmp_path / "ablations", A.paths(tmp_path / "ablations")
    jobs = [{"id": f"A6-{arm}-s{s}", "ablation": "A6", "arm": arm, "seed": s} for arm in ("no_context",) for s in (0, 1)]
    jobs += [{"id": f"A5-B-s{s}", "ablation": "A5", "arm": "B", "seed": s} for s in (0, 1)]
    scored = {"evaluation": "eval.json", "score_s": 0.0, "scenarios_scored": 3, "selection_sha256": "abc",
              "score_share": 0.25, "score_salt": "tracer-score-v1"}      # the metadata run_arm records with a score
    for j in jobs:
        A._write(p["jobs"] / f"{j['id']}.json", j)
        A._write(p["arms"] / j["id"] / "result.json",
                 {"train": {"steps": 100, "best_val_sel": 0.1 + 0.01 * j["seed"], "call_s": 1800},
                  "val": {"capped_mean_warning_min": 10.0 + j["seed"]} | scored,
                  "test": {"capped_mean_warning_min": 9.0} | scored})
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
    assert "score_salt" not in a6["arms"]["no_context"]["val"] and doc["scoring"]["test"]["same_selection"]
    assert doc["scoring"]["val"]["shares"] == [0.25] and doc["scoring"]["val"]["arms"] == 4


# --- A1's store ----------------------------------------------------------------------------------------------------------

A1_PLAN = {"replicates": {"dev-00-example": 3}}


@pytest.fixture(scope="module")
def a1_stores(tmp_path_factory):
    """One train and one validation scenario simulated as the main simulation does (train replicates 0 to 2,
    validation 0 to 3) into a store, and A1's simulation and store for a plan giving the train scenario three scored
    replicates."""
    d = tmp_path_factory.mktemp("a1")
    main = devlib.build(d / "main", indices=(0, 7), checked=False, workers=2, replicates=freeze.REPLICATES)
    library = d / "main" / devlib.LIBRARY_NAME
    records, sha = store.read_library(library)
    manifest = freeze.simulate_replicates(records, freeze.a1_replicates(records, A1_PLAN), d / "sim-a1", 2,
                                          library_sha256=sha)
    store.build_v2(d / "sim-a1", library, d / "main" / devlib.SPLITS_NAME, d / "store-a1", workers=2)
    return {"main_sim": d / "main", "main_store": main["store_dir"], "a1_sim": d / "sim-a1", "a1_store": d / "store-a1",
            "manifest": manifest}


def files_of(sim_dir):
    return {f["path"]: f["sha256"] for f in json.loads((sim_dir / "manifest.json").read_text())["files"]}


def test_the_a1_plan_takes_train_scenarios_and_validation_as_the_main_simulation_has_it():
    scenarios = [{"scenario_id": s, "split": sp} for s, sp in (("t1", "train"), ("t2", "train"), ("v1", "val"),
                                                                ("x1", "test"), ("o1", "test_ood"))]
    assert freeze.a1_replicates(scenarios, {"replicates": {"t1": 2}}) == {"t1": [0, 1, 2], "v1": [0, 1, 2, 3]}
    for plan in ({"x1": 1}, {"missing": 1}, {"t1": 0}):
        with pytest.raises(runner.JobError):
            freeze.a1_replicates(scenarios, {"replicates": plan})
    assert store.STORE_V2_A1_DIR == store.ROOT / CONFIG["ablations"]["a1"]["store"]
    assert freeze.A1_PLAN_PATH == A.paths()["a1"] and freeze.A1_SIM_DIR == store.SIM_A1_DIR


def test_a_replicate_both_simulations_hold_is_byte_identical(a1_stores):
    """The simulator seeds a replicate from (master seed, scenario index, replicate) alone."""
    main, a1 = files_of(a1_stores["main_sim"]), files_of(a1_stores["a1_sim"])

    def replicate_files(folder, replicates):
        return {f"{folder}/{kind}-{r}.{ext}" for r in replicates
                for kind, ext in (("rep", "jsonl.gz"), ("disruptions-rep", "json"))}
    both = set(main) & set(a1)
    assert replicate_files("train/dev-00-example", (0, 1, 2)) | replicate_files("val/dev-07-weekend-050", range(4)) \
        | {"train/dev-00-example/roster.json", "val/dev-07-weekend-050/roster.json"} == both
    for path in sorted(both):
        assert (a1_stores["a1_sim"] / path).read_bytes() == (a1_stores["main_sim"] / path).read_bytes(), path
    assert set(a1) - set(main) == {"train/dev-00-example/rep-3.jsonl.gz", "train/dev-00-example/disruptions-rep-3.json"}
    m = a1_stores["manifest"]
    assert m["replicates_written"] == 8 and m["replicates_dropped"] == 0 and len(m["runs"]) == 8


def test_the_a1_store_holds_the_plan_and_exactly_the_main_stores_validation(a1_stores):
    import duckdb

    con = duckdb.connect()
    for alias, key in (("m", "main_store"), ("a", "a1_store")):
        for name in ("observed", "truth"):
            con.execute(f"attach '{a1_stores[key] / f'{name}.duckdb'}' as {alias}_{name} (read_only)")
    queries = {
        "specimens": "select * from {o}.specimens where split = 'val'",
        "events": "select e.* from {o}.events e join {o}.specimens s using (specimen_id) where s.split = 'val'",
        "runs": "select * from {o}.runs where split = 'val'",
        "roster": "select * from {o}.roster where scenario_id in (select scenario_id from {o}.runs where split = 'val')",
        "truth_specimens": "select * from {t}.truth_specimens where split = 'val'",
        "truth_segments": "select g.* from {t}.truth_segments g join {t}.truth_specimens s using (specimen_id) "
                          "where s.split = 'val'",
        "truth_disruptions": "select * from {t}.truth_disruptions where scenario_id in "
                             "(select scenario_id from {t}.truth_scenarios where split = 'val')",
        "truth_scenarios": "select * from {t}.truth_scenarios where split = 'val'"}
    for name, q in queries.items():
        m, a = q.format(o="m_observed", t="m_truth"), q.format(o="a_observed", t="a_truth")
        rows = con.execute(f"select count(*) from ({m})").fetchone()[0]
        only = con.execute(f"select (select count(*) from (({m}) except all ({a}))), "
                           f"(select count(*) from (({a}) except all ({m})))").fetchone()
        assert rows > 0 and only == (0, 0), (name, rows, only)
    train = con.execute("select scenario_id, replicate from a_observed.runs where split = 'train' order by 2").fetchall()
    assert train == [("dev-00-example", r) for r in range(4)]
    A.check_a1_store(a1_stores["a1_store"], A1_PLAN)
    with pytest.raises(A.AblationError, match="lacks 1 and adds 0"):
        A.check_a1_store(a1_stores["main_store"], A1_PLAN)


def test_an_a1_arm_refuses_to_train_without_the_a1_store(a1_stores, tmp_path):
    root = tmp_path / "ablations"
    p = A.paths(root)
    job = {"id": "A1-1x3-s0", "ablation": "A1", "arm": "1x3", "seed": 0, "rung": "A",
           "train": {"store": str(a1_stores["main_store"]),
                     "overrides": TINY | {"max_steps": 1, "train_subset": {"scenarios": ["dev-00-example"],
                                                                           "replicates": [1, 2, 3]}}},
           "members": ["A1-1x3-s0"], "evaluate": ["val"], "eval_store": str(a1_stores["main_store"]),
           "budgets_per_shift": [6], "budgets_per_100": [30], "resamples": 50}
    A._write(p["jobs"] / f"{job['id']}.json", job)
    kw = dict(root=root, stage="train", device="cpu", workers=1, out=lambda *_: None)
    with pytest.raises(A.AblationError, match="no plan"):
        A.run_arm(job["id"], **kw)
    A._write(p["a1"], A1_PLAN)
    with pytest.raises(A.AblationError, match="not the A1 store"):
        A.run_arm(job["id"], **kw)
    with pytest.raises(A.AblationError, match="no observed store"):
        A.run_arm(job["id"], train_store=tmp_path / "nowhere", **kw)
    assert not p["runs"].exists() and not p["arms"].exists()
