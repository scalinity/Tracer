"""TracerNet's predictor: the artifact loaded for scoring, alerts and explanations (docs/neural/spec.md §10.1 to §10.4).

    from src.models.tracernet.predictor import History, Predictor
    p = Predictor.load()                                      # artifacts/tracernet/v1, on CPU
    risk = p.score(store_dir, runs, history=History())        # one row per specimen and tick (spec §10.3)
    alerts = p.alerts(risk, budget_per_shift=6, tune_on=history_risk)
    alerts = p.explain(alerts, store_dir=store_dir)              # factors and related specimens (spec §9)

The artifact holds three members (one rung, three seeds), the feature schema, the bins and the calibration. A
prediction at a tick averages the members' receipt-time distributions, divides the log of that average by the
calibration temperature, and reads p_miss, p_late, p_never and the arrival quantiles off the result; when the
calibration has an isotonic map, p_miss goes through it and p_never and p_late are rescaled so they still add up
to it. `ensemble_sd` is the spread (population standard deviation) of the members' own p_miss.

Scoring reads the observed store only, through features.py. Each run is one task: its context is built once and
its ticks go through the members in fixed chunks of CHUNK ticks on one CPU thread, in worker processes when
`workers` > 1, so the same call gives bit-identical numbers whatever the number of workers.

Runs of the locked splits (test and test_ood, spec §8.7) are scored only with the test run the lock recorded: an
object whose `check(split)` refuses unless the lock still holds that run.

`alerts` raises an alert at a specimen's first tick where the running maximum of its harm (p_miss times the
urgency weight of its tier, config/triage.yaml) reaches the knob, which is the first tick where the harm itself
does. The knob is given, or set per scenario on that scenario's history (replicate 0 on a v2 store, runs 0 to 23
on v1) from alert volume alone: the smallest knob whose alerts per 8-hour shift stay within the budget, by
bisection (spec §8.3), or a knob above every attainable harm (no alerts) when no knob that alerts meets it.

Explanations (spec §9) need the ensemble as a differentiable function: `embed` turns a batch into each member's
continuous inputs, `logit_miss_embedded` runs from there to the logit of the members' mean p_miss with gradients
through all members, `ensemble_logit_miss` does both, and `input_groups` names the inputs of each factor group.
`explain` runs src/models/tracernet/explain.py on an alert table and fills its `factors` and `related` columns; it
reads features through the same locked-split guard as `score` (`check_lock`).
"""

from __future__ import annotations

import json
import math
import multiprocessing as mp
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import polars as pl
import torch
import yaml

from src.generator import config
from src.models.tracernet import bins as tbins
from src.models.tracernet import features as F
from src.models.tracernet.net import SERIES_GROUP, TracerNet, TracerNetConfig
from src.pipeline import store, triage
from src import workers as workers_

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ARTIFACT = ROOT / "artifacts" / "tracernet" / "v1"
VERSION = "tracernet-v1"
CHUNK = 8192
SHIFTS_PER_RUN = 7 * 3          # seven days of three 8-hour shifts
TUNE_STEPS = 30                 # bisection steps, as src/pipeline's run tunes a model's threshold
LOCKED_SPLITS = ("test", "test_ood")    # scored only after the test lock is taken (spec §8.7)
HISTORY_REPLICATE = 0                   # a v2 scenario's history replicate
ARTIFACT_FILES = ("model.pt", "config.yaml", "feature_schema.json", "bins.json", "calibration.json")
BASE_KEYS = ("cat", "tests", "num", "fingerprint", "tick", "window")
COLUMNS = ("specimen_id", "run", "scenario_id", "replicate", "tick_min", "at", "window_min", "tier", "route", "p_miss",
           "p_late", "p_never", "p_on_time", "harm_default", "harm_equal", "arrival_q50_min", "arrival_q90_min",
           "ensemble_sd", "model_version", "policy_hash")
FACTOR = pl.Struct({"group": pl.Utf8, "text": pl.Utf8, "share": pl.Float64, "attribution": pl.Float64,
                    "direction": pl.Utf8})
