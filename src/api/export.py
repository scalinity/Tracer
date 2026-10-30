"""Export the demo bundle: the real model output the static demo page reads (plan tasks D11 and D12).

    python -m src.api.export --run test --out docs/demo/bundle.json

For every held-out specimen that has a clock the bundle holds its brief, its scan events, the risk and the
location probabilities every 5 minutes while it was silent, the alert moment of each method at the
headline operating point, and the truth (planted fault, breach label, true segments). It also holds
everything the browser needs to recompute and compare: the fitted parameters, the policy and its hash, the
urgency weights, and the plan's baseline thresholds. A grid of 20 operating points gives, for the plan's
baseline, a window-aware baseline and the risk model, the alerts per shift and the median warning, so the
demo's threshold slider needs no model run. Ten showcase candidates are picked by fixed rules, and the stories written for them (src/api/heroes.py)
are merged in when they were written for that very list.

Each method's knob at each grid point is set on the fit runs, from alert volume alone, as in
src/pipeline/evaluate.py, and applied unchanged to the held-out runs. A method alerts a specimen at most once,
so its alert count at a knob is the number of specimens whose last scoring tick reaches it, and the knob for
a budget is found by bisection on that count. Risk is written to the end of the demo's horizon (240 min), or
to the strictest alert if that comes later; the location probabilities stop at the horizon.

This module reads the truth only through evaluate.py. The bundle is synthetic data with no clinical review.
Writes docs/demo/bundle.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

from src.generator import config
from src.models import location
from src.pipeline import evaluate, intervals, store, triage

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "docs" / "demo" / "bundle.json"
HEROES_PATH = ROOT / "docs" / "demo" / "heroes.json"
BUNDLE_VERSION = 1
HORIZON_MIN = 240
GRID_BUDGETS = (0.5, 0.75, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 5.5, 6, 7, 8, 9, 10, 11, 12, 14)
SEGMENT_STATES = (*location.PLACES, "at_receiving_bench")  # where the carrier was, up to the lab's bench
NOTICE = ("Synthetic data written by claude-haiku-5-5. It had no clinical review, and every parameter is an "
          "assumption. Warning is the minutes from the alert to the stability window closing.")


def smallest_knob(count, lo: float, hi: float, limit: float, steps: int = 48) -> float:
    """The smallest knob in [lo, hi] whose alert count stays within the limit; the count only falls as it rises."""
    for _ in range(steps):
        mid = (lo + hi) / 2
        if count(mid) <= limit:
            hi = mid
        else:
            lo = mid
    return hi


def last_ticks(specs) -> np.ndarray:
    return np.array([s.ticks[-1] if len(s.ticks) else -np.inf for s in specs])


def knob_counters(specs, harm: dict, quantiles: dict, weights: np.ndarray | None = None) -> dict:
    """Alert counts as a function of each method's knob, over the given specimens. With `weights` (one per
    specimen) the count is the weighted one, as when a scenario reweights the population."""
    last = last_ticks(specs)
    peak = np.array([harm[s.specimen_id][-1] if len(harm[s.specimen_id]) else -np.inf for s in specs])
    windows = np.array([s.window for s in specs], dtype=float)
    names = [next(n for n in s.chain if n in quantiles) for s in specs]
    unique = sorted(set(names))
    index = np.array([unique.index(n) for n in names])
    w = np.ones(len(specs)) if weights is None else np.asarray(weights, dtype=float)
    out = (lambda mask: int(np.sum(mask))) if weights is None else (lambda mask: float(np.sum(w[mask])))

    def plan(q: float):
        threshold = np.array([np.quantile(quantiles[n], q) for n in unique])
        return out(last > threshold[index])

    return {"model": lambda theta: out(peak >= theta), "plan_baseline": plan,
            "window_baseline": lambda share: out(last >= share * windows)}


def metrics(specs, alerts: dict, shifts: float) -> dict:
    sc = evaluate.score(specs, alerts, shifts)
    return {"alerts_per_shift": round(sc["alerts_per_shift"], 4), "breaches": sc["breaches"], "caught": sc["caught"],
            "median_warning_min": sc["median_warning_min"], "median_warning_all_breaches_min": sc["median_warning_all_breaches_min"],
            "caught_with_good_warning": round(sc["caught_with_good_warning"], 4), "false_alerts": sc["false_alerts"]}


def whole(minute: float | None) -> int | None:
    return None if minute is None else int(minute)


def showcase(specs, alerts: dict, risk_at: dict) -> list[dict]:
    """Ten candidates by fixed rules, most telling first within each rule, ties to the lower id. A specimen
    whose window fits the demo's horizon is preferred, since the timeline and the 3D view can show it; a kind
    that no specimen fits is left out, and one specimen is never picked twice."""
    model, plan = alerts["model"], alerts["plan_baseline"]

    def warning(s, table):
        return None if table[s.specimen_id] is None else s.window - table[s.specimen_id]

    def caught(s):
        return s.breach and (warning(s, model) or 0) > 0

    def edge(s):
        return (warning(s, model) or 0) - max(warning(s, plan) or 0, 0)

    def margin(s):
        return abs(s.receipt_min - s.window) if s.receipt_min is not None else float("inf")

    def gap(s):
        return (s.receipt_min - model[s.specimen_id]) if s.receipt_min is not None and model[s.specimen_id] is not None else -1

    rules = [
        ("clear_catch", "A breach Tracer warned about at least 30 minutes ahead, after more than 10 minutes of silence, with the biggest lead over the plan's baseline.",
         lambda s: caught(s) and (warning(s, model) or 0) >= 30 and model[s.specimen_id] >= 3 * evaluate.TICK_MIN,
         lambda s: (-edge(s), s.specimen_id)),
        ("stalled_then_found", "Stalled at the station, alerted, then received inside the window.",
         lambda s: s.fault == "stalled_at_station" and model[s.specimen_id] is not None and s.receipt_min is not None
         and model[s.specimen_id] < s.receipt_min <= s.window, lambda s: (-gap(s), s.specimen_id)),
        ("never_sent", "Left at the unit and never sent: the same silence as a stall, a different place.",
         lambda s: s.fault == "not_dispatched" and model[s.specimen_id] is not None, lambda s: (-(warning(s, model) or 0), s.specimen_id)),
        ("lost_in_tube", "Lost in the tube and never received.",
         lambda s: s.fault == "lost_in_tube" and model[s.specimen_id] is not None, lambda s: (-(warning(s, model) or 0), s.specimen_id)),
        ("misrouted_then_found", "Sent to the wrong station, alerted, then found.",
         lambda s: s.fault == "misrouted" and model[s.specimen_id] is not None and s.receipt_min is not None
         and model[s.specimen_id] < s.receipt_min, lambda s: (-gap(s), s.specimen_id)),
        ("false_alarm", "An alert on a specimen that arrived in time, the most convincing one: the highest risk at the alert.",
         lambda s: not s.breach and model[s.specimen_id] is not None, lambda s: (-risk_at.get(s.specimen_id, 0.0), s.specimen_id)),
        ("content_failure", "Arrived on time and was rejected at accessioning: a failure Tracer cannot see.",
         lambda s: s.fault == "content_failure" and model[s.specimen_id] is None, lambda s: (s.tier, s.specimen_id)),
        ("borderline", "Received within minutes of the window closing, the closest call.",
         lambda s: s.receipt_min is not None and s.fault == "none", lambda s: (margin(s), s.specimen_id)),
        ("baseline_ahead", "A breach where the plan's baseline warned earlier than the risk model, an honest counterexample.",
         lambda s: s.breach and (warning(s, plan) or 0) > (warning(s, model) or 0), lambda s: (edge(s), s.specimen_id)),
        ("urgent_tier_catch", "A tier 1 specimen (lavage or tissue) caught with a long warning, where the urgency weights count.",
         lambda s: s.tier == 1 and caught(s), lambda s: (-(warning(s, model) or 0), s.specimen_id)),
    ]
    picked, used = [], set()
    for kind, why, fits, order in rules:
        pool = [s for s in specs if fits(s) and s.specimen_id not in used]
        pool = sorted([s for s in pool if s.window <= HORIZON_MIN] or pool, key=order)
        if not pool:
            continue
        s = pool[0]
        used.add(s.specimen_id)
        picked.append({
            "id": s.specimen_id, "kind": kind, "why": why, "type": s.kind, "tier": s.tier, "fault": s.fault, "breach": s.breach,
            "window_min": s.window, "receipt_min": s.receipt_min, "model_alert_min": whole(model[s.specimen_id]),
            "baseline_alert_min": whole(plan[s.specimen_id]), "model_warning_min": warning(s, model),
            "baseline_warning_min": warning(s, plan)})
    return picked


def candidates_hash(candidates: list[dict]) -> str:
    """Which candidate list a set of stories was written for."""
    return hashlib.sha256(json.dumps([[c["id"], c["kind"]] for c in candidates]).encode()).hexdigest()[:16]


def load_heroes(candidates: list[dict], path: Path | None = None) -> list[dict]:
    """The stories in docs/demo/heroes.json, if they were written for this very candidate list. A story about
    a specimen the rules no longer pick would be wrong, so a changed list leaves `heroes` empty."""
    path = HEROES_PATH if path is None else path
    if not path.exists():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc["candidates_hash"] != candidates_hash(candidates):
        print("warning: docs/demo/heroes.json was written for a different candidate list; heroes left empty", file=sys.stderr)
        return []
    return [{k: h[k] for k in ("id", "story", "takeaway", "question")} for h in doc["heroes"]]


def policy_block(loaded: dict) -> dict:
    keep = ("specimen_type", "test", "clock_start", "clock_stop", "window_min", "urgency_tier", "source", "assumed")
    return {"hash": loaded["policy_hash"], "clock_start": next(iter(loaded["policy"]["policies"].values()))["clock_start"],
            "clock_stop": next(iter(loaded["policy"]["policies"].values()))["clock_stop"],
            "multi_test_rule": loaded["policy"]["multi_test_rule"],
            "policies": {pid: {k: p[k] for k in keep} for pid, p in loaded["policy"]["policies"].items()}}


def build() -> dict:
    loaded = config.load()
    cfg = triage.load()
    weights = triage.weight_schemes(cfg)["default"]
    headline = cfg["operating_point"]["budget_per_shift"]
    if headline not in GRID_BUDGETS:
        raise RuntimeError(f"the headline budget of {headline} alerts per shift is not on the grid")
    start, stop = intervals.clock_events(loaded["policy"])
    params = json.loads(intervals.PARAMS_PATH.read_text(encoding="utf-8"))
    if params["policy_hash"] != loaded["policy_hash"]:
        raise RuntimeError("the fitted parameters were made under a different policy; refit before exporting")
    manifest = json.loads(store.MANIFEST_PATH.read_text(encoding="utf-8"))
    obs = store.open_observed()
    quantiles = evaluate.baseline_quantiles(intervals.load_intervals(obs, start, stop))

    # the knobs, set on the fit runs from alert volume alone
    fit_specs, _ = evaluate.load_specs(obs, None, start, stop, "fit")
    fit_harm = triage.harm_series(fit_specs, evaluate.risk_series(fit_specs, params), weights)
    fit_shifts = len({s.run for s in fit_specs}) * 7 * 3
    count = knob_counters(fit_specs, fit_harm, quantiles)
    knobs = []
    for budget in GRID_BUDGETS:
        limit = budget * fit_shifts
        knobs.append({"model": smallest_knob(count["model"], 0.0, float(max(weights.values())), limit),
                      "plan_baseline": smallest_knob(count["plan_baseline"], 0.0, 0.9999, limit),
                      "window_baseline": smallest_knob(count["window_baseline"], 0.0, 1.0, limit)})

    # the held-out runs
    specs, excluded = evaluate.load_test_specs(start, stop)
    series = evaluate.risk_series(specs, params)
    harm = triage.harm_series(specs, series, weights)
    shifts = len({s.run for s in specs}) * 7 * 3
    grid, per_point = [], []
    for budget, k in zip(GRID_BUDGETS, knobs):
        alerts = {"model": evaluate.model_alerts(specs, harm, k["model"]),
                  "plan_baseline": evaluate.baseline_alerts(specs, k["plan_baseline"], quantiles),
                  "window_baseline": triage.window_share_alerts(specs, k["window_baseline"])}
        per_point.append(alerts)
        grid.append({"budget_per_shift": budget,
                     "model": {"harm_threshold": k["model"], **metrics(specs, alerts["model"], shifts)},
                     "plan_baseline": {"quantile": k["plan_baseline"], **metrics(specs, alerts["plan_baseline"], shifts)},
                     "window_baseline": {"share": k["window_baseline"], **metrics(specs, alerts["window_baseline"], shifts)}})
    at = list(GRID_BUDGETS).index(headline)
    alerts = per_point[at]

    # the places, by group and route, to the horizon
    tube_share = sum(s.route == "tube" for s in fit_specs) / len(fit_specs)
    places = {}
    for group, route in sorted({(s.group, s.route) for s in specs}):
        legs = location.legs_for(params, loaded["config"], group, route, tube_share)
        places[(group, route)] = location.place_series(*location.filter_series(legs, HORIZON_MIN))

    # one record per specimen
    brief = {sid: row for sid, *row in obs.execute(
        "select specimen_id, unit, priority, policy_id, urgency_tier from specimens where split = 'test'").fetchall()}
    events: dict[str, list] = {}
    for sid, activity, minute, loc, role, note in obs.execute(f"""
            select e.specimen_id, e.activity, epoch(e.timestamp - c.timestamp) / 60.0, e.location, e.actor_role, e.note
            from events e join events c on c.specimen_id = e.specimen_id and c.activity = ?
            join specimens s on s.specimen_id = e.specimen_id where s.split = 'test'
            order by e.specimen_id, e.seq""", [start]).fetchall():
        events.setdefault(sid, []).append({"activity": activity, "at_min": round(minute, 2), "location": loc, "role": role, "note": note})
    truth = evaluate.truth_places("test")
    strictest = per_point[0]["model"]
    records, risk_at = [], {}
    for s in specs:
        sid = s.specimen_id
        reach = max(HORIZON_MIN, strictest[sid] or 0)
        ticks = [float(t) for t in s.ticks if t <= reach]
        shown = [t for t in ticks if t <= HORIZON_MIN]
        table = places[(s.group, s.route)]
        if alerts["model"][sid] is not None:
            risk_at[sid] = float(series[sid][int(alerts["model"][sid] // evaluate.TICK_MIN) - 1])
        records.append({
            "id": sid, "run": s.run, "type": s.kind, "group": s.group, "tier": s.tier, "priority": brief[sid][1],
            "unit": brief[sid][0], "route": s.route, "policy_id": brief[sid][2], "window_min": s.window,
            "collected_at": s.collected_at, "events": events[sid], "received_min": s.receipt_min,
            "risk": [float(r) for r in series[sid][:len(ticks)]],  # full precision: an alert is a comparison with a threshold
            "places": [[round(float(table[p][int(t)]), 5) for p in location.PLACES] for t in shown],
            "alerts": {name: whole(alerts[name][sid]) for name in ("model", "plan_baseline", "window_baseline")},
            "truth": {"fault": s.fault, "breach": s.breach,
                      "segments": [[state, round(a, 2), None if b is None else round(b, 2)]
                                   for a, b, state in truth[sid] if state in SEGMENT_STATES]}})

    candidates = showcase(specs, alerts, risk_at)
    groups = sorted({s.group for s in specs})
    chain = {s.group: s.chain for s in specs}
    qs = [k["plan_baseline"] for k in knobs]
    return {
        "bundle_version": BUNDLE_VERSION, "notice": NOTICE,
        "source": {"dataset": manifest["dataset"], "sha256": manifest["sha256"], "model": manifest["model"],
                   "prompt_hash": manifest["prompt_hash"], "seed": manifest["seed"], "split": "test",
                   "runs": f"{min(s.run for s in specs)} to {max(s.run for s in specs)}", "specimens": len(specs),
                   "never_collected_set_aside": excluded["without_a_clock"], "breaches": sum(s.breach for s in specs),
                   "shifts": shifts, "clinical_review": manifest["clinical_review"]},
        "tick_min": evaluate.TICK_MIN, "shift_min": evaluate.SHIFT_MIN, "horizon_min": HORIZON_MIN,
        "good_warning_min": evaluate.GOOD_WARNING_MIN, "place_order": list(location.PLACES),
        "policy": policy_block(loaded),
        "model": {"policy_hash": params["policy_hash"], "source_sha256": params["source_sha256"], "fit_runs": params["fit_runs"],
                  "pooled": params["pooled"], "groups": {g: {"mu": params["groups"][g]["mu"], "sigma": params["groups"][g]["sigma"]} for g in groups},
                  "risk": "(p_never + sum_k w_k S_k(window)) / (p_never + sum_k w_k S_k(t)), 1 from the window on; "
                          "S_k is the lognormal survival, k runs over the normal and stalled components"},
        "triage": {"weights": {str(t): w for t, w in weights.items()}, "routing": {k: cfg["routing"][k] for k in (*triage.ROUTES, "still_at_unit")},
                   "source": "config/triage.yaml, every value assumed", "headline_budget_per_shift": headline},
        "baselines": {"plan": {"quantiles": qs, "threshold_min_by_group": {g: [float(np.quantile(
            quantiles[next(n for n in chain[g] if n in quantiles)], q)) for q in qs] for g in groups}},
                      "window": {"shares": [k["window_baseline"] for k in knobs]}},
        "grid": grid, "headline_index": at,
        "specimens": records, "showcase_candidates": candidates, "heroes": load_heroes(candidates)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", choices=["test"], default="test", help="the held-out runs")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    bundle = build()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(bundle, separators=(",", ":")) + "\n", encoding="utf-8")
    h = bundle["grid"][bundle["headline_index"]]
    shown = args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out
    print(f"wrote {shown}: {len(bundle['specimens'])} specimens, {args.out.stat().st_size / 1e6:.2f} MB, "
          f"{len(bundle['grid'])} grid points, {len(bundle['showcase_candidates'])} showcase candidates")
    print(f"headline {h['budget_per_shift']} per shift: model {h['model']['median_warning_all_breaches_min']} min "
          f"against {h['plan_baseline']['median_warning_all_breaches_min']} (plan) and "
          f"{h['window_baseline']['median_warning_all_breaches_min']} (window-aware)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        sys.exit(f"error: {e}")
