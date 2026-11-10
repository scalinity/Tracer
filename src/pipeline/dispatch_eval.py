"""Scoring the decision layer (docs/neural/spec.md §11.3): X2's dispatcher, X3's zero-shot forecasts, X1's audit.

    python -m src.neural.pipeline decide --job X2 --set val --score-only     (the pipeline calls these functions)

Labels come only through src/pipeline/evaluate.py's accessors (load_specs_v2, truth_places_v2, truth_causes_v2);
this module never opens the hidden side itself. On a locked split (test, test_ood) every function first checks the
test run the lock recorded (`test_run.check(split, store_dir=...)`), as the evaluation does.

X2, per alert and per method (the decision model's dispatch, rule R1, rule R2):
- the true place at the alert tick (spec §5.4's table, truth_places_v2); alerts with no place there are counted and
  left out;
- place accuracy; action appropriateness against the table below; role accuracy (the role owns an appropriate
  action);
- shared_problem against "the cause is a disruption, or at least two specimens of the same replicate and route are
  past their window unreceived during the clock hour of the alert" (that hour is this module's reading of "late in
  the same hour"; source: assumed), as AUROC and AUPRC of the probability and precision and recall of the decision;
- escalate against "unrecoverable": never received, or received more than 60 minutes after the window;
- the assumed counterfactual (config/decision_model.yaml, source: assumed): an appropriate action finds the specimen
  10 minutes after the alert and it is received 5 minutes later, which averts a breach when that is within the
  window; for a lost specimen an appropriate action starts a recollection, counted separately. Breaches averted per
  100 alerts for each method.
Every metric carries a 95% interval from a cluster bootstrap over scenarios (2,000 resamples, seed 0, the draws of
neural_eval.draw_counts), as do the role-accuracy differences. The adoption rule (pre-registered): the decision
model's dispatch is adopted only if its role accuracy beats both R1 and R2 by at least 5 points, with the lower end of
the 95% interval of each difference above zero, on test. Otherwise R2 ships, and the comparison is reported.

    | True place            | Appropriate actions                                                          |
    | still_at_unit         | call_unit_confirm_sent                                                       |
    | waiting_for_courier   | contact_courier_coordinator, call_unit_confirm_sent                          |
    | with_courier          | contact_courier_coordinator                                                  |
    | in_tube_system        | escalate_tube_system, wait_and_recheck                                       |
    | at_lab_tube_station   | check_lab_tube_station                                                       |
    | misrouted_elsewhere   | check_misroute_station                                                       |
    | in_lab_awaiting_scan  | check_receiving_queue                                                        |
    | lost                  | request_recollection, plus escalate_tube_system (in a tube) or               |
    |                       | contact_courier_coordinator (in transit)                                     |

X3: the decision model's p(miss), TracerNet's p_miss and the local analytic refit's risk on the same ticks (the ticks
the model refused are left out of all three), against the label; AUROC, AUPRC, Brier score and expected calibration
error, each with a cluster-bootstrap interval, and the decision model's differences from the other two. No threshold.

X1: the plausibility score's AUROC on the negative controls (corrupted against clean), with the soft-filter verdict
(AUROC at least 0.80) and, when it holds, the review list (scenarios whose most likely level is one of the two
lowest); agreement of `dominant_failure` with the planner's failure focus, and of each edge question with the tag the
planner requested.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Sequence

import numpy as np

from src.decide import rules
from src.pipeline import evaluate, neural_eval as ne

LOCKED = ne.LOCKED_SPLITS
APPROPRIATE = {
    "still_at_unit": {"call_unit_confirm_sent"},
    "waiting_for_courier": {"contact_courier_coordinator", "call_unit_confirm_sent"},
    "with_courier": {"contact_courier_coordinator"},
    "in_tube_system": {"escalate_tube_system", "wait_and_recheck"},
    "at_lab_tube_station": {"check_lab_tube_station"},
    "misrouted_elsewhere": {"check_misroute_station"},
    "in_lab_awaiting_scan": {"check_receiving_queue"},
}
UNRECOVERABLE_AFTER_MIN = 60
ADOPTION_MARGIN = 0.05
SOFT_FILTER_AUROC = 0.80
METHODS = ("model", "R1", "R2")
EPOCH = datetime(1970, 1, 1)


def guard(split: str, test_run, store_dir: Path) -> None:
    """Refuse a locked split without the test run its lock recorded (and the store it recorded)."""
    if split in LOCKED:
        check = getattr(test_run, "check", None)
        if not callable(check):
            raise ne.LockError(f"{split} is scored only through the test lock: pass the test run guard_test_run "
                               "recorded")
        check(split, store_dir=store_dir)


def appropriate_actions(place: str, lost_in: str | None = None) -> set[str]:
    if place == "lost":
        return {"request_recollection", "escalate_tube_system" if lost_in == "tube" else "contact_courier_coordinator"}
    return set(APPROPRIATE.get(place, ()))


def appropriate_roles(place: str, lost_in: str | None = None) -> set[str]:
    return {rules.role(a) for a in appropriate_actions(place, lost_in)}


def _minutes(iso: str) -> float:
    return (datetime.fromisoformat(iso).replace(tzinfo=None) - EPOCH).total_seconds() / 60


# --- the labels each alert is scored against --------------------------------------------------------------------------

def x2_labels(rows: Sequence[dict], split: str, store_dir: Path, *, test_run=None) -> dict[str, dict]:
    """Per alert id: the true place at the tick, where a lost specimen was lost, whether the delay was shared, whether
    it was unrecoverable, its breach, receipt and window."""
    guard(split, test_run, store_dir)
    scenarios = sorted({r["scenario_id"] for r in rows})
    specs, _ = evaluate.load_specs_v2(split, roles=("scored",), store_dir=store_dir, scenarios=scenarios)
    by_id = {s.specimen_id: s for s in specs}
    places = evaluate.truth_places_v2({r["specimen_id"]: [float(r["tick_min"])] for r in rows}, store_dir)
    causes = evaluate.truth_causes_v2(split, store_dir)
    late: dict = {}
    for s in specs:
        if not s.breach:
            continue
        col = _minutes(s.collected_at)
        end = math.inf if s.receipt_min is None else col + s.receipt_min
        late.setdefault((s.run, s.route_id), []).append((col + s.window, end))
    out = {}
    for r in rows:
        s = by_id.get(r["specimen_id"])
        if s is None:
            raise ValueError(f"{r['specimen_id']} is not a scored specimen of {split}")
        at = _minutes(s.collected_at) + float(r["tick_min"])
        h0 = math.floor(at / 60) * 60
        overdue = sum(1 for a, b in late.get((s.run, s.route_id), ()) if a < h0 + 60 and b > h0)
        c = causes.get(s.specimen_id, {})
        fault = c.get("fault")
        lost_in = "tube" if fault == "lost_in_tube" else "transit" if fault == "lost_in_transit" else \
            ("tube" if s.route_kind == "tube" else "transit")
        out[r["alert_id"]] = {
            "place": places.get(s.specimen_id, [None])[0], "lost_in": lost_in,
            "shared": c.get("cause") == "disruption" or overdue >= 2,
            "unrecoverable": s.receipt_min is None or s.receipt_min > s.window + UNRECOVERABLE_AFTER_MIN,
            "breach": bool(s.breach), "receipt_min": s.receipt_min, "window_min": s.window,
            "route_kind": s.route_kind}
    return out


# --- the bootstrap ----------------------------------------------------------------------------------------------------

class Clusters:
    """Scenario clusters and their bootstrap draws (neural_eval.draw_counts: evaluate.bootstrap's call pattern)."""

    def __init__(self, keys: Sequence[str], resamples: int = ne.BOOTSTRAPS, seed: int = ne.BOOTSTRAP_SEED):
        self.names = sorted(set(keys))
        index = {k: i for i, k in enumerate(self.names)}
        self.of = np.array([index[k] for k in keys], dtype=np.int64)
        self.counts = ne.draw_counts(len(self.names), resamples, seed) if resamples else np.zeros((0, len(self.names)))

    def sums(self, values: np.ndarray) -> np.ndarray:
        return np.bincount(self.of, weights=np.asarray(values, dtype=float), minlength=len(self.names))

    def ratio(self, num, den) -> tuple[float | None, np.ndarray]:
        n, d = self.sums(num), self.sums(den)
        point = float(n.sum() / d.sum()) if d.sum() > 0 else None
        with np.errstate(divide="ignore", invalid="ignore"):
            draws = (self.counts @ n) / (self.counts @ d)
        return point, draws[np.isfinite(draws)]

    def weights(self, b: int) -> np.ndarray:
        return self.counts[b][self.of].astype(float)


def _ci(point, draws: np.ndarray) -> dict:
    return {"point": point, **ne._interval(np.asarray(draws, dtype=float))}


def _diff(c: Clusters, a: tuple, b: tuple) -> dict:
    pa, da = c.ratio(*a)
    pb, db = c.ratio(*b)
    n1, d1, n2, d2 = (c.sums(x) for x in (*a, *b))
    with np.errstate(divide="ignore", invalid="ignore"):
        draws = (c.counts @ n1) / (c.counts @ d1) - (c.counts @ n2) / (c.counts @ d2)
    draws = draws[np.isfinite(draws)]
    point = None if pa is None or pb is None else pa - pb
    return {**_ci(point, draws), "share_ahead": float(np.mean(draws > 0)) if len(draws) else None}


def _boot_stat(c: Clusters, fn, *arrays) -> np.ndarray:
    out = []
    for b in range(len(c.counts)):
        w = c.weights(b)
        v = fn(*arrays, w)
        if v is not None and np.isfinite(v):
            out.append(v)
    return np.asarray(out)


# --- X2 ---------------------------------------------------------------------------------------------------------------

def _decision(row: dict, method: str) -> dict:
    if method == "model":
        return row["dispatch"]
    return row[method.lower()]


def x2_scores(rows: Sequence[dict], labels: dict[str, dict], split: str, cfg: dict, *,
              resamples: int = ne.BOOTSTRAPS, seed: int = ne.BOOTSTRAP_SEED) -> dict:
    """X2's metrics on the whole set and on each slice by true place and by route kind, every one with its
    scenario-cluster bootstrap interval; a metric a slice cannot define (no positives, nothing flagged, one class
    only) says "undefined" and why. The adoption rule reads the whole set."""
    found = float(cfg["counterfactual"]["found_after_alert_min"])
    received = float(cfg["counterfactual"]["received_after_found_min"])
    yes = float(cfg["decisions"]["noul_yes"])
    scored = [r for r in rows if labels[r["alert_id"]]["place"] is not None]
    lab = [labels[r["alert_id"]] for r in scored]
    per: dict = {}
    for m in METHODS:
        d = [_decision(r, m) for r in scored]
        action = np.array([x["action"] in appropriate_actions(y["place"], y["lost_in"]) for x, y in zip(d, lab)],
                          dtype=float)
        lost = np.array([y["place"] == "lost" for y in lab], dtype=float)
        per[m] = {"place": np.array([x["place"] == y["place"] for x, y in zip(d, lab)], dtype=float),
                  "action": action,
                  "role": np.array([x["role"] in appropriate_roles(y["place"], y["lost_in"]) for x, y in zip(d, lab)],
                                   dtype=float),
                  "averted": np.array([a and y["place"] != "lost" and y["breach"]
                                       and r["tick_min"] + found + received <= y["window_min"]
                                       for a, y, r in zip(action, lab, scored)], dtype=float),
                  "recollections": action * lost}
    whole = _x2_block(np.arange(len(scored)), scored, lab, per, yes, resamples, seed)
    out = {**whole, "set": split, "alerts": len(rows), "scored_alerts": len(scored),
           "alerts_without_a_place": len(rows) - len(scored),
           "bootstrap": {"resamples": resamples, "seed": seed, "clusters": "scenarios"},
           "counterfactual": {"found_after_alert_min": found, "received_after_found_min": received, "source": "assumed"},
           "model_source": _counts([r["dispatch"]["source"] for r in rows]), "slices": {}}
    for name, key in (("by_place", "place"), ("by_route_kind", "route_kind")):
        groups: dict = {}
        for i, y in enumerate(lab):
            groups.setdefault(y[key], []).append(i)
        out["slices"][name] = {g: _x2_block(np.asarray(ix), scored, lab, per, yes, resamples, seed)
                               for g, ix in sorted(groups.items())}
    out["adoption"] = adoption(out, binding=split == "test")
    return out


def _x2_block(ix: np.ndarray, scored, lab, per: dict, yes: float, resamples: int, seed: int) -> dict:
    """Every X2 metric over the alerts `ix`, its intervals from a bootstrap over their own scenarios."""
    rows = [scored[i] for i in ix]
    sub = [lab[i] for i in ix]
    c = Clusters([r["scenario_id"] for r in rows], resamples, seed)
    ones = np.ones(len(ix))
    out = {"alerts": len(ix), "scenarios": len(c.names), "methods": {}, "differences": {}}
    for m in METHODS:
        p = {k: v[ix] for k, v in per[m].items()}
        out["methods"][m] = {
            "place_accuracy": _defined(c.ratio(p["place"], ones), "no alerts"),
            "action_appropriate": _defined(c.ratio(p["action"], ones), "no alerts"),
            "role_accuracy": _defined(c.ratio(p["role"], ones), "no alerts"),
            "breaches_averted_per_100_alerts": _defined(_per100(c, p["averted"], ones), "no alerts"),
            "recollections_started_per_100_alerts": _defined(_per100(c, p["recollections"], ones), "no alerts")}
    for m in ("R1", "R2"):
        out["differences"][f"model - {m}"] = {"role_accuracy": _diff(c, (per["model"]["role"][ix], ones),
                                                                     (per[m]["role"][ix], ones))}
    out["shared_problem"] = _binary_block(c, rows, sub, "shared", "shared_problem", yes)
    out["escalate"] = _binary_block(c, rows, sub, "unrecoverable", "escalate", yes)
    return out


def _defined(ratio: tuple, why: str) -> dict:
    """A metric with its interval, or marked undefined with the reason when it has no value."""
    point, draws = ratio
    out = _ci(point, draws)
    if point is None:
        out["undefined"] = why
    return out


def _per100(c: Clusters, num, den):
    point, draws = c.ratio(num, den)
    return (None if point is None else 100 * point), 100 * draws


def _binary_block(c: Clusters, rows, lab, label_key: str, question: str, yes: float) -> dict:
    y = np.array([x[label_key] for x in lab], dtype=float)
    p_model = np.array([np.nan if r["dispatch"][f"{question}_p"] is None else r["dispatch"][f"{question}_p"]
                        for r in rows], dtype=float)
    has = ~np.isnan(p_model)
    out = {"label": label_key, "positives": int(y.sum()), "alerts": len(y), "threshold": yes,
           "model_probabilities": int(has.sum())}
    sub = Clusters([r["scenario_id"] for r, h in zip(rows, has) if h], len(c.counts), ne.BOOTSTRAP_SEED) \
        if has.any() else None

    def why(yy):
        return "no alerts" if len(yy) == 0 else ("no positives" if yy.sum() == 0 else
                                                 ("no negatives" if yy.sum() == len(yy) else "undefined"))
    r2 = np.array([bool(r["r2"][question]) for r in rows], dtype=float)
    for name, fn in (("auroc", ne.auroc), ("auprc", ne.auprc)):
        if sub is not None:
            out[f"model_{name}"] = _defined((fn(y[has], p_model[has]), _boot_stat(sub, fn, y[has], p_model[has])),
                                            why(y[has]))
        else:
            out[f"model_{name}"] = _defined((None, np.array([])), "no model probabilities")
        out[f"R2_{name}"] = _defined((fn(y, r2), _boot_stat(c, fn, y, r2)), why(y))
    for m in ("model", "R2"):
        flag = np.array([bool(_decision(r, m)[question]) for r in rows], dtype=float)
        out[f"{m}_precision"] = _defined(c.ratio(flag * y, flag), "nothing flagged")
        out[f"{m}_recall"] = _defined(c.ratio(flag * y, y), "no positives")
    return out


def adoption(scores: dict, binding: bool) -> dict:
    """The pre-registered rule: adopt the decision model's dispatch only if its role accuracy beats R1 and R2 by at
    least 5 points each, with the lower end of each difference's 95% interval above zero, on test."""
    checks = {}
    for m in ("R1", "R2"):
        d = scores["differences"][f"model - {m}"]["role_accuracy"]
        checks[m] = {"difference": d["point"], "low": d["low"],
                     "holds": d["point"] is not None and d["low"] is not None
                     and d["point"] >= ADOPTION_MARGIN - 1e-12 and d["low"] > 0}
    adopt = all(v["holds"] for v in checks.values())
    return {"rule": "role accuracy beats R1 and R2 by at least 5 points, each difference's 95% interval above zero, "
                    "on test", "checks": checks, "holds": adopt, "binding": binding,
            "dispatch_source": ("decision model" if adopt else "rules R2") if binding else None}


def _counts(values) -> dict:
    out: dict = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items()))


