"""X2, the alert dispatcher (docs/neural/spec.md §11.3): where to look, what to do, who to call, for each alert.

    sample = dispatcher.sample_alerts(alerts, n=5000, seed=0, per_run=10)   # capped runs, seeded order
    items = dispatcher.build_items(sample, facts_rows, cfg)             # X2's state and questions per alert
    rows = dispatcher.decisions(outcomes, items, cfg)                   # the decision for every alert

The alerts are TracerNet's at the headline budget (6 per 8-hour shift, 3-2-1 weights), each with its factors from
src/models/tracernet/explain.py. A sample takes runs (laboratory weeks) in an order fixed by the seed and at most
`samples.X2_per_run` alerts of each, until it holds at least n alerts; a cluster bootstrap over scenarios scores it. Each item's state is render.alert_state with
TracerNet's p_miss, p_never and its top 3 factor texts (the groups that raise the risk); its meta keeps the alert's
identifiers (never sent) and the decisions of rules R1 and R2 on the same facts, so any question can fall back.

Decisions: the top choice for `place` and `action` (no threshold tuned on any label), the role that owns the action,
and `shared_problem` and `escalate` as yes at a probability of at least 0.7, fixed in advance. A refused question
takes R2's answer for that question alone; an item with no answer at all (a 400, a stopped job, retries used up)
takes R2's whole decision, with `source` "rules" and the reason. `source` is "drex-max" for answers from Drex Max and
"jev-fallback" when the job ran on the configured fallback model.
"""

from __future__ import annotations

import zlib

import polars as pl

from src.decide import render, rules
from src.decide.client import Item

def sample_alerts(alerts: pl.DataFrame, n: int | None, seed: int, per_run: int | None = None) -> pl.DataFrame:
    """Runs (one laboratory week of one scenario) in the seed's order, taking at most `per_run` alerts of each (in
    the seed's order of alert ids; None: every alert), until at least n alerts are in (n None: every alert). A week
    holds about 126 alerts at 6 per 8-hour shift, so whole runs would fill a 5,000-alert sample from about 40 weeks;
    capping each run spreads it over hundreds of scenarios. Every score is per alert and every state is written from
    the store, so no alert needs the others of its week; the cluster bootstrap resamples by scenario."""
    if n is None or (per_run is None and alerts.height <= n):
        return alerts                  # with a cap, a small input still goes through the cap below
    counts = alerts.group_by("scenario_id", "run").len().rows()
    order = sorted(counts, key=lambda r: (zlib.crc32(f"{seed}:{r[0]}:{r[1]}".encode()), r[0], r[1]))
    keep, total = [], 0
    for _scenario, run, k in order:
        if total >= n:
            break
        keep.append(run)
        total += k if per_run is None else min(k, per_run)
    if per_run is None:
        return alerts.filter(pl.col("run").is_in(keep))
    return _cap_runs(alerts, keep, per_run, seed)


def _cap_runs(alerts: pl.DataFrame, runs: list, per_run: int, seed: int) -> pl.DataFrame:
    """At most `per_run` alerts of each of `runs`, in the seed's order of alert ids, kept in the alerts' own order."""
    chosen = alerts.filter(pl.col("run").is_in(runs))
    rank = pl.col("alert_id").map_elements(lambda a: zlib.crc32(f"{seed}:{a}".encode()), return_dtype=pl.Int64)
    ids = (chosen.select("run", "alert_id").with_columns(_rank=rank).sort("run", "_rank", "alert_id")
           .group_by("run", maintain_order=True).head(per_run)["alert_id"])
    return chosen.filter(pl.col("alert_id").is_in(ids.implode()))


def raising_texts(factors) -> list[str]:
    """TracerNet's top 3 factor texts: the groups that raise the risk, in the order explain gave them."""
    return [f["text"] for f in (factors or []) if f.get("direction") == "raises" and f.get("text")][:3]


