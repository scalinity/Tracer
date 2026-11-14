// Tracer, self-playing: the Silent Specimen's bench with nothing to press. Four specimens take turns
// (model/play-script.ts holds the script); the clock runs by itself, stands still on each caption, and
// loops forever. The white capsule is the truth, the glow is Tracer's guess, and one short caption at
// a time names what matters.
//
// The risk bar and the alerts are TracerNet's, Tracer's primary risk model (tracernet-v2): its
// precomputed p_miss for one held-out synthetic specimen per scene (model/tracernet-stories.json,
// picked from docs/demo/neural.json by a fixed rule). The bar uses the bench chart's log axis, so it
// crosses the alert mark on the tick the stored alert fires. Where the specimen is (the glow, the place
// an alert names) is the forward filter of TracerModel, as on the interactive bench.
// Address parameters, not controls: ?scene=N (1 to 4) starts on a scene, ?t=M at a minute, ?paused
// holds it there.

import '@fontsource-variable/outfit';
import '@fontsource/dm-mono/400.css';
import '@fontsource/dm-mono/500.css';
import './kit/tokens.css';
import './kit/hud.css';
import './play.css';
import * as THREE from 'three/webgpu';
import { buildLabSet, CameraRig, cloneLooks, createStage, HOUSE_LOOKS, type CameraPose, type HeroShot, type SafeArea } from './kit';
import { FATES, placeOfSpot, type FateId } from './model/fates';
import { DEADLINE, FADE_SEC, INTRO_SEC, NET, OUTRO_SEC, riskFill, SCENES, stepClock, type Card, type Tone } from './model/play-script';
import { TracerModel, type Reading } from './model/state';
import { ASSUMED, MAX_T } from './model/tracer';
import { scoreAt } from './model/tracernet';
import { buildApparatus } from './scene/apparatus';
import { buildCarrier } from './scene/carrier';
import { buildEcho } from './scene/echo';
import { Glow } from './scene/glow';
import { RECEIVER } from './scene/layout';
import { drawBars, drawLog, drawNetChart, netAxis } from './scene/screen-draw';

const root = document.getElementById('app')!;
const world = document.getElementById('world')!;
const $ = <T extends HTMLElement>(s: string) => root.querySelector<T>(s)!;

const P = ASSUMED;
const model = new TracerModel(P);
const W = DEADLINE;
const TICK = NET.tick_min;

// ---------- the reading: TracerNet's risk and alert, the forward filter's places ----------
type Shown = Reading & { score: number | null };
/**
 * The bench's reading at minute m with TracerNet's p_miss, alert line and alert for this scene's
 * held-out specimen in place of the analytic model's. Where the specimen is stays the forward
 * filter's, and after the receipt scan the outcome is known. `score` is null before TracerNet's
 * first tick.
 */
const read = (fate: FateId, m: number): Shown => {
  const r = model.read({ fate, silence: m, alertLine: P.operatingPoint, windowMin: W, truth: true });
  const s = NET.stories[fate];
  const score = r.phase === 'silent' ? scoreAt(s, r.minute, TICK) : r.risk;
  const fired = s.alert_min !== null && s.alert_min <= r.minute;
  return {
    ...r, risk: score ?? 0, score, alertLine: s.line, lead: null, components: null, scan: null,
    alert: { projected: fired ? s.alert_min : null, minute: s.alert_min, fired, clearedAt: fired ? r.received : null },
  };
};

// ---------- stage and scene ----------
const looks = cloneLooks(HOUSE_LOOKS);
looks.lab.sets = ['room', 'console', 'bench', 'glass'];
looks.lab.fog = { color: '#252e43', density: 0.0105 };
const stage = await createStage({ container: world, looks, initialLook: 'lab', lightTarget: { x: 0, y: 1.5, z: 0 } });
const { scene, camera } = stage;

const app = buildApparatus();
scene.add(app.group);
stage.registerSet('console', app.console);
stage.registerSet('bench', app.bench);
for (const m of app.glass) stage.registerSet('glass', m);
const carrier = buildCarrier();
scene.add(carrier.group, carrier.anchor);
const glow = new Glow(model);
scene.add(glow.group);
const FLOOR_Y = -9.05;
const lab = buildLabSet({ floorY: FLOOR_Y, seed: 5, windowGain: 0.4 });
buildEcho(lab.window, lab.anchors.window, FLOOR_Y + 44);
scene.add(lab.group);
stage.registerSet('room', lab.group);

