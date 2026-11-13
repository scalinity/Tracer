// Tracer, self-playing: the Silent Specimen's bench with nothing to press. Four specimens take turns,
// each with a scripted true fate the model never sees; the clock runs by itself, slows near each
// moment that matters, and holds there under a one-line caption. Every number shown comes from
// TracerModel.read() at that minute, the same model the interactive bench uses.
// ?scene=N (1 to 4) starts on a specimen, ?t=M at a minute, ?paused holds it there.

import '@fontsource-variable/outfit';
import '@fontsource/dm-mono/400.css';
import '@fontsource/dm-mono/500.css';
import './kit/tokens.css';
import './kit/hud.css';
import './play.css';
import * as THREE from 'three/webgpu';
import { buildLabSet, CameraRig, cloneLooks, createStage, HOUSE_LOOKS, WorldLabels, type CameraPose, type HeroShot, type SafeArea } from './kit';
import { describeSpot, FATES, placeOfSpot, type FateId, type Place } from './model/fates';
import { TracerModel, type ExhibitState, type Reading } from './model/state';
import { ASSUMED, MAX_T } from './model/tracer';
import { buildApparatus } from './scene/apparatus';
import { buildCarrier } from './scene/carrier';
import { buildEcho } from './scene/echo';
import { Glow } from './scene/glow';
import { RECEIVER } from './scene/layout';
import { drawBars, drawChart, drawLog } from './scene/screen-draw';

const root = document.getElementById('app')!;
const world = document.getElementById('world')!;
const $ = <T extends HTMLElement>(s: string) => root.querySelector<T>(s)!;

const P = ASSUMED;
const model = new TracerModel(P);
const LINE = P.operatingPoint;
const W = P.windowMin;

// ---------- the four stories ----------
type Tone = 'info' | 'good' | 'warn' | 'alert' | 'bad';
interface Beat { at: number; hold: number; tone: Tone; text: (r: Reading) => string }
interface Story { fate: FateId; title: string; intro: string; end: number; beats: Beat[]; verdict: (r: Reading) => string; tone: Tone }

const pct = (p: number) => `${Math.round(p * 100)}%`;
const PLACE_NAME: Record<Place, string> = { ward: 'the ward', tube: 'the tube', station: 'the tube station', lost: 'the lost bin', lab: 'the lab' };
const at = (fate: FateId, m: number) => model.read({ fate, silence: m, alertLine: LINE, windowMin: W, truth: true });
const alertMin = (fate: FateId) => at(fate, MAX_T).alert.minute;

const alertBeat = (fate: FateId, extra: (r: Reading) => string = () => ''): Beat => ({
  at: alertMin(fate) ?? MAX_T, hold: 3.4, tone: 'alert',
  text: (r) => `{amber:${r.alert.minute} min} of silence. Tracer raises the alert with {amber:${W - r.alert.minute!} min} to spare.${extra(r)}`,
});

