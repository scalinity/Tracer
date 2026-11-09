"""The scenario prompt: one brief in, one laboratory-week configuration out (docs/neural/spec.md §4.4).

Request layout, for the prompt cache: the developer message is stable for a whole job and comes
first (role and purpose, what each field does in the simulator, bounds, consistency rules, how to
realize every brief field, realism guidance with every range marked as an assumption, one worked
example). The brief is the last thing in the request, as JSON; on a repair the validator's error
text follows it. The bounds list is generated from the full schema so the text cannot drift from it.

The prompt never names a real institution, never carries a window value from a workplace and never
asks the model to make numbers sum to anything: shares and weights are normalized in code.

    prompt_hash = sha256(developer text + canonical API schema + model + effort)[:16]
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from . import planner, schema

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_PATH = ROOT / "config" / "scenario.example.json"

# What the model sees of a brief. job, ood, origin and checks stay in code.
MODEL_BRIEF_KEYS = ("brief_id", "stratum", "archetype", "size_band", "transport_mix", "staffing", "failure_focus",
                    "edge_tags", "anchors", "quirk", "must_include", "must_avoid")

ROLE = """\
You write configuration files for a synthetic research simulator of clinical microbiology laboratories. Everything you write is invented. No real hospital, health system, laboratory, person or place is involved, and none may appear anywhere in your reply, including ids and notes. Do not write names of people, patient details, dates of birth, or names of real organizations. Describe the laboratory generically ("a mid-sized community hospital").

Purpose. A culture specimen is collected, travels to the laboratory by pneumatic tube, courier or hand, and is scanned as received at the laboratory's receiving desk. Between the collection scan and the receipt scan nobody records where it is. The research asks whether the state of the whole laboratory (queues, failures, staffing) tells a model something about which specimens will miss their stability window. Your configurations are the laboratories that the simulator then runs for a week.

Task. Each request holds one brief. Write one laboratory-week configuration as a single JSON object that follows the response schema exactly: every property present, no extra properties, null where a value does not apply. A deterministic simulator turns your configuration into specimen timelines, so every number you write changes what happens. A checker then rejects a reply that breaks a bound, a rule below, a name rule or its brief, or that is too close to another configuration. Write a laboratory that is coherent, realistic and different from the worked example.\
"""

SIMULATOR = """\
HOW THE SIMULATOR USES EACH FIELD

Calendar. A replicate runs Monday to Sunday (day 0 is Monday, day 6 is Sunday), preceded by a warm-up day. Times are local, 24 hours, no time zones. Day-of-week matters only through day numbers in disruptions, weekday versus weekend staffing, and weekend_factor.

archetype, size_band, transport_mix, staffing, failure_focus: copy them from the brief exactly. They are labels used to check your work and to slice the analysis; the simulator ignores them.

narrative (at most 600 characters): documentation only. Describe the laboratory and its main failure in plain sentences. No proper names of any kind. Do not write shift patterns such as 7-15-23 or any numeric range with dashes or slashes.

volume.specimens_per_weekday: the average number of specimens collected per weekday. It must lie inside the brief's size band. Arrivals are a Poisson process with this daily mean.
volume.hourly_profile: 24 non-negative numbers, hour 0 to hour 23, the relative arrival rate by hour. Only the shape matters; they are scaled to sum to 1. Do not make them sum to anything. A real profile has a morning peak and a quiet night; a flat profile is rejected.
volume.weekend_factor: Saturday and Sunday volume as a multiple of a weekday.
specimen_mix: 12 non-negative weights, one per specimen type ({mix_keys}). They are normalized, so only the proportions matter. They decide which stability window each specimen carries.
units[]: the collecting units (3 to 16), each with a unique unit_id. Each unit generates arrivals in proportion to volume_share (normalized; do not make shares sum to 1).
  kind sets defaults for the unit's mix. stat_fraction is the share of its specimens ordered stat. mix_tilt lists up to 6 {key, multiplier} entries that multiply chosen specimen_mix weights for this unit only (key is a specimen_mix key; for example an oncology clinic with more body fluids).
  route: tube (carried by pneumatic tube on tube_line_id), courier (picked up by courier_route_id), walk_in (carried by hand to the receiving desk), mixed (a share tube_share_if_mixed goes by tube on tube_line_id; the rest goes by courier_route_id if one is given, otherwise it is walked in). A clinic_offsite unit must use route courier on an offsite_van route.
  dispatch: the wait between collection and leaving the unit. immediate = a lognormal wait with the given median_min and p90_min (nine waits in ten are shorter than p90_min). batched = the unit holds specimens until its next sweep every batch_interval_min (stat specimens skip the sweep); median_min and p90_min still describe the wait to get ready.
  not_dispatched_rate: the chance a specimen is collected and then never sent (it sits at the unit forever). This is a fault, so keep it small.
