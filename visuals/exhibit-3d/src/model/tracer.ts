// Tracer's analytic model, ported line for line from the working model in visuals/architecture.html
// (architecture.md §5.2, risk, and §5.3, location). Pure TypeScript, no three imports. Every
// parameter is an assumption: nothing measured, nothing fitted, no institutional data. The
// arithmetic keeps the original's order of operations, so the port agrees with it to the last bit
// (tracer.test.ts runs the original under node and compares).

export type ExitKind = 'leg' | 'lost' | 'recv';

/** A sub-leg inside the unobserved interval: a lognormal duration and where the specimen goes next. */
export interface Leg {
  name: string;
  median: number;
  sigma: number;
  /** [kind, target leg (for 'leg'), probability]; probabilities sum to 1. */
  exit: [ExitKind, number, number][];
}

export interface Params {
  /** Where the numbers come from: 'assumed' until fitted parameters replace them. */
  source: string;
  /** Collection → receipt as a mixture of lognormals (minutes); weights sum with lossFraction to 1. */
  mix: { w: number; m: number; s: number; label: string }[];
  /** The defective mass: specimens that never arrive. */
  lossFraction: number;
  /** Receipt → plated, lognormal (minutes). */
  setupMedian: number;
  setupSigma: number;
  /** Stability window, minutes from collection. */
  windowMin: number;
  /** Re-score cadence; the alert rule checks on these ticks. */
  tickMin: number;
  /** Alert threshold on P(miss window). */
  operatingPoint: number;
  legs: Leg[];
  /** Which legs each place shows; the stalled specimen is still physically at the station. */
  smearOf: number[][];
}

/** The working model's parameters, exactly as architecture.html sets them. All assumed. */
export const ASSUMED: Params = {
  source: 'assumed',
  mix: [
    { w: 0.88, m: 24, s: 0.50, label: 'normal transit' },
    { w: 0.08, m: 70, s: 0.60, label: 'stalled, arrives late' },
  ],
  lossFraction: 0.04,
  setupMedian: 22,
  setupSigma: 0.40,
  windowMin: 120,
  tickMin: 5,
  operatingPoint: 0.45,
  legs: [
    { name: 'Awaiting dispatch', median: 6, sigma: 0.60, exit: [['leg', 1, 0.992], ['lost', 0, 0.008]] },
    { name: 'In the tube', median: 4, sigma: 0.25, exit: [['leg', 2, 0.995], ['lost', 0, 0.005]] },
    { name: 'At the station', median: 11, sigma: 0.70, exit: [['recv', 0, 0.910], ['leg', 3, 0.070], ['lost', 0, 0.020]] },
    { name: 'Stalled', median: 45, sigma: 0.80, exit: [['recv', 0, 0.850], ['lost', 0, 0.150]] },
  ],
  smearOf: [[0], [1], [2, 3]],
};

/** The longest silence the exhibit plays (minutes), as in the original's plot. */
export const MAX_T = 130;
/** Ages per leg the forward filter tracks (minutes), as in the original. */
const CAP = 180;
/** Minutes-in-tube ages kept for drawing the tube's smear; a tube trip is over well before this. */
export const TUBE_AGES = 24;

/** Abramowitz & Stegun 7.1.26 (absolute error under 1.5e-7). */
export function erf(x: number): number {
  const s = x < 0 ? -1 : 1;
  x = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * x);
  const y = 1 - ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
  return s * y;
}
export const normCdf = (z: number): number => 0.5 * (1 + erf(z / Math.SQRT2));
export const lnCdf = (t: number, m: number, s: number): number => (t <= 0 ? 0 : normCdf(Math.log(t / m) / s));
export const lnSurv = (t: number, m: number, s: number): number => 1 - lnCdf(t, m, s);
export const lnPdf = (t: number, m: number, s: number): number =>
  t <= 0 ? 0 : Math.exp(-Math.pow(Math.log(t / m), 2) / (2 * s * s)) / (t * s * Math.sqrt(2 * Math.PI));

/**
 * P(window missed | no receipt scan yet at t minutes). A mixture of lognormal transits and a fraction
 * that never arrives: as t passes the normal transit mode, the surviving mass is increasingly the
 * failure component.
 */
