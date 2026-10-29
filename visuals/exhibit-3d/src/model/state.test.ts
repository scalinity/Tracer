import { describe, expect, it } from 'vitest';
import { FATES, truthAt } from './fates';
import { applyOverride } from './params';
import {
  defaultState, matchPreset, nudge, PRESETS, TracerModel, withPreset, withTruth, withValue, type ExhibitState,
} from './state';
import { ASSUMED, MAX_T, riskReceived, riskUnreceived } from './tracer';
import { readSpecimenQuery, sameOriginUrl, setsUpState, startState } from './url';

const model = new TracerModel(ASSUMED);
const at = (fate: ExhibitState['fate'], silence: number, extra: Partial<ExhibitState> = {}): ExhibitState =>
  ({ ...defaultState(ASSUMED), fate, silence, ...extra });

describe('the start state and the presets', () => {
  it('starts on the stalled specimen of architecture.md §3 at its alert minute, truth shown', () => {
    const s = defaultState(ASSUMED);
    expect(s).toEqual({ fate: 'stalls', silence: 55, alertLine: 0.45, windowMin: 120, truth: true });
    expect(matchPreset(s, ASSUMED)).toBe('stalls');
  });

  it('On time: the receipt scan at 24 min ends the silence and the risk falls to 0.0%', () => {
    const r = model.read(withPreset(defaultState(ASSUMED), 'arrives', ASSUMED));
    expect(r.phase).toBe('received');
    expect(r.scan).toEqual({ at: 24, before: riskUnreceived(24, ASSUMED), after: riskReceived(24, ASSUMED) });
    expect(r.risk).toBe(riskReceived(26, ASSUMED));
    expect(r.risk).toBeLessThan(0.0005);
    expect(r.likely).toEqual({ place: 'lab', p: 1 });
    expect(r.alert.fired).toBe(false);
  });

  it('Stalls: the alert fires at 55 min with 65 min left, and the station is most likely', () => {
    const r = model.read(withPreset(defaultState(ASSUMED), 'stalls', ASSUMED));
    expect(r.alert).toEqual({ projected: 55, minute: 55, fired: true, clearedAt: null });
    expect(r.windowLeft).toBe(65);
    expect(r.lead).toBe(65);
    expect(r.likely.place).toBe('station');
    expect(r.likely.p).toBeCloseTo(0.6845, 4);
  });

  it('Stalls: the receipt scan at 68 min clears the alert', () => {
    const r = model.read(at('stalls', 70));
    expect(r.alert).toEqual({ projected: 55, minute: 55, fired: true, clearedAt: 68 });
    expect(r.risk).toBe(riskReceived(70, ASSUMED));
  });

  it('Lost: by 90 min lost is the best explanation', () => {
    const r = model.read(withPreset(defaultState(ASSUMED), 'lost', ASSUMED));
    expect(r.likely.place).toBe('lost');
    expect(r.likely.p).toBeCloseTo(0.5968, 4);
    expect(r.risk).toBeCloseTo(0.9276, 4);
  });

  it('Never sent reads exactly like Stalls at 55 min: silence carries no clue', () => {
    const a = model.read(withPreset(defaultState(ASSUMED), 'unsent', ASSUMED));
    const b = model.read(withPreset(defaultState(ASSUMED), 'stalls', ASSUMED));
    expect(a).toEqual(b);
    expect(truthAt(FATES.unsent, 55).spot).toBe('ward');
    expect(truthAt(FATES.stalls, 55).spot).toBe('station');
  });

  it('presets come in teaching order with keys 1 to 4', () => {
    expect(PRESETS.map((p) => p.label)).toEqual(['On time', 'Stalls', 'Lost', 'Never sent']);
  });
});

describe('readouts', () => {
  it('places sum to 1 while silent and after the scan', () => {
    for (const fate of ['arrives', 'stalls', 'lost', 'unsent'] as const) {
      for (let t = 0; t <= MAX_T; t += 7) {
        const r = model.read(at(fate, t));
        expect(Object.values(r.places).reduce((s, v) => s + v, 0)).toBeCloseTo(1, 12);
      }
    }
  });

  it('readouts use whole minutes, a whole-minute window and a whole-percent line', () => {
    expect(model.read(at('lost', 54.6, { alertLine: 0.3049, windowMin: 89.6 }))).toEqual(model.read(at('lost', 55, { alertLine: 0.3, windowMin: 90 })));
  });

  it('warning left is the window minus the silence, floored at 0', () => {
    expect(model.read(at('lost', 100, { windowMin: 120 })).windowLeft).toBe(20);
    expect(model.read(at('lost', 130, { windowMin: 120 })).windowLeft).toBe(0);
  });

  it('a lower line warns sooner: 30% alerts at 45 min with 75 min of lead', () => {
    const r = model.read(at('lost', 55, { alertLine: 0.3 }));
    expect(r.alert.projected).toBe(45);
    expect(r.lead).toBe(75);
  });

  it('a shorter window warns sooner and leaves less warning: 60 min alerts at 20 with 40 left', () => {
    const r = model.read(at('lost', 55, { windowMin: 60 }));
    expect(r.alert.projected).toBe(20);
    expect(r.lead).toBe(40);
    expect(model.read(at('lost', 55, { windowMin: 90 })).lead).toBeLessThan(65);
  });

  it('plating inside the window ends the risk; a window shorter than the trip is missed', () => {
    expect(model.read(at('stalls', 90)).risk).toBe(0);
    const late = model.read(at('stalls', 90, { windowMin: 60 }));
    expect(late.risk).toBe(1);
    expect(late.missed).toBe(true);
  });

  it('the glow is the posterior, interpolated between minutes, and all in the lab after the scan', () => {
    for (const t of [0, 3.5, 8.25, 41.9, 129.5]) {
      const g = model.glow('lost', t);
      expect(g.ward + g.tube + g.station + g.lost + g.lab).toBeCloseTo(1, 12);
      expect(g.tubeAges.reduce((s, v) => s + v, 0)).toBeCloseTo(g.tube, 6);
    }
    expect(model.glow('arrives', 30).lab).toBe(1);
  });
});

