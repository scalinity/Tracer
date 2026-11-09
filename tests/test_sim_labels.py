"""Labels come from the policy, timed from the simulated timeline (docs/neural/spec.md §5.8, §15.2).

Policies here are changed in memory only; the policy files are never edited."""

import copy
import json
from datetime import datetime
from pathlib import Path

import pytest

from src.generator import config
from src.sim import engine, records, validate_v2

ROOT = Path(__file__).resolve().parents[1]
INDEX, REPLICATE = 21, 1


def scenario() -> dict:
    sc = json.loads((ROOT / "config" / "scenario.example.json").read_text(encoding="utf-8"))
    sc.update({"scenario_id": "labels", "family_id": "labels"})
    return sc


def with_clock(loaded: dict, **changes) -> dict:
    """A copy of the loaded configuration whose every policy row has these clock fields, revalidated."""
    out = copy.deepcopy(loaded)
    for row in out["policy"]["policies"].values():
        row.update(changes)
    config.validate_policy(out["policy"], out["graph"])
    out["policy_hash"] = config.policy_hash(out["policy"])
    return out


def at(rec: dict) -> dict[str, datetime]:
    return {e["activity"]: datetime.fromisoformat(e["timestamp"]) for e in rec["trail"]["observed_events"]}


def minutes_between(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 60


@pytest.fixture(scope="module")
def loaded():
    return config.load()


@pytest.fixture(scope="module")
def result(loaded):
    return engine.simulate(scenario(), scenario_index=INDEX, replicate=REPLICATE, loaded=loaded)


def test_every_label_follows_the_governing_policy(result, loaded):
    policy = loaded["policy"]
    for rec in result.records:
        b = rec["brief"]
        pid, row = config.resolve(policy, b["fixed"]["type"], b["fixed"]["tests"])
        lab = b["label"]
        assert (lab["policy_id"], lab["window_min"], lab["urgency_tier"], lab["clock_start"], lab["clock_stop"]) == \
            (pid, row["window_min"], row["urgency_tier"], row["clock_start"], row["clock_stop"])
        times = at(rec)
        if "received" in times:
            assert lab["elapsed_min"] == pytest.approx(minutes_between(times["collected"], times["received"]), abs=1e-3)
            assert lab["breach"] == (lab["elapsed_min"] > lab["window_min"])
        else:
            assert lab["elapsed_min"] is None and lab["breach"]


def test_the_outcome_class_follows_the_label(result):
    for rec in result.records:
        lab, cls = rec["brief"]["label"], rec["brief"]["v2"]["outcome_class"]
        if not lab["clock_started"]:
            assert cls == "no_clock"
        elif lab["elapsed_min"] is None:
            assert cls == "lost"
        else:
            assert cls == ("late" if lab["elapsed_min"] > lab["window_min"] else "on_time")
    scored = [r["brief"]["v2"]["outcome_class"] for r in result.records if r["brief"]["v2"]["scored"]]
    assert {"no_clock", "on_time", "late", "lost"} <= set(scored)


def test_never_received_specimens_breach_with_no_elapsed_time(result):
    never = [r for r in result.records if r["brief"]["label"]["clock_started"] and not r["trail"]["outcome"]["received"]]
    faults = {r["brief"]["planted_fault"] for r in never}
    assert {"not_dispatched", "lost_in_tube"} <= faults or {"not_dispatched", "lost_in_transit"} <= faults, faults
    for r in never:
        assert r["brief"]["label"]["breach"] and r["brief"]["label"]["elapsed_min"] is None
        assert r["brief"]["v2"]["outcome_class"] == "lost"
        assert "received" not in at(r)
        assert r["trail"]["truth_segments"][-1]["end"] is None
        assert r["trail"]["truth_segments"][-1]["state"] in ("lost", "at_unit")


def test_never_collected_specimens_have_no_clock(result):
    nc = [r for r in result.records if r["brief"]["planted_fault"] == "never_collected"]
    assert nc
    for r in nc:
        lab = r["brief"]["label"]
        assert not lab["clock_started"] and lab["elapsed_min"] is None and lab["breach"]
        assert r["brief"]["v2"]["outcome_class"] == "no_clock"
        assert [e["activity"] for e in r["trail"]["observed_events"]] == ["ordered"]
        assert r["trail"]["observed_events"][0]["timestamp"] == r["brief"]["fixed"]["collected_at"]


def test_a_policy_whose_clock_stops_at_plating_moves_labels(result, loaded):
    plating = with_clock(loaded, clock_stop="plated")
    moved = engine.simulate(scenario(), scenario_index=INDEX, replicate=REPLICATE, loaded=plating)
    assert [r["trail"] for r in moved.records] == [r["trail"] for r in result.records]  # labels never change the run
    changed = 0
    for old, new in zip(result.records, moved.records):
        lab, times = new["brief"]["label"], at(new)
        assert lab["clock_stop"] == "plated"
        if "plated" in times:
            assert lab["elapsed_min"] == pytest.approx(minutes_between(times["collected"], times["plated"]), abs=1e-3)
            assert lab["elapsed_min"] > old["brief"]["label"]["elapsed_min"]
        else:  # rejected at accessioning, lost or never sent: the clock never stops
            assert lab["elapsed_min"] is None and lab["breach"]
        assert lab["breach"] or not old["brief"]["label"]["breach"]  # a later stop never clears a breach
        changed += lab["breach"] != old["brief"]["label"]["breach"]
    assert changed > 50
    rejected = [r for r in moved.records if r["brief"]["planted_fault"] == "content_failure"]
    assert rejected and all(r["brief"]["v2"]["outcome_class"] == "lost" for r in rejected)
    assert validate_v2.validate(moved) == []


def test_a_policy_whose_clock_starts_at_dispatch_is_timed_from_the_hidden_timeline(result, loaded):
    dispatch = with_clock(loaded, clock_start="dispatched")
    moved = engine.simulate(scenario(), scenario_index=INDEX, replicate=REPLICATE, loaded=dispatch)
    checked = 0
    for old, new in zip(result.records, moved.records):
        segs = new["trail"]["truth_segments"]
        lab = new["brief"]["label"]
        if new["trail"]["outcome"]["received"]:
            left_unit = max(datetime.fromisoformat(s["end"]) for s in segs if s["state"] == "at_unit")
            assert lab["elapsed_min"] == pytest.approx(minutes_between(left_unit, at(new)["received"]), abs=1e-3)
            assert lab["elapsed_min"] <= old["brief"]["label"]["elapsed_min"]
            checked += 1
    assert checked > 1000


def test_a_state_the_simulator_does_not_time_is_a_policy_error(loaded):
    stained = with_clock(loaded, clock_stop="stained")
    with pytest.raises(config.PolicyError, match="does not time state 'stained'"):
        engine.simulate(scenario(), scenario_index=INDEX, replicate=REPLICATE, loaded=stained)


class _Mix:
    policy_id = "p"
    policy = {"window_min": 60, "urgency_tier": 3, "clock_start": "collected", "clock_stop": "received"}


@pytest.mark.parametrize("stop_after_min, breach, elapsed", [
    (59.5, False, 59.5), (60.0, False, 60.0), (60.5, True, 60.5), (7200.0, True, 7200.0), (7201.0, True, None)])
def test_a_clock_that_stops_after_the_horizon_never_stopped(stop_after_min, breach, elapsed):
    lab = records.label(_Mix, {"collected": 0, "received": int(stop_after_min * 60)})
    assert (lab["breach"], lab["elapsed_min"]) == (breach, elapsed)
    assert records.outcome_class(lab) == ("lost" if elapsed is None else "late" if breach else "on_time")
