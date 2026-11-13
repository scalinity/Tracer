"""The v2 report (docs/neural/spec.md §8.9, §8.10): the first paragraph from a constructed verdict, the counts and the
non-binding view beside R1, test_ood whole and on its out-of-distribution scenarios, §8.9's recommendations, missing
inputs as "not run yet", the ledger read without opening it for writing, and the real evaluation shape."""

import copy
import json
import re
from pathlib import Path

import numpy as np
import pytest

from src.neural import devlib, report as R
from src.pipeline import neural_eval as ne
from src.scenarios import ledger as L

BUDGETS = ["1/shift", "2/shift", "4/shift", "6/shift", "5/100", "10/100", "30/100"]
WEIGHTED = {"tracernet": True, "analytic_global": True, "analytic_local": True, "window_rule": False}


def interval_of(point, spread=0.5):
    return {"point": point, "low": point - spread, "high": point + spread, "n_used": 2000, "n_excluded": 0}


def make(set_name="test", *, tn=2.0, base=0.0, low=0.5, tn_30=None, brier=(0.07, 0.075), clusters=772,
         no_alert=261, disrupted_lead=0.0):
    """A constructed evaluation in evaluate_split's shape: TracerNet's median warning over all breaches `tn`
    against `base` for every baseline (`tn_30` at 30 per 100), equal alert volumes and losses, and the decision
    rule computed by the evaluation's own decide()."""
    tn_30 = tn if tn_30 is None else tn_30
    r = {"set": set_name, "notice": ne.NOTICE,
         "counts": {"tuning_groups": clusters, "clusters": clusters, "scored_specimens": 1000, "breaches": 100,
                    "never_received": 10, "scored_shifts": 21.0 * clusters},
         "settings": {"headline": "6/shift", "budgets": BUDGETS, "cluster": "scenario", "bootstrap": {"resamples": 2000}},
         "methods": {m: {"weighted": w, "probabilistic": m != "window_rule"} for m, w in WEIGHTED.items()},
         "operating_points": {}, "knobs": {}, "differences": {}, "calibration": {}, "discrimination": {},
         "slices": {"disruption": {"disrupted": {}}}}
    for b in BUDGETS:
        r["operating_points"][b], r["knobs"][b], r["differences"][b] = {}, {}, {}
        for m, weighted in WEIGHTED.items():
            warn = (tn_30 if b == "30/100" else tn) if m == "tracernet" else base
            values = {k: interval_of(1.0) for k in ne.METRICS} | {
                "median_warning_all_breaches_min": interval_of(warn), "capped_mean_warning_min": interval_of(warn + 10),
                "realized_alerts_per_shift": interval_of(6.0), "missed_loss_rate": interval_of(0.1, 0.0),
                "catch_rate": interval_of(0.5, 0.1)}
            ws = ("3-2-1", "equal") if weighted else ("none",)
            r["operating_points"][b][m] = {w: copy.deepcopy(values) for w in ws}
            r["knobs"][b][m] = {w: {"no_alert_groups": 0 if m == "tracernet" else no_alert} for w in ws}
        for w in ("3-2-1", "equal"):
            gain = (tn_30 if b == "30/100" else tn) - base
            r["differences"][b][w] = {m: {"median_warning_all_breaches_min": interval_of(gain, gain - low),
                                          "capped_mean_warning_min": interval_of(gain, gain - low) | {"share_ahead": 0.9},
                                          "missed_loss_rate": {"point": 0.0, "low": -0.01, "high": 0.01}}
                                      for m in WEIGHTED if m != "tracernet"}
    for m in ("tracernet", "analytic_global", "analytic_local"):
        b30 = brier[0] if m == "tracernet" else brier[1]
        r["calibration"][m] = {L: {"landmark_min": int(L), "n": 500, "breach_rate": 0.1, "brier": b30,
                                   "brier_of_the_base_rate": 0.09, "ece": 0.01, "slope": 1.0, "intercept": 0.0,
                                   "bins": [{"from": 0.0, "to": 0.5, "n": 400, "predicted": 0.1, "observed": 0.12},
                                            {"from": 0.5, "to": 1.0, "n": 100, "predicted": 0.7, "observed": 0.65}]}
                               for L in ("30", "60")}
    for m in WEIGHTED:
        r["discrimination"][m] = {f"landmark_{x}": {"auroc": 0.9, "auprc": 0.5} for x in (15, 30, 60, 90)} | \
            {"pooled": {"auroc": 0.95, "auprc": 0.7}}
        ws = ("3-2-1", "equal") if WEIGHTED[m] else ("none",)
        lead = disrupted_lead if m == "tracernet" else 0.0
        r["slices"]["disruption"]["disrupted"][m] = {w: {"specimens": 400, "breaches": 50,
                                                         "median_warning_all_breaches_min": base + lead,
                                                         "capped_mean_warning_min": base + lead} for w in ws}
    rule = ne.decide(R.headline_points(r, "6/shift"), {w: r["differences"]["6/shift"][w] for w in ne.WEIGHTINGS},
                     R.brier_of(r))
    r["decision_rule"] = rule | {"set": set_name, "binding": set_name == "test"}
    return r


