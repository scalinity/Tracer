"""No API key reaches the repo or the public site (plan task P7, step "no key, no API"; docs/neural/spec.md §15.1).

The keys live only in .env: ANTHROPIC_API_KEY, OPENAI_API_KEY and OPENROUTER_API_KEY. The demo is static, so nothing
under docs/ may hold a key or name an API host (base URLs live in config/), and the value of each key in .env, when
there is one, appears in no tracked file. A failure names the file, never the key.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
# Anthropic keys (sk-ant-), OpenAI secret, project, service-account and admin keys (sk-, sk-proj-, sk-svcacct-,
# sk-admin-) and OpenRouter keys (sk-or-v1-). The look-behind keeps words such as "risk-adjusted" out.
KEY_SHAPE = re.compile(rb"sk-ant-[A-Za-z0-9_\-]{10,}|(?<![A-Za-z0-9_])sk-(?:proj-|svcacct-|admin-|or-v1-|or-)?[A-Za-z0-9_\-]{20,}")
API_HOSTS = (b"api.anthropic.com", b"x-api-key", b"anthropic-version",
             b"api.openai.com", b"openrouter.ai/api", b"api.typesafe.ai")
KEY_NAMES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY")
TEXT_SUFFIXES = {".html", ".js", ".json", ".css", ".svg", ".md", ".txt", ".py", ".yaml", ".yml", ".cjs", ".mjs", ".csv", ".tex"}


def tracked() -> list[Path]:
    names = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True).stdout.split(b"\0")
    return [ROOT / n.decode() for n in names if n and (ROOT / n.decode()).is_file()]


def test_nothing_published_under_docs_has_a_key_or_names_the_api():
    published = [p for p in (ROOT / "docs").rglob("*") if p.is_file() and p.suffix in TEXT_SUFFIXES and "course" not in p.parts and "submissions" not in p.parts]
    assert published, "docs/ has no text files to check"
    hits = [str(p.relative_to(ROOT)) for p in published if (data := p.read_bytes()) and (KEY_SHAPE.search(data) or any(h in data for h in API_HOSTS))]
    assert hits == [], f"key-shaped text or an API host in the published site: {hits}"


@pytest.mark.parametrize("text", [b"sk-ant-api03-abcdefghij0123", b"sk-proj-abcdefghij0123456789ABCD",
                                  b"sk-abcdefghij0123456789ABCDEFGH", b"sk-or-v1-0123456789abcdef0123456789abcdef",
                                  b"sk-svcacct-abcdefghij0123456789AB"])
def test_the_key_shape_matches_every_provider(text):
    assert KEY_SHAPE.search(b"key = " + text)


@pytest.mark.parametrize("text", [b"risk-adjusted-warning-for-every-specimen", b"desk-and-the-receiving-queue-at-night",
                                  b"sk-short"])
def test_the_key_shape_leaves_ordinary_words_alone(text):
    assert not KEY_SHAPE.search(text)


@pytest.mark.parametrize("name", KEY_NAMES)
def test_the_value_of_each_key_in_env_is_in_no_tracked_file(name):
    key = (dotenv_values(ROOT / ".env").get(name) or "").encode()
    if len(key) < 20:
        pytest.skip(f"no {name} in .env here, so there is no value to look for")
    hits = [str(p.relative_to(ROOT)) for p in tracked() if key in p.read_bytes()]
    assert hits == [], f"the value of {name} is in tracked files: {hits}"
