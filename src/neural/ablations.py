"""Ablations A1 to A9 (docs/neural/spec.md §8.8; stage S18).

    python -m src.neural.pipeline ablate --set core|all [--store DIR] [--eval-store DIR] [--rung B] [--cells PATH]
    python -m src.neural.pipeline ablate --run ARM_ID [--stage train|score|all] [--device cuda] [--workers N]
    python -m src.neural.pipeline ablate --collect

`--set` writes one job specification per arm and seed (data/neural/ablations/jobs/<id>.json, listed in jobs.json),
so each can run as its own cluster job: `--run ID --stage train` where the observed store is, `--stage score` where
the evaluation store is. `--collect` gathers every finished arm into results/neural/ablations.json.

Every trained arm is a configuration of src/neural/train.py's run: the same rung settings, the same number of
gradient steps (`max_steps`, the steps of `step_samples` samples at the configured batch, early stopping off), and
one difference, a data subset (`train_subset`), an input cut (`input_mask`, train.mask_inputs), a rung, or the loss
weights. Three seeds for A1, A2, A5 and A6, one otherwise.

  A1  the same 800 scenario-replicates as 100 x 8, 200 x 4, 400 x 2 and 800 x 1: nested train scenario sets (lowest
      salted hash first) on a store that holds their extra replicates (data/neural/ablations/a1-replicates.json says
      which; the simulator writes them, at no API cost)
  A2  with and without the edge stratum, the normal stratum downsampled so both arms have the same number of
      scenarios; read overall and on the edge slice. The strata come from the scenario cells file (train and
      validation scenarios only), which the evaluation side writes
  A5  rungs L, A, B and C, and the chosen rung's three seeds as an ensemble against each single seed
  A6  the chosen rung without context (specimen and elapsed features only), without the fingerprint, without the
      roster, and without the aggregates (encoders only); its full model is A5's chosen-rung arm, trained identically.
      Plus the negative control: TracerNet's lead over the local analytic refit on control_v1world (from that
      evaluation)
  A7  (--set all) the distribution NLL alone, plus the p_miss cross-entropy, plus the ranking term
  A3, A4, A8, A9 are recorded as not run, each with its reason

Scoring an arm: its runs become a calibrated artifact (src/neural/ensemble.py `build`, calibrated on validation),
scored on validation, then on test. Test is scored only after the main test lock exists (spec §8.7: ablations run
after the lock), through the same guard as the main evaluation (neural_eval.guard_test_run) with the arm's own lock
file, every look logged in results/neural/ablations/test_runs.jsonl. Arm numbers are descriptive and never change
the headline model.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np

from src.models.tracernet import features as F
from src.neural import train as T
from src.pipeline import store

ROOT = Path(__file__).resolve().parents[2]
ABLATION_DIR = ROOT / "data" / "neural" / "ablations"
CELLS_PATH = ROOT / "data" / "neural" / "scenario-cells.json"
RESULT_PATH = ROOT / "results" / "neural" / "ablations.json"
TEST_RUNS_PATH = ROOT / "results" / "neural" / "ablations" / "test_runs.jsonl"
SELECTION_PATH = ROOT / "results" / "neural" / "selection.json"
CONTROL_PATH = ROOT / "results" / "neural" / "control_v1world" / "evaluation.json"
ARM_METHOD = "arm"
QUESTIONS = {"A1": "More data or more variety?", "A2": "Do the hard edge cases matter?", "A3": "Did round 2 help?",
             "A4": "Was the API spend worth it?", "A5": "Which parts of the network earn their place?",
             "A6": "Is it the context?", "A7": "Does the loss matter?",
             "A8": "Does mixing generators help across generators?", "A9": "Neural or tabular?"}
CORE = ("A1", "A2", "A5", "A6")
SETS = {"core": CORE, "all": CORE + ("A7",)}
NOT_BUILT = {
    "A3": "not run: it needs each train scenario's job (O2 or O3), which the scenario cells do not carry, and O3's data",
    "A4": "not run: it needs code-random scenarios simulated into their own store and a code-random test set, which "
          "the pipeline cannot build yet",
    "A8": "not run: it needs a store holding the v1 fit runs 0 to 23 as one extra training scenario, which store v2 "
          "cannot build yet",
    "A9": "not run: the gradient-boosted reference is first on the cut list (spec §18)"}
METRICS = ("median_warning_all_breaches_min", "capped_mean_warning_min", "catch_rate", "realized_alerts_per_shift",
           "missed_loss_rate")


class AblationError(RuntimeError):
    """A job that cannot be written or run as asked."""


def paths(root: Path = ABLATION_DIR) -> dict:
    root = Path(root)
    return {"root": root, "jobs": root / "jobs", "index": root / "jobs.json", "runs": root / "runs",
            "runs_csv": root / "runs.csv", "arms": root / "arms", "a1": root / "a1-replicates.json"}


def _salted(ids, salt: str) -> list[str]:
    return sorted(ids, key=lambda s: hashlib.sha256(f"{salt}:{s}".encode()).hexdigest())


def _write(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).is_file() else None


def train_scenarios(store_dir: Path) -> list[str]:
    con = store.open_observed_v2(store_dir)
    try:
        return sorted(r[0] for r in con.execute("select distinct scenario_id from runs where split = 'train'").fetchall())
    finally:
        con.close()


# --- The arms --------------------------------------------------------------------------------------------------------

def step_plan(config: dict) -> dict:
    """The gradient steps every trained arm takes, and the overrides that hold it to them."""
    a, tcfg = config["ablations"], T.training_config(config)
    batch = int(tcfg["batch"])
    steps = int(math.ceil(int(a["step_samples"]) / batch))
    per_epoch = int(math.ceil(steps / int(a["epochs"])))
    return {"steps": steps, "batch": batch,
            "overrides": {"max_steps": steps, "epoch_samples": per_epoch * batch, "max_epochs": 10 ** 6,
                          "patience": 10 ** 6}}


def a6_cuts(config: dict) -> dict:
    """A6's arms as train.mask_inputs cuts, in spec §7.4's groups."""
    a = config["ablations"]
    aggregates = [name for name, group, _ in F.NUM_COLUMNS if group in a["aggregate_groups"]]
    roster = list(a["roster_columns"])
    unknown = sorted(set(roster) - set(F.NUM_INDEX))
    if unknown:
        raise AblationError(f"roster columns {unknown} are not feature columns")
    return {"no_context": {"num": aggregates + roster, "branches": ["fingerprint", "series", "tokens"]},
            "no_fingerprint": {"branches": ["fingerprint"]}, "no_roster": {"num": roster},
            "no_aggregates": {"num": aggregates}}


def a7_losses(config: dict) -> dict:
    """A7's arms: the distribution NLL alone, then the p_miss cross-entropy, then the ranking term (each added at
    the configured weight, or at the spec's default when the configuration has it at zero)."""
    tcfg = T.training_config(config)
    lam = float(tcfg["lam"]) or 1.0
    eta = float(tcfg["eta"]) or 0.05
    return {"nll_only": {"lam": 0.0, "eta": 0.0}, "plus_cross_entropy": {"lam": lam, "eta": 0.0},
            "plus_ranking": {"lam": lam, "eta": eta}}


