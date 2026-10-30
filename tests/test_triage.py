import copy

import numpy as np
import pytest

from src.generator import config
from src.pipeline import triage
from src.pipeline.evaluate import Spec


@pytest.fixture(scope="module")
def loaded():
    return triage.load()


@pytest.fixture
def cfg(loaded):
    return copy.deepcopy(loaded)


def spec(receipt, window=60, tier=3, route="tube", sid="SP-r24-000001", collected="2026-08-17T00:57:00", breach=None):
    breach = (receipt is None or receipt > window) if breach is None else breach
    return Spec(sid, 24, "g", ("g",), window, receipt, breach, "none", tier, route, collected, "tissue")


# ---- the committed settings ----

def test_the_committed_settings_are_valid_and_every_group_is_assumed(loaded):
    triage.validate(loaded)
    for name in triage.SECTIONS:
        assert loaded[name]["source"] == "assumed", name


def test_default_weights_are_three_two_one_with_the_two_checks(loaded):
    schemes = triage.weight_schemes(loaded)
    assert list(schemes) == ["default", "equal", "steep"]
    assert schemes["default"] == {1: 3, 2: 2, 3: 1}
    assert schemes["equal"] == {1: 1, 2: 1, 3: 1}
    assert schemes["steep"] == {1: 9, 2: 3, 3: 1}


def test_the_operating_point_is_six_alerts_per_shift(loaded):
    assert loaded["operating_point"]["budget_per_shift"] == 6


def test_a_tube_specimen_goes_to_the_receiving_technologist_and_a_carried_one_to_the_courier_coordinator(loaded):
    assert triage.role_for(loaded, "tube") == "receiving technologist"
    assert triage.role_for(loaded, "hand_carry") == "courier coordinator"
    assert triage.role_for(loaded, "tube", "in_tube") == "receiving technologist"
    assert triage.role_for(loaded, "hand_carry", "hand_carry") == "courier coordinator"


def test_a_specimen_the_filter_places_at_its_unit_goes_to_the_charge_nurse_whatever_its_route(loaded):
    assert loaded["routing"]["still_at_unit"] == "charge nurse"
    for route in triage.ROUTES:
        assert triage.role_for(loaded, route, "at_unit") == "charge nurse"


def test_the_processing_bench_is_not_a_role_under_the_receipt_clock(loaded):
    roles = [loaded["routing"][k] for k in (*triage.ROUTES, "still_at_unit")]
    assert not any("process" in role or "plat" in role for role in roles)


def test_an_unknown_route_is_refused(loaded):
    with pytest.raises(config.PolicyError, match="unknown route"):
        triage.role_for(loaded, "drone")


def test_every_policy_tier_has_a_weight(loaded):
    tiers = {p["urgency_tier"] for p in config.load()["policy"]["policies"].values()}
    assert tiers == set(triage.TIERS) == set(loaded["urgency_weights"]["default"])


# ---- what the loader refuses ----

@pytest.mark.parametrize("mutate, message", [
    (lambda c: c.pop("routing"), "no routing section"),
    (lambda c: c["urgency_weights"].pop("source"), "urgency_weights.*missing source"),
    (lambda c: c["operating_point"].update(source=" "), "operating_point.*missing source"),
    (lambda c: c["urgency_weights"]["default"].pop(2), "default must give a weight for tiers 1, 2 and 3"),
    (lambda c: c["urgency_weights"]["default"].update({4: 1}), "default must give a weight for tiers 1, 2 and 3"),
    (lambda c: c["urgency_weights"]["default"].update({3: 0}), "every weight must be positive"),
    (lambda c: c["urgency_weights"]["default"].update({3: "low"}), "every weight must be positive"),
    (lambda c: c["urgency_weights"]["default"].update({3: 5}), "less urgent tier cannot outweigh"),
    (lambda c: c["urgency_weights"]["checks"].pop("steep"), "needs the checks"),
    (lambda c: c["urgency_weights"].pop("checks"), "needs the checks"),
    (lambda c: c["operating_point"].update(budget_per_shift=0), "budget_per_shift must be positive"),
    (lambda c: c["routing"].update(tube=""), "routing tube must name a role"),
    (lambda c: c["routing"].pop("hand_carry"), "routing hand_carry must name a role"),
    (lambda c: c["routing"].pop("still_at_unit"), "routing still_at_unit must name a role"),
])
def test_the_loader_fails_loudly(cfg, mutate, message):
    mutate(cfg)
    with pytest.raises(config.PolicyError, match=message):
        triage.validate(cfg)


