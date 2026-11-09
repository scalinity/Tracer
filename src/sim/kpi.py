"""Plausibility KPIs for a scenario, from a check simulation (spec §4.8 layer 4 and §5.10).

kpis(scenario) simulates the full scored week of replicate 99 (a seed reserved for checks) and
returns the KPIs below; check(scenario, stratum) returns one plain-English failure per KPI outside
its pass range, so an empty list means the scenario is plausible. The scenario validator
(src/scenarios/) calls check() on every generated configuration, and the scenario signature
(src/scenarios/signature.py) reads the flat median_*_min, breach_share and never_received_share keys.

- median collection-to-receipt by route kind (tube, courier, walk-in) and over every route,
  scored specimens received;
- breach share among scored specimens with a clock, against the stratum's range;
- never-received share among scored specimens with a clock, and the share of those cut at the
  7,200-minute horizon (bounded by the never-received share, so it has no range of its own);
- the peak count of silent specimens: collected, not received, and no older than their own window
  plus 60 minutes (the "outstanding" definition of spec §7.4), over the scored days;
- the mean receiving utilization of each shift that no disruption touches (busy server-minutes over
  available server-minutes); the largest is reported;
- realized weekday volume against specimens_per_weekday;
- the share of specimens resolved by the horizon: received, lost, never sent, never collected, or
  cut at 7,200 minutes (spec §5.2), which only a broken record misses.

Every threshold is an assumption (spec Appendix D, A-04).
"""

from __future__ import annotations

import hashlib
import json
import statistics
from datetime import datetime

from . import engine, processes, records

# Spec §5.10 says 2 days. A 2-day check never sees a disruption planned for days 2 to 6 or the
# weekend staffing, so the disrupted and edge breach ranges would be tested on weeks whose disruption
# never happened. The whole scored week (with the usual warm-up and warm-down days) costs under a second.
KPI_DAYS = 7
KPI_REPLICATE = 99
OUTSTANDING_GRACE_MIN = 60  # ASSUMED (A-07): a specimen stops being outstanding 60 minutes after its window

# Pass ranges. Every one is ASSUMED (spec §5.10, A-04).
MEDIAN_RECEIPT_RANGE_MIN = {"tube": (6, 90), "courier": (15, 300), "walk_in": (8, 120)}  # ASSUMED
BREACH_SHARE_RANGE = {"normal": (0.003, 0.10), "stressed": (0.005, 0.18), "disrupted": (0.01, 0.25),
                      "edge": (0.01, 0.40)}  # ASSUMED
MAX_NEVER_RECEIVED_SHARE = 0.06  # ASSUMED
MAX_PEAK_SILENT_PER_WEEKDAY_VOLUME = 3.0  # ASSUMED: peak silent count at most 3 times specimens_per_weekday
MAX_SHIFT_UTILIZATION = 0.97  # ASSUMED
WEEKDAY_VOLUME_TOLERANCE = 0.25  # ASSUMED: realized weekday volume within 25% of specimens_per_weekday
MIN_RESOLVED_SHARE = 1.0  # ASSUMED: every specimen is received or lost by the horizon


