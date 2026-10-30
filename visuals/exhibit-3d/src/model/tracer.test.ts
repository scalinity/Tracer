// Parity with the original model, and the invariants the exhibit relies on. The original functions
// run under node exactly as written in visuals/architecture.html (sections 1 and 2 of its script);
// original-model.fixture.js is a verbatim copy for when the exhibit lives away from that page.

import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import { describe, expect, it } from 'vitest';
import {
  alertMinute, ASSUMED, componentWeights, locationSeries, lnSurv, MAX_T, riskAfterReceipt, riskUnreceived, type Params,
} from './tracer';

const ARCH = fileURLToPath(new URL('../../../architecture.html', import.meta.url));
const FIXTURE = fileURLToPath(new URL('./original-model.fixture.js', import.meta.url));
const TOL = 1e-9;

/** Section 1 (the model) and section 2 up to the clock formatting (the series and the alert rule). */
function sections(text: string): { model: string; rule: string; whole: string } {
  const at = (marker: string) => {
    const i = text.indexOf(marker);
    if (i < 0) throw new Error(`marker not found: ${marker}`);
    return i;
  };
  const model = text.lastIndexOf('/* ═', at('1. Model. Illustrative parameters'));
  const derived = text.lastIndexOf('/* ═', at('2. Derived series and scenario events'));
  const hhmm = text.indexOf('const hhmm');
  const end = hhmm < 0 ? text.length : hhmm;
  return { model: text.slice(model, derived), rule: text.slice(derived, end), whole: text.slice(model, end) };
}

const live = existsSync(ARCH) ? sections(readFileSync(ARCH, 'utf8')) : null;
const source = live ?? sections(readFileSync(FIXTURE, 'utf8'));

interface Original {
  riskUnreceived(t: number): number;
  locationSeries(maxT: number): { legs: number[]; lost: number }[];
  RISK: number[];
  LOC: { legs: number[]; lost: number }[];
  ALERT_T: number | null;
}

/** A fresh run of the original, with its PARAM changed first if asked. */
function original(change?: { windowMin?: number; operatingPoint?: number; receivedAt?: number }): Original {
  const ctx = vm.createContext({});
  vm.runInContext(source.model, ctx);
  if (change) {
    ctx.__change = change;
    vm.runInContext('Object.assign(PARAM, __change);', ctx);
  }
  vm.runInContext(`${source.rule}\n;this.__out = { riskUnreceived, locationSeries, RISK, LOC, ALERT_T };`, ctx);
  return ctx.__out as Original;
}

const P: Params = ASSUMED;
/**
 * The exhibit's stalled specimen, found at 68 min (the page's own scenario is a urine found at 52 min in
 * a 60 min window, so the page is told this one): the model while silent, the known outcome after the scan.
 */
const RECEIVED = 68;
const scenarioRisk = (t: number, W = P.windowMin) => (t < RECEIVED ? riskUnreceived(t, P, W) : riskAfterReceipt(RECEIVED, W));
const exhibitScenario = (extra: { windowMin?: number; operatingPoint?: number } = {}) =>
  original({ windowMin: P.windowMin, receivedAt: RECEIVED, ...extra });

describe('the fixture is the original', () => {
  it.skipIf(!live)('original-model.fixture.js matches visuals/architecture.html byte for byte', () => {
    expect(readFileSync(FIXTURE, 'utf8')).toBe(live!.whole);
  });
});

