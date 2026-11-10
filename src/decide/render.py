"""Decision-model states and questions, rendered by deterministic templates (docs/neural/spec.md §11.3, §11.4).

    from src.decide import render
    text = render.alert_state(facts, tracernet={"p_miss": .62, "p_never": .2, "factors": [...]})   # X2
    text = render.alert_state(facts)                                                                # X3
    text = render.scenario_state(scenario, kpis)                                                    # X1

The same input gives the same text, and `state_hash` is what a job records. Every number a decision depends on is
computed by code (src/decide/facts.py) and stated beside its comparison ("1.5 times the usual 20 min"); a
probability carries its band (low below 20%, moderate 20% to 50%, high above 50%). Alert and tick states use
relative minutes only: no clock time, no date, no specimen, scenario or route identifier, nothing hidden from the
laboratory, and nothing from the scenario library beyond the roster. `check_alert_text` refuses a state that breaks
those rules, and every alert state passes through it. Choice questions always offer `other`. States stay short
because decision models lose accuracy as irrelevant input grows; `check_budget` refuses one over its token budget
(the local estimate: characters / 3.5).

X1 is the one job whose state describes a scenario's configuration: archetype, units by kind and route, transport,
the staffing table, disruptions in words and the plausibility measures of a test simulation in words. It leaves out
what X1 asks about (the planner's failure focus and edge tags) and the free text the generator wrote (narrative,
realism notes), which could name them.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

import yaml

from src.decide import rules

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "decision_model.yaml"
PLANNER_PATH = ROOT / "config" / "scenario_planner.yaml"
SCHEMA_PATH = ROOT / "config" / "scenario.schema.json"
CHARS_PER_TOKEN = 3.5

UNIT_WORDS = {"ed": "an emergency department", "icu": "an intensive care unit", "med_surg": "a medical-surgical unit",
              "periop": "a perioperative area", "oncology": "an oncology clinic", "clinic_offsite": "an off-site clinic"}
ROUTE_WORDS = {"tube": "sent by pneumatic tube", "courier": "carried by courier", "walk_in": "walked over by staff"}
ROUTE_NOUN = {"tube": "tube", "courier": "courier", "walk_in": "walk-in"}

CLOCK = re.compile(r"\b\d{1,2}:\d{2}\b")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}")
SPECIMEN_ID = re.compile(r"SP-r\d+", re.IGNORECASE)
SCENARIO_ID = re.compile(r"\b(?:S\d{4,}|dev-\d+|F\d{4,})\b")
HIDDEN_WORDS = ("truth", "planted", "fault", "breach", "outcome", "cause", "disruption", "substate", "scenario",
                "replicate", "library", "segment")


class RenderError(ValueError):
    """A state broke a rendering rule or its token budget."""


def load_config(path: Path = CONFIG_PATH) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def check_budget(text: str, budget: int, what: str) -> str:
    n = estimate_tokens(text)
    if n > budget:
        raise RenderError(f"the {what} state is about {n} tokens, over its budget of {budget}")
    return text


def state_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def band(p: float) -> str:
    return "low" if p < 0.2 else ("moderate" if p <= 0.5 else "high")


def pct(p: float) -> str:
    v = 100 * float(p)
    return f"{v:.0f}%" if v >= 1 or v == 0 else f"{v:.1f}%"


def _min(x: float) -> str:
    return f"{round(float(x)):,} min"


def _n(k: int, noun: str) -> str:
    return f"{k:,} {noun}" + ("" if k == 1 else "s")


def scrub_factor(text: str) -> str:
    """A TracerNet factor text without its clock time ('Saturday 14:05, 50 min to shift change' keeps the day and
    the minutes)."""
    return re.sub(r"\s+,", ",", CLOCK.sub("", text)).strip(" ,")


def check_alert_text(text: str) -> str:
    """Refuse an alert or tick state that holds a clock time, a date, an identifier or a hidden-side word."""
    low = text.lower()
    for pattern, what in ((CLOCK, "a clock time"), (DATE, "a date"), (SPECIMEN_ID, "a specimen id"),
                          (SCENARIO_ID, "a scenario or family id")):
        if pattern.search(text):
            raise RenderError(f"the state holds {what}")
    hit = [w for w in HIDDEN_WORDS if re.search(rf"\b{w}", low)]      # whole words: "because" is not "cause"
    if hit:
        raise RenderError(f"the state holds a word from the hidden side: {hit}")
    return text


# --- X2 and X3: an alert, or a tick ----------------------------------------------------------------------------------

def _specimen_line(f: dict) -> str:
    s = f["specimen"]
    tests = f" (tests: {', '.join(s['tests'])})" if s["tests"] else ""
    return (f"Specimen: {s['type']}{tests}; urgency tier {s['tier']} of 3 (1 is the most urgent); {s['priority']}; "
            f"from {UNIT_WORDS.get(s['unit_kind'], 'a unit')}; {ROUTE_WORDS.get(s['route_kind'], 'hand carried')}.")


def _time_line(f: dict) -> str:
    t, w, left = f["silence_min"], f["window_min"], f["left_min"]
    h = f["route_history"]
    where = "this route" if h and h["scope"] == "route" else "this kind of route"
    if h:
        ratio = t / h["p50"] if h["p50"] > 0 else math.inf
        faster = h["share_faster"]
        compare = ("longer than every past arrival" if faster >= 0.995 else
                   f"longer than {pct(faster)} of past arrivals")
        usual = (f"silent {_min(t)}, {ratio:.1f} times the usual {_min(h['p50'])} for {where} here and {compare} "
                 f"(9 in 10 arrive within {_min(h['p90'])}; {pct(h['never_share'])} never arrived)")
    else:
        usual = f"silent {_min(t)}; this laboratory has no history for {where}"
    left_text = f"{_min(left)} left of a {_min(w)} window" if left > 0 else f"the {_min(w)} window has closed"
    return f"Time: {usual}; {left_text}."


def _context_lines(f: dict) -> list[str]:
    lines = []
    m = f["mates"]
    if m["n"]:
        lines.append(f"Batch-mates: {m['received']} of {_n(m['n'], 'specimen')} sent from this unit by the same route "
                     f"within 20 min of it {'has' if m['received'] == 1 else 'have'} arrived.")
    else:
        lines.append("Batch-mates: no other specimen was sent from this unit by the same route within 20 min of it.")
    since, gap = f["route_last_receipt_min"], f["route_usual_gap_min"]
    usual = f"at this hour one usually arrives every {_min(gap)}" if gap else "at this hour usually none arrives"
    if since is None:
        lines.append(f"Route: no receipt via this route yet this week; {usual}.")
    else:
        ratio = f", {since / gap:.1f} times the usual gap" if gap else ""
        lines.append(f"Route: the last receipt via this route was {_min(since)} ago{ratio}; {usual}.")
    h = f["route_history"]
    if h:
        lines.append(f"Route: {_n(f['route_overdue'], 'other specimen')} on this route "
                     f"{'is' if f['route_overdue'] == 1 else 'are'} outstanding past its usual 90th percentile "
                     f"({_min(h['p90'])}).")
    lines.append(f"Unit: {_n(f['unit_outstanding'], 'other specimen')} from this unit "
                 f"{'is' if f['unit_outstanding'] == 1 else 'are'} outstanding.")
    if f["specimen"]["route_kind"] == "courier":
        c = f["courier_interval_min"]
        lines.append(f"Courier: rounds every {_min(c)} on this route this shift." if c else
                     "Courier: no scheduled rounds on this route this shift.")
    usual60 = f["lab_usual_receipts_60"]
    staff, span = f["staff_this_shift"], f["staff_range"]
    receipts = f"{_n(f['lab_receipts_60'], 'receipt')} in the last hour"
    receipts += (f" against a usual {usual60:.0f} at this hour of day" if usual60 is not None else "")
    staffing = ""
    if staff is not None:
        staffing = f"; {staff} receiving staff scheduled this shift"
        if span:
            staffing += f" ({span[0]} to {span[1]} across the week's shifts)"
    lines.append(f"Receiving: {receipts}{staffing}.")
    lines.append(f"Shift: the {f['shift']} shift on a {f['day_type']}, {_min(f['to_shift_change_min'])} to the next "
                 "shift change.")
    return lines


def alert_state(f: dict, tracernet: dict | None = None, *, budget: int | None = None) -> str:
    """X2's state (with TracerNet's p_miss, p_never and up to three factor texts) or X3's (tracernet None)."""
    lines = [_specimen_line(f), _time_line(f)]
    if tracernet is not None:
        pm, pn = float(tracernet["p_miss"]), float(tracernet["p_never"])
        lines.append(f"TracerNet: chance it misses its window {pct(pm)} ({band(pm)}); chance it never arrives "
                     f"{pct(pn)} ({band(pn)}).")
        reasons = [scrub_factor(t) for t in tracernet.get("factors") or []][:3]
        if reasons:
            lines.append("TracerNet's main reasons: " + "; ".join(f"({i}) {r}" for i, r in enumerate(reasons, 1)) + ".")
    lines.append("Context:")
    lines += [f"- {line}" for line in _context_lines(f)]
    text = check_alert_text("\n".join(lines))
    return check_budget(text, budget, "X2" if tracernet is not None else "X3") if budget else text


