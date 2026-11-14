"""The public phone page, docs/demo/index.html, and the survival model's browser code, docs/demo/survival.js.

The page shows TracerNet's committed results only and runs no model. The survival model's browser code that the
page once ran live is kept, unchanged and not loaded by the page, in docs/demo/survival.js. These tests run it
under node, as tests/test_risk.py does with visuals/architecture.html, and hold it to the Python: the normal CDF
to scipy, the risk to the bundle's arrays, the expected arrival to src/models/risk.arrival_quantile, both alert
rules to the bundle's alert minutes for every specimen, and the threshold recomputation to the bundle's grid of 20
operating points. Beat Tracer's pure functions stay in the page's <script id="core"> block (tests/test_demo_game.py).
The 3D bench folder is checked as a byte-identical copy that loads nothing from elsewhere. Static checks keep the
page self-contained, in the contract's section order and honest, and a headless Chrome check loads it at phone
and laptop widths. The browser check needs node, Playwright (from visuals/exhibit-3d/node_modules) and Google
Chrome, and is skipped without them. Set TRACER_DEMO_SHOTS to a folder outside the repository to keep its
screenshots.
"""

import hashlib
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from scipy.special import ndtr

from src.models import risk

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "docs" / "demo"
PAGE = DEMO / "index.html"
SURVIVAL = DEMO / "survival.js"
BUNDLE = DEMO / "bundle.json"
BRIEFS = DEMO / "briefs.json"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
SVG_NAMESPACE = "http://www.w3.org/2000/svg"

# The survival model with the bundle loaded and every specimen prepared the way the page once prepared it.
PRELUDE = """
var fs = require("fs");
var B = JSON.parse(fs.readFileSync(DATA.bundle, "utf8"));
var H = B.headline_index, THETA = B.grid[H].model.harm_threshold, mixes = {};
function mixOf(s) { return mixes[s.group] || (mixes[s.group] = C.mixture(B.model, s.group)); }
function weightOf(s) { return B.triage.weights[String(s.tier)]; }
function seriesOf(s) { return s._series || (s._series = C.riskSeries(s._ticks, mixOf(s), s.window_min)); }
function planThresholdOf(s) { return B.baselines.plan.threshold_min_by_group[s.group][H]; }
B.specimens.forEach(function (s) {
  s._c = C.civilMinutes(s.collected_at);
  s._ticks = C.ticksFor(s.window_min, s.received_min, B.tick_min);
});
var byId = {};
B.specimens.forEach(function (s) { byId[s.id] = s; });
"""


def page_html() -> str:
    return PAGE.read_text(encoding="utf-8")


def core_js() -> str:
    html = page_html()
    start = html.index('<script id="core">') + len('<script id="core">')
    return html[start:html.index("</script>", start)]


def run_core(harness: str, data: dict | None = None, prelude: bool = True):
    """Run docs/demo/survival.js and the page's core under node with `harness` after them. C holds both
    (TracerSurvival and the page's TracerCore; the two functions they share are the same code). `data` arrives
    as the global DATA, with the bundle's path in DATA.bundle; the harness prints one JSON value."""
    payload = {"bundle": str(BUNDLE), **(data or {})}
    with tempfile.TemporaryDirectory() as tmp:
        data_path, script = Path(tmp) / "data.json", Path(tmp) / "run.js"
        data_path.write_text(json.dumps(payload), encoding="utf-8")
        script.write_text(SURVIVAL.read_text(encoding="utf-8") + "\n" + core_js()
                          + "\nvar C = Object.assign({}, TracerSurvival, TracerCore);"
                          + f"\nvar DATA = JSON.parse(require('fs').readFileSync({json.dumps(str(data_path))}, 'utf8'));\n"
                          + "function out(x) { console.log(JSON.stringify(x)); }\n"
                          + (PRELUDE if prelude else "") + harness, encoding="utf-8")
        done = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.fixture(scope="module")
def bundle():
    return json.loads(BUNDLE.read_text(encoding="utf-8"))


# ---- the survival model (docs/demo/survival.js) against the Python ----

