// Console instruments: the stopwatch (its hand is the silence, its coral bezel mark the window's
// end), two knobs with amber value arcs (the alert line and the window) and the truth switch. Every
// part faces +z like a clock and turns about z; the console places them on its face.

import * as THREE from 'three/webgpu';
import { atan, degrees, float, mod, positionLocal, smoothstep, uniform } from 'three/tsl';
import { brass, emitterMaterial, lacquer, machined, rubber } from '../kit/materials';
import { knurledRing, lathe, roundedBox } from '../kit/geometry';

/** The stopwatch reads 0 to 240 min over 270°, from 7:30 clockwise to 4:30. */
export const DIAL_MAX = 240;
const SWEEP = 270;
/** Degrees counterclockwise from +x for a minute on the dial. */
export const dialAngle = (min: number) => 225 - (min / DIAL_MAX) * SWEEP;
/** Minutes per degree of turn on the dial. */
export const MIN_PER_DEG = DIAL_MAX / SWEEP;
const RAD = Math.PI / 180;

/** A ring segment that lights from the dial's start (225°) clockwise through `sweep` degrees. */
function arcMask(sweep: THREE.UniformNode<'float', number>, from?: THREE.UniformNode<'float', number>): THREE.Node<'float'> {
  const a = degrees(atan(positionLocal.y, positionLocal.x));
  const fromStart = mod(float(225).sub(a).add(360), 360);
  const until = float(1).sub(smoothstep(sweep.sub(0.6), sweep.add(0.6), fromStart));
  if (!from) return until;
  return until.mul(smoothstep(from.sub(0.6), from.add(0.6), fromStart));
}

function faceTexture(): THREE.CanvasTexture {
  const size = 1024;
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const g = c.getContext('2d') as CanvasRenderingContext2D;
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  const draw = () => {
    const m = size / 2;
    g.clearRect(0, 0, size, size);
    const grad = g.createRadialGradient(m, m * 0.8, size * 0.04, m, m, m);
    grad.addColorStop(0, '#262a33');
    grad.addColorStop(1, '#14171d');
    g.fillStyle = grad;
    g.beginPath();
    g.arc(m, m, m, 0, Math.PI * 2);
    g.fill();
    g.strokeStyle = '#d9d2c2';
    g.fillStyle = '#d9d2c2';
    for (let min = 0; min <= DIAL_MAX; min += 5) {
      const a = -dialAngle(min) * RAD; // canvas y points down
      const major = min % 30 === 0;
      const r0 = m * 0.93;
      const r1 = r0 - (major ? m * 0.12 : min % 15 === 0 ? m * 0.075 : m * 0.045);
      g.lineWidth = major ? size * 0.007 : size * 0.0035;
      g.beginPath();
      g.moveTo(m + Math.cos(a) * r0, m + Math.sin(a) * r0);
      g.lineTo(m + Math.cos(a) * r1, m + Math.sin(a) * r1);
      g.stroke();
      if (major) {
        g.font = `500 ${Math.round(size * 0.058)}px "DM Mono", ui-monospace, monospace`;
        g.textAlign = 'center';
        g.textBaseline = 'middle';
        const lr = m * 0.66;
        g.fillText(String(min), m + Math.cos(a) * lr, m + Math.sin(a) * lr);
      }
    }
    // Both titles stay above the 0 and 240 numerals at the foot of the dial.
    g.font = `500 ${Math.round(size * 0.036)}px "DM Mono", ui-monospace, monospace`;
    g.fillStyle = '#9ea8c2';
    g.textAlign = 'center';
    g.fillText('MIN SINCE COLLECTION', m, m + m * 0.33);
    g.font = `600 ${Math.round(size * 0.05)}px "DM Mono", ui-monospace, monospace`;
    g.fillStyle = '#d9d2c2';
    g.fillText('SILENCE', m, m + m * 0.22);
    tex.needsUpdate = true;
  };
  draw();
  if (typeof document !== 'undefined' && document.fonts) void document.fonts.ready.then(draw);
  return tex;
}

export interface Stopwatch {
  group: THREE.Group;
  /** Drag target for the hand: the whole face, turned by the pointer's angle around the center. */
  face: THREE.Object3D;
  /** Drag target for the window: the knurled bezel. */
  bezel: THREE.Object3D;
  /** Where a hint pill can sit, below the dial. */
  hint: THREE.Object3D;
  center(): THREE.Vector3;
  update(silence: number, windowMin: number, alertMinute: number | null): void;
}

