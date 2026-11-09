"""TracerNet's training loop (docs/neural/spec.md §7.5, §7.7, §12.2 stage S12).

    python -m src.neural.train --rung B [--seed 0] [--store data/store-v2] [--time-box-min 180] [--device auto]
    python -m src.neural.train --rung A --seed 0 --run-id A-s0-... --resume      # continue a paused or interrupted run

Training reads the observed store only, and only its train and val splits: the table is loaded with
`splits=("train", "val")`, so test and test_ood rows never enter this process. Train ticks come from
src/neural/samplers.py's TrainSampler (breaches drawn 3 times as often and weighted 1/3, up to 4 ticks per
specimen with probability proportional to 1/sqrt(t)); the loss is spec §7.5's through src/neural/losses.py.

One run, step by step:

1. Store and features. The store's `meta` table gives the policy, library and split hashes; the feature schema
   (robust scalers fitted on train ticks, seed 0) is fitted once per store and cached under
   data/neural/features/, so every run of a store, every rung and every ensemble member reads the same scaled
   inputs.
2. Device. With device="auto", the chosen rung is benchmarked for about 30 seconds in all on every available
   device (cpu, mps, cuda) and the fastest is used; both throughputs are recorded. float32 only.
3. Epoch size. An epoch is sized from the measured throughput (the slower of the device and the feature
   workers) to about `epoch_minutes`; under a time box or a sample cap it shrinks so `epochs_in_box` epochs fit.
   `epoch_samples` fixes it instead (tests). The cosine schedule (3% warmup) spans the planned steps.
4. Epochs. AdamW, gradient clipping at 1.0; after each epoch `val_sel` (miss log loss on the val_sel ticks,
   averaged per specimen then over specimens), the best epoch's weights in best.pt, and a checkpoint in last.pt
   holding the model, optimizer, schedule, sampler state and every random state. Early stopping after
   `patience` epochs without improvement.
5. Numerical guard. A non-finite loss or gradient skips the step and halves the learning rate; three in a row
   reload the last checkpoint; a run that reloads `max_reloads` times is diverged.

States (spec §12.3): running, paused (stopped after a set number of epochs, resumable exactly), finished,
stopped_early, timed_out, diverged, pruned (a search trial stopped by the pruner).

Every run writes data/neural/runs/<run_id>/: config.yaml, metrics.jsonl (one line per epoch), progress.jsonl (the mean training loss every 10 s,
for watching a run), env.json (Python,
torch, device, a hash of the installed distributions, the git SHA), feature_schema.json, last.pt, best.pt and
result.json; a finished run appends one row to results/neural/runs.csv, or data/neural/runs-dev.csv for any
store other than data/store-v2.

On CPU the run is bit-reproducible (deterministic algorithms, seeded Python, numpy and torch), and a run
resumed from its checkpoint equals an uninterrupted one exactly. MPS is not bit-reproducible.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import queue
import random
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Callable

import numpy as np
import torch
import yaml

from src.models.tracernet import features as F
from src.models.tracernet.net import TracerNet, TracerNetConfig, n_parameters
from src.neural import losses, samplers as S, stages
from src.pipeline import store

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "neural.yaml"
RUNS_DIR = ROOT / "data" / "neural" / "runs"
RUNS_CSV = ROOT / "results" / "neural" / "runs.csv"
PROGRESS_EVERY_S = 10.0  # progress.jsonl: the mean training loss of the last 10 s of steps
RUNS_CSV_DEV = ROOT / "data" / "neural" / "runs-dev.csv"
FEATURES_DIR = ROOT / "data" / "neural" / "features"
TRAIN_SPLITS = ("train", "val")
FINAL = ("finished", "stopped_early", "timed_out", "diverged", "pruned")
BASE_KEYS = ("cat", "tests", "num", "fingerprint", "tick", "window")
SERIES_KEYS = ("series_lab", "series_unit", "series_route")
TOKEN_KEYS = ("tokens", "token_mask")
LABEL_KEYS = ("target_bin", "breach", "weight")
TUNABLE = ("width", "temporal_channels", "dropout")
CSV_COLUMNS = ("run_id", "rung", "seed", "status", "config_hash", "store_hash", "schema_hash", "library_sha256",
               "splits_sha256", "policy_hash", "best_val_sel", "best_epoch", "epochs", "samples", "device",
               "wall_s", "started", "git_sha")
BOX_SHARE = 0.7    # share of a time box the planned epochs fill; the rest covers validation and setup


# --- Configuration -------------------------------------------------------------------------------------------------

def load_config(path: Path = CONFIG_PATH) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def training_config(config: dict, overrides: dict | None = None) -> dict:
    """The training section with the searched values (`search.best`) laid over it, then `overrides`."""
    tcfg = dict(config["training"])
    tcfg.update((config.get("search") or {}).get("best") or {})
    tcfg.update(overrides or {})
    if tcfg["feature_version"] != F.SCHEMA_VERSION:
        raise RuntimeError(f"config/neural.yaml names feature version {tcfg['feature_version']!r}, the code builds "
                           f"{F.SCHEMA_VERSION!r}")
    return tcfg


def is_dev_store(store_dir: Path) -> bool:
    return Path(store_dir).resolve() != Path(store.STORE_V2_DIR).resolve()


def default_runs_csv(store_dir: Path) -> Path:
    return RUNS_CSV_DEV if is_dev_store(store_dir) else RUNS_CSV


def input_keys(rung: str) -> tuple:
    return BASE_KEYS + (SERIES_KEYS if rung in ("B", "C") else ()) + (TOKEN_KEYS if rung == "C" else ())


def net_config_for(schema: dict, rung: str, tcfg: dict) -> TracerNetConfig:
    """F.net_config for a rung with the training section's tunable sizes."""
    return F.net_config(schema, rung=rung, **{k: tcfg[k] for k in TUNABLE})