@needs_node
def test_the_normal_cdf_matches_scipy_to_1e_12_from_minus_8_to_8():
    rng = random.Random(7)
    zs = [-8 + i / 1000 for i in range(16001)] + [rng.uniform(-8, 8) for _ in range(2000)]
    got = run_core("console.log(JSON.stringify(DATA.z.map(C.Phi)));", {"z": zs}, prelude=False)
    want = ndtr(zs)
    assert max(abs(g - w) for g, w in zip(got, want)) < 1e-12
    # the lower tail is the survival of a long silence: it holds to 1e-12 relative too, not only absolute
    assert max(abs(g - w) / w for z, g, w in zip(zs, got, want) if z <= 0) < 1e-12


@needs_node
def test_the_page_risk_matches_the_python_risk_in_the_bundle(bundle):
    rng = random.Random(11)
    scored = [s for s in bundle["specimens"] if s["risk"]]
    states = [(s["id"], i) for s in rng.sample(scored, 50) for i in [rng.randrange(len(s["risk"]))]]
    states += [(s["id"], 0) for s in scored] + [(s["id"], len(s["risk"]) - 1) for s in scored]
    got = run_core("""
      out({at: DATA.states.map(function (st) {
             var s = byId[st[0]];
             return C.risk(s._ticks[st[1]], mixOf(s), s.window_min);
           }),
           worstSeries: B.specimens.reduce(function (worst, s) {
             var mine = seriesOf(s);
             return s.risk.reduce(function (w, r, i) { return Math.max(w, Math.abs(mine[i] - r)); }, worst);
           }, 0)});
    """, {"states": states})
    by_id = {s["id"]: s for s in bundle["specimens"]}
    want = [by_id[sid]["risk"][i] for sid, i in states]
    assert len(states) == 50 + 2 * len(scored)
    assert max(abs(g - w) for g, w in zip(got["at"], want)) < 1e-9
    assert got["worstSeries"] < 1e-9  # and every value the bundle stores, through the page's running maximum
    assert min(want) < 0.05 and max(want) == 1.0  # the states cover the whole range of risk


@needs_node
def test_the_page_expected_arrival_matches_python_arrival_quantile(bundle):
    rng = random.Random(5)
    silent = [s for s in bundle["specimens"] if s["risk"]]
    states = []
    for s in rng.sample(silent, 30):
        tick = 5 * (1 + rng.randrange(len(s["risk"])))
        states.append((s["id"], tick, rng.choice((0.5, 0.9))))
    got = run_core("out(DATA.states.map(function (st) { return C.arrivalQuantile(st[1], mixOf(byId[st[0]]), st[2]); }));",
                   {"states": states})
    by_id = {s["id"]: s for s in bundle["specimens"]}
    want = [risk.arrival_quantile(t, risk.mixture_from(bundle["model"], by_id[sid]["group"]), q) for sid, t, q in states]
    assert max(abs(g - w) for g, w in zip(got, want)) < 1e-6
    assert len({q for _, _, q in states}) == 2 and max(want) > 3 * min(want)


@needs_node
def test_the_page_expected_arrival_and_role_match_every_stored_search_brief():
    briefs = json.loads(BRIEFS.read_text(encoding="utf-8"))["briefs"]
    got = run_core("""
      out(Object.keys(DATA.ids).map(function (id) {
        var s = byId[id], a = s.alerts.model, mix = mixOf(s);
        return [id, C.arrivalQuantile(a, mix, 0.5), C.arrivalQuantile(a, mix, 0.9),
                C.roleFor(B.triage.routing, s.route, s.places[a / B.tick_min - 1], B.place_order)];
      }));
    """, {"ids": {sid: 1 for sid in briefs}})
    assert len(got) == len(briefs) == 806
    for sid, q50, q90, role in got:
        inputs = briefs[sid]["inputs"]
        assert (round(q50), round(q90)) == (inputs["expected_arrival_minutes_from_now"]["median"],
                                            inputs["expected_arrival_minutes_from_now"]["latest_likely"]), sid
        assert role == inputs["role"], sid


