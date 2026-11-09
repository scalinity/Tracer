"""The laboratory's parts as the simulator sees them (spec §5.3 and §5.4).

build_world() reads one scenario configuration into plain objects: units, tube lines, courier
routes, the receiving desk, the processing bench and the specimen mix with each entry's governing
policy. The rest of this module holds the pieces that do not need the event loop:

- the shift clock, one per scenario from `receiving.shift_pattern`, which governs every day,
  evening and night value (receiving staff, station emptying, courier rounds);
- the receiving desk's server count over time (shift staff, half during the gap after each shift
  change, less during short staffing and while an instrument is down, never below one);
- the schedules of station emptyings, courier rounds and unit batch sweeps;
- arrivals, a non-homogeneous Poisson process drawn by thinning from a bound no surge can exceed;
- each candidate specimen's latent draws, indexed by its place in its unit's candidate stream;
- the tube leg, which needs only the line's disruption windows, so it is computed in one call.

The v1-world scenario (src/sim/v1world.py) carries a `v1world` block; where it is present a few
components follow v1 instead: one categorical fault per specimen, uniform stall and misroute delays,
misroutes landing at tube_station_pharmacy with no second transit, continuous station emptying (the
dwell is v1's lognormal) and an instant receipt scan at the desk.

Every timing constant here is an assumption, marked where it is defined.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from src.generator import briefs, config

from . import disruptions, rng

DAY_MIN = 1440
HORIZON_MIN = 7200  # ASSUMED (spec A-05): a specimen not received within 7,200 minutes is never received
SURGE_MAX = 4.0  # a surge multiplies a unit's rate by 1 + 3s with s <= 1, so 4 bounds every rate
ORDERED_SCAN_SHARE = 0.7  # ASSUMED: v1 allows an ordered scan; 7 specimens in 10 carry one
ORDERED_LEAD_MIN = (5.0, 60.0)  # ASSUMED: the order comes 5 to 60 minutes before the collection
WALK = (10.0, 30.0)  # ASSUMED: a walk-in's carry to receiving, median and 90th percentile, minutes
RUNNER_WALK = (8.0, 20.0)  # ASSUMED: the on-call runner's carry, median and 90th percentile, minutes
RUNNER_RESPONSE_P90_RATIO = 3.0  # ASSUMED: the runner's response 90th percentile is 3 times its median
DEFAULT_RUNNER_RESPONSE_MIN = 15.0  # ASSUMED: used when on_call_runner is set but no median is given
STATION_JITTER = 0.2  # ASSUMED: station emptying intervals vary by plus or minus 20%
ROUND_JITTER = 0.1  # ASSUMED: courier round intervals vary by plus or minus 10%
DEGRADED_TRANSIT = 3.0  # transit times (1 + 3s) on a degraded line (spec §5.5)
DEGRADED_FAULTS = 5.0  # ASSUMED: misroute and loss rates times (1 + 5s) on a degraded line
OUTAGE_LOSS = 20.0  # a stuck carrier is lost with min(0.5, lost_rate * (1 + 20s)) (spec §5.4)
OUTAGE_LOSS_CAP = 0.5
DELAYED_ROUND = 1.0  # a delayed round is late by s times the interval; its travel times (1 + s)

SHIFT_PATTERNS = {
    "7-15-23": ((7 * 60, "day"), (15 * 60, "evening"), (23 * 60, "night")),
    "6-14-22": ((6 * 60, "day"), (14 * 60, "evening"), (22 * 60, "night")),
    "7-19": ((7 * 60, "day"), (19 * 60, "night")),
}
WEEKEND_DAYS = (5, 6)  # day 0 is Monday, January 5, 2026; day -1 (the warm-up) is a Sunday

# Candidate latent columns, one array per stream and unit (see rng.py on common random numbers).
ATTR = ("mix", "stat", "route", "ordered", "order_lead", "collector", "receiver", "bench")
FAULT = ("never_collected", "not_dispatched", "content_failure", "lost_tube", "misroute", "stall",
         "lost_transit", "stuck_lost", "v1_fault", "v1_delay")
NORMAL = {"dispatch": ("dispatch",), "tube": ("transit", "transit2", "found"), "station": ("stall_extra", "dwell"),
          "courier": ("response", "runner_walk", "walk"), "receiving": ("service", "rescan"),
          "processing": ("accession", "plating")}
TUBE_FAULTS = ("lost_in_tube", "stalled_at_station", "misrouted")


@dataclass
class Unit:
    index: int
    unit_id: str
    kind: str
    share: float
    stat_fraction: float
    route: str
    tube_line_id: str | None
    courier_route_id: str | None
    tube_share: float
    batched: bool
    dispatch: tuple[float, float]
    batch_interval: float | None
    not_dispatched_rate: float
    mix_cdf: list[float]


@dataclass
class Line:
    line_id: str
    transit: tuple[float, float]
    misroute_rate: float
    lost_rate: float


@dataclass
class Route:
    index: int
    route_id: str
    kind: str
    intervals: dict
    travel: tuple[float, float]
    missed_rate: float
    lost_rate: float
    capacity: int


@dataclass
class MixEntry:
    key: str
    specimen_type: str
    tests: list[str]
    policy_id: str
    policy: dict


@dataclass
class World:
    days: int
    t_start: float
    t_arrivals_end: float
    t_end: float
    pattern: tuple
    units: list[Unit]
    lines: dict[str, Line]
    routes: dict[str, Route]
    mix: list[MixEntry]
    per_weekday: float
    profile: list[float]
    weekend_factor: float
    station_intervals: dict
    stalled_rate: float
    stall_extra: tuple[float, float]
    misroute_found: tuple[float, float]
    on_call_runner: bool
    runner_response: tuple[float, float]
    staff: dict
    gap: float
    service: tuple[float, float]
    accession: tuple[float, float]
    plating: tuple[float, float]
    pull: float
    never_collected_rate: float
    content_failure_rate: float
    walk: tuple[float, float]
    runner_walk: tuple[float, float] = field(default_factory=lambda: rng.params(*RUNNER_WALK))
    v1: dict | None = None


def _mp(d: dict, median: str = "median", p90: str = "p90") -> tuple[float, float]:
    return rng.params(d[median], d[p90])


def resolve_mix(loaded: dict) -> list[MixEntry]:
    """Each mix entry of config/config.yaml with its tests and governing policy (as v1 resolves it)."""
    policy = loaded["policy"]
    out = []
    for key, entry in loaded["config"]["specimen_mix"]["entries"].items():
        rows = [policy["policies"][pid] for pid in entry["policies"]]
        tests = [row["test"] for row in rows]
        pid, governing = config.resolve(policy, rows[0]["specimen_type"], tests)
        for state in (governing["clock_start"], governing["clock_stop"]):
            if state not in briefs.TIMED_STATES:
                raise config.PolicyError(f"policy {pid}: the simulator does not time state {state!r}")
        out.append(MixEntry(key, rows[0]["specimen_type"], tests, pid, governing))
    return out


def _cdf(weights: list[float]) -> list[float]:
    total = sum(weights)
    if total <= 0:
        raise ValueError("specimen mix weights must not all be zero")
    acc, out = 0.0, []
    for w in weights:
        acc += w / total
        out.append(acc)
    out[-1] = 1.0
    return out


def build_world(scenario: dict, loaded: dict, days: int) -> World:
    mix = resolve_mix(loaded)
    keys = [m.key for m in mix]
    unknown = sorted(set(scenario["specimen_mix"]) - set(keys))
    if unknown:
        raise ValueError(f"specimen_mix has keys that config/config.yaml does not: {unknown}")
    base = [float(scenario["specimen_mix"].get(k, 0.0)) for k in keys]
    shares = [u["volume_share"] for u in scenario["units"]]
    units = []
    for i, u in enumerate(scenario["units"]):
        tilt = {t["key"]: t["multiplier"] for t in u.get("mix_tilt", [])}
        d = u["dispatch"]
        units.append(Unit(
            i, u["unit_id"], u["kind"], u["volume_share"] / sum(shares), u["stat_fraction"], u["route"],
            u.get("tube_line_id"), u.get("courier_route_id"), u.get("tube_share_if_mixed") or 0.0,
            d["mode"] == "batched", rng.params(d["median_min"], d["p90_min"]), d.get("batch_interval_min"),
            u.get("not_dispatched_rate", 0.0), _cdf([b * tilt.get(k, 1.0) for b, k in zip(base, keys)])))
    tube = scenario.get("tube_system") or {}
    lines = {ln["line_id"]: Line(ln["line_id"], rng.params(ln["transit_median_min"], ln["transit_p90_min"]),
                                 ln["misroute_rate"], ln["lost_rate"]) for ln in tube.get("lines", [])}
    couriers = scenario.get("couriers") or {}
    routes = {r["route_id"]: Route(i, r["route_id"], r["kind"], r["round_interval_min"],
                                   rng.params(r["travel_median_min"], r["travel_p90_min"]), r["missed_round_rate"],
                                   r["lost_rate"], int(r["capacity_per_round"]))
              for i, r in enumerate(couriers.get("routes", []))}
    for u in units:
        if u.route in ("tube", "mixed") and u.tube_line_id not in lines:
            raise ValueError(f"unit {u.unit_id} sends by tube but line {u.tube_line_id!r} does not exist")
        if u.route == "courier" and u.courier_route_id not in routes:
            raise ValueError(f"unit {u.unit_id} sends by courier but route {u.courier_route_id!r} does not exist")
    rec, proc, faults = scenario["receiving"], scenario["processing"], scenario.get("independent_faults") or {}
    if any(n < 1 for shifts in rec["staff_by_shift"].values() for n in shifts.values()):
        raise ValueError("every shift needs at least one receiving staff member (spec §13)")
    response = couriers.get("on_call_response_median_min") or DEFAULT_RUNNER_RESPONSE_MIN
    profile = [float(x) for x in scenario["volume"]["hourly_profile"]]
    v1 = scenario.get("v1world")
    t_arrivals_end = float((days + 1) * DAY_MIN)
    return World(
        days=days, t_start=-float(DAY_MIN), t_arrivals_end=t_arrivals_end, t_end=t_arrivals_end + HORIZON_MIN,
        pattern=SHIFT_PATTERNS[rec["shift_pattern"]], units=units, lines=lines, routes=routes, mix=mix,
        per_weekday=float(scenario["volume"]["specimens_per_weekday"]),
        profile=[p / sum(profile) for p in profile], weekend_factor=float(scenario["volume"]["weekend_factor"]),
        station_intervals=tube.get("station_empty_interval_min") or {"day": 10, "evening": 10, "night": 10},
        stalled_rate=tube.get("stalled_rate", 0.0),
        stall_extra=_mp(tube.get("stall_extra_min") or {"median": 30, "p90": 90}),
        misroute_found=_mp(tube.get("misroute_found_min") or {"median": 30, "p90": 90}),
        on_call_runner=bool(couriers.get("on_call_runner")),
        runner_response=rng.params(response, RUNNER_RESPONSE_P90_RATIO * response),
        staff=rec["staff_by_shift"], gap=float(rec.get("shift_change_gap_min", 0)), service=_mp(rec["scan_service_min"]),
        accession=_mp(proc["accession_min"]), plating=_mp(proc["plating_min"]),
        pull=float(proc.get("staff_pull_from_receiving", 0.0)),
        never_collected_rate=faults.get("never_collected_rate", 0.0),
        content_failure_rate=faults.get("content_failure_rate", 0.0),
        walk=rng.params(*(v1["hand_carry_transit"] if v1 else WALK)), v1=v1)


# --- The shift clock -------------------------------------------------------------------------------

def shift_at(pattern: tuple, t: float) -> tuple[str, float, str]:
    """(shift name, the minute it started, day type) at minute t. A shift belongs to the day it started on."""
    day = math.floor(t / DAY_MIN)
    minute = t - day * DAY_MIN
    starts = [s for s, _ in pattern]
    idx = sum(1 for s in starts if s <= minute) - 1
    if idx < 0:
        day, idx = day - 1, len(pattern) - 1
    start = day * DAY_MIN + pattern[idx][0]
    return pattern[idx][1], float(start), "weekend" if day % 7 in WEEKEND_DAYS else "weekday"


def shift_boundaries(pattern: tuple, t0: float, t1: float) -> list[float]:
    out = []
    for day in range(math.floor(t0 / DAY_MIN) - 1, math.ceil(t1 / DAY_MIN) + 1):
        out += [float(day * DAY_MIN + s) for s, _ in pattern if t0 <= day * DAY_MIN + s < t1]
    return sorted(out)


def next_boundary(pattern: tuple, t: float) -> float:
    return shift_boundaries(pattern, t + 1e-9, t + 2 * DAY_MIN)[0]


def scheduled_staff(world: World, t: float) -> int:
    name, _, day_type = shift_at(world.pattern, t)
    return int(world.staff[day_type][name])


def _servers(x: float) -> int:
    return max(1, int(math.floor(x + 0.5)))


def desk_capacity(world: World, wins: disruptions.Windows, t: float) -> tuple[int, int, tuple[str, ...]]:
    """Receiving servers at minute t: staff, halved in a shift-change gap, less for short staffing and
    while any instrument is down, rounded to the nearest whole server and never below one.

    Returns (servers, servers without any disruption, ids of the disruptions taking servers away).
    """
    name, start, day_type = shift_at(world.pattern, t)
    servers = float(world.staff[day_type][name])
    if t - start < world.gap:
        servers *= 0.5
    scheduled = _servers(servers)
    short, ids = wins.severity("receiving_short_staffed", disruptions.RECEIVING_TARGET, t)
    servers *= 1 - short
    down = tuple(w.disruption_id for w in wins.of_type("instrument_down") if w.start <= t < w.end)
    if down:
        servers *= 1 - world.pull
    cap = _servers(servers)
    return cap, scheduled, (ids + down) if cap < scheduled else ()


def capacity_timeline(world: World, wins: disruptions.Windows) -> list[tuple[float, int, tuple[str, ...]]]:
    """(minute, servers, ids of disruptions taking servers away) at every change, warm-up to horizon."""
    points = set(shift_boundaries(world.pattern, world.t_start, world.t_end))
    points |= {b + world.gap for b in points if world.gap > 0}
    for w in wins.of_type(*disruptions.DESK_CAPACITY_TYPES):
        points |= {w.start, w.end}
    points.add(world.t_start)
    out: list[tuple[float, int, tuple[str, ...]]] = []
    for p in sorted(p for p in points if world.t_start <= p < world.t_end):
        cap, _, ids = desk_capacity(world, wins, p)
        if not out or out[-1][1:] != (cap, ids):
            out.append((p, cap, ids))
    return out


# --- Schedules -------------------------------------------------------------------------------------

def emptying_times(world: World, wins: disruptions.Windows, gen: np.random.Generator) -> list[float]:
    """Laboratory tube station emptyings: every interval of the current shift, plus or minus 20%,
    skipped while the station is unattended. The desk never has fewer than one server, so the
    condition that someone is on duty to empty the station always holds."""
    shortest = min(v for v in world.station_intervals.values() if v)
    n = int((world.t_end - world.t_start) / (shortest * (1 - STATION_JITTER))) + 3
    u = gen.random(n + 1).tolist()
    unattended = wins.merged("station_unattended", disruptions.STATION_TARGET)
    out, k = [], 1
    t = world.t_start + u[0] * world.station_intervals[shift_at(world.pattern, world.t_start)[0]]
    while t < world.t_end and k <= n:
        if not any(m.start <= t < m.end for m in unattended):
            out.append(t)
        t += world.station_intervals[shift_at(world.pattern, t)[0]] * (1 + STATION_JITTER * (2 * u[k] - 1))
        k += 1
    return out


@dataclass
class Round:
    route: Route
    index: int
    base: float
    time: float
    status: str  # "run", "missed" or "closed"
    travel: float
    ids: tuple[str, ...]  # disruptions that missed, delayed or closed this round
    by_disruption: bool  # True when a disruption (not the base rate) caused a miss


def round_schedule(world: World, wins: disruptions.Windows, route: Route, gen: np.random.Generator) -> list[Round]:
    """One route's rounds: every interval of the current shift with a per-route phase and plus or minus
    10% jitter, none in a shift whose interval is null; then missed, delayed or closed by disruptions."""
    present = [v for v in route.intervals.values() if v]
    if not present:
        return []
    n = int((world.t_end - world.t_start) / (min(present) * (1 - ROUND_JITTER))) + 3
    phase = float(gen.random())
    jitter, miss, travel_z = gen.random(n).tolist(), gen.random(n).tolist(), gen.standard_normal(n).tolist()
    out: list[Round] = []
    t, k = world.t_start, 0
    first = route.intervals[shift_at(world.pattern, t)[0]]
    t += phase * (first or 0)
    while t < world.t_end and k < n:
        interval = route.intervals[shift_at(world.pattern, t)[0]]
        if not interval:
            t = next_boundary(world.pattern, t)
            nxt = route.intervals[shift_at(world.pattern, t)[0]]
            t += phase * (nxt or 0)
            continue
        out.append(_round(wins, route, k, t, interval, miss[k], travel_z[k]))
        t += interval * (1 + ROUND_JITTER * (2 * jitter[k] - 1))
        k += 1
    return out


def _round(wins: disruptions.Windows, route: Route, k: int, t: float, interval: float, u_miss: float,
           z_travel: float) -> Round:
    closed, closed_ids = wins.severity("offsite_route_closed", route.route_id, t)
    if closed_ids:
        return Round(route, k, t, t, "closed", 0.0, closed_ids, True)
    s_miss, miss_ids = wins.severity("courier_missed_rounds", route.route_id, t)
    p = max(route.missed_rate, s_miss) if miss_ids else route.missed_rate
    s_late, late_ids = wins.severity("courier_delayed", route.route_id, t)
    travel = rng.lognormal(route.travel, z_travel) * (1 + DELAYED_ROUND * s_late)
    when = t + DELAYED_ROUND * s_late * interval
    if u_miss < p:
        by_disruption = u_miss >= route.missed_rate
        return Round(route, k, t, when, "missed", travel, miss_ids if by_disruption else (), by_disruption)
    return Round(route, k, t, when, "run", travel, late_ids, False)


def sweep_phase(unit: Unit, gen: np.random.Generator) -> float:
    return float(gen.random()) * (unit.batch_interval or 0)


def next_sweep(phase: float, interval: float, t: float) -> float:
    return phase + math.ceil((t - phase) / interval - 1e-12) * interval


# --- Arrivals and candidate draws ------------------------------------------------------------------

def base_rate(world: World, unit: Unit, t: np.ndarray) -> np.ndarray:
    """Specimens per minute at each time, before any surge."""
    day = np.floor(t / DAY_MIN)
    hour = np.floor((t - day * DAY_MIN) / 60).astype(int)
    weekend = np.isin(np.mod(day, 7), WEEKEND_DAYS)
    hourly = np.asarray(world.profile)[hour] * world.per_weekday * unit.share / 60
    return hourly * np.where(weekend, world.weekend_factor, 1.0)


def rate_bound(world: World, unit: Unit) -> float:
    return world.per_weekday * unit.share * max(world.profile) / 60 * max(1.0, world.weekend_factor) * SURGE_MAX


def candidates(world: World, wins: disruptions.Windows, unit: Unit, gen: np.random.Generator) -> dict:
    """The unit's candidate arrivals over the warm-up, scored and warm-down days, thinned to its rate.

    Returns the candidate times, which are kept, and each kept candidate's surge window ids.
    """
    bound = rate_bound(world, unit)
    span = world.t_arrivals_end - world.t_start
    n = int(gen.poisson(bound * span))
    times = np.sort(gen.uniform(world.t_start, world.t_arrivals_end, n))
    u = gen.random(n)
    surge = np.zeros(n)
    surge_ids = [()] * n
    for w in wins.of("unit_surge", unit.unit_id):
        inside = (times >= w.start) & (times < w.end)
        surge = np.where(inside, np.maximum(surge, w.severity), surge)
        for k in np.flatnonzero(inside).tolist():
            surge_ids[k] = surge_ids[k] + (w.disruption_id,)
    keep = u * bound < base_rate(world, unit, times) * (1 + 3 * surge)
    return {"times": times, "keep": keep, "surge_ids": surge_ids, "n": n}


def candidate_draws(streams: rng.Streams, unit: Unit, n: int) -> dict[str, np.ndarray]:
    """Every latent draw of each of the unit's n candidates, by candidate position."""
    draws = {"attributes": streams.get("attributes", 0, unit.index).random((n, len(ATTR))),
             "faults": streams.get("faults", 0, unit.index).random((n, len(FAULT)))}
    for name, cols in NORMAL.items():
        draws[name] = streams.get(name, 0, unit.index).standard_normal((n, len(cols)))
    return draws


