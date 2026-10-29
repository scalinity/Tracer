// Furniture for the Lab room (style-bible §8): an optics workshop at dusk. Every builder returns a
// group standing on y = 0 with its front facing +z, so the room places and turns it against a wall.
// Engineered pieces are bevelled and chunky; plants and props are faceted low-poly (style-bible §7).
// Repeats are instanced or merged, and nothing here casts shadows: the key light's shadow map covers
// the bench, and soft contact decals ground the furniture instead. 1 world unit = 10 cm.

import * as THREE from 'three/webgpu';
import { float, smoothstep, uv, vec4 } from 'three/tsl';
import { brass, darkSteel, emissiveStrip, emitterMaterial, lacquer, organic, screen } from './materials';
import { lathe, lightStrip, lowPoly, makeRng, mergeStatic, roundedBox, scatter } from './geometry';

type Rng = () => number;

let shared: ReturnType<typeof makePalette> | null = null;
function makePalette() {
  return {
    walnut: lacquer('#3a2a20'),
    oak: organic('#6e4f36', 0.62),
    butcher: organic('#8a6747', 0.58),
    steel: darkSteel('#3a3f49'),
    brass: brass(),
    cream: organic('#e7ddca', 0.75),
    terracotta: organic('#a5603f', 0.8),
    charcoal: organic('#2e3136', 0.7),
    leaf: organic('#5f8c45', 0.7),
    leafDark: organic('#46703a', 0.72),
    cognac: organic('#7a4b30', 0.55),
    linen: organic('#e6e1d6', 0.85),
    paper: organic('#d9cfbd', 0.9),
    strip: emissiveStrip('#ffbe78', 1.5),
    bulb: emitterMaterial('#ffd29a', 1.6),
  };
}
/** One palette for the whole room, so repeated pieces share materials and pipelines. */
export function palette(): ReturnType<typeof makePalette> {
  return (shared ??= makePalette());
}

function mesh(geo: THREE.BufferGeometry, mat: THREE.Material, x = 0, y = 0, z = 0): THREE.Mesh {
  const m = new THREE.Mesh(geo, mat);
  m.position.set(x, y, z);
  return m;
}
const box = (w: number, h: number, d: number, mat: THREE.Material, x = 0, y = 0, z = 0, r = 0.08) =>
  mesh(roundedBox(w, h, d, Math.min(r, Math.min(w, h, d) * 0.45), 2), mat, x, y, z);
const rod = (r: number, h: number, mat: THREE.Material, x = 0, y = 0, z = 0, seg = 12) =>
  mesh(new THREE.CylinderGeometry(r, r, h, seg), mat, x, y, z);

function canvasTexture(w: number, h: number, draw: (g: CanvasRenderingContext2D) => void): THREE.CanvasTexture {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  draw(c.getContext('2d') as CanvasRenderingContext2D);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}

/** A soft dark oval on the floor under a piece of furniture. */
export function contactShadow(width: number, depth: number, strength = 0.55): THREE.Mesh {
  const m = new THREE.MeshBasicNodeMaterial({ transparent: true, depthWrite: false });
  const d = uv().sub(0.5).mul(2).length();
  m.colorNode = vec4(0, 0, 0, float(1).sub(smoothstep(0.35, 1, d)).mul(strength));
  const s = mesh(new THREE.PlaneGeometry(width, depth), m, 0, 0.04, 0);
  s.rotation.x = -Math.PI / 2;
  s.renderOrder = 1;
  return s;
}

// ---------- north wall: credenza ----------

/** A low walnut sideboard with brass pulls, books and a vase on top. */
export function credenza(rng: Rng, o: { width?: number; height?: number; depth?: number } = {}): THREE.Group {
  const p = palette();
  const W = o.width ?? 20;
  const H = o.height ?? 6.5;
  const D = o.depth ?? 4.5;
  const g = new THREE.Group();
  const legH = 1;
  g.add(box(W, H - legH - 0.4, D, p.walnut, 0, legH + (H - legH - 0.4) / 2, 0, 0.12));
  g.add(box(W + 0.4, 0.4, D + 0.3, p.walnut, 0, H - 0.2, 0, 0.1));
  for (const x of [-W / 2 + 0.8, W / 2 - 0.8]) for (const z of [-D / 2 + 0.6, D / 2 - 0.6]) g.add(rod(0.18, legH, p.steel, x, legH / 2, z, 8));
  const doors = 4;
  const seams: THREE.Mesh[] = [];
  const pulls: THREE.Mesh[] = [];
  for (let i = 1; i < doors; i++) seams.push(box(0.06, H - legH - 0.9, 0.08, p.walnut, -W / 2 + (W * i) / doors, legH + (H - legH - 0.4) / 2, D / 2 + 0.01, 0.02));
  for (let i = 0; i < doors; i++) pulls.push(box(1.4, 0.16, 0.2, p.brass, -W / 2 + (W * (i + 0.5)) / doors, H - 1.4, D / 2 + 0.1, 0.05));
  g.add(mergeStatic(seams, p.charcoal), mergeStatic(pulls, p.brass));
  // A stack of books lying flat, a lidded box and a faceted vase with dry stems.
  const books: THREE.Mesh[] = [];
  let y = H;
  for (let i = 0; i < 3; i++) {
    const t = 0.45 + rng() * 0.25;
    const b = box(4.2 - i * 0.4, t, 3 - i * 0.2, p.linen, -W / 2 + 4, y + t / 2, 0, 0.04);
    b.rotation.y = (rng() - 0.5) * 0.3;
    books.push(b);
    y += t;
  }
  g.add(mergeStatic(books, p.cognac));
  g.add(box(3, 1.4, 2.2, p.oak, W / 2 - 5, H + 0.7, -0.2, 0.12));
  const vase = mesh(lowPoly.pot({ radius: 0.9, height: 2.6, sides: 7 }), p.terracotta, W / 2 - 1.8, H, 0.2);
  g.add(vase);
  for (let i = 0; i < 3; i++) {
    const stem = rod(0.04, 4.5 + i * 0.8, p.oak, W / 2 - 1.8, H + 4 + i * 0.4, 0.2, 5);
    stem.rotation.z = (i - 1) * 0.2;
    g.add(stem);
  }
  g.add(contactShadow(W + 3, D + 3));
  return g;
}

