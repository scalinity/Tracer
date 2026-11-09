"""The simulator's engine: byte-identical reruns, common random numbers, heap order, and every
spec §5.9 invariant on random scenarios (docs/neural/spec.md §15.2)."""

import copy
import gzip
import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from src.generator import config
from src.sim import engine, kpi, records, run, validate_v2

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = json.loads((ROOT / "config" / "scenario.example.json").read_text(encoding="utf-8"))

UNIT_KINDS = ("ed", "icu", "med_surg", "periop", "clinic_onsite", "clinic_offsite", "oncology", "pediatrics",
              "labor_delivery", "dialysis", "long_term_care")
DISRUPTION_TYPES = ("tube_line_outage", "tube_line_degraded", "station_unattended", "receiving_short_staffed",
                    "courier_missed_rounds", "courier_delayed", "unit_surge", "unit_batching_hold",
                    "scanner_downtime", "instrument_down", "offsite_route_closed")
OUTAGE_RESPONSES = ("hold_at_unit", "divert_to_courier", "stuck_in_system")


@pytest.fixture(scope="module")
def loaded():
    return config.load()


def example(**top) -> dict:
    sc = copy.deepcopy(EXAMPLE)
    sc.update({"scenario_id": "example", "family_id": "example", **top})
    return sc


def _median_p90(g, lo, hi, p90_max, ratio=(1.3, 3.0)) -> tuple[float, float]:
    median = round(float(g.uniform(lo, hi)), 1)
    return median, round(min(p90_max, median * float(g.uniform(*ratio))), 1)


