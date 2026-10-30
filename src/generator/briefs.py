"""Specimen briefs: what happens to each specimen, drawn before Claude writes a word.

make_briefs(seed, run, n) returns n plain dicts sorted by collection time. Seeded code
decides the specimen (type, tests, unit, priority, route, collection time), the one
planted fault, and every duration; Claude later supplies scan texture around those
numbers (docs/architecture.md §6, AGENTS.md). Parameters come from config/config.yaml.

Two random streams are spawned from (seed, run): one picks which specimens exist, the
other draws how long each leg takes. Retuning a leg in config.yaml therefore changes
timings without changing which specimen carries which fault.

In `targets`, a number is the minutes that stage lasts and None means the stage does not
happen for this specimen (a hand carry has no tube leg, a lost specimen has no receipt).
`receipt_after_min` is the sum of the stages before receipt. A never-collected specimen
has no collection scan, so `fixed.collected_at` is when the draw was scheduled and
`label.clock_started` is false; like every specimen that never reaches the clock stop,
it is labeled a breach.

Run `python -m src.generator.briefs --seed 0 --run 0 --n 200` to print a table.
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import date, datetime, time, timedelta
from statistics import NormalDist

import numpy as np

from . import config

Z90 = NormalDist().inv_cdf(0.9)

# Faults that need the pneumatic tube, so the specimen is routed by tube.
TUBE_FAULTS = ("lost_in_tube", "stalled_at_station", "misrouted")

# States the generator gives a minute to. A policy may only start and stop its clock at one.
TIMED_STATES = ("collected", "dispatched", "in_tube", "misrouted", "at_station", "stalled_at_station",
                "received", "accessioned", "plated")

TARGET_KEYS = ("dispatch_wait_min", "tube_transit_min", "station_dwell_min", "hand_carry_min", "stalled_min",
               "misrouted_min", "receipt_after_min", "accession_wait_min", "plate_wait_min")

LAB_LOCATIONS = ("tube_station_lab", "lab_receiving", "lab_bench_1", "lab_bench_2")
WRONG_STATION = "tube_station_pharmacy"  # where a misrouted carrier lands


def _streams(seed: int, run: int) -> tuple[np.random.Generator, np.random.Generator]:
    brief_seq, timing_seq = np.random.SeedSequence([seed, run]).spawn(2)
    return np.random.default_rng(brief_seq), np.random.default_rng(timing_seq)


def _collection_times(rng: np.random.Generator, cfg: dict, run: int, n: int) -> list[datetime]:
    """n collection times over the run's days, in proportion to the hourly profile.

    Spreading a fixed count over the (day, hour) slots in proportion to their rates is a
    Poisson process conditioned on its total, so n is exact and the shape is the profile's.
    """
    days = cfg["run"]["days"]
    start = date.fromisoformat(cfg["run"]["start_date"]) + timedelta(days=days * run)
    hourly = np.array(cfg["arrivals"]["rate_per_hour"], dtype=float)
    weight = np.concatenate([
        hourly * (cfg["arrivals"]["weekend_factor"] if (start + timedelta(days=d)).weekday() >= 5 else 1.0)
        for d in range(days)])
    slot = rng.choice(days * 24, size=n, p=weight / weight.sum())
    minute = np.sort(slot * 60 + rng.integers(0, 60, size=n), kind="stable")
    origin = datetime.combine(start, time.min)
    return [origin + timedelta(minutes=int(m)) for m in minute]


def _leg_minutes(rng: np.random.Generator, leg: dict, n: int) -> np.ndarray:
    """Whole minutes, at least 1, from the lognormal with this median and 90th percentile."""
    sigma = math.log(leg["p90_min"] / leg["median_min"]) / Z90
    return np.maximum(1, np.rint(rng.lognormal(math.log(leg["median_min"]), sigma, size=n))).astype(int)


def _timing(route: str, fault: str, x: dict) -> tuple[dict, dict]:
    """Targets and the minute (after collection) each timed state is entered."""
    targets = dict.fromkeys(TARGET_KEYS)
    timeline: dict[str, int] = {}
    if fault == "never_collected":
        return targets, timeline
    timeline["collected"] = 0
    if fault == "not_dispatched":
        return targets, timeline

    clock = targets["dispatch_wait_min"] = x["dispatch_wait"]
    timeline["dispatched"] = clock
    if route == "hand_carry":
        targets["hand_carry_min"] = x["hand_carry_transit"]
        clock += x["hand_carry_transit"]
    else:
        timeline["in_tube"] = clock
        targets["tube_transit_min"] = x["tube_transit"]
        clock += x["tube_transit"]
        if fault == "lost_in_tube":
            return targets, timeline
        if fault == "misrouted":
            timeline["misrouted"] = clock
            targets["misrouted_min"] = x["misrouted"]
            clock += x["misrouted"]
        timeline["at_station"] = clock
        targets["station_dwell_min"] = x["station_dwell"]
        clock += x["station_dwell"]
        if fault == "stalled_at_station":
            timeline["stalled_at_station"] = clock
            targets["stalled_min"] = x["stalled_at_station"]
            clock += x["stalled_at_station"]

    targets["receipt_after_min"] = timeline["received"] = clock
    targets["accession_wait_min"] = x["accession_wait"]
    if fault == "content_failure":  # rejected at accessioning, never plated
        timeline["rejected"] = clock + x["accession_wait"]
    else:
        timeline["accessioned"] = clock + x["accession_wait"]
        targets["plate_wait_min"] = x["plating_wait"]
        timeline["plated"] = timeline["accessioned"] + x["plating_wait"]
    return targets, timeline


def _label(pid: str, policy: dict, timeline: dict) -> dict:
    """Window and breach from the policy's own clock_start and clock_stop."""
    start, stop = timeline.get(policy["clock_start"]), timeline.get(policy["clock_stop"])
    elapsed = stop - start if start is not None and stop is not None else None
    return {
        "policy_id": pid,
        "window_min": policy["window_min"],
        "urgency_tier": policy["urgency_tier"],
        "clock_start": policy["clock_start"],
        "clock_stop": policy["clock_stop"],
        "clock_started": start is not None,
        "elapsed_min": elapsed,
        "breach": elapsed is None or elapsed > policy["window_min"],
    }


