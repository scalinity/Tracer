"""The public demo page, docs/demo/index.html (plan tasks P2 to P4).

The page keeps its arithmetic in one <script id="core"> block that never touches the page. These tests slice
that block out and run it under node, as tests/test_risk.py does with visuals/architecture.html, and hold it to
the Python: the normal CDF to scipy, the risk to the bundle's arrays, the expected arrival to
src/models/risk.arrival_quantile, both alert rules to the bundle's alert minutes for every specimen, and the
threshold control's recomputation to the bundle's grid of 20 operating points.
Static checks keep the page self-contained and honest, and a headless Chrome check loads it at phone and laptop
widths. The browser check needs node, Playwright (from visuals/exhibit-3d/node_modules) and Google Chrome, and
is skipped without them. Set TRACER_DEMO_SHOTS to a folder outside the repository to keep its screenshots.
"""

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
BUNDLE = DEMO / "bundle.json"
BRIEFS = DEMO / "briefs.json"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
SVG_NAMESPACE = "http://www.w3.org/2000/svg"

# The page's core with the bundle loaded and every specimen prepared the way the page prepares it.
PRELUDE = """
var C = TracerCore, fs = require("fs");
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
    """Run the page's core under node with `harness` after it. `data` arrives as the global DATA, with the
    bundle's path in DATA.bundle; the harness prints one JSON value."""
    payload = {"bundle": str(BUNDLE), **(data or {})}
    with tempfile.TemporaryDirectory() as tmp:
        data_path, script = Path(tmp) / "data.json", Path(tmp) / "run.js"
        data_path.write_text(json.dumps(payload), encoding="utf-8")
        script.write_text(core_js() + f"\nvar DATA = JSON.parse(require('fs').readFileSync({json.dumps(str(data_path))}, 'utf8'));\n"
                          + "function out(x) { console.log(JSON.stringify(x)); }\n"
                          + (PRELUDE if prelude else "") + harness, encoding="utf-8")
        done = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.fixture(scope="module")
def bundle():
    return json.loads(BUNDLE.read_text(encoding="utf-8"))


# ---- the core against the Python ----

@needs_node
def test_the_normal_cdf_matches_scipy_to_1e_12_from_minus_8_to_8():
    rng = random.Random(7)
    zs = [-8 + i / 1000 for i in range(16001)] + [rng.uniform(-8, 8) for _ in range(2000)]
    got = run_core("console.log(JSON.stringify(DATA.z.map(TracerCore.Phi)));", {"z": zs}, prelude=False)
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
    got = run_core("out(DATA.orders.map(function (rows) { return TracerCore.rankByHarm(rows).map(function (r) { return r.id; }); }));",
                   {"orders": orders}, prelude=False)
    assert all(order == ["SP-c", "SP-a", "SP-b", "SP-e", "SP-0", "SP-d"] for order in got)


