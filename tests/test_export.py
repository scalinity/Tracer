import copy
import json

import jsonschema
import numpy as np
import pytest

from src.api import export
from src.models import location
from src.pipeline import evaluate, store, triage

BUNDLE_PATH = export.DEFAULT_OUT
SCHEMA_PATH = export.ROOT / "docs" / "demo" / "bundle.schema.json"
TICK = evaluate.TICK_MIN


@pytest.fixture(scope="module")
def schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def bundle():
    return json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rebuilt():
    return export.build()


@pytest.fixture(scope="module")
def evaluation():
    return json.loads(evaluate.RESULTS_PATH.read_text(encoding="utf-8"))


# ---- the schema ----

def test_the_schema_is_a_valid_json_schema(schema):
    jsonschema.Draft202012Validator.check_schema(schema)


def test_the_committed_bundle_validates_against_the_schema(bundle, schema):
    jsonschema.validate(bundle, schema)


@pytest.mark.parametrize("damage", [
    lambda b: b.pop("grid"),
    lambda b: b.update(extra=1),
    lambda b: b["specimens"][0].pop("alerts"),
    lambda b: b["specimens"][0].update(tier=4),
    lambda b: b["specimens"][0]["risk"].append(1.5),
    lambda b: b["specimens"][0]["events"][0].pop("note"),
    lambda b: b["specimens"][0]["truth"].update(breach="yes"),
    lambda b: b["grid"].pop(),
    lambda b: b["grid"][3]["model"].pop("harm_threshold"),
    lambda b: b["policy"]["policies"][next(iter(b["policy"]["policies"]))].update(urgency_tier=9),
    lambda b: b["showcase_candidates"][0].update(kind="best_ever"),
    lambda b: b["heroes"].append({"id": "x", "story": "s"}),
    lambda b: b["source"].update(sha256="not a hash"),
])
def test_a_damaged_bundle_is_rejected(bundle, schema, damage):
    broken = copy.deepcopy(bundle)
    damage(broken)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(broken, schema)


# ---- it is what the code regenerates, and what the evaluation says ----

def test_the_committed_bundle_is_the_one_the_code_regenerates(bundle, rebuilt):
    """A stale bundle fails here: rerun `python -m src.api.export --run test --out docs/demo/bundle.json`."""
    assert json.dumps(json.loads(json.dumps(rebuilt)), sort_keys=True) == json.dumps(bundle, sort_keys=True)


