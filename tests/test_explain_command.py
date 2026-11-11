"""The `explain` and `export` pipeline commands (docs/neural/spec.md §9.4, §12.1, §8.7). `explain` runs the checks on
a set's TracerNet alerts at 6 per shift from the set's risk table, with each specimen's tier and window joined from
the observed store. On a test set it is a later look: it refuses without the lock or without TracerNet's recorded
risk table, is logged as a repeat when every hash matches the look that scored the set, and refuses without logging
anything when a hash or the risk table differs. `export` hands every argument to src.api.export_neural."""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

import tinynet as T
from src.models.tracernet.predictor import HISTORY_REPLICATE, Predictor
from src.neural import pipeline
from src.pipeline import neural_eval as ne, store
from test_neural_eval import fake_artifact


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    d = T.build_store(tmp_path_factory.mktemp("explain-command"))
    return {"store": d, "pred": T.predictor(d)}


@pytest.fixture
def env(tiny, tmp_path, monkeypatch):
    """The test lock, its run log and the risk tables under tmp_path; Predictor.load gives the tiny network as if
    loaded from a fake artifact folder, so a recorded run checks that folder's hashes."""
    artifact = fake_artifact(tmp_path / "artifact")
    p = tiny["pred"]
    loaded = Predictor(p.members, p.schema, p.calibration, manifest=p.manifest)
    loaded.artifact_dir = artifact
    monkeypatch.setattr(Predictor, "load", classmethod(lambda cls, path=None, device="cpu": loaded))
    lock = {"lock_path": tmp_path / "lock" / "test_lock.json", "runs_path": tmp_path / "lock" / "test_runs.jsonl"}
    monkeypatch.setattr(ne, "LOCK_PATH", lock["lock_path"])
    monkeypatch.setattr(ne, "TEST_RUNS_PATH", lock["runs_path"])
    monkeypatch.setattr(ne, "RISK_DIR", tmp_path / "risk")
    lines = []

    def cli(split, *more):
        lines.clear()
        code = pipeline.main(["explain", "--split", split, "--store", str(tiny["store"]), "--artifact", str(artifact),
                              "--limit", "4", "--out", str(tmp_path / "explanations.json"), *more], out=lines.append)
        return code, "\n".join(lines)

    def look(split):
        """The look that scores a test set: `evaluate --split SPLIT --artifact ...`, which takes the lock, writes
        the risk table under RISK_DIR and records its parts."""
        assert ne.command_evaluate(split, store_dir=tiny["store"], out_dir=tmp_path / "out" / split, workers=1,
                                   artifact_dir=artifact) == 0

    return SimpleNamespace(store=tiny["store"], pred=loaded, artifact=artifact, lock=lock, cli=cli, look=look,
                           out=tmp_path / "explanations.json", tmp=tmp_path)


def logged(lock):
    path = lock["runs_path"]
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def val_table(env) -> str:
    """The validation risk table as `evaluate --split val --artifact` writes it (val is not locked)."""
    return str(ne.risk_table("val", env.artifact, store_dir=env.store, out_dir=ne.RISK_DIR / "val", workers=1))


# --- The tier and window join ----------------------------------------------------------------------------------------

