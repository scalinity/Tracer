import json
import math
import random
import re
import shutil
import subprocess

import pytest
from scipy import integrate

from src.generator import config
from src.models import risk
from src.models.risk import Mixture
from src.pipeline import intervals, recovery
from test_truth_isolation import touches_truth

ARCHITECTURE = intervals.ROOT / "visuals" / "architecture.html"

# The illustrative parameters in visuals/architecture.html: PARAM.mix, lossFraction, windowMin, receivedAt.
JS_MIX = Mixture(((0.88, math.log(24), 0.50), (0.08, math.log(70), 0.60)), 0.04)
JS_WINDOW, JS_RECEIVED = 60, 52


def js_page(change=None, times=()):
    """The page's own risk, run by node: riskUnreceived at the given times, the scenario's RISK curve
    (every whole minute) and its alert minute. `change` overrides PARAM fields first."""
    html = ARCHITECTURE.read_text(encoding="utf-8")
    model = html[html.index("const PARAM = {"):html.index("const MAXT = ")]
    rest = html[html.index("const MAXT = "):html.index("const hhmm")]
    script = (model + f"\nObject.assign(PARAM, {json.dumps(change or {})});\n" + rest
              + f"\nconsole.log(JSON.stringify({{at: {json.dumps(list(times))}.map(riskUnreceived), risk: RISK, alert: ALERT_T}}));")
    return json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)


def scenario_risk(t, mix=JS_MIX, window=JS_WINDOW, received=JS_RECEIVED):
    """The same scenario in Python: the model while silent, the known outcome after the receipt scan."""
    if t < received:
        return risk.risk_unreceived(t, mix, window)
    return 0.0 if received <= window else 1.0


# ---- parity with the JavaScript ----

@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_python_agrees_with_the_javascript_to_1e_6_on_50_states_before_the_scan():
    rng = random.Random(0)
    times = [0.0] + [round(rng.uniform(0, 110), 2) for _ in range(49)]
    js = js_page(times=times)["at"]
    py = [risk.risk_unreceived(t, JS_MIX, JS_WINDOW) for t in times]
    worst = max(abs(a - b) for a, b in zip(js, py))
    assert worst < 1e-6, worst
    assert max(js) > 0.9 and min(js) < 0.2  # the 50 states cover a real range of risk


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_python_agrees_with_the_javascript_at_every_minute_of_the_scenario_including_after_the_scan():
    js = js_page()["risk"]
    py = [scenario_risk(t) for t in range(len(js))]
    assert len(js) == 91 and max(abs(a - b) for a, b in zip(js, py)) < 1e-6
    assert js[JS_RECEIVED - 1] > 0.7 and js[JS_RECEIVED] == 0 and js[-1] == 0  # the scan resolves it


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("window, received", [(60, 52), (60, 70), (90, 52), (120, 68)])
def test_parity_holds_for_other_windows_and_for_a_scan_that_comes_too_late(window, received):
    js = js_page({"windowMin": window, "receivedAt": received})["risk"]
    py = [scenario_risk(t, window=window, received=received) for t in range(len(js))]
    assert max(abs(a - b) for a, b in zip(js, py)) < 1e-6
    assert js[-1] == (0 if received <= window else 1)  # a scan after the window closed is a miss


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_alert_minute_is_the_first_five_minute_tick_at_the_operating_point():
    out = js_page()
    first = next(t for t in range(0, JS_RECEIVED, 5) if risk.risk_unreceived(t, JS_MIX, JS_WINDOW) >= 0.45)
    assert out["alert"] == first == 40


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_javascript_parameters_are_the_ones_used_here():
    html = ARCHITECTURE.read_text(encoding="utf-8")
    assert re.search(r"\{w:0\.88, m:24, s:0\.50", html) and re.search(r"\{w:0\.08, m:70, s:0\.60", html)
    assert "lossFraction:  0.04" in html and "windowMin:     60," in html and "receivedAt:    52," in html
    assert "setupMedian" not in html and "riskReceived" not in html  # the clock stops at receipt: no step after it


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_risk_never_falls_while_silent():
    js = js_page()["risk"][:JS_RECEIVED]
    assert all(b >= a for a, b in zip(js, js[1:]))


# ---- the closed form under the receipt clock ----

def test_closed_form_matches_numerical_integration():
    mix, W = Mixture(((0.9, math.log(22), 0.45), (0.07, math.log(90), 0.5)), 0.03), 60
    for t in (0.5, 10, 25, 40, 55):
        pdf = lambda u: sum(w * math.exp(-((math.log(u) - mu) ** 2) / (2 * s * s)) / (u * s * math.sqrt(2 * math.pi))
                            for w, mu, s in mix.components)
        arrives = integrate.quad(pdf, t, W)[0]
        assert risk.risk_unreceived(t, mix, W) == pytest.approx(1 - arrives / risk.p_not_received(t, mix), abs=1e-9)