@needs_node
def test_both_alerts_recomputed_from_the_bundle_risk_arrays_match_for_every_specimen(bundle):
    got = run_core("""
      var bad = {model: [], plan: []}, fired = 0;
      B.specimens.forEach(function (s) {
        var m = C.modelAlert(s.risk, s._ticks, weightOf(s), THETA);
        var p = C.planAlert(s._ticks, planThresholdOf(s));
        if (m !== s.alerts.model) bad.model.push([s.id, m, s.alerts.model]);
        if (p !== s.alerts.plan_baseline) bad.plan.push([s.id, p, s.alerts.plan_baseline]);
        if (m != null) fired++;
      });
      out({bad: bad, fired: fired, n: B.specimens.length});
    """)
    assert got["n"] == len(bundle["specimens"]) == 2378
    assert got["bad"] == {"model": [], "plan": []}
    assert got["fired"] == sum(s["alerts"]["model"] is not None for s in bundle["specimens"]) > 0


@needs_node
def test_the_alert_fires_at_the_minute_python_reports_when_the_page_computes_the_risk_itself(bundle):
    got = run_core("""
      out(B.specimens.filter(function (s) {
        return C.modelAlert(seriesOf(s), s._ticks, weightOf(s), THETA) !== s.alerts.model;
      }).map(function (s) { return s.id; }));
    """)
    assert got == []


@needs_node
def test_the_ranking_puts_the_highest_harm_first_and_breaks_ties_by_id():
    rows = [{"id": "SP-b", "harm": 0.5}, {"id": "SP-a", "harm": 0.5}, {"id": "SP-c", "harm": 0.9},
            {"id": "SP-d", "harm": None}, {"id": "SP-e", "harm": 0.1}, {"id": "SP-0", "harm": None}]
    orders = [rows[i:] + rows[:i] for i in range(len(rows))] + [rows[::-1]]
    got = run_core("out(DATA.orders.map(function (rows) { return C.rankByHarm(rows).map(function (r) { return r.id; }); }));",
                   {"orders": orders}, prelude=False)
    assert all(order == ["SP-c", "SP-a", "SP-b", "SP-e", "SP-0", "SP-d"] for order in got)


# ---- the threshold control (P4) ----

GRID_CTX = """
var GRID_CTX = {points: B.grid.length, shifts: B.source.shifts,
  windowOf: function (s) { return s._window; }, ticksOf: function (s) { return s._ticks; },
  seriesOf: function (s) { return s._series || (s._series = C.riskSeries(s._ticks, mixOf(s), s._window)); },
  weightOf: weightOf, theta: function (i) { return B.grid[i].model.harm_threshold; },
  planThreshold: function (s, i) { return B.baselines.plan.threshold_min_by_group[s.group][i]; }};
"""
METRICS = ("breaches", "caught", "median_warning_min", "median_warning_all_breaches_min", "false_alerts")


@needs_node
def test_the_page_recomputes_both_methods_at_all_20_grid_points_as_the_evaluation_did(bundle):
    got = run_core(GRID_CTX + "C.applyWindow(B.specimens, null, B.tick_min); out(C.operatingGrid(B.specimens, GRID_CTX));")
    assert len(got) == len(bundle["grid"]) == 20
    for mine, point in zip(got, bundle["grid"]):
        for key, method in (("model", "model"), ("plan", "plan_baseline")):
            want = point[method]
            assert {k: mine[key][k] for k in METRICS} == {k: want[k] for k in METRICS}, (point["budget_per_shift"], key)
            assert abs(mine[key]["alerts_per_shift"] - want["alerts_per_shift"]) < 6e-5  # the grid keeps four decimals


@needs_node
def test_a_miss_is_read_from_the_receipt_scan_as_the_evaluation_derives_it(bundle):
    got = run_core("out(B.specimens.map(function (s) { return C.isMiss(s.received_min, s.window_min); }));")
    assert got == [s["truth"]["breach"] for s in bundle["specimens"]]
    assert run_core("out([C.median([]), C.median([3]), C.median([4, 1, 3]), C.median([4, 1, 3, 2]), C.median([0, 0, 5, 7])]);",
                    prelude=False) == [None, 3, 3, 2.5, 2.5]


