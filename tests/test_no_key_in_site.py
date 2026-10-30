"""The Claude key never reaches the repo or the public site (plan task P7, step "no key, no API").

The key lives only in .env. The demo is static, so nothing under docs/ may hold a key or name the API, and the
value of the key in .env, when there is one, appears in no tracked file. A failure names the file, never the key.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
KEY_SHAPE = re.compile(rb"sk-ant-[A-Za-z0-9_\-]{10,}")
API_HOSTS = (b"api.anthropic.com", b"x-api-key", b"anthropic-version")
TEXT_SUFFIXES = {".html", ".js", ".json", ".css", ".svg", ".md", ".txt", ".py", ".yaml", ".yml", ".cjs", ".mjs", ".csv", ".tex"}


def tracked() -> list[Path]:
    names = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True).stdout.split(b"\0")
    return [ROOT / n.decode() for n in names if n and (ROOT / n.decode()).is_file()]


def test_nothing_published_under_docs_has_a_key_or_names_the_api():
    published = [p for p in (ROOT / "docs").rglob("*") if p.is_file() and p.suffix in TEXT_SUFFIXES and "course" not in p.parts and "submissions" not in p.parts]
    assert published, "docs/ has no text files to check"
    hits = [str(p.relative_to(ROOT)) for p in published if (data := p.read_bytes()) and (KEY_SHAPE.search(data) or any(h in data for h in API_HOSTS))]
    assert hits == [], f"key-shaped text or an API host in the published site: {hits}"


def test_the_value_of_the_key_in_env_is_in_no_tracked_file():
    key = (dotenv_values(ROOT / ".env").get("ANTHROPIC_API_KEY") or "").encode()
    if len(key) < 20:
        pytest.skip("no ANTHROPIC_API_KEY in .env here, so there is no value to look for")
    hits = [str(p.relative_to(ROOT)) for p in tracked() if key in p.read_bytes()]
    assert hits == [], f"the key's value is in tracked files: {hits}"
