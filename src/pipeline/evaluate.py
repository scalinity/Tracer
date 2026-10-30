"""Baseline against risk model on the held-out runs (plan task M4; docs/architecture.md §5.2.1, §9).

    python -m src.pipeline.evaluate

This is the only module that opens the truth store. It does so for the breach label and the planted
fault; every score a method sees comes from the observed file and the parameters fitted on the fit runs.

Every 5 minutes each specimen that has been collected and not received is scored, and a method raises
at most one alert per specimen. Warning time is the minutes from the alert to the window closing; an
alert at or after the close is too late and does not catch a breach. The two methods are:

  baseline   alert once the time since collection passes the q-th quantile of the group's observed
             collection-to-receipt times (the group is specimen type, six-hour block, priority, with
             the same fallback as the fit)
  model      alert once the risk of missing the window, from src/models/risk.py, reaches a threshold

Each has one knob. For a budget of alerts per 8-hour shift the knob is set, by bisection on the fit
runs and from their scans alone (no labels), to the most aggressive value that stays within the budget;
it is then applied unchanged to the held-out runs, whose own alert volume is reported. Median warning
among caught breaches rewards a method that misses the hard cases, so two measures count misses too:
the median warning over all breaches (a miss counts as zero) and the share caught with at least
GOOD_WARNING_MIN of notice. Specimens with no collection scan (never collected) have no clock; they are
counted and set aside. Intervals come from a paired bootstrap over the held-out runs. Writes
results/evaluation.json.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from src.generator import config
from src.models import risk
from src.pipeline import intervals, store

ROOT = Path(__file__).resolve().parents[2]
RESULTS_PATH = ROOT / "results" / "evaluation.json"
TICK_MIN = 5
SHIFT_MIN = 8 * 60
BUDGETS = (1, 2, 4, 6)
HEADLINE_BUDGET = 6  # architecture §5.2.1: provisional, an assumption to validate
LANDMARKS = (30, 60)
BINS = (0.0, 0.05, 0.1, 0.2, 0.4, 0.7, 1.0000001)
BOOTSTRAPS = 2000
BOOTSTRAP_SEED = 0
SHORT_WINDOW_MAX = 120  # minutes; the rest are windows of a day or more
GOOD_WARNING_MIN = 15  # a warning shorter than this leaves little time to find a specimen


@dataclass(frozen=True)
class Spec:
    specimen_id: str
    run: int
    group: str
    chain: tuple
    window: int
    receipt_min: float | None  # minutes from the clock start to the clock stop, None if it never arrives
    breach: bool
    fault: str

    @property
    def ticks(self) -> np.ndarray:
        """Scoring times: every tick the window is still open and the specimen still unreceived."""
        last = self.window if self.receipt_min is None else min(self.window, math.ceil(self.receipt_min) - 1)
        return np.arange(TICK_MIN, last + 1, TICK_MIN, dtype=float)


def load_specs(obs, truth, start: str, stop: str, split: str) -> tuple[list[Spec], dict]:
    """The specimens of one split that have a clock. The breach label comes from the truth store when one
    is given; otherwise it is derived from the scans and the window, which is all the fit runs need."""
    rows = obs.execute("""
        select s.specimen_id, s.run, s.specimen_type, s.priority, hour(a.timestamp), s.window_min,
               epoch(b.timestamp - a.timestamp) / 60.0
        from specimens s
        join events a on a.specimen_id = s.specimen_id and a.activity = ?
        left join events b on b.specimen_id = s.specimen_id and b.activity = ?
        where s.split = ? order by s.specimen_id""", [start, stop, split]).fetchall()
    labels = {} if truth is None else {sid: (breach, fault) for sid, breach, fault in truth.execute(
        "select specimen_id, breach, planted_fault from truth_specimens where split = ?", [split]).fetchall()}
    total = obs.execute("select count(*) from specimens where split = ?", [split]).fetchone()[0]
    specs = []
    for sid, run, kind, priority, hour, window, minutes in rows:
        key = (kind, hour // intervals.BLOCK_HOURS, priority)
        chain = tuple(intervals.key_name(k) for k in intervals.parent_chain(*key))
        derived = minutes is None or minutes > window
        breach, fault = labels.get(sid, (derived, "unknown"))
        if breach != derived:
            raise RuntimeError(f"{sid}: the breach label disagrees with its scans and its window")
        specs.append(Spec(sid, run, intervals.key_name(key), chain, int(window),
                          None if minutes is None else float(minutes), bool(breach), fault))
    return specs, {"specimens": total, "with_a_clock": len(specs), "without_a_clock": total - len(specs)}


def baseline_quantiles(fit_rows) -> dict[str, np.ndarray]:
    """Sorted collection-to-receipt times for every group and parent that has at least the minimum members."""
    out = {}
    for key in {k for t, b, p, _ in fit_rows for k in intervals.parent_chain(t, b, p)}:
        x = np.sort(np.array(intervals.members(fit_rows, key)))
        if len(x) >= intervals.MIN_GROUP or key == (None, None, None):
            out[intervals.key_name(key)] = x
    return out


def baseline_alerts(specs: list[Spec], q: float, quantiles: dict[str, np.ndarray]) -> dict[str, float | None]:
    out = {}
    for s in specs:
        times = quantiles[next(name for name in s.chain if name in quantiles)]
        threshold = float(np.quantile(times, q))
        ticks = s.ticks
        i = int(np.searchsorted(ticks, threshold, side="right"))  # first tick strictly after the threshold
        out[s.specimen_id] = float(ticks[i]) if i < len(ticks) else None
    return out


def risk_series(specs: list[Spec], params: dict) -> dict[str, np.ndarray]:
    """The risk at every scoring tick. It only rises as silence continues, and the running maximum
    guards the search against a rounding wobble."""
    mixtures = {g: risk.mixture_from(params, g) for g in params["groups"]}
    out = {}
    for s in specs:
        mix = mixtures[s.group]
        out[s.specimen_id] = np.maximum.accumulate(
            np.array([risk.risk_unreceived(t, mix, s.window) for t in s.ticks]))
    return out


def model_alerts(specs: list[Spec], series: dict[str, np.ndarray], theta: float) -> dict[str, float | None]:
    out = {}
    for s in specs:
        r, ticks = series[s.specimen_id], s.ticks
        i = int(np.searchsorted(r, theta, side="left"))
        out[s.specimen_id] = float(ticks[i]) if i < len(r) else None
    return out


def score(specs: list[Spec], alerts: dict[str, float | None], shifts: float) -> dict:
    fired = [s for s in specs if alerts[s.specimen_id] is not None]
    lead = {s.specimen_id: s.window - alerts[s.specimen_id] for s in fired}
    breaches = [s for s in specs if s.breach]
    caught = [s for s in breaches if s.specimen_id in lead and lead[s.specimen_id] > 0]
    warnings = [lead[s.specimen_id] for s in caught]
    everyone = warnings + [0.0] * (len(breaches) - len(caught))
    return {
        "alerts": len(fired), "alerts_per_shift": len(fired) / shifts,
        "breaches": len(breaches), "caught": len(caught),
        "catch_rate": len(caught) / len(breaches) if breaches else None,
        "median_warning_min": float(np.median(warnings)) if warnings else None,
        "median_warning_all_breaches_min": float(np.median(everyone)) if everyone else None,
        "caught_with_good_warning": sum(w >= GOOD_WARNING_MIN for w in warnings) / len(breaches) if breaches else None,
        "false_alerts": sum(1 for s in fired if not s.breach),
    }


def tune(alerts_at, lo: float, hi: float, specs: list[Spec], shifts: float, budget: float, steps: int = 40) -> float:
    """The smallest knob in [lo, hi] whose alerts per shift stay within budget; alerts only fall as the knob rises."""
    for _ in range(steps):
        mid = (lo + hi) / 2
        if score(specs, alerts_at(mid), shifts)["alerts_per_shift"] <= budget:
            hi = mid
        else:
            lo = mid
    return hi


def reliability(specs: list[Spec], params: dict, landmark: int) -> dict:
    """Predicted risk against what happened, for specimens still unreceived at the landmark."""
    mixtures = {g: risk.mixture_from(params, g) for g in params["groups"]}
    rows = [(risk.risk_unreceived(landmark, mixtures[s.group], s.window), s.breach) for s in specs
            if s.window > landmark and (s.receipt_min is None or s.receipt_min > landmark)]
    p, y = np.array([r for r, _ in rows]), np.array([b for _, b in rows], dtype=float)
    base = y.mean()
    bins = []
    for lo, hi in zip(BINS, BINS[1:]):
        m = (p >= lo) & (p < hi)
        if m.any():
            bins.append({"from": lo, "to": min(hi, 1.0), "n": int(m.sum()), "predicted": float(p[m].mean()),
                         "observed": float(y[m].mean())})
    return {"landmark_min": landmark, "n": len(rows), "breach_rate": float(base),
            "brier": float(np.mean((p - y) ** 2)), "brier_of_the_base_rate": float(np.mean((base - y) ** 2)),
            "bins": bins}


def by_group(specs, alerts, shifts, keyfn) -> dict:
    out = {}
    for name in sorted({keyfn(s) for s in specs}):
        sub = [s for s in specs if keyfn(s) == name]
        sc = score(sub, alerts, shifts)
        out[name] = {k: sc[k] for k in ("breaches", "caught", "catch_rate", "median_warning_min",
                                         "median_warning_all_breaches_min")}
    return out


def bootstrap(specs, alert_sets: dict, shifts_per_run: float) -> dict:
    """Paired bootstrap over the held-out runs: resample whole runs, rescore both methods on the same draw."""
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    runs = sorted({s.run for s in specs})
    by_run = {r: [s for s in specs if s.run == r] for r in runs}
    diffs = {"median_warning_min": [], "median_warning_all_breaches_min": [], "catch_rate": []}
    for _ in range(BOOTSTRAPS):
        draw = rng.choice(runs, size=len(runs), replace=True)
        sample = [s for r in draw for s in by_run[r]]
        a = score(sample, alert_sets["baseline"], shifts_per_run * len(draw))
        b = score(sample, alert_sets["model"], shifts_per_run * len(draw))
        for k in diffs:
            if a[k] is not None and b[k] is not None:
                diffs[k].append(b[k] - a[k])
    return {k: {"low": float(np.percentile(v, 2.5)), "high": float(np.percentile(v, 97.5)),
                "share_model_ahead": float(np.mean(np.array(v) > 0))} for k, v in diffs.items()}


def evaluate() -> dict:
    loaded = config.load()
    start, stop = intervals.clock_events(loaded["policy"])
    params = json.loads(intervals.PARAMS_PATH.read_text(encoding="utf-8"))
    if params["policy_hash"] != loaded["policy_hash"]:
        raise RuntimeError("the fitted parameters were made under a different policy; refit before scoring")
    obs, truth = store.open_observed(), store.open_truth()
    fit_rows = intervals.load_intervals(obs, start, stop)
    quantiles = baseline_quantiles(fit_rows)

    fit_specs, _ = load_specs(obs, None, start, stop, "fit")  # no labels: only used to set the knobs
    fit_series = risk_series(fit_specs, params)
    fit_shifts = len({s.run for s in fit_specs}) * 7 * 3

    specs, excluded = load_specs(obs, truth, start, stop, "test")
    series = risk_series(specs, params)
    runs = sorted({s.run for s in specs})
    shifts = len(runs) * 7 * 3  # three 8-hour shifts a day, seven days a run

    result = {"policy_hash": params["policy_hash"], "source_sha256": params["source_sha256"],
              "held_out_runs": f"{runs[0]} to {runs[-1]}", "shift_minutes": SHIFT_MIN, "tick_minutes": TICK_MIN,
              "good_warning_min": GOOD_WARNING_MIN, "test_specimens": excluded["specimens"],
              "with_a_clock": excluded["with_a_clock"], "without_a_clock": excluded["without_a_clock"],
              "breaches": sum(s.breach for s in specs), "shifts": shifts,
              "breaches_per_shift": sum(s.breach for s in specs) / shifts,
              "specimens_per_shift": len(specs) / shifts, "operating_points": {}}
    for budget in BUDGETS:
        q = tune(lambda v: baseline_alerts(fit_specs, v, quantiles), 0.0, 0.9999, fit_specs, fit_shifts, budget, 30)
        theta = tune(lambda v: model_alerts(fit_specs, fit_series, v), 0.0, 1.0, fit_specs, fit_shifts, budget, 30)
        alerts = {"baseline": baseline_alerts(specs, q, quantiles), "model": model_alerts(specs, series, theta)}
        point = {"budget_per_shift": budget, "baseline_quantile": q, "model_threshold": theta}
        for name, a in alerts.items():
            point[name] = score(specs, a, shifts) | {
                "by_window": by_group(specs, a, shifts, lambda s: "short (up to 2 h)" if s.window <= SHORT_WINDOW_MAX else "long (a day or more)"),
                "by_fault": by_group([s for s in specs if s.breach], a, shifts, lambda s: s.fault)}
        point["bootstrap_model_minus_baseline"] = bootstrap(specs, alerts, 7 * 3)
        result["operating_points"][str(budget)] = point
    result["calibration"] = {str(m): reliability(specs, params, m) for m in LANDMARKS}
    return result


def table(r: dict) -> str:
    head = (f"held-out runs {r['held_out_runs']}: {r['with_a_clock']} specimens with a clock "
            f"({r['specimens_per_shift']:.1f} per 8-hour shift), {r['breaches']} breaches "
            f"({r['breaches_per_shift']:.2f} per shift); {r['without_a_clock']} never collected, set aside",
            "knobs are set on the fit runs to the alert budget, then applied unchanged to the held-out runs")
    fmt = lambda v, d=1: "-" if v is None else f"{v:.{d}f}"
    lines = [*head, "", f"{'':20s}{'baseline (quantile rule)':>26s}{'risk model':>20s}"]
    for budget, p in r["operating_points"].items():
        a, b = p["baseline"], p["model"]
        boot = p["bootstrap_model_minus_baseline"]
        lines += [f"{budget} alerts per shift: baseline q = {p['baseline_quantile']:.3f}, model threshold = {p['model_threshold']:.3f}",
                  f"  {'alerts per shift':28s}{a['alerts_per_shift']:>10.2f}{b['alerts_per_shift']:>20.2f}",
                  f"  {'breaches caught':28s}{a['caught']:>5d} of {a['breaches']:<3d}{'':3s}{b['caught']:>10d} of {b['breaches']}",
                  f"  {'median warning, caught (min)':28s}{fmt(a['median_warning_min']):>10s}{fmt(b['median_warning_min']):>20s}",
                  f"  {'median warning, all (min)':28s}{fmt(a['median_warning_all_breaches_min']):>10s}{fmt(b['median_warning_all_breaches_min']):>20s}",
                  f"  {'caught with >= ' + str(r['good_warning_min']) + ' min warning':28s}{100 * a['caught_with_good_warning']:>9.0f}%{100 * b['caught_with_good_warning']:>19.0f}%"]
        for key, label in (("median_warning_min", "median warning, caught"), ("median_warning_all_breaches_min", "median warning, all")):
            d = boot[key]
            lines.append(f"  model minus baseline, {label}: {d['low']:+.1f} to {d['high']:+.1f} min "
                         f"(95%), ahead in {d['share_model_ahead']:.0%} of resamples")
        lines.append("")
    for m, c in r["calibration"].items():
        lines += [f"calibration at {m} min silent ({c['n']} specimens, {c['breach_rate']:.1%} breach): Brier {c['brier']:.4f} "
                  f"against {c['brier_of_the_base_rate']:.4f} for the base rate",
                  f"  {'predicted':>10s}{'observed':>10s}{'n':>7s}"]
        lines += [f"  {b['predicted']:>10.3f}{b['observed']:>10.3f}{b['n']:>7d}" for b in c["bins"]]
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    result = evaluate()
    RESULTS_PATH.parent.mkdir(exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(table(result))
    print(f"wrote {RESULTS_PATH.relative_to(ROOT) if RESULTS_PATH.is_relative_to(ROOT) else RESULTS_PATH}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        sys.exit(f"error: {e}")
