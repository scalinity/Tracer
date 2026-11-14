"""docs/demo/results.json, the numbers the phone page's #warning section shows.

scripts/build_demo_results.py copies them from the committed evaluation of the fresh test set; nothing here reads
or scores a test set. The file must be what the script writes, and the numbers on screen must be the ones the
talk and the docs state.
"""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "docs" / "demo" / "results.json"


def builder():
    spec = importlib.util.spec_from_file_location("build_demo_results", ROOT / "scripts" / "build_demo_results.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_results_json_is_what_the_script_writes_from_the_committed_evaluation():
    same = builder().build() == RESULTS.read_text(encoding="utf-8")
    assert same, "docs/demo/results.json differs from evaluation.json: run python scripts/build_demo_results.py"


def test_the_page_shows_tracernets_minutes_of_warning_and_false_alarms_at_1_2_4_and_6_alerts_a_shift():
    r = json.loads(RESULTS.read_text(encoding="utf-8"))
    assert (r["method"], r["weights"], r["default_alerts_per_shift"]) == ("tracernet", "3-2-1", 6)
    shown = [(b["alerts_per_shift"], f"{b['warning_min']:.1f}", f"{b['false_alarms_per_shift']:.1f}") for b in r["budgets"]]
    assert shown == [(1, "3.1", "0.2"), (2, "8.2", "0.6"), (4, "16.4", "1.6"), (6, "23.3", "2.9")]


def test_the_source_line_names_the_scored_set_and_its_fingerprint():
    r = json.loads(RESULTS.read_text(encoding="utf-8"))
    looked = json.loads((ROOT / "results" / "neural" / "test_fresh" / "looked.json").read_text(encoding="utf-8"))
    assert r["sha256"] == looked["sha256"] and r["scenarios"] == 776
    page = (ROOT / "docs" / "demo" / "index.html").read_text(encoding="utf-8")
    assert f"From one offline run on {r['scenarios']} synthetic lab weeks." in page
