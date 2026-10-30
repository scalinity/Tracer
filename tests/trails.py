"""A correct trail for any brief, built in code. The validator tests use it as their oracle."""

from datetime import datetime, timedelta


def render_trail(brief: dict, *, with_order: bool = True) -> dict:
    f, t, fault = brief["fixed"], brief["targets"], brief["planted_fault"]
    unit_loc, unit = brief["locations"][0], f["unit"]
    t0 = datetime.fromisoformat(f["collected_at"])

    def iso(dt):
        return dt.isoformat()

    events, segments = [], []
    if with_order:
        events.append({"activity": "ordered", "timestamp": iso(t0 - timedelta(minutes=10)), "location": unit_loc,
                       "actor_role": "system"})
    if fault == "never_collected":
        return {"specimen_id": brief["specimen_id"], "observed_events": [
                    {"activity": "ordered", "timestamp": iso(t0), "location": unit_loc, "actor_role": "system"}],
                "truth_segments": [{"state": "not_collected", "start": iso(t0), "end": None, "location": unit_loc}],
                "outcome": {"received": False, "plated": False, "fault": fault}}
    events.append({"activity": "collected", "timestamp": iso(t0), "location": unit_loc, "actor_role": "nurse"})

    cur = t0
    legs = [("at_unit", "dispatch_wait_min", unit_loc), ("in_tube", "tube_transit_min", f"tube_station_{unit}"),
            ("misrouted", "misrouted_min", "tube_station_pharmacy"), ("at_station", "station_dwell_min", "tube_station_lab"),
            ("stalled_at_station", "stalled_min", "tube_station_lab"), ("hand_carry", "hand_carry_min", unit_loc)]
    for state, key, loc in legs:
        if t[key] is not None:
            end = cur + timedelta(minutes=t[key])
            segments.append({"state": state, "start": iso(cur), "end": iso(end), "location": loc})
            cur = end

    if fault == "not_dispatched":
        segments = [{"state": "at_unit", "start": iso(t0), "end": None, "location": unit_loc}]
    elif fault == "lost_in_tube":
        segments.append({"state": "lost", "start": iso(cur), "end": None, "location": f"tube_station_{unit}"})
    else:
        events.append({"activity": "received", "timestamp": iso(cur), "location": "lab_receiving",
                       "actor_role": "technologist"})
        end = cur + timedelta(minutes=t["accession_wait_min"])
        segments.append({"state": "at_receiving_bench", "start": iso(cur), "end": iso(end), "location": "lab_receiving"})
        if fault == "content_failure":
            events.append({"activity": "rejected", "timestamp": iso(end), "location": "lab_receiving",
                           "actor_role": "technologist", "note": "specimen leaking at the lid"})
        else:
            events.append({"activity": "accessioned", "timestamp": iso(end), "location": "lab_receiving",
                           "actor_role": "technologist"})
            plated = end + timedelta(minutes=t["plate_wait_min"])
            segments.append({"state": "accessioned", "start": iso(end), "end": iso(plated), "location": "lab_bench_1"})
            events.append({"activity": "plated", "timestamp": iso(plated), "location": "lab_bench_1",
                           "actor_role": "technologist"})
            segments.append({"state": "plated", "start": iso(plated), "end": None, "location": "lab_bench_1"})

    got_plated = any(e["activity"] == "plated" for e in events)
    got_received = any(e["activity"] == "received" for e in events)
    return {"specimen_id": brief["specimen_id"], "observed_events": events, "truth_segments": segments,
            "outcome": {"received": got_received, "plated": got_plated, "fault": fault}}
