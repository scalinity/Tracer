// The Silent Specimen: wiring. One state object; every input path (the stopwatch, the chart, the
// knobs, the truth switch, the card, keys, presets, the lesson, the tour and the address) changes it
// through the model's own setters; the scene, HUD, labels and screens read the eased display state
// every frame, and every number on screen comes from TracerModel.read().

import '@fontsource-variable/outfit';
import '@fontsource/dm-mono/400.css';
import '@fontsource/dm-mono/500.css';
import './exhibit.css';
import * as THREE from 'three/webgpu';
import {
  bindKeys, buildExplodedStage, buildLabSet, buildStudioSet, CameraRig, Cinematic, cloneLooks, createStage, Eased,
  HOUSE_LOOKS, hudText, installDebugHandle, Lesson, Loupe, mountHud, mountTune, opensWithTour, PartDragger, plainLoad,
  prefersReducedMotion, readUrlParams, WorldLabels, type HeroShot, type PoseSpec, type Shot, type Vec3,
} from './kit';
import { buildSchema, caption, MIN_PER_SECOND, PLACE_WORDS, placePct, placeShare, riskPct } from './hud/schema';
import { describeSpot, FATES, placeOfSpot, type FateId } from './model/fates';
import { applyOverride } from './model/params';
import {
  defaultState, LIMITS, matchPreset, nudge, PRESETS, quantized, TracerModel, withPreset, withTruth, withValue,
  type ExhibitState, type Key, type Reading,
} from './model/state';
import { ASSUMED, MAX_T, type Params } from './model/tracer';
import { readSpecimenQuery, sameOriginUrl, setsUpState, startState } from './model/url';
import { BAND_Y, buildApparatus } from './scene/apparatus';
import { buildCarrier } from './scene/carrier';
import { buildDecks } from './scene/decks';
import { buildEcho } from './scene/echo';
import { Glow } from './scene/glow';
import { MIN_PER_DEG } from './scene/instruments';
import { BEACON, LOST_BIN, RECEIVER, SLEEVE, TUBE_Z } from './scene/layout';
import { silentDrawing } from './scene/loupe-drawing';
import { CHART_T, drawBars, drawChart, drawLog, PLOT } from './scene/screen-draw';

const params = readUrlParams();
const query = readSpecimenQuery(location.search);
const root = document.getElementById('app') as HTMLElement;
const world = document.getElementById('world') as HTMLElement;
if (query.embed) root.classList.add('embed');

/** Fitted parameters can replace the assumed ones: window.TRACER_PARAMS, or ?params=<same-origin JSON>. */
async function loadParams(): Promise<Params> {
  let p = ASSUMED;
  const given = (window as { TRACER_PARAMS?: unknown }).TRACER_PARAMS;
  if (given !== undefined) {
    const r = applyOverride(p, given);
    if (r.params) p = r.params;
    else console.warn(`TRACER_PARAMS ignored: ${r.error}`);
  }
  if (query.params) {
    const url = sameOriginUrl(query.params, location.href);
    if (!url) console.warn('?params ignored: it must be a JSON file on this page\'s origin');
    else {
      try {
        const res = await fetch(url);
        const r = applyOverride(p, await res.json());
        if (r.params) p = r.params;
        else console.warn(`?params ignored: ${r.error}`);
      } catch {
        console.warn('?params ignored: it could not be read as JSON');
      }
    }
  }
  return p;
}

const P = await loadParams();
const model = new TracerModel(P);
const schema = buildSchema(P, model);
const instant = params.instant || prefersReducedMotion();
const linked = setsUpState(query);

const hud = mountHud(root, schema);
const looks = cloneLooks(HOUSE_LOOKS);
looks.lab.sets = ['room', 'console', 'bench', 'glass'];
looks.lab.fog = { color: '#252e43', density: 0.0105 };
looks.exploded.sets = ['plinth', 'bench', 'decks', 'glass'];
looks.exploded.reflection = 0.22;
looks.exploded.key.intensity = 1.25;
looks.exploded.rim.relative = 0.35;
looks.exploded.environmentIntensity = 0.62;
looks.exploded.hemisphere.intensity = 0.4;
looks.studio.sets = ['cyclorama', 'paper-glass'];
looks.studio.beamGain = 0.8;
const stage = await createStage({
  container: world, looks, initialLook: 'lab', lightTarget: { x: 0, y: 1.5, z: 0 }, forceWebGL: params.backend === 'webgl2',
});
const { scene, camera } = stage;

// ---------- scene ----------
const app = buildApparatus();
scene.add(app.group);
stage.registerSet('console', app.console);
stage.registerSet('bench', app.bench);
for (const m of app.glass) stage.registerSet('glass', m);
for (const m of app.paper) stage.registerSet('paper-glass', m);
const carrier = buildCarrier();
scene.add(carrier.group, carrier.anchor);
const glow = new Glow(model);
scene.add(glow.group);
const decks = buildDecks();
scene.add(decks.group);
stage.registerSet('decks', decks.group);