RELATED = pl.Struct({"specimen_id": pl.Utf8, "weight": pl.Float64})
DISPATCH = pl.Struct({"source": pl.Utf8, "model": pl.Utf8, "place": pl.Utf8, "place_p": pl.Float64,
                      "action": pl.Utf8, "action_p": pl.Float64, "role": pl.Utf8, "shared_problem_p": pl.Float64,
                      "escalate_p": pl.Float64})


def input_keys(rung: str) -> tuple:
    return BASE_KEYS + (tuple(SERIES_GROUP) if rung in ("B", "C") else ()) + \
        (("tokens", "token_mask") if rung == "C" else ())


def net_config_from(doc: dict) -> TracerNetConfig:
    """A TracerNetConfig from its saved form (lists back to tuples)."""
    def tup(v):
        return tuple(tup(x) for x in v) if isinstance(v, list) else v
    return TracerNetConfig(**{k: tup(v) for k, v in doc.items()})


def weight_schemes() -> dict[str, dict[int, float]]:
    """Urgency weights by scheme name (default, equal, steep) and tier."""
    return {name: {int(k): float(v) for k, v in w.items()} for name, w in triage.weight_schemes(triage.load()).items()}


@dataclass(frozen=True)
class History:
    """The runs that play the history role for the fingerprint: None means each scored scenario's own replicate 0
    on a v2 store, or runs 0 to 23 on the v1 store. The history must live in the scored store."""
    store_dir: Path | None = None
    runs: tuple | None = None


@dataclass(frozen=True)
class CheckedTable:
    """A feature table read in the same step as its lock check (checked_table): the store it came from, the runs it
    was read and checked for, their history runs, the locked splits among them, and the test run and artifact the
    check held for. explain takes it in place of reading the store, and checks every alert against it."""
    table: F.Table
    store_dir: Path
    runs: tuple
    history_runs: tuple
    locked_splits: tuple
    test_run: object
    artifact_dir: Path | None


def checked_table(pred: "Predictor", store_dir: Path, runs, *, test_run=None) -> CheckedTable:
    """Read the features of `runs` and their history runs with the predictor's lock check (check_lock) in the same
    step. A caller on a locked split calls this inside the lock's mutex, so the check and the reads it guards cannot
    be separated by a relock; what it returns needs no further read of the store."""
    store_dir = Path(store_dir)
    runs = sorted({int(r) for r in runs})
    hist = _history_runs(store_dir, runs)
    pred.check_lock(store_dir, runs, hist, History(), test_run)
    locked = _locked_splits(store_dir, set(runs) | set(hist))
    t = F.load_table(store_dir, runs=sorted(set(runs) | set(hist)))
    return CheckedTable(t, store_dir.resolve(), tuple(runs), tuple(hist), tuple(locked), test_run, pred.artifact_dir)


