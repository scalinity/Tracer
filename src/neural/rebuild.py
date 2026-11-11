"""`run --from-frozen`, the single command of docs/neural/spec.md §12.1 and §17 item 18: everything rebuilt from the
frozen scenario library, with no API call, into a scratch folder, and compared with what the repository commits.

    python -m src.neural.pipeline run --from-frozen --out SCRATCH [--workers N] [--device auto|cpu|cuda]
    python -m src.neural.pipeline run --from-frozen --evaluate-committed --out SCRATCH
        --reuse-data DIR        DIR/sim and DIR/store-v2: a simulation and store already rebuilt, read and never written
        --reference-store DIR   the committed store, compared table by table
        --reference-sim PATH    the committed simulation's manifest.json (or its folder), compared file by file
        --until STAGE           stop after simulate, store, features, train, calibrate or evaluate
        --seeds 0,1             with --until train: only these seeds, for training jobs run side by side

Retrain (`run --from-frozen`). Simulate the frozen library into SCRATCH/sim and load it into SCRATCH/store-v2; fit the
feature schema; train every seed of the committed artifact's rung with the training settings its config.yaml records
(the rung, the seeds, the searched values and the epoch plan's settings, so the run configuration hash is the
committed one); build and calibrate a scratch artifact in SCRATCH/artifact; score validation with it and evaluate
validation. Then compare: the configuration and feature schema exactly, and the metrics of GATED within 0.5%
relative. REPORTED metrics are listed without a gate, for the reason UNGATED gives.

Evaluate the committed artifact (`--evaluate-committed`). Rebuild the same data, score validation with
artifacts/tracernet/v1, evaluate validation, and compare the evaluation with results/neural/val/evaluation.json on
every field except the three of EXCLUDED, which name the store's path and its manifest (whose DuckDB file hashes
differ by build) and the run's timing: with those three taken from the committed file, the rebuilt file must be the
committed file byte for byte. The test sets are not re-evaluated: the test lock now holds the decision layer's look.
In both modes validation is evaluated as the committed evaluation was (`evaluate --split val --matched`, read from
its matched-volume section).

Both modes check the rebuilt data first: the simulation manifest's SHA-256 against the one the committed artifact
records (the manifest lists every file's SHA-256), the store's input hashes against the artifact's, and, when given,
the simulation file by file against --reference-sim and every table of both store files against --reference-store
(store.table_digests: row counts, columns and order-free row hashes, which read every row of every split for a hash
and compute no statistic).

Neither mode reads a test set for a model or a metric, the test lock or any API: training reads the train and val
splits, the risk table and the evaluation are validation's, and nothing here opens a connection. Each step leaves a
marker in SCRATCH/stages (src/neural/stages.py) and is reused while its inputs and outputs are unchanged, so a run
resumes where it stopped; an interrupted training run resumes from its last checkpoint. Nothing is written outside
SCRATCH, and a SCRATCH that is, holds or sits inside the committed artifact, results or datasets, the working store or
simulation, or a folder this run reads is refused before any step. The result, with each step's times and output
hashes and each comparison's numbers, goes to SCRATCH/run-from-frozen.json (or run-evaluate-committed.json).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import yaml

from src import workers as workers_
from src.generator import config as policy_config
from src.neural import ensemble, samplers as S, stages, train as T
from src.pipeline import neural_eval as ne, store

ROOT = stages.ROOT
COMMITTED_ARTIFACT = ensemble.ARTIFACT_DIR
COMMITTED_EVALUATION = ROOT / "results" / "neural" / "val" / "evaluation.json"
TOLERANCE = 0.005                     # spec §12.1 and §17 item 18: the committed validation metrics within 0.5%
EXCLUDED = (("store", "path"), ("store", "manifest_sha256"), ("timing",))
STAGES = {"retrain": ("simulate", "store", "features", "train", "calibrate", "evaluate"),
          "committed": ("simulate", "store", "evaluate")}
RESULT_NAMES = {"retrain": "run-from-frozen.json", "committed": "run-evaluate-committed.json"}
SOURCES = {"manifest": "artifact manifest", "evaluation": "validation evaluation"}
HEADLINE = ("operating_points", "6/shift", "tracernet", "3-2-1")
# The metrics a retrained artifact must match within TOLERANCE: the spec's val_sel, and the validation evaluation's
# whole-set summaries of TracerNet's probabilities (discrimination over every tick, calibration at 30 and 60 min).
GATED = (("val_sel of the ensemble", "manifest", ("validation", "ensemble_val_sel")),
         ("pooled AUROC", "evaluation", ("discrimination", "tracernet", "pooled", "auroc")),
         ("pooled AUPRC", "evaluation", ("discrimination", "tracernet", "pooled", "auprc")),
         ("Brier at 30 min", "evaluation", ("calibration", "tracernet", "30", "brier")),
         ("Brier at 60 min", "evaluation", ("calibration", "tracernet", "60", "brier")))
REPORTED = (("catch rate at 6 per shift (3-2-1)", "evaluation", HEADLINE + ("catch_rate", "point")),
            ("median warning at 6 per shift (3-2-1), min", "evaluation", HEADLINE + ("median_warning_min", "point")))
UNGATED = ("Each member's val_sel follows its own early stopping, and its epochs are sized from the throughput the "
           "machine measures, so one seed can stop at another epoch; an operating point is a count at a knob tuned "
           "per scenario on 5-minute ticks (the median warning moves in 5-minute steps). These are reported with "
           "their differences, not gated.")
STORE_INPUTS = ("policy_hash", "library_sha256", "splits_sha256", "sim_manifest_sha256")
TRAINED = ("finished", "stopped_early", "timed_out")


class RebuildError(RuntimeError):
    pass


# --- Comparisons (pure: documents in, one record out) ------------------------------------------------------------

def _get(doc, path):
    for key in path:
        if not isinstance(doc, dict) or key not in doc:
            return None
        doc = doc[key]
    return doc


def _short(value) -> str:
    text = json.dumps(value, default=str)
    return text if len(text) <= 200 else text[:200] + "..."


def diff_paths(a, b, at: str = "") -> list[tuple[str, object, object]]:
    """Every place two JSON documents differ, as (path, a's value, b's value): a key on one side only, a list of
    another length, or two leaves that differ in value or type (1 and 1.0 differ, as their JSON text does)."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for key in list(a) + [k for k in b if k not in a]:
            p = f"{at}.{key}" if at else str(key)
            out += diff_paths(a[key], b[key], p) if key in a and key in b else [(p, a.get(key), b.get(key))]
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in diff_paths(x, y, f"{at}[{i}]")]
    return [] if type(a) is type(b) and a == b else [(at or "(root)", a, b)]


