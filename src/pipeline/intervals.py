"""Fit the collection-to-receipt time distributions (docs/architecture.md §5.1).

    python -m src.pipeline.intervals

Reads only observed.duckdb, the fit runs only (the held-out runs are never touched). The interval runs
from the policy's clock_start to its clock_stop, read from the policy and not from a stage name here.

Model: a specimen that arrives takes a normal time, a lognormal, or with a small probability a stalled
time, a longer lognormal; some specimens never arrive. The stalled lognormal, its weight and the
never-arrive share are fitted once over all specimens, because late completions and losses are too
rare to estimate per group. The normal lognormal is fitted per group (specimen type, six-hour block of
the day, priority) by maximum likelihood with those pooled parts held fixed. A group with fewer than
MIN_GROUP received specimens takes the fit of its parent: drop the hour block, then the priority, then
the specimen type.

Writes results/interval_params.json carrying the policy hash and the source file's SHA-256; a rerun
gives identical numbers.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy import optimize, special

from src.generator import config
from src.pipeline import store

ROOT = Path(__file__).resolve().parents[2]
PARAMS_PATH = ROOT / "results" / "interval_params.json"
MIN_GROUP = 30
BLOCK_HOURS = 6
BLOCKS = [f"{h:02d}-{h + BLOCK_HOURS - 1:02d}" for h in range(0, 24, BLOCK_HOURS)]
Z90 = 1.2815515655446004
ROUND = 6


def clock_events(policy: dict) -> tuple[str, str]:
    """The one (start, stop) pair every policy row uses. Fails if rows disagree or the stop is not scanned."""
    pairs = {(p["clock_start"], p["clock_stop"]) for p in policy["policies"].values()}
    if len(pairs) != 1:
        raise RuntimeError(f"the policy has {len(pairs)} different clocks; this fit handles one: {sorted(pairs)}")
    return next(iter(pairs))


def load_intervals(con, start: str, stop: str) -> list[tuple[str, int, str, float | None]]:
    """(specimen type, hour block, priority, minutes from start to stop or None) for every fit specimen
    whose start event was scanned."""
    rows = con.execute("""
        select s.specimen_type, s.priority, hour(a.timestamp), epoch(b.timestamp - a.timestamp) / 60.0
        from specimens s
        join events a on a.specimen_id = s.specimen_id and a.activity = ?
        left join events b on b.specimen_id = s.specimen_id and b.activity = ?
        where s.split = 'fit' order by s.specimen_id""", [start, stop]).fetchall()
    return [(t, h // BLOCK_HOURS, p, m) for t, p, h, m in rows]


def _logpdf(y: np.ndarray, mu: float, sigma: float) -> np.ndarray:
    return -0.5 * ((y - mu) / sigma) ** 2 - math.log(sigma) - 0.5 * math.log(2 * math.pi)


def _mixture_nll(y: np.ndarray, mu1: float, s1: float, mu2: float, s2: float, w2: float) -> float:
    a = math.log1p(-w2) + _logpdf(y, mu1, s1)
    b = math.log(w2) + _logpdf(y, mu2, s2)
    return -float(np.sum(np.logaddexp(a, b)))


def fit_pooled(minutes: np.ndarray) -> dict:
    """Two-component lognormal mixture, by maximum likelihood on the log times. The stalled component
    is constrained to sit above the normal one, and three starting gaps are tried so the answer does
    not hinge on one start."""
    y = np.log(np.maximum(minutes, 1.0))

    def unpack(t):
        mu1, s1 = t[0], math.exp(t[1])
        return mu1, s1, mu1 + math.exp(t[2]), math.exp(t[3]), float(special.expit(t[4]))

    best = None
    for gap in (1.0, 1.5, 2.0):
        x0 = [np.median(y), math.log(0.5), math.log(gap), math.log(0.5), special.logit(0.03)]
        res = optimize.minimize(lambda t: _mixture_nll(y, *unpack(t)), x0, method="L-BFGS-B",
                                bounds=[(None, None), (-3, 1), (-2, 3), (-3, 1), (-8, 0)])
        if best is None or res.fun < best.fun:
            best = res
    mu1, s1, mu2, s2, w2 = unpack(best.x)
    return {"mu": mu1, "sigma": s1, "stalled_mu": mu2, "stalled_sigma": s2, "w_stalled": w2, "nll": float(best.fun)}


def fit_normal(minutes: np.ndarray, pooled: dict) -> tuple[float, float]:
    """The group's normal lognormal, with the pooled stalled component and weight held fixed."""
    y = np.log(np.maximum(minutes, 1.0))

    def nll(t):
        return _mixture_nll(y, t[0], math.exp(t[1]), pooled["stalled_mu"], pooled["stalled_sigma"], pooled["w_stalled"])

    res = optimize.minimize(nll, [pooled["mu"], math.log(pooled["sigma"])], method="L-BFGS-B",
                            bounds=[(None, None), (-3, 1)])
    return float(res.x[0]), math.exp(float(res.x[1]))


