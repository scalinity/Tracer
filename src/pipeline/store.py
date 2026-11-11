"""Load the frozen dataset into two DuckDB files, so the truth log cannot leak into a model.

    python -m src.pipeline.store

data/store/observed.duckdb holds what a model may see: `specimens` (what is known when the specimen
is collected, plus the policy window) and `events` (the scans). data/store/truth.duckdb holds what
only the evaluation may see: the planted fault, the true timings, the breach label and the hidden
path. Only src/pipeline/evaluate.py opens the truth file, through open_truth(); a test fails if any
module under src/models/ refers to it (AGENTS.md, docs/architecture.md §9).

The 30 runs are split by run number, never by specimen: runs 0 to 23 are for fitting and the last
six, 24 to 29, are held out for testing. Runs are consecutive weeks, so the held-out runs are also
the latest. The load refuses a dataset whose SHA-256 differs from its manifest or whose policy
differs from the one in the repository, and it checks its row counts against the manifest.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl

from src.generator import config

ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = ROOT / "datasets" / "tracer-synthetic-v1.jsonl.gz"
MANIFEST_PATH = ROOT / "datasets" / "manifest.json"
STORE_DIR = ROOT / "data" / "store"
OBSERVED_NAME = "observed.duckdb"
TRUTH_NAME = "truth.duckdb"

TEST_RUNS = range(24, 30)

TARGET_KEYS = ("dispatch_wait_min", "tube_transit_min", "station_dwell_min", "hand_carry_min", "stalled_min",
               "misrouted_min", "receipt_after_min", "accession_wait_min", "plate_wait_min")


def run_of(specimen_id: str) -> int:
    """SP-r07-000123 is specimen 123 of run 7."""
    return int(specimen_id.split("-")[1][1:])


def split_of(run: int) -> str:
    return "test" if run in TEST_RUNS else "fit"


def ts(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def read_dataset(dataset: Path, manifest: dict) -> list[dict]:
    raw = dataset.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
        raise RuntimeError(f"{dataset.name} does not match the SHA-256 in its manifest")
    return [json.loads(line) for line in gzip.decompress(raw).decode("utf-8").splitlines()]


def tables(records: list[dict]) -> dict[str, pl.DataFrame]:
    specimens, events, truth, segments = [], [], [], []
    for rec in records:
        brief, trail = rec["brief"], rec["trail"]
        sid, fixed, label = brief["specimen_id"], brief["fixed"], brief["label"]
        run = run_of(sid)
        specimens.append({
            "specimen_id": sid, "run": run, "split": split_of(run), "specimen_type": fixed["type"],
            "tests": fixed["tests"], "unit": fixed["unit"], "priority": fixed["priority"],
            "planned_route": fixed["route"], "policy_id": label["policy_id"], "window_min": label["window_min"],
            "urgency_tier": label["urgency_tier"]})
        for seq, e in enumerate(trail["observed_events"]):
            events.append({"specimen_id": sid, "seq": seq, "activity": e["activity"], "timestamp": ts(e["timestamp"]),
                           "location": e["location"], "actor_role": e["actor_role"], "note": e.get("note")})
        truth.append({
            "specimen_id": sid, "run": run, "split": split_of(run), "planted_fault": brief["planted_fault"],
            "collected_at": ts(fixed["collected_at"]), "clock_started": label["clock_started"],
            "elapsed_min": label["elapsed_min"], "breach": label["breach"],
            **{k: brief["targets"][k] for k in TARGET_KEYS},
            "outcome_received": trail["outcome"]["received"], "outcome_plated": trail["outcome"]["plated"]})
        for seq, s in enumerate(trail["truth_segments"]):
            segments.append({"specimen_id": sid, "seq": seq, "state": s["state"], "start": ts(s["start"]),
                             "end": ts(s["end"]), "location": s["location"]})
    int_cols = {k: pl.Int64 for k in TARGET_KEYS} | {"elapsed_min": pl.Int64}
    return {
        "specimens": pl.DataFrame(specimens),
        "events": pl.DataFrame(events, schema_overrides={"note": pl.Utf8}),
        "truth_specimens": pl.DataFrame(truth, schema_overrides=int_cols),
        "truth_segments": pl.DataFrame(segments, schema_overrides={"end": pl.Datetime}),
    }


def write_db(path: Path, frames: dict[str, pl.DataFrame]) -> None:
    path.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as tmp, duckdb.connect(str(path)) as con:
        for name, df in frames.items():
            parquet = Path(tmp) / f"{name}.parquet"
            df.write_parquet(parquet)
            con.execute(f"CREATE TABLE {name} AS SELECT * FROM read_parquet('{parquet}')")


def build(dataset: Path = DATASET_PATH, manifest_path: Path = MANIFEST_PATH, store_dir: Path = STORE_DIR) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["policy_hash"] != config.load()["policy_hash"]:
        raise RuntimeError("the dataset was made under a different policy than the one in the repository")
    records = read_dataset(dataset, manifest)
    if len(records) != manifest["accepted"]:
        raise RuntimeError(f"{len(records)} records, but the manifest says {manifest['accepted']} were accepted")
    frames = tables(records)
    store_dir.mkdir(parents=True, exist_ok=True)
    write_db(store_dir / OBSERVED_NAME, {k: frames[k] for k in ("specimens", "events")})
    write_db(store_dir / TRUTH_NAME, {k: frames[k] for k in ("truth_specimens", "truth_segments")})
    return {name: df.height for name, df in frames.items()}


def source_sha256(manifest_path: Path = MANIFEST_PATH) -> str:
    """The SHA-256 of the file the store was built from, for artifacts that carry their source."""
    return json.loads(manifest_path.read_text(encoding="utf-8"))["sha256"]


def open_observed(store_dir: Path = STORE_DIR) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(store_dir / OBSERVED_NAME), read_only=True)


def open_truth(store_dir: Path = STORE_DIR) -> duckdb.DuckDBPyConnection:
    """For src/pipeline/evaluate.py only. A test fails if anything else refers to this."""
    return duckdb.connect(str(store_dir / TRUTH_NAME), read_only=True)


# --- Store v2 (docs/neural/spec.md §6.4) --------------------------------------------------------------------
#
# build_v2 loads the simulated laboratory weeks (src/sim/run.py's files under data/sim/) into the same two-file
# split. Observed: `specimens` (the v1 columns plus the scenario, its family, the replicate, the role, the scored
# flag and the unit and route kinds), `events` (the v1 columns), `roster` (what each laboratory knows about
# itself: no rate, no fault, no disruption), `runs` and `meta`. Truth: `truth_specimens`, `truth_segments`
# (with the substate), `truth_disruptions` (realized windows), `truth_scenarios` (hidden parameters,
# flattened) and `meta`. A run is a (scenario, replicate) pair, keyed by the run number in the specimen id
# (100000 + 10 x scenario_index + replicate). Replicate 0 is the scenario's history; the rest are scored.
# The load refuses a simulation whose policy, library or files differ from what its manifest records, and a
# split that puts one family in two splits. data/store-v2/manifest.json records the input and output hashes.

STORE_V2_DIR = ROOT / "data" / "store-v2"
STORE_V2_MANIFEST = "manifest.json"
V2_SPLITS = ("train", "val", "test", "test_ood")
V2_DAY0 = datetime(2026, 1, 5)  # Monday, day 0 of every simulated replicate (spec §5.2)
V2_SCORED_DAYS = 7
HISTORY_REPLICATE = 0
TRUTH_V2_TABLES = ("truth_specimens", "truth_segments", "truth_disruptions", "truth_scenarios")
OBSERVED_V2_TABLES = ("specimens", "events", "roster", "runs")
ROSTER_KINDS = ("unit", "tube_line", "courier_route", "receiving_shift")


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def library_bytes(records: list[dict]) -> bytes:
    """The deterministic gzip src/sim/run.py's write_records produces: one sorted-key JSON line per record,
    mtime 0, no file name. Its SHA-256 is the library hash a simulation manifest records."""
    import io
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz:
        for rec in records:
            gz.write((json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    return buf.getvalue()


def read_library(library) -> tuple[list[dict], str]:
    """The library's records and SHA-256, from a .jsonl.gz path (the file's own bytes) or from records."""
    if isinstance(library, (str, Path)):
        raw = Path(library).read_bytes()
        text = gzip.decompress(raw).decode("utf-8") if raw[:2] == b"\x1f\x8b" else raw.decode("utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()], _sha256_bytes(raw)
    records = list(library)
    return records, _sha256_bytes(library_bytes(records))


def _scenario_of(rec: dict) -> dict:
    """A library record as one flat scenario dict: either the scenario itself with scenario_id, family_id and
    scenario_index beside its configuration (as simulate_library takes it), or {"scenario": {...}, ...}, the frozen
    library's shape, whose meta block gives scenario_id, family_id, scenario_index and stratum."""
    if "scenario" in rec and isinstance(rec["scenario"], dict):
        out = dict(rec["scenario"])
        out.update({k: v for k, v in rec.items() if k not in ("scenario", "meta")})
        meta = rec.get("meta") or {}
        out.update({k: meta[k] for k in ("scenario_id", "family_id", "scenario_index", "stratum") if k in meta})
        return out
    return dict(rec)