def inputs(tmp_path, evaluations=None, **files):
    """Every input under tmp_path, written only where given."""
    i = R.default_inputs(tmp_path)
    for s, doc in (evaluations or {}).items():
        i["evaluations"][s].parent.mkdir(parents=True, exist_ok=True)
        i["evaluations"][s].write_text(json.dumps(doc))
    for key, doc in files.items():
        Path(i[key]).parent.mkdir(parents=True, exist_ok=True)
        Path(i[key]).write_text(doc if isinstance(doc, str) else json.dumps(doc))
    return i


def page(tmp_path, **kw) -> str:
    i = inputs(tmp_path, **kw)
    out = tmp_path / "results" / "neural" / "REPORT.md"
    R.report_command(out_path=out, inputs=i, out=lambda *_: None)
    return out.read_text()


def section(text: str, number: int) -> str:
    return text.split(f"## {number}. ")[1].split("\n## ")[0]


def test_the_first_paragraph_states_each_rule_with_numbers_intervals_and_the_notice(tmp_path):
    text = page(tmp_path, evaluations={"test": make()})
    first = text.split("\n\n")[1]
    assert first.startswith("**Verdict on `test` at 6 alerts per shift, a chosen budget: R1 failed, R2 held, R3 held, "
                            "R4 held. The analytic model stays primary because R1 failed.**")
    assert "Under spec §8.6 the primary model follows R1 to R3, so R1 alone decides it." in first
    assert "TracerNet's median warning over all breaches was 2.0 min against window rule 0.0 (difference 2.0, " \
           "interval low 0.5)" in first
    assert "at 30 min TracerNet 0.0700 against 0.0750 (excess -0.0050)" in first
    assert ("R2 (deficit in never-received specimens alerted below 2 points) held at both weightings: at 3-2-1 weights "
            "it is even with all three, within 1 point (largest deficit 0.0 points, interval high 1.0)") in first
    assert R.NOTICE in first


def test_a_held_rule_names_tracernet_as_the_primary_model(tmp_path):
    first = page(tmp_path, evaluations={"test": make(tn=12.0, low=2.0)}).split("\n\n")[1]
    assert "R1 held, R2 held, R3 held, R4 held. TracerNet becomes the primary risk model: R1 to R3 held.**" in first


def floored():
    """Every R1 median 0 (no method catches half the breaches) while TracerNet leads on the capped mean warning, and
    the baselines realizing fewer alerts than TracerNet, so R4 fails."""
    r = make(tn=0.0, low=0.0)
    for m, by_w in r["operating_points"]["6/shift"].items():
        for v in by_w.values():
            v["catch_rate"] = interval_of(0.392 if m == "tracernet" else 0.3, 0.01)
            v["capped_mean_warning_min"] = interval_of(23.9 if m == "tracernet" else 10.0)
            if m != "tracernet":
                v["realized_alerts_per_shift"] = interval_of(4.0)
    r["discrimination"]["tracernet"]["landmark_30"]["auroc"] = 0.957
    rule = ne.decide(R.headline_points(r, "6/shift"), {w: r["differences"]["6/shift"][w] for w in ne.WEIGHTINGS},
                     R.brier_of(r))
    r["decision_rule"] = rule | {"set": "test", "binding": True}
    return r


def test_a_floored_r1_says_why_beside_the_capped_mean_and_the_warning_check_that_compared_zeros(tmp_path):
    text = page(tmp_path, evaluations={"test": floored()},
                selection={"chosen": "C", "warning_check": {"A": 0.0, "C": 0.0}})
    first, s1 = text.split("\n\n")[1], section(text, 1)
    assert ("The analytic model stays primary because R1 failed. R4 also failed, mostly because the baselines leave "
            "part of the 6-alert budget unused (the window rule alerts on nothing in 261 of 772 scenarios).**") in first
    assert "median warning over all breaches was 0.0 min at 3-2-1 and equal weights" in first
    assert "A missed breach counts as 0 minutes (spec §8.4)" in first and "best catch rate 39.2%" in first
    assert "TracerNet 23.9 min against 10.0 to 10.0 for R1's three baselines at 3-2-1 weights" in first
    assert "**Why R1 cannot separate the methods here.**" in s1 and "November 8 at 11:42 PM EST" in s1
    assert "compared rung C's validation median warning with rung A's, 0.0 against 0.0 min, so it tested nothing." in s1
    assert "R1 cannot separate" not in section(page(tmp_path / "plain", evaluations={"test": make()}), 1)


