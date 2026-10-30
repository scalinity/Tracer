"""How much does the headline depend on the arrival profile and the specimen mix? (plan task A2)

    python -m src.pipeline.sensitivity

Every parameter behind the dataset is an assumption, so the conclusions are only as good as those guesses
(docs/architecture.md §9, arm 4). This reweights the specimens instead of generating new ones, which is
exact for what the model sees: each specimen keeps its own timings and its own risk, and a scenario only
changes how many of each kind there are, when they arrive, and how many arrive in all. It does not change
how a specimen travels, so it cannot show congestion, which the generator does not model either.

For each scenario the specimens are weighted (the held-out runs for scoring, the fit runs for setting
knobs), and every method is scored two ways, with the same one-line rules as src/pipeline/evaluate.py:

  fixed knobs     the knobs set for 6 alerts per shift on the original fit runs, applied to the new
                  population: how the alert volume and the warning change if nothing is retuned
  retuned         each knob set again, on the scenario's reweighted fit runs, to 6 alerts per shift:
                  how much warning the same alert budget buys in the new population

Alerts per shift scale with the volume factor. Warning is minutes from the alert to the window closing, and
a breach that is never alerted counts as zero in the median over all breaches. The risk model uses the
default urgency weights. Writes results/sensitivity.json. The data is synthetic and had no clinical review.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

from src.api import export
from src.generator import config
from src.pipeline import evaluate, intervals, store, triage

ROOT = Path(__file__).resolve().parents[2]
RESULTS_PATH = ROOT / "results" / "sensitivity.json"
BUDGET = 6
METHODS = ("plan_baseline", "window_baseline", "model")


def hour_of(spec) -> int:
    return datetime.fromisoformat(spec.collected_at).hour


def hour_weights(specs, multiplier) -> np.ndarray:
    """Weights that give each collection hour its multiplier, as a share of all specimens, summing to the count."""
    hours = np.array([hour_of(s) for s in specs])
    share = np.bincount(hours, minlength=24) / len(specs)
    target = share * np.asarray(multiplier, dtype=float)
    per_hour = np.divide(target / target.sum(), share, out=np.zeros(24), where=share > 0)
    return per_hour[hours] * 1.0


def kind_weights(specs, factor) -> np.ndarray:
    """Weights that scale each specimen by factor(spec), then renormalize so they sum to the count."""
    w = np.array([factor(s) for s in specs], dtype=float)
    return w * len(specs) / w.sum()


def scenarios(specs) -> list[dict]:
    """The scenarios, each a name, a plain description, a volume factor and a weight function over any split."""
    hours = np.array([hour_of(s) for s in specs])
    share = np.bincount(hours, minlength=24) / len(specs)
    flat = [1 / (24 * x) if x > 0 else 0 for x in share]  # uniform target: weight each hour by 1/(24 x its share)
    morning = [1.6 if 7 <= h < 10 else 1.0 for h in range(24)]
    night = [3.0 if h < 6 else 1.0 for h in range(24)]
    urine = lambda s: 1.5 if s.kind.startswith("urine") else 1.0
    swab = lambda s: 1.5 if s.kind.startswith(("culture swab", "eSwab")) else 1.0
    urgent = lambda s: 2.0 if s.tier == 1 else 1.0
    ones = lambda split: np.ones(len(split))
    return [
        {"group": "as generated", "name": "as generated", "description": "The held-out runs as they are.", "volume": 1.0, "weights": ones},
        *({"group": "arrival volume", "name": f"volume x{f:g}", "volume": f, "weights": ones,
           "description": f"{f:g} times as many specimens arrive, with the same mix and hours."} for f in (0.5, 0.75, 1.5, 2.0)),
        {"group": "arrival profile", "name": "flat day", "volume": 1.0, "weights": lambda sp: hour_weights(sp, flat),
         "description": "The same number of specimens, spread evenly over the 24 hours."},
        {"group": "arrival profile", "name": "sharper morning peak", "volume": 1.0, "weights": lambda sp: hour_weights(sp, morning),
         "description": "Collections from 07:00 to 10:00 are 1.6 times as common, and the rest are scaled to keep the total."},
        {"group": "arrival profile", "name": "night-heavy", "volume": 1.0, "weights": lambda sp: hour_weights(sp, night),
         "description": "Collections from midnight to 06:00 are 3 times as common, like an intensive care unit."},
        {"group": "specimen mix", "name": "more urine", "volume": 1.0, "weights": lambda sp: kind_weights(sp, urine),
         "description": "Urine specimens are 1.5 times as common compared with everything else (unpreserved room-temperature urine has the shortest window, 60 minutes)."},
        {"group": "specimen mix", "name": "more swabs", "volume": 1.0, "weights": lambda sp: kind_weights(sp, swab),
         "description": "Culture swabs and eSwabs are 1.5 times as common compared with everything else (windows of a day or more)."},
        {"group": "specimen mix", "name": "more urgent specimens", "volume": 1.0, "weights": lambda sp: kind_weights(sp, urgent),
         "description": "Tier 1 specimens (lavage and tissue) are twice as common compared with everything else."},
    ]


# ---- weighted scoring ----

def weighted_median(values: np.ndarray, weights: np.ndarray) -> float | None:
    """The median under weights; with equal weights it is numpy's, the mean of the two middle values."""
    if len(values) == 0 or weights.sum() <= 0:
        return None
    order = np.argsort(values, kind="stable")
    v, cum = values[order], np.cumsum(weights[order])
    half = cum[-1] / 2
    i = int(np.searchsorted(cum, half - 1e-9 * cum[-1], side="left"))
    if abs(cum[i] - half) <= 1e-9 * cum[-1] and i + 1 < len(v):
        return float((v[i] + v[i + 1]) / 2)
    return float(v[i])


