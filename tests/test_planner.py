import collections

import numpy as np
import pytest

from src.scenarios import planner, validate

FACTORS = ("archetype", "size_band", "failure_focus", "transport_mix", "staffing")
STRATA = planner.STRATA
OOD_ARCHETYPE = "multi_site_system"


@pytest.fixture(scope="module")
def o1():
    return planner.plan("O1", 1)


@pytest.fixture(scope="module")
def o2():
    return planner.plan("O2", 7, n=2250)


def by_stratum(briefs):
    out = collections.defaultdict(list)
    for b in briefs:
        out[b["stratum"]].append(b)
    return out


def worst_marginal_gap(briefs, ood):
    """The largest distance of any factor level's count from its exact target, within each stratum."""
    gap = 0.0
    for stratum, items in by_stratum(briefs).items():
        shares = planner.level_shares(stratum, ood)
        for factor in FACTORS:
            total = sum(shares[factor].values())
            counts = collections.Counter(b[factor] for b in items)
            assert set(counts) <= set(shares[factor]), (stratum, factor)
            for level, share in shares[factor].items():
                gap = max(gap, abs(counts.get(level, 0) - share / total * len(items)))
    return gap


# --- Determinism and shape ---------------------------------------------------------------------

def test_same_seed_gives_the_same_briefs():
    assert planner.plan("O2", 3, n=300) == planner.plan("O2", 3, n=300)
    assert planner.plan("O1", 3) == planner.plan("O1", 3)


def test_different_seeds_give_different_briefs():
    assert planner.plan("O1", 1) != planner.plan("O1", 2)


def test_o1_is_24_briefs_6_per_stratum(o1):
    assert len(o1) == 24
    assert {s: len(b) for s, b in by_stratum(o1).items()} == {s: 6 for s in STRATA}
    assert [b["brief_id"] for b in o1] == [f"O1-{i:04d}" for i in range(1, 25)]


def test_o1b_is_12_new_briefs_3_per_stratum_different_from_o1(o1):
    o1b = planner.plan("O1b", 1)
    assert len(o1b) == 12
    assert {s: len(b) for s, b in by_stratum(o1b).items()} == {s: 3 for s in STRATA}
    assert {planner.cell_key(b) for b in o1b}.isdisjoint({planner.cell_key(b) for b in o1})
    assert {b["brief_id"] for b in o1b}.isdisjoint({b["brief_id"] for b in o1})


def test_strata_are_interleaved_so_any_prefix_is_balanced(o1):
    assert collections.Counter(b["stratum"] for b in o1[:8]) == {s: 2 for s in STRATA}


def test_brief_id_is_stable_for_a_job_and_index():
    full = planner.plan("O2", 5, n=100)
    assert [b["brief_id"] for b in full] == [f"O2-{i:04d}" for i in range(1, 101)]


def test_n_as_a_total_follows_the_stratum_shares():
    counts = collections.Counter(b["stratum"] for b in planner.plan("O2", 1, n=1000))
    assert counts == {"normal": 350, "stressed": 350, "disrupted": 200, "edge": 100}


def test_n_as_a_dict_sets_counts_per_stratum():
    briefs = planner.plan("O2", 1, n={"normal": 5, "edge": 7})
    assert collections.Counter(b["stratum"] for b in briefs) == {"normal": 5, "edge": 7}
    with pytest.raises(ValueError):
        planner.plan("O2", 1, n={"weird": 3})


def test_unknown_job_is_refused():
    with pytest.raises(ValueError):
        planner.plan("O9", 1)


def test_exclude_ids_drops_briefs_and_keeps_the_other_ids():
    full = planner.plan("O2", 2, n=40)
    kept = planner.plan("O2", 2, n=40, exclude_ids={"O2-0003", "O2-0010"})
    assert [b["brief_id"] for b in kept] == [b["brief_id"] for b in full if b["brief_id"] not in {"O2-0003", "O2-0010"}]
    assert kept[0] == full[0]


# --- Balance and compatibility ------------------------------------------------------------------

@pytest.mark.parametrize("seed", range(8))
def test_marginals_are_within_one_count_of_target_in_small_jobs(seed):
    assert worst_marginal_gap(planner.plan("O1", seed), ood=False) < 1
    assert worst_marginal_gap(planner.plan("O1b", seed), ood=False) < 1