export function stopwatch(radius = 1.15): Stopwatch {
  const group = new THREE.Group();
  group.name = 'stopwatch';
  const faceMat = lacquer('#ffffff');
  faceMat.map = faceTexture();
  faceMat.roughness = 0.5;
  faceMat.clearcoat = 0.4;
  const face = new THREE.Mesh(new THREE.CircleGeometry(radius, 96), faceMat);
  face.position.z = 0.02;
  // A glass crystal over the face would mirror the room; the face stays matte and readable. The well's
  // cap sits under the face, not in its plane, where the two fought for depth and shredded the numerals.
  const well = new THREE.Mesh(lathe([[0, -0.1], [radius + 0.04, -0.1], [radius + 0.04, 0], [0, 0]], 96).rotateX(Math.PI / 2), lacquer('#0d0f13'));
  // The bezel: a knurled brass ring that turns with the window, its coral mark at the window's end.
  const bezel = new THREE.Group();
  const ring = new THREE.Mesh(knurledRing({ radius: radius + 0.2, width: 0.2, ridges: 140, depth: 0.025, inner: radius + 0.02 }).rotateX(Math.PI / 2), brass());
  ring.position.z = 0.07;
  ring.castShadow = true;
  const mark = new THREE.Mesh(new THREE.ConeGeometry(0.075, 0.22, 3).rotateZ(Math.PI), lacquer('#ff6b86'));
  mark.position.set(0, radius + 0.2, 0.19);
  bezel.add(ring, mark);
  // Cyan arc for the silence so far, coral beyond the window, both thin rings inside the ticks.
  const elapsed = uniform(0);
  const closedFrom = uniform(270);
  const arcGeo = new THREE.RingGeometry(radius * 0.955, radius * 0.995, 192, 1);
  const elapsedArc = new THREE.Mesh(arcGeo, emitterMaterial('#7fe3ff', 1.1, arcMask(elapsed)));
  elapsedArc.position.z = 0.03;
  const closedArc = new THREE.Mesh(arcGeo, emitterMaterial('#ff6b86', 0.75, arcMask(uniform(270), closedFrom)));
  closedArc.position.z = 0.028;
  // The hand: a tapered cream needle with a brass hub; the amber tick marks the alert minute.
  const hand = new THREE.Group();
  const needle = new THREE.Mesh(new THREE.ConeGeometry(0.045, radius * 1.05, 4).translate(0, radius * 0.4, 0), machined('#efe6d4'));
  needle.position.z = 0.1;
  needle.castShadow = true;
  hand.add(needle);
  const hub = new THREE.Mesh(new THREE.CylinderGeometry(0.11, 0.12, 0.1, 32).rotateX(Math.PI / 2), brass());
  hub.position.z = 0.12;
  const tick = new THREE.Group();
  const tickBar = new THREE.Mesh(roundedBox(0.045, 0.2, 0.02, 0.01, 1), emitterMaterial('#ffb454', 1.4));
  tickBar.position.set(0, radius * 0.84, 0.035);
  tick.add(tickBar);
  group.add(well, face, elapsedArc, closedArc, tick, hand, hub, bezel);
  // Hints sit above the dial: the console's lower half falls below the first view's frame.
  const hint = new THREE.Object3D();
  hint.position.set(0, radius + 0.62, 0.35);
  group.add(hint);
  const c = new THREE.Vector3();
  return {
    group, face, bezel, hint,
    center: () => group.getWorldPosition(c),
    update(silence, windowMin, alertMinute) {
      hand.rotation.z = (dialAngle(silence) - 90) * RAD;
      bezel.rotation.z = (dialAngle(windowMin) - 90) * RAD;
      elapsed.value = (silence / DIAL_MAX) * SWEEP;
      closedFrom.value = (windowMin / DIAL_MAX) * SWEEP;
      tick.visible = alertMinute !== null;
      if (alertMinute !== null) tick.rotation.z = (dialAngle(alertMinute) - 90) * RAD;
    },
  };
}

export interface Knob {
  group: THREE.Group;
  /** Drag target: the knurled grip and its cap. */
  grip: THREE.Object3D;
  hint: THREE.Object3D;
  center(): THREE.Vector3;
  /** 0 to 1 over the knob's 270° travel. */
  set(fraction: number): void;
}