# --- X3 ---------------------------------------------------------------------------------------------------------------

def x3_labels(rows: Sequence[dict], split: str, store_dir: Path, *, test_run=None) -> dict[str, dict]:
    """Per tick: the label (the specimen breached its window) and the specimen's Spec, for the local refit."""
    guard(split, test_run, store_dir)
    scenarios = sorted({r["scenario_id"] for r in rows})
    specs, _ = evaluate.load_specs_v2(split, roles=("scored",), store_dir=store_dir, scenarios=scenarios)
    by_id = {s.specimen_id: s for s in specs}
    return {r["item_id"]: {"miss": bool(by_id[r["specimen_id"]].breach)} for r in rows}


def tracernet_at(rows: Sequence[dict], files: Sequence, expected: dict | None = None) -> dict[str, float]:
    """TracerNet's p_miss at each tick, from the parts of the set's risk table, each decoded from the bytes read.
    With `expected` ({part name: SHA-256}, the parts a test lock recorded), the parts must be exactly those, and
    each part's bytes are checked against its hash before they are decoded."""
    import hashlib
    import io

    import polars as pl

    ids = sorted({r["specimen_id"] for r in rows})
    if expected is not None and {Path(f).name for f in files} != set(expected):
        raise ne.LockError("the risk table's parts are not the ones the test lock recorded")
    frames = []
    for f in files:
        raw = Path(f).read_bytes()
        if expected is not None and hashlib.sha256(raw).hexdigest() != expected[Path(f).name]:
            raise ne.LockError(f"the risk table part {Path(f).name} is not the one the test lock recorded")
        frames.append(pl.read_parquet(io.BytesIO(raw)).filter(pl.col("specimen_id").is_in(ids))
                      .select("specimen_id", "tick_min", "p_miss"))
    frame = pl.concat(frames) if frames else pl.DataFrame()
    table = {(s, int(t)): float(v) for s, t, v in frame.iter_rows()}
    out = {}
    for r in rows:
        key = (r["specimen_id"], int(r["tick_min"]))
        if key not in table:
            raise ValueError(f"the risk table lacks {key[0]} at tick {key[1]}")
        out[r["item_id"]] = table[key]
    return out


