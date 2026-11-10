"""The frozen library's scenarios as X1 reads them (docs/neural/spec.md §11.3: scenario labels and audit).

X1 audits the generated laboratory weeks themselves, so its input is the library. The library belongs to the
generation side, and the decision package (src/decide/) never opens it (tests/test_neural_isolation.py); this module
reads it and hands src/decide/critic.py one record per scenario: its configuration, the plausibility measures the
freeze recorded, and the planner's tags, which X1's agreement is measured against and which never enter a state.
"""

from __future__ import annotations

from pathlib import Path

from src.scenarios import freeze


def records(path: Path = freeze.LIBRARY_PATH) -> list[dict]:
    out = []
    for rec in freeze.read_library(Path(path)):
        meta = rec["meta"]
        brief = meta.get("brief") or {}
        out.append({"scenario_id": meta["scenario_id"], "scenario": rec["scenario"], "kpis": meta.get("kpis"),
                    "planner": {"failure_focus": brief.get("failure_focus"),
                                "edge_tags": sorted(brief.get("edge_tags") or [])}})
    if not out:
        raise FileNotFoundError(f"no frozen library at {path}")
    return out
