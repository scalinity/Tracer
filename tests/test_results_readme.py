"""The headline paragraph quotes the midterm snapshot (plan task M7)."""

import json

from src.pipeline import store

README = (store.ROOT / "results" / "README.md").read_text(encoding="utf-8")
SNAP = json.loads((store.ROOT / "results" / "mvp-nov8.json").read_text(encoding="utf-8"))
H = SNAP["headline_by_alerts_per_shift"]


def n(v) -> str:
    return f"{v:g}"


def span(pair) -> str:
    return f"{pair[0]:+g} to {pair[1]:+g}".replace("+", "") if pair[0] >= 0 else f"{pair[0]:g} to {pair[1]:g}"


def test_the_headline_numbers_are_the_snapshots():
    six = H["6"]
    med = six["median_warning_all_breaches_min"]
    assert f"{n(med['model_3_2_1'])} minutes at 6 alerts per shift against {n(med['plan_baseline'])} minutes" in README
    assert f"a lead of {span(six['model_minus_plan_baseline_95pct_bootstrap_min']['model_3_2_1'])} minutes" in README
    assert f"it was {n(med['model_equal_weights'])} minutes against {n(med['plan_baseline'])}" in README
    assert f"a lead of {span(six['model_minus_plan_baseline_95pct_bootstrap_min']['model_equal_weights'])} minutes" in README
    assert f"reaches {n(med['window_baseline'])} minutes at 6 alerts per shift" in README
    assert f"is {span(six['model_minus_window_baseline_95pct_bootstrap_min']['model_3_2_1'])} minutes at weights 3, 2, 1" in README


def test_the_window_aware_result_sits_beside_the_headline_not_only_in_the_caveats():
    text = README.split("**Headline, midterm snapshot.**")[1].split("\n")[0]
    assert text.index("window-aware rule") < text.index("Read every number with these limits")
    assert text.index("clears the bar") < text.index("window-aware rule")
    four, two = H["4"]["median_warning_all_breaches_min"], H["2"]["median_warning_all_breaches_min"]
    assert four["model_equal_weights"] == four["window_baseline"] == 40
    assert f"the model trails by {n(two['window_baseline'] - two['model_equal_weights'])} minutes at 2" in text


def test_the_bar_is_met_where_the_paragraph_says():
    met = SNAP["pre_registered_bar"]["met_by_the_lower_end"]
    assert all(all(v.values()) for k, v in met.items() if k in ("2", "4", "6"))
    assert "at 2, 4 and 6 alerts per shift and at both weightings" in README


def test_the_caveats_travel_with_the_numbers():
    text = README.split("**Headline, midterm snapshot.**")[1].split("\n")[0]
    ev, split = SNAP["evaluation"], SNAP["split"]
    c30, c60 = ev["calibration"]["30"], ev["calibration"]["60"]
    for needle in (f"{split['specimens_with_a_clock']:,} specimens with a clock", f"{split['breaches']} breaches",
                   f"{ev['breaches_per_shift']:.2f} breaches per 8-hour shift", "share assumptions", "best-case",
                   f"{c30['brier']:.3f} against {c30['brier_of_the_base_rate']:.3f}", f"{c60['brier']:.3f} against {c60['brier_of_the_base_rate']:.3f}",
                   "not in sorting", f"{SNAP['dataset']['dropped']} specimens the validator dropped", "a third of all specimens",
                   f"{H['6']['alerts_per_shift']['model_3_2_1']:.1f}"):
        assert needle in text, needle
    assert "synthetic data that had no clinical review" in README.split("\n")[2]


def test_the_table_matches_the_snapshot():
    for budget in ("2", "4", "6"):
        row = next(line for line in README.splitlines() if line.startswith(f"| {budget} |"))
        med, caught = H[budget]["median_warning_all_breaches_min"], H[budget]["breaches_caught_of_100"]
        for arm in ("plan_baseline", "window_baseline", "model_equal_weights", "model_3_2_1"):
            assert f"{n(med[arm])} ({caught[arm]})" in row, (budget, arm)
