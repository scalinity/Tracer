"""`run --from-frozen` (docs/neural/spec.md §12.1, §17 item 18; src/neural/rebuild.py) on a tiny development world.

The "committed" side is built here: a development store with one train, val, test and test_ood scenario, a rung C
artifact of two seeds trained on CPU at small widths, and its validation evaluation. The command then rebuilds the
data into a scratch folder (the simulation is copied, since simulating the frozen library is the simulator's own
tests' business; the store is loaded for real), retrains, calibrates, scores and evaluates validation, and compares.
CPU training is bit-reproducible, so the retrained metrics equal the committed ones."""

import ast
import copy
import hashlib
import json
import shutil
import socket
from pathlib import Path

import duckdb
import pytest
import yaml

import tinynet
from src.models.tracernet import features as F
from src.neural import devlib, ensemble, pipeline, rebuild, train as T
from src.pipeline import evaluate, neural_eval as ne, store
from src.scenarios import freeze

TINY = {"epoch_samples": 4096, "batch": 512, "max_epochs": 2, "schema_samples": 20000, "width": 16,
        "temporal_channels": 8, "workers": 1}
RETRAIN_STEPS = ["simulate", "store", "features", "train-s0", "train-s1", "calibrate", "risk-retrained",
                 "evaluate-retrained"]


def snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob("*")) if p.is_file()}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    d = tmp_path_factory.mktemp("rebuild")
    data = d / "data"
    devlib.build(data / "sim", indices=tinynet.INDICES, checked=False, workers=2, replicates=tinynet.REPLICATES,
                 store_dir=data / "store-v2")
    config = T.load_config()
    config["training"] |= TINY
    config["search"]["best"] = {}
    config_path = d / "neural.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    store_dir = data / "store-v2"
    runs = [Path(T.train(config, rung="C", seed=s, store_dir=store_dir, out_dir=d / "runs", runs_csv=d / "runs.csv",
                         device="cpu", workers=1, run_id=f"C-s{s}", cache_root=d / "cache",
                         features_root=d / "features", log=lambda *_: None).run_dir) for s in (0, 1)]
    artifact = d / "committed" / "artifact"
    ensemble.build(config, runs, store_dir=store_dir, out_dir=artifact, selection={"chosen": "C", "by_val_sel": "C"},
                   workers=1, cache_root=d / "cache", device="cpu", log=lambda *_: None)
    risk = ne.risk_table("val", artifact, store_dir=store_dir, out_dir=d / "committed" / "risk", workers=1)
    ne.evaluate_split("val", ne.baselines() + [ne.tracernet_method(risk)], store_dir=store_dir,
                      out_dir=d / "committed" / "val", workers=1, matched=True)   # as `evaluate --split val --matched`
    kw = dict(artifact_dir=artifact, committed_evaluation=d / "committed" / "val" / "evaluation.json",
              config_path=config_path, workers=1, device="cpu")
    return {"dir": d, "data": data, "kw": kw, "before": {"committed": snapshot(d / "committed"),
                                                         "data": snapshot(data)}}


def guard(mp, world):
    """The rebuild's seams for this world (a copied simulation, the development library behind the store), every
    read of the store's tables and labels recorded, and the test lock, its guard and any connection refused.
    Returns the record of calls."""
    sim = world["data"] / "sim"
    calls = {k: [] for k in ("risk", "evaluate", "labels", "tables")}

    def copy_simulation(workers, out_dir, out=print):
        shutil.copytree(sim, out_dir, dirs_exist_ok=True)
        return 0
    mp.setattr(freeze, "simulate", copy_simulation)
    mp.setattr(store, "LIBRARY_V2", sim / devlib.LIBRARY_NAME)
    mp.setattr(store, "SPLITS_V2", sim / devlib.SPLITS_NAME)

    def spy(name, module, attr, key):
        fn = getattr(module, attr)

        def wrapper(*a, **k):
            calls[name].append(key(*a, **k))
            return fn(*a, **k)
        mp.setattr(module, attr, wrapper)
    spy("risk", ne, "risk_table", lambda split, *a, **k: split)
    spy("evaluate", ne, "evaluate_split", lambda split, *a, **k: split)
    spy("labels", evaluate, "load_specs_v2", lambda split, *a, **k: split)
    spy("tables", F, "load_table", lambda store_dir=None, **k: (store_dir, k.get("runs"), k.get("splits")))

    def refuse(*a, **k):
        raise AssertionError("the rebuild touched the test lock or opened a connection")
    lock = world["dir"] / "lock"
    mp.setattr(ne, "LOCK_PATH", lock / "test_lock.json")
    mp.setattr(ne, "TEST_RUNS_PATH", lock / "test_runs.jsonl")
    for name in ("guard_test_run", "record_risk_parts", "_check_run", "_lock_transaction"):
        mp.setattr(ne, name, refuse)
    mp.setattr(socket.socket, "connect", refuse)
    mp.setattr(socket, "create_connection", refuse)
    return calls


