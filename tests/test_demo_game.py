"""Beat Tracer, the game on the phone page (plan task P5).

The game's pure parts live in the page's <script id="core"> block and run here under node, as in
tests/test_demo_page.py: the seeded generator against an independent Python mulberry32, the rounds for seed 18
pinned once, round selection held to the fields it may read and to its rules for every seed tried, and the
scoring of a player and of Tracer. The game reads the slim docs/demo/game.json (scripts/build_demo_game.py),
which must draw the same rounds as the bundle for every seed and carry TracerNet's alerts unchanged. A headless
Chrome run opens the QR code's address, ?seed=18#beat, and plays a whole game in real time: the game alone on
the screen, one tap to start and then one tap per round, nothing to scroll at a phone's visible height, and both
totals added up from the rounds.
"""

import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_demo_page import NODE, ROOT, free_port, needs_node, page_html, playwright_dir, run_core

# The three rounds seed 18 draws from the bundle, generated once by the page and pinned: the room's rounds must
# not change unless the bundle or the drawing rule does.
SEED_18 = [
    {"run": 26, "miss": "SP-r26-000274", "window": 120,
     "ids": ["SP-r26-000273", "SP-r26-000277", "SP-r26-000282", "SP-r26-000274", "SP-r26-000278", "SP-r26-000266"]},
    {"run": 27, "miss": "SP-r27-000152", "window": 60,
     "ids": ["SP-r27-000147", "SP-r27-000145", "SP-r27-000152", "SP-r27-000150", "SP-r27-000160", "SP-r27-000155"]},
    {"run": 29, "miss": "SP-r29-000263", "window": 60,
     "ids": ["SP-r29-000268", "SP-r29-000263", "SP-r29-000254", "SP-r29-000256", "SP-r29-000274", "SP-r29-000261"]},
]


@pytest.fixture(scope="module")
def bundle():
    return json.loads((ROOT / "docs" / "demo" / "bundle.json").read_text(encoding="utf-8"))


def mulberry32(seed: int):
    """mulberry32 written again in Python with explicit 32-bit arithmetic, to check the page's."""
    a = seed & 0xFFFFFFFF

    def imul(x, y):
        return ((x & 0xFFFFFFFF) * (y & 0xFFFFFFFF)) & 0xFFFFFFFF

    def nxt():
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = imul(a ^ (a >> 15), 1 | a)
        t = ((t + imul(t ^ (t >> 7), 61 | t)) & 0xFFFFFFFF) ^ t
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296
    return nxt


@needs_node
def test_the_seeded_generator_is_mulberry32_to_the_last_bit():
    seeds = [0, 1, 18, 99999, 4294967295]
    got = run_core("out(DATA.seeds.map(function (s) { var r = TracerCore.mulberry32(s), o = [];"
                   " for (var i = 0; i < 1000; i++) o.push(r()); return o; }));", {"seeds": seeds}, prelude=False)
    for seed, values in zip(seeds, got):
        r = mulberry32(seed)
        assert values == [r() for _ in range(1000)], seed
        assert all(0 <= v < 1 for v in values)


@needs_node
def test_seed_18_gives_the_pinned_rounds_every_time_and_in_any_input_order():
    got = run_core("""
      var shuffled = B.specimens.slice().reverse();
      out([C.gameRounds(B.specimens, 18), C.gameRounds(B.specimens, 18), C.gameRounds(shuffled, 18), C.gameRounds(B.specimens, 19)]);
    """)
    assert got[0] == got[1] == got[2] == SEED_18
    assert got[3] != SEED_18


@needs_node
def test_round_selection_reads_only_what_the_page_shows_before_the_truth():
    got = run_core("""
      var read = {};
      var watched = B.specimens.map(function (s) {
        return new Proxy(s, { get: function (o, k) { if (typeof k === "string") read[k] = true; return o[k]; } });
      });
      var rounds = [18, 1, 2, 3, 77].map(function (seed) { return C.gameRounds(watched, seed); });
      out({read: Object.keys(read).sort(), same: JSON.stringify(rounds[0]) === JSON.stringify(C.gameRounds(B.specimens, 18))});
    """)
    assert got["read"] == ["collected_at", "id", "received_min", "run", "window_min"]
    assert got["same"]