// ---------- north-east: open shelving and ladder ----------

const BOOK_COLORS = ['#5b4a3c', '#3d4a5a', '#6a5a44', '#7a3b2e', '#2f4a43', '#8a7a5c', '#4a3d5a', '#b8a888'];

/**
 * Open shelving in walnut, each board lit from below by a warm strip, filled with instanced books, a few
 * storage boxes and optics props (a camera, binoculars, a lens on a stand).
 */
export function shelving(rng: Rng, o: { bays?: number; bayWidth?: number; height?: number; depth?: number; boards?: number } = {}): THREE.Group {
  const p = palette();
  const bays = o.bays ?? 3;
  const bw = o.bayWidth ?? 6.8;
  const H = o.height ?? 30;
  const D = o.depth ?? 3.6;
  const boards = o.boards ?? 4;
  const W = bays * bw;
  const g = new THREE.Group();
  const frame: THREE.Mesh[] = [];
  for (let i = 0; i <= bays; i++) frame.push(box(0.5, H, D, p.walnut, -W / 2 + i * bw, H / 2, 0, 0.08));
  const levels: number[] = [];
  for (let k = 0; k <= boards; k++) {
    const y = 1 + (k * (H - 1.5)) / boards;
    levels.push(y);
    frame.push(box(W, 0.4, D, p.walnut, 0, y, 0, 0.06));
  }
  frame.push(box(W, H, 0.3, p.walnut, 0, H / 2, -D / 2 + 0.15, 0.04));
  g.add(mergeStatic(frame, p.walnut));
  // Warm strips under every board except the lowest, facing down onto the shelf below.
  for (let k = 1; k <= boards; k++) {
    const s = mesh(lightStrip({ length: W - 0.8, thickness: 0.06, depth: 0.08 }), p.strip, 0, levels[k] - 0.26, D / 2 - 0.3);
    g.add(s);
  }
  // Books: one instanced mesh, per-instance size and color. Each shelf keeps a gap for a prop.
  const spots: { x: number; y: number }[] = [];
  const mats: THREE.Matrix4[] = [];
  const colors: THREE.Color[] = [];
  const q = new THREE.Quaternion();
  for (let k = 0; k < boards; k++) {
    const y0 = levels[k] + 0.2;
    const room = levels[k + 1] - levels[k] - 0.6;
    for (let b = 0; b < bays; b++) {
      const left = -W / 2 + b * bw + 0.45;
      const right = left + bw - 0.9;
      const gapAt = rng() < 0.55 ? left + (right - left) * (0.25 + rng() * 0.5) : Infinity;
      let x = left;
      while (x < right - 0.5) {
        if (Math.abs(x - gapAt) < 1.8) {
          spots.push({ x: x + 1.6, y: y0 });
          x += 3.6;
          continue;
        }
        const w = 0.35 + rng() * 0.35;
        const h = Math.min(room, 2.6 + rng() * 2.4);
        const d = D * (0.62 + rng() * 0.2);
        const lean = rng() < 0.08 ? 0.22 : 0;
        q.setFromAxisAngle(new THREE.Vector3(0, 0, 1), lean);
        mats.push(new THREE.Matrix4().compose(new THREE.Vector3(x + w / 2, y0 + h / 2, -D / 2 + d / 2 + 0.2), q, new THREE.Vector3(w, h, d)));
        colors.push(new THREE.Color(BOOK_COLORS[Math.floor(rng() * BOOK_COLORS.length)]));
        x += w + 0.04 + (lean ? 0.4 : 0);
      }
    }
  }
  const books = scatter(roundedBox(1, 1, 1, 0.06, 1), organic('#ffffff', 0.82), mats.length, (i, m) => m.copy(mats[i]));
  colors.forEach((c, i) => books.setColorAt(i, c));
  books.castShadow = books.receiveShadow = false;
  g.add(books);
  // Props in some of the gaps.
  const props = [camera, binoculars, lensOnStand, storageBox];
  spots.slice(0, 6).forEach((s, i) => {
    const prop = props[i % props.length]();
    prop.position.set(s.x, s.y, 0.2);
    prop.rotation.y = (rng() - 0.5) * 0.6;
    g.add(prop);
  });
  g.add(contactShadow(W + 2, D + 3, 0.4));
  return g;
}