/** A console knob: knurled rubber grip, brass cap, amber index and an amber arc for its value. */
export function knob(radius = 0.6): Knob {
  const group = new THREE.Group();
  const turn = new THREE.Group();
  const grip = new THREE.Mesh(knurledRing({ radius, width: 0.5, ridges: 54, depth: 0.04, inner: 0.05 }).rotateX(Math.PI / 2), rubber('#15171b'));
  grip.position.z = 0.25;
  const cap = new THREE.Mesh(lathe([[0, 0], [radius * 0.86, 0], [radius * 0.86, 0.22], [radius * 0.72, 0.32], [0, 0.34]], 64).rotateX(Math.PI / 2), brass());
  cap.position.z = 0.5;
  const index = new THREE.Mesh(roundedBox(0.07, radius * 0.6, 0.04, 0.02, 1), emitterMaterial('#ffb454', 1.0));
  index.position.set(0, radius * 0.42, 0.85);
  turn.add(grip, cap, index);
  for (const m of [grip, cap]) m.castShadow = true;
  const sweep = uniform(0);
  const ringGeo = new THREE.RingGeometry(radius + 0.12, radius + 0.18, 160, 1);
  const track = new THREE.Mesh(ringGeo, new THREE.MeshBasicNodeMaterial({ color: '#232a36' }));
  track.position.z = 0.012;
  const arc = new THREE.Mesh(ringGeo, emitterMaterial('#ffb454', 1.1, arcMask(sweep)));
  arc.position.z = 0.016;
  const bezel = new THREE.Mesh(new THREE.TorusGeometry(radius + 0.06, 0.016, 8, 96), brass());
  bezel.position.z = 0.02;
  group.add(turn, track, arc, bezel);
  const hint = new THREE.Object3D();
  hint.position.set(0, radius + 0.62, 0.4);
  group.add(hint);
  const c = new THREE.Vector3();
  return {
    group, grip: turn, hint,
    center: () => group.getWorldPosition(c),
    set(fraction) {
      const k = Math.min(1, Math.max(0, fraction));
      // The pointer starts where the value arc starts (7:30) and turns clockwise with it.
      turn.rotation.z = (135 - k * SWEEP) * RAD;
      sweep.value = k * SWEEP;
    },
  };
}

export interface ToggleSwitch {
  group: THREE.Group;
  lever: THREE.Object3D;
  hint: THREE.Object3D;
  set(on: number): void;
}

/** A brass bat-handle toggle on a round lacquer boss: up shows the truth, down hides it. */
export function toggleSwitch(): ToggleSwitch {
  const group = new THREE.Group();
  const boss = new THREE.Mesh(lathe([[0, 0], [0.42, 0], [0.42, 0.1], [0.36, 0.16], [0, 0.16]], 48).rotateX(Math.PI / 2), lacquer('#1a1e26'));
  const nut = new THREE.Mesh(new THREE.CylinderGeometry(0.17, 0.17, 0.12, 6).rotateX(Math.PI / 2), machined('#b9bec8'));
  nut.position.z = 0.2;
  const pivot = new THREE.Group();
  pivot.position.z = 0.24;
  const bat = new THREE.Mesh(lathe([[0, 0], [0.07, 0], [0.06, 0.5], [0.09, 0.56], [0.06, 0.64], [0, 0.65]], 24), brass());
  bat.castShadow = true;
  pivot.add(bat);
  group.add(boss, nut, pivot);
  // The hint stands off the switch's face, so it sits above a switch that faces up.
  const hint = new THREE.Object3D();
  hint.position.set(0, 0, 1.05);
  group.add(hint);
  return {
    group, lever: group, hint,
    set(on) {
      // The bat is lathed along +y; a quarter turn about x points it at the visitor. Up (60°) shows
      // the truth, down (120°) hides it.
      pivot.rotation.x = THREE.MathUtils.lerp(120, 60, on) * RAD;
    },
  };
}

/** A small engraved plate (brass or dark) with one line of mono capitals. */
export function plate(text: string, o: { width?: number; height?: number; brassy?: boolean } = {}): THREE.Mesh {
  const w = o.width ?? 1.6;
  const h = o.height ?? 0.34;
  const c = document.createElement('canvas');
  c.width = 512;
  c.height = Math.round((512 * h) / w);
  const g = c.getContext('2d') as CanvasRenderingContext2D;
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  const draw = () => {
    g.fillStyle = o.brassy ? '#b98a46' : '#15181e';
    g.fillRect(0, 0, c.width, c.height);
    g.fillStyle = o.brassy ? '#3d2a12' : '#cfc7b4';
    g.font = `500 ${Math.round(c.height * 0.5)}px "DM Mono", ui-monospace, monospace`;
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText(text, c.width / 2, c.height * 0.54);
    tex.needsUpdate = true;
  };
  draw();
  if (typeof document !== 'undefined' && document.fonts) void document.fonts.ready.then(draw);
  const mat = o.brassy ? machined('#ffffff') : lacquer('#ffffff');
  mat.map = tex;
  mat.roughness = o.brassy ? 0.38 : 0.55;
  return new THREE.Mesh(roundedBox(w, h, 0.04, 0.02, 2), mat);
}