// ---------- camera: the bench's hero framing, swaying slowly, never steered by the viewer ----------
const top = $('.pl-top');
const hud = $('.pl-hud');
// Keep the whole bench clear of the top row, the caption and the clock: a part hidden behind a panel
// is a part the audience can't follow.
const safeArea = (): SafeArea => {
  const w = root.clientWidth;
  const h = root.clientHeight;
  const phone = w <= 820;
  const captionRoom = phone ? 84 : 112;
  return { top: top.getBoundingClientRect().bottom + 8, bottom: h - hud.getBoundingClientRect().top + captionRoom, left: 12, right: 12, phone };
};
const views: Record<string, HeroShot> = {
  lab: { target: [0.9, 1.15, 0.3], yaw: -0.34, pitch: 0.18, fit: { width: 18.4, height: 10.9 }, fov: 30, bounds: lab.bounds },
};
const rig = new CameraRig(camera, world, views, { safeArea });
rig.enabled = false;
rig.idleDrift = false;
rig.focusPoint = new THREE.Vector3(RECEIVER.x, 1.4, RECEIVER.z);
let hero: CameraPose = rig.heroPose('lab');
const FOCUS_BAND = stage.uniforms.focusBand.value;

// ---------- overlay ----------
const tagEl = $('.pl-tag');
const captionEl = $('.pl-caption');
const clockV = $('.pl-clock-v');
const ring = root.querySelector<SVGCircleElement>('.pl-ring-fg')!;
const meter = $('.pl-meter');
const meterFill = $('.pl-meter-fill');
const meterMark = $('.pl-meter-mark');
const card = $('.pl-card');
const alarmEl = $('.pl-alarm');
const pctOf = (f: number) => `${(Math.min(1, Math.max(0, f)) * 100).toFixed(2)}%`;

let lastCaption = '';
function setCaption(text: string, tone: Tone): void {
  const key = `${tone}|${text}`;
  if (key === lastCaption) return;
  lastCaption = key;
  captionEl.classList.remove('show');
  void captionEl.offsetWidth;
  captionEl.className = `pl-caption show t-${tone}`;
  captionEl.textContent = text;
}
const hideCaption = () => {
  lastCaption = '';
  captionEl.classList.remove('show');
};

function showCard(c: { k?: string; n: string; unit?: string; line?: string; tone: Tone; intro?: boolean }): void {
  card.className = `pl-card t-${c.tone}${c.intro ? ' intro' : ''}`;
  card.hidden = false;
  const set = (sel: string, t = '') => {
    const e = $(`.${sel}`);
    e.textContent = t;
    e.hidden = t === '';
  };
  set('pl-card-k', c.k);
  set('pl-card-n', c.n);
  set('pl-card-u', c.unit);
  set('pl-card-s', c.line);
  requestAnimationFrame(() => card.classList.add('show'));
}
const endCard = (c: Card) => showCard({ n: c.n, unit: c.unit, line: c.line, tone: c.tone });
const hideCard = () => card.classList.remove('show');

/** The alert moment: an amber pulse round the frame edge, replayed from the start. */
function sound(on: boolean): void {
  root.classList.toggle('alarming', on);
  if (on) {
    alarmEl.classList.remove('go');
    void alarmEl.offsetWidth;
    alarmEl.classList.add('go');
  } else {
    alarmEl.classList.remove('go');
  }
}

let lastHud = '';
function drawHud(r: Shown, m: number): void {
  const tone: Tone = r.phase === 'received' ? (r.missed ? 'bad' : 'good') : r.missed ? 'bad' : r.alert.fired ? 'alert' : 'info';
  const fill = riskFill(r.score);
  const key = `${Math.floor(m)}|${tone}|${fill}`;
  if (key === lastHud) return;
  lastHud = key;
  clockV.textContent = String(Math.floor(m));
  ring.style.strokeDashoffset = String(100 - Math.min(100, (m / W) * 100));
  root.classList.toggle('late', m >= W && r.phase === 'silent');
  meter.className = `pl-meter t-${tone}`;
  meterFill.style.width = pctOf(fill);
}

// ---------- the director ----------
const params = new URLSearchParams(location.search);
let story = Math.min(SCENES.length - 1, Math.max(0, Number(params.get('scene') ?? 1) - 1 || 0));
let fate: FateId = SCENES[story].fate;
let minute = 0;
let shown: Shown = read(fate, 0);
let phase: 'intro' | 'run' | 'hold' | 'outro' | 'switch' = 'intro';
let phaseLeft = 0;
let beat = 0;
const paused = params.has('paused');
let runToken = 0;

function place(i: number, m: number): void {
  story = i;
  fate = SCENES[i].fate;
  minute = m;
  beat = SCENES[i].beats.findIndex((b) => b.at > m - 0.001);
  if (beat < 0) beat = SCENES[i].beats.length;
  tagEl.textContent = `${i + 1} of ${SCENES.length} · ${SCENES[i].title}`;
  meterMark.style.left = pctOf(riskFill(NET.stories[fate].line));
  root.dataset.tone = SCENES[i].card.tone;
  hideCaption();
  sound(false);
  lastHud = '';
  stage.invalidateShadows();
}