const FLOOR_Y = -9.05;
// A dimmer dusk sky than the kit's default, so the window behind the bench never rivals the station's light.
const lab = buildLabSet({ floorY: FLOOR_Y, seed: params.seed ?? 5, windowGain: 0.4 });
buildEcho(lab.window, lab.anchors.window, FLOOR_Y + 44);
scene.add(lab.group);
stage.registerSet('room', lab.group);
const exploded = buildExplodedStage({ makerPlate: ['DANIEL ESCALANTE', 'TRACER · CAI 5724 · UF'], width: 22, depth: 9.5 });
exploded.group.position.y = -0.27 - exploded.top;
scene.add(exploded.group);
stage.registerSet('plinth', exploded.group);
const studio = buildStudioSet({ floorY: -0.005 });
scene.add(studio);
stage.registerSet('cyclorama', studio);

// ---------- state ----------
let state: ExhibitState = linked ? startState(query, P) : defaultState(P);
const silence = new Eased(state.silence, 14);
const alertLine = new Eased(state.alertLine);
const windowMin = new Eased(state.windowMin);
const reveal = new Eased(state.truth ? 1 : 0, 6);
/** A carrier whose fate changes dissolves back in at its new place. */
const arrival = new Eased(1, 6);
const explode = new Eased(0);
let presetId: FateId | null = null;
let playing = false;
let speed = 1;
let beaconOn = 0;

function setPlaying(on: boolean): void {
  // Play at the end of the run starts the clock again from the collection scan.
  if (on && state.silence >= MAX_T) setState({ ...state, silence: 0 }, { instant: true });
  playing = on;
  hud.setControl('silence.play', playing);
}

function setState(next: ExhibitState, o: { tween?: boolean; seconds?: number; instant?: boolean } = {}): void {
  const jump = o.instant || instant;
  // An instant change lands every displayed value on its target, even one mid-tween toward the same
  // value: the tour's setups rely on it to close the loop without a seam.
  const move = (e: Eased, v: number, changed: boolean) => {
    if (jump) e.jump(v);
    else if (changed && o.tween) e.to(v, o.seconds ?? 0.9);
    else if (changed) e.follow(v);
  };
  move(silence, next.silence, next.silence !== state.silence);
  move(alertLine, next.alertLine, next.alertLine !== state.alertLine);
  move(windowMin, next.windowMin, next.windowMin !== state.windowMin);
  if (jump) reveal.jump(next.truth ? 1 : 0);
  else if (next.truth !== state.truth) reveal.to(next.truth ? 1 : 0, 0.9);
  if (next.fate !== state.fate) {
    if (jump) arrival.jump(1);
    else {
      arrival.jump(0);
      arrival.to(1, 0.9);
    }
  }
  state = next;
  presetId = matchPreset(state, P);
  hud.setPreset(presetId);
  hud.setControl('truth', state.truth ? 'shown' : 'hidden');
  stage.invalidateShadows();
}

/** Manual moves of the clock pause playback: the visitor has taken the clock. */
function setValue(key: Key, v: number, o: { tween?: boolean; seconds?: number } = {}): void {
  if (key === 'silence') setPlaying(false);
  setState(withValue(state, key, v), o);
}

function applyPreset(i: number, o: { instant?: boolean } = {}): void {
  const p = PRESETS[i];
  if (!p) return;
  setPlaying(false);
  setState(withPreset(state, p.id, P), { tween: true, seconds: 1.1, instant: o.instant });
}

function setTruth(on: boolean): void {
  setState(withTruth(state, on));
}

// ---------- camera ----------
const views: Record<string, HeroShot> = {
  lab: { target: [0.9, 1.15, 0.3], yaw: -0.34, pitch: 0.18, fit: { width: 18.4, height: 10.9 }, bounds: lab.bounds, limits: { zoom: [0.38, 1.3], pan: 7 } },
  exploded: { target: [0.3, 4.6, 0], yaw: -0.24, pitch: 0.34, fit: { width: 21.5, height: 15.5 } },
  studio: { target: [0.9, 2.2, -0.2], yaw: 0.44, pitch: 0.2, fit: { width: 18.4, height: 6.6 } },
};
/** Docked layouts (narrow frames and phones) leave a tall, narrow space: frame the bench tighter there. */
const FITS = { lab: views.lab.fit, exploded: views.exploded.fit, studio: views.studio.fit };
const NARROW_FITS = { lab: { width: 19, height: 9.5 }, exploded: { width: 20.5, height: 15 }, studio: { width: 19, height: 6.4 } };
const fitToFrame = () => {
  const narrow = world.clientWidth <= 900;
  for (const k of ['lab', 'exploded', 'studio'] as const) views[k].fit = narrow ? NARROW_FITS[k] : FITS[k];
};
fitToFrame();
const CLOSEUP: PoseSpec = { target: [2.6, 1.5, 0], yaw: -0.5, pitch: 0.14, distance: 12 };
let dragger: PartDragger | null = null;
const rig = new CameraRig(camera, world, views, {
  safeArea: () => hud.safeArea(),
  interactionFilter: (e) => dragger?.filter(e) ?? true,
});
rig.idleDrift = !params.instant;
/** Depth of field rests on the station's receiver, where the light gathers. */
const FOCUS_HOME = new THREE.Vector3(RECEIVER.x, 1.4, RECEIVER.z);
rig.focusPoint = FOCUS_HOME.clone();
rig.focusSpan = [new THREE.Vector3(SLEEVE.x, 1.5, SLEEVE.z), new THREE.Vector3(BEACON.x, 2.5, BEACON.z)];
const FOCUS_BAND = stage.uniforms.focusBand.value;
let lookBand = FOCUS_BAND;

