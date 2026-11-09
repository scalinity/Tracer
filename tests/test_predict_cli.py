"""TracerNet's command line and records (docs/neural/spec.md §10.2, §10.3, §17 item 16): it writes predictions.parquet,
alerts.parquet and alerts.json that validate against their schemas, merges frozen dispatch decisions (null without
them), refuses a locked split without its test run, and the schemas themselves catch damaged records."""

import json
from pathlib import Path

import jsonschema
import polars as pl
import pytest

import tinynet as T
from src.models.tracernet import predict as PR
from src.pipeline import store

DECISION = {"source": "rules", "model": None, "place": "at_lab_tube_station", "place_p": 0.7,
            "action": "check_station", "action_p": 0.8, "role": "receiving", "shared_problem_p": 0.1,
            "escalate_p": 0.05}


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    d = T.build_store(tmp_path_factory.mktemp("predict"))
    return {"store": d, "pred": T.predictor(d)}


@pytest.fixture(scope="module")
def written(tiny, tmp_path_factory):
    out = tmp_path_factory.mktemp("predictions") / "val"
    res = PR.run(None, tiny["store"], "val", 6, "default", explain=True, out=out, predictor=tiny["pred"], workers=1)
    return out, res


def test_the_schemas_are_draft_2020_12():
    for path in (PR.PREDICTION_SCHEMA, PR.ALERT_SCHEMA):
        doc = json.loads(path.read_text())
        assert doc["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        jsonschema.Draft202012Validator.check_schema(doc)


def test_run_writes_the_three_files_and_each_validates(tiny, written):
    out, res = written
    risk, alerts = pl.read_parquet(out / "predictions.parquet"), pl.read_parquet(out / "alerts.parquet")
    assert risk.equals(res["predictions"]) and alerts.equals(res["alerts"])
    records = json.loads((out / "alerts.json").read_text())
    assert records == json.loads(json.dumps(res["records"])) and len(records) == alerts.height > 0
    PR.validate_predictions(risk, sample=risk.height)          # every row through jsonschema too
    PR.validate_alerts(records)
    assert set(risk["run"].unique()) == set(T.runs(tiny["store"], "val", "scored"))
    assert all(r["factors"] for r in records) and all(r["dispatch"] is None for r in records)
    assert all(r["notice"] == T.NOTICE for r in records)


def test_the_command_line_runs_through_the_same_path(tiny, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(PR.Predictor, "load", classmethod(lambda cls, path, **k: tiny["pred"]))
    out = tmp_path / "cli"
    code = PR.main(["--store", str(tiny["store"]), "--split", "val", "--budget", "6", "--knob", "0.9",
                    "--out", str(out)])
    assert code == 0 and "wrote" in capsys.readouterr().out
    alerts = pl.read_parquet(out / "alerts.parquet")
    assert (alerts["knob"] == 0.9).all() and all(f == [] for f in alerts["factors"].to_list())   # no --explain


def test_frozen_dispatch_decisions_are_merged_and_validated(tiny, written, tmp_path):
    _, res = written
    first = res["records"][0]["alert_id"]
    good = tmp_path / "decisions.json"
    good.write_text(json.dumps({first: DECISION}))
    merged = PR.merge_dispatch(res["alerts"], PR.load_dispatch(good)).to_dicts()
    assert merged[0]["dispatch"] == DECISION and all(r["dispatch"] is None for r in merged[1:])
    PR.validate_alerts(merged)
    folder = tmp_path / "decisions"
    folder.mkdir()
    (folder / "x2.json").write_text(json.dumps([{"alert_id": first, "dispatch": DECISION}]))
    assert PR.load_dispatch(folder) == {first: DECISION}
    bad = PR.merge_dispatch(res["alerts"], {first: DECISION | {"source": "oracle"}}).to_dicts()
    with pytest.raises(jsonschema.ValidationError):
        PR.validate_alerts(bad)


def test_a_locked_split_is_refused_without_its_test_run(tiny, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(PR.Predictor, "load", classmethod(lambda cls, path, **k: tiny["pred"]))
    code = PR.main(["--store", str(tiny["store"]), "--split", "test", "--out", str(tmp_path / "t")])
    assert code == 2 and "test lock" in capsys.readouterr().err
    assert not (tmp_path / "t").exists()


def test_damaged_predictions_are_caught_column_by_column(written):
    _, res = written
    risk = res["predictions"]
    doc = json.loads(PR.PREDICTION_SCHEMA.read_text())
    assert PR.column_problems(risk, doc) == []
    for damaged, words in (
            (risk.with_columns(p_miss=pl.col("p_miss") + 1.5), "p_miss"),
            (risk.with_columns(specimen_id=pl.lit("SP-x")), "specimen_id"),
            (risk.with_columns(route=pl.lit("drone")), "route"),
            (risk.with_columns(tier=pl.lit(None, dtype=pl.Int64)), "tier: "),
            (risk.with_columns(extra=pl.lit(1)), "not in the schema")):
        assert any(words in p for p in PR.column_problems(damaged, doc)), words
    with pytest.raises(ValueError, match="p_late \\+ p_never"):
        PR.validate_predictions(risk.with_columns(p_late=pl.col("p_late") * 0.5 + 0.001))


def test_the_v1_store_scores_its_held_out_runs_with_the_fit_runs_as_history():
    scored, history = PR.split_runs(store.STORE_DIR, "test")
    assert scored == list(range(24, 30)) and history == list(range(24))
