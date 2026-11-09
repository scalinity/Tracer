"""Rung selection, the 3-seed ensemble, calibration and the artifact (docs/neural/spec.md §7.6, §7.9, §7.10, §10.4;
stage S13).

    python -m src.neural.ensemble select    [--store data/store-v2] [--run RUN_DIR ...]
    python -m src.neural.ensemble calibrate [--store data/store-v2] [--rung B] [--run RUN_DIR ...]

Selection (pre-registered, spec §7.9). Among rungs A, B and C (L is a reference and never chosen), the rung with the
lowest `val_sel`; when two are within 1% relative, the simpler, so the simplest rung within 1% of the best wins.
Then, if the chosen rung's validation median warning over all breaches at 6 alerts per shift is more than 5 minutes
below rung A's, rung A. The warning comes from `median_warning(predict_fn) -> float | None`; the command uses the
evaluation driver's validation-only check (src/pipeline/neural_eval.py `median_warning`), and `predict_fn(store_dir,
runs, *, history)` is a rung's best run as an uncalibrated predictor's `score`. A B or C choice is never recorded with
that check unrun: if it cannot run, selection fails. The record goes to results/neural/selection.json
(data/neural/selection-dev.json for any other store).

Ensemble (spec §7.10). Three seeds of the chosen rung with the searched configuration. Their distributions are
averaged; `ensemble_sd` is the spread of the members' p_miss.

Calibration (spec §7.6), fitted once on validation ticks with src/neural/calibrate.py: one temperature on the log of
the averaged distribution (every validation tick counts once), then an isotonic map of p_miss only if the expected
calibration error is still above 0.02. Above `calibration_max_ticks` validation ticks, a fixed uniform sample of
that many ticks (seed 0) is used and the manifest says so.

Artifact (spec §10.4) in artifacts/tracernet/v1/ (data/neural/artifact-dev/ for any other store): model.pt (the
members' weights), config.yaml (rung, network and training configuration, seeds, runs), feature_schema.json,
bins.json, calibration.json, manifest.json and MODEL_CARD.md; at most 10 MB. Nothing here reads test or test_ood.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import numpy as np
import polars as pl
import torch
import yaml

from src.models.tracernet import features as F
from src.models.tracernet.predictor import History, Predictor
from src.neural import calibrate, samplers as S, stages, train as T
from src.pipeline import store

ROOT = Path(__file__).resolve().parents[2]
LADDER = ("A", "B", "C")
SELECTION_PATH = ROOT / "results" / "neural" / "selection.json"
SELECTION_DEV_PATH = ROOT / "data" / "neural" / "selection-dev.json"
ARTIFACT_DIR = ROOT / "artifacts" / "tracernet" / "v1"
ARTIFACT_DEV_DIR = ROOT / "data" / "neural" / "artifact-dev"
FILES = ("model.pt", "config.yaml", "feature_schema.json", "bins.json", "calibration.json", "manifest.json",
         "MODEL_CARD.md")
NOTICE = ("Synthetic data: TracerNet was trained and checked only on simulated laboratory weeks, configured by "
          "language models and simulated by Tracer's own code, with no clinical review. Every parameter is an "
          "assumption, and its numbers describe the simulator, not any laboratory.")


# --- Selection -----------------------------------------------------------------------------------------------------

def choose_rung(val_sel: dict, tolerance: float = 0.01, ladder=LADDER) -> str:
    """The rung with the lowest val_sel; when two are within `tolerance` relative, the simpler. The simplest rung
    within the tolerance of the best is chosen, so a chain of near-ties resolves to its simplest rung."""
    rungs = [r for r in ladder if val_sel.get(r) is not None]
    if not rungs:
        raise RuntimeError(f"no rung of {ladder} has a val_sel")
    best = min(val_sel[r] for r in rungs)
    return next(r for r in rungs if val_sel[r] <= best * (1 + tolerance))


def apply_warning_check(chosen: str, warning_of: Callable[[str], float | None], *, budget: float,
                        margin: float) -> dict:
    """Spec §7.9's second step: rung A replaces the chosen rung if the chosen rung's median warning is more than
    `margin` minutes below rung A's. `warning_of(rung)` returns None when no warning can be computed."""
    if chosen == "A":
        return {"chosen": "A", "warning": {"status": "not needed", "budget_per_shift": budget}}
    w_chosen, w_a = warning_of(chosen), warning_of("A")
    if w_chosen is None or w_a is None:
        missing = [r for r, w in ((chosen, w_chosen), ("A", w_a)) if w is None]
        return {"chosen": chosen, "warning": {"status": "not run", "budget_per_shift": budget,
                                              "reason": f"no median warning for rung {' or '.join(missing)}"}}
    below = w_a - w_chosen
    return {"chosen": "A" if below > margin else chosen,
            "warning": {"status": "run", "budget_per_shift": budget, "margin_min": margin, chosen: w_chosen, "A": w_a,
                        "below_a_min": below, "switched_to_a": below > margin}}