const STORIES: Story[] = [
  {
    fate: 'arrives', title: 'On time', tone: 'good', end: 30,
    intro: 'A specimen leaves the ward. Will it reach the lab before its 2-hour deadline?',
    beats: [
      { at: 0, hold: 1.8, tone: 'info', text: () => 'Scanned at the ward. The clock starts.' },
      { at: 7, hold: 1.2, tone: 'info', text: () => 'Into the pneumatic tube.' },
      { at: 12, hold: 1.2, tone: 'info', text: () => 'It reaches the lab\'s tube station.' },
      { at: FATES.arrives.received!, hold: 2.8, tone: 'good', text: (r) => `Scanned in the lab at {green:${r.received} min}. Safe, and no alert was needed.` },
    ],
    verdict: () => 'Arrived in time. Tracer stayed quiet, as it should.',
  },
  {
    fate: 'stalls', title: 'Stalled', tone: 'warn', end: 73,
    intro: 'The next specimen makes the same trip.',
    beats: [
      { at: 0, hold: 1.2, tone: 'info', text: () => 'Scanned at the ward. The clock starts.' },
      { at: 26, hold: 2.6, tone: 'warn', text: () => 'It\'s stuck at the tube station. The lab can\'t see that. It only sees silence.' },
      { at: 45, hold: 1.8, tone: 'info', text: (r) => `The longer the silence, the higher the risk: {cyan:${pct(r.risk)}} now.` },
      alertBeat('stalls', (r) => ` Check {cyan:${PLACE_NAME[r.likely.place]}} first.`),
      { at: FATES.stalls.received!, hold: 3, tone: 'good', text: (r) => `Found and scanned at {green:${r.received} min}, well inside the deadline.` },
    ],
    verdict: () => `Stalled. Tracer's alert gave the lab ${W - (alertMin('stalls') ?? 0)} minutes to find it.`,
  },
  {
    fate: 'lost', title: 'Lost', tone: 'bad', end: 124,
    intro: 'A third specimen. This one goes the wrong way.',
    beats: [
      { at: 0, hold: 1.2, tone: 'info', text: () => 'Scanned at the ward. The clock starts.' },
      { at: 13, hold: 2.6, tone: 'bad', text: () => 'Misrouted into the lost bin. No scan will ever come.' },
      alertBeat('lost'),
      { at: 82, hold: 2.6, tone: 'bad', text: (r) => `Still nothing. Tracer now thinks {coral:the lost bin} is most likely (${pct(r.places.lost)}).` },
      { at: W, hold: 3, tone: 'bad', text: () => 'The deadline passes. Without an alert, nobody would have started looking until now.' },
    ],
    verdict: () => `Lost. Tracer flagged it ${W - (alertMin('lost') ?? 0)} minutes before the deadline.`,
  },
  {
    fate: 'unsent', title: 'Never sent', tone: 'warn', end: 64,
    intro: 'The last one is scanned at the ward, but never goes into the tube.',
    beats: [
      { at: 0, hold: 1.2, tone: 'info', text: () => 'Scanned at the ward. The clock starts.' },
      { at: 9, hold: 2.6, tone: 'warn', text: () => 'It\'s still sitting at the ward. To the lab, this is the same silence.' },
      alertBeat('unsent', () => ' Same minute as the stalled one.'),
    ],
    verdict: () => 'Never sent. The alert still came in time, but silence alone can\'t say where it is.',
  },
];

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
const bar = $('.pl-bar');
const riskBox = $('.pl-risk');
const COLUMN = 860; // from this width up, the caption sits in a column under the risk card
const safeArea = (columnAllowed = true): SafeArea => {
  const w = root.clientWidth;
  const h = root.clientHeight;
  const phone = w <= 820;
  const column = columnAllowed && w >= COLUMN;
  // Keep the whole bench clear of the top row, the caption and the clock: a part hidden behind a
  // panel is a part the audience can't follow. On a wide screen the caption and risk share a
  // right-hand column, so the bench moves left of it and nothing covers its dials.
  const barRoom = h - bar.getBoundingClientRect().top;
  if (column) {
    return { top: top.getBoundingClientRect().bottom + 30, bottom: barRoom + 24, left: 16, right: riskBox.offsetWidth + 48, phone };
  }
  const captionRoom = phone ? 96 : 104;
  return {
    top: top.getBoundingClientRect().bottom + (phone ? riskBox.offsetHeight + 12 : 40),
    bottom: barRoom + captionRoom,
    left: 16,
    right: 16,
    phone,
  };
};
const views: Record<string, HeroShot> = {
  lab: { target: [0.9, 1.15, 0.3], yaw: -0.34, pitch: 0.18, fit: { width: 18.4, height: 10.9 }, bounds: lab.bounds },
};
// The column leaves the bench less room, and the room's back wall stops the camera backing away far
// enough to fit it, so the bench would come out cropped. Widen the lens instead, just enough that the
// camera stands where it would with the whole width free.
const BASE_FOV = 30;
const fitLens = (): void => {
  const v = views.lab;
  const w = root.clientWidth;
  const h = root.clientHeight;
  const need = (sa: SafeArea): number =>
    Math.max(v.fit.height / Math.max(80, h - sa.top - sa.bottom), v.fit.width / Math.max(80, w - sa.left - sa.right));
  const ratio = need(safeArea()) / need(safeArea(false));
  const tanHalf = Math.tan(THREE.MathUtils.degToRad(BASE_FOV) / 2) * Math.max(1, ratio);
  v.fov = Math.min(46, THREE.MathUtils.radToDeg(2 * Math.atan(tanHalf)));
};
fitLens();
const rig = new CameraRig(camera, world, views, { safeArea: () => safeArea() });
rig.enabled = false;
rig.idleDrift = false;
rig.focusPoint = new THREE.Vector3(RECEIVER.x, 1.4, RECEIVER.z);
let hero: CameraPose = rig.heroPose('lab');
const FOCUS_BAND = stage.uniforms.focusBand.value;

