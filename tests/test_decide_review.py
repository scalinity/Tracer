"""Regressions for the independent review of the decision layer (findings F001 to F007), and the billing record the
X1 pilot reads. Each test fails on the reviewed commit 60530f1 and passes after its fix. A mock transport stands in
for the network; nothing leaves the machine."""

import json
import logging
import threading
from datetime import timedelta

import httpx2
import pytest

from src.decide import client as dc
from src.decide import render
from test_decide import (FAKE_KEY, NOW, RESOLVED, Transport, answer_for, config, env, items, kinds,  # noqa: F401
                         make_job, ok)


# --- F001: no request starts once the job has stopped -----------------------------------------------------------------

def test_f001_a_request_is_not_admitted_after_the_job_stops_and_its_reservation_is_voided(env):
    tmp, ledger = env
    t = Transport()
    job = make_job(env, t)

    class StopWhileWaiting(dc._Gate):
        """Another request stops the job while this one waits at the gate."""

        def __enter__(self):
            job.stop("another request saw a 402")
            return super().__enter__()
    job.gate = StopWhileWaiting(1, 1)
    r = job.run(items(2))
    assert t.requests == [] and r.sent == 0
    assert [o.status for o in r.outcomes] == ["fallback", "fallback"]
    assert kinds(ledger) == ["reserve", "void"]                     # reserved, refused at admission, handed back


def test_f001_a_402_on_one_request_keeps_the_waiting_ones_from_starting(env):
    """Two workers: the second request has reserved while the first is in flight; the first comes back 402 and
    stops the job; the second reaches the gate after that stop and must be refused, not sent (the reviewer's
    sequence). A request admitted before a stop may finish; this one was not."""
    tmp, ledger = env
    first_in, release = threading.Event(), threading.Event()

    def route(request):
        first_in.set()
        release.wait(5)
        return httpx2.Response(402, content=json.dumps({"error": {"code": 402}}))
    client = dc.DecisionClient(config(), env_path=tmp / ".env", transport=httpx2.MockTransport(route))
    dc.save_state(tmp / "decide", "X2", {"model": "nace-ai/drex-max", "resolved_model": RESOLVED, "versions": [],
                                         "events": []})                 # pinned, so no first-request probe
    job = dc.Job("X2", config(), ledger, client_factory=lambda: client, budget_usd="1", decide_dir=tmp / "decide",
                 sleep=lambda s: None, concurrency=2, out=lambda s: None)
    job.gate.limit = 1                                                  # two workers, one slot
    calls = []
    real_enter = dc._Gate.__enter__

    def enter(gate):
        calls.append(1)
        if len(calls) == 2:                  # the second request, reserved, reaches the gate only after the stop
            assert first_in.wait(5)
            release.set()
            for _ in range(500):
                if job.stopped:
                    break
                threading.Event().wait(0.01)
            assert job.stopped
        return real_enter(gate)
    job.gate.__class__ = type("Gate", (dc._Gate,), {"__enter__": enter})
    r = job.run(items(2))
    assert len(calls) == 2 and "402" in r.stopped
    assert r.sent == 1 and [o.status for o in r.outcomes] == ["fallback", "fallback"]


# --- F002: an error settled over its reservation stops the job ---------------------------------------------------------

def test_f002_an_error_settlement_over_its_reservation_stops_the_job(env):
    tmp, ledger = env
    over = (400, {"error": {"code": 400}, "usage": {"input_tokens": 10, "output_tokens": 0, "cost": 0.5}})
    t = Transport(ok(), over, ok())
    r = make_job(env, t).run(items(3))
    assert r.stopped and "passed its reservation" in r.stopped and len(t.requests) == 2
    settle = [e for e in ledger.entries("X2") if e["kind"] == "settle"][-1]
    assert settle["detail"]["over_reservation"] is True and settle["micros"] == 500_000


# --- F003: unknown outcomes count whatever their status --------------------------------------------------------------

def test_f003_503s_settled_as_unknown_count_toward_the_stop_and_end_the_retries(env):
    tmp, ledger = env
    t = Transport(ok(), (503, {}))                                  # every later request: 503 with no usage
    r = make_job(env, t).run(items(3))
    assert r.stopped and "unknown outcome" in r.stopped
    assert len(t.requests) == 1 + 5                                 # five unknown outcomes, then nothing more
    unknown = [e for e in ledger.entries("X2") if e["kind"] == "settle" and e["detail"].get("unknown")]
    assert len(unknown) == 5


