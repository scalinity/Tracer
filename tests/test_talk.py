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
LENGTH = "15:00"  # the run of show, the length of the slot
SLIDES = 10
HEADLINE_WORDS = 8  # a headline a first-time viewer reads at a glance
SLIDE_WORDS = 23  # the headline and at most about 15 more words on screen
RESULT = {"caption": "Minutes of warning per late specimen, at 6 alerts a shift.",
          "source": "776 fresh synthetic lab weeks, scored once."}  # the shared wording every surface uses
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
    assert len(slides) == len(notes) == SLIDES
    for i, slide in enumerate(slides):
        assert slide["notes"] == notes[i], f"slide {i}: the notes and docs/talk/README.md differ"


def test_the_run_of_show_runs_its_length_with_no_gap():
    timed = [s["data"] for s in parsed().slides if s["data"].get("data-start")]
    assert secs(timed[0]["data-start"]) == 0 and timed[-1]["data-end"] == LENGTH
    assert f"It runs {LENGTH}" in SCRIPT, "the script must say how long the talk runs"
    for a, b in zip(timed, timed[1:]):
        assert a["data-end"] == b["data-start"], (a["data-beat"], b["data-beat"])
    for d in timed:
        assert f"{d['data-start']} to {d['data-end']}" in SCRIPT, d["data-beat"]
    hands_on = sum(secs(d["data-end"]) - secs(d["data-start"]) for d in timed
                   if d["data-beat"] in ("Try it on your phone", "Beat Tracer", "Does it work"))
    assert hands_on == 420  # 7 minutes, as the script says


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


def test_the_two_speakers_split_the_time_evenly_and_trade_often():
    """The script says the speaking time is split about evenly and the talk changes speaker six times.

    Daniel's lines are written out, so his words are counted. Karissa's sections are points she says in her own
    words, so each one's length is the word count its stage direction gives ("about 50 words"). Daniel's
    directions give a count too, and it has to be close to the words written, so the two are comparable.
    """
    words, order = {"Daniel": 0, "Karissa": 0}, []
    for n, lines in sorted(script_notes().items()):
        blocks, who = [], None
        for line in lines:
            if line in words:
                who = line
                order.append(who)
                blocks.append([who, 0, None])
            elif who:
                stated = re.search(r"\[(?:In her own words, about|About) (\d+) words", line)
                if stated:
                    blocks[-1][2] = int(stated.group(1))
                if not re.fullmatch(r"\[[^\]]*\]", line):
                    blocks[-1][1] += len(re.sub(r"\[[^\]]*\]", " ", line).split())
        for who, written, stated in blocks:
            assert stated, f"slide {n}: {who}'s section gives no word count"
            if who == "Daniel":
                assert abs(written - stated) <= max(10, stated * 0.15), (n, written, stated)
                words[who] += written
            else:
                words[who] += stated
    assert 0.45 <= words["Daniel"] / sum(words.values()) <= 0.55, words
    assert sum(a != b for a, b in zip(order, order[1:])) == 6, order
    assert "changes speaker six times" in SCRIPT


def test_karissa_has_an_outline_not_lines():
    """Karissa writes her own lines (Daniel's rule of November 12), so her sections are numbered points."""
    for n, lines in script_notes().items():
        who = None
        for line in lines:
            if line in ("Daniel", "Karissa"):
                who = line
            elif who == "Karissa" and not re.fullmatch(r"\[[^\]]*\]", line):
                assert re.match(r"\d+\. ", line), f"slide {n}: Karissa has a written line, not a point: {line[:60]}"
    for answer in re.findall(r"^\d+\. \*\*.+?\*\* \[Karissa\] (.+)$", SCRIPT, flags=re.M):
        assert answer.startswith("Points: "), answer[:60]


def words(text: str) -> int:
    return sum(1 for w in text.split() if re.search(r"[A-Za-z0-9]", w))


def on_screen(chunk: str) -> str:
    """A slide's visible text, SVG labels included: no notes, no footer caveat, no alt or aria text."""
    chunk = re.sub(r"<aside.*?</aside>", " ", chunk, flags=re.S)
    chunk = re.sub(r'<div class="foot">.*?</div>', " ", chunk, flags=re.S)
    return plain(re.sub(r"<[^>]+>", " ", chunk))


def test_every_slide_reads_at_a_glance():
    """A first-time viewer gets each slide in about five seconds: a short headline and little else to read.

    The synthetic-data caveat in the footer is not counted, since it is the same line on every slide that needs
    it. The result slide also carries the shared caption and source line every surface uses, word for word.
    """
    chunks = re.findall(r'<section class="slide".*?</section>', DECK, flags=re.S)
    assert len(chunks) == SLIDES
    for n, chunk in enumerate(chunks):
        head = re.search(r"<h[12][^>]*>(.*?)</h[12]>", chunk, flags=re.S)
        assert head, f"slide {n} has no headline"
        assert words(plain(head.group(1))) <= HEADLINE_WORDS, (n, plain(head.group(1)))
        text, allowed = on_screen(chunk), SLIDE_WORDS
        if 'class="bars"' in chunk:
            for line in RESULT.values():
                assert line in text, (n, line)
                allowed += words(line)
        assert words(text) <= allowed, (n, words(text), text)
        assert not re.search(r"<(ul|ol)[^>]*>(\s*<li[^>]*>[^<]{60,})", chunk), f"slide {n} has a list of sentences"


