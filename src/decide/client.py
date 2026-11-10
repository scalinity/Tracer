"""The decision-model client and its operations: OpenRouter's Decisions API through the spend ledger (spec §11.5).

    from src.decide import client as dc
    cfg = dc.load_config()
    d = dc.dry_run("X2", "val", items, cfg, totals)                 # prices every request; no connection
    job = dc.Job("X2", cfg, ledger, client_factory=lambda: dc.DecisionClient(cfg), budget_usd="1.00")
    result = job.run(items)                                         # cached answers, then live calls

The request is `{model, state, questions}` and the response `{id, model, provider, answers, usage}`, as the API
reference gives them (an alpha API: "the shape may change"). `parse_response` checks every response against that
pinned contract (CONTRACT, with a recorded fixture in tests/fixtures/); a mismatch stops the job, and its items fall
back to the rules. Question types: `noul` answers a probability; `choice` the chosen option, a probability for each
option and a confidence; `score` an expected level, a probability per level, a legend and a confidence; any question
can come back as a `refusal`, which falls back to the rules for that question alone.

Money. Nothing is sent without a reservation in the ledger's `decide` account under the job's cap (and the run's
`--budget-usd`): the larger of the request's input bound (every UTF-8 byte of the serialized request plus framing) and
its estimated input tokens times 1.5, at the model's standard price ($0.12 per million for Drex Max, not the
discounted $0.06; output is free). An answer settles from the response's `usage.cost` (OpenRouter's credits, one per
dollar), or from its tokens at the standard price when it reports none. Outcomes follow the ledger's one classifier
(src/scenarios/runner.py `billing_outcome`): any 429 and a 4xx raised before the request ran are void; everything
else that brings no usage (a 5xx, a timeout, a dropped connection, a 402) keeps its whole reservation as spent.

Operations. 429, 503 and 529 back off exponentially with jitter, up to six tries in all, each try under its own
reservation; then the item falls back to the rules. A 400 is logged (status and error code only) and the item falls
back. A 402 (no credits) stops the job, which is Daniel's to resolve. Half of the last ten attempts ending with an
unknown outcome, a settlement above its reservation, or a contract mismatch stops the job too. Concurrency starts at 8,
halves on each 429 and grows back by one after every 20 answers. A stopped job sends nothing more; requests already
in flight finish and are settled.

Versions. Every response's resolved model is recorded. The first answer pins it in the job's state file; a later
answer from another version stops the job, both versions are recorded, and the job's next run starts from scratch on
the new version (answers are cached per resolved version, so the old version's never mix in). The fallback model
(`typesafe/jev-1.14`) is used only when Drex Max gives no answer to a job's first request, before anything else is
sent, and the switch is recorded.

Cache. Every answered body is written to data/decide/<resolved model>/<sha256 of state and questions>.json before it
is parsed; a request whose answer is stored is never sent again, so reruns are free.

Secrets. The key is read from .env with python-dotenv inside the client at the moment of each call (only its name
lives in config/decision_model.yaml). Errors carry the HTTP status and a sanitized error code, never a message from
the server, a header or the key; nothing here logs a request.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import re
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

import yaml

from src.scenarios import ledger as ledger_mod
from src.scenarios import prices
from src.scenarios.runner import PRE_EXECUTION_4XX, billing_outcome

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "decision_model.yaml"
JOBS_PATH = ROOT / "config" / "openai_jobs.yaml"
DECIDE_DIR = ROOT / "data" / "decide"
RESULTS_DIR = ROOT / "results" / "neural" / "decisions"
CONTRACT = "decisions-alpha-2026-11-09"
JOBS = ("X1", "X2", "X3", "X4")
DRYRUN_MAX_AGE = timedelta(hours=24)
SAFE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
SAFE_MODEL = re.compile(r"^[A-Za-z0-9_.\-]+(/[A-Za-z0-9_.\-]+)?$")
GROW_AFTER = 20          # answers in a row before concurrency grows by one
PROB_SLACK = 0.02        # a choice's or score's probabilities may sum to 1 within this

log = logging.getLogger(__name__)


class DecisionError(RuntimeError):
    """A call failed in a way no result describes. Carries no text from the server."""

    def __init__(self, kind: str, status: int | None = None, code: str | None = None):
        self.kind, self.status, self.code = kind, status, _safe(code)
        super().__init__(f"decision call failed: {kind} (status {status}, code {self.code})")


class ContractError(DecisionError):
    """A response does not have the pinned shape."""

    def __init__(self, what: str):
        self.what = what
        super().__init__("contract")
        self.args = (f"the response does not match the pinned contract {CONTRACT}: {what}",)


class JobError(RuntimeError):
    """A decision job cannot start."""


def _safe(value) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if SAFE.match(text) else "redacted"


# --- configuration ---------------------------------------------------------------------------------------------------

def load_config(path: Path = CONFIG_PATH) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def check_caps(cfg: dict, jobs_path: Path = JOBS_PATH) -> None:
    """The caps here must be the ledger's (config/openai_jobs.yaml); a disagreement refuses every job."""
    jobs = ledger_mod.load_jobs(jobs_path)
    caps = cfg["caps"]
    account = jobs["accounts"].get(caps["account"])
    if account is None or Decimal(str(account["cap_usd"])) != Decimal(str(caps["account_usd"])):
        raise JobError(f"the {caps['account']} account cap differs between config/decision_model.yaml and "
                       "config/openai_jobs.yaml")
    for job, cap in caps["jobs"].items():
        j = jobs["jobs"].get(job)
        if j is None or j["account"] != caps["account"] or Decimal(str(j["cap_usd"])) != Decimal(str(cap)):
            raise JobError(f"job {job}'s cap differs between config/decision_model.yaml and config/openai_jobs.yaml")


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def config_hash(cfg: dict) -> str:
    """The decision-model configuration a test look records (spec §8.7): what decides an answer and how it becomes a
    decision. Prices, their dates and operational settings are left out, so re-checking a price never breaks the
    lock. The question wordings join through `prompt_version` and the question hashes the callers add."""
    keep = {k: cfg.get(k) for k in ("provider", "path", "model", "resolved_prefix", "fallback", "contract",
                                    "decisions", "counterfactual", "budgets", "samples")}
    return sha256_text(canonical(keep))