// ---------- in-world labels: Tracer's belief above the parts, the truth below on a leader ----------
const labels = new WorldLabels(scene, camera, root);
let shown: Reading = at('arrives', 0);
let fate: FateId = 'arrives';
let minute = 0;
const placeValue = (k: 'ward' | 'tube' | 'station' | 'lost') => () => (shown.phase === 'silent' ? pct(shown.places[k]) : '0%');
const o = { x: 0, y: 0, z: 0 };
labels.add(glow.anchors.ward, { name: 'Ward', value: placeValue('ward'), color: 'cyan', priority: 2, offset: o });
labels.add(glow.anchors.tube, { name: 'Tube', value: placeValue('tube'), color: 'cyan', priority: 1, offset: o });
labels.add(glow.anchors.station, { name: 'Tube station', value: placeValue('station'), color: 'cyan', priority: 6, offset: o });
labels.add(glow.anchors.lost, { name: 'Lost bin', value: placeValue('lost'), color: 'coral', priority: 5, offset: o });
labels.add(glow.anchors.lab, { name: 'Lab scanner', color: 'cyan', priority: 3, offset: o, value: () => (shown.received !== null ? `scanned at ${shown.received} min` : 'no scan yet') });
labels.add(app.anchors.beacon, {
  name: 'Alert', color: 'amber', priority: 6,
  value: () => (shown.alert.fired ? (shown.alert.clearedAt !== null ? 'found' : `on at ${shown.alert.minute} min`) : 'off'),
});
labels.add(carrier.anchor, {
  name: 'Really', color: 'truth', priority: 7, leader: true, offset: { x: -0.35, y: -1.05, z: 1.35 },
  value: () => describeSpot(model.truth(fate, minute).spot, minute),
});

// ---------- overlay ----------
const steps = $('.pl-steps');
const pills = STORIES.map((s, i) => {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'pl-step';
  b.innerHTML = `<span class="pl-step-n">${i + 1}</span>${s.title}`;
  b.addEventListener('click', () => void goTo(i));
  steps.appendChild(b);
  return b;
});
const captionEl = $('.pl-caption');
const riskV = $('.pl-risk-v');
const riskS = $('.pl-risk-s');
const clockV = $('.pl-clock-v');
const fill = $('.pl-fill');
const markDeadline = $('.pl-deadline');
const markAlert = $('.pl-alert');
const markScan = $('.pl-scan');
const card = $('.pl-card');
const pauseBtn = $<HTMLButtonElement>('.pl-pause');
const RAIL_MAX = MAX_T;
const railX = (m: number) => `${(Math.min(RAIL_MAX, Math.max(0, m)) / RAIL_MAX) * 100}%`;
markDeadline.style.left = railX(W);

const esc = (s: string) => s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]!);
const markup = (s: string) => esc(s).replace(/\{(\w+):([^}]*)\}/g, (_, c, t) => `<b class="c-${c}">${t}</b>`);

