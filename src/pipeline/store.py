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
from datetime import datetime
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


def main() -> int:
    counts = build()
    print(f"wrote {STORE_DIR}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        sys.exit(f"error: {e}")
