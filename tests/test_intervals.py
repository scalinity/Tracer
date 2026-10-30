import copy
import json
import math
import re

import numpy as np
import pytest
from test_truth_isolation import touches_truth

from src.generator import config
from src.pipeline import intervals, store


def lognormal(rng, median, sigma, n):
    return np.exp(rng.normal(math.log(median), sigma, n))


@pytest.fixture(scope="module")
def params():
    return intervals.build()


# ---- the fitting itself, on data with a known answer ----

def test_the_pooled_fit_recovers_a_known_mixture():
    rng = np.random.default_rng(0)
    x = np.concatenate([lognormal(rng, 20, 0.5, 28500), lognormal(rng, 120, 0.4, 1500)])
    fit = intervals.fit_pooled(x)
    assert fit["mu"] == pytest.approx(math.log(20), abs=0.03) and fit["sigma"] == pytest.approx(0.5, abs=0.03)
    assert fit["stalled_mu"] == pytest.approx(math.log(120), abs=0.1)
    assert fit["stalled_sigma"] == pytest.approx(0.4, abs=0.08)
    assert fit["w_stalled"] == pytest.approx(0.05, abs=0.01)


def test_the_stalled_component_always_sits_above_the_normal_one():
    rng = np.random.default_rng(1)
    fit = intervals.fit_pooled(lognormal(rng, 25, 0.6, 5000))
    assert fit["stalled_mu"] > fit["mu"]


def test_a_group_fit_recovers_its_own_median_with_the_pooled_parts_held_fixed():
    rng = np.random.default_rng(2)
    pooled = {"mu": math.log(20), "sigma": 0.5, "stalled_mu": math.log(120), "stalled_sigma": 0.4, "w_stalled": 0.05}
    x = np.concatenate([lognormal(rng, 35, 0.45, 1900), lognormal(rng, 120, 0.4, 100)])
    mu, sigma = intervals.fit_normal(x, pooled)
    assert mu == pytest.approx(math.log(35), abs=0.04) and sigma == pytest.approx(0.45, abs=0.04)


# ---- falling back to a parent group ----

def rows_for(spec):
    """spec: {(type, block, priority): count}; every specimen arrives, around 20 minutes."""
    rng = np.random.default_rng(3)
    out = []
    for (t, b, p), n in spec.items():
        out += [(t, b, p, float(m)) for m in lognormal(rng, 20, 0.4, n)]
    return out


def test_the_chain_drops_the_hour_then_the_priority_then_the_type():
    assert intervals.parent_chain("urine", 2, "stat") == [
        ("urine", 2, "stat"), ("urine", None, "stat"), ("urine", None, None), (None, None, None)]


def test_a_group_of_thirty_fits_itself_and_one_of_twenty_nine_does_not():
    rows = rows_for({("urine", 0, "stat"): 30, ("urine", 1, "stat"): 29, ("urine", 2, "stat"): 200})
    groups = intervals.fit_all(rows)["groups"]
    assert groups["urine | 00-05 | stat"]["fitted_from"] == "urine | 00-05 | stat"
    assert groups["urine | 06-11 | stat"]["fitted_from"] == "urine | any hour | stat"
    assert groups["urine | 12-17 | stat"]["n"] == 200


def test_a_thin_type_falls_all_the_way_back_to_everything():
    rows = rows_for({("urine", 0, "stat"): 400, ("tissue", 0, "stat"): 10, ("tissue", 1, "routine"): 12})
    out = intervals.fit_all(rows)
    assert out["groups"]["tissue | 00-05 | stat"]["fitted_from"] == "all | any hour | any priority"
    assert out["groups"]["tissue | 00-05 | stat"]["mu"] == pytest.approx(out["pooled"]["mu"])


def test_never_arrivals_are_counted_from_the_started_specimens():
    rows = rows_for({("urine", 0, "stat"): 190}) + [("urine", 0, "stat", None)] * 10
    pooled = intervals.fit_all(rows)["pooled"]
    assert (pooled["n_started"], pooled["n_arrived"], pooled["n_never"]) == (200, 190, 10)
    assert pooled["p_never"] == pytest.approx(0.05) and sum(pooled["weights"].values()) == pytest.approx(1)
    assert pooled["weights"]["never"] == pytest.approx(0.05)


