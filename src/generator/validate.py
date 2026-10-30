"""The nine checks that decide whether a generated trail is trusted.

The checks are the "Validator" recipe in visuals/plan.html. validate(brief, trail) runs them
and returns every failure; check 1 gates the rest, since they read fields it has proven exist.
generate_valid() wraps one trail request: on a failure it retries once with the error text
added to the brief, and on a second failure it drops the specimen and logs it.

Beyond the recipe, check 7 also holds the collection and receipt scans to the brief's
timing, because the observed log is what the models see.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from . import prompt

DROPPED_LOG = prompt.ROOT / "data" / "claude" / "dropped.jsonl"

SEGMENT_TOLERANCE_MIN = 2
RECEIPT_TOLERANCE_MIN = 1
MAX_NOTES = 2
MAX_NOTE_WORDS = 12

CHECK_NAMES = {1: "schema", 2: "event order", 3: "hidden stages", 4: "truth segments", 5: "segment lengths",
               6: "planted fault", 7: "receipt time", 8: "locations and notes", 9: "personal data"}

# The target that sets each segment's length. plated, lost and not_collected are open-ended.
SEGMENT_TARGET = {"at_unit": "dispatch_wait_min", "in_tube": "tube_transit_min", "misrouted": "misrouted_min",
                  "at_station": "station_dwell_min", "stalled_at_station": "stalled_min",
                  "hand_carry": "hand_carry_min", "at_receiving_bench": "accession_wait_min",
                  "accessioned": "plate_wait_min"}
TRANSIT_STATES = ("in_tube", "misrouted", "at_station", "stalled_at_station", "hand_carry")
NO_RECEIPT_FAULTS = ("lost_in_tube", "not_dispatched", "never_collected")
AFTER_RECEIPT = ("received", "accessioned", "plated", "rejected")
HIDDEN_LOCATION_PREFIX = "tube_station"  # a tube station is a hidden stage: nothing scans there

DOB_PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    r"\b(?:19|20)\d{2}[/-]\d{1,2}[/-]\d{1,2}\b",
    r"\b(?:dob|d\.o\.b\.?|date of birth|born)\b",
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2},? (?:19|20)\d{2}\b")]
# A title or role followed by a capitalized word: "Mr. Smith", "Nurse Patel", "patient Garcia".
TITLED_NAME = re.compile(r"\b(?i:mr|mrs|ms|miss|dr|prof|nurse|tech|technologist|courier|runner|patient|pt)\.?\s+[A-Z][a-z]+")
# Common first names that are not also ordinary words (so no Mark, Will, May, Grace, Rose).
FIRST_NAMES = frozenset("""
james john robert michael william david richard joseph thomas charles christopher daniel matthew anthony
donald steven paul andrew joshua kenneth kevin brian george timothy ronald edward jason jeffrey ryan
jacob gary nicholas eric jonathan stephen larry justin scott brandon benjamin samuel gregory alexander
patrick  raymond  dennis jerry tyler aaron jose adam nathan henry douglas zachary peter kyle
mary patricia jennifer linda elizabeth barbara susan jessica sarah karen nancy lisa betty margaret sandra
ashley kimberly emily donna michelle carol amanda melissa deborah stephanie rebecca sharon laura cynthia
kathleen amy angela shirley anna brenda pamela nicole samantha katherine christine helen debra rachel
carolyn janet maria heather diane olivia julie joyce victoria kelly christina lauren joan evelyn judith
megan cheryl andrea hannah jacqueline martha gloria teresa sara janice marie julia kathryn
garcia smith johnson williams  jones  davis rodriguez martinez hernandez lopez gonzalez wilson
anderson taylor moore jackson martin lee thompson  harris sanchez clark ramirez lewis robinson 
 allen   scott torres nguyen  flores  adams nelson   rivera campbell mitchell
