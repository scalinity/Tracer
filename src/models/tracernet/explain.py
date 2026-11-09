"""Explanations of TracerNet's alerts: integrated gradients summed into ten factor groups (docs/neural/spec.md §9).

    from src.models.tracernet import explain
    alerts = pred.explain(alerts, store_dir=d)                        # factors and related specimens filled in
    doc = explain.run_checks(pred, alerts, store_dir=d, out_path=p)   # the §9.4 checks, for the report

The function explained (§9.2) is F, the logit of the mean of the members' p_miss before calibration, with gradients
through every member (Predictor.logit_miss_embedded). For an alert with input x1 and baseline x0 the attribution of
input element i is

    A_i = (x1_i - x0_i) * sum_k w_k dF/dx_i (x0 + a_k (x1 - x0)),    a_k = k / S,  k = 0 .. S,

the trapezoid rule (w_k = 1 / S, halved at both ends) with S = 64 steps, doubled while the median alert's
completeness gap |sum_i A_i - (F(x1) - F(x0))| / |F(x1) - F(x0)| is above 1%, up to MAX_STEPS. Spec §9.2 doubles once,
to 128; the batch-mate attention is often close to one-hot, so F turns sharply along the path and 128 steps can leave
the median gap at a few percent while 512 bring it under 1% (measured on the development store), hence the doubling
goes on. The count is chosen on a seeded sample of STEP_SAMPLE alerts and confirmed on all of them.

Categorical inputs are integrated in each member's own embedding space, the numeric inputs, fingerprint and count
series element-wise, and the batch-mate tokens in the space after the token MLP, where every token starts from the
member's learned null token (net.py's `token_emb`). A set of null tokens gives exactly the output of an empty set
(each attention row then sees identical keys and values), so the baseline has no batch-mates. Every element's
attribution is summed into its factor group by TracerNet.input_groups (each member's copy counted), so the ten group
sums add up to the change in F.

The baseline: the same specimen at the same tick in its laboratory's typical state (§9.2). Input by input, from
features.py's columns (`input_groups` assigns them to groups):

    specimen, route_unit, time_of_day, lab_history   unchanged: the same specimen and roster, at the same tick and
            clock time, in the same laboratory with the same fingerprint. These four groups get zero attribution
    silence  log(1 + t0), t0 = min(t, m), m the route's history median collection-to-receipt time (the
            fingerprint's route_p50: this specimen's route, shrunk toward its kind); t when there is no history
    window   t_over_window and window_left at t0; log_window and the window classes unchanged. The tick that the
            network receives (the bins its head masks) is unchanged
    batch_mates, route_flow, unit_flow, lab_flow   their aggregates and count series: the element-wise median of
            the same inputs over probes in the history runs (replicate 0 of the scenario; v1: runs 0 to 23), the
            observed tables the fingerprint summarizes. A probe is a history specimen at the alert's tick t whose
            clock time falls in the same hour of day as the alert's, from the same unit and route; the hour window
            widens by an hour each side until MIN_PROBES are found, then the same route alone is used, then the
            whole laboratory. With no history the inputs stay as they are
    batch-mate tokens   the null token

Factors (§9.3): each alert carries its top 3 groups by positive attribution (share of the positive total,
attribution in logit units, direction "raises") and the group that lowers the risk most, if any (share of the
negative total, direction "lowers"). Texts come from the alert's input values and its baseline's, never from the
attribution sizes. Related specimens are the batch-mates with the largest cross-attention weight (members averaged),
up to 5: what the network looked at, not an explanation.

The checks (§9.4), run on alerts that a caller has already scored (this module chooses no split; a locked split
needs the test run its lock recorded, as Predictor.score does):

    completeness   the median alert's gap at the steps used is within 1%
    deletion       resetting the top 3 groups (by attribution, among the groups whose inputs differ from the
                   baseline) to the baseline lowers p_miss (the ensemble mean, before calibration) more than resetting
                   3 random groups of those (the mean drop over RANDOM_DRAWS draws) in at least 70% of alerts, with a
                   larger mean drop
    randomization  the attributions of a re-initialized network rank the groups whose inputs differ from the baseline
                   differently: mean Spearman correlation over alerts below 0.3

Alerts are explained in tasks of at most BLOCK_ALERTS alerts of one scenario, in a fixed order, each on one CPU thread
in fixed chunks, in worker processes when `workers` > 1, so the same call gives bit-identical numbers whatever the
number of workers.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import shutil
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import polars as pl
import torch
from scipy.stats import rankdata

from src import workers as workers_
from src.models.tracernet import features as F
from src.models.tracernet import predictor as P
from src.models.tracernet.net import GROUPS, TracerNet

ROOT = Path(__file__).resolve().parents[3]
RESULTS_PATH = ROOT / "results" / "neural" / "explanations.json"
STEPS, MAX_STEPS = 64, 1024  # doubled from 64 while completeness misses (MAX_STEPS ASSUMED: cost bound)
STEP_SAMPLE = 256           # alerts the step count is chosen on, a seeded sample (ASSUMED: cost)
COMPLETENESS_GAP = 0.01
TOP_K = 3
RELATED_K = 5
MIN_PROBES = 20             # history probes behind a typical state (ASSUMED: enough for a stable median)
MAX_HOURS = 12              # the hour window widens to the whole day at most
RANDOM_DRAWS = 10           # random 3-group resets per alert in the deletion check (ASSUMED)
DELETION_SHARE = 0.70
RANDOMIZATION_LIMIT = 0.3
REINIT_SEED = 7919           # the re-initialized network's seeds start here, away from the training seeds (0, 1, 2)
RANDOMIZATION_ALERTS = 1000  # alerts the randomization check re-explains, a seeded sample (ASSUMED: cost)
PATH_ROWS = 2048            # rows per integrated-gradients forward pass: alerts per chunk = PATH_ROWS // (steps + 1)
BLOCK_ALERTS = 64           # alerts per task: features and history probes built at once, the unit of parallel work
TYPICAL_GROUPS = ("batch_mates", "route_flow", "unit_flow", "lab_flow")
TYPICAL_NUM = np.array([g in TYPICAL_GROUPS for _, g, _ in F.NUM_COLUMNS])
UNIT_WORDS = {"other": "unit", "ed": "emergency department", "icu": "ICU", "med_surg": "medical-surgical unit",
              "periop": "perioperative unit", "clinic_onsite": "on-site clinic", "clinic_offsite": "off-site clinic",
              "oncology": "oncology unit", "pediatrics": "pediatrics unit", "labor_delivery": "labor and delivery unit",
              "dialysis": "dialysis unit", "long_term_care": "long-term care unit"}
ROUTE_WORDS = {"other": "other-route", "tube": "tube", "courier": "courier", "walk_in": "walk-in",
               "hand_carry": "hand-carried"}


def _n(name: str) -> int:
    return F.NUM_INDEX[name]


def _fp(name: str) -> int:
    return F.FINGERPRINT_COLUMNS.index(name)


# --- The baseline ------------------------------------------------------------------------------------------------

def _raw_at(t: F.Table, rows: np.ndarray, ticks: np.ndarray, contexts: dict, token_rows: bool = False) -> dict:
    """Unscaled features of table rows at ticks, in input order (rows may span runs)."""
    runs = np.asarray(t.a["run"])[rows]
    parts, where = [], []
    for run in np.unique(runs):
        sel = np.flatnonzero(runs == run)
        if int(run) not in contexts:
            contexts[int(run)] = F.RunContext(t, int(run))
        ctx = contexts[int(run)]
        parts.append(F.raw_features(t, ctx, rows[sel] - ctx.lo, ticks[sel], token_rows=token_rows))
        where.append(sel)
    out = F.concat(parts)
    back = np.argsort(np.concatenate(where), kind="stable")
    return {k: v[back] for k, v in out.items()}


def _hour(minutes: np.ndarray) -> np.ndarray:
    return np.floor(np.mod(minutes, F.DAY_MIN) / 60).astype(np.int64)


def _probes(t: F.Table, row: int, tick: float, hist: np.ndarray) -> tuple[np.ndarray, str, int]:
    """History rows that stand in for the laboratory's typical state at this alert's hour, unit and route, with the
    scope used and the hour window (hours each side)."""
    a = t.a
    if len(hist) == 0:
        return hist, "none", 0
    hour = _hour(np.asarray(a["collected"][row]) / 60 + tick)
    dist = np.abs(_hour(np.asarray(a["collected"])[hist] / 60 + tick) - hour)
    dist = np.minimum(dist, 24 - dist)
    same_unit = np.asarray(a["unit"])[hist] == a["unit"][row]
    same_route = np.asarray(a["route"])[hist] == a["route"][row]
    for scope, keep in (("unit and route", same_unit & same_route), ("route", same_route),
                        ("laboratory", np.ones(len(hist), dtype=bool))):
        if keep.sum() < MIN_PROBES:
            continue
        for d in range(MAX_HOURS + 1):
            sel = keep & (dist <= d)
            if sel.sum() >= MIN_PROBES:
                return hist[sel], scope, d
    return hist, "laboratory", MAX_HOURS       # a history smaller than MIN_PROBES: all of it


def typical_state(t: F.Table, raw: dict, rows: np.ndarray, ticks: np.ndarray, hist: np.ndarray,
                  contexts: dict) -> tuple[dict, list[dict]]:
    """The baseline's unscaled inputs for each alert (see the module docstring) and how each was found."""
    base = {k: v.copy() for k, v in raw.items()}
    picks = [_probes(t, int(r), float(k), hist) for r, k in zip(rows, ticks)]
    notes = [{"probes": len(p), "scope": s, "hours_each_side": d} for p, s, d in picks]
    owner = np.concatenate([np.full(len(p), i) for i, (p, _, _) in enumerate(picks)]) if picks else np.empty(0, int)
    if len(owner):
        prow = np.concatenate([p for p, _, _ in picks])
        praw = _raw_at(t, prow, np.asarray(ticks)[owner], contexts)
        for i in range(len(rows)):
            sel = owner == i
            if not sel.any():
                continue
            base["num"][i, TYPICAL_NUM] = np.median(praw["num"][sel][:, TYPICAL_NUM], axis=0)
            for key in F.SERIES:
                base[key][i] = np.median(praw[key][sel], axis=0)
    fp = raw["fingerprint"]
    has = fp[:, _fp("history_available")] > 0
    tick = np.asarray(raw["tick"], dtype=float)
    t0 = np.where(has, np.minimum(tick, np.expm1(fp[:, _fp("route_p50")])), tick)
    w = np.asarray(raw["window"], dtype=float)
    base["num"][:, _n("silence")] = np.log1p(t0)
    base["num"][:, _n("t_over_window")] = t0 / w
    base["num"][:, _n("window_left")] = np.log1p(np.maximum(w - t0, 0))
    for note, s in zip(notes, t0):
        note["silence_min"] = float(s)
    return base, notes


