import copy

import pytest

from src.generator import config


@pytest.fixture(scope="module")
def loaded():
    return config.load()


@pytest.fixture
def cfg(loaded):
    return copy.deepcopy(loaded["config"])


def check(cfg, loaded):
    config.validate_config(cfg, loaded["policy"], loaded["graph"])


def test_config_loads_with_a_hash(loaded):
    assert loaded["config"]["version"] == 1
    assert len(loaded["config_hash"]) == 16
    assert loaded["config_hash"] != loaded["policy_hash"]


def test_committed_config_is_valid(loaded, cfg):
    check(cfg, loaded)


def test_every_mix_entry_has_a_policy_row(loaded):
    policies = loaded["policy"]["policies"]
    for name, entry in loaded["config"]["specimen_mix"]["entries"].items():
        assert entry["policies"], name
        for pid in entry["policies"]:
            assert pid in policies, f"{name}: {pid}"


def test_every_policy_variant_is_in_the_mix(loaded):
    """The mix covers all 12 specimen variants, not a subset of the table."""
    policies = loaded["policy"]["policies"]
    in_mix = {policies[pid]["specimen_type"]
              for e in loaded["config"]["specimen_mix"]["entries"].values() for pid in e["policies"]}
    assert in_mix == {p["specimen_type"] for p in policies.values()}


def test_every_parameter_is_marked_assumed(loaded):
    c = loaded["config"]
    groups = [c["run"], c["arrivals"], c["specimen_mix"], c["collecting_units"], c["priority"], c["route"],
              *c["legs"].values(), *c["faults"].values()]
    assert all(g["source"] == "assumed" for g in groups)


def test_every_graph_state_is_reachable_from_ordered(loaded):
    assert set(loaded["graph"]) == config.reachable(loaded["graph"], "ordered")


def test_misrouted_sits_between_the_tube_and_the_station(loaded):
    g = loaded["graph"]
    assert "misrouted" in g["in_tube"]["next"]
    assert "at_station" in g["misrouted"]["next"]
    assert g["misrouted"]["scan"] is False


# ---- the four failures the loader exists to catch ----

def test_mix_weights_must_sum_to_one(loaded, cfg):
    cfg["specimen_mix"]["entries"]["tissue"]["weight"] += 0.001
    with pytest.raises(config.PolicyError, match="specimen_mix weights must sum to 1"):
        check(cfg, loaded)


def test_fault_rates_must_sum_to_one(loaded, cfg):
    cfg["faults"]["none"]["rate"] -= 0.001
    with pytest.raises(config.PolicyError, match="fault rates must sum to 1"):
        check(cfg, loaded)


def test_sum_tolerance_is_one_in_a_billion(loaded, cfg):
    cfg["faults"]["none"]["rate"] += 5e-10
    check(cfg, loaded)
    cfg["faults"]["none"]["rate"] += 1e-9
    with pytest.raises(config.PolicyError, match="fault rates"):
        check(cfg, loaded)


def test_mix_entry_naming_no_policy_fails(loaded, cfg):
    cfg["specimen_mix"]["entries"]["tissue"]["policies"] = []
    with pytest.raises(config.PolicyError, match="tissue.*names no policy"):
        check(cfg, loaded)


def test_mix_entry_naming_an_unknown_policy_fails(loaded, cfg):
    cfg["specimen_mix"]["entries"]["tissue"]["policies"] = ["tissue__cold_fusion"]
    with pytest.raises(config.PolicyError, match="tissue__cold_fusion.*not a policy"):
        check(cfg, loaded)


def test_mix_entry_cannot_span_specimen_types(loaded, cfg):
    cfg["specimen_mix"]["entries"]["tissue"]["policies"].append("urine_boric_acid__culture")
    with pytest.raises(config.PolicyError, match="tissue.*more than one specimen type"):
        check(cfg, loaded)


def test_fault_naming_a_missing_state_fails(loaded, cfg):
    cfg["faults"]["misrouted"]["states"].append("teleported")
    with pytest.raises(config.PolicyError, match="misrouted.*teleported.*not a workflow state"):
        check(cfg, loaded)


def test_fault_set_is_exactly_the_seven_modes(loaded, cfg):
    del cfg["faults"]["lost_in_tube"]
    with pytest.raises(config.PolicyError, match="fault modes"):
        check(cfg, loaded)


@pytest.mark.parametrize("leg", sorted(config.LEGS))
@pytest.mark.parametrize("field, value", [("median_min", 0), ("median_min", -3), ("p90_min", 0), ("p90_min", -1)])
def test_leg_median_and_spread_must_be_positive(loaded, cfg, leg, field, value):
    cfg["legs"][leg][field] = value
    with pytest.raises(config.PolicyError, match=f"{leg}.*{field}.*positive"):
        check(cfg, loaded)


def test_leg_spread_must_exceed_the_median(loaded, cfg):
    cfg["legs"]["tube_transit"]["p90_min"] = cfg["legs"]["tube_transit"]["median_min"]
    with pytest.raises(config.PolicyError, match="tube_transit.*p90_min.*above"):
        check(cfg, loaded)


# ---- the rest of what the generator relies on ----

def test_missing_leg_fails(loaded, cfg):
    del cfg["legs"]["plating_wait"]
    with pytest.raises(config.PolicyError, match="legs"):
        check(cfg, loaded)


def test_arrivals_need_24_hours(loaded, cfg):
    cfg["arrivals"]["rate_per_hour"].pop()
    with pytest.raises(config.PolicyError, match="24"):
        check(cfg, loaded)


def test_negative_arrival_rate_fails(loaded, cfg):
    cfg["arrivals"]["rate_per_hour"][3] = -1
    with pytest.raises(config.PolicyError, match="rate_per_hour"):
        check(cfg, loaded)


def test_route_probabilities_must_sum_to_one(loaded, cfg):
    cfg["route"]["tube_probability"] = 0.5
    with pytest.raises(config.PolicyError, match="route probabilities must sum to 1"):
        check(cfg, loaded)


def test_unit_weights_must_sum_to_one(loaded, cfg):
    cfg["collecting_units"]["weights"]["ed"] = 0.5
    with pytest.raises(config.PolicyError, match="collecting_units weights must sum to 1"):
        check(cfg, loaded)


def test_stat_fraction_is_a_probability(loaded, cfg):
    cfg["priority"]["stat_fraction"] = 1.2
    with pytest.raises(config.PolicyError, match="stat_fraction"):
        check(cfg, loaded)


def test_stall_delay_needs_an_ordered_positive_range(loaded, cfg):
    cfg["faults"]["stalled_at_station"]["delay_min"] = [180, 30]
    with pytest.raises(config.PolicyError, match="stalled_at_station.*delay_min"):
        check(cfg, loaded)


def test_a_group_without_a_source_fails(loaded, cfg):
    del cfg["legs"]["station_dwell"]["source"]
    with pytest.raises(config.PolicyError, match="station_dwell.*source"):
        check(cfg, loaded)


def test_run_start_must_be_a_date(loaded, cfg):
    cfg["run"]["start_date"] = "next tuesday"
    with pytest.raises(config.PolicyError, match="start_date"):
        check(cfg, loaded)


# ---- the hash ----

def test_hash_follows_values_not_comments(loaded, cfg):
    assert config.config_hash(cfg) == loaded["config_hash"]
    cfg["legs"]["tube_transit"]["median_min"] += 1
    assert config.config_hash(cfg) != loaded["config_hash"]
