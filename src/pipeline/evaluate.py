"""Baseline against risk model on the held-out runs (plan tasks M4 and M5; docs/architecture.md §5.2.1, §9).

    python -m src.pipeline.evaluate

This is the only module that opens the truth store. It does so for the breach label and the planted
fault; every score a method sees comes from the observed file and the parameters fitted on the fit runs.

Every 5 minutes each specimen that has been collected and not received is scored, and a method raises
at most one alert per specimen. Warning time is the minutes from the alert to the window closing; an
alert at or after the close is too late and does not catch a breach. The methods are:

  baseline         alert once the time since collection passes the q-th quantile of the group's observed
                   collection-to-receipt times (the group is specimen type, six-hour block, priority,
                   with the same fallback as the fit). The pre-registered comparison is against this.
  window baseline  alert once a fixed share of the stability window has passed, whatever the specimen is
  model            alert once the expected harm, the risk of missing the window from src/models/risk.py
                   times the urgency weight of the specimen's tier (src/pipeline/triage.py), reaches a
                   threshold. The weights are config/triage.yaml's default; equal weights and a steeper
                   9-3-1 are scored beside it so the weights cannot quietly change the result.

Each has one knob. For a budget of alerts per 8-hour shift the knob is set, by bisection on the fit
runs and from their scans alone (no labels), to the most aggressive value that stays within the budget;
it is then applied unchanged to the held-out runs, whose own alert volume is reported. Median warning
among caught breaches rewards a method that misses the hard cases, so two measures count misses too:
the median warning over all breaches (a miss counts as zero) and the share caught with at least
GOOD_WARNING_MIN of notice. Specimens with no collection scan (never collected) have no clock; they are
counted and set aside. Intervals come from a paired bootstrap over the held-out runs. Writes
results/evaluation.json and, for the model at the operating point in config/triage.yaml,
results/alerts.csv: one row per alert, with its most likely place from the location filter, routed to a role
(by route, or the charge nurse when the filter places it at its unit) and closed at the receipt scan.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from src.generator import config
from src.models import risk
from src.pipeline import intervals, store, triage

ROOT = Path(__file__).resolve().parents[2]
RESULTS_PATH = ROOT / "results" / "evaluation.json"
ALERTS_PATH = ROOT / "results" / "alerts.csv"
TICK_MIN = 5
SHIFT_MIN = 8 * 60
BUDGETS = (1, 2, 4, 6)
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
    tier: int = 3
    route: str = "tube"
    collected_at: str = ""  # ISO time of the collection scan, to the second
    kind: str = ""  # the specimen type

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
               epoch(b.timestamp - a.timestamp) / 60.0, s.urgency_tier, s.planned_route, a.timestamp
        from specimens s
        join events a on a.specimen_id = s.specimen_id and a.activity = ?
        left join events b on b.specimen_id = s.specimen_id and b.activity = ?
        where s.split = ? order by s.specimen_id""", [start, stop, split]).fetchall()
    labels = {} if truth is None else {sid: (breach, fault) for sid, breach, fault in truth.execute(
        "select specimen_id, breach, planted_fault from truth_specimens where split = ?", [split]).fetchall()}
    total = obs.execute("select count(*) from specimens where split = ?", [split]).fetchone()[0]
    specs = []
    for sid, run, kind, priority, hour, window, minutes, tier, route, collected in rows:
        key = (kind, hour // intervals.BLOCK_HOURS, priority)
        chain = tuple(intervals.key_name(k) for k in intervals.parent_chain(*key))
        derived = minutes is None or minutes > window
        breach, fault = labels.get(sid, (derived, "unknown"))
        if breach != derived:
            raise RuntimeError(f"{sid}: the breach label disagrees with its scans and its window")
        specs.append(Spec(sid, run, intervals.key_name(key), chain, int(window),
                          None if minutes is None else float(minutes), bool(breach), fault,
                          int(tier), route, collected.isoformat(), kind))
    return specs, {"specimens": total, "with_a_clock": len(specs), "without_a_clock": total - len(specs)}


def truth_places(split: str) -> dict[str, list[tuple[float, float | None, str]]]:
    """Where each specimen of a split with a clock really was: (from, to, place) in minutes since the
    collection scan, `to` empty while the stay never ends. For scoring the location filter
    (src/pipeline/location_eval.py), which never opens the truth file itself."""
    rows = store.open_truth().execute("""
        select g.specimen_id, epoch(g.start - s.collected_at) / 60.0, epoch(g."end" - s.collected_at) / 60.0, g.state
        from truth_segments g join truth_specimens s using (specimen_id)
        where s.split = ? and s.clock_started order by g.specimen_id, g.seq""", [split]).fetchall()
    out: dict[str, list] = {}
    for sid, begin, end, state in rows:
        out.setdefault(sid, []).append((float(begin), None if end is None else float(end), state))
    return out


# --- The v2 store (docs/neural/spec.md §6.4, §8.1) ------------------------------------------------------------

@dataclass(frozen=True)
class SpecV2(Spec):
    """A Spec from the v2 store, with its scenario, replicate and the slice fields of spec §8.4."""
    scenario_id: str = ""
    replicate: int = 0
    family_id: str = ""
    role: str = "scored"
    unit_kind: str = ""
    route_kind: str = ""
    route_id: str = ""
    outcome_class: str = ""
    cause: str = ""
    disruption_ids: tuple = ()
    horizon_cut: bool = False
    archetype: str = ""
    stratum: str = ""
    specimens_per_weekday: float = 0.0
    edge_tags: tuple = ()


# Spec §5.4: where a specimen is, as the dispatcher would name it, from its truth state and substate.
PLACE_OF = {("at_unit", "pre_dispatch"): "still_at_unit", ("at_unit", "outage_hold"): "still_at_unit",
            ("at_unit", "awaiting_pickup"): "waiting_for_courier", ("at_unit", "not_dispatched"): "still_at_unit",
            ("in_tube", "transit"): "in_tube_system", ("in_tube", "stuck"): "in_tube_system",
            ("misrouted", "wrong_station"): "misrouted_elsewhere", ("at_station", "lab_station"): "at_lab_tube_station",
            ("stalled_at_station", "overlooked"): "at_lab_tube_station",
            ("stalled_at_station", "unattended"): "at_lab_tube_station",
            ("at_station", "receiving_queue"): "in_lab_awaiting_scan", ("hand_carry", "in_transit"): "with_courier",
            ("hand_carry", "walk"): "with_courier", ("hand_carry", "receiving_queue"): "in_lab_awaiting_scan",
            ("at_station", "scanner_backlog"): "in_lab_awaiting_scan",
            ("hand_carry", "scanner_backlog"): "in_lab_awaiting_scan",
            ("lost", "in_tube"): "lost", ("lost", "in_transit"): "lost"}
NOT_ALERTABLE = ("at_receiving_bench", "accessioned", "plated", "not_collected")


def _v2_dir(store_dir: Path | None) -> Path:
    return Path(store_dir) if store_dir is not None else store.STORE_V2_DIR


def _check_v2_policy(con, policy_hash: str) -> None:
    found = dict(con.execute("select key, value from meta").fetchall()).get("policy_hash")
    if found != policy_hash:
        raise RuntimeError("the v2 store was built under a different policy; rebuild it before scoring")


def load_specs_v2(split: str | None, roles=("scored",), store_dir: Path | None = None,
                  labels: bool = True, scenarios=None) -> tuple[list[SpecV2], dict]:
    """The scored-day specimens with a clock of one split of the v2 store, in the given roles (`scored` for
    replicates 1 and up, `history` for replicate 0). With labels, the breach label, fault, cause and outcome
    class come from the truth file and the label is checked against the scans, as load_specs does; without,
    the label is derived from the scans and the window. `scenarios` (scenario ids) narrows the rows, labels
    included, to those scenarios, so the evaluation driver can load a batch per worker; with it, `split` may be
    None for every split (the v1-world control, which a store keeps under whatever split it was given)."""
    loaded = config.load()
    start, stop = intervals.clock_events(loaded["policy"])
    d = _v2_dir(store_dir)
    obs = store.open_observed_v2(d)
    _check_v2_policy(obs, loaded["policy_hash"])
    roles = list(roles)
    if split is None and scenarios is None:
        raise ValueError("name a split, or the scenarios to load")
    where, params = ["s.scored", "list_contains(?, s.role)"], [roles]
    if split is not None:
        where.insert(0, "s.split = ?")
        params.insert(0, split)
    if scenarios is not None:
        where.append("list_contains(?, s.scenario_id)")
        params.append([str(x) for x in scenarios])
    rows = obs.execute(f"""
        select s.specimen_id, s.run, s.specimen_type, s.priority, hour(a.timestamp), s.window_min,
               epoch(b.timestamp - a.timestamp) / 60.0, s.urgency_tier, s.planned_route, a.timestamp,
               s.scenario_id, s.replicate, s.family_id, s.role, s.unit_kind, s.route_kind, s.route_id
        from specimens s
        join events a on a.specimen_id = s.specimen_id and a.activity = ?
        left join events b on b.specimen_id = s.specimen_id and b.activity = ?
        where {" and ".join(where)} order by s.specimen_id""", [start, stop, *params]).fetchall()
    total = obs.execute(f"select count(*) from specimens s where {' and '.join(where)}", params).fetchone()[0]
    truth = {}
    scen = {}
    if labels:
        con = store.open_truth(d)
        _check_v2_policy(con, loaded["policy_hash"])
        t_where, t_params = [], []
        if split is not None:
            t_where.append("split = ?")
            t_params.append(split)
        if scenarios is not None:
            t_where.append("list_contains(?, scenario_id)")
            t_params.append([str(x) for x in scenarios])
        truth = {r[0]: r[1:] for r in con.execute(f"""
            select specimen_id, breach, planted_fault, outcome_class, cause, disruption_ids, horizon_cut
            from truth_specimens{" where " + " and ".join(t_where) if t_where else ""}""", t_params).fetchall()}
        s_where = " where list_contains(?, scenario_id)" if scenarios is not None else ""
        scen = {r[0]: r[1:] for r in con.execute(f"""
            select scenario_id, archetype, stratum, specimens_per_weekday, edge_tags from truth_scenarios{s_where}""",
                                                 [] if scenarios is None else [[str(x) for x in scenarios]]).fetchall()}
    specs = []
    for (sid, run, kind, priority, hour, window, minutes, tier, route, collected, scenario_id, replicate, family_id,
         role, unit_kind, route_kind, route_id) in rows:
        key = (kind, hour // intervals.BLOCK_HOURS, priority)
        chain = tuple(intervals.key_name(k) for k in intervals.parent_chain(*key))
        derived = minutes is None or minutes > window
        breach, fault, outcome, cause, dis, cut = truth.get(sid, (derived, "unknown", "", "", [], False))
        if breach != derived:
            raise RuntimeError(f"{sid}: the breach label disagrees with its scans and its window")
        archetype, stratum, volume, tags = scen.get(scenario_id, ("", "", 0.0, []))
        specs.append(SpecV2(sid, run, intervals.key_name(key), chain, int(window),
                            None if minutes is None else float(minutes), bool(breach), fault, int(tier), route,
                            collected.isoformat(), kind, scenario_id, int(replicate), family_id, role, unit_kind,
                            route_kind, route_id, outcome, cause, tuple(dis or ()), bool(cut), archetype or "",
                            stratum or "", float(volume or 0.0), tuple(tags or ())))
    return specs, {"specimens": total, "with_a_clock": len(specs), "without_a_clock": total - len(specs)}


def truth_places_v2(ticks: dict, store_dir: Path | None = None) -> dict[str, list[str | None]]:
    """The true place category (spec §5.4) of each specimen at each of its ticks (minutes since the collection
    scan): ticks maps specimen ids to tick lists. None where the specimen is past its receipt scan (not
    alertable) or in no segment."""
    ids = sorted(ticks)
    if not ids:
        return {}
    con = store.open_truth(_v2_dir(store_dir))
    rows = con.execute("""
        select g.specimen_id, epoch(g.start - s.collected_at) / 60.0, epoch(g."end" - s.collected_at) / 60.0,
               g.state, g.substate
        from truth_segments g join truth_specimens s using (specimen_id)
        where list_contains(?, g.specimen_id) order by g.specimen_id, g.seq""", [ids]).fetchall()
    segs: dict[str, list] = {}
    for sid, begin, end, state, sub in rows:
        segs.setdefault(sid, []).append((float(begin), math.inf if end is None else float(end), state, sub))
    out = {}
    for sid in ids:
        places = []
        for t in ticks[sid]:
            hit = next(((state, sub) for a, b, state, sub in segs.get(sid, []) if a <= t < b), None)
            if hit is None or hit[0] in NOT_ALERTABLE:
                places.append(None)
            else:
                places.append(PLACE_OF.get(hit, "lost" if hit[0] == "lost" else None))
        out[sid] = places
    return out


def truth_causes_v2(split: str | None = None, store_dir: Path | None = None) -> dict[str, dict]:
    """Per specimen: cause (none, independent, disruption, congestion), the disruptions that touched it, its
    outcome class, whether it was cut at the horizon, and its planted fault."""
    con = store.open_truth(_v2_dir(store_dir))
    sql = """select specimen_id, cause, disruption_ids, outcome_class, horizon_cut, planted_fault
             from truth_specimens""" + (" where split = ?" if split is not None else "")
    return {sid: {"cause": cause, "disruption_ids": tuple(dis or ()), "outcome_class": outcome,
                  "horizon_cut": bool(cut), "fault": fault}
            for sid, cause, dis, outcome, cut, fault in con.execute(sql, [split] if split is not None else []).fetchall()}


def truth_disruptions_v2(scenario_id: str | None = None, replicate: int | None = None,
                         store_dir: Path | None = None) -> list[dict]:
    """The realized disruption windows, optionally for one scenario and replicate, in time order."""
    con = store.open_truth(_v2_dir(store_dir))
    where, params = [], []
    if scenario_id is not None:
        where.append("scenario_id = ?")
        params.append(scenario_id)
    if replicate is not None:
        where.append("replicate = ?")
        params.append(int(replicate))
    sql = "select * from truth_disruptions" + (" where " + " and ".join(where) if where else "") + \
          " order by scenario_id, replicate, start_min, disruption_id"
    cur = con.execute(sql, params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def scenario_cells_v2(splits=("train", "val"), store_dir: Path | None = None) -> dict[str, dict]:
    """Each scenario's planned cell (archetype, stratum and the planner's factors), for the named splits only."""
    con = store.open_truth(_v2_dir(store_dir))
    rows = con.execute("""select scenario_id, split, archetype, stratum, size_band, transport_mix, staffing,
                                 failure_focus, edge_tags from truth_scenarios
                          where list_contains(?, split) order by scenario_id""", [list(splits)]).fetchall()
    return {r[0]: {"split": r[1], "archetype": r[2], "stratum": r[3], "size_band": r[4], "transport_mix": r[5],
                   "staffing": r[6], "failure_focus": r[7], "edge_tags": list(r[8] or [])} for r in rows}


def load_test_specs(start: str, stop: str) -> tuple[list[Spec], dict]:
    """The held-out specimens with their breach labels and planted faults, for the demo bundle
    (src/api/export.py), which never opens the truth file itself."""
    return load_specs(store.open_observed(), store.open_truth(), start, stop, "test")


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


def by_tier(specs, alerts, shifts) -> dict:
    """The same scores for each urgency tier, with the alerts each tier receives."""
    out = {}
    for tier in triage.TIERS:
        sub = [s for s in specs if s.tier == tier]
        sc = score(sub, alerts, shifts)
        out[str(tier)] = {"specimens": len(sub)} | {k: sc[k] for k in (
            "alerts", "alerts_per_shift", "breaches", "caught", "catch_rate", "median_warning_min",
            "median_warning_all_breaches_min")}
    return out


def bootstrap(specs, alert_sets: dict, shifts_per_run: float, base: str = "baseline", other: str = "model") -> dict:
    """Paired bootstrap over the held-out runs: resample whole runs, rescore both methods on the same draw."""
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    runs = sorted({s.run for s in specs})
    by_run = {r: [s for s in specs if s.run == r] for r in runs}
    diffs = {"median_warning_min": [], "median_warning_all_breaches_min": [], "catch_rate": []}
    for _ in range(BOOTSTRAPS):
        draw = rng.choice(runs, size=len(runs), replace=True)
        sample = [s for r in draw for s in by_run[r]]
        a = score(sample, alert_sets[base], shifts_per_run * len(draw))
        b = score(sample, alert_sets[other], shifts_per_run * len(draw))
        for k in diffs:
            if a[k] is not None and b[k] is not None:
                diffs[k].append(b[k] - a[k])
    return {k: {"low": float(np.percentile(v, 2.5)), "high": float(np.percentile(v, 97.5)),
                "share_model_ahead": float(np.mean(np.array(v) > 0))} for k, v in diffs.items()}


def scored(specs, alerts, shifts, faults: bool = True) -> dict:
    out = score(specs, alerts, shifts)
    out["by_window"] = by_group(specs, alerts, shifts, lambda s: "short (up to 2 h)" if s.window <= SHORT_WINDOW_MAX else "long (a day or more)")
    if faults:
        out["by_fault"] = by_group([s for s in specs if s.breach], alerts, shifts, lambda s: s.fault)
    return out | {"by_tier": by_tier(specs, alerts, shifts)}


def run() -> tuple[dict, list[dict]]:
    """The evaluation result and the alert list for the held-out runs."""
    loaded = config.load()
    cfg = triage.load()
    schemes = triage.weight_schemes(cfg)
    headline = cfg["operating_point"]["budget_per_shift"]
    if headline not in BUDGETS:
        raise RuntimeError(f"the headline budget of {headline} alerts per shift is not one of {BUDGETS}")
    start, stop = intervals.clock_events(loaded["policy"])
    params = json.loads(intervals.PARAMS_PATH.read_text(encoding="utf-8"))
    if params["policy_hash"] != loaded["policy_hash"]:
        raise RuntimeError("the fitted parameters were made under a different policy; refit before scoring")
    obs, truth = store.open_observed(), store.open_truth()
    fit_rows = intervals.load_intervals(obs, start, stop)
    quantiles = baseline_quantiles(fit_rows)

    fit_specs, _ = load_specs(obs, None, start, stop, "fit")  # no labels: only used to set the knobs
    fit_series = risk_series(fit_specs, params)
    fit_harm = {name: triage.harm_series(fit_specs, fit_series, w) for name, w in schemes.items()}
    tube_share = sum(s.route == "tube" for s in fit_specs) / len(fit_specs)  # observed at collection
    fit_shifts = len({s.run for s in fit_specs}) * 7 * 3

    specs, excluded = load_specs(obs, truth, start, stop, "test")
    series = risk_series(specs, params)
    harm = {name: triage.harm_series(specs, series, w) for name, w in schemes.items()}
    runs = sorted({s.run for s in specs})
    shifts = len(runs) * 7 * 3  # three 8-hour shifts a day, seven days a run

    result = {"policy_hash": params["policy_hash"], "source_sha256": params["source_sha256"],
              "held_out_runs": f"{runs[0]} to {runs[-1]}", "shift_minutes": SHIFT_MIN, "tick_minutes": TICK_MIN,
              "good_warning_min": GOOD_WARNING_MIN, "test_specimens": excluded["specimens"],
              "with_a_clock": excluded["with_a_clock"], "without_a_clock": excluded["without_a_clock"],
              "breaches": sum(s.breach for s in specs), "shifts": shifts,
              "breaches_per_shift": sum(s.breach for s in specs) / shifts,
              "specimens_per_shift": len(specs) / shifts,
              "triage": {"source": "config/triage.yaml, every value assumed", "headline_budget_per_shift": headline,
                         "weights": {name: {str(t): w for t, w in by_t.items()} for name, by_t in schemes.items()},
                         "routing": {k: cfg["routing"][k] for k in (*triage.ROUTES, "still_at_unit")}},
              "operating_points": {}}
    rows = []
    for budget in BUDGETS:
        q = tune(lambda v: baseline_alerts(fit_specs, v, quantiles), 0.0, 0.9999, fit_specs, fit_shifts, budget, 30)
        share = tune(lambda v: triage.window_share_alerts(fit_specs, v), 0.0, 1.0, fit_specs, fit_shifts, budget, 30)
        theta = {name: tune(lambda v: model_alerts(fit_specs, fit_harm[name], v), 0.0, float(max(w.values())),
                            fit_specs, fit_shifts, budget, 30) for name, w in schemes.items()}
        alerts = {"baseline": baseline_alerts(specs, q, quantiles),
                  "window_baseline": triage.window_share_alerts(specs, share),
                  "model": model_alerts(specs, harm["default"], theta["default"])}
        checks = {name: model_alerts(specs, harm[name], theta[name]) for name in ("equal", "steep")}
        point = {"budget_per_shift": budget, "baseline_quantile": q, "window_share": share,
                 "model_threshold": theta["default"]}
        for name, a in alerts.items():
            point[name] = scored(specs, a, shifts)
        point["weights_check"] = {name: {"harm_threshold": theta[name]} | scored(specs, a, shifts, faults=False)
                                  for name, a in checks.items()}
        point["bootstrap_model_minus_baseline"] = bootstrap(specs, alerts, 7 * 3)
        point["bootstrap_model_minus_window_baseline"] = bootstrap(specs, alerts, 7 * 3, "window_baseline")
        equal = alerts | {"equal": checks["equal"]}  # the conservative weights, against the same two baselines
        point["weights_check"]["equal"]["bootstrap_minus_baseline"] = bootstrap(specs, equal, 7 * 3, "baseline", "equal")
        point["weights_check"]["equal"]["bootstrap_minus_window_baseline"] = bootstrap(
            specs, equal, 7 * 3, "window_baseline", "equal")
        result["operating_points"][str(budget)] = point
        if budget == headline:
            places = triage.likely_places(specs, alerts["model"], params, loaded["config"], tube_share)
            rows = triage.alert_rows(specs, alerts["model"], series, harm["default"], places, cfg)
    result["alert_list"] = {"budget_per_shift": headline, "alerts": len(rows), "file": "results/alerts.csv",
                            "by_role": {r: sum(row["role"] == r for row in rows) for r in sorted({x["role"] for x in rows})},
                            "by_tier": {str(t): sum(row["tier"] == t for row in rows) for t in triage.TIERS}}
    result["calibration"] = {str(m): reliability(specs, params, m) for m in LANDMARKS}
    return result, rows


def evaluate() -> dict:
    return run()[0]


def table(r: dict) -> str:
    head = (f"held-out runs {r['held_out_runs']}: {r['with_a_clock']} specimens with a clock "
            f"({r['specimens_per_shift']:.1f} per 8-hour shift), {r['breaches']} breaches "
            f"({r['breaches_per_shift']:.2f} per shift); {r['without_a_clock']} never collected, set aside",
            "knobs are set on the fit runs to the alert budget, then applied unchanged to the held-out runs",
            "synthetic data, no clinical review; every weight and role is an assumption (config/triage.yaml)")
    fmt = lambda v, d=1: "-" if v is None else f"{v:.{d}f}"
    pct = lambda v: "-" if v is None else f"{100 * v:.0f}%"
    names = {"default": "3-2-1", "equal": "equal", "steep": "9-3-1"}
    lines = [*head, ""]
    for budget, p in r["operating_points"].items():
        a, w, b = p["baseline"], p["window_baseline"], p["model"]
        lines += [f"{budget} alerts per shift: baseline q = {p['baseline_quantile']:.3f}, window share = {p['window_share']:.3f}, "
                  f"harm threshold = {p['model_threshold']:.3f}",
                  f"  {'':30s}{'baseline':>10s}{'window':>10s}{'model':>10s}"]
        for label, key, f in (("alerts per shift", "alerts_per_shift", lambda v: fmt(v, 2)),
                              ("median warning, caught (min)", "median_warning_min", fmt),
                              ("median warning, all (min)", "median_warning_all_breaches_min", fmt),
                              ("caught with >= %d min" % r["good_warning_min"], "caught_with_good_warning", pct)):
            lines.append(f"  {label:30s}{f(a[key]):>10s}{f(w[key]):>10s}{f(b[key]):>10s}")
        lines.append(f"  {'breaches caught':30s}{a['caught']:>10d}{w['caught']:>10d}{b['caught']:>10d}  of {b['breaches']}")
        for other, label in (("", "the plan's baseline"), ("_window", "the window-aware baseline")):
            boot = p[f"bootstrap_model_minus{other}_baseline"]
            for key, what in (("median_warning_min", "caught"), ("median_warning_all_breaches_min", "all")):
                d = boot[key]
                lines.append(f"  model minus {label}, median warning {what}: {d['low']:+.1f} to {d['high']:+.1f} min "
                             f"(95%), ahead in {d['share_model_ahead']:.0%} of resamples")
        lines.append(f"  {'model, by weights':30s}" + "".join(f"{names[n]:>10s}" for n in ("equal", "default", "steep")))
        variants = {"equal": p["weights_check"]["equal"], "default": b, "steep": p["weights_check"]["steep"]}
        for label, key, f in (("alerts per shift", "alerts_per_shift", lambda v: fmt(v, 2)),
                              ("breaches caught", "caught", lambda v: str(v)),
                              ("median warning, caught (min)", "median_warning_min", fmt),
                              ("median warning, all (min)", "median_warning_all_breaches_min", fmt)):
            lines.append(f"  {label:30s}" + "".join(f"{f(variants[n][key]):>10s}" for n in ("equal", "default", "steep")))
        for other, label in (("", "the plan's baseline"), ("_window", "the window-aware baseline")):
            d = variants["equal"][f"bootstrap_minus{other}_baseline"]["median_warning_all_breaches_min"]
            lines.append(f"  equal weights minus {label}, median warning all: {d['low']:+.1f} to {d['high']:+.1f} min "
                         f"(95%), ahead in {d['share_model_ahead']:.0%} of resamples")
        lines.append(f"  {'model 3-2-1, by tier':30s}" + "".join(f"{'tier ' + t:>10s}" for t in b["by_tier"]))
        for label, f in (("alerts per shift", lambda g: fmt(g["alerts_per_shift"], 2)),
                         ("breaches caught", lambda g: f"{g['caught']}/{g['breaches']}"),
                         ("median warning, caught (min)", lambda g: fmt(g["median_warning_min"]))):
            lines.append(f"  {label:30s}" + "".join(f"{f(g):>10s}" for g in b["by_tier"].values()))
        lines.append("")
    al = r["alert_list"]
    lines += [f"alert list at {al['budget_per_shift']} per shift: {al['alerts']} alerts, "
              + ", ".join(f"{n} to the {role}" for role, n in al["by_role"].items()) + f" ({al['file']})", ""]
    for m, c in r["calibration"].items():
        lines += [f"calibration at {m} min silent ({c['n']} specimens, {c['breach_rate']:.1%} breach): Brier {c['brier']:.4f} "
                  f"against {c['brier_of_the_base_rate']:.4f} for the base rate",
                  f"  {'predicted':>10s}{'observed':>10s}{'n':>7s}"]
        lines += [f"  {b['predicted']:>10.3f}{b['observed']:>10.3f}{b['n']:>7d}" for b in c["bins"]]
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    result, rows = run()
    RESULTS_PATH.parent.mkdir(exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    with ALERTS_PATH.open("w", newline="", encoding="utf-8") as f:
        out = csv.DictWriter(f, fieldnames=triage.ALERT_COLUMNS, lineterminator="\n")
        out.writeheader()
        out.writerows(rows)
    print(table(result))
    rel = lambda p: p.relative_to(ROOT) if p.is_relative_to(ROOT) else p
    print(f"wrote {rel(RESULTS_PATH)} and {rel(ALERTS_PATH)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        sys.exit(f"error: {e}")