def build_items(alerts: pl.DataFrame, facts_rows: list[dict], cfg: dict) -> list[Item]:
    """One Item per alert, in the alerts' order; facts_rows[i] are the facts of alert i."""
    budget = int(cfg["budgets"]["X2"])
    questions = render.x2_questions()
    out = []
    for row, f in zip(alerts.iter_rows(named=True), facts_rows, strict=True):
        tn = {"p_miss": float(row["p_miss"]), "p_never": float(row["p_never"]),
              "factors": raising_texts(row.get("factors"))}
        state = render.alert_state(f, tn, budget=budget)
        out.append(Item(row["alert_id"], state, questions, meta={
            "alert_id": row["alert_id"], "specimen_id": row["specimen_id"], "run": int(row["run"]),
            "scenario_id": row["scenario_id"], "tick_min": int(row["tick_min"]), "window_min": int(row["window_min"]),
            "route_kind": f["specimen"]["route_kind"], "p_miss": tn["p_miss"], "p_never": tn["p_never"],
            "state_hash": render.state_hash(state), "r1": rules.r1(f), "r2": rules.r2(f, tn["p_never"])}))
    return out


def _choice(answer: dict | None):
    if not answer or answer.get("type") != "choice":
        return None, None
    return answer["choice"], float(answer["probabilities"].get(answer["choice"], 0.0))


def _noul(answer: dict | None):
    return float(answer["p"]) if answer and answer.get("type") == "noul" else None


def decide(outcome, item: Item, cfg: dict, model: str) -> dict:
    """The dispatch record of one alert (spec §10.3's `dispatch`, plus the yes-no decisions and what fell back)."""
    yes = float(cfg["decisions"]["noul_yes"])
    r2 = item.meta["r2"]
    if outcome.status not in ("answered", "cached"):
        return {"source": "rules", "model": None, "place": r2["place"], "place_p": None, "action": r2["action"],
                "action_p": None, "role": r2["role"], "shared_problem_p": None, "escalate_p": None,
                "shared_problem": r2["shared_problem"], "escalate": r2["escalate"],
                "fallback": ["place", "action", "shared_problem", "escalate"], "fallback_reason": outcome.reason}
    a = outcome.answers or {}
    fallback = []
    place, place_p = _choice(a.get("place"))
    if place is None:
        place, fallback = r2["place"], fallback + ["place"]
    action, action_p = _choice(a.get("action"))
    if action is None:
        action, fallback = r2["action"], fallback + ["action"]
    shared_p, escalate_p = _noul(a.get("shared_problem")), _noul(a.get("escalate"))
    if shared_p is None:
        fallback.append("shared_problem")
    if escalate_p is None:
        fallback.append("escalate")
    return {"source": "drex-max" if model == cfg["model"] else "jev-fallback", "model": outcome.resolved_model,
            "place": place, "place_p": place_p, "action": action, "action_p": action_p, "role": rules.role(action),
            "shared_problem_p": shared_p, "escalate_p": escalate_p,
            "shared_problem": r2["shared_problem"] if shared_p is None else shared_p >= yes,
            "escalate": r2["escalate"] if escalate_p is None else escalate_p >= yes,
            "fallback": fallback, "fallback_reason": "refused" if fallback else None}


def decisions(outcomes, items: list[Item], result, cfg: dict) -> list[dict]:
    """Every alert's output row: its identifiers, the hashes of its request and state, the decision model's answers
    and decision, and R1's and R2's decisions beside them."""
    rows = []
    for o, it in zip(outcomes, items, strict=True):
        rows.append({"item_id": it.item_id, "request_hash": o.request_hash, "state_hash": it.meta["state_hash"],
                     "status": o.status, "reason": o.reason, "resolved_model": o.resolved_model,
                     "refusals": o.refusals, "answers": o.answers, "dispatch": decide(o, it, cfg, result.model),
                     **{k: it.meta[k] for k in ("alert_id", "specimen_id", "run", "scenario_id", "tick_min",
                                                "window_min", "route_kind", "p_miss", "p_never", "r1", "r2")}})
    return rows