def _differences(found: list, limit: int = 20) -> list[dict]:
    return [{"path": p, "committed": _short(x), "rebuilt": _short(y)} for p, x, y in found[:limit]]


def compare_evaluation(rebuilt_raw: bytes, committed_raw: bytes) -> dict:
    """The rebuilt validation evaluation against the committed one: every field but EXCLUDED must be equal, and with
    the excluded fields taken from the committed file, the rebuilt one written as evaluate_split writes it must be the
    committed file byte for byte (which also holds key order and number formatting)."""
    rebuilt, committed = json.loads(rebuilt_raw), json.loads(committed_raw)
    a, b = copy.deepcopy(committed), copy.deepcopy(rebuilt)
    substituted = copy.deepcopy(rebuilt)
    for *head, last in EXCLUDED:
        for doc in (a, b):
            node = _get(doc, head)
            if isinstance(node, dict):
                node.pop(last, None)
        target, source = _get(substituted, head), _get(committed, head)
        if isinstance(target, dict) and isinstance(source, dict):
            if last in source:
                target[last] = source[last]
            else:
                target.pop(last, None)
    found = diff_paths(a, b)
    same_bytes = (json.dumps(substituted, indent=1, allow_nan=False) + "\n").encode() == committed_raw
    return {"name": "validation evaluation", "kind": "exact", "excluded": [".".join(p) for p in EXCLUDED],
            "excluded_values": {".".join(p): {"committed": _get(committed, p), "rebuilt": _get(rebuilt, p)}
                                for p in EXCLUDED},
            "differences": len(found), "first_differences": _differences(found),
            "bytes_equal_after_substitution": same_bytes, "passed": not found and same_bytes}