def test_marginals_are_within_one_count_of_target_in_the_main_run(o2):
    assert worst_marginal_gap(o2, ood=True) < 1


def test_marginals_hold_for_a_short_main_run_too():
    assert worst_marginal_gap(planner.plan("O2", 4, n=60), ood=True) < 1


def test_compatibility_table_holds_everywhere(o1, o2):
    for b in o1 + planner.plan("O1b", 1) + o2:
        assert planner.compatibility_errors(b) == [], b["brief_id"]


def test_specific_compatibility_rules(o2):
    sizes = {b["size_band"] for b in o2 if b["archetype"] == "critical_access_rural"}
    assert sizes == {"small"}
    assert {b["size_band"] for b in o2 if b["archetype"] == "academic_medical_center"} <= {"medium", "large"}
    assert {b["failure_focus"] for b in o2 if b["stratum"] == "normal"} == {"random_faults_only", "unit_batching"}
    assert not any(b["staffing"] == "chronic_short" and b["size_band"] == "small" for b in o2)


def test_compatibility_errors_names_each_broken_rule():
    cell = {"stratum": "normal", "archetype": "critical_access_rural", "size_band": "large", "transport_mix": "courier_dominant",
            "staffing": "chronic_short", "failure_focus": "tube_outage", "edge_tags": ["outage_during_peak"]}
    text = " ".join(planner.compatibility_errors(cell))
    for word in ("cannot be large", "cannot pair with courier_dominant", "does not occur", "normal week", "belong to the edge stratum"):
        assert word in text


def test_balanced_counts_round_to_floor_or_ceiling_and_sum_to_n():
    rng = np.random.default_rng(0)
    shares = {"a": 0.33, "b": 0.33, "c": 0.34}
    for n in (1, 7, 10, 101):
        counts = planner.balanced_counts(shares, n, rng)
        assert sum(counts.values()) == n
        assert all(abs(counts[k] - shares[k] * n) < 1 for k in shares)


def test_assign_meets_targets_by_moving_other_items():
    rng = np.random.default_rng(0)
    # items 0..2 may only take x; item 3 may take x or y; targets need item 3 to take y
    levels, bad = planner.assign([["x"], ["x"], ["x"], ["x", "y"]], {"x": 3, "y": 1}, rng)
    assert bad == 0 and levels.count("x") == 3 and levels[3] == "y"
    levels, bad = planner.assign([["x"], ["x"]], {"x": 1, "y": 1}, rng)
    assert bad == 1  # infeasible: reported, not hidden


# --- Out-of-distribution cells ----------------------------------------------------------------------

def test_ood_cells_exist_in_the_main_run_and_are_tagged(o2):
    ood = [b for b in o2 if b["ood"]]
    sites = [b for b in o2 if b["archetype"] == OOD_ARCHETYPE]
    scanner = [b for b in o2 if b["failure_focus"] == "scanner_downtime"]
    assert len(sites) / len(o2) == pytest.approx(0.05, abs=0.005)
    assert len(scanner) / len(o2) == pytest.approx(0.03, abs=0.005)
    assert {b["brief_id"] for b in ood} == {b["brief_id"] for b in sites + scanner}
    assert not set(b["brief_id"] for b in sites) & set(b["brief_id"] for b in scanner)
    assert {b["stratum"] for b in scanner} == {"disrupted", "edge"}
    assert not any(b["ood"] for b in o2 if b["archetype"] != OOD_ARCHETYPE and b["failure_focus"] != "scanner_downtime")


def test_scanner_briefs_ask_for_scanner_downtime_and_others_forbid_it(o2):
    for b in o2:
        predicates = {(c["kind"], c["predicate"], c["params"].get("type") or tuple(c["params"].get("types", ()))) for c in b["checks"]}
        if b["failure_focus"] == "scanner_downtime":
            assert ("include", "has_disruption", ("scanner_downtime",)) in predicates
            assert not any(k == "avoid" and p == "no_disruption_type" and t == "scanner_downtime" for k, p, t in predicates)
        else:
            assert ("avoid", "no_disruption_type", "scanner_downtime") in predicates


