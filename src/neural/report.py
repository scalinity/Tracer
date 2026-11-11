"""The v2 report, results/neural/REPORT.md (docs/neural/spec.md §8.9, §8.10; stage S21).

    python -m src.neural.pipeline report [--out results/neural/REPORT.md] [--ledger-json PATH]

It renders the files the earlier stages wrote and nothing else:

  results/neural/<set>/evaluation.json     val, test, test_ood, legacy_v1, control_v1world (`evaluate --split`)
  results/neural/test_ood_feature/evaluation.json   test_ood on the scenarios that carry the out-of-distribution
                                           feature (archetype multi_site_system or a scanner_downtime disruption),
                                           or a `subsets.ood_feature` entry inside the test_ood evaluation
  results/neural/decisions/<job>-<set>.json and -scores.json   the decision layer's run summaries and scores (X1 on
                                           the library, with its pilot's and its paid run's summaries; X2 on
                                           validation, prompt rounds one and two, and on test; X3 on test), and
                                           X2-val-p1-choices.json, round one's choices counted from its outputs;
                                           section 8
  results/neural/selection.json, hpo.json, ablations.json, explanations.json, test_lock.json, test_runs.jsonl,
  results/neural/ablations/test_runs.jsonl, results/neural/generation/pilot.json and round2-plan.json,
  artifacts/tracernet/v1/manifest.json, datasets/scenarios-v2.manifest.json, and the OpenAI ledger's totals (its
  append-only mirror replayed read-only through src/scenarios/ledger.py, else a JSON given with --ledger-json,
  else the library manifest's record of the ledger at the freeze, each labeled with its source)

Every number on the page is read from one of those files, or computed from them by a rule the page states (R1 to
R3 at 30 alerts per 100 specimens go through the evaluation's own decision function). A file that does not exist
yet renders as a "not run yet" line, never as a number. The page carries no date or time: the same inputs give the
same page. The sections follow spec §8.10's order; the first paragraph is the verdict with its numbers and
intervals, and the synthetic-data notice (§8.9). Reliability plots are written as SVG beside it
(results/neural/plots/).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from src.pipeline import neural_eval as ne

ROOT = Path(__file__).resolve().parents[2]
NEURAL = ROOT / "results" / "neural"
REPORT_PATH = NEURAL / "REPORT.md"
SETS = ("val", "test", "test_ood", "legacy_v1", "control_v1world")
OOD_FEATURE = "test_ood_feature"
CONFIG_PATH = ROOT / "config" / "neural.yaml"
NOT_RUN = "Not run yet"
NOTICE = ("Every number in this report comes from synthetic data: laboratory weeks designed by language models and "
          "simulated by this project's own code, with no clinical review. They describe the simulator, not any "
          "laboratory.")
HEAD = "6/shift"
SECONDARY = "30/100"
PER_SHIFT = ("1/shift", "2/shift", "4/shift", "6/shift")
COLORS = ("#1f6feb", "#d1242f", "#1a7f37", "#9a6700", "#8250df", "#57606a")


def default_inputs(root: Path = ROOT) -> dict:
    neural = root / "results" / "neural"
    return {"evaluations": {s: neural / s / "evaluation.json" for s in SETS + (OOD_FEATURE,)},
            "selection": neural / "selection.json", "hpo": neural / "hpo.json", "ablations": neural / "ablations.json",
            "lock": neural / "test_lock.json", "test_runs": neural / "test_runs.jsonl",
            "ablation_test_runs": neural / "ablations" / "test_runs.jsonl",
            "pilot": neural / "generation" / "pilot.json", "round2": neural / "generation" / "round2-plan.json",
            "artifact": root / "artifacts" / "tracernet" / "v1" / "manifest.json",
            "manifest": root / "datasets" / "scenarios-v2.manifest.json",
            "explanations": neural / "explanations.json", "decisions": neural / "decisions", "limits": neural / "limits.md",
            "acceptance": neural / "acceptance.md",
            "ledger": root / "data" / "openai" / "ledger.sqlite", "ledger_json": None,
            "plots": neural / "plots", "root": root}


# --- Reading -----------------------------------------------------------------------------------------------------------

def _json(path) -> dict | None:
    return json.loads(Path(path).read_text(encoding="utf-8")) if path is not None and Path(path).is_file() else None


def _jsonl(path) -> list[dict]:
    if path is None or not Path(path).is_file():
        return []
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def ledger_totals(ledger_path: Path | None = None, json_path: Path | None = None) -> dict | None:
    """Spend per job and account: a JSON the parent provides ({"jobs": {job: {account, cap_usd, spent_usd,
    held_usd}}, "accounts": {...}}), else the ledger's append-only mirror rebuilt without opening the database
    (src/scenarios/ledger.py `replay`). None when neither exists."""
    given = _json(json_path)
    if given is not None:
        return {"source": f"{Path(json_path).name}, as provided"} | given
    from src.scenarios import ledger

    mirror = ledger.mirror_path(Path(ledger_path or ledger.LEDGER_PATH))
    if not mirror.is_file():
        return None
    balances = ledger.replay(mirror)
    cfg = ledger.load_jobs()

    def usd(b):
        return {"spent_usd": ledger.as_usd((b or {}).get("spent", 0)), "held_usd": ledger.as_usd((b or {}).get("held", 0))}
    return {"source": f"the ledger mirror {mirror.name}, replayed read-only",
            "jobs": {j: {"account": c["account"], "cap_usd": float(c["cap_usd"])} | usd(balances["jobs"].get(j))
                     for j, c in cfg["jobs"].items()},
            "accounts": {a: {"cap_usd": float(c["cap_usd"]), "margin_usd": float(c["margin_usd"])}
                         | usd(balances["accounts"].get(a)) for a, c in cfg["accounts"].items()}}


def load(inputs: dict) -> dict:
    ev = {s: _json(p) for s, p in inputs["evaluations"].items()}
    ood = ev.get("test_ood")
    if ev.get(OOD_FEATURE) is None and ood is not None and (ood.get("subsets") or {}).get(ne.OOD_FEATURE):
        ev[OOD_FEATURE] = ne.subset_view(ood, ne.OOD_FEATURE)
    return {"ev": ev, "selection": _json(inputs["selection"]), "hpo": _json(inputs["hpo"]),
            "ablations": _json(inputs["ablations"]), "lock": _json(inputs["lock"]),
            "test_runs": _jsonl(inputs["test_runs"]), "ablation_test_runs": _jsonl(inputs["ablation_test_runs"]),
            "pilot": _json(inputs["pilot"]), "round2": _json(inputs["round2"]), "artifact": _json(inputs["artifact"]),
            "artifact_sha256": (hashlib.sha256(Path(inputs["artifact"]).read_bytes()).hexdigest()
                                if Path(inputs["artifact"]).is_file() else None),
            "manifest": _json(inputs["manifest"]), "ledger": ledger_totals(inputs["ledger"], inputs["ledger_json"]),
            "limits": [line.rstrip() for line in Path(inputs["limits"]).read_text(encoding="utf-8").splitlines()
                       if line.strip()] if inputs.get("limits") and Path(inputs["limits"]).is_file() else [],
            "acceptance": (Path(inputs["acceptance"]).read_text(encoding="utf-8")
                           if inputs.get("acceptance") and Path(inputs["acceptance"]).is_file() else None),
            "explanations": _json(inputs["explanations"]), "decisions": load_decisions(inputs["decisions"]),
            "config": (yaml.safe_load(Path(CONFIG_PATH).read_text(encoding="utf-8")) or {}).get("ablations") or {}}


# --- Formatting ------------------------------------------------------------------------------------------------------

def num(x, digits: int = 1) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, int) and not isinstance(x, bool):
        return f"{x:,}"
    return f"{x:,.{digits}f}"


def pct(x, digits: int = 1) -> str:
    return "n/a" if x is None else f"{100 * x:.{digits}f}%"


def interval(m: dict | None, digits: int = 1, scale: float = 1.0) -> str:
    if not m or m.get("low") is None or m.get("high") is None:
        return "n/a"
    return f"{m['low'] * scale:.{digits}f} to {m['high'] * scale:.{digits}f}"


def table(header: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + ["| " + " | ".join(r) + " |" for r in rows]


def series_keys(result: dict) -> list[tuple[str, str]]:
    """(method, weighting) for every series of an evaluation, in its own order."""
    op = (result.get("operating_points") or {}).get(result["settings"]["headline"]) or {}
    return [(m, w) for m, by_w in op.items() for w in by_w]


def weighting_of(result: dict, method: str, weighting: str) -> str:
    return weighting if (result.get("methods") or {}).get(method, {}).get("weighted", True) else ne.UNWEIGHTED


def point(result: dict, budget: str, method: str, weighting: str, metric: str):
    w = weighting_of(result, method, weighting)
    return (((result.get("operating_points") or {}).get(budget) or {}).get(method) or {}).get(w, {}).get(metric, {}).get("point")


def headline_points(result: dict, budget: str) -> dict:
    """decide()'s `headline` at any budget: point metrics of TracerNet and the rule's baselines per weighting."""
    out = {}
    for w in ne.WEIGHTINGS:
        out[w] = {}
        for m in (ne.TRACERNET, *ne.BASELINES):
            got = (((result.get("operating_points") or {}).get(budget) or {}).get(m) or {}).get(weighting_of(result, m, w))
            if got is not None:
                out[w][m] = {k: v.get("point") for k, v in got.items()}
    return out


def brier_of(result: dict) -> dict:
    return {m: {str(L): ((result.get("calibration") or {}).get(m) or {}).get(str(L), {}).get("brier")
                for L in ne.CALIBRATION_LANDMARKS} for m in (ne.TRACERNET, "analytic_global", "analytic_local")}


def decision_at(result: dict, budget: str) -> dict | None:
    """R1 to R4 at another budget through the evaluation's own decide() (the non-binding view)."""
    diffs = (result.get("differences") or {}).get(budget)
    if not diffs or ne.TRACERNET not in (result.get("methods") or {}):
        return None
    return ne.decide(headline_points(result, budget), {w: diffs.get(w) or {} for w in ne.WEIGHTINGS}, brier_of(result))


def held(x: bool) -> str:
    return "held" if x else "failed"


# --- Section 1: the verdict --------------------------------------------------------------------------------------------