def test_a_hybrid_that_leads_overall_says_the_options_fit_imperfectly(tmp_path):
    ev = floored()
    ev["slices"]["disruption"]["disrupted"]["tracernet"] = {w: {"median_warning_all_breaches_min": 0.0,
                                                                "capped_mean_warning_min": 17.7}
                                                            for w in ("3-2-1", "equal")}
    s10 = section(page(tmp_path, evaluations={"test": ev}), 10)
    assert "The rule failed. The analytic model stays primary because R1 failed." in s10
    assert "**Hybrid.** §8.9's options fit this result imperfectly." in s10
    assert ("it leads on capped mean warning (23.9 min against the best baseline's 10.0 at 6 alerts per shift, 3-2-1 "
            "weights), AUROC (0.957 against 0.900 at 30 minutes of silence) and Brier score") in s10
    assert "The tie exists only in R1's floored median (section 1)." in s10 and "ties overall (R1 failed)" not in s10


def test_o1s_acceptance_counts_every_arm_and_the_lock_reads_first_then_relocked(tmp_path):
    jobs = {"O1": {"arms": {"all": ["a-low", "b-low", "c-medium"]}, "tier": "flex", "spent_usd": 0.6,
                   "counts": {"briefs": 24, "requests": 79, "accepted": 69}},
            "O2": {"arms": {"normal": ["b-low"], "edge": ["b-low"]}, "tier": "flex", "spent_usd": 40.0,
                   "counts": {"briefs": 100, "requests": 110, "accepted": 98}}}
    text = page(tmp_path, evaluations={"test": make()}, manifest={"jobs": jobs},
                lock={"first_split": "test", "time": "at-relock", "relocked_from": "at-first"})
    s9 = section(text, 9)
    assert "| O1 | a-low, b-low, c-medium | flex | 24 × 3 arms | 79 | 69 | 95.8% |" in s9
    assert "| O2 | b-low | flex | 100 | 110 | 98 | 98.0% |" in s9
    s11 = section(text, 11)
    assert "Test sets are scored under a hash lock; every rerun is listed in `results/neural/test_runs.jsonl`" in s11
    assert "Locked first on `test` at at-first, relocked at at-relock." in s11


def test_the_paper_analyses_are_not_run_with_their_reasons_and_the_caveats_sit_under_the_verdict(tmp_path):
    text = page(tmp_path, evaluations={"test": make()}, caveats="- The control flags leakage.\nNo bullet.\n")
    s5 = section(text, 5)
    assert "### Sensitivity sets and face validity (spec §8.11)" in s5
    assert "`sens_pace_slow`): not run." in s5 and "Blinded face-validity rating: not run." in s5
    assert text.split("\n\n")[2:4] == ["**Read with the verdict:**", "- The control flags leakage.\n- No bullet."]
    assert "Read with the verdict" not in page(tmp_path / "none", evaluations={"test": make()})


def test_beside_r1_the_scenarios_without_an_in_budget_knob_and_the_non_binding_view(tmp_path):
    s1 = section(page(tmp_path, evaluations={"test": make(tn=2.0, tn_30=11.0, low=2.0)}), 1)
    assert "| window_rule | none | 261 of 772 |" in s1 and "| tracernet | 3-2-1 | 0 of 772 |" in s1
    assert "At 30 per 100 specimens on `test`: R1 held, R2 held, R3 held." in s1
    assert "| tracernet | 3-2-1 | 12.0 | 12.0 | 12.0 | 12.0 | 12.0 | 12.0 | 21.0 |" in s1   # capped mean, every budget
    assert "non-binding" in s1 and "a secondary analysis" in s1


def test_every_budget_and_both_weightings_are_in_the_headline_table(tmp_path):
    s2 = section(page(tmp_path, evaluations={"test": make()}), 2)
    for title in ("1 alert per shift", "2 alerts per shift", "4 alerts per shift", "6 alerts per shift",
                  "5 alerts per 100 specimens", "10 alerts per 100 specimens", "30 alerts per 100 specimens"):
        assert f"### {title}\n" in s2
    assert s2.count("| tracernet | equal |") == 7 and s2.count("| window_rule | none |") == 7


def test_test_ood_is_shown_whole_and_on_its_out_of_distribution_scenarios(tmp_path):
    ood = make("test_ood", clusters=849)
    ood["subsets"] = {"ood_feature": make("test_ood", clusters=482)}
    s5 = section(page(tmp_path, evaluations={"test": make(), "test_ood": ood}), 5)
    whole, feature = s5.split("### test_ood, the scenarios that carry")
    assert "849 scenarios" in whole and "482 scenarios" in feature
    assert "legacy_v1\n\nThe v1 store's held-out runs 24 to 29: 6 clusters" in s5 and "Not run yet." in s5


def test_a_win_on_test_that_fails_on_test_ood_is_reported_as_exactly_that(tmp_path):
    s5 = section(page(tmp_path, evaluations={"test": make(tn=12.0, low=2.0), "test_ood": make("test_ood")}), 5)
    assert "TracerNet won on `test` and fails the rule on `test_ood`: reported as exactly that" in s5


def test_a_large_lead_on_the_negative_control_is_treated_as_leakage(tmp_path):
    s5 = section(page(tmp_path, evaluations={"control_v1world": make("control_v1world", tn=9.0, low=2.0)}), 5)
    assert "| 6/shift | median warning, all breaches | 9.0 | 2.0 to 16.0 | large: treated as leakage |" in s5
    assert "The lead is large somewhere above" in s5


