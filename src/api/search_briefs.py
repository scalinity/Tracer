"""Search briefs for every alert in the demo bundle: a fixed template and a Claude version (plan task M9).

    python -m src.api.search_briefs --dry-run                  # price the job, change nothing
    python -m src.api.search_briefs --try 5                    # five alerts, one at a time, printed for review
    python -m src.api.search_briefs --budget-usd 1.5 --wait    # one Batch call for every alert, then write the file

A brief tells the person who receives an alert where to look first, why that place, and how many minutes are
left. For each specimen the risk model alerts at the bundle's headline setting, the inputs are read from
docs/demo/bundle.json (its alert minute, risk, location probabilities, expected arrival and window), a fixed
template writes one brief from them, and Claude (`claude-sonnet-5-5`, the recipe in visuals/plan.html) writes
the other. Both go to docs/demo/briefs.json with a seeded blind key saying which one is "A", so the two can
be compared without knowing which is which. A Claude brief is accepted only if it passes the checks below
(at most 60 words, plain text, only numbers from the input, the first place named, the minutes left, no
claim of certainty, no medical advice). A failure is retried once with the error text, and a second failure
leaves that alert with the template alone.

The spend guard: do the dry run before any job. A job that estimates over $2, or a total that would pass $10,
is refused, and that is Daniel's call. The demo itself calls no API; these briefs are files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import time
from pathlib import Path

from src.api import export
from src.generator import prompt
from src.models import location, risk

ROOT = Path(__file__).resolve().parents[2]
BUNDLE_PATH = export.DEFAULT_OUT
OUT_PATH = ROOT / "docs" / "demo" / "briefs.json"
LEDGER_PATH = ROOT / "data" / "claude" / "search-briefs-batch.json"
MODEL = "claude-sonnet-5-5"
JOB = "J4"
EFFORT = "low"
MAX_TOKENS = 300
EXPECTED_OUT_TOKENS = 120  # plan job J4
MAX_WORDS = 60
JOB_LIMIT_USD = 2.0
TOTAL_LIMIT_USD = 10.0
BLIND_SEED = 1
POLL_SECONDS = 20
NOTICE = ("Synthetic data with no clinical review. The template brief is written by code and the Claude brief by "
          f"{MODEL}; which one is A is in each entry's blind_a.")

SYSTEM = (
    "You write a search brief for the person at the laboratory or on the ward who receives an alert about a specimen "
    "that has not been scanned in. The input is JSON: the person's role, the specimen type, the minutes since the "
    "last scan, the risk of missing the stability window in percent, the most likely places with probabilities in "
    "percent, the expected arrival in minutes from now (a median and a latest likely time), and the minutes left "
    "before the window closes.\n\n"
    "Write at most 50 words of plain text in three parts, in this order: where to look first, why that place, how "
    "many minutes are left. If a second place is close, you may name it.\n\n"
    "Use only the numbers in the input, written as they appear. Do not claim certainty. Do not give medical advice. "
    "Do not mention a model or an algorithm.\n\n"
    "Write one paragraph with no lists, headings or markdown, and name places in plain words.\n\n"
    "Example input: {\"role\": \"receiving technologist\", \"specimen_type\": \"urine\", \"minutes_since_last_scan\": 30, "
    "\"risk_percent\": 20, \"likely_places\": [{\"place\": \"the pneumatic tube\", \"percent\": 55}, {\"place\": \"the "
    "collecting unit\", \"percent\": 30}], \"expected_arrival_minutes_from_now\": {\"median\": 10, \"latest_likely\": 40}, "
    "\"minutes_left\": 45}\n"
    "Example brief: Check the pneumatic tube first, 55 percent. Nothing has scanned in for 30 minutes, which is late "
    "for a trip that should finish in about 10 more. The collecting unit is next, 30 percent. 45 minutes are left.")

# Where a specimen can be, in words a person on the ward or at the bench would use.
PLACE_WORDS = {
    "at_unit": "the collecting unit",
    "hand_carry": "the runner carrying it from the unit",
    "in_tube": "the pneumatic tube",
    "at_station": "the laboratory's tube station",
    "stalled_at_station": "the laboratory's tube station, where it may be stalled",
    "misrouted": "the wrong tube station it may have been sent to",
    "lost": "the tube system, where it may be lost",
}
PLACE_KEYWORDS = {
    "at_unit": ("unit",), "hand_carry": ("runner", "carr"), "in_tube": ("tube",), "at_station": ("station",),
    "stalled_at_station": ("station",), "misrouted": ("wrong", "station"), "lost": ("tube", "lost")}
CERTAINTY = ("definitely", "certainly", "for sure", "guarantee", "surely", "undoubtedly", "without doubt", "no doubt")
MEDICAL = ("patient", "treat", "diagnos", "medication", "dose", "antibiotic", "therapy", "prescrib", "symptom")


class SpendGuardError(RuntimeError):
    """A job over the guard. The next step is Daniel's word, not a retry."""


