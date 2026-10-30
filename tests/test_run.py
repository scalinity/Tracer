import json
import re
from types import SimpleNamespace

import pytest

from src.generator import briefs as briefs_mod
from src.generator import config, prompt, run, show, validate
from trails import render_trail


@pytest.fixture(scope="module")
def pool():
    return briefs_mod.make_briefs(0, 0, 600)


class FakeClient:
    """Answers each request with the correct trail, except for ids in `broken`, which get a bad one."""

    def __init__(self, pool, broken=(), always_broken=()):
        self.by_id = {b["specimen_id"]: b for b in pool}
        self.broken, self.always_broken = set(broken), set(always_broken)
        self.seen: dict[str, int] = {}
        self.creates, self.counts = [], []
        self.messages = SimpleNamespace(create=self.create, count_tokens=self.count_tokens)

    def create(self, **kwargs):
        self.creates.append(kwargs)
        sent = json.loads(kwargs["messages"][0]["content"])
        sid = sent["specimen_id"]
        self.seen[sid] = self.seen.get(sid, 0) + 1
        trail = render_trail(self.by_id[sid])
        if sid in self.always_broken or (sid in self.broken and self.seen[sid] == 1):
            trail["outcome"]["fault"] = "wrong"
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(trail))], stop_reason="end_turn", _request_id="req",
            usage=SimpleNamespace(input_tokens=300, output_tokens=600, cache_creation_input_tokens=0,
                                  cache_read_input_tokens=4000))

    def count_tokens(self, **kwargs):
        self.counts.append(kwargs)
        empty = json.loads(kwargs["messages"][0]["content"])["specimen_id"] == ""
        return SimpleNamespace(input_tokens=4000 if empty else 4240)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(prompt, "USAGE_LOG", tmp_path / "usage.jsonl")
    monkeypatch.setattr(validate, "DROPPED_LOG", tmp_path / "dropped.jsonl")
    return tmp_path


def use(monkeypatch, client):
    monkeypatch.setattr(prompt, "make_client", lambda: client)
    return client


def go(client, briefs, tmp_path, **kw):
    return run.generate_run(client, briefs, tmp_path / "run-00.jsonl", job="J1", log_path=tmp_path / "usage.jsonl",
                            dropped_path=tmp_path / "dropped.jsonl", **kw)


def test_accepted_specimens_are_saved_with_their_brief(pool, tmp_path):
    counts = go(FakeClient(pool), pool[:30], tmp_path)
    assert counts == {"requested": 30, "skipped": 0, "accepted": 30, "dropped": 0}
    rows = [json.loads(l) for l in (tmp_path / "run-00.jsonl").read_text().splitlines()]
    assert [r["brief"]["specimen_id"] for r in rows] == [b["specimen_id"] for b in pool[:30]]
    assert rows[0]["brief"]["label"]["window_min"] and rows[0]["trail"]["specimen_id"] == rows[0]["brief"]["specimen_id"]


def test_a_rerun_skips_what_is_saved_and_sends_nothing(pool, tmp_path):
    go(FakeClient(pool), pool[:10], tmp_path)
    client = FakeClient(pool)
    counts = go(client, pool[:15], tmp_path)
    assert counts == {"requested": 5, "skipped": 10, "accepted": 5, "dropped": 0}
    assert len(client.creates) == 5
    assert go(client, pool[:15], tmp_path)["requested"] == 0 and len(client.creates) == 5


def test_a_specimen_failing_twice_is_dropped_not_saved(pool, tmp_path):
    bad = pool[3]["specimen_id"]
    counts = go(FakeClient(pool, always_broken=[bad]), pool[:10], tmp_path)
    assert (counts["accepted"], counts["dropped"]) == (9, 1)
    assert bad not in run.saved_ids(tmp_path / "run-00.jsonl")
    assert json.loads((tmp_path / "dropped.jsonl").read_text())["specimen_id"] == bad


def test_summary_separates_first_attempt_from_after_retry(pool, tmp_path):
    client = FakeClient(pool, broken=[pool[1]["specimen_id"], pool[2]["specimen_id"]],
                        always_broken=[pool[4]["specimen_id"]])
    counts = go(client, pool[:20], tmp_path)
    s = run.summarize(counts, run.usage_rows(tmp_path / "usage.jsonl", 0, "J1"))
    assert (s["accepted"], s["dropped"], s["calls"]) == (19, 1, 23)  # 3 specimens needed a second call
    assert s["first_pass"] == 17 and s["first_pass_rate"] == pytest.approx(17 / 20)
    assert s["accepted_rate"] == pytest.approx(19 / 20)
    assert s["cost_usd"] == pytest.approx(23 * (300 * 0.10 + 4000 * 0.01 + 600 * 0.50) / 1e6)
    assert s["tokens"]["cache_read_input_tokens"] == 23 * 4000
    assert "first-attempt pass 17 of 20 (85.0%)" in run.format_summary(s)