# --- Store, table and feature schema -------------------------------------------------------------------------------

def store_info(store_dir: Path) -> dict:
    """The store's meta hashes (policy, library, splits, simulation manifest) and a hash of its observed file."""
    d = Path(store_dir)
    con = store.open_observed_v2(d)
    try:
        meta = dict(con.execute("select key, value from meta").fetchall())
    finally:
        con.close()
    return {"store_dir": str(d), "store_hash": stages.file_hash(d / store.OBSERVED_NAME), **meta}


def load_train_table(store_dir: Path) -> F.Table:
    """Every collected specimen of the train and val splits: the only splits training reads."""
    return F.load_table(store_dir, splits=TRAIN_SPLITS)


def feature_schema(t: F.Table, info: dict, tcfg: dict, *, cache_root: Path = FEATURES_DIR, workers: int = 1,
                   table_cache: Path = S.CACHE_ROOT) -> dict:
    """The robust scalers fitted on train ticks (seed 0), cached per store, splits and sample count."""
    key = stages.obj_hash({"store": info.get("store_hash"), "splits": list(t.names["splits"]),
                           "samples": tcfg["schema_samples"], "version": F.SCHEMA_VERSION,
                           "policy": info.get("policy_hash"), "table": S.table_digest(t)})
    path = Path(cache_root) / key / "feature_schema.json"
    if path.exists():
        return F.load_schema(path)
    schema = S.fit_feature_schema(t, samples=int(tcfg["schema_samples"]), seed=0, workers=workers,
                                  cache_root=table_cache,
                                  store_info={k: info.get(k) for k in ("store_hash", "library_sha256",
                                                                       "splits_sha256", "policy_hash")})
    F.save_schema(schema, path)
    return schema


# --- Environment ---------------------------------------------------------------------------------------------------

def freeze_hash() -> str:
    """A hash of every installed distribution and version (the content of `pip freeze`, without needing pip)."""
    lines = sorted({f"{d.metadata['Name']}=={d.version}" for d in metadata.distributions() if d.metadata["Name"]})
    return stages.sha("\n".join(lines).encode())


def environment(device: str) -> dict:
    return {"python": platform.python_version(), "platform": platform.platform(), "machine": platform.machine(),
            "torch": torch.__version__, "numpy": np.__version__, "device": device,
            "mps_available": bool(torch.backends.mps.is_available()), "cuda_available": bool(torch.cuda.is_available()),
            "torch_threads": torch.get_num_threads(), "cpu_count": os.cpu_count(), "cores_available": S.available_cores(), "freeze_hash": freeze_hash(),
            "git_sha": stages.git_sha(ROOT)}


def available_devices() -> list[str]:
    out = ["cpu"]
    if torch.backends.mps.is_available():
        out.append("mps")
    if torch.cuda.is_available():
        out.append("cuda")
    return out


def default_workers(device: str) -> int:
    """Feature worker processes: most cores when the network runs on a GPU, the cores torch leaves on CPU."""
    cores = S.available_cores()
    if device == "cpu":
        return max(1, cores - torch.get_num_threads() - 1)
    return max(1, cores - 2)


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed % 2 ** 32)
    torch.manual_seed(seed)
    if torch.backends.mps.is_available():
        torch.mps.manual_seed(seed)


def rng_state() -> dict:
    kind, keys, pos, has_gauss, cached = np.random.get_state()
    state = {"python": random.getstate(), "numpy": [kind, keys.tolist(), int(pos), int(has_gauss), float(cached)],
             "torch": torch.get_rng_state()}
    if torch.backends.mps.is_available():
        state["mps"] = torch.mps.get_rng_state()
    if torch.cuda.is_available():
        state["cuda"] = torch.cuda.get_rng_state_all()
    return state


def set_rng_state(state: dict) -> None:
    random.setstate(state["python"])
    kind, keys, pos, has_gauss, cached = state["numpy"]
    np.random.set_state((kind, np.asarray(keys, dtype=np.uint32), pos, has_gauss, cached))
    torch.set_rng_state(state["torch"])
    if "mps" in state and torch.backends.mps.is_available():
        torch.mps.set_rng_state(state["mps"])
    if "cuda" in state and torch.cuda.is_available():
        torch.cuda.set_rng_state_all(state["cuda"])


def sync(device: str) -> None:
    if device.startswith("mps"):
        torch.mps.synchronize()
    elif device.startswith("cuda"):
        torch.cuda.synchronize()


# --- Data ----------------------------------------------------------------------------------------------------------

def bounded_map(pool: S.Workers, tasks: list, lookahead: int):
    """Results of the feature tasks in task order, with at most `lookahead` tasks built ahead of the consumer.

    The same results as `pool.map(tasks)`, but `imap` there dispatches every task at once and queues results
    without bound when the workers outpace the network; this keeps memory to `lookahead` tasks."""
    if pool.pool is None:
        yield from pool.map(tasks)
        return
    pending: deque = deque()
    it = iter(tasks)
    for task in it:
        pending.append(pool.pool.apply_async(S._work, (task,)))
        if len(pending) >= lookahead:
            break
    while pending:
        result = pending.popleft().get()
        nxt = next(it, None)
        if nxt is not None:
            pending.append(pool.pool.apply_async(S._work, (nxt,)))
        yield result


