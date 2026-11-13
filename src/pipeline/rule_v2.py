"""The second decision rule, docs/neural/preregistration-v2.md §2 to §4, applied to one evaluation.

    python -m src.pipeline.rule_v2 results/neural/test_fresh/evaluation.json [--reported REPORTED.json]

It reads an evaluation written by src/pipeline/neural_eval.py and nothing else, so the verdict is a function of the
look's own record:

- P, the primary endpoint: at the 3-2-1 and the equal weights, the lower end of the 95% interval of TracerNet's capped
  mean warning minus each baseline's, at 6 alerts per shift, is above 0. The baselines are the five of spec §8.2; the
  analytic refits at TracerNet's weighting, the rules as they are.
- G1 (R3): Brier at 30 and 60 minutes of silence no worse than the better analytic refit's by more than 0.01.
- G2: AUROC for a miss among specimens still silent at 30 minutes at least every baseline's.
- G3: at each weighting, false alerts per alert sent no higher than each baseline's, at 6 alerts per shift.
- G4 (R2): never-received specimens alerted before the window closes not lower than the window rule's or either
  analytic refit's by more than 2 points (upper end of the 95% interval of the deficit), at both weightings.

G1 and G2 score the probability TracerNet reports. When that is its site-mapped probability, `reported` gives the
calibration and discrimination of that probability on the same look ({"calibration": {"30": {"brier": ...}, ...},
"discrimination": {"landmark_30": {"auroc": ...}}}); otherwise the evaluation's own TracerNet entries are used.
TracerNet becomes the primary model only if P and G1 to G4 all hold.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BUDGET = "6/shift"
WEIGHTINGS = ("3-2-1", "equal")
BASELINES = ("window_rule", "analytic_global", "analytic_local", "quantile_local", "quantile_global")
WEIGHTED = ("analytic_global", "analytic_local")
ANALYTIC = ("analytic_global", "analytic_local")
R2_BASELINES = ("window_rule", "analytic_global", "analytic_local")
BRIER_MARGIN = 0.01
LANDMARKS = ("30", "60")
AUROC_LANDMARK = "landmark_30"
LOSS_MARGIN = 0.02
TRACERNET = "tracernet"


def _op(ev: dict, method: str, weighting: str) -> dict:
    ops = ev["operating_points"][BUDGET][method]
    return ops[weighting] if method == TRACERNET or method in WEIGHTED else ops["none"]


def primary(ev: dict) -> dict:
    checks = []
    for w in WEIGHTINGS:
        diffs = ev["differences"][BUDGET][w]
        for b in BASELINES:
            d = diffs[b]["capped_mean_warning_min"]
            checks.append({"weighting": w, "baseline": b,
                           "tracernet_min": _op(ev, TRACERNET, w)["capped_mean_warning_min"]["point"],
                           "baseline_min": _op(ev, b, w)["capped_mean_warning_min"]["point"],
                           "difference_min": d["point"], "interval_low_min": d["low"], "interval_high_min": d["high"],
                           "share_ahead": d.get("share_ahead"), "held": d["low"] > 0})
    best = {w: max((c for c in checks if c["weighting"] == w), key=lambda c: c["baseline_min"])["baseline"]
            for w in WEIGHTINGS}
    return {"held": all(c["held"] for c in checks), "best_baseline": best, "checks": checks}


def _tracernet_block(ev: dict, reported: dict | None, key: str) -> dict:
    return (reported or {}).get(key) or ev[key][TRACERNET]


def g1(ev: dict, reported: dict | None = None) -> dict:
    mine = _tracernet_block(ev, reported, "calibration")
    checks = []
    for lm in LANDMARKS:
        better = min(ev["calibration"][a][lm]["brier"] for a in ANALYTIC)
        excess = mine[lm]["brier"] - better
        checks.append({"landmark_min": int(lm), "tracernet_brier": mine[lm]["brier"], "better_analytic_brier": better,
                       "excess": excess, "allowed_excess": BRIER_MARGIN, "held": excess <= BRIER_MARGIN})
    return {"held": all(c["held"] for c in checks), "checks": checks}


def g2(ev: dict, reported: dict | None = None) -> dict:
    mine = _tracernet_block(ev, reported, "discrimination")[AUROC_LANDMARK]["auroc"]
    checks = [{"baseline": b, "tracernet_auroc": mine, "baseline_auroc": ev["discrimination"][b][AUROC_LANDMARK]["auroc"],
               "held": mine >= ev["discrimination"][b][AUROC_LANDMARK]["auroc"]} for b in BASELINES]
    return {"held": all(c["held"] for c in checks), "checks": checks}


def _false_share(op: dict) -> float:
    alerts = op["alerts"]["point"]
    return op["false_alerts"]["point"] / alerts if alerts else 0.0


def g3(ev: dict) -> dict:
    checks = []
    for w in WEIGHTINGS:
        mine = _op(ev, TRACERNET, w)
        for b in BASELINES:
            theirs = _op(ev, b, w)
            checks.append({"weighting": w, "baseline": b, "tracernet_false_per_alert": _false_share(mine),
                           "baseline_false_per_alert": _false_share(theirs),
                           "tracernet_false_per_shift": mine["false_alerts_per_shift"]["point"],
                           "baseline_false_per_shift": theirs["false_alerts_per_shift"]["point"],
                           "held": _false_share(mine) <= _false_share(theirs)})
    return {"held": all(c["held"] for c in checks), "checks": checks}


def g4(ev: dict) -> dict:
    r2 = ev["decision_rule"]["R2"]
    checks = [c for c in r2["checks"] if c["weighting"] in WEIGHTINGS and c["baseline"] in R2_BASELINES]
    seen = {(c["weighting"], c["baseline"]) for c in checks}
    missing = [(w, b) for w in WEIGHTINGS for b in R2_BASELINES if (w, b) not in seen]
    if missing:
        raise ValueError(f"the evaluation's R2 lacks {missing}")
    if any(c["needs_deficit_below"] != LOSS_MARGIN for c in checks):
        raise ValueError("the evaluation's R2 margin is not 2 points")
    return {"held": all(c["held"] for c in checks), "checks": checks}


def verdict(ev: dict, reported: dict | None = None) -> dict:
    parts = {"P": primary(ev), "G1": g1(ev, reported), "G2": g2(ev, reported), "G3": g3(ev), "G4": g4(ev)}
    passed = all(p["held"] for p in parts.values())
    return {"set": ev.get("set"), "rule": "docs/neural/preregistration-v2.md", "budget": BUDGET,
            "probability_scored": "reported (site map)" if reported else "calibrated output",
            **parts, "held": {k: v["held"] for k, v in parts.items()}, "passed": passed,
            "primary_model": "TracerNet" if passed else "analytic model",
            "text": ("TracerNet becomes Tracer's primary risk model." if passed else
                     "TracerNet stays a challenger; the analytic model stays primary.")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Apply the second decision rule to an evaluation.")
    ap.add_argument("evaluation", type=Path)
    ap.add_argument("--reported", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    ev = json.loads(args.evaluation.read_text(encoding="utf-8"))
    reported = json.loads(args.reported.read_text(encoding="utf-8")) if args.reported else None
    v = verdict(ev, reported)
    text = json.dumps(v, indent=1, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(f"{ev.get('set')}: " + ", ".join(f"{k} {'held' if h else 'failed'}" for k, h in v["held"].items())
          + f"; {v['text']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
