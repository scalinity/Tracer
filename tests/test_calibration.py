"""Calibration of TracerNet (docs/neural/spec.md §7.6, §15.2)."""

import json

import numpy as np
import pytest
import torch

from src.models.tracernet import bins as tbins
from src.neural import calibrate
from src.neural.calibrate import Calibration

BINS = tbins.build()


def toy(n=4000, seed=0, scale=1.0, miss_bias=0.0):
    """Validation-like ticks of a 60-minute window, 5 to 60 minutes. Targets are drawn from a known
    distribution; the model's logits are that distribution's logits times `scale` (overconfident above 1)
    plus `miss_bias` on every bin after the window, which a temperature alone cannot undo."""
    g = torch.Generator().manual_seed(seed)
    window = torch.full((n,), 60.0)
    tick = torch.randint(1, 13, (n,), generator=g).float() * 5
    on_time = torch.as_tensor(BINS.lower < 60)
    true = torch.randn(n, BINS.n_classes, generator=g) * 0.5 + torch.where(
        on_time, torch.randn(n, 1, generator=g) * 1.5 + 0.5, torch.tensor(-3.0))
    true = BINS.mask_logits(true, tick)
    target = torch.multinomial(torch.softmax(true, -1), 1, generator=g)[:, 0]
    logits = BINS.mask_logits(true * scale + miss_bias * ~on_time, tick)
    return logits, target, window, tick


def test_the_temperature_lowers_the_nll_on_an_overconfident_toy():
    logits, target, window, tick = toy(scale=3.0)
    weight = 1.0 + (tick.numpy() <= 30)  # tick weights are honored
    temperature = calibrate.fit_temperature(logits, target, weight)
    before = calibrate.weighted_nll(logits, target, weight)
    after = calibrate.weighted_nll(logits, target, weight, temperature)
    assert temperature == pytest.approx(3.0, rel=0.05)
    assert after < before - 1.0
    scaled = torch.softmax(Calibration(temperature).scale(logits), -1)
    assert (scaled[BINS.impossible(tick)] == 0).all()  # masked bins keep exactly zero mass


def test_the_temperature_alone_suffices_when_it_brings_the_ece_under_the_limit():
    cal, report = calibrate.fit(*toy(scale=3.0), BINS)
    assert report["ece_after_temperature"] <= calibrate.ECE_LIMIT and cal.iso_x is None
    assert report["after"]["ece"] < report["before"]["ece"]


def test_an_isotonic_map_follows_when_the_temperature_leaves_the_ece_above_the_limit():
    cal, report = calibrate.fit(*toy(scale=2.0, miss_bias=1.5), BINS)
    assert report["ece_after_temperature"] > calibrate.ECE_LIMIT and report["isotonic"]
    assert report["after"]["ece"] < calibrate.ECE_LIMIT < report["before"]["ece"]
    for side in ("before", "after"):
        assert len(report[side]["reliability"]) == calibrate.N_BINS
        assert set(report[side]["brier"]) == {"30", "60"} and None not in report[side]["brier"].values()
        assert report[side]["slope"] is not None and report[side]["intercept"] is not None
    assert report["after"]["slope"] == pytest.approx(1.0, abs=0.05)


def test_the_isotonic_map_is_monotone():
    rng = np.random.default_rng(0)
    p = rng.uniform(size=5000)
    y = rng.uniform(size=5000) < p ** 2  # p overstates the risk
    xs, ys = calibrate.fit_isotonic(p, y)
    assert (np.diff(xs) > 0).all() and (np.diff(ys) >= 0).all()
    mapped = Calibration(1.0, tuple(xs), tuple(ys)).map_p_miss(np.linspace(-0.1, 1.1, 1201))
    assert (np.diff(mapped) >= 0).all() and mapped.min() >= 0 and mapped.max() <= 1
    assert calibrate.ece(Calibration(1.0, tuple(xs), tuple(ys)).map_p_miss(p), y) < calibrate.ece(p, y)


def test_rescaling_keeps_p_late_plus_p_never_equal_to_p_miss():
    rng = np.random.default_rng(1)
    probs = rng.dirichlet(np.ones(BINS.n_classes) * 0.3, size=2000)
    derived = BINS.derived(probs, rng.choice([60.0, 120.0, 1440.0], size=2000))
    cal = Calibration(1.3, (0.0, 0.2, 0.5, 1.0), (0.05, 0.1, 0.6, 0.9))
    out = cal.apply(derived["p_miss"], derived["p_late"], derived["p_never"])
    assert np.allclose(out["p_late"] + out["p_never"], out["p_miss"], rtol=0, atol=1e-12)
    assert np.array_equal(out["p_miss"], cal.map_p_miss(derived["p_miss"]))
    assert (out["p_late"] >= 0).all() and (out["p_never"] >= 0).all()
    share = derived["p_never"] / derived["p_miss"]
    assert np.allclose(out["p_never"] / out["p_miss"], share)  # in proportion
    assert np.allclose(out["p_on_time"], 1 - out["p_miss"])


def test_ece_slope_and_intercept_on_a_calibrated_toy():
    rng = np.random.default_rng(2)
    p = rng.uniform(0.01, 0.99, size=200_000)
    y = rng.uniform(size=p.size) < p
    assert calibrate.ece(p, y) < 0.01
    fit = calibrate.slope_intercept(p, y)
    assert fit["slope"] == pytest.approx(1.0, abs=0.03) and fit["intercept"] == pytest.approx(0.0, abs=0.03)
    assert calibrate.ece(np.full(4, 0.5), np.array([1, 1, 0, 0]), n_bins=2) == pytest.approx(0.5)


@pytest.mark.parametrize("cal", [Calibration(1.7), Calibration(0.83, (0.0, 0.31, 1.0), (0.02, 0.4, 0.97))])
def test_save_and_load_round_trip(cal, tmp_path):
    path = tmp_path / "calibration.json"
    cal.save(path)
    loaded = Calibration.load(path)
    assert loaded == cal and loaded.to_json() == json.loads(path.read_text())
    p = np.linspace(0, 1, 101)
    assert np.array_equal(loaded.map_p_miss(p), cal.map_p_miss(p))
