import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { FATES, FATE_IDS } from './fates';
import { CHART_FLOOR, pickStories, riskText, scoreAt, type NetStories, type NeuralFile } from './tracernet';
import committed from './tracernet-stories.json';

const neural = JSON.parse(readFileSync(new URL('../../../../docs/demo/neural.json', import.meta.url), 'utf8')) as NeuralFile;
const net = committed as NetStories;
const TICK = net.tick_min;

describe('the four held-out specimens behind the self-playing page', () => {
  it('the committed file is what the rule picks from docs/demo/neural.json today', () => {
    expect(pickStories(neural, FATES)).toEqual(net);
  });

  it('the rule picks these four: a change to its result fails here', () => {
    const picked = FATE_IDS.map((f) => [f, net.stories[f].id, net.stories[f].received_min, net.stories[f].alert_min]);
    expect(picked).toEqual([
      ['arrives', 'SP-r24-000298', 24, null],
      ['stalls', 'SP-r24-000247', 67, 25],
      ['lost', 'SP-r24-000336', null, 30],
      ['unsent', 'SP-r24-000036', null, 25],
    ]);
    expect(net.model.version).toBe('tracernet-v2');
    expect(net.budget_per_shift).toBe(6);
    for (const f of FATE_IDS) {
      const s = net.stories[f];
      expect([s.window_min, s.tier, s.weight]).toEqual([120, 1, 3]);
      expect(s.line).toBe(s.knob / 3);
      expect(s.line).toBeCloseTo(0.0186, 4);
    }
  });

  it('each alert is the first tick where p_miss reaches the alert line, and no alert means it never does', () => {
    for (const f of FATE_IDS) {
      const s = net.stories[f];
      // As the rule states it (p_miss times the weight reaches the knob) and as the chart draws it.
      const byKnob = s.p_miss.findIndex((p) => p * s.weight >= s.knob);
      const byLine = s.p_miss.findIndex((p) => p >= s.line);
      expect(byKnob).toBe(byLine);
      expect(byKnob < 0 ? null : TICK * (byKnob + 1)).toBe(s.alert_min);
    }
  });

  it('the shown risk steps at each 5-minute tick, is none before 5 min and holds after the last silent tick', () => {
    const s = net.stories.stalls;
    expect(scoreAt(s, 0, TICK)).toBeNull();
    expect(scoreAt(s, 4, TICK)).toBeNull();
    expect(scoreAt(s, 5, TICK)).toBe(s.p_miss[0]);
    expect(scoreAt(s, 24, TICK)).toBe(s.p_miss[3]);
    expect(scoreAt(s, 25, TICK)).toBe(s.p_miss[4]);
    expect(s.p_miss).toHaveLength(13); // received at 67: ticks 5 to 65
    expect(scoreAt(s, 68, TICK)).toBe(s.p_miss[12]);
    expect(scoreAt(net.stories.lost, 124, TICK)).toBe(1); // the window closed at 120 min
  });
});

describe('what the page says about them', () => {
  const all = FATE_IDS.flatMap((f) => net.stories[f].p_miss.map((p) => ({ f, p, line: net.stories[f].line })));
  const shownValue = (p: number) => Number(riskText(p).slice(0, -1));

  it('every score sits above the chart floor, and no rounding puts it on the other side of the line or at 100%', () => {
    for (const { p, line } of all) {
      expect(p).toBeGreaterThan(CHART_FLOOR);
      expect(shownValue(p) >= shownValue(line)).toBe(p >= line);
      if (riskText(p) === '100%') expect(p).toBe(1);
    }
    expect(riskText(net.stories.stalls.line)).toBe('1.9%');
  });

  it('once an alert fires the risk stays over the line ("Over the 1.9% alert line")', () => {
    for (const f of FATE_IDS) {
      const s = net.stories[f];
      if (s.alert_min === null) continue;
      for (let m = s.alert_min; m <= 130; m++) expect(scoreAt(s, m, TICK)!).toBeGreaterThanOrEqual(s.line);
    }
  });

  it('the captions hold: on time stays under the line, the stalled risk keeps climbing to 45 min, never sent alerts at the stalled minute', () => {
    const on = net.stories.arrives;
    expect(Math.max(...on.p_miss)).toBeLessThan(on.line);
    const st = net.stories.stalls;
    for (let m = st.alert_min! + TICK; m <= 45; m += TICK) expect(scoreAt(st, m, TICK)!).toBeGreaterThan(scoreAt(st, m - TICK, TICK)!);
    expect(net.stories.unsent.alert_min).toBe(st.alert_min);
    // Each story's script agrees with its specimen: received in the window, or never received.
    expect(FATES.arrives.received! <= on.window_min && FATES.stalls.received! <= st.window_min).toBe(true);
    expect([net.stories.lost.received_min, net.stories.unsent.received_min, FATES.lost.received, FATES.unsent.received]).toEqual([null, null, null, null]);
  });
});
