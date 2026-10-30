import copy

import pytest

from src.generator import compile_policy, config


@pytest.fixture(scope="module")
def loaded():
    return config.load()


def test_committed_yaml_matches_csv():
    expected = compile_policy.render(compile_policy.compile_rows(compile_policy.read_csv()))
    assert compile_policy.YAML_PATH.read_text(encoding="utf-8") == expected, \
        "breach_policy.yaml is stale: run python -m src.generator.compile_policy"


def test_every_csv_row_compiles(loaded):
    rows = compile_policy.read_csv()
    assert len(loaded["policy"]["policies"]) == len(rows)


def test_assumed_flag_follows_the_source_column(loaded):
    for pid, p in loaded["policy"]["policies"].items():
        assert p["assumed"] == p["source"].startswith("?"), pid
        if not p["assumed"]:
            assert p["verified_by"], f"{pid} has a source but no verifier"


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
