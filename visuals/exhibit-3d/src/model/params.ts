// Fitted parameters from the Python pipeline can replace the assumed ones without code changes:
// window.TRACER_PARAMS, or ?params=<same-origin JSON url>. An override names only what it changes
// (arrays are replaced whole) and is checked before use; one that fails a check is ignored whole.
// Without a `source` of its own, an override is still labeled assumed.

import type { ExitKind, Params } from './tracer';

type Result = { params: Params; error?: undefined } | { params?: undefined; error: string };

const positive = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v > 0;
const unit = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 1;
const near1 = (v: number) => Math.abs(v - 1) < 1e-6;

export function applyOverride(base: Params, override: unknown): Result {
  if (!override || typeof override !== 'object' || Array.isArray(override)) return { error: 'the override is not an object' };
  const o = override as Partial<Record<keyof Params, unknown>>;
  const p = { ...base, ...o, source: typeof o.source === 'string' && o.source.trim() ? o.source.trim() : 'assumed' } as Params;

  if (!Array.isArray(p.mix) || p.mix.length === 0) return { error: 'mix must list at least one component' };
  for (const c of p.mix) {
    if (!c || !positive(c.w) || !positive(c.m) || !positive(c.s)) return { error: 'each mix component needs positive w, m and s' };
  }
  if (!unit(p.lossFraction) || p.lossFraction >= 1) return { error: 'lossFraction must lie in [0, 1)' };
  if (!near1(p.mix.reduce((s, c) => s + c.w, p.lossFraction))) return { error: 'mix weights and lossFraction must sum to 1' };
  if (!positive(p.windowMin)) return { error: 'windowMin must be positive' };
  if (!positive(p.tickMin) || !Number.isInteger(p.tickMin)) return { error: 'tickMin must be a positive whole number of minutes' };
  if (!unit(p.operatingPoint) || p.operatingPoint === 0) return { error: 'operatingPoint must lie in (0, 1]' };

  if (!Array.isArray(p.legs) || p.legs.length < 2) return { error: 'legs must list the sub-legs' };
  const kinds: ExitKind[] = ['leg', 'lost', 'recv'];
  for (const leg of p.legs) {
    if (!leg || !positive(leg.median) || !positive(leg.sigma) || !Array.isArray(leg.exit)) return { error: 'each leg needs a positive median and sigma and an exit list' };
    let sum = 0;
    for (const e of leg.exit) {
      if (!Array.isArray(e) || !kinds.includes(e[0]) || !unit(e[2])) return { error: 'each exit is [kind, target, probability]' };
      if (e[0] === 'leg' && !(Number.isInteger(e[1]) && e[1] >= 0 && e[1] < p.legs.length)) return { error: 'a leg exit must name an existing leg' };
      sum += e[2];
    }
    if (!near1(sum)) return { error: `exits of "${leg.name}" must sum to 1` };
  }
  // Three places (ward, tube, station), each a list of legs; the first leg of the station is
  // "at the station", the rest of it "stalled".
  if (!Array.isArray(p.smearOf) || p.smearOf.length !== 3 || !p.smearOf.every((g) => Array.isArray(g) && g.length > 0 && g.every((i) => Number.isInteger(i) && i >= 0 && i < p.legs.length))) {
    return { error: 'smearOf must map ward, tube and station to existing legs' };
  }
  return { params: p };
}