class Predictor:
    def __init__(self, members: Sequence[TracerNet], schema: dict, calibration: dict | None = None, *,
                 version: str = VERSION, policy_hash: str | None = None, manifest: dict | None = None,
                 device: str = "cpu"):
        if not members:
            raise ValueError("a predictor needs at least one member")
        rungs = {m.rung for m in members}
        if len(rungs) != 1:
            raise ValueError(f"members of one predictor share a rung, got {sorted(rungs)}")
        F.check_schema(schema)
        self.members = [m.to(device).eval() for m in members]
        self.rung = members[0].rung
        self.schema = schema
        self.bins = members[0].bins
        self.calibration = calibration or {"temperature": 1.0, "isotonic": None}
        iso = self.calibration.get("isotonic")
        self.iso_x = None if iso is None else np.asarray(iso["x"], dtype=float)
        self.iso_y = None if iso is None else np.asarray(iso["y"], dtype=float)
        self.temperature = float(self.calibration["temperature"])
        self.version = version
        self.policy_hash = policy_hash or schema["policy_hash"]
        self.manifest = manifest or {}
        self.artifact_dir: Path | None = None    # set by load; only a loaded artifact may score a locked split
        self.device = device
        self.keys = input_keys(self.rung)

    # --- Loading -----------------------------------------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path = DEFAULT_ARTIFACT, *, device: str = "cpu") -> "Predictor":
        """The artifact at `path`. Refuses one fitted under another policy or another feature-schema version, and
        one whose files are missing from its manifest or differ from the SHA-256 the manifest records."""
        path = Path(path)
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        current = config.load()["policy_hash"]
        if manifest.get("policy_hash") != current:
            raise RuntimeError(f"the artifact was trained under policy {manifest.get('policy_hash')}, the policy is now "
                               f"{current}; retrain it")
        schema = json.loads((path / "feature_schema.json").read_text(encoding="utf-8"))
        if schema.get("version") != F.SCHEMA_VERSION:
            raise RuntimeError(f"the artifact's feature schema is {schema.get('version')!r}, this code builds "
                               f"{F.SCHEMA_VERSION!r}")
        files = manifest.get("files") or {}
        unlisted = [name for name in ARTIFACT_FILES if name not in files]
        if unlisted:
            raise RuntimeError(f"the artifact manifest records no SHA-256 for {unlisted}; rebuild the artifact")
        store.verify_files(path, files, "artifact")
        F.check_schema(schema)
        doc = yaml.safe_load((path / "config.yaml").read_text(encoding="utf-8"))
        bins = tbins.Bins.from_json(json.loads((path / "bins.json").read_text(encoding="utf-8")))
        weights = torch.load(path / "model.pt", map_location="cpu", weights_only=True)
        members = []
        for state in weights["members"]:
            net = TracerNet(net_config_from(doc["net"] | {"edges": list(bins.edges)}))
            net.load_state_dict(state)
            members.append(net)
        calibration = json.loads((path / "calibration.json").read_text(encoding="utf-8"))
        pred = cls(members, schema, calibration, version=manifest.get("version", VERSION),
                   policy_hash=manifest["policy_hash"], manifest=manifest, device=device)
        pred.artifact_dir = path
        return pred

    def payload(self) -> dict:
        """Everything a worker process needs to rebuild this predictor on CPU."""
        return {"members": [{k: v.detach().cpu() for k, v in m.state_dict().items()} for m in self.members],
                "net": asdict(self.members[0].config) | {"edges": list(self.bins.edges)}, "schema": self.schema,
                "calibration": self.calibration, "version": self.version, "policy_hash": self.policy_hash,
                "manifest": self.manifest}

    @classmethod
    def from_payload(cls, doc: dict) -> "Predictor":
        members = []
        for state in doc["members"]:
            net = TracerNet(net_config_from(doc["net"]))
            net.load_state_dict(state)
            members.append(net)
        return cls(members, doc["schema"], doc["calibration"], version=doc["version"],
                   policy_hash=doc["policy_hash"], manifest=doc["manifest"])

    # --- The ensemble as a function -----------------------------------------------------------------------------------

    def _tensors(self, batch: dict) -> dict:
        return {k: (v if isinstance(v, torch.Tensor) else torch.from_numpy(np.ascontiguousarray(v))).to(self.device)
                for k, v in batch.items() if k in self.keys}

    def embed(self, batch: dict) -> list[dict]:
        """Each member's continuous inputs (its own embeddings of the categorical ids)."""
        x = self._tensors(batch)
        return [m.embed(x) for m in self.members]

    def logit_miss_embedded(self, inputs: list[dict], tick: torch.Tensor, window: torch.Tensor, *,
                            temperature: bool = False) -> torch.Tensor:
        """The logit of the members' mean p_miss (spec §9.2), differentiable through every member; with
        temperature=True, the logit of p_miss of the temperature-scaled averaged distribution instead."""
        outs = [m.forward_embedded(inp, tick, window) for m, inp in zip(self.members, inputs)]
        if temperature:
            return self.bins.logit_miss(self._log_mean([o["logits"] for o in outs]) / self.temperature, window)
        z = torch.stack([o["logit_miss"] for o in outs])
        return torch.logsumexp(torch.nn.functional.logsigmoid(z), 0) - \
            torch.logsumexp(torch.nn.functional.logsigmoid(-z), 0)

    def ensemble_logit_miss(self, batch: dict, *, temperature: bool = False) -> torch.Tensor:
        x = self._tensors(batch)
        return self.logit_miss_embedded([m.embed(x) for m in self.members], x["tick"], x["window"],
                                        temperature=temperature)

    def input_groups(self) -> dict:
        """For each factor group of spec §9.3, the embedded inputs it covers (the same for every member)."""
        return self.members[0].input_groups()

    @staticmethod
    def _log_mean(logits: list[torch.Tensor]) -> torch.Tensor:
        logp = torch.stack([torch.log_softmax(x, -1) for x in logits])
        return torch.logsumexp(logp, 0) - math.log(len(logits))

    def mean_logits(self, batch: dict) -> torch.Tensor:
        """The log of the members' averaged receipt-time distribution, uncalibrated: [B, K], impossible bins near
        bins.MASK_LOGIT. Calibration fits its temperature on these."""
        x = self._tensors(batch)
        with torch.no_grad():
            return self._log_mean([m(x)["logits"] for m in self.members])

    def outputs(self, batch: dict) -> dict:
        """Calibrated outputs of a feature batch as float64 numpy arrays."""
        x = self._tensors(batch)
        with torch.no_grad():
            outs = [m(x) for m in self.members]
            log_mean = self._log_mean([o["logits"] for o in outs])
            probs = torch.softmax(log_mean / self.temperature, -1)
            member_miss = torch.stack([o["p_miss"] for o in outs]).double()
        p = probs.cpu().double().numpy()
        window = x["window"].cpu().double().numpy()
        tick = x["tick"].cpu().double().numpy()
        d = self.bins.derived(p, window)
        miss, late, never = self.apply_isotonic(d["p_miss"], d["p_late"], d["p_never"])
        q = self.bins.arrival_quantiles(p, tick, (0.5, 0.9))
        sd = member_miss.std(0, unbiased=False).cpu().numpy() if len(outs) > 1 else np.zeros(len(tick))
        return {"p_miss": miss, "p_late": late, "p_never": never, "p_on_time": 1.0 - miss,
                "arrival_q50_min": q[:, 0], "arrival_q90_min": q[:, 1], "ensemble_sd": sd}

    def apply_isotonic(self, miss, late, never):
        if self.iso_x is None:
            return miss, late, never
        new = np.clip(np.interp(miss, self.iso_x, self.iso_y), 0.0, 1.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            factor = np.where(miss > 0, new / miss, 0.0)
        never = np.minimum(never * factor, new)
        return new, new - never, never

    # --- Scoring -----------------------------------------------------------------------------------------------------

    def score(self, store_dir: Path, runs: Sequence[int], *, history: History | None = None,
              workers: int | None = None, chunk: int = CHUNK, test_run=None) -> pl.DataFrame:
        """One row per tick of every scored-day specimen with a clock in `runs` (the columns of spec §10.3),
        calibrated, computed on CPU. Runs of a locked split (test, test_ood) need `test_run`, the run the test
        lock recorded; its `check(split)` refuses unless the lock still holds it (spec §8.7)."""
        store_dir = Path(store_dir)
        history = history or History()
        if history.store_dir is not None and Path(history.store_dir).resolve() != store_dir.resolve():
            raise ValueError("the history runs must live in the scored store")
        runs = sorted({int(r) for r in runs})
        hist_runs = list(history.runs) if history.runs is not None else _history_runs(store_dir, runs)
        self.check_lock(store_dir, runs, hist_runs, history, test_run)
        t = F.load_table(store_dir, runs=sorted(set(runs) | {int(r) for r in hist_runs}))
        present = set(t.runs.tolist())
        missing = [r for r in runs if r not in present]
        if missing:
            raise KeyError(f"runs {missing} have no collected specimens in {store_dir}")
        tasks = []
        for run in runs:
            rows = t.rows_of_run(run)
            rows = rows[np.asarray(t.a["scored"])[rows]]
            rows = rows[t.n_ticks(rows) > 0]
            if len(rows):
                tasks.append((run, rows))
        cores = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 2)
        workers = workers if workers is not None else max(1, min(len(tasks), cores - 1))
        parts = _run_tasks(self, t, tasks, workers, chunk)
        return self._frame(t, parts)

    def check_lock(self, store_dir: Path, runs, hist_runs, history: History, test_run) -> None:
        """Refuse runs of a locked split (test, test_ood) unless `test_run`, the run the test lock recorded, still
        holds for this artifact and store; score and explain both read features only after this guard."""
        for split in _locked_splits(store_dir, {int(r) for r in runs} | {int(r) for r in hist_runs}):
            check = getattr(test_run, "check", None)
            if not callable(check):
                raise RuntimeError(f"runs of the {split} split are scored only after the test lock is taken "
                                   "(spec §8.7): pass the test run the lock recorded")
            if history.runs is not None:
                raise RuntimeError(f"runs of the {split} split are scored with their own history replicate only")
            if self.artifact_dir is None:
                raise RuntimeError(f"runs of the {split} split are scored only by a predictor loaded from an artifact")
            check(split, artifact_dir=self.artifact_dir, store_dir=store_dir)

    def _frame(self, t: F.Table, parts: list[dict]) -> pl.DataFrame:
        if not parts:
            return pl.DataFrame(schema={c: pl.Utf8 for c in COLUMNS}).select(COLUMNS)
        cols = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
        rows = cols["row"]
        a = t.a
        v1 = bool(t.meta["v1"])
        run = np.asarray(a["run"])[rows]
        serial = np.asarray(a["serial"])[rows]
        tick = cols["tick"]
        origin = np.asarray(a["run_origin"])[np.searchsorted(np.asarray(a["run_ids"]), run)]
        at_s = origin + np.asarray(a["collected"])[rows] + 60.0 * tick
        tier = np.asarray(a["tier"])[rows].astype(np.int64) + 1
        weights = weight_schemes()
        w_default = np.array([0.0] + [weights["default"][k] for k in (1, 2, 3)])
        scenario = np.asarray(t.names["scenarios"], dtype=object)[np.asarray(a["scenario"])[rows]]
        df = pl.DataFrame({
            "run": run.astype(np.int64), "serial": serial.astype(np.int64),
            "scenario_id": scenario.astype(str), "replicate": np.asarray(a["replicate"])[rows].astype(np.int64),
            "tick_min": tick.astype(np.int64), "at_s": np.floor(at_s).astype(np.int64),
            "window_min": np.asarray(a["window"])[rows].astype(np.int64), "tier": tier,
            "route": np.where(np.asarray(a["route_type"])[rows] == 0, "tube", "hand_carry"),
            "p_miss": cols["p_miss"], "p_late": cols["p_late"], "p_never": cols["p_never"],
            "p_on_time": cols["p_on_time"], "harm_default": cols["p_miss"] * w_default[tier],
            "harm_equal": cols["p_miss"] * 1.0,
            "arrival_q50_min": cols["arrival_q50_min"], "arrival_q90_min": cols["arrival_q90_min"],
            "ensemble_sd": cols["ensemble_sd"]})
        run_text = pl.col("run").cast(pl.Utf8)
        if v1:
            run_text = run_text.str.zfill(2)
        return df.with_columns(
            specimen_id=pl.concat_str([pl.lit("SP-r"), run_text, pl.lit("-"), pl.col("serial").cast(pl.Utf8).str.zfill(6)]),
            at=pl.from_epoch("at_s", time_unit="s").dt.strftime("%Y-%m-%dT%H:%M"),
            scenario_id=pl.lit(None, dtype=pl.Utf8) if v1 else pl.col("scenario_id"),
            replicate=pl.lit(None, dtype=pl.Int64) if v1 else pl.col("replicate"),
            arrival_q50_min=pl.col("arrival_q50_min").fill_nan(None),
            arrival_q90_min=pl.col("arrival_q90_min").fill_nan(None),
            model_version=pl.lit(self.version), policy_hash=pl.lit(self.policy_hash),
        ).select(COLUMNS)

    # --- Alerts ------------------------------------------------------------------------------------------------------

    def alerts(self, risk: pl.DataFrame, *, budget_per_shift: float, weights: str = "default",
               knob: float | None = None, tune_on: pl.DataFrame | None = None) -> pl.DataFrame:
        """One alert per specimen whose harm reaches the knob, at its first such tick (spec §10.3's alert
        record; factors, related specimens and dispatch are filled later). The knob is `knob`, or set per scenario
        of `risk` on that scenario's history rows in `tune_on` (replicate 0 on a v2 store, runs 0 to 23 on v1);
        `tune_on` must hold history rows only."""
        scheme = weight_schemes()[weights]
        h = _with_harm(risk, scheme).with_columns(_group=_scenario_key())
        if knob is not None:
            h = h.with_columns(knob=pl.lit(float(knob)))
        else:
            if tune_on is None:
                raise ValueError("alerts needs a knob or a risk table to tune it on")
            knobs = tune_by_scenario(tune_on, h["_group"].unique().to_list(), budget_per_shift, scheme)
            h = h.join(pl.DataFrame({"_group": list(knobs), "knob": list(knobs.values())},
                                    schema={"_group": pl.Utf8, "knob": pl.Float64}), on="_group", how="left")
        fired = (h.filter(pl.col("harm") >= pl.col("knob")).sort(["specimen_id", "tick_min"])
                 .group_by("specimen_id", maintain_order=True).first())
        notice = self.manifest.get("notice", "")
        return fired.select(
            alert_id=pl.concat_str([pl.col("specimen_id"), pl.lit("@"), pl.col("tick_min").cast(pl.Utf8)]),
            specimen_id="specimen_id", run="run", scenario_id="scenario_id", tick_min="tick_min",
            alert_at="at", window_min="window_min",
            warning_min=(pl.col("window_min") - pl.col("tick_min")).cast(pl.Int64), p_miss="p_miss",
            p_never="p_never", harm="harm", weights=pl.lit(weights), budget_per_shift=pl.lit(float(budget_per_shift)),
            knob="knob", method=pl.lit(self.version),
            factors=pl.lit([], dtype=pl.List(FACTOR)), related=pl.lit([], dtype=pl.List(RELATED)),
            dispatch=pl.lit(None, dtype=DISPATCH), notice=pl.lit(notice),
        ).sort(["alert_at", "specimen_id"])

    def explain(self, alerts: pl.DataFrame, *, top_k: int = 3, store_dir: Path, history: History | None = None,
                test_run=None, workers: int = 1, table: CheckedTable | None = None) -> pl.DataFrame:
        """The alerts with `factors` (spec §9.3: the top_k groups that raise the risk and the one that lowers it most)
        and `related` (up to 5 batch-mates by attention weight, which is not an explanation) filled in, explained
        from `store_dir`, the store they were scored on. Runs of a locked split need `test_run`, as score does.
        `table`, a CheckedTable from checked_table, replaces the store read, and every alert is checked against it."""
        from src.models.tracernet import explain as X

        ex = X.explain_alerts(self, alerts, store_dir=store_dir, history=history, test_run=test_run, top_k=top_k,
                              workers=workers, table=table)
        return alerts.with_columns(
            factors=pl.Series([r["factors"] for r in ex.records], dtype=pl.List(FACTOR)),
            related=pl.Series([r["related"] for r in ex.records], dtype=pl.List(RELATED)))