def a1_design(scenarios: list[str], config: dict) -> dict:
    """The nested train scenario sets of A1 and the replicates each needs: {arm: (scenarios, replicates)} and
    {scenario: the highest replicate it needs}."""
    a = config["ablations"]["a1"]
    designs = [(int(n), int(r)) for n, r in a["designs"]]
    need = max(n for n, _ in designs)
    if len(scenarios) < need:
        raise AblationError(f"A1 needs {need} train scenarios and the store has {len(scenarios)}")
    order = _salted(scenarios, a["salt"])
    arms, top = {}, {}
    for n, r in designs:
        chosen = sorted(order[:n])
        arms[f"{n}x{r}"] = (chosen, list(range(1, r + 1)))
        for s in chosen:
            top[s] = max(top.get(s, 0), r)
    return {"arms": arms, "replicates": dict(sorted(top.items()))}


def a2_design(scenarios: list[str], cells: dict, config: dict) -> dict:
    """A2's two arms: every train scenario but the edge stratum, and every train scenario with as many normal
    scenarios left out as there are edge ones (lowest salted hash first), so both arms are the same size."""
    known = {s: c for s, c in cells.items() if s in set(scenarios)}
    missing = sorted(set(scenarios) - set(known))
    if missing:
        raise AblationError(f"the scenario cells lack {len(missing)} train scenarios ({missing[:3]})")
    edge = sorted(s for s, c in known.items() if c["stratum"] == "edge")
    normal = _salted([s for s, c in known.items() if c["stratum"] == "normal"], config["ablations"]["a2_salt"])
    if len(normal) < len(edge):
        raise AblationError(f"A2 needs at least as many normal train scenarios ({len(normal)}) as edge ones ({len(edge)})")
    left_out = set(normal[:len(edge)])
    return {"with_edge": sorted(s for s in scenarios if s not in left_out),
            "without_edge": sorted(s for s in scenarios if s not in set(edge)), "edge": len(edge)}


