"""The talk's slides on a real screen (docs/talk/).

Loads the deck in headless Chrome at a 1920 by 1080 projector and a 1280 by 800 laptop. On every slide nothing
runs into the footer, the type is not shrunk to fit, nothing projected is too small to read from the back of a
room, and the slides that show the demo or its numbers say on screen that the data is synthetic. Then it presses
the keys the presenter uses (L, N, S, P and the arrows), opens the script page from the presenter window, and
opens the script page on one speaker's lines at phone width.
Needs node, Playwright (from visuals/exhibit-3d/node_modules) and Google Chrome, and is skipped without them.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DECK = (ROOT / "docs" / "talk" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")
SYNTHETIC = {0, 5, 6, 7, 9}  # the slides that show the demo or its numbers
SMALLEST_PX = 24  # the shortest line of text allowed on the 1280 by 800 screen


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
const { chromium } = require(PLAYWRIGHT);
(async () => {
  let browser;
  try { browser = await chromium.launch({ channel: "chrome", headless: true }); }
  catch (e) { console.log(JSON.stringify({ skip: "Google Chrome did not start: " + String(e.message).split("\\n")[0] })); return; }
  const out = { sizes: [], keys: {}, script: {} };
  const watch = (page, list) => {
    page.on("pageerror", (e) => list.push(String(e)));
    page.on("console", (m) => { if (m.type() === "error") list.push(m.text()); });
  };

  for (const [width, height] of [[1920, 1080], [1280, 800]]) {
    const ctx = await browser.newContext({ viewport: { width, height }, reducedMotion: "reduce" });
    const page = await ctx.newPage();
    const errors = [];
    watch(page, errors);
    await page.goto(BASE + "/talk/index.html#0", { waitUntil: "load" });
    const slides = [];
    for (let i = 0; i < 10; i++) {
      await page.evaluate((n) => { location.hash = "#" + n; }, i);
      await page.waitForFunction((n) => document.querySelectorAll(".slide")[n].classList.contains("on"), i);
      await page.evaluate(() => Promise.all(document.getAnimations().map((a) => a.finished.catch(() => {}))));
      slides.push(await page.evaluate(() => {
        const s = document.querySelector(".slide.on");
        const natural = Math.min(innerWidth / 100, innerHeight * 1.78 / 100);
        const set = s.style.getPropertyValue("--u");
        const content = [...s.children].filter((c) => !c.matches(".foot, .notes-src"));
        const foot = s.querySelector(".foot");
        let smallest = Infinity;
        const walker = document.createTreeWalker(s, NodeFilter.SHOW_TEXT);
        while (walker.nextNode()) {
          const node = walker.currentNode, parent = node.parentElement;
          if (!node.textContent.trim() || parent.closest(".notes-src")) continue;
          if (parent instanceof SVGElement) { smallest = Math.min(smallest, parent.getBoundingClientRect().height); continue; }
          const range = document.createRange();
          range.selectNodeContents(node);
          for (const r of range.getClientRects()) if (r.height > 0) smallest = Math.min(smallest, r.height);
        }
        const caveat = [...s.querySelectorAll(".caveat")].find((c) => c.textContent.trim() === "Synthetic data, no clinical review");
        const cr = caveat ? caveat.getBoundingClientRect() : null;
        return {
          shrink: set ? parseFloat(set) / natural : 1,
          contentBottom: Math.max(...content.map((c) => c.getBoundingClientRect().bottom)),
          contentRight: Math.max(...content.map((c) => c.getBoundingClientRect().right)),
          railTop: document.querySelector(".rail").getBoundingClientRect().top,
          footTop: foot ? foot.getBoundingClientRect().top : null,
          smallest,
          caveatOnScreen: !!cr && cr.width > 0 && cr.top >= 0 && cr.bottom <= innerHeight && cr.right <= innerWidth
            && getComputedStyle(s).visibility === "visible",
        };
      }));
    }
    out.sizes.push({ width, height, slides, errors });
    await ctx.close();
  }

  // The presenter's keys, on a laptop whose system setting is light: the deck still opens dark.
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: "light", reducedMotion: "reduce" });
  const page = await ctx.newPage();
  const errors = [];
  watch(page, errors);
  await page.goto(BASE + "/talk/index.html#0", { waitUntil: "load" });
  const k = out.keys;
  k.themeAtLoad = await page.evaluate(() => document.documentElement.getAttribute("data-theme"));
  await page.keyboard.press("l");
  k.themeAfterL = await page.evaluate(() => document.documentElement.getAttribute("data-theme"));
  await page.keyboard.press("l");
  k.themeAfterLL = await page.evaluate(() => document.documentElement.getAttribute("data-theme"));
  await page.keyboard.press("n");
  k.notesOn = await page.evaluate(() => document.body.classList.contains("shownotes") && document.getElementById("notes").offsetHeight > 0);
  await page.keyboard.press("n");
  k.notesOff = await page.evaluate(() => !document.body.classList.contains("shownotes"));
  await page.keyboard.press("s");
  k.clock = await page.evaluate(() => document.getElementById("nhead").textContent);
  const [popup] = await Promise.all([page.waitForEvent("popup"), page.keyboard.press("p")]);
  watch(popup, errors);
  await popup.waitForLoadState("load");
  await popup.waitForFunction(() => document.body.classList.contains("presenter") && document.querySelector("#nhead .clk b"));
  k.popupUrl = popup.url();
  k.popupFirstLine = await popup.evaluate(() => (document.querySelector("#nbody p:not(.who)") || {}).textContent || "");
  k.popupClock = await popup.evaluate(() => document.getElementById("nhead").textContent);
  k.scriptLink = await popup.evaluate(() => {
    const a = document.querySelector(".pnav a[href='script.html']");
    return a ? { target: a.target, shown: a.getBoundingClientRect().width > 0 } : null;
  });
  await page.keyboard.press("ArrowRight");
  await popup.waitForFunction(() => location.hash === "#1", null, { timeout: 5000 });
  k.popupBeat = await popup.evaluate(() => document.querySelector("#nhead .beat").textContent);
  k.popupSpeakerBlocks = await popup.evaluate(() => [...document.querySelectorAll("#nbody .say")].map((b) => b.getAttribute("data-who")));
  const [script] = await Promise.all([popup.waitForEvent("popup"), popup.click(".pnav a[href='script.html']")]);
  watch(script, errors);
  await script.waitForLoadState("load");
  k.scriptTitle = await script.title();
  k.mainStillOnDeck = page.url().includes("/talk/index.html");
  k.errors = errors;
  await ctx.close();

  // The script page opened on Karissa's lines: hers in full, Daniel's down to one cue each.
  const sctx = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: "reduce" });
  const sp = await sctx.newPage();
  const serrors = [];
  watch(sp, serrors);
  await sp.goto(BASE + "/talk/script.html?who=karissa", { waitUntil: "load" });
  out.script = await sp.evaluate(() => {
    const shown = (e) => e.getClientRects().length > 0;
    const of = (sel) => [...document.querySelectorAll(sel)];
    return {
      sections: of("section[data-slide]").length,
      karissaLines: of('.block[data-who="Karissa"] .line').length,
      karissaLinesShown: of('.block[data-who="Karissa"] .line').filter(shown).length,
      danielLinesShown: of('.block[data-who="Daniel"] .line').filter(shown).length,
      danielBlocks: of('.block[data-who="Daniel"]').length,
      danielCuesShown: of('.block[data-who="Daniel"] .cue').filter(shown).length,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    };
  });
  out.script.errors = serrors;
  await sctx.close();
  await browser.close();
  console.log(JSON.stringify(out));
})();
"""


