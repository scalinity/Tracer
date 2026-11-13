"""The fresh test set of docs/neural/preregistration-v2.md: its own lock, looked at once, and the placement rule that
keeps its scenarios out of every library family. Nothing here reads a test set."""
import json

import numpy as np
import pytest

from src.pipeline import neural_eval as ne, rule_v2, store
from src.scenarios import fresh

NOW = "2026-11-14T09:00:00-05:00"


def hashes(**override):
    return ne.current_hashes("a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64) | override


@pytest.fixture
def fresh_files(tmp_path):
    d = tmp_path / "test_fresh"
    return {"lock_path": d / "test_lock.json", "runs_path": d / "test_runs.jsonl", "now": NOW}


def test_the_fresh_set_is_locked_with_its_own_files():
    assert ne.FRESH_SPLIT == "test_fresh" and ne.FRESH_SPLIT in ne.LOCKED_SPLITS
    assert ne.lock_paths("test_fresh") == (ne.FRESH_LOCK_PATH, ne.FRESH_RUNS_PATH)
    assert ne.lock_paths("test") == ne.lock_paths("test_ood") == (ne.LOCK_PATH, ne.TEST_RUNS_PATH)
    assert ne.FRESH_LOCK_PATH.parent != ne.LOCK_PATH.parent
    assert "test_fresh" in store.V2_SPLITS


def test_the_fresh_set_has_no_relock(fresh_files):
    first = ne.guard_test_run("test_fresh", hashes(), **fresh_files)
    assert first["status"] == "first"
    with pytest.raises(ne.LockError, match="no relock"):
        ne.guard_test_run("test_fresh", hashes(store_sha256="9" * 64), relock_reason="a better model", **fresh_files)
    with pytest.raises(ne.LockError, match="changed"):
        ne.guard_test_run("test_fresh", hashes(store_sha256="9" * 64), **fresh_files)


def test_a_crashed_look_may_resume_on_the_same_inputs_but_no_look_follows_a_written_result(fresh_files):
    ne.guard_test_run("test_fresh", hashes(), **fresh_files)
    again = ne.guard_test_run("test_fresh", hashes(), **fresh_files)
    assert again["status"] == "repeat"
    (fresh_files["lock_path"].parent / "evaluation.json").write_text("{}")
    with pytest.raises(ne.LockError, match="no second look"):
        ne.guard_test_run("test_fresh", hashes(), **fresh_files)
    log = [json.loads(x) for x in fresh_files["runs_path"].read_text().splitlines()]
    assert [r["status"] for r in log] == ["first", "repeat"]


def test_the_fresh_set_needs_the_training_store_for_its_global_fits(tmp_path):
    with pytest.raises(ValueError, match="training store"):
        ne.command_evaluate("test_fresh", store_dir=tmp_path, out_dir=tmp_path)


def test_placement_leaves_out_near_duplicates_and_would_be_family_members():
    delta = 1.0
    x_lib = np.array([[0.0, 0.0], [10.0, 0.0]])
    arch_lib = ["a", "b"]
    x_new = np.array([[0.5, 0.0],    # same archetype as the library's first, within delta: near duplicate
                      [11.2, 0.0],   # other archetype, within 1.5 delta of the second: would share a family
                      [30.0, 0.0],   # far from everything: kept
                      [30.5, 0.0]])  # a near duplicate of the fresh one before it: later item left out
    out = fresh.placement(x_lib, arch_lib, x_new, ["a", "a", "c", "c"], ["T1-1", "T1-2", "T1-3", "T1-4"], delta)
    assert out == {"T1-1": "near duplicate of a library scenario",
                   "T1-2": "would share a family with library scenarios",
                   "T1-4": "near duplicate of an earlier fresh scenario"}


def test_the_fresh_ids_seeds_and_prompt_are_the_preregistered_ones():
    assert fresh.FIRST_INDEX == 20001 and fresh.PREREGISTERED_PROMPT_HASH == "5876a443517f72a4"
    assert fresh.REPLICATES == {"test_fresh": [0, 1, 2, 3]}
    from src.sim import records
    assert records.run_key(fresh.FIRST_INDEX + 999, 3) < 1_000_000   # still six digits in a specimen id


def test_the_rule_reads_the_five_baselines_and_both_weightings():
    assert rule_v2.BASELINES == ("window_rule", "analytic_global", "analytic_local", "quantile_local",
                                 "quantile_global")
    assert rule_v2.WEIGHTINGS == ("3-2-1", "equal") and rule_v2.BUDGET == "6/shift"
    assert rule_v2.BRIER_MARGIN == 0.01 and rule_v2.LOSS_MARGIN == 0.02
