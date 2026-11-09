"""The six layers that decide whether a written scenario is trusted (docs/neural/spec.md §4.8).

validate(scenario, brief) applies them in order and stops at the first layer that fails, so the
errors it returns are the ones a repair request carries:

  1 schema            the full schema of config/scenario.schema.json (src/scenarios/schema.py)
  2 semantics         ids, references, shift staffing, disruption targets, the brief's fixed fields
  3 names and safety  no personal names, dates of birth or real institutions
  4 plausibility      an injected callable that simulates the scenario (src/sim/kpi.py)
  5 brief adherence   the brief's include and avoid rules, each a named predicate below
  6 diversity         a near duplicate of a scenario already in the library is rejected

Layer 6 failures are not repairable: the later scenario is dropped, not rewritten. Every threshold
the predicates use is an assumption, written in config/scenario_planner.yaml or in a comment here.
Appendix C of the spec defines the edge tags; `tag_<name>` predicates implement them.

normalize() applies the one transformation code owes the simulator: shares and weights are scaled
to sum to 1, because the model is never asked to make numbers sum to anything.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Callable

from src.generator import validate as v1

from . import planner, schema

LAYERS = {1: "schema", 2: "semantics", 3: "names and safety", 4: "plausibility", 5: "brief adherence", 6: "diversity"}
RESERVED_IDS = ("lab_station", "receiving")
DAY_MIN = 1440
WEEK_END_MIN = 7 * DAY_MIN + 12 * 60       # disruptions end inside days 0 to 6 plus 12 hours
FLAT_PROFILE_SPREAD = 0.25                 # assumed: (max - min) / mean below this is a flat profile

# Shift clocks: (name, first hour, last hour). A shift whose last hour is before its first wraps midnight.
SHIFT_CLOCKS = {
    "7-15-23": (("day", 7, 15), ("evening", 15, 23), ("night", 23, 7)),
    "6-14-22": (("day", 6, 14), ("evening", 14, 22), ("night", 22, 6)),
    "7-19": (("day", 7, 19), ("night", 19, 7)),
}


@dataclass(frozen=True)
class Result:
    """ok; the first failing layer as "<number> <name>"; that layer's messages; whether a repair may help."""
    ok: bool
    layer: str | None = None
    errors: list[str] = field(default_factory=list)
    repairable: bool = True

    @property
    def number(self) -> int | None:
        return None if self.layer is None else int(self.layer.split()[0])

    def text(self) -> str:
        """The errors as one block, ready to append to a repair request."""
        return "\n".join(f"- {e}" for e in self.errors)


# --- Shared geometry --------------------------------------------------------------------------

def shift_of_hour(pattern: str, hour: float) -> str:
    for name, first, last in SHIFT_CLOCKS[pattern]:
        if (first <= hour < last) if first < last else (hour >= first or hour < last):
            return name
    raise ValueError(f"hour {hour} is outside every shift of {pattern}")


def used_shifts(pattern: str) -> list[str]:
    return [name for name, _, _ in SHIFT_CLOCKS[pattern]]


def shift_changes(pattern: str) -> list[int]:
    """Minutes of the day at which the receiving staff changes."""
    return [first * 60 for _, first, _ in SHIFT_CLOCKS[pattern]]


def night_windows(pattern: str) -> list[tuple[int, int]]:
    """Every night shift of the week as absolute minutes from day 0 00:00 (days -1 to 7)."""
    _, first, last = next(c for c in SHIFT_CLOCKS[pattern] if c[0] == "night")
    return [(d * DAY_MIN + first * 60, (d + 1) * DAY_MIN + last * 60) for d in range(-1, 8)]


def hhmm(text: str) -> int:
    return int(text[:2]) * 60 + int(text[3:5])


def occurrences(d: dict) -> list[tuple[int, int]]:
    """A disruption's windows as absolute minutes from day 0 00:00: one, or one per day if it recurs."""
    start, length = hhmm(d["start"]), d["duration_min"]
    days = range(7) if d["recurs_daily"] else [d["day"]]
    return [(day * DAY_MIN + start, day * DAY_MIN + start + length) for day in days]