# --- F004: the job itself enforces the price and the budget ----------------------------------------------------------

def test_f004_a_job_with_an_unverified_price_sends_nothing(env):
    tmp, ledger = env
    cfg = config()
    cfg["price"]["verified_at"] = None
    t = Transport()
    r = make_job(env, t, cfg).run(items(2))
    assert t.requests == [] and "never been verified" in r.stopped and ledger.entries("X2") == []


def test_f004_a_price_going_stale_during_the_job_stops_the_next_admission(env):
    tmp, ledger = env
    t = Transport()
    cfg = config()
    clock = lambda: NOW + (timedelta(hours=25) if t.requests else timedelta(0))  # noqa: E731
    client = dc.DecisionClient(cfg, env_path=tmp / ".env", transport=httpx2.MockTransport(t))
    r = dc.Job("X2", cfg, ledger, client_factory=lambda: client, budget_usd="1", decide_dir=tmp / "decide",
               sleep=lambda s: None, concurrency=1, out=lambda s: None, now=clock).run(items(3))
    assert len(t.requests) == 1 and "hours ago" in r.stopped
    assert [o.status for o in r.outcomes] == ["answered", "fallback", "fallback"]


def test_f004_a_job_refuses_a_budget_past_its_cap(env):
    tmp, ledger = env
    with pytest.raises(dc.JobError, match="cap"):
        dc.Job("X2", config(), ledger, client_factory=lambda: None, budget_usd="5.00", decide_dir=tmp / "decide")


# --- F005: no echoed key reaches a file, a message or a log ----------------------------------------------------------

def test_f005_an_echoed_key_reaches_no_cache_file_no_stop_message_and_no_log(env, caplog):
    tmp, ledger = env

    def echo(q):
        body = answer_for(q)
        body["answers"]["escalate"] = {"type": "noul", "noul": FAKE_KEY}
        return 200, body
    lines = []
    with caplog.at_level(logging.DEBUG):
        r = make_job(env, Transport(echo), lines=lines).run(items(2))
    assert r.stopped and "answers.escalate.noul" in r.stopped
    stored = [p.read_text() for p in (tmp / "decide").rglob("*") if p.is_file()]
    for text in [r.stopped, *lines, caplog.text, *stored, json.dumps([o.record() for o in r.outcomes])]:
        assert FAKE_KEY not in text
    assert any(dc.REDACTED in text for text in stored)            # the body is kept, with the key scrubbed


def test_f005_a_key_echoed_in_an_error_code_never_reaches_the_log(env, caplog):
    t = Transport(ok(), (400, {"error": {"code": FAKE_KEY, "message": FAKE_KEY}}), ok())
    with caplog.at_level(logging.DEBUG):
        r = make_job(env, t).run(items(3))
    assert r.outcomes[1].reason == "http_400" and FAKE_KEY not in caplog.text


def test_f005_contract_messages_name_fields_never_values():
    q = render.x2_questions()
    for bad in ({"type": "noul", "noul": "sk-or-v1-secretvalue"}, {"type": "sk-or-v1-secretvalue"}):
        body = answer_for(q)
        body["answers"]["escalate"] = bad
        with pytest.raises(dc.ContractError) as e:
            dc.parse_response(body, q, "nace-ai/drex-max")
        assert "secretvalue" not in str(e.value) and "answers.escalate" in str(e.value)
    with pytest.raises(dc.ContractError) as e:
        dc.parse_response({**answer_for(q), "model": "sk-or-v1-secretvalue"}, q, "nace-ai/drex-max")
    assert "secretvalue" not in str(e.value)


# --- F006: a stored answer is found without a pin and never sent again ----------------------------------------------

def test_f006_a_malformed_first_answer_is_not_sent_again_on_the_next_run(env):
    tmp, ledger = env
    bad = lambda q: (200, {**answer_for(q), "answers": {}})       # noqa: E731
    first = make_job(env, Transport(bad)).run(items(1))
    assert first.stopped and dc.read_state(tmp / "decide", "X2")["resolved_model"] is None
    t = Transport()
    again = make_job(env, t).run(items(1))
    assert t.requests == [] and again.outcomes[0].status == "fallback"
    assert again.outcomes[0].reason.startswith("stored answer off the contract") and not again.stopped