def metric(name: str, source: str, path: str, committed, rebuilt, *, gated: bool = True) -> dict:
    """One metric: its relative difference, and against TOLERANCE when gated. Two missing values are equal; a value
    missing on one side, or a committed zero against a rebuilt non-zero, has no relative difference and fails."""
    numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (committed, rebuilt))
    if numbers:
        rel = abs(rebuilt - committed) / abs(committed) if committed else (0.0 if rebuilt == committed else None)
    else:
        rel = 0.0 if committed is None and rebuilt is None else None
    return {"name": name, "source": source, "path": path, "committed": committed, "rebuilt": rebuilt,
            "relative_difference": rel, "tolerance": TOLERANCE if gated else None,
            "passed": (rel is not None and rel <= TOLERANCE) if gated else None}


def metric_records(committed: dict, rebuilt: dict, specs, *, gated: bool) -> list[dict]:
    """`specs` (GATED or REPORTED) read from {"manifest": ..., "evaluation": ...} on both sides."""
    return [metric(name, SOURCES[src], ".".join(path), _get(committed[src], path), _get(rebuilt[src], path),
                   gated=gated) for name, src, path in specs]


def member_records(committed_manifest: dict, rebuilt_manifest: dict) -> list[dict]:
    """Each committed member's val_sel against the rebuilt member of its seed (reported, not gated)."""
    rebuilt = {m["seed"]: m["val_sel"] for m in (_get(rebuilt_manifest, ("validation", "members")) or [])}
    return [metric(f"val_sel of seed {m['seed']}", SOURCES["manifest"], f"validation.members[seed {m['seed']}].val_sel",
                   m["val_sel"], rebuilt.get(m["seed"]), gated=False)
            for m in _get(committed_manifest, ("validation", "members")) or []]


def compare_simulation(manifest_raw: bytes, committed_sha256: str, reference_raw: bytes | None = None) -> list[dict]:
    """The rebuilt simulation manifest's SHA-256 against the committed one (the manifest lists every file's SHA-256,
    so equal manifests mean equal files), and with a reference manifest each file's SHA-256 against its own."""
    sha = hashlib.sha256(manifest_raw).hexdigest()
    out = [{"name": "simulation manifest", "kind": "exact", "committed": committed_sha256, "rebuilt": sha,
            "passed": sha == committed_sha256}]
    if reference_raw is not None:
        a = {f["path"]: f["sha256"] for f in json.loads(manifest_raw)["files"]}
        b = {f["path"]: f["sha256"] for f in json.loads(reference_raw)["files"]}
        missing, extra = sorted(set(b) - set(a)), sorted(set(a) - set(b))
        different = sorted(p for p in set(a) & set(b) if a[p] != b[p])
        out.append({"name": "simulation files", "kind": "exact", "files": len(a), "reference_files": len(b),
                    "equal": len(set(a) & set(b)) - len(different), "missing": missing[:20], "extra": extra[:20],
                    "different": different[:20], "passed": bool(b) and not (missing or extra or different)})
    return out


def compare_store_inputs(inputs: dict, committed_manifest: dict) -> dict:
    """The hashes the rebuilt store was loaded under against those the committed artifact records."""
    c = {k: committed_manifest.get(k) for k in STORE_INPUTS}
    r = {k: inputs.get(k) for k in STORE_INPUTS}
    return {"name": "store inputs", "kind": "exact", "committed": c, "rebuilt": r, "passed": c == r}


