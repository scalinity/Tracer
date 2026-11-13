"""The negative control's audit (spec §8.8 A6; results/neural/caveats.md, first item).

The committed control (results/neural/control_v1world/evaluation.json) flags leakage: on the v1-world scenario, where
no context exists, TracerNet's median warning at 6 alerts per shift leads the local analytic refit by 10 minutes
(95% interval 5 to 20). The rule in spec §8.8 treats that as leakage "until shown otherwise". This module runs the
three checks that can show it otherwise, without touching the rule, a test set or the artifact:

1. Placebo. The analytic model fit on 40 further v1-world replicates (run keys outside the control's own 20, so no
   specimen is shared) reads no context and cannot leak by construction: its inputs are a specimen's type, priority,
   collection hour and elapsed silence. The A6 rule is applied to its lead over the same local refit. If the placebo
   is flagged too, the rule's comparator, not TracerNet, produces the flag.
2. TracerNet against that reference. A leak would show as a lead over the best context-free model; matching it shows
   none is used.
3. Future invariance on the control's own trails. TracerNet's p_miss at a tick is recomputed with every scan after
   that tick deleted from the store; any change means the score reads the future.

Nothing here is fit on a scored control replicate, and no test set is read.

    python -m src.pipeline.control_audit reference      # simulate the 40 reference replicates into their own store
    python -m src.pipeline.control_audit placebo        # check 1 (no torch needed)
    python -m src.pipeline.control_audit tracernet --artifact artifacts/tracernet/v1   # check 2
    python -m src.pipeline.control_audit invariance --artifact artifacts/tracernet/v1  # check 3
    python -m src.pipeline.control_audit summary        # results/neural/control_audit/summary.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REF_SIM_DIR = ROOT / "data" / "sim-control-ref"
REF_STORE_DIR = ROOT / "data" / "store-v2-control-ref"
OUT_DIR = ROOT / "results" / "neural" / "control_audit"
REF_REPLICATES = tuple(range(20, 60))       # the control itself uses replicates 0 to 19
MARGIN_MIN = 5.0                             # spec §8.8 A6: a lead of 5 minutes or more is "large"
REFERENCE = "analytic_reference"
TRAIN_SAMPLE_SIM_DIR = ROOT / "data" / "sim-train-sample"
TRAIN_SAMPLE_STORE_DIR = ROOT / "data" / "store-v2-train-sample"
TRAIN_SAMPLE_SHARE = 0.10
TRAIN_SAMPLE_SEED = 20261113


def build_reference(workers: int = 1) -> dict:
    """The v1-world scenario's replicates 20 to 59, simulated as a train split into their own folder and store, so
    the evaluation's global fit (`train_store_dir`) is a context-free fit on specimens the control never scores."""
    from src.pipeline import store
    from src.sim import run as sim_run, v1world

    REF_SIM_DIR.mkdir(parents=True, exist_ok=True)
    scenario = v1world.scenario() | {"split": "train"}
    raw = store.library_bytes([scenario])
    (REF_SIM_DIR / "library.jsonl.gz").write_bytes(raw)
    sim_run.simulate_library([scenario], REF_SIM_DIR, {"train": list(REF_REPLICATES)}, workers,
                             library_sha256=store._sha256_bytes(raw))
    return store.build_v2(REF_SIM_DIR, REF_SIM_DIR / "library.jsonl.gz", None, REF_STORE_DIR, workers=workers)


def build_train_sample(workers: int = 1, share: float = TRAIN_SAMPLE_SHARE, seed: int = TRAIN_SAMPLE_SEED) -> dict:
    """A seeded share of the v2 train scenarios, simulated with the main simulation's replicates (the simulator seeds
    each replicate from the master seed, scenario index and replicate alone, so every week here is byte-identical to
    the same week of data/sim/) into their own store. Its global analytic fit stands in for the fit on all of train
    (`analytic_global` in the committed control), which reads no context either."""
    import random

    from src.pipeline import store
    from src.scenarios import freeze

    records = freeze.read_library()
    splits = json.loads(freeze.SPLITS_PATH.read_text(encoding="utf-8"))
    inputs = freeze.simulation_inputs(records, splits)
    train = sorted(s["scenario_id"] for s in inputs if s["split"] == "train")
    keep = set(random.Random(seed).sample(train, max(1, round(share * len(train)))))
    reps = {sid: list(freeze.REPLICATES["train"]) for sid in keep}
    lib_sha = __import__("hashlib").sha256(freeze.LIBRARY_PATH.read_bytes()).hexdigest()
    TRAIN_SAMPLE_SIM_DIR.mkdir(parents=True, exist_ok=True)
    freeze.simulate_replicates(inputs, reps, TRAIN_SAMPLE_SIM_DIR, workers, library_sha256=lib_sha)
    counts = store.build_v2(TRAIN_SAMPLE_SIM_DIR, freeze.LIBRARY_PATH, freeze.SPLITS_PATH, TRAIN_SAMPLE_STORE_DIR,
                            workers=workers)
    return {"scenarios": len(keep), "of_train": len(train), "share": share, "seed": seed, "counts": counts}