def threshold_sentence(model: dict, plan: dict, window: int | None) -> str:
    def warn(v):
        return "no" if v is None else str(round(v)) if abs(v - round(v)) < 1e-9 else f"{v:.1f}"

    def part(who, m):
        return f"{who} warns {warn(m['median_warning_all_breaches_min'])} min ahead and alerts {m['alerts_per_shift']:.1f} times a shift"
    lead = "At this setting " if window is None else f"At this setting, with every window set to {window} min, "
    return lead + part("Tracer", model) + "; " + part("the plan's rule", plan) + "."


@needs_node
def test_the_threshold_sentence_states_the_grid_numbers_at_every_point(bundle):
    got = run_core("out(B.grid.map(function (g) { return C.thresholdSentence(g.model, g.plan_baseline, null); }));")
    want = [threshold_sentence(g["model"], g["plan_baseline"], None) for g in bundle["grid"]]
    assert got == want
    assert len(set(got)) >= 15  # the sentence moves with the threshold
    g = bundle["grid"][bundle["headline_index"]]
    assert f"Tracer warns {g['model']['median_warning_all_breaches_min']:g} min ahead" in got[bundle["headline_index"]]
    over = run_core("out(C.thresholdSentence({median_warning_all_breaches_min: 12.5, alerts_per_shift: 5.21},"
                    " {median_warning_all_breaches_min: 0, alerts_per_shift: 1.04}, 90));", prelude=False)
    assert over == ("At this setting, with every window set to 90 min, Tracer warns 12.5 min ahead and alerts 5.2 times a shift; "
                    "the plan's rule warns 0 min ahead and alerts 1.0 times a shift.")


@needs_node
def test_one_window_for_every_specimen_recomputes_ticks_and_risk_and_drops_the_cached_series(bundle):
    got = run_core(GRID_CTX + """
      C.applyWindow(B.specimens, null, B.tick_min);
      var s = B.specimens.filter(function (x) { return x.window_min === 120 && x.received_min == null; })[0];
      var own = GRID_CTX.seriesOf(s).slice(), ownTicks = s._ticks.slice();
      C.applyWindow(B.specimens, 90, B.tick_min);
      var cleared = B.specimens.every(function (x) { return x._series === null && x._window === 90; });
      var lastTick = Math.max.apply(null, B.specimens.map(function (x) { return x._ticks.length ? x._ticks[x._ticks.length - 1] : 0; }));
      var at90 = GRID_CTX.seriesOf(s).slice(), ticks90 = s._ticks.slice();
      var grid90 = C.operatingGrid(B.specimens, GRID_CTX)[H];
      // a specimen silent at 100 minutes is past a 90-minute window and still inside a 120-minute one
      var t = {_c: 0, window_min: 120, received_min: null, _window: 90};
      var closed90 = C.outstanding([t], 100, 480, function (x) { return x._window; }).live[0].closed;
      var closedOwn = C.outstanding([t], 100, 480).live[0].closed;
      C.applyWindow(B.specimens, null, B.tick_min);
      var back = GRID_CTX.seriesOf(s);
      out({cleared: cleared, lastTick: lastTick, own: own, ownTicks: ownTicks, at90: at90, ticks90: ticks90,
           back: back, stored: s.risk, grid90: grid90, closed90: closed90, closedOwn: closedOwn,
           restored: B.specimens.every(function (x) { return x._window === x.window_min; })});
    """)
    assert got["cleared"] and got["restored"]
    assert got["lastTick"] == 90 and got["ticks90"][-1] == 90 and got["ownTicks"][-1] == 120
    # the same silence carries more risk when the window is shorter
    assert all(a > o for a, o in zip(got["at90"], got["own"]))
    assert max(abs(b - r) for b, r in zip(got["back"], got["stored"])) < 1e-9
    headline = bundle["grid"][bundle["headline_index"]]["model"]
    assert got["grid90"]["model"]["breaches"] != headline["breaches"]  # which specimens miss depends on the window
    assert got["closed90"] is True and got["closedOwn"] is False


# ---- the page itself ----

SECTIONS = ["top", "watch", "beat", "warning", "limits"]
REPO = "https://github.com/scalinity/Tracer"


