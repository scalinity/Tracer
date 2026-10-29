// Geometry language (style-bible §7): bevel everything engineered, add the mechanical detail that
// says "precision" (knurling, teeth, ticks, bolts), keep proportions chunky. Natural props are
// low-poly and flat-shaded on purpose. 1 world unit = 10 cm.
//
// Axis conventions: round parts (rings, knobs, stages, bolts) turn about +Y like CylinderGeometry;
// dials face +Z like a clock; rails run along +X. Rotate the result to fit the apparatus.

import * as THREE from 'three/webgpu';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

/** Seeded random numbers (mulberry32): the same seed always dresses the set the same way. */
export function makeRng(seed = 1): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Rounded box; the radius is clamped so thin parts stay valid. */
export function roundedBox(w: number, h: number, d: number, radius = 0.08, segments = 3): THREE.BufferGeometry {
  const r = Math.max(0.001, Math.min(radius, Math.min(w, h, d) / 2 - 0.001));
  return new RoundedBoxGeometry(w, h, d, segments, r);
}

/** Lathe a [radius, y] profile about +Y (knobs, housings, barrels). */
export function lathe(profile: [number, number][], segments = 64): THREE.BufferGeometry {
  return new THREE.LatheGeometry(profile.map(([r, y]) => new THREE.Vector2(r, y)), segments);
}

/** Flat-shaded copy without uvs, so low-poly pieces merge cleanly. */
export function flat(geo: THREE.BufferGeometry): THREE.BufferGeometry {
  const g = geo.index ? geo.toNonIndexed() : geo.clone();
  g.deleteAttribute('uv');
  g.deleteAttribute('normal');
  g.computeVertexNormals();
  return g;
}

function extrudeRing(outer: THREE.Vector2[], inner: number, width: number, bevel: number): THREE.BufferGeometry {
  const shape = new THREE.Shape(outer);
  const hole = new THREE.Path();
  hole.absarc(0, 0, inner, 0, Math.PI * 2, true);
  shape.holes.push(hole);
  const geo = new THREE.ExtrudeGeometry(shape, {
    depth: Math.max(0.001, width - 2 * bevel), bevelEnabled: bevel > 0, bevelThickness: bevel, bevelSize: bevel * 0.8,
    bevelSegments: 2, curveSegments: 48,
  });
  geo.translate(0, 0, -(width - 2 * bevel) / 2);
  geo.rotateX(-Math.PI / 2);
  geo.computeVertexNormals();
  return geo;
}

/** Knurled grip ring: real ridges, not a texture. */
export function knurledRing(o: { radius: number; width: number; ridges?: number; depth?: number; inner?: number; bevel?: number }): THREE.BufferGeometry {
  const n = o.ridges ?? 90;
  const depth = o.depth ?? 0.035;
  const pts: THREE.Vector2[] = [];
  for (let i = 0; i < n * 2; i++) {
    const a = (i / (n * 2)) * Math.PI * 2;
    const r = i % 2 === 0 ? o.radius + depth : o.radius;
    pts.push(new THREE.Vector2(Math.cos(a) * r, Math.sin(a) * r));
  }
  return extrudeRing(pts, o.inner ?? o.radius - 0.12, o.width, o.bevel ?? 0.008);
}

/** Gear ring with trapezoid teeth on the outer edge. */
export function gearRing(o: { radius: number; teeth?: number; toothDepth?: number; width: number; inner?: number; bevel?: number }): THREE.BufferGeometry {
  const t = o.teeth ?? 48;
  const td = o.toothDepth ?? 0.06;
  const pts: THREE.Vector2[] = [];
  const step = (Math.PI * 2) / t;
  const at = (a: number, r: number) => new THREE.Vector2(Math.cos(a) * r, Math.sin(a) * r);
  for (let k = 0; k < t; k++) {
    const a = k * step;
    pts.push(at(a, o.radius), at(a + step * 0.18, o.radius + td), at(a + step * 0.48, o.radius + td), at(a + step * 0.66, o.radius));
  }
  return extrudeRing(pts, o.inner ?? o.radius - 0.14, o.width, o.bevel ?? 0.01);
}

