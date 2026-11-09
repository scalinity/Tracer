import copy
import json

import numpy as np
import pytest

from src.scenarios import planner, random_baseline, schema, signature, validate

EXAMPLE = json.loads((schema.ROOT / "config" / "scenario.example.json").read_text(encoding="utf-8"))


def stub_kpis(s: dict) -> dict:
    """KPIs shaped like src/sim/kpi.kpis(), computed from the configuration (the simulator is not under test)."""
    shares = validate.route_shares(s)
    load = s["volume"]["specimens_per_weekday"] / 900
    return {"median_tube_min": 8 + 30 * load + 40 * shares["tube"], "median_courier_min": 40 + 60 * shares["courier"],
            "median_walk_in_min": 15 + 20 * shares["walk_in"], "breach_share": 0.02 + 0.1 * load + 0.01 * len(s["disruptions"]),
            "never_received_share": 0.01 + 0.005 * len(s["disruptions"])}


@pytest.fixture(scope="module")
def scenarios():
    return [random_baseline.random_scenario(seed) for seed in range(60)]


@pytest.fixture(scope="module")
def reference(scenarios):
    return signature.fit_reference(scenarios, [stub_kpis(s) for s in scenarios])


def nudged(s: dict) -> dict:
    """A near copy: same laboratory, one more specimen a day and a hair's difference in a rate."""
    t = copy.deepcopy(s)
    t["volume"]["specimens_per_weekday"] = min(900, t["volume"]["specimens_per_weekday"] + 1)
    t["tube_system"]["stalled_rate"] = round(min(0.06, t["tube_system"]["stalled_rate"] + 0.0001), 4)
    return t


# --- The signature ------------------------------------------------------------------------------------

def test_signature_has_32_named_numbers():
    assert signature.N_FEATURES == 32 and len(set(signature.FEATURE_NAMES)) == 32
    vec = signature.signature(EXAMPLE, stub_kpis(EXAMPLE))
    assert vec.shape == (32,) and np.isfinite(vec).all()


def test_signature_is_deterministic():
    a = signature.signature(EXAMPLE, stub_kpis(EXAMPLE))
    b = signature.signature(copy.deepcopy(EXAMPLE), stub_kpis(EXAMPLE))
    assert np.array_equal(a, b)


def test_signature_reads_the_scenario_and_the_kpis():
    names = list(signature.FEATURE_NAMES)
    base = signature.signature(EXAMPLE, stub_kpis(EXAMPLE))
    assert base[names.index("log_volume")] == pytest.approx(np.log(220))
    assert base[names.index("unit_count")] == 6 and base[names.index("tube_line_count")] == 2
    assert base[names.index("share_tube")] == pytest.approx(0.608) and base[names.index("share_offsite")] == pytest.approx(0.12)
    assert base[names.index("round_interval_night")] == pytest.approx(90.0)
    assert base[names.index("staff_per_100_night")] == pytest.approx(1 / 2.2)
    assert base[names.index("disruptions_tube")] == 1 and base[names.index("disruptions_laboratory")] == 2
    kpis = stub_kpis(EXAMPLE) | {"breach_share": 0.5}
    other = signature.signature(EXAMPLE, kpis)
    assert other[names.index("breach_share")] == 0.5
    assert np.array_equal(np.delete(other, names.index("breach_share")), np.delete(base, names.index("breach_share")))


def test_missing_kpis_count_as_zero():
    names = list(signature.FEATURE_NAMES)
    vec = signature.signature(EXAMPLE, {})
    assert all(vec[names.index(k)] == 0 for k in signature.KPI_KEYS)