let lastCaption = '';
function setCaption(text: string, tone: Tone): void {
  const key = `${tone}|${text}`;
  if (key === lastCaption) return;
  lastCaption = key;
  captionEl.classList.remove('show');
  void captionEl.offsetWidth;
  captionEl.className = `pl-caption show t-${tone}`;
  captionEl.innerHTML = markup(text);
}

function showCard(n: string, title: string, sub: string, tone: Tone): void {
  card.className = `pl-card t-${tone}`;
  card.hidden = false;
  $('.pl-card-n').textContent = n;
  $('.pl-card-t').textContent = title;
  $('.pl-card-s').innerHTML = markup(sub);
  requestAnimationFrame(() => card.classList.add('show'));
}
const hideCard = () => {
  card.classList.remove('show');
};

let lastHud = '';
function drawHud(r: Reading, m: number): void {
  const missed = r.missed;
  const risk = r.phase === 'received' ? 0 : r.risk;
  const tone = r.phase === 'received' ? (missed ? 'bad' : 'good') : missed ? 'bad' : r.alert.fired ? 'alert' : 'info';
  const sub = r.phase === 'received'
    ? (missed ? 'Arrived too late' : 'Arrived in time')
    : missed ? 'Deadline passed' : r.alert.fired ? `Over the ${Math.round(LINE * 100)}% alert line` : `Alert at ${Math.round(LINE * 100)}%`;
  const key = `${Math.round(risk * 100)}|${tone}|${sub}|${Math.floor(m)}|${r.alert.fired}|${r.received}`;
  if (key === lastHud) return;
  lastHud = key;
  riskV.textContent = pct(risk);
  riskBox.className = `pl-risk t-${tone}`;
  riskS.textContent = sub;
  clockV.textContent = String(Math.floor(m));
  fill.style.width = railX(m);
  fill.className = `pl-fill t-${tone}`;
  markAlert.classList.toggle('on', r.alert.fired);
  if (r.alert.fired) markAlert.style.left = railX(r.alert.minute!);
  markScan.classList.toggle('on', r.received !== null);
  if (r.received !== null) markScan.style.left = railX(r.received);
}

// ---------- the director ----------
const params = new URLSearchParams(location.search);
let story = Math.min(STORIES.length - 1, Math.max(0, Number(params.get('scene') ?? 1) - 1 || 0));
let phase: 'intro' | 'run' | 'hold' | 'outro' | 'switch' = 'intro';
let phaseLeft = 0;
let beat = 0;
let paused = params.has('paused');
let runToken = 0;
const INTRO_SEC = 2.8;
const OUTRO_SEC = 4.2;
const MIN_SPEED = 4;
const MAX_SPEED = 16;

function setPaused(on: boolean): void {
  paused = on;
  pauseBtn.textContent = on ? '▶' : '❚❚';
  pauseBtn.setAttribute('aria-label', on ? 'Play' : 'Pause');
  root.classList.toggle('paused', on);
}
pauseBtn.addEventListener('click', () => setPaused(!paused));

function place(i: number, m: number): void {
  story = i;
  fate = STORIES[i].fate;
  minute = m;
  beat = STORIES[i].beats.findIndex((b) => b.at > m - 0.001);
  if (beat < 0) beat = STORIES[i].beats.length;
  pills.forEach((p, k) => {
    p.classList.toggle('on', k === i);
    p.classList.toggle('done', k < i);
  });
  root.dataset.tone = STORIES[i].tone;
  lastCaption = '';
  captionEl.classList.remove('show');
  stage.invalidateShadows();
}

async function goTo(i: number, o: { first?: boolean; t?: number } = {}): Promise<void> {
  const token = ++runToken;
  phase = 'switch';
  hideCard();
  if (!o.first) await stage.fade(1, 380, '#000');
  if (token !== runToken) return;
  place(i, o.t ?? 0);
  const s = STORIES[i];
  if (o.t !== undefined && o.t > 0) {
    // A linked moment: show the caption that moment would show.
    const prev = s.beats.filter((b) => b.at <= o.t!).pop();
    if (prev) setCaption(prev.text(at(fate, prev.at)), prev.tone);
    phase = 'run';
  } else {
    showCard(`${i + 1} of ${STORIES.length}`, s.title, s.intro, 'info');
    phase = 'intro';
    phaseLeft = INTRO_SEC;
  }
  await stage.fade(0, 520, '#000');
}

