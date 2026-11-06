"""The talk's slides and its script agree (docs/talk/).

The speaker lines live twice: in docs/talk/README.md, where they are read and edited, and in the slides'
notes, where the presenter reads them. These tests keep the two word for word, keep the run of show at
15 minutes with no gaps, and check that every local link on the slides points at a file that exists.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TALK = ROOT / "docs" / "talk"
SCRIPT = (TALK / "README.md").read_text(encoding="utf-8")
DECK = (TALK / "index.html").read_text(encoding="utf-8")


def plain(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text))).strip()


def script_notes() -> dict[int, list[str]]:
    """Each slide's notes from the script: speaker names and paragraphs, in order."""
    parts = re.split(r"^### Slide (\d+) .*$", SCRIPT, flags=re.M)
    notes = {}
    for k in range(1, len(parts), 2):
        body = parts[k + 1].split("\n## ")[0]
        lines, speaking = [], False
        for para in (p.strip() for p in body.split("\n\n") if p.strip()):
            m = re.match(r"^\*\*(Karissa|Daniel|Notes):\*\*\s*(.*)$", para, re.S)
            if m:
                speaking = True
                if m.group(1) != "Notes":
                    lines.append(m.group(1))
                if m.group(2):
                    lines.append(plain(m.group(2)))
            elif para.startswith("**Screen:**"):
                speaking = False
            elif speaking:
                lines.append(plain(para))
        notes[int(parts[k])] = lines
    return notes


class Slides(HTMLParser):
    def __init__(self):
        super().__init__()
        self.slides, self.links, self._in_notes, self._p = [], [], False, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "section" and "slide" in (a.get("class") or "").split():
            self.slides.append({"data": a, "notes": []})
        if tag == "aside" and a.get("class") == "notes-src":
            self._in_notes = True
        if self._in_notes and tag == "p":
            self._p = []
        for key in ("href", "src"):
            if a.get(key):
                self.links.append(a[key])

    def handle_endtag(self, tag):
        if tag == "aside":
            self._in_notes = False
        if tag == "p" and self._p is not None:
            self.slides[-1]["notes"].append(re.sub(r"\s+", " ", "".join(self._p)).strip())
            self._p = None

    def handle_data(self, data):
        if self._p is not None:
            self._p.append(data)


def parsed() -> Slides:
    p = Slides()
    p.feed(DECK)
    return p


def secs(t: str) -> int:
    m, s = t.split(":")
    return int(m) * 60 + int(s)


def test_the_slides_carry_the_scripts_lines_word_for_word():
    slides, notes = parsed().slides, script_notes()
    assert len(slides) == len(notes) == 10
    for i, slide in enumerate(slides):
        assert slide["notes"] == notes[i], f"slide {i}: the notes and docs/talk/README.md differ"


def test_the_run_of_show_fills_fifteen_minutes_with_no_gap():
    timed = [s["data"] for s in parsed().slides if s["data"].get("data-start")]
    assert secs(timed[0]["data-start"]) == 0 and secs(timed[-1]["data-end"]) == 15 * 60
    for a, b in zip(timed, timed[1:]):
        assert a["data-end"] == b["data-start"], (a["data-beat"], b["data-beat"])
    for d in timed:
        assert f"{d['data-start']} to {d['data-end']}" in SCRIPT, d["data-beat"]
    hands_on = sum(secs(d["data-end"]) - secs(d["data-start"]) for d in timed
                   if d["data-beat"] in ("Board and timeline", "Beat Tracer", "Why this design"))
    assert hands_on == 510  # 8.5 minutes, as the script says


def test_every_local_link_on_the_slides_exists():
    for link in parsed().links:
        if link.startswith(("http", "data:", "#")):
            continue
        path = link.split("#")[0].split("?")[0]
        if path:
            assert (TALK / path).resolve().is_file(), link


def test_the_slides_that_show_the_demo_or_its_numbers_say_it_is_synthetic():
    for chunk in re.findall(r'<section class="slide".*?</section>', DECK, flags=re.S):
        visible = chunk.split("<aside")[0]
        demo = re.search(r'(href|src)="\.\./demo/(?!exhibit)', visible) is not None
        if demo or "min</span>" in visible:
            assert "Synthetic data, no clinical review" in visible, plain(visible)[:60]


def test_the_talk_never_says_the_clock_stops_at_plating():
    for text in (SCRIPT.lower(), DECK.lower()):
        assert "plated" not in text and "plating" not in text
