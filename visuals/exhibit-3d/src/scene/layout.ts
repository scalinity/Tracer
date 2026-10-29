// Where everything stands on the bench. World units: 1 = 10 cm; the bench top is y = 0, the bench
// runs along x, +z faces the visitor. The tube run is schematic, not any hospital's system: a ward
// send station on the left, a glass tube over to a diverter above the station's receiver, a branch
// down to the lost bin, and the lab's receiving desk with its scanner on the right.

import * as THREE from 'three/webgpu';

export const TUBE_R = 0.34;
export const TUBE_Z = 0.55;

/** The ward's glass send sleeve, where a carrier stands until it is sent. */
export const SLEEVE = { x: -7.4, z: TUBE_Z, r: 0.5, bottom: 0.42, top: 2.7 };
/** The horizontal run's centerline height and the radius of its two bends. */
export const RUN_Y = 4.1;
const BEND = 0.7;
/** The diverter body: a drum the tube enters from above, with the branch leaving its side. */
export const DIVERTER = { x: 1.6, z: TUBE_Z, r: 0.5, bottom: 2.4, top: 3.3 };
/** The station's glass receiver under the diverter. */
export const RECEIVER = { x: DIVERTER.x, z: TUBE_Z, r: 0.6, bottom: 0.42, top: 2.28 };
/** The lost bin: a canister the branch drops into. */
export const LOST_BIN = { x: 4.85, z: -1.5, r: 0.62, bottom: 0.44, top: 2.08 };
/** The lab's glass tray on the receiving desk, where a scanned carrier lies. */
export const DESK = { x: 7.35, z: 0.2, w: 2.9, h: 0.72, d: 2.2 };
export const LAB_TRAY = { x: 6.95, z: 0.62, w: 1.62, h: 0.46, d: 0.92, y: DESK.h };
/** The receiving scanner, behind the tray. */
export const SCANNER = { x: 8.15, z: -0.28 };
/** The alert beacon on its post at the bench's back right corner. */
export const BEACON = { x: 9.0, z: -1.95, top: 5.0 };

/** A circular arc in a vertical plane z = const: center (cx, cy), radius r, angles in radians. */
class Arc extends THREE.Curve<THREE.Vector3> {
  constructor(private cx: number, private cy: number, private r: number, private a0: number, private a1: number, private z: number) {
    super();
  }
  getPoint(t: number, target = new THREE.Vector3()): THREE.Vector3 {
    const a = this.a0 + (this.a1 - this.a0) * t;
    return target.set(this.cx + this.r * Math.cos(a), this.cy + this.r * Math.sin(a), this.z);
  }
}

const v = (x: number, y: number, z = TUBE_Z) => new THREE.Vector3(x, y, z);

/** The tube's centerline from the top of the ward's sleeve to the top of the diverter. */
export function tubePath(): THREE.CurvePath<THREE.Vector3> {
  const path = new THREE.CurvePath<THREE.Vector3>();
  const x0 = SLEEVE.x;
  const x1 = DIVERTER.x;
  const yBend = RUN_Y - BEND;
  path.add(new THREE.LineCurve3(v(x0, SLEEVE.top), v(x0, yBend)));
  path.add(new Arc(x0 + BEND, yBend, BEND, Math.PI, Math.PI / 2, TUBE_Z));
  path.add(new THREE.LineCurve3(v(x0 + BEND, RUN_Y), v(x1 - BEND, RUN_Y)));
  path.add(new Arc(x1 - BEND, yBend, BEND, Math.PI / 2, 0, TUBE_Z));
  path.add(new THREE.LineCurve3(v(x1, yBend), v(x1, DIVERTER.top)));
  return path;
}

/** Where the branch leaves the diverter's side, toward the lost bin. */
export const BRANCH_DIR = new THREE.Vector3(LOST_BIN.x - DIVERTER.x, 0, LOST_BIN.z - DIVERTER.z).normalize();
export const BRANCH_Y = (DIVERTER.bottom + DIVERTER.top) / 2;

/** The branch's centerline from the diverter's side outlet down into the lost bin's lid. */
export function branchPath(): THREE.CubicBezierCurve3 {
  const start = v(DIVERTER.x, BRANCH_Y, DIVERTER.z).addScaledVector(BRANCH_DIR, DIVERTER.r + 0.12);
  const inlet = v(LOST_BIN.x, LOST_BIN.top + 0.32, LOST_BIN.z);
  return new THREE.CubicBezierCurve3(
    start,
    start.clone().addScaledVector(BRANCH_DIR, 1.3),
    inlet.clone().add(new THREE.Vector3(0, 1.15, 0)),
    inlet,
  );
}

/** A carrier's hand carry from the receiver up and over to the lab's tray. */
export function carryPath(): THREE.CubicBezierCurve3 {
  const from = v(RECEIVER.x, RECEIVER.bottom + 0.75, RECEIVER.z);
  const to = v(LAB_TRAY.x, LAB_TRAY.y + 0.33, LAB_TRAY.z);
  return new THREE.CubicBezierCurve3(from, from.clone().add(new THREE.Vector3(0.2, 2.8, 0.9)), to.clone().add(new THREE.Vector3(-0.8, 1.8, 0.6)), to);
}

/** Plan positions of the places (x, z), for the exploded decks' maps. */
export const PLACES_PLAN = {
  ward: new THREE.Vector2(SLEEVE.x, SLEEVE.z),
  station: new THREE.Vector2(RECEIVER.x, RECEIVER.z),
  lost: new THREE.Vector2(LOST_BIN.x, LOST_BIN.z),
  lab: new THREE.Vector2(LAB_TRAY.x, LAB_TRAY.z),
};
