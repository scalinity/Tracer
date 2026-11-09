"""The v2 evaluation harness (docs/neural/spec.md §8): equivalence with evaluate.py on the v1 held-out runs,
the metrics on hand-built cases, the decision rule, the test lock."""

import copy
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.generator import config
from src.pipeline import evaluate, intervals, neural_eval as ne, store, triage
from src.pipeline.evaluate import Spec

SPEC_MD = Path(evaluate.ROOT) / "docs" / "neural" / "spec.md"


def make_spec(sid, run=0, receipt=None, window=60, breach=None, tier=3, route="tube", **extra):
    breach = (receipt is None or receipt > window) if breach is None else breach
    return Spec(sid, run, "g", ("g",), window, receipt, breach, "none", tier, route)


# ---------------------------------------------------------------------------------------------------------
# Equivalence with evaluate.py on the v1 held-out runs (runs 24 to 29 as clusters, 21 shifts per run)
# ---------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def legacy():
    loaded = config.load()
    schemes = triage.weight_schemes(triage.load())
    start, stop = intervals.clock_events(loaded["policy"])
    params = json.loads(intervals.PARAMS_PATH.read_text(encoding="utf-8"))
    obs = store.open_observed()
    quantiles = evaluate.baseline_quantiles(intervals.load_intervals(obs, start, stop))
    fit, _ = evaluate.load_specs(obs, None, start, stop, "fit")
    fit_harm = {n: triage.harm_series(fit, evaluate.risk_series(fit, params), w) for n, w in schemes.items()}
    specs, _ = evaluate.load_test_specs(start, stop)
    series = evaluate.risk_series(specs, params)
    harm = {n: triage.harm_series(specs, series, w) for n, w in schemes.items()}
    fit_shifts = len({s.run for s in fit}) * ne.SHIFTS_PER_REPLICATE
    out = {"specs": specs, "series": series, "params": params, "quantiles": quantiles, "fit": fit,
           "schemes": schemes, "points": {}, "alerts": {}}
    for budget in ne.BUDGETS:
        q = ne.tune_knob(lambda v: evaluate.baseline_alerts(fit, v, quantiles), fit, fit_shifts, budget, 0.0, 0.9999)
        share = ne.tune_knob(lambda v: triage.window_share_alerts(fit, v), fit, fit_shifts, budget, 0.0, 1.0)
        theta = {n: ne.tune_knob(lambda v: ne.model_alerts(fit, fit_harm[n], v), fit, fit_shifts, budget,
                                 0.0, float(max(w.values()))) for n, w in schemes.items()}
        out["points"][budget] = {"q": q, "share": share, "theta": theta}
        out["alerts"][budget] = {
            "baseline": evaluate.baseline_alerts(specs, q, quantiles),
            "window_baseline": triage.window_share_alerts(specs, share),
            "model": evaluate.model_alerts(specs, harm["default"], theta["default"]),
            "equal": evaluate.model_alerts(specs, harm["equal"], theta["equal"])}
    return out


def v1_series(legacy, alerts):
    return ne.build_series(legacy["specs"], alerts, ne.SHIFTS_PER_REPLICATE)


def test_the_v1_held_out_runs_are_six_clusters_of_21_shifts(legacy):
    s = v1_series(legacy, legacy["alerts"][6]["model"])
    assert s.keys == (24, 25, 26, 27, 28, 29) and s.scalars[:, ne._C["shifts"]].sum() == 126


def test_the_knobs_set_by_the_vectorized_tuning_are_the_committed_ones(legacy):
    committed = json.loads(evaluate.RESULTS_PATH.read_text(encoding="utf-8"))["operating_points"]
    for budget, p in legacy["points"].items():
        c = committed[str(budget)]
        assert p["q"] == c["baseline_quantile"] and p["share"] == c["window_share"]
        assert p["theta"]["default"] == c["model_threshold"]
        assert p["theta"]["equal"] == c["weights_check"]["equal"]["harm_threshold"]


@pytest.mark.parametrize("budget", ne.BUDGETS)
def test_the_vectorized_score_equals_evaluate_score_exactly(legacy, budget):
    for name, alerts in legacy["alerts"][budget].items():
        want = evaluate.score(legacy["specs"], alerts, 126)
        got = ne.score(legacy["specs"], alerts, ne.SHIFTS_PER_REPLICATE)
        assert {k: got[k] for k in want} == want, (budget, name)


def test_the_vectorized_score_equals_the_committed_numbers(legacy):
    committed = json.loads(evaluate.RESULTS_PATH.read_text(encoding="utf-8"))["operating_points"]
    for budget in ne.BUDGETS:
        for name in ("baseline", "window_baseline", "model"):
            want = committed[str(budget)][name]
            got = ne.score(legacy["specs"], legacy["alerts"][budget][name], ne.SHIFTS_PER_REPLICATE)
            for k in ("alerts", "alerts_per_shift", "breaches", "caught", "catch_rate", "median_warning_min",
                      "median_warning_all_breaches_min", "caught_with_good_warning", "false_alerts"):
                assert got[k] == want[k], (budget, name, k)


@pytest.mark.parametrize("budget", ne.BUDGETS)
def test_the_vectorized_bootstrap_equals_evaluate_bootstrap_exactly(legacy, budget):
    alerts = legacy["alerts"][budget]
    series = {n: v1_series(legacy, a) for n, a in alerts.items()}
    boot = ne.bootstrap(series, "model", ["baseline", "window_baseline"])
    for base in ("baseline", "window_baseline"):
        want = evaluate.bootstrap(legacy["specs"], alerts, 21, base, "model")
        for k, w in want.items():
            got = boot["differences"][base][k]
            assert (got["low"], got["high"], got["share_ahead"]) == (w["low"], w["high"], w["share_model_ahead"]), (base, k)
    equal = evaluate.bootstrap(legacy["specs"], alerts, 21, "baseline", "equal")
    boot = ne.bootstrap(series, "equal", ["baseline"])
    for k, w in equal.items():
        got = boot["differences"]["baseline"][k]
        assert (got["low"], got["high"], got["share_ahead"]) == (w["low"], w["high"], w["share_model_ahead"])


def test_the_bootstrap_reports_every_metric_for_every_method_and_is_reproducible(legacy):
    series = {n: v1_series(legacy, a) for n, a in legacy["alerts"][6].items()}
    a, b = ne.bootstrap(series, "model"), ne.bootstrap(series, "model")
    assert a == b and a["resamples"] == 2000 and a["seed"] == 0 and a["clusters"] == 6
    assert set(a["intervals"]) == set(series) and set(a["differences"]) == set(series) - {"model"}
    for metrics in a["intervals"].values():
        assert set(metrics) == set(ne.METRICS)
        for m in metrics.values():
            assert m["n_used"] + m["n_excluded"] == 2000
            if m["low"] is not None:
                assert m["low"] <= m["high"]
    point = ne.point_metrics(series["model"])
    assert a["intervals"]["model"]["catch_rate"]["point"] == point["catch_rate"]
    d = a["differences"]["baseline"]["median_warning_all_breaches_min"]
    assert d["point"] == point["median_warning_all_breaches_min"] - ne.point_metrics(series["baseline"])["median_warning_all_breaches_min"]


def test_the_tuned_volume_on_the_fit_runs_respects_the_budget(legacy):
    fit, shifts = legacy["fit"], 24 * 21
    for budget in (1, 6):
        t = ne.tuned_alerts(lambda specs, v: triage.window_share_alerts(specs, v), fit, shifts,
                            legacy["specs"], 126, budget, 0.0, 1.0)
        assert 0.8 * budget < t.solved_on <= budget
        assert t.knob == legacy["points"][budget]["share"]
        assert t.alerts == legacy["alerts"][budget]["window_baseline"]


def test_the_calibration_matches_evaluate_reliability(legacy):
    for landmark in (30, 60):
        want = evaluate.reliability(legacy["specs"], legacy["params"], landmark)
        got = ne.calibration_at(legacy["specs"], legacy["series"], (landmark,))[str(landmark)]
        assert got["n"] == want["n"] and got["bins"] == want["bins"]
        assert got["brier"] == pytest.approx(want["brier"], abs=1e-12)
        assert got["brier_of_the_base_rate"] == pytest.approx(want["brier_of_the_base_rate"], abs=1e-12)
        assert 0 <= got["ece"] < 0.2 and got["slope"] is not None


def test_discrimination_covers_every_landmark_and_the_pooled_ticks(legacy):
    d = ne.discrimination(legacy["specs"], legacy["series"])
    assert list(d) == ["landmark_15", "landmark_30", "landmark_60", "landmark_90", "pooled"]
    assert d["landmark_30"]["n"] == evaluate.reliability(legacy["specs"], legacy["params"], 30)["n"]
    assert d["pooled"]["specimens"] == sum(len(s.ticks) > 0 for s in legacy["specs"])
    for k, v in d.items():
        assert 0.5 < v["auroc"] <= 1 and 0 < v["auprc"] <= 1, k


def test_the_rules_are_ranked_by_their_own_scores(legacy):
    for scores in (ne.window_rule_scores(legacy["specs"]), ne.quantile_rule_scores(legacy["specs"], legacy["quantiles"])):
        d = ne.discrimination(legacy["specs"], scores, (30, 60))
        assert d["landmark_60"]["auroc"] is not None and 0 <= d["pooled"]["auroc"] <= 1
    window = ne.window_rule_scores(legacy["specs"][:3])
    assert all(np.all(np.diff(v) > 0) for v in window.values() if len(v) > 1)


# ---------------------------------------------------------------------------------------------------------
# Alert metrics on hand-built cases
# ---------------------------------------------------------------------------------------------------------

def test_the_added_alert_metrics_on_a_small_case():
    # window 60: a loss alerted at 30, a loss never alerted, a late arrival alerted at 50, an on-time one alerted
    specs = [make_spec("a", receipt=None), make_spec("b", receipt=None), make_spec("c", receipt=75.0),
             make_spec("d", receipt=40.0), make_spec("e", receipt=20.0)]
    alerts = {"a": 30.0, "b": None, "c": 50.0, "d": 35.0, "e": None}
    m = ne.score(specs, alerts, 2)
    assert (m["alerts"], m["alerts_per_shift"], m["realized_alerts_per_shift"]) == (3, 1.5, 1.5)
    assert (m["breaches"], m["caught"], m["catch_rate"]) == (3, 2, 2 / 3)
    assert m["median_warning_min"] == 20.0 and m["median_warning_all_breaches_min"] == 10.0
    assert m["caught_with_good_warning"] == 1 / 3
    assert (m["false_alerts"], m["false_alerts_per_shift"]) == (1, 0.5)
    assert (m["never_received"], m["missed_losses"], m["missed_loss_rate"]) == (2, 1, 0.5)
    assert m["capped_mean_warning_min"] == pytest.approx((30 + 10 + 0) / 3)
    assert m["alerts_per_100_specimens"] == 60.0