def predict_fn_of(run_dir: Path) -> Callable:
    """A run's best epoch as an uncalibrated one-member predictor's `score`."""
    model, _ = T.load_member(run_dir)
    schema = F.load_schema(Path(run_dir) / "feature_schema.json")
    pred = Predictor([model], schema, None, version=f"run:{Path(run_dir).name}")

    def predict(store_dir: Path, runs, *, history: History | None = None, workers: int | None = None):
        return pred.score(store_dir, runs, history=history, workers=workers)
    return predict


def select(config: dict, runs: dict, *, out_path: Path, median_warning: Callable, log: Callable = print) -> dict:
    """Apply the selection rule to one run per rung ({rung: run_dir}) and write the record. When the lowest
    val_sel picks rung B or C, the warning check against rung A must run; if it cannot, nothing is written and
    selection fails."""
    ecfg = config["ensemble"]
    results = {r: T.load_result(d) for r, d in runs.items()}
    val_sel = {r: res["best_val_sel"] for r, res in results.items()}
    ladder = tuple(ecfg["ladder"])
    first = choose_rung(val_sel, float(ecfg["tolerance"]), ladder)
    fns: dict = {}

    def warning_of(rung: str):
        if rung not in runs:
            return None
        fns.setdefault(rung, predict_fn_of(runs[rung]))
        return median_warning(fns[rung])

    check = apply_warning_check(first, warning_of, budget=float(ecfg["warning_budget_per_shift"]),
                                margin=float(ecfg["warning_margin_min"]))
    if first != "A" and check["warning"]["status"] != "run":
        raise RuntimeError(f"rung {first} has the lowest val_sel, but the warning check against rung A did not run "
                           f"({check['warning'].get('reason')}); spec §7.9 needs it before {first} can be chosen")
    hashes = {res["store"].get("store_hash") for res in results.values()}
    record = {"rule": "lowest val_sel among the ladder; within the tolerance (relative) the simpler rung; then rung A "
                      "if the chosen rung's validation median warning over all breaches at the budget is more than "
                      "the margin below rung A's (spec §7.9)",
              "ladder": list(ladder), "tolerance": float(ecfg["tolerance"]),
              "rungs": {r: {"run_id": res["run_id"], "run_dir": str(runs[r]), "val_sel": res["best_val_sel"],
                            "best_epoch": res["best_epoch"], "epochs": res["epochs"], "status": res["status"],
                            "seed": res["seed"], "device": res["device"]} for r, res in sorted(results.items())},
              "by_val_sel": first, "chosen": check["chosen"], "warning_check": check["warning"],
              "store_hash": sorted(h for h in hashes if h), "created": stages.now_iso(), "git_sha": stages.git_sha(ROOT)}
    if len(hashes) > 1:
        raise RuntimeError(f"the runs were trained on different stores: {sorted(hashes)}")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    log(f"selection: {check['chosen']} (lowest val_sel within {ecfg['tolerance']:.0%}: {first}; warning check "
        f"{check['warning']['status']}) -> {out_path}")
    return record


# --- Ensemble and calibration --------------------------------------------------------------------------------------

def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_members(run_dirs: list[Path]) -> tuple[list, dict, list[dict], list[dict]]:
    """The members' best networks (CPU, eval), the shared feature schema, the runs' results and configs."""
    members, results, configs, schemas = [], [], [], set()
    schema = None
    for d in run_dirs:
        model, cfg = T.load_member(d)
        members.append(model)
        configs.append(cfg)
        results.append(T.load_result(d))
        schema = F.load_schema(Path(d) / "feature_schema.json")
        schemas.add(stages.obj_hash(schema))
    if len(schemas) != 1:
        raise RuntimeError("the members were trained on different feature schemas")
    if len({m.rung for m in members}) != 1:
        raise RuntimeError("the members are not one rung")
    if len({r["seed"] for r in results}) != len(results):
        raise RuntimeError("two members share a seed")
    return members, schema, results, configs