def test_usage_rows_ignore_what_was_logged_before_and_other_jobs(pool, tmp_path):
    log = tmp_path / "usage.jsonl"
    go(FakeClient(pool), pool[:3], tmp_path)
    start = log.stat().st_size
    run.generate_run(FakeClient(pool), pool[3:5], tmp_path / "other.jsonl", job="J9", log_path=log,
                     dropped_path=tmp_path / "dropped.jsonl")
    go(FakeClient(pool), pool[5:9], tmp_path)
    assert len(run.usage_rows(log, start, "J1")) == 4
    assert len(run.usage_rows(log, 0, "J1")) == 7


def test_dry_run_counts_tokens_and_sends_no_trail_request(pool, paths, monkeypatch, capsys):
    client = use(monkeypatch, FakeClient(pool))
    assert run.main(["--n", "50", "--out", str(paths / "out"), "--dry-run"]) == 0
    assert "50 trails on claude-haiku-5-5" in capsys.readouterr().out
    assert client.counts and not client.creates and not (paths / "out").exists()


def test_a_real_run_needs_a_budget(pool, paths, monkeypatch):
    use(monkeypatch, FakeClient(pool))
    with pytest.raises(SystemExit):
        run.main(["--n", "5", "--out", str(paths / "out")])


def test_an_estimate_over_budget_stops_before_anything_is_sent(pool, paths, monkeypatch):
    client = use(monkeypatch, FakeClient(pool))
    with pytest.raises(prompt.BudgetError):
        run.main(["--n", "200", "--budget-usd", "0.01", "--out", str(paths / "out")])
    assert not client.creates


def test_a_full_run_writes_trails_and_a_summary_with_hashes(pool, paths, monkeypatch, capsys):
    client = use(monkeypatch, FakeClient(briefs_mod.make_briefs(0, 0, 25) + briefs_mod.make_briefs(0, 1, 25)))
    assert run.main(["--runs", "2", "--n", "25", "--budget-usd", "1", "--out", str(paths / "out")]) == 0
    assert run.saved_ids(paths / "out" / "run-00.jsonl") and run.saved_ids(paths / "out" / "run-01.jsonl")
    summary = json.loads((paths / "out" / "summary.json").read_text())
    loaded = config.load()
    assert (summary["accepted"], summary["dropped"], summary["runs"], summary["n_per_run"]) == (50, 0, 2, 25)
    assert summary["policy_hash"] == loaded["policy_hash"] and summary["config_hash"] == loaded["config_hash"]
    assert summary["prompt_hash"] == prompt.prompt_hash() and summary["model"] == "claude-haiku-5-5"
    assert summary["cost_usd"] > 0 and "real cost" in capsys.readouterr().out


def test_the_prompt_hash_follows_the_wording(monkeypatch):
    before = prompt.prompt_hash()
    assert before == prompt.prompt_hash()
    monkeypatch.setattr(prompt, "INSTRUCTIONS", prompt.INSTRUCTIONS + " Be brief.")
    assert prompt.prompt_hash() != before


# ---- the review sheet ----

@pytest.fixture(scope="module")
def records(pool):
    return [{"brief": b, "trail": render_trail(b)} for b in pool]


def test_the_sheet_has_the_requested_entries_and_a_third_are_failures(records):
    text = show.sheet(records, 20)
    assert text.count("\n## ") == 20 and text.count("[ ] Plausible") == 20
    assert text.count("What went wrong") == 6
    assert "At least 17 of the 20 plausible counts as a pass" in text


def test_the_sheet_is_deterministic(records):
    assert show.sheet(records, 20) == show.sheet(list(records), 20)


def test_the_sheet_shows_every_kind_of_failure_present(records):
    kinds = {r["brief"]["planted_fault"] for r in show.sample(records, 20)} - {"none"}
    assert len(kinds) >= 5


def test_the_sheet_is_plain_language(records):
    text = show.sheet(records, 20)
    for leftover in ("{", "}", "python", "stalled_at_station", "tube_station", "planted_fault", "claude", "usage.jsonl"):
        assert leftover not in text.lower(), leftover
    assert "made-up" in text and "no data from any workplace" in text
    assert re.search(r"- \d{1,2}:\d{2} (AM|PM): collected", text)


def test_the_sheet_states_each_verdict_against_the_window(records):
    text = show.sheet(records, 20)
    assert "made it in time" in text and "missed the window" in text and "never reached the lab" in text


def test_durations_read_naturally():
    assert [show.span(m) for m in (7, 119, 120, 121, 1440)] == ["7 min", "119 min", "2 h", "2 h 1 min", "24 h"]


def test_too_few_specimens_is_an_error(tmp_path):
    (tmp_path / "run-00.jsonl").write_text("")
    with pytest.raises(SystemExit, match="only 0 accepted"):
        show.main(["--n", "20", "--pilot", str(tmp_path)])


def test_main_writes_the_sheet_beside_the_trails(records, tmp_path, capsys):
    (tmp_path / "run-00.jsonl").write_text("\n".join(json.dumps(r) for r in records[:300]) + "\n")
    assert show.main(["--n", "20", "--pilot", str(tmp_path)]) == 0
    assert (tmp_path / "review-sheet.md").read_text().startswith("# 20 specimen timelines to review")