def overlap(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def total_overlap(windows: list[tuple[int, int]], others: list[tuple[int, int]]) -> int:
    return max((sum(overlap(w, o) for o in others) for w in windows), default=0)


def peak_hours(profile: list[float], count: int) -> list[int]:
    """The busiest hours of the day, ties to the earlier hour."""
    return sorted(range(24), key=lambda h: (-profile[h], h))[:count]


def volume_shares(scenario: dict) -> list[float]:
    total = sum(u["volume_share"] for u in scenario["units"])
    return [u["volume_share"] / total for u in scenario["units"]]


def route_shares(scenario: dict) -> dict[str, float]:
    """Volume shares by route: tube, courier (all rounds and runners), walk_in, and offsite (van routes)."""
    routes = {r["route_id"]: r for r in scenario["couriers"]["routes"]}
    out = {"tube": 0.0, "courier": 0.0, "walk_in": 0.0, "offsite": 0.0}
    for unit, share in zip(scenario["units"], volume_shares(scenario)):
        tube = {"tube": 1.0, "mixed": unit["tube_share_if_mixed"] or 0.0}.get(unit["route"], 0.0)
        rest = share * (1 - tube)
        out["tube"] += share * tube
        if unit["route"] == "walk_in" or (unit["route"] == "mixed" and unit["courier_route_id"] not in routes):
            out["walk_in"] += rest
        elif unit["route"] in ("courier", "mixed"):
            out["courier"] += rest
            if routes.get(unit["courier_route_id"], {}).get("kind") == "offsite_van":
                out["offsite"] += rest
    return out


def classify_transport(shares: dict[str, float]) -> str:
    """The transport_mix label a set of route shares earns (used to label code-random scenarios)."""
    if shares["offsite"] >= 0.30:
        return "offsite_heavy"
    if shares["tube"] >= 0.70:
        return "tube_dominant"
    if shares["courier"] >= 0.60:
        return "courier_dominant"
    return "mixed"


def staff_sums(scenario: dict) -> tuple[float, float]:
    """Total receiving staff across the used shifts, weekday and weekend."""
    r = scenario["receiving"]
    names = used_shifts(r["shift_pattern"])
    return (sum(r["staff_by_shift"]["weekday"][n] for n in names), sum(r["staff_by_shift"]["weekend"][n] for n in names))


def classify_staffing(scenario: dict) -> str:
    """The staffing label a scenario earns, first match of chronic_short, weekend_skeleton, reduced_nights."""
    for pattern in ("chronic_short", "weekend_skeleton", "reduced_nights"):
        if _staffing(scenario, planner.config()["staffing_rules"][pattern]["params"]) is None:
            return pattern
    return "full_24_7"


# --- Layer 2: semantics -----------------------------------------------------------------------

def _duplicates(ids: list[str]) -> list[str]:
    return sorted({i for i in ids if ids.count(i) > 1})


def _id_errors(s: dict) -> list[str]:
    groups = {"unit": [u["unit_id"] for u in s["units"]], "tube line": [x["line_id"] for x in s["tube_system"]["lines"]],
              "courier route": [r["route_id"] for r in s["couriers"]["routes"]],
              "instrument": [i["name"] for i in s["processing"]["instruments"]],
              "disruption": [d["disruption_id"] for d in s["disruptions"]]}
    errors = [f"{kind} id {i!r} is used more than once" for kind, ids in groups.items() for i in _duplicates(ids)]
    every = [i for ids in groups.values() for i in ids]
    errors += [f"id {i!r} is shared by different kinds of things; every id must be unique across the scenario"
               for i in _duplicates(every) if not any(i in _duplicates(ids) for ids in groups.values())]
    errors += [f"{i!r} is reserved for disruption targets and cannot be an id" for i in RESERVED_IDS if i in every]
    return errors


def _unit_errors(s: dict) -> list[str]:
    lines = {x["line_id"] for x in s["tube_system"]["lines"]}
    routes = {r["route_id"]: r for r in s["couriers"]["routes"]}
    errors = []
    for u in s["units"]:
        who = f"unit {u['unit_id']!r}"
        if u["route"] in ("tube", "mixed") and u["tube_line_id"] not in lines:
            errors.append(f"{who} uses the tube but its tube_line_id {u['tube_line_id']!r} is not a line in tube_system.lines")
        elif u["tube_line_id"] is not None and u["tube_line_id"] not in lines:
            errors.append(f"{who} names tube_line_id {u['tube_line_id']!r}, which is not a line")
        if u["route"] == "courier" and u["courier_route_id"] not in routes:
            errors.append(f"{who} uses a courier but its courier_route_id {u['courier_route_id']!r} is not a route in couriers.routes")
        elif u["courier_route_id"] is not None and u["courier_route_id"] not in routes:
            errors.append(f"{who} names courier_route_id {u['courier_route_id']!r}, which is not a route")
        if u["route"] == "mixed" and u["tube_share_if_mixed"] is None:
            errors.append(f"{who} is mixed and needs tube_share_if_mixed")
        if u["kind"] == "clinic_offsite" and (u["route"] != "courier" or routes.get(u["courier_route_id"], {}).get("kind") != "offsite_van"):
            errors.append(f"{who} is an off-site clinic: its route must be courier on an offsite_van route")
        if u["dispatch"]["mode"] == "batched" and u["dispatch"]["batch_interval_min"] is None:
            errors.append(f"{who} is batched and needs dispatch.batch_interval_min")
        if u["dispatch"]["p90_min"] <= u["dispatch"]["median_min"]:
            errors.append(f"{who}: dispatch.p90_min must be greater than median_min")
    return errors


def _pair_errors(s: dict) -> list[str]:
    pairs = {"tube_system.stall_extra_min": s["tube_system"]["stall_extra_min"],
             "tube_system.misroute_found_min": s["tube_system"]["misroute_found_min"],
             "receiving.scan_service_min": s["receiving"]["scan_service_min"],
             "processing.accession_min": s["processing"]["accession_min"],
             "processing.plating_min": s["processing"]["plating_min"]}
    errors = [f"{name}: p90 must be greater than median" for name, p in pairs.items() if p["p90"] <= p["median"]]
    errors += [f"tube line {x['line_id']!r}: transit_p90_min must be greater than transit_median_min"
               for x in s["tube_system"]["lines"] if x["transit_p90_min"] <= x["transit_median_min"]]
    for r in s["couriers"]["routes"]:
        if r["travel_p90_min"] <= r["travel_median_min"]:
            errors.append(f"courier route {r['route_id']!r}: travel_p90_min must be greater than travel_median_min")
        if all(r["round_interval_min"][k] is None for k in ("day", "evening", "night")):
            errors.append(f"courier route {r['route_id']!r} has no service on any shift")
    return errors


def _staffing_errors(s: dict) -> list[str]:
    r = s["receiving"]
    errors = []
    for kind in ("weekday", "weekend"):
        for name in used_shifts(r["shift_pattern"]):
            if r["staff_by_shift"][kind][name] < 1:
                errors.append(f"receiving.staff_by_shift.{kind}.{name} is 0, but specimens arrive in that shift under "
                              f"shift_pattern {r['shift_pattern']}: every shift needs at least 1 receiving staff member")
    if s["couriers"]["on_call_runner"] and s["couriers"]["on_call_response_median_min"] is None:
        errors.append("couriers.on_call_runner is true, so on_call_response_median_min is required")
    return errors


def _volume_errors(s: dict, brief: dict | None) -> list[str]:
    profile = s["volume"]["hourly_profile"]
    errors = []
    if sum(profile) <= 0:
        errors.append("volume.hourly_profile must have a positive sum")
    elif (max(profile) - min(profile)) / (sum(profile) / 24) < FLAT_PROFILE_SPREAD \
            and not (brief or {}).get("anchors", {}).get("flat_profile"):
        errors.append("volume.hourly_profile is flat; real arrivals have a morning peak and a quiet night")
    if sum(s["specimen_mix"].values()) <= 0:
        errors.append("specimen_mix must have a positive sum")
    return errors


def _target_options(s: dict) -> dict[str, set[str]]:
    return {"line": {x["line_id"] for x in s["tube_system"]["lines"]},
            "route": {r["route_id"] for r in s["couriers"]["routes"]},
            "van": {r["route_id"] for r in s["couriers"]["routes"] if r["kind"] == "offsite_van"},
            "unit": {u["unit_id"] for u in s["units"]},
            "instrument": {i["name"] for i in s["processing"]["instruments"]}}


TARGET_OF_TYPE = {"tube_line_outage": "line", "tube_line_degraded": "line", "station_unattended": "lab_station",
                  "receiving_short_staffed": "receiving", "scanner_downtime": "receiving",
                  "courier_missed_rounds": "route", "courier_delayed": "route", "offsite_route_closed": "van",
                  "unit_surge": "unit", "unit_batching_hold": "unit", "instrument_down": "instrument"}
TARGET_WORDS = {"line": "a tube line id", "route": "a courier route id", "van": "an offsite_van route id",
                "unit": "a unit id", "instrument": "an instrument name", "lab_station": '"lab_station"',
                "receiving": '"receiving"'}


def _disruption_errors(s: dict, brief: dict | None) -> list[str]:
    options, errors = _target_options(s), []
    for d in s["disruptions"]:
        who = f"disruption {d['disruption_id']!r} ({d['type']})"
        want = TARGET_OF_TYPE[d["type"]]
        ok = d["target_id"] == want if want in ("lab_station", "receiving") else d["target_id"] in options[want]
        if not ok:
            errors.append(f"{who} has target_id {d['target_id']!r}; it must be {TARGET_WORDS[want]}")
        if d["type"] == "tube_line_outage" and d["response"] == "none":
            errors.append(f"{who} needs a response: hold_at_unit, divert_to_courier or stuck_in_system")
        if d["type"] != "tube_line_outage" and d["response"] != "none":
            errors.append(f"{who} must have response 'none'; only tube_line_outage has a response")
        if max(end for _, end in occurrences(d)) > WEEK_END_MIN:
            errors.append(f"{who} ends after day 6 plus 12 hours; start it earlier or shorten it")
        if d["type"] == "scanner_downtime" and not (brief and brief.get("ood") and brief.get("failure_focus") == "scanner_downtime"):
            errors.append(f"{who}: scanner_downtime is allowed only when the brief asks for it")
        if d["type"] == "instrument_down" and not s["processing"]["instruments"]:
            errors.append(f"{who} needs at least one entry in processing.instruments")
    return errors


def _brief_field_errors(s: dict, brief: dict) -> list[str]:
    errors = [f"{f} is {s[f]!r}, but the brief fixes it at {brief[f]!r}"
              for f in ("archetype", "size_band", "transport_mix", "staffing", "failure_focus") if s[f] != brief[f]]
    lo, hi = planner.config()["size_bands"]["specimens_per_weekday"][brief["size_band"]]
    volume = s["volume"]["specimens_per_weekday"]
    if not lo <= volume <= hi:
        errors.append(f"volume.specimens_per_weekday is {volume}, outside the {brief['size_band']} band of {lo} to {hi}")
    missing = [t for t in brief.get("edge_tags", []) if t not in s["edge_tags"]]
    if missing:
        errors.append(f"edge_tags must include {missing}")
    if brief.get("stratum") != "edge" and s["edge_tags"]:
        errors.append("edge_tags must be empty unless the brief is in the edge stratum")
    if len(set(s["edge_tags"])) != len(s["edge_tags"]):
        errors.append("edge_tags lists a tag more than once")
    return errors


def semantic_errors(scenario: dict, brief: dict | None = None) -> list[str]:
    """Layer 2. brief=None skips the brief's fixed fields (used for code-random scenarios)."""
    errors = (_id_errors(scenario) + _unit_errors(scenario) + _pair_errors(scenario) + _staffing_errors(scenario)
              + _volume_errors(scenario, brief) + _disruption_errors(scenario, brief))
    if brief is not None:
        errors += _brief_field_errors(scenario, brief)
    return errors


# --- Layer 3: names and safety ------------------------------------------------------------------

# v1's titled-name pattern also matches "technologist. On" at a sentence break, so roles need a
# following space (no period) and a lowercase role word; only the true abbreviations take a period.
TITLED_NAME = re.compile(r"\b(?i:mr|mrs|ms|miss|dr|prof)\.?\s+[A-Z][a-z]+|\b(?:nurse|tech|technologist|courier|runner|patient|pt)\s+[A-Z][a-z]+")


def _strings(node, path: str = ""):
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _strings(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _strings(v, f"{path}[{i}]")


def _denylist_pattern(term: str, flags: int) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z])" + re.escape(term) + r"(?![A-Za-z])", flags)