def analytic_local_at(rows: Sequence[dict], split: str, store_dir: Path, *, train_store_dir: Path | None = None,
                      test_run=None, read=None) -> tuple[dict[str, float], dict]:
    """The analytic model refit on each scenario's own history replicate (spec §8.2), its risk at each tick; the
    global fit on train is used, and noted, where the history has fewer than MIN_LOCAL_FIT specimens with a clock,
    the refit fails or its risk is not a number at a tick, as neural_eval's local method does. Every store read goes
    through `read(fn)` (the pipeline runs each inside the test lock's mutex on a locked split); the fits are computed
    on what it returned."""
    guard(split, test_run, store_dir)
    from src.generator import config
    from src.pipeline import intervals

    read = read or (lambda fn: fn())
    scenarios = sorted({r["scenario_id"] for r in rows})
    history, scored = read(lambda: (
        evaluate.load_specs_v2(split, roles=("history",), store_dir=store_dir, labels=False, scenarios=scenarios)[0],
        evaluate.load_specs_v2(split, roles=("scored",), store_dir=store_dir, labels=False, scenarios=scenarios)[0]))
    need = {r["specimen_id"] for r in rows}
    hist_of: dict = {}
    for s in history:
        hist_of.setdefault(s.scenario_id, []).append(s)
    spec_of: dict = {}
    for s in scored:
        if s.specimen_id in need:
            spec_of.setdefault(s.scenario_id, []).append(s)
    loaded = config.load()
    start, stop = intervals.clock_events(loaded["policy"])
    global_fit: dict = {}

    def glob(groups):
        if "setup" not in global_fit:
            global_fit["setup"] = ne.Setup("v2", split, Path(store_dir), Path(train_store_dir or store_dir), start,
                                           stop, loaded["policy_hash"], groups)
        setup = global_fit["setup"]
        train_rows = read(setup.rows)              # the store read; the fit below runs on the rows it returned
        return ne.complete_groups(setup.analytic_params(), train_rows, groups)

    risk, notes = {}, {}
    for scenario in scenarios:
        specs = spec_of.get(scenario, [])
        hist = hist_of.get(scenario, [])
        groups = {s.group for s in specs} | {s.group for s in hist}
        rows_h = ne.interval_rows(hist)
        params, note = None, None
        if len(rows_h) < ne.MIN_LOCAL_FIT:
            note = f"{len(rows_h)} specimens with a clock in the history: global fit used"
        else:
            try:
                fit = intervals.fit_all(rows_h)
                params = ne.complete_groups(fit, rows_h, groups) if ne._valid_fit(fit) else None
                note = None if params is not None else "the refit gave an out-of-range parameter: global fit used"
            except Exception as e:  # noqa: BLE001 - as the evaluation, any optimizer failure falls back and is noted
                note = f"the refit failed ({type(e).__name__}): global fit used"
        flat = ne.flat_ticks(specs)
        values = ne.analytic_risk(specs, flat, params) if params is not None else None
        if values is None or not np.all(np.isfinite(values)):
            note = note or "the refit's risk is not a number at some tick: global fit used"
            values = ne.analytic_risk(specs, flat, glob(groups))
        index = {s.specimen_id: k for k, s in enumerate(specs)}
        for r in rows:
            if r["scenario_id"] != scenario:
                continue
            k = index[r["specimen_id"]]
            j = flat.start[k] + int(r["tick_min"]) // evaluate.TICK_MIN - 1
            risk[r["item_id"]] = float(values[j])
        if note:
            notes[scenario] = note
    return risk, notes