# ---- harm ----

def test_harm_is_the_tier_weight_times_the_risk():
    specs = [spec(None, tier=1, sid="a"), spec(None, tier=3, sid="b")]
    risk = {"a": np.array([0.1, 0.2]), "b": np.array([0.1, 0.2])}
    harm = triage.harm_series(specs, risk, {1: 3, 2: 2, 3: 1})
    assert harm["a"].tolist() == pytest.approx([0.3, 0.6]) and harm["b"].tolist() == [0.1, 0.2]


def test_equal_weights_leave_the_risk_unchanged():
    s = spec(None, tier=2, sid="a")
    risk = {"a": np.array([0.05, 0.4, 0.9])}
    assert triage.harm_series([s], risk, {1: 1, 2: 1, 3: 1})["a"].tolist() == risk["a"].tolist()


def test_a_lower_harm_threshold_never_moves_an_alert_later():
    from src.pipeline import evaluate
    s = spec(None, 120, tier=1)
    series = {s.specimen_id: np.linspace(0.01, 1.0, len(s.ticks))}
    harm = triage.harm_series([s], series, {1: 3, 2: 2, 3: 1})
    times = [evaluate.model_alerts([s], harm, theta)[s.specimen_id] for theta in np.linspace(0.05, 2.9, 30)]
    assert all(t is not None for t in times)
    assert times == sorted(times)  # a higher threshold only ever alerts later


# ---- the window-aware baseline ----

def test_the_window_baseline_alerts_at_the_first_tick_once_the_share_of_the_window_has_passed():
    s = spec(None, 120)
    assert triage.window_share_alerts([s], 0.5)[s.specimen_id] == 60.0   # on a tick
    assert triage.window_share_alerts([s], 0.51)[s.specimen_id] == 65.0  # between ticks
    assert triage.window_share_alerts([s], 1.0)[s.specimen_id] == 120.0


def test_the_window_baseline_scales_with_the_window_and_ignores_the_kind_of_specimen():
    short, long = spec(None, 60, sid="a"), spec(None, 1440, sid="b")
    out = triage.window_share_alerts([short, long], 0.25)
    assert out == {"a": 15.0, "b": 360.0}


def test_the_window_baseline_does_not_alert_a_specimen_received_first():
    s = spec(20.0, 120)
    assert triage.window_share_alerts([s], 0.5)[s.specimen_id] is None


# ---- the alert list ----

def rows_for(specs, alerts, cfg, place="in_tube"):
    risk = {s.specimen_id: np.full(len(s.ticks), 0.25) for s in specs}
    harm = {s.specimen_id: 2 * risk[s.specimen_id] for s in specs}
    places = {s.specimen_id: (place, 0.6) for s in specs}
    return triage.alert_rows(specs, alerts, risk, harm, places, cfg)


def test_there_is_one_row_per_alert_and_none_for_a_specimen_that_never_alerted(cfg):
    specs = [spec(80.0, sid="a"), spec(None, sid="b"), spec(30.0, sid="c")]
    rows = rows_for(specs, {"a": 40.0, "b": None, "c": 25.0}, cfg)
    assert sorted(r["specimen_id"] for r in rows) == ["a", "c"]