def test_edge_scanner_briefs_carry_the_scanner_backlog_tag(o2):
    edge = [b for b in o2 if b["failure_focus"] == "scanner_downtime" and b["stratum"] == "edge"]
    assert edge and all(b["edge_tags"] == ["scanner_backlog"] for b in edge)


@pytest.mark.parametrize("job", ["O1", "O1b"])
def test_pilot_jobs_never_contain_ood_cells(job):
    for seed in range(5):
        for b in planner.plan(job, seed):
            assert not b["ood"] and b["archetype"] != OOD_ARCHETYPE and b["failure_focus"] != "scanner_downtime"
            assert "scanner_backlog" not in b["edge_tags"]


# --- Briefs --------------------------------------------------------------------------------------------

def test_every_brief_has_its_fields(o1):
    keys = {"brief_id", "job", "stratum", "archetype", "size_band", "transport_mix", "staffing", "failure_focus",
            "edge_tags", "anchors", "quirk", "must_include", "must_avoid", "ood", "origin", "checks"}
    for b in o1:
        assert set(b) == keys
        assert b["job"] == "O1"


def test_edge_tags_are_one_to_three_in_the_edge_stratum_only(o2):
    for b in o2:
        if b["stratum"] == "edge":
            assert 1 <= len(b["edge_tags"]) <= 3 and len(set(b["edge_tags"])) == len(b["edge_tags"])
        else:
            assert b["edge_tags"] == []


def test_every_edge_tag_is_covered_in_the_main_run(o2):
    seen = collections.Counter(t for b in o2 for t in b["edge_tags"])
    tags = [t for t, rule in planner.config()["edge_tags"].items() if not rule.get("ood_only")]
    assert set(tags) <= set(seen)
    counts = [seen[t] for t in tags]
    assert max(counts) <= 2 * min(counts)


def test_forty_quirks_each_used_equally_often():
    assert len(planner.config()["quirks"]) == 40
    counts = collections.Counter(b["quirk"] for b in planner.plan("O2", 1, n=80))
    assert len(counts) == 40 and set(counts.values()) == {2}


def test_every_include_sentence_has_a_known_predicate(o1, o2):
    for b in o1 + o2[:300]:
        includes = [c for c in b["checks"] if c["kind"] == "include"]
        assert [c["sentence"] for c in includes] == b["must_include"]
        for c in b["checks"]:
            assert c["predicate"] in validate.PREDICATES and isinstance(c["params"], dict) and c["sentence"]
        assert all(s in b["must_avoid"] for s in (c["sentence"] for c in b["checks"] if c["kind"] == "avoid"))


def test_every_configured_predicate_is_implemented():
    cfg = planner.config()
    named = {r["predicate"] for r in list(cfg["transport_rules"].values()) + list(cfg["staffing_rules"].values())}
    named |= {r["predicate"] for rules in cfg["stratum_rules"].values() for r in rules}
    named |= {r["predicate"] for rules in cfg["focus_rules"].values() for r in rules}
    named |= {cfg["avoid_scanner"]["predicate"]} | {f"tag_{t}" for t in cfg["edge_tags"]}
    assert named <= set(validate.PREDICATES)


def test_anchors_sit_inside_their_ranges(o2):
    cfg = planner.config()
    for b in o2:
        a, lo_hi = b["anchors"], cfg["size_bands"]["specimens_per_weekday"][b["size_band"]]
        assert lo_hi[0] <= a["specimens_per_weekday"] <= lo_hi[1] and a["specimens_per_weekday"] % 5 == 0
        assert cfg["anchors"]["unit_count"][b["size_band"]][0] <= a["unit_count"] <= cfg["anchors"]["unit_count"][b["size_band"]][1]
        assert 0 <= a["disruption_day"] <= 6 and 0 <= a["disruption_hour"] <= 23
        assert a["courier_route_count"] >= a["offsite_van_count"]
        assert a["shift_pattern"] in cfg["anchors"]["shift_pattern_shares"]
        if b["failure_focus"] in cfg["needs_tube_focus"]:
            assert a["tube_line_count"] >= 1
        if b["transport_mix"] == "offsite_heavy":
            assert a["offsite_van_count"] >= 1


