"""Alert triage (plan task M5; docs/architecture.md §5.2.1): weights, routing and the alert list.

The settings are in config/triage.yaml and every one is an assumption. The model alerts a specimen
once its expected harm, the risk of missing the window times its urgency weight, reaches a threshold.
Lowering the threshold can only move every alert earlier, so the lowest threshold that keeps alerts
within the per-shift budget is the one with the longest warning that budget allows; that is how
src/pipeline/evaluate.py sets it, on the fit runs. This module holds the pieces that need no scoring:
the loader, the harm series, the window-aware baseline, the likely place, the routing and the alert rows.

A specimen is anything with the attributes of evaluate.Spec; this module never touches the truth.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from src.generator import config
from src.models import location

ROOT = Path(__file__).resolve().parents[2]
TRIAGE_PATH = ROOT / "config" / "triage.yaml"
TIERS = (1, 2, 3)
ROUTES = ("tube", "hand_carry")
SECTIONS = ("urgency_weights", "operating_point", "routing")
ALERT_COLUMNS = ("specimen_id", "run", "specimen_type", "tier", "route", "likely_place", "place_probability", "role",
                 "collected_at", "alert_at",
                 "minutes_since_collection", "window_min", "warning_min", "risk", "harm", "closed_at", "outcome")


def validate(cfg: dict) -> None:
    for name in SECTIONS:
        if name not in cfg:
            raise config.PolicyError(f"triage.yaml has no {name} section")
        config.check_source(name, cfg[name])
    weights = cfg["urgency_weights"]
    schemes = {"default": weights.get("default"), **(weights.get("checks") or {})}
    if "equal" not in schemes or "steep" not in schemes:
        raise config.PolicyError("urgency_weights needs the checks 'equal' and 'steep' beside the default")
    for scheme, by_tier in schemes.items():
        if not isinstance(by_tier, dict) or set(by_tier) != set(TIERS):
            raise config.PolicyError(f"urgency_weights {scheme} must give a weight for tiers 1, 2 and 3")
        if any(not isinstance(w, (int, float)) or w <= 0 for w in by_tier.values()):
            raise config.PolicyError(f"urgency_weights {scheme}: every weight must be positive")
        if not by_tier[1] >= by_tier[2] >= by_tier[3]:
            raise config.PolicyError(f"urgency_weights {scheme}: a less urgent tier cannot outweigh a more urgent one")
    budget = cfg["operating_point"].get("budget_per_shift")
    if not isinstance(budget, (int, float)) or budget <= 0:
        raise config.PolicyError("operating_point budget_per_shift must be positive")
    for key in (*ROUTES, "still_at_unit"):
        if not isinstance(cfg["routing"].get(key), str) or not cfg["routing"][key].strip():
            raise config.PolicyError(f"routing {key} must name a role")


def load(path: Path = TRIAGE_PATH) -> dict:
    cfg = config.load_yaml(path)
    validate(cfg)
    return cfg


def weight_schemes(cfg: dict) -> dict[str, dict[int, float]]:
    """The default weights first, then the two checks."""
    w = cfg["urgency_weights"]
    return {"default": w["default"], **w["checks"]}


def harm_series(specs, series: dict[str, np.ndarray], weights: dict[int, float]) -> dict[str, np.ndarray]:
    """Expected harm at every scoring tick: the weight of the specimen's tier times its risk."""
    return {s.specimen_id: weights[s.tier] * series[s.specimen_id] for s in specs}


def window_share_alerts(specs, share: float) -> dict[str, float | None]:
    """The window-aware baseline: alert at the first tick once a fixed share of the stability window
    has passed, whatever the specimen is. It ignores how long its kind of specimen usually takes."""
    out = {}
    for s in specs:
        ticks = s.ticks
        i = int(np.searchsorted(ticks, share * s.window, side="left"))
        out[s.specimen_id] = float(ticks[i]) if i < len(ticks) else None
    return out


def likely_places(specs, alerts: dict[str, float | None], params: dict, gen: dict,
                  tube_share: float) -> dict[str, tuple[str, float]]:
    """The most likely place of each alerted specimen at its alert tick, and its probability, from the
    location filter (src/models/location.py) run for the specimen's group and route."""
    wanted: dict[tuple, int] = {}
    for s in specs:
        if alerts[s.specimen_id] is not None:
            key = (s.group, s.route)
            wanted[key] = max(wanted.get(key, 0), int(alerts[s.specimen_id]))
    series = {}
    for (group, route), last in wanted.items():
        legs = location.legs_for(params, gen, group, route, tube_share)
        series[(group, route)] = location.place_series(*location.filter_series(legs, last))
    out = {}
    for s in specs:
        if alerts[s.specimen_id] is not None:
            places, i = series[(s.group, s.route)], int(alerts[s.specimen_id])
            best = max(places, key=lambda p: places[p][i])
            out[s.specimen_id] = (best, float(places[best][i]))
    return out


def role_for(cfg: dict, route: str, place: str | None = None) -> str:
    """The role that receives an alert. A specimen the location filter places still at its unit is the
    unit's to send, so it goes to the charge nurse; any other goes by its route."""
    if route not in ROUTES:
        raise config.PolicyError(f"unknown route {route!r}")
    return cfg["routing"]["still_at_unit"] if place == "at_unit" else cfg["routing"][route]


def alert_rows(specs, alerts: dict[str, float | None], risk: dict[str, np.ndarray],
               harm: dict[str, np.ndarray], places: dict[str, tuple[str, float]], cfg: dict) -> list[dict]:
    """One row per alert, earliest first. An alert opens at the tick it fires and closes at the receipt
    scan, so a specimen with no receipt stays open (closed_at is empty). `outcome` comes from the
    evaluation and is not something the alert knew."""
    rows = []
    for s in specs:
        minute = alerts[s.specimen_id]
        if minute is None:
            continue
        i = int(np.searchsorted(s.ticks, minute))
        start = datetime.fromisoformat(s.collected_at)
        at = lambda m: (start + timedelta(seconds=round(m * 60))).isoformat(timespec="minutes")
        rows.append({
            "specimen_id": s.specimen_id, "run": s.run, "specimen_type": s.kind, "tier": s.tier,
            "route": s.route, "likely_place": places[s.specimen_id][0],
            "place_probability": round(places[s.specimen_id][1], 4),
            "role": role_for(cfg, s.route, places[s.specimen_id][0]), "collected_at": start.isoformat(timespec="minutes"),
            "alert_at": at(minute), "minutes_since_collection": minute, "window_min": s.window,
            "warning_min": s.window - minute,
            "risk": round(float(risk[s.specimen_id][i]), 4), "harm": round(float(harm[s.specimen_id][i]), 4),
            "closed_at": "" if s.receipt_min is None else at(s.receipt_min),
            "outcome": "breach" if s.breach else "in time"})
    return sorted(rows, key=lambda r: (r["alert_at"], r["specimen_id"]))
