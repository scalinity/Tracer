import copy
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.generator import briefs as briefs_mod
from src.generator import prompt, validate
from trails import render_trail


@pytest.fixture(scope="module")
def pool():
    return briefs_mod.make_briefs(0, 0, 6000)


def pick(pool, fault, route=None, **where):
    for b in pool:
        if b["planted_fault"] == fault and (route is None or b["fixed"]["route"] == route) \
                and all(b["targets"].get(k) is not None for k in where):
            return b
    raise LookupError((fault, route))


@pytest.fixture(scope="module")
def stall(pool):
    return pick(pool, "stalled_at_station")


@pytest.fixture
def good(stall):
    return render_trail(stall)


def failed(brief, trail):
    return {f.check for f in validate.validate(brief, trail)}


def messages(brief, trail, check):
    return " | ".join(f.message for f in validate.validate(brief, trail) if f.check == check)


def shift(trail, after_index, minutes):
    """Move every segment from index on, and the events at or after the first moved start, by some minutes."""
    delta = timedelta(minutes=minutes)
    cut = datetime.fromisoformat(trail["truth_segments"][after_index]["start"])
    for s in trail["truth_segments"][after_index:]:
        s["start"] = (datetime.fromisoformat(s["start"]) + delta).isoformat()
        if s["end"]:
            s["end"] = (datetime.fromisoformat(s["end"]) + delta).isoformat()
    if after_index:
        trail["truth_segments"][after_index - 1]["end"] = trail["truth_segments"][after_index]["start"]
    for e in trail["observed_events"]:
        if datetime.fromisoformat(e["timestamp"]) >= cut:
            e["timestamp"] = (datetime.fromisoformat(e["timestamp"]) + delta).isoformat()


# ---- a good trail passes, in every shape a trail takes ----

def test_the_prompts_worked_examples_pass_their_own_validator():
    for brief, trail in zip(prompt.EXAMPLE_BRIEFS, prompt.EXAMPLE_TRAILS):
        assert validate.validate(brief, trail) == []


def test_a_correct_trail_passes_for_every_fault_and_route(pool):
    seen = set()
    for b in pool:
        assert validate.validate(b, render_trail(b)) == [], b["specimen_id"]
        seen.add((b["planted_fault"], b["fixed"]["route"]))
    assert {f for f, _ in seen} == {"none", "lost_in_tube", "stalled_at_station", "misrouted", "not_dispatched",
                                    "never_collected", "content_failure"}
    assert ("none", "hand_carry") in seen and ("none", "tube") in seen


def test_the_ordered_event_is_optional(stall):
    assert validate.validate(stall, render_trail(stall, with_order=False)) == []


def test_a_trail_that_crosses_midnight_passes(pool):
    late = next(b for b in pool if b["fixed"]["collected_at"][11:13] == "23" and b["planted_fault"] == "none"
                and (b["targets"]["receipt_after_min"] or 0) > 60)
    assert validate.validate(late, render_trail(late)) == []


# ---- check 1: the reply parses, matches the schema, and names the right specimen ----

@pytest.mark.parametrize("mutate, text", [
    (lambda t: t.pop("outcome"), "missing 'outcome'"),
    (lambda t: t.update(extra=1), "unexpected field 'extra'"),
    (lambda t: t.update(specimen_id="SP-r99-000001"), "specimen_id"),
    (lambda t: t["observed_events"][0].update(activity="dispatched"), "not one of"),
    (lambda t: t["observed_events"][1].update(timestamp="yesterday"), "not an ISO 8601"),
    (lambda t: t["observed_events"][1].update(timestamp="2026-03-04T02:14:00Z"), "time zone"),
    (lambda t: t["truth_segments"][0].update(state="in_the_hallway"), "not one of"),
    (lambda t: t["outcome"].update(received="yes"), "must be boolean"),
    (lambda t: t.update(truth_segments=[]), "empty"),
])
def test_check_1_catches_a_bad_reply(stall, good, mutate, text):
    mutate(good)
    assert failed(stall, good) == {1}
    assert text in messages(stall, good, 1)


def test_check_1_gates_the_others(stall):
    assert failed(stall, {"nonsense": True}) == {1}
    assert failed(stall, None) == {1}


# ---- check 2: events in time order, each activity once, ordered or collected first ----

def test_check_2_time_order(stall, good):
    ev = good["observed_events"]
    ev[2]["timestamp"], ev[3]["timestamp"] = ev[3]["timestamp"], ev[2]["timestamp"]
    assert 2 in failed(stall, good) and "time order" in messages(stall, good, 2)


