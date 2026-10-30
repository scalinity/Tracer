import gzip
import json
import shutil

import duckdb
import pytest

from src.pipeline import store

TRUTH_ONLY = {"planted_fault", "breach", "elapsed_min", "clock_started", "outcome_received", "outcome_plated",
              *store.TARGET_KEYS}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("store")
    return d, store.build(store_dir=d)


@pytest.fixture(scope="module")
def manifest():
    return json.loads(store.MANIFEST_PATH.read_text())


@pytest.fixture(scope="module")
def records():
    return [json.loads(l) for l in gzip.decompress(store.DATASET_PATH.read_bytes()).decode().splitlines()]


def rows(con, sql):
    return con.sql(sql).fetchall()


def test_row_counts_match_the_manifest_and_the_dataset(built, manifest, records):
    d, counts = built
    assert counts["specimens"] == counts["truth_specimens"] == manifest["accepted"] == len(records)
    assert counts["events"] == sum(len(r["trail"]["observed_events"]) for r in records)
    assert counts["truth_segments"] == sum(len(r["trail"]["truth_segments"]) for r in records)
    o, t = store.open_observed(d), store.open_truth(d)
    assert rows(o, "select count(*) from specimens") == [(manifest["accepted"],)]
    assert rows(o, "select count(*) from events") == [(counts["events"],)]
    assert rows(t, "select count(*) from truth_segments") == [(counts["truth_segments"],)]
    assert rows(t, "select sum(breach::int) from truth_specimens") == [(manifest["breach_accepted"],)]


def test_the_split_is_by_run_and_never_by_specimen(built):
    o = store.open_observed(built[0])
    assert rows(o, "select split, min(run), max(run), count(distinct run) from specimens group by 1 order by 1") == \
        [("fit", 0, 23, 24), ("test", 24, 29, 6)]
    assert rows(o, "select count(*) from (select run from specimens group by run having count(distinct split) > 1)") == [(0,)]
    t = store.open_truth(built[0])
    assert rows(t, "select split, count(*) from truth_specimens group by 1 order by 1") == \
        rows(o, "select split, count(*) from specimens group by 1 order by 1")


def test_run_and_split_helpers():
    assert store.run_of("SP-r07-000123") == 7 and store.run_of("SP-r29-000400") == 29
    assert [store.split_of(r) for r in (0, 23, 24, 29)] == ["fit", "fit", "test", "test"]


def test_the_observed_file_holds_no_truth(built):
    o = store.open_observed(built[0])
    assert {r[0] for r in rows(o, "select table_name from information_schema.tables")} == {"specimens", "events"}
    columns = {r[0] for r in rows(o, "select column_name from information_schema.columns")}
    assert not columns & TRUTH_ONLY and not any("truth" in c for c in columns)
    assert {"specimen_id", "split", "specimen_type", "activity", "timestamp"} <= columns


def test_the_truth_file_holds_only_truth_tables(built):
    t = store.open_truth(built[0])
    assert {r[0] for r in rows(t, "select table_name from information_schema.tables")} == \
        {"truth_specimens", "truth_segments"}
    columns = {r[0] for r in rows(t, "select column_name from information_schema.columns")}
    assert TRUTH_ONLY <= columns


def test_the_observed_file_cannot_be_written_through_the_model_handle(built):
    with pytest.raises(duckdb.Error):
        store.open_observed(built[0]).execute("create table oops (x int)")
    with pytest.raises(duckdb.Error):
        store.open_truth(built[0]).execute("delete from truth_specimens")


def test_a_never_collected_specimen_has_only_an_order_to_observe(built):
    t, o = store.open_truth(built[0]), store.open_observed(built[0])
    ids = [r[0] for r in rows(t, "select specimen_id from truth_specimens where planted_fault = 'never_collected'")]
    assert ids
    for sid in ids[:10]:
        assert rows(o, f"select activity from events where specimen_id = '{sid}'") == [("ordered",)]


def test_a_changed_dataset_is_refused(tmp_path, manifest):
    bad = tmp_path / "d.jsonl.gz"
    shutil.copy(store.DATASET_PATH, bad)
    with bad.open("ab") as f:
        f.write(b"x")
    with pytest.raises(RuntimeError, match="SHA-256"):
        store.build(bad, store.MANIFEST_PATH, tmp_path / "s")


@pytest.mark.parametrize("key, value, text", [("policy_hash", "0" * 16, "different policy"),
                                              ("accepted", 11966, "manifest says 11966")])
def test_a_manifest_that_disagrees_is_refused(tmp_path, manifest, key, value, text):
    m = tmp_path / "manifest.json"
    m.write_text(json.dumps(manifest | {key: value}))
    with pytest.raises(RuntimeError, match=text):
        store.build(store.DATASET_PATH, m, tmp_path / "s")
    assert not (tmp_path / "s" / store.OBSERVED_NAME).exists()


def test_building_twice_gives_the_same_tables(tmp_path):
    store.build(store_dir=tmp_path / "a")
    store.build(store_dir=tmp_path / "b")
    q = "select * from events order by specimen_id, seq"
    assert rows(store.open_observed(tmp_path / "a"), q) == rows(store.open_observed(tmp_path / "b"), q)