def check_cells(cells: dict) -> dict:
    """The scenario cells file holds train and validation scenarios only: ablation data choices never read a test
    scenario's description."""
    other = sorted({c.get("split") for c in cells.values()} - {"train", "val"})
    if other:
        raise AblationError(f"the scenario cells hold {other} scenarios; they must hold train and val only")
    return cells


def make_jobs(set_name: str, *, config: dict, rung: str, store_dir: Path, eval_store: Path, train_ids: list[str],
              cells: dict | None, a1_store: Path, evaluate=("val", "test")) -> tuple[list[dict], dict, dict | None]:
    """Every job of a set, the ablations recorded as not run (with why), and A1's replicate plan."""
    if set_name not in SETS:
        raise AblationError(f"unknown set {set_name!r}; one of {sorted(SETS)}")
    a = config["ablations"]
    plan = step_plan(config)
    core, optional = [int(s) for s in a["seeds_core"]], [int(s) for s in a["seeds_optional"]]
    scoring = {"evaluate": list(evaluate), "eval_store": str(eval_store), "budgets_per_shift": list(a["budgets_per_shift"]),
               "budgets_per_100": list(a["budgets_per_100"]), "resamples": int(a["resamples"])}
    jobs, not_run, a1_plan = [], {}, None

    def add(ablation, arm, seeds, *, job_rung=rung, train_store=store_dir, overrides=None):
        for seed in seeds:
            jid = f"{ablation}-{arm}-s{seed}"
            jobs.append({"id": jid, "ablation": ablation, "arm": arm, "seed": seed, "rung": job_rung,
                         "train": {"store": str(train_store), "overrides": plan["overrides"] | (overrides or {})},
                         "members": [jid], **scoring})

    for ablation in SETS[set_name]:
        try:
            if ablation == "A1":
                design = a1_design(train_ids, config)
                a1_plan = {"store": str(a1_store), "replicates": design["replicates"],
                           "note": "simulate each listed train scenario's replicates 0 to the number given, and every "
                                   "validation scenario as the main store has it, into the A1 store"}
                for arm, (ids, reps) in design["arms"].items():
                    add("A1", arm, core, train_store=a1_store,
                        overrides={"train_subset": {"scenarios": ids, "replicates": reps}})
            elif ablation == "A2":
                if cells is None:
                    raise AblationError("it needs the scenario cells (data/neural/scenario-cells.json), which the "
                                        "evaluation side writes")
                design = a2_design(train_ids, cells, config)
                for arm in ("with_edge", "without_edge"):
                    add("A2", arm, core, overrides={"train_subset": {"scenarios": design[arm]}})
            elif ablation == "A5":
                for r in ("L", "A", "B", "C"):
                    add("A5", r, core, job_rung=r)
                jobs.append({"id": "A5-ensemble", "ablation": "A5", "arm": "ensemble", "seed": None, "rung": rung,
                             "train": None, "members": [f"A5-{rung}-s{s}" for s in core], **scoring})
            elif ablation == "A6":
                for arm, cut in a6_cuts(config).items():
                    add("A6", arm, core, overrides={"input_mask": cut})
            elif ablation == "A7":
                for arm, loss in a7_losses(config).items():
                    add("A7", arm, optional, overrides=loss)
        except AblationError as e:
            not_run[ablation] = f"blocked: {e}"
    for ablation in QUESTIONS:
        if ablation not in SETS[set_name] and ablation not in not_run:
            not_run[ablation] = NOT_BUILT.get(ablation, f"not run: not in the {set_name} set")
    return jobs, not_run, a1_plan


