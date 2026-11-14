"""Export TracerNet's demo file, docs/demo/neural.json, beside the demo bundle (docs/neural/spec.md §10.5).

    python -m src.api.export_neural --source legacy_v1 [--artifact artifacts/tracernet/v1] [--out docs/demo/neural.json]
    python -m src.api.export_neural --source v2 --scenarios ID[,ID...] [--store data/store-v2]

`legacy_v1` covers the demo's held-out specimens (runs 24 to 29 of the v1 store, the bundle's), with every knob set on
fit runs 0 to 23 and TracerNet's fingerprint from them; `v2` covers the scored replicates of the chosen scenarios, with
every knob set on each scenario's history replicate. For each specimen with a clock the file holds:

    p_miss, p_never     TracerNet's, every 5 minutes while the specimen is silent, to the bundle's 240-minute horizon
    alerts              the alert minute at each of the 20 export.GRID_BUDGETS (alerts per shift), or null: TracerNet
                        and the local analytic model at both weightings (3-2-1 and equal), the window rule (unweighted)
    headline            TracerNet's alert at the headline budget (config/triage.yaml, 6 per shift) and 3-2-1 weights,
                        its factors (spec §9.3) and its frozen dispatch decision (null without one)

and, once for the file, `headline_knobs`: TracerNet's harm threshold at the headline budget and 3-2-1 weights in each
tuning group, the first tick where p_miss times the tier's weight reaches it being the headline alert.
    truth               the breach label and the planted fault (v2: also the outcome class and cause), read only through
                        src/pipeline/evaluate.py's accessors, as export.py reads them

Every knob comes from src/pipeline/neural_eval.py's `evaluate_group`, which tunes every method the same way from alert
volume alone (spec §8.3); on legacy_v1 the local analytic model is the committed results/interval_params.json, as in
the evaluation. TracerNet's headline alerts are explained with Predictor.explain against the same store. The rounding
of p_miss and p_never to ROUND places is for display: alerts are precomputed at full precision.

This module reads docs/demo/bundle.json never and writes it never; the bundle stays byte-identical. On v2, every run of
the chosen scenarios is read from the observed runs table and put through Predictor.check_lock before any specimen,
label or feature is read, so a locked split (test, test_ood) among them needs the test run its lock recorded
(`build(..., test_run=)`).
The file is synthetic data with no clinical review. Rebuilding the demo page on top of it is a separate task.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

from src.api import export
from src.generator import config
from src.models.tracernet import predict
from src.models.tracernet.predictor import DEFAULT_ARTIFACT, History, Predictor
from src.pipeline import evaluate, intervals, neural_eval as ne, store, triage

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "docs" / "demo" / "neural.json"
SCHEMA_PATH = ROOT / "docs" / "demo" / "neural.schema.json"
NEURAL_VERSION = 1
ROUND = 5
WEIGHTINGS = (("3-2-1", "default"), ("equal", "equal"))   # (the harness's name, Predictor's scheme)
METHODS = {"tracernet": "TracerNet ensemble, weighted by tier (harm threshold)",
           "analytic_local": "the analytic model refit on the scenario's own history (legacy_v1: the committed fit), "
                             "weighted by tier",
           "window_rule": "alert once a fixed share of the window has passed, unweighted"}


class _RiskSeries:
    """TracerNet's p_miss at every tick of each specimen, from a risk table, for neural_eval's RiskMethod."""

    def __init__(self, risk: pl.DataFrame):
        self.by_id = {sid: np.asarray(v, dtype=float) for sid, v in
                      risk.sort(["specimen_id", "tick_min"]).group_by("specimen_id", maintain_order=True)
                      .agg(pl.col("p_miss")).iter_rows()}

    def __call__(self, specs, context) -> dict[str, np.ndarray]:
        return {s.specimen_id: self.by_id.get(s.specimen_id, np.empty(0)) for s in specs}


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if path and Path(path).is_file() else None


def scenario_runs(store_dir: Path, scenarios) -> list[int]:
    """Every run (every replicate, the history included) of the chosen v2 scenarios, from the observed runs table
    alone, so the lock can be checked before any specimen or label is read. Refuses a scenario the store lacks."""
    con = store.open_observed_v2(Path(store_dir))
    try:
        rows = con.execute("select scenario_id, run from runs where list_contains(?, scenario_id) order by run",
                           [[str(s) for s in scenarios]]).fetchall()
    finally:
        con.close()
    missing = sorted(set(scenarios) - {s for s, _ in rows})
    if missing:
        raise ValueError(f"scenarios not in the store at {store_dir}: {missing}")
    return [r for _, r in rows]


def _groups(source: str, store_dir: Path, scenarios, env: ne.Env, start: str, stop: str) -> tuple[list, set]:
    """The tuning groups (neural_eval.Group) and the analytic leaf groups their specimens fall in."""
    if source == "legacy_v1":
        history, _ = evaluate.load_specs(store.open_observed(store_dir), None, start, stop, "fit")
        scored, _ = evaluate.load_test_specs(start, stop)
        group = ne._group("fit runs 0 to 23 for runs 24 to 29", history, scored, True,
                          {"set": "legacy_v1", "store_dir": str(store_dir), "scenario_id": None})
        return [group], {s.group for s in history} | {s.group for s in scored}
    groups, _ = ne._v2_groups(env, list(scenarios))
    missing = sorted(set(scenarios) - {g.key for g in groups})
    if missing:
        raise ValueError(f"no scored specimens with a clock for scenarios {missing}")
    return groups, ne._leaf_groups(store_dir, start, stop, None, list(scenarios))


def build(pred: Predictor, source: str, *, scenarios=None, store_dir: Path | None = None, test_run=None,
          dispatch: Path | None = None, workers: int = 1) -> dict:
    """The contents of docs/demo/neural.json for `source` ("legacy_v1", or "v2" with `scenarios`)."""
    loaded = config.load()
    if pred.policy_hash != loaded["policy_hash"]:
        raise RuntimeError(f"the model was trained under policy {pred.policy_hash}, the policy is now "
                           f"{loaded['policy_hash']}; retrain it before exporting")
    if source not in ("legacy_v1", "v2"):
        raise ValueError(f"source must be legacy_v1 or v2, not {source!r}")
    if source == "v2" and not scenarios:
        raise ValueError("--source v2 needs --scenarios")
    legacy = source == "legacy_v1"
    store_dir = Path(store_dir) if store_dir is not None else (store.STORE_DIR if legacy else store.STORE_V2_DIR)
    if legacy and store_dir.resolve() != store.STORE_DIR.resolve():
        raise ValueError("legacy_v1 is the v1 store (store.STORE_DIR), whose labels evaluate.load_test_specs reads")
    start, stop = intervals.clock_events(loaded["policy"])
    cfg = triage.load()
    schemes = triage.weight_schemes(cfg)
    headline_budget = cfg["operating_point"]["budget_per_shift"]
    if headline_budget not in export.GRID_BUDGETS:
        raise RuntimeError(f"the headline budget of {headline_budget} alerts per shift is not on the grid")
    budgets = tuple(ne.Budget(f"{b}/shift", float(b), "shift") for b in export.GRID_BUDGETS)
    head = budgets[export.GRID_BUDGETS.index(headline_budget)].key
    env = ne.Env(set_name=source, kind="legacy" if legacy else "v2", store_split="test" if legacy else None,
                 store_dir=str(store_dir), by_run=legacy, schemes=tuple((n, schemes[s]) for n, s in WEIGHTINGS),
                 budgets=budgets, headline=head, slice_dimensions=(), matched=False, keep_alerts=True)
    if not legacy:      # the lock before any read: a test or test_ood scenario needs the run its lock recorded
        pred.check_lock(store_dir, scenario_runs(store_dir, scenarios), [], History(), test_run)
    groups, leaves = _groups(source, store_dir, scenarios, env, start, stop)

    # TracerNet's risk at every tick of the history and scored specimens
    runs = sorted({s.run for g in groups for s in (*g.history, *g.scored)})
    risk = pred.score(store_dir, runs, workers=workers, test_run=test_run)
    setup = ne.Setup(env.kind, source, store_dir, store_dir, start, stop, loaded["policy_hash"], leaves)
    methods = [ne.RiskMethod("tracernet", _RiskSeries(risk), monotone=False),
               ne.AnalyticModel("analytic_local", "local"), ne.WindowRule()]
    for m in methods:
        m.setup(setup)
    results = [ne.evaluate_group(g, methods, env) for g in groups]

    # the headline alerts at the harness's knob, explained
    parts = []
    for g, res in zip(groups, results):
        knob = res["knobs"][head]["tracernet|3-2-1"][0]
        rows = risk.filter(pl.col("specimen_id").is_in([s.specimen_id for s in g.scored]))
        part = pred.alerts(rows, budget_per_shift=float(headline_budget), weights="default", knob=knob)
        harness = {sid: m for sid, m in res["alerts"][head]["tracernet|3-2-1"].items() if m is not None}
        if dict(zip(part["specimen_id"].to_list(), part["tick_min"].cast(pl.Float64).to_list())) != harness:
            raise RuntimeError(f"TracerNet's headline alerts in {g.key} differ between the harness and the predictor")
        parts.append(part)
    alerts = pred.explain(pl.concat(parts), store_dir=store_dir, test_run=test_run, workers=workers)
    decisions = predict.load_dispatch(dispatch) if dispatch is not None else {}
    headline = {r["specimen_id"]: {"alert_min": int(r["tick_min"]), "factors": r["factors"],
                                   "dispatch": decisions.get(r["alert_id"])} for r in alerts.to_dicts()}

    # one record per scored specimen
    series = {sid: (pm, pn) for sid, pm, pn in risk.filter(pl.col("tick_min") <= export.HORIZON_MIN)
              .sort(["specimen_id", "tick_min"]).group_by("specimen_id", maintain_order=True)
              .agg(pl.col("p_miss"), pl.col("p_never")).iter_rows()}
    records = []
    for g, res in zip(groups, results):
        def ticks_of(key, sid):
            return [export.whole(res["alerts"][b.key][key][sid]) for b in budgets]
        for s in g.scored:
            sid = s.specimen_id
            pm, pn = series.get(sid, ([], []))
            truth = {"breach": bool(s.breach), "fault": s.fault}
            if not legacy:
                truth |= {"outcome": s.outcome_class, "cause": s.cause}
            records.append({
                "id": sid, "run": s.run, "scenario_id": None if legacy else s.scenario_id, "type": s.kind,
                "tier": s.tier, "route": s.route, "window_min": s.window, "received_min": s.receipt_min,
                "p_miss": [round(float(x), ROUND) for x in pm], "p_never": [round(float(x), ROUND) for x in pn],
                "alerts": {"tracernet": {w: ticks_of(f"tracernet|{w}", sid) for w, _ in WEIGHTINGS},
                           "analytic_local": {w: ticks_of(f"analytic_local|{w}", sid) for w, _ in WEIGHTINGS},
                           "window_rule": ticks_of("window_rule|none", sid)},
                "headline": headline.get(sid, {"alert_min": None, "factors": [], "dispatch": None}),
                "truth": truth})
    notes = [n for res in results for n in res["notes"]]
    manifest = json.loads(store.MANIFEST_PATH.read_text(encoding="utf-8")) if legacy else None
    data_notice = export.NOTICE if legacy else ne.NOTICE
    return {
        "neural_version": NEURAL_VERSION,
        "notice": f"{data_notice} {pred.manifest.get('notice', '')}".strip(),
        "source": source, "scenarios": None if legacy else sorted(scenarios),
        "model": {"version": pred.version, "policy_hash": pred.policy_hash, "rung": pred.rung,
                  "members": len(pred.members), "feature_schema_version": pred.schema["version"],
                  "manifest_sha256": _sha256(pred.artifact_dir / "manifest.json") if pred.artifact_dir else None},
        "store": {"path": export_path(store_dir),
                  "sha256": manifest["sha256"] if legacy else ne._manifest_hash(store_dir)},
        "tick_min": evaluate.TICK_MIN, "horizon_min": export.HORIZON_MIN,
        "grid_budgets": list(export.GRID_BUDGETS), "headline_index": export.GRID_BUDGETS.index(headline_budget),
        "weightings": [w for w, _ in WEIGHTINGS], "methods": METHODS,
        "knob_notes": [f"{n['method']} in {n['group']}: {n['note']}" for n in notes],
        "headline_knobs": {str(g.key): float(res["knobs"][head]["tracernet|3-2-1"][0]) for g, res in zip(groups, results)},
        "specimens": records,
    }


def export_path(path: Path) -> str:
    path = Path(path).resolve()
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export TracerNet's demo file, docs/demo/neural.json.")
    ap.add_argument("--source", choices=("legacy_v1", "v2"), required=True)
    ap.add_argument("--scenarios", default=None, help="comma-separated scenario ids (v2)")
    ap.add_argument("--store", type=Path, default=None)
    ap.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    ap.add_argument("--dispatch", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args(argv)
    scenarios = [s for s in (args.scenarios or "").split(",") if s] or None
    try:
        doc = build(Predictor.load(args.artifact), args.source, scenarios=scenarios, store_dir=args.store,
                    dispatch=args.dispatch, workers=args.workers)
    except (RuntimeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, separators=(",", ":"), allow_nan=False) + "\n", encoding="utf-8")
    alerted = sum(s["headline"]["alert_min"] is not None for s in doc["specimens"])
    print(f"wrote {export_path(args.out)}: {len(doc['specimens'])} specimens, {alerted} headline alerts, "
          f"{args.out.stat().st_size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
