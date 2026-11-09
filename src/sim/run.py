"""Simulate a scenario library to files (spec §5.7, §6.3).

    python -m src.sim.run --example                  # the worked example and the v1 world, to data/sim-example
    python -m src.sim.run --example --out /tmp/sim   # the same, to a folder of your choice

simulate_library(scenarios, out_dir, replicates_by_split, workers) runs every replicate of every
scenario in a process pool and writes, under out_dir (data/sim by default):

    <split>/<scenario_id>/rep-<r>.jsonl.gz           one sorted-key JSON line per record, gzip with mtime=0
    <split>/<scenario_id>/roster.json                what the laboratory knows about itself
    <split>/<scenario_id>/disruptions-rep-<r>.json   the realized disruption windows (truth side)
    manifest.json                                    every file's SHA-256 and counts

Each scenario dict carries scenario_id, family_id, split and scenario_index beside its
configuration; replicates_by_split maps a split to its replicate numbers (a list, or a count).
Every replicate is checked by src/sim/validate_v2.py before it is written. One that fails is
dropped and listed in the manifest with its first failures (spec §13: above 1% of replicates,
investigate). Each run's entry counts its specimens cut at the 7,200-minute horizon, which is where
a runaway queue shows. Same inputs, same bytes: the manifest holds no times.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from src.generator import config

from . import engine, records, rng, v1world, validate_v2

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "data" / "sim"
EXAMPLE_OUT = ROOT / "data" / "sim-example"  # ignored, like all of data/; rewritten on every --example run
EXAMPLE_PATH = ROOT / "config" / "scenario.example.json"
EXAMPLE_INDEX = 0
EXAMPLE_REPLICATE = 1
MAX_LOGGED_FAILURES = 5

_LOADED: dict | None = None


def _loaded() -> dict:
    """config.load() once per worker process."""
    global _LOADED
    if _LOADED is None:
        _LOADED = config.load()
    return _LOADED


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_records(recs: list[dict], path: Path) -> str:
    """Deterministic gzip of one sorted-key JSON line per record (as src/generator/collect.py does)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        for rec in recs:
            gz.write((json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    return sha256(path)


def write_json(obj, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    return sha256(path)


def replicates_of(spec) -> list[int]:
    return list(range(spec)) if isinstance(spec, int) else [int(r) for r in spec]


def folder_of(out_dir: Path, scenario: dict) -> Path:
    return out_dir / scenario["split"] / scenario["scenario_id"]


def counts(result: engine.SimResult) -> dict:
    s = result.stats
    return {"specimens": s["specimens"], "scored": s["scored"], "traffic": s["traffic"],
            "scored_breaches": s["scored_breaches"], "outcome_classes": s["outcome_classes"],
            "horizon_cut": s["horizon_cut"]}


def simulate_one(job: tuple[dict, int, Path]) -> dict:
    """Simulate, check and write one replicate. Returns its manifest entry and its timing."""
    scenario, replicate, out_dir = job
    started = time.perf_counter()
    result = engine.simulate(scenario, scenario_index=scenario["scenario_index"], replicate=replicate,
                             loaded=_loaded())
    failures = validate_v2.validate(result)
    entry = {"scenario_id": scenario["scenario_id"], "split": scenario["split"], "replicate": replicate,
             "run_key": records.run_key(scenario["scenario_index"], replicate), **counts(result)}
    timing = {"simulate_seconds": result.stats["wall_seconds"]}
    if failures:
        entry["dropped"] = failures[:MAX_LOGGED_FAILURES] + ([f"{len(failures)} failures in all"]
                                                              if len(failures) > MAX_LOGGED_FAILURES else [])
        return {"entry": entry, "files": [], "timing": timing}
    folder = folder_of(out_dir, scenario)
    rec_path = folder / f"rep-{replicate}.jsonl.gz"
    dis_path = folder / f"disruptions-rep-{replicate}.json"
    files = [{"path": rec_path.relative_to(out_dir).as_posix(), "sha256": write_records(result.records, rec_path),
              "bytes": rec_path.stat().st_size, "records": len(result.records)},
             {"path": dis_path.relative_to(out_dir).as_posix(), "sha256": write_json(result.disruptions, dis_path),
              "bytes": dis_path.stat().st_size, "windows": len(result.disruptions)}]
    timing["total_seconds"] = round(time.perf_counter() - started, 4)
    return {"entry": entry, "files": files, "timing": timing}


def simulate_library(scenarios: list[dict], out_dir: Path, replicates_by_split: dict, workers: int, *,
                     library_sha256: str | None = None) -> dict:
    """Simulate every replicate of every scenario; write the files and data/sim/manifest.json.

    Returns the manifest, plus a `timing` entry (kept out of the file, which must not change between runs).
    """
    out_dir = Path(out_dir)
    jobs = [(sc, r, out_dir) for sc in scenarios for r in replicates_of(replicates_by_split[sc["split"]])]
    started = time.perf_counter()
    if workers <= 1:
        results = [simulate_one(job) for job in jobs]
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(simulate_one, jobs, chunksize=1))
    files = []
    for sc in scenarios:
        path = folder_of(out_dir, sc) / "roster.json"
        files.append({"path": path.relative_to(out_dir).as_posix(), "sha256": write_json(records.roster(sc), path),
                      "bytes": path.stat().st_size})
    for res in results:
        files += res["files"]
    entries = [res["entry"] for res in results]
    kept = [e for e in entries if "dropped" not in e]
    loaded = _loaded()
    manifest = {
        "format": "gzip, one sorted-key JSON object per line: {brief, trail}; brief.label, brief.v2 and "
                  "trail.truth_segments are the truth side, as are the disruptions files",
        "master_seed": rng.master_seed(),
        "policy_hash": loaded["policy_hash"],
        "config_hash": loaded["config_hash"],
        "library_sha256": library_sha256,
        "scenarios": len(scenarios),
        "replicates": len(entries),
        "replicates_written": len(kept),
        "replicates_dropped": len(entries) - len(kept),
        "specimens": sum(e["specimens"] for e in kept),
        "scored": sum(e["scored"] for e in kept),
        "scored_breaches": sum(e["scored_breaches"] for e in kept),
        "runs": sorted(entries, key=lambda e: (e["split"], e["scenario_id"], e["replicate"])),
        "files": sorted(files, key=lambda f: f["path"]),
        "clinical_review": "none",
        "note": "Every parameter is an assumption for a synthetic simulator; nothing is real data.",
    }
    write_json(manifest, out_dir / "manifest.json")
    timing = {"wall_seconds": round(time.perf_counter() - started, 3),
              "simulate_seconds": round(sum(r["timing"]["simulate_seconds"] for r in results), 3)}
    return {**manifest, "timing": timing, "results": results}


def example_scenarios(loaded: dict | None = None) -> list[dict]:
    example = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    example.update({"scenario_id": "example", "family_id": "example", "split": "example",
                    "scenario_index": EXAMPLE_INDEX})
    return [example, v1world.scenario(loaded)]


def _median_by_route(out_dir: Path, path: str) -> dict:
    by: dict[str, list[float]] = {}
    with gzip.open(out_dir / path, "rt", encoding="utf-8") as f:
        for line in f:
            b = json.loads(line)["brief"]
            if b["v2"]["scored"] and b["targets"]["receipt_after_min"] is not None:
                by.setdefault(b["v2"]["route_kind"], []).append(b["targets"]["receipt_after_min"])
    return {k: round(statistics.median(v), 1) for k, v in sorted(by.items())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Simulate scenarios with the v2 laboratory simulator.")
    ap.add_argument("--example", action="store_true", help="simulate the worked example and the v1 world")
    ap.add_argument("--out", type=Path, default=EXAMPLE_OUT, help="output folder (default: data/sim-example)")
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args(argv)
    if not args.example:
        ap.error("only --example is available from the command line; the library runner calls simulate_library")
    out = args.out
    manifest = simulate_library(example_scenarios(_loaded()), out,
                                {"example": [EXAMPLE_REPLICATE], "control": [0]}, args.workers)
    print(f"wrote {out}")
    for res in manifest["results"]:
        e, t = res["entry"], res["timing"]
        share = e["scored_breaches"] / e["scored"] if e["scored"] else float("nan")
        per_1000 = t["simulate_seconds"] / e["specimens"] * 1000
        print(f"{e['scenario_id']} replicate {e['replicate']}: {e['specimens']} specimens ({e['scored']} scored, "
              f"{e['traffic']} traffic), breach share {share:.2%} ({e['scored_breaches']} of {e['scored']}), "
              f"{per_1000:.3f} s per 1,000 specimens" + ("; DROPPED: " + "; ".join(e["dropped"]) if "dropped" in e else ""))
        if res["files"]:
            print(f"  median collection-to-receipt by route (min): {_median_by_route(out, res['files'][0]['path'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