def _brier(y, p, w=None):
    w = np.ones_like(y) if w is None else w
    return float(np.sum(w * (p - y) ** 2) / np.sum(w)) if np.sum(w) > 0 else None


def _ece(y, p, w=None):
    if w is None:
        return ne.expected_calibration_error(y, p)
    k = w.astype(np.int64)
    return ne.expected_calibration_error(np.repeat(y, k), np.repeat(p, k)) if k.sum() else None


X3_METRICS = {"auroc": ne.auroc, "auprc": ne.auprc, "brier": _brier, "ece": _ece}


def x3_scores(rows: Sequence[dict], labels: dict, tracernet: dict, analytic: dict, split: str, *,
              resamples: int = ne.BOOTSTRAPS, seed: int = ne.BOOTSTRAP_SEED, notes: dict | None = None) -> dict:
    kept = [r for r in rows if r.get("p_miss") is not None]
    c = Clusters([r["scenario_id"] for r in kept], resamples, seed)
    y = np.array([labels[r["item_id"]]["miss"] for r in kept], dtype=float)
    series = {"decision_model": np.array([r["p_miss"] for r in kept], dtype=float),
              "tracernet": np.array([tracernet[r["item_id"]] for r in kept], dtype=float),
              "analytic_local": np.array([analytic[r["item_id"]] for r in kept], dtype=float)}
    out = {"set": split, "ticks": len(rows), "scored_ticks": len(kept), "refused_or_unanswered": len(rows) - len(kept),
           "scenarios": len(c.names), "misses": int(y.sum()),
           "strata": _counts([r["stratum"] for r in kept]),
           "bootstrap": {"resamples": resamples, "seed": seed, "clusters": "scenarios"},
           "local_refit_notes": notes or {}, "methods": {}, "differences": {}}
    draws = {}
    for name, p in series.items():
        out["methods"][name] = {}
        for metric, fn in X3_METRICS.items():
            d = _boot_stat(c, fn, y, p)
            draws[(name, metric)] = d
            out["methods"][name][metric] = _ci(fn(y, p), d)
    for other in ("tracernet", "analytic_local"):
        out["differences"][f"decision_model - {other}"] = {}
        for metric in X3_METRICS:
            a, b = draws[("decision_model", metric)], draws[(other, metric)]
            n = min(len(a), len(b))
            diff = a[:n] - b[:n]
            pa, pb = out["methods"]["decision_model"][metric]["point"], out["methods"][other][metric]["point"]
            out["differences"][f"decision_model - {other}"][metric] = {
                **_ci(None if pa is None or pb is None else pa - pb, diff),
                "share_ahead": float(np.mean(diff > 0)) if n else None}
    return out