/** Hex bolt head with a chamfered top (flat shaded). */
export function bolt(o: { radius?: number; height?: number } = {}): THREE.BufferGeometry {
  const r = o.radius ?? 0.05;
  const h = o.height ?? 0.04;
  return flat(lathe([[0, 0], [r, 0], [r, h * 0.68], [r * 0.8, h], [0, h]], 6));
}

/** LED strip: a thin rounded bar along +X. Pair with materials.emissiveStrip(). */
export function lightStrip(o: { length: number; thickness?: number; depth?: number }): THREE.BufferGeometry {
  const t = o.thickness ?? 0.035;
  const d = o.depth ?? 0.035;
  return roundedBox(o.length, t, d, Math.min(t, d) * 0.45, 2);
}

/** Frustum plinth (narrower at the top) with rounded edges; base at y = 0, top at y = height. */
export function chamferedPlinth(o: { width: number; depth: number; height: number; slope?: number; radius?: number }): THREE.BufferGeometry {
  const slope = o.slope ?? o.height * 0.45;
  const geo = roundedBox(o.width, o.height, o.depth, o.radius ?? 0.06, 4);
  const p = geo.getAttribute('position') as THREE.BufferAttribute;
  for (let i = 0; i < p.count; i++) {
    const t = (p.getY(i) + o.height / 2) / o.height;
    p.setX(i, p.getX(i) * (1 - ((2 * slope) / o.width) * t));
    p.setZ(i, p.getZ(i) * (1 - ((2 * slope) / o.depth) * t));
  }
  geo.translate(0, o.height / 2, 0);
  geo.computeVertexNormals();
  return geo;
}

/** Optical rail along +X with ruler ticks on its front face (1 cm minor, 5 cm mid, 10 cm major). */
export function rail(o: {
  length: number; width?: number; height?: number; body: THREE.Material; tick: THREE.Material;
  tickEvery?: number; midEvery?: number; majorEvery?: number;
}): THREE.Group {
  const w = o.width ?? 0.42;
  const h = o.height ?? 0.3;
  const g = new THREE.Group();
  g.name = 'rail';
  const body = new THREE.Mesh(roundedBox(o.length, h, w, 0.035), o.body);
  body.position.y = h / 2;
  const top = new THREE.Mesh(roundedBox(o.length, 0.06, w * 0.52, 0.02), o.body);
  top.position.y = h + 0.03;
  body.castShadow = top.castShadow = true;
  body.receiveShadow = top.receiveShadow = true;
  g.add(body, top);
  const every = o.tickEvery ?? 0.1;
  const count = Math.floor(o.length / every) + 1;
  const ticks = new THREE.InstancedMesh(new THREE.BoxGeometry(0.009, 1, 0.006), o.tick, count);
  const m = new THREE.Matrix4();
  for (let i = 0; i < count; i++) {
    const len = i % (o.majorEvery ?? 10) === 0 ? 0.13 : i % (o.midEvery ?? 5) === 0 ? 0.085 : 0.05;
    m.makeScale(1, len, 1).setPosition(-o.length / 2 + i * every, h - 0.025 - len / 2, w / 2 + 0.004);
    ticks.setMatrixAt(i, m);
  }
  ticks.name = 'rail-ticks';
  g.add(ticks);
  return g;
}

/** Optical breadboard: a plate (top at y = 0) with a hole grid at `pitch` (25 mm by default). */
export function breadboard(o: { width: number; depth: number; thickness?: number; pitch?: number; holeRadius?: number; plate: THREE.Material; hole: THREE.Material }): THREE.Group {
  const t = o.thickness ?? 0.25;
  const pitch = o.pitch ?? 0.25;
  const g = new THREE.Group();
  g.name = 'breadboard';
  const plate = new THREE.Mesh(roundedBox(o.width, t, o.depth, 0.03), o.plate);
  plate.position.y = -t / 2;
  plate.receiveShadow = true;
  g.add(plate);
  const nx = Math.floor((o.width - pitch) / pitch) + 1;
  const nz = Math.floor((o.depth - pitch) / pitch) + 1;
  const holes = new THREE.InstancedMesh(new THREE.CircleGeometry(o.holeRadius ?? 0.03, 12).rotateX(-Math.PI / 2), o.hole, nx * nz);
  const m = new THREE.Matrix4();
  let k = 0;
  for (let i = 0; i < nx; i++) {
    for (let j = 0; j < nz; j++) {
      m.makeTranslation(-((nx - 1) * pitch) / 2 + i * pitch, 0.002, -((nz - 1) * pitch) / 2 + j * pitch);
      holes.setMatrixAt(k++, m);
    }
  }
  holes.name = 'breadboard-holes';
  g.add(holes);
  return g;
}