# Prompt p2 (validation round two; spec §8.7: prompt work uses validation only). Round one's answers chose
# check_receiving_queue for 67% of validation alerts and placed 30% in the laboratory against 6% truly there, so the
# action now says to act where the specimen most likely is, each option says when it fits, and the receiving queue's
# place says what a long silence usually means. The option keys are rules.PLACES and rules.ACTIONS unchanged.
X2_PLACE_CRITERIA = {**rules.PLACES, "in_lab_awaiting_scan": "Inside the laboratory, waiting in the receiving queue for "
                     "its receipt scan. Receiving usually scans a specimen within minutes of its arrival, so a long "
                     "silence seldom means this."}
X2_ACTION_CRITERIA = {
    "call_unit_confirm_sent": "Call the unit to confirm the specimen was sent: when it is probably still on the unit, "
                              "or sent and waiting there for a pickup.",
    "contact_courier_coordinator": "Contact the courier coordinator about the round carrying it: when it probably "
                                   "travels by courier or hand carry and is waiting for or on that round.",
    "check_lab_tube_station": "Check the laboratory's tube station: when it probably arrived by tube and sits at the "
                              "station unscanned.",
    "check_misroute_station": "Check the station where misrouted carriers end up: when it was probably sent by tube to "
                              "the wrong station.",
    "check_receiving_queue": "Check the receiving queue inside the laboratory: only when it has probably already reached "
                             "the laboratory.",
    "escalate_tube_system": "Report a tube system problem to facilities: when the tube system is probably failing or "
                            "holding it.",
    "request_recollection": "Ask the unit to collect the specimen again: when it is probably lost or cannot arrive in "
                            "time.",
    "wait_and_recheck": "Wait and check again at the next score: when it is probably moving normally and no call "
                        "would help yet.",
    "other": rules.ACTIONS["other"],
}
assert list(X2_ACTION_CRITERIA) == list(rules.ACTIONS) and list(X2_PLACE_CRITERIA) == list(rules.PLACES)


