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
    assert first.startswith("**Verdict on `test` at 6 alerts per shift: R1 failed, R2 held, R3 held, R4 held; "
                            "primary risk model: analytic model.**")
    assert "TracerNet's median warning over all breaches was 2.0 min against window rule 0.0 (difference 2.0, " \
           "interval low 0.5)" in first
    assert "at 30 min TracerNet 0.0700 against 0.0750 (excess -0.0050)" in first
    assert "the largest deficit was 0.0%" in first and "interval high 1.0%" in first
    assert R.NOTICE in first


def test_a_held_rule_names_tracernet_as_the_primary_model(tmp_path):
    first = page(tmp_path, evaluations={"test": make(tn=12.0, low=2.0)}).split("\n\n")[1]
    assert "R1 held, R2 held, R3 held, R4 held; primary risk model: TracerNet." in first


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