def draw_mix(cdf: list[float], u: float) -> int:
    for i, c in enumerate(cdf):
        if u < c:
            return i
    return len(cdf) - 1


def v1_fault(v1: dict, u: float) -> str:
    """v1's one categorical fault per specimen."""
    acc = 0.0
    names = list(v1["faults"])
    for name in names:
        acc += v1["faults"][name]
        if u < acc:
            return name
    return names[0]


# --- The tube leg ----------------------------------------------------------------------------------

def _transit(wins: disruptions.Windows, line: Line, t: float, z: float, touched: set) -> tuple[list, float, float, float | None]:
    """One transit starting at t: parts (substate, start, end), arrival, the worst outage severity it
    was stuck in, and when its first stuck period ended."""
    s_deg, deg_ids = wins.severity("tube_line_degraded", line.line_id, t)
    touched.update(deg_ids)
    remaining = rng.lognormal(line.transit, z) * (1 + DEGRADED_TRANSIT * s_deg)
    parts, cur, worst, first_end = [], t, 0.0, None
    for o in wins.merged("tube_line_outage", line.line_id):
        if o.end <= cur:
            continue
        if o.start >= cur + remaining:
            break
        if o.start > cur:
            parts.append(("transit", cur, o.start))
            remaining -= o.start - cur
            cur = o.start
        parts.append(("stuck", cur, o.end))
        worst = max(worst, o.severity)
        first_end = o.end if first_end is None else first_end
        touched.update(o.ids)
        cur = o.end
    parts.append(("transit", cur, cur + remaining))
    return parts, cur + remaining, worst, first_end


