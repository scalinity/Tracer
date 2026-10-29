// The Silent Specimen HUD as data, the caption writer and the number formats. Every number written
// here comes from the model's reading at the moment it is shown.

import type { ExhibitSchema } from '../kit/schema';
import { FATES, type FateId, type Place } from '../model/fates';
import { PRESETS, type Reading, type TracerModel } from '../model/state';
import { lnSurv, MAX_T, type Params } from '../model/tracer';
import { silentCounts } from '../scene/loupe-drawing';

/** Playback rate at 1×, simulated minutes per second; the card names it on its speed buttons. */
export const MIN_PER_SECOND = 3;

/** A probability as a percentage: one decimal under 10%, whole above. */
export const placePct = (p: number) => (p < 0.095 ? `${(p * 100).toFixed(1)}%` : `${Math.round(p * 100)}%`);
export const riskPct = (p: number) => `${(p * 100).toFixed(1)}%`;
/** A place's probability in the color of its light: coral for lost, cyan for the rest. */
export const placeShare = (place: Place, p: number) => `{${place === 'lost' ? 'coral' : 'cyan'}:${placePct(p)}}`;
/** A parameter's share as written: 0.992 → 99.2%, 0.91 → 91%. */
const share = (w: number) => `${+(w * 100).toFixed(1)}%`;

export const PLACE_WORDS: Record<Place, string> = { ward: 'ward', tube: 'tube', station: 'station', lost: 'lost', lab: 'lab' };
const where: Record<Place, string> = { ward: 'the ward', tube: 'the tube', station: 'the station', lost: 'lost', lab: 'the lab' };

