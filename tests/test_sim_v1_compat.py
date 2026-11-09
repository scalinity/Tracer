"""The v1 world ties the simulator to v1 (docs/neural/spec.md §5.9, §15.2): its trails pass v1's
validator, every leg matches its configured median and 90th percentile, and its breach share sits
inside the 99% binomial interval around v1's requested 522 of 12,000."""

import math
from collections import Counter
from datetime import datetime

import pytest
from scipy import stats

from src.generator import config, validate
from src.sim import engine, records, v1world, validate_v2

KS_DRAWS = 5000
KS_MIN_P = 0.01
V1_BREACHES, V1_SPECIMENS = 522, 12000
# The leg each truth state times, and its v1 configuration (config/config.yaml legs).
LEG_OF_STATE = {"at_unit": "dispatch_wait", "in_tube": "tube_transit", "at_station": "station_dwell",
                "hand_carry": "hand_carry_transit", "at_receiving_bench": "accession_wait",
                "accessioned": "plating_wait"}


@pytest.fixture(scope="module")
def loaded():
    return config.load()


@pytest.fixture(scope="module")
def world(loaded):
    """The v1 world at v1's volume, all 20 replicates of scenario index 9000."""
    sc = v1world.scenario(loaded)
    return [engine.simulate(sc, scenario_index=v1world.SCENARIO_INDEX, replicate=r, loaded=loaded)
            for r in range(v1world.REPLICATES)]


@pytest.fixture(scope="module")
def large(loaded):
    """Every leg's sample from the v1 world at 20 times v1's volume, enough for 5,000 draws of each
    (the v1 world shares no resource, so volume changes nothing about any one specimen)."""
    sc = v1world.scenario(loaded, specimens_per_weekday=20 * sum(loaded["config"]["arrivals"]["rate_per_hour"]))
    return leg_samples([engine.simulate(sc, scenario_index=v1world.SCENARIO_INDEX, replicate=r, loaded=loaded)
                        for r in range(3)])


def test_every_v1_world_trail_passes_the_v1_validator(world):
    checked, failures = 0, Counter()
    for result in world:
        for rec in result.records:
            view = records.v1_view(rec)
            for f in validate.validate(view["brief"], view["trail"]):
                failures[str(f)] += 1
            checked += 1
    assert checked > 8000
    assert not failures, failures.most_common(5)


def test_every_v1_world_replicate_holds_the_v2_invariants(world):
    for result in world:
        assert validate_v2.validate(result) == []


def test_v1_view_only_drops_the_substate_and_the_v2_block(world):
    rec = world[0].records[10]
    view = records.v1_view(rec)
    assert "v2" not in view["brief"] and "v2" in rec["brief"]
    assert all("substate" not in s for s in view["trail"]["truth_segments"])
    assert view["trail"]["observed_events"] == rec["trail"]["observed_events"]
    assert {k: v for k, v in view["brief"].items()} == {k: v for k, v in rec["brief"].items() if k != "v2"}


def _minutes(seg: dict) -> float:
    return (datetime.fromisoformat(seg["end"]) - datetime.fromisoformat(seg["start"])).total_seconds() / 60


def leg_samples(results) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {leg: [] for leg in LEG_OF_STATE.values()}
    for result in results:
        for rec in result.records:
            for seg in rec["trail"]["truth_segments"]:
                leg = LEG_OF_STATE.get(seg["state"])
                if leg and seg["end"] is not None:
                    out[leg].append(_minutes(seg))
    return out


@pytest.mark.parametrize("leg", sorted(LEG_OF_STATE.values()))
def test_each_leg_matches_its_configured_median_and_90th_percentile(large, loaded, leg):
    sample = large[leg]
    assert len(sample) >= KS_DRAWS, (leg, len(sample))
    cfg = loaded["config"]["legs"][leg]
    sigma = math.log(cfg["p90_min"] / cfg["median_min"]) / stats.norm.ppf(0.9)
    result = stats.kstest(sample[:KS_DRAWS], "lognorm", args=(sigma, 0, cfg["median_min"]))
    assert result.pvalue > KS_MIN_P, (leg, result)


def test_the_breach_share_is_inside_the_99_percent_interval_around_v1s(world):
    scored = [r for result in world for r in result.records if r["brief"]["v2"]["scored"]]
    share = sum(r["brief"]["label"]["breach"] for r in scored) / len(scored)
    p = V1_BREACHES / V1_SPECIMENS
    lo, hi = (stats.binom.ppf(q, V1_SPECIMENS, p) / V1_SPECIMENS for q in (0.005, 0.995))
    assert lo <= share <= hi, (share, lo, hi, len(scored))


def test_faults_come_one_per_specimen_at_v1s_rates_and_tube_faults_ride_the_tube(world, loaded):
    scored = [r for result in world for r in result.records if r["brief"]["v2"]["scored"]]
    faults = Counter(r["brief"]["planted_fault"] for r in scored)
    assert set(faults) == set(config.FAULTS)
    for name, f in loaded["config"]["faults"].items():
        expected = f["rate"] * len(scored)
        assert abs(faults[name] - expected) < 4 * math.sqrt(expected) + 2, (name, faults[name], expected)
    for r in scored:
        if r["brief"]["planted_fault"] in ("lost_in_tube", "stalled_at_station", "misrouted"):
            assert r["brief"]["fixed"]["route"] == "tube"
        if r["brief"]["planted_fault"] == "misrouted":
            assert "tube_station_pharmacy" in r["brief"]["locations"]


def test_the_v1_world_draws_v1s_volume_mix_units_and_priority(world, loaded):
    cfg = loaded["config"]
    scored = [r for result in world for r in result.records if r["brief"]["v2"]["scored"]]
    weekly = sum(cfg["arrivals"]["rate_per_hour"]) * (5 + 2 * cfg["arrivals"]["weekend_factor"])
    assert abs(len(scored) / len(world) - weekly) < 0.05 * weekly
    units = Counter(r["brief"]["fixed"]["unit"] for r in scored)
    for unit, weight in cfg["collecting_units"]["weights"].items():
        assert abs(units[unit] / len(scored) - weight) < 0.02, unit
    stat = sum(r["brief"]["fixed"]["priority"] == "stat" for r in scored) / len(scored)
    assert abs(stat - cfg["priority"]["stat_fraction"]) < 0.02


def test_the_v1_world_run_keys_never_collide_with_a_library_scenarios():
    keys = [records.run_key(v1world.SCENARIO_INDEX, r) for r in range(v1world.REPLICATES)]
    assert keys == list(range(190000, 190020))
    library_max = records.run_key(v1world.SCENARIO_INDEX - 1, 9)
    assert library_max < min(keys)
    assert records.run_key(v1world.RESERVED_INDEX, 0) in keys  # why 9001 is reserved