def _r1_sentence(rule: dict) -> str:
    parts = []
    for w in ne.WEIGHTINGS:
        cs = [c for c in rule["checks"] if c["weighting"] == w]
        if not cs:
            continue
        tn = cs[0]["tracernet_min"]
        parts.append(f"at {w} weights TracerNet's median warning over all breaches was {num(tn)} min against "
                     + ", ".join(f"{c['baseline'].replace('_', ' ')} {num(c['baseline_min'])} (difference "
                                 f"{num(c['difference_min'])}, interval low {num(c['interval_low_min'])})" for c in cs))
    return "; ".join(parts)


def verdict_paragraph(data: dict) -> str:
    test = data["ev"].get("test")
    rule = (test or {}).get("decision_rule")
    if rule is None:
        why = ("the test set has not been scored" if test is None else
               "the test evaluation holds no TracerNet result")
        return (f"**Verdict: {NOT_RUN.lower()}.** R1 to R4 are judged once, on `test` at 6 alerts per shift, and "
                f"{why} (results/neural/test/evaluation.json). Nothing in this report is the claim yet; validation "
                f"numbers below are development numbers and never decide the rule. {NOTICE}")
    v = rule["verdict"]
    r1, r2, r3, r4 = (rule[k] for k in ("R1", "R2", "R3", "R4"))
    worst2 = max((c for c in r2["checks"] if c["deficit"] is not None), key=lambda c: c["deficit"], default=None)
    r3_txt = "; ".join(f"at {c['landmark_min']} min TracerNet {num(c['tracernet_brier'], 4)} against "
                       f"{num(c['better_analytic_brier'], 4)} (excess {num(c['excess'], 4)})" for c in r3["checks"])
    r4_worst = max((c for c in r4["checks"] if c["relative_difference"] is not None),
                   key=lambda c: c["relative_difference"], default=None)
    return (f"**Verdict on `test` at 6 alerts per shift: R1 {held(r1['held'])}, R2 {held(r2['held'])}, R3 "
            f"{held(r3['held'])}, R4 {held(r4['held'])}; primary risk model: {v['primary_model']}.** "
            f"R1 (at least 5 minutes more warning than each baseline, with the interval's lower end above zero): "
            f"{_r1_sentence(r1)}. R2 (deficit in never-received specimens alerted below 2 points): the largest "
            f"deficit was "
            + (f"{pct(worst2['deficit'])} against {worst2['baseline'].replace('_', ' ')} at {worst2['weighting']} "
               f"weights (interval high {pct(worst2['deficit_interval_high'])})" if worst2 else "n/a")
            + f". R3 (Brier no worse than the better analytic refit's by more than 0.01): {r3_txt}. R4 (realized "
            f"alerts within 10% of each baseline's): the largest relative difference was "
            + (f"{pct(r4_worst['relative_difference'])} against {r4_worst['baseline'].replace('_', ' ')} at "
               f"{r4_worst['weighting']} weights" if r4_worst else "n/a")
            + (", so the comparison is reported as unequal and R1 is repeated at matched volume below" if r4["unequal"]
               else "") + f". {NOTICE}")


def no_alert_rows(result: dict, budget: str = HEAD) -> list[list[str]]:
    knobs = (result.get("knobs") or {}).get(budget) or {}
    groups = result["counts"]["tuning_groups"]
    rows = []
    for m, by_w in knobs.items():
        for w, k in by_w.items():
            rows.append([m, w, f"{k['no_alert_groups']:,} of {groups:,}"])
    return rows


def section_verdict(data: dict) -> list[str]:
    out = ["## 1. Verdict against R1 to R4", "", "The pre-registered rule (spec §8.6), quoted verbatim:", ""]
    out += ["> " + line if line else ">" for line in ne.RULE_TEXT.split("\n")]
    test, val = data["ev"].get("test"), data["ev"].get("val")
    rule = (test or {}).get("decision_rule")
    out.append("")
    if rule is None:
        out.append(f"{NOT_RUN}: the rule is judged on `test`, which has not been scored with TracerNet.")
    else:
        out += ["### R1. Warning", ""]
        out += table(["Weights", "Baseline", "TracerNet (min)", "Baseline (min)", "Difference (min)",
                      "Interval low (min)", "Held"],
                     [[c["weighting"], c["baseline"], num(c["tracernet_min"]), num(c["baseline_min"]),
                       num(c["difference_min"]), num(c["interval_low_min"]), held(c["held"])]
                      for c in rule["R1"]["checks"]])
        out += ["", "### R2. Losses", ""]
        out += table(["Weights", "Baseline", "TracerNet alerted", "Baseline alerted", "Deficit", "Interval high",
                      "Held"],
                     [[c["weighting"], c["baseline"], pct(c["tracernet_share_alerted"]),
                       pct(c["baseline_share_alerted"]), pct(c["deficit"]), pct(c["deficit_interval_high"]),
                       held(c["held"])] for c in rule["R2"]["checks"]])
        out += ["", "### R3. Calibration", ""]
        out += table(["Silence (min)", "TracerNet Brier", "Better analytic refit's Brier", "Excess", "Held"],
                     [[str(c["landmark_min"]), num(c["tracernet_brier"], 4), num(c["better_analytic_brier"], 4),
                       num(c["excess"], 4), held(c["held"])] for c in rule["R3"]["checks"]])
        out += ["", "### R4. Equal alerts", ""]
        out += table(["Weights", "Baseline", "TracerNet per shift", "Baseline per shift", "Relative difference",
                      "Held"],
                     [[c["weighting"], c["baseline"], num(c["tracernet_per_shift"], 2), num(c["baseline_per_shift"], 2),
                       pct(c["relative_difference"]), held(c["held"])] for c in rule["R4"]["checks"]])
        matched = rule["R4"].get("r1_at_matched_volume")
        if rule["R4"]["unequal"]:
            out += ["", "R1 repeated at matched realized volume (a sensitivity check, not a replacement): "
                    + (held(matched["held"]) + "; " + _r1_sentence(matched) + "." if matched
                       else f"{NOT_RUN.lower()} (the evaluation holds no matched-volume result).")]
    out += ["", "### Scenarios with no in-budget knob", ""]
    source = test if test is not None else val
    if source is None:
        out.append(f"{NOT_RUN}: no evaluation to count them in.")
    else:
        label = "test" if test is not None else "validation, for reference (not the claim)"
        out += [f"In a scenario with more breaches per shift than the budget has alerts, a method whose risk reaches 1 "
                f"when the window closes has no knob that both alerts and stays within 6 alerts per shift, so it "
                f"alerts on nothing there. Counted on {label}, beside R1:", ""]
        out += table(["Method", "Weights", "Scenarios with no in-budget knob"], no_alert_rows(source))
    out += ["", "### Secondary view (non-binding)", "",
            "Fixed before any validation or test result existed (docs/neural/build-log.md, \"a secondary analysis\"): "
            "R1 to R3 recomputed at 30 alerts per 100 specimens with the same margins and intervals, and the capped "
            "mean warning at every budget. It never replaces the rule above.", ""]
    second = decision_at(test, SECONDARY) if test is not None else None
    if second is None:
        out.append(f"{NOT_RUN}: R1 to R3 at 30 alerts per 100 specimens need the test evaluation with TracerNet.")
    else:
        out.append(f"At 30 per 100 specimens on `test`: R1 {held(second['R1']['held'])}, R2 "
                   f"{held(second['R2']['held'])}, R3 {held(second['R3']['held'])}. R1: {_r1_sentence(second['R1'])}.")
    if source is not None:
        out += ["", f"Capped mean warning (minutes, each warning capped at 120) at every budget, on "
                    f"{'test' if test is not None else 'validation, for reference'}:", ""]
        budgets = source["settings"]["budgets"]
        rows = [[m, w] + [num(point(source, b, m, w, "capped_mean_warning_min")) for b in budgets]
                for m, w in series_keys(source)]
        out += table(["Method", "Weights", *budgets], rows)
    out += ["", f"Recommendations when the rule fails (spec §8.9) are in section 10. Every test run, with its reason, is "
                f"listed in section 11."]
    return out


# --- Section 2: the headline table -------------------------------------------------------------------------------------

METRIC_COLUMNS = (("Alerts per shift", "realized_alerts_per_shift", 2), ("Alerts per 100", "alerts_per_100_specimens", 1),
                  ("Alerts", "alerts", 0), ("Breaches", "breaches", 0), ("Caught", "caught", 0),
                  ("Catch rate", "catch_rate", None), ("Median warning, all breaches (min)",
                                                       "median_warning_all_breaches_min", 1),
                  ("95% interval", "median_warning_all_breaches_min", "interval"),
                  ("Median warning, caught (min)", "median_warning_min", 1),
                  ("Caught with 15 min or more", "caught_with_good_warning", None),
                  ("False alerts", "false_alerts", 0), ("False per shift", "false_alerts_per_shift", 2),
                  ("Never received", "never_received", 0), ("Missed losses", "missed_losses", 0),
                  ("Missed-loss rate", "missed_loss_rate", None), ("Capped mean warning (min)",
                                                                   "capped_mean_warning_min", 1),
                  ("95% interval ", "capped_mean_warning_min", "interval"))


def budget_title(budget: str) -> str:
    value, per = budget.split("/")
    noun = "alert" if value == "1" else "alerts"
    return f"{value} {noun} per shift" if per == "shift" else f"{value} {noun} per 100 specimens"


def operating_table(result: dict, budget: str) -> list[str]:
    op = (result.get("operating_points") or {}).get(budget)
    if op is None:
        return [f"{NOT_RUN}: no results at {budget}."]
    rows = []
    for m, by_w in op.items():
        for w, metrics in by_w.items():
            cells = [m, w]
            for _, key, digits in METRIC_COLUMNS:
                v = metrics.get(key) or {}
                if digits == "interval":
                    cells.append(interval(v))
                elif digits is None:
                    cells.append(pct(v.get("point")))
                else:
                    cells.append(num(v.get("point"), digits))
            rows.append(cells)
    return table(["Method", "Weights"] + [c for c, _, _ in METRIC_COLUMNS], rows)


