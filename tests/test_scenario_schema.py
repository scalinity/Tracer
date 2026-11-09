import copy
import hashlib
import json
import re

import pytest
import yaml
from jsonschema import Draft202012Validator

from src.scenarios import planner, prompt, random_baseline, schema, validate

EXAMPLE = json.loads((schema.ROOT / "config" / "scenario.example.json").read_text(encoding="utf-8"))
UNSUPPORTED = {"allOf", "not", "if", "then", "else", "dependentRequired", "dependentSchemas", "$ref", "$defs",
               "minLength", "maxLength", "title", "$schema", "$id", "description", "default", "uniqueItems"}


def objects(node: dict):
    """Every object schema inside a schema, including array items."""
    if node.get("type") == "object":
        yield node
    for sub in node.get("properties", {}).values():
        yield from objects(sub)
    if "items" in node:
        yield from objects(node["items"])


# --- The API schema ------------------------------------------------------------------------------

def test_api_schema_uses_only_allow_listed_keywords():
    used = schema.keywords_used(schema.api_schema())
    assert used <= schema.API_KEYWORDS
    assert not used & UNSUPPORTED


def test_full_schema_carries_the_keywords_the_api_schema_drops():
    assert {"minLength", "maxLength"} <= schema.keywords_used(schema.full_schema())
    assert {"title", "$schema", "$id"} <= set(schema.full_schema())


def test_api_schema_meets_the_strict_mode_limits():
    stats = schema.api_stats()
    assert stats["properties"] <= schema.MAX_PROPERTIES
    assert stats["depth"] <= schema.MAX_DEPTH
    assert stats["name_chars"] <= schema.MAX_NAME_CHARS
    assert stats["enum_values"] <= schema.MAX_ENUM_VALUES


def test_api_schema_root_is_an_object_without_anyof():
    root = schema.api_schema()
    assert root["type"] == "object" and "anyOf" not in root


@pytest.mark.parametrize("which", ["api", "full"])
def test_every_object_requires_all_its_properties_and_forbids_extras(which):
    root = schema.api_schema() if which == "api" else schema.full_schema()
    for node in objects(root):
        assert node["additionalProperties"] is False
        assert sorted(node["required"]) == sorted(node["properties"])


def test_api_schema_nullable_values_use_a_null_type():
    nullable = schema.api_schema()["properties"]["units"]["items"]["properties"]["tube_line_id"]
    assert nullable["type"] == ["string", "null"]


def test_api_schema_keeps_bounds_and_patterns_strict_mode_supports():
    unit = schema.api_schema()["properties"]["units"]["items"]["properties"]
    assert unit["dispatch"]["properties"]["median_min"] == {"type": "number", "minimum": 1, "maximum": 120}
    assert "pattern" in unit["unit_id"] and "maxLength" not in schema.api_schema()["properties"]["narrative"]


