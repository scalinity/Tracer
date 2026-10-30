"""Collect finished batches, validate every trail, retry failures once, and freeze the dataset.

    python -m src.generator.collect --batch-ids data/claude/batches.json            # gather what has ended
    python -m src.generator.collect --batch-ids data/claude/batches.json --wait     # poll until all have
    python -m src.generator.collect --batch-ids data/claude/batches.json --freeze   # then write datasets/

Each batch result is read, logged to data/claude/usage.jsonl at the batch price, and run through the
validator. A trail that fails, or a request that errored or expired, gets one synchronous retry with
the error text added to the brief; a second failure drops the specimen into <out>/run-NN.dropped.jsonl.
The briefs are regenerated from the ledger's seed, and the ledger's prompt, policy and config hashes
must match the repository, so a trail is only ever paired with the brief it was written for.

Accepted specimens go to <out>/run-NN.jsonl as {"brief", "trail"}. A rerun skips what is already
accepted or dropped. --freeze writes datasets/tracer-synthetic-v1.jsonl.gz and datasets/manifest.json.
The frozen file holds every brief's label beside its trail: the truth side is read only by the
evaluation harness.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import briefs as briefs_mod
from . import config, prompt, run, validate

OUT_DIR = prompt.ROOT / "data" / "claude" / "full"
DATASET_PATH = prompt.ROOT / "datasets" / "tracer-synthetic-v1.jsonl.gz"
MANIFEST_PATH = prompt.ROOT / "datasets" / "manifest.json"
POLL_SECONDS = 60
MIN_ACCEPTED = 11000  # plan task D9
LARGE_FILE_BYTES = 25 * 1024 * 1024  # AGENTS.md: larger goes up as a release asset
CLINICAL_REVIEW = "not done: plan task K2, Karissa's review of twenty pilot timelines, was skipped"


def dropped_path(out: Path, r: int) -> Path:
    return out / f"run-{r:02d}.dropped.jsonl"


def dropped_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(line)["specimen_id"] for line in path.read_text(encoding="utf-8").splitlines() if line}


def check_ledger(ledger: dict, loaded: dict) -> None:
    """The repository must still produce the briefs and prompt the batches were made from."""
    now = run.batch_context(ledger["context"]["job"], ledger["context"]["seed"], ledger["context"]["n_per_run"], loaded)
    differs = sorted(k for k in now if ledger["context"].get(k) != now[k])
    if differs:
        raise RuntimeError(f"the repository no longer matches the batches: {', '.join(differs)} changed")


def collect_run(client, briefs: list[dict], batch_id: str, out: Path, r: int, *, job: str,
                workers: int = run.DEFAULT_WORKERS, log_path: Path = prompt.USAGE_LOG) -> dict:
    """Read one ended batch. Returns counts for this call."""
    path, drops = run.run_path(out, r), dropped_path(out, r)
    done = run.saved_ids(path) | dropped_ids(drops)
    by_id = {b["specimen_id"]: b for b in briefs}
    ok: list[dict] = []
    retry: list[tuple[dict, str | None]] = []
    seen: set[str] = set()
    for result in client.messages.batches.results(batch_id):
        sid = result.custom_id
        if sid not in by_id:
            raise RuntimeError(f"batch {batch_id} returned {sid}, which is not one of run {r:02d}'s briefs")
        seen.add(sid)
        if sid in done:
            continue
        brief = by_id[sid]
        if result.result.type != "succeeded":
            retry.append((brief, None))
            continue
        message = result.result.message
        prompt.log_usage(prompt.usage_record(message, job=job, specimen_id=sid, batch=True), log_path)
        try:
            trail = prompt.read_trail(message, sid)
            failures = validate.validate(brief, trail)
            error = validate.error_text(failures) if failures else None
        except prompt.TrailError as e:
            trail, error = None, f"check 1 (schema): {e}"
        if error is None:
            ok.append({"brief": brief, "trail": trail})
        else:
            retry.append((brief, error))
    retry += [(by_id[sid], None) for sid in by_id if sid not in seen and sid not in done]

    counts = {"from_batch": len(ok), "retried": len(retry), "accepted": 0, "dropped": 0}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for rec in ok:
            f.write(json.dumps(rec) + "\n")
            counts["accepted"] += 1
        with ThreadPoolExecutor(workers) as pool:
            tries = pool.map(lambda item: validate.attempt(client, item[0], job=job, log_path=log_path, error=item[1]),
                             retry)
            for (brief, _), (trail, error) in zip(retry, tries):
                if trail is None:
                    validate.drop(brief, error, job=job, path=drops)
                    counts["dropped"] += 1
                else:
                    f.write(json.dumps({"brief": brief, "trail": trail}) + "\n")
                    counts["accepted"] += 1
    return counts


def read_records(out: Path, runs: int) -> list[dict]:
    records = []
    for r in range(runs):
        path = run.run_path(out, r)
        if path.exists():
            records += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return sorted(records, key=lambda rec: rec["brief"]["specimen_id"])


def write_dataset(records: list[dict], path: Path) -> str:
    """Deterministic gzip of one sorted-key JSON line per specimen. Returns the file's SHA-256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        for rec in records:
            gz.write((json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(ledger: dict, requested: list[dict], records: list[dict], dropped: int, dataset: Path,
                   sha256: str, log_path: Path) -> dict:
    ctx = ledger["context"]
    rows = [r for r in map(json.loads, filter(None, log_path.read_text(encoding="utf-8").splitlines()))
            if r["job"] == ctx["job"]] if log_path.exists() else []
    calls: dict[str, list[dict]] = {}
    for r in rows:
        calls.setdefault(r["specimen_id"], []).append(r)
    accepted_ids = {rec["brief"]["specimen_id"] for rec in records}
    first_pass = sum(1 for sid in accepted_ids if len(calls.get(sid, [])) == 1 and calls[sid][0]["batch"])
    req_fault = Counter(b["planted_fault"] for b in requested)
    acc_fault = Counter(rec["brief"]["planted_fault"] for rec in records)
    return {
        "dataset": dataset.name,
        "format": "gzip, one JSON object per line: {brief, trail}; brief.label and trail.truth_segments are the truth side",
        "sha256": sha256,
        "bytes": dataset.stat().st_size,
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "model": ctx["model"],
        "prompt_hash": ctx["prompt_hash"],
        "policy_hash": ctx["policy_hash"],
        "config_hash": ctx["config_hash"],
        "seed": ctx["seed"],
        "runs": len(ledger["batches"]),
        "n_per_run": ctx["n_per_run"],
        "requested": len(requested),
        "accepted": len(records),
        "dropped": dropped,
        "accepted_rate": round(len(records) / len(requested), 4),
        "first_attempt_rate": round(first_pass / len(requested), 4),
        "cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
        "requested_by_fault": dict(sorted(req_fault.items())),
        "accepted_by_fault": dict(sorted(acc_fault.items())),
        "breach_accepted": sum(rec["brief"]["label"]["breach"] for rec in records),
        "clinical_review": CLINICAL_REVIEW,
    }


def wait_for(client, ledger: dict) -> dict[int, object]:
    while True:
        batches = {int(r): client.messages.batches.retrieve(b["id"]) for r, b in ledger["batches"].items()}
        pending = sum(1 for b in batches.values() if b.processing_status != "ended")
        print(f"{len(batches) - pending} of {len(batches)} batches ended")
        if not pending:
            return batches
        time.sleep(POLL_SECONDS)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Collect finished batches and freeze the dataset.")
    ap.add_argument("--batch-ids", type=Path, default=run.BATCHES_PATH)
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--wait", action="store_true", help="poll until every batch has ended")
    ap.add_argument("--freeze", action="store_true", help="write the dataset and manifest once every run is done")
    ap.add_argument("--workers", type=int, default=run.DEFAULT_WORKERS)
    args = ap.parse_args(argv)

    ledger = json.loads(args.batch_ids.read_text(encoding="utf-8"))
    loaded = config.load()
    check_ledger(ledger, loaded)
    ctx, client = ledger["context"], prompt.make_client()
    batches = wait_for(client, ledger) if args.wait else \
        {int(r): client.messages.batches.retrieve(b["id"]) for r, b in ledger["batches"].items()}

    requested = []
    for r in sorted(batches):
        briefs = briefs_mod.make_briefs(ctx["seed"], r, ctx["n_per_run"], loaded)
        requested += briefs
        batch = batches[r]
        if batch.processing_status != "ended":
            counts = batch.request_counts
            print(f"run {r:02d}: {batch.processing_status}, {counts.processing} still processing")
            continue
        c = collect_run(client, briefs, ledger["batches"][str(r)]["id"], args.out, r, job=ctx["job"],
                        workers=args.workers, log_path=prompt.USAGE_LOG)
        print(f"run {r:02d}: {c['from_batch']} accepted from the batch, {c['retried']} retried, "
              f"{c['accepted'] - c['from_batch']} rescued, {c['dropped']} dropped")

    records = read_records(args.out, len(batches))
    dropped = sum(len(dropped_ids(dropped_path(args.out, r))) for r in batches)
    print(f"{len(records)} accepted, {dropped} dropped, of {len(requested)}")
    if not args.freeze:
        return 0
    if len(records) + dropped < len(requested):
        raise RuntimeError("not every run has been collected; nothing frozen")
    sha = write_dataset(records, DATASET_PATH)
    manifest = build_manifest(ledger, requested, records, dropped, DATASET_PATH, sha, prompt.USAGE_LOG)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"froze {DATASET_PATH.name}: {manifest['accepted']} specimens, {manifest['bytes']} bytes, "
          f"cost ${manifest['cost_usd']}; manifest at {MANIFEST_PATH}")
    if manifest["accepted"] < MIN_ACCEPTED:
        print(f"warning: fewer than {MIN_ACCEPTED} accepted (plan task D9)")
    if manifest["bytes"] > LARGE_FILE_BYTES:
        print("warning: over 25 MB; publish as a release asset and commit only the manifest and checksum")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        sys.exit(f"error: {e}")
