import { describe, expect, it } from 'vitest';
import { netAxis } from '../scene/screen-draw';
import { DEADLINE, NET, riskFill, SCENES, sceneSeconds, warningMin, words } from './play-script';
import { scoreAt } from './tracernet';

describe('the self-playing page script', () => {
  it('plays the four scenes the data supports, in order', () => {
    expect(SCENES.map((s) => [s.fate, s.title])).toEqual([
      ['arrives', 'On time'],
      ['stalls', 'Stalled'],
      ['lost', 'Lost'],
      ['unsent', 'Never sent'],
    ]);
  });

  it('every caption and every card is 8 words or fewer', () => {
    for (const s of SCENES) {
      for (const b of s.beats) expect(words(b.text), b.text).toBeLessThanOrEqual(8);
      const c = s.card;
      expect(words(`${c.unit} ${c.line}`), `${c.unit} ${c.line}`).toBeLessThanOrEqual(8);
      expect(words(c.n)).toBe(1);
    }
  });

  it('each card number is the deadline minus the stored alert minute, and no alert shows 0 alerts', () => {
    expect(DEADLINE).toBe(120);
    for (const s of SCENES) {
      const story = NET.stories[s.fate];
      expect(story.window_min).toBe(DEADLINE);
      if (story.alert_min === null) {
        expect([s.card.n, s.card.unit]).toEqual(['0', 'alerts']);
      } else {
        expect(Number(s.card.n)).toBe(DEADLINE - story.alert_min);
        expect(s.card.unit).toBe('minutes of warning');
      }
    }
    expect(SCENES.map((s) => warningMin(s.fate))).toEqual([null, 95, 90, 95]);
  });

  it('the alert moment sits on the stored alert minute, and only where TracerNet alerted', () => {
    for (const s of SCENES) {
      const alarms = s.beats.filter((b) => b.alarm);
      const a = NET.stories[s.fate].alert_min;
      expect(alarms.map((b) => b.at)).toEqual(a === null ? [] : [a]);
      for (const b of alarms) expect(b.text).toMatch(/^Alert! Check the /);
    }
  });

  it('beats run in time order within each scene and the loop stays under about 2 minutes', () => {
    for (const s of SCENES) {
      const at = s.beats.map((b) => b.at);
      expect(at).toEqual([...at].sort((x, y) => x - y));
      expect(at[at.length - 1]).toBeLessThanOrEqual(s.end);
    }
    const loop = SCENES.reduce((t, s) => t + sceneSeconds(s), 0);
    expect(loop).toBeGreaterThan(60);
    expect(loop).toBeLessThan(120);
  });

  it('the risk bar uses the chart axis, so its fill crosses the alert mark on the alert tick', () => {
    for (const p of [0.005, 0.0186, 0.05, 0.3, 1]) expect(riskFill(p)).toBeCloseTo(netAxis(p), 12);
    expect(riskFill(null)).toBe(0);
    for (const s of SCENES) {
      const story = NET.stories[s.fate];
      const mark = riskFill(story.line);
      const last = story.received_min ?? DEADLINE;
      const crossing = Array.from({ length: last }, (_, m) => m).find((m) => riskFill(scoreAt(story, m, NET.tick_min)) >= mark);
      expect(crossing ?? null).toBe(story.alert_min);
    }
  });
});