def score(specs, alerts: dict, weights: np.ndarray, volume: float, shifts: float) -> dict:
    fired = np.array([alerts[s.specimen_id] is not None for s in specs])
    warning = np.array([s.window - alerts[s.specimen_id] if alerts[s.specimen_id] is not None else 0.0 for s in specs])
    breach = np.array([s.breach for s in specs])
    caught = breach & (warning > 0)
    per_shift = len(specs) / shifts * volume / weights.sum()  # a unit of weight, as specimens per shift
    return {"alerts_per_shift": round(float(weights[fired].sum() * per_shift), 4),
            "breaches_per_shift": round(float(weights[breach].sum() * per_shift), 4),
            "catch_rate": round(float(weights[caught].sum() / weights[breach].sum()), 4),
            "median_warning_min": weighted_median(warning[caught], weights[caught]),
            "median_warning_all_breaches_min": weighted_median(np.where(caught, warning, 0.0)[breach], weights[breach])}


def tune(count, hi: float, limit: float) -> float:
    return export.smallest_knob(count, 0.0, hi, limit)


# ---- the run ----

def run() -> dict:
    loaded = config.load()
    weights3 = triage.weight_schemes(triage.load())["default"]
    start, stop = intervals.clock_events(loaded["policy"])
    params = json.loads(intervals.PARAMS_PATH.read_text(encoding="utf-8"))
    if params["policy_hash"] != loaded["policy_hash"]:
        raise RuntimeError("the fitted parameters were made under a different policy; refit before scoring")
    obs = store.open_observed()
    quantiles = evaluate.baseline_quantiles(intervals.load_intervals(obs, start, stop))
    fit_specs, _ = evaluate.load_specs(obs, None, start, stop, "fit")
    fit_harm = triage.harm_series(fit_specs, evaluate.risk_series(fit_specs, params), weights3)
    fit_shifts = len({s.run for s in fit_specs}) * 21
    specs, _ = evaluate.load_test_specs(start, stop)
    harm = triage.harm_series(specs, evaluate.risk_series(specs, params), weights3)
    shifts = len({s.run for s in specs}) * 21
    his = {"model": float(max(weights3.values())), "plan_baseline": 0.9999, "window_baseline": 1.0}

    def alerts_at(knobs: dict) -> dict:
        return {"model": evaluate.model_alerts(specs, harm, knobs["model"]),
                "plan_baseline": evaluate.baseline_alerts(specs, knobs["plan_baseline"], quantiles),
                "window_baseline": triage.window_share_alerts(specs, knobs["window_baseline"])}

    original = knob_set(export.knob_counters(fit_specs, fit_harm, quantiles), his, BUDGET * fit_shifts)
    base_alerts = alerts_at(original)
    rows = []
    for sc in scenarios(specs):
        w_fit, w_test = sc["weights"](fit_specs), sc["weights"](specs)
        counts = export.knob_counters(fit_specs, fit_harm, quantiles, weights=w_fit)
        limit = BUDGET * w_fit.sum() * fit_shifts / (len(fit_specs) * sc["volume"])  # weighted count that is 6 alerts a shift
        retuned = knob_set(counts, his, limit)
        row = {"group": sc["group"], "name": sc["name"], "description": sc["description"], "volume_factor": sc["volume"]}
        for label, alerts in (("fixed_knobs", base_alerts), ("retuned_to_6_per_shift", alerts_at(retuned))):
            row[label] = {m: score(specs, alerts[m], w_test, sc["volume"], shifts) for m in METHODS}
        row["retuned_knobs"] = {m: round(retuned[m], 6) for m in METHODS}
        row["specimens_per_shift"] = round(len(specs) / shifts * sc["volume"], 2)
        rows.append(row)
    return {"policy_hash": params["policy_hash"], "source_sha256": params["source_sha256"], "budget_per_shift": BUDGET,
            "held_out_runs": f"{min(s.run for s in specs)} to {max(s.run for s in specs)}", "method_names": {
                "plan_baseline": "the plan's quantile rule", "window_baseline": "alert at a share of the window", "model": "the risk model, weights 3, 2, 1"},
            "original_knobs": {m: round(original[m], 6) for m in METHODS},
            "note": "Reweighted held-out specimens: nothing about how a specimen travels changes. Synthetic data, no clinical review.",
            "scenarios": rows}


