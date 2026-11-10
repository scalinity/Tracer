"""The facts a decision-model state is written from, computed by code from the observed v2 store (spec §11.3, §11.4).

    from src.decide import facts
    rows = facts.alert_facts(store_dir, [(specimen_id, run, tick_min), ...], test_run=run)

One dict per (specimen, tick). Everything comes from what the laboratory could see at the tick τ (the collection
time plus the tick): the specimen's own attributes, the scans of its run up to τ, the roster, and the observed
scans of its scenario's history replicate (replicate 0, the laboratory's recent past). The same leakage rules as
TracerNet's features (spec §7.4): no later scan, no other scored replicate, and nothing from the simulator's hidden
side, which this module never opens. Runs of a locked split (test, test_ood) are read only with the test run the
lock recorded (`test_run.check(split, store_dir=...)`, as the predictor does).

Fields (minutes throughout; None where a fact does not exist):
    specimen            type, tests, tier, priority, unit_kind, route_kind (tube, courier, walk_in), route
                        (tube or hand_carry)
    silence_min         the tick; window_min and left_min (the window less the tick)
    route_history       from the history replicate: received specimens on this route (its tube line, courier
                        route or walk-in), their median and 90th percentile collection-to-receipt time, the share
                        of them that arrived faster than this specimen's silence, and the share never received;
                        on the route kind instead when the route has fewer than MIN_HISTORY arrivals
    mates               batch-mates: same unit and route, collected within 20 minutes either side and by τ; how
                        many, and how many were received by τ
    route_last_receipt_min   minutes since the last receipt via this route in the run; route_usual_gap_min the
                        history's average gap between receipts via the route at this hour of day
    route_overdue       other outstanding specimens on the route older than the route's 90th percentile
    unit_outstanding    other outstanding specimens from the same unit
    lab_receipts_60     receipts at the laboratory in the last 60 minutes; lab_usual_receipts_60 the history's
                        average for the same hour of day
    shift, day_type, to_shift_change_min   from the roster's shift pattern and τ's weekday
    staff_this_shift, staff_range          scheduled receiving staff (roster)
    courier_interval_min                   scheduled courier rounds on this route this shift (courier routes;
                        None when the route has no rounds this shift)

Outstanding means collected, not received by τ, and no older than its own window plus 60 minutes (spec §7.4).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from src.generator import config
from src.pipeline import intervals, store

MATE_WINDOW_MIN = 20.0
OUTSTANDING_EXTRA_MIN = 60.0
MIN_HISTORY = 20            # arrivals a route needs before its own history is used (source: assumed)
LOCKED_SPLITS = ("test", "test_ood")
DAY_MIN = 24 * 60


class FactsError(RuntimeError):
    """The store cannot give the facts asked for."""


def check_lock(con, store_dir: Path, runs: set[int], test_run) -> None:
    splits = {s for (s,) in con.execute("select distinct split from runs where list_contains(?, run)",
                                        [sorted(runs)]).fetchall()}
    for split in sorted(splits & set(LOCKED_SPLITS)):
        check = getattr(test_run, "check", None)
        if not callable(check):
            raise FactsError(f"runs of the {split} split are read only after the test lock is taken (spec §8.7): "
                             "pass the test run the lock recorded")
        check(split, store_dir=store_dir)


def shift_of(pattern: str, minute_of_day: float) -> tuple[str, float]:
    """The receiving shift ('day', 'evening', 'night') at a minute of the day under a pattern such as '7-15-23',
    and the minutes to the next change."""
    starts = [int(h) * 60 for h in str(pattern).split("-")]
    names = ("day", "evening", "night")
    m = minute_of_day % DAY_MIN
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else starts[0] + DAY_MIN
        span = (start, end)
        if span[0] <= m < span[1] or span[0] <= m + DAY_MIN < span[1]:
            here = m if m >= span[0] else m + DAY_MIN
            return names[i], float(span[1] - here)
    raise FactsError(f"shift pattern {pattern!r} does not cover minute {m}")


def _quantile(x: np.ndarray, q: float) -> float:
    return float(np.quantile(x, q, method="linear"))


class _Run:
    """One run's specimens as arrays (minutes since the epoch), and its history replicate's."""

    def __init__(self, rows: list[tuple]):
        cols = list(zip(*rows)) if rows else [()] * 13
        (self.ids, self.unit, self.unit_kind, self.route_kind, self.route_id, self.route, self.kind, self.tests,
         self.priority, self.tier, window, col, rec) = cols
        self.window = np.asarray(window, dtype=float)
        self.col = np.array([np.nan if v is None else v for v in col], dtype=float)
        self.rec = np.array([np.nan if v is None else v for v in rec], dtype=float)
        self.index = {sid: i for i, sid in enumerate(self.ids)}
        self.unit_a = np.asarray(self.unit, dtype=object)
        self.route_a = np.asarray(self.route_id, dtype=object)
        self.kind_a = np.asarray(self.route_kind, dtype=object)


def _load_run(con, run: int, start: str, stop: str) -> _Run:
    rows = con.execute("""
        with ev as (
            select e.specimen_id,
                   min(case when e.activity = ? then epoch(e.timestamp) end) / 60.0 as col,
                   min(case when e.activity = ? then epoch(e.timestamp) end) / 60.0 as rec
            from events e join specimens r on r.specimen_id = e.specimen_id
            where r.run = ? group by e.specimen_id)
        select s.specimen_id, s.unit, s.unit_kind, s.route_kind, s.route_id, s.planned_route, s.specimen_type,
               s.tests, s.priority, s.urgency_tier, s.window_min, ev.col, ev.rec
        from specimens s left join ev on ev.specimen_id = s.specimen_id
        where s.run = ? order by s.specimen_id""", [start, stop, run, run]).fetchall()
    return _Run(rows)


def _route_history(h: _Run, route_id: str, route_kind: str, silence: float) -> dict | None:
    for scope, keep in (("route", h.route_a == route_id), ("route kind", h.kind_a == route_kind)):
        clocked = keep & ~np.isnan(h.col)
        transit = (h.rec - h.col)[clocked]
        arrived = transit[~np.isnan(transit)]
        if len(arrived) >= MIN_HISTORY:
            return {"scope": scope, "n": int(len(arrived)), "p50": _quantile(arrived, 0.5),
                    "p90": _quantile(arrived, 0.9), "share_faster": float(np.mean(arrived < silence)),
                    "never_share": float(np.mean(np.isnan(transit)))}
    return None


def _hour_counts(times: np.ndarray, days: float) -> np.ndarray:
    """Average receipts per clock hour of day over the history's days."""
    hours = np.floor(np.mod(times[~np.isnan(times)], DAY_MIN) / 60).astype(int)
    return np.bincount(hours, minlength=24)[:24] / max(days, 1.0)