function camera(): THREE.Group {
  const p = palette();
  const g = new THREE.Group();
  g.add(box(2.4, 1.5, 1.1, p.charcoal, 0, 0.75, 0, 0.18));
  g.add(box(0.8, 0.5, 0.7, p.charcoal, -0.5, 1.7, 0, 0.1));
  const lens = rod(0.55, 1.2, p.steel, 0.2, 0.8, 0.9, 16);
  lens.rotation.x = Math.PI / 2;
  g.add(lens);
  const glass = rod(0.42, 0.05, p.brass, 0.2, 0.8, 1.52, 16);
  glass.rotation.x = Math.PI / 2;
  g.add(glass);
  return g;
}

function binoculars(): THREE.Group {
  const p = palette();
  const g = new THREE.Group();
  for (const x of [-0.5, 0.5]) {
    const tube = rod(0.42, 2, p.charcoal, x, 0.45, 0, 12);
    tube.rotation.x = Math.PI / 2;
    g.add(tube);
  }
  g.add(box(0.7, 0.3, 0.9, p.steel, 0, 0.5, 0, 0.08));
  return g;
}

function lensOnStand(): THREE.Group {
  const p = palette();
  const g = new THREE.Group();
  g.add(rod(0.7, 0.25, p.steel, 0, 0.12, 0, 16));
  g.add(rod(0.1, 2.2, p.brass, 0, 1.3, 0, 8));
  const ring = mesh(new THREE.TorusGeometry(1, 0.14, 8, 24), p.brass, 0, 3.2, 0);
  g.add(ring);
  const glass = mesh(new THREE.CircleGeometry(0.95, 24), organic('#9fb8c8', 0.15), 0, 3.2, 0);
  g.add(glass);
  return g;
}

function storageBox(): THREE.Group {
  const p = palette();
  const g = new THREE.Group();
  g.add(box(3, 1.8, 2.6, p.paper, 0, 0.9, 0, 0.1));
  g.add(box(3.1, 0.3, 2.7, p.oak, 0, 1.85, 0, 0.06));
  return g;
}

/** A rolling library ladder; lean it on shelving by tilting it about x. */
export function ladder(o: { height?: number; width?: number } = {}): THREE.Group {
  const p = palette();
  const H = o.height ?? 30;
  const W = o.width ?? 4.4;
  const g = new THREE.Group();
  const parts: THREE.Mesh[] = [box(0.36, H, 0.6, p.oak, -W / 2, H / 2, 0, 0.08), box(0.36, H, 0.6, p.oak, W / 2, H / 2, 0, 0.08)];
  for (let y = 2.2; y < H - 1; y += 2.8) parts.push(box(W, 0.3, 0.9, p.oak, 0, y, 0, 0.06));
  g.add(mergeStatic(parts, p.oak));
  for (const x of [-W / 2, W / 2]) g.add(mesh(new THREE.TorusGeometry(0.5, 0.12, 6, 12), p.brass, x, H - 0.4, -0.5));
  return g;
}

// ---------- west wall: workbench ----------

