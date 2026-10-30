"""Do the fitted distributions recover what the generator planted? (plan task M3)

    python -m src.pipeline.recovery

The planted distribution is the generator's own: a large run of briefs from config/config.yaml, with a
seed no fit run uses. The truth store is never opened. The fitted distribution is the pooled mixture in
results/interval_params.json. Writes results/parameter-recovery.json and results/parameter-recovery.svg.

Two things are held to sampling error. Survival at 30 minutes and later, where alerts operate: the gap
must be within three binomial standard errors for the fit's sample size. And the never-arrive share.
The body of the distribution is reported but not held to that bar: transit is a sum of legs and a
single lognormal only approximates its shape there, which does not move a risk that is read at 30
minutes and later.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.special import ndtr

from src.generator import briefs as briefs_mod
from src.pipeline import intervals

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "results"
PLANTED_SEED, PLANTED_RUN, PLANTED_N = 999, 0, 400_000
CHECKPOINTS = (30, 45, 60, 90, 120, 180)
GRID = np.arange(1, 241)
SE_BAR = 3.0


def planted_minutes() -> np.ndarray:
    """Minutes from collection to receipt for each collected specimen; infinity where it never arrives."""
    out = [b["targets"]["receipt_after_min"] if b["targets"]["receipt_after_min"] is not None else math.inf
           for b in briefs_mod.make_briefs(PLANTED_SEED, PLANTED_RUN, PLANTED_N) if b["planted_fault"] != "never_collected"]
    return np.array(out, dtype=float)


def fitted_survival(pooled: dict, x: np.ndarray) -> np.ndarray:
    w = pooled["weights"]
    sf = lambda mu, s: 1 - ndtr((np.log(x) - mu) / s)
    return w["normal"] * sf(pooled["mu"], pooled["sigma"]) + w["stalled"] * sf(pooled["stalled_mu"], pooled["stalled_sigma"]) + w["never"]


def recover(params: dict) -> dict:
    pooled, n = params["pooled"], params["pooled"]["n_started"]
    planted = planted_minutes()
    true_s = np.array([(planted > x).mean() for x in GRID])
    fit_s = fitted_survival(pooled, GRID.astype(float))
    gap = fit_s - true_s
    points = []
    for x in CHECKPOINTS:
        t, f = float(true_s[x - 1]), float(fit_s[x - 1])
        se = math.sqrt(t * (1 - t) / n)
        points.append({"minutes": x, "planted": round(t, 4), "fitted": round(f, 4), "gap": round(f - t, 4),
                       "standard_errors": round((f - t) / se, 2), "within_bar": abs(f - t) <= SE_BAR * se})
    planted_never = float(np.isinf(planted).mean())
    se_never = math.sqrt(planted_never * (1 - planted_never) / n)
    body = int(np.abs(gap[:29]).argmax()) + 1
    return {
        "planted_run": f"seed {PLANTED_SEED}, run {PLANTED_RUN}, {len(planted)} specimens",
        "fit_size": n,
        "survival_at": points,
        "never_arrive": {"planted": round(planted_never, 4), "fitted": round(pooled["p_never"], 4),
                         "standard_errors": round((pooled["p_never"] - planted_never) / se_never, 2),
                         "within_bar": abs(pooled["p_never"] - planted_never) <= SE_BAR * se_never},
        "body_before_30_min": {"largest_gap": round(float(gap[:29][body - 1]), 4), "at_minutes": body},
        "grid": {"minutes": GRID.tolist(), "planted": np.round(true_s, 5).tolist(), "fitted": np.round(fit_s, 5).tolist()},
    }


def svg(result: dict) -> str:
    """Survival of collection-to-receipt time on a log scale: planted against fitted."""
    w, h, left, right, top, bottom = 640, 380, 64, 20, 28, 48
    xs, lo = result["grid"]["minutes"], 0.01
    px = lambda m: left + (m - 1) / 239 * (w - left - right)
    py = lambda s: top + (math.log10(1) - math.log10(max(s, lo))) / (-math.log10(lo)) * (h - top - bottom)
    line = lambda ys, color, dash="": (f'<polyline fill="none" stroke="{color}" stroke-width="2" {dash} points="' +
                                       " ".join(f"{px(m):.1f},{py(s):.1f}" for m, s in zip(xs, ys)) + '"/>')
    grid = "".join(f'<line x1="{left}" x2="{w - right}" y1="{py(s):.1f}" y2="{py(s):.1f}" stroke="#ddd"/>'
                   f'<text x="{left - 8}" y="{py(s) + 4:.1f}" text-anchor="end" font-size="11" fill="#555">{s:g}</text>'
                   for s in (0.01, 0.1, 1))
    ticks = "".join(f'<line x1="{px(m):.1f}" x2="{px(m):.1f}" y1="{top}" y2="{h - bottom}" stroke="#eee"/>'
                    f'<text x="{px(m):.1f}" y="{h - bottom + 16}" text-anchor="middle" font-size="11" fill="#555">{m}</text>'
                    for m in (1, 30, 60, 90, 120, 150, 180, 210, 240))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'font-family="system-ui, sans-serif"><rect width="{w}" height="{h}" fill="#fff"/>'
            f'<text x="{left}" y="18" font-size="13" font-weight="600" fill="#222">Chance a specimen is still not received, by minutes since collection</text>'
            f'{grid}{ticks}{line(result["grid"]["planted"], "#1b6ca8")}{line(result["grid"]["fitted"], "#d9541e", "stroke-dasharray=\"6 4\"")}'
            f'<line x1="{px(30):.1f}" x2="{px(30):.1f}" y1="{top}" y2="{h - bottom}" stroke="#999" stroke-dasharray="2 3"/>'
            f'<text x="{px(30) + 4:.1f}" y="{top + 12}" font-size="10" fill="#777">alerts act from here</text>'
            f'<text x="{(left + w - right) / 2:.0f}" y="{h - 10}" text-anchor="middle" font-size="12" fill="#444">minutes since collection</text>'
            f'<line x1="{w - 190}" x2="{w - 160}" y1="{top + 24}" y2="{top + 24}" stroke="#1b6ca8" stroke-width="2"/>'
            f'<text x="{w - 154}" y="{top + 28}" font-size="11" fill="#333">planted by the generator</text>'
            f'<line x1="{w - 190}" x2="{w - 160}" y1="{top + 42}" y2="{top + 42}" stroke="#d9541e" stroke-width="2" stroke-dasharray="6 4"/>'
            f'<text x="{w - 154}" y="{top + 46}" font-size="11" fill="#333">fitted from the data</text></svg>\n')


def table(r: dict) -> str:
    lines = [f"planted: {r['planted_run']}; fitted from {r['fit_size']} specimens", "",
             "minutes   planted  fitted    gap   standard errors"]
    lines += [f"{p['minutes']:7d}   {p['planted']:.4f}  {p['fitted']:.4f}  {p['gap']:+.4f}  {p['standard_errors']:+.2f}"
              f"{'' if p['within_bar'] else '   OUTSIDE'}" for p in r["survival_at"]]
    n = r["never_arrive"]
    lines += ["", f"never arrive: planted {n['planted']:.2%}, fitted {n['fitted']:.2%} ({n['standard_errors']:+.2f} standard errors)",
              f"body before 30 min: largest gap {r['body_before_30_min']['largest_gap']:+.4f} at "
              f"{r['body_before_30_min']['at_minutes']} min (not held to the bar)"]
    return "\n".join(lines)


def main() -> int:
    params = json.loads(intervals.PARAMS_PATH.read_text(encoding="utf-8"))
    result = recover(params)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "parameter-recovery.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    (RESULTS / "parameter-recovery.svg").write_text(svg(result), encoding="utf-8")
    print(table(result))
    print("\nwrote results/parameter-recovery.json and results/parameter-recovery.svg")
    return 0 if all(p["within_bar"] for p in result["survival_at"]) and result["never_arrive"]["within_bar"] else 1


if __name__ == "__main__":
    sys.exit(main())
