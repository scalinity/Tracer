"""Each disruption type moves outcomes the right way against the same scenario without it, paired
specimen by specimen through common random numbers (docs/neural/spec.md §5.5, §15.2)."""

import copy
import json
from datetime import datetime
from pathlib import Path

import pytest

from src.generator import config
from src.sim import engine, processes, validate_v2

ROOT = Path(__file__).resolve().parents[1]
INDEX, REPLICATE = 11, 1
NEVER = processes.HORIZON_MIN  # a specimen never received counts as the horizon


def base_scenario() -> dict:
    """The worked example with no scheduled disruption and enough volume that queues respond."""
    sc = json.loads((ROOT / "config" / "scenario.example.json").read_text(encoding="utf-8"))
    sc.update({"scenario_id": "paired", "family_id": "paired", "disruptions": []})
    sc["volume"]["specimens_per_weekday"] = 400
    return sc


def disruption(kind: str, target: str, start: str, duration: int, severity: float = 1.0, response: str = "none",
               noticed: int = 0) -> dict:
    return {"disruption_id": "x", "type": kind, "target_id": target, "day": 1, "start": start,
            "duration_min": duration, "severity": severity, "response": response, "noticed_after_min": noticed,
            "recurs_daily": False}


CASES = {
    "tube_line_outage, hold_at_unit": disruption("tube_line_outage", "line_a", "09:00", 120, 0.8, "hold_at_unit", 15),
    "tube_line_outage, divert_to_courier": disruption("tube_line_outage", "line_a", "09:00", 120, 0.8,
                                                      "divert_to_courier", 15),
    "tube_line_outage, stuck_in_system": disruption("tube_line_outage", "line_a", "09:00", 120, 0.8,
                                                    "stuck_in_system", 15),
    "tube_line_degraded": disruption("tube_line_degraded", "line_b", "06:00", 720),
    "station_unattended": disruption("station_unattended", "lab_station", "09:00", 120),
    "receiving_short_staffed": disruption("receiving_short_staffed", "receiving", "08:00", 480, 0.75),
    "courier_missed_rounds": disruption("courier_missed_rounds", "campus_loop", "07:00", 480),
    "courier_delayed": disruption("courier_delayed", "campus_loop", "07:00", 480),
    "unit_surge": disruption("unit_surge", "ed_main", "08:00", 240),
    "unit_batching_hold": disruption("unit_batching_hold", "med_surg_3", "07:00", 480),
    "scanner_downtime": disruption("scanner_downtime", "receiving", "09:00", 120),
    "instrument_down": disruption("instrument_down", "maldi_tof", "08:00", 240),
    "offsite_route_closed": disruption("offsite_route_closed", "van_west", "06:00", 720),
}


@pytest.fixture(scope="module")
def loaded():
    return config.load()


@pytest.fixture(scope="module")
def base(loaded):
    return engine.simulate(base_scenario(), scenario_index=INDEX, replicate=REPLICATE, loaded=loaded)


@pytest.fixture(scope="module")
def worlds(loaded):
    out = {}
    for name, d in CASES.items():
        sc = base_scenario()
        sc["disruptions"] = [copy.deepcopy(d)]
        out[name] = engine.simulate(sc, scenario_index=INDEX, replicate=REPLICATE, loaded=loaded)
    return out


def receipt(rec: dict) -> float:
    t = rec["brief"]["targets"]["receipt_after_min"]
    return NEVER if t is None else t


def plated_after(rec: dict) -> float | None:
    at = {e["activity"]: e["timestamp"] for e in rec["trail"]["observed_events"]}
    if "plated" not in at:
        return None
    return (datetime.fromisoformat(at["plated"]) - datetime.fromisoformat(at["collected"])).total_seconds() / 60


def pairs(base, other) -> list[tuple[dict, dict]]:
    """(base record, disrupted record) for every scored, collected specimen that exists in both worlds."""
    by_key = {base.stats["candidates"][r["brief"]["specimen_id"]]: r for r in base.records}
    out = []
    for r in other.records:
        b = by_key.get(other.stats["candidates"][r["brief"]["specimen_id"]])
        if b is not None and b["brief"]["v2"]["scored"] and b["brief"]["label"]["clock_started"]:
            out.append((b, r))
    return out


def touched(p) -> list[tuple[dict, dict]]:
    return [(b, r) for b, r in p if "x" in r["brief"]["v2"]["disruption_ids"]]


def breaches(p, side: int) -> int:
    return sum(pair[side]["brief"]["label"]["breach"] for pair in p)


