"""TracerNet's training sampler and validation tick builder (docs/neural/spec.md §7.5, §7.7, §8.1).

    python -m src.neural.samplers --store data/store-v2-dev --workers 17     # measure the training throughput

Training reads the observed store only, through src/models/tracernet/features.py.

Training sampler. Each epoch draws `specimens_per_epoch` train specimens with replacement, a breaching specimen
(scan-derived: no receipt, or receipt after the window) 3 times as often as the others and weighted 1/3, so the
weighted loss stays unbiased. Each draw takes up to 4 distinct ticks of the specimen's scoring ticks (every 5
minutes while the window is open and the specimen is unreceived, the same ticks src/pipeline's Spec scores) with
probability proportional to 1/sqrt(t). The draws of a run are one task; tasks go to a pool of worker processes that
build the run's context once and the features of its sampled ticks; the results are pooled over a few tasks,
shuffled and cut into chunks the training loop consumes. The plan and the shuffle depend only on (seed, epoch), and
results are consumed in task order, so chunks are identical whatever the number of workers.

Validation ticks. Every tick of every scored specimen of a split, or the `val_sel` subset: a fixed 25% of the
validation scenarios (lowest salted hash of the scenario id) with ticks up to the smaller of the window and 240
minutes. Chunks keep the (run, specimen, tick) order.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

import numpy as np

from src.models.tracernet import features as F

ROOT = Path(__file__).resolve().parents[2]
CACHE_ROOT = ROOT / "data" / "neural" / "cache"
TICKS_PER_SPECIMEN = 4
BREACH_FACTOR = 3.0
CHUNK = 2048
VAL_SEL_SHARE = 0.25
VAL_SEL_MAX_TICK = 240
VAL_SEL_SALT = "tracer-val-sel-v1"
VAL_SEL_MAX_SPECIMENS = 100_000  # ASSUMED: a fixed sample of the val_sel scenarios' specimens (salted hash), so its
                                 # ticks fit in memory once per run instead of being rebuilt every epoch
MAX_TASK = 8192          # samples per task; a bigger run is split
FIT_EPOCH = 1_000_000    # the plan the scalers are fitted on, apart from every training epoch
WARMUP_EPOCH = 1_000_001


# --- Selection ---------------------------------------------------------------------------------------------------

def split_code(t: F.Table, split: str) -> int:
    if split not in t.names["splits"]:
        raise KeyError(f"split {split!r} is not in the table ({t.names['splits']})")
    return t.names["splits"].index(split)


def scored_rows(t: F.Table, split: str, scenarios=None) -> np.ndarray:
    """Rows of a split that are scored (a scored replicate, a scored day) and have at least one tick."""
    a = t.a
    keep = (np.asarray(a["split"]) == split_code(t, split)) & ~np.asarray(a["history"]) & np.asarray(a["scored"])
    if scenarios is not None:
        codes = [t.names["scenarios"].index(s) for s in scenarios]
        keep &= np.isin(np.asarray(a["scenario"]), codes)
    rows = np.flatnonzero(keep)
    return rows[t.n_ticks(rows) > 0]


def val_sel_scenarios(t: F.Table, split: str = "val", share: float = VAL_SEL_SHARE,
                      salt: str = VAL_SEL_SALT) -> list[str]:
    """A fixed share of a split's scenarios: those with the lowest salted SHA-256 of their id (at least one)."""
    code = split_code(t, split)
    names = sorted({t.names["scenarios"][s] for s, sp in zip(np.asarray(t.a["run_scenario"]),
                                                              np.asarray(t.a["run_split"])) if sp == code})
    ranked = sorted(names, key=lambda s: hashlib.sha256(f"{salt}:{s}".encode()).hexdigest())
    return sorted(ranked[:max(1, int(round(share * len(names))))])


