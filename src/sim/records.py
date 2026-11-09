"""Turn simulated specimens into v1-shaped records (spec §5.7 and §5.8).

Each specimen becomes {"brief", "trail"} as in the v1 dataset, so src/pipeline/store.py and
src/pipeline/evaluate.py read it unchanged:

- ids are SP-r{run_key}-{n:06d}, with run_key = 100000 + 10 * scenario_index + replicate and n
  counting specimens by collection time, traffic included;
- times are minutes as floats inside the engine and naive ISO 8601 to the second here, from day 0
  00:00 = Monday, January 5, 2026. Every boundary is rounded once, so segments stay contiguous;
- `targets` holds the realized minutes of every v1 target key (None where the stage did not
  happen); waits for a courier pickup count in dispatch_wait_min, time at the receiving desk counts
  in station_dwell_min after the tube and in hand_carry_min otherwise;
- `label` follows the governing policy (config.resolve), timed from the realized timeline. The clock
  must stop within 7,200 minutes or the specimen is never received;
- truth segments carry the `substate` of spec §5.4, and the brief carries a `v2` block.

Horizon cut (spec §5.2, A-05). A specimen that was collected and sent but has no receipt within
7,200 minutes of its collection, and was not lost before then, is never received in whatever state
it is: its trail stops at that moment, the segment it is in is left open-ended, nothing after it
(a late receipt included) is recorded, and `v2.horizon_cut` is true. Its outcome class is `lost`.
Only faults whose segment falls inside the kept trail stay; one that would happen later did not
happen within the horizon. The engine itself is untouched by the cut, so no other specimen changes.

v1's trail schema allows no extra segment field, so v1_view() drops `substate` (and the v2 block)
before src.generator.validate.validate is run on a record.
"""

from __future__ import annotations

import copy
from collections import Counter
from datetime import datetime, timedelta
from functools import lru_cache

from . import disruptions, processes

DAY0 = datetime(2026, 1, 5)  # Monday; day -1 is the warm-up Sunday
HORIZON_S = processes.HORIZON_MIN * 60
RUN_KEY_BASE = 100000
FAULT_PRECEDENCE = ("never_collected", "not_dispatched", "lost_in_tube", "lost_in_transit", "content_failure",
                    "misrouted", "stalled_at_station")
SEGMENT_TARGET = {"at_unit": "dispatch_wait_min", "in_tube": "tube_transit_min", "misrouted": "misrouted_min",
                  "at_station": "station_dwell_min", "stalled_at_station": "stalled_min",
                  "hand_carry": "hand_carry_min", "at_receiving_bench": "accession_wait_min",
                  "accessioned": "plate_wait_min"}
TARGET_KEYS = ("dispatch_wait_min", "tube_transit_min", "station_dwell_min", "hand_carry_min", "stalled_min",
               "misrouted_min", "receipt_after_min", "accession_wait_min", "plate_wait_min")
FIRST_ENTRY_STATES = ("in_tube", "misrouted", "at_station", "stalled_at_station")


def run_key(scenario_index: int, replicate: int) -> int:
    return RUN_KEY_BASE + 10 * scenario_index + replicate


def specimen_id(key: int, n: int) -> str:
    return f"SP-r{key}-{n:06d}"


