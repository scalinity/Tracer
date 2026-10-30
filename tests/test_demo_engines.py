"""The demo page in the three browser engines GitHub Pages visitors use (plan task P7).

Chromium covers Chrome and Android, WebKit covers Safari on iPhone, Firefox covers Firefox. Each loads the page at
a phone width and a laptop width, opens a timeline, moves the threshold and window controls and starts a round of Beat Tracer, served under /Tracer/ the way GitHub Pages serves the project (a page at
/Tracer/demo/), so an address written as "/demo/..." instead of a relative one fails here and not at the talk.
A real phone is still Daniel's check; this is what can be checked without one. An engine that is not installed
is skipped and says so: run `npx playwright install firefox webkit` in visuals/exhibit-3d to add it.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
ENGINES = ("chromium", "firefox", "webkit")
VIEWPORTS = ((390, 844), (1280, 800))

CHECK = """
const { chromium, firefox, webkit } = require(PLAYWRIGHT);
const engines = { chromium, firefox, webkit };
(async () => {
  const out = {};
  for (const name of ENGINES) {
    let browser;
    try { browser = await engines[name].launch({ headless: true }); }
    catch (e) { out[name] = { skip: String(e.message).split("\\n")[0] }; continue; }
    out[name] = { runs: [] };
    for (const [width, height] of VIEWPORTS) {
      const context = await browser.newContext({ viewport: { width, height } });
      const page = await context.newPage();
      const run = { width, messages: [], errors: [], foreign: [] };
      page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") run.messages.push(m.type() + ": " + m.text()); });
      page.on("pageerror", (e) => run.errors.push(String(e)));
      page.on("request", (r) => { const u = r.url(); if (!u.startsWith(BASE + "/Tracer/") && !u.startsWith("data:") && !u.startsWith("blob:")) run.foreign.push(u); });
      try {
        await page.goto(BASE + "/Tracer/demo/", { waitUntil: "load" });
        await page.waitForSelector("#board-body tr[data-id]", { timeout: 20000 });
        run.rows = await page.$$eval("#board-body tr[data-id]", (rows) => rows.length);
        run.overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        await page.click("#board-body tr[data-id] .sid");
        await page.waitForSelector("#timeline:not([hidden])", { timeout: 10000 });
        run.timeline = true;
        run.overflowOpen = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        run.link = await page.$eval("#tl-3d", (a) => a.href).catch(() => null);
        await page.focus("#th-point");
        const sentence = await page.textContent("#th-sentence");
        await page.keyboard.press("ArrowRight");
        run.sentenceChanged = (await page.textContent("#th-sentence")) !== sentence;
        await page.focus("#th-win");
        const windowOut = await page.textContent("#th-win-out");
        await page.keyboard.press("ArrowRight");
        // the page applies a window on the next animation frame, so wait for the label rather than read it at once
        run.windowChanged = await page.waitForFunction((was) => document.getElementById("th-win-out").textContent !== was, windowOut, { timeout: 3000 }).then(() => true, () => false);
        await page.click("#game-go");
        const clock = await page.textContent("#game-clock");
        await page.waitForTimeout(1500);
        run.gameClockMoves = (await page.textContent("#game-clock")) !== clock;
        run.overflowGame = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        if (SHOTS) await page.screenshot({ path: SHOTS + "/" + name + "-" + width + ".png", fullPage: true });
      } catch (e) { run.failure = String(e.message).split("\\n")[0]; }
      out[name].runs.push(run);
      await context.close();
    }
    await browser.close();
  }
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


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


@pytest.fixture(scope="module")
def engines(tmp_path_factory):
    playwright = playwright_dir()
    if NODE is None or playwright is None:
        pytest.skip("node or Playwright (visuals/exhibit-3d/node_modules) is not installed")
    work = tmp_path_factory.mktemp("pages")
    (work / "site").mkdir()
    (work / "site" / "Tracer").symlink_to(ROOT / "docs", target_is_directory=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(work / "site")],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.05)
        script = work / "check.js"
        script.write_text(f"const PLAYWRIGHT = {json.dumps(str(playwright))}, BASE = {json.dumps(f'http://127.0.0.1:{port}')}, "
                          f"ENGINES = {json.dumps(ENGINES)}, VIEWPORTS = {json.dumps(VIEWPORTS)}, SHOTS = {json.dumps(__import__('os').environ.get('TRACER_DEMO_SHOTS', ''))};\n" + CHECK, encoding="utf-8")
        done = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=240)
    finally:
        server.terminate()
        server.wait(timeout=10)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.parametrize("engine", ENGINES)
def test_the_page_works_in_the_engine_at_phone_and_laptop_widths_under_the_pages_path(engines, engine):
    result = engines[engine]
    if "skip" in result:
        pytest.skip(f"{engine} did not start: {result['skip']}")
    assert [run["width"] for run in result["runs"]] == [w for w, _ in VIEWPORTS]
    for run in result["runs"]:
        assert "failure" not in run, run
        assert run["messages"] == [] and run["errors"] == [], run
        assert run["foreign"] == [], f"{engine} asked for something outside the site: {run['foreign']}"
        assert run["rows"] >= 3 and run["timeline"], run
        assert run["overflow"] <= 0 and run["overflowOpen"] <= 0 and run["overflowGame"] <= 0, f"{engine} scrolls sideways at {run['width']} px: {run}"
        assert run["sentenceChanged"] and run["windowChanged"] and run["gameClockMoves"], f"a control did nothing in {engine}: {run}"
        assert "/Tracer/demo/exhibit/index.html?" in run["link"], f"the 3D link does not point into the site: {run['link']}"
