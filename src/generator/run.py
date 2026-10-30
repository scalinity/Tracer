"""Write trails for seeded briefs and keep the ones the validator accepts.

    python -m src.generator.run --seed 0 --runs 1 --n 200 --out data/claude/pilot --dry-run
    python -m src.generator.run --seed 0 --runs 1 --n 200 --out data/claude/pilot --budget-usd 0.25

A dry run counts tokens, prints the estimate and stops. A real run needs --budget-usd and aborts
before sending anything if the estimate is higher. With --batch each run goes to the Batch API as
one batch at half price; the batch ids are saved to data/claude/batches.json, nothing is awaited,
and src.generator.collect gathers the results. A rerun submits only the runs not yet in that file. Each accepted specimen is appended to
<out>/run-NN.jsonl as {"brief", "trail"}, so a rerun skips what is already there. Dropped
specimens go to data/claude/dropped.jsonl and every call to data/claude/usage.jsonl. The summary
(counts, pass rates, real cost, hashes) is printed and saved to <out>/summary.json.

The saved file holds each brief's label next to its trail: it is the truth side of the data and
is read only by the evaluation harness (AGENTS.md).
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import briefs as briefs_mod
from . import config, prompt, validate

DEFAULT_WORKERS = 8
BATCHES_PATH = prompt.ROOT / "data" / "claude" / "batches.json"
CACHE_TTL_BATCH = "1h"  # a batch may wait up to an hour before it starts


def run_path(out: Path, run: int) -> Path:
    return out / f"run-{run:02d}.jsonl"


def saved_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(line)["brief"]["specimen_id"] for line in path.read_text(encoding="utf-8").splitlines() if line}


def generate_run(client, briefs: list[dict], path: Path, *, job: str, workers: int = DEFAULT_WORKERS,
                 log_path: Path = prompt.USAGE_LOG, dropped_path: Path = validate.DROPPED_LOG) -> dict:
    """Generate and save the briefs not already in `path`. Returns counts for this call."""
    done = saved_ids(path)
    todo = [b for b in briefs if b["specimen_id"] not in done]
    counts = {"requested": len(todo), "skipped": len(briefs) - len(todo), "accepted": 0, "dropped": 0}
    path.parent.mkdir(parents=True, exist_ok=True)

    def one(brief):
        return validate.generate_valid(client, brief, job=job, log_path=log_path, dropped_path=dropped_path)

    with path.open("a", encoding="utf-8") as f, ThreadPoolExecutor(workers) as pool:
        for brief, trail in zip(todo, pool.map(one, todo)):
            if trail is None:
                counts["dropped"] += 1
                continue
            f.write(json.dumps({"brief": brief, "trail": trail}) + "\n")
            f.flush()
            counts["accepted"] += 1
    return counts


def batch_context(job: str, seed: int, n: int, loaded: dict) -> dict:
    """What a batch ledger must agree on: change any of it and the briefs or trails would differ."""
    return {"job": job, "seed": seed, "n_per_run": n, "model": prompt.MODEL, "prompt_hash": prompt.prompt_hash(),
            "policy_hash": loaded["policy_hash"], "config_hash": loaded["config_hash"]}


def read_ledger(path: Path, context: dict) -> dict:
    """The saved ledger, or a new one. Fails if the saved one was made with different settings."""
    if not path.exists():
        return {"context": context, "batches": {}}
    ledger = json.loads(path.read_text(encoding="utf-8"))
    differs = sorted(k for k in context if ledger["context"].get(k) != context[k])
    if differs:
        raise RuntimeError(f"{path} was made with different {', '.join(differs)}; move it aside to start again")
    return ledger


def write_ledger(path: Path, ledger: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def submit_batches(client, runs: list[list[dict]], path: Path, context: dict) -> dict:
    """One batch per run, skipping runs already in the ledger. The ledger is rewritten after each submit."""
    ledger = read_ledger(path, context)
    for r, briefs in enumerate(runs):
        if str(r) in ledger["batches"]:
            print(f"run {r:02d}: already submitted as {ledger['batches'][str(r)]['id']}")
            continue
        batch = client.messages.batches.create(requests=[
            {"custom_id": b["specimen_id"], "params": prompt.build_request(b, cache_ttl=CACHE_TTL_BATCH)}
            for b in briefs])
        ledger["batches"][str(r)] = {"id": batch.id, "specimens": len(briefs)}
        ledger["created"] = datetime.now().astimezone().isoformat(timespec="seconds")
        write_ledger(path, ledger)
        print(f"run {r:02d}: submitted {len(briefs)} specimens as {batch.id}")
    return ledger


def usage_rows(log_path: Path, start: int, job: str) -> list[dict]:
    """The usage rows this invocation appended for `job`; `start` is the log's size before it began."""
    if not log_path.exists():
        return []
    with log_path.open("rb") as f:
        f.seek(start)
        text = f.read().decode("utf-8")
    return [r for r in map(json.loads, filter(None, text.splitlines())) if r["job"] == job]


