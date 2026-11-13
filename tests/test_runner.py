"""The request runner and the spend guards around it (docs/neural/spec.md §4.6, §4.7, §15.2).

A dry run spends nothing and opens no connection; a spend needs a fresh dry run of the same request set; O2 needs the
O1 gate; resume is idempotent; a stored response is never sent again; Batch chunks respect the queue limit; unknown
outcomes stay billed. N1's planner, prompt and validator and the OpenAI client are small fakes defined here, so
nothing leaves the machine."""

import json
import socket
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml

from src.neural import pipeline
from src.scenarios import ledger as L
from src.scenarios import prices, runner
from src.scenarios.client import CallResult, ClientError, parse_response

ROOT = Path(__file__).resolve().parents[1]
EST = timezone(timedelta(hours=-5))
NOW = datetime(2026, 11, 9, 10, 0, tzinfo=EST)
STRATA = ("normal", "stressed", "disrupted", "edge")
JOB_DEFAULTS = {"O1": 24, "O1b": 12}
DEV = "You write one synthetic laboratory week as JSON for a research simulator; no real place or person. " * 16
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["brief_id", "narrative"],
          "properties": {"brief_id": {"type": "string"}, "narrative": {"type": "string"}}}
USAGE = {"input_tokens": 600, "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
         "output_tokens": 2000, "output_tokens_details": {"reasoning_tokens": 700}, "total_tokens": 2600}


class Crash(BaseException):
    """The process dying mid-run."""


class Result(NamedTuple):
    ok: bool
    layer: str | None
    errors: list


class FakeSeams:
    """Stands in for N1's planner, prompt, schema and validator. The verdict rides in the fake scenario."""

    def __init__(self):
        self.dev = DEV

    # The real planner's defaults when a job's config says briefs: null (6 per stratum for O1, 3 for O1b).
    def plan(self, job, seed, *, n=None, targets=None):
        return [{"brief_id": f"S{seed}-{i:03d}", "stratum": STRATA[i % 4], "archetype": "community_hospital",
                 "quirk": f"quirk {i}"} for i in range(n or JOB_DEFAULTS.get(job, 8))]

    def build_messages(self, brief, repair_errors=None):
        user = dict(brief)
        if repair_errors:
            user["repair_errors"] = list(repair_errors)
        return [{"role": "developer", "content": self.dev}, {"role": "user", "content": json.dumps(user, sort_keys=True)}]

    def api_schema(self):
        return SCHEMA

    def schema_hash(self):
        return "schemahash000001"

    def prompt_hash(self, model, effort):
        return f"ph-{model}-{effort}"

    def validate(self, scenario, brief):
        verdict = scenario.get("verdict")
        return Result(True, None, []) if verdict is None else Result(False, verdict["layer"], verdict["errors"])


def key_of(body):
    user = json.loads(body["input"][-1]["content"])
    return user["brief_id"], body["model"], 2 if "repair_errors" in user else 1


def response_for(outcome, body, tier="flex"):
    bid, _, _ = key_of(body)
    scenario = {"brief_id": bid, "narrative": f"week for {bid}"}
    usage, status = dict(USAGE), "completed"
    if isinstance(outcome, tuple) and outcome[0] == "invalid":
        scenario["verdict"] = {"layer": outcome[1], "errors": outcome[2]}
    if isinstance(outcome, tuple) and outcome[0] == "usage":
        usage = outcome[1]
    if outcome == "incomplete":
        status = "incomplete"
    content = ([{"type": "refusal", "refusal": "no"}] if outcome == "refusal"
               else [{"type": "output_text", "text": json.dumps(scenario)}])
    return {"id": f"resp_{bid}", "object": "response", "status": status, "service_tier": tier,
            "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
            "output": [{"type": "message", "role": "assistant", "content": content}], "usage": usage}


class FakeClient:
    """Answers by script: keys (brief_id, model, attempt), (brief_id, attempt), (brief_id,) or "*" map to a list of
    outcomes used in turn (the last repeats). Records every request; also fakes the Batch API."""

    def __init__(self, script=None):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.calls = []
        self.batch_lines = []
        self.batches = {}
        self.cancelled = []
        self.crash_after = None
        self.submit_error = None
        self.lock = threading.Lock()

    def outcome(self, body):
        bid, model, attempt = key_of(body)
        for k in ((bid, model, attempt), (bid, attempt), (bid,), "*"):
            if k in self.script:
                seq = self.script[k]
                return seq.pop(0) if len(seq) > 1 else seq[0]
        return "ok"

    def call(self, body, *, timeout, on_raw=None):
        assert "temperature" not in body
        with self.lock:
            self.calls.append((runner.request_hash(body),) + key_of(body))
            n = len(self.calls)
            if self.crash_after is not None and n > self.crash_after:
                raise Crash()
            o = self.outcome(body)
        if o in ("timeout", "connection"):
            return CallResult(status=o)
        if isinstance(o, tuple) and o[0] == "http":
            return CallResult(status=f"http_{o[1]}", **o[2])
        served = o[1] if isinstance(o, tuple) and o[0] in ("tier", "served") else body.get("service_tier", "default")
        if isinstance(o, tuple) and o[0] == "served":  # ("served", tier, usage): billed at that tier
            o = ("usage", o[2])
        resp = response_for(o, body, served)  # the API echoes the tier it actually used
        if on_raw:
            on_raw(json.dumps(resp))
        r = parse_response(resp)
        r.request_id = "req_fake"
        return r

    # Batch
    def submit_batch(self, lines, *, metadata):
        if self.submit_error is not None:  # the create call failed as the client saw it; nothing exists here
            raise self.submit_error
        bid = f"batch_{len(self.batches) + 1}"
        self.batches[bid] = {"lines": lines, "metadata": metadata, "status": "validating", "lost": set(),
                             "errors": set(), "status_lines": {}}
        self.batch_lines += lines
        return {"id": bid, "status": "validating"}

    def retrieve_batch(self, bid):
        b = self.batches[bid]
        out = {"id": bid, "status": b["status"]}
        if b["status"] in runner.CHUNK_END:
            out.update(output_file_id=f"out_{bid}", error_file_id=f"err_{bid}")
        return out

    def file_lines(self, fid):
        """Output lines: 200s, or a status from `status_lines`. Error lines: `errors` is a set (code
        batch_cancelled) or a dict of custom_id to code."""
        kind, bid = fid.split("_", 1)
        b = self.batches[bid]
        errors = b["errors"] if isinstance(b["errors"], dict) else dict.fromkeys(b["errors"], "batch_cancelled")
        if kind == "out":
            out = []
            for x in b["lines"]:
                if x["custom_id"] in b["lost"] or x["custom_id"] in errors:
                    continue
                status = b["status_lines"].get(x["custom_id"], 200)
                resp = (response_for("ok", x["body"], "default") if status == 200
                        else {"error": {"message": "gateway timeout", "type": "server_error"}})
                out.append({"custom_id": x["custom_id"], "response": {"status_code": status, "request_id": "req_b",
                                                                      "body": resp}})
            return out
        return [{"custom_id": x["custom_id"], "response": None, "error": {"code": errors[x["custom_id"]]}}
                for x in b["lines"] if x["custom_id"] in errors]

    def cancel_batch(self, bid):
        self.cancelled.append(bid)
        self.batches[bid]["status"] = "cancelling"
        return {"id": bid, "status": "cancelling"}

    def find_batch(self, chunk_id):
        return next(({"id": k, **v} for k, v in self.batches.items() if v["metadata"]["chunk_id"] == chunk_id), None)


class Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class Env:
    def __init__(self, tmp_path: Path, jobs_changes=None, script=None):
        cfg = yaml.safe_load((ROOT / "config" / "openai_jobs.yaml").read_text())
        # These tests are written for O2's shape before the pilot (the planner's count, Sol on the edge stratum); the
        # live configuration's count and mix come from the pilot gate and must not change what the tests check.
        cfg["jobs"]["O2"].update(briefs=None, arms={"normal": ["terra-low"], "stressed": ["terra-low"],
                                                    "disrupted": ["terra-low"], "edge": ["sol-medium"]})
        for fn in jobs_changes or ():
            fn(cfg)
        table = yaml.safe_load((ROOT / "config" / "openai_prices.yaml").read_text())
        table["verified_at"] = (NOW - timedelta(hours=1)).isoformat()
        (tmp_path / "jobs.yaml").write_text(yaml.safe_dump(cfg))
        (tmp_path / "prices.yaml").write_text(yaml.safe_dump(table))
        self.cfg = cfg
        self.paths = runner.Paths(root=tmp_path, jobs_config=tmp_path / "jobs.yaml", prices=tmp_path / "prices.yaml",
                                  env=tmp_path / ".env")
        self.seams = FakeSeams()
        self.client = FakeClient(script)
        self.clock = Clock()
        self.client_factory = lambda: self.client

    def rewrite_jobs(self, fn):
        fn(self.cfg)
        self.paths.jobs_config.write_text(yaml.safe_dump(self.cfg))

    def cli(self, *args, now=NOW):
        lines = []
        code = pipeline.main(list(args), paths=self.paths, seams=self.seams, client_factory=self.client_factory,
                             now=lambda: now, out=lines.append, sleep=self.clock.sleep, clock=self.clock)
        return code, "\n".join(lines)

    def ledger(self):
        return L.Ledger(self.paths.ledger, jobs=self.cfg)

    def store(self):
        return runner.ItemStore(self.paths.items, self.paths.items_log)

    def items(self, job):
        s = self.store()
        try:
            return {i["item_id"]: i for i in s.items(job)}
        finally:
            s.close()

    def pass_gate(self):
        self.paths.pilot_gate.parent.mkdir(parents=True, exist_ok=True)
        self.paths.pilot_gate.write_text(json.dumps({"passed": True}))


def ok(result):
    code, out = result
    assert code == 0, out
    return out


def transitions_are_legal(env):
    for line in env.paths.items_log.read_text().splitlines():
        rec = json.loads(line)
        if "item_id" in rec and rec["from"] is not None:
            assert rec["to"] in runner.TRANSITIONS[rec["from"]], rec


# -- dry run and the guards on spending ------------------------------------------------------------------------------

def test_a_dry_run_spends_nothing_and_makes_no_network_call(tmp_path, monkeypatch):
    env = Env(tmp_path)

    def no_network(*args, **kwargs):
        raise AssertionError("the dry run opened a connection")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    env.client_factory = lambda: pytest.fail("the dry run built an API client")
    out = ok(env.cli("dry-run", "--job", "O1"))
    assert "nothing sent, nothing spent" in out and "Job cap $3.0000" in out
    d = json.loads(env.paths.dryrun("O1").read_text())
    assert d["requests"] == 72 and len(d["items"]) == 72  # 24 briefs on each of three models
    assert {(g["model"], g["effort"]) for g in d["groups"]} == {("gpt-6-luna", "low"), ("gpt-6.1-terra", "low"),
                                                                ("gpt-6.1-sol", "medium")}
    assert all(i["worst_micros"] > i["expected_micros"] > 0 for i in d["items"].values())
    assert d["estimator"] == "local" and len(d["request_set_hash"]) == 32
    lg = env.ledger()
    assert lg.entries() == [] and lg.totals()["accounts"]["openai"]["spent"] == 0
    assert env.client.calls == [] and not env.paths.responses.exists() and not env.paths.items.exists()


def test_the_token_count_option_is_gone(tmp_path):
    """F009: the counting endpoint's billing is undocumented and the byte bound replaces it, so the dry run has no
    option that calls the API."""
    env = Env(tmp_path)
    env.client_factory = lambda: pytest.fail("the dry run built an API client")
    with pytest.raises(SystemExit):
        env.cli("dry-run", "--job", "O1", "--count-tokens-api")
    assert not hasattr(pipeline.Pipeline, "_counter")
    ok(env.cli("dry-run", "--job", "O1"))
    assert env.ledger().entries() == []


def test_a_spend_needs_a_fresh_dry_run_of_the_same_requests(tmp_path):
    env = Env(tmp_path)
    code, out = env.cli("generate", "--job", "O1", "--budget-usd", "3")
    assert code == 1 and ("no briefs" in out or "no dry run" in out)
    ok(env.cli("dry-run", "--job", "O1"))
    d = json.loads(env.paths.dryrun("O1").read_text())
    d["created_at"] = (NOW - timedelta(hours=25)).isoformat()
    env.paths.dryrun("O1").write_text(json.dumps(d))
    code, out = env.cli("generate", "--job", "O1", "--budget-usd", "3")
    assert code == 1 and "hours old" in out
    ok(env.cli("dry-run", "--job", "O1", "--force"))
    code, out = env.cli("generate", "--job", "O1", "--budget-usd", "3", "--tier", "standard")
    assert code == 1 and "priced the flex tier" in out
    code, out = env.cli("generate", "--job", "O1", "--budget-usd", "3.01")
    assert code == 1 and "above O1's $3.00 cap" in out
    env.seams.dev = DEV + " One more sentence in the prompt."
    code, out = env.cli("generate", "--job", "O1", "--budget-usd", "3")
    assert code == 1 and "changed since its dry run" in out
    assert env.client.calls == [] and env.ledger().entries() == []
    ok(env.cli("dry-run", "--job", "O1"))  # a dry run of the new prompt clears the way
    ok(env.cli("generate", "--job", "O1", "--budget-usd", "3"))
    assert len(env.client.calls) == 72


def test_stale_prices_stop_a_spend(tmp_path):
    env = Env(tmp_path)
    ok(env.cli("dry-run", "--job", "O1"))
    code, out = env.cli("generate", "--job", "O1", "--budget-usd", "3", now=NOW + timedelta(hours=23, minutes=30))
    assert code == 1 and "re-verify" in out and env.client.calls == []


def test_o2_needs_the_o1_gate_and_o3_a_weak_spot_plan(tmp_path):
    env = Env(tmp_path)
    ok(env.cli("dry-run", "--job", "O2"))
    code, out = env.cli("generate", "--job", "O2", "--budget-usd", "44")
    assert code == 1 and "O1 gate" in out
    env.paths.pilot_gate.parent.mkdir(parents=True)
    env.paths.pilot_gate.write_text(json.dumps({"passed": False}))
    assert env.cli("generate", "--job", "O2", "--budget-usd", "44")[0] == 1
    env.paths.pilot_gate.write_text(json.dumps({"passed": "yes"}))  # only the JSON value true counts
    assert env.cli("generate", "--job", "O2", "--budget-usd", "44")[0] == 1
    assert env.client.calls == []
    env.pass_gate()
    ok(env.cli("generate", "--job", "O2", "--budget-usd", "44"))
    assert len(env.client.calls) == 8
    ok(env.cli("dry-run", "--job", "O3"))
    code, out = env.cli("generate", "--job", "O3", "--budget-usd", "18")
    assert code == 1 and "weak-spot plan" in out


def test_t1_spends_nothing_until_the_preregistration_names_the_frozen_model(tmp_path):
    env = Env(tmp_path, jobs_changes=[lambda c: c["jobs"]["T1"].update(briefs=8)])
    ok(env.cli("dry-run", "--job", "T1"))
    code, out = env.cli("generate", "--job", "T1", "--budget-usd", "20")
    assert code == 1 and "section 6" in out
    prereg = env.paths.preregistration
    prereg.parent.mkdir(parents=True)
    merged = (ROOT / "docs" / "neural" / "preregistration-v2.md").read_text(encoding="utf-8")
    prereg.write_text(merged, encoding="utf-8")  # as merged in Phase 1: section 6 still reads "to be filled"
    assert env.cli("generate", "--job", "T1", "--budget-usd", "20")[0] == 1
    prereg.write_text(merged.replace("to be filled", "filled", 3), encoding="utf-8")  # one field left
    assert env.cli("generate", "--job", "T1", "--budget-usd", "20")[0] == 1
    assert env.client.calls == [] and env.ledger().entries() == []
    prereg.write_text(merged.replace("to be filled", "filled"), encoding="utf-8")
    ok(env.cli("generate", "--job", "T1", "--budget-usd", "20"))
    assert len(env.client.calls) == 8


def test_an_unreleased_job_cannot_spend(tmp_path):
    env = Env(tmp_path)
    ok(env.cli("dry-run", "--job", "O1b"))
    code, out = env.cli("generate", "--job", "O1b", "--budget-usd", "2")
    assert code == 1 and "not released" in out
    ok(env.cli("ledger", "release-job", "O1b", "--reason", "pilot gate failed once"))
    ok(env.cli("dry-run", "--job", "O1b"))  # the ledger changed, so the dry run reruns
    ok(env.cli("generate", "--job", "O1b", "--budget-usd", "2"))
    assert len(env.client.calls) == 36


