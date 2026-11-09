"""The OpenAI client (docs/neural/spec.md §4.4, §15.2): request shape, usage parsing, incomplete and refusal handling,
and that no key, Authorization header or other request header reaches a log line, an exception, a stored file or the
test output. A mock transport stands in for the network; no request leaves the machine."""

import gc
import json
import logging
import traceback

import httpx2
import pytest

from src.scenarios import client as C
from src.scenarios.client import CallResult, ClientError, OpenAIClient, build_body, parse_response

# Assembled at run time so no key-shaped literal sits in the repository.
FAKE_KEY = "sk-proj-" + "tracerFAKEkey" + "0123456789" * 3
OTHER_KEY = "sk-proj-" + "tracerOTHERkey" + "9876543210" * 3
SETTINGS = {"base_url": "https://api.example.invalid/v1", "env_file": ".env", "key_name": "OPENAI_API_KEY",
            "flex_timeout_s": 900, "standard_timeout_s": 600, "schema_name": "laboratory_week"}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["narrative"],
          "properties": {"narrative": {"type": "string"}}}
HEADER_WORDS = ("authorization", "bearer", "x-stainless", "user-agent", "openai-organization", "openai-project",
                "x-ratelimit", "retry-after")


def usage(inp=7000, cached=1000, writes=0, out=4000, reasoning=1500):
    return {"input_tokens": inp, "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": writes},
            "output_tokens": out, "output_tokens_details": {"reasoning_tokens": reasoning},
            "total_tokens": inp + out}


def response(content, *, status="completed", tier="flex", reason=None):
    return {"id": "resp_abc", "object": "response", "status": status, "service_tier": tier,
            "incomplete_details": {"reason": reason} if reason else None, "error": None,
            "output": [{"type": "reasoning", "summary": []},
                       {"type": "message", "role": "assistant", "status": "completed", "content": content}],
            "usage": usage()}


def text(obj):
    return [{"type": "output_text", "text": json.dumps(obj), "annotations": []}]


RATE_HEADERS = {"x-request-id": "req_123", "x-ratelimit-limit-tokens": "1000000",
                "x-ratelimit-remaining-tokens": "999000", "x-ratelimit-reset-tokens": "6m0s",
                "x-ratelimit-limit-requests": "5000", "x-ratelimit-remaining-requests": "4999"}