PROBABILITY_SLACK = 1e-5     # float32 rounding takes p_miss about 1e-7 past [0, 1]; anything further is refused


def _with_harm(risk: pl.DataFrame, scheme: dict) -> pl.DataFrame:
    """Harm: p_miss, held to [0, 1] within float32 rounding, times the tier's weight, so no harm passes the largest
    weight and the knob just above it alerts on nothing. A p_miss that is not finite or lies further outside
    [0, 1] than rounding explains is refused."""
    p = risk["p_miss"].to_numpy()
    if not np.all(np.isfinite(p)) or p.min(initial=0.0) < -PROBABILITY_SLACK or p.max(initial=0.0) > 1 + PROBABILITY_SLACK:
        raise ValueError("p_miss holds a value that is not a probability")
    w = pl.col("tier").replace_strict({k: float(v) for k, v in scheme.items()}, return_dtype=pl.Float64)
    return risk.with_columns(harm=pl.col("p_miss").clip(0.0, 1.0) * w)


def alert_volume(max_harm: np.ndarray, knob: float, shifts: float) -> float:
    """Alerts per shift at a knob: a specimen alerts once if its harm ever reaches the knob."""
    return float((max_harm >= knob).sum()) / shifts


def tune_knob(risk: pl.DataFrame, budget_per_shift: float, scheme: dict, steps: int = TUNE_STEPS,
              no_alert: bool = True) -> float:
    """The smallest knob whose alerts per 8-hour shift on `risk` (one scenario's history) stay within the budget,
    by bisection on [0, the largest weight], from alert volume alone (no outcome is read). Every specimen still
    silent at its window's last tick has p_miss 1 there, so the largest weight can be over budget; then no knob
    that alerts meets the budget, and the knob is the one just above the largest weight, above every attainable
    harm, which alerts on nothing (spec §8.3). `no_alert=False` keeps v1's knob (the largest weight), as the
    legacy evaluation does."""
    h = _with_harm(risk, scheme).group_by("specimen_id").agg(pl.col("harm").max(), pl.col("run").first())
    shifts = SHIFTS_PER_RUN * h["run"].n_unique()
    max_harm = h["harm"].to_numpy()
    top = float(max(scheme.values()))
    lo, hi = 0.0, top
    for _ in range(steps):
        mid = (lo + hi) / 2
        if alert_volume(max_harm, mid, shifts) <= budget_per_shift:
            hi = mid
        else:
            lo = mid
    if no_alert and alert_volume(max_harm, hi, shifts) > budget_per_shift:
        hi = float(np.nextafter(top, np.inf))
    return hi