async function goTo(i: number, o: { first?: boolean; t?: number } = {}): Promise<void> {
  const token = ++runToken;
  phase = 'switch';
  hideCard();
  if (!o.first) await stage.fade(1, (FADE_SEC * 1000) * 0.42, '#000');
  if (token !== runToken) return;
  place(i, o.t ?? 0);
  const s = SCENES[i];
  if (o.t !== undefined && o.t > 0) {
    // A linked moment: show the caption that moment would show.
    const prev = s.beats.filter((b) => b.at <= o.t!).pop();
    if (prev) setCaption(prev.text, prev.tone);
    phase = 'run';
  } else {
    showCard({ k: `${i + 1} of ${SCENES.length}`, n: s.title, tone: 'info', intro: true });
    phase = 'intro';
    phaseLeft = INTRO_SEC;
  }
  await stage.fade(0, (FADE_SEC * 1000) * 0.58, '#000');
}

function tick(dt: number): void {
  if (paused || phase === 'switch') return;
  const s = SCENES[story];
  if (phase === 'intro') {
    phaseLeft -= dt;
    if (phaseLeft <= 0) {
      hideCard();
      phase = 'run';
    }
    return;
  }
  if (phase === 'hold') {
    phaseLeft -= dt;
    if (phaseLeft <= 0) {
      phase = 'run';
      root.classList.remove('alarming');
    }
    return;
  }
  if (phase === 'outro') {
    phaseLeft -= dt;
    if (phaseLeft <= 0) void goTo((story + 1) % SCENES.length);
    return;
  }
  // Running: fire the beat we've reached, else move the clock, easing into the next stop.
  const b = s.beats[beat];
  if (b && minute >= b.at - 1e-6) {
    minute = b.at;
    setCaption(b.text, b.tone);
    if (b.alarm) sound(true);
    beat++;
    phase = 'hold';
    phaseLeft = b.hold;
    return;
  }
  if (minute >= s.end) {
    hideCaption();
    endCard(s.card);
    phase = 'outro';
    phaseLeft = OUTRO_SEC;
    return;
  }
  minute = stepClock(minute, b ? b.at : s.end, dt);
}

// ---------- frame ----------
let sway = 0;
let beaconOn = 0;
stage.onFrame((dt, elapsed) => {
  tick(dt);
  const f = FATES[fate];
  shown = read(fate, minute);
  if (!paused && phase !== 'hold') stage.invalidateShadows(0.25);

  app.stopwatch.update(minute, W, shown.alert.projected);
  // The knob has no scale of its own: its arc stands at the alert line's height on the chart beside it.
  app.alertKnob.set(netAxis(shown.alertLine));
  app.windowKnob.set((W - 60) / 180);
  app.truthSwitch.set(1);
  // The beacon lights with the alert and flares through the alert moment itself.
  const alarm = shown.alert.fired && shown.alert.clearedAt === null;
  const flare = root.classList.contains('alarming');
  beaconOn += ((alarm ? (flare ? 1.8 : 1) : 0) - beaconOn) * (1 - Math.exp(-dt * (flare ? 9 : 5)));
  app.beaconLevel.value = beaconOn * (3.0 + (flare ? 1.4 : 0.45) * Math.sin(elapsed * (flare ? 7 : 2.2)));
  const near = f.received === null ? Infinity : Math.abs(minute - f.received);
  app.scanLevel.value = 0.55 + 4.5 * Math.max(0, 1 - near / 1.5);

  const tr = model.truth(fate, minute);
  carrier.place(tr.spot, tr.u);
  carrier.setReveal(1);
  glow.update(dt, { fate, silence: minute, explode: 0 });

  app.chart.draw([shown.minute, fate, shown.phase], (ctx, size) =>
    drawNetChart(ctx, size, { reading: shown, score: shown.score, story: NET.stories[fate], tick: TICK }));
  const truthPlace = placeOfSpot(tr.spot);
  app.bars.draw([shown.minute, fate, truthPlace, shown.phase], (ctx, size) => drawBars(ctx, size, { reading: shown, truth: truthPlace }));
  app.log.draw([shown.phase === 'silent' ? shown.minute : -1, shown.received, shown.alert.fired], (ctx, size) => drawLog(ctx, size, { reading: shown }));

  drawHud(shown, minute);

  // The camera breathes around the hero framing; it never moves far enough to lose a part.
  sway += paused ? 0 : dt;
  rig.setPose({ ...hero, yaw: hero.yaw + 0.07 * Math.sin(sway * 0.11), pitch: hero.pitch + 0.015 * Math.sin(sway * 0.07) });
  rig.update(dt);
  lab.cutaway(camera);
  stage.uniforms.focusDistance.value = rig.focusDistance;
  stage.uniforms.focusBand.value = FOCUS_BAND + Math.max(0, rig.focusSpanDepth - 4) * 5;
});
stage.onResize(() => {
  rig.refit();
  hero = rig.heroPose('lab');
});

await stage.start();
rig.refit();
hero = rig.heroPose('lab');
const startT = params.has('t') ? Math.max(0, Math.min(MAX_T, Number(params.get('t')) || 0)) : undefined;
void goTo(story, { first: true, t: startT });