# --- Factor texts --------------------------------------------------------------------------------------------------

def _int(x: float) -> str:
    return f"{int(round(float(x))):,}"


def _count(x: float, noun: str) -> str:
    """'1 receipt', '2 receipts'."""
    n = int(round(float(x)))
    return f"{n:,} {noun}" + ("" if n == 1 else "s")


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def texts(raw: dict, base: dict, i: int, schema: dict, clock_min: float) -> dict[str, str]:
    """The §9.3 sentence of every group for alert i, filled from its input values and its baseline's."""
    num, fp, cat = raw["num"][i], raw["fingerprint"][i], raw["cat"][i]
    vocab = schema["vocab"]
    tick, w = float(raw["tick"][i]), float(raw["window"][i])
    route = ROUTE_WORDS.get(vocab["route_kind"][cat[2]], vocab["route_kind"][cat[2]])
    unit = UNIT_WORDS.get(vocab["unit_kind"][cat[1]], "unit")
    variant = F.vocabularies_cached()["variant_match"][int(cat[0])]["specimen_type"].replace("_", " ")
    has = fp[_fp("history_available")] > 0
    p50, p90, never = np.expm1(fp[_fp("route_p50")]), np.expm1(fp[_fp("route_p90")]), fp[_fp("route_never")]
    out = {}
    out["silence"] = (f"Silent {_int(tick)} min; {route} specimens here usually arrive in {_int(p50)} min, 9 in 10 "
                      f"within {_int(p90)}" if has else f"Silent {_int(tick)} min; no history for this route")
    out["window"] = f"{_int(max(w - tick, 0))} min left of a {_int(w)}-min window"
    out["specimen"] = f"{_cap(variant)}, tier {int(cat[3]) + 1}, {vocab['priority'][cat[4]]}"
    out["route_unit"] = _cap(f"{route} from {unit}")
    day = vocab["weekday_tau"][cat[6]].capitalize()
    hh, mm = divmod(int(np.mod(clock_min, F.DAY_MIN)), 60)
    out["time_of_day"] = f"{day} {hh:02d}:{mm:02d}, {_int(num[_n('to_shift_change_h')] * 60)} min to shift change"
    n_mates, got = np.expm1(num[_n("mates")]), np.expm1(num[_n("mates_received")])
    if round(n_mates) > 0:
        out["batch_mates"] = (f"{_int(got)} of {_count(n_mates, 'specimen')} sent from this unit around the same "
                              f"time {'has' if round(got) == 1 else 'have'} arrived")
    else:
        out["batch_mates"] = "No other specimens were sent from this unit around the same time"
    rate = np.expm1(base["num"][i, _n("rec60_route")])
    usual = f", usually every {_int(60 / rate)} min" if rate > 0 else ", usually none within an hour"
    if num[_n("since_rec_route_missing")] > 0:
        out["route_flow"] = "No receipts via this route yet in this run"
    else:
        out["route_flow"] = f"No receipts via this route for {_int(np.expm1(num[_n('since_rec_route')]))} min{usual}"
    outstanding = np.expm1(num[_n("out_unit")])
    out["unit_flow"] = (f"{_count(outstanding, 'specimen')} from this {unit} "
                        f"{'is' if round(outstanding) == 1 else 'are'} still outstanding")
    rec = np.expm1(num[_n("rec60_lab")])
    col = (1 + rec) / np.exp(num[_n("lab_receipts_over_collections")]) - 1
    state = "behind" if rec < col else "keeping up"
    out["lab_flow"] = (f"Receiving is {state}: {_count(rec, 'receipt')} in the last hour against "
                       f"{_count(max(col, 0), 'collection')} the hour before")
    out["lab_history"] = (f"In this laboratory's history, {route} specimens take {_int(p50)} min and "
                          f"{100 * never:.0f}% never arrive" if has else "No history for this laboratory")
    return out


