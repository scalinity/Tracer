import copy
import json

import pytest

from src.generator import validate as v1
from src.scenarios import planner, prompt, schema, validate

EXAMPLE = json.loads((schema.ROOT / "config" / "scenario.example.json").read_text(encoding="utf-8"))
CELL = {"stratum": "disrupted", "archetype": "community_hospital", "size_band": "medium", "transport_mix": "mixed",
        "staffing": "reduced_nights", "failure_focus": "tube_outage", "edge_tags": [], "sub_seed": 1}


def brief_for(**changes) -> dict:
    """A planner brief for a cell near the worked example, anchored to its outage (Tuesday, about 09:00)."""
    cell = CELL | changes
    brief = planner.make_brief(cell) | {"brief_id": "T-0001"}
    brief["anchors"] = brief["anchors"] | {"disruption_day": 1, "disruption_hour": 9}
    brief["checks"] = planner.checks_for(cell, brief["anchors"])
    brief["must_include"] = [c["sentence"] for c in brief["checks"] if c["kind"] == "include"]
    return brief


def example() -> dict:
    return copy.deepcopy(EXAMPLE)


def add(s: dict, kind: str, target: str, *, day: int = 1, start: str = "10:00", duration: int = 60, severity: float = 0.5,
        response: str = "none", noticed: int = 0, recurs: bool = False) -> dict:
    n = len(s["disruptions"]) + 1
    s["disruptions"].append({"disruption_id": f"x{n}", "type": kind, "target_id": target, "day": day, "start": start,
                             "duration_min": duration, "severity": severity, "response": response,
                             "noticed_after_min": noticed, "recurs_daily": recurs})
    return s


def neutral() -> dict:
    """The example with nothing that satisfies an edge tag: no disruptions, roomy couriers, small rates."""
    s = example()
    s["disruptions"] = []
    s["units"][4]["dispatch"]["batch_interval_min"] = 60
    for route in s["couriers"]["routes"]:
        route["capacity_per_round"] = 200
    return s


def messages(result) -> str:
    return " | ".join(result.errors)


# --- The worked example passes every layer ----------------------------------------------------------------

def test_the_worked_example_passes_every_layer_against_its_brief():
    stub_calls = []
    result = validate.validate(example(), brief_for(), plausibility=lambda s, st: stub_calls.append(st) or [])
    assert result.ok and result.layer is None and result.errors == [] and result.repairable
    assert stub_calls == ["disrupted"]


def test_result_reports_layer_number_name_and_text():
    result = validate.validate({"archetype": "x"}, brief_for())
    assert not result.ok and result.layer == "1 schema" and result.number == 1
    assert result.text().startswith("- ")


# --- Layer 2: semantics ------------------------------------------------------------------------------------

def mutate(path, value):
    def apply(s):
        node = s
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return apply


