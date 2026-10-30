import json
import math

import numpy as np
import pytest

from src.generator import config
from src.models import risk
from src.pipeline import evaluate, intervals, store
from src.pipeline.evaluate import Spec


def spec(receipt, window=60, breach=None, run=24, fault="none", sid="SP-r24-000001", group="g"):
    breach = (receipt is None or receipt > window) if breach is None else breach
    return Spec(sid, run, group, (group,), window, receipt, breach, fault)


# ---- which ticks a specimen is scored at ----

@pytest.mark.parametrize("receipt, window, last", [
    (22.0, 60, 20),     # the receipt scan at minute 22 ends the silence before the 25-minute tick
    (25.0, 60, 20),     # a scan at the tick itself counts as received
    (25.5, 60, 25),
    (None, 60, 60),     # never arrives: scored until the window closes
    (90.0, 60, 60),     # arrives after the window closed: scored until it closed
])
def test_a_specimen_is_scored_at_every_tick_while_it_is_silent_and_the_window_is_open(receipt, window, last):
    ticks = spec(receipt, window).ticks
    assert list(ticks) == list(range(5, last + 1, 5))


def test_a_specimen_received_within_five_minutes_is_never_scored():
    assert len(spec(3.0).ticks) == 0


# ---- what counts as caught, and the warning ----

def alerts_at(specs, minutes):
    return {s.specimen_id: m for s, m in zip(specs, minutes)}


def make(*receipts, window=60):
    return [spec(r, window, sid=f"SP-r24-{i:06d}") for i, r in enumerate(receipts, 1)]


def test_warning_is_the_minutes_from_the_alert_to_the_window_closing():
    specs = make(75.0, None)  # both breach a 60-minute window
    sc = evaluate.score(specs, alerts_at(specs, [40.0, 50.0]), shifts=1)
    assert (sc["breaches"], sc["caught"], sc["median_warning_min"]) == (2, 2, 15.0)


def test_an_alert_at_the_close_or_after_does_not_catch_a_breach():
    specs = make(75.0, None, 80.0)
    sc = evaluate.score(specs, alerts_at(specs, [60.0, None, 55.0]), shifts=1)
    assert sc["caught"] == 1 and sc["alerts"] == 2 and sc["median_warning_min"] == 5.0


def test_an_alert_on_a_specimen_that_arrives_in_time_is_a_false_alert():
    specs = make(40.0, 75.0)
    sc = evaluate.score(specs, alerts_at(specs, [30.0, 30.0]), shifts=2)
    assert (sc["alerts"], sc["false_alerts"], sc["breaches"], sc["caught"]) == (2, 1, 1, 1)
    assert sc["alerts_per_shift"] == 1.0


def test_misses_count_as_zero_in_the_measures_that_include_them():
    specs = make(75.0, 80.0, 90.0, None)
    # caught with 40, 20 and 10 minutes of warning; the fourth is never alerted
    sc = evaluate.score(specs, alerts_at(specs, [20.0, 40.0, 50.0, None]), shifts=1)
    assert sc["median_warning_min"] == 20.0                      # among the caught only
    assert sc["median_warning_all_breaches_min"] == 15.0         # median of 40, 20, 10, 0
    assert sc["caught_with_good_warning"] == 0.5                 # 40 and 20 are at least 15; 10 and a miss are not


def test_a_method_that_catches_few_cannot_win_on_median_warning_alone():
    specs = make(*[75.0] * 4, window=1440)
    specs = [Spec(s.specimen_id, 24, "g", ("g",), 1440, 1500.0, True, "none") for s in specs]
    few = evaluate.score(specs, alerts_at(specs, [100.0, None, None, None]), shifts=1)
    many = evaluate.score(specs, alerts_at(specs, [1400.0] * 4), shifts=1)
    assert few["median_warning_min"] > many["median_warning_min"]
    assert many["median_warning_all_breaches_min"] > few["median_warning_all_breaches_min"] == 0


def test_no_breaches_gives_no_rates():
    sc = evaluate.score(make(10.0, 20.0), {"SP-r24-000001": None, "SP-r24-000002": None}, 1)
    assert sc["catch_rate"] is None and sc["median_warning_min"] is None


# ---- the two rules ----

def test_the_baseline_alerts_at_the_first_tick_after_the_quantile():
    s = Spec("a", 24, "g", ("g",), 120, None, True, "none")
    q = {"g": np.arange(1.0, 101.0)}  # the 0.5 quantile is 50.5 minutes
    assert evaluate.baseline_alerts([s], 0.5, q)["a"] == 55.0
    q = {"g": np.full(40, 50.0)}      # a threshold on a tick: strictly after it
    assert evaluate.baseline_alerts([s], 0.5, q)["a"] == 55.0