@needs_node
def test_the_default_moment_is_the_first_that_meets_the_rule_stated_beside_it():
    html = page_html()
    day = re.search(r'DEFAULT_DAY = "(\d{4}-\d{2}-\d{2})"', html).group(1)
    hours, minutes = map(int, re.search(r"DEFAULT_MINUTE = (\d+) \* 60 \+ (\d+)", html).groups())
    got = run_core("""
      var days = {};
      B.specimens.forEach(function (s) { days[s.collected_at.slice(0, 10)] = s.run; });
      var ctx = {tickMin: B.tick_min, threshold: THETA, seriesOf: seriesOf, weightOf: weightOf, planThresholdOf: planThresholdOf};
      var byRun = {};
      B.specimens.forEach(function (s) { (byRun[s.run] = byRun[s.run] || []).push(s); });
      function board(date, minute) {
        return C.boardRows(C.outstanding(byRun[days[date]], C.civilMinutes(date) + minute, B.shift_min).live, ctx);
      }
      function meets(rows) {
        if (rows.length < 4) return false;
        if (rows.some(function (r) { return r.closed || r.t > B.horizon_min; })) return false;
        var top = rows[0];
        return top.spec.tier === 1 && top.model != null && top.plan == null && top.spec.truth.breach;
      }
      var first = null;
      Object.keys(days).sort().some(function (date) {
        for (var m = 8 * 60; m < 18 * 60; m += B.tick_min) if (meets(board(date, m))) { first = [date, m]; return true; }
        return false;
      });
      var rows = board(DATA.day, DATA.minute);
      out({first: first, rows: rows.length,
           tracerAlerted: rows.filter(function (r) { return r.model != null; }).length,
           planQuiet: rows.filter(function (r) { return r.tick != null && r.plan == null; }).length});
    """, {"day": day, "minute": hours * 60 + minutes})
    assert got["first"] == [day, hours * 60 + minutes]
    assert got["rows"] >= 3 and got["tracerAlerted"] >= 1 and got["planQuiet"] >= 1


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
    assert run_core("var C = TracerCore; out([C.median([]), C.median([3]), C.median([4, 1, 3]), C.median([4, 1, 3, 2]), C.median([0, 0, 5, 7])]);",
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
    over = run_core("out(TracerCore.thresholdSentence({median_warning_all_breaches_min: 12.5, alerts_per_shift: 5.21},"
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

def test_the_page_loads_nothing_from_another_origin():
    html = page_html()
    for value in re.findall(r"""\b(?:src|href|action|poster|srcset)\s*=\s*["']([^"']*)""", html):
        assert not re.match(r"(?i)\s*(?:https?:)?//", value), value
    assert set(re.findall(r"(?i)https?://[^\s\"'`)<>]+", html)) <= {SVG_NAMESPACE}  # a namespace, never fetched
    scripts = "".join(re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.S))
    assert sorted(re.findall(r"""\bfetch\(\s*["']([^"']+)["']""", scripts)) == ["briefs.json", "bundle.json"]
    assert not re.search(r"<script\b[^>]*\bsrc=|<link\b[^>]*\bstylesheet|@import|\bimport\s*\(|\bimportScripts\b", html)


def test_the_page_never_draws_a_clock_that_stops_at_plating():
    text = page_html().lower()
    assert "plated" not in text and "plating" not in text and "set-up" not in text and "setup" not in text
    assert "receipt scan stops the clock" in text and "clock stops" in text


def test_the_page_says_the_data_is_synthetic_and_unreviewed():
    html = page_html()
    assert re.search(r"(?i)\bsynthetic\b", html) and "no clinical review" in html
    assert "Placeholder." in html  # the problem paragraph waits for Karissa's text
    assert all(f'id="{section}"' in html for section in ("problem", "board", "timeline", "threshold", "beat", "why", "limits"))


def test_the_core_is_one_block_that_never_touches_the_page():
    html = page_html()
    assert html.count('<script id="core">') == 1
    assert not re.search(r"\b(?:window|document|navigator|localStorage|sessionStorage|location)\.[A-Za-z_$]|\bfetch\(", core_js())


def test_the_page_holds_no_key_and_stays_small():
    html = page_html()
    assert not re.search(r"sk-ant-|ANTHROPIC_API_KEY|(?i:x-api-key)|(?i:api[_-]?key)\s*[:=]", html)
    assert PAGE.stat().st_size < 200_000


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
  for (const [width, height, scheme] of [[375, 812, "dark"], [1280, 800, "light"]]) {
    const context = await browser.newContext({ viewport: { width, height }, colorScheme: scheme });
    const page = await context.newPage();
    const run = { width, messages: [], errors: [], foreign: [] };
    page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") run.messages.push(m.type() + ": " + m.text()); });
    page.on("pageerror", (e) => run.errors.push(String(e)));
    page.on("request", (r) => { const u = r.url(); if (!u.startsWith(BASE + "/") && !u.startsWith("data:")) run.foreign.push(u); });
    await page.goto(BASE + "/demo/index.html", { waitUntil: "load" });
    await page.waitForSelector("#board-body tr[data-id]", { timeout: 15000 });
    run.rows = await page.$$eval("#board-body tr[data-id]", (rows) => rows.length);
    run.overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    await page.screenshot({ path: SHOTS + "/board-" + width + ".png", fullPage: true });
    run.firstId = await page.$eval("#board-body tr[data-id]", (tr) => tr.getAttribute("data-id"));
    await page.click("#board-body tr[data-id] .sid");
    await page.waitForSelector("#timeline:not([hidden])");
    run.hash = await page.evaluate(() => location.hash);
    run.riskBefore = await page.textContent("#ro-risk");
    await page.focus("#tl-scrub");
    await page.keyboard.press("ArrowRight");
    run.riskAfterKey = await page.textContent("#ro-risk");
    await page.click("#tl-fwd");
    run.riskAfterButton = await page.textContent("#ro-risk");
    await page.waitForFunction(() => document.getElementById("brief-template").textContent.length > 0, null, { timeout: 15000 });
    await page.click("#tl-truth");
    run.truthShown = await page.isVisible("#tl-truth-line");
    run.overflowOpen = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    await page.screenshot({ path: SHOTS + "/timeline-" + width + ".png", fullPage: true });
    // the threshold control: one step of the threshold, then one step of the window, then back to the own windows
    const th = async () => ({ sentence: await page.textContent("#th-sentence"), note: await page.textContent("#th-note"),
      numbers: await page.$$eval("#th-m-warn, #th-m-rate, #th-p-warn, #th-p-rate", (els) => els.map((e) => e.textContent)),
      boardRisk: await page.textContent("#ro-risk") });
    run.th0 = await th();
    await page.focus("#th-point");
    await page.keyboard.press("ArrowLeft");
    run.th1 = await th();
    await page.focus("#th-win");
    await page.keyboard.press("ArrowRight");
    await page.waitForFunction(() => document.getElementById("th-sentence").textContent.includes("every window set to 125 min"));
    run.th2 = await th();
    run.overflowThreshold = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    await page.locator("#threshold").screenshot({ path: SHOTS + "/threshold-" + width + ".png" });
    await page.click("#th-own");
    run.th3 = await th();
    result.runs.push(run);
    await context.close();
  }
  const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
  const page = await context.newPage();
  await page.route("**/bundle.json", (route) => route.abort());
  await page.goto(BASE + "/demo/index.html", { waitUntil: "load" });
  await page.waitForSelector("#load-status.failed", { timeout: 15000 });
  result.blocked = await page.textContent("#load-status");
  await page.screenshot({ path: SHOTS + "/blocked-375.png" });
  await context.close();
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
        done = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=60)
    finally:
        server.terminate()
        server.wait(timeout=10)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    if "skip" in result:
        pytest.skip(result["skip"])
    for run in result["runs"]:
        assert run["messages"] == [] and run["errors"] == [] and run["foreign"] == [], run
        assert run["rows"] >= 3, run
        assert run["hash"] == "#" + run["firstId"]
        assert run["riskBefore"] != run["riskAfterKey"] != run["riskAfterButton"], run
        assert run["truthShown"]
        th0, th1, th2, th3 = run["th0"], run["th1"], run["th2"], run["th3"]
        assert th0["sentence"].startswith("At this setting Tracer warns") and "Synthetic data, no clinical review." in th0["note"]
        assert th1["sentence"] != th0["sentence"] and th1["numbers"] != th0["numbers"], run  # the threshold moved them
        assert th2["sentence"] != th1["sentence"] and th2["numbers"] != th1["numbers"], run  # and so did the window
        assert "Recomputed in this page for a 125-minute window" in th2["note"] and th2["boardRisk"] != th1["boardRisk"]
        assert th3["sentence"] == th1["sentence"] and th3["numbers"] == th1["numbers"]  # own windows give the grid back
    phone = result["runs"][0]
    assert phone["width"] == 375 and phone["overflow"] <= 0 and phone["overflowOpen"] <= 0, phone
    assert phone["overflowThreshold"] <= 0, phone
    assert "did not load" in result["blocked"] and "bundle.json" in result["blocked"]
    assert "python3 -m http.server" in result["blocked"]
