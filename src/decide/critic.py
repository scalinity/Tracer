"""X1: scenario labels and audit (docs/neural/spec.md §11.3), after each freeze.

    items = critic.build_items(records, cfg)           # labels for every scenario, plus the negative controls
    rows = critic.answers(outcomes, items)

`records` come from the frozen library through src/scenarios/audit.py, which reads it; this package never opens the
library file. A record is {scenario_id, scenario, kpis, planner: {failure_focus, edge_tags}}.

Two kinds of item:
- label (every scenario): the state is render.scenario_state with the scenario's plausibility measures; the questions
  are `plausibility` (a 5-level score), `dominant_failure` (the 11 failure focuses and `other`) and one
  `edge_<tag>` yes-no question for each edge tag its brief requested, worded with the tag's sentence (Appendix C).
  The planner's own tags never enter the state; they are what agreement is measured against.
- control: CONTROLS_EACH scenarios corrupted by code and the same number left as they are, drawn in the seed's order,
  asked `plausibility` and `dominant_failure` with the state rendered without plausibility measures (a corrupted
  configuration was never simulated, so both arms leave them out). The corruptions, cycled: staffing absurd for the
  volume, impossible routes (90th percentiles below their medians, transit far beyond any campus), contradictory
  schedules (a station emptied every two days, couriers with no rounds at all while units still send by courier),
  and rates ten times their schema bounds.

Plausibility serves as a soft filter only if its AUROC on the controls is at least 0.80; then scenarios whose most
likely level is one of the two lowest go to a review list and nothing is deleted (src/pipeline/dispatch_eval.py).
"""

from __future__ import annotations

import copy
import json
import zlib
from pathlib import Path

from src.decide import render
from src.decide.client import Item

CORRUPTIONS = ("staffing_absurd", "impossible_routes", "contradictory_schedules", "rates_10x")
SCHEMA_PATH = render.SCHEMA_PATH


def _bounds(path: Path = SCHEMA_PATH) -> dict:
    """The schema's maximum for each rate the controls corrupt."""
    s = json.loads(Path(path).read_text(encoding="utf-8"))["properties"]

    def top(*keys):
        node = s
        for k in keys:
            node = node[k]
        return float(node["maximum"])
    return {"lost": top("tube_system", "properties", "lines", "items", "properties", "lost_rate"),
            "misroute": top("tube_system", "properties", "lines", "items", "properties", "misroute_rate"),
            "courier_lost": top("couriers", "properties", "routes", "items", "properties", "lost_rate"),
            "missed": top("couriers", "properties", "routes", "items", "properties", "missed_round_rate"),
            "stalled": top("tube_system", "properties", "stalled_rate"),
            "not_dispatched": top("units", "items", "properties", "not_dispatched_rate"),
            "never_collected": top("independent_faults", "properties", "never_collected_rate"),
            "content": top("independent_faults", "properties", "content_failure_rate")}


def corrupt(scenario: dict, kind: str) -> dict:
    """A deep copy of the scenario with one corruption applied."""
    s = copy.deepcopy(scenario)
    if kind == "staffing_absurd":
        s["volume"]["specimens_per_weekday"] = int(s["volume"]["specimens_per_weekday"]) * 10
        for day in s["receiving"]["staff_by_shift"].values():
            for shift in day:
                day[shift] = 1 if shift == "day" else 0
    elif kind == "impossible_routes":
        for line in s.get("tube_system", {}).get("lines") or []:
            line["transit_median_min"] = float(line["transit_median_min"]) * 30
            line["transit_p90_min"] = round(line["transit_median_min"] / 3, 1)
        for r in s.get("couriers", {}).get("routes") or []:
            r["travel_median_min"] = float(r["travel_median_min"]) * 20
            r["travel_p90_min"] = round(r["travel_median_min"] / 4, 1)
    elif kind == "contradictory_schedules":
        tube = s.get("tube_system") or {}
        if tube.get("station_empty_interval_min"):
            tube["station_empty_interval_min"] = {k: 2880 for k in tube["station_empty_interval_min"]}
        for r in s.get("couriers", {}).get("routes") or []:
            r["round_interval_min"] = {k: None for k in r["round_interval_min"]}
        for u in s.get("units") or []:
            if u.get("route") == "tube" and (s.get("couriers", {}).get("routes") or []):
                u["route"], u["courier_route_id"] = "courier", s["couriers"]["routes"][0]["route_id"]
    elif kind == "rates_10x":
        b = _bounds()
        for line in s.get("tube_system", {}).get("lines") or []:
            line["lost_rate"], line["misroute_rate"] = 10 * b["lost"], 10 * b["misroute"]
        if "stalled_rate" in (s.get("tube_system") or {}):
            s["tube_system"]["stalled_rate"] = 10 * b["stalled"]
        for r in s.get("couriers", {}).get("routes") or []:
            r["lost_rate"], r["missed_round_rate"] = 10 * b["courier_lost"], 10 * b["missed"]
        for u in s.get("units") or []:
            u["not_dispatched_rate"] = 10 * b["not_dispatched"]
        faults = s.setdefault("independent_faults", {})
        faults["never_collected_rate"], faults["content_failure_rate"] = 10 * b["never_collected"], 10 * b["content"]
    else:
        raise ValueError(f"unknown corruption {kind!r}")
    return s


