"""The decision layer's client, ledger use, cache, contract, rules and rendering (docs/neural/spec.md §11, §15.2).

Request shape; a cache hit means no call; caps and the 402 stop; retries and the rules after them; refusal fallback
per question; rule R2's cases; a resolved-version change stops a job; the dry run opens no connection; and no key
reaches a log, an exception or a file. A mock transport stands in for the network; nothing leaves the machine."""

import copy
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import httpx2
import pytest

from src.decide import client as dc
from src.decide import critic, dispatcher, distill, render, rules
from src.scenarios import ledger as L

ROOT = Path(__file__).resolve().parents[1]
# Assembled at run time so no key-shaped literal sits in the repository.
FAKE_KEY = "sk-or-v1-" + "tracerFAKEkey" + "0123456789" * 4
RESOLVED = "nace-ai/drex-max-20261016"
NOW = datetime.now().astimezone()
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "decisions_example.json").read_text())


def config(**over) -> dict:
    cfg = copy.deepcopy(dc.load_config())
    cfg["price"]["verified_at"] = NOW.isoformat(timespec="seconds")
    cfg.update(over)
    return cfg


def answer_for(questions: dict, *, model: str = RESOLVED, cost=0.00005, refuse=(), choose: dict | None = None,
               noul: float = 0.8) -> dict:
    """A valid response to `questions`: noul at `noul`, a choice of the first option (or `choose`), level 3."""
    out = {}
    for name, q in questions.items():
        if name in refuse:
            out[name] = {"type": "refusal"}
        elif q["type"] == "noul":
            out[name] = {"type": "noul", "noul": noul}
        elif q["type"] == "choice":
            opts = list(q["criteria"])
            pick = (choose or {}).get(name, opts[0])
            out[name] = {"type": "choice", "choice": pick, "confidence": 0.5,
                         "probabilities": {o: (0.6 if o == pick else 0.4 / (len(opts) - 1)) for o in opts}}
        else:
            n = len(q["criteria"])
            out[name] = {"type": "score", "score": 3.0, "confidence": 0.9, "legend": {},
                         "probabilities": {str(i): float(i == 3) for i in range(n)}}
    usage = {"input_tokens": 900, "output_tokens": 40}
    if cost is not None:
        usage["cost"] = cost
    return {"id": "gen-dec-1", "model": model, "provider": "DeepInfra", "answers": out, "usage": usage}


class Transport:
    """Answers each request from a queue (the last answer repeats): (status, body) or a function of the request's
    questions, or an exception class. Records every request."""

    def __init__(self, *answers):
        self.answers = list(answers) or [lambda q: (200, answer_for(q))]
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, type) and issubclass(a, Exception):
            raise a("simulated", request=request)
        if callable(a):
            a = a(json.loads(request.content)["questions"])
        status, body = a
        return httpx2.Response(status, content=body if isinstance(body, (str, bytes)) else json.dumps(body))


def ok(**kw):
    return lambda q: (200, answer_for(q, **kw))


@pytest.fixture
def env(tmp_path):
    (tmp_path / ".env").write_text(f"OPENROUTER_API_KEY={FAKE_KEY}\n")
    ledger = L.Ledger(tmp_path / "ledger.sqlite", jobs=L.load_jobs(), create=True)
    yield tmp_path, ledger
    ledger.close()


def make_job(env, transport, cfg=None, *, job="X2", budget="1.00", sleeps=None, lines=None):
    tmp, ledger = env
    cfg = cfg or config()
    client = dc.DecisionClient(cfg, env_path=tmp / ".env", transport=httpx2.MockTransport(transport))
    return dc.Job(job, cfg, ledger, client_factory=lambda: client, budget_usd=budget, decide_dir=tmp / "decide",
                  sleep=(sleeps.append if sleeps is not None else (lambda s: None)), concurrency=1,
                  out=(lines.append if lines is not None else (lambda s: None)))


def items(n: int = 3, questions=None):
    return [dc.Item(f"a{i}", f"A state, number {i}.", questions or render.x2_questions()) for i in range(n)]


def kinds(ledger, job="X2"):
    return [e["kind"] for e in ledger.entries(job)]


# --- request shape and the key ------------------------------------------------------------------------------------