SEMANTIC_CASES = {
    "duplicate unit id": (mutate(("units", 1, "unit_id"), "ed_main"), "used more than once"),
    "id shared across kinds": (mutate(("disruptions", 0, "disruption_id"), "ed_main"), "shared by different kinds"),
    "reserved id": (mutate(("units", 5, "unit_id"), "receiving"), "reserved"),
    "tube unit with an unknown line": (mutate(("units", 0, "tube_line_id"), "line_zz"), "not a line"),
    "tube unit without a line": (mutate(("units", 0, "tube_line_id"), None), "tube_line_id"),
    "courier unit with an unknown route": (mutate(("units", 3, "courier_route_id"), "nope"), "not a route"),
    "mixed unit without a tube share": (mutate(("units", 2, "tube_share_if_mixed"), None), "tube_share_if_mixed"),
    "mixed unit with an unknown route": (mutate(("units", 2, "courier_route_id"), "nope"), "not a route"),
    "offsite clinic not on courier": (mutate(("units", 4, "route"), "walk_in"), "off-site clinic"),
    "offsite clinic on a loop": (mutate(("units", 4, "courier_route_id"), "campus_loop"), "off-site clinic"),
    "batched unit without an interval": (mutate(("units", 4, "dispatch", "batch_interval_min"), None), "batch_interval_min"),
    "dispatch p90 not above median": (mutate(("units", 0, "dispatch", "p90_min"), 5), "p90_min must be greater"),
    "line p90 not above median": (mutate(("tube_system", "lines", 0, "transit_p90_min"), 4), "transit_p90_min must be greater"),
    "stall p90 not above median": (mutate(("tube_system", "stall_extra_min", "p90"), 45), "stall_extra_min"),
    "scan p90 not above median": (mutate(("receiving", "scan_service_min", "p90"), 1.2), "scan_service_min"),
    "route p90 not above median": (mutate(("couriers", "routes", 0, "travel_p90_min"), 10), "travel_p90_min must be greater"),
    "route with no service": (mutate(("couriers", "routes", 1, "round_interval_min"), {"day": None, "evening": None, "night": None}),
                              "no service on any shift"),
    "unstaffed weekday night": (mutate(("receiving", "staff_by_shift", "weekday", "night"), 0), "every shift needs at least 1"),
    "unstaffed weekend day": (mutate(("receiving", "staff_by_shift", "weekend", "day"), 0), "weekend.day"),
    "on-call runner without a response time": (mutate(("couriers", "on_call_response_median_min"), None), "on_call_response_median_min"),
    "flat hourly profile": (mutate(("volume", "hourly_profile"), [3.0] * 24), "flat"),
    "empty hourly profile": (mutate(("volume", "hourly_profile"), [0.0] * 24), "positive sum"),
    "empty specimen mix": (lambda s: s.__setitem__("specimen_mix", dict.fromkeys(s["specimen_mix"], 0)), "specimen_mix must have"),
    "outage on a missing line": (mutate(("disruptions", 0, "target_id"), "line_zz"), "must be a tube line id"),
    "station window on the wrong target": (mutate(("disruptions", 2, "target_id"), "receiving"), '"lab_station"'),
    "short-staffing on a unit": (mutate(("disruptions", 1, "target_id"), "ed_main"), '"receiving"'),
    "missed rounds on a line": (lambda s: add(s, "courier_missed_rounds", "line_a"), "courier route id"),
    "road closure on a loop": (lambda s: add(s, "offsite_route_closed", "campus_loop"), "offsite_van route id"),
    "surge on a line": (lambda s: add(s, "unit_surge", "line_a"), "a unit id"),
    "instrument down on a missing instrument": (lambda s: add(s, "instrument_down", "ghost"), "instrument name"),
    "outage without a response": (mutate(("disruptions", 0, "response"), "none"), "needs a response"),
    "degradation with a response": (lambda s: add(s, "tube_line_degraded", "line_a", response="hold_at_unit"), "must have response 'none'"),
    "scanner downtime without a scanner brief": (lambda s: add(s, "scanner_downtime", "receiving"), "allowed only when the brief asks"),
    "instrument down without instruments": (lambda s: (s["processing"].__setitem__("instruments", []),
                                                       add(s, "instrument_down", "maldi_tof")), "at least one entry in processing.instruments"),
    "archetype differs from the brief": (mutate(("archetype",), "cancer_center"), "archetype is"),
    "size band differs": (mutate(("size_band",), "large"), "size_band is"),
    "transport mix differs": (mutate(("transport_mix",), "tube_dominant"), "transport_mix is"),
    "staffing differs": (mutate(("staffing",), "full_24_7"), "staffing is"),
    "failure focus differs": (mutate(("failure_focus",), "surge"), "failure_focus is"),
    "volume below the band": (mutate(("volume", "specimens_per_weekday"), 100), "outside the medium band"),
    "volume above the band": (mutate(("volume", "specimens_per_weekday"), 360), "outside the medium band"),
    "edge tags on a non-edge brief": (mutate(("edge_tags",), ["compound_failure"]), "edge_tags must be empty"),
    "duplicate edge tag": (mutate(("edge_tags",), ["compound_failure", "compound_failure"]), "more than once"),
}


@pytest.mark.parametrize("name", list(SEMANTIC_CASES))
def test_each_semantic_rule_rejects_its_violation(name):
    mutator, expected = SEMANTIC_CASES[name]
    s = example()
    mutator(s)
    result = validate.validate(s, brief_for())
    assert not result.ok and result.layer == "2 semantics", (name, result)
    assert expected in messages(result), (name, result.errors)


