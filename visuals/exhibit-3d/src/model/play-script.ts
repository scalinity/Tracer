// The self-playing page's script: four scenes in order, each a short run of captions on the clock and
// an end card with one number. Pure TypeScript (no DOM, no three), so vitest checks every word of it.
//
// Where the numbers come from. The risk and the alert minutes are TracerNet's (tracernet-v2, Tracer's
// primary risk model since November 13, 2026): its precomputed p_miss for one held-out synthetic
// specimen per scene, picked from docs/demo/neural.json by the fixed rule in tracernet.ts
// (tracernet-stories.json). Where Tracer thinks the specimen is (the glow, and the place an alert
// caption names) is the analytic forward filter of TracerModel, as on the interactive bench. The
// scenes' true paths (fates.ts) are scripted theatre that the models never see. The data are
// synthetic and had no clinical review; the page says so in one line.

import { FATES, type FateId, type Place } from './fates';
import { TracerModel } from './state';
import { ASSUMED } from './tracer';
import { CHART_FLOOR, type NetStories } from './tracernet';
import netStories from './tracernet-stories.json';

export type Tone = 'info' | 'good' | 'warn' | 'alert' | 'bad';

export interface Beat {
  /** Minute on the scene's clock (minutes since the specimen left the ward). */
  at: number;
  /** Seconds the clock stands still on this beat. */
  hold: number;
  tone: Tone;
  /** The caption: 8 words or fewer. */
  text: string;
  /** TracerNet's alert fires on this beat: the page's alert moment. */
  alarm?: boolean;
}

export interface Card {
  /** The one big number. */
  n: string;
  /** What the number counts, under it. */
  unit: string;
  /** One short line under that. */
  line: string;
  tone: Tone;
}

export interface Scene {
  fate: FateId;
  title: string;
  /** The minute the scene's clock stops before its end card. */
  end: number;
  beats: Beat[];
  card: Card;
}

export const NET = netStories as NetStories;
/** The deadline: the stability window of every scene's specimen, minutes from the collection scan. */
export const DEADLINE = ASSUMED.windowMin;

const PLACE_NAME: Record<Place, string> = {
  ward: 'the ward', tube: 'the tube', station: 'the tube station', lost: 'the lost bin', lab: 'the lab',
};

/** Words as a viewer counts them: anything with a letter or digit in it. */
export const words = (s: string): number => s.split(/\s+/).filter((w) => /[A-Za-z0-9]/.test(w)).length;

/**
 * Where a risk fills the page's risk bar, 0 to 1: the same log axis as TracerNet's chart on the bench
 * (netAxis in scene/screen-draw.ts), so the fill crosses the alert mark on the tick the alert fires.
 * No score yet (before TracerNet's first tick) fills nothing.
 */
export const riskFill = (p: number | null): number =>
  p === null ? 0 : Math.log10(Math.max(CHART_FLOOR, p) / CHART_FLOOR) / Math.log10(1 / CHART_FLOOR);

/** Minutes of warning TracerNet's stored alert gave before the deadline; null for no alert. */
export const warningMin = (fate: FateId): number | null => {
  const a = NET.stories[fate].alert_min;
  return a === null ? null : DEADLINE - a;
};

