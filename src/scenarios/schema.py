"""The scenario schema, in the two forms the pipeline needs (docs/neural/spec.md §4.3).

`config/scenario.schema.json` is the full schema: JSON Schema Draft 2020-12 with every bound,
length and pattern, every property required (optional ones are nullable) and
`additionalProperties: false`. Code applies it to every model reply (`errors`).

`api_schema()` is that schema with every keyword that OpenAI Structured Outputs does not accept
in strict mode removed (`minLength`, `maxLength`, titles, ids and the like), checked against the
explicit allow-list below. The allow-list is the supported-keyword list of notes/neural/
api-reference.md section D. Shares and weights are normalized in code (validate.normalize), so
the schema never asks the model to make numbers sum to anything.

`schema_hash` identifies the full schema; the prompt hash covers the API schema.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "config" / "scenario.schema.json"
CONFIG_PATH = ROOT / "config" / "config.yaml"

# Strict mode accepts these keywords (api-reference.md section D: supported types and properties).
# minLength and maxLength are not on the list, so only the full schema carries them.
API_KEYWORDS = frozenset({
    "type", "properties", "required", "additionalProperties", "items", "enum", "anyOf", "pattern",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minItems", "maxItems"})

# Strict-mode limits from the same section.
MAX_PROPERTIES = 5000
MAX_DEPTH = 10
MAX_NAME_CHARS = 120_000
MAX_ENUM_VALUES = 1000

MAX_ERRORS = 30  # a repair request carries at most this many messages


@lru_cache(maxsize=1)
def full_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def mix_keys() -> list[str]:
    """The 12 specimen_mix keys, in config/config.yaml order."""
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return list(cfg["specimen_mix"]["entries"])


def _strip(node: dict) -> dict:
    kept = {k: v for k, v in node.items() if k in API_KEYWORDS}
    if "properties" in kept:
        kept["properties"] = {name: _strip(sub) for name, sub in kept["properties"].items()}
    if "items" in kept:
        kept["items"] = _strip(kept["items"])
    if "anyOf" in kept:
        kept["anyOf"] = [_strip(sub) for sub in kept["anyOf"]]
    return kept


def api_schema() -> dict:
    """The full schema reduced to the keywords strict mode accepts."""
    return _strip(full_schema())


def canonical(schema: dict) -> str:
    return json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def schema_hash() -> str:
    return hashlib.sha256(canonical(full_schema()).encode("utf-8")).hexdigest()[:16]


def keywords_used(node: dict) -> set[str]:
    """Every schema keyword that appears in a schema (property names are not keywords)."""
    found = set(node)
    for sub in node.get("properties", {}).values():
        found |= keywords_used(sub)
    if "items" in node:
        found |= keywords_used(node["items"])
    for sub in node.get("anyOf", []):
        found |= keywords_used(sub)
    return found


def api_stats(schema: dict | None = None) -> dict:
    """The numbers strict mode limits: properties, nesting, name and enum characters, enum values."""
    schema = api_schema() if schema is None else schema
    stats = {"properties": 0, "depth": 0, "name_chars": 0, "enum_values": 0}

    def walk(node: dict, depth: int) -> None:
        stats["depth"] = max(stats["depth"], depth)
        for value in node.get("enum", []):
            stats["enum_values"] += 1
            stats["name_chars"] += len(str(value))
        for name, sub in node.get("properties", {}).items():
            stats["properties"] += 1
            stats["name_chars"] += len(name)
            walk(sub, depth + 1)
        if "items" in node:
            walk(node["items"], depth + 1)
        for sub in node.get("anyOf", []):
            walk(sub, depth + 1)

    walk(schema, 1)
    stats["json_chars"] = len(canonical(schema))
    return stats


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(full_schema())


def _path(error) -> str:
    out = ""
    for part in error.absolute_path:
        out += f"[{part}]" if isinstance(part, int) else f".{part}" if out else str(part)
    return out or "the scenario"


def errors(scenario) -> list[str]:
    """Plain-English layer-1 errors, sorted by location. An empty list means the schema holds."""
    found = sorted({f"{_path(e)}: {e.message}" for e in _validator().iter_errors(scenario)})
    if len(found) > MAX_ERRORS:
        found = found[:MAX_ERRORS] + [f"and {len(found) - MAX_ERRORS} more schema errors"]
    return found