def test_main_writes_the_bundle(tmp_path, monkeypatch, capsys, rebuilt):
    monkeypatch.setattr(export, "build", lambda: rebuilt)
    out = tmp_path / "nested" / "bundle.json"
    assert export.main(["--run", "test", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["bundle_version"] == 1
    assert "wrote" in capsys.readouterr().out


def test_the_population_is_the_evaluations(bundle, evaluation):
    assert len(bundle["specimens"]) == bundle["source"]["specimens"] == evaluation["with_a_clock"]
    assert bundle["source"]["breaches"] == evaluation["breaches"] == sum(s["truth"]["breach"] for s in bundle["specimens"])
    assert bundle["source"]["never_collected_set_aside"] == evaluation["without_a_clock"]
    assert bundle["source"]["runs"] == evaluation["held_out_runs"] and bundle["source"]["shifts"] == evaluation["shifts"]
    assert {s["run"] for s in bundle["specimens"]} == set(range(24, 30))


@pytest.mark.parametrize("budget", ["1", "2", "4", "6"])
def test_the_grid_agrees_with_the_evaluation_at_its_four_budgets(bundle, evaluation, budget):
    point = next(g for g in bundle["grid"] if g["budget_per_shift"] == float(budget))
    for ours, theirs in (("model", "model"), ("plan_baseline", "baseline"), ("window_baseline", "window_baseline")):
        a, b = point[ours], evaluation["operating_points"][budget][theirs]
        assert a["alerts_per_shift"] == pytest.approx(b["alerts_per_shift"], abs=1e-4), (budget, ours)
        for key in ("breaches", "caught", "median_warning_min", "median_warning_all_breaches_min", "false_alerts"):
            assert a[key] == b[key], (budget, ours, key)
        assert a["caught_with_good_warning"] == pytest.approx(b["caught_with_good_warning"], abs=1e-4)
    knob = {"model": "harm_threshold", "plan_baseline": "quantile", "window_baseline": "share"}
    op = evaluation["operating_points"][budget]
    assert point["model"]["harm_threshold"] == pytest.approx(op["model_threshold"], abs=1e-6)
    assert point["plan_baseline"][knob["plan_baseline"]] == pytest.approx(op["baseline_quantile"], abs=1e-6)
    assert point["window_baseline"][knob["window_baseline"]] == pytest.approx(op["window_share"], abs=1e-6)


def test_the_grid_is_twenty_budgets_and_alert_volume_rises_with_each(bundle):
    grid = bundle["grid"]
    assert len(grid) == 20 and [g["budget_per_shift"] for g in grid] == sorted(g["budget_per_shift"] for g in grid)
    assert grid[bundle["headline_index"]]["budget_per_shift"] == bundle["triage"]["headline_budget_per_shift"] == 6
    for method in ("model", "plan_baseline", "window_baseline"):
        volumes = [g[method]["alerts_per_shift"] for g in grid]
        assert volumes == sorted(volumes), method
        assert all(0.3 * g["budget_per_shift"] < g[method]["alerts_per_shift"] < 2.2 * g["budget_per_shift"] for g in grid), method
    assert grid[bundle["headline_index"]]["model"]["median_warning_all_breaches_min"] == 55.0


# ---- inside the specimens ----

def test_risk_is_a_probability_that_never_falls_and_places_sum_to_one(bundle):
    for s in bundle["specimens"]:
        assert all(0 <= r <= 1 for r in s["risk"]) and s["risk"] == sorted(s["risk"]), s["id"]
        assert len(s["places"]) == min(len(s["risk"]), bundle["horizon_min"] // TICK), s["id"]
        for row in s["places"]:
            assert abs(sum(row) - 1) < 1e-3, s["id"]
        if s["route"] == "hand_carry":  # a carried specimen is never in the tube, at the station or lost
            assert all(row[bundle["place_order"].index(p)] == 0 for row in s["places"] for p in ("in_tube", "at_station", "stalled_at_station", "misrouted", "lost"))


def test_the_risk_ticks_are_the_ones_the_evaluation_scores(bundle):
    for s in bundle["specimens"]:
        last = s["window_min"] if s["received_min"] is None else min(s["window_min"], int(np.ceil(s["received_min"])) - 1)
        ticks = list(range(TICK, last + 1, TICK))
        assert len(s["risk"]) >= min(len(ticks), bundle["horizon_min"] // TICK), s["id"]
        assert len(s["risk"]) <= len(ticks), s["id"]


def test_the_model_alert_is_the_first_tick_where_expected_harm_reaches_the_threshold(bundle):
    weights, point = bundle["triage"]["weights"], bundle["grid"][bundle["headline_index"]]
    theta = point["model"]["harm_threshold"]
    alerts = 0
    for s in bundle["specimens"]:
        harm = [weights[str(s["tier"])] * r for r in s["risk"]]
        first = next((TICK * (i + 1) for i, h in enumerate(harm) if h >= theta), None)
        assert s["alerts"]["model"] == first, s["id"]
        alerts += first is not None
    assert alerts / bundle["source"]["shifts"] == pytest.approx(point["model"]["alerts_per_shift"], abs=1e-4)


def test_the_plan_baseline_alert_is_the_first_tick_after_the_groups_threshold(bundle):
    i = bundle["headline_index"]
    for s in bundle["specimens"]:
        thr = bundle["baselines"]["plan"]["threshold_min_by_group"][s["group"]][i]
        last = s["window_min"] if s["received_min"] is None else min(s["window_min"], int(np.ceil(s["received_min"])) - 1)
        first = next((t for t in range(TICK, last + 1, TICK) if t > thr), None)
        assert s["alerts"]["plan_baseline"] == first, s["id"]


def test_the_window_baseline_alert_is_the_first_tick_at_the_share_of_the_window(bundle):
    share = bundle["baselines"]["window"]["shares"][bundle["headline_index"]]
    for s in bundle["specimens"]:
        last = s["window_min"] if s["received_min"] is None else min(s["window_min"], int(np.ceil(s["received_min"])) - 1)
        first = next((t for t in range(TICK, last + 1, TICK) if t >= share * s["window_min"]), None)
        assert s["alerts"]["window_baseline"] == first, s["id"]


def test_the_risk_in_the_bundle_is_the_models_from_the_parameters_it_carries(bundle):
    from src.models import risk
    pooled, w = bundle["model"]["pooled"], bundle["model"]["pooled"]["weights"]
    for s in bundle["specimens"][:300]:
        g = bundle["model"]["groups"][s["group"]]
        mix = risk.Mixture(((w["normal"], g["mu"], g["sigma"]), (w["stalled"], pooled["stalled_mu"], pooled["stalled_sigma"])), w["never"])
        for i in (0, len(s["risk"]) // 2, len(s["risk"]) - 1):
            if 0 <= i < len(s["risk"]):
                assert s["risk"][i] == pytest.approx(risk.risk_unreceived(TICK * (i + 1), mix, s["window_min"]), abs=1e-9), s["id"]


def test_events_and_truth_start_where_the_clock_does(bundle):
    for s in bundle["specimens"]:
        collected = next(e for e in s["events"] if e["activity"] == "collected")
        assert collected["at_min"] == 0
        assert [e["at_min"] for e in s["events"]] == sorted(e["at_min"] for e in s["events"])
        received = [e["at_min"] for e in s["events"] if e["activity"] == "received"]
        assert (received[0] if received else None) == (None if s["received_min"] is None else pytest.approx(s["received_min"], abs=0.01))
        seg = s["truth"]["segments"]
        assert seg[0][1] == 0 and seg[0][0] in ("at_unit",), s["id"]
        assert all(a[2] == b[1] for a, b in zip(seg, seg[1:])), s["id"]  # contiguous
        assert s["truth"]["breach"] == (s["received_min"] is None or s["received_min"] > s["window_min"]), s["id"]


def test_the_places_are_the_location_models_and_the_set_is_the_models(bundle):
    assert bundle["place_order"] == list(location.PLACES)


# ---- the showcase candidates ----

def test_ten_showcase_candidates_one_per_kind_and_each_is_in_the_bundle(bundle):
    cands, by_id = bundle["showcase_candidates"], {s["id"]: s for s in bundle["specimens"]}
    assert len(cands) == 10 and len({c["id"] for c in cands}) == 10 and len({c["kind"] for c in cands}) == 10
    for c in cands:
        s = by_id[c["id"]]
        assert (c["fault"], c["breach"], c["tier"], c["window_min"]) == (s["truth"]["fault"], s["truth"]["breach"], s["tier"], s["window_min"])
        assert c["model_alert_min"] == s["alerts"]["model"] and c["baseline_alert_min"] == s["alerts"]["plan_baseline"]
        assert c["receipt_min"] == s["received_min"]
    kinds = {c["kind"]: by_id[c["id"]] for c in cands}
    assert kinds["never_sent"]["truth"]["fault"] == "not_dispatched" and kinds["lost_in_tube"]["truth"]["fault"] == "lost_in_tube"
    assert kinds["content_failure"]["alerts"]["model"] is None and not kinds["content_failure"]["truth"]["breach"]
    assert not kinds["false_alarm"]["truth"]["breach"] and kinds["false_alarm"]["alerts"]["model"] is not None
    assert kinds["clear_catch"]["truth"]["breach"] and kinds["urgent_tier_catch"]["tier"] == 1
    assert kinds["stalled_then_found"]["received_min"] > kinds["stalled_then_found"]["alerts"]["model"]
    shortest = [c["window_min"] for c in cands if c["kind"] != "baseline_ahead"]
    assert max(shortest) <= bundle["horizon_min"]  # the timeline and the 3D view can show them


def test_the_heroes_are_the_stories_for_the_candidates(bundle):
    assert [h["id"] for h in bundle["heroes"]] == [c["id"] for c in bundle["showcase_candidates"]]  # plan task G3


# ---- what the bundle must not be ----

def test_the_bundle_is_synthetic_keyless_and_small_enough_for_a_phone(bundle):
    text = BUNDLE_PATH.read_text(encoding="utf-8")
    assert "Synthetic" in bundle["notice"] and "no clinical review" in bundle["notice"]
    assert "no clinical review" not in bundle["source"]["clinical_review"] or "not done" in bundle["source"]["clinical_review"]
    for needle in ("sk-ant", "ANTHROPIC", "api_key", "api.anthropic.com"):
        assert needle not in text
    assert BUNDLE_PATH.stat().st_size < 4_000_000


def test_the_exporter_does_not_open_the_truth_file(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the exporter opened the truth file itself")
    monkeypatch.setattr(store, "open_truth", refuse)
    assert "open_truth" not in (export.ROOT / "src" / "api" / "export.py").read_text(encoding="utf-8")


def test_a_stale_fit_is_refused(tmp_path, monkeypatch):
    from src.pipeline import intervals
    params = json.loads(intervals.PARAMS_PATH.read_text()) | {"policy_hash": "0" * 16}
    (tmp_path / "p.json").write_text(json.dumps(params))
    monkeypatch.setattr(intervals, "PARAMS_PATH", tmp_path / "p.json")
    with pytest.raises(RuntimeError, match="different policy"):
        export.build()


def test_the_triage_settings_in_the_bundle_are_the_configs(bundle):
    cfg = triage.load()
    assert bundle["triage"]["weights"] == {str(t): w for t, w in triage.weight_schemes(cfg)["default"].items()}
    assert bundle["triage"]["routing"]["still_at_unit"] == cfg["routing"]["still_at_unit"]