export function riskUnreceived(t: number, p: Params, W = p.windowMin): number {
  const { mix, setupMedian: m2, setupSigma: s2, lossFraction: pl } = p;
  let notReceived = pl;
  let safe = 0; // arrives at u, then set up in time
  for (const c of mix) {
    notReceived += c.w * lnSurv(t, c.m, c.s);
    for (let u = Math.max(t, 0) + 0.5; u < W; u += 1) {
      safe += c.w * lnPdf(u, c.m, c.s) * lnCdf(W - u, m2, s2);
    }
  }
  return 1 - safe / notReceived;
}

/** After the receipt scan only the in-lab leg remains. */
export function riskReceived(t: number, p: Params, W = p.windowMin): number {
  return lnSurv(W - t, p.setupMedian, p.setupSigma);
}

/**
 * The mixture's posterior weights while silent: of the specimens with no receipt scan by t, the
 * share in each transit component and the share that never arrives. These are the terms of
 * riskUnreceived's denominator, normalized.
 */
export function componentWeights(t: number, p: Params): { parts: number[]; lost: number } {
  const parts = p.mix.map((c) => c.w * lnSurv(t, c.m, c.s));
  let notReceived = p.lossFraction;
  for (const v of parts) notReceived += v;
  return { parts: parts.map((v) => v / notReceived), lost: p.lossFraction / notReceived };
}

export interface LocationStep {
  /** P(each leg | no receipt scan yet). */
  legs: number[];
  /** P(lost | no receipt scan yet). */
  lost: number;
  /** P(in the tube leg with this many minutes in it | no receipt scan yet), ages 0 to TUBE_AGES − 1. */
  tubeAges: Float64Array;
}

/**
 * Location posterior: forward filter over sub-legs on a one-minute grid, conditioned on "no receipt
 * scan yet". Absence of a scan is the input, not a special case. The extra tubeAges output copies
 * the tube leg's age distribution for drawing; it changes none of the arithmetic.
 */
export function locationSeries(maxT: number, p: Params, tubeLeg = 1): LocationStep[] {
  const legs = p.legs;
  const N = legs.length;
  let alpha = legs.map(() => new Float64Array(CAP)); // [leg][minutes in that leg]
  alpha[0][0] = 1;
  let lost = 0;
  const out: LocationStep[] = [];
  const snapshot = () => {
    const mass = alpha.map((a) => {
      let s = 0;
      for (let k = 0; k < a.length; k++) s += a[k];
      return s;
    });
    let live = 0; // conditioned on not received
    for (const m of mass) live += m;
    live += lost;
    const tubeAges = new Float64Array(TUBE_AGES);
    if (tubeLeg < N) for (let k = 0; k < TUBE_AGES; k++) tubeAges[k] = alpha[tubeLeg][k] / live;
    out.push({ legs: mass.map((m) => m / live), lost: lost / live, tubeAges });
  };
  snapshot();
  for (let t = 1; t <= maxT; t++) {
    const next = legs.map(() => new Float64Array(CAP));
    for (let i = 0; i < N; i++) {
      const { median: m, sigma: s, exit } = legs[i];
      for (let a = 0; a < CAP - 1; a++) {
        const mass = alpha[i][a];
        if (mass < 1e-12) continue;
        const surv = Math.max(lnSurv(a, m, s), 1e-9);
        const hazard = Math.min(Math.max((lnCdf(a + 1, m, s) - lnCdf(a, m, s)) / surv, 0), 1);
        const leaving = mass * hazard;
        next[i][a + 1] += mass - leaving;
        for (const [kind, target, q] of exit) {
          if (kind === 'leg') next[target][0] += leaving * q;
          else if (kind === 'lost') lost += leaving * q;
          // 'recv': the mass leaves the silent population; the snapshot conditions it away.
        }
      }
    }
    alpha = next;
    snapshot();
  }
  return out;
}

/**
 * The alert fires at the first tick past the operating point: minutes 0, tick, 2·tick, … up to
 * and including `upTo` (the receipt scan, or the end of the run when none comes). The same rule
 * the triage component applies, so the alert minute is computed, not chosen.
 */
export function alertMinute(risk: (t: number) => number, line: number, tick: number, upTo: number): number | null {
  for (let t = 0; t <= upTo; t += tick) {
    if (risk(t) >= line) return t;
  }
  return null;
}