def test_each_warning_is_capped_at_120_minutes_in_the_capped_mean():
    s = [make_spec("a", receipt=None, window=1440), make_spec("b", receipt=None, window=1440)]
    m = ne.score(s, {"a": 100.0, "b": 1400.0}, 1)
    assert m["capped_mean_warning_min"] == (120 + 40) / 2


def test_an_alert_at_the_window_close_is_a_missed_loss_not_a_catch():
    s = [make_spec("a", receipt=None)]
    m = ne.score(s, {"a": 60.0}, 1)
    assert (m["caught"], m["missed_losses"], m["alerts"]) == (0, 1, 1)


def test_undefined_metrics_are_none():
    m = ne.score([make_spec("a", receipt=10.0)], {"a": None}, 1)
    assert m["catch_rate"] is None and m["median_warning_min"] is None and m["missed_loss_rate"] is None
    assert m["capped_mean_warning_min"] is None and m["alerts_per_shift"] == 0.0


def test_the_weighted_median_equals_numpy_median_of_the_expanded_values():
    rng = np.random.default_rng(3)
    values = [rng.integers(0, 20, size=int(n)).astype(float) for n in (0, 3, 4, 1, 6)]
    vals, ids = ne._pooled(values)
    counts = rng.integers(0, 4, size=(300, len(values)))
    got = ne._weighted_median(vals, ids, counts)
    for row, g in zip(counts, got):
        expanded = np.concatenate([np.tile(v, c) for v, c in zip(values, row)])
        if len(expanded):
            assert g == np.median(expanded)
        else:
            assert np.isnan(g)


def test_a_resample_without_breaches_is_left_out_of_that_metric_and_counted():
    quiet = [make_spec(f"q{i}", run=0, receipt=10.0) for i in range(3)]
    loud = [make_spec(f"l{i}", run=1, receipt=None) for i in range(3)]
    specs = quiet + loud
    a = ne.build_series(specs, {s.specimen_id: 20.0 for s in specs}, 1)
    b = ne.build_series(specs, {s.specimen_id: 40.0 for s in specs}, 1)
    out = ne.bootstrap({"a": a, "b": b}, "b", resamples=400)
    d = out["differences"]["a"]["median_warning_all_breaches_min"]
    assert 0 < d["n_excluded"] < 400 and d["n_used"] + d["n_excluded"] == 400
    assert d["low"] <= d["high"]
    assert out["differences"]["a"]["alerts_per_shift"]["n_excluded"] == 0


def test_series_over_different_clusters_cannot_be_compared():
    s = [make_spec("a", run=0), make_spec("b", run=1)]
    one = ne.build_series(s, {"a": 10.0, "b": 10.0}, 1)
    two = ne.build_series(s[:1], {"a": 10.0}, 1)
    with pytest.raises(ValueError, match="same clusters"):
        ne.bootstrap({"x": one, "y": two}, "x")
    assert ne.build_series(s[:1], {"a": 10.0}, 1, clusters=(0, 1)).keys == (0, 1)


def test_slices_put_every_specimen_in_its_groups_and_share_the_shifts():
    specs = [make_spec("a", receipt=None, window=60, tier=1), make_spec("b", receipt=75.0, window=60, tier=2),
             make_spec("c", receipt=None, window=1440, tier=2), make_spec("d", receipt=10.0, window=1440, tier=3)]
    alerts = {"a": 30.0, "b": 30.0, "c": 100.0, "d": 100.0}
    by_window = ne.slice_metrics(specs, alerts, 2, ne.window_class)
    assert sorted(by_window) == ["long (a day or more)", "short (up to 2 h)"]
    assert sum(g["breaches"] for g in by_window.values()) == 3 and by_window["short (up to 2 h)"]["alerts_per_shift"] == 1.0
    by_outcome = ne.slice_metrics(specs, alerts, 2, ne.outcome)
    assert sorted(by_outcome) == ["in time", "late", "never received"] and by_outcome["in time"]["false_alerts"] == 1
    tags = {"a": ["x", "y"], "b": ["y"], "c": [], "d": ["x"]}
    by_tag = ne.slice_metrics(specs, alerts, 2, ne.mapping_key(tags))
    assert by_tag["x"]["alerts"] == 2 and by_tag["y"]["alerts"] == 2 and sorted(by_tag) == ["x", "y"]
    assert sum(g["alerts"] for g in ne.slice_metrics(specs, alerts, 2, ne.tier).values()) == 4


def test_terciles_split_the_clusters_by_volume():
    labels = ne.tercile_labels({i: float(i) for i in range(9)})
    assert [labels[i] for i in range(9)] == ["low"] * 3 + ["middle"] * 3 + ["high"] * 3
    assert set(ne.tercile_labels({0: 1.0, 1: 2.0}).values()) == {"middle"}


# ---------------------------------------------------------------------------------------------------------
# Knobs
# ---------------------------------------------------------------------------------------------------------

def share_rule(population):
    """A fake rule: the knob is the share of the population left unalerted, so volume falls as it rises."""
    return lambda specs, v: {s.specimen_id: (30.0 if i >= v * len(specs) else None) for i, s in enumerate(specs)}


def population(n, prefix, run=0):
    return [make_spec(f"{prefix}{i}", run=run, receipt=None) for i in range(n)]


def test_tune_knob_is_evaluate_tune_with_the_volume_computed_from_the_alerts():
    specs = population(100, "a")
    alerts_at = lambda v: {s.specimen_id: (30.0 if i >= v * 100 else None) for i, s in enumerate(specs)}
    want = evaluate.tune(alerts_at, 0.0, 1.0, specs, 10, 4.0, 40)
    assert ne.tune_knob(alerts_at, specs, 10, 4.0, 0.0, 1.0, 40) == want


def test_a_knob_tuned_on_the_history_respects_the_budget_there_and_moves_unchanged_to_the_scored_set():
    history, scored = population(200, "h"), population(100, "s", run=1)
    fn = share_rule(None)
    t = ne.tuned_alerts(fn, history, 10, scored, 5, budget=4.0, lo=0.0, hi=1.0)
    assert t.solved_on <= 4.0 and t.solved_on > 3.8
    assert t.alerts == fn(scored, t.knob)                       # the knob is not re-solved
    assert t.scored_volume == sum(v is not None for v in t.alerts.values()) / 5


def test_per_100_specimens_budgets_tune_on_volume_per_specimen():
    history = population(200, "h")
    t = ne.tuned_alerts(share_rule(None), history, 10, population(50, "s"), 5, budget=10, lo=0.0, hi=1.0, per="100")
    assert t.solved_on <= 10 and t.solved_on > 9.5
    with pytest.raises(ValueError):
        ne.alert_volume({}, [], 1, per="week")


def test_matched_volume_re_solves_the_knob_on_the_scored_set():
    scored = population(100, "s")
    fn = share_rule(None)
    # a rule that alerts twice as much per knob step: matched volume still lands on the budget
    greedy = lambda specs, v: fn(specs, v / 2)
    m = ne.matched_volume_alerts(greedy, scored, 5, budget=4.0, lo=0.0, hi=2.0)
    assert m.scored_volume <= 4.0 and m.scored_volume > 3.8
    history = population(100, "h")
    t = ne.tuned_alerts(greedy, history, 10, scored, 5, budget=4.0, lo=0.0, hi=2.0)
    assert t.scored_volume == pytest.approx(8.0) and m.knob != t.knob


def test_a_falling_risk_series_is_made_monotone_before_the_first_crossing_search():
    s = make_spec("a", receipt=None, window=30)
    series = {"a": np.array([0.1, 0.6, 0.2, 0.3, 0.4, 0.5])}
    assert ne.monotone_series(series)["a"].tolist() == [0.1, 0.6, 0.6, 0.6, 0.6, 0.6]
    assert ne.model_alerts([s], series, 0.5)["a"] == 10.0
    assert ne.model_alerts([s], series, 0.7)["a"] is None


# ---------------------------------------------------------------------------------------------------------
# Discrimination and calibration on toys with known answers
# ---------------------------------------------------------------------------------------------------------

def test_auroc_on_known_cases():
    assert ne.auroc([0, 0, 1, 1], [0.1, 0.4, 0.35, 0.8]) == 0.75
    assert ne.auroc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == 1.0
    assert ne.auroc([0, 0, 1, 1], [0.9, 0.8, 0.2, 0.1]) == 0.0
    assert ne.auroc([0, 1, 0, 1], [0.5, 0.5, 0.5, 0.5]) == 0.5       # all tied
    assert ne.auroc([1, 1], [0.1, 0.2]) is None and ne.auroc([], []) is None


def test_weights_act_like_repeated_rows():
    y, s = np.array([0, 0, 1, 1, 1]), np.array([0.1, 0.4, 0.35, 0.8, 0.4])
    w = np.array([1.0, 3.0, 2.0, 1.0, 1.0])
    rep = np.repeat(np.arange(5), w.astype(int))
    assert ne.auroc(y, s, w) == pytest.approx(ne.auroc(y[rep], s[rep]))
    assert ne.auprc(y, s, w) == pytest.approx(ne.auprc(y[rep], s[rep]))


def test_auprc_on_known_cases():
    assert ne.auprc([0, 0, 1, 1], [0.1, 0.4, 0.35, 0.8]) == pytest.approx(5 / 6)   # the textbook average precision
    assert ne.auprc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == 1.0
    assert ne.auprc([1, 0, 0, 0], [0.9, 0.1, 0.2, 0.3]) == 1.0
    assert ne.auprc([0, 0, 0], [0.1, 0.2, 0.3]) is None
    assert ne.auprc([0, 1], [0.5, 0.5]) == 0.5                                      # a tie: precision of the pair


def test_pooled_rows_weight_each_specimen_equally():
    long_one, short_one = make_spec("a", receipt=None, window=120), make_spec("b", receipt=None, window=30)
    y, p, w = ne.pooled_rows([long_one, short_one], {"a": np.linspace(0, 1, 24), "b": np.linspace(0, 1, 6)})
    assert len(y) == 30 and w.sum() == pytest.approx(2.0)
    assert w[:24].sum() == pytest.approx(1.0) and w[24:].sum() == pytest.approx(1.0)