def control_draw(records: list[dict], each: int, seed: int) -> list[tuple[dict, str]]:
    """2 x each scenarios in the seed's order: the first `each` corrupted (the kinds cycled), the next `each` clean."""
    order = sorted(records, key=lambda r: (zlib.crc32(f"{seed}:control:{r['scenario_id']}".encode()), r["scenario_id"]))
    picked = order[:2 * each]
    return ([(r, CORRUPTIONS[i % len(CORRUPTIONS)]) for i, r in enumerate(picked[:each])]
            + [(r, "clean") for r in picked[each:]])


def build_items(records: list[dict], cfg: dict, *, sentences: dict | None = None, label_n: int | None = None,
                seed: int | None = None) -> list[Item]:
    """Label items for every scenario (or a seeded `label_n` of them), then the controls."""
    budget = int(cfg["budgets"]["X1"])
    seed = int(cfg["samples"]["seed"]) if seed is None else seed
    sentences = sentences if sentences is not None else render.edge_sentences()
    labelled = records if label_n is None else sorted(
        records, key=lambda r: (zlib.crc32(f"{seed}:label:{r['scenario_id']}".encode()), r["scenario_id"]))[:label_n]
    out = []
    for r in sorted(labelled, key=lambda r: r["scenario_id"]):
        tags = list(r["planner"].get("edge_tags") or [])
        state = render.scenario_state(r["scenario"], r.get("kpis"), budget=budget)
        out.append(Item(f"label:{r['scenario_id']}", state, render.x1_questions(tags, sentences), meta={
            "kind": "label", "scenario_id": r["scenario_id"], "corruption": None, "planner": r["planner"],
            "state_hash": render.state_hash(state)}))
    for r, kind in control_draw(records, int(cfg["samples"]["X1_controls_each"]), seed):
        scenario = r["scenario"] if kind == "clean" else corrupt(r["scenario"], kind)
        state = render.scenario_state(scenario, None, budget=budget)
        out.append(Item(f"control:{r['scenario_id']}", state, render.x1_questions([], sentences), meta={
            "kind": "control", "scenario_id": r["scenario_id"], "corruption": kind, "planner": r["planner"],
            "state_hash": render.state_hash(state)}))
    return out


def answers(outcomes, items: list[Item], result=None) -> list[dict]:
    rows = []
    for o, it in zip(outcomes, items, strict=True):
        a = o.answers or {}
        plaus, focus = a.get("plausibility") or {}, a.get("dominant_failure") or {}
        rows.append({"item_id": it.item_id, "request_hash": o.request_hash, "status": o.status, "reason": o.reason,
                     "resolved_model": o.resolved_model, "refusals": o.refusals, **it.meta,
                     "plausibility": ({"score": plaus["score"], "probabilities": plaus["probabilities"]}
                                      if plaus.get("type") == "score" else None),
                     "dominant_failure": ({"choice": focus["choice"], "probabilities": focus["probabilities"]}
                                          if focus.get("type") == "choice" else None),
                     "edges": {name[5:]: v["p"] for name, v in a.items()
                               if name.startswith("edge_") and v.get("type") == "noul"}})
    return rows
