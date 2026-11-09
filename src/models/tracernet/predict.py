"""TracerNet's command line (docs/neural/spec.md §10.2, §10.3).

    python -m src.models.tracernet.predict --artifact artifacts/tracernet/v1 --store data/store-v2 \\
      --split val --budget 6 --weights default --explain --out results/neural/predictions/val

Scores the split's scored replicates with the artifact (on the v1 store, the split's runs: `test` is runs 24 to 29),
sets each scenario's knob on its history from alert volume alone (or takes --knob), raises the alerts, explains them
with --explain, merges frozen dispatch decisions when --dispatch names them (dispatch is null otherwise), and writes:

    predictions.parquet          one row per specimen and tick    docs/neural/schemas/prediction.schema.json
    alerts.parquet, alerts.json  one record per alert             docs/neural/schemas/alert.schema.json

Probabilities that float32 rounding took a hair past [0, 1] are clipped into it for the records (`bounded`). Before
anything is written, every alert record is validated with its schema, and so is a seeded sample of SAMPLE_ROWS
prediction rows, while every prediction row is checked column by column against the schema's own types, bounds, enums
and patterns, plus p_late + p_never = p_miss. Scoring and explaining go through Predictor alone, so
test and test_ood are refused unless `run(..., test_run=...)` is given the run the test lock recorded; the command
line has no way to pass one.

--dispatch is a JSON file, or a directory of them, holding either a mapping from alert_id to a dispatch object or a
list of {"alert_id": ..., "dispatch": {...}} records (spec §11's frozen decisions). Alerts without one keep null.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import jsonschema
import numpy as np
import polars as pl

from src.models.tracernet.predictor import DEFAULT_ARTIFACT, DISPATCH, PROBABILITY_SLACK, Predictor, weight_schemes
from src.pipeline import store

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = ROOT / "docs" / "neural" / "schemas"
PREDICTION_SCHEMA = SCHEMA_DIR / "prediction.schema.json"
ALERT_SCHEMA = SCHEMA_DIR / "alert.schema.json"
SAMPLE_ROWS = 2000
SUM_TOLERANCE = 1e-9


def schema(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def split_runs(store_dir: Path, split: str) -> tuple[list[int], list[int]]:
    """(scored runs, history runs) of a split: v2 scored and history replicates; on the v1 store the split's runs and
    the fit runs 0 to 23."""
    con = store.open_observed(Path(store_dir))
    try:
        tables = {r[0] for r in con.execute("show tables").fetchall()}
        if "runs" in tables:
            rows = con.execute("select run, role from runs where split = ? order by run", [split]).fetchall()
            scored = [r for r, role in rows if role == "scored"]
            history = [r for r, role in rows if role == "history"]
        else:
            scored = [r for (r,) in con.execute("select distinct run from specimens where split = ? order by run",
                                                [split]).fetchall()]
            history = [r for (r,) in con.execute("select distinct run from specimens where split = 'fit' order by run")
                       .fetchall()]
    finally:
        con.close()
    if not scored:
        raise RuntimeError(f"the store at {store_dir} has no scored runs in split {split!r}")
    return scored, history


# --- Dispatch -------------------------------------------------------------------------------------------------------

def load_dispatch(path: Path) -> dict[str, dict]:
    """alert_id -> dispatch object from a JSON file or every JSON file of a directory."""
    path = Path(path)
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    if not files or not all(f.is_file() for f in files):
        raise FileNotFoundError(f"no dispatch decisions at {path}")
    out: dict[str, dict] = {}
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        items = doc.items() if isinstance(doc, dict) else ((r["alert_id"], r["dispatch"]) for r in doc)
        for alert_id, decision in items:
            if alert_id in out and out[alert_id] != decision:
                raise ValueError(f"two different dispatch decisions for {alert_id}")
            out[alert_id] = decision
    return out


def merge_dispatch(alerts: pl.DataFrame, decisions: dict[str, dict]) -> pl.DataFrame:
    fields = list(DISPATCH.to_schema())
    values = [None if (d := decisions.get(a)) is None else {k: d.get(k) for k in fields}
              for a in alerts["alert_id"].to_list()]
    return alerts.with_columns(dispatch=pl.Series(values, dtype=DISPATCH))


# --- Validation -----------------------------------------------------------------------------------------------------

_TYPES = {"integer": (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64),
          "number": (pl.Float32, pl.Float64, pl.Int8, pl.Int16, pl.Int32, pl.Int64), "string": (pl.Utf8,)}


def column_problems(df: pl.DataFrame, doc: dict) -> list[str]:
    """Every row of a frame against a flat object schema, column by column: required columns and no others, types
    (null only where the schema allows it), minimum, maximum, enum and pattern."""
    props = doc["properties"]
    problems = []
    missing, extra = set(doc["required"]) - set(df.columns), set(df.columns) - set(props)
    if missing or extra:
        return [f"columns missing {sorted(missing)}, not in the schema {sorted(extra)}"]
    for name, rule in props.items():
        col = df[name]
        kinds = rule.get("type", [])
        kinds = [kinds] if isinstance(kinds, str) else list(kinds)
        if col.null_count() and "null" not in kinds:
            problems.append(f"{name}: {col.null_count()} nulls")
        vals = col.drop_nulls()
        if not len(vals):
            continue
        typed = [k for k in kinds if k != "null"]
        if typed and not any(col.dtype in _TYPES.get(k, ()) for k in typed):
            problems.append(f"{name}: dtype {col.dtype} is not {typed}")
            continue
        if "minimum" in rule and vals.min() < rule["minimum"]:
            problems.append(f"{name}: {vals.min()} below {rule['minimum']}")
        if "maximum" in rule and vals.max() > rule["maximum"]:
            problems.append(f"{name}: {vals.max()} above {rule['maximum']}")
        if col.dtype in (pl.Float32, pl.Float64) and not vals.is_finite().all():
            problems.append(f"{name}: a value that is not finite")
        if "enum" in rule and not vals.is_in(rule["enum"]).all():
            problems.append(f"{name}: values outside {rule['enum']}")
        if "pattern" in rule and not vals.str.contains(rule["pattern"]).all():
            problems.append(f"{name}: values not matching {rule['pattern']}")
    return problems


def validate_predictions(risk: pl.DataFrame, doc: dict | None = None, sample: int = SAMPLE_ROWS) -> None:
    doc = doc or schema(PREDICTION_SCHEMA)
    problems = column_problems(risk, doc)
    if not problems:
        gap = (risk["p_late"] + risk["p_never"] - risk["p_miss"]).abs().max()
        if gap is not None and gap > SUM_TOLERANCE:
            problems.append(f"p_late + p_never differs from p_miss by up to {gap}")
    if problems:
        raise ValueError("predictions fail their schema: " + "; ".join(problems))
    rows = risk if risk.height <= sample else risk.sample(n=sample, seed=0)
    validator = jsonschema.Draft202012Validator(doc)
    for row in rows.to_dicts():
        validator.validate(row)


def bounded(risk: pl.DataFrame) -> pl.DataFrame:
    """The risk table as records: float32 rounding takes a probability about 1e-7 past [0, 1], which Predictor.alerts
    tolerates up to PROBABILITY_SLACK; here such values are clipped into [0, 1] (p_late becoming p_miss - p_never, and
    the harms following p_miss) and anything further out is refused. The scores themselves are left as they are."""
    p = risk["p_miss"].to_numpy()
    lo, hi = p.min(initial=0.0), p.max(initial=0.0)
    if not np.all(np.isfinite(p)) or lo < -PROBABILITY_SLACK or hi > 1 + PROBABILITY_SLACK:
        raise ValueError("p_miss holds a value that is not a probability")
    w = weight_schemes()["default"]
    miss = pl.col("p_miss").clip(0.0, 1.0)
    never = pl.min_horizontal(pl.col("p_never").clip(0.0, 1.0), miss)
    return risk.with_columns(p_miss=miss, p_never=never, p_late=miss - never, p_on_time=1.0 - miss,
                             harm_default=miss * pl.col("tier").replace_strict(w, return_dtype=pl.Float64),
                             harm_equal=miss)


def validate_alerts(records: list[dict], doc: dict | None = None) -> None:
    validator = jsonschema.Draft202012Validator(doc or schema(ALERT_SCHEMA))
    for rec in records:
        validator.validate(rec)


# --- The command ------------------------------------------------------------------------------------------------------

def run(artifact: Path, store_dir: Path, split: str, budget: float, weights: str = "default", *,
        knob: float | None = None, explain: bool = False, out: Path | None = None, dispatch: Path | None = None,
        test_run=None, workers: int | None = None, predictor: Predictor | None = None) -> dict:
    """Score, alert, explain, merge dispatch, validate and (with `out`) write. Returns the frames and the records."""
    pred = predictor or Predictor.load(Path(artifact))
    scored, history = split_runs(store_dir, split)
    risk = bounded(pred.score(store_dir, scored, workers=workers, test_run=test_run))
    tune_on = None if knob is not None else bounded(pred.score(store_dir, history, workers=workers, test_run=test_run))
    alerts = pred.alerts(risk, budget_per_shift=budget, weights=weights, knob=knob, tune_on=tune_on)
    if explain:
        alerts = pred.explain(alerts, store_dir=store_dir, test_run=test_run, workers=workers or 1)
    if dispatch is not None:
        alerts = merge_dispatch(alerts, load_dispatch(dispatch))
    records = alerts.to_dicts()
    validate_predictions(risk)
    validate_alerts(records)
    if out is not None:
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        risk.write_parquet(out / "predictions.parquet")
        alerts.write_parquet(out / "alerts.parquet")
        (out / "alerts.json").write_text(json.dumps(records, indent=1, allow_nan=False) + "\n", encoding="utf-8")
    return {"predictions": risk, "alerts": alerts, "records": records}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Score a split with TracerNet and write its predictions and alerts.")
    ap.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    ap.add_argument("--store", type=Path, default=store.STORE_V2_DIR)
    ap.add_argument("--split", required=True)
    ap.add_argument("--budget", type=float, default=6.0, help="alerts per 8-hour shift")
    ap.add_argument("--weights", choices=("default", "equal", "steep"), default="default")
    ap.add_argument("--knob", type=float, default=None, help="a harm threshold instead of tuning on the history")
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("--dispatch", type=Path, default=None)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=None)
    args = ap.parse_args(argv)
    try:
        res = run(args.artifact, args.store, args.split, args.budget, args.weights, knob=args.knob,
                  explain=args.explain, out=args.out, dispatch=args.dispatch, workers=args.workers)
    except (RuntimeError, ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    n = res["alerts"].height
    print(f"wrote {args.out}: {res['predictions'].height} predictions, {n} alerts"
          f"{', explained' if args.explain else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
