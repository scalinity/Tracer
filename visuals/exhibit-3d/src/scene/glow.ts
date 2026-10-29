// The phenomenon: Tracer's inference drawn as light. Probability is conserved, so the light is too:
// each place's glass vessel fills with light to the level of its probability (the ward's sleeve, the
// station's receiver, the lost bin in coral, the lab's tray once a receipt scan has located the
// specimen), and the share in transit glows inside the tube, each minute's cohort placed by its
// minutes in the tube at the leg's median speed. A coral trickle down the branch runs as fast as
// probability moves into lost. In the exploded view the light lifts off the hardware onto the top
// deck as a map: columns over each place and the route between them.

import * as THREE from 'three/webgpu';
import {
  abs, emissive, float, Fn, positionLocal, positionViewDirection, normalView, smoothstep, uniform, vec3, vec4,
} from 'three/tsl';
import { Ribbon } from '../kit/beams';
import { beam, house } from '../kit/materials';
import type { FateId } from '../model/fates';
import { FATES } from '../model/fates';
import type { TracerModel } from '../model/state';
import { MAX_T, TUBE_AGES } from '../model/tracer';
import { branchPath, LAB_TRAY, LOST_BIN, PLACES_PLAN, RECEIVER, RUN_Y, SLEEVE, TUBE_Z, tubePath } from './layout';

const CYAN = new THREE.Color('#7fe3ff');
const CORAL = new THREE.Color('#ff6b86');
/** HDR multiple of white for a vessel's light (its surface and rim run brighter). */
const LIQUID_GAIN = 1.45;
/** Smear brightness per unit of probability density along the tube (density per tube length). */
const SMEAR_GAIN = 0.95;
/** A cohort's spread along the tube, as a fraction of its length. */
const SMEAR_SIGMA = 0.07;
/** Branch trickle brightness per unit of probability moving into lost per minute. */
const FLOW_GAIN = 130;
const SMEAR_POINTS = 120;
const FLOW_POINTS = 64;
/** The top deck's height in the exploded view, and its columns' height at probability 1. */
export const DECK3_Y = 9.0;
const DECK_COLUMN = { r: 0.46, h: 2.8 };
/** Where each place's label stands on the bench: beside or above its vessel, clear of the tube. */
const LABEL_AT = {
  ward: new THREE.Vector3(SLEEVE.x - 1.1, SLEEVE.top - 0.4, SLEEVE.z),
  tube: new THREE.Vector3(-2.5, RUN_Y + 0.6, TUBE_Z),
  station: new THREE.Vector3(RECEIVER.x - 1.2, RECEIVER.top - 0.35, RECEIVER.z),
  lost: new THREE.Vector3(LOST_BIN.x + 0.3, LOST_BIN.top + 1.0, LOST_BIN.z),
  lab: new THREE.Vector3(LAB_TRAY.x - 0.2, LAB_TRAY.y + 1.15, LAB_TRAY.z),
};

type Uniform = THREE.UniformNode<'float', number>;

/** Unlit additive light with a bright surface and rim, written to the emissive target for bloom. */
function liquidMaterial(color: THREE.Color, level: Uniform): THREE.MeshBasicNodeMaterial {
  const mat = new THREE.MeshBasicNodeMaterial({
    transparent: false, depthWrite: true, blending: THREE.AdditiveBlending, fog: false, side: THREE.FrontSide,
  });
  const c = vec3(color.r, color.g, color.b);
  // positionLocal.y runs 0 (bottom) to 1 (surface) whatever the fill, since the column is scaled in y.
  // A see-through body, so a carrier inside stays visible, with a bright rim and a brighter surface.
  const surface = smoothstep(0.86, 1.0, positionLocal.y);
  const facing = abs(normalView.dot(positionViewDirection));
  const rim = float(1).sub(facing).pow(2.4);
  const body = float(0.2).add(rim.mul(0.95)).add(surface.mul(1.05));
  const rgb = c.mul(level).mul(body);
  mat.colorNode = Fn(() => {
    emissive.assign(rgb.mul(house.glow));
    return vec4(rgb.mul(house.beamGain), 1);
  })();
  return mat;
}

