// The exploded view's three decks, stacked by how much is known. The bench itself is the truth. Above
// it, What the lab sees: a smoked-glass plate with the route etched on it and an amber pip at each
// scan that has happened; between the scans, nothing. On top, What Tracer infers: the plate the glow
// lifts onto (glow.ts), the same route as a map of light.

import * as THREE from 'three/webgpu';
import { uniform } from 'three/tsl';
import { emitterMaterial, lacquer, machined, thinGlass } from '../kit/materials';
import { lightStrip, roundedBox } from '../kit/geometry';
import { DECK3_Y } from './glow';
import { PLACES_PLAN } from './layout';
import { plate } from './instruments';

export const DECK2_Y = 6.3;
const W = 17.6;
const D = 4.6;
const CZ = -0.25;

type Uniform = THREE.UniformNode<'float', number>;

export interface Decks {
  group: THREE.Group;
  lab: THREE.Group;
  tracer: THREE.Group;
  /** Front-edge anchors for the deck labels. */
  anchors: { truth: THREE.Object3D; lab: THREE.Object3D; tracer: THREE.Object3D };
  /** 0 to 1: the decks rise from the bench to their heights. Scans light their pips. */
  update(explode: number, scans: { received: boolean }): void;
}

/** A thin etched line on a plate between two plan points. */
function etch(a: THREE.Vector2, b: THREE.Vector2, mat: THREE.Material, dashed = false): THREE.Object3D {
  const g = new THREE.Group();
  const len = a.distanceTo(b);
  const pieces = dashed ? Math.max(2, Math.round(len / 0.5)) : 1;
  for (let i = 0; i < pieces; i++) {
    if (dashed && i % 2 === 1) continue;
    const k0 = i / pieces;
    const k1 = (i + 1) / pieces;
    const seg = new THREE.Mesh(new THREE.BoxGeometry(len / pieces, 0.012, 0.045), mat);
    seg.position.set(THREE.MathUtils.lerp(a.x, b.x, (k0 + k1) / 2), 0.008, THREE.MathUtils.lerp(a.y, b.y, (k0 + k1) / 2));
    seg.rotation.y = -Math.atan2(b.y - a.y, b.x - a.x);
    g.add(seg);
  }
  return g;
}

function ringAt(p: THREE.Vector2, r: number, mat: THREE.Material): THREE.Mesh {
  const m = new THREE.Mesh(new THREE.RingGeometry(r - 0.04, r, 48).rotateX(-Math.PI / 2), mat);
  m.position.set(p.x, 0.012, p.y);
  return m;
}

function deck(title: string): { group: THREE.Group; plateTop: THREE.Object3D; front: THREE.Object3D } {
  const group = new THREE.Group();
  const glass = new THREE.Mesh(roundedBox(W, 0.1, D, 0.04, 2), thinGlass({ opacity: 0.32, tint: '#9fb8d8' }));
  glass.position.set(0, -0.05, CZ);
  glass.userData.occluder = false;
  glass.renderOrder = 21;
  // A lacquered frame under the glass's edge, and a thin warm strip along the front.
  const frameMat = lacquer('#0d1015');
  for (const [w, d, x, z] of [[W, 0.16, 0, CZ + D / 2], [W, 0.16, 0, CZ - D / 2], [0.16, D, W / 2, CZ], [0.16, D, -W / 2, CZ]] as const) {
    const bar = new THREE.Mesh(roundedBox(w, 0.16, d, 0.04, 2), frameMat);
    bar.position.set(x, -0.1, z);
    bar.castShadow = true;
    group.add(bar);
  }
  const led = new THREE.Mesh(lightStrip({ length: W - 0.6, thickness: 0.025, depth: 0.025 }), emitterMaterial('#ffd9a0', 1.15));
  led.position.set(0, -0.19, CZ + D / 2 + 0.07);
  const sign = plate(title, { width: 3.6, height: 0.32 });
  sign.position.set(-W / 2 + 2.4, -0.1, CZ + D / 2 + 0.1);
  group.add(glass, led, sign);
  const plateTop = new THREE.Group();
  group.add(plateTop);
  // The deck's label hangs off the middle of its front edge, clear of the places' labels at the ends.
  const front = new THREE.Object3D();
  front.position.set(-3.4, 0.25, CZ + D / 2);
  group.add(front);
  return { group, plateTop, front };
}

export function buildDecks(): Decks {
  const group = new THREE.Group();
  group.name = 'decks';
  const etchMat = machined('#b9c2d0');
  const { ward, station, lost, lab } = PLACES_PLAN;

  // ---- What the lab sees: the route etched, scan pips at the ward and the lab ----
  const labDeck = deck('WHAT THE LAB SEES');
  const lt = labDeck.plateTop;
  lt.add(etch(ward, station, etchMat), etch(station, lost, etchMat), etch(station, lab, etchMat, true));
  for (const p of [ward, station, lost, lab]) lt.add(ringAt(p, 0.42, etchMat));
  const pips: Uniform[] = [];
  const pipAt = (p: THREE.Vector2) => {
    const level = uniform(0.12);
    const pip = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.2, 0.06, 32), emitterMaterial('#ffb454', level));
    pip.position.set(p.x, 0.04, p.y);
    lt.add(pip);
    pips.push(level);
  };
  pipAt(ward);
  pipAt(lab);

  // ---- What Tracer infers: the plate the glow lifts onto, its route etched faintly ----
  const tracerDeck = deck('WHAT TRACER INFERS');
  const tt = tracerDeck.plateTop;
  const faint = machined('#6d7686');
  tt.add(etch(ward, station, faint), etch(station, lost, faint), etch(station, lab, faint, true));
  for (const p of [ward, station, lost, lab]) tt.add(ringAt(p, 0.55, faint));

  group.add(labDeck.group, tracerDeck.group);
  const truth = new THREE.Object3D();
  // On the bench's front-left corner, the bottom deck, clear of the carrier's own truth label.
  truth.position.set(-6.8, 0.25, 3.3);
  group.add(truth);
  return {
    group, lab: labDeck.group, tracer: tracerDeck.group,
    anchors: { truth, lab: labDeck.front, tracer: tracerDeck.front },
    update(explode, scans) {
      const k = explode * explode * (3 - 2 * explode);
      labDeck.group.position.y = DECK2_Y * k;
      tracerDeck.group.position.y = DECK3_Y * k;
      group.visible = explode > 0.02;
      pips[0].value = 2.6;
      pips[1].value = scans.received ? 2.6 : 0.12;
    },
  };
}