def test_before_anything_is_known_the_risk_is_the_unconditional_breach_chance():
    mix, W = JS_MIX, 60
    expected = 1 - sum(w * risk.ln_cdf(W, mu, s) for w, mu, s in mix.components)
    assert risk.risk_unreceived(0, mix, W) == pytest.approx(expected)


def test_silence_raises_the_risk_and_a_passed_window_is_certain():
    mix = Mixture(((0.93, math.log(22), 0.45), (0.05, math.log(90), 0.5)), 0.02)
    series = [risk.risk_unreceived(t, mix, 120) for t in range(0, 120, 5)]
    assert all(b >= a - 1e-12 for a, b in zip(series, series[1:]))
    assert series[0] < 0.1 < series[-1]
    assert risk.risk_unreceived(120, mix, 120) == 1.0 and risk.risk_unreceived(500, mix, 120) == 1.0


def test_with_no_failure_mass_the_risk_is_driven_only_by_the_tail():
    clean = Mixture(((1.0, math.log(20), 0.3),), 0.0)
    assert risk.risk_unreceived(10, clean, 120) < 1e-6
    assert risk.risk_unreceived(60, clean, 120) < 1e-3


def test_weights_that_do_not_add_up_are_refused():
    with pytest.raises(ValueError, match="add up"):
        Mixture(((0.5, 0.0, 1.0),), 0.1)


# ---- the fitted model ----

@pytest.fixture(scope="module")
def params():
    return intervals.build()


def test_the_fitted_mixture_has_the_pooled_weights(params):
    mix = risk.mixture_from(params, "BAL, room temp | 06-11 | routine")
    assert mix.p_never == pytest.approx(params["pooled"]["weights"]["never"])
    assert sum(w for w, _, _ in mix.components) + mix.p_never == pytest.approx(1)


def test_a_one_hour_specimen_is_riskier_than_a_day_long_one_at_the_same_silence(params):
    policy = config.load()["policy"]
    _, urine = config.resolve(policy, "urine, unpreserved, room temp", ["culture"])
    _, swab = config.resolve(policy, "culture swab (BBL, Stuart's)", ["bacterial culture"])
    group_u, group_s = "urine, unpreserved, room temp | 06-11 | routine", "culture swab (BBL, Stuart's) | 06-11 | routine"
    short = risk.risk_unreceived(40, risk.mixture_from(params, group_u), urine["window_min"])
    long_ = risk.risk_unreceived(40, risk.mixture_from(params, group_s), swab["window_min"])
    assert urine["window_min"] == 60 and swab["window_min"] == 1440
    # With a day to spare, only a lost specimen misses the window: the never-arrive share over the chance of
    # still being unreceived at 40 minutes.
    mix = risk.mixture_from(params, group_s)
    assert long_ == pytest.approx(mix.p_never / risk.p_not_received(40, mix), abs=1e-3)
    assert short > 0.4 and short > 3 * long_


# ---- the model never touches the truth ----

def test_the_risk_module_does_not_refer_to_the_truth():
    assert touches_truth((intervals.ROOT / "src" / "models" / "risk.py").read_text(encoding="utf-8")) == []


# ---- parameter recovery ----

@pytest.fixture(scope="module")
def recovered(params):
    return recovery.recover(params)


def test_the_tail_where_alerts_act_recovers_within_three_standard_errors(recovered):
    assert [p["minutes"] for p in recovered["survival_at"]] == [30, 45, 60, 90, 120, 180]
    for p in recovered["survival_at"]:
        assert p["within_bar"], p
        assert abs(p["standard_errors"]) < 3


def test_the_never_arrive_share_recovers(recovered):
    n = recovered["never_arrive"]
    assert n["within_bar"] and n["planted"] == pytest.approx(0.0178, abs=0.001)


def test_the_body_mismatch_is_reported_not_hidden(recovered):
    body = recovered["body_before_30_min"]
    assert 0.01 < abs(body["largest_gap"]) < 0.05 and body["at_minutes"] < 30


def test_the_recovery_never_opens_the_truth_file(monkeypatch, params):
    from src.pipeline import store

    def refuse(*a, **k):
        raise AssertionError("opened the truth file")
    monkeypatch.setattr(store, "open_truth", refuse)
    assert recovery.recover(params)["fit_size"] == params["pooled"]["n_started"]


def test_the_plot_is_a_valid_svg_with_both_curves(recovered):
    svg = recovery.svg(recovered)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>") and svg.count("<polyline") == 2
    assert "planted by the generator" in svg and "fitted from the data" in svg


def test_main_writes_the_numbers_and_the_plot(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(recovery, "RESULTS", tmp_path)
    assert recovery.main() == 0
    assert (tmp_path / "parameter-recovery.svg").exists()
    assert json.loads((tmp_path / "parameter-recovery.json").read_text())["survival_at"][0]["minutes"] == 30
    assert "never arrive: planted" in capsys.readouterr().out