def test_semantic_rules_pass_on_valid_variants():
    cases = []
    s = example()
    s["disruptions"][2]["recurs_daily"] = False
    cases.append(s)
    s = example()
    s["receiving"]["shift_pattern"] = "7-19"
    s["receiving"]["staff_by_shift"]["weekday"]["evening"] = 0   # unused under 7-19
    s["receiving"]["staff_by_shift"]["weekend"]["evening"] = 0
    cases.append(s)
    s = example()
    s["couriers"]["on_call_runner"], s["couriers"]["on_call_response_median_min"] = False, None
    cases.append(s)
    s = example()
    s["units"][2]["courier_route_id"] = None   # a mixed unit may omit its courier route: the rest walks in
    cases.append(s)
    for s in cases:
        assert validate.semantic_errors(s, brief_for()) == []


def test_flat_profile_is_allowed_when_the_brief_asks():
    s = example()
    s["volume"]["hourly_profile"] = [3.0] * 24
    brief = brief_for()
    assert any("flat" in e for e in validate.semantic_errors(s, brief))
    brief["anchors"]["flat_profile"] = True
    assert validate.semantic_errors(s, brief) == []


def test_scanner_downtime_is_allowed_only_in_an_ood_scanner_brief():
    s = add(example(), "scanner_downtime", "receiving", duration=90)
    assert any("allowed only when the brief asks" in e for e in validate.semantic_errors(s, brief_for()))
    scanner = brief_for(failure_focus="scanner_downtime")
    scanner["ood"] = True
    assert not any("allowed only when the brief asks" in e for e in validate.semantic_errors(s, scanner))
    scanner["ood"] = False   # a scanner focus that is not tagged out-of-distribution is still refused
    assert any("allowed only when the brief asks" in e for e in validate.semantic_errors(s, scanner))


def test_a_disruption_may_not_run_past_day_six_plus_twelve_hours():
    s = example()
    s["disruptions"][0].update(day=6, start="23:00", duration_min=800)   # beyond the schema's 720, so semantics alone catch it
    assert any("ends after day 6 plus 12 hours" in e for e in validate.semantic_errors(s, None))
    s["disruptions"][0].update(day=6, start="23:00", duration_min=720)
    assert not any("ends after" in e for e in validate.semantic_errors(s, None))


def test_recurring_disruptions_are_checked_on_every_repeat():
    d = {"day": 0, "start": "23:00", "duration_min": 720, "recurs_daily": True}
    assert len(validate.occurrences(d)) == 7 and max(e for _, e in validate.occurrences(d)) == 6 * 1440 + 1380 + 720


# --- Layer 3: names and safety --------------------------------------------------------------------------------

def with_text(narrative=None, realism_notes=None):
    s = example()
    if narrative is not None:
        s["narrative"] = narrative
    if realism_notes is not None:
        s["realism_notes"] = realism_notes
    return s


BAD_TEXT = {
    "shift pattern written as a date": "The night staff follow the 7-15-23 pattern in this mid-sized laboratory every single week.",
    "slash date": "A mid-sized laboratory that changed its receiving desk on 03/04/1980 and has run smoothly since then.",
    "date of birth word": "A mid-sized laboratory where the date of birth of the patient is printed on every label there.",
    "titled name": "A mid-sized laboratory where Dr. Smith decides when the tube line is restarted after each outage.",
    "role and name": "A mid-sized laboratory where the technologist Patel scans every tube carrier that arrives overnight.",
    "first name": "A mid-sized laboratory where Maria scans every tube carrier that arrives during the overnight shift.",
    "hospital name": "A mid-sized laboratory serving Riverside Hospital and two outpatient sites with a shared courier loop.",
    "medical center name": "A mid-sized laboratory serving Lakeview Medical Center and two outpatient sites with a shared van.",
    "clinic name": "A mid-sized laboratory fed by the Oakwood Clinic and two outpatient sites by a shared courier loop.",
    "health name": "A mid-sized laboratory fed by the Bayview Health sites and two outpatient clinics by a shared van.",
    "university name": "A mid-sized laboratory belonging to Westfield University and two outpatient sites with a shared van.",
    "health system name": "A mid-sized laboratory belonging to the Prairie Health System and two outpatient sites with a van.",
}


