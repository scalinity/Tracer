"""Run one OpenAI job: items and their states, the response cache, Flex and Batch execution, resume and repair.

docs/neural/spec.md §4.7 is the design. Every request is an item, `item_id = f"{job}-{unit}-{attempt}"`, where the unit
is the brief id, or `<brief id>.<arm>` in a job that sends each brief to several models (the paired pilot). Item states
and transitions are exactly the §4.7 diagram (TRANSITIONS below); they live in `data/openai/items.sqlite`, and every
transition is appended to `data/openai/items.jsonl`.

Spend rules the runner keeps:
- Nothing is sent without a ledger reservation for the request's worst case. The item moves to `in_flight` before
  the request leaves, so a crash can only ever over-count: on resume an `in_flight` item with no stored response is
  `unknown`, and its whole reservation stays spent.
- Every HTTP 200 body is written to `data/openai/responses/<request_hash>.json` before anything parses it. A request
  whose response is stored is never sent again.
- A Flex 429, another 429 that is not about billing, a server 5xx or any other 4xx is voided (not billed). Timeouts,
  dropped connections, HTTP 408/504/524 and anything unexpected keep the full reservation.
- An `unknown` item is billed and finished; its brief gets the repair attempt, never a silent resend.
- A settlement above its reservation, an out-of-credit error, a systematic 4xx or an unexpected client error stops the
  job: in-flight requests finish and are settled, and nothing new is sent.

The N1 modules (planner, prompt, schema, validate) are reached only through `Seams`; `real_seams()` imports them.
"""

from __future__ import annotations

import fcntl
import hashlib
import heapq
import json
import math
import os
import random
import sqlite3
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable

import yaml

from . import ledger as ledger_mod
from . import prices
from .client import CallResult, ClientError, build_body, parse_response

ROOT = Path(__file__).resolve().parents[2]

STATES = ("planned", "reserved", "refused", "in_flight", "unavailable", "unknown", "received", "parsed", "failed",
          "validated", "sim_checked", "accepted", "repair_planned", "dropped")
TRANSITIONS = {
    "planned": {"reserved"},
    "reserved": {"refused", "in_flight"},
    "in_flight": {"unavailable", "unknown", "received"},
    "unavailable": {"reserved"},
    "received": {"parsed", "failed"},
    "parsed": {"validated", "failed"},
    "validated": {"sim_checked", "failed"},
    "sim_checked": {"accepted", "failed"},
    "failed": {"repair_planned", "dropped"},
    "repair_planned": {"reserved"},
}
# An attempt is over in these states; `unknown` and `repair_planned` hand the brief to its next attempt.
ATTEMPT_OVER = {"refused", "unknown", "accepted", "dropped", "repair_planned"}
TO_SEND = {"planned", "reserved", "unavailable"}
MAX_ATTEMPTS = 2

FLEX_TRIES = 6
BACKOFF_BASE_S = 40.0  # waits of about 40, 80, 160, 320 and 640 s between six tries: about 20 minutes in all
JITTER = (0.75, 1.25)
START_WORKERS, MAX_WORKERS = 8, 32
TPM_SHARE = 0.8  # requested tokens per minute (input plus max_output_tokens) stay under this share of the limit
TIMEOUT_STATUSES = {408, 504, 524}
BILLING_CODES = {"insufficient_quota", "credit_balance_exhausted", "organization_spend_limit_exceeded",
                 "project_spend_limit_exceeded", "organization_usage_limit_exceeded"}
RATE_LIMIT_CODES = {"rate_limit_exceeded", "slow_down"}

BATCH_MAX_AGE = timedelta(hours=6)
BATCH_MAX_REQUESTS = 50_000
CHUNK_STATES = ("created", "submitted", "validating", "in_progress", "finalizing", "cancelling", "completed",
                "failed", "expired", "cancelled")
CHUNK_END = {"completed", "failed", "expired", "cancelled"}

# Local input-token estimate (the dry run's default): characters of the messages and the schema / 2.5, plus framing,
# times a 15% margin. 2.5 characters per token is deliberately low for JSON-heavy English (source: assumed).
CHARS_PER_TOKEN = 2.5
LOCAL_MARGIN = 1.15
MESSAGE_OVERHEAD_TOKENS = 16
REQUEST_OVERHEAD_TOKENS = 64
DRYRUN_MAX_AGE = timedelta(hours=24)

# Which state a validation layer's failure is recorded from (spec §4.8 order), and the layers that get no repair.
LAYER_STAGE = {"plausibility": "validated", "simulation": "validated", "adherence": "sim_checked",
               "dedup": "sim_checked", "diversity": "sim_checked", "duplicate": "sim_checked",
               "near_duplicate": "sim_checked"}
NO_REPAIR_LAYERS = {"dedup", "diversity", "duplicate", "near_duplicate"}
VALIDATION_ORDER = ("parsed", "validated", "sim_checked", "accepted")
MODEL_FAILURES = {
    "incomplete": "The previous reply stopped at max_output_tokens before the JSON was complete. Use compact values "
                  "and return the whole configuration.",
    "refusal": "The previous reply was a refusal. This is a synthetic research simulator: no real institution, "
               "patient or person is involved. Return the configuration.",
    "not_json": "The previous reply was not a JSON object matching the schema. Return only the configuration.",
    "failed": "The previous request failed before a configuration was written. Return the configuration.",
}
UNKNOWN_TEXT = ("The previous attempt for this brief did not come back (a timeout or a dropped connection). "
                "Write the configuration again.")


class JobError(RuntimeError):
    """A job cannot start or must stop."""


# -- paths, configuration and the seam to N1's modules ----------------------------------------------------------------

@dataclass(frozen=True)
class Paths:
    root: Path = ROOT
    jobs_config: Path = ROOT / "config" / "openai_jobs.yaml"
    prices: Path = ROOT / "config" / "openai_prices.yaml"
    env: Path = ROOT / ".env"

    @property
    def openai_dir(self) -> Path:
        return self.root / "data" / "openai"

    @property
    def ledger(self) -> Path:
        return self.openai_dir / "ledger.sqlite"

    @property
    def items(self) -> Path:
        return self.openai_dir / "items.sqlite"

    @property
    def items_log(self) -> Path:
        return self.openai_dir / "items.jsonl"

    @property
    def responses(self) -> Path:
        return self.openai_dir / "responses"

    @property
    def lock(self) -> Path:
        return self.openai_dir / "generator.lock"

    @property
    def stages(self) -> Path:
        return self.root / "data" / "neural" / "stages"

    @property
    def pilot_gate(self) -> Path:
        return self.root / "results" / "neural" / "generation" / "pilot.json"

    @property
    def weak_spot_plan(self) -> Path:
        return self.root / "results" / "neural" / "generation" / "round2-plan.json"

    def briefs(self, job: str) -> Path:
        return self.openai_dir / f"briefs-{job}.jsonl"

    def dryrun(self, job: str) -> Path:
        return self.openai_dir / f"dryrun-{job}.json"