def section_headline(data: dict) -> list[str]:
    test, val = data["ev"].get("test"), data["ev"].get("val")
    out = ["## 2. Headline table", ""]
    source, label = (test, "test") if test is not None else (val, "validation")
    if source is None:
        return out + [f"{NOT_RUN}: no evaluation has been written."]
    c = source["counts"]
    if test is None:
        out += [f"{NOT_RUN} on `test`. Shown instead: validation, for reference only (it never decides the rule)."]
    out += [f"`{label}`: {c['scored_specimens']:,} scored specimens with a clock in {c['clusters']:,} "
            f"{source['settings']['cluster']}s, {c['breaches']:,} breaches ({c['never_received']:,} never received), "
            f"{num(c['scored_shifts'], 0)} shifts. Knobs are set on each scenario's history replicate from alert volume "
            f"alone; intervals are the paired cluster bootstrap ({source['settings']['bootstrap']['resamples']:,} "
            f"resamples). Every budget and every weighting is shown.", ""]
    for b in [b for b in source["settings"]["budgets"] if b in PER_SHIFT]:
        out += [f"### {budget_title(b)}", ""] + operating_table(source, b) + [""]
    per_100 = [b for b in source["settings"]["budgets"] if b.endswith("/100")]
    if per_100:
        out += ["Per-100-specimen budgets (the secondary view of spec §8.3; volumes differ about thirtyfold):", ""]
        for b in per_100:
            out += [f"### {budget_title(b)}", ""] + operating_table(source, b) + [""]
    if test is not None and test.get("differences"):
        out += ["### TracerNet minus each method", "",
                "Paired bootstrap differences in the median warning over all breaches and in the capped mean warning "
                "(minutes), with the share of resamples in which TracerNet is ahead:", ""]
        rows = []
        for b in test["settings"]["budgets"]:
            for w, by_m in (test["differences"].get(b) or {}).items():
                for m, d in by_m.items():
                    a, cm = d.get("median_warning_all_breaches_min") or {}, d.get("capped_mean_warning_min") or {}
                    rows.append([b, w, m, num(a.get("point")), interval(a), num(cm.get("point")), interval(cm),
                                 pct(cm.get("share_ahead"), 0)])
        out += table(["Budget", "Weights", "Against", "Median, all breaches", "95% interval", "Capped mean",
                      "95% interval", "Ahead (capped mean)"], rows)
    return out


# --- Section 3: discrimination and calibration -------------------------------------------------------------------------

def reliability_svg(title: str, by_method: dict) -> str:
    """A reliability plot: observed against predicted miss rate per bin, one line per method, the diagonal."""
    size, pad = 360, 44
    span = size - 2 * pad

    def xy(p, o):
        return f"{pad + p * span:.1f},{size - pad - o * span:.1f}"
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
             f'font-family="sans-serif" font-size="11">', f'<rect width="{size}" height="{size}" fill="#ffffff"/>',
             f'<text x="{size / 2:.0f}" y="18" text-anchor="middle" font-size="12">{title}</text>',
             f'<rect x="{pad}" y="{pad}" width="{span}" height="{span}" fill="none" stroke="#8c959f"/>',
             f'<line x1="{pad}" y1="{size - pad}" x2="{size - pad}" y2="{pad}" stroke="#8c959f" stroke-dasharray="4 3"/>',
             f'<text x="{size / 2:.0f}" y="{size - 10}" text-anchor="middle">predicted miss rate</text>',
             f'<text x="14" y="{size / 2:.0f}" text-anchor="middle" transform="rotate(-90 14 {size / 2:.0f})">'
             f'observed miss rate</text>']
    for tick in (0, 0.5, 1):
        parts.append(f'<text x="{pad + tick * span:.1f}" y="{size - pad + 14}" text-anchor="middle">{tick:g}</text>')
        parts.append(f'<text x="{pad - 6}" y="{size - pad - tick * span + 4:.1f}" text-anchor="end">{tick:g}</text>')
    for i, (name, bins) in enumerate(by_method.items()):
        color = COLORS[i % len(COLORS)]
        pts = [(b["predicted"], b["observed"]) for b in bins if b.get("n") and b.get("predicted") is not None
               and b.get("observed") is not None]
        if pts:
            parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="1.5" points="'
                         + " ".join(xy(p, o) for p, o in pts) + '"/>')
            parts += [f'<circle cx="{xy(p, o).split(",")[0]}" cy="{xy(p, o).split(",")[1]}" r="2.5" fill="{color}"/>'
                      for p, o in pts]
        parts.append(f'<text x="{pad + 6}" y="{pad + 14 + 13 * i}" fill="{color}">{name}</text>')
    return "\n".join(parts + ["</svg>"]) + "\n"


def section_calibration(data: dict, plots_dir: Path, root: Path) -> list[str]:
    test, val = data["ev"].get("test"), data["ev"].get("val")
    source, label = (test, "test") if test is not None else (val, "validation")
    out = ["## 3. Discrimination and calibration", ""]
    if source is None:
        return out + [f"{NOT_RUN}: no evaluation has been written."]
    if test is None:
        out += [f"{NOT_RUN} on `test`. Shown instead: validation, for reference only.", ""]
    out += ["AUROC and AUPRC for a miss among specimens still silent at each landmark whose window is longer, and "
            "pooled over every tick with each specimen weighted equally; rules are ranked by their own scores:", ""]
    landmarks = [f"landmark_{m}" for m in ne.DISCRIMINATION_LANDMARKS]
    rows = []
    for m, d in (source.get("discrimination") or {}).items():
        rows.append([m] + [f"{num(d[k]['auroc'], 3)} / {num(d[k]['auprc'], 3)}" for k in landmarks if k in d]
                    + [f"{num(d['pooled']['auroc'], 3)} / {num(d['pooled']['auprc'], 3)}"])
    out += table(["Method"] + [f"{m} min (AUROC / AUPRC)" for m in ne.DISCRIMINATION_LANDMARKS] + ["Pooled"], rows)
    out += ["", "Calibration of the probabilistic methods at 30 and 60 minutes of silence:", ""]
    rows = []
    for m, by_l in (source.get("calibration") or {}).items():
        for L, c in by_l.items():
            rows.append([m, L, num(c.get("n")), num(c.get("breach_rate"), 4), num(c.get("brier"), 4),
                         num(c.get("brier_of_the_base_rate"), 4), num(c.get("ece"), 4), num(c.get("slope"), 3),
                         num(c.get("intercept"), 3)])
    out += table(["Method", "Silence (min)", "Specimens", "Miss rate", "Brier", "Brier of the base rate", "ECE",
                  "Slope", "Intercept"], rows)
    plots_dir.mkdir(parents=True, exist_ok=True)
    links = []
    for L in ne.CALIBRATION_LANDMARKS:
        bins = {m: by_l[str(L)].get("bins") or [] for m, by_l in (source.get("calibration") or {}).items()
                if str(L) in by_l}
        if not bins:
            continue
        path = plots_dir / f"reliability-{label}-{L}.svg"
        path.write_text(reliability_svg(f"Reliability at {L} min of silence ({label})", bins), encoding="utf-8")
        links.append(f"[{L} min]({path.relative_to(root / 'results' / 'neural').as_posix()})"
                     if path.is_relative_to(root / "results" / "neural") else f"[{L} min]({path})")
    if links:
        out += ["", "Reliability plots: " + ", ".join(links) + "."]
    out += ["", "### TracerNet's own calibration (validation, spec §7.6)", ""]
    art = data["artifact"]
    if art is None:
        out.append(f"{NOT_RUN}: no artifact at artifacts/tracernet/v1/.")
    else:
        v = art.get("validation") or {}
        ece = v.get("ece_after")
        out.append(f"Temperature {num(v.get('temperature'), 3)}, isotonic map {'applied' if v.get('isotonic') else 'not needed'}; "
                   f"expected calibration error of p_miss {num(v.get('ece_before'), 4)} before and {num(ece, 4)} after "
                   f"on {num(v.get('calibration_ticks'))} validation ticks."
                   + ("" if ece is None or ece <= 0.02 else
                      f" The residual is above spec §17 item 10's 0.02 by {num(ece - 0.02, 4)}."))
    return out


# --- Section 4: slices -------------------------------------------------------------------------------------------------

def section_slices(data: dict) -> list[str]:
    test, val = data["ev"].get("test"), data["ev"].get("val")
    source, label = (test, "test") if test is not None else (val, "validation")
    out = ["## 4. Slices", ""]
    if source is None:
        return out + [f"{NOT_RUN}: no evaluation has been written."]
    if test is None:
        out += [f"{NOT_RUN} on `test`. Shown instead: validation, for reference only.", ""]
    head = source["settings"]["headline"]
    keys = [(m, w) for m, w in series_keys(source) if w in ("3-2-1", "equal", ne.UNWEIGHTED)]
    out += [f"At {head}, each cell is the median warning over all breaches / the capped mean warning (minutes):", ""]
    for d, groups in (source.get("slices") or {}).items():
        out += [f"### {d.replace('_', ' ')}", ""]
        rows = []
        for g, by_m in groups.items():
            first = next(iter(next(iter(by_m.values())).values()))
            rows.append([str(g), num(first.get("specimens")), num(first.get("breaches"))]
                        + [f"{num(((by_m.get(m) or {}).get(w) or {}).get('median_warning_all_breaches_min'))} / "
                           f"{num(((by_m.get(m) or {}).get(w) or {}).get('capped_mean_warning_min'))}" for m, w in keys])
        out += table(["Slice", "Specimens", "Breaches"] + [f"{m} ({w})" for m, w in keys], rows) + [""]
    return out


# --- Section 5: test_ood, legacy_v1 and the negative control -------------------------------------------------------------

def compact(result: dict, budgets=(HEAD, SECONDARY)) -> list[str]:
    rows = []
    for b in budgets:
        for m, w in series_keys(result):
            if m not in (ne.TRACERNET, *ne.BASELINES) or w not in (*ne.WEIGHTINGS, ne.UNWEIGHTED):
                continue
            op = ((result.get("operating_points") or {}).get(b) or {}).get(m, {}).get(w)
            if op is None:
                continue
            k = (((result.get("knobs") or {}).get(b) or {}).get(m) or {}).get(w) or {}
            rows.append([b, m, w, num(op["median_warning_all_breaches_min"]["point"]),
                         interval(op["median_warning_all_breaches_min"]), num(op["capped_mean_warning_min"]["point"]),
                         interval(op["capped_mean_warning_min"]), pct(op["catch_rate"]["point"]),
                         num(op["realized_alerts_per_shift"]["point"], 2),
                         f"{k.get('no_alert_groups', 'n/a')} of {result['counts']['tuning_groups']}"])
    return table(["Budget", "Method", "Weights", "Median warning, all (min)", "95% interval", "Capped mean (min)",
                  "95% interval", "Catch rate", "Alerts per shift", "No in-budget knob"], rows)


