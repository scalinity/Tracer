"""How different scenarios are from each other: signatures, duplicates, families, coverage (spec §4.9).

signature(scenario, kpis) is a vector of 32 numbers (FEATURE_NAMES). Standardized with a robust
scaler (median and interquartile range), it supports:

  near duplicates   two scenarios of one archetype closer than delta are near duplicates; delta is
                    half the median nearest-neighbor distance of a reference set (the pilot)
  families          union-find over pairs closer than 1.5 delta, whatever the archetype; splits are
                    made by family so near copies never straddle train and test
  coverage          counts per cell, per factor level (each at least 90% of target) and per edge tag
  dispersion        mean nearest-neighbor distance and the Vendi score, reported beside the same
                    numbers for code-random scenarios (reported, never gated)
  collapse check    within a stratum, the coefficient of variation of median transit and of breach
                    share must be at least 0.15

The spec lists more than 32 quantities once disruption counts are taken by type, so counts are
grouped into three (tube, courier, laboratory) to keep the vector at 32; this and the choices
marked "assumed" below are the only departures. The plausibility KPIs come from src/sim/kpi.py
and are passed in as a dict with the keys of KPI_KEYS; a missing key counts as 0.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.spatial.distance import cdist

from . import validate

KPI_KEYS = ("median_tube_min", "median_courier_min", "median_walk_in_min", "breach_share", "never_received_share")
FEATURE_NAMES = (
    "log_volume", "weekend_factor", "unit_count",
    "share_tube", "share_courier", "share_walk_in", "share_offsite",
    "tube_line_count", "courier_route_count",
    "round_interval_day", "round_interval_evening", "round_interval_night",
    "staff_per_100_day", "staff_per_100_evening", "staff_per_100_night",
    "logit_never_collected", "logit_content_failure", "logit_not_dispatched", "logit_stalled",
    "disruptions_tube", "disruptions_courier", "disruptions_laboratory",
    "log_disruption_minutes", "mix_entropy",
    "profile_pc1", "profile_pc2", "profile_pc3",
    *KPI_KEYS)
N_FEATURES = len(FEATURE_NAMES)
assert N_FEATURES == 32

NO_SERVICE_MIN = 480           # assumed: a shift without rounds counts as an interval of 8 hours
RATE_FLOOR = 1e-4              # assumed: fault rates are floored before the logit
FAMILY_FACTOR = 1.5
COLLAPSE_CV = 0.15
COVERAGE_FRACTION = 0.90
TUBE_TYPES = ("tube_line_outage", "tube_line_degraded")
COURIER_TYPES = ("courier_missed_rounds", "courier_delayed", "offsite_route_closed")


# --- The signature ----------------------------------------------------------------------------

def default_basis() -> tuple[np.ndarray, np.ndarray]:
    """Mean and three fixed harmonics of the day, used until a reference supplies fitted components."""
    hours = np.arange(24)
    angle = 2 * np.pi * hours / 24
    comps = np.stack([np.cos(angle), np.sin(angle), np.cos(2 * angle)])
    return np.full(24, 1 / 24), comps / np.linalg.norm(comps, axis=1, keepdims=True)


def _logit(p: float) -> float:
    p = min(max(p, RATE_FLOOR), 1 - RATE_FLOOR)
    return float(np.log(p / (1 - p)))


def _mean_interval(scenario: dict, shift: str) -> float:
    values = [r["round_interval_min"][shift] for r in scenario["couriers"]["routes"]]
    values = [v for v in values if v is not None]
    return float(np.mean(values)) if values else float(NO_SERVICE_MIN)


def _disruption_minutes(scenario: dict) -> float:
    return float(sum(d["duration_min"] * (7 if d["recurs_daily"] else 1) for d in scenario["disruptions"]))


def _group_counts(scenario: dict) -> list[int]:
    types = [d["type"] for d in scenario["disruptions"]]
    tube = sum(t in TUBE_TYPES for t in types)
    courier = sum(t in COURIER_TYPES for t in types)
    return [tube, courier, len(types) - tube - courier]


def _mix_entropy(scenario: dict) -> float:
    p = np.array(list(scenario["specimen_mix"].values()), dtype=float)
    p = p[p > 0] / p.sum()
    return float(-(p * np.log(p)).sum())


def signature(scenario: dict, kpis: dict, reference: "Reference | None" = None) -> np.ndarray:
    """The 32 raw numbers of a scenario (not yet standardized)."""
    s = scenario
    shares = validate.route_shares(s)
    daily = s["volume"]["specimens_per_weekday"]
    staff = s["receiving"]["staff_by_shift"]["weekday"]
    weights = validate.volume_shares(s)
    not_dispatched = sum(w * u["not_dispatched_rate"] for w, u in zip(weights, s["units"]))
    profile = np.array(s["volume"]["hourly_profile"], dtype=float)
    mean, comps = (reference.pca_mean, reference.pca_components) if reference else default_basis()
    pcs = comps @ (profile / profile.sum() - mean)
    vec = [np.log(daily), s["volume"]["weekend_factor"], len(s["units"]),
           shares["tube"], shares["courier"], shares["walk_in"], shares["offsite"],
           len(s["tube_system"]["lines"]), len(s["couriers"]["routes"]),
           *(_mean_interval(s, shift) for shift in ("day", "evening", "night")),
           *(staff[shift] / (daily / 100) for shift in ("day", "evening", "night")),
           _logit(s["independent_faults"]["never_collected_rate"]), _logit(s["independent_faults"]["content_failure_rate"]),
           _logit(not_dispatched), _logit(s["tube_system"]["stalled_rate"]),
           *_group_counts(s), np.log1p(_disruption_minutes(s)), _mix_entropy(s),
           *pcs, *(float(kpis.get(k) or 0.0) for k in KPI_KEYS)]
    return np.array(vec, dtype=float)


# --- The reference: scaler, components and delta -----------------------------------------------

@dataclass
class Reference:
    center: np.ndarray
    scale: np.ndarray
    pca_mean: np.ndarray
    pca_components: np.ndarray
    delta: float

    def standardize(self, raw: np.ndarray) -> np.ndarray:
        return (np.atleast_2d(raw) - self.center) / self.scale

    def save(self, path: Path) -> None:
        body = {"center": self.center.tolist(), "scale": self.scale.tolist(), "pca_mean": self.pca_mean.tolist(),
                "pca_components": self.pca_components.tolist(), "delta": self.delta, "features": list(FEATURE_NAMES)}
        Path(path).write_text(json.dumps(body, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Reference":
        body = json.loads(Path(path).read_text(encoding="utf-8"))
        if body["features"] != list(FEATURE_NAMES):
            raise ValueError("the saved reference was fitted on different features")
        return cls(np.array(body["center"]), np.array(body["scale"]), np.array(body["pca_mean"]),
                   np.array(body["pca_components"]), float(body["delta"]))


def robust_scaler(raw: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Median and IQR / 1.349 per feature; a constant feature gets scale 1 so it never divides by zero."""
    q1, med, q3 = np.percentile(raw, [25, 50, 75], axis=0)
    scale = (q3 - q1) / 1.349
    return med, np.where(scale > 1e-9, scale, 1.0)


