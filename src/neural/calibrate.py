"""Calibration of TracerNet on validation ticks (docs/neural/spec.md §7.6).

1. One temperature T on the masked logits (logits / T), fitted by minimizing the tick-weighted NLL. T is
   kept in [0.05, 20], so a masked logit stays below -5e7 and its bin keeps exactly zero mass.
2. If the expected calibration error of p_miss (10 equal-mass bins) is still above 0.02, an isotonic map
   of p_miss (knots read by linear interpolation, flat beyond the end knots), with p_never and p_late
   rescaled in proportion so they still add up to the new p_miss.

`fit` returns the `Calibration` and a report: NLL, ECE, reliability tables, Brier at the 30- and
60-minute landmarks, and calibration slope and intercept, before (raw) and after. `Calibration` saves and
loads as the artifact's calibration.json.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from scipy.optimize import isotonic_regression, minimize, minimize_scalar
from scipy.special import expit

from src.models.tracernet.bins import Bins

TEMPERATURE_BOUNDS = (0.05, 20.0)
ECE_LIMIT = 0.02
N_BINS = 10
LANDMARKS = (30, 60)
CHUNK = 65536


def _weights(n: int, weight) -> np.ndarray:
    return np.ones(n) if weight is None else np.asarray(weight, dtype=float)


def weighted_nll(logits: torch.Tensor, target: torch.Tensor, weight=None, temperature: float = 1.0) -> float:
    """Weighted mean of -log softmax(logits / T)[target], in chunks."""
    w = torch.as_tensor(_weights(len(target), weight), dtype=torch.float64)
    total = 0.0
    with torch.no_grad():
        for s in range(0, len(target), CHUNK):
            logp = torch.log_softmax(logits[s:s + CHUNK] / temperature, -1)
            nll = -logp.gather(1, target[s:s + CHUNK, None])[:, 0].double()
            total += float((nll * w[s:s + CHUNK]).sum())
    return total / float(w.sum())


def fit_temperature(logits: torch.Tensor, target: torch.Tensor, weight=None) -> float:
    lo, hi = (math.log(b) for b in TEMPERATURE_BOUNDS)
    res = minimize_scalar(lambda u: weighted_nll(logits, target, weight, math.exp(u)), bounds=(lo, hi),
                          method="bounded", options={"xatol": 1e-4})
    return float(math.exp(res.x))


def p_miss_of(logits: torch.Tensor, window: torch.Tensor, bins: Bins, temperature: float = 1.0) -> np.ndarray:
    out = []
    with torch.no_grad():
        for s in range(0, len(window), CHUNK):
            probs = torch.softmax(logits[s:s + CHUNK] / temperature, -1)
            out.append(bins.p_miss(probs, window[s:s + CHUNK]).double().cpu().numpy())
    return np.concatenate(out) if out else np.zeros(0)


def _bin_ids(p: np.ndarray, w: np.ndarray, n_bins: int) -> np.ndarray:
    """Equal-mass bins of p: sorted by p, cut where the cumulative weight crosses each 1/n_bins."""
    order = np.argsort(p, kind="stable")
    cw = np.cumsum(w[order])
    mid = (cw - w[order] / 2) / cw[-1]
    ids = np.empty(len(p), dtype=int)
    ids[order] = np.minimum((mid * n_bins).astype(int), n_bins - 1)
    return ids


def reliability(p, y, weight=None, n_bins: int = N_BINS) -> list[dict]:
    """One row per equal-mass bin: count, weight, p range, mean predicted, observed share."""
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    w = _weights(len(p), weight)
    ids = _bin_ids(p, w, n_bins)
    rows = []
    for b in range(n_bins):
        m = ids == b
        if not m.any():
            continue
        wb = w[m].sum()
        rows.append({"bin": b, "n": int(m.sum()), "weight": float(wb), "p_lo": float(p[m].min()),
                     "p_hi": float(p[m].max()), "mean_p": float((w[m] * p[m]).sum() / wb),
                     "observed": float((w[m] * y[m]).sum() / wb)})
    return rows


def ece(p, y, weight=None, n_bins: int = N_BINS) -> float:
    rows = reliability(p, y, weight, n_bins)
    total = sum(r["weight"] for r in rows)
    return float(sum(r["weight"] * abs(r["observed"] - r["mean_p"]) for r in rows) / total)


def brier(p, y, tick, weight=None, landmarks=LANDMARKS) -> dict:
    """Weighted Brier score of p_miss at each landmark tick; None where no row has that tick."""
    p, y, tick = (np.asarray(a, dtype=float) for a in (p, y, tick))
    w = _weights(len(p), weight)
    out = {}
    for t in landmarks:
        m = tick == t
        out[str(t)] = float((w[m] * (p[m] - y[m]) ** 2).sum() / w[m].sum()) if m.any() else None
    return out


def slope_intercept(p, y, weight=None) -> dict:
    """Logistic recalibration y ~ sigmoid(a + b * logit(p)), fitted by weighted maximum likelihood:
    slope b, intercept a (1 and 0 when p is calibrated)."""
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, dtype=float)
    w = _weights(len(p), weight)
    w = w / w.sum()
    x = np.stack([np.ones_like(p), np.log(p / (1 - p))], 1)

    def log_loss(theta):
        z = x @ theta
        return float(w @ (np.logaddexp(0.0, z) - y * z)), x.T @ (w * (expit(z) - y))

    res = minimize(log_loss, np.array([0.0, 1.0]), jac=True, method="L-BFGS-B",
                   options={"gtol": 1e-10, "ftol": 1e-14, "maxiter": 500})
    if not np.isfinite(res.x).all():
        return {"slope": None, "intercept": None}
    return {"slope": float(res.x[1]), "intercept": float(res.x[0])}


def fit_isotonic(p, y, weight=None) -> tuple[np.ndarray, np.ndarray]:
    """Knots (x, y) of the weighted isotonic regression of y on p: ties pooled first, then the first and
    last point of each constant block kept, to be read by linear interpolation."""
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    w = _weights(len(p), weight)
    ux, inv = np.unique(p, return_inverse=True)
    wsum = np.bincount(inv, weights=w)
    fitted = isotonic_regression(np.bincount(inv, weights=w * y) / wsum, weights=wsum, increasing=True).x
    keep = np.ones(len(ux), dtype=bool)
    keep[1:-1] = (fitted[1:-1] != fitted[:-2]) | (fitted[1:-1] != fitted[2:])
    return ux[keep], fitted[keep]


@dataclass
class Calibration:
    temperature: float = 1.0
    iso_x: tuple | None = None
    iso_y: tuple | None = None

    def scale(self, logits: torch.Tensor) -> torch.Tensor:
        """The masked logits divided by the temperature."""
        return logits / self.temperature

    def map_p_miss(self, p_miss) -> np.ndarray:
        p = np.asarray(p_miss, dtype=float)
        if self.iso_x is None:
            return p.copy()
        return np.clip(np.interp(p, self.iso_x, self.iso_y), 0.0, 1.0)

    def apply(self, p_miss, p_late, p_never) -> dict:
        """The derived outputs after the isotonic map (unchanged without one): p_never and p_late scaled by
        the same factor so p_late + p_never = p_miss."""
        miss, late, never = (np.asarray(a, dtype=float) for a in (p_miss, p_late, p_never))
        if self.iso_x is None:
            return {"p_miss": miss, "p_late": late, "p_never": never, "p_on_time": 1 - miss}
        new = self.map_p_miss(miss)
        with np.errstate(invalid="ignore", divide="ignore"):
            factor = np.where(miss > 0, new / miss, 0.0)
        never = np.minimum(never * factor, new)
        return {"p_miss": new, "p_late": new - never, "p_never": never, "p_on_time": 1 - new}

    def to_json(self) -> dict:
        iso = None if self.iso_x is None else {"x": [float(v) for v in self.iso_x],
                                               "y": [float(v) for v in self.iso_y]}
        return {"temperature": self.temperature, "isotonic": iso, "ece_limit": ECE_LIMIT}

    @classmethod
    def from_json(cls, doc: dict) -> "Calibration":
        iso = doc.get("isotonic")
        return cls(float(doc["temperature"]), None if iso is None else tuple(iso["x"]),
                   None if iso is None else tuple(iso["y"]))

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_json(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Calibration":
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))


def _summary(p, y, tick, weight) -> dict:
    return {"ece": ece(p, y, weight), "reliability": reliability(p, y, weight),
            "brier": brier(p, y, tick, weight), **slope_intercept(p, y, weight)}


def fit(logits: torch.Tensor, target: torch.Tensor, window: torch.Tensor, tick: torch.Tensor, bins: Bins,
        weight=None) -> tuple[Calibration, dict]:
    """Fit the temperature, then the isotonic map if the ECE after the temperature is above ECE_LIMIT.
    `logits` [N, K] are the model's masked logits on validation ticks, `target` long [N] the receipt
    classes, `window` and `tick` [N], `weight` [N] the tick weights (None for equal)."""
    y = bins.missed(target, window).cpu().numpy().astype(float)
    ticks = tick.cpu().numpy()
    temperature = fit_temperature(logits, target, weight)
    p_raw = p_miss_of(logits, window, bins)
    p_t = p_miss_of(logits, window, bins, temperature)
    cal = Calibration(temperature)
    ece_t = ece(p_t, y, weight)
    if ece_t > ECE_LIMIT:
        xs, ys = fit_isotonic(p_t, y, weight)
        cal = Calibration(temperature, tuple(xs.tolist()), tuple(ys.tolist()))
    p_final = cal.map_p_miss(p_t)
    report = {"temperature": temperature, "isotonic": cal.iso_x is not None,
              "nll_before": weighted_nll(logits, target, weight),
              "nll_after": weighted_nll(logits, target, weight, temperature),
              "ece_after_temperature": ece_t,
              "before": _summary(p_raw, y, ticks, weight), "after": _summary(p_final, y, ticks, weight)}
    return cal, report