def lead(result: dict, budget: str, baseline: str, weighting: str = "3-2-1") -> dict:
    d = (((result.get("differences") or {}).get(budget) or {}).get(weighting) or {}).get(baseline) or {}
    return {"median": d.get("median_warning_all_breaches_min"), "capped": d.get("capped_mean_warning_min")}


def section_beside(data: dict) -> list[str]:
    ev = data["ev"]
    out = ["## 5. test_ood, legacy_v1 and the negative control", "",
           "Reported beside the claim, never folded into it (spec §8.6)."]
    test_rule = (ev.get("test") or {}).get("decision_rule")
    for name, title, note in (
            ("test_ood", "test_ood, every scenario", "Families go to `test_ood` whole, so it also holds ordinary weeks "
             "that travel with an out-of-distribution family member (docs/neural/build-log.md)."),
            (OOD_FEATURE, "test_ood, the scenarios that carry the out-of-distribution feature",
             "Archetype `multi_site_system` or a `scanner_downtime` disruption."),
            ("legacy_v1", "legacy_v1", "The v1 store's held-out runs 24 to 29: 6 clusters, so its intervals are wide."),
            ("control_v1world", "The negative control (control_v1world)", None)):
        out += ["", f"### {title}", ""]
        if note:
            out += [note, ""]
        r = ev.get(name)
        if r is None:
            out.append(f"{NOT_RUN}.")
            continue
        c = r["counts"]
        out += [f"{c['clusters']:,} {r['settings']['cluster']}s, {c['scored_specimens']:,} scored specimens, "
                f"{c['breaches']:,} breaches.", ""] + compact(r)
        rule = r.get("decision_rule")
        if rule is not None and name != "control_v1world":
            v = rule["verdict"]["held"]
            out += ["", f"The rule applied here, non-binding: R1 {held(v['R1'])}, R2 {held(v['R2'])}, R3 "
                        f"{held(v['R3'])}, R4 {held(v['R4'])}."]
            if test_rule is not None and test_rule["verdict"]["tracernet_primary"] and not (v["R1"] and v["R2"] and v["R3"]):
                out.append(f"TracerNet won on `test` and fails the rule on `{name}`: reported as exactly that (spec §8.9).")
        if name == "control_v1world":
            out += control_lines(r, data["config"])
    return out


def control_lines(r: dict, cfg: dict) -> list[str]:
    if ne.TRACERNET not in (r.get("methods") or {}):
        return ["", f"{NOT_RUN}: the control has no TracerNet result."]
    margin = float(cfg.get("leakage_lead_min", ne.MIN_WARNING_GAIN_MIN))
    out = ["", f"Where no context exists, TracerNet's lead over the local analytic refit should be small; a lead of "
               f"{margin:g} minutes or more whose interval's lower end is above zero is treated as leakage until shown "
               f"otherwise (spec §8.8 A6).", ""]
    rows, flagged = [], False
    for b in (HEAD, SECONDARY):
        got = lead(r, b, "analytic_local")
        for metric, d in (("median warning, all breaches", got["median"]), ("capped mean warning", got["capped"])):
            if not d:
                continue
            large = d.get("point") is not None and d["point"] >= margin and (d.get("low") or 0) > 0
            flagged |= large
            rows.append([b, metric, num(d.get("point")), interval(d), "large: treated as leakage" if large else "small"])
    if not rows:
        return out + [f"{NOT_RUN}: the control evaluation holds no difference against the local refit."]
    out += table(["Budget", "Metric", "Lead (min)", "95% interval", "Reading"], rows)
    out += ["", "The lead is large somewhere above: treat it as leakage until shown otherwise." if flagged else
            "The lead is small at every budget shown."]
    return out


# --- Section 6: ablations ----------------------------------------------------------------------------------------------

def section_ablations(data: dict) -> list[str]:
    out = ["## 6. Ablations A1 to A9", ""]
    a = data["ablations"]
    if a is None:
        return out + [f"{NOT_RUN}: results/neural/ablations.json does not exist (`ablate --collect`)."]
    out += [f"Every trained arm took the same {num(a.get('steps'))} gradient steps "
            f"({'equal across arms' if a.get('steps_equal') else 'NOT equal across arms: see the arms'}); "
            f"compute {num((a.get('compute') or {}).get('hours'), 2)} hours of the {num(a.get('time_box_h'), 0)}-hour "
            f"box. Test numbers are descriptive, scored after the lock, and never change the headline model.", ""]
    for aid, ab in (a.get("ablations") or {}).items():
        out += [f"### {aid}. {ab.get('question', '')}", ""]
        if ab.get("status") != "done":
            why = str(ab.get("reason", "no reason recorded"))
            status = ab.get("status", NOT_RUN)
            out += [(why[0].upper() + why[1:] if why.lower().startswith(status.lower()) else f"{status.capitalize()}: {why}")
                    + ("" if why.endswith(".") else "."), ""]
        arms = ab.get("arms") or {}
        if not arms:
            continue
        rows = []
        for name, arm in arms.items():
            def stat(split, key):
                s = ((arm.get(split) or {}).get(key)) or {}
                if not s or s.get("mean") is None:
                    return "n/a"
                d = 4 if key in ("val_sel", "brier_30", "brier_60") else 2    # these differ in the third decimal
                rng = f" ({num(s['min'], d)} to {num(s['max'], d)})" if s.get("n", 1) > 1 else ""
                return f"{num(s['mean'], d)}{rng}"
            rows.append([name, str(arm.get("seeds_done", 0)), stat("val", "val_sel"), stat("val", "capped_mean_warning_min"),
                         stat("val", "median_warning_all_breaches_min"), stat("test", "capped_mean_warning_min"),
                         stat("test", "median_warning_all_breaches_min"), stat("test", "brier_30"),
                         stat("test", "brier_60")])
        out += table(["Arm", "Seeds", "val_sel", "Val capped mean (min)", "Val median, all (min)",
                      "Test capped mean (min)", "Test median, all (min)", "Test Brier 30", "Test Brier 60"], rows)
        for note in ab.get("notes") or []:
            out += ["", note]
        out.append("")
    return out


# --- Sections 7 to 9 ---------------------------------------------------------------------------------------------------

def section_explanations(data: dict) -> list[str]:
    """Spec §8.10 item 7 from results/neural/explanations.json (src/models/tracernet/explain.py `run_checks`)."""
    out = ["## 7. Explanation checks and example alerts", ""]
    ex = data["explanations"]
    if ex is None:
        return out + [f"{NOT_RUN}: the explanation checks of spec §9.4 (task N9) have not been run "
                      "(no results/neural/explanations.json)."]
    c = ex["checks"]
    comp, dele, rand = c["completeness"], c["deletion"], c["randomization"]
    budget, weights = ex.get("budget_per_shift"), ex.get("weights")
    at = "" if budget is None else f" at {num(budget, 0)} alerts per shift"
    under = "" if weights is None else f" ({weights} weights)"
    given = ex.get("alerts_given", ex["alerts"])
    scope = (f"{num(ex['alerts'])} alerts" if given == ex["alerts"] else
             f"a seeded sample of {num(ex['alerts'])} of its {num(given)} alerts")
    out += [f"Integrated gradients on the logit of the ensemble's mean p_miss, summed into ten factor groups (spec §9), "
            f"on {scope} of `{ex.get('set') or 'an unnamed set'}`{at}{under}, "
            f"{num(ex['steps'])} steps. Attributions explain the network, not the world, and the data is synthetic.",
            ""]
    rows = [["Completeness", f"median gap {pct(comp.get('median_relative_gap'), 2)} at {num(comp.get('steps'))} steps",
             "within 1% for the median alert", held(comp["passed"])],
            ["Deletion", f"top 3 beat 3 random groups in {pct(dele.get('share_top_beats_random'))} of "
                         f"{num(dele.get('alerts'))} alerts; mean drop in p_miss {num(dele.get('mean_drop_top'), 4)} "
                         f"against {num(dele.get('mean_drop_random'), 4)}", "at least 70%, with a larger mean drop",
             held(dele["passed"])],
            ["Randomization", f"mean Spearman {num(rand.get('mean_spearman'), 2)} (median "
                              f"{num(rand.get('median_spearman'), 2)}) over {num(rand.get('alerts'))} alerts",
             "below 0.3", held(rand["passed"])],
            ["Factors", f"{num(ex['alerts_with_factors'])} of {num(ex['alerts'])} alerts carry factors", "every alert",
             held(ex["alerts_with_factors"] == ex["alerts"])]]
    out += table(["Check", "Result", "Rule", "Outcome"], rows)
    closed, saturated = ex.get("closed_window_alerts", 0), ex.get("saturated_alerts", 0)
    out += ["", f"Closed-window alerts: {num(closed)}. At or past its window's close an alert's p_miss is 1 by "
                "construction, so it carries one window factor with no attribution and is left out of the three "
                "checks above."
            + (f" Another {num(saturated)} alerts reached the limit of the network's scale and are treated the same "
               "way." if saturated else "")]
    raising = ex.get("raising_groups") or {}
    if raising:
        ranked = sorted(raising.items(), key=lambda kv: (-kv[1], kv[0]))
        out += ["", "Groups among the factors that raise the risk, by how often: "
                + ", ".join(f"{g} {num(n)}" for g, n in ranked) + "."]
    out += ["", "### Example alerts", "",
            "The highest, the median and the lowest p_miss among the alerts explained. Related specimens are the "
            "batch-mates the network attended to most, which is not an explanation."]
    for e in ex.get("examples") or []:
        where = f"{e['scenario_id']}, " if e.get("scenario_id") else ""
        out += ["", f"**{e['alert_id']}** ({where}minute {num(e['tick_min'])} of a {num(e['window_min'])}-minute "
                    f"window, p_miss {num(e['p_miss'], 3)})", ""]
        out += table(["Direction", "Group", "Share", "Attribution (logit)", "Factor"],
                     [[f["direction"], f["group"], pct(f["share"], 0), num(f["attribution"], 3), f["text"]]
                      for f in e["factors"]])
        related = ", ".join(r["specimen_id"] for r in e.get("related") or [])
        if related:
            out += ["", f"Related specimens: {related}."]
    return out


