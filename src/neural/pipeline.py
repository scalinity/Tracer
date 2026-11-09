"""The v2 pipeline command line (docs/neural/spec.md §12.1). This module holds the generation commands, up to the dry
run, generate and collect; the later stages join it with their tasks.

    python -m src.neural.pipeline status
    python -m src.neural.pipeline plan     --job O1 [--force]
    python -m src.neural.pipeline dry-run  --job O1 [--tier flex|batch|standard] [--force]
    python -m src.neural.pipeline generate --job O1 --budget-usd 3 [--tier flex|batch|standard]
    python -m src.neural.pipeline collect  --job O1
    python -m src.neural.pipeline ledger report
    python -m src.neural.pipeline ledger release-job O1b --reason TEXT
    python -m src.neural.pipeline weakspots   (src/neural/weakspots.py: round 2's slices and the O3 plan)
    python -m src.neural.pipeline ablate --set core|all | --run ID | --collect   (src/neural/ablations.py)
    python -m src.neural.pipeline report      (src/neural/report.py: results/neural/REPORT.md)

The dry run sends nothing, spends nothing and opens no connection: it builds every request of the job with the real
planner and prompt and prices each one's worst case (what generate reserves: its input bound, every UTF-8 byte sent
plus framing, and max_output_tokens, at Standard rates) and its expected cost (a local estimate, characters / 2.5 plus
15%, at the tier's rates), with the measured median input of stored responses beside it. `generate` refuses unless a
dry run of the same request set and tier exists from the last 24 hours (and not dated ahead), the price table is under
24 hours old and prices every model and tier it may use, `--budget-usd` is no greater than the job's cap, the job is
released, and (O2) the O1 gate passed or (O3) a weak-spot plan exists. It sends only that request set and the repairs
it spawns.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from src.neural import stages
from src.scenarios import ledger as ledger_mod
from src.scenarios import prices, runner
from src.scenarios.client import ClientError, OpenAIClient, api_settings

TIERS = ("flex", "batch", "standard")


def usd(micros: int) -> str:
    return f"${micros / 1e6:,.4f}"


class Pipeline:
    def __init__(self, *, paths: runner.Paths, seams=None, client_factory=None, now=None, out=print,
                 sleep=time.sleep, clock=time.monotonic):
        self.paths = paths
        self._seams = seams
        self.client_factory = client_factory or (lambda: OpenAIClient(env_path=paths.env,
                                                                      settings=api_settings(paths.jobs_config)))
        self.now = now or (lambda: datetime.now().astimezone())
        self.out, self.sleep, self.clock = out, sleep, clock

    # shared ----------------------------------------------------------------------------------------------------------

    @property
    def seams(self) -> runner.Seams:
        if self._seams is None:
            try:
                self._seams = runner.real_seams()
            except ImportError as e:
                raise runner.JobError(f"the scenario planner, prompt, schema and validator (task N1) are not "
                                      f"importable here: {e}") from None
        return self._seams

    def cfg(self) -> dict:
        return runner.load_jobs(self.paths.jobs_config)

    def ledger_exists(self) -> bool:
        return self.paths.ledger.exists() or ledger_mod.mirror_path(self.paths.ledger).exists()

    def open_ledger(self, cfg: dict, *, create: bool = False) -> ledger_mod.Ledger:
        return ledger_mod.Ledger(self.paths.ledger, jobs=cfg, create=create)

    def read_briefs(self, job: str) -> list[dict]:
        path = self.paths.briefs(job)
        if not path.exists():
            raise runner.JobError(f"no briefs for {job}; run `plan --job {job}` or the dry run first")
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    # commands --------------------------------------------------------------------------------------------------------

    def plan(self, job: str, force: bool = False) -> list[dict]:
        cfg = self.cfg()
        j = runner.job_settings(cfg, job)
        targets, n = None, j.get("briefs")
        if j.get("needs") == "weak_spot_plan" and self.paths.weak_spot_plan.exists():
            targets = json.loads(self.paths.weak_spot_plan.read_text(encoding="utf-8"))
            if isinstance(targets, dict):   # the weak-spot record: its targets, and its count when the job sets none
                n = targets["briefs"] if n is None else n
                targets = targets["targets"]
        inputs = {"job": stages.obj_hash({k: j.get(k) for k in ("seed", "briefs", "arms", "needs")}),
                  "planner_config": stages.file_hash(runner.ROOT / "config" / "scenario_planner.yaml"),
                  "planner_code": stages.file_hash(runner.ROOT / "src" / "scenarios" / "planner.py"),
                  "targets": stages.obj_hash({"targets": targets, "briefs": n}) if targets is not None else None}
        path = self.paths.briefs(job)

        def fn():
            briefs = self.seams.plan(job, j["seed"], n=n, targets=targets)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("".join(json.dumps(b, sort_keys=True) + "\n" for b in briefs), encoding="utf-8")
            return {"briefs": path}, briefs

        ran, briefs, reason = stages.run_stage(self.paths.stages, f"plan-{job}", inputs, fn, force=force,
                                               now=self.now())
        if not ran:
            briefs = self.read_briefs(job)
        by_stratum: dict[str, int] = {}
        for b in briefs:
            by_stratum[b["stratum"]] = by_stratum.get(b["stratum"], 0) + 1
        mix = ", ".join(f"{k} {v}" for k, v in sorted(by_stratum.items()))
        self.out(f"plan {job}: {len(briefs)} briefs ({mix}) in {path.relative_to(self.paths.root)} "
                 f"[{'ran: ' + reason if ran else 'up to date'}]")
        return briefs

    def measured_input(self) -> dict:
        """{model: (median billed input tokens, responses)} over every stored response the item store records."""
        if not self.paths.items.exists():
            return {}
        store = runner.ItemStore(self.paths.items, self.paths.items_log)
        try:
            seen: dict[str, list[int]] = {}
            for item in store.items():
                n = (item.get("tokens") or {}).get("input")
                if isinstance(n, int) and not isinstance(n, bool) and n > 0:
                    seen.setdefault(item["model"], []).append(n)
        finally:
            store.close()
        return {m: (round(statistics.median(v)), len(v)) for m, v in seen.items()}

    def dry_run(self, job: str, tier: str | None = None, force: bool = False) -> dict:
        cfg = self.cfg()
        tier = tier or runner.job_settings(cfg, job)["tier"]
        self.plan(job)
        table = prices.load(self.paths.prices)
        runner.check_efforts(cfg, job, table)
        requests = runner.plan_requests(self.seams, cfg, job, self.read_briefs(job), tier)
        set_hash = runner.request_set_hash(job, tier, requests)
        created = not self.ledger_exists()
        with self.open_ledger(cfg, create=created) as lg:
            totals = lg.totals()
            balance = {"account": totals["accounts"][cfg["jobs"][job]["account"]], "job": totals["jobs"][job]}
            inputs = {"briefs": stages.file_hash(self.paths.briefs(job)), "request_set": set_hash,
                      "prices": stages.file_hash(self.paths.prices), "tier": tier,
                      "estimator": "local; worst case from the byte bound at standard rates",
                      "ledger": stages.obj_hash(balance)}
            path = self.paths.dryrun(job)

            def fn():
                d = runner.dry_run(job=job, cfg=cfg, table=table, requests=requests, tier=tier, totals=totals,
                                   now=self.now(), seams=self.seams, measured=self.measured_input(),
                                   ledger_state="created empty by this dry run" if created else "present")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(d, indent=1, sort_keys=True) + "\n", encoding="utf-8")
                return {"dryrun": path}, d

            ran, d, _ = stages.run_stage(self.paths.stages, f"dry-run-{job}", inputs, fn, force=force,
                                         max_age=runner.DRYRUN_MAX_AGE, now=self.now())
        if not ran:
            d = json.loads(path.read_text(encoding="utf-8"))
            self.out("The dry run is up to date (same inputs, under 24 hours old); here it is again.")
        self.out(runner.format_dry_run(d))
        try:
            prices.check_fresh(table, self.now())
        except prices.PriceError as e:
            self.out(f"NOTE: {e}. generate will refuse until the table is re-verified.")
        return d

    def generate(self, job: str, budget_usd: str | None, tier: str | None = None) -> int:
        cfg = self.cfg()
        j = runner.job_settings(cfg, job)
        tier = tier or j["tier"]
        try:
            budget = Decimal(str(budget_usd))
        except (InvalidOperation, ValueError):
            raise runner.JobError("generate needs --budget-usd as a number of dollars") from None
        if not budget.is_finite() or budget <= 0:
            raise runner.JobError("--budget-usd must be a positive number of dollars")
        if budget > Decimal(str(j["cap_usd"])):
            raise runner.JobError(f"--budget-usd {budget} is above {job}'s ${j['cap_usd']:.2f} cap")
        now = self.now()
        table = prices.load(self.paths.prices)
        uses = {(cfg["arms"][a]["model"], t) for arms in j["arms"].values() for a in arms
                for t in {tier, "batch", prices.RESERVE_TIER}}  # Batch after six unanswered tries; reserved at Standard
        prices.require(table, sorted(uses), now)
        runner.check_efforts(cfg, job, table)
        if j.get("needs") == "pilot_gate":
            gate = self.paths.pilot_gate
            passed = gate.exists() and json.loads(gate.read_text(encoding="utf-8")).get("passed") is True
            if not passed:
                raise runner.JobError(f"{job} needs the O1 gate to have passed ({gate.relative_to(self.paths.root)} "
                                      'with "passed": true)')
        if j.get("needs") == "weak_spot_plan" and not self.paths.weak_spot_plan.exists():
            raise runner.JobError(f"{job} needs a weak-spot plan ({self.paths.weak_spot_plan.relative_to(self.paths.root)})")
        requests = runner.plan_requests(self.seams, cfg, job, self.read_briefs(job), tier)
        set_hash = runner.request_set_hash(job, tier, requests)
        dry = runner.check_dry_run(self.paths, job, tier, set_hash, now)
        with runner.GeneratorLock(self.paths.lock):
            with self.open_ledger(cfg) as lg:
                if not lg.totals()["jobs"][job]["released"]:
                    raise runner.JobError(f"{job} is not released; `ledger release-job {job} --reason TEXT` first")
                store = runner.ItemStore(self.paths.items, self.paths.items_log)
                client = self.client_factory()
                try:
                    run = runner.Runner(job=job, cfg=cfg, table=table, ledger=lg, store=store, client=client,
                                        seams=self.seams, paths=self.paths, tier=tier, budget_usd=budget,
                                        sleep=self.sleep, clock=self.clock, now=self.now, out=self.out,
                                        units={r["unit"] for r in requests})  # the dry run's checked set only
                    run.plan_items(requests, dry)
                    _, summary, _ = stages.run_stage(
                        self.paths.stages, f"generate-{job}",
                        {"request_set": set_hash, "tier": tier, "budget_usd": str(budget)},
                        lambda: ({}, run.generate()), always=True, now=self.now)
                finally:
                    store.close()
                    getattr(client, "close", lambda: None)()
        self._print_summary(summary)
        return 2 if summary["stopped"] else 0

    def collect(self, job: str) -> int:
        cfg = self.cfg()
        j = runner.job_settings(cfg, job)
        table = prices.load(self.paths.prices)
        with runner.GeneratorLock(self.paths.lock):
            with self.open_ledger(cfg) as lg:
                store = runner.ItemStore(self.paths.items, self.paths.items_log)
                client = self.client_factory()
                try:
                    run = runner.Runner(job=job, cfg=cfg, table=table, ledger=lg, store=store, client=client,
                                        seams=self.seams, paths=self.paths, tier=j["tier"], sleep=self.sleep,
                                        clock=self.clock, now=self.now, out=self.out)

                    def fn():
                        run.resume()
                        chunks = run.collect()
                        return {}, {**run.summary(), "chunks": chunks}
                    _, summary, _ = stages.run_stage(self.paths.stages, f"collect-{job}", {}, fn, always=True,
                                                     now=self.now)
                finally:
                    store.close()
                    getattr(client, "close", lambda: None)()
        self.out(f"collect {job}: {summary['chunks']['finished']} chunks finished, {summary['chunks']['open']} open")
        self._print_summary(summary)
        return 2 if summary["stopped"] else 0

    def _print_summary(self, s: dict) -> None:
        jt = s.get("job_totals") or {}
        states = ", ".join(f"{k} {v}" for k, v in s["states"].items())
        self.out(f"{s['job']}: sent {s['sent']} requests this run; items: {states}")
        self.out(f"{s['job']}: spent {usd(jt.get('spent', 0))}, held {usd(jt.get('held', 0))}, cap "
                 f"{usd(jt.get('cap', 0))}; {s['open_chunks']} Batch chunks open")
        if s.get("stopped"):
            self.out(f"{s['job']}: stopped: {s['stopped']}")

    def status(self) -> int:
        self.out("Stages:")
        found = stages.markers(self.paths.stages) if self.paths.stages.exists() else []
        for m in found:
            sha = (m.get("git_sha") or "")[:8]
            self.out(f"  {m['stage']:<18} {m['status']:<8} finished {m.get('finished_at') or '-'}  git {sha or '-'}"
                     + (f"  ({m['note']})" if m.get("note") else ""))
        if not found:
            self.out("  none run yet")
        if not self.ledger_exists():
            self.out("Ledger: not created yet (the first dry run creates it).")
        else:
            try:
                with self.open_ledger(self.cfg()) as lg:
                    self._print_totals(lg.totals())
            except ledger_mod.LedgerError as e:
                self.out(f"Ledger: UNAVAILABLE: {e}")
        if self.paths.items.exists():
            store = runner.ItemStore(self.paths.items, self.paths.items_log)
            try:
                jobs = sorted({i["job"] for i in store.items()})
                for job in jobs:
                    counts = ", ".join(f"{k} {v}" for k, v in store.counts(job).items())
                    chunks = len(store.chunks(job, open_only=True))
                    self.out(f"Items {job}: {counts}; {chunks} Batch chunks open")
            finally:
                store.close()
        return 0

    def _print_totals(self, totals: dict) -> None:
        self.out("Ledger:")
        for account, a in totals["accounts"].items():
            self.out(f"  account {account:<7} cap {usd(a['cap'])} (margin {usd(a['margin'])}): spent {usd(a['spent'])},"
                     f" held {usd(a['held'])}, headroom {usd(a['headroom'])}")
        for job, t in totals["jobs"].items():
            flag = "" if t["released"] else "  (not released)"
            self.out(f"  job {job:<4} cap {usd(t['cap'])}: spent {usd(t['spent'])}, held {usd(t['held'])}, "
                     f"headroom {usd(t['headroom'])}{flag}")

    def ledger_report(self) -> int:
        with self.open_ledger(self.cfg()) as lg:
            report = lg.report()
        self.out(f"{'job':<5} {'model':<15} {'tier':<9} {'requests':>8} {'settled':>11} {'unknown':>7} "
                 f"{'input':>10} {'cached':>9} {'writes':>9} {'output':>10} {'reasoning':>10}")
        for r in report["rows"]:
            t = r["tokens"]
            self.out(f"{r['job']:<5} {r['model']:<15} {r['tier']:<9} {r['requests']:>8} {usd(r['settled']):>11} "
                     f"{r['unknown']:>7} {t['input']:>10,} {t['cached']:>9,} {t['cache_write']:>9,} "
                     f"{t['output']:>10,} {t['reasoning']:>10,}")
            if r["over_reservation"]:
                self.out(f"  WARNING: {r['over_reservation']} settlements above their reservations")
        self._print_totals(report["totals"])
        return 0

    def release_job(self, job: str, reason: str) -> int:
        with self.open_ledger(self.cfg()) as lg:
            changed = lg.release_job(job, reason)
        self.out(f"{job} released: {reason}" if changed else f"{job} was already released")
        return 0


def gate_command(job: str, paths: runner.Paths, out=print) -> int:
    """The pilot gate (spec §4.10); src.scenarios.gate scores it."""
    from src.scenarios import gate

    return gate.run(job, paths, out)


def simulate_command(workers: int, out_dir: str | None, paths: runner.Paths, out=print, a1: bool = False) -> int:
    """Simulate the frozen library (spec §5, §6.1), or with `a1` ablation A1's replicates (spec §8.8);
    src.scenarios.freeze drives the simulator."""
    from src.scenarios import freeze

    if a1:
        return freeze.simulate_a1(workers, Path(out_dir) if out_dir else None, out=out)
    return freeze.simulate(workers, Path(out_dir) if out_dir else None, out)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m src.neural.pipeline", description="Tracer v2 pipeline.")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="stage markers, ledger totals and item counts")
    p = sub.add_parser("plan", help="write the job's briefs")
    p.add_argument("--job", required=True)
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("dry-run", help="price every request of a job; sends nothing")
    p.add_argument("--job", required=True)
    p.add_argument("--tier", choices=TIERS)
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("generate", help="send a job's requests (spends money)")
    p.add_argument("--job", required=True)
    p.add_argument("--budget-usd", required=True)
    p.add_argument("--tier", choices=TIERS)
    p = sub.add_parser("collect", help="collect finished Batch chunks")
    p.add_argument("--job", required=True)
    p = sub.add_parser("gate", help="the pilot gate: G1 to G6 per model and the model rule (spec §4.10)")
    p.add_argument("--job", required=True, choices=("O1", "O1b"))
    p = sub.add_parser("freeze", help="freeze accepted scenarios into datasets/scenarios-v2.* (spec §4.11)")
    p.add_argument("--jobs", required=True, help="comma-separated, for example O1,O2")
    p.add_argument("--workers", type=int, default=16)
    p = sub.add_parser("simulate", help="simulate the frozen library (spec §5, §6.1)")
    p.add_argument("--workers", type=int, default=16)
    p.add_argument("--out", default=None, help="output folder (default: the simulation folder)")
    p.add_argument("--a1", action="store_true", help="ablation A1's train replicates (its plan, a1-replicates.json) "
                                                     "and every validation replicate, into A1's own folder (spec §8.8)")
    p = sub.add_parser("store", help="load the simulation into the v2 store (spec §6.3)")
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--a1", action="store_true", help="load A1's simulation into A1's own store (spec §8.8)")
    p = sub.add_parser("evaluate", help="score every method on a set (spec §8); test sets go through the lock")
    p.add_argument("--split", required=True,
                   choices=("val", "test", "test_ood", "legacy_v1", "control_v1world"))
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--store", default=None, help="store folder (default: the v2 store)")
    p.add_argument("--out", default=None, help="output folder (default: results/neural/<split>/)")
    p.add_argument("--tracernet", default=None, help="TracerNet's risk table (parquet) for this set")
    p.add_argument("--artifact", default=None, help="TracerNet artifact: scores the set (after the lock on a test set)")
    p.add_argument("--matched", action="store_true", help="also tune knobs to matched alert volume (R4)")
    p.add_argument("--relock-reason", default=None)
    p.add_argument("--train-store", default=None, help="for control_v1world: the v2 store giving global fits")
    p = sub.add_parser("train", help="train a rung, or search on rung B with --hpo (spec §7.7, §7.8)")
    p.add_argument("--rung", choices=("L", "A", "B", "C"))
    p.add_argument("--seeds", default="0", help="comma-separated, for example 0,1,2")
    p.add_argument("--hpo", action="store_true", help="the hyperparameter search (rung B)")
    p.add_argument("--store", default=None, help="store folder (default: the v2 store)")
    p.add_argument("--time-box-min", type=float, default=None)
    p.add_argument("--max-samples", type=int, default=None)
    p.add_argument("--device", default="auto", help="train: auto, cpu, mps or cuda[:N]")
    p.add_argument("--devices", default="auto", help="--hpo: comma-separated devices, one per worker")
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--trials", type=int, default=None)
    p.add_argument("--study", default=None)
    p.add_argument("--storage", default=None)
    p.add_argument("--first-worker", type=int, default=0)
    p.add_argument("--run-id", default=None)
    p.add_argument("--resume", action="store_true")
    p = sub.add_parser("select", help="choose the rung from the ladder's validation runs (spec §7.9)")
    p.add_argument("--store", default=None)
    p = sub.add_parser("calibrate", help="3-seed ensemble, calibration and the artifact (spec §7.10, §10.4)")
    p.add_argument("--store", default=None)
    p.add_argument("--rung", choices=("A", "B", "C"), default=None)
    p.add_argument("--out", default=None, help="artifact folder (default artifacts/tracernet/v1)")
    p.add_argument("--workers", type=int, default=None)
    p = sub.add_parser("weakspots", help="validation weak spots and the O3 plan (spec §4.12)")
    p.add_argument("--rows", default=None, help="validation rows (default data/neural/weakspots/val-rows.parquet)")
    p.add_argument("--risk", default=None, help="TracerNet's validation RiskTable (default data/neural/risk/val/*.parquet)")
    p.add_argument("--evaluation", default=None, help="default results/neural/val/evaluation.json")
    p.add_argument("--store", default=None, help="the observed store whose validation split is checked")
    p.add_argument("--out", default=None, help="default results/neural/generation/ (dev stores: data/neural/dev-generation/)")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("ablate", help="ablation job specs, one arm, or the collected results (spec §8.8)")
    p.add_argument("--set", dest="ablation_set", choices=("core", "all"), default=None)
    p.add_argument("--run", default=None, help="run one job (arm and seed) by its id")
    p.add_argument("--stage", choices=("train", "score", "all"), default="all")
    p.add_argument("--collect", action="store_true", help="gather every arm into results/neural/ablations.json")
    p.add_argument("--store", default=None, help="the training store")
    p.add_argument("--eval-store", default=None, help="the store holding labels for scoring")
    p.add_argument("--rung", default=None, help="default: the chosen rung in results/neural/selection.json")
    p.add_argument("--cells", default=None, help="scenario cells (default data/neural/scenario-cells.json)")
    p.add_argument("--evaluate", default="val,test")
    p.add_argument("--device", default="auto")
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--relock-reason", default=None)
    p = sub.add_parser("report", help="render results/neural/REPORT.md (spec §8.10)")
    p.add_argument("--out", default=None)
    p.add_argument("--ledger-json", default=None, help="ledger totals as JSON, when the ledger mirror is elsewhere")
    p = sub.add_parser("ledger", help="ledger report or job release")
    lsub = p.add_subparsers(dest="ledger_command", required=True)
    lsub.add_parser("report")
    r = lsub.add_parser("release-job")
    r.add_argument("job")
    r.add_argument("--reason", required=True)
    return ap


def main(argv: list[str] | None = None, *, paths: runner.Paths | None = None, seams=None, client_factory=None,
         now=None, out=print, sleep=time.sleep, clock=time.monotonic) -> int:
    args = build_parser().parse_args(argv)
    pipe = Pipeline(paths=paths or runner.Paths(), seams=seams, client_factory=client_factory, now=now, out=out,
                    sleep=sleep, clock=clock)
    try:
        if args.command == "status":
            return pipe.status()
        if args.command == "plan":
            pipe.plan(args.job, force=args.force)
            return 0
        if args.command == "dry-run":
            pipe.dry_run(args.job, tier=args.tier, force=args.force)
            return 0
        if args.command == "generate":
            return pipe.generate(args.job, args.budget_usd, tier=args.tier)
        if args.command == "collect":
            return pipe.collect(args.job)
        if args.command == "gate":
            return gate_command(args.job, pipe.paths, out)
        if args.command == "freeze":
            from src.scenarios import freeze
            freeze.freeze(tuple(j.strip() for j in args.jobs.split(",") if j.strip()), paths=pipe.paths,
                          workers=args.workers, out=out)
            return 0
        if args.command == "simulate":
            return simulate_command(args.workers, args.out, pipe.paths, out, a1=args.a1)
        if args.command == "store":
            from src.pipeline import store
            if args.a1:
                return store.command_build_v2(workers=args.workers, sim_dir=store.SIM_A1_DIR,
                                              store_dir=store.STORE_V2_A1_DIR, out=out)
            return store.command_build_v2(workers=args.workers, out=out)
        if args.command == "evaluate":
            from src.pipeline import neural_eval
            opt = (lambda v: Path(v) if v else None)
            return neural_eval.command_evaluate(args.split, store_dir=opt(args.store), out_dir=opt(args.out),
                                                workers=args.workers, tracernet=opt(args.tracernet),
                                                artifact_dir=opt(args.artifact), matched=args.matched,
                                                relock_reason=args.relock_reason,
                                                train_store_dir=opt(args.train_store))
        if args.command == "train":
            opt = (lambda v: Path(v) if v else None)
            if args.hpo:
                from src.neural import hpo
                return hpo.hpo_command(store_dir=opt(args.store), workers=args.workers or 1, devices=args.devices,
                                       trials=args.trials, time_box_min=args.time_box_min,
                                       max_samples=args.max_samples, study_name=args.study, storage=args.storage,
                                       first_worker=args.first_worker, out=out)
            if args.rung is None:
                out("error: train needs --rung (or --hpo)")
                return 1
            from src.neural import train
            return train.train_command(rung=args.rung, seeds=tuple(int(x) for x in args.seeds.split(",") if x),
                                       store_dir=opt(args.store), time_box_min=args.time_box_min,
                                       max_samples=args.max_samples, device=args.device, run_id=args.run_id,
                                       resume=args.resume, workers=args.workers, out=out)
        if args.command == "select":
            from src.neural import ensemble
            return ensemble.select_command(store_dir=Path(args.store) if args.store else None, out=out)
        if args.command == "calibrate":
            from src.neural import ensemble
            return ensemble.calibrate_command(store_dir=Path(args.store) if args.store else None, rung=args.rung,
                                              out_dir=Path(args.out) if args.out else None, workers=args.workers,
                                              out=out)
        if args.command == "weakspots":
            from src.neural import weakspots
            opt = (lambda v: Path(v) if v else None)
            try:
                return weakspots.weakspots_command(rows_path=opt(args.rows), risk=args.risk,
                                                   evaluation_path=opt(args.evaluation), store_dir=opt(args.store),
                                                   out_dir=opt(args.out), force=args.force, out=out)
            except weakspots.WeakSpotError as e:
                out(f"error: {e}")
                return 1
        if args.command == "ablate":
            from src.neural import ablations
            opt = (lambda v: Path(v) if v else None)
            return ablations.ablate_command(set_name=args.ablation_set, run=args.run, collect_results=args.collect,
                                            stage=args.stage, store_dir=opt(args.store), eval_store=opt(args.eval_store),
                                            rung=args.rung, cells_path=opt(args.cells), device=args.device,
                                            workers=args.workers, relock_reason=args.relock_reason,
                                            evaluate=args.evaluate, out=out)
        if args.command == "report":
            from src.neural import report
            return report.report_command(out_path=Path(args.out) if args.out else None,
                                         ledger_json=Path(args.ledger_json) if args.ledger_json else None, out=out)
        if args.ledger_command == "report":
            return pipe.ledger_report()
        return pipe.release_job(args.job, args.reason)
    except (runner.JobError, ledger_mod.LedgerError, prices.PriceError, ClientError) as e:
        out(f"error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
