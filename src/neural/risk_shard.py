"""One shard of an unlocked set's TracerNet risk table, for choosing a model on validation
(docs/neural/preregistration-v2.md §7).

    python -m src.neural.risk_shard --split val --artifact DIR --store STORE --out DIR --parts 0:6 --workers 31

Scenarios are taken in sorted order, 50 to a part, every run of each (the history replicate and the scored ones), and
each part is scored by `Predictor.score` on CPU exactly as `evaluate --artifact` scores a set, then written as
`part-NNNN.parquet` with the columns the evaluation reads. Several shards side by side write one table, which
`evaluate --split val --tracernet "DIR/*.parquet"` reads. A locked set (test, test_ood, test_fresh) is refused: it is
scored only through `evaluate` under its lock.

The output folder records what made its parts (`inputs.json`: the split, the artifact's manifest and calibration
SHA-256 and the store's manifest SHA-256). A shard writes into a folder only when that record matches its own, so a
restarted or parallel shard resumes the same table and never mixes another model's or store's parts into it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from src.pipeline import neural_eval as ne, store

PER_PART = 50
KEEP = ("specimen_id", "run", "scenario_id", "replicate", "tick_min", "p_miss", "harm_default", "harm_equal",
        "ensemble_sd")


def scenario_runs(store_dir: Path, split: str) -> dict[str, list[int]]:
    con = store.open_observed_v2(store_dir)
    try:
        rows = con.execute("select scenario_id, run from runs where split = ? order by scenario_id, run",
                           [split]).fetchall()
    finally:
        con.close()
    out: dict[str, list[int]] = {}
    for sid, run in rows:
        out.setdefault(sid, []).append(int(run))
    return out


def inputs_of(split: str, artifact: Path, store_dir: Path) -> dict:
    """The identity of what a shard scores with: the split, the artifact and the store."""
    sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()  # noqa: E731
    manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
    return {"split": split, "artifact_manifest_sha256": sha(artifact / "manifest.json"),
            "calibration_sha256": (manifest.get("files") or {}).get("calibration.json"),
            "store_manifest_sha256": sha(store_dir / store.STORE_V2_MANIFEST)}


def claim(out_dir: Path, inputs: dict) -> None:
    """Record the inputs in an empty folder, or check them against the record already there (refusing a mismatch, or
    parts with no record). The record is written whole to a private file and then linked into place, which fails if a
    record exists, so a reader never sees a partial record and two shards starting together agree on one."""
    out_dir.mkdir(parents=True, exist_ok=True)
    record = out_dir / "inputs.json"
    if not record.exists() and any(out_dir.glob("part-*.parquet")):
        raise SystemExit(f"{out_dir} holds parts with no record of their inputs; score into a new folder")
    tmp = out_dir / f".inputs-{os.getpid()}-{time.monotonic_ns()}.json"
    tmp.write_text(json.dumps(inputs, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.link(tmp, record)
    except FileExistsError:
        have = json.loads(record.read_text(encoding="utf-8"))
        if have != inputs:
            changed = sorted(k for k in set(have) | set(inputs) if have.get(k) != inputs.get(k))
            raise SystemExit(f"{out_dir} holds parts made from other inputs ({', '.join(changed)}); "
                             "score into a new folder") from None
    finally:
        tmp.unlink()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--split", required=True)
    ap.add_argument("--artifact", type=Path, required=True)
    ap.add_argument("--store", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--parts", default="0:999999", help="LO:HI, the parts [LO, HI) this shard writes")
    ap.add_argument("--workers", type=int, default=None)
    args = ap.parse_args(argv)
    if args.split in ne.LOCKED_SPLITS:
        print(f"{args.split} is locked: score it with `evaluate` under the lock", file=sys.stderr)
        return 2
    from src.models.tracernet.predictor import Predictor
    lo, hi = (int(x) for x in args.parts.split(":"))
    by_scenario = scenario_runs(args.store, args.split)
    sids = sorted(by_scenario)
    parts = (len(sids) + PER_PART - 1) // PER_PART
    print(f"{args.split}: {len(sids)} scenarios, {parts} parts; this shard writes parts {lo} to {min(hi, parts) - 1}",
          flush=True)
    claim(args.out, inputs_of(args.split, args.artifact, args.store))
    predictor = Predictor.load(args.artifact, device="cpu")
    t0 = time.monotonic()
    for k in range(lo, min(hi, parts)):
        target = args.out / f"part-{k:04d}.parquet"
        if target.exists():
            print(f"part {k} exists", flush=True)
            continue
        part_runs = [r for sid in sids[k * PER_PART:(k + 1) * PER_PART] for r in by_scenario[sid]]
        frame = predictor.score(args.store, part_runs, workers=args.workers)
        tmp = target.with_suffix(".tmp")
        frame.select(KEEP).write_parquet(tmp)
        tmp.rename(target)
        print(f"part {k} written ({time.monotonic() - t0:.0f} s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
