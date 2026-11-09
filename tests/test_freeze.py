"""The library freeze's split rules (docs/neural/spec.md §4.11, §6.2)."""

import copy
import json
from pathlib import Path

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
