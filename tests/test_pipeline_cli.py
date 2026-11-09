"""The pipeline command line (docs/neural/spec.md §12, §15.2): stage markers, `status`, and idempotent reruns. Uses
the fakes of test_runner.py; nothing leaves the machine."""

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from src.neural import stages
from test_runner import NOW, Env, ok

ROOT = Path(__file__).resolve().parents[1]


def marker(env, name):
    return json.loads((env.paths.stages / f"{name}.json").read_text())


def test_a_stage_marker_holds_input_and_output_hashes_the_git_sha_and_its_times(tmp_path):
    env = Env(tmp_path)
    out = ok(env.cli("plan", "--job", "O1"))
    assert "24 briefs (disrupted 6, edge 6, normal 6, stressed 6)" in out and "ran: never run" in out
    m = marker(env, "plan-O1")
    assert m["stage"] == "plan-O1" and m["status"] == "done" and m["note"] is None
    assert set(m["inputs"]) == {"job", "planner_config", "planner_code", "targets"}
    assert m["outputs"]["briefs"] == {"path": str(env.paths.briefs("O1")),
                                      "hash": stages.file_hash(env.paths.briefs("O1"))}
    assert m["started_at"] <= m["finished_at"]
    assert m["git_sha"] is None or len(m["git_sha"]) == 40
    ok(env.cli("dry-run", "--job", "O1"))
    d = marker(env, "dry-run-O1")
    assert set(d["inputs"]) == {"briefs", "request_set", "prices", "tier", "estimator", "ledger"}
    assert d["inputs"]["request_set"] == json.loads(env.paths.dryrun("O1").read_text())["request_set_hash"]


def test_reruns_are_idempotent_and_happen_only_when_an_input_changes_or_with_force(tmp_path):
    env = Env(tmp_path)
    ok(env.cli("plan", "--job", "O1"))
    briefs = env.paths.briefs("O1").read_bytes()
    first = marker(env, "plan-O1")
    assert "up to date" in ok(env.cli("plan", "--job", "O1"))
    assert marker(env, "plan-O1") == first and env.paths.briefs("O1").read_bytes() == briefs
    assert "ran: forced" in ok(env.cli("plan", "--job", "O1", "--force"))
    assert env.paths.briefs("O1").read_bytes() == briefs  # the same seed gives the same briefs
    env.paths.briefs("O1").write_text("tampered\n")
    assert "ran: output changed or missing: briefs" in ok(env.cli("plan", "--job", "O1"))
    env.rewrite_jobs(lambda c: c["jobs"]["O1"].update(seed=999))
    assert "ran: input changed: job" in ok(env.cli("plan", "--job", "O1"))
    assert json.loads(env.paths.briefs("O1").read_text().splitlines()[0])["brief_id"] == "S999-000"


def test_the_dry_run_reruns_when_its_inputs_change_or_it_is_a_day_old(tmp_path):
    env = Env(tmp_path)
    ok(env.cli("dry-run", "--job", "O2"))
    saved = env.paths.dryrun("O2").read_bytes()
    same = "The dry run is up to date"
    out = ok(env.cli("dry-run", "--job", "O2"))
    assert same in out and "Dry run for O2" in out and env.paths.dryrun("O2").read_bytes() == saved
    out = ok(env.cli("dry-run", "--job", "O2", now=NOW + timedelta(hours=25)))
    assert same not in out and "generate will refuse" in out  # rerun, and the prices are a day old too
    assert marker(env, "dry-run-O2")["status"] == "done"
    with env.ledger() as lg:
        lg.reserve("O2", "elsewhere", "0.01")  # the headroom it reports changed
    assert same not in ok(env.cli("dry-run", "--job", "O2"))
    env.seams.dev += " A revised sentence."
    assert same not in ok(env.cli("dry-run", "--job", "O2"))
    assert json.loads(env.paths.dryrun("O2").read_bytes())["request_set_hash"] != json.loads(saved)["request_set_hash"]