def x2_questions() -> dict:
    return {
        "place": {"type": "choice", "instructions": "Where is this specimen most likely to be right now?",
                  "criteria": dict(X2_PLACE_CRITERIA)},
        "action": {"type": "choice", "instructions": "What should the laboratory do first to recover this specimen "
                                                     "before its window closes? Act where the specimen most likely "
                                                     "is now.",
                   "criteria": dict(X2_ACTION_CRITERIA)},
        "shared_problem": {"type": "noul",
                           "instructions": "Is this delay likely part of a problem affecting other specimens on the "
                                           "same route or unit right now?",
                           "criteria": {"true": "Other specimens on the same route or unit are delayed by the same "
                                                "problem.",
                                        "false": "This specimen's delay is its own."}},
        "escalate": {"type": "noul",
                     "instructions": "Should a supervisor be paged now because this specimen is unlikely to be "
                                     "recovered within its window, or a system failure is likely?",
                     "criteria": {"true": "Page a supervisor now.",
                                  "false": "The receiving team can handle it without a supervisor."}},
    }


def x3_questions(left_min: float) -> dict:
    return {"miss": {"type": "noul",
                     "instructions": f"Will this specimen still be unreceived when its stability window closes in "
                                     f"{round(max(float(left_min), 0.0)):,} minutes?",
                     "criteria": {"true": "It will still be unreceived when the window closes.",
                                  "false": "It will be received before the window closes."}}}


# --- X1: a scenario's configuration ----------------------------------------------------------------------------------

PLAUSIBILITY_LEVELS = [
    "Not a plausible week for a U.S. hospital microbiology laboratory",
    "Implausible in several respects",
    "Plausible with notable oddities",
    "Plausible",
    "Highly plausible",
]

FAILURE_FOCUS = {
    "random_faults_only": "Only scattered independent faults; no planned problem dominates.",
    "tube_outage": "Tube line outages.",
    "tube_degradation": "A slowed or error-prone tube line.",
    "station_neglect": "The laboratory's tube station left unattended.",
    "courier_unreliable": "Missed, late or closed courier rounds.",
    "unit_batching": "Units holding specimens and sending them in batches.",
    "understaffing_spikes": "Receiving short of staff.",
    "surge": "A surge of specimens from a unit.",
    "instrument_coupling": "Instruments down, pulling staff away from receiving.",
    "compound": "Several different problems overlapping.",
    "scanner_downtime": "The receiving scanner down, so receipt scans are deferred.",
    "other": "Something else.",
}

