// Sets (style-bible §8). Each builder returns a group the look preset shows or hides: register it
// with stage.registerSet('room' | 'plinth' | 'cyclorama', group). 1 world unit = 10 cm.
//
// Lab: an optics workshop at dusk, a room finished on every side so the camera can circle it: a
// tall dusk window, shelving lit from below, a workbench wall, a display cabinet, a reading corner
// and a door (furniture.ts). Put the topic's background echo on the window anchor (one echo per
// set, kept out of focus, read by silhouette).

import * as THREE from 'three/webgpu';
import { cameraPosition, float, mix, normalView, positionLocal, positionView, positionWorld, smoothstep, uv, vec3, vec4 } from 'three/tsl';
import { darkSteel, emissiveStrip, followEnvironment, lacquer, machined } from './materials';
import { chamferedPlinth, lightStrip, makeRng, mergeStatic, roundedBox } from './geometry';
import {
  armchair, coatHook, credenza, deskLamp, displayCabinet, door, drawerCabinet, floorLamp, framedPrint, ladder,
  pegboard, pendant, plantGroup, rug, shelving, sideTable, stool, tallPlant, workbench,
} from './furniture';

export interface LabSetOptions {
  /** Background echo of the topic. Placed on anchors.window unless you place it yourself. */
  echo?: THREE.Object3D;
  /** Floor height (the bench stands on it). */
  floorY?: number;
  /** Inner walls: x = ±x, the north and south z, and the wall height. The bench sits near the origin. */
  size?: { x: number; north: number; south: number; height: number };
  /** Center of the tall window on the north wall (behind the bench in the first view). */
  windowX?: number;
  /** Brightness of the dusk sky in the window (0.55 by default, about a stop under the phenomenon). */
  windowGain?: number;
  /** Leave single pieces out; every piece is in by default. */
  pieces?: Partial<Record<LabPiece, boolean>>;
  seed?: number;
}

export type LabPiece = 'credenza' | 'shelving' | 'workbench' | 'cabinet' | 'reading' | 'door' | 'plants' | 'rug' | 'pendants';

export interface LabSet {
  group: THREE.Group;
  /**
   * Where the camera may go: the room's inner box shrunk by 2 units, open toward the front (+z) so a
   * narrow screen's hero camera can stand farther back (CameraRig view bounds).
   */
  bounds: THREE.Box3;
  /** The window pane, for an echo that shades the light coming through it. */
  window: { pane: THREE.Mesh; center: THREE.Vector3; width: number; height: number };
  anchors: { window: THREE.Object3D; wall: THREE.Object3D; shelf: THREE.Object3D; floor: THREE.Object3D };
  /**
   * Call every frame before rendering: hides each wall, with its furniture, that the camera stands behind
   * or within 1.4 m of. The camera faces the bench, so that wall is behind it, and the camera never ends up
   * inside a piece of furniture.
   */
  cutaway(camera: THREE.Camera): void;
}

function tileTexture(): THREE.CanvasTexture {
  const c = document.createElement('canvas');
  c.width = c.height = 512;
  const g = c.getContext('2d') as CanvasRenderingContext2D;
  const rng = makeRng(7);
  const n = 4;
  const s = 512 / n;
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      const v = 0.92 + rng() * 0.08;
      // Dark slate: a pale floor washes a third of every orbit view and lifts the blacks.
      g.fillStyle = `rgb(${Math.round(80 * v)},${Math.round(82 * v)},${Math.round(92 * v)})`;
      g.fillRect(i * s, j * s, s, s);
    }
  }
  g.strokeStyle = '#2e3038';
  g.lineWidth = 5;
  for (let i = 0; i <= n; i++) {
    g.beginPath(); g.moveTo(i * s, 0); g.lineTo(i * s, 512); g.stroke();
    g.beginPath(); g.moveTo(0, i * s); g.lineTo(512, i * s); g.stroke();
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.anisotropy = 8;
  return t;
}