def fit_profile_basis(scenarios: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """The mean hourly shape and its first three principal components over a set of scenarios."""
    shapes = np.array([np.array(s["volume"]["hourly_profile"], dtype=float) / sum(s["volume"]["hourly_profile"])
                       for s in scenarios])
    mean = shapes.mean(axis=0)
    _, _, vt = np.linalg.svd(shapes - mean, full_matrices=False)
    return mean, vt[:3]


def nearest_distances(x: np.ndarray) -> np.ndarray:
    d = cdist(x, x)
    np.fill_diagonal(d, np.inf)
    return d.min(axis=1)


def fit_reference(scenarios: list[dict], kpis: list[dict], *, scaler_scenarios: list[dict] | None = None,
                  scaler_kpis: list[dict] | None = None) -> Reference:
    """Fit components and delta on a reference set (the pilot); the scaler may use a larger set (pilot plus main run)."""
    if len(scenarios) < 3:
        raise ValueError("a reference set needs at least 3 scenarios")
    mean, comps = fit_profile_basis(scenarios)
    basis = Reference(np.zeros(N_FEATURES), np.ones(N_FEATURES), mean, comps, 0.0)
    pool, pool_kpis = (scaler_scenarios or scenarios), (scaler_kpis or kpis)
    center, scale = robust_scaler(np.array([signature(s, k, basis) for s, k in zip(pool, pool_kpis)]))
    ref = Reference(center, scale, mean, comps, 0.0)
    x = ref.standardize(np.array([signature(s, k, ref) for s, k in zip(scenarios, kpis)]))
    ref.delta = 0.5 * float(np.median(nearest_distances(x)))
    return ref


# --- Duplicates and families ----------------------------------------------------------------------

def near_duplicate_pairs(x: np.ndarray, archetypes: list[str], delta: float) -> list[tuple[int, int, float]]:
    """Pairs (i, j) of the same archetype closer than delta."""
    d = cdist(x, x)
    same = np.equal.outer(np.array(archetypes), np.array(archetypes))
    i, j = np.where(np.triu((d < delta) & same, k=1))
    return [(int(a), int(b), float(d[a, b])) for a, b in zip(i, j)]


def later_duplicates(item_ids: list[str], x: np.ndarray, archetypes: list[str], delta: float) -> set[str]:
    """The item to reject in each near-duplicate pair: the later one by item_id."""
    return {max(item_ids[i], item_ids[j]) for i, j, _ in near_duplicate_pairs(x, archetypes, delta)}


def families(x: np.ndarray, delta: float, factor: float = FAMILY_FACTOR) -> np.ndarray:
    """Union-find over pairs closer than factor * delta, whatever the archetype. Labels count up from 0 by first member."""
    n = len(x)
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    d = cdist(x, x)
    for a, b in zip(*np.where(np.triu(d < factor * delta, k=1))):
        ra, rb = find(int(a)), find(int(b))
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    roots = [find(i) for i in range(n)]
    label = {r: k for k, r in enumerate(dict.fromkeys(roots))}
    return np.array([label[r] for r in roots])


class Library:
    """The accepted scenarios' standardized signatures, for the near-duplicate layer of validate()."""

    def __init__(self, reference: Reference, kpis_fn=None):
        self.reference, self.kpis_fn = reference, kpis_fn
        self.ids: list[str] = []
        self.archetypes: list[str] = []
        self.vectors: list[np.ndarray] = []

    def vector(self, scenario: dict, kpis: dict | None = None) -> np.ndarray:
        if kpis is None:
            kpis = self.kpis_fn(scenario) if self.kpis_fn else {}
        return self.reference.standardize(signature(scenario, kpis, self.reference))[0]

    def add(self, item_id: str, scenario: dict, kpis: dict | None = None) -> None:
        self.ids.append(item_id)
        self.archetypes.append(scenario["archetype"])
        self.vectors.append(self.vector(scenario, kpis))

    def duplicate_of(self, scenario: dict, brief: dict | None = None) -> str | None:
        """A message naming the near duplicate already in the library, or None."""
        same = [i for i, a in enumerate(self.archetypes) if a == scenario["archetype"]]
        if not same:
            return None
        d = np.linalg.norm(np.array([self.vectors[i] for i in same]) - self.vector(scenario), axis=1)
        k = int(d.argmin())
        if d[k] >= self.reference.delta:
            return None
        return (f"near duplicate of accepted scenario {self.ids[same[k]]} (distance {d[k]:.2f}, "
                f"threshold {self.reference.delta:.2f}): it is dropped, not repaired")


# --- Coverage, dispersion, collapse -----------------------------------------------------------------

def _counts(rows: list[dict], key) -> dict:
    out: dict = {}
    for row in rows:
        for k in key(row):
            out[k] = out.get(k, 0) + 1
    return out


def coverage(accepted: list[dict], planned: list[dict]) -> dict:
    """Accepted counts against the planned briefs' counts, per cell, per factor level and per edge tag.

    A cell is (stratum, archetype), assumed: the full five-factor cell has far more combinations
    than briefs. accepted rows carry a brief_id; their factors are read from the planned brief.
    """
    by_id = {b["brief_id"]: b for b in planned}
    have = [by_id[a["brief_id"]] for a in accepted if a["brief_id"] in by_id]
    report: dict = {"marginals": {}, "ok": True}

    def block(key) -> dict:
        target, got = _counts(planned, key), _counts(have, key)
        rows = {k: {"target": t, "count": got.get(k, 0), "ok": got.get(k, 0) >= COVERAGE_FRACTION * t} for k, t in target.items()}
        report["ok"] = report["ok"] and all(r["ok"] for r in rows.values())
        return rows

    report["cells"] = block(lambda b: [f"{b['stratum']}/{b['archetype']}"])
    for factor in ("stratum", "archetype", "size_band", "transport_mix", "staffing", "failure_focus"):
        report["marginals"][factor] = block(lambda b, f=factor: [b[f]])
    report["edge_tags"] = block(lambda b: b["edge_tags"])
    return report


def mean_nearest_distance(x: np.ndarray) -> float:
    return float(nearest_distances(x).mean())


def vendi_score(x: np.ndarray, sigma: float | None = None) -> float:
    """exp of the entropy of the eigenvalues of K / n, with K the RBF similarity matrix (K_ii = 1).

    Identical items give 1; n items far apart give n. The default bandwidth is sqrt(dimension), so
    two typical standardized signatures have similarity exp(-1)-ish and the score is not saturated at n.
    """
    n = len(x)
    sigma = float(np.sqrt(x.shape[1])) if sigma is None else sigma
    k = np.exp(-cdist(x, x, "sqeuclidean") / (2 * sigma**2))
    eig = np.clip(np.linalg.eigvalsh(k / n), 0, None)
    eig = eig[eig > 1e-12]
    return float(np.exp(-(eig * np.log(eig)).sum()))


def dispersion(x: np.ndarray, strata: list[str] | None = None, sigma: float | None = None) -> dict:
    """Mean nearest-neighbor distance and Vendi score overall and per stratum."""
    out = {"all": {"n": len(x), "mean_nn": mean_nearest_distance(x), "vendi": vendi_score(x, sigma)}}
    for s in sorted(set(strata or [])):
        rows = x[[i for i, v in enumerate(strata) if v == s]]
        if len(rows) >= 2:
            out[s] = {"n": len(rows), "mean_nn": mean_nearest_distance(rows), "vendi": vendi_score(rows, sigma)}
    return out


def _cv(values: list[float]) -> float:
    a = np.array(values, dtype=float)
    return float(a.std(ddof=1) / abs(a.mean())) if len(a) > 1 and a.mean() != 0 else 0.0


def median_transit(kpis: dict) -> float:
    """The scenario's median collection-to-receipt time: median_transit_min if given, else the mean of the route medians."""
    if kpis.get("median_transit_min") is not None:
        return float(kpis["median_transit_min"])
    found = [kpis[k] for k in KPI_KEYS[:3] if kpis.get(k)]
    return float(np.mean(found)) if found else 0.0


def collapse_check(kpis_by_stratum: dict[str, list[dict]]) -> dict:
    """Per stratum: the CV of median transit and of breach share, and whether either falls below 0.15."""
    out = {}
    for stratum, rows in kpis_by_stratum.items():
        cv_transit = _cv([median_transit(k) for k in rows])
        cv_breach = _cv([float(k.get("breach_share") or 0.0) for k in rows])
        out[stratum] = {"n": len(rows), "cv_median_transit": cv_transit, "cv_breach_share": cv_breach,
                        "flagged": cv_transit < COLLAPSE_CV or cv_breach < COLLAPSE_CV}
    return out