class Transport:
    """Records each request and answers from a queue of (status, json body, headers) or exceptions."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise type(answer)(str(answer), request=request)
        status, body, headers = answer
        content = body if isinstance(body, (bytes, str)) else json.dumps(body)
        return httpx2.Response(status, content=content, headers={**RATE_HEADERS, **headers})


def make_client(tmp_path, transport, key=FAKE_KEY):
    if key is not None:
        (tmp_path / ".env").write_text(f"OPENAI_API_KEY={key}\n")
    return OpenAIClient(env_path=tmp_path / ".env", settings=SETTINGS,
                        http_client=httpx2.Client(transport=httpx2.MockTransport(transport)))


def body(**kw):
    args = dict(model="gpt-6.1-terra", messages=[{"role": "developer", "content": "write one week"},
                                                  {"role": "user", "content": '{"brief_id": "B1"}'}],
                schema=SCHEMA, schema_name="laboratory_week", effort="low", max_output_tokens=16000,
                service_tier="flex", prompt_cache_key="tracer-v2-O1")
    args.update(kw)
    return build_body(**args)


def assert_clean(*texts):
    for t in texts:
        low = str(t).lower()
        assert FAKE_KEY.lower() not in low and OTHER_KEY.lower() not in low, "a key leaked"
        assert "sk-proj-" not in low, "a key-shaped string leaked"
        for word in HEADER_WORDS:
            assert word not in low, f"a request or rate-limit header leaked: {word}"


# -- request shape ---------------------------------------------------------------------------------------------------

def test_the_request_has_the_documented_shape_and_no_temperature(tmp_path):
    t = Transport((200, response(text({"narrative": "ok"})), {}))
    c = make_client(tmp_path, t)
    r = c.call(body(), timeout=900)
    assert r.ok and r.scenario == {"narrative": "ok"}
    sent = json.loads(t.requests[0].content)
    assert t.requests[0].url.path.endswith("/responses")
    assert sent["text"] == {"format": {"type": "json_schema", "name": "laboratory_week", "schema": SCHEMA,
                                       "strict": True}}
    assert sent["reasoning"] == {"effort": "low"}
    assert sent["max_output_tokens"] == 16000
    assert sent["service_tier"] == "flex"
    assert sent["store"] is False
    assert sent["prompt_cache_key"] == "tracer-v2-O1"
    assert sent["input"][0]["role"] == "developer"
    assert "temperature" not in sent and "top_p" not in sent
    assert t.requests[0].headers["authorization"] == f"Bearer {FAKE_KEY}"  # the key came from .env


def test_a_temperature_is_refused_before_anything_is_sent(tmp_path):
    t = Transport((200, response(text({})), {}))
    with pytest.raises(ValueError):
        make_client(tmp_path, t).call({**body(), "temperature": 0.2}, timeout=900)
    assert t.requests == []


def test_standard_and_batch_tiers_use_the_documented_values():
    assert C.build_body(model="m", messages=[], schema=SCHEMA, schema_name="n", effort="low", max_output_tokens=16,
                        service_tier="default", prompt_cache_key=None)["service_tier"] == "default"
    assert "service_tier" not in body(service_tier=None)


def test_the_key_is_read_from_env_at_call_time_never_at_construction(tmp_path):
    t = Transport((200, response(text({"narrative": "ok"})), {}))
    c = OpenAIClient(env_path=tmp_path / ".env", settings=SETTINGS,
                     http_client=httpx2.Client(transport=httpx2.MockTransport(t)))  # no .env yet: no error
    with pytest.raises(ClientError) as e:
        c.call(body(), timeout=900)
    assert e.value.kind == "missing_key" and t.requests == []
    (tmp_path / ".env").write_text(f"OPENAI_API_KEY={FAKE_KEY}\n")
    c.call(body(), timeout=900)
    (tmp_path / ".env").write_text(f"OPENAI_API_KEY={OTHER_KEY}\n")
    c.call(body(), timeout=900)
    assert [r.headers["authorization"] for r in t.requests] == [f"Bearer {FAKE_KEY}", f"Bearer {OTHER_KEY}"]


def test_the_environment_is_not_a_key_source(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    t = Transport((200, response(text({})), {}))
    c = OpenAIClient(env_path=tmp_path / ".env", settings=SETTINGS,
                     http_client=httpx2.Client(transport=httpx2.MockTransport(t)))
    with pytest.raises(ClientError, match="missing_key"):
        c.call(body(), timeout=900)
    assert t.requests == []


# -- parsing ---------------------------------------------------------------------------------------------------------

def test_usage_is_parsed_with_cached_and_reasoning_tokens_and_the_tier_served(tmp_path):
    raw = response(text({"narrative": "ok"}), tier="flex")
    raw["usage"] = usage(inp=12000, cached=4000, writes=7000, out=5000, reasoning=2200)
    c = make_client(tmp_path, Transport((200, raw, {})))
    seen = []
    r = c.call(body(), timeout=900, on_raw=seen.append)
    assert json.loads(seen[0]) == raw  # the raw body, handed over before parsing
    assert r.tokens() == {"input": 12000, "cached": 4000, "cache_write": 7000, "output": 5000, "reasoning": 2200}
    assert r.service_tier == "flex" and r.request_id == "req_123" and r.response_id == "resp_abc"
    assert r.elapsed_s >= 0
    assert r.rate_limits.limit_tokens == 1_000_000 and r.rate_limits.remaining_tokens == 999_000
    assert r.rate_limits.reset_tokens_s == 360


def test_an_incomplete_response_is_a_billed_failure_with_its_reason():
    r = parse_response(response(text({"narrative": "cut"}), status="incomplete", reason="max_output_tokens"))
    assert r.status == "incomplete" and r.incomplete_reason == "max_output_tokens" and r.scenario is None
    assert r.usage["output_tokens"] == 4000  # still billed: usage is kept for settlement


def test_a_refusal_is_a_failure_and_keeps_its_text():
    r = parse_response(response([{"type": "refusal", "refusal": "I can't help with that."}]))
    assert r.status == "refusal" and "can't" in r.refusal and r.scenario is None


@pytest.mark.parametrize("content", [[{"type": "output_text", "text": "not json"}],
                                     [{"type": "output_text", "text": "[1, 2]"}], []])
def test_output_that_is_not_a_json_object_is_not_json(content):
    assert parse_response(response(content)).status == "not_json"


def test_a_failed_or_odd_status_is_a_failure():
    raw = response(text({}), status="failed")
    raw["error"] = {"code": "server_error", "message": "boom"}
    r = parse_response(raw)
    assert r.status == "failed" and r.error_code == "server_error"
    assert parse_response(None).status == "not_json"
    assert parse_response({**response(text({})), "status": "cancelled"}).status == "failed"


def test_a_body_that_is_not_json_is_handed_over_raw_and_reported(tmp_path):
    seen = []
    r = make_client(tmp_path, Transport((200, b"<html>gateway</html>", {}))).call(body(), timeout=900,
                                                                                 on_raw=seen.append)
    assert seen == ["<html>gateway</html>"] and r.status == "not_json"


# -- errors ----------------------------------------------------------------------------------------------------------

def test_a_flex_429_is_reported_with_its_rate_limit_values(tmp_path):
    err = {"error": {"message": "Resource Unavailable: flex capacity", "type": "rate_limit_error", "code": None}}
    r = make_client(tmp_path, Transport((429, err, {"retry-after": "56"}))).call(body(), timeout=900)
    assert r.status == "http_429" and r.http_status == 429 and r.resource_unavailable
    assert r.rate_limits.retry_after_s == 56 and r.request_id == "req_123"


def test_billing_errors_keep_their_code_and_type(tmp_path):
    err = {"error": {"message": "You exceeded your quota", "type": "insufficient_quota",
                     "code": "credit_balance_exhausted"}}
    r = make_client(tmp_path, Transport((429, err, {}))).call(body(), timeout=900)
    assert (r.status, r.error_type, r.error_code, r.resource_unavailable) == ("http_429", "insufficient_quota",
                                                                             "credit_balance_exhausted", False)


def test_a_timeout_and_a_dropped_connection_are_results_not_exceptions(tmp_path):
    assert make_client(tmp_path, Transport(httpx2.ReadTimeout("slow"))).call(body(), timeout=1).status == "timeout"
    assert make_client(tmp_path, Transport(httpx2.ConnectError("gone"))).call(body(), timeout=1).status == "connection"


def test_the_sdk_never_retries_on_its_own(tmp_path):
    t = Transport((500, {"error": {"message": "x", "type": "server_error"}}, {}))
    r = make_client(tmp_path, t).call(body(), timeout=900)
    assert r.status == "http_500" and len(t.requests) == 1


# -- no key, no header: logs, exceptions, stored files, output ----------------------------------------------------

def test_no_key_or_header_reaches_a_log_an_exception_a_stored_file_or_the_output(tmp_path, caplog, capsys):
    leaky = {"error": {"message": f"Incorrect API key provided: {FAKE_KEY[:12]}****{FAKE_KEY[-4:]} "
                                  f"(Authorization: Bearer {FAKE_KEY})", "type": "invalid_request_error",
                       "code": "invalid_api_key"}}
    store = tmp_path / "responses"
    store.mkdir()
    writes = []

    def on_raw(raw):
        writes.append(raw)
        (store / f"r{len(writes)}.json").write_text(raw)

    t = Transport((200, response(text({"narrative": "ok"})), {}), (401, leaky, {}), (429, leaky, {}),
                  httpx2.ReadTimeout(f"timeout with {FAKE_KEY}"), httpx2.ConnectError(f"refused, key {FAKE_KEY}"))
    c = make_client(tmp_path, t)
    caught = []
    with caplog.at_level(logging.DEBUG):
        results = [c.call(body(), timeout=900, on_raw=on_raw) for _ in range(5)]
        for exc in ("missing", "exception"):
            try:
                if exc == "missing":
                    OpenAIClient(env_path=tmp_path / "absent.env", settings=SETTINGS).call(body(), timeout=1)
                else:  # an unexpected SDK failure whose message carries the key
                    broken = make_client(tmp_path, Transport((200, response(text({})), {})))

                    def explode(**kw):
                        raise RuntimeError(f"unexpected, with Authorization: Bearer {FAKE_KEY}")
                    sdk = type("SDK", (), {})()
                    sdk.responses = type("R", (), {"with_raw_response": type("W", (), {"create": staticmethod(explode)})})
                    broken._sdk = lambda: sdk
                    broken.call(body(), timeout=1)
            except ClientError as e:
                caught.append(e)
        for fn in (lambda: c.retrieve_batch("batch_1"), lambda: c.file_lines("file_1")):
            try:
                fn()
            except ClientError as e:
                caught.append(e)
    assert [r.status for r in results] == ["ok", "http_401", "http_429", "timeout", "connection"]
    assert len(caught) == 4
    for e in caught:
        assert e.__cause__ is None and e.__context__ is None, "an SDK exception (with its request) is still attached"
        assert_clean(str(e), repr(e), "".join(traceback.format_exception(e)), e.args)
    for r in results:
        assert_clean(json.dumps(r.record()), repr(r))
    assert_clean(*(rec.getMessage() for rec in caplog.records), caplog.text)
    assert_clean(*(p.read_text() for p in tmp_path.rglob("*") if p.is_file() and p.name != ".env"))
    out = capsys.readouterr()
    assert_clean(out.out, out.err)
    assert results[1].error_code == "invalid_api_key" and results[1].request_id == "req_123"
    gc.collect()


def test_rate_limit_values_never_reach_the_stored_record(tmp_path):
    r = make_client(tmp_path, Transport((200, response(text({"narrative": "ok"})), {}))).call(body(), timeout=900)
    rec = r.record()
    assert "rate_limits" not in rec and "scenario" not in rec
    assert repr(r.rate_limits) == "RateLimits(...)"
    assert_clean(json.dumps(rec))


def test_identifiers_with_odd_characters_are_dropped():
    assert C._safe("req_123") == "req_123"
    assert C._safe(f"Bearer {FAKE_KEY}") == "redacted"
    assert C._safe("x" * 65) == "redacted"


# -- Batch and token counting ----------------------------------------------------------------------------------------

def test_batch_calls_use_the_files_and_batches_endpoints(tmp_path):
    batch = {"id": "batch_1", "object": "batch", "status": "validating", "metadata": {"chunk_id": "O1-c1"}}
    lines = [{"id": "batch_req_1", "custom_id": "O1-B1-1",
              "response": {"status_code": 200, "request_id": "req_9", "body": response(text({"narrative": "ok"}))},
              "error": None}]

    def route(request):
        path = request.url.path
        if path.endswith("/files"):
            assert b"purpose" in request.content and b'"custom_id": "O1-B1-1"' in request.content
            return httpx2.Response(200, json={"id": "file_in", "object": "file"})
        if path.endswith("/batches") and request.method == "POST":
            sent = json.loads(request.content)
            assert sent == {"input_file_id": "file_in", "endpoint": "/v1/responses", "completion_window": "24h",
                            "metadata": {"chunk_id": "O1-c1", "job": "O1"}}
            return httpx2.Response(200, json=batch)
        if path.endswith("/batches") and request.method == "GET":
            return httpx2.Response(200, json={"data": [batch]})
        if path.endswith("/batches/batch_1"):
            return httpx2.Response(200, json={**batch, "status": "completed", "output_file_id": "file_out"})
        if path.endswith("/batches/batch_1/cancel"):
            return httpx2.Response(200, json={**batch, "status": "cancelling"})
        if path.endswith("/files/file_out/content"):
            return httpx2.Response(200, content="\n".join(json.dumps(x) for x in lines) + "\n")
        return httpx2.Response(404, json={"error": {"type": "not_found", "message": "no"}})

    (tmp_path / ".env").write_text(f"OPENAI_API_KEY={FAKE_KEY}\n")
    c = OpenAIClient(env_path=tmp_path / ".env", settings=SETTINGS,
                     http_client=httpx2.Client(transport=httpx2.MockTransport(route)))
    line = {"custom_id": "O1-B1-1", "method": "POST", "url": "/v1/responses", "body": body(service_tier=None)}
    assert c.submit_batch([line], metadata={"chunk_id": "O1-c1", "job": "O1"})["id"] == "batch_1"
    assert c.retrieve_batch("batch_1")["output_file_id"] == "file_out"
    assert c.cancel_batch("batch_1")["status"] == "cancelling"
    assert c.file_lines("file_out")[0]["custom_id"] == "O1-B1-1"
    assert c.find_batch("O1-c1")["id"] == "batch_1" and c.find_batch("other") is None
    with pytest.raises(ClientError) as e:
        c.retrieve_batch("missing")
    assert e.value.kind == "http_404" and e.value.stage == "batch status"


def test_a_failed_upload_is_reported_as_the_upload_stage(tmp_path):
    c = make_client(tmp_path, Transport((400, {"error": {"type": "invalid_request_error", "message": "bad"}}, {})))
    with pytest.raises(ClientError) as e:
        c.submit_batch([{"custom_id": "x"}], metadata={})
    assert (e.value.stage, e.value.status) == ("upload", 400)


def test_the_token_counting_endpoint_is_gone():
    """F009: its billing is undocumented and the byte bound makes it unnecessary."""
    assert not hasattr(OpenAIClient, "count_input_tokens")


def test_finding_a_batch_reads_every_page_of_the_list(tmp_path):
    """F003: a chunk older than the newest 100 batches must still be found."""
    pages = {None: {"data": [{"id": f"batch_{i}", "metadata": {"chunk_id": f"c{i}"}} for i in range(100)],
                    "has_more": True, "last_id": "batch_99"},
             "batch_99": {"data": [{"id": "batch_100", "metadata": None},
                                   {"id": "batch_101", "metadata": {"chunk_id": "O2-old"}}],
                          "has_more": False, "last_id": "batch_101"}}
    seen = []

    def route(request):
        after = request.url.params.get("after")
        seen.append(after)
        return httpx2.Response(200, json=pages[after])

    (tmp_path / ".env").write_text(f"OPENAI_API_KEY={FAKE_KEY}\n")
    c = OpenAIClient(env_path=tmp_path / ".env", settings=SETTINGS,
                     http_client=httpx2.Client(transport=httpx2.MockTransport(route)))
    assert c.find_batch("O2-old")["id"] == "batch_101" and seen == [None, "batch_99"]
    seen.clear()
    assert c.find_batch("never-made") is None and seen == [None, "batch_99"]

    def stuck(request):  # a list that never advances ends the search instead of looping
        return httpx2.Response(200, json={"data": [{"id": "batch_1", "metadata": None}], "has_more": True,
                                          "last_id": "batch_1"})
    c = OpenAIClient(env_path=tmp_path / ".env", settings=SETTINGS,
                     http_client=httpx2.Client(transport=httpx2.MockTransport(stuck)))
    assert c.find_batch("x") is None


def test_an_http_error_keeps_the_usage_it_reports_as_numbers_only(tmp_path):
    """F002: the spec's exception "unless the error says tokens were consumed" needs the usage an error body carries,
    and nothing else from it."""
    err = {"error": {"message": f"upstream failed for Bearer {FAKE_KEY}", "type": "server_error", "code": None},
           "usage": {"input_tokens": 9000, "input_tokens_details": {"cached_tokens": 1000, "note": "x"},
                     "output_tokens": 120, "output_tokens_details": {"reasoning_tokens": 100},
                     "label": f"Bearer {FAKE_KEY}", "total_tokens": "9120"}}
    r = make_client(tmp_path, Transport((500, err, {}))).call(body(), timeout=900)
    assert r.status == "http_500" and r.tokens_consumed
    assert r.usage == {"input_tokens": 9000, "input_tokens_details": {"cached_tokens": 1000}, "output_tokens": 120,
                       "output_tokens_details": {"reasoning_tokens": 100}}
    assert_clean(json.dumps(r.record()), repr(r))
    nested = {"error": {"message": "m", "type": "server_error", "usage": {"input_tokens": 50, "output_tokens": 0}}}
    assert make_client(tmp_path, Transport((502, nested, {}))).call(body(), timeout=900).usage["input_tokens"] == 50
    flag = {"error": {"message": "m", "type": "server_error", "billed_tokens": 3}}
    r = make_client(tmp_path, Transport((500, flag, {}))).call(body(), timeout=900)
    assert r.usage is None and r.tokens_consumed
    plain = make_client(tmp_path, Transport((500, {"error": {"message": "m", "type": "server_error"}}, {})))
    r = plain.call(body(), timeout=900)
    assert r.usage is None and not r.tokens_consumed
    assert C.billing_evidence("<html>bad gateway</html>") == (None, False)
    limits = {"error": {"type": "tokens", "code": "rate_limit_exceeded", "limit_tokens": 1_000_000,
                        "remaining_tokens": 0, "requested_tokens": 40_000}}
    assert C.billing_evidence(limits) == (None, False)  # a rate limit's numbers are not consumption


def test_call_results_report_their_http_status():
    assert CallResult(status="http_503").http_status == 503
    assert CallResult(status="timeout").http_status is None