def compare_tables(rebuilt: dict, reference: dict, *, file_bytes_equal: bool | None = None) -> dict:
    """Two stores' store.table_digests, table by table: equal rows, columns and row hashes. The files' own bytes are
    reported, not compared, since DuckDB's layout varies by build."""
    tables = []
    for f in sorted(set(rebuilt) | set(reference)):
        a, b = rebuilt.get(f) or {}, reference.get(f) or {}
        for t in sorted(set(a) | set(b)):
            tables.append({"file": f, "table": t, "rows": (a.get(t) or {}).get("rows"),
                           "reference_rows": (b.get(t) or {}).get("rows"), "equal": a.get(t) == b.get(t)})
    return {"name": "store tables", "kind": "exact", "tables": tables, "file_bytes_equal": file_bytes_equal,
            "passed": bool(tables) and all(t["equal"] for t in tables)}


def compare_configuration(committed_cfg: dict, rebuilt_cfg: dict) -> dict:
    """The scratch artifact's config.yaml against the committed one: rung, seeds, training settings, the runs'
    configuration hashes (which cover the rung, the settings and the network built from the feature schema) and
    the network."""
    keys = ("rung", "seeds", "training", "training_config_hashes", "net")
    found = diff_paths({k: committed_cfg.get(k) for k in keys}, {k: rebuilt_cfg.get(k) for k in keys})
    shown = ("rung", "seeds", "training_config_hashes")
    return {"name": "training configuration", "kind": "exact", "committed": {k: committed_cfg.get(k) for k in shown},
            "rebuilt": {k: rebuilt_cfg.get(k) for k in shown}, "differences": len(found),
            "first_differences": _differences(found), "passed": not found}


def compare_schema(committed: dict, rebuilt: dict) -> dict:
    """The scratch artifact's feature schema against the committed one, without its `store` record (which holds the
    observed file's byte hash)."""
    found = diff_paths({k: v for k, v in committed.items() if k != "store"},
                       {k: v for k, v in rebuilt.items() if k != "store"})
    return {"name": "feature schema", "kind": "exact", "excluded": ["store"], "differences": len(found),
            "first_differences": _differences(found), "passed": not found}


# --- The run --------------------------------------------------------------------------------------------------------

def _inside(a: Path, b: Path) -> bool:
    return a == b or a.is_relative_to(b)


def _manifest_path(path: Path) -> Path:
    path = Path(path)
    return path / "manifest.json" if path.is_dir() else path


def _output(path: Path) -> dict:
    path = Path(path)
    return {"path": str(path), "sha256": store.sha256_path(path) if path.is_file() else None}


def _write_json(path: Path, doc) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, indent=1, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


