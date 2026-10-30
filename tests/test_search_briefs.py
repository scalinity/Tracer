import csv
import json
from types import SimpleNamespace

import pytest

from src.api import export, search_briefs as sb
from src.generator import prompt
from src.models import risk


@pytest.fixture(scope="module")
def bundle():
    return json.loads(export.DEFAULT_OUT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def items(bundle):
    return sb.alert_inputs(bundle)


def good(inputs: dict) -> str:
    a = inputs["likely_places"][0]
    return (f"Check {a['place']} first, {a['percent']} percent. Nothing has scanned in for {inputs['minutes_since_last_scan']} minutes. "
            f"{inputs['minutes_left']} minutes are left.")


def message(text):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn", _request_id="req",
                           usage=SimpleNamespace(input_tokens=400, output_tokens=60, cache_creation_input_tokens=0, cache_read_input_tokens=0))


class FakeClient:
    """Writes a good brief for every request, except where told to write a bad one."""

    def __init__(self, bad_batch=(), bad_always=(), errored=(), missing=()):
        self.bad_batch, self.bad_always, self.errored, self.missing = set(bad_batch), set(bad_always), set(errored), set(missing)
        self.requests, self.creates, self.batch_creates = {}, [], []
        self.messages = SimpleNamespace(create=self.create, count_tokens=lambda **kw: SimpleNamespace(input_tokens=400),
                                        batches=SimpleNamespace(create=self.batch_create, retrieve=self.retrieve, results=self.results))

    def create(self, **kw):
        inputs = json.loads(kw["messages"][0]["content"])
        sid = next(i["specimen_id"] for i in self.items if i["minutes_left"] == inputs["minutes_left"] and i["minutes_since_last_scan"] == inputs["minutes_since_last_scan"]
                   and i["risk_percent"] == inputs["risk_percent"] and i["specimen_type"] == inputs["specimen_type"])
        self.creates.append((sid, inputs.get("validator_error")))
        return message("Nothing here is certain, definitely 99" if sid in self.bad_always else good(sb_input(inputs)))

    def batch_create(self, requests):
        self.batch_creates.append(requests)
        self.requests = {r["custom_id"]: r["params"] for r in requests}
        return SimpleNamespace(id="batch_1")

    def retrieve(self, batch_id):
        return SimpleNamespace(processing_status="ended", request_counts=SimpleNamespace(processing=0))

    def results(self, batch_id):
        for sid, params in self.requests.items():
            if sid in self.missing:
                continue
            if sid in self.errored:
                yield SimpleNamespace(custom_id=sid, result=SimpleNamespace(type="errored"))
                continue
            inputs = json.loads(params["messages"][0]["content"])
            text = "This one is far too long. " * 20 if sid in self.bad_batch or sid in self.bad_always else good(sb_input(inputs))
            yield SimpleNamespace(custom_id=sid, result=SimpleNamespace(type="succeeded", message=message(text)))


def sb_input(inputs):
    return inputs


def fake(items, **kw):
    c = FakeClient(**kw)
    c.items = items
    return c


@pytest.fixture
def small(items):
    """Eight alerts with distinct inputs, so a fake can tell them apart."""
    seen, out = set(), []
    for i in items:
        key = (i["minutes_left"], i["minutes_since_last_scan"], i["risk_percent"], i["specimen_type"])
        if key not in seen and len(out) < 8:
            seen.add(key)
            out.append(i)
    return out


def small_bundle(bundle, small):
    keep = {i["specimen_id"] for i in small}
    return bundle | {"specimens": [s for s in bundle["specimens"] if s["id"] in keep]}


# ---- the inputs ----

def test_there_is_one_input_per_alert_at_the_headline_setting(bundle, items):
    assert len(items) == sum(s["alerts"]["model"] is not None for s in bundle["specimens"]) == 806
    assert [i["specimen_id"] for i in items] == [s["id"] for s in bundle["specimens"] if s["alerts"]["model"] is not None]