@pytest.mark.parametrize("name", [n for n in CASES if n not in ("unit_surge", "instrument_down")])
def test_a_disruption_delays_the_specimens_it_touches_and_never_helps_overall(base, worlds, name):
    world = worlds[name]
    p = pairs(base, world)
    hit = touched(p)
    assert len(hit) >= 5, (name, len(hit))
    assert sum(receipt(r) for _, r in hit) > sum(receipt(b) for b, _ in hit), name
    assert sum(receipt(r) for _, r in p) > sum(receipt(b) for b, _ in p), name
    assert breaches(p, 1) >= breaches(p, 0), name
    assert all(r["brief"]["v2"]["cause"] == "disruption" for _, r in hit)
    assert validate_v2.validate(world) == []


def test_every_specimen_exists_in_both_worlds_unless_the_disruption_adds_arrivals(base, worlds):
    for name, world in worlds.items():
        if name != "unit_surge":
            assert len(pairs(base, world)) == len(pairs(base, base)), name


def test_a_surge_adds_specimens_at_its_unit_and_slows_nobody_down(base, worlds):
    world = worlds["unit_surge"]
    count = lambda res: sum(r["brief"]["fixed"]["unit"] == "ed_main" for r in res.records)
    assert count(world) > count(base) + 20
    p = pairs(base, world)
    assert len(p) == len(pairs(base, base))
    assert sum(receipt(r) for _, r in p) >= sum(receipt(b) for b, _ in p)
    assert breaches(p, 1) >= breaches(p, 0)
    scored = lambda res: sum(r["brief"]["label"]["breach"] for r in res.records if r["brief"]["v2"]["scored"])
    assert scored(world) >= scored(base)


def test_an_instrument_down_delays_plating_and_pulls_receiving_staff(base, worlds):
    world = worlds["instrument_down"]
    p = pairs(base, world)
    hit = [(b, r) for b, r in touched(p) if plated_after(b) is not None]
    assert len(hit) >= 5
    assert sum(plated_after(r) for _, r in hit) > sum(plated_after(b) for b, _ in hit)
    assert all(plated_after(r) >= plated_after(b) - 1e-6 for b, r in hit)
    assert sum(receipt(r) for _, r in p) >= sum(receipt(b) for b, _ in p)
    ten_am_day_1 = 1440 + 10 * 60
    servers = lambda res: validate_v2.capacity_at(res.stats["capacity"], [t for t, _ in res.stats["capacity"]],
                                                  ten_am_day_1)
    assert servers(base) == 4 and servers(world) == 3  # 4 day staff less the 25% pull while it is down


def test_an_outage_holds_diverts_or_strands_carriers_by_its_response(worlds):
    subs = {}
    for name in ("tube_line_outage, hold_at_unit", "tube_line_outage, divert_to_courier",
                 "tube_line_outage, stuck_in_system"):
        subs[name] = {(s["state"], s["substate"]) for r in worlds[name].records
                      if "x" in r["brief"]["v2"]["disruption_ids"] for s in r["trail"]["truth_segments"]}
    assert ("at_unit", "outage_hold") in subs["tube_line_outage, hold_at_unit"]
    assert ("hand_carry", "in_transit") in subs["tube_line_outage, divert_to_courier"]
    assert ("in_tube", "stuck") in subs["tube_line_outage, stuck_in_system"]
    assert ("at_unit", "outage_hold") not in subs["tube_line_outage, stuck_in_system"]


def test_an_unattended_station_and_a_scanner_downtime_show_in_the_truth(worlds):
    unattended = {s["substate"] for r in worlds["station_unattended"].records for s in r["trail"]["truth_segments"]
                  if s["state"] == "stalled_at_station"}
    assert "unattended" in unattended
    backlog = [r for r in worlds["scanner_downtime"].records
               if any(s["substate"] == "scanner_backlog" for s in r["trail"]["truth_segments"])]
    assert backlog
    for r in backlog:  # the scan comes after the downtime ends, never backdated
        received = next(e["timestamp"] for e in r["trail"]["observed_events"] if e["activity"] == "received")
        assert received >= "2026-01-06T11:00:00"


def test_disruptions_on_unscored_days_or_past_the_horizon_are_dropped_or_clipped(loaded):
    sc = base_scenario()
    sc["disruptions"] = [dict(disruption("station_unattended", "lab_station", "03:00", 60), disruption_id="late", day=6)]
    two_days = engine.simulate(sc, scenario_index=INDEX, replicate=0, loaded=loaded, days=2)
    assert two_days.disruptions == [] or all(d["source"] == "random" for d in two_days.disruptions)
    sc["disruptions"] = [dict(disruption("station_unattended", "lab_station", "03:00", 60), disruption_id="daily",
                              recurs_daily=True)]
    daily = engine.simulate(sc, scenario_index=INDEX, replicate=0, loaded=loaded, days=2)
    assert [d["day"] for d in daily.disruptions if d["disruption_id"] == "daily"] == [0, 1]