def test_the_baseline_does_not_alert_a_specimen_that_arrives_before_its_threshold():
    s = Spec("a", 24, "g", ("g",), 120, 30.0, False, "none")
    assert evaluate.baseline_alerts([s], 0.9, {"g": np.full(40, 50.0)})["a"] is None


def test_the_baseline_falls_back_to_the_first_parent_that_has_a_distribution():
    s = Spec("a", 24, "leaf", ("leaf", "parent", "all"), 120, None, True, "none")
    assert evaluate.baseline_alerts([s], 0.5, {"parent": np.full(40, 10.0), "all": np.full(40, 90.0)})["a"] == 15.0


def test_the_model_alerts_at_the_first_tick_whose_risk_reaches_the_threshold():
    s = spec(None, 60)
    series = {s.specimen_id: np.array([0.01, 0.05, 0.2, 0.2, 0.5, 0.7, 0.8, 0.9, 0.95, 0.99, 0.999, 1.0])}
    assert evaluate.model_alerts([s], series, 0.2)[s.specimen_id] == 15.0
    assert evaluate.model_alerts([s], series, 0.21)[s.specimen_id] == 25.0
    assert evaluate.model_alerts([s], series, 1.0)[s.specimen_id] == 60.0
    assert evaluate.model_alerts([s], {s.specimen_id: np.array([0.1] * 12)}, 0.5)[s.specimen_id] is None


def test_the_tuned_knob_is_the_smallest_that_meets_the_budget():
    specs = [spec(None, 60, sid=f"SP-r24-{i:06d}") for i in range(100)]
    # a fake rule: the knob is the share of specimens that alert, so alerts rise as the knob falls
    alerts_at = lambda v: {s.specimen_id: (30.0 if i >= v * 100 else None) for i, s in enumerate(specs)}
    knob = evaluate.tune(alerts_at, 0.0, 1.0, specs, shifts=10, budget=4.0, steps=40)
    assert evaluate.score(specs, alerts_at(knob), 10)["alerts_per_shift"] <= 4.0
    assert evaluate.score(specs, alerts_at(knob - 0.02), 10)["alerts_per_shift"] > 4.0


# ---- calibration ----

def test_the_reliability_table_counts_the_specimens_still_silent_at_the_landmark():
    params = json.loads(intervals.PARAMS_PATH.read_text())
    group = "urine, unpreserved, room temp | 06-11 | routine"
    specs = [Spec(f"s{i}", 24, group, (group,), 120, r, r is None or r > 120, "none")
             for i, r in enumerate([10.0, 35.0, 50.0, 200.0, None, 29.0])]
    out = evaluate.reliability(specs, params, 30)
    assert out["n"] == 4 and sum(b["n"] for b in out["bins"]) == 4  # 10 and 29 had arrived; the others were silent
    assert out["breach_rate"] == 0.5
    mix = risk.mixture_from(params, group)
    p = risk.risk_unreceived(30, mix, 120)
    assert out["brier"] == pytest.approx(np.mean([(p - y) ** 2 for y in (0, 0, 1, 1)]))
    assert out["brier_of_the_base_rate"] == pytest.approx(0.25)


# ---- the real held-out runs ----

@pytest.fixture(scope="module")
def result():
    return evaluate.evaluate()


def test_the_numbers_regenerate_exactly(result):
    assert json.dumps(evaluate.evaluate(), sort_keys=True) == json.dumps(result, sort_keys=True)


def test_the_held_out_population_matches_the_store(result):
    con = store.open_observed()
    assert result["test_specimens"] == con.sql("select count(*) from specimens where split = 'test'").fetchone()[0]
    assert result["with_a_clock"] + result["without_a_clock"] == result["test_specimens"]
    assert result["held_out_runs"] == "24 to 29" and result["shifts"] == 126
    t = store.open_truth()
    assert result["breaches"] == t.sql(
        "select count(*) from truth_specimens where split = 'test' and breach and clock_started").fetchone()[0]
    assert result["without_a_clock"] == t.sql(
        "select count(*) from truth_specimens where split = 'test' and not clock_started").fetchone()[0]