def random_scenario(seed: int) -> dict:
    """The worked example perturbed within the bounds of spec §4.3, with a few disruptions of every
    type across seeds. Volumes stay at the low end so the tests stay fast."""
    g = np.random.default_rng([20261108, seed])
    sc = example(scenario_id=f"random-{seed:02d}", family_id=f"random-{seed:02d}", scenario_index=seed)
    sc["volume"] = {"specimens_per_weekday": int(g.integers(30, 260)),
                    "hourly_profile": [round(float(x), 2) for x in g.uniform(0.2, 5.0, 24)],
                    "weekend_factor": round(float(g.uniform(0.3, 1.2)), 2)}
    sc["specimen_mix"] = {k: round(float(g.uniform(0, 1)), 3) for k in sc["specimen_mix"]}

    n_lines = int(g.integers(1, 4))
    lines = []
    for i in range(n_lines):
        median, p90 = _median_p90(g, 1, 20, 45)
        lines.append({"line_id": f"line_{i}", "transit_median_min": median, "transit_p90_min": p90,
                      "misroute_rate": round(float(g.uniform(0, 0.03)), 4), "lost_rate": round(float(g.uniform(0, 0.01)), 4)})
    stall, found = _median_p90(g, 10, 240, 720), _median_p90(g, 10, 120, 360)
    sc["tube_system"] = {"lines": lines,
                         "station_empty_interval_min": {s: int(g.integers(2, 60)) for s in ("day", "evening", "night")},
                         "stalled_rate": round(float(g.uniform(0, 0.06)), 4),
                         "stall_extra_min": {"median": stall[0], "p90": stall[1]},
                         "misroute_found_min": {"median": found[0], "p90": found[1]}}

    kinds = ("onsite_runner", "campus_loop", "offsite_van")
    routes = []
    for i in range(int(g.integers(1, 4))):
        kind = kinds[i % 3] if i else "offsite_van"
        median, p90 = _median_p90(g, 3, 90, 240)
        night = None if kind == "offsite_van" and g.random() < 0.6 else int(g.integers(10, 241))
        routes.append({"route_id": f"route_{i}", "kind": kind,
                       "round_interval_min": {"day": int(g.integers(10, 181)), "evening": int(g.integers(10, 241)),
                                              "night": night},
                       "travel_median_min": median, "travel_p90_min": p90,
                       "missed_round_rate": round(float(g.uniform(0, 0.2)), 3),
                       "lost_rate": round(float(g.uniform(0, 0.01)), 4), "capacity_per_round": int(g.integers(5, 201))})
    vans = [r["route_id"] for r in routes if r["kind"] == "offsite_van"]
    sc["couriers"] = {"routes": routes, "on_call_runner": bool(g.random() < 0.6),
                      "on_call_response_median_min": int(g.integers(5, 61))}

    units = []
    for i in range(int(g.integers(3, 9))):
        kind = UNIT_KINDS[int(g.integers(len(UNIT_KINDS)))]
        route = "courier" if kind == "clinic_offsite" else ("tube", "courier", "walk_in", "mixed")[int(g.integers(4))]
        line = f"line_{int(g.integers(n_lines))}" if route in ("tube", "mixed") else None
        courier = (vans[0] if kind == "clinic_offsite" else routes[int(g.integers(len(routes)))]["route_id"]) \
            if route == "courier" or (route == "mixed" and g.random() < 0.7) else None
        batched = bool(g.random() < 0.3)
        median, p90 = _median_p90(g, 1, 60, 480)
        units.append({"unit_id": f"unit_{i}", "kind": kind, "volume_share": round(float(g.uniform(0.05, 1)), 3),
                      "stat_fraction": round(float(g.uniform(0, 1)), 2), "route": route, "tube_line_id": line,
                      "courier_route_id": courier,
                      "tube_share_if_mixed": round(float(g.uniform(0, 1)), 2) if route == "mixed" else None,
                      "dispatch": {"mode": "batched" if batched else "immediate", "median_min": median, "p90_min": p90,
                                   "batch_interval_min": int(g.integers(15, 241)) if batched else None},
                      "not_dispatched_rate": round(float(g.uniform(0, 0.05)), 4),
                      "mix_tilt": [{"key": k, "multiplier": round(float(g.uniform(0, 5)), 2)}
                                   for k in g.choice(list(sc["specimen_mix"]), size=int(g.integers(0, 4)), replace=False)]})
    sc["units"] = units

    sc["receiving"] = {"staff_by_shift": {d: {s: int(g.integers(1, 6)) for s in ("day", "evening", "night")}
                                          for d in ("weekday", "weekend")},
                       "shift_pattern": ("7-15-23", "6-14-22", "7-19")[int(g.integers(3))],
                       "scan_service_min": dict(zip(("median", "p90"), _median_p90(g, 0.3, 2.0, 10))),
                       "shift_change_gap_min": int(g.integers(0, 46))}
    acc, plate = _median_p90(g, 2, 120, 480), _median_p90(g, 2, 120, 480)
    instruments = [{"name": f"instrument_{i}", "downtime_per_week": round(float(g.uniform(0, 3)), 2),
                    "downtime_median_min": int(g.integers(10, 481))} for i in range(int(g.integers(0, 5)))]
    sc["processing"] = {"accession_min": {"median": acc[0], "p90": acc[1]},
                        "plating_min": {"median": plate[0], "p90": plate[1]}, "instruments": instruments,
                        "staff_pull_from_receiving": round(float(g.uniform(0, 1)), 2)}
    sc["independent_faults"] = {"never_collected_rate": round(float(g.uniform(0, 0.02)), 4),
                                "content_failure_rate": round(float(g.uniform(0, 0.05)), 4)}

    targets = {"tube_line_outage": [ln["line_id"] for ln in lines], "tube_line_degraded": [ln["line_id"] for ln in lines],
               "station_unattended": ["lab_station"], "receiving_short_staffed": ["receiving"],
               "scanner_downtime": ["receiving"], "courier_missed_rounds": [r["route_id"] for r in routes],
               "courier_delayed": [r["route_id"] for r in routes], "offsite_route_closed": vans or [routes[0]["route_id"]],
               "unit_surge": [u["unit_id"] for u in units], "unit_batching_hold": [u["unit_id"] for u in units],
               "instrument_down": [i["name"] for i in instruments]}
    types = [DISRUPTION_TYPES[(seed + j) % len(DISRUPTION_TYPES)] for j in range(int(g.integers(2, 9)))]
    sc["disruptions"] = []
    for j, kind in enumerate(types):
        if not targets[kind]:
            continue
        sc["disruptions"].append({
            "disruption_id": f"d{j + 1}", "type": kind, "target_id": str(g.choice(targets[kind])),
            "day": int(g.integers(0, 7)), "start": f"{int(g.integers(0, 24)):02d}:{int(g.integers(0, 60)):02d}",
            "duration_min": int(g.integers(10, 721)), "severity": round(float(g.uniform(0.1, 1)), 2),
            "response": OUTAGE_RESPONSES[(seed + j) % 3] if kind == "tube_line_outage" else "none",
            "noticed_after_min": int(g.integers(0, 241)), "recurs_daily": bool(g.random() < 0.15)})
    return sc


