"""The evaluation harness and driver for the v2 comparison (docs/neural/spec.md §8; plan task N6).

    evaluate_split("val", baselines())                            # every baseline on a set, written to JSON
    evaluate_split("val", baselines() + [tracernet_method(path)]) # with TracerNet: differences and the rule
    command_evaluate("val")                                       # the `evaluate --split` command

The driver (`evaluate_split`, at the end of this file) runs every method of §8.2 through the pieces below on one
set of §8.1: per tuning group (a scenario; on legacy_v1 the fit runs) the knobs from the history alone, the alerts
on the scored replicates, every metric with its cluster bootstrap, discrimination, calibration, slices, matched
volume on request or when R4 fails, and the decision rule when TracerNet is present. Methods implement `Method`
(`AnalyticModel`, `WindowRule`, `QuantileRule`, and `RiskMethod` for a supplied risk series such as TracerNet's). On
legacy_v1 the whole path reproduces results/evaluation.json (tests/test_neural_eval.py).

Every method of §8.2 is scored here the same way, so no method gets a private yardstick:

  alert metrics   the definitions of src/pipeline/evaluate.py `score`, plus the §8.4 additions (false alerts per
                  shift, missed losses, the capped mean warning), computed from per-cluster arrays
  intervals       the paired cluster bootstrap of §8.5, vectorized; on the v1 held-out runs it reproduces
                  `evaluate.score` and `evaluate.bootstrap` float for float (tests/test_neural_eval.py)
  knobs           bisection on a history set from alert volume alone, applied unchanged to the scored set,
                  and the matched-volume variant that re-solves the knob on the scored set (R4's check)
  discrimination  AUROC and AUPRC at the silence landmarks and pooled over ticks, each specimen weighted equally
  calibration     Brier, the base-rate Brier, expected calibration error, slope and intercept, reliability bins
  slices          the alert metrics over any grouping
  decision rule   R1 to R4 of §8.6 as a pure function over the headline results
  test lock       §8.7: the first evaluation of `test` or `test_ood` records hashes; a later one needs them to match.
                  The lock is taken and the run recorded before any test inference; the risk-table parts read are
                  recorded once written and compared on every later look

A "cluster" is whatever the bootstrap resamples whole: a run on the v1 data (`cluster_key=lambda s: s.run`),
a scenario on the v2 data. A specimen is anything with the attributes of `evaluate.Spec`. Labels arrive on the
specimens, loaded through `evaluate` (for example `evaluate.load_test_specs`); this module never opens the
label store itself.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Hashable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np
from scipy.special import ndtr

from src import workers as workers_
from src.generator import config
from src.pipeline import evaluate, intervals, store, triage

ROOT = evaluate.ROOT
NEURAL_DIR = ROOT / "results" / "neural"
LOCK_PATH = NEURAL_DIR / "test_lock.json"
TEST_RUNS_PATH = NEURAL_DIR / "test_runs.jsonl"
# The fresh test set of docs/neural/preregistration-v2.md has its own lock and run log, and is looked at once: no
# relock, and no look once its evaluation is written.
FRESH_SPLIT = "test_fresh"
FRESH_DIR = NEURAL_DIR / FRESH_SPLIT
FRESH_LOCK_PATH = FRESH_DIR / "test_lock.json"
FRESH_RUNS_PATH = FRESH_DIR / "test_runs.jsonl"
LOOKED_NAME = "looked.json"   # beside the fresh lock once its evaluation is written, wherever that went

BUDGETS = evaluate.BUDGETS                  # alerts per 8-hour shift: 1, 2, 4, 6
HEADLINE_BUDGET = 6
PER_100_BUDGETS = (5, 10, 30)               # alerts per 100 specimens, the secondary view of §8.3
SHIFTS_PER_REPLICATE = 7 * 3                # seven days of three 8-hour shifts
GOOD_WARNING_MIN = evaluate.GOOD_WARNING_MIN
WARNING_CAP_MIN = 120                       # the capped mean warning caps each warning here
BOOTSTRAPS = evaluate.BOOTSTRAPS
BOOTSTRAP_SEED = evaluate.BOOTSTRAP_SEED
DISCRIMINATION_LANDMARKS = (15, 30, 60, 90)
CALIBRATION_LANDMARKS = (30, 60)
ECE_BINS = 10

TRACERNET = "tracernet"
BASELINES = ("window_rule", "analytic_global", "analytic_local")
WEIGHTINGS = ("3-2-1", "equal")
LOCKED_SPLITS = ("test", "test_ood", "test_fresh")
EASTERN = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------------------------------------
# Per-cluster arrays and the metrics computed from them
# ---------------------------------------------------------------------------------------------------------

COLUMNS = ("specimens", "breaches", "caught", "never_received", "missed_losses", "alerts", "false_alerts",
           "good_warning", "capped_warning_sum", "shifts")
_C = {name: i for i, name in enumerate(COLUMNS)}

# Everything that is bootstrapped. The first three groups are the metrics of evaluate.score.
METRICS = ("alerts", "alerts_per_shift", "realized_alerts_per_shift", "alerts_per_100_specimens", "breaches",
           "caught", "catch_rate", "median_warning_min", "median_warning_all_breaches_min",
           "caught_with_good_warning", "false_alerts", "false_alerts_per_shift", "never_received",
           "missed_losses", "missed_loss_rate", "capped_mean_warning_min")
LOWER_IS_BETTER = frozenset({"alerts", "alerts_per_shift", "realized_alerts_per_shift", "alerts_per_100_specimens",
                             "false_alerts", "false_alerts_per_shift", "missed_losses", "missed_loss_rate"})
_COUNTS = frozenset({"alerts", "breaches", "caught", "false_alerts", "never_received", "missed_losses"})


def run_of(spec) -> int:
    """The cluster of a v1 specimen: its weekly run."""
    return spec.run


@dataclass(frozen=True)
class Series:
    """One method at one weighting and budget, as per-cluster arrays: for each cluster, the warning of every
    breach (a miss counts as zero), whether it was caught, and the counts the other metrics need."""
    keys: tuple
    scalars: np.ndarray                 # (clusters, len(COLUMNS))
    warnings: tuple[np.ndarray, ...]    # per cluster, one float per breach
    caught: tuple[np.ndarray, ...]      # per cluster, one bool per breach


def _per_cluster_shifts(shifts_per_cluster, keys) -> list[float]:
    if isinstance(shifts_per_cluster, Mapping):
        return [float(shifts_per_cluster[k]) for k in keys]
    return [float(shifts_per_cluster)] * len(keys)


def build_series(specs: Iterable, alerts: Mapping[str, float | None], shifts_per_cluster,
                 cluster_key: Callable = run_of, clusters: Sequence[Hashable] | None = None) -> Series:
    """Per-cluster arrays for one method. `alerts` maps a specimen id to the minute of its alert (None for
    none), as `evaluate.baseline_alerts` and the like return. `shifts_per_cluster` is a number or a mapping from
    cluster to shifts. Pass `clusters` to fix the cluster set (every series compared together needs the same one,
    and a slice may lack some clusters); otherwise it is the sorted keys found."""
    by_cluster: dict[Hashable, list] = {}
    for s in specs:
        by_cluster.setdefault(cluster_key(s), []).append(s)
    keys = tuple(sorted(by_cluster)) if clusters is None else tuple(clusters)
    missing = set(by_cluster) - set(keys)
    if missing:
        raise ValueError(f"specimens in clusters {sorted(missing)!r} that are not in the cluster set")
    shifts = _per_cluster_shifts(shifts_per_cluster, keys)
    scalars = np.zeros((len(keys), len(COLUMNS)))
    warnings, caught_flags = [], []
    for i, key in enumerate(keys):
        warn, caught, never, fired_n, false_n = [], [], [], 0, 0
        members = by_cluster.get(key, [])
        for s in members:
            minute = alerts[s.specimen_id]
            fired = minute is not None
            fired_n += fired
            false_n += fired and not s.breach
            if s.breach:
                lead = s.window - minute if fired else None
                is_caught = fired and lead > 0
                warn.append(lead if is_caught else 0.0)
                caught.append(is_caught)
                never.append(s.receipt_min is None)
        w, c, n = np.array(warn, dtype=float), np.array(caught, dtype=bool), np.array(never, dtype=bool)
        warnings.append(w)
        caught_flags.append(c)
        scalars[i] = (len(members), len(w), c.sum(), n.sum(), (n & ~c).sum(), fired_n, false_n,
                      (w >= GOOD_WARNING_MIN).sum(), np.minimum(w, WARNING_CAP_MIN).sum(), shifts[i])
    return Series(keys, scalars, tuple(warnings), tuple(caught_flags))


def _pooled(values: Sequence[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Values of every cluster, sorted, with the cluster each came from."""
    if not values or sum(len(v) for v in values) == 0:
        return np.empty(0), np.empty(0, dtype=int)
    flat = np.concatenate(values)
    ids = np.concatenate([np.full(len(v), i) for i, v in enumerate(values)])
    order = np.argsort(flat, kind="stable")
    return flat[order], ids[order]


_HISTOGRAM_CELLS = 50_000_000   # clusters x distinct values up to which the median works on a histogram


