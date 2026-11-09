"""The discrete-event engine: one scenario and one replicate in, one simulated week out (spec §5).

    from src.sim import engine
    result = engine.simulate(scenario, scenario_index=12, replicate=1)

Events sit in a heap ordered by (minute, priority class, sequence number), so equal times resolve
the same way on every run: desk capacity changes and scanner restarts first, then service
completions (servers free up before anyone starts), then station emptyings and courier rounds, then
arrivals at the station or desk, then collections. The same scenario, index and replicate give
byte-identical records.

A specimen's own legs that touch nothing shared (dispatch waits, the tube, walks and the on-call
runner, everything after receipt) are computed when it reaches them; the shared resources are
simulated event by event: the laboratory tube station empties on a schedule, courier rounds pick up
what is waiting at their units up to their capacity, and the receiving desk is a multi-server queue,
stat first, whose server count follows the staffing clock. The simulation stops once every specimen
collected before the warm-down day ends is received or lost, or at the horizon.

simulate() returns a SimResult: `records` (v1-shaped {brief, trail} dicts with the v2 block, traffic
included and marked scored false), `roster` (what the laboratory knows about itself), `disruptions`
(the realized windows, truth side) and `stats` (counts, the desk, station and courier traces that
src/sim/validate_v2.py checks, and wall time).
"""

from __future__ import annotations

import heapq
import time
from dataclasses import dataclass

from src.generator import config

from . import disruptions, processes, records, rng

# Priority classes for events at equal times.
CAPACITY, SERVICE_DONE, SCHEDULED, ARRIVAL, COLLECTION = 0, 1, 2, 3, 4
CONGESTION_WAIT_MIN = 10.0  # ASSUMED: a wait this long for a receiving server counts as congestion
LAB_STATION = "tube_station_lab"
LAB_RECEIVING = "lab_receiving"
LATENT_NAMES = (processes.ATTR + processes.FAULT
                + tuple(c for cols in processes.NORMAL.values() for c in cols))


@dataclass
class SimResult:
    records: list[dict]
    roster: dict
    disruptions: list[dict]
    stats: dict


class EventQueue:
    """A heap of (minute, priority class, sequence number, kind, payload)."""

    def __init__(self) -> None:
        self._heap: list[tuple] = []
        self._seq = 0

    def push(self, t: float, cls: int, kind: str, payload=None) -> None:
        heapq.heappush(self._heap, (t, cls, self._seq, kind, payload))
        self._seq += 1

    def pop(self) -> tuple:
        return heapq.heappop(self._heap)

    def __len__(self) -> int:
        return len(self._heap)

    @property
    def pushed(self) -> int:
        """Events pushed so far."""
        return self._seq


class Specimen:
    __slots__ = ("seq", "unit", "k", "c", "scored", "mix", "stat", "route_kind", "route_id", "via", "v1_fault",
                 "never_collected", "not_dispatched", "content_failure", "lat", "segs", "touched", "faults",
                 "missed_base", "congested", "bumped", "wait", "station_arrive", "desk_arrive", "service_start",
                 "service_end", "received", "accessioned", "plated", "rejected", "ordered_at", "terminal", "n")

    def __init__(self, seq: int, unit: processes.Unit, k: int, c: float, scored: bool, lat: dict):
        self.seq, self.unit, self.k, self.c, self.scored, self.lat = seq, unit, k, c, scored, lat
        self.segs: list[tuple] = []
        self.touched: set[str] = set()
        self.faults: set[str] = set()
        self.missed_base = self.congested = self.bumped = self.terminal = False
        self.via = "tube"
        self.wait = self.station_arrive = self.desk_arrive = self.service_start = self.service_end = None
        self.received = self.accessioned = self.plated = self.rejected = self.ordered_at = None
        self.v1_fault = None
        self.n = 0

    @property
    def unit_loc(self) -> str:
        return f"{self.unit.unit_id}_collection"

    @property
    def carry_state(self) -> str:
        """The truth state while waiting at the desk: at_station after the tube, hand_carry otherwise."""
        return "at_station" if self.via == "tube" else "hand_carry"