def dumps(result) -> str:
    return json.dumps([result.records, result.roster, result.disruptions], sort_keys=True, separators=(",", ":"))


# --- the event heap ---------------------------------------------------------------------------------

def test_the_heap_orders_by_time_then_priority_class_then_insertion():
    q = engine.EventQueue()
    q.push(5.0, engine.COLLECTION, "c1")
    q.push(5.0, engine.CAPACITY, "cap")
    q.push(1.0, engine.COLLECTION, "early")
    q.push(5.0, engine.SERVICE_DONE, "s1")
    q.push(5.0, engine.SERVICE_DONE, "s2")
    q.push(5.0, engine.ARRIVAL, "a1")
    q.push(5.0, engine.SCHEDULED, "empty")
    order = [q.pop()[3] for _ in range(len(q))]
    assert order == ["early", "cap", "s1", "s2", "empty", "a1", "c1"]


def test_capacity_changes_come_before_service_completions_and_those_before_arrivals():
    assert engine.CAPACITY < engine.SERVICE_DONE < engine.SCHEDULED < engine.ARRIVAL < engine.COLLECTION


# --- determinism ------------------------------------------------------------------------------------

def test_reruns_are_byte_identical(loaded):
    a = engine.simulate(example(), scenario_index=3, replicate=1, loaded=loaded)
    b = engine.simulate(example(), scenario_index=3, replicate=1, loaded=loaded)
    assert dumps(a) == dumps(b)
    assert a.stats["scored"] > 1000


def test_another_replicate_or_index_draws_a_different_week(loaded):
    base = dumps(engine.simulate(example(), scenario_index=3, replicate=1, loaded=loaded))
    assert dumps(engine.simulate(example(), scenario_index=3, replicate=2, loaded=loaded)) != base
    assert dumps(engine.simulate(example(), scenario_index=4, replicate=1, loaded=loaded)) != base