@pytest.mark.parametrize("ev, extra, expected", [
    (make(tn=2.0, disrupted_lead=6.0), {}, "**Hybrid.**"),
    (make(tn=12.0, low=2.0, brier=(0.2, 0.075)), {}, "**Recalibrate.**"),
    (make(tn=2.0), {"ablations": {"ablations": {"A6": {"arms": {"full": {"val": {"val_sel": {"mean": 0.100}}},
                                                                "no_context": {"val": {"val_sel": {"mean": 0.1005}}}}}}}},
     "**H₀ holds here too.**"),
    (make(tn=2.0), {}, "None of §8.9's four patterns is supported"),
])
def test_a_failed_rule_gets_the_recommendation_the_evidence_supports(tmp_path, ev, extra, expected):
    s10 = section(page(tmp_path, evaluations={"test": ev}, **extra), 10)
    assert expected in s10


def test_a_v2_win_that_fails_on_legacy_v1_recommends_mixed_generators(tmp_path):
    s10 = section(page(tmp_path, evaluations={"test": make(tn=12.0, low=2.0), "legacy_v1": make("legacy_v1")}), 10)
    assert "The rule held" in s10 and "**Mixed generators.**" in s10


def test_missing_inputs_render_as_not_run_yet_and_never_as_numbers(tmp_path):
    text = page(tmp_path)
    assert text.split("\n\n")[1].startswith("**Verdict: not run yet.**")
    for n in (1, 2, 3, 4, 5, 6, 7, 9, 10, 11):
        assert "Not run yet" in section(text, n), n
    assert "Not run: the decision layer" in section(text, 8)
    assert not [line for line in text.splitlines() if line.startswith("|")]     # every table comes from an input
    assert not re.search(r"\d+\.\d+ min|\$\d", text)


def test_limits_name_the_ablation_scoring_share_and_carry_the_written_notes(tmp_path):
    scoring = {"val": {"arms": 43, "selections": ["a"], "same_selection": True, "shares": [0.25],
                       "salts": ["s"], "scenarios_scored": [273]},
               "test": {"arms": 43, "selections": ["a", "b"], "same_selection": False, "shares": [0.25],
                        "salts": ["s"], "scenarios_scored": [196]}}
    text = section(page(tmp_path, evaluations={"test": make()}, ablations={"scoring": scoring},
                        limits="- The ensemble's training was unstable.\nA note without its bullet.\n"), 10)
    assert "on val on a fixed salted share of its scenarios (25%; 273 scenarios)" in text
    assert "196 scenarios), the same for every arm" in text and "NOT all scored on one selection" in text
    assert "- The ensemble's training was unstable." in text and "- A note without its bullet." in text
    assert "salted share" not in section(page(tmp_path / "none", evaluations={"test": make()}), 10)


def test_validation_alone_is_shown_for_reference_and_never_as_the_claim(tmp_path):
    text = page(tmp_path, evaluations={"val": make("val")})
    assert text.split("\n\n")[1].startswith("**Verdict: not run yet.**")
    assert "Counted on validation, for reference (not the claim)" in section(text, 1)
    assert "Shown instead: validation, for reference only" in section(text, 2)
    assert (tmp_path / "results" / "neural" / "plots" / "reliability-validation-30.svg").read_text().startswith("<svg")


def test_the_page_is_the_same_for_the_same_inputs_and_carries_no_date(tmp_path):
    i = inputs(tmp_path, evaluations={"test": make()})
    first = R.render(R.load(i), tmp_path / "plots", tmp_path)
    assert first == R.render(R.load(i), tmp_path / "plots", tmp_path)
    assert not re.search(r"20\d\d-\d\d-\d\d", first)


def test_the_ledger_totals_come_from_its_mirror_or_a_given_json(tmp_path):
    cfg = L.load_jobs()
    path = tmp_path / "ledger.sqlite"
    with L.Ledger(path, jobs=cfg, create=True) as lg:
        entry = lg.reserve("O1", "item-1", "0.50")
        lg.settle(entry, "0.25", None, "req-1", "flex")
    got = R.ledger_totals(path)
    assert got["jobs"]["O1"]["spent_usd"] == pytest.approx(0.25) and got["jobs"]["O1"]["held_usd"] == 0
    assert "replayed read-only" in got["source"]
    (tmp_path / "given.json").write_text(json.dumps({"jobs": {"O1": {"account": "openai", "spent_usd": 1.0}}}))
    assert R.ledger_totals(path, tmp_path / "given.json")["jobs"]["O1"]["spent_usd"] == 1.0
    assert R.ledger_totals(tmp_path / "none.sqlite") is None


