"""Site recalibration of TracerNet from a laboratory's own history (results/neural/caveats.md, second item).

TracerNet's probabilities carry the population it was trained on, the v2 laboratory weeks. Where a laboratory's
base rates differ (the Claude-written v1 world, `legacy_v1`, and the v1-world control), its Brier score falls behind
the analytic model, which is refit on that laboratory's own history. This gives TracerNet the same thing the local
refit has and nothing more: on each tuning group's history (the v2 sets' replicate 0, legacy_v1's fit runs 0 to 23)
a two-parameter logistic map

    logit p' = a + b * logit p

is fit to the history's own outcomes, which are derived from its scans and windows exactly as the history's other
uses derive them (evaluate.load_specs: no receipt, or a receipt after the window, is a miss). The scored replicates
are never read for the fit. A Gaussian prior centred on the identity (a = 0, b = 1), worth PRIOR_TICKS ticks, keeps a
short history from moving the map far. Within one tuning group the map is monotone, so it changes each specimen's
order only across urgency tiers (harm is the tier's weight times p), and it leaves TracerNet's network, temperature
and features as they are.

    tracernet_sitecal(risk_table)   a RiskMethod named "tracernet" (so the evaluation's focus and decision rule apply
                                    unchanged), for evaluate_split(..., methods=baselines() + [tracernet_sitecal(path)])

The evaluations that use it, and the choice of the map's form on legacy_v1's history, are in
src/pipeline/sitecal_eval.py.
"""
from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np

from src.pipeline import neural_eval as ne

PRIOR_TICKS = 2000.0          # the identity prior's weight, in history ticks
EPS = 1e-6


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p) - np.log1p(-p)


FORMS = {"linear": 2, "elapsed": 3}      # logit p' = a + b logit p (+ c log(t / 30))
FORM = "elapsed"


def design(p: np.ndarray, tick_min: np.ndarray, form: str = FORM) -> np.ndarray:
    cols = [np.ones(len(p)), _logit(np.asarray(p, float))]
    if form == "elapsed":
        cols.append(np.log(np.maximum(np.asarray(tick_min, float), 1.0) / 30.0))
    return np.column_stack(cols)


def fit_map(p: np.ndarray, y: np.ndarray, tick_min: np.ndarray | None = None, form: str = FORM,
            prior_ticks: float = PRIOR_TICKS) -> tuple[np.ndarray, dict]:
    """The map's coefficients maximizing the Bernoulli log-likelihood of y under sigmoid(design @ theta), minus a
    Gaussian prior centred on the identity (0, 1, 0) with precision prior_ticks / 4 per coefficient (about the
    information of `prior_ticks` ticks at p = 0.5). Returns the coefficients and a record of the fit; a fit that
    fails or reverses TracerNet's order (b <= 0) falls back to the identity."""
    from scipy.optimize import minimize

    if form not in FORMS:
        raise ValueError(f"form must be one of {sorted(FORMS)}")
    ticks = np.zeros(len(p)) if tick_min is None else np.asarray(tick_min, float)
    X = design(p, ticks, form)
    y = np.asarray(y, float)
    centre = np.array([0.0, 1.0, 0.0][:FORMS[form]])
    lam = prior_ticks / 4.0

    def f(theta):
        z = X @ theta
        ll = np.sum(y * z - np.logaddexp(0.0, z))
        q = 1.0 / (1.0 + np.exp(-z))
        d = theta - centre
        return -(ll - 0.5 * lam * d @ d), -(X.T @ (y - q) - lam * d)

    res = minimize(f, centre.copy(), jac=True, method="L-BFGS-B")
    theta = np.asarray(res.x, float)
    rec = {"form": form, "ticks": int(len(y)), "misses": float(y.sum()), "prior_ticks": prior_ticks}
    if not np.all(np.isfinite(theta)) or theta[1] <= 0:
        return centre, rec | {"fell_back": "the fit failed or reversed the order: identity used"}
    return theta, rec | {"theta": [float(v) for v in theta], "converged": bool(res.success)}


def apply_map(p: np.ndarray, theta: np.ndarray, tick_min: np.ndarray | None = None) -> np.ndarray:
    theta = np.asarray(theta, float)
    form = "elapsed" if len(theta) == 3 else "linear"
    ticks = np.zeros(len(p)) if tick_min is None else tick_min
    return 1.0 / (1.0 + np.exp(-(design(p, ticks, form) @ theta)))


def history_xy(specs, series) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(p, outcome, tick minutes) over every tick of `specs`, from a RiskMethod series; the outcome is the
    specimen's breach as the history derives it from its scans and window."""
    specs = [s for s in specs if len(s.ticks)]
    if not specs:
        return np.empty(0), np.empty(0), np.empty(0)
    got = series(specs)
    p = np.concatenate([np.asarray(got[s.specimen_id], float) for s in specs])
    y = np.concatenate([np.full(len(s.ticks), float(s.breach)) for s in specs])
    t = np.concatenate([s.ticks for s in specs])
    return p, y, t


class SiteCalibrated(ne.RiskMethod):
    """TracerNet's risk table, mapped per tuning group by fit_map on that group's history."""

    def __init__(self, series, prior_ticks: float = PRIOR_TICKS, form: str = FORM, name: str = ne.TRACERNET):
        super().__init__(name, series, monotone=False, probabilistic=True,
                         fitted_on="train (TracerNet artifact), recalibrated on each tuning group's history")
        self.prior_ticks, self.form = prior_ticks, form

    def history_map(self, group) -> tuple[np.ndarray, dict]:
        p, y, t = history_xy(group.history, lambda specs: self.series(specs, group.context | {"role": "history"}))
        if not len(p):
            return np.array([0.0, 1.0, 0.0][:FORMS[self.form]]), {"ticks": 0, "fell_back": "no history ticks: identity"}
        return fit_map(p, y, t, self.form, self.prior_ticks)

    def prepare(self, group):
        theta, rec = self.history_map(group)
        return theta, rec.get("fell_back")

    def scores(self, state, specs: Sequence, flat, group, role):
        raw = super().scores(state, specs, flat, group, role)
        return apply_map(raw, state, np.asarray(flat.t, float))


SITE_METHOD = "tracernet_site"   # the site-mapped probability beside TracerNet's own, in one evaluation


def tracernet_sitecal(risk_table, value: str = "p_miss", prior_ticks: float = PRIOR_TICKS, form: str = FORM,
                      name: str = ne.TRACERNET):
    """TracerNet with the site map. Named "tracernet" it replaces TracerNet's own scores; named SITE_METHOD it sits
    beside them, so one evaluation scores alerts on TracerNet's own ranking and calibration on the mapped probability
    (docs/neural/preregistration-v2.md §4, G1 and G2)."""
    return SiteCalibrated(ne.FrameRisk(risk_table, value), prior_ticks, form, name=name)


def maps(result: Mapping) -> list:
    """The fitted maps a sitecal evaluation logged as notes (fall-backs only); kept for the report."""
    return [n for n in (result.get("local_fit") or {}).get("notes", []) if n.get("method") == "tracernet"]
