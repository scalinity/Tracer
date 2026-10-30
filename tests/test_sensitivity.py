import json

import numpy as np
import pytest

from src.pipeline import evaluate, intervals, sensitivity as sens, store


@pytest.fixture(scope="module")
def result():
    return sens.run()


@pytest.fixture(scope="module")
def specs():
    loaded = sens.config.load()
    start, stop = intervals.clock_events(loaded["policy"])
    return evaluate.load_test_specs(start, stop)[0]


def row(result, name):
    return next(r for r in result["scenarios"] if r["name"] == name)


# ---- the weighting ----

def test_a_weighted_median_is_numpys_when_the_weights_are_equal():
    for values in ([5.0, 1.0, 3.0], [4.0, 1.0, 9.0, 2.0], [7.0]):
        assert sens.weighted_median(np.array(values), np.ones(len(values))) == float(np.median(values))


def test_weights_move_the_median_and_an_empty_set_has_none():
    assert sens.weighted_median(np.array([1.0, 2.0, 10.0]), np.array([1.0, 1.0, 5.0])) == 10.0
    assert sens.weighted_median(np.array([1.0, 2.0, 10.0]), np.array([5.0, 1.0, 1.0])) == 1.0
    assert sens.weighted_median(np.array([]), np.array([])) is None


def test_hour_weights_keep_the_count_and_give_each_hour_its_multiplier(specs):
    flat = next(s for s in sens.scenarios(specs) if s["name"] == "flat day")["weights"](specs)
    hours = np.array([sens.hour_of(s) for s in specs])
    assert flat.sum() == pytest.approx(len(specs))
    per_hour = np.array([flat[hours == h].sum() for h in range(24)])
    assert np.allclose(per_hour, len(specs) / 24)  # every hour holds an equal share
    peak = next(s for s in sens.scenarios(specs) if s["name"] == "sharper morning peak")["weights"](specs)
    share = lambda w, hs: w[np.isin(hours, hs)].sum() / w.sum()
    assert share(peak, [7, 8, 9]) / share(np.ones(len(specs)), [7, 8, 9]) == pytest.approx(1.6 / (1 + 0.6 * share(np.ones(len(specs)), [7, 8, 9])))


def test_kind_weights_keep_the_count_and_raise_the_chosen_kinds_share(specs):
    more = next(s for s in sens.scenarios(specs) if s["name"] == "more urine")["weights"](specs)
    urine = np.array([s.kind.startswith("urine") for s in specs])
    assert more.sum() == pytest.approx(len(specs))
    before = urine.mean()
    assert more[urine].sum() / more.sum() == pytest.approx(1.5 * before / (1.5 * before + (1 - before)))  # 1.5 times as common as the rest
    urgent = next(s for s in sens.scenarios(specs) if s["name"] == "more urgent specimens")["weights"](specs)
    assert urgent.sum() == pytest.approx(len(specs))


# ---- the scenarios ----

def test_the_first_scenario_is_the_evaluation_at_six_alerts_per_shift(result):
    ev = json.loads(evaluate.RESULTS_PATH.read_text(encoding="utf-8"))["operating_points"]["6"]
    base = row(result, "as generated")
    for ours, theirs in (("plan_baseline", ev["baseline"]), ("window_baseline", ev["window_baseline"]), ("model", ev["model"])):
        for label in ("fixed_knobs", "retuned_to_6_per_shift"):
            x = base[label][ours]
            assert x["alerts_per_shift"] == pytest.approx(theirs["alerts_per_shift"], abs=1e-3), (ours, label)
            assert x["median_warning_all_breaches_min"] == theirs["median_warning_all_breaches_min"]
            assert x["median_warning_min"] == theirs["median_warning_min"]
            assert x["catch_rate"] == pytest.approx(theirs["catch_rate"], abs=1e-4)
    assert base["breaches_per_shift"] if "breaches_per_shift" in base else True
    assert base["fixed_knobs"]["model"]["breaches_per_shift"] == pytest.approx(ev["model"]["breaches"] / 126, abs=1e-3)