def test_f006_a_malformed_stored_answer_under_a_pin_falls_back_without_ending_the_run(env):
    tmp, ledger = env
    make_job(env, Transport()).run(items(2))
    path = dc.cache_path(tmp / "decide", RESOLVED, items(2)[0].request_hash)
    path.write_text(json.dumps({"model": RESOLVED, "answers": {}, "usage": {}}))
    t = Transport()
    r = make_job(env, t).run(items(2))
    assert t.requests == [] and [o.status for o in r.outcomes] == ["fallback", "cached"]


def test_f006_a_crash_between_the_receipt_and_the_body_never_sends_the_request_again(env):
    tmp, ledger = env
    make_job(env, Transport()).run(items(1))
    (tmp / "decide" / "state-X2.json").unlink()                     # the pin is lost too
    dc.cache_path(tmp / "decide", RESOLVED, items(1)[0].request_hash).unlink()
    t = Transport()
    r = make_job(env, t).run(items(1))
    assert t.requests == [] and "missing" in r.outcomes[0].reason


def test_f006_an_answer_stored_before_its_pin_was_saved_is_found_and_pinned(env):
    tmp, ledger = env
    make_job(env, Transport()).run(items(2))
    (tmp / "decide" / "state-X2.json").unlink()                     # a crash after the body, before the pin
    t = Transport()
    r = make_job(env, t).run(items(2))
    assert t.requests == [] and [o.status for o in r.outcomes] == ["cached", "cached"]
    assert dc.read_state(tmp / "decide", "X2")["resolved_model"] == RESOLVED


# --- F007: every wrongly typed field is a contract failure -----------------------------------------------------------

@pytest.mark.parametrize("mutate", [
    lambda b: {**b, "usage": ["wrong shape"]},
    lambda b: {**b, "usage": "wrong shape"},
    lambda b: {**b, "answers": {**b["answers"], "place": {**b["answers"]["place"], "choice": ["still_at_unit"]}}},
    lambda b: {**b, "answers": {**b["answers"], "place": {**b["answers"]["place"], "choice": {"x": 1}}}},
])
def test_f007_a_wrongly_typed_field_ends_in_a_contract_stop_not_a_crash(env, mutate):
    tmp, ledger = env
    r = make_job(env, Transport(lambda q: (200, mutate(answer_for(q))))).run(items(2))
    assert r.stopped and [o.status for o in r.outcomes] == ["fallback", "fallback"]
    assert kinds(ledger)[:2] == ["reserve", "settle"]                # billed, never left open


def test_f007_usage_that_is_not_an_object_is_a_contract_error():
    with pytest.raises(dc.ContractError, match="usage is not an object"):
        dc.usage_micros(["wrong shape"], config(), "nace-ai/drex-max")


# --- The billing record the X1 pilot reads ----------------------------------------------------------------------------

def test_every_call_records_its_request_bytes_its_bound_and_its_billed_input_tokens(env):
    tmp, ledger = env
    r = make_job(env, Transport()).run(items(2))
    body = dc.build_body("nace-ai/drex-max", items(1)[0].state, items(1)[0].questions)
    o = r.outcomes[0]
    assert o.billed_input_tokens == 900 and o.request_bytes == len(dc.canonical(body).encode())
    assert o.input_bound == dc.input_bound(body, config()) and o.reserved_micros == dc.reservation_micros(body, config())
    settle = [e for e in ledger.entries("X2") if e["kind"] == "settle"][0]
    assert settle["detail"]["billed_input_tokens"] == 900 and settle["detail"]["request_bytes"] == o.request_bytes
    b = dc.billing_summary(r.outcomes)
    assert b["calls_with_usage"] == 2 and b["billed_input_tokens"] == 1800
    assert 0 < b["max_cost_over_reservation"] < 1 and b["max_billed_over_bound"] == 900 / o.input_bound


# --- F009 (second round): a spend is bound to the configuration and wording its dry run froze -------------------------

