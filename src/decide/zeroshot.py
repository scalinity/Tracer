"""X3: can a decision model forecast? (docs/neural/spec.md §11.3.)

    ticks = zeroshot.sample_ticks(store_dir, "test", n=4000, seed=0, test_run=run)
    items = zeroshot.build_items(ticks, facts_rows, cfg)
    rows = zeroshot.answers(outcomes, items)

The sample is drawn by whole scenario and stratified. Scenarios of the split are taken in an order fixed by the seed
(SCENARIOS of them, every scored replicate); their ticks (every 5 minutes while the window is open and the specimen
unreceived, as evaluate.Spec.ticks counts them) fall into 18 strata: the ending its scans show (received on time,
late, or never within 7,200 minutes), the window class (2 hours or less, longer) and the silence band (up to 30, 30 to
90, over 90 minutes). Each non-empty stratum gets an equal share of n, any shortfall going to the others, and its
ticks are drawn uniformly (a specimen in proportion to its ticks in the band, then one of those ticks), without
repeats. The ending selects which ticks are asked about and never enters a state: the state is X2's without any
TracerNet output, and the one question, `miss`, asks whether the specimen will still be unreceived when its window
closes. No threshold is set; the answers are scored as probabilities beside TracerNet and the local analytic refit on
the same ticks (src/pipeline/dispatch_eval.py).

This module reads the observed store only; a locked split's runs only with the test run the lock recorded.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from src.decide import facts as facts_mod
from src.decide import render
from src.decide.client import Item
from src.generator import config
from src.pipeline import intervals, store

TICK_MIN = 5
HORIZON_MIN = 7200
SCENARIOS = 100
OUTCOMES = ("on_time", "late", "never")
WINDOW_CLASSES = ("short", "long")
BANDS = ((0, 30), (30, 90), (90, math.inf))
BAND_NAMES = ("up to 30", "30 to 90", "over 90")


@dataclass(frozen=True)
class Tick:
    specimen_id: str
    run: int
    scenario_id: str
    tick_min: int
    window_min: int
    stratum: str
    weight: float           # ticks in the stratum's pool per tick drawn from it


def _specimens(store_dir: Path, split: str, scenarios: list[str]) -> dict:
    start, stop = intervals.clock_events(config.load()["policy"])
    con = store.open_observed_v2(store_dir)
    try:
        rows = con.execute("""
            with ev as (
                select e.specimen_id,
                       min(case when e.activity = ? then epoch(e.timestamp) end) / 60.0 as col,
                       min(case when e.activity = ? then epoch(e.timestamp) end) / 60.0 as rec
                from events e join specimens r on r.specimen_id = e.specimen_id
                where r.split = ? and r.role = 'scored' and list_contains(?, r.scenario_id)
                group by e.specimen_id)
            select s.specimen_id, s.run, s.scenario_id, s.window_min, ev.col, ev.rec
            from specimens s join ev on ev.specimen_id = s.specimen_id
            where s.split = ? and s.role = 'scored' and s.scored and ev.col is not null
              and list_contains(?, s.scenario_id)
            order by s.specimen_id""", [start, stop, split, scenarios, split, scenarios]).fetchall()
    finally:
        con.close()
    cols = list(zip(*rows)) if rows else [()] * 6
    ids, runs, scen, window, col, rec = cols
    window = np.asarray(window, dtype=float)
    receipt = np.array([np.nan if r is None else r for r in rec], dtype=float) - np.asarray(col, dtype=float)
    return {"ids": np.asarray(ids, dtype=object), "run": np.asarray(runs, dtype=np.int64),
            "scenario": np.asarray(scen, dtype=object), "window": window, "receipt": receipt}


def strata_of(window: np.ndarray, receipt: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per specimen: its ending (0 on time, 1 late, 2 never), its window class (0 short, 1 long) and its last tick."""
    never = np.isnan(receipt) | (receipt > HORIZON_MIN)
    ending = np.where(never, 2, np.where(receipt > window, 1, 0))
    wclass = (window > 120).astype(int)
    last = np.where(np.isnan(receipt), window, np.minimum(window, np.ceil(np.nan_to_num(receipt, nan=0.0)) - 1))
    return ending, wclass, np.floor(last / TICK_MIN) * TICK_MIN