/**
 * Window glass seen from inside at dusk: deep lavender above, lavender in the middle, a warm peach
 * band at the horizon. Bright but not emissive. No fog: fog would gray the sky into a neutral block.
 */
function duskPaneMaterial(height: number, gain: number): THREE.MeshBasicNodeMaterial {
  const m = new THREE.MeshBasicNodeMaterial({ fog: false });
  const k = smoothstep(-height / 2, height / 2, positionLocal.y);
  const horizon = vec3(0.92, 0.6, 0.5);
  const middle = vec3(0.5, 0.48, 0.86);
  const top = vec3(0.24, 0.26, 0.6);
  const sky = mix(mix(horizon, middle, smoothstep(0, 0.3, k)), top, smoothstep(0.3, 1, k));
  // About a stop under the beam's world: the window sets the hour, it does not compete.
  m.colorNode = vec4(sky.mul(float(gain)), 1);
  return m;
}

export function buildLabSet(o: LabSetOptions = {}): LabSet {
  const floorY = o.floorY ?? -9;
  const S = { x: 44, north: -36, south: 46, height: 44, ...o.size };
  const has = (k: LabPiece) => o.pieces?.[k] !== false;
  const rng = makeRng(o.seed ?? 3);
  const group = new THREE.Group();
  group.name = 'lab-set';
  // Each wall and the furniture against it form one side. A side whose outer face the camera stands
  // behind is cut away: the room's open front, for a camera that must stand farther back than the room
  // is deep (a narrow screen's hero framing).
  const side = { north: new THREE.Group(), south: new THREE.Group(), west: new THREE.Group(), east: new THREE.Group() };
  group.add(side.north, side.south, side.west, side.east);
  const W = S.x * 2;
  const depth = S.south - S.north;
  const midZ = (S.north + S.south) / 2;
  const place = <T extends THREE.Object3D>(obj: T, x: number, z: number, rotY = 0, y = 0, into: THREE.Object3D = group): T => {
    obj.position.set(x, floorY + y, z);
    obj.rotation.y = rotY;
    into.add(obj);
    return obj;
  };

  // Floor and ceiling
  const tiles = tileTexture();
  tiles.repeat.set(W / 7, depth / 7);
  const floorMat = followEnvironment(new THREE.MeshStandardNodeMaterial({ map: tiles, roughness: 0.74, metalness: 0 }), 0.7);
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(W, depth), floorMat);
  floor.rotation.x = -Math.PI / 2;
  floor.position.set(0, floorY, midZ);
  group.add(floor);
  const ceiling = new THREE.Mesh(new THREE.PlaneGeometry(W, depth), followEnvironment(new THREE.MeshStandardNodeMaterial({ color: '#171a22', roughness: 0.95, metalness: 0 }), 0.8));
  ceiling.rotation.x = Math.PI / 2;
  ceiling.position.set(0, floorY + S.height, midZ);
  group.add(ceiling);

  // Four walls, each plaster above a dark wood wainscot with a chair rail and a baseboard. A wall's
  // local +z faces into the room.
  const plaster = followEnvironment(new THREE.MeshStandardNodeMaterial({ color: '#2b3040', roughness: 0.88, metalness: 0 }), 1);
  const wainscot = lacquer('#241c17');
  const trimMat = lacquer('#1a1d24');
  const walls: [number, number, number, number, THREE.Group][] = [
    [0, S.north, W, 0, side.north],
    [0, S.south, W, Math.PI, side.south],
    [-S.x, midZ, depth, Math.PI / 2, side.west],
    [S.x, midZ, depth, -Math.PI / 2, side.east],
  ];
  for (const [cx, cz, len, rotY, into] of walls) {
    const wall = new THREE.Group();
    const upper = new THREE.Mesh(new THREE.BoxGeometry(len, S.height, 0.6), plaster);
    upper.position.set(0, S.height / 2, -0.3);
    const low = new THREE.Mesh(roundedBox(len, 9, 0.5, 0.06, 2), wainscot);
    low.position.set(0, 4.5, 0.25);
    const rail = new THREE.Mesh(roundedBox(len, 0.5, 0.7, 0.08, 2), trimMat);
    rail.position.set(0, 9.1, 0.35);
    const base = new THREE.Mesh(roundedBox(len, 1.1, 0.7, 0.08, 2), trimMat);
    base.position.set(0, 0.55, 0.35);
    wall.add(upper, low, rail, base);
    place(wall, cx, cz, rotY, 0, into);
  }

  // North: a tall steel-framed dusk window, flanked by amber light bars, a credenza under it.
  const winW = 18;
  const winH = 28;
  const winX = o.windowX ?? 4;
  const sillY = floorY + 12;
  const winY = sillY + winH / 2;
  const zN = S.north;
  const pane = new THREE.Mesh(new THREE.PlaneGeometry(winW, winH), duskPaneMaterial(winH, o.windowGain ?? 0.55));
  pane.position.set(winX, winY, zN + 0.05);
  side.north.add(pane);
  const frameMat = darkSteel('#3a3f49');
  const bars: THREE.Mesh[] = [];
  const bar = (w: number, h: number, x: number, y: number) => {
    const b = new THREE.Mesh(roundedBox(w, h, 0.5, 0.06), frameMat);
    b.position.set(x, y, zN + 0.3);
    bars.push(b);
  };
  bar(winW + 1, 0.8, winX, winY + winH / 2);
  bar(winW + 1, 1, winX, winY - winH / 2);
  bar(0.8, winH, winX - winW / 2, winY);
  bar(0.8, winH, winX + winW / 2, winY);
  for (const k of [1, 2]) bar(0.35, winH, winX - winW / 2 + (winW * k) / 3, winY);
  for (const k of [1, 2, 3]) bar(winW, 0.35, winX, winY - winH / 2 + (winH * k) / 4);
  side.north.add(mergeStatic(bars, frameMat));
  const sill = new THREE.Mesh(roundedBox(winW + 2.4, 0.5, 1.6, 0.1), lacquer('#d8d2c6'));
  sill.position.set(winX, sillY - 0.75, zN + 0.8);
  side.north.add(sill);
  // A soft shaft of dusk light falls from the window, down and to the left, onto the floor beside the bench
  // (theatre: drawn, not lit). Its faces fade as they turn edge-on, so it reads as a volume from every side.
  const shaftDir = new THREE.Vector3(-0.35, -0.75, 0.56).normalize();
  const shaftLen = 56;
  const shaftMat = new THREE.MeshBasicNodeMaterial({ transparent: true, depthWrite: false, fog: false, side: THREE.DoubleSide, blending: THREE.AdditiveBlending });
  const along = positionLocal.y.div(shaftLen);
  const facing = normalView.dot(positionView.normalize()).abs().pow(2.5);
  // Haze scatters light mostly forward: the shaft glows when you look toward the window and all but vanishes
  // seen from the side, so a side view's floor is not washed by it.
  const forward = smoothstep(-0.6, 0.6, cameraPosition.sub(positionWorld).normalize().dot(vec3(shaftDir.x, shaftDir.y, shaftDir.z)));
  shaftMat.colorNode = vec4(vec3(0.62, 0.58, 0.95), facing.mul(forward).mul(smoothstep(0, 0.1, along)).mul(float(1).sub(smoothstep(0.3, 0.9, along))).mul(0.08));
  const shaft = new THREE.Mesh(new THREE.CylinderGeometry(1.2, 1, 1, 32, 1, true).translate(0, 0.5, 0).scale(winW * 0.45, shaftLen, winH * 0.3), shaftMat);
  shaft.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), shaftDir);
  shaft.position.set(winX, winY, zN + 0.4);
  side.north.add(shaft);
  // Where it lands, dusk light lies across the floor: from the sill's ray to the top's.
  const land = (y: number) => (y - floorY) / -shaftDir.y;
  const reach = (land(sillY) + land(sillY + winH)) / 2;
  const spillMat = new THREE.MeshBasicNodeMaterial({ transparent: true, depthWrite: false, fog: false, blending: THREE.AdditiveBlending });
  const across = float(1).sub(uv().x.sub(0.5).abs().mul(2)).clamp(0, 1);
  const lengthwise = float(1).sub(smoothstep(0.3, 1, uv().y.sub(0.5).abs().mul(2)));
  spillMat.colorNode = vec4(vec3(0.5, 0.48, 0.86).mul(0.05), lengthwise.mul(across));
  const spillLen = (land(sillY + winH) - land(sillY)) * Math.hypot(shaftDir.x, shaftDir.z) + 6;
  const spill = new THREE.Mesh(new THREE.PlaneGeometry(winW + 2, spillLen).rotateX(-Math.PI / 2), spillMat);
  spill.rotation.y = Math.atan2(shaftDir.x, shaftDir.z);
  spill.position.set(winX + shaftDir.x * reach, floorY + 0.06, zN + shaftDir.z * reach);
  group.add(spill);
  const barMat = emissiveStrip('#ffc78c', 1.7);
  for (const x of [winX - 17, winX + 17]) {
    const lb = new THREE.Mesh(lightStrip({ length: 22, thickness: 0.07, depth: 0.06 }), barMat);
    lb.rotation.z = Math.PI / 2;
    lb.position.set(x, floorY + 21, zN + 0.25);
    const housing = new THREE.Mesh(roundedBox(0.5, 23, 0.3, 0.08), trimMat);
    housing.position.set(x, floorY + 21, zN + 0.12);
    side.north.add(housing, lb);
  }
  if (has('credenza')) place(credenza(rng, { width: 20 }), winX, zN + 2.85, 0, 0, side.north);

  // North-east: open shelving lit from below, with a library ladder.
  const shelfAnchor = new THREE.Object3D();
  if (has('shelving')) {
    const shelfX = S.x - 12.2;
    place(shelving(rng, { bays: 3 }), shelfX, zN + 2.2, 0, 0, side.north);
    shelfAnchor.position.set(shelfX + 4, floorY + 15.5, zN + 2.2);
    const lad = place(ladder({ height: 31 }), shelfX - 5, zN + 9.4, 0, 0, side.north);
    lad.rotation.x = -0.16;
  }

  // West: the workbench under a pegboard, a lit desk lamp, a drawer cabinet and a stool.
  if (has('workbench')) {
    place(workbench(rng, { length: 26 }), -S.x + 4.1, 2, Math.PI / 2, 0, side.west);
    place(pegboard({ width: 22, height: 12 }), -S.x + 0.6, 2, Math.PI / 2, 11, side.west);
    place(deskLamp().group, -S.x + 2.4, 11, Math.PI / 2, 9, side.west);
    place(drawerCabinet(), -S.x + 2.9, -18, Math.PI / 2, 0, side.west);
    place(stool(), -S.x + 12, 5, 0, 0, side.west);
  }

  // East: a glass-fronted display cabinet glowing warm inside.
  if (has('cabinet')) place(displayCabinet(rng), S.x - 2.9, 2, -Math.PI / 2, 0, side.east);

  // South: a reading corner under a framed print, the door and a lab coat.
  if (has('reading')) {
    place(armchair(), 2, S.south - 7.5, Math.PI + 0.25, 0, side.south);
    place(sideTable(), -6, S.south - 6.5, Math.PI, 0, side.south);
    place(floorLamp().group, 10.5, S.south - 5, Math.PI, 0, side.south);
    place(framedPrint({ width: 15, height: 10.5, seed: 9 }), 2, S.south - 0.4, Math.PI, 20, side.south);
  }
  if (has('door')) {
    place(door(), -28, S.south - 0.3, Math.PI, 0, side.south);
    place(coatHook(), -17, S.south - 0.3, Math.PI, 0, side.south);
  }

  // Plants stand in the corners, never loose in the foreground of the first view.
  if (has('plants')) {
    // Just left of the window's left light bar, where the first view's left side would otherwise be bare wall.
    place(tallPlant(rng, { height: 20 }), winX - 21, zN + 5, 0, 0, side.north);
    place(plantGroup(rng), S.x - 9, S.south - 7, Math.PI, 0, side.south);
  }
  if (has('rug')) place(rug({ width: 36, depth: 26 }), 0, 2, 0, 0.03);
  if (has('pendants')) for (const x of [-7, 7]) place(pendant({ drop: 16 }), x, 0, 0, S.height);

  // Only the floor and the rug take the key light's shadows; the room casts none (the shadow map
  // covers the bench), and contact decals ground the furniture.
  group.traverse((m) => {
    if ((m as THREE.Mesh).isMesh) {
      m.castShadow = false;
      m.receiveShadow = m === floor || m.parent === group && (m as THREE.Mesh).geometry.type === 'PlaneGeometry' && m.position.y < floorY + 0.1;
    }
  });

  // Anchors for the topic's echo
  const windowAnchor = new THREE.Object3D();
  windowAnchor.position.set(winX, winY, zN + 0.4);
  const wallAnchor = new THREE.Object3D();
  wallAnchor.position.set(winX, floorY + 19, zN + 0.35);
  const floorAnchor = new THREE.Object3D();
  floorAnchor.position.set(-18, floorY, zN + 7);
  group.add(windowAnchor, wallAnchor, floorAnchor, shelfAnchor);
  if (o.echo) windowAnchor.add(o.echo);

  // Open toward the front (+z): the south side cuts away when the camera stands behind it.
  const bounds = new THREE.Box3(new THREE.Vector3(-S.x + 2, floorY + 2, S.north + 2), new THREE.Vector3(S.x - 2, floorY + S.height - 2, Infinity));
  const planes: [THREE.Group, number, number, number][] = [
    [side.north, 0, 1, S.north], [side.south, 0, -1, S.south], [side.west, 1, 0, -S.x], [side.east, -1, 0, S.x],
  ];
  const eye = new THREE.Vector3();
  // Deeper than any piece stands out from its wall (the stool, 13.7 units from the west wall).
  const NEAR = 14;
  return {
    group,
    bounds,
    window: { pane, center: new THREE.Vector3(winX, winY, zN + 0.05), width: winW, height: winH },
    anchors: { window: windowAnchor, wall: wallAnchor, shelf: shelfAnchor, floor: floorAnchor },
    cutaway(camera) {
      camera.getWorldPosition(eye);
      for (const [g, nx, nz, at] of planes) g.visible = (nx ? (eye.x - at) * nx : (eye.z - at) * nz) > NEAR;
    },
  };
}