def test_landmark_rows_take_the_specimens_still_silent_with_a_longer_window():
    specs = [make_spec("silent", receipt=None, window=120), make_spec("arrived", receipt=20.0, window=120),
             make_spec("at_it", receipt=30.0, window=120), make_spec("short", receipt=None, window=30),
             make_spec("late", receipt=130.0, window=120)]
    series = {s.specimen_id: np.arange(1.0, len(s.ticks) + 1) for s in specs}
    y, p = ne.landmark_rows(specs, series, 30)
    assert y.tolist() == [1.0, 1.0] and p.tolist() == [6.0, 6.0]       # "silent" and "late"
    with pytest.raises(ValueError):
        ne.landmark_rows(specs, series, 32)


def test_expected_calibration_error_on_known_cases():
    y = np.tile([0.0, 1.0], 50)
    assert ne.expected_calibration_error(y, np.full(100, 0.9)) == pytest.approx(0.4)
    assert ne.expected_calibration_error(y, np.full(100, 0.5)) == pytest.approx(0.0)
    p = np.linspace(0.05, 0.95, 100)
    perfect = (np.arange(100) % 10 < np.round(p * 10)).astype(float)       # not a claim of exactness: a small ECE
    assert ne.expected_calibration_error(perfect, p) < 0.1
    assert ne.expected_calibration_error([], []) is None
    # two groups of equal mass, one predicted 0.2 and seen 0.4, one predicted 0.8 and seen 0.6: both off by 0.2
    y2 = np.array([1, 1, 0, 0, 0] * 2 + [1, 1, 1, 0, 0] * 2, dtype=float)
    p2 = np.array([0.2] * 10 + [0.8] * 10)
    assert ne.expected_calibration_error(y2, p2, bins=2) == pytest.approx(0.2)


def test_the_logistic_calibration_recovers_the_slope_and_intercept():
    rng = np.random.default_rng(1)
    z = rng.normal(size=20000)
    truth = 1 / (1 + np.exp(-(0.3 + 0.5 * z)))
    y = (rng.random(20000) < truth).astype(float)
    p = 1 / (1 + np.exp(-z))                  # overconfident: the real slope on its logit is 0.5
    fit = ne.logistic_calibration(y, p)
    assert fit["slope"] == pytest.approx(0.5, abs=0.06) and fit["intercept"] == pytest.approx(0.3, abs=0.06)
    ok = ne.logistic_calibration((rng.random(20000) < p).astype(float), p)
    assert ok["slope"] == pytest.approx(1.0, abs=0.06) and ok["intercept"] == pytest.approx(0.0, abs=0.06)
    assert ne.logistic_calibration([0, 0, 0], [0.1, 0.2, 0.3]) is None
    assert ne.logistic_calibration([0, 1, 0, 1], [0.5] * 4) is None


def test_calibration_reports_the_brier_scores_and_the_reliability_table():
    y, p = np.array([0, 0, 1, 1.0]), np.array([0.1, 0.3, 0.6, 0.9])
    c = ne.calibration(y, p)
    assert c["brier"] == pytest.approx((0.01 + 0.09 + 0.16 + 0.01) / 4) and c["brier_of_the_base_rate"] == 0.25
    assert c["n"] == 4 and sum(b["n"] for b in c["bins"]) == 4 and c["breach_rate"] == 0.5
    assert ne.calibration([], [])["n"] == 0


# ---------------------------------------------------------------------------------------------------------
# The decision rule (§8.6)
# ---------------------------------------------------------------------------------------------------------

def metrics(warning, missed=0.1, volume=6.0):
    return {"median_warning_all_breaches_min": warning, "missed_loss_rate": missed, "realized_alerts_per_shift": volume}


def passing_case():
    headline = {w: {ne.TRACERNET: metrics(60.0), "window_rule": metrics(45.0), "analytic_global": metrics(50.0),
                    "analytic_local": metrics(52.0)} for w in ne.WEIGHTINGS}
    diff = {"median_warning_all_breaches_min": {"low": 3.0, "high": 20.0}, "missed_loss_rate": {"low": -0.05, "high": 0.01}}
    differences = {w: {b: copy.deepcopy(diff) for b in ne.BASELINES} for w in ne.WEIGHTINGS}
    brier = {ne.TRACERNET: {"30": 0.108, "60": 0.180}, "analytic_global": {30: 0.110, 60: 0.182},
             "analytic_local": {"30": 0.105, "60": 0.178}}
    return headline, differences, brier


def test_the_rule_text_is_the_spec_text_verbatim():
    lines = SPEC_MD.read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("### 8.6")) + 2
    quoted = []
    for l in lines[start:]:
        if not l.startswith(">"):
            break
        quoted.append(l[2:] if l.startswith("> ") else "")
    assert ne.RULE_TEXT == "\n".join(quoted)
    assert sorted(ne.RULES) == ["R1", "R2", "R3", "R4"] and all(t in ne.RULE_TEXT for t in ne.RULES.values())


def test_all_four_rules_holding_makes_tracernet_primary():
    out = ne.decide(*passing_case())
    assert [out[r]["held"] for r in ("R1", "R2", "R3", "R4")] == [True] * 4
    assert out["verdict"]["tracernet_primary"] and out["verdict"]["primary_model"] == "TracerNet"
    assert len(out["R1"]["checks"]) == 6 and out["R4"]["unequal"] is False and "r1_at_matched_volume" not in out["R4"]
    assert out["R1"]["checks"][0]["difference_min"] == 15.0 and out["R1"]["rule"] == ne.RULES["R1"]
    assert out["R3"]["checks"][0]["better_analytic_brier"] == 0.105


def failing(mutate):
    headline, differences, brier = passing_case()
    mutate(headline, differences, brier)
    return ne.decide(headline, differences, brier)


def test_r1_fails_alone_when_a_gain_is_under_five_minutes():
    out = failing(lambda h, d, b: h["equal"][ne.TRACERNET].update(median_warning_all_breaches_min=54.0))
    assert [out[r]["held"] for r in ("R1", "R2", "R3", "R4")] == [False, True, True, True]
    assert not out["verdict"]["tracernet_primary"]


def test_r1_fails_alone_when_an_interval_reaches_zero():
    out = failing(lambda h, d, b: d["3-2-1"]["analytic_local"]["median_warning_all_breaches_min"].update(low=0.0))
    assert [out[r]["held"] for r in ("R1", "R2", "R3", "R4")] == [False, True, True, True]
    bad = [c for c in out["R1"]["checks"] if not c["held"]]
    assert len(bad) == 1 and (bad[0]["weighting"], bad[0]["baseline"]) == ("3-2-1", "analytic_local")
    assert not out["verdict"]["tracernet_primary"]


def test_r2_fails_alone_when_the_loss_deficit_may_exceed_two_points():
    out = failing(lambda h, d, b: d["equal"]["window_rule"]["missed_loss_rate"].update(high=0.02))
    assert [out[r]["held"] for r in ("R1", "R2", "R3", "R4")] == [True, False, True, True]
    assert not out["verdict"]["tracernet_primary"]


def test_r3_fails_alone_when_the_brier_is_worse_than_the_better_refit_by_over_a_hundredth():
    out = failing(lambda h, d, b: b[ne.TRACERNET].update({"60": 0.1891}))
    assert [out[r]["held"] for r in ("R1", "R2", "R3", "R4")] == [True, True, False, True]
    assert out["R3"]["checks"][1]["excess"] == pytest.approx(0.0111)
    exact = failing(lambda h, d, b: b[ne.TRACERNET].update({"30": 0.115}))        # 0.115 - 0.105 = 0.010 is allowed
    assert exact["R3"]["checks"][0]["held"] is True


def test_r4_fails_alone_and_the_verdict_does_not_depend_on_it():
    out = failing(lambda h, d, b: h["3-2-1"][ne.TRACERNET].update(realized_alerts_per_shift=7.0))
    assert [out[r]["held"] for r in ("R1", "R2", "R3", "R4")] == [True, True, True, False]
    assert out["verdict"]["tracernet_primary"] and out["R4"]["unequal"]
    assert out["R4"]["r1_at_matched_volume"] is None         # not supplied yet: the caller must run the matched path


def test_unequal_alerts_trigger_the_matched_volume_repeat_of_r1():
    headline, differences, brier = passing_case()
    headline["3-2-1"][ne.TRACERNET]["realized_alerts_per_shift"] = 5.0
    matched, matched_diffs = copy.deepcopy(headline), copy.deepcopy(differences)
    matched["3-2-1"][ne.TRACERNET]["realized_alerts_per_shift"] = 6.0
    matched_diffs["equal"]["analytic_global"]["median_warning_all_breaches_min"]["low"] = -1.0
    out = ne.decide(headline, differences, brier, matched, matched_diffs)
    assert out["R4"]["unequal"] and out["R4"]["held"] is False
    sensitivity = out["R4"]["r1_at_matched_volume"]
    assert sensitivity["held"] is False and sum(not c["held"] for c in sensitivity["checks"]) == 1
    assert out["R1"]["held"] and out["verdict"]["tracernet_primary"]      # a sensitivity check, not a replacement


def test_a_missing_number_fails_its_check_rather_than_passing():
    headline, differences, brier = passing_case()
    del brier["analytic_local"]
    del differences["equal"]["analytic_global"]
    out = ne.decide(headline, differences, brier)
    assert out["R1"]["held"] is False and out["R2"]["held"] is False and out["R3"]["held"] is True


# ---------------------------------------------------------------------------------------------------------
# The test lock (§8.7)
# ---------------------------------------------------------------------------------------------------------