@pytest.fixture(scope="module")
def browser_result(tmp_path_factory):
    if NODE is None:
        pytest.skip("node is not installed")
    playwright = playwright_dir()
    if playwright is None:
        pytest.skip("Playwright is not installed in visuals/exhibit-3d/node_modules")
    tmp = tmp_path_factory.mktemp("talk")
    port = free_port()
    server = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(ROOT / "docs")],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.05)
        script = tmp / "check.js"
        script.write_text(f"const PLAYWRIGHT = {json.dumps(str(playwright))}, BASE = {json.dumps(f'http://127.0.0.1:{port}')};\n"
                          + BROWSER_CHECK, encoding="utf-8")
        done = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=120)
    finally:
        server.terminate()
        server.wait(timeout=10)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    if "skip" in result:
        pytest.skip(result["skip"])
    return result


def test_every_slide_fits_and_reads_from_the_back_of_the_room(browser_result):
    for size in browser_result["sizes"]:
        assert size["errors"] == [], size["errors"]
        for i, s in enumerate(size["slides"]):
            where = f"slide {i} at {size['width']} by {size['height']}"
            assert s["shrink"] >= 0.9, (where, "the type was shrunk to fit", s)
            assert s["contentBottom"] <= s["railTop"] - 4, (where, "the content runs into the footer", s)
            if s["footTop"] is not None:
                assert s["contentBottom"] <= s["footTop"] - 4, (where, "the content runs into the caveat", s)
            assert s["contentRight"] <= size["width"], (where, "the content runs off the right edge", s)
            if size["width"] == 1280:
                assert s["smallest"] >= SMALLEST_PX, (where, "a line of text is too small to read projected", s)
            if i in SYNTHETIC:
                assert s["caveatOnScreen"], (where, "the synthetic-data caveat is not on screen")


def test_the_presenter_keys_work(browser_result):
    k = browser_result["keys"]
    assert k["errors"] == [], k["errors"]
    assert k["themeAtLoad"] == "dark", "the deck must open dark even when the system setting is light"
    assert (k["themeAfterL"], k["themeAfterLL"]) == ("light", "dark"), "L must switch the theme on its first press"
    assert k["notesOn"] and k["notesOff"], "N must show and hide the notes"
    assert "of 15:00" in k["clock"], "S must start the talk clock"
    assert "?presenter" in k["popupUrl"], "P must open the presenter window"
    first_note = re.search(r'<aside class="notes-src"><p>(.*?)</p>', DECK).group(1)
    assert k["popupFirstLine"] == first_note
    assert "of 15:00" in k["popupClock"], "the presenter window takes the clock from the slides"
    assert k["popupBeat"] == "1. The silent tube", "the presenter window follows the slides"
    assert k["popupSpeakerBlocks"] == ["Karissa"]
    assert k["scriptLink"] == {"target": "_blank", "shown": True}
    assert k["scriptTitle"] == "Tracer talk script" and k["mainStillOnDeck"]


def test_the_script_page_opens_on_one_speakers_lines(browser_result):
    s = browser_result["script"]
    assert s["errors"] == [], s["errors"]
    assert s["sections"] == 10
    assert s["karissaLines"] > 0 and s["karissaLinesShown"] == s["karissaLines"], s
    assert s["danielLinesShown"] == 0 and s["danielCuesShown"] == s["danielBlocks"] > 0, s
    assert s["overflow"] <= 0, "the script page scrolls sideways on a phone"