def test_one_generator_at_a_time(tmp_path):
    env = Env(tmp_path)
    ok(env.cli("dry-run", "--job", "O1"))
    with runner.GeneratorLock(env.paths.lock):
        code, out = env.cli("generate", "--job", "O1", "--budget-usd", "3")
    assert code == 1 and "one at a time" in out and env.client.calls == []


# -- sending ---------------------------------------------------------------------------------------------------------

def test_generate_sends_each_request_once_and_settles_each(tmp_path):
    env = Env(tmp_path)
    ok(env.cli("dry-run", "--job", "O1"))
    out = ok(env.cli("generate", "--job", "O1", "--budget-usd", "3"))
    assert "sent 72 requests" in out
    items = env.items("O1")
    assert len(items) == 72 and {i["state"] for i in items.values()} == {"accepted"}
    hashes = [c[0] for c in env.client.calls]
    assert len(hashes) == len(set(hashes)) == 72
    assert all((env.paths.responses / f"{h}.json").exists() for h in hashes)
    lg = env.ledger()
    t = lg.totals()["jobs"]["O1"]
    assert t["held"] == 0 and lg.open_reservations() == []
    # 24 x (luna 600 x 0.05 + 2000 x 0.25) + (terra 600 x 0.5 + 2000 x 2.5) + (sol 600 x 1 + 2000 x 5)
    assert t["spent"] == 24 * (530 + 5300 + 10600)
    assert L.replay(lg.mirror) == lg.balances()
    rows = {(r["model"], r["tier"]): r for r in lg.report()["rows"]}
    assert rows[("gpt-6.1-sol", "flex")]["tokens"]["reasoning"] == 24 * 700
    transitions_are_legal(env)
    out = ok(env.cli("generate", "--job", "O1", "--budget-usd", "3"))  # nothing left to do
    assert "sent 0 requests" in out and len(env.client.calls) == 72


def test_resume_after_a_crash_is_idempotent(tmp_path):
    env = Env(tmp_path)
    ok(env.cli("dry-run", "--job", "O1"))
    env.client.crash_after = 20
    with pytest.raises(Crash):
        env.cli("generate", "--job", "O1", "--budget-usd", "3")
    stranded = [i for i in env.items("O1").values() if i["state"] == "in_flight"]
    assert stranded, "the crash left nothing in flight, so resume is not exercised"
    with env.ledger() as lg:
        assert len(lg.open_reservations()) >= len(stranded)
        orphan = lg.reserve("O1", "O1-never-sent", "0.01")  # a reservation whose request never left
    env.client.crash_after = None
    ok(env.cli("generate", "--job", "O1", "--budget-usd", "3"))
    counts = Counter(c[0] for c in env.client.calls)
    assert max(counts.values()) == 1, "a request was sent twice"
    items = env.items("O1")
    units = {i["unit"] for i in items.values()}
    assert len(units) == 72
    for unit in units:
        states = {i["attempt"]: i["state"] for i in items.values() if i["unit"] == unit}
        assert states.get(1) == "accepted" or (states[1] == "unknown" and states[2] == "accepted"), (unit, states)
    unknown = [i for i in items.values() if i["state"] == "unknown"]
    assert unknown, "requests that died with the process must count as billed"
    with env.ledger() as lg:
        entries = lg.entries()
        assert next(e for e in entries if e["ref"] == orphan)["kind"] == "void"
        for i in unknown:
            settle = next(e for e in entries if e["ref"] == i["reservation_id"] and e["kind"] == "settle")
            assert settle["detail"]["unknown"] and settle["micros"] == settle["detail"]["reserved"]
        before = (len(entries), lg.balances(), env.items("O1"))
    calls = len(env.client.calls)
    out = ok(env.cli("generate", "--job", "O1", "--budget-usd", "3"))
    with env.ledger() as lg:
        after = (len(lg.entries()), lg.balances(), env.items("O1"))
    assert "sent 0 requests" in out and len(env.client.calls) == calls and after == before
    transitions_are_legal(env)


def test_a_stored_response_is_never_sent_again(tmp_path):
    env = Env(tmp_path)
    ok(env.cli("dry-run", "--job", "O1"))
    d = json.loads(env.paths.dryrun("O1").read_text())
    item_id, info = sorted(d["items"].items())[0]
    env.paths.responses.mkdir(parents=True)
    brief_id = item_id.split("-", 1)[1].rsplit(".", 1)[0]
    stored = response_for("ok", {"model": info["model"],
                                 "input": [{"role": "user", "content": json.dumps({"brief_id": brief_id})}]})
    (env.paths.responses / f"{info['request_hash']}.json").write_text(json.dumps(stored))
    ok(env.cli("generate", "--job", "O1", "--budget-usd", "3"))
    assert info["request_hash"] not in {c[0] for c in env.client.calls} and len(env.client.calls) == 71
    item = env.items("O1")[item_id]
    assert item["state"] == "accepted" and item["cached"] is True
    entries = [e for e in env.ledger().entries() if e["item_id"] == item_id]
    assert [e["kind"] for e in entries] == ["reserve", "void"]


def test_an_unknown_outcome_stays_billed_and_gets_its_repair_never_a_resend(tmp_path):
    env = Env(tmp_path, script={("S101-000", "gpt-6.1-terra", 1): ["timeout"],
                                ("S101-001", "gpt-6.1-sol", 1): ["connection"],
                                ("S101-002", "gpt-6-luna", 1): [("http", 504, {})]})
    ok(env.cli("dry-run", "--job", "O1"))
    ok(env.cli("generate", "--job", "O1", "--budget-usd", "3"))
    items = env.items("O1")
    lg = env.ledger()
    for unit in ("S101-000.terra-low", "S101-001.sol-medium", "S101-002.luna-low"):
        first, second = items[f"O1-{unit}-1"], items[f"O1-{unit}-2"]
        assert first["state"] == "unknown" and second["state"] == "accepted"
        assert second["repair_errors"] == [runner.UNKNOWN_TEXT]
        settle = next(e for e in lg.entries() if e["ref"] == first["reservation_id"] and e["kind"] == "settle")
        assert settle["detail"]["unknown"] and settle["micros"] == first["worst_micros"]
        assert [c[0] for c in env.client.calls].count(first["request_hash"]) == 1
    calls = len(env.client.calls)
    ok(env.cli("generate", "--job", "O1", "--budget-usd", "3"))
    assert len(env.client.calls) == calls


def test_one_repair_per_brief_with_the_validator_text_then_drop(tmp_path):
    env = Env(tmp_path, script={("S201-000", 1): [("invalid", "semantics", ["units[2] is off-site but uses a tube"])],
                                ("S201-001",): [("invalid", "names", ["narrative names an institution"])],
                                ("S201-002", 1): [("invalid", "dedup", ["near duplicate of S201-000"])],
                                ("S201-003", 1): ["incomplete"],
                                ("S201-004", 1): [("invalid", "plausibility", ["breach share 0.61"])],
                                ("S201-005", 1): [("invalid", "adherence", ["no outage in the morning peak"])]})
    env.pass_gate()
    ok(env.cli("dry-run", "--job", "O2"))
    ok(env.cli("generate", "--job", "O2", "--budget-usd", "44"))
    items = env.items("O2")
    assert items["O2-S201-000-1"]["state"] == "repair_planned"
    assert items["O2-S201-000-2"]["state"] == "accepted"
    assert items["O2-S201-000-2"]["repair_errors"] == ["units[2] is off-site but uses a tube"]
    repaired_user = [c for c in env.client.calls if c[1] == "S201-000" and c[3] == 2]
    assert len(repaired_user) == 1
    assert items["O2-S201-001-2"]["state"] == "dropped" and "O2-S201-001-3" not in items
    assert items["O2-S201-002-1"]["state"] == "dropped" and "O2-S201-002-2" not in items  # no repair for duplicates
    assert items["O2-S201-003-2"]["repair_errors"] == [runner.MODEL_FAILURES["incomplete"]]
    assert items["O2-S201-004-1"]["layer"] == "plausibility" and items["O2-S201-005-1"]["layer"] == "adherence"
    log = [json.loads(x) for x in env.paths.items_log.read_text().splitlines()]
    path = [r["to"] for r in log if r.get("item_id") == "O2-S201-005-1"]
    assert path == ["planned", "reserved", "in_flight", "received", "parsed", "validated", "sim_checked", "failed",
                    "repair_planned"]
    transitions_are_legal(env)


