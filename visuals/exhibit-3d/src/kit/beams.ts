// Beams: camera-facing HDR ribbons along polylines, with per-point width and color.
// The ribbon turns to face the camera in the vertex shader, so moving the camera costs nothing on
// the CPU. Paths are in world space: keep ribbon meshes at the scene root with an identity
// transform (they are created that way).

import * as THREE from 'three/webgpu';
import { attribute, cameraPosition, cross, float, max, positionGeometry } from 'three/tsl';
import { beam, BEAM_LAYER, beamShade } from './materials';
import { wavelengthToLinearSRGB } from './spectrum';

export interface RibbonOptions {
  points: THREE.Vector3[];
  /** Full width in world units, one value or one per point. */
  width?: number | number[];
  /** Linear HDR color per point (intensity baked in), or one color for the whole ribbon. */
  colors?: THREE.Color | THREE.Color[];
  /** Material override. Defaults to materials.beam('#ffffff', 1). */
  material?: THREE.Material;
  renderOrder?: number;
  /** Rewritten every frame: skip frustum culling and the per-frame bounding sphere. */
  dynamic?: boolean;
  /** Draw a soft dark band under the ribbon in light looks (materials.beamShade). */
  shade?: boolean;
}

/** Install the camera-facing vertex logic on any material used for a ribbon. */
export function ribbonPositionNode(): THREE.Node<'vec3'> {
  const tangent = attribute<'vec3'>('ribbonTangent', 'vec3');
  const side = attribute<'float'>('ribbonSide', 'float');
  const halfWidth = attribute<'float'>('ribbonHalfWidth', 'float');
  const toCamera = cameraPosition.sub(positionGeometry);
  const across = cross(tangent, toCamera);
  const dir = across.div(max(across.length(), float(1e-5)));
  return positionGeometry.add(dir.mul(side.mul(halfWidth)));
}

export class Ribbon {
  readonly mesh: THREE.Mesh;
  /** The shade under the ribbon, when created with `shade: true`. */
  readonly shade: THREE.Mesh | null = null;
  private count = 0;
  private dynamic: boolean;
  private lengths = new Float32Array(0);
  private tan = new THREE.Vector3();

  constructor(opts: RibbonOptions) {
    const material = opts.material ?? beam('#ffffff', 1);
    (material as THREE.MeshBasicNodeMaterial).positionNode = ribbonPositionNode();
    this.mesh = new THREE.Mesh(new THREE.BufferGeometry(), material);
    this.mesh.matrixAutoUpdate = false;
    this.mesh.layers.set(BEAM_LAYER);
    this.mesh.renderOrder = opts.renderOrder ?? 10;
    this.mesh.castShadow = false;
    this.mesh.receiveShadow = false;
    this.dynamic = opts.dynamic ?? false;
    if (this.dynamic) this.mesh.frustumCulled = false;
    if (opts.shade) {
      // A child sharing the geometry, drawn just before the beams so it never darkens them.
      const mat = beamShade();
      mat.positionNode = ribbonPositionNode();
      this.shade = new THREE.Mesh(this.mesh.geometry, mat);
      this.shade.layers.set(BEAM_LAYER);
      this.shade.renderOrder = this.mesh.renderOrder - 1;
      this.shade.frustumCulled = this.mesh.frustumCulled;
      this.shade.userData.occluder = false;
      this.mesh.add(this.shade);
    }
    this.setPath(opts.points, opts.width ?? 0.2, opts.colors ?? new THREE.Color(1, 1, 1));
  }

