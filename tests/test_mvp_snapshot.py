"""The midterm snapshot (plan task M6) is a record: the December 2 results are compared against it.

Editing results/mvp-nov8.json fails the first test on purpose. A deliberate change means replacing
the hash below in the same commit and saying why in the pull request.
"""

import hashlib
import json

from src.pipeline import store

SNAPSHOT = store.ROOT / "results" / "mvp-nov8.json"
PINNED_SHA256 = "5dd432d0f2b86c9afa5722b6c8cc197212cc5e6f93548874d86ca2c16c6f1455"


def load() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def test_the_midterm_snapshot_is_untouched():
    assert hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest() == PINNED_SHA256


def test_it_was_taken_from_the_frozen_dataset():
    snap = load()
    assert snap["snapshot"] == "v0.1-mvp"
    assert snap["dataset"]["sha256"] == store.source_sha256()
    assert snap["dataset"]["accepted"] == 11967 and "no clinical review" in snap["synthetic_data_notice"]


def test_the_headline_agrees_with_the_full_evaluation_inside_it():
    snap = load()
    for budget, block in snap["headline_by_alerts_per_shift"].items():
        point = snap["evaluation"]["operating_points"][budget]
        assert block["median_warning_all_breaches_min"]["plan_baseline"] == point["baseline"]["median_warning_all_breaches_min"]
        assert block["median_warning_all_breaches_min"]["model_3_2_1"] == point["model"]["median_warning_all_breaches_min"]
        assert block["median_warning_all_breaches_min"]["window_baseline"] == point["window_baseline"]["median_warning_all_breaches_min"]


def test_the_bar_is_judged_on_misses_counted_as_zero_and_the_caveats_travel_with_it():
    snap = load()
    assert "a miss counts as zero" in snap["pre_registered_bar"]["measure"]
    text = " ".join(snap["caveats"])
    for needle in ("Synthetic data", "no clinical review", "share assumptions", "window-aware baseline", "budget is generous",
                   "weights move the headline", "Brier", "dropped", "best case"):
        assert needle in text, needle


def test_it_matches_the_results_the_code_regenerates_today():
    """Later changes to the code may move the live results; this fails only if they stop agreeing on the same data."""
    snap = load()
    live = json.loads((store.ROOT / "results" / "evaluation.json").read_text(encoding="utf-8"))
    assert snap["evaluation"]["held_out_runs"] == live["held_out_runs"] and snap["evaluation"]["breaches"] == live["breaches"]
    assert snap["evaluation"]["policy_hash"] == live["policy_hash"] and snap["evaluation"]["source_sha256"] == live["source_sha256"]
