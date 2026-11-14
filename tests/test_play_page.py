"""The self-playing bench page: every file its built HTML loads is committed beside it, and it has
nothing to press."""

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


def test_play_page_has_no_controls_and_one_source_line():
    html = (PLAY / "index.html").read_text()
    assert "<button" not in html
    assert not re.search(r"<(input|select|nav)\b", html)
    assert html.count("Synthetic data, no clinical review.") == 1
    # One built script and one stylesheet: stale hashed bundles are removed when the page is rebuilt.
    assert len(list(PLAY.glob("assets/*.js"))) == 1
    assert len(list(PLAY.glob("assets/*.css"))) == 1