def _scenario_key() -> pl.Expr:
    """A risk row's tuning group: its scenario on a v2 store, one group for the v1 store (no scenario)."""
    return pl.col("scenario_id").fill_null("")


def history_only(tune_on: pl.DataFrame) -> None:
    """Refuse tuning rows that are not history: replicate 0 of a v2 scenario, or runs 0 to 23 of the v1 store."""
    v2 = tune_on["replicate"].is_not_null()
    if v2.any() and not v2.all():
        raise ValueError("tune_on mixes v2 rows and v1 rows")
    if v2.all():
        bad = tune_on.filter(pl.col("replicate") != HISTORY_REPLICATE)
        which = "replicate 0"
    else:
        bad = tune_on.filter(~pl.col("run").is_in(list(F.V1_HISTORY_RUNS)))
        which = "runs 0 to 23"
    if bad.height:
        runs = sorted(bad["run"].unique().to_list())
        raise ValueError(f"tune_on holds rows of runs {runs[:5]} that are not history ({which}); tune on the history "
                         "alone, or use the evaluation's labeled matched-volume check")


def tune_by_scenario(tune_on: pl.DataFrame, groups: Sequence[str], budget_per_shift: float, scheme: dict) -> dict:
    """{group: knob}: each scenario's knob tuned on its own history rows, so no scenario spends another's budget.
    Refuses rows that are not history and a scenario with no history rows."""
    history_only(tune_on)
    keyed = tune_on.with_columns(_group=_scenario_key())
    knobs = {}
    for group in sorted(groups):
        rows = keyed.filter(pl.col("_group") == group)
        if rows.height == 0:
            raise ValueError(f"tune_on has no history rows for scenario {group or '(the v1 store)'}")
        knobs[group] = tune_knob(rows, budget_per_shift, scheme, no_alert=group != "")  # "" is the v1 store
    return knobs


