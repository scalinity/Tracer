"""TracerNet's hyperparameter search (docs/neural/spec.md §7.8; `train --hpo`).

    python -m src.neural.hpo search  [--store data/store-v2] [--workers 2] [--devices mps,mps] [--trials 24]
                                     [--time-box-min 180] [--max-samples 1500000] [--storage URL] [--study NAME]
                                     [--first-worker K]
    python -m src.neural.hpo worker  --storage sqlite:///... --study NAME --deadline EPOCH [--device cuda:0] ...
    python -m src.neural.hpo summary --storage sqlite:///... --study NAME

Optuna's TPE sampler (a fixed seed; worker k of a parallel search uses seed + k, since processes with one seed
would propose the same points) with a median pruner, on rung B, in a wall-clock box (3 hours by default) of at
most 24 trials of at most 1.5 million training samples each, over spec §7.8's space: learning rate, weight decay,
dropout, trunk width, temporal channels, lambda, eta and batch size. Every trial is an ordinary training run
(src/neural/train.py) named `hpo-<study>-t<number>` that reports val_sel after each epoch to the pruner. Selection
uses validation only.

Parallel trials. The study lives in a SQLite file under data/neural/ (hpo.sqlite3, or hpo-dev.sqlite3 for any
store other than data/store-v2). `search` creates the study, prepares the shared feature schema and table cache
once, then starts one worker process per entry of `--workers`, each running `study.optimize` against the same
storage on its own device (`--devices cuda:0,cuda:1,...` round-robin; `cpu`, `mps` or `cuda:N`). On a cluster, run
`search` on every node with the same `--storage` (`journal:///shared/path` or a server database URL: SQLite
needs one local disk) and `--study`,
and a different `--first-worker` per node (node 2 of 4-GPU nodes: `--first-worker 4`) so no two workers share a
sampler seed; each node prepares its own caches and the last node to finish writes the complete summary. A trial
whose number is at or past the cap is pruned before it trains, so the cap holds however many workers race for the
last trial.

When the box closes, the best trial's values go to `search.best` in config/neural.yaml (real store only) and the
study summary to results/neural/hpo.json (data/neural/hpo-dev.json for any other store).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import optuna
import torch
import yaml

from src.neural import samplers as S, stages, train as T
from src.pipeline import store

ROOT = Path(__file__).resolve().parents[2]
NEURAL_DATA = ROOT / "data" / "neural"
HPO_JSON = ROOT / "results" / "neural" / "hpo.json"
HPO_DEV_JSON = NEURAL_DATA / "hpo-dev.json"
FLOAT_PARAMS = ("learning_rate", "weight_decay", "dropout")
CHOICE_PARAMS = ("width", "temporal_channels", "lam", "eta", "batch")


def storage_url(store_dir: Path) -> str:
    name = "hpo-dev.sqlite3" if T.is_dev_store(store_dir) else "hpo.sqlite3"
    return f"sqlite:///{NEURAL_DATA / name}"


def summary_path(store_dir: Path) -> Path:
    return HPO_DEV_JSON if T.is_dev_store(store_dir) else HPO_JSON


def study_name_for(config: dict, info: dict) -> str:
    s = config["search"]
    return f"tracernet-{s['rung']}-{str(info.get('store_hash'))[:8]}-{stages.obj_hash(s['space'])[:6]}"


def open_storage(url: str):
    """The study storage. `journal:///path` is Optuna's journal file, safe on a shared cluster filesystem, so search
    workers on several nodes can share one study; otherwise an RDB URL, where SQLite waits up to a minute for a lock
    instead of failing (trials are minutes long)."""
    if url.startswith("journal://"):
        from optuna.storages import JournalStorage
        from optuna.storages.journal import JournalFileBackend
        return JournalStorage(JournalFileBackend(url[len("journal://"):]))
    kwargs = {"connect_args": {"timeout": 60}} if url.startswith("sqlite") else {}
    return optuna.storages.RDBStorage(url, engine_kwargs=kwargs)


def make_sampler(config: dict, worker_index: int = 0) -> optuna.samplers.TPESampler:
    s = config["search"]
    return optuna.samplers.TPESampler(seed=int(s["sampler_seed"]) + worker_index,
                                      n_startup_trials=int(s["startup_trials"]), constant_liar=True)


def make_pruner(config: dict) -> optuna.pruners.MedianPruner:
    s = config["search"]
    return optuna.pruners.MedianPruner(n_startup_trials=int(s["pruner_startup_trials"]),
                                       n_warmup_steps=int(s["pruner_warmup_epochs"]))


def suggest(trial: optuna.Trial, space: dict) -> dict:
    """Spec §7.8's space: floats as ranges (log where marked), the rest as categorical choices."""
    out = {}
    for name in FLOAT_PARAMS:
        r = space[name]
        out[name] = trial.suggest_float(name, float(r["low"]), float(r["high"]), log=bool(r.get("log", False)))
    for name in CHOICE_PARAMS:
        out[name] = trial.suggest_categorical(name, list(space[name]))
    return out