def lock_hash(cfg: dict) -> str:
    """The decision-model configuration hash a test look adds to the lock (spec §8.7): config_hash and the exact
    wording of X2's and X3's questions."""
    from src.decide import render

    return sha256_text(canonical({"config": config_hash(cfg), "x2": render.x2_questions(),
                                  "x3": render.x3_questions(0)}))


def model_names(cfg: dict) -> dict:
    return {"primary": cfg["model"], "fallback": cfg["fallback"]["model"]}


def prefix_of(cfg: dict, model: str) -> str:
    return cfg["fallback"]["resolved_prefix"] if model == cfg["fallback"]["model"] else cfg["resolved_prefix"]


def rates(cfg: dict, model: str) -> dict[str, Decimal]:
    row = (cfg["price"]["models"] or {}).get(model)
    if not isinstance(row, dict):
        raise JobError(f"config/decision_model.yaml has no price for {model}")
    out = {}
    for k in ("listed_input", "standard_input", "output"):
        v = row.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            raise JobError(f"the {k} price of {model} is {v!r}, not a rate")
        out[k] = Decimal(str(v))
    return out


def check_price(cfg: dict, now: datetime | None = None) -> None:
    """A spending job's price check (Appendix A item 4): verified within 24 hours, dated with a time zone and not
    ahead of now."""
    raw = cfg["price"].get("verified_at")
    if raw is None:
        raise JobError("the decision-model price has never been verified; check OpenRouter's model page and set "
                       "price.verified_at in config/decision_model.yaml (spec Appendix A item 4)")
    try:
        prices.check_fresh({"verified_at": raw}, now)
    except prices.PriceError as e:
        raise JobError(f"decision-model price: {e}") from None


def key_present(cfg: dict, env_path: Path | None = None) -> bool:
    """Whether .env names the key (its value is read and dropped; never printed)."""
    from dotenv import dotenv_values

    env = Path(env_path) if env_path else ROOT / cfg.get("env_file", ".env")
    return env.exists() and bool(dotenv_values(env).get(cfg["key_name"]))


# --- requests and money ----------------------------------------------------------------------------------------------

def request_hash(state, questions: dict) -> str:
    """sha256 of the state and the questions: the cache key, independent of the model asked."""
    return sha256_text(canonical({"questions": questions, "state": state}))


def build_body(model: str, state, questions: dict) -> dict:
    if not questions:
        raise ValueError("a decision request needs at least one question")
    for name, q in questions.items():
        if q.get("type") not in ("noul", "choice", "score"):
            raise ValueError(f"question {name} has type {q.get('type')!r}; the jobs use noul, choice and score")
        if q["type"] == "choice" and "other" not in (q.get("criteria") or {}):
            raise ValueError(f"choice question {name} must offer `other` (spec §11.4)")
    return {"model": model, "state": state, "questions": questions}


def input_bound(body: dict, cfg: dict) -> int:
    """An upper bound on billed input tokens: the UTF-8 bytes of the serialized request (a byte-level tokenizer
    cannot exceed them) plus framing per request and per question (source: assumed)."""
    r = cfg["reservation"]
    return (len(canonical(body).encode("utf-8")) + int(r["framing_per_request"])
            + int(r["framing_per_question"]) * len(body["questions"]))


def estimate_tokens(body: dict, cfg: dict) -> int:
    text = canonical(body["state"]) + canonical(body["questions"])
    return math.ceil(len(text) / float(cfg["reservation"]["chars_per_token"]))


def _micros(amount: Decimal) -> int:
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


def reservation_micros(body: dict, cfg: dict) -> int:
    """The worst case one call can cost: max(input bound, estimate x 1.5) tokens at the standard input price."""
    tokens = max(input_bound(body, cfg),
                 math.ceil(estimate_tokens(body, cfg) * float(cfg["reservation"]["input_multiplier"])))
    return max(1, _micros(Decimal(tokens) * rates(cfg, body["model"])["standard_input"]))


def expected_micros(body: dict, cfg: dict) -> int:
    """The local estimate at the price listed today: what the dry run expects a call to cost."""
    r = rates(cfg, body["model"])
    return _micros(Decimal(estimate_tokens(body, cfg)) * r["listed_input"])


def usage_micros(usage: dict, cfg: dict, model: str) -> tuple[int, str]:
    """A response's cost: its `usage.cost` (credits, one per dollar) when it reports one, else its tokens at the
    standard price. Returns (micros, the source used)."""
    cost = usage.get("cost") if isinstance(usage, dict) else None
    if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
        return _micros(Decimal(str(cost)) * 1_000_000), "usage.cost"
    r = rates(cfg, model)
    inp, out = (usage or {}).get("input_tokens"), (usage or {}).get("output_tokens")
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (inp, out)):
        raise ContractError("usage lacks input_tokens or output_tokens")
    return _micros(Decimal(inp) * r["standard_input"] + Decimal(out) * r["output"]), "tokens at the standard price"


# --- the response contract -------------------------------------------------------------------------------------------

