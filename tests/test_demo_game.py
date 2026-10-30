"""Beat Tracer, the class activity on the demo page (plan task P5).

The game's pure parts live in the page's <script id="core"> block and run here under node, as in
tests/test_demo_page.py: the seeded generator against an independent Python mulberry32, the rounds for seed 18
pinned once, round selection held to the fields it may read and to its rules for every seed tried, and the
scoring of a player and of Tracer. A headless Chrome run plays a whole game with ?seed=18 in real time.
"""

import json
import os
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


def test_the_activity_card_lists_three_things_to_try_and_two_questions():
    html = page_html()
    card = html[html.index('<section id="activity"'):html.index("</section>", html.index('<section id="activity"'))]
    assert card.count("<li>") == 5
    steps = card[card.index('class="act-steps"'):card.index("</ol>")]
    assert [h for h in ("#board", "#threshold", "#beat") if f'href="{h}"' in steps] == ["#board", "#threshold", "#beat"]
    questions = card[card.index('class="act-qs"'):]
    assert questions.count("<li>") == 2 and questions.count("?") == 2
    assert html.index('id="activity"') < html.index('id="problem"')  # at the top of the page


GAME_CHECK = """
const { chromium } = require(PLAYWRIGHT);
(async () => {
  let browser;
  try { browser = await chromium.launch({ channel: "chrome", headless: true }); }
  catch (e) { console.log(JSON.stringify({ skip: "Google Chrome did not start: " + String(e.message).split("\\n")[0] })); return; }
  const out = { runs: [] };
  for (const [width, height, scheme] of [[375, 812, "dark"], [1280, 800, "light"]]) {
    const context = await browser.newContext({ viewport: { width, height }, colorScheme: scheme });
    const page = await context.newPage();
    const run = { width, messages: [], errors: [], foreign: [], overflow: [] };
    page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") run.messages.push(m.type() + ": " + m.text()); });
    page.on("pageerror", (e) => run.errors.push(String(e)));
    page.on("request", (r) => { const u = r.url(); if (!u.startsWith(BASE + "/") && !u.startsWith("data:")) run.foreign.push(u); });
    await page.goto(BASE + "/demo/index.html?seed=18", { waitUntil: "load" });
    await page.waitForSelector("#beat:not([hidden]) #game-go");
    run.activity = await page.$$eval("#activity .act-steps li, #activity .act-qs li", (els) => els.map((e) => e.closest("ol, ul").className));
    run.seedLine = await page.textContent("#game-seed");
    run.targets = await page.$$eval("#game-go, #game-call", (els) => els.map((e) => { const r = e.getBoundingClientRect(); return [r.width, r.height]; }));
    const started = Date.now();
    // Play as a person would, without reading the answer: call the one specimen still silent, or after 40
    // minutes the first silent one, and let a wrong call rule it out. The phone plays the whole game; the
    // laptop plays one round.
    const rounds = width === 375 ? 3 : 1;
    for (let round = 0; round < rounds; round++) {
      await page.click("#game-go");
      for (;;) {
        await page.waitForTimeout(200);
        const st = await page.evaluate(() => ({ phase: window.TracerDemo.game().phase, clock: +document.getElementById("game-clock").textContent,
          open: Array.from(document.querySelectorAll("#game-feed .fs:not(:disabled)")).map((b) => b.getAttribute("data-id")) }));
        if (st.phase !== "running") break;
        if (round === 0 && st.clock >= 6 && !run.rowTarget) {
          run.rowTarget = await page.$eval("#game-feed .fs", (b) => b.getBoundingClientRect().height);
          run.overflow.push(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth));
        }
        if (st.open.length === 1 || (st.clock >= 40 && st.open.length)) {
          await page.click('#game-feed .fs[data-id="' + st.open[0] + '"]');
          await page.click("#game-call");
        }
      }
      run.overflow.push(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth));
    }
    run.result = await page.textContent("#game-result");
    await page.locator("#beat").screenshot({ path: SHOTS + "/game-round-" + width + ".png" });
    if (rounds === 3) {
      await page.click("#game-go");
      await page.waitForSelector("#game-end:not([hidden])");
      run.seconds = (Date.now() - started) / 1000;
      run.end = await page.textContent("#game-end");
      run.medians = await page.$$eval("#game-end .end-pair dd", (els) => els.map((e) => e.firstChild.textContent));
      run.overflow.push(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth));
      await page.locator("#beat").screenshot({ path: SHOTS + "/game-end-" + width + ".png" });
    }
    run.game = await page.evaluate(() => window.TracerDemo.game());
    out.runs.push(run);
    await context.close();
  }
  await browser.close();
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
def test_a_whole_game_with_seed_18_finishes_in_under_three_minutes_in_headless_chrome(tmp_path):
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
        assert run["activity"] == ["act-steps"] * 3 + ["act-qs"] * 2
        assert "seed 18" in run["seedLine"] and run["game"]["rounds"] == SEED_18
        last = run["game"]["results"][-1]["miss"]
        assert "Tracer" in run["result"] and last == SEED_18[len(run["game"]["results"]) - 1]["miss"] and last in run["result"]
        assert all(o <= 0 for o in run["overflow"]), run["overflow"]
        assert all(h >= 44 for _, h in run["targets"]) and run["rowTarget"] >= 44
    phone = result["runs"][0]
    assert phone["width"] == 375 and phone["game"]["phase"] == "end" and len(phone["game"]["results"]) == 3
    assert phone["seconds"] < 180, phone["seconds"]
    assert "Your median warning and Tracer's" in phone["end"] and "Silence carries information" in phone["end"]
    you = sorted(r["you"]["warning"] for r in phone["game"]["results"])
    tracer = sorted(r["tracer"]["warning"] for r in phone["game"]["results"])
    assert phone["medians"] == [f"{you[1]:g} min", f"{tracer[1]:g} min"]