export interface DialOptions {
  radius: number;
  /** Ticks over the full turn (72 = every 5°). */
  ticks?: number;
  /** Every n-th tick is major and long. */
  majorEvery?: number;
  /** Label text per major tick; return '' to skip. Receives degrees. */
  label?: (deg: number) => string;
  face?: string;
  engrave?: string;
  /** Inner radius as a fraction of the radius where the ticks stop (0.72 by default). */
  inner?: number;
  /** Canvas size in pixels. */
  size?: number;
  /**
   * 'cw' numbers increase clockwise from the top. Use 'ccw' on a ring that turns clockwise past a
   * fixed index at the top, so the index reads the turn.
   */
  direction?: 'cw' | 'ccw';
  /** Tick length multiplier (1 by default). */
  tickScale?: number;
  /** Label radius as a fraction of the radius (defaults just inside the major ticks). */
  labelRadius?: number;
  /** Draw the fine inner ring (true by default). */
  innerRing?: boolean;
}

/** Draw an engraved dial face onto a canvas: ticks, numerals, a fine inner ring. Faces +Z, 0° at the top. */
export function dialTexture(o: DialOptions): THREE.CanvasTexture {
  const size = o.size ?? 512;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d') as CanvasRenderingContext2D;
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  const draw = () => {
    const c = size / 2;
    ctx.clearRect(0, 0, size, size);
    const grad = ctx.createRadialGradient(c, c * 0.8, size * 0.05, c, c, c);
    grad.addColorStop(0, o.face ? lighten(o.face, 0.12) : '#2c313b');
    grad.addColorStop(1, o.face ?? '#1d2129');
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(c, c, c, 0, Math.PI * 2);
    ctx.fill();
    const n = o.ticks ?? 72;
    const major = o.majorEvery ?? 6;
    const inner = (o.inner ?? 0.72) * c;
    ctx.strokeStyle = o.engrave ?? '#d9dee8';
    ctx.fillStyle = o.engrave ?? '#d9dee8';
    const dir = o.direction === 'ccw' ? -1 : 1;
    const ts = o.tickScale ?? 1;
    for (let i = 0; i < n; i++) {
      const a = dir * (i / n) * Math.PI * 2 - Math.PI / 2;
      const isMajor = i % major === 0;
      const isMid = !isMajor && i % (major / 2) === 0;
      const r0 = c * 0.965;
      const r1 = r0 - (isMajor ? c * 0.13 : isMid ? c * 0.085 : c * 0.05) * ts;
      ctx.lineWidth = isMajor ? size * 0.006 : size * 0.0035;
      ctx.beginPath();
      ctx.moveTo(c + Math.cos(a) * r0, c + Math.sin(a) * r0);
      ctx.lineTo(c + Math.cos(a) * r1, c + Math.sin(a) * r1);
      ctx.stroke();
      if (isMajor && o.label) {
        const text = o.label((i / n) * 360);
        if (text) {
          ctx.save();
          const lr = o.labelRadius !== undefined ? o.labelRadius * c : r1 - c * 0.075;
          ctx.translate(c + Math.cos(a) * lr, c + Math.sin(a) * lr);
          ctx.rotate(a + Math.PI / 2);
          ctx.font = `500 ${Math.round(size * 0.05)}px "DM Mono", ui-monospace, monospace`;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(text, 0, 0);
          ctx.restore();
        }
      }
    }
    if (o.innerRing !== false) {
      ctx.lineWidth = size * 0.003;
      ctx.globalAlpha = 0.5;
      ctx.beginPath();
      ctx.arc(c, c, inner, 0, Math.PI * 2);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    tex.needsUpdate = true;
  };
  draw();
  // Numerals need the mono font; redraw once it has loaded.
  if (typeof document !== 'undefined' && document.fonts) void document.fonts.ready.then(draw);
  return tex;
}

function lighten(hex: string, k: number): string {
  const c = new THREE.Color(hex);
  c.lerp(new THREE.Color('#ffffff'), k);
  return `#${c.getHexString()}`;
}

/** A dial: a disc facing +Z with an engraved face texture, on a material you pass (white base color). */
export function dial(o: DialOptions & { material: (map: THREE.Texture) => THREE.Material; thickness?: number }): THREE.Mesh {
  const tex = dialTexture(o);
  const geo = new THREE.CylinderGeometry(o.radius, o.radius, o.thickness ?? 0.02, 96, 1);
  geo.rotateX(Math.PI / 2);
  // CylinderGeometry's cap uvs already span the disc but put the texture's top at +X: a quarter
  // turn puts 0° at the top (+Y) and 90° at +X.
  geo.rotateZ(Math.PI / 2);
  const mesh = new THREE.Mesh(geo, o.material(tex));
  mesh.castShadow = true;
  return mesh;
}

/** Rotation stage turning about +Y: a base, a platter that turns, a degree ring and a side knob. */
export function rotationStage(o: { radius?: number; height?: number; base: THREE.Material; platter: THREE.Material; knob: THREE.Material; ring?: THREE.Material }): { group: THREE.Group; platter: THREE.Group } {
  const r = o.radius ?? 0.9;
  const h = o.height ?? 0.32;
  const group = new THREE.Group();
  group.name = 'rotation-stage';
  const base = new THREE.Mesh(lathe([[0, 0], [r, 0], [r, h * 0.62], [r * 0.97, h * 0.7], [0, h * 0.7]], 72), o.base);
  base.castShadow = base.receiveShadow = true;
  group.add(base);
  const platter = new THREE.Group();
  platter.position.y = h * 0.7;
  const top = new THREE.Mesh(lathe([[0, 0], [r * 0.93, 0], [r * 0.93, h * 0.24], [r * 0.9, h * 0.3], [0, h * 0.3]], 72), o.platter);
  top.castShadow = top.receiveShadow = true;
  platter.add(top);
  if (o.ring) {
    const ring = new THREE.Mesh(new THREE.RingGeometry(r * 0.7, r * 0.9, 96).rotateX(-Math.PI / 2), o.ring);
    ring.position.y = h * 0.3 + 0.002;
    platter.add(ring);
  }
  group.add(platter);
  const knob = new THREE.Mesh(knurledRing({ radius: r * 0.16, width: r * 0.22, ridges: 28, depth: 0.012, inner: 0.001 }), o.knob);
  knob.rotation.z = Math.PI / 2;
  knob.position.set(r + r * 0.1, h * 0.38, 0);
  knob.castShadow = true;
  group.add(knob);
  return { group, platter };
}

// ---------- low-poly naturals (flat shaded) ----------

export const lowPoly = {
  /** A clump of 5 to 8 faceted leaves. */
  foliage(rng: () => number, o: { size?: number; leaves?: number } = {}): THREE.BufferGeometry {
    const size = o.size ?? 1;
    const n = o.leaves ?? 5 + Math.floor(rng() * 4);
    const parts: THREE.BufferGeometry[] = [];
    for (let i = 0; i < n; i++) {
      const leaf = new THREE.OctahedronGeometry(1, 0);
      leaf.scale(0.22 * size, 0.06 * size, 0.62 * size * (0.75 + rng() * 0.5));
      leaf.translate(0, 0, 0.5 * size);
      leaf.rotateX(-0.5 - rng() * 0.7);
      leaf.rotateY((i / n) * Math.PI * 2 + rng() * 0.6);
      leaf.translate(0, size * (0.15 + rng() * 0.25), 0);
      parts.push(flat(leaf));
    }
    return mergeGeometries(parts) ?? parts[0];
  },

  /** A chunky pebble or rock: a jittered icosahedron. */
  rock(rng: () => number, o: { size?: number; squash?: number } = {}): THREE.BufferGeometry {
    const g = new THREE.IcosahedronGeometry(o.size ?? 1, 0);
    const p = g.getAttribute('position') as THREE.BufferAttribute;
    const seen = new Map<string, number>();
    for (let i = 0; i < p.count; i++) {
      const key = `${p.getX(i).toFixed(4)},${p.getY(i).toFixed(4)},${p.getZ(i).toFixed(4)}`;
      const k = seen.get(key) ?? 0.78 + rng() * 0.36;
      seen.set(key, k);
      p.setXYZ(i, p.getX(i) * k, p.getY(i) * k * (o.squash ?? 0.7), p.getZ(i) * k);
    }
    return flat(g);
  },

  /** A pine: stacked six-sided cones on a short trunk. */
  pine(o: { height?: number; radius?: number; tiers?: number; sides?: number } = {}): THREE.BufferGeometry {
    const h = o.height ?? 2;
    const r = o.radius ?? 0.6;
    const tiers = o.tiers ?? 3;
    const sides = o.sides ?? 6;
    const parts: THREE.BufferGeometry[] = [flat(new THREE.CylinderGeometry(r * 0.12, r * 0.15, h * 0.2, sides).translate(0, h * 0.1, 0))];
    for (let i = 0; i < tiers; i++) {
      const k = 1 - i / (tiers + 0.6);
      parts.push(flat(new THREE.ConeGeometry(r * k, h * 0.42, sides).translate(0, h * (0.32 + i * 0.2), 0)));
    }
    return mergeGeometries(parts) ?? parts[0];
  },

  /** A faceted crystal: a hexagonal prism with a pointed tip. */
  crystal(rng: () => number, o: { height?: number; radius?: number; sides?: number } = {}): THREE.BufferGeometry {
    const h = o.height ?? 1;
    const r = o.radius ?? 0.22;
    const s = o.sides ?? 6;
    const body = new THREE.CylinderGeometry(r * (0.85 + rng() * 0.15), r, h * 0.68, s).translate(0, h * 0.34, 0);
    const tip = new THREE.ConeGeometry(r * (0.85 + rng() * 0.15), h * 0.32, s).translate(0, h * 0.84, 0);
    const g = mergeGeometries([flat(body), flat(tip)]) ?? flat(body);
    g.rotateZ((rng() - 0.5) * 0.4);
    return g;
  },

  /** A faceted pot (7 sides by default), base at y = 0. */
  pot(o: { radius?: number; height?: number; sides?: number } = {}): THREE.BufferGeometry {
    const r = o.radius ?? 0.5;
    const h = o.height ?? 0.7;
    return flat(lathe([[0, 0], [r * 0.72, 0], [r, h * 0.92], [r * 1.04, h], [r * 0.9, h], [r * 0.86, h * 0.95], [0, h * 0.95]], o.sides ?? 7));
  },
};

/** Instance one geometry many times; `place` fills the matrix for instance i. */
export function scatter(geometry: THREE.BufferGeometry, material: THREE.Material, count: number, place: (i: number, m: THREE.Matrix4) => void): THREE.InstancedMesh {
  const mesh = new THREE.InstancedMesh(geometry, material, count);
  const m = new THREE.Matrix4();
  for (let i = 0; i < count; i++) {
    m.identity();
    place(i, m);
    mesh.setMatrixAt(i, m);
  }
  mesh.instanceMatrix.needsUpdate = true;
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

/** Merge static meshes that share a material into one draw call (webgpu-stack §7). */
export function mergeStatic(meshes: THREE.Mesh[], material: THREE.Material): THREE.Mesh {
  const geos = meshes.map((m) => {
    m.updateMatrixWorld(true);
    const g = m.geometry.clone().applyMatrix4(m.matrixWorld);
    return g.index ? g.toNonIndexed() : g;
  });
  const merged = mergeGeometries(geos) ?? geos[0];
  const out = new THREE.Mesh(merged, material);
  out.castShadow = true;
  out.receiveShadow = true;
  return out;
}