@needs_node
def test_every_round_has_one_real_miss_and_five_on_time_specimens_from_one_run(bundle):
    seeds = list(range(200)) + [18, 4294967295]
    got = run_core("out(DATA.seeds.map(function (s) { return C.gameRounds(B.specimens, s); }));", {"seeds": seeds})
    by_id = {s["id"]: s for s in bundle["specimens"]}

    def collected(s):
        return time.mktime(time.strptime(s["collected_at"], "%Y-%m-%dT%H:%M:%S"))

    misses = set()
    for seed, rounds in zip(seeds, got):
        assert len(rounds) == 3, seed
        assert len({r["run"] for r in rounds}) == 3, seed  # three different held-out runs
        for r in rounds:
            six = [by_id[i] for i in r["ids"]]
            miss = by_id[r["miss"]]
            assert len(set(r["ids"])) == 6 and r["miss"] in r["ids"]
            assert {s["run"] for s in six} == {r["run"]}
            # the miss, read from its receipt scan: none, or one after its window closed
            assert miss["received_min"] is None or miss["received_min"] > miss["window_min"]
            assert miss["truth"]["breach"] and r["window"] == miss["window_min"] <= 120
            for s in six:
                if s is not miss:
                    assert s["received_min"] is not None and s["received_min"] <= s["window_min"], s["id"]
                    assert not s["truth"]["breach"] and s["window_min"] <= 240
                    assert abs(collected(s) - collected(miss)) <= 240 * 60  # collected within four hours of it
            misses.add(r["miss"])
    assert len(misses) > 40  # the draws cover many of the short-window misses, not a favored few


@needs_node
def test_a_player_scores_the_window_minus_the_minute_of_a_correct_call():
    rounds = {"miss": "M", "window": 60, "ids": ["A", "B", "M", "C", "D", "E"]}
    cases = [
        [],                                                           # no call
        [{"id": "M", "minute": 5}],                                   # correct and early
        [{"id": "M", "minute": 52}],                                  # correct and late
        [{"id": "A", "minute": 10}, {"id": "M", "minute": 30}],       # one wrong call, then right
        [{"id": "A", "minute": 10}, {"id": "B", "minute": 11}, {"id": "M", "minute": 40}],
        [{"id": "A", "minute": 10}, {"id": "B", "minute": 11}, {"id": "C", "minute": 12}, {"id": "M", "minute": 20}],
        [{"id": "M", "minute": 60}],                                  # at the close: too late
        [{"id": "A", "minute": 3}],                                   # wrong, and never corrected
    ]
    got = run_core("out(DATA.cases.map(function (c) { return TracerCore.scoreRound(DATA.round, c, 3); }));",
                   {"cases": cases, "round": rounds}, prelude=False)
    assert [(g["warning"], g["wrong"], g["called"], g["at"]) for g in got] == [
        (0, 0, False, None), (55, 0, True, 5), (8, 0, True, 52), (30, 1, True, 30), (20, 2, True, 40),
        (0, 3, False, None), (0, 0, False, None), (0, 1, False, None)]


@needs_node
def test_tracer_scores_its_stored_alert_on_the_same_round():
    got = run_core("""
      var r = {miss: "M", window: 60, ids: ["A", "B", "M", "C", "D", "E"]};
      function play(alerts) { return TracerCore.tracerRound(r, function (id) { return alerts[id] == null ? null : alerts[id]; }); }
      out([play({M: 20, A: 15, C: 40}), play({B: 5}), play({M: 60}), play({M: 75, A: 5, B: 5, C: 5, D: 5, E: 5})]);
    """, prelude=False)
    assert got == [{"at": 20, "warning": 40, "falseAlerts": 2}, {"at": None, "warning": 0, "falseAlerts": 1},
                   {"at": 60, "warning": 0, "falseAlerts": 0}, {"at": 75, "warning": 0, "falseAlerts": 5}]