// ---------- Exploded: plinth in a navy void ----------

function plateTexture(lines: string[]): THREE.CanvasTexture {
  const c = document.createElement('canvas');
  c.width = 512;
  c.height = 128;
  const g = c.getContext('2d') as CanvasRenderingContext2D;
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const draw = () => {
    const grad = g.createLinearGradient(0, 0, 512, 128);
    grad.addColorStop(0, '#c9944a');
    grad.addColorStop(0.5, '#b7843d');
    grad.addColorStop(1, '#a87736');
    g.fillStyle = grad;
    g.fillRect(0, 0, 512, 128);
    for (let y = 0; y < 128; y += 2) {
      g.fillStyle = `rgba(255,240,210,${0.03 + ((y * 7919) % 13) / 400})`;
      g.fillRect(0, y, 512, 1);
    }
    g.fillStyle = '#4a3216';
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.font = '600 44px "DM Mono", ui-monospace, monospace';
    g.fillText(lines[0] ?? '', 256, lines.length > 1 ? 50 : 64);
    if (lines[1]) {
      g.font = '500 24px "DM Mono", ui-monospace, monospace';
      g.fillText(lines[1], 256, 96);
    }
    tex.needsUpdate = true;
  };
  draw();
  if (typeof document !== 'undefined' && document.fonts) void document.fonts.ready.then(draw);
  return tex;
}

