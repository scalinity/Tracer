"""Evaluations of TracerNet's site recalibration (src/neural/sitecal.py) on the development sets that are not locked.

    python -m src.pipeline.sitecal_eval --cv                    # choose the map's form on legacy_v1's history alone
    python -m src.pipeline.sitecal_eval --split legacy_v1       # every baseline beside TracerNet with the site map

Results go to results/neural/sitecal/. Nothing here reads the test set.
"""
from __future__ import annotations

import numpy as np

from src.neural.sitecal import FORM, FORMS, PRIOR_TICKS, apply_map, fit_map, history_xy, tracernet_sitecal
from src.pipeline import neural_eval as ne


def landmark_brier(p, y, t, landmarks=(30, 60)) -> dict:
    """Brier score over the ticks at each landmark (a specimen still silent there), as the evaluation scores it."""
    out = {}
    for L in landmarks:
        m = t == L
        out[str(L)] = {"n": int(m.sum()), "brier": float(np.mean((p[m] - y[m]) ** 2)) if m.any() else None}
    return out


def history_cv_legacy(fit_runs=range(0, 16), check_runs=range(16, 24), prior_ticks: float = PRIOR_TICKS) -> dict:
    """The map's form chosen on legacy_v1's history alone: fit on fit runs 0 to 15, Brier score at 30 and 60 minutes
    of silence on fit runs 16 to 23, for the identity and each form. Scored runs 24 to 29 are never read."""
    from src.generator import config
    from src.pipeline import evaluate, intervals, store

    start, stop = intervals.clock_events(config.load()["policy"])
    obs = store.open_observed(store.STORE_DIR)
    history, _ = evaluate.load_specs(obs, None, start, stop, "fit")
    if any(s.run >= 24 for s in history):
        raise RuntimeError("a scored run reached the history")
    series = ne.FrameRisk(ne.RISK_DIR / "legacy_v1" / "*.parquet")
    series.pin()
    call = lambda specs: series(specs, {})  # noqa: E731
    fit = [s for s in history if s.run in set(fit_runs)]
    chk = [s for s in history if s.run in set(check_runs)]
    pf, yf, tf = history_xy(fit, call)
    pc, yc, tc = history_xy(chk, call)
    out = {"fit_runs": [min(fit_runs), max(fit_runs)], "check_runs": [min(check_runs), max(check_runs)],
           "identity": landmark_brier(pc, yc, tc)}
    for form in FORMS:
        theta, rec = fit_map(pf, yf, tf, form, prior_ticks)
        out[form] = landmark_brier(apply_map(pc, theta, tc), yc, tc) | {"fit": rec}
    return out


def evaluate_own_store(split: str, *, workers: int = 2, prior_ticks: float = PRIOR_TICKS, form: str = FORM,
                       out_dir=None) -> dict:
    """legacy_v1 or control_v1world, every baseline beside TracerNet with site recalibration as the focus, from the
    risk table `evaluate --split <split> --artifact` wrote under data/neural/risk/<split>/. Neither set is locked, and
    neither is the pre-registered claim; the fitted maps are recorded beside the evaluation."""
    import json
    from pathlib import Path

    from src.pipeline import store

    if split not in ("legacy_v1", "control_v1world"):
        raise ValueError("site recalibration is evaluated here on legacy_v1 or control_v1world; a v2 set goes through "
                         "evaluate_split with tracernet_sitecal")
    risk = ne.RISK_DIR / split / "*.parquet"
    if not list(ne.RISK_DIR.joinpath(split).glob("*.parquet")):
        raise SystemExit(f"no risk table under {ne.RISK_DIR / split}; run `evaluate --split {split} --artifact ...`")
    out_dir = Path(out_dir) if out_dir else store.ROOT / "results" / "neural" / "sitecal" / split
    store_dir = store.STORE_DIR if split == "legacy_v1" else store.CONTROL_STORE_DIR
    method = tracernet_sitecal(risk, prior_ticks=prior_ticks, form=form)
    result = ne.evaluate_split(split, ne.baselines()[1:3] + [method] if split == "control_v1world" else
                               ne.baselines() + [method], store_dir=store_dir, out_dir=out_dir, workers=workers,
                               train_store_dir=None)
    return result


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="TracerNet with site recalibration on legacy_v1 or control_v1world")
    ap.add_argument("--split", choices=("legacy_v1", "control_v1world"))
    ap.add_argument("--cv", action="store_true", help="choose the map's form on legacy_v1's history alone")
    ap.add_argument("--form", default=FORM, choices=sorted(FORMS))
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--prior-ticks", type=float, default=PRIOR_TICKS)
    a = ap.parse_args()
    if a.cv:
        print(json.dumps(history_cv_legacy(prior_ticks=a.prior_ticks), indent=1))
        raise SystemExit(0)
    r = evaluate_own_store(a.split, workers=a.workers, prior_ticks=a.prior_ticks, form=a.form)
    print(json.dumps({"calibration": {m: {k: round(v["brier"], 4) for k, v in c.items()}
                                      for m, c in r["calibration"].items()},
                      "held": r["decision_rule"]["verdict"]["held"]}, indent=1))