def name_errors(scenario: dict) -> list[str]:
    """Layer 3: personal names, dates of birth, institution-like names, the denylist."""
    cfg = planner.config()["names"]
    suffix = "|".join(re.escape(x) for x in sorted(cfg["institution_suffixes"], key=len, reverse=True))
    institution = re.compile(rf"\b([A-Z][A-Za-z'’-]+)\s+(?:{suffix})\b")
    stop = set(cfg["institution_stopwords"])
    errors: list[str] = []
    prose = {"narrative": scenario["narrative"], "realism_notes": scenario["realism_notes"]}
    for where, text in prose.items():
        errors += [f"{where} has a date-of-birth or date pattern ({m.group()!r}); write no dates or numeric ranges like 7-15-23"
                   for p in v1.DOB_PATTERNS for m in [p.search(text)] if m][:1]
        titled = TITLED_NAME.search(text)
        first = next((w for w in re.findall(r"[a-z]+", text.lower()) if w in v1.FIRST_NAMES), None)
        if titled or first:
            errors.append(f"{where} appears to contain a personal name ({(titled.group() if titled else first)!r}); "
                          "name no person")
        errors += [f"{where} appears to name an institution ({m.group()!r}); describe the laboratory generically"
                   for m in institution.finditer(text) if m.group(1) not in stop][:2]
    for where, text in _strings(scenario):
        plain = text.replace("_", " ")
        for term in cfg["denylist"]:
            if _denylist_pattern(term, re.IGNORECASE if where not in prose else 0).search(plain):
                errors.append(f"{where} contains {term!r}, a real institution; remove it")
    return errors