export interface ExplodedStageOptions {
  /** Engraved maker plate lines: Daniel's mark, never the reference creator's. */
  makerPlate: string[];
  width?: number;
  depth?: number;
  height?: number;
}

export function buildExplodedStage(o: ExplodedStageOptions): { group: THREE.Group; top: number } {
  const W = o.width ?? 24;
  const D = o.depth ?? 10;
  const H = o.height ?? 2.6;
  const group = new THREE.Group();
  group.name = 'exploded-stage';

  const plinth = new THREE.Mesh(chamferedPlinth({ width: W, depth: D, height: H, slope: H * 0.55, radius: 0.08 }), lacquer('#0c0e13'));
  plinth.castShadow = plinth.receiveShadow = true;
  group.add(plinth);
  // Warm LED strips under the top lip and along the bottom edge.
  // Thin warm lines, well below any phenomenon's brightness: a long bright strip at the base would
  // compete with the beam for the eye.
  const led = emissiveStrip('#ffd9a0', 1.2);
  const topInset = H * 0.55;
  const lip = new THREE.Mesh(lightStrip({ length: W - 2 * topInset - 0.6, thickness: 0.04, depth: 0.04 }), led);
  lip.position.set(0, H - 0.16, D / 2 - topInset + 0.02);
  const foot = new THREE.Mesh(lightStrip({ length: W - 0.8, thickness: 0.035, depth: 0.04 }), led);
  foot.position.set(0, 0.07, D / 2 + 0.02);
  group.add(lip, foot);
  // Brass maker plate on the sloped front face.
  const plateMat = machined('#ffffff');
  plateMat.map = plateTexture(o.makerPlate);
  const plate = new THREE.Mesh(roundedBox(3.2, 0.8, 0.06, 0.03, 2), plateMat);
  const slopeAngle = Math.atan2(topInset, H);
  plate.position.set(W * 0.3, H * 0.48, D / 2 - topInset * 0.48 + 0.05);
  plate.rotation.x = -slopeAngle;
  group.add(plate);
  // The void and its haze are the look's screen-space backdrop (looks.ts), the same from every angle.
  return { group, top: H };
}