# ---- the real fit ----

def test_every_group_has_parameters(params):
    assert len(params["groups"]) == 96
    for name, g in params["groups"].items():
        assert math.isfinite(g["mu"]) and g["sigma"] > 0 and g["n"] > 0, name
        assert 0 < g["median_min"] < g["p90_min"], name


def test_a_group_fits_itself_exactly_when_it_has_thirty_received(params):
    for name, g in params["groups"].items():
        assert (g["fitted_from"] == name) == (g["n"] >= intervals.MIN_GROUP), name


def test_the_fit_uses_the_fit_runs_only(params):
    con = store.open_observed()
    started = con.sql("""select count(*) from specimens s join events e on e.specimen_id = s.specimen_id
                         and e.activity = 'collected' where s.split = 'fit'""").fetchone()[0]
    everything = con.sql("select count(*) from events where activity = 'collected'").fetchone()[0]
    assert params["pooled"]["n_started"] == started < everything
    assert params["fit_runs"] == "0 to 23"


def test_the_parameters_carry_the_policy_and_the_source(params):
    assert params["policy_hash"] == config.load()["policy_hash"]
    assert params["source_sha256"] == store.source_sha256()
    assert (params["clock_start"], params["clock_stop"]) == ("collected", "received")


def test_the_pooled_weights_add_up(params):
    p = params["pooled"]
    assert sum(p["weights"].values()) == pytest.approx(1, abs=1e-5)
    assert 0 < p["w_stalled"] < 0.2 and 0.01 < p["p_never"] < 0.03
    assert p["stalled_mu"] > math.log(params["groups"]["BAL, room temp | 06-11 | routine"]["median_min"])


def test_a_rerun_gives_identical_numbers(params):
    assert json.dumps(intervals.build(), sort_keys=True) == json.dumps(params, sort_keys=True)


# ---- the clock comes from the policy ----

def test_the_clock_is_read_from_the_policy():
    loaded = config.load()
    assert intervals.clock_events(loaded["policy"]) == ("collected", "received")
    moved = copy.deepcopy(loaded["policy"])
    for p in moved["policies"].values():
        p["clock_stop"] = "plated"
    assert intervals.clock_events(moved) == ("collected", "plated")
    con = store.open_observed()
    to_receipt = [m for *_, m in intervals.load_intervals(con, "collected", "received") if m is not None]
    to_plating = [m for *_, m in intervals.load_intervals(con, "collected", "plated") if m is not None]
    assert np.median(to_plating) > np.median(to_receipt) + 10


def test_policy_rows_that_disagree_about_the_clock_are_refused():
    policy = copy.deepcopy(config.load()["policy"])
    next(iter(policy["policies"].values()))["clock_stop"] = "plated"
    with pytest.raises(RuntimeError, match="2 different clocks"):
        intervals.clock_events(policy)


# ---- the fit never touches the truth ----

def test_the_fit_code_does_not_refer_to_the_truth():
    assert touches_truth((intervals.ROOT / "src" / "pipeline" / "intervals.py").read_text(encoding="utf-8")) == []


def test_the_fit_runs_with_the_truth_file_unavailable(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the fit opened the truth file")
    monkeypatch.setattr(store, "open_truth", refuse)
    assert intervals.build()["pooled"]["n_started"] > 9000


# ---- the command ----

def test_main_prints_every_group_and_writes_the_file(tmp_path, monkeypatch, capsys, params):
    monkeypatch.setattr(intervals, "PARAMS_PATH", tmp_path / "interval_params.json")
    assert intervals.main() == 0
    out = capsys.readouterr().out
    assert len(re.findall(r"^.+ \| (?:00-05|06-11|12-17|18-23) \| (?:routine|stat) ", out, flags=re.M)) == 96
    assert "pooled: stalled lognormal" in out
    assert json.loads((tmp_path / "interval_params.json").read_text()) == json.loads(json.dumps(params))
