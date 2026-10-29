// The pneumatic tube bench: a lacquered console with the stopwatch, two screens, two knobs and the
// truth switch; a breadboard on top carrying the ward's send station, the glass tube run, the
// diverter over the station's receiver, the branch to the lost bin, the lab's receiving desk with its
// scanner and scan log, and the alert beacon. Built from the kit; main.ts feeds it the display state.

import * as THREE from 'three/webgpu';
import { uniform } from 'three/tsl';
import {
  anodized, benchTop, brass, darkSteel, emissiveStrip, emitterMaterial, lacquer, machined, organic, rubber, thinGlass,
} from '../kit/materials';
import { bolt, breadboard, lathe, lightStrip, lowPoly, makeRng, mergeStatic, roundedBox } from '../kit/geometry';
import { DiegeticScreen } from '../kit/screens';
import { paperTwin, tubeGlass } from './glass';
import { knob, plate, stopwatch, toggleSwitch, type Knob, type Stopwatch, type ToggleSwitch } from './instruments';
import {
  BEACON, branchPath, BRANCH_DIR, BRANCH_Y, DESK, DIVERTER, LAB_TRAY, LOST_BIN, RECEIVER, RUN_Y, SCANNER, SLEEVE, TUBE_R, TUBE_Z, tubePath,
} from './layout';
import { PLOT } from './screen-draw';

/** The console face's instrument band. */
export const BAND_Y = -2.45;
const FACE_Z = 4.1;

type Uniform = THREE.UniformNode<'float', number>;

export interface Apparatus {
  group: THREE.Group;
  console: THREE.Group;
  bench: THREE.Group;
  stopwatch: Stopwatch;
  alertKnob: Knob;
  windowKnob: Knob;
  truthSwitch: ToggleSwitch;
  chart: DiegeticScreen;
  bars: DiegeticScreen;
  log: DiegeticScreen;
  /** Invisible grip over the chart's alert line, kept on the line. */
  lineHandle: THREE.Mesh;
  /** Amber strip of the alert beacon (HDR level) and the scanner's scan line. */
  beaconLevel: Uniform;
  scanLevel: Uniform;
  anchors: { beacon: THREE.Object3D; scanner: THREE.Object3D; screens: THREE.Object3D; stopwatch: THREE.Object3D };
  /** The lit glass meshes, and their unlit paper twins for Studio. */
  glass: THREE.Mesh[];
  paper: THREE.Mesh[];
  /** Put the line handle on the chart's alert line. */
  placeLineHandle(alertLine: number): void;
}

const shadows = (g: THREE.Object3D, except: THREE.Object3D[] = []) => {
  g.traverse((o) => {
    if ((o as THREE.Mesh).isMesh && !except.includes(o)) { o.castShadow = true; o.receiveShadow = true; }
  });
};

/** A ring around an axis: a lathe band (inner r0, outer r1, half height h) turned to point along `axis`. */
function collar(r0: number, r1: number, h: number, axis: THREE.Vector3, mat: THREE.Material): THREE.Mesh {
  const geo = lathe([[r0, -h], [r1 - 0.02, -h], [r1, -h + 0.02], [r1, h - 0.02], [r1 - 0.02, h], [r0, h], [r0, -h]], 48);
  const m = new THREE.Mesh(geo, mat);
  m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), axis.clone().normalize());
  return m;
}

/** Inner skins draw before outer ones, whose edge band writes depth (glass.ts) and would hide them. */
const skinOrder = (m: THREE.Mesh) => ((m.material as THREE.Material).side === THREE.BackSide ? 19 : 20);

/** An open glass cylinder (inner and outer skins, so its rims read from every side). */
function glassCylinder(r: number, h: number): THREE.Group {
  const g = new THREE.Group();
  const outer = new THREE.Mesh(new THREE.CylinderGeometry(r, r, h, 64, 1, true), tubeGlass({ face: 0.04, edge: 0.5 }));
  const inner = new THREE.Mesh(new THREE.CylinderGeometry(r - 0.025, r - 0.025, h, 64, 1, true), tubeGlass({ face: 0.02, edge: 0.28, side: THREE.BackSide }));
  for (const m of [outer, inner]) {
    m.userData.occluder = false;
    m.renderOrder = skinOrder(m);
  }
  g.add(outer, inner);
  return g;
}