@dataclass
class Seams:
    """The N1 interfaces the runner calls. Tests pass fakes; real_seams() wires the real modules."""
    plan: Callable            # planner.plan(job, seed, *, n=None, targets=None) -> list[dict]
    build_messages: Callable  # prompt.build_messages(brief, repair_errors=None) -> list[dict]
    api_schema: Callable      # schema.api_schema() -> dict
    schema_hash: Callable     # schema.schema_hash() -> str
    prompt_hash: Callable     # prompt.prompt_hash(model, effort) -> str
    validate: Callable        # validate.validate(scenario, brief) -> Result(ok, layer, errors)


# src/scenarios/validate.py numbers its layers (Result.layer is "2 semantics"); the runner's stage map and its
# no-repair set use these names.
LAYER_NAMES = {1: "schema", 2: "semantics", 3: "names", 4: "plausibility", 5: "adherence", 6: "near_duplicate"}


def real_validate(scenario: dict, brief: dict):
    """Layers 1 to 5 of spec §4.8, with the simulator's plausibility check, and the layer named for the runner.

    Layer 6 (near duplicates) runs at freeze time over the accepted scenarios in item-id order, so which of two
    near duplicates survives never depends on which request finished first; a near duplicate gets no repair either
    way, so the result is the same as rejecting it here."""
    from src.scenarios import validate
    from src.sim import kpi  # generation never runs without the plausibility layer

    res = validate.validate(scenario, brief, plausibility=kpi.check)
    if res.ok:
        return res.ok, None, []
    return False, LAYER_NAMES.get(res.number, f"layer {res.number}"), list(res.errors)


def real_seams() -> Seams:
    from src.scenarios import planner, prompt, schema  # N1's modules

    return Seams(plan=planner.plan, build_messages=prompt.build_messages, api_schema=schema.api_schema,
                 schema_hash=schema.schema_hash, prompt_hash=prompt.prompt_hash, validate=real_validate)


