// The words around the numbers: the help sheet quotes the model, and the caption colors every number.

import { describe, expect, it } from 'vitest';
import { FATE_IDS } from '../model/fates';
import { TracerModel } from '../model/state';
import { ASSUMED, MAX_T } from '../model/tracer';
import { buildSchema, caption } from './schema';

const model = new TracerModel(ASSUMED);
const help = buildSchema(ASSUMED, model).help;
const text = [help.intro, ...help.sections.flatMap((s) => s.body), ...(help.misconception?.body ?? []), ...help.exact.body].join('\n');

describe('the help sheet quotes the model', () => {
  it('the worked example at the alert minute (architecture.md §3)', () => {
    expect(text).toContain('at 60 min, 25 of every 100 still-silent specimens are on a normal trip');
    expect(text).toContain('Tracer still puts 64% on the station and 36% on lost');
    expect(text).toContain('Lost becomes the best explanation only at 77 min');
  });

  it('the clock stops at the receipt scan: the outcome is known and nothing after it enters the model', () => {
    expect(text).toContain('so the risk is 0.0% or 100.0%');
    expect(text).toContain('Nothing after the receipt scan, plating included, enters the model.');
    expect(text).not.toContain('Setup, receipt to plating');
    expect(text).not.toContain('midpoint');
  });

  it('every assumed parameter, with its spread and exits', () => {
    expect(text).toContain('88% normal (median 24 min, σ 0.50), 8% late (median 70 min, σ 0.60), 4% never arrive');
    expect(text).toContain('Window: 120 min from the collection scan.');
    expect(text).toContain('At the station, median 11 min, σ 0.70: 91% → received, 7% → stalled, 2% → lost.');
    expect(text).toContain('Stalled, median 45 min, σ 0.80: 85% → received, 15% → lost.');
  });

  it('the loupe and the lost bin, from their two models', () => {
    expect(text).toContain('at 60 min the loupe has 34 of 100 never arriving while the lost bin holds 36%');
  });
});

describe('the caption', () => {
  it('colors every number it writes, at every minute of every fate', () => {
    for (const fate of FATE_IDS) {
      for (let silence = 0; silence <= MAX_T; silence++) {
        const r = model.read({ fate, silence, alertLine: ASSUMED.operatingPoint, windowMin: ASSUMED.windowMin, truth: true });
        const c = caption(r, { shown: false, place: r.likely.place, words: '' }, ASSUMED);
        expect(c.replace(/\{[a-z]+:[^}]*\}/g, ''), `${fate} at ${silence} min: ${c}`).not.toMatch(/\d/);
      }
    }
  });
});