def summarize(counts: dict, rows: list[dict]) -> dict:
    calls_per_specimen: dict[str, int] = {}
    for r in rows:
        calls_per_specimen[r["specimen_id"]] = calls_per_specimen.get(r["specimen_id"], 0) + 1
    first_pass = sum(1 for n in calls_per_specimen.values() if n == 1)
    finished = counts["accepted"] + counts["dropped"]
    tokens = {k: sum(r[k] for r in rows) for k in ("input_tokens", "cache_creation_input_tokens",
                                                   "cache_read_input_tokens", "output_tokens")}
    return {
        **counts,
        "calls": len(rows),
        "first_pass": first_pass,
        "first_pass_rate": first_pass / counts["requested"] if counts["requested"] else None,
        "accepted_rate": counts["accepted"] / finished if finished else None,
        "cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
        "tokens": tokens,
    }


def format_summary(s: dict) -> str:
    pct = lambda x: "n/a" if x is None else f"{x:.1%}"
    return (f"{s['accepted']} accepted, {s['dropped']} dropped, {s['skipped']} already saved\n"
            f"first-attempt pass {s['first_pass']} of {s['requested']} ({pct(s['first_pass_rate'])}); "
            f"accepted after one retry {pct(s['accepted_rate'])}\n"
            f"{s['calls']} calls, {s['tokens']['cache_read_input_tokens']} cached tokens read, "
            f"real cost ${s['cost_usd']:.4f} (data/claude/usage.jsonl)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write trails for seeded briefs and keep the valid ones.")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--n", type=int, default=200, help="specimens per run")
    ap.add_argument("--out", type=Path, default=prompt.ROOT / "data" / "claude" / "pilot")
    ap.add_argument("--job", default="J1")
    ap.add_argument("--budget-usd", type=float)
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    ap.add_argument("--batch", action="store_true", help="send each run to the Batch API at half price")
    ap.add_argument("--batches", type=Path, default=BATCHES_PATH, help="where --batch saves the batch ids")
    ap.add_argument("--dry-run", action="store_true", help="count tokens, print the estimate and stop")
    args = ap.parse_args(argv)
    if not args.dry_run and args.budget_usd is None:
        ap.error("a real run needs --budget-usd")

    loaded = config.load()
    runs = [briefs_mod.make_briefs(args.seed, r, args.n, loaded) for r in range(args.runs)]
    client = prompt.make_client()
    estimate = prompt.estimate_job(prompt.token_counter(client), runs[0], n=args.n * args.runs, batch=args.batch)
    print(prompt.format_estimate(estimate))
    if args.budget_usd is not None:
        prompt.check_budget(estimate, args.budget_usd)
    if args.dry_run:
        return 0

    if args.batch:
        submit_batches(client, runs, args.batches, batch_context(args.job, args.seed, args.n, loaded))
        print(f"batch ids saved to {args.batches}; collect them with: python -m src.generator.collect "
              f"--batch-ids {args.batches}")
        return 0

    log_start = prompt.USAGE_LOG.stat().st_size if prompt.USAGE_LOG.exists() else 0
    totals = {"requested": 0, "skipped": 0, "accepted": 0, "dropped": 0}
    for r, briefs in enumerate(runs):
        counts = generate_run(client, briefs, run_path(args.out, r), job=args.job, workers=args.workers,
                              log_path=prompt.USAGE_LOG, dropped_path=validate.DROPPED_LOG)
        totals = {k: totals[k] + counts[k] for k in totals}
        print(f"run {r:02d}: {counts['accepted']} accepted, {counts['dropped']} dropped")
    summary = summarize(totals, usage_rows(prompt.USAGE_LOG, log_start, args.job))
    summary |= {"job": args.job, "seed": args.seed, "runs": args.runs, "n_per_run": args.n, "model": prompt.MODEL,
                "prompt_hash": prompt.prompt_hash(), "policy_hash": loaded["policy_hash"],
                "config_hash": loaded["config_hash"], "estimated_cost_usd": round(estimate["cost_usd"], 6)}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(format_summary(summary))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (prompt.BudgetError, RuntimeError) as e:
        sys.exit(f"error: {e}")