def test_the_request_has_the_documented_shape_and_the_key_comes_from_env(env):
    t = Transport()
    r = make_job(env, t).run(items(1))
    assert r.outcomes[0].status == "answered"
    req = t.requests[0]
    cfg = config()
    assert req.method == "POST" and str(req.url) == cfg["base_url"] + cfg["path"]
    assert req.headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert req.headers["content-type"] == "application/json"
    sent = json.loads(req.content)
    assert set(sent) == {"model", "state", "questions"} and sent["model"] == "nace-ai/drex-max"
    q = sent["questions"]
    assert {k: v["type"] for k, v in q.items()} == {"place": "choice", "action": "choice", "shared_problem": "noul",
                                                    "escalate": "noul"}
    assert "other" in q["place"]["criteria"] and "other" in q["action"]["criteria"]
    assert set(q["shared_problem"]["criteria"]) == {"true", "false"}
    assert "temperature" not in sent


def test_every_question_type_the_jobs_send_has_the_documented_criteria():
    x1 = render.x1_questions(["unnoticed_outage"], render.edge_sentences())
    assert x1["plausibility"]["type"] == "score" and len(x1["plausibility"]["criteria"]) == 5
    assert isinstance(x1["plausibility"]["criteria"], list)
    assert set(x1["dominant_failure"]["criteria"]) == set(render.failure_foci()) | {"other"}
    assert len(render.failure_foci()) == 11
    assert x1["edge_unnoticed_outage"] == {"type": "noul", "instructions": "Does this week include the following: a "
                                           "tube line outage has noticed_after_min of 60 or more?"}
    assert render.x3_questions(75)["miss"]["instructions"] == ("Will this specimen still be unreceived when its "
                                                               "stability window closes in 75 minutes?")


def test_a_choice_without_other_is_refused_before_anything_is_sent():
    with pytest.raises(ValueError, match="other"):
        dc.build_body("m", "s", {"q": {"type": "choice", "criteria": {"a": "A"}}})
    with pytest.raises(ValueError, match="box"):
        dc.build_body("m", "s", {"q": {"type": "box", "instructions": "where"}})


def test_the_key_is_read_at_call_time_and_a_missing_key_voids_the_reservation(env):
    tmp, ledger = env
    (tmp / ".env").write_text("")
    t = Transport()
    r = make_job(env, t).run(items(1))
    assert t.requests == [] and r.stopped and "missing_key" in r.stopped
    assert kinds(ledger) == ["reserve", "void"]
    assert dc.key_present(config(), tmp / ".env") is False


# --- the contract --------------------------------------------------------------------------------------------------

def test_the_recorded_fixture_meets_the_pinned_contract():
    f = FIXTURE
    parsed = dc.parse_response(f["response"], f["request"]["questions"], "typesafe/jev-1.13")
    assert parsed["model"] == "typesafe/jev-1.13-20260917" and parsed["refusals"] == []
    a = parsed["answers"]
    assert a["is_bug"] == {"type": "noul", "p": 0.96}
    assert a["team"]["choice"] == "payments" and a["team"]["probabilities"]["payments"] == 0.84
    assert a["urgency"]["score"] == 1.99 and a["urgency"]["probabilities"]["2"] == 0.99
    assert dc.usage_micros(parsed["usage"], config(), "nace-ai/drex-max") == (20, "usage.cost")


def _mutate(path, value):
    body = copy.deepcopy(FIXTURE["response"])
    node = body
    for k in path[:-1]:
        node = node[k]
    if value is KeyError:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return body


@pytest.mark.parametrize("path,value", [
    (("answers", "is_bug"), KeyError),                                   # a question unanswered
    (("answers", "is_bug", "noul"), 1.4),                                # not a probability
    (("answers", "team", "choice"), "billing"),                          # an option the question does not offer
    (("answers", "team", "probabilities", "frontend"), 0.6),             # probabilities summing to 1.44
    (("answers", "urgency", "type"), "choice"),                          # the wrong type
    (("answers", "urgency", "score"), 7),                                # past the last level
    (("usage",), KeyError),                                              # no usage
    (("model",), "other-vendor/model-1"),                                # not the expected model
])
def test_a_response_off_the_contract_is_refused(path, value):
    with pytest.raises(dc.ContractError):
        dc.parse_response(_mutate(path, value), FIXTURE["request"]["questions"], "typesafe/jev-1.13")


def test_a_refusal_is_an_answer_of_its_own_kind():
    body = _mutate(("answers", "team"), {"type": "refusal"})
    parsed = dc.parse_response(body, FIXTURE["request"]["questions"], "typesafe/jev-1.13")
    assert parsed["refusals"] == ["team"] and parsed["answers"]["team"] == {"type": "refusal"}