@pytest.mark.parametrize("name", list(BAD_TEXT))
@pytest.mark.parametrize("field", ["narrative", "realism_notes"])
def test_names_layer_rejects_each_pattern_in_either_text_field(name, field):
    s = with_text(**{field: BAD_TEXT[name]})
    result = validate.validate(s, brief_for())
    assert not result.ok and result.layer == "3 names and safety", (name, result.errors)
    assert field in messages(result)


GOOD_TEXT = [
    "A mid-sized community hospital with a busy emergency department and one aging tube line, nothing else unusual.",
    "Nights run on one receiving technologist. On Tuesday morning the shared tube line fails during the peak.",
    "The Clinic sends specimens by van twice a day and the receiving desk is quiet on weekends and holidays.",
    "An outpatient clinic network and a hospital share one courier loop that runs every half hour during the day.",
]


@pytest.mark.parametrize("text", GOOD_TEXT)
def test_names_layer_accepts_generic_descriptions(text):
    assert validate.name_errors(with_text(narrative=text)) == []


def test_the_denylist_has_the_required_names():
    terms = set(planner.config()["names"]["denylist"])
    assert {"Mayo", "Cleveland Clinic", "Johns Hopkins", "Kaiser", "HCA", "Ascension", "Baptist", "Shands", "UF Health"} <= terms


@pytest.mark.parametrize("term", planner.config()["names"]["denylist"])
def test_names_layer_rejects_every_denylisted_name(term):
    s = with_text(narrative=f"A mid-sized laboratory two blocks from the {term} campus with one old tube line.")
    found = validate.name_errors(s)
    assert any(term in e for e in found), (term, found)


def test_names_layer_checks_ids_without_regard_to_case_or_underscores():
    s = example()
    s["units"][5]["unit_id"] = "mayo_lab"
    assert any("units[5].unit_id" in e and "Mayo" in e for e in validate.name_errors(s))
    s = example()
    s["units"][5]["unit_id"] = "cleveland_clinic_ed"
    assert any("Cleveland Clinic" in e for e in validate.name_errors(s))


def test_the_prompt_names_no_denylisted_institution():
    text = prompt.developer_text()
    assert [t for t in planner.config()["names"]["denylist"] if t in text] == []


def test_names_layer_reuses_the_v1_patterns():
    assert validate.v1.DOB_PATTERNS is v1.DOB_PATTERNS and validate.v1.FIRST_NAMES is v1.FIRST_NAMES


# --- Layer 5: the predicates -----------------------------------------------------------------------------------

P = validate.PREDICATES


def test_has_disruption_checks_type_duration_severity_and_time():
    s = example()
    base = {"types": ["tube_line_outage"], "min_duration_min": 45, "max_duration_min": 120}
    assert P["has_disruption"](s, base) is None
    assert "no courier" in P["has_disruption"](s, {"types": ["courier_missed_rounds"]})
    assert P["has_disruption"](s, base | {"max_duration_min": 60})
    assert P["has_disruption"](s, base | {"min_duration_min": 80})
    assert P["has_disruption"](s, base | {"min_severity": 0.7})
    assert P["has_disruption"](s, base | {"min_severity": 0.6}) is None
    near = base | {"day": 1, "hour": 9, "hour_tolerance_h": 3}
    assert P["has_disruption"](s, near) is None
    assert P["has_disruption"](s, near | {"day": 2})
    assert P["has_disruption"](s, near | {"hour": 14})
    assert P["has_disruption"](s, {"types": ["station_unattended"], "hour": 3, "hour_tolerance_h": 3}) is None   # recurring: day ignored
    assert P["has_disruption"](s, {"types": ["station_unattended"], "day": 4, "hour": 3, "hour_tolerance_h": 3}) is None
    assert P["has_disruption"](s, {"types": ["station_unattended"], "hour": 12})


def test_disruption_count_and_no_disruption_type():
    s = example()
    assert P["disruption_count"](s, {"min": 2, "max": 8}) is None
    assert P["disruption_count"](s, {"max": 2}) == "3 disruptions"
    assert P["disruption_count"](s, {"min": 4})
    assert P["disruption_count"](neutral(), {"max": 0}) is None
    assert P["no_disruption_type"](s, {"type": "scanner_downtime"}) is None
    assert "tube_line_outage" in P["no_disruption_type"](s, {"type": "tube_line_outage"})