/** Darkens what lies behind a vessel's light in light looks (house.beamShade), so it reads on paper. */
function shadeMaterial(level: Uniform): THREE.MeshBasicNodeMaterial {
  const mat = new THREE.MeshBasicNodeMaterial({
    transparent: false, depthWrite: false, fog: false, side: THREE.FrontSide,
    blending: THREE.CustomBlending, blendSrc: THREE.ZeroFactor, blendDst: THREE.SrcColorFactor,
  });
  const dark = house.beamShade.mul(level.div(LIQUID_GAIN).min(1)).mul(0.85);
  mat.colorNode = Fn(() => {
    emissive.assign(vec3(1));
    return vec4(vec3(float(1).sub(dark)), 1);
  })();
  return mat;
}

/** Candela at a vessel full of light: the light in the glass lights the hardware around it (theatre). */
const VESSEL_LIGHT = 7;

interface Column {
  mesh: THREE.Mesh;
  shade: THREE.Mesh;
  /** Lights the parts around the vessel, as bright as the vessel is full. Never hidden: only its intensity moves. */
  light: THREE.PointLight;
  level: Uniform;
  /** Displayed fill, eased toward the model's probability. */
  fill: number;
  lab: { x: number; y: number; z: number; sx: number; sz: number; h: number };
  deck: { x: number; y: number; z: number; sx: number; sz: number; h: number };
}

export interface GlowDisplay {
  fate: FateId;
  silence: number;
  /** 0 assembled, 1 exploded. */
  explode: number;
}

export class Glow {
  readonly group = new THREE.Group();
  /** Where each place's light stands, for labels: ward, station, lost, lab, and the tube's middle. */
  readonly anchors = { ward: new THREE.Object3D(), tube: new THREE.Object3D(), station: new THREE.Object3D(), lost: new THREE.Object3D(), lab: new THREE.Object3D() };
  private columns: Record<'ward' | 'station' | 'lost' | 'lab', Column>;
  private smear: Ribbon;
  private flow: Ribbon;
  private tubePts: THREE.Vector3[];
  private deckTube: THREE.Vector3[];
  private branchPts: THREE.Vector3[];
  private deckBranch: THREE.Vector3[];
  private pts: THREE.Vector3[];
  private cols: THREE.Color[];
  private widths: number[];
  private fpts: THREE.Vector3[];
  private fcols: THREE.Color[];
  private density = new Float64Array(SMEAR_POINTS);
  private model: TracerModel;
  /** How far the vessels' light reaches the hardware in the current look (dimmed on Studio's paper). */
  lightScale = 1;