def call(scratch, world, **kw):
    lines = []
    code = rebuild.run_from_frozen(scratch, out=lines.append, **(world["kw"] | kw))
    name = rebuild.RESULT_NAMES["committed" if kw.get("evaluate_committed") else "retrain"]
    return code, json.loads((scratch / name).read_text()), lines


@pytest.fixture(scope="module")
def retrained(world):
    """The retrain in four calls on one scratch: stop after the features; train seed 1 alone (a job run beside
    others); the whole run; and the whole run again."""
    scratch, out = world["dir"] / "scratch", {}
    with pytest.MonkeyPatch.context() as mp:
        calls = guard(mp, world)
        out["features"] = call(scratch, world, until="features")
        out["seed1"] = call(scratch, world, until="train", seeds=[1])
        out["full"] = call(scratch, world)
        out["again"] = call(scratch, world)
    return {"scratch": scratch, "calls": calls, **out}


@pytest.fixture(scope="module")
def evaluated(world, retrained):
    with pytest.MonkeyPatch.context() as mp:
        calls = guard(mp, world)
        code, doc, lines = call(retrained["scratch"], world, evaluate_committed=True,
                                reference_store=world["data"] / "store-v2", reference_sim=world["data"] / "sim")
    return {"code": code, "doc": doc, "lines": lines, "calls": calls}


@pytest.fixture(scope="module")
def reused(world, retrained):
    scratch = world["dir"] / "reuse-scratch"
    with pytest.MonkeyPatch.context() as mp:
        guard(mp, world)
        code, doc, _ = call(scratch, world, evaluate_committed=True, reuse_data=world["data"])
    return {"code": code, "doc": doc, "scratch": scratch}


def names(doc):
    return [s["name"] for s in doc["steps"]]


def status(doc):
    return {s["name"]: s["status"].split(":")[0] for s in doc["steps"]}


# --- The retrain ------------------------------------------------------------------------------------------------

def test_the_steps_run_in_order_and_stop_where_until_says(retrained):
    code, doc, _ = retrained["features"]
    assert code == 0 and names(doc) == ["simulate", "store", "features"] and doc["stopped_after"] == "features"
    assert set(status(doc).values()) == {"ran"} and doc["comparisons"] == [] and doc["passed"] is None
    code, doc, _ = retrained["seed1"]
    assert code == 0 and names(doc) == ["simulate", "store", "features", "train-s1"]
    assert status(doc) == {"simulate": "reused", "store": "reused", "features": "reused", "train-s1": "ran"}
    code, doc, lines = retrained["full"]
    assert code == 0 and names(doc) == RETRAIN_STEPS and doc["passed"] is True
    assert status(doc) == {"simulate": "reused", "store": "reused", "features": "reused", "train-s0": "ran",
                           "train-s1": "reused", "calibrate": "ran", "risk-retrained": "ran",
                           "evaluate-retrained": "ran"}
    assert lines[-1].startswith("PASSED; wrote ")