async function setView(name: string, o: { instant?: boolean } = {}): Promise<void> {
  if (!views[name]) return;
  hud.setView(name);
  const inst = o.instant || instant;
  await stage.setLook(name, {
    instant: inst,
    onSwap: () => {
      void rig.setView(name, { instant: true });
      explode.jump(0);
      if (name === 'exploded') {
        if (inst) explode.jump(1);
        else explode.to(1, 1.4);
      }
      loupe.setVisible(name === 'lab');
      stage.invalidateShadows();
    },
  });
}
stage.onLook((look) => {
  hud.setLightTheme(look.hudTheme === 'light');
  glow.lightScale = look.name === 'studio' ? 0.25 : 1;
  lookBand = look.name === 'studio' ? FOCUS_BAND * 2.4 : look.name === 'exploded' ? FOCUS_BAND * 1.8 : FOCUS_BAND;
});

// ---------- in-world information ----------
const labels = new WorldLabels(scene, camera, root);
function setLabels(visible: boolean): void {
  labels.setVisible(visible);
  hud.setTool('labels', visible);
}
setLabels(labels.visible);
let shown: Reading = model.read(state);
const placeValue = (k: 'ward' | 'tube' | 'station' | 'lost') => () => placePct(shown.places[k]);
// Tracer's belief labels sit above or beside the vessels; the truth hangs below the parts on a leader.
const at = { x: 0, y: 0, z: 0 };
labels.add(glow.anchors.ward, { name: 'Ward', value: placeValue('ward'), color: 'cyan', priority: 2, offset: at });
labels.add(glow.anchors.tube, { name: 'Tube', value: placeValue('tube'), color: 'cyan', priority: 1, offset: at });
labels.add(glow.anchors.station, { name: 'Station', value: placeValue('station'), color: 'cyan', priority: 6, offset: at });
labels.add(glow.anchors.lost, { name: 'Lost', value: placeValue('lost'), color: 'coral', priority: 5, offset: at });
labels.add(glow.anchors.lab, {
  name: 'Lab', color: 'cyan', priority: 3, offset: at,
  value: () => (shown.received !== null ? `scan at ${shown.received} min` : 'no scan yet'),
});
labels.add(app.anchors.beacon, {
  name: 'Alert', color: 'amber', priority: 6,
  value: () => (shown.alert.clearedAt !== null ? `cleared at ${shown.alert.clearedAt} min` : shown.alert.fired ? `at ${shown.alert.minute} min` : 'quiet'),
});
const truthLabel = labels.add(carrier.anchor, {
  name: 'Truth', color: 'truth', priority: 7, leader: true, offset: { x: -0.35, y: -1.05, z: 1.35 },
  value: () => describeSpot(model.truth(state.fate, silence.value).spot, silence.value),
});
const deckLabels = [
  labels.add(decks.anchors.truth, { name: 'What happened', color: 'truth', priority: 2, occlude: false, value: () => 'the truth' }),
  labels.add(decks.anchors.lab, { name: 'What the lab sees', color: 'amber', priority: 2, occlude: false, value: () => `${1 + (shown.received !== null ? 1 : 0)} scan${shown.received !== null ? 's' : ''}` }),
  labels.add(decks.anchors.tracer, { name: 'What Tracer infers', color: 'cyan', priority: 2, occlude: false, value: () => `${riskPct(shown.risk)} risk` }),
];
// Amber hint pills on the parts, each shown only while a lesson step waits on that part.
const hintPill = { color: 'amber', priority: -1, offset: { x: 0, y: 0, z: 0 }, occlude: false };
const watchHint = labels.add(app.stopwatch.hint, { name: 'Drag the hand to run the clock', ...hintPill });
const switchHint = labels.add(app.truthSwitch.hint, { name: 'Flip to hide the truth', ...hintPill });
const knobHint = labels.add(app.alertKnob.hint, { name: 'Turn to move the line', ...hintPill });
const bezelAnchor = new THREE.Object3D();
bezelAnchor.position.set(0, 1.7, 0.4);
app.stopwatch.group.add(bezelAnchor);
const bezelHint = labels.add(bezelAnchor, { name: 'Drag the coral mark to shrink the window', ...hintPill });
for (const h of [watchHint, switchHint, knobHint, bezelHint]) h.setVisible(false);

