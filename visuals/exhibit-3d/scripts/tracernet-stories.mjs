// Writes src/model/tracernet-stories.json: for each story of the self-playing page, the held-out
// specimen the fixed rule (src/model/tracernet.ts, pickStories) picks from docs/demo/neural.json, with
// TracerNet's p_miss, alert minute and alert line, and the model's version and manifest checksum. The
// page imports the small file at build time and never fetches neural.json. Exits non-zero when
// TracerNet's first crossing of a specimen's alert line is not its stored alert minute, so
// `npm run build:play`, which runs this first, fails too. Run from visuals/exhibit-3d: npm run stories.

import { readFileSync, writeFileSync } from 'node:fs';
import { FATES } from '../src/model/fates.ts';
import { pickStories } from '../src/model/tracernet.ts';

const neural = JSON.parse(readFileSync(new URL('../../../docs/demo/neural.json', import.meta.url), 'utf8'));
const out = pickStories(neural, FATES);
writeFileSync(new URL('../src/model/tracernet-stories.json', import.meta.url), `${JSON.stringify(out, null, 2)}\n`);
for (const [fate, s] of Object.entries(out.stories)) {
  console.log(`${fate}: ${s.id}, received ${s.received_min ?? 'never'}, alert ${s.alert_min === null ? 'none' : `at ${s.alert_min} min`}, line ${(s.line * 100).toFixed(2)}%`);
}