def test_transport_predicates():
    s = example()
    assert validate.route_shares(s) == pytest.approx({"tube": 0.608, "courier": 0.292, "walk_in": 0.1, "offsite": 0.12})
    assert P["transport_share"](s, {"kind": "tube", "min": 0.6}) is None
    assert P["transport_share"](s, {"kind": "tube", "min": 0.7})
    assert P["transport_share"](s, {"kind": "courier", "min": 0.6})
    assert P["transport_share"](s, {"kind": "offsite", "min": 0.1}) is None
    assert P["transport_share"](s, {"kind": "offsite", "min": 0.3})
    mixed = {"tube_min": 0.25, "tube_max": 0.69, "courier_min": 0.15}
    assert P["transport_mixed"](s, mixed) is None
    assert P["transport_mixed"](s, mixed | {"tube_max": 0.6})
    assert P["transport_mixed"](s, mixed | {"courier_min": 0.3})
    assert validate.classify_transport(validate.route_shares(s)) == "mixed"


def test_a_mixed_unit_without_a_courier_route_walks_in():
    s = example()
    s["units"][2]["courier_route_id"] = None
    shares = validate.route_shares(s)
    assert shares["walk_in"] == pytest.approx(0.1 + 0.24 * 0.3) and shares["courier"] == pytest.approx(0.292 - 0.072)


def test_staffing_predicates():
    r = lambda s, pattern, **extra: P["staffing_pattern"](s, {"pattern": pattern} | extra)  # noqa: E731
    s = example()
    assert r(s, "reduced_nights") is None and r(s, "full_24_7") and r(s, "weekend_skeleton") and r(s, "chronic_short", min_peak_per_staff=12)
    full = example()
    full["receiving"]["staff_by_shift"]["weekday"]["night"] = 3
    full["receiving"]["staff_by_shift"]["weekend"] = {"day": 4, "evening": 3, "night": 3}
    assert r(full, "full_24_7") is None and r(full, "reduced_nights")
    skeleton = example()
    skeleton["receiving"]["staff_by_shift"]["weekend"] = {"day": 1, "evening": 1, "night": 1}
    assert r(skeleton, "weekend_skeleton") is None
    short = example()
    short["receiving"]["staff_by_shift"]["weekday"]["day"] = 1
    assert r(short, "chronic_short", min_peak_per_staff=12) is None
    assert validate.classify_staffing(short) == "chronic_short" and validate.classify_staffing(s) == "reduced_nights"
    # under 7-19 the unused evening value does not count toward totals
    s = example()
    s["receiving"]["shift_pattern"] = "7-19"
    assert validate.staff_sums(s) == (5, 4)


def test_batching_and_instrument_predicates():
    s = example()
    assert P["batching_present"](s, {"min_interval_min": 60}) is None
    assert P["batching_present"](s, {"min_interval_min": 180}) is not None
    assert P["batching_present"](neutral() | {"units": [u | {"dispatch": u["dispatch"] | {"mode": "immediate"}} for u in neutral()["units"]]},
                                 {"min_interval_min": 60})
    hold = add(neutral(), "unit_batching_hold", "ed_main")
    hold["units"][4]["dispatch"]["mode"] = "immediate"
    assert P["batching_present"](hold, {"min_interval_min": 60}) is None
    params = {"min_pull": 0.15, "min_duration_min": 60}
    assert P["instrument_coupling"](neutral(), params)
    ok = add(neutral(), "instrument_down", "maldi_tof", duration=90)
    assert P["instrument_coupling"](ok, params) is None
    ok["processing"]["staff_pull_from_receiving"] = 0.1
    assert P["instrument_coupling"](ok, params)


# Each tag: how to make a scenario satisfy it, and how to miss it by a hair.
def _outage(s, **kw):
    return add(s, "tube_line_outage", "line_a", response="hold_at_unit", **kw)


def _crunch(s, capacity=5):
    s["couriers"]["routes"][0].update(capacity_per_round=capacity, round_interval_min={"day": 240, "evening": 240, "night": 240})
    return s