// The loupe magnifies the silence itself: who is behind it, specimen by specimen. Its leader points at
// the tube, where the silence happens.
const loupeAnchor = new THREE.Object3D();
loupeAnchor.position.set(-3.6, 4.1 + 0.36, TUBE_Z);
scene.add(loupeAnchor);
const loupe = new Loupe({
  radius: 1.45, title: '100 silent specimens', focus: stage.uniforms.focusDistance, fps: 12, pixels: 768, anchor: loupeAnchor,
  draw: silentDrawing(() => shown),
});
loupe.group.position.set(-3.4, 7.0, -1.3);
loupe.addTo(scene);
labels.keepClearOf(loupe);

// ---------- input paths ----------
dragger = new PartDragger(camera, world, hud);
const zAxis = new THREE.Vector3(0, 0, 1);
const deg = THREE.MathUtils.radToDeg;
dragger.add({
  object: app.stopwatch.face, tip: 'Drag to run the clock',
  mode: { kind: 'rotate', axis: zAxis, center: () => app.stopwatch.center(), onTurn: (r) => setValue('silence', state.silence - deg(r) * MIN_PER_DEG) },
});
dragger.add({
  object: app.stopwatch.bezel, tip: 'Drag the coral mark to set the window',
  mode: { kind: 'rotate', axis: zAxis, center: () => app.stopwatch.center(), onTurn: (r) => setValue('windowMin', state.windowMin - deg(r) * MIN_PER_DEG) },
});
dragger.add({
  object: app.alertKnob.grip, tip: 'Turn to move the alert line',
  mode: { kind: 'rotate', axis: zAxis, center: () => app.alertKnob.center(), onTurn: (r) => setValue('alertLine', state.alertLine - (deg(r) / 270) * (LIMITS.alertLine[1] - LIMITS.alertLine[0])) },
});
dragger.add({
  object: app.windowKnob.grip, tip: 'Turn to change the window',
  mode: { kind: 'rotate', axis: zAxis, center: () => app.windowKnob.center(), onTurn: (r) => setValue('windowMin', state.windowMin - (deg(r) / 270) * (LIMITS.windowMin[1] - LIMITS.windowMin[0])) },
});
dragger.add({
  object: app.truthSwitch.lever, tip: () => (state.truth ? 'Click to hide the truth' : 'Click to show the truth'),
  mode: { kind: 'click', onClick: () => setTruth(!state.truth) },
});
/** Screen pixels across the chart's plot (time) and up it (risk), measured now. */
const chartPixels = () => {
  const m = app.chart.mesh;
  const w = 6.2;
  const h = 2.5;
  const at = (fx: number, fy: number) => m.localToWorld(new THREE.Vector3((fx - 0.5) * w, (0.5 - fy) * h, 0)).project(camera);
  const rect = world.getBoundingClientRect();
  const a = at(PLOT.x0, PLOT.y1);
  const b = at(PLOT.x1, PLOT.y1);
  const c = at(PLOT.x0, PLOT.y0);
  return {
    across: Math.hypot(((b.x - a.x) / 2) * rect.width, ((b.y - a.y) / 2) * rect.height),
    up: Math.hypot(((c.x - a.x) / 2) * rect.width, ((c.y - a.y) / 2) * rect.height),
  };
};
dragger.add({
  object: app.lineHandle, tip: 'Drag the alert line up or down',
  mode: { kind: 'vertical', onDrag: (px) => setValue('alertLine', state.alertLine + px / Math.max(20, chartPixels().up)) },
});
dragger.add({
  object: app.chart.mesh, tip: 'Drag along the chart to move through the silence',
  mode: { kind: 'horizontal', onDrag: (px) => setValue('silence', state.silence + (px * CHART_T) / Math.max(40, chartPixels().across)) },
});