def _weighted_median(values: np.ndarray, ids: np.ndarray, counts: np.ndarray, chunk: int = 128) -> np.ndarray:
    """The median of the multiset in which cluster i's values appear counts[b, i] times, for every row b.
    Matches np.median of the expanded values: the mean of the two middle values when their number is even.

    Warnings sit on the 5-minute grid, so a few hundred distinct values serve hundreds of thousands of breaches;
    then the cumulative counts run over a (clusters x distinct values) histogram, which picks the same order
    statistics as the cumulative counts over every value and keeps a validation split's bootstrap to seconds."""
    rows = counts.shape[0]
    out = np.full(rows, np.nan)
    if len(values) == 0:
        return out
    uniq, inverse = np.unique(values, return_inverse=True)
    n_clusters = counts.shape[1]
    if len(uniq) < len(values) and n_clusters * len(uniq) <= _HISTOGRAM_CELLS:
        hist = np.bincount(ids * len(uniq) + inverse, minlength=n_clusters * len(uniq)).reshape(n_clusters, len(uniq))
        hist = hist.astype(float)
        step = max(1, min(rows, 20_000_000 // len(uniq)))
        for lo in range(0, rows, step):
            cum = np.rint(np.cumsum(counts[lo:lo + step].astype(float) @ hist, axis=1)).astype(np.int64)
            total = cum[:, -1]
            low, high = (total - 1) // 2, total // 2
            i_low = np.minimum((cum <= low[:, None]).sum(axis=1), len(uniq) - 1)
            i_high = np.minimum((cum <= high[:, None]).sum(axis=1), len(uniq) - 1)
            out[lo:lo + step] = np.where(total > 0, (uniq[i_low] + uniq[i_high]) / 2.0, np.nan)
        return out
    for lo in range(0, rows, chunk):
        w = counts[lo:lo + chunk][:, ids]
        cum = np.cumsum(w, axis=1)
        total = cum[:, -1]
        ok = total > 0
        low, high = (total - 1) // 2, total // 2
        i_low = np.minimum((cum <= low[:, None]).sum(axis=1), len(values) - 1)
        i_high = np.minimum((cum <= high[:, None]).sum(axis=1), len(values) - 1)
        out[lo:lo + chunk] = np.where(ok, (values[i_low] + values[i_high]) / 2.0, np.nan)
    return out


def _ratio(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.divide(a, b, out=np.full(a.shape, np.nan), where=b > 0)


def _metrics_for(series: Series, counts: np.ndarray) -> dict[str, np.ndarray]:
    """Every metric for each row of `counts`, a (rows, clusters) matrix of how many times each cluster is drawn.
    Undefined metrics (no breaches, no caught breach, no never-received specimen) are NaN."""
    t = counts.astype(float) @ series.scalars
    col = lambda name: t[:, _C[name]]
    med_caught = _weighted_median(*_pooled([w[c] for w, c in zip(series.warnings, series.caught)]), counts)
    med_all = _weighted_median(*_pooled(list(series.warnings)), counts)
    per_shift = _ratio(col("alerts"), col("shifts"))
    return {
        "alerts": col("alerts"), "alerts_per_shift": per_shift, "realized_alerts_per_shift": per_shift,
        "alerts_per_100_specimens": _ratio(100 * col("alerts"), col("specimens")),
        "breaches": col("breaches"), "caught": col("caught"),
        "catch_rate": _ratio(col("caught"), col("breaches")),
        "median_warning_min": med_caught, "median_warning_all_breaches_min": med_all,
        "caught_with_good_warning": _ratio(col("good_warning"), col("breaches")),
        "false_alerts": col("false_alerts"), "false_alerts_per_shift": _ratio(col("false_alerts"), col("shifts")),
        "never_received": col("never_received"), "missed_losses": col("missed_losses"),
        "missed_loss_rate": _ratio(col("missed_losses"), col("never_received")),
        "capped_mean_warning_min": _ratio(col("capped_warning_sum"), col("breaches")),
    }


def _plain(name: str, v: float):
    if np.isnan(v):
        return None
    return int(v) if name in _COUNTS else float(v)


def point_metrics(series: Series) -> dict:
    """The metrics over all clusters. The first block has the keys and values of `evaluate.score`."""
    ones = np.ones((1, len(series.keys)), dtype=np.int64)
    return {k: _plain(k, v[0]) for k, v in _metrics_for(series, ones).items()}


def score(specs: Iterable, alerts: Mapping[str, float | None], shifts_per_cluster,
          cluster_key: Callable = run_of, clusters: Sequence[Hashable] | None = None) -> dict:
    """`evaluate.score` for clustered specimens, plus the §8.4 additions."""
    return point_metrics(build_series(specs, alerts, shifts_per_cluster, cluster_key, clusters))


# ---------------------------------------------------------------------------------------------------------
# The paired cluster bootstrap (§8.5)
# ---------------------------------------------------------------------------------------------------------

def draw_counts(n_clusters: int, resamples: int = BOOTSTRAPS, seed: int = BOOTSTRAP_SEED) -> np.ndarray:
    """(resamples, clusters): how many times each cluster is drawn. Each resample is
    `rng.choice(clusters, size=n, replace=True)` on one generator, the call pattern of `evaluate.bootstrap`,
    so a cluster set of the same size draws the same clusters."""
    rng = np.random.default_rng(seed)
    clusters = np.arange(n_clusters)
    counts = np.zeros((resamples, n_clusters), dtype=np.int64)
    for b in range(resamples):
        counts[b] = np.bincount(rng.choice(clusters, size=n_clusters, replace=True), minlength=n_clusters)
    return counts


def _interval(values: np.ndarray) -> dict:
    if len(values) == 0:
        return {"low": None, "high": None}
    return {"low": float(np.percentile(values, 2.5)), "high": float(np.percentile(values, 97.5))}


def bootstrap(series: Mapping[str, Series], focus: str, baselines: Sequence[str] | None = None,
              resamples: int = BOOTSTRAPS, seed: int = BOOTSTRAP_SEED) -> dict:
    """Paired cluster bootstrap. All series are rescored on the same draws of whole clusters. Returns

      intervals[name][metric]   point, low, high (95% percentile), n_used, n_excluded
      differences[name][metric] focus minus name: point, low, high, share_ahead (share of resamples in which
                                the focus is ahead, in the direction that is better for the metric), n_used,
                                n_excluded

    A resample in which a metric is undefined (no breaches, nothing caught, nothing never received) is left out
    of that metric and counted in n_excluded; a difference needs both sides defined."""
    names = list(series)
    if focus not in series:
        raise KeyError(f"{focus!r} is not among the series")
    if len({s.keys for s in series.values()}) != 1:
        raise ValueError("every series must be built over the same clusters")
    baselines = [n for n in names if n != focus] if baselines is None else list(baselines)
    n_clusters = len(series[focus].keys)
    counts = draw_counts(n_clusters, resamples, seed)
    ones = np.ones((1, n_clusters), dtype=np.int64)
    unknown = [n for n in baselines if n not in series]
    if unknown:
        raise KeyError(f"{unknown!r} are not among the series")
    sampled = {n: _metrics_for(series[n], counts) for n in names}
    points = {n: {k: v[0] for k, v in _metrics_for(series[n], ones).items()} for n in names}
    intervals_out = {}
    for n in names:
        intervals_out[n] = {}
        for m in METRICS:
            x = sampled[n][m]
            used = x[~np.isnan(x)]
            intervals_out[n][m] = {"point": _plain(m, points[n][m]), **_interval(used),
                                   "n_used": int(len(used)), "n_excluded": int(resamples - len(used))}
    differences = {}
    for n in baselines:
        differences[n] = {}
        for m in METRICS:
            a, b = sampled[n][m], sampled[focus][m]
            ok = ~np.isnan(a) & ~np.isnan(b)
            d = (b - a)[ok]
            ahead = float(np.mean(d < 0 if m in LOWER_IS_BETTER else d > 0)) if len(d) else None
            point = None if np.isnan(points[n][m]) or np.isnan(points[focus][m]) else float(points[focus][m] - points[n][m])
            differences[n][m] = {"point": point, **_interval(d), "share_ahead": ahead,
                                 "n_used": int(len(d)), "n_excluded": int(resamples - len(d))}
    return {"focus": focus, "resamples": resamples, "seed": seed, "clusters": n_clusters,
            "intervals": intervals_out, "differences": differences}


# ---------------------------------------------------------------------------------------------------------
# Slices
# ---------------------------------------------------------------------------------------------------------

def window_class(s) -> str:
    return "short (up to 2 h)" if s.window <= evaluate.SHORT_WINDOW_MAX else "long (a day or more)"


def outcome(s) -> str:
    return "never received" if s.receipt_min is None else ("late" if s.breach else "in time")


def tier(s) -> int:
    return s.tier


def route_kind(s) -> str:
    return s.route


def attribute(name: str, default: str = "unknown") -> Callable:
    """A key for a property the v2 specimens carry (archetype, stratum, disruption_present, edge_tags ...)."""
    return lambda s: getattr(s, name, default)


def mapping_key(mapping: Mapping[str, Hashable], default: str = "unknown") -> Callable:
    """A key from a table the caller holds, specimen id to label (for example the disruption flags in the truth
    log, which reach this module only through the loaders in evaluate.py)."""
    return lambda s: mapping.get(s.specimen_id, default)


def tercile_labels(volume_by_cluster: Mapping[Hashable, float]) -> dict[Hashable, str]:
    """'low', 'middle' and 'high' by rank of each cluster's volume (the extra clusters go to the middle)."""
    order = sorted(volume_by_cluster, key=lambda k: (volume_by_cluster[k], str(k)))
    n = len(order)
    third = n // 3
    labels = {}
    for i, k in enumerate(order):
        labels[k] = "low" if i < third else ("high" if i >= n - third else "middle")
    return labels


def tercile_key(volume_by_cluster: Mapping[Hashable, float], cluster_key: Callable = run_of) -> Callable:
    labels = tercile_labels(volume_by_cluster)
    return lambda s: labels[cluster_key(s)]


SLICE_KEYS = {"window_class": window_class, "outcome": outcome, "tier": tier, "route_kind": route_kind}


def slice_metrics(specs: Sequence, alerts: Mapping[str, float | None], shifts_per_cluster, key: Callable,
                  cluster_key: Callable = run_of) -> dict:
    """The alert metrics for each group `key(spec)` names; a key may return a list, tuple or set to put a specimen
    in several groups (edge tags). Alerts and false alerts count only the group's specimens, and the shifts are
    those of the whole set, as `evaluate.by_group` does."""
    clusters = tuple(sorted({cluster_key(s) for s in specs}))
    groups: dict = {}
    for s in specs:
        k = key(s)
        for g in (k if isinstance(k, (list, tuple, set, frozenset)) else (k,)):
            groups.setdefault(g, []).append(s)
    return {str(g): score(groups[g], alerts, shifts_per_cluster, cluster_key, clusters)
            for g in sorted(groups, key=str)}


# ---------------------------------------------------------------------------------------------------------
# Alert thresholds (§8.3)
# ---------------------------------------------------------------------------------------------------------

def monotone_series(series: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    """The running maximum of each risk series. `evaluate.model_alerts` finds the first crossing with
    np.searchsorted, which needs a non-decreasing series; a risk that can fall would otherwise be searched wrongly.
    The first crossing, the alert tick, is the same."""
    return {k: np.maximum.accumulate(v) if len(v) else v for k, v in series.items()}


def model_alerts(specs: Sequence, series: Mapping[str, np.ndarray], theta: float) -> dict[str, float | None]:
    """`evaluate.model_alerts` on the running maximum of the series."""
    return evaluate.model_alerts(specs, monotone_series({s.specimen_id: series[s.specimen_id] for s in specs}), theta)


def alert_volume(alerts: Mapping[str, float | None], specs: Sequence, shifts: float, per: str = "shift") -> float:
    """Alerts per shift, or per 100 specimens (per='100'), from the alerts alone: no labels."""
    fired = sum(alerts[s.specimen_id] is not None for s in specs)
    if per == "shift":
        return fired / shifts
    if per == "100":
        return 100 * fired / len(specs)
    raise ValueError(f"per must be 'shift' or '100', not {per!r}")


def tune_knob(alerts_at: Callable[[float], Mapping], specs: Sequence, shifts: float, budget: float,
              lo: float, hi: float, steps: int = 30, per: str = "shift", no_alert: float | None = None) -> float:
    """`evaluate.tune`'s bisection from alert volume alone: the smallest knob in [lo, hi] whose volume on `specs`
    stays within the budget (alerts only fall as the knob rises). `shifts` is the total over `specs`. The default of
    30 steps is what evaluate.run uses. With `no_alert`, a knob above every attainable score (it alerts on
    nothing), a budget that even `hi` exceeds is met above it: the bisection goes on in [hi, no_alert], whose top
    always meets it, so the knob is the most aggressive one within the budget (§8.3)."""
    def bisect(lo, hi):
        for _ in range(steps):
            mid = (lo + hi) / 2
            if alert_volume(alerts_at(mid), specs, shifts, per) <= budget:
                hi = mid
            else:
                lo = mid
        return hi
    knob = bisect(lo, hi)
    if no_alert is not None and alert_volume(alerts_at(knob), specs, shifts, per) > budget:
        knob = bisect(knob, no_alert)
    return knob


@dataclass(frozen=True)
class Tuned:
    knob: float
    budget: float
    per: str
    solved_on: float         # alerts per shift (or per 100) at the knob on the set the knob was solved on
    alerts: dict             # alerts on the scored set at that knob
    scored_volume: float     # alerts per shift (or per 100) on the scored set


def tuned_alerts(alert_fn: Callable[[Sequence, float], Mapping], history: Sequence, history_shifts: float,
                 scored: Sequence, scored_shifts: float, budget: float, lo: float, hi: float,
                 steps: int = 30, per: str = "shift", no_alert: float | None = None) -> Tuned:
    """A method's alerts on the scored set with its knob set on the history set. `alert_fn(specs, knob)` returns
    the alerts of a set of specimens at a knob. The knob is solved on `history` from alert volume alone and applied
    to `scored` unchanged. Pass a history set per scenario (§8.3) or the fit runs (legacy_v1)."""
    knob = tune_knob(lambda v: alert_fn(history, v), history, history_shifts, budget, lo, hi, steps, per, no_alert)
    on_history = alert_volume(alert_fn(history, knob), history, history_shifts, per)
    alerts = alert_fn(scored, knob)
    return Tuned(knob, budget, per, on_history, dict(alerts), alert_volume(alerts, scored, scored_shifts, per))


def matched_volume_alerts(alert_fn: Callable[[Sequence, float], Mapping], scored: Sequence, scored_shifts: float,
                          budget: float, lo: float, hi: float, steps: int = 30, per: str = "shift",
                          no_alert: float | None = None) -> Tuned:
    """The sensitivity check of R4: the knob is re-solved on the scored set itself, from alert volume alone, so
    every method alerts at (nearly) the same realized volume there. A reported sensitivity, never a replacement."""
    knob = tune_knob(lambda v: alert_fn(scored, v), scored, scored_shifts, budget, lo, hi, steps, per, no_alert)
    alerts = alert_fn(scored, knob)
    volume = alert_volume(alerts, scored, scored_shifts, per)
    return Tuned(knob, budget, per, volume, dict(alerts), volume)


# ---------------------------------------------------------------------------------------------------------
# Discrimination
# ---------------------------------------------------------------------------------------------------------

def _grouped_weights(y: np.ndarray, s: np.ndarray, w: np.ndarray):
    """Positive and negative weight at each distinct score, ascending."""
    _, inverse = np.unique(s, return_inverse=True)
    k = inverse.max() + 1
    return (np.bincount(inverse, weights=w * y, minlength=k), np.bincount(inverse, weights=w * (1 - y), minlength=k))


def _prepare(y, s, w):
    y, s = np.asarray(y, dtype=float), np.asarray(s, dtype=float)
    w = np.ones_like(y) if w is None else np.asarray(w, dtype=float)
    if not (len(y) == len(s) == len(w)):
        raise ValueError("outcomes, scores and weights must have the same length")
    return y, s, w


def auroc(y, s, w=None) -> float | None:
    """Area under the ROC curve (the Mann-Whitney statistic, ties counting half), with optional case weights.
    None when there is no positive or no negative."""
    y, s, w = _prepare(y, s, w)
    if len(y) == 0:
        return None
    pos, neg = _grouped_weights(y, s, w)
    if pos.sum() <= 0 or neg.sum() <= 0:
        return None
    below = np.cumsum(neg) - neg
    return float((pos * (below + 0.5 * neg)).sum() / (pos.sum() * neg.sum()))


def auprc(y, s, w=None) -> float | None:
    """Average precision, the area under the precision-recall step curve: the sum over thresholds of the gain in
    recall times the precision there, ties taken together. Optional case weights. None without a positive."""
    y, s, w = _prepare(y, s, w)
    if len(y) == 0:
        return None
    pos, neg = _grouped_weights(y, s, w)
    if pos.sum() <= 0:
        return None
    pos, neg = pos[::-1], neg[::-1]          # most suspicious first
    tp, fp = np.cumsum(pos), np.cumsum(neg)
    precision = tp / (tp + fp)
    return float((pos / pos.sum() * precision).sum())


def landmark_rows(specs: Sequence, series: Mapping[str, np.ndarray], landmark: int,
                  tick: int = evaluate.TICK_MIN) -> tuple[np.ndarray, np.ndarray]:
    """Outcome (miss) and score at `landmark` minutes for the specimens still silent then whose window is longer."""
    if landmark % tick:
        raise ValueError(f"the landmark {landmark} is not a scoring tick")
    i = landmark // tick - 1
    rows = [(s.breach, series[s.specimen_id][i]) for s in specs
            if s.window > landmark and (s.receipt_min is None or s.receipt_min > landmark)]
    return np.array([r[0] for r in rows], dtype=float), np.array([r[1] for r in rows], dtype=float)


def pooled_rows(specs: Sequence, series: Mapping[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Outcome, score and weight at every tick of every specimen; a specimen's ticks weigh 1 / its tick count, so
    each specimen counts once however long it stayed silent."""
    ys, ps, ws = [], [], []
    for s in specs:
        p = np.asarray(series[s.specimen_id], dtype=float)
        if len(p):
            ys.append(np.full(len(p), float(s.breach)))
            ps.append(p)
            ws.append(np.full(len(p), 1.0 / len(p)))
    if not ys:
        return np.empty(0), np.empty(0), np.empty(0)
    return np.concatenate(ys), np.concatenate(ps), np.concatenate(ws)


def discrimination(specs: Sequence, series: Mapping[str, np.ndarray],
                   landmarks: Sequence[int] = DISCRIMINATION_LANDMARKS) -> dict:
    """AUROC and AUPRC for a miss at each landmark and pooled over ticks. `series` maps a specimen id to a score
    at every tick: a probability for the models, `rule_scores` for the rules."""
    out = {}
    for m in landmarks:
        y, p = landmark_rows(specs, series, m)
        out[f"landmark_{m}"] = {"landmark_min": m, "n": int(len(y)), "misses": int(y.sum()),
                                "auroc": auroc(y, p), "auprc": auprc(y, p)}
    y, p, w = pooled_rows(specs, series)
    out["pooled"] = {"n_ticks": int(len(y)), "specimens": int(round(w.sum())), "auroc": auroc(y, p, w),
                     "auprc": auprc(y, p, w)}
    return out


def window_rule_scores(specs: Sequence) -> dict[str, np.ndarray]:
    """The window-aware rule's own score at every tick: the share of the window that has passed."""
    return {s.specimen_id: s.ticks / s.window for s in specs}


def quantile_rule_scores(specs: Sequence, quantiles: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    """The quantile rule's own score at every tick: the share of its group's observed collection-to-receipt times
    that are no longer than the elapsed time (the rule alerts once this passes its quantile). `quantiles` is
    `evaluate.baseline_quantiles`, sorted times by group, with the fallback chain of `evaluate.baseline_alerts`."""
    out = {}
    for s in specs:
        times = quantiles[next(name for name in s.chain if name in quantiles)]
        out[s.specimen_id] = np.searchsorted(times, s.ticks, side="right") / len(times)
    return out


# ---------------------------------------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------------------------------------

def expected_calibration_error(y, p, bins: int = ECE_BINS) -> float | None:
    """Mean over equal-mass bins (by sorted prediction) of |mean prediction - observed rate|, weighted by bin size."""
    y, p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    if len(y) == 0:
        return None
    order = np.argsort(p, kind="stable")
    total = 0.0
    for part in np.array_split(order, bins):
        if len(part):
            total += len(part) * abs(p[part].mean() - y[part].mean())
    return float(total / len(y))


def logistic_calibration(y, p, eps: float = 1e-6, iterations: int = 100) -> dict | None:
    """Logistic regression of the outcome on the logit of the prediction, outcome ~ intercept + slope * logit(p),
    by Newton's method. A calibrated model has intercept 0 and slope 1. None when the outcome is constant, the
    predictions are constant, or the fit does not converge (perfect separation)."""
    y = np.asarray(y, dtype=float)
    p = np.clip(np.asarray(p, dtype=float), eps, 1 - eps)
    if len(y) < 2 or y.min() == y.max() or np.ptp(p) == 0:
        return None
    x = np.log(p / (1 - p))
    design = np.column_stack([np.ones_like(x), x])
    beta = np.array([0.0, 1.0])
    for _ in range(iterations):
        mu = 1 / (1 + np.exp(-design @ beta))
        gradient = design.T @ (y - mu)
        hessian = design.T @ (design * (mu * (1 - mu))[:, None])
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            return None
        beta = beta + step
        if np.abs(beta).max() > 1e3:
            return None
        if np.abs(step).max() < 1e-10:
            return {"intercept": float(beta[0]), "slope": float(beta[1])}
    return None


def calibration(y, p, bins: Sequence[float] = evaluate.BINS, ece_bins: int = ECE_BINS) -> dict:
    """Brier score, the Brier score of the base rate, expected calibration error, slope and intercept, and the
    reliability table (`evaluate.reliability`'s bins) for predictions `p` of the outcomes `y`."""
    y, p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    if len(y) == 0:
        return {"n": 0, "breach_rate": None, "brier": None, "brier_of_the_base_rate": None, "ece": None,
                "slope": None, "intercept": None, "bins": []}
    base = y.mean()
    table = []
    for lo, hi in zip(bins, bins[1:]):
        m = (p >= lo) & (p < hi)
        if m.any():
            table.append({"from": lo, "to": min(hi, 1.0), "n": int(m.sum()), "predicted": float(p[m].mean()),
                          "observed": float(y[m].mean())})
    fit = logistic_calibration(y, p)
    return {"n": int(len(y)), "breach_rate": float(base), "brier": float(np.mean((p - y) ** 2)),
            "brier_of_the_base_rate": float(np.mean((base - y) ** 2)),
            "ece": expected_calibration_error(y, p, ece_bins),
            "slope": None if fit is None else fit["slope"], "intercept": None if fit is None else fit["intercept"],
            "bins": table}


def calibration_at(specs: Sequence, series: Mapping[str, np.ndarray], landmarks: Sequence[int] = CALIBRATION_LANDMARKS) -> dict:
    """`calibration` at each landmark, for the specimens still silent then, keyed by the landmark in minutes."""
    out = {}
    for m in landmarks:
        y, p = landmark_rows(specs, series, m)
        out[str(m)] = {"landmark_min": m} | calibration(y, p)
    return out


# ---------------------------------------------------------------------------------------------------------
# The decision rule (§8.6)
# ---------------------------------------------------------------------------------------------------------

RULE_TEXT = """TracerNet is declared better than the baselines only if all four hold on `test` at 6 alerts per shift.

**R1. Warning.** Its median warning over all breaches, a miss counting as zero, exceeds that of the window-aware rule, of the analytic model refit globally and of the analytic model refit locally by at least 5 minutes each, with the lower end of the 95% paired cluster-bootstrap interval of every difference above zero, at both the default 3-2-1 and the equal urgency weights.

**R2. Losses.** Its share of never-received specimens alerted before the window closes is not lower than any of those baselines' by more than 2 percentage points (upper end of the 95% interval of the deficit below 2 points).

**R3. Calibration.** Its Brier score at 30 and at 60 minutes of silence is no worse than the better analytic refit's by more than 0.01 (the rule of `docs/architecture.md` §5.2.1).

**R4. Equal alerts.** Its realized alerts per shift on the scored replicates are within 10% of each baseline's. If not, the comparison is reported as unequal, and R1 is repeated at matched realized volume (knobs re-solved on the scored replicates from alert volume alone, for every method) as a sensitivity check, not a replacement.

If R1 to R3 hold, TracerNet becomes the primary risk model. Otherwise the analytic model stays primary and TracerNet is reported as a challenger result."""

RULES = {name: next(p for p in RULE_TEXT.split("\n\n") if p.startswith(f"**{name}."))
         for name in ("R1", "R2", "R3", "R4")}
MIN_WARNING_GAIN_MIN = 5.0
MAX_LOSS_DEFICIT = 0.02
MAX_BRIER_EXCESS = 0.01
VOLUME_TOLERANCE = 0.10
_EPS = 1e-9  # a margin met exactly is met, whatever the last bit of the subtraction says


def _get(d, *path):
    for p in path:
        if not isinstance(d, Mapping) or p not in d:
            return None
        d = d[p]
    return d


def _check_r1(headline, differences) -> dict:
    checks = []
    for weighting in WEIGHTINGS:
        for base in BASELINES:
            tn = _get(headline, weighting, TRACERNET, "median_warning_all_breaches_min")
            other = _get(headline, weighting, base, "median_warning_all_breaches_min")
            low = _get(differences, weighting, base, "median_warning_all_breaches_min", "low")
            gain = None if tn is None or other is None else tn - other
            held = gain is not None and low is not None and gain >= MIN_WARNING_GAIN_MIN - _EPS and low > 0
            checks.append({"weighting": weighting, "baseline": base, "tracernet_min": tn, "baseline_min": other,
                           "difference_min": gain, "interval_low_min": low, "needs_difference_min": MIN_WARNING_GAIN_MIN,
                           "held": held})
    return {"held": all(c["held"] for c in checks), "checks": checks}


def _check_r2(headline, differences) -> dict:
    """The deficit is the baseline's share alerted minus TracerNet's, which is TracerNet's missed-loss rate minus
    the baseline's: the bootstrap difference of `missed_loss_rate`."""
    checks = []
    for weighting in WEIGHTINGS:
        for base in BASELINES:
            tn = _get(headline, weighting, TRACERNET, "missed_loss_rate")
            other = _get(headline, weighting, base, "missed_loss_rate")
            high = _get(differences, weighting, base, "missed_loss_rate", "high")
            deficit = None if tn is None or other is None else tn - other
            held = deficit is not None and high is not None and high < MAX_LOSS_DEFICIT - _EPS
            checks.append({"weighting": weighting, "baseline": base, "tracernet_share_alerted": None if tn is None else 1 - tn,
                           "baseline_share_alerted": None if other is None else 1 - other, "deficit": deficit,
                           "deficit_interval_high": high, "needs_deficit_below": MAX_LOSS_DEFICIT, "held": held})
    return {"held": all(c["held"] for c in checks), "checks": checks}


def _brier(brier, method, landmark):
    v = _get(brier, method, landmark)
    return v if v is not None else _get(brier, method, str(landmark))


def _check_r3(brier) -> dict:
    checks = []
    for landmark in CALIBRATION_LANDMARKS:
        tn = _brier(brier, TRACERNET, landmark)
        refits = [v for v in (_brier(brier, m, landmark) for m in ("analytic_global", "analytic_local")) if v is not None]
        best = min(refits) if refits else None
        excess = None if tn is None or best is None else tn - best
        checks.append({"landmark_min": landmark, "tracernet_brier": tn, "better_analytic_brier": best,
                       "excess": excess, "allowed_excess": MAX_BRIER_EXCESS,
                       "held": excess is not None and excess <= MAX_BRIER_EXCESS + _EPS})
    return {"held": all(c["held"] for c in checks), "checks": checks}


def _check_r4(headline) -> dict:
    checks = []
    for weighting in WEIGHTINGS:
        for base in BASELINES:
            tn = _get(headline, weighting, TRACERNET, "realized_alerts_per_shift")
            other = _get(headline, weighting, base, "realized_alerts_per_shift")
            if tn is None or other is None:
                ratio, held = None, False
            elif other == 0:
                ratio, held = (0.0 if tn == 0 else None), tn == 0
            else:
                ratio = abs(tn - other) / other
                held = ratio <= VOLUME_TOLERANCE + _EPS
            checks.append({"weighting": weighting, "baseline": base, "tracernet_per_shift": tn, "baseline_per_shift": other,
                           "relative_difference": ratio, "tolerance": VOLUME_TOLERANCE, "held": held})
    return {"held": all(c["held"] for c in checks), "checks": checks}


def decide(headline: Mapping, differences: Mapping, brier: Mapping, matched: Mapping | None = None,
           matched_differences: Mapping | None = None) -> dict:
    """The pre-registered decision rule of §8.6, a pure function of the headline results on `test` at 6 alerts per
    shift. Inputs:

      headline[weighting][method]            `point_metrics` dicts; weighting in WEIGHTINGS, method TRACERNET or one
                                             of BASELINES (the window rule has no weights; give it under both)
      differences[weighting][baseline]       `bootstrap(...)["differences"][baseline]`, TracerNet minus the baseline,
                                             so metric -> {low, high, ...}
      brier[method][landmark]                the Brier score at 30 and 60 minutes of silence for TRACERNET,
                                             "analytic_global" and "analytic_local" (landmark as int or str)
      matched, matched_differences           the same as headline and differences, with every knob re-solved on the
                                             scored replicates (`matched_volume_alerts`); used only if R4 fails

    Returns R1 to R4 each with `held`, the quoted rule text and every check with the numbers that decided it, the
    R1 sensitivity at matched volume when R4 failed, and the verdict. R2 and R4 are checked at both weightings, as
    R1 is. A missing number fails its check. The verdict needs R1 to R3; R4 and the matched-volume repeat are
    reported beside it and never decide it."""
    out = {"R1": _check_r1(headline, differences), "R2": _check_r2(headline, differences),
           "R3": _check_r3(brier), "R4": _check_r4(headline)}
    for name, result in out.items():
        result["rule"] = RULES[name]
    out["R4"]["unequal"] = not out["R4"]["held"]
    if out["R4"]["unequal"]:
        if matched is not None and matched_differences is not None:
            out["R4"]["r1_at_matched_volume"] = _check_r1(matched, matched_differences)
        else:
            out["R4"]["r1_at_matched_volume"] = None   # the driver passes the matched volume in whenever R4 fails
    primary = out["R1"]["held"] and out["R2"]["held"] and out["R3"]["held"]
    out["verdict"] = {"tracernet_primary": primary, "primary_model": "TracerNet" if primary else "analytic model",
                      "text": RULE_TEXT.split("\n\n")[-1], "held": {k: out[k]["held"] for k in ("R1", "R2", "R3", "R4")}}
    return out


# ---------------------------------------------------------------------------------------------------------
# The test lock (§8.7)
# ---------------------------------------------------------------------------------------------------------

class LockError(RuntimeError):
    """A test evaluation whose inputs differ from the locked ones, run without a relock reason."""


HASH_FIELDS = ("artifact_sha256", "calibration_sha256", "configuration_sha256", "split_manifest_sha256",
               "store_sha256", "neural_eval_blob_sha", "evaluate_blob_sha", "decision_model_sha256")
SUBSETS_FIELD = "subsets_sha256"    # the subsets one split's look reports; pinned per split (guard_test_run)
SCENARIOS_FIELD = "scenarios_sha256"    # the scenario selection a look scores (None: the whole set)
_ANY = object()


def subsets_sha256(subsets: Mapping[str, Sequence[str]] | None) -> str:
    """The hash of the named scenario subsets a look reports (none hashes as null)."""
    return sha256_json(_subset_lists(subsets))


def scenarios_sha256(scenarios: Sequence[str] | None) -> str:
    """The hash of the scenario selection a look scores, sorted and without repeats (the whole set hashes as null)."""
    return sha256_json(None if scenarios is None else sorted({str(s) for s in scenarios}))


def sha256_file(path: Path) -> str:
    return store.sha256_path(path)


def sha256_json(obj) -> str:
    """A hash of any JSON-able configuration, independent of key order."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def git_blob_sha(path: Path) -> str:
    """The SHA-1 git gives the content of a file (`git hash-object`), computed without running git."""
    data = Path(path).read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def current_hashes(artifact: str, calibration: str, configuration: str, split_manifest: str, store: str,
                   decision_model: str | None = None, neural_eval_path: Path | None = None,
                   evaluate_path: Path | None = None) -> dict:
    """The record the lock compares: the five hashes the caller computed, the git blob SHAs of this file and of
    evaluate.py, and the decision-model configuration hash (None unless X2 runs on test)."""
    return {"artifact_sha256": artifact, "calibration_sha256": calibration, "configuration_sha256": configuration,
            "split_manifest_sha256": split_manifest, "store_sha256": store,
            "neural_eval_blob_sha": git_blob_sha(neural_eval_path or Path(__file__)),
            "evaluate_blob_sha": git_blob_sha(evaluate_path or Path(evaluate.__file__)),
            "decision_model_sha256": decision_model}


def _now(now) -> str:
    stamp = now if now is not None else datetime.now(EASTERN)
    return stamp if isinstance(stamp, str) else stamp.astimezone(EASTERN).isoformat(timespec="seconds")


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _log(runs_path: Path, entry: dict) -> None:
    runs_path.parent.mkdir(parents=True, exist_ok=True)
    with runs_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


class TestRun(dict):
    """A test run guard_test_run recorded: its log entry, and the context that lets inference and scoring on a
    locked split go ahead. `check(split)` refuses unless the run is for that split, the lock still holds the run's
    hashes and the run is in the log. Pass it to risk_table, Predictor.score and evaluate_split."""
    __test__ = False        # not a pytest class

    def __init__(self, entry: Mapping, lock_path: Path, runs_path: Path, relock_reason: str | None):
        super().__init__(entry)
        self.lock_path, self.runs_path, self.relock_reason = Path(lock_path), Path(runs_path), relock_reason

    def check(self, split: str, *, artifact_dir: Path | None = None, store_dir: Path | None = None,
              scenarios=_ANY) -> None:
        """Refuse unless the run is this split's, still in the lock and the log, and (when given) the artifact,
        the store and the scenario selection being used are the ones its hashes record."""
        self._check_run(split)
        recorded = self["hashes"]
        if scenarios is not _ANY and scenarios_sha256(scenarios) != (recorded.get(SCENARIOS_FIELD)
                                                                    or scenarios_sha256(None)):
            raise LockError("the scenario selection is not the one this test run recorded")
        if artifact_dir is not None:
            manifest_path = Path(artifact_dir) / "manifest.json"
            files = json.loads(manifest_path.read_text(encoding="utf-8")).get("files") or {}
            store.verify_files(Path(artifact_dir), files, "artifact")
            if (sha256_file(manifest_path), files.get("calibration.json")) != (
                    recorded["artifact_sha256"], recorded["calibration_sha256"]):
                raise LockError(f"the artifact at {artifact_dir} is not the one this test run recorded")
        if store_dir is not None and _manifest_hash(Path(store_dir)) != recorded["store_sha256"]:
            raise LockError(f"the store at {store_dir} is not the one this test run recorded")

    def _check_run(self, split: str) -> None:
        if split != self["split"]:
            raise LockError(f"this test run was recorded for {self['split']}, not {split}")
        if not self.lock_path.exists():
            raise LockError(f"no test lock at {self.lock_path}; take it with guard_test_run first")
        locked = json.loads(self.lock_path.read_text(encoding="utf-8"))
        changed = [k for k in HASH_FIELDS if locked.get(k) != self["hashes"][k]]
        if (locked.get("subsets") or {}).get(split) != (self["hashes"].get(SUBSETS_FIELD) or subsets_sha256(None)):
            changed.append("subsets")       # a relock of either split re-pins or clears this split's subsets
        if changed:
            raise LockError(f"the test lock changed since this run was recorded ({', '.join(changed)})")
        logged = self.runs_path.read_text(encoding="utf-8").splitlines() if self.runs_path.exists() else []
        mine = {k: self[k] for k in ("time", "split", "status", "hashes")}
        if not any({k: e.get(k) for k in mine} == mine for e in map(json.loads, logged)):
            raise LockError(f"this test run is not in {self.runs_path}")


def _check_run(test_run, split: str, **inputs) -> None:
    """Refuse inference on a locked split without the run the lock recorded (and, given `artifact_dir` or
    `store_dir`, unless they are the ones it recorded)."""
    check = getattr(test_run, "check", None)
    if not callable(check):
        raise LockError(f"{split} is scored only after the test lock is taken: pass the test run guard_test_run "
                        "recorded")
    check(split, **inputs)


@contextmanager
def _lock_transaction(runs_path: Path):
    """One read-check-write of a test lock at a time, across processes and across nodes (/blue is Lustre mounted
    with cluster-wide flock): the run log is the mutex, since it is only appended to while the lock file itself is
    replaced on every write."""
    runs_path = Path(runs_path)
    runs_path.parent.mkdir(parents=True, exist_ok=True)
    with runs_path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def guard_test_run(split: str, hashes: Mapping, relock_reason: str | None = None, lock_path: Path | None = None,
                   runs_path: Path | None = None, now=None, risk_parts: Mapping | None = None) -> TestRun | None:
    """Call before any inference or scoring on `split`. Other splits are not locked (returns None). For `test` and
    `test_ood`:

      no lock yet          writes test_lock.json from `hashes` and the time, and logs the run as 'first'
      every hash matches   logs the run as 'repeat' and lets it go ahead
      a hash differs       raises LockError naming the fields, unless `relock_reason` is given: then the lock is
                           replaced by the new hashes and the run is logged as 'relock' with the reason, the
                           fields that changed and the previous hashes

    `risk_parts` ({method: {part: SHA-256}}) are the parts of a risk table that already exists; parts the lock
    recorded for this split must match them, as a hash must. Parts written after the lock are recorded with
    record_risk_parts. A relock clears the recorded parts, which belong to the old inputs.

    The scenario subsets a look reports (`hashes[SUBSETS_FIELD]`; test_ood's ood_feature) belong to one split, so
    they are pinned per split like the risk parts, not in the hashes both splits share: a split's first look under
    the lock records its subsets, and a later look at that split must report the same ones.

    Every test run is appended to test_runs.jsonl (the report lists them all). Returns the run (its log entry) as
    a TestRun, the context the locked steps need."""
    if split not in LOCKED_SPLITS:
        return None
    default_lock, default_runs = lock_paths(split)
    lock_path = default_lock if lock_path is None else Path(lock_path)
    runs_path = default_runs if runs_path is None else Path(runs_path)
    if split == FRESH_SPLIT:
        if relock_reason is not None:
            raise LockError("the fresh test set has no relock (docs/neural/preregistration-v2.md §8)")
        done = [p for p in (lock_path.parent / LOOKED_NAME, lock_path.parent / "evaluation.json") if p.exists()]
        if done:
            raise LockError(f"the fresh test set was looked at: {done[0]} exists, and there is no second look "
                            "(docs/neural/preregistration-v2.md §8)")
    missing = [k for k in HASH_FIELDS if k not in hashes]
    if missing:
        raise ValueError(f"the lock record lacks {missing}")
    if relock_reason is not None and not relock_reason.strip():
        raise ValueError("a relock needs a reason")
    new = {k: hashes[k] for k in HASH_FIELDS}
    subsets = hashes.get(SUBSETS_FIELD) or subsets_sha256(None)
    stamp = _now(now)
    entry = {"time": stamp, "split": split,
             "hashes": new | {SUBSETS_FIELD: subsets, SCENARIOS_FIELD: hashes.get(SCENARIOS_FIELD) or scenarios_sha256(None)}}
    if risk_parts:
        entry["risk_parts"] = {m: dict(p) for m, p in risk_parts.items()}
    with _lock_transaction(runs_path):    # read, check and write the lock as one step
        if not lock_path.exists():
            entry |= {"status": "first", "reason": "first evaluation of a test set"}
            _write_atomic(lock_path, json.dumps({"first_split": split, "time": stamp, "risk_parts": {},
                                                 "subsets": {split: subsets}} | new, indent=1, sort_keys=True) + "\n")
        else:
            locked = json.loads(lock_path.read_text(encoding="utf-8"))
            changed = [k for k in HASH_FIELDS if locked.get(k) != new[k]]
            pinned = (locked.get("subsets") or {}).get(split)
            if pinned is not None and pinned != subsets:
                changed.append("subsets")
            recorded = (locked.get("risk_parts") or {}).get(split, {})
            if any(m in recorded and recorded[m] != dict(p) for m, p in (risk_parts or {}).items()):
                changed.append("risk_parts")
            if not changed:
                entry |= {"status": "repeat", "reason": relock_reason or "inputs unchanged"}
                if pinned is None:      # this split's first look under the lock: pin the subsets it reports
                    pins = (locked.get("subsets") or {}) | {split: subsets}
                    _write_atomic(lock_path, json.dumps(locked | {"subsets": pins}, indent=1, sort_keys=True) + "\n")
            elif relock_reason is None:
                raise LockError(f"the test sets were locked at {locked.get('time')} and these changed: {', '.join(changed)}; "
                                "rerun with a relock reason to record the new inputs")
            else:
                entry |= {"status": "relock", "reason": relock_reason, "changed": changed,
                          "previous": {k: locked.get(k) for k in changed}, "locked_at": locked.get("time")}
                _write_atomic(lock_path, json.dumps({"first_split": locked.get("first_split", split), "time": stamp,
                                                     "relocked_from": locked.get("time"), "risk_parts": {},
                                                     "subsets": {split: subsets}} | new,
                                                    indent=1, sort_keys=True) + "\n")
        _log(runs_path, entry)
    return TestRun(entry, lock_path, runs_path, relock_reason)


def lock_paths(split: str) -> tuple[Path, Path]:
    """The lock file and run log a locked split is checked against: the fresh set's own, or the shared one of test
    and test_ood."""
    return (FRESH_LOCK_PATH, FRESH_RUNS_PATH) if split == FRESH_SPLIT else (LOCK_PATH, TEST_RUNS_PATH)


def record_risk_parts(test_run: TestRun, parts: Mapping, now=None) -> None:
    """The SHA-256 of every risk-table part a locked look reads ({method: {part: SHA-256}}). Parts the lock has not
    recorded for this split are recorded in the lock and logged with the run; recorded parts must match, unless the
    run carries a relock reason, which replaces them and is logged as a relock. Otherwise raises LockError."""
    split = test_run["split"]
    with _lock_transaction(test_run.runs_path):
        _check_run(test_run, split)
        locked = json.loads(test_run.lock_path.read_text(encoding="utf-8"))
        recorded = dict((locked.get("risk_parts") or {}).get(split, {}))
        parts = {m: dict(p) for m, p in parts.items()}
        changed = sorted(m for m, p in parts.items() if m in recorded and recorded[m] != p)
        if changed and test_run.relock_reason is None:
            raise LockError(f"the risk table of {', '.join(changed)} on {split} differs from the one recorded at the lock "
                            f"({locked.get('time')}); rerun with a relock reason to record the new table")
        added = {m: p for m, p in parts.items() if m not in recorded or m in changed}
        if not added:
            return
        entry = {"time": _now(now), "split": split, "run": test_run["time"], "risk_parts": added}
        if changed:
            entry |= {"status": "risk table relock", "reason": test_run.relock_reason,
                      "previous": {m: recorded[m] for m in changed}}
        else:
            entry |= {"status": "risk table recorded", "reason": "the parts this run read, recorded once written"}
        recorded.update(added)
        locked["risk_parts"] = dict(locked.get("risk_parts") or {}) | {split: recorded}
        _write_atomic(test_run.lock_path, json.dumps(locked, indent=1, sort_keys=True) + "\n")
        _log(test_run.runs_path, entry)


# ---------------------------------------------------------------------------------------------------------
# The driver: every method of §8.2 on one set of §8.1, end to end
# ---------------------------------------------------------------------------------------------------------
#
# evaluate_split cuts a set into tuning groups. On the v2 sets a group is a scenario: its history replicate
# (replicate 0) sets every knob and its scored replicates are scored. On legacy_v1 the one group is fit runs 0 to
# 23 setting the knobs for runs 24 to 29. Each group runs in a worker: the local refits, every method's score at
# every tick, each knob by bisection on the history from alert volume alone, the alerts on the scored specimens,
# and per-cluster arrays for every metric, slice and landmark. The parent stacks the clusters (scenarios on v2,
# runs on legacy_v1 and the v1-world control) and runs the bootstrap, discrimination, calibration and, when
# TracerNet is among the methods, the decision rule.
#
# Ticks are handled end to end ("flat"): every specimen's ticks in one array, specimen after specimen. A knob
# becomes an alert at the first tick whose value crosses the specimen's threshold; values never fall within a
# specimen, so whether a specimen alerts at all is decided by its last tick, which is what makes the bisection a
# comparison of two vectors.

SCHEME_NAMES = {"default": "3-2-1", "equal": "equal", "steep": "9-3-1"}   # config/triage.yaml's weight schemes
ALL_WEIGHTINGS = ("3-2-1", "equal", "9-3-1")
UNWEIGHTED = "none"                     # the rules have no urgency weights
MIN_LOCAL_FIT = 100                     # specimens with a clock a history needs for its own refit (§8.2)
KNOB_STEPS = 30                         # bisection steps, as evaluate.run
PROBABILITY_SLACK = 1e-5                # a weighted method's risk may leave [0, 1] by this much (float32 rounding)
CONTROL_SCENARIO = "v1-world"           # src/sim/v1world.py's scenario id
DEV_EVAL_DIR = ROOT / "data" / "neural" / "dev-eval"
SETS = {
    "val": "validation scenarios; history replicate 0 sets the knobs, scored replicates are scored",
    "test": "test scenarios, under the test lock; history replicate 0 sets the knobs",
    "test_ood": "out-of-distribution scenarios, under the test lock; history replicate 0 sets the knobs",
    "test_fresh": "the fresh test set of docs/neural/preregistration-v2.md, under its own lock and looked at once; "
                  "history replicate 0 sets the knobs; the global fits come from the training store's train split",
    "legacy_v1": "the v1 store: knobs set on fit runs 0 to 23, held-out runs 24 to 29 scored",
    "control_v1world": "the v1-world scenario: replicate 0 sets the knobs, every other replicate is scored",
}
V2_SPLIT_SETS = ("val", "test", "test_ood", "test_fresh")
OOD_FEATURE = "ood_feature"             # the subset of test_ood whose scenarios carry the feature themselves
OOD_ARCHETYPE, OOD_DISRUPTION = "multi_site_system", "scanner_downtime"   # spec §6.2 (src/scenarios/freeze.py is_ood)
SUBSET_CONTEXT = ("set", "description", "notice", "settings", "methods", "store")
SUBSET_NOTE = ("the same look as the whole set: every knob as tuned there, every metric over this subset's scenarios, "
               "the bootstrap resampling them with the same seed; the decision rule is judged on test only")
SLICE_DIMENSIONS_V2 = ("window_class", "outcome", "tier", "archetype", "stratum", "disruption", "route_kind",
                       "volume_tercile", "edge_tag")
SLICE_DIMENSIONS_V1 = ("window_class", "outcome", "tier", "route_kind", "fault")
NOTICE = ("Synthetic data: laboratory weeks designed by language models and simulated by this project's code, "
          "with no clinical review. Every timing parameter is an assumption.")


@dataclass(frozen=True)
class Budget:
    key: str            # "6/shift" or "10/100"
    value: float
    per: str            # "shift" or "100"


def budget_list(budgets: Sequence[float] = BUDGETS, per_100: Sequence[float] = PER_100_BUDGETS) -> list[Budget]:
    def label(v):
        return f"{int(v) if float(v).is_integer() else v}"
    return ([Budget(f"{label(b)}/shift", float(b), "shift") for b in budgets]
            + [Budget(f"{label(b)}/100", float(b), "100") for b in per_100])


# --- Ticks, end to end ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Flat:
    """The ticks of a list of specimens, specimen after specimen: exactly `Spec.ticks` for each."""
    n: np.ndarray           # ticks per specimen
    start: np.ndarray       # where each specimen's ticks begin; start[-1] is the total
    t: np.ndarray           # minutes since collection at each tick
    owner: np.ndarray       # the specimen of each tick
    window: np.ndarray      # per specimen, minutes
    receipt: np.ndarray     # per specimen, minutes to the receipt scan, NaN if never received
    breach: np.ndarray      # per specimen


def flat_ticks(specs: Sequence) -> Flat:
    k = len(specs)
    window = np.fromiter((s.window for s in specs), float, k)
    receipt = np.fromiter((np.nan if s.receipt_min is None else s.receipt_min for s in specs), float, k)
    breach = np.fromiter((bool(s.breach) for s in specs), bool, k)
    last = np.where(np.isnan(receipt), window, np.minimum(window, np.ceil(receipt) - 1))
    n = np.maximum(0, np.floor_divide(last, evaluate.TICK_MIN)).astype(np.int64)
    start = np.zeros(k + 1, dtype=np.int64)
    np.cumsum(n, out=start[1:])
    owner = np.repeat(np.arange(k), n)
    t = (np.arange(start[-1]) - start[owner] + 1) * float(evaluate.TICK_MIN)
    return Flat(n, start, t, owner, window, receipt, breach)


def _falls(v: np.ndarray, flat: Flat) -> bool:
    return bool(len(v) > 1 and np.any((v[1:] < v[:-1]) & (flat.owner[1:] == flat.owner[:-1])))


def running_max(v: np.ndarray, flat: Flat) -> np.ndarray:
    """np.maximum.accumulate within each specimen, exactly, without a loop: values become ranks, ranks get an
    offset per specimen that no rank can bridge, and one global running maximum does every specimen at once."""
    if not _falls(v, flat):
        return v
    uniq, rank = np.unique(v, return_inverse=True)
    offset = flat.owner.astype(np.int64) * (len(uniq) + 1)
    return uniq[np.maximum.accumulate(offset + rank) - offset]


def _last(v: np.ndarray, flat: Flat) -> np.ndarray:
    """Each specimen's value at its last tick, -inf for a specimen with no tick (it can never alert)."""
    out = np.full(len(flat.n), -np.inf)
    has = flat.n > 0
    out[has] = v[flat.start[1:][has] - 1]
    return out


def _first_alert(mask: np.ndarray, flat: Flat) -> np.ndarray:
    """The minute of each specimen's first tick where `mask` holds, NaN if none. Within a specimen the mask is
    false and then true (values never fall and the threshold is fixed), so the first true tick sits as many ticks
    before the specimen's end as it has true ticks."""
    hits = np.bincount(flat.owner[mask], minlength=len(flat.n))
    out = np.full(len(flat.n), np.nan)
    ok = hits > 0
    out[ok] = flat.t[flat.start[1:][ok] - hits[ok]]
    return out


def as_alerts(specs: Sequence, minutes: np.ndarray) -> dict[str, float | None]:
    """Alert minutes in the mapping form evaluate.py uses (specimen id to minute, None for none)."""
    return {s.specimen_id: (None if np.isnan(m) else float(m)) for s, m in zip(specs, minutes)}


# --- The analytic model, vectorized --------------------------------------------------------------------------

def _key_of(name: str) -> tuple:
    kind, block, priority = name.split(" | ")
    return kind, intervals.BLOCKS.index(block), priority


def interval_rows(specs: Sequence) -> list[tuple]:
    """The rows intervals.load_intervals gives for these specimens (type, hour block, priority, minutes), in their
    order: what a refit on a history replicate needs, without querying the store again."""
    return [(*_key_of(s.group), s.receipt_min) for s in specs]


def complete_groups(params: dict, rows: Sequence, names: Iterable[str]) -> dict:
    """`params` with a fit for every group in `names` it lacks (a group the scored replicates have and the fitted
    rows did not), made as intervals.fit_all makes the fit of a group too small for its own: the first parent in
    its chain with at least MIN_GROUP members, the root being the pooled fit."""
    missing = sorted(set(names) - set(params["groups"]))
    if not missing:
        return params
    out = {**params, "groups": dict(params["groups"])}
    pooled, cache = params["pooled"], {}
    for name in missing:
        for parent in intervals.parent_chain(*_key_of(name)):
            if parent == (None, None, None):
                mu, sigma = pooled["mu"], pooled["sigma"]
                break
            if parent not in cache:
                x = np.array(intervals.members(rows, parent))
                cache[parent] = intervals.fit_normal(x, pooled) if len(x) >= intervals.MIN_GROUP else None
            if cache[parent] is not None:
                mu, sigma = cache[parent]
                break
        out["groups"][name] = {"n": 0, "fitted_from": intervals.key_name(parent), "mu": mu, "sigma": sigma,
                               "median_min": math.exp(mu), "p90_min": math.exp(mu + intervals.Z90 * sigma)}
    return out


def _valid_fit(params: dict) -> bool:
    p = params["pooled"]
    values = [p["mu"], p["sigma"], p["stalled_mu"], p["stalled_sigma"], p["w_stalled"], p["p_never"],
              *p["weights"].values()] + [v for g in params["groups"].values() for v in (g["mu"], g["sigma"])]
    return (all(np.isfinite(v) for v in values) and p["sigma"] > 0 and p["stalled_sigma"] > 0
            and all(0 <= w <= 1 for w in p["weights"].values()) and all(g["sigma"] > 0 for g in params["groups"].values()))


def analytic_risk(specs: Sequence, flat: Flat, params: dict) -> np.ndarray:
    """evaluate.risk_series at every tick, float for float, vectorized: risk.risk_unreceived with the group's
    mixture (risk.mixture_from), 1 from the window on, then the running maximum."""
    pooled, w = params["pooled"], params["pooled"]["weights"]
    w_normal, w_stalled, p_never = w["normal"], w["stalled"], w["never"]
    mu2, s2 = pooled["stalled_mu"], pooled["stalled_sigma"]
    groups = params["groups"]
    mu1 = np.fromiter((groups[s.group]["mu"] for s in specs), float, len(specs))
    s1 = np.fromiter((groups[s.group]["sigma"] for s in specs), float, len(specs))

    def not_received(t, m1, sd1):
        a = w_normal * (1 - ndtr((np.log(t) - m1) / sd1))
        b = w_stalled * (1 - ndtr((np.log(t) - mu2) / s2))
        return p_never + (a + b)

    by_window = not_received(flat.window, mu1, s1)
    o = flat.owner
    with np.errstate(divide="ignore", invalid="ignore"):   # a degenerate local fit; evaluate_group falls back
        r = by_window[o] / not_received(flat.t, mu1[o], s1[o])
    r[flat.t >= flat.window[o]] = 1.0
    return running_max(r, flat)


# --- Methods ---------------------------------------------------------------------------------------------------

class Method:
    """What the driver needs from a method. Every method gets the same specimens, history and ticks.

    weighted       the knob is a harm threshold on (urgency weight of the tier) x score, scored at each weighting;
                   otherwise the method has one knob of its own and `bind(...).thresholds(knob)` turns it into a
                   threshold on the tick minutes (strictly after it when `strict`)
    probabilistic  the score is a probability of a miss, so calibration is reported
    monotone       the score never falls while a specimen stays silent; a method that says False gets the running
                   maximum before the first-crossing search (§8.3), one that says True is checked
    """
    name = ""
    kind = ""
    weighted = False
    probabilistic = False
    monotone = True
    strict = False
    knob = ""
    fitted_on = ""

    def setup(self, ctx: "Setup") -> None:
        """Once per evaluation, in the parent process: global fits."""

    def prepare(self, group: "Group") -> tuple[object, str | None]:
        """Per tuning group, in a worker: the group's own fit, and a note when it fell back."""
        return None, None

    def scores(self, state, specs: Sequence, flat: Flat, group: "Group", role: str) -> np.ndarray:
        """The method's own score at every tick of `flat` (a risk for the models)."""
        raise NotImplementedError

    def fallback(self, state) -> tuple[object, str] | None:
        """When the group's own fit gives a score that is not a number, the state to use instead and why, or None
        (the score is then refused)."""
        return None

    def bind(self, state, specs: Sequence):
        raise NotImplementedError

    def knob_range(self, weights: Mapping | None = None) -> tuple[float, float]:
        return 0.0, float(max(weights.values()))

    def no_alert_knob(self, weights: Mapping | None = None) -> float:
        """A knob above every score the method can give, so it alerts on nothing: the top of §8.3's search on the
        v2 sets when no knob that alerts meets a budget. A weighted method's score is a risk in [0, 1] times a
        weight, so this is just above the largest weight; the window rule's share never passes 1."""
        return float(np.nextafter(self.knob_range(weights)[1], np.inf))

    def describe(self) -> dict:
        return {"kind": self.kind, "weighted": self.weighted, "probabilistic": self.probabilistic,
                "monotone": self.monotone, "knob": self.knob, "fitted_on": self.fitted_on}


class AnalyticModel(Method):
    """The analytic mixture-survival model (src/models/risk.py) refit globally (`intervals.fit_all` on the scored
    replicates of train) or locally (on the scenario's own history replicate, with the global fit used and logged
    when the history has fewer than MIN_LOCAL_FIT specimens with a clock or the fit fails). On legacy_v1 both are
    the committed results/interval_params.json."""
    kind = "analytic"
    weighted = probabilistic = True
    knob = "harm threshold"

    def __init__(self, name: str = "analytic_global", fit: str = "global"):
        if fit not in ("global", "local"):
            raise ValueError(f"fit must be 'global' or 'local', not {fit!r}")
        self.name, self.fit = name, fit
        self.params = None
        self.legacy = False

    def setup(self, ctx):
        self.legacy = ctx.kind == "legacy"
        self.params = ctx.analytic_params()
        self.fitted_on = ctx.fitted_on("analytic", self.fit)

    def prepare(self, group):
        if self.fit == "global" or self.legacy:
            return self.params, None
        rows = group.history_rows
        if len(rows) < MIN_LOCAL_FIT:
            return self.params, f"{len(rows)} specimens with a clock in the history, under {MIN_LOCAL_FIT}: global fit used"
        try:
            params = intervals.fit_all(rows)
        except Exception as e:      # noqa: BLE001 - any failure of the optimizer falls back, and is logged
            return self.params, f"the refit failed ({type(e).__name__}: {e}): global fit used"
        if not _valid_fit(params):
            return self.params, "the refit gave a non-finite or out-of-range parameter: global fit used"
        return complete_groups(params, rows, group.groups), None

    def scores(self, state, specs, flat, group, role):
        return analytic_risk(specs, flat, state)

    def fallback(self, state):
        if self.fit != "local" or state is self.params:
            return None
        return self.params, ("the refit gives a risk that is not a number at some tick (a specimen outlives every "
                             "component of the fitted mixture, whose never-received share is too small): global fit used")


class _WindowBound:
    def __init__(self, specs):
        self.window = np.fromiter((s.window for s in specs), float, len(specs))

    def thresholds(self, share: float) -> np.ndarray:
        return share * self.window


class WindowRule(Method):
    """Alert once a fixed share of the window has passed (triage.window_share_alerts); its score is that share."""
    kind = "window rule"
    knob = "share of the window"
    fitted_on = "nothing"

    def __init__(self, name: str = "window_rule"):
        self.name = name

    def bind(self, state, specs):
        return _WindowBound(specs)

    def knob_range(self, weights=None):
        return 0.0, 1.0

    def scores(self, state, specs, flat, group, role):
        return flat.t / flat.window[flat.owner]


def sorted_quantiles(values: np.ndarray, offsets: np.ndarray, sizes: np.ndarray, q: float) -> np.ndarray:
    """np.quantile(a, q) with numpy's default (linear) method for several sorted arrays at once, float for float:
    array g is values[offsets[g]:offsets[g] + sizes[g]]. The same virtual index, neighbours, gamma and two-sided
    interpolation as numpy, without the partition numpy repeats on every call (tests hold it to np.quantile)."""
    virtual = (sizes - 1) * q
    previous = np.floor(virtual)
    above = virtual >= sizes - 1
    gamma = virtual - previous
    i_prev = np.where(above, sizes - 1, previous).astype(np.int64)
    i_next = np.where(above, sizes - 1, previous + 1).astype(np.int64)
    a, b = values[offsets + i_prev], values[offsets + i_next]
    diff = b - a
    out = a + diff * gamma
    upper = gamma >= 0.5
    out[upper] = (b - diff * (1 - gamma))[upper]
    return out


class _QuantileBound:
    def __init__(self, quantiles: Mapping[str, np.ndarray], specs):
        names = [next(n for n in s.chain if n in quantiles) for s in specs]
        self.names = sorted(set(names))
        index = {n: i for i, n in enumerate(self.names)}
        self.group = np.fromiter((index[n] for n in names), np.int64, len(names))
        self.arrays = [quantiles[n] for n in self.names]
        if any(len(a) == 0 for a in self.arrays):
            raise ValueError("a quantile group has no observed times")
        self.sizes = np.array([len(a) for a in self.arrays], dtype=np.int64)
        self.offsets = np.concatenate([[0], np.cumsum(self.sizes)[:-1]]).astype(np.int64)
        self.values = np.concatenate(self.arrays) if self.arrays else np.empty(0)

    def thresholds(self, q: float) -> np.ndarray:
        if not len(self.arrays):
            return np.empty(0)
        if q > 1.0:                     # QuantileRule.no_alert_knob: past every time, so nothing alerts
            return np.full(len(self.group), np.inf)
        return sorted_quantiles(self.values, self.offsets, self.sizes, q)[self.group]


class QuantileRule(Method):
    """Alert once the elapsed time passes the q-th quantile of the group's observed collection-to-receipt times
    (evaluate.baseline_alerts), the times from the scenario's history replicate (local, with the analytic model's
    fallback) or from the scored replicates of train (global). On legacy_v1 both use the fit runs, as today."""
    kind = "quantile rule"
    strict = True
    knob = "quantile"

    def __init__(self, name: str = "quantile_global", fit: str = "global"):
        if fit not in ("global", "local"):
            raise ValueError(f"fit must be 'global' or 'local', not {fit!r}")
        self.name, self.fit = name, fit
        self.quantiles = None
        self.legacy = False

    def setup(self, ctx):
        self.legacy = ctx.kind == "legacy"
        self.quantiles = ctx.quantiles()
        self.fitted_on = ctx.fitted_on("quantile", self.fit)

    def prepare(self, group):
        if self.fit == "global" or self.legacy:
            return self.quantiles, None
        rows = group.history_rows
        if len(rows) < MIN_LOCAL_FIT or not any(m is not None for *_, m in rows):
            return self.quantiles, f"{len(rows)} specimens with a clock in the history, under {MIN_LOCAL_FIT}: global times used"
        return evaluate.baseline_quantiles(rows), None

    def bind(self, state, specs):
        return _QuantileBound(state, specs)

    def knob_range(self, weights=None):
        return 0.0, 0.9999

    def no_alert_knob(self, weights=None) -> float:
        """Just above the quantile 1, where the threshold is past every time (_QuantileBound.thresholds)."""
        return float(np.nextafter(1.0, np.inf))

    def scores(self, state, specs, flat, group, role):
        bound = _QuantileBound(state, specs)
        out = np.empty(len(flat.t))
        tick_group = bound.group[flat.owner]
        for i, times in enumerate(bound.arrays):
            m = tick_group == i
            out[m] = np.searchsorted(times, flat.t[m], side="right") / len(times)
        return out


class RiskMethod(Method):
    """A method that supplies its own risk series: TracerNet, or anything else scored like it. `series(specs,
    context)` returns, for each specimen, a probability of a miss at each of its ticks (`Spec.ticks`), as
    evaluate.risk_series does; `context` names the set, the store, the scenario (None on legacy_v1), the history
    and scored runs, and the role ('history' or 'scored') of the specimens asked for. Its knob is a harm threshold
    on the tier's weight times the risk, set like the analytic model's. With `monotone=False` (TracerNet's risk can
    fall when the laboratory recovers) the series gets its running maximum before alerts. With workers > 1 the
    method and its `series` must be picklable (a module-level function or a FrameRisk)."""
    kind = "risk series"
    weighted = True
    knob = "harm threshold"

    def __init__(self, name: str, series: Callable, *, monotone: bool = False, probabilistic: bool = True,
                 fitted_on: str = "its own training"):
        self.name, self.series = name, series
        self.monotone, self.probabilistic, self.fitted_on = monotone, probabilistic, fitted_on

    def scores(self, state, specs, flat, group, role):
        got = self.series(specs, group.context | {"role": role})
        parts = []
        for s, n in zip(specs, flat.n):
            v = np.asarray(got[s.specimen_id], dtype=float)
            if len(v) != n:
                raise ValueError(f"{self.name} gave {len(v)} values for {s.specimen_id}, which has {n} ticks")
            parts.append(v)
        return np.concatenate(parts) if parts else np.empty(0)


class FrameRisk:
    """A RiskMethod's `series` from a table of (specimen_id, tick_min, value) rows, such as the parquet file of a
    TracerNet RiskTable (spec §10.3, `p_miss`). The table must hold every tick of every specimen asked for, the
    history replicate's included; rows past a specimen's last tick are ignored."""

    def __init__(self, path: Path | str, value: str = "p_miss"):
        self.path, self.value = str(path), value
        self.files: list[str] | None = None
        self.ranges: list[tuple[str, str, str]] | None = None   # (file, lowest specimen_id, highest)

    def pin(self) -> dict[str, str]:
        """Resolve the table's parts now (a glob or one file), read only these from here on, and return each
        part's SHA-256 by file name: the digest of every part actually read. Each part's specimen_id range is
        noted too, so a lookup reads only the parts that can hold its specimens rather than the whole table."""
        import polars as pl

        p = Path(self.path)
        found = sorted(p.parent.glob(p.name)) if any(c in p.name for c in "*?[") else [p]
        found = [f for f in found if f.is_file()]
        if not found:
            raise FileNotFoundError(f"no risk table at {self.path}")
        self.files = [str(f) for f in found]
        bounds = [pl.scan_parquet(f).select(lo=pl.col("specimen_id").min(), hi=pl.col("specimen_id").max())
                  .collect().row(0) for f in self.files]
        self.ranges = [(f, lo, hi) for f, (lo, hi) in zip(self.files, bounds) if lo is not None]
        return {f.name: sha256_file(f) for f in found}

    def __call__(self, specs: Sequence, context: Mapping) -> dict[str, np.ndarray]:
        import polars as pl

        ids = [s.specimen_id for s in specs if len(s.ticks)]    # a specimen received before its first tick needs no rows
        if not ids:
            return {s.specimen_id: np.array([]) for s in specs}
        files = self.files if self.files is not None else self.path
        if self.ranges is not None:
            first, last = min(ids), max(ids)
            files = [f for f, lo, hi in self.ranges if lo <= last and hi >= first]
            if not files:
                raise ValueError(f"{self.path} lacks {first}")
        frame = (pl.scan_parquet(files)
                 .filter(pl.col("specimen_id").is_in(ids))
                 .select("specimen_id", "tick_min", self.value).collect())
        table = {(sid, int(t)): float(v) for sid, t, v in frame.iter_rows()}
        out = {}
        for s in specs:
            try:
                out[s.specimen_id] = np.array([table[(s.specimen_id, int(t))] for t in s.ticks])
            except KeyError as e:
                raise ValueError(f"{self.path} lacks {s.specimen_id} at tick {e.args[0][1]}") from None
        return out


RISK_DIR = store.ROOT / "data" / "neural" / "risk"


def risk_table(split: str, artifact_dir: Path, *, store_dir: Path | None = None, out_dir: Path | None = None,
               workers: int | None = None, scenarios_per_part: int = 50, test_run: TestRun | None = None,
               scenarios: Sequence[str] | None = None) -> Path:
    """TracerNet's RiskTable for a set: every tick of every specimen of its history and scored replicates, scored
    on CPU by the artifact, written as parquet parts (one per group of scenarios, to bound memory) under
    data/neural/risk/<split>/. Returns the parts' glob, which `tracernet_method` reads. A locked split (test,
    test_ood) is scored only with `test_run`, the run guard_test_run recorded, and the parts' SHA-256 are
    recorded with that run once written (record_risk_parts), so a later look must produce the same table.
    `scenarios` scores a selection of the set's scenarios instead (the ablations' fixed scoring share)."""
    import polars as pl

    from src.models.tracernet.predictor import Predictor

    store_dir = Path(store_dir) if store_dir is not None else store.STORE_V2_DIR
    if split in LOCKED_SPLITS:
        _check_run(test_run, split, artifact_dir=Path(artifact_dir), store_dir=store_dir, scenarios=scenarios)
    out_dir = Path(out_dir) if out_dir is not None else RISK_DIR / split
    con = store.open_observed_v2(store_dir)
    try:
        runs = con.execute("select scenario_id, run from runs where split = ? order by scenario_id, run",
                           [split]).fetchall()
    finally:
        con.close()
    if not runs:
        raise RuntimeError(f"no runs of split {split!r} in {store_dir}")
    by_scenario: dict[str, list[int]] = {}
    for sid, run in runs:
        by_scenario.setdefault(sid, []).append(int(run))
    sids = sorted(by_scenario)
    if scenarios is not None:       # a selection of the set's scenarios (the ablations' scoring share)
        unknown = sorted(set(scenarios) - set(sids))
        if unknown or not scenarios:
            raise ValueError(f"scenarios not among the {split} scenarios of {store_dir}: {unknown[:5]}" if unknown
                             else "an empty scenario selection")
        sids = sorted(set(scenarios))
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.parquet"):
        old.unlink()
    predictor = Predictor.load(artifact_dir, device="cpu")
    keep = ("specimen_id", "run", "scenario_id", "replicate", "tick_min", "p_miss", "harm_default", "harm_equal",
            "ensemble_sd")
    t0 = time.monotonic()
    for i in range(0, len(sids), scenarios_per_part):
        part_runs = [r for sid in sids[i:i + scenarios_per_part] for r in by_scenario[sid]]
        frame = predictor.score(store_dir, part_runs, workers=workers, test_run=test_run)
        frame.select(keep).write_parquet(out_dir / f"part-{i // scenarios_per_part:04d}.parquet")
        print(f"risk {split}: {min(i + scenarios_per_part, len(sids))} of {len(sids)} scenarios scored "
              f"({time.monotonic() - t0:.0f} s)", flush=True)
    if split in LOCKED_SPLITS:
        record_risk_parts(test_run, {TRACERNET: FrameRisk(out_dir / "*.parquet").pin()})
    return out_dir / "*.parquet"


def baselines() -> list[Method]:
    """The baselines of spec §8.2."""
    return [AnalyticModel("analytic_global", "global"), AnalyticModel("analytic_local", "local"), WindowRule(),
            QuantileRule("quantile_local", "local"), QuantileRule("quantile_global", "global")]


WEAKSPOT_ROWS = ROOT / "data" / "neural" / "weakspots" / "val-rows.parquet"
SCENARIO_CELLS = ROOT / "data" / "neural" / "scenario-cells.json"


def weakspot_rows(store_dir: Path | None = None):
    """Spec §4.12's rows for the weak-spot analysis (src/neural/weakspots.py, which may not read labels itself):
    every scored validation specimen with a clock, its breach label and its slice attributes. Validation only."""
    import polars as pl
    specs, _ = evaluate.load_specs_v2("val", ("scored",), store_dir, labels=True)
    types = {}
    for sid in sorted({s.scenario_id for s in specs}):
        for d in evaluate.truth_disruptions_v2(sid, store_dir=store_dir):
            types[(sid, int(d["replicate"]), d["disruption_id"])] = d["type"]
    return pl.DataFrame({
        "specimen_id": [s.specimen_id for s in specs], "scenario_id": [s.scenario_id for s in specs],
        "run": [s.run for s in specs], "breach": [s.breach for s in specs], "window_min": [s.window for s in specs],
        "receipt_min": [s.receipt_min for s in specs], "route_kind": [s.route_kind or s.route for s in specs],
        "archetype": [s.archetype for s in specs], "stratum": [s.stratum for s in specs],
        "edge_tags": [list(s.edge_tags) for s in specs],
        "disruption_types": [sorted({types[(s.scenario_id, s.replicate, i)] for i in s.disruption_ids}) for s in specs]},
        schema_overrides={"edge_tags": pl.List(pl.Utf8), "disruption_types": pl.List(pl.Utf8),
                          "receipt_min": pl.Float64})


def write_weakspot_rows(path: Path = WEAKSPOT_ROWS, store_dir: Path | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    weakspot_rows(store_dir).write_parquet(path)
    return path


def write_scenario_cells(path: Path = SCENARIO_CELLS, store_dir: Path | None = None) -> Path:
    """The train and validation scenarios' planned cells, for the ablations' data subsets (never a test split)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evaluate.scenario_cells_v2(("train", "val"), store_dir), indent=1, sort_keys=True) + "\n")
    return path


def tracernet_method(risk_table: Path | str, value: str = "p_miss") -> RiskMethod:
    """TracerNet as a method, from its RiskTable written as parquet (every tick of the history and scored
    replicates of the set): `evaluate_split(split, baselines() + [tracernet_method(path)])`."""
    return RiskMethod(TRACERNET, FrameRisk(risk_table, value), monotone=False, probabilistic=True,
                      fitted_on="train (TracerNet artifact)")


# --- The parent's global fits --------------------------------------------------------------------------------

class Setup:
    """What the parent fits once and the methods copy (the train rows themselves never go to a worker)."""

    def __init__(self, kind: str, split: str, store_dir: Path, train_store_dir: Path, start: str, stop: str,
                 policy_hash: str, groups: Iterable[str] = ()):
        self.kind, self.split, self.store_dir, self.train_store_dir = kind, split, store_dir, train_store_dir
        self.start, self.stop, self.policy_hash, self.groups = start, stop, policy_hash, set(groups)
        self._rows = self._params = self._quantiles = None

    def rows(self) -> list[tuple]:
        if self._rows is None:
            if self.kind == "legacy":
                self._rows = intervals.load_intervals(store.open_observed(self.store_dir), self.start, self.stop)
            else:
                self._rows = intervals.load_intervals(store.open_observed_v2(self.train_store_dir), self.start,
                                                      self.stop, split="train", roles=["scored"], scored=True)
            if not self._rows:
                raise RuntimeError("no rows to fit the global baselines on")
        return self._rows

    def analytic_params(self) -> dict:
        if self._params is None:
            if self.kind == "legacy":
                params = json.loads(intervals.PARAMS_PATH.read_text(encoding="utf-8"))
                if params["policy_hash"] != self.policy_hash:
                    raise RuntimeError("the fitted parameters were made under a different policy; refit before scoring")
                missing = self.groups - set(params["groups"])
                if missing:
                    raise RuntimeError(f"results/interval_params.json lacks the groups {sorted(missing)[:5]}")
            else:
                params = complete_groups(intervals.fit_all(self.rows()), self.rows(), self.groups)
                if not _valid_fit(params):
                    raise RuntimeError("the global refit on train gave a non-finite or out-of-range parameter")
            self._params = params
        return self._params

    def quantiles(self) -> dict:
        if self._quantiles is None:
            self._quantiles = evaluate.baseline_quantiles(self.rows())
        return self._quantiles

    def fitted_on(self, kind: str, fit: str) -> str:
        if self.kind == "legacy":
            return ("results/interval_params.json (fit runs 0 to 23)" if kind == "analytic"
                    else "collection-to-receipt times of fit runs 0 to 23")
        if fit == "local":
            return (f"the scenario's history replicate (global fit under {MIN_LOCAL_FIT} specimens with a clock "
                    "or if the refit fails)")
        return "the scored replicates of train"


# --- Tuning groups ---------------------------------------------------------------------------------------------

@dataclass
class Group:
    key: str
    history: list
    scored: list
    history_shifts: float
    clusters: tuple                  # the clusters of the scored specimens
    cluster_of: np.ndarray           # per scored specimen, its index in `clusters`
    shifts: dict                     # cluster -> shifts
    volume: dict                     # cluster -> scored specimens with a clock per replicate (the tercile slice)
    context: dict

    @property
    def history_rows(self) -> list[tuple]:
        return interval_rows(self.history)

    @property
    def groups(self) -> set:
        return {s.group for s in self.history} | {s.group for s in self.scored}


def _group(key, history, scored, by_run: bool, context: dict) -> Group:
    cluster_key = (lambda s: s.run) if by_run else (lambda s: s.scenario_id)
    clusters = tuple(sorted({cluster_key(s) for s in scored}))
    index = {c: i for i, c in enumerate(clusters)}
    cluster_of = np.fromiter((index[cluster_key(s)] for s in scored), np.int64, len(scored))
    runs_of = {c: {s.run for s in scored if cluster_key(s) == c} for c in clusters}
    shifts = {c: float(len(runs_of[c]) * SHIFTS_PER_REPLICATE) for c in clusters}
    volume = {c: float(np.sum(cluster_of == index[c])) / len(runs_of[c]) for c in clusters}
    history_runs = sorted({s.run for s in history})
    return Group(key, history, scored, float(len(history_runs) * SHIFTS_PER_REPLICATE), clusters, cluster_of,
                 shifts, volume, context | {"history_runs": history_runs, "scored_runs": sorted({s.run for s in scored})})


@dataclass(frozen=True)
class Env:
    """What every worker needs, besides the methods."""
    set_name: str
    kind: str                       # "v2" or "legacy"
    store_split: str | None
    store_dir: str
    by_run: bool                    # clusters are runs (legacy_v1, the control), else scenarios
    schemes: tuple                  # ((weighting name, {tier: weight}), ...)
    budgets: tuple                  # Budget, ...
    headline: str                   # the budget key the slices and matched volume use
    slice_dimensions: tuple
    matched: bool                   # matched volume was asked for
    keep_alerts: bool
    matched_parts: bool = False     # the workers compute matched volume (asked for, or R4 may need it)


def _v2_groups(env: Env, scenario_ids: Sequence[str]) -> tuple[list[Group], dict]:
    d = Path(env.store_dir)
    scored, sc = evaluate.load_specs_v2(env.store_split, ("scored",), d, labels=True, scenarios=scenario_ids)
    history, hc = evaluate.load_specs_v2(env.store_split, ("history",), d, labels=False, scenarios=scenario_ids)
    by_h, by_s = {}, {}
    for s in history:
        by_h.setdefault(s.scenario_id, []).append(s)
    for s in scored:
        by_s.setdefault(s.scenario_id, []).append(s)
    groups = [_group(sid, by_h.get(sid, []), by_s[sid], env.by_run,
                     {"set": env.set_name, "store_dir": env.store_dir, "scenario_id": sid})
              for sid in scenario_ids if sid in by_s]
    counts = {"scored_without_a_clock": sc["without_a_clock"], "history_without_a_clock": hc["without_a_clock"]}
    return groups, counts


# --- One group, in a worker ------------------------------------------------------------------------------------

def _part(flat: Flat, members: np.ndarray, alerts: np.ndarray, shifts: float) -> tuple:
    """One cluster's row of a Series for the specimens `members`, exactly as build_series makes it."""
    a, breach, window = alerts[members], flat.breach[members], flat.window[members]
    never = np.isnan(flat.receipt[members])
    fired = ~np.isnan(a)
    lead = window - a
    caught_all = fired & (lead > 0)
    w = np.where(caught_all[breach], lead[breach], 0.0)
    c = caught_all[breach]
    n = never[breach]
    scalars = (len(members), len(w), c.sum(), n.sum(), (n & ~c).sum(), fired.sum(), (fired & ~breach).sum(),
               (w >= GOOD_WARNING_MIN).sum(), np.minimum(w, WARNING_CAP_MIN).sum(), shifts)
    return np.array(scalars, dtype=float), w, c


def _slice_labels(specs: Sequence, dimension: str) -> list[tuple]:
    if dimension == "window_class":
        return [(window_class(s),) for s in specs]
    if dimension == "outcome":
        return [(outcome(s),) for s in specs]
    if dimension == "tier":
        return [(str(s.tier),) for s in specs]
    if dimension == "route_kind":
        return [(getattr(s, "route_kind", "") or s.route,) for s in specs]
    if dimension in ("archetype", "stratum"):
        return [(getattr(s, dimension, "") or "unknown",) for s in specs]
    if dimension == "disruption":
        return [("disrupted" if getattr(s, "disruption_ids", ()) else "undisrupted",) for s in specs]
    if dimension == "edge_tag":
        return [tuple(getattr(s, "edge_tags", ())) or ("none",) for s in specs]
    if dimension == "fault":                    # v1's by_fault: the breaches, by planted fault
        return [(s.fault,) if s.breach else () for s in specs]
    raise ValueError(f"unknown slice dimension {dimension!r}")


def _bisect(fires: Callable, n: int, shifts: float, budget: Budget, lo: float, hi: float) -> float:
    """tune_knob's bisection with the volume counted from the specimens that fire at all (their last tick)."""
    for _ in range(KNOB_STEPS):
        mid = (lo + hi) / 2
        fired = int(np.count_nonzero(fires(mid)))
        volume = fired / shifts if budget.per == "shift" else (100 * fired / n if n else 0.0)
        if volume <= budget.value:
            hi = mid
        else:
            lo = mid
    return hi


def _volume(fired: int, n: int, shifts: float, per: str) -> float:
    if per == "shift":
        return fired / shifts if shifts else 0.0
    return 100 * fired / n if n else 0.0


def _tune(fires: Callable, n: int, shifts: float, budget: Budget, lo: float, hi: float, none: float | None) -> float:
    """The knob of §8.3: the most aggressive one whose volume stays within the budget. `_bisect` on [lo, hi]; on the
    v2 sets (`none`, the method's no-alert knob, given) a budget that even `hi` exceeds is met above it, by going on
    in [hi, none], whose top alerts on nothing. On legacy_v1 (`none` None) the knob is evaluate.py's, as it was."""
    knob = _bisect(fires, n, shifts, budget, lo, hi)
    if none is not None and _volume(int(np.count_nonzero(fires(knob))), n, shifts, budget.per) > budget.value:
        knob = _bisect(fires, n, shifts, budget, knob, none)
    return knob


class _Crossing:
    """A method at one weighting as values per tick and a threshold per specimen for each knob."""

    def __init__(self, values: np.ndarray, flat: Flat, thresholds: Callable, strict: bool):
        self.values, self.flat, self.thresholds, self.strict = values, flat, thresholds, strict
        self.last = _last(values, flat)

    def _cmp(self, v, thr):
        return v > thr if self.strict else v >= thr

    def fires(self, knob: float) -> np.ndarray:
        return self._cmp(self.last, self.thresholds(knob))

    def alerts(self, knob: float) -> np.ndarray:
        thr = self.thresholds(knob)
        thr = thr[self.flat.owner] if np.ndim(thr) else thr
        return _first_alert(self._cmp(self.values, thr), self.flat)


def _scored_rows(flat: Flat, score: np.ndarray) -> dict:
    """The discrimination and calibration inputs of one method on one group: the outcome and score of every
    specimen still silent at each landmark whose window is longer, and the score at every tick grouped by value
    with the summed weight of misses and non-misses (each specimen weighing 1 in all, spread over its ticks)."""
    out = {"landmarks": {}}
    for m in sorted(set(DISCRIMINATION_LANDMARKS) | set(CALIBRATION_LANDMARKS)):
        rows = (flat.window > m) & (np.isnan(flat.receipt) | (flat.receipt > m))
        idx = flat.start[:-1][rows] + m // evaluate.TICK_MIN - 1
        out["landmarks"][m] = (flat.breach[rows].astype(float), score[idx])
    if len(score):
        uniq, inv = np.unique(score, return_inverse=True)
        w = 1.0 / flat.n[flat.owner]
        y = flat.breach[flat.owner].astype(float)
        out["pooled"] = (uniq, np.bincount(inv, weights=w * y, minlength=len(uniq)),
                         np.bincount(inv, weights=w * (1 - y), minlength=len(uniq)), int(np.count_nonzero(flat.n)),
                         len(score))
    else:
        out["pooled"] = (np.empty(0), np.empty(0), np.empty(0), 0, 0)
    return out


def _series_keys(m: Method, env: Env) -> list[tuple[str, str]]:
    return [(m.name, w) for w, _ in env.schemes] if m.weighted else [(m.name, UNWEIGHTED)]


def _checked(values, flat: Flat, m: Method) -> np.ndarray:
    v = np.asarray(values, dtype=float)
    if v.shape != flat.t.shape:
        raise ValueError(f"{m.name} gave {v.shape[0] if v.ndim else 0} scores for {len(flat.t)} ticks")
    if not np.all(np.isfinite(v)):
        raise ValueError(f"{m.name} gave a score that is not a finite number")
    if m.weighted and len(v):
        if v.min() < -PROBABILITY_SLACK or v.max() > 1.0 + PROBABILITY_SLACK:
            raise ValueError(f"{m.name} gave a risk outside [0, 1]; its knob range and no-alert knob assume a "
                             "probability")
        v = np.clip(v, 0.0, 1.0)    # float32 rounding: a network's p_miss can pass 1 by about 1e-7
    return v


def evaluate_group(group: Group, methods: Sequence[Method], env: Env) -> dict:
    """Everything one tuning group contributes: per series and budget the knob, the history and scored volume,
    whether only the no-alert knob met the budget, and each cluster's row (warnings, caught flags, counts); slices
    at the headline budget; matched volume when the parent may need it; the discrimination and calibration rows of
    every method."""
    H, S = group.history, group.scored
    fh, fs = flat_ticks(H), flat_ticks(S)
    members = [np.flatnonzero(group.cluster_of == i) for i in range(len(group.clusters))]
    dims = {d: _slice_labels(S, d) for d in env.slice_dimensions if d != "volume_tercile"}
    slice_members = {}
    for d, labels in dims.items():
        found: dict = {}
        for i, labs in enumerate(labels):
            for g in labs:
                found.setdefault(g, []).append(i)
        slice_members[d] = {g: np.array(ix, dtype=np.int64) for g, ix in found.items()}
    out = {"key": group.key, "clusters": list(group.clusters), "shifts": group.shifts, "volume": group.volume,
           "counts": {"history": len(H), "scored": len(S), "breaches": int(fs.breach.sum()),
                      "never_received": int(np.isnan(fs.receipt).sum()), "history_shifts": group.history_shifts,
                      "scored_shifts": float(sum(group.shifts.values()))},
           "notes": [], "parts": {}, "knobs": {}, "no_alert": {}, "slices": {}, "matched": {}, "rows": {},
           "alerts": {}}
    scored_shifts = out["counts"]["scored_shifts"]
    v2 = env.kind == "v2"           # legacy_v1 keeps evaluate.py's knobs (§8.3)

    def parts_for(alerts):
        return [_part(fs, ix, alerts, group.shifts[c]) for c, ix in zip(group.clusters, members)]

    for m in methods:
        state, note = m.prepare(group)
        raw_h, raw_s = m.scores(state, H, fh, group, "history"), m.scores(state, S, fs, group, "scored")
        if not (np.all(np.isfinite(raw_h)) and np.all(np.isfinite(raw_s))):
            back = m.fallback(state)
            if back is not None:    # the same fit for history and scored replicates, so tuning stays consistent
                state, note = back
                raw_h, raw_s = m.scores(state, H, fh, group, "history"), m.scores(state, S, fs, group, "scored")
        if note:
            out["notes"].append({"method": m.name, "group": group.key, "note": note})
        raw_h, raw_s = _checked(raw_h, fh, m), _checked(raw_s, fs, m)
        out["rows"][m.name] = _scored_rows(fs, raw_s)
        crossings = {}
        if m.weighted:
            if m.monotone:
                if _falls(raw_h, fh) or _falls(raw_s, fs):
                    raise ValueError(f"{m.name} says its score never falls, but it does; pass monotone=False")
                mono_h, mono_s = raw_h, raw_s
            else:
                mono_h, mono_s = running_max(raw_h, fh), running_max(raw_s, fs)
            for wname, weights in env.schemes:
                wh = np.fromiter((weights[s.tier] for s in H), float, len(H))
                ws = np.fromiter((weights[s.tier] for s in S), float, len(S))
                identity = lambda k: k   # noqa: E731 - the knob is the threshold itself
                crossings[wname] = (_Crossing(wh[fh.owner] * mono_h, fh, identity, False),
                                    _Crossing(ws[fs.owner] * mono_s, fs, identity, False), m.knob_range(weights),
                                    m.no_alert_knob(weights) if v2 else None)
        else:
            bh, bs = m.bind(state, H), m.bind(state, S)
            crossings[UNWEIGHTED] = (_Crossing(fh.t, fh, bh.thresholds, m.strict),
                                     _Crossing(fs.t, fs, bs.thresholds, m.strict), m.knob_range(),
                                     m.no_alert_knob() if v2 else None)
        for wname, (ch, cs, (lo, hi), none) in crossings.items():
            key = f"{m.name}|{wname}"
            for b in env.budgets:
                knob = _tune(ch.fires, len(H), group.history_shifts, b, lo, hi, none)
                on_history = _volume(int(np.count_nonzero(ch.fires(knob))), len(H), group.history_shifts, b.per)
                alerts = cs.alerts(knob)
                fired = int(np.count_nonzero(~np.isnan(alerts)))
                out["knobs"].setdefault(b.key, {})[key] = (knob, on_history, _volume(fired, len(S), scored_shifts, b.per))
                out["no_alert"].setdefault(b.key, {})[key] = none is not None and knob >= none
                out["parts"].setdefault(b.key, {})[key] = parts_for(alerts)
                if env.keep_alerts:
                    out["alerts"].setdefault(b.key, {})[key] = as_alerts(S, alerts)
                if b.key != env.headline:
                    continue
                for d, groups_of in slice_members.items():
                    for g, ix in groups_of.items():
                        cl = group.cluster_of[ix]
                        out["slices"].setdefault(d, {}).setdefault(g, {})[key] = [
                            _part(fs, ix[cl == i], alerts, group.shifts[c]) for i, c in enumerate(group.clusters)]
                if env.matched_parts:
                    mk = _tune(cs.fires, len(S), scored_shifts, b, lo, hi, none)
                    ma = cs.alerts(mk)
                    out["matched"][key] = {"knob": mk, "parts": parts_for(ma), "no_alert": none is not None and mk >= none,
                                           "volume": _volume(int(np.count_nonzero(~np.isnan(ma))), len(S), scored_shifts, b.per)}
    return out


_WORKER: dict = {}


def _init_worker(methods, env) -> None:
    _WORKER["methods"], _WORKER["env"] = methods, env


def _run_batch(scenario_ids: Sequence[str]) -> tuple[list[dict], dict]:
    methods, env = _WORKER["methods"], _WORKER["env"]
    groups, counts = _v2_groups(env, scenario_ids)
    return [evaluate_group(g, methods, env) for g in groups], counts


# --- The parent: stacking, intervals, discrimination, calibration ---------------------------------------------

def _stack(clusters: Sequence, parts_by_cluster: Mapping, shifts: Mapping) -> Series:
    """A Series over every cluster from each cluster's row; a cluster with no row (a slice it lacks) adds its
    shifts and nothing else, as slice_metrics does."""
    scalars, warnings, caught = [], [], []
    for c in clusters:
        if c in parts_by_cluster:
            s, w, k = parts_by_cluster[c]
        else:
            s = np.zeros(len(COLUMNS))
            s[_C["shifts"]] = shifts[c]
            w, k = np.empty(0), np.empty(0, dtype=bool)
        scalars.append(s)
        warnings.append(w)
        caught.append(k)
    return Series(tuple(clusters), np.array(scalars).reshape(len(clusters), len(COLUMNS)), tuple(warnings), tuple(caught))


def _summary(values: Sequence[float]) -> dict:
    v = np.asarray(values, dtype=float)
    if len(v) == 0:
        return {"min": None, "median": None, "max": None}
    return {"min": float(v.min()), "median": float(np.median(v)), "max": float(v.max())}


def _nest(flat_keys: Mapping[str, object]) -> dict:
    """{"method|weighting": x} as {method: {weighting: x}}."""
    out: dict = {}
    for key, v in flat_keys.items():
        name, w = key.split("|")
        out.setdefault(name, {})[w] = v
    return out


def _auc_from_groups(uniq, pos, neg) -> tuple:
    if len(uniq) == 0:
        return None, None
    y = np.concatenate([np.ones(len(uniq)), np.zeros(len(uniq))])
    s = np.concatenate([uniq, uniq])
    w = np.concatenate([pos, neg])
    return auroc(y, s, w), auprc(y, s, w)


def _discrimination_from_rows(rows: Sequence[dict]) -> dict:
    out = {}
    for m in DISCRIMINATION_LANDMARKS:
        y = np.concatenate([r["landmarks"][m][0] for r in rows]) if rows else np.empty(0)
        p = np.concatenate([r["landmarks"][m][1] for r in rows]) if rows else np.empty(0)
        out[f"landmark_{m}"] = {"landmark_min": m, "n": int(len(y)), "misses": int(y.sum()),
                                "auroc": auroc(y, p), "auprc": auprc(y, p)}
    s = np.concatenate([r["pooled"][0] for r in rows]) if rows else np.empty(0)
    pos = np.concatenate([r["pooled"][1] for r in rows]) if rows else np.empty(0)
    neg = np.concatenate([r["pooled"][2] for r in rows]) if rows else np.empty(0)
    if len(s):
        uniq, inv = np.unique(s, return_inverse=True)
        pos, neg, s = np.bincount(inv, weights=pos), np.bincount(inv, weights=neg), uniq
    a, p = _auc_from_groups(s, pos, neg)
    out["pooled"] = {"n_ticks": int(sum(r["pooled"][4] for r in rows)), "specimens": int(sum(r["pooled"][3] for r in rows)),
                     "auroc": a, "auprc": p}
    return out


def _calibration_from_rows(rows: Sequence[dict]) -> dict:
    out = {}
    for m in CALIBRATION_LANDMARKS:
        y = np.concatenate([r["landmarks"][m][0] for r in rows]) if rows else np.empty(0)
        p = np.concatenate([r["landmarks"][m][1] for r in rows]) if rows else np.empty(0)
        out[str(m)] = {"landmark_min": m} | calibration(y, p)
    return out


def _out_dir(split: str, store_dir: Path, kind: str) -> Path:
    if split == FRESH_SPLIT:
        return FRESH_DIR
    real = kind == "legacy" or Path(store_dir).resolve() == store.STORE_V2_DIR.resolve()
    return (NEURAL_DIR if real else DEV_EVAL_DIR) / split


def _leaf_groups(store_dir: Path, start: str, stop: str, split: str | None, scenarios) -> set[str]:
    """The (type, hour block, priority) groups of a v2 set's specimens with a clock, history included."""
    con = store.open_observed_v2(store_dir)
    where, params = ["s.scored"], [start]
    if split is not None:
        where.append("s.split = ?")
        params.append(split)
    if scenarios is not None:
        where.append("list_contains(?, s.scenario_id)")
        params.append([str(x) for x in scenarios])
    rows = con.execute(f"""select distinct s.specimen_type, hour(a.timestamp) // {intervals.BLOCK_HOURS}, s.priority
                           from specimens s join events a on a.specimen_id = s.specimen_id and a.activity = ?
                           where {" and ".join(where)}""", params).fetchall()
    return {intervals.key_name((t, int(b), p)) for t, b, p in rows}


def _set_scenarios(split: str, store_dir: Path, control_scenario: str, scenarios) -> tuple[str | None, list[str]]:
    con = store.open_observed_v2(store_dir)
    if split == "control_v1world":
        where = [r[0] for r in con.execute("select distinct split from specimens where scenario_id = ?",
                                           [control_scenario]).fetchall()]
        if not where:
            raise RuntimeError(f"the store at {store_dir} holds no scenario {control_scenario!r} for the control")
        if set(where) & set(LOCKED_SPLITS):
            raise LockError(f"{control_scenario!r} sits in a locked split ({', '.join(where)}); it cannot be the control")
        return None, [control_scenario]
    found = sorted(r[0] for r in con.execute("select distinct scenario_id from specimens where split = ?",
                                             [split]).fetchall())
    if scenarios is not None:
        unknown = sorted(set(scenarios) - set(found))
        if unknown:
            raise ValueError(f"scenarios not in {split}: {unknown[:5]}")
        found = [s for s in found if s in set(scenarios)]
    if not found:
        raise RuntimeError(f"the store at {store_dir} has no scenario in {split}")
    return split, found


def ood_feature_scenarios(store_dir: Path, split: str = "test_ood") -> list[str]:
    """The scenarios of a split that carry the out-of-distribution feature themselves (spec §6.2's rule): archetype
    multi_site_system, or a scanner_downtime disruption in the configuration. Families go to test_ood whole, so the
    rest of test_ood are ordinary weeks that share a family with one of these."""
    traits = evaluate.scenario_traits_v2(split, store_dir)
    return sorted(s for s, t in traits.items()
                  if t["archetype"] == OOD_ARCHETYPE or OOD_DISRUPTION in t["disruption_types"])


def _subset_lists(subsets: Mapping[str, Sequence[str]] | None) -> dict | None:
    """Named scenario lists by name, each sorted and without repeats; None for none."""
    if not subsets:
        return None
    out = {}
    for name in sorted(subsets):
        ids = subsets[name]
        if not isinstance(name, str) or not name or isinstance(ids, str):
            raise ValueError("subsets map a name to a list of scenario ids")
        out[name] = sorted({str(s) for s in ids})
        if not out[name]:
            raise ValueError(f"the subset {name!r} names no scenario")
    return out


def _without_a_clock(store_dir: Path, split: str, scenarios: Sequence[str], start: str, stop: str) -> dict:
    """load_specs_v2's counts of scored-day specimens without a clock, history and scored, over some scenarios of a
    split (a subset's counts), from the observed store."""
    where = "s.split = ? and s.scored and list_contains(?, s.scenario_id)"
    params = [split, [str(s) for s in scenarios]]
    con = store.open_observed_v2(store_dir)
    try:
        total = dict(con.execute(f"select s.role, count(*) from specimens s where {where} group by s.role",
                                 params).fetchall())
        clock = dict(con.execute(f"""select s.role, count(*) from specimens s
                                     join events a on a.specimen_id = s.specimen_id and a.activity = ?
                                     left join events b on b.specimen_id = s.specimen_id and b.activity = ?
                                     where {where} group by s.role""", [start, stop, *params]).fetchall())
    finally:
        con.close()
    return {f"{role}_without_a_clock": int(total.get(role, 0) - clock.get(role, 0)) for role in ("scored", "history")}


def subset_view(result: Mapping, name: str) -> dict:
    """A subset of an evaluation read as an evaluation of its own: the look's context with the subset's sections."""
    return {k: v for k, v in result.items() if k in SUBSET_CONTEXT} | dict(result["subsets"][name])


def evaluate_split(split: str, methods: Sequence[Method] | None = None, *, store_dir: Path | None = None,
                   budgets: Sequence[float] = BUDGETS, per_100_budgets: Sequence[float] = PER_100_BUDGETS,
                   out_dir: Path | None = None, workers: int | None = None, matched: bool = False,
                   focus: str | None = None, train_store_dir: Path | None = None, scenarios: Sequence[str] | None = None,
                   control_scenario: str = CONTROL_SCENARIO, lock_hashes: Mapping | None = None,
                   relock_reason: str | None = None, lock_path: Path | None = None, runs_path: Path | None = None,
                   resamples: int = BOOTSTRAPS, keep_alerts: bool = False, write: bool = True,
                   test_run: TestRun | None = None, subsets: Mapping[str, Sequence[str]] | None = None) -> dict:
    """Score every method on one set of spec §8.1, identically, and write `<out_dir>/evaluation.json`.

    split          val, test, test_ood (v2 scenarios; test and test_ood only through the test lock: pass
                   `test_run`, the run guard_test_run recorded before any inference, which is reused and not
                   recorded again, or `lock_hashes` (lock_record) to take the lock here; either record must
                   describe this very call, or it refuses), legacy_v1 (the v1 store) or control_v1world (the
                   v1-world scenario in `store_dir`, every replicate after replicate 0 scored, replicates as clusters)
    methods        Method objects; baselines() by default. TracerNet is a RiskMethod named "tracernet"
                   (tracernet_method(risk_table)); with it the differences and the decision rule are computed
    store_dir      the v2 store (store.STORE_V2_DIR) or, for legacy_v1, the v1 store (store.STORE_DIR)
    budgets        alerts per shift; `per_100_budgets` alerts per 100 specimens (the secondary view)
    out_dir        results/neural/<split>/ for the real stores, data/neural/dev-eval/<split>/ for any other
    workers        processes for the tuning groups (default: the CPU count less one); 1 runs in this process
    matched        also re-solve every knob on the scored replicates at the headline budget (R4's check); with
                   TracerNet as the focus this is done anyway, and reported whenever R4 fails
    focus          the method whose differences against every other are bootstrapped (default "tracernet" when
                   present); weighted methods are compared at the same weighting
    train_store_dir  the store whose train split gives the global fits (default store_dir)
    keep_alerts    keep every specimen's alert minute per series and budget in result["detail"] (tests)
    subsets        named scenario lists of a v2 set ({"ood_feature": [...]}). Each is reported from this same look
                   in result["subsets"][name]: every knob as tuned for the whole set, every metric, interval, slice
                   and the matched volume computed again over the subset's scenarios from their parts, the
                   bootstrap resampling the subset's clusters (`resamples`, seed 0), and the subset's counts. The
                   decision rule is judged on the whole set only. The subsets are part of the lock's configuration

    Returns the result, with a "detail" entry (per-group knobs, notes, and alerts if kept) that is not written."""
    t0 = time.perf_counter()
    if split not in SETS:
        raise ValueError(f"unknown set {split!r}; one of {sorted(SETS)}")
    if split in LOCKED_SPLITS and test_run is None and lock_hashes is None:
        raise LockError(f"{split} is scored only through the test lock: pass the test run guard_test_run recorded, "
                        "or lock_hashes (see lock_record)")
    methods = list(baselines() if methods is None else methods)
    names = [m.name for m in methods]
    if len(set(names)) != len(names) or any("|" in n for n in names):
        raise ValueError(f"method names must be distinct and free of '|': {names}")
    focus = focus if focus is not None else (TRACERNET if TRACERNET in names else None)
    if focus is not None and (focus not in names or not methods[names.index(focus)].weighted):
        raise ValueError(f"the focus {focus!r} must be one of the weighted methods")
    kind = "legacy" if split == "legacy_v1" else "v2"
    store_dir = Path(store_dir) if store_dir is not None else (store.STORE_DIR if kind == "legacy" else store.STORE_V2_DIR)
    train_store_dir = Path(train_store_dir) if train_store_dir is not None else store_dir
    subsets = _subset_lists(subsets)
    if subsets and split not in V2_SPLIT_SETS:
        raise ValueError(f"subsets are scenario lists of a v2 set ({', '.join(V2_SPLIT_SETS)}), not {split}")
    store_split, ids = _set_scenarios(split, store_dir, control_scenario, scenarios) if kind == "v2" else (None, [])
    unknown = sorted({s for v in (subsets or {}).values() for s in v} - set(ids))
    if unknown:
        raise ValueError(f"subset scenarios that are not among the {split} scenarios evaluated: {unknown[:5]}")
    risk_parts = _pin_risk_tables(methods)
    store_hash = _manifest_hash(store_dir) if kind == "v2" else None
    if split in LOCKED_SPLITS:
        current = _evaluation_hashes(store_dir, methods, store_hash=store_hash, train_store_dir=train_store_dir,
                                     budgets=budgets, per_100_budgets=per_100_budgets, resamples=resamples,
                                     matched=matched, focus=focus, scenarios=scenarios, subsets=subsets)
        supplied = test_run["hashes"] if test_run is not None else lock_hashes
        differ = [k for k, v in current.items() if supplied.get(k) != v]
        if differ:
            raise LockError(f"the lock record does not describe this evaluation ({', '.join(differ)} differ); build "
                            "it with lock_record from the same arguments")
        if test_run is None:
            test_run = guard_test_run(split, lock_hashes, relock_reason, lock_path, runs_path, risk_parts=risk_parts)
        else:
            _check_run(test_run, split)
        if risk_parts:
            record_risk_parts(test_run, risk_parts)
    loaded = config.load()
    start, stop = intervals.clock_events(loaded["policy"])
    cfg = triage.load()
    schemes = tuple((SCHEME_NAMES[k], v) for k, v in triage.weight_schemes(cfg).items())
    blist = budget_list(budgets, per_100_budgets)
    headline = next((b.key for b in blist if b.per == "shift" and b.value == HEADLINE_BUDGET), None)
    workers = workers if workers is not None else max(1, workers_.available_cores() - 1)
    env_args = dict(set_name=split, kind=kind, store_dir=str(store_dir), schemes=schemes, budgets=tuple(blist),
                    headline=headline, matched=matched and headline is not None, keep_alerts=keep_alerts,
                    matched_parts=(matched or focus == TRACERNET) and headline is not None)
    results, counts = [], {"scored_without_a_clock": 0, "history_without_a_clock": 0}
    if kind == "legacy":
        if store_dir.resolve() != store.STORE_DIR.resolve():
            raise ValueError("legacy_v1 is the v1 store (store.STORE_DIR), whose labels evaluate.load_test_specs reads")
        obs = store.open_observed(store_dir)
        history, _ = evaluate.load_specs(obs, None, start, stop, "fit")      # no labels: only sets the knobs
        scored, excluded = evaluate.load_test_specs(start, stop)
        counts["scored_without_a_clock"] = excluded["without_a_clock"]
        env = Env(store_split="test", by_run=True, slice_dimensions=SLICE_DIMENSIONS_V1, **env_args)
        setup = Setup(kind, split, store_dir, train_store_dir, start, stop, loaded["policy_hash"],
                      {s.group for s in history} | {s.group for s in scored})
        for m in methods:
            m.setup(setup)
        group = _group("fit runs 0 to 23 for runs 24 to 29", history, scored, True,
                       {"set": split, "store_dir": str(store_dir), "scenario_id": None})
        results.append(evaluate_group(group, methods, env))
        n_groups = 1
    else:
        env = Env(store_split=store_split, by_run=split == "control_v1world",
                  slice_dimensions=SLICE_DIMENSIONS_V2, **env_args)
        setup = Setup(kind, split, store_dir, train_store_dir, start, stop, loaded["policy_hash"],
                      _leaf_groups(store_dir, start, stop, store_split, ids))
        for m in methods:
            m.setup(setup)
        n_groups = len(ids)
        if workers <= 1 or len(ids) <= 1:
            _init_worker(methods, env)
            batches = [_run_batch(ids)]
        else:
            import multiprocessing as mp
            from concurrent.futures import ProcessPoolExecutor, as_completed

            size = max(1, math.ceil(len(ids) / (workers * 3)))
            chunks = [ids[i:i + size] for i in range(0, len(ids), size)]
            stage = f"{split}: tuning and scoring"
            workers_.report(stage, 0, len(ids), "scenarios")
            with workers_.one_thread_children(), ProcessPoolExecutor(
                    max_workers=min(workers, len(chunks)), mp_context=mp.get_context("spawn"),
                    initializer=_init_worker, initargs=(methods, env)) as pool:
                futures = {pool.submit(_run_batch, c): len(c) for c in chunks}
                done = 0
                for f in as_completed(futures):
                    f.result()      # a failed batch raises here, before it is counted as done
                    done += futures[f]
                    workers_.report(stage, done, len(ids), "scenarios")
                batches = [f.result() for f in futures]
        for res, c in batches:
            results.extend(res)
            for k in counts:
                counts[k] += c[k]
    subset_parts = {name: (v, _without_a_clock(store_dir, store_split, v, start, stop))
                    for name, v in (subsets or {}).items()}
    result = _assemble(split, methods, env, results, counts, focus, resamples, n_groups, subset_parts)
    result["store"] ={"path": _shown(store_dir), "policy_hash": loaded["policy_hash"]}
    if kind == "v2":
        result["store"]["manifest_sha256"] = store_hash
        if train_store_dir != store_dir:
            result["store"]["train_store"] = _shown(train_store_dir)
    elapsed = time.perf_counter() - t0
    total = result["counts"]["scored_specimens"] + result["counts"]["history_specimens"]
    result["timing"] = {"seconds": round(elapsed, 2), "workers": workers if kind == "v2" else 1,
                        "specimens_read": total,
                        "seconds_per_1000_specimens": round(1000 * elapsed / total, 4) if total else None}
    if write:
        out_dir = Path(out_dir) if out_dir is not None else _out_dir(split, store_dir, kind)
        out_dir.mkdir(parents=True, exist_ok=True)
        written = {k: v for k, v in result.items() if k != "detail"}
        _write_atomic(out_dir / "evaluation.json", json.dumps(written, indent=1, allow_nan=False) + "\n")
        result["written"] = str(out_dir / "evaluation.json")
        if split == FRESH_SPLIT and test_run is not None:   # the one look is done: no guard lets another start
            _write_atomic(Path(test_run.lock_path).parent / LOOKED_NAME, json.dumps(
                {"time": _now(None), "evaluation": result["written"],
                 "sha256": sha256_file(out_dir / "evaluation.json")}, indent=1, sort_keys=True) + "\n")
    return result


def _shown(path: Path) -> str:
    p = Path(path).resolve()
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def _manifest_hash(store_dir: Path) -> str | None:
    """The store's identity: the hash of its manifest, once the files the manifest lists are checked against the
    SHA-256 it records (a store whose files changed under an unchanged manifest refuses). None without a
    manifest."""
    if not (Path(store_dir) / store.STORE_V2_MANIFEST).exists():
        return None
    return sha256_json(store.verified_store_v2_manifest(store_dir))


def _pin_risk_tables(methods: Sequence[Method]) -> dict:
    """{method: {part: SHA-256}} for every method that reads a risk table (FrameRisk), each pinned to the parts
    hashed, so the parts the evaluation reads are the parts digested."""
    return {m.name: m.series.pin() for m in methods if isinstance(getattr(m, "series", None), FrameRisk)}


def _stacker(results: list[dict]) -> tuple[list, dict, Callable]:
    """The clusters of some tuning groups, their shifts, and a function stacking any per-cluster parts over them."""
    clusters = sorted({c for r in results for c in r["clusters"]})
    shifts = {c: r["shifts"][c] for r in results for c in r["clusters"]}

    def stacked(get) -> Series:
        rows = {}
        for r in results:
            for c, part in zip(r["clusters"], get(r)):
                rows[c] = part
        return _stack(clusters, rows, shifts)
    return clusters, shifts, stacked


def _measure(methods: Sequence[Method], env: Env, results: list[dict], focus: str | None,
             resamples: int) -> tuple[dict, dict]:
    """Every metric section from the tuning groups' parts: the knobs, the operating points with their cluster
    bootstrap, the differences from the focus, discrimination, calibration and slices. Returns the sections and
    each budget's series."""
    clusters, shifts, stacked = _stacker(results)
    volume = {c: r["volume"][c] for r in results for c in r["clusters"]}
    series_keys = [f"{n}|{w}" for m in methods for n, w in _series_keys(m, env)]

    operating, differences, knobs = {}, {}, {}
    series_at = {}
    for b in env.budgets:
        series = {k: stacked(lambda r, k=k, b=b: r["parts"][b.key][k]) for k in series_keys}
        series_at[b.key] = series
        boot = bootstrap(series, series_keys[0], [], resamples=resamples)
        operating[b.key] = _nest(boot["intervals"])
        knobs[b.key] = {}
        for k in series_keys:
            triples = [r["knobs"][b.key][k] for r in results]
            hv = [t[1] for t in triples]
            knobs[b.key][k] = {"groups": len(triples), "knob": _summary([t[0] for t in triples]),
                               "history_volume": {"max": max(hv) if hv else None,
                                                  "mean": float(np.mean(hv)) if hv else None},
                               "over_budget": int(sum(v > b.value + 1e-9 for v in hv)),
                               "no_alert_groups": int(sum(r["no_alert"][b.key][k] for r in results))}
        knobs[b.key] = _nest(knobs[b.key])
        if focus is not None:
            differences[b.key] = _differences(series, focus, methods, env, resamples)

    rows_by_method = {m.name: [r["rows"][m.name] for r in results] for m in methods}
    discrimination_out = {m.name: _discrimination_from_rows(rows_by_method[m.name]) for m in methods}
    calibration_out = {m.name: _calibration_from_rows(rows_by_method[m.name]) for m in methods if m.probabilistic}

    slices = {}
    if env.headline is not None:
        for d in env.slice_dimensions:
            if d == "volume_tercile":
                labels = tercile_labels(volume)
                slices[d] = {}
                for g in ("low", "middle", "high"):
                    keep = {c for c in clusters if labels[c] == g}
                    if not keep:
                        continue
                    slices[d][g] = _nest({k: _slice_metrics(_stack(
                        clusters, {c: p for c, p in zip(clusters, _cluster_parts(series_at[env.headline][k])) if c in keep},
                        shifts)) for k in series_keys})
                continue
            groups_found = sorted({g for r in results for g in r["slices"].get(d, {})}, key=str)
            slices[d] = {}
            for g in groups_found:
                slices[d][g] = _nest({k: _slice_metrics(stacked(
                    lambda r, d=d, g=g, k=k: r["slices"].get(d, {}).get(g, {}).get(k, []))) for k in series_keys})
    sections = {"knobs": knobs, "operating_points": operating, "discrimination": discrimination_out,
                "calibration": calibration_out, "slices": slices}
    if focus is not None:
        sections["differences"] = differences
    return sections, series_at


def _matched(methods: Sequence[Method], env: Env, results: list[dict], focus: str | None, resamples: int,
             trigger: str) -> dict:
    """The matched-volume sensitivity check (R4) from the tuning groups' matched parts."""
    _, _, stacked = _stacker(results)
    series_keys = [f"{n}|{w}" for m in methods for n, w in _series_keys(m, env)]
    mseries = {k: stacked(lambda r, k=k: r["matched"][k]["parts"]) for k in series_keys}
    boot = bootstrap(mseries, series_keys[0], [], resamples=resamples)
    out = {"budget": env.headline, "note": "knobs re-solved on the scored replicates from alert volume "
                                           "alone, per tuning group: a sensitivity check (R4), never a replacement",
           "trigger": trigger,
           "operating_point": _nest(boot["intervals"]),
           "knob": _nest({k: _summary([r["matched"][k]["knob"] for r in results]) for k in series_keys}),
           "no_alert_groups": _nest({k: int(sum(r["matched"][k]["no_alert"] for r in results)) for k in series_keys})}
    if focus is not None:
        out["differences"] = _differences(mseries, focus, methods, env, resamples)
    return out


def _counts(results: list[dict], counts: Mapping, n_groups: int) -> dict:
    shifts = {c: r["shifts"][c] for r in results for c in r["clusters"]}
    return {"tuning_groups": n_groups, "clusters": len(shifts),
            "scored_specimens": sum(r["counts"]["scored"] for r in results),
            "scored_without_a_clock": counts["scored_without_a_clock"],
            "history_specimens": sum(r["counts"]["history"] for r in results),
            "history_without_a_clock": counts["history_without_a_clock"],
            "breaches": sum(r["counts"]["breaches"] for r in results),
            "never_received": sum(r["counts"]["never_received"] for r in results),
            "scored_shifts": float(sum(shifts.values())),
            "history_shifts": float(sum(r["counts"]["history_shifts"] for r in results))}


def _local_fit(methods: Sequence[Method], results: list[dict], n_groups: int) -> dict:
    notes = [n for r in results for n in r["notes"]]
    return {"fell_back": len(notes), "of": n_groups if any(getattr(m, "fit", "") == "local" for m in methods) else 0,
            "notes": notes}


def _subset(ids: Sequence[str], counts: Mapping, methods: Sequence[Method], env: Env, results: list[dict],
            focus: str | None, resamples: int, trigger: str | None) -> dict:
    """One subset of a v2 set from the parts the look computed (a group's key is its scenario)."""
    keep = set(ids)
    mine = [r for r in results if r["key"] in keep]
    out = {"scenarios": list(ids), "note": SUBSET_NOTE, "counts": _counts(mine, counts, len(ids)),
           "local_fit": _local_fit(methods, mine, len(ids))}
    if not mine:
        return out | {"note": "no scenario of this subset has a scored specimen"}
    sections, _ = _measure(methods, env, mine, focus, resamples)
    out |= {k: sections[k] for k in ("knobs", "operating_points", "discrimination", "calibration", "slices")}
    if focus is not None:
        out |= {"focus": focus, "differences": sections["differences"]}
    if trigger is not None:
        out["matched_volume"] = _matched(methods, env, mine, focus, resamples, trigger)
    return out


def _assemble(split: str, methods: Sequence[Method], env: Env, results: list[dict], counts: dict, focus: str | None,
              resamples: int, n_groups: int, subsets: Mapping[str, tuple] | None = None) -> dict:
    sections, series_at = _measure(methods, env, results, focus, resamples)
    by_name = {m.name: m for m in methods}
    decides = TRACERNET in by_name and env.headline is not None and focus == TRACERNET
    r4_failed = decides and not _check_r4(_headline_points(series_at[env.headline],
                                                           {m.name: m.weighted for m in methods}))["held"]
    trigger = None
    if env.matched_parts and (env.matched or r4_failed):
        trigger = "R4 failed: unequal realized alert volume" if r4_failed else "requested"
    matched_out = _matched(methods, env, results, focus, resamples, trigger) if trigger is not None else None

    result = {
        "set": split, "description": SETS[split], "notice": NOTICE, "counts": _counts(results, counts, n_groups),
        "settings": {"tick_min": evaluate.TICK_MIN, "shifts_per_replicate": SHIFTS_PER_REPLICATE,
                     "budgets": [b.key for b in env.budgets], "headline": env.headline,
                     "weightings": {w: {str(t): v for t, v in ws.items()} for w, ws in env.schemes},
                     "knob": f"bisection, {KNOB_STEPS} steps, on each tuning group's history from alert volume alone"
                             + ("; where no knob that alerts meets a budget, the knob above every attainable score, "
                                "so the group gets no alerts (counted in no_alert_groups)" if env.kind == "v2" else ""),
                     "cluster": "run" if env.by_run else "scenario", "bootstrap": {"resamples": resamples,
                                                                                     "seed": BOOTSTRAP_SEED},
                     "good_warning_min": GOOD_WARNING_MIN, "warning_cap_min": WARNING_CAP_MIN,
                     "discrimination_landmarks_min": list(DISCRIMINATION_LANDMARKS),
                     "calibration_landmarks_min": list(CALIBRATION_LANDMARKS), "min_local_fit": MIN_LOCAL_FIT},
        "methods": {m.name: m.describe() for m in methods},
        "local_fit": _local_fit(methods, results, n_groups),
    } | {k: sections[k] for k in ("knobs", "operating_points", "discrimination", "calibration", "slices")}
    if split == "legacy_v1":
        result["local_fit"]["note"] = ("on legacy_v1 the history is fit runs 0 to 23 for every method, so the local "
                                       "and global refits are the same committed fit")
    if focus is not None:
        result["focus"] = focus
        result["differences"] = sections["differences"]
    if matched_out is not None:
        result["matched_volume"] = matched_out
    if decides:
        result["decision_rule"] = _decision(result, series_at[env.headline], matched_out)
    if subsets:
        result["subsets"] = {name: _subset(ids, c, methods, env, results, focus, resamples, trigger)
                             for name, (ids, c) in subsets.items()}
    result["detail"] = {"groups": [{"key": r["key"], "clusters": r["clusters"], "knobs": r["knobs"],
                                    "counts": r["counts"], "alerts": r["alerts"],
                                    "matched_knobs": {k: v["knob"] for k, v in r["matched"].items()}}
                                   for r in results]}
    return result


def _cluster_parts(series: Series) -> list[tuple]:
    return [(series.scalars[i], series.warnings[i], series.caught[i]) for i in range(len(series.keys))]


SLICE_METRICS = ("alerts", "alerts_per_shift", "breaches", "caught", "catch_rate", "median_warning_min",
                 "median_warning_all_breaches_min", "caught_with_good_warning", "false_alerts", "never_received",
                 "missed_losses", "capped_mean_warning_min")


def _slice_metrics(series: Series) -> dict:
    """The alert metrics a slice reports (alerts per shift over the whole set's shifts, as evaluate.by_group), with
    its specimen count."""
    m = point_metrics(series)
    return {"specimens": int(series.scalars[:, _C["specimens"]].sum())} | {k: m[k] for k in SLICE_METRICS}


def _differences(series: Mapping[str, Series], focus: str, methods: Sequence[Method], env: Env,
                 resamples: int) -> dict:
    """The focus method minus every other at each weighting (a rule, having none, under each)."""
    out = {}
    for wname, _ in env.schemes:
        fkey = f"{focus}|{wname}"
        others = {}
        for m in methods:
            if m.name == focus:
                continue
            others[m.name] = f"{m.name}|{wname}" if m.weighted else f"{m.name}|{UNWEIGHTED}"
        sub = {fkey: series[fkey]} | {k: series[k] for k in others.values()}
        boot = bootstrap(sub, fkey, list(others.values()), resamples=resamples)
        out[wname] = {name: boot["differences"][key] for name, key in others.items()}
    return out


def _headline_points(series: Mapping[str, Series], weighted: Mapping[str, bool]) -> dict:
    """decide()'s `headline`: the point metrics of TracerNet and the rule's baselines at each weighting
    (`weighted` maps a method's name to whether it has urgency weights)."""
    return {w: {m: point_metrics(series[f"{m}|{w if weighted[m] else UNWEIGHTED}"])
                for m in (TRACERNET, *BASELINES) if m in weighted} for w in WEIGHTINGS}


def _decision(result: dict, series: Mapping[str, Series], matched: dict | None) -> dict:
    head = result["settings"]["headline"]
    headline = _headline_points(series, {m: d["weighted"] for m, d in result["methods"].items()})
    differences = {w: result["differences"][head][w] for w in WEIGHTINGS}
    brier = {m: {str(L): result["calibration"].get(m, {}).get(str(L), {}).get("brier") for L in CALIBRATION_LANDMARKS}
             for m in (TRACERNET, "analytic_global", "analytic_local")}
    m_head = m_diff = None
    if matched is not None:
        m_head = {w: {m: {k: v["point"] for k, v in matched["operating_point"][m][
            w if result["methods"][m]["weighted"] else UNWEIGHTED].items()}
            for m in (TRACERNET, *BASELINES) if m in result["methods"]} for w in WEIGHTINGS}
        m_diff = {w: matched["differences"][w] for w in WEIGHTINGS}
    out = decide(headline, differences, brier, m_head, m_diff)
    out["set"] = result["set"]
    out["binding"] = result["set"] == "test"
    return out


# --- The table, the lock record and the command -------------------------------------------------------------

_TABLE = (("alerts/shift", "realized_alerts_per_shift", "{:.2f}"), ("breaches", "breaches", "{:d}"),
          ("caught", "caught", "{:d}"), ("med. warn all", "median_warning_all_breaches_min", "{:.1f}"),
          ("95% interval", None, None), ("med. warn caught", "median_warning_min", "{:.1f}"),
          (">=15 min", "caught_with_good_warning", "{:.0%}"), ("false", "false_alerts", "{:d}"),
          ("missed losses", "missed_losses", "{:d}"), ("capped mean", "capped_mean_warning_min", "{:.1f}"))


def table(result: dict, budget: str | None = None) -> str:
    """Every method and weighting at one budget (the headline by default), with the headline metric's interval."""
    budget = budget or result["settings"]["headline"]
    op = result["operating_points"][budget]
    c = result["counts"]
    lines = [f"{result['set']}: {c['scored_specimens']} scored specimens with a clock in {c['clusters']} clusters "
             f"({c['tuning_groups']} tuning groups), {c['breaches']} breaches ({c['never_received']} never received), "
             f"{c['scored_shifts']:.0f} shifts",
             f"knobs set on each group's history ({c['history_specimens']} specimens) from alert volume alone; "
             f"at {budget}",
             result["notice"], ""]
    head = f"{'method':18s}{'weights':>8s}" + "".join(f"{label:>17s}" for label, _, _ in _TABLE)
    lines.append(head)
    for name, by_w in op.items():
        for w, metrics in by_w.items():
            cells = []
            for label, key, fmt in _TABLE:
                if key is None:
                    m = metrics["median_warning_all_breaches_min"]
                    cells.append("-" if m["low"] is None else f"{m['low']:.0f} to {m['high']:.0f}")
                    continue
                v = metrics[key]["point"]
                cells.append("-" if v is None else fmt.format(v))
            lines.append(f"{name:18s}{w:>8s}" + "".join(f"{x:>17s}" for x in cells))
    notes = result["local_fit"]
    if notes["fell_back"]:
        lines += ["", f"local refits that fell back to the global fit: {notes['fell_back']}"]
    t = result.get("timing", {})
    if t:
        lines += ["", f"{t['seconds']} s with {t['workers']} workers, {t['seconds_per_1000_specimens']} s per 1,000 "
                      f"specimens read ({t['specimens_read']} scored and history specimens)"]
    return "\n".join(lines)


def _configuration(methods: Sequence[Method], *, train_store_hash: str | None, budgets: Sequence[float],
                   per_100_budgets: Sequence[float], resamples: int, matched: bool, focus: str | None,
                   scenarios: Sequence[str] | None) -> dict:
    """Every setting of an evaluation that can change a reported number: the methods, the effective urgency
    weights, the training store behind the global fits, the budgets, the bootstrap, the matched-volume request,
    the focus and the scenario selection. (The subsets a look reports are hashed apart, subsets_sha256, because
    they belong to one split; the risk tables read are recorded with the run, record_risk_parts.)"""
    names = [m.name for m in methods]
    focus = focus if focus is not None else (TRACERNET if TRACERNET in names else None)
    described = {m.name: {k: v for k, v in m.describe().items() if k != "fitted_on"} | {"fit": getattr(m, "fit", None)}
                 | ({"risk_column": m.series.value} if isinstance(getattr(m, "series", None), FrameRisk) else {})
                 for m in methods}      # fitted_on is filled in by setup, so it stays out of the hash
    weightings = {SCHEME_NAMES[k]: {str(t): float(w) for t, w in v.items()}
                  for k, v in triage.weight_schemes(triage.load()).items()}
    return {"methods": described, "budgets": [float(b) for b in budgets],
            "per_100": [float(b) for b in per_100_budgets], "bootstrap": [int(resamples), BOOTSTRAP_SEED],
            "weightings": weightings, "train_store_sha256": train_store_hash or "none", "matched": bool(matched),
            "focus": focus, "scenarios": None if scenarios is None else sorted(str(s) for s in scenarios)}


def _evaluation_hashes(store_dir: Path, methods: Sequence[Method], *, store_hash: str | None,
                       train_store_dir: Path | None = None, budgets: Sequence[float] = BUDGETS,
                       per_100_budgets: Sequence[float] = PER_100_BUDGETS, resamples: int = BOOTSTRAPS,
                       matched: bool = False, focus: str | None = None, scenarios: Sequence[str] | None = None,
                       subsets: Mapping[str, Sequence[str]] | None = None) -> dict:
    """The part of the lock record an evaluation can rebuild from its own arguments: the configuration, the store,
    this code and the subsets it reports. evaluate_split compares it with the record it is given."""
    train = Path(train_store_dir) if train_store_dir is not None else Path(store_dir)
    train_hash = store_hash if train.resolve() == Path(store_dir).resolve() else _manifest_hash(train)
    if train_hash is None and train.resolve() != Path(store_dir).resolve():
        raise RuntimeError(f"the training store at {train} has no manifest, so the global fits have no identity")
    configuration = _configuration(methods, train_store_hash=train_hash, budgets=budgets,
                                   per_100_budgets=per_100_budgets, resamples=resamples, matched=matched, focus=focus,
                                   scenarios=scenarios)
    hashes = current_hashes("none", "none", sha256_json(configuration), "none", store_hash or "none")
    return {k: hashes[k] for k in ("configuration_sha256", "store_sha256", "neural_eval_blob_sha", "evaluate_blob_sha")} \
        | {SUBSETS_FIELD: subsets_sha256(subsets), SCENARIOS_FIELD: scenarios_sha256(scenarios)}


def lock_record(store_dir: Path, methods: Sequence[Method], *, artifact_dir: Path | None = None,
                splits_path: Path | None = None, decision_model: str | None = None,
                train_store_dir: Path | None = None, budgets: Sequence[float] = BUDGETS,
                per_100_budgets: Sequence[float] = PER_100_BUDGETS, resamples: int = BOOTSTRAPS, matched: bool = False,
                focus: str | None = None, scenarios: Sequence[str] | None = None,
                subsets: Mapping[str, Sequence[str]] | None = None) -> dict:
    """The hashes guard_test_run compares (§8.7), for an evaluate_split call with the same arguments: the
    artifact's manifest and calibration (or "none" without an artifact), the configuration (_configuration), the
    split manifest, the store and this code, plus the subsets the look reports (pinned per split). The artifact's
    and the store's files are checked against their manifests first, so a manifest's hash stands for the files it
    lists."""
    def file_hash(path):
        return sha256_file(path) if path is not None and Path(path).exists() else "none"
    splits_path = Path(splits_path) if splits_path is not None else ROOT / "datasets" / "scenarios-v2.splits.json"
    artifact = calibration = "none"
    if artifact_dir is not None:
        artifact_dir = Path(artifact_dir)
        manifest_path = artifact_dir / "manifest.json"
        if not manifest_path.exists():
            raise RuntimeError(f"no artifact manifest at {manifest_path}")
        files = json.loads(manifest_path.read_text(encoding="utf-8")).get("files") or {}
        if "calibration.json" not in files:
            raise RuntimeError(f"the artifact manifest at {manifest_path} records no SHA-256 for calibration.json")
        store.verify_files(artifact_dir, files, "artifact")
        artifact, calibration = sha256_file(manifest_path), files["calibration.json"]
    store_hash = _manifest_hash(store_dir)
    if store_hash is None:
        raise RuntimeError(f"the store at {store_dir} has no manifest, so the lock cannot record its identity")
    own = _evaluation_hashes(store_dir, methods, store_hash=store_hash, train_store_dir=train_store_dir,
                             budgets=budgets, per_100_budgets=per_100_budgets, resamples=resamples, matched=matched,
                             focus=focus, scenarios=scenarios, subsets=subsets)
    return current_hashes(artifact, calibration, own["configuration_sha256"], file_hash(splits_path), store_hash,
                          decision_model=decision_model) | {SUBSETS_FIELD: own[SUBSETS_FIELD],
                                                            SCENARIOS_FIELD: own[SCENARIOS_FIELD]}


def command_evaluate(split: str, *, store_dir: Path | None = None, out_dir: Path | None = None,
                     workers: int | None = None, tracernet: Path | None = None, artifact_dir: Path | None = None,
                     matched: bool = False, relock_reason: str | None = None,
                     train_store_dir: Path | None = None, site_map: bool = False) -> int:
    """`evaluate --split SPLIT`: every baseline (and TracerNet, from its RiskTable parquet `tracernet`, or scored
    here by the artifact) on the set, written to <out_dir>/evaluation.json, with the headline table printed. On a
    test set the lock is taken and the run recorded first; only then does the artifact score the set, and the
    evaluation reuses that run. For control_v1world, `store_dir` is the store holding the v1-world scenario and
    `train_store_dir` the v2 store whose train split gives the global fits. On test_ood the same look also reports
    the subset `ood_feature`, the scenarios that carry the out-of-distribution feature themselves, found in the
    store's truth (ood_feature_scenarios)."""
    if split == FRESH_SPLIT and train_store_dir is None:
        raise ValueError("the fresh test set's global fits come from the training store: pass train_store_dir "
                         "(--train-store), the store the frozen model trained on")
    score_artifact = tracernet is None and artifact_dir is not None
    if score_artifact:
        tracernet = RISK_DIR / split / "*.parquet"
    methods = baselines() + ([tracernet_method(tracernet)] if tracernet is not None else [])
    if site_map:        # the probability TracerNet reports, beside its own scores, in the same evaluation and lock
        if tracernet is None:
            raise ValueError("the site map needs TracerNet's risk table")
        from src.neural.sitecal import SITE_METHOD, tracernet_sitecal
        methods.append(tracernet_sitecal(tracernet, name=SITE_METHOD))
    store_path = Path(store_dir) if store_dir is not None else store.STORE_V2_DIR
    feature = ood_feature_scenarios(store_path) if split == "test_ood" else []
    subsets = {OOD_FEATURE: feature} if feature else None
    test_run = None
    if split in LOCKED_SPLITS:
        hashes = lock_record(store_path, methods, artifact_dir=artifact_dir, train_store_dir=train_store_dir,
                             matched=matched, subsets=subsets)
        supplied = None if score_artifact else _pin_risk_tables(methods)   # a table given: checked before logging
        test_run = guard_test_run(split, hashes, relock_reason, risk_parts=supplied)
    if score_artifact:  # after the lock: on a test set, scoring it with the artifact is part of the one look
        risk_table(split, Path(artifact_dir), store_dir=store_dir, workers=workers, test_run=test_run)
    result = evaluate_split(split, methods, store_dir=store_dir, out_dir=out_dir, workers=workers, matched=matched,
                            train_store_dir=train_store_dir, test_run=test_run, subsets=subsets)
    print(table(result))
    if "decision_rule" in result:
        v = result["decision_rule"]["verdict"]
        print(f"\ndecision rule on {split} (binding only on test): R1 to R4 held = {v['held']}; "
              f"primary model: {v['primary_model']}")
    for name, sub in (result.get("subsets") or {}).items():
        print(f"\n{name}: {len(sub['scenarios'])} of the {result['counts']['tuning_groups']} {split} scenarios, "
              f"from the same look\n" + (table(subset_view(result, name)) if "operating_points" in sub else sub["note"]))
    print(f"\nwrote {_shown(Path(result['written']))}")
    return 0


WARNING_PART_ROWS = 1_000_000


def median_warning(store_dir: Path | None = None, *, workers: int | None = None) -> Callable:
    """Spec §7.9's warning check for rung selection, on validation only. `median_warning(store_dir)(predict_fn)`
    scores every history and scored replicate of `val` with `predict_fn(store_dir, runs, *, workers)` (a rung's
    uncalibrated predictor, src/neural/ensemble.py `predict_fn_of`), runs the evaluation driver on `val` at 6 alerts
    per shift, and returns the median warning over all breaches (a miss counting as zero) under the 3-2-1 weights.
    Nothing here reads test or test_ood."""
    store_dir = Path(store_dir) if store_dir is not None else store.STORE_V2_DIR

    def warning(predict_fn: Callable) -> float | None:
        con = store.open_observed_v2(store_dir)
        try:
            runs = [r for (r,) in con.execute("select run from runs where split = 'val' order by run").fetchall()]
        finally:
            con.close()
        if not runs:
            raise RuntimeError(f"the store at {store_dir} has no val runs for the warning check")
        frame = predict_fn(store_dir, runs, workers=workers).select("specimen_id", "tick_min", "p_miss")
        with tempfile.TemporaryDirectory() as tmp:
            # Sorted parts, so each scenario's lookup reads one or two parts rather than the whole table.
            for i, part in enumerate(frame.sort("specimen_id").iter_slices(n_rows=WARNING_PART_ROWS)):
                part.write_parquet(Path(tmp) / f"risk-{i:05d}.parquet")
            rung = RiskMethod("rung", FrameRisk(Path(tmp) / "risk-*.parquet"), monotone=False,
                              fitted_on="train (the rung's best run)")
            r = evaluate_split("val", [rung], store_dir=store_dir, budgets=(HEADLINE_BUDGET,), per_100_budgets=(),
                               workers=workers, write=False)
        point = r["operating_points"][f"{HEADLINE_BUDGET}/shift"]["rung"]["3-2-1"]
        return point["median_warning_all_breaches_min"]["point"]
    return warning