def hashes(**override):
    base = ne.current_hashes("a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64)
    return base | override


@pytest.fixture
def lock_files(tmp_path):
    return {"lock_path": tmp_path / "neural" / "test_lock.json", "runs_path": tmp_path / "neural" / "test_runs.jsonl",
            "now": "2026-11-12T09:00:00-05:00"}


def runs(files):
    return [json.loads(l) for l in files["runs_path"].read_text().splitlines()]


def test_the_first_test_evaluation_writes_the_lock(lock_files):
    entry = ne.guard_test_run("test", hashes(), **lock_files)
    lock = json.loads(lock_files["lock_path"].read_text())
    assert entry["status"] == "first" and lock["time"] == "2026-11-12T09:00:00-05:00" and lock["first_split"] == "test"
    for field in ne.HASH_FIELDS:
        assert field in lock
    assert lock["artifact_sha256"] == "a" * 64 and lock["decision_model_sha256"] is None
    assert [r["status"] for r in runs(lock_files)] == ["first"]


def test_matching_hashes_let_a_later_evaluation_run_and_it_is_listed(lock_files):
    ne.guard_test_run("test", hashes(), **lock_files)
    again = ne.guard_test_run("test_ood", hashes(), **lock_files)
    assert again["status"] == "repeat" and [r["status"] for r in runs(lock_files)] == ["first", "repeat"]


def test_a_changed_hash_refuses_without_a_reason_and_changes_nothing(lock_files):
    ne.guard_test_run("test", hashes(), **lock_files)
    before = lock_files["lock_path"].read_text()
    with pytest.raises(ne.LockError, match="calibration_sha256"):
        ne.guard_test_run("test", hashes(calibration_sha256="f" * 64), **lock_files)
    with pytest.raises(ne.LockError, match="decision_model_sha256"):
        ne.guard_test_run("test", hashes(decision_model_sha256="1" * 64), **lock_files)
    assert lock_files["lock_path"].read_text() == before and len(runs(lock_files)) == 1


def test_a_changed_source_file_refuses(lock_files):
    ne.guard_test_run("test", hashes(), **lock_files)
    with pytest.raises(ne.LockError, match="neural_eval_blob_sha"):
        ne.guard_test_run("test", hashes(neural_eval_blob_sha="0" * 40), **lock_files)


def test_a_relock_with_a_reason_is_accepted_logged_and_becomes_the_lock(lock_files):
    ne.guard_test_run("test", hashes(), **lock_files)
    new = hashes(calibration_sha256="f" * 64)
    entry = ne.guard_test_run("test", new, relock_reason="recalibrated per window class on validation",
                              **(lock_files | {"now": "2026-11-13T10:00:00-05:00"}))
    assert entry["status"] == "relock" and entry["changed"] == ["calibration_sha256"]
    assert entry["previous"] == {"calibration_sha256": "b" * 64}
    lock = json.loads(lock_files["lock_path"].read_text())
    assert lock["calibration_sha256"] == "f" * 64 and lock["relocked_from"] == "2026-11-12T09:00:00-05:00"
    assert ne.guard_test_run("test", new, **lock_files)["status"] == "repeat"
    log = runs(lock_files)
    assert [r["status"] for r in log] == ["first", "relock", "repeat"]
    assert log[1]["reason"] == "recalibrated per window class on validation"


def test_a_blank_relock_reason_is_refused(lock_files):
    ne.guard_test_run("test", hashes(), **lock_files)
    with pytest.raises(ValueError, match="reason"):
        ne.guard_test_run("test", hashes(store_sha256="9" * 64), relock_reason="  ", **lock_files)


def test_only_the_test_sets_are_locked(lock_files):
    for split in ("val", "legacy_v1", "control_v1world"):
        assert ne.guard_test_run(split, hashes(), **lock_files) is None
    assert not lock_files["lock_path"].exists() and not lock_files["runs_path"].exists()


def test_the_lock_record_needs_every_hash(lock_files):
    short = {k: v for k, v in hashes().items() if k != "store_sha256"}
    with pytest.raises(ValueError, match="store_sha256"):
        ne.guard_test_run("test", short, **lock_files)


def test_the_default_lock_paths_are_the_ones_the_spec_names():
    assert ne.LOCK_PATH == evaluate.ROOT / "results" / "neural" / "test_lock.json"
    assert ne.TEST_RUNS_PATH == evaluate.ROOT / "results" / "neural" / "test_runs.jsonl"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_the_blob_sha_is_what_git_gives_the_file():
    path = Path(ne.__file__)
    want = subprocess.run(["git", "hash-object", str(path)], capture_output=True, text=True, check=True).stdout.strip()
    assert ne.git_blob_sha(path) == want
    assert ne.current_hashes("a", "b", "c", "d", "e")["evaluate_blob_sha"] == ne.git_blob_sha(Path(evaluate.__file__))


def test_hashing_a_configuration_ignores_key_order():
    assert ne.sha256_json({"a": 1, "b": [1, 2]}) == ne.sha256_json({"b": [1, 2], "a": 1})
    assert ne.sha256_json({"a": 1}) != ne.sha256_json({"a": 2})


# ---------------------------------------------------------------------------------------------------------
# The truth file stays behind evaluate.py
# ---------------------------------------------------------------------------------------------------------

def test_this_module_never_names_the_truth_file():
    text = Path(ne.__file__).read_text(encoding="utf-8")
    assert "open_truth" not in text and store.TRUTH_NAME not in text
    assert re.search(r"\bduckdb\b", text) is None


# ---------------------------------------------------------------------------------------------------------
# The driver's vectorized pieces against the per-specimen code they replace
# ---------------------------------------------------------------------------------------------------------

def random_specs(n, seed=0):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        window = int(rng.choice([60, 120, 1440, 2880]))
        receipt = None if rng.random() < 0.1 else float(rng.choice([0.4, 3.0, 4.99, 5.0, 17.3, 59.0, 60.0, 61.2,
                                                                    rng.uniform(0, 3000)]))
        out.append(make_spec(f"s{i}", run=i % 3, receipt=receipt, window=window, tier=int(rng.integers(1, 4))))
    return out


def test_flat_ticks_are_each_specimens_ticks():
    specs = random_specs(400)
    flat = ne.flat_ticks(specs)
    for i, s in enumerate(specs):
        assert flat.t[flat.start[i]:flat.start[i + 1]].tolist() == s.ticks.tolist()
        assert (flat.owner[flat.start[i]:flat.start[i + 1]] == i).all()


def test_the_running_maximum_is_taken_within_each_specimen_only():
    specs = random_specs(300, seed=1)
    flat = ne.flat_ticks(specs)
    v = np.random.default_rng(2).random(len(flat.t))
    got = ne.running_max(v, flat)
    for i in range(len(specs)):
        part = slice(flat.start[i], flat.start[i + 1])
        assert np.array_equal(got[part], np.maximum.accumulate(v[part]) if flat.n[i] else v[part])


def test_sorted_quantiles_equal_numpy_quantile_float_for_float():
    rng = np.random.default_rng(4)
    for _ in range(300):
        arrays = [np.sort(np.round(rng.lognormal(3, 1, int(rng.choice([1, 2, 3, 30, 31, 997]))) * 60) / 60)
                  for _ in range(int(rng.integers(1, 5)))]
        sizes = np.array([len(a) for a in arrays])
        offsets = np.concatenate([[0], np.cumsum(sizes)[:-1]])
        lo, hi, qs = 0.0, 0.9999, [0.0, 0.5, 0.9999, *rng.random(3)]
        for _ in range(30):                       # the knobs a bisection visits
            mid = (lo + hi) / 2
            qs.append(mid)
            lo, hi = (mid, hi) if rng.random() < 0.5 else (lo, mid)
        for q in qs:
            got = ne.sorted_quantiles(np.concatenate(arrays), offsets, sizes, q)
            assert got.tolist() == [float(np.quantile(a, q)) for a in arrays]


def test_the_weighted_median_without_repeated_values_takes_the_chunked_path_and_still_matches():
    rng = np.random.default_rng(5)
    values = [rng.permutation(1000)[:int(n)].astype(float) + i * 1000 for i, n in enumerate((3, 5, 0, 8))]
    vals, ids = ne._pooled(values)
    assert len(np.unique(vals)) == len(vals)
    counts = rng.integers(0, 3, size=(200, len(values)))
    for row, g in zip(counts, ne._weighted_median(vals, ids, counts)):
        expanded = np.concatenate([np.tile(v, c) for v, c in zip(values, row)])
        assert (g == np.median(expanded)) if len(expanded) else np.isnan(g)


def test_the_vectorized_analytic_risk_is_evaluate_risk_series_float_for_float(legacy):
    specs = legacy["specs"]
    flat = ne.flat_ticks(specs)
    got = ne.analytic_risk(specs, flat, legacy["params"])
    for i, s in enumerate(specs):
        assert np.array_equal(got[flat.start[i]:flat.start[i + 1]], legacy["series"][s.specimen_id]), s.specimen_id


def test_a_missing_group_takes_its_parent_as_fit_all_would():
    rows = [("urine", b, p, 30.0 + (i % 17)) for i, (b, p) in enumerate([(0, "routine"), (1, "routine")] * 40)]
    params = intervals.fit_all(rows)
    done = ne.complete_groups(params, rows, {"urine | 18-23 | routine", "tissue | 00-05 | stat"})
    assert done["groups"]["urine | 18-23 | routine"]["fitted_from"] == "urine | any hour | routine"
    assert done["groups"]["tissue | 00-05 | stat"]["fitted_from"] == "all | any hour | any priority"
    assert done["groups"]["tissue | 00-05 | stat"]["mu"] == params["pooled"]["mu"]
    assert ne.complete_groups(params, rows, params["groups"]) is params


# ---------------------------------------------------------------------------------------------------------
# legacy_v1 through the whole driver reproduces results/evaluation.json
# ---------------------------------------------------------------------------------------------------------

LEGACY_NAMES = {"baseline": ("quantile_global", "none"), "window_baseline": ("window_rule", "none"),
                "model": ("analytic_global", "3-2-1")}
SCORE_KEYS = ("alerts", "alerts_per_shift", "breaches", "caught", "catch_rate", "median_warning_min",
              "median_warning_all_breaches_min", "caught_with_good_warning", "false_alerts")


@pytest.fixture(scope="module")
def legacy_run():
    return ne.evaluate_split("legacy_v1", focus="analytic_global", write=False)


def test_the_legacy_set_through_the_driver_reproduces_the_committed_evaluation_exactly(legacy_run):
    r, committed = legacy_run, json.loads(evaluate.RESULTS_PATH.read_text(encoding="utf-8"))
    assert r["counts"]["scored_specimens"] == committed["with_a_clock"] and r["counts"]["clusters"] == 6
    assert r["counts"]["breaches"] == committed["breaches"] and r["counts"]["scored_shifts"] == committed["shifts"]
    for budget in ("1", "2", "4", "6"):
        c, op, kn = committed["operating_points"][budget], r["operating_points"][f"{budget}/shift"], r["knobs"][f"{budget}/shift"]
        for ck, (m, w) in LEGACY_NAMES.items():
            assert {k: op[m][w][k]["point"] for k in SCORE_KEYS} == {k: c[ck][k] for k in SCORE_KEYS}, (budget, ck)
        for knob, (m, w) in (("baseline_quantile", ("quantile_global", "none")), ("window_share", ("window_rule", "none")),
                             ("model_threshold", ("analytic_global", "3-2-1"))):
            assert kn[m][w]["knob"]["min"] == kn[m][w]["knob"]["max"] == c[knob], (budget, knob)
        for ck, w in (("equal", "equal"), ("steep", "9-3-1")):
            check = c["weights_check"][ck]
            assert kn["analytic_global"][w]["knob"]["min"] == check["harm_threshold"]
            assert {k: op["analytic_global"][w][k]["point"] for k in SCORE_KEYS} == {k: check[k] for k in SCORE_KEYS}
        d = r["differences"][f"{budget}/shift"]
        for w, block in (("3-2-1", {"bootstrap_model_minus_baseline": c["bootstrap_model_minus_baseline"],
                                     "bootstrap_model_minus_window_baseline": c["bootstrap_model_minus_window_baseline"]}),
                         ("equal", {"bootstrap_model_minus_baseline": c["weights_check"]["equal"]["bootstrap_minus_baseline"],
                                    "bootstrap_model_minus_window_baseline":
                                        c["weights_check"]["equal"]["bootstrap_minus_window_baseline"]})):
            for name, base in (("bootstrap_model_minus_baseline", "quantile_global"),
                               ("bootstrap_model_minus_window_baseline", "window_rule")):
                for k, want in block[name].items():
                    got = d[w][base][k]
                    assert (got["low"], got["high"], got["share_ahead"]) == \
                        (want["low"], want["high"], want["share_model_ahead"]), (budget, w, base, k)
    for landmark in ("30", "60"):
        got, want = r["calibration"]["analytic_global"][landmark], committed["calibration"][landmark]
        assert {k: got[k] for k in want} == want
    six = committed["operating_points"]["6"]
    for ck, (m, w) in LEGACY_NAMES.items():
        for label, want in six[ck]["by_window"].items():
            assert {k: r["slices"]["window_class"][label][m][w][k] for k in want} == want
        for tier, want in six[ck]["by_tier"].items():
            assert {k: r["slices"]["tier"][tier][m][w][k] for k in want} == want
        for fault, want in six[ck]["by_fault"].items():
            assert {k: r["slices"]["fault"][fault][m][w][k] for k in want} == want


def test_the_legacy_headline_is_the_one_the_spec_quotes(legacy_run):
    op = legacy_run["operating_points"]["6/shift"]
    assert op["quantile_global"]["none"]["median_warning_all_breaches_min"]["point"] == 30
    assert op["window_rule"]["none"]["median_warning_all_breaches_min"]["point"] == 45
    assert op["analytic_global"]["3-2-1"]["median_warning_all_breaches_min"]["point"] == 55
    assert op["analytic_global"]["equal"]["median_warning_all_breaches_min"]["point"] == 45
    assert [op[m][w]["false_alerts"]["point"] for m, w in LEGACY_NAMES.values()] == [690, 569, 706]
    assert legacy_run["local_fit"]["fell_back"] == 0 and "note" in legacy_run["local_fit"]


# ---------------------------------------------------------------------------------------------------------
# The v2 driver on a small store built here
# ---------------------------------------------------------------------------------------------------------

V2_INDICES = (0, 7, 8, 22)                  # train, two validation scenarios, one test scenario
V2_REPLICATES = {"train": [0, 1], "val": [0, 1, 2], "test": [0, 1], "test_ood": [0, 1]}


@pytest.fixture(scope="module")
def v2_store(tmp_path_factory):
    from src.neural import devlib

    d = tmp_path_factory.mktemp("neural-eval")
    out = devlib.build(d, indices=V2_INDICES, checked=False, workers=2, replicates=V2_REPLICATES)
    return out["store_dir"]


@pytest.fixture(scope="module")
def v2_run(v2_store):
    return ne.evaluate_split("val", ne.baselines(), store_dir=v2_store, workers=1, keep_alerts=True, write=False)


def specs_of(store_dir, scenario, role):
    specs, _ = evaluate.load_specs_v2("val", (role,), store_dir, labels=role == "scored", scenarios=[scenario])
    return specs


def test_v2_knobs_respect_each_budget_on_the_history_replicate(v2_run):
    """F004: on the v2 sets every knob meets its budget on the history replicate, with no exception for a knob
    that saturates at the top of its range; where only the no-alert knob does, the scenario is counted."""
    assert v2_run["counts"]["tuning_groups"] == 2 and v2_run["settings"]["cluster"] == "scenario"
    budgets = {b.key: b for b in ne.budget_list()}
    for g in v2_run["detail"]["groups"]:
        for key, by_series in g["knobs"].items():
            for series_key, (knob, on_history, _) in by_series.items():
                assert on_history <= budgets[key].value, (g["key"], key, series_key)
    for key, by_method in v2_run["knobs"].items():
        for by_w in by_method.values():
            for k in by_w.values():
                assert k["groups"] == 2 and k["history_volume"]["max"] is not None and k["over_budget"] == 0
                assert 0 <= k["no_alert_groups"] <= 2


def test_v2_knobs_and_alerts_equal_evaluate_py_per_specimen_code(v2_run, v2_store):
    loaded = config.load()
    start, stop = intervals.clock_events(loaded["policy"])
    obs = store.open_observed_v2(v2_store)
    for g in v2_run["detail"]["groups"]:
        sid = g["key"]
        history, scored = specs_of(v2_store, sid, "history"), specs_of(v2_store, sid, "scored")
        rows = intervals.load_intervals(obs, start, stop, split="val", scenarios=[sid], roles=["history"], scored=True)
        assert ne.interval_rows(history) == rows                    # the local fit sees load_intervals' rows
        quantiles = evaluate.baseline_quantiles(rows)
        params = ne.complete_groups(intervals.fit_all(rows), rows, {s.group for s in history + scored})
        risk = evaluate.risk_series(history + scored, params)
        harm = triage.harm_series(history + scored, risk, triage.weight_schemes(triage.load())["default"])
        none = lambda specs: {s.specimen_id: None for s in specs}   # noqa: E731 - past every quantile
        rules = {"window_rule|none": (lambda specs, v: triage.window_share_alerts(specs, v), 1.0, 1.0),
                 "quantile_local|none": (lambda specs, v: none(specs) if v > 1 else
                                         evaluate.baseline_alerts(specs, v, quantiles), 0.9999, 1.0),
                 "analytic_local|3-2-1": (lambda specs, v: evaluate.model_alerts(specs, harm, v), 3.0, 3.0)}
        for b in ne.budget_list((1, 6), (10,)):
            for key, (fn, hi, top) in rules.items():
                t = ne.tuned_alerts(fn, history, 21.0, scored, 42.0, b.value, 0.0, hi, per=b.per,
                                    no_alert=float(np.nextafter(top, np.inf)))
                knob, on_history, on_scored = g["knobs"][b.key][key]
                assert (knob, on_history, on_scored) == (t.knob, t.solved_on, t.scored_volume), (sid, b.key, key)
                assert g["alerts"][b.key][key] == t.alerts, (sid, b.key, key)


def test_the_local_refit_falls_back_to_the_global_fit_below_100_specimens_and_logs_it(v2_store, v2_run, monkeypatch):
    group = ne._group("s", specs_of(v2_store, "dev-07-weekend-050", "history")[:99],
                      specs_of(v2_store, "dev-07-weekend-050", "scored"), False, {})
    model = ne.AnalyticModel("analytic_local", "local")
    model.params = {"sentinel": True}
    params, note = model.prepare(group)
    assert params == {"sentinel": True} and "99 specimens with a clock" in note and "global fit used" in note
    monkeypatch.setattr(ne, "MIN_LOCAL_FIT", 10 ** 6)
    r = ne.evaluate_split("val", ne.baselines(), store_dir=v2_store, workers=1, write=False)
    notes = r["local_fit"]["notes"]
    assert r["local_fit"]["fell_back"] == 4 and {n["method"] for n in notes} == {"analytic_local", "quantile_local"}
    assert all("global" in n["note"] for n in notes)
    for key, op in r["operating_points"].items():
        assert op["analytic_local"] == op["analytic_global"] and op["quantile_local"] == op["quantile_global"], key
    assert r["calibration"]["analytic_local"] == r["calibration"]["analytic_global"]
    assert v2_run["local_fit"]["fell_back"] == 0                 # with real histories nothing falls back


def test_a_local_fit_whose_risk_is_not_a_number_falls_back_to_the_global_fit_and_logs_it(v2_store, monkeypatch):
    """A local mixture with no never-received share gives 0/0 for a specimen silent past both components; the
    scenario then uses the global fit for its history and scored replicates alike, with a note."""
    real_prepare = ne.AnalyticModel.prepare

    def degenerate(self, group):
        params, note = real_prepare(self, group)
        if self.fit != "local" or note:
            return params, note
        bad = {**params, "pooled": {**params["pooled"], "weights": {"normal": 1.0, "stalled": 0.0, "never": 0.0}},
               "groups": {g: {**v, "mu": -50.0, "sigma": 0.01} for g, v in params["groups"].items()}}
        return bad, None
    monkeypatch.setattr(ne.AnalyticModel, "prepare", degenerate)
    r = ne.evaluate_split("val", ne.baselines(), store_dir=v2_store, workers=1, write=False)
    notes = [n for n in r["local_fit"]["notes"] if n["method"] == "analytic_local"]
    assert notes and all("not a number" in n["note"] and "global fit used" in n["note"] for n in notes)
    for key, op in r["operating_points"].items():
        assert op["analytic_local"] == op["analytic_global"], key


class Spy:
    """A risk series that records what it was asked for."""

    def __init__(self):
        self.calls = []

    def __call__(self, specs, context):
        self.calls.append((context["role"], context["scenario_id"], tuple(context["history_runs"]),
                           tuple(context["scored_runs"]), tuple(s.specimen_id for s in specs)))
        return {s.specimen_id: np.minimum(1.0, s.ticks / s.window) for s in specs}


def test_every_method_gets_the_same_specimens_and_history(v2_store):
    a, b = Spy(), Spy()
    methods = [ne.RiskMethod("spy_a", a, monotone=True), ne.RiskMethod("spy_b", b, monotone=True)]
    r = ne.evaluate_split("val", methods, store_dir=v2_store, workers=1, write=False)
    assert a.calls == b.calls and len(a.calls) == 4               # two scenarios, history and scored each
    for role, scenario, history_runs, scored_runs, ids in a.calls:
        want = specs_of(v2_store, scenario, role)
        assert ids == tuple(s.specimen_id for s in want)
        assert history_runs == tuple(sorted({s.run for s in specs_of(v2_store, scenario, "history")}))
        assert len(scored_runs) == 2
    assert r["operating_points"]["6/shift"]["spy_a"] == r["operating_points"]["6/shift"]["spy_b"]


def wobbly_risk(specs, context):
    """A fake TracerNet: a risk that rises with the share of the window passed but halves on every other tick."""
    out = {}
    for s in specs:
        t = s.ticks
        base = np.minimum(1.0, t / s.window) * 0.8
        out[s.specimen_id] = base * np.where(np.arange(len(t)) % 2 == 1, 0.5, 1.0)
    return out


def test_a_non_monotone_tracernet_gets_its_running_maximum_before_alerts(v2_store):
    fake = ne.RiskMethod(ne.TRACERNET, wobbly_risk, monotone=False)
    r = ne.evaluate_split("val", ne.baselines() + [fake], store_dir=v2_store, workers=1, keep_alerts=True,
                          write=False, matched=True)
    weights = triage.weight_schemes(triage.load())["default"]
    differed = 0
    for g in r["detail"]["groups"]:
        history, scored = specs_of(v2_store, g["key"], "history"), specs_of(v2_store, g["key"], "scored")
        harm = triage.harm_series(history + scored, wobbly_risk(history + scored, {}), weights)
        knob, _, _ = g["knobs"]["6/shift"]["tracernet|3-2-1"]
        assert knob == ne.tune_knob(lambda v: ne.model_alerts(history, harm, v), history, 21.0, 6, 0.0, 3.0)
        got = g["alerts"]["6/shift"]["tracernet|3-2-1"]
        assert got == ne.model_alerts(scored, harm, knob)           # the first crossing of the running maximum
        raw = evaluate.model_alerts(scored, harm, knob)             # searchsorted on the falling series
        differed += sum(got[k] != raw[k] for k in got)
    assert differed > 0
    assert set(r["differences"]["6/shift"]["3-2-1"]) == {"analytic_global", "analytic_local", "window_rule",
                                                          "quantile_local", "quantile_global"}
    rule = r["decision_rule"]
    assert rule["binding"] is False and set(rule) >= {"R1", "R2", "R3", "R4", "verdict"}
    assert len(rule["R1"]["checks"]) == 6 and rule["R3"]["checks"][0]["tracernet_brier"] is not None
    assert "differences" in r["matched_volume"]
    falling = ne.RiskMethod("liar", wobbly_risk, monotone=True)
    with pytest.raises(ValueError, match="never falls"):
        ne.evaluate_split("val", [falling], store_dir=v2_store, workers=1, write=False)


def test_the_test_sets_are_scored_only_through_the_lock(v2_store, tmp_path):
    with pytest.raises(ne.LockError):
        ne.evaluate_split("test", store_dir=v2_store, workers=1, write=False)
    files = {"lock_path": tmp_path / "test_lock.json", "runs_path": tmp_path / "test_runs.jsonl"}
    methods = ne.baselines()
    hashes = ne.lock_record(v2_store, methods)
    r = ne.evaluate_split("test", methods, store_dir=v2_store, workers=1, write=False, lock_hashes=hashes, **files)
    assert r["counts"]["tuning_groups"] == 1 and json.loads(files["lock_path"].read_text())["store_sha256"] != "none"
    assert ne.lock_record(v2_store, methods) == hashes             # the same record after the methods were fitted
    with pytest.raises(ne.LockError):
        ne.evaluate_split("test", store_dir=v2_store, workers=1, write=False,
                          lock_hashes=hashes | {"configuration_sha256": "0" * 64}, **files)
    with pytest.raises(ne.LockError, match="locked split"):
        ne.evaluate_split("control_v1world", store_dir=v2_store, control_scenario="dev-22-normal-random", write=False)


def test_the_control_set_uses_replicates_as_clusters(v2_store):
    r = ne.evaluate_split("control_v1world", ne.baselines(), store_dir=v2_store, workers=1, write=False,
                          control_scenario="dev-07-weekend-050")
    assert r["settings"]["cluster"] == "run" and r["counts"]["clusters"] == 2 and r["counts"]["tuning_groups"] == 1


def test_the_pool_gives_the_result_of_one_process(v2_store, v2_run):
    pooled = ne.evaluate_split("val", ne.baselines(), store_dir=v2_store, workers=2, write=False)
    strip = lambda r: json.dumps({k: v for k, v in r.items() if k not in ("detail", "timing")}, sort_keys=True)
    assert strip(pooled) == strip(v2_run)


def test_the_written_json_has_every_section_and_no_arrays(v2_store, tmp_path):
    r = ne.evaluate_split("val", ne.baselines(), store_dir=v2_store, workers=1, out_dir=tmp_path)
    written = json.loads((tmp_path / "evaluation.json").read_text())
    assert "detail" not in written and written["notice"] == ne.NOTICE
    for key in ("counts", "settings", "methods", "local_fit", "knobs", "operating_points", "discrimination",
                "calibration", "slices", "timing", "store"):
        assert key in written, key
    assert sorted(written["operating_points"]) == sorted(b.key for b in ne.budget_list())
    six = written["operating_points"]["6/shift"]
    assert set(six) == {"analytic_global", "analytic_local", "window_rule", "quantile_local", "quantile_global"}
    assert set(six["analytic_global"]) == {"3-2-1", "equal", "9-3-1"} and set(six["window_rule"]) == {"none"}
    assert set(six["window_rule"]["none"]) == set(ne.METRICS)
    assert set(written["calibration"]) == {"analytic_global", "analytic_local"}
    assert set(written["slices"]) == set(ne.SLICE_DIMENSIONS_V2)
    assert written["timing"]["seconds_per_1000_specimens"] > 0
    assert (tmp_path / "evaluation.json").stat().st_size < 1_000_000
    assert ne.table(r).count("\n") > 10


def test_a_frame_risk_reads_a_risk_table(v2_store, tmp_path):
    import polars as pl

    specs = specs_of(v2_store, "dev-07-weekend-050", "scored")[:20]
    rows = [(s.specimen_id, int(t), float(t) / 1000) for s in specs for t in s.ticks]
    rows.append((specs[0].specimen_id, 99999, 0.5))                  # past the last tick: ignored
    pl.DataFrame(rows, schema=["specimen_id", "tick_min", "p_miss"], orient="row").write_parquet(tmp_path / "r.parquet")
    got = ne.FrameRisk(tmp_path / "r.parquet")(specs, {})
    assert all(np.array_equal(got[s.specimen_id], s.ticks / 1000) for s in specs)
    pl.DataFrame(rows[1:], schema=["specimen_id", "tick_min", "p_miss"], orient="row").write_parquet(tmp_path / "s.parquet")
    with pytest.raises(ValueError, match="lacks"):
        ne.FrameRisk(tmp_path / "s.parquet")(specs, {})
    assert ne.tracernet_method(tmp_path / "r.parquet").name == ne.TRACERNET


def test_a_pinned_frame_risk_reads_only_the_parts_that_can_hold_its_specimens(v2_store, tmp_path):
    import polars as pl

    specs = specs_of(v2_store, "dev-07-weekend-050", "scored")[:30]
    rows = [(s.specimen_id, int(t), float(t) / 1000) for s in specs for t in s.ticks]
    frame = pl.DataFrame(rows, schema=["specimen_id", "tick_min", "p_miss"], orient="row").sort("specimen_id")
    for i, part in enumerate(frame.iter_slices(n_rows=max(1, frame.height // 4))):
        part.write_parquet(tmp_path / f"risk-{i:05d}.parquet")
    risk = ne.FrameRisk(tmp_path / "risk-*.parquet")
    assert len(risk.pin()) == len(risk.ranges) >= 4
    got = risk(specs, {})
    assert all(np.array_equal(got[s.specimen_id], s.ticks / 1000) for s in specs)   # the same as a full read
    first = sorted(specs, key=lambda s: s.specimen_id)[:1]
    read = [f for f, lo, hi in risk.ranges if lo <= first[0].specimen_id <= hi]
    assert len(read) == 1 and np.array_equal(risk(first, {})[first[0].specimen_id], first[0].ticks / 1000)


def test_specimens_without_ticks_need_no_risk_rows_in_a_pinned_table(v2_store, tmp_path):
    from types import SimpleNamespace

    import polars as pl

    specs = specs_of(v2_store, "dev-07-weekend-050", "scored")[:5]
    rows = [(s.specimen_id, int(t), 0.5) for s in specs for t in s.ticks]
    pl.DataFrame(rows, schema=["specimen_id", "tick_min", "p_miss"], orient="row").write_parquet(tmp_path / "risk-0.parquet")
    risk = ne.FrameRisk(tmp_path / "risk-*.parquet")
    risk.pin()
    # received before the first tick: no rows, and an id outside every part's range
    early = SimpleNamespace(specimen_id="SP-r999999-000001", ticks=np.array([], dtype=np.int64))
    assert risk([early], {})["SP-r999999-000001"].size == 0
    got = risk([early, specs[1]], {})
    assert got["SP-r999999-000001"].size == 0 and np.array_equal(got[specs[1].specimen_id], np.full(len(specs[1].ticks), 0.5))


# ---------------------------------------------------------------------------------------------------------
# The integrity review's findings (F001 to F005): the lock, the record, the manifests, the knobs, R4
# ---------------------------------------------------------------------------------------------------------

def fake_risk_table(store_dir, split, path, scale=1.0):
    """A risk table for every tick of a split's history and scored specimens: the share of the window passed."""
    import polars as pl

    specs, _ = evaluate.load_specs_v2(split, ("history", "scored"), store_dir, labels=False)
    rows = [(s.specimen_id, int(t), min(1.0, scale * float(t) / s.window)) for s in specs for t in s.ticks]
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["specimen_id", "tick_min", "p_miss"], orient="row").write_parquet(path)
    return path


def lock_paths(tmp_path):
    return {"lock_path": tmp_path / "lock" / "test_lock.json", "runs_path": tmp_path / "lock" / "test_runs.jsonl"}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def logged(files):
    path = files["runs_path"]
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def fake_artifact(folder):
    """An artifact folder whose manifest lists its files with their SHA-256 (all lock_record reads)."""
    folder.mkdir(parents=True)
    (folder / "calibration.json").write_text('{"temperature": 1.0, "isotonic": null}\n')
    (folder / "model.pt").write_bytes(b"weights")
    files = {name: sha256(folder / name) for name in ("calibration.json", "model.pt")}
    (folder / "manifest.json").write_text(json.dumps({"files": files}))
    return folder


class FakePredictor:
    """Stands in for the artifact's predictor in risk_table: every tick of the asked runs, a fixed risk."""

    def __init__(self, store_dir, events, scale=1.0):
        self.store_dir, self.events, self.scale, self.fail = store_dir, events, scale, False

    def score(self, store_dir, runs, *, workers=None, test_run=None, history=None):
        import polars as pl

        self.events.append("scored")
        if self.fail:
            raise RuntimeError("interrupted")
        specs, _ = evaluate.load_specs_v2("test", ("history", "scored"), store_dir, labels=False)
        rows = [(s.specimen_id, s.run, s.scenario_id, s.replicate, int(t), min(1.0, self.scale * float(t) / s.window))
                for s in specs if s.run in set(runs) for t in s.ticks]
        df = pl.DataFrame(rows, schema=["specimen_id", "run", "scenario_id", "replicate", "tick_min", "p_miss"],
                          orient="row")
        return df.with_columns(harm_default=pl.col("p_miss"), harm_equal=pl.col("p_miss"), ensemble_sd=pl.lit(0.0))


def test_f001_risk_table_refuses_a_locked_split_without_the_run_the_lock_recorded(v2_store, tmp_path):
    """F001: TracerNet's test predictions are computed only under a recorded lock; a plain mapping or another
    split's run is not one."""
    for split in ne.LOCKED_SPLITS:
        with pytest.raises(ne.LockError, match="test lock"):
            ne.risk_table(split, tmp_path / "no-artifact", store_dir=v2_store, out_dir=tmp_path / "risk")
    files = lock_paths(tmp_path)
    run = ne.guard_test_run("test_ood", ne.lock_record(v2_store, ne.baselines()), **files)
    with pytest.raises(ne.LockError, match="test_ood, not test"):
        ne.risk_table("test", tmp_path / "no-artifact", store_dir=v2_store, out_dir=tmp_path / "risk", test_run=run)
    with pytest.raises(ne.LockError, match="test lock"):
        ne.risk_table("test", tmp_path / "no-artifact", store_dir=v2_store, out_dir=tmp_path / "risk",
                      test_run=dict(run) | {"split": "test"})
    assert not (tmp_path / "risk").exists()


def test_f001_the_lock_is_taken_and_the_run_recorded_before_the_artifact_scores_a_test_set(v2_store, tmp_path,
                                                                                            monkeypatch):
    """F001: `evaluate --split test --artifact` takes the lock and records the run before any test inference, an
    interrupted first look still leaves its run recorded, and the evaluation reuses that run. F002: the parts the
    artifact wrote are recorded once written, and a later look whose table differs refuses."""
    from src.models.tracernet import predictor as P

    files = lock_paths(tmp_path)
    monkeypatch.setattr(ne, "LOCK_PATH", files["lock_path"])
    monkeypatch.setattr(ne, "TEST_RUNS_PATH", files["runs_path"])
    monkeypatch.setattr(ne, "RISK_DIR", tmp_path / "risk")
    events = []
    fake = FakePredictor(v2_store, events)
    monkeypatch.setattr(P.Predictor, "load", classmethod(lambda cls, path, device="cpu": fake))
    real_guard = ne.guard_test_run

    def guard(*a, **k):
        run = real_guard(*a, **k)
        events.append("locked")
        return run
    monkeypatch.setattr(ne, "guard_test_run", guard)
    artifact = fake_artifact(tmp_path / "artifact")
    command = lambda: ne.command_evaluate("test", store_dir=v2_store, out_dir=tmp_path / "out", workers=1,  # noqa: E731
                                          artifact_dir=artifact)

    fake.fail = True                     # the first look is interrupted while the artifact scores the set
    with pytest.raises(RuntimeError, match="interrupted"):
        command()
    assert events == ["locked", "scored"]
    assert files["lock_path"].exists() and [e["status"] for e in logged(files)] == ["first"]

    fake.fail = False
    events.clear()
    assert command() == 0
    assert events == ["locked", "scored"]
    log = logged(files)
    assert [e["status"] for e in log] == ["first", "repeat", "risk table recorded"]   # one record per look
    parts = json.loads(files["lock_path"].read_text())["risk_parts"]["test"][ne.TRACERNET]
    assert parts == log[-1]["risk_parts"][ne.TRACERNET] and set(parts) == {"part-0000.parquet"}
    assert json.loads((tmp_path / "out" / "evaluation.json").read_text())["decision_rule"]["set"] == "test"

    fake.scale = 0.5                     # the same artifact hashes, but other predictions
    with pytest.raises(ne.LockError, match="risk table of tracernet on test differs"):
        command()
    assert json.loads(files["lock_path"].read_text())["risk_parts"]["test"][ne.TRACERNET] == parts


def test_f001_evaluate_split_reuses_the_recorded_run_and_refuses_a_stale_or_forged_one(v2_store, tmp_path):
    """F001: evaluate_split reuses the run guard_test_run recorded rather than recording a second one, and only
    a run the current lock still holds lets it go ahead."""
    files = lock_paths(tmp_path)
    methods = ne.baselines()
    hashes = ne.lock_record(v2_store, methods)
    run = ne.guard_test_run("test", hashes, **files)
    ne.evaluate_split("test", methods, store_dir=v2_store, workers=1, write=False, test_run=run)
    assert [e["status"] for e in logged(files)] == ["first"]
    with pytest.raises(ne.LockError, match="test lock"):
        ne.evaluate_split("test", methods, store_dir=v2_store, workers=1, write=False, test_run=dict(run))
    ne.guard_test_run("test", hashes | {"artifact_sha256": "f" * 64}, relock_reason="a new artifact", **files)
    with pytest.raises(ne.LockError, match="changed since this run was recorded"):
        ne.evaluate_split("test", methods, store_dir=v2_store, workers=1, write=False, test_run=run)


def test_f002_two_different_risk_tables_give_different_records(v2_store, tmp_path):
    """F002: the record of a test look covers the risk table it reads. Two tables with the same file name and
    different predictions give different records, and a later look with the other table refuses without a
    relock reason (nothing logged), and is recorded as a relock with one."""
    a = fake_risk_table(v2_store, "test", tmp_path / "a" / "risk.parquet", 1.0)
    b = fake_risk_table(v2_store, "test", tmp_path / "b" / "risk.parquet", 0.9)
    with_a, with_b = (ne.baselines() + [ne.tracernet_method(t)] for t in (a, b))
    files = lock_paths(tmp_path / "a")
    ne.evaluate_split("test", with_a, store_dir=v2_store, workers=1, write=False,
                      lock_hashes=ne.lock_record(v2_store, with_a), **files)
    before = (files["lock_path"].read_text(), len(logged(files)))
    with pytest.raises(ne.LockError, match="risk_parts"):
        ne.evaluate_split("test", with_b, store_dir=v2_store, workers=1, write=False,
                          lock_hashes=ne.lock_record(v2_store, with_b), **files)
    assert (files["lock_path"].read_text(), len(logged(files))) == before

    records = {"a": json.loads(files["lock_path"].read_text())["risk_parts"]["test"][ne.TRACERNET]}
    fresh = lock_paths(tmp_path / "b")
    ne.evaluate_split("test", with_b, store_dir=v2_store, workers=1, write=False,
                      lock_hashes=ne.lock_record(v2_store, with_b), **fresh)
    records["b"] = json.loads(fresh["lock_path"].read_text())["risk_parts"]["test"][ne.TRACERNET]
    assert records["a"] != records["b"] and set(records["a"]) == set(records["b"]) == {"risk.parquet"}
    assert records["a"]["risk.parquet"] == sha256(a)

    ne.evaluate_split("test", with_b, store_dir=v2_store, workers=1, write=False, relock_reason="new predictions",
                      lock_hashes=ne.lock_record(v2_store, with_b), **files)
    assert [e["status"] for e in logged(files)] == ["first", "risk table recorded", "relock", "risk table recorded"]
    assert json.loads(files["lock_path"].read_text())["risk_parts"]["test"][ne.TRACERNET] == records["b"]


def test_f002_the_record_covers_the_weights_the_training_store_the_budgets_and_the_bootstrap(v2_store, tmp_path,
                                                                                            monkeypatch):
    """F002: every input that can change a reported number changes the configuration hash, and evaluate_split
    refuses a record built for other arguments than its own."""
    methods = ne.baselines()
    base = ne.lock_record(v2_store, methods)
    real = triage.weight_schemes
    with monkeypatch.context() as patch:
        patch.setattr(triage, "weight_schemes", lambda cfg: {k: {**v, 1: 5.0} if k == "default" else v
                                                            for k, v in real(cfg).items()})
        variants = {"weights": ne.lock_record(v2_store, methods)}
    assert variants["weights"]["configuration_sha256"] != base["configuration_sha256"]
    assert ne.lock_record(v2_store, methods, train_store_dir=v2_store) == base
    other_train = tmp_path / "train-store"
    shutil.copytree(v2_store, other_train)
    m = json.loads((other_train / store.STORE_V2_MANIFEST).read_text())
    m["inputs"]["splits_sha256"] = "0" * 64
    (other_train / store.STORE_V2_MANIFEST).write_text(json.dumps(m))
    variants |= {"train store": ne.lock_record(v2_store, methods, train_store_dir=other_train),
                 "budgets": ne.lock_record(v2_store, methods, budgets=(6,)),
                 "per 100": ne.lock_record(v2_store, methods, per_100_budgets=(10,)),
                 "bootstrap": ne.lock_record(v2_store, methods, resamples=100),
                 "matched": ne.lock_record(v2_store, methods, matched=True)}
    for name, record in variants.items():
        assert record["configuration_sha256"] != base["configuration_sha256"], name
        assert {k: v for k, v in record.items() if k != "configuration_sha256"} == \
            {k: v for k, v in base.items() if k != "configuration_sha256"}, name
    files = lock_paths(tmp_path)
    for kwargs in ({"resamples": 100}, {"budgets": (6,)}, {"train_store_dir": other_train}):
        with pytest.raises(ne.LockError, match="does not describe this evaluation"):
            ne.evaluate_split("test", methods, store_dir=v2_store, workers=1, write=False, lock_hashes=base,
                              **kwargs, **files)
    assert logged(files) == []


def test_f003_a_store_file_that_differs_from_its_manifest_refuses_before_the_manifest_is_an_identity(v2_store,
                                                                                                    tmp_path):
    """F003: the store's manifest hash stands for the store only once both database files match the SHA-256 it
    records; a changed or missing file refuses the lock record and the evaluation."""
    copy = tmp_path / "store"
    shutil.copytree(v2_store, copy)
    assert ne.lock_record(copy, ne.baselines())["store_sha256"] == ne.lock_record(v2_store, ne.baselines())["store_sha256"]
    with (copy / store.OBSERVED_NAME).open("ab") as f:
        f.write(b"\0")
    with pytest.raises(RuntimeError, match="does not match the SHA-256"):
        ne.lock_record(copy, ne.baselines())
    with pytest.raises(RuntimeError, match="does not match the SHA-256"):
        ne.evaluate_split("val", ne.baselines(), store_dir=copy, workers=1, write=False)
    gone = tmp_path / "gone"
    shutil.copytree(v2_store, gone)
    (gone / store.TRUTH_NAME).unlink()
    with pytest.raises(RuntimeError, match="missing"):
        store.verified_store_v2_manifest(gone)
    bare = tmp_path / "bare"
    shutil.copytree(v2_store, bare)
    m = json.loads((bare / store.STORE_V2_MANIFEST).read_text())
    (bare / store.STORE_V2_MANIFEST).write_text(json.dumps({k: v for k, v in m.items() if k != "outputs"}))
    with pytest.raises(RuntimeError, match="records no SHA-256"):
        ne.lock_record(bare, ne.baselines())


def test_f003_the_lock_record_refuses_an_artifact_whose_files_differ_from_its_manifest(v2_store, tmp_path):
    """F003: the artifact's manifest hash is its identity only once every file it lists matches."""
    artifact = fake_artifact(tmp_path / "artifact")
    good = ne.lock_record(v2_store, ne.baselines(), artifact_dir=artifact)
    assert good["calibration_sha256"] == sha256(artifact / "calibration.json")
    (artifact / "model.pt").write_bytes(b"other weights")
    with pytest.raises(RuntimeError, match="model.pt .* does not match"):
        ne.lock_record(v2_store, ne.baselines(), artifact_dir=artifact)
    (artifact / "model.pt").unlink()
    with pytest.raises(RuntimeError, match="missing"):
        ne.lock_record(v2_store, ne.baselines(), artifact_dir=artifact)
    with pytest.raises(RuntimeError, match="no artifact manifest"):
        ne.lock_record(v2_store, ne.baselines(), artifact_dir=tmp_path / "nowhere")


def saturating(specs, context):
    """A risk of 1 at every tick: every specimen reaches the largest harm its tier allows."""
    return {s.specimen_id: np.ones(len(s.ticks)) for s in specs}


def test_f004_where_no_knob_that_alerts_meets_the_budget_the_v2_knob_alerts_on_nothing():
    """F004: with 100 history specimens that all reach the top of every method's score, no knob that alerts
    meets 1 alert per shift (21 shifts) or 10 per 100 specimens. On the v2 sets each method then takes its
    no-alert knob, alerts on nothing on the history or the scored set, and the group is counted; legacy_v1 keeps
    evaluate.py's knob, as the committed results need."""
    history = [make_spec(f"h{i}", run=0, receipt=None, window=60, tier=1) for i in range(100)]
    scored = [make_spec(f"s{i}", run=1, receipt=None, window=60, tier=1) for i in range(50)]
    group = ne._group("g", history, scored, True, {"set": "val", "store_dir": "", "scenario_id": "g"})
    quantile = ne.QuantileRule("quantile_global", "global")
    quantile.quantiles = {"g": np.array([10.0, 20.0, 30.0, 40.0, 50.0])}
    methods = [ne.WindowRule(), quantile, ne.RiskMethod("saturating", saturating, monotone=True)]
    env = ne.Env(set_name="val", kind="v2", store_split="val", store_dir="", by_run=True,
                 schemes=(("3-2-1", {1: 3.0, 2: 2.0, 3: 1.0}), ("equal", {1: 1.0, 2: 1.0, 3: 1.0})),
                 budgets=tuple(ne.budget_list((1,), (10,))), headline=None, slice_dimensions=(), matched=False,
                 keep_alerts=True)
    out = ne.evaluate_group(group, methods, env)
    keys = ("window_rule|none", "quantile_global|none", "saturating|3-2-1", "saturating|equal")
    for budget in ("1/shift", "10/100"):
        for key in keys:
            knob, on_history, on_scored = out["knobs"][budget][key]
            assert on_history == 0 and on_scored == 0 and out["no_alert"][budget][key], (budget, key)
            assert all(v is None for v in out["alerts"][budget][key].values())
    assert out["knobs"]["1/shift"]["saturating|3-2-1"][0] == np.nextafter(3.0, np.inf)
    assert out["knobs"]["1/shift"]["window_rule|none"][0] == np.nextafter(1.0, np.inf)

    legacy = ne.evaluate_group(group, methods, ne.Env(**{**env.__dict__, "kind": "legacy"}))
    for key, top in zip(keys, (1.0, 0.9999, 3.0, 1.0)):
        knob, on_history, _ = legacy["knobs"]["1/shift"][key]
        assert knob == top and on_history == pytest.approx(100 / 21) and not legacy["no_alert"]["1/shift"][key]

    rule = lambda specs, v: triage.window_share_alerts(specs, v)   # noqa: E731 - the per-specimen reference
    assert ne.tune_knob(lambda v: rule(history, v), history, 21, 1, 0.0, 1.0) == 1.0
    assert ne.tune_knob(lambda v: rule(history, v), history, 21, 1, 0.0, 1.0,
                        no_alert=float(np.nextafter(1.0, np.inf))) == np.nextafter(1.0, np.inf)


def test_f004_a_saturating_risk_on_the_v2_store_alerts_on_nothing_and_the_json_counts_it(v2_store, tmp_path):
    """F004: through the driver, a risk at 1 everywhere gives every validation scenario more history alerts than
    6 per shift at any knob that alerts under equal weights, so both scenarios get the no-alert knob, alert on
    nothing, and the written evaluation counts them per method and budget."""
    r = ne.evaluate_split("val", [ne.RiskMethod("saturating", saturating, monotone=True)], store_dir=v2_store,
                          workers=1, out_dir=tmp_path)
    for g in r["detail"]["groups"]:
        for b in ne.budget_list():
            knob, on_history, on_scored = g["knobs"][b.key]["saturating|equal"]
            assert knob > 1.0 and on_history == 0 and on_scored == 0, (g["key"], b.key)
    written = json.loads((tmp_path / "evaluation.json").read_text())
    assert written["knobs"]["6/shift"]["saturating"]["equal"]["no_alert_groups"] == 2
    assert written["knobs"]["6/shift"]["saturating"]["equal"]["over_budget"] == 0
    assert written["operating_points"]["6/shift"]["saturating"]["equal"]["alerts"]["point"] == 0
    assert "no_alert_groups" in written["settings"]["knob"]


def history_heavy(specs, context):
    """A fake TracerNet whose risk on the scored replicates is a fifth of its risk on the history: the knob set on
    the history alerts far less on the scored set than any baseline's, so R4 fails."""
    scale = 1.0 if context["role"] == "history" else 0.2
    return {s.specimen_id: np.minimum(1.0, s.ticks / s.window) * scale for s in specs}


def test_f005_an_r4_failure_computes_the_matched_volume_sensitivity_without_being_asked(v2_store, v2_run):
    """F005: when R4 fails (unequal realized alerts), R1 is repeated at matched realized volume automatically,
    reported apart from the verdict, which R1 to R3 alone decide."""
    fake = ne.RiskMethod(ne.TRACERNET, history_heavy, monotone=True)
    r = ne.evaluate_split("val", ne.baselines() + [fake], store_dir=v2_store, workers=1, write=False)
    rule = r["decision_rule"]
    assert rule["R4"]["held"] is False and rule["R4"]["unequal"]
    sensitivity = rule["R4"]["r1_at_matched_volume"]
    assert sensitivity is not None and len(sensitivity["checks"]) == 6
    assert r["matched_volume"]["trigger"].startswith("R4 failed") and r["matched_volume"]["budget"] == "6/shift"
    matched_tn = r["matched_volume"]["operating_point"][ne.TRACERNET]["3-2-1"]["realized_alerts_per_shift"]["point"]
    headline_tn = r["operating_points"]["6/shift"][ne.TRACERNET]["3-2-1"]["realized_alerts_per_shift"]["point"]
    assert matched_tn > headline_tn and matched_tn <= 6
    assert rule["verdict"]["tracernet_primary"] == (rule["R1"]["held"] and rule["R2"]["held"] and rule["R3"]["held"])
    assert "matched_volume" not in v2_run          # no TracerNet, no rule, nothing asked: no sensitivity


def test_r2_the_risk_column_a_method_reads_is_part_of_the_lock(v2_store, tmp_path):
    """Re-review R2: two TracerNet methods reading different columns of the same risk table give different
    configuration hashes, so changing only the column needs a relock."""
    path = tmp_path / "risk.parquet"
    a = ne.lock_record(v2_store, ne.baselines() + [ne.tracernet_method(path, "p_miss")])
    b = ne.lock_record(v2_store, ne.baselines() + [ne.tracernet_method(path, "ensemble_sd")])
    assert a["configuration_sha256"] != b["configuration_sha256"]


def test_r3_a_separate_training_store_without_a_manifest_is_refused(v2_store, tmp_path):
    """Re-review R3: a training store with no manifest has no identity for the lock, so it is refused."""
    other_train = tmp_path / "train-store"
    shutil.copytree(v2_store, other_train)
    (other_train / store.STORE_V2_MANIFEST).unlink()
    with pytest.raises(RuntimeError, match="no manifest"):
        ne.lock_record(v2_store, ne.baselines(), train_store_dir=other_train)


def test_weakspot_rows_and_scenario_cells_read_validation_and_train_only(v2_store, tmp_path):
    """The exports src/neural/weakspots.py and the ablations read: validation rows only, and the train and
    validation scenarios' cells only, never a test split."""
    rows = ne.weakspot_rows(v2_store)
    specs, _ = evaluate.load_specs_v2("val", ("scored",), v2_store, labels=True)
    assert rows.height == len(specs) and set(rows["scenario_id"]) == {s.scenario_id for s in specs}
    assert rows["breach"].sum() == sum(s.breach for s in specs)
    cells = json.loads(ne.write_scenario_cells(tmp_path / "cells.json", v2_store).read_text())
    assert cells and {c["split"] for c in cells.values()} <= {"train", "val"}
    assert ne.write_weakspot_rows(tmp_path / "rows.parquet", v2_store).exists()
