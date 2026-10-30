"""The trail prompt: one brief in, one event trail and hidden true path out.

The wording and schema follow the "Trail writer" recipes in visuals/plan.html. Two
changes serve reliability: truth_segments.state is an enum in the schema, and the
prompt adds `not_collected` for a specimen whose draw never happens.

Request layout, for the prompt cache: the stable system text (rules, state list, fault
rules, two worked examples) comes first and carries the cache breakpoint; the single
brief is the last thing in the request. The brief sent to the model leaves out `label`,
because the window and the breach are not Claude's to see or to write.

Before any job, estimate_job() asks the API to count the real tokens and prices them from
the plan's PRICE table, and check_budget() refuses a job that would exceed --budget-usd.
Every call appends its usage to data/claude/usage.jsonl.

    python -m src.generator.prompt --seed 0 --run 0 --n 200 --budget-usd 0.25
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from . import briefs as briefs_mod

ROOT = Path(__file__).resolve().parents[2]
USAGE_LOG = ROOT / "data" / "claude" / "usage.jsonl"

MODEL = "claude-haiku-5-5"
EFFORT = "low"
MAX_TOKENS = 4096  # a trail is about 750 tokens; the rest is headroom against a truncated reply
EXPECTED_OUT_TOKENS = 750  # plan job J1; the pilot replaces it with the measured figure

# USD per million tokens, Anthropic list prices as of October 6, 2026 (the plan's PRICE table).
# cache_read is the rate for a cached prefix; a cache write costs CACHE_WRITE times the input rate.
PRICES = {
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "cache_read": 0.01},
    "claude-sonnet-5-5": {"in": 2.00, "out": 10.00, "cache_read": 0.20},
    "claude-opus-5-5": {"in": 4.00, "out": 20.00, "cache_read": 0.20},
}
OUT_ALLOWANCE = 1.15  # the plan's margin on estimated output tokens
CACHE_WRITE = 1.25
BATCH_DISCOUNT = 0.5

OBSERVED_ACTIVITIES = ("ordered", "collected", "received", "accessioned", "plated", "rejected")
ACTOR_ROLES = ("nurse", "phlebotomist", "courier", "processor", "technologist", "system")
TRUTH_STATES = ("at_unit", "in_tube", "at_station", "stalled_at_station", "misrouted", "hand_carry",
                "at_receiving_bench", "accessioned", "plated", "lost", "not_collected")

# The recipe's output schema, for output_config.format.
TRAIL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["specimen_id", "observed_events", "truth_segments", "outcome"],
    "properties": {
        "specimen_id": {"type": "string"},
        "observed_events": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["activity", "timestamp", "location", "actor_role"],
            "properties": {
                "activity": {"enum": list(OBSERVED_ACTIVITIES)},
                "timestamp": {"type": "string"},
                "location": {"type": "string"},
                "actor_role": {"enum": list(ACTOR_ROLES)},
                "note": {"type": "string"}}}},
        "truth_segments": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["state", "start", "end", "location"],
            "properties": {
                "state": {"enum": list(TRUTH_STATES)},
                "start": {"type": "string"},
                "end": {"type": ["string", "null"]},
                "location": {"type": "string"}}}},
        "outcome": {"type": "object", "additionalProperties": False,
                    "required": ["received", "plated", "fault"],
                    "properties": {"received": {"type": "boolean"}, "plated": {"type": "boolean"},
                                   "fault": {"type": "string"}}},
    },
}

INSTRUCTIONS = """\
You write the record of one synthetic specimen for a clinical microbiology research simulator. No real patient or institution is involved. Do not include names, ages, room numbers or anything that could identify a person.

You receive one brief as JSON. Every value under "fixed" and "targets" is binding.

Write two logs and keep them apart.

1. observed_events: only what a scanner or a person records. Allowed activities: ordered, collected, received, accessioned, plated, rejected. Never record a scan for a hidden stage (dispatch, tube, station). A specimen that never arrives has no received event.

2. truth_segments: where the specimen really was, from collected_at to the end, with no gaps and no overlaps. Allowed states: at_unit, in_tube, at_station, stalled_at_station, misrouted, hand_carry, at_receiving_bench, accessioned, plated, lost, not_collected. Each segment's length must match the targets within 2 minutes. A lost specimen ends in an open-ended lost segment.

Timestamps are ISO 8601, local time, to the second. Use only the locations listed in the brief. Notes are optional, at most two, at most 12 words each, plain and lab-realistic (for example "tube arrived with cap loose").