def test_a_flex_429_is_voided_retried_with_backoff_then_moved_to_batch(tmp_path):
    env = Env(tmp_path, script={("S101-005", "gpt-6.1-terra", 1): [("http", 429, {"resource_unavailable": True})]})
    ok(env.cli("dry-run", "--job", "O1"))
    out = ok(env.cli("generate", "--job", "O1", "--budget-usd", "3"))
    unit = "O1-S101-005.terra-low-1"
    tries = [c for c in env.client.calls if c[1:] == ("S101-005", "gpt-6.1-terra", 1)]
    assert len(tries) == runner.FLEX_TRIES and "moved to Batch" in out
    assert 0.75 * 1240 <= env.clock.t <= 1.25 * 1240 + 60  # five backoff waits, about 20 minutes
    entries = [e for e in env.ledger().entries() if e["item_id"] == unit]
    assert [e["kind"] for e in entries].count("void") == runner.FLEX_TRIES
    item = env.items("O1")[unit]
    assert item["state"] == "in_flight" and item["tier"] == "batch" and item["chunk_id"]
    assert [x["custom_id"] for x in env.client.batch_lines] == [unit]
    assert "service_tier" not in env.client.batch_lines[0]["body"]
    env.client.batches["batch_1"]["status"] = "completed"
    ok(env.cli("collect", "--job", "O1"))
    item = env.items("O1")[unit]
    assert item["state"] == "accepted"
    settle = next(e for e in env.ledger().entries() if e["item_id"] == unit and e["kind"] == "settle")
    assert settle["detail"]["tier"] == "batch" and settle["micros"] == 600 * 0.5 + 2000 * 2.5