# Each run of the decision layer the section reads, in the order the spend table lists them: (job, file stem, label).
# A run is its summary results/neural/decisions/<job>-<stem>.json and, where scored, its scores <job>-<stem>-scores.json
# (src/decide/client.py, src/pipeline/dispatch_eval.py). X1 ran as a pilot, then a paid run over the rest, then a
# refresh that answered everything from the cache (X1-library.json, the scored run); X2's first prompt round on
# validation is kept as X2-val-p1*.json beside round two, with X2-val-p1-choices.json, its choices counted from its
# outputs (its scores predate the choices record).
DECISION_RUNS = (("X1", "library-pilot", "X1 pilot"), ("X1", "library-paid", "X1 paid run"),
                 ("X1", "library", "X1 refresh, from the cache"), ("X2", "val-p1", "X2 on validation, prompt round one"),
                 ("X2", "val", "X2 on validation, prompt round two"), ("X2", "test", "X2 on test"),
                 ("X3", "test", "X3 on test"))
METHOD_NAMES = {"model": "Drex Max", "R1": "R1 (today's routing)", "R2": "R2 (hand-written rules)"}


def load_decisions(folder) -> dict:
    """{(job, stem): {"summary", "scores", "choices"}} for every run of DECISION_RUNS, None where a file is absent."""
    folder = Path(folder)
    return {(job, stem): {"summary": _json(folder / f"{job}-{stem}.json"),
                          "scores": _json(folder / f"{job}-{stem}-scores.json"),
                          "choices": _json(folder / f"{job}-{stem}-choices.json")} for job, stem, _ in DECISION_RUNS}


def ci(m: dict | None, digits: int = 1, scale: float = 1.0) -> str:
    """'point (low to high)'; 'undefined' where the scores say a metric cannot be defined."""
    if not m or m.get("point") is None:
        return "undefined" if m and m.get("undefined") else "n/a"
    return f"{num(m['point'] * scale, digits)} ({interval(m, digits, scale)})"


def _pts(m: dict | None, scale: float = 100.0, digits: int = 1) -> str:
    if not m or m.get("point") is None:
        return "undefined" if m and m.get("undefined") else "n/a"
    return num(m["point"] * scale, digits)


def _run(data: dict, job: str, stem: str) -> dict:
    return data["decisions"].get((job, stem)) or {"summary": None, "scores": None, "choices": None}


def _absent(label: str, run: dict, why: str) -> str | None:
    """The 'not run' line of a run without scores, or None when its scores exist."""
    s = run["summary"]
    if run["scores"] is not None:
        return None
    if s is not None and s.get("status") == "not run":
        return f"{NOT_RUN}: {label}: {s.get('reason', 'no reason recorded')}."
    if s is not None:
        return f"{NOT_RUN}: {label} has run (its summary exists) but is not scored yet (`decide --score-only`)."
    return f"{NOT_RUN}: {label}: {why}."


def _decision_what() -> list[str]:
    return ["### What it is", "",
            "Drex Max v1.1 (Nace.AI), reached through OpenRouter's Decisions API, answers typed questions with a "
            "probability for every option and writes no text. It is used zero-shot: it was never trained on this "
            "project's data and never sees a label. It labels the scenario library (X1), proposes where to look, what "
            "to do and whom to call for each TracerNet alert (X2), and is tested as a forecaster on its own (X3). X2 is "
            "compared with two rules on the same alerts: R1, today's routing (a tube specimen to the receiving "
            "technologist, a hand-carried one to the courier coordinator), and R2, hand-written rules over the same "
            "facts the model sees. The adoption rule, pre-registered in spec §11.3: \"exports and the demo use Drex "
            "Max's decisions only if its role accuracy beats both R1 and R2 by at least 5 points, with the 95% interval "
            "of each difference above zero on test. Otherwise they use R2, and the comparison is reported.\""]


def _decision_spend(data: dict) -> list[str]:
    out = ["### Spend", ""]
    rows, models, latest = [], set(), {}
    for job, stem, label in DECISION_RUNS:
        s = _run(data, job, stem)["summary"]
        if s is None or s.get("status") == "not run":
            continue
        b, led = s.get("billing") or {}, s.get("ledger") or {}
        if s.get("resolved_model"):
            models.add(s["resolved_model"])
        latest[job] = max(latest.get(job, 0.0), float(led.get("spent_usd") or 0.0))
        cached = (s.get("counts") or {}).get("cached", 0)
        rows.append([label, s.get("prompt_version", "n/a"), f"{num(s.get('items'))} ({num(cached)} from the cache)",
                     num(b.get("calls_with_usage")), f"${num(b.get('cost_usd'), 4)}",
                     f"${num(led.get('spent_usd'), 4)} of ${num(led.get('cap_usd'), 2)}",
                     num(b.get("max_billed_over_bound"), 2), num(b.get("max_cost_over_reservation"), 2)])
    if not rows:
        return out + [f"{NOT_RUN}: no decision-layer run has a summary yet."]
    out += table(["Run", "Prompt", "Items", "Calls billed this run", "Billed this run", "Job's total so far, of its cap",
                  "Worst billed input / input bound", "Worst cost / reservation"], rows)
    out += _x1_spend(data)
    from src.decide import client as dc

    caps = dc.load_config()["caps"]
    out += ["", f"The decide account so far: ${num(sum(latest.values()), 4)} of its ${num(float(caps['account_usd']), 2)} "
                "cap, the latest total of each job. A worst ratio under 1 means every billed call stayed inside its "
                "reservation. Resolved model: " + (", ".join(f"`{m}`" for m in sorted(models)) or "none recorded") + "."]
    return out


def _x1_spend(data: dict) -> list[str]:
    """X1's cost in words, from whichever of its runs' summaries exist: the pilot, the paid run over the rest (whose
    ledger block holds the job's total) and the refresh from the cache."""
    runs = [(stem, _run(data, "X1", stem)["summary"]) for stem in ("library-pilot", "library-paid", "library")]
    runs = [(stem, s) for stem, s in runs if s is not None and s.get("status") != "not run"]
    paid = [(stem, s) for stem, s in runs if stem != "library"]
    if not runs:
        return []
    parts = [f"${num((s.get('billing') or {}).get('cost_usd'), 4)} for "
             + (f"its {num((s.get('billing') or {}).get('calls_with_usage'))}-call pilot" if stem == "library-pilot" else
                f"the {num((s.get('billing') or {}).get('calls_with_usage'))} calls of its paid run")
             for stem, s in paid]
    total = max(float((s.get("ledger") or {}).get("spent_usd") or 0.0) for _, s in runs)
    line = (f"X1 cost {', then '.join(parts)}: ${num(total, 4)} in all, the ledger's total for the job." if parts else
            f"X1's job total in the ledger: ${num(total, 4)}.")
    bounds = [b for _, s in paid if (b := (s.get("billing") or {}).get("max_billed_over_bound")) is not None]
    costs = [c for _, s in paid if (c := (s.get("billing") or {}).get("max_cost_over_reservation")) is not None]
    if bounds and costs:
        line += (f" Its worst call, across {'both paid runs' if len(paid) > 1 else 'its paid run'}, billed "
                 f"{num(max(bounds), 2)} of its input bound and {num(max(costs), 2)} of its reservation.")
    refresh = next((s for stem, s in runs if stem == "library"), None)
    if refresh is not None:
        line += (f" The refresh the scores come from answered {num((refresh.get('counts') or {}).get('cached'))} of its "
                 f"{num(refresh.get('items'))} items from the cache and was billed "
                 f"${num((refresh.get('billing') or {}).get('cost_usd'), 4)}.")
    return ["", line]


def _decision_x1(data: dict) -> list[str]:
    out = ["### X1: scenario labels and audit", ""]
    run = _run(data, "X1", "library")
    line = _absent("X1", run, "no results/neural/decisions/X1-library-scores.json (`decide --job X1`)")
    if line:
        return out + [line]
    x = run["scores"]
    auc, f = x.get("control_auroc") or {}, x.get("soft_filter") or {}
    works = bool(f.get("works"))
    review = f.get("review_list") or []
    out += [f"{num(x.get('labels_answered'))} of {num(x.get('labels'))} scenarios labeled, and {num(x.get('controls'))} "
            "negative controls (code-made corruptions mixed with accepted scenarios, rendered without plausibility "
            "measures in both arms).", "",
            f"Plausibility on the controls: AUROC {ci(auc, 3)} against the bar of {num(f.get('threshold'), 2)}, so "
            f"plausibility {'works' if works else 'does not work'} as a soft filter. "
            + (f"{num(len(review))} scenarios, whose most likely level is one of the two lowest, go to a review list; "
               "none is deleted." if works else "No review list is drawn; none is deleted.")]
    by = x.get("control_scores_by_corruption") or {}
    if by:
        order = sorted(by, key=lambda k: (k != "clean", k))
        out += ["", "Mean plausibility score (0, not plausible, to 4, highly plausible) by control:", ""]
        out += table(["Control", "Scenarios", "Mean score"],
                     [[k.replace("_", " "), num(by[k].get("n")), num(by[k].get("mean_score"), 2)] for k in order])
    levels = x.get("plausibility_levels") or {}
    if levels:
        out += ["", "Most likely plausibility level across the library: "
                + ", ".join(f"level {k} {num(levels[k])}" for k in sorted(levels, key=int)) + "."]
    fa, ea = x.get("failure_focus_agreement") or {}, x.get("edge_tag_agreement") or {}
    out += ["", f"The dominant failure agrees with the planner's failure focus for {pct(fa.get('share'))} of "
                f"{num(fa.get('n'))} scenarios. Edge-tag questions were answered yes for {pct(ea.get('share_yes'))} of the "
                f"{num(ea.get('asked'))} asked; only the tags each scenario's brief requested were asked, so this measures "
                "a lean toward yes rather than agreement."]
    return out