def parent_chain(t: str, block: int, priority: str) -> list[tuple]:
    """The group and its fallbacks, most specific first."""
    return [(t, block, priority), (t, None, priority), (t, None, None), (None, None, None)]


def key_name(key: tuple) -> str:
    t, block, priority = key
    parts = [t if t is not None else "all", BLOCKS[block] if block is not None else "any hour",
             priority if priority is not None else "any priority"]
    return " | ".join(parts)


def members(rows, key) -> list[float]:
    t, block, priority = key
    return [m for rt, rb, rp, m in rows
            if m is not None and (t is None or rt == t) and (block is None or rb == block)
            and (priority is None or rp == priority)]


def fit_all(rows, pooled: dict | None = None) -> dict:
    """Pooled parts, then the normal lognormal for every (type, block, priority) group that exists."""
    arrived = np.array([m for *_, m in rows if m is not None])
    started = len(rows)
    pooled = pooled or fit_pooled(arrived)
    never = started - len(arrived)
    p_never = never / started
    w2 = pooled["w_stalled"]
    weights = {"normal": (1 - p_never) * (1 - w2), "stalled": (1 - p_never) * w2, "never": p_never}

    cache: dict[tuple, tuple[float, float, int]] = {}

    def level(key):
        if key not in cache:
            x = np.array(members(rows, key))
            cache[key] = (*fit_normal(x, pooled), len(x)) if len(x) >= MIN_GROUP else None
        return cache[key]

    groups = {}
    for key in sorted({(t, b, p) for t, b, p, _ in rows}, key=lambda k: (k[0], k[1], k[2])):
        n = len(members(rows, key))
        for parent in parent_chain(*key):
            fit = level(parent) if parent != (None, None, None) else None
            if parent == (None, None, None):  # the root is the pooled fit itself
                fit = (pooled["mu"], pooled["sigma"], len(arrived))
            if fit is not None:
                mu, sigma, _ = fit
                groups[key_name(key)] = {"n": n, "fitted_from": key_name(parent), "mu": mu, "sigma": sigma,
                                         "median_min": math.exp(mu), "p90_min": math.exp(mu + Z90 * sigma)}
                break
    return {"pooled": {**{k: v for k, v in pooled.items() if k != "nll"}, "p_never": p_never, "weights": weights,
                       "n_started": started, "n_arrived": len(arrived), "n_never": never},
            "groups": groups}


def rounded(value):
    if isinstance(value, float):
        return round(value, ROUND)
    if isinstance(value, dict):
        return {k: rounded(v) for k, v in value.items()}
    return value


def build(store_dir: Path = store.STORE_DIR) -> dict:
    loaded = config.load()
    start, stop = clock_events(loaded["policy"])
    con = store.open_observed(store_dir)
    rows = load_intervals(con, start, stop)
    fitted = fit_all(rows)
    return rounded({
        "version": 1,
        "interval": f"{start} to {stop}, in minutes",
        "clock_start": start, "clock_stop": stop,
        "policy_hash": loaded["policy_hash"],
        "source_sha256": store.source_sha256(),
        "fit_runs": f"{min(set(range(30)) - set(store.TEST_RUNS))} to {max(set(range(30)) - set(store.TEST_RUNS))}",
        "min_group_size": MIN_GROUP,
        **fitted})


def table(params: dict) -> str:
    p = params["pooled"]
    lines = [f"interval {params['interval']}; policy {params['policy_hash']}; fit runs {params['fit_runs']}",
             f"pooled: stalled lognormal median {math.exp(p['stalled_mu']):.0f} min (sigma {p['stalled_sigma']:.2f}), "
             f"{p['w_stalled']:.1%} of arrivals; {p['n_never']} of {p['n_started']} never arrive ({p['p_never']:.2%})",
             "", f"{'group':64s} {'n':>5s}  {'median':>6s} {'p90':>6s}  fitted from"]
    for name, g in params["groups"].items():
        own = "itself" if g["fitted_from"] == name else g["fitted_from"]
        lines.append(f"{name:64s} {g['n']:5d}  {g['median_min']:6.1f} {g['p90_min']:6.1f}  {own}")
    return "\n".join(lines)


def main() -> int:
    params = build()
    PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PARAMS_PATH.write_text(json.dumps(params, indent=2) + "\n", encoding="utf-8")
    print(table(params))
    shown = PARAMS_PATH.relative_to(ROOT) if PARAMS_PATH.is_relative_to(ROOT) else PARAMS_PATH
    print(f"\nwrote {shown}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        sys.exit(f"error: {e}")