def knob_set(counts: dict, his: dict, limit: float) -> dict:
    return {m: tune(counts[m], his[m], limit) for m in METHODS}


def table(r: dict) -> str:
    fmt = lambda v: "-" if v is None else f"{v:g}"
    lines = [f"sensitivity of the headline, held-out runs {r['held_out_runs']} (synthetic data, no clinical review)",
             "median warning over all breaches in minutes; alerts per 8-hour shift in brackets; ",
             "fixed knobs are set for 6 per shift on the original fit runs, retuned are set again for each scenario", "",
             f"{'':24s}{'-- fixed knobs --':>34s}{'-- retuned to 6 per shift --':>46s}",
             f"{'scenario':24s}{'plan':>12s}{'window':>11s}{'model':>11s}{'plan':>12s}{'window':>11s}{'model':>11s}"]
    for row in r["scenarios"]:
        cells = []
        for label in ("fixed_knobs", "retuned_to_6_per_shift"):
            for m in METHODS:
                x = row[label][m]
                cells.append(f"{fmt(x['median_warning_all_breaches_min'])} ({x['alerts_per_shift']:.1f})")
        lines.append(f"{row['name']:24s}" + "".join(f"{c:>12s}" if i % 3 == 0 else f"{c:>11s}" for i, c in enumerate(cells)))
    return "\n".join(lines)


def main() -> int:
    result = run()
    RESULTS_PATH.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(table(result))
    print(f"\nwrote {RESULTS_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        sys.exit(f"error: {e}")