def tube_leg(world: World, wins: disruptions.Windows, line: Line, unit_id: str, t: float, lat: dict,
             fault: str | None, touched: set) -> tuple[list, float, str | None]:
    """Segments from sending at t to the laboratory tube station.

    Returns (segments, minute of arrival or loss, fault): fault is "lost_in_tube", "misrouted" or None.
    `fault` in the arguments is the v1 world's categorical fault (None outside the v1 world).
    """
    loc = f"tube_station_{unit_id}"
    parts, arrive, worst, first_end = _transit(wins, line, t, lat["transit"], touched)
    segs = [("in_tube", sub, a, b, loc) for sub, a, b in parts if b > a]
    if worst > 0 and lat["stuck_lost"] < min(OUTAGE_LOSS_CAP, line.lost_rate * (1 + OUTAGE_LOSS * worst)):
        kept = [s for s in segs if s[3] <= first_end]
        return kept + [("lost", "in_tube", first_end, None, loc)], first_end, "lost_in_tube"
    s_deg, _ = wins.severity("tube_line_degraded", line.line_id, t)
    raise_by = 1 + DEGRADED_FAULTS * s_deg
    if world.v1:
        lost, misrouted = fault == "lost_in_tube", fault == "misrouted"
    else:
        lost = lat["lost_tube"] < line.lost_rate * raise_by
        misrouted = not lost and lat["misroute"] < line.misroute_rate * raise_by
    if lost:
        return segs + [("lost", "in_tube", arrive, None, loc)], arrive, "lost_in_tube"
    if not misrouted:
        return segs, arrive, None
    if world.v1:
        lo, hi = world.v1["delays"]["misrouted"]
        found = arrive + lo + lat["v1_delay"] * (hi - lo)
        return segs + [("misrouted", "wrong_station", arrive, found, world.v1["misroute_station"])], found, "misrouted"
    station = "tube_station_misroute"
    found = arrive + rng.lognormal(world.misroute_found, lat["found"])
    segs.append(("misrouted", "wrong_station", arrive, found, station))
    parts, arrive2, _, _ = _transit(wins, line, found, lat["transit2"], touched)
    segs += [("misrouted", "wrong_station", a, b, station) for _, a, b in parts if b > a]
    return segs, arrive2, "misrouted"