def test_round_interval_averages_the_routes_that_run_and_no_routes_means_eight_hours():
    names = list(signature.FEATURE_NAMES)
    vec = signature.signature(EXAMPLE, {})
    assert vec[names.index("round_interval_night")] == pytest.approx(90.0)   # only the campus loop runs at night (van has none)
    no_routes = copy.deepcopy(EXAMPLE)
    no_routes["couriers"]["routes"] = []
    for u in no_routes["units"]:
        u["courier_route_id"] = None
    assert signature.signature(no_routes, {})[names.index("round_interval_day")] == signature.NO_SERVICE_MIN


def test_the_default_profile_basis_is_orthonormal():
    mean, comps = signature.default_basis()
    assert mean.shape == (24,) and comps.shape == (3, 24)
    assert np.allclose(comps @ comps.T, np.eye(3), atol=1e-9)


def test_fitted_profile_components_are_orthonormal_and_explain_the_shapes(scenarios):
    mean, comps = signature.fit_profile_basis(scenarios)
    assert comps.shape == (3, 24) and np.allclose(comps @ comps.T, np.eye(3), atol=1e-9)
    assert mean.sum() == pytest.approx(1)


# --- Scaler and reference --------------------------------------------------------------------------------

def test_robust_scaler_uses_median_and_iqr_and_survives_constant_features():
    raw = np.array([[1.0, 5.0], [2.0, 5.0], [3.0, 5.0], [4.0, 5.0], [100.0, 5.0]])
    center, scale = signature.robust_scaler(raw)
    assert center.tolist() == [3.0, 5.0]
    assert scale[0] == pytest.approx((4.0 - 2.0) / 1.349) and scale[1] == 1.0   # the outlier does not move the scale


def test_delta_is_half_the_median_nearest_neighbor_distance(scenarios, reference):
    x = reference.standardize(np.array([signature.signature(s, stub_kpis(s), reference) for s in scenarios]))
    assert reference.delta > 0
    assert reference.delta == pytest.approx(0.5 * np.median(signature.nearest_distances(x)))


def test_reference_round_trips_through_a_file(reference, tmp_path):
    reference.save(tmp_path / "ref.json")
    loaded = signature.Reference.load(tmp_path / "ref.json")
    for a, b in ((reference.center, loaded.center), (reference.scale, loaded.scale), (reference.pca_mean, loaded.pca_mean),
                 (reference.pca_components, loaded.pca_components)):
        assert np.allclose(a, b)
    assert loaded.delta == reference.delta


def test_a_reference_fitted_on_other_features_is_refused(reference, tmp_path):
    path = tmp_path / "ref.json"
    reference.save(path)
    body = json.loads(path.read_text())
    body["features"] = body["features"][:-1]
    path.write_text(json.dumps(body))
    with pytest.raises(ValueError):
        signature.Reference.load(path)


def test_a_reference_needs_a_few_scenarios():
    with pytest.raises(ValueError):
        signature.fit_reference([EXAMPLE], [stub_kpis(EXAMPLE)])


def test_the_scaler_can_be_fitted_on_a_larger_set_than_the_components(scenarios):
    small, kp = scenarios[:20], [stub_kpis(s) for s in scenarios[:20]]
    both = signature.fit_reference(small, kp, scaler_scenarios=scenarios, scaler_kpis=[stub_kpis(s) for s in scenarios])
    alone = signature.fit_reference(small, kp)
    assert np.allclose(both.pca_components, alone.pca_components) and not np.allclose(both.center, alone.center)


# --- Near duplicates --------------------------------------------------------------------------------------

def test_near_copies_are_near_duplicates_and_distinct_scenarios_are_not(scenarios, reference):
    base = scenarios[0]
    pair = [base, nudged(base)]
    x = reference.standardize(np.array([signature.signature(s, stub_kpis(s), reference) for s in pair + scenarios[1:6]]))
    found = signature.near_duplicate_pairs(x, [s["archetype"] for s in pair + scenarios[1:6]], reference.delta)
    assert [(i, j) for i, j, _ in found] == [(0, 1)]
    assert found[0][2] < reference.delta