def create_study(config: dict, url: str, name: str) -> optuna.Study:
    return optuna.create_study(storage=open_storage(url), study_name=name, direction="minimize",
                               sampler=make_sampler(config), pruner=make_pruner(config), load_if_exists=True)


def objective_for(config: dict, *, store_dir: Path, out_dir: Path, runs_csv: Path, device: str, deadline: float,
                  max_samples: int, max_trials: int, table, study_name: str, workers: int | None,
                  worker_index: int = 0, log=print):
    s = config["search"]

    def objective(trial: optuna.Trial) -> float:
        if trial.number >= max_trials:
            trial.study.stop()
            raise optuna.TrialPruned(f"trial {trial.number} is past the cap of {max_trials}")
        remaining = deadline - time.time()
        if remaining < 60:
            trial.study.stop()
            raise optuna.TrialPruned("the search's time box has closed")
        params = suggest(trial, s["space"])
        run_id = f"hpo-{study_name}-t{trial.number:03d}"
        trial.set_user_attr("run_id", run_id)
        trial.set_user_attr("device", device)
        trial.set_user_attr("worker", worker_index)
        trial.set_user_attr("pid", os.getpid())

        def on_epoch(epoch: int, val_sel: float) -> bool:
            trial.report(val_sel, epoch)
            return trial.should_prune()

        r = T.train(config, rung=s["rung"], seed=int(s["train_seed"]), store_dir=store_dir, out_dir=out_dir,
                    max_samples=max_samples, time_box_s=remaining, device=device, run_id=run_id, runs_csv=runs_csv,
                    overrides=params, workers=workers, on_epoch=on_epoch, table=table, log=log)
        trial.set_user_attr("status", r.status)
        trial.set_user_attr("epochs", r.epochs)
        trial.set_user_attr("samples", r.samples)
        trial.set_user_attr("wall_s", r.wall_s)
        if r.status == "pruned":
            raise optuna.TrialPruned(f"pruned after epoch {r.epochs}")
        if r.best_val_sel is None:
            raise RuntimeError(f"trial {trial.number} ended {r.status} with no val_sel")
        return float(r.best_val_sel)
    return objective


def worker(*, storage: str, study_name: str, deadline: float, worker_index: int = 0, device: str = "auto",
           store_dir: Path | None = None, out_dir: Path = T.RUNS_DIR, runs_csv: Path | None = None,
           max_samples: int | None = None, max_trials: int | None = None, feature_workers: int | None = None,
           config_path: Path = T.CONFIG_PATH, log=print) -> int:
    """One worker process: load the study and run trials until the cap, the box or the study stops it."""
    config = T.load_config(config_path)
    s = config["search"]
    store_dir = Path(store_dir) if store_dir is not None else Path(store.STORE_V2_DIR)
    max_trials = int(max_trials or s["trials"])
    study = optuna.create_study(storage=open_storage(storage), study_name=study_name, direction="minimize",
                                sampler=make_sampler(config, worker_index), pruner=make_pruner(config),
                                load_if_exists=True)
    table = T.load_train_table(store_dir)
    objective = objective_for(config, store_dir=store_dir, out_dir=Path(out_dir),
                              runs_csv=runs_csv or T.default_runs_csv(store_dir), device=device, deadline=deadline,
                              max_samples=int(max_samples or s["max_samples"]), max_trials=max_trials, table=table,
                              study_name=study_name, workers=feature_workers, worker_index=worker_index, log=log)
    remaining = deadline - time.time()
    if remaining > 0:
        study.optimize(objective, timeout=remaining, catch=(RuntimeError,),
                       callbacks=[optuna.study.MaxTrialsCallback(max_trials, states=None)])
    return 0