def write_specs(set_name: str = "core", *, store_dir: Path | None = None, eval_store: Path | None = None,
                rung: str | None = None, cells_path: Path | None = None, config_path: Path = T.CONFIG_PATH,
                root: Path = ABLATION_DIR, evaluate=("val", "test"), out=print) -> dict:
    """`ablate --set`: write every job specification and the index."""
    config = T.load_config(config_path)
    store_dir = Path(store_dir or store.STORE_V2_DIR)
    eval_store = Path(eval_store or store_dir)
    if rung is None:
        sel = _read(SELECTION_PATH) if not T.is_dev_store(store_dir) else None
        rung = (sel or {}).get("chosen")
        if rung is None:
            raise AblationError("no chosen rung: pass --rung, or run `select` first (results/neural/selection.json)")
    cells = _read(Path(cells_path or CELLS_PATH))
    a1_store = ROOT / config["ablations"]["a1"]["store"]
    jobs, not_run, a1_plan = make_jobs(set_name, config=config, rung=rung, store_dir=store_dir, eval_store=eval_store,
                                       train_ids=train_scenarios(store_dir),
                                       cells=check_cells(cells) if cells is not None else None,
                                       a1_store=a1_store, evaluate=evaluate)
    p = paths(root)
    for job in jobs:
        job["command"] = f"python -m src.neural.pipeline ablate --run {job['id']}"
        _write(p["jobs"] / f"{job['id']}.json", job)
    if a1_plan is not None:
        _write(p["a1"], a1_plan)
    plan = step_plan(config)
    index = {"set": set_name, "rung": rung, "steps": plan["steps"], "batch": plan["batch"], "store": str(store_dir),
             "development": T.is_dev_store(store_dir),
             "time_box_h": config["ablations"]["time_box_h"], "jobs": [j["id"] for j in jobs], "not_run": not_run,
             "a1_replicates": str(p["a1"]) if a1_plan is not None else None,
             "order": "train jobs in any order; A5-ensemble after its three members"}
    _write(p["index"], index)
    out(f"ablate --set {set_name}: {len(jobs)} jobs at {plan['steps']:,} steps each (batch {plan['batch']}), rung "
        f"{rung}, in {p['jobs']}")
    for ablation, why in sorted(not_run.items()):
        out(f"  {ablation} {why}")
    return index


# --- One arm ---------------------------------------------------------------------------------------------------------

def arm_method(risk_glob: Path):
    from src.pipeline import neural_eval as ne

    return ne.RiskMethod(ARM_METHOD, ne.FrameRisk(risk_glob), monotone=False, fitted_on="train (an ablation arm)")


def summarize(result: dict) -> dict:
    """An arm's evaluation as flat numbers: the alert metrics at each budget at 3-2-1 weights (suffixed with the
    budget unless it is the headline), Brier at 30 and 60 minutes, pooled AUROC, and the edge stratum's slice."""
    out = {}
    head = result["settings"]["headline"]
    for b, by_m in (result.get("operating_points") or {}).items():
        op = (by_m.get(ARM_METHOD) or {}).get("3-2-1") or {}
        for k in METRICS:
            if k in op:
                out[k if b == head else f"{k}@{b}"] = op[k]["point"]
    cal = (result.get("calibration") or {}).get(ARM_METHOD) or {}
    for L in ("30", "60"):
        out[f"brier_{L}"] = (cal.get(L) or {}).get("brier")
    out["auroc_pooled"] = (((result.get("discrimination") or {}).get(ARM_METHOD) or {}).get("pooled") or {}).get("auroc")
    edge = (((result.get("slices") or {}).get("stratum") or {}).get("edge") or {}).get(ARM_METHOD, {}).get("3-2-1") or {}
    for k in ("median_warning_all_breaches_min", "capped_mean_warning_min"):
        out[f"edge_{k}"] = edge.get(k)
    return out


