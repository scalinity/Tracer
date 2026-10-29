// The truth: a pneumatic tube carrier, a cream capsule with felt wear bands and a brass latch, lit by
// the room and never an emitter. It follows its scripted fate (model/fates.ts). Hiding the truth
// dissolves it into nothing; with it hidden, the visitor sees only what the lab sees, and the glow.

import * as THREE from 'three/webgpu';
import { Discard, float, Fn, If, mx_noise_float, positionLocal, uniform, vec3, vec4 } from 'three/tsl';
import { brass, followEnvironment, rubber } from '../kit/materials';
import { lathe, roundedBox } from '../kit/geometry';
import type { Spot } from '../model/fates';
import { branchPath, carryPath, DIVERTER, LAB_TRAY, LOST_BIN, RECEIVER, SLEEVE, tubePath } from './layout';

export const CARRIER_HALF = 0.55;
const CARRIER_R = 0.24;

export interface Carrier {
  group: THREE.Group;
  /** Above the carrier in world space, for the truth label. */
  anchor: THREE.Object3D;
  /** 0 hidden, 1 shown. */
  setReveal(k: number): void;
  place(spot: Spot, u: number): void;
}

/** A lit material that dissolves where a smooth noise exceeds `reveal`, so the carrier evaporates rather than fades. */
function dissolving<M extends THREE.MeshPhysicalNodeMaterial>(mat: M, reveal: THREE.UniformNode<'float', number>): M {
  const c = mat.color;
  const base = vec3(c.r, c.g, c.b);
  mat.colorNode = Fn(() => {
    const n = mx_noise_float(positionLocal.mul(5.5)).mul(0.5).add(0.5);
    If(n.greaterThan(reveal.mul(1.25).sub(0.12)), () => {
      Discard();
    });
    return vec4(base, float(1));
  })();
  return mat;
}

export function buildCarrier(): Carrier {
  const group = new THREE.Group();
  group.name = 'carrier';
  const reveal = uniform(1);
  // Cream molded shell: a soft clear-coated plastic, warm in the key light.
  const shell = dissolving(followEnvironment(new THREE.MeshPhysicalNodeMaterial({ color: '#e6dac2', roughness: 0.42, metalness: 0, clearcoat: 0.55, clearcoatRoughness: 0.25 }), 0.9), reveal);
  const felt = dissolving(rubber('#2b2d33'), reveal);
  const latch = dissolving(brass(), reveal);
  const body = new THREE.Mesh(lathe([
    [0, -CARRIER_HALF], [0.12, -0.545], [0.2, -0.52], [0.232, -0.47], [CARRIER_R, -0.4],
    [CARRIER_R, 0.4], [0.232, 0.47], [0.2, 0.52], [0.12, 0.545], [0, CARRIER_HALF],
  ], 48), shell);
  // The lid seam, two felt wear bands and a latch: the bands are what ride the tube wall.
  const bands = [-0.33, 0.33].map((y) => {
    const b = new THREE.Mesh(new THREE.TorusGeometry(CARRIER_R + 0.004, 0.028, 10, 48).rotateX(Math.PI / 2), felt);
    b.position.y = y;
    return b;
  });
  const seam = new THREE.Mesh(new THREE.TorusGeometry(CARRIER_R + 0.002, 0.008, 6, 48).rotateX(Math.PI / 2), felt);
  seam.position.y = 0.18;
  const clasp = new THREE.Mesh(roundedBox(0.09, 0.16, 0.05, 0.015, 2), latch);
  clasp.position.set(0, 0.18, CARRIER_R + 0.012);
  const hinge = new THREE.Mesh(new THREE.CylinderGeometry(0.022, 0.022, 0.14, 10), latch);
  hinge.position.set(0, 0.18, -CARRIER_R - 0.01);
  group.add(body, ...bands, seam, clasp, hinge);
  // The truth label's anchor sits inside the carrier: its shell must not count as hiding it.
  group.traverse((o) => { if ((o as THREE.Mesh).isMesh) { o.castShadow = true; o.receiveShadow = true; o.userData.occluder = false; } });

  const anchor = new THREE.Object3D();
  const tube = tubePath();
  const branch = branchPath();
  const carry = carryPath();
  const up = new THREE.Vector3(0, 1, 0);
  const tan = new THREE.Vector3();
  const q = new THREE.Quaternion();
  const lying = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), Math.PI / 2);
  const ease = (k: number) => k * k * (3 - 2 * k);
  const upright = (x: number, y: number, z: number, tilt = 0) => {
    group.position.set(x, y, z);
    group.quaternion.setFromAxisAngle(new THREE.Vector3(0, 0, 1), tilt);
  };
  const along = (curve: THREE.Curve<THREE.Vector3>, k: number) => {
    curve.getPointAt(k, group.position);
    curve.getTangentAt(k, tan);
    group.quaternion.setFromUnitVectors(up, tan.normalize());
  };
  const standY = (bottom: number) => bottom + CARRIER_HALF + 0.03;

  return {
    group, anchor,
    setReveal(k) {
      reveal.value = k;
      group.visible = k > 0.01;
      group.traverse((o) => { if ((o as THREE.Mesh).isMesh) o.castShadow = k > 0.5; });
    },
    place(spot, u) {
      switch (spot) {
        case 'ward':
          upright(SLEEVE.x, standY(SLEEVE.bottom), SLEEVE.z);
          break;
        case 'launch':
          // Drawn up the sleeve to where the tube begins.
          upright(SLEEVE.x, THREE.MathUtils.lerp(standY(SLEEVE.bottom), SLEEVE.top - 0.2, ease(u)), SLEEVE.z);
          break;
        case 'tube':
          along(tube, Math.min(0.999, Math.max(0.001, ease(u) * 0.97 + 0.02)));
          break;
        case 'drop':
          upright(RECEIVER.x, THREE.MathUtils.lerp(DIVERTER.top - CARRIER_HALF, standY(RECEIVER.bottom), u * u), RECEIVER.z);
          break;
        case 'station':
          upright(RECEIVER.x, standY(RECEIVER.bottom), RECEIVER.z, 0.05);
          break;
        case 'carry': {
          const k = ease(u);
          carry.getPointAt(k, group.position);
          q.identity();
          group.quaternion.copy(q).slerp(lying, k);
          break;
        }
        case 'lab':
          group.position.set(LAB_TRAY.x, LAB_TRAY.y + CARRIER_R + 0.07, LAB_TRAY.z);
          group.quaternion.copy(lying);
          break;
        case 'branch':
          along(branch, Math.min(0.999, Math.max(0.001, u)));
          break;
        case 'bin':
          // Fallen to the bottom of the bin and leaning on its wall.
          upright(LOST_BIN.x + 0.12, LOST_BIN.bottom + 0.52, LOST_BIN.z, 0.42);
          break;
      }
      anchor.position.copy(group.position);
      anchor.position.y += 0.25;
    },
  };
}