def _prob(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1:
        raise ContractError(f"{what} is {v!r}, not a probability")
    return float(v)


def parse_answer(name: str, question: dict, a) -> dict:
    """One answer, checked against its question: {"type", ...} normalized, or {"type": "refusal"}."""
    if not isinstance(a, dict) or "type" not in a:
        raise ContractError(f"answer {name} is not an object with a type")
    if a["type"] == "refusal":
        return {"type": "refusal"}
    kind = question["type"]
    if a["type"] != kind:
        raise ContractError(f"answer {name} has type {a['type']!r}, the question {kind!r}")
    conf = a.get("confidence")
    if conf is not None:
        conf = _prob(conf, f"{name}.confidence")
    if kind == "noul":
        return {"type": "noul", "p": _prob(a.get("noul"), f"{name}.noul")}
    probs = a.get("probabilities")
    if not isinstance(probs, dict) or not probs:
        raise ContractError(f"answer {name} has no probabilities")
    probs = {str(k): _prob(v, f"{name}.probabilities[{k}]") for k, v in probs.items()}
    if abs(sum(probs.values()) - 1) > PROB_SLACK:
        raise ContractError(f"answer {name}'s probabilities sum to {sum(probs.values()):.3f}")
    if kind == "choice":
        options = set(question["criteria"])
        if not set(probs) <= options or a.get("choice") not in options:
            raise ContractError(f"answer {name} names an option the question does not offer")
        return {"type": "choice", "choice": a["choice"], "probabilities": {k: probs.get(k, 0.0) for k in sorted(options)},
                "confidence": conf}
    levels = {str(i) for i in range(len(question["criteria"]))}
    score = a.get("score")
    if not set(probs) <= levels or isinstance(score, bool) or not isinstance(score, (int, float)) \
            or not 0 <= score <= len(levels) - 1 + 1e-9:
        raise ContractError(f"answer {name} has levels or a score outside the question's {len(levels)} levels")
    return {"type": "score", "score": float(score), "probabilities": {k: probs.get(k, 0.0) for k in sorted(levels, key=int)},
            "confidence": conf}


def parse_response(body, questions: dict, prefix: str) -> dict:
    """The answers of a response body, every question answered or refused, and the resolved model, provider, id and
    usage. Raises ContractError on any other shape."""
    if not isinstance(body, dict):
        raise ContractError("the body is not a JSON object")
    model = body.get("model")
    if not isinstance(model, str) or not SAFE_MODEL.match(model) or not model.startswith(prefix):
        raise ContractError(f"the resolved model {model!r} does not start with {prefix!r}")
    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise ContractError("no answers object")
    missing = sorted(set(questions) - set(answers))
    if missing:
        raise ContractError(f"no answer for {missing}")
    usage = body.get("usage")
    if not isinstance(usage, dict):
        raise ContractError("no usage object")
    parsed = {name: parse_answer(name, q, answers[name]) for name, q in questions.items()}
    return {"id": _safe(body.get("id")), "model": model, "provider": _safe(body.get("provider")),
            "answers": parsed, "refusals": sorted(n for n, a in parsed.items() if a["type"] == "refusal"),
            "usage": {k: usage.get(k) for k in ("input_tokens", "output_tokens", "cost")}}


# --- HTTP ------------------------------------------------------------------------------------------------------------

@dataclass
class CallResult:
    """One call. `status` is "ok" (HTTP 200), "http_<code>", "timeout" or "connection"."""
    status: str
    http_status: int | None = None
    body: dict | None = None
    raw: str | None = None
    usage: dict | None = None          # an error body's token counts, kept for billing only
    tokens_consumed: bool = False
    error_code: str | None = None
    elapsed_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class DecisionClient:
    """POST {base_url}{path} with the key from .env, read at each call. `transport` lets tests pass an httpx2
    MockTransport; nothing opens a socket until a call is made."""

    def __init__(self, cfg: dict, *, env_path: Path | None = None, transport=None):
        self.cfg = cfg
        self.env_path = Path(env_path) if env_path else ROOT / cfg.get("env_file", ".env")
        self._transport = transport
        self._http = None
        self._lock = threading.Lock()

    def _key(self) -> str:
        from dotenv import dotenv_values

        key = dotenv_values(self.env_path).get(self.cfg["key_name"]) if self.env_path.exists() else None
        if not key:
            raise DecisionError("missing_key")
        return key

    def _client(self):
        import httpx2

        with self._lock:
            if self._http is None:
                self._http = httpx2.Client(transport=self._transport, timeout=float(self.cfg.get("timeout_s", 60)))
            return self._http

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def call(self, body: dict) -> CallResult:
        """Send one request. HTTP errors, timeouts and dropped connections come back as a CallResult; anything else
        raises DecisionError outside the except block, so no exception chain carries the request."""
        import httpx2

        url = self.cfg["base_url"].rstrip("/") + self.cfg["path"]
        payload = canonical(body).encode("utf-8")
        start = time.monotonic()
        failure, response = None, None
        try:
            response = self._client().post(url, content=payload, headers={
                "Authorization": f"Bearer {self._key()}", "Content-Type": "application/json"})
        except DecisionError as e:
            failure = DecisionError(e.kind)
        except httpx2.TimeoutException:
            return CallResult(status="timeout", elapsed_s=time.monotonic() - start)
        except httpx2.TransportError:
            return CallResult(status="connection", elapsed_s=time.monotonic() - start)
        except Exception as e:  # noqa: BLE001 - nothing may escape with the request attached
            failure = DecisionError("exception", code=type(e).__name__)
        if failure is not None:
            raise failure
        elapsed = time.monotonic() - start
        text = response.text
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if response.status_code == 200:
            return CallResult(status="ok", http_status=200, body=parsed if isinstance(parsed, dict) else None,
                              raw=text, elapsed_s=elapsed)
        usage, consumed = _error_usage(parsed)
        err = parsed.get("error") if isinstance(parsed, dict) else None
        code = err.get("code") if isinstance(err, dict) else None
        return CallResult(status=f"http_{response.status_code}", http_status=response.status_code, usage=usage,
                          tokens_consumed=consumed, error_code=_safe(code), elapsed_s=elapsed)


def _error_usage(parsed) -> tuple[dict | None, bool]:
    """An error body's usage, as token counts and a cost only (never text), and whether it reports tokens used."""
    if not isinstance(parsed, dict):
        return None, False
    for holder in (parsed, parsed.get("error")):
        u = holder.get("usage") if isinstance(holder, dict) else None
        if isinstance(u, dict):
            keep = {k: u[k] for k in ("input_tokens", "output_tokens", "cost")
                    if isinstance(u.get(k), (int, float)) and not isinstance(u.get(k), bool) and u[k] >= 0}
            if "input_tokens" in keep or "cost" in keep:
                return keep, any(keep.get(k, 0) > 0 for k in ("input_tokens", "output_tokens", "cost"))
    return None, False


# --- the cache and the job's state ----------------------------------------------------------------------------------

def cache_path(decide_dir: Path, resolved: str, h: str) -> Path:
    if not SAFE_MODEL.match(resolved) or ".." in resolved:
        raise ContractError(f"resolved model {resolved!r} is not a safe folder name")
    return Path(decide_dir) / resolved / f"{h}.json"


def write_atomic(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def write_cache(decide_dir: Path, resolved: str, h: str, raw: str) -> Path:
    """Store an answered body; the first copy is never replaced."""
    path = cache_path(decide_dir, resolved, h)
    if path.exists():
        path = path.with_name(f"{h}.extra-{uuid.uuid4().hex[:8]}.json")
    write_atomic(path, raw)
    return path


def state_path(decide_dir: Path, job: str) -> Path:
    return Path(decide_dir) / f"state-{job}.json"


def read_state(decide_dir: Path, job: str) -> dict:
    p = state_path(decide_dir, job)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"model": None, "resolved_model": None,
                                                                          "versions": [], "events": []}


def save_state(decide_dir: Path, job: str, state: dict) -> None:
    write_atomic(state_path(decide_dir, job), json.dumps(state, indent=1, sort_keys=True) + "\n")


# --- items, dry run --------------------------------------------------------------------------------------------------

@dataclass
class Item:
    item_id: str
    state: object
    questions: dict
    meta: dict = field(default_factory=dict)        # kept with the outputs, never sent

    @property
    def request_hash(self) -> str:
        return request_hash(self.state, self.questions)


def request_set_hash(job: str, set_name: str, items) -> str:
    rows = sorted((i.item_id, i.request_hash) for i in items)
    return sha256_text(canonical({"job": job, "set": set_name, "requests": rows}))


def dryrun_path(decide_dir: Path, job: str, set_name: str) -> Path:
    return Path(decide_dir) / f"dryrun-{job}-{set_name}.json"


def dry_run(job: str, set_name: str, items, cfg: dict, totals: dict | None, *, decide_dir: Path = DECIDE_DIR,
            now: datetime | None = None) -> dict:
    """Price every request of a job without sending one or opening a connection: requests, estimated input tokens,
    the input bound, the expected cost at the price listed today and the worst case (what a call reserves), with
    answers already cached counted as free, beside the job's cap and the account's headroom."""
    now = now or datetime.now().astimezone()
    state = read_state(decide_dir, job)
    model = state.get("model") or cfg["model"]
    pinned = state.get("resolved_model")
    est, bound, expected, worst, cached = [], [], 0, 0, 0
    for it in items:
        body = build_body(model, it.state, it.questions)
        if pinned and cache_path(decide_dir, pinned, it.request_hash).exists():
            cached += 1
            continue
        est.append(estimate_tokens(body, cfg))
        bound.append(input_bound(body, cfg))
        expected += expected_micros(body, cfg)
        worst += reservation_micros(body, cfg)
    cap = ledger_mod.to_micros(cfg["caps"]["jobs"][job])
    jt = (totals or {}).get("jobs", {}).get(job) or {}
    at = (totals or {}).get("accounts", {}).get(cfg["caps"]["account"]) or {}
    raw = cfg["price"].get("verified_at")
    return {"job": job, "set": set_name, "created_at": now.isoformat(timespec="seconds"), "model": model,
            "pinned_resolved_model": pinned, "requests": len(items), "cached": cached, "to_send": len(est),
            "request_set_hash": request_set_hash(job, set_name, items),
            "est_input_tokens": {"median": sorted(est)[len(est) // 2] if est else 0, "max": max(est, default=0),
                                 "total": sum(est)},
            "input_bound_tokens": {"max": max(bound, default=0), "total": sum(bound)},
            "expected_micros": expected, "worst_micros": worst, "job_cap_micros": cap,
            "job_spent_micros": jt.get("spent", 0), "job_held_micros": jt.get("held", 0),
            "account_headroom_micros": at.get("headroom"), "price_verified_at": raw,
            "prices": {k: str(v) for k, v in rates(cfg, model).items()}, "contract": CONTRACT,
            "prompt_version": cfg["decisions"]["prompt_version"], "config_hash": config_hash(cfg)}


def format_dry_run(d: dict) -> str:
    usd = lambda m: "unknown" if m is None else f"${m / 1e6:,.4f}"  # noqa: E731
    lines = [f"Dry run for {d['job']} on {d['set']}: {d['requests']:,} requests ({d['cached']:,} already answered in "
             f"the cache, {d['to_send']:,} to send) with {d['model']}; nothing sent, nothing spent, no connection.",
             f"Estimated input per request: median {d['est_input_tokens']['median']:,} tokens, most "
             f"{d['est_input_tokens']['max']:,}; input bound at most {d['input_bound_tokens']['max']:,}.",
             f"Expected {usd(d['expected_micros'])} at the listed price (${d['prices']['listed_input']} per million "
             f"input tokens); worst case reserved {usd(d['worst_micros'])} at the standard price "
             f"(${d['prices']['standard_input']} per million), one call at a time.",
             f"Job cap {usd(d['job_cap_micros'])}; spent {usd(d['job_spent_micros'])}, held {usd(d['job_held_micros'])};"
             f" decide account headroom {usd(d['account_headroom_micros'])}.",
             f"Price verified at {d['price_verified_at'] or 'never (a spending run refuses until it is)'}.",
             f"Request-set hash {d['request_set_hash']}."]
    if d["expected_micros"] > d["job_cap_micros"]:
        lines.append("WARNING: the expected cost passes the job cap; the job stops when the cap is reached.")
    return "\n".join(lines)


def check_dry_run(path: Path, set_hash: str, now: datetime | None = None) -> dict:
    """A spending run's check: a dry run of the same request set, under 24 hours old, dated with a time zone and not
    more than five minutes ahead of now."""
    path = Path(path)
    if not path.exists():
        raise JobError(f"no dry run at {path}; run the job with --dry-run first")
    d = json.loads(path.read_text(encoding="utf-8"))
    now = now or datetime.now().astimezone()
    try:
        made = datetime.fromisoformat(str(d.get("created_at")))
    except ValueError:
        raise JobError("the dry run has no readable created_at; run it again") from None
    if made.tzinfo is None or made - now > prices.FUTURE_SKEW or now - made > DRYRUN_MAX_AGE:
        raise JobError(f"the dry run at {path} is stale, undated or ahead of now ({d.get('created_at')}); run it again")
    if d.get("request_set_hash") != set_hash:
        raise JobError(f"the requests changed since the dry run ({d.get('request_set_hash')} there, {set_hash} now); "
                       "run the dry run again")
    return d


# --- the job ---------------------------------------------------------------------------------------------------------

@dataclass
class Outcome:
    item_id: str
    request_hash: str
    status: str                       # answered, cached or fallback
    reason: str | None = None         # why an item fell back to the rules
    answers: dict | None = None
    refusals: list = field(default_factory=list)
    resolved_model: str | None = None
    attempts: int = 0
    cost_micros: int = 0

    def record(self) -> dict:
        return asdict(self)


class _Gate:
    """A concurrency limit that halves on a 429 and grows back by one after GROW_AFTER answers."""

    def __init__(self, start: int, low: int):
        self.limit, self.low, self.top = start, low, start
        self.busy, self.streak = 0, 0
        self.cond = threading.Condition()

    def __enter__(self):
        with self.cond:
            while self.busy >= self.limit:
                self.cond.wait()
            self.busy += 1

    def __exit__(self, *exc):
        with self.cond:
            self.busy -= 1
            self.cond.notify_all()

    def throttled(self) -> None:
        with self.cond:
            self.limit, self.streak = max(self.low, self.limit // 2), 0

    def answered(self) -> None:
        with self.cond:
            self.streak += 1
            if self.streak >= GROW_AFTER and self.limit < self.top:
                self.limit, self.streak = self.limit + 1, 0
                self.cond.notify_all()


class _Unavailable(Exception):
    """The model gave no answer to the job's first request: the fallback model may be tried."""


@dataclass
class JobResult:
    job: str
    outcomes: list
    stopped: str | None
    model: str
    resolved_model: str | None
    versions: list
    sent: int
    cached: int
    spent_micros: int


class Job:
    """One decision job over a list of Items, through the ledger, the cache and the client."""

    def __init__(self, job: str, cfg: dict, ledger: ledger_mod.Ledger, *, client_factory, budget_usd,
                 decide_dir: Path = DECIDE_DIR, sleep=time.sleep, rng: random.Random | None = None,
                 concurrency: int | None = None, out=print, now=None):
        if job not in JOBS:
            raise JobError(f"unknown decision job {job}")
        self.job, self.cfg, self.ledger = job, cfg, ledger
        self.client_factory, self.decide_dir = client_factory, Path(decide_dir)
        self.budget = Decimal(str(budget_usd))
        self.sleep, self.rng, self.out = sleep, rng or random.Random(0), out
        self.now = now or (lambda: datetime.now().astimezone())
        ops = cfg["operations"]
        self.tries, self.base = int(ops["tries"]), float(ops["backoff_base_s"])
        self.retry = set(ops["retry_statuses"])
        self.gate = _Gate(int(concurrency or ops["concurrency_start"]), int(ops["concurrency_min"]))
        self.recent: deque = deque(maxlen=int(ops["unknown_window"]))
        self.unknown_stop = int(ops["unknown_stop"])
        self.state = read_state(self.decide_dir, job)
        self.model = self.state.get("model") or cfg["model"]
        self._lock = threading.Lock()
        self.stopped: str | None = None
        self.version_changed = False
        self.sent = 0
        self._client = None

    # -- shared -----------------------------------------------------------------------------------------------------

    def stop(self, reason: str) -> None:
        with self._lock:
            if self.stopped is None:
                self.stopped = reason
                self.out(f"{self.job}: stopping: {reason}")

    def client(self):
        with self._lock:
            if self._client is None:
                self._client = self.client_factory()
            return self._client

    def _note(self, unknown: bool) -> None:
        with self._lock:
            self.recent.append(unknown)
            bad = sum(self.recent)
        if bad >= self.unknown_stop:
            self.stop(f"{bad} of the last {len(self.recent)} attempts ended with an unknown outcome")

    def _event(self, kind: str, **detail) -> None:
        with self._lock:
            self.state.setdefault("events", []).append({"at": self.now().isoformat(timespec="seconds"), "kind": kind,
                                                        **detail})
            save_state(self.decide_dir, self.job, self.state)

    def _pin(self, resolved: str) -> bool:
        """Pin the first resolved version; False (and the job stops) when a later answer comes from another one."""
        with self._lock:
            if self.version_changed:      # answers still in flight after a change are not pinned or recorded
                return False
            pinned = self.state.get("resolved_model")
            if pinned is None:
                self.state.update(model=self.model, resolved_model=resolved)
                self.state.setdefault("versions", []).append(
                    {"resolved_model": resolved, "first_seen": self.now().isoformat(timespec="seconds")})
                save_state(self.decide_dir, self.job, self.state)
                return True
            if pinned == resolved:
                return True
            self.state.setdefault("versions", []).append(
                {"resolved_model": resolved, "first_seen": self.now().isoformat(timespec="seconds"),
                 "replaces": pinned})
            self.state["resolved_model"] = resolved
            self.version_changed = True
            self.state.setdefault("events", []).append(
                {"at": self.now().isoformat(timespec="seconds"), "kind": "version_changed", "from": pinned,
                 "to": resolved, "note": "the job stopped; its next run starts from scratch on the new version"})
            save_state(self.decide_dir, self.job, self.state)
        self.stop(f"the resolved model changed from {pinned} to {resolved}; rerun the job from scratch")
        return False

    # -- one item ---------------------------------------------------------------------------------------------------

    def _cached(self, it: Item) -> Outcome | None:
        pinned = self.state.get("resolved_model")
        if not pinned:
            return None
        path = cache_path(self.decide_dir, pinned, it.request_hash)
        if not path.exists():
            return None
        parsed = parse_response(json.loads(path.read_text(encoding="utf-8")), it.questions, prefix_of(self.cfg, self.model))
        return Outcome(it.item_id, it.request_hash, "cached", answers=parsed["answers"], refusals=parsed["refusals"],
                       resolved_model=parsed["model"])

    def _reserve(self, it: Item, body: dict, attempt: int) -> str | None:
        try:
            return self.ledger.reserve(self.job, it.item_id, Decimal(reservation_micros(body, self.cfg)) / 1_000_000,
                                       ceiling_usd=self.budget,
                                       detail={"model": body["model"], "request_hash": it.request_hash,
                                               "input_bound": input_bound(body, self.cfg),
                                               "est_input_tokens": estimate_tokens(body, self.cfg), "attempt": attempt})
        except ledger_mod.CapReached as e:
            self.stop(f"the {e.scope} cap was reached: {e}")
            return None

    def _settle_failure(self, entry: str, r: CallResult) -> str:
        kind = billing_outcome(status=r.http_status, kind=r.status, usage=r.usage, tokens_consumed=r.tokens_consumed)
        if kind == "void":
            self.ledger.void(entry, f"{r.status}: not billed")
        elif kind == "usage":
            try:
                micros, src = usage_micros(r.usage, self.cfg, self.model)
                self.ledger.settle(entry, Decimal(micros) / 1_000_000, r.usage, None, "decisions",
                                   detail={"cost_source": src, "status": r.status})
            except ContractError:
                self.ledger.settle_unknown(entry, f"{r.status} with unpriceable usage")
                kind = "unknown"
        else:
            self.ledger.settle_unknown(entry, f"{r.status}: outcome unknown, kept as spent")
        return kind

    def _send(self, it: Item, probe: bool = False) -> Outcome:
        body = build_body(self.model, it.state, it.questions)
        out = Outcome(it.item_id, it.request_hash, "fallback")
        for attempt in range(1, self.tries + 1):
            if self.stopped:
                out.reason = f"job stopped: {self.stopped}"
                return out
            entry = self._reserve(it, body, attempt)
            if entry is None:
                out.reason = f"job stopped: {self.stopped}"
                return out
            out.attempts = attempt
            try:
                with self.gate:
                    with self._lock:
                        self.sent += 1
                    r = self.client().call(body)
            except DecisionError as e:          # raised before or while sending, with nothing to show for it
                if e.kind == "missing_key":
                    self.ledger.void(entry, "no key in .env; nothing was sent")
                else:
                    self.ledger.settle_unknown(entry, f"client error {e.kind}; kept as spent")
                self.stop(f"the client failed: {e.kind}")
                out.reason = e.kind
                return out
            if r.ok:
                return self._answered(it, entry, r, out)
            kind = self._settle_failure(entry, r)
            code = r.http_status
            self._note(kind == "unknown" and code not in self.retry)
            if code == 402:
                self.stop("402: the OpenRouter account has no credits (Daniel resolves this)")
                out.reason = "402"
                return out
            if code in (401, 403):
                self.stop(f"{code}: the key was refused")
                out.reason = str(code)
                return out
            if code in self.retry:
                if code == 429:
                    self.gate.throttled()
                if attempt < self.tries:
                    self.sleep(self.base * 2 ** (attempt - 1) * self.rng.uniform(0.75, 1.25))
                    continue
                if probe:
                    raise _Unavailable(f"{r.status} after {self.tries} tries")
                out.reason = f"{r.status} after {self.tries} tries"
                return out
            if probe and (code == 404 or r.status in ("timeout", "connection") or (code or 0) >= 500):
                raise _Unavailable(r.status)
            if code == 400:
                log.warning("decision request %s refused with 400 (code %s); the item falls back to the rules",
                            it.item_id, r.error_code)
            out.reason = r.status if code in PRE_EXECUTION_4XX or code is None else f"{r.status} (unknown outcome)"
            return out
        return out

    def _answered(self, it: Item, entry: str, r: CallResult, out: Outcome) -> Outcome:
        resolved = (r.body or {}).get("model") if isinstance(r.body, dict) else None
        if isinstance(resolved, str) and SAFE_MODEL.match(resolved) and ".." not in resolved:
            write_cache(self.decide_dir, resolved, it.request_hash, r.raw or "")
        try:
            usage = (r.body or {}).get("usage")
            micros, src = usage_micros(usage, self.cfg, self.model)
            settled = self.ledger.settle(entry, Decimal(micros) / 1_000_000, usage, _safe((r.body or {}).get("id")),
                                         "decisions", detail={"model": resolved, "cost_source": src})
            out.cost_micros = micros
            if settled["over"]:
                self.stop(f"a settlement passed its reservation ({it.item_id})")
        except ContractError as e:
            self.ledger.settle_unknown(entry, "an answer whose usage cannot be priced")
            self.stop(str(e))
            out.reason = "contract"
            return out
        self._note(False)
        try:
            parsed = parse_response(r.body, it.questions, prefix_of(self.cfg, self.model))
        except ContractError as e:
            self.stop(str(e))
            out.reason = "contract"
            return out
        if not self._pin(parsed["model"]):
            out.reason = "the resolved model changed"
            return out
        self.gate.answered()
        out.status, out.answers, out.refusals, out.resolved_model = "answered", parsed["answers"], parsed["refusals"], \
            parsed["model"]
        return out

    # -- the whole job ----------------------------------------------------------------------------------------------

    def run(self, items: list[Item]) -> JobResult:
        outcomes: list = [None] * len(items)
        todo = []
        for i, it in enumerate(items):
            hit = self._cached(it)
            if hit is not None:
                outcomes[i] = hit
            else:
                todo.append(i)
        cached = len(items) - len(todo)
        if todo and self.state.get("resolved_model") is None:     # the first request decides the model
            first = todo.pop(0)
            outcomes[first] = self._probe(items[first])
        try:
            if todo and not self.stopped:
                with ThreadPoolExecutor(max_workers=self.gate.top) as pool:
                    for i, res in zip(todo, pool.map(lambda k: self._send(items[k]), todo)):
                        outcomes[i] = res
            for i in todo:
                if outcomes[i] is None:
                    outcomes[i] = Outcome(items[i].item_id, items[i].request_hash, "fallback",
                                          reason=f"job stopped: {self.stopped}")
        finally:
            if self._client is not None:
                getattr(self._client, "close", lambda: None)()
        spent = sum(o.cost_micros for o in outcomes)
        return JobResult(self.job, outcomes, self.stopped, self.model, self.state.get("resolved_model"),
                         list(self.state.get("versions") or []), self.sent, cached, spent)

    def _probe(self, it: Item) -> Outcome:
        try:
            return self._send(it, probe=True)
        except _Unavailable as e:
            if self.model != self.cfg["model"]:
                self.stop(f"the fallback model {self.model} is unavailable too ({e})")
                return Outcome(it.item_id, it.request_hash, "fallback", reason=str(e))
            self._event("fallback_model", primary=self.model, fallback=self.cfg["fallback"]["model"], why=str(e))
            self.out(f"{self.job}: {self.model} gave no answer to the first request ({e}); using the fallback "
                     f"{self.cfg['fallback']['model']}, recorded in the job's state")
            self.model = self.cfg["fallback"]["model"]
            with self._lock:
                self.state["model"] = self.model
                save_state(self.decide_dir, self.job, self.state)
            try:
                return self._send(it, probe=True)
            except _Unavailable as e2:
                self.stop(f"the fallback model is unavailable too ({e2})")
                return Outcome(it.item_id, it.request_hash, "fallback", reason=str(e2))


# --- the steps every job takes: freeze its items, price them, send them, write what came back ------------------------

NOTICE = ("Synthetic data: laboratory weeks designed by language models and simulated by this project's code, with no "
          "clinical review. Decision-model answers are zero-shot and their calibration on this task is measured, "
          "never assumed.")


def items_path(decide_dir: Path, job: str, set_name: str) -> Path:
    return Path(decide_dir) / f"items-{job}-{set_name}.jsonl.gz"


def outputs_path(decide_dir: Path, job: str, set_name: str) -> Path:
    return Path(decide_dir) / "outputs" / f"{job}-{set_name}.jsonl"


def summary_path(results_dir: Path, job: str, set_name: str) -> Path:
    return Path(results_dir) / f"{job}-{set_name}.json"


def save_items(path: Path, items: list[Item]) -> None:
    import gzip

    text = "".join(canonical({"item_id": i.item_id, "meta": i.meta, "questions": i.questions, "state": i.state})
                   + "\n" for i in items)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(gzip.compress(text.encode("utf-8"), mtime=0))
    os.replace(tmp, path)


def load_items(path: Path) -> list[Item]:
    import gzip

    path = Path(path)
    if not path.exists():
        raise JobError(f"no frozen items at {path}; run the job with --dry-run first")
    rows = [json.loads(line) for line in gzip.decompress(path.read_bytes()).decode("utf-8").splitlines() if line]
    return [Item(r["item_id"], r["state"], r["questions"], r.get("meta") or {}) for r in rows]


def dry_run_step(job: str, set_name: str, items: list[Item], cfg: dict, ledger_path: Path, *,
                 decide_dir: Path = DECIDE_DIR, now: datetime | None = None, out=print) -> dict:
    """Freeze the job's items and price them: writes items-<job>-<set>.jsonl.gz and dryrun-<job>-<set>.json. Opens
    no connection and spends nothing; reads the ledger's totals, creating an empty ledger when there is none (as the
    OpenAI dry run does)."""
    check_caps(cfg)
    ids = [i.item_id for i in items]
    if len(set(ids)) != len(ids):
        raise JobError(f"{job} on {set_name} has duplicate item ids")
    with ledger_mod.Ledger(ledger_path, create=not Path(ledger_path).exists()) as lg:
        totals = lg.totals()
    save_items(items_path(decide_dir, job, set_name), items)
    d = dry_run(job, set_name, items, cfg, totals, decide_dir=decide_dir, now=now)
    write_atomic(dryrun_path(decide_dir, job, set_name), json.dumps(d, indent=1, sort_keys=True) + "\n")
    out(format_dry_run(d))
    return d


def spend_checks(job: str, set_name: str, cfg: dict, budget_usd, *, decide_dir: Path = DECIDE_DIR,
                 env_path: Path | None = None, now: datetime | None = None) -> tuple[list[Item], Decimal]:
    """Everything a spending run needs before its first reservation: the caps agree, the budget is a positive amount
    within the job's cap, the key is in .env, the price was verified in the last 24 hours, and a dry run of exactly
    these frozen items exists from the last 24 hours. Returns the items and the budget."""
    check_caps(cfg)
    try:
        budget = Decimal(str(budget_usd))
    except ArithmeticError:
        raise JobError("--budget-usd must be a number of dollars") from None
    if not budget.is_finite() or budget <= 0:
        raise JobError("--budget-usd must be a positive number of dollars")
    if budget > Decimal(str(cfg["caps"]["jobs"][job])):
        raise JobError(f"--budget-usd {budget} is above {job}'s ${cfg['caps']['jobs'][job]:.2f} cap")
    if not key_present(cfg, env_path):
        raise JobError(f"no {cfg['key_name']} in .env: {job} is skipped and dispatch uses the rules (spec §13)")
    check_price(cfg, now)
    items = load_items(items_path(decide_dir, job, set_name))
    check_dry_run(dryrun_path(decide_dir, job, set_name), request_set_hash(job, set_name, items), now)
    return items, budget


def write_outputs(path: Path, rows: list[dict]) -> None:
    write_atomic(path, "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


def read_outputs(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        raise JobError(f"no outputs at {path}; the job has not run")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def summary(result: JobResult, items: list[Item], set_name: str, cfg: dict, totals: dict | None,
            now: datetime | None = None) -> dict:
    """The committed record of one run: counts, refusals, versions and the ledger's totals for the job."""
    reasons: dict = {}
    refusals: dict = {}
    for o in result.outcomes:
        if o.status == "fallback":
            reasons[o.reason or "unknown"] = reasons.get(o.reason or "unknown", 0) + 1
        for q in o.refusals:
            refusals[q] = refusals.get(q, 0) + 1
    jt = (totals or {}).get("jobs", {}).get(result.job) or {}
    counts = {s: sum(o.status == s for o in result.outcomes) for s in ("answered", "cached", "fallback")}
    return {"job": result.job, "set": set_name, "notice": NOTICE,
            "created_at": (now or datetime.now().astimezone()).isoformat(timespec="seconds"),
            "status": "stopped" if result.stopped else "run", "stopped": result.stopped,
            "items": len(items), "counts": counts, "fallback_reasons": dict(sorted(reasons.items())),
            "refusals": dict(sorted(refusals.items())), "requests_sent": result.sent,
            "model": result.model, "resolved_model": result.resolved_model, "versions": result.versions,
            "request_set_hash": request_set_hash(result.job, set_name, items), "contract": CONTRACT,
            "prompt_version": cfg["decisions"]["prompt_version"], "config_hash": config_hash(cfg),
            "ledger": {"spent_usd": jt.get("spent", 0) / 1e6, "held_usd": jt.get("held", 0) / 1e6,
                       "cap_usd": float(cfg["caps"]["jobs"][result.job])}}


def not_run(job: str, set_name: str, reason: str, results_dir: Path = RESULTS_DIR, now: datetime | None = None) -> dict:
    """Record a job that did not run and why (spec §13: no key means the rules ship and the report says why)."""
    doc = {"job": job, "set": set_name, "status": "not run", "reason": reason, "notice": NOTICE,
           "created_at": (now or datetime.now().astimezone()).isoformat(timespec="seconds")}
    write_atomic(summary_path(results_dir, job, set_name), json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return doc


def spend_step(job: str, set_name: str, cfg: dict, budget_usd, ledger_path: Path, decide_fn, *, client_factory=None,
               decide_dir: Path = DECIDE_DIR, results_dir: Path = RESULTS_DIR, env_path: Path | None = None,
               sleep=time.sleep, rng=None, concurrency: int | None = None, now=None, out=print) -> dict:
    """Send a job's frozen items (after spend_checks), then write each item's decision (decide_fn(outcomes, items)
    -> rows) to data/decide/outputs/ and the run's summary to results/neural/decisions/. One decision process at a
    time (a lock file beside the cache)."""
    from src.scenarios.runner import GeneratorLock

    clock = now or (lambda: datetime.now().astimezone())
    items, budget = spend_checks(job, set_name, cfg, budget_usd, decide_dir=decide_dir, env_path=env_path, now=clock())
    factory = client_factory or (lambda: DecisionClient(cfg, env_path=env_path))
    with GeneratorLock(Path(decide_dir) / "decide.lock"):
        with ledger_mod.Ledger(ledger_path) as lg:
            if not lg.totals()["jobs"][job]["released"]:
                raise JobError(f"{job} is not released in the ledger")
            run = Job(job, cfg, lg, client_factory=factory, budget_usd=budget, decide_dir=decide_dir, sleep=sleep,
                      rng=rng, concurrency=concurrency, out=out, now=clock)
            result = run.run(items)
            totals = lg.totals()
    rows = decide_fn(result.outcomes, items, result)
    write_outputs(outputs_path(decide_dir, job, set_name), rows)
    doc = summary(result, items, set_name, cfg, totals, clock())
    write_atomic(summary_path(results_dir, job, set_name), json.dumps(doc, indent=1, sort_keys=True) + "\n")
    c = doc["counts"]
    out(f"{job} on {set_name}: {c['answered']:,} answered, {c['cached']:,} from the cache, {c['fallback']:,} on the "
        f"rules; {result.sent:,} requests sent; job spent ${doc['ledger']['spent_usd']:.4f} of its "
        f"${doc['ledger']['cap_usd']:.2f} cap" + (f"; STOPPED: {result.stopped}" if result.stopped else ""))
    return doc