def load_jobs(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def job_settings(cfg: dict, job: str) -> dict:
    j = (cfg.get("jobs") or {}).get(job)
    if not j or not j.get("arms"):
        raise JobError(f"{job} is not an OpenAI scenario job in config/openai_jobs.yaml")
    for arms in j["arms"].values():
        for arm in arms:
            if arm not in cfg["arms"]:
                raise JobError(f"job {job} names unknown arm {arm}")
    return j


def arms_for(cfg: dict, job: str, stratum: str) -> list[str]:
    arms = job_settings(cfg, job)["arms"]
    chosen = arms.get("all") or arms.get(stratum)
    if not chosen:
        raise JobError(f"job {job} has no arm for stratum {stratum!r}")
    return list(chosen)


def multi_arm(cfg: dict, job: str) -> bool:
    return any(len(a) > 1 for a in job_settings(cfg, job)["arms"].values())


def check_efforts(cfg: dict, job: str, table: dict) -> None:
    """Each arm's effort must be one its model lists in the price table (Sol has no `none`)."""
    for arms in job_settings(cfg, job)["arms"].values():
        for name in arms:
            arm = cfg["arms"][name]
            allowed = ((table.get("models") or {}).get(arm["model"]) or {}).get("reasoning_efforts")
            if allowed is not None and arm["effort"] not in allowed:
                raise JobError(f"{arm['model']} does not take reasoning effort {arm['effort']!r} (it takes {allowed})")


# -- requests ---------------------------------------------------------------------------------------------------------

def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def request_hash(body: dict) -> str:
    """Covers the model, every parameter and the messages; not the service tier, so a stored response serves any
    tier and is never bought twice."""
    return hashlib.sha256(canonical({k: v for k, v in body.items() if k != "service_tier"}).encode()).hexdigest()[:32]


def local_raw_tokens(body: dict) -> int:
    text = canonical(body.get("input")) + canonical(((body.get("text") or {}).get("format") or {}).get("schema"))
    messages = len(body.get("input") or [])
    return math.ceil(len(text) / CHARS_PER_TOKEN) + MESSAGE_OVERHEAD_TOKENS * messages + REQUEST_OVERHEAD_TOKENS


def estimate_input_tokens(body: dict) -> int:
    """Input tokens with a 15% margin, counted locally (no network)."""
    return math.ceil(local_raw_tokens(body) * LOCAL_MARGIN)


def make_item_id(job: str, unit: str, attempt: int) -> str:
    return f"{job}-{unit}-{attempt}"


def build_request(seams: Seams, cfg: dict, job: str, brief: dict, arm_name: str, tier: str,
                  repair_errors: list[str] | None = None) -> dict:
    arm = cfg["arms"][arm_name]
    messages = seams.build_messages(brief, repair_errors=repair_errors)
    return build_body(model=arm["model"], messages=messages, schema=seams.api_schema(),
                      schema_name=cfg["api"]["schema_name"], effort=arm["effort"],
                      max_output_tokens=arm["max_output_tokens"], service_tier=prices.REQUEST_TIERS[tier],
                      prompt_cache_key=f"tracer-v2-{job}")


def plan_requests(seams: Seams, cfg: dict, job: str, briefs: list[dict], tier: str) -> list[dict]:
    """Attempt-1 requests for every brief and arm of the job, in planner order."""
    several = multi_arm(cfg, job)
    out = []
    for brief in briefs:
        for arm_name in arms_for(cfg, job, brief["stratum"]):
            arm = cfg["arms"][arm_name]
            unit = f"{brief['brief_id']}.{arm_name}" if several else str(brief["brief_id"])
            body = build_request(seams, cfg, job, brief, arm_name, tier)
            out.append({"item_id": make_item_id(job, unit, 1), "unit": unit, "brief_id": brief["brief_id"],
                        "arm": arm_name, "attempt": 1, "model": arm["model"], "effort": arm["effort"],
                        "max_output_tokens": arm["max_output_tokens"],
                        "expected_output_tokens": arm.get("expected_output_tokens", arm["max_output_tokens"]),
                        "stratum": brief["stratum"], "brief": brief, "body": body,
                        "request_hash": request_hash(body), "est_input_tokens": estimate_input_tokens(body)})
    ids = [r["item_id"] for r in out]
    if len(set(ids)) != len(ids):
        raise JobError(f"job {job} has duplicate item ids; brief ids must be unique")
    return out


def request_set_hash(job: str, tier: str, requests: list[dict]) -> str:
    rows = sorted((r["item_id"], r["request_hash"]) for r in requests)
    return hashlib.sha256(canonical({"job": job, "tier": tier, "requests": rows}).encode()).hexdigest()[:32]


# -- dry run ----------------------------------------------------------------------------------------------------------

def dry_run(*, job: str, cfg: dict, table: dict, requests: list[dict], tier: str, totals: dict | None,
            counter: Callable[[dict], int] | None = None, now: datetime | None = None,
            seams: Seams | None = None, ledger_state: str = "present") -> dict:
    """Price every request of the job without sending one. `counter`, when given, is the token-counting endpoint
    (behind a flag): it counts up to three requests per model and scales the local estimates by the worst ratio."""
    now = now or datetime.now().astimezone()
    check_efforts(cfg, job, table)
    estimator = "local"
    if counter is not None:
        estimator = "count_api"
        ratio: dict[str, float] = {}
        measured: dict[str, int] = {}
        for model in sorted({r["model"] for r in requests}):
            sample = [r for r in requests if r["model"] == model][:3]
            for r in sample:
                n = counter(r["body"])
                measured[r["item_id"]] = n
                ratio[model] = max(ratio.get(model, 0.0), n / local_raw_tokens(r["body"]))
        for r in requests:
            r["est_input_tokens"] = measured.get(r["item_id"]) or math.ceil(
                local_raw_tokens(r["body"]) * ratio[r["model"]] * LOCAL_MARGIN)
    groups: dict = {}
    items = {}
    for r in requests:
        rates = prices.rates(table, r["model"], tier)
        worst = prices.worst_case_micros(r["est_input_tokens"], r["max_output_tokens"], rates)
        expected = prices.expected_micros(r["est_input_tokens"], r["expected_output_tokens"], rates)
        items[r["item_id"]] = {"request_hash": r["request_hash"], "est_input_tokens": r["est_input_tokens"],
                               "worst_micros": worst, "expected_micros": expected, "model": r["model"],
                               "effort": r["effort"], "stratum": r["stratum"]}
        g = groups.setdefault((r["model"], r["effort"], r["stratum"]),
                              {"model": r["model"], "effort": r["effort"], "stratum": r["stratum"], "requests": 0,
                               "input_tokens": 0, "expected_micros": 0, "worst_micros": 0})
        g["requests"] += 1
        g["input_tokens"] += r["est_input_tokens"]
        g["expected_micros"] += expected
        g["worst_micros"] += worst
    j = job_settings(cfg, job)
    acct = cfg["accounts"][j["account"]]
    job_totals = (totals or {}).get("jobs", {}).get(job)
    acct_totals = (totals or {}).get("accounts", {}).get(j["account"])
    reservable = ledger_mod.to_micros(acct["cap_usd"]) - ledger_mod.to_micros(acct.get("margin_usd", 0))
    out = {
        "job": job, "tier": tier, "created_at": now.isoformat(timespec="seconds"),
        "request_set_hash": request_set_hash(job, tier, requests), "requests": len(requests),
        "estimator": estimator, "chars_per_token": CHARS_PER_TOKEN, "local_margin": LOCAL_MARGIN,
        "prices_verified_at": str(table.get("verified_at")),
        "prices_age_hours": round(prices.age(table, now).total_seconds() / 3600, 2),
        "groups": [groups[k] for k in sorted(groups)],
        "expected_micros": sum(g["expected_micros"] for g in groups.values()),
        "worst_micros": sum(g["worst_micros"] for g in groups.values()),
        "job_cap_micros": ledger_mod.to_micros(j["cap_usd"]),
        "job_spent_micros": job_totals["spent"] if job_totals else 0,
        "job_held_micros": job_totals["held"] if job_totals else 0,
        "account": j["account"],
        "account_headroom_micros": acct_totals["headroom"] if acct_totals else reservable,
        "ledger": ledger_state,
        "items": items,
    }
    if seams is not None:
        out["schema_hash"] = seams.schema_hash()
        out["prompt_hashes"] = {f"{m} {e}": seams.prompt_hash(m, e)
                                for m, e in sorted({(r["model"], r["effort"]) for r in requests})}
    return out


def format_dry_run(d: dict) -> str:
    usd = lambda m: f"${m / 1e6:,.4f}"  # noqa: E731
    lines = [f"Dry run for {d['job']} at the {d['tier']} tier: {d['requests']} requests, nothing sent, nothing spent.",
             f"Input tokens estimated {'locally (characters / ' + str(d['chars_per_token']) + ', +15%)' if d['estimator'] == 'local' else 'with the token-counting endpoint (sampled)'}; "
             f"prices verified {d['prices_verified_at']} ({d['prices_age_hours']:.1f} h ago).",
             "", f"{'model':<15} {'effort':<7} {'stratum':<10} {'requests':>8} {'expected':>11} {'worst case':>11}"]
    for g in d["groups"]:
        lines.append(f"{g['model']:<15} {g['effort']:<7} {g['stratum']:<10} {g['requests']:>8} "
                     f"{usd(g['expected_micros']):>11} {usd(g['worst_micros']):>11}")
    lines += [f"{'total':<34} {d['requests']:>8} {usd(d['expected_micros']):>11} {usd(d['worst_micros']):>11}",
              "", f"Job cap {usd(d['job_cap_micros'])}; job spent {usd(d['job_spent_micros'])}, held "
              f"{usd(d['job_held_micros'])}; {d['account']} account headroom {usd(d['account_headroom_micros'])} "
              f"(ledger {d['ledger']}).",
              "Repairs (one per rejected brief) are not in these numbers. Reservations are per request, so the worst "
              "case total may pass the cap; requests the cap cannot cover are refused, never sent.",
              f"Request-set hash {d['request_set_hash']}; written to data/openai/dryrun-{d['job']}.json."]
    if d["expected_micros"] > d["job_cap_micros"]:
        lines.append("WARNING: the expected cost passes the job cap; the job will stop when the cap is reached.")
    return "\n".join(lines)


def check_dry_run(paths: Paths, job: str, tier: str, set_hash: str, now: datetime | None = None) -> dict:
    """A spending command's check: a dry run of the same request set and tier, under 24 hours old."""
    path = paths.dryrun(job)
    if not path.exists():
        raise JobError(f"no dry run for {job}; run `python -m src.neural.pipeline dry-run --job {job}` first")
    d = json.loads(path.read_text(encoding="utf-8"))
    now = now or datetime.now().astimezone()
    made = datetime.fromisoformat(d["created_at"])
    if now - made > DRYRUN_MAX_AGE:
        raise JobError(f"the dry run for {job} is {(now - made).total_seconds() / 3600:.1f} hours old (limit 24); "
                       "run it again")
    if d.get("tier") != tier:
        raise JobError(f"the dry run for {job} priced the {d.get('tier')} tier, not {tier}; run it with --tier {tier}")
    if d.get("request_set_hash") != set_hash:
        raise JobError(f"the requests of {job} changed since its dry run (hash {d.get('request_set_hash')} there, "
                       f"{set_hash} now); run the dry run again")
    return d


# -- stores -----------------------------------------------------------------------------------------------------------

class ItemStore:
    """Item and Batch-chunk states in SQLite, every transition appended to a JSONL log."""

    def __init__(self, path: Path, log_path: Path):
        self.path, self.log_path = Path(path), Path(log_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.con = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False, timeout=30)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("CREATE TABLE IF NOT EXISTS items (item_id TEXT PRIMARY KEY, job TEXT NOT NULL, "
                         "state TEXT NOT NULL, data TEXT NOT NULL)")
        self.con.execute("CREATE TABLE IF NOT EXISTS chunks (chunk_id TEXT PRIMARY KEY, job TEXT NOT NULL, "
                         "state TEXT NOT NULL, data TEXT NOT NULL)")

    def close(self) -> None:
        self.con.close()

    def _log(self, rec: dict) -> None:
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")

    @staticmethod
    def _row(row) -> dict | None:
        if row is None:
            return None
        return {**json.loads(row[3]), "item_id": row[0], "job": row[1], "state": row[2]}

    def add(self, item: dict, state: str = "planned", detail: str | None = None) -> bool:
        with self._lock:
            data = {k: v for k, v in item.items() if k not in ("item_id", "job", "state", "body")}
            cur = self.con.execute("INSERT OR IGNORE INTO items VALUES (?, ?, ?, ?)",
                                   (item["item_id"], item["job"], state, json.dumps(data, sort_keys=True)))
            if cur.rowcount:
                self._log({"at": ledger_mod.now_iso(), "item_id": item["item_id"], "job": item["job"], "from": None,
                           "to": state, "detail": detail})
            return bool(cur.rowcount)

    def get(self, item_id: str) -> dict | None:
        with self._lock:
            return self._row(self.con.execute("SELECT item_id, job, state, data FROM items WHERE item_id=?",
                                              (item_id,)).fetchone())

    def items(self, job: str | None = None) -> list[dict]:
        with self._lock:
            if job is None:
                rows = self.con.execute("SELECT item_id, job, state, data FROM items ORDER BY rowid").fetchall()
            else:
                rows = self.con.execute("SELECT item_id, job, state, data FROM items WHERE job=? ORDER BY rowid",
                                        (job,)).fetchall()
            return [self._row(r) for r in rows]

    def _save(self, item: dict) -> None:
        data = {k: v for k, v in item.items() if k not in ("item_id", "job", "state")}
        self.con.execute("UPDATE items SET state=?, data=? WHERE item_id=?",
                         (item["state"], json.dumps(data, sort_keys=True), item["item_id"]))

    def transition(self, item_id: str, to: str, detail: str | None = None, **fields) -> dict:
        with self._lock:
            item = self.get(item_id)
            if item is None:
                raise JobError(f"no item {item_id}")
            if to not in TRANSITIONS.get(item["state"], set()):
                raise JobError(f"item {item_id}: {item['state']} -> {to} is not a transition of spec §4.7")
            frm = item["state"]
            item.update(fields, state=to, updated_at=ledger_mod.now_iso())
            self._save(item)
            self._log({"at": item["updated_at"], "item_id": item_id, "job": item["job"], "from": frm, "to": to,
                       "detail": detail})
            return item

    def update(self, item_id: str, **fields) -> dict:
        with self._lock:
            item = self.get(item_id)
            item.update(fields)
            self._save(item)
            return item

    def counts(self, job: str) -> dict:
        with self._lock:
            return dict(self.con.execute("SELECT state, COUNT(*) FROM items WHERE job=? GROUP BY state ORDER BY state",
                                         (job,)).fetchall())

    # chunks
    def add_chunk(self, chunk: dict) -> None:
        with self._lock:
            data = {k: v for k, v in chunk.items() if k not in ("chunk_id", "job", "state")}
            self.con.execute("INSERT INTO chunks VALUES (?, ?, ?, ?)",
                             (chunk["chunk_id"], chunk["job"], chunk["state"], json.dumps(data, sort_keys=True)))
            self._log({"at": ledger_mod.now_iso(), "chunk_id": chunk["chunk_id"], "job": chunk["job"], "from": None,
                       "to": chunk["state"]})

    def chunks(self, job: str | None = None, open_only: bool = False) -> list[dict]:
        with self._lock:
            rows = self.con.execute("SELECT chunk_id, job, state, data FROM chunks ORDER BY rowid").fetchall()
            out = [{**json.loads(d), "chunk_id": c, "job": j, "state": s} for c, j, s, d in rows]
            if job is not None:
                out = [c for c in out if c["job"] == job]
            if open_only:
                out = [c for c in out if not c.get("finished")]
            return out

    def chunk_transition(self, chunk_id: str, to: str, **fields) -> dict:
        with self._lock:
            chunk = next(c for c in self.chunks() if c["chunk_id"] == chunk_id)
            frm = chunk["state"]
            if to != frm:
                if frm in CHUNK_END or CHUNK_STATES.index(to) < CHUNK_STATES.index(frm) and to not in CHUNK_END:
                    raise JobError(f"chunk {chunk_id}: {frm} -> {to} goes backward")
            chunk.update(fields, state=to)
            data = {k: v for k, v in chunk.items() if k not in ("chunk_id", "job", "state")}
            self.con.execute("UPDATE chunks SET state=?, data=? WHERE chunk_id=?",
                             (to, json.dumps(data, sort_keys=True), chunk_id))
            if to != frm:
                self._log({"at": ledger_mod.now_iso(), "chunk_id": chunk_id, "job": chunk["job"], "from": frm,
                           "to": to})
            return chunk


class ResponseCache:
    """Raw response bodies by request hash. A stored response is never overwritten and its request never re-sent."""

    def __init__(self, directory: Path):
        self.dir = Path(directory)

    def path(self, h: str) -> Path:
        return self.dir / f"{h}.json"

    def has(self, h: str) -> bool:
        return self.path(h).exists()

    def read(self, h: str) -> str | None:
        p = self.path(h)
        return p.read_text(encoding="utf-8") if p.exists() else None

    def write(self, h: str, text: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        target = self.path(h)
        if target.exists():  # never replace the first copy; keep the second beside it for inspection
            target = self.dir / f"{h}.extra-{uuid.uuid4().hex[:8]}.json"
        tmp = self.dir / f".{h}.{uuid.uuid4().hex[:8]}.tmp"
        with tmp.open("w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)


class GeneratorLock:
    """One generator process at a time: an exclusive flock on data/openai/generator.lock (released at exit)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.fd = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None
            raise JobError(f"another generator process holds {self.path}; one at a time") from None
        os.ftruncate(self.fd, 0)
        os.write(self.fd, f"{os.getpid()}\n".encode())
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None


class Throttle:
    """Keeps requested tokens per minute (input plus max_output_tokens) under 80% of each model's limit."""

    def __init__(self, limits: dict[str, int], clock: Callable[[], float]):
        self.limit = dict(limits)
        self.clock = clock
        self.window: dict[str, list[tuple[float, int]]] = {}

    def wait_s(self, model: str, tokens: int) -> float:
        now = self.clock()
        window = [(t, n) for t, n in self.window.get(model, []) if now - t < 60]
        self.window[model] = window
        cap = TPM_SHARE * self.limit.get(model, 1_000_000)
        used = sum(n for _, n in window)
        if used + tokens <= cap or not window:
            return 0.0
        for t, n in window:  # the wait until enough of the window has aged out
            used -= n
            if used + tokens <= cap:
                return max(0.0, 60 - (now - t))
        return max(0.0, 60 - (now - window[-1][0]))

    def take(self, model: str, tokens: int) -> None:
        self.window.setdefault(model, []).append((self.clock(), tokens))

    def observe(self, model: str, limits) -> None:
        if limits is not None and limits.limit_tokens:
            self.limit[model] = limits.limit_tokens


def _result_fields(res) -> tuple[bool, str | None, list[str]]:
    ok = getattr(res, "ok", None)
    if ok is None:
        ok, layer, errors = res
    else:
        layer, errors = getattr(res, "layer", None), getattr(res, "errors", [])
    return bool(ok), layer, [str(e) for e in (errors or [])]


# -- the runner -------------------------------------------------------------------------------------------------------

class Runner:
    def __init__(self, *, job: str, cfg: dict, table: dict, ledger: ledger_mod.Ledger, store: ItemStore, client,
                 seams: Seams, paths: Paths, tier: str, budget_usd=None, sleep: Callable = time.sleep,
                 clock: Callable = time.monotonic, now: Callable | None = None, rng: random.Random | None = None,
                 out: Callable = print):
        self.job, self.cfg, self.table, self.ledger, self.store = job, cfg, table, ledger, store
        self.client, self.seams, self.paths, self.tier = client, seams, paths, tier
        self.budget = None if budget_usd is None else Decimal(str(budget_usd))
        self.sleep, self.clock = sleep, clock
        self.now = now or (lambda: datetime.now().astimezone())
        self.rng = rng or random.Random()
        self.out = out
        self.cache = ResponseCache(paths.responses)
        self.workers = START_WORKERS
        self.throttle = Throttle({m: v.get("tpm_limit") or 1_000_000 for m, v in (cfg.get("models") or {}).items()},
                                 clock)
        self.stop_reason: str | None = None
        self.heap: list = []
        self._seq = 0
        self.sent = 0
        self.batch_pending: list[str] = []

    # helpers ---------------------------------------------------------------------------------------------------------

    def stop(self, reason: str) -> None:
        if self.stop_reason is None:
            self.stop_reason = reason
            self.out(f"STOP: {reason}")

    def _push(self, item_id: str, ready_at: float | None = None) -> None:
        self._seq += 1
        heapq.heappush(self.heap, (self.clock() if ready_at is None else ready_at, self._seq, item_id))

    def _body(self, item: dict, tier: str | None = None) -> dict:
        body = build_request(self.seams, self.cfg, self.job, item["brief"], item["arm"], tier or item["tier"],
                             item.get("repair_errors"))
        if request_hash(body) != item["request_hash"]:
            raise JobError(f"item {item['item_id']}: its request no longer matches the one planned (the prompt, schema "
                           "or brief changed mid-job); run the dry run again")
        return body

    def _worst_micros(self, item: dict, tier: str) -> int:
        return prices.worst_case_micros(item["est_input_tokens"], item["max_output_tokens"],
                                        prices.rates(self.table, item["model"], tier))

    @staticmethod
    def _usd(micros: int) -> Decimal:
        return Decimal(micros) / 1_000_000

    def _timeout(self, tier: str) -> float:
        api = self.cfg["api"]
        return float(api["flex_timeout_s"] if tier == "flex" else api["standard_timeout_s"])

    # planning --------------------------------------------------------------------------------------------------------

    def plan_items(self, requests: list[dict], dry: dict | None = None) -> int:
        """Store the attempt-1 items (estimates from the dry run when given). Returns how many were new."""
        new = 0
        for r in requests:
            existing = self.store.get(r["item_id"])
            if existing is not None:
                if existing["request_hash"] != r["request_hash"]:
                    raise JobError(f"item {r['item_id']} exists with a different request; the prompt or brief "
                                   "changed after the job started")
                continue
            est = (dry or {}).get("items", {}).get(r["item_id"], {}).get("est_input_tokens", r["est_input_tokens"])
            item = {k: v for k, v in r.items() if k != "body"}
            item.update(job=self.job, est_input_tokens=est, tier=self.tier, tries=0, repair_errors=None)
            new += self.store.add(item)
        return new

    def _next_attempt(self, item: dict, errors: list[str]) -> dict | None:
        """Create the brief's repair attempt (once). Returns the new item, or None if it already exists."""
        attempt = item["attempt"] + 1
        new_id = make_item_id(self.job, item["unit"], attempt)
        if self.store.get(new_id) is not None:
            return None
        body = build_request(self.seams, self.cfg, self.job, item["brief"], item["arm"], self.tier, errors)
        new = {k: item[k] for k in ("job", "unit", "brief_id", "arm", "model", "effort", "max_output_tokens",
                                    "expected_output_tokens", "stratum", "brief")}
        new.update(item_id=new_id, attempt=attempt, repair_of=item["item_id"], repair_errors=errors,
                   request_hash=request_hash(body), est_input_tokens=estimate_input_tokens(body), tier=self.tier,
                   tries=0)
        self.store.add(new, detail=f"repair of {item['item_id']}")
        self._push(new_id)
        return new

    # resume ----------------------------------------------------------------------------------------------------------

    def resume(self) -> None:
        """Rebuild states after a crash. In-flight Flex items settle from a stored response or become unknown;
        reservations no item is using are voided (they were never sent); repairs are planned where owed."""
        open_res = {r["entry_id"]: r for r in self.ledger.open_reservations(self.job)}
        claimed = set()
        for item in self.store.items(self.job):
            rid, state = item.get("reservation_id"), item["state"]
            if state == "in_flight" and item.get("chunk_id"):
                claimed.add(rid)
            elif state == "in_flight":
                claimed.add(rid)
                text = self.cache.read(item["request_hash"])
                if rid in open_res:
                    if text is not None:
                        self._settle(item, self._parse_text(text))
                    else:
                        self.ledger.settle_unknown(rid, "interrupted while in flight; not proven unbilled")
                        self._unknown(item, "interrupted while in flight")
                else:  # the ledger closed it but the item never moved on
                    closing = [e for e in self.ledger.entries(self.job) if e["ref"] == rid and e["kind"] in
                               ("settle", "void")]
                    if closing and closing[0]["kind"] == "void":
                        self.store.transition(item["item_id"], "unavailable", "voided before a crash")
                    elif text is not None:
                        self.store.transition(item["item_id"], "received", "settled before a crash")
                    else:
                        self._unknown(item, "settled as unknown before a crash")
            elif state == "reserved":
                if rid in open_res:
                    claimed.add(rid)
                else:
                    self.store.update(item["item_id"], reservation_id=None)
        for rid, res in open_res.items():
            if rid in claimed:
                continue
            if str(res.get("item_id") or "").startswith("count-"):
                self.ledger.settle_unknown(rid, "token count interrupted; not proven unbilled")
            else:
                self.ledger.void(rid, "never sent (open with no request in flight on resume)")
        self.process_pending()

    def process_pending(self) -> None:
        """Parse and validate stored responses, plan owed repairs and drops. Idempotent."""
        for item in self.store.items(self.job):
            state = item["state"]
            if state in ("received", "parsed", "validated", "sim_checked"):
                self._process(item)
            elif state == "failed":
                self._after_failure(item, item.get("layer"), item.get("errors") or [])
            elif state == "repair_planned":
                self._next_attempt(item, item.get("errors") or [])
            elif state == "unknown" and item["attempt"] < MAX_ATTEMPTS:
                self._next_attempt(item, [UNKNOWN_TEXT])

    # outcomes --------------------------------------------------------------------------------------------------------

    def _parse_text(self, text: str) -> CallResult:
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            body = None
        return parse_response(body)

    def _unknown(self, item: dict, why: str) -> None:
        item = self.store.transition(item["item_id"], "unknown", why)
        if item["attempt"] < MAX_ATTEMPTS:
            self._next_attempt(item, [UNKNOWN_TEXT])

    def _settle(self, item: dict, r: CallResult, *, tier: str | None = None) -> None:
        """Close the reservation of an HTTP 200 body, then parse and validate it."""
        rid = item["reservation_id"]
        tier_key = tier or prices.served_tier(r.service_tier)
        detail = {"served_tier_field": r.service_tier, "response_status": r.response_status,
                  "elapsed_s": round(r.elapsed_s, 3)}
        if tier_key is None and r.service_tier is None:
            tier_key, detail["tier_inferred"] = item["tier"], True
        cost = None
        try:
            if tier_key is None:
                raise prices.PriceError(f"served tier {r.service_tier!r} is not one the price table names")
            rates = prices.rates(self.table, item["model"], tier_key)
        except prices.PriceError as e:  # an unknown tier may cost more than was reserved
            self.ledger.settle_unknown(rid, f"cannot price the response: {e}", request_id=r.request_id)
            self.stop(f"{item['item_id']} could not be priced ({e}); its whole reservation is kept as spent, and "
                      "nothing more is sent, since an unpriced tier may cost more than was reserved")
        else:
            try:
                cost = prices.usage_cost_micros(r.usage, rates)
            except prices.PriceError as e:  # no usable usage at a known tier: the reservation covers any of it
                self.ledger.settle_unknown(rid, f"usage cannot be priced: {e}", request_id=r.request_id)
                self.out(f"LEDGER: {item['item_id']} reported no usable usage ({e}); its whole reservation is kept")
        if cost is not None:
            s = self.ledger.settle(rid, self._usd(cost), r.usage, r.request_id, tier_key, detail=detail)
            if s["over"]:
                self.stop(f"{item['item_id']} cost ${cost / 1e6:.6f}, above its ${s['reserved'] / 1e6:.6f} "
                          "reservation; the input estimate is too low. Nothing more is sent; rerun the dry run.")
        item = self.store.transition(item["item_id"], "received", r.status, request_id=r.request_id,
                                     served_tier=r.service_tier, tokens=r.tokens(), elapsed_s=round(r.elapsed_s, 3),
                                     result=r.status)
        self._process(item, r)

    def _process(self, item: dict, r: CallResult | None = None) -> None:
        """received -> parsed -> validated -> sim_checked -> accepted, or failed at the first failing layer."""
        if r is None:
            text = self.cache.read(item["request_hash"])
            if text is None:
                raise JobError(f"item {item['item_id']} is {item['state']} but its response is not stored")
            r = self._parse_text(text)
        iid = item["item_id"]
        if item["state"] == "received" and not r.ok:
            errors = [MODEL_FAILURES.get(r.status, MODEL_FAILURES["failed"])]
            item = self.store.transition(iid, "failed", r.status, errors=errors, layer="model", failure=r.status)
            self._after_failure(item, "model", errors)
            return
        if item["state"] == "received":
            item = self.store.transition(iid, "parsed")
        ok, layer, errors = _result_fields(self.seams.validate(r.scenario, item["brief"]))
        target = "accepted" if ok else LAYER_STAGE.get(layer or "", "parsed")
        for step in VALIDATION_ORDER[1:]:
            if item["state"] == target or (not ok and VALIDATION_ORDER.index(item["state"]) >=
                                           VALIDATION_ORDER.index(target)):
                break
            if step in TRANSITIONS.get(item["state"], set()):
                item = self.store.transition(iid, step)
        if not ok:
            item = self.store.transition(iid, "failed", layer, errors=errors, layer=layer)
            self._after_failure(item, layer, errors)

    def _after_failure(self, item: dict, layer: str | None, errors: list[str]) -> None:
        if item["attempt"] >= MAX_ATTEMPTS or layer in NO_REPAIR_LAYERS:
            self.store.transition(item["item_id"], "dropped", f"{layer}: {'; '.join(errors)[:300]}")
            return
        item = self.store.transition(item["item_id"], "repair_planned", layer)
        self._next_attempt(item, errors)

    def _void_to_unavailable(self, item: dict, r: CallResult, why: str, **fields) -> dict:
        self.ledger.void(item["reservation_id"], why, request_id=r.request_id)
        return self.store.transition(item["item_id"], "unavailable", why, **fields)

    def _handle(self, item: dict, r: CallResult) -> None:
        """Settle one Flex or Standard call's outcome."""
        self.throttle.observe(item["model"], r.rate_limits)
        rl = r.rate_limits
        if rl is not None and rl.limit_tokens and rl.remaining_tokens is not None:
            if rl.remaining_tokens > rl.limit_tokens / 2 and self.workers < MAX_WORKERS:
                self.workers += 1
        code = r.http_status
        if r.status in ("timeout", "connection") or code in TIMEOUT_STATUSES:
            self.ledger.settle_unknown(item["reservation_id"], f"{r.status}: outcome unknown", request_id=r.request_id)
            self._unknown(item, r.status)
            return
        if code is None:
            self._settle(item, r)
            return
        if code == 429 and (r.error_code in BILLING_CODES or r.error_type == "insufficient_quota"):
            self._void_to_unavailable(item, r, f"http_429 {r.error_code or r.error_type}")
            if r.error_code in ("credit_balance_exhausted", "insufficient_quota") or r.error_type == "insufficient_quota":
                self.stop("the OpenAI account is out of credit (insufficient_quota). Add credit, then rerun generate.")
            else:
                self.stop(f"an OpenAI spend or usage limit was reached ({r.error_code}). Nothing more is sent.")
            return
        if code == 429 or code >= 500:
            rate_limited = code == 429 and (r.error_code in RATE_LIMIT_CODES or not r.resource_unavailable and
                                            item["tier"] != "flex")
            if rate_limited:
                self.workers = max(1, self.workers // 2)
            tries = item.get("tries", 0) + 1
            item = self._void_to_unavailable(item, r, f"http_{code} (not billed); try {tries} of {FLEX_TRIES}",
                                             tries=tries)
            if tries >= FLEX_TRIES:
                self.store.update(item["item_id"], tier="batch")
                self.batch_pending.append(item["item_id"])
                self.out(f"{item['item_id']}: {FLEX_TRIES} tries without capacity; moved to Batch")
                return
            delay = BACKOFF_BASE_S * 2 ** (tries - 1) * self.rng.uniform(*JITTER)
            if rl is not None and rl.retry_after_s:
                delay = max(delay, rl.retry_after_s)
            self._push(item["item_id"], self.clock() + delay)
            return
        self._void_to_unavailable(item, r, f"http_{code} ({r.error_type}/{r.error_code}); not billed")
        self.stop(f"HTTP {code} from OpenAI ({r.error_type}/{r.error_code}): the request itself is wrong or not "
                  "allowed (model id, schema, service tier or key). Nothing more is sent.")

    # Flex and Standard -----------------------------------------------------------------------------------------------

    def _prepare(self, item: dict):
        """Reserve (or reuse a resumed reservation) and move to in_flight. Returns ("send", body), ("cached", None)
        or ("cap", CapReached)."""
        tier = item["tier"]
        body = self._body(item, tier)
        if item["state"] == "reserved" and item.get("reservation_id") and item.get("reserved_tier") != tier:
            self.ledger.void(item["reservation_id"], f"tier changed to {tier} before sending; nothing was sent")
            item = self.store.update(item["item_id"], reservation_id=None)
        if item["state"] == "reserved" and item.get("reservation_id"):
            rid = item["reservation_id"]
        else:
            worst = self._worst_micros(item, tier)
            try:
                rid = self.ledger.reserve(self.job, item["item_id"], self._usd(worst), ceiling_usd=self.budget,
                                          detail={"model": item["model"], "tier": tier, "attempt": item["attempt"],
                                                  "request_hash": item["request_hash"]})
            except ledger_mod.CapReached as e:
                return "cap", e
            if item["state"] == "reserved":  # resumed in `reserved` after its reservation was closed
                item = self.store.update(item["item_id"], reservation_id=rid, worst_micros=worst, reserved_tier=tier)
            else:
                item = self.store.transition(item["item_id"], "reserved", f"${worst / 1e6:.6f}", reservation_id=rid,
                                             worst_micros=worst, reserved_tier=tier)
        if self.cache.has(item["request_hash"]):
            item = self.store.transition(item["item_id"], "in_flight", "response already stored; nothing sent")
            self.ledger.void(rid, "response already stored; nothing sent")
            item = self.store.transition(item["item_id"], "received", "from the response cache", cached=True)
            self._process(item)
            return "cached", None
        self.store.transition(item["item_id"], "in_flight", f"sent at the {tier} tier",
                              sent_at=ledger_mod.now_iso())
        return "send", body

    def _send(self, item: dict, body: dict) -> CallResult:
        h = item["request_hash"]
        return self.client.call(body, timeout=self._timeout(item["tier"]), on_raw=lambda text: self.cache.write(h, text))

    def _refuse(self, item: dict, err: ledger_mod.CapReached) -> None:
        if err.scope in ("budget", "released"):
            self.stop(str(err))  # the items stay as they are for a later run
            return
        if item["state"] in ("planned", "unavailable"):
            item = self.store.transition(item["item_id"], "reserved", "reservation refused")
        self.store.transition(item["item_id"], "refused", str(err))
        self.out(f"{item['item_id']}: refused ({err})")

    def run_flex(self) -> None:
        """Send every pending non-Batch item, with retries, repairs and concurrency control."""
        for item in self.store.items(self.job):
            if item["state"] in TO_SEND and not item.get("chunk_id"):
                if item["tier"] == "batch":
                    self.batch_pending.append(item["item_id"])
                else:
                    self._push(item["item_id"])
        inflight: dict = {}
        with ThreadPoolExecutor(MAX_WORKERS) as pool:
            while self.heap or inflight:
                if self.stop_reason:
                    self.heap.clear()
                now = self.clock()
                cap_wait, next_wake = False, None
                while self.heap and not self.stop_reason and len(inflight) < self.workers:
                    ready, _, iid = self.heap[0]
                    if ready > now:
                        next_wake = ready
                        break
                    item = self.store.get(iid)
                    if item["state"] not in TO_SEND:
                        heapq.heappop(self.heap)
                        continue
                    if item["tier"] == "batch":
                        heapq.heappop(self.heap)
                        self.batch_pending.append(iid)
                        continue
                    tokens = item["est_input_tokens"] + item["max_output_tokens"]
                    w = self.throttle.wait_s(item["model"], tokens)
                    if w > 0:
                        next_wake = now + w
                        break
                    heapq.heappop(self.heap)
                    kind, payload = self._prepare(item)
                    if kind == "cap":
                        if inflight:  # held reservations may release when in-flight requests settle
                            self._push(iid, now)
                            cap_wait = True
                            break
                        self._refuse(item, payload)
                        continue
                    if kind == "cached":
                        continue
                    self.throttle.take(item["model"], tokens)
                    inflight[pool.submit(self._send, self.store.get(iid), payload)] = iid
                    self.sent += 1
                if inflight:
                    timeout = None if cap_wait or next_wake is None else max(0.05, min(1.0, next_wake - now))
                    done, _ = wait(list(inflight), timeout=timeout, return_when=FIRST_COMPLETED)
                    for fut in done:
                        self._finish(inflight.pop(fut), fut)
                elif self.heap and not self.stop_reason:
                    target = next_wake if next_wake is not None else self.heap[0][0]
                    delay = target - self.clock()
                    if delay > 0:
                        self.sleep(delay)

    def _finish(self, iid: str, fut) -> None:
        item = self.store.get(iid)
        failure = None
        try:
            r = fut.result()
        except ClientError as e:
            failure = e.kind
        except Exception as e:  # noqa: BLE001 - a failed cache write or anything unexpected after the send
            failure = type(e).__name__
        if failure == "missing_key":
            self.ledger.void(item["reservation_id"], "no key; nothing was sent")
            self.store.transition(iid, "unavailable", "no key")
            self.stop("OPENAI_API_KEY is not set in .env")
            return
        if failure is not None:
            self.ledger.settle_unknown(item["reservation_id"], f"client error {failure}; not proven unbilled")
            self._unknown(item, f"client error {failure}")
            self.stop(f"unexpected client error ({failure}); stopped so it cannot repeat")
            return
        self._handle(item, r)

    # Batch -----------------------------------------------------------------------------------------------------------

    def _queue_limit(self, model: str) -> int:
        limit = ((self.cfg.get("models") or {}).get(model) or {}).get("batch_queue_limit_tokens")
        return int(limit or self.cfg["api"].get("default_batch_queue_limit_tokens", 2_500_000))

    @staticmethod
    def _queued_tokens(item: dict) -> int:
        return math.ceil(item["est_input_tokens"] * float(prices.WORST_CASE_INPUT_FACTOR))

    def submit_batches(self, item_ids: list[str] | None = None) -> int:
        """Put pending Batch items into chunks whose enqueued input tokens stay under each model's queue limit
        (counting chunks already open). Returns the number of items enqueued; the rest wait for room."""
        ids = list(dict.fromkeys(item_ids if item_ids is not None else self.batch_pending))
        self.batch_pending = []
        items = [i for i in (self.store.get(x) for x in ids) if i and i["state"] in TO_SEND and not i.get("chunk_id")]
        enqueued = 0
        for model in sorted({i["model"] for i in items}):
            if self.stop_reason:
                break
            open_tokens = sum(c["input_tokens"] for c in self.store.chunks(open_only=True) if c["model"] == model)
            room = self._queue_limit(model) - open_tokens
            chunk, tokens = [], 0
            for item in (i for i in items if i["model"] == model):
                t = self._queued_tokens(item)
                if tokens + t >= room or len(chunk) >= BATCH_MAX_REQUESTS:
                    break
                chunk.append(item)
                tokens += t
            waiting = sum(1 for i in items if i["model"] == model) - len(chunk)
            if chunk and self._submit_chunk(model, chunk, tokens):
                enqueued += len(chunk)
            if waiting:
                self.out(f"{waiting} {model} items wait for room in the Batch queue (limit "
                         f"{self._queue_limit(model):,} input tokens); rerun generate after collect")
        return enqueued

    def _submit_chunk(self, model: str, items: list[dict], tokens: int) -> bool:
        chunk_id = f"{self.job}-{uuid.uuid4().hex[:10]}"
        wanted = []
        for item in items:
            worst = self._worst_micros(item, "batch")
            wanted.append((item["item_id"], self._usd(worst), {"model": model, "tier": "batch", "chunk_id": chunk_id,
                                                                "attempt": item["attempt"],
                                                                "request_hash": item["request_hash"]}))
        try:
            rids = self.ledger.reserve_many(self.job, wanted, ceiling_usd=self.budget)
        except ledger_mod.CapReached as e:
            self.out(f"Batch chunk of {len(items)} {model} items not submitted: {e}")
            return False
        self.store.add_chunk({"chunk_id": chunk_id, "job": self.job, "model": model, "state": "created",
                              "item_ids": [i["item_id"] for i in items], "input_tokens": tokens,
                              "created_at": self.now().isoformat(timespec="seconds")})
        lines = []
        for item, rid in zip(items, rids):
            body = self._body(item, "batch")
            self.store.transition(item["item_id"], "reserved", "Batch chunk", reservation_id=rid, tier="batch",
                                  chunk_id=chunk_id)
            self.store.transition(item["item_id"], "in_flight", f"enqueued in {chunk_id}")
            lines.append({"custom_id": item["item_id"], "method": "POST", "url": "/v1/responses", "body": body})
        try:
            batch = self.client.submit_batch(lines, metadata={"chunk_id": chunk_id, "job": self.job})
        except ClientError as e:
            if e.stage == "upload" or (e.status is not None and 400 <= e.status < 500):
                self._end_chunk_unsent(chunk_id, f"Batch submission refused ({e.kind}); nothing ran")
                self.out(f"chunk {chunk_id} was not created ({e.kind}); its items stay pending")
            else:
                self.out(f"chunk {chunk_id}: submission outcome unknown ({e.kind}); collect will look for it")
            return False
        self.store.chunk_transition(chunk_id, "submitted", batch_id=batch.get("id"),
                                    submitted_at=self.now().isoformat(timespec="seconds"))
        self.out(f"chunk {chunk_id}: {len(items)} {model} requests, about {tokens:,} input tokens, enqueued")
        return True

    def _end_chunk_unsent(self, chunk_id: str, why: str) -> None:
        chunk = next(c for c in self.store.chunks() if c["chunk_id"] == chunk_id)
        for iid in chunk["item_ids"]:
            item = self.store.get(iid)
            if item["state"] == "in_flight" and item.get("chunk_id") == chunk_id:
                self.ledger.void(item["reservation_id"], why)
                self.store.transition(iid, "unavailable", why, chunk_id=None)
        self.store.chunk_transition(chunk_id, "failed", finished=True, note=why)

    def collect(self) -> dict:
        """Poll this job's open chunks; settle finished ones line by line; cancel chunks older than 6 hours."""
        summary = {"open": 0, "finished": 0}
        for chunk in self.store.chunks(self.job, open_only=True):
            cid = chunk["chunk_id"]
            try:
                if chunk["state"] == "created":
                    found = self.client.find_batch(cid)
                    if found is None:
                        self._end_chunk_unsent(cid, "Batch never created")
                        continue
                    chunk = self.store.chunk_transition(cid, "submitted", batch_id=found["id"],
                                                        submitted_at=self.now().isoformat(timespec="seconds"))
                batch = self.client.retrieve_batch(chunk["batch_id"])
            except ClientError as e:
                self.out(f"chunk {cid}: could not read its status ({e.kind}); try collect again")
                summary["open"] += 1
                continue
            status = batch.get("status")
            if status in CHUNK_STATES and status != chunk["state"]:
                chunk = self.store.chunk_transition(cid, status)
            if status in CHUNK_END:
                self._finish_chunk(chunk, batch)
                summary["finished"] += 1
                continue
            summary["open"] += 1
            age = self.now() - datetime.fromisoformat(chunk["submitted_at"])
            if age > BATCH_MAX_AGE and status != "cancelling":
                try:
                    self.client.cancel_batch(chunk["batch_id"])
                    self.store.chunk_transition(cid, "cancelling")
                    self.out(f"chunk {cid}: unfinished after {age}; cancelling, its unfinished items go back to Flex")
                except ClientError as e:
                    self.out(f"chunk {cid}: cancel failed ({e.kind}); try collect again")
        return summary

    def _to_flex(self, iid: str, why: str) -> None:
        item = self.store.get(iid)
        self.ledger.void(item["reservation_id"], why)
        self.store.transition(iid, "unavailable", why, tier="flex", tries=0, chunk_id=None)

    def _finish_chunk(self, chunk: dict, batch: dict) -> None:
        cid = chunk["chunk_id"]
        pending = {iid for iid in chunk["item_ids"]
                   if (i := self.store.get(iid)) and i["state"] == "in_flight" and i.get("chunk_id") == cid}
        if batch.get("output_file_id"):
            for line in self.client.file_lines(batch["output_file_id"]):
                iid = line.get("custom_id")
                if iid not in pending:
                    continue
                pending.discard(iid)
                resp = line.get("response") or {}
                item = self.store.get(iid)
                if resp.get("status_code") == 200 and isinstance(resp.get("body"), dict):
                    self.cache.write(item["request_hash"], json.dumps(resp["body"]))
                    r = parse_response(resp["body"])
                    r.request_id = resp.get("request_id")
                    self._settle(item, r, tier="batch")
                else:
                    self._to_flex(iid, f"Batch line status {resp.get('status_code')}; not billed")
        if batch.get("error_file_id"):
            for line in self.client.file_lines(batch["error_file_id"]):
                iid = line.get("custom_id")
                if iid in pending:
                    pending.discard(iid)
                    code = (line.get("error") or {}).get("code") or (line.get("response") or {}).get("status_code")
                    self._to_flex(iid, f"Batch error {code}; not billed")
        for iid in sorted(pending):
            if batch.get("status") == "failed":
                self._to_flex(iid, "Batch failed validation; nothing ran")
            else:
                item = self.store.get(iid)
                self.ledger.settle_unknown(item["reservation_id"], f"no line for it in a {batch.get('status')} batch")
                self._unknown(item, f"no Batch line ({batch.get('status')})")
        self.store.chunk_transition(cid, chunk["state"], finished=True)
        self.out(f"chunk {cid}: {batch.get('status')}; settled")

    # whole runs ------------------------------------------------------------------------------------------------------

    def generate(self) -> dict:
        """resume, collect open chunks, send everything pending (Flex/Standard, then Batch), and report."""
        self.resume()
        self.collect()
        for item in self.store.items(self.job):  # --tier applies to everything still to send
            if item["state"] in TO_SEND and not item.get("chunk_id") and item["tier"] != self.tier:
                if self.tier != "flex" or item["tier"] == "standard":  # a Flex run keeps items moved to Batch there
                    self.store.update(item["item_id"], tier=self.tier)
        self.run_flex()
        if self.batch_pending and not self.stop_reason:
            self.submit_batches()
        return self.summary()

    def summary(self) -> dict:
        totals = self.ledger.totals()
        return {"job": self.job, "sent": self.sent, "states": self.store.counts(self.job),
                "open_chunks": len(self.store.chunks(self.job, open_only=True)),
                "job_totals": totals["jobs"].get(self.job), "stopped": self.stop_reason}