def factors_of(attr: np.ndarray, sentences: dict, top_k: int = TOP_K) -> list[dict]:
    """The top_k groups by positive attribution and the one that lowers the risk most (spec §9.3)."""
    pos = sorted(((float(a), g) for g, a in zip(GROUPS, attr) if a > 0), key=lambda x: (-x[0], GROUPS.index(x[1])))
    neg = sorted(((float(a), g) for g, a in zip(GROUPS, attr) if a < 0), key=lambda x: (x[0], GROUPS.index(x[1])))
    up, down = sum(a for a, _ in pos), -sum(a for a, _ in neg)
    out = [{"group": g, "text": sentences[g], "share": a / up, "attribution": a, "direction": "raises"}
           for a, g in pos[:top_k]]
    if neg:
        a, g = neg[0]
        out.append({"group": g, "text": sentences[g], "share": -a / down, "attribution": a, "direction": "lowers"})
    return out


# --- Integrated gradients ------------------------------------------------------------------------------------------

def _tensors(x: dict, keys, sl) -> dict:
    return {k: torch.from_numpy(np.ascontiguousarray(x[k][sl])) for k in keys}


def member_inputs(member: TracerNet, x: dict, baseline: bool = False) -> dict:
    """A member's continuous inputs for a scaled batch; for rung C the tokens through the token MLP (`token_emb`),
    or every token the learned null token when `baseline`."""
    with torch.no_grad():
        inp = member.embed(x)
        if member.rung == "C":
            tokens = inp.pop("tokens")
            if baseline:
                inp["token_emb"] = member.attention.null.expand(tokens.shape[0], tokens.shape[1], -1).clone()
            else:
                inp["token_emb"] = member.attention.token(tokens)
    return inp