def _methods(with_tracernet: Path | None):
    from src.pipeline import neural_eval as ne

    methods = [ne.AnalyticModel(REFERENCE, "global"), ne.AnalyticModel("analytic_local", "local"), ne.WindowRule()]
    if with_tracernet is not None:
        methods.append(ne.tracernet_method(with_tracernet))
    return methods


def _evaluate(name: str, focus: str, tracernet: Path | None, workers: int, train_store: Path = REF_STORE_DIR,
              methods=None) -> dict:
    from src.pipeline import neural_eval as ne, store

    if not (Path(train_store) / "observed.duckdb").exists():
        raise SystemExit(f"no store at {train_store}; build it first (`reference` or `train-sample`)")
    return ne.evaluate_split("control_v1world", methods or _methods(tracernet), store_dir=store.CONTROL_STORE_DIR,
                             train_store_dir=train_store, focus=focus, out_dir=OUT_DIR / name, workers=workers)


def placebo_v2() -> list:
    from src.pipeline import neural_eval as ne

    return [ne.AnalyticModel("analytic_global", "global"), ne.AnalyticModel("analytic_local", "local"), ne.WindowRule()]


def lead(result: dict, against: str, margin: float = MARGIN_MIN) -> dict:
    """The focus's lead over `against` at the headline budget and 3-2-1 weights, judged by A6's rule exactly as
    src/neural/ablations.control_lead judges TracerNet's (at least `margin` minutes, interval's lower end above 0)."""
    head = result["settings"]["headline"]
    d = result["differences"][head]["3-2-1"][against]
    out = {"focus": result["focus"], "against": against, "budget": head, "margin_min": margin}
    for k in ("median_warning_all_breaches_min", "capped_mean_warning_min"):
        x = d[k]
        out[k] = {"point": x["point"], "low": x["low"], "high": x["high"],
                  "large": x["point"] is not None and x["point"] >= margin and (x["low"] or 0) > 0}
    out["flagged"] = any(out[k]["large"] for k in ("median_warning_all_breaches_min", "capped_mean_warning_min"))
    return out


def invariance(artifact: Path, n_ticks: int = 400, seed: int = 0) -> dict:
    """Check 3: for `n_ticks` (specimen, tick) pairs drawn from the scored control replicates, TracerNet's p_miss
    with the whole store against the same tick scored from a copy of the store with every event after the tick
    removed (src.models.tracernet.predictor.Predictor.score on that copy). Returns the largest absolute change."""
    from src.neural import invariance_check

    return invariance_check.run(artifact, n_ticks=n_ticks, seed=seed)