# --- Layer 5: brief adherence ---------------------------------------------------------------------
# Every predicate takes (scenario, params) and returns None when it holds or a short reason when it does not.

def _disruptions(s: dict, *types: str) -> list[dict]:
    return [d for d in s["disruptions"] if d["type"] in types]


def _near(d: dict, params: dict) -> bool:
    """Whether a disruption starts within the tolerance of the anchored weekday and hour."""
    if "hour" not in params:
        return True
    tolerance = params.get("hour_tolerance_h", 3) * 60
    start, target = hhmm(d["start"]), params["hour"] * 60
    if "day" not in params or d["recurs_daily"]:
        gap = abs(start - target)
        return min(gap, DAY_MIN - gap) <= tolerance
    return abs((d["day"] * DAY_MIN + start) - (params["day"] * DAY_MIN + target)) <= tolerance


def _has_disruption(s: dict, p: dict) -> str | None:
    found = _disruptions(s, *p["types"])
    if not found:
        return f"no {' or '.join(p['types'])} disruption"
    for d in found:
        if d["duration_min"] < p.get("min_duration_min", 0) or d["duration_min"] > p.get("max_duration_min", 10**6):
            continue
        if d["severity"] < p.get("min_severity", 0) or not _near(d, p):
            continue
        return None
    return "no " + "/".join(p["types"]) + " disruption has the required duration, severity and start time"