# --- The real shape ----------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real(tmp_path_factory):
    """The evaluation driver's own output on a tiny store, with a stand-in for TracerNet (the share of the window
    elapsed) scored like it."""
    d = tmp_path_factory.mktemp("tiny")
    out = devlib.build(d, indices=(0, 7, 8), checked=False, workers=2,
                       replicates={"train": [0, 1], "val": [0, 1], "test": [0, 1], "test_ood": [0, 1]})
    stand_in = ne.RiskMethod("tracernet", lambda specs, ctx: {s.specimen_id: np.minimum(1.0, s.ticks / s.window)
                                                              for s in specs})
    r = ne.evaluate_split("val", ne.baselines() + [stand_in], store_dir=out["store_dir"], workers=1, resamples=50,
                          write=False, subsets={ne.OOD_FEATURE: ["dev-07-weekend-050"]})
    return json.loads(json.dumps({k: v for k, v in r.items() if k != "detail"}))


def test_the_evaluation_drivers_own_output_renders(tmp_path, real):
    as_test = real | {"set": "test"}
    text = page(tmp_path, evaluations={"test": as_test, "val": real})
    s1 = section(text, 1)
    assert s1.count("\n| 3-2-1 |") + s1.count("\n| equal |") >= 12      # R1 and R2: two weightings x three baselines
    assert "At 30 per 100 specimens on `test`: R1" in s1
    assert "### 30 alerts per 100 specimens" in section(text, 2)
    assert "| tracernet |" in section(text, 3) and "### stratum" in section(text, 4)
    assert "Not run yet" not in text.split("\n\n")[1]


def test_the_drivers_subset_renders_as_test_ood_on_its_feature_scenarios(tmp_path, real):
    """A test_ood evaluation's ood_feature subset is read with the look's settings and never carries the rule."""
    s5 = section(page(tmp_path, evaluations={"test": real | {"set": "test"}, "test_ood": real | {"set": "test_ood"}}), 5)
    whole, feature = s5.split("### test_ood, the scenarios that carry")
    feature = feature.split("### legacy_v1")[0]
    c = real["subsets"][ne.OOD_FEATURE]["counts"]
    assert f"{c['clusters']:,} scenarios, {c['scored_specimens']:,} scored specimens" in feature
    assert "| 6/shift | tracernet | 3-2-1 |" in feature and "Not run yet" not in feature
    assert "The rule applied here" in whole and "The rule applied here" not in feature


# --- Section 8: the decision layer ---------------------------------------------------------------------------------

def _ci(point, half=0.02):
    return {"point": point, "low": point - half, "high": point + half}


def _summary(job, set_name, prompt="p1", spent=0.25, cap=4.5, calls=100, cost=0.01, cached=0):
    return {"job": job, "set": set_name, "status": "run", "stopped": None, "prompt_version": prompt, "items": calls + cached,
            "counts": {"answered": calls, "cached": cached, "fallback": 0}, "resolved_model": "nace-ai/drex-max-20261016",
            "ledger": {"spent_usd": spent, "held_usd": 0.0, "cap_usd": cap},
            "billing": {"calls_with_usage": calls, "cost_usd": cost, "max_billed_over_bound": 0.31 if calls else None,
                        "max_cost_over_reservation": 0.16 if calls else None},
            "notice": "Synthetic data, with no clinical review. Decision-model answers are zero-shot."}


def _x2_block(model_role, r1_role=0.46, r2_role=0.68):
    methods = {m: {"place_accuracy": _ci(p), "action_appropriate": _ci(p), "role_accuracy": _ci(r),
                   "breaches_averted_per_100_alerts": _ci(9.2, 1.0),
                   "recollections_started_per_100_alerts": _ci(0.0, 0.0) | {"low": 0.0}}
               for m, p, r in (("model", 0.31, model_role), ("R1", 0.20, r1_role), ("R2", 0.42, r2_role))}
    yes = {f"{w}_{k}": _ci(0.6) for w in ("model", "R2") for k in ("auroc", "auprc", "precision", "recall")}
    return {"alerts": 40, "scenarios": 4, "methods": methods,
            "differences": {f"model - {m}": {"role_accuracy": _ci(model_role - r, 0.02) | {"share_ahead": 0.0}}
                            for m, r in (("R1", r1_role), ("R2", r2_role))},
            "shared_problem": yes | {"positives": 18, "alerts": 40, "threshold": 0.7, "label": "shared"},
            "escalate": yes | {"positives": 5, "alerts": 40, "threshold": 0.7, "label": "unrecoverable"}}


def _x2_scores(set_name, model_role, *, holds=False, choices=True):
    x = _x2_block(model_role) | {"set": set_name, "scored_alerts": 40, "alerts_without_a_place": 0,
                                 "counterfactual": {"found_after_alert_min": 10.0, "received_after_found_min": 5.0,
                                                    "source": "assumed"},
                                 "slices": {"by_place": {"in_tube_system": _x2_block(model_role) | {"alerts": 30},
                                                         "lost": _x2_block(model_role) | {"alerts": 10}},
                                            "by_route_kind": {"tube": _x2_block(model_role)}},
                                 "adoption": {"holds": holds, "binding": set_name == "test",
                                              "checks": {"R1": {"holds": holds}, "R2": {"holds": holds}},
                                              "dispatch_source": ("decision model" if holds else "rules R2")
                                              if set_name == "test" else None}}
    if choices:
        x["choices"] = {"model": {"action": {"contact_courier_coordinator": 0.42, "check_receiving_queue": 0.30},
                                  "place": {"with_courier": 0.5}}}
    return x