def _share(v) -> float:
    return v["share"] if isinstance(v, dict) else v


def _top_action(run: dict) -> str:
    """Drex Max's most-chosen action and its share: from the scores' choices record, else from the run's counted
    choices file when it was counted from the very outputs the scores were computed from."""
    x, counted = run["scores"] or {}, run.get("choices")
    actions = ((x.get("choices") or {}).get("model") or {}).get("action") or {}
    if actions:
        top = min(actions.items(), key=lambda kv: (-_share(kv[1]), kv[0]))   # ties by name, as scored
        return f"`{top[0]}`, {pct(_share(top[1]), 0)}"
    if counted is not None:
        if counted.get("outputs_sha256") != (x.get("provenance") or {}).get("outputs_sha256"):
            return "not shown: the counted choices are not of the outputs these scores read"
        actions = ((counted.get("model") or {}).get("action")) or {}
        if actions:
            top = min(actions.items(), key=lambda kv: (-_share(kv[1]), kv[0]))   # ties by name, as scored
            return f"`{top[0]}`, {pct(_share(top[1]), 0)} (shares counted from this round's outputs)"
    return "not recorded in this scores file"


def _x2_round_row(label: str, run: dict) -> list[str]:
    x = run["scores"]
    m = x.get("methods") or {}

    def cell(metric):
        return (f"{ci((m.get('model') or {}).get(metric), 1, 100)}; {_pts((m.get('R1') or {}).get(metric))}; "
                f"{_pts((m.get('R2') or {}).get(metric))}")
    return [label, num(x.get("alerts")), cell("role_accuracy"), cell("place_accuracy"), cell("action_appropriate"),
            _top_action(run)]


def selected_round(data: dict) -> tuple[str | None, str]:
    """The prompt round test uses: the one with the higher validation role accuracy (a tie keeps round one), and
    the sentence that says so."""
    scored = []
    for stem, name in (("val-p1", "round one"), ("val", "round two")):
        run = _run(data, "X2", stem)
        if run["scores"] is not None:
            role = (((run["scores"].get("methods") or {}).get("model") or {}).get("role_accuracy") or {}).get("point")
            prompt = (run["summary"] or {}).get("prompt_version") or ("p1" if stem == "val-p1" else "p2")
            scored.append((name, prompt, role))
    if len(scored) < 2:
        return None, ("The round test uses is the one with the higher validation role accuracy; it is named once both "
                      "rounds are scored.")
    (n1, p1, r1), (n2, p2, r2) = scored
    pick = (n2, p2, r2) if (r2 or 0) > (r1 or 0) else (n1, p1, r1)
    return pick[1], (f"Selection: test uses the round with the higher validation role accuracy, {pick[0]} "
                     f"({pick[1]}, {pct(pick[2])} against {pct(r1 if pick[0] == n2 else r2)}).")


def _decision_x2_val(data: dict) -> list[str]:
    out = ["### X2: prompt rounds on validation (not binding)", ""]
    rows, missing = [], []
    for stem, label, why in (("val-p1", "Round one (p1)", "no X2-val-p1-scores.json"),
                             ("val", "Round two (p2)", "no X2-val-scores.json yet")):
        run = _run(data, "X2", stem)
        line = _absent(f"X2 {label.lower()}", run, why)
        if line:
            missing.append(line)
        else:
            rows.append(_x2_round_row(label, run))
    if rows:
        out += ["Percentages with Drex Max's 95% interval, then R1 and R2 on the same alerts:", ""]
        out += table(["Round", "Alerts", "Role accuracy, % (Drex Max; R1; R2)", "Place accuracy, %",
                      "Action appropriate, %", "Drex Max's most-chosen action"], rows)
        out.append("")
    out += missing + ([""] if missing else [])
    out += [selected_round(data)[1], "", "Validation is for prompt work only; no number on it is binding (spec §8.7)."]
    return out


def _x2_methods_table(x: dict) -> list[str]:
    m = x.get("methods") or {}
    return table(["Method", "Place accuracy, %", "Action appropriate, %", "Role accuracy, %",
                  "Breaches averted per 100 alerts", "Recollections per 100 alerts"],
                 [[METHOD_NAMES[k], ci(m[k].get("place_accuracy"), 1, 100), ci(m[k].get("action_appropriate"), 1, 100),
                   ci(m[k].get("role_accuracy"), 1, 100), ci(m[k].get("breaches_averted_per_100_alerts")),
                   ci(m[k].get("recollections_started_per_100_alerts"))] for k in ("model", "R1", "R2") if k in m])


def _x2_slice_table(title: str, slices: dict) -> list[str]:
    def p(block, method, metric, scale=100.0):
        return _pts(((block.get("methods") or {}).get(method) or {}).get(metric), scale)
    return table([title, "Alerts", "Role, %: Drex Max", "R1", "R2", "Action, %: Drex Max", "Averted per 100: Drex Max"],
                 [[f"`{k}`", num(v.get("alerts")), p(v, "model", "role_accuracy"), p(v, "R1", "role_accuracy"),
                   p(v, "R2", "role_accuracy"), p(v, "model", "action_appropriate"),
                   p(v, "model", "breaches_averted_per_100_alerts", 1.0)] for k, v in slices.items()])


def _decision_x2_test(data: dict) -> list[str]:
    out = ["### X2 on test (binding)", ""]
    run = _run(data, "X2", "test")
    line = _absent("X2 on test", run, "it runs after the TracerNet test looks, and its decision-layer look relocks "
                                      "the shared test lock with the decision model's hash (spec §8.7)")
    if line:
        return out + [line]
    x, s = run["scores"], run["summary"] or {}
    pick, _ = selected_round(data)
    used = s.get("prompt_version")
    out += [f"{num(x.get('alerts'))} alerts from {num(x.get('scenarios'))} scenarios, {num(x.get('scored_alerts'))} with a "
            f"true place at the alert tick; prompt {used or 'n/a'}"
            + ("" if pick is None or used is None else (" (the selected round)." if used == pick else
                                                        f" (NOT the selected round, {pick})."))]
    a = x.get("adoption") or {}
    rows = []
    for k in ("R1", "R2"):
        d = ((x.get("differences") or {}).get(f"model - {k}") or {}).get("role_accuracy") or {}
        rows.append([f"Drex Max minus {k}", ci(d, 1, 100), "at least +5.0, interval above zero",
                     held(bool(((a.get("checks") or {}).get(k) or {}).get("holds")))])
    out += ["", "The adoption rule, binding on test, in percentage points of role accuracy:", ""]
    out += table(["Difference", "Points (95% interval)", "Needed", "Outcome"], rows)
    source = "Drex Max's dispatch" if a.get("holds") else "rule R2's dispatch"
    out += ["", f"The adoption rule {'held' if a.get('holds') else 'did not hold'} (binding): any dispatch the exports "
                f"carry is {source}. The demo's specimens come from `legacy_v1`, whose v1 store records no route, unit "
                "kind or staffing roster, so neither dispatcher can run on them and `docs/demo/neural.json` carries no "
                "dispatch (null).", "", "Every method on the same alerts, with 95% intervals:", ""]
    out += _x2_methods_table(x)
    yes_rows = []
    for q, label in (("shared_problem", "Shared problem"), ("escalate", "Escalate")):
        b = x.get(q) or {}
        for who, name in (("model", "Drex Max"), ("R2", "R2")):
            yes_rows.append([f"{label}: {name}", ci(b.get(f"{who}_auroc"), 3), ci(b.get(f"{who}_auprc"), 3),
                             ci(b.get(f"{who}_precision"), 3), ci(b.get(f"{who}_recall"), 3)])
    sp, es = x.get("shared_problem") or {}, x.get("escalate") or {}
    out += ["", f"The yes-no questions, against their labels: a shared problem ({num(sp.get('positives'))} of "
                f"{num(sp.get('alerts'))} alerts) and an unrecoverable specimen ({num(es.get('positives'))} of "
                f"{num(es.get('alerts'))}); Drex Max says yes at {num(sp.get('threshold'), 1)}, fixed in advance.", ""]
    out += table(["Question and method", "AUROC", "AUPRC", "Precision", "Recall"], yes_rows)
    slices = x.get("slices") or {}
    for key, title in (("by_place", "True place"), ("by_route_kind", "Route kind")):
        if slices.get(key):
            out += ["", f"By {title.lower()} (points; the intervals are in the scores file):", ""]
            out += _x2_slice_table(title, slices[key])
    cf = x.get("counterfactual") or {}
    out += ["", f"The counterfactual behind \"breaches averted\" (`source: {cf.get('source', 'assumed')}`): acting on an "
                f"appropriate action finds the specimen {num(cf.get('found_after_alert_min'), 0)} minutes after the "
                f"alert, and it is received {num(cf.get('received_after_found_min'), 0)} minutes later; for a lost "
                "specimen it starts a recollection, counted separately."]
    return out


def _decision_x3(data: dict) -> list[str]:
    out = ["### X3: can a decision model forecast?", ""]
    run = _run(data, "X3", "test")
    line = _absent("X3 on test", run, "it runs after the TracerNet test looks, under the same lock as X2 (spec §8.7)")
    if line:
        return out + [line]
    x = run["scores"]
    m = x.get("methods") or {}
    out += [f"{num(x.get('scored_ticks'))} of {num(x.get('ticks'))} test ticks from {num(x.get('scenarios'))} scenarios "
            f"({num(x.get('refused_or_unanswered'))} refused or unanswered, left out for every method), "
            f"{num(x.get('misses'))} of them misses; the same ticks for all three, no threshold tuned.", ""]
    names = {"decision_model": "Drex Max", "tracernet": "TracerNet", "analytic_local": "Analytic model, local refit"}
    out += table(["Method", "AUROC", "AUPRC", "Brier", "ECE"],
                 [[names[k], *(ci((m.get(k) or {}).get(metric), 3) for metric in ("auroc", "auprc", "brier", "ece"))]
                  for k in names if k in m])
    d = ((x.get("differences") or {}).get("decision_model - tracernet") or {}).get("auroc") or {}
    if d.get("low") is None or d.get("high") is None:
        verdict = "No reading: the AUROC difference with TracerNet has no interval."
    elif d["high"] < 0:
        a = ((x.get("differences") or {}).get("decision_model - analytic_local") or {}).get("auroc") or {}
        also = (f" and the local analytic refit ({ci(a, 3)})" if a.get("high") is not None and a["high"] < 0 else "")
        verdict = (f"No: on the same ticks Drex Max ranks the misses worse than TracerNet (AUROC difference "
                   f"{ci(d, 3)}){also}. X3 compares Drex Max with the risk models; it leaves the primary-model "
                   "verdict of section 1 as it is.")
    elif d["low"] > 0:
        verdict = f"Yes, on these ticks: Drex Max ranks the misses better than TracerNet (AUROC difference {ci(d, 3)})."
    else:
        verdict = (f"Not distinguishably: the AUROC difference with TracerNet, {ci(d, 3)}, spans zero.")
    return out + ["", verdict]