function next(): void {
  void goTo((story + 1) % STORIES.length);
}

window.addEventListener('keydown', (e) => {
  if (e.code === 'Space') { e.preventDefault(); setPaused(!paused); }
  if (e.key === 'ArrowRight') void goTo((story + 1) % STORIES.length);
  if (e.key === 'ArrowLeft') void goTo((story + STORIES.length - 1) % STORIES.length);
});

function tick(dt: number): void {
  if (paused || phase === 'switch') return;
  const s = STORIES[story];
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
    if (phaseLeft <= 0) phase = 'run';
    return;
  }
  if (phase === 'outro') {
    phaseLeft -= dt;
    if (phaseLeft <= 0) next();
    return;
  }
  // Running: fire the beat we've reached, else move the clock, slowing near the next stop.
  const b = s.beats[beat];
  if (b && minute >= b.at - 1e-6) {
    minute = b.at;
    setCaption(b.text(at(fate, b.at)), b.tone);
    beat++;
    phase = 'hold';
    phaseLeft = b.hold;
    return;
  }
  const stop = b ? b.at : s.end;
  if (minute >= s.end) {
    const r = at(fate, s.end);
    showCard(`${story + 1} of ${STORIES.length}`, s.title, s.verdict(r), s.tone);
    phase = 'outro';
    phaseLeft = OUTRO_SEC;
    return;
  }
  const speed = Math.min(MAX_SPEED, Math.max(MIN_SPEED, (stop - minute) / 1.4));
  minute = Math.min(stop, minute + dt * speed);
}

// ---------- frame ----------
let sway = 0;
let beaconOn = 0;
const history: number[] = [];
stage.onFrame((dt, elapsed) => {
  tick(dt);
  const f = FATES[fate];
  const ds: ExhibitState = { fate, silence: minute, alertLine: LINE, windowMin: W, truth: true };
  shown = model.read(ds);
  if (!paused && phase !== 'hold') stage.invalidateShadows(0.25);

  app.stopwatch.update(minute, W, shown.alert.projected);
  app.alertKnob.set((LINE - 0.1) / 0.8);
  app.windowKnob.set((W - 60) / 180);
  app.truthSwitch.set(1);
  app.placeLineHandle(LINE);
  const alarm = shown.alert.fired && shown.alert.clearedAt === null;
  beaconOn += ((alarm ? 1 : 0) - beaconOn) * (1 - Math.exp(-dt * 5));
  app.beaconLevel.value = beaconOn * (3.0 + 0.45 * Math.sin(elapsed * 2.2));
  const near = f.received === null ? Infinity : Math.abs(minute - f.received);
  app.scanLevel.value = 0.55 + 4.5 * Math.max(0, 1 - near / 1.5);

  const tr = model.truth(fate, minute);
  carrier.place(tr.spot, tr.u);
  carrier.setReveal(1);
  glow.update(dt, { fate, silence: minute, explode: 0 });

  const curve = model.curve(W);
  app.chart.draw([shown.minute, W, Math.round(LINE * 100), fate, shown.phase], (ctx, size) => {
    history.length = 0;
    for (let t = 0; t <= shown.minute; t++) history.push(model.riskAt(f, t, W));
    drawChart(ctx, size, { reading: shown, history, curve });
  });
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
  labels.update(dt);
  labels.render();
});
stage.onResize((w, h) => {
  labels.setSize(w, h);
  fitLens();
  rig.refit();
  hero = rig.heroPose('lab');
});

await stage.start();
fitLens();
rig.refit();
hero = rig.heroPose('lab');
setPaused(paused);
const startT = params.has('t') ? Math.max(0, Math.min(MAX_T, Number(params.get('t')) || 0)) : undefined;
void goTo(story, { first: true, t: startT });