Return only JSON that matches the schema.\
"""

RULES = """\
How the targets become the two logs. A target is the minutes a stage lasts; null means that stage does not happen. Add them up from fixed.collected_at, and carry across midnight and month ends correctly.

Segments, in order, each starting where the last ended:
- at_unit: dispatch_wait_min, at the unit's collection location (the first entry in locations).
- in_tube: tube_transit_min, at the tube station for the unit.
- misrouted: misrouted_min, at tube_station_pharmacy (the carrier went to the wrong station and was re-sent).
- at_station: station_dwell_min, at tube_station_lab.
- stalled_at_station: stalled_min, at tube_station_lab.
- hand_carry: hand_carry_min, at the unit's collection location (a runner carries it; there is no tube).
- at_receiving_bench: accession_wait_min, at lab_receiving. It starts at the receipt scan.
- accessioned: plate_wait_min, at a lab bench. It starts at the accession scan.
- plated: open-ended (end null), at the same bench. It starts at the plating scan.
Skip every stage whose target is null. receipt_after_min is the minutes from collected_at to the received event, and equals the sum of the stages before receipt.

Observed events for a normal specimen: collected at collected_at (unit collection location, nurse or phlebotomist); received after receipt_after_min (lab_receiving, technologist or processor); accessioned after accession_wait_min more; plated after plate_wait_min more. An ordered event is optional: 5 to 60 minutes before collected_at, at the collection location.

Faults, by fixed planted_fault:
- none: as above.
- stalled_at_station: a stalled_at_station segment after at_station; the receipt follows it.
- misrouted: a misrouted segment after in_tube, then at_station; the receipt follows.
- lost_in_tube: dispatch_wait_min and tube_transit_min only. The specimen is then lost: an open-ended lost segment at the tube station for the unit. No received, accessioned or plated event. outcome.received is false.
- not_dispatched: collected, then it sits at the unit and is never sent. One open-ended at_unit segment from collected_at. Observed: ordered (optional) and collected only.
- never_collected: the draw never happens. Observed: a single ordered event at collected_at, which is the scheduled time. Truth: one open-ended not_collected segment from collected_at at the collection location.
- content_failure: timing is normal up to the receipt. After accession_wait_min the lab rejects the specimen (for example mislabeled or leaking): an observed rejected event instead of accessioned and plated, with a note giving the reason. The last truth segment is at_receiving_bench, ending at the rejection time. outcome.plated is false.

outcome.received is true when a received event exists. outcome.plated is true when a plated event exists. outcome.fault repeats planted_fault.

