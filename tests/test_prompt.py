import json
import re
from types import SimpleNamespace

import pytest

from src.generator import briefs as briefs_mod
from src.generator import prompt

ROOT = prompt.ROOT


@pytest.fixture(scope="module")
def briefs():
    return briefs_mod.make_briefs(0, 0, 50)


def fake_response(text='{"specimen_id": "x"}', stop_reason="end_turn", blocks=None, **usage):
    usage = {"input_tokens": 400, "output_tokens": 700, "cache_creation_input_tokens": 0,
             "cache_read_input_tokens": 0, **usage}
    content = blocks if blocks is not None else [SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=SimpleNamespace(**usage),
                           _request_id="req_test")


class FakeClient:
    def __init__(self, response):
        self.calls = []
        self.messages = SimpleNamespace(create=self.create)
        self._response = response

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


# ---- prompt assembly ----

def test_request_uses_the_agreed_model_effort_and_no_thinking(briefs):
    r = prompt.build_request(briefs[0])
    assert r["model"] == "claude-haiku-5-5"
    assert r["thinking"] == {"type": "disabled"}
    assert r["output_config"]["effort"] == "low"
    assert r["output_config"]["format"] == {"type": "json_schema", "schema": prompt.TRAIL_SCHEMA}
    assert not {"temperature", "top_p", "top_k", "tools", "tool_choice"} & set(r)


