import json
from types import SimpleNamespace

import jsonschema
import pytest

from src.api import export, heroes
from src.generator import prompt

BUNDLE_PATH = export.DEFAULT_OUT


@pytest.fixture(scope="module")
def bundle():
    return json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def stories():
    return json.loads(heroes.OUT_PATH.read_text(encoding="utf-8"))


def facts_of(bundle, kind):
    return heroes.facts_for(bundle, next(c for c in bundle["showcase_candidates"] if c["kind"] == kind))


def reply_for(facts, **change):
    """A reply that passes every check for these facts."""
    alert = facts["what_tracer_did"].get("alert_minute")
    story = (f"A specimen was collected at minute 0 and Tracer {'alerted at minute %d' % alert if alert is not None else 'did not alert'}. "
             f"The stability window was {facts['specimen']['stability_window_minutes']} minutes.")
    return {"story": story, "takeaway": "A short alert can leave time to act.", "question": "Who should act on an early alert?"} | change


def message(reply):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(reply) if isinstance(reply, dict) else reply)],
                           stop_reason="end_turn", _request_id="req",
                           usage=SimpleNamespace(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=0, cache_read_input_tokens=0))


class FakeClient:
    def __init__(self, bundle, bad_first=(), bad_always=()):
        self.facts = {json.dumps(heroes.facts_for(bundle, c), sort_keys=True): c["id"] for c in bundle["showcase_candidates"]}
        self.by_id = {c["id"]: heroes.facts_for(bundle, c) for c in bundle["showcase_candidates"]}
        self.bad_first, self.bad_always = set(bad_first), set(bad_always)
        self.calls = []
        self.messages = SimpleNamespace(create=self.create, count_tokens=lambda **kw: SimpleNamespace(input_tokens=1000))

    def create(self, **kw):
        sent = json.loads(kw["messages"][0]["content"])
        error = sent.pop("validator_error", None)
        sid = self.facts[json.dumps(sent, sort_keys=True)]
        self.calls.append((sid, error))
        broken = sid in self.bad_always or (sid in self.bad_first and error is None)
        return message(reply_for(self.by_id[sid], story="This one is wrong by 999 minutes.") if broken else reply_for(self.by_id[sid]))


# ---- the facts ----

def test_every_candidate_has_facts_in_plain_words_and_no_identifiers(bundle):
    for c in bundle["showcase_candidates"]:
        f = heroes.facts_for(bundle, c)
        text = json.dumps(f)
        assert c["id"] not in text and "SP-r" not in text and "stalled_at_station" not in text and "at_min" not in text
        assert f["specimen"]["stability_window_minutes"] == c["window_min"] and f["what_makes_it_worth_telling"]
        assert f["what_really_went_wrong"] and f["outcome"] and f["true_path"]
        assert all(e["event"] in ("collected", "received", "rejected") for e in f["timeline_seen_by_the_laboratory"])  # the clock stops at receipt


def test_the_facts_carry_what_tracer_and_the_rule_did(bundle):
    catch = facts_of(bundle, "clear_catch")
    assert catch["what_tracer_did"]["alerted"] and catch["what_tracer_did"]["minutes_of_warning"] == catch["specimen"]["stability_window_minutes"] - catch["what_tracer_did"]["alert_minute"]
    assert catch["what_the_plans_simple_rule_did"]["tracer_alerted_this_many_minutes_earlier"] > 0
    blind = facts_of(bundle, "content_failure")
    assert blind["what_tracer_did"] == {"alerted": False} and "rejected" in json.dumps(blind["timeline_seen_by_the_laboratory"])
    behind = facts_of(bundle, "baseline_ahead")
    assert behind["what_the_plans_simple_rule_did"]["tracer_alerted_this_many_minutes_later"] > 0
    late = facts_of(bundle, "clear_catch")
    assert late["minutes_after_the_window_closed"] > 0 and "after the window" in late["outcome"]
    assert "never received" == facts_of(bundle, "never_sent")["outcome"]