def _spend(tmp, cfg, transport, job="X2", set_name="val"):
    """spend_step with a mock transport; each row keeps its request hash, as every job's rows do."""
    return dc.spend_step(job, set_name, cfg, "1", tmp / "ledger.sqlite",
                         lambda outcomes, its, result: [{"item_id": o.item_id, "request_hash": o.request_hash,
                                                         "answers": o.answers} for o in outcomes],
                         client_factory=lambda: dc.DecisionClient(cfg, env_path=tmp / ".env",
                                                                  transport=httpx2.MockTransport(transport)),
                         decide_dir=tmp / "decide", results_dir=tmp / "results", env_path=tmp / ".env",
                         sleep=lambda s: None, concurrency=1, now=lambda: NOW, out=lambda s: None)


def test_f009_a_spend_after_the_wording_and_prompt_version_change_needs_a_new_dry_run(env, monkeypatch):
    """Sol's sequence: freeze and send p1, revise a question's wording and set p2, spend again with no new dry
    run. The second spend is refused before any cache lookup or request, and nothing is relabeled."""
    tmp, ledger = env
    p1 = config()
    dc.dry_run_step("X2", "val", items(2), p1, tmp / "ledger.sqlite", decide_dir=tmp / "decide", now=NOW,
                    out=lambda s: None)
    t = Transport()
    first = _spend(tmp, p1, t)
    assert len(t.requests) == 2 and first["provenance"]["config_hash"] == dc.config_hash(p1)
    summary_before = (tmp / "results" / "X2-val.json").read_bytes()
    outputs_before = (tmp / "decide" / "outputs" / "X2-val.jsonl").read_bytes()
    real = render.x2_questions

    def reworded():
        q = real()
        q["escalate"]["instructions"] = "Should a supervisor be called right away?"
        return q
    monkeypatch.setattr(render, "x2_questions", reworded)
    p2 = config()
    p2["decisions"]["prompt_version"] += "-revised"
    with pytest.raises(dc.JobError, match="run the dry run again"):
        _spend(tmp, p2, t)
    assert len(t.requests) == 2                                     # no request, no cache answer relabeled
    assert (tmp / "results" / "X2-val.json").read_bytes() == summary_before
    assert (tmp / "decide" / "outputs" / "X2-val.jsonl").read_bytes() == outputs_before
    with pytest.raises(dc.ProvenanceError):
        dc.verified_outputs("X2", "val", p2, decide_dir=tmp / "decide", results_dir=tmp / "results")


def test_f009_a_changed_wording_alone_is_refused_at_the_spend_and_at_scoring(env, monkeypatch):
    """X1's wording is in neither config_hash nor lock_hash: only the question binding catches it."""
    from src.decide import critic
    from test_decide import _record

    tmp, ledger = env
    cfg = config()
    cfg["samples"] = {**cfg["samples"], "X1_controls_each": 1}
    its = critic.build_items([_record(i) for i in range(3)], cfg)
    dc.dry_run_step("X1", "library", its, cfg, tmp / "ledger.sqlite", decide_dir=tmp / "decide", now=NOW,
                    out=lambda s: None)
    _spend(tmp, cfg, Transport(), job="X1", set_name="library")
    dc.verified_outputs("X1", "library", cfg, decide_dir=tmp / "decide", results_dir=tmp / "results")
    monkeypatch.setattr(render, "PLAUSIBILITY_LEVELS", ["Implausible", "Odd", "Fair", "Plausible", "Very plausible"])
    with pytest.raises(dc.ProvenanceError, match="question wording"):
        dc.verified_outputs("X1", "library", cfg, decide_dir=tmp / "decide", results_dir=tmp / "results")
    with pytest.raises(dc.JobError, match="questions_hash"):
        _spend(tmp, cfg, Transport(), job="X1", set_name="library")


def test_f009_the_frozen_items_are_bound_to_the_bytes_the_dry_run_froze(env):
    tmp, ledger = env
    cfg = config()
    dc.dry_run_step("X2", "val", items(2), cfg, tmp / "ledger.sqlite", decide_dir=tmp / "decide", now=NOW,
                    out=lambda s: None)
    path = dc.items_path(tmp / "decide", "X2", "val")
    frozen = dc.load_items(path)
    frozen[0].meta = {"edited": True}                              # same requests, other bytes
    dc.save_items(path, frozen)
    with pytest.raises(dc.JobError, match="not the bytes the dry run froze"):
        _spend(tmp, cfg, Transport())