def test_check_2_each_activity_at_most_once(stall, good):
    good["observed_events"].append(copy.deepcopy(good["observed_events"][-1]))
    assert failed(stall, good) == {2}
    assert "'plated' appears 2 times" in messages(stall, good, 2)


def test_check_2_ordered_or_collected_comes_first(stall, good):
    good["observed_events"] = [e for e in good["observed_events"] if e["activity"] not in ("ordered", "collected")]
    assert 2 in failed(stall, good) and "must be ordered or collected" in messages(stall, good, 2)


# ---- check 3: no scan for a hidden stage; a lost or unsent specimen is never received ----

def test_check_3_no_scan_at_a_hidden_stage(stall, good):
    good["observed_events"][1]["location"] = stall["locations"][1]  # the unit's tube station
    assert failed(stall, good) == {3}
    assert "hidden stage" in messages(stall, good, 3)


@pytest.mark.parametrize("fault", ["lost_in_tube", "not_dispatched", "never_collected"])
def test_check_3_a_specimen_that_never_arrives_has_no_received_event(pool, fault):
    b = pick(pool, fault)
    t = render_trail(b)
    t["observed_events"].append({"activity": "received", "timestamp": "2026-03-04T05:00:00",
                                 "location": "lab_receiving", "actor_role": "technologist"})
    assert 3 in failed(b, t) and "never reaches the lab" in messages(b, t, 3)


# ---- check 4: segments start at collected_at, join, and use allowed states ----

def test_check_4_gap(stall, good):
    s = good["truth_segments"][1]
    s["end"] = (datetime.fromisoformat(s["end"]) - timedelta(minutes=1)).isoformat()
    assert 4 in failed(stall, good) and "gap" in messages(stall, good, 4)


def test_check_4_overlap(stall, good):
    s = good["truth_segments"][1]
    s["end"] = (datetime.fromisoformat(s["end"]) + timedelta(minutes=1)).isoformat()
    assert 4 in failed(stall, good) and "overlap" in messages(stall, good, 4)


def test_check_4_starts_at_collected_at(stall, good):
    good["truth_segments"][0]["start"] = "2026-03-04T00:00:00"
    assert 4 in failed(stall, good) and "not at collected_at" in messages(stall, good, 4)


def test_check_4_only_the_last_segment_may_be_open(stall, good):
    good["truth_segments"][2]["end"] = None
    assert 4 in failed(stall, good) and "open-ended but" in messages(stall, good, 4)


def test_check_4_allowed_states(stall, good):
    good["truth_segments"][0]["state"] = "in_the_hallway"
    assert "unknown states" in " ".join(validate.check_segments(stall, good))  # check 1 also catches it on a real reply


# ---- check 5: segment lengths match the targets ----

def test_check_5_a_segment_too_long(stall, good):
    i = next(i for i, s in enumerate(good["truth_segments"]) if s["state"] == "stalled_at_station")
    shift(good, i + 1, 5)  # stall now 5 minutes long than its target; everything after moves with it
    assert 5 in failed(stall, good)
    assert "stalled_at_station lasts" in messages(stall, good, 5)


def test_check_5_two_minutes_is_tolerated(stall, good):
    i = next(i for i, s in enumerate(good["truth_segments"]) if s["state"] == "stalled_at_station")
    shift(good, i + 1, 2)
    assert 5 not in failed(stall, good)


def test_check_5_a_missing_segment(stall, good):
    i = next(i for i, s in enumerate(good["truth_segments"]) if s["state"] == "at_station")
    good["truth_segments"][i]["state"] = "in_tube"  # relabel, so at_station is absent and in_tube appears twice
    assert 5 in failed(stall, good)
    assert "no at_station segment" in messages(stall, good, 5)


def test_check_5_a_segment_for_a_stage_that_does_not_happen(pool):
    b = pick(pool, "none", "hand_carry")
    t = render_trail(b)
    t["truth_segments"].insert(1, {**t["truth_segments"][0], "state": "in_tube"})
    assert 5 in failed(b, t) and "in_tube segment exists but tube_transit_min is null" in messages(b, t, 5)


# ---- check 6: the planted fault matches the outcome and the segment states ----

def test_check_6_outcome_fault(stall, good):
    good["outcome"]["fault"] = "none"
    assert failed(stall, good) == {6}
    assert "planted fault" in messages(stall, good, 6)