def test_stable_text_comes_first_and_is_identical_across_briefs(briefs):
    a, b = prompt.build_request(briefs[0]), prompt.build_request(briefs[1])
    assert a["system"] == b["system"] and a["output_config"] == b["output_config"]
    assert a["messages"] != b["messages"]
    assert list(a).index("system") < list(a).index("messages")
    assert a["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_the_brief_is_last_and_is_the_only_message(briefs):
    r = prompt.build_request(briefs[3])
    assert len(r["messages"]) == 1 and r["messages"][0]["role"] == "user"
    sent = json.loads(r["messages"][0]["content"])
    assert sent == prompt.model_brief(briefs[3])
    assert list(sent) == ["specimen_id", "fixed", "planted_fault", "targets", "locations"]


def test_a_retry_puts_the_validator_error_inside_the_brief(briefs):
    plain, retry = prompt.build_request(briefs[0]), prompt.build_request(briefs[0], error="check 5: too long")
    assert plain["system"] == retry["system"]
    sent = json.loads(retry["messages"][0]["content"])
    assert sent["validator_error"] == "check 5: too long" and list(sent)[-1] == "validator_error"
    assert "validator_error" in prompt.system_text()
    assert "validator_error" not in plain["messages"][0]["content"]


def test_claude_never_sees_the_label(briefs):
    text = prompt.build_request(briefs[0])["messages"][0]["content"]
    assert "label" not in json.loads(text)
    assert not re.search(r"breach|window_min|policy_id", text)


def test_system_text_is_deterministic_and_long_enough_to_cache():
    assert prompt.system_text() == prompt.system_text()
    assert len(prompt.system_text()) / 4 > 600  # the cache minimum is 512 tokens on this model


def test_the_rules_say_what_a_note_may_contain():
    text = prompt.system_text()
    assert "must not reveal the true path" in text
    assert "Do not restate the specimen type, tests, priority or route" in text
    assert "Most scans have no note" in text


def test_system_text_carries_the_recipe_rules():
    text = prompt.system_text()
    for phrase in ("Never record a scan for a hidden stage", "within 2 minutes", "at most two, at most 12 words",
                   "Return only JSON that matches the schema"):
        assert phrase in text
    for state in prompt.TRUTH_STATES:
        assert state in text


def test_schema_follows_the_recipe():
    s = prompt.TRAIL_SCHEMA
    assert s["required"] == ["specimen_id", "observed_events", "truth_segments", "outcome"]
    assert s["additionalProperties"] is False
    event = s["properties"]["observed_events"]["items"]
    assert event["properties"]["activity"]["enum"] == ["ordered", "collected", "received", "accessioned", "plated",
                                                       "rejected"]
    assert "note" in event["properties"] and "note" not in event["required"]
    assert s["properties"]["truth_segments"]["items"]["properties"]["end"]["type"] == ["string", "null"]


def test_the_worked_examples_fit_the_schema_and_the_brief_shape(briefs):
    def shape(schema, value, where="trail"):
        if "enum" in schema:
            assert value in schema["enum"], where
        elif schema["type"] == "object":
            assert set(schema["required"]) <= set(value) <= set(schema["properties"]), where
            for k, v in value.items():
                shape(schema["properties"][k], v, f"{where}.{k}")
        elif schema["type"] == "array":
            for i, v in enumerate(value):
                shape(schema["items"], v, f"{where}[{i}]")
        else:
            types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            py = {"string": str, "boolean": bool, "null": type(None)}
            assert isinstance(value, tuple(py[t] for t in types)), where

    for brief, trail in zip(prompt.EXAMPLE_BRIEFS, prompt.EXAMPLE_TRAILS):
        shape(prompt.TRAIL_SCHEMA, trail)
        assert brief["specimen_id"] == trail["specimen_id"]
        assert list(brief) == list(prompt.model_brief(briefs[0]))
        assert list(brief["targets"]) == list(briefs[0]["targets"])


def test_count_request_drops_only_max_tokens(briefs):
    r = prompt.build_request(briefs[0])
    c = prompt.count_request(r)
    assert set(r) - set(c) == {"max_tokens"} and c["system"] == r["system"]


# ---- the estimate and the budget gate, with no network ----

def test_prices_match_the_plan_table():
    html = (ROOT / "visuals" / "plan.html").read_text(encoding="utf-8")
    ids = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
    for key, model in ids.items():
        m = re.search(key + r':\s*\{name:"[^"]*",\s*in:([\d.]+),\s*out:([\d.]+),\s*cache:([\d.]+)\}', html)
        assert m, key
        assert tuple(map(float, m.groups())) == tuple(prompt.PRICES[model][k] for k in ("in", "out", "cache_read"))
    assert float(re.search(r"OUT_ALLOWANCE = ([\d.]+);", html).group(1)) == prompt.OUT_ALLOWANCE


def counter(prefix=3000, brief=350):
    """A stand-in for the API's tokenizer: the prefix plus a fixed-size brief."""
    calls = []

    def count(request):
        calls.append(request)
        return prefix + (brief if request["messages"][0]["content"] != prompt._dump(prompt.EMPTY_BRIEF) else 0)
    count.calls = calls
    return count


def test_estimate_prices_the_plan_pilot_at_about_ten_cents(briefs):
    est = prompt.estimate_job(counter(), briefs, n=200)
    per = (350 * 0.10 + 3000 * 0.01 + 750 * 1.15 * 0.50) / 1e6
    assert est["cost_usd"] == pytest.approx(200 * per + 3000 * 0.10 * 1.25 / 1e6)
    assert 0.09 < est["cost_usd"] < 0.11
    assert (est["prefix_tokens"], est["brief_tokens"], est["model"]) == (3000, 350.0, "claude-haiku-5-5")


def test_batch_is_half_price(briefs):
    full = prompt.estimate_job(counter(), briefs, n=12000)["cost_usd"]
    half = prompt.estimate_job(counter(), briefs, n=12000, batch=True)["cost_usd"]
    assert half == pytest.approx(full / 2)


def test_estimate_counts_the_request_it_would_send(briefs):
    count = counter()
    prompt.estimate_job(count, briefs)
    assert count.calls and all(c["model"] == "claude-haiku-5-5" and "max_tokens" in c for c in count.calls)


def test_an_over_budget_job_aborts_before_anything_is_sent(briefs):
    est = prompt.estimate_job(counter(), briefs, n=12000)
    with pytest.raises(prompt.BudgetError, match="exceeds the \\$5.00 budget"):
        prompt.check_budget(est, 5.00)
    prompt.check_budget(est, 50.00)


def test_the_estimate_prints_in_plain_words(briefs):
    line = prompt.format_estimate(prompt.estimate_job(counter(), briefs, n=200))
    assert "200 trails on claude-haiku-5-5" in line and "estimated $0.0996" in line


# ---- calling, parsing and logging, against a fake client ----

TRAIL = {"specimen_id": "SP-r00-000001", "observed_events": [], "truth_segments": [],
         "outcome": {"received": False, "plated": False, "fault": "none"}}


def test_generate_trail_returns_the_parsed_trail_and_logs_usage(briefs, tmp_path):
    log = tmp_path / "claude" / "usage.jsonl"
    client = FakeClient(fake_response(json.dumps(TRAIL), cache_read_input_tokens=3000, input_tokens=350))
    assert prompt.generate_trail(client, briefs[0], job="J1", log_path=log) == TRAIL
    assert client.calls[0]["model"] == "claude-haiku-5-5"
    row = json.loads(log.read_text())
    assert row["job"] == "J1" and row["specimen_id"] == briefs[0]["specimen_id"]
    assert row["cache_read_input_tokens"] == 3000 and row["request_id"] == "req_test"
    assert row["cost_usd"] == pytest.approx((350 * 0.10 + 3000 * 0.01 + 700 * 0.50) / 1e6, rel=1e-6)
    prompt.generate_trail(client, briefs[1], job="J1", log_path=log)
    assert len(log.read_text().splitlines()) == 2  # appends


def test_thinking_blocks_are_skipped_when_reading_the_reply(briefs, tmp_path):
    blocks = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=json.dumps(TRAIL))]
    client = FakeClient(fake_response(blocks=blocks))
    assert prompt.generate_trail(client, briefs[0], job="J1", log_path=tmp_path / "u.jsonl") == TRAIL


@pytest.mark.parametrize("stop", ["max_tokens", "refusal"])
def test_an_incomplete_reply_is_an_error_but_is_still_logged(briefs, tmp_path, stop):
    log = tmp_path / "u.jsonl"
    with pytest.raises(prompt.TrailError, match=stop):
        prompt.generate_trail(FakeClient(fake_response(stop_reason=stop)), briefs[0], job="J1", log_path=log)
    assert json.loads(log.read_text())["stop_reason"] == stop


def test_a_reply_that_is_not_json_is_an_error(briefs, tmp_path):
    with pytest.raises(prompt.TrailError, match="not JSON"):
        prompt.generate_trail(FakeClient(fake_response("here is your trail")), briefs[0], job="J1",
                              log_path=tmp_path / "u.jsonl")


def test_the_client_needs_a_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(prompt, "ROOT", tmp_path)  # no .env here
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is not set"):
        prompt.make_client()