X1_SCORES = {"labels": 120, "labels_answered": 120, "controls": 20, "control_auroc": _ci(0.98, 0.01),
             "soft_filter": {"works": True, "threshold": 0.8, "review_list": ["S0001", "S0002", "S0003"],
                             "note": "flagged scenarios go to a review list; none is deleted"},
             "control_scores_by_corruption": {"clean": {"n": 10, "mean_score": 2.74},
                                              "rates_10x": {"n": 10, "mean_score": 0.40}},
             "plausibility_levels": {"1": 3, "3": 117},
             "failure_focus_agreement": {"n": 120, "share": 0.571},
             "edge_tag_agreement": {"asked": 40, "share_yes": 0.983, "by_tag": {}}}
X3_SCORES = {"set": "test", "ticks": 400, "scored_ticks": 396, "refused_or_unanswered": 4, "scenarios": 30, "misses": 150,
             "methods": {m: {k: _ci(v) for k, v in zip(("auroc", "auprc", "brier", "ece"), vals)}
                         for m, vals in (("decision_model", (0.62, 0.50, 0.24, 0.12)),
                                         ("tracernet", (0.91, 0.85, 0.09, 0.03)),
                                         ("analytic_local", (0.88, 0.80, 0.11, 0.04)))},
             "differences": {"decision_model - tracernet": {"auroc": {"point": -0.29, "low": -0.33, "high": -0.25}}}}


def decision_page(tmp_path, files: dict) -> str:
    """The report with results/neural/decisions/ holding exactly `files` ({file name: document})."""
    i = inputs(tmp_path)
    folder = Path(i["decisions"])
    folder.mkdir(parents=True, exist_ok=True)
    for name, doc in files.items():
        (folder / name).write_text(json.dumps(doc))
    out = tmp_path / "results" / "neural" / "REPORT.md"
    R.report_command(out_path=out, inputs=i, out=lambda *_: None)
    return section(out.read_text(), 8)


P1_OUTPUTS = "09d953acd28a3a225c8d795894cbbdfdafbbb47555768c510494a2ec517d33ab"
P1_CHOICES = {"job": "X2", "set": "val", "prompt_version": "p1", "alerts": 5000, "outputs_sha256": P1_OUTPUTS,
              "model": {"place": {"in_lab_awaiting_scan": {"alerts": 1477, "share": 0.2954}},
                        "action": {"wait_and_recheck": {"alerts": 579, "share": 0.1158},
                                   "check_receiving_queue": {"alerts": 3359, "share": 0.6718}}}}


def every_decision_file(p1_role=0.335, p2_role=0.52, test_holds=False) -> dict:
    pilot = _summary("X1", "library", spent=0.024707, cap=1.0, calls=320, cost=0.024707)
    paid = _summary("X1", "library", spent=0.696058, cap=1.0, calls=8122, cost=0.671351, cached=320)
    pilot["billing"] |= {"max_billed_over_bound": 0.331, "max_cost_over_reservation": 0.166}
    paid["billing"] |= {"max_billed_over_bound": 0.353, "max_cost_over_reservation": 0.177}
    return {"X1-library-pilot.json": pilot, "X1-library-paid.json": paid,
            "X1-library.json": _summary("X1", "library", spent=0.696058, cap=1.0, calls=0, cost=0.0, cached=8442),
            "X1-library-scores.json": X1_SCORES,
            "X2-val-p1.json": _summary("X2", "val", "p1", spent=0.331374, calls=5000, cost=0.331374),
            "X2-val-p1-scores.json": _x2_scores("val", p1_role, choices=False) | {"provenance": {"outputs_sha256": P1_OUTPUTS}},
            "X2-val-p1-choices.json": P1_CHOICES,
            "X2-val.json": _summary("X2", "val", "p2", spent=0.66, calls=5000, cost=0.33),
            "X2-val-scores.json": _x2_scores("val", p2_role),
            "X2-test.json": _summary("X2", "test", "p2" if p2_role > p1_role else "p1", spent=1.9, calls=20000, cost=1.24),
            "X2-test-scores.json": _x2_scores("test", 0.74 if test_holds else 0.40, holds=test_holds),
            "X3-test.json": _summary("X3", "test", spent=0.06, cap=0.75, calls=4000, cost=0.06),
            "X3-test-scores.json": X3_SCORES}