def test_check_6_a_stall_needs_its_segment(stall, good):
    i = next(i for i, s in enumerate(good["truth_segments"]) if s["state"] == "stalled_at_station")
    good["truth_segments"][i]["state"] = "at_station"
    assert 6 in failed(stall, good) and "needs a stalled_at_station segment" in messages(stall, good, 6)


def test_check_6_a_loss_ends_in_lost(pool):
    b = pick(pool, "lost_in_tube")
    t = render_trail(b)
    t["truth_segments"][-1]["end"] = "2026-03-09T00:00:00"
    assert 6 in failed(b, t) and "open-ended lost segment" in messages(b, t, 6)


def test_check_6_lost_specimen_cannot_be_received(pool):
    b = pick(pool, "lost_in_tube")
    t = render_trail(b)
    t["outcome"]["received"] = True
    assert 6 in failed(b, t) and "outcome.received" in messages(b, t, 6)


def test_check_6_content_failure_is_rejected_not_plated(pool):
    b = pick(pool, "content_failure")
    t = render_trail(b)
    for e in t["observed_events"]:
        if e["activity"] == "rejected":
            e["activity"] = "accessioned"
    assert 6 in failed(b, t) and "rejected event" in messages(b, t, 6)


def test_check_6_an_uncollected_specimen_has_no_collected_event(pool):
    b = pick(pool, "never_collected")
    t = render_trail(b)
    t["observed_events"].append({"activity": "collected", "timestamp": t["observed_events"][0]["timestamp"],
                                 "location": b["locations"][0], "actor_role": "nurse"})
    assert 6 in failed(b, t) and "no collected event" in messages(b, t, 6)


def test_check_6_a_normal_specimen_must_complete(pool):
    b = pick(pool, "none", "tube")
    t = render_trail(b)
    t["observed_events"] = [e for e in t["observed_events"] if e["activity"] != "plated"]
    t["outcome"]["plated"] = False
    assert 6 in failed(b, t) and "accessioned and plated" in messages(b, t, 6)


# ---- check 7: the receipt scan lines up with the end of transit ----

def test_check_7_receipt_off_the_last_transit_segment(stall, good):
    for e in good["observed_events"]:
        if e["activity"] == "received":
            e["timestamp"] = (datetime.fromisoformat(e["timestamp"]) + timedelta(minutes=5)).isoformat()
    assert failed(stall, good) == {7}
    assert "end of the last transit segment" in messages(stall, good, 7)


def test_check_7_one_minute_is_tolerated(stall, good):
    for e in good["observed_events"]:
        if e["activity"] == "received":
            e["timestamp"] = (datetime.fromisoformat(e["timestamp"]) + timedelta(minutes=1)).isoformat()
    assert failed(stall, good) == set()


def test_check_7_small_drifts_that_add_up_are_caught(stall, good):
    """Every segment is within its 2 minute tolerance, but three of them each run 2 minutes long."""
    for state in ("in_tube", "at_station", "stalled_at_station"):
        i = next(i for i, s in enumerate(good["truth_segments"]) if s["state"] == state)
        shift(good, i + 1, 2)
    assert failed(stall, good) == {7}
    assert "from collected_at + receipt_after_min" in messages(stall, good, 7)


def test_check_7_collected_event_is_at_collected_at(stall, good):
    for e in good["observed_events"]:
        if e["activity"] == "collected":
            e["timestamp"] = (datetime.fromisoformat(e["timestamp"]) + timedelta(minutes=4)).isoformat()
    assert 7 in failed(stall, good) and "collected event" in messages(stall, good, 7)


# ---- check 8: locations come from the brief; at most two notes of at most 12 words ----

def test_check_8_location_outside_the_list(stall, good):
    good["observed_events"][1]["location"] = "ed_bay_04"
    assert failed(stall, good) == {8}
    assert "'ed_bay_04' is not in the brief's list" in messages(stall, good, 8)


def test_check_8_segment_location_outside_the_list(stall, good):
    good["truth_segments"][0]["location"] = "radiology"
    assert failed(stall, good) == {8}


def test_check_8_too_many_notes(stall, good):
    for e in good["observed_events"][:3]:
        e["note"] = "plain note"
    assert failed(stall, good) == {8}
    assert "3 notes; at most 2" in messages(stall, good, 8)


def test_check_8_a_long_note(stall, good):
    good["observed_events"][1]["note"] = " ".join(["word"] * 13)
    assert failed(stall, good) == {8}
    assert "13 words" in messages(stall, good, 8)


def test_check_8_twelve_words_is_fine(stall, good):
    good["observed_events"][1]["note"] = " ".join(["word"] * 12)
    assert failed(stall, good) == set()