def test_focus_rules_are_anchored_only_outside_normal_and_edge(o1):
    for b in o1:
        anchored = [c for c in b["checks"] if "day" in c["params"] or "hour" in c["params"]]
        if b["stratum"] in ("normal", "edge"):
            assert anchored == []
        for c in anchored:
            assert c["params"].get("hour") == b["anchors"]["disruption_hour"]


def test_stratum_rules_by_stratum(o1):
    for b in o1:
        bounds = [c["params"] for c in b["checks"] if c["predicate"] == "disruption_count"]
        assert len(bounds) == 1
        if b["stratum"] == "normal":
            assert bounds[0] == {"max": 0}
        if b["stratum"] == "stressed":
            assert bounds[0]["max"] == 2


# --- Round 2 (O3) ---------------------------------------------------------------------------------------

TARGETS = [{"archetype": "cancer_center", "stratum": "disrupted", "count": 40},
           {"failure_focus": "surge", "count": 20},
           {"edge_tag": "misroute_storm", "count": 10},
           {"disruption_type": "courier_delayed", "size_band": "large", "count": 10}]


@pytest.fixture(scope="module")
def o3():
    return planner.plan("O3", 11, n=400, targets=TARGETS)


def test_o3_follows_the_round_two_shares(o3):
    origin = collections.Counter(b["origin"].split(":")[0] for b in o3)
    assert len(o3) == 400
    assert origin == {"slice": 240, "edge": 100, "coverage": 60}


def test_o3_slices_are_filled_in_proportion_to_their_counts(o3):
    counts = collections.Counter(b["origin"] for b in o3 if b["origin"].startswith("slice"))
    assert counts == {"slice:0": 120, "slice:1": 60, "slice:2": 30, "slice:3": 30}


def test_o3_slice_briefs_honor_their_pins(o3):
    for b in o3:
        if b["origin"] == "slice:0":
            assert (b["archetype"], b["stratum"]) == ("cancer_center", "disrupted")
        if b["origin"] == "slice:1":
            assert b["failure_focus"] == "surge"
        if b["origin"] == "slice:2":
            assert b["stratum"] == "edge" and "misroute_storm" in b["edge_tags"]
        if b["origin"] == "slice:3":
            assert b["failure_focus"] == "courier_unreliable" and b["size_band"] == "large"
        if b["origin"] == "edge":
            assert b["stratum"] == "edge"


def test_o3_has_no_ood_cells_and_respects_compatibility(o3):
    for b in o3:
        assert not b["ood"] and b["archetype"] != OOD_ARCHETYPE and b["failure_focus"] != "scanner_downtime"
        assert planner.compatibility_errors(b) == [], b["brief_id"]


def test_o3_is_deterministic_and_ids_are_unique(o3):
    assert o3 == planner.plan("O3", 11, n=400, targets=TARGETS)
    assert len({b["brief_id"] for b in o3}) == 400 and o3[0]["brief_id"] == "O3-0001"


def test_o3_under_covered_cells_are_used_when_given():
    cells = [{"archetype": "childrens_hospital", "stratum": "normal"}, {"archetype": "safety_net_urban", "stratum": "stressed"}]
    briefs = planner.plan("O3", 2, n=100, targets=[{"failure_focus": "surge", "count": 5}, {"under_covered": cells}])
    cover = [b for b in briefs if b["origin"] == "coverage"]
    assert len(cover) == 15
    assert {(b["archetype"], b["stratum"]) for b in cover} == {("childrens_hospital", "normal"), ("safety_net_urban", "stressed")}


def test_o3_needs_targets_and_other_jobs_refuse_them():
    with pytest.raises(ValueError):
        planner.plan("O3", 1)
    with pytest.raises(ValueError):
        planner.plan("O2", 1, n=10, targets=[{"archetype": "cancer_center"}])


@pytest.mark.parametrize("bad", [{"colour": "red"}, {"archetype": OOD_ARCHETYPE}, {"failure_focus": "scanner_downtime"},
                                 {"archetype": "critical_access_rural", "size_band": "large"},
                                 {"stratum": "normal", "failure_focus": "tube_outage"}])
def test_o3_refuses_slices_it_cannot_plan(bad):
    with pytest.raises(ValueError):
        planner.plan("O3", 1, n=20, targets=[bad])