describe('parity with the original (architecture.html), every minute of its scenario', () => {
  const o = exhibitScenario();
  const N = o.RISK.length - 1; // the page plots the first 90 minutes

  it('riskUnreceived agrees to 1e-9', () => {
    let worst = 0;
    for (let t = 0; t <= MAX_T; t++) worst = Math.max(worst, Math.abs(riskUnreceived(t, P) - o.riskUnreceived(t)));
    expect(worst).toBeLessThanOrEqual(TOL);
  });

  it('the scenario risk series agrees to 1e-9 at every minute, silent and after the receipt scan', () => {
    let worst = 0;
    for (let t = 0; t <= N; t++) worst = Math.max(worst, Math.abs(scenarioRisk(t) - o.RISK[t]));
    expect(worst).toBeLessThanOrEqual(TOL);
    expect(o.RISK[RECEIVED - 1]).toBeGreaterThan(0.5);
    expect(o.RISK[RECEIVED]).toBe(0); // the scan resolves it
    expect(N).toBeGreaterThan(RECEIVED);
  });

  it('a receipt after the window closed is a miss, in the original and here', () => {
    const late = exhibitScenario({ windowMin: 60 });
    expect(late.RISK[N]).toBe(1);
    expect(riskAfterReceipt(RECEIVED, 60)).toBe(1);
    expect(riskAfterReceipt(RECEIVED, 120)).toBe(0);
  });

  it('every leg and lost of the location posterior agrees to 1e-9', () => {
    const mine = locationSeries(MAX_T, P);
    const theirs = o.locationSeries(MAX_T);
    expect(mine.length).toBe(theirs.length);
    expect(o.LOC.length).toBe(N + 1); // the page's own scenario keeps the first 90 minutes of it
    let worst = 0;
    for (let t = 0; t <= MAX_T; t++) {
      for (let i = 0; i < P.legs.length; i++) worst = Math.max(worst, Math.abs(mine[t].legs[i] - theirs[t].legs[i]));
      worst = Math.max(worst, Math.abs(mine[t].lost - theirs[t].lost));
    }
    expect(worst).toBeLessThanOrEqual(TOL);
  });

  it('the alert minute agrees: 60 min', () => {
    expect(o.ALERT_T).toBe(60);
    expect(alertMinute((t) => scenarioRisk(t), P.operatingPoint, P.tickMin, RECEIVED)).toBe(o.ALERT_T);
  });

  it('riskUnreceived agrees at other windows (60, 90, 150, 240)', () => {
    for (const W of [60, 90, 150, 240]) {
      const ow = original({ windowMin: W });
      let worst = 0;
      for (let t = 0; t <= MAX_T; t++) worst = Math.max(worst, Math.abs(riskUnreceived(t, P, W) - ow.riskUnreceived(t)));
      expect(worst, `window ${W}`).toBeLessThanOrEqual(TOL);
    }
  });

  it('the alert rule agrees across alert lines and windows', () => {
    for (const W of [60, 90, 120, 180, 240]) {
      for (const line of [0.1, 0.2, 0.3, 0.45, 0.6, 0.75, 0.9]) {
        const ow = exhibitScenario({ windowMin: W, operatingPoint: line });
        expect(alertMinute((t) => scenarioRisk(t, W), line, P.tickMin, RECEIVED), `window ${W}, line ${line}`).toBe(ow.ALERT_T);
      }
    }
  });
});