def validation_logits(pred: Predictor, t: F.Table, rows: np.ndarray, ticks: np.ndarray, *, workers: int,
                      chunk: int = 16384, cache_root: Path = S.CACHE_ROOT) -> dict:
    """The log of the averaged distribution at every given tick, with targets, windows, ticks and specimens."""
    keys = pred.keys + ("target_bin", "breach", "specimen_index")
    parts: dict[str, list] = {k: [] for k in ("logits", "target", "window", "tick", "breach", "specimen")}
    with S.Workers(t, pred.schema, workers, cache_root) as pool:
        for c in T.tick_chunks(t, pool, rows, ticks, chunk, max(4, 2 * workers), keys):
            parts["logits"].append(pred.mean_logits(c).cpu())
            parts["target"].append(torch.from_numpy(c["target_bin"]))
            parts["window"].append(torch.from_numpy(c["window"]))
            parts["tick"].append(torch.from_numpy(c["tick"]))
            parts["breach"].append(c["breach"])
            parts["specimen"].append(c["specimen_index"])
    return {"logits": torch.cat(parts["logits"]), "target": torch.cat(parts["target"]),
            "window": torch.cat(parts["window"]), "tick": torch.cat(parts["tick"]),
            "breach": np.concatenate(parts["breach"]), "specimen": np.concatenate(parts["specimen"])}


def miss_log_loss_of(logits: torch.Tensor, window: torch.Tensor, breach, specimen, bins, temperature: float = 1.0):
    z = bins.logit_miss(logits / temperature, window).double().numpy()
    return T.miss_log_loss(z, breach, specimen)