DISRUPTION_WORDS = {
    "tube_line_outage": "tube line outage", "tube_line_degraded": "slowed tube line",
    "station_unattended": "unattended laboratory tube station", "receiving_short_staffed": "short-staffed receiving",
    "courier_missed_rounds": "missed courier rounds", "courier_delayed": "delayed courier rounds",
    "unit_surge": "surge of specimens", "unit_batching_hold": "unit batching hold",
    "scanner_downtime": "receiving scanner downtime", "instrument_down": "instrument down",
    "offsite_route_closed": "off-site route closed"}
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def edge_sentences(path: Path = PLANNER_PATH) -> dict[str, str]:
    """Each edge tag's sentence (Appendix C), from the planner's configuration."""
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return {tag: spec["sentence"] for tag, spec in cfg["edge_tags"].items()}


def failure_foci(path: Path = SCHEMA_PATH) -> list[str]:
    return list(json.loads(Path(path).read_text(encoding="utf-8"))["properties"]["failure_focus"]["enum"])


def x1_questions(edge_tags, sentences: dict[str, str]) -> dict:
    foci = failure_foci()
    criteria = {k: FAILURE_FOCUS.get(k, k.replace("_", " ")) for k in foci} | {"other": FAILURE_FOCUS["other"]}
    q = {"plausibility": {"type": "score",
                          "instructions": "How plausible is this configuration as one week of a U.S. hospital "
                                          "microbiology laboratory?",
                          "criteria": list(PLAUSIBILITY_LEVELS)},
         "dominant_failure": {"type": "choice", "instructions": "Which kind of problem dominates this week?",
                              "criteria": criteria}}
    for tag in sorted(edge_tags):
        q[f"edge_{tag}"] = {"type": "noul", "instructions": f"Does this week include the following: {sentences[tag]}?"}
    return q


def _num(v, digits: int = 0) -> str:
    if v is None:
        return "none"
    if isinstance(v, float) and digits:
        return f"{v:.{digits}f}"
    return f"{v:,}" if isinstance(v, int) else f"{v:g}"


def _rate(v) -> str:
    return "none" if v is None else pct(v)


def _shifts(d: dict | None) -> str:
    if not isinstance(d, dict):
        return "none"
    return ", ".join(f"{k} {_num(v)}" for k, v in d.items())


