// The exhibit's state and everything it displays, from the model. Pure TypeScript. Every input path
// (drags, console, card, keys, presets, the address) changes the state through the functions here,
// and every number on screen comes from read(). Readouts are taken at whole minutes, a whole-minute
// window and a whole-percent alert line, so any two paths to the same settings show the same numbers.

import { FATES, truthAt, type FateId, type Fate, type Place } from './fates';
import {
  alertMinute, componentWeights, locationSeries, MAX_T, riskReceived, riskUnreceived, TUBE_AGES, type LocationStep, type Params,
} from './tracer';

export interface ExhibitState {
  /** The scripted true fate (a TRY preset); the model sees only the scans it produces. */
  fate: FateId;
  /** Minutes since the collection scan, 0 to 130. Continuous: drags and playback move it smoothly. */
  silence: number;
  /** Alert line, the operating point on P(miss window), 0.10 to 0.90. */
  alertLine: number;
  /** Stability window, minutes, 60 to 240. */
  windowMin: number;
  /** The real carrier is shown. */
  truth: boolean;
}

export type Key = 'silence' | 'alertLine' | 'windowMin';

export const LIMITS: Record<Key, [number, number]> = {
  silence: [0, MAX_T],
  alertLine: [0.1, 0.9],
  windowMin: [60, 240],
};

/** Key steps: fine, and coarse with Shift. */
export const STEPS: Record<Key, [number, number]> = {
  silence: [1, 5],
  alertLine: [0.01, 0.05],
  windowMin: [5, 15],
};

export interface Preset { id: FateId; label: string; silence: number }

/** The fates in teaching order, each at the minute that shows its idea. */
export const PRESETS: Preset[] = [
  { id: 'arrives', label: FATES.arrives.label, silence: 26 },
  { id: 'stalls', label: FATES.stalls.label, silence: 55 },
  { id: 'lost', label: FATES.lost.label, silence: 90 },
  { id: 'unsent', label: FATES.unsent.label, silence: 55 },
];

const clamp = (v: number, [lo, hi]: [number, number]) => Math.min(hi, Math.max(lo, v));

/** The start state: the stalled specimen of architecture.md §3 at its alert minute, truth shown. */
export function defaultState(p: Params): ExhibitState {
  return withPreset({ fate: 'stalls', silence: 0, alertLine: p.operatingPoint, windowMin: p.windowMin, truth: true }, 'stalls', p);
}

export function withValue(s: ExhibitState, key: Key, v: number): ExhibitState {
  if (!Number.isFinite(v)) return s;
  return { ...s, [key]: clamp(v, LIMITS[key]) };
}

/** One key press: a fine step, or a coarse one with Shift. */
export function nudge(s: ExhibitState, key: Key, dir: 1 | -1, coarse = false): ExhibitState {
  return withValue(s, key, s[key] + dir * STEPS[key][coarse ? 1 : 0]);
}

/** A preset shows its fate at its moment, with the default line and window and the truth in view. */
export function withPreset(s: ExhibitState, id: FateId, p: Params): ExhibitState {
  const preset = PRESETS.find((q) => q.id === id);
  if (!preset) return s;
  return { ...s, fate: id, silence: preset.silence, alertLine: clamp(p.operatingPoint, LIMITS.alertLine), windowMin: clamp(p.windowMin, LIMITS.windowMin), truth: true };
}

export function withTruth(s: ExhibitState, on: boolean): ExhibitState {
  return { ...s, truth: on };
}

/** The inputs as the readouts use them: whole minutes, a whole-minute window, a whole-percent line. */
export function quantized(s: ExhibitState): { minute: number; windowMin: number; alertLine: number } {
  return {
    minute: Math.round(clamp(s.silence, LIMITS.silence)),
    windowMin: Math.round(clamp(s.windowMin, LIMITS.windowMin)),
    alertLine: Math.round(clamp(s.alertLine, LIMITS.alertLine) * 100) / 100,
  };
}

/** The preset the state shows, or null (Custom). */
export function matchPreset(s: ExhibitState, p: Params): FateId | null {
  const q = quantized(s);
  const hit = PRESETS.find((pr) => pr.id === s.fate && Math.abs(pr.silence - s.silence) < 0.25);
  if (!hit) return null;
  const line = Math.round(clamp(p.operatingPoint, LIMITS.alertLine) * 100) / 100;
  return q.alertLine === line && q.windowMin === Math.round(clamp(p.windowMin, LIMITS.windowMin)) ? hit.id : null;
}

export const PLACES: Place[] = ['ward', 'tube', 'station', 'lost', 'lab'];

export interface Reading {
  minute: number;
  windowMin: number;
  alertLine: number;
  /** Silent: no receipt scan yet. Received: in the lab, not yet plated. Plated: the clock has stopped. */
  phase: 'silent' | 'received' | 'plated';
  /** Scans the lab has seen by now (minutes), besides the collection scan at 0. */
  received: number | null;
  plated: number | null;
  /** P(miss the window). */
  risk: number;
  /** Where the specimen is: the location posterior while silent, the lab once scanned. Sums to 1. */
  places: Record<Place, number>;
  /** The stalled part of `places.station`. */
  stalled: number;
  likely: { place: Place; p: number };
  alert: {
    /** The minute the alert would fire if the silence continues (the chart and the card's marker). */
    projected: number | null;
    /** The minute it fires for this specimen (the original rule, bounded by its receipt scan). */
    minute: number | null;
    fired: boolean;
    /** The receipt scan that cleared a fired alert. */
    clearedAt: number | null;
  };
  /** max(0, window − minute). */
  windowLeft: number;
  /** Warning the projected alert buys: window − projected alert minute. */
  lead: number | null;
  /** The mixture's weights among specimens still silent (normal, stalled…, then lost); null once scanned. */
  components: { parts: number[]; lost: number } | null;
  /** The window closed before plating. */
  missed: boolean;
  /** The last receipt scan and the risk an instant before and after it. */
  scan: { at: number; before: number; after: number } | null;
}