def _member_store(job: dict, p: dict) -> Path:
    first = _read(p["jobs"] / f"{job['members'][0]}.json") if job["train"] is None else job
    return Path(first["train"]["store"])


def run_arm(job_id: str, *, stage: str = "all", device: str = "auto", workers: int | None = None,
            train_store: Path | None = None, eval_store: Path | None = None, relock_reason: str | None = None,
            root: Path = ABLATION_DIR, config_path: Path = T.CONFIG_PATH, main_lock: Path | None = None,
            test_runs: Path = TEST_RUNS_PATH, out=print) -> dict:
    """`ablate --run ID`: train the arm (stage train), then build its artifact and score it on validation and,
    after the main lock, on test (stage score). Idempotent: a finished run is not retrained and a scored set is not
    rescored unless its artifact changed. Writes the arm's result.json."""
    from src.neural import ensemble
    from src.pipeline import neural_eval as ne

    p = paths(root)
    job = _read(p["jobs"] / f"{job_id}.json")
    if job is None:
        raise AblationError(f"no job {job_id} in {p['jobs']}; run `ablate --set` first")
    config = T.load_config(config_path)
    arm_dir = p["arms"] / job_id
    result = _read(arm_dir / "result.json") or {k: job[k] for k in ("id", "ablation", "arm", "seed", "rung")}
    if stage in ("train", "all") and job["train"] is not None:
        store_dir = Path(train_store or job["train"]["store"])
        if not (store_dir / store.OBSERVED_NAME).is_file():
            raise AblationError(f"no observed store at {store_dir}" + (" (A1 needs its replicates simulated first, see "
                                                                        "a1-replicates.json)" if job["ablation"] == "A1" else ""))
        t0 = time.monotonic()
        r = T.train(config, rung=job["rung"], seed=int(job["seed"]), store_dir=store_dir, out_dir=p["runs"],
                    run_id=job_id, resume=True, runs_csv=p["runs_csv"], overrides=job["train"]["overrides"],
                    workers=workers, device=device, log=out)
        result["train"] = {"run_id": r.run_id, "status": r.status, "steps": r.steps, "samples": r.samples,
                           "best_val_sel": r.best_val_sel, "best_epoch": r.best_epoch, "epochs": r.epochs,
                           "device": r.device, "wall_s": r.wall_s, "store_hash": r.store.get("store_hash"),
                           "call_s": round(time.monotonic() - t0, 1)}
        _write(arm_dir / "result.json", result)
    if stage in ("score", "all"):
        members = [p["runs"] / m for m in job["members"]]
        unfinished = [m.name for m in members if (T.load_result(m) if (m / "result.json").is_file() else {}).get("status")
                      not in ("finished", "stopped_early", "timed_out")]
        if unfinished:
            raise AblationError(f"{job_id} needs finished runs first: {unfinished}")
        eval_dir = Path(eval_store or job["eval_store"])
        artifact = arm_dir / "artifact"
        t0 = time.monotonic()
        if not (artifact / "manifest.json").is_file():
            manifest = ensemble.build(config, members, store_dir=Path(train_store or _member_store(job, p)),
                                      out_dir=artifact, workers=workers, device=device, log=out)
        else:
            manifest = _read(artifact / "manifest.json")
        sha = ne.sha256_file(artifact / "manifest.json")
        if (result.get("artifact") or {}).get("manifest_sha256") != sha:   # a new artifact: every set is scored again
            result = {k: v for k, v in result.items() if k not in job["evaluate"]}
            result["artifact"] = {"path": str(artifact), "manifest_sha256": sha,
                                  "ensemble_val_sel": manifest["validation"]["ensemble_val_sel"],
                                  "build_s": round(time.monotonic() - t0, 1)}
        budgets, per_100, resamples = job["budgets_per_shift"], job["budgets_per_100"], int(job["resamples"])
        for split in job["evaluate"]:
            if split in result and relock_reason is None:
                continue                      # scored with this artifact already: no second look
            t0 = time.monotonic()
            risk_dir = arm_dir / "risk" / split
            method = arm_method(risk_dir / "*.parquet")
            test_run = None
            if split in ne.LOCKED_SPLITS:
                lock = Path(main_lock or ne.LOCK_PATH)
                if not lock.is_file():
                    raise AblationError(f"ablations score {split} only after the main test lock exists ({lock}): spec §8.7")
                hashes = ne.lock_record(eval_dir, [method], artifact_dir=artifact, budgets=budgets,
                                        per_100_budgets=per_100, resamples=resamples)
                test_run = ne.guard_test_run(split, hashes, relock_reason, lock_path=arm_dir / f"{split}_lock.json",
                                             runs_path=test_runs)
            ne.risk_table(split, artifact, store_dir=eval_dir, out_dir=risk_dir, workers=workers, test_run=test_run)
            ev = ne.evaluate_split(split, [method], store_dir=eval_dir, budgets=budgets, per_100_budgets=per_100,
                                   resamples=resamples, out_dir=arm_dir / "eval" / split, workers=workers,
                                   test_run=test_run)
            result[split] = summarize(ev) | {"evaluation": ev["written"], "score_s": round(time.monotonic() - t0, 1)}
            _write(arm_dir / "result.json", result)
    out(f"ablate {job_id}: " + ", ".join(k for k in ("train", "artifact", "val", "test") if k in result))
    return result