def test_a_second_run_reuses_every_step_and_records_its_times_and_output_hashes(retrained):
    code, doc, _ = retrained["again"]
    assert code == 0 and names(doc) == RETRAIN_STEPS and set(status(doc).values()) == {"reused"}
    full = {s["name"]: s for s in retrained["full"][1]["steps"]}
    for s in doc["steps"]:
        assert s["outputs"] and all(len(o["sha256"]) == 64 for o in s["outputs"].values()), s["name"]
        assert s["outputs"] == full[s["name"]]["outputs"] and s["seconds"] >= 0
    assert (retrained["scratch"] / "artifact" / "manifest.json").is_file()


def test_the_retrained_artifact_matches_the_committed_configuration_and_metrics(retrained):
    doc = retrained["full"][1]
    checks = {c["name"]: c for c in doc["comparisons"]}
    gated = [name for name, _, _ in rebuild.GATED]
    assert list(checks) == ["simulation manifest", "store inputs", "training configuration", "feature schema", *gated]
    assert all(c["passed"] for c in checks.values())
    for name in gated:      # CPU training is bit-reproducible: the rebuild is the committed artifact's twin
        c = checks[name]
        assert c["tolerance"] == 0.005 and c["committed"] is not None and c["relative_difference"] == 0.0, name
    assert {r["name"] for r in doc["reported"]} >= {"val_sel of seed 0", "val_sel of seed 1"}
    assert all(r["passed"] is None and r["tolerance"] is None for r in doc["reported"])


def test_training_read_only_train_and_val_and_nothing_scored_or_evaluated_a_test_set(world, retrained):
    calls = retrained["calls"]
    assert calls["risk"] == ["val"] and calls["evaluate"] == ["val"]
    assert set(calls["labels"]) <= {"train", "val"} and "val" in calls["labels"]
    con = store.open_observed_v2(retrained["scratch"] / "store-v2")
    try:
        split_of = dict(con.execute("select run, split from runs").fetchall())
    finally:
        con.close()
    for store_dir, runs, splits in calls["tables"]:
        assert runs is not None or splits is not None, "a whole store was read"
        assert set(splits or ()) <= {"train", "val"} and {split_of[r] for r in runs or ()} <= {"train", "val"}
    assert not (world["dir"] / "lock").exists()


