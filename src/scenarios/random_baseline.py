"""Code-random scenarios: the schema sampled within its bounds, with only consistency repaired.

Ablation A4 (docs/neural/spec.md §8.8) asks whether the language-model spend was worth it, and the
dispersion report (§4.9) sets the library beside the same number of uniform draws. Both need
scenarios with no judgment in them: every number is drawn uniformly inside the schema's bounds,
and code repairs only what a scenario cannot be without (unique ids, references that resolve,
p90 above median, a staffed shift, a disruption that fits the week, a target that exists).

The enum labels (size band, transport mix, staffing, failure focus) are set from the content so
the record is well formed. There is no narrative beyond one fixed sentence, no edge tag and no
brief. Scanner downtime is out-of-distribution and is never drawn.
"""

from __future__ import annotations

import numpy as np

from . import planner, schema, validate

ARCHETYPES = tuple(planner.config()["compatibility"]["size_by_archetype"])
UNIT_KINDS = ("ed", "icu", "med_surg", "periop", "clinic_onsite", "clinic_offsite", "oncology", "pediatrics",
              "labor_delivery", "dialysis", "long_term_care")
# Disruption count by stratum: the only thing the stratum changes in a code-random scenario.
DISRUPTION_COUNTS = {"normal": (0, 0), "stressed": (0, 2), "disrupted": (2, 6), "edge": (3, 10)}
FOCUS_OF_TYPE = planner.FOCUS_OF_DISRUPTION
SIZE_RANGES = {"small": (30, 120), "medium": (121, 350), "large": (351, 900)}


def _uniform(rng, lo: float, hi: float, digits: int = 3) -> float:
    return round(float(rng.uniform(lo, hi)), digits)


def _int(rng, lo: int, hi: int) -> int:
    return int(rng.integers(lo, hi + 1))


def _pair(rng, lo: float, hi: float, p90_hi: float) -> dict:
    median = _uniform(rng, lo, hi, 2)
    return {"median": median, "p90": round(float(rng.uniform(median + 0.01, p90_hi)), 2)}


def _slug(prefix: str, i: int) -> str:
    return f"{prefix}_{i + 1:02d}"


def _volume(rng, archetype: str) -> dict:
    bands = planner.sizes_for(archetype)
    lo, hi = SIZE_RANGES[bands[0]][0], SIZE_RANGES[bands[-1]][1]
    while True:
        profile = [_uniform(rng, 0.1, 10, 2) for _ in range(24)]
        if (max(profile) - min(profile)) / (sum(profile) / 24) >= 2 * validate.FLAT_PROFILE_SPREAD:
            break
    return {"specimens_per_weekday": _int(rng, lo, hi), "hourly_profile": profile,
            "weekend_factor": _uniform(rng, 0.3, 1.2, 2)}


def _lines(rng) -> list[dict]:
    out = []
    for i in range(_int(rng, 0, 8)):
        pair = _pair(rng, 1, 20, 45)
        out.append({"line_id": _slug("line", i), "transit_median_min": pair["median"], "transit_p90_min": pair["p90"],
                    "misroute_rate": _uniform(rng, 0, 0.03, 4), "lost_rate": _uniform(rng, 0, 0.01, 5)})
    return out


def _routes(rng) -> list[dict]:
    out = []
    for i in range(_int(rng, 0, 6)):
        intervals = {s: (None if rng.random() < 0.15 else _int(rng, 10, 240)) for s in ("day", "evening", "night")}
        if all(v is None for v in intervals.values()):
            intervals["day"] = _int(rng, 10, 240)
        travel = _pair(rng, 3, 120, 240)
        out.append({"route_id": _slug("route", i), "kind": str(rng.choice(["onsite_runner", "campus_loop", "offsite_van"])),
                    "round_interval_min": intervals, "travel_median_min": travel["median"],
                    "travel_p90_min": travel["p90"], "missed_round_rate": _uniform(rng, 0, 0.2, 4),
                    "lost_rate": _uniform(rng, 0, 0.01, 5), "capacity_per_round": _int(rng, 5, 200)})
    return out


