"""The spend ledger and the price table (docs/neural/spec.md §4.6, §15.2): no sequence of operations can push spend
plus holds past a cap, racing threads never overspend, the mirror replays to the database, and stale or incomplete
prices refuse. Everything runs on temporary files; nothing touches the network."""

import json
import logging
import random
import sqlite3
import threading
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from src.scenarios import ledger as L
from src.scenarios import prices

ROOT = Path(__file__).resolve().parents[1]
REAL_JOBS = yaml.safe_load((ROOT / "config" / "openai_jobs.yaml").read_text())
EST = timezone(timedelta(hours=-5))
NOW = datetime(2026, 11, 9, 10, 0, tzinfo=EST)

SMALL = {"accounts": {"openai": {"cap_usd": 1.00, "margin_usd": 0.10}, "decide": {"cap_usd": 0.50, "margin_usd": 0}},
         "jobs": {"A": {"account": "openai", "cap_usd": 0.60, "released": True},
                  "B": {"account": "openai", "cap_usd": 0.50, "released": True},
                  "C": {"account": "openai", "cap_usd": 0.30, "released": False},
                  "X": {"account": "decide", "cap_usd": 0.40, "released": True}}}


def open_ledger(tmp_path, jobs=SMALL, create=True, **kw):
    return L.Ledger(tmp_path / "ledger.sqlite", jobs=jobs, create=create, **kw)


def m(usd) -> int:
    return L.to_micros(usd)


def check_caps(lg, jobs=SMALL):
    t = lg.totals()
    for account, a in t["accounts"].items():
        assert a["spent"] + a["held"] <= a["reservable"], (account, a)
    for job, j in t["jobs"].items():
        assert j["spent"] + j["held"] <= j["cap"], (job, j)
    assert L.replay(lg.mirror) == lg.balances()


# -- caps and the real configuration ---------------------------------------------------------------------------------

def test_the_real_caps_match_the_spec():
    jobs = REAL_JOBS["jobs"]
    openai = REAL_JOBS["accounts"]["openai"]
    assert (openai["cap_usd"], openai["margin_usd"]) == (76.43, 2.43)
    assert REAL_JOBS["accounts"]["decide"] == {"cap_usd": 8.00, "margin_usd": 0.00}
    caps = {j: v["cap_usd"] for j, v in jobs.items()}
    assert caps == {"O1": 3.00, "O1b": 2.00, "O2": 44.00, "O3": 18.00, "O3b": 7.00, "X1": 1.00, "X2": 4.50,
                    "X3": 0.75, "X4": 1.75}
    assert {j for j, v in jobs.items() if not v["released"]} == {"O1b", "O3b"}
    openai_jobs = sum(m(v["cap_usd"]) for v in jobs.values() if v["account"] == "openai")
    assert openai_jobs == m(openai["cap_usd"]) - m(openai["margin_usd"]) == m("74.00")
    assert sum(m(v["cap_usd"]) for v in jobs.values() if v["account"] == "decide") == m("8.00")


def test_the_real_ledger_never_lets_more_than_74_dollars_be_held(tmp_path):
    lg = open_ledger(tmp_path, jobs=REAL_JOBS)
    lg.release_job("O1b", "test")
    lg.release_job("O3b", "test")
    for job, cap in (("O1", "3"), ("O1b", "2"), ("O2", "44"), ("O3", "18"), ("O3b", "7")):
        lg.reserve(job, f"{job}-all", cap)
    with pytest.raises(L.CapReached) as e:
        lg.reserve("O2", "one-more", "0.000001")
    assert e.value.scope in ("account", "job")
    assert lg.totals()["accounts"]["openai"]["held"] == m("74.00")
    lg.reserve("X2", "decide-still-has-room", "4.50")  # the decision account is separate


def test_a_reservation_is_refused_past_the_job_cap_and_past_the_account_cap(tmp_path):
    lg = open_ledger(tmp_path)
    lg.reserve("A", "a1", "0.60")
    with pytest.raises(L.CapReached) as e:
        lg.reserve("A", "a2", "0.000001")
    assert e.value.scope == "job"
    lg.reserve("B", "b1", "0.30")  # 0.90 of the account's 0.90 reservable
    with pytest.raises(L.CapReached) as e:
        lg.reserve("B", "b2", "0.01")
    assert e.value.scope == "account"
    check_caps(lg)