def group_map(member: TracerNet) -> dict:
    """input_groups with the tokens' place taken by `token_emb`."""
    groups = member.input_groups()
    for g in groups.values():
        if "tokens" in g:
            g["token_emb"] = g.pop("tokens")
    return groups


def _group_sums(groups: dict, attr: dict, n: int) -> np.ndarray:
    out = np.zeros((n, len(GROUPS)))
    for j, g in enumerate(GROUPS):
        for key, idx in groups[g].items():
            v = attr[key].double()
            v = v if idx is None else v[:, idx]
            out[:, j] += v.reshape(n, -1).sum(1).numpy()
    return out


def integrated_gradients(pred: P.Predictor, x1: dict, x0: dict, steps: int = STEPS) -> dict:
    """Group attributions [B, 10], F(x1), F(x0) and the members' mean attention [B, 16] (rung C) for scaled batches
    x1 (the alerts) and x0 (their baselines), every input element's attribution summed into its group."""
    keys = pred.keys
    b1, b0 = _tensors(x1, keys, slice(None)), _tensors(x0, keys, slice(None))
    n, s1 = len(b1["tick"]), steps + 1
    alpha = torch.linspace(0.0, 1.0, s1)
    wts = torch.full((s1,), 1.0 / steps)
    wts[0] = wts[-1] = 0.5 / steps
    tick, window = b1["tick"].repeat(s1), b1["window"].repeat(s1)
    ends, paths, attention = [], [], []
    for m in pred.members:
        i1, i0 = member_inputs(m, b1), member_inputs(m, b0, baseline=True)
        path = {}
        for k, v1 in i1.items():
            if k == "token_mask":
                path[k] = v1.repeat(s1, 1)
                continue
            a = alpha.view(-1, *([1] * v1.dim()))
            path[k] = (i0[k].unsqueeze(0) + a * (v1 - i0[k]).unsqueeze(0)).reshape(-1, *v1.shape[1:]) \
                .requires_grad_(True)
        ends.append((i1, i0))
        paths.append(path)
        if m.rung == "C":
            with torch.no_grad():
                attention.append(m.forward_embedded(i1, b1["tick"], b1["window"])["attention"])
    f = pred.logit_miss_embedded(paths, tick, window)
    f.sum().backward()
    total = np.zeros((n, len(GROUPS)))
    for m, path, (i1, i0) in zip(pred.members, paths, ends):
        attr = {}
        for k, p in path.items():
            if k == "token_mask":
                continue
            g = p.grad.reshape(s1, n, *p.shape[1:])
            avg = (wts.view(-1, *([1] * (g.dim() - 1))) * g).sum(0)
            attr[k] = avg * (i1[k] - i0[k])
        total += _group_sums(group_map(m), attr, n)
    f = f.detach().double().numpy()
    return {"groups": total, "f1": f[steps * n:], "f0": f[:n],
            "attention": torch.stack(attention).mean(0).numpy() if attention else None}