def test_the_true_path_has_durations_and_merges_nothing_it_should_not(bundle):
    f = facts_of(bundle, "stalled_then_found")
    stalls = [p for p in f["true_path"] if "stalled" in p["it"]]
    assert stalls and stalls[0]["for_minutes"] == stalls[0]["to_minute"] - stalls[0]["from_minute"] > 0


def test_the_hash_names_the_candidate_list(bundle):
    c = bundle["showcase_candidates"]
    assert heroes.candidates_hash(c) == export.candidates_hash(c) != heroes.candidates_hash(c[:-1])
    assert heroes.candidates_hash(c) != heroes.candidates_hash(list(reversed(c))) or len(c) < 2


# ---- the checks ----

@pytest.mark.parametrize("change, expect", [
    ({"story": ""}, "story is missing"),
    ({"story": "word " * 91}, "story is 91 words"),
    ({"story": "A **bold** story about 999 minutes."}, "plain text"),
    ({"story": "A story with 77777 minutes."}, "numbers not in the input: 77777"),
    ({"story": "Dr Smith saw it."}, "names a person"),
    ({"story": "Then Maria checked the tube."}, "may name someone or something: Maria"),
    ({"story": "It needed treatment with a new dose."}, "medical advice"),
    ({"takeaway": "One. Two."}, "one sentence"),
    ({"takeaway": "word " * 36}, "one sentence"),
    ({"question": "Not a question."}, "one question"),
    ({"question": "Why? And who?"}, "one question"),
    ({"question": "x " * 36 + "?"}, "one question"),
])
def test_each_check_names_what_is_wrong(bundle, change, expect):
    f = facts_of(bundle, "clear_catch")
    assert any(expect in e for e in heroes.check_story(reply_for(f, **change), f)), heroes.check_story(reply_for(f, **change), f)


def test_a_good_reply_passes_and_a_difference_of_two_stated_minutes_is_allowed(bundle):
    f = facts_of(bundle, "clear_catch")
    assert heroes.check_story(reply_for(f), f) == []
    a, b = f["what_tracer_did"]["alert_minute"], f["what_the_plans_simple_rule_did"]["alert_minute"]
    ok = reply_for(f, takeaway=f"Tracer alerted {b - a} minutes earlier than the simple rule.")
    assert heroes.check_story(ok, f) == []


def test_capitalized_words_at_the_start_of_a_sentence_and_safe_names_are_fine(bundle):
    f = facts_of(bundle, "clear_catch")
    assert heroes.check_story(reply_for(f, story="Tracer alerted. The ICU and the ED sent it. Then it moved."), f) == []


# ---- the cost, the guard and the job ----

def test_the_estimate_prices_ten_stories_not_in_a_batch(bundle):
    e = heroes.estimate_job(lambda request: 1000, [heroes.facts_for(bundle, c) for c in bundle["showcase_candidates"]])
    price = prompt.PRICES[heroes.MODEL]
    one = (1000 * price["in"] + 400 * prompt.OUT_ALLOWANCE * price["out"]) / 1e6
    assert e["n"] == 10 and e["batch"] is False and e["cost_usd"] == pytest.approx(10 * one) and e["cost_usd"] < 0.2


def run(client, bundle, tmp_path, **kw):
    return heroes.run_job(client, bundle, tmp_path / "heroes.json", budget_usd=kw.pop("budget_usd", 0.5), log_path=tmp_path / "u.jsonl")


def test_a_job_writes_ten_stories_for_the_candidate_list_and_logs_each_call(bundle, tmp_path):
    client = FakeClient(bundle)
    doc = run(client, bundle, tmp_path)
    assert [h["id"] for h in doc["heroes"]] == [c["id"] for c in bundle["showcase_candidates"]] and doc["job"]["stories"] == 10
    assert doc["candidates_hash"] == export.candidates_hash(bundle["showcase_candidates"]) and doc["job"]["retries"] == 0
    assert json.loads((tmp_path / "heroes.json").read_text()) == doc
    logged = [json.loads(line) for line in (tmp_path / "u.jsonl").read_text().splitlines()]
    assert len(logged) == 10 and all(r["job"] == "J3" and r["model"] == heroes.MODEL and r["batch"] is False for r in logged)
    assert doc["job"]["cost_usd"] == pytest.approx(sum(r["cost_usd"] for r in logged), abs=1e-4)