def test_library_files_are_byte_identical_across_runs(tmp_path):
    scenarios = [example(split="train", scenario_index=7), random_scenario(1) | {"split": "validation"}]
    reps = {"train": [0, 1], "validation": 1}
    first = run.simulate_library(scenarios, tmp_path / "a", reps, workers=1)
    second = run.simulate_library(scenarios, tmp_path / "b", reps, workers=2)
    assert (tmp_path / "a" / "manifest.json").read_bytes() == (tmp_path / "b" / "manifest.json").read_bytes()
    assert first["replicates_written"] == 3 and first["replicates_dropped"] == 0
    for f in first["files"]:
        assert (tmp_path / "a" / f["path"]).read_bytes() == (tmp_path / "b" / f["path"]).read_bytes(), f["path"]
    paths = {f["path"] for f in second["files"]}
    assert {"train/example/rep-0.jsonl.gz", "train/example/rep-1.jsonl.gz", "train/example/roster.json",
            "train/example/disruptions-rep-0.json", "validation/random-01/rep-0.jsonl.gz"} <= paths
    with gzip.open(tmp_path / "a" / "train" / "example" / "rep-1.jsonl.gz", "rt", encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert lines and all(line == json.dumps(json.loads(line), sort_keys=True, separators=(",", ":")) for line in lines)
    assert [json.loads(line)["brief"]["specimen_id"] for line in lines] == \
        sorted(json.loads(line)["brief"]["specimen_id"] for line in lines)


# --- common random numbers -------------------------------------------------------------------------

def paired(a, b) -> list[tuple[dict, dict]]:
    """Records of two worlds matched by their candidate (unit, position in the unit's candidate stream)."""
    by_key = {a.stats["candidates"][r["brief"]["specimen_id"]]: r for r in a.records}
    return [(by_key[b.stats["candidates"][r["brief"]["specimen_id"]]], r) for r in b.records
            if b.stats["candidates"][r["brief"]["specimen_id"]] in by_key]


def test_changing_a_disruption_leaves_every_specimens_own_draws_unchanged(loaded):
    with_outage = engine.simulate(example(), scenario_index=5, replicate=1, loaded=loaded, keep_draws=True)
    calm = example()
    calm["disruptions"] = [d for d in calm["disruptions"] if d["disruption_id"] != "d1"]
    without = engine.simulate(calm, scenario_index=5, replicate=1, loaded=loaded, keep_draws=True)
    pairs = paired(with_outage, without)
    assert len(pairs) == len(with_outage.records) == len(without.records)
    touched = 0
    for a, b in pairs:
        assert a["brief"]["specimen_id"] == b["brief"]["specimen_id"]
        assert with_outage.stats["draws"][a["brief"]["specimen_id"]] == without.stats["draws"][b["brief"]["specimen_id"]]
        assert a["brief"]["fixed"] == b["brief"]["fixed"]
        if "d1" in a["brief"]["v2"]["disruption_ids"]:
            touched += 1
        elif a["brief"]["v2"]["route_id"] != "line_a":  # untouched and off the line: its own legs are the same
            assert a["trail"]["truth_segments"][0] == b["trail"]["truth_segments"][0]
    assert touched > 0


def test_a_surge_adds_specimens_and_leaves_every_existing_one_paired(loaded):
    base = example()
    base["disruptions"] = []
    surged = copy.deepcopy(base)
    surged["disruptions"] = [{"disruption_id": "s1", "type": "unit_surge", "target_id": "ed_main", "day": 2,
                              "start": "10:00", "duration_min": 240, "severity": 0.8, "response": "none",
                              "noticed_after_min": 0, "recurs_daily": False}]
    a = engine.simulate(base, scenario_index=6, replicate=0, loaded=loaded, keep_draws=True)
    b = engine.simulate(surged, scenario_index=6, replicate=0, loaded=loaded, keep_draws=True)
    pairs = paired(a, b)
    assert len(pairs) == len(a.records) < len(b.records)
    for ra, rb in pairs:
        assert a.stats["draws"][ra["brief"]["specimen_id"]] == b.stats["draws"][rb["brief"]["specimen_id"]]
        assert ra["brief"]["fixed"] == rb["brief"]["fixed"]
    extra = [r for r in b.records if b.stats["candidates"][r["brief"]["specimen_id"]]
             not in set(a.stats["candidates"].values())]
    assert extra and all(r["brief"]["fixed"]["unit"] == "ed_main" and r["brief"]["v2"]["disruption_ids"] == ["s1"]
                         and "2026-01-07T10:00:00" <= r["brief"]["fixed"]["collected_at"] < "2026-01-07T14:00:00"
                         for r in extra)


def test_a_capacity_change_leaves_every_specimens_own_draws_unchanged(loaded):
    lean = example()
    lean["receiving"]["staff_by_shift"]["weekday"]["day"] = 1
    a = engine.simulate(example(), scenario_index=8, replicate=0, loaded=loaded, keep_draws=True)
    b = engine.simulate(lean, scenario_index=8, replicate=0, loaded=loaded, keep_draws=True)
    pairs = paired(a, b)
    assert len(pairs) == len(a.records) == len(b.records)
    assert all(a.stats["draws"][x["brief"]["specimen_id"]] == b.stats["draws"][y["brief"]["specimen_id"]]
               for x, y in pairs)


# --- invariants -------------------------------------------------------------------------------------

@pytest.mark.parametrize("seed", range(20))
def test_every_invariant_holds_on_a_random_scenario(seed, loaded):
    sc = random_scenario(seed)
    result = engine.simulate(sc, scenario_index=sc["scenario_index"], replicate=1, loaded=loaded)
    assert validate_v2.validate(result) == []
    assert result.stats["scored"] > 0 and all(kpi.resolved(r) for r in result.records)
    assert {r["brief"]["v2"]["outcome_class"] for r in result.records if r["brief"]["v2"]["scored"]} <= \
        set(validate_v2.OUTCOME_CLASSES)


def test_random_scenarios_exercise_every_disruption_type_and_response():
    kinds = {(d["type"], d["response"]) for seed in range(20) for d in random_scenario(seed)["disruptions"]}
    assert {k for k, _ in kinds} == set(DISRUPTION_TYPES)
    assert {r for k, r in kinds if k == "tube_line_outage"} == set(OUTAGE_RESPONSES)


@pytest.fixture(scope="module")
def example_result(loaded):
    return engine.simulate(example(), scenario_index=0, replicate=1, loaded=loaded)


def test_the_example_holds_every_invariant(example_result):
    assert validate_v2.validate(example_result) == []


def _first(result, pred):
    return next(r for r in result.records if pred(r))


@pytest.mark.parametrize("break_it, expect", [
    (lambda r: r["trail"]["truth_segments"][1].update(start="2026-01-01T00:00:00"), "starts"),
    (lambda r: r["trail"]["observed_events"].reverse(), "time order"),
    (lambda r: r["trail"]["observed_events"][-1].update(location="tube_station_lab"), "hidden location"),
    (lambda r: r["trail"]["truth_segments"][0].update(substate="stuck"), "does not belong"),
    (lambda r: r["brief"]["label"].update(breach=not r["brief"]["label"]["breach"]), "label"),
    (lambda r: r["brief"].update(planted_fault="lost_in_tube"), "planted fault"),
    (lambda r: r["brief"]["v2"].update(outcome_class="late"), "outcome class"),
    (lambda r: r["brief"]["locations"].pop(), "not in the brief's list"),
])
def test_the_validator_catches_a_broken_record(example_result, break_it, expect):
    result = copy.deepcopy(example_result)
    rec = _first(result, lambda r: r["brief"]["v2"]["scored"] and r["brief"]["v2"]["outcome_class"] == "on_time"
                 and len(r["trail"]["truth_segments"]) >= 4)
    break_it(rec)
    assert any(expect in f for f in validate_v2.validate(result)), validate_v2.validate(result)[:3]


def test_the_validator_catches_broken_resources(example_result):
    result = copy.deepcopy(example_result)
    result.stats["capacity"] = [(t, 0) for t, _ in result.stats["capacity"]]
    result.stats["rounds"][0]["picked"] = result.stats["rounds"][0]["capacity"] + 1
    sid, arrive, emptied, leave = result.stats["station_visits"][5]
    result.stats["station_visits"][5] = (sid, arrive, emptied + 1000, leave)
    routine = [s for s in result.stats["services"] if s[1] == 1]
    sid0, _, a0, s0, _ = routine[len(routine) // 2]
    result.stats["services"].append((sid0 + "x", 1, a0 - 60, s0 + 600, s0 + 601))  # first come, served 10 h late
    result.stats["services"].append((sid0 + "s", 0, s0 - 5, s0 + 5, s0 + 6))  # a stat waits while sid0 starts
    failures = validate_v2.validate(result)
    for expect in ("servers", "capacity", "not the next", "later arrival", "stat specimen waited"):
        assert any(expect in f for f in failures), expect


# --- record shape ----------------------------------------------------------------------------------

def test_records_have_the_v1_shape_with_the_v2_block(example_result):
    key = records.run_key(0, 1)
    assert key == 100001
    for n, rec in enumerate(example_result.records, 1):
        b, t = rec["brief"], rec["trail"]
        assert b["specimen_id"] == t["specimen_id"] == f"SP-r{key}-{n:06d}"
        assert re.fullmatch(r"SP-r[0-9]+-[0-9]+", b["specimen_id"])
        assert set(b) == {"specimen_id", "fixed", "planted_fault", "targets", "locations", "label", "v2"}
        assert set(b["fixed"]) == {"type", "tests", "unit", "priority", "route", "collected_at"}
        assert set(b["targets"]) == set(records.TARGET_KEYS)
        assert set(b["v2"]) == {"scenario_id", "family_id", "replicate", "unit_kind", "route_kind", "route_id",
                                "scored", "cause", "disruption_ids", "outcome_class", "horizon_cut"}
        assert b["v2"]["horizon_cut"] is False
        assert b["fixed"]["route"] in ("tube", "hand_carry") and b["v2"]["route_kind"] in ("tube", "courier", "walk_in")
        assert b["v2"]["cause"] in ("none", "independent", "disruption", "congestion")
        assert set(t) == {"specimen_id", "observed_events", "truth_segments", "outcome"}
        assert all(set(s) == {"state", "start", "end", "location", "substate"} for s in t["truth_segments"])
        assert all("note" not in e for e in t["observed_events"])
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", b["fixed"]["collected_at"])
    scored = [r for r in example_result.records if r["brief"]["v2"]["scored"]]
    assert all("2026-01-05" <= r["brief"]["fixed"]["collected_at"] < "2026-01-12" for r in scored)
    traffic = [r for r in example_result.records if not r["brief"]["v2"]["scored"]]
    assert {r["brief"]["fixed"]["collected_at"][:10] for r in traffic} == {"2026-01-04", "2026-01-12"}


def test_the_roster_holds_no_rate_fault_or_disruption(example_result):
    text = json.dumps(example_result.roster)
    for word in ("rate", "fault", "disruption", "share", "median", "p90", "severity", "lost", "misroute", "stall",
                 "capacity", "volume"):
        assert word not in text, word
    assert example_result.roster["shift_pattern"] == "7-15-23"
    assert {u["unit_id"] for u in example_result.roster["units"]} == {u["unit_id"] for u in EXAMPLE["units"]}
    assert example_result.roster["courier_routes"][1]["round_interval_min"] == {"day": 120, "evening": 180, "night": None}


def test_realized_disruptions_expand_daily_windows_and_add_random_downtimes(example_result):
    d3 = [d for d in example_result.disruptions if d["disruption_id"] == "d3"]
    assert [d["start"] for d in d3] == [f"2026-01-{5 + day:02d}T03:00:00" for day in range(7)]
    assert all(d["source"] == "random" and d["type"] == "instrument_down"
               for d in example_result.disruptions if d["disruption_id"].endswith(tuple("0123456789"))
               and "_random_" in d["disruption_id"])


def test_a_shift_with_no_receiving_staff_is_refused_before_simulation(loaded):
    sc = example()
    sc["receiving"]["staff_by_shift"]["weekend"]["night"] = 0
    with pytest.raises(ValueError, match="at least one receiving staff member"):
        engine.simulate(sc, scenario_index=0, replicate=0, loaded=loaded)


def test_one_week_runs_at_two_seconds_or_less_per_thousand_specimens(example_result):
    assert example_result.stats["wall_seconds"] / example_result.stats["specimens"] * 1000 <= 2.0


# --- the 7,200-minute horizon (spec §5.2, A-05) ----------------------------------------------------

HORIZON_INDEX = 31
PRE_RECEIPT_STATES = ("at_unit", "in_tube", "misrouted", "at_station", "stalled_at_station", "hand_carry")


def horizon_scenario() -> dict:
    """A courier unit whose dispatch lognormal has sigma about 3.5, so about 1% of its specimens wait
    more than 7,200 minutes, on a route with no daytime rounds."""
    sc = example(scenario_id="horizon", family_id="horizon", split="train", scenario_index=HORIZON_INDEX)
    periop = next(u for u in sc["units"] if u["unit_id"] == "periop")
    periop["dispatch"] = {"mode": "immediate", "median_min": 1.99, "p90_min": 170.18, "batch_interval_min": None}
    loop = next(r for r in sc["couriers"]["routes"] if r["route_id"] == "campus_loop")
    loop["round_interval_min"] = {"day": None, "evening": 24, "night": 81}
    return sc


@pytest.fixture(scope="module")
def horizon_runs(loaded):
    return [engine.simulate(horizon_scenario(), scenario_index=HORIZON_INDEX, replicate=r, loaded=loaded)
            for r in range(3)]


def test_a_specimen_with_no_receipt_within_the_horizon_is_cut_and_every_replicate_holds(horizon_runs):
    cut = []
    for result in horizon_runs:
        assert validate_v2.validate(result) == []
        cut += [r for r in result.records if r["brief"]["v2"]["horizon_cut"]]
        assert result.stats["horizon_cut"] == sum(r["brief"]["v2"]["horizon_cut"] for r in result.records)
    assert cut
    for r in cut:
        b, t = r["brief"], r["trail"]
        assert b["v2"]["outcome_class"] == "lost" and b["label"]["breach"] and b["label"]["elapsed_min"] is None
        assert t["outcome"] == {"received": False, "plated": False, "fault": b["planted_fault"]}
        assert {e["activity"] for e in t["observed_events"]} <= {"ordered", "collected"}
        last = t["truth_segments"][-1]
        assert last["end"] is None and last["state"] in PRE_RECEIPT_STATES
        started = datetime.fromisoformat(last["start"]) - datetime.fromisoformat(b["fixed"]["collected_at"])
        assert started.total_seconds() <= 7200 * 60
        assert b["targets"]["receipt_after_min"] is None
    others = [r for result in horizon_runs for r in result.records if not r["brief"]["v2"]["horizon_cut"]]
    assert all(r["trail"]["outcome"]["received"] or r["trail"]["truth_segments"][-1]["state"] in ("lost", "not_collected")
               or r["trail"]["truth_segments"][-1]["substate"] == "not_dispatched" for r in others)


def test_cutting_at_the_horizon_changes_no_other_specimen(loaded, monkeypatch):
    cut = engine.simulate(horizon_scenario(), scenario_index=HORIZON_INDEX, replicate=0, loaded=loaded, keep_draws=True)
    monkeypatch.setattr(records, "HORIZON_S", 10 ** 9)  # the same run with the cut moved out of reach
    uncut = engine.simulate(horizon_scenario(), scenario_index=HORIZON_INDEX, replicate=0, loaded=loaded,
                            keep_draws=True)
    assert cut.stats["draws"] == uncut.stats["draws"]
    assert len(cut.records) == len(uncut.records)
    n_cut = 0
    for a, b in zip(cut.records, uncut.records):
        if a["brief"]["v2"]["horizon_cut"]:
            n_cut += 1
            receipt = b["brief"]["targets"]["receipt_after_min"]
            assert receipt is None or receipt > 7200
            assert a["trail"]["truth_segments"][0]["start"] == b["trail"]["truth_segments"][0]["start"]
        else:
            assert a == b
    assert n_cut == cut.stats["horizon_cut"] > 0


def test_no_replicate_of_the_horizon_scenario_is_dropped_from_the_library(tmp_path):
    manifest = run.simulate_library([horizon_scenario()], tmp_path, {"train": 3}, workers=1)
    assert manifest["replicates_dropped"] == 0 and manifest["replicates_written"] == 3
    assert sum(r["horizon_cut"] for r in manifest["runs"]) > 0


def _received_record(result) -> dict:
    return copy.deepcopy(_first(result, lambda r: r["brief"]["v2"]["scored"] and r["brief"]["v2"]["outcome_class"]
                                == "on_time" and len(r["trail"]["truth_segments"]) >= 4))


def test_the_validator_allows_an_open_pre_receipt_segment_only_for_a_horizon_cut(example_result):
    rec = _received_record(example_result)
    b, t = rec["brief"], rec["trail"]
    t["observed_events"] = [e for e in t["observed_events"] if e["activity"] in ("ordered", "collected")]
    t["truth_segments"] = [s for s in t["truth_segments"] if s["state"] not in validate_v2.POST_RECEIPT]
    t["truth_segments"][-1]["end"] = None
    t["outcome"].update(received=False, plated=False)
    b["targets"]["receipt_after_min"] = None
    b["label"].update(elapsed_min=None, breach=True)
    b["v2"]["outcome_class"] = "lost"
    errors = validate_v2.check_record(rec)
    assert any("not horizon-cut" in e for e in errors) and any("not lost" in e for e in errors), errors
    b["v2"]["horizon_cut"] = True
    assert validate_v2.check_record(rec) == []


def test_the_validator_refuses_a_horizon_cut_that_is_not_one(example_result):
    rec = _received_record(example_result)
    rec["brief"]["v2"]["horizon_cut"] = True
    assert any("has a received event" in e for e in validate_v2.check_record(rec))
    lost = copy.deepcopy(_first(example_result, lambda r: r["brief"]["planted_fault"] == "lost_in_tube"))
    lost["brief"]["v2"]["horizon_cut"] = True
    assert any("ends lost" in e for e in validate_v2.check_record(lost))
    unsent = copy.deepcopy(_first(example_result, lambda r: r["brief"]["planted_fault"] == "not_dispatched"))
    unsent["brief"]["v2"]["horizon_cut"] = True
    assert any("only a specimen that was sent" in e for e in validate_v2.check_record(unsent))


# --- plausibility KPIs ------------------------------------------------------------------------------

def test_the_kpis_cover_the_full_week_and_carry_the_names_the_scenario_signature_reads(loaded):
    k = kpi.kpis(example(), loaded)
    assert k["days"] == kpi.KPI_DAYS == 7
    for name in ("median_tube_min", "median_courier_min", "median_walk_in_min", "breach_share",
                 "never_received_share", "median_transit_min"):
        assert k[name] is not None, name
    assert k["median_tube_min"] == k["median_receipt_min"]["tube"]
    assert k["horizon_cut_share"] == 0.0 and k["resolved_share"] == 1.0
    for stratum in ("normal", "stressed", "disrupted"):
        assert kpi.check(example(), stratum, loaded) == [], stratum


def test_the_kpis_report_the_share_cut_at_the_horizon(loaded):
    k = kpi.kpis(horizon_scenario(), loaded, scenario_index=HORIZON_INDEX)
    assert 0 < k["horizon_cut_share"] <= k["never_received_share"]