class Sim:
    def __init__(self, world: processes.World, wins: disruptions.Windows, streams: rng.Streams):
        self.world, self.wins, self.streams = world, wins, streams
        self.q = EventQueue()
        self.specimens: list[Specimen] = []
        self.station: list[Specimen] = []
        self.waiting: dict[str, list] = {r: [] for r in world.routes}
        self.desk: list[tuple] = []
        self.backlog: list[Specimen] = []
        self.scanner_ends: set[float] = set()
        self.busy = 0
        self.capacity = 1
        self.pending = 0
        self.arrivals_left = 0
        self.sweep_phase: dict[int, float] = {}
        self.instrument_windows = sorted(wins.of_type("instrument_down"), key=lambda w: w.start)
        self.unattended = wins.merged("station_unattended", disruptions.STATION_TARGET)
        self.v1_dwell = rng.params(*world.v1["station_dwell"]) if world.v1 else None
        self.cap_trace: list[tuple[float, int]] = []
        self.busy_trace: list[tuple[float, int]] = []
        self.reduced: list[tuple[float, float, tuple[str, ...]]] = []
        self.emptyings: list[float] = []
        self.visits: list[tuple] = []
        self.rounds: list[dict] = []
        self.handlers = {"collect": self.collect, "station_arrive": self.station_arrive, "empty": self.empty,
                         "round": self.courier_round, "desk_arrive": self.desk_arrive,
                         "service_done": self.service_done, "capacity": self.set_capacity,
                         "scanner_end": self.scanner_end}

    # --- set-up ------------------------------------------------------------------------------------

    def schedule(self, keep_draws: bool) -> dict:
        world, wins = self.world, self.wins
        timeline = processes.capacity_timeline(world, wins)
        for i, (t, cap, ids) in enumerate(timeline):
            self.q.push(t, CAPACITY, "capacity", cap)
            if ids:
                end = timeline[i + 1][0] if i + 1 < len(timeline) else world.t_end
                self.reduced.append((t, end, ids))
        if not world.v1:
            for t in processes.emptying_times(world, wins, self.streams.get("jitter", 0)):
                self.q.push(t, SCHEDULED, "empty")
        for route in world.routes.values():
            for rd in processes.round_schedule(world, wins, route, self.streams.get("courier", 1, route.index)):
                self.q.push(rd.time, SCHEDULED, "round", rd)
        draws: dict[int, tuple] = {}
        found = []
        for unit in world.units:
            if unit.batched:
                self.sweep_phase[unit.index] = processes.sweep_phase(unit, self.streams.get("jitter", 1, unit.index))
            cand = processes.candidates(world, wins, unit, self.streams.get("arrivals", 0, unit.index))
            lat = processes.candidate_draws(self.streams, unit, cand["n"])
            idx = cand["keep"].nonzero()[0]
            columns = [lat["attributes"][idx].tolist(), lat["faults"][idx].tolist()]
            columns += [lat[name][idx].tolist() for name in processes.NORMAL]
            times = cand["times"][idx].tolist()
            for j, k in enumerate(idx.tolist()):
                row = [v for col in columns for v in col[j]]
                found.append((times[j], unit.index, k, row, cand["surge_ids"][k]))
        found.sort(key=lambda f: (f[0], f[1], f[2]))
        scored_end = world.days * processes.DAY_MIN
        for seq, (c, ui, k, row, surge_ids) in enumerate(found):
            sp = Specimen(seq, world.units[ui], k, c, 0 <= c < scored_end, dict(zip(LATENT_NAMES, row)))
            sp.touched.update(surge_ids)
            self.attributes(sp)
            sp.n = seq + 1
            self.specimens.append(sp)
            self.q.push(c, COLLECTION, "collect", sp)
            if keep_draws:
                draws[sp.n] = tuple(row)
        self.arrivals_left = len(self.specimens)
        return draws

    def attributes(self, sp: Specimen) -> None:
        """What is fixed at collection: mix entry, priority, route, faults drawn up front, the ordered scan."""
        world, unit, lat = self.world, sp.unit, sp.lat
        sp.mix = world.mix[processes.draw_mix(unit.mix_cdf, lat["mix"])]
        sp.stat = lat["stat"] < unit.stat_fraction
        if world.v1:
            sp.v1_fault = processes.v1_fault(world.v1, lat["v1_fault"])
        route = unit.route
        if route == "mixed":
            forced = sp.v1_fault in processes.TUBE_FAULTS
            route = "tube" if forced or lat["route"] < unit.tube_share else \
                ("courier" if unit.courier_route_id in world.routes else "walk_in")
        sp.route_kind = route
        sp.route_id = {"tube": unit.tube_line_id, "courier": unit.courier_route_id}.get(route, "walk_in")
        if world.v1:
            sp.never_collected = sp.v1_fault == "never_collected"
            sp.not_dispatched = sp.v1_fault == "not_dispatched"
            sp.content_failure = sp.v1_fault == "content_failure"
        else:
            sp.never_collected = lat["never_collected"] < world.never_collected_rate
            sp.not_dispatched = lat["not_dispatched"] < unit.not_dispatched_rate
            sp.content_failure = lat["content_failure"] < world.content_failure_rate
        if not sp.never_collected and lat["ordered"] < processes.ORDERED_SCAN_SHARE:
            lo, hi = processes.ORDERED_LEAD_MIN
            sp.ordered_at = sp.c - (lo + lat["order_lead"] * (hi - lo))

    # --- the event loop ----------------------------------------------------------------------------

    def run(self) -> None:
        q, t_end, handlers = self.q, self.world.t_end, self.handlers
        while len(q):
            t, _, _, kind, payload = q.pop()
            if t > t_end:
                break
            handlers[kind](t, payload)
            if self.arrivals_left == 0 and self.pending == 0:
                break
        for sp in self.specimens:
            if not sp.terminal and sp.received is None and sp.wait is not None:
                state, sub, since, loc = sp.wait
                sp.segs.append((state, sub, since, None, loc))

    def collect(self, t: float, sp: Specimen) -> None:
        self.arrivals_left -= 1
        if sp.never_collected:
            sp.faults.add("never_collected")
            sp.terminal = True
            return
        if sp.not_dispatched:
            sp.faults.add("not_dispatched")
            sp.segs.append(("at_unit", "not_dispatched", t, None, sp.unit_loc))
            sp.terminal = True
            return
        d = self.dispatch_time(sp, t + rng.lognormal(sp.unit.dispatch, sp.lat["dispatch"]))
        sp.segs.append(("at_unit", "pre_dispatch", t, d, sp.unit_loc))
        self.pending += 1
        if sp.route_kind == "tube":
            self.send_tube(sp, d)
        elif sp.route_kind == "courier":
            self.to_courier(sp, d, self.world.routes[sp.route_id])
        else:
            self.walk(sp, d)

    def dispatch_time(self, sp: Specimen, ready: float) -> float:
        """When the unit sends it: after its own wait, at the next sweep for a batching unit or under a
        batching hold (stat specimens skip both)."""
        unit = sp.unit
        if sp.stat:
            return ready
        for m in self.wins.merged("unit_batching_hold", unit.unit_id):
            if m.start <= ready < m.end:
                sp.touched.update(m.ids)
                interval = 30 + 150 * m.severity
                sweep = processes.next_sweep(m.start, interval, ready)
                if sweep < m.end:
                    return sweep
                ready = m.end
        if unit.batched:
            return processes.next_sweep(self.sweep_phase[unit.index], unit.batch_interval, ready)
        return ready

    def send_tube(self, sp: Specimen, d: float) -> None:
        world = self.world
        line = world.lines[sp.route_id]
        outage = self.wins.covering("tube_line_outage", line.line_id, d)
        if outage is not None and d >= outage.noticed_at:
            sp.touched.update(outage.ids)
            response = outage.response
            if response == "divert_to_courier":
                if sp.unit.courier_route_id in world.routes:
                    sp.via = "hand"
                    self.to_courier(sp, d, world.routes[sp.unit.courier_route_id])
                    return
                if world.on_call_runner:
                    self.runner(sp, d)
                    return
                response = "hold_at_unit"
            if response == "hold_at_unit":
                sp.segs.append(("at_unit", "outage_hold", d, outage.end, sp.unit_loc))
                d = outage.end
        segs, t_next, fault = processes.tube_leg(world, self.wins, line, sp.unit.unit_id, d, sp.lat, sp.v1_fault,
                                                 sp.touched)
        sp.segs += segs
        if fault:
            sp.faults.add(fault)
        if fault == "lost_in_tube":
            self.lose(sp)
        elif world.v1:
            self.v1_station(sp, t_next)
        else:
            self.q.push(t_next, ARRIVAL, "station_arrive", sp)

    def v1_station(self, sp: Specimen, t: float) -> None:
        """v1's station: continuous emptying, so the dwell is v1's lognormal; a stall is v1's uniform delay."""
        v1 = self.world.v1
        dwell = t + rng.lognormal(self.v1_dwell, sp.lat["dwell"])
        sp.segs.append(("at_station", "lab_station", t, dwell, LAB_STATION))
        if sp.v1_fault == "stalled_at_station":
            lo, hi = v1["delays"]["stalled_at_station"]
            end = dwell + lo + sp.lat["v1_delay"] * (hi - lo)
            sp.segs.append(("stalled_at_station", "overlooked", dwell, end, LAB_STATION))
            sp.faults.add("stalled_at_station")
            dwell = end
        self.desk_arrive(dwell, sp)

    def to_courier(self, sp: Specimen, d: float, route: processes.Route) -> None:
        if sp.stat and self.world.on_call_runner and route.kind != "offsite_van":
            self.runner(sp, d)
            return
        sp.via = "hand"
        sp.wait = ("at_unit", "awaiting_pickup", d, sp.unit_loc)
        heapq.heappush(self.waiting[route.route_id], (d, sp.seq, sp))

    def runner(self, sp: Specimen, d: float) -> None:
        sp.via = "hand"
        pickup = d + rng.lognormal(self.world.runner_response, sp.lat["response"])
        arrive = pickup + rng.lognormal(self.world.runner_walk, sp.lat["runner_walk"])
        sp.segs.append(("at_unit", "awaiting_pickup", d, pickup, sp.unit_loc))
        sp.segs.append(("hand_carry", "in_transit", pickup, arrive, "courier_on_call"))
        self.q.push(arrive, ARRIVAL, "desk_arrive", sp)

    def walk(self, sp: Specimen, d: float) -> None:
        sp.via = "hand"
        arrive = d + rng.lognormal(self.world.walk, sp.lat["walk"])
        sp.segs.append(("hand_carry", "walk", d, arrive, sp.unit_loc))
        self.q.push(arrive, ARRIVAL, "desk_arrive", sp)

    def lose(self, sp: Specimen) -> None:
        sp.terminal = True
        sp.wait = None
        self.pending -= 1

    def station_arrive(self, t: float, sp: Specimen) -> None:
        sp.station_arrive = t
        sp.wait = ("at_station", "lab_station", t, LAB_STATION)
        self.station.append(sp)

    def empty(self, t: float, _=None) -> None:
        self.emptyings.append(t)
        present, self.station = self.station, []
        for sp in present:
            sp.segs += self.station_segments(sp, sp.station_arrive, t)
            sp.wait = None
            if sp.lat["stall"] < self.world.stalled_rate:
                leave = t + rng.lognormal(self.world.stall_extra, sp.lat["stall_extra"])
                sp.segs.append(("stalled_at_station", "overlooked", t, leave, LAB_STATION))
                sp.faults.add("stalled_at_station")
                self.visits.append((sp, sp.station_arrive, t, leave))
                self.q.push(leave, ARRIVAL, "desk_arrive", sp)
            else:
                self.visits.append((sp, sp.station_arrive, t, t))
                self.desk_arrive(t, sp)

    def station_segments(self, sp: Specimen, a: float, e: float) -> list[tuple]:
        """Waiting at the laboratory station from a to the emptying at e, split where it was unattended."""
        out, cur = [], a
        for m in self.unattended:
            if m.end <= cur or m.start >= e:
                continue
            if m.start > cur:
                out.append(("at_station", "lab_station", cur, m.start, LAB_STATION))
                cur = m.start
            end = min(m.end, e)
            out.append(("stalled_at_station", "unattended", cur, end, LAB_STATION))
            sp.touched.update(m.ids)
            cur = end
        if e > cur:
            out.append(("at_station", "lab_station", cur, e, LAB_STATION))
        return out

    def courier_round(self, t: float, rd: processes.Round) -> None:
        route = rd.route
        heap = self.waiting[route.route_id]
        if rd.status != "run":
            for d, _, sp in heap:
                if d <= t:
                    if rd.by_disruption:
                        sp.touched.update(rd.ids)
                    else:
                        sp.missed_base = True
            self.rounds.append({"route": route.route_id, "base": rd.base, "time": t, "status": rd.status,
                                "picked": 0, "capacity": route.capacity})
            return
        picked = []
        while heap and heap[0][0] <= t and len(picked) < route.capacity:
            picked.append(heapq.heappop(heap)[2])
        for d, _, sp in heap:
            if d <= t:
                sp.bumped = True
        loc = f"courier_{route.route_id}"
        for sp in picked:
            _, _, d, _ = sp.wait
            sp.wait = None
            sp.segs.append(("at_unit", "awaiting_pickup", d, t, sp.unit_loc))
            sp.touched.update(rd.ids)
            arrive = t + rd.travel
            sp.segs.append(("hand_carry", "in_transit", t, arrive, loc))
            if sp.lat["lost_transit"] < route.lost_rate:
                sp.segs.append(("lost", "in_transit", arrive, None, loc))
                sp.faults.add("lost_in_transit")
                self.lose(sp)
            else:
                self.q.push(arrive, ARRIVAL, "desk_arrive", sp)
        self.rounds.append({"route": route.route_id, "base": rd.base, "time": t, "status": rd.status,
                            "picked": len(picked), "capacity": route.capacity})

    def desk_arrive(self, t: float, sp: Specimen) -> None:
        sp.desk_arrive = t
        if self.world.v1:  # ample receiving: the scan is instant
            self.receive(t, sp)
            return
        sp.wait = (sp.carry_state, "receiving_queue", t, LAB_RECEIVING)
        heapq.heappush(self.desk, (0 if sp.stat else 1, t, sp.seq, sp))
        self.start_services(t)

    def start_services(self, t: float) -> None:
        started = False
        while self.busy < self.capacity and self.desk:
            _, arrived, _, sp = heapq.heappop(self.desk)
            self.busy += 1
            started = True
            sp.service_start = t
            if t > arrived:
                for a, b, ids in self.reduced:
                    if a < t and b > arrived:
                        sp.touched.update(ids)
                if t - arrived >= CONGESTION_WAIT_MIN:
                    sp.congested = True
            self.q.push(t + rng.lognormal(self.world.service, sp.lat["service"]), SERVICE_DONE, "service_done", sp)
        if started:
            self.busy_trace.append((t, self.busy))

    def service_done(self, t: float, sp: Specimen) -> None:
        self.busy -= 1
        self.busy_trace.append((t, self.busy))
        sp.service_end = t
        sp.segs.append((sp.carry_state, "receiving_queue", sp.desk_arrive, t, LAB_RECEIVING))
        down = self.wins.covering("scanner_downtime", disruptions.RECEIVING_TARGET, t)
        if down is not None:
            sp.touched.update(down.ids)
            sp.wait = (sp.carry_state, "scanner_backlog", t, LAB_RECEIVING)
            self.backlog.append(sp)
            if down.end not in self.scanner_ends:
                self.scanner_ends.add(down.end)
                self.q.push(down.end, CAPACITY, "scanner_end", down)
        else:
            self.receive(t, sp)
        self.start_services(t)

    def scanner_end(self, t: float, _=None) -> None:
        """The backlog is scanned one specimen at a time at the service rate; the scan is the receipt."""
        at = t
        backlog, self.backlog = self.backlog, []
        for sp in backlog:
            at += rng.lognormal(self.world.service, sp.lat["rescan"])
            sp.segs.append((sp.carry_state, "scanner_backlog", sp.service_end, at, LAB_RECEIVING))
            self.receive(at, sp)

    def set_capacity(self, t: float, cap: int) -> None:
        self.capacity = cap
        self.cap_trace.append((t, cap))
        self.start_services(t)

    def receive(self, t: float, sp: Specimen) -> None:
        world, lat = self.world, sp.lat
        sp.received = t
        sp.wait = None
        self.pending -= 1
        accession = t + rng.lognormal(world.accession, lat["accession"])
        sp.segs.append(("at_receiving_bench", None, t, accession, LAB_RECEIVING))
        if sp.content_failure:
            sp.rejected = accession
            sp.faults.add("content_failure")
            return
        plated = accession + rng.lognormal(world.plating, lat["plating"])
        for w in self.instrument_windows:  # plating waits for every instrument that is down
            if w.start <= plated < w.end:
                plated = w.end
                sp.touched.add(w.disruption_id)
        bench = "lab_bench_1" if lat["bench"] < 0.5 else "lab_bench_2"
        sp.accessioned, sp.plated = accession, plated
        sp.segs.append(("accessioned", None, accession, plated, bench))
        sp.segs.append(("plated", None, plated, None, bench))


def simulate(scenario: dict, *, scenario_index: int, replicate: int, loaded: dict | None = None, days: int = 7,
             keep_draws: bool = False) -> SimResult:
    """Simulate one replicate of one scenario.

    `days` is the number of scored days (7 for a library replicate, 2 for the plausibility check);
    `keep_draws` adds every specimen's latent draws to stats["draws"] for paired comparisons.
    """
    started = time.perf_counter()
    loaded = loaded or config.load()
    world = processes.build_world(scenario, loaded, days)
    streams = rng.Streams(scenario_index, replicate)
    wins = disruptions.realize(scenario, streams, days, world.t_end)
    sim = Sim(world, wins, streams)
    draws = sim.schedule(keep_draws)
    sim.run()
    run_key = records.run_key(scenario_index, replicate)
    recs = [records.record(sp, scenario, replicate, run_key) for sp in sim.specimens]
    stats = records.stats(sim, recs, run_key)
    if keep_draws:
        stats["draws"] = {records.specimen_id(run_key, n): row for n, row in draws.items()}
    stats["wall_seconds"] = round(time.perf_counter() - started, 4)
    return SimResult(recs, records.roster(scenario), records.realized(wins), stats)
