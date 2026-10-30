import json
import math
import re
import shutil
import subprocess

import numpy as np
import pytest
from scipy.special import ndtr

from src.generator import config
from src.models import location
from src.models.location import Leg, RECEIVED
from src.pipeline import intervals

ARCHITECTURE = intervals.ROOT / "visuals" / "architecture.html"

# The default legs in visuals/architecture.html (PARAM.legs): name, median, sigma, exits.
JS_LEGS = [
    Leg("Awaiting dispatch", 6, 0.60, ((1, 0.992), ("lost", 0.008))),
    Leg("In the tube", 4, 0.25, ((2, 0.995), ("lost", 0.005))),
    Leg("At the station", 11, 0.70, ((RECEIVED, 0.910), (3, 0.070), ("lost", 0.020))),
    Leg("Stalled", 45, 0.80, ((RECEIVED, 0.850), ("lost", 0.150))),
]
GROUP = "urine, unpreserved, room temp | 06-11 | routine"


@pytest.fixture(scope="module")
def params():
    return json.loads(intervals.PARAMS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def gen():
    return config.load()["config"]


def js_location_series(max_t):
    """locationSeries from the real page, evaluated by node: one row per minute, the four legs then lost."""
    html = ARCHITECTURE.read_text(encoding="utf-8")
    script = (html[html.index("const PARAM = {"):html.index("const MAXT = ")]
              + f"\nconsole.log(JSON.stringify(locationSeries({max_t}).map(s => [...s.legs, s.lost])));")
    return np.array(json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout))


# ---- parity with the JavaScript ----

def page_normal_cdf(z):
    """normCdf from the page: Abramowitz and Stegun 7.1.26, good to 1.5e-7."""
    x = np.abs(z / math.sqrt(2))
    t = 1 / (1 + 0.3275911 * x)
    y = 1 - ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * np.exp(-x * x)
    return 0.5 * (1 + np.where(z < 0, -1.0, 1.0) * y)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_python_is_the_same_filter_as_the_javascript_given_the_pages_own_normal_cdf(monkeypatch):
    monkeypatch.setattr(location, "ndtr", page_normal_cdf)
    js = js_location_series(130)
    names, py = location.filter_series(JS_LEGS, 130)
    assert names == [leg.name for leg in JS_LEGS] + ["lost"]
    assert py.shape == js.shape == (131, 5)
    assert np.abs(py - js).max() < 1e-9
    assert js[:, 3].max() > 0.1 and js[:, 4].max() > 0.1  # the stalled and lost places both carry real mass


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_with_the_exact_normal_cdf_the_two_agree_to_5e_6_the_pages_own_error_amplified_by_the_hazards():
    js = js_location_series(130)
    _, py = location.filter_series(JS_LEGS, 130)
    assert np.abs(py - js).max() < 5e-6


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_javascript_legs_are_the_ones_used_here():
    html = ARCHITECTURE.read_text(encoding="utf-8")
    for needle in ('{name:"Awaiting dispatch", median:6,  sigma:0.60, exit:[["leg",1,0.992],["lost",0,0.008]]}',
                   '{name:"In the tube",       median:4,  sigma:0.25, exit:[["leg",2,0.995],["lost",0,0.005]]}',
                   '{name:"At the station",    median:11, sigma:0.70, exit:[["recv",0,0.910],["leg",3,0.070],["lost",0,0.020]]}',
                   '{name:"Stalled",           median:45, sigma:0.80, exit:[["recv",0,0.850],["lost",0,0.150]]}'):
        assert needle in html
    assert re.search(r"CAP = 180", html)


def test_the_posterior_sums_to_one_at_every_minute():
    _, post = location.filter_series(JS_LEGS, 130)
    assert np.abs(post.sum(axis=1) - 1).max() < 1e-12 and post.min() >= 0


# ---- what the filter does ----

def test_at_collection_the_specimen_is_at_the_unit():
    names, post = location.filter_series(JS_LEGS, 10)
    assert post[0].tolist() == [1, 0, 0, 0, 0]


def test_a_specimen_that_can_only_be_received_is_still_in_its_leg_given_silence():
    legs = [Leg("only", 10, 0.5, ((RECEIVED, 1.0),))]
    _, post = location.filter_series(legs, 60)
    assert post[:, 0].tolist() == pytest.approx([1.0] * 61)


def test_one_leg_with_a_loss_exit_matches_the_closed_form():
    m, s, q = 12.0, 0.6, 0.05
    legs = [Leg("here", m, s, ((RECEIVED, 1 - q), ("lost", q)))]
    _, post = location.filter_series(legs, 90)
    for t in (5, 20, 45, 90):
        F = float(ndtr(math.log(t / m) / s))
        assert post[t, 1] == pytest.approx(q * F / ((1 - F) + q * F), abs=1e-9)
        assert post[t, 0] == pytest.approx((1 - F) / ((1 - F) + q * F), abs=1e-9)


def test_the_share_that_is_lost_only_rises_with_silence():
    _, post = location.filter_series(JS_LEGS, 130)
    lost = post[:, 4]
    assert (np.diff(lost) >= -1e-12).all() and lost[-1] > lost[10]