/** A butcher-block workbench on a steel frame, with a vise, a tray of parts and a small instrument. */
export function workbench(rng: Rng, o: { length?: number; height?: number; depth?: number } = {}): THREE.Group {
  const p = palette();
  const L = o.length ?? 26;
  const H = o.height ?? 9;
  const D = o.depth ?? 7;
  const g = new THREE.Group();
  g.add(box(L, 0.9, D, p.butcher, 0, H - 0.45, 0, 0.12));
  const frame: THREE.Mesh[] = [];
  for (const x of [-L / 2 + 1, L / 2 - 1]) for (const z of [-D / 2 + 0.8, D / 2 - 0.8]) frame.push(box(0.6, H - 0.9, 0.6, p.steel, x, (H - 0.9) / 2, z, 0.06));
  frame.push(box(L - 1.4, 0.4, D - 1.2, p.steel, 0, 2, 0, 0.06));
  frame.push(box(L - 2, 0.5, 0.5, p.steel, 0, H - 1.4, D / 2 - 0.8, 0.06));
  g.add(mergeStatic(frame, p.steel));
  // Vise on the right end.
  const vise = new THREE.Group();
  vise.add(box(2.2, 1.2, 1.6, p.steel, 0, 0.6, 0, 0.1), box(2.4, 1, 0.5, p.steel, 0, 1.1, 1, 0.08));
  const screw = rod(0.12, 2.4, p.brass, 0, 0.7, 1.6, 8);
  screw.rotation.x = Math.PI / 2;
  vise.add(screw);
  vise.position.set(L / 2 - 2.2, H, D / 2 - 1.4);
  g.add(vise);
  // A tray of small parts and a stack of notebooks.
  g.add(box(4.2, 0.5, 2.8, p.charcoal, -L / 2 + 4, H + 0.25, 0.6, 0.08));
  const bits: THREE.Mesh[] = [];
  for (let i = 0; i < 9; i++) bits.push(box(0.5, 0.3 + rng() * 0.3, 0.5, p.brass, -L / 2 + 2.6 + (i % 3) * 1.2, H + 0.6, -0.4 + Math.floor(i / 3) * 0.9, 0.05));
  g.add(mergeStatic(bits, p.brass));
  for (let i = 0; i < 3; i++) g.add(box(3, 0.3, 2.2, i === 1 ? p.cognac : p.linen, 1.5, H + 0.15 + i * 0.3, -1.2, 0.04));
  // A small instrument with a dim trace on its screen.
  const inst = new THREE.Group();
  inst.add(box(4.6, 3, 3.2, p.charcoal, 0, 1.5, 0, 0.2));
  const trace = canvasTexture(256, 160, (c) => {
    c.fillStyle = '#0b1416';
    c.fillRect(0, 0, 256, 160);
    c.strokeStyle = '#7fd4c0';
    c.lineWidth = 6;
    c.beginPath();
    for (let x = 0; x <= 256; x += 4) c.lineTo(x, 80 + Math.sin(x / 18) * 36);
    c.stroke();
  });
  inst.add(mesh(new THREE.PlaneGeometry(2.8, 1.8), screen(trace, 0.6), -0.4, 1.7, 1.62));
  for (let i = 0; i < 3; i++) inst.add(rod(0.22, 0.3, p.brass, 1.5, 0.9 + i * 0.7, 1.65, 10).rotateX(Math.PI / 2));
  inst.position.set(L / 2 - 8, H, -1);
  g.add(inst);
  g.add(contactShadow(L + 3, D + 3));
  return g;
}

/** A pegboard with a few large tools that read as silhouettes from across the room. */
export function pegboard(o: { width?: number; height?: number } = {}): THREE.Group {
  const p = palette();
  const W = o.width ?? 22;
  const H = o.height ?? 12;
  const g = new THREE.Group();
  const holes = canvasTexture(512, 280, (c) => {
    c.fillStyle = '#8b7356';
    c.fillRect(0, 0, 512, 280);
    c.fillStyle = '#4a3c2c';
    for (let x = 12; x < 512; x += 24) for (let y = 12; y < 280; y += 24) c.fillRect(x - 2, y - 2, 4, 4);
  });
  const board = new THREE.MeshStandardNodeMaterial({ map: holes, roughness: 0.85 });
  g.add(box(W, H, 0.3, board, 0, H / 2, 0, 0.05));
  const tools: THREE.Mesh[] = [];
  // Calipers, a long rule, a wrench, a hand saw blade and a hammer, laid out like a tool wall.
  tools.push(box(0.5, 7, 0.2, p.steel, -W / 2 + 2.5, H / 2, 0.3, 0.05), box(2.2, 0.5, 0.2, p.steel, -W / 2 + 3.4, H / 2 + 3.2, 0.3, 0.05));
  tools.push(box(0.6, 9, 0.15, p.steel, -W / 2 + 5.5, H / 2, 0.3, 0.04));
  tools.push(box(0.8, 5.4, 0.25, p.steel, -W / 2 + 8.5, H / 2 - 0.8, 0.3, 0.12));
  tools.push(box(4.8, 2.6, 0.12, p.steel, W / 2 - 8, H / 2 + 1.5, 0.3, 0.06));
  tools.push(box(0.5, 5, 0.3, p.steel, W / 2 - 3, H / 2 - 1, 0.3, 0.08), box(2.2, 0.9, 0.5, p.steel, W / 2 - 3, H / 2 + 1.8, 0.3, 0.1));
  g.add(mergeStatic(tools, p.steel));
  g.add(mesh(new THREE.TorusGeometry(1.1, 0.35, 6, 12), p.steel, -W / 2 + 8.5, H / 2 + 2.6, 0.4));
  g.add(mesh(new THREE.TorusGeometry(1.6, 0.18, 6, 20), p.charcoal, W / 2 - 13, H / 2, 0.4));
  g.add(box(1.6, 3, 0.4, p.cognac, W / 2 - 8, H / 2 - 2, 0.4, 0.1));
  return g;
}