def _history_runs(store_dir: Path, runs: list[int]) -> list[int]:
    """Replicate 0 of each scored run's scenario (v2), or runs 0 to 23 (v1)."""
    con = store.open_observed(Path(store_dir))
    try:
        tables = {r[0] for r in con.execute("show tables").fetchall()}
        if "runs" not in tables:
            return list(F.V1_HISTORY_RUNS)
        rows = con.execute("select run, scenario_id, role from runs").fetchall()
    finally:
        con.close()
    scen_of = {r: s for r, s, _ in rows}
    wanted = {scen_of[r] for r in runs if r in scen_of}
    return sorted(r for r, s, role in rows if s in wanted and role == "history")


def _locked_splits(store_dir: Path, runs) -> list[str]:
    """The locked splits any of `runs` belongs to. The v1 store has no runs table and no locked split."""
    con = store.open_observed(Path(store_dir))
    try:
        tables = {r[0] for r in con.execute("show tables").fetchall()}
        if "runs" not in tables:
            return []
        split_of = dict(con.execute("select run, split from runs").fetchall())
    finally:
        con.close()
    return sorted({split_of[r] for r in runs if split_of.get(r) in LOCKED_SPLITS})


# --- Workers -------------------------------------------------------------------------------------------------------

_W: dict = {}