def test_with_nothing_retuned_alerts_per_shift_scale_with_volume(result):
    base = row(result, "as generated")["fixed_knobs"]
    for name, f in (("volume x0.5", 0.5), ("volume x0.75", 0.75), ("volume x1.5", 1.5), ("volume x2", 2.0)):
        for m in sens.METHODS:
            assert row(result, name)["fixed_knobs"][m]["alerts_per_shift"] == pytest.approx(f * base[m]["alerts_per_shift"], rel=2e-3)
            assert row(result, name)["fixed_knobs"][m]["median_warning_all_breaches_min"] == base[m]["median_warning_all_breaches_min"]


def test_retuned_to_the_budget_every_method_lands_near_six_alerts_a_shift(result):
    for r in result["scenarios"]:
        for m in sens.METHODS:
            assert 5.0 <= r["retuned_to_6_per_shift"][m]["alerts_per_shift"] <= 7.5, (r["name"], m)


def test_more_volume_at_the_same_budget_buys_less_warning(result):
    names = ["volume x0.5", "volume x0.75", "as generated", "volume x1.5", "volume x2"]
    for m in sens.METHODS:
        warnings = [row(result, n)["retuned_to_6_per_shift"][m]["median_warning_all_breaches_min"] for n in names]
        assert warnings == sorted(warnings, reverse=True), (m, warnings)
        assert warnings[0] > warnings[-1]


def test_the_arrival_profile_does_not_move_what_the_methods_do(result):
    base = row(result, "as generated")["fixed_knobs"]
    for name in ("flat day", "sharper morning peak", "night-heavy"):
        for m in sens.METHODS:
            x = row(result, name)["fixed_knobs"][m]
            assert abs(x["median_warning_all_breaches_min"] - base[m]["median_warning_all_breaches_min"]) <= 5, (name, m)
            assert abs(x["alerts_per_shift"] - base[m]["alerts_per_shift"]) <= 0.4, (name, m)


def test_every_scenario_names_itself_and_every_knob_is_in_range(result):
    assert [r["group"] for r in result["scenarios"]][0] == "as generated" and len(result["scenarios"]) == 11
    for r in result["scenarios"]:
        assert r["description"] and r["specimens_per_shift"] > 0
        assert 0 <= r["retuned_knobs"]["plan_baseline"] <= 1 and 0 <= r["retuned_knobs"]["window_baseline"] <= 1 and r["retuned_knobs"]["model"] > 0


def test_the_committed_numbers_are_the_ones_the_code_regenerates(result):
    committed = json.loads(sens.RESULTS_PATH.read_text(encoding="utf-8"))
    assert json.dumps(committed, sort_keys=True) == json.dumps(result, sort_keys=True)


def test_a_stale_fit_is_refused(tmp_path, monkeypatch):
    params = json.loads(intervals.PARAMS_PATH.read_text()) | {"policy_hash": "0" * 16}
    (tmp_path / "p.json").write_text(json.dumps(params))
    monkeypatch.setattr(intervals, "PARAMS_PATH", tmp_path / "p.json")
    with pytest.raises(RuntimeError, match="different policy"):
        sens.run()


def test_main_writes_the_numbers_and_the_table(tmp_path, monkeypatch, capsys, result):
    monkeypatch.setattr(sens, "run", lambda: result)
    monkeypatch.setattr(sens, "RESULTS_PATH", tmp_path / "sensitivity.json")
    (tmp_path / "results").mkdir()
    monkeypatch.setattr(sens, "ROOT", tmp_path)
    assert sens.main() == 0
    out = capsys.readouterr().out
    assert "volume x2" in out and "no clinical review" in out and (tmp_path / "sensitivity.json").exists()


def test_the_module_does_not_open_the_truth_file():
    text = (sens.ROOT / "src" / "pipeline" / "sensitivity.py").read_text(encoding="utf-8")
    assert "open_truth" not in text and store.TRUTH_NAME not in text
