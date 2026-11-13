"""Site recalibration (src/neural/sitecal.py) and the negative control's lead rule (src/pipeline/control_audit.lead)."""

import numpy as np
import pytest

from src.neural import sitecal
from src.pipeline import control_audit


def _sample(n=20000, seed=0, a=-0.8, b=0.7, c=0.6):
    rng = np.random.default_rng(seed)
    p = rng.uniform(0.02, 0.9, n)
    t = rng.choice(np.arange(5, 241, 5), n).astype(float)
    z = a + b * sitecal._logit(p) + c * np.log(t / 30.0)
    y = (rng.uniform(size=n) < 1 / (1 + np.exp(-z))).astype(float)
    return p, y, t


def test_identity_is_kept_when_already_calibrated():
    rng = np.random.default_rng(1)
    p = rng.uniform(0.05, 0.95, 20000)
    y = (rng.uniform(size=len(p)) < p).astype(float)
    t = np.full(len(p), 30.0)
    for form in sitecal.FORMS:
        theta, rec = sitecal.fit_map(p, y, t, form)
        assert np.allclose(theta, [0.0, 1.0, 0.0][:sitecal.FORMS[form]], atol=0.08), rec


def test_elapsed_form_recovers_a_known_map():
    p, y, t = _sample()
    theta, rec = sitecal.fit_map(p, y, t, "elapsed", prior_ticks=10)
    assert rec["converged"]
    assert np.allclose(theta, [-0.8, 0.7, 0.6], atol=0.12)
    before = np.mean((p - y) ** 2)
    after = np.mean((sitecal.apply_map(p, theta, t) - y) ** 2)
    assert after < before


def test_prior_holds_a_short_history_near_the_identity():
    p, y, t = _sample(n=60)
    theta, _ = sitecal.fit_map(p, y, t, "elapsed", prior_ticks=sitecal.PRIOR_TICKS)
    assert np.all(np.abs(theta - [0.0, 1.0, 0.0]) < 0.35)


def test_a_reversing_fit_falls_back_to_the_identity():
    rng = np.random.default_rng(2)
    p = rng.uniform(0.05, 0.95, 5000)
    y = (rng.uniform(size=len(p)) < 1 - p).astype(float)        # outcomes run against TracerNet's order
    theta, rec = sitecal.fit_map(p, y, None, "linear", prior_ticks=1)
    assert "fell_back" in rec and np.allclose(theta, [0.0, 1.0])


def test_map_is_monotone_at_a_fixed_tick():
    theta = np.array([0.3, 0.8, 0.5])
    p = np.linspace(0.01, 0.99, 200)
    q = sitecal.apply_map(p, theta, np.full(len(p), 45.0))
    assert np.all(np.diff(q) > 0)


def test_unknown_form_is_refused():
    with pytest.raises(ValueError):
        sitecal.fit_map(np.array([0.5]), np.array([1.0]), None, "cubic")


def _result(point, low, high, cap=(0.0, -1.0, 1.0)):
    d = {"median_warning_all_breaches_min": {"point": point, "low": low, "high": high},
         "capped_mean_warning_min": {"point": cap[0], "low": cap[1], "high": cap[2]}}
    return {"focus": "tracernet", "settings": {"headline": "6"}, "differences": {"6": {"3-2-1": {"analytic_local": d}}}}


def test_lead_flags_only_a_large_lead_with_interval_above_zero():
    assert control_audit.lead(_result(10, 5, 20), "analytic_local")["flagged"]
    assert not control_audit.lead(_result(10, 0, 20), "analytic_local")["flagged"]     # interval touches zero
    assert not control_audit.lead(_result(4, 1, 8), "analytic_local")["flagged"]       # under the margin
    assert not control_audit.lead(_result(None, None, None), "analytic_local")["flagged"]
    assert control_audit.lead(_result(0, -5, 5, cap=(6.0, 1.0, 9.0)), "analytic_local")["flagged"]