def resettable(pred: P.Predictor, x1: dict, x0: dict) -> np.ndarray:
    """[B, 10] True where a group's inputs differ from the baseline (only those can be reset)."""
    groups = group_map(pred.members[0])
    n = len(x1["tick"])
    out = np.zeros((n, len(GROUPS)), dtype=bool)
    for j, g in enumerate(GROUPS):
        for key, idx in groups[g].items():
            if key == "token_emb":
                out[:, j] |= np.asarray(x1["token_mask"]).any(1)
                continue
            if key == "cat_emb":
                continue            # the same categorical ids in input and baseline
            a, b = np.asarray(x1[key]), np.asarray(x0[key])
            if idx is not None:
                a, b = a[:, idx], b[:, idx]
            out[:, j] |= (a != b).reshape(n, -1).any(1)
    return out


def reset_p_miss(pred: P.Predictor, x1: dict, x0: dict, chosen: np.ndarray) -> np.ndarray:
    """[B, V] the members' mean p_miss (before calibration) with, in variant v, the groups chosen[b, v] reset to the
    baseline and every other input as in x1."""
    n, v = chosen.shape[:2]
    keys = pred.keys
    b1, b0 = _tensors(x1, keys, slice(None)), _tensors(x0, keys, slice(None))
    ch = torch.from_numpy(chosen.reshape(n * v, len(GROUPS)))
    inputs = []
    for m in pred.members:
        i1, i0 = member_inputs(m, b1), member_inputs(m, b0, baseline=True)
        groups = group_map(m)
        mixed = {}
        for k, v1 in i1.items():
            r1 = v1.repeat_interleave(v, 0)
            if k == "token_mask":
                mixed[k] = r1
                continue
            r0 = i0[k].repeat_interleave(v, 0)
            mask = torch.zeros(r1.shape[0], *([v1.shape[1]] if v1.dim() > 1 else []), dtype=torch.bool)
            for j, g in enumerate(GROUPS):
                idx = groups[g].get(k, "absent")
                if isinstance(idx, str):
                    continue
                if idx is None:
                    mask |= ch[:, j].view(-1, *([1] * (mask.dim() - 1)))
                else:
                    cols = torch.zeros(v1.shape[1], dtype=torch.bool)
                    cols[idx] = True
                    mask |= ch[:, j:j + 1] & cols[None, :]
            mask = mask.view(*mask.shape, *([1] * (r1.dim() - mask.dim())))
            mixed[k] = torch.where(mask, r0, r1)
        inputs.append(mixed)
    with torch.no_grad():
        z = pred.logit_miss_embedded(inputs, b1["tick"].repeat_interleave(v), b1["window"].repeat_interleave(v))
    return torch.sigmoid(z.double()).numpy().reshape(n, v)


def random_predictor(pred: P.Predictor, seed: int = 0) -> P.Predictor:
    """The same architecture and schema with every member re-initialized (the randomization check, §9.4), from
    seeds of its own (REINIT_SEED + seed + member), away from the training seeds, without touching the global state."""
    members = []
    with torch.random.fork_rng(devices=[]):
        for i, m in enumerate(pred.members):
            torch.manual_seed(REINIT_SEED + seed + i)
            members.append(TracerNet(m.config))
    return P.Predictor(members, pred.schema, None, version=pred.version, policy_hash=pred.policy_hash,
                       manifest=pred.manifest)


def _rng(seed: int, alert_id: str) -> np.random.Generator:
    return np.random.default_rng([seed, zlib.crc32(alert_id.encode())])


