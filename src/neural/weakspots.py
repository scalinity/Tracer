"""Round 2's weak spots and the O3 brief plan (docs/neural/spec.md §4.12; stage S15).

    python -m src.neural.pipeline weakspots [--rows data/neural/weakspots/val-rows.parquet]
        [--risk 'data/neural/risk/val/*.parquet'] [--evaluation results/neural/val/evaluation.json]
        [--store data/store-v2] [--out results/neural/generation] [--force]

What it reads, validation only:

  evaluation  the validation evaluation with TracerNet (`evaluate --split val --artifact ...`); a file of another
              set, or one without TracerNet, is refused. Its slices give the median warnings.
  risk        TracerNet's validation RiskTable (p_miss at every tick of every specimen), which that evaluation writes
  rows        one row per scored validation specimen with a clock: specimen_id, scenario_id, run, breach,
              window_min, receipt_min (null when never received), route_kind, archetype, stratum, edge_tags (a
              list) and disruption_types (a list: the types of the disruptions that touched the specimen). The
              evaluation side writes it from the labels; this module never opens the label store.
  store       the observed store: every scenario in the rows and in the risk table must be one of its validation
              scenarios, or nothing is computed

Steps (spec §4.12):

  2. Every slice of six dimensions (archetype by stratum, disruption type, route kind, window class, volume tercile,
     edge tag) with at least 30 validation breaches gets TracerNet's miss log loss (val_sel's definition, spec §7.7:
     ticks up to min(window, 240 minutes), averaged per specimen, then over the slice's specimens), the log loss of
     the slice's own base rate (the binary entropy of its breach share), their difference (the excess: below zero,
     TracerNet beats the base rate there), and TracerNet's median warning over all breaches minus the best
     baseline's at 6 alerts per shift and 3-2-1 weights, read from the evaluation's slices where it has the slice.
  3. Slices rank by excess times the slice's share of validation breaches, highest first. The top four that the
     planner can target are chosen; a slice no brief factor can produce is listed and passed over.
  4. The O3 targets: the four slices (equal shares of the 60%), and the manifest's cells and edge tags below their
     coverage targets (the 15%); the planner adds the 25% of general edge briefs. The count is Appendix B's formula,
     the one O2's count used: accepted = floor(cap x 0.95 / cost per accepted scenario), briefs = ceil(accepted /
     acceptance). O3 sends its strata to different models, so each model's measured cost and acceptance is weighted
     by the share of the briefs its strata get, read from the planned briefs themselves.
  6. round2.md names the slices, the numbers that chose them and the spend; round2-plan.json holds the targets and
     the count that `plan --job O3` reads (results/neural/generation/; data/neural/dev-generation/ for any other
     store unless --out is given).
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import yaml

from src.neural import stages
from src.pipeline import store
from src.scenarios import planner, runner

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "neural.yaml"
GENERATION_DIR = ROOT / "results" / "neural" / "generation"
DEV_DIR = ROOT / "data" / "neural" / "dev-generation"
ROWS_PATH = ROOT / "data" / "neural" / "weakspots" / "val-rows.parquet"
EVALUATION_PATH = ROOT / "results" / "neural" / "val" / "evaluation.json"
RISK_GLOB = ROOT / "data" / "neural" / "risk" / "val" / "*.parquet"
MANIFEST_PATH = ROOT / "datasets" / "scenarios-v2.manifest.json"
PLAN_NAME, REPORT_NAME = "round2-plan.json", "round2.md"
TRACERNET = "tracernet"
JOB = "O3"
EPS = 1e-7                     # p_miss is clipped to [EPS, 1 - EPS] inside the log loss
ROW_COLUMNS = ("specimen_id", "scenario_id", "run", "breach", "window_min", "receipt_min", "route_kind", "archetype",
               "stratum", "edge_tags", "disruption_types")
DIMENSIONS = {"archetype_stratum": "archetype by stratum", "disruption_type": "disruption type",
              "route_kind": "route kind", "window_class": "window class", "volume_tercile": "volume tercile",
              "edge_tag": "edge tag"}
EVALUATION_SLICE = {"route_kind": "route_kind", "window_class": "window_class", "volume_tercile": "volume_tercile",
                    "edge_tag": "edge_tag"}       # the evaluation slices archetype and stratum apart, disruption as present
NOTICE = ("Synthetic data: laboratory weeks designed by language models and simulated by this project's code, with no "
          "clinical review. Every number below is a validation number from that simulator.")


class WeakSpotError(RuntimeError):
    """An input that is missing, inconsistent or not validation."""


def load_config(path: Path = CONFIG_PATH) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))["weakspots"]


def _risk_files(risk) -> list[Path]:
    p = Path(risk)
    found = sorted(p.parent.glob(p.name)) if any(c in p.name for c in "*?[") else [p]
    found = [f for f in found if f.is_file()]
    if not found:
        raise WeakSpotError(f"no risk table at {risk}; run `evaluate --split val --artifact ...` first")
    return found


# --- Validation only -------------------------------------------------------------------------------------------------

def val_scenarios(store_dir: Path) -> set[str]:
    con = store.open_observed_v2(store_dir)
    try:
        return {r[0] for r in con.execute("select distinct scenario_id from runs where split = 'val'").fetchall()}
    finally:
        con.close()


def check_validation(evaluation: dict, row_scenarios: set, risk_scenarios: set, allowed: set) -> None:
    """Refuse anything but validation: an evaluation of another set or without TracerNet, and rows or a risk table
    holding a scenario that is not a validation scenario of the store. Called on the files' scenario ids alone,
    before any other value of the rows or the risk table is read."""
    if evaluation.get("set") != "val":
        raise WeakSpotError(f"the evaluation is of {evaluation.get('set')!r}; weak spots read validation only")
    if TRACERNET not in (evaluation.get("methods") or {}):
        raise WeakSpotError("the validation evaluation has no TracerNet; run `evaluate --split val --artifact ...`")
    for name, found in (("rows", set(row_scenarios)), ("risk table", set(risk_scenarios))):
        outside = sorted(found - set(allowed))
        if outside:
            raise WeakSpotError(f"the {name} holds {len(outside)} scenarios that are not validation scenarios of the "
                                f"store ({', '.join(outside[:3])}); weak spots read validation only")
    clusters = (evaluation.get("counts") or {}).get("clusters")
    if clusters is not None and clusters != len(set(row_scenarios)):
        raise WeakSpotError(f"the rows hold {len(set(row_scenarios))} scenarios and the evaluation {clusters}: "
                            "they describe different validation sets")


# --- Step 2: slices --------------------------------------------------------------------------------------------------

def entropy(b: float) -> float:
    """The log loss of always predicting the base rate b on outcomes whose share is b."""
    if b <= 0.0 or b >= 1.0:
        return 0.0
    return -(b * math.log(b) + (1.0 - b) * math.log(1.0 - b))


def specimen_losses(rows: pl.DataFrame, risk: pl.LazyFrame, max_tick: float) -> pl.DataFrame:
    """Per scored specimen with a tick: TracerNet's mean miss log loss over its ticks up to min(window, max_tick)
    (val_sel's definition), with its label. Refuses a risk table that lacks a specimen that has a tick."""
    keyed = rows.select("specimen_id", "breach", "window_min").lazy()
    per = (risk.select("specimen_id", "tick_min", "p_miss")
           .join(keyed, on="specimen_id", how="inner")
           .filter(pl.col("tick_min") <= pl.min_horizontal(pl.col("window_min"), pl.lit(float(max_tick))))
           .with_columns(p=pl.col("p_miss").clip(EPS, 1.0 - EPS))
           .with_columns(loss=pl.when(pl.col("breach")).then(-pl.col("p").log()).otherwise(-(1.0 - pl.col("p")).log()))
           .group_by("specimen_id").agg(pl.col("loss").mean(), pl.col("breach").first())
           .collect(engine="streaming"))
    ticked = rows.filter((pl.col("window_min") >= 5) & (pl.col("receipt_min").is_null() | (pl.col("receipt_min") > 5)))
    missing = ticked.height - ticked.join(per.select("specimen_id"), on="specimen_id", how="semi").height
    if missing:
        raise WeakSpotError(f"the risk table lacks {missing} scored validation specimens that have a tick")
    return per.sort("specimen_id")


def slice_keys(rows: pl.DataFrame, dimension: str) -> pl.DataFrame:
    """(specimen_id, key) for one dimension. Edge tags and disruption types can put a specimen in several slices,
    or (no disruption) in none; a scenario without edge tags is the slice "none", as in the evaluation."""
    from src.pipeline import neural_eval

    if dimension == "archetype_stratum":
        return rows.select("specimen_id", key=pl.concat_str(["archetype", "stratum"], separator=" / "))
    if dimension == "disruption_type":
        return rows.select("specimen_id", key=pl.col("disruption_types")).explode("key").drop_nulls("key")
    if dimension == "route_kind":
        return rows.select("specimen_id", key=pl.col("route_kind"))
    if dimension == "window_class":
        labels = {w: neural_eval.window_class(SimpleNamespace(window=w)) for w in rows["window_min"].unique().to_list()}
        return rows.select("specimen_id", key=pl.col("window_min").replace_strict(labels, return_dtype=pl.Utf8))
    if dimension == "volume_tercile":   # scored specimens with a clock per replicate, as the evaluation's terciles
        volume = rows.group_by("scenario_id").agg((pl.len() / pl.col("run").n_unique()).alias("v"))
        labels = neural_eval.tercile_labels(dict(zip(volume["scenario_id"].to_list(), volume["v"].to_list())))
        return rows.select("specimen_id", key=pl.col("scenario_id").replace_strict(labels, return_dtype=pl.Utf8))
    if dimension == "edge_tag":
        tags = pl.when(pl.col("edge_tags").list.len() > 0).then(pl.col("edge_tags")).otherwise(pl.lit(["none"]))
        return rows.select("specimen_id", key=tags).explode("key")
    raise ValueError(f"unknown dimension {dimension!r}; one of {sorted(DIMENSIONS)}")


def slice_table(losses: pl.DataFrame, rows: pl.DataFrame, dimensions, min_breaches: int) -> list[dict]:
    """Every slice of every dimension: specimens, breaches, base rate, TracerNet's and the base rate's log loss,
    the excess, the share of all validation breaches, the score (excess x share) and whether it has enough
    breaches to be ranked."""
    total = int(losses["breach"].sum())
    if total == 0:
        raise WeakSpotError("the validation specimens have no breach")
    out = []
    for d in dimensions:
        g = (slice_keys(rows, d).join(losses, on="specimen_id", how="inner")
             .group_by("key").agg(pl.len().alias("specimens"), pl.col("breach").sum().alias("breaches"),
                                  pl.col("loss").mean().alias("loss"))
             .sort("key"))
        for r in g.iter_rows(named=True):
            base_rate = r["breaches"] / r["specimens"]
            base = entropy(base_rate)
            excess = float(r["loss"]) - base
            share = r["breaches"] / total
            out.append({"dimension": d, "slice": str(r["key"]), "specimens": int(r["specimens"]),
                        "breaches": int(r["breaches"]), "base_rate": base_rate, "tracernet_log_loss": float(r["loss"]),
                        "base_rate_log_loss": base, "excess": excess, "breach_share": share, "score": excess * share,
                        "ranked": int(r["breaches"]) >= min_breaches})
    return out


def warning_gap(evaluation: dict, dimension: str, key: str, budget: str, weighting: str) -> dict:
    """TracerNet's median warning over all breaches minus the best baseline's in one slice, from the evaluation's
    slices (computed at its headline budget). Unavailable, with the reason, where the evaluation lacks the slice."""
    from src.pipeline import neural_eval

    metric = "median_warning_all_breaches_min"
    if dimension not in EVALUATION_SLICE:
        why = ("the evaluation slices archetype and stratum separately" if dimension == "archetype_stratum" else
               "the evaluation slices disruption as present or absent, not by type")
        return {"available": False, "why": why}
    if (evaluation.get("settings") or {}).get("headline") != budget:
        return {"available": False, "why": f"the evaluation's slices are not at {budget}"}
    group = (evaluation.get("slices") or {}).get(EVALUATION_SLICE[dimension], {}).get(key)
    if group is None:
        return {"available": False, "why": "the evaluation has no such slice"}
    methods = evaluation.get("methods") or {}

    def value(name):
        w = weighting if (methods.get(name) or {}).get("weighted", True) else neural_eval.UNWEIGHTED
        return ((group.get(name) or {}).get(w) or {}).get(metric)

    tn = value(TRACERNET)
    found = [(name, value(name)) for name in neural_eval.BASELINES if value(name) is not None]
    if tn is None or not found:
        return {"available": False, "why": "the slice has no median warning for TracerNet or the baselines"}
    best, best_value = max(found, key=lambda x: x[1])   # the first of equals, in the baselines' order
    return {"available": True, "tracernet_min": tn, "best_baseline": best, "best_baseline_min": best_value,
            "gap_min": tn - best_value}


# --- Step 3: ranking and the planner's pins --------------------------------------------------------------------------

def pins_for(dimension: str, key: str, cfg: dict, seed: int) -> tuple[dict | None, str | None]:
    """The planner target that produces more of a slice, or None and why not."""
    if dimension == "archetype_stratum":
        archetype, stratum = key.split(" / ")
        pins = {"archetype": archetype, "stratum": stratum}
    elif dimension == "disruption_type":
        if key not in planner.FOCUS_OF_DISRUPTION:
            return None, "no failure focus produces this disruption type in round 2"
        pins = {"disruption_type": key}
    elif dimension == "route_kind":
        if key not in cfg["transport_of_route_kind"]:
            return None, "no transport mix is dominated by this route kind"
        pins = {"transport_mix": cfg["transport_of_route_kind"][key]}
    elif dimension == "volume_tercile":
        pins = {"size_band": cfg["size_of_volume_tercile"][key]}
    elif dimension == "edge_tag":
        if key == "none":
            return None, "a week without edge tags is not an edge-tag target"
        pins = {"edge_tag": key}
    elif dimension == "window_class":
        return None, "no brief factor sets a specimen's window"
    else:
        raise ValueError(f"unknown dimension {dimension!r}")
    try:
        planner.plan(JOB, seed, n=10, targets=[pins | {"count": 1}])
    except ValueError as e:
        return None, f"the planner cannot target it: {e}"
    return pins, None


def rank(slices: list[dict]) -> list[dict]:
    """The ranked slices (enough breaches), highest excess x share first. Scores are compared to 12 decimals, so two
    slices holding the same specimens tie whatever order their sums ran in; ties go in the dimensions' order."""
    order = list(DIMENSIONS)
    return sorted((s for s in slices if s["ranked"]),
                  key=lambda s: (-round(s["score"], 12), order.index(s["dimension"]), s["slice"]))


def choose(ranked: list[dict], cfg: dict, seed: int) -> tuple[list[dict], list[dict]]:
    """The top `cfg["top"]` slices the planner can target, and the higher-ranked ones passed over (with why)."""
    chosen, passed = [], []
    for s in ranked:
        if len(chosen) == int(cfg["top"]):
            break
        pins, why = pins_for(s["dimension"], s["slice"], cfg, seed)
        if pins is None:
            passed.append(s | {"why": why})
        else:
            chosen.append(s | {"pins": pins})
    if not chosen:
        raise WeakSpotError("no ranked slice can be targeted by the planner")
    return chosen, passed


# --- Step 4: the O3 targets and count -------------------------------------------------------------------------------

def under_covered(manifest: dict | None) -> list[dict]:
    """The library's stratum-by-archetype cells and edge tags below their coverage targets (none out of
    distribution, which round 2 never plans), the largest deficit first."""
    coverage = (manifest or {}).get("coverage") or {}
    cfg = planner.config()
    ood = cfg["ood"]["archetype"]
    tags = {t for t, r in cfg["edge_tags"].items() if not r.get("ood_only")}
    found = []
    for key, c in sorted((coverage.get("cells") or {}).items()):
        stratum, archetype = key.split("/")
        if archetype != ood and c["count"] < c["target"]:
            found.append({"pins": {"stratum": stratum, "archetype": archetype}, "count": c["count"],
                          "target": c["target"], "deficit": c["target"] - c["count"]})
    for tag, c in sorted((coverage.get("edge_tags") or {}).items()):
        if tag in tags and c["count"] < c["target"]:
            found.append({"pins": {"edge_tag": tag}, "count": c["count"], "target": c["target"],
                          "deficit": c["target"] - c["count"]})
    return sorted(found, key=lambda c: (-c["deficit"], json.dumps(c["pins"], sort_keys=True)))


def targets_of(chosen: list[dict], cover: list[dict]) -> list[dict]:
    """The planner's `targets`: each chosen slice at an equal share of the slice briefs, then the under-covered
    cells (without them the planner gives the 15% to general non-edge briefs)."""
    out = [dict(s["pins"]) | {"count": 1} for s in chosen]
    if cover:
        out.append({"under_covered": [dict(c["pins"]) for c in cover]})
    return out


def measured_costs(manifest: dict | None, pilot: dict | None, jobs_cfg: dict, job: str = JOB) -> dict:
    """Each arm the job uses: its measured cost per accepted scenario (everything spent, repairs and failures
    included, over the scenarios accepted) and its acceptance (accepted over briefs). From the largest frozen job
    that sent every brief to that arm alone (the library manifest), else from the pilot gate's measurement."""
    arms = sorted({a for arms in jobs_cfg["jobs"][job]["arms"].values() for a in arms})
    out = {}
    for arm in arms:
        found = None
        for name, j in sorted(((manifest or {}).get("jobs") or {}).items()):
            c = j.get("counts") or {}
            if {tuple(v) for v in (j.get("arms") or {}).values()} == {(arm,)} and c.get("accepted") and c.get("briefs"):
                if found is None or c["briefs"] > found["briefs"]:
                    found = {"cost_per_accepted_usd": j["spent_usd"] / c["accepted"],
                             "acceptance": c["accepted"] / c["briefs"], "briefs": c["briefs"],
                             "source": f"library manifest, job {name}: ${j['spent_usd']:.6f} spent, "
                                       f"{c['accepted']:,} accepted of {c['briefs']:,} briefs"}
        if found is None:
            m = ((pilot or {}).get("models") or {}).get(arm) or {}
            if m.get("accepted") and m.get("briefs") and m.get("cost_per_accepted_usd"):
                found = {"cost_per_accepted_usd": float(m["cost_per_accepted_usd"]),
                         "acceptance": m["accepted"] / m["briefs"], "briefs": m["briefs"],
                         "source": f"pilot gate (results/neural/generation/pilot.json): "
                                   f"${m['cost_per_accepted_usd']:.6f} per accepted, {m['accepted']} accepted of "
                                   f"{m['briefs']} briefs"}
        if found is None:
            raise WeakSpotError(f"no measured cost for {arm}: neither the library manifest nor the pilot gate has it")
        out[arm] = found
    return out


def count(targets: list[dict], *, jobs_cfg: dict, measured: dict, cap_share: float, n0: int | None = None,
          job: str = JOB) -> dict:
    """Appendix B's count for O3. The briefs at the planner's default size give each stratum's share; each
    stratum's arms give its spend and accepted scenarios per brief; then accepted = floor(cap x cap_share / cost
    per accepted) and briefs = ceil(accepted / accepted per brief)."""
    j = jobs_cfg["jobs"][job]
    cap, seed = float(j["cap_usd"]), int(j["seed"])
    n0 = int(n0 if n0 is not None else planner.config()["jobs"][job]["total"])
    mix = Counter(b["stratum"] for b in planner.plan(job, seed, n=n0, targets=targets))
    share = {s: mix[s] / n0 for s in sorted(mix)}
    arm_share: dict[str, float] = {}
    for s, f in share.items():
        for a in j["arms"][s]:
            arm_share[a] = arm_share.get(a, 0.0) + f
    accepted_per_brief = sum(f * measured[a]["acceptance"] for a, f in arm_share.items())
    spend_per_brief = sum(f * measured[a]["cost_per_accepted_usd"] * measured[a]["acceptance"]
                          for a, f in arm_share.items())
    cost_per_accepted = spend_per_brief / accepted_per_brief
    accepted = int(math.floor(cap * cap_share / cost_per_accepted))
    briefs = int(math.ceil(accepted / accepted_per_brief))
    return {"cap_usd": cap, "cap_share": cap_share, "seed": seed, "provisional_briefs": n0,
            "stratum_share": share, "arm_share": arm_share, "accepted_per_brief": accepted_per_brief,
            "spend_per_brief_usd": spend_per_brief, "cost_per_accepted_usd": cost_per_accepted,
            "accepted": accepted, "briefs": briefs, "expected_spend_usd": briefs * spend_per_brief,
            "formula": "accepted = floor(cap x cap_share / cost per accepted); briefs = ceil(accepted / acceptance)"}


def plan_summary(targets: list[dict], n: int, *, jobs_cfg: dict, measured: dict, job: str = JOB) -> dict:
    """The briefs `plan --job O3` will write: counts by origin and stratum, and their expected spend."""
    j = jobs_cfg["jobs"][job]
    briefs = planner.plan(job, int(j["seed"]), n=n, targets=targets)
    origin = Counter(b["origin"].split(":")[0] for b in briefs)
    by_slice = Counter(b["origin"] for b in briefs if b["origin"].startswith("slice"))
    strata = Counter(b["stratum"] for b in briefs)
    spend = sum(measured[a]["cost_per_accepted_usd"] * measured[a]["acceptance"]
                for b in briefs for a in j["arms"][b["stratum"]])
    return {"briefs": len(briefs), "by_origin": dict(sorted(origin.items())), "by_slice": dict(sorted(by_slice.items())),
            "by_stratum": dict(sorted(strata.items())), "expected_spend_usd": spend,
            "ood_briefs": sum(1 for b in briefs if b["ood"])}


# --- Step 6: the record and the page ---------------------------------------------------------------------------------

def _num(x, digits: int = 4) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def _gap_cell(g: dict) -> str:
    if not g["available"]:
        return f"n/a ({g['why']})"
    return (f"{g['gap_min']:+.1f} min (TracerNet {g['tracernet_min']:.1f}, best baseline {g['best_baseline']} "
            f"{g['best_baseline_min']:.1f})")


def render(doc: dict) -> str:
    c, s = doc["count"], doc["plan"]
    lines = ["# Round 2 (O3): weak spots on validation", "", NOTICE, "",
             f"Read validation only: {doc['inputs']['evaluation']['path']} (set `{doc['inputs']['evaluation']['set']}`, "
             f"{doc['inputs']['rows']['scenarios']:,} scenarios, {doc['inputs']['rows']['specimens']:,} scored specimens "
             f"with a clock, {doc['inputs']['rows']['breaches']:,} breaches with a tick), TracerNet's validation risk "
             f"table ({doc['inputs']['risk']['parts']} parts) and the observed store's validation split. No test set "
             "was read.", "",
             "## How the slices were chosen (spec §4.12)", "",
             f"- Miss log loss: TracerNet's p_miss against the breach label at every tick up to the smaller of the "
             f"window and {doc['settings']['max_tick_min']:g} minutes, averaged per specimen, then over the slice's "
             "specimens (val_sel's definition, spec §7.7).",
             "- Excess: that log loss minus the log loss of the slice's own base rate (the binary entropy of its "
             "breach share). Below zero, TracerNet beats the base rate in the slice.",
             f"- Ranked: slices with at least {doc['settings']['min_breaches']} breaches, by excess times the slice's "
             "share of all validation breaches, highest first.",
             f"- Warning gap: TracerNet's median warning over all breaches (a miss counting as zero) minus the best "
             f"baseline's, at {doc['settings']['budget']} and {doc['settings']['weighting']} weights, from the "
             "evaluation's slices. Reported, not ranked on.", "",
             "## The four slices", "",
             "| Rank | Dimension | Slice | Breaches | Share of breaches | Base rate | TracerNet log loss | Base-rate log loss | Excess | Excess x share | Warning gap | Planned as |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for x in doc["chosen"]:
        lines.append(f"| {x['rank']} | {DIMENSIONS[x['dimension']]} | {x['slice']} | {x['breaches']:,} | "
                     f"{x['breach_share']:.4f} | {x['base_rate']:.4f} | {x['tracernet_log_loss']:.4f} | "
                     f"{x['base_rate_log_loss']:.4f} | {x['excess']:+.4f} | {x['score']:+.5f} | "
                     f"{_gap_cell(x['warning_gap'])} | `{json.dumps(x['pins'], sort_keys=True)}` |")
    if doc["ranking"] and all(x["excess"] < 0 for x in doc["ranking"]):
        lines += ["", "Every ranked slice has an excess below zero: TracerNet beats each slice's base rate. Excess x "
                      "share is then closest to zero, and ranks first, for slices with a small share of the breaches; "
                      "the rule (spec §4.12 step 3) is applied as written."]
    if doc["passed_over"]:
        lines += ["", "Ranked above a chosen slice but not plannable:", ""]
        lines += [f"- Rank {x['rank']}, {DIMENSIONS[x['dimension']]} `{x['slice']}` (excess x share "
                  f"{x['score']:+.5f}): {x['why']}." for x in doc["passed_over"]]
    lines += ["", "## Every ranked slice", "",
              "| Rank | Dimension | Slice | Specimens | Breaches | Share | Base rate | Excess | Excess x share | Warning gap |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for x in doc["ranking"]:
        lines.append(f"| {x['rank']} | {DIMENSIONS[x['dimension']]} | {x['slice']} | {x['specimens']:,} | "
                     f"{x['breaches']:,} | {x['breach_share']:.4f} | {x['base_rate']:.4f} | {x['excess']:+.4f} | "
                     f"{x['score']:+.5f} | {_gap_cell(x['warning_gap'])} |")
    if doc["unranked"]:
        lines += ["", f"Slices with fewer than {doc['settings']['min_breaches']} breaches (not ranked): "
                  + ", ".join(f"{DIMENSIONS[x['dimension']]} `{x['slice']}` ({x['breaches']})" for x in doc["unranked"])
                  + "."]
    lines += ["", "## The O3 plan", "",
              f"Targets: the four slices at equal shares of the {doc['shares']['slices']:.0%} of slice briefs, "
              f"{doc['shares']['general_edge']:.0%} general edge briefs and {doc['shares']['under_covered']:.0%} for "
              + (f"{len(doc['under_covered'])} cells and edge tags below their coverage targets in the library "
                 "manifest (largest deficit first: "
                 + ", ".join(f"`{json.dumps(u['pins'], sort_keys=True)}` {u['count']} of {u['target']}"
                             for u in doc["under_covered"]) + ")."
                 if doc["under_covered"] else "general non-edge briefs (no cell or edge tag is below its coverage "
                                               "target in the library manifest)."),
              "", "Measured cost and acceptance per model arm:", ""]
    for arm, m in sorted(doc["measured"].items()):
        lines.append(f"- `{arm}`: ${m['cost_per_accepted_usd']:.6f} per accepted scenario, acceptance "
                     f"{m['acceptance']:.4f} ({m['source']}).")
    lines += ["", f"Count (spec Appendix B, the formula O2's count used): the {c['provisional_briefs']} briefs the planner "
              "makes at its default size give the strata's shares ("
              + ", ".join(f"{k} {v:.4f}" for k, v in c["stratum_share"].items()) + "), so the arms' shares ("
              + ", ".join(f"`{k}` {v:.4f}" for k, v in sorted(c["arm_share"].items())) + "). Spend per brief "
              f"${c['spend_per_brief_usd']:.6f} and accepted per brief {c['accepted_per_brief']:.4f} give "
              f"${c['cost_per_accepted_usd']:.6f} per accepted scenario; accepted = floor(${c['cap_usd']:.2f} x "
              f"{c['cap_share']} / ${c['cost_per_accepted_usd']:.6f}) = {c['accepted']:,}; briefs = ceil({c['accepted']:,} / "
              f"{c['accepted_per_brief']:.4f}) = {c['briefs']:,}.", "",
              f"The briefs `plan --job O3` will write (seed {c['seed']}): {s['briefs']:,} in all, "
              + ", ".join(f"{k} {v:,}" for k, v in s["by_origin"].items()) + " by origin; "
              + ", ".join(f"{k} {v:,}" for k, v in s["by_stratum"].items()) + f" by stratum; {s['ood_briefs']} out of "
              "distribution.", "",
              "## Spend", "",
              f"- O3 cap: ${c['cap_usd']:.2f}. Expected spend of these briefs at the measured costs: "
              f"${s['expected_spend_usd']:.2f}. The ledger's cap stops spending first whatever the estimate.",
              f"- Spent before O3 ({doc['spend']['source']}): "
              + (", ".join(f"{k} ${v:.4f}" for k, v in sorted(doc["spend"]["by_job"].items()))
                 + f"; ${doc['spend']['total']:.4f} in all." if doc["spend"]["by_job"] else "not available.")]
    return "\n".join(lines) + "\n"


def analyze(rows: pl.DataFrame, risk: pl.LazyFrame, evaluation: dict, *, cfg: dict, jobs_cfg: dict,
            manifest: dict | None, pilot: dict | None) -> dict:
    """Steps 2 to 4 on inputs already checked as validation. Returns the plan record (round2-plan.json)."""
    missing = [c for c in ROW_COLUMNS if c not in rows.columns]
    if missing:
        raise WeakSpotError(f"the rows lack the columns {missing}")
    seed = int(jobs_cfg["jobs"][JOB]["seed"])
    losses = specimen_losses(rows, risk, float(cfg["max_tick_min"]))
    slices = slice_table(losses, rows, cfg["dimensions"], int(cfg["min_breaches"]))
    for x in slices:
        x["warning_gap"] = warning_gap(evaluation, x["dimension"], x["slice"], cfg["budget"], cfg["weighting"])
    ranked = rank(slices)
    for i, x in enumerate(ranked, 1):
        x["rank"] = i
    chosen, passed = choose(ranked, cfg, seed)
    cover = under_covered(manifest)
    targets = targets_of(chosen, cover)
    measured = measured_costs(manifest, pilot, jobs_cfg)
    c = count(targets, jobs_cfg=jobs_cfg, measured=measured, cap_share=float(cfg["cap_share"]))
    summary = plan_summary(targets, c["briefs"], jobs_cfg=jobs_cfg, measured=measured)
    return {"job": JOB, "targets": targets, "briefs": c["briefs"], "count": c, "plan": summary,
            "measured": measured, "chosen": chosen, "passed_over": passed, "under_covered": cover,
            "ranking": ranked, "unranked": [x for x in slices if not x["ranked"]],
            "shares": dict(planner.config()["round2_shares"]),
            "settings": {k: cfg[k] for k in ("dimensions", "min_breaches", "top", "max_tick_min", "budget",
                                             "weighting", "cap_share")},
            "breaches": int(losses["breach"].sum()), "notice": NOTICE}


def spend_so_far(manifest: dict | None, ledger_path: Path | None = None) -> dict:
    """OpenAI spend per job from the ledger's append-only mirror (read, never opened for writing), else the
    library manifest's record of the ledger at the freeze."""
    from src.neural import report

    found = report.ledger_totals(ledger_path)
    if found is not None:
        jobs = {j: v["spent_usd"] for j, v in found["jobs"].items() if v["account"] == "openai" and v["spent_usd"]}
        return {"source": found["source"], "by_job": jobs, "total": sum(jobs.values())}
    snap = (manifest or {}).get("ledger") or {}
    jobs = dict(snap.get("spent_usd_by_job") or {})
    return {"source": "the library manifest's ledger record at the freeze", "by_job": jobs,
            "total": float(snap.get("spent_usd_total", sum(jobs.values())))}


def _read_json(path: Path) -> dict | None:
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else None


def weakspots_command(*, rows_path: Path | None = None, risk: str | Path | None = None,
                      evaluation_path: Path | None = None, store_dir: Path | None = None, out_dir: Path | None = None,
                      manifest_path: Path = MANIFEST_PATH, pilot_path: Path | None = None,
                      jobs_path: Path | None = None, config_path: Path = CONFIG_PATH,
                      ledger_path: Path | None = None, stages_dir: Path | None = None, force: bool = False,
                      out=print) -> int:
    """`weakspots`: check that every input is validation, rank the slices, plan O3 and write round2.md and
    round2-plan.json. Reruns only when an input changed (or with --force)."""
    paths = runner.Paths()
    rows_path = Path(rows_path or ROWS_PATH)
    evaluation_path = Path(evaluation_path or EVALUATION_PATH)
    store_dir = Path(store_dir or store.STORE_V2_DIR)
    real = store_dir.resolve() == Path(store.STORE_V2_DIR).resolve()
    out_dir = Path(out_dir) if out_dir is not None else (GENERATION_DIR if real else DEV_DIR)
    pilot_path = Path(pilot_path or paths.pilot_gate)
    jobs_path = Path(jobs_path or paths.jobs_config)
    for label, p in (("rows", rows_path), ("validation evaluation", evaluation_path)):
        if not p.exists():
            raise WeakSpotError(f"no {label} at {p}")
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    files = _risk_files(risk or RISK_GLOB)

    def scenarios_in(source) -> set:
        return set(pl.scan_parquet(source).select("scenario_id").unique().collect()["scenario_id"].to_list())
    check_validation(evaluation, scenarios_in(rows_path), scenarios_in(files), val_scenarios(store_dir))
    rows = pl.read_parquet(rows_path)
    cfg = load_config(config_path)
    inputs = {"evaluation": stages.file_hash(evaluation_path), "rows": stages.file_hash(rows_path),
              "risk": {f.name: stages.file_hash(f) for f in files}, "config": stages.obj_hash(cfg),
              "manifest": stages.file_hash(manifest_path), "pilot": stages.file_hash(pilot_path),
              "jobs": stages.file_hash(jobs_path), "planner": stages.file_hash(planner.CONFIG_PATH),
              "out": str(out_dir)}

    def fn():
        manifest = _read_json(manifest_path)
        doc = analyze(rows, pl.scan_parquet(files), evaluation, cfg=cfg, jobs_cfg=runner.load_jobs(jobs_path),
                      manifest=manifest, pilot=_read_json(pilot_path))
        doc["spend"] = spend_so_far(manifest, ledger_path)
        doc["inputs"] = {"evaluation": {"path": _shown(evaluation_path), "set": evaluation["set"],
                                        "sha256": stages.file_hash(evaluation_path)},
                         "rows": {"path": _shown(rows_path), "sha256": inputs["rows"],
                                  "scenarios": rows["scenario_id"].n_unique(), "specimens": rows.height,
                                  "breaches": doc["breaches"]},
                         "risk": {"parts": len(files), "sha256": inputs["risk"]},
                         "manifest": inputs["manifest"], "pilot": inputs["pilot"], "jobs": inputs["jobs"]}
        out_dir.mkdir(parents=True, exist_ok=True)
        plan_path, md_path = out_dir / PLAN_NAME, out_dir / REPORT_NAME
        plan_path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        md_path.write_text(render(doc), encoding="utf-8")
        return {"plan": plan_path, "report": md_path}, doc

    ran, doc, reason = stages.run_stage(Path(stages_dir or paths.stages), "weakspots", inputs, fn, force=force)
    if not ran:
        out(f"weakspots: up to date ({out_dir / PLAN_NAME})")
        return 0
    for x in doc["chosen"]:
        out(f"slice {x['rank']}: {DIMENSIONS[x['dimension']]} {x['slice']}: excess {x['excess']:+.4f} x share "
            f"{x['breach_share']:.4f} = {x['score']:+.5f}")
    out(f"O3: {doc['briefs']:,} briefs (expected ${doc['plan']['expected_spend_usd']:.2f} of the "
        f"${doc['count']['cap_usd']:.2f} cap) -> {out_dir / PLAN_NAME} and {out_dir / REPORT_NAME} [{reason}]")
    return 0


def _shown(path: Path) -> str:
    p = Path(path).resolve()
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)