def _x4_state(data: dict) -> str:
    x = _run(data, "X2", "test")["scores"]
    if x is None:
        return " X2 has not been scored on test yet."
    if (x.get("adoption") or {}).get("holds"):
        return " The rule held on test, so X4 is the way to run the dispatcher without live calls, if it is built."
    return " The rule did not hold on test, so X4 is not needed."


def section_decisions(data: dict) -> list[str]:
    """Spec §8.10 item 8 from results/neural/decisions/ (src/decide/, src/pipeline/dispatch_eval.py)."""
    out = ["## 8. Decision layer", ""]
    runs = data.get("decisions") or {}
    if not any(r["summary"] is not None or r["scores"] is not None for r in runs.values()):
        return out + ["Not run: the decision layer of spec §11 (task N10) has not run yet."]
    from src.decide import client as dc, distill

    notice = next((r["summary"]["notice"] for r in runs.values() if r["summary"] and r["summary"].get("notice")),
                  dc.NOTICE)
    for part in (_decision_what(), _decision_spend(data), _decision_x1(data), _decision_x2_val(data),
                 _decision_x2_test(data), _decision_x3(data),
                 ["### X4: the distilled dispatcher", "", distill.REASON + _x4_state(data)],
                 ["### Notice", "", notice]):
        out += part + [""]
    return out[:-1]


def section_data(data: dict) -> list[str]:
    out = ["## 9. Data", ""]
    m = data["manifest"]
    if m is None:
        out.append(f"{NOT_RUN}: no library manifest at datasets/scenarios-v2.manifest.json.")
    else:
        out += [m.get("notice", ""), "",
                f"The library: {num(m.get('scenarios'))} scenarios ({num(m.get('bytes'))} bytes, SHA-256 "
                f"`{m.get('sha256')}`), clinical review: {m.get('clinical_review')}. Prices verified at "
                f"{m.get('prices_verified_at')}.", ""]
        rows = []
        for job, j in sorted((m.get("jobs") or {}).items()):
            c = j.get("counts") or {}
            rows.append([job, ", ".join(sorted({a for arms in (j.get("arms") or {}).values() for a in arms})),
                         j.get("tier", ""), num(c.get("briefs")), num(c.get("requests")), num(c.get("accepted")),
                         pct(c["accepted"] / c["briefs"]) if c.get("briefs") else "n/a", num(c.get("repaired")),
                         num(c.get("dropped")), num(c.get("refused")),
                         ", ".join(f"{k} {v}" for k, v in sorted((c.get("rejections_by_layer") or {}).items())),
                         f"${j.get('spent_usd', 0):.4f}",
                         f"${j['spent_usd'] / c['accepted']:.6f}" if c.get("accepted") else "n/a"])
        out += table(["Job", "Model arms", "Tier", "Briefs", "Requests", "Accepted", "Acceptance", "Repaired",
                      "Dropped", "Refused", "Rejections by layer", "Spent", "Per accepted"], rows)
        out += ["", "Accepted by job and model: " + ", ".join(f"{k} {v:,}" for k, v in
                                                              sorted((m.get("accepted_by_job_and_model") or {}).items()))
                + ". By stratum: " + ", ".join(f"{k} {v:,}" for k, v in sorted((m.get("accepted_by_stratum") or {}).items()))
                + ". By archetype: " + ", ".join(f"{k} {v:,}" for k, v in sorted((m.get("accepted_by_archetype") or {}).items()))
                + "."]
        cov = m.get("coverage") or {}
        under = [(k, c) for k, c in sorted((cov.get("cells") or {}).items()) if c["count"] < c["target"]]
        out += ["", f"Coverage: {'every cell, factor level and edge tag within its tolerance' if cov.get('ok') else 'NOT within tolerance'}; "
                    f"{len(under)} of {len(cov.get('cells') or {})} stratum-by-archetype cells below their exact "
                    "targets" + (" (" + ", ".join(f"{k} {c['count']} of {c['target']}" for k, c in under) + ")" if under else "") + "."]
        d = m.get("dispersion") or {}
        lib, rnd = (d.get("library") or {}).get("all") or {}, (d.get("code_random") or {}).get("all") or {}
        nd = m.get("near_duplicates") or {}
        sp = m.get("splits") or {}
        out += ["", f"Dispersion: Vendi score {num(lib.get('vendi'))} on {num(lib.get('n'))} library scenarios against "
                    f"{num(rnd.get('vendi'))} on {num(rnd.get('n'))} code-random ones; mean nearest-neighbor distance "
                    f"{num(lib.get('mean_nn'), 2)} against {num(rnd.get('mean_nn'), 2)}. Near duplicates left out: "
                    f"{num(nd.get('left_out'))}.",
                "", f"Splits: " + ", ".join(f"{k} {v:,}" for k, v in (sp.get("counts") or {}).items())
                + f" scenarios in {num(sp.get('families'))} families. {m.get('ood_definition', '')}."]
    p = data["pilot"]
    out += ["", "### The pilot gate (O1)", ""]
    if p is None:
        out.append(f"{NOT_RUN}.")
    else:
        out += table(["Arm", "Briefs", "Accepted", "Spent", "Per accepted", "Eligible"],
                     [[a, num(x.get("briefs")), num(x.get("accepted")), f"${x.get('spent_usd', 0):.4f}",
                       f"${x.get('cost_per_accepted_usd', 0):.6f}", "yes" if x.get("eligible") else "no"]
                      for a, x in sorted((p.get("models") or {}).items())])
        out += ["", f"Gate passed: {'yes' if p.get('passed') else 'no'}; core and edge model: "
                    f"{(p.get('choice') or {}).get('core')} and {(p.get('choice') or {}).get('edge')}."]
    r2 = data["round2"]
    out += ["", "### Round 2 (O3)", ""]
    if r2 is None:
        out.append(f"{NOT_RUN}: no weak-spot plan (results/neural/generation/round2-plan.json).")
    else:
        out += [f"Planned {num(r2.get('briefs'))} briefs from the validation weak spots "
                + ", ".join(f"`{x['slice']}` ({x['dimension'].replace('_', ' ')})" for x in r2.get("chosen") or [])
                + "; see results/neural/generation/round2.md."]
    lg = data["ledger"]
    out += ["", "### Spend", ""]
    if lg is not None:
        out += [f"From {lg['source']}:", ""]
        out += table(["Job", "Account", "Cap", "Spent", "Held"],
                     [[j, x.get("account", ""), f"${x.get('cap_usd', 0):.2f}", f"${x.get('spent_usd', 0):.4f}",
                       f"${x.get('held_usd', 0):.4f}"] for j, x in (lg.get("jobs") or {}).items()])
        accts = lg.get("accounts") or {}
        if accts:
            out += ["", "Accounts: " + "; ".join(f"{a} ${x.get('spent_usd', 0):.4f} spent of ${x.get('cap_usd', 0):.2f}"
                                                 for a, x in accts.items()) + "."]
    elif m is not None and m.get("ledger"):
        snap = m["ledger"]
        out.append("From the library manifest's record of the ledger at the freeze (the ledger itself was not read): "
                   + ", ".join(f"{j} ${v:.4f}" for j, v in sorted(snap.get("spent_usd_by_job", {}).items()))
                   + f"; ${snap.get('spent_usd_total', 0):.4f} of ${snap.get('cap_usd', 0):.2f}.")
    else:
        out.append(f"{NOT_RUN}: no ledger totals were available.")
    out += ["", "### Evaluation sets", ""]
    rows = [[s, num(r["counts"]["clusters"]), r["settings"]["cluster"], num(r["counts"]["scored_specimens"]),
             num(r["counts"]["breaches"]), num(r["counts"]["never_received"]),
             "yes" if ne.TRACERNET in (r.get("methods") or {}) else "no"]
            for s, r in data["ev"].items() if r is not None]
    out += table(["Set", "Clusters", "Cluster", "Scored specimens", "Breaches", "Never received", "TracerNet scored"],
                 rows) if rows else [f"{NOT_RUN}: no evaluation has been written."]
    return out


# --- Section 10: limits, next steps and the §8.9 recommendation ------------------------------------------------------------