def test_the_tier_and_window_come_from_the_store_and_give_the_predictors_own_alerts(env):
    parts = ne.FrameRisk(val_table(env))
    parts.pin()
    risk = pipeline.alert_inputs(parts.files, env.store, "val")
    con = store.open_observed_v2(env.store)
    rows = con.execute("select specimen_id, urgency_tier, window_min from specimens where split = 'val'").fetchall()
    con.close()
    want = {sid: (tier, window) for sid, tier, window in rows}
    assert risk.height > 0 and risk["at"].null_count() == risk["p_never"].null_count() == risk.height
    for sid, tier, window in risk.select("specimen_id", "tier", "window_min").unique().iter_rows():
        assert want[sid] == (tier, window), sid
    # The slim table plus the join gives the alerts the predictor's own full record gives.
    full = env.pred.score(env.store, T.runs(env.store, "val"), workers=1)
    history = pl.col("replicate") == HISTORY_REPLICATE
    joined = full.select("specimen_id", "tick_min", "tier", "window_min").join(
        risk.select("specimen_id", "tick_min", "tier", "window_min"), on=["specimen_id", "tick_min"], suffix="_joined")
    assert joined.height == full.height == risk.height
    assert (joined["tier"] == joined["tier_joined"]).all() and (joined["window_min"] == joined["window_min_joined"]).all()
    a, b = (env.pred.alerts(r.filter(~history), budget_per_shift=6, tune_on=r.filter(history)) for r in (full, risk))
    keep = ("alert_id", "knob", "harm", "p_miss", "warning_min")    # alert_at is empty in the slim table, so sort
    assert a.height > 0 and a.select(keep).sort("alert_id").equals(b.select(keep).sort("alert_id"))


def test_a_risk_table_of_another_set_is_refused(env):
    parts = ne.FrameRisk(val_table(env))
    parts.pin()
    with pytest.raises(ValueError, match="not test specimens"):
        pipeline.alert_inputs(parts.files, env.store, "test")


# --- explain on validation -------------------------------------------------------------------------------------------

def test_explain_on_validation_writes_the_checks_file_and_takes_no_lock(env):
    val_table(env)
    code, out = env.cli("val")
    assert code == 0, out
    doc = json.loads(env.out.read_text())
    assert doc["set"] == "val" and doc["alerts"] == 4 and doc["alerts_given"] > 4
    assert doc["budget_per_shift"] == 6 and doc["weights"] == "default"
    assert set(doc["checks"]) == {"completeness", "deletion", "randomization"}
    assert "val run" not in out and f"wrote {env.out}" in out
    assert not env.lock["lock_path"].exists() and not env.lock["runs_path"].exists()


def test_a_missing_risk_table_is_refused(env):
    code, out = env.cli("val")
    assert code == 1 and "no risk table" in out and not env.out.exists()


# --- explain on a locked set -----------------------------------------------------------------------------------------

def test_a_locked_set_without_a_lock_is_refused_and_takes_none(env):
    val_table(env)
    shutil.copytree(ne.RISK_DIR / "val", ne.RISK_DIR / "test")     # a table to read, so only the lock is missing
    code, out = env.cli("test")
    assert code == 1 and "no test lock" in out
    assert not env.lock["lock_path"].exists() and not env.lock["runs_path"].exists() and not env.out.exists()


@pytest.mark.parametrize("split", ne.LOCKED_SPLITS)
def test_a_repeat_of_the_locked_look_is_logged_and_writes_the_checks(env, split):
    env.look(split)
    before = env.lock["lock_path"].read_text()
    statuses = [e["status"] for e in logged(env.lock)]
    assert statuses == ["first", "risk table recorded"]
    code, out = env.cli(split)
    assert code == 0, out
    log = logged(env.lock)
    assert [e["status"] for e in log] == statuses + ["repeat"] and log[-1]["split"] == split
    assert log[-1]["risk_parts"] == json.loads(before)["risk_parts"][split]
    assert env.lock["lock_path"].read_text() == before            # a repeat leaves the lock as it was
    assert f"{split} run: repeat" in out
    doc = json.loads(env.out.read_text())
    assert doc["set"] == split and doc["alerts_given"] > 0 and doc["alerts"] == min(4, doc["alerts_given"])
    assert doc["budget_per_shift"] == 6 and doc["weights"] == "default"


