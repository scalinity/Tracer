import copy

import pytest

from src.generator import compile_policy, config


@pytest.fixture(scope="module")
def loaded():
    return config.load()


def test_committed_yaml_matches_csv():
    expected = compile_policy.render(compile_policy.compile_rows(
        compile_policy.read_csv(), compile_policy.read_csv(compile_policy.SOURCES_PATH)))
    assert compile_policy.YAML_PATH.read_text(encoding="utf-8") == expected, \
        "breach_policy.yaml is stale: run python -m src.generator.compile_policy"


def test_every_csv_row_compiles(loaded):
    rows = compile_policy.read_csv()
    assert len(loaded["policy"]["policies"]) == len(rows)


def test_assumed_flag_follows_the_source(loaded):
    for pid, p in loaded["policy"]["policies"].items():
        assert p["assumed"] == compile_policy.is_unsourced(p["source"]), pid
        if not p["assumed"] and p["source_origin"] == "sheet":
            assert p["verified_by"], f"{pid} has a source but no verifier"


def test_added_sources_never_override_the_sheet():
    rows = compile_policy.read_csv()
    sourced = next(r for r in rows if not compile_policy.is_unsourced(r["source_citation"]))
    clash = [{"specimen_type": sourced["specimen_type"], "test": sourced["test"],
              "source_citation": "something else", "note": ""}]
    with pytest.raises(ValueError, match="already has a source"):
        compile_policy.compile_rows(rows, clash)


def test_every_clock_stops_at_receipt(loaded):
    assert {p["clock_stop"] for p in loaded["policy"]["policies"].values()} == {"received"}


def test_bad_state_fails_loudly(loaded):
    bad = copy.deepcopy(loaded["policy"])
    next(iter(bad["policies"].values()))["clock_stop"] = "set_up"
    with pytest.raises(config.PolicyError, match="not a workflow state"):
        config.validate_policy(bad, loaded["graph"])


def test_unreachable_stop_fails_loudly(loaded):
    bad = copy.deepcopy(loaded["policy"])
    p = next(iter(bad["policies"].values()))
    p["clock_start"], p["clock_stop"] = "plated", "received"
    with pytest.raises(config.PolicyError, match="not reachable"):
        config.validate_policy(bad, loaded["graph"])


def test_hash_changes_with_clock_stop(loaded):
    moved = copy.deepcopy(loaded["policy"])
    for p in moved["policies"].values():
        p["clock_stop"] = "plated"
    assert config.policy_hash(moved) != loaded["policy_hash"]
    assert config.policy_hash(copy.deepcopy(loaded["policy"])) == loaded["policy_hash"]


def test_multiple_tests_take_the_shortest_window(loaded):
    pid, p = config.resolve(loaded["policy"], "BAL, refrigerated", ["respiratory panel", "Gram stain"])
    assert p["test"] == "Gram stain"
    assert p["window_min"] == 1440