def test_each_alert_goes_to_the_role_alerts_csv_routes_it_to(items):
    with (export.ROOT / "results" / "alerts.csv").open(newline="", encoding="utf-8") as f:
        routed = {row["specimen_id"]: row["role"] for row in csv.DictReader(f)}
    assert {i["specimen_id"]: i["role"] for i in items} == routed


def test_the_inputs_are_the_bundles_numbers_in_plain_words(bundle, items):
    by_id = {s["id"]: s for s in bundle["specimens"]}
    for i in items:
        s = by_id[i["specimen_id"]]
        k = s["alerts"]["model"] // bundle["tick_min"] - 1
        assert i["minutes_since_last_scan"] == s["alerts"]["model"] and i["minutes_left"] == s["window_min"] - s["alerts"]["model"]
        assert i["risk_percent"] == round(100 * s["risk"][k])
        places = i["likely_places"]
        assert 1 <= len(places) <= 3 and [p["percent"] for p in places] == sorted((p["percent"] for p in places), reverse=True)
        assert sum(p["percent"] for p in places) <= 101 and len({p["place"] for p in places}) == len(places)
        arrival = i["expected_arrival_minutes_from_now"]
        assert 0 <= arrival["median"] <= arrival["latest_likely"]
    one = next(i for i in items if i["specimen_id"] == "SP-r24-000012")
    assert one["likely_places"][0] == {"place": "the laboratory's tube station, where it may be stalled", "percent": 57}


def test_the_station_and_the_stall_are_one_place(items):
    for i in items:
        stations = [p for p in i["likely_places"] if "laboratory's tube station" in p["place"]]
        assert len(stations) <= 1, i["specimen_id"]


def test_the_expected_arrival_is_the_risk_models(bundle, items):
    i = next(x for x in items if x["specimen_id"] == "SP-r24-000001")
    s = next(x for x in bundle["specimens"] if x["id"] == i["specimen_id"])
    pooled, w = bundle["model"]["pooled"], bundle["model"]["pooled"]["weights"]
    g = bundle["model"]["groups"][s["group"]]
    mix = risk.Mixture(((w["normal"], g["mu"], g["sigma"]), (w["stalled"], pooled["stalled_mu"], pooled["stalled_sigma"])), w["never"])
    assert i["expected_arrival_minutes_from_now"] == {"median": round(risk.arrival_quantile(20, mix, 0.5)), "latest_likely": round(risk.arrival_quantile(20, mix, 0.9))}


def test_the_model_never_sees_the_id_or_the_bookkeeping(items):
    sent = json.loads(sb.build_request(items[0])["messages"][0]["content"])
    assert "specimen_id" not in sent and "_top" not in sent and "validator_error" not in sent
    assert "validator_error" in json.loads(sb.build_request(items[0], error="too long")["messages"][0]["content"])


def test_the_request_asks_sonnet_for_a_short_plain_text_brief(items):
    r = sb.build_request(items[0])
    assert r["model"] == "claude-sonnet-5-5" and r["thinking"] == {"type": "between_tools"} and "temperature" not in r
    assert "at most 50 words" in r["system"] and "Use only the numbers in the input" in r["system"] and "Do not claim certainty" in r["system"]


# ---- the template ----

def test_the_template_is_short_deterministic_and_passes_its_own_checks(items):
    for i in items:
        text = sb.template_brief(i)
        assert text == sb.template_brief(i) and len(text.split()) <= sb.MAX_WORDS
        assert sb.check_brief(text, i) == [], (i["specimen_id"], text)


# ---- the checks on a Claude brief ----