def test_a_changed_hash_or_risk_table_is_refused_without_logging(env):
    env.look("test")
    before, log = env.lock["lock_path"].read_text(), logged(env.lock)
    code, out = env.cli("test", "--matched")                     # the look was not matched: another configuration
    assert code == 1 and "configuration_sha256" in out and "never relocks" in out
    other = env.tmp / "other-risk"
    other.mkdir()
    for part in sorted((ne.RISK_DIR / "test").glob("*.parquet")):
        pl.read_parquet(part).with_columns(p_miss=pl.col("p_miss") * 0.5).write_parquet(other / part.name)
    code, out = env.cli("test", "--tracernet", str(other / "*.parquet"))
    assert code == 1 and "risk_parts" in out
    assert env.lock["lock_path"].read_text() == before and logged(env.lock) == log and not env.out.exists()


def test_a_lock_without_tracernets_risk_table_is_refused(env):
    val_table(env)
    shutil.copytree(ne.RISK_DIR / "val", ne.RISK_DIR / "test")
    methods = ne.baselines() + [ne.tracernet_method(ne.RISK_DIR / "test" / "*.parquet")]
    ne.guard_test_run("test", ne.lock_record(env.store, methods, artifact_dir=env.artifact), **env.lock)
    log = logged(env.lock)
    code, out = env.cli("test")
    assert code == 1 and "records no TracerNet risk table for test" in out
    assert logged(env.lock) == log and not env.out.exists()


# --- export --------------------------------------------------------------------------------------------------------

def test_export_hands_every_argument_to_export_neural(monkeypatch, capsys):
    from src.api import export_neural

    seen = []
    monkeypatch.setattr(export_neural, "main", lambda argv=None: seen.append(argv) or 7)
    argv = ["--source", "v2", "--scenarios", "a,b", "--out", "x.json"]
    assert pipeline.main(["export", *argv]) == 7 and seen == [argv]
    monkeypatch.undo()
    with pytest.raises(SystemExit) as stop:
        pipeline.main(["export", "--help"])
    assert stop.value.code == 0 and "--source" in capsys.readouterr().out


def test_the_help_lists_explain_and_export():
    text = pipeline.build_parser().format_help()
    assert "explain" in text and "export" in text


def test_explain_writes_test_checks_where_the_report_reads_and_other_sets_beside_them(monkeypatch, tmp_path):
    from src.models.tracernet import explain
    written = []
    monkeypatch.setattr(explain, "RESULTS_PATH", tmp_path / "explanations.json")
    monkeypatch.setattr(ne, "baselines", lambda: [])
    monkeypatch.setattr(ne, "tracernet_method", lambda path: SimpleNamespace(series=SimpleNamespace(files=[path])))
    monkeypatch.setattr(ne, "_pin_risk_tables", lambda methods: {"tracernet": {}})
    monkeypatch.setattr(pipeline, "risk_snapshot", lambda files: (pl.DataFrame({"replicate": [0, 1]}), {"tracernet": {}}))
    monkeypatch.setattr(pipeline, "alert_inputs", lambda *a, **k: pl.DataFrame({"replicate": [0, 1]}))
    fake = SimpleNamespace(alerts=lambda risk, **k: pl.DataFrame({"x": [1]}))
    monkeypatch.setattr(Predictor, "load", classmethod(lambda cls, *a, **k: fake))
    doc = {"checks": {}, "alerts": 1, "alerts_given": 1, "attributed_alerts": 1, "steps": 64, "passed": True}
    monkeypatch.setattr(explain, "run_checks", lambda *a, **k: written.append(k["out_path"]) or doc)
    assert pipeline.explain_command("val", out=lambda *_: None) == 0
    assert written == [tmp_path / "explanations-val.json"]          # the report's test file is left alone