def test_batch_chunks_keep_enqueued_input_tokens_under_the_queue_limit(tmp_path):
    env = Env(tmp_path)
    env.pass_gate()
    ok(env.cli("dry-run", "--job", "O2", "--tier", "batch"))
    d = json.loads(env.paths.dryrun("O2").read_text())
    queued = {k: -(-v["est_input_tokens"] * 115 // 100) for k, v in d["items"].items()}
    terra = sorted(k for k, v in d["items"].items() if v["model"] == "gpt-6.1-terra")
    limit = sum(queued[k] for k in terra[:3]) + 1  # room for exactly three terra requests
    env.rewrite_jobs(lambda c: c["models"]["gpt-6.1-terra"].update(batch_queue_limit_tokens=limit))
    out = ok(env.cli("generate", "--job", "O2", "--budget-usd", "44", "--tier", "batch"))
    assert "wait for room in the Batch queue" in out and env.client.calls == []

    def enqueued():
        s = env.store()
        try:
            return {c["model"]: c["input_tokens"] for c in s.chunks(open_only=True)}
        finally:
            s.close()
    assert enqueued()["gpt-6.1-terra"] < limit
    assert [i["state"] for i in env.items("O2").values() if i["model"] == "gpt-6.1-terra"].count("in_flight") == 3
    ok(env.cli("generate", "--job", "O2", "--budget-usd", "44", "--tier", "batch"))  # no room yet
    assert len(env.client.batch_lines) == 3 + 2  # three terra and both sol requests
    for b in env.client.batches.values():
        b["status"] = "completed"
    ok(env.cli("collect", "--job", "O2"))
    ok(env.cli("generate", "--job", "O2", "--budget-usd", "44", "--tier", "batch"))  # room again
    assert enqueued()["gpt-6.1-terra"] < limit and len(env.client.batch_lines) == 8
    for b in env.client.batches.values():
        b["status"] = "completed"
    ok(env.cli("collect", "--job", "O2"))
    assert {i["state"] for i in env.items("O2").values()} == {"accepted"}
    lg = env.ledger()
    assert lg.open_reservations() == []
    assert {e["detail"]["tier"] for e in lg.entries() if e["kind"] == "settle"} == {"batch"}
    assert len({x["custom_id"] for x in env.client.batch_lines}) == 8


def test_a_chunk_unfinished_after_six_hours_is_cancelled_and_its_items_return_to_flex(tmp_path):
    env = Env(tmp_path)
    env.pass_gate()
    ok(env.cli("dry-run", "--job", "O2", "--tier", "batch"))
    ok(env.cli("generate", "--job", "O2", "--budget-usd", "44", "--tier", "batch"))
    terra = next(b for b in env.client.batches.values() if b["metadata"] and
                 b["lines"][0]["body"]["model"] == "gpt-6.1-terra")
    for b in env.client.batches.values():
        b["status"] = "in_progress"
    ok(env.cli("collect", "--job", "O2", now=NOW + timedelta(hours=1)))
    assert env.client.cancelled == []
    out = ok(env.cli("collect", "--job", "O2", now=NOW + timedelta(hours=7)))
    assert len(env.client.cancelled) == 2 and "cancelling" in out
    ids = [x["custom_id"] for x in terra["lines"]]
    # One finished; two never ran before the window closed (batch_expired, proven unbilled); two carry another error
    # code, which proves nothing about billing (F002); one has no line at all.
    terra.update(status="cancelled", errors={ids[1]: "batch_expired", ids[2]: "batch_expired",
                                             ids[3]: "batch_cancelled", ids[4]: "batch_cancelled"}, lost={ids[5]})
    for b in env.client.batches.values():
        if b is not terra:
            b["status"] = "cancelled"
    ok(env.cli("collect", "--job", "O2", now=NOW + timedelta(hours=7, minutes=10)))
    items = env.items("O2")
    assert items[ids[0]]["state"] == "accepted"
    for iid in ids[1:3]:
        assert items[iid]["state"] == "unavailable" and items[iid]["tier"] == "flex"
    for iid in ids[3:6]:  # not proven unbilled: kept as spent, with a repair attempt
        assert items[iid]["state"] == "unknown"
        assert items[iid[:-1] + "2"]["repair_errors"] == [runner.UNKNOWN_TEXT]
    ok(env.cli("dry-run", "--job", "O2"))
    ok(env.cli("generate", "--job", "O2", "--budget-usd", "44"))  # Flex, under fresh reservations
    items = env.items("O2")
    for iid in ids[1:3]:
        assert items[iid]["state"] == "accepted"
        reserves = [e for e in env.ledger().entries() if e["item_id"] == iid and e["kind"] == "reserve"]
        assert [e["detail"]["tier"] for e in reserves] == ["batch", "flex"]
    sent = [c[0] for c in env.client.calls] + [runner.request_hash(x["body"]) for x in env.client.batch_lines]
    for iid in ids[3:6]:
        assert sent.count(items[iid]["request_hash"]) == 1, "an unknown outcome's request was sent again"
        assert items[iid[:-1] + "2"]["state"] == "accepted"
    transitions_are_legal(env)


def test_a_resumed_reservation_is_not_reused_at_another_tier(tmp_path):
    env = Env(tmp_path)
    env.pass_gate()
    ok(env.cli("dry-run", "--job", "O2", "--tier", "standard"))
    briefs = [json.loads(x) for x in env.paths.briefs("O2").read_text().splitlines()]
    reqs = runner.plan_requests(env.seams, env.cfg, "O2", briefs, "flex")
    iid = reqs[0]["item_id"]
    with env.ledger() as lg:  # left reserved at the Flex tier, as a crash before sending would leave it
        store = env.store()
        run = runner.Runner(job="O2", cfg=env.cfg, table=prices.load(env.paths.prices), ledger=lg, store=store,
                            client=env.client, seams=env.seams, paths=env.paths, tier="flex")
        run.plan_items(reqs)
        rid = lg.reserve("O2", iid, "0.046", detail={"tier": "flex"})
        store.transition(iid, "reserved", reservation_id=rid, reserved_tier="flex")
        store.close()
    ok(env.cli("generate", "--job", "O2", "--budget-usd", "44", "--tier", "standard"))
    entries = [e for e in env.ledger().entries() if e["item_id"] == iid]
    assert [e["kind"] for e in entries] == ["reserve", "void", "reserve", "settle", "release"]
    assert entries[2]["detail"]["tier"] == "standard" and entries[3]["detail"]["tier"] == "standard"
    assert entries[3]["micros"] == 600 * 1.0 + 2000 * 5.0  # Standard rates for gpt-6.1-terra
    assert {i["tier"] for i in env.items("O2").values()} == {"standard"}


# -- stops -----------------------------------------------------------------------------------------------------------

def test_a_response_without_usage_keeps_its_whole_reservation_and_the_job_goes_on(tmp_path):
    env = Env(tmp_path, script={("S101-001", "gpt-6.1-terra", 1): [("usage", None)]})
    ok(env.cli("dry-run", "--job", "O1"))
    ok(env.cli("generate", "--job", "O1", "--budget-usd", "3"))
    item = env.items("O1")["O1-S101-001.terra-low-1"]
    assert item["state"] == "accepted" and len(env.client.calls) == 72
    settle = next(e for e in env.ledger().entries() if e["ref"] == item["reservation_id"] and e["kind"] == "settle")
    assert settle["detail"]["unknown"] and settle["micros"] == item["worst_micros"]


def test_a_response_at_a_tier_the_table_cannot_price_stops_the_job(tmp_path):
    env = Env(tmp_path, script={("S101-000", "gpt-6.1-sol", 1): [("tier", "priority")]})
    ok(env.cli("dry-run", "--job", "O1"))
    code, out = env.cli("generate", "--job", "O1", "--budget-usd", "3")
    assert code == 2 and "could not be priced" in out
    item = env.items("O1")["O1-S101-000.sol-medium-1"]
    settle = next(e for e in env.ledger().entries() if e["ref"] == item["reservation_id"] and e["kind"] == "settle")
    assert settle["detail"]["unknown"] and settle["micros"] == item["worst_micros"]
    assert len(env.client.calls) < 72


def test_insufficient_quota_stops_the_job_and_says_the_account_is_out_of_credit(tmp_path):
    env = Env(tmp_path, script={"*": [("http", 429, {"error_type": "insufficient_quota",
                                                     "error_code": "credit_balance_exhausted"})]})
    ok(env.cli("dry-run", "--job", "O1"))
    code, out = env.cli("generate", "--job", "O1", "--budget-usd", "3")
    assert code == 2 and "out of credit" in out
    assert len(env.client.calls) <= runner.START_WORKERS
    lg = env.ledger()
    assert lg.totals()["jobs"]["O1"]["spent"] == 0 and lg.open_reservations() == []
    assert not any(i["state"] == "unknown" for i in env.items("O1").values())


def test_a_settlement_above_its_reservation_stops_the_job(tmp_path):
    huge = {"input_tokens": 5_000_000, "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens": 10, "output_tokens_details": {"reasoning_tokens": 0}}
    env = Env(tmp_path, script={("S101-000", "gpt-6.1-sol", 1): [("usage", huge)]})
    ok(env.cli("dry-run", "--job", "O1"))
    code, out = env.cli("generate", "--job", "O1", "--budget-usd", "3")
    assert code == 2 and "above its" in out
    assert len(env.client.calls) < 72
    report = env.ledger().report()
    assert sum(r["over_reservation"] for r in report["rows"]) == 1


def test_a_systematic_4xx_stops_the_job_with_nothing_billed(tmp_path):
    env = Env(tmp_path, script={"*": [("http", 400, {"error_type": "invalid_request_error",
                                                     "error_code": "invalid_value"})]})
    ok(env.cli("dry-run", "--job", "O1"))
    code, out = env.cli("generate", "--job", "O1", "--budget-usd", "3")
    assert code == 2 and "HTTP 400" in out
    assert env.ledger().totals()["jobs"]["O1"]["spent"] == 0


def test_the_cap_refuses_requests_it_cannot_cover(tmp_path):
    env = Env(tmp_path)
    ok(env.cli("dry-run", "--job", "O1"))
    code, out = env.cli("generate", "--job", "O1", "--budget-usd", "0.30")
    assert code == 2 and "budget" in out
    lg = env.ledger()
    t = lg.totals()["jobs"]["O1"]
    assert t["spent"] + t["held"] <= L.to_micros("0.30") and t["held"] == 0
    left = [i for i in env.items("O1").values() if i["state"] == "planned"]
    assert left, "items the budget could not cover stay planned for a later run"


# -- the spend-control findings of the independent review (F001 to F011) ---------------------------------------------

def standard_worst(env, item_id, job="O1", tier="flex"):
    """The reservation the runner must hold for an item: its byte bound and max_output_tokens at Standard rates."""
    briefs = [json.loads(x) for x in env.paths.briefs(job).read_text().splitlines()]
    req = next(r for r in runner.plan_requests(env.seams, env.cfg, job, briefs, tier) if r["item_id"] == item_id)
    r = prices.rates(prices.load(env.paths.prices), req["model"], "standard")
    return prices.worst_case_micros(runner.input_token_bound(req["body"]), req["max_output_tokens"], r)


def seed_spent(env, job, usd):
    with env.ledger() as lg:
        rid = lg.reserve(job, "earlier-run", usd)
        lg.settle(rid, usd, None, None, "flex")


def sent_hashes(env):
    return [c[0] for c in env.client.calls] + [runner.request_hash(x["body"]) for x in env.client.batch_lines]


def test_f001_a_flex_request_billed_at_standard_never_takes_the_job_past_its_cap(tmp_path):
    """The reviewer's probe: O1 at $2.94 spent, a Terra request reserved at Flex rates and billed at Standard
    ($0.0801 for 100 input and 16,000 output tokens) settled the job at $3.0201."""
    outputs = {"gpt-6-luna": 12000, "gpt-6.1-terra": 16000, "gpt-6.1-sol": 24000}
    script = {(f"S101-{i:03d}", model, 1): [("served", "default", {"input_tokens": 100, "output_tokens": out,
                                                                    "input_tokens_details": {"cached_tokens": 0,
                                                                                             "cache_write_tokens": 0}})]
              for i in range(24) for model, out in outputs.items()}
    env = Env(tmp_path, script=script)
    ok(env.cli("dry-run", "--job", "O1"))
    seed_spent(env, "O1", "2.94")
    env.cli("generate", "--job", "O1", "--budget-usd", "3")
    lg = env.ledger()
    t = lg.totals()["jobs"]["O1"]
    assert t["spent"] + t["held"] <= L.to_micros("3.00")
    settles = [e for e in lg.entries() if e["kind"] == "settle" and e["item_id"] != "earlier-run"]
    assert settles and not any(e["detail"]["over_reservation"] for e in settles)
    assert {e["detail"]["tier"] for e in settles} == {"standard"}  # settled at the tier actually served
    for e in lg.entries():
        if e["kind"] == "reserve" and e["item_id"] != "earlier-run":
            assert e["micros"] >= standard_worst(env, e["item_id"]), "a Flex request was reserved below Standard"
    assert not [c for c in env.client.calls if c[2] != "gpt-6-luna"], "only Luna's Standard worst case fits $0.06"


def test_f005_the_input_bound_is_every_byte_sent_plus_framing_whatever_the_script():
    messages = [{"role": "developer", "content": "Écrivez une semaine 🧪 " * 50},
                {"role": "user", "content": json.dumps({"brief_id": "B1", "note": "检验科 \"quoted\"\n"})}]
    body = {"model": "gpt-6.1-terra", "input": messages,
            "text": {"format": {"type": "json_schema", "name": "laboratory_week", "schema": SCHEMA, "strict": True}},
            "reasoning": {"effort": "low"}, "max_output_tokens": 16000, "store": False, "service_tier": "flex"}
    bound = runner.input_token_bound(body)
    sent_text = (sum(len(m["content"].encode("utf-8")) for m in messages) + len(body["model"].encode())
                 + len(json.dumps(body["text"]).encode("utf-8")))
    assert bound >= sent_text + runner.FRAMING_PER_REQUEST + runner.FRAMING_PER_MESSAGE * 2
    assert bound == len(runner.canonical(body).encode("utf-8")) + 64 + 16 * 2
    assert not runner.wants_12h_retention(body)
    assert runner.wants_12h_retention({**body, "prompt_cache_options": {"ttl": "12h"}})


def test_f005_billed_input_above_its_bound_stops_the_job(tmp_path):
    env = Env(tmp_path)
    ok(env.cli("dry-run", "--job", "O1"))
    bound = json.loads(env.paths.dryrun("O1").read_text())["items"]["O1-S101-000.luna-low-1"]["input_bound"]
    over = {"input_tokens": bound + 1, "output_tokens": 10,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}}
    env.client.script = {("S101-000", "gpt-6-luna", 1): [("usage", over)]}
    code, out = env.cli("generate", "--job", "O1", "--budget-usd", "3")
    assert code == 2 and "input bound" in out and len(env.client.calls) < 72


def test_f005_the_dry_run_prices_the_worst_case_from_the_byte_bound_and_shows_measured_input(tmp_path):
    env = Env(tmp_path)
    out = ok(env.cli("dry-run", "--job", "O1"))
    d = json.loads(env.paths.dryrun("O1").read_text())
    table = prices.load(env.paths.prices)
    for iid, info in d["items"].items():
        assert info["worst_micros"] == standard_worst(env, iid)
        assert info["input_bound"] > info["est_input_tokens"]
        flex = prices.rates(table, info["model"], "flex")
        assert info["expected_micros"] == prices.expected_micros(info["est_input_tokens"],
                                                                 env.cfg["arms"][iid.split(".")[1][:-2]]
                                                                 ["expected_output_tokens"], flex)
    assert "byte" in out and "Standard" in out and "measured" not in out
    ok(env.cli("generate", "--job", "O1", "--budget-usd", "3"))  # the fake usage reports 600 input tokens each
    out = ok(env.cli("dry-run", "--job", "O2"))
    assert "measured median input 600 tokens" in out


def test_f002_server_errors_stay_billed_and_get_a_repair_never_a_resend(tmp_path):
    used = {"input_tokens": 900, "output_tokens": 300, "output_tokens_details": {"reasoning_tokens": 200}}
    env = Env(tmp_path, script={("S101-000", "gpt-6.1-terra", 1): [("http", 500, {"usage": used,
                                                                                  "tokens_consumed": True})],
                                ("S101-001", "gpt-6.1-terra", 1): [("http", 503, {})],
                                ("S101-002", "gpt-6.1-terra", 1): [("http", 502, {"tokens_consumed": True})]})
    ok(env.cli("dry-run", "--job", "O1"))
    ok(env.cli("generate", "--job", "O1", "--budget-usd", "3"))
    items, lg = env.items("O1"), env.ledger()
    for n in range(3):
        first, second = items[f"O1-S101-00{n}.terra-low-1"], items[f"O1-S101-00{n}.terra-low-2"]
        assert first["state"] == "unknown" and second["state"] == "accepted"
        assert second["repair_errors"] == [runner.UNKNOWN_TEXT]
        assert sent_hashes(env).count(first["request_hash"]) == 1
        settle = next(e for e in lg.entries() if e["ref"] == first["reservation_id"] and e["kind"] == "settle")
        if n == 0:  # the usage it reported, at Standard rates (an error names no tier): 900 x 1.25 + 300 x 5.00
            assert settle["micros"] == 1125 + 1500 and not settle["detail"].get("unknown")
        else:
            assert settle["detail"]["unknown"] and settle["micros"] == first["worst_micros"]
    assert not any(e["kind"] == "void" for e in lg.entries())


def test_f002_repeated_server_errors_stop_the_job(tmp_path):
    env = Env(tmp_path, script={"*": [("http", 500, {})]})
    ok(env.cli("dry-run", "--job", "O1"))
    code, out = env.cli("generate", "--job", "O1", "--budget-usd", "3")
    assert code == 2 and "server errors" in out and len(env.client.calls) <= runner.START_WORKERS
    lg = env.ledger()
    assert lg.open_reservations() == [] and not any(e["kind"] == "void" for e in lg.entries())


@pytest.mark.parametrize("failure", ["refusal", "incomplete"])
def test_f012_repeated_billed_but_unusable_answers_stop_the_job(tmp_path, failure):
    """An HTTP 200 refusal or truncated reply is billed and unusable; a run of them stops the job like an outage
    (independent review finding F012), and every call already sent still settles."""
    env = Env(tmp_path, script={"*": [failure]})
    ok(env.cli("dry-run", "--job", "O1"))
    code, out = env.cli("generate", "--job", "O1", "--budget-usd", "3")
    assert code == 2 and "unusable answer" in out
    assert len(env.client.calls) < 72  # stopped well before every brief and repair was bought
    lg = env.ledger()
    assert lg.open_reservations() == [] and sum(e["kind"] == "settle" for e in lg.entries()) == len(env.client.calls)


def test_f012_answers_that_fail_validation_still_count_as_usable(tmp_path):
    """A configuration that parses but fails a validation layer is a usable answer: it gets its repair and never
    trips the outage stop."""
    env = Env(tmp_path)
    env.seams.validate = lambda scenario, brief: (False, "plausibility", ["too slow"])
    ok(env.cli("dry-run", "--job", "O1"))
    code, out = env.cli("generate", "--job", "O1", "--budget-usd", "3")
    assert "unusable answer" not in out


def test_f002_errors_proven_unbilled_are_voided_and_the_rest_are_not():
    c = runner.billing_outcome
    assert c(status=429, kind="http_429") == "void"  # Flex resource unavailable, a rate limit or a quota refusal
    for status in (400, 401, 403, 404, 409, 413, 415, 422):
        assert c(status=status, kind=f"http_{status}") == "void"
    for status in (408, 500, 502, 503, 504, 524, 402, 410, 499):
        assert c(status=status, kind=f"http_{status}") == "unknown", status
    assert c(status=None, kind="timeout") == "unknown" and c(status=None, kind="connection") == "unknown"
    assert c(status=400, kind="http_400", usage={"input_tokens": 5, "output_tokens": 0}) == "usage"
    assert c(status=429, kind="http_429", tokens_consumed=True) == "unknown"
    assert c(status=200, kind="ok") == "usage"
    assert c(status=None, kind="batch_error", batch_code="batch_expired") == "void"
    for code in ("batch_cancelled", "batch_failed", None, "server_error"):
        assert c(status=None, kind="batch_error", batch_code=code) == "unknown"
    assert c(status=504, kind="batch_line") == "unknown" and c(status=400, kind="batch_line") == "unknown"


def test_f002_a_batch_504_line_stays_billed_and_its_request_is_never_resent(tmp_path):
    env = Env(tmp_path)
    env.pass_gate()
    ok(env.cli("dry-run", "--job", "O2", "--tier", "batch"))
    ok(env.cli("generate", "--job", "O2", "--budget-usd", "44", "--tier", "batch"))
    terra = next(b for b in env.client.batches.values() if b["lines"][0]["body"]["model"] == "gpt-6.1-terra")
    ids = [x["custom_id"] for x in terra["lines"]]
    terra["status_lines"] = {ids[0]: 504}
    terra["errors"] = {ids[1]: "batch_expired"}
    for b in env.client.batches.values():
        b["status"] = "completed"
    ok(env.cli("collect", "--job", "O2"))
    items, lg = env.items("O2"), env.ledger()
    first = items[ids[0]]
    assert first["state"] == "unknown" and items[ids[0][:-1] + "2"]["repair_errors"] == [runner.UNKNOWN_TEXT]
    settle = next(e for e in lg.entries() if e["ref"] == first["reservation_id"] and e["kind"] == "settle")
    assert settle["detail"]["unknown"] and settle["micros"] == first["worst_micros"]
    assert items[ids[1]]["state"] == "unavailable" and items[ids[1]]["tier"] == "flex"  # expired: never ran
    ok(env.cli("dry-run", "--job", "O2"))
    ok(env.cli("generate", "--job", "O2", "--budget-usd", "44"))
    assert sent_hashes(env).count(first["request_hash"]) == 1
    assert env.items("O2")[ids[1]]["state"] == "accepted"


@pytest.mark.parametrize("failure", [dict(kind="timeout"), dict(kind="http_408", status=408),
                                     dict(kind="http_500", status=500), dict(kind="exception")])
def test_f003_an_uncertain_batch_create_keeps_its_reservations_and_a_list_miss_never_resubmits(tmp_path, failure):
    env = Env(tmp_path)
    env.pass_gate()
    ok(env.cli("dry-run", "--job", "O2", "--tier", "batch"))
    env.client.submit_error = ClientError(stage="create", **failure)
    ok(env.cli("generate", "--job", "O2", "--budget-usd", "44", "--tier", "batch"))
    items = env.items("O2")
    first = {iid: i for iid, i in items.items() if i["attempt"] == 1}
    assert {i["state"] for i in first.values()} == {"in_flight"}
    lg = env.ledger()
    assert lg.totals()["jobs"]["O2"]["held"] == sum(standard_worst(env, iid, "O2", "batch") for iid in first)
    reserved = {e["item_id"]: e["micros"] for e in lg.entries() if e["kind"] == "reserve"}
    env.client.submit_error = None
    ok(env.cli("collect", "--job", "O2"))  # the paged search finds nothing: inconclusive, not proof
    items = env.items("O2")
    lg = env.ledger()
    for iid in first:
        assert items[iid]["state"] == "unknown"
        settle = next(e for e in lg.entries() if e["ref"] == items[iid]["reservation_id"] and e["kind"] == "settle")
        assert settle["detail"]["unknown"] and settle["micros"] == reserved[iid]
        assert items[iid[:-1] + "2"]["repair_errors"] == [runner.UNKNOWN_TEXT]
    assert not any(e["kind"] == "void" for e in lg.entries())
    ok(env.cli("generate", "--job", "O2", "--budget-usd", "44", "--tier", "batch"))
    assert {x["custom_id"][-1] for x in env.client.batch_lines} == {"2"}, "an original request was resubmitted"
    assert not set(sent_hashes(env)) & {i["request_hash"] for i in first.values()}


def test_f013_a_crash_while_preparing_a_batch_chunk_is_reconciled_as_unsent(tmp_path, monkeypatch):
    """Every member moves to in_flight before the create call, so a member still reserved after a crash proves the
    chunk was never submitted: collect ends it as unsent, voids every hold, and the requests are sent once later
    (independent review finding F013)."""
    env = Env(tmp_path)
    env.pass_gate()
    ok(env.cli("dry-run", "--job", "O2", "--tier", "batch"))
    real = runner.ItemStore.transition
    moved = []

    def crash_on_third_enqueue(self, item_id, state, *a, **k):
        if state == "in_flight" and "enqueued in" in (a[0] if a else ""):
            moved.append(item_id)
            if len(moved) == 3:
                raise Crash()
        return real(self, item_id, state, *a, **k)

    monkeypatch.setattr(runner.ItemStore, "transition", crash_on_third_enqueue)
    with pytest.raises(Crash):
        env.cli("generate", "--job", "O2", "--budget-usd", "44", "--tier", "batch")
    monkeypatch.setattr(runner.ItemStore, "transition", real)
    items = env.items("O2")
    crashed = next(i["chunk_id"] for i in items.values() if i["state"] == "reserved" and i.get("chunk_id"))
    members = {iid for iid, i in items.items() if i.get("chunk_id") == crashed}
    assert members  # the stranded member the review found (with any moved before it)
    assert not members & {x["custom_id"] for x in env.client.batch_lines}  # that chunk was never submitted
    ok(env.cli("collect", "--job", "O2"))
    items, lg = env.items("O2"), env.ledger()
    assert all(items[iid]["state"] == "unavailable" and not items[iid].get("chunk_id") for iid in members)
    assert not {r["item_id"] for r in lg.open_reservations()} & members
    assert not any(e["kind"] == "settle" and e["item_id"] in members for e in lg.entries())
    ok(env.cli("generate", "--job", "O2", "--budget-usd", "44", "--tier", "batch"))
    ids = [x["custom_id"] for x in env.client.batch_lines]
    assert len(ids) == len(set(ids)) and members <= set(ids)  # every request sent exactly once


def test_f003_a_definite_pre_execution_refusal_of_the_create_call_voids(tmp_path):
    env = Env(tmp_path)
    env.pass_gate()
    ok(env.cli("dry-run", "--job", "O2", "--tier", "batch"))
    env.client.submit_error = ClientError(stage="create", kind="http_400", status=400)
    ok(env.cli("generate", "--job", "O2", "--budget-usd", "44", "--tier", "batch"))
    lg = env.ledger()
    assert lg.totals()["jobs"]["O2"]["spent"] == 0 and lg.open_reservations() == []
    assert {i["state"] for i in env.items("O2").values()} == {"unavailable"}
    env.client.submit_error = None
    ok(env.cli("generate", "--job", "O2", "--budget-usd", "44", "--tier", "batch"))
    assert len(env.client.batch_lines) == 8


def test_f004_a_resumed_reservation_below_the_current_worst_case_is_topped_up_before_sending(tmp_path):
    env = Env(tmp_path)
    ok(env.cli("dry-run", "--job", "O1"))
    briefs = [json.loads(x) for x in env.paths.briefs("O1").read_text().splitlines()]
    reqs = runner.plan_requests(env.seams, env.cfg, "O1", briefs, "flex")
    iid = "O1-S101-000.terra-low-1"
    with env.ledger() as lg:  # left reserved, as a crash before sending leaves it, under older and lower prices
        store = env.store()
        run = runner.Runner(job="O1", cfg=env.cfg, table=prices.load(env.paths.prices), ledger=lg, store=store,
                            client=env.client, seams=env.seams, paths=env.paths, tier="flex")
        run.plan_items(reqs)
        rid = lg.reserve("O1", iid, "0.040124", detail={"tier": "flex"})
        store.transition(iid, "reserved", reservation_id=rid, reserved_tier="flex")
        store.close()
    ok(env.cli("generate", "--job", "O1", "--budget-usd", "3"))
    entries = [e for e in env.ledger().entries() if e["item_id"] == iid]
    assert [e["kind"] for e in entries] == ["reserve", "void", "reserve", "settle", "release"]
    assert entries[1]["ref"] == rid and entries[2]["micros"] == standard_worst(env, iid)
    assert entries[3]["ref"] == entries[2]["entry_id"]


def test_f004_a_top_up_that_does_not_fit_is_refused_and_nothing_is_sent(tmp_path):
    env = Env(tmp_path, jobs_changes=[lambda c: c["jobs"]["O1"].update(briefs=1)])
    ok(env.cli("dry-run", "--job", "O1"))
    briefs = [json.loads(x) for x in env.paths.briefs("O1").read_text().splitlines()]
    reqs = runner.plan_requests(env.seams, env.cfg, "O1", briefs, "flex")
    iid = "O1-S101-000.terra-low-1"
    seed_spent(env, "O1", "2.94")
    with env.ledger() as lg:
        store = env.store()
        run = runner.Runner(job="O1", cfg=env.cfg, table=prices.load(env.paths.prices), ledger=lg, store=store,
                            client=env.client, seams=env.seams, paths=env.paths, tier="flex")
        run.plan_items(reqs)
        rid = lg.reserve("O1", iid, "0.040124", detail={"tier": "flex"})
        store.transition(iid, "reserved", reservation_id=rid, reserved_tier="flex")
        store.close()
    env.cli("generate", "--job", "O1", "--budget-usd", "3")
    item = env.items("O1")[iid]
    assert item["state"] == "refused" and item["request_hash"] not in sent_hashes(env)
    lg = env.ledger()
    assert [e["kind"] for e in lg.entries() if e["item_id"] == iid] == ["reserve", "void"]
    t = lg.totals()["jobs"]["O1"]
    assert t["held"] == 0 and t["spent"] <= L.to_micros("3.00")


def test_f006_batch_submission_serves_stored_responses_and_never_uploads_them(tmp_path):
    env = Env(tmp_path)
    env.pass_gate()
    ok(env.cli("dry-run", "--job", "O2", "--tier", "batch"))
    d = json.loads(env.paths.dryrun("O2").read_text())
    item_id, info = sorted(d["items"].items())[0]
    env.paths.responses.mkdir(parents=True)
    stored = response_for("ok", {"model": info["model"], "input": [
        {"role": "user", "content": json.dumps({"brief_id": item_id.split("-", 1)[1].rsplit("-", 1)[0]})}]})
    (env.paths.responses / f"{info['request_hash']}.json").write_text(json.dumps(stored))
    ok(env.cli("generate", "--job", "O2", "--budget-usd", "44", "--tier", "batch"))
    assert item_id not in [x["custom_id"] for x in env.client.batch_lines]
    assert len(env.client.batch_lines) == 7
    item = env.items("O2")[item_id]
    assert item["state"] == "accepted" and item["cached"] is True
    assert not [e for e in env.ledger().entries() if e["item_id"] == item_id and e["kind"] != "void"
                and e["kind"] != "reserve"]
    transitions_are_legal(env)


def test_f007_batch_collection_resumes_after_a_crash_between_settling_and_moving_an_item(tmp_path, monkeypatch):
    env = Env(tmp_path)
    env.pass_gate()
    ok(env.cli("dry-run", "--job", "O2", "--tier", "batch"))
    ok(env.cli("generate", "--job", "O2", "--budget-usd", "44", "--tier", "batch"))
    terra = next(b for b in env.client.batches.values() if b["lines"][0]["body"]["model"] == "gpt-6.1-terra")
    ids = [x["custom_id"] for x in terra["lines"]]
    terra["errors"] = {ids[1]: "batch_expired"}
    for b in env.client.batches.values():
        b["status"] = "completed"
    real_transition, real_void = runner.ItemStore.transition, L.Ledger.void
    crashed = []

    def transition(self, item_id, to, detail=None, **fields):
        if to == "received" and "settled" not in crashed:  # the settlement committed; the item did not move
            crashed.append("settled")
            raise Crash()
        return real_transition(self, item_id, to, detail, **fields)
    monkeypatch.setattr(runner.ItemStore, "transition", transition)
    with pytest.raises(Crash):
        env.cli("collect", "--job", "O2")

    def void(self, entry_id, reason, request_id=None):
        if "batch_expired" in reason and "voided" not in crashed:  # the item moved; the void did not commit
            crashed.append("voided")
            raise Crash()
        return real_void(self, entry_id, reason, request_id)
    monkeypatch.setattr(L.Ledger, "void", void)
    with pytest.raises(Crash):
        env.cli("collect", "--job", "O2")
    monkeypatch.undo()
    ok(env.cli("collect", "--job", "O2"))
    items, lg = env.items("O2"), env.ledger()
    assert crashed == ["settled", "voided"]
    assert items[ids[1]]["state"] == "unavailable" and items[ids[1]]["tier"] == "flex"
    assert all(items[i]["state"] == "accepted" for i in items if i != ids[1])
    closes = Counter(e["ref"] for e in lg.entries() if e["kind"] in ("settle", "void"))
    assert max(closes.values()) == 1 and lg.open_reservations() == []
    assert L.replay(lg.mirror) == lg.balances()
    transitions_are_legal(env)


def test_f008_generate_sends_only_the_checked_request_set_and_its_repairs(tmp_path):
    env = Env(tmp_path, jobs_changes=[lambda c: c["jobs"]["O1"].update(briefs=2)])
    ok(env.cli("dry-run", "--job", "O1"))
    code, out = env.cli("generate", "--job", "O1", "--budget-usd", "0.000001")  # stops at once; items stay planned
    assert code == 2 and len(env.items("O1")) == 6 and env.client.calls == []
    env.rewrite_jobs(lambda c: c["jobs"]["O1"].update(briefs=1))
    env.client.script = {("S101-000", "gpt-6-luna", 1): [("invalid", "semantics", ["fix the units"])]}
    ok(env.cli("dry-run", "--job", "O1"))
    out = ok(env.cli("generate", "--job", "O1", "--budget-usd", "3"))
    assert "3 pending items are outside the checked request set" in out
    assert {c[1] for c in env.client.calls} == {"S101-000"} and len(env.client.calls) == 4  # three and one repair
    items = env.items("O1")
    assert {i["state"] for iid, i in items.items() if "S101-001" in iid} == {"planned"}


def test_f011_a_dry_run_dated_in_the_future_or_without_a_zone_is_refused(tmp_path):
    env = Env(tmp_path)
    ok(env.cli("dry-run", "--job", "O1"))
    d = json.loads(env.paths.dryrun("O1").read_text())
    for created, refused in (((NOW + timedelta(days=365)).isoformat(), "future"),
                             ((NOW + timedelta(minutes=6)).isoformat(), "future"),
                             (NOW.replace(tzinfo=None).isoformat(), "time zone")):
        env.paths.dryrun("O1").write_text(json.dumps({**d, "created_at": created}))
        code, out = env.cli("generate", "--job", "O1", "--budget-usd", "3")
        assert code == 1 and refused in out, created
    assert env.client.calls == []
    env.paths.dryrun("O1").write_text(json.dumps({**d, "created_at": (NOW + timedelta(minutes=4)).isoformat()}))
    ok(env.cli("generate", "--job", "O1", "--budget-usd", "3"))


# -- units -----------------------------------------------------------------------------------------------------------

def test_an_illegal_transition_is_refused(tmp_path):
    s = runner.ItemStore(tmp_path / "items.sqlite", tmp_path / "items.jsonl")
    s.add({"item_id": "O1-x-1", "job": "O1"})
    with pytest.raises(runner.JobError, match="not a transition"):
        s.transition("O1-x-1", "received")
    s.transition("O1-x-1", "reserved")
    with pytest.raises(runner.JobError):
        s.transition("O1-x-1", "planned")


def test_the_throttle_keeps_requested_tokens_per_minute_under_eighty_percent():
    clock = Clock()
    t = runner.Throttle({"m": 100_000}, clock)
    sent = 0
    while clock.t < 300:
        wait_s = t.wait_s("m", 17_000)
        if wait_s:
            clock.sleep(wait_s)
            continue
        t.take("m", 17_000)
        sent += 1
        in_window = sum(n for at, n in t.window["m"] if clock.t - at < 60)
        assert in_window <= 80_000
    assert 4 * 4 <= sent <= 4 * 6
    from src.scenarios.client import RateLimits
    t.observe("m", RateLimits(limit_tokens=2_000_000))
    assert t.limit["m"] == 2_000_000


def test_item_ids_follow_the_spec():
    assert runner.make_item_id("O2", "S201-004", 1) == "O2-S201-004-1"
    seams, cfg = FakeSeams(), yaml.safe_load((ROOT / "config" / "openai_jobs.yaml").read_text())
    cfg["jobs"]["O2"]["arms"]["edge"] = ["sol-medium"]  # the pre-pilot shape this test checks, as in Env
    briefs = seams.plan("O1", 101, n=4)
    o1 = runner.plan_requests(seams, cfg, "O1", briefs, "flex")
    assert [r["item_id"] for r in o1[:3]] == ["O1-S101-000.luna-low-1", "O1-S101-000.terra-low-1",
                                              "O1-S101-000.sol-medium-1"]
    o2 = runner.plan_requests(seams, cfg, "O2", briefs, "flex")
    assert [(r["item_id"], r["model"]) for r in o2] == [("O2-S101-000-1", "gpt-6.1-terra"),
                                                        ("O2-S101-001-1", "gpt-6.1-terra"),
                                                        ("O2-S101-002-1", "gpt-6.1-terra"),
                                                        ("O2-S101-003-1", "gpt-6.1-sol")]
    body = o2[0]["body"]
    assert body["service_tier"] == "flex" and body["prompt_cache_key"] == "tracer-v2-O2"
    assert runner.request_hash(body) == runner.request_hash({k: v for k, v in body.items() if k != "service_tier"})


def test_real_validate_names_each_layer_for_the_runner_and_never_repairs_a_near_duplicate(monkeypatch):
    """src/scenarios/validate.py reports "6 diversity"; the runner's stage map and no-repair set need bare names,
    or a near duplicate would be sent for a paid repair it can never pass."""
    import sys
    import types
    from src.scenarios import validate as v

    monkeypatch.setitem(sys.modules, "src.sim.kpi", types.SimpleNamespace(check=lambda scenario, stratum: []))
    for number, name in runner.LAYER_NAMES.items():
        monkeypatch.setattr(v, "validate", lambda s, b, **k: v.Result(False, f"{number} {v.LAYERS[number]}", ["x"]))
        ok, layer, errors = runner.real_validate({}, {})
        assert (ok, layer, errors) == (False, name, ["x"])
    assert runner.LAYER_NAMES[6] in runner.NO_REPAIR_LAYERS
    assert runner.LAYER_STAGE[runner.LAYER_NAMES[4]] == "validated"
    assert runner.LAYER_STAGE[runner.LAYER_NAMES[5]] == "sim_checked"


def test_a_repair_request_through_the_real_planner_and_prompt_carries_every_error():
    """The runner passes the validator's messages as a list; the real prompt must write them all. The fakes above
    took any type, so this path broke only against the real modules (the first pilot run stopped on it)."""
    from src.scenarios import planner
    cfg = runner.load_jobs(runner.Paths().jobs_config)
    brief = planner.plan("O1", 101)[0]
    body = runner.build_request(runner.real_seams(), cfg, "O1", brief, "terra-low", "flex",
                                ["first reason", "second reason"])
    user = body["input"][-1]["content"]
    assert "- first reason" in user and "- second reason" in user and "rejected for these reasons" in user
