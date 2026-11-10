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
    python -m src.neural.pipeline explain --split val|test|test_ood   (spec §9.4's checks: explain_command below)
    python -m src.neural.pipeline export ...  (src/api/export_neural.py's own arguments: docs/demo/neural.json)
    python -m src.neural.pipeline decide --job X1|X2|X3 --set SET --dry-run | --budget-usd N | --score-only
                                              (the decision layer, spec §11: decide_command below)

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


UNLOCKED_OWN_STORES = ("legacy_v1", "control_v1world")   # sets whose store is not the v2 store's own split
RISK_KEEP = ("specimen_id", "run", "scenario_id", "replicate", "tick_min", "p_miss", "harm_default", "harm_equal",
             "ensemble_sd")                              # the columns neural_eval.risk_table writes


def own_store_risk(split: str, artifact_dir: Path, store_dir: Path | None, workers: int | None, out=print) -> Path:
    """TracerNet's RiskTable for `evaluate --split legacy_v1|control_v1world --artifact`, whose stores the v2 risk
    table cannot read by split name: on the v1 store every run (fit runs 0 to 23 are the history, 24 to 29 scored)
    through Predictor.score; on the control store its v1-world scenario, which that store keeps in val. Written
    under data/neural/risk/<split>/ with risk_table's columns; returns the parts' glob."""
    from src.models.tracernet.predictor import Predictor
    from src.pipeline import neural_eval as ne, store

    out_dir = ne.RISK_DIR / split
    if split == "control_v1world":
        return ne.risk_table("val", artifact_dir, store_dir=store_dir or store.CONTROL_STORE_DIR, out_dir=out_dir,
                             workers=workers)
    store_dir = Path(store_dir) if store_dir is not None else store.STORE_DIR
    con = store.open_observed(store_dir)
    try:
        runs = sorted(int(r[0]) for r in con.execute("select distinct run from specimens").fetchall())
    finally:
        con.close()
    frame = Predictor.load(artifact_dir).score(store_dir, runs, workers=workers)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.parquet"):
        old.unlink()
    frame.select(RISK_KEEP).write_parquet(out_dir / "part-0000.parquet")
    out(f"risk {split}: {frame.height:,} rows over runs {runs[0]} to {runs[-1]}")
    return out_dir / "*.parquet"


def risk_snapshot(files) -> tuple:
    """The risk table's parts read once into memory: (their rows, {TracerNet: {part: SHA-256 of the bytes read}}).
    The hashes the guard compares and the rows decoded come from the same bytes, so a part replaced on disk after
    the guard cannot be read under the old part's hash."""
    import hashlib
    import io

    import polars as pl

    from src.pipeline import neural_eval as ne

    frames, hashes = [], {}
    for f in files:
        raw = Path(f).read_bytes()
        hashes[Path(f).name] = hashlib.sha256(raw).hexdigest()
        frames.append(pl.read_parquet(io.BytesIO(raw)))
    return (pl.concat(frames) if frames else pl.DataFrame()), {ne.TRACERNET: hashes}


def alert_inputs(files, store_dir: Path, split: str):
    """The risk table's rows (parquet `files`), each with its specimen's urgency tier and window from the observed
    store. The risk table keeps a slim projection, and Predictor.alerts also needs the tier (for harm) and the
    window; `at` and `p_never` stay empty, since neither the knob nor the checks read them. Refuses an empty table
    and rows that are not specimens of `split` in the store."""
    import polars as pl

    from src.pipeline import store

    risk = files if isinstance(files, pl.DataFrame) else pl.scan_parquet(list(files)).collect()
    if risk.height == 0:
        raise ValueError(f"the risk table holds no rows ({', '.join(map(str, files))})")
    con = store.open_observed_v2(store_dir)
    try:
        rows = con.execute("select specimen_id, urgency_tier, window_min from specimens where split = ?",
                           [split]).fetchall()
    finally:
        con.close()
    spec = pl.DataFrame(rows, schema={"specimen_id": pl.Utf8, "tier": pl.Int64, "window_min": pl.Int64}, orient="row")
    joined = risk.join(spec, on="specimen_id", how="left").with_columns(
        at=pl.lit(None, dtype=pl.Datetime), p_never=pl.lit(None, dtype=pl.Float64))
    stray = joined["tier"].null_count()
    if stray:
        raise ValueError(f"{stray:,} of {risk.height:,} risk rows are not {split} specimens in {store_dir}: the risk "
                         f"table is not the {split} one")
    return joined


def explain_command(split: str, *, store_dir: Path | None = None, artifact_dir: Path | None = None,
                    tracernet: str | None = None, matched: bool = False, limit: int | None = None, workers: int = 1,
                    out_path: Path | None = None, out=print) -> int:
    """`explain --split SPLIT`: spec §9.4's checks on TracerNet's alerts on the split's scored replicates at the
    headline budget (6 alerts per 8-hour shift, the default 3-2-1 weights), each scenario's knob tuned on its history
    replicate from alert volume alone, written to results/neural/explanations.json (`out_path`). The alerts come
    from the risk table an evaluation of the split wrote (`evaluate --split SPLIT --artifact ...`, by default
    data/neural/risk/<split>/*.parquet), read part by part as hashed; `limit` explains a seeded sample of them.

    On test and test_ood this is a later look at a scored test set (spec §8.7). It needs the lock, with TracerNet's
    risk table recorded for the split; the lock record is rebuilt from the arguments of that split's look (every
    baseline and TracerNet, `matched` as that look had it, test_ood's ood_feature subset), and guard_test_run
    logs the look as a repeat only when every hash and the risk table match, refusing otherwise. It never takes
    the first lock and never relocks."""
    import polars as pl

    from src.models.tracernet import explain
    from src.models.tracernet.predictor import DEFAULT_ARTIFACT, HISTORY_REPLICATE, Predictor
    from src.pipeline import neural_eval as ne, store

    store_dir = Path(store_dir) if store_dir is not None else store.STORE_V2_DIR
    artifact_dir = Path(artifact_dir) if artifact_dir is not None else DEFAULT_ARTIFACT
    tracernet = str(tracernet) if tracernet is not None else str(ne.RISK_DIR / split / "*.parquet")
    out_path = Path(out_path) if out_path is not None else (     # the report reads test's; other sets beside it
        explain.RESULTS_PATH if split == "test" else explain.RESULTS_PATH.with_name(f"explanations-{split}.json"))
    methods = ne.baselines() + [ne.tracernet_method(tracernet)]
    pinned = ne._pin_risk_tables(methods)       # resolves the table's parts
    table, parts = risk_snapshot(methods[-1].series.files)
    if pinned != parts:
        raise ValueError(f"the risk table at {tracernet} changed while it was read; run explain again")
    run = None
    if split in ne.LOCKED_SPLITS:
        lock_path, runs_path = ne.LOCK_PATH, ne.TEST_RUNS_PATH
        if not lock_path.exists():
            raise ne.LockError(f"no test lock at {lock_path}: explain looks again at a test set an evaluation has "
                               f"scored, so run `evaluate --split {split} --artifact ...` first")
        locked = json.loads(lock_path.read_text(encoding="utf-8"))
        if ne.TRACERNET not in ((locked.get("risk_parts") or {}).get(split) or {}):
            raise ne.LockError(f"the test lock records no TracerNet risk table for {split}: no look has scored "
                               "TracerNet on it, so there are no alerts to explain")
        feature = ne.ood_feature_scenarios(store_dir) if split == "test_ood" else []
        hashes = ne.lock_record(store_dir, methods, artifact_dir=artifact_dir, matched=matched,
                                subsets={ne.OOD_FEATURE: feature} if feature else None)
        try:
            run = ne.guard_test_run(split, hashes, lock_path=lock_path, runs_path=runs_path, risk_parts=parts)
        except ne.LockError as e:
            raise ne.LockError(f"{e} (explain never relocks: it explains only the look the lock holds)") from None
        if run["status"] != "repeat":
            raise ne.LockError(f"the {split} look was logged as {run['status']!r}, not as a repeat of the locked one")
        out(f"{split} run: {run['status']} at {run['time']}")
        # inside the lock's mutex: the lock still records exactly the parts read, the run still holds for this
        # artifact and store, and only then is the store read (the guard admits a repeat whose split has no
        # recorded table, so this closes a relock that cleared the parts between the check above and the guard)
        with ne._lock_transaction(runs_path):
            locked = json.loads(lock_path.read_text(encoding="utf-8"))
            if ((locked.get("risk_parts") or {}).get(split) or {}).get(ne.TRACERNET) != parts[ne.TRACERNET]:
                raise ne.LockError(f"the test lock no longer records the {split} risk table this run read")
            run.check(split, artifact_dir=artifact_dir, store_dir=store_dir)
            risk = alert_inputs(table, store_dir, split)
    else:
        risk = alert_inputs(table, store_dir, split)
    pred = Predictor.load(artifact_dir)
    history = pl.col("replicate") == HISTORY_REPLICATE
    alerts = pred.alerts(risk.filter(~history), budget_per_shift=ne.HEADLINE_BUDGET, weights="default",
                         tune_on=risk.filter(history))
    out(f"{alerts.height:,} {split} alerts at {ne.HEADLINE_BUDGET} per 8-hour shift (3-2-1 weights)")
    doc = explain.run_checks(pred, alerts, store_dir=store_dir, test_run=run, set_name=split, out_path=out_path,
                             workers=workers, limit=limit)
    verdicts = ", ".join(f"{name} {'passed' if c['passed'] else 'failed'}" for name, c in doc["checks"].items())
    out(f"explained {doc['alerts']:,} of {doc['alerts_given']:,} alerts ({doc['attributed_alerts']:,} attributed, "
        f"{doc['steps']} steps): {verdicts}; all passed: {doc['passed']}")
    out(f"wrote {out_path}")
    return 0


DECIDE_SETS = {"X1": ("library",), "X2": ("val", "test"), "X3": ("val", "test")}


def decision_look(split: str, store_dir: Path, artifact_dir: Path, methods, parts: dict, cfg: dict, *,
                  matched: bool = False, relock_reason: str | None = None, out=print):
    """The test run for a decision-layer look at a locked split (spec §8.7). It rebuilds the lock record of the
    split's TracerNet look (every baseline and TracerNet, `matched` as that look had it) with the decision-model
    configuration hash added. When only that hash differs from the lock (the first decision-layer look, or a changed
    decision configuration), it relocks with `relock_reason`, logged, and records TracerNet's risk table again (a
    relock clears the recorded tables); any other difference refuses, so the decision layer never relocks over a
    changed artifact, store or evaluation. When nothing differs, the look is logged as a repeat."""
    from src.decide import client as dc
    from src.pipeline import neural_eval as ne

    if not ne.LOCK_PATH.exists():
        raise ne.LockError(f"no test lock at {ne.LOCK_PATH}: the decision layer looks again at a test set an evaluation "
                           f"has scored, so run `evaluate --split {split} --artifact ...` first")
    locked = json.loads(ne.LOCK_PATH.read_text(encoding="utf-8"))
    if ne.TRACERNET not in ((locked.get("risk_parts") or {}).get(split) or {}):
        raise ne.LockError(f"the test lock records no TracerNet risk table for {split}: no look has scored TracerNet "
                           "on it, so it has no alerts")
    hashes = ne.lock_record(store_dir, methods, artifact_dir=artifact_dir, matched=matched,
                            decision_model=dc.lock_hash(cfg))
    changed = [k for k in ne.HASH_FIELDS if locked.get(k) != hashes[k]]
    other = [k for k in changed if k != "decision_model_sha256"]
    if other:
        raise ne.LockError(f"these differ from the locked look at {split}: {', '.join(other)}; the decision layer "
                           "relocks only to add its own configuration hash")
    if changed and not (relock_reason or "").strip():
        raise ne.LockError("the lock does not hold this decision-model configuration; spec §8.7 adds its hash to the "
                           "lock record, which is a relock: rerun with --relock-reason")
    run = ne.guard_test_run(split, hashes, relock_reason if changed else None, risk_parts=parts)
    if run["status"] == "relock":
        ne.record_risk_parts(run, parts)
    elif run["status"] != "repeat":
        raise ne.LockError(f"the {split} look was logged as {run['status']!r}")
    out(f"{split} run: {run['status']} at {run['time']}")
    return run


def _lock_still_holds(split: str, dry: dict, cfg: dict) -> None:
    """A spending run on a test set reads no test data, but sends states built under a test run: the lock must
    still be the one that run recorded."""
    from src.decide import client as dc
    from src.pipeline import neural_eval as ne

    rec = dry.get("test_run") or {}
    locked = json.loads(ne.LOCK_PATH.read_text(encoding="utf-8")) if ne.LOCK_PATH.exists() else {}
    if not rec or locked.get("time") != rec.get("lock_time") or locked.get("decision_model_sha256") != dc.lock_hash(cfg):
        raise ne.LockError(f"the test lock changed since the {split} dry run built these states; run the dry run again")


def _decide_risk(split: str, store_dir: Path, artifact_dir: Path, tracernet: str | None, cfg: dict, *,
                 matched: bool, relock_reason: str | None, table: bool = True, out=print):
    """TracerNet's risk table for the set (read once as hashed, when `table`; otherwise only its parts' hashes), its
    parts' files, and the test run on a locked split."""
    from src.pipeline import neural_eval as ne

    tracernet = str(tracernet) if tracernet is not None else str(ne.RISK_DIR / split / "*.parquet")
    methods = ne.baselines() + [ne.tracernet_method(tracernet)]
    pinned = ne._pin_risk_tables(methods)
    frame, parts = risk_snapshot(methods[-1].series.files) if table else (None, pinned)
    if pinned != parts:
        raise ValueError(f"the risk table at {tracernet} changed while it was read; run again")
    run = None
    if split in ne.LOCKED_SPLITS:
        run = decision_look(split, store_dir, artifact_dir, methods, parts, cfg, matched=matched,
                            relock_reason=relock_reason, out=out)
        with ne._lock_transaction(ne.TEST_RUNS_PATH):
            locked = json.loads(ne.LOCK_PATH.read_text(encoding="utf-8"))
            if ((locked.get("risk_parts") or {}).get(split) or {}).get(ne.TRACERNET) != parts[ne.TRACERNET]:
                raise ne.LockError(f"the test lock no longer records the {split} risk table this run read")
            run.check(split, artifact_dir=artifact_dir, store_dir=store_dir)
    return frame, methods[-1].series.files, run


def alert_p_never(pred, alerts, store_dir: Path, test_run=None):
    """The alerts with TracerNet's p_never at their ticks. The risk table keeps p_miss alone, so the alert ticks go
    through the predictor's members as `score` sends every tick (features from the observed store, calibrated), with
    the same lock check; only these ticks are scored."""
    import numpy as np
    import polars as pl

    from src.models.tracernet import features as F
    from src.models.tracernet import predictor as P

    if alerts.height == 0:
        return alerts
    runs = sorted({int(r) for r in alerts["run"].to_list()})
    hist = P._history_runs(store_dir, runs)
    pred.check_lock(store_dir, runs, hist, P.History(), test_run)
    t = F.load_table(store_dir, runs=sorted(set(runs) | set(hist)))
    rows = t.rows_of(alerts["specimen_id"].to_list())
    ticks = alerts["tick_min"].to_numpy().astype(np.float64)
    owner = alerts["run"].to_numpy()
    never = np.empty(len(rows))
    for run in np.unique(owner):
        sel = np.flatnonzero(owner == run)
        batch = F.features_for_run(t, int(run), rows[sel], ticks[sel], pred.schema, bins=pred.bins)
        never[sel] = pred.outputs(batch)["p_never"]
    return alerts.with_columns(p_never=pl.Series(np.minimum(never, alerts["p_miss"].to_numpy())))


def decide_items(job: str, split: str, cfg: dict, *, store_dir: Path, artifact_dir: Path, tracernet: str | None,
                 matched: bool, relock_reason: str | None, n: int | None, seed: int, workers: int, out=print):
    """The job's items, built from the observed store (X2, X3) or the library (X1), and the test run on a locked
    split."""
    import polars as pl

    from src.decide import critic, dispatcher, facts, zeroshot
    from src.models.tracernet.predictor import HISTORY_REPLICATE, Predictor
    from src.pipeline import neural_eval as ne

    if job == "X1":
        from src.scenarios import audit
        return critic.build_items(audit.records(), cfg, label_n=n, seed=seed), None
    if job == "X3" and split not in ne.LOCKED_SPLITS:
        table, run = None, None                    # X3 needs no risk table until it is scored
    else:
        table, _files, run = _decide_risk(split, store_dir, artifact_dir, tracernet, cfg, matched=matched,
                                          relock_reason=relock_reason, table=job == "X2", out=out)
    if job == "X3":
        ticks = zeroshot.sample_ticks(store_dir, split, n or int(cfg["samples"]["X3"]), seed, test_run=run)
        rows = facts.alert_facts(store_dir, [(t.specimen_id, t.run, t.tick_min) for t in ticks], test_run=run)
        out(f"X3 on {split}: {len(ticks):,} ticks from {len({t.scenario_id for t in ticks}):,} scenarios")
        return zeroshot.build_items(ticks, rows, cfg), run
    risk = alert_inputs(table, store_dir, split)
    pred = Predictor.load(artifact_dir)
    history = pl.col("replicate") == HISTORY_REPLICATE
    alerts = pred.alerts(risk.filter(~history), budget_per_shift=ne.HEADLINE_BUDGET, weights="default",
                         tune_on=risk.filter(history))
    sample = dispatcher.sample_alerts(alerts, n if n is not None else int(cfg["samples"]["X2"][split]), seed)
    out(f"X2 on {split}: {sample.height:,} of {alerts.height:,} alerts at {ne.HEADLINE_BUDGET} per 8-hour shift, from "
        f"{sample['scenario_id'].n_unique():,} whole scenarios; explaining them")
    explained = alert_p_never(pred, pred.explain(sample, store_dir=store_dir, test_run=run, workers=workers),
                              store_dir, test_run=run)
    rows = facts.alert_facts(store_dir, explained.select("specimen_id", "run", "tick_min").rows(), test_run=run)
    return dispatcher.build_items(explained, rows, cfg), run


def decide_command(job: str, set_name: str | None, *, mode: str, paths: runner.Paths, budget_usd: str | None = None,
                   n: int | None = None, seed: int | None = None, store_dir: Path | None = None,
                   artifact_dir: Path | None = None, tracernet: str | None = None, matched: bool = False,
                   relock_reason: str | None = None, workers: int = 1, train_store_dir: Path | None = None,
                   client_factory=None, now=None, sleep=time.sleep, out=print) -> int:
    """`decide --job X1|X2|X3 --set SET` with --dry-run (build and freeze the items, price them; no connection),
    --budget-usd N (send the frozen items, spending at most N), or --score-only (score the outputs against the
    labels through evaluate.py). X4 is not built."""
    from src.decide import client as dc, critic, dispatcher, distill, zeroshot
    from src.models.tracernet.predictor import DEFAULT_ARTIFACT
    from src.pipeline import dispatch_eval, neural_eval as ne, store

    if job == "X4":
        out(distill.REASON)
        return 1
    sets = DECIDE_SETS[job]
    set_name = (set_name or sets[0]) if job == "X1" else set_name
    if set_name not in sets:
        raise dc.JobError(f"{job} runs on {' or '.join(sets)}; name one with --set")
    cfg = dc.load_config()
    now = now or (lambda: datetime.now().astimezone())
    decide_dir = paths.root / "data" / "decide"
    results_dir = paths.root / "results" / "neural" / "decisions"
    store_dir = Path(store_dir) if store_dir is not None else store.STORE_V2_DIR
    artifact_dir = Path(artifact_dir) if artifact_dir is not None else DEFAULT_ARTIFACT
    seed = int(cfg["samples"]["seed"]) if seed is None else seed

    if mode == "dry-run":
        items, run = decide_items(job, set_name, cfg, store_dir=store_dir, artifact_dir=artifact_dir,
                                  tracernet=tracernet, matched=matched, relock_reason=relock_reason, n=n, seed=seed,
                                  workers=workers, out=out)
        d = dc.dry_run_step(job, set_name, items, cfg, paths.ledger, decide_dir=decide_dir, now=now(), out=out)
        if run is not None:
            locked = json.loads(ne.LOCK_PATH.read_text(encoding="utf-8"))
            d["test_run"] = {"time": run["time"], "status": run["status"], "lock_time": locked.get("time")}
            dc.write_atomic(dc.dryrun_path(decide_dir, job, set_name), json.dumps(d, indent=1, sort_keys=True) + "\n")
        return 0

    if mode == "spend":
        if not dc.key_present(cfg, paths.env):
            doc = dc.not_run(job, set_name, f"no {cfg['key_name']} in .env; dispatch uses rule R2 (spec §13)",
                             results_dir, now())
            out(f"{job} not run: {doc['reason']}")
            return 1
        if set_name in ne.LOCKED_SPLITS:
            _lock_still_holds(set_name, json.loads(dc.dryrun_path(decide_dir, job, set_name).read_text(encoding="utf-8")),
                              cfg)
        decide_fn = {"X1": critic.answers, "X3": zeroshot.answers,
                     "X2": lambda o, i, r: dispatcher.decisions(o, i, r, cfg)}[job]
        doc = dc.spend_step(job, set_name, cfg, budget_usd, paths.ledger, decide_fn, client_factory=client_factory,
                            decide_dir=decide_dir, results_dir=results_dir, env_path=paths.env, sleep=sleep, now=now,
                            out=out)
        return 2 if doc["stopped"] else 0

    rows = dc.read_outputs(dc.outputs_path(decide_dir, job, set_name))
    if job == "X1":
        scores = dispatch_eval.x1_scores(rows, noul_yes=float(cfg["decisions"]["noul_yes"]))
    else:
        if set_name in ne.LOCKED_SPLITS:           # the look: its guard pins and checks the risk table's parts
            _table, files, run = _decide_risk(set_name, store_dir, artifact_dir, tracernet, cfg, matched=matched,
                                              relock_reason=None, table=False, out=out)
        else:
            glob = Path(tracernet) if tracernet is not None else ne.RISK_DIR / set_name / "*.parquet"
            files, run = sorted(glob.parent.glob(glob.name)) if any(c in glob.name for c in "*?[") else [glob], None
        if job == "X2":
            labels = dispatch_eval.x2_labels(rows, set_name, store_dir, test_run=run)
            scores = dispatch_eval.x2_scores(rows, labels, set_name, cfg)
        else:
            labels = dispatch_eval.x3_labels(rows, set_name, store_dir, test_run=run)
            tn = dispatch_eval.tracernet_at(rows, files)
            analytic, notes = dispatch_eval.analytic_local_at(rows, set_name, store_dir, train_store_dir=train_store_dir,
                                                              test_run=run)
            scores = dispatch_eval.x3_scores(rows, labels, tn, analytic, set_name, notes=notes)
    scores |= {"job": job, "notice": dc.NOTICE, "created_at": now().isoformat(timespec="seconds"),
               "config_hash": dc.config_hash(cfg)}
    path = results_dir / f"{job}-{set_name}-scores.json"
    dc.write_atomic(path, json.dumps(scores, indent=1, sort_keys=True, allow_nan=False) + "\n")
    out(f"{job} on {set_name}: scored; wrote {path}")
    if job == "X2":
        a = scores["adoption"]
        out(f"adoption rule held: {a['holds']} ({'binding' if a['binding'] else 'not binding: validation'})")
    return 0


def _limit(value: str) -> int | None:
    if value == "all":
        return None
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("--limit is a positive number of alerts, or all")
    return n


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
    p.add_argument("--control", action="store_true", help="simulate the v1-world scenario's replicates into the "
                                                          "control_v1world store (spec §8.1)")
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
    p.add_argument("--evaluate", default="val,test", help="sets: the jobs --set writes, or the sets --run scores")
    p.add_argument("--device", default="auto")
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--relock-reason", default=None)
    p = sub.add_parser("explain", help="the explanation checks on a set's alerts at 6 alerts per 8-hour shift "
                                       "(spec §9.4); test sets only as a repeat of their locked look")
    p.add_argument("--split", required=True, choices=("val", "test", "test_ood"))
    p.add_argument("--store", default=None, help="store folder (default: the v2 store)")
    p.add_argument("--artifact", default=None, help="TracerNet artifact (default artifacts/tracernet/v1)")
    p.add_argument("--tracernet", default=None, help="the set's risk table, a parquet file or glob (default "
                                                     "data/neural/risk/<split>/*.parquet)")
    p.add_argument("--matched", action="store_true", help="as the set's locked look was run (its lock record)")
    p.add_argument("--limit", type=_limit, default=None, help="explain a seeded sample of N alerts, or all (default)")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--out", default=None, help="default results/neural/explanations.json for test (the report's), "
                                               "explanations-<split>.json otherwise")
    sub.add_parser("export", add_help=False, help="TracerNet's demo file, docs/demo/neural.json: every argument goes "
                                                  "to python -m src.api.export_neural")
    p = sub.add_parser("decide", help="the decision layer's jobs X1 to X3 (spec §11): --dry-run, then --budget-usd, "
                                      "then --score-only; test sets go through the lock")
    p.add_argument("--job", required=True, choices=("X1", "X2", "X3", "X4"))
    p.add_argument("--set", dest="decide_set", default=None, help="X1: library; X2 and X3: val or test")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="build and freeze the items and price them; no "
                                                             "connection, no spending")
    mode.add_argument("--budget-usd", default=None, help="send the frozen items, spending at most this much")
    mode.add_argument("--score-only", action="store_true", help="score the outputs against the labels")
    p.add_argument("--n", type=int, default=None, help="X1: label scenarios; X2: alerts; X3: ticks (config default)")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--store", default=None, help="store folder (default: the v2 store)")
    p.add_argument("--artifact", default=None, help="TracerNet artifact (default artifacts/tracernet/v1)")
    p.add_argument("--tracernet", default=None, help="the set's risk table (default data/neural/risk/<set>/*.parquet)")
    p.add_argument("--matched", action="store_true", help="as the set's locked look was run (its lock record)")
    p.add_argument("--relock-reason", default=None, help="a test set's first decision-layer look adds the decision "
                                                         "model's hash to the lock, which needs a reason")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--train-store", default=None, help="X3 scoring: the store whose train split gives the global fit")
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
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["export"]:     # its arguments, --help included, are export_neural's own
        from src.api import export_neural
        return export_neural.main(argv[1:])
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
            if args.control:
                counts = store.build_control_v2(workers=args.workers or 1)
                out(f"control_v1world store {store.CONTROL_STORE_DIR}: {json.dumps(counts, sort_keys=True)}")
                return 0
            if args.a1:
                return store.command_build_v2(workers=args.workers, sim_dir=store.SIM_A1_DIR,
                                              store_dir=store.STORE_V2_A1_DIR, out=out)
            return store.command_build_v2(workers=args.workers, out=out)
        if args.command == "evaluate":
            from src.pipeline import neural_eval
            opt = (lambda v: Path(v) if v else None)
            tracernet = opt(args.tracernet)
            if tracernet is None and args.artifact and args.split in UNLOCKED_OWN_STORES:
                tracernet = own_store_risk(args.split, Path(args.artifact), opt(args.store), args.workers, out=out)
            return neural_eval.command_evaluate(args.split, store_dir=opt(args.store), out_dir=opt(args.out),
                                                workers=args.workers, tracernet=tracernet,
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
        if args.command == "explain":
            from src.pipeline import neural_eval
            try:
                return explain_command(args.split, store_dir=Path(args.store) if args.store else None,
                                       artifact_dir=Path(args.artifact) if args.artifact else None,
                                       tracernet=args.tracernet, matched=args.matched, limit=args.limit,
                                       workers=args.workers, out_path=Path(args.out) if args.out else None, out=out)
            except (neural_eval.LockError, ValueError, FileNotFoundError) as e:
                out(f"error: {e}")
                return 1
        if args.command == "decide":
            from src.decide import client as dc, facts, render
            from src.pipeline import neural_eval
            opt = (lambda v: Path(v) if v else None)
            mode = "dry-run" if args.dry_run else ("score" if args.score_only else "spend")
            try:
                return decide_command(args.job, args.decide_set, mode=mode, paths=pipe.paths,
                                      budget_usd=args.budget_usd, n=args.n, seed=args.seed, store_dir=opt(args.store),
                                      artifact_dir=opt(args.artifact), tracernet=args.tracernet, matched=args.matched,
                                      relock_reason=args.relock_reason, workers=args.workers,
                                      train_store_dir=opt(args.train_store), client_factory=client_factory,
                                      now=now, sleep=sleep, out=out)
            except (dc.JobError, dc.DecisionError, neural_eval.LockError, facts.FactsError, render.RenderError,
                    ValueError, FileNotFoundError) as e:
                out(f"error: {e}")
                return 1
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
