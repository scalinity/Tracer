import gzip
import hashlib
import json

import pytest
from fakes import FakeClient

from src.generator import briefs as briefs_mod
from src.generator import collect, config, prompt, run, validate


@pytest.fixture(scope="module")
def loaded():
    return config.load()


@pytest.fixture(scope="module")
def runs(loaded):
    return [briefs_mod.make_briefs(1, r, 40, loaded) for r in range(3)]


@pytest.fixture
def ctx(loaded):
    return run.batch_context("J2", 1, 40, loaded)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(prompt, "USAGE_LOG", tmp_path / "usage.jsonl")
    monkeypatch.setattr(validate, "DROPPED_LOG", tmp_path / "dropped.jsonl")
    monkeypatch.setattr(collect, "DATASET_PATH", tmp_path / "datasets" / "tracer-synthetic-v1.jsonl.gz")
    monkeypatch.setattr(collect, "MANIFEST_PATH", tmp_path / "datasets" / "manifest.json")
    return tmp_path


def everyone(runs):
    return [b for briefs in runs for b in briefs]


def usage(tmp_path):
    return [json.loads(l) for l in (tmp_path / "usage.jsonl").read_text().splitlines()]


# ---- submitting ----

def test_one_batch_per_run_with_the_trail_request_inside(runs, ctx, tmp_path):
    client = FakeClient(everyone(runs))
    ledger = run.submit_batches(client, runs, tmp_path / "batches.json", ctx)
    assert sorted(ledger["batches"]) == ["0", "1", "2"] and len(client.submitted) == 3
    first = client.submitted[ledger["batches"]["0"]["id"]]
    assert [r["custom_id"] for r in first] == [b["specimen_id"] for b in runs[0]]
    params = first[0]["params"]
    assert params["model"] == "claude-haiku-5-5" and params["thinking"] == {"type": "disabled"}
    assert params["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert params["output_config"]["format"]["type"] == "json_schema"
    assert json.loads(params["messages"][0]["content"])["specimen_id"] == runs[0][0]["specimen_id"]


def test_a_rerun_submits_only_the_runs_not_yet_in_the_ledger(runs, ctx, tmp_path, capsys):
    client = FakeClient(everyone(runs))
    path = tmp_path / "batches.json"
    run.submit_batches(client, runs[:2], path, ctx)
    ledger = run.submit_batches(client, runs, path, ctx)
    assert len(client.submitted) == 3 and sorted(ledger["batches"]) == ["0", "1", "2"]
    assert "run 00: already submitted" in capsys.readouterr().out
    run.submit_batches(client, runs, path, ctx)
    assert len(client.submitted) == 3
    assert json.loads(path.read_text())["context"] == ctx


def test_a_ledger_made_with_other_settings_is_refused(runs, ctx, tmp_path):
    path = tmp_path / "batches.json"
    run.submit_batches(FakeClient(everyone(runs)), runs, path, ctx)
    with pytest.raises(RuntimeError, match="different seed"):
        run.submit_batches(FakeClient(everyone(runs)), runs, path, ctx | {"seed": 2})


def test_changed_wording_stops_collection(runs, ctx, loaded, tmp_path, monkeypatch):
    ledger = {"context": ctx, "batches": {}}
    collect.check_ledger(ledger, loaded)
    monkeypatch.setattr(prompt, "INSTRUCTIONS", prompt.INSTRUCTIONS + " Be brief.")
    with pytest.raises(RuntimeError, match="prompt_hash changed"):
        collect.check_ledger(ledger, loaded)


# ---- collecting one batch ----

def run_collect(client, briefs, tmp_path, batch_id="msgbatch_1"):
    return collect.collect_run(client, briefs, batch_id, tmp_path / "full", 0, job="J2",
                               log_path=tmp_path / "usage.jsonl")


def submit(client, briefs):
    return client.batch_create([{"custom_id": b["specimen_id"], "params": {}} for b in briefs]).id


def test_a_clean_batch_is_accepted_without_a_single_synchronous_call(runs, env):
    client = FakeClient(runs[0])
    bid = submit(client, runs[0])
    counts = run_collect(client, runs[0], env, bid)
    assert counts == {"from_batch": 40, "retried": 0, "accepted": 40, "dropped": 0} and not client.creates
    rows = usage(env)
    assert len(rows) == 40 and all(r["batch"] and r["job"] == "J2" for r in rows)
    assert rows[0]["cost_usd"] == pytest.approx((300 * 0.10 + 4000 * 0.01 + 600 * 0.50) / 1e6 / 2)  # half price
    saved = [json.loads(l) for l in (env / "full" / "run-00.jsonl").read_text().splitlines()]
    assert saved[0]["brief"] == runs[0][0] and saved[0]["trail"]["specimen_id"] == runs[0][0]["specimen_id"]


def test_a_trail_that_fails_validation_is_retried_once_with_the_error_text(runs, env):
    bad = runs[0][5]["specimen_id"]
    client = FakeClient(runs[0], batch_broken=[bad])
    bid = submit(client, runs[0])
    counts = run_collect(client, runs[0], env, bid)
    assert (counts["from_batch"], counts["retried"], counts["accepted"], counts["dropped"]) == (39, 1, 40, 0)
    sent = json.loads(client.creates[0]["messages"][0]["content"])
    assert sent["specimen_id"] == bad and "check 6 (planted fault)" in sent["validator_error"]
    assert bad in run.saved_ids(env / "full" / "run-00.jsonl")


@pytest.mark.parametrize("kind", ["errored", "expired"])
def test_a_request_that_errored_or_expired_is_retried_without_an_error_text(runs, env, kind):
    lost = runs[0][7]["specimen_id"]
    client = FakeClient(runs[0], **{kind: [lost]})
    bid = submit(client, runs[0])
    counts = run_collect(client, runs[0], env, bid)
    assert (counts["retried"], counts["accepted"]) == (1, 40)
    assert "validator_error" not in client.creates[0]["messages"][0]["content"]
    assert len(usage(env)) == 39 + 1  # 39 batch results and the one synchronous retry


def test_a_second_failure_is_dropped_and_logged_in_the_run_directory(runs, env):
    bad = runs[0][2]["specimen_id"]
    client = FakeClient(runs[0], always_broken=[bad])
    bid = submit(client, runs[0])
    counts = run_collect(client, runs[0], env, bid)
    assert (counts["accepted"], counts["dropped"]) == (39, 1)
    assert bad not in run.saved_ids(env / "full" / "run-00.jsonl")
    assert collect.dropped_ids(collect.dropped_path(env / "full", 0)) == {bad}
    assert not (env / "dropped.jsonl").exists()


def test_collecting_again_does_nothing(runs, env):
    client = FakeClient(runs[0], always_broken=[runs[0][2]["specimen_id"]])
    bid = submit(client, runs[0])
    run_collect(client, runs[0], env, bid)
    calls, rows = len(client.creates), len(usage(env))
    again = run_collect(client, runs[0], env, bid)
    assert again == {"from_batch": 0, "retried": 0, "accepted": 0, "dropped": 0}
    assert (len(client.creates), len(usage(env))) == (calls, rows)


def test_a_result_for_an_unknown_specimen_is_an_error(runs, env):
    client = FakeClient(runs[0] + runs[1])
    bid = submit(client, runs[0] + runs[1])
    with pytest.raises(RuntimeError, match="not one of run 00's briefs"):
        run_collect(client, runs[0], env, bid)


# ---- the command: waiting, collecting, freezing ----

def setup_main(runs, ctx, env, monkeypatch, client):
    monkeypatch.setattr(prompt, "make_client", lambda: client)
    ledger = run.submit_batches(client, runs, env / "batches.json", ctx)
    return ["--batch-ids", str(env / "batches.json"), "--out", str(env / "full")]


def test_batches_still_processing_are_reported_and_left_alone(runs, ctx, env, monkeypatch, capsys):
    client = FakeClient(everyone(runs))
    args = setup_main(runs, ctx, env, monkeypatch, client)
    client.status = "in_progress"
    assert collect.main(args) == 0
    out = capsys.readouterr().out
    assert "run 00: in_progress, 40 still processing" in out and "0 accepted, 0 dropped, of 120" in out
    assert not (env / "full").exists()


def test_freezing_before_every_run_is_collected_is_refused(runs, ctx, env, monkeypatch):
    client = FakeClient(everyone(runs))
    args = setup_main(runs, ctx, env, monkeypatch, client)
    client.status = "in_progress"
    with pytest.raises(RuntimeError, match="not every run has been collected"):
        collect.main(args + ["--freeze"])
    assert not collect.DATASET_PATH.exists()


def test_freeze_writes_a_deterministic_dataset_and_a_manifest(runs, ctx, env, monkeypatch, capsys):
    bad, lost = runs[1][3]["specimen_id"], runs[2][9]["specimen_id"]
    client = FakeClient(everyone(runs), batch_broken=[bad], always_broken=[lost])
    args = setup_main(runs, ctx, env, monkeypatch, client)
    assert collect.main(args + ["--freeze"]) == 0
    manifest = json.loads(collect.MANIFEST_PATH.read_text())
    assert (manifest["requested"], manifest["accepted"], manifest["dropped"]) == (120, 119, 1)
    assert manifest["accepted_rate"] == pytest.approx(119 / 120, abs=1e-4)
    assert manifest["first_attempt_rate"] == pytest.approx(118 / 120, abs=1e-4)  # one retried, one dropped
    assert manifest["sha256"] == hashlib.sha256(collect.DATASET_PATH.read_bytes()).hexdigest()
    assert manifest["bytes"] == collect.DATASET_PATH.stat().st_size
    for key in ("model", "prompt_hash", "policy_hash", "config_hash", "seed", "n_per_run"):
        assert manifest[key] == ctx[key], key
    assert manifest["runs"] == 3 and manifest["prompt_hash"] == prompt.prompt_hash() and manifest["created"]
    assert sum(manifest["requested_by_fault"].values()) == 120 and sum(manifest["accepted_by_fault"].values()) == 119
    assert manifest["cost_usd"] > 0 and "K2" in manifest["clinical_review"]
    assert manifest["breach_accepted"] == sum(r["label"]["breach"] for r in
                                              (json.loads(l)["brief"] for l in
                                               gzip.open(collect.DATASET_PATH, "rt").read().splitlines()))

    lines = gzip.open(collect.DATASET_PATH, "rt").read().splitlines()
    ids = [json.loads(l)["brief"]["specimen_id"] for l in lines]
    assert ids == sorted(ids) and len(set(ids)) == 119 and lost not in ids and bad in ids

    first_sha = manifest["sha256"]
    collect.main(args + ["--freeze"])  # collecting again changes nothing, so the frozen bytes are identical
    assert json.loads(collect.MANIFEST_PATH.read_text())["sha256"] == first_sha
    assert "froze tracer-synthetic-v1.jsonl.gz" in capsys.readouterr().out


def test_a_warning_when_fewer_than_11000_are_accepted(runs, ctx, env, monkeypatch, capsys):
    client = FakeClient(everyone(runs))
    args = setup_main(runs, ctx, env, monkeypatch, client)
    collect.main(args + ["--freeze"])
    assert "fewer than 11000 accepted" in capsys.readouterr().out


def test_the_dry_run_prices_the_batch_at_half(runs, env, monkeypatch, capsys):
    monkeypatch.setattr(prompt, "make_client", lambda: FakeClient(everyone(runs)))
    run.main(["--seed", "1", "--runs", "30", "--n", "400", "--batch", "--dry-run", "--budget-usd", "5"])
    out = capsys.readouterr().out
    assert "12000 trails on claude-haiku-5-5 (batch)" in out
    run.main(["--seed", "1", "--runs", "30", "--n", "400", "--dry-run"])
    full = float(out.split("estimated $")[1])
    assert float(capsys.readouterr().out.split("estimated $")[1]) == pytest.approx(full * 2, rel=0.01)


def test_run_batch_submits_without_waiting_and_saves_the_ids(runs, env, monkeypatch, capsys):
    client = FakeClient(everyone(runs))
    monkeypatch.setattr(prompt, "make_client", lambda: client)
    path = env / "ids.json"
    assert run.main(["--seed", "1", "--runs", "3", "--n", "40", "--batch", "--budget-usd", "5",
                     "--batches", str(path), "--out", str(env / "out")]) == 0
    assert len(client.submitted) == 3 and not client.creates
    assert sorted(json.loads(path.read_text())["batches"]) == ["0", "1", "2"]
    assert "python -m src.generator.collect" in capsys.readouterr().out