def _locations(unit: str, route: str, fault: str) -> list[str]:
    out = [f"{unit}_collection"]
    if route == "tube":
        out.append(f"tube_station_{unit}")
        if fault == "misrouted":
            out.append(WRONG_STATION)
    return out + list(LAB_LOCATIONS if route == "tube" else LAB_LOCATIONS[1:])


def make_briefs(seed: int, run: int, n: int, loaded: dict | None = None) -> list[dict]:
    loaded = loaded or config.load()
    cfg, policy = loaded["config"], loaded["policy"]
    rng, timing_rng = _streams(seed, run)

    entries = list(cfg["specimen_mix"]["entries"].values())
    resolved = []
    for entry in entries:
        rows = [policy["policies"][pid] for pid in entry["policies"]]
        tests = [row["test"] for row in rows]
        pid, governing = config.resolve(policy, rows[0]["specimen_type"], tests)
        for state in (governing["clock_start"], governing["clock_stop"]):
            if state not in TIMED_STATES:
                raise config.PolicyError(f"policy {pid}: the generator does not time state {state!r}")
        resolved.append((rows[0]["specimen_type"], tests, pid, governing))

    units = list(cfg["collecting_units"]["weights"])
    fault_names = list(cfg["faults"])

    collected = _collection_times(rng, cfg, run, n)
    mix = rng.choice(len(entries), size=n, p=[e["weight"] for e in entries])
    unit = rng.choice(len(units), size=n, p=list(cfg["collecting_units"]["weights"].values()))
    stat = rng.random(n) < cfg["priority"]["stat_fraction"]
    fault = rng.choice(len(fault_names), size=n, p=[f["rate"] for f in cfg["faults"].values()])
    by_tube = rng.random(n) < cfg["route"]["tube_probability"]

    legs = {name: _leg_minutes(timing_rng, cfg["legs"][name], n) for name in config.LEGS}
    for name in config.FAULTS_WITH_DELAY:
        lo, hi = cfg["faults"][name]["delay_min"]
        legs[name] = timing_rng.integers(lo, hi + 1, size=n)

    briefs = []
    for i in range(n):
        planted = fault_names[fault[i]]
        route = "tube" if by_tube[i] or planted in TUBE_FAULTS else "hand_carry"
        specimen_type, tests, pid, governing = resolved[mix[i]]
        targets, timeline = _timing(route, planted, {name: int(arr[i]) for name, arr in legs.items()})
        briefs.append({
            "specimen_id": f"SP-r{run:02d}-{i + 1:06d}",
            "fixed": {
                "type": specimen_type,
                "tests": tests,
                "unit": units[unit[i]],
                "priority": "stat" if stat[i] else "routine",
                "route": route,
                "collected_at": collected[i].isoformat(),
            },
            "planted_fault": planted,
            "targets": targets,
            "locations": _locations(units[unit[i]], route, planted),
            "label": _label(pid, governing, timeline),
        })
    return briefs


def table(briefs: list[dict]) -> str:
    def cell(value) -> str:
        return "-" if value is None else str(value)

    head = ("id", "collected", "type", "unit", "pri", "route", "fault", "disp", "tube", "dwell", "hand", "stall",
            "misr", "recv", "window", "breach")
    rows = []
    for b in briefs:
        f, t, lab = b["fixed"], b["targets"], b["label"]
        rows.append((b["specimen_id"], f["collected_at"][5:16].replace("T", " "), lab["policy_id"].split("__")[0][:30], f["unit"],
                     f["priority"], f["route"], b["planted_fault"], cell(t["dispatch_wait_min"]),
                     cell(t["tube_transit_min"]), cell(t["station_dwell_min"]), cell(t["hand_carry_min"]),
                     cell(t["stalled_min"]), cell(t["misrouted_min"]), cell(t["receipt_after_min"]),
                     lab["window_min"], "BREACH" if lab["breach"] else "ok"))
    widths = [max(len(str(r[c])) for r in [head, *rows]) for c in range(len(head))]
    lines = ["  ".join(str(v).ljust(w) for v, w in zip(r, widths)).rstrip() for r in [head, *rows]]
    breaches = sum(b["label"]["breach"] for b in briefs)
    return "\n".join(lines + [f"{len(briefs)} specimens, {breaches} breach"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Print a table of specimen briefs.")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--run", type=int, default=0)
    ap.add_argument("--n", type=int, default=200)
    args = ap.parse_args(argv)
    print(table(make_briefs(args.seed, args.run, args.n)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