If the brief has a "validator_error", your previous reply for this brief failed that check. Fix it and return the whole trail again.\
"""

EXAMPLE_BRIEFS = (
    {
        "specimen_id": "SP-r00-000001",
        "fixed": {"type": "urine, unpreserved, room temp", "tests": ["culture"], "unit": "ed",
                  "priority": "routine", "route": "tube", "collected_at": "2026-03-04T02:14:00"},
        "planted_fault": "stalled_at_station",
        "targets": {"dispatch_wait_min": 7, "tube_transit_min": 4, "station_dwell_min": 10, "hand_carry_min": None,
                    "stalled_min": 46, "misrouted_min": None, "receipt_after_min": 67, "accession_wait_min": 9,
                    "plate_wait_min": 11},
        "locations": ["ed_collection", "tube_station_ed", "tube_station_lab", "lab_receiving", "lab_bench_1",
                      "lab_bench_2"],
    },
    {
        "specimen_id": "SP-r00-000002",
        "fixed": {"type": "tissue", "tests": ["Gram stain", "aerobic culture"], "unit": "icu",
                  "priority": "stat", "route": "tube", "collected_at": "2026-03-05T23:50:00"},
        "planted_fault": "lost_in_tube",
        "targets": {"dispatch_wait_min": 12, "tube_transit_min": 5, "station_dwell_min": None, "hand_carry_min": None,
                    "stalled_min": None, "misrouted_min": None, "receipt_after_min": None, "accession_wait_min": None,
                    "plate_wait_min": None},
        "locations": ["icu_collection", "tube_station_icu", "tube_station_lab", "lab_receiving", "lab_bench_1",
                      "lab_bench_2"],
    },
)

EXAMPLE_TRAILS = (
    {
        "specimen_id": "SP-r00-000001",
        "observed_events": [
            {"activity": "ordered", "timestamp": "2026-03-04T02:05:00", "location": "ed_collection",
             "actor_role": "system"},
            {"activity": "collected", "timestamp": "2026-03-04T02:14:00", "location": "ed_collection",
             "actor_role": "nurse", "note": "label printed at the bedside"},
            {"activity": "received", "timestamp": "2026-03-04T03:21:00", "location": "lab_receiving",
             "actor_role": "technologist", "note": "carrier found sitting in the station"},
            {"activity": "accessioned", "timestamp": "2026-03-04T03:30:00", "location": "lab_receiving",
             "actor_role": "technologist"},
            {"activity": "plated", "timestamp": "2026-03-04T03:41:00", "location": "lab_bench_1",
             "actor_role": "technologist"},
        ],
        "truth_segments": [
            {"state": "at_unit", "start": "2026-03-04T02:14:00", "end": "2026-03-04T02:21:00",
             "location": "ed_collection"},
            {"state": "in_tube", "start": "2026-03-04T02:21:00", "end": "2026-03-04T02:25:00",
             "location": "tube_station_ed"},
            {"state": "at_station", "start": "2026-03-04T02:25:00", "end": "2026-03-04T02:35:00",
             "location": "tube_station_lab"},
            {"state": "stalled_at_station", "start": "2026-03-04T02:35:00", "end": "2026-03-04T03:21:00",
             "location": "tube_station_lab"},
            {"state": "at_receiving_bench", "start": "2026-03-04T03:21:00", "end": "2026-03-04T03:30:00",
             "location": "lab_receiving"},
            {"state": "accessioned", "start": "2026-03-04T03:30:00", "end": "2026-03-04T03:41:00",
             "location": "lab_bench_1"},
            {"state": "plated", "start": "2026-03-04T03:41:00", "end": None, "location": "lab_bench_1"},
        ],
        "outcome": {"received": True, "plated": True, "fault": "stalled_at_station"},
    },
    {
        "specimen_id": "SP-r00-000002",
        "observed_events": [
            {"activity": "collected", "timestamp": "2026-03-05T23:50:00", "location": "icu_collection",
             "actor_role": "nurse", "note": "sent as an urgent tube"},
        ],
        "truth_segments": [
            {"state": "at_unit", "start": "2026-03-05T23:50:00", "end": "2026-03-06T00:02:00",
             "location": "icu_collection"},
            {"state": "in_tube", "start": "2026-03-06T00:02:00", "end": "2026-03-06T00:07:00",
             "location": "tube_station_icu"},
            {"state": "lost", "start": "2026-03-06T00:07:00", "end": None, "location": "tube_station_icu"},
        ],
        "outcome": {"received": False, "plated": False, "fault": "lost_in_tube"},
    },
)

BRIEF_KEYS = ("specimen_id", "fixed", "planted_fault", "targets", "locations")
EMPTY_BRIEF = {"specimen_id": "", "fixed": {}, "planted_fault": "none", "targets": {}, "locations": []}


class BudgetError(RuntimeError):
    pass


class TrailError(RuntimeError):
    pass


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def system_text() -> str:
    parts = [INSTRUCTIONS, RULES]
    for brief, trail in zip(EXAMPLE_BRIEFS, EXAMPLE_TRAILS):
        parts.append(f"Example brief:\n{_dump(brief)}\n\nExample reply:\n{_dump(trail)}")
    return "\n\n".join(parts)


def model_brief(brief: dict) -> dict:
    """What Claude sees: the specimen and its targets, never the window or the breach label."""
    return {key: brief[key] for key in BRIEF_KEYS}


def build_request(brief: dict, model: str = MODEL, error: str | None = None) -> dict:
    """Keyword arguments for client.messages.create. The brief is the last thing in the request.

    On a retry, `error` is the validator's text for the previous reply; it rides inside the brief.
    """
    sent = model_brief(brief) | ({"validator_error": error} if error else {})
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": system_text(), "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": _dump(sent)}],
        "thinking": {"type": "disabled"},
        "output_config": {"effort": EFFORT, "format": {"type": "json_schema", "schema": TRAIL_SCHEMA}},
    }


def count_request(request: dict) -> dict:
    """The same request minus max_tokens, which count_tokens does not take."""
    return {k: v for k, v in request.items() if k != "max_tokens"}


def make_client():
    """An Anthropic client with the key from .env. The key is never printed."""
    from dotenv import load_dotenv
    import anthropic

    load_dotenv(ROOT / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set: put it in .env (see .env.example)")
    return anthropic.Anthropic()


def token_counter(client, model: str = MODEL):
    """A function from a request to its input-token count, using the API's own tokenizer."""
    def count(request: dict) -> int:
        return client.messages.count_tokens(**count_request({**request, "model": model})).input_tokens
    return count


