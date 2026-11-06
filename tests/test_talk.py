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


# ── the script page (docs/talk/script.html), built from the script by scripts/build_talk_script.py ──

SCRIPT_PAGE = TALK / "script.html"
DIRECTION = r"\[[^\]]*\]"


def generator():
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_talk_script", ROOT / "scripts" / "build_talk_script.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScriptPage(HTMLParser):
    """Reads the script page on its own terms: beats, speaker blocks, lines, directions, answers and nav."""

    def __init__(self):
        super().__init__()
        self.slide_tags, self.sections, self.answers, self.nav, self.directions = [], {}, [], [], []
        self._n, self._open = None, []  # the beat being read; captures as [kind, tag, depth, text]

    def handle_starttag(self, tag, attrs):
        a, classes = dict(attrs), (dict(attrs).get("class") or "").split()
        for cap in self._open:
            if cap[1] == tag:
                cap[2] += 1
        if "data-slide" in a:
            self.slide_tags.append((tag, int(a["data-slide"])))
            if tag == "section":
                self._n = int(a["data-slide"])
                self.sections[self._n] = {"items": [], "lines": [], "window": None}
        if self._n is not None and "block" in classes and a.get("data-who"):
            self.sections[self._n]["items"].append(a["data-who"])
        if self._n is not None and tag == "p" and "line" in classes:
            self._open.append(["line", tag, 0, []])
        if self._n is not None and "window" in classes and self.sections[self._n]["window"] is None:
            self._open.append(["window", tag, 0, []])
        if "sd" in classes:
            self._open.append(["sd", tag, 0, []])
        if tag == "li" and "answer" in classes:
            self.answers.append(a.get("data-who"))
        if tag == "a" and "seg" in classes:
            self.nav.append((a.get("data-beat"), a.get("href")))

    def handle_endtag(self, tag):
        for cap in list(self._open):
            if cap[1] != tag:
                continue
            if cap[2]:
                cap[2] -= 1
                continue
            self._open.remove(cap)
            text = re.sub(r"\s+", " ", "".join(cap[3])).strip()
            if cap[0] == "line":
                self.sections[self._n]["items"].append(text)
                self.sections[self._n]["lines"].append(text)
            elif cap[0] == "window":
                self.sections[self._n]["window"] = text
            else:
                self.directions.append(text)
        if tag == "section":
            self._n = None

    def handle_data(self, data):
        for cap in self._open:
            cap[3].append(data)


def script_page() -> ScriptPage:
    p = ScriptPage()
    p.feed(SCRIPT_PAGE.read_text(encoding="utf-8"))
    return p


def test_the_script_page_is_built_from_the_current_script():
    same = generator().render(SCRIPT).encode("utf-8") == SCRIPT_PAGE.read_bytes()
    assert same, "docs/talk/script.html differs from docs/talk/README.md: run python scripts/build_talk_script.py"


def test_the_script_page_carries_every_line():
    page, notes, deck = script_page(), script_notes(), parsed().slides
    assert sorted(n for _, n in page.slide_tags) == list(range(10))
    assert {tag for tag, _ in page.slide_tags} == {"section"}, "only the beats carry data-slide"
    for n, section in page.sections.items():
        assert section["items"] == notes[n], f"beat {n}: the script page and docs/talk/README.md differ"
        d = deck[n]["data"]
        if d.get("data-start"):
            assert section["window"] == f"{d['data-start']} to {d['data-end']}", f"beat {n}: its time window"
        else:
            assert section["window"] and " to " not in section["window"], f"beat {n} is before the clock starts"


def test_the_script_page_keeps_the_answers_and_run_of_show():
    page = script_page()
    tags = re.findall(r"^\d+\. \*\*.+?\*\* \[(Daniel|Karissa)\]", SCRIPT, flags=re.M)
    assert len(tags) == 10 and page.answers == tags
    assert page.nav == [(str(n), f"#beat-{n}") for n in range(10)]


def md_plain(text: str) -> str:
    """A markdown cell or line as the page shows it: bold, code and autolink markers removed."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    return re.sub(r"\s+", " ", re.sub(r"<(https?://[^>]+)>", r"\1", text)).strip()


class PageTables(HTMLParser):
    """Every table inside the script page's timekeeping section, as rows of cell text."""

    def __init__(self):
        super().__init__()
        self.tables, self._depth, self._cell = [], 0, None

    def handle_starttag(self, tag, attrs):
        if self._depth:
            self._depth += tag == "section"
        elif tag == "section" and dict(attrs).get("id") == "timekeeping":
            self._depth = 1
        if not self._depth:
            return
        if tag == "table":
            self.tables.append([])
        elif tag == "tr":
            self.tables[-1].append([])
        elif tag in ("th", "td"):
            self._cell = []

    def handle_endtag(self, tag):
        if self._depth and tag in ("th", "td"):
            self.tables[-1][-1].append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        if self._depth and tag == "section":
            self._depth -= 1

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def test_the_script_page_shows_every_cell_of_the_run_of_show():
    section = SCRIPT.split("\n## Run of show\n")[1].split("\n## ")[0]
    readme = []
    for group in re.findall(r"(?:^\|.*\n?)+", section, flags=re.M):
        rows = [[md_plain(c) for c in line.strip().strip("|").split("|")] for line in group.strip().split("\n")]
        readme.append([r for r in rows if not all(re.fullmatch(r":?-+:?", c) for c in r)])
    page = PageTables()
    page.feed(SCRIPT_PAGE.read_text(encoding="utf-8"))
    assert len(readme) == 2 and readme[0][0][0] == "Clock" and len(readme[0]) == 11, "the run of show has a header and 10 beats"
    assert page.tables == readme, "a run-of-show or minute-by-minute cell on the script page differs from docs/talk/README.md"



def test_stage_directions_are_only_directions():
    page, notes = script_page(), script_notes()
    assert page.directions and all(re.fullmatch(DIRECTION, d) for d in page.directions), page.directions
    spoken = [line for n in sorted(notes) for line in notes[n] if line not in ("Daniel", "Karissa")]
    assert len(page.directions) == sum(len(re.findall(DIRECTION, line)) for line in spoken)
    for n, section in page.sections.items():
        readme = [line for line in notes[n] if line not in ("Daniel", "Karissa")]
        assert " ".join(section["lines"]) == " ".join(readme), f"beat {n}: spoken text was lost"
