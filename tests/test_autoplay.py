"""The self-playing 3D demo (docs/demo/play/): its data file matches the two committed demo files, and its story reads right."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.api import autoplay

ROOT = Path(__file__).resolve().parents[1]
PLAY = ROOT / "docs" / "demo" / "play"


def test_scenes_json_is_what_the_export_writes():
    built = autoplay.build(json.loads(autoplay.BUNDLE.read_text()), json.loads(autoplay.NEURAL.read_text()))
    assert json.loads((PLAY / "scenes.json").read_text()) == json.loads(json.dumps(built))


def test_the_three_scenes_are_on_time_stalled_and_lost():
    data = json.loads((PLAY / "scenes.json").read_text())
    kinds = [(s["kind"], s["fault"], s["received_min"] is None) for s in data["scenes"]]
    assert kinds == [("on_time", "none", False), ("stalled", "stalled_at_station", False), ("lost", "lost_in_tube", True)]
    for s in data["scenes"]:
        assert len(s["risk"]["tracer"]) == len(s["risk"]["tracernet"]) == len(s["places"])
        for row in s["places"]:
            assert abs(sum(row) - 1) < 1e-3


def test_every_file_the_page_loads_is_in_the_folder():
    html = (PLAY / "index.html").read_text()
    for name in ("play.css", "main.js", "vendor/three.module.js"):
        assert name in html and (PLAY / name).is_file()
    main = (PLAY / "main.js").read_text()
    for line in main.splitlines():
        if line.startswith("import") and "three/addons/" in line:
            rel = line.split("three/addons/")[1].split('"')[0]
            assert (PLAY / "vendor" / "addons" / rel).is_file(), rel
    assert (PLAY / "vendor" / "three.core.js").is_file()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_story_under_node():
    script = """
import * as S from './story.js';
import fs from 'fs';
const d = JSON.parse(fs.readFileSync('scenes.json'));
const out = d.scenes.map((s) => ({ caps: S.captions(s).map((c) => [c.at, c.tone, c.text]), verdict: S.verdict(s),
  risk_end: S.riskAt(s, d.tick_min, S.endMinute(s)) }));
console.log(JSON.stringify(out));
"""
    res = subprocess.run(["node", "--input-type=module", "-e", script], cwd=PLAY, capture_output=True, text=True, check=True)
    on_time, stalled, lost = json.loads(res.stdout)
    assert not any(c[1] == "alert" for c in on_time["caps"])
    assert on_time["risk_end"] == {"tracer": 0, "tracernet": 0}
    assert [c[0] for c in stalled["caps"] if c[1] == "alert"] == [20, 25, 30]
    assert any("Deadline passes" in c[2] for c in lost["caps"])
    assert lost["risk_end"] == {"tracer": 1, "tracernet": 1}
    assert lost["verdict"]["lines"][0]["text"] == "45 min warning"