def build(config: dict, run_dirs: list[Path], *, store_dir: Path, out_dir: Path, selection: dict | None = None,
          table: F.Table | None = None, workers: int | None = None, cache_root: Path = S.CACHE_ROOT,
          device: str = "auto", log: Callable = print) -> dict:
    """Average the members, fit the calibration on validation, and write the artifact. Returns the manifest.
    The validation logits are computed on `device` (auto: MPS or CUDA when available, which is far faster for
    rungs B and C than CPU); the artifact itself is a CPU artifact."""
    ecfg = config["ensemble"]
    store_dir, out_dir = Path(store_dir), Path(out_dir)
    members, schema, results, configs = load_members([Path(d) for d in run_dirs])
    rung = members[0].rung
    info = T.store_info(store_dir)
    stores = {r["store"].get("store_hash") for r in results}
    if stores != {info["store_hash"]}:
        raise RuntimeError(f"the members were trained on store {sorted(stores)}, not {info['store_hash']}")
    t = table if table is not None else T.load_train_table(store_dir)
    workers = workers or T.default_workers("cpu")
    if device == "auto":
        device = next((d for d in T.available_devices() if d != "cpu"), "cpu")
    pred = Predictor(members, schema, None, version=ecfg["version"], device=device)
    bins = pred.bins

    rows, ticks = S.validation_ticks(t, "val")
    n_all = len(ticks)
    cap = int(ecfg["calibration_max_ticks"])
    if n_all > cap:
        keep = np.sort(np.random.default_rng(0).choice(n_all, cap, replace=False))
        rows, ticks = rows[keep], ticks[keep]
    log(f"calibration: {len(ticks):,} of {n_all:,} validation ticks")
    v = validation_logits(pred, t, rows, ticks, workers=workers, cache_root=cache_root)
    cal, report = calibrate.fit(v["logits"], v["target"], v["window"], v["tick"], bins)
    cal_doc = cal.to_json()

    s_rows, s_ticks = S.validation_ticks(t, "val", subset="val_sel")
    vs = validation_logits(pred, t, s_rows, s_ticks, workers=workers, cache_root=cache_root)
    ens_val_sel = miss_log_loss_of(vs["logits"], vs["window"], vs["breach"], vs["specimen"], bins)
    ens_val_sel_cal = miss_log_loss_of(vs["logits"], vs["window"], vs["breach"], vs["specimen"], bins,
                                       cal.temperature)
    metrics = {"members": [{"run_id": r["run_id"], "seed": r["seed"], "val_sel": r["best_val_sel"],
                            "best_epoch": r["best_epoch"], "device": r["device"]} for r in results],
               "ensemble_val_sel": ens_val_sel, "ensemble_val_sel_after_temperature": ens_val_sel_cal,
               "calibration_ticks": len(ticks), "validation_ticks": n_all,
               "ece_before": report["before"]["ece"], "ece_after_temperature": report["ece_after_temperature"],
               "ece_after": report["after"]["ece"], "temperature": report["temperature"],
               "isotonic": report["isotonic"], "nll_before": report["nll_before"], "nll_after": report["nll_after"],
               "brier_before": report["before"]["brier"], "brier_after": report["after"]["brier"],
               "slope_before": report["before"]["slope"], "intercept_before": report["before"]["intercept"],
               "slope_after": report["after"]["slope"], "intercept_after": report["after"]["intercept"],
               "reliability_before": report["before"]["reliability"],
               "reliability_after": report["after"]["reliability"]}
    log(f"ensemble of {len(members)} rung-{rung} members: val_sel {ens_val_sel:.5f} (members "
        + ", ".join(f"{m['val_sel']:.5f}" for m in metrics["members"]) + f"); ECE {metrics['ece_before']:.4f} before, "
        f"{metrics['ece_after']:.4f} after (temperature {cal.temperature:.3f}, isotonic {report['isotonic']})")

    out_dir.mkdir(parents=True, exist_ok=True)
    torch.save({"members": [{k: x.detach().cpu() for k, x in m.state_dict().items()} for m in pred.members]},
               out_dir / "model.pt")
    net = asdict(pred.members[0].config)
    (out_dir / "config.yaml").write_text(yaml.safe_dump({
        "version": ecfg["version"], "rung": rung, "seeds": [r["seed"] for r in results],
        "runs": [r["run_id"] for r in results], "net": net, "training": configs[0]["training"],
        "training_config_hashes": sorted({r["config_hash"] for r in results}),
        "selection": None if selection is None else {"chosen": selection["chosen"],
                                                     "by_val_sel": selection["by_val_sel"]}}, sort_keys=False),
        encoding="utf-8")
    F.save_schema(schema, out_dir / "feature_schema.json")
    (out_dir / "bins.json").write_text(json.dumps(bins.to_json(), indent=1) + "\n", encoding="utf-8")
    (out_dir / "calibration.json").write_text(json.dumps(cal_doc, indent=2) + "\n", encoding="utf-8")
    env = T.environment("cpu")
    manifest = {
        "version": ecfg["version"], "created": stages.now_iso(), "git_sha": env["git_sha"],
        "policy_hash": info.get("policy_hash"), "library_sha256": info.get("library_sha256"),
        "splits_sha256": info.get("splits_sha256"), "sim_manifest_sha256": info.get("sim_manifest_sha256"),
        "store_hash": info.get("store_hash"), "store_dir": str(store_dir), "development": T.is_dev_store(store_dir),
        "feature_schema_version": schema["version"], "feature_schema_sha256": _sha(out_dir / "feature_schema.json"),
        "rung": rung, "seeds": [r["seed"] for r in results], "runs": [r["run_id"] for r in results],
        "versions": {"python": env["python"], "torch": env["torch"], "numpy": env["numpy"], "polars": pl.__version__},
        "freeze_hash": env["freeze_hash"], "training_devices": sorted({r["device"] for r in results}),
        "inference_device": "cpu", "calibration_device": device, "training_time_s": round(sum(r["wall_s"] for r in results), 1),
        "training_samples": int(sum(r["samples"] for r in results)), "validation": metrics, "notice": NOTICE,
        "files": {}}
    (out_dir / "MODEL_CARD.md").write_text(model_card(manifest, selection), encoding="utf-8")
    manifest["files"] = {name: _sha(out_dir / name) for name in FILES if name != "manifest.json"}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    size = sum((out_dir / name).stat().st_size for name in FILES)
    if size > float(ecfg["max_artifact_mb"]) * 1e6:
        raise RuntimeError(f"the artifact is {size / 1e6:.1f} MB, over the {ecfg['max_artifact_mb']} MB limit")
    log(f"artifact {out_dir}: {size / 1e6:.2f} MB")
    return manifest