def test_exit_probabilities_must_add_up_to_one():
    with pytest.raises(ValueError, match="add up to"):
        Leg("a", 5, 0.5, ((RECEIVED, 0.9),))


def test_a_leg_needs_a_positive_median_and_spread():
    with pytest.raises(ValueError, match="positive"):
        Leg("a", 0, 0.5, ((RECEIVED, 1.0),))
    with pytest.raises(ValueError, match="positive"):
        Leg("a", 5, -1, ((RECEIVED, 1.0),))


def test_hazards_start_at_zero_for_a_leg_that_cannot_finish_at_once():
    h = location.hazards(Leg("a", 30, 0.2, ((RECEIVED, 1.0),)))
    assert h[0] < 1e-9 and h.max() <= 1 and len(h) == location.CAP - 1


# ---- the legs built from the fit ----

@pytest.mark.parametrize("route", ["tube", "hand_carry"])
def test_the_real_legs_give_a_posterior_that_sums_to_one_at_every_minute(params, gen, route):
    legs = location.legs_for(params, gen, GROUP, route, 0.8)
    names, post = location.filter_series(legs, 240)
    assert np.abs(post.sum(axis=1) - 1).max() < 1e-9 and post.min() >= -1e-15
    assert names[0] == "at_unit" and post[0, 0] == 1.0


def test_the_sub_legs_are_scaled_to_the_fitted_normal_transit(params, gen):
    g = params["groups"][GROUP]
    share = 0.8
    mean = {}
    for route in ("tube", "hand_carry"):
        legs = location.legs_for(params, gen, GROUP, route, share)
        chain = [leg for leg in legs if leg.name in ("at_unit", "in_tube", "at_station", "hand_carry")]
        mean[route] = sum(location.mean_of(leg.median, leg.sigma) for leg in chain)
    assert share * mean["tube"] + (1 - share) * mean["hand_carry"] == pytest.approx(math.exp(g["mu"] + g["sigma"] ** 2 / 2))


def test_the_split_between_legs_follows_the_assumed_leg_times(params, gen):
    a, b = (location.legs_for(params, gen, GROUP, "tube", 0.8)[i] for i in (1, 2))
    base = gen["legs"]
    assert a.median / b.median == pytest.approx(base["tube_transit"]["median_min"] / base["station_dwell"]["median_min"])
    assert a.sigma == pytest.approx(location.sigma_of(base["tube_transit"]["median_min"], base["tube_transit"]["p90_min"]))


def test_only_a_tube_specimen_can_be_lost_stalled_or_misrouted(params, gen):
    tube = location.legs_for(params, gen, GROUP, "tube", 0.8)
    hand = location.legs_for(params, gen, GROUP, "hand_carry", 0.8)
    assert {leg.name for leg in tube} == {"at_unit", "in_tube", "at_station", "stalled_at_station", "misrouted"}
    assert [leg.name for leg in hand] == ["at_unit", "hand_carry"]
    assert location.absorbing(hand) == ["left_at_unit"] and location.absorbing(tube) == ["left_at_unit", "lost"]


def test_the_failure_shares_come_from_the_fitted_weights(params, gen):
    tube = location.legs_for(params, gen, GROUP, "tube", 0.8)
    w = params["pooled"]["weights"]
    never_lost = gen["faults"]["lost_in_tube"]["rate"] / (gen["faults"]["lost_in_tube"]["rate"] + gen["faults"]["not_dispatched"]["rate"])
    q_left = dict(tube[0].exits)["left_at_unit"]
    q_lost = dict(tube[1].exits)["lost"]
    assert q_left == pytest.approx(w["never"] * (1 - never_lost))
    assert 0.8 * q_lost == pytest.approx(w["never"] * never_lost)  # all of the lost are among the tube specimens


def test_silence_moves_a_tube_specimen_from_the_tube_to_the_station_and_then_to_trouble(params, gen):
    names, post = location.filter_series(location.legs_for(params, gen, GROUP, "tube", 0.8), 120)
    places = location.place_series(names, post)
    at = lambda t: max(places, key=lambda p: places[p][t])
    assert at(5) == "at_unit" and at(15) == "at_station" and at(90) == "stalled_at_station"
    assert at(120) in ("stalled_at_station", "at_unit")  # at the unit means left there
    assert np.allclose(sum(places.values()), 1)


def test_a_specimen_left_at_its_unit_counts_as_at_the_unit():
    names = ["at_unit", "hand_carry", "left_at_unit"]
    post = np.array([[0.2, 0.5, 0.3]])
    places = location.place_series(names, post)
    assert places["at_unit"][0] == pytest.approx(0.5) and places["hand_carry"][0] == 0.5
    assert places["lost"][0] == 0 and places["in_tube"][0] == 0


def test_an_unknown_route_is_refused(params, gen):
    with pytest.raises(ValueError, match="unknown route"):
        location.legs_for(params, gen, GROUP, "drone", 0.8)