describe('invariants', () => {
  it('risk stays in [0, 1] for every window 60 to 240 and minute 0 to 130', () => {
    for (let W = 60; W <= 240; W++) {
      for (let t = 0; t <= MAX_T; t++) {
        const r = riskUnreceived(t, P, W);
        expect(r >= 0 && r <= 1, `riskUnreceived(${t}) at window ${W} = ${r}`).toBe(true);
      }
    }
  });

  it('the location posterior (four legs plus lost) sums to 1 at every minute', () => {
    for (const step of locationSeries(MAX_T, P)) {
      expect(Math.abs(step.legs.reduce((s, v) => s + v, 0) + step.lost - 1)).toBeLessThan(1e-12);
    }
  });

  it('P(lost) never decreases with silence', () => {
    const series = locationSeries(MAX_T, P);
    for (let t = 1; t <= MAX_T; t++) expect(series[t].lost).toBeGreaterThanOrEqual(series[t - 1].lost);
  });

  it('risk never falls while the silence lasts, at any window', () => {
    for (let W = 60; W <= 240; W += 10) {
      for (let t = 1; t <= MAX_T; t++) {
        expect(riskUnreceived(t, P, W), `window ${W}, minute ${t}`).toBeGreaterThanOrEqual(riskUnreceived(t - 1, P, W));
      }
    }
  });

  it('a passed window is certain: risk is 1 from the window on', () => {
    for (const W of [60, 90, 120, 240]) {
      expect(riskUnreceived(W, P, W)).toBe(1);
      expect(riskUnreceived(W - 1, P, W)).toBeLessThan(1);
    }
  });

  it('lowering the alert line never moves the alert minute later, at any window', () => {
    for (let W = 60; W <= 240; W += 10) {
      let last = Infinity;
      for (let k = 90; k >= 10; k--) {
        const a = alertMinute((t) => riskUnreceived(t, P, W), k / 100, P.tickMin, MAX_T) ?? Infinity;
        expect(a, `window ${W}, line ${k}%`).toBeLessThanOrEqual(last);
        last = a;
      }
    }
  });

  it('shrinking the window never moves the alert minute later, at any line', () => {
    for (let k = 10; k <= 90; k += 5) {
      let last = Infinity;
      for (let W = 240; W >= 60; W -= 2) {
        const a = alertMinute((t) => riskUnreceived(t, P, W), k / 100, P.tickMin, MAX_T) ?? Infinity;
        expect(a, `line ${k}%, window ${W}`).toBeLessThanOrEqual(last);
        last = a;
      }
    }
  });

  it('the default parameters alert at 60 min, a lead of 60 min (window − alert minute)', () => {
    const a = alertMinute((t) => riskUnreceived(t, P), P.operatingPoint, P.tickMin, MAX_T);
    expect(a).toBe(60);
    expect(P.windowMin - a!).toBe(60);
  });

  it('the mixture weights are riskUnreceived\'s denominator, normalized', () => {
    for (const t of [0, 24, 55, 90, 130]) {
      const w = componentWeights(t, P);
      expect(w.parts.reduce((s, v) => s + v, w.lost)).toBeCloseTo(1, 12);
      const notReceived = P.lossFraction + P.mix.reduce((s, c) => s + c.w * lnSurv(t, c.m, c.s), 0);
      expect(w.lost).toBeCloseTo(P.lossFraction / notReceived, 12);
    }
    expect(componentWeights(0, P).parts[0]).toBeCloseTo(0.88, 12);
  });
});

describe('known values from architecture.md §3', () => {
  const series = locationSeries(MAX_T, P);
  it('risk 0.055 at minute 0, 0.41 at minute 55 and 0.47 at minute 60 (the first tick past the line)', () => {
    expect(riskUnreceived(0, P)).toBeCloseTo(0.0553, 4);
    expect(riskUnreceived(55, P)).toBeCloseTo(0.409, 3);
    expect(riskUnreceived(60, P)).toBeCloseTo(0.4707, 4);
  });
  it('at 55 min the station holds 0.68 and lost 0.31', () => {
    expect(series[55].legs[2] + series[55].legs[3]).toBeCloseTo(0.6845, 4);
    expect(series[55].lost).toBeCloseTo(0.3138, 4);
  });
  it('the receipt scan at 68 min stops the clock: the outcome is known, 0 in a 120 min window and 1 in a 60 min one', () => {
    expect(riskAfterReceipt(68, 120)).toBe(0);
    expect(riskAfterReceipt(68, 60)).toBe(1);
  });
  it('at 55 min, 32% of the still-silent specimens are on a normal trip, 39% stalled, 30% never arrive', () => {
    const w = componentWeights(55, P);
    expect(w.parts[0]).toBeCloseTo(0.316, 3);
    expect(w.parts[1]).toBeCloseTo(0.388, 3);
    expect(w.lost).toBeCloseTo(0.296, 3);
  });
});
