"""Worker pools and the progress file (src/workers.py)."""

import json
import os

from src import workers


def test_one_thread_children_sets_every_thread_variable_and_restores_the_parent(monkeypatch):
    monkeypatch.setenv("OMP_NUM_THREADS", "8")
    monkeypatch.delenv("POLARS_MAX_THREADS", raising=False)
    with workers.one_thread_children():
        assert all(os.environ[k] == "1" for k in workers.THREAD_VARS)
    assert os.environ["OMP_NUM_THREADS"] == "8" and "POLARS_MAX_THREADS" not in os.environ


def test_report_writes_the_progress_file_only_when_asked(monkeypatch, tmp_path):
    path = tmp_path / "progress.json"
    monkeypatch.delenv("TRACER_PROGRESS", raising=False)
    workers.report("val: tuning and scoring", 0, 10, "scenarios")
    assert not path.exists()
    monkeypatch.setenv("TRACER_PROGRESS", str(path))
    workers.label("warning check, rung C")
    workers.report("val: tuning and scoring", 0, 10, "scenarios")
    workers.report("val: tuning and scoring", 3, 10, "scenarios")      # within 5 s of the last write: skipped
    assert json.loads(path.read_text())["done"] == 0
    workers.report("val: tuning and scoring", 10, 10, "scenarios")     # a stage's end is always written
    record = json.loads(path.read_text())
    workers.label(None)
    assert record["stage"] == "warning check, rung C: val: tuning and scoring"
    assert (record["done"], record["total"], record["unit"]) == (10, 10, "scenarios") and record["started"]