def _roster(con, scenario_id: str) -> dict:
    out = {"units": {}, "couriers": {}, "staff": {}, "pattern": "7-15-23"}
    for kind, key, attrs in con.execute("select kind, key, attributes from roster where scenario_id = ?",
                                        [scenario_id]).fetchall():
        a = json.loads(attrs)
        if kind == "courier_route":
            out["couriers"][key] = a.get("round_interval_min") or {}
        elif kind == "receiving_shift":
            out["staff"][(a["day_type"], a["shift"])] = a.get("staff")
            out["pattern"] = a.get("shift_pattern") or out["pattern"]
    return out


def alert_facts(store_dir: Path, items, *, test_run=None) -> list[dict]:
    """Facts for each (specimen_id, run, tick_min) of `items`, in their order."""
    store_dir = Path(store_dir)
    items = [(str(s), int(r), float(t)) for s, r, t in items]
    if not items:
        return []
    start, stop = intervals.clock_events(config.load()["policy"])
    con = store.open_observed_v2(store_dir)
    try:
        runs = {r for _, r, _ in items}
        check_lock(con, store_dir, runs, test_run)
        meta = {r: (sid, rep) for r, sid, rep in con.execute(
            "select run, scenario_id, replicate from runs where list_contains(?, run)", [sorted(runs)]).fetchall()}
        missing = runs - set(meta)
        if missing:
            raise FactsError(f"runs {sorted(missing)[:5]} are not in the store at {store_dir}")
        scenarios = sorted({sid for sid, _ in meta.values()})
        hist = {sid: run for run, sid in con.execute(
            "select run, scenario_id from runs where role = 'history' and list_contains(?, scenario_id)",
            [scenarios]).fetchall()}
        spans = {run: (a, b) for run, a, b in con.execute(
            "select run, epoch(scored_start) / 60.0, epoch(scored_end) / 60.0 from runs "
            "where list_contains(?, run)", [sorted(hist.values())]).fetchall()}
        loaded: dict[int, _Run] = {}
        rosters: dict[str, dict] = {}

        def run_of(r: int) -> _Run:
            if r not in loaded:
                loaded[r] = _load_run(con, r, start, stop)
            return loaded[r]

        out = []
        for sid, run, tick in items:
            scenario = meta[run][0]
            if scenario not in rosters:
                rosters[scenario] = _roster(con, scenario)
            h_run = hist.get(scenario)
            out.append(_facts(run_of(run), sid, tick, run_of(h_run) if h_run is not None else None,
                              spans.get(h_run), rosters[scenario]))
        return out
    finally:
        con.close()