def test_a_response_off_the_contract_stops_the_job_and_its_items_fall_back(env):
    tmp, ledger = env
    bad = lambda q: (200, {**answer_for(q), "answers": {"place": {"type": "noul", "noul": 0.2}}})  # noqa: E731
    t = Transport(bad)
    r = make_job(env, t).run(items(3))
    assert r.stopped and "contract" in r.stopped
    assert [o.status for o in r.outcomes] == ["fallback"] * 3 and len(t.requests) == 1
    assert kinds(ledger) == ["reserve", "settle", "release"]          # billed, then stopped


# --- the cache -----------------------------------------------------------------------------------------------------

def test_an_answer_in_the_cache_means_no_call_and_no_reservation(env):
    tmp, ledger = env
    first = make_job(env, Transport()).run(items(3))
    assert [o.status for o in first.outcomes] == ["answered"] * 3
    path = dc.cache_path(tmp / "decide", RESOLVED, items(1)[0].request_hash)
    assert path.exists() and json.loads(path.read_text())["model"] == RESOLVED
    before = len(ledger.entries())
    refuse = Transport(lambda q: pytest.fail("a cached request was sent again"))
    again = make_job(env, refuse).run(items(3))
    assert [o.status for o in again.outcomes] == ["cached"] * 3 and refuse.requests == []
    assert len(ledger.entries()) == before and again.sent == 0
    assert [o.answers for o in again.outcomes] == [o.answers for o in first.outcomes]


def test_the_cache_key_is_the_state_and_the_questions():
    a, b = items(2)
    assert a.request_hash != b.request_hash
    assert a.request_hash == dc.request_hash(a.state, a.questions)
    assert dc.request_hash("s", render.x2_questions()) != dc.request_hash("s", render.x3_questions(10))


# --- money ---------------------------------------------------------------------------------------------------------

def test_every_call_is_reserved_first_and_settled_from_its_reported_cost(env):
    tmp, ledger = env
    make_job(env, Transport()).run(items(2))
    entries = ledger.entries("X2")
    assert [e["kind"] for e in entries] == ["reserve", "settle", "release"] * 2
    for res, settle in zip(entries[0::3], entries[1::3]):
        assert settle["ref"] == res["entry_id"] and settle["micros"] == 50 and settle["micros"] <= res["micros"]
        assert settle["detail"]["cost_source"] == "usage.cost" and res["detail"]["model"] == "nace-ai/drex-max"
    mirror = [json.loads(line) for line in (tmp / "ledger.jsonl").read_text().splitlines()]
    reserved = {e["entry_id"] for e in mirror if e.get("kind") == "reserve"}
    assert reserved == {e["entry_id"] for e in entries if e["kind"] == "reserve"}   # every reservation logged
    assert L.replay(tmp / "ledger.jsonl") == ledger.balances()