def deletion_resets(pred: P.Predictor, x1: dict, x0: dict, attr: np.ndarray, can: np.ndarray, ids: Sequence[str],
                    seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """The deletion check's p_miss (§9.4) for each alert: [B, 2 + RANDOM_DRAWS] as the input, with its top 3 groups
    by attribution reset to the baseline, and with 3 random groups reset in each draw, the groups drawn from those
    whose inputs differ from the baseline (`can`), seeded per alert id; and the groups chosen in each variant."""
    n = len(ids)
    chosen = np.zeros((n, 2 + RANDOM_DRAWS, len(GROUPS)), dtype=bool)
    for b in range(n):
        cand = np.flatnonzero(can[b])
        k = min(TOP_K, len(cand))
        chosen[b, 1, cand[np.argsort(-attr[b, cand], kind="stable")[:k]]] = True
        rng = _rng(seed, ids[b])
        for d in range(RANDOM_DRAWS):
            chosen[b, 2 + d, rng.choice(cand, size=k, replace=False)] = True
    return reset_p_miss(pred, x1, x0, chosen), chosen


def _spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = rankdata(a), rankdata(b)
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


# --- One task: the alerts of one scenario ------------------------------------------------------------------------

def _explain_task(pred: P.Predictor, rand: P.Predictor | None, t: F.Table, task: dict, steps: int, top_k: int,
                  checks: bool) -> list[dict]:
    """A task's alerts in fixed blocks of BLOCK_ALERTS (bounding the history probes held at once), sharing the run
    contexts."""
    contexts: dict = {}
    out = []
    for s in range(0, len(task["rows"]), BLOCK_ALERTS):
        sl = slice(s, s + BLOCK_ALERTS)
        out += _explain_block(pred, rand, t, task["rows"][sl], task["ticks"][sl], task["alert_ids"][sl],
                              task["history_rows"], task["random"][sl], task["seed"], contexts, steps, top_k, checks)
    return out


def _explain_block(pred, rand, t, rows, ticks, ids, history_rows, random_flags, seed, contexts, steps, top_k,
                   checks) -> list[dict]:
    raw = _raw_at(t, rows, ticks, contexts, token_rows=True)
    base, notes = typical_state(t, raw, rows, ticks, history_rows, contexts)
    x1, x0 = F.transform(raw, pred.schema), F.transform(base, pred.schema)
    clock = np.asarray(t.a["collected"])[rows] / 60 + ticks
    out = []
    per = max(1, PATH_ROWS // (steps + 1))
    for s in range(0, len(rows), per):
        sl = slice(s, s + per)
        c1, c0 = {k: v[sl] for k, v in x1.items()}, {k: v[sl] for k, v in x0.items()}
        ig = integrated_gradients(pred, c1, c0, steps)
        can = resettable(pred, c1, c0)
        if checks:
            p, chosen = deletion_resets(pred, c1, c0, ig["groups"], can, ids[s:s + per], seed)
            flags = random_flags[sl]
            if rand is not None and flags.any():
                sub = np.flatnonzero(flags)
                rig = integrated_gradients(rand, {k: v[sub] for k, v in c1.items()},
                                           {k: v[sub] for k, v in c0.items()}, steps)
                random_groups = dict(zip(sub.tolist(), rig["groups"]))
            else:
                random_groups = {}
        for b in range(len(c1["tick"])):
            i = s + b
            attr = ig["groups"][b]
            delta = float(ig["f1"][b] - ig["f0"][b])
            gap = abs(float(attr.sum()) - delta)
            sentences = texts(raw, base, i, pred.schema, clock[i])
            related = []
            if ig["attention"] is not None:
                trow, w = raw["token_row"][i], ig["attention"][b]
                order = [j for j in np.argsort(-w, kind="stable") if trow[j] >= 0][:RELATED_K]
                if order:
                    related = [{"specimen_id": sid, "weight": float(w[j])}
                               for sid, j in zip(t.specimen_ids(trow[order]), order)]
            rec = {"alert_id": ids[i], "groups": attr.tolist(), "f1": float(ig["f1"][b]), "f0": float(ig["f0"][b]),
                   "gap": gap, "relative_gap": gap / max(abs(delta), 1e-12),
                   "factors": factors_of(attr, sentences, top_k),
                   "related": related, "baseline": notes[i], "resettable": can[b].tolist()}
            if checks:
                rec["deletion"] = {"full": float(p[b, 0]), "top": float(p[b, 1]),
                                   "random": float(p[b, 2:].mean()), "groups_reset": int(chosen[b, 1].sum())}
                if b in random_groups:
                    rec["random_groups"] = random_groups[b].tolist()
            out.append(rec)
    return out


# --- Running ---------------------------------------------------------------------------------------------------

_W: dict = {}


def _init(table_dir: str, payload: dict, rand_payload: dict | None) -> None:
    torch.set_num_threads(1)
    _W["table"] = F.Table.load(Path(table_dir), mmap=True)
    _W["pred"] = P.Predictor.from_payload(payload)
    _W["rand"] = None if rand_payload is None else P.Predictor.from_payload(rand_payload)


def _work(args: tuple) -> list[dict]:
    task, steps, top_k, checks = args
    return _explain_task(_W["pred"], _W["rand"], _W["table"], task, steps, top_k, checks)


def _run(pred, rand, t, tasks, steps, top_k, checks, workers) -> list[list[dict]]:
    if pred.device != "cpu":
        raise RuntimeError("explanations run on CPU; load the predictor with device='cpu'")
    if workers <= 1 or len(tasks) <= 1:
        threads = torch.get_num_threads()
        torch.set_num_threads(1)
        try:
            return [_explain_task(pred, rand, t, task, steps, top_k, checks) for task in tasks]
        finally:
            torch.set_num_threads(threads)
    tmp = Path(tempfile.mkdtemp(prefix="tracernet-explain-"))
    try:
        t.save(tmp)
        out = []
        workers_.report("explanations", 0, len(tasks), "blocks")
        with workers_.one_thread_children(), mp.get_context("spawn").Pool(
                min(workers, len(tasks)), initializer=_init,
                initargs=(str(tmp), pred.payload(), None if rand is None else rand.payload())) as pool:
            for res in pool.imap(_work, [(task, steps, top_k, checks) for task in tasks], chunksize=1):
                out.append(res)
                workers_.report("explanations", len(out), len(tasks), "blocks")
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@dataclass
class Explained:
    """One record per alert, in the order of the alerts given, and the steps and median completeness gap used."""
    records: list[dict]
    steps: int
    median_relative_gap: float | None


def _sample(ids: Sequence[str], limit: int | None, seed: int) -> np.ndarray:
    if limit is None or len(ids) <= limit:
        return np.ones(len(ids), dtype=bool)
    key = np.array([zlib.crc32(f"{seed}:{i}".encode()) for i in ids], dtype=np.int64)
    keep = np.zeros(len(ids), dtype=bool)
    keep[np.lexsort((np.array(ids), key))[:limit]] = True
    return keep


def explain_alerts(pred: P.Predictor, alerts: pl.DataFrame, *, store_dir: Path, history: P.History | None = None,
                   test_run=None, top_k: int = TOP_K, checks: bool = False, seed: int = 0,
                   randomization_alerts: int | None = RANDOMIZATION_ALERTS, workers: int = 1,
                   steps: int | None = None) -> Explained:
    """Integrated gradients for every alert (spec §9), from the observed store the alerts were scored on. With
    `checks`, also the deletion resets and, for a seeded sample of `randomization_alerts` alerts (None: all), the
    attributions of a re-initialized network. Unless `steps` is given, the steps start at 64 and double while the
    median alert misses completeness by more than 1%, up to MAX_STEPS: chosen on a seeded sample of STEP_SAMPLE
    alerts, then confirmed on all of them. Runs of a locked split need `test_run`, as Predictor.score does."""
    store_dir = Path(store_dir)
    history = history or P.History()
    if history.store_dir is not None and Path(history.store_dir).resolve() != store_dir.resolve():
        raise ValueError("the history runs must live in the explained store")
    if alerts.height == 0:
        return Explained([], steps or STEPS, None)
    runs = sorted({int(r) for r in alerts["run"].to_list()})
    hist_runs = list(history.runs) if history.runs is not None else P._history_runs(store_dir, runs)
    pred.check_lock(store_dir, runs, hist_runs, history, test_run)
    t = F.load_table(store_dir, runs=sorted(set(runs) | {int(r) for r in hist_runs}))
    ids = alerts["alert_id"].to_list()
    rows = t.rows_of(alerts["specimen_id"].to_list())
    ticks = alerts["tick_min"].to_numpy().astype(np.float64)
    a = t.a
    scen = np.asarray(a["scenario"])[rows]
    hist_set = np.isin(np.asarray(a["run"]), hist_runs) & np.asarray(a["scored"])
    flags = _sample(ids, randomization_alerts, seed) if checks else np.zeros(len(ids), dtype=bool)
    rand = random_predictor(pred, seed) if checks else None
    workers = max(1, min(workers, workers_.available_cores()))

    def tasks_of(keep: np.ndarray) -> tuple[list[dict], list[np.ndarray]]:
        tasks, order = [], []
        for s in np.unique(scen[keep]):
            ordered = np.flatnonzero(keep & (scen == s))
            ordered = ordered[np.lexsort((ticks[ordered], rows[ordered]))]
            hist = np.flatnonzero(hist_set & (np.asarray(a["scenario"]) == s))
            for k in range(0, len(ordered), BLOCK_ALERTS):          # a task per block: workers share a scenario
                sel = ordered[k:k + BLOCK_ALERTS]
                tasks.append({"rows": rows[sel], "ticks": ticks[sel], "alert_ids": [ids[i] for i in sel],
                              "history_rows": hist, "random": flags[sel], "seed": seed})
                order.append(sel)
        return tasks, order

    def once(keep: np.ndarray, n_steps: int, with_checks: bool) -> tuple[list[dict], float]:
        tasks, order = tasks_of(keep)
        parts = _run(pred, rand if with_checks else None, t, tasks, n_steps, top_k, with_checks, workers)
        recs: list = [None] * len(ids)
        for sel, part in zip(order, parts):
            for i, rec in zip(sel, part):
                recs[i] = rec
        return recs, float(np.median([recs[i]["relative_gap"] for i in np.flatnonzero(keep)]))

    every = np.ones(len(ids), dtype=bool)
    used = steps or STEPS
    if steps is None:
        sample = _sample(ids, STEP_SAMPLE, seed)          # every alert when there are few
        records, median_gap = once(sample, used, False)
        while median_gap > COMPLETENESS_GAP and used < MAX_STEPS:
            used *= 2
            records, median_gap = once(sample, used, False)
        if sample.all() and not checks:
            return Explained(records, used, median_gap)
    records, median_gap = once(every, used, checks)
    while steps is None and median_gap > COMPLETENESS_GAP and used < MAX_STEPS:
        used *= 2
        records, median_gap = once(every, used, checks)
    return Explained(records, used, median_gap)


# --- The checks of §9.4 ---------------------------------------------------------------------------------------------

def completeness_check(ex: Explained) -> dict:
    gaps = [r["relative_gap"] for r in ex.records]
    return {"steps": ex.steps, "median_relative_gap": ex.median_relative_gap,
            "median_absolute_gap": float(np.median([r["gap"] for r in ex.records])) if gaps else None,
            "limit": COMPLETENESS_GAP, "alerts": len(gaps),
            "passed": bool(gaps) and ex.median_relative_gap <= COMPLETENESS_GAP}


def deletion_check(ex: Explained) -> dict:
    rows = [r["deletion"] for r in ex.records if "deletion" in r]
    if not rows:
        return {"alerts": 0, "passed": False}
    top = np.array([d["full"] - d["top"] for d in rows])
    rnd = np.array([d["full"] - d["random"] for d in rows])
    share = float(np.mean(top > rnd))
    return {"alerts": len(rows), "share_top_beats_random": share, "required_share": DELETION_SHARE,
            "mean_drop_top": float(top.mean()), "mean_drop_random": float(rnd.mean()), "random_draws": RANDOM_DRAWS,
            "passed": share >= DELETION_SHARE and float(top.mean()) > float(rnd.mean())}


def randomization_check(ex: Explained) -> dict:
    rho = []
    for r in ex.records:
        if "random_groups" not in r:
            continue
        keep = np.asarray(r["resettable"])
        value = _spearman(np.asarray(r["groups"])[keep], np.asarray(r["random_groups"])[keep])
        if value is not None:
            rho.append(value)
    if not rho:
        return {"alerts": 0, "passed": False}
    return {"alerts": len(rho), "mean_spearman": float(np.mean(rho)), "median_spearman": float(np.median(rho)),
            "limit": RANDOMIZATION_LIMIT, "passed": float(np.mean(rho)) < RANDOMIZATION_LIMIT}


def examples(alerts: pl.DataFrame, ex: Explained, k: int = 3) -> list[dict]:
    """Three alerts with their factors: the highest, the median and the lowest p_miss (ties to the alert id)."""
    if alerts.height == 0:
        return []
    order = alerts.with_row_index("_i").sort(["p_miss", "alert_id"])
    picks = sorted({0, order.height // 2, order.height - 1}, key=lambda j: -j)[:k]
    out = []
    for j in picks:
        row = order.row(j, named=True)
        rec = ex.records[row["_i"]]
        out.append({"alert_id": row["alert_id"], "scenario_id": row["scenario_id"], "tick_min": row["tick_min"],
                    "window_min": row["window_min"], "p_miss": row["p_miss"], "factors": rec["factors"],
                    "related": rec["related"], "baseline": rec["baseline"]})
    return out


def run_checks(pred: P.Predictor, alerts: pl.DataFrame, *, store_dir: Path, history: P.History | None = None,
               test_run=None, set_name: str | None = None, out_path: Path | None = None, seed: int = 0,
               randomization_alerts: int | None = RANDOMIZATION_ALERTS, workers: int = 1,
               limit: int | None = None) -> dict:
    """The §9.4 checks on the alerts given (scored by the caller on whichever set it chose), or on a seeded sample of
    `limit` of them, and three example alerts with their factors, written to `out_path`
    (results/neural/explanations.json for the report) when given."""
    total = alerts.height
    if limit is not None and total > limit:
        alerts = alerts.filter(pl.Series(_sample(alerts["alert_id"].to_list(), limit, seed)))
    ex = explain_alerts(pred, alerts, store_dir=store_dir, history=history, test_run=test_run, checks=True, seed=seed,
                        randomization_alerts=randomization_alerts, workers=workers)
    checks = {"completeness": completeness_check(ex), "deletion": deletion_check(ex),
              "randomization": randomization_check(ex)}
    with_factors = sum(1 for r in ex.records if r["factors"])
    doc = {"set": set_name, "alerts": len(ex.records), "alerts_given": total, "alerts_with_factors": with_factors,
           "model_version": pred.version, "policy_hash": pred.policy_hash, "steps": ex.steps, "seed": seed,
           "budget_per_shift": _one(alerts, "budget_per_shift"), "weights": _one(alerts, "weights"),
           "checks": checks, "passed": all(c["passed"] for c in checks.values()) and with_factors == len(ex.records),
           "examples": examples(alerts, ex), "notice": pred.manifest.get("notice", ""),
           "baseline_scopes": _counts([r["baseline"]["scope"] for r in ex.records]),
           "raising_groups": _counts([f["group"] for r in ex.records for f in r["factors"]
                                      if f["direction"] == "raises"])}
    if out_path is not None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(doc, indent=1, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return doc


def _one(alerts: pl.DataFrame, col: str):
    if col not in alerts.columns or alerts.height == 0:
        return None
    values = alerts[col].unique().to_list()
    return values[0] if len(values) == 1 else sorted(values)


def _counts(values: list) -> dict:
    out: dict = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))

