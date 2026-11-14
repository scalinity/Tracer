"""Build docs/demo/results.json, the numbers the phone page's #warning section shows.

It copies TracerNet's results at 3-2-1 tier weights and 1, 2, 4 and 6 alerts a shift from the committed
evaluation of the fresh test set, results/neural/test_fresh/evaluation.json: minutes of warning per late
specimen (capped_mean_warning_min) and false alarms a shift (false_alerts_per_shift). It reads that committed
file only; it scores nothing. The output depends only on that file and looked.json, so tests/test_demo_results.py
rebuilds it and compares it byte for byte. Run: python scripts/build_demo_results.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVALUATION = ROOT / "results" / "neural" / "test_fresh" / "evaluation.json"
LOOKED = ROOT / "results" / "neural" / "test_fresh" / "looked.json"
OUT = ROOT / "docs" / "demo" / "results.json"

BUDGETS = (1, 2, 4, 6)
DEFAULT_BUDGET = 6
METHOD, WEIGHTS = "tracernet", "3-2-1"


def build() -> str:
    raw = EVALUATION.read_bytes()
    sha = json.loads(LOOKED.read_text(encoding="utf-8"))["sha256"]
    if hashlib.sha256(raw).hexdigest() != sha:
        raise SystemExit("evaluation.json is not the file looked.json recorded")
    ev = json.loads(raw)
    counts = ev["counts"]
    if counts["clusters"] != counts["tuning_groups"]:
        raise SystemExit("the evaluation's scenario count is ambiguous")
    rows = []
    for budget in BUDGETS:
        point = ev["operating_points"][f"{budget}/shift"][METHOD][WEIGHTS]
        rows.append({"alerts_per_shift": budget,
                     "warning_min": point["capped_mean_warning_min"]["point"],
                     "false_alarms_per_shift": point["false_alerts_per_shift"]["point"]})
    out = {
        "source": "results/neural/test_fresh/evaluation.json",
        "sha256": sha,
        "method": METHOD,
        "weights": WEIGHTS,
        "scenarios": counts["clusters"],
        "default_alerts_per_shift": DEFAULT_BUDGET,
        "budgets": rows,
    }
    return json.dumps(out, indent=1) + "\n"


if __name__ == "__main__":
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