// ---------- Studio: warm paper bowl ----------

/**
 * A paper floor that curves up into a wall all the way around the bench, so every orbit angle shows
 * paper. `radius` must exceed the Studio camera's farthest reach (hero distance × zoom-out, plus pan).
 */
export function buildStudioSet(o: { radius?: number; height?: number; cove?: number; floorY?: number } = {}): THREE.Group {
  const R = o.radius ?? 72;
  const H = o.height ?? 50;
  const C = o.cove ?? 14;
  const y0 = o.floorY ?? -9;
  // Profile in (r, y): floor → cove → wall, lathed around the vertical axis.
  const prof: THREE.Vector2[] = [];
  for (let r = 0; r < R - C; r += 6) prof.push(new THREE.Vector2(r, y0));
  for (let i = 0; i <= 16; i++) {
    const a = (i / 16) * (Math.PI / 2);
    prof.push(new THREE.Vector2(R - C + Math.sin(a) * C, y0 + C - Math.cos(a) * C));
  }
  prof.push(new THREE.Vector2(R, y0 + H));
  // Only the inside of the bowl is drawn (the lathe's outer faces are its front): a camera that must
  // stand farther back than the bowl is wide (a narrow screen) looks in through the near wall.
  const paper = followEnvironment(new THREE.MeshStandardNodeMaterial({ color: '#efe3d3', roughness: 0.93, metalness: 0, side: THREE.BackSide }), 0.6);
  const bowl = new THREE.Mesh(new THREE.LatheGeometry(prof, 96), paper);
  bowl.receiveShadow = true;
  const g = new THREE.Group();
  g.name = 'studio-set';
  g.add(bowl);
  return g;
}