/** Builds the script from the stored alerts and the forward filter's most likely place at each alert. */
export function buildScript(): Scene[] {
  const model = new TracerModel(ASSUMED);
  const likelyAt = (fate: FateId, m: number): Place =>
    model.read({ fate, silence: m, alertLine: ASSUMED.operatingPoint, windowMin: DEADLINE, truth: true }).likely.place;
  const alarm = (fate: FateId): Beat => {
    const at = NET.stories[fate].alert_min!;
    return { at, hold: 3.4, tone: 'alert', alarm: true, text: `Alert! Check ${PLACE_NAME[likelyAt(fate, at)]}.` };
  };
  const warned = (fate: FateId, tone: Tone): Card => ({
    n: String(warningMin(fate)), unit: 'minutes of warning', line: `before the ${DEADLINE}-minute deadline`, tone,
  });
  const onTime = FATES.arrives.received!;
  const found = FATES.stalls.received!;
  const scenes: Scene[] = [
    {
      fate: 'arrives', title: 'On time', end: onTime + 4,
      beats: [
        { at: 0, hold: 2.2, tone: 'info', text: 'A specimen leaves the ward.' },
        { at: 3, hold: 2.8, tone: 'info', text: 'The glow shows where Tracer thinks it is.' },
        { at: 9, hold: 2.6, tone: 'info', text: `It has ${DEADLINE} minutes to reach the lab.` },
        { at: onTime, hold: 2.6, tone: 'good', text: `Scanned in at ${onTime} min. Safe.` },
      ],
      card: { n: '0', unit: 'alerts', line: `Arrived at ${onTime} min. No alarm needed.`, tone: 'good' },
    },
    {
      fate: 'stalls', title: 'Stalled', end: found + 4,
      beats: [
        { at: 0, hold: 1.8, tone: 'info', text: 'Another specimen leaves the ward.' },
        { at: 14, hold: 2.4, tone: 'warn', text: 'It gets stuck at the tube station.' },
        { at: 19, hold: 2.2, tone: 'warn', text: 'No scan comes. The lab sees nothing.' },
        alarm('stalls'),
        { at: found, hold: 2.6, tone: 'good', text: `Found and scanned at ${found} min.` },
      ],
      card: warned('stalls', 'warn'),
    },
    {
      fate: 'lost', title: 'Lost', end: DEADLINE + 4,
      beats: [
        { at: 0, hold: 1.8, tone: 'info', text: 'A third specimen leaves the ward.' },
        { at: 12, hold: 2.4, tone: 'bad', text: 'It falls into the lost bin.' },
        { at: 18, hold: 2.0, tone: 'bad', text: 'No scan will ever come.' },
        alarm('lost'),
        { at: 82, hold: 2.6, tone: 'bad', text: `Now Tracer points to ${PLACE_NAME[likelyAt('lost', 82)]}.` },
        { at: DEADLINE, hold: 2.6, tone: 'bad', text: 'The deadline passes. Still no scan.' },
      ],
      card: warned('lost', 'bad'),
    },
    {
      fate: 'unsent', title: 'Never sent', end: 40,
      beats: [
        { at: 0, hold: 2.2, tone: 'warn', text: 'The last one never leaves the ward.' },
        { at: 12, hold: 2.2, tone: 'warn', text: 'To the lab, it is the same silence.' },
        alarm('unsent'),
        { at: 34, hold: 2.8, tone: 'warn', text: 'It was at the ward all along.' },
      ],
      card: warned('unsent', 'warn'),
    },
  ];
  // A stored alert can land before or after a scripted beat; the page plays beats in time order.
  for (const s of scenes) s.beats.sort((a, b) => a.at - b.at);
  return scenes;
}

export const SCENES: Scene[] = buildScript();

// ---------- pacing: the director in play.ts moves the clock with these ----------
/** Seconds of the title card before each scene, of the end card after it, and of the fade between. */
export const INTRO_SEC = 1.8;
export const OUTRO_SEC = 4.0;
export const FADE_SEC = 0.9;
const MIN_SPEED = 4;
const MAX_SPEED = 16;

/** The clock's next minute after dt seconds: fast across empty stretches, easing into the next stop. */
export const stepClock = (minute: number, stop: number, dt: number): number =>
  Math.min(stop, minute + dt * Math.min(MAX_SPEED, Math.max(MIN_SPEED, (stop - minute) / 1.4)));

/** Seconds one scene takes, simulated at 60 frames a second as the director plays it. */
export function sceneSeconds(s: Scene, dt = 1 / 60): number {
  let sec = INTRO_SEC + OUTRO_SEC + FADE_SEC;
  let m = 0;
  for (const stop of [...s.beats.map((b) => b.at), s.end]) {
    while (m < stop - 1e-9) { m = stepClock(m, stop, dt); sec += dt; }
  }
  return sec + s.beats.reduce((t, b) => t + b.hold, 0);
}
