"""The v2 store: its tables and columns, the split by family, the roster, the truth split, and the hash refusals
(docs/neural/spec.md §6.4, §15.2)."""

import json
import shutil

import duckdb
import pytest

from src.neural import devlib
from src.pipeline import evaluate, store

# Two variants of one family (train), one val, one test and the OOD scenario: a small library built in seconds.
INDICES = (0, 1, 7, 22, 45)

OBSERVED_COLUMNS = {
    "specimens": {"specimen_id", "run", "split", "specimen_type", "tests", "unit", "priority", "planned_route",
                  "policy_id", "window_min", "urgency_tier", "scenario_id", "family_id", "replicate", "role", "scored",
                  "unit_kind", "route_kind", "route_id"},
    "events": {"specimen_id", "seq", "activity", "timestamp", "location", "actor_role", "note"},
    "roster": {"scenario_id", "kind", "key", "attributes"},
    "runs": {"run", "scenario_id", "replicate", "role", "day0", "scored_start", "scored_end"},
}
TRUTH_COLUMNS = {
    "truth_specimens": {"specimen_id", "run", "split", "planted_fault", "collected_at", "clock_started", "elapsed_min",
                        "breach", *store.TARGET_KEYS, "outcome_received", "outcome_plated", "scenario_id", "replicate",
                        "cause", "disruption_ids", "outcome_class", "route_kind", "horizon_cut"},
    "truth_segments": {"specimen_id", "seq", "state", "start", "end", "location", "substate"},
    "truth_disruptions": {"scenario_id", "replicate", "run", "disruption_id", "type", "target_id", "start", "end",
                          "severity"},
    "truth_scenarios": {"scenario_id", "family_id", "split", "archetype", "stratum", "parameters"},
}
TRUTH_ONLY = {"planted_fault", "breach", "elapsed_min", "clock_started", "outcome_received", "outcome_plated", "cause",
              "disruption_ids", "outcome_class", "horizon_cut", "substate", "severity", *store.TARGET_KEYS}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("dev")
    out = devlib.build(d, indices=INDICES, workers=2)
    return d, out


def columns(con, table):
    return {r[0]: r[1] for r in con.execute(f"describe {table}").fetchall()}


def tables(con):
    return {r[0] for r in con.execute("show tables").fetchall()}


def test_every_listed_table_and_column_exists(built):
    d, _ = built
    obs, tru = store.open_observed_v2(d / "store"), store.open_truth(d / "store")
    for name, cols in OBSERVED_COLUMNS.items():
        assert cols <= set(columns(obs, name)), (name, cols - set(columns(obs, name)))
    for name, cols in TRUTH_COLUMNS.items():
        assert cols <= set(columns(tru, name)), (name, cols - set(columns(tru, name)))
    ts = columns(tru, "truth_specimens")
    assert all(ts[k] == "DOUBLE" for k in (*store.TARGET_KEYS, "elapsed_min"))


def test_counts_match_the_simulation_manifest_and_runs_are_keyed_by_the_specimen_id(built):
    d, out = built
    m, counts = out["manifest"], out["store"]
    assert counts["specimens"] == counts["truth_specimens"] == m["specimens"]
    obs = store.open_observed_v2(d / "store")
    runs = dict(obs.execute("select run, scenario_id from runs").fetchall())
    assert set(runs) == {e["run_key"] for e in m["runs"]}
    bad = obs.execute("""select count(*) from specimens s join runs r using (run)
                         where cast(substr(split_part(s.specimen_id, '-', 2), 2) as bigint) != s.run
                            or r.scenario_id != s.scenario_id or r.replicate != s.replicate""").fetchone()[0]
    assert bad == 0
    roles = obs.execute("select distinct replicate = 0, role from runs").fetchall()
    assert sorted(roles) == [(False, "scored"), (True, "history")]


def test_no_family_is_in_two_splits_and_the_ood_scenario_is_apart(built):
    d, out = built
    obs = store.open_observed_v2(d / "store")
    assert obs.execute("""select count(*) from (select family_id from specimens group by family_id
                          having count(distinct split) > 1)""").fetchone()[0] == 0
    splits = dict(obs.execute("select distinct scenario_id, split from specimens").fetchall())
    assert splits["dev-45-ood-scanner"] == "test_ood"
    assert set(splits.values()) == {"train", "val", "test", "test_ood"}


def test_a_split_that_puts_one_family_in_two_splits_is_refused(built, tmp_path):
    d, out = built
    doc = json.loads((d / devlib.SPLITS_NAME).read_text())
    doc["scenarios"]["dev-01-outage-hold"] = "val"   # its family-mate dev-00-example stays in train
    with pytest.raises(RuntimeError, match="more than one split|families in more than one split"):
        store.build_v2(d, d / devlib.LIBRARY_NAME, doc, tmp_path / "store", workers=1)