/** An articulated desk lamp with a warm bulb and a point light under its head. */
export function deskLamp(): { group: THREE.Group; light: THREE.PointLight } {
  const p = palette();
  const g = new THREE.Group();
  g.add(rod(1, 0.4, p.steel, 0, 0.2, 0, 16));
  const lower = box(0.3, 5, 0.3, p.steel, 0.9, 2.5, 0, 0.06);
  lower.rotation.z = -0.35;
  const upper = box(0.3, 4.6, 0.3, p.steel, 2.6, 5.6, 0, 0.06);
  upper.rotation.z = 0.9;
  g.add(lower, upper);
  const shadeMat = p.steel.clone();
  shadeMat.side = THREE.DoubleSide;
  const shade = mesh(new THREE.ConeGeometry(1.2, 1.5, 16, 1, true), shadeMat, 4.5, 6.4, 0);
  shade.rotation.z = 0.5;
  g.add(shade);
  const bulb = mesh(new THREE.SphereGeometry(0.45, 12, 8), p.bulb, 4.6, 6, 0);
  g.add(bulb);
  const light = new THREE.PointLight('#ffcf8a', 55, 26, 2);
  light.position.set(4.8, 5.6, 0);
  g.add(light);
  return { group: g, light };
}

/** A tall cabinet of small parts drawers with brass pulls and paper labels. */
export function drawerCabinet(o: { cols?: number; rows?: number; width?: number; height?: number; depth?: number } = {}): THREE.Group {
  const p = palette();
  const cols = o.cols ?? 3;
  const rows = o.rows ?? 6;
  const W = o.width ?? 7;
  const H = o.height ?? 12;
  const D = o.depth ?? 4.5;
  const g = new THREE.Group();
  g.add(box(W, H, D, p.steel, 0, H / 2, 0, 0.12));
  const cw = (W - 0.6) / cols;
  const ch = (H - 0.8) / rows;
  const fronts = scatter(roundedBox(cw - 0.18, ch - 0.18, 0.2, 0.05, 1), p.charcoal, cols * rows, (i, m) =>
    m.makeTranslation(-W / 2 + 0.3 + cw * ((i % cols) + 0.5), 0.5 + ch * (Math.floor(i / cols) + 0.5), D / 2 + 0.1));
  const pulls = scatter(roundedBox(cw * 0.4, 0.14, 0.18, 0.04, 1), p.brass, cols * rows, (i, m) =>
    m.makeTranslation(-W / 2 + 0.3 + cw * ((i % cols) + 0.5), 0.5 + ch * (Math.floor(i / cols) + 0.5) - ch * 0.18, D / 2 + 0.24));
  const labels = scatter(new THREE.PlaneGeometry(cw * 0.5, ch * 0.22), p.paper, cols * rows, (i, m) =>
    m.makeTranslation(-W / 2 + 0.3 + cw * ((i % cols) + 0.5), 0.5 + ch * (Math.floor(i / cols) + 0.5) + ch * 0.14, D / 2 + 0.21));
  for (const s of [fronts, pulls, labels]) s.castShadow = s.receiveShadow = false;
  g.add(fronts, pulls, labels);
  g.add(contactShadow(W + 2, D + 2));
  return g;
}

/** A round shop stool with splayed legs and a foot ring. */
export function stool(): THREE.Group {
  const p = palette();
  const g = new THREE.Group();
  g.add(rod(1.7, 0.5, p.cognac, 0, 7, 0, 18));
  for (let i = 0; i < 4; i++) {
    const a = (i / 4) * Math.PI * 2 + Math.PI / 4;
    const leg = rod(0.14, 7.2, p.steel, Math.cos(a) * 1.05, 3.5, Math.sin(a) * 1.05, 6);
    leg.rotation.set(Math.sin(a) * 0.12, 0, -Math.cos(a) * 0.12);
    g.add(leg);
  }
  const ring = mesh(new THREE.TorusGeometry(1.3, 0.09, 6, 20), p.steel, 0, 2.6, 0);
  ring.rotation.x = Math.PI / 2;
  g.add(ring, contactShadow(4.5, 4.5, 0.4));
  return g;
}

// ---------- south wall: reading corner, door, coat ----------

/** A low cognac armchair with chunky cushions and short steel legs. */
export function armchair(): THREE.Group {
  const p = palette();
  const g = new THREE.Group();
  g.add(box(8, 2.4, 7.5, p.cognac, 0, 2.6, 0, 0.6));
  g.add(box(6.4, 1.2, 6, p.cognac, 0, 4.3, 0.4, 0.5));
  g.add(box(8, 6.5, 1.8, p.cognac, 0, 5.4, -2.9, 0.7));
  for (const x of [-3.5, 3.5]) g.add(box(1.4, 4.2, 7.2, p.cognac, x, 4.4, 0.1, 0.6));
  for (const x of [-3.2, 3.2]) for (const z of [-2.8, 2.8]) g.add(rod(0.22, 1.4, p.steel, x, 0.7, z, 8));
  g.add(contactShadow(10, 9.5, 0.6));
  return g;
}