# ---- the inputs ----

def alert_inputs(bundle: dict) -> list[dict]:
    """One input per specimen the risk model alerts at the headline setting, in bundle order."""
    pooled, weights = bundle["model"]["pooled"], bundle["model"]["pooled"]["weights"]
    routing = bundle["triage"]["routing"]
    order = bundle["place_order"]
    out = []
    for s in bundle["specimens"]:
        minute = s["alerts"]["model"]
        if minute is None:
            continue
        i = minute // bundle["tick_min"] - 1
        probs = dict(zip(order, s["places"][i]))
        # the station and the stall are one place to a person looking for it, so their probabilities are added
        shown = {p: v for p, v in probs.items() if p != "stalled_at_station"}
        shown["at_station"] += probs["stalled_at_station"]
        words = dict(PLACE_WORDS)
        if probs["stalled_at_station"] >= 0.5 * shown["at_station"]:
            words["at_station"] = PLACE_WORDS["stalled_at_station"]
        top = [(p, v) for p, v in sorted(shown.items(), key=lambda kv: (-kv[1], order.index(kv[0]))) if round(100 * v) >= 1][:3]
        first_place = max(order, key=lambda p: probs[p])  # the alert's own most likely place, which routes it
        g = bundle["model"]["groups"][s["group"]]
        mix = risk.Mixture(((weights["normal"], g["mu"], g["sigma"]), (weights["stalled"], pooled["stalled_mu"], pooled["stalled_sigma"])),
                           weights["never"])
        role = routing["still_at_unit"] if first_place == "at_unit" else routing[s["route"]]
        out.append({
            "specimen_id": s["id"], "role": role, "specimen_type": s["type"], "minutes_since_last_scan": minute,
            "risk_percent": round(100 * s["risk"][i]),
            "likely_places": [{"place": words[p], "percent": round(100 * v)} for p, v in top],
            "expected_arrival_minutes_from_now": {"median": round(risk.arrival_quantile(minute, mix, 0.5)),
                                                  "latest_likely": round(risk.arrival_quantile(minute, mix, 0.9))},
            "minutes_left": s["window_min"] - minute, "_top": top[0][0]})
    return out


def model_input(inputs: dict) -> dict:
    """What the model sees: the inputs without the specimen id and the bookkeeping key."""
    return {k: v for k, v in inputs.items() if k not in ("specimen_id", "_top")}


def inputs_hash(items: list[dict]) -> str:
    return hashlib.sha256(json.dumps([model_input(i) | {"id": i["specimen_id"]} for i in items], sort_keys=True).encode()).hexdigest()[:16]


# ---- the template ----

def template_brief(inputs: dict) -> str:
    places = inputs["likely_places"]
    first = f"First check {places[0]['place']} ({places[0]['percent']}%)"
    first += f", then {places[1]['place']} ({places[1]['percent']}%)." if len(places) > 1 else "."
    return (f"{first} No scan for {inputs['minutes_since_last_scan']} min, and the risk of missing the window is "
            f"{inputs['risk_percent']}%. {inputs['minutes_left']} min left.")


# ---- the Claude brief ----

def build_request(inputs: dict, model: str = MODEL, error: str | None = None) -> dict:
    sent = model_input(inputs) | ({"validator_error": error} if error else {})
    return {"model": model, "max_tokens": MAX_TOKENS, "system": SYSTEM, "thinking": {"type": "between_tools"},  # Sonnet 5.5 rejects "disabled"; there are no tools here
            "output_config": {"effort": EFFORT}, "messages": [{"role": "user", "content": json.dumps(sent, sort_keys=True)}]}