class Rebuild:
    """One `run --from-frozen` into `scratch`; check() refuses before anything is written."""

    def __init__(self, scratch: Path, *, evaluate_committed: bool = False, reuse_data: Path | None = None,
                 reference_store: Path | None = None, reference_sim: Path | None = None, until: str | None = None,
                 seeds=None, workers: int | None = None, device: str = "auto", artifact_dir: Path | None = None,
                 committed_evaluation: Path | None = None, config_path: Path | None = None, out=print):
        self.scratch = Path(scratch)
        self.mode = "committed" if evaluate_committed else "retrain"
        self.reuse = Path(reuse_data) if reuse_data is not None else None
        self.reference_store = Path(reference_store) if reference_store is not None else None
        self.reference_sim = _manifest_path(reference_sim) if reference_sim is not None else None
        self.until, self.workers, self.device, self.out = until, workers, device, out
        self.artifact_dir = Path(artifact_dir) if artifact_dir is not None else COMMITTED_ARTIFACT
        self.committed_evaluation = Path(committed_evaluation) if committed_evaluation is not None \
            else COMMITTED_EVALUATION
        self.config = T.load_config(config_path or T.CONFIG_PATH)
        self.seeds_asked = None if seeds is None else [int(s) for s in seeds]
        self.sim_dir = (self.reuse or self.scratch) / "sim"
        self.store_dir = (self.reuse or self.scratch) / "store-v2"
        self.stage_dir = self.scratch / "stages"
        self.features_dir, self.cache = self.scratch / "features", self.scratch / "cache"
        self.runs_dir, self.scratch_artifact = self.scratch / "runs", self.scratch / "artifact"
        self.steps: list[dict] = []
        self._table = None

    # --- refusals, before any write
    def protected(self) -> list[Path]:
        paths = [ROOT / "artifacts", ROOT / "results", ROOT / "datasets", store.STORE_V2_DIR, store.SIM_DIR,
                 self.artifact_dir, self.committed_evaluation.parent]
        if self.reuse is not None:
            paths += [self.sim_dir, self.store_dir]
        if self.reference_store is not None:
            paths.append(self.reference_store)
        if self.reference_sim is not None:
            paths.append(self.reference_sim.parent)
        return [p.resolve() for p in paths]

    def check(self) -> None:
        s = self.scratch.resolve()
        for p in self.protected():
            if _inside(s, p) or _inside(p, s):
                raise RebuildError(f"--out {self.scratch} would write over, inside or around {p}, which this run must "
                                   "not change; choose a scratch folder apart from it")
        if self.until is not None and self.until not in STAGES[self.mode]:
            raise RebuildError(f"--until is one of {', '.join(STAGES[self.mode])} here")
        for path in (self.artifact_dir / "manifest.json", self.artifact_dir / "config.yaml", self.committed_evaluation):
            if not path.is_file():
                raise RebuildError(f"the committed file {path} is missing")
        self.committed = json.loads((self.artifact_dir / "manifest.json").read_text(encoding="utf-8"))
        self.committed_cfg = yaml.safe_load((self.artifact_dir / "config.yaml").read_text(encoding="utf-8"))
        # The committed evaluation ran `evaluate --split val --matched`. Its matched-volume section is there when the
        # re-solve was requested or R4 failed, and matched=True reproduces it either way; without one, matched=False.
        self.matched = "matched_volume" in json.loads(self.committed_evaluation.read_text(encoding="utf-8"))
        try:
            store.verify_files(self.artifact_dir, self.committed.get("files") or {}, "artifact")
        except RuntimeError as e:
            raise RebuildError(str(e)) from None
        policy = policy_config.load()["policy_hash"]
        if self.committed.get("policy_hash") != policy:
            raise RebuildError(f"the committed artifact was trained under policy {self.committed.get('policy_hash')}, "
                               f"the repository's policy is {policy}: a rebuild cannot match it")
        self.policy_hash = policy
        self.rung, self.seeds = self.committed_cfg["rung"], [int(s) for s in self.committed_cfg["seeds"]]
        if self.seeds_asked is not None:
            if self.mode != "retrain" or self.until != "train":
                raise RebuildError("--seeds trains some seeds only, for jobs run side by side; it needs --until train")
            unknown = sorted(set(self.seeds_asked) - set(self.seeds))
            if unknown:
                raise RebuildError(f"seeds {unknown} are not the committed artifact's seeds {self.seeds}")
        for need in ([self.sim_dir / "manifest.json", self.store_dir / store.STORE_V2_MANIFEST] if self.reuse else []) \
                + ([self.reference_sim] if self.reference_sim else []) \
                + ([self.reference_store / store.OBSERVED_NAME] if self.reference_store else []):
            if not need.is_file():
                raise RebuildError(f"{need} is missing")

    # --- steps
    def step(self, name: str, inputs: dict, fn) -> None:
        ran, _, reason = stages.run_stage(self.stage_dir, name, inputs, fn)
        m = stages.Stage(self.stage_dir, name).marker()
        seconds = (datetime.fromisoformat(m["finished_at"]) - datetime.fromisoformat(m["started_at"])).total_seconds()
        self.steps.append({"name": name, "status": f"ran: {reason}" if ran else "reused",
                           "started_at": m["started_at"], "finished_at": m["finished_at"], "seconds": seconds,
                           "outputs": {k: _output(v["path"]) for k, v in m["outputs"].items()}})
        self.out(f"{name}: {self.steps[-1]['status']} ({seconds:.0f} s)")

    def store_key(self) -> str:
        return stages.file_hash(self.store_dir / store.STORE_V2_MANIFEST)

    def table(self):
        if self._table is None:
            self._table = T.load_train_table(self.store_dir)
        return self._table

    def simulate(self) -> None:
        if self.reuse is not None:
            self.steps.append({"name": "simulate", "status": f"reused from {self.reuse}",
                               "outputs": {"manifest": _output(self.sim_dir / "manifest.json")}})
            return

        def fn():
            from src.scenarios import freeze
            freeze.simulate(self.workers or max(1, workers_.available_cores() - 1), self.sim_dir, self.out)
            return {"manifest": self.sim_dir / "manifest.json"}, None
        self.step("simulate", {"splits": stages.file_hash(store.SPLITS_V2), "policy": self.policy_hash}, fn)

    def build_store(self) -> None:
        if self.reuse is not None:
            self.steps.append({"name": "store", "status": f"reused from {self.reuse}",
                               "outputs": {"manifest": _output(self.store_dir / store.STORE_V2_MANIFEST)}})
            return

        def fn():
            store.command_build_v2(workers=self.workers, sim_dir=self.sim_dir, store_dir=self.store_dir, out=self.out)
            return {"manifest": self.store_dir / store.STORE_V2_MANIFEST}, None
        self.step("store", {"simulation": stages.file_hash(self.sim_dir / "manifest.json"),
                            "splits": stages.file_hash(store.SPLITS_V2), "policy": self.policy_hash}, fn)

    @property
    def training(self) -> dict:
        return dict(self.committed_cfg["training"])

    def features(self) -> None:
        """The feature schema and the shared table cache, made once, so training jobs run side by side only read
        them."""
        def fn():
            workers = self.workers or self.training.get("workers") or \
                T.default_workers("cpu" if self.device == "cpu" else "mps")
            T.feature_schema(self.table(), T.store_info(self.store_dir), self.training, cache_root=self.features_dir,
                             workers=workers, table_cache=self.cache)
            return {"table": S.cached_table_dir(self.table(), self.cache) / "table.json"}, None
        self.step("features", {"store": self.store_key(), "training": stages.obj_hash(self.training)}, fn)

    def train(self, seed: int) -> Path:
        """One seed with the committed settings exactly (no searched values laid over them), resumed from its
        checkpoint when an earlier attempt stopped."""
        run_id = f"{self.rung}-s{seed}-rebuild"
        run_dir = self.runs_dir / run_id
        config = {**self.config, "training": self.training, "search": {"best": {}}}

        def fn():
            r = T.train(config, rung=self.rung, seed=seed, store_dir=self.store_dir, out_dir=self.runs_dir,
                        device=self.device, run_id=run_id, resume=(run_dir / "last.pt").exists(),
                        runs_csv=self.runs_dir / f"{run_id}.csv", workers=self.workers, table=self.table(),
                        cache_root=self.cache, features_root=self.features_dir, log=self.out)
            if r.status not in TRAINED:
                raise RebuildError(f"seed {seed} ended {r.status}")
            return {"result": run_dir / "result.json", "best": run_dir / "best.pt"}, None
        self.step(f"train-s{seed}", {"store": self.store_key(), "training": stages.obj_hash(self.training),
                                     "rung": self.rung, "seed": seed}, fn)
        return run_dir

    def calibrate(self, run_dirs: list[Path]) -> None:
        def fn():
            ensemble.build(self.config, run_dirs, store_dir=self.store_dir, out_dir=self.scratch_artifact,
                           selection=self.committed_cfg.get("selection"), table=self.table(), workers=self.workers,
                           cache_root=self.cache, device=self.device, log=self.out)
            return {"manifest": self.scratch_artifact / "manifest.json"}, None
        self.step("calibrate", {"members": {d.name: stages.file_hash(d / "result.json") for d in run_dirs},
                                "ensemble": stages.obj_hash(self.config["ensemble"])}, fn)

    def evaluate(self, label: str, artifact_dir: Path) -> Path:
        """Validation scored by `artifact_dir` into SCRATCH/risk/<label>/val, then evaluated as `evaluate --split
        val` evaluates it, into SCRATCH/eval/<label>/val/evaluation.json."""
        risk_dir = self.scratch / "risk" / label / "val"
        eval_dir = self.scratch / "eval" / label / "val"

        def risk():
            ne.risk_table("val", artifact_dir, store_dir=self.store_dir, out_dir=risk_dir, workers=self.workers)
            return {p.name: p for p in sorted(risk_dir.glob("*.parquet"))}, None
        self.step(f"risk-{label}", {"artifact": stages.file_hash(artifact_dir / "manifest.json"),
                                    "store": self.store_key()}, risk)

        def evaluate():
            methods = ne.baselines() + [ne.tracernet_method(risk_dir / "*.parquet")]
            ne.evaluate_split("val", methods, store_dir=self.store_dir, out_dir=eval_dir, workers=self.workers,
                              matched=self.matched)
            return {"evaluation": eval_dir / "evaluation.json"}, None
        code = {k: v for k, v in ne.current_hashes("", "", "", "", "").items() if k.endswith("_blob_sha")}
        self.step(f"evaluate-{label}", {"risk": {p.name: stages.file_hash(p) for p in sorted(risk_dir.glob("*.parquet"))},
                                        "store": self.store_key(), "code": code, "matched": self.matched}, evaluate)
        return eval_dir / "evaluation.json"

    # --- comparisons
    def data_checks(self) -> list[dict]:
        sim = (self.sim_dir / "manifest.json").read_bytes()
        reference = self.reference_sim.read_bytes() if self.reference_sim is not None else None
        checks = compare_simulation(sim, self.committed.get("sim_manifest_sha256"), reference)
        rebuilt = store.store_v2_manifest(self.store_dir)
        checks.append(compare_store_inputs(rebuilt.get("inputs") or {}, self.committed))
        if self.reference_store is not None:
            self.out("hashing every table of both stores")
            has_manifest = (self.reference_store / store.STORE_V2_MANIFEST).is_file()
            ref = store.store_v2_manifest(self.reference_store) if has_manifest else None
            checks.append(compare_tables(store.table_digests(self.store_dir), store.table_digests(self.reference_store),
                                         file_bytes_equal=None if ref is None else rebuilt.get("outputs") == ref.get("outputs")))
        return checks

    def run(self) -> dict:
        until = self.until
        self.simulate()
        if until == "simulate":
            return {}
        self.build_store()
        if until == "store":
            return {}
        if self.mode == "committed":
            evaluation = self.evaluate("committed", self.artifact_dir)
            if until == "evaluate":
                return {}
            checks = self.data_checks()
            checks.append(compare_evaluation(evaluation.read_bytes(), self.committed_evaluation.read_bytes()))
            return {"comparisons": checks, "reported": []}
        self.features()
        if until == "features":
            return {}
        run_dirs = [self.train(s) for s in (self.seeds_asked or self.seeds)]
        if until == "train":
            return {}
        self.calibrate(run_dirs)
        if until == "calibrate":
            return {}
        evaluation = self.evaluate("retrained", self.scratch_artifact)
        if until == "evaluate":
            return {}
        a = self.scratch_artifact
        rebuilt = {"manifest": json.loads((a / "manifest.json").read_text(encoding="utf-8")),
                   "evaluation": json.loads(evaluation.read_text(encoding="utf-8"))}
        committed = {"manifest": self.committed,
                     "evaluation": json.loads(self.committed_evaluation.read_text(encoding="utf-8"))}
        checks = self.data_checks() + [
            compare_configuration(self.committed_cfg, yaml.safe_load((a / "config.yaml").read_text(encoding="utf-8"))),
            compare_schema(json.loads((self.artifact_dir / "feature_schema.json").read_text(encoding="utf-8")),
                           json.loads((a / "feature_schema.json").read_text(encoding="utf-8")))]
        checks += metric_records(committed, rebuilt, GATED, gated=True)
        reported = member_records(committed["manifest"], rebuilt["manifest"]) + \
            metric_records(committed, rebuilt, REPORTED, gated=False)
        return {"comparisons": checks, "reported": reported}