// ---------- lesson ----------
const readAt = (s: Partial<ExhibitState>) => model.read({ ...state, ...s });
const arrange = (s: Partial<ExhibitState>, seconds = 1) => {
  setPlaying(false);
  setState({ ...state, ...s }, { tween: true, seconds });
};
const runClock = (to: number, seconds: number) => () => setValue('silence', to, { tween: true, seconds });
/** Where the lesson starts, and the tour's own run of it: a specimen on its way, the defaults, the truth shown. */
const LESSON_START: Partial<ExhibitState> = { fate: 'arrives', silence: 0, alertLine: P.operatingPoint, windowMin: P.windowMin, truth: true };
const lineWords = `{amber:${Math.round(P.operatingPoint * 100)}%}`;
const lesson = new Lesson([
  {
    ask: 'Run the clock and watch a specimen reach the scanner: press play, or drag the stopwatch hand.',
    hint: watchHint,
    enter: () => arrange(LESSON_START),
    done: () => state.fate === 'arrives' && silence.value >= 26 && state.silence >= 26,
    hold: 0.4,
    said: () => {
      const scan = readAt({ silence: 26 }).scan!;
      return `The scan at {amber:${scan.at} min} stopped the clock: the risk fell from {cyan:${riskPct(scan.before)}} to {cyan:${riskPct(scan.after)}}.`;
    },
    perform: () => {
      setState({ ...state, fate: 'arrives', silence: 0, truth: true }, { instant: true });
      runClock(30, 7)();
    },
  },
  {
    ask: 'A second specimen is on its way. Hide the truth: press T or flip the truth switch.',
    hint: switchHint,
    enter: () => arrange({ fate: 'lost', silence: 0, truth: true }),
    done: () => !state.truth && reveal.value < 0.05,
    hold: 0.3,
    said: () => 'Now you see what the lab sees: one scan at the ward, and then nothing. The light is all Tracer knows.',
    perform: () => setTruth(false),
  },
  {
    ask: 'Run the clock until Tracer raises the alarm.',
    hint: watchHint,
    done: () => shown.alert.fired && silence.settled,
    hold: 0.5,
    said: () => {
      const a = shown.alert.minute!;
      const r = readAt({ silence: a });
      return `After {amber:${a} min} of silence the risk reached {cyan:${riskPct(r.risk)}}, past the {amber:${Math.round(r.alertLine * 100)}%} line: Tracer alerts with {amber:${r.windowMin - a} min} left and looks first at ${r.likely.place === 'lost' ? 'lost' : `the ${PLACE_WORDS[r.likely.place]}`} (${placeShare(r.likely.place, r.likely.p)}).`;
    },
    perform: runClock(63, 7),
  },
  {
    ask: 'Lower the alert line to warn sooner: drag it on the chart, or turn the ALERT LINE knob.',
    hint: knobHint,
    enter: () => setPlaying(false),
    done: () => shown.alert.projected !== null && shown.alert.projected <= 50 && alertLine.settled,
    hold: 0.6,
    said: () => {
      const base = readAt({ alertLine: P.operatingPoint });
      return `At a {amber:${Math.round(shown.alertLine * 100)}%} line Tracer alerts at {amber:${shown.alert.projected} min}: {amber:${shown.lead} min} of warning instead of {amber:${base.lead} min}. A lower line warns sooner, and also for more specimens that would have made it. The line goes back to ${lineWords}.`;
    },
    perform: () => setValue('alertLine', 0.3, { tween: true, seconds: 3 }),
  },
  {
    ask: 'Now give the specimen a shorter life: drag the coral mark on the stopwatch to shrink the window to 90 min or less.',
    hint: bezelHint,
    enter: () => setValue('alertLine', P.operatingPoint, { tween: true }),
    done: () => quantized(state).windowMin <= 90 && windowMin.settled && alertLine.settled,
    hold: 0.6,
    said: () => {
      const base = readAt({ windowMin: P.windowMin });
      return `With a {amber:${shown.windowMin} min} window the alert comes at {amber:${shown.alert.projected} min} and leaves {amber:${shown.lead} min} of warning, not {amber:${base.lead} min}. **Silence becomes the alarm** once the chance of arriving in time falls below the line: sooner, when the window is short.`;
    },
    perform: () => setValue('windowMin', 60, { tween: true, seconds: 4 }),
  },
], {
  onChange: (on) => {
    hud.setTool('lesson', on);
    if (on) cinematic.stop();
  },
});