def _unit(rng, i: int, lines: list[dict], routes: list[dict], mix_keys: list[str]) -> dict:
    vans = [r["route_id"] for r in routes if r["kind"] == "offsite_van"]
    kinds = [k for k in UNIT_KINDS if k != "clinic_offsite" or vans]
    kind = str(rng.choice(kinds))
    available = {"tube": bool(lines), "mixed": bool(lines), "courier": bool(routes), "walk_in": True}
    options = [r for r, ok in available.items() if ok]
    route = "courier" if kind == "clinic_offsite" else str(rng.choice(options))
    tube_line = str(rng.choice([x["line_id"] for x in lines])) if route in ("tube", "mixed") else None
    if kind == "clinic_offsite":
        courier = str(rng.choice(vans))
    elif route == "courier" or (route == "mixed" and routes and rng.random() < 0.7):
        courier = str(rng.choice([r["route_id"] for r in routes]))
    else:
        courier = None
    batched = rng.random() < 0.3
    dispatch = _pair(rng, 1, 120, 480)
    tilt_keys = [str(k) for k in rng.choice(mix_keys, size=_int(rng, 0, 6), replace=False)]
    return {"unit_id": _slug("unit", i), "kind": kind, "volume_share": _uniform(rng, 0.01, 1),
            "stat_fraction": _uniform(rng, 0, 1, 2), "route": route, "tube_line_id": tube_line,
            "courier_route_id": courier, "tube_share_if_mixed": _uniform(rng, 0, 1, 2) if route == "mixed" else None,
            "dispatch": {"mode": "batched" if batched else "immediate", "median_min": dispatch["median"],
                         "p90_min": dispatch["p90"], "batch_interval_min": _int(rng, 15, 240) if batched else None},
            "not_dispatched_rate": _uniform(rng, 0, 0.05, 4),
            "mix_tilt": [{"key": k, "multiplier": _uniform(rng, 0, 5, 2)} for k in tilt_keys]}