def scenario_state(s: dict, kpis: dict | None = None, *, budget: int | None = None) -> str:
    """X1's state: a code-written summary of one laboratory week's configuration and, when given, its plausibility
    measures from a test simulation."""
    vol = s["volume"]
    profile = vol.get("hourly_profile") or []
    busiest = sorted(range(len(profile)), key=lambda h: (-profile[h], h))[:3]
    lines = ["One simulated week of a hospital microbiology laboratory, as configured.",
             f"Hospital: {str(s.get('archetype', 'unknown')).replace('_', ' ')}; {_num(vol['specimens_per_weekday'])} "
             f"specimens per weekday, weekends at {pct(vol['weekend_factor'])} of that; busiest hours of the day "
             f"start at hour {', '.join(str(h) for h in sorted(busiest))}."]
    units = s.get("units") or []
    total_share = sum(float(u.get("volume_share") or 0) for u in units) or 1.0
    lines.append(f"Units ({len(units)}):")
    for u in units:
        d = u.get("dispatch") or {}
        via = {"tube": f"tube line {u.get('tube_line_id')}", "courier": f"courier route {u.get('courier_route_id')}",
               "walk_in": "walk-in",
               "mixed": f"tube line {u.get('tube_line_id')} ({pct(u.get('tube_share_if_mixed') or 0)}) or courier "
                        f"route {u.get('courier_route_id')}"}.get(u.get("route"), str(u.get("route")))
        batch = f", batched every {_num(d.get('batch_interval_min'))} min" if d.get("mode") == "batched" else ""
        lines.append(f"- {u['unit_id']}: {str(u['kind']).replace('_', ' ')}, {pct(float(u['volume_share']) / total_share)} of volume, "
                     f"{pct(u['stat_fraction'])} stat, by {via}; sent after a median {_num(d.get('median_min'))} min "
                     f"(90th percentile {_num(d.get('p90_min'))}){batch}; never sent {_rate(u.get('not_dispatched_rate'))}.")
    tube = s.get("tube_system") or {}
    for line in tube.get("lines") or []:
        lines.append(f"Tube line {line['line_id']}: transit median {_num(line['transit_median_min'])} min, 90th "
                     f"percentile {_num(line['transit_p90_min'])}; misrouted {_rate(line['misroute_rate'])}, lost "
                     f"{_rate(line['lost_rate'])}.")
    if tube:
        lines.append(f"Laboratory tube station: emptied every {_shifts(tube.get('station_empty_interval_min'))} min "
                     f"(by shift); carriers overlooked {_rate(tube.get('stalled_rate'))}, then waiting a median "
                     f"{_num((tube.get('stall_extra_min') or {}).get('median'))} min more; misrouted carriers found "
                     f"after a median {_num((tube.get('misroute_found_min') or {}).get('median'))} min.")
    couriers = s.get("couriers") or {}
    for r in couriers.get("routes") or []:
        lines.append(f"Courier route {r['route_id']} ({str(r['kind']).replace('_', ' ')}): rounds every "
                     f"{_shifts(r.get('round_interval_min'))} min (by shift); travel median "
                     f"{_num(r['travel_median_min'])} min, 90th percentile {_num(r['travel_p90_min'])}; rounds missed "
                     f"{_rate(r['missed_round_rate'])}, lost {_rate(r['lost_rate'])}; up to "
                     f"{_num(r['capacity_per_round'])} specimens a round.")
    if couriers:
        runner = (f"yes, median response {_num(couriers.get('on_call_response_median_min'))} min"
                  if couriers.get("on_call_runner") else "no")
        lines.append(f"On-call runner for stat specimens: {runner}.")
    rec = s.get("receiving") or {}
    staff = rec.get("staff_by_shift") or {}
    lines.append(f"Receiving staff by shift: weekday {_shifts(staff.get('weekday'))}; weekend "
                 f"{_shifts(staff.get('weekend'))}; shifts change at hours {rec.get('shift_pattern')}, with a "
                 f"{_num(rec.get('shift_change_gap_min'))}-min handover; a scan takes a median "
                 f"{_num((rec.get('scan_service_min') or {}).get('median'))} min.")
    proc = s.get("processing") or {}
    instruments = proc.get("instruments") or []
    lines.append(f"Processing: {len(instruments)} instruments, down about "
                 f"{_num(round(sum(i.get('downtime_per_week', 0) for i in instruments), 1))} times a week in all; "
                 f"an instrument down pulls {_rate(proc.get('staff_pull_from_receiving'))} of receiving staff away.")
    faults = s.get("independent_faults") or {}
    lines.append(f"Other faults: never collected {_rate(faults.get('never_collected_rate'))}, rejected for content "
                 f"{_rate(faults.get('content_failure_rate'))}.")
    events = s.get("disruptions") or []
    lines.append(f"Planned problems ({len(events)}):" if events else "Planned problems: none.")
    for e in events:
        extra = []
        if e.get("response") not in (None, "none"):
            extra.append(f"response: {str(e['response']).replace('_', ' ')}")
        if e.get("noticed_after_min"):
            extra.append(f"noticed after {_num(e['noticed_after_min'])} min")
        if e.get("recurs_daily"):
            extra.append("every day")
        day = DAYS[e["day"]] if isinstance(e.get("day"), int) and 0 <= e["day"] < 7 else f"day {e.get('day')}"
        lines.append(f"- {DISRUPTION_WORDS.get(e['type'], str(e['type']).replace('_', ' '))} on {e.get('target_id')}, "
                     f"{day} from hour {e.get('start')} for {_num(e.get('duration_min'))} min, severity "
                     f"{_num(e.get('severity'))}" + (f" ({'; '.join(extra)})" if extra else "") + ".")
    if kpis:
        rm = kpis.get("median_receipt_min") or {}
        by_route = ", ".join(f"{k.replace('_', '-')} {_num(round(v))}" for k, v in sorted(rm.items()) if v is not None)
        median = kpis.get("median_transit_min")
        lines.append(f"A test simulation of this week: median collection to receipt "
                     f"{_num(None if median is None else round(median))}"
                     f" min ({by_route}); {_rate(kpis.get('never_received_share'))} never received; "
                     f"{_rate(kpis.get('breach_share'))} miss their stability window; the busiest receiving shift is "
                     f"{_rate(kpis.get('max_shift_utilization'))} utilized; at most {_num(kpis.get('peak_silent'))} "
                     f"specimens silent at once; {_num(kpis.get('weekday_volume'))} specimens on an average weekday.")
    text = "\n".join(lines)
    return check_budget(text, budget, "X1") if budget else text