def test_the_page_loads_nothing_from_another_origin():
    html = page_html()
    for value in re.findall(r"""\b(?:src|action|poster|srcset)\s*=\s*["']([^"']*)""", html):
        assert not re.match(r"(?i)\s*(?:https?:)?//", value), value
    # the one address on another origin is the repository, a link a visitor may follow, never fetched
    hrefs = re.findall(r"""\bhref\s*=\s*["']([^"']*)""", html)
    assert [h for h in hrefs if re.match(r"(?i)\s*(?:https?:)?//", h)] == [REPO]
    assert set(re.findall(r"(?i)https?://[^\s\"'`)<>]+", html)) <= {REPO, SVG_NAMESPACE}  # a namespace, never fetched
    # relative addresses only, so the page works under /Tracer/ on GitHub Pages
    assert not [h for h in hrefs if h.startswith("/")]
    scripts = "".join(re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.S))
    assert sorted(re.findall(r"""\bgetJson\(\s*["']([^"']+)["']""", scripts)) == ["game.json", "results.json"]
    assert scripts.count("fetch(") == 1  # inside getJson
    assert not re.search(r"<script\b[^>]*\bsrc=|<link\b[^>]*\bstylesheet|@import|\bimport\s*\(|\bimportScripts\b", html)


def test_the_page_runs_no_model_and_shows_no_survival_model_output():
    html = re.sub(r"/\*.*?\*/", "", page_html(), flags=re.S)  # its comments may name the file the model moved to
    for gone in ("bundle.json", "neural.json", "briefs.json", "survival.js", "TracerSurvival", "riskSeries", "harm_threshold"):
        assert gone not in html, gone
    for gone in ('id="board"', 'id="timeline"', 'id="threshold"', 'id="activity"', 'id="why"', 'type="range"'):
        assert gone not in html, gone


def test_the_page_never_says_the_clock_stops_at_plating():
    for text in (page_html().lower(), SURVIVAL.read_text(encoding="utf-8").lower()):
        assert "plated" not in text and "plating" not in text and "set-up" not in text


def test_the_sections_come_in_the_contract_order_with_one_synthetic_line_on_the_page():
    html = page_html()
    at = [html.index(f'id="{s}"') for s in SECTIONS]
    assert at == sorted(at)
    # once on the page; Beat Tracer alone (#beat) carries its own copy, shown only there
    body = html[html.index("<body>"):]
    assert body.count("Synthetic data, no clinical review.") == 2 and body.count('class="bt-note"') == 1
    assert "body:not(.play) .bt-back,body:not(.play) .bt-note{display:none}" in html
    assert "Tracer warns the lab when a specimen goes quiet." in html
    assert "One will be late. Tap it before Tracer does." in html
    assert "From one offline run on 776 synthetic lab weeks." in html
    jumps = html[html.index('class="jumps"'):html.index("</nav>")]
    assert re.findall(r'href="([^"]+)"', jumps) == ["#watch", "#beat", "#warning", "#limits"]
    watch = html[html.index('id="watch"'):html.index("</section>", html.index('id="watch"'))]
    assert re.findall(r'<a [^>]*href="([^"]+)"', watch) == ["./play/"]
    limits = html[html.index('id="limits"'):html.index("</section>", html.index('id="limits"'))]
    assert re.findall(r'href="([^"]+)"', limits) == ["../explained/short/", REPO]
    for word in ("capped mean", "operating point", "tier", "Brier", "p_miss", "held-out", "harm threshold"):
        assert word.lower() not in re.sub(r"<script\b.*?</script>|<style>.*?</style>|<!--.*?-->", "", html, flags=re.S).lower(), word


def test_the_core_never_touches_the_page():
    html = page_html()
    assert html.count('<script id="core">') == 1
    pattern = r"\b(?:window|document|navigator|localStorage|sessionStorage|location)\.[A-Za-z_$]|\bfetch\("
    assert not re.search(pattern, core_js())
    assert not re.search(pattern, SURVIVAL.read_text(encoding="utf-8"))


def test_the_page_holds_no_key_and_stays_small():
    html = page_html()
    assert not re.search(r"sk-ant-|ANTHROPIC_API_KEY|(?i:x-api-key)|(?i:api[_-]?key)\s*[:=]", html)
    assert PAGE.stat().st_size < 60_000