def summarize(storage: str, study_name: str, max_trials: int) -> dict:
    study = optuna.load_study(study_name=study_name, storage=open_storage(storage))
    trials = [t for t in study.get_trials(deepcopy=False) if t.number < max_trials]
    complete = [t for t in trials if t.state == optuna.trial.TrialState.COMPLETE]
    best = min(complete, key=lambda t: t.value) if complete else None
    return {"study": study_name, "storage": storage, "direction": "minimize", "metric": "val_sel",
            "trials": [{"number": t.number, "state": t.state.name, "value": t.value, "params": t.params,
                        "intermediate": {str(k): v for k, v in sorted(t.intermediate_values.items())},
                        **{k: t.user_attrs.get(k) for k in ("run_id", "device", "worker", "pid", "status", "epochs",
                                                             "samples", "wall_s")},
                        "started": t.datetime_start.isoformat(timespec="seconds") if t.datetime_start else None,
                        "finished": t.datetime_complete.isoformat(timespec="seconds") if t.datetime_complete else None}
                       for t in trials],
            "counts": {state: sum(t.state.name == state for t in trials) for state in ("COMPLETE", "PRUNED", "FAIL",
                                                                                       "RUNNING")},
            "best": None if best is None else {"number": best.number, "value": best.value, "params": best.params,
                                               "run_id": best.user_attrs.get("run_id")}}


def _yaml_scalar(v) -> str:
    """A number as YAML 1.1 reads it back (PyYAML needs a dot in a float's mantissa: 3.0e-05, not 3e-05)."""
    if isinstance(v, float):
        mantissa, _, exponent = repr(v).partition("e")
        return (mantissa if "." in mantissa else mantissa + ".0") + (f"e{exponent}" if exponent else "")
    return json.dumps(v)