def split_map(splits, scenarios: list[dict]) -> dict[str, str]:
    """scenario_id -> split, from a mapping, a splits document with a `scenarios` mapping, a path to one, or
    None (each library record's own `split`)."""
    if splits is None:
        return {s["scenario_id"]: s.get("split") for s in scenarios}
    if isinstance(splits, (str, Path)):
        splits = json.loads(Path(splits).read_text(encoding="utf-8"))
    if "scenarios" in splits and isinstance(splits["scenarios"], dict):
        splits = splits["scenarios"]
    return {str(k): v for k, v in splits.items()}


def _flatten(node, prefix: str = "") -> dict:
    out = {}
    if isinstance(node, dict):
        for k in sorted(node):
            out.update(_flatten(node[k], f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(node, list) and node and all(isinstance(x, dict) for x in node):
        for i, x in enumerate(node):
            out.update(_flatten(x, f"{prefix}[{i}]"))
    else:
        out[prefix] = node
    return out


def roster_rows(scenario_id: str, roster: dict) -> list[dict]:
    """One row per unit, tube line, courier route and receiving shift. Attributes hold the unit kind and route,
    the scheduled round intervals by shift and the scheduled staff by shift and day type, nothing else."""
    rows = []
    for u in roster["units"]:
        rows.append({"scenario_id": scenario_id, "kind": "unit", "key": u["unit_id"],
                     "attributes": {"kind": u["kind"], "route": u["route"], "tube_line_id": u.get("tube_line_id"),
                                    "courier_route_id": u.get("courier_route_id")}})
    for line in roster["tube_lines"]:
        rows.append({"scenario_id": scenario_id, "kind": "tube_line", "key": line, "attributes": {}})
    for r in roster["courier_routes"]:
        rows.append({"scenario_id": scenario_id, "kind": "courier_route", "key": r["route_id"],
                     "attributes": {"kind": r["kind"], "round_interval_min": r["round_interval_min"]}})
    for day_type, by_shift in sorted(roster["receiving_staff"].items()):
        for shift, staff in sorted(by_shift.items()):
            rows.append({"scenario_id": scenario_id, "kind": "receiving_shift", "key": f"{day_type}.{shift}",
                         "attributes": {"day_type": day_type, "shift": shift, "staff": staff,
                                        "shift_pattern": roster["shift_pattern"]}})
    for row in rows:
        row["attributes"] = json.dumps(row["attributes"], sort_keys=True)
    return rows


def _v2_part(job: tuple) -> dict:
    """Parse one replicate file into parquet parts (observed and truth), in a worker process."""
    path, tmp, scenario_id, family_id, split, replicate, part = job
    role = "history" if replicate == HISTORY_REPLICATE else "scored"
    spec_cols = {k: [] for k in ("specimen_id", "run", "split", "specimen_type", "tests", "unit", "priority",
                                 "planned_route", "policy_id", "window_min", "urgency_tier", "scenario_id",
                                 "family_id", "replicate", "role", "scored", "unit_kind", "route_kind", "route_id")}
    ev_cols = {k: [] for k in ("specimen_id", "seq", "activity", "timestamp", "location", "actor_role")}
    tr_cols = {k: [] for k in ("specimen_id", "run", "split", "planted_fault", "collected_at", "clock_started",
                               "elapsed_min", "breach", *TARGET_KEYS, "outcome_received", "outcome_plated",
                               "scenario_id", "replicate", "cause", "disruption_ids", "outcome_class", "route_kind",
                               "horizon_cut")}
    seg_cols = {k: [] for k in ("specimen_id", "seq", "state", "start", "end", "location", "substate")}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            brief, trail = rec["brief"], rec["trail"]
            sid, fixed, label, v2 = brief["specimen_id"], brief["fixed"], brief["label"], brief["v2"]
            if v2["scenario_id"] != scenario_id or v2["replicate"] != replicate:
                raise RuntimeError(f"{sid} is in the file of {scenario_id} replicate {replicate} but says otherwise")
            run = run_of(sid)
            for k, v in (("specimen_id", sid), ("run", run), ("split", split), ("specimen_type", fixed["type"]),
                         ("tests", fixed["tests"]), ("unit", fixed["unit"]), ("priority", fixed["priority"]),
                         ("planned_route", fixed["route"]), ("policy_id", label["policy_id"]),
                         ("window_min", label["window_min"]), ("urgency_tier", label["urgency_tier"]),
                         ("scenario_id", scenario_id), ("family_id", family_id), ("replicate", replicate),
                         ("role", role), ("scored", bool(v2["scored"])), ("unit_kind", v2["unit_kind"]),
                         ("route_kind", v2["route_kind"]), ("route_id", v2["route_id"])):
                spec_cols[k].append(v)
            for seq, e in enumerate(trail["observed_events"]):
                for k, v in (("specimen_id", sid), ("seq", seq), ("activity", e["activity"]),
                             ("timestamp", e["timestamp"]), ("location", e["location"]),
                             ("actor_role", e["actor_role"])):
                    ev_cols[k].append(v)
            for k, v in (("specimen_id", sid), ("run", run), ("split", split), ("planted_fault", brief["planted_fault"]),
                         ("collected_at", fixed["collected_at"]), ("clock_started", label["clock_started"]),
                         ("elapsed_min", label["elapsed_min"]), ("breach", label["breach"]),
                         *((t, brief["targets"][t]) for t in TARGET_KEYS),
                         ("outcome_received", trail["outcome"]["received"]),
                         ("outcome_plated", trail["outcome"]["plated"]), ("scenario_id", scenario_id),
                         ("replicate", replicate), ("cause", v2["cause"]), ("disruption_ids", v2["disruption_ids"]),
                         ("outcome_class", v2["outcome_class"]), ("route_kind", v2["route_kind"]),
                         ("horizon_cut", bool(v2.get("horizon_cut", False)))):
                tr_cols[k].append(v)
            for seq, s in enumerate(trail["truth_segments"]):
                for k, v in (("specimen_id", sid), ("seq", seq), ("state", s["state"]), ("start", s["start"]),
                             ("end", s["end"]), ("location", s["location"]), ("substate", s.get("substate"))):
                    seg_cols[k].append(v)
    iso = "%Y-%m-%dT%H:%M:%S"
    floats = {k: pl.Float64 for k in (*TARGET_KEYS, "elapsed_min")}
    frames = {
        "specimens": pl.DataFrame(spec_cols, schema_overrides={"tests": pl.List(pl.Utf8), "window_min": pl.Int64,
                                                               "urgency_tier": pl.Int64, "run": pl.Int64,
                                                               "replicate": pl.Int64, "route_id": pl.Utf8}),
        "events": pl.DataFrame(ev_cols, schema_overrides={"seq": pl.Int64})
        .with_columns(pl.col("timestamp").str.to_datetime(iso), pl.lit(None, dtype=pl.Utf8).alias("note")),
        "truth_specimens": pl.DataFrame(tr_cols, schema_overrides={**floats, "disruption_ids": pl.List(pl.Utf8),
                                                                   "run": pl.Int64, "replicate": pl.Int64})
        .with_columns(pl.col("collected_at").str.to_datetime(iso)),
        "truth_segments": pl.DataFrame(seg_cols, schema_overrides={"seq": pl.Int64, "end": pl.Utf8,
                                                                   "substate": pl.Utf8})
        .with_columns(pl.col("start").str.to_datetime(iso), pl.col("end").str.to_datetime(iso)),
    }
    counts = {}
    for name, df in frames.items():
        (Path(tmp) / name).mkdir(parents=True, exist_ok=True)
        df.write_parquet(Path(tmp) / name / f"part-{part:06d}.parquet")
        counts[name] = df.height
    return counts


def _write_tables(path: Path, tmp: Path, names: dict[str, str], extra: dict[str, pl.DataFrame]) -> None:
    """Create each table from its parquet parts in a fixed order, so a rebuild writes the same rows."""
    path.unlink(missing_ok=True)
    with duckdb.connect(str(path)) as con:
        for name, order in names.items():
            con.execute(f"CREATE TABLE {name} AS SELECT * FROM read_parquet('{(tmp / name).as_posix()}/*.parquet') "
                        f"ORDER BY {order}")
        for name, df in extra.items():
            parquet = tmp / f"{name}.parquet"
            df.write_parquet(parquet)
            con.execute(f"CREATE TABLE {name} AS SELECT * FROM read_parquet('{parquet.as_posix()}')")


def build_v2(sim_dir: Path, library, splits, store_dir: Path = STORE_V2_DIR, *, workers: int | None = None) -> dict:
    """Load a simulated library into data/store-v2/observed.duckdb and truth.duckdb. Returns the row counts.

    sim_dir holds src/sim/run.py's files and manifest.json; library is the library file (.jsonl.gz) or its
    records; splits maps scenario ids to splits (or is a splits document, a path to one, or None to use each
    record's own split)."""
    import os
    from concurrent.futures import ProcessPoolExecutor

    sim_dir, store_dir = Path(sim_dir), Path(store_dir)
    manifest_raw = (sim_dir / "manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    loaded = config.load()
    if manifest.get("policy_hash") != loaded["policy_hash"]:
        raise RuntimeError("the simulation was made under a different policy than the one in the repository")
    records, library_sha = read_library(library)
    if manifest.get("library_sha256") is None:
        raise RuntimeError("the simulation manifest records no library hash, so its source cannot be checked")
    if manifest["library_sha256"] != library_sha:
        raise RuntimeError("the library does not match the SHA-256 the simulation manifest records")
    scenarios = {s["scenario_id"]: s for s in map(_scenario_of, records)}
    if len(scenarios) != len(records):
        raise RuntimeError("the library repeats a scenario id")
    split_of_scenario = split_map(splits, list(scenarios.values()))
    families: dict[str, set] = {}
    for sid, sc in scenarios.items():
        split = split_of_scenario.get(sid)
        if split not in V2_SPLITS:
            raise RuntimeError(f"scenario {sid} has split {split!r}, not one of {V2_SPLITS}")
        families.setdefault(sc["family_id"], set()).add(split)
    mixed = sorted(f for f, s in families.items() if len(s) > 1)
    if mixed:
        raise RuntimeError(f"families in more than one split: {mixed[:5]}")
    for f in manifest["files"]:
        path = sim_dir / f["path"]
        if not path.exists():
            raise RuntimeError(f"{f['path']} is listed in the simulation manifest but missing")
        if _sha256_bytes(path.read_bytes()) != f["sha256"]:
            raise RuntimeError(f"{f['path']} does not match the SHA-256 in the simulation manifest")
    runs = [e for e in manifest["runs"] if "dropped" not in e]
    for e in runs:
        if e["scenario_id"] not in scenarios:
            raise RuntimeError(f"simulated scenario {e['scenario_id']} is not in the library")
        if e["split"] != split_of_scenario[e["scenario_id"]]:
            raise RuntimeError(f"scenario {e['scenario_id']} was simulated under split {e['split']} but is assigned "
                               f"to {split_of_scenario[e['scenario_id']]}")

    store_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=store_dir) as tmp_name:
        tmp = Path(tmp_name)
        jobs = [(sim_dir / e["split"] / e["scenario_id"] / f"rep-{e['replicate']}.jsonl.gz", str(tmp), e["scenario_id"],
                 scenarios[e["scenario_id"]]["family_id"], e["split"], int(e["replicate"]), i)
                for i, e in enumerate(sorted(runs, key=lambda e: e["run_key"]))]
        workers = workers if workers is not None else max(1, min(len(jobs), (os.cpu_count() or 2) - 1))
        if workers <= 1 or len(jobs) <= 1:
            parts = [_v2_part(j) for j in jobs]
        else:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                parts = list(pool.map(_v2_part, jobs, chunksize=1))
        if not parts:
            raise RuntimeError("the simulation manifest lists no replicate to load")

        roster, run_rows, dis_rows, scen_rows = [], [], [], []
        simulated = sorted({e["scenario_id"] for e in runs})
        for sid in simulated:
            sc = scenarios[sid]
            folder = sim_dir / split_of_scenario[sid] / sid
            roster += roster_rows(sid, json.loads((folder / "roster.json").read_text(encoding="utf-8")))
            flat = _flatten({k: v for k, v in sc.items() if k not in ("brief", "narrative", "realism_notes")})
            scen_rows.append({
                "scenario_id": sid, "family_id": sc["family_id"], "split": split_of_scenario[sid],
                "scenario_index": int(sc["scenario_index"]), "archetype": sc.get("archetype"),
                "stratum": sc.get("stratum") or (sc.get("brief") or {}).get("stratum"),
                "size_band": sc.get("size_band"), "transport_mix": sc.get("transport_mix"),
                "staffing": sc.get("staffing"), "failure_focus": sc.get("failure_focus"),
                "edge_tags": list(sc.get("edge_tags") or []),
                "specimens_per_weekday": float(sc["volume"]["specimens_per_weekday"]),
                "parameters": json.dumps(flat, sort_keys=True, default=str)})
        for e in sorted(runs, key=lambda e: e["run_key"]):
            day0 = V2_DAY0
            run_rows.append({"run": int(e["run_key"]), "scenario_id": e["scenario_id"], "replicate": int(e["replicate"]),
                             "role": "history" if e["replicate"] == HISTORY_REPLICATE else "scored", "day0": day0,
                             "scored_start": day0, "scored_end": day0 + timedelta(days=V2_SCORED_DAYS),
                             "split": e["split"], "family_id": scenarios[e["scenario_id"]]["family_id"]})
            folder = sim_dir / e["split"] / e["scenario_id"]
            for w in json.loads((folder / f"disruptions-rep-{e['replicate']}.json").read_text(encoding="utf-8")):
                dis_rows.append({"scenario_id": e["scenario_id"], "replicate": int(e["replicate"]),
                                 "run": int(e["run_key"]), "disruption_id": w["disruption_id"], "type": w["type"],
                                 "target_id": w["target_id"], "day": w["day"], "start": ts(w["start"]),
                                 "end": ts(w["end"]), "start_min": float(w["start_min"]), "end_min": float(w["end_min"]),
                                 "severity": float(w["severity"]), "response": w["response"],
                                 "noticed_at": ts(w["noticed_at"]), "source": w["source"]})
        dis_schema = {"scenario_id": pl.Utf8, "replicate": pl.Int64, "run": pl.Int64, "disruption_id": pl.Utf8,
                      "type": pl.Utf8, "target_id": pl.Utf8, "day": pl.Int64, "start": pl.Datetime, "end": pl.Datetime,
                      "start_min": pl.Float64, "end_min": pl.Float64, "severity": pl.Float64, "response": pl.Utf8,
                      "noticed_at": pl.Datetime, "source": pl.Utf8}
        hashes = {"policy_hash": loaded["policy_hash"], "library_sha256": library_sha,
                  "sim_manifest_sha256": _sha256_bytes(manifest_raw),
                  "splits_sha256": _sha256_bytes(json.dumps(split_of_scenario, sort_keys=True).encode("utf-8"))}
        meta = pl.DataFrame({"key": list(hashes), "value": list(hashes.values())})
        _write_tables(store_dir / OBSERVED_NAME, tmp,
                      {"specimens": "specimen_id", "events": "specimen_id, seq"},
                      {"roster": pl.DataFrame(roster, schema={"scenario_id": pl.Utf8, "kind": pl.Utf8, "key": pl.Utf8,
                                                              "attributes": pl.Utf8}),
                       "runs": pl.DataFrame(run_rows), "meta": meta})
        _write_tables(store_dir / TRUTH_NAME, tmp,
                      {"truth_specimens": "specimen_id", "truth_segments": "specimen_id, seq"},
                      {"truth_disruptions": pl.DataFrame(dis_rows, schema=dis_schema),
                       "truth_scenarios": pl.DataFrame(scen_rows), "meta": meta})
    counts = {name: sum(p[name] for p in parts) for name in parts[0]}
    counts.update(roster=len(roster), runs=len(run_rows), truth_disruptions=len(dis_rows),
                  truth_scenarios=len(scen_rows))
    out = {"inputs": hashes, "counts": counts,
           "outputs": {name: _sha256_bytes((store_dir / name).read_bytes()) for name in (OBSERVED_NAME, TRUTH_NAME)}}
    (store_dir / STORE_V2_MANIFEST).write_text(json.dumps(out, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    return counts


SIM_DIR = ROOT / "data" / "sim"
LIBRARY_V2 = ROOT / "datasets" / "scenarios-v2.jsonl.gz"
SPLITS_V2 = ROOT / "datasets" / "scenarios-v2.splits.json"
SIM_A1_DIR = ROOT / "data" / "sim-a1"            # ablation A1's simulation (src/scenarios/freeze.py simulate_a1)
STORE_V2_A1_DIR = ROOT / "data" / "store-v2-a1"  # and its store, which A1's arms train on
CONTROL_SIM_DIR = ROOT / "data" / "sim-control"    # the v1-world scenario's replicates (spec §8.1 control_v1world)
CONTROL_STORE_DIR = ROOT / "data" / "store-v2-control"  # and its own store, the negative control's set


def build_control_v2(sim_dir: Path = CONTROL_SIM_DIR, store_dir: Path = CONTROL_STORE_DIR, *, workers: int = 1,
                     replicates: int | None = None) -> dict:
    """The control_v1world set (spec §8.1, §8.8): the v1-world scenario (src/sim/v1world.py) simulated for its
    replicates (v1world.REPLICATES; run keys 190000 on) into its own folder and store. A store's splits are the v2
    ones, so the scenario sits in val there; the evaluation finds it by its id (neural_eval.CONTROL_SCENARIO) and
    refuses it only in a locked split. Returns the store's counts."""
    from src.sim import run as sim_run, v1world

    sim_dir = Path(sim_dir)
    sim_dir.mkdir(parents=True, exist_ok=True)
    scenario = v1world.scenario() | {"split": "val"}
    raw = library_bytes([scenario])
    (sim_dir / "library.jsonl.gz").write_bytes(raw)
    reps = list(range(v1world.REPLICATES if replicates is None else int(replicates)))
    sim_run.simulate_library([scenario], sim_dir, {"val": reps}, workers, library_sha256=_sha256_bytes(raw))
    return build_v2(sim_dir, sim_dir / "library.jsonl.gz", None, Path(store_dir), workers=workers)


def command_build_v2(*, workers: int | None = None, sim_dir: Path = SIM_DIR, store_dir: Path = STORE_V2_DIR,
                     out=print) -> int:
    """`store`: the frozen library's simulation (data/sim/) into data/store-v2/, with its splits. `store --a1`:
    ablation A1's simulation (data/sim-a1/) into data/store-v2-a1/, with its own manifest."""
    counts = build_v2(sim_dir, LIBRARY_V2, SPLITS_V2, store_dir, workers=workers)
    out(json.dumps(counts, indent=1, sort_keys=True, default=str))
    return 0


def open_observed_v2(store_dir: Path = STORE_V2_DIR) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(Path(store_dir) / OBSERVED_NAME), read_only=True)


def store_v2_manifest(store_dir: Path = STORE_V2_DIR) -> dict:
    return json.loads((Path(store_dir) / STORE_V2_MANIFEST).read_text(encoding="utf-8"))


def sha256_path(path: Path, block: int = 1 << 24) -> str:
    """The SHA-256 of a file, read in blocks so a large store need not fit in memory."""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_files(folder: Path, expected: dict, what: str) -> None:
    """Refuse unless every file a manifest lists is in `folder` with the SHA-256 the manifest records."""
    if not expected:
        raise RuntimeError(f"the {what} manifest in {folder} records no file hashes, so its files cannot be checked")
    for name, digest in sorted(expected.items()):
        path = Path(folder) / name
        if not path.is_file():
            raise RuntimeError(f"{name} is listed in the {what} manifest but missing from {folder}")
        if sha256_path(path) != digest:
            raise RuntimeError(f"{name} in {folder} does not match the SHA-256 in the {what} manifest")


def verified_store_v2_manifest(store_dir: Path = STORE_V2_DIR) -> dict:
    """The store's manifest, once both database files are checked against the SHA-256 it records, so the
    manifest can stand for the store's content."""
    manifest = store_v2_manifest(store_dir)
    outputs = manifest.get("outputs") or {}
    missing = sorted({OBSERVED_NAME, TRUTH_NAME} - set(outputs))
    if missing:
        raise RuntimeError(f"the store manifest in {store_dir} records no SHA-256 for {missing}; rebuild the store")
    verify_files(Path(store_dir), outputs, "store")
    return manifest


def table_digests(store_dir: Path = STORE_V2_DIR) -> dict:
    """{database file: {table: {rows, columns, sum, xor}}} for every table of a v2 store's two files: the row count,
    the column names and types, and the sum and XOR of DuckDB's hash of each whole row. Neither aggregate depends on
    row order or on how DuckDB laid the file out, so two stores loaded from the same simulation agree here even when
    their files' bytes differ (they do across DuckDB builds). The hashes are DuckDB's own: compare digests made by
    one DuckDB version. Opens both files read-only and computes no statistic of any column."""
    out = {}
    for name in (OBSERVED_NAME, TRUTH_NAME):
        with duckdb.connect(str(Path(store_dir) / name), read_only=True) as con:
            tables = [t for (t,) in con.execute("select table_name from information_schema.tables where "
                                                "table_schema = 'main' order by table_name").fetchall()]
            out[name] = {}
            for t in tables:
                columns = [[c[0], c[1]] for c in con.execute(f'describe "{t}"').fetchall()]
                n, total, xor = con.execute(f'select count(*), sum(hash(r)::hugeint), bit_xor(hash(r)) from "{t}" r'
                                            ).fetchone()
                out[name][t] = {"rows": int(n), "columns": columns, "sum": str(total or 0), "xor": str(xor or 0)}
    return out


def main() -> int:
    counts = build()
    print(f"wrote {STORE_DIR}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        sys.exit(f"error: {e}")