export function buildSchema(p: Params, model: TracerModel): ExhibitSchema {
  const normal = p.mix[0];
  const late = p.mix[1];
  const op = Math.round(p.operatingPoint * 100);
  // The help's worked example, read from the model on the default line and window: a specimen still
  // silent (every silent fate reads alike) at the minute it alerts.
  const at = (fate: FateId, silence: number) => model.read({ fate, silence, alertLine: p.operatingPoint, windowMin: p.windowMin, truth: true });
  const alerts = at('unsent', 0).alert.projected;
  const A = alerts ?? PRESETS.find((q) => q.id === 'stalls')!.silence;
  const ex = at('unsent', A);
  const [normalCount, , neverCount] = silentCounts(ex);
  let lostLeads: number | null = null;
  for (let t = 0; t <= MAX_T && lostLeads === null; t++) if (at('unsent', t).likely.place === 'lost') lostLeads = t;
  // The scripted fates' minutes.
  const { arrives, stalls, lost } = FATES;
  const inBin = Math.round(lost.segments.find((s) => s.spot === 'bin')!.from);
  const labMin = stalls.plated! - stalls.received!;
  // After the receipt scan the working model puts the whole setup ahead at every minute. Counting the
  // setup already done instead conditions on it: P(setup > W − receipt) / P(setup > t − receipt).
  const beforePlating = stalls.plated! - 1;
  const setupOver = (x: number) => lnSurv(x, p.setupMedian, p.setupSigma);
  const conditional = setupOver(p.windowMin - stalls.received!) / setupOver(beforePlating - stalls.received!);
  const leg = (i: number) => p.legs[i].name.toLowerCase();
  const exits = p.legs.map((l) => `${l.name}, median ${l.median} min, σ ${l.sigma.toFixed(2)}: ${l.exit.map(([kind, to, pr]) => `${share(pr)} → ${kind === 'recv' ? 'received' : kind === 'lost' ? 'lost' : leg(to)}`).join(', ')}.`).join(' ');
  return {
    id: 'tracer-silence',
    brand: {
      kicker: 'Tracer',
      title: 'The Silent Specimen',
      lede: 'A specimen left the ward and no scan has come back. Run the clock, hide the truth, and watch Tracer judge how late is too late and where it is now.',
    },
    setSentence: 'A pneumatic tube bench in a laboratory at dusk: a ward station, a glass tube run, a diverter over a receiver, a lost bin, a receiving scanner and a console, with Tracer\'s belief about a missing specimen glowing in the glass.',
    loading: 'Pressurizing the tube…',
    stats: [
      { id: 'risk', label: 'Miss risk', hot: true },
      { id: 'likely', label: 'Most likely' },
      { id: 'warning', label: 'Warning left' },
    ],
    presets: PRESETS.map(({ id, label }) => ({ id, label })),
    controls: [
      [
        {
          kind: 'time', id: 'silence', label: 'Silence', min: 0, max: 130, step: 1, value: 55, unit: 'min',
          ticks: [0, 30, 60, 90, 120], markers: [55], speeds: [1, 4], speed: 1, keyHint: 'Space , .',
          speedLabel: (s) => `${s * MIN_PER_SECOND} min/s`,
          format: (v) => `${Math.round(v)} min`,
          valueText: (v) => `${Math.round(v)} minutes since the collection scan`,
        },
      ],
      [
        {
          kind: 'slider', id: 'alertLine', label: 'Alert line', min: 0.1, max: 0.9, step: 0.01, coarse: 0.05, value: p.operatingPoint,
          ticks: [0.25, 0.45, 0.75], keyHint: '[ ]', format: (v) => `${Math.round(v * 100)}%`,
          valueText: (v) => `${Math.round(v * 100)} percent risk`,
        },
        {
          kind: 'slider', id: 'windowMin', label: 'Window', min: 60, max: 240, step: 1, coarse: 15, value: p.windowMin,
          ticks: [60, 120, 180, 240], keyHint: '- =', format: (v) => `${Math.round(v)} min`,
          valueText: (v) => `${Math.round(v)} minutes`,
        },
        {
          kind: 'segmented', id: 'truth', label: 'Truth', keyHint: 'T', value: 'shown',
          options: [{ value: 'shown', label: 'Shown' }, { value: 'hidden', label: 'Hidden' }],
        },
      ],
    ],
    views: ['lab', 'exploded', 'studio'],
    tools: [
      { id: 'lesson', label: 'Try it step by step (L)', icon: 'lesson', toggle: true },
      { id: 'cinematic', label: 'Play the cinematic tour (C)', icon: 'camera', toggle: true },
      { id: 'labels', label: 'Show or hide the labels (Shift+N)', icon: 'labels', toggle: true },
      { id: 'help', label: 'How it works', icon: 'help' },
    ],
    keys: [
      { keys: ['1', '2', '3', '4'], does: 'Try a fate: on time, stalls, lost, never sent' },
      { keys: ['L'], does: 'Try it step by step: each step waits until you have done it' },
      { keys: ['Space'], does: `Run or pause the clock (${MIN_PER_SECOND} min a second; ${MIN_PER_SECOND * 4} at the faster speed)` },
      { keys: [',', '.'], does: 'Step the silence 1 min (Shift: 5 min)' },
      { keys: ['Backspace'], does: 'Back to the collection scan, minute 0' },
      { keys: ['[', ']'], does: 'Lower or raise the alert line 1 point (Shift: 5)' },
      { keys: ['0'], does: `Alert line back to ${Math.round(p.operatingPoint * 100)}%` },
      { keys: ['-', '='], does: 'Shorten or lengthen the window 5 min (Shift: 15 min)' },
      { keys: ['T'], does: 'Show or hide the truth, the real carrier' },
      { keys: ['Shift+V', 'Shift+X'], does: 'Next view, exploded view' },
      { keys: ['C'], does: 'Cinematic tour, the one the exhibit opens with' },
      { keys: ['R'], does: 'Reset the camera' },
      { keys: ['Shift+N'], does: 'Labels on or off' },
      { keys: ['/'], does: 'Hide the interface' },
      { keys: ['H'], does: 'Collapse or expand the controls' },
      { keys: ['W', 'A', 'S', 'D', 'Q', 'E'], does: 'Fly the camera; drag to orbit, right-drag to pan, scroll to zoom' },
      { keys: ["Cmd+'"], does: 'Render tuning panel' },
    ],
    help: {
      h2: 'When silence becomes the alarm',
      intro: `A pneumatic tube bench, drawn schematically. A specimen was collected and scanned at the ward on the left. It should ride the glass tube to the station's receiver in the middle and be carried to the lab's scanner on the right. Between the collection scan and the receipt scan nothing reports where it is, so a specimen that never arrives produces no event at all. **Tracer** scores it on the clock instead, every ${p.tickMin} minutes. The light is Tracer's belief about where the specimen is; the cream carrier is where it really is, which the lab cannot see. Every number on screen comes from the project's analytic model, on assumed parameters.`,
      sections: [
        {
          h3: 'Why silence raises the risk',
          body: [
            `Most specimens make the trip in about **${normal.m} min** (assumed: ${Math.round(normal.w * 100)}% follow a lognormal with that median). A few **stall** and arrive late (${Math.round(late.w * 100)}%, median ${late.m} min), and a few **never arrive** (${Math.round(p.lossFraction * 100)}%). Tracer's risk is the chance the specimen misses its window, given that no receipt scan has come yet. As the silence outlasts a normal trip, the specimens still silent are more and more the late ones and the ones that never arrive, so the risk climbs with no event at all. The loupe counts them: at ${A} min, ${normalCount} of every 100 still-silent specimens are on a normal trip. This is a **mixture survival** model, and the share that never arrives, the **defective mass**, is what turns silence into a signal.`,
          ],
        },
        {
          h3: 'Where Tracer thinks it is',
          body: [
            `A second model follows the specimen through four legs: waiting at the ward (median ${p.legs[0].median} min), in the tube (${p.legs[1].median} min), at the station (${p.legs[2].median} min) and, if nobody unloads it, stalled at the station (${p.legs[3]?.median ?? '—'} min), each with its own chance of being lost. A **forward filter** moves the probability along these legs minute by minute, conditioned on hearing nothing. The light shows it: each glass vessel fills to its probability, the tube glows with what is still in transit, and the lost bin fills in coral, fed by a trickle down the branch. When the receipt scan comes, the place is known and the light pours into the lab's tray. The two models are kept apart on purpose, so each can be checked on its own.`,
          ],
        },
        {
          h3: 'Run the clock, move the line, shrink the window',
          body: [
            `Run the clock with Space or the card's play button (${MIN_PER_SECOND} min a second, or ${MIN_PER_SECOND * 4} at the faster speed), drag the stopwatch hand on the console, drag along the risk chart, use the card's scrubber, or press \`,\` \`.\` (Shift for 5 min). Backspace goes back to the collection scan.`,
            `The **alert line** is the operating point: Tracer alerts at the first ${p.tickMin}-minute re-score where the risk reaches it. Drag the amber dashed line on the chart, turn the ALERT LINE knob, use the card's slider, or press \`[\` \`]\` (Shift for 5 points) and \`0\` to reset it. A lower line warns sooner, and it also fires for more specimens that would have made it.`,
            `The **stability window** is how long the specimen keeps before it must be plated (assumed ${p.windowMin} min; the project's policy table will replace it with a cited value). Drag the coral mark on the stopwatch's bezel, turn the WINDOW knob, use the card's slider, or press \`-\` \`=\` (Shift for 15 min). A shorter window brings the alert sooner.`,
          ],
        },
        {
          h3: 'Hide the truth',
          body: [
            `Press T, flip the TRUTH switch on the bench, or use the card. Hidden, the bench shows what the lab sees: one scan at the ward, and nothing more until a receipt scan. Shown, compare the carrier with the light. The four fates are scripted: on time (received at ${arrives.received} min), stalls (stalled at the station, received at ${stalls.received} min), lost (misrouted into the lost bin at ${inBin} min) and never sent (still in the ward's sleeve). The model never sees them. At ${A} min the stalled and the never-sent specimens read exactly alike, because their silence is the same.`,
          ],
        },
      ],
      misconception: {
        h3: 'Does an alert mean it is lost?',
        body: [
          `No. ${alerts !== null ? `When a specimen still silent alerts at ${A} min` : `At ${A} min of silence`}, Tracer still puts ${placePct(ex.places.station)} on the station and ${placePct(ex.places.lost)} on lost. The alert says the chance of making the window has fallen below the line, so someone should look now; the light says where to look first. ${lostLeads !== null ? `Lost becomes the best explanation only at ${lostLeads} min.` : `Within ${MAX_T} min lost never becomes the best explanation.`}`,
        ],
      },
      exact: {
        h3: 'What is exact and what is not',
        body: [
          `**Exact:** the mixture-survival risk and the forward-filter location posterior as implemented in the project's working model (\`visuals/architecture.html\`), ported line for line and checked against it at every minute from 0 to ${MAX_T}: they agree to the last bit. The alert rule, the warning time and the loupe's shares come from the same functions.`,
          '**Assumed, every parameter:** none is measured, none is fitted, and none comes from any institution. They are the working model\'s own (`visuals/architecture.html`, which implements `docs/architecture.md` §5.2 and §5.3), set to show the mechanism until intervals fitted to the simulator and the policy table\'s cited windows replace them. A lognormal is given by its median and σ, the spread of its logarithm.',
          `Transit, collection to receipt: ${p.mix.map((c, i) => `${share(c.w)} ${i === 0 ? 'normal' : 'late'} (median ${c.m} min, σ ${c.s.toFixed(2)})`).join(', ')}, ${share(p.lossFraction)} never arrive. Setup, receipt to plating: median ${p.setupMedian} min, σ ${p.setupSigma.toFixed(2)}. Window: ${p.windowMin} min from the collection scan. Re-scored every ${p.tickMin} min; alert line ${op}%.`,
          `Location legs: ${exits}`,
          `**Not real:** the four fates are scripted theatre: receipt at ${arrives.received} and ${stalls.received} min, plating ${labMin} min after receipt, the misrouted carrier and the one never sent. The tube layout is schematic and is not any hospital's system; real tubes are opaque, and the glass is here so you can see the truth. The clock runs at ${MIN_PER_SECOND} min a second (${MIN_PER_SECOND * 60} times real time) or ${MIN_PER_SECOND * 4} (${MIN_PER_SECOND * 240} times); presets, the lesson and the tour move it within a few seconds.`,
          `**After the receipt scan** the working model, as written, puts the whole setup still ahead at every minute, so the risk climbs while the specimen waits in the lab: in the Stalls run, from ${riskPct(model.riskAt(stalls, stalls.received!, p.windowMin))} at ${stalls.received} min to ${riskPct(model.riskAt(stalls, beforePlating, p.windowMin))} at ${beforePlating} min, then 0 at plating. Counting the ${beforePlating - stalls.received!} min of setup already done would give ${riskPct(conditional)} at ${beforePlating} min. The exhibit shows the model as written.`,
          `**Approximated, with sizes:** erf is Abramowitz and Stegun 7.1.26 (error under 1.5 × 10⁻⁷). The risk integral is a midpoint sum on a one-minute grid; it dips by at most 5 × 10⁻⁶ between minutes 1 and 4 instead of rising. The location filter runs on a one-minute grid with ages capped at 180 min. Readouts are taken at whole minutes, and the light is interpolated between them. Within the tube the light places each minute's cohort at the median speed, since the model has no positions inside a leg. A vessel's fill is its probability; the coral trickle down the branch is the probability moving into lost per minute. The loupe is a drawing of the model's weights, rounded to whole carriers. It counts the trip model's kinds (normal, late, never arrive), and the vessels show the location model, so the two need not agree: at ${A} min the loupe has ${neverCount} of 100 never arriving while the lost bin holds ${placePct(ex.places.lost)}.`,
        ],
      },
      footnote: `Times are minutes since the collection scan. Parameters: ${p.source}. Address parameters: \`fate=arrives|stalls|lost|unsent\`, \`silence=0–130\`, \`alert=0.1–0.9\`, \`window=60–240\`, \`truth=0|1\`, \`embed=1\`, \`params=<same-origin JSON>\`. Tracer: Daniel Escalante and Karissa Kocourek, CAI 5724, University of Florida.`,
    },
    // The truth's cream carrier, and the loupe's cream for specimens on a normal trip.
    semanticColors: { truth: '#f1e2c0', normal: '#e8dcc4' },
    credit: { label: 'Tracer · Escalante and Kocourek · CAI 5724 · University of Florida', href: 'https://github.com/scalinity' },
    cardWidth: 468,
  };
}