  /** Update the path. Reuses the buffers when the point count is unchanged. */
  setPath(points: THREE.Vector3[], width: number | number[] = 0.2, colors: THREE.Color | THREE.Color[] = new THREE.Color(1, 1, 1)): void {
    const n = points.length;
    if (n < 2) {
      this.mesh.visible = false;
      return;
    }
    this.mesh.visible = true;
    const geo = this.mesh.geometry;
    if (n !== this.count) {
      geo.dispose();
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(n * 6), 3));
      g.setAttribute('ribbonTangent', new THREE.BufferAttribute(new Float32Array(n * 6), 3));
      g.setAttribute('ribbonSide', new THREE.BufferAttribute(new Float32Array(n * 2), 1));
      g.setAttribute('ribbonHalfWidth', new THREE.BufferAttribute(new Float32Array(n * 2), 1));
      g.setAttribute('beamColor', new THREE.BufferAttribute(new Float32Array(n * 6), 3));
      g.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(n * 4), 2));
      const index: number[] = [];
      for (let i = 0; i < n - 1; i++) {
        const a = i * 2;
        index.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
      }
      g.setIndex(index);
      this.mesh.geometry = g;
      if (this.shade) this.shade.geometry = g;
      this.count = n;
      this.lengths = new Float32Array(n);
    }
    const g = this.mesh.geometry;
    const pos = g.getAttribute('position') as THREE.BufferAttribute;
    const tan = g.getAttribute('ribbonTangent') as THREE.BufferAttribute;
    const side = g.getAttribute('ribbonSide') as THREE.BufferAttribute;
    const hw = g.getAttribute('ribbonHalfWidth') as THREE.BufferAttribute;
    const col = g.getAttribute('beamColor') as THREE.BufferAttribute;
    const uv = g.getAttribute('uv') as THREE.BufferAttribute;
    const t = this.tan;
    const lengths = this.lengths;
    let length = 0;
    lengths[0] = 0;
    for (let i = 1; i < n; i++) {
      length += points[i].distanceTo(points[i - 1]);
      lengths[i] = length;
    }
    let maxHalf = 0;
    for (let i = 0; i < n; i++) {
      const prev = points[Math.max(0, i - 1)];
      const next = points[Math.min(n - 1, i + 1)];
      t.subVectors(next, prev);
      if (t.lengthSq() < 1e-12) t.set(1, 0, 0);
      t.normalize();
      const w = (Array.isArray(width) ? width[i] ?? width[width.length - 1] : width) / 2;
      maxHalf = Math.max(maxHalf, w);
      const c = Array.isArray(colors) ? colors[i] ?? colors[colors.length - 1] : colors;
      const u = length > 0 ? lengths[i] / length : 0;
      for (let s = 0; s < 2; s++) {
        const k = i * 2 + s;
        pos.setXYZ(k, points[i].x, points[i].y, points[i].z);
        tan.setXYZ(k, t.x, t.y, t.z);
        side.setX(k, s === 0 ? -1 : 1);
        hw.setX(k, w);
        col.setXYZ(k, c.r, c.g, c.b);
        uv.setXY(k, u, s);
      }
    }
    pos.needsUpdate = tan.needsUpdate = side.needsUpdate = hw.needsUpdate = col.needsUpdate = uv.needsUpdate = true;
    if (!this.dynamic || !g.boundingSphere) {
      g.computeBoundingSphere();
      if (g.boundingSphere) g.boundingSphere.radius += maxHalf;
    }
  }

  dispose(): void {
    this.mesh.geometry.dispose();
  }
}

/** One ribbon per wavelength, all sharing one material. `paths` come from the exhibit's model. */
export function spectrumFan(
  paths: { nm: number; points: THREE.Vector3[]; intensity?: number }[],
  opts: { width?: number; intensity?: number; material?: THREE.Material } = {},
): { group: THREE.Group; ribbons: Ribbon[]; update: (paths: { nm: number; points: THREE.Vector3[]; intensity?: number }[]) => void } {
  const group = new THREE.Group();
  const material = opts.material ?? beam('#ffffff', 1);
  const ribbons: Ribbon[] = [];
  const colorFor = (nm: number, k: number) => {
    const [r, g, b] = wavelengthToLinearSRGB(nm);
    return new THREE.Color(r * k, g * k, b * k);
  };
  for (const p of paths) {
    const r = new Ribbon({
      points: p.points,
      width: opts.width ?? 0.12,
      colors: colorFor(p.nm, (opts.intensity ?? 3) * (p.intensity ?? 1)),
      material,
    });
    ribbons.push(r);
    group.add(r.mesh);
  }
  return {
    group,
    ribbons,
    update(next) {
      next.forEach((p, i) => ribbons[i]?.setPath(p.points, opts.width ?? 0.12, colorFor(p.nm, (opts.intensity ?? 3) * (p.intensity ?? 1))));
    },
  };
}