def test_near_duplicates_need_the_same_archetype(scenarios, reference):
    base = scenarios[0]
    other = nudged(base)
    other["archetype"] = next(a for a in random_baseline.ARCHETYPES if a != base["archetype"])
    x = reference.standardize(np.array([signature.signature(s, stub_kpis(s), reference) for s in (base, other)]))
    assert signature.near_duplicate_pairs(x, [base["archetype"], other["archetype"]], reference.delta) == []


def test_the_later_item_id_is_the_one_rejected(scenarios, reference):
    base = scenarios[0]
    pair = [base, nudged(base), scenarios[7]]
    x = reference.standardize(np.array([signature.signature(s, stub_kpis(s), reference) for s in pair]))
    ids = ["O2-0007-1", "O2-0003-1", "O2-0001-1"]
    assert signature.later_duplicates(ids, x, [s["archetype"] for s in pair], reference.delta) == {"O2-0007-1"}


def test_library_names_the_duplicate_it_found(scenarios, reference):
    library = signature.Library(reference, stub_kpis)
    library.add("O2-0001-1", scenarios[0])
    library.add("O2-0002-1", scenarios[1])
    message = library.duplicate_of(nudged(scenarios[0]))
    assert message and "O2-0001-1" in message and "dropped, not repaired" in message
    assert library.duplicate_of(scenarios[9]) is None
    different = nudged(scenarios[0])
    different["archetype"] = next(a for a in random_baseline.ARCHETYPES if a != scenarios[0]["archetype"])
    assert library.duplicate_of(different) is None


def test_an_empty_library_has_no_duplicates(reference):
    assert signature.Library(reference, stub_kpis).duplicate_of(EXAMPLE) is None


def test_validate_rejects_a_near_duplicate_at_layer_6(reference):
    cell = {"stratum": "disrupted", "archetype": "community_hospital", "size_band": "medium", "transport_mix": "mixed",
            "staffing": "reduced_nights", "failure_focus": "tube_outage", "edge_tags": [], "sub_seed": 1}
    brief = planner.make_brief(cell) | {"brief_id": "T-0001", "checks": []}
    library = signature.Library(reference, stub_kpis)
    assert validate.validate(copy.deepcopy(EXAMPLE), brief, library=library).ok
    library.add("O2-0001-1", EXAMPLE)
    result = validate.validate(nudged(EXAMPLE), brief, library=library)
    assert result.layer == "6 diversity" and not result.repairable and "O2-0001-1" in result.errors[0]


# --- Families ------------------------------------------------------------------------------------------------

def test_families_join_chains_by_union_find():
    # A-B and B-C are closer than 1.5 delta, A-C is not: all three are one family. D stands alone.
    x = np.array([[0.0, 0], [1.4, 0], [2.8, 0], [10, 0]])
    assert signature.families(x, 1.0).tolist() == [0, 0, 0, 1]


def test_families_label_by_first_member_and_ignore_archetype():
    x = np.array([[0.0, 0], [50, 0], [0.5, 0], [50.5, 0], [100, 0]])
    assert signature.families(x, 1.0).tolist() == [0, 1, 0, 1, 2]


def test_family_threshold_is_one_and_a_half_delta():
    x = np.array([[0.0, 0], [1.49, 0]])
    assert signature.families(x, 1.0).tolist() == [0, 0]
    assert signature.families(x, 0.99).tolist() == [0, 1]


def test_families_of_a_real_set_include_the_near_copy(scenarios, reference):
    pool = [scenarios[0], nudged(scenarios[0])] + scenarios[1:15]
    x = reference.standardize(np.array([signature.signature(s, stub_kpis(s), reference) for s in pool]))
    labels = signature.families(x, reference.delta)
    assert labels[0] == labels[1] and len(set(labels)) > 1


# --- Coverage, dispersion, collapse ------------------------------------------------------------------------------