def numbers_in(value) -> set[float]:
    if isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)):
        return {float(value)}
    if isinstance(value, dict):
        return set().union(*(numbers_in(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(numbers_in(v) for v in value)) if value else set()
    return set()


def check_brief(text: str, inputs: dict) -> list[str]:
    """Why a Claude brief is not acceptable; empty when it is."""
    errors = []
    words = text.split()
    if not text.strip():
        return ["the brief is empty"]
    if len(words) > MAX_WORDS:
        errors.append(f"{len(words)} words, at most {MAX_WORDS}")
    if "\n" in text.strip() or re.search(r"[*#_`>]|^\s*[-•]", text, re.M):
        errors.append("plain text in one paragraph only, with no markdown or lists")
    allowed = numbers_in(model_input(inputs))
    stray = sorted({n for n in re.findall(r"\d+(?:\.\d+)?", text) if float(n) not in allowed}, key=float)
    if stray:
        errors.append(f"numbers not in the input: {', '.join(stray)}")
    if f"{inputs['minutes_left']}" not in re.findall(r"\d+", text):
        errors.append(f"it does not give the {inputs['minutes_left']} minutes left")
    first = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0].lower()
    if not any(k in first for k in PLACE_KEYWORDS[inputs["_top"]]):
        errors.append("the first sentence does not name the most likely place")
    lower = text.lower()
    if any(w in lower for w in CERTAINTY):
        errors.append("it claims certainty")
    if any(w in lower for w in MEDICAL):
        errors.append("it gives medical advice")
    return errors


def read_text(response) -> str:
    return "".join(b.text for b in response.content if getattr(b, "type", "") == "text").strip()


def blind_a(specimen_id: str) -> str:
    """Which brief is shown as A, from a seeded coin per alert."""
    return "template" if random.Random(f"{BLIND_SEED}:{specimen_id}").random() < 0.5 else "claude"


# ---- the cost, and the guard ----

def spent_so_far(log_path: Path = prompt.USAGE_LOG) -> float:
    if not log_path.exists():
        return 0.0
    return sum(json.loads(line).get("cost_usd", 0.0) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip())


def estimate_job(count, items: list[dict]) -> dict:
    """The cost of a batch of one brief per alert, from real input-token counts and the plan's output allowance."""
    price = prompt.PRICES[MODEL]
    step = max(1, len(items) // 5)
    sample = items[::step][:5]
    in_tokens = sum(count(build_request(i)) for i in sample) / len(sample)
    per = (in_tokens * price["in"] + EXPECTED_OUT_TOKENS * prompt.OUT_ALLOWANCE * price["out"]) / 1e6
    return {"model": MODEL, "n": len(items), "batch": True, "input_tokens": round(in_tokens, 1), "out_tokens": EXPECTED_OUT_TOKENS,
            "cost_usd": len(items) * per * prompt.BATCH_DISCOUNT}


def check_guard(estimate: dict, spent: float, *, job_limit: float = JOB_LIMIT_USD, total_limit: float = TOTAL_LIMIT_USD) -> None:
    if estimate["cost_usd"] > job_limit:
        raise SpendGuardError(f"this job estimates ${estimate['cost_usd']:.2f}, over the ${job_limit:.2f} a job may cost: "
                              "stop and ask Daniel")
    if spent + estimate["cost_usd"] > total_limit:
        raise SpendGuardError(f"${spent:.2f} spent plus ${estimate['cost_usd']:.2f} for this job would pass ${total_limit:.2f}: "
                              "stop and ask Daniel")


def format_estimate(e: dict, spent: float) -> str:
    return (f"{e['n']} briefs on {e['model']} (batch): {e['input_tokens']} tokens in and {e['out_tokens']} out each; "
            f"estimated ${e['cost_usd']:.4f}; ${spent:.2f} spent so far")


# ---- running it ----

def log(response, specimen_id: str, *, job: str, batch: bool, log_path: Path) -> float:
    record = prompt.usage_record(response, job=job, specimen_id=specimen_id, model=MODEL, batch=batch)
    prompt.log_usage(record, log_path)
    return record["cost_usd"]


def try_some(client, items: list[dict], n: int, *, log_path: Path = prompt.USAGE_LOG) -> list[dict]:
    """The first n alerts, one at a time and at full price, for a person to read before the real job."""
    rows = []
    for item in items[:n]:
        response = client.messages.create(**build_request(item))
        text = read_text(response)
        cost = log(response, item["specimen_id"], job=JOB + "-test", batch=False, log_path=log_path)
        rows.append({"id": item["specimen_id"], "inputs": model_input(item), "template": template_brief(item), "claude": text,
                     "errors": check_brief(text, item), "cost_usd": cost})
    return rows


def wait_for(client, batch_id: str, poll: float = POLL_SECONDS):
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            return batch
        print(f"batch {batch_id}: {batch.processing_status}, {batch.request_counts.processing} still processing")
        time.sleep(poll)


def collect(client, batch_id: str, items: list[dict], *, log_path: Path = prompt.USAGE_LOG) -> dict:
    """The accepted Claude briefs by id, one retry for each that failed, and a count of what happened."""
    by_id = {i["specimen_id"]: i for i in items}
    accepted, failed, cost, counts = {}, {}, 0.0, {"first_try": 0, "retried": 0, "fell_back": 0, "batch_errors": 0}
    for entry in client.messages.batches.results(batch_id):
        item = by_id[entry.custom_id]
        if entry.result.type != "succeeded":
            counts["batch_errors"] += 1
            failed[entry.custom_id] = f"the batch reported {entry.result.type}"
            continue
        text = read_text(entry.result.message)
        cost += log(entry.result.message, entry.custom_id, job=JOB, batch=True, log_path=log_path)
        errors = check_brief(text, item)
        if errors:
            failed[entry.custom_id] = "; ".join(errors)
        else:
            accepted[entry.custom_id] = text
            counts["first_try"] += 1
    for sid in sorted(set(by_id) - set(accepted) - set(failed)):
        failed[sid] = "the batch returned nothing for it"
    for sid, why in sorted(failed.items()):
        response = client.messages.create(**build_request(by_id[sid], error=why))
        text = read_text(response)
        cost += log(response, sid, job=JOB, batch=False, log_path=log_path)
        if check_brief(text, by_id[sid]):
            counts["fell_back"] += 1
        else:
            accepted[sid] = text
            counts["retried"] += 1
    return {"accepted": accepted, "counts": counts, "cost_usd": cost}


def write_briefs(items: list[dict], result: dict, out: Path, *, bundle: dict, batch_id: str) -> dict:
    doc = {"notice": NOTICE, "model": MODEL, "prompt_hash": hashlib.sha256((SYSTEM + EFFORT).encode()).hexdigest()[:16],
           "inputs_hash": inputs_hash(items), "bundle_dataset_sha256": bundle["source"]["sha256"],
           "job": {"id": JOB, "batch_id": batch_id, "alerts": len(items), "claude_accepted": len(result["accepted"]),
                   **result["counts"], "cost_usd": round(result["cost_usd"], 4)},
           "blind": {"seed": BLIND_SEED, "meaning": "blind_a says which brief to show as A: the template or the claude brief"},
           "briefs": {i["specimen_id"]: {"inputs": model_input(i), "template": template_brief(i),
                                         "claude": result["accepted"].get(i["specimen_id"]), "blind_a": blind_a(i["specimen_id"])}
                      for i in sorted(items, key=lambda x: x["specimen_id"])}}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    return doc


def run_job(client, bundle: dict, out: Path, *, budget_usd: float, ledger_path: Path = LEDGER_PATH,
            log_path: Path = prompt.USAGE_LOG, wait: bool = True, poll: float = POLL_SECONDS) -> dict | None:
    items = alert_inputs(bundle)
    estimate = estimate_job(prompt.token_counter(client, MODEL), items)
    print(format_estimate(estimate, spent_so_far(log_path)))
    check_guard(estimate, spent_so_far(log_path))
    prompt.check_budget(estimate, budget_usd)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {}
    if ledger.get("inputs_hash") == inputs_hash(items) and ledger.get("batch_id"):
        batch_id = ledger["batch_id"]
        print(f"already submitted as {batch_id}")
    else:
        batch = client.messages.batches.create(requests=[{"custom_id": i["specimen_id"], "params": build_request(i)} for i in items])
        batch_id = batch.id
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(json.dumps({"batch_id": batch_id, "inputs_hash": inputs_hash(items), "alerts": len(items)}) + "\n", encoding="utf-8")
        print(f"submitted {len(items)} briefs as {batch_id}")
    if not wait:
        return None
    wait_for(client, batch_id, poll)
    result = collect(client, batch_id, items, log_path=log_path)
    doc = write_briefs(items, result, out, bundle=bundle, batch_id=batch_id)
    j = doc["job"]
    print(f"{j['claude_accepted']} of {j['alerts']} Claude briefs accepted ({j['first_try']} first try, {j['retried']} after a retry, "
          f"{j['fell_back']} left with the template), ${j['cost_usd']:.4f}")
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bundle", type=Path, default=BUNDLE_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    ap.add_argument("--dry-run", action="store_true", help="price the job and apply the spend guard; send nothing")
    ap.add_argument("--try", dest="try_n", type=int, help="write this many briefs one at a time, print them and stop")
    ap.add_argument("--budget-usd", type=float, help="required for a real run: the most this job may cost")
    ap.add_argument("--wait", action="store_true", help="wait for the batch and write the file")
    args = ap.parse_args(argv)
    bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
    items = alert_inputs(bundle)
    client = prompt.make_client()
    if args.dry_run:
        estimate = estimate_job(prompt.token_counter(client, MODEL), items)
        print(format_estimate(estimate, spent_so_far()))
        check_guard(estimate, spent_so_far())
        print("within the spend guard")
        return 0
    if args.try_n:
        for row in try_some(client, items, args.try_n):
            print(f"{row['id']}\n  template: {row['template']}\n  claude:   {row['claude']}\n  checks:   {row['errors'] or 'pass'}\n")
        return 0
    if args.budget_usd is None:
        sys.exit("error: a real run needs --budget-usd (run --dry-run first)")
    run_job(client, bundle, args.out, budget_usd=args.budget_usd, wait=args.wait)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, prompt.BudgetError) as e:
        sys.exit(f"error: {e}")