@pytest.mark.parametrize("text, expect", [
    ("", "empty"),
    ("Check the pneumatic tube first. " * 13, "words, at most 60"),
    ("**Check** the pneumatic tube first, 55 percent. 45 minutes are left.", "plain text"),
    ("Check the pneumatic tube first.\n\n45 minutes are left.", "plain text"),
    ("- Check the pneumatic tube first, 55 percent. 45 minutes are left.", "plain text"),
    ("Check the pneumatic tube first, 61 percent. 45 minutes are left.", "numbers not in the input: 61"),
    ("Check the pneumatic tube first, 55 percent. About 25 minutes are left.", "numbers not in the input: 25"),
    ("Check the pneumatic tube first, 55 percent. Time is short.", "does not give the 45 minutes left"),
    ("Look in the ward first, 55 percent. 45 minutes are left.", "does not name the most likely place"),
    ("Check the pneumatic tube first, 55 percent. It is definitely there. 45 minutes are left.", "claims certainty"),
    ("Check the pneumatic tube first, 55 percent. The patient may need a new draw. 45 minutes are left.", "medical advice"),
])
def test_each_check_names_what_is_wrong(text, expect):
    inputs = {"role": "receiving technologist", "specimen_type": "urine", "minutes_since_last_scan": 30, "risk_percent": 20,
              "likely_places": [{"place": "the pneumatic tube", "percent": 55}, {"place": "the collecting unit", "percent": 30}],
              "expected_arrival_minutes_from_now": {"median": 10, "latest_likely": 40}, "minutes_left": 45, "_top": "in_tube"}
    assert any(expect in e for e in sb.check_brief(text, inputs)), sb.check_brief(text, inputs)


def test_a_good_brief_passes_and_numbers_from_the_input_are_allowed():
    inputs = {"role": "r", "specimen_type": "u", "minutes_since_last_scan": 30, "risk_percent": 20,
              "likely_places": [{"place": "the pneumatic tube", "percent": 55}], "expected_arrival_minutes_from_now": {"median": 10, "latest_likely": 40},
              "minutes_left": 45, "_top": "in_tube"}
    ok = "Check the pneumatic tube first, 55 percent. Nothing has scanned in for 30 minutes, and the usual wait is 10 more, at most 40. 45 minutes are left."
    assert sb.check_brief(ok, inputs) == []


def test_the_blind_key_is_seeded_and_roughly_balanced(items):
    a = [sb.blind_a(i["specimen_id"]) for i in items]
    assert a == [sb.blind_a(i["specimen_id"]) for i in items] and set(a) == {"template", "claude"}
    assert 0.4 < a.count("template") / len(a) < 0.6


# ---- the cost, and the guard ----

def test_the_estimate_is_the_batch_price_of_the_counted_tokens(items):
    e = sb.estimate_job(lambda request: 400, items)
    price = prompt.PRICES[sb.MODEL]
    one = (400 * price["in"] + 120 * prompt.OUT_ALLOWANCE * price["out"]) / 1e6 * prompt.BATCH_DISCOUNT
    assert e["n"] == 806 and e["cost_usd"] == pytest.approx(806 * one) and 0.5 < e["cost_usd"] < 2


@pytest.mark.parametrize("cost, spent, message", [(2.5, 0.0, "over the \\$2.00 a job may cost"), (1.5, 9.0, "would pass \\$10.00")])
def test_the_guard_stops_a_job_over_two_dollars_or_a_total_over_ten_and_says_to_ask(cost, spent, message):
    with pytest.raises(sb.SpendGuardError, match=message + ".*ask Daniel"):
        sb.check_guard({"cost_usd": cost}, spent)


def test_the_guard_lets_a_cheap_job_through():
    sb.check_guard({"cost_usd": 0.9}, 2.4)
    sb.check_guard({"cost_usd": 2.0}, 8.0)


def test_spent_so_far_adds_the_usage_log(tmp_path):
    log = tmp_path / "u.jsonl"
    log.write_text(json.dumps({"cost_usd": 1.25}) + "\n" + json.dumps({"cost_usd": 0.5}) + "\n\n")
    assert sb.spent_so_far(log) == pytest.approx(1.75) and sb.spent_so_far(tmp_path / "none.jsonl") == 0


# ---- the five-alert test and the job ----