tube_system.lines[]: pneumatic lines. transit_median_min and transit_p90_min give a lognormal travel time. misroute_rate is the chance a carrier lands at the wrong station and waits until it is found (misroute_found_min) and re-sent. lost_rate is the chance a carrier is lost for good.
tube_system.station_empty_interval_min: how often the laboratory's tube station is emptied, by shift (day, evening, night). Carriers wait for the next emptying, and emptying needs a receiving staff member on duty.
tube_system.stalled_rate and stall_extra_min: the chance a carrier is overlooked at the laboratory station and the extra wait it then suffers.
couriers.routes[]: courier or runner routes. kind onsite_runner and campus_loop serve buildings of one campus; offsite_van serves off-site clinics. round_interval_min is the time between rounds by shift (null means no service in that shift; a route needs service in at least one shift). A round is missed with missed_round_rate, may lose a specimen with lost_rate, and carries at most capacity_per_round specimens (the overflow waits for the next round). travel_median_min and travel_p90_min describe the trip.
couriers.on_call_runner: if true, stat specimens on non-van courier routes may be carried on demand after on_call_response_median_min (required when true, otherwise null).
receiving.staff_by_shift: receiving staff on duty by shift and day type (weekday, weekend). They are the servers of the receiving desk. Specimens are scanned as received when a server finishes them (scan_service_min, a lognormal). For shift_pattern 7-15-23 the shifts are day 07:00 to 15:00, evening 15:00 to 23:00, night 23:00 to 07:00; for 6-14-22 they are 06:00 to 14:00, 14:00 to 22:00, 22:00 to 06:00; for 7-19 there are only day 07:00 to 19:00 and night 19:00 to 07:00 (the evening value is unused, so write the day value or any number). Every shift that exists needs at least 1 staff member. shift_change_gap_min is the period after each change when capacity is halved.
processing: after receipt, accession and plating waits (lognormal, accession_min and plating_min). instruments[] go down downtime_per_week times a week for about downtime_median_min minutes; while one is down, staff_pull_from_receiving of the receiving capacity is diverted.
independent_faults: never_collected_rate (an ordered specimen that is never collected) and content_failure_rate (rejected at accessioning), as chances per specimen, independent of everything else.