def epoch_chunks(sampler: S.TrainSampler, epoch: int, lookahead: int):
    """`sampler.epoch(epoch)` chunk for chunk, through `bounded_map`."""
    rng = np.random.default_rng([int(sampler.seed), int(epoch), 1])
    yield from S.chunks_of(bounded_map(sampler.workers, sampler.tasks(epoch), lookahead), sampler.chunk, rng,
                           sampler.buffer_tasks)


class Prefetch:
    """Iterate `source` in a background thread up to `depth` items ahead, in order, so building and shuffling the
    next chunk overlaps the network's step. Exceptions in the source are raised in the consumer."""

    def __init__(self, source, depth: int = 4):
        self.q: queue.Queue = queue.Queue(depth)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(source,), daemon=True)
        self.thread.start()

    def _put(self, item) -> bool:
        while not self.stop.is_set():
            try:
                self.q.put(item, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def _run(self, source) -> None:
        try:
            for item in source:
                if not self._put(("item", item)):
                    return
            self._put(("end", None))
        except BaseException as e:  # handed to the consumer
            self._put(("error", e))

    def __iter__(self):
        while True:
            kind, item = self.q.get()
            if kind == "end":
                return
            if kind == "error":
                raise item
            yield item

    def close(self) -> None:
        self.stop.set()
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                break
        self.thread.join(timeout=30)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def tick_chunks(t: F.Table, pool: S.Workers, rows: np.ndarray, ticks: np.ndarray, chunk: int, lookahead: int,
                keys: tuple | None = None):
    """Unshuffled feature chunks for (rows, ticks) in run order, keeping only `keys` when given."""
    for c in S.chunks_of(bounded_map(pool, S.tasks_of(t, rows, ticks), lookahead), chunk, None, buffer_tasks=4):
        yield c if keys is None else {k: c[k] for k in keys}


def to_device(chunk: dict, keys, device: str) -> dict:
    return {k: torch.from_numpy(np.ascontiguousarray(chunk[k])).to(device) for k in keys}


def expected_ticks(t: F.Table, rows: np.ndarray, k: int, factor: float) -> float:
    """Mean samples per drawn specimen: min(ticks, k) under the breach-weighted draw."""
    n = np.minimum(t.n_ticks(rows), k)
    p = np.where(t.breach(rows), factor, 1.0)
    return float((p * n).sum() / p.sum())


def shuffled_table(t: F.Table, seed: int = 0) -> F.Table:
    """The shuffled-label control of spec §17 item 7, applied to the observed data: within each run, every
    specimen's collection-to-receipt time (or its absence) is another specimen's, chosen by a seeded permutation,
    and its accession moves with its receipt. Everything downstream (history fingerprints, laboratory state, ticks,
    targets) is rebuilt from the shuffled scans, so the table stays self-consistent while a specimen's outcome
    carries no information about its inputs beyond its tick and window. A network trained and scored on it should
    rank no better than chance within a (window, tick) stratum; a feature that read a specimen's own future would
    not."""
    a = {k: np.array(v) for k, v in t.a.items()}
    rng = np.random.default_rng(seed)
    for lo, hi in zip(a["run_start"], a["run_stop"]):
        c, r, acc = a["collected"][lo:hi], a["received"][lo:hi], a["accessioned"][lo:hi]
        offset = np.where(np.isfinite(acc) & np.isfinite(r), acc - r, np.nan)
        typical = np.nanmedian(offset) if np.isfinite(offset).any() else 600.0
        new_r = c + rng.permutation(r - c)
        a["received"][lo:hi] = new_r
        a["accessioned"][lo:hi] = np.where(np.isfinite(new_r), new_r + np.where(np.isfinite(offset), offset, typical),
                                           np.nan)
    out = F.Table(a, t.names, t.rosters, dict(t.meta) | {"transform": f"shuffled-label control, seed {seed}"})
    out.hist = F.history_stats(out)
    return out


# --- Metrics -------------------------------------------------------------------------------------------------------

def miss_log_loss(logit_miss: np.ndarray, breach: np.ndarray, specimen: np.ndarray) -> float:
    """Binary log loss of p_miss = sigmoid(logit_miss), averaged per specimen, then over specimens."""
    z = np.asarray(logit_miss, dtype=np.float64)
    y = np.asarray(breach, dtype=np.float64)
    loss = np.logaddexp(0.0, z) - y * z
    _, inv = np.unique(np.asarray(specimen), return_inverse=True)
    per = np.bincount(inv, weights=loss) / np.bincount(inv)
    return float(per.mean())


def predict_chunks(model: TracerNet, chunks, keys: tuple, device: str) -> dict:
    """logit_miss and p_miss of every tick, with the chunks' labels and ids, as float64 numpy arrays."""
    model.eval()
    out = {k: [] for k in ("logit_miss", "p_miss", "breach", "specimen_index", "tick", "window")}
    with torch.no_grad():
        for c in chunks:
            o = model(to_device(c, keys, device))
            out["logit_miss"].append(o["logit_miss"].cpu().double().numpy())
            out["p_miss"].append(o["p_miss"].cpu().double().numpy())
            for k in ("breach", "specimen_index", "tick", "window"):
                out[k].append(np.asarray(c[k]))
    return {k: np.concatenate(v) if v else np.zeros(0) for k, v in out.items()}


def val_sel(model: TracerNet, chunks, keys: tuple, device: str) -> float:
    p = predict_chunks(model, chunks, keys, device)
    return miss_log_loss(p["logit_miss"], p["breach"], p["specimen_index"])


def auroc(y: np.ndarray, s: np.ndarray) -> float | None:
    """Probability that a random positive outranks a random negative, ties counting one half."""
    from scipy.stats import rankdata
    y = np.asarray(y, dtype=bool)
    n_pos, n_neg = int(y.sum()), int((~y).sum())
    if n_pos == 0 or n_neg == 0:
        return None
    r = rankdata(s)
    return float((r[y].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def stratified_auroc(y: np.ndarray, s: np.ndarray, strata: np.ndarray) -> float | None:
    """AUROC over positive-negative pairs in the same stratum (pairs pooled across strata)."""
    from scipy.stats import rankdata
    y = np.asarray(y, dtype=bool)
    _, inv = np.unique(np.asarray(strata), return_inverse=True)
    u = pairs = 0.0
    order = np.argsort(inv, kind="stable")
    bounds = np.flatnonzero(np.r_[True, np.diff(inv[order]) != 0, True])
    for a, b in zip(bounds[:-1], bounds[1:]):
        idx = order[a:b]
        yy = y[idx]
        n_pos, n_neg = int(yy.sum()), int((~yy).sum())
        if n_pos == 0 or n_neg == 0:
            continue
        r = rankdata(s[idx])
        u += r[yy].sum() - n_pos * (n_pos + 1) / 2
        pairs += n_pos * n_neg
    return float(u / pairs) if pairs else None


# --- Schedule ------------------------------------------------------------------------------------------------------

def lr_at(step: int, base: float, planned: int, warmup: int) -> float:
    """Linear warmup over `warmup` steps, then a cosine to zero at `planned` steps (flat at zero after)."""
    if step < warmup:
        return base * (step + 1) / warmup
    progress = min(1.0, (step - warmup) / max(1, planned - warmup))
    return base * 0.5 * (1.0 + math.cos(math.pi * progress))


# --- Device benchmark ----------------------------------------------------------------------------------------------

def step_throughput(ncfg: TracerNetConfig, device: str, batches: list, seconds: float, tcfg: dict) -> float:
    """Training samples per second of the rung on a device: forward, loss, backward, clipping, AdamW."""
    keys = input_keys(ncfg.rung) + LABEL_KEYS
    model = TracerNet(ncfg).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=tcfg["learning_rate"], weight_decay=tcfg["weight_decay"])
    tensors = [to_device(b, keys, device) for b in batches]
    n, i, start = 0, 0, None
    while True:
        b = tensors[i % len(tensors)]
        out = model(b)
        loss, _ = losses.loss(out, b["target_bin"], b["breach"], b["tick"], model.bins, lam=tcfg["lam"],
                              eta=tcfg["eta"], weight=b["weight"])
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), tcfg["grad_clip"])
        opt.step()
        i += 1
        if i == 2:
            sync(device)
            start, n = time.perf_counter(), 0
            continue
        if start is not None:
            n += len(b["tick"])
            if time.perf_counter() - start >= seconds and i >= 4:
                break
    sync(device)
    return n / (time.perf_counter() - start)


