"""The pilot gate (docs/neural/spec.md §4.10): which models may write the main run, from the pilot's own items.

    python -m src.neural.pipeline gate --job O1

The pilot sends the same briefs to every model, so each gate compares models pairwise. Per model (arm):

  G1 structure   share of first-attempt responses that completed and passed the full schema (at least 95%)
  G2 acceptance  share of briefs accepted after at most one repair (at least 80%)
  G3 adherence   share of first-attempt responses that passed layers 1 to 4 and also met every must_include
                 predicate (at least 85%). Acceptance itself requires adherence, so "adherent among accepted" is
                 always 100%; this is the share that holds the line the gate is about.
  G4 duplicates  share of accepted scenarios that are near duplicates of an earlier one of the same model and
                 archetype (at most 5%), with delta fitted on every accepted pilot scenario (spec §4.9)
  G5 cost        measured cost per accepted scenario (every attempt counted) against spec §4.5's estimate;
                 above 1.5 times it the main run's counts are recomputed, which is not a failure
  G6 variety     the collapse check of §4.9 in every stratum the model wrote

Model rule: a model is eligible when it passes G1 to G4 and G6. For the core strata, the cheapest eligible model
whose G2 and G3 are within 5 points of the best eligible model's; for the edge stratum, the cheapest eligible model
with G3 of at least 85% on the edge briefs themselves. The pilot passes when both choices exist. Items whose outcome is unknown (sent, never seen)
are left out of the response-based rates and counted.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np

from src.scenarios import runner, signature

CORE = ("normal", "stressed", "disrupted")
STRATA = (*CORE, "edge")
THRESHOLDS = {"G1": 0.95, "G2": 0.80, "G3": 0.85, "G4": 0.05, "G5": 1.5}
CLOSE = 0.05  # G2 and G3 within 5 points of the best
# Spec §4.5's expected cost per scenario at Flex rates with no caching, by arm (source: the spec's estimate).
ESTIMATE_USD = {"luna-low": 0.0012, "terra-low": 0.0135, "terra-medium": 0.0185, "sol-medium": 0.0395}
SCHEMA_LAYERS = {"model", "schema"}


def _cost_by_item(entries: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for e in entries:
        if e["kind"] in ("settle", "adjust") and e.get("item_id"):
            usd = e["micros"] / 1e6 if e.get("micros") is not None else float(e["usd"])
            out[e["item_id"]] = out.get(e["item_id"], 0.0) + usd
    return out


def collect(job: str, paths: runner.Paths | None = None) -> list[dict]:
    """Every item of the job with its arm, attempt, outcome, cost and, when accepted, its scenario."""
    paths = paths or runner.Paths()
    store = runner.ItemStore(paths.items, paths.items_log)
    ledger = runner.ledger_mod.Ledger(paths.ledger, jobs=runner.load_jobs(paths.jobs_config))
    cache = runner.ResponseCache(paths.responses)
    cost = _cost_by_item(ledger.entries(job))
    rows = []
    for item in store.items(job):
        scenario = None
        if item["state"] == "accepted":
            scenario = runner.parse_response(json.loads(cache.read(item["request_hash"]))).scenario
        rows.append({"item_id": item["item_id"], "arm": item["arm"], "brief_id": item["brief_id"],
                     "stratum": item["stratum"], "attempt": item["attempt"], "state": item["state"],
                     "layer": item.get("layer"), "cost_usd": cost.get(item["item_id"], 0.0),
                     "archetype": item["brief"].get("archetype"), "scenario": scenario})
    store.close()
    return rows


def evaluate(rows: list[dict], kpis_fn) -> dict:
    """The gate table and the model rule from collected items; kpis_fn(scenario) -> the simulator's KPI dict."""
    accepted = [r for r in rows if r["state"] == "accepted"]
    kpis = {r["item_id"]: kpis_fn(r["scenario"]) for r in accepted}
    ref = signature.fit_reference([r["scenario"] for r in accepted], [kpis[r["item_id"]] for r in accepted]) \
        if len(accepted) >= 3 else None
    models = {}
    for arm in sorted({r["arm"] for r in rows}):
        mine = [r for r in rows if r["arm"] == arm]
        first = [r for r in mine if r["attempt"] == 1]
        seen = [r for r in first if r["state"] not in ("planned", "reserved", "refused", "unknown", "in_flight")]
        unknown = sum(r["state"] == "unknown" for r in first)
        g1_pass = [r for r in seen if not (r["layer"] in SCHEMA_LAYERS and r["state"] != "accepted")]
        through4 = [r for r in seen if r["state"] == "accepted" or r["layer"] in ("adherence", "near_duplicate")]
        briefs = sorted({r["brief_id"] for r in first})
        acc = [r for r in mine if r["state"] == "accepted"]
        acc_briefs = {r["brief_id"] for r in acc}
        dup = set()
        if ref is not None and len(acc) > 1:
            x = ref.standardize(np.array([signature.signature(r["scenario"], kpis[r["item_id"]], ref) for r in acc]))
            dup = signature.later_duplicates([r["item_id"] for r in acc], x, [r["archetype"] for r in acc], ref.delta)
        spent = sum(r["cost_usd"] for r in mine)
        per_acc = spent / len(acc) if acc else None
        by_stratum = {s: [kpis[r["item_id"]] for r in acc if r["stratum"] == s] for s in STRATA}
        collapse = signature.collapse_check({s: v for s, v in by_stratum.items() if len(v) >= 2})
        g3_edge = [r for r in through4 if r["stratum"] == "edge"]
        g = {
            "G1": len(g1_pass) / len(seen) if seen else None,
            "G2": len(acc_briefs) / len(briefs) if briefs else None,
            "G3": sum(r["state"] == "accepted" for r in through4) / len(through4) if through4 else None,
            "G3_edge": sum(r["state"] == "accepted" for r in g3_edge) / len(g3_edge) if g3_edge else None,
            "G4": len(dup) / len(acc) if acc else None,
            "G5": per_acc / ESTIMATE_USD[arm] if per_acc is not None and arm in ESTIMATE_USD else None,
        }
        passed = {
            "G1": g["G1"] is not None and g["G1"] >= THRESHOLDS["G1"],
            "G2": g["G2"] is not None and g["G2"] >= THRESHOLDS["G2"],
            "G3": g["G3"] is not None and g["G3"] >= THRESHOLDS["G3"],
            "G4": g["G4"] is not None and g["G4"] <= THRESHOLDS["G4"],
            "G5": g["G5"] is not None and g["G5"] <= THRESHOLDS["G5"],
            "G6": not any(v["flagged"] for v in collapse.values()),
        }
        models[arm] = {
            "briefs": len(briefs), "first_attempt_responses": len(seen), "unknown_outcomes": unknown,
            "accepted": len(acc), "accepted_first_attempt": sum(r["attempt"] == 1 for r in acc),
            "near_duplicates": sorted(dup), "spent_usd": round(spent, 6),
            "cost_per_accepted_usd": None if per_acc is None else round(per_acc, 6),
            "estimate_usd": ESTIMATE_USD.get(arm), "rates": g, "passed": passed,
            "eligible": all(passed[k] for k in ("G1", "G2", "G3", "G4", "G6")),
            "collapse": collapse,
            "failures_by_layer": _count([r["layer"] for r in mine if r["state"] in ("repair_planned", "dropped")]),
            "accepted_by_stratum": {s: sum(r["stratum"] == s for r in acc) for s in STRATA},
        }
    choice = choose(models)
    return {"job": rows[0]["item_id"].split("-")[0] if rows else None, "thresholds": THRESHOLDS,
            "delta": None if ref is None else ref.delta, "accepted_total": len(accepted), "models": models,
            "choice": choice, "passed": choice["core"] is not None and choice["edge"] is not None}