def all_ticks(t: F.Table, rows: np.ndarray, max_tick: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Every tick of every row, (row, tick) in row order then tick order."""
    n = t.n_ticks(rows)
    if max_tick is not None:
        n = np.minimum(n, int(max_tick // F.TICK_MIN))
    rows_rep = np.repeat(rows, n)
    first = np.repeat(np.cumsum(n) - n, n)
    ticks = (np.arange(len(rows_rep)) - first + 1) * F.TICK_MIN
    return rows_rep, ticks.astype(np.float64)


def validation_ticks(t: F.Table, split: str = "val", subset: str | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(rows, ticks) for a split's scored specimens; subset="val_sel" for the selection subset."""
    if subset is None:
        return all_ticks(t, scored_rows(t, split))
    if subset != "val_sel":
        raise ValueError(f"unknown subset {subset!r}")
    return all_ticks(t, val_sel_rows(t, split), VAL_SEL_MAX_TICK)


def val_sel_rows(t: F.Table, split: str = "val", limit: int = VAL_SEL_MAX_SPECIMENS,
                 salt: str = VAL_SEL_SALT) -> np.ndarray:
    """The scored rows of the val_sel scenarios; above `limit`, the `limit` specimens with the lowest salted
    SHA-256 of (run, serial), so every rung and seed is judged on the same specimens."""
    rows = scored_rows(t, split, val_sel_scenarios(t, split, salt=salt))
    if len(rows) <= limit:
        return rows
    run, serial = np.asarray(t.a["run"])[rows], np.asarray(t.a["serial"])[rows]
    keys = np.array([hashlib.sha256(f"{salt}:{r}:{s}".encode()).digest()[:8] for r, s in zip(run, serial)])
    order = np.argsort(np.frombuffer(keys.tobytes(), dtype=">u8"), kind="stable")
    return np.sort(rows[order[:limit]])


def draw_ticks(rng: np.random.Generator, n_ticks: np.ndarray, k: int = TICKS_PER_SPECIMEN) -> tuple:
    """Up to k distinct tick numbers (1-based) per draw, with probability proportional to 1/sqrt(t).

    Returns (draw index per tick, tick in minutes). Inverse-CDF draws over a shared cumulative table, with
    repeated rounds for the draws that came out with a duplicate."""
    n_max = int(n_ticks.max()) if len(n_ticks) else 0
    cum = np.cumsum(1.0 / np.sqrt(F.TICK_MIN * np.arange(1, n_max + 1)))
    take = np.minimum(n_ticks, k)
    small = n_ticks <= k
    out = np.zeros((len(n_ticks), k), dtype=np.int64)
    if small.any():
        out[small] = np.where(np.arange(k)[None, :] < n_ticks[small, None], np.arange(1, k + 1)[None, :], 0)
    big = np.flatnonzero(~small)
    todo = big
    while len(todo):
        u = rng.random((len(todo), k)) * cum[n_ticks[todo] - 1][:, None]
        picks = np.searchsorted(cum, u, side="right") + 1
        picks = np.minimum(picks, n_ticks[todo][:, None])
        srt = np.sort(picks, axis=1)
        dup = (srt[:, 1:] == srt[:, :-1]).any(1)
        out[todo[~dup]] = picks[~dup]
        todo = todo[dup]
    draw = np.repeat(np.arange(len(n_ticks)), take)
    ticks = out[np.arange(k)[None, :] < take[:, None]]
    return draw, ticks.astype(np.float64) * F.TICK_MIN


def epoch_plan(t: F.Table, rows: np.ndarray, seed: int, epoch: int, n_specimens: int,
               k: int = TICKS_PER_SPECIMEN, factor: float = BREACH_FACTOR) -> dict:
    """The draws of one epoch: rows, ticks and loss weights, plus the drawn specimens' breach flags."""
    rng = np.random.default_rng([int(seed), int(epoch)])
    breach = t.breach(rows)
    p = np.where(breach, factor, 1.0)
    picked = rng.choice(len(rows), size=int(n_specimens), p=p / p.sum())
    draw, ticks = draw_ticks(rng, t.n_ticks(rows[picked]), k)
    return {"rows": rows[picked][draw], "ticks": ticks,
            "weight": np.where(breach[picked][draw], 1.0 / factor, 1.0).astype(np.float32),
            "drawn": rows[picked], "drawn_breach": breach[picked]}


def tasks_of(t: F.Table, rows: np.ndarray, ticks: np.ndarray, weight: np.ndarray | None = None,
             max_task: int = MAX_TASK) -> list[tuple]:
    """(run, rows, ticks, weights) per run, runs in table order, a run's samples in their given order."""
    runs = np.asarray(t.a["run"])[rows]
    order = np.argsort(runs, kind="stable")
    rows, ticks, runs = rows[order], ticks[order], runs[order]
    weight = np.ones(len(rows), dtype=np.float32) if weight is None else weight[order]
    out = []
    bounds = np.flatnonzero(np.r_[True, runs[1:] != runs[:-1], True])
    for a, b in zip(bounds[:-1], bounds[1:]):
        for s in range(a, b, max_task):
            e = min(b, s + max_task)
            out.append((int(runs[a]), rows[s:e], ticks[s:e], weight[s:e]))
    return out


# --- Workers -----------------------------------------------------------------------------------------------------

_W: dict = {}


def _init(cache_dir: str, schema: dict | None) -> None:
    _W["table"] = F.Table.load(Path(cache_dir), mmap=True)
    _W["schema"] = schema


def _work(task: tuple) -> dict:
    run, rows, ticks, weight = task
    out = F.features_for_run(_W["table"], run, rows, ticks, _W["schema"])
    out["weight"] = np.asarray(weight, dtype=np.float32)
    return out


def table_digest(t: F.Table) -> str:
    """The identity of everything a worker reads from a table: every array and history array in full (name,
    type, shape and bytes), the names, the rosters, the meta (where a transformed table such as the shuffled-label
    control says so) and the feature-schema version."""
    h = hashlib.sha256()
    for part, arrays in (("a", t.a), ("hist", t.hist)):
        for key in sorted(arrays):
            v = np.ascontiguousarray(arrays[key])
            h.update(f"{part}.{key}:{v.dtype.str}:{v.shape}\n".encode())
            h.update(v.reshape(-1).view(np.uint8))
    h.update(json.dumps({"names": t.names, "rosters": t.rosters, "meta": t.meta}, sort_keys=True,
                        default=str).encode())
    h.update(F.SCHEMA_VERSION.encode())
    return h.hexdigest()


def cached_table_dir(t: F.Table, cache_root: Path = CACHE_ROOT) -> Path:
    """The table saved once where worker processes memory-map it, keyed by its complete content (table_digest),
    so a cache made from other content is never reused."""
    path = Path(cache_root) / f"table-{table_digest(t)}"
    if not (path / "table.json").exists():
        t.save(path)
    return path


class Workers:
    """A spawn-context pool whose processes each memory-map the table once, or in-process work for workers <= 1."""

    def __init__(self, t: F.Table, schema: dict | None, workers: int | None = None, cache_root: Path = CACHE_ROOT):
        self.table, self.schema = t, schema
        self.workers = workers if workers is not None else max(1, (os.cpu_count() or 2) - 1)
        self.pool = None
        if self.workers > 1:
            path = cached_table_dir(t, cache_root)
            self.pool = mp.get_context("spawn").Pool(self.workers, initializer=_init, initargs=(str(path), schema))

    def map(self, tasks: list[tuple]):
        """Results in task order."""
        if self.pool is None:
            for run, rows, ticks, weight in tasks:
                out = F.features_for_run(self.table, run, rows, ticks, self.schema)
                out["weight"] = np.asarray(weight, dtype=np.float32)
                yield out
        else:
            yield from self.pool.imap(_work, tasks, chunksize=1)

    def close(self) -> None:
        if self.pool is not None:
            self.pool.close()
            self.pool.join()
            self.pool = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def chunks_of(results, chunk: int, rng: np.random.Generator | None = None, buffer_tasks: int = 16):
    """Cut a stream of result dicts into chunks of `chunk` samples; with rng, shuffle within each buffer of tasks."""
    buf, carry = [], None
    for res in results:
        buf.append(res)
        if len(buf) >= buffer_tasks:
            carry = yield from _emit(buf, carry, chunk, rng, final=False)
            buf = []
    yield from _emit(buf, carry, chunk, rng, final=True)


def _emit(buf: list, carry, chunk: int, rng, final: bool):
    parts = ([carry] if carry is not None else []) + buf
    if not parts:
        return None
    data = F.concat(parts)
    n = len(data["tick"])
    if rng is not None:
        perm = rng.permutation(n)
        data = {k: v[perm] for k, v in data.items()}
    full = (n // chunk) * chunk
    for s in range(0, full, chunk):
        yield {k: v[s:s + chunk] for k, v in data.items()}
    rest = {k: v[full:] for k, v in data.items()}
    if final:
        if n > full:
            yield rest
        return None
    return rest if n > full else None


class TrainSampler:
    """Shuffled, weighted training chunks, one epoch at a time (spec §7.7)."""

    def __init__(self, t: F.Table, schema: dict | None, *, split: str = "train", seed: int = 0,
                 specimens_per_epoch: int | None = None, ticks_per_specimen: int = TICKS_PER_SPECIMEN,
                 breach_factor: float = BREACH_FACTOR, chunk: int = CHUNK, workers: int | None = None,
                 buffer_tasks: int = 16, cache_root: Path = CACHE_ROOT):
        self.table, self.schema, self.seed = t, schema, seed
        self.rows = scored_rows(t, split)
        if not len(self.rows):
            raise RuntimeError(f"no scored specimen with a tick in split {split!r}")
        self.specimens = specimens_per_epoch or len(self.rows)
        self.k, self.factor, self.chunk, self.buffer_tasks = ticks_per_specimen, breach_factor, chunk, buffer_tasks
        self.workers = Workers(t, schema, workers, cache_root)

    def plan(self, epoch: int) -> dict:
        return epoch_plan(self.table, self.rows, self.seed, epoch, self.specimens, self.k, self.factor)

    def tasks(self, epoch: int) -> list[tuple]:
        plan = self.plan(epoch)
        tasks = tasks_of(self.table, plan["rows"], plan["ticks"], plan["weight"])
        order = np.random.default_rng([int(self.seed), int(epoch), 2]).permutation(len(tasks))
        return [tasks[i] for i in order]

    def epoch(self, epoch: int):
        rng = np.random.default_rng([int(self.seed), int(epoch), 1])
        yield from chunks_of(self.workers.map(self.tasks(epoch)), self.chunk, rng, self.buffer_tasks)

    def state(self, epoch: int) -> dict:
        """Everything a checkpoint needs to resume the sampler: the plan is a function of these alone."""
        return {"seed": self.seed, "epoch": epoch, "specimens_per_epoch": self.specimens, "k": self.k,
                "factor": self.factor, "chunk": self.chunk, "buffer_tasks": self.buffer_tasks}

    def close(self) -> None:
        self.workers.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def stream(t: F.Table, schema: dict | None, rows: np.ndarray, ticks: np.ndarray, *, chunk: int = 16384,
           workers: int | None = None, cache_root: Path = CACHE_ROOT, pool: Workers | None = None):
    """Unshuffled chunks of features for (rows, ticks), in run order (validation, test, calibration)."""
    own = pool is None
    pool = pool or Workers(t, schema, workers, cache_root)
    try:
        yield from chunks_of(pool.map(tasks_of(t, rows, ticks)), chunk, None, buffer_tasks=4)
    finally:
        if own:
            pool.close()


def fit_feature_schema(t: F.Table, *, split: str = "train", samples: int = 200_000, seed: int = 0,
                       workers: int | None = None, cache_root: Path = CACHE_ROOT, store_info: dict | None = None) -> dict:
    """Fit the scalers on raw features of train ticks drawn as the training sampler draws them (unweighted)."""
    rows = scored_rows(t, split)
    plan = epoch_plan(t, rows, seed, FIT_EPOCH, max(1, samples // TICKS_PER_SPECIMEN))
    with Workers(t, None, workers, cache_root) as pool:
        raw = F.concat(list(pool.map(tasks_of(t, plan["rows"], plan["ticks"], plan["weight"]))))
    return F.fit_schema(raw, fitted_on={"split": split, "samples": int(len(raw["tick"])), "seed": seed},
                        store_info=store_info)


def benchmark(t: F.Table, schema: dict, *, workers: int, specimens: int, seed: int = 0) -> dict:
    """Samples per second of full rung-C inputs, consumed from the sampler after a warm-up epoch."""
    with TrainSampler(t, schema, seed=seed, specimens_per_epoch=specimens, workers=workers) as s:
        for _ in s.epoch(WARMUP_EPOCH):
            pass
        started = time.perf_counter()
        n = 0
        for chunk in s.epoch(0):
            n += len(chunk["tick"])
        wall = time.perf_counter() - started
    return {"workers": workers, "samples": n, "seconds": round(wall, 3), "samples_per_second": round(n / wall)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Measure the training sampler's throughput on a v2 store.")
    ap.add_argument("--store", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--specimens", type=int, default=150_000)
    args = ap.parse_args(argv)
    t = F.load_table(args.store)
    schema = fit_feature_schema(t, samples=100_000, workers=args.workers)
    r = benchmark(t, schema, workers=args.workers, specimens=args.specimens)
    print(f"{r['samples']} samples in {r['seconds']} s with {r['workers']} workers: {r['samples_per_second']} per second")
    return 0


if __name__ == "__main__":
    sys.exit(main())