def test_status_shows_stages_the_ledger_and_items(tmp_path):
    env = Env(tmp_path)
    out = ok(env.cli("status"))
    assert "none run yet" in out and "not created yet" in out
    assert not env.paths.ledger.exists() and not env.paths.items.exists()  # status creates nothing
    ok(env.cli("dry-run", "--job", "O1"))
    ok(env.cli("generate", "--job", "O1", "--budget-usd", "3"))
    out = ok(env.cli("status"))
    for stage in ("plan-O1", "dry-run-O1", "generate-O1"):
        assert stage in out
    assert "account openai  cap $76.4300 (margin $2.4300): spent $0.3943" in out
    assert "job O1   cap $3.0000: spent $0.3943, held $0.0000" in out
    assert "job O1b  cap $2.0000: spent $0.0000, held $0.0000, headroom $2.0000  (not released)" in out
    assert "Items O1: accepted 72; 0 Batch chunks open" in out
    env.paths.ledger.with_suffix(".jsonl").write_text("garbage\n")
    out = ok(env.cli("status"))
    assert "Ledger: UNAVAILABLE" in out


def test_generate_records_every_run_and_a_stop(tmp_path):
    env = Env(tmp_path)
    ok(env.cli("dry-run", "--job", "O1"))
    ok(env.cli("generate", "--job", "O1", "--budget-usd", "3"))
    first = marker(env, "generate-O1")
    assert first["status"] == "done" and first["inputs"]["budget_usd"] == "3"
    code, out = env.cli("generate", "--job", "O1", "--budget-usd", "2")
    assert code == 0 and "sent 0 requests" in out
    assert marker(env, "generate-O1")["inputs"]["budget_usd"] == "2"
    (tmp_path / "second").mkdir()
    env2 = Env(tmp_path / "second", script={"*": [("http", 400, {"error_type": "invalid_request_error"})]})
    ok(env2.cli("dry-run", "--job", "O1"))
    assert env2.cli("generate", "--job", "O1", "--budget-usd", "3")[0] == 2
    m = marker(env2, "generate-O1")
    assert m["status"] == "stopped" and "HTTP 400" in m["note"]


def test_a_failed_stage_is_recorded_and_runs_again(tmp_path):
    env = Env(tmp_path)

    def broken(*args, **kwargs):
        raise RuntimeError("planner bug")
    real = env.seams.plan
    env.seams.plan = broken
    with pytest.raises(RuntimeError):
        env.cli("plan", "--job", "O1")
    m = marker(env, "plan-O1")
    assert m["status"] == "failed" and "planner bug" in m["note"]
    env.seams.plan = real
    assert "ran: last run failed" in ok(env.cli("plan", "--job", "O1"))


def test_ledger_report_and_release_job(tmp_path):
    env = Env(tmp_path)
    code, out = env.cli("ledger", "report")
    assert code == 1 and "no ledger" in out
    ok(env.cli("dry-run", "--job", "O1"))
    ok(env.cli("generate", "--job", "O1", "--budget-usd", "3"))
    out = ok(env.cli("ledger", "report"))
    assert "O1    gpt-6.1-sol     flex            24     $0.2544" in out
    assert "job O1   cap $3.0000: spent $0.3943" in out
    assert "O3b released: the round-2 slices are still the worst" in ok(
        env.cli("ledger", "release-job", "O3b", "--reason", "the round-2 slices are still the worst"))
    assert "already released" in ok(env.cli("ledger", "release-job", "O3b", "--reason", "again"))
    with pytest.raises(SystemExit):
        env.cli("ledger", "release-job", "O3b")  # a reason is required


def test_unknown_jobs_and_bad_budgets_are_refused(tmp_path):
    env = Env(tmp_path)
    code, out = env.cli("dry-run", "--job", "X2")
    assert code == 1 and "not an OpenAI scenario job" in out
    ok(env.cli("dry-run", "--job", "O1"))
    for budget in ("abc", "0", "-1", "nan"):
        code, out = env.cli("generate", "--job", "O1", "--budget-usd", budget)
        assert code == 1, budget
    assert env.client.calls == []


def test_the_module_runs_as_a_command():
    done = subprocess.run([sys.executable, "-m", "src.neural.pipeline", "--help"], cwd=ROOT, capture_output=True,
                          text=True, timeout=60)
    assert done.returncode == 0
    for command in ("status", "plan", "dry-run", "generate", "collect", "ledger"):
        assert command in done.stdout