// ---------- cinematic (the opening, Lab only) ----------
const STATION: Vec3 = [RECEIVER.x, 1.4, RECEIVER.z];
const TUBE_MID: Vec3 = [-3, 3.6, TUBE_Z];
const SCREENS: Vec3 = [0.3, BAND_Y, 4.1];
const WATCH: Vec3 = [-7.05, BAND_Y, 4.2];
const LOST_AT: Vec3 = [LOST_BIN.x, 1.3, LOST_BIN.z];
const step = (i: number) => lesson.perform(i);
const startOfTour = () => setState(withTruth(withPreset(state, 'stalls', P), true), { instant: true });
const shots: Shot[] = [
  // Wide and slow, the bench at dusk: a 17° arc, nothing changing.
  { view: 'lab', pose: { yaw: -0.58, pitch: 0.2, distanceScale: 1.05 }, dolly: { yaw: 0.3 }, hold: 9, focus: STATION, setup: startOfTour },
  // Closer and lower, along the bench from the ward end, the tube at eye level.
  { view: 'lab', pose: { target: [-3.5, 2.4, 0.6], yaw: -0.92, pitch: 0.05, distance: 15 }, dolly: { pan: [4, 0, 0], yaw: 0.12 }, hold: 9, focus: TUBE_MID },
  // Round the ward and the tube: a specimen makes the trip and is scanned.
  {
    view: 'lab', pose: { target: [-2.2, 2.2, 0.6], yaw: -0.5, pitch: 0.12, distance: 15.5 }, dolly: { yaw: 0.26 }, hold: 10, focus: TUBE_MID,
    setup: () => setState({ ...state, ...LESSON_START }, { instant: true }),
    beats: [[0.6, step(0)]],
  },
  // Toward the receiver: the truth hides and the silence runs until the beacon lights.
  {
    view: 'lab', pose: { target: [3.2, 1.8, 0.2], yaw: -0.18, pitch: 0.1, distance: 13.5 }, dolly: { distanceScale: 0.86, yaw: 0.08 }, hold: 10, focus: STATION,
    setup: () => setState({ ...state, fate: 'lost', silence: 0, truth: true }, { instant: true }),
    beats: [[0.8, step(1)], [1.8, step(2)]],
  },
  // Along the console's screens: the alert line drops and the alert comes sooner.
  { view: 'lab', pose: { target: SCREENS, yaw: 0.06, pitch: 0.12, distance: 13 }, dolly: { pan: [2.8, 0, 0] }, hold: 10, focus: SCREENS, beats: [[0.7, step(3)]] },
  // Round the stopwatch: the window shrinks.
  { view: 'lab', pose: { target: WATCH, yaw: -0.36, pitch: 0.1, distance: 8 }, dolly: { yaw: 0.26 }, hold: 9, focus: WATCH, beats: [[0.6, step(4)]] },
  // The payoff, low and slow toward the lost bin: the coral fills, then the truth is shown.
  {
    view: 'lab', pose: { target: [3.6, 1.4, -0.4], yaw: -0.14, pitch: 0.07, distanceScale: 0.78 }, dolly: { yaw: -0.24 }, hold: 11, focus: LOST_AT,
    setup: () => setState({ ...state, fate: 'lost', silence: 60, alertLine: P.operatingPoint, windowMin: P.windowMin, truth: false }, { instant: true }),
    beats: [[0.5, runClock(120, 8)], [7.2, () => setTruth(true)]],
  },
];
let opening = false;
/**
 * The opening's first input hands over to the lesson. The tour's return home puts shot one's setup in
 * place under the black; the lesson's start lands right after it, still under the black, so the bench
 * comes up already at the first request. A preset or the lesson tool in the meantime cancels it.
 */
let handover = false;
const cinematic = new Cinematic(shots, {
  rig, setView: (n) => setView(n), fade: (to, ms, color) => stage.fade(to, ms, color),
  onChange: (on) => {
    hud.setTool('cinematic', on);
    if (on) {
      lesson.stop();
      setPlaying(false);
    } else if (opening) {
      opening = false;
      hud.setPresenting(false);
      handover = true;
    }
  },
  onHome: () => {
    if (!handover) return;
    handover = false;
    setState({ ...state, ...LESSON_START }, { instant: true });
    lesson.start();
  },
});
dragger.onInput = () => cinematic.stop();

// ---------- HUD and keys ----------
hud.on('control', (id, v) => {
  cinematic.stop();
  if (id === 'silence') setValue('silence', Number(v));
  if (id === 'silence.play') setPlaying(Boolean(v));
  if (id === 'silence.speed') speed = Number(v);
  if (id === 'alertLine') setValue('alertLine', Number(v));
  if (id === 'windowMin') setValue('windowMin', Number(v));
  if (id === 'truth') setTruth(v === 'shown');
  if (id === 'silence.speed') hud.setControl('silence.speed', String(speed));
});
/** A setup the visitor picks ("show me") ends the lesson. */
function pick(i: number): void {
  handover = false;
  lesson.stop();
  applyPreset(i);
}
function toggleLesson(): void {
  handover = false;
  lesson.toggle();
}
hud.on('preset', (id) => {
  cinematic.stop();
  pick(PRESETS.findIndex((p) => p.id === id));
});
hud.on('view', (name) => {
  cinematic.stop();
  void setView(String(name));
});
hud.on('tool', (id) => {
  if (id === 'help') hud.openHelp();
  if (id === 'cinematic') cinematic.toggle();
  if (id === 'labels') setLabels(!labels.visible);
  if (id === 'lesson') toggleLesson();
});

