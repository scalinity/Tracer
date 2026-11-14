// TracerNet on the self-playing page. TracerNet (artifact tracernet-v2) became Tracer's primary risk
// model on November 13, 2026 (docs/neural/results-v2.md). It cannot run in a browser, so its scores for
// the demo's held-out specimens are precomputed in docs/demo/neural.json. Each of the page's four
// stories takes TracerNet's numbers from one held-out specimen picked by the fixed rule in
// pickStories; scripts/tracernet-stories.mjs applies it and writes tracernet-stories.json, which the
// page imports at build time. TracerNet scores the chance of a miss only: where the specimen is stays
// the analytic forward filter (TracerModel). Pure TypeScript with type-only imports, so node runs it.

import type { Fate, FateId } from './fates';

/** One specimen of docs/demo/neural.json, the fields the rule reads (neural.schema.json). */
interface NeuralSpecimen {
  id: string;
  type: string;
  tier: number;
  route: string;
  window_min: number;
  received_min: number | null;
  /** p_miss at minutes 5, 10, 15, … while the specimen is silent. */
  p_miss: number[];
  alerts: { tracernet: Record<string, (number | null)[]> };
  headline: { alert_min: number | null };
  truth: { fault: string };
}

export interface NeuralFile {
  source: string;
  model: { version: string; manifest_sha256: string | null };
  tick_min: number;
  grid_budgets: number[];
  headline_index: number;
  headline_knobs: Record<string, number>;
  specimens: NeuralSpecimen[];
}

/** The held-out specimen behind one story, with TracerNet's numbers for it. */
export interface NetStory {
  id: string;
  type: string;
  tier: number;
  window_min: number;
  received_min: number | null;
  /** The tier's weight at the 3-2-1 weighting (config/triage.yaml: tier 1 weighs 3). */
  weight: number;
  /** TracerNet's harm threshold at the headline budget and 3-2-1 weights. */
  knob: number;
  /** The alert line on p_miss: knob ÷ weight. */
  line: number;
  /** TracerNet's alert minute at the headline budget, null for none. */
  alert_min: number | null;
  p_miss: number[];
}

export interface NetStories {
  from: string;
  source: string;
  model: { version: string; manifest_sha256: string | null };
  weighting: string;
  /** Alerts per 8-hour shift at the headline budget. */
  budget_per_shift: number;
  tick_min: number;
  stories: Record<FateId, NetStory>;
}

/** The planted fault each story's specimen carries. */
export const FAULT: Record<FateId, string> = {
  arrives: 'none',
  stalls: 'stalled_at_station',
  lost: 'lost_in_tube',
  unsent: 'not_dispatched',
};

/** Tier weights at the 3-2-1 weighting, as config/triage.yaml sets them (urgency_weights.default). */
const WEIGHT_321: Record<number, number> = { 1: 3, 2: 2, 3: 1 };

const byId = (a: NeuralSpecimen, b: NeuralSpecimen): number => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);

/**
 * The fixed rule. Among specimens with a 120 min window, tier 1 and the tube route, with the planted
 * fault matching the fate (arrives and stalls also received within the window), take the one received
 * nearest the script's receipt minute, or for a fate never received the lowest id; ties go to the
 * lowest id. Throws unless TracerNet's first crossing of its alert line, the first 5-minute tick where
 * p_miss times the tier's weight reaches the knob, is the stored alert minute.
 */
export function pickStories(neural: NeuralFile, fates: Record<FateId, Fate>): NetStories {
  const knobs = Object.values(neural.headline_knobs);
  if (knobs.length !== 1) throw new Error(`expected one tuning group, found ${knobs.length}`);
  const knob = knobs[0];
  const h = neural.headline_index;
  const pool = neural.specimens.filter((s) => s.window_min === 120 && s.tier === 1 && s.route === 'tube');
  const stories = {} as Record<FateId, NetStory>;
  for (const fate of Object.keys(FAULT) as FateId[]) {
    const target = fates[fate].received;
    let found = pool.filter((s) => s.truth.fault === FAULT[fate]);
    if (target !== null) {
      found = found.filter((s) => s.received_min !== null && s.received_min <= s.window_min);
      found.sort((a, b) => Math.abs(a.received_min! - target) - Math.abs(b.received_min! - target) || byId(a, b));
    } else {
      found.sort(byId);
    }
    const s = found[0];
    if (!s) throw new Error(`no specimen for ${fate}`);
    if (target === null && s.received_min !== null) throw new Error(`${s.id} was received, but the ${fate} story never is`);
    const weight = WEIGHT_321[s.tier];
    const alert = s.alerts.tracernet['3-2-1'][h];
    if (alert !== s.headline.alert_min) throw new Error(`${s.id}: the headline alert and the grid's differ`);
    const i = s.p_miss.findIndex((p) => p * weight >= knob);
    const crossing = i < 0 ? null : neural.tick_min * (i + 1);
    if (crossing !== alert) throw new Error(`${s.id}: p_miss first reaches the line at ${crossing} min, the stored alert is ${alert}`);
    stories[fate] = {
      id: s.id, type: s.type, tier: s.tier, window_min: s.window_min, received_min: s.received_min,
      weight, knob, line: knob / weight, alert_min: alert, p_miss: s.p_miss,
    };
  }
  return {
    from: 'docs/demo/neural.json',
    source: neural.source,
    model: { version: neural.model.version, manifest_sha256: neural.model.manifest_sha256 },
    weighting: '3-2-1',
    budget_per_shift: neural.grid_budgets[h],
    tick_min: neural.tick_min,
    stories,
  };
}

/**
 * TracerNet's p_miss as the page shows it at whole minute m: the last tick at or before m, held after
 * the specimen's last silent tick; null before the first tick (minute 5), when there is no score yet.
 */
export function scoreAt(s: NetStory, m: number, tick: number): number | null {
  const i = Math.floor(m / tick) - 1;
  return i < 0 ? null : s.p_miss[Math.min(i, s.p_miss.length - 1)];
}

/**
 * A risk as the page prints it: one decimal under 10%, where TracerNet's alert line sits (about 1.9%),
 * whole percent above, and a plain 0% for the known outcome of a scan inside the window.
 */
export const riskText = (p: number): string => (p > 0 && p < 0.1 ? `${(p * 100).toFixed(1)}%` : `${Math.round(p * 100)}%`);

/** The chart's log axis starts here; every score the page shows lies above it (tested). */
export const CHART_FLOOR = 0.005;
