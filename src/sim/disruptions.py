"""Disruption windows: when each scheduled failure is active, and what it targets (spec §5.5).

realize() turns a scenario's `disruptions[]` into concrete windows in minutes from day 0 00:00
(Monday, January 5, 2026). A window with `recurs_daily: true` repeats on every scored day; the
others happen on their own day. Windows on days the replicate does not score are dropped, and a
window that runs past the simulation's horizon is clipped to it. Instruments also go down at
random: per replicate, a Poisson count with mean `downtime_per_week` (scaled to the scored days),
each downtime a lognormal with the instrument's median and a 90th percentile of three times it,
starting uniformly over the scored days. These random windows are realized beside the scheduled
ones and, like `instrument_down`, pull receiving staff.

Targets: a tube line id for the two tube types, "lab_station" for `station_unattended`,
"receiving" for `receiving_short_staffed` and `scanner_downtime`, a courier route id for the three
courier types, a unit id for `unit_surge` and `unit_batching_hold`, and an instrument name for
`instrument_down`. Two windows of one type on one target combine by the larger severity.

The realized windows are truth: they go to disruptions-rep-<r>.json and never to the observed side.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import rng

DAY_MIN = 1440
TYPES = ("tube_line_outage", "tube_line_degraded", "station_unattended", "receiving_short_staffed",
         "courier_missed_rounds", "courier_delayed", "unit_surge", "unit_batching_hold", "scanner_downtime",
         "instrument_down", "offsite_route_closed")
RESPONSES = ("hold_at_unit", "divert_to_courier", "stuck_in_system", "none")
STATION_TARGET = "lab_station"
RECEIVING_TARGET = "receiving"
# Disruption types that take servers from the receiving desk.
DESK_CAPACITY_TYPES = ("receiving_short_staffed", "instrument_down")
RANDOM_DOWNTIME_P90_RATIO = 3.0  # ASSUMED: a random instrument downtime's 90th percentile is 3 times its median


@dataclass(frozen=True)
class Window:
    disruption_id: str
    type: str
    target_id: str
    start: float
    end: float
    severity: float
    response: str
    noticed_at: float
    day: int
    source: str  # "scenario" or "random"


@dataclass(frozen=True)
class Merged:
    """Overlapping windows of one type on one target, combined."""
    start: float
    end: float
    severity: float
    ids: tuple[str, ...]
    noticed_at: float
    response: str


def clock_minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _scheduled(scenario: dict, days: int, t_end: float) -> list[Window]:
    out = []
    for d in scenario.get("disruptions", []):
        on_days = range(days) if d.get("recurs_daily") else ([d["day"]] if d["day"] < days else [])
        for day in on_days:
            start = day * DAY_MIN + clock_minutes(d["start"])
            if start >= t_end:
                continue
            out.append(Window(d["disruption_id"], d["type"], d["target_id"], float(start),
                              float(min(start + d["duration_min"], t_end)), float(d["severity"]),
                              d.get("response") or "none", float(start + d.get("noticed_after_min", 0)), day,
                              "scenario"))
    return out


def _random_downtimes(scenario: dict, streams: rng.Streams, days: int, t_end: float) -> list[Window]:
    out = []
    for i, inst in enumerate(scenario.get("processing", {}).get("instruments", [])):
        gen = streams.get("processing", 2, i)
        count = int(gen.poisson(inst["downtime_per_week"] * days / 7))
        starts = sorted(gen.uniform(0, days * DAY_MIN, count).tolist())
        z = gen.standard_normal(count).tolist()
        mu_sigma = rng.params(inst["downtime_median_min"], RANDOM_DOWNTIME_P90_RATIO * inst["downtime_median_min"])
        for j, (start, zj) in enumerate(zip(starts, z)):
            out.append(Window(f"{inst['name']}_random_{j + 1}", "instrument_down", inst["name"], start,
                              min(start + rng.lognormal(mu_sigma, zj), t_end), 1.0, "none", start,
                              int(start // DAY_MIN), "random"))
    return out


def realize(scenario: dict, streams: rng.Streams, days: int, t_end: float) -> "Windows":
    windows = _scheduled(scenario, days, t_end) + _random_downtimes(scenario, streams, days, t_end)
    return Windows(sorted(windows, key=lambda w: (w.start, w.disruption_id, w.day)))


class Windows:
    """Realized windows, indexed by (type, target)."""

    def __init__(self, windows: list[Window]):
        self.all = windows
        self._by: dict[tuple[str, str], list[Window]] = {}
        for w in windows:
            self._by.setdefault((w.type, w.target_id), []).append(w)
        self._merged: dict[tuple[str, str], list[Merged]] = {}

    def of(self, kind: str, target: str) -> list[Window]:
        return self._by.get((kind, target), [])

    def of_type(self, *kinds: str) -> list[Window]:
        return [w for w in self.all if w.type in kinds]

    def severity(self, kind: str, target: str, t: float) -> tuple[float, tuple[str, ...]]:
        """The largest severity of the windows active at t, and their ids."""
        sev, ids = 0.0, ()
        for w in self._by.get((kind, target), ()):
            if w.start <= t < w.end:
                sev, ids = max(sev, w.severity), ids + (w.disruption_id,)
        return sev, ids

    def active(self, kind: str, target: str, t: float) -> bool:
        return any(w.start <= t < w.end for w in self._by.get((kind, target), ()))

    def merged(self, kind: str, target: str) -> list[Merged]:
        key = (kind, target)
        if key not in self._merged:
            out: list[Merged] = []
            for w in sorted(self._by.get(key, ()), key=lambda w: w.start):
                if out and w.start <= out[-1].end:
                    m = out[-1]
                    out[-1] = Merged(m.start, max(m.end, w.end), max(m.severity, w.severity),
                                     m.ids + ((w.disruption_id,) if w.disruption_id not in m.ids else ()),
                                     min(m.noticed_at, w.noticed_at), m.response)
                else:
                    out.append(Merged(w.start, w.end, w.severity, (w.disruption_id,), w.noticed_at, w.response))
            self._merged[key] = out
        return self._merged[key]

    def covering(self, kind: str, target: str, t: float) -> Merged | None:
        for m in self.merged(kind, target):
            if m.start <= t < m.end:
                return m
        return None

    def overlap_ids(self, kinds: tuple[str, ...], t0: float, t1: float) -> set[str]:
        """Ids of every window of these types, on any target, that overlaps [t0, t1)."""
        return {w.disruption_id for w in self.all if w.type in kinds and w.start < t1 and w.end > t0}