  constructor(model: TracerModel) {
    this.model = model;
    this.group.name = 'glow';
    const cyl = new THREE.CylinderGeometry(1, 1, 1, 48, 1, false).translate(0, 0.5, 0);
    const box = new THREE.BoxGeometry(1, 1, 1).translate(0, 0.5, 0);
    const deckAt = (p: THREE.Vector2) => ({ x: p.x, y: DECK3_Y, z: p.y, sx: DECK_COLUMN.r, sz: DECK_COLUMN.r, h: DECK_COLUMN.h });
    const column = (geo: THREE.BufferGeometry, color: THREE.Color, lab: Column['lab'], deck: Column['deck']): Column => {
      const level = uniform(LIQUID_GAIN);
      const mesh = new THREE.Mesh(geo, liquidMaterial(color, level));
      mesh.renderOrder = 10;
      mesh.userData.occluder = false;
      const shade = new THREE.Mesh(geo, shadeMaterial(level));
      shade.renderOrder = 9;
      shade.userData.occluder = false;
      const light = new THREE.PointLight(color, 0, 7, 2);
      light.castShadow = false;
      this.group.add(shade, mesh, light);
      return { mesh, shade, light, level, fill: 0, lab, deck };
    };
    this.columns = {
      ward: column(cyl, CYAN, { x: SLEEVE.x, y: SLEEVE.bottom + 0.04, z: SLEEVE.z, sx: SLEEVE.r - 0.07, sz: SLEEVE.r - 0.07, h: SLEEVE.top - SLEEVE.bottom - 0.14 }, deckAt(PLACES_PLAN.ward)),
      station: column(cyl, CYAN, { x: RECEIVER.x, y: RECEIVER.bottom + 0.04, z: RECEIVER.z, sx: RECEIVER.r - 0.07, sz: RECEIVER.r - 0.07, h: RECEIVER.top - RECEIVER.bottom - 0.12 }, deckAt(PLACES_PLAN.station)),
      lost: column(cyl, CORAL, { x: LOST_BIN.x, y: LOST_BIN.bottom + 0.04, z: LOST_BIN.z, sx: LOST_BIN.r - 0.07, sz: LOST_BIN.r - 0.07, h: LOST_BIN.top - LOST_BIN.bottom - 0.1 }, deckAt(PLACES_PLAN.lost)),
      lab: column(box, CYAN, { x: LAB_TRAY.x, y: LAB_TRAY.y + 0.04, z: LAB_TRAY.z, sx: LAB_TRAY.w - 0.12, sz: LAB_TRAY.d - 0.12, h: LAB_TRAY.h - 0.08 }, { ...deckAt(PLACES_PLAN.lab), sx: 0.82, sz: 0.82 }),
    };
    // The tube's smear and the branch's trickle, sampled evenly along their centerlines, with the
    // same number of points on the deck's flat map, so the light can lift and flatten in one move.
    this.tubePts = tubePath().getSpacedPoints(SMEAR_POINTS - 1);
    const w = PLACES_PLAN.ward;
    const s = PLACES_PLAN.station;
    this.deckTube = this.tubePts.map((_, i) => new THREE.Vector3(THREE.MathUtils.lerp(w.x, s.x, i / (SMEAR_POINTS - 1)), DECK3_Y + 0.06, THREE.MathUtils.lerp(w.y, s.y, i / (SMEAR_POINTS - 1))));
    this.branchPts = branchPath().getSpacedPoints(FLOW_POINTS - 1);
    const l = PLACES_PLAN.lost;
    this.deckBranch = this.branchPts.map((_, i) => new THREE.Vector3(THREE.MathUtils.lerp(s.x, l.x, i / (FLOW_POINTS - 1)), DECK3_Y + 0.06, THREE.MathUtils.lerp(s.y, l.y, i / (FLOW_POINTS - 1))));
    this.pts = this.tubePts.map((p) => p.clone());
    this.cols = this.tubePts.map(() => new THREE.Color());
    this.widths = this.tubePts.map(() => 0.2);
    this.fpts = this.branchPts.map((p) => p.clone());
    this.fcols = this.branchPts.map(() => new THREE.Color());
    this.smear = new Ribbon({ points: this.pts, width: 0.3, material: beam('#ffffff', 1, { core: 10, halo: 0.5 }), dynamic: true, shade: true });
    this.flow = new Ribbon({ points: this.fpts, width: 0.12, material: beam('#ffffff', 1, { core: 14, halo: 0.4 }), dynamic: true, shade: true, renderOrder: 11 });
    this.smear.mesh.userData.occluder = false;
    this.flow.mesh.userData.occluder = false;
    this.group.add(this.smear.mesh, this.flow.mesh, ...Object.values(this.anchors));
  }