def test_vendi_score_of_identical_items_is_one_and_of_orthogonal_items_is_n():
    assert signature.vendi_score(np.ones((6, 4))) == pytest.approx(1.0)
    assert signature.vendi_score(100 * np.eye(5), sigma=1.0) == pytest.approx(5.0)
    assert signature.vendi_score(np.zeros((1, 3))) == pytest.approx(1.0)


def test_vendi_score_counts_clusters():
    two = np.vstack([np.zeros((4, 3)), 1000 * np.ones((4, 3))])
    assert signature.vendi_score(two, sigma=1.0) == pytest.approx(2.0)
    spread = np.random.default_rng(0).normal(size=(30, 6))
    assert 1.0 < signature.vendi_score(spread) < 30.0
    assert signature.vendi_score(spread * 3, sigma=2.0) > signature.vendi_score(spread * 0.2, sigma=2.0)


def test_mean_nearest_distance_and_dispersion_by_stratum():
    x = np.array([[0.0, 0], [3, 0], [3, 4], [100, 0], [103, 0]])
    assert signature.mean_nearest_distance(x[:3]) == pytest.approx((3 + 3 + 4) / 3)
    report = signature.dispersion(x, ["a", "a", "a", "b", "b"], sigma=5.0)
    assert set(report) == {"all", "a", "b"} and report["a"]["n"] == 3 and report["b"]["mean_nn"] == pytest.approx(3.0)
    assert report["all"]["n"] == 5 and report["a"]["vendi"] > 1


def test_coverage_counts_cells_marginals_and_edge_tags_against_the_plan():
    planned = planner.plan("O1", 3)
    full = signature.coverage([{"brief_id": b["brief_id"]} for b in planned], planned)
    assert full["ok"] and full["cells"] and set(full["marginals"]) >= {"archetype", "stratum", "failure_focus"}
    assert all(v["count"] == v["target"] for v in full["marginals"]["stratum"].values())
    assert sum(v["target"] for v in full["edge_tags"].values()) == sum(len(b["edge_tags"]) for b in planned)
    half = signature.coverage([{"brief_id": b["brief_id"]} for b in planned[::2]], planned)
    assert not half["ok"]


def test_a_level_at_90_percent_of_target_passes_and_below_fails():
    planned = [{"brief_id": f"T-{i}", "stratum": "normal", "archetype": "cancer_center", "size_band": "small",
                "transport_mix": "mixed", "staffing": "full_24_7", "failure_focus": "surge", "edge_tags": []} for i in range(10)]
    nine = signature.coverage([{"brief_id": b["brief_id"]} for b in planned[:9]], planned)
    eight = signature.coverage([{"brief_id": b["brief_id"]} for b in planned[:8]], planned)
    assert nine["ok"] and not eight["ok"]


def test_collapse_check_flags_a_stratum_whose_scenarios_all_look_alike():
    same = [{"median_tube_min": 20, "breach_share": 0.05}] * 5
    varied = [{"median_tube_min": m, "breach_share": b} for m, b in ((10, 0.02), (20, 0.05), (30, 0.10), (45, 0.2))]
    report = signature.collapse_check({"normal": same, "stressed": varied})
    assert report["normal"]["flagged"] and report["normal"]["cv_median_transit"] == 0
    assert not report["stressed"]["flagged"] and report["stressed"]["cv_breach_share"] >= signature.COLLAPSE_CV


def test_collapse_check_flags_when_either_measure_is_flat():
    flat_breach = [{"median_tube_min": m, "breach_share": 0.05} for m in (10, 20, 30, 45)]
    assert signature.collapse_check({"edge": flat_breach})["edge"]["flagged"]


def test_median_transit_prefers_the_explicit_key_then_averages_routes():
    assert signature.median_transit({"median_transit_min": 33, "median_tube_min": 10}) == 33
    assert signature.median_transit({"median_tube_min": 10, "median_courier_min": 30, "median_walk_in_min": 0}) == 20
    assert signature.median_transit({}) == 0