def _icu(s, stat):
    s["units"][1]["stat_fraction"] = stat
    return s


def _storm(s, rate, degraded=True):
    s["tube_system"]["lines"][0]["misroute_rate"] = rate
    return add(s, "tube_line_degraded", "line_a") if degraded else s


def _batch_hold(s, duration):
    s["units"][4]["dispatch"]["batch_interval_min"] = 60
    return add(s, "unit_batching_hold", "ed_main", start="23:00", duration=duration)


def _compound(s, second, overlap):
    """A one-hour outage from 10:00 and a second disruption that overlaps its end by `overlap` minutes."""
    s = add(s, "tube_line_outage", "line_a", start="10:00", duration=60, response="hold_at_unit")
    start = 10 * 60 + 60 - overlap
    return add(s, second, {"receiving_short_staffed": "receiving"}.get(second, "line_a"),
               start=f"{start // 60:02d}:{start % 60:02d}", duration=60)


TAG_CASES = {
    "outage_at_shift_change": (lambda s: _outage(s, start="07:10"), lambda s: _outage(s, start="07:45")),
    "outage_during_peak": (lambda s: _outage(s, start="08:30", duration=60), lambda s: _outage(s, start="08:30", duration=40)),
    "unnoticed_outage": (lambda s: _outage(s, noticed=90), lambda s: _outage(s, noticed=59)),
    "mass_casualty_surge": (lambda s: add(s, "unit_surge", "ed_main", severity=0.8, duration=150),
                            lambda s: add(s, "unit_surge", "ed_main", severity=0.69, duration=150)),
    "weekend_courier_no_show": (lambda s: add(s, "courier_missed_rounds", "campus_loop", day=5, severity=0.9),
                                lambda s: add(s, "courier_missed_rounds", "campus_loop", day=4, severity=0.9)),
    "slow_degradation": (lambda s: add(s, "tube_line_degraded", "line_a", duration=400, severity=0.5),
                         lambda s: add(s, "tube_line_degraded", "line_a", duration=359, severity=0.5)),
    "chronic_non_dispatch_unit": (lambda s: s["units"][0].update(not_dispatched_rate=0.03) or s,
                                  lambda s: s["units"][0].update(not_dispatched_rate=0.019) or s),
    "misroute_storm": (lambda s: _storm(s, 0.025), lambda s: _storm(s, 0.025, degraded=False)),
    "night_station_neglect": (lambda s: add(s, "station_unattended", "lab_station", start="02:00", duration=60, recurs=True),
                              lambda s: add(s, "station_unattended", "lab_station", start="05:01", duration=60, recurs=True)),
    "holiday_skeleton": (lambda s: add(s, "receiving_short_staffed", "receiving", day=2, severity=0.5, duration=500),
                         lambda s: add(s, "receiving_short_staffed", "receiving", day=5, severity=0.5, duration=500)),
    "offsite_road_closure": (lambda s: add(s, "offsite_route_closed", "van_west", duration=200),
                             lambda s: add(s, "offsite_route_closed", "van_west", duration=179)),
    "instrument_staff_drain": (lambda s: s["processing"].update(staff_pull_from_receiving=0.35) or add(s, "instrument_down", "maldi_tof", duration=150),
                               lambda s: s["processing"].update(staff_pull_from_receiving=0.29) or add(s, "instrument_down", "maldi_tof", duration=150)),
    "batching_night_unit": (lambda s: s["units"][4]["dispatch"].update(batch_interval_min=120) or s,
                            lambda s: s["units"][4]["dispatch"].update(batch_interval_min=119) or s),
    "compound_failure": (lambda s: _compound(s, "receiving_short_staffed", 40),
                         lambda s: _compound(s, "receiving_short_staffed", 20)),
    "courier_capacity_crunch": (lambda s: _crunch(s, 5), lambda s: _crunch(s, 200)),
    "stat_heavy_icu_night": (lambda s: add(_icu(s, 0.7), "receiving_short_staffed", "receiving", start="02:00"),
                             lambda s: add(_icu(s, 0.59), "receiving_short_staffed", "receiving", start="02:00")),
    "misroute_long_search": (lambda s: s["tube_system"]["misroute_found_min"].update(median=70) or s,
                             lambda s: s["tube_system"]["misroute_found_min"].update(median=59) or s),
    "scanner_backlog": (lambda s: add(s, "scanner_downtime", "receiving", duration=90),
                        lambda s: add(s, "scanner_downtime", "receiving", duration=59)),
}