def sec(t: float) -> int:
    """Minutes from day 0 to whole seconds, rounded half up."""
    return int((t * 60 + 0.5) // 1)


@lru_cache(maxsize=1 << 16)
def iso(s: int) -> str:
    return (DAY0 + timedelta(seconds=s)).isoformat()


def minutes(seconds: int) -> float:
    return round(seconds / 60, 4)


def segments(sp) -> list[list]:
    """[state, substate, start, end, location] in seconds: zero-length pieces dropped, equal neighbors joined."""
    out: list[list] = []
    for state, sub, a, b, loc in sp.segs:
        sa, sb = sec(a), (None if b is None else sec(b))
        if sb is not None and sb <= sa:
            continue
        last = out[-1] if out else None
        if last and last[3] == sa and last[0] == state and last[1] == sub and last[4] == loc:
            last[3] = sb
            continue
        out.append([state, sub, sa, sb, loc])
    return out


def truncate(segs: list[list], deadline: int) -> list[list]:
    """The segments up to the deadline, the last one (the state at the deadline) left open-ended."""
    out: list[list] = []
    for s in segs:
        if s[2] > deadline:
            break
        out.append(list(s))
        if s[3] is None or s[3] > deadline:
            break
    if out:
        out[-1][3] = None
    return out


def cut_at_horizon(sp, segs: list[list], received: int | None, c: int) -> bool:
    """True for a specimen collected and sent, with no receipt within the horizon and not lost before it."""
    if sp.never_collected or sp.not_dispatched:
        return False
    if received is not None and received - c <= HORIZON_S:
        return False
    return not (segs and segs[-1][0] == "lost" and segs[-1][2] <= c + HORIZON_S)


# The segment that shows a fault happened. A fault without its segment in a cut trail is dropped.
FAULT_SEGMENT = {"misrouted": ("misrouted", "wrong_station"),
                 "stalled_at_station": ("stalled_at_station", "overlooked")}


def label(mix, timeline: dict) -> dict:
    """Window and breach from the governing policy's own clock_start and clock_stop, as v1 does."""
    policy = mix.policy
    start, stop = timeline.get(policy["clock_start"]), timeline.get(policy["clock_stop"])
    elapsed = stop - start if start is not None and stop is not None else None
    if elapsed is not None and elapsed > HORIZON_S:
        elapsed = None
    return {
        "policy_id": mix.policy_id,
        "window_min": policy["window_min"],
        "urgency_tier": policy["urgency_tier"],
        "clock_start": policy["clock_start"],
        "clock_stop": policy["clock_stop"],
        "clock_started": start is not None,
        "elapsed_min": None if elapsed is None else minutes(elapsed),
        "breach": elapsed is None or elapsed > policy["window_min"] * 60,
    }


def outcome_class(lab: dict) -> str:
    if not lab["clock_started"]:
        return "no_clock"
    if lab["elapsed_min"] is None:
        return "lost"
    return "late" if lab["breach"] else "on_time"


def cause(sp, fault: str) -> str:
    if sp.touched:
        return "disruption"
    if fault != "none" or sp.missed_base:
        return "independent"
    if sp.congested or sp.bumped:
        return "congestion"
    return "none"


def _event(activity: str, s: int, location: str, role: str) -> dict:
    return {"activity": activity, "timestamp": iso(s), "location": location, "actor_role": role}


def record(sp, scenario: dict, replicate: int, key: int) -> dict:
    sid = specimen_id(key, sp.n)
    unit, lat = sp.unit, sp.lat
    c = sec(sp.c)
    faults = set(sp.faults)
    received = sec(sp.received) if sp.received is not None else None
    if sp.never_collected:
        segs = [["not_collected", "not_collected", c, None, sp.unit_loc]]
    else:
        segs = segments(sp)
    horizon_cut = cut_at_horizon(sp, segs, received, c)
    if horizon_cut:  # no receipt within 7,200 minutes: never received, in the state it is in then
        segs, received = truncate(segs, c + HORIZON_S), None
        kept = {(s[0], s[1]) for s in segs}
        faults = {f for f in faults if FAULT_SEGMENT.get(f) in kept}

    events = []
    if sp.never_collected:
        events.append(_event("ordered", c, sp.unit_loc, "system"))
    else:
        if sp.ordered_at is not None:
            events.append(_event("ordered", sec(sp.ordered_at), sp.unit_loc, "system"))
        events.append(_event("collected", c, sp.unit_loc, "nurse" if lat["collector"] < 0.5 else "phlebotomist"))
    timeline: dict[str, int] = {} if sp.never_collected else {"collected": c}
    if received is not None:
        events.append(_event("received", received, "lab_receiving",
                             "technologist" if lat["receiver"] < 0.5 else "processor"))
        timeline["received"] = received
        if sp.rejected is not None:
            events.append(_event("rejected", sec(sp.rejected), "lab_receiving", "technologist"))
        else:
            accessioned, plated = sec(sp.accessioned), sec(sp.plated)
            events.append(_event("accessioned", accessioned, "lab_receiving", "technologist"))
            bench = "lab_bench_1" if lat["bench"] < 0.5 else "lab_bench_2"
            events.append(_event("plated", plated, bench, "technologist"))
            timeline["accessioned"], timeline["plated"] = accessioned, plated

    at_unit = [i for i, s in enumerate(segs) if s[0] == "at_unit"]
    if at_unit and segs[at_unit[-1]][3] is not None and at_unit[-1] + 1 < len(segs):
        timeline["dispatched"] = segs[at_unit[-1]][3]
    for s in segs:
        if s[0] in FIRST_ENTRY_STATES and s[0] not in timeline:
            timeline[s[0]] = s[2]

    targets = dict.fromkeys(TARGET_KEYS)
    for state, _, a, b, _ in segs:
        k = SEGMENT_TARGET.get(state)
        if k is not None and b is not None:
            targets[k] = (targets[k] or 0) + (b - a)
    targets = {k: (None if v is None else minutes(v)) for k, v in targets.items()}
    if received is not None:
        targets["receipt_after_min"] = minutes(received - c)

    fault = next((f for f in FAULT_PRECEDENCE if f in faults), "none")
    lab = label(sp.mix, timeline)
    locations: list[str] = []
    for loc in [s[4] for s in segs] + [e["location"] for e in events]:
        if loc not in locations:
            locations.append(loc)
    truth = [{"state": s[0], "start": iso(s[2]), "end": None if s[3] is None else iso(s[3]), "location": s[4],
              "substate": s[1]} for s in segs]
    brief = {
        "specimen_id": sid,
        "fixed": {"type": sp.mix.specimen_type, "tests": list(sp.mix.tests), "unit": unit.unit_id,
                  "priority": "stat" if sp.stat else "routine",
                  "route": "tube" if sp.route_kind == "tube" else "hand_carry", "collected_at": iso(c)},
        "planted_fault": fault,
        "targets": targets,
        "locations": locations,
        "label": lab,
        "v2": {"scenario_id": scenario.get("scenario_id"), "family_id": scenario.get("family_id"),
               "replicate": replicate, "unit_kind": unit.kind, "route_kind": sp.route_kind, "route_id": sp.route_id,
               "scored": sp.scored, "cause": cause(sp, fault), "disruption_ids": sorted(sp.touched),
               "outcome_class": outcome_class(lab), "horizon_cut": horizon_cut},
    }
    trail = {"specimen_id": sid, "observed_events": events, "truth_segments": truth,
             "outcome": {"fault": fault, "received": received is not None,
                         "plated": any(e["activity"] == "plated" for e in events)}}
    return {"brief": brief, "trail": trail}


def v1_view(rec: dict) -> dict:
    """The record as v1's validator expects it: no substate on truth segments and no v2 block."""
    out = copy.deepcopy(rec)
    out["brief"].pop("v2", None)
    for s in out["trail"]["truth_segments"]:
        s.pop("substate", None)
    return out


def roster(scenario: dict) -> dict:
    """What the laboratory knows about itself. No rate, fault or disruption (spec §6.4, §7.4)."""
    return {
        "scenario_id": scenario.get("scenario_id"),
        "shift_pattern": scenario["receiving"]["shift_pattern"],
        "units": [{"unit_id": u["unit_id"], "kind": u["kind"], "route": u["route"],
                   "tube_line_id": u.get("tube_line_id"), "courier_route_id": u.get("courier_route_id")}
                  for u in scenario["units"]],
        "tube_lines": [ln["line_id"] for ln in (scenario.get("tube_system") or {}).get("lines", [])],
        "courier_routes": [{"route_id": r["route_id"], "kind": r["kind"],
                            "round_interval_min": dict(r["round_interval_min"])}
                           for r in (scenario.get("couriers") or {}).get("routes", [])],
        "receiving_staff": copy.deepcopy(scenario["receiving"]["staff_by_shift"]),
    }


def realized(wins: disruptions.Windows) -> list[dict]:
    """The realized disruption windows, truth side."""
    return [{"disruption_id": w.disruption_id, "type": w.type, "target_id": w.target_id, "day": w.day,
             "start": iso(sec(w.start)), "end": iso(sec(w.end)), "start_min": round(w.start, 4),
             "end_min": round(w.end, 4), "severity": w.severity, "response": w.response,
             "noticed_at": iso(sec(w.noticed_at)), "source": w.source} for w in wins.all]


def stats(sim, recs: list[dict], key: int) -> dict:
    """Counts and the resource traces that validate_v2 and kpi read."""
    ids = {sp.seq: specimen_id(key, sp.n) for sp in sim.specimens}
    scored = [r for r in recs if r["brief"]["v2"]["scored"]]
    return {
        "specimens": len(recs),
        "scored": len(scored),
        "traffic": len(recs) - len(scored),
        "scored_breaches": sum(r["brief"]["label"]["breach"] for r in scored),
        "horizon_cut": sum(r["brief"]["v2"]["horizon_cut"] for r in recs),
        "scored_horizon_cut": sum(r["brief"]["v2"]["horizon_cut"] for r in scored),
        "outcome_classes": dict(sorted(Counter(r["brief"]["v2"]["outcome_class"] for r in scored).items())),
        "events": sim.q.pushed,
        "days": sim.world.days,
        "t_end": sim.world.t_end,
        "candidates": {ids[sp.seq]: f"{sp.unit.index}:{sp.k}" for sp in sim.specimens},
        "capacity": list(sim.cap_trace),
        "busy": list(sim.busy_trace),
        "services": [(ids[sp.seq], 0 if sp.stat else 1, sp.desk_arrive, sp.service_start, sp.service_end)
                     for sp in sim.specimens if sp.service_start is not None],
        "emptyings": list(sim.emptyings),
        "station_visits": [(ids[sp.seq], a, e, leave) for sp, a, e, leave in sim.visits],
        "rounds": list(sim.rounds),
    }