def check_index(scenario: dict) -> int:
    """The scenario's index, or a stable one from its content when it has none yet (before freezing)."""
    if "scenario_index" in scenario:
        return int(scenario["scenario_index"])
    digest = hashlib.sha256(json.dumps(scenario, sort_keys=True).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 9000


def _minute(text: str) -> float:
    return (datetime.fromisoformat(text) - records.DAY0).total_seconds() / 60


def peak_silent(recs: list[dict], t0: float, t1: float) -> int:
    marks = []
    for r in recs:
        lab = r["brief"]["label"]
        if not lab["clock_started"]:
            continue
        c = _minute(r["brief"]["fixed"]["collected_at"])
        received = next((_minute(e["timestamp"]) for e in r["trail"]["observed_events"] if e["activity"] == "received"),
                        None)
        end = c + lab["window_min"] + OUTSTANDING_GRACE_MIN
        end = end if received is None else min(end, received)
        if end > t0 and c < t1 and end > c:
            marks += [(max(c, t0), 1), (min(end, t1), -1)]
    peak = level = 0
    for _, step in sorted(marks, key=lambda m: (m[0], m[1])):
        level += step
        peak = max(peak, level)
    return peak


def _integral(trace: list[tuple[float, int]], t0: float, t1: float) -> float:
    total = 0.0
    for i, (t, v) in enumerate(trace):
        nxt = trace[i + 1][0] if i + 1 < len(trace) else float("inf")
        a, b = max(t, t0), min(nxt, t1)
        if b > a:
            total += v * (b - a)
    return total


def shift_utilization(result, scenario: dict, days: int) -> float | None:
    """The largest mean utilization over the shifts of the scored days that no disruption touches."""
    pattern = processes.SHIFT_PATTERNS[scenario["receiving"]["shift_pattern"]]
    bounds = processes.shift_boundaries(pattern, -processes.DAY_MIN, (days + 1) * processes.DAY_MIN)
    windows = [(w["start_min"], w["end_min"]) for w in result.disruptions]
    busy = result.stats["busy"]  # in event order; keep the last value at each minute
    busy = [(t, v) for i, (t, v) in enumerate(busy) if i + 1 == len(busy) or busy[i + 1][0] != t]
    worst = None
    for a, b in zip(bounds, bounds[1:]):
        if b <= 0 or a >= days * processes.DAY_MIN or any(s < b and e > a for s, e in windows):
            continue
        available = _integral(result.stats["capacity"], a, b)
        if available > 0:
            u = _integral(busy, a, b) / available
            worst = u if worst is None else max(worst, u)
    return worst


def kpis(scenario: dict, loaded: dict | None = None, *, scenario_index: int | None = None) -> dict:
    index = check_index(scenario) if scenario_index is None else scenario_index
    result = engine.simulate(scenario, scenario_index=index, replicate=KPI_REPLICATE, loaded=loaded, days=KPI_DAYS)
    scored = [r for r in result.records if r["brief"]["v2"]["scored"]]
    with_clock = [r for r in scored if r["brief"]["label"]["clock_started"]]
    received = [r for r in scored if r["brief"]["targets"]["receipt_after_min"] is not None]
    medians = {kind: _median([r for r in received if r["brief"]["v2"]["route_kind"] == kind])
               for kind in MEDIAN_RECEIPT_RANGE_MIN}
    weekdays = [d for d in range(KPI_DAYS) if d % 7 not in processes.WEEKEND_DAYS]
    on_weekdays = sum(1 for r in scored if int(_minute(r["brief"]["fixed"]["collected_at"]) // processes.DAY_MIN)
                      in weekdays)
    utilization = shift_utilization(result, scenario, KPI_DAYS)
    share = lambda rows, pred: round(sum(map(pred, rows)) / len(rows), 4) if rows else None
    return {
        "days": KPI_DAYS,
        "replicate": KPI_REPLICATE,
        "scenario_index": index,
        "scored": len(scored),
        "median_receipt_min": medians,
        "median_tube_min": medians.get("tube"),
        "median_courier_min": medians.get("courier"),
        "median_walk_in_min": medians.get("walk_in"),
        "median_transit_min": _median(received),
        "breach_share": share(with_clock, lambda r: r["brief"]["label"]["breach"]),
        "never_received_share": share(with_clock, lambda r: not r["trail"]["outcome"]["received"]),
        "horizon_cut_share": share(with_clock, lambda r: r["brief"]["v2"]["horizon_cut"]),
        "peak_silent": peak_silent(result.records, 0, KPI_DAYS * processes.DAY_MIN),
        "max_shift_utilization": None if utilization is None else round(utilization, 4),
        "weekday_volume": round(on_weekdays / len(weekdays), 2) if weekdays else None,
        "resolved_share": share(result.records, resolved),
    }


def _median(recs: list[dict]) -> float | None:
    times = [r["brief"]["targets"]["receipt_after_min"] for r in recs]
    return round(statistics.median(times), 2) if times else None


def resolved(rec: dict) -> bool:
    """Received, lost, never sent, never collected, or cut at its 7,200-minute horizon (spec §5.2)."""
    last = rec["trail"]["truth_segments"][-1]
    return (rec["trail"]["outcome"]["received"] or rec["brief"]["v2"]["horizon_cut"]
            or last["state"] in ("lost", "not_collected") or last["substate"] == "not_dispatched")


def check(scenario: dict, stratum: str, loaded: dict | None = None) -> list[str]:
    """One failure per KPI outside its pass range; an empty list means the scenario is plausible."""
    k = kpis(scenario, loaded)
    failures = []
    for kind, (lo, hi) in MEDIAN_RECEIPT_RANGE_MIN.items():
        m = k["median_receipt_min"][kind]
        if m is not None and not lo <= m <= hi:
            failures.append(f"median collection-to-receipt by {kind.replace('_', '-')} is {m:g} min, "
                            f"outside {lo} to {hi} min")
    lo, hi = BREACH_SHARE_RANGE[stratum]
    if k["breach_share"] is not None and not lo <= k["breach_share"] <= hi:
        failures.append(f"breach share is {k['breach_share']:.1%}, outside {lo:.1%} to {hi:.0%} for a {stratum} scenario")
    if k["never_received_share"] is not None and k["never_received_share"] > MAX_NEVER_RECEIVED_SHARE:
        failures.append(f"never-received share is {k['never_received_share']:.1%}, above {MAX_NEVER_RECEIVED_SHARE:.0%}")
    per_weekday = scenario["volume"]["specimens_per_weekday"]
    if k["peak_silent"] > MAX_PEAK_SILENT_PER_WEEKDAY_VOLUME * per_weekday:
        failures.append(f"peak count of silent specimens is {k['peak_silent']}, above "
                        f"{MAX_PEAK_SILENT_PER_WEEKDAY_VOLUME:g} times the {per_weekday} specimens per weekday")
    if k["max_shift_utilization"] is not None and k["max_shift_utilization"] > MAX_SHIFT_UTILIZATION:
        failures.append(f"receiving utilization reaches {k['max_shift_utilization']:.2f} in a shift with no "
                        f"disruption, above {MAX_SHIFT_UTILIZATION}")
    if k["weekday_volume"] is not None and abs(k["weekday_volume"] / per_weekday - 1) > WEEKDAY_VOLUME_TOLERANCE:
        failures.append(f"realized weekday volume is {k['weekday_volume']:g}, more than "
                        f"{WEEKDAY_VOLUME_TOLERANCE:.0%} from the {per_weekday} specimens per weekday")
    if k["resolved_share"] < MIN_RESOLVED_SHARE:
        failures.append(f"only {k['resolved_share']:.2%} of specimens are received or lost by the horizon")
    return failures