def test_the_roster_holds_no_rate_fault_or_disruption(built):
    d, _ = built
    obs = store.open_observed_v2(d / "store")
    assert set(columns(obs, "roster")) == OBSERVED_COLUMNS["roster"]
    kinds = {k for (k,) in obs.execute("select distinct kind from roster").fetchall()}
    assert kinds == set(store.ROSTER_KINDS)

    def keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k
                yield from keys(v)
        elif isinstance(node, list):
            for v in node:
                yield from keys(v)
    for (attrs,) in obs.execute("select attributes from roster").fetchall():
        for k in keys(json.loads(attrs)):
            assert not any(w in k for w in ("rate", "fault", "disruption", "severity", "lost", "misroute")), k


def test_the_truth_lives_only_in_the_truth_file(built):
    d, _ = built
    obs, tru = store.open_observed_v2(d / "store"), store.open_truth(d / "store")
    assert not tables(obs) & set(store.TRUTH_V2_TABLES)
    assert set(store.TRUTH_V2_TABLES) <= tables(tru)
    for name in tables(obs):
        assert not set(columns(obs, name)) & TRUTH_ONLY, name
    assert obs.execute("select count(*) from events where activity not in "
                       "('ordered', 'collected', 'received', 'rejected', 'accessioned', 'plated')").fetchone()[0] == 0


def test_the_store_records_its_inputs_and_outputs(built):
    d, _ = built
    m = store.store_v2_manifest(d / "store")
    lib = store.read_library(d / devlib.LIBRARY_NAME)
    assert m["inputs"]["library_sha256"] == lib[1] == store.read_library(lib[0])[1]
    assert set(m["outputs"]) == {store.OBSERVED_NAME, store.TRUTH_NAME}
    meta = dict(store.open_observed_v2(d / "store").execute("select key, value from meta").fetchall())
    assert meta["policy_hash"] == m["inputs"]["policy_hash"] and meta["library_sha256"] == lib[1]


def copy_sim(d, tmp_path):
    sim = tmp_path / "sim"
    shutil.copytree(d, sim, ignore=shutil.ignore_patterns("store"))
    return sim


def test_a_changed_replicate_file_is_refused(built, tmp_path):
    d, out = built
    sim = copy_sim(d, tmp_path)
    path = sim / next(f["path"] for f in out["manifest"]["files"] if f["path"].endswith("rep-1.jsonl.gz"))
    raw = bytearray(path.read_bytes())
    raw[-5] ^= 0xFF
    path.write_bytes(bytes(raw))
    with pytest.raises(RuntimeError, match="does not match the SHA-256"):
        store.build_v2(sim, sim / devlib.LIBRARY_NAME, sim / devlib.SPLITS_NAME, tmp_path / "store", workers=1)


def test_a_different_library_is_refused(built, tmp_path):
    d, out = built
    records = [dict(r) for r in out["records"]]
    records[0]["volume"] = dict(records[0]["volume"], weekend_factor=0.9)
    with pytest.raises(RuntimeError, match="library does not match"):
        store.build_v2(d, records, d / devlib.SPLITS_NAME, tmp_path / "store", workers=1)


def test_a_simulation_under_another_policy_is_refused(built, tmp_path):
    d, _ = built
    sim = copy_sim(d, tmp_path)
    m = json.loads((sim / "manifest.json").read_text())
    (sim / "manifest.json").write_text(json.dumps(m | {"policy_hash": "0" * 16}))
    with pytest.raises(RuntimeError, match="different policy"):
        store.build_v2(sim, sim / devlib.LIBRARY_NAME, sim / devlib.SPLITS_NAME, tmp_path / "store", workers=1)


def test_the_v2_accessors_read_labels_that_agree_with_the_scans(built):
    d, _ = built
    specs, counts = evaluate.load_specs_v2("train", store_dir=d / "store")
    assert counts["with_a_clock"] == len(specs) > 1000
    assert {s.replicate for s in specs} == {1, 2} and all(s.role == "scored" for s in specs)
    assert {s.outcome_class for s in specs} <= {"on_time", "late", "lost"}
    history, _ = evaluate.load_specs_v2("train", roles=("history",), store_dir=d / "store", labels=False)
    assert {s.replicate for s in history} == {0} and all(s.fault == "unknown" for s in history)
    late = next(s for s in specs if s.receipt_min and s.receipt_min > 40)
    places = evaluate.truth_places_v2({late.specimen_id: [5.0, late.receipt_min + 1]}, store_dir=d / "store")
    assert places[late.specimen_id][0] in set(evaluate.PLACE_OF.values()) and places[late.specimen_id][1] is None
    causes = evaluate.truth_causes_v2("train", store_dir=d / "store")
    assert causes[late.specimen_id]["outcome_class"] == late.outcome_class
    windows = evaluate.truth_disruptions_v2(late.scenario_id, late.replicate, store_dir=d / "store")
    assert windows and all(w["replicate"] == late.replicate for w in windows)