def test_the_counts_inside_every_operating_point_are_consistent(result):
    assert list(result["operating_points"]) == ["1", "2", "4", "6"]
    for budget, point in result["operating_points"].items():
        for name in ("baseline", "model"):
            s = point[name]
            assert s["breaches"] == result["breaches"] and 0 <= s["caught"] <= s["breaches"]
            assert s["alerts"] >= s["caught"] and s["alerts"] - s["false_alerts"] >= s["caught"]
            assert 0 <= s["caught_with_good_warning"] <= s["catch_rate"] <= 1
            assert sum(g["breaches"] for g in s["by_window"].values()) == s["breaches"]
            assert sum(g["breaches"] for g in s["by_fault"].values()) == s["breaches"]
            assert 0.3 * int(budget) < s["alerts_per_shift"] < 2.0 * int(budget), (budget, name, s["alerts_per_shift"])
        b = point["bootstrap_model_minus_baseline"]["median_warning_min"]
        assert b["low"] <= b["high"]


def test_more_budget_means_more_alerts(result):
    for name in ("baseline", "model"):
        volumes = [result["operating_points"][b][name]["alerts_per_shift"] for b in ("1", "2", "4", "6")]
        assert volumes == sorted(volumes)


def test_the_knobs_are_set_on_the_fit_runs_not_the_held_out_ones(result):
    obs, params = store.open_observed(), json.loads(intervals.PARAMS_PATH.read_text())
    start, stop = intervals.clock_events(config.load()["policy"])
    fit, _ = evaluate.load_specs(obs, None, start, stop, "fit")
    series = evaluate.risk_series(fit, params)
    shifts = len({s.run for s in fit}) * 21
    theta = evaluate.tune(lambda v: evaluate.model_alerts(fit, series, v), 0.0, 1.0, fit, shifts, 6, 30)
    assert theta == pytest.approx(result["operating_points"]["6"]["model_threshold"])
    assert {s.run for s in fit} == set(range(24))


def test_the_fit_runs_are_loaded_without_the_truth_file(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("opened the truth file")
    monkeypatch.setattr(store, "open_truth", refuse)
    start, stop = intervals.clock_events(config.load()["policy"])
    specs, counts = evaluate.load_specs(store.open_observed(), None, start, stop, "fit")
    assert counts["with_a_clock"] == len(specs) > 9000 and all(s.fault == "unknown" for s in specs)


def test_a_label_that_disagrees_with_the_scans_is_refused(monkeypatch):
    start, stop = intervals.clock_events(config.load()["policy"])
    real = store.open_truth()

    class Flipped:
        def execute(self, sql, params=None):
            rows = real.execute(sql, params).fetchall()
            return type("R", (), {"fetchall": lambda s: [(sid, not b, f) for sid, b, f in rows]})()
    with pytest.raises(RuntimeError, match="disagrees with its scans"):
        evaluate.load_specs(store.open_observed(), Flipped(), start, stop, "test")


def test_a_stale_fit_is_refused(tmp_path, monkeypatch):
    params = json.loads(intervals.PARAMS_PATH.read_text()) | {"policy_hash": "0" * 16}
    (tmp_path / "p.json").write_text(json.dumps(params))
    monkeypatch.setattr(intervals, "PARAMS_PATH", tmp_path / "p.json")
    with pytest.raises(RuntimeError, match="different policy"):
        evaluate.evaluate()


def test_the_calibration_covers_both_landmarks(result):
    for m, c in result["calibration"].items():
        assert c["landmark_min"] == int(m) and c["n"] == sum(b["n"] for b in c["bins"])
        assert 0 < c["brier"] < 0.25 and c["brier_of_the_base_rate"] <= 0.25


def test_the_table_prints_both_methods_at_every_budget(result):
    text = evaluate.table(result)
    for needle in ("baseline (quantile rule)", "risk model", "1 alerts per shift", "6 alerts per shift",
                   "median warning, all (min)", "calibration at 30 min", "never collected, set aside"):
        assert needle in text


def test_main_writes_the_numbers(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(evaluate, "RESULTS_PATH", tmp_path / "evaluation.json")
    assert evaluate.main() == 0
    assert json.loads((tmp_path / "evaluation.json").read_text())["operating_points"]["6"]["model"]["alerts"] > 0
    assert "wrote" in capsys.readouterr().out


def test_only_the_evaluation_and_the_store_open_the_truth_file():
    from test_truth_isolation import ALLOWED_TO_OPEN_TRUTH
    users = {p for p in (evaluate.ROOT / "src").rglob("*.py")
             if "open_truth" in p.read_text() or store.TRUTH_NAME in p.read_text()}
    assert users == ALLOWED_TO_OPEN_TRUTH
