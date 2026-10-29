// Address parameters that open the exhibit in a specimen's situation (the plan page and the demo
// link to it this way). Parsed once at load; an invalid value is ignored, never clamped:
//   ?fate=arrives|stalls|lost|unsent  the TRY preset to start from
//   ?silence=0..130  ?alert=0.1..0.9  ?window=60..240  ?truth=0|1
//   ?embed=1  hide the maker plate and the overlays a small frame cannot hold
//   ?params=<same-origin JSON url>  replace any of the assumed parameters
// Every value still goes through the model's own setters, so a linked state reads exactly as the
// same state reached through the controls.

import { FATE_IDS, type FateId } from './fates';
import { defaultState, LIMITS, withPreset, withTruth, withValue, type ExhibitState } from './state';
import type { Params } from './tracer';

export interface SpecimenQuery {
  fate: FateId | null;
  silence: number | null;
  alert: number | null;
  window: number | null;
  truth: boolean | null;
  embed: boolean;
  params: string | null;
}

export function readSpecimenQuery(search: string): SpecimenQuery {
  const q = new URLSearchParams(search);
  const inRange = (key: string, [lo, hi]: [number, number]) => {
    const raw = q.get(key);
    if (raw === null || raw.trim() === '') return null;
    const v = Number(raw);
    return Number.isFinite(v) && v >= lo && v <= hi ? v : null;
  };
  const fate = q.get('fate');
  const truth = q.get('truth');
  const embed = (q.get('embed') ?? '').toLowerCase();
  return {
    fate: FATE_IDS.includes(fate as FateId) ? (fate as FateId) : null,
    silence: inRange('silence', LIMITS.silence),
    alert: inRange('alert', LIMITS.alertLine),
    window: inRange('window', LIMITS.windowMin),
    truth: truth === '1' || truth === 'true' ? true : truth === '0' || truth === 'false' ? false : null,
    embed: embed === '1' || embed === 'true',
    params: q.get('params') || null,
  };
}

/** The visitor arrived on purpose: the address sets up a specimen's state. */
export function setsUpState(q: SpecimenQuery): boolean {
  return q.fate !== null || q.silence !== null || q.alert !== null || q.window !== null || q.truth !== null;
}

/** The start state the address asks for: the default, then the preset, then each override. */
export function startState(q: SpecimenQuery, p: Params): ExhibitState {
  let s = defaultState(p);
  if (q.fate) s = withPreset(s, q.fate, p);
  if (q.silence !== null) s = withValue(s, 'silence', q.silence);
  if (q.alert !== null) s = withValue(s, 'alertLine', q.alert);
  if (q.window !== null) s = withValue(s, 'windowMin', q.window);
  if (q.truth !== null) s = withTruth(s, q.truth);
  return s;
}

/** The ?params address resolved against the page, or null when it leaves the page's origin. */
export function sameOriginUrl(raw: string, base: string): string | null {
  try {
    const u = new URL(raw, base);
    return u.origin === new URL(base).origin ? u.href : null;
  } catch {
    return null;
  }
}