def _f(x, digits: int = 4) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def model_card(m: dict, selection: dict | None) -> str:
    v = m["validation"]
    dev = ("\n**Development artifact.** Built on the development store (variants of one worked example), only to "
           "exercise the pipeline. Its numbers are a smoke check, not a result.\n" if m["development"] else "")
    chosen = "" if selection is None else (f" Rung {selection['chosen']} was chosen by the pre-registered rule "
                                           f"(spec §7.9); see `selection.json`.")
    return f"""# TracerNet {m['version']}

{m['notice']}
{dev}
## Intended use

A research prototype for a course project (UF CAI 5724). Every 5 minutes, for every clinical-microbiology specimen
collected but not yet scanned as received, it gives the probability that the receipt scan comes after the
specimen's stability window (`p_miss`), split into late and never (`p_late`, `p_never`), with arrival quantiles and
the spread across its members. It is a challenger to the analytic survival model under the rule of spec §8.6, not a
clinical tool, and must not be used to make decisions about real specimens.

## Model

Rung {m['rung']} of the TracerNet ladder (spec §7.3), {len(m['seeds'])} seeds ({', '.join(str(s) for s in m['seeds'])}),
their receipt-time distributions averaged.{chosen} Calibration: temperature {_f(v['temperature'], 3)} on the log of
the averaged distribution{', then an isotonic map of p_miss' if v['isotonic'] else ''}, fitted on validation ticks.
Inference runs on CPU and is bit-reproducible.

## Data

Observed scans of the scenario store `{m['store_hash']}` (library `{str(m['library_sha256'])[:16]}`, splits
`{str(m['splits_sha256'])[:16]}`, policy `{m['policy_hash']}`): train for fitting, validation for early stopping,
selection and calibration. Test and test_ood were never read. Features: `{m['feature_schema_version']}`.

## Validation metrics

| Metric | Value |
|---|---|
| val_sel (miss log loss, ticks up to min(window, 240), per specimen) | {_f(v['ensemble_val_sel'], 5)} |
| Members' val_sel | {', '.join(_f(x['val_sel'], 5) for x in v['members'])} |
| Expected calibration error of p_miss, before | {_f(v['ece_before'])} |
| Expected calibration error of p_miss, after | {_f(v['ece_after'])} |
| Brier at 30 and 60 min, after | {_f(v['brier_after'].get('30'))}, {_f(v['brier_after'].get('60'))} |
| Calibration slope and intercept, after | {_f(v['slope_after'], 3)}, {_f(v['intercept_after'], 3)} |
| Calibration ticks | {v['calibration_ticks']:,} of {v['validation_ticks']:,} |

## Limits

- The data is simulated; nothing here has been checked against a real laboratory, and no clinician reviewed it.
- Scenario families in test and test_ood differ from train; validation measures generalization to its own families
  only.
- The network explains its inputs, not the world; its attributions (spec §9) say what moved its output.
- A policy change (windows, clock) needs retraining: the predictor refuses another policy hash.
"""


# --- Discovery of runs ---------------------------------------------------------------------------------------------

def latest_runs(runs_csv: Path, store_hash: str, *, rungs, seeds=None, runs_dir: Path = T.RUNS_DIR) -> dict:
    """{(rung, seed): run_dir} of the latest finished run per rung and seed on a store (search trials excluded)."""
    out = {}
    for row in T.read_runs(runs_csv):
        if row["store_hash"] != store_hash or row["run_id"].startswith("hpo-") or row["rung"] not in rungs:
            continue
        if row["status"] not in ("finished", "stopped_early", "timed_out") or not row["best_val_sel"]:
            continue
        if seeds is not None and int(row["seed"]) not in seeds:
            continue
        d = Path(runs_dir) / row["run_id"]
        if (d / "best.pt").exists():
            out[(row["rung"], int(row["seed"]))] = d
    return out


def _paths(store_dir: Path, selection_path, out_dir):
    dev = T.is_dev_store(store_dir)
    return (Path(selection_path) if selection_path else (SELECTION_DEV_PATH if dev else SELECTION_PATH),
            Path(out_dir) if out_dir else (ARTIFACT_DEV_DIR if dev else ARTIFACT_DIR))