def test_every_edge_tag_has_a_predicate_and_cases():
    tags = set(schema.full_schema()["properties"]["edge_tags"]["items"]["enum"])
    assert tags == set(planner.config()["edge_tags"]) == set(TAG_CASES)
    assert {f"tag_{t}" for t in tags} <= set(P)


@pytest.mark.parametrize("tag", list(TAG_CASES))
def test_edge_tag_predicate_passes_when_satisfied_and_fails_on_a_near_miss(tag):
    satisfy, near_miss = TAG_CASES[tag]
    assert P[f"tag_{tag}"](neutral(), {}), f"{tag} should fail on a scenario that does nothing"
    assert P[f"tag_{tag}"](satisfy(neutral()), {}) is None
    assert P[f"tag_{tag}"](near_miss(neutral()), {}), f"{tag} should fail on a near miss"


def test_batching_night_unit_also_accepts_a_four_hour_night_hold():
    assert P["tag_batching_night_unit"](_batch_hold(neutral(), 300), {}) is None
    assert P["tag_batching_night_unit"](_batch_hold(neutral(), 200), {})   # 200 night minutes is under 4 hours


def test_outage_during_peak_uses_the_three_busiest_hours_of_the_profile():
    assert validate.peak_hours(EXAMPLE["volume"]["hourly_profile"], 3) == [8, 9, 10]
    s = neutral()
    s["volume"]["hourly_profile"] = [1.0] * 24
    s["volume"]["hourly_profile"][20] = 9.0   # an evening peak moves the busy hours
    assert P["tag_outage_during_peak"](_outage(s, start="08:30"), {})
    assert P["tag_outage_during_peak"](_outage(neutral() | {"volume": s["volume"]}, start="20:10"), {}) is None


def test_courier_peak_load_is_computed_from_volume_profile_and_interval():
    s = example()
    route = s["couriers"]["routes"][0]
    served = 0.24 * 0.3 + 0.10   # med_surg_3's courier share plus periop
    total = sum(s["volume"]["hourly_profile"])
    # Hour 8 is the busiest hour (day interval 30), but hour 6 falls in the night shift (interval 90) and waits longest.
    by_hour = {8: 220 * served * 5.2 / total * 30 / 60, 6: 220 * served * 3.0 / total * 90 / 60}
    assert validate.courier_peak_load(s, route) == pytest.approx(max(by_hour.values())) and max(by_hour, key=by_hour.get) == 6


def test_adherence_errors_name_the_unmet_sentence_and_the_reason():
    s = example()
    s["disruptions"][0]["duration_min"] = 200
    found = validate.adherence_errors(s, brief_for())
    assert len(found) == 1 and found[0].startswith("Required by the brief: at least one tube line outage")
    assert "Found:" in found[0]


def test_avoid_rules_are_enforced():
    s = add(example(), "tube_line_outage", "line_a", response="hold_at_unit")
    brief = brief_for(stratum="stressed", failure_focus="surge")
    found = validate.adherence_errors(s, brief)
    assert any(e.startswith("Not allowed by the brief: no tube line outage") for e in found)


def test_a_claimed_edge_tag_must_be_real():
    s = example()
    s["edge_tags"] = ["unnoticed_outage"]
    brief = brief_for(stratum="edge", edge_tags=[])
    assert any("edge_tags lists 'unnoticed_outage'" in e for e in validate.adherence_errors(s, brief))


def test_the_brief_edge_tags_are_checked_through_their_rules():
    edge = brief_for(stratum="edge", failure_focus="compound", edge_tags=["unnoticed_outage"])
    s = example()
    s["edge_tags"] = ["unnoticed_outage"]
    assert any("unnoticed_outage" in e for e in validate.adherence_errors(s, edge))
    s["disruptions"][0]["noticed_after_min"] = 80
    assert not any("unnoticed_outage" in e for e in validate.adherence_errors(s, edge))


