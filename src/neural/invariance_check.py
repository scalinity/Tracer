"""Future invariance of TracerNet on real trails (the negative control's audit, src/pipeline/control_audit.py, check 3).

tests/test_features.py checks the feature builder's future invariance on fixtures. This repeats the check end to end
on the v1-world control store with the committed artifact: for a few scored runs and cut times T, a copy of the store
loses every scan of that run after T (and every specimen of that run with no scan left), the run is scored again,
and each tick at least two minutes before T must give the same p_miss as with the whole store. Any difference means a score reads
something that had not happened yet.
"""
from __future__ import annotations

import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

import numpy as np


def run(artifact: Path, *, n_ticks: int = 400, seed: int = 0, runs: int = 4, cuts_per_run: int = 3,
        store_dir: Path | None = None) -> dict:
    import duckdb
    import polars as pl

    from src.models.tracernet.predictor import Predictor
    from src.pipeline import store

    store_dir = Path(store_dir) if store_dir is not None else store.CONTROL_STORE_DIR
    rng = np.random.default_rng(seed)
    con = duckdb.connect(str(store_dir / store.OBSERVED_NAME), read_only=True)
    scored = [r for (r,) in con.execute("select run from runs where role = 'scored' order by run").fetchall()]
    bounds = dict((r, (a, b)) for r, a, b in con.execute(
        "select run, scored_start, scored_end from runs").fetchall())
    con.close()
    chosen = sorted(rng.choice(scored, size=min(runs, len(scored)), replace=False).tolist())
    pred = Predictor.load(artifact, device="cpu")
    full = pred.score(store_dir, chosen, workers=1)
    _check_clock(full, store_dir)
    checks, max_diff, compared = [], 0.0, 0
    with tempfile.TemporaryDirectory() as tmp:
        for r in chosen:
            start, end = bounds[r]
            for frac in sorted(rng.uniform(0.15, 0.85, size=cuts_per_run)):
                cut = start + (end - start) * float(frac)
                copy = Path(tmp) / f"r{r}-{int(frac * 1e6)}"
                shutil.copytree(store_dir, copy)
                c = duckdb.connect(str(copy / store.OBSERVED_NAME))
                c.execute("""delete from events where timestamp > ? and specimen_id in
                             (select specimen_id from specimens where run = ?)""", [cut, r])
                c.execute("""delete from specimens where run = ? and specimen_id not in
                             (select distinct specimen_id from events)""", [r])
                c.close()
                part = pred.score(copy, [r], workers=1)
                t0 = full.filter(pl.col("run") == r)
                both = t0.join(part.select("specimen_id", "tick_min", pl.col("p_miss").alias("p_cut"),
                                           pl.col("at").alias("at_cut")), on=["specimen_id", "tick_min"], how="inner")
                early = both.filter(pl.col("at").str.to_datetime("%Y-%m-%dT%H:%M") <= cut - timedelta(minutes=2))
                if early.height == 0:
                    continue
                d = np.abs(early["p_miss"].to_numpy() - early["p_cut"].to_numpy())
                checks.append({"run": int(r), "cut_share_of_week": round(float(frac), 3), "ticks": int(early.height),
                               "max_abs_diff": float(d.max()), "ticks_changed": int(np.sum(d > 1e-9))})
                max_diff = max(max_diff, float(d.max()))
                compared += int(early.height)
                shutil.rmtree(copy)
    return {"artifact": str(artifact), "store": str(store_dir), "runs": chosen, "cuts": checks,
            "ticks_compared": compared, "max_abs_diff": max_diff, "passed": bool(compared > 0 and max_diff <= 1e-6),
            "rule": "every tick at least two minutes before the cut scores the same p_miss (within 1e-6) with every "
                    "later scan of its run deleted"}


def _check_clock(frame, store_dir: Path) -> None:
    """The frame's `at` (minutes, text) must equal the specimen's collection scan plus its tick, on the store's own
    clock, or the cut would be compared on another clock."""
    import duckdb
    import polars as pl

    from src.generator import config
    from src.pipeline import intervals, store

    start_ev, _ = intervals.clock_events(config.load()["policy"])
    row = frame.row(0, named=True)
    con = duckdb.connect(str(Path(store_dir) / store.OBSERVED_NAME), read_only=True)
    collected = con.execute("select timestamp from events where specimen_id = ? and activity = ?",
                            [row["specimen_id"], start_ev]).fetchone()[0]
    con.close()
    at = pl.Series([row["at"]]).str.to_datetime("%Y-%m-%dT%H:%M")[0]
    gap = abs((at - (collected + timedelta(minutes=int(row["tick_min"])))).total_seconds())
    if gap >= 60:
        raise RuntimeError(f"the score's clock is {gap:.0f} s off the store's; the cut cannot be placed")