/** A floor lamp with a linen drum shade glowing warm, and a point light inside it. */
export function floorLamp(): { group: THREE.Group; light: THREE.PointLight } {
  const p = palette();
  const g = new THREE.Group();
  g.add(rod(1.4, 0.3, p.steel, 0, 0.15, 0, 18));
  g.add(rod(0.12, 15, p.brass, 0, 7.6, 0, 8));
  const shade = mesh(lathe([[2.2, 0], [1.7, 3.2]], 24), new THREE.MeshStandardNodeMaterial({ color: '#efe2c8', roughness: 0.9, side: THREE.DoubleSide, emissive: new THREE.Color('#ff9f4a'), emissiveIntensity: 0.38 }), 0, 14.4, 0);
  g.add(shade);
  g.add(mesh(new THREE.SphereGeometry(0.6, 12, 8), p.bulb, 0, 15.6, 0));
  const light = new THREE.PointLight('#ffcb85', 150, 42, 2);
  light.position.set(0, 15.4, 0);
  g.add(light, contactShadow(4.5, 4.5, 0.4));
  return { group: g, light };
}

/** A round side table with a mug and a book. */
export function sideTable(): THREE.Group {
  const p = palette();
  const g = new THREE.Group();
  g.add(rod(2.4, 0.4, p.walnut, 0, 6, 0, 24));
  g.add(rod(0.35, 5.8, p.steel, 0, 2.9, 0, 10));
  g.add(rod(1.4, 0.3, p.steel, 0, 0.15, 0, 18));
  const mug = mesh(lathe([[0, 0], [0.55, 0], [0.6, 1.1], [0.52, 1.1], [0.47, 0.15], [0, 0.15]], 16), p.cream, 0.8, 6.2, 0.5);
  const handle = mesh(new THREE.TorusGeometry(0.32, 0.08, 6, 12), p.cream, 1.45, 6.75, 0.5);
  g.add(mug, handle, box(2.6, 0.4, 1.9, p.linen, -0.6, 6.4, -0.4, 0.04), contactShadow(5.5, 5.5, 0.45));
  return g;
}

/** A framed print: an abstract dusk landscape in large, simple bands that survive the blur. */
export function framedPrint(o: { width?: number; height?: number; seed?: number } = {}): THREE.Group {
  const p = palette();
  const W = o.width ?? 14;
  const H = o.height ?? 10;
  const rng = makeRng(o.seed ?? 5);
  const art = canvasTexture(512, Math.round((512 * H) / W), (c) => {
    const h = c.canvas.height;
    const sky = c.createLinearGradient(0, 0, 0, h);
    sky.addColorStop(0, '#2b3355');
    sky.addColorStop(0.55, '#8a7aa6');
    sky.addColorStop(0.75, '#e2a98a');
    c.fillStyle = sky;
    c.fillRect(0, 0, 512, h);
    for (const [col, base] of [['#4b4a6e', 0.62], ['#2e3150', 0.78]] as [string, number][]) {
      c.fillStyle = col;
      c.beginPath();
      c.moveTo(0, h);
      for (let x = 0; x <= 512; x += 64) c.lineTo(x, h * (base - rng() * 0.16));
      c.lineTo(512, h);
      c.fill();
    }
  });
  const g = new THREE.Group();
  g.add(box(W + 1.2, H + 1.2, 0.5, p.walnut, 0, 0, 0, 0.1));
  // A brass picture light above the frame; the print reads as lit by it without a light of its own.
  const lit = new THREE.MeshStandardNodeMaterial({ map: art, roughness: 0.8, emissiveMap: art, emissive: new THREE.Color('#ffe2bf'), emissiveIntensity: 0.32 });
  g.add(mesh(new THREE.PlaneGeometry(W, H), lit, 0, 0, 0.27));
  g.add(box(W * 0.55, 0.45, 0.6, p.brass, 0, H / 2 + 1.4, 0.9, 0.12));
  g.add(mesh(lightStrip({ length: W * 0.5, thickness: 0.05, depth: 0.05 }), p.strip, 0, H / 2 + 1.15, 1.2));
  return g;
}

/** A panelled dark door with a brass lever, in its frame. */
export function door(o: { width?: number; height?: number } = {}): THREE.Group {
  const p = palette();
  const W = o.width ?? 9;
  const H = o.height ?? 21;
  const g = new THREE.Group();
  const trim: THREE.Mesh[] = [box(0.7, H + 0.7, 0.6, p.walnut, -W / 2 - 0.35, (H + 0.7) / 2, 0.1, 0.08), box(0.7, H + 0.7, 0.6, p.walnut, W / 2 + 0.35, (H + 0.7) / 2, 0.1, 0.08), box(W + 2.1, 0.7, 0.6, p.walnut, 0, H + 0.35, 0.1, 0.08)];
  g.add(mergeStatic(trim, p.walnut));
  g.add(box(W, H, 0.5, p.walnut, 0, H / 2, 0, 0.06));
  const panels: THREE.Mesh[] = [];
  for (const [y, h] of [[H * 0.72, H * 0.34], [H * 0.27, H * 0.36]] as [number, number][]) panels.push(box(W - 2.4, h, 0.12, p.walnut, 0, y, 0.28, 0.06));
  g.add(mergeStatic(panels, p.charcoal));
  g.add(box(0.5, 0.5, 0.5, p.brass, W / 2 - 1.2, H * 0.47, 0.45, 0.12), box(1.6, 0.25, 0.25, p.brass, W / 2 - 1.9, H * 0.47, 0.75, 0.08));
  return g;
}