def _disruptions(rng, scenario: dict, count: int) -> list[dict]:
    targets = {
        "tube_line_outage": [x["line_id"] for x in scenario["tube_system"]["lines"]],
        "tube_line_degraded": [x["line_id"] for x in scenario["tube_system"]["lines"]],
        "station_unattended": ["lab_station"], "receiving_short_staffed": ["receiving"],
        "courier_missed_rounds": [r["route_id"] for r in scenario["couriers"]["routes"]],
        "courier_delayed": [r["route_id"] for r in scenario["couriers"]["routes"]],
        "offsite_route_closed": [r["route_id"] for r in scenario["couriers"]["routes"] if r["kind"] == "offsite_van"],
        "unit_surge": [u["unit_id"] for u in scenario["units"]],
        "unit_batching_hold": [u["unit_id"] for u in scenario["units"]],
        "instrument_down": [i["name"] for i in scenario["processing"]["instruments"]]}
    types = [t for t, options in targets.items() if options]
    out = []
    for i in range(count):
        kind = str(rng.choice(types))
        day, start = _int(rng, 0, 6), _int(rng, 0, 24 * 60 - 1)
        room = validate.WEEK_END_MIN - (day * validate.DAY_MIN + start)
        out.append({"disruption_id": _slug("d", i), "type": kind, "target_id": str(rng.choice(targets[kind])), "day": day,
                    "start": f"{start // 60:02d}:{start % 60:02d}", "duration_min": _int(rng, 10, min(720, room)),
                    "severity": _uniform(rng, 0.1, 1, 2),
                    "response": str(rng.choice(["hold_at_unit", "divert_to_courier", "stuck_in_system"]))
                    if kind == "tube_line_outage" else "none",
                    "noticed_after_min": _int(rng, 0, 240) if kind == "tube_line_outage" else 0,
                    "recurs_daily": bool(rng.random() < 0.2)})
    # A recurring window repeats on every day, so it must fit the last day too.
    for d in out:
        while max(end for _, end in validate.occurrences(d)) > validate.WEEK_END_MIN:
            d["duration_min"] = max(10, d["duration_min"] // 2)
    return out


def _label(scenario: dict) -> None:
    daily = scenario["volume"]["specimens_per_weekday"]
    scenario["size_band"] = next(b for b, (_, hi) in SIZE_RANGES.items() if daily <= hi)
    scenario["transport_mix"] = validate.classify_transport(validate.route_shares(scenario))
    scenario["staffing"] = validate.classify_staffing(scenario)
    foci = {FOCUS_OF_TYPE[d["type"]] for d in scenario["disruptions"]}
    scenario["failure_focus"] = "random_faults_only" if not foci else next(iter(foci)) if len(foci) == 1 else "compound"


def random_scenario(seed: int, *, archetype: str | None = None, stratum: str | None = None) -> dict:
    """One code-random scenario, deterministic in (seed, archetype, stratum)."""
    rng = np.random.default_rng(np.random.SeedSequence([seed, 31337]))
    archetype = archetype or str(rng.choice(ARCHETYPES))
    stratum = stratum or str(rng.choice(planner.STRATA))
    if archetype not in ARCHETYPES or stratum not in DISRUPTION_COUNTS:
        raise ValueError(f"unknown archetype {archetype!r} or stratum {stratum!r}")
    mix_keys = schema.mix_keys()
    lines, routes = _lines(rng), _routes(rng)
    units = [_unit(rng, i, lines, routes, mix_keys) for i in range(_int(rng, 3, 16))]
    staff = lambda: {s: _int(rng, 1, 12) for s in ("day", "evening", "night")}  # noqa: E731
    scenario = {
        "archetype": archetype, "size_band": "small", "transport_mix": "mixed", "staffing": "full_24_7",
        "failure_focus": "random_faults_only",
        "narrative": f"A code-random laboratory configuration (seed {seed}): every value is drawn uniformly inside the schema bounds.",
        "volume": _volume(rng, archetype),
        "specimen_mix": {k: _uniform(rng, 0, 1) for k in mix_keys},
        "units": units,
        "tube_system": {"lines": lines,
                        "station_empty_interval_min": {s: _uniform(rng, 2, 90, 1) for s in ("day", "evening", "night")},
                        "stalled_rate": _uniform(rng, 0, 0.06, 4), "stall_extra_min": _pair(rng, 10, 240, 720),
                        "misroute_found_min": _pair(rng, 10, 120, 480)},
        "couriers": {"routes": routes, "on_call_runner": bool(rng.random() < 0.5), "on_call_response_median_min": None},
        "receiving": {"staff_by_shift": {"weekday": staff(), "weekend": staff()},
                      "shift_pattern": str(rng.choice(list(validate.SHIFT_CLOCKS))),
                      "scan_service_min": _pair(rng, 0.3, 5, 30), "shift_change_gap_min": _int(rng, 0, 45)},
        "processing": {"accession_min": _pair(rng, 2, 120, 600), "plating_min": _pair(rng, 2, 120, 600),
                       "instruments": [{"name": _slug("instrument", i), "downtime_per_week": _uniform(rng, 0, 3, 2),
                                        "downtime_median_min": _uniform(rng, 10, 480, 1)} for i in range(_int(rng, 0, 4))],
                       "staff_pull_from_receiving": _uniform(rng, 0, 1, 2)},
        "independent_faults": {"never_collected_rate": _uniform(rng, 0, 0.02, 4),
                               "content_failure_rate": _uniform(rng, 0, 0.05, 4)},
        "disruptions": [], "edge_tags": [],
        "realism_notes": "Drawn by code with no judgment: a baseline for comparison, not a realistic laboratory.",
    }
    if scenario["couriers"]["on_call_runner"]:
        scenario["couriers"]["on_call_response_median_min"] = _uniform(rng, 5, 60, 1)
    lo, hi = DISRUPTION_COUNTS[stratum]
    scenario["disruptions"] = _disruptions(rng, scenario, _int(rng, lo, hi))
    _label(scenario)
    return scenario