def _count(values) -> dict:
    out: dict = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return out


def choose(models: dict) -> dict:
    eligible = {a: m for a, m in models.items() if m["eligible"] and m["cost_per_accepted_usd"] is not None}
    if not eligible:
        return {"core": None, "edge": None, "why": "no model passes G1 to G4 and G6"}
    best_g2 = max(m["rates"]["G2"] for m in eligible.values())
    best_g3 = max(m["rates"]["G3"] for m in eligible.values())
    core_ok = [a for a, m in eligible.items()
               if m["rates"]["G2"] >= best_g2 - CLOSE - 1e-9 and m["rates"]["G3"] >= best_g3 - CLOSE - 1e-9]
    # The edge rule reads G3 on the edge stratum's own briefs, where compound events make adherence hardest.
    edge_ok = [a for a, m in eligible.items()
               if m["rates"]["G3_edge"] is not None and m["rates"]["G3_edge"] >= THRESHOLDS["G3"] - 1e-9]
    cheapest = lambda arms: min(arms, key=lambda a: eligible[a]["cost_per_accepted_usd"]) if arms else None
    return {"core": cheapest(core_ok), "edge": cheapest(edge_ok), "eligible": sorted(eligible),
            "core_candidates": sorted(core_ok), "edge_candidates": sorted(edge_ok),
            "best_g2": best_g2, "best_g3": best_g3}