# ---- the 3D bench folder (P6) and its link's pure functions, kept in survival.js ----

EXHIBIT = DEMO / "exhibit"
EXHIBIT_SOURCE = ROOT / "visuals" / "exhibit-3d" / "dist"
# Addresses written inside the built exhibit's script. None is fetched (the browser check watches every request):
# two XML namespaces, a link in one of three.js's error messages, the credit's link a visitor may follow, and the
# source credited in a shader comment.
EXHIBIT_URLS = {"http://www.w3.org/1999/xhtml", SVG_NAMESPACE, "https://github.com/mrdoob/three.js/issues/32012",
                "https://github.com/scalinity", "https://www.shadertoy.com/view/WtyXRy"}


def exhibit_files(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def exhibit_text() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in EXHIBIT.rglob("*") if p.suffix in (".html", ".js", ".css", ".svg")}


def test_the_exhibit_folder_is_a_byte_identical_copy_of_the_built_exhibit():
    copied, built = exhibit_files(EXHIBIT), exhibit_files(EXHIBIT_SOURCE)
    assert copied == built and "index.html" in copied and len(copied) >= 10
    # built with base './', so it works from any folder
    assert re.findall(r"""(?:src|href)="([^"]+)\"""", (EXHIBIT / "index.html").read_text(encoding="utf-8")) and all(
        v.startswith("./") for v in re.findall(r"""(?:src|href)="([^"]+)\"""", (EXHIBIT / "index.html").read_text(encoding="utf-8")))


def test_the_exhibit_loads_nothing_from_another_origin_and_holds_no_key():
    texts = exhibit_text()
    assert set(texts) >= {"index.html", "favicon.svg"} and any(name.endswith(".js") for name in texts)
    found = set()
    for name, text in texts.items():
        assert not re.search(r"sk-ant-|ANTHROPIC_API_KEY|(?i:x-api-key)|(?i:api[_-]?key)\s*[:=]", text), name
        found |= set(re.findall(r"(?i)https?://[^\s\"'`)<>\\]+", text))
        assert not re.search(r"""\bimport\s*\(\s*["'`]https?:|<script\b[^>]*\bsrc=["']?(?:https?:)?//|@import\s+url\(\s*["']?(?:https?:)?//""", text), name
    assert found <= EXHIBIT_URLS, found - EXHIBIT_URLS
    assert all(not re.match(r"(?i)\s*(?:https?:)?//", v)
               for v in re.findall(r"""\b(?:src|href)\s*=\s*["']([^"']*)""", texts["index.html"]))


def test_the_exhibit_never_says_the_clock_stops_at_plating():
    for name, text in exhibit_text().items():
        lower = text.lower()
        assert "plated" not in lower and "set-up" not in lower, name
        # its one mention of plating says plating is outside the clock
        for m in re.finditer(r"plating", lower):
            assert lower[max(0, m.start() - 40):m.start()].endswith("nothing after the receipt scan, "), (name, lower[m.start() - 60:m.end() + 20])
    # three.js names a method setup, so the word is checked in the exhibit's own page rather than its script
    assert "setup" not in exhibit_text()["index.html"].lower()


@needs_node
def test_the_3d_link_takes_its_fate_from_the_scans_until_the_truth_is_shown():
    got = run_core("""
      var obs = DATA.obs.map(function (o) { return C.observedFate(o[0], o[1]); });
      var tru = DATA.tru.map(function (o) { return C.trueFate(o[0], o[1], o[2]); });
      var q = DATA.q.map(C.exhibitQuery);
      out({obs: obs, tru: tru, q: q});
    """, {"obs": [[20, None], [67, None], [68, None], [90, 120], [30, 27], [80, 70], [70, 70]],
          "tru": [["lost_in_tube", None, 60], ["not_dispatched", None, 60], ["stalled_at_station", 90, 120], ["misrouted", 50, 60],
                  ["none", 40, 60], ["none", 75, 60], ["content_failure", 30, 1440]],
          "q": [{"fate": "stalls", "silence": 62, "window": 120, "alert": 0.45, "truth": False},
                {"fate": "lost", "silence": 131, "window": 1440, "alert": 0.046, "truth": True},
                {"fate": "arrives", "silence": 0, "window": 60, "alert": 0.9, "truth": False},
                {"fate": "stalls", "silence": 62.4, "window": 240, "alert": 0.3333, "truth": False}]}, prelude=False)
    assert got["obs"] == ["stalls", "stalls", "lost", "lost", "arrives", "stalls", "stalls"]
    assert got["tru"] == ["lost", "unsent", "stalls", "stalls", "arrives", "stalls", "arrives"]
    assert got["q"] == ["?fate=stalls&silence=62&window=120&alert=0.45&truth=0", "?fate=lost&truth=1",
                        "?fate=arrives&silence=0&window=60&alert=0.9&truth=0", "?fate=stalls&silence=62&window=240&alert=0.33&truth=0"]