def summary(doc: dict) -> list[str]:
    lines = [f"run --from-frozen{' --evaluate-committed' if doc['mode'] == 'committed' else ''} in {doc['scratch']}"]
    for s in doc["steps"]:
        took = f", {s['seconds']:.0f} s" if s.get("seconds") is not None else ""
        lines.append(f"  {s['name']:<20} {s['status']}{took}")
    for c in doc.get("comparisons") or []:
        verdict = "PASS" if c["passed"] else "FAIL"
        if "relative_difference" in c:
            rel = c["relative_difference"]
            lines.append(f"  {verdict}  {c['name']}: committed {c['committed']}, rebuilt {c['rebuilt']} "
                         f"({'no relative difference' if rel is None else f'{100 * rel:.3f}%'}, tolerance "
                         f"{100 * TOLERANCE:.1f}%)")
        else:
            lines.append(f"  {verdict}  {c['name']}" + (f" ({c['differences']} differences)" if c.get("differences")
                                                         else ""))
    for r in doc.get("reported") or []:
        rel = r["relative_difference"]
        lines.append(f"  (reported) {r['name']}: committed {r['committed']}, rebuilt {r['rebuilt']}"
                     + ("" if rel is None else f" ({100 * rel:.3f}%)"))
    verdict = ("stopped after " + doc["stopped_after"]) if doc.get("stopped_after") else \
        ("PASSED" if doc["passed"] else "FAILED")
    lines.append(f"{verdict}; wrote {doc['result']}")
    return lines


