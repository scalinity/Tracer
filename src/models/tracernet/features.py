"""TracerNet's inputs: the feature definitions and the builder (docs/neural/spec.md §7.4).

Reads the observed store only (v2: specimens, events, roster, runs and meta; v1: specimens and events). A feature
at tick t of a specimen uses only what is observable at tau = collection + t: the specimen's own attributes, scans
of the same run with a timestamp at or before tau, the laboratory's roster, the history replicate's observed tables
(the fingerprint) and the policy. "Collection" and "receipt" are the policy's clock_start and clock_stop events.

How it is computed. `load_table` reads every collected specimen into flat arrays sorted by (run, collection), with
times as seconds since the run's origin (the midnight before its first scan). For each run a `RunContext` is built
once: per-minute cumulative counts of collections, receipts, stat receipts and accessions for the laboratory, each
unit, each route and each route type; per-minute outstanding counts and the oldest outstanding age per scope
(outstanding: collected, not received by that minute, and no older than its own window plus 60 minutes; the oldest
age is swept in linear time because "removed by minute m" is monotone in m); the receipt times for the transit
statistics; candidate lists for batch-mates and tokens. Every feature of a batch of (specimen, tick) pairs is then a
vectorized gather or difference at minute m = floor(tau), or an exact comparison with tau for per-specimen ones.

Batch (the dict TracerNet reads, see net.py): `cat` [B, 7], `tests` [B, 13], `num` [B, N_NUM] in the named groups
of `num_groups()`, `fingerprint` [B, 30], `series_lab` [B, 6, 36], `series_unit` [B, 4, 36], `series_route`
[B, 4, 36], `tokens` [B, 16, 12], `token_mask` [B, 16], `tick`, `window`, plus `target_bin` (the observed receipt's
class in bins.py), `breach` (from the scans and the window), `specimen_index` (row of the table) and `run`.

Scaling. Numeric inputs and series channels are robust-scaled with medians and interquartile ranges fitted on train
samples and clipped to +-10; binary and bounded inputs pass unscaled; tokens carry the spec's own fixed scales and
are clipped to +-10. A missing input gets an indicator and 0. Scalers and vocabularies live in feature_schema.json
with SCHEMA_VERSION; loading a schema of another version, or one fitted under another policy, is refused.

On the v1 store the roster is missing (indicators), the route kind is the route type, every specimen is in a
scored day, and the fingerprint comes from fit runs 0 to 23.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import polars as pl

from src.generator import config
from src.pipeline import store
from src.models.tracernet import bins as tbins

SCHEMA_VERSION = "tracernet-features-1"
TICK_MIN = 5
CLIP = 10.0
GRACE_MIN = 60              # outstanding until its window plus this (spec §7.4; ASSUMED A-07)
N_LAGS, LAG_MIN = 36, 5
N_TOKENS, TOKEN_FEATURES = 16, 12
SIDE_TOKENS = 16            # nearest same-unit-or-route specimens kept on each side of the collection
BATCH_MATE_MIN = 20
TOKEN_RANGE_MIN = 60
TRANSIT_LOOKBACK_MIN = 180
SINCE_CAP_MIN = 720
OLDEST_CAP_H = 12
SHRINK_N = 20
PAD = 256                   # minutes kept before the origin, so every look-back index is valid
TRANSIT_CAP = 512           # receipts per window kept for the transit quantiles (the latest ones)
DAY_MIN = 1440
V1_HISTORY_RUNS = tuple(range(24))
LEGACY = "legacy_v1"
DEFAULT_PATTERN = "7-15-23"
SHIFTS = ("day", "evening", "night")
SHIFT_CLOCKS = {"7-15-23": (("day", 7, 15), ("evening", 15, 23), ("night", 23, 7)),
                "6-14-22": (("day", 6, 14), ("evening", 14, 22), ("night", 22, 6)),
                "7-19": (("day", 7, 19), ("night", 19, 7))}
FINGERPRINT_BLOCKS = ((7, 15), (15, 23), (23, 7))  # receipts per hour by fixed clock block (day, evening, night)
UNIT_KINDS = ("other", "ed", "icu", "med_surg", "periop", "clinic_onsite", "clinic_offsite", "oncology",
              "pediatrics", "labor_delivery", "dialysis", "long_term_care")
UNIT_ALIASES = {"clinic": "clinic_onsite"}  # v1's unit names are kinds, except its one clinic
ROUTE_KINDS = ("other", "tube", "courier", "walk_in", "hand_carry")
FP_KINDS = ("tube", "courier", "walk_in")
WINDOW_CLASSES = (60, 120, 1440, 2880, 4320)
PRIORITIES = ("routine", "stat")
CAT_COLUMNS = ("variant", "unit_kind", "route_kind", "tier", "priority", "weekday", "weekday_tau")

# (name, factor group, kind): kind "num" is robust-scaled; "bin" (0 or 1) and "bnd" (bounded) pass unscaled.
NUM_COLUMNS = (
    ("ordered", "specimen", "bin"), ("order_to_collection", "specimen", "num"),
    ("log_window", "window", "num"), *((f"window_{w}", "window", "bin") for w in WINDOW_CLASSES),
    ("window_other", "window", "bin"), ("t_over_window", "window", "bnd"), ("window_left", "window", "num"),
    ("silence", "silence", "num"),
    ("collection_hour_sin", "time_of_day", "bnd"), ("collection_hour_cos", "time_of_day", "bnd"),
    ("weekend", "time_of_day", "bin"), ("tau_hour_sin", "time_of_day", "bnd"), ("tau_hour_cos", "time_of_day", "bnd"),
    ("to_shift_change_h", "time_of_day", "num"),
    ("route_tube", "route_unit", "bin"), ("round_interval", "route_unit", "num"), ("no_round", "route_unit", "bin"),
    ("staff_this_shift", "route_unit", "num"), ("tube_lines", "route_unit", "num"),
    ("courier_routes", "route_unit", "num"), ("roster_missing", "route_unit", "bin"),
    ("mates", "batch_mates", "num"), ("mates_received", "batch_mates", "num"), ("mates_share", "batch_mates", "bnd"),
    ("mates_since_first_receipt", "batch_mates", "num"), ("mates_transit", "batch_mates", "num"),
    ("mates_none_received", "batch_mates", "bin"),
    ("out_route", "route_flow", "num"), ("out_route_older", "route_flow", "num"),
    ("out_route_older_p90", "route_flow", "num"), ("rec15_route", "route_flow", "num"),
    ("rec60_route", "route_flow", "num"), ("since_rec_route", "route_flow", "num"),
    ("since_rec_route_missing", "route_flow", "bin"), ("flow_route", "route_flow", "num"),
    ("transit_type_p50", "route_flow", "num"), ("transit_type_p90", "route_flow", "num"),
    ("transit_type_ratio", "route_flow", "num"), ("transit_type_n", "route_flow", "num"),
    ("transit_type_missing", "route_flow", "bin"),
    ("transit_route_p50", "route_flow", "num"), ("transit_route_p90", "route_flow", "num"),
    ("transit_route_ratio", "route_flow", "num"), ("transit_route_n", "route_flow", "num"),
    ("transit_route_missing", "route_flow", "bin"),
    ("out_unit", "unit_flow", "num"), ("rec15_unit", "unit_flow", "num"), ("rec60_unit", "unit_flow", "num"),
    ("since_rec_unit", "unit_flow", "num"), ("since_rec_unit_missing", "unit_flow", "bin"),
    ("out_lab", "lab_flow", "num"), ("rec15_lab", "lab_flow", "num"), ("rec60_lab", "lab_flow", "num"),
    ("since_rec_lab", "lab_flow", "num"), ("since_rec_lab_missing", "lab_flow", "bin"),
    ("lab_receipts_over_collections", "lab_flow", "num"), ("lab_volume_ratio", "lab_flow", "num"),
)
NUM_INDEX = {name: i for i, (name, _, _) in enumerate(NUM_COLUMNS)}
GROUP_ORDER = ("specimen", "window", "silence", "time_of_day", "route_unit", "batch_mates", "route_flow",
               "unit_flow", "lab_flow")
FINGERPRINT_COLUMNS = (
    "lab_p50", "lab_p90", "lab_never",
    *(f"type_{rt}_{q}" for rt in ("tube", "hand_carry") for q in ("p10", "p50", "p90", "never")),
    *(f"kind_{k}_{q}" for k in FP_KINDS for q in ("p50", "never")),
    "route_p50", "route_p90", "route_never", "unit_p50", "unit_p90", "unit_never",
    "daily_volume", "stat_share", "breach_share", "receipts_per_hour_day", "receipts_per_hour_evening",
    "receipts_per_hour_night", "history_available")
SERIES = {"series_lab": ("collections", "receipts", "stat_receipts", "accessions", "outstanding", "oldest_age_h"),
          "series_unit": ("collections", "receipts", "outstanding", "oldest_age_h"),
          "series_route": ("collections", "receipts", "outstanding", "oldest_age_h")}
TOKEN_COLUMNS = ("same_unit", "same_route", "offset_30", "received", "transit_60", "age_60", "stat", "tier_1",
                 "tier_2", "tier_3", "route_tube", "log_window_1440")

assert [g for g in GROUP_ORDER] == list(dict.fromkeys(g for _, g, _ in NUM_COLUMNS))
assert len(FINGERPRINT_COLUMNS) == 30 and len(TOKEN_COLUMNS) == TOKEN_FEATURES


def num_groups() -> tuple:
    """The `num` layout as TracerNetConfig.num_groups: (factor group, width) in column order."""
    return tuple((g, sum(1 for _, gg, _ in NUM_COLUMNS if gg == g)) for g in GROUP_ORDER)


def ticks_of(window: float, receipt_min: float | None) -> np.ndarray:
    """The scoring ticks: every 5 minutes while the window is open and the specimen is still unreceived (the
    tick rule of src/pipeline's Spec)."""
    last = window if receipt_min is None or not np.isfinite(receipt_min) else \
        min(window, math.ceil(receipt_min) - 1)
    return np.arange(TICK_MIN, last + 1, TICK_MIN, dtype=float)


def shift_lookup(pattern: str) -> np.ndarray:
    """[24] index into SHIFTS of the receiving shift that covers each hour of the day."""
    out = np.zeros(24, dtype=np.int64)
    for name, first, last in SHIFT_CLOCKS[pattern]:
        hours = range(first, last) if first < last else [*range(first, 24), *range(0, last)]
        out[list(hours)] = SHIFTS.index(name)
    return out


def shift_changes(pattern: str) -> np.ndarray:
    return np.array([first * 60 for _, first, _ in SHIFT_CLOCKS[pattern]], dtype=float)


def vocabularies(loaded: dict | None = None) -> dict:
    """The categorical vocabularies and the test list, from config/config.yaml and the policy."""
    loaded = loaded or config.load()
    policy = loaded["policy"]["policies"]
    variants = []
    for key, entry in loaded["config"]["specimen_mix"]["entries"].items():
        rows = [policy[pid] for pid in entry["policies"]]
        variants.append({"key": key, "specimen_type": rows[0]["specimen_type"],
                         "tests": sorted(r["test"] for r in rows)})
    return {"variant": [v["key"] for v in variants], "variant_match": variants, "unit_kind": list(UNIT_KINDS),
            "route_kind": list(ROUTE_KINDS), "tier": ["1", "2", "3"], "priority": list(PRIORITIES),
            "weekday": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            "weekday_tau": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            "tests": sorted({r["test"] for r in policy.values()}), "window_classes": list(WINDOW_CLASSES)}


# --- The table ---------------------------------------------------------------------------------------------------

ARRAYS = ("run", "serial", "scenario", "replicate", "history", "scored", "split", "variant", "unit_kind",
          "route_kind", "tier", "stat", "tests", "window", "unit", "route", "route_type", "ordered", "collected",
          "received", "accessioned")
RUN_ARRAYS = ("run_ids", "run_start", "run_stop", "run_origin", "run_weekday0", "run_scenario", "run_history",
              "run_split")
HISTORY_ARRAYS = ("h_lab", "h_kind", "h_route", "h_route_n", "h_unit_own", "h_unit_n", "h_hourly", "h_type_p50",
                  "h_misc", "h_has")


@dataclass
class Table:
    """Every collected specimen of the selected runs as flat arrays, sorted by (run, collection, id).

    Times are seconds since the run's origin (NaN when the scan is absent): `ordered`, `collected` (the clock
    start), `received` (the clock stop), `accessioned`. Codes index the lists in `names`."""
    a: dict
    names: dict
    rosters: dict
    meta: dict
    hist: dict = field(default_factory=dict)

    def __getattr__(self, key):
        try:
            return self.__dict__["a"][key]
        except KeyError:
            try:
                return self.__dict__["hist"][key]
            except KeyError:
                raise AttributeError(key) from None

    def __len__(self) -> int:
        return len(self.a["run"])

    @property
    def runs(self) -> np.ndarray:
        return self.a["run_ids"]

    def run_pos(self, run: int) -> int:
        k = int(np.searchsorted(self.a["run_ids"], run))
        if k >= len(self.a["run_ids"]) or self.a["run_ids"][k] != run:
            raise KeyError(f"run {run} is not in the table")
        return k

    def rows_of_run(self, run: int) -> np.ndarray:
        k = self.run_pos(run)
        return np.arange(self.a["run_start"][k], self.a["run_stop"][k])

    def specimen_ids(self, rows) -> list[str]:
        rows = np.atleast_1d(rows)
        return [self._sid(int(r), int(s)) for r, s in zip(self.a["run"][rows], self.a["serial"][rows])]

    def _sid(self, run: int, serial: int) -> str:
        return f"SP-r{run:02d}-{serial:06d}" if self.meta["v1"] else f"SP-r{run}-{serial:06d}"

    def rows_of(self, specimen_ids) -> np.ndarray:
        index = {self._sid(int(r), int(s)): i for i, (r, s) in enumerate(zip(self.a["run"], self.a["serial"]))}
        return np.array([index[s] for s in specimen_ids], dtype=np.int64)

    def receipt_min(self, rows=None) -> np.ndarray:
        """Minutes from collection to receipt, computed from whole seconds as the store's queries do."""
        rows = slice(None) if rows is None else rows
        return (self.a["received"][rows] - self.a["collected"][rows]) / 60.0

    def ticks(self, row: int) -> np.ndarray:
        r = float(self.receipt_min(row))
        return ticks_of(float(self.a["window"][row]), None if not np.isfinite(r) else r)

    def n_ticks(self, rows=None) -> np.ndarray:
        """How many ticks each row has (vectorized ticks_of)."""
        rows = slice(None) if rows is None else rows
        w, r = self.a["window"][rows].astype(float), self.receipt_min(rows)
        last = np.where(np.isfinite(r), np.minimum(w, np.ceil(np.nan_to_num(r, nan=0.0)) - 1), w)
        return np.maximum(np.floor(last / TICK_MIN), 0).astype(np.int64)

    def breach(self, rows=None) -> np.ndarray:
        rows = slice(None) if rows is None else rows
        r = self.receipt_min(rows)
        return ~np.isfinite(r) | (r > self.a["window"][rows])

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        for k, v in {**self.a, **self.hist}.items():
            np.save(path / f"{k}.npy", np.ascontiguousarray(v))
        (path / "table.json").write_text(json.dumps({"names": self.names, "rosters": self.rosters, "meta": self.meta},
                                                    sort_keys=True), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path, mmap: bool = True) -> "Table":
        path = Path(path)
        doc = json.loads((path / "table.json").read_text(encoding="utf-8"))
        mode = "r" if mmap else None
        a = {k: np.load(path / f"{k}.npy", mmap_mode=mode) for k in (*ARRAYS, *RUN_ARRAYS)}
        hist = {k: np.load(path / f"{k}.npy", mmap_mode=mode) for k in HISTORY_ARRAYS if (path / f"{k}.npy").exists()}
        return cls(a, doc["names"], doc["rosters"], doc["meta"], hist)


def _store_policy_hash(con, v2: bool) -> str | None:
    if not v2:
        return None
    return dict(con.execute("select key, value from meta").fetchall()).get("policy_hash")


def load_table(store_dir: Path | None = None, *, runs=None, splits=None, con=None, loaded: dict | None = None) -> Table:
    """Read the observed store (v2 when its specimens carry scenario ids, else v1) into a Table, optionally only
    some runs or splits, and compute the history fingerprints. A selection must hold each scenario's history runs
    (v2: replicate 0, in the scenario's own split; v1: the fit runs) or its fingerprint is marked unavailable.
    Refuses a v2 store built under another policy."""
    loaded = loaded or config.load()
    starts = {(p["clock_start"], p["clock_stop"]) for p in loaded["policy"]["policies"].values()}
    if len(starts) != 1:
        raise RuntimeError("the policy has more than one clock; the feature builder handles one")
    start_ev, stop_ev = next(iter(starts))
    if con is None:
        d = Path(store_dir) if store_dir is not None else store.STORE_V2_DIR
        con = store.open_observed(d)
    cols = {r[0] for r in con.execute("describe specimens").fetchall()}
    v2 = "scenario_id" in cols
    policy_hash = _store_policy_hash(con, v2)
    if v2 and policy_hash != loaded["policy_hash"]:
        raise RuntimeError("the store was built under a different policy; rebuild it before building features")
    v2_cols = ("s.scenario_id, s.replicate, s.role, s.scored, s.unit_kind, s.route_kind, s.route_id" if v2 else
               "null as scenario_id, null as replicate, null as role, true as scored, null as unit_kind, "
               "null as route_kind, null as route_id")
    where, params = ["ev.collected is not null"], [start_ev, stop_ev]
    if runs is not None:
        where.append("list_contains(?, s.run)")
        params.append([int(r) for r in runs])
    if splits is not None:
        where.append("list_contains(?, s.split)")
        params.append([str(s) for s in splits])
    df = _frame(con, f"""
        with ev as (
            select specimen_id,
                   min(case when activity = ? then timestamp end) as collected,
                   min(case when activity = ? then timestamp end) as received,
                   min(case when activity = 'ordered' then timestamp end) as ordered,
                   min(case when activity = 'accessioned' then timestamp end) as accessioned,
                   min(timestamp) as first_event
            from events group by specimen_id)
        select s.run, cast(split_part(s.specimen_id, '-', 3) as bigint) as serial, s.split, s.specimen_type, s.tests,
               s.unit, s.priority, s.planned_route, s.window_min, s.urgency_tier, {v2_cols},
               epoch(ev.collected) as collected, epoch(ev.received) as received, epoch(ev.ordered) as ordered,
               epoch(ev.accessioned) as accessioned, epoch(ev.first_event) as first_event
        from specimens s join ev using (specimen_id)
        where {" and ".join(where)}
        order by s.run, ev.collected, s.specimen_id""", params)
    rosters = {}
    if v2:
        for sid, kind, key, attrs in con.execute("select scenario_id, kind, key, attributes from roster "
                                                 "order by scenario_id, kind, key").fetchall():
            rosters.setdefault(sid, []).append((kind, key, json.loads(attrs)))
    return _table(df, v2, {k: _roster(v) for k, v in rosters.items()}, loaded, policy_hash)


def _frame(con, sql: str, params: list) -> pl.DataFrame:
    """A query's result as a polars frame, through a temporary parquet file (no pyarrow needed)."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "rows.parquet"
        con.execute(f"COPY ({sql}) TO '{path.as_posix()}' (FORMAT parquet)", params)
        return pl.read_parquet(path)


def _roster(rows: list) -> dict:
    out = {"units": {}, "routes": {}, "tube_lines": 0, "staff": {"weekday": {}, "weekend": {}},
           "pattern": DEFAULT_PATTERN}
    for kind, key, attrs in rows:
        if kind == "unit":
            out["units"][key] = attrs
        elif kind == "tube_line":
            out["tube_lines"] += 1
        elif kind == "courier_route":
            out["routes"][key] = attrs
        elif kind == "receiving_shift":
            out["staff"].setdefault(attrs["day_type"], {})[attrs["shift"]] = attrs["staff"]
            out["pattern"] = attrs["shift_pattern"]
    return out


def _codes(values: list, vocab: list) -> np.ndarray:
    index = {v: i for i, v in enumerate(vocab)}
    return np.array([index.get(v, 0) for v in values], dtype=np.int16)


def _index(values: list, names: list) -> np.ndarray:
    index = {v: i for i, v in enumerate(names)}
    return np.array([index[v] for v in values], dtype=np.int32)


def _table(df: pl.DataFrame, v2: bool, rosters: dict, loaded: dict, policy_hash) -> Table:
    vocab = vocabularies(loaded)
    n = df.height
    if n == 0:
        raise RuntimeError("no collected specimens in the selection")
    run = df["run"].to_numpy().astype(np.int64)
    split_names = sorted(set(df["split"].to_list()))
    split = _codes(df["split"].to_list(), split_names).astype(np.int8)
    if v2:
        scen_list = df["scenario_id"].to_list()
        replicate = df["replicate"].to_numpy().astype(np.int16)
        history = (df["role"] == "history").to_numpy()
        scored = df["scored"].to_numpy().astype(bool)
        unit_kind = df["unit_kind"].to_list()
        route_kind = df["route_kind"].to_list()
        route_id = df["route_id"].to_list()
    else:
        scen_list = [LEGACY] * n
        replicate = run.astype(np.int16)
        history = np.isin(run, V1_HISTORY_RUNS)
        scored = np.ones(n, dtype=bool)
        unit_kind = [UNIT_ALIASES.get(u, u) for u in df["unit"].to_list()]
        route_kind = df["planned_route"].to_list()
        route_id = route_kind
    scenarios = sorted(set(scen_list))
    scen = _codes(scen_list, scenarios).astype(np.int32)
    units_raw = [f"{s}|{u}" for s, u in zip(scen_list, df["unit"].to_list())]
    routes_raw = [f"{s}|{k}:{r}" for s, k, r in zip(scen_list, route_kind, route_id)]
    unit_names, routes_names = sorted(set(units_raw)), sorted(set(routes_raw))
    match = {(v["specimen_type"], tuple(v["tests"])): i for i, v in enumerate(vocab["variant_match"])}
    by_type = {}
    for i, v in enumerate(vocab["variant_match"]):
        by_type.setdefault(v["specimen_type"], i)
    tests_index = {t: i for i, t in enumerate(vocab["tests"])}
    variant = np.zeros(n, dtype=np.int16)
    tests = np.zeros(n, dtype=np.int32)
    for i, (kind, ts) in enumerate(zip(df["specimen_type"].to_list(), df["tests"].to_list())):
        ts = sorted(ts or [])
        variant[i] = match.get((kind, tuple(ts)), by_type.get(kind, 0))
        bits = 0
        for t in ts:
            if t in tests_index:
                bits |= 1 << tests_index[t]
        tests[i] = bits
    first = df["first_event"].to_numpy()
    starts = np.flatnonzero(np.r_[True, run[1:] != run[:-1]])
    stops = np.r_[starts[1:], n]
    run_ids = run[starts]
    origin = np.array([math.floor(np.nanmin(first[a:b]) / 86400) * 86400 for a, b in zip(starts, stops)],
                      dtype=np.float64)
    per_row_origin = np.repeat(origin, stops - starts)

    def rel(col: str) -> np.ndarray:
        x = df[col].to_numpy().astype(np.float64)
        return x - per_row_origin

    a = {
        "run": run, "serial": df["serial"].to_numpy().astype(np.int64), "scenario": scen,
        "replicate": replicate, "history": history.astype(bool), "scored": scored, "split": split,
        "variant": variant, "unit_kind": _codes(unit_kind, list(UNIT_KINDS)),
        "route_kind": _codes(route_kind, list(ROUTE_KINDS)),
        "tier": np.clip(df["urgency_tier"].to_numpy().astype(np.int16) - 1, 0, 2),
        "stat": (df["priority"] == "stat").to_numpy(), "tests": tests,
        "window": df["window_min"].to_numpy().astype(np.float64),
        "unit": _index(units_raw, unit_names), "route": _index(routes_raw, routes_names),
        "route_type": (df["planned_route"] != "tube").to_numpy().astype(np.int8),
        "ordered": rel("ordered"), "collected": rel("collected"), "received": rel("received"),
        "accessioned": rel("accessioned"),
        "run_ids": run_ids, "run_start": starts.astype(np.int64), "run_stop": stops.astype(np.int64),
        "run_origin": origin, "run_weekday0": ((origin // 86400 + 3) % 7).astype(np.int64),
        "run_scenario": scen[starts].astype(np.int64), "run_history": history[starts].astype(bool),
        "run_split": split[starts].astype(np.int64),
    }
    names = {"scenarios": scenarios, "units": unit_names, "routes": routes_names, "splits": split_names}
    table = Table(a, names, rosters, {"v1": not v2, "policy_hash": policy_hash or loaded["policy_hash"],
                                      "store_policy_hash": policy_hash})
    table.hist = history_stats(table)
    return table


# --- History fingerprints ----------------------------------------------------------------------------------------

def _group_stats(keys: np.ndarray, n_keys: int, transit: np.ndarray, qs=(0.10, 0.50, 0.90)) -> tuple:
    """Per key: (n, quantiles [n_keys, len(qs)] of log1p transit over the received, never-received share)."""
    n = np.bincount(keys, minlength=n_keys).astype(float)
    never = np.bincount(keys[~np.isfinite(transit)], minlength=n_keys).astype(float)
    q = np.full((n_keys, len(qs)), np.nan)
    ok = np.isfinite(transit)
    order = np.lexsort((transit[ok], keys[ok]))
    k_sorted, x_sorted = keys[ok][order], transit[ok][order]
    bounds = np.searchsorted(k_sorted, np.arange(n_keys + 1))
    for g in np.flatnonzero(bounds[1:] > bounds[:-1]):
        q[g] = np.log1p(np.quantile(x_sorted[bounds[g]:bounds[g + 1]], qs))
    with np.errstate(invalid="ignore", divide="ignore"):
        share = np.where(n > 0, never / np.maximum(n, 1), np.nan)
    return n, q, share


def _shrink(own: np.ndarray, n: np.ndarray, parent: np.ndarray) -> np.ndarray:
    """own pulled toward parent by n / (n + 20); the parent where own is undefined."""
    w = (n / (n + SHRINK_N))[..., None] if own.ndim > n.ndim else n / (n + SHRINK_N)
    out = w * own + (1 - w) * parent
    return np.where(np.isfinite(own), out, parent)


def history_stats(t: Table) -> dict:
    """The fingerprint ingredients of every scenario, from its history runs' scored-day specimens."""
    a = t.a
    n_scen, n_units, n_routes = len(t.names["scenarios"]), len(t.names["units"]), len(t.names["routes"])
    h = a["history"] & a["scored"]
    days = np.zeros(n_scen)
    for k in range(len(a["run_ids"])):
        if a["run_history"][k]:
            days[a["run_scenario"][k]] += 7
    transit = (a["received"][h] - a["collected"][h]) / 60.0
    scen, kinds, rtype = a["scenario"][h].astype(np.int64), a["route_kind"][h].astype(np.int64), \
        a["route_type"][h].astype(np.int64)
    n_kinds = len(ROUTE_KINDS)
    ln, lq, ls = _group_stats(scen, n_scen, transit)
    lab = np.column_stack([lq[:, 1], lq[:, 2], ls])                                   # p50, p90, never
    tn, tq, ts = _group_stats(scen * 2 + rtype, n_scen * 2, transit)
    type_own = np.column_stack([tq, ts]).reshape(n_scen, 2, 4)                        # p10, p50, p90, never
    lab4 = np.column_stack([lq[:, 0], lq[:, 1], lq[:, 2], ls])
    types = _shrink(type_own, tn.reshape(n_scen, 2), lab4[:, None, :])
    kn, kq, ks = _group_stats(scen * n_kinds + kinds, n_scen * n_kinds, transit)
    kind_own = np.column_stack([kq[:, 1], kq[:, 2], ks]).reshape(n_scen, n_kinds, 3)  # p50, p90, never
    kind_parent = types[:, [1 if ROUTE_KINDS[k] != "tube" else 0 for k in range(n_kinds)], :][:, :, [1, 2, 3]]
    kind = _shrink(kind_own, kn.reshape(n_scen, n_kinds), kind_parent)
    rn, rq, rs = _group_stats(a["route"][h].astype(np.int64), n_routes, transit)
    route_own = np.column_stack([rq[:, 1], rq[:, 2], rs])
    route_scen = np.zeros(n_routes, dtype=np.int64)
    route_kind = np.zeros(n_routes, dtype=np.int64)
    route_scen[a["route"]] = a["scenario"]
    route_kind[a["route"]] = a["route_kind"]
    route = _shrink(route_own, rn, kind[route_scen, route_kind])
    un, uq, us = _group_stats(a["unit"][h].astype(np.int64), n_units, transit)
    unit_own = np.column_stack([uq[:, 1], uq[:, 2], us])
    # Daily volume, stat share, breach share (from the scans and the window), receipts per hour by clock block.
    with np.errstate(invalid="ignore", divide="ignore"):
        vol = np.bincount(scen, minlength=n_scen) / np.maximum(days, 1)
        stat = np.bincount(scen, weights=a["stat"][h].astype(float), minlength=n_scen) / np.maximum(ln, 1)
        breach = np.bincount(scen, weights=(~np.isfinite(transit) | (transit > a["window"][h])).astype(float),
                             minlength=n_scen) / np.maximum(ln, 1)
        rec_hour = np.floor(np.nan_to_num(a["received"][h], nan=-1) / 3600) % 24
        blocks = []
        for first, last in FINGERPRINT_BLOCKS:
            inside = (rec_hour >= first) & (rec_hour < last) if first < last else (rec_hour >= first) | (rec_hour < last)
            inside &= np.isfinite(a["received"][h])
            hours = (last - first) % 24
            blocks.append(np.bincount(scen, weights=inside.astype(float), minlength=n_scen) / np.maximum(days * hours, 1))
        coll_hour = (np.floor(a["collected"][h] / 3600) % 24).astype(np.int64)
        hourly = np.bincount(scen * 24 + coll_hour, minlength=n_scen * 24).reshape(n_scen, 24)
        hourly = hourly / np.maximum(days, 1)[:, None]
    blocks = np.column_stack(blocks)
    has = ln > 0
    type_p50 = np.expm1(types[:, :, 1])
    misc = np.column_stack([np.log1p(vol), stat, breach, np.log1p(blocks)])
    fp_kinds = kind[:, [ROUTE_KINDS.index(k) for k in FP_KINDS], :][:, :, [0, 2]].reshape(n_scen, 6)  # p50, never
    return {"h_lab": np.column_stack([lab, types.reshape(n_scen, 8), fp_kinds]),
            "h_kind": kind, "h_route": route, "h_route_n": rn, "h_unit_own": unit_own, "h_unit_n": un,
            "h_hourly": hourly, "h_type_p50": type_p50, "h_misc": misc, "h_has": has}


# --- Per-run context ---------------------------------------------------------------------------------------------

def _prefix_tree(keys: np.ndarray, last: np.ndarray) -> list:
    """A merge-sort tree over positions: level L holds, for each aligned block of 2**L positions, its `last`
    values sorted, as composite keys (block * BIG + last) so one searchsorted counts inside a block."""
    n = 1 << max(1, int(math.ceil(math.log2(max(len(last), 2)))))
    vals = np.full(n, -1, dtype=np.int64)
    vals[:len(last)] = last
    levels = []
    size = 1
    while size <= n:
        blocks = vals.reshape(-1, size)
        srt = np.sort(blocks, axis=1)
        comp = (np.arange(blocks.shape[0], dtype=np.int64)[:, None] << 32) + (srt + 1)
        levels.append(comp.ravel())
        size <<= 1
    return levels


def _prefix_count(levels: list, k: np.ndarray, m: np.ndarray) -> np.ndarray:
    """For each query, how many of the first k positions have last >= m."""
    out = np.zeros(len(k), dtype=np.int64)
    for lv, comp in enumerate(levels):
        size = 1 << lv
        has = ((k >> lv) & 1).astype(bool)
        if not has.any():
            continue
        b = (k[has] >> lv) - 1
        pos = np.searchsorted(comp, (b << 32) + (m[has] + 1), side="left")
        out[has] += (b + 1) * size - pos
    return out


class RunContext:
    """Everything about one run that features at any tick are read from (built once per run)."""

    def __init__(self, t: Table, run: int):
        a = t.a
        k = t.run_pos(run)
        lo, hi = int(a["run_start"][k]), int(a["run_stop"][k])
        self.run, self.lo, self.n = run, lo, hi - lo
        self.scenario = int(a["run_scenario"][k])
        self.weekday0 = int(a["run_weekday0"][k])
        sl = slice(lo, hi)
        self.cs = np.asarray(a["collected"][sl], dtype=np.float64)   # seconds since the run's origin
        self.rs = np.asarray(a["received"][sl], dtype=np.float64)
        self.c, self.r = self.cs / 60.0, self.rs / 60.0                                           # minutes
        acc = np.asarray(a["accessioned"][sl], dtype=np.float64) / 60.0
        self.w = np.asarray(a["window"][sl], dtype=np.float64)
        self.stat = np.asarray(a["stat"][sl], dtype=bool)
        self.tier = np.asarray(a["tier"][sl], dtype=np.int64)
        self.rtype = np.asarray(a["route_type"][sl], dtype=np.int64)
        units, self.unit_l = np.unique(np.asarray(a["unit"][sl]), return_inverse=True)
        routes, self.route_l = np.unique(np.asarray(a["route"][sl]), return_inverse=True)
        self.units, self.routes = units, routes
        U, R = len(units), len(routes)
        self.U, self.R = U, R
        self.S = S = 1 + U + R + 2
        n = self.n
        scopes = np.stack([np.zeros(n, dtype=np.int64), 1 + self.unit_l, 1 + U + self.route_l,
                           1 + U + R + self.rtype], 1)
        self.scopes = scopes
        received = np.isfinite(self.r)
        end = np.nanmax(np.concatenate([self.c + self.w + GRACE_MIN, self.r[received], acc[np.isfinite(acc)]]))
        self.L = L = PAD + int(math.ceil(end)) + 2
        cmin = PAD + np.ceil(self.c).astype(np.int64)                     # first minute that counts the collection
        rmin = np.where(received, PAD + np.ceil(np.nan_to_num(self.r, nan=0)).astype(np.int64), L - 1)

        def cum(index: np.ndarray, rows_scopes: np.ndarray, weight=None) -> np.ndarray:
            flat = (rows_scopes * L + index[:, None]).ravel()
            wts = None if weight is None else np.repeat(weight, rows_scopes.shape[1])
            return np.cumsum(np.bincount(flat, weights=wts, minlength=S * L).reshape(S, L), axis=1)

        self.COL = cum(cmin, scopes).astype(np.int32)
        rec_scopes = scopes[received]
        self.REC = cum(rmin[received], rec_scopes).astype(np.int32)
        lab = np.zeros((received.sum(), 1), dtype=np.int64)
        self.STAT_REC = cum(rmin[received], lab, self.stat[received].astype(float))[0].astype(np.int32)
        acc_ok = np.isfinite(acc)
        self.ACC = cum(PAD + np.ceil(acc[acc_ok]).astype(np.int64), np.zeros((acc_ok.sum(), 1), dtype=np.int64))[0] \
            .astype(np.int32)
        # Outstanding over minutes [start, last]: collected by then, not yet received, within window + grace.
        start = cmin
        last = np.minimum(np.where(received, rmin - 1, L - 2), PAD + np.floor(self.c + self.w + GRACE_MIN).astype(np.int64))
        last = np.minimum(last, L - 2)
        self.start, self.last = start, last
        live = last >= start
        plus = (scopes[live] * L + start[live, None]).ravel()
        minus = (scopes[live] * L + (last[live] + 1)[:, None]).ravel()
        diff = np.bincount(plus, minlength=S * L) - np.bincount(minus, minlength=S * L)
        self.OUT = np.cumsum(diff.reshape(S, L), axis=1).astype(np.int32)
        # Oldest outstanding age, swept per scope: rows of a scope are in collection order; the oldest outstanding at
        # minute m is the first row whose running maximum of `last` reaches m, if it has started by m.
        order = np.lexsort((np.arange(4 * n) % n, (scopes.T.ravel())))
        sc_flat = scopes.T.ravel()[order]
        row_flat = (np.arange(4 * n) % n)[order]
        big = np.int64(L + 2)
        runmax = np.maximum.accumulate(sc_flat * big + last[row_flat])
        mins = np.arange(L, dtype=np.int64)
        q = (np.arange(S, dtype=np.int64)[:, None] * big + mins[None, :]).ravel()
        p = np.searchsorted(runmax, q, side="left")
        pc = np.minimum(p, len(runmax) - 1)
        ok = (p < len(runmax)) & (sc_flat[pc] == np.repeat(np.arange(S), L)) & (start[row_flat[pc]] <= np.tile(mins, S))
        age = np.tile(mins - PAD, S) - self.c[row_flat[pc]]
        self.OLD = np.where(ok, age, 0.0).reshape(S, L).astype(np.float32)
        # Time (minutes) of the last receipt at or before each minute, per scope.
        lastrec = np.full(S * L, -np.inf)
        flat = (rec_scopes * L + rmin[received, None]).ravel()
        np.maximum.at(lastrec, flat, np.repeat(self.r[received], 4))
        self.LAST = np.maximum.accumulate(lastrec.reshape(S, L), axis=1)
        # Receipts per transit scope (route types, then routes) sorted by time, for the rolling quantiles.
        tsc = np.concatenate([1 + U + R + self.rtype[received], 1 + U + self.route_l[received]])
        tr = np.concatenate([self.r[received], self.r[received]])
        tt = np.concatenate([(self.r - self.c)[received], (self.r - self.c)[received]])
        o = np.lexsort((tr, tsc))
        self.t_key, self.t_val = tsc[o] * 1e6 + tr[o], tt[o]
        # Outstanding-older counts: rows sorted by (route, collection) with a prefix tree over `last`.
        o = np.lexsort((np.arange(n), self.c, self.route_l))
        self.o_key = self.route_l[o] * 1e6 + self.c[o]
        self.o_levels = _prefix_tree(self.o_key, last[o] - PAD)
        self.o_route_start = np.searchsorted(self.route_l[o], np.arange(R), side="left")
        # Batch-mates: same unit and route, collected within 20 minutes either side.
        grp = self.unit_l * R + self.route_l
        o = np.lexsort((np.arange(n), self.c, grp))
        gkey = grp[o] * 1e6 + self.c[o]
        own = grp * 1e6 + self.c
        lo_b = np.searchsorted(gkey, own - BATCH_MATE_MIN, side="left")
        hi_b = np.searchsorted(gkey, own + BATCH_MATE_MIN, side="right")
        kb = max(1, int((hi_b - lo_b).max()))
        idx = lo_b[:, None] + np.arange(kb)[None, :]
        valid = idx < hi_b[:, None]
        cand = o[np.minimum(idx, n - 1)]
        valid &= cand != np.arange(n)[:, None]
        self.mates = np.where(valid, cand, -1)
        # Token candidates: the nearest SIDE_TOKENS same-unit-or-route rows before and after each row in run order,
        # within 60 minutes. Before-side rows are all collected by the tick; the after-side list is in collection
        # order, so the rows collected by the tick are a prefix of it.
        self.before, self.after = self._sides()

    def _sides(self) -> tuple[np.ndarray, np.ndarray]:
        n, rows = self.n, np.arange(self.n)
        lists_b, lists_a = [], []
        for key in (self.unit_l, self.route_l):
            o = np.lexsort((rows, key))                     # rows are in run order already
            pos = np.empty(n, dtype=np.int64)
            pos[o] = np.arange(n)
            k_sorted = key[o]
            for sign, out in ((-1, lists_b), (1, lists_a)):
                p = pos[:, None] + sign * np.arange(1, SIDE_TOKENS + 1)[None, :]
                inside = (p >= 0) & (p < n)
                pc = np.clip(p, 0, n - 1)
                j = o[pc]
                same = inside & (k_sorted[pc] == key[:, None])
                near = np.abs(self.c[j] - self.c[:, None]) <= TOKEN_RANGE_MIN
                out.append(np.where(same & near, j, -1))
        result = []
        for parts, sign in ((lists_b, -1), (lists_a, 1)):
            j = np.concatenate(parts, 1)
            ok = j >= 0
            jc = np.where(ok, j, 0)
            # nearest first: by distance in run order (position difference), duplicates removed
            dist = np.where(ok, sign * (jc - rows[:, None]), np.iinfo(np.int64).max)
            o = np.argsort(dist, axis=1, kind="stable")
            js, ds = np.take_along_axis(np.where(ok, j, -1), o, 1), np.take_along_axis(dist, o, 1)
            dup = np.zeros_like(ok)
            dup[:, 1:] = (js[:, 1:] == js[:, :-1]) & (js[:, 1:] >= 0)
            js = np.where(dup, -1, js)
            ds = np.where(dup, np.iinfo(np.int64).max, ds)
            o = np.argsort(ds, axis=1, kind="stable")[:, :SIDE_TOKENS]
            result.append(np.take_along_axis(js, o, 1))
        return result[0], result[1]


# --- Features ----------------------------------------------------------------------------------------------------

def _quantiles_rows(vals: np.ndarray, counts: np.ndarray, qs=(0.5, 0.9)) -> list[np.ndarray]:
    """Linear-interpolation quantiles of each row's first `counts` entries of an ascending-sorted matrix."""
    out = []
    rows = np.arange(len(vals))
    for q in qs:
        pos = q * np.maximum(counts - 1, 0)
        lo = np.floor(pos).astype(np.int64)
        hi = np.minimum(lo + 1, np.maximum(counts - 1, 0))
        if vals.shape[1] == 0:
            out.append(np.full(len(vals), np.nan))
            continue
        lo_c, hi_c = np.minimum(lo, vals.shape[1] - 1), np.minimum(hi, vals.shape[1] - 1)
        a, b = vals[rows, lo_c], vals[rows, hi_c]
        with np.errstate(invalid="ignore"):
            v = a + (pos - lo) * np.where(np.isfinite(b) & np.isfinite(a), b - a, 0.0)
        out.append(np.where(counts > 0, v, np.nan))
    return out


def _transit(ctx: RunContext, scope: np.ndarray, m: np.ndarray) -> tuple:
    lo = np.searchsorted(ctx.t_key, scope * 1e6 + (m - TRANSIT_LOOKBACK_MIN), side="right")
    hi = np.searchsorted(ctx.t_key, scope * 1e6 + m, side="right")
    count = hi - lo
    k = int(min(TRANSIT_CAP, count.max() if len(count) else 0))
    idx = hi[:, None] - k + np.arange(k)[None, :]
    valid = idx >= lo[:, None]
    vals = np.where(valid, ctx.t_val[np.clip(idx, 0, max(len(ctx.t_val) - 1, 0))] if len(ctx.t_val) else 0.0, np.inf)
    vals = np.sort(vals, axis=1)
    p50, p90 = _quantiles_rows(vals, np.minimum(count, k))
    return p50, p90, count


def _roster_arrays(t: Table, ctx: RunContext) -> dict:
    """Per run: the scheduled round interval of each local route by shift, staff by day type and shift, counts."""
    name = t.names["scenarios"][ctx.scenario]
    roster = t.rosters.get(name)
    interval = np.full((ctx.R, 3), np.nan)
    staff = np.full((2, 3), np.nan)
    if roster is None:
        return {"missing": True, "interval": interval, "staff": staff, "lines": 0.0, "routes": 0.0,
                "pattern": DEFAULT_PATTERN}
    for i, g in enumerate(ctx.routes):
        kind_id = t.names["routes"][g].split("|", 1)[1]
        kind, rid = kind_id.split(":", 1)
        info = roster["routes"].get(rid) if kind == "courier" else None
        if info:
            for s, sh in enumerate(SHIFTS):
                v = info["round_interval_min"].get(sh)
                interval[i, s] = np.nan if v is None else float(v)
    for d, day_type in enumerate(("weekday", "weekend")):
        for s, sh in enumerate(SHIFTS):
            v = roster["staff"].get(day_type, {}).get(sh)
            staff[d, s] = np.nan if v is None else float(v)
    return {"missing": False, "interval": interval, "staff": staff, "lines": float(roster["tube_lines"]),
            "routes": float(len(roster["routes"])), "pattern": roster["pattern"]}


def raw_features(t: Table, ctx: RunContext, rows_local: np.ndarray, ticks: np.ndarray, bins=None,
                 roster: dict | None = None, token_rows: bool = False) -> dict:
    """Unscaled inputs for (row, tick) pairs of one run; rows_local index the run's rows. With token_rows, also
    `token_row` [Q, 16]: the table row of each batch-mate token, -1 for padding (explanations name them)."""
    i = np.asarray(rows_local, dtype=np.int64)
    tick = np.asarray(ticks, dtype=np.float64)
    Q = len(i)
    g = ctx.lo + i                                   # table rows
    a = t.a
    c, w = ctx.c[i], ctx.w[i]
    tau = c + tick
    tau_s = ctx.cs[i] + 60.0 * tick
    m = np.floor(tau).astype(np.int64)
    mi = m + PAD
    s_unit, s_route, s_type = ctx.scopes[i, 1], ctx.scopes[i, 2], ctx.scopes[i, 3]
    scen = ctx.scenario
    hist_ok = bool(t.hist["h_has"][scen])
    roster = roster if roster is not None else _roster_arrays(t, ctx)
    num = np.zeros((Q, len(NUM_COLUMNS)))

    def put(name, values):
        num[:, NUM_INDEX[name]] = values

    # 1. specimen and window
    ordered = np.asarray(a["ordered"][g], dtype=np.float64) / 60.0
    has_order = np.isfinite(ordered)
    put("ordered", has_order)
    put("order_to_collection", np.where(has_order, np.log1p(np.maximum(c - np.nan_to_num(ordered), 0)), 0.0))
    put("log_window", np.log(w))
    cls = np.full(Q, len(WINDOW_CLASSES))
    for k, wc in enumerate(WINDOW_CLASSES):
        cls[w == wc] = k
    for k, wc in enumerate(WINDOW_CLASSES):
        put(f"window_{wc}", cls == k)
    put("window_other", cls == len(WINDOW_CLASSES))
    put("t_over_window", tick / w)
    put("window_left", np.log1p(np.maximum(w - tick, 0)))
    put("silence", np.log1p(tick))
    # 2. time of day
    c_day, tau_day = np.floor(c / DAY_MIN), np.floor(tau / DAY_MIN)
    c_mod, tau_mod = c - c_day * DAY_MIN, tau - tau_day * DAY_MIN
    weekday = ((ctx.weekday0 + c_day) % 7).astype(np.int64)
    weekday_tau = ((ctx.weekday0 + tau_day) % 7).astype(np.int64)
    put("collection_hour_sin", np.sin(2 * np.pi * c_mod / DAY_MIN))
    put("collection_hour_cos", np.cos(2 * np.pi * c_mod / DAY_MIN))
    put("weekend", weekday >= 5)
    put("tau_hour_sin", np.sin(2 * np.pi * tau_mod / DAY_MIN))
    put("tau_hour_cos", np.cos(2 * np.pi * tau_mod / DAY_MIN))
    changes = shift_changes(roster["pattern"])
    put("to_shift_change_h", ((changes[None, :] - tau_mod[:, None]) % DAY_MIN).min(1) / 60.0)
    # 7. roster (route_unit group)
    shift = shift_lookup(roster["pattern"])[np.floor(tau_mod / 60).astype(np.int64) % 24]
    interval = roster["interval"][ctx.route_l[i], shift]
    put("route_tube", ctx.rtype[i] == 0)
    put("round_interval", np.where(np.isfinite(interval), np.log1p(np.nan_to_num(interval)), 0.0))
    put("no_round", ~np.isfinite(interval))
    staff = roster["staff"][(weekday_tau >= 5).astype(np.int64), shift]
    put("staff_this_shift", np.where(np.isfinite(staff), np.log1p(np.nan_to_num(staff)), 0.0))
    put("tube_lines", np.log1p(roster["lines"]))
    put("courier_routes", np.log1p(roster["routes"]))
    put("roster_missing", roster["missing"])

    # 3. aggregates
    def window_count(C, s, minutes):
        return C[s, mi] - C[s, mi - minutes]

    for scope_name, s in (("lab", np.zeros(Q, dtype=np.int64)), ("unit", s_unit), ("route", s_route)):
        put(f"out_{scope_name}", np.log1p(ctx.OUT[s, mi]))
        put(f"rec15_{scope_name}", np.log1p(window_count(ctx.REC, s, 15)))
        put(f"rec60_{scope_name}", np.log1p(window_count(ctx.REC, s, 60)))
        last = ctx.LAST[s, mi]
        missing = ~np.isfinite(last)
        put(f"since_rec_{scope_name}", np.log1p(np.where(missing, SINCE_CAP_MIN,
                                                         np.minimum(tau - np.where(missing, 0, last), SINCE_CAP_MIN))))
        put(f"since_rec_{scope_name}_missing", missing)
    route_l = ctx.route_l[i]
    base = ctx.o_route_start[route_l]
    k_self = np.searchsorted(ctx.o_key, route_l * 1e6 + c, side="left")
    older = _prefix_count(ctx.o_levels, k_self, m) - _prefix_count(ctx.o_levels, base, m)
    put("out_route_older", np.log1p(older))
    p90_route = np.expm1(t.hist["h_route"][ctx.routes[route_l], 1]) if hist_ok else np.full(Q, np.nan)
    x = np.where(np.isfinite(p90_route), np.minimum(tau - np.nan_to_num(p90_route), m), -np.inf)
    k_p90 = np.where(np.isfinite(x), np.searchsorted(ctx.o_key, route_l * 1e6 + np.where(np.isfinite(x), x, 0),
                                                     side="left"), base)
    k_p90 = np.maximum(k_p90, base)
    put("out_route_older_p90", np.log1p(_prefix_count(ctx.o_levels, k_p90, m) - _prefix_count(ctx.o_levels, base, m)))
    flow = (window_count(ctx.COL, s_route, 120) - window_count(ctx.COL, s_route, 60)) - window_count(ctx.REC, s_route, 60)
    put("flow_route", np.sign(flow) * np.log1p(np.abs(flow)))
    lab0 = np.zeros(Q, dtype=np.int64)
    put("lab_receipts_over_collections",
        np.log((1 + window_count(ctx.REC, lab0, 60)) /
               (1 + window_count(ctx.COL, lab0, 120) - window_count(ctx.COL, lab0, 60))))
    hourly = t.hist["h_hourly"][scen][np.floor(tau_mod / 60).astype(np.int64) % 24]
    put("lab_volume_ratio", np.log((1 + window_count(ctx.COL, lab0, 60)) / (1 + hourly)) if hist_ok else 0.0)
    p50, p90, n_tr = _transit(ctx, np.concatenate([s_type, s_route]), np.concatenate([m, m]))
    type_hist = t.hist["h_type_p50"][scen][ctx.rtype[i]] if hist_ok else np.full(Q, np.nan)
    route_hist = np.expm1(t.hist["h_route"][ctx.routes[route_l], 0]) if hist_ok else np.full(Q, np.nan)
    for part, sl, hist_med in (("type", slice(0, Q), type_hist), ("route", slice(Q, 2 * Q), route_hist)):
        miss = n_tr[sl] == 0
        put(f"transit_{part}_p50", np.where(miss, 0.0, np.log1p(np.nan_to_num(p50[sl]))))
        put(f"transit_{part}_p90", np.where(miss, 0.0, np.log1p(np.nan_to_num(p90[sl]))))
        ratio = np.log((1 + np.nan_to_num(p50[sl])) / (1 + np.nan_to_num(hist_med, nan=0.0)))
        put(f"transit_{part}_ratio", np.where(miss | ~np.isfinite(hist_med), 0.0, ratio))
        put(f"transit_{part}_n", np.log1p(n_tr[sl]))
        put(f"transit_{part}_missing", miss)
    # batch-mates
    cand = ctx.mates[i]
    ok = cand >= 0
    cc = np.where(ok, cand, 0)
    seen = ok & (ctx.cs[cc] <= tau_s[:, None])
    got = seen & (ctx.rs[cc] <= tau_s[:, None])
    n_seen, n_got = seen.sum(1), got.sum(1)
    put("mates", np.log1p(n_seen))
    put("mates_received", np.log1p(n_got))
    put("mates_share", np.where(n_seen > 0, n_got / np.maximum(n_seen, 1), 0.0))
    first_r = np.where(got, ctx.r[cc], np.inf).min(1) if cand.shape[1] else np.full(Q, np.inf)
    put("mates_since_first_receipt", np.where(n_got > 0, np.log1p(np.maximum(tau - np.where(n_got > 0, first_r, 0), 0)), 0.0))
    mt = np.sort(np.where(got, ctx.r[cc] - ctx.c[cc], np.inf), axis=1)
    med = _quantiles_rows(mt, n_got, (0.5,))[0]
    put("mates_transit", np.where(n_got > 0, np.log1p(np.nan_to_num(med)), 0.0))
    put("mates_none_received", n_got == 0)

    # 4. series: 36 lags of 5 minutes ending at the tick's minute
    edges = mi[:, None] - LAG_MIN * np.arange(N_LAGS, -1, -1)[None, :]   # [Q, 37], oldest first
    ends = edges[:, 1:]

    def counts(C, s):
        return np.diff(C[s[:, None], edges], axis=1)

    def level(A, s):
        return A[s[:, None], ends]

    series = {}
    zero = np.zeros(Q, dtype=np.int64)
    series["series_lab"] = np.stack([
        np.log1p(counts(ctx.COL, zero)), np.log1p(counts(ctx.REC, zero)),
        np.log1p(np.diff(ctx.STAT_REC[edges], axis=1)), np.log1p(np.diff(ctx.ACC[edges], axis=1)),
        np.log1p(level(ctx.OUT, zero)), np.minimum(level(ctx.OLD, zero) / 60.0, OLDEST_CAP_H)], 1)
    for key, s in (("series_unit", s_unit), ("series_route", s_route)):
        series[key] = np.stack([np.log1p(counts(ctx.COL, s)), np.log1p(counts(ctx.REC, s)),
                                np.log1p(level(ctx.OUT, s)), np.minimum(level(ctx.OLD, s) / 60.0, OLDEST_CAP_H)], 1)

    # 5. batch-mate tokens: nearest 16 of the before-list and the after-list rows collected by the tick
    bj, aj = ctx.before[i], ctx.after[i]
    bok = bj >= 0
    aok = (aj >= 0) & (ctx.cs[np.where(aj >= 0, aj, 0)] <= tau_s[:, None])
    js = np.concatenate([bj, aj], 1)
    oks = np.concatenate([bok, aok], 1)
    jc = np.where(oks, js, 0)
    dist = np.where(oks, np.abs(ctx.c[jc] - c[:, None]), np.inf)
    order = np.argsort(dist, axis=1, kind="stable")[:, :N_TOKENS]
    tj = np.take_along_axis(jc, order, 1)
    tmask = np.take_along_axis(oks, order, 1)
    if tj.shape[1] < N_TOKENS:
        pad = N_TOKENS - tj.shape[1]
        tj = np.pad(tj, ((0, 0), (0, pad)))
        tmask = np.pad(tmask, ((0, 0), (0, pad)))
    rec_by = ctx.rs[tj] <= tau_s[:, None]
    age = tau[:, None] - ctx.c[tj]
    out_by = ~rec_by & (age <= ctx.w[tj] + GRACE_MIN)
    tokens = np.stack([
        ctx.unit_l[tj] == ctx.unit_l[i][:, None], ctx.route_l[tj] == route_l[:, None],
        (ctx.c[tj] - c[:, None]) / 30.0, rec_by, np.where(rec_by, (ctx.r[tj] - ctx.c[tj]) / 60.0, 0.0),
        np.where(out_by, age / 60.0, 0.0), ctx.stat[tj], ctx.tier[tj] == 0, ctx.tier[tj] == 1, ctx.tier[tj] == 2,
        ctx.rtype[tj] == 0, np.log(ctx.w[tj] / 1440.0)], -1).astype(np.float64)
    tokens = np.clip(np.where(tmask[..., None], tokens, 0.0), -CLIP, CLIP)

    # 6. fingerprint
    fp = np.zeros((Q, len(FINGERPRINT_COLUMNS)))
    if hist_ok:
        fp[:, 0:17] = t.hist["h_lab"][scen]
        fp[:, 17:20] = t.hist["h_route"][ctx.routes[route_l]]
        kind_vals = t.hist["h_kind"][scen][np.asarray(a["route_kind"][g], dtype=np.int64)]
        unit_g = ctx.units[ctx.unit_l[i]]
        fp[:, 20:23] = _shrink(t.hist["h_unit_own"][unit_g], t.hist["h_unit_n"][unit_g], kind_vals)
        fp[:, 23:29] = t.hist["h_misc"][scen]
        fp[:, 29] = 1.0
    fp = np.nan_to_num(fp, nan=0.0)

    tests_bits = np.asarray(a["tests"][g], dtype=np.int64)
    n_tests = len(vocabularies_cached()["tests"])
    tests = ((tests_bits[:, None] >> np.arange(n_tests)[None, :]) & 1).astype(np.float32)
    cat = np.stack([np.asarray(a["variant"][g], dtype=np.int64), np.asarray(a["unit_kind"][g], dtype=np.int64),
                    np.asarray(a["route_kind"][g], dtype=np.int64), ctx.tier[i],
                    ctx.stat[i].astype(np.int64), weekday, weekday_tau], 1)
    receipt = (ctx.rs[i] - ctx.cs[i]) / 60.0
    bins = bins or bins_cached()
    out = {"cat": cat, "tests": tests, "num": num, "fingerprint": fp, **series, "tokens": tokens,
           "token_mask": tmask, "tick": tick, "window": w, "target_bin": bins.index(receipt).astype(np.int64),
           "breach": ~np.isfinite(receipt) | (receipt > w), "specimen_index": g.astype(np.int64),
           "run": np.full(Q, ctx.run, dtype=np.int64)}
    if token_rows:
        out["token_row"] = np.where(tmask, ctx.lo + tj, -1).astype(np.int64)
    return out


_VOCAB: dict | None = None
_BINS = None


def vocabularies_cached() -> dict:
    global _VOCAB
    if _VOCAB is None:
        _VOCAB = vocabularies()
    return _VOCAB


def bins_cached():
    global _BINS
    if _BINS is None:
        _BINS = tbins.build()
    return _BINS


# --- Scaling and the schema --------------------------------------------------------------------------------------

def _robust(x: np.ndarray, axis=0) -> tuple[np.ndarray, np.ndarray]:
    center = np.median(x, axis=axis)
    q75, q25 = np.percentile(x, [75, 25], axis=axis)
    scale = q75 - q25
    std = np.std(x, axis=axis)
    scale = np.where(scale > 1e-9, scale, np.where(std > 1e-9, std, 1.0))
    return center, scale


def fit_schema(raw: dict, *, fitted_on: dict | None = None, store_info: dict | None = None,
               loaded: dict | None = None) -> dict:
    """Medians and interquartile ranges from raw train samples (one dict of stacked raw features)."""
    loaded = loaded or config.load()
    kinds = np.array([k for _, _, k in NUM_COLUMNS])
    center, scale = _robust(raw["num"])
    center = np.where(kinds == "num", center, 0.0)
    scale = np.where(kinds == "num", scale, 1.0)
    fc, fs = _robust(raw["fingerprint"])
    flag = np.array([name == "history_available" for name in FINGERPRINT_COLUMNS])
    fc, fs = np.where(flag, 0.0, fc), np.where(flag, 1.0, fs)
    series = {}
    for key in SERIES:
        x = np.moveaxis(raw[key], 1, -1).reshape(-1, raw[key].shape[1])
        sc, ss = _robust(x)
        series[key] = {"center": sc.tolist(), "scale": ss.tolist()}
    vocab = vocabularies(loaded)
    return {
        "version": SCHEMA_VERSION, "policy_hash": loaded["policy_hash"], "clip": CLIP,
        "num_columns": [list(c) for c in NUM_COLUMNS], "num_groups": [list(g) for g in num_groups()],
        "fingerprint_columns": list(FINGERPRINT_COLUMNS), "series": {k: list(v) for k, v in SERIES.items()},
        "token_columns": list(TOKEN_COLUMNS), "cat_columns": list(CAT_COLUMNS),
        "vocab": {k: vocab[k] for k in ("variant", "unit_kind", "route_kind", "tier", "priority", "weekday",
                                        "weekday_tau", "tests", "window_classes")},
        "scalers": {"num": {"center": center.tolist(), "scale": scale.tolist()},
                    "fingerprint": {"center": fc.tolist(), "scale": fs.tolist()}, **series},
        "constants": {"tick_min": TICK_MIN, "grace_min": GRACE_MIN, "lags": N_LAGS, "lag_min": LAG_MIN,
                      "tokens": N_TOKENS, "batch_mate_min": BATCH_MATE_MIN, "token_range_min": TOKEN_RANGE_MIN,
                      "transit_lookback_min": TRANSIT_LOOKBACK_MIN, "since_cap_min": SINCE_CAP_MIN,
                      "shrink_n": SHRINK_N, "transit_cap": TRANSIT_CAP, "side_tokens": SIDE_TOKENS},
        "fitted_on": fitted_on or {}, "store": store_info or {},
    }


def save_schema(schema: dict, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    return path


def load_schema(path: Path, loaded: dict | None = None) -> dict:
    schema = json.loads(Path(path).read_text(encoding="utf-8"))
    check_schema(schema, loaded)
    return schema


def check_schema(schema: dict, loaded: dict | None = None) -> None:
    if schema.get("version") != SCHEMA_VERSION:
        raise RuntimeError(f"feature schema version {schema.get('version')!r} is not {SCHEMA_VERSION!r}")
    loaded = loaded or config.load()
    if schema.get("policy_hash") != loaded["policy_hash"]:
        raise RuntimeError("the feature schema was fitted under a different policy")
    if [list(c) for c in NUM_COLUMNS] != schema["num_columns"]:
        raise RuntimeError("the feature schema's numeric columns differ from this code's")


def transform(raw: dict, schema: dict) -> dict:
    """Scaled float32 inputs plus the passthrough keys: the batch TracerNet reads."""
    clip = schema["clip"]
    sc = schema["scalers"]

    def scale(x, s, axis_shape=None):
        c, k = np.asarray(s["center"]), np.asarray(s["scale"])
        if axis_shape is not None:
            c, k = c[:, None], k[:, None]
        return np.clip((x - c) / k, -clip, clip).astype(np.float32)

    out = {"cat": raw["cat"].astype(np.int64), "tests": raw["tests"].astype(np.float32),
           "num": scale(raw["num"], sc["num"]), "fingerprint": scale(raw["fingerprint"], sc["fingerprint"])}
    for key in SERIES:
        out[key] = scale(raw[key], sc[key], axis_shape=True)
    out["tokens"] = raw["tokens"].astype(np.float32)
    out["token_mask"] = raw["token_mask"].astype(bool)
    for key in ("tick", "window"):
        out[key] = raw[key].astype(np.float32)
    for key in ("target_bin", "specimen_index", "run"):
        out[key] = raw[key].astype(np.int64)
    out["breach"] = raw["breach"].astype(bool)
    return out


def concat(parts: list[dict]) -> dict:
    keys = parts[0].keys()
    return {k: np.concatenate([p[k] for p in parts]) for k in keys}


def to_torch(batch: dict) -> dict:
    """The batch as torch tensors (imports torch only here)."""
    import torch
    return {k: torch.from_numpy(np.ascontiguousarray(v)) for k, v in batch.items()}


def net_config(schema: dict, **overrides):
    """A TracerNetConfig whose input sizes follow the schema (the factor-group names are net.py's)."""
    from src.models.tracernet.net import TracerNetConfig
    dims = dict((c, d) for c, _, d in TracerNetConfig().vocab)
    vocab = tuple((c, len(schema["vocab"][c]), dims[c]) for c in CAT_COLUMNS)
    return TracerNetConfig(vocab=vocab, n_tests=len(schema["vocab"]["tests"]),
                           num_groups=tuple(tuple(g) for g in schema["num_groups"]),
                           n_fingerprint=len(schema["fingerprint_columns"]),
                           series_channels=tuple((k, len(schema["series"][k])) for k in SERIES),
                           token_features=len(schema["token_columns"]), **overrides)


# --- The builder -------------------------------------------------------------------------------------------------

def features_for_run(t: Table, run: int, rows: np.ndarray, ticks: np.ndarray, schema: dict | None = None,
                     ctx: RunContext | None = None, bins=None) -> dict:
    """Features for table rows of one run at their ticks; scaled when a schema is given, raw otherwise."""
    ctx = ctx or RunContext(t, run)
    raw = raw_features(t, ctx, np.asarray(rows, dtype=np.int64) - ctx.lo, ticks, bins=bins)
    return transform(raw, schema) if schema is not None else raw


class FeatureBuilder:
    """The inference-time builder: a table read from the observed store, its run contexts, and a schema."""

    def __init__(self, store_dir: Path | None = None, schema: dict | None = None, *, table: Table | None = None,
                 runs=None, splits=None, cache_runs: int = 8):
        self.table = table if table is not None else load_table(store_dir, runs=runs, splits=splits)
        if schema is not None:
            check_schema(schema)
        self.schema = schema
        self.bins = tbins.build()
        self._ctx: dict[int, RunContext] = {}
        self.cache_runs = cache_runs

    def context(self, run: int) -> RunContext:
        if run not in self._ctx:
            if len(self._ctx) >= self.cache_runs:
                self._ctx.pop(next(iter(self._ctx)))
            self._ctx[run] = RunContext(self.table, run)
        return self._ctx[run]

    def build(self, rows, ticks, raw: bool = False) -> dict:
        """Features for table rows at ticks (rows may span runs; output follows the input order)."""
        rows, ticks = np.asarray(rows, dtype=np.int64), np.asarray(ticks, dtype=np.float64)
        runs = self.table.a["run"][rows]
        parts, where = [], []
        for run in np.unique(runs):
            sel = np.flatnonzero(runs == run)
            ctx = self.context(int(run))
            r = raw_features(self.table, ctx, rows[sel] - ctx.lo, ticks[sel], bins=self.bins)
            parts.append(r if raw or self.schema is None else transform(r, self.schema))
            where.append(sel)
        out = concat(parts)
        back = np.argsort(np.concatenate(where), kind="stable")
        return {k: v[back] for k, v in out.items()}

    def build_ids(self, specimen_ids, ticks, raw: bool = False) -> dict:
        return self.build(self.table.rows_of(specimen_ids), ticks, raw=raw)