# ---- in a browser ----

def playwright_dir() -> Path | None:
    """Playwright from the 3D exhibit's node_modules, here or, from a git worktree, in the main checkout."""
    candidates = [ROOT / "visuals" / "exhibit-3d" / "node_modules" / "playwright"]
    try:
        common = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, timeout=10).stdout.strip()
        if common:
            candidates.append(Path(common).parent / "visuals" / "exhibit-3d" / "node_modules" / "playwright")
    except (OSError, subprocess.SubprocessError):
        pass
    return next((c for c in candidates if (c / "package.json").exists()), None)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


BROWSER_CHECK = """
// $eval and $$eval below are Playwright's element queries, not JavaScript's eval.
const { chromium } = require(PLAYWRIGHT);
(async () => {
  let browser;
  try { browser = await chromium.launch({ channel: "chrome", headless: true }); }
  catch (e) { console.log(JSON.stringify({ skip: "Google Chrome did not start: " + String(e.message).split("\\n")[0] })); return; }
  const result = { runs: [] };
  for (const [width, height, scheme] of [[390, 844, "dark"], [1440, 900, "light"]]) {
    const context = await browser.newContext({ viewport: { width, height }, colorScheme: scheme });
    const page = await context.newPage();
    const run = { width, height, messages: [], errors: [], foreign: [], asked: [] };
    page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") run.messages.push(m.type() + ": " + m.text()); });
    page.on("pageerror", (e) => run.errors.push(String(e)));
    page.on("request", (r) => {
      const u = r.url();
      if (u.startsWith(BASE + "/")) run.asked.push(u.slice(BASE.length));
      else if (!u.startsWith("data:")) run.foreign.push(u);
    });
    await page.goto(BASE + "/demo/index.html", { waitUntil: "load" });
    await page.waitForSelector("#w-bars .bar", { timeout: 15000 });
    await page.waitForSelector("#game-go:not([disabled])", { timeout: 15000 });
    run.overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    // every section: its id, its height, the height its content takes, and its visible words
    run.sections = await page.evaluate(() => Array.from(document.querySelectorAll("#top, main > section")).map((e) => {
      const kids = Array.from(e.children).filter((c) => c.getClientRects().length);
      const top = Math.min(...kids.map((c) => c.getBoundingClientRect().top)), bottom = Math.max(...kids.map((c) => c.getBoundingClientRect().bottom));
      return { id: e.id, height: e.getBoundingClientRect().height, content: bottom - top,
               words: e.innerText.split(/\\s+/).filter((w) => /[A-Za-z0-9]/.test(w)).length };
    }));
    await page.screenshot({ path: SHOTS + "/page-" + width + ".png", fullPage: true });
    // the #warning picker: each of 1, 2, 4 and 6 alerts a shift
    run.picks = {};
    for (const n of [1, 2, 4, 6]) {
      await page.click('#w-bars .bar[data-n="' + n + '"]');
      run.picks[n] = { minutes: await page.textContent("#w-min"), falseAlarms: await page.textContent("#w-fa"),
        caption: await page.textContent("#w-cap"),
        pressed: await page.$$eval("#w-bars .bar", (b) => b.filter((x) => x.getAttribute("aria-pressed") === "true").map((x) => x.dataset.n)) };
    }
    run.targets = await page.$$eval("#w-bars .bar, .jumps a, #watch .cta, #game-go", (els) => els.map((e) => e.getBoundingClientRect().height));
    run.barHeights = await page.$$eval("#w-bars .bar i", (els) => els.map((e) => e.getBoundingClientRect().height));
    result.runs.push(run);
    await context.close();
  }
  // the default is 6 alerts a shift, before anything is tapped
  {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await context.newPage();
    await page.goto(BASE + "/demo/index.html", { waitUntil: "load" });
    await page.waitForSelector("#w-bars .bar");
    result.first = { minutes: await page.textContent("#w-min"), budget: await page.evaluate(() => window.TracerDemo.warningBudget()) };
    await context.close();
  }
  // a data file that does not load says so
  for (const name of ["results.json", "game.json"]) {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await context.newPage();
    await page.route("**/" + name, (route) => route.abort());
    await page.goto(BASE + "/demo/index.html", { waitUntil: "load" });
    await page.waitForTimeout(1500);
    result["blocked_" + name] = { warning: await page.textContent("#w-fa"), go: await page.textContent("#game-go"), status: await page.textContent("#game-status") };
    await context.close();
  }
  await browser.close();
  console.log(JSON.stringify(result));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
def test_the_page_works_in_headless_chrome_at_phone_and_laptop_widths(tmp_path):
    playwright = playwright_dir()
    if playwright is None:
        pytest.skip("Playwright is not installed in visuals/exhibit-3d/node_modules")
    shots = Path(os.environ.get("TRACER_DEMO_SHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    port = free_port()
    server = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(ROOT / "docs")],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.05)
        script = tmp_path / "check.js"
        script.write_text(f"const PLAYWRIGHT = {json.dumps(str(playwright))}, BASE = {json.dumps(f'http://127.0.0.1:{port}')}, "
                          f"SHOTS = {json.dumps(str(shots))};\n" + BROWSER_CHECK, encoding="utf-8")
        done = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=120)
    finally:
        server.terminate()
        server.wait(timeout=10)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    if "skip" in result:
        pytest.skip(result["skip"])
    want = {1: ("3.1", "0.2"), 2: ("8.2", "0.6"), 4: ("16.4", "1.6"), 6: ("23.3", "2.9")}
    for run in result["runs"]:
        assert run["messages"] == [] and run["errors"] == [] and run["foreign"] == [], run
        # the page reads its two small files and nothing else: no bundle, no neural file, no model
        assert sorted(set(run["asked"])) == ["/demo/game.json", "/demo/index.html", "/demo/results.json"], run["asked"]
        assert run["overflow"] <= 0, run
        assert [s["id"] for s in run["sections"]] == SECTIONS
        for s in run["sections"]:
            # one phone screen each: the content fits the viewport, and no section is taller than it
            assert s["content"] <= run["height"] and s["height"] <= run["height"] + 1, (run["width"], s)
            assert s["words"] <= 42, (run["width"], s)
        for n, (minutes, false_alarms) in want.items():
            pick = run["picks"][str(n)]
            assert pick["minutes"] == minutes and pick["falseAlarms"] == f"{false_alarms} false alarms a shift", pick
            assert pick["pressed"] == [str(n)] and f"at {n} alert" in pick["caption"], pick
        assert all(h >= 44 for h in run["targets"]), run["targets"]
        # each bar's height is in proportion to its minutes of warning (none squeezed to the same height)
        tallest = max(run["barHeights"])
        for h, b in zip(run["barHeights"], json.loads((ROOT / "docs" / "demo" / "results.json").read_text(encoding="utf-8"))["budgets"]):
            assert h == pytest.approx(tallest * b["warning_min"] / max(x["warning_min"] for x in json.loads((ROOT / "docs" / "demo" / "results.json").read_text(encoding="utf-8"))["budgets"]), abs=2), run["barHeights"]
    assert result["first"] == {"minutes": "23.3", "budget": 6}
    assert "did not load" in result["blocked_results.json"]["warning"] and "results.json" in result["blocked_results.json"]["warning"]
    assert result["blocked_game.json"]["go"] == "The game could not load" and "game.json" in result["blocked_game.json"]["status"]