def _disruption_count(s: dict, p: dict) -> str | None:
    n = len(s["disruptions"])
    if n < p.get("min", 0) or n > p.get("max", 10**6):
        return f"{n} disruptions"
    return None


def _no_disruption_type(s: dict, p: dict) -> str | None:
    return f"a {p['type']} disruption is present" if _disruptions(s, p["type"]) else None


def _transport_share(s: dict, p: dict) -> str | None:
    share = route_shares(s)[p["kind"]]
    return None if share >= p["min"] else f"{p['kind']} carries {share:.0%}"


def _transport_mixed(s: dict, p: dict) -> str | None:
    sh = route_shares(s)
    if p["tube_min"] <= sh["tube"] <= p["tube_max"] and sh["courier"] >= p["courier_min"]:
        return None
    return f"tube carries {sh['tube']:.0%} and courier {sh['courier']:.0%}"


def _staffing(s: dict, p: dict) -> str | None:
    r, pattern = s["receiving"], p["pattern"]
    wd, we = r["staff_by_shift"]["weekday"], r["staff_by_shift"]["weekend"]
    weekday_total, weekend_total = staff_sums(s)
    # Assumed ratios (also written in the brief sentences of config/scenario_planner.yaml): night at least half of day
    # for full coverage, at most half for reduced nights, weekend at least 75% / at most 60% of weekday in total.
    if pattern == "full_24_7":
        ok = wd["night"] >= 0.5 * wd["day"] and weekend_total >= 0.75 * weekday_total
    elif pattern == "reduced_nights":
        ok = wd["night"] <= 0.5 * wd["day"] and wd["night"] < wd["day"]
    elif pattern == "weekend_skeleton":
        ok = weekend_total <= 0.6 * weekday_total
    else:
        profile = s["volume"]["hourly_profile"]
        per_hour = [s["volume"]["specimens_per_weekday"] * x / sum(profile) for x in profile]
        load = max(per_hour[h] / max(1, wd[shift_of_hour(r["shift_pattern"], h)]) for h in range(24))
        ok = load >= p["min_peak_per_staff"]
    return None if ok else f"the receiving staff does not fit the {pattern} pattern"