def _band_ticks(last: np.ndarray, lo: float, hi: float) -> tuple[np.ndarray, np.ndarray]:
    """Per specimen, the first tick and the number of ticks in (lo, hi] up to its last tick (ticks 5, 10, ...)."""
    first = np.full(len(last), max(TICK_MIN, (math.floor(lo / TICK_MIN) + 1) * TICK_MIN), dtype=float)
    top = np.minimum(last, hi if math.isfinite(hi) else last)
    top = np.floor(top / TICK_MIN) * TICK_MIN
    n = np.maximum(0, (top - first) // TICK_MIN + 1).astype(np.int64)
    return first, n


def sample_ticks(store_dir: Path, split: str, n: int, seed: int, *, test_run=None,
                 scenarios: int = SCENARIOS) -> list[Tick]:
    store_dir = Path(store_dir)
    con = store.open_observed_v2(store_dir)
    try:
        if split in facts_mod.LOCKED_SPLITS:
            facts_mod.check_lock(con, store_dir, {r for (r,) in con.execute(
                "select run from runs where split = ?", [split]).fetchall()}, test_run)
        all_scen = [s for (s,) in con.execute("select distinct scenario_id from runs where split = ? and role = 'scored'",
                                              [split]).fetchall()]
    finally:
        con.close()
    chosen = sorted(all_scen, key=lambda s: (zlib.crc32(f"{seed}:{s}".encode()), s))[:scenarios]
    sp = _specimens(store_dir, split, sorted(chosen))
    ending, wclass, last = strata_of(sp["window"], sp["receipt"])
    pools = {}
    for o in range(3):
        for w in range(2):
            for b, (lo, hi) in enumerate(BANDS):
                first, k = _band_ticks(last, lo, hi)
                k = np.where((ending == o) & (wclass == w), k, 0)
                if k.sum() > 0:
                    pools[(o, w, b)] = (first, k)
    targets = _allocate(n, {key: int(k.sum()) for key, (_, k) in pools.items()})
    rng = np.random.default_rng(seed)
    out = []
    for key in sorted(pools):
        first, k = pools[key]
        want = targets[key]
        if want == 0:
            continue
        picked: set = set()
        while len(picked) < want:
            who = rng.choice(len(k), size=want - len(picked), p=k / k.sum())
            at = first[who] + TICK_MIN * np.floor(rng.random(len(who)) * k[who])
            picked |= set(zip(who.tolist(), at.astype(int).tolist()))
        o, w, b = key
        name = f"{OUTCOMES[o]}|{WINDOW_CLASSES[w]}|{BAND_NAMES[b]}"
        weight = float(k.sum()) / want
        for i, t in sorted(picked)[:want]:
            out.append(Tick(str(sp["ids"][i]), int(sp["run"][i]), str(sp["scenario"][i]), int(t),
                            int(sp["window"][i]), name, weight))
    return sorted(out, key=lambda x: (x.specimen_id, x.tick_min))


def _allocate(n: int, sizes: dict) -> dict:
    """An equal share of n per non-empty stratum, capped by its size, the remainder spread over the others."""
    left, out, open_ = n, {k: 0 for k in sizes}, {k for k, v in sizes.items() if v > 0}
    while left > 0 and open_:
        share = max(1, left // len(open_))
        for k in sorted(open_):
            take = min(share, sizes[k] - out[k], left)
            out[k] += take
            left -= take
        open_ = {k for k in open_ if out[k] < sizes[k]}
    return out


def build_items(ticks: list[Tick], facts_rows: list[dict], cfg: dict) -> list[Item]:
    budget = int(cfg["budgets"]["X3"])
    out = []
    for t, f in zip(ticks, facts_rows, strict=True):
        state = render.alert_state(f, None, budget=budget)
        out.append(Item(f"{t.specimen_id}@{t.tick_min}", state, render.x3_questions(f["left_min"]), meta={
            "specimen_id": t.specimen_id, "run": t.run, "scenario_id": t.scenario_id, "tick_min": t.tick_min,
            "window_min": t.window_min, "stratum": t.stratum, "weight": t.weight,
            "state_hash": render.state_hash(state)}))
    return out


def answers(outcomes, items: list[Item], result=None) -> list[dict]:
    rows = []
    for o, it in zip(outcomes, items, strict=True):
        a = (o.answers or {}).get("miss") or {}
        rows.append({"item_id": it.item_id, "request_hash": o.request_hash, "status": o.status, "reason": o.reason,
                     "resolved_model": o.resolved_model, "refusals": o.refusals,
                     "p_miss": float(a["p"]) if a.get("type") == "noul" else None, **it.meta})
    return rows