def test_the_run_budget_is_a_ceiling_inside_the_job_cap(tmp_path):
    lg = open_ledger(tmp_path)
    lg.reserve("A", "a1", "0.20", ceiling_usd="0.25")
    with pytest.raises(L.CapReached) as e:
        lg.reserve("A", "a2", "0.10", ceiling_usd="0.25")
    assert e.value.scope == "budget"


def test_an_unreleased_job_takes_nothing_until_released_with_a_reason(tmp_path):
    lg = open_ledger(tmp_path)
    with pytest.raises(L.CapReached) as e:
        lg.reserve("C", "c1", "0.01")
    assert e.value.scope == "released"
    with pytest.raises(L.LedgerError):
        lg.release_job("C", "  ")
    assert lg.release_job("C", "pilot gate failed once; prompt revised")
    assert not lg.release_job("C", "again")
    lg.reserve("C", "c1", "0.01")
    lg.close()
    lines = [json.loads(x) for x in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert any(x.get("job_release") == "C" and "prompt revised" in x["reason"] for x in lines)
    open_ledger(tmp_path, create=False).reserve("C", "c2", "0.01")  # stays released across opens


def test_settle_records_the_cost_and_releases_the_rest(tmp_path):
    lg = open_ledger(tmp_path)
    rid = lg.reserve("A", "a1", "0.05", detail={"model": "gpt-6.1-terra", "tier": "flex"})
    usage = {"input_tokens": 7000, "input_tokens_details": {"cached_tokens": 1000, "cache_write_tokens": 0},
             "output_tokens": 4000, "output_tokens_details": {"reasoning_tokens": 1500}}
    s = lg.settle(rid, "0.0135", usage, "req_1", "flex")
    assert s == {"micros": 13500, "reserved": 50000, "released": 36500, "over": False}
    kinds = [e["kind"] for e in lg.entries()]
    assert kinds == ["reserve", "settle", "release"]
    assert lg.totals()["jobs"]["A"]["spent"] == 13500 and lg.totals()["jobs"]["A"]["held"] == 0
    with pytest.raises(L.LedgerError):
        lg.settle(rid, "0.01", usage, "req_1", "flex")
    with pytest.raises(L.LedgerError):
        lg.void(rid, "too late")
    row = lg.report()["rows"][0]
    assert (row["job"], row["model"], row["tier"], row["settled"], row["requests"]) == ("A", "gpt-6.1-terra", "flex",
                                                                                        13500, 1)
    assert row["tokens"] == {"input": 7000, "cached": 1000, "cache_write": 0, "output": 4000, "reasoning": 1500}


def test_void_spends_nothing_and_settle_unknown_keeps_the_whole_reservation(tmp_path):
    lg = open_ledger(tmp_path)
    r1, r2 = lg.reserve("A", "a1", "0.05"), lg.reserve("A", "a2", "0.07")
    assert lg.void(r1, "flex 429 resource unavailable") == 50000
    assert lg.settle_unknown(r2, "timeout") == 70000
    t = lg.totals()["jobs"]["A"]
    assert (t["spent"], t["held"]) == (70000, 0)
    unknown = [e for e in lg.entries() if e["kind"] == "settle"][0]
    assert unknown["detail"]["unknown"] is True and unknown["micros"] == unknown["detail"]["reserved"]
    assert lg.report()["rows"][0]["unknown"] == 1


def test_a_settlement_above_its_reservation_is_recorded_as_it_is_and_flagged(tmp_path, caplog):
    lg = open_ledger(tmp_path)
    rid = lg.reserve("A", "a1", "0.01")
    with caplog.at_level(logging.ERROR, logger="src.scenarios.ledger"):
        s = lg.settle(rid, "0.03", {"input_tokens": 1, "output_tokens": 1}, None, "flex")
    assert s["over"] is True and s["micros"] == 30000 and s["released"] == 0
    assert any("above its" in r.getMessage() for r in caplog.records)
    assert lg.totals()["jobs"]["A"]["spent"] == 30000
    assert lg.report()["rows"][0]["over_reservation"] == 1


def test_reserve_many_is_all_or_nothing(tmp_path):
    lg = open_ledger(tmp_path)
    with pytest.raises(L.CapReached):
        lg.reserve_many("A", [(f"a{i}", "0.10", None) for i in range(7)])  # 0.70 > 0.60
    assert lg.entries() == []
    ids = lg.reserve_many("A", [(f"a{i}", "0.10", None) for i in range(6)])
    assert len(ids) == 6 and lg.totals()["jobs"]["A"]["held"] == m("0.60")


def test_bad_amounts_are_refused(tmp_path):
    lg = open_ledger(tmp_path)
    for bad in ("0", "-0.01", "nan", "inf", True):
        with pytest.raises(L.LedgerError):
            lg.reserve("A", "a", bad)
    with pytest.raises(L.LedgerError):
        lg.reserve("nope", "a", "0.01")


def test_a_cap_change_is_refused_once_a_job_has_entries(tmp_path):
    lg = open_ledger(tmp_path)
    lg.reserve("A", "a1", "0.01")
    lg.close()
    changed = deepcopy(SMALL)
    changed["jobs"]["A"]["cap_usd"] = 0.70
    with pytest.raises(L.LedgerError, match="cannot change"):
        open_ledger(tmp_path, jobs=changed, create=False)
    changed = deepcopy(SMALL)
    changed["jobs"]["B"]["cap_usd"] = 0.40  # B has no entries yet
    open_ledger(tmp_path, jobs=changed, create=False).reserve("B", "b1", "0.40")
    changed["accounts"]["openai"]["margin_usd"] = 0.05
    with pytest.raises(L.LedgerError, match="cannot change"):
        open_ledger(tmp_path, jobs=changed, create=False)


# -- missing, corrupt, locked ----------------------------------------------------------------------------------------

def test_a_missing_ledger_stops_everything(tmp_path):
    with pytest.raises(L.LedgerUnavailable, match="dry run"):
        open_ledger(tmp_path, create=False)
    lg = open_ledger(tmp_path)
    lg.reserve("A", "a1", "0.01")
    lg.close()
    (tmp_path / "ledger.sqlite").unlink()
    for suffix in ("-wal", "-shm"):
        (tmp_path / f"ledger.sqlite{suffix}").unlink(missing_ok=True)
    with pytest.raises(L.LedgerUnavailable, match="missing"):
        open_ledger(tmp_path, create=True)  # never silently recreated over a mirror with entries


def test_a_corrupt_database_stops_everything(tmp_path):
    lg = open_ledger(tmp_path)
    lg.reserve("A", "a1", "0.01")
    lg.close()
    for suffix in ("-wal", "-shm"):
        (tmp_path / f"ledger.sqlite{suffix}").unlink(missing_ok=True)
    (tmp_path / "ledger.sqlite").write_bytes(b"this is not a database" * 100)
    with pytest.raises(L.LedgerUnavailable):
        open_ledger(tmp_path, create=False)


def test_a_mirror_that_disagrees_stops_everything(tmp_path):
    lg = open_ledger(tmp_path)
    lg.reserve("A", "a1", "0.01")
    lg.reserve("A", "a2", "0.02")
    lg.close()
    mirror = tmp_path / "ledger.jsonl"
    lines = mirror.read_text().splitlines()
    first = json.loads(lines[0])
    first["micros"] = 1
    mirror.write_text("\n".join([json.dumps(first)] + lines[1:]) + "\n")
    with pytest.raises(L.LedgerUnavailable, match="disagrees"):
        open_ledger(tmp_path, create=False)
    mirror.write_text("not json\n")
    with pytest.raises(L.LedgerUnavailable):
        open_ledger(tmp_path, create=False)
    mirror.write_text("")
    with pytest.raises(L.LedgerUnavailable, match="missing or empty"):
        open_ledger(tmp_path, create=False)


def test_a_replay_that_does_not_reproduce_the_database_stops_everything(tmp_path, monkeypatch):
    lg = open_ledger(tmp_path)
    lg.reserve("A", "a1", "0.01")
    lg.close()
    monkeypatch.setattr(L, "replay", lambda path: {"accounts": {}, "jobs": {}})
    with pytest.raises(L.LedgerUnavailable, match="does not reproduce"):
        open_ledger(tmp_path, create=False)


def test_a_locked_ledger_stops_everything(tmp_path):
    open_ledger(tmp_path).close()
    holder = sqlite3.connect(tmp_path / "ledger.sqlite", isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        lg = None
        with pytest.raises(L.LedgerUnavailable, match="locked"):
            lg = open_ledger(tmp_path, create=False, busy_timeout_s=0.2)
            lg.reserve("A", "a1", "0.01")
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    lg = open_ledger(tmp_path, create=False)
    assert lg.entries() == []


def test_a_crash_between_commit_and_mirror_write_is_repaired_on_open(tmp_path, monkeypatch):
    lg = open_ledger(tmp_path)
    lg.reserve("A", "a1", "0.01")

    def boom(self, lines):
        raise L.LedgerUnavailable("disk went away")
    monkeypatch.setattr(L.Ledger, "_append_mirror", boom)
    with pytest.raises(L.LedgerUnavailable):
        lg.reserve("A", "a2", "0.02")  # committed, mirror write failed
    monkeypatch.undo()
    lg.close()
    lg = open_ledger(tmp_path, create=False)
    assert len(lg.entries()) == 2
    assert L.replay(tmp_path / "ledger.jsonl") == lg.balances()
    assert lg.totals()["jobs"]["A"]["held"] == m("0.03")


def test_an_old_entry_missing_from_the_mirror_is_corruption_not_a_crash_tail(tmp_path):
    lg = open_ledger(tmp_path)
    for i in range(3):
        lg.reserve("A", f"a{i}", "0.01")
    lg.close()
    mirror = tmp_path / "ledger.jsonl"
    lines = mirror.read_text().splitlines()
    mirror.write_text("\n".join(lines[:1] + lines[2:]) + "\n")
    with pytest.raises(L.LedgerUnavailable, match="crash tail"):
        open_ledger(tmp_path, create=False)


# -- randomized sequences, crashes and races -------------------------------------------------------------------------

@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_no_sequence_of_operations_passes_a_cap(tmp_path, seed):
    """Reserve, settle (at or under the reservation), void, timeout, batch chunks with expiry, adjust, and crashes
    (the process dies between reserve and settle). Spend plus holds never passes the account's reservable amount or
    any job's cap, and the mirror always replays to the database."""
    rnd = random.Random(seed)
    lg = open_ledger(tmp_path)
    lg.release_job("C", "randomized test")
    open_res: list[str] = []
    refused = 0
    for step in range(260):
        op = rnd.choices(["reserve", "settle", "void", "timeout", "batch", "crash", "adjust"],
                         weights=[34, 22, 10, 8, 8, 6, 2])[0]
        if op == "reserve":
            job = rnd.choice("ABCX")
            try:
                open_res.append(lg.reserve(job, f"{job}-{step}", Decimal(rnd.randint(1, 90_000)) / 1_000_000))
            except L.CapReached:
                refused += 1
        elif op in ("settle", "void", "timeout") and open_res:
            rid = open_res.pop(rnd.randrange(len(open_res)))
            reserved = next(e for e in lg.entries() if e["entry_id"] == rid)["micros"]
            if op == "settle":
                lg.settle(rid, Decimal(rnd.randint(0, reserved)) / 1_000_000, {"input_tokens": 1, "output_tokens": 1},
                          f"req-{step}", "flex")
            elif op == "void":
                lg.void(rid, "flex 429")
            else:
                lg.settle_unknown(rid, "timeout")
        elif op == "batch":
            job = rnd.choice("AB")
            n = rnd.randint(2, 6)
            try:
                chunk = lg.reserve_many(job, [(f"{job}-{step}-{i}", Decimal(rnd.randint(1, 30_000)) / 1_000_000,
                                               {"tier": "batch"}) for i in range(n)])
            except L.CapReached:
                refused += 1
                chunk = []
            done = rnd.randint(0, len(chunk))  # some lines completed, the rest expired with the batch
            for rid in chunk[:done]:
                lg.settle(rid, "0.000500", {"input_tokens": 10, "output_tokens": 10}, None, "batch")
            for rid in chunk[done:]:
                lg.void(rid, "batch_expired")
        elif op == "crash":
            lg.close()  # dies without settling: every open reservation must still be held after reopening
            lg = open_ledger(tmp_path, create=False)
        elif op == "adjust":
            lg.adjust(rnd.choice("AB"), "-0.000100", "reconciliation")
        check_caps(lg)
    assert refused > 0, "the sequence never reached a cap, so it tested nothing"
    lg.close()
    lg = open_ledger(tmp_path, create=False)
    check_caps(lg)
    assert {e["entry_id"] for e in lg.open_reservations()} == set(open_res)


def test_sixteen_threads_racing_for_the_last_dollars_never_overspend(tmp_path):
    """Sixteen threads, each with its own connection, reserve one cent at a time until refused."""
    open_ledger(tmp_path).close()
    granted = []
    errors = []
    start = threading.Barrier(16)

    def worker(n):
        lg = open_ledger(tmp_path, create=False)
        start.wait()
        try:
            while True:
                try:
                    granted.append(lg.reserve(rnd_job(n), f"t{n}-{len(granted)}", "0.01"))
                except L.CapReached:
                    return
        except Exception as e:  # noqa: BLE001
            errors.append(e)
        finally:
            lg.close()

    def rnd_job(n):
        return "AB"[n % 2]

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    lg = open_ledger(tmp_path, create=False)
    t = lg.totals()
    assert t["accounts"]["openai"]["held"] == m("0.90")  # every last cent, and not one more
    assert t["jobs"]["A"]["held"] <= m("0.60") and t["jobs"]["B"]["held"] <= m("0.50")
    assert len(granted) == 90
    assert L.replay(lg.mirror) == lg.balances()


def test_sixteen_threads_sharing_one_ledger_never_overspend_a_job(tmp_path):
    lg = open_ledger(tmp_path)
    granted = []

    def worker(n):
        while True:
            try:
                rid = lg.reserve("A", f"t{n}-{len(granted)}", "0.007")
            except L.CapReached:
                return
            granted.append(rid)
            if n % 3 == 0:
                lg.settle(rid, "0.003", None, None, "flex")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    t = lg.totals()["jobs"]["A"]
    assert t["spent"] + t["held"] <= m("0.60")
    assert m("0.60") - (t["spent"] + t["held"]) < m("0.007")
    assert L.replay(lg.mirror) == lg.balances()


# -- prices ----------------------------------------------------------------------------------------------------------

def price_table(**changes):
    table = yaml.safe_load((ROOT / "config" / "openai_prices.yaml").read_text())
    table["verified_at"] = (NOW - timedelta(hours=1)).isoformat()
    table.update(changes)
    return table


def test_the_committed_price_table_has_every_model_and_tier_the_jobs_use():
    table = prices.load()
    for arm in REAL_JOBS["arms"].values():
        for tier in ("flex", "batch", "standard"):
            r = prices.rates(table, arm["model"], tier)
            assert set(prices.RATE_KEYS) <= set(r)
        assert arm["effort"] in table["models"][arm["model"]]["reasoning_efforts"]


def test_stale_prices_refuse():
    table = price_table(verified_at=(NOW - timedelta(hours=24, minutes=1)).isoformat())
    with pytest.raises(prices.PriceError, match="re-verify"):
        prices.require(table, [("gpt-6.1-terra", "flex")], NOW)
    prices.require(price_table(verified_at=(NOW - timedelta(hours=23)).isoformat()), [("gpt-6.1-terra", "flex")], NOW)


@pytest.mark.parametrize("bad", ["", "yesterday", "2026-11-09T09:00:00"])
def test_a_verified_at_that_is_missing_unparseable_or_without_a_zone_refuses(bad):
    with pytest.raises(prices.PriceError):
        prices.check_fresh(price_table(verified_at=bad), NOW)


def test_a_verified_at_in_the_future_refuses():
    with pytest.raises(prices.PriceError, match="future"):
        prices.check_fresh(price_table(verified_at=(NOW + timedelta(hours=2)).isoformat()), NOW)


def test_a_verified_at_more_than_five_minutes_ahead_refuses_and_clock_skew_under_it_passes():
    """F011: five minutes of clock skew is allowed, no more."""
    with pytest.raises(prices.PriceError, match="future"):
        prices.check_fresh(price_table(verified_at=(NOW + timedelta(minutes=6)).isoformat()), NOW)
    prices.check_fresh(price_table(verified_at=(NOW + timedelta(minutes=4)).isoformat()), NOW)


def test_incomplete_prices_refuse():
    table = price_table()
    with pytest.raises(prices.PriceError, match="no model"):
        prices.require(table, [("gpt-6-astra", "flex")], NOW)
    del table["models"]["gpt-6.1-sol"]["flex"]
    with pytest.raises(prices.PriceError, match="no 'flex' tier"):
        prices.require(table, [("gpt-6.1-sol", "flex")], NOW)
    table = price_table()
    del table["models"]["gpt-6-luna"]["batch"]["cache_write"]
    with pytest.raises(prices.PriceError, match="cache_write"):
        prices.rates(table, "gpt-6-luna", "batch")
    table = price_table()
    table["models"]["gpt-6-luna"]["batch"]["output"] = -1
    with pytest.raises(prices.PriceError):
        prices.rates(table, "gpt-6-luna", "batch")
    with pytest.raises(prices.PriceError):
        prices.load(Path("/nonexistent/prices.yaml"))


def test_usage_is_priced_per_appendix_b():
    r = prices.rates(price_table(), "gpt-6.1-terra", "flex")  # 0.50 in, 0.025 cached, 0.625 write, 2.50 out
    with_writes = {"input_tokens": 12000, "input_tokens_details": {"cached_tokens": 4000, "cache_write_tokens": 7000,
                                                                  "cache_write_tokens_details": {"ttl_30m_tokens": 7000,
                                                                                                 "ttl_12h_tokens": 0}},
                   "output_tokens": 4000, "output_tokens_details": {"reasoning_tokens": 1500}}
    # 1,000 ordinary x 0.50 + 4,000 cached x 0.025 + 7,000 written x 0.625 + 4,000 output (reasoning inside) x 2.50
    assert prices.usage_cost_micros(with_writes, r) == 500 + 100 + 4375 + 10000
    without_writes = {"input_tokens": 12000, "input_tokens_details": {"cached_tokens": 4000}, "output_tokens": 4000}
    # writes not broken out: all 8,000 uncached tokens at the cache-write rate
    assert prices.usage_cost_micros(without_writes, r) == 8000 * 0.625 + 100 + 10000
    twelve = deepcopy(with_writes)
    twelve["input_tokens_details"]["cache_write_tokens_details"] = {"ttl_30m_tokens": 3000, "ttl_12h_tokens": 4000}
    assert prices.usage_cost_micros(twelve, r) == 500 + 100 + 3000 * 0.625 + 4000 * 1.25 + 10000
    luna = prices.rates(price_table(), "gpt-6-luna", "flex")
    assert prices.usage_cost_micros({"input_tokens": 3, "output_tokens": 0}, luna) == 1  # rounded up, never down
    for broken in (None, {}, {"input_tokens": 5, "output_tokens": -1},
                   {"input_tokens": 5, "input_tokens_details": {"cached_tokens": 9}, "output_tokens": 1}):
        with pytest.raises(prices.PriceError):
            prices.usage_cost_micros(broken, r)


def test_no_usage_within_the_input_bound_and_the_output_limit_at_any_tier_costs_more_than_the_worst_case():
    """F001, F005: the reservation is the byte bound at the highest input rate plus max_output_tokens at the output
    rate, all at Standard; no usage up to those counts, served at Flex, Batch or Standard, costs more."""
    rnd = random.Random(7)
    table = price_table()
    for _ in range(3000):
        model = rnd.choice(["gpt-6-luna", "gpt-6.1-terra", "gpt-6.1-sol"])
        served = prices.rates(table, model, rnd.choice(["flex", "batch", "standard"]))
        bound, max_out = rnd.randint(100, 60000), rnd.randint(16, 30000)
        inp = rnd.randint(0, bound)
        cached = rnd.randint(0, inp)
        writes = rnd.randint(0, inp - cached)
        out = rnd.randint(0, max_out)
        usage = {"input_tokens": inp, "output_tokens": out,
                 "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": writes}}
        if rnd.random() < 0.3:
            del usage["input_tokens_details"]["cache_write_tokens"]
        worst = prices.worst_case_micros(bound, max_out, prices.rates(table, model, prices.RESERVE_TIER))
        assert prices.usage_cost_micros(usage, served) <= worst


def test_the_worst_case_is_the_bound_at_the_highest_input_rate_plus_the_output_limit():
    table = price_table()
    terra = prices.rates(table, "gpt-6.1-terra", "standard")
    assert prices.RESERVE_TIER == "standard"
    # 8,000 bound x 1.25 cache-write + 16,000 x 5.00 output = 10,000 + 80,000 micro-dollars
    assert prices.worst_case_micros(8000, 16000, terra) == 90000
    # 12-hour retention, when a request asks for it, writes at 2.50
    assert prices.worst_case_micros(8000, 16000, terra, ttl_12h=True) == 100000
    no_12h = prices.rates(price_table(), "gpt-6.1-terra", "standard")
    del no_12h["cache_write_12h"]
    with pytest.raises(prices.PriceError, match="12-hour"):
        prices.worst_case_micros(8000, 16000, no_12h, ttl_12h=True)
    with pytest.raises(prices.PriceError, match="272"):  # long-context rates are not in the table
        prices.worst_case_micros(272_001, 16000, terra)
    assert prices.served_tier("default") == "standard" and prices.served_tier("flex") == "flex"
    assert prices.served_tier("priority") == "fast" and prices.served_tier(None) is None


# -- reservations topped up before a send (F004) --------------------------------------------------------------------

def test_cover_keeps_a_large_enough_reservation_and_replaces_a_smaller_one_under_the_caps(tmp_path):
    lg = open_ledger(tmp_path)
    rid = lg.reserve("A", "a1", "0.040124", detail={"tier": "flex"})
    assert lg.cover(rid, "0.040124") == rid and lg.cover(rid, "0.01") == rid  # enough: unchanged
    new = lg.cover(rid, "0.080124", detail={"tier": "flex"})
    assert new != rid
    kinds = [(e["kind"], e["ref"], e["micros"]) for e in lg.entries()]
    assert kinds == [("reserve", None, 40124), ("void", rid, 40124), ("reserve", None, 80124)]
    assert lg.totals()["jobs"]["A"]["held"] == 80124
    s = lg.settle(new, "0.08", None, None, "standard")
    assert s["over"] is False and lg.totals()["jobs"]["A"]["held"] == 0
    check_caps(lg)


def test_cover_refuses_a_top_up_that_does_not_fit_and_leaves_the_old_reservation_open(tmp_path):
    lg = open_ledger(tmp_path)
    lg.reserve("A", "filler", "0.55")
    rid = lg.reserve("A", "a1", "0.04")  # 0.59 of A's 0.60
    with pytest.raises(L.CapReached) as e:
        lg.cover(rid, "0.06")
    assert e.value.scope == "job"
    assert [r["entry_id"] for r in lg.open_reservations()][-1] == rid and lg.totals()["jobs"]["A"]["held"] == m("0.59")
    assert lg.cover(rid, "0.05") != rid  # 0.55 + 0.05 fits once the old 0.04 is freed
    with pytest.raises(L.CapReached) as e:
        lg.cover(lg.open_reservations()[-1]["entry_id"], "0.20", ceiling_usd="0.61")
    assert e.value.scope == "job"
    check_caps(lg)
    lg.void(lg.open_reservations()[-1]["entry_id"], "done")
    with pytest.raises(L.LedgerError, match="closed"):
        lg.cover(rid, "0.07")