def test_the_decision_section_with_every_run_present(tmp_path):
    s = decision_page(tmp_path, every_decision_file())
    order = ["### What it is", "### Spend", "### X1", "### X2: prompt rounds on validation", "### X2 on test (binding)",
             "### X3", "### X4", "### Notice"]
    assert [s.index(h) for h in order] == sorted(s.index(h) for h in order)
    assert "beats both R1 and R2 by at least 5 points, with the 95% interval of each difference above zero on test" in s
    assert "| X3 on test | p1 | 4,000 (0 from the cache) | 4,000 | $0.0600 | $0.0600 of $0.75 | 0.31 | 0.16 |" in s
    assert "| X1 pilot | p1 | 320 (0 from the cache) | 320 | $0.0247 | $0.0247 of $1.00 | 0.33 | 0.17 |" in s
    assert "| X1 paid run | p1 | 8,442 (320 from the cache) | 8,122 | $0.6714 | $0.6961 of $1.00 | 0.35 | 0.18 |" in s
    assert "| X1 refresh, from the cache | p1 | 8,442 (8,442 from the cache) | 0 | $0.0000 |" in s
    assert ("X1 cost $0.0247 for its 320-call pilot, then $0.6714 for the 8,122 calls of its paid run: $0.6961 in all, "
            "the ledger's total for the job. Its worst call, across both paid runs, billed 0.35 of its input bound and "
            "0.18 of its reservation.") in s
    assert "commit" not in s
    assert "`nace-ai/drex-max-20261016`" in s and "The decide account so far: $2.6561 of its $8.00 cap" in s
    assert "AUROC 0.980 (0.970 to 0.990) against the bar of 0.80, so plausibility works as a soft filter" in s
    assert "3 scenarios, whose most likely level is one of the two lowest, go to a review list; none is deleted" in s
    assert "| rates 10x | 10 | 0.40 |" in s and "57.1% of 120 scenarios" in s
    assert "98.3% of the 40 asked" in s and "a lean toward yes rather than agreement" in s
    assert "| Round one (p1) | 40 | 33.5 (31.5 to 35.5); 46.0; 68.0 |" in s
    assert "`check_receiving_queue`, 67% (shares counted from this round's outputs)" in s     # p1 predates choices
    assert "`contact_courier_coordinator`, 42% |" in s                                      # p2 records them
    assert "no number on it is binding" in s
    assert "prompt p2 (the selected round)." in s
    assert "| Drex Max minus R2 | -28.0 (-30.0 to -26.0) | at least +5.0, interval above zero | failed |" in s
    assert "The adoption rule did not hold (binding): any dispatch the exports carry is rule R2's dispatch." in s
    assert "carries no dispatch (null)" in s
    assert "| R2 (hand-written rules) | 42.0 (40.0 to 44.0) | 42.0 (40.0 to 44.0) | 68.0 (66.0 to 70.0) |" in s
    assert "| Shared problem: Drex Max | 0.600 (0.580 to 0.620) |" in s and "| Escalate: R2 |" in s
    assert "| `in_tube_system` | 30 | 40.0 | 46.0 | 68.0 | 31.0 | 9.2 |" in s and "| `tube` | 40 |" in s
    assert "(`source: assumed`): acting on an appropriate action finds the specimen 10 minutes after the alert" in s
    assert "| TracerNet | 0.910 (0.890 to 0.930) |" in s and "| Analytic model, local refit |" in s
    assert "No: on the same ticks Drex Max ranks the misses worse than TracerNet" in s
    assert "X4 (the distilled dispatcher) is not built" in s and "The rule did not hold on test, so X4 is not needed." in s
    assert "zero-shot" in s.split("### Notice")[1] and "Not run" not in s


def test_a_held_adoption_rule_puts_drex_max_in_the_exports(tmp_path):
    s = decision_page(tmp_path, every_decision_file(test_holds=True))
    assert "The adoption rule held (binding): any dispatch the exports carry is Drex Max's dispatch." in s


def test_the_decision_section_with_x2_and_x3_test_absent(tmp_path):
    files = {k: v for k, v in every_decision_file().items() if not k.endswith("-test.json")
             and not k.endswith("-test-scores.json")}
    s = decision_page(tmp_path, files)
    assert "Not run yet: X2 on test: it runs after the TracerNet test looks" in s
    assert "Not run yet: X3 on test: it runs after the TracerNet test looks" in s
    assert "X2 has not been scored on test yet." in s
    assert "### Spend" in s and "| X2 on test |" not in s and "| X3 on test |" not in s
    assert "Selection: test uses" in s                                     # both rounds scored: still named


def test_a_run_without_scores_and_a_run_not_run_say_why(tmp_path):
    files = every_decision_file()
    del files["X2-test-scores.json"]
    files["X3-test.json"] = {"job": "X3", "set": "test", "status": "not run", "reason": "no OPENROUTER_API_KEY in .env"}
    del files["X3-test-scores.json"]
    s = decision_page(tmp_path, files)
    assert "Not run yet: X2 on test has run (its summary exists) but is not scored yet" in s
    assert "Not run yet: X3 on test: no OPENROUTER_API_KEY in .env." in s


@pytest.mark.parametrize("p1_role,p2_role,named", [(0.335, 0.52, "round two (p2, 52.0% against 33.5%)"),
                                                   (0.45, 0.40, "round one (p1, 45.0% against 40.0%)")])