@needs_node
def test_a_seed_in_the_address_is_whole_digits_within_32_bits():
    raws = ["18", "0", "4294967295", "4294967296", "-1", "1.5", "abc", "", "00018", "12345678901"]
    got = run_core("out(DATA.raws.map(TracerCore.parseSeed).concat([TracerCore.parseSeed(null)]));", {"raws": raws}, prelude=False)
    assert got == [18, 0, 4294967295, None, None, None, None, None, 18, None, None]


def test_the_rule_is_one_short_line():
    html = page_html()
    rules = re.findall(r'<p class="bt-rules"[^>]*>(.*?)</p>', html)
    assert rules == ["One will be late. Tap it before Tracer does."]


# ---- the slim game file, docs/demo/game.json ----

GAME = ROOT / "docs" / "demo" / "game.json"


def load_builder():
    spec = importlib.util.spec_from_file_location("build_demo_game", ROOT / "scripts" / "build_demo_game.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_game_json_is_what_the_script_writes_from_the_bundle_and_neural_json():
    same = load_builder().build() == GAME.read_text(encoding="utf-8")
    assert same, "docs/demo/game.json differs from bundle.json and neural.json: run python scripts/build_demo_game.py"
    assert GAME.stat().st_size < 250_000  # against 5.7 MB for bundle.json and neural.json together


def test_game_json_names_every_kind_of_specimen_in_plain_words(bundle):
    name = load_builder().plain_name
    assert {t: name(t) for t in sorted({s["type"] for s in bundle["specimens"]})} == {
        "BAL, refrigerated": "Lung wash", "BAL, room temp": "Lung wash",
        "body fluid, blood culture bottle (peritoneal, pleural)": "Body fluid",
        "body fluid, sterile container, refrigerated": "Body fluid",
        "body fluid, sterile container, room temp (pleural, peritoneal, synovial, pericardial)": "Body fluid",
        "culture swab (BBL, Stuart's)": "Swab", "eSwab": "Swab", "pleural fluid, anaerobic transport vial": "Body fluid",
        "tissue": "Tissue", "urine, boric acid": "Urine", "urine, unpreserved, refrigerated": "Urine",
        "urine, unpreserved, room temp": "Urine"}


@needs_node
def test_game_json_draws_the_same_rounds_as_the_bundle_for_every_seed_and_keeps_tracernets_alerts(bundle):
    seeds = list(range(300)) + [18, 99999, 4294967295]
    got = run_core("""
      var G = JSON.parse(fs.readFileSync(DATA.game, "utf8")), f = G.fields;
      var slim = G.rows.map(function (row) { var s = {}; f.forEach(function (k, i) { s[k] = row[i]; }); return s; });
      var differ = DATA.seeds.filter(function (seed) {
        return JSON.stringify(C.gameRounds(slim, seed)) !== JSON.stringify(C.gameRounds(B.specimens, seed));
      });
      out({differ: differ, eighteen: C.gameRounds(slim, 18), n: slim.length,
           alerts: slim.map(function (s) { return [s.id, s.tracer_alert_min]; })});
    """, {"seeds": seeds, "game": str(GAME)})
    assert got["differ"] == [] and got["eighteen"] == SEED_18
    assert got["n"] == len(bundle["specimens"]) == 2378
    neural = {s["id"]: s["headline"]["alert_min"]
              for s in json.loads((ROOT / "docs" / "demo" / "neural.json").read_text(encoding="utf-8"))["specimens"]}
    assert dict(got["alerts"]) == {s["id"]: neural[s["id"]] for s in bundle["specimens"]}
    assert sum(a is not None for _, a in got["alerts"]) > 100


GAME_CHECK = """
const { chromium } = require(PLAYWRIGHT);
(async () => {
  let browser;
  try { browser = await chromium.launch({ channel: "chrome", headless: true }); }
  catch (e) { console.log(JSON.stringify({ skip: "Google Chrome did not start: " + String(e.message).split("\\n")[0] })); return; }
  const out = { runs: [] };
  // 390 by 664 is what Safari on an iPhone shows with its bars; the laptop plays one round and sees the next begin.
  for (const [width, height, scheme] of [[390, 664, "dark"], [1440, 900, "light"]]) {
    const context = await browser.newContext({ viewport: { width, height }, colorScheme: scheme });
    const page = await context.newPage();
    const run = { width, messages: [], errors: [], foreign: [], asked: [], overflow: [], scroll: [], taps: 0, goShown: [] };
    page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") run.messages.push(m.type() + ": " + m.text()); });
    page.on("pageerror", (e) => run.errors.push(String(e)));
    page.on("request", (r) => { const u = r.url(); if (u.startsWith(BASE + "/")) run.asked.push(u.slice(BASE.length)); else if (!u.startsWith("data:")) run.foreign.push(u); });
    const fit = async () => {
      run.overflow.push(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth));
      run.scroll.push(await page.evaluate(() => document.documentElement.scrollHeight - document.documentElement.clientHeight));
    };
    const game = () => page.evaluate(() => window.TracerDemo.game());
    // the address on the slides' QR code
    await page.goto(BASE + "/demo/index.html?seed=18#beat", { waitUntil: "load" });
    await page.waitForSelector("#game-go:not([disabled])");
    run.alone = await page.evaluate(() => ["top", "watch", "warning", "limits"].every((id) => document.getElementById(id).offsetParent === null)
      && document.getElementById("beat").offsetParent !== null);
    run.targets = await page.$$eval("#game-go, #game-feed .bt-card", (els) => els.map((e) => e.getBoundingClientRect().height));
    run.ready = await page.textContent("#beat");
    await fit();
    const started = Date.now();
    // One tap starts the game, from the keyboard as a player without a mouse would. After it the player only
    // guesses: every round starts by itself. Play as a person would, without reading the answer: tap the one card
    // still waiting for its scan, or after 40 minutes the first waiting one.
    await page.focus("#game-go");
    await page.keyboard.press("Enter");
    run.taps++;
    const rounds = width === 390 ? 3 : 1;
    run.focus = [];
    for (let round = 0; round < rounds; round++) {
      await page.waitForFunction((r) => { const g = window.TracerDemo.game(); return g.round === r && g.phase === "running"; }, round, { timeout: 15000 });
      run.goShown.push(await page.isVisible("#game-go"));
      // focus is on the first card still waiting, and Tab moves on to the next one
      const focusNow = () => page.evaluate(() => Array.from(document.querySelectorAll("#game-feed .bt-card:not(:disabled)")).indexOf(document.activeElement));
      const at = { start: await focusNow() };
      if (round === 0) { await page.keyboard.press("Tab"); at.tab = await focusNow(); }
      run.focus.push(at);
      for (;;) {
        await page.waitForTimeout(200);
        const st = await page.evaluate(() => ({ phase: window.TracerDemo.game().phase, clock: +document.getElementById("game-clock").textContent,
          open: Array.from(document.querySelectorAll("#game-feed .bt-card:not(:disabled)")).map((b) => b.getAttribute("data-id")) }));
        if (st.phase !== "running") break;
        if (round === 0 && st.clock >= 6 && run.scroll.length === 1) await fit();
        if (st.open.length === 1 || (st.clock >= 40 && st.open.length)) { await page.click('#game-feed .bt-card[data-id="' + st.open[0] + '"]'); run.taps++; }
      }
      await page.waitForTimeout(150);
      run.result = await page.textContent("#game-status");
      await fit();
    }
    await page.locator("#beat").screenshot({ path: SHOTS + "/game-round-" + width + ".png" });
    if (rounds === 3) {
      await page.waitForSelector("#game-end:not([hidden])", { timeout: 15000 });
      run.seconds = (Date.now() - started) / 1000;
      run.end = await page.textContent("#game-end");
      run.scores = await page.$$eval("#game-end .bt-score dd", (els) => els.map((e) => e.firstChild.textContent));
      await fit();
      await page.locator("#beat").screenshot({ path: SHOTS + "/game-end-" + width + ".png" });
    } else {
      // with no tap, the next round counts down and starts by itself
      await page.waitForFunction(() => { const g = window.TracerDemo.game(); return g.round === 1 && g.phase === "running"; }, null, { timeout: 15000 });
      run.nextStarted = true;
    }
    run.game = await game();
    out.runs.push(run);
    await context.close();
  }
  await browser.close();
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
def test_a_whole_game_with_seed_18_takes_one_tap_a_round_and_finishes_in_under_two_minutes_in_headless_chrome(tmp_path):
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
        script = tmp_path / "game.js"
        script.write_text(f"const PLAYWRIGHT = {json.dumps(str(playwright))}, BASE = {json.dumps(f'http://127.0.0.1:{port}')}, "
                          f"SHOTS = {json.dumps(str(shots))};\n" + GAME_CHECK, encoding="utf-8")
        done = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=600)
    finally:
        server.terminate()
        server.wait(timeout=10)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    if "skip" in result:
        pytest.skip(result["skip"])
    for run in result["runs"]:
        assert run["messages"] == [] and run["errors"] == [] and run["foreign"] == [], run
        # the game reads the slim game.json alone: not the 3.2 MB bundle or the 2.5 MB neural file
        assert sorted(set(run["asked"])) == ["/demo/game.json", "/demo/index.html?seed=18", "/demo/results.json"], run["asked"]
        assert run["alone"], "the address #beat shows the game alone"
        assert "One will be late. Tap it before Tracer does." in run["ready"]
        assert run["game"]["seed"] == 18 and run["game"]["rounds"] == SEED_18
        played = [r for r in run["game"]["results"]]
        assert "Tracer" in run["result"] and played[-1]["miss"] == SEED_18[len(played) - 1]["miss"]
        assert all(o <= 0 for o in run["overflow"]), run["overflow"]
        assert all(s <= 0 for s in run["scroll"]), f"the game scrolls at {run['width']} px: {run['scroll']}"
        assert all(h >= 44 for h in run["targets"]), run["targets"]
        # one tap on Play, then one tap per round at most: no Start or Next button ever shows while playing
        assert run["taps"] <= 1 + len(played) and run["goShown"] == [False] * len(run["goShown"]), run
        assert [f["start"] for f in run["focus"]] == [0] * len(run["focus"]), run["focus"]
        assert run["focus"][0]["tab"] == 1, run["focus"]
    assert result["runs"][1]["nextStarted"]
    phone = result["runs"][0]
    assert phone["width"] == 390 and phone["game"]["phase"] == "end" and len(phone["game"]["results"]) == 3
    assert phone["seconds"] < 120, phone["seconds"]
    you = sum(r["you"]["warning"] for r in phone["game"]["results"])
    tracer = sum(r["tracer"]["warning"] for r in phone["game"]["results"])
    # two big numbers in minutes of warning over the three rounds
    assert phone["scores"] == [str(round(you)), str(round(tracer))]
    assert "Minutes of warning over 3 rounds." in phone["end"] and "Play again" in phone["end"]
    # Tracer plays TracerNet's stored alerts at the headline budget (docs/demo/neural.json), round by round: on the
    # seed-18 rounds at 5, 15 and 15 minutes, 205 minutes early in all, and 13 others flagged that arrived in time
    neural = {s["id"]: s["headline"]["alert_min"]
              for s in json.loads((ROOT / "docs" / "demo" / "neural.json").read_text(encoding="utf-8"))["specimens"]}
    assert [neural[r["miss"]] for r in SEED_18] == [5, 15, 15]
    assert [r["tracer"]["warning"] for r in phone["game"]["results"]] == [115, 45, 45] and tracer == 205
    assert [r["tracer"]["falseAlerts"] for r in phone["game"]["results"]] == [
        sum(neural[i] is not None for i in r["ids"] if i != r["miss"]) for r in SEED_18] == [5, 5, 3]
    # the honest line: Tracer had no one-guess limit, and it flagged specimens that arrived fine
    assert "Tracer also flagged 13 specimens that arrived fine." in phone["end"]
    verdict = "Tracer warned sooner." if tracer > you else "You beat Tracer!" if you > tracer else "A tie."
    assert verdict in phone["end"]