/** One causal sentence per regime, live numbers in the color of what they describe. */
export function caption(r: Reading, truth: { shown: boolean; place: Place; words: string }, p: Params): string {
  const m = `${r.minute} min`;
  const W = `${r.windowMin} min`;
  let text: string;
  if (r.phase === 'plated') {
    text = r.plated! <= r.windowMin
      ? `Plated at {amber:${r.plated} min}, inside the {amber:${W}} window: the clock stopped, and the risk is {cyan:${riskPct(r.risk)}}.`
      : `Plated at {amber:${r.plated} min}, after the {amber:${W}} window closed: the specimen missed it, {cyan:${riskPct(r.risk)}}.`;
  } else if (r.phase === 'received') {
    text = r.missed
      ? `Received at {amber:${r.received} min}, but the {amber:${W}} window has closed before plating: missed, {cyan:${riskPct(r.risk)}}.`
      : `The receipt scan at {amber:${r.received} min} ended the silence: only the in-lab setup remains, and the risk fell from {cyan:${riskPct(r.scan!.before)}} to {cyan:${riskPct(r.risk)}}.`;
  } else if (r.missed) {
    text = `No scan for {amber:${m}}: the {amber:${W}} window has closed, so the specimen has missed it ({cyan:${riskPct(r.risk)}}).`;
  } else if (r.alert.fired) {
    const a = r.alert.minute!;
    const place = r.likely.place === 'lost' ? `{coral:lost} is now the best explanation (${placeShare('lost', r.likely.p)})` : `it looks first at ${where[r.likely.place]} (${placeShare(r.likely.place, r.likely.p)})`;
    text = `No scan for {amber:${m}}. The risk reached the {amber:${Math.round(r.alertLine * 100)}%} line at {amber:${a} min}, so Tracer alerted with {amber:${r.windowMin - a} min} to spare; ${place}.`;
  } else if (r.components && r.components.parts[0] > 0.7) {
    text = `{amber:${m}} since the collection scan. A normal trip takes about {amber:${p.mix[0].m} min}, so silence this short is expected: the risk of missing the {amber:${W}} window is {cyan:${riskPct(r.risk)}}.`;
  } else {
    const normal = Math.round((r.components?.parts[0] ?? 0) * 100);
    text = `No scan for {amber:${m}}. Only {normal:${normal}%} of specimens this quiet are still on a normal trip, so the risk of missing the window is {cyan:${riskPct(r.risk)}}, under the {amber:${Math.round(r.alertLine * 100)}%} line.`;
  }
  if (truth.shown && truth.place !== r.likely.place) text += ` The truth: it is {truth:${truth.words}}.`;
  return text;
}