function screenWithBezel(group: THREE.Group, w: number, h: number, px: number, x: number): DiegeticScreen {
  const s = new DiegeticScreen(w, h, px, { gain: 1.05 });
  s.mesh.position.set(x, BAND_Y, FACE_Z + 0.06);
  const bezel = new THREE.Mesh(roundedBox(w + 0.45, h + 0.45, 0.16, 0.14, 3), anodized('#0b0d12'));
  bezel.position.set(x, BAND_Y, FACE_Z - 0.04);
  bezel.castShadow = bezel.receiveShadow = true;
  group.add(bezel, s.mesh);
  return s;
}

export function buildApparatus(): Apparatus {
  const group = new THREE.Group();
  group.name = 'tube-bench';

  // ---------------- console ----------------
  const consoleGroup = new THREE.Group();
  consoleGroup.name = 'console';
  const body = new THREE.Mesh(roundedBox(19.6, 8.4, 8.0, 0.32, 4), lacquer('#1a1e26'));
  body.position.set(0, -4.48, 0);
  const lip = new THREE.Mesh(roundedBox(19.9, 0.28, 8.3, 0.1, 3), lacquer('#14171d'));
  lip.position.set(0, -0.42, 0);
  const face = new THREE.Mesh(roundedBox(18.4, 5.2, 0.28, 0.14, 3), lacquer('#101318'));
  face.position.set(0, -3.55, 3.96);
  const strip = new THREE.Mesh(lightStrip({ length: 18.6, thickness: 0.04, depth: 0.04 }), emissiveStrip('#ffb15a', 1.1));
  strip.position.set(0, -0.62, 4.1);
  consoleGroup.add(body, lip, face, strip);
  for (const x of [-8.85, 8.85]) {
    for (const y of [-1.25, -5.85]) {
      const b = new THREE.Mesh(bolt({ radius: 0.12, height: 0.06 }).rotateX(Math.PI / 2), brass());
      b.position.set(x, y, FACE_Z);
      consoleGroup.add(b);
    }
  }
  for (const x of [-9.0, 9.0]) {
    for (const z of [-3.4, 3.4]) {
      const foot = new THREE.Mesh(new THREE.CylinderGeometry(0.42, 0.5, 0.35, 24), rubber('#101114'));
      foot.position.set(x, -8.85, z);
      consoleGroup.add(foot);
    }
  }
  // The back, finished for the camera that circles the room: a vented service panel, a cable gland
  // with the mains cable, a brass plate and four bolts.
  const backPanel = new THREE.Mesh(roundedBox(17.6, 5.6, 0.2, 0.1, 3), lacquer('#101318'));
  backPanel.position.set(0, -4.2, -4.02);
  const slots: THREE.Mesh[] = [];
  for (const x of [-4.6, 4.6]) {
    for (let i = 0; i < 6; i++) {
      const s = new THREE.Mesh(roundedBox(3.4, 0.16, 0.08, 0.03, 1), rubber('#07080b'));
      s.position.set(x, -2.3 - i * 0.42, -4.14);
      slots.push(s);
    }
  }
  const gland = new THREE.Mesh(new THREE.CylinderGeometry(0.34, 0.34, 0.5, 16).rotateX(Math.PI / 2), brass());
  gland.position.set(6.6, -6.3, -4.3);
  const mains = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.12, 2.6, 8), rubber('#15171b'));
  mains.position.set(6.6, -7.6, -4.5);
  const backPlate = plate('TRACER · SERVICE', { width: 2.6, height: 0.5, brassy: true });
  backPlate.position.set(-6.2, -6.4, -4.13);
  backPlate.rotation.y = Math.PI;
  consoleGroup.add(backPanel, mergeStatic(slots, rubber('#07080b')), gland, mains, backPlate);
  for (const x of [-8.3, 8.3]) {
    for (const y of [-1.85, -6.55]) {
      const b = new THREE.Mesh(bolt({ radius: 0.12, height: 0.06 }).rotateX(-Math.PI / 2), brass());
      b.position.set(x, y, -4.14);
      consoleGroup.add(b);
    }
  }
  shadows(consoleGroup, [strip]);

  // Instruments on the face, one band under the bench lip.
  const watch = stopwatch(1.15);
  watch.group.position.set(-7.05, BAND_Y, FACE_Z);
  const chart = screenWithBezel(consoleGroup, 6.2, 2.5, 1536, -2.4);
  const bars = screenWithBezel(consoleGroup, 3.7, 2.5, 960, 2.95);
  const alertKnob = knob(0.55);
  alertKnob.group.position.set(6.1, BAND_Y + 0.25, FACE_Z);
  const windowKnob = knob(0.55);
  windowKnob.group.position.set(8.1, BAND_Y + 0.25, FACE_Z);
  consoleGroup.add(watch.group, alertKnob.group, windowKnob.group);
  const plates: [string, number, number, number][] = [
    ['SILENCE', -7.05, -4.45, 1.5],
    ['ALERT LINE', 6.1, -3.4, 1.5],
    ['WINDOW', 8.1, -3.4, 1.5],
  ];
  for (const [text, x, y, w] of plates) {
    const p = plate(text, { width: w, height: 0.3 });
    p.position.set(x, y, FACE_Z + 0.02);
    consoleGroup.add(p);
  }
  // The grip over the chart's alert line: invisible, but raycasts find it in front of the chart.
  const lineHandle = new THREE.Mesh(
    new THREE.PlaneGeometry(6.2 * (PLOT.x1 - PLOT.x0) + 0.3, 0.34),
    new THREE.MeshBasicNodeMaterial({ transparent: true, opacity: 0, depthWrite: false, colorWrite: false }),
  );
  lineHandle.userData.occluder = false;
  lineHandle.position.set(chart.mesh.position.x + 6.2 * ((PLOT.x0 + PLOT.x1) / 2 - 0.5), BAND_Y, FACE_Z + 0.09);
  consoleGroup.add(lineHandle);
  group.add(consoleGroup);

  // ---------------- bench: breadboard with a planar reflection ----------------
  const bench = new THREE.Group();
  bench.name = 'bench';
  const top = benchTop({ reflect: true });
  bench.add(breadboard({ width: 19.4, depth: 7.4, thickness: 0.26, plate: top.material, hole: rubber('#07080a') }));
  if (top.reflectorTarget) {
    top.reflectorTarget.position.y = 0.001;
    bench.add(top.reflectorTarget);
  }
  group.add(bench);

  const parts = new THREE.Group();
  parts.name = 'bench-parts';
  group.add(parts);
  const glassParts: THREE.Object3D[] = [];

  // ---------------- ward send station ----------------
  const ward = new THREE.Group();
  const wardBase = new THREE.Mesh(roundedBox(2.5, 0.42, 2.1, 0.08, 3), lacquer('#1c2028'));
  wardBase.position.set(-7.6, 0.21, 0.12);
  const tower = new THREE.Mesh(roundedBox(1.5, 4.0, 0.95, 0.1, 3), lacquer('#20242d'));
  tower.position.set(-7.78, 0.42 + 2.0, -0.62);
  const towerCap = new THREE.Mesh(roundedBox(1.66, 0.18, 1.1, 0.06, 2), lacquer('#14171d'));
  towerCap.position.set(-7.78, 4.5, -0.62);
  const sleeve = glassCylinder(SLEEVE.r, SLEEVE.top - SLEEVE.bottom);
  sleeve.position.set(SLEEVE.x, (SLEEVE.top + SLEEVE.bottom) / 2, SLEEVE.z);
  glassParts.push(sleeve);
  const up = new THREE.Vector3(0, 1, 0);
  const sleeveFoot = collar(SLEEVE.r - 0.04, SLEEVE.r + 0.1, 0.08, up, brass());
  sleeveFoot.position.set(SLEEVE.x, SLEEVE.bottom + 0.06, SLEEVE.z);
  const sleeveCup = new THREE.Mesh(new THREE.CylinderGeometry(SLEEVE.r - 0.03, SLEEVE.r - 0.03, 0.06, 48), rubber('#121318'));
  sleeveCup.position.set(SLEEVE.x, SLEEVE.bottom + 0.03, SLEEVE.z);
  const sleeveTop = collar(TUBE_R - 0.02, SLEEVE.r + 0.06, 0.1, up, brass());
  sleeveTop.position.set(SLEEVE.x, SLEEVE.top, SLEEVE.z);
  // Control panel on the tower beside the rising tube: a status lamp, the send button, the plate.
  const panel = new THREE.Mesh(roundedBox(0.62, 1.3, 0.08, 0.04, 2), lacquer('#101318'));
  panel.position.set(-8.18, 3.15, -0.12);
  const lamp = new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.075, 0.05, 20).rotateX(Math.PI / 2), emitterMaterial('#ffb454', 1.6));
  lamp.position.set(-8.18, 3.55, -0.06);
  const button = new THREE.Mesh(lathe([[0, 0], [0.15, 0], [0.15, 0.08], [0.12, 0.12], [0, 0.12]], 32).rotateX(Math.PI / 2), brass());
  button.position.set(-8.18, 3.1, -0.07);
  const wardPlate = plate('WARD · SEND', { width: 1.3, height: 0.26, brassy: true });
  wardPlate.position.set(-7.6, 0.24, 1.18);
  wardPlate.rotation.x = -0.05;
  ward.add(wardBase, tower, towerCap, sleeve, sleeveFoot, sleeveCup, sleeveTop, panel, lamp, button, wardPlate);
  // A waiting carrier rack on the tower's side: two empty cradles say what the sleeve is for.
  for (const z of [-0.3, -0.85]) {
    const cradle = collar(0.25, 0.32, 0.05, up, darkSteel('#4d525b'));
    cradle.position.set(-8.75, 1.4, z);
    ward.add(cradle);
  }
  parts.add(ward);

  // ---------------- the glass tube run ----------------
  const tubeGeo = new THREE.TubeGeometry(tubePath(), 260, TUBE_R, 36, false);
  const tube = new THREE.Mesh(tubeGeo, tubeGlass());
  const tubeIn = new THREE.Mesh(new THREE.TubeGeometry(tubePath(), 260, TUBE_R - 0.03, 36, false), tubeGlass({ face: 0.02, edge: 0.3, side: THREE.BackSide }));
  for (const m of [tube, tubeIn]) {
    m.userData.occluder = false;
    m.renderOrder = skinOrder(m);
  }
  glassParts.push(tube, tubeIn);
  parts.add(tube, tubeIn);
  // Brass couplings join the glass sections every unit and a half, bends included: they give the run a
  // readable structure from every side, even where the glass itself is faint.
  const clampXs = [-5.1, -2.5, 0.0];
  const runPath = tubePath();
  const runLength = runPath.getLength();
  const joint = new THREE.Vector3();
  const jointDir = new THREE.Vector3();
  for (let s = 0.55; s < runLength - 0.35; s += 1.5) {
    runPath.getPointAt(s / runLength, joint);
    if (Math.abs(joint.y - RUN_Y) < 0.01 && clampXs.some((x) => Math.abs(joint.x - x) < 0.45)) continue;
    runPath.getTangentAt(s / runLength, jointDir);
    const ring = collar(TUBE_R - 0.012, TUBE_R + 0.045, 0.045, jointDir, brass());
    ring.position.copy(joint);
    parts.add(ring);
  }
  const branchCurve = branchPath();
  for (const k of [0.3, 0.62]) {
    const ring = collar(TUBE_R - 0.012, TUBE_R + 0.045, 0.045, branchCurve.getTangentAt(k), brass());
    ring.position.copy(branchCurve.getPointAt(k));
    parts.add(ring);
  }
  // Brass clamps on slim posts carry the run; a coupling where it meets the diverter.
  const xAxis = new THREE.Vector3(1, 0, 0);
  for (const x of clampXs) {
    const clamp = collar(TUBE_R - 0.01, TUBE_R + 0.07, 0.09, xAxis, brass());
    clamp.position.set(x, RUN_Y, TUBE_Z);
    const lug = new THREE.Mesh(roundedBox(0.16, 0.22, 0.2, 0.03, 1), machined('#8d939c'));
    lug.position.set(x, RUN_Y - TUBE_R - 0.13, TUBE_Z);
    const postH = RUN_Y - TUBE_R - 0.22;
    const post = new THREE.Mesh(new THREE.CylinderGeometry(0.065, 0.065, postH, 16), darkSteel('#4d525b'));
    post.position.set(x, postH / 2, TUBE_Z);
    const foot = new THREE.Mesh(lathe([[0, 0], [0.24, 0], [0.24, 0.05], [0.18, 0.1], [0, 0.1]], 32), brass());
    foot.position.set(x, 0, TUBE_Z);
    parts.add(clamp, lug, post, foot);
  }

  // ---------------- diverter over the station's receiver ----------------
  const div = new THREE.Group();
  const drum = new THREE.Mesh(lathe([
    [0, DIVERTER.bottom], [DIVERTER.r - 0.05, DIVERTER.bottom], [DIVERTER.r, DIVERTER.bottom + 0.05],
    [DIVERTER.r, DIVERTER.top - 0.05], [DIVERTER.r - 0.05, DIVERTER.top], [0, DIVERTER.top],
  ], 64), lacquer('#1b1f27'));
  drum.position.set(DIVERTER.x, 0, DIVERTER.z);
  for (const y of [DIVERTER.bottom + 0.16, DIVERTER.top - 0.16]) {
    const band = new THREE.Mesh(new THREE.TorusGeometry(DIVERTER.r + 0.005, 0.028, 10, 64).rotateX(Math.PI / 2), brass());
    band.position.set(DIVERTER.x, y, DIVERTER.z);
    div.add(band);
  }
  const inlet = collar(TUBE_R - 0.02, TUBE_R + 0.08, 0.08, up, brass());
  inlet.position.set(DIVERTER.x, DIVERTER.top + 0.04, DIVERTER.z);
  const outlet = collar(TUBE_R - 0.02, RECEIVER.r + 0.04, 0.06, up, brass());
  outlet.position.set(DIVERTER.x, RECEIVER.top + 0.04, DIVERTER.z);
  const side = collar(TUBE_R - 0.02, TUBE_R + 0.08, 0.1, BRANCH_DIR, brass());
  side.position.set(DIVERTER.x, BRANCH_Y, DIVERTER.z).addScaledVector(BRANCH_DIR, DIVERTER.r + 0.04);
  // The flap that sends a carrier down or aside, shown as an amber route lamp on the drum.
  const route = new THREE.Mesh(roundedBox(0.16, 0.06, 0.03, 0.01, 1), emitterMaterial('#ffb454', 1.2));
  route.position.set(DIVERTER.x, (DIVERTER.top + DIVERTER.bottom) / 2, DIVERTER.z + DIVERTER.r + 0.01);
  div.add(drum, inlet, outlet, side, route);
  const receiver = glassCylinder(RECEIVER.r, RECEIVER.top - RECEIVER.bottom);
  receiver.position.set(RECEIVER.x, (RECEIVER.top + RECEIVER.bottom) / 2, RECEIVER.z);
  glassParts.push(receiver);
  const recBase = new THREE.Mesh(lathe([[0, 0], [RECEIVER.r + 0.22, 0], [RECEIVER.r + 0.22, RECEIVER.bottom - 0.08], [RECEIVER.r + 0.14, RECEIVER.bottom], [0, RECEIVER.bottom]], 64), lacquer('#1c2028'));
  recBase.position.set(RECEIVER.x, 0, RECEIVER.z);
  const recRing = collar(RECEIVER.r - 0.03, RECEIVER.r + 0.08, 0.06, up, brass());
  recRing.position.set(RECEIVER.x, RECEIVER.bottom + 0.04, RECEIVER.z);
  const bumper = new THREE.Mesh(new THREE.CylinderGeometry(RECEIVER.r - 0.03, RECEIVER.r - 0.03, 0.05, 48), rubber('#121318'));
  bumper.position.set(RECEIVER.x, RECEIVER.bottom + 0.025, RECEIVER.z);
  const stationPlate = plate('STATION', { width: 1.0, height: 0.22, brassy: true });
  stationPlate.position.set(RECEIVER.x, 0.22, RECEIVER.z + RECEIVER.r + 0.17);
  stationPlate.rotation.x = -0.18;
  div.add(receiver, recBase, recRing, bumper, stationPlate);
  parts.add(div);

  // ---------------- the branch and the lost bin ----------------
  const branch = new THREE.Mesh(new THREE.TubeGeometry(branchPath(), 120, TUBE_R, 32, false), tubeGlass());
  const branchIn = new THREE.Mesh(new THREE.TubeGeometry(branchPath(), 120, TUBE_R - 0.03, 32, false), tubeGlass({ face: 0.02, edge: 0.3, side: THREE.BackSide }));
  for (const m of [branch, branchIn]) {
    m.userData.occluder = false;
    m.renderOrder = skinOrder(m);
  }
  glassParts.push(branch, branchIn);
  parts.add(branch, branchIn);
  const bin = new THREE.Group();
  const binBase = new THREE.Mesh(lathe([[0, 0], [LOST_BIN.r + 0.22, 0], [LOST_BIN.r + 0.22, LOST_BIN.bottom - 0.08], [LOST_BIN.r + 0.14, LOST_BIN.bottom], [0, LOST_BIN.bottom]], 64), lacquer('#1c2028'));
  binBase.position.set(LOST_BIN.x, 0, LOST_BIN.z);
  const binGlass = glassCylinder(LOST_BIN.r, LOST_BIN.top - LOST_BIN.bottom);
  binGlass.position.set(LOST_BIN.x, (LOST_BIN.top + LOST_BIN.bottom) / 2, LOST_BIN.z);
  glassParts.push(binGlass);
  const lid = new THREE.Mesh(lathe([[TUBE_R + 0.06, LOST_BIN.top], [LOST_BIN.r + 0.1, LOST_BIN.top], [LOST_BIN.r + 0.1, LOST_BIN.top + 0.16], [LOST_BIN.r - 0.02, LOST_BIN.top + 0.24], [TUBE_R + 0.06, LOST_BIN.top + 0.24]], 64), lacquer('#1c2028'));
  lid.position.set(LOST_BIN.x, 0, LOST_BIN.z);
  const binInlet = collar(TUBE_R - 0.02, TUBE_R + 0.08, 0.1, up, brass());
  binInlet.position.set(LOST_BIN.x, LOST_BIN.top + 0.3, LOST_BIN.z);
  const binFloor = new THREE.Mesh(new THREE.CylinderGeometry(LOST_BIN.r - 0.03, LOST_BIN.r - 0.03, 0.05, 48), rubber('#121318'));
  binFloor.position.set(LOST_BIN.x, LOST_BIN.bottom + 0.025, LOST_BIN.z);
  const lostPlate = plate('LOST', { width: 0.8, height: 0.22, brassy: true });
  lostPlate.position.set(LOST_BIN.x, 0.22, LOST_BIN.z + LOST_BIN.r + 0.17);
  lostPlate.rotation.x = -0.18;
  bin.add(binBase, binGlass, lid, binInlet, binFloor, lostPlate);
  parts.add(bin);

  // ---------------- the lab's receiving desk ----------------
  const desk = new THREE.Group();
  const deskBody = new THREE.Mesh(roundedBox(DESK.w, DESK.h, DESK.d, 0.1, 3), lacquer('#1c2028'));
  deskBody.position.set(DESK.x, DESK.h / 2, DESK.z);
  const deskTop = new THREE.Mesh(roundedBox(DESK.w + 0.08, 0.06, DESK.d + 0.08, 0.025, 2), machined('#3a3f48'));
  deskTop.position.set(DESK.x, DESK.h + 0.01, DESK.z);
  // The lab's tray: four glass walls on a dark floor with a brass rim.
  const tray = new THREE.Group();
  const trayFloor = new THREE.Mesh(roundedBox(LAB_TRAY.w, 0.05, LAB_TRAY.d, 0.02, 2), rubber('#121318'));
  trayFloor.position.y = 0.025;
  tray.add(trayFloor);
  const wallMat = thinGlass({ opacity: 0.16 });
  for (const [w, d, x, z] of [[LAB_TRAY.w, 0.03, 0, LAB_TRAY.d / 2], [LAB_TRAY.w, 0.03, 0, -LAB_TRAY.d / 2], [0.03, LAB_TRAY.d, LAB_TRAY.w / 2, 0], [0.03, LAB_TRAY.d, -LAB_TRAY.w / 2, 0]] as const) {
    const wall = new THREE.Mesh(new THREE.BoxGeometry(w, LAB_TRAY.h, d), wallMat);
    wall.position.set(x, LAB_TRAY.h / 2, z);
    wall.userData.occluder = false;
    wall.renderOrder = 20;
    tray.add(wall);
    glassParts.push(wall);
  }
  const rimBars: THREE.Mesh[] = [];
  for (const [w, d, x, z] of [[LAB_TRAY.w + 0.06, 0.05, 0, LAB_TRAY.d / 2], [LAB_TRAY.w + 0.06, 0.05, 0, -LAB_TRAY.d / 2], [0.05, LAB_TRAY.d, LAB_TRAY.w / 2, 0], [0.05, LAB_TRAY.d, -LAB_TRAY.w / 2, 0]] as const) {
    const b = new THREE.Mesh(roundedBox(w, 0.05, d, 0.015, 1), brass());
    b.position.set(x, LAB_TRAY.h, z);
    rimBars.push(b);
  }
  tray.add(...rimBars);
  tray.position.set(LAB_TRAY.x, LAB_TRAY.y, LAB_TRAY.z);
  // The receiving scanner: a presentation scanner leaning back, its window crossed by a scan line.
  const scanner = new THREE.Group();
  const head = new THREE.Mesh(roundedBox(0.95, 1.1, 0.72, 0.12, 3), lacquer('#15181e'));
  head.position.y = 0.55;
  const windowGlass = new THREE.Mesh(roundedBox(0.7, 0.58, 0.04, 0.04, 2), anodized('#05070b'));
  windowGlass.position.set(0, 0.66, 0.36);
  const scanLevel = uniform(0.55);
  const scanLine = new THREE.Mesh(lightStrip({ length: 0.6, thickness: 0.022, depth: 0.02 }), emitterMaterial('#ffb454', scanLevel));
  scanLine.position.set(0, 0.66, 0.39);
  const neck = new THREE.Mesh(roundedBox(0.5, 0.12, 0.5, 0.04, 2), machined('#3a3f48'));
  neck.position.y = 0.06;
  scanner.add(neck, head, windowGlass, scanLine);
  scanner.position.set(SCANNER.x, DESK.h + 0.02, SCANNER.z);
  scanner.rotation.set(-0.22, -0.32, 0);
  // The scan log on a small stand behind the tray: everything the lab sees of this specimen.
  const log = new DiegeticScreen(1.55, 0.95, 512, { gain: 1.05 });
  const logBezel = new THREE.Mesh(roundedBox(1.75, 1.15, 0.12, 0.08, 2), anodized('#0b0d12'));
  const logStand = new THREE.Mesh(roundedBox(0.16, 0.7, 0.16, 0.04, 1), darkSteel('#4d525b'));
  logStand.position.set(0, -0.85, -0.1);
  log.mesh.position.z = 0.07;
  const logGroup = new THREE.Group();
  logGroup.add(logBezel, log.mesh, logStand);
  logGroup.position.set(6.8, DESK.h + 1.22, -0.55);
  logGroup.rotation.x = -0.12;
  const labPlate = plate('LAB · RECEIVING', { width: 1.6, height: 0.24, brassy: true });
  labPlate.position.set(DESK.x - 0.4, 0.38, DESK.z + DESK.d / 2 + 0.03);
  desk.add(deskBody, deskTop, tray, scanner, logGroup, labPlate);
  parts.add(desk);

  // ---------------- the truth switch ----------------
  // On the bench in front of the lost bin, where the first view sees it: a brass bat on a lacquered box,
  // its face turned up; up shows the real carrier, down hides it.
  const truthSwitch = toggleSwitch();
  const veil = new THREE.Group();
  const veilBox = new THREE.Mesh(roundedBox(1.05, 0.42, 0.8, 0.08, 3), lacquer('#1c2028'));
  veilBox.position.y = 0.21;
  truthSwitch.group.rotation.x = -Math.PI / 2;
  truthSwitch.group.position.y = 0.42;
  const veilPlate = plate('TRUTH', { width: 0.8, height: 0.2, brassy: true });
  veilPlate.position.set(0, 0.21, 0.41);
  veil.add(veilBox, truthSwitch.group, veilPlate);
  veil.position.set(4.55, 0, 2.75);
  parts.add(veil);

  // ---------------- the alert beacon ----------------
  const beacon = new THREE.Group();
  const postH = BEACON.top - 1.9;
  const beaconPost = new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.075, postH, 16), darkSteel('#4d525b'));
  beaconPost.position.y = postH / 2;
  const beaconFoot = new THREE.Mesh(lathe([[0, 0], [0.3, 0], [0.3, 0.06], [0.22, 0.12], [0, 0.12]], 32), brass());
  const headBox = new THREE.Mesh(roundedBox(0.42, 1.95, 0.36, 0.08, 3), lacquer('#14171d'));
  headBox.position.y = BEACON.top - 0.98;
  const lens = new THREE.Mesh(roundedBox(0.22, 1.68, 0.05, 0.04, 2), lacquer('#3a2812'));
  lens.position.set(0, BEACON.top - 0.98, 0.18);
  const beaconLevel = uniform(0);
  const beaconStrip = new THREE.Mesh(lightStrip({ length: 1.56, thickness: 0.09, depth: 0.05 }).rotateZ(Math.PI / 2), emitterMaterial('#ffb454', beaconLevel));
  beaconStrip.position.set(0, BEACON.top - 0.98, 0.21);
  const beaconCap = new THREE.Mesh(roundedBox(0.5, 0.1, 0.44, 0.04, 2), brass());
  beaconCap.position.y = BEACON.top + 0.02;
  beacon.add(beaconPost, beaconFoot, headBox, lens, beaconStrip, beaconCap);
  beacon.position.set(BEACON.x, 0, BEACON.z);
  parts.add(beacon);

  // A low-poly succulent in a faceted pot on the bench's front left: the toy beside the precision.
  const rng = makeRng(17);
  const plant = new THREE.Group();
  plant.add(new THREE.Mesh(lowPoly.pot({ radius: 0.4, height: 0.58, sides: 7 }), organic('#e9e0cf', 0.75)));
  const leaves = organic('#6f9a52', 0.7);
  for (let i = 0; i < 2; i++) {
    const f = new THREE.Mesh(lowPoly.foliage(rng, { size: 0.52 - i * 0.13, leaves: 6 }), leaves);
    f.position.y = 0.55 + i * 0.18;
    f.rotation.y = rng() * Math.PI;
    plant.add(f);
  }
  plant.position.set(-4.3, 0.02, 2.75);
  parts.add(plant);

  shadows(parts, [...glassParts, lamp, route, scanLine, beaconStrip]);
  // Every glass mesh gets an unlit paper twin beside it: the lit glass shows in the dark looks, the
  // twin on Studio's paper (stage sets 'glass' and 'paper-glass').
  const glass: THREE.Mesh[] = [];
  for (const g of glassParts) g.traverse((o) => { o.castShadow = false; o.receiveShadow = false; if ((o as THREE.Mesh).isMesh) glass.push(o as THREE.Mesh); });
  const paper = glass.map((m) => {
    const twin = paperTwin(m);
    m.parent?.add(twin);
    return twin;
  });

  const anchors = {
    beacon: new THREE.Object3D(),
    scanner: new THREE.Object3D(),
    screens: new THREE.Object3D(),
    stopwatch: new THREE.Object3D(),
  };
  anchors.beacon.position.set(BEACON.x, BEACON.top + 0.45, BEACON.z);
  anchors.scanner.position.set(SCANNER.x, DESK.h + 1.4, SCANNER.z);
  anchors.screens.position.set(0.3, BAND_Y, FACE_Z + 0.1);
  anchors.stopwatch.position.set(-7.05, BAND_Y, FACE_Z + 0.2);
  group.add(...Object.values(anchors));

  return {
    group, console: consoleGroup, bench, stopwatch: watch, alertKnob, windowKnob, truthSwitch, chart, bars, log, lineHandle,
    beaconLevel, scanLevel, anchors, glass, paper,
    placeLineHandle(alertLine) {
      const yFrac = PLOT.y0 + (1 - alertLine) * (PLOT.y1 - PLOT.y0);
      lineHandle.position.y = BAND_Y + (0.5 - yFrac) * 2.5;
    },
  };
}