def test_try_writes_one_at_a_time_logs_it_as_a_test_and_reports_the_checks(small, tmp_path):
    client = fake(small)
    rows = sb.try_some(client, small, 3, log_path=tmp_path / "u.jsonl")
    assert len(rows) == 3 and all(r["errors"] == [] and r["template"] and r["claude"] for r in rows)
    logged = [json.loads(line) for line in (tmp_path / "u.jsonl").read_text().splitlines()]
    assert [r["job"] for r in logged] == ["J4-test"] * 3 and all(r["batch"] is False for r in logged)


def run(client, bundle, small, tmp_path, **kw):
    kwargs = dict(budget_usd=2.0, ledger_path=tmp_path / "ledger.json", log_path=tmp_path / "u.jsonl", poll=0)
    return sb.run_job(client, small_bundle(bundle, small), tmp_path / "briefs.json", **(kwargs | kw))


def test_a_job_submits_one_batch_and_writes_both_briefs_for_every_alert(bundle, small, tmp_path, capsys):
    client = fake(small)
    doc = run(client, bundle, small, tmp_path)
    assert len(client.batch_creates) == 1 and len(client.batch_creates[0]) == len(small) and client.creates == []
    assert doc["job"]["alerts"] == doc["job"]["claude_accepted"] == len(small) == doc["job"]["first_try"]
    assert json.loads((tmp_path / "briefs.json").read_text()) == doc
    for i in small:
        entry = doc["briefs"][i["specimen_id"]]
        assert entry["template"] == sb.template_brief(i) and entry["claude"] == good(sb.model_input(i)) and entry["blind_a"] in ("template", "claude")
    logged = [json.loads(line) for line in (tmp_path / "u.jsonl").read_text().splitlines()]
    assert len(logged) == len(small) and all(r["job"] == "J4" and r["batch"] and r["model"] == sb.MODEL for r in logged)
    assert doc["job"]["cost_usd"] == pytest.approx(sum(r["cost_usd"] for r in logged), abs=1e-4)
    assert "accepted" in capsys.readouterr().out


def test_a_failed_brief_is_retried_once_with_the_error_and_a_second_failure_keeps_the_template(bundle, small, tmp_path):
    first, second, third = (small[k]["specimen_id"] for k in (0, 1, 2))
    client = fake(small, bad_batch={first}, bad_always={second}, errored={third})
    doc = run(client, bundle, small, tmp_path)
    assert doc["job"]["first_try"] == len(small) - 3 and doc["job"]["retried"] == 2 and doc["job"]["fell_back"] == 1
    assert doc["briefs"][first]["claude"] and doc["briefs"][third]["claude"] and doc["briefs"][second]["claude"] is None
    assert doc["briefs"][second]["template"]  # the template stands alone
    retried = {sid: err for sid, err in client.creates}
    assert set(retried) == {first, second, third} and all(retried.values())  # each retry carried the validator's text
    assert "words, at most" in retried[first] and "batch reported errored" in retried[third]


def test_an_alert_the_batch_never_answered_is_retried_too(bundle, small, tmp_path):
    gone = small[3]["specimen_id"]
    doc = run(fake(small, missing={gone}), bundle, small, tmp_path)
    assert doc["briefs"][gone]["claude"] and doc["job"]["retried"] == 1


def test_a_rerun_reuses_the_submitted_batch(bundle, small, tmp_path):
    client = fake(small)
    run(client, bundle, small, tmp_path)
    run(client, bundle, small, tmp_path)
    assert len(client.batch_creates) == 1


def test_a_job_over_its_budget_submits_nothing(bundle, small, tmp_path):
    client = fake(small)
    with pytest.raises(prompt.BudgetError):
        run(client, bundle, small, tmp_path, budget_usd=0.000001)
    assert client.batch_creates == [] and not (tmp_path / "briefs.json").exists()


def test_a_job_that_trips_the_spend_guard_submits_nothing(bundle, small, tmp_path):
    (tmp_path / "u.jsonl").write_text(json.dumps({"cost_usd": 9.999}) + "\n")
    client = fake(small)
    with pytest.raises(sb.SpendGuardError, match="ask Daniel"):
        run(client, bundle, small, tmp_path)
    assert client.batch_creates == []


