"""The `decide` pipeline command on the tiny development store (docs/neural/spec.md §11, §12.1, §8.7): the dry run
freezes and prices the items, a spending run sends them through a mock transport and writes the outputs and the
summary, and --score-only scores them through evaluate.py. On test the first decision-layer look relocks only to add
the decision model's hash, with a reason, and records TracerNet's risk table again; it refuses any other difference,
and later looks are repeats. Facts and the X3 sample read the observed store only, and a locked split only with its
test run. Nothing leaves the machine."""

import gzip
import json
import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import duckdb
import httpx2
import pytest

import tinynet as T
from src.decide import client as dc, facts, render, zeroshot
from src.models.tracernet.predictor import Predictor
from src.neural import pipeline
from src.pipeline import dispatch_eval, neural_eval as ne
from src.scenarios import runner
from test_decide import FAKE_KEY, answer_for
from test_neural_eval import fake_artifact

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    d = T.build_store(tmp_path_factory.mktemp("decide-command"))
    return {"store": d, "pred": T.predictor(d)}


@pytest.fixture
def env(tiny, tmp_path, monkeypatch):
    artifact = fake_artifact(tmp_path / "artifact")
    p = tiny["pred"]
    loaded = Predictor(p.members, p.schema, p.calibration, manifest=p.manifest)
    loaded.artifact_dir = artifact
    monkeypatch.setattr(Predictor, "load", classmethod(lambda cls, path=None, device="cpu": loaded))
    lock = tmp_path / "lock"
    monkeypatch.setattr(ne, "LOCK_PATH", lock / "test_lock.json")
    monkeypatch.setattr(ne, "TEST_RUNS_PATH", lock / "test_runs.jsonl")
    monkeypatch.setattr(ne, "RISK_DIR", tmp_path / "risk")
    cfg = dc.load_config()
    cfg["price"]["verified_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    monkeypatch.setattr(dc, "load_config", lambda path=None: cfg)
    root = tmp_path / "root"
    root.mkdir()
    (root / ".env").write_text(f"OPENROUTER_API_KEY={FAKE_KEY}\n")
    paths = runner.Paths(root=root, jobs_config=ROOT / "config" / "openai_jobs.yaml",
                         prices=ROOT / "config" / "openai_prices.yaml", env=root / ".env")
    sent = []

    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        return httpx2.Response(200, content=json.dumps(answer_for(body["questions"])))
    lines = []

    def cli(*args):
        lines.clear()
        code = pipeline.main(["decide", *args, "--store", str(tiny["store"]), "--artifact", str(artifact)],
                             paths=paths, out=lines.append, sleep=lambda s: None,
                             client_factory=lambda: dc.DecisionClient(cfg, env_path=root / ".env",
                                                                      transport=httpx2.MockTransport(handler)))
        return code, "\n".join(lines)

    def look(split):
        assert ne.command_evaluate(split, store_dir=tiny["store"], out_dir=tmp_path / "out" / split, workers=1,
                                   artifact_dir=artifact) == 0

    ne.risk_table("val", artifact, store_dir=tiny["store"], out_dir=ne.RISK_DIR / "val", workers=1)
    return SimpleNamespace(store=tiny["store"], artifact=artifact, root=root, cli=cli, look=look, sent=sent, cfg=cfg,
                           decide=root / "data" / "decide", results=root / "results" / "neural" / "decisions",
                           lock=lock)


def logged(env):
    path = env.lock / "test_runs.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def frozen(env, job, set_name):
    return [json.loads(line) for line in gzip.open(env.decide / f"items-{job}-{set_name}.jsonl.gz", "rt")]


def test_x2_on_validation_freezes_prices_sends_and_scores(env):
    code, out = env.cli("--job", "X2", "--set", "val", "--dry-run", "--n", "30")
    assert code == 0 and "nothing sent, nothing spent, no connection" in out and env.sent == []
    rows = frozen(env, "X2", "val")
    assert len(rows) >= 30 and len({r["meta"]["scenario_id"] for r in rows}) == 1
    for r in rows:
        render.check_alert_text(r["state"])                       # no id, time, date or hidden word
        assert r["meta"]["alert_id"] not in r["state"] and r["meta"]["r2"]["role"]
        assert "TracerNet: chance it misses its window" in r["state"]
        assert set(r["questions"]) == {"place", "action", "shared_problem", "escalate"}
    code, out = env.cli("--job", "X2", "--set", "val", "--budget-usd", "0.10")
    assert code == 0 and len(env.sent) == len(rows), out
    outputs = dc.read_outputs(env.decide / "outputs" / "X2-val.jsonl")
    assert [o["alert_id"] for o in outputs] == [r["meta"]["alert_id"] for r in rows]
    assert all(o["dispatch"]["source"] == "drex-max" and o["request_hash"] for o in outputs)
    summary = json.loads((env.results / "X2-val.json").read_text())
    assert summary["status"] == "run" and summary["counts"]["answered"] == len(rows)
    assert summary["resolved_model"] == "nace-ai/drex-max-20261016" and summary["ledger"]["spent_usd"] > 0
    code, out = env.cli("--job", "X2", "--set", "val", "--budget-usd", "0.10")      # a rerun is free: all cached
    assert code == 0 and len(env.sent) == len(rows) and "from the cache" in out
    code, out = env.cli("--job", "X2", "--set", "val", "--score-only")
    assert code == 0 and "not binding: validation" in out
    scores = json.loads((env.results / "X2-val-scores.json").read_text())
    assert scores["scored_alerts"] + scores["alerts_without_a_place"] == len(rows)
    assert set(scores["methods"]) == {"model", "R1", "R2"} and scores["adoption"]["binding"] is False


def test_x2_on_test_relocks_only_to_add_the_decision_models_hash_and_then_repeats(env):
    env.look("test")
    before = len(logged(env))
    code, out = env.cli("--job", "X2", "--set", "test", "--dry-run")
    assert code == 1 and "--relock-reason" in out and len(logged(env)) == before    # refused, nothing logged
    code, out = env.cli("--job", "X2", "--set", "test", "--dry-run", "--relock-reason", "X2 joins the lock")
    assert code == 0 and "test run: relock" in out
    relock, recorded = logged(env)[before:]
    assert relock["status"] == "relock" and relock["changed"] == ["decision_model_sha256"]
    assert relock["reason"] == "X2 joins the lock" and recorded["status"] == "risk table recorded"
    lock = json.loads((env.lock / "test_lock.json").read_text())
    assert lock["decision_model_sha256"] == dc.lock_hash(env.cfg) and ne.TRACERNET in lock["risk_parts"]["test"]
    code, out = env.cli("--job", "X2", "--set", "test", "--budget-usd", "0.10")
    assert code == 0, out
    code, out = env.cli("--job", "X2", "--set", "test", "--score-only")
    assert code == 0 and "test run: repeat" in out and "(binding)" in out
    assert logged(env)[-1]["status"] == "repeat"
    lock["time"] = "2026-11-11T00:00:00-05:00"                                    # the lock moved on since the dry run
    (env.lock / "test_lock.json").write_text(json.dumps(lock))
    code, out = env.cli("--job", "X2", "--set", "test", "--budget-usd", "0.10")
    assert code == 1 and "run the dry run again" in out


def test_a_decision_look_never_relocks_over_another_change(env, tmp_path):
    env.look("test")
    other = fake_artifact(tmp_path / "other-artifact")
    (other / "model.pt").write_bytes(b"other weights")
    files = {n: ne.sha256_file(other / n) for n in ("calibration.json", "model.pt")}
    (other / "manifest.json").write_text(json.dumps({"files": files}))
    before = len(logged(env))
    code = pipeline.main(["decide", "--job", "X2", "--set", "test", "--dry-run", "--store", str(env.store),
                          "--artifact", str(other), "--relock-reason", "trying"], paths=runner.Paths(root=env.root),
                         out=(lines := []).append)
    assert code == 1 and "artifact_sha256" in "\n".join(lines) and len(logged(env)) == before


def test_x3_on_test_samples_by_whole_scenario_and_scores_three_forecasts_on_the_same_ticks(env):
    env.look("test")
    code, out = env.cli("--job", "X3", "--set", "test", "--dry-run", "--n", "40", "--relock-reason", "X3 joins")
    assert code == 0, out
    rows = frozen(env, "X3", "test")
    assert 30 <= len(rows) <= 40 and all(set(r["questions"]) == {"miss"} for r in rows)
    for r in rows:
        render.check_alert_text(r["state"])
        assert "TracerNet" not in r["state"]
    assert env.cli("--job", "X3", "--set", "test", "--budget-usd", "0.10")[0] == 0
    code, out = env.cli("--job", "X3", "--set", "test", "--score-only")
    assert code == 0 and "test run: repeat" in out
    scores = json.loads((env.results / "X3-test-scores.json").read_text())
    assert scores["scored_ticks"] == len(rows) and set(scores["methods"]) == {"decision_model", "tracernet",
                                                                             "analytic_local"}


def test_no_key_means_the_job_is_not_run_and_says_why(env):
    assert env.cli("--job", "X2", "--set", "val", "--dry-run", "--n", "5")[0] == 0
    (env.root / ".env").write_text("")
    code, out = env.cli("--job", "X2", "--set", "val", "--budget-usd", "0.10")
    assert code == 1 and "not run" in out and env.sent == []
    doc = json.loads((env.results / "X2-val.json").read_text())
    assert doc["status"] == "not run" and "rule R2" in doc["reason"]


def test_x4_is_not_built():
    lines = []
    assert pipeline.main(["decide", "--job", "X4", "--budget-usd", "1"], out=lines.append) == 1
    assert "not built" in lines[0]


# --- facts, the sample and the labels --------------------------------------------------------------------------------

def _specimens(store_dir, split, n=5):
    con = duckdb.connect(str(Path(store_dir) / "observed.duckdb"), read_only=True)
    rows = con.execute("""select s.specimen_id, s.run from specimens s join events e on e.specimen_id = s.specimen_id
                          and e.activity = 'collected' where s.split = ? and s.role = 'scored'
                          order by s.specimen_id limit ?""", [split, n]).fetchall()
    con.close()
    return rows


def test_facts_read_a_locked_split_only_with_its_test_run(tiny):
    with pytest.raises(facts.FactsError, match="test lock"):
        facts.alert_facts(tiny["store"], [(s, r, 10) for s, r in _specimens(tiny["store"], "test")])
    got = facts.alert_facts(tiny["store"], [(s, r, 10) for s, r in _specimens(tiny["store"], "val")])
    assert len(got) == 5 and all(f["silence_min"] == 10 for f in got)
    for f in got:
        render.check_alert_text(render.alert_state(f))


def test_facts_ignore_every_scan_after_the_tick(tiny, tmp_path):
    """Future invariance: moving every receipt of the run after τ later leaves the facts at τ unchanged (the
    history replicate is a run of its own, which the facts may read whole)."""
    sid, run = _specimens(tiny["store"], "val", 1)[0]
    before = facts.alert_facts(tiny["store"], [(sid, run, 30)])[0]
    copy = tmp_path / "store"
    shutil.copytree(tiny["store"], copy)
    con = duckdb.connect(str(copy / "observed.duckdb"))
    tau = con.execute("select timestamp + interval 30 minute from events where specimen_id = ? and activity = 'collected'",
                      [sid]).fetchone()[0]
    con.execute("update events set timestamp = timestamp + interval 600 minute where activity = 'received' "
                "and timestamp > ? and specimen_id in (select specimen_id from specimens where run = ?)", [tau, run])
    assert con.execute("select count(*) from events e join specimens s using (specimen_id) where s.run = ? "
                       "and e.activity = 'received' and e.timestamp > ?", [run, tau]).fetchone()[0] > 0
    con.close()
    assert facts.alert_facts(copy, [(sid, run, 30)])[0] == before


def test_the_x3_sample_is_stratified_seeded_and_inside_each_specimens_ticks(tiny):
    a = zeroshot.sample_ticks(tiny["store"], "val", 90, seed=3)
    assert a == zeroshot.sample_ticks(tiny["store"], "val", 90, seed=3) and len(a) == 90
    counts = {}
    for t in a:
        counts[t.stratum] = counts.get(t.stratum, 0) + 1
        assert t.tick_min % 5 == 0 and 5 <= t.tick_min <= t.window_min
    assert max(counts.values()) - min(counts.values()) <= max(counts.values()) // 2 + 1 or len(counts) == 1
    assert len({(t.specimen_id, t.tick_min) for t in a}) == len(a)
    with pytest.raises(facts.FactsError):
        zeroshot.sample_ticks(tiny["store"], "test", 10, seed=0)


def test_x2_labels_place_each_alert_where_its_truth_segments_say(tiny):
    scored = [s.specimen_id for s in dispatch_eval.evaluate.load_specs_v2("val", store_dir=tiny["store"],
                                                                          labels=False)[0]][:200]
    con = duckdb.connect(str(Path(tiny["store"]) / "truth.duckdb"), read_only=True)
    segs = con.execute("""select g.specimen_id, epoch(g.start - s.collected_at) / 60, epoch(g."end" - s.collected_at) / 60,
                                 g.state, g.substate, s.scenario_id
                          from truth_segments g join truth_specimens s using (specimen_id)
                          where list_contains(?, g.specimen_id) and g.state in ('in_tube', 'at_unit', 'hand_carry')
                          order by g.specimen_id, g.seq""", [scored]).fetchall()
    con.close()
    rows = []
    for sid, a, b, state, sub, scen in segs:
        tick = int(a // 5 + 1) * 5
        if b is not None and tick >= b:
            continue
        rows.append({"alert_id": f"{sid}@{tick}", "specimen_id": sid, "scenario_id": scen, "tick_min": tick,
                     "want": dispatch_eval.evaluate.PLACE_OF[(state, sub)]})
    rows = list({r["specimen_id"]: r for r in rows}.values())
    assert rows
    labels = dispatch_eval.x2_labels(rows, "val", tiny["store"])
    for r in rows:
        assert labels[r["alert_id"]]["place"] == r["want"]


# --- Review findings F008 and F009 ------------------------------------------------------------------------------------

def test_f008_the_first_test_store_read_happens_inside_the_locks_mutex(env, monkeypatch):
    from contextlib import contextmanager

    env.look("test")
    depth, seen = {"n": 0}, []
    real = ne._lock_transaction

    @contextmanager
    def tracked(path):
        with real(path):
            depth["n"] += 1
            try:
                yield
            finally:
                depth["n"] -= 1
    monkeypatch.setattr(ne, "_lock_transaction", tracked)
    real_inputs, real_sample = pipeline.alert_inputs, zeroshot.sample_ticks
    monkeypatch.setattr(pipeline, "alert_inputs", lambda *a, **k: (seen.append(("X2", depth["n"])),
                                                                   real_inputs(*a, **k))[1])
    monkeypatch.setattr(zeroshot, "sample_ticks", lambda *a, **k: (seen.append(("X3", depth["n"])),
                                                                   real_sample(*a, **k))[1])
    assert env.cli("--job", "X2", "--set", "test", "--dry-run", "--relock-reason", "X2 joins the lock")[0] == 0
    assert env.cli("--job", "X3", "--set", "test", "--dry-run", "--n", "20")[0] == 0
    assert seen == [("X2", 1), ("X3", 1)]                 # each read inside the mutex, after the final checks


def test_f009_score_only_refuses_outputs_from_another_decision_configuration(env):
    assert env.cli("--job", "X2", "--set", "val", "--dry-run", "--n", "10")[0] == 0
    assert env.cli("--job", "X2", "--set", "val", "--budget-usd", "0.10")[0] == 0
    env.cfg["decisions"]["prompt_version"] = "p2"          # revised on validation, never sent
    code, out = env.cli("--job", "X2", "--set", "val", "--score-only")
    assert code == 1 and "the decision configuration" in out
    assert not (env.results / "X2-val-scores.json").exists()


def test_f009_score_only_refuses_outputs_altered_after_their_run(env):
    assert env.cli("--job", "X2", "--set", "val", "--dry-run", "--n", "10")[0] == 0
    assert env.cli("--job", "X2", "--set", "val", "--budget-usd", "0.10")[0] == 0
    path = env.decide / "outputs" / "X2-val.jsonl"
    path.write_text(path.read_text().replace('"drex-max"', '"rules"', 1))
    code, out = env.cli("--job", "X2", "--set", "val", "--score-only")
    assert code == 1 and "not the bytes their run wrote" in out


def test_f009_on_test_outputs_sent_before_a_relock_are_never_scored_under_it(env):
    env.look("test")
    assert env.cli("--job", "X2", "--set", "test", "--dry-run", "--relock-reason", "X2 joins the lock")[0] == 0
    assert env.cli("--job", "X2", "--set", "test", "--budget-usd", "0.10")[0] == 0
    env.cfg["decisions"]["prompt_version"] = "p2"          # a new configuration relocks and rebuilds the states
    code, out = env.cli("--job", "X2", "--set", "test", "--dry-run", "--relock-reason", "p2 on test")
    assert code == 0 and "test run: relock" in out
    before = len(logged(env))
    code, out = env.cli("--job", "X2", "--set", "test", "--score-only")
    assert code == 1 and "do not match" in out and len(logged(env)) == before    # refused before any look
    assert not (env.results / "X2-test-scores.json").exists()


def test_f009_scores_carry_the_provenance_they_were_checked_against(env):
    assert env.cli("--job", "X2", "--set", "val", "--dry-run", "--n", "10")[0] == 0
    assert env.cli("--job", "X2", "--set", "val", "--budget-usd", "0.10")[0] == 0
    assert env.cli("--job", "X2", "--set", "val", "--score-only")[0] == 0
    summary = json.loads((env.results / "X2-val.json").read_text())
    scores = json.loads((env.results / "X2-val-scores.json").read_text())
    assert scores["provenance"] == summary["provenance"]
    assert summary["provenance"]["outputs_sha256"] == dc.file_sha256(env.decide / "outputs" / "X2-val.jsonl")
    assert summary["billing"]["calls_with_usage"] == summary["counts"]["answered"]
    rows = dc.read_outputs(env.decide / "outputs" / "X2-val.jsonl")
    assert all(r["billing"]["billed_input_tokens"] == 900 and r["billing"]["request_bytes"] > 0 for r in rows)