describe('the address opens the same state the controls reach', () => {
  it('a parameterized start reads exactly as the same state reached by keys and clicks', () => {
    const q = readSpecimenQuery('?fate=lost&silence=55&alert=0.3&window=90&truth=0');
    expect(setsUpState(q)).toBe(true);
    const linked = startState(q, ASSUMED);

    // The visitor: press 3 (Lost), then step the clock back from 90 to 55 with ',', lower the line
    // from 45% to 30% with '[', shorten the window from 120 to 90 min with '-', and press T.
    let s = withPreset(defaultState(ASSUMED), 'lost', ASSUMED);
    for (let i = 0; i < 35; i++) s = nudge(s, 'silence', -1);
    for (let i = 0; i < 15; i++) s = nudge(s, 'alertLine', -1);
    for (let i = 0; i < 6; i++) s = nudge(s, 'windowMin', -1);
    s = withTruth(s, false);

    expect(model.read(linked)).toEqual(model.read(s));
    expect(linked.truth).toBe(s.truth);
    expect(linked.fate).toBe(s.fate);
    expect(matchPreset(linked, ASSUMED)).toBe(matchPreset(s, ASSUMED));
  });

  it('a preset alone opens that preset', () => {
    const s = startState(readSpecimenQuery('?fate=arrives'), ASSUMED);
    expect(matchPreset(s, ASSUMED)).toBe('arrives');
  });

  it('invalid values are ignored, never clamped', () => {
    const q = readSpecimenQuery('?fate=gone&silence=200&alert=abc&window=30&truth=maybe');
    expect(setsUpState(q)).toBe(false);
    expect(startState(q, ASSUMED)).toEqual(defaultState(ASSUMED));
  });

  it('embed and params do not set up a state', () => {
    const q = readSpecimenQuery('?embed=1&params=fit.json');
    expect(q.embed).toBe(true);
    expect(q.params).toBe('fit.json');
    expect(setsUpState(q)).toBe(false);
  });

  it('?params must stay on the page\'s origin', () => {
    expect(sameOriginUrl('fit.json', 'http://localhost:5173/visuals/exhibit-3d/')).toBe('http://localhost:5173/visuals/exhibit-3d/fit.json');
    expect(sameOriginUrl('https://example.com/fit.json', 'http://localhost:5173/')).toBeNull();
  });

  it('withValue clamps to the physical ranges', () => {
    expect(withValue(defaultState(ASSUMED), 'windowMin', 400).windowMin).toBe(240);
    expect(withValue(defaultState(ASSUMED), 'silence', -3).silence).toBe(0);
  });
});

describe('parameter overrides', () => {
  it('a valid override replaces only what it names, and keeps its source', () => {
    const r = applyOverride(ASSUMED, { windowMin: 90, source: 'fitted, run 12' });
    expect(r.params?.windowMin).toBe(90);
    expect(r.params?.source).toBe('fitted, run 12');
    expect(r.params?.mix).toEqual(ASSUMED.mix);
  });

  it('an override without a source is still labeled assumed', () => {
    expect(applyOverride(ASSUMED, { setupMedian: 30 }).params?.source).toBe('assumed');
  });

  it('weights that do not sum to 1 are rejected whole', () => {
    expect(applyOverride(ASSUMED, { lossFraction: 0.1 }).error).toMatch(/sum to 1/);
  });

  it('exits that do not sum to 1 are rejected whole', () => {
    const legs = structuredClone(ASSUMED.legs);
    legs[2].exit[0][2] = 0.5;
    expect(applyOverride(ASSUMED, { legs }).error).toMatch(/must sum to 1/);
  });

  it('a new window flows into the start state', () => {
    const r = applyOverride(ASSUMED, { windowMin: 90 });
    expect(defaultState(r.params!).windowMin).toBe(90);
  });
});
