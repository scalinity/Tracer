import json
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from src.models import location
from src.pipeline import evaluate, location_eval
from src.pipeline.evaluate import Spec


def spec(receipt, sid="a", group="g", route="tube"):
    return Spec(sid, 24, group, (group,), 120, receipt, receipt is None or receipt > 120, "none", 3, route, "2026-08-17T00:00:00", "urine")


def flat(**mass):
    """A place series that holds the given mass at every minute and nothing elsewhere."""
    return {p: np.full(location_eval.LONGEST + 1, mass.get(p, 0.0)) for p in location.PLACES}


# ---- the pieces ----

def test_a_place_is_found_by_the_segment_that_covers_the_minute():
    segments = [(0.0, 7.0, "at_unit"), (7.0, 14.0, "in_tube"), (14.0, None, "lost")]
    assert [location_eval.place_at(segments, m) for m in (0, 6.9, 7, 13, 14, 500)] == [
        "at_unit", "at_unit", "in_tube", "in_tube", "lost", "lost"]


def test_a_specimen_already_at_the_bench_has_no_place_to_find():
    assert location_eval.place_at([(0.0, 10.0, "at_unit"), (10.0, None, "at_receiving_bench")], 12) is None


def test_a_specimen_is_silent_until_its_receipt_scan():
    assert location_eval.silent(spec(None), 90) and location_eval.silent(spec(40.0), 30)
    assert not location_eval.silent(spec(30.0), 30) and not location_eval.silent(spec(10.0), 30)


def test_places_are_ranked_by_probability_with_ties_to_the_earlier_place():
    places = flat(in_tube=0.5, at_station=0.3, lost=0.2)
    assert location_eval.ranked(places, 10)[:3] == ["in_tube", "at_station", "lost"]
    assert location_eval.ranked(flat(at_unit=0.5, in_tube=0.5), 10)[:2] == ["at_unit", "in_tube"]


def test_accuracy_counts_top_one_top_three_and_the_commonest_place():
    specs = [spec(None, sid=f"s{i}") for i in range(4)] + [spec(5.0, sid="early")]
    series = {("g", "tube"): flat(in_tube=0.4, at_station=0.3, lost=0.2, at_unit=0.1)}
    truth = {"s0": [(0.0, None, "in_tube")], "s1": [(0.0, None, "in_tube")], "s2": [(0.0, None, "at_station")],
             "s3": [(0.0, None, "at_unit")], "early": [(0.0, 5.0, "at_unit"), (5.0, None, "at_receiving_bench")]}
    a = location_eval.accuracy(specs, truth, series, 30)
    assert a["silent_specimens"] == 4                       # "early" was received before minute 30
    assert a["top1"] == 0.5 and a["top3"] == 0.75           # in_tube twice; at_station is second, at_unit fourth
    assert a["most_common_place"] == "in_tube" and a["most_common_share"] == 0.5
    assert a["confusion_true_by_predicted"]["at_unit"]["in_tube"] == 1


def test_the_calibration_of_lost_compares_the_predicted_chance_with_what_happened():
    specs = [spec(None, sid=f"s{i}") for i in range(10)]
    series = {("g", "tube"): flat(lost=0.3, in_tube=0.7)}
    truth = {f"s{i}": [(0.0, None, "lost" if i < 3 else "in_tube")] for i in range(10)}
    c = location_eval.lost_calibration(specs, truth, series)
    (b,) = c["bins"]
    assert b["predicted"] == pytest.approx(0.3) and b["observed"] == pytest.approx(0.3)
    assert c["specimens_really_lost"] == 3 and b["n"] == c["pooled_ticks"] == 10 * len(location_eval.CALIBRATION_TICKS)


# ---- the real held-out runs ----

@pytest.fixture(scope="module")
def result():
    return location_eval.run()


def test_every_landmark_is_scored_and_the_silent_population_shrinks(result):
    assert list(result["landmarks"]) == ["15", "30", "60", "90"]
    n = [a["silent_specimens"] for a in result["landmarks"].values()]
    assert n == sorted(n, reverse=True) and n[-1] > 50
    for a in result["landmarks"].values():
        assert 0 < a["top1"] <= a["top3"] <= 1 and sum(a["true_places"].values()) == a["silent_specimens"]
        assert sum(sum(row.values()) for row in a["confusion_true_by_predicted"].values()) == a["silent_specimens"]


def test_the_filter_beats_always_naming_the_commonest_place_at_every_landmark(result):
    for m, a in result["landmarks"].items():
        assert a["top1"] > a["most_common_share"], m


def test_the_chance_of_lost_is_calibrated_where_there_is_data(result):
    c = result["lost_calibration"]
    assert sum(b["n"] for b in c["bins"]) == c["pooled_ticks"] and c["specimens_really_lost"] > 5
    assert c["brier"] < c["brier_of_the_base_rate"]
    for b in c["bins"]:
        if b["predicted"] < 0.1:
            assert abs(b["observed"] - b["predicted"]) < 0.02, b


def test_the_numbers_regenerate_exactly_and_match_the_committed_file(result):
    committed = json.loads((location_eval.RESULTS / "location-accuracy.json").read_text(encoding="utf-8"))
    assert json.dumps(committed, sort_keys=True) == json.dumps(result, sort_keys=True)


def test_a_stale_fit_is_refused(tmp_path, monkeypatch):
    from src.pipeline import intervals
    params = json.loads(intervals.PARAMS_PATH.read_text()) | {"policy_hash": "0" * 16}
    (tmp_path / "p.json").write_text(json.dumps(params))
    monkeypatch.setattr(intervals, "PARAMS_PATH", tmp_path / "p.json")
    with pytest.raises(RuntimeError, match="different policy"):
        location_eval.run()


def test_the_plot_is_a_valid_svg_with_both_panels(result):
    root = ET.fromstring(location_eval.svg(result))
    text = " ".join(t.text or "" for t in root.iter("{http://www.w3.org/2000/svg}text"))
    assert "Chance of lost" in text and "minutes of silence" in text and "15 min" in text and "90 min" in text
    assert len(list(root.iter("{http://www.w3.org/2000/svg}circle"))) == len(result["lost_calibration"]["bins"])


def test_the_table_prints_every_landmark_and_the_calibration(result):
    text = location_eval.table(result)
    for needle in ("top 1", "top 3", "most common true place", "probability of lost", "synthetic data with no clinical review"):
        assert needle in text


def test_main_writes_the_numbers_and_the_plot(tmp_path, monkeypatch, capsys, result):
    monkeypatch.setattr(location_eval, "run", lambda: result)
    monkeypatch.setattr(location_eval, "RESULTS", tmp_path)
    assert location_eval.main() == 0
    assert json.loads((tmp_path / "location-accuracy.json").read_text())["landmarks"]["15"]["top3"] > 0.9
    assert (tmp_path / "location-calibration.svg").read_text().startswith("<svg")
    assert "wrote" in capsys.readouterr().out
