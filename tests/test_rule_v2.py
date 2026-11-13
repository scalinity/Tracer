"""The second decision rule (docs/neural/preregistration-v2.md), computed from an evaluation's own record. Checked on
the committed validation evaluation, which is development data, and on copies with one number changed."""
import copy
import json
from pathlib import Path

import pytest

from src.pipeline import rule_v2 as R

VAL = json.loads((Path(__file__).resolve().parents[1] / "results" / "neural" / "val" / "evaluation.json").read_text())


def test_the_first_artifact_passes_every_condition_on_validation():
    v = R.verdict(VAL)
    assert v["held"] == {"P": True, "G1": True, "G2": True, "G3": True, "G4": True} and v["passed"]
    assert len(v["P"]["checks"]) == 10 and v["P"]["best_baseline"] == {"3-2-1": "analytic_local",
                                                                        "equal": "analytic_local"}
    low = {(c["weighting"], c["baseline"]): c["interval_low_min"] for c in v["P"]["checks"]}
    assert low[("3-2-1", "analytic_local")] == pytest.approx(8.43, abs=0.01)
    assert low[("equal", "analytic_local")] == pytest.approx(3.69, abs=0.01)


def test_one_interval_touching_zero_fails_the_primary_endpoint():
    ev = copy.deepcopy(VAL)
    ev["differences"]["6/shift"]["equal"]["quantile_global"]["capped_mean_warning_min"]["low"] = 0.0
    v = R.verdict(ev)
    assert not v["P"]["held"] and not v["passed"] and v["primary_model"] == "analytic model"


def test_each_guardrail_can_fail_the_rule_alone():
    ev = copy.deepcopy(VAL)
    ev["calibration"]["tracernet"]["60"]["brier"] = min(ev["calibration"][a]["60"]["brier"]
                                                       for a in ("analytic_global", "analytic_local")) + 0.0101
    assert R.verdict(ev)["held"]["G1"] is False
    ev = copy.deepcopy(VAL)
    ev["discrimination"]["window_rule"]["landmark_30"]["auroc"] = 0.99
    assert R.verdict(ev)["held"]["G2"] is False
    ev = copy.deepcopy(VAL)
    op = ev["operating_points"]["6/shift"]["window_rule"]["none"]
    op["false_alerts"]["point"] = 0.1 * op["alerts"]["point"]
    assert R.verdict(ev)["held"]["G3"] is False
    ev = copy.deepcopy(VAL)
    ev["decision_rule"]["R2"]["checks"][0]["held"] = False
    assert R.verdict(ev)["held"]["G4"] is False


def test_false_alerts_are_compared_per_alert_sent_not_per_shift():
    """On validation the window rule sends fewer alerts, so it has fewer false alerts per shift but more per alert."""
    c = next(c for c in R.verdict(VAL)["G3"]["checks"] if c["weighting"] == "3-2-1" and c["baseline"] == "window_rule")
    assert c["tracernet_false_per_shift"] > c["baseline_false_per_shift"]
    assert c["tracernet_false_per_alert"] < c["baseline_false_per_alert"] and c["held"]


def test_a_reported_probability_replaces_the_calibrated_output_for_g1_and_g2():
    reported = {"calibration": {"30": {"brier": 0.5}, "60": {"brier": 0.5}},
                "discrimination": {"landmark_30": {"auroc": 0.5}}}
    v = R.verdict(VAL, reported)
    assert v["held"]["G1"] is False and v["held"]["G2"] is False and v["probability_scored"] == "reported (site map)"


def test_the_site_map_reads_the_same_evaluations_tracernet_site():
    ev = copy.deepcopy(VAL)
    with pytest.raises(ValueError, match="--site-map"):
        R.verdict(ev, site_map=True)
    ev["calibration"]["tracernet_site"] = {"30": {"brier": 0.5}, "60": {"brier": 0.5}}
    ev["discrimination"]["tracernet_site"] = {"landmark_30": {"auroc": 0.99}}
    v = R.verdict(ev, site_map=True)
    assert v["held"]["G1"] is False and v["held"]["G2"] is True and v["probability_scored"] == "reported (site map)"
    assert R.verdict(ev)["held"]["G1"] is True     # without the flag, TracerNet's own calibrated output


def test_the_site_mapped_method_can_sit_beside_tracernet():
    from src.neural import sitecal
    beside = sitecal.tracernet_sitecal("x/*.parquet", name=sitecal.SITE_METHOD)
    assert beside.name == "tracernet_site" == R.SITE_METHOD and beside.probabilistic
    assert sitecal.tracernet_sitecal("x/*.parquet").name == "tracernet"