def test_the_result_slide_shows_exactly_the_three_results():
    chunk = next(c for c in re.findall(r'<section class="slide".*?</section>', DECK, flags=re.S) if 'class="bars"' in c)
    text = on_screen(chunk)
    assert re.findall(r"\d+(?:\.\d+)?", text) == ["6", "23.3", "13.6", "7.6", "776"], text
    assert [plain(v) for v in re.findall(r'<span class="val">(.*?)</span>', chunk)] == ["23.3", "13.6", "7.6"]
    assert [plain(v) for v in re.findall(r'<p class="lab">(.*?)</p>', chunk)] == ["TracerNet", "Our survival model", "Simple deadline rule"]
    assert "capped" not in text.lower() and "Synthetic data, no clinical review." in chunk


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
        self.qa, self.quiet = [], []  # each prepared answer as (question, speaker, answer); the quiet-room questions
        self._n, self._open = None, []  # the beat being read; captures as [kind, tag, depth, text]
        self._in_answers = False

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
            self.qa.append(["", a.get("data-who"), ""])
        if tag == "p" and self.qa and ("q" in classes or "a" in classes):
            self._open.append(["q" if "q" in classes else "a", tag, 0, []])
        if tag == "section":
            self._in_answers = a.get("id") == "answers"
        if self._in_answers and tag == "li" and "answer" not in classes:
            self._open.append(["quiet", tag, 0, []])
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
            elif cap[0] == "q":
                self.qa[-1][0] = text
            elif cap[0] == "a":
                self.qa[-1][2] = text
            elif cap[0] == "quiet":
                self.quiet.append(text)
            else:
                self.directions.append(text)
        if tag == "section":
            self._n, self._in_answers = None, False

    def handle_data(self, data):
        for cap in self._open:
            cap[3].append(data)


def script_page() -> ScriptPage:
    p = ScriptPage()
    p.feed(SCRIPT_PAGE.read_text(encoding="utf-8"))
    p.qa = [tuple(entry) for entry in p.qa]
    return p


def test_the_script_page_is_built_from_the_current_script():
    same = generator().render(SCRIPT).encode("utf-8") == SCRIPT_PAGE.read_bytes()
    assert same, "docs/talk/script.html differs from docs/talk/README.md: run python scripts/build_talk_script.py"


def test_the_script_page_carries_every_line():
    page, notes, deck = script_page(), script_notes(), parsed().slides
    assert sorted(n for _, n in page.slide_tags) == list(range(SLIDES))
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
    assert page.nav == [(str(n), f"#beat-{n}") for n in range(SLIDES)]


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
    assert len(readme) == 2 and readme[0][0][0] == "Clock" and len(readme[0]) == SLIDES + 1, "the run of show has a header and a row per slide"
    assert page.tables == readme, "a run-of-show or minute-by-minute cell on the script page differs from docs/talk/README.md"


def test_the_script_page_keeps_every_prepared_question_and_answer():
    section = SCRIPT.split("\n## Prepared answers\n")[1].split("\n## ")[0]
    readme = [(f"{n} {md_plain(q)}", who, md_plain(a))
              for n, q, who, a in re.findall(r"^(\d+)\. \*\*(.+?)\*\* \[(Daniel|Karissa)\] (.+)$", section, flags=re.M)]
    quiet = [md_plain(q) for q in re.findall(r"^- (.+)$", section, flags=re.M)]
    page = script_page()
    assert len(readme) == 10 and page.qa == readme, "a prepared question or answer differs from docs/talk/README.md"
    assert len(quiet) == 2 and page.quiet == quiet, "the questions for a quiet room differ from docs/talk/README.md"


def test_stage_directions_are_only_directions():
    page, notes = script_page(), script_notes()
    assert page.directions and all(re.fullmatch(DIRECTION, d) for d in page.directions), page.directions
    spoken = [line for n in sorted(notes) for line in notes[n] if line not in ("Daniel", "Karissa")]
    assert len(page.directions) == sum(len(re.findall(DIRECTION, line)) for line in spoken)
    for n, section in page.sections.items():
        readme = [line for line in notes[n] if line not in ("Daniel", "Karissa")]
        assert " ".join(section["lines"]) == " ".join(readme), f"beat {n}: spoken text was lost"


def test_karissas_outline_is_the_scripts_outline():
    """docs/talk/karissa-outline.md is Karissa's part of this script on its own, so a change here is copied there."""
    outline = (TALK / "karissa-outline.md").read_text(encoding="utf-8")
    mine = re.findall(r"^\*\*Karissa:\*\*\n\n(.+?)(?=\n\n\*\*|\n\n### |\n\n## |\Z)", SCRIPT, flags=re.M | re.S)
    assert len(mine) == 6  # her six sections across slides 1, 2, 5, 6, 8 and 9
    points = []
    for block in mine:
        for para in (p.strip() for p in block.split("\n\n")):
            if para.startswith("[In her own words, about "):
                para = "[About " + para[len("[In her own words, about "):]
            assert para in outline, para
            points.append(para)
    for para in (p.strip() for p in outline.split("\n\n")):
        if re.match(r"\d+\. |\[About ", para):
            assert para in points, para
    for q, a in re.findall(r"^\d+\. (\*\*.+?\*\*) \[Karissa\] (.+)$", SCRIPT, flags=re.M):
        assert f"- {q} {a}" in outline, q
