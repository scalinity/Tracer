"""Load and validate the breach policy against the workflow graph.

Fails loudly when a policy names a state the graph does not have, or when its
clock_stop cannot be reached from its clock_start (docs/architecture.md §4.1).
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "config" / "breach_policy.yaml"
GRAPH_PATH = ROOT / "config" / "workflow_graph.yaml"


class PolicyError(ValueError):
    pass


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def reachable(graph: dict, start: str) -> set[str]:
    seen, queue = {start}, deque([start])
    while queue:
        for nxt in graph[queue.popleft()]["next"]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def validate_graph(graph: dict) -> None:
    for name, state in graph.items():
        for nxt in state["next"]:
            if nxt not in graph:
                raise PolicyError(f"workflow graph: {name} -> {nxt}, but {nxt} is not a state")


def validate_policy(policy: dict, graph: dict) -> None:
    for pid, p in policy["policies"].items():
        for key in ("clock_start", "clock_stop"):
            if p[key] not in graph:
                raise PolicyError(f"policy {pid}: {key} {p[key]!r} is not a workflow state")
        if p["clock_stop"] == p["clock_start"] or p["clock_stop"] not in reachable(graph, p["clock_start"]):
            raise PolicyError(f"policy {pid}: clock_stop {p['clock_stop']!r} is not reachable "
                              f"from clock_start {p['clock_start']!r}")
        if not graph[p["clock_stop"]]["scan"]:
            raise PolicyError(f"policy {pid}: clock_stop {p['clock_stop']!r} produces no scan, "
                              "so the clock could never be seen to stop")
        if p["window_min"] <= 0:
            raise PolicyError(f"policy {pid}: window_min must be positive")
        if p["urgency_tier"] not in (1, 2, 3):
            raise PolicyError(f"policy {pid}: urgency_tier must be 1, 2 or 3")


def policy_hash(policy: dict) -> str:
    """Hash of everything that defines the target variable. Artifacts carry it."""
    canonical = json.dumps(policy["policies"], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def load(policy_path: Path = POLICY_PATH, graph_path: Path = GRAPH_PATH) -> dict:
    graph = load_yaml(graph_path)["states"]
    policy = load_yaml(policy_path)
    validate_graph(graph)
    validate_policy(policy, graph)
    return {"graph": graph, "policy": policy, "policy_hash": policy_hash(policy)}


def resolve(policy: dict, specimen_type: str, tests: list[str]) -> tuple[str, dict]:
    """The governing policy for a specimen: the shortest window among its tests,
    ties broken by the more urgent tier."""
    rows = [(pid, p) for pid, p in policy["policies"].items()
            if p["specimen_type"] == specimen_type and p["test"] in tests]
    if not rows:
        raise PolicyError(f"no policy for {specimen_type!r} with tests {tests}")
    return min(rows, key=lambda r: (r[1]["window_min"], r[1]["urgency_tier"]))