def test_full_schema_is_a_valid_draft_2020_12_schema():
    Draft202012Validator.check_schema(schema.full_schema())
    assert schema.full_schema()["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_specimen_mix_keys_are_the_twelve_of_config_yaml():
    cfg = yaml.safe_load((schema.ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    keys = list(cfg["specimen_mix"]["entries"])
    assert len(keys) == 12 and schema.mix_keys() == keys
    assert list(schema.full_schema()["properties"]["specimen_mix"]["properties"]) == keys


def test_schema_hash_is_stable_and_changes_with_the_schema():
    assert schema.schema_hash() == schema.schema_hash() and len(schema.schema_hash()) == 16
    other = copy.deepcopy(schema.full_schema())
    other["properties"]["volume"]["properties"]["weekend_factor"]["maximum"] = 2
    assert schema.canonical(other) != schema.canonical(schema.full_schema())


# --- What the full schema accepts and rejects ---------------------------------------------------------

def test_full_schema_accepts_the_worked_example():
    assert schema.errors(EXAMPLE) == []


@pytest.mark.parametrize("seed", range(50))
def test_full_schema_accepts_code_random_scenarios(seed):
    s = random_baseline.random_scenario(seed)
    assert schema.errors(s) == []
    assert validate.semantic_errors(s) == []
    assert validate.name_errors(s) == []


def set_at(scenario, path, value):
    node = scenario
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return scenario


UNIT = ("units", 0)
LINE = ("tube_system", "lines", 0)
ROUTE = ("couriers", "routes", 0)
DISRUPTION = ("disruptions", 0)
MUTATIONS = {
    "volume too low": (("volume", "specimens_per_weekday"), 29),
    "volume too high": (("volume", "specimens_per_weekday"), 901),
    "volume not an integer": (("volume", "specimens_per_weekday"), 220.5),
    "weekend factor low": (("volume", "weekend_factor"), 0.29),
    "weekend factor high": (("volume", "weekend_factor"), 1.21),
    "profile too short": (("volume", "hourly_profile"), [1.0] * 23),
    "profile too long": (("volume", "hourly_profile"), [1.0] * 25),
    "profile negative": (("volume", "hourly_profile", 3), -0.1),
    "mix negative": (("specimen_mix", "tissue"), -1),
    "mix not a number": (("specimen_mix", "tissue"), "a lot"),
    "unit kind unknown": (UNIT + ("kind",), "pharmacy"),
    "unit share zero": (UNIT + ("volume_share",), 0),
    "unit stat above 1": (UNIT + ("stat_fraction",), 1.1),
    "unit route unknown": (UNIT + ("route",), "drone"),
    "unit id uppercase": (UNIT + ("unit_id",), "ED_Main"),
    "unit id too short": (UNIT + ("unit_id",), "a"),
    "tube share above 1": (UNIT + ("tube_share_if_mixed",), 1.5),
    "dispatch mode unknown": (UNIT + ("dispatch", "mode"), "whenever"),
    "dispatch median low": (UNIT + ("dispatch", "median_min"), 0.5),
    "dispatch median high": (UNIT + ("dispatch", "median_min"), 121),
    "dispatch p90 high": (UNIT + ("dispatch", "p90_min"), 481),
    "batch interval low": (UNIT + ("dispatch", "batch_interval_min"), 14),
    "batch interval high": (UNIT + ("dispatch", "batch_interval_min"), 241),
    "not dispatched high": (UNIT + ("not_dispatched_rate",), 0.06),
    "tilt multiplier high": (UNIT + ("mix_tilt",), [{"key": "tissue", "multiplier": 6}]),
    "tilt key unknown": (UNIT + ("mix_tilt",), [{"key": "blood", "multiplier": 1}]),
    "tilt too long": (UNIT + ("mix_tilt",), [{"key": "tissue", "multiplier": 1}] * 7),
    "too few units": (("units",), EXAMPLE["units"][:2]),
    "too many units": (("units",), [EXAMPLE["units"][0]] * 17),
    "too many lines": (("tube_system", "lines"), [EXAMPLE["tube_system"]["lines"][0]] * 9),
    "line median high": (LINE + ("transit_median_min",), 21),
    "line p90 high": (LINE + ("transit_p90_min",), 46),
    "misroute high": (LINE + ("misroute_rate",), 0.031),
    "line lost high": (LINE + ("lost_rate",), 0.011),
    "station interval low": (("tube_system", "station_empty_interval_min", "day"), 1),
    "station interval high": (("tube_system", "station_empty_interval_min", "night"), 91),
    "stalled rate high": (("tube_system", "stalled_rate"), 0.061),
    "stall median low": (("tube_system", "stall_extra_min", "median"), 9),
    "stall p90 high": (("tube_system", "stall_extra_min", "p90"), 721),
    "misroute found median high": (("tube_system", "misroute_found_min", "median"), 121),
    "too many routes": (("couriers", "routes"), [EXAMPLE["couriers"]["routes"][0]] * 7),
    "route kind unknown": (ROUTE + ("kind",), "pigeon"),
    "round interval low": (ROUTE + ("round_interval_min", "day"), 9),
    "round interval high": (ROUTE + ("round_interval_min", "night"), 241),
    "round interval fractional": (ROUTE + ("round_interval_min", "day"), 30.5),
    "travel median low": (ROUTE + ("travel_median_min",), 2),
    "travel p90 high": (ROUTE + ("travel_p90_min",), 241),
    "missed rate high": (ROUTE + ("missed_round_rate",), 0.21),
    "route lost high": (ROUTE + ("lost_rate",), 0.011),
    "capacity low": (ROUTE + ("capacity_per_round",), 4),
    "capacity high": (ROUTE + ("capacity_per_round",), 201),
    "on call not boolean": (("couriers", "on_call_runner"), "yes"),
    "on call response low": (("couriers", "on_call_response_median_min"), 4),
    "staff too high": (("receiving", "staff_by_shift", "weekday", "day"), 13),
    "staff negative": (("receiving", "staff_by_shift", "weekend", "night"), -1),
    "shift pattern unknown": (("receiving", "shift_pattern"), "8-16-24"),
    "scan median low": (("receiving", "scan_service_min", "median"), 0.2),
    "scan median high": (("receiving", "scan_service_min", "median"), 5.1),
    "shift gap high": (("receiving", "shift_change_gap_min"), 46),
    "accession median low": (("processing", "accession_min", "median"), 1),
    "plating p90 high": (("processing", "plating_min", "p90"), 601),
    "too many instruments": (("processing", "instruments"), [EXAMPLE["processing"]["instruments"][0]] * 5),
    "downtime per week high": (("processing", "instruments", 0, "downtime_per_week"), 3.1),
    "downtime median low": (("processing", "instruments", 0, "downtime_median_min"), 9),
    "pull above 1": (("processing", "staff_pull_from_receiving"), 1.1),
    "never collected high": (("independent_faults", "never_collected_rate"), 0.021),
    "content failure high": (("independent_faults", "content_failure_rate"), 0.051),
    "too many disruptions": (("disruptions",), [EXAMPLE["disruptions"][0]] * 15),
    "disruption type unknown": (DISRUPTION + ("type",), "earthquake"),
    "disruption day high": (DISRUPTION + ("day",), 7),
    "disruption start not a time": (DISRUPTION + ("start",), "8:30"),
    "disruption start hour 25": (DISRUPTION + ("start",), "25:00"),
    "disruption duration low": (DISRUPTION + ("duration_min",), 9),
    "disruption duration high": (DISRUPTION + ("duration_min",), 721),
    "severity low": (DISRUPTION + ("severity",), 0.09),
    "severity high": (DISRUPTION + ("severity",), 1.01),
    "response unknown": (DISRUPTION + ("response",), "panic"),
    "noticed high": (DISRUPTION + ("noticed_after_min",), 241),
    "recurs not boolean": (DISRUPTION + ("recurs_daily",), "daily"),
    "edge tag unknown": (("edge_tags",), ["alien_invasion"]),
    "edge tags too many": (("edge_tags",), ["compound_failure"] * 7),
    "narrative too long": (("narrative",), "x" * 601),
    "narrative too short": (("narrative",), "short"),
    "notes too long": (("realism_notes",), "x" * 401),
    "archetype unknown": (("archetype",), "teaching_clinic"),
    "focus unknown": (("failure_focus",), "everything"),
}


def test_there_are_at_least_thirty_mutations():
    assert len(MUTATIONS) >= 30


@pytest.mark.parametrize("name", list(MUTATIONS))
def test_full_schema_rejects_each_mutation_of_the_example(name):
    path, value = MUTATIONS[name]
    bad = set_at(copy.deepcopy(EXAMPLE), path, copy.deepcopy(value))
    found = schema.errors(bad)
    assert found, name


def test_full_schema_rejects_missing_and_extra_properties_and_wrong_roots():
    missing = copy.deepcopy(EXAMPLE)
    del missing["couriers"]["on_call_runner"]
    extra = copy.deepcopy(EXAMPLE)
    extra["units"][0]["favorite_color"] = "blue"
    missing_mix = copy.deepcopy(EXAMPLE)
    del missing_mix["specimen_mix"]["tissue"]
    for bad in (missing, extra, missing_mix, [], "text", None):
        assert schema.errors(bad)


def test_errors_say_where_and_why_in_plain_english():
    bad = set_at(copy.deepcopy(EXAMPLE), ("units", 2, "dispatch", "median_min"), 140)
    text = " ".join(schema.errors(bad))
    assert "units[2].dispatch.median_min" in text and "maximum" in text


def test_errors_are_capped_so_a_repair_request_stays_short():
    bad = copy.deepcopy(EXAMPLE)
    bad["units"] = [{"unit_id": "A"}] * 10
    found = schema.errors(bad)
    assert len(found) <= schema.MAX_ERRORS + 1


# --- The prompt (its hash covers the API schema, so it is checked beside it) ------------------------------------

def all_names(node: dict):
    for name, sub in node.get("properties", {}).items():
        yield name
        yield from all_names(sub)
    if "items" in node:
        yield from all_names(node["items"])


def test_developer_text_is_stable_and_ignores_its_argument():
    assert prompt.developer_text() == prompt.developer_text("O2") == prompt.developer_text({"model": "x"})


def test_developer_text_explains_every_field_before_listing_bounds():
    text = prompt.developer_text().split("BOUNDS")[0]
    assert [n for n in all_names(schema.full_schema()) if n not in text] == []
    disruption = schema.full_schema()["properties"]["disruptions"]["items"]["properties"]
    for value in disruption["type"]["enum"] + disruption["response"]["enum"]:
        assert value in text
    for value in schema.full_schema()["properties"]["receiving"]["properties"]["shift_pattern"]["enum"]:
        assert value in text
    whole = prompt.developer_text()
    for tag in planner.config()["edge_tags"]:
        assert tag in whole
    for value in schema.full_schema()["properties"]["archetype"]["enum"]:
        assert value in whole


def test_developer_text_carries_the_worked_example_and_says_not_to_copy_it():
    text = prompt.developer_text()
    assert json.dumps(EXAMPLE, separators=(",", ":"), ensure_ascii=False) in text
    assert "Do not copy its values" in text


def test_bounds_are_generated_from_the_full_schema():
    lines = prompt.bounds_lines()
    assert "units[].dispatch.median_min: number, 1 to 120" in lines
    assert "volume.hourly_profile: list of exactly 24" in lines
    assert "units[].tube_line_id: id or null" in lines
    assert "disruptions[].start: time HH:MM" in lines
    assert "narrative: text, 40 to 600 characters" in lines


def test_every_range_in_the_realism_guidance_is_marked_as_an_assumption():
    for line in prompt.REALISM.splitlines():
        if line.startswith("- "):
            assert line.startswith("- Assumed") or not re.search(r"\d", line), line
    assert "assumptions for a synthetic simulator" in prompt.REALISM


def test_prompt_names_no_workplace_person_or_key_and_does_not_ask_for_sums():
    text = prompt.developer_text()
    for word in ("Daniel", "Karissa", "Escalante", "Kocourek", "UF ", "Shands", "api_key", "sk-"):
        assert word not in text
    assert "Do not make them sum to anything" in text and "normalized" in text
    assert "No real hospital" in text


def test_build_messages_puts_the_developer_text_first_and_the_brief_last():
    brief = planner.plan("O1", 1)[7]
    messages = prompt.build_messages(brief)
    assert [m["role"] for m in messages] == ["developer", "user"]
    assert messages[0]["content"] == prompt.developer_text()
    body = messages[1]["content"]
    assert body.startswith("Brief:\n") and "rejected" not in body
    sent = json.loads(body.removeprefix("Brief:\n"))
    assert sent == {k: brief[k] for k in prompt.MODEL_BRIEF_KEYS}
    assert not {"checks", "ood", "job", "origin"} & set(sent)


def test_a_repair_appends_the_error_text_after_the_brief():
    brief = planner.plan("O1", 1)[0]
    body = prompt.build_messages(brief, "- units[2].dispatch.median_min: 140 is greater than the maximum of 120")[1]["content"]
    first, rest = body.split("\n\nYour previous reply was rejected", 1)
    assert json.loads(first.removeprefix("Brief:\n"))["brief_id"] == "O1-0001"
    assert "median_min: 140" in rest and rest.rstrip().endswith("keep everything else valid.")


def test_prompt_hash_is_sixteen_hex_digits_of_the_documented_recipe():
    text = prompt.developer_text() + schema.canonical(schema.api_schema()) + "gpt-6.1-terra" + "low"
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    assert prompt.prompt_hash("gpt-6.1-terra", "low") == expected and re.fullmatch(r"[0-9a-f]{16}", expected)


def test_prompt_hash_changes_with_model_effort_text_and_schema(monkeypatch):
    base = prompt.prompt_hash("gpt-6.1-terra", "low")
    assert prompt.prompt_hash("gpt-6.1-sol", "low") != base and prompt.prompt_hash("gpt-6.1-terra", "medium") != base
    with monkeypatch.context() as m:
        m.setattr(prompt, "developer_text", lambda *a: "another text")
        assert prompt.prompt_hash("gpt-6.1-terra", "low") != base
    changed = copy.deepcopy(schema.api_schema())
    changed["properties"]["volume"]["properties"]["weekend_factor"]["maximum"] = 2
    with monkeypatch.context() as m:
        m.setattr(schema, "api_schema", lambda: changed)
        assert prompt.prompt_hash("gpt-6.1-terra", "low") != base