def test_the_reservation_is_the_worst_case_at_the_standard_price():
    cfg = config()
    body = dc.build_body("nace-ai/drex-max", "A state.", render.x2_questions())
    bound, est = dc.input_bound(body, cfg), dc.estimate_tokens(body, cfg)
    assert bound >= len(dc.canonical(body).encode()) and bound >= est * 1.5
    assert dc.reservation_micros(body, cfg) == -(-bound * 12 // 100)          # $0.12 per million, rounded up
    assert dc.expected_micros(body, cfg) == -(-est * 6 // 100)                # the listed $0.06


def test_without_a_reported_cost_the_tokens_settle_at_the_standard_price(env):
    tmp, ledger = env
    make_job(env, Transport(ok(cost=None))).run(items(1))
    settle = [e for e in ledger.entries("X2") if e["kind"] == "settle"][0]
    assert settle["micros"] == 108 and settle["detail"]["cost_source"] == "tokens at the standard price"   # 900 x 0.12


def test_the_ledger_refuses_a_call_past_the_run_budget_and_nothing_is_sent(env):
    tmp, ledger = env
    t = Transport()
    r = make_job(env, t, budget="0.000001").run(items(3))
    assert t.requests == [] and r.stopped and "budget" in r.stopped
    assert [o.status for o in r.outcomes] == ["fallback"] * 3 and ledger.entries("X2") == []


def test_the_ledger_refuses_a_call_past_the_job_cap(env):
    tmp, ledger = env
    ledger.adjust("X3", "0.75", "the job's cap already spent (a test)")
    t = Transport()
    r = make_job(env, t, job="X3", budget="0.75").run(items(2, render.x3_questions(30)))
    assert t.requests == [] and "job" in r.stopped and L.as_usd(ledger.totals()["jobs"]["X3"]["spent"]) == 0.75


def test_the_caps_here_must_be_the_ledgers():
    dc.check_caps(config())
    bad = config()
    bad["caps"]["jobs"]["X2"] = 9.0
    with pytest.raises(dc.JobError, match="X2"):
        dc.check_caps(bad)


def test_a_402_stops_the_job_and_sends_nothing_more(env):
    tmp, ledger = env
    t = Transport(ok(), (402, {"error": {"code": 402, "message": "Insufficient credits"}}), ok())
    r = make_job(env, t).run(items(3))
    assert "402" in r.stopped and len(t.requests) == 2
    assert [o.status for o in r.outcomes] == ["answered", "fallback", "fallback"]
    settles = [e for e in ledger.entries("X2") if e["kind"] == "settle"]
    assert settles[-1]["detail"]["unknown"] is True                    # not proven unbilled: kept as spent


@pytest.mark.parametrize("status", [429, 503, 529])
def test_429_503_and_529_back_off_with_jitter_and_retry(env, status):
    tmp, ledger = env
    sleeps = []
    t = Transport(ok(), (status, {"error": {"code": status}}), (status, {"error": {"code": status}}), ok())
    r = make_job(env, t, sleeps=sleeps).run(items(2))
    assert [o.status for o in r.outcomes] == ["answered", "answered"] and r.outcomes[1].attempts == 3
    assert len(sleeps) == 2 and 1.5 <= sleeps[0] <= 2.5 and 3.0 <= sleeps[1] <= 5.0      # 2 s, 4 s, x 0.75 to 1.25
    closing = [e["kind"] for e in ledger.entries("X2") if e["kind"] in ("void", "settle")]
    assert closing == ["settle"] + (["void", "void"] if status == 429 else ["settle", "settle"]) + ["settle"]


def test_after_six_tries_the_item_falls_back_to_the_rules_and_the_job_goes_on(env):
    t = Transport(ok(), *[(503, {})] * 6, ok())
    r = make_job(env, t).run(items(3))
    assert [o.status for o in r.outcomes] == ["answered", "fallback", "answered"]
    assert r.outcomes[1].reason == "http_503 after 6 tries" and r.outcomes[1].attempts == 6 and not r.stopped


def test_a_400_falls_back_for_its_item_and_is_never_billed(env, caplog):
    tmp, ledger = env
    t = Transport(ok(), (400, {"error": {"code": 400, "message": f"bad request {FAKE_KEY}"}}), ok())
    with caplog.at_level(logging.WARNING):
        r = make_job(env, t).run(items(3))
    assert [o.status for o in r.outcomes] == ["answered", "fallback", "answered"] and not r.stopped
    assert r.outcomes[1].reason == "http_400" and "falls back to the rules" in caplog.text
    assert FAKE_KEY not in caplog.text and "bad request" not in caplog.text
    assert "void" in kinds(ledger)


def test_unknown_outcomes_in_half_of_the_last_ten_attempts_stop_the_job(env):
    t = Transport(ok(), httpx2.ReadTimeout)
    r = make_job(env, t).run(items(9))
    assert r.stopped and "unknown outcome" in r.stopped
    assert sum(o.status == "fallback" for o in r.outcomes) == 8 and len(t.requests) == 6


# --- versions and the fallback model ---------------------------------------------------------------------------------

def test_a_new_resolved_version_stops_the_job_and_both_versions_are_recorded(env):
    tmp, ledger = env
    t = Transport(ok(), ok(model="nace-ai/drex-max-20261101"), ok())
    r = make_job(env, t).run(items(3))
    assert r.stopped and "changed" in r.stopped and len(t.requests) == 2
    assert [o.status for o in r.outcomes] == ["answered", "fallback", "fallback"]
    state = dc.read_state(tmp / "decide", "X2")
    assert [v["resolved_model"] for v in state["versions"]] == [RESOLVED, "nace-ai/drex-max-20261101"]
    assert state["resolved_model"] == "nace-ai/drex-max-20261101"
    assert any(e["kind"] == "version_changed" for e in state["events"])
    rerun = make_job(env, Transport(ok(model="nace-ai/drex-max-20261101"))).run(items(3))   # from scratch on the new one
    assert [o.status for o in rerun.outcomes] == ["answered", "cached", "answered"]   # the old version never mixes in


def test_drex_max_silent_at_the_start_hands_the_job_to_the_fallback_model_and_records_it(env):
    tmp, ledger = env
    jev = "typesafe/jev-1.14-20261014"

    def answer(request_questions):
        return (200, answer_for(request_questions, model=jev))
    seen = []

    def route(request):
        body = json.loads(request.content)
        seen.append(body["model"])
        if body["model"] == "nace-ai/drex-max":
            return httpx2.Response(503, content="{}")
        return httpx2.Response(200, content=json.dumps(answer(body["questions"])[1]))
    cfg = config()
    client = dc.DecisionClient(cfg, env_path=tmp / ".env", transport=httpx2.MockTransport(route))
    lines = []
    r = dc.Job("X2", cfg, ledger, client_factory=lambda: client, budget_usd="1", decide_dir=tmp / "decide",
               sleep=lambda s: None, concurrency=1, out=lines.append).run(items(2))
    assert seen == ["nace-ai/drex-max"] * 6 + ["typesafe/jev-1.14"] * 2
    assert [o.status for o in r.outcomes] == ["answered", "answered"] and r.model == "typesafe/jev-1.14"
    state = dc.read_state(tmp / "decide", "X2")
    assert state["model"] == "typesafe/jev-1.14" and state["resolved_model"] == jev
    assert any(e["kind"] == "fallback_model" for e in state["events"]) and "fallback" in lines[0]
    row = dispatcher.decide(r.outcomes[0], _x2_item(), cfg, r.model)
    assert row["source"] == "jev-fallback"


# --- dry run and the spending checks ----------------------------------------------------------------------------------

def test_the_dry_run_prices_every_request_without_a_connection_or_a_reservation(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx2.Client, "__init__", lambda *a, **k: pytest.fail("the dry run opened a client"))
    lines = []
    d = dc.dry_run_step("X2", "val", items(4), config(), tmp_path / "ledger.sqlite", decide_dir=tmp_path / "decide",
                        now=NOW, out=lines.append)
    with L.Ledger(tmp_path / "ledger.sqlite") as lg:                       # created empty, as the OpenAI dry run does
        assert lg.entries() == []
    assert d["requests"] == d["to_send"] == 4 and d["cached"] == 0 and 0 < d["expected_micros"] < d["worst_micros"]
    assert d["request_set_hash"] == dc.request_set_hash("X2", "val", items(4))
    assert dc.load_items(dc.items_path(tmp_path / "decide", "X2", "val"))[2].state == items(4)[2].state
    assert "nothing sent, nothing spent, no connection" in lines[0]


def test_spending_needs_the_key_a_fresh_price_and_a_fresh_dry_run_of_the_same_items(env):
    tmp, ledger = env
    d = tmp / "decide"
    cfg = config()
    with pytest.raises(dc.JobError, match="above X2's"):
        dc.spend_checks("X2", "val", cfg, "5", decide_dir=d, env_path=tmp / ".env", now=NOW)
    with pytest.raises(dc.JobError, match="frozen items"):
        dc.spend_checks("X2", "val", cfg, "1", decide_dir=d, env_path=tmp / ".env", now=NOW)
    dc.dry_run_step("X2", "val", items(2), cfg, tmp / "ledger.sqlite", decide_dir=d, now=NOW, out=lambda s: None)
    got, budget = dc.spend_checks("X2", "val", cfg, "1", decide_dir=d, env_path=tmp / ".env", now=NOW)
    assert len(got) == 2 and str(budget) == "1"
    later = NOW + timedelta(hours=25)
    rechecked = config()
    rechecked["price"]["verified_at"] = later.isoformat(timespec="seconds")
    with pytest.raises(dc.JobError, match="stale"):
        dc.spend_checks("X2", "val", rechecked, "1", decide_dir=d, env_path=tmp / ".env", now=later)
    stale = config()
    stale["price"]["verified_at"] = (NOW - timedelta(hours=30)).isoformat()
    with pytest.raises(dc.JobError, match="price"):
        dc.spend_checks("X2", "val", stale, "1", decide_dir=d, env_path=tmp / ".env", now=NOW)
    never = config()
    never["price"]["verified_at"] = None
    with pytest.raises(dc.JobError, match="never been verified"):
        dc.spend_checks("X2", "val", never, "1", decide_dir=d, env_path=tmp / ".env", now=NOW)
    dc.save_items(dc.items_path(d, "X2", "val"), items(3))                    # the items changed since the dry run
    with pytest.raises(dc.JobError, match="changed since the dry run"):
        dc.spend_checks("X2", "val", cfg, "1", decide_dir=d, env_path=tmp / ".env", now=NOW)
    (tmp / ".env").write_text("")
    with pytest.raises(dc.JobError, match="no OPENROUTER_API_KEY"):
        dc.spend_checks("X2", "val", cfg, "1", decide_dir=d, env_path=tmp / ".env", now=NOW)


def test_the_committed_configuration_refuses_to_spend_until_the_price_is_checked():
    cfg = dc.load_config()
    assert cfg["price"]["verified_at"] is None
    with pytest.raises(dc.JobError):
        dc.check_price(cfg)
    assert cfg["base_url"].startswith("https://") and cfg["key_name"] == "OPENROUTER_API_KEY"
    assert cfg["counterfactual"] == {"found_after_alert_min": 10, "received_after_found_min": 5, "source": "assumed"}
    assert cfg["decisions"]["noul_yes"] == 0.7 and cfg["operations"]["retry_statuses"] == [429, 503, 529]


def test_no_key_reaches_a_log_an_exception_a_cache_file_an_output_or_the_ledger(env, caplog):
    tmp, ledger = env
    t = Transport(ok(), (400, {"error": {"message": FAKE_KEY}}), httpx2.ConnectError, (500, FAKE_KEY), ok())
    with caplog.at_level(logging.DEBUG):
        r = make_job(env, t).run(items(5))
    rows = dispatcher.decisions(r.outcomes, [_x2_item(i.item_id) for i in items(5)], r, config())
    texts = [caplog.text, json.dumps(rows), json.dumps([o.record() for o in r.outcomes]), str(r.stopped)]
    texts += [p.read_text() for p in tmp.rglob("*") if p.is_file() and p.suffix in (".json", ".jsonl")]
    for text in texts:
        assert FAKE_KEY not in text and "bearer" not in text.lower()
    err = dc.DecisionError("exception", code=f"x {FAKE_KEY}")
    assert FAKE_KEY not in str(err) and err.code == "redacted"


# --- rules R1 and R2 -------------------------------------------------------------------------------------------------

def facts(**over) -> dict:
    """A tube specimen silent 30 min on a route that usually takes 20 (90th percentile 40), nothing unusual."""
    f = {"specimen": {"type": "urine", "tests": ["culture"], "tier": 3, "priority": "routine", "unit_kind": "ed",
                      "route_kind": "tube", "route": "tube"},
         "silence_min": 30.0, "window_min": 120, "left_min": 90.0,
         "route_history": {"scope": "route", "n": 500, "p50": 20.0, "p90": 40.0, "share_faster": 0.7,
                           "never_share": 0.004},
         "mates": {"n": 2, "received": 0}, "route_last_receipt_min": 5.0, "route_usual_gap_min": 10.0,
         "route_overdue": 0, "unit_outstanding": 1, "lab_receipts_60": 20, "lab_usual_receipts_60": 18.0,
         "shift": "day", "day_type": "weekday", "to_shift_change_min": 120.0, "staff_this_shift": 3,
         "staff_range": [1, 4], "courier_interval_min": None}
    for k, v in over.items():
        if k in ("route_kind", "route"):
            f["specimen"][k] = v
        else:
            f[k] = v
    return f


def test_r1_routes_by_route_alone():
    assert rules.r1(facts()) == {"rule": "route", "place": "at_lab_tube_station", "action": "check_lab_tube_station",
                                 "role": "receiving technologist", "shared_problem": None, "escalate": None}
    hand = rules.r1(facts(route_kind="courier", route="hand_carry"))
    assert (hand["place"], hand["action"], hand["role"]) == ("with_courier", "contact_courier_coordinator",
                                                             "courier coordinator")


def test_r2_very_long_looks_lost():
    r = rules.r2(facts(silence_min=200.0))
    assert (r["rule"], r["place"], r["action"], r["role"], r["escalate"]) == (
        "very_long", "lost", "request_recollection", "charge nurse", True)


def test_r2_route_quiet_points_at_the_tube_system_or_the_courier():
    tube = rules.r2(facts(route_last_receipt_min=25.0))
    assert (tube["rule"], tube["place"], tube["action"], tube["shared_problem"], tube["escalate"]) == (
        "route_quiet", "in_tube_system", "escalate_tube_system", True, True)
    courier = rules.r2(facts(route_last_receipt_min=25.0, route_kind="courier", route="hand_carry"))
    assert (courier["place"], courier["action"], courier["escalate"]) == ("with_courier", "contact_courier_coordinator",
                                                                          False)


def test_r2_receiving_behind_points_at_the_receiving_queue():
    r = rules.r2(facts(silence_min=50.0, lab_receipts_60=5))
    assert (r["rule"], r["place"], r["action"], r["shared_problem"]) == (
        "receiving_behind", "in_lab_awaiting_scan", "check_receiving_queue", True)


def test_r2_a_batch_mate_arrived_so_this_one_waits_at_the_station_or_with_the_courier():
    r = rules.r2(facts(mates={"n": 2, "received": 1}))
    assert (r["rule"], r["place"], r["action"]) == ("mates_arrived", "at_lab_tube_station", "check_lab_tube_station")
    c = rules.r2(facts(mates={"n": 2, "received": 1}, route_kind="courier", route="hand_carry"))
    assert (c["place"], c["action"]) == ("with_courier", "contact_courier_coordinator")


def test_r2_early_silence_with_no_batch_mate_arrived_is_still_at_the_unit():
    r = rules.r2(facts(silence_min=15.0))
    assert (r["rule"], r["place"], r["action"], r["role"]) == ("early_silence", "still_at_unit",
                                                               "call_unit_confirm_sent", "charge nurse")


@pytest.mark.parametrize("kind,place,action", [("tube", "at_lab_tube_station", "check_lab_tube_station"),
                                               ("courier", "waiting_for_courier", "contact_courier_coordinator"),
                                               ("walk_in", "still_at_unit", "call_unit_confirm_sent")])
def test_r2_otherwise_goes_by_route(kind, place, action):
    r = rules.r2(facts(route_kind=kind, route="tube" if kind == "tube" else "hand_carry"))
    assert (r["rule"], r["place"], r["action"]) == ("by_route", place, action)


def test_r2_shared_problem_and_escalate_follow_their_definitions():
    assert rules.r2(facts(route_overdue=2))["shared_problem"] is True
    assert rules.r2(facts(route_overdue=1))["shared_problem"] is False
    assert rules.r2(facts(), p_never=0.6)["escalate"] is True and rules.r2(facts(), p_never=0.3)["escalate"] is False
    assert {rules.role(a) for a in rules.ACTIONS} == {"receiving technologist", "courier coordinator", "charge nurse"}


# --- X2's decisions --------------------------------------------------------------------------------------------------

def _x2_item(item_id="a0", f=None):
    f = f or facts()
    it = dc.Item(item_id, "A state.", render.x2_questions())
    it.meta = {"alert_id": item_id, "specimen_id": "SP-r100071-000001", "run": 100071, "scenario_id": "S1",
               "tick_min": 30, "window_min": 120, "route_kind": "tube", "p_miss": 0.6, "p_never": 0.1,
               "state_hash": "h", "r1": rules.r1(f), "r2": rules.r2(f, 0.1)}
    return it


def test_decisions_take_the_top_choice_and_yes_at_seven_tenths():
    cfg = config()
    o = dc.Outcome("a0", "h", "answered", answers=dc.parse_response(
        answer_for(render.x2_questions(), choose={"place": "in_tube_system", "action": "escalate_tube_system"},
                   noul=0.7), render.x2_questions(), "nace-ai/drex-max")["answers"], resolved_model=RESOLVED)
    d = dispatcher.decide(o, _x2_item(), cfg, cfg["model"])
    assert (d["source"], d["place"], d["action"], d["role"], d["place_p"]) == (
        "drex-max", "in_tube_system", "escalate_tube_system", "receiving technologist", 0.6)
    assert d["shared_problem"] is True and d["escalate"] is True and d["fallback"] == []
    low = dc.Outcome("a0", "h", "answered", answers=dc.parse_response(
        answer_for(render.x2_questions(), noul=0.69), render.x2_questions(), "nace-ai/drex-max")["answers"])
    assert dispatcher.decide(low, _x2_item(), cfg, cfg["model"])["escalate"] is False


def test_a_refused_question_falls_back_to_r2_for_that_question_alone():
    cfg = config()
    body = answer_for(render.x2_questions(), refuse=("place", "escalate"),
                      choose={"action": "check_receiving_queue"})
    o = dc.Outcome("a0", "h", "answered", answers=dc.parse_response(body, render.x2_questions(),
                                                                    "nace-ai/drex-max")["answers"])
    item = _x2_item()
    d = dispatcher.decide(o, item, cfg, cfg["model"])
    assert d["place"] == item.meta["r2"]["place"] and d["action"] == "check_receiving_queue"
    assert d["escalate"] == item.meta["r2"]["escalate"] and d["escalate_p"] is None
    assert d["shared_problem_p"] == 0.8 and d["fallback"] == ["place", "escalate"] and d["source"] == "drex-max"


def test_an_item_with_no_answer_takes_r2s_whole_decision():
    cfg = config()
    item = _x2_item()
    d = dispatcher.decide(dc.Outcome("a0", "h", "fallback", reason="http_400"), item, cfg, cfg["model"])
    assert d["source"] == "rules" and d["place"] == item.meta["r2"]["place"] and d["fallback_reason"] == "http_400"


# --- rendering -------------------------------------------------------------------------------------------------------

def test_an_alert_state_is_deterministic_and_states_numbers_with_their_comparisons():
    tn = {"p_miss": 0.62, "p_never": 0.2, "factors": ["Saturday 14:05, 50 min to shift change", "Silent 30 min"]}
    a, b = render.alert_state(facts(), tn, budget=1200), render.alert_state(copy.deepcopy(facts()), dict(tn))
    assert a == b and render.state_hash(a) == render.state_hash(b)
    assert "silent 30 min, 1.5 times the usual 20 min for this route here and longer than 70% of past arrivals" in a
    assert "chance it misses its window 62% (high); chance it never arrives 20% (moderate)" in a
    assert "(1) Saturday, 50 min to shift change" in a and "14:05" not in a
    assert "90 min left of a 120 min window" in a
    assert "TracerNet" not in render.alert_state(facts())


@pytest.mark.parametrize("p,word", [(0.0, "low"), (0.199, "low"), (0.2, "moderate"), (0.5, "moderate"),
                                    (0.501, "high"), (1.0, "high")])
def test_probability_bands(p, word):
    assert render.band(p) == word


@pytest.mark.parametrize("text", ["Collected at 14:05.", "On 2026-01-05.", "Specimen SP-r100071-000001.",
                                  "Scenario S0042.", "Its planted fault.", "The breach label.", "A disruption."])
def test_an_alert_state_with_an_id_a_time_or_a_hidden_word_is_refused(text):
    with pytest.raises(render.RenderError):
        render.check_alert_text(text)


def test_the_hidden_word_check_reads_whole_words():
    assert render.check_alert_text("Late because the default route was slow.")
    with pytest.raises(render.RenderError):
        render.check_alert_text("Breaches on this route.")


def test_a_state_over_its_token_budget_is_refused():
    with pytest.raises(render.RenderError, match="budget"):
        render.alert_state(facts(), budget=50)


# --- X1 ----------------------------------------------------------------------------------------------------------------

def _record(i=0):
    scenario = json.loads((ROOT / "config" / "scenario.example.json").read_text())
    return {"scenario_id": f"S{i:04d}", "scenario": scenario, "kpis": None,
            "planner": {"failure_focus": scenario["failure_focus"], "edge_tags": ["unnoticed_outage"]}}


def test_the_scenario_state_leaves_out_what_x1_asks_and_the_generators_own_words():
    r = _record()
    s = r["scenario"]
    text = render.scenario_state(s, None, budget=1500)
    assert s["narrative"][:30] not in text and s["realism_notes"][:30] not in text
    assert s["failure_focus"] not in text and "edge" not in text.lower()
    assert "Receiving staff by shift: weekday" in text and "tube line outage" in text


@pytest.mark.parametrize("kind", critic.CORRUPTIONS)
def test_each_corruption_shows_in_the_state(kind):
    s = _record()["scenario"]
    bad = critic.corrupt(s, kind)
    assert bad != s and s == _record()["scenario"]                           # a copy; the original untouched
    assert render.scenario_state(bad) != render.scenario_state(s)


def test_the_controls_are_half_corrupted_and_drawn_in_the_seeds_order():
    records = [_record(i) for i in range(10)]
    draw = critic.control_draw(records, 3, seed=0)
    assert [k for _, k in draw] == [*critic.CORRUPTIONS[:3], "clean", "clean", "clean"]
    assert len({r["scenario_id"] for r, _ in draw}) == 6 and draw == critic.control_draw(records, 3, seed=0)
    cfg = config()
    cfg["samples"] = {**cfg["samples"], "X1_controls_each": 3}
    built = critic.build_items(records, cfg)
    labels = [i for i in built if i.meta["kind"] == "label"]
    assert len(labels) == 10 and len(built) == 16
    assert set(labels[0].questions) == {"plausibility", "dominant_failure", "edge_unnoticed_outage"}
    assert set(built[-1].questions) == {"plausibility", "dominant_failure"}


def test_x4_is_not_built_and_says_why():
    with pytest.raises(distill.NotBuilt, match="cut list"):
        distill.run()