export interface Glow {
  ward: number;
  tube: number;
  station: number;
  lost: number;
  lab: number;
  /** The tube's share by minutes in the tube (ages 0 to TUBE_AGES − 1); sums to `tube`. */
  tubeAges: Float64Array;
}

/** The model with its caches: the location series (it depends on the parameters only) and risk curves per window. */
export class TracerModel {
  readonly params: Params;
  readonly series: LocationStep[];
  private curves = new Map<number, Float64Array>();
  private ages = new Float64Array(TUBE_AGES);

  constructor(params: Params) {
    this.params = params;
    this.series = locationSeries(MAX_T, params);
  }

  /** riskUnreceived at every whole minute 0 to MAX_T, for a whole-minute window. */
  curve(W: number): Float64Array {
    let c = this.curves.get(W);
    if (!c) {
      c = new Float64Array(MAX_T + 1);
      for (let t = 0; t <= MAX_T; t++) c[t] = riskUnreceived(t, this.params, W);
      this.curves.set(W, c);
    }
    return c;
  }

  /** P(miss) for this fate at whole minute t, as the lab's Tracer would have computed it then. */
  riskAt(fate: Fate, t: number, W: number): number {
    if (fate.received === null || t < fate.received) return this.curve(W)[t];
    if (fate.plated === null || t < fate.plated) return riskReceived(t, this.params, W);
    return fate.plated <= W ? 0 : 1;
  }

  read(s: ExhibitState): Reading {
    const { minute: m, windowMin: W, alertLine: line } = quantized(s);
    const p = this.params;
    const fate = FATES[s.fate];
    const curve = this.curve(W);
    const received = fate.received !== null && m >= fate.received ? fate.received : null;
    const plated = fate.plated !== null && m >= fate.plated ? fate.plated : null;
    const phase = plated !== null ? 'plated' : received !== null ? 'received' : 'silent';
    const risk = this.riskAt(fate, m, W);

    const places: Record<Place, number> = { ward: 0, tube: 0, station: 0, lost: 0, lab: 0 };
    let stalled = 0;
    if (phase === 'silent') {
      const step = this.series[m];
      const [ward, tube, station] = p.smearOf.map((legs) => legs.reduce((sum, i) => sum + step.legs[i], 0));
      places.ward = ward;
      places.tube = tube;
      places.station = station;
      places.lost = step.lost;
      stalled = p.smearOf[2].slice(1).reduce((sum, i) => sum + step.legs[i], 0);
    } else {
      places.lab = 1;
    }
    let likely: Place = 'ward';
    for (const k of PLACES) if (places[k] > places[likely]) likely = k;

    const upTo = fate.received ?? MAX_T;
    const minute = alertMinute((t) => this.riskAt(fate, t, W), line, p.tickMin, upTo);
    const projected = alertMinute((t) => curve[t], line, p.tickMin, MAX_T);
    const fired = minute !== null && minute <= m;
    return {
      minute: m, windowMin: W, alertLine: line, phase, received, plated, risk, places, stalled,
      likely: { place: likely, p: places[likely] },
      alert: { projected, minute, fired, clearedAt: fired && received !== null ? received : null },
      windowLeft: Math.max(0, W - m),
      lead: projected === null ? null : W - projected,
      components: phase === 'silent' ? componentWeights(m, p) : null,
      missed: m >= W && !(plated !== null && plated <= W),
      scan: received === null ? null : { at: received, before: curve[received], after: riskReceived(received, p, W) },
    };
  }

  /**
   * The light to draw at a continuous minute: the location posterior interpolated between whole
   * minutes while silent, all of it in the lab once the receipt scan has come.
   */
  glow(fate: FateId, silence: number): Glow {
    const f = FATES[fate];
    const t = Math.min(MAX_T, Math.max(0, silence));
    if (f.received !== null && t >= f.received) {
      this.ages.fill(0);
      return { ward: 0, tube: 0, station: 0, lost: 0, lab: 1, tubeAges: this.ages };
    }
    const i0 = Math.floor(t);
    const i1 = Math.min(MAX_T, i0 + 1);
    const k = t - i0;
    const a = this.series[i0];
    const b = this.series[i1];
    const place = (legs: number[]) => legs.reduce((sum, i) => sum + a.legs[i] * (1 - k) + b.legs[i] * k, 0);
    for (let j = 0; j < TUBE_AGES; j++) this.ages[j] = a.tubeAges[j] * (1 - k) + b.tubeAges[j] * k;
    const [ward, tube, station] = this.params.smearOf.map(place);
    return { ward, tube, station, lost: a.lost * (1 - k) + b.lost * k, lab: 0, tubeAges: this.ages };
  }

  /** Where the real carrier is (the truth), for the scene and the truth labels. */
  truth(fate: FateId, silence: number): ReturnType<typeof truthAt> {
    return truthAt(FATES[fate], silence);
  }
}
