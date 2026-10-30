import copy
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timedelta

import numpy as np
import pytest

from src.generator import briefs as briefs_mod
from src.generator import config
from src.generator.briefs import make_briefs


def sha(briefs) -> str:
    return hashlib.sha256(json.dumps(briefs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def three_se(p: float, n: int, k: float = 3) -> float:
    return k * math.sqrt(p * (1 - p) / n)


@pytest.fixture(scope="module")
def loaded():
    return config.load()


@pytest.fixture(scope="module")
def big(loaded):
    return make_briefs(0, 0, 20000, loaded)


# ---- D2: arrivals, mix and faults ----

def test_same_arguments_give_byte_identical_output(loaded):
    assert sha(make_briefs(3, 2, 500, loaded)) == sha(make_briefs(3, 2, 500, loaded))


def test_seed_and_run_both_change_the_output(loaded):
    base = sha(make_briefs(0, 0, 200, loaded))
    assert sha(make_briefs(1, 0, 200, loaded)) != base
    assert sha(make_briefs(0, 1, 200, loaded)) != base


def test_output_is_plain_json_with_unique_sorted_ids(loaded):
    briefs = make_briefs(0, 7, 300, loaded)
    json.dumps(briefs)  # numpy scalars would raise here
    assert len(briefs) == 300
    assert [b["specimen_id"] for b in briefs] == [f"SP-r07-{i:06d}" for i in range(1, 301)]
    stamps = [b["fixed"]["collected_at"] for b in briefs]
    assert stamps == sorted(stamps)


def test_a_run_covers_its_own_week(loaded):
    first = [datetime.fromisoformat(b["fixed"]["collected_at"]) for b in make_briefs(0, 0, 2000, loaded)]
    second = [datetime.fromisoformat(b["fixed"]["collected_at"]) for b in make_briefs(0, 1, 2000, loaded)]
    start = datetime(2026, 3, 2)
    assert start <= min(first) and max(first) < start + timedelta(days=7)
    assert start + timedelta(days=7) <= min(second) and max(second) < start + timedelta(days=14)


def test_collection_follows_the_hourly_profile_and_weekend_factor(loaded, big):
    cfg = loaded["config"]
    rate, wf = np.array(cfg["arrivals"]["rate_per_hour"]), cfg["arrivals"]["weekend_factor"]
    stamps = [datetime.fromisoformat(b["fixed"]["collected_at"]) for b in big]
    weekday_mass = rate.sum() * (5 + 2 * wf)
    hours = Counter(t.hour for t in stamps)
    for h in range(24):
        p = rate[h] * (5 + 2 * wf) / weekday_mass
        assert abs(hours[h] / len(big) - p) < three_se(p, len(big), 4), h
    p_weekend = 2 * wf / (5 + 2 * wf)
    weekend = sum(t.weekday() >= 5 for t in stamps) / len(big)
    assert abs(weekend - p_weekend) < three_se(p_weekend, len(big), 4)


def test_fault_mix_is_within_three_standard_errors_of_config(loaded):
    briefs = make_briefs(0, 0, 10000, loaded)
    counts = Counter(b["planted_fault"] for b in briefs)
    for name, fault in loaded["config"]["faults"].items():
        assert abs(counts[name] / 10000 - fault["rate"]) < three_se(fault["rate"], 10000), name


def test_specimen_mix_matches_config(loaded, big):
    entries = loaded["config"]["specimen_mix"]["entries"]
    policies = loaded["policy"]["policies"]
    share = Counter((b["fixed"]["type"], tuple(b["fixed"]["tests"])) for b in big)
    for name, entry in entries.items():
        key = (policies[entry["policies"][0]]["specimen_type"], tuple(policies[p]["test"] for p in entry["policies"]))
        assert abs(share[key] / len(big) - entry["weight"]) < three_se(entry["weight"], len(big), 4), name


def test_only_combinations_the_policy_has(loaded, big):
    rows = {(p["specimen_type"], p["test"]) for p in loaded["policy"]["policies"].values()}
    for b in big[:2000]:
        assert all((b["fixed"]["type"], t) in rows for t in b["fixed"]["tests"])


def test_unit_priority_and_route(loaded, big):
    cfg = loaded["config"]
    n = len(big)
    units = Counter(b["fixed"]["unit"] for b in big)
    for unit, w in cfg["collecting_units"]["weights"].items():
        assert abs(units[unit] / n - w) < three_se(w, n, 4), unit
    stat = sum(b["fixed"]["priority"] == "stat" for b in big) / n
    p = cfg["priority"]["stat_fraction"]
    assert abs(stat - p) < three_se(p, n, 4)
    # Tube faults force the tube, so the tube share is the config share plus that excess.
    tube_fault = sum(f["rate"] for k, f in cfg["faults"].items() if k in briefs_mod.TUBE_FAULTS)
    q = cfg["route"]["tube_probability"]
    expected = tube_fault + (1 - tube_fault) * q
    assert abs(sum(b["fixed"]["route"] == "tube" for b in big) / n - expected) < three_se(expected, n, 4)


def test_tube_faults_only_on_the_tube(big):
    for b in big:
        if b["planted_fault"] in briefs_mod.TUBE_FAULTS:
            assert b["fixed"]["route"] == "tube"


# ---- D3: the timing skeleton ----

STAGES = ("dispatch_wait_min", "tube_transit_min", "station_dwell_min", "hand_carry_min", "stalled_min",
          "misrouted_min")


def test_stage_times_add_up_to_the_receipt(big):
    for b in big:
        t = b["targets"]
        if t["receipt_after_min"] is None:
            continue
        assert sum(t[k] for k in STAGES if t[k] is not None) == t["receipt_after_min"], b["specimen_id"]
        if b["fixed"]["route"] == "tube":
            assert t["hand_carry_min"] is None and t["tube_transit_min"] and t["station_dwell_min"]
        else:
            assert t["tube_transit_min"] is None and t["station_dwell_min"] is None and t["hand_carry_min"]


def test_losses_and_unsent_specimens_have_no_receipt(big):
    for b in big:
        lost = b["planted_fault"] in ("lost_in_tube", "not_dispatched", "never_collected")
        assert (b["targets"]["receipt_after_min"] is None) == lost, b["specimen_id"]
        if lost:
            assert b["targets"]["accession_wait_min"] is None and b["targets"]["plate_wait_min"] is None
            assert b["label"]["breach"] is True


def test_what_each_fault_leaves_in_the_targets(big):
    seen = set()
    for b in big:
        t, fault = b["targets"], b["planted_fault"]
        seen.add(fault)
        assert (t["stalled_min"] is not None) == (fault == "stalled_at_station")
        assert (t["misrouted_min"] is not None) == (fault == "misrouted")
        if fault == "lost_in_tube":
            assert t["dispatch_wait_min"] and t["tube_transit_min"] and t["station_dwell_min"] is None
        if fault in ("not_dispatched", "never_collected"):
            assert all(v is None for v in t.values())
        if fault == "content_failure":
            assert t["accession_wait_min"] and t["plate_wait_min"] is None
    assert seen == set(config.FAULTS)


def test_delays_stay_inside_their_configured_ranges(loaded, big):
    for name, key in (("stalled_at_station", "stalled_min"), ("misrouted", "misrouted_min")):
        lo, hi = loaded["config"]["faults"][name]["delay_min"]
        values = [b["targets"][key] for b in big if b["planted_fault"] == name]
        assert len(values) > 50
        assert lo <= min(values) and max(values) <= hi
        assert min(values) < lo + 0.1 * (hi - lo) and max(values) > hi - 0.1 * (hi - lo)  # spans the range


def test_legs_recover_the_configured_median_and_p90(loaded, big):
    for key, leg in (("dispatch_wait_min", "dispatch_wait"), ("tube_transit_min", "tube_transit"),
                     ("hand_carry_min", "hand_carry_transit")):
        values = [b["targets"][key] for b in big if b["targets"][key] is not None]
        want = loaded["config"]["legs"][leg]
        assert abs(np.median(values) - want["median_min"]) <= 1, key
        assert abs(np.percentile(values, 90) - want["p90_min"]) <= max(1.5, 0.08 * want["p90_min"]), key
        assert min(values) >= 1


def test_same_seed_gives_the_same_targets(loaded):
    a, b = make_briefs(5, 3, 400, loaded), make_briefs(5, 3, 400, loaded)
    assert sha([x["targets"] for x in a]) == sha([x["targets"] for x in b])


def test_retuning_a_leg_moves_timings_but_not_who_is_lost(loaded):
    tuned = copy.deepcopy(loaded)
    tuned["config"]["legs"]["tube_transit"]["median_min"] = 9
    tuned["config"]["legs"]["tube_transit"]["p90_min"] = 20
    a, b = make_briefs(0, 0, 500, loaded), make_briefs(0, 0, 500, tuned)
    assert [(x["specimen_id"], x["fixed"], x["planted_fault"]) for x in a] == \
           [(x["specimen_id"], x["fixed"], x["planted_fault"]) for x in b]
    assert [x["targets"]["tube_transit_min"] for x in a] != [x["targets"]["tube_transit_min"] for x in b]
    assert [x["targets"]["dispatch_wait_min"] for x in a] == [x["targets"]["dispatch_wait_min"] for x in b]


def test_locations_cover_the_route(big):
    for b in big[:3000]:
        locs, unit = b["locations"], b["fixed"]["unit"]
        assert locs[0] == f"{unit}_collection" and "lab_receiving" in locs
        assert (f"tube_station_{unit}" in locs) == (b["fixed"]["route"] == "tube")
        assert ("tube_station_pharmacy" in locs) == (b["planted_fault"] == "misrouted")


# ---- the label reads the clock from the policy ----

def test_window_and_breach_come_from_the_resolved_policy(loaded, big):
    policy = loaded["policy"]
    for b in big:
        _, p = config.resolve(policy, b["fixed"]["type"], b["fixed"]["tests"])
        lab = b["label"]
        assert lab["window_min"] == p["window_min"] and lab["policy_id"] in policy["policies"]
        assert (lab["clock_start"], lab["clock_stop"]) == (p["clock_start"], p["clock_stop"])
        received = b["targets"]["receipt_after_min"]
        assert lab["breach"] == (received is None or received > p["window_min"]), b["specimen_id"]
        assert lab["elapsed_min"] == received


def test_the_shortest_window_among_the_tests_governs(loaded, big):
    multi = [b for b in big if b["fixed"]["type"] == "BAL, refrigerated"]
    assert multi and all(b["label"]["window_min"] == 1440 and len(b["fixed"]["tests"]) == 3 for b in multi)


def test_a_boundary_receipt_is_not_a_breach(loaded):
    policy = loaded["policy"]["policies"]["urine_unpreserved_room_temp__culture"]
    on_time = briefs_mod._label("urine_unpreserved_room_temp__culture", policy, {"collected": 0, "received": 60})
    late = briefs_mod._label("urine_unpreserved_room_temp__culture", policy, {"collected": 0, "received": 61})
    assert (on_time["breach"], late["breach"]) == (False, True)


def test_moving_the_clock_stop_moves_the_label_without_code_changes(loaded, big):
    plated = copy.deepcopy(loaded)
    for p in plated["policy"]["policies"].values():
        p["clock_stop"] = "plated"
    moved = make_briefs(0, 0, 20000, plated)
    assert [b["fixed"] for b in moved] == [b["fixed"] for b in big]
    for a, b in zip(big, moved):
        t = b["targets"]
        if b["planted_fault"] == "none":
            assert b["label"]["elapsed_min"] == t["receipt_after_min"] + t["accession_wait_min"] + t["plate_wait_min"]
        if b["planted_fault"] == "content_failure":
            assert b["label"]["breach"] is True  # rejected at accessioning, never plated
    assert sum(b["label"]["breach"] for b in moved) > sum(b["label"]["breach"] for b in big)


def test_a_clock_the_generator_cannot_time_fails_loudly(loaded):
    reported = copy.deepcopy(loaded)
    for p in reported["policy"]["policies"].values():
        p["clock_stop"] = "reported"
    with pytest.raises(config.PolicyError, match="does not time state 'reported'"):
        make_briefs(0, 0, 10, reported)


def test_never_collected_has_no_running_clock(big):
    rows = [b for b in big if b["planted_fault"] == "never_collected"]
    assert rows and all(not b["label"]["clock_started"] and b["label"]["breach"] for b in rows)
    assert all(b["label"]["clock_started"] for b in big if b["planted_fault"] != "never_collected")


# ---- the table ----

def test_cli_prints_a_table_of_every_brief(capsys):
    assert briefs_mod.main(["--seed", "0", "--run", "0", "--n", "200"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split()[:3] == ["id", "collected", "type"]
    assert len(lines) == 1 + 200 + 1
    assert lines[1].startswith("SP-r00-000001") and lines[-1].startswith("200 specimens")