/** A wall rail with a white lab coat hanging from a hook. */
export function coatHook(): THREE.Group {
  const p = palette();
  const g = new THREE.Group();
  g.add(box(6, 0.8, 0.5, p.walnut, 0, 17, 0.25, 0.1));
  for (const x of [-1.6, 1.6]) g.add(box(0.25, 0.25, 1, p.brass, x, 16.8, 0.8, 0.06));
  const coat = new THREE.Group();
  coat.add(box(4.2, 10.5, 1.4, p.linen, 0, -5.4, 0, 0.6));
  coat.add(box(2.6, 1.6, 1.5, p.linen, 0, -0.6, 0.05, 0.6));
  for (const x of [-2.4, 2.4]) {
    const sleeve = box(1.1, 8, 1.1, p.linen, x, -4.6, 0.2, 0.5);
    sleeve.rotation.z = x > 0 ? 0.08 : -0.08;
    coat.add(sleeve);
  }
  coat.position.set(-1.6, 16.6, 1.1);
  g.add(coat);
  return g;
}

// ---------- plants, rug, pendants ----------

/** A tall faceted plant in a large planter: a trunk with stacked leaf clusters. */
export function tallPlant(rng: Rng, o: { height?: number; pot?: THREE.Material } = {}): THREE.Group {
  const p = palette();
  const H = o.height ?? 16;
  const g = new THREE.Group();
  g.add(mesh(lowPoly.pot({ radius: 2.4, height: 4.2, sides: 7 }), o.pot ?? p.cream));
  g.add(rod(0.22, H - 4, p.oak, 0, 4 + (H - 4) / 2, 0, 6));
  for (let i = 0; i < 6; i++) {
    const f = mesh(lowPoly.foliage(rng, { size: 3.4 - i * 0.25, leaves: 7 }), i % 2 ? p.leafDark : p.leaf, (rng() - 0.5) * 1.4, 6.5 + i * ((H - 7) / 6), (rng() - 0.5) * 1.4);
    f.rotation.y = rng() * Math.PI * 2;
    g.add(f);
  }
  g.add(contactShadow(6.5, 6.5, 0.5));
  return g;
}

/** Three plants of different heights in matching planters, arranged as one group. */
export function plantGroup(rng: Rng): THREE.Group {
  const p = palette();
  const g = new THREE.Group();
  const tall = tallPlant(rng, { height: 18, pot: p.charcoal });
  tall.position.set(-2.4, 0, -1.4);
  g.add(tall);
  // A snake plant: upright faceted blades.
  const snake = new THREE.Group();
  snake.add(mesh(lowPoly.pot({ radius: 1.8, height: 3.4, sides: 7 }), p.charcoal));
  for (let i = 0; i < 9; i++) {
    const blade = mesh(new THREE.ConeGeometry(0.35, 5.5 + rng() * 3.5, 4, 1), i % 3 ? p.leaf : p.leafDark, (rng() - 0.5) * 1.6, 5.5, (rng() - 0.5) * 1.6);
    blade.rotation.set((rng() - 0.5) * 0.3, rng() * Math.PI, (rng() - 0.5) * 0.3);
    blade.scale.set(1, 1, 0.35);
    snake.add(blade);
  }
  snake.position.set(2.8, 0, 0.6);
  g.add(snake);
  const bush = new THREE.Group();
  bush.add(mesh(lowPoly.pot({ radius: 1.4, height: 2.4, sides: 7 }), p.charcoal));
  for (let i = 0; i < 3; i++) {
    const f = mesh(lowPoly.foliage(rng, { size: 2.2, leaves: 7 }), p.leaf, 0, 2.6 + i * 0.6, 0);
    f.rotation.y = rng() * Math.PI;
    bush.add(f);
  }
  bush.position.set(0.4, 0, 3);
  g.add(bush, contactShadow(11, 9, 0.45));
  return g;
}

/** A dark rug with a muted warm border, lying on the floor. */
export function rug(o: { width?: number; depth?: number } = {}): THREE.Mesh {
  const W = o.width ?? 36;
  const D = o.depth ?? 26;
  const tex = canvasTexture(512, Math.round((512 * D) / W), (c) => {
    const h = c.canvas.height;
    c.fillStyle = '#1d2232';
    c.fillRect(0, 0, 512, h);
    c.strokeStyle = '#8a5a3c';
    c.lineWidth = 10;
    c.strokeRect(22, 22, 512 - 44, h - 44);
    c.strokeStyle = '#3b4258';
    c.lineWidth = 4;
    c.strokeRect(46, 46, 512 - 92, h - 92);
  });
  const r = mesh(new THREE.PlaneGeometry(W, D), new THREE.MeshStandardNodeMaterial({ map: tex, roughness: 0.95 }), 0, 0.03, 0);
  r.rotation.x = -Math.PI / 2;
  r.receiveShadow = true;
  return r;
}