# --- X1 ---------------------------------------------------------------------------------------------------------------

def x1_scores(rows: Sequence[dict], *, resamples: int = ne.BOOTSTRAPS, seed: int = ne.BOOTSTRAP_SEED,
              noul_yes: float = 0.7) -> dict:
    """The controls' AUROC and the soft-filter verdict, the review list, and agreement with the planner's tags."""
    controls = [r for r in rows if r["kind"] == "control" and r.get("plausibility")]
    labels = [r for r in rows if r["kind"] == "label"]
    y = np.array([r["corruption"] != "clean" for r in controls], dtype=float)
    s = np.array([-r["plausibility"]["score"] for r in controls], dtype=float)   # lower plausibility, more suspicious
    c = Clusters([r["item_id"] for r in controls], resamples, seed)
    auc = _ci(ne.auroc(y, s), _boot_stat(c, ne.auroc, y, s)) if len(controls) else _ci(None, np.array([]))
    works = auc["point"] is not None and auc["point"] >= SOFT_FILTER_AUROC
    review = []
    if works:
        for r in labels:
            pr = (r.get("plausibility") or {}).get("probabilities") or {}
            if pr and max(pr, key=lambda k: (pr[k], -int(k))) in ("0", "1"):
                review.append(r["scenario_id"])
    by_kind = {}
    for kind in sorted({r["corruption"] for r in controls}):
        sel = [r["plausibility"]["score"] for r in controls if r["corruption"] == kind]
        by_kind[kind] = {"n": len(sel), "mean_score": float(np.mean(sel))}
    focus = [r for r in labels if r.get("dominant_failure") and r["planner"].get("failure_focus")]
    agree = [r["dominant_failure"]["choice"] == r["planner"]["failure_focus"] for r in focus]
    confusion: dict = {}
    for r in focus:
        row = confusion.setdefault(r["planner"]["failure_focus"], {})
        row[r["dominant_failure"]["choice"]] = row.get(r["dominant_failure"]["choice"], 0) + 1
    edges: dict = {}
    for r in labels:
        for tag, p in (r.get("edges") or {}).items():
            e = edges.setdefault(tag, {"asked": 0, "yes": 0})
            e["asked"] += 1
            e["yes"] += int(p >= noul_yes)
    asked = sum(e["asked"] for e in edges.values())
    return {"labels": len(labels), "labels_answered": sum(1 for r in labels if r.get("plausibility")),
            "controls": len(controls), "control_auroc": auc, "soft_filter": {
                "threshold": SOFT_FILTER_AUROC, "works": works, "review_list": sorted(review),
                "note": "flagged scenarios go to a review list; none is deleted"},
            "control_scores_by_corruption": by_kind,
            "plausibility_levels": _counts([max(r["plausibility"]["probabilities"],
                                                key=lambda k: (r["plausibility"]["probabilities"][k], -int(k)))
                                            for r in labels if r.get("plausibility")]),
            "failure_focus_agreement": {"n": len(agree), "share": float(np.mean(agree)) if agree else None,
                                        "confusion": confusion},
            "edge_tag_agreement": {"asked": asked, "share_yes": (sum(e["yes"] for e in edges.values()) / asked)
                                   if asked else None, "by_tag": dict(sorted(edges.items()))}}
