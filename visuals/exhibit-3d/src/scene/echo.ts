// The Lab's background echo: the hospital's own tube system, two pneumatic lines crossing the dusk
// window on the back wall, dropping to a wall station at its left and rising into the ceiling at its
// right. Out of focus they read by silhouette against the bright glass: dark bands with brass
// couplings, in the room's dusk steel. Placed relative to the window anchor (its center).

import * as THREE from 'three/webgpu';
import { brass, darkSteel, lacquer } from '../kit/materials';
import { roundedBox } from '../kit/geometry';
import type { LabSet } from '../kit/sets';

const R = 0.62;

/** A pipe through `points` with rounded bends of radius `bend`, plus a coupling every few units. */
function pipe(points: THREE.Vector3[], bend: number, mat: THREE.Material, ring: THREE.Material, into: THREE.Group): void {
  const path = new THREE.CurvePath<THREE.Vector3>();
  let from = points[0].clone();
  for (let i = 1; i < points.length; i++) {
    const p = points[i];
    if (i === points.length - 1) {
      path.add(new THREE.LineCurve3(from, p.clone()));
      break;
    }
    const n = points[i + 1];
    const a = p.clone().sub(points[i - 1]).normalize();
    const b = n.clone().sub(p).normalize();
    const start = p.clone().addScaledVector(a, -bend);
    const end = p.clone().addScaledVector(b, bend);
    path.add(new THREE.LineCurve3(from, start));
    path.add(new THREE.QuadraticBezierCurve3(start, p.clone(), end));
    from = end;
  }
  const mesh = new THREE.Mesh(new THREE.TubeGeometry(path, 200, R, 20, false), mat);
  into.add(mesh);
  const length = path.getLength();
  for (let s = 3; s < length - 1; s += 6.5) {
    const at = path.getPointAt(s / length);
    const tan = path.getTangentAt(s / length);
    const c = new THREE.Mesh(new THREE.CylinderGeometry(R + 0.12, R + 0.12, 0.42, 24), ring);
    c.position.copy(at);
    c.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), tan);
    into.add(c);
  }
}

export function buildEcho(win: LabSet['window'], anchor: THREE.Object3D, ceilingY: number): THREE.Group {
  const g = new THREE.Group();
  g.name = 'echo-tubes';
  const steel = darkSteel('#3d434d');
  const ring = brass();
  // Points relative to the anchor (the window's center), a little off the glass. The lines drop
  // between the window's left edge and its left light bar, and rise at its right edge, short of the
  // right bar.
  const p = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);
  const left = -win.width / 2 - 3;
  const right = win.width / 2 + 6;
  const top = ceilingY - anchor.position.y + 2;
  const stationY = -win.height / 2 - 0.1;
  // Across the window's lower half, the band the first view sees above the bench.
  for (const [y, dz, dropX] of [[-win.height * 0.21, 1.4, left], [-win.height * 0.12, 2.6, left - 2.2]] as const) {
    pipe([p(dropX, stationY + 2, dz), p(dropX, y, dz), p(right, y, dz), p(right, top, dz)], 2.2, steel, ring, g);
  }
  // The wall station both lines drop into: a lacquered box with a brass door.
  const station = new THREE.Mesh(roundedBox(4.8, 4.2, 3.4, 0.2, 3), lacquer('#1b1f27'));
  station.position.set(left - 1.1, stationY, 2.0);
  const door = new THREE.Mesh(roundedBox(2.8, 2.2, 0.2, 0.1, 2), brass());
  door.position.copy(station.position).add(new THREE.Vector3(0, -0.2, 1.75));
  g.add(station, door);
  anchor.add(g);
  return g;
}