/** A pendant lamp on a cord from the ceiling: dark dome, warm bulb, no light of its own. */
export function pendant(o: { drop?: number } = {}): THREE.Group {
  const p = palette();
  const drop = o.drop ?? 16;
  const g = new THREE.Group();
  g.add(rod(0.06, drop, p.charcoal, 0, -drop / 2, 0, 5));
  g.add(rod(0.8, 0.3, p.steel, 0, -0.15, 0, 12));
  const dome = mesh(lathe([[0.2, 0], [1.8, -1.6], [2.2, -2.4], [0, -0.4]], 20), p.steel, 0, -drop, 0);
  g.add(dome, mesh(new THREE.SphereGeometry(0.55, 12, 8), p.bulb, 0, -drop - 1.9, 0));
  return g;
}

// ---------- east wall: display cabinet ----------

function telescope(): THREE.Group {
  const p = palette();
  const g = new THREE.Group();
  for (let i = 0; i < 3; i++) {
    const a = (i / 3) * Math.PI * 2;
    const leg = rod(0.08, 3.4, p.oak, Math.cos(a) * 0.7, 1.6, Math.sin(a) * 0.7, 5);
    leg.rotation.set(Math.sin(a) * 0.22, 0, -Math.cos(a) * 0.22);
    g.add(leg);
  }
  const tube = rod(0.42, 5, p.brass, 0, 3.6, 0, 14);
  tube.rotation.z = 1.15;
  g.add(tube, rod(0.55, 0.5, p.charcoal, 2.2, 4.6, 0, 14).rotateZ(1.15));
  return g;
}

/**
 * A tall glass-fronted cabinet with a warm glowing back, so the old optical instruments inside read as
 * silhouettes from across the room.
 */
export function displayCabinet(rng: Rng, o: { width?: number; height?: number; depth?: number } = {}): THREE.Group {
  const p = palette();
  const W = o.width ?? 12;
  const H = o.height ?? 24;
  const D = o.depth ?? 4.4;
  const g = new THREE.Group();
  const levels = [1, 6.6, 12.2, 17.8, H - 0.6];
  const shell: THREE.Mesh[] = [
    box(W, 1, D, p.walnut, 0, 0.5, 0, 0.1),
    box(W + 0.4, 0.6, D + 0.3, p.walnut, 0, H - 0.3, 0, 0.1),
    box(0.6, H, D, p.walnut, -W / 2 + 0.3, H / 2, 0, 0.08),
    box(0.6, H, D, p.walnut, W / 2 - 0.3, H / 2, 0, 0.08),
  ];
  for (const y of levels.slice(1, -1)) shell.push(box(W - 1.2, 0.3, D - 0.6, p.walnut, 0, y, -0.1, 0.04));
  g.add(mergeStatic(shell, p.walnut));
  // The glowing back: warm, brightest at the top of each compartment.
  const glow = new THREE.MeshBasicNodeMaterial({ fog: false });
  glow.colorNode = vec4(0.24, 0.155, 0.085, 1).mul(float(0.55).add(uv().y.mul(0.45)));
  for (let k = 0; k < levels.length - 1; k++) {
    const h = levels[k + 1] - levels[k] - 0.3;
    g.add(mesh(new THREE.PlaneGeometry(W - 1.2, h), glow, 0, levels[k] + 0.15 + h / 2, -D / 2 + 0.1));
  }
  const items = [telescope, camera, lensOnStand, binoculars, storageBox, lensOnStand, camera];
  let n = 0;
  for (let k = 0; k < levels.length - 1; k++) {
    for (const x of k % 2 ? [-2.6, 2.4] : [0]) {
      const it = items[n++ % items.length]();
      it.position.set(x + (rng() - 0.5) * 0.8, levels[k] + 0.3, -0.2);
      it.rotation.y = (rng() - 0.5) * 0.8;
      g.add(it);
    }
  }
  // Two glass doors in thin brass frames.
  const glass = new THREE.MeshPhysicalNodeMaterial({ color: '#cfe0ea', roughness: 0.05, metalness: 0, transparent: true, opacity: 0.12, depthWrite: false });
  const frames: THREE.Mesh[] = [];
  for (const x of [-W / 4 + 0.15, W / 4 - 0.15]) {
    g.add(mesh(new THREE.PlaneGeometry(W / 2 - 0.9, H - 1.8), glass, x, H / 2 + 0.2, D / 2 + 0.02));
    frames.push(box(W / 2 - 0.6, 0.25, 0.2, p.brass, x, 1.25, D / 2 + 0.05, 0.05), box(W / 2 - 0.6, 0.25, 0.2, p.brass, x, H - 0.85, D / 2 + 0.05, 0.05));
    frames.push(box(0.25, H - 1.8, 0.2, p.brass, x - W / 4 + 0.3, H / 2 + 0.2, D / 2 + 0.05, 0.05), box(0.25, H - 1.8, 0.2, p.brass, x + W / 4 - 0.3, H / 2 + 0.2, D / 2 + 0.05, 0.05));
  }
  g.add(mergeStatic(frames, p.brass));
  g.add(contactShadow(W + 3, D + 3));
  return g;
}
