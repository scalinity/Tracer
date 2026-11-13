"""The library freeze's split rules (docs/neural/spec.md §4.11, §6.2)."""

import copy
import json
from pathlib import Path

import pytest

from src.scenarios import freeze

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = json.loads((ROOT / "config" / "scenario.example.json").read_text())


def member(archetype="community_hospital", stratum="normal", disruptions=()):
    s = copy.deepcopy(EXAMPLE)
    s["archetype"] = archetype
    s["disruptions"] = [{"type": t} for t in disruptions]
    return {"scenario": s, "brief": {"stratum": stratum}}


def test_the_out_of_distribution_rule_is_the_pre_registered_one():
    assert freeze.is_ood(member("multi_site_system")["scenario"])
    assert freeze.is_ood(member(disruptions=("tube_line_outage", "scanner_downtime"))["scenario"])
    assert not freeze.is_ood(member(disruptions=("tube_line_outage",))["scenario"])


def test_a_family_with_any_out_of_distribution_member_goes_to_test_ood_and_never_to_train_or_val():
    fams = {"F0001": [member(), member("multi_site_system")], "F0002": [member()]}
    split = freeze.assign_first_splits(fams)
    assert split["F0001"] == "test_ood" and split["F0002"] in ("train", "val", "test")


def test_families_split_70_15_15_within_each_cell_and_the_assignment_is_stable():
    fams = {f"F{k:04d}": [member(stratum="stressed")] for k in range(1, 201)}
    split = freeze.assign_first_splits(fams)
    counts = {s: list(split.values()).count(s) for s in ("train", "val", "test")}
    assert counts == {"train": 140, "val": 30, "test": 30}
    assert freeze.assign_first_splits(fams) == split
    assert all(0 <= freeze.unit_hash(f) < 1 for f in fams)


def test_simulation_inputs_carry_id_family_split_and_index_beside_the_configuration():
    rec = {"meta": {"scenario_id": "S0007", "family_id": "F0003", "scenario_index": 7}, "scenario": EXAMPLE}
    out = freeze.simulation_inputs([rec], {"scenarios": {"S0007": "val"}})
    assert out[0]["split"] == "val" and out[0]["scenario_index"] == 7 and out[0]["units"] == EXAMPLE["units"]


def test_a_later_freeze_skips_the_first_freezes_left_out_items_and_refuses_any_other_first_round_item():
    from src.scenarios import runner

    items = [{"item_id": f"O2-{k}", "job": "O2"} for k in range(5)] + [{"item_id": "O3-0", "job": "O3"}]
    known = {"O2-0", "O2-1", "O2-2"}                      # O2-3 and O2-4 were left out by the first freeze
    assert [i["item_id"] for i in freeze.round2_items(items, known, 2)] == ["O3-0"]
    with pytest.raises(runner.JobError, match="only add round-2"):
        freeze.round2_items(items, known, 1)              # one first-round item the first freeze never left out
    manifest = {"near_duplicates": {"left_out": 21}, "left_out_other": {"plausibility": 2}}
    assert freeze.first_round_left_out(manifest) == 23
    assert freeze.first_round_left_out(manifest | {"first_round_left_out": 21}) == 21


def test_a_later_freeze_takes_coverage_fill_items_too():
    known = {f"O2-{k}" for k in range(3)}
    items = [{"item_id": f"O2-{k}", "job": "O2"} for k in range(4)] + [{"item_id": "O3-0", "job": "O3"},
                                                                         {"item_id": "O4-0", "job": "O4"}]
    assert [i["item_id"] for i in freeze.round2_items(items, known, 1)] == ["O3-0", "O4-0"]


def test_fill_jobs_are_the_coverage_fill_and_keep_validation_unchanged():
    """O4's new families go to train only (preregistration-v2 §7): the source says so, beside round two's 85/15."""
    import inspect
    assert freeze.FILL_JOBS == ("O4", "O4b") and set(freeze.LATER_JOBS) == {"O3", "O3b", "O4", "O4b"}
    src = inspect.getsource(freeze.freeze)
    assert 'if all(m["job"] in FILL_JOBS for m in members):' in src
    assert '(fill_only and split_of_family[fid] == "val")' in src
    assert 'split_of_family[fid] = "train"' in src


def test_a_later_freeze_keeps_out_what_an_earlier_freeze_of_the_same_job_left_out():
    """Freezing O4 must not reconsider O3 items the round-two freeze left out."""
    known = {"O2-0", "O3-0"}
    items = [{"item_id": "O2-0", "job": "O2"}, {"item_id": "O3-0", "job": "O3"}, {"item_id": "O3-1", "job": "O3"},
             {"item_id": "O4-0", "job": "O4"}]
    got = freeze.round2_items(items, known, 0, frozen_jobs={"O2", "O3"})
    assert [i["item_id"] for i in got] == ["O4-0"]
