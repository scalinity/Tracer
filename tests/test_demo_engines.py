"""The demo page in the three browser engines GitHub Pages visitors use (plan task P7).

Chromium covers Chrome and Android, WebKit covers Safari on iPhone, Firefox covers Firefox. Each loads the page at
a phone width and a laptop width, taps the #warning picker and plays a round of Beat Tracer, served under /Tracer/
the way GitHub Pages serves the project (a page at /Tracer/demo/), so an address written as "/demo/..." instead of
a relative one fails here and not at the talk.
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
VIEWPORTS = ((390, 844), (1440, 900))

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
        await page.waitForSelector("#w-bars .bar", { timeout: 20000 });
        run.overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        // the links resolve inside the site
        run.links = await page.$$eval("#watch .cta, #limits .more a", (as) => as.map((a) => a.href));
        // the #warning picker changes the number
        const minutes = await page.textContent("#w-min");
        await page.click('#w-bars .bar[data-n="2"]');
        run.picked = await page.textContent("#w-min");
        run.pickerChanged = run.picked !== minutes;
        // a round of Beat Tracer: one tap on Play, the round starts by itself, one tap on a card ends it
        await page.evaluate(() => { location.hash = "beat"; });
        await page.waitForSelector("#game-go:not([disabled])", { state: "visible", timeout: 10000 });
        await page.click("#game-go");
        await page.waitForFunction(() => window.TracerDemo.game().phase === "running", null, { timeout: 10000 });
        const clock = await page.textContent("#game-clock");
        await page.waitForTimeout(1500);
        run.gameClockMoves = (await page.textContent("#game-clock")) !== clock;
        await page.click("#game-feed .bt-card:not(:disabled)");
        await page.waitForFunction(() => window.TracerDemo.game().phase === "result", null, { timeout: 5000 });
        run.roundResult = await page.textContent("#game-status");
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
        assert run["overflow"] <= 0 and run["overflowGame"] <= 0, f"{engine} scrolls sideways at {run['width']} px: {run}"
        assert run["pickerChanged"] and run["picked"] == "8.2" and run["gameClockMoves"], f"a control did nothing in {engine}: {run}"
        assert "Tracer" in run["roundResult"], run
        base = run["links"][0].split("/Tracer/")[0]
        assert run["links"] == [base + "/Tracer/demo/play/", base + "/Tracer/explained/short/", "https://github.com/scalinity/Tracer"], run["links"]
