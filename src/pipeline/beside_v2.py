"""The tables reported beside the second rule's verdict (docs/neural/preregistration-v2.md §4), read from one evaluation.

    python -m src.pipeline.beside_v2 results/neural/test_fresh/evaluation.json [--out BESIDE.md]

None of these numbers decides anything; the verdict is src/pipeline/rule_v2.py's. Printed as markdown: R1's median
warning, R4, R1 and the capped mean at matched realized volume, the capped mean at the other budgets, the 9-3-1 weights,
AUROC by minutes of silence (TracerNet's site-mapped probability) and the archetype slices.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

NAMES = {"tracernet": "TracerNet", "window_rule": "Window rule", "analytic_global": "Analytic, global refit",
         "analytic_local": "Analytic, local refit", "quantile_local": "Quantile rule, local",
         "quantile_global": "Quantile rule, global"}
BASELINES = ("window_rule", "analytic_global", "analytic_local", "quantile_local", "quantile_global")
BUDGETS = ("1/shift", "2/shift", "4/shift", "6/shift", "5/100", "10/100", "30/100")


def _op(ev: dict, budget: str, method: str, weighting: str) -> dict:
    o = ev["operating_points"][budget][method]
    return o[weighting] if weighting in o else o["none"]


def _diff_rows(block: dict, metric: str, weightings: tuple[str, ...]) -> list[str]:
    return [f"| {w} | {NAMES[b]} | {c['point']:.1f} | {c['low']:.1f} to {c['high']:.1f} |"
            for w in weightings for b in BASELINES if b in block[w] for c in [block[w][b][metric]]]


def tables(ev: dict) -> str:
    out = ["### R1's median warning at 6 alerts per shift (all late specimens, a miss counts as 0)", "",
           "| Method | 3-2-1 | Equal |", "|---|---|---|"]
    for m in ("tracernet",) + BASELINES:
        cells = [f"{_op(ev, '6/shift', m, w)['median_warning_all_breaches_min']['point']:.1f}" for w in ("3-2-1", "equal")]
        out.append(f"| {NAMES[m]} | " + " | ".join(cells) + " |")

    out += ["", "### R4: realized alerts per shift at the 6-per-shift budget (tolerance 10%)", "",
            "| Weights | Baseline | TracerNet | Baseline | Relative difference | Within 10% |", "|---|---|---|---|---|---|"]
    for c in ev["decision_rule"]["R4"]["checks"]:
        out.append(f"| {c['weighting']} | {NAMES[c['baseline']]} | {c['tracernet_per_shift']:.2f} | "
                   f"{c['baseline_per_shift']:.2f} | {c['relative_difference']:+.0%} | {'yes' if c['held'] else 'no'} |")

    mv = ev["matched_volume"]
    head = ["| Weights | Baseline | Difference (min) | 95% interval |", "|---|---|---|---|"]
    out += ["", f"### At matched realized volume ({mv['budget']}; {mv['note']})", "",
            "TracerNet minus each baseline. R1's median warning:", ""] + head
    out += _diff_rows(mv["differences"], "median_warning_all_breaches_min", ("3-2-1", "equal"))
    out += ["", "Capped mean warning:", ""] + head
    out += _diff_rows(mv["differences"], "capped_mean_warning_min", ("3-2-1", "equal"))

    out += ["", "### Capped mean warning at other budgets (TracerNet minus the closest baseline)", "",
            "| Budget | Weights | TracerNet (min) | Closest baseline | Its value (min) | Difference | 95% interval |",
            "|---|---|---|---|---|---|---|"]
    for budget in BUDGETS:
        for w in ("3-2-1", "equal"):
            d = ev["differences"][budget][w]
            b = min((k for k in d if k in BASELINES), key=lambda k: d[k]["capped_mean_warning_min"]["point"])
            c = d[b]["capped_mean_warning_min"]
            out.append(f"| {budget} | {w} | {_op(ev, budget, 'tracernet', w)['capped_mean_warning_min']['point']:.1f} | "
                       f"{NAMES[b]} | {_op(ev, budget, b, w)['capped_mean_warning_min']['point']:.1f} | "
                       f"{c['point']:.1f} | {c['low']:.1f} to {c['high']:.1f} |")

    out += ["", "### The 9-3-1 weights at 6 alerts per shift (TracerNet minus each baseline, capped mean)", ""] + head
    out += _diff_rows(ev["differences"]["6/shift"], "capped_mean_warning_min", ("9-3-1",))

    disc = ev["discrimination"]
    out += ["", "### AUROC by minutes of silence (TracerNet's reported, site-mapped probability)", "",
            "| Landmark | TracerNet | Best baseline | Its AUROC |", "|---|---|---|---|"]
    for lm in ("landmark_15", "landmark_30", "landmark_60", "landmark_90", "pooled"):
        best = max(BASELINES, key=lambda b: disc[b][lm]["auroc"])
        label = "pooled" if lm == "pooled" else lm.replace("landmark_", "") + " min"
        out.append(f"| {label} | {disc['tracernet_site'][lm]['auroc']:.3f} | {NAMES[best]} | {disc[best][lm]['auroc']:.3f} |")

    out += ["", "### Slices: capped mean warning by laboratory archetype, 3-2-1 weights, 6 alerts per shift (points)", "",
            "| Archetype | TracerNet | Best baseline | Its value |", "|---|---|---|---|"]
    for arch, s in ev["slices"]["archetype"].items():
        value = {b: s[b].get("3-2-1", s[b].get("none"))["capped_mean_warning_min"] for b in BASELINES}
        best = max(value, key=value.get)
        out.append(f"| {arch.replace('_', ' ')} | {s['tracernet']['3-2-1']['capped_mean_warning_min']:.1f} | "
                   f"{NAMES[best]} | {value[best]:.1f} |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Print the tables reported beside the second rule's verdict.")
    ap.add_argument("evaluation", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    text = tables(json.loads(args.evaluation.read_text(encoding="utf-8")))
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