const step1 = (key: Key, dir: 1 | -1) => (e: KeyboardEvent) => {
  if (key === 'silence') setPlaying(false);
  setState(nudge(state, key, dir, e.shiftKey));
};
const viewOrder = schema.views;
bindKeys(schema.keys, {
  '1': () => pick(0),
  '2': () => pick(1),
  '3': () => pick(2),
  '4': () => pick(3),
  Space: () => setPlaying(!playing),
  ',': step1('silence', -1),
  '.': step1('silence', 1),
  Backspace: () => setValue('silence', 0, { tween: true, seconds: 0.8 }),
  '[': step1('alertLine', -1),
  ']': step1('alertLine', 1),
  '0': () => setValue('alertLine', P.operatingPoint, { tween: true }),
  '-': step1('windowMin', -1),
  '=': step1('windowMin', 1),
  T: () => setTruth(!state.truth),
  'Shift+V': () => void setView(viewOrder[(viewOrder.indexOf(rig.view as (typeof viewOrder)[number]) + 1) % viewOrder.length]),
  'Shift+X': () => void setView('exploded'),
  L: () => toggleLesson(),
  C: () => cinematic.toggle(),
  R: () => void rig.reset(),
  'Shift+N': () => setLabels(!labels.visible),
  '/': () => hud.setUiVisible(!hud.uiVisible),
  H: () => hud.setCompact(!hud.compact),
}, { repeat: [',', '.', '[', ']', '-', '='] });

mountTune(root, { stage, rig });

// ---------- frame ----------
const last = { risk: '', likely: '', warning: '', caption: '', marker: -1, likelyColor: '', warnColor: '' };
const history: number[] = [];
stage.onFrame((dt, elapsed) => {
  cinematic.update(dt);
  if (playing) {
    state = { ...state, silence: Math.min(MAX_T, state.silence + dt * MIN_PER_SECOND * speed) };
    silence.jump(state.silence);
    if (state.silence >= MAX_T) setPlaying(false);
    const p = matchPreset(state, P);
    if (p !== presetId) hud.setPreset((presetId = p));
  }
  // The carrier casts a shadow: keep the shadow map current while the clock moves it.
  if (playing || !silence.settled || !arrival.settled) stage.invalidateShadows(0.25);
  silence.update(dt);
  alertLine.update(dt);
  windowMin.update(dt);
  reveal.update(dt);
  arrival.update(dt);
  explode.update(dt);
  const ds: ExhibitState = { fate: state.fate, silence: silence.value, alertLine: alertLine.value, windowMin: windowMin.value, truth: state.truth };
  shown = model.read(ds);

  // Instruments on the console.
  app.stopwatch.update(ds.silence, ds.windowMin, shown.alert.projected);
  app.alertKnob.set((ds.alertLine - LIMITS.alertLine[0]) / (LIMITS.alertLine[1] - LIMITS.alertLine[0]));
  app.windowKnob.set((ds.windowMin - LIMITS.windowMin[0]) / (LIMITS.windowMin[1] - LIMITS.windowMin[0]));
  app.truthSwitch.set(reveal.value);
  app.placeLineHandle(ds.alertLine);
  // The beacon lights while an alert stands, breathing slowly; it goes dark when a scan clears it.
  const alarm = shown.alert.fired && shown.alert.clearedAt === null;
  beaconOn += ((alarm ? 1 : 0) - beaconOn) * (1 - Math.exp(-dt * 5));
  app.beaconLevel.value = beaconOn * (3.0 + (prefersReducedMotion() ? 0 : 0.45 * Math.sin(elapsed * 2.2)));
  // The scanner's line flashes as a scan happens.
  const f = FATES[state.fate];
  const near = f.received === null ? Infinity : Math.abs(ds.silence - f.received);
  app.scanLevel.value = 0.55 + 4.5 * Math.max(0, 1 - near / 1.5);

  // The truth, and the light.
  const tr = model.truth(state.fate, ds.silence);
  carrier.place(tr.spot, tr.u);
  carrier.setReveal(reveal.value * arrival.value);
  truthLabel.setVisible(reveal.value > 0.5);
  glow.update(dt, { fate: state.fate, silence: ds.silence, explode: explode.value });
  decks.update(explode.value, { received: shown.received !== null });
  for (const l of deckLabels) l.setVisible(explode.value > 0.6);

  // Console screens: redrawn only when what they show changes.
  const W = shown.windowMin;
  const curve = model.curve(W);
  app.chart.draw([shown.minute, W, Math.round(shown.alertLine * 100), state.fate, shown.phase], (ctx, size) => {
    history.length = 0;
    for (let t = 0; t <= shown.minute; t++) history.push(model.riskAt(f, t, W));
    drawChart(ctx, size, { reading: shown, history, curve });
  });
  const truthPlace = reveal.value > 0.5 ? placeOfSpot(tr.spot) : null;
  app.bars.draw([shown.minute, state.fate, truthPlace, shown.phase], (ctx, size) => drawBars(ctx, size, { reading: shown, truth: truthPlace }));
  app.log.draw([shown.phase === 'silent' ? shown.minute : -1, shown.received, shown.alert.fired], (ctx, size) => drawLog(ctx, size, { reading: shown }));

  // Readouts, the card, the caption.
  const risk = riskPct(shown.risk);
  if (risk !== last.risk) hud.setStat('risk', (last.risk = risk));
  const likely = `${placePct(shown.likely.p)}|${PLACE_WORDS[shown.likely.place]}`;
  if (likely !== last.likely) {
    last.likely = likely;
    hud.setStat('likely', placePct(shown.likely.p), PLACE_WORDS[shown.likely.place]);
  }
  const likelyColor = shown.likely.place === 'lost' ? 'coral' : '';
  if (likelyColor !== last.likelyColor) hud.setStatColor('likely', (last.likelyColor = likelyColor) || null);
  const warning = shown.phase === 'received' ? '—' : String(shown.windowLeft);
  if (warning !== last.warning) hud.setStat('warning', (last.warning = warning), warning === '—' ? undefined : 'min');
  const warnColor = shown.alert.fired && shown.alert.clearedAt === null ? 'amber' : '';
  if (warnColor !== last.warnColor) hud.setStatColor('warning', (last.warnColor = warnColor) || null);
  const marker = shown.alert.projected ?? -1;
  if (marker !== last.marker) hud.setMarkers('silence', (last.marker = marker) >= 0 ? [marker] : []);
  hud.setControl('silence', ds.silence);
  hud.setControl('alertLine', ds.alertLine);
  hud.setControl('windowMin', ds.windowMin);
  lesson.update(dt);
  const live = caption(shown, { shown: reveal.value > 0.5, place: placeOfSpot(tr.spot), words: describeSpot(tr.spot, ds.silence) }, P);
  const c = lesson.caption(live);
  if (c !== last.caption) hud.setCaption((last.caption = c));
  root.classList.toggle('lesson-on', lesson.speaking);

  rig.update(dt);
  lab.cutaway(camera);
  stage.uniforms.focusDistance.value = rig.focusDistance;
  stage.uniforms.focusBand.value = lookBand + Math.max(0, rig.focusSpanDepth - 4) * 5;
  labels.update(dt);
  labels.render();
  loupe.update(dt, camera);
});
stage.onResize((w, h) => {
  labels.setSize(w, h);
  fitToFrame();
  rig.refit();
});
// ---------- start ----------
async function boot(): Promise<void> {
  setState(state, { instant: true });
  hud.setControl('silence.speed', String(speed));
  await stage.start();
  if (params.view && views[params.view]) await setView(params.view, { instant: true });
  if (params.orbit) rig.setPose(rig.orbitPose(params.orbit.yaw, params.orbit.pitch, params.orbit.distanceScale));
  if (params.preset) applyPreset(params.preset - 1, { instant: true });
  if (params.ui === false) hud.setUiVisible(false, { quiet: true });
  if (params.compact) hud.setCompact(true);
  if (params.labels !== null) setLabels(params.labels);
  if (params.pose === 'closeup') rig.setPose(rig.resolve(CLOSEUP));
  // A plain first load opens with the tour, the control card faded away, and the first input hands
  // over to the lesson. A linked state opens still at that state; reduced motion opens on the lesson.
  const plain = plainLoad(params) && !linked;
  const tour = params.cinematic ?? (opensWithTour(params) && !linked);
  if (tour) {
    opening = true;
    hud.setPresenting(true);
    cinematic.play(0, { opening: true });
    hud.hint(matchMedia('(pointer: coarse)').matches ? 'Tap to take over' : 'Drag, click or press a key to take over', 4200);
  } else if (plain) {
    lesson.start();
  }
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
}

