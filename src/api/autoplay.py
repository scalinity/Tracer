"""The data file for the self-playing 3D demo, docs/demo/play/ (the Nov 13 rework).

    python -m src.api.autoplay        # writes docs/demo/play/scenes.json

The page loops through three held-out specimens, one on time, one stalled at the laboratory's tube station and
one lost in the tube, and shows when each method alerts. Everything comes from the two committed demo files:
the timeline, the true path, the analytic model's risk and location belief from docs/demo/bundle.json, and
TracerNet's risk and every method's alert minute at the headline budget (6 alerts per shift, 3-2-1 weights) from
docs/demo/neural.json. Nothing is recomputed, and the page calls no API.

The three specimens are picked by hand to show the three situations, not to favor a method: in the stalled one
TracerNet alerts last of the three. They are illustrations, not a scorecard; results/neural/REPORT.md is that.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "docs" / "demo" / "bundle.json"
NEURAL = ROOT / "docs" / "demo" / "neural.json"
OUT = ROOT / "docs" / "demo" / "play" / "scenes.json"

# (specimen id, kind, title on screen)
SCENES = [
    ("SP-r24-000046", "on_time", "On time"),
    ("SP-r27-000368", "stalled", "Stalled"),
    ("SP-r25-000396", "lost", "Lost"),
]

# The bundle's seven places folded into the five stops the scene draws.
STOPS = ["unit", "tube", "station", "lab", "lost"]
FOLD = {"at_unit": "unit", "hand_carry": "unit", "in_tube": "tube", "at_station": "station",
        "stalled_at_station": "station", "misrouted": "lost", "lost": "lost", "at_receiving_bench": "lab"}

TYPE_WORDS = {"tissue": "tissue", "BAL, room temp": "lung wash", "urine, unpreserved, room temp": "urine"}
UNIT_WORDS = {"ed": "Emergency department", "icu": "ICU", "med_surg": "Med-surg ward",
              "periop": "Operating rooms", "clinic": "Clinic"}


def _fold_places(row: list[float], order: list[str]) -> list[float]:
    out = dict.fromkeys(STOPS, 0.0)
    for name, p in zip(order, row):
        out[FOLD[name]] += p
    return [round(out[s], 5) for s in STOPS]


def build(bundle: dict, neural: dict) -> dict:
    if bundle["tick_min"] != neural["tick_min"]:
        raise ValueError("the two demo files use different tick lengths")
    hi = neural["headline_index"]
    if hi != bundle["headline_index"]:
        raise ValueError("the two demo files disagree on the headline budget")
    by_id = {s["id"]: s for s in bundle["specimens"]}
    nn_by_id = {s["id"]: s for s in neural["specimens"]}
    scenes = []
    for sid, kind, title in SCENES:
        s, n = by_id[sid], nn_by_id[sid]
        if len(s["risk"]) != len(n["p_miss"]):
            raise ValueError(f"{sid}: the analytic and TracerNet series have different lengths")
        a = n["alerts"]
        scenes.append({
            "id": sid, "kind": kind, "title": title,
            "specimen": TYPE_WORDS.get(s["type"], s["type"]), "unit": UNIT_WORDS.get(s["unit"], s["unit"]),
            "priority": s["priority"], "window_min": s["window_min"], "received_min": s["received_min"],
            "breach": s["truth"]["breach"], "fault": s["truth"]["fault"],
            "truth": [[FOLD[name], start, end, name] for name, start, end in s["truth"]["segments"]],
            "risk": {"tracer": [round(x, 5) for x in s["risk"]], "tracernet": n["p_miss"]},
            "places": [_fold_places(row, bundle["place_order"]) for row in s["places"]],
            "alerts": {"rule": a["window_rule"][hi], "tracer": a["analytic_local"]["3-2-1"][hi],
                       "tracernet": a["tracernet"]["3-2-1"][hi]},
        })
    return {
        "notice": "Synthetic data, no clinical review.",
        "tick_min": bundle["tick_min"],
        "budget": f"{neural['grid_budgets'][hi]:g} alerts per 8-hour shift",
        "stops": STOPS,
        "scenes": scenes,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    data = build(json.loads(BUNDLE.read_text()), json.loads(NEURAL.read_text()))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=1) + "\n")
    print(f"wrote {args.out.relative_to(ROOT)}: {', '.join(s['id'] for s in data['scenes'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