@pytest.mark.parametrize("note", [
    "carrier found sitting in the station", "tube came back from pharmacy station", "tube found stalled at lab station",
    "specimen was delayed in transit", "stuck in the tube", "was lost and then re-sent", "sent to the wrong station",
    "rerouted after a long wait"])
def test_check_8_a_note_may_not_reveal_the_hidden_path(stall, good, note):
    good["observed_events"][1]["note"] = note
    assert failed(stall, good) == {8}
    assert "hidden path" in messages(stall, good, 8)


@pytest.mark.parametrize("note", ["specimen leaking at the lid", "label slightly torn", "cap loose on arrival",
                                  "underfilled tube", "wrong label on the container", "amount looks small"])
def test_check_8_ordinary_handling_notes_pass(stall, good, note):
    good["observed_events"][1]["note"] = note
    assert failed(stall, good) == set()


def test_check_8_the_prompts_examples_do_not_teach_leaking():
    for trail in prompt.EXAMPLE_TRAILS:
        for e in trail["observed_events"]:
            assert not validate.HIDDEN_PATH_WORDS.search(e.get("note", "")), e


# ---- check 9: no personal names or dates of birth ----

@pytest.mark.parametrize("note, text", [
    ("Mr. Smith waiting in bay four", "personal name"),
    ("Nurse Patel brought it down", "personal name"),
    ("sent by Maria from the unit", "personal name"),
    ("DOB 04/12/1961 on the label", "date-of-birth"),
    ("born 1961-04-12", "date-of-birth"),
    ("patient born March 3, 1958", "date-of-birth"),
])
def test_check_9_catches_personal_data(stall, good, note, text):
    good["observed_events"][1]["note"] = note
    assert failed(stall, good) == {9}
    assert text in messages(stall, good, 9)


@pytest.mark.parametrize("note", ["tube arrived with cap loose", "green cap tube, label slightly torn",
                                  "label printed at the bedside", "sent as an urgent tube", "white lid cracked"])
def test_check_9_leaves_plain_lab_notes_alone(stall, good, note):
    good["observed_events"][1]["note"] = note
    assert failed(stall, good) == set()


# ---- retry once, then drop ----

class ScriptedClient:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1, cache_creation_input_tokens=0,
                                                     cache_read_input_tokens=0), _request_id="req")


def run(client, brief, tmp_path):
    return validate.generate_valid(client, brief, job="J1", log_path=tmp_path / "usage.jsonl",
                                   dropped_path=tmp_path / "dropped.jsonl")


def bad_copy(trail):
    t = copy.deepcopy(trail)
    t["outcome"]["fault"] = "none"
    return t


def test_a_good_first_reply_is_returned_without_a_retry(stall, good, tmp_path):
    client = ScriptedClient(good)
    assert run(client, stall, tmp_path) == good and len(client.calls) == 1
    assert not (tmp_path / "dropped.jsonl").exists()


def test_a_failed_first_reply_is_retried_once_with_the_error_text(stall, good, tmp_path):
    client = ScriptedClient(bad_copy(good), good)
    assert run(client, stall, tmp_path) == good and len(client.calls) == 2
    sent = json.loads(client.calls[1]["messages"][0]["content"])
    assert "check 6 (planted fault)" in sent["validator_error"]
    assert "validator_error" not in json.loads(client.calls[0]["messages"][0]["content"])
    assert len((tmp_path / "usage.jsonl").read_text().splitlines()) == 2  # both calls are billed and logged


def test_a_second_failure_drops_the_specimen_and_logs_it(stall, good, tmp_path):
    client = ScriptedClient(bad_copy(good), bad_copy(good))
    assert run(client, stall, tmp_path) is None and len(client.calls) == 2
    row = json.loads((tmp_path / "dropped.jsonl").read_text())
    assert row["specimen_id"] == stall["specimen_id"] and row["job"] == "J1"
    assert "check 6" in row["errors"]


def test_a_reply_that_is_not_json_counts_as_a_failed_attempt(stall, good, tmp_path):
    client = ScriptedClient("sorry, here is your trail:", good)
    assert run(client, stall, tmp_path) == good
    assert "not JSON" in json.loads(client.calls[1]["messages"][0]["content"])["validator_error"]


def test_a_schema_failure_reports_check_1_and_stops_there(stall, tmp_path):
    client = ScriptedClient({"nonsense": 1}, {"nonsense": 2})
    assert run(client, stall, tmp_path) is None
    assert "check 1 (schema)" in json.loads((tmp_path / "dropped.jsonl").read_text())["errors"]