def recommendations(data: dict) -> list[str]:
    """Spec §8.9's recommendations the evidence supports, each with why; [] when the rule held and nothing applies.
    Rules stated on the page: a lead on disruption slices is at least 5 minutes over the best baseline in the
    median or the capped mean warning on `test`'s disrupted slice at 6 alerts per shift, 3-2-1 weights; the
    specimen-only ablation equals the full model when its mean val_sel is within 1% of the full arm's (the
    selection rule's tolerance)."""
    test = data["ev"].get("test")
    rule = (test or {}).get("decision_rule")
    if rule is None:
        return []
    v, out = rule["verdict"]["held"], []
    margin = ne.MIN_WARNING_GAIN_MIN
    disrupted = ((test.get("slices") or {}).get("disruption") or {}).get("disrupted") or {}

    def slice_value(m, w, k):
        return ((disrupted.get(m) or {}).get(weighting_of(test, m, w)) or {}).get(k)
    leads = []
    for k in ("median_warning_all_breaches_min", "capped_mean_warning_min"):
        tn = slice_value(ne.TRACERNET, "3-2-1", k)
        base = [x for x in (slice_value(b, "3-2-1", k) for b in ne.BASELINES) if x is not None]
        if tn is not None and base and tn - max(base) >= margin:
            leads.append(f"{k.replace('_min', '').replace('_', ' ')} {num(tn)} against the best baseline's {num(max(base))}")
    if not v["R1"] and leads:
        out.append("**Hybrid.** TracerNet ties overall (R1 failed) but leads on the disruption slice ("
                   + "; ".join(leads) + "): score routine silence with the analytic model or the window rule and let "
                   "TracerNet add a context alert when its p_miss exceeds the analytic risk by a fixed margin. Evaluate "
                   "the blend on validation before claiming anything.")
    if v["R1"] and not v["R3"]:
        out.append("**Recalibrate.** TracerNet leads on warning (R1 held) and fails calibration (R3): fit an isotonic "
                   "map per window class on validation; re-running test then needs a relock and a reason.")
    a6 = ((data["ablations"] or {}).get("ablations") or {}).get("A6") or {}
    arms = a6.get("arms") or {}
    full, ctx = (arms.get("full") or {}).get("val", {}).get("val_sel"), (arms.get("no_context") or {}).get("val", {}).get("val_sel")
    equal = bool(full and ctx and full.get("mean") and ctx.get("mean") is not None
                 and abs(ctx["mean"] - full["mean"]) <= 0.01 * full["mean"])
    if not v["R1"] and not leads and equal:
        out.append(f"**H₀ holds here too.** TracerNet leads nowhere, and the specimen-only ablation equals the full "
                   f"model (val_sel {num(ctx['mean'], 5)} against {num(full['mean'], 5)}): elapsed time and the window "
                   "are enough, and a laboratory can deploy the analytic model and the window rule with no "
                   "machine-learning infrastructure.")
    legacy = (data["ev"].get("legacy_v1") or {}).get("decision_rule")
    if v["R1"] and v["R2"] and v["R3"] and legacy is not None and not legacy["R1"]["held"]:
        out.append("**Mixed generators.** TracerNet wins on v2 and loses on `legacy_v1` (R1 failed there): it learned "
                   "this generator. Train on mixed generators (A8) and treat the v2 result as specific to it.")
    return out


def section_limits(data: dict) -> list[str]:
    out = ["## 10. Limits and next steps", "", "- " + NOTICE,
           "- `legacy_v1` has 6 clusters, so its intervals are wide.",
           "- Under the strict 6-per-shift budget a laboratory with more breaches per shift than alerts gets no alerts "
           "from a method with no in-budget knob; section 1 counts those scenarios per method."]
    for split, rec in sorted(((data["ablations"] or {}).get("scoring") or {}).items()):
        shares = [s for s in rec.get("shares") or [] if s is not None and float(s) < 1.0]
        if shares:
            counts = ", ".join(str(n) for n in rec.get("scenarios_scored") or [])
            out.append(f"- The ablation arms were scored on {split} on a fixed salted share of its scenarios "
                       f"({', '.join(f'{float(s):.0%}' for s in shares)}; {counts} scenarios), the same for every arm, "
                       "so their intervals are wider than the main evaluation's"
                       + ("." if rec.get("same_selection") else "; the arms were NOT all scored on one selection."))
    out += [line if line.startswith("- ") else f"- {line}" for line in data.get("limits") or []]
    test = data["ev"].get("test")
    rule = (test or {}).get("decision_rule")
    out += ["", "### Recommendation (spec §8.9)", ""]
    if rule is None:
        return out + [f"{NOT_RUN}: it follows the verdict on `test`."]
    recs = recommendations(data)
    if rule["verdict"]["tracernet_primary"]:
        out.append("The rule held: TracerNet becomes the primary risk model.")
        return out + ([""] + recs if recs else [])
    out.append("The rule failed: the analytic model stays primary and TracerNet is reported as a challenger result.")
    if recs:
        return out + [""] + recs
    return out + ["", "None of §8.9's four patterns is supported by the evidence above (a lead on the disruption "
                      "slice, a warning lead that fails calibration, a specimen-only ablation equal to the full model, "
                      "or a v2 win that fails on `legacy_v1`), so the report recommends none of them."]


# --- Section 11: how to reproduce --------------------------------------------------------------------------------------

COMMANDS = ("python -m src.neural.pipeline freeze --jobs O1,O2,O3", "python -m src.neural.pipeline simulate",
            "python -m src.neural.pipeline store", "python -m src.neural.pipeline train --hpo",
            "python -m src.neural.pipeline train --rung A|B|C --seeds 0,1,2", "python -m src.neural.pipeline select",
            "python -m src.neural.pipeline calibrate",
            "python -m src.neural.pipeline evaluate --split val --artifact artifacts/tracernet/v1 --matched",
            "python -m src.neural.pipeline weakspots",
            "python -m src.neural.pipeline evaluate --split test --artifact artifacts/tracernet/v1 --matched",
            "python -m src.neural.pipeline evaluate --split test_ood --artifact artifacts/tracernet/v1 --matched",
            "python -m src.neural.pipeline evaluate --split legacy_v1 --artifact artifacts/tracernet/v1 --matched",
            "python -m src.neural.pipeline store --control",
            "python -m src.neural.pipeline evaluate --split control_v1world --artifact artifacts/tracernet/v1 "
            "--store data/store-v2-control --train-store data/store-v2 --matched",
            "python -m src.neural.pipeline ablate --set core", "python -m src.neural.pipeline ablate --collect",
            "python -m src.neural.pipeline explain --split test --matched",
            "python -m src.neural.pipeline export --source legacy_v1",
            "python -m src.neural.pipeline report")


def section_reproduce(data: dict) -> list[str]:
    out = ["## 11. How to reproduce", "", "```sh"] + list(COMMANDS) + ["```", "", "### Hashes", ""]
    m, art, lock = data["manifest"], data["artifact"], data["lock"]
    rows = []
    if m is not None:
        rows += [["Library", m.get("sha256")], ["Policy", m.get("policy_hash")], ["Schema", m.get("schema_hash")]]
    for s, r in data["ev"].items():
        if r is not None and (r.get("store") or {}).get("manifest_sha256"):
            rows.append([f"Store behind `{s}`", r["store"]["manifest_sha256"]])
    if art is not None:
        rows += [["Artifact manifest", data["artifact_sha256"]], ["Artifact store", art.get("store_hash")],
                 ["Artifact splits", art.get("splits_sha256")]]
    out += table(["Input", "SHA-256 or hash"], [[k, f"`{v}`" if v else "n/a"] for k, v in rows]) if rows else \
        [f"{NOT_RUN}: no hashed input exists yet."]
    out += ["", "### The model", ""]
    sel = data["selection"]
    if sel is None:
        out.append(f"{NOT_RUN}: no rung selection (results/neural/selection.json).")
    else:
        w = sel.get("warning_check") or {}
        out.append(f"Rung {sel.get('chosen')} chosen by the rule of spec §7.9 (lowest val_sel: {sel.get('by_val_sel')}); "
                   + ", ".join(f"rung {r} val_sel {num(x.get('val_sel'), 5)}" for r, x in sorted((sel.get("rungs") or {}).items()))
                   + f". Warning check: {w.get('status')}.")
    hpo = data["hpo"]
    if hpo is None:
        out.append(f"{NOT_RUN}: no search summary (results/neural/hpo.json).")
    else:
        b = hpo.get("best") or {}
        out.append(f"Search: " + ", ".join(f"{k.lower()} {v}" for k, v in (hpo.get("counts") or {}).items())
                   + f" trials; best val_sel {num(b.get('value'), 5)} (trial {b.get('number')}) with "
                   + ", ".join(f"{k} {v}" for k, v in sorted((b.get("params") or {}).items())) + ".")
    out += ["", "### Test runs", ""]
    if lock is None and not data["test_runs"]:
        out.append(f"{NOT_RUN}: no test set has been scored (no results/neural/test_lock.json).")
    else:
        if lock is not None:
            out.append(f"Locked first on `{lock.get('first_split')}` at {lock.get('time')}"
                       + (f", relocked from {lock['relocked_from']}" if lock.get("relocked_from") else "") + ".")
        out += [""] + table(["Time", "Set", "Status", "Reason"],
                            [[e.get("time", ""), e.get("split", ""), e.get("status", ""), e.get("reason", "")]
                             for e in data["test_runs"]])
    if data["ablation_test_runs"]:
        out += ["", "Ablation test runs (each arm under its own lock, after the main lock):", ""]
        out += table(["Time", "Set", "Status", "Reason", "Artifact"],
                     [[e.get("time", ""), e.get("split", ""), e.get("status", ""), e.get("reason", ""),
                       f"`{str((e.get('hashes') or {}).get('artifact_sha256', ''))[:16]}`"] for e in data["ablation_test_runs"]])
    return out


# --- The page ----------------------------------------------------------------------------------------------------------

def section_acceptance(data: dict) -> list[str]:
    """Spec §17's acceptance appendix, from results/neural/acceptance.md, one heading level down."""
    text = data.get("acceptance")
    if not text:
        return ["## Appendix: acceptance criteria (spec §17)", "", f"{NOT_RUN}: the acceptance audit of task N11."]
    return [("#" + line) if line.startswith("#") else line for line in text.rstrip("\n").splitlines()]


def render(data: dict, plots_dir: Path, root: Path = ROOT) -> str:
    lines = ["# TracerNet against the baselines: the v2 report", "", verdict_paragraph(data), ""]
    for section in (section_verdict(data), section_headline(data), section_calibration(data, plots_dir, root),
                    section_slices(data), section_beside(data), section_ablations(data), section_explanations(data),
                    section_decisions(data), section_data(data), section_limits(data), section_reproduce(data),
                    section_acceptance(data)):
        lines += section + [""]
    return "\n".join(lines).rstrip("\n") + "\n"


def report_command(*, out_path: Path | None = None, inputs: dict | None = None, ledger_json: Path | None = None,
                   out=print) -> int:
    """`report`: render results/neural/REPORT.md from whatever results exist."""
    inputs = inputs or default_inputs()
    if ledger_json is not None:
        inputs = inputs | {"ledger_json": Path(ledger_json)}
    data = load(inputs)
    out_path = Path(out_path or REPORT_PATH)
    text = render(data, Path(inputs["plots"]), Path(inputs["root"]))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    present = [s for s, r in data["ev"].items() if r is not None]
    out(f"report: {out_path} from {', '.join(present) or 'no evaluation'}"
        f"{'' if data['ev'].get('test') else ' (test not run yet)'}")
    return 0