def test_a_bad_reply_is_retried_with_the_error_and_a_third_failure_stops_the_job(bundle, tmp_path):
    first = bundle["showcase_candidates"][2]["id"]
    client = FakeClient(bundle, bad_first={first})
    doc = run(client, bundle, tmp_path)
    assert doc["job"]["retries"] == 1
    assert [e for sid, e in client.calls if sid == first][1] and "numbers not in the input" in [e for sid, e in client.calls if sid == first][1]
    stuck = bundle["showcase_candidates"][1]["id"]
    with pytest.raises(RuntimeError, match=f"{stuck}: no acceptable story after 3 tries"):
        run(FakeClient(bundle, bad_always={stuck}), bundle, tmp_path / "again" if False else tmp_path)


def test_a_job_over_budget_or_over_the_guard_sends_nothing(bundle, tmp_path):
    client = FakeClient(bundle)
    with pytest.raises(prompt.BudgetError):
        run(client, bundle, tmp_path, budget_usd=0.0001)
    (tmp_path / "u.jsonl").write_text(json.dumps({"cost_usd": 9.99}) + "\n")
    with pytest.raises(heroes.SpendGuardError, match="ask Daniel"):
        run(client, bundle, tmp_path)
    assert client.calls == []


def test_the_command_line_needs_a_budget_and_the_dry_run_sends_nothing(monkeypatch, bundle, capsys):
    client = FakeClient(bundle)
    monkeypatch.setattr(prompt, "make_client", lambda: client)
    with pytest.raises(SystemExit, match="--budget-usd"):
        heroes.main([])
    assert heroes.main(["--dry-run"]) == 0 and "within the spend guard" in capsys.readouterr().out and client.calls == []


# ---- the committed stories and the bundle ----

def test_there_are_ten_stories_for_the_ten_candidates_and_each_passes_the_checks(bundle, stories):
    cands = {c["id"]: c for c in bundle["showcase_candidates"]}
    assert [h["id"] for h in stories["heroes"]] == list(cands) and len(stories["heroes"]) == 10
    assert stories["candidates_hash"] == export.candidates_hash(bundle["showcase_candidates"])
    assert stories["bundle_dataset_sha256"] == bundle["source"]["sha256"] and stories["model"] == "claude-sonnet-5-5"
    for h in stories["heroes"]:
        assert h["kind"] == cands[h["id"]]["kind"]
        assert heroes.check_story(h, heroes.facts_for(bundle, cands[h["id"]])) == [], h["id"]
        assert not any(w in (h["story"] + h["takeaway"] + h["question"]).lower() for w in ("plating", "plated", "set-up"))


def test_the_prompt_in_the_code_is_the_one_the_stories_were_written_with(stories):
    import hashlib
    assert stories["prompt_hash"] == hashlib.sha256((heroes.SYSTEM + heroes.EFFORT).encode()).hexdigest()[:16]
    assert "no clinical review" in stories["notice"]


def test_the_bundle_carries_exactly_those_stories(bundle, stories):
    assert bundle["heroes"] == [{k: h[k] for k in ("id", "story", "takeaway", "question")} for h in stories["heroes"]]
    schema = json.loads((export.ROOT / "docs" / "demo" / "bundle.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(bundle, schema)


def test_stories_for_a_different_candidate_list_are_left_out_of_the_bundle(tmp_path, bundle, stories, capsys):
    stale = stories | {"candidates_hash": "0" * 16}
    path = tmp_path / "heroes.json"
    path.write_text(json.dumps(stale))
    assert export.load_heroes(bundle["showcase_candidates"], path) == []
    assert "different candidate list" in capsys.readouterr().err
    assert export.load_heroes(bundle["showcase_candidates"], tmp_path / "none.json") == []
    assert len(export.load_heroes(bundle["showcase_candidates"], heroes.OUT_PATH)) == 10
