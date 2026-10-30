"""Load and validate the breach policy and the generator parameters.

Fails loudly when a policy names a state the graph does not have, or when its
clock_stop cannot be reached from its clock_start (docs/architecture.md §4.1).
config/config.yaml is checked against both: mix entries must name policy rows,
faults must name graph states, and the weights and rates must be well formed.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "config" / "breach_policy.yaml"
GRAPH_PATH = ROOT / "config" / "workflow_graph.yaml"
CONFIG_PATH = ROOT / "config" / "config.yaml"

SUM_TOLERANCE = 1e-9
LEGS = ("dispatch_wait", "tube_transit", "station_dwell", "hand_carry_transit", "accession_wait", "plating_wait")
FAULTS = ("none", "lost_in_tube", "stalled_at_station", "misrouted", "not_dispatched", "never_collected",
          "content_failure")
FAULTS_WITH_DELAY = ("stalled_at_station", "misrouted")


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


def check_sum(label: str, values: list[float]) -> None:
    total = sum(values)
    if abs(total - 1) > SUM_TOLERANCE:
        raise PolicyError(f"config.yaml: {label} must sum to 1 (within {SUM_TOLERANCE:g}), got {total!r}")


def check_source(label: str, group: dict) -> None:
    source = group.get("source")
    if not isinstance(source, str) or not source.strip():
        raise PolicyError(f"config.yaml {label}: missing source (write 'assumed' or a citation)")


def validate_config(cfg: dict, policy: dict, graph: dict) -> None:
    for key in ("run", "arrivals", "specimen_mix", "collecting_units", "priority", "route", "legs", "faults"):
        if key not in cfg:
            raise PolicyError(f"config.yaml: missing section {key}")
    for key in ("run", "arrivals", "specimen_mix", "collecting_units", "priority", "route"):
        check_source(key, cfg[key])

    run = cfg["run"]
    if not isinstance(run["days"], int) or run["days"] <= 0:
        raise PolicyError("config.yaml run: days must be a positive whole number")
    try:
        date.fromisoformat(str(run["start_date"]))
    except ValueError:
        raise PolicyError(f"config.yaml run: start_date {run['start_date']!r} is not an ISO date") from None

    rates = cfg["arrivals"]["rate_per_hour"]
    if len(rates) != 24:
        raise PolicyError(f"config.yaml arrivals: rate_per_hour needs 24 values, got {len(rates)}")
    if min(rates) < 0 or sum(rates) <= 0:
        raise PolicyError("config.yaml arrivals: rate_per_hour must be non-negative and not all zero")
    if cfg["arrivals"]["weekend_factor"] <= 0:
        raise PolicyError("config.yaml arrivals: weekend_factor must be positive")

    entries = cfg["specimen_mix"]["entries"]
    check_sum("specimen_mix weights", [e["weight"] for e in entries.values()])
    for name, entry in entries.items():
        if entry["weight"] <= 0:
            raise PolicyError(f"config.yaml specimen_mix {name}: weight must be positive")
        if not entry["policies"]:
            raise PolicyError(f"config.yaml specimen_mix {name}: names no policy")
        for pid in entry["policies"]:
            if pid not in policy["policies"]:
                raise PolicyError(f"config.yaml specimen_mix {name}: {pid!r} is not a policy in breach_policy.yaml")
        if len({policy["policies"][pid]["specimen_type"] for pid in entry["policies"]}) != 1:
            raise PolicyError(f"config.yaml specimen_mix {name}: policies span more than one specimen type")

    check_sum("collecting_units weights", list(cfg["collecting_units"]["weights"].values()))
    if not 0 <= cfg["priority"]["stat_fraction"] <= 1:
        raise PolicyError("config.yaml priority: stat_fraction must be between 0 and 1")
    check_sum("route probabilities", [cfg["route"]["tube_probability"], cfg["route"]["hand_carry_probability"]])

    if set(cfg["legs"]) != set(LEGS):
        raise PolicyError(f"config.yaml legs: expected {sorted(LEGS)}, got {sorted(cfg['legs'])}")
    for name, leg in cfg["legs"].items():
        check_source(f"legs {name}", leg)
        for field in ("median_min", "p90_min"):
            if leg[field] <= 0:
                raise PolicyError(f"config.yaml legs {name}: {field} must be positive, got {leg[field]!r}")
        if leg["p90_min"] <= leg["median_min"]:
            raise PolicyError(f"config.yaml legs {name}: p90_min must be above median_min")

    if set(cfg["faults"]) != set(FAULTS):
        raise PolicyError(f"config.yaml faults: expected fault modes {sorted(FAULTS)}, got {sorted(cfg['faults'])}")
    check_sum("fault rates", [f["rate"] for f in cfg["faults"].values()])
    for name, fault in cfg["faults"].items():
        check_source(f"faults {name}", fault)
        if fault["rate"] < 0:
            raise PolicyError(f"config.yaml faults {name}: rate must not be negative")
        for state in fault["states"]:
            if state not in graph:
                raise PolicyError(f"config.yaml faults {name}: {state!r} is not a workflow state")
        if name in FAULTS_WITH_DELAY:
            lo, hi = fault.get("delay_min", (0, 0))
            if not 0 < lo < hi:
                raise PolicyError(f"config.yaml faults {name}: delay_min needs [low, high] with 0 < low < high")


def policy_hash(policy: dict) -> str:
    """Hash of everything that defines the target variable. Artifacts carry it."""
    canonical = json.dumps(policy["policies"], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def config_hash(cfg: dict) -> str:
    """Hash of every generator parameter. It follows the values, not the comments."""
    canonical = json.dumps(cfg, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def load(policy_path: Path = POLICY_PATH, graph_path: Path = GRAPH_PATH, config_path: Path = CONFIG_PATH) -> dict:
    graph = load_yaml(graph_path)["states"]
    policy = load_yaml(policy_path)
    cfg = load_yaml(config_path)
    validate_graph(graph)
    validate_policy(policy, graph)
    validate_config(cfg, policy, graph)
    return {"graph": graph, "policy": policy, "policy_hash": policy_hash(policy),
            "config": cfg, "config_hash": config_hash(cfg)}


def resolve(policy: dict, specimen_type: str, tests: list[str]) -> tuple[str, dict]:
    """The governing policy for a specimen: the shortest window among its tests,
    ties broken by the more urgent tier."""
    rows = [(pid, p) for pid, p in policy["policies"].items()
            if p["specimen_type"] == specimen_type and p["test"] in tests]
    if not rows:
        raise PolicyError(f"no policy for {specimen_type!r} with tests {tests}")
    return min(rows, key=lambda r: (r[1]["window_min"], r[1]["urgency_tier"]))