disruptions[] (0 to 14): scheduled failures. Each has disruption_id, type, target_id, day (0 to 6), start (HH:MM), duration_min (10 to 720), severity (0.1 to 1), response, noticed_after_min (0 to 240) and recurs_daily. A disruption must end by 12:00 on the Monday after day 6. recurs_daily repeats the same window on every day of the week (use it for chronic patterns such as an unattended night station). Two disruptions of one type on one target combine by the larger severity. type decides target_id and effect:
  tube_line_outage: target a tube line id. Nothing moves on the line; carriers inside are stuck. After the staff notice (noticed_after_min), new specimens follow response: hold_at_unit (kept at the unit until the outage ends), divert_to_courier (sent by the unit's courier route or the on-call runner), stuck_in_system (sent anyway, so it jams). Severity raises the chance stuck carriers are lost. response must be one of these three.
  tube_line_degraded: target a tube line id. Transit times grow by a factor of 1 + 3 x severity and misroutes by 1 + 5 x severity.
  station_unattended: target "lab_station". No emptying of the laboratory tube station while active. Severity unused.
  receiving_short_staffed: target "receiving". Receiving servers drop by the share severity (at least one stays).
  courier_missed_rounds: target a courier route id. Each round is missed with probability at least the severity.
  courier_delayed: target a courier route id. Rounds run late by severity x interval and trips take longer by that factor.
  unit_surge: target a unit id. The unit's arrivals multiply by 1 + 3 x severity.
  unit_batching_hold: target a unit id. The unit sends specimens only in sweeps every 30 + 150 x severity minutes (stat specimens exempt).
  scanner_downtime: target "receiving". Receipt scans are deferred until the downtime ends (the specimens are served but not recorded). Use it only if the brief asks for it.
  instrument_down: target an instrument name from processing.instruments. Plating waits for the instrument, and receiving capacity falls by staff_pull_from_receiving.
  offsite_route_closed: target an offsite_van route id. No rounds run while closed.
For every type except tube_line_outage, response must be "none"; noticed_after_min matters only for outages (write 0 otherwise).

edge_tags: a list of tags from the brief (see below) that your configuration really realizes; it must include every tag in the brief and may not claim a tag your disruptions and units do not realize.
realism_notes (at most 400 characters): one or two plain sentences saying the values are assumptions. No names, no dates.\
"""

CONSISTENCY = """\
CONSISTENCY RULES (each is checked by code)
- Ids are lowercase letters, digits and underscores, start with a letter, and are unique across the whole configuration: no unit, line, route, instrument or disruption shares an id with anything. Do not use "lab_station" or "receiving" as ids. Ids must not contain organization names.
- Every tube_line_id and courier_route_id a unit names must exist. A unit on route tube or mixed needs a tube_line_id. A unit on route courier needs a courier_route_id. A mixed unit needs tube_share_if_mixed (0 to 1) and may omit courier_route_id. Other units use null for ids they do not use.
- A clinic_offsite unit uses route courier on an offsite_van route. Give every offsite_van route at least one unit that uses it.
- dispatch.mode batched needs batch_interval_min (15 to 240); immediate uses null.
- For every median/p90 pair, p90 is strictly greater than the median.
- Every shift of the chosen shift_pattern has at least 1 receiving staff member, weekday and weekend.
- Disruption targets must exist and match the type (see above). Disruptions stay inside days 0 to 6 plus 12 hours, counting every repeat of a recurring one.
- If on_call_runner is true, on_call_response_median_min is a number; if false it is null.
- If you write an instrument_down disruption, processing.instruments must contain that instrument.
- Do not write numbers that must add up. Shares, weights, the specimen mix and the hourly profile are normalized by code.\
"""

BRIEF_GUIDE = """\
HOW TO REALIZE THE BRIEF
The brief is JSON with these fields. Realize every one.
- stratum: normal = an ordinary week with only random faults and no disruptions; stressed = tight capacity (thin staffing, high load, a mild chronic pattern) with at most two short disruptions and no tube outage; disrupted = real failures, several disruptions led by the failure focus; edge = rare and compound events built from the edge tags.
- archetype: the kind of laboratory. academic_medical_center: many units (emergency, intensive care, oncology, perioperative), a large tube network, round-the-clock staffing. community_hospital: a mid-sized general hospital with an emergency department, wards and a few clinics. critical_access_rural: a small hospital with few units, one tube line or none, walk-in and a rural van, thin staffing. childrens_hospital: pediatric and neonatal units, small-volume specimens, much hand carrying. cancer_center: oncology clinics and infusion, many body fluids and tissue. multi_site_system: several campuses joined by vans, with a mix of every route. surgical_specialty: perioperative and procedure areas, tissue, swabs and fluids, a small emergency presence. safety_net_urban: a busy urban hospital with a heavy emergency department, a high stat share and tight staffing.
- size_band: specimens_per_weekday must be inside the band (small 30 to 120, medium 120 to 350, large 350 to 900). anchors.specimens_per_weekday suggests a value.
- transport_mix, staffing, failure_focus, edge_tags and the stratum have checks, listed in must_include and must_avoid; satisfy all of them.
{rules}
- anchors: numeric suggestions drawn by the planner (unit count, tube line count, courier route count, off-site vans, the day-shift courier interval, the shift pattern, and the weekday and hour at which the main disruption should start; disruption_day is 0 for Monday). Follow them unless a must_include sentence needs otherwise.
- quirk: one short texture line. Work it into the layout where it makes sense (for example by the narrative, a unit or a route), but never at the cost of a rule.
- must_include: sentences that code will check. Each is a hard requirement. Where a sentence names a minimum or a range, go clearly past the minimum (for example 70 minutes for a "45 to 120 minute" outage) so a rounding choice cannot fail it.
- must_avoid: things that must not appear. These are checked too.
- edge_tags: if present, build the disruption pattern each tag describes. A tag is satisfied only by the disruptions, units and rates it names, so write them explicitly.
Use failures of the kind the failure focus names, sized as a real laboratory would suffer them, and keep the rest of the laboratory healthy so the disruption is what stands out.\
"""

REALISM = """\
REALISM GUIDANCE
All ranges below are assumptions for a synthetic simulator, not measurements of any laboratory. They describe a typical laboratory; vary them with the archetype and stay inside the schema bounds.
- Assumed: dispatch median 3 to 20 minutes and p90 15 to 60 minutes for ward and emergency units; off-site clinics that batch use batch intervals of 60 to 240 minutes.
- Assumed: tube transit median 2 to 8 minutes, p90 6 to 20 minutes; misroute_rate 0.002 to 0.01; line lost_rate 0.0003 to 0.002; stalled_rate 0.005 to 0.03; stall_extra_min median 20 to 90, p90 90 to 300; misroute_found_min median 20 to 60, p90 60 to 150. Station emptying every 5 to 15 minutes by day, 10 to 30 by evening, 20 to 60 at night.
- Assumed: a campus loop runs every 20 to 60 minutes by day, every 45 to 120 by evening and every 60 to 240 at night (or not at night); an off-site van runs every 90 to 240 minutes and not at night; loop trips take 8 to 25 minutes, van trips 25 to 75; missed_round_rate 0.01 to 0.08; courier lost_rate 0.0002 to 0.002; capacity_per_round 20 to 80 for loops, 40 to 120 for vans.
- Assumed: receiving scan_service median 0.8 to 2 minutes, p90 2 to 6 minutes; shift_change_gap_min 10 to 30; a small laboratory has 1 to 2 staff per shift, a large one 3 to 10 by day.
- Assumed: accession median 5 to 20 minutes, plating median 10 to 30 minutes (they follow the receipt scan and do not change whether a specimen misses its window); 0 to 3 instruments, each down 0 to 2 times a week for 30 to 180 minutes; staff_pull_from_receiving 0 to 0.4.
- Assumed: never_collected_rate 0.002 to 0.01; content_failure_rate 0.005 to 0.03; not_dispatched_rate 0.001 to 0.01 for most units (a few units higher only when a tag asks).
- Assumed: weekend_factor 0.5 to 0.9 for a hospital with elective work, 0.8 to 1.1 for an emergency-heavy one. The hourly profile has a quiet night (about 1 unit per hour at hour 3), a rise from 05:00 and a peak between 07:00 and 11:00 (about 4 to 6 units), then a slow decline.
- Assumed: a disruption lasts 30 to 240 minutes for a typical event, up to 720 for a chronic one; severity 0.3 to 0.8 for a serious event; outages are noticed after 5 to 30 minutes unless the brief asks for slow notice.
- Typical units: an emergency department (high stat_fraction, tube), intensive care (high stat_fraction, lavage tilt), wards (low stat_fraction, mixed), perioperative (tissue and fluid tilt, courier), clinics (low stat_fraction, urine and swab tilt, off-site clinics by van and batched), oncology (body fluid tilt), dialysis and long-term care (batched, courier), pediatrics (walk-in or hand carry), labor and delivery (stat, few specimens).
- Specimen types (the public stability windows, in short): the twelve specimen_mix keys are urine (room temperature, refrigerated, boric acid), body fluid (room temperature, refrigerated, pleural anaerobic vial, culture bottle), bronchoalveolar lavage (room temperature, refrigerated), swabs (Stuart medium, liquid medium) and tissue. Room-temperature and unpreserved specimens have the shortest windows (roughly one to two hours), refrigerated or preserved ones last a day or more. Urine and swabs dominate a general hospital's volume; tissue and lavage are rarer and more urgent. A cancer center sends more fluid and tissue, a surgical hospital more tissue, an outpatient-heavy system more preserved urine.
- Vary the laboratory: pick different unit sets, line and route counts, shift patterns, rates and profile shapes from request to request. Do not copy the worked example's numbers.\
"""

CHECKLIST = """\
BEFORE YOU ANSWER
- The five label fields equal the brief's. specimens_per_weekday is inside the band.
- Every must_include sentence is satisfied with some margin and every must_avoid is respected.
- Every id is unique and every reference exists. Every shift has staff. Every p90 is above its median.
- Disruptions have the right target type, response only on outages, and end inside the week.
- edge_tags includes the brief's tags and claims nothing else.
- No names, dates, institutions or numeric ranges with dashes in the narrative or notes.
Return only the JSON object.\
"""


def _describe(node: dict) -> str:
    kind = node.get("type")
    nullable = isinstance(kind, list)
    base = next((t for t in kind if t != "null"), "null") if nullable else kind
    if "enum" in node:
        text = "one of " + ", ".join(node["enum"])
    elif base in ("number", "integer"):
        lo, hi, ex = node.get("minimum"), node.get("maximum"), node.get("exclusiveMinimum")
        text = base + (f", {lo} to {hi}" if lo is not None and hi is not None else f", above {ex}" + (f" up to {hi}" if hi is not None else ""))
    elif base == "string":
        if "[0-5][0-9]" in node.get("pattern", ""):
            text = "time HH:MM"
        elif "maxLength" in node:
            text = f"text, {node.get('minLength', 0)} to {node['maxLength']} characters"
        else:
            text = "id"
    elif base == "boolean":
        text = "true or false"
    else:
        text = base
    return text + (" or null" if nullable else "")


def bounds_lines(node: dict | None = None, path: str = "") -> list[str]:
    """One line per leaf of the full schema: where it is and what it may hold. Generated, so it matches the schema."""
    node = schema.full_schema() if node is None else node
    lines = []
    for name, sub in node.get("properties", {}).items():
        here = f"{path}.{name}" if path else name
        if sub.get("type") == "object":
            lines += bounds_lines(sub, here)
        elif sub.get("type") == "array":
            lo, hi = sub.get("minItems", 0), sub["maxItems"]
            lines.append(f"{here}: list of exactly {lo}" if lo == hi else f"{here}: list of {lo} to {hi}")
            inner = sub["items"]
            lines += bounds_lines(inner, here + "[]") if inner.get("type") == "object" else [f"{here}[]: {_describe(inner)}"]
        else:
            lines.append(f"{here}: {_describe(sub)}")
    return lines


@lru_cache(maxsize=1)
def example_json() -> str:
    return json.dumps(json.loads(EXAMPLE_PATH.read_text(encoding="utf-8")), separators=(",", ":"), ensure_ascii=False)


def _brief_rules() -> str:
    cfg = planner.config()
    out = ["  Rules for each value the planner can assign (code checks these):"]
    for group in ("transport_rules", "staffing_rules"):
        out += [f"  {key}: {rule['sentence']}" for key, rule in cfg[group].items()]
    out += [f"  edge tag {tag}: {rule['sentence']}" for tag, rule in cfg["edge_tags"].items()]
    return "\n".join(out)


@lru_cache(maxsize=1)
def _developer() -> str:
    bounds = "BOUNDS (generated from the schema; the response schema enforces what it can, and code checks the rest)\n" + \
        "\n".join(bounds_lines())
    example = ("WORKED EXAMPLE (a medium community hospital with a tube outage). It shows the shape and the level of detail. "
               "Do not copy its values, ids or wording; your laboratory must differ in layout and numbers.\n" + example_json())
    return "\n\n".join([ROLE, SIMULATOR.replace("{mix_keys}", ", ".join(schema.mix_keys())), bounds, CONSISTENCY, BRIEF_GUIDE.format(rules=_brief_rules()), REALISM, example, CHECKLIST])


def developer_text(job_or_model_settings=None) -> str:
    """The stable first message of every request. It is the same for every job and model, so one prompt cache serves all."""
    return _developer()


def model_brief(brief: dict) -> dict:
    return {k: brief[k] for k in MODEL_BRIEF_KEYS if k in brief}


def brief_text(brief: dict, repair_errors: str | None = None) -> str:
    """The brief as JSON, then (on a repair) the checker's reasons."""
    text = "Brief:\n" + json.dumps(model_brief(brief), ensure_ascii=False)
    if repair_errors:
        text += ("\n\nYour previous reply was rejected for these reasons:\n" + repair_errors.strip()
                 + "\nWrite the whole configuration again. Fix every item above and keep everything else valid.")
    return text


def build_messages(brief: dict, repair_errors: str | None = None) -> list[dict]:
    """Responses API input: the developer message, then the user message holding the brief."""
    return [{"role": "developer", "content": developer_text()},
            {"role": "user", "content": brief_text(brief, repair_errors)}]


def prompt_hash(model: str, effort: str) -> str:
    """Identifies the wording, schema, model and effort a scenario was written with."""
    text = developer_text() + schema.canonical(schema.api_schema()) + model + effort
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