def _batching_present(s: dict, p: dict) -> str | None:
    batched = [u for u in s["units"] if u["dispatch"]["mode"] == "batched"
               and (u["dispatch"]["batch_interval_min"] or 0) >= p["min_interval_min"]]
    return None if batched or _disruptions(s, "unit_batching_hold") else "no unit batches its specimens"


def _instrument_coupling(s: dict, p: dict) -> str | None:
    ok = any(d["duration_min"] >= p["min_duration_min"] for d in _disruptions(s, "instrument_down")) \
        and s["processing"]["instruments"] and s["processing"]["staff_pull_from_receiving"] >= p["min_pull"]
    return None if ok else "no instrument_down disruption, or staff_pull_from_receiving is too low"


def courier_peak_load(s: dict, route: dict) -> float:
    """Expected specimens waiting for one round of the route in its busiest hour (the spec leaves the load to code)."""
    profile, daily = s["volume"]["hourly_profile"], s["volume"]["specimens_per_weekday"]
    pattern = s["receiving"]["shift_pattern"]
    served = 0.0
    for unit, share in zip(s["units"], volume_shares(s)):
        if unit["courier_route_id"] == route["route_id"] and unit["route"] in ("courier", "mixed"):
            served += share * (1 - ((unit["tube_share_if_mixed"] or 0.0) if unit["route"] == "mixed" else 0.0))
    loads = []
    for hour in range(24):
        interval = route["round_interval_min"][shift_of_hour(pattern, hour)]
        if interval is not None:
            loads.append(daily * served * profile[hour] / sum(profile) * interval / 60)
    return max(loads, default=0.0)


def _tag_outage_at_shift_change(s: dict, p: dict) -> str | None:
    window = planner.config()["thresholds"]["shift_change_window_min"]
    changes = shift_changes(s["receiving"]["shift_pattern"])
    for d in _disruptions(s, "tube_line_outage"):
        start = hhmm(d["start"])
        if any(min(abs(start - c), DAY_MIN - abs(start - c)) <= window for c in changes):
            return None
    return "no tube line outage starts within 30 minutes of a shift change"


def _tag_outage_during_peak(s: dict, p: dict) -> str | None:
    hours = peak_hours(s["volume"]["hourly_profile"], planner.config()["thresholds"]["peak_hours"])
    slots = [(day * DAY_MIN + h * 60, day * DAY_MIN + h * 60 + 60) for day in range(8) for h in hours]
    for d in _disruptions(s, "tube_line_outage"):
        if d["duration_min"] >= 45 and total_overlap(occurrences(d), slots):
            return None
    return "no tube line outage of 45 minutes or more overlaps the three busiest hours of the profile"


def _tag_unnoticed_outage(s: dict, p: dict) -> str | None:
    ok = any(d["noticed_after_min"] >= 60 for d in _disruptions(s, "tube_line_outage"))
    return None if ok else "no tube line outage has noticed_after_min of 60 or more"