def test_evaluate_scores_the_own_store_sets_before_evaluating_them(monkeypatch, tmp_path):
    import duckdb
    calls = []
    monkeypatch.setattr(ne, "RISK_DIR", tmp_path / "risk")
    monkeypatch.setattr(ne, "risk_table", lambda split, art, **k: calls.append(("risk_table", split, k["store_dir"],
                                                                                   k["out_dir"])) or k["out_dir"] / "*.parquet")
    monkeypatch.setattr(ne, "command_evaluate", lambda split, **k: calls.append(("evaluate", split, k["tracernet"])) or 0)
    assert pipeline.main(["evaluate", "--split", "control_v1world", "--artifact", "art", "--store", str(tmp_path / "c")]) == 0
    assert calls[0] == ("risk_table", "val", tmp_path / "c", tmp_path / "risk" / "control_v1world")
    assert calls[1] == ("evaluate", "control_v1world", tmp_path / "risk" / "control_v1world" / "*.parquet")

    v1 = tmp_path / "v1"
    v1.mkdir()
    con = duckdb.connect(str(v1 / store.OBSERVED_NAME))
    con.execute("create table specimens as select * from (values (2), (0), (1), (1)) t(run)")
    con.close()
    scored = []

    class Fake:
        def score(self, store_dir, runs, workers=None):
            scored.append(runs)
            return pl.DataFrame({c: [0] for c in pipeline.RISK_KEEP} | {"extra": [1]})
    monkeypatch.setattr(Predictor, "load", classmethod(lambda cls, *a, **k: Fake()))
    calls.clear()
    assert pipeline.main(["evaluate", "--split", "legacy_v1", "--artifact", "art", "--store", str(v1)]) == 0
    assert scored == [[0, 1, 2]] and calls == [("evaluate", "legacy_v1", tmp_path / "risk" / "legacy_v1" / "*.parquet")]
    assert pl.read_parquet(tmp_path / "risk" / "legacy_v1" / "part-0000.parquet").columns == list(pipeline.RISK_KEEP)
    calls.clear()
    assert pipeline.main(["evaluate", "--split", "legacy_v1", "--tracernet", "given.parquet", "--store", str(v1)]) == 0
    assert calls == [("evaluate", "legacy_v1", Path("given.parquet"))]       # a given table is not rescored


def _after_guard(monkeypatch, action, before=False):
    """Run `action` just before (or just after) the real guard, as another process would."""
    real = ne.guard_test_run

    def guard(*a, **k):
        if before:
            action()
        run = real(*a, **k)
        if not before:
            action()
        return run
    monkeypatch.setattr(ne, "guard_test_run", guard)


def test_a_risk_part_replaced_after_the_guard_is_not_read(env, monkeypatch):
    env.look("test")
    part = sorted((ne.RISK_DIR / "test").glob("*.parquet"))[0]
    seen = []
    real_inputs = pipeline.alert_inputs
    monkeypatch.setattr(pipeline, "alert_inputs", lambda table, *a: seen.append(table) or real_inputs(table, *a))
    original = pl.read_parquet(part)
    _after_guard(monkeypatch, lambda: part.write_bytes(b"not the recorded table"))
    code, out = env.cli("test")
    assert code == 0, out
    assert seen[0].head(original.height).equals(original)      # the rows decoded are the bytes the guard hashed


def test_a_relock_that_cleared_the_recorded_table_before_the_guard_is_refused(env, monkeypatch):
    env.look("test")

    def clear():
        doc = json.loads(env.lock["lock_path"].read_text())
        doc["risk_parts"] = {}
        env.lock["lock_path"].write_text(json.dumps(doc))
    _after_guard(monkeypatch, clear, before=True)
    called = []
    monkeypatch.setattr(pipeline, "alert_inputs", lambda *a: called.append(a))
    code, out = env.cli("test")
    assert code == 1 and "no longer records the test risk table" in out and not called


def test_a_relock_after_the_guard_is_refused_before_the_store_is_read(env, monkeypatch):
    env.look("test")

    def relock():
        doc = json.loads(env.lock["lock_path"].read_text())
        doc["configuration_sha256"] = "0" * 64
        env.lock["lock_path"].write_text(json.dumps(doc))
    _after_guard(monkeypatch, relock)
    called = []
    monkeypatch.setattr(pipeline, "alert_inputs", lambda *a: called.append(a))
    code, out = env.cli("test")
    assert code == 1 and "changed since this run was recorded" in out and not called