# --- The run -------------------------------------------------------------------------------------------------------

@dataclass
class RunResult:
    run_id: str
    run_dir: str
    rung: str
    seed: int
    status: str
    best_val_sel: float | None
    best_epoch: int | None
    epochs: int
    samples: int
    steps: int
    wall_s: float
    device: str
    throughput: dict
    config_hash: str
    schema_hash: str
    store: dict
    history: list = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)


def _atomic_save(obj, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    torch.save(obj, tmp)
    os.replace(tmp, path)


def _write_json(path: Path, doc) -> None:
    path.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")


def append_run_row(path: Path, row: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


def read_runs(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_result(run_dir: Path) -> dict:
    return json.loads((Path(run_dir) / "result.json").read_text(encoding="utf-8"))


def load_member(run_dir: Path, map_location: str = "cpu") -> tuple[TracerNet, dict]:
    """The best epoch's network of a run (eval mode, CPU by default) and the run's config.yaml."""
    run_dir = Path(run_dir)
    cfg = yaml.safe_load((run_dir / "config.yaml").read_text(encoding="utf-8"))
    ncfg = TracerNetConfig(**{k: (tuple(tuple(x) if isinstance(x, list) else x for x in v) if isinstance(v, list)
                                  else v) for k, v in cfg["net"].items()})
    model = TracerNet(ncfg)
    best = torch.load(run_dir / "best.pt", map_location=map_location, weights_only=True)
    model.load_state_dict(best["model"])
    return model.eval(), cfg


def train(config: dict, *, rung: str, seed: int, store_dir: Path | None = None, out_dir: Path = RUNS_DIR,
          max_samples: int | None = None, time_box_s: float | None = None, device: str = "auto",
          run_id: str | None = None, resume: bool = False, runs_csv: Path | None = None,
          overrides: dict | None = None, workers: int | None = None, stop_after_epochs: int | None = None,
          on_epoch: Callable[[int, float], bool] | None = None, table: F.Table | None = None,
          cache_root: Path = S.CACHE_ROOT, features_root: Path = FEATURES_DIR, log: Callable = print) -> RunResult:
    """Train one run of a rung (L, A, B or C) with a seed; see the module docstring. `on_epoch(epoch, val_sel)`
    returning True stops the run as pruned (the search's median pruner). `stop_after_epochs` pauses it after that
    many epochs in this call (resume with `resume=True` and the same `run_id`)."""
    started_wall = time.perf_counter()
    started_at = stages.now_iso()
    tcfg = training_config(config, overrides)
    store_dir = Path(store_dir) if store_dir is not None else Path(store.STORE_V2_DIR)
    out_dir = Path(out_dir)
    info = store_info(store_dir)
    t = table if table is not None else load_train_table(store_dir)
    if set(t.names["splits"]) - set(TRAIN_SPLITS):
        raise RuntimeError(f"the training table holds splits {t.names['splits']}; training reads only "
                           f"{TRAIN_SPLITS}")

    run_dir = out_dir / run_id if run_id else None
    ckpt = None
    if run_dir is not None and (run_dir / "last.pt").exists():
        if not resume:
            raise RuntimeError(f"run {run_id} already exists in {out_dir}; pass resume=True to continue it")
        ckpt = torch.load(run_dir / "last.pt", map_location="cpu", weights_only=True)
        tcfg = ckpt["training"]
    if ckpt is not None and ckpt["status"] in FINAL:
        log(f"run {run_id} is already {ckpt['status']}")
        return RunResult(**load_result(run_dir))

    probe_workers = workers or tcfg["workers"] or default_workers("cpu" if device == "cpu" else "mps")
    schema = feature_schema(t, info, tcfg, cache_root=features_root, workers=probe_workers, table_cache=cache_root)
    schema_hash = stages.obj_hash(schema)
    ncfg = net_config_for(schema, rung, tcfg)
    cfg_hash = stages.obj_hash({"rung": rung, "training": tcfg, "net": asdict(ncfg), "max_samples": max_samples,
                                "time_box_s": time_box_s})
    if run_id is None:
        run_id = f"{rung}-s{seed}-{cfg_hash[:8]}-{datetime.now():%Y%m%d-%H%M%S}"
        run_dir = out_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    keys = input_keys(rung)
    batch = int(tcfg["batch"])

    sampler = S.TrainSampler(t, schema, seed=seed, specimens_per_epoch=1, ticks_per_specimen=int(tcfg["ticks_per_specimen"]),
                             breach_factor=float(tcfg["breach_factor"]), chunk=batch,
                             workers=workers or tcfg["workers"] or (default_workers(device) if device != "auto"
                                                                    else default_workers("mps")),
                             cache_root=cache_root)
    lookahead = max(4, 2 * sampler.workers.workers)
    try:
        mean_k = expected_ticks(t, sampler.rows, sampler.k, sampler.factor)
        if ckpt is None:
            plan = _plan(t, sampler, ncfg, tcfg, device, mean_k, max_samples, time_box_s, lookahead, log)
        else:
            plan = ckpt["plan"]
        sampler.specimens = plan["specimens_per_epoch"]
        dev = plan["device"]

        # validation ticks: the val_sel subset, kept in memory when it fits
        v_rows, v_ticks = S.validation_ticks(t, "val", subset="val_sel")
        v_keys = keys + ("breach", "specimen_index")
        val_chunk = int(tcfg["val_chunk"])
        cached = None
        per_tick = _bytes_per_tick(t, sampler, v_keys)
        if len(v_ticks) * per_tick <= float(tcfg["val_cache_gb"]) * 1e9:
            cached = list(tick_chunks(t, sampler.workers, v_rows, v_ticks, val_chunk, lookahead, v_keys))

        def val_batches():
            return cached if cached is not None else \
                tick_chunks(t, sampler.workers, v_rows, v_ticks, val_chunk, lookahead, v_keys)

        deterministic = torch.are_deterministic_algorithms_enabled()
        if dev == "cpu":
            torch.use_deterministic_algorithms(True)
        try:
            result = _loop(t, sampler, ncfg, tcfg, plan, dev, keys, val_batches, run_dir, run_id, rung, seed,
                           ckpt, max_samples, time_box_s, stop_after_epochs, on_epoch, lookahead, info,
                           schema, schema_hash, cfg_hash, started_wall, started_at, len(v_ticks),
                           len(np.unique(v_rows)), log)
        finally:
            torch.use_deterministic_algorithms(deterministic)
    finally:
        sampler.close()
    if result.status in FINAL:
        append_run_row(runs_csv or default_runs_csv(store_dir), {
            "run_id": run_id, "rung": rung, "seed": seed, "status": result.status, "config_hash": cfg_hash,
            "store_hash": info.get("store_hash"), "schema_hash": schema_hash,
            "library_sha256": info.get("library_sha256"), "splits_sha256": info.get("splits_sha256"),
            "policy_hash": info.get("policy_hash"),
            "best_val_sel": "" if result.best_val_sel is None else f"{result.best_val_sel:.6f}",
            "best_epoch": "" if result.best_epoch is None else result.best_epoch, "epochs": result.epochs,
            "samples": result.samples, "device": result.device, "wall_s": f"{result.wall_s:.1f}",
            "started": started_at, "git_sha": stages.git_sha(ROOT)})
    return result


def _bytes_per_tick(t: F.Table, sampler: S.TrainSampler, keys: tuple) -> int:
    row = sampler.rows[:1]
    probe = F.features_for_run(t, int(t.a["run"][row[0]]), row, np.array([5.0]), sampler.schema)
    return int(sum(probe[k].nbytes for k in keys))


def _plan(t, sampler, ncfg, tcfg, device, mean_k, max_samples, time_box_s, lookahead, log) -> dict:
    """Device choice, throughput and the epoch and schedule sizes of a new run."""
    batch = int(tcfg["batch"])
    throughput, feature_sps = {}, None
    need_tput = tcfg["epoch_samples"] is None
    candidates = available_devices() if device == "auto" else [device]
    if device == "auto" or need_tput:
        n_spec = int(math.ceil(24 * batch / mean_k))
        plan0 = S.epoch_plan(t, sampler.rows, sampler.seed, S.WARMUP_EPOCH, n_spec, sampler.k, sampler.factor)
        tasks = S.tasks_of(t, plan0["rows"], plan0["ticks"], plan0["weight"])
        batches, start, n_timed = [], None, 0
        for b in S.chunks_of(bounded_map(sampler.workers, tasks, lookahead), batch, None, 4):
            if start is None:
                start = time.perf_counter()   # from the first chunk on: pool start-up is not throughput
            else:
                n_timed += len(b["tick"])
            batches.append(b)
        feature_sps = n_timed / max(time.perf_counter() - start, 1e-9) if n_timed else float("inf")
        batches = batches[:8]
        seconds = float(tcfg["device_benchmark_s"]) / len(candidates) if device == "auto" else 5.0
        state = rng_state()
        for d in candidates:
            throughput[d] = round(step_throughput(ncfg, d, batches, seconds, tcfg), 1)
        set_rng_state(state)
        throughput["features"] = round(feature_sps, 1)
        log(f"throughput (samples per second): " + ", ".join(f"{k} {v:,.0f}" for k, v in throughput.items()))
    chosen = max(candidates, key=lambda d: throughput[d]) if device == "auto" else device
    if tcfg["epoch_samples"] is not None:
        epoch_samples = int(tcfg["epoch_samples"])
    else:
        sps = min(throughput[chosen], feature_sps)
        epoch_samples = int(float(tcfg["epoch_minutes"]) * 60 * sps)
        if time_box_s is not None:
            epoch_samples = min(epoch_samples, int(BOX_SHARE * time_box_s * sps / int(tcfg["epochs_in_box"])))
    if max_samples is not None:
        epoch_samples = min(epoch_samples, int(math.ceil(max_samples / int(tcfg["epochs_in_box"]))))
    epoch_samples = max(epoch_samples, batch)
    steps_per_epoch = int(math.ceil(epoch_samples / batch))
    planned = int(tcfg["max_epochs"]) * steps_per_epoch
    if max_samples is not None:
        planned = min(planned, int(math.ceil(max_samples / batch)))
    if time_box_s is not None and tcfg["epoch_samples"] is None:   # the steps the box is expected to hold
        planned = min(planned, max(steps_per_epoch, int(math.ceil(BOX_SHARE * time_box_s * sps / batch))))
    return {"device": chosen, "throughput": throughput, "epoch_samples": epoch_samples,
            "specimens_per_epoch": int(math.ceil(epoch_samples / mean_k)), "mean_ticks_per_specimen": mean_k,
            "steps_per_epoch": steps_per_epoch, "planned_steps": planned,
            "warmup_steps": max(1, int(math.ceil(float(tcfg["warmup_share"]) * planned))),
            "workers": sampler.workers.workers}


def _loop(t, sampler, ncfg, tcfg, plan, dev, keys, val_batches, run_dir, run_id, rung, seed, ckpt, max_samples,
          time_box_s, stop_after_epochs, on_epoch, lookahead, info, schema, schema_hash, cfg_hash, started_wall,
          started_at, n_val_ticks, n_val_specimens, log) -> RunResult:
    batch_keys = keys + LABEL_KEYS
    base_lr = float(tcfg["learning_rate"])
    if ckpt is None:
        seed_everything(seed)
        model = TracerNet(ncfg).to(dev)
        opt = torch.optim.AdamW(model.parameters(), lr=base_lr, weight_decay=float(tcfg["weight_decay"]))
        state = {"epoch": 0, "step": 0, "samples": 0, "lr_scale": 1.0, "best": None, "best_epoch": None,
                 "bad_epochs": 0, "reloads": 0, "history": [], "elapsed_s": 0.0}
        (run_dir / "config.yaml").write_text(yaml.safe_dump({
            "run_id": run_id, "rung": rung, "seed": seed, "training": tcfg, "net": asdict(ncfg), "plan": plan,
            "max_samples": max_samples, "time_box_s": time_box_s, "config_hash": cfg_hash,
            "schema_hash": schema_hash, "store": info, "started": started_at, "parameters": n_parameters(model),
            "val_sel_ticks": n_val_ticks, "val_sel_specimens": n_val_specimens}, sort_keys=False),
            encoding="utf-8")
        _write_json(run_dir / "env.json", environment(dev) | {"throughput": plan["throughput"],
                                                             "workers": plan["workers"]})
        F.save_schema(schema, run_dir / "feature_schema.json")
        _save(run_dir, model, opt, sampler, state, plan, tcfg, "running")
    else:
        model = TracerNet(ncfg).to(dev)
        opt = torch.optim.AdamW(model.parameters(), lr=base_lr, weight_decay=float(tcfg["weight_decay"]))
        model.load_state_dict(ckpt["model"])
        opt.load_state_dict(ckpt["optimizer"])
        state = ckpt["state"]
        set_rng_state(ckpt["rng"])
        log(f"resuming {run_id} after epoch {state['epoch']}")
    with (run_dir / "metrics.jsonl").open("w", encoding="utf-8") as fh:
        for line in state["history"]:
            fh.write(json.dumps(line, sort_keys=True) + "\n")
    params = list(model.parameters())
    bins = model.bins
    elapsed0 = state["elapsed_s"]
    loop_start = time.perf_counter()
    status, epochs_this_call, nonfinite = "running", 0, 0

    def elapsed():
        return elapsed0 + time.perf_counter() - loop_start

    prog_t, prog_loss, prog_n = time.perf_counter(), 0.0, 0
    while status == "running":
        epoch = state["epoch"] + 1
        model.train()
        sums = {"total": 0.0, "nll": 0.0, "bce": 0.0, "ranking": 0.0}
        n_epoch, skipped, t_epoch = 0, 0, time.perf_counter()
        cut = None
        feed = Prefetch(epoch_chunks(sampler, epoch, lookahead))
        for chunk in feed:
            b = to_device(chunk, batch_keys, dev)
            lr = lr_at(state["step"], base_lr, plan["planned_steps"], plan["warmup_steps"]) * state["lr_scale"]
            for g in opt.param_groups:
                g["lr"] = lr
            out = model(b)
            loss, parts = losses.loss(out, b["target_bin"], b["breach"], b["tick"], bins, lam=float(tcfg["lam"]),
                                      eta=float(tcfg["eta"]), weight=b["weight"])
            opt.zero_grad(set_to_none=True)
            finite = math.isfinite(parts["total"])
            if finite:
                loss.backward()
                norm = torch.nn.utils.clip_grad_norm_(params, float(tcfg["grad_clip"]))
                finite = bool(torch.isfinite(norm))
            if not finite:
                opt.zero_grad(set_to_none=True)
                skipped += 1
                nonfinite += 1
                state["lr_scale"] *= 0.5
                if nonfinite >= int(tcfg["max_nonfinite"]):
                    state["reloads"] += 1
                    nonfinite = 0
                    if state["reloads"] > int(tcfg["max_reloads"]):
                        cut, status = "diverged", "diverged"
                        break
                    last = torch.load(run_dir / "last.pt", map_location="cpu", weights_only=True)
                    model.load_state_dict(last["model"])
                    opt.load_state_dict(last["optimizer"])
                    log(f"three non-finite steps in a row: reloaded epoch {last['state']['epoch']}")
                continue
            nonfinite = 0
            opt.step()
            n = len(chunk["tick"])
            state["step"] += 1
            state["samples"] += n
            n_epoch += n
            for k in sums:
                sums[k] += parts[k] * n
            prog_loss, prog_n = prog_loss + parts["total"] * n, prog_n + n
            if time.perf_counter() - prog_t >= PROGRESS_EVERY_S:  # live loss for the monitor; not used by training
                with (run_dir / "progress.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"epoch": epoch, "step": state["step"], "samples": state["samples"],
                                         "loss": prog_loss / prog_n, "lr": lr, "elapsed_s": round(elapsed(), 1)}) + "\n")
                prog_t, prog_loss, prog_n = time.perf_counter(), 0.0, 0
            if time_box_s is not None and elapsed() >= time_box_s:
                cut = "timed_out"
                break
            if max_samples is not None and state["samples"] >= max_samples:
                cut = "budget"
                break
        feed.close()
        if status == "diverged" and n_epoch == 0:
            break
        v = val_sel(model, val_batches(), keys, dev)
        improved = math.isfinite(v) and (state["best"] is None or v < state["best"])
        if improved:
            state["best"], state["best_epoch"], state["bad_epochs"] = v, epoch, 0
            _atomic_save({"model": {k: x.detach().cpu() for k, x in model.state_dict().items()}, "epoch": epoch,
                          "val_sel": v}, run_dir / "best.pt")
        else:
            state["bad_epochs"] += 1
        line = {"epoch": epoch, "samples": n_epoch, "total_samples": state["samples"], "steps": state["step"],
                "val_sel": v, "best_val_sel": state["best"], "lr": lr if n_epoch else None,
                "lr_scale": state["lr_scale"], "skipped_steps": skipped, "seconds": round(time.perf_counter() - t_epoch, 2),
                "partial": cut in ("timed_out", "budget"), "device": dev,
                **{f"train_{k}": (s / n_epoch if n_epoch else None) for k, s in sums.items()}}
        state["history"].append(line)
        state["epoch"] = epoch
        with (run_dir / "metrics.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, sort_keys=True) + "\n")
        epochs_this_call += 1
        log(f"{run_id} epoch {epoch}: val_sel {v:.5f} (best {_fmt(state['best'])} at {state['best_epoch']}), "
            f"train loss {_fmt(line['train_total'])}, {n_epoch:,} samples in {line['seconds']} s")
        if epoch == 1 and status == "running" and cut is None:
            replan_after_first_epoch(plan, sampler, tcfg, line["seconds"], n_epoch, max_samples, time_box_s, log)
        if status == "diverged":
            pass
        elif cut == "timed_out":
            status = "timed_out"
        elif on_epoch is not None and on_epoch(epoch, v):
            status = "pruned"
        elif cut == "budget" or (max_samples is not None and
                                 state["samples"] >= max_samples - 0.5 * plan["epoch_samples"]):
            status = "finished"
        elif state["bad_epochs"] >= int(tcfg["patience"]):
            status = "stopped_early"
        elif epoch >= int(tcfg["max_epochs"]):
            status = "finished"
        elif time_box_s is not None and elapsed() >= time_box_s:
            status = "timed_out"
        elif stop_after_epochs is not None and epochs_this_call >= stop_after_epochs:
            status = "paused"
        state["elapsed_s"] = elapsed()
        _save(run_dir, model, opt, sampler, state, plan, tcfg, status)
    result = RunResult(run_id=run_id, run_dir=str(run_dir), rung=rung, seed=seed, status=status,
                       best_val_sel=state["best"], best_epoch=state["best_epoch"], epochs=state["epoch"],
                       samples=state["samples"], steps=state["step"], wall_s=round(time.perf_counter() - started_wall, 2),
                       device=dev, throughput=plan["throughput"], config_hash=cfg_hash, schema_hash=schema_hash,
                       store=info, history=state["history"])
    _write_json(run_dir / "result.json", result.to_json())
    return result


def replan_after_first_epoch(plan: dict, sampler, tcfg: dict, seconds: float, n_epoch: int, max_samples, time_box_s,
                             log) -> bool:
    """Resize the epochs from the first epoch's measured speed when it took under half its target (spec §7.7: an
    epoch takes about `epoch_minutes`). The start-up benchmark draws few samples per laboratory week, so rebuilding
    each week's context dominates it and it can underestimate the real speed many times over. The schedule is
    stretched to the new size (`max_epochs` epochs), the warm-up already done is kept, and the change is recorded in
    the plan (saved with every checkpoint, so a resumed run keeps it). Runs with a fixed epoch size, a sample cap or a
    time box keep their plan."""
    target = 60.0 * float(tcfg["epoch_minutes"])
    if tcfg["epoch_samples"] is not None or max_samples is not None or time_box_s is not None or "replanned" in plan:
        return False
    if seconds <= 0 or seconds >= 0.5 * target:
        return False
    batch = int(tcfg["batch"])
    cap = int(len(sampler.rows) * sampler.k)                # at most one full draw of every specimen's ticks
    new_epoch = max(batch, min(cap, int(plan["epoch_samples"] * target / seconds)))
    if new_epoch <= plan["epoch_samples"]:
        return False
    before = {k: plan[k] for k in ("epoch_samples", "steps_per_epoch", "planned_steps", "specimens_per_epoch")}
    plan["epoch_samples"] = new_epoch
    plan["steps_per_epoch"] = int(math.ceil(new_epoch / batch))
    plan["planned_steps"] = int(tcfg["max_epochs"]) * plan["steps_per_epoch"]
    plan["specimens_per_epoch"] = int(math.ceil(new_epoch / plan["mean_ticks_per_specimen"]))
    sampler.specimens = plan["specimens_per_epoch"]
    plan["replanned"] = {"after_epoch": 1, "measured_samples_per_s": round(n_epoch / seconds, 1),
                         "epoch_seconds": seconds, "target_seconds": target, "before": before}
    log(f"re-planned after epoch 1: it took {seconds:.0f} s against a {target:.0f}-second target at "
        f"{n_epoch / seconds:,.0f} samples a second; epochs now {new_epoch:,} samples, schedule "
        f"{plan['planned_steps']:,} steps")
    return True


def _fmt(x, digits: int = 5) -> str:
    return "none" if x is None else f"{x:.{digits}f}"


def _save(run_dir, model, opt, sampler, state, plan, tcfg, status) -> None:
    _atomic_save({"model": {k: v.detach().cpu() for k, v in model.state_dict().items()},
                  "optimizer": opt.state_dict(), "state": dict(state), "plan": plan, "training": tcfg,
                  "sampler": sampler.state(state["epoch"]), "rng": rng_state(), "status": status,
                  "schedule": {"step": state["step"], "lr_scale": state["lr_scale"],
                               "planned_steps": plan["planned_steps"], "warmup_steps": plan["warmup_steps"]}},
                 Path(run_dir) / "last.pt")


# --- Command -------------------------------------------------------------------------------------------------------

def train_command(*, rung: str, seeds=(0,), store_dir: Path | None = None, time_box_min: float | None = None,
                  max_samples: int | None = None, device: str = "auto", out_dir: Path = RUNS_DIR,
                  runs_csv: Path | None = None, run_id: str | None = None, resume: bool = False,
                  workers: int | None = None, config_path: Path = CONFIG_PATH, out=print) -> int:
    """`train --rung A|B|C|L [--seeds 0,1,2]`: one run per seed; prints each run's id, status and best val_sel."""
    config = load_config(config_path)
    store_dir = Path(store_dir) if store_dir is not None else Path(store.STORE_V2_DIR)
    table = load_train_table(store_dir)
    code = 0
    for seed in seeds:
        r = train(config, rung=rung, seed=int(seed), store_dir=store_dir, out_dir=out_dir, max_samples=max_samples,
                  time_box_s=None if time_box_min is None else 60.0 * time_box_min, device=device,
                  run_id=run_id if len(seeds) == 1 else None, resume=resume, runs_csv=runs_csv, workers=workers,
                  table=table, log=out)
        out(f"run {r.run_id}: {r.status}, best val_sel {r.best_val_sel} at epoch {r.best_epoch} of {r.epochs}, "
            f"{r.samples:,} samples on {r.device} in {r.wall_s:.0f} s ({r.run_dir})")
        code = code or (0 if r.status in ("finished", "stopped_early", "timed_out", "paused") else 1)
    return code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Train TracerNet runs (spec §7.7).")
    ap.add_argument("--rung", required=True, choices=("L", "A", "B", "C"))
    ap.add_argument("--seeds", default="0", help="comma-separated seeds, one run each")
    ap.add_argument("--store", type=Path, default=None)
    ap.add_argument("--time-box-min", type=float, default=None)
    ap.add_argument("--max-samples", type=int, default=None)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--out", type=Path, default=RUNS_DIR)
    ap.add_argument("--runs-csv", type=Path, default=None)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args(argv)
    return train_command(rung=args.rung, seeds=[int(s) for s in args.seeds.split(",")], store_dir=args.store,
                         time_box_min=args.time_box_min, max_samples=args.max_samples, device=args.device,
                         out_dir=args.out, runs_csv=args.runs_csv, run_id=args.run_id, resume=args.resume,
                         workers=args.workers)


if __name__ == "__main__":
    sys.exit(main())