def test_wait_false_submits_and_stops(bundle, small, tmp_path):
    client = fake(small)
    assert run(client, bundle, small, tmp_path, wait=False) is None
    assert len(client.batch_creates) == 1 and (tmp_path / "ledger.json").exists() and not (tmp_path / "briefs.json").exists()


# ---- the command line ----

def test_a_real_run_needs_a_budget(monkeypatch, items):
    monkeypatch.setattr(prompt, "make_client", lambda: fake(items))
    with pytest.raises(SystemExit, match="--budget-usd"):
        sb.main([])


def test_the_dry_run_prices_the_job_and_sends_nothing(monkeypatch, items, capsys):
    client = fake(items)
    monkeypatch.setattr(prompt, "make_client", lambda: client)
    assert sb.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "806 briefs on claude-sonnet-5-5 (batch)" in out and "within the spend guard" in out
    assert client.batch_creates == [] and client.creates == []


def test_the_module_never_prints_or_writes_a_key():
    text = (export.ROOT / "src" / "api" / "search_briefs.py").read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" not in text and "print(client" not in text and "api_key" not in text


# ---- the committed briefs ----

BRIEFS_PATH = sb.OUT_PATH


@pytest.fixture(scope="module")
def briefs():
    return json.loads(BRIEFS_PATH.read_text(encoding="utf-8"))


def test_the_committed_briefs_validate_against_their_schema(briefs):
    import jsonschema
    schema = json.loads((export.ROOT / "docs" / "demo" / "briefs.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(briefs, schema)


def test_every_alert_in_the_bundle_has_a_template_brief_and_a_claude_brief(bundle, items, briefs):
    assert list(briefs["briefs"]) == sorted(i["specimen_id"] for i in items) and len(items) == briefs["job"]["alerts"] == 806
    assert briefs["inputs_hash"] == sb.inputs_hash(items) and briefs["bundle_dataset_sha256"] == bundle["source"]["sha256"]
    for i in items:
        assert briefs["briefs"][i["specimen_id"]]["inputs"] == sb.model_input(i)


def test_the_template_briefs_are_what_the_code_writes(items, briefs):
    for i in items:
        assert briefs["briefs"][i["specimen_id"]]["template"] == sb.template_brief(i)


def test_every_claude_brief_passes_the_checks_and_the_counts_add_up(items, briefs):
    for i in items:
        text = briefs["briefs"][i["specimen_id"]]["claude"]
        assert text is not None and sb.check_brief(text, i) == [], (i["specimen_id"], text)
    j = briefs["job"]
    assert j["claude_accepted"] == sum(v["claude"] is not None for v in briefs["briefs"].values()) == j["first_try"] + j["retried"]
    assert j["fell_back"] == 0 and j["first_try"] + j["retried"] + j["fell_back"] == j["alerts"]
    assert j["cost_usd"] < sb.JOB_LIMIT_USD


def test_the_blind_key_is_the_seeded_one_and_hides_nothing_in_the_text(briefs):
    flags = [v["blind_a"] for v in briefs["briefs"].values()]
    assert all(v["blind_a"] == sb.blind_a(sid) for sid, v in briefs["briefs"].items()) and {"template", "claude"} == set(flags)
    for v in briefs["briefs"].values():  # neither brief says which writer it is
        for text in (v["template"], v["claude"]):
            assert "template" not in text.lower() and "claude" not in text.lower() and "model" not in text.lower()


def test_the_briefs_were_written_with_the_prompt_in_the_code(briefs):
    import hashlib
    assert briefs["prompt_hash"] == hashlib.sha256((sb.SYSTEM + sb.EFFORT).encode()).hexdigest()[:16]
    assert briefs["model"] == sb.MODEL == "claude-sonnet-5-5" and "no clinical review" in briefs["notice"]