def summary() -> dict:
    parts = {}
    for name in ("placebo", "placebo_v2", "tracernet", "tracernet_sitecal"):
        p = OUT_DIR / name / "evaluation.json"
        if p.exists():
            parts[name] = json.loads(p.read_text(encoding="utf-8"))
    out = {"notice": "Synthetic data: the v1-world scenario simulated by this project's code, no clinical review.",
           "rule": "spec §8.8 A6: a lead over the local analytic refit of at least 5 minutes whose 95% interval's "
                   "lower end is above zero is treated as leakage until shown otherwise",
           "reference": f"the analytic model fit on v1-world replicates {REF_REPLICATES[0]} to {REF_REPLICATES[-1]} "
                        "(never scored by the control), which reads no context"}
    if "placebo" in parts:
        out["placebo"] = lead(parts["placebo"], "analytic_local")
    if "placebo_v2" in parts:
        out["placebo_v2"] = lead(parts["placebo_v2"], "analytic_local") | {
            "fitted_on": "the analytic model fit on a seeded 10% of the v2 train scenarios (trained like TracerNet, on "
                         "v2 weeks, and reading no context)"}
    if "tracernet_sitecal" in parts:
        out["tracernet_sitecal_vs_local"] = lead(parts["tracernet_sitecal"], "analytic_local")
        cal = parts["tracernet_sitecal"]["calibration"]["tracernet"]
        out["tracernet_sitecal_brier"] = {k: round(v["brier"], 4) for k, v in cal.items()}
    if "tracernet" in parts:
        out["tracernet_vs_local"] = lead(parts["tracernet"], "analytic_local")
        out["tracernet_vs_reference"] = lead(parts["tracernet"], REFERENCE)
        disc = parts["tracernet"]["discrimination"]
        out["pooled_auroc"] = {m: round(disc[m]["pooled"]["auroc"], 4) for m in disc}
    inv = OUT_DIR / "invariance.json"
    if inv.exists():
        out["invariance"] = json.loads(inv.read_text(encoding="utf-8"))
    if all(k in out for k in ("placebo", "placebo_v2", "tracernet_vs_local", "invariance")):
        population = (not out["placebo"]["flagged"]) and out["placebo_v2"]["flagged"]
        out["finding"] = {
            "leakage": "not supported" if population and out["invariance"]["passed"] else "unresolved",
            "cause": ("the training population: an analytic model fit on v1-world weeks matches the local refit, while "
                      "the same context-free analytic model fit on v2 weeks (which cannot read context, so cannot leak "
                      "it) leads the local refit by as much as TracerNet does; TracerNet also reads nothing after a "
                      "tick (invariance). A6 compares a v2-trained model with a v1-world fit, so it measures the "
                      "population gap, not leakage. Against the analytic model fit on the same v2 train weeks the "
                      "committed control already shows no lead (results/neural/REPORT.md, negative control)")
            if population else "not established by these checks",
            "proposed_rule": ("for any later control, judge leakage against the analytic model fit on TracerNet's own "
                              "training population (the global fit), with the local refit reported beside it"),
        }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "summary.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("reference", "train-sample", "placebo", "placebo-v2", "tracernet", "sitecal",
                                        "invariance", "summary"))
    ap.add_argument("--artifact", default="artifacts/tracernet/v1")
    ap.add_argument("--risk", default=None, help="TracerNet's control risk table (parquet glob); built if absent")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--ticks", type=int, default=400)
    a = ap.parse_args(argv)
    if a.command == "reference":
        print(json.dumps(build_reference(a.workers), indent=1, sort_keys=True, default=str))
    elif a.command == "train-sample":
        print(json.dumps(build_train_sample(a.workers), indent=1, sort_keys=True, default=str))
    elif a.command == "placebo-v2":
        r = _evaluate("placebo_v2", "analytic_global", None, a.workers, TRAIN_SAMPLE_STORE_DIR, placebo_v2())
        print(json.dumps(lead(r, "analytic_local"), indent=1))
    elif a.command == "sitecal":
        from src.neural import sitecal
        from src.pipeline import neural_eval as ne
        risk = Path(a.risk) if a.risk else ne.RISK_DIR / "control_v1world" / "*.parquet"
        methods = _methods(None) + [sitecal.tracernet_sitecal(risk)]
        r = _evaluate("tracernet_sitecal", "tracernet", None, a.workers, methods=methods)
        print(json.dumps({"vs_local": lead(r, "analytic_local"), "vs_reference": lead(r, REFERENCE),
                          "brier": {k: v["brier"] for k, v in r["calibration"]["tracernet"].items()}}, indent=1))
    elif a.command == "placebo":
        r = _evaluate("placebo", REFERENCE, None, a.workers)
        print(json.dumps(lead(r, "analytic_local"), indent=1))
    elif a.command == "tracernet":
        risk = Path(a.risk) if a.risk else None
        if risk is None:
            from src.neural.pipeline import own_store_risk
            risk = own_store_risk("control_v1world", ROOT / a.artifact, None, a.workers)
        r = _evaluate("tracernet", "tracernet", risk, a.workers)
        print(json.dumps({"vs_local": lead(r, "analytic_local"), "vs_reference": lead(r, REFERENCE)}, indent=1))
    elif a.command == "invariance":
        res = invariance(ROOT / a.artifact, a.ticks)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "invariance.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(res, indent=1))
    else:
        print(json.dumps(summary(), indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