def _facts(x: _Run, sid: str, tick: float, h: _Run | None, span: tuple | None, roster: dict) -> dict:
    if sid not in x.index:
        raise FactsError(f"{sid} is not in its run")
    i = x.index[sid]
    if math.isnan(x.col[i]):
        raise FactsError(f"{sid} has no collection scan, so it has no clock")
    tau = x.col[i] + tick
    others = np.ones(len(x.ids), dtype=bool)
    others[i] = False
    collected = ~np.isnan(x.col) & (x.col <= tau)
    received = ~np.isnan(x.rec) & (x.rec <= tau)
    age = tau - x.col
    outstanding = collected & ~received & (age <= x.window + OUTSTANDING_EXTRA_MIN) & others
    same_unit, same_route = x.unit_a == x.unit[i], x.route_a == x.route_id[i]
    mates = others & same_unit & same_route & collected & (np.abs(x.col - x.col[i]) <= MATE_WINDOW_MIN)

    hist = _route_history(h, x.route_id[i], x.route_kind[i], tick) if h is not None else None
    on_route = same_route & received
    last = float(np.max(x.rec[on_route])) if on_route.any() else None
    minute_of_day = float(np.mod(tau, DAY_MIN))
    hour = int(minute_of_day // 60)
    usual_gap = lab_usual = None
    if h is not None and span is not None:
        days = (span[1] - span[0]) / DAY_MIN
        in_span = (h.rec >= span[0]) & (h.rec < span[1])
        route_rate = _hour_counts(h.rec[in_span & (h.route_a == x.route_id[i])], days)[hour]
        usual_gap = 60.0 / route_rate if route_rate > 0 else None
        lab_usual = float(_hour_counts(h.rec[in_span], days)[hour])
    overdue = 0
    if hist is not None:
        overdue = int(np.sum(outstanding & same_route & (age > hist["p90"])))

    weekday = int((math.floor(tau / DAY_MIN) + 3) % 7)        # the epoch (1970-01-01) was a Thursday: Monday is 0
    day_type = "weekend" if weekday >= 5 else "weekday"
    shift, to_change = shift_of(roster["pattern"], minute_of_day)
    staff = roster["staff"].get((day_type, shift))
    staffed = [v for v in roster["staff"].values() if v is not None]
    courier = None
    if x.route_kind[i] == "courier":
        courier = (roster["couriers"].get(x.route_id[i]) or {}).get(shift)

    return {
        "specimen": {"type": x.kind[i], "tests": list(x.tests[i] or []), "tier": int(x.tier[i]),
                     "priority": x.priority[i], "unit_kind": x.unit_kind[i], "route_kind": x.route_kind[i],
                     "route": x.route[i]},
        "silence_min": float(tick), "window_min": int(x.window[i]), "left_min": float(x.window[i] - tick),
        "route_history": hist,
        "mates": {"n": int(mates.sum()), "received": int((mates & received).sum())},
        "route_last_receipt_min": None if last is None else float(tau - last),
        "route_usual_gap_min": usual_gap,
        "route_overdue": overdue,
        "unit_outstanding": int((outstanding & same_unit).sum()),
        "lab_receipts_60": int(np.sum(received & (x.rec > tau - 60))),
        "lab_usual_receipts_60": lab_usual,
        "shift": shift, "day_type": day_type, "to_shift_change_min": to_change,
        "staff_this_shift": staff, "staff_range": [min(staffed), max(staffed)] if staffed else None,
        "courier_interval_min": courier,
    }