def markdown(result: dict, spent_total: float, cap: float) -> str:
    pct = lambda v: "-" if v is None else f"{100 * v:.0f}%"
    usd = lambda v: "-" if v is None else f"${v:.4f}"
    lines = [f"# Pilot gate ({result['job']})", "",
             "Synthetic data: laboratory weeks written by language models for our own simulator, with no clinical "
             "review. Every threshold below is an assumption (spec §4.10, Appendix D, A-04).", "",
             f"Spent ${spent_total:.4f} of the ${cap:.2f} pilot cap. {result['accepted_total']} scenarios accepted. "
             f"Near-duplicate distance delta = {result['delta']:.3f}." if result["delta"] is not None else
             f"Spent ${spent_total:.4f} of the ${cap:.2f} pilot cap.", "",
             "| Model | Briefs | Accepted | G1 structure | G2 acceptance | G3 adherence | G3, edge briefs | G4 duplicates | Cost per accepted | G5 vs estimate | G6 variety | Eligible |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for arm, m in result["models"].items():
        r, p = m["rates"], m["passed"]
        mark = lambda k: ("pass" if p[k] else "FAIL")
        lines.append(f"| {arm} | {m['briefs']} | {m['accepted']} | {pct(r['G1'])} {mark('G1')} | {pct(r['G2'])} {mark('G2')} | "
                     f"{pct(r['G3'])} {mark('G3')} | {pct(r['G3_edge'])} | {pct(r['G4'])} {mark('G4')} | {usd(m['cost_per_accepted_usd'])} | "
                     f"{'-' if r['G5'] is None else f'{r['G5']:.2f}x'} {'ok' if p['G5'] else 'recount'} | "
                     f"{mark('G6')} | {'yes' if m['eligible'] else 'no'} |")
    c = result["choice"]
    lines += ["", f"**Model rule.** Core strata (normal, stressed, disrupted): **{c.get('core')}**. "
                  f"Edge stratum: **{c.get('edge')}**. Gate {'passed' if result['passed'] else 'FAILED'}."]
    if c.get("why"):
        lines.append(c["why"])
    lines += ["", "Failures by validation layer (attempts that needed a repair or were dropped):", ""]
    for arm, m in result["models"].items():
        lines.append(f"- {arm}: {m['failures_by_layer'] or 'none'}; unknown outcomes {m['unknown_outcomes']}")
    lines += ["", f"Written {datetime.now().astimezone().isoformat(timespec='minutes')} by "
                  "`python -m src.neural.pipeline gate --job O1`."]
    return "\n".join(lines) + "\n"


def write(result: dict, spent_total: float, cap: float, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    js, md = out_dir / "pilot.json", out_dir / "pilot.md"
    slim = json.loads(json.dumps(result))
    js.write_text(json.dumps(slim | {"spent_usd": round(spent_total, 6), "cap_usd": cap}, indent=1) + "\n",
                  encoding="utf-8")
    md.write_text(markdown(result, spent_total, cap), encoding="utf-8")
    return js, md