carter roberts patel
""".split())

TS_FORMAT_HELP = "an ISO 8601 local time such as 2026-03-04T02:14:00"


@dataclass(frozen=True)
class Failure:
    check: int
    message: str

    def __str__(self) -> str:
        return f"check {self.check} ({CHECK_NAMES[self.check]}): {self.message}"


def error_text(failures: list[Failure]) -> str:
    return "; ".join(str(f) for f in failures)


def schema_errors(schema: dict, value, path: str = "reply") -> list[str]:
    """Errors against the subset of JSON Schema the trail schema uses."""
    if "enum" in schema:
        return [] if value in schema["enum"] else [f"{path} is {value!r}, not one of {schema['enum']}"]
    wanted = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
    kinds = {"string": str, "boolean": bool, "null": type(None), "object": dict, "array": list}
    if not isinstance(value, tuple(kinds[t] for t in wanted)):
        return [f"{path} must be {' or '.join(wanted)}"]
    errors: list[str] = []
    if isinstance(value, dict):
        errors += [f"{path} is missing {k!r}" for k in schema.get("required", []) if k not in value]
        if schema.get("additionalProperties") is False:
            errors += [f"{path} has an unexpected field {k!r}" for k in value if k not in schema["properties"]]
        for k, v in value.items():
            if k in schema.get("properties", {}):
                errors += schema_errors(schema["properties"][k], v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            errors += schema_errors(schema["items"], v, f"{path}[{i}]")
    return errors


def ts(text: str) -> datetime:
    return datetime.fromisoformat(text)


def minutes(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 60


def events(trail: dict, activity: str) -> list[dict]:
    return [e for e in trail["observed_events"] if e["activity"] == activity]


def check_schema(brief: dict, trail) -> list[str]:
    errors = schema_errors(prompt.TRAIL_SCHEMA, trail)
    if errors:
        return errors
    if trail["specimen_id"] != brief["specimen_id"]:
        errors.append(f"specimen_id is {trail['specimen_id']!r}, the brief's is {brief['specimen_id']!r}")
    stamps = [e["timestamp"] for e in trail["observed_events"]]
    stamps += [s["start"] for s in trail["truth_segments"]]
    stamps += [s["end"] for s in trail["truth_segments"] if s["end"] is not None]
    for stamp in stamps:
        try:
            if ts(stamp).tzinfo is not None:
                errors.append(f"timestamp {stamp!r} has a time zone; use {TS_FORMAT_HELP}")
        except ValueError:
            errors.append(f"timestamp {stamp!r} is not {TS_FORMAT_HELP}")
    if not trail["truth_segments"]:
        errors.append("truth_segments is empty")
    if not trail["observed_events"]:
        errors.append("observed_events is empty")
    return errors


def check_event_order(brief: dict, trail: dict) -> list[str]:
    evs, errors = trail["observed_events"], []
    stamps = [ts(e["timestamp"]) for e in evs]
    if any(b < a for a, b in zip(stamps, stamps[1:])):
        errors.append("observed_events are not in time order")
    names = [e["activity"] for e in evs]
    errors += [f"activity {a!r} appears {names.count(a)} times; at most once" for a in sorted(set(names))
               if names.count(a) > 1]
    if names[0] not in ("ordered", "collected"):
        errors.append(f"the first event is {names[0]!r}; it must be ordered or collected")
    return errors


def check_hidden_stages(brief: dict, trail: dict) -> list[str]:
    errors = [f"a {e['activity']} event is located at {e['location']!r}, a hidden stage that nothing scans"
              for e in trail["observed_events"] if e["location"].startswith(HIDDEN_LOCATION_PREFIX)]
    if brief["planted_fault"] in NO_RECEIPT_FAULTS:
        errors += [f"a {e['activity']} event exists, but the specimen never reaches the lab"
                   for e in trail["observed_events"] if e["activity"] in AFTER_RECEIPT]
    return errors


def check_segments(brief: dict, trail: dict) -> list[str]:
    segs, errors = trail["truth_segments"], []
    unknown = sorted({s["state"] for s in segs} - set(prompt.TRUTH_STATES))
    if unknown:
        errors.append(f"unknown states {unknown}")
    start = ts(brief["fixed"]["collected_at"])
    if ts(segs[0]["start"]) != start:
        errors.append(f"the first segment starts {segs[0]['start']}, not at collected_at {brief['fixed']['collected_at']}")
    for a, b in zip(segs, segs[1:]):
        if a["end"] is None:
            errors.append(f"the {a['state']} segment is open-ended but {b['state']} follows it")
        elif ts(a["end"]) != ts(b["start"]):
            errors.append(f"{a['state']} ends {a['end']} but {b['state']} starts {b['start']}: "
                          f"{'gap' if ts(a['end']) < ts(b['start']) else 'overlap'}")
    for s in segs:
        if s["end"] is not None and ts(s["end"]) <= ts(s["start"]):
            errors.append(f"the {s['state']} segment ends at or before it starts")
    return errors


def check_segment_lengths(brief: dict, trail: dict) -> list[str]:
    segs, targets, errors = trail["truth_segments"], brief["targets"], []
    for s in segs:
        key = SEGMENT_TARGET.get(s["state"])
        if key is None:
            continue
        target = targets[key]
        if target is None:
            if not (s["state"] == "at_unit" and s["end"] is None):  # an unsent specimen waits at the unit
                errors.append(f"a {s['state']} segment exists but {key} is null")
        elif s["end"] is None:
            errors.append(f"the {s['state']} segment is open-ended but {key} is {target}")
        else:
            length = minutes(ts(s["start"]), ts(s["end"]))
            if abs(length - target) > SEGMENT_TOLERANCE_MIN:
                errors.append(f"{s['state']} lasts {length:g} min but {key} is {target}")
    present = {s["state"] for s in segs}
    errors += [f"no {state} segment for {key} = {targets[key]}" for state, key in SEGMENT_TARGET.items()
               if targets[key] is not None and state not in present]
    return errors


def check_fault(brief: dict, trail: dict) -> list[str]:
    fault, out = brief["planted_fault"], trail["outcome"]
    segs = trail["truth_segments"]
    states = [s["state"] for s in segs]
    has = lambda activity: bool(events(trail, activity))
    errors = []
    if out["fault"] != fault:
        errors.append(f"outcome.fault is {out['fault']!r}, the planted fault is {fault!r}")
    receipt = brief["targets"]["receipt_after_min"] is not None
    if out["received"] != receipt:
        errors.append(f"outcome.received is {out['received']}, but receipt_after_min is "
                      f"{brief['targets']['receipt_after_min']}")
    if out["received"] != has("received"):
        errors.append("outcome.received does not match the received event")
    if out["plated"] != has("plated"):
        errors.append("outcome.plated does not match the plated event")

    if fault != "never_collected" and not has("collected"):
        errors.append("there is no collected event")
    if fault == "stalled_at_station" and "stalled_at_station" not in states:
        errors.append("a stall needs a stalled_at_station segment")
    if fault == "misrouted" and "misrouted" not in states:
        errors.append("a misroute needs a misrouted segment")
    if fault == "lost_in_tube" and not (states[-1] == "lost" and segs[-1]["end"] is None):
        errors.append("a lost specimen must end in an open-ended lost segment")
    if fault == "not_dispatched" and not (states == ["at_unit"] and segs[0]["end"] is None):
        errors.append("an unsent specimen is one open-ended at_unit segment")
    if fault == "never_collected":
        if not (states == ["not_collected"] and segs[0]["end"] is None):
            errors.append("an uncollected specimen is one open-ended not_collected segment")
        if has("collected") or not has("ordered"):
            errors.append("an uncollected specimen has an ordered event and no collected event")
    if fault == "content_failure":
        if not has("rejected") or has("accessioned") or has("plated"):
            errors.append("a content failure has a rejected event and no accessioned or plated event")
    elif receipt and not (has("accessioned") and has("plated") and not has("rejected")):
        errors.append("a specimen that completes has accessioned and plated events and no rejected event")
    if "lost" in states and fault != "lost_in_tube":
        errors.append("a lost segment appears without the lost_in_tube fault")
    if "not_collected" in states and fault != "never_collected":
        errors.append("a not_collected segment appears without the never_collected fault")
    return errors


def check_receipt(brief: dict, trail: dict) -> list[str]:
    errors = []
    start = ts(brief["fixed"]["collected_at"])
    for e in events(trail, "collected"):
        if abs(minutes(start, ts(e["timestamp"]))) > RECEIPT_TOLERANCE_MIN:
            errors.append(f"the collected event is at {e['timestamp']}, not at collected_at")
    for e in events(trail, "received"):
        at = ts(e["timestamp"])
        transit = [s for s in trail["truth_segments"] if s["state"] in TRANSIT_STATES]
        if not transit or transit[-1]["end"] is None:
            errors.append("a received event needs a closed transit segment before it")
            continue
        gap = minutes(ts(transit[-1]["end"]), at)
        if abs(gap) > RECEIPT_TOLERANCE_MIN:
            errors.append(f"received is {gap:+g} min from the end of the last transit segment "
                          f"({transit[-1]['state']})")
        expected = start + timedelta(minutes=brief["targets"]["receipt_after_min"] or 0)
        if abs(minutes(expected, at)) > SEGMENT_TOLERANCE_MIN:
            errors.append(f"received is at {e['timestamp']}, {minutes(expected, at):+g} min from "
                          f"collected_at + receipt_after_min")
    return errors


def check_locations_and_notes(brief: dict, trail: dict) -> list[str]:
    allowed, errors = set(brief["locations"]), []
    seen = [e["location"] for e in trail["observed_events"]] + [s["location"] for s in trail["truth_segments"]]
    errors += [f"location {loc!r} is not in the brief's list" for loc in sorted(set(seen) - allowed)]
    notes = [e["note"] for e in trail["observed_events"] if "note" in e]
    if len(notes) > MAX_NOTES:
        errors.append(f"{len(notes)} notes; at most {MAX_NOTES}")
    errors += [f"a note has {len(n.split())} words; at most {MAX_NOTE_WORDS}" for n in notes
               if len(n.split()) > MAX_NOTE_WORDS]
    return errors


def check_personal_data(brief: dict, trail: dict) -> list[str]:
    errors = []
    for e in trail["observed_events"]:
        note = e.get("note", "")
        errors += [f"note {note!r} has a date-of-birth pattern" for p in DOB_PATTERNS if p.search(note)][:1]
        if TITLED_NAME.search(note) or any(w in FIRST_NAMES for w in re.findall(r"[a-z]+", note.lower())):
            errors.append(f"note {note!r} contains a personal name")
    return errors


CHECKS = ((1, check_schema), (2, check_event_order), (3, check_hidden_stages), (4, check_segments),
          (5, check_segment_lengths), (6, check_fault), (7, check_receipt), (8, check_locations_and_notes),
          (9, check_personal_data))


def validate(brief: dict, trail) -> list[Failure]:
    """Every failed check, in order. An empty list means the trail is trusted."""
    failures: list[Failure] = []
    for number, check in CHECKS:
        found = check(brief, trail)
        failures += [Failure(number, message) for message in found]
        if number == 1 and found:
            break  # later checks read fields that check 1 has not proven exist
    return failures


def drop(brief: dict, errors: str, *, job: str, path: Path = DROPPED_LOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), "job": job,
           "specimen_id": brief["specimen_id"], "errors": errors}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def generate_valid(client, brief: dict, *, job: str, model: str = prompt.MODEL,
                   log_path: Path = prompt.USAGE_LOG, dropped_path: Path = DROPPED_LOG) -> dict | None:
    """A validated trail, or None after two failed attempts (the specimen is then logged and dropped)."""
    error = None
    for attempt in (1, 2):
        try:
            trail = prompt.generate_trail(client, brief, job=job, model=model, log_path=log_path, error=error)
            failures = validate(brief, trail)
            error = error_text(failures) if failures else None
        except prompt.TrailError as e:
            error = f"check 1 (schema): {e}"
        if error is None:
            return trail
    drop(brief, error, job=job, path=dropped_path)
    return None