def _tag_mass_casualty_surge(s: dict, p: dict) -> str | None:
    ed = {u["unit_id"] for u in s["units"] if u["kind"] == "ed"}
    ok = any(d["target_id"] in ed and d["severity"] >= 0.7 and d["duration_min"] >= 120 for d in _disruptions(s, "unit_surge"))
    return None if ok else "no unit surge on an ed unit with severity 0.7 or more for 120 minutes or more"


def _tag_weekend_courier_no_show(s: dict, p: dict) -> str | None:
    ok = any(d["severity"] >= 0.8 and (d["recurs_daily"] or d["day"] in (5, 6)) for d in _disruptions(s, "courier_missed_rounds"))
    return None if ok else "no courier_missed_rounds on day 5 or 6 with severity 0.8 or more"


def _tag_slow_degradation(s: dict, p: dict) -> str | None:
    ok = any(d["duration_min"] >= 360 and d["severity"] >= 0.4 for d in _disruptions(s, "tube_line_degraded"))
    return None if ok else "no tube_line_degraded for 360 minutes or more at severity 0.4 or more"


def _tag_chronic_non_dispatch_unit(s: dict, p: dict) -> str | None:
    ok = any(u["not_dispatched_rate"] >= 0.02 for u in s["units"])
    return None if ok else "no unit has not_dispatched_rate of 0.02 or more"


def _tag_misroute_storm(s: dict, p: dict) -> str | None:
    degraded = {d["target_id"] for d in _disruptions(s, "tube_line_degraded")}
    ok = any(x["misroute_rate"] >= 0.02 and x["line_id"] in degraded for x in s["tube_system"]["lines"])
    return None if ok else "no line with misroute_rate of 0.02 or more has a tube_line_degraded disruption"


def _tag_night_station_neglect(s: dict, p: dict) -> str | None:
    ok = any(d["recurs_daily"] and hhmm(d["start"]) <= 300 and d["duration_min"] >= 45
             for d in _disruptions(s, "station_unattended"))
    return None if ok else "no recurring station_unattended window starts between 00:00 and 05:00 for 45 minutes or more"


def _tag_holiday_skeleton(s: dict, p: dict) -> str | None:
    ok = any(d["severity"] >= 0.4 and d["duration_min"] >= 480 and (d["recurs_daily"] or d["day"] <= 4)
             for d in _disruptions(s, "receiving_short_staffed"))
    return None if ok else "no receiving_short_staffed at severity 0.4 or more for 480 minutes or more on a weekday"


def _tag_offsite_road_closure(s: dict, p: dict) -> str | None:
    ok = any(d["duration_min"] >= 180 for d in _disruptions(s, "offsite_route_closed"))
    return None if ok else "no offsite_route_closed disruption of 180 minutes or more"


def _tag_instrument_staff_drain(s: dict, p: dict) -> str | None:
    ok = s["processing"]["staff_pull_from_receiving"] >= 0.3 and any(d["duration_min"] >= 120 for d in _disruptions(s, "instrument_down"))
    return None if ok else "no instrument_down of 120 minutes or more with staff_pull_from_receiving of 0.3 or more"


def _tag_batching_night_unit(s: dict, p: dict) -> str | None:
    if any(u["dispatch"]["mode"] == "batched" and (u["dispatch"]["batch_interval_min"] or 0) >= 120 for u in s["units"]):
        return None
    nights = night_windows(s["receiving"]["shift_pattern"])
    if any(total_overlap(occurrences(d), nights) >= 240 for d in _disruptions(s, "unit_batching_hold")):
        return None
    return "no unit batches every 120 minutes or more, and no batching hold covers 4 night hours"


def _tag_compound_failure(s: dict, p: dict) -> str | None:
    need = planner.config()["thresholds"]["compound_overlap_min"]
    ds = s["disruptions"]
    for i, a in enumerate(ds):
        for b in ds[i + 1:]:
            if a["type"] != b["type"] and any(overlap(x, y) >= need for x in occurrences(a) for y in occurrences(b)):
                return None
    return "no two different disruption types overlap for 30 minutes or more"


def _tag_courier_capacity_crunch(s: dict, p: dict) -> str | None:
    ok = any(r["capacity_per_round"] < courier_peak_load(s, r) for r in s["couriers"]["routes"])
    return None if ok else "no courier route has capacity_per_round below its expected peak load per round"