def write_best(params: dict, path: Path = T.CONFIG_PATH) -> None:
    """Replace `search.best` in config/neural.yaml with the searched values, keeping every comment."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    block = "  best:" + ("\n" + "\n".join(f"    {k}: {_yaml_scalar(v)}" for k, v in params.items()) if params
                         else " null") + "\n"
    pattern = re.compile(r"^  best:.*\n(?:    .*\n|\s*\n(?=    ))*", re.M)
    search_at = text.index("\nsearch:")
    m = pattern.search(text, search_at)
    if m is None:
        raise RuntimeError("config/neural.yaml has no search.best line to replace")
    new = text[:m.start()] + block + text[m.end():]
    if yaml.safe_load(new)["search"]["best"] != (params or None):
        raise RuntimeError("writing search.best did not round-trip")
    path.write_text(new, encoding="utf-8")


def search(config: dict, *, store_dir: Path | None = None, workers: int = 1, devices=("auto",),
           storage: str | None = None, study_name: str | None = None, trials: int | None = None,
           time_box_s: float | None = None, max_samples: int | None = None, out_dir: Path = T.RUNS_DIR,
           runs_csv: Path | None = None, summary_out: Path | None = None, write_config: bool | None = None,
           first_worker: int = 0, config_path: Path = T.CONFIG_PATH, log=print) -> dict:
    """Create the study, run `workers` worker processes against it until the box or the cap, and write the
    summary (and, on the real store, search.best)."""
    s = config["search"]
    store_dir = Path(store_dir) if store_dir is not None else Path(store.STORE_V2_DIR)
    storage = storage or storage_url(store_dir)
    info = T.store_info(store_dir)
    study_name = study_name or study_name_for(config, info)
    trials = int(trials or s["trials"])
    time_box_s = float(time_box_s if time_box_s is not None else 3600 * float(s["time_box_h"]))
    max_samples = int(max_samples or s["max_samples"])
    NEURAL_DATA.mkdir(parents=True, exist_ok=True)
    create_study(config, storage, study_name)
    # the shared inputs every worker reads, prepared once so no two processes write them at the same time
    table = T.load_train_table(store_dir)
    tcfg = T.training_config(config)
    T.feature_schema(table, info, tcfg, workers=max(1, T.default_workers("mps")))
    S.cached_table_dir(table)
    deadline = time.time() + time_box_s
    torch_threads = 0 if all(str(d).startswith(("cuda", "mps")) for d in devices) else torch.get_num_threads()
    feature_workers = max(1, (S.available_cores() - 2 - workers * torch_threads) // max(1, workers))
    started = stages.now_iso()
    procs = []
    for k in range(workers):
        cmd = [sys.executable, "-m", "src.neural.hpo", "worker", "--storage", storage, "--study", study_name,
               "--deadline", repr(deadline), "--worker-index", str(first_worker + k),
               "--device", devices[k % len(devices)],
               "--store", str(store_dir), "--out", str(out_dir), "--max-samples", str(max_samples),
               "--trials", str(trials), "--feature-workers", str(feature_workers),
               "--config", str(config_path)]
        if runs_csv is not None:
            cmd += ["--runs-csv", str(runs_csv)]
        log(f"worker {k} on {devices[k % len(devices)]}")
        procs.append(subprocess.Popen(cmd, cwd=ROOT, env=os.environ | {"PYTORCH_ENABLE_MPS_FALLBACK": "1"}))
    codes = [p.wait() for p in procs]
    summary = summarize(storage, study_name, trials) | {
        "started": started, "finished": stages.now_iso(), "time_box_s": time_box_s, "max_samples": max_samples,
        "max_trials": trials, "workers": workers, "devices": list(devices), "worker_exit_codes": codes,
        "rung": s["rung"], "sampler": {"name": "TPE", "seed": int(s["sampler_seed"]),
                                       "startup_trials": int(s["startup_trials"]), "constant_liar": True},
        "pruner": {"name": "median", "startup_trials": int(s["pruner_startup_trials"]),
                   "warmup_epochs": int(s["pruner_warmup_epochs"])},
        "space": s["space"], "store": info, "git_sha": stages.git_sha(ROOT)}
    out = Path(summary_out) if summary_out else summary_path(store_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")
    if write_config is None:
        write_config = not T.is_dev_store(store_dir)
    if write_config and summary["best"] is not None:
        write_best(summary["best"]["params"], config_path)
    best = summary["best"]
    log(f"search {study_name}: {summary['counts']} -> {out}" +
        ("" if best is None else f"; best trial {best['number']} val_sel {best['value']:.5f} {best['params']}"))
    return summary


def hpo_command(*, store_dir: Path | None = None, workers: int = 1, devices: str = "auto", trials: int | None = None,
                time_box_min: float | None = None, max_samples: int | None = None, study_name: str | None = None,
                storage: str | None = None, first_worker: int = 0, config_path: Path = T.CONFIG_PATH,
                out=print) -> int:
    """`train --hpo`: the search of spec §7.8 with `workers` parallel worker processes on `devices` (comma-separated,
    round-robin). Writes data/neural/hpo*.sqlite3, the summary JSON and, on the real store, search.best."""
    summary = search(T.load_config(config_path), store_dir=store_dir, workers=workers,
                     devices=tuple(d.strip() for d in devices.split(",")), storage=storage, study_name=study_name,
                     trials=trials, time_box_s=None if time_box_min is None else 60.0 * time_box_min,
                     max_samples=max_samples, first_worker=first_worker, config_path=config_path, log=out)
    return 0 if summary["best"] is not None else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="TracerNet hyperparameter search (spec §7.8).")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("search", help="create the study and run worker processes against it")
    p.add_argument("--store", type=Path, default=None)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--devices", default="auto", help="comma-separated, round-robin over workers: cpu, mps, cuda:N")
    p.add_argument("--trials", type=int, default=None)
    p.add_argument("--time-box-min", type=float, default=None)
    p.add_argument("--max-samples", type=int, default=None)
    p.add_argument("--study", default=None)
    p.add_argument("--storage", default=None)
    p.add_argument("--first-worker", type=int, default=0, help="worker index of this node's first worker")
    w = sub.add_parser("worker", help="one worker process against an existing study")
    w.add_argument("--storage", required=True)
    w.add_argument("--study", required=True)
    w.add_argument("--deadline", type=float, required=True, help="seconds since the epoch when the box closes")
    w.add_argument("--worker-index", type=int, default=0)
    w.add_argument("--device", default="auto")
    w.add_argument("--store", type=Path, default=None)
    w.add_argument("--out", type=Path, default=T.RUNS_DIR)
    w.add_argument("--runs-csv", type=Path, default=None)
    w.add_argument("--max-samples", type=int, default=None)
    w.add_argument("--trials", type=int, default=None)
    w.add_argument("--feature-workers", type=int, default=None)
    w.add_argument("--config", type=Path, default=T.CONFIG_PATH)
    m = sub.add_parser("summary", help="print a study's summary")
    m.add_argument("--storage", required=True)
    m.add_argument("--study", required=True)
    m.add_argument("--trials", type=int, default=24)
    args = ap.parse_args(argv)
    if args.command == "search":
        return hpo_command(store_dir=args.store, workers=args.workers, devices=args.devices, trials=args.trials,
                           time_box_min=args.time_box_min, max_samples=args.max_samples, study_name=args.study,
                           storage=args.storage, first_worker=args.first_worker)
    if args.command == "worker":
        return worker(storage=args.storage, study_name=args.study, deadline=args.deadline,
                      worker_index=args.worker_index, device=args.device, store_dir=args.store, out_dir=args.out,
                      runs_csv=args.runs_csv, max_samples=args.max_samples, max_trials=args.trials,
                      feature_workers=args.feature_workers, config_path=args.config)
    print(json.dumps(summarize(args.storage, args.study, args.trials), indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