def test_the_selection_line_names_the_round_with_the_higher_validation_role_accuracy(tmp_path, p1_role, p2_role, named):
    s = decision_page(tmp_path, every_decision_file(p1_role, p2_role))
    assert f"Selection: test uses the round with the higher validation role accuracy, {named}." in s


def test_with_one_round_scored_the_selection_waits_for_the_second(tmp_path):
    files = {k: v for k, v in every_decision_file().items() if not k.startswith("X2-val.") and k != "X2-val-scores.json"}
    s = decision_page(tmp_path, files)
    assert "Not run yet: X2 round two (p2): no X2-val-scores.json yet." in s
    assert "it is named once both rounds are scored" in s and "Selection:" not in s


def test_x1s_spend_and_round_ones_choices_drop_with_their_files(tmp_path):
    files = every_decision_file()
    for name in ("X1-library-pilot.json", "X1-library-paid.json", "X2-val-p1-choices.json"):
        del files[name]
    s = decision_page(tmp_path, files)
    assert "| X1 pilot |" not in s and "| X1 paid run |" not in s and "for its 320-call pilot" not in s
    assert "X1's job total in the ledger: $0.6961." in s and "answered 8,442 of its 8,442 items from the cache" in s
    assert "| Round one (p1) |" in s and "not recorded in this scores file" in s


def test_counted_choices_of_other_outputs_are_not_shown(tmp_path):
    files = every_decision_file()
    files["X2-val-p1-choices.json"] = P1_CHOICES | {"outputs_sha256": "0" * 64}
    s = decision_page(tmp_path, files)
    assert "not shown: the counted choices are not of the outputs these scores read" in s
    assert "`check_receiving_queue`, 67%" not in s


def test_the_acceptance_appendix_comes_last_one_heading_down_or_says_not_run():
    from src.neural import report
    text = "# Appendix: acceptance criteria (spec §17)\n\nIntro.\n\n## 1. Keys\n\n**Verdict: held.**\n"
    out = report.section_acceptance({"acceptance": text})
    assert out[0] == "## Appendix: acceptance criteria (spec §17)" and "### 1. Keys" in out and "**Verdict: held.**" in out
    missing = report.section_acceptance({"acceptance": None})
    assert missing[0].startswith("## Appendix") and "N11" in missing[-1]


def test_the_caveats_under_the_verdict_match_their_committed_sources():
    """results/neural/caveats.md restates numbers by hand; each must agree with the committed file it comes from."""
    neural = R.ROOT / "results" / "neural"
    text = (neural / "caveats.md").read_text(encoding="utf-8")
    cal = json.loads((neural / "legacy_v1" / "evaluation.json").read_text())["calibration"]
    tn, analytic = cal["tracernet"]["60"]["brier"], min(cal[m]["60"]["brier"] for m in ("analytic_global", "analytic_local"))
    assert (round(tn, 4), round(analytic, 4)) == (0.2155, 0.1817)
    assert f"at 60 minutes of silence is {tn:.3f} against the analytic model's {analytic:.3f}" in text
    cells = json.loads((R.ROOT / "datasets" / "scenarios-v2.manifest.json").read_text())["coverage"]["cells"]
    under = sum(c["count"] < c["target"] for c in cells.values())
    assert (under, len(cells)) == (20, 32)
    assert f"misses its coverage targets in {under} of {len(cells)} stratum-by-archetype cells" in text
    deletion = json.loads((neural / "explanations.json").read_text())["checks"]["deletion"]
    assert (round(deletion["share_top_beats_random"], 4), deletion["required_share"]) == (0.6821, 0.70)
    assert (f"in {deletion['share_top_beats_random']:.1%} of test alerts, against the required "
            f"{deletion['required_share']:.0%}") in text
    lead = json.loads((neural / "control_v1world" / "evaluation.json").read_text())["differences"]["6/shift"]["3-2-1"]
    m = lead["analytic_local"]["median_warning_all_breaches_min"]
    assert f"by {m['point']:g} minutes (95% interval {m['low']:g} to {m['high']:g})" in text


def test_the_follow_up_numbers_match_their_committed_sources():
    """results/neural/caveats.md and limits.md restate the November 13 follow-up; each number must match its file."""
    neural = R.ROOT / "results" / "neural"
    text = (neural / "caveats.md").read_text(encoding="utf-8") + (neural / "limits.md").read_text(encoding="utf-8")
    audit = json.loads((neural / "control_audit" / "summary.json").read_text())
    assert audit["finding"]["leakage"] == "not supported"
    assert not audit["placebo"]["flagged"] and audit["placebo_v2"]["flagged"]
    assert audit["placebo_v2"]["median_warning_all_breaches_min"]["point"] == 10.0
    assert audit["invariance"]["passed"] and audit["invariance"]["ticks_compared"] == 35499
    assert "0 of 35,499 scores changed" in text
    cal = json.loads((neural / "sitecal" / "legacy_v1" / "evaluation.json").read_text())
    assert round(cal["calibration"]["tracernet"]["60"]["brier"], 3) == 0.181
    assert cal["decision_rule"]["verdict"]["held"]["R3"]
    assert "brings it to 0.181" in text