def run_from_frozen(scratch: Path, *, out=print, **kw) -> int:
    """`run --from-frozen [--evaluate-committed]`: the steps, the comparisons and SCRATCH/run-*.json. Returns 0
    when every comparison passed (or the run stopped at --until), 1 when one failed. Refusals raise RebuildError
    before anything is written; a step that fails is recorded in the result file and raised."""
    r = Rebuild(scratch, out=out, **kw)
    r.check()
    r.scratch.mkdir(parents=True, exist_ok=True)
    path = r.scratch / RESULT_NAMES[r.mode]
    doc = {"mode": r.mode, "scratch": str(r.scratch), "result": str(path), "git_sha": stages.git_sha(),
           "started_at": stages.now_iso(), "settings": {
               "reuse_data": str(r.reuse) if r.reuse else None,
               "reference_store": str(r.reference_store) if r.reference_store else None,
               "reference_sim": str(r.reference_sim) if r.reference_sim else None, "until": r.until,
               "seeds": r.seeds_asked or r.seeds,
               "workers": r.workers, "device": r.device, "matched": r.matched, "artifact": str(r.artifact_dir),
               "committed_evaluation": str(r.committed_evaluation), "tolerance": TOLERANCE},
           "notes": [UNGATED, "DuckDB's file bytes vary by build, so stores are compared table by table and the "
                     "evaluation's store path, store manifest hash and timing are left out of its comparison.",
                     "No test set is scored and the test lock is not read; the store's table digests hash every row "
                     "of every split and compute no statistic."]}
    try:
        found = r.run()
    except BaseException as e:
        doc |= {"steps": r.steps, "error": f"{type(e).__name__}: {e}"[:2000], "finished_at": stages.now_iso(),
                "passed": False}
        _write_json(path, doc)
        raise
    doc |= {"steps": r.steps, "finished_at": stages.now_iso()}
    if not found:
        doc |= {"stopped_after": r.until, "comparisons": [], "reported": [], "passed": None}
    else:
        doc |= found | {"passed": all(c["passed"] for c in found["comparisons"])}
    _write_json(path, doc)
    for line in summary(doc):
        out(line)
    return 0 if doc["passed"] is not False else 1