if (import.meta.env.DEV) Object.assign(window, { __dev: { THREE, stage, rig, app, lab, labels, loupe, cinematic, lesson, glow, carrier, model } });

installDebugHandle({
  ready: boot(),
  backend: stage.backend,
  fps: () => stage.fps(),
  fpsStats: (ms) => stage.fpsStats(ms),
  getState: () => ({ ...state, view: rig.view, preset: presetId, playing, speed, params: P.source, embed: query.embed, query, reading: model.read(state) }),
  setState: (p, o) => {
    let s = state;
    const q = p as Partial<ExhibitState>;
    if (q.fate && q.fate in FATES) s = { ...s, fate: q.fate };
    for (const k of ['silence', 'alertLine', 'windowMin'] as Key[]) if (typeof q[k] === 'number') s = withValue(s, k, q[k] as number);
    if (typeof q.truth === 'boolean') s = withTruth(s, q.truth);
    setPlaying(false);
    setState(s, { instant: o?.instant ?? true });
  },
  applyPreset: (i) => applyPreset(i - 1, { instant: true }),
  setView: (name, o) => setView(name, o),
  setUi: (v) => hud.setUiVisible(v, { quiet: true }),
  setLabels,
  cinematic: (on) => (on ? cinematic.play() : cinematic.stop()),
  lesson: (on) => (on ? lesson.start() : lesson.stop()),
  pose: (name) => rig.setPose(name === 'closeup' ? rig.resolve(CLOSEUP) : rig.heroPose()),
  orbit: (yawDeg, pitchDeg, distanceScale) => rig.setPose(rig.orbitPose(yawDeg, pitchDeg, distanceScale)),
  setCompact: (on) => hud.setCompact(on),
  text: () => hudText(root),
});