def _tag_stat_heavy_icu_night(s: dict, p: dict) -> str | None:
    pattern = s["receiving"]["shift_pattern"]
    night = any(shift_of_hour(pattern, hhmm(d["start"]) / 60) == "night" for d in s["disruptions"])
    icu = any(u["kind"] == "icu" and u["stat_fraction"] >= 0.6 for u in s["units"])
    return None if icu and night else "no icu unit with stat_fraction of 0.6 or more and a disruption that starts at night"


def _tag_misroute_long_search(s: dict, p: dict) -> str | None:
    ok = s["tube_system"]["misroute_found_min"]["median"] >= 60
    return None if ok else "misroute_found_min median is under 60"


def _tag_scanner_backlog(s: dict, p: dict) -> str | None:
    ok = any(d["duration_min"] >= 60 for d in _disruptions(s, "scanner_downtime"))
    return None if ok else "no scanner_downtime of 60 minutes or more"


Predicate = Callable[[dict, dict], "str | None"]
PREDICATES: dict[str, Predicate] = {
    "has_disruption": _has_disruption, "disruption_count": _disruption_count,
    "no_disruption_type": _no_disruption_type, "transport_share": _transport_share,
    "transport_mixed": _transport_mixed, "staffing_pattern": _staffing, "batching_present": _batching_present,
    "instrument_coupling": _instrument_coupling,
    **{f"tag_{name[5:]}": fn for name, fn in globals().items() if name.startswith("_tag_")}}


def adherence_errors(scenario: dict, brief: dict) -> list[str]:
    """Layer 5: every include rule holds, every avoid rule is respected, every claimed edge tag is real."""
    errors = []
    for check in brief.get("checks", []):
        detail = PREDICATES[check["predicate"]](scenario, check["params"])
        if detail is not None:
            what = "Required" if check["kind"] == "include" else "Not allowed"
            errors.append(f"{what} by the brief: {check['sentence']}. Found: {detail}.")
    for tag in scenario["edge_tags"]:
        detail = PREDICATES[f"tag_{tag}"](scenario, {})
        if detail is not None and tag not in brief.get("edge_tags", []):
            errors.append(f"edge_tags lists {tag!r}, but the scenario does not realize it ({detail}); remove the tag or realize it")
    return errors


# --- The entry point --------------------------------------------------------------------------

def validate(scenario: dict, brief: dict, *, plausibility: Callable[[dict, str], list[str]] | None = None,
             library=None) -> Result:
    """Apply the six layers in order; the first failing layer's errors are the result.

    plausibility(scenario, stratum) returns messages (empty means plausible); None skips layer 4.
    library has duplicate_of(scenario, brief) returning a message or None; None skips layer 6.
    """
    def fail(number: int, errors: list[str], repairable: bool = True) -> Result:
        return Result(False, f"{number} {LAYERS[number]}", errors, repairable)

    errors = schema.errors(scenario)
    if errors:
        return fail(1, errors)
    for number, check in ((2, lambda: semantic_errors(scenario, brief)), (3, lambda: name_errors(scenario))):
        errors = check()
        if errors:
            return fail(number, errors)
    if plausibility is not None:
        errors = list(plausibility(scenario, brief["stratum"]))
        if errors:
            return fail(4, errors)
    errors = adherence_errors(scenario, brief)
    if errors:
        return fail(5, errors)
    if library is not None:
        message = library.duplicate_of(scenario, brief)
        if message:
            return fail(6, [message], repairable=False)
    return Result(True)


def normalize(scenario: dict) -> dict:
    """A copy with unit shares, the specimen mix and the hourly profile scaled to sum to 1."""
    s = copy.deepcopy(scenario)

    def scale(values: list[float]) -> list[float]:
        total = sum(values)
        return [round(x / total, 9) for x in values]

    mix = s["specimen_mix"]
    s["specimen_mix"] = dict(zip(mix, scale(list(mix.values()))))
    for unit, share in zip(s["units"], scale([u["volume_share"] for u in s["units"]])):
        unit["volume_share"] = share
    s["volume"]["hourly_profile"] = scale(s["volume"]["hourly_profile"])
    return s