  update(dt: number, d: GlowDisplay): void {
    const g = this.model.glow(d.fate, d.silence);
    const e = d.explode;
    const k = 1 - Math.exp(-dt * 9);
    const lerp = THREE.MathUtils.lerp;
    for (const [name, c] of Object.entries(this.columns) as [keyof Glow['columns'], Column][]) {
      c.fill += (g[name] - c.fill) * k;
      const f = Math.max(0, c.fill);
      const x = lerp(c.lab.x, c.deck.x, e);
      const y = lerp(c.lab.y, c.deck.y, e);
      const z = lerp(c.lab.z, c.deck.z, e);
      const h = lerp(c.lab.h, c.deck.h, e) * f;
      const visible = f > 0.002;
      c.mesh.visible = c.shade.visible = visible;
      c.mesh.position.set(x, y, z);
      c.mesh.scale.set(lerp(c.lab.sx, c.deck.sx, e), Math.max(1e-4, h), lerp(c.lab.sz, c.deck.sz, e));
      c.shade.position.copy(c.mesh.position);
      c.shade.scale.copy(c.mesh.scale).multiplyScalar(1.03);
      c.light.position.set(x, y + Math.max(h, 0.1) * 0.6, z);
      c.light.intensity = VESSEL_LIGHT * f * this.lightScale;
      // Labels hold still beside their vessels on the bench, and ride the columns' tops on the deck.
      const at = LABEL_AT[name];
      this.anchors[name].position.set(lerp(at.x, c.deck.x, e), lerp(at.y, c.deck.y + c.deck.h * f + 0.45, e), lerp(at.z, c.deck.z, e));
    }

    // ---- the tube: each cohort of minutes-in-tube, carried along continuously between minutes ----
    const t = Math.min(MAX_T, Math.max(0, d.silence));
    const fate = FATES[d.fate];
    const scanned = fate.received !== null && t >= fate.received;
    const i0 = Math.floor(t);
    const i1 = Math.min(MAX_T, i0 + 1);
    const kk = t - i0;
    const a0 = this.model.series[i0].tubeAges;
    const a1 = this.model.series[i1].tubeAges;
    const median = this.model.params.legs[1].median;
    const dens = this.density;
    dens.fill(0);
    const norm = 1 / (SMEAR_SIGMA * Math.sqrt(2 * Math.PI));
    const deposit = (mass: number, age: number) => {
      if (mass < 1e-6) return;
      const u = Math.min(1, Math.max(0, (age + 0.5) / median));
      for (let i = 0; i < SMEAR_POINTS; i++) {
        const du = i / (SMEAR_POINTS - 1) - u;
        dens[i] += mass * norm * Math.exp(-(du * du) / (2 * SMEAR_SIGMA * SMEAR_SIGMA));
      }
    };
    if (!scanned) {
      // A cohort aged a at minute i0 is aged a + 1 at i1: it moves while its mass crossfades.
      for (let a = -1; a < TUBE_AGES - 1; a++) deposit((a < 0 ? 0 : a0[a] * (1 - kk)) + a1[a + 1] * kk, a + kk);
    }
    for (let i = 0; i < SMEAR_POINTS; i++) {
      this.pts[i].lerpVectors(this.tubePts[i], this.deckTube[i], e);
      // A faint floor keeps the route legible on the deck's map without adding light to the tube.
      const v = SMEAR_GAIN * dens[i] + 0.04 * e;
      this.cols[i].copy(CYAN).multiplyScalar(v);
      // The smear fills more of the bore where more of the probability is.
      this.widths[i] = 0.16 + 0.34 * Math.min(1, Math.sqrt(dens[i] / 2.5));
    }
    const ends = Math.min(1, dens.reduce((m, x) => Math.max(m, x), 0) * 4 + e);
    this.smear.mesh.visible = ends > 0.01;
    this.smear.setPath(this.pts, this.widths, this.cols);
    this.anchors.tube.position.lerpVectors(LABEL_AT.tube, this.deckTube[SMEAR_POINTS >> 1].clone().setY(DECK3_Y + 0.5), e);

    // ---- the branch: probability moving into lost, per minute of silence ----
    const rate = scanned ? 0 : Math.max(0, this.model.series[i1].lost - this.model.series[i0].lost);
    const level = FLOW_GAIN * rate;
    // The pattern advances with the clock, not the wall clock: paused, it holds still.
    const phase = t * 0.9;
    for (let i = 0; i < FLOW_POINTS; i++) {
      this.fpts[i].lerpVectors(this.branchPts[i], this.deckBranch[i], e);
      const s = i / (FLOW_POINTS - 1);
      const dash = Math.pow(Math.max(0, Math.sin((s * 7 - phase) * Math.PI * 2)), 2);
      const fade = Math.min(1, s / 0.08, (1 - s) / 0.08);
      this.fcols[i].copy(CORAL).multiplyScalar((level * (0.25 + 0.75 * dash) + 0.03 * e) * fade);
    }
    this.flow.mesh.visible = level > 0.01 || e > 0.01;
    this.flow.setPath(this.fpts, 0.12, this.fcols);
  }

  /** Light a vessel holds now (the displayed fill), for tests of what is drawn. */
  fill(place: 'ward' | 'station' | 'lost' | 'lab'): number {
    return this.columns[place].fill;
  }
}
