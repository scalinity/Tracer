"""The control_v1world set's store (spec §8.1, §8.8): the v1-world scenario simulated into its own store."""

from src.pipeline import neural_eval as ne, store
from src.sim import v1world


def test_the_control_store_holds_the_v1_world_scenario_the_control_set_finds(tmp_path):
    counts = store.build_control_v2(tmp_path / "sim", tmp_path / "store", workers=1, replicates=2)
    con = store.open_observed_v2(tmp_path / "store")
    try:
        runs = con.execute("select scenario_id, split, run from runs order by run").fetchall()
    finally:
        con.close()
    assert [(s, sp) for s, sp, _ in runs] == [(v1world.SCENARIO_ID, "val")] * 2 and counts["truth_scenarios"] == 1
    assert len({r for *_, r in runs}) == 2
    assert ne._set_scenarios("control_v1world", tmp_path / "store", ne.CONTROL_SCENARIO, None) == \
        (None, [v1world.SCENARIO_ID])
