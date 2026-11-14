"""Build docs/demo/game.json, the slim file Beat Tracer reads instead of bundle.json (3.2 MB) and neural.json (2.5 MB).

One row per held-out specimen in bundle.json, with only what the game reads: the five fields gameRounds draws
rounds from (id, run, collected_at, window_min, received_min), the plain name a card shows, and TracerNet's
stored alert minute at the headline budget and 3-2-1 weights (neural.json specimens[].headline.alert_min), which
is Tracer's play. Every specimen is kept, so gameRounds draws the same rounds from this file as from the bundle
for any seed; tests/test_demo_game.py checks that and rebuilds the file byte for byte.
Run: python scripts/build_demo_game.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "docs" / "demo" / "bundle.json"
NEURAL = ROOT / "docs" / "demo" / "neural.json"
OUT = ROOT / "docs" / "demo" / "game.json"
FIELDS = ["id", "run", "collected_at", "window_min", "received_min", "name", "tracer_alert_min"]


def plain_name(specimen_type: str) -> str:
    """The name on a card, in words a visitor knows."""
    if specimen_type.startswith("urine"):
        return "Urine"
    if specimen_type.startswith("BAL"):
        return "Lung wash"
    if specimen_type.startswith("tissue"):
        return "Tissue"
    if re.search(r"swab", specimen_type, re.I):
        return "Swab"
    return "Body fluid"


def build() -> str:
    bundle = json.loads(BUNDLE.read_text(encoding="utf-8"))
    neural = json.loads(NEURAL.read_text(encoding="utf-8"))
    if neural["grid_budgets"][neural["headline_index"]] != bundle["grid"][bundle["headline_index"]]["budget_per_shift"]:
        raise SystemExit("TracerNet's headline budget is not the bundle's")
    alert = {s["id"]: s["headline"]["alert_min"] for s in neural["specimens"]}
    rows = [[s["id"], s["run"], s["collected_at"], s["window_min"], s["received_min"], plain_name(s["type"]), alert.get(s["id"])]
            for s in sorted(bundle["specimens"], key=lambda s: s["id"])]
    out = {
        "about": "Beat Tracer's specimens: synthetic data, no clinical review. Built by scripts/build_demo_game.py "
                 "from bundle.json and neural.json.",
        "alerts_per_shift": neural["grid_budgets"][neural["headline_index"]],
        "fields": FIELDS,
        "rows": rows,
    }
    return json.dumps(out, separators=(",", ":")).replace("],[", "],\n[") + "\n"


if __name__ == "__main__":
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