def _score_run(pred: Predictor, t: F.Table, run: int, rows: np.ndarray, chunk: int) -> dict:
    """All ticks of a run's rows through the members, in fixed chunks, on one CPU thread."""
    n = t.n_ticks(rows)
    rows_rep = np.repeat(rows, n)
    first = np.repeat(np.cumsum(n) - n, n)
    ticks = ((np.arange(len(rows_rep)) - first + 1) * F.TICK_MIN).astype(np.float64)
    ctx = F.RunContext(t, run)
    out: dict[str, list] = {}
    for s in range(0, len(ticks), chunk):
        batch = F.features_for_run(t, run, rows_rep[s:s + chunk], ticks[s:s + chunk], pred.schema, ctx=ctx,
                                   bins=pred.bins)
        res = pred.outputs(batch)
        res["row"] = rows_rep[s:s + chunk]
        res["tick"] = ticks[s:s + chunk]
        for k, v in res.items():
            out.setdefault(k, []).append(v)
    return {k: np.concatenate(v) for k, v in out.items()}


def _init(table_dir: str, payload: dict) -> None:
    torch.set_num_threads(1)
    _W["table"] = F.Table.load(Path(table_dir), mmap=True)
    _W["pred"] = Predictor.from_payload(payload)


def _work(task: tuple) -> dict:
    run, rows, chunk = task
    return _score_run(_W["pred"], _W["table"], run, rows, chunk)


def _run_tasks(pred: Predictor, t: F.Table, tasks: list, workers: int, chunk: int) -> list[dict]:
    if pred.device != "cpu":
        raise RuntimeError("scoring runs on CPU; load the predictor with device='cpu'")
    if workers <= 1 or len(tasks) <= 1:
        threads = torch.get_num_threads()
        torch.set_num_threads(1)
        try:
            return [_score_run(pred, t, run, rows, chunk) for run, rows in tasks]
        finally:
            torch.set_num_threads(threads)
    tmp = Path(tempfile.mkdtemp(prefix="tracernet-score-"))
    try:
        t.save(tmp)
        out = []
        workers_.report("network risk", 0, len(tasks), "runs")
        with workers_.one_thread_children(), mp.get_context("spawn").Pool(
                min(workers, len(tasks)), initializer=_init, initargs=(str(tmp), pred.payload())) as pool:
            for res in pool.imap(_work, [(run, rows, chunk) for run, rows in tasks], chunksize=1):
                out.append(res)
                workers_.report("network risk", len(out), len(tasks), "runs")
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