def test_the_rebuild_module_imports_no_client_and_opens_no_connection():
    tree = ast.parse(Path(rebuild.__file__).read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
        {f"{n.module}.{a.name}" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    banned = ("src.scenarios.client", "src.decide", "openai", "anthropic", "requests", "httpx", "urllib", "socket")
    assert not [m for m in imported if m.startswith(banned)]


# --- Evaluating the committed artifact --------------------------------------------------------------------------

def test_evaluate_committed_reproduces_the_committed_evaluation_but_for_the_three_excluded_fields(evaluated):
    doc = evaluated["doc"]
    assert evaluated["code"] == 0 and doc["passed"] is True and doc["settings"]["matched"] is True
    assert names(doc) == ["simulate", "store", "risk-committed", "evaluate-committed"]
    assert status(doc) == {"simulate": "reused", "store": "reused", "risk-committed": "ran", "evaluate-committed": "ran"}
    checks = {c["name"]: c for c in doc["comparisons"]}
    assert list(checks) == ["simulation manifest", "simulation files", "store inputs", "store tables",
                            "validation evaluation"]
    assert all(c["passed"] for c in checks.values())
    ev = checks["validation evaluation"]
    assert ev["excluded"] == ["store.path", "store.manifest_sha256", "timing"]
    assert ev["differences"] == 0 and ev["bytes_equal_after_substitution"]
    path = ev["excluded_values"]["store.path"]
    assert path["committed"] != path["rebuilt"]       # a rebuilt store lives elsewhere: the exclusion is exercised
    assert evaluated["calls"]["risk"] == ["val"] and evaluated["calls"]["evaluate"] == ["val"]


def test_validation_is_evaluated_matched_exactly_when_the_committed_evaluation_was(world, tmp_path):
    r = rebuild.Rebuild(tmp_path / "s", **world["kw"])
    r.check()
    assert r.matched is True
    doc = json.loads(world["kw"]["committed_evaluation"].read_text())
    doc.pop("matched_volume")
    plain = tmp_path / "val" / "evaluation.json"
    plain.parent.mkdir()
    plain.write_text(json.dumps(doc, indent=1) + "\n")
    r = rebuild.Rebuild(tmp_path / "s", **(world["kw"] | {"committed_evaluation": plain}))
    r.check()
    assert r.matched is False and not (tmp_path / "s").exists()


def test_the_rebuilt_store_matches_the_reference_table_by_table(evaluated):
    tables = next(c for c in evaluated["doc"]["comparisons"] if c["name"] == "store tables")["tables"]
    assert len(tables) == 10 and all(t["equal"] and t["rows"] == t["reference_rows"] for t in tables)
    files = next(c for c in evaluated["doc"]["comparisons"] if c["name"] == "simulation files")
    assert files["files"] == files["reference_files"] == files["equal"] > 0


def test_a_real_difference_in_the_committed_evaluation_is_caught(world, retrained, evaluated, tmp_path):
    raw = json.loads(world["kw"]["committed_evaluation"].read_text())
    raw["discrimination"]["tracernet"]["pooled"]["auroc"] += 1e-9
    tampered = tmp_path / "val" / "evaluation.json"
    tampered.parent.mkdir()
    tampered.write_text(json.dumps(raw, indent=1, allow_nan=False) + "\n")
    with pytest.MonkeyPatch.context() as mp:
        guard(mp, world)
        code, doc, lines = call(retrained["scratch"], world, evaluate_committed=True, committed_evaluation=tampered)
    ev = next(c for c in doc["comparisons"] if c["name"] == "validation evaluation")
    assert code == 1 and doc["passed"] is False and lines[-1].startswith("FAILED")
    assert ev["differences"] == 1 and ev["first_differences"][0]["path"] == "discrimination.tracernet.pooled.auroc"
    assert set(status(doc).values()) == {"reused"}       # the comparison alone ran again


def test_reuse_data_reads_an_existing_simulation_and_store_and_writes_nothing_there(world, reused):
    doc = reused["doc"]
    assert reused["code"] == 0 and doc["passed"] is True
    assert [s["status"] for s in doc["steps"][:2]] == [f"reused from {world['data']}"] * 2
    assert not (reused["scratch"] / "sim").exists() and not (reused["scratch"] / "store-v2").exists()
    ev = next(c for c in doc["comparisons"] if c["name"] == "validation evaluation")
    assert ev["passed"] and ev["excluded_values"]["store.path"]["committed"] == \
        ev["excluded_values"]["store.path"]["rebuilt"]


def test_no_committed_or_reused_file_changed(world, retrained, evaluated, reused):
    assert snapshot(world["dir"] / "committed") == world["before"]["committed"]
    assert snapshot(world["data"]) == world["before"]["data"]


# --- Refusals, before anything is written -----------------------------------------------------------------------

@pytest.mark.parametrize("where", ["artifact", "inside results", "the repository", "around the working store",
                                   "the reused data"])
def test_a_scratch_folder_over_committed_or_read_files_is_refused_before_any_write(world, tmp_path, where):
    kw = dict(world["kw"])
    scratch = {"artifact": kw["artifact_dir"], "inside results": rebuild.ROOT / "results" / "neural" / "rebuild-test",
               "the repository": rebuild.ROOT, "around the working store": store.STORE_V2_DIR.parent,
               "the reused data": world["data"]}[where]
    if where == "the reused data":
        kw["reuse_data"] = world["data"]
    existed = scratch.exists()
    with pytest.raises(rebuild.RebuildError, match="choose a scratch folder"):
        rebuild.run_from_frozen(scratch, out=lambda *_: None, **kw)
    assert scratch.exists() == existed and not (scratch / "stages").exists()


@pytest.mark.parametrize("kw, message", [
    ({"seeds": [0]}, "needs --until train"),
    ({"seeds": [7], "until": "train"}, "not the committed artifact's seeds"),
    ({"until": "train", "evaluate_committed": True}, "--until is one of simulate, store, evaluate"),
    ({"reuse_data": "missing"}, "is missing"),
])
def test_inconsistent_options_are_refused(world, tmp_path, kw, message):
    if kw.get("reuse_data") == "missing":
        kw = {"reuse_data": tmp_path / "nothing"}
    with pytest.raises(rebuild.RebuildError, match=message):
        rebuild.run_from_frozen(tmp_path / "scratch", out=lambda *_: None, **(world["kw"] | kw))
    assert not (tmp_path / "scratch").exists()


def test_an_artifact_of_another_policy_or_with_changed_files_is_refused(world, tmp_path):
    other = tmp_path / "artifact"
    shutil.copytree(world["kw"]["artifact_dir"], other)
    m = json.loads((other / "manifest.json").read_text())
    (other / "manifest.json").write_text(json.dumps(m | {"policy_hash": "0000000000000000"}))
    with pytest.raises(rebuild.RebuildError, match="policy"):
        rebuild.run_from_frozen(tmp_path / "s1", out=lambda *_: None, **(world["kw"] | {"artifact_dir": other}))
    (other / "manifest.json").write_text(json.dumps(m))
    (other / "calibration.json").write_text("{}")
    with pytest.raises(rebuild.RebuildError, match="calibration.json"):
        rebuild.run_from_frozen(tmp_path / "s2", out=lambda *_: None, **(world["kw"] | {"artifact_dir": other}))


# --- The comparisons --------------------------------------------------------------------------------------------

def written(doc) -> bytes:
    return (json.dumps(doc, indent=1, allow_nan=False) + "\n").encode()


EVAL = {"set": "val", "x": {"a": 1.5, "b": [1, 2]},
        "store": {"path": "/one", "policy_hash": "p", "manifest_sha256": "m1"}, "timing": {"seconds": 3.0}}


def rebuilt_with(**changes):
    doc = copy.deepcopy(EVAL)
    doc["store"] |= {"path": "/two", "manifest_sha256": "m2"}
    doc["timing"] = {"seconds": 9.5, "workers": 4}
    for path, value in changes.items():
        *head, last = path.split(".")
        node = doc
        for k in head:
            node = node[k]
        node[last] = value
    return doc


def test_the_three_excluded_fields_may_differ_and_nothing_else_may():
    ok = rebuild.compare_evaluation(written(rebuilt_with()), written(EVAL))
    assert ok["passed"] and ok["differences"] == 0 and ok["bytes_equal_after_substitution"]
    assert ok["excluded_values"]["timing"] == {"committed": {"seconds": 3.0}, "rebuilt": {"seconds": 9.5, "workers": 4}}
    for path, value in (("x.a", 1.5000001), ("store.policy_hash", "q"), ("set", "test")):
        bad = rebuild.compare_evaluation(written(rebuilt_with(**{path: value})), written(EVAL))
        assert not bad["passed"] and [d["path"] for d in bad["first_differences"]] == [path]
    bad = rebuild.compare_evaluation(written(rebuilt_with(x={"a": 1.5, "b": [1, 2, 3]})), written(EVAL))
    assert not bad["passed"] and bad["first_differences"][0]["path"] == "x.b"


def test_a_change_of_type_or_key_order_alone_fails_the_byte_comparison():
    as_float = rebuild.compare_evaluation(written(rebuilt_with(x={"a": 1.5, "b": [1.0, 2]})), written(EVAL))
    assert not as_float["passed"] and as_float["first_differences"][0]["path"] == "x.b[0]"
    reordered = rebuilt_with()
    reordered = {k: reordered[k] for k in ("x", "set", "store", "timing")}
    r = rebuild.compare_evaluation(written(reordered), written(EVAL))
    assert r["differences"] == 0 and not r["bytes_equal_after_substitution"] and not r["passed"]


def test_a_metric_passes_within_half_a_percent_only():
    assert rebuild.metric("m", "s", "p", 0.0800, 0.0803)["passed"]               # 0.375%
    over = rebuild.metric("m", "s", "p", 0.0800, 0.0805)                         # 0.625%
    assert not over["passed"] and abs(over["relative_difference"] - 0.00625) < 1e-12
    assert rebuild.metric("m", "s", "p", None, None)["passed"]
    assert not rebuild.metric("m", "s", "p", 0.08, None)["passed"]
    assert not rebuild.metric("m", "s", "p", 0.0, 0.001)["passed"]
    assert rebuild.metric("m", "s", "p", 0.0, 0.0)["passed"]
    reported = rebuild.metric("m", "s", "p", 0.08, 0.09, gated=False)
    assert reported["passed"] is None and reported["tolerance"] is None


def test_the_simulation_is_compared_by_manifest_hash_and_file_by_file():
    files = [{"path": "train/s1/rep-1.jsonl.gz", "sha256": "a"}, {"path": "val/s2/rep-1.jsonl.gz", "sha256": "b"}]
    raw = json.dumps({"files": files}).encode()
    sha = hashlib.sha256(raw).hexdigest()
    same = rebuild.compare_simulation(raw, sha, raw)
    assert [c["passed"] for c in same] == [True, True] and same[1]["equal"] == 2
    other = json.dumps({"files": [files[0], {**files[1], "sha256": "c"}]}).encode()
    found = rebuild.compare_simulation(other, sha, raw)
    assert [c["passed"] for c in found] == [False, False] and found[1]["different"] == ["val/s2/rep-1.jsonl.gz"]


def test_table_digests_ignore_row_order_and_file_layout_and_catch_one_changed_row(world, tmp_path):
    a = tmp_path / "a"
    shutil.copytree(world["data"] / "store-v2", a)
    reference = store.table_digests(world["data"] / "store-v2")
    with duckdb.connect(str(a / store.OBSERVED_NAME)) as con:   # the same rows written in another order
        con.execute("create table shuffled as select * from runs order by run desc")
        con.execute("drop table runs")
        con.execute("alter table shuffled rename to runs")
    same = rebuild.compare_tables(store.table_digests(a), reference)
    assert same["passed"] and len(same["tables"]) == 10
    with duckdb.connect(str(a / store.OBSERVED_NAME)) as con:
        con.execute("update runs set replicate = replicate + 1 where run = (select min(run) from runs)")
    found = rebuild.compare_tables(store.table_digests(a), reference)
    assert not found["passed"] and [t["table"] for t in found["tables"] if not t["equal"]] == ["runs"]
    missing = {f: dict(t) for f, t in reference.items()}
    missing[store.OBSERVED_NAME].pop("events")
    assert not rebuild.compare_tables(missing, reference)["passed"]


# --- The command line -------------------------------------------------------------------------------------------

def test_the_command_line_passes_its_options_through(monkeypatch, tmp_path):
    seen = {}

    def fake(scratch, **kw):
        seen.update(kw, scratch=scratch)
        return 0
    monkeypatch.setattr(rebuild, "run_from_frozen", fake)
    argv = ["run", "--from-frozen", "--evaluate-committed", "--out", str(tmp_path / "s"), "--reuse-data", "/x/data",
            "--reference-store", "/x/ref", "--reference-sim", "/x/sim", "--until", "train", "--seeds", "0,2",
            "--workers", "3", "--device", "cuda"]
    assert pipeline.main(argv, out=lambda *_: None) == 0
    assert seen["scratch"] == tmp_path / "s" and seen["evaluate_committed"] and seen["seeds"] == [0, 2]
    assert (seen["reuse_data"], seen["reference_store"], seen["reference_sim"]) == (Path("/x/data"), Path("/x/ref"),
                                                                                  Path("/x/sim"))
    assert (seen["until"], seen["workers"], seen["device"]) == ("train", 3, "cuda")


def test_run_needs_from_frozen_and_reports_a_refusal(monkeypatch, tmp_path, capsys):
    with pytest.raises(SystemExit) as stop:
        pipeline.main(["run", "--out", str(tmp_path)])
    assert stop.value.code == 2 and "--from-frozen" in capsys.readouterr().err

    def refuse(scratch, **kw):
        raise rebuild.RebuildError("no")
    monkeypatch.setattr(rebuild, "run_from_frozen", refuse)
    lines = []
    assert pipeline.main(["run", "--from-frozen", "--out", str(tmp_path)], out=lines.append) == 1
    assert lines == ["error: no"]
    assert "run" in pipeline.build_parser().format_help()
