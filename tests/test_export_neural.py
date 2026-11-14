"""TracerNet's demo file (docs/neural/spec.md §10.5, §15.2 test_export_neural.py, §17 item 16): the output validates
against docs/demo/neural.schema.json for both sources, a wrong policy hash refuses, docs/demo/bundle.json stays
byte-identical, every knob and alert comes from the evaluation harness (on legacy_v1 the bundle's own alerts at the
headline budget), the headline alerts carry factors, and the truth comes only through evaluate.py's accessors."""

import hashlib
import json

import jsonschema
import pytest

import tinynet as T
from src.api import export, export_neural as E
from src.models.tracernet.predictor import Predictor
from src.pipeline import evaluate

SCHEMA = json.loads(E.SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    d = T.build_store(tmp_path_factory.mktemp("export-neural"))
    pred = T.predictor(d)
    scenario = T.scenario(d, "val")
    return {"store": d, "pred": pred, "scenario": scenario,
            "doc": E.build(pred, "v2", scenarios=[scenario], store_dir=d)}


@pytest.fixture(scope="module")
def legacy(tiny):
    return E.build(tiny["pred"], "legacy_v1")


def sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_schema_is_valid_and_both_sources_validate(tiny, legacy):
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    jsonschema.validate(tiny["doc"], SCHEMA)
    jsonschema.validate(legacy, SCHEMA)
    assert tiny["doc"]["scenarios"] == [tiny["scenario"]] and legacy["scenarios"] is None
    assert "Synthetic" in tiny["doc"]["notice"] and "Synthetic" in legacy["notice"]


def test_main_writes_only_where_told_and_the_bundle_stays_byte_identical(tiny, tmp_path, monkeypatch, capsys):
    before, existed = sha(export.DEFAULT_OUT), E.DEFAULT_OUT.exists()
    monkeypatch.setattr(E.Predictor, "load", classmethod(lambda cls, path, **k: tiny["pred"]))
    out = tmp_path / "neural.json"
    code = E.main(["--source", "v2", "--scenarios", tiny["scenario"], "--store", str(tiny["store"]), "--out", str(out)])
    assert code == 0 and "wrote" in capsys.readouterr().out
    assert json.loads(out.read_text()) == json.loads(json.dumps(tiny["doc"]))
    assert sha(export.DEFAULT_OUT) == before and E.DEFAULT_OUT.exists() == existed
    assert E.main(["--source", "v2", "--out", str(tmp_path / "x.json")]) == 2       # v2 needs scenarios


def test_a_model_trained_under_another_policy_is_refused(tiny):
    p = tiny["pred"]
    other = Predictor(p.members, p.schema, p.calibration, policy_hash="0" * 16, manifest=p.manifest)
    with pytest.raises(RuntimeError, match="policy"):
        E.build(other, "v2", scenarios=[tiny["scenario"]], store_dir=tiny["store"])


def test_the_specimens_are_the_scored_replicates_with_their_truth_from_evaluate(tiny):
    doc = tiny["doc"]
    specs, _ = evaluate.load_specs_v2(None, ("scored",), tiny["store"], labels=True, scenarios=[tiny["scenario"]])
    by_id = {s.specimen_id: s for s in specs}
    assert [s["id"] for s in doc["specimens"]] == [s.specimen_id for s in specs]
    for s in doc["specimens"]:
        spec = by_id[s["id"]]
        assert s["truth"] == {"breach": spec.breach, "fault": spec.fault, "outcome": spec.outcome_class,
                              "cause": spec.cause}
        assert len(s["p_miss"]) == len(s["p_never"]) == min(len(spec.ticks), export.HORIZON_MIN // 5)
    source = (E.ROOT / "src" / "api" / "export_neural.py").read_text()
    assert "open_truth" not in source and "truth.duckdb" not in source


def test_alerts_come_earlier_or_stay_as_the_budget_grows(tiny, legacy):
    for doc in (tiny["doc"], legacy):
        assert doc["grid_budgets"] == list(export.GRID_BUDGETS)
        for s in doc["specimens"]:
            lists = [*s["alerts"]["tracernet"].values(), *s["alerts"]["analytic_local"].values(),
                     s["alerts"]["window_rule"]]
            for ticks in lists:
                fired = [t for t in ticks if t is not None]
                assert fired == sorted(fired, reverse=True), s["id"]                 # earlier or equal
                assert ticks.index(fired[0]) + len(fired) == len(ticks) if fired else True


def test_the_headline_alert_is_tracernets_at_the_headline_budget_and_carries_factors(tiny, legacy):
    for doc in (tiny["doc"], legacy):
        i = doc["headline_index"]
        assert doc["grid_budgets"][i] == 6
        alerted = 0
        for s in doc["specimens"]:
            h = s["headline"]
            assert h["alert_min"] == s["alerts"]["tracernet"]["3-2-1"][i], s["id"]
            if h["alert_min"] is not None:
                alerted += 1
                assert h["factors"] and h["dispatch"] is None, s["id"]
            else:
                assert h["factors"] == []
        assert alerted > 0


def test_on_legacy_v1_the_baselines_are_the_bundles_own_alerts(legacy):
    """The same held-out specimens as the demo bundle, and at the headline budget the window rule's and the local
    analytic model's alerts (the committed fit on v1) equal the bundle's window baseline and model alerts."""
    bundle = json.loads(export.DEFAULT_OUT.read_text(encoding="utf-8"))
    assert [s["id"] for s in legacy["specimens"]] == [s["id"] for s in bundle["specimens"]]
    i = legacy["headline_index"]
    assert bundle["grid"][bundle["headline_index"]]["budget_per_shift"] == legacy["grid_budgets"][i]
    for ours, theirs in zip(legacy["specimens"], bundle["specimens"]):
        assert ours["alerts"]["window_rule"][i] == theirs["alerts"]["window_baseline"], ours["id"]
        assert ours["alerts"]["analytic_local"]["3-2-1"][i] == theirs["alerts"]["model"], ours["id"]
        assert ours["truth"] == {"breach": theirs["truth"]["breach"], "fault": theirs["truth"]["fault"]}
        assert ours["received_min"] == theirs["received_min"]


TRAPPED = ("load_specs_v2", "load_specs", "load_test_specs", "truth_places", "truth_places_v2", "truth_causes_v2",
           "truth_disruptions_v2", "scenario_cells_v2", "scenario_traits_v2")


class Refusing:
    """A test run whose lock no longer holds."""

    def check(self, split, **kw):
        raise RuntimeError("the lock does not hold this run")


@pytest.mark.parametrize("split", ["test", "test_ood"])
def test_a_locked_scenario_is_refused_before_any_specimen_label_or_feature_is_read(tiny, monkeypatch, tmp_path, split):
    """F001: the lock is checked on every run of the chosen scenarios, history included, before evaluate's accessors
    or the feature table are reached: with no test run, and with a test run whose check refuses."""
    from src.models.tracernet import features as F
    from src.pipeline import store

    read = []

    def trap(name):
        def refuse(*a, **k):
            read.append(name)
            raise AssertionError(f"{name} was reached before the lock")
        return refuse
    for name in TRAPPED:
        monkeypatch.setattr(evaluate, name, trap(name))
    monkeypatch.setattr(store, "open_truth", trap("open_truth"))
    monkeypatch.setattr(F, "load_table", trap("load_table"))
    scenario, val = T.scenario(tiny["store"], split), tiny["scenario"]
    for chosen in ([scenario], [val, scenario]):
        with pytest.raises(RuntimeError, match="test lock"):
            E.build(tiny["pred"], "v2", scenarios=chosen, store_dir=tiny["store"])
    p = tiny["pred"]
    loaded = Predictor(p.members, p.schema, p.calibration, manifest=p.manifest)
    loaded.artifact_dir = tmp_path                        # as if loaded from an artifact, so the run's check is asked
    with pytest.raises(RuntimeError, match="does not hold"):
        E.build(loaded, "v2", scenarios=[scenario], store_dir=tiny["store"], test_run=Refusing())
    with pytest.raises(ValueError, match="not in the store"):
        E.build(tiny["pred"], "v2", scenarios=["no-such-scenario"], store_dir=tiny["store"])
    assert read == []


def test_the_committed_demo_file_is_tracernet_v2_and_its_knob_gives_every_headline_alert():
    """docs/demo/neural.json comes from the primary model, and each headline alert is the first 5-minute tick where
    p_miss (whose first value is at minute 5) times the tier's 3-2-1 weight reaches the file's headline knob."""
    doc = json.loads(E.DEFAULT_OUT.read_text(encoding="utf-8"))
    jsonschema.validate(doc, json.loads(E.SCHEMA_PATH.read_text(encoding="utf-8")))
    assert doc["model"]["version"] == "tracernet-v2" and doc["store"]["path"] == "data/store"
    (knob,) = doc["headline_knobs"].values()
    weight = {1: 3, 2: 2, 3: 1}
    alerts = 0
    for s in doc["specimens"]:
        first = next((5 * (i + 1) for i, p in enumerate(s["p_miss"]) if p * weight[s["tier"]] >= knob), None)
        assert first == s["headline"]["alert_min"] == s["alerts"]["tracernet"]["3-2-1"][doc["headline_index"]], s["id"]
        alerts += first is not None
    assert alerts == 817