# --- Layer order and the injected hooks --------------------------------------------------------------------------

def test_the_first_failing_layer_is_reported():
    brief = brief_for()
    # Fails layers 1, 2, 3, 5 at once: the schema is first.
    s = example()
    s["volume"]["weekend_factor"] = 5
    s["units"][1]["unit_id"] = "ed_main"
    s["narrative"] = BAD_TEXT["hospital name"]
    s["disruptions"][0]["duration_min"] = 200
    assert validate.validate(s, brief).layer == "1 schema"
    s["volume"]["weekend_factor"] = 0.75
    assert validate.validate(s, brief).layer == "2 semantics"
    s["units"][1]["unit_id"] = "icu_2"
    assert validate.validate(s, brief).layer == "3 names and safety"
    s["narrative"] = EXAMPLE["narrative"]
    assert validate.validate(s, brief, plausibility=lambda sc, st: ["too slow"]).layer == "4 plausibility"
    assert validate.validate(s, brief).layer == "5 brief adherence"


def test_a_layer_reports_all_its_own_errors():
    s = example()
    s["units"][1]["unit_id"] = "ed_main"
    s["receiving"]["staff_by_shift"]["weekday"]["night"] = 0
    result = validate.validate(s, brief_for())
    assert result.layer == "2 semantics" and len(result.errors) >= 2


def test_plausibility_layer_calls_the_injected_callable_with_scenario_and_stratum():
    calls = []

    def plausibility(scenario, stratum):
        calls.append((scenario, stratum))
        return ["median tube transit is 140 minutes, above the 90-minute limit"]

    result = validate.validate(example(), brief_for(), plausibility=plausibility)
    assert not result.ok and result.layer == "4 plausibility" and result.repairable
    assert result.errors == ["median tube transit is 140 minutes, above the 90-minute limit"]
    assert len(calls) == 1 and calls[0][0] == EXAMPLE and calls[0][1] == "disrupted"


def test_plausibility_is_skipped_when_none_and_when_an_earlier_layer_fails():
    assert validate.validate(example(), brief_for(), plausibility=None).ok
    spy = []
    bad = example()
    bad["units"][1]["unit_id"] = "ed_main"
    validate.validate(bad, brief_for(), plausibility=lambda s, st: spy.append(1) or [])
    assert spy == []


class FakeLibrary:
    def __init__(self, message):
        self.message, self.seen = message, []

    def duplicate_of(self, scenario, brief):
        self.seen.append(brief["brief_id"])
        return self.message


def test_diversity_layer_rejects_a_near_duplicate_without_a_repair():
    library = FakeLibrary("near duplicate of accepted scenario O2-0001")
    result = validate.validate(example(), brief_for(), library=library)
    assert not result.ok and result.layer == "6 diversity" and result.repairable is False
    assert result.errors == ["near duplicate of accepted scenario O2-0001"]
    assert library.seen == [brief_for()["brief_id"]]


def test_diversity_layer_passes_a_new_scenario_and_runs_last():
    assert validate.validate(example(), brief_for(), library=FakeLibrary(None)).ok
    library = FakeLibrary("duplicate")
    bad = example()
    bad["disruptions"][0]["duration_min"] = 200
    assert validate.validate(bad, brief_for(), library=library).layer == "5 brief adherence" and library.seen == []


# --- normalize -----------------------------------------------------------------------------------------------------

def test_normalize_scales_shares_mix_and_profile_to_sum_to_one_without_changing_the_input():
    original = example()
    out = validate.normalize(original)
    assert original == EXAMPLE
    assert sum(u["volume_share"] for u in out["units"]) == pytest.approx(1)
    assert sum(out["specimen_mix"].values()) == pytest.approx(1)
    assert sum(out["volume"]["hourly_profile"]) == pytest.approx(1)
    raw, new = EXAMPLE["units"], out["units"]
    assert new[0]["volume_share"] / new[1]["volume_share"] == pytest.approx(raw[0]["volume_share"] / raw[1]["volume_share"])
    assert out["units"][0]["dispatch"] == EXAMPLE["units"][0]["dispatch"]


def test_normalize_leaves_a_valid_scenario_valid():
    assert schema.errors(validate.normalize(example())) == []
