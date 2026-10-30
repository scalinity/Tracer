"""Showcase stories for the ten candidate specimens in the demo bundle (plan task G3).

    python -m src.api.heroes --dry-run                 # price the job, change nothing
    python -m src.api.heroes --budget-usd 0.5          # ten stories, one at a time, written to docs/demo/heroes.json

For each candidate in docs/demo/bundle.json, Claude (`claude-sonnet-5-5`, the "Showcase scenario" recipe in
visuals/plan.html) writes a story of at most 90 words, a one-sentence takeaway and a question for the
audience, from the facts of that specimen: its timeline, what Tracer did, and the true path. A story is
accepted only if it passes the checks below (length, only numbers from the facts or the difference of two of them, no names, no medical advice,
one question). A failure is retried with the error text, twice at most, and a third failure stops the job.

The stories are kept in docs/demo/heroes.json with the hash of the candidate list they were written for;
src/api/export.py puts them in the bundle under `heroes` when the list still matches, and leaves `heroes`
empty when it does not, so a changed selection rule never shows a story about the wrong specimen.

The spend guard: do the dry run before any job. A job that estimates over $2, or a total that would pass
$10, is refused, and that is Daniel's call. The demo itself calls no API; the stories are a file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from src.api import export
from src.api.export import candidates_hash
from src.api.search_briefs import PLACE_WORDS, SpendGuardError, check_guard, numbers_in, spent_so_far
from src.generator import prompt

ROOT = Path(__file__).resolve().parents[2]
BUNDLE_PATH = export.DEFAULT_OUT
OUT_PATH = ROOT / "docs" / "demo" / "heroes.json"
MODEL = "claude-sonnet-5-5"
JOB = "J3"
EFFORT = "medium"
MAX_TOKENS = 1500
EXPECTED_OUT_TOKENS = 400
MAX_STORY_WORDS = 90
MAX_LINE_WORDS = 35
MAX_RETRIES = 2
NOTICE = f"Synthetic data with no clinical review. The stories are written by {MODEL} from the facts of each specimen."

SYSTEM = (
    "You write the story of one real simulated specimen for a mixed audience of students and clinicians. The input is its "
    "timeline, what Tracer did, and the true path. Return JSON with three fields: story (at most 90 words, past tense, no "
    "names), takeaway (one sentence), and question (one question the audience should ask). Use only facts in the input.\n\n"
    "Write plain American English with units on every number, and use only numbers that appear in the input or the difference of two of them. Say what "
    "happened and when, what Tracer did and how much warning that gave, and what the specimen was really doing. Do not "
    "name any person, and do not give medical advice. If Tracer did not alert, say so and why that is a limit, not a flaw.")

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["story", "takeaway", "question"],
          "properties": {"story": {"type": "string"}, "takeaway": {"type": "string"}, "question": {"type": "string"}}}

UNIT_WORDS = {"ed": "emergency department", "icu": "intensive care unit", "med_surg": "medical-surgical ward",
              "periop": "perioperative area", "clinic": "outpatient clinic"}
STATE_WORDS = {"at_unit": "waited at the collecting unit", "hand_carry": "was carried by a runner", "in_tube": "traveled in the pneumatic tube",
               "at_station": "was waiting at the laboratory's tube station", "stalled_at_station": "sat stalled at the laboratory's tube station",
               "misrouted": "was sent to the wrong tube station", "lost": "was lost in the tube system", "at_receiving_bench": "was at the receiving bench"}
FAULT_WORDS = {"none": "no fault: it simply took the time it took", "stalled_at_station": "it stalled at the laboratory's tube station",
               "not_dispatched": "it was left at the unit and never sent", "lost_in_tube": "it was lost in the tube",
               "misrouted": "it was sent to the wrong tube station and found later", "content_failure": "it was mislabeled or leaking and was rejected at accessioning"}
KIND_WORDS = {
    "clear_catch": "a breach Tracer warned about well ahead of the plan's simple rule",
    "stalled_then_found": "a specimen that stalled, was flagged, and was found in time",
    "never_sent": "a specimen left at the unit and never sent",
    "lost_in_tube": "a specimen lost in the tube",
    "misrouted_then_found": "a specimen sent to the wrong station",
    "false_alarm": "an alert on a specimen that arrived in time",
    "content_failure": "a content failure that Tracer cannot see",
    "borderline": "a close call at the edge of the window",
    "baseline_ahead": "a case where the plan's simple rule warned earlier than the risk model",
    "urgent_tier_catch": "an urgent specimen (lavage or tissue) caught with a long warning"}
SAFE_CAPITALS = {"Tracer", "I", "ED", "ICU", "BAL", "UTI", "IDSA", "ASM"}
MEDICAL = ("diagnos", "treat", "medication", "dose", "antibiotic", "therapy", "prescrib", "symptom")


# ---- the facts of one candidate ----

def facts_for(bundle: dict, candidate: dict) -> dict:
    s = next(x for x in bundle["specimens"] if x["id"] == candidate["id"])
    order, minute = bundle["place_order"], s["alerts"]["model"]
    window, received = s["window_min"], s["received_min"]
    # the clock runs from collection to receipt, so the scans after it (accessioning, plating) are left out
    timeline = [{"minute": round(e["at_min"]), "event": e["activity"].replace("_", " ")} for e in s["events"]
                if e["activity"] in ("collected", "received", "rejected")]
    path = [{"from_minute": round(a), "to_minute": None if b is None else round(b), "it": STATE_WORDS[state]}
            | ({} if b is None else {"for_minutes": round(b) - round(a)})
            for state, a, b in s["truth"]["segments"] if state in STATE_WORDS]
    out = {"what_makes_it_worth_telling": KIND_WORDS[candidate["kind"]], "specimen": {
               "type": s["type"], "collected_in": UNIT_WORDS.get(s["unit"], s["unit"]), "priority": s["priority"],
               "sent_by": "pneumatic tube" if s["route"] == "tube" else "a runner", "stability_window_minutes": window},
           "timeline_seen_by_the_laboratory": timeline, "true_path": path, "what_really_went_wrong": FAULT_WORDS[s["truth"]["fault"]],
           "outcome": ("never received" if received is None else f"received {round(received)} minutes after collection, "
                       + ("inside" if received <= window else "after") + " the window")}
    if received is not None and received > window:
        out["minutes_after_the_window_closed"] = round(received - window)
    if minute is None:
        out["what_tracer_did"] = {"alerted": False}
    else:
        probs = dict(zip(order, s["places"][minute // bundle["tick_min"] - 1]))
        shown = {p: v for p, v in probs.items() if p != "stalled_at_station"}
        shown["at_station"] += probs["stalled_at_station"]
        top = sorted(shown.items(), key=lambda kv: -kv[1])[:2]
        did = {"alerted": True, "alert_minute": minute, "minutes_of_warning": window - minute,
               "risk_percent_at_alert": round(100 * s["risk"][minute // bundle["tick_min"] - 1]),
               "most_likely_places_at_alert": [{"place": PLACE_WORDS[p], "percent": round(100 * v)} for p, v in top]}
        if received is not None:
            did["minutes_from_alert_to_receipt"] = round(received - minute)
        out["what_tracer_did"] = did
    plan = s["alerts"]["plan_baseline"]
    out["what_the_plans_simple_rule_did"] = ({"alerted": False} if plan is None else
                                             {"alerted": True, "alert_minute": plan, "minutes_of_warning": window - plan})
    if minute is not None and plan is not None and plan != minute:  # the one comparison a story is likely to make
        key = "tracer_alerted_this_many_minutes_earlier" if minute < plan else "tracer_alerted_this_many_minutes_later"
        out["what_the_plans_simple_rule_did"][key] = abs(plan - minute)
    return out


# ---- the request and the checks ----

def build_request(facts: dict, model: str = MODEL, error: str | None = None) -> dict:
    sent = facts | ({"validator_error": error} if error else {})
    return {"model": model, "max_tokens": MAX_TOKENS, "system": SYSTEM, "thinking": {"type": "between_tools"},
            "output_config": {"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
            "messages": [{"role": "user", "content": json.dumps(sent, sort_keys=True)}]}


def check_story(reply: dict, facts: dict) -> list[str]:
    """Why a story is not acceptable; empty when it is."""
    errors = []
    stated = numbers_in(facts)
    allowed = stated | {abs(a - b) for a in stated for b in stated}  # a difference of two stated numbers is a derived fact
    for field in ("story", "takeaway", "question"):
        text = reply.get(field)
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{field} is missing")
            continue
        if re.search(r"[*#_`]|\n", text.strip()):
            errors.append(f"{field} must be plain text on one line")
        stray = sorted({n for n in re.findall(r"\d+(?:\.\d+)?", text) if float(n) not in allowed}, key=float)
        if stray:
            errors.append(f"{field} has numbers not in the input: {', '.join(stray)}")
        if any(w in text.lower() for w in MEDICAL):
            errors.append(f"{field} gives medical advice")
        if re.search(r"\b(Dr|Mr|Ms|Mrs)\b\.?", text):
            errors.append(f"{field} names a person")
        mid = [w for w in re.findall(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]+\b", text) if w not in SAFE_CAPITALS]
        if mid:
            errors.append(f"{field} may name someone or something: {', '.join(sorted(set(mid)))}")
    story, takeaway, question = (reply.get(k) or "" for k in ("story", "takeaway", "question"))
    if len(story.split()) > MAX_STORY_WORDS:
        errors.append(f"story is {len(story.split())} words, at most {MAX_STORY_WORDS}")
    if len(takeaway.split()) > MAX_LINE_WORDS or len(re.findall(r"[.!?](?:\s|$)", takeaway.strip())) > 1:
        errors.append(f"takeaway must be one sentence of at most {MAX_LINE_WORDS} words")
    if len(question.split()) > MAX_LINE_WORDS or not question.strip().endswith("?") or question.count("?") != 1:
        errors.append(f"question must be one question of at most {MAX_LINE_WORDS} words")
    return errors


# ---- the cost, and running it ----

def estimate_job(count, all_facts: list[dict]) -> dict:
    price = prompt.PRICES[MODEL]
    in_tokens = sum(count(build_request(f)) for f in all_facts) / len(all_facts)
    per = (in_tokens * price["in"] + EXPECTED_OUT_TOKENS * prompt.OUT_ALLOWANCE * price["out"]) / 1e6
    return {"model": MODEL, "n": len(all_facts), "batch": False, "input_tokens": round(in_tokens, 1), "out_tokens": EXPECTED_OUT_TOKENS,
            "cost_usd": len(all_facts) * per}


def format_estimate(e: dict, spent: float) -> str:
    return (f"{e['n']} stories on {e['model']}: {e['input_tokens']} tokens in and {e['out_tokens']} out each; "
            f"estimated ${e['cost_usd']:.4f}; ${spent:.2f} spent so far")


def write_one(client, facts: dict, sid: str, *, log_path: Path) -> tuple[dict, int, float]:
    """One validated story: the reply, the retries it took, and its cost. A third failure raises."""
    error, cost = None, 0.0
    for attempt in range(MAX_RETRIES + 1):
        response = client.messages.create(**build_request(facts, error=error))
        record = prompt.usage_record(response, job=JOB, specimen_id=sid, model=MODEL)
        prompt.log_usage(record, log_path)
        cost += record["cost_usd"]
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        try:
            reply = json.loads(text)
            errors = check_story(reply, facts)
        except json.JSONDecodeError:
            reply, errors = {}, ["the reply is not valid JSON"]
        if not errors:
            return {k: reply[k].strip() for k in ("story", "takeaway", "question")}, attempt, cost
        error = "; ".join(errors)
    raise RuntimeError(f"{sid}: no acceptable story after {MAX_RETRIES + 1} tries: {error}")


def run_job(client, bundle: dict, out: Path, *, budget_usd: float, log_path: Path = prompt.USAGE_LOG) -> dict:
    candidates = bundle["showcase_candidates"]
    all_facts = [facts_for(bundle, c) for c in candidates]
    estimate = estimate_job(prompt.token_counter(client, MODEL), all_facts)
    print(format_estimate(estimate, spent_so_far(log_path)))
    check_guard(estimate, spent_so_far(log_path))
    prompt.check_budget(estimate, budget_usd)
    heroes, retries, cost = [], 0, 0.0
    for c, facts in zip(candidates, all_facts):
        reply, tries, spent = write_one(client, facts, c["id"], log_path=log_path)
        heroes.append({"id": c["id"], "kind": c["kind"]} | reply)
        retries, cost = retries + tries, cost + spent
        print(f"{c['kind']}: {c['id']}" + (f" (after {tries} retry)" if tries else ""))
    doc = {"notice": NOTICE, "model": MODEL, "prompt_hash": hashlib.sha256((SYSTEM + EFFORT).encode()).hexdigest()[:16],
           "candidates_hash": candidates_hash(candidates), "bundle_dataset_sha256": bundle["source"]["sha256"],
           "job": {"id": JOB, "stories": len(heroes), "retries": retries, "cost_usd": round(cost, 4)}, "heroes": heroes}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(f"{len(heroes)} stories written, {retries} retries, ${cost:.4f}")
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bundle", type=Path, default=BUNDLE_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    ap.add_argument("--dry-run", action="store_true", help="price the job and apply the spend guard; send nothing")
    ap.add_argument("--budget-usd", type=float, help="required for a real run: the most this job may cost")
    args = ap.parse_args(argv)
    bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
    client = prompt.make_client()
    if args.dry_run:
        estimate = estimate_job(prompt.token_counter(client, MODEL), [facts_for(bundle, c) for c in bundle["showcase_candidates"]])
        print(format_estimate(estimate, spent_so_far()))
        check_guard(estimate, spent_so_far())
        print("within the spend guard")
        return 0
    if args.budget_usd is None:
        sys.exit("error: a real run needs --budget-usd (run --dry-run first)")
    run_job(client, bundle, args.out, budget_usd=args.budget_usd)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, SpendGuardError, prompt.BudgetError) as e:
        sys.exit(f"error: {e}")
