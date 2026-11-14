"""The short explainer (docs/explained/short/index.html): eight screens, short words, the committed result.

The page is the audience version of docs/explained/index.html, one screen per idea. These tests hold it to its
word budgets, tie the result chart to results/neural/test_fresh/evaluation.json, and check that every relative
link lands on a file under docs/ when the site is served as GitHub Pages serves it.
"""

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
PATH = DOCS / "explained" / "short" / "index.html"
PAGE = PATH.read_text(encoding="utf-8")
SCREENS = ["problem", "matters", "does", "watch", "try", "result", "limits", "more"]
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Screens(HTMLParser):
    """Visible text and headline of each <section class="screen">, and every href on the page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.current, self.in_head, self.skip = [], None, False, 0
        self.text, self.head, self.order, self.links = {}, {}, [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "href" in a and tag == "a":
            self.links.append(a["href"])
        if tag in VOID:
            return
        self.stack.append(tag)
        if tag == "section" and "screen" in (a.get("class") or "").split():
            self.current = a["id"]
            self.order.append(a["id"])
            self.text[self.current], self.head[self.current] = [], []
            self.depth = len(self.stack)
        if tag in ("script", "style"):
            self.skip += 1
        if tag in ("h1", "h2") and self.current:
            self.in_head = True

    def handle_endtag(self, tag):
        if tag in VOID or not self.stack:
            return
        if tag in ("script", "style"):
            self.skip -= 1
        if tag in ("h1", "h2"):
            self.in_head = False
        if tag == "section" and self.current and len(self.stack) == self.depth:
            self.current = None
        self.stack.pop()

    def handle_data(self, data):
        if self.current and not self.skip:
            self.text[self.current].append(data)
            if self.in_head:
                self.head[self.current].append(data)


PARSED = Screens()
PARSED.feed(PAGE)


def words(parts):
    return [w for w in re.split(r"\s+", " ".join(parts)) if re.search(r"[A-Za-z0-9]", w)]


def screen_text(sid):
    return re.sub(r"\s+", " ", " ".join(PARSED.text[sid])).strip()


def test_the_page_has_eight_screens_in_the_decks_order():
    assert PARSED.order == SCREENS


def test_every_headline_is_short_and_every_screen_is_brief():
    for sid in SCREENS:
        head = words(PARSED.head[sid])
        assert 0 < len(head) <= 8, (sid, head)
        assert len(words(PARSED.text[sid])) <= 33, (sid, len(words(PARSED.text[sid])))


def test_the_result_chart_is_the_committed_fresh_test():
    data = json.loads((ROOT / "results" / "neural" / "test_fresh" / "evaluation.json").read_text(encoding="utf-8"))
    six = data["operating_points"]["6/shift"]
    want = {
        "TracerNet": six["tracernet"]["3-2-1"]["capped_mean_warning_min"]["point"],
        "Our survival model": six["analytic_local"]["3-2-1"]["capped_mean_warning_min"]["point"],
        "Simple deadline rule": six["window_rule"]["none"]["capped_mean_warning_min"]["point"],
    }
    shown = re.findall(r'<span class="name">([^<]+)</span>.*?<span class="v">([\d.]+)</span>', PAGE)
    assert {name: float(v) for name, v in shown} == {name: round(v, 1) for name, v in want.items()}
    assert [v for _, v in shown] == ["23.3", "13.6", "7.6"]
    text = screen_text("result")
    assert "Minutes of warning per late specimen, at 6 alerts a shift." in text
    assert f"{data['counts']['tuning_groups']} fresh synthetic lab weeks, scored once." in text
    assert data["counts"]["tuning_groups"] == 776


def test_the_synthetic_line_appears_once():
    visible = " ".join(screen_text(sid) for sid in SCREENS)
    assert visible.count("Synthetic data, no clinical review.") == 1


def test_every_relative_link_resolves_to_a_file_under_docs():
    relative = [h for h in PARSED.links if not urlsplit(h).scheme and not h.startswith("#")]
    assert relative, "no relative links found"
    for href in relative:
        assert not href.startswith("/"), href  # served under /Tracer/, so never root-relative
        target = (PATH.parent / urlsplit(href).path).resolve()
        if target.is_dir():
            target = target / "index.html"
        assert target.is_file() and DOCS.resolve() in target.parents, href
    for want in ("../../demo/play/", "../../demo/", "../../demo/?seed=18#beat", "../../talk/", "../"):
        assert want in PARSED.links, want
    assert "https://github.com/scalinity/Tracer" in PARSED.links
    assert all(h[1:] in SCREENS for h in PARSED.links if h.startswith("#"))


def test_no_survival_model_internals_or_jargon_on_screen():
    visible = " ".join(screen_text(sid) for sid in SCREENS).lower()
    for median in ("55", "45", "30"):
        assert not re.search(rf"(?<![\d.]){median}(?![\d.])", visible), median
    for term in ("tier", "harm threshold", "brier", "p_miss", "capped mean", "operating point", "held-out", "auroc"):
        assert term not in visible, term