# --- Collecting ------------------------------------------------------------------------------------------------------

def _stats(values: list) -> dict:
    v = [float(x) for x in values if x is not None]
    if not v:
        return {"mean": None, "min": None, "max": None, "n": 0}
    return {"mean": float(np.mean(v)), "min": min(v), "max": max(v), "n": len(v)}


def _seconds(r: dict) -> float:
    return float((r.get("train") or {}).get("call_s") or 0) + float((r.get("artifact") or {}).get("build_s") or 0) + \
        sum(float((r.get(s) or {}).get("score_s") or 0) for s in ("val", "test"))


def control_lead(control: dict | None, margin: float) -> dict | None:
    """The negative control of A6: TracerNet minus the local analytic refit on control_v1world at its headline budget,
    3-2-1 weights, and whether it is large (at least `margin` minutes with the interval's lower end above zero)."""
    if control is None:
        return None
    head = control["settings"]["headline"]
    d = (((control.get("differences") or {}).get(head) or {}).get("3-2-1") or {}).get("analytic_local")
    if d is None:
        return None
    out = {"budget": head, "margin_min": margin}
    for k in ("median_warning_all_breaches_min", "capped_mean_warning_min"):
        x = d.get(k) or {}
        out[k] = {"point": x.get("point"), "low": x.get("low"), "high": x.get("high"),
                  "large": x.get("point") is not None and x["point"] >= margin and (x.get("low") or 0) > 0}
    out["leakage_suspected"] = any(out[k]["large"] for k in ("median_warning_all_breaches_min", "capped_mean_warning_min"))
    return out