def select_command(*, store_dir: Path | None = None, runs: list[Path] | None = None, runs_csv: Path | None = None,
                   runs_dir: Path = T.RUNS_DIR, selection_path: Path | None = None, seed: int = 0,
                   median_warning: Callable | None = None, config_path: Path = T.CONFIG_PATH, out=print) -> int:
    """`select`: one run per rung (given, or the latest finished seed-`seed` run of each rung on the store from the
    runs CSV), the rule of spec §7.9, and the record in selection.json. The warning check is the evaluation
    driver's, on validation only, unless `median_warning` is given."""
    config = T.load_config(config_path)
    store_dir = Path(store_dir) if store_dir is not None else Path(store.STORE_V2_DIR)
    selection_path, _ = _paths(store_dir, selection_path, None)
    if runs:
        by_rung = {T.load_result(d)["rung"]: Path(d) for d in runs}
    else:
        info = T.store_info(store_dir)
        found = latest_runs(runs_csv or T.default_runs_csv(store_dir), info["store_hash"],
                            rungs=("L", *config["ensemble"]["ladder"]), seeds={seed}, runs_dir=runs_dir)
        by_rung = {r: d for (r, _), d in found.items()}
    if not any(r in by_rung for r in config["ensemble"]["ladder"]):
        out("error: no finished run of rungs A, B or C on this store")
        return 1
    if median_warning is None:
        from src.pipeline import neural_eval
        median_warning = neural_eval.median_warning(store_dir)
    select(config, by_rung, out_path=selection_path, median_warning=median_warning, log=out)
    return 0


def calibrate_command(*, store_dir: Path | None = None, rung: str | None = None, runs: list[Path] | None = None,
                      runs_csv: Path | None = None, runs_dir: Path = T.RUNS_DIR, selection_path: Path | None = None,
                      out_dir: Path | None = None, workers: int | None = None, config_path: Path = T.CONFIG_PATH,
                      out=print) -> int:
    """`calibrate`: the ensemble of the chosen rung's seeds (config ensemble.seeds), its calibration and the artifact.
    The rung comes from `rung`, else from selection.json; the runs are given or the latest finished per seed."""
    config = T.load_config(config_path)
    store_dir = Path(store_dir) if store_dir is not None else Path(store.STORE_V2_DIR)
    selection_path, out_dir = _paths(store_dir, selection_path, out_dir)
    selection = json.loads(selection_path.read_text(encoding="utf-8")) if selection_path.exists() else None
    if runs is None:
        rung = rung or (selection or {}).get("chosen")
        if rung is None:
            out("error: no rung given and no selection record; run `select` first")
            return 1
        seeds = [int(s) for s in config["ensemble"]["seeds"]]
        found = latest_runs(runs_csv or T.default_runs_csv(store_dir), T.store_info(store_dir)["store_hash"],
                            rungs=(rung,), seeds=set(seeds), runs_dir=runs_dir)
        missing = [s for s in seeds if (rung, s) not in found]
        if missing:
            out(f"error: rung {rung} has no finished run for seeds {missing}; train them with --seeds")
            return 1
        runs = [found[(rung, s)] for s in seeds]
    build(config, list(runs), store_dir=store_dir, out_dir=out_dir, selection=selection, workers=workers, log=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rung selection, ensemble, calibration and the artifact (spec §7.9-§7.10).")
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("select", "calibrate"):
        p = sub.add_parser(name)
        p.add_argument("--store", type=Path, default=None)
        p.add_argument("--run", type=Path, action="append", default=None, help="a run directory (repeatable)")
        p.add_argument("--runs-csv", type=Path, default=None)
        p.add_argument("--selection", type=Path, default=None)
    sub.choices["calibrate"].add_argument("--rung", default=None)
    sub.choices["calibrate"].add_argument("--out", type=Path, default=None)
    sub.choices["calibrate"].add_argument("--workers", type=int, default=None)
    args = ap.parse_args(argv)
    if args.command == "select":
        return select_command(store_dir=args.store, runs=args.run, runs_csv=args.runs_csv, selection_path=args.selection)
    return calibrate_command(store_dir=args.store, rung=args.rung, runs=args.run, runs_csv=args.runs_csv,
                             selection_path=args.selection, out_dir=args.out, workers=args.workers)


if __name__ == "__main__":
    sys.exit(main())
