"""The self-playing bench page: every file its built HTML loads is committed beside it."""

import re
from pathlib import Path

PLAY = Path(__file__).resolve().parents[1] / "docs" / "demo" / "play"


def test_play_page_loads_only_committed_files():
    html = (PLAY / "index.html").read_text()
    refs = re.findall(r'(?:src|href)="\./([^"]+)"', html)
    assert any(r.endswith(".js") for r in refs)
    for ref in refs:
        assert (PLAY / ref).is_file(), ref
    css = next(PLAY.glob("assets/*.css")).read_text()
    for font in re.findall(r"url\(\./([^)]+)\)", css):
        assert (PLAY / "assets" / font).is_file(), font