def collect(*, root: Path = ABLATION_DIR, out_path: Path | None = None, control_path: Path = CONTROL_PATH,
            config_path: Path = T.CONFIG_PATH, out=print) -> dict:
    """`ablate --collect`: every arm's seeds summarized (mean, min, max), the steps check, the compute against the
    time box, the negative control, and every ablation not run with its reason, into results/neural/ablations.json
    (jobs written for a development store: data/neural/ablations/ablations-dev.json)."""
    p = paths(root)
    index = _read(p["index"])
    if index is None:
        raise AblationError(f"no {p['index']}; run `ablate --set` first")
    out_path = Path(out_path or (p["root"] / "ablations-dev.json" if index.get("development") else RESULT_PATH))
    config = T.load_config(config_path)
    jobs = [_read(p["jobs"] / f"{j}.json") for j in index["jobs"]]
    results = {j["id"]: _read(p["arms"] / j["id"] / "result.json") or {} for j in jobs}
    ablations: dict = {}
    for a in QUESTIONS:
        mine = [j for j in jobs if j["ablation"] == a]
        entry = {"question": QUESTIONS[a], "required": a in CORE}
        if not mine:
            entry |= {"status": "not run", "reason": index["not_run"].get(a, "not in this set")}
            ablations[a] = entry
            continue
        arms: dict = {}
        for j in mine:
            arms.setdefault(j["arm"], []).append(results[j["id"]])
        entry["arms"] = {}
        done = 0
        for arm, rs in arms.items():
            finished = [r for r in rs if all(s in r for s in ("val", "test"))]
            done += len(finished)
            val_sel = [(r.get("train") or {}).get("best_val_sel") if r.get("train") else
                       (r.get("artifact") or {}).get("ensemble_val_sel") for r in rs]
            keys = sorted({k for r in rs for s in ("val", "test") for k in (r.get(s) or {})
                           if k not in ("evaluation", "score_s")})
            entry["arms"][arm] = {"seeds": len(rs), "seeds_done": len(finished),
                                  "val": {"val_sel": _stats(val_sel)} | {k: _stats([(r.get("val") or {}).get(k) for r in rs])
                                                                         for k in keys},
                                  "test": {k: _stats([(r.get("test") or {}).get(k) for r in rs]) for k in keys},
                                  "steps": sorted({(r.get("train") or {}).get("steps") for r in rs if r.get("train")})}
        entry["status"] = "done" if done == len(mine) else ("partial" if any(results[j["id"]] for j in mine) else "not run")
        if entry["status"] != "done":
            entry["reason"] = (f"{done} of {len(mine)} jobs finished and scored on validation and test"
                               if entry["status"] == "partial" else "no job has run yet")
        ablations[a] = entry
    a6 = ablations.get("A6") or {}
    full = ((ablations.get("A5") or {}).get("arms") or {}).get(index["rung"])
    if a6.get("arms") is not None and full is not None:
        a6["arms"] = {"full": full} | a6["arms"]
        a6.setdefault("notes", []).append(f"The full arm is A5's rung-{index['rung']} arm, trained identically.")
    margin = float(config["ablations"]["leakage_lead_min"])
    control = control_lead(_read(control_path), margin)
    if "A6" in ablations:
        ablations["A6"]["negative_control"] = control
        ablations["A6"].setdefault("notes", []).append(
            "Negative control: " + ("not run yet (no control_v1world evaluation with TracerNet)." if control is None else
                                    f"TracerNet's lead over the local analytic refit on control_v1world at {control['budget']}"
                                    f" is {control['median_warning_all_breaches_min']['point']} min (median, all breaches) "
                                    f"and {control['capped_mean_warning_min']['point']} min (capped mean); "
                                    + ("large: treated as leakage until shown otherwise." if control["leakage_suspected"]
                                       else "small.")))
    steps = sorted({(r.get("train") or {}).get("steps") for r in results.values() if r.get("train")})
    hours = sum(_seconds(r) for r in results.values()) / 3600
    doc = {"set": index["set"], "rung": index["rung"], "steps": steps[0] if len(steps) == 1 else index["steps"],
           "steps_seen": steps, "steps_equal": len(steps) <= 1, "time_box_h": index["time_box_h"],
           "compute": {"hours": hours, "over_box": hours > float(index["time_box_h"])}, "ablations": ablations,
           "notice": "Synthetic data, no clinical review. Ablation numbers are descriptive; test was scored after the "
                     "lock and never changes the headline model."}
    _write(out_path, doc)
    out(f"ablations: {sum(1 for a in ablations.values() if a.get('status') == 'done')} of {len(ablations)} done, "
        f"{hours:.2f} hours of {index['time_box_h']} -> {out_path}")
    return doc


def ablate_command(*, set_name: str | None = None, run: str | None = None, collect_results: bool = False,
                   stage: str = "all", store_dir: Path | None = None, eval_store: Path | None = None,
                   rung: str | None = None, cells_path: Path | None = None, device: str = "auto",
                   workers: int | None = None, relock_reason: str | None = None, evaluate: str = "val,test",
                   out=print) -> int:
    try:
        if run is not None:
            run_arm(run, stage=stage, device=device, workers=workers, train_store=store_dir, eval_store=eval_store,
                    relock_reason=relock_reason, out=out)
        elif collect_results:
            collect(out=out)
        else:
            write_specs(set_name or "core", store_dir=store_dir, eval_store=eval_store, rung=rung,
                        cells_path=cells_path, evaluate=tuple(s for s in evaluate.split(",") if s), out=out)
    except AblationError as e:
        out(f"error: {e}")
        return 1
    return 0
