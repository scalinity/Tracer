"""The OpenAI client: one Responses API call per item, and the Batch API calls, through the official SDK.

docs/neural/spec.md §4.4 is the design; parameter names follow OpenAI's current docs (notes/neural/api-reference.md):
`text.format` is a flat `json_schema` object with `strict: true`; `reasoning.effort`; `max_output_tokens` (it counts
reasoning tokens); `store: false`; `service_tier` ("flex", or "default" for the Standard tier, none inside a Batch);
`prompt_cache_key` per job. A temperature is never sent.

Secrets. The key is read from .env with python-dotenv inside the client at the moment of each call, never from or
into the process environment. Every SDK exception is caught at this boundary and replaced by a ClientError raised
outside the except block (so no exception chain survives), carrying only the HTTP status, the error type, the error
code and the request id. Error messages from the server are dropped (a 401's message can quote part of a key).
Rate-limit header values are parsed into a RateLimits object for the runner's concurrency control and are never
logged or stored. The SDK's own retries are off (max_retries=0): a retry is the runner's decision, made only where
the ledger says the first attempt was not billed.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
JOBS_PATH = ROOT / "config" / "openai_jobs.yaml"
SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
BATCH_ENDPOINT = "/v1/responses"


class ClientError(RuntimeError):
    """A call failed in a way the result object does not describe. Carries no message text from the server."""

    def __init__(self, *, kind: str, status: int | None = None, error_type: str | None = None,
                 code: str | None = None, request_id: str | None = None, stage: str = "call"):
        self.kind, self.status, self.error_type, self.code = kind, status, _safe(error_type), _safe(code)
        self.request_id, self.stage = _safe(request_id), stage
        super().__init__(f"OpenAI {stage} failed: {kind} (status {status}, type {self.error_type}, "
                         f"code {self.code}, request {self.request_id})")


def _safe(value) -> str | None:
    """Keep an identifier only if it looks like one (no spaces, no secrets, at most 64 characters)."""
    if value is None:
        return None
    text = str(value)
    return text if SAFE_TOKEN.match(text) else "redacted"


def api_settings(path: Path = JOBS_PATH) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))["api"]


@dataclass
class RateLimits:
    """Values from the x-ratelimit-* and retry-after headers. Held in memory only."""
    limit_tokens: int | None = None
    remaining_tokens: int | None = None
    reset_tokens_s: float | None = None
    limit_requests: int | None = None
    remaining_requests: int | None = None
    retry_after_s: float | None = None

    def __repr__(self) -> str:  # never printed by accident with values
        return "RateLimits(...)"


def _duration_s(text: str | None) -> float | None:
    """'6m0s', '1s', '120ms', '1h2m3.5s' or a bare number of seconds."""
    if not text:
        return None
    text = str(text).strip()
    try:
        return float(text)
    except ValueError:
        pass
    total, found = 0.0, False
    for number, unit in re.findall(r"([0-9]*\.?[0-9]+)(ms|h|m|s)", text):
        total += float(number) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
        found = True
    return total if found else None


def _int(text) -> int | None:
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return None


def rate_limits(headers) -> RateLimits:
    get = (lambda k: headers.get(k)) if headers is not None else (lambda k: None)
    return RateLimits(limit_tokens=_int(get("x-ratelimit-limit-tokens")),
                      remaining_tokens=_int(get("x-ratelimit-remaining-tokens")),
                      reset_tokens_s=_duration_s(get("x-ratelimit-reset-tokens")),
                      limit_requests=_int(get("x-ratelimit-limit-requests")),
                      remaining_requests=_int(get("x-ratelimit-remaining-requests")),
                      retry_after_s=_duration_s(get("retry-after")))


@dataclass
class CallResult:
    """One Responses API call. `status` is "ok" or the failure kind: incomplete, refusal, not_json, failed,
    http_<code>, timeout or connection."""
    status: str
    scenario: dict | None = None
    usage: dict | None = None
    request_id: str | None = None
    response_id: str | None = None
    service_tier: str | None = None
    response_status: str | None = None
    incomplete_reason: str | None = None
    refusal: str | None = None
    error_type: str | None = None
    error_code: str | None = None
    resource_unavailable: bool = False
    elapsed_s: float = 0.0
    rate_limits: RateLimits | None = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def http_status(self) -> int | None:
        return int(self.status[5:]) if self.status.startswith("http_") else None

    def tokens(self) -> dict:
        """input, cached input, cache writes (None when not reported), output and reasoning tokens."""
        u = self.usage or {}
        inp, out = u.get("input_tokens_details") or {}, u.get("output_tokens_details") or {}
        return {"input": u.get("input_tokens"), "cached": inp.get("cached_tokens"),
                "cache_write": inp.get("cache_write_tokens"), "output": u.get("output_tokens"),
                "reasoning": out.get("reasoning_tokens")}

    def record(self) -> dict:
        """What may be stored: everything except the rate-limit values and the scenario itself."""
        out = asdict(self)
        out.pop("rate_limits")
        out.pop("scenario")
        return out


def parse_response(body: dict) -> CallResult:
    """Read a Responses API body: status, refusal, the JSON scenario, usage and the tier actually served."""
    if not isinstance(body, dict):
        return CallResult(status="not_json")
    out = CallResult(status="ok", usage=body.get("usage"), response_id=_safe(body.get("id")),
                     service_tier=_safe(body.get("service_tier")), response_status=_safe(body.get("status")))
    status = body.get("status")
    if status == "incomplete":
        out.status = "incomplete"
        out.incomplete_reason = _safe((body.get("incomplete_details") or {}).get("reason"))
        return out
    if status != "completed":
        out.status = "failed"
        out.error_code = _safe((body.get("error") or {}).get("code"))
        return out
    texts, refusals = [], []
    for item in body.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "output_text":
                texts.append(part.get("text") or "")
            elif isinstance(part, dict) and part.get("type") == "refusal":
                refusals.append(str(part.get("refusal") or ""))
    if refusals:
        out.status, out.refusal = "refusal", " ".join(refusals)[:500]
        return out
    try:
        scenario = json.loads("".join(texts))
    except json.JSONDecodeError:
        scenario = None
    if not isinstance(scenario, dict):
        out.status = "not_json"
        return out
    out.scenario = scenario
    return out


def build_body(*, model: str, messages: list[dict], schema: dict, schema_name: str, effort: str,
               max_output_tokens: int, service_tier: str | None, prompt_cache_key: str | None) -> dict:
    """The request body, in the docs' parameter names. No temperature, ever."""
    body = {
        "model": model,
        "input": messages,
        "text": {"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
        "reasoning": {"effort": effort},
        "max_output_tokens": int(max_output_tokens),
        "store": False,
    }
    if service_tier:
        body["service_tier"] = service_tier
    if prompt_cache_key:
        body["prompt_cache_key"] = prompt_cache_key
    return body


class OpenAIClient:
    """Calls through the official SDK with the key read from .env at call time.

    http_client lets tests pass an httpx2.Client with a mock transport; nothing here opens a socket otherwise until a
    call is made.
    """

    def __init__(self, *, env_path: Path | None = None, settings: dict | None = None, http_client=None):
        self.settings = settings or api_settings()
        self.env_path = Path(env_path) if env_path else ROOT / self.settings.get("env_file", ".env")
        self._http_client = http_client
        self._owns_http = http_client is None
        self._lock = threading.Lock()

    # -- plumbing ----------------------------------------------------------------------------------------------------

    def _key(self) -> str:
        from dotenv import dotenv_values

        name = self.settings.get("key_name", "OPENAI_API_KEY")
        key = dotenv_values(self.env_path).get(name) if self.env_path.exists() else None
        if not key:
            raise ClientError(kind="missing_key", error_type="configuration")
        return key

    def _sdk(self):
        import openai

        with self._lock:
            if self._http_client is None:
                import httpx2
                self._http_client = httpx2.Client(timeout=None)
        return openai.OpenAI(api_key=self._key(), base_url=self.settings["base_url"], max_retries=0,
                             http_client=self._http_client)

    def close(self) -> None:
        if self._owns_http and self._http_client is not None:
            self._http_client.close()
            self._http_client = None

    def _guarded(self, stage: str, fn):
        """Run fn(sdk); turn any exception into a sanitized ClientError raised outside the except block."""
        import openai

        failure = None
        try:
            return fn(self._sdk())
        except ClientError as e:
            failure = dict(kind=e.kind, status=e.status, error_type=e.error_type, code=e.code,
                           request_id=e.request_id)
        except openai.APITimeoutError:
            failure = dict(kind="timeout")
        except openai.APIConnectionError:
            failure = dict(kind="connection")
        except openai.APIStatusError as e:
            failure = dict(kind=f"http_{e.status_code}", status=e.status_code, error_type=_error_field(e, "type"),
                           code=_error_field(e, "code"), request_id=e.request_id)
        except Exception as e:  # noqa: BLE001 - nothing from the SDK may escape with its request attached
            failure = dict(kind="exception", error_type=type(e).__name__)
        raise ClientError(stage=stage, **failure)

    # -- Responses API -----------------------------------------------------------------------------------------------

    def call(self, body: dict, *, timeout: float, on_raw=None) -> CallResult:
        """Send one request. `on_raw(text)` receives the raw body of an HTTP 200 before anything parses it.

        HTTP errors, timeouts and dropped connections come back as a CallResult; anything else raises ClientError.
        """
        import openai

        if "temperature" in body:
            raise ValueError("a temperature is never sent")
        start = time.monotonic()
        failure, raw_text, request_id, limits = None, None, None, None
        try:
            raw = self._sdk().responses.with_raw_response.create(**body, timeout=timeout)
            raw_text, request_id, limits = raw.text, raw.headers.get("x-request-id"), rate_limits(raw.headers)
        except ClientError:
            raise
        except openai.APITimeoutError:
            failure = CallResult(status="timeout")
        except openai.APIConnectionError:
            failure = CallResult(status="connection")
        except openai.APIStatusError as e:
            message = _error_field(e, "message") or ""
            failure = CallResult(status=f"http_{e.status_code}", request_id=_safe(e.request_id),
                                 error_type=_safe(_error_field(e, "type")), error_code=_safe(_error_field(e, "code")),
                                 resource_unavailable="resource unavailable" in str(message).lower(),
                                 rate_limits=rate_limits(e.response.headers))
            message = None
        except Exception as e:  # noqa: BLE001
            failure = ClientError(kind="exception", error_type=type(e).__name__)
        elapsed = time.monotonic() - start
        if isinstance(failure, ClientError):
            raise ClientError(kind=failure.kind, error_type=failure.error_type)
        if failure is not None:
            failure.elapsed_s = elapsed
            return failure
        if on_raw is not None:
            on_raw(raw_text)
        try:
            body_json = json.loads(raw_text)
        except json.JSONDecodeError:
            body_json = None
        result = parse_response(body_json)
        result.request_id, result.elapsed_s, result.rate_limits = _safe(request_id), elapsed, limits
        return result

    def count_input_tokens(self, body: dict) -> int:
        """The input-token counting endpoint (POST /responses/input_tokens). Used only behind a dry-run flag."""
        args = {k: body[k] for k in ("model", "input", "text", "reasoning") if k in body}

        def fn(sdk):
            raw = sdk.responses.input_tokens.with_raw_response.count(**args)
            return int(json.loads(raw.text)["input_tokens"])
        return self._guarded("token count", fn)

    # -- Batch API ---------------------------------------------------------------------------------------------------

    def submit_batch(self, lines: list[dict], *, metadata: dict) -> dict:
        """Upload a JSONL of requests and create a batch. Raises ClientError with stage 'upload' or 'create'."""
        data = "".join(json.dumps(line, sort_keys=True) + "\n" for line in lines).encode("utf-8")

        def upload(sdk):
            raw = sdk.files.with_raw_response.create(file=("chunk.jsonl", data, "application/jsonl"),
                                                     purpose="batch")
            return json.loads(raw.text)["id"]
        file_id = self._guarded("upload", upload)

        def create(sdk):
            raw = sdk.batches.with_raw_response.create(input_file_id=file_id, endpoint=BATCH_ENDPOINT,
                                                       completion_window="24h", metadata=metadata)
            return json.loads(raw.text)
        return self._guarded("create", create)

    def retrieve_batch(self, batch_id: str) -> dict:
        return self._guarded("batch status", lambda sdk: json.loads(
            sdk.batches.with_raw_response.retrieve(batch_id).text))

    def cancel_batch(self, batch_id: str) -> dict:
        return self._guarded("batch cancel", lambda sdk: json.loads(
            sdk.batches.with_raw_response.cancel(batch_id).text))

    def file_lines(self, file_id: str) -> list[dict]:
        text = self._guarded("file download", lambda sdk: sdk.files.with_raw_response.content(file_id).text)
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def find_batch(self, chunk_id: str) -> dict | None:
        """The batch whose metadata names this chunk, among the most recent 100, or None."""
        page = self._guarded("batch list", lambda sdk: json.loads(sdk.batches.with_raw_response.list(limit=100).text))
        for batch in page.get("data") or []:
            if (batch.get("metadata") or {}).get("chunk_id") == chunk_id:
                return batch
        return None


def _error_field(e, name: str):
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        inner = body.get("error") if isinstance(body.get("error"), dict) else body
        return inner.get(name)
    return getattr(e, name, None)