def test_a_row_carries_time_tier_role_and_warning(cfg):
    s = spec(80.0, 60, tier=1, route="hand_carry", collected="2026-08-17T00:57:30")
    (row,) = rows_for([s], {s.specimen_id: 40.0}, cfg)
    assert row["alert_at"] == "2026-08-17T01:37"                   # 40 minutes after the collection scan
    assert row["collected_at"] == "2026-08-17T00:57"
    assert row["tier"] == 1 and row["role"] == "courier coordinator" and row["route"] == "hand_carry"
    assert row["likely_place"] == "in_tube" and row["place_probability"] == 0.6
    assert row["minutes_since_collection"] == 40.0 and row["warning_min"] == 20.0 and row["window_min"] == 60
    assert row["risk"] == 0.25 and row["harm"] == 0.5
    assert row["outcome"] == "breach"
    assert list(row) == list(triage.ALERT_COLUMNS)


def test_an_alert_for_a_specimen_placed_at_its_unit_goes_to_the_charge_nurse(cfg):
    s = spec(None, 60, route="tube")
    (row,) = rows_for([s], {s.specimen_id: 20.0}, cfg, place="at_unit")
    assert row["role"] == "charge nurse" and row["likely_place"] == "at_unit" and row["route"] == "tube"


def test_the_alert_closes_at_the_receipt_scan(cfg):
    s = spec(52.5, 60, collected="2026-08-17T00:00:00")
    (row,) = rows_for([s], {s.specimen_id: 45.0}, cfg)
    assert row["closed_at"] == "2026-08-17T00:52"                  # a scan at 52 minutes 30 seconds
    assert row["outcome"] == "in time"


def test_a_specimen_with_no_receipt_scan_stays_open(cfg):
    s = spec(None, 60)
    (row,) = rows_for([s], {s.specimen_id: 45.0}, cfg)
    assert row["closed_at"] == "" and row["outcome"] == "breach"


def test_the_list_is_in_time_order(cfg):
    specs = [spec(None, sid="late", collected="2026-08-17T09:00:00"), spec(None, sid="early", collected="2026-08-17T01:00:00")]
    rows = rows_for(specs, {"late": 10.0, "early": 10.0}, cfg)
    assert [r["specimen_id"] for r in rows] == ["early", "late"]


def test_the_risk_and_harm_in_a_row_are_those_at_the_tick_the_alert_fired(cfg):
    s = spec(None, 60)
    risk = {s.specimen_id: np.linspace(0.1, 1.0, len(s.ticks))}
    harm = {s.specimen_id: 3 * risk[s.specimen_id]}
    (row,) = triage.alert_rows([s], {s.specimen_id: 15.0}, risk, harm, {s.specimen_id: ("in_tube", 0.5)}, cfg)
    assert row["risk"] == round(risk[s.specimen_id][2], 4) and row["harm"] == round(harm[s.specimen_id][2], 4)


# ---- the likely place at the alert ----

def test_the_likely_place_is_the_filters_best_guess_at_the_alert_tick():
    import json
    from src.models import location
    from src.pipeline import intervals
    params = json.loads(intervals.PARAMS_PATH.read_text(encoding="utf-8"))
    gen = config.load()["config"]
    group = "urine, unpreserved, room temp | 06-11 | routine"
    tube = Spec("t", 24, group, (group,), 60, None, True, "none", 3, "tube", "2026-08-17T00:00:00", "urine")
    hand = Spec("h", 24, group, (group,), 60, None, True, "none", 3, "hand_carry", "2026-08-17T00:00:00", "urine")
    quiet = Spec("q", 24, group, (group,), 60, None, True, "none", 3, "tube", "2026-08-17T00:00:00", "urine")
    out = triage.likely_places([tube, hand, quiet], {"t": 15.0, "h": 15.0, "q": None}, params, gen, 0.8)
    assert set(out) == {"t", "h"}
    names, post = location.filter_series(location.legs_for(params, gen, group, "tube", 0.8), 15)
    places = location.place_series(names, post)
    assert out["t"][0] == max(places, key=lambda p: places[p][15]) and out["t"][1] == pytest.approx(max(v[15] for v in places.values()))
    assert out["h"][0] in ("at_unit", "hand_carry") and 0 < out["h"][1] <= 1