def estimate_job(count, briefs: list[dict], *, n: int | None = None, batch: bool = False,
                 out_tokens: int = EXPECTED_OUT_TOKENS, model: str = MODEL) -> dict:
    """Price a job of n trails from real token counts. `count` maps a request to input tokens.

    The prefix is the stable system text, counted once with an empty brief; the brief is the
    extra tokens of a handful of sample briefs. The first request writes the cache and the rest
    read it. Output tokens are an allowance until a pilot measures them.
    """
    price = PRICES[model]
    n = len(briefs) if n is None else n
    step = max(1, len(briefs) // 5)
    samples = briefs[::step][:5]
    prefix = count(build_request(EMPTY_BRIEF, model))
    brief_tokens = sum(count(build_request(b, model)) - prefix for b in samples) / len(samples)
    per_trail = (brief_tokens * price["in"] + prefix * price["cache_read"]
                 + out_tokens * OUT_ALLOWANCE * price["out"]) / 1e6
    cache_write = prefix * price["in"] * CACHE_WRITE / 1e6
    cost = (cache_write + n * per_trail) * (BATCH_DISCOUNT if batch else 1)
    return {"model": model, "n": n, "batch": batch, "prefix_tokens": prefix, "brief_tokens": round(brief_tokens, 1),
            "out_tokens": out_tokens, "cost_usd": cost}


def check_budget(estimate: dict, budget_usd: float) -> None:
    if estimate["cost_usd"] > budget_usd:
        raise BudgetError(f"estimated ${estimate['cost_usd']:.4f} exceeds the ${budget_usd:.2f} budget; "
                          "nothing was sent")


def format_estimate(estimate: dict) -> str:
    return (f"{estimate['n']} trails on {estimate['model']}{' (batch)' if estimate['batch'] else ''}: "
            f"{estimate['prefix_tokens']} cached prefix + {estimate['brief_tokens']} brief tokens in, "
            f"{estimate['out_tokens']} tokens out each; estimated ${estimate['cost_usd']:.4f}")


def usage_record(response, *, job: str, specimen_id: str, model: str = MODEL) -> dict:
    u = response.usage
    price = PRICES[model]
    cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
    cost = (u.input_tokens * price["in"] + cache_write * price["in"] * CACHE_WRITE
            + cache_read * price["cache_read"] + u.output_tokens * price["out"]) / 1e6
    return {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "job": job,
        "model": model,
        "specimen_id": specimen_id,
        "request_id": getattr(response, "_request_id", None),
        "stop_reason": response.stop_reason,
        "input_tokens": u.input_tokens,
        "cache_creation_input_tokens": cache_write,
        "cache_read_input_tokens": cache_read,
        "output_tokens": u.output_tokens,
        "cost_usd": round(cost, 8),
    }


def log_usage(record: dict, path: Path = USAGE_LOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def generate_trail(client, brief: dict, *, job: str, model: str = MODEL, log_path: Path = USAGE_LOG,
                   error: str | None = None) -> dict:
    """One brief in, one parsed trail out. The call's usage is logged even when the reply is unusable."""
    response = client.messages.create(**build_request(brief, model, error))
    log_usage(usage_record(response, job=job, specimen_id=brief["specimen_id"], model=model), log_path)
    if response.stop_reason != "end_turn":
        raise TrailError(f"{brief['specimen_id']}: stopped with {response.stop_reason!r}, not a complete trail")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise TrailError(f"{brief['specimen_id']}: reply is not JSON: {e}") from e


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Count tokens and price a trail-writing job. Sends no trail requests.")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--run", type=int, default=0)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--budget-usd", type=float, help="abort if the estimate is higher")
    ap.add_argument("--batch", action="store_true", help="price it through the Batch API")
    ap.add_argument("--out-tokens", type=int, default=EXPECTED_OUT_TOKENS)
    args = ap.parse_args(argv)

    briefs = briefs_mod.make_briefs(args.seed, args.run, args.n)
    estimate = estimate_job(token_counter(make_client()), briefs, batch=args.batch, out_tokens=args.out_tokens)
    print(format_estimate(estimate))
    if args.budget_usd is not None:
        check_budget(estimate, args.budget_usd)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BudgetError, RuntimeError) as e:
        sys.exit(f"error: {e}")
