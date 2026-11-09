"""The v2 trail validator: every invariant of spec §5.9, checked on one simulated replicate.

validate(result) returns one plain-English line per failure (empty means the replicate holds).

Record level, for every specimen:
- observed events are in time order, each activity at most once, the first is `ordered` or
  `collected`, and nothing is scanned at a hidden location (a tube station or a courier);
- truth segments start at collected_at and are contiguous, with no gaps or overlaps; only a lost
  specimen, a never-sent one, a never-collected one, the final post-receipt segment and the last
  segment of a horizon-cut specimen may be open-ended; every substate belongs to its state (spec
  §5.4); every location is in the brief's list;
- the `received` event is within one second of the end of the last segment before receipt;
- faults agree with the trail: a specimen that was sent has no receipt if and only if it is lost
  or horizon-cut, `not_dispatched` is one open-ended at_unit segment, a content failure has
  `rejected` and no `accessioned` or `plated`, and only a content failure is rejected;
- a horizon-cut specimen (`v2.horizon_cut`, spec A-05) has no receipt, is not lost, and ends in an
  open-ended pre-receipt segment entered within 7,200 minutes of its collection;
- every scored specimen has exactly one outcome class, which agrees with its label, and the label
  equals the one derived from the scans and the policy alone.

Resource level, from the engine's traces: a service starts only while the desk has a free server;
stat first and first come, first served within a priority; every carrier leaves the laboratory
station at the first emptying after it arrived, or later if it was overlooked; and no courier
round carries more than its capacity.
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime

MAX_FAILURES = 200
RECEIPT_TOLERANCE_S = 1
OBSERVED = ("ordered", "collected", "received", "accessioned", "plated", "rejected")
HIDDEN_PREFIXES = ("tube_station", "courier_")
SUBSTATES = {
    "at_unit": {"pre_dispatch", "outage_hold", "awaiting_pickup", "not_dispatched"},
    "in_tube": {"transit", "stuck"},
    "misrouted": {"wrong_station"},
    "at_station": {"lab_station", "receiving_queue", "scanner_backlog"},
    "stalled_at_station": {"overlooked", "unattended"},
    "hand_carry": {"in_transit", "walk", "receiving_queue", "scanner_backlog"},
    "lost": {"in_tube", "in_transit"},
    "not_collected": {"not_collected"},
    "at_receiving_bench": {None},
    "accessioned": {None},
    "plated": {None},
}
OPEN_ENDED = {("lost", "in_tube"), ("lost", "in_transit"), ("at_unit", "not_dispatched"),
              ("not_collected", "not_collected"), ("plated", None)}
POST_RECEIPT = ("at_receiving_bench", "accessioned", "plated")
LOST_FAULTS = ("lost_in_tube", "lost_in_transit")
OUTCOME_CLASSES = ("no_clock", "on_time", "late", "lost")
HORIZON_S = 7200 * 60  # spec A-05: no receipt within 7,200 minutes of collection is never received


def horizon_cut(brief: dict) -> bool:
    return bool(brief.get("v2", {}).get("horizon_cut", False))


def ts(text: str) -> datetime:
    return datetime.fromisoformat(text)


def seconds(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds()


def check_events(brief: dict, trail: dict) -> list[str]:
    evs, errors = trail["observed_events"], []
    if not evs:
        return ["no observed events"]
    stamps = [ts(e["timestamp"]) for e in evs]
    if any(b < a for a, b in zip(stamps, stamps[1:])):
        errors.append("observed events are not in time order")
    names = [e["activity"] for e in evs]
    errors += [f"activity {a!r} appears {names.count(a)} times" for a in sorted(set(names)) if names.count(a) > 1]
    errors += [f"unknown activity {a!r}" for a in sorted(set(names) - set(OBSERVED))]
    if names[0] not in ("ordered", "collected"):
        errors.append(f"the first event is {names[0]!r}, not ordered or collected")
    errors += [f"a {e['activity']} event is scanned at hidden location {e['location']!r}" for e in evs
               if e["location"].startswith(HIDDEN_PREFIXES)]
    return errors


def check_segments(brief: dict, trail: dict) -> list[str]:
    segs, errors = trail["truth_segments"], []
    if not segs:
        return ["no truth segments"]
    if ts(segs[0]["start"]) != ts(brief["fixed"]["collected_at"]):
        errors.append(f"the first segment starts {segs[0]['start']}, not at collected_at")
    for i, s in enumerate(segs):
        if s.get("substate") not in SUBSTATES.get(s["state"], ()):
            errors.append(f"substate {s.get('substate')!r} does not belong to state {s['state']!r}")
        if s["end"] is None:
            cut_allows = horizon_cut(brief) and s["state"] not in POST_RECEIPT + ("not_collected",)
            if i != len(segs) - 1:
                errors.append(f"the open-ended {s['state']} segment is not the last")
            elif (s["state"], s.get("substate")) not in OPEN_ENDED and not cut_allows:
                errors.append(f"a {s['state']}/{s.get('substate')} segment is open-ended, but the specimen "
                              "is not horizon-cut")
        elif ts(s["end"]) <= ts(s["start"]):
            errors.append(f"the {s['state']} segment ends at or before it starts")
    for a, b in zip(segs, segs[1:]):
        if a["end"] is not None and ts(a["end"]) != ts(b["start"]):
            errors.append(f"{a['state']} ends {a['end']} but {b['state']} starts {b['start']}")
    used = {s["location"] for s in segs} | {e["location"] for e in trail["observed_events"]}
    errors += [f"location {loc!r} is not in the brief's list" for loc in sorted(used - set(brief["locations"]))]
    return errors


def check_receipt(brief: dict, trail: dict) -> list[str]:
    received = [e for e in trail["observed_events"] if e["activity"] == "received"]
    if not received:
        return []
    segs = trail["truth_segments"]
    before = [s for s in segs if s["state"] not in POST_RECEIPT]
    if not before or before[-1]["end"] is None:
        return ["a received event has no closed segment before it"]
    gap = seconds(ts(before[-1]["end"]), ts(received[0]["timestamp"]))
    errors = [] if abs(gap) <= RECEIPT_TOLERANCE_S else [f"received is {gap:+g} s from the end of the last pre-receipt segment"]
    expected = seconds(ts(brief["fixed"]["collected_at"]), ts(received[0]["timestamp"])) / 60
    if abs((brief["targets"]["receipt_after_min"] or 0) - expected) > 1e-3:
        errors.append(f"receipt_after_min is {brief['targets']['receipt_after_min']}, the scans say {expected:.4f}")
    return errors


def check_faults(brief: dict, trail: dict) -> list[str]:
    fault, segs, out = brief["planted_fault"], trail["truth_segments"], trail["outcome"]
    has = {e["activity"] for e in trail["observed_events"]}
    states = [(s["state"], s.get("substate")) for s in segs]
    errors = []
    if out["fault"] != fault:
        errors.append(f"outcome.fault is {out['fault']!r}, the planted fault is {fault!r}")
    if out["received"] != ("received" in has) or out["plated"] != ("plated" in has):
        errors.append("outcome does not match the received and plated events")
    cut = horizon_cut(brief)
    if cut and fault in ("never_collected", "not_dispatched"):
        errors.append(f"a {fault} specimen is horizon-cut, but only a specimen that was sent can be")
    if fault == "never_collected":
        if states != [("not_collected", "not_collected")] or segs[0]["end"] is not None or has != {"ordered"}:
            errors.append("a never-collected specimen is one open not_collected segment and one ordered event")
        return errors
    if "collected" not in has:
        errors.append("there is no collected event")
    if fault == "not_dispatched":
        if states != [("at_unit", "not_dispatched")] or segs[0]["end"] is not None:
            errors.append("a never-sent specimen is one open-ended at_unit segment")
        return errors
    lost = segs[-1]["state"] == "lost"
    if cut:
        errors += check_cut(brief, trail, lost, has)
    elif lost != ("received" not in has):
        errors.append("lost does not match the missing receipt" if lost else "no receipt, but the specimen is not lost")
    if lost != (fault in LOST_FAULTS):
        errors.append(f"the trail {'ends' if lost else 'does not end'} lost, but the planted fault is {fault!r}")
    if fault == "content_failure":
        if "rejected" not in has or "accessioned" in has or "plated" in has:
            errors.append("a content failure has a rejected event and no accessioned or plated event")
    elif "rejected" in has:
        errors.append(f"a rejected event without a content failure (planted fault {fault!r})")
    elif "received" in has and not {"accessioned", "plated"} <= has:
        errors.append("a received specimen that is not rejected is accessioned and plated")
    return errors


def check_cut(brief: dict, trail: dict, lost: bool, has: set[str]) -> list[str]:
    """A horizon-cut specimen: no receipt, not lost, and an open pre-receipt segment entered in time."""
    errors, last = [], trail["truth_segments"][-1]
    if "received" in has:
        errors.append("a horizon-cut specimen has a received event")
    if lost:
        errors.append("a horizon-cut specimen ends lost; a loss before the horizon is not a cut")
    if last["end"] is not None or last["state"] in POST_RECEIPT:
        errors.append("a horizon-cut specimen must end in an open-ended pre-receipt segment")
    if seconds(ts(brief["fixed"]["collected_at"]), ts(last["start"])) > HORIZON_S:
        errors.append("a horizon-cut specimen's last segment starts after its 7,200-minute horizon")
    return errors


def scan_label(brief: dict, trail: dict) -> tuple[bool, bool] | None:
    """(clock started, breach) from the scans and the policy alone, or None if a clock state is not scanned."""
    lab = brief["label"]
    if lab["clock_start"] not in OBSERVED or lab["clock_stop"] not in OBSERVED:
        return None
    at = {e["activity"]: ts(e["timestamp"]) for e in trail["observed_events"]}
    start, stop = at.get(lab["clock_start"]), at.get(lab["clock_stop"])
    if start is None:
        return False, True
    if stop is None:
        return True, True
    elapsed = seconds(start, stop)
    return True, elapsed > 7200 * 60 or elapsed > lab["window_min"] * 60


def check_label(brief: dict, trail: dict) -> list[str]:
    lab, v2, errors = brief["label"], brief["v2"], []
    cls = v2["outcome_class"]
    if cls not in OUTCOME_CLASSES:
        return [f"outcome class {cls!r} is not one of {OUTCOME_CLASSES}"]
    expected = ("no_clock" if not lab["clock_started"] else "lost" if lab["elapsed_min"] is None
                else "late" if lab["breach"] else "on_time")
    if cls != expected:
        errors.append(f"outcome class {cls!r} disagrees with the label ({expected!r})")
    derived = scan_label(brief, trail)
    if derived is not None and derived != (lab["clock_started"], lab["breach"]):
        errors.append(f"the label (started {lab['clock_started']}, breach {lab['breach']}) differs from the scans' "
                      f"(started {derived[0]}, breach {derived[1]})")
    return errors


RECORD_CHECKS = (check_events, check_segments, check_receipt, check_faults)


def check_record(rec: dict) -> list[str]:
    brief, trail = rec["brief"], rec["trail"]
    errors = []
    for check in RECORD_CHECKS:
        errors += check(brief, trail)
    if brief["v2"]["scored"]:
        errors += check_label(brief, trail)
    return errors


def capacity_at(trace: list[tuple[float, int]], times: list[float], t: float) -> int:
    i = bisect_right(times, t) - 1
    return trace[i][1] if i >= 0 else 1


def check_servers(stats: dict) -> list[str]:
    """A service starts only while the desk has a free server (completions at a minute free theirs first)."""
    services, trace = stats["services"], stats["capacity"]
    times = [t for t, _ in trace]
    errors = []
    marks = sorted([(end, 0, sid) for sid, _, _, _, end in services if end is not None]
                   + [(start, 1, sid) for sid, _, _, start, _ in services])
    busy = 0
    for t, kind, sid in marks:
        busy += 1 if kind else -1
        if kind and busy > capacity_at(trace, times, t):
            errors.append(f"desk: {sid} started at minute {t:.3f} with {busy} in service and "
                          f"{capacity_at(trace, times, t)} servers")
    return errors


def check_stat_first(stats: dict) -> list[str]:
    services, errors = stats["services"], []
    waits = sorted((arrive, start) for _, rank, arrive, start, _ in services if rank == 0 and start > arrive)
    merged: list[list[float]] = []
    for a, b in waits:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    starts = [a for a, _ in merged]
    for sid, rank, _, start, _ in services:
        i = bisect_right(starts, start) - 1
        if rank == 1 and i >= 0 and merged[i][0] < start < merged[i][1]:
            errors.append(f"desk: routine {sid} started at minute {start:.3f} while a stat specimen waited")
    return errors


def check_first_come(stats: dict) -> list[str]:
    """Within a priority, nobody who arrived earlier starts later than someone who arrived after them."""
    services, errors = stats["services"], []
    for rank in (0, 1):
        rows = sorted((start, arrive, sid) for sid, r, arrive, start, _ in services if r == rank)
        latest, group_start, group_max = float("-inf"), None, float("-inf")
        for start, arrive, sid in rows:
            if start != group_start:
                latest, group_start, group_max = max(latest, group_max), start, float("-inf")
            if arrive < latest:
                errors.append(f"desk: {sid} arrived at minute {arrive:.3f} but started after a later arrival")
            group_max = max(group_max, arrive)
    return errors


def check_station(stats: dict) -> list[str]:
    emptyings, errors, seen = stats["emptyings"], [], set()
    for sid, arrive, emptied, leave in stats["station_visits"]:
        i = bisect_right(emptyings, arrive)
        if i >= len(emptyings) or emptyings[i] != emptied:
            errors.append(f"station: {sid} arrived at minute {arrive:.3f} and left at an emptying that was not the next")
        if leave < emptied:
            errors.append(f"station: {sid} left before the emptying")
        if sid in seen:
            errors.append(f"station: {sid} was emptied twice")
        seen.add(sid)
    return errors


def check_couriers(stats: dict) -> list[str]:
    return [f"courier: round of {r['route']} at minute {r['time']:.3f} carried {r['picked']} over its capacity of "
            f"{r['capacity']}" for r in stats["rounds"] if r["picked"] > r["capacity"]]


RESOURCE_CHECKS = (check_servers, check_stat_first, check_first_come, check_station, check_couriers)


def _capped(lines: list[str]) -> list[str]:
    return lines if len(lines) <= MAX_FAILURES else lines[:MAX_FAILURES] + [f"... and {len(lines) - MAX_FAILURES} more"]


def validate(result) -> list[str]:
    """Every failed invariant on one replicate; each group (records, desk, station, couriers) is cut
    at MAX_FAILURES lines plus a count, so one flood never hides another kind of failure."""
    rows: list[str] = []
    for rec in result.records:
        rows += [f"{rec['brief']['specimen_id']}: {e}" for e in check_record(rec)]
    stats = result.stats
    failures = _capped(rows)
    for check in RESOURCE_CHECKS:
        failures += _capped(check(stats))
    return failures
