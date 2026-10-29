// World labels (style-bible §11): a dark pill anchored to a part with a colored dot, a short bold
// name and a terse lowercase live value in mono ("Analyzer · 90°"). Rendered with CSS2DRenderer
// in a layer above the canvas, so depth of field never blurs them; they fade with distance and
// when their anchor is hidden behind something (checked about 8 times a second, not per frame).
// Pills never stack: one that crowds a stronger pill, the HUD, a loupe or the frame's edge fades instead.

import * as THREE from 'three/webgpu';
import { CSS2DObject, CSS2DRenderer } from 'three/addons/renderers/CSS2DRenderer.js';

export interface LabelOptions {
  name: string;
  /** Live value, terse and lowercase. Called every frame; the DOM changes only when the text does. */
  value?: () => string;
  /** CSS color or a token name such as 'cyan' or 'amber'. */
  color?: string;
  /** World offset from the anchor to the pill. */
  offset?: THREE.Vector3Like;
  /** Thin leader line from the pill to the anchor. */
  leader?: boolean;
  /** Fade out between these camera distances (world units). */
  fade?: [number, number];
  /** Set false for labels that should never fade when occluded. */
  occlude?: boolean;
  /**
   * When two pills come closer than 12 px on screen, the lower priority one fades out (phones, close-ups,
   * views along the bench, where parts line up one behind another).
   */
  priority?: number;
}

interface Item {
  anchor: THREE.Object3D;
  object: CSS2DObject;
  el: HTMLElement;
  valueEl: HTMLElement;
  nameEl: HTMLElement;
  leaderEl: HTMLElement | null;
  opts: LabelOptions;
  last: string;
  occluded: boolean;
  crowded: boolean;
  /** Anchor inside the viewport. */
  onScreen: boolean;
  /** Last opacity written: style.opacity reads "1" back for "1.00", so it cannot be the guard. */
  opacity: string;
  /** Half the pill's height, measured once (reading offsetHeight every frame forces a layout). */
  half: number;
}

export interface WorldLabelOptions {
  /**
   * HUD elements labels must not overlap: a label whose pill would cover one fades out, like a
   * crowded label. Defaults to the house HUD's title column, control card and bottom-right tools.
   */
  avoid?: string;
}

/** Something drawn in the scene that labels fade rather than cover, such as a loupe. */
export interface LabelObstacle {
  /** Its circle on screen in CSS pixels, or null while it is not drawn. */
  screenCircle(width: number, height: number): { x: number; y: number; r: number } | null;
}

/** Clear space between two pills: any closer and a column of labels reads as one stack. */
const PILL_GAP = 12;
/** Clear space a label keeps from an obstacle, unless it names the part the obstacle shows. */
const OBSTACLE_GAP = 10;
/** A pill this close to the frame's edge is cut by it, or about to be. */
const EDGE = 4;

export interface LabelHandle {
  setName(name: string): void;
  setColor(color: string): void;
  setVisible(on: boolean): void;
  remove(): void;
}

const TOKENS = new Set(['cyan', 'amber', 'coral', 'violet', 'green']);

export class WorldLabels {
  readonly renderer: CSS2DRenderer;
  private items: Item[] = [];
  private scene: THREE.Scene;
  private camera: THREE.Camera;
  private raycaster = new THREE.Raycaster();
  private occluders: THREE.Object3D[] = [];
  private occluderAge = Infinity;
  private sinceCheck = 0;
  private shown = true;
  private tmpA = new THREE.Vector3();
  private tmpB = new THREE.Vector3();
  private projA = new THREE.Vector3();
  private projB = new THREE.Vector3();
  private dir = new THREE.Vector3();
  private width = 1;
  private height = 1;
  private root: HTMLElement;
  private avoid: string;
  private obstacles: { obstacle: LabelObstacle; caption: Item | undefined }[] = [];
  private byHandle = new WeakMap<LabelHandle, Item>();

  constructor(scene: THREE.Scene, camera: THREE.Camera, root: HTMLElement, o: WorldLabelOptions = {}) {
    this.scene = scene;
    this.camera = camera;
    this.root = root;
    this.avoid = o.avoid ?? '.side > *, .ctl, .br > *';
    this.renderer = new CSS2DRenderer();
    const layer = this.renderer.domElement;
    layer.className = 'labels-layer';
    layer.style.position = 'absolute';
    layer.style.inset = '0';
    layer.style.pointerEvents = 'none';
    const veil = root.querySelector('#veil');
    root.insertBefore(layer, veil);
    this.setSize(root.clientWidth, root.clientHeight);
  }

  add(anchor: THREE.Object3D, o: LabelOptions): LabelHandle {
    const el = document.createElement('div');
    el.className = 'wl';
    const dot = document.createElement('span');
    dot.className = 'dot';
    const nameEl = document.createElement('span');
    nameEl.className = 'wn';
    nameEl.textContent = o.name;
    const valueEl = document.createElement('span');
    valueEl.className = 'wv';
    el.append(dot, nameEl, valueEl);
    let leaderEl: HTMLElement | null = null;
    if (o.leader) {
      leaderEl = document.createElement('div');
      leaderEl.className = 'wl-leader';
      el.append(leaderEl);
    }
    const object = new CSS2DObject(el);
    object.position.copy(o.offset ?? { x: 0, y: 0.6, z: 0 });
    anchor.add(object);
    const item: Item = {
      anchor, object, el, valueEl, nameEl, leaderEl, opts: o, last: '', occluded: false, crowded: false, onScreen: true, opacity: '', half: 0,
    };
    const setColor = (c: string) => el.style.setProperty('--dot', TOKENS.has(c) || /^[a-z][a-z0-9-]*$/.test(c) ? `var(--${c})` : c);
    setColor(o.color ?? 'cyan');
    this.items.push(item);
    const handle: LabelHandle = {
      setName: (n) => { nameEl.textContent = n; },
      setColor,
      setVisible: (on) => { object.visible = on; },
      remove: () => {
        anchor.remove(object);
        this.items = this.items.filter((i) => i !== item);
      },
    };
    this.byHandle.set(handle, item);
    return handle;
  }

  /**
   * Labels fade rather than cover this (checked with the declutter, about 8 times a second). `caption`, the
   * label of the part it shows, may sit against its rim but never on it; every other label keeps 10 px clear.
   */
  keepClearOf(obstacle: LabelObstacle, caption?: LabelHandle): void {
    this.obstacles.push({ obstacle, caption: caption && this.byHandle.get(caption) });
  }

  setVisible(on: boolean): void {
    this.shown = on;
    this.renderer.domElement.style.visibility = on ? 'visible' : 'hidden';
  }

  get visible(): boolean { return this.shown; }

  setSize(w: number, h: number): void {
    this.width = w;
    this.height = h;
    this.renderer.setSize(w, h);
  }

  /** Call every frame after the camera moved, before render(). */
  update(dt: number): void {
    if (!this.shown) return;
    this.sinceCheck += dt;
    this.occluderAge += dt;
    const check = this.sinceCheck > 0.12;
    if (check) this.sinceCheck = 0;
    if (check && this.occluderAge > 2) this.collectOccluders();
    const camPos = this.camera.getWorldPosition(this.tmpB);
    for (const it of this.items) {
      if (it.opts.value) {
        const v = it.opts.value();
        if (v !== it.last) {
          it.last = v;
          it.valueEl.textContent = v;
        }
      }
      const anchorPos = it.anchor.getWorldPosition(this.tmpA);
      const dist = anchorPos.distanceTo(camPos);
      if (check && it.opts.occlude !== false) it.occluded = this.isOccluded(anchorPos, camPos, dist);
      // A label whose part has left the frame goes too, rather than pointing at nothing.
      const a = this.projA.copy(anchorPos).project(this.camera);
      it.onScreen = a.z < 1 && Math.abs(a.x) < 1.02 && Math.abs(a.y) < 1.02;
      const [f0, f1] = it.opts.fade ?? [60, 110];
      const fade = 1 - THREE.MathUtils.smoothstep(dist, f0, f1);
      const opacity = it.crowded || !it.onScreen ? 0 : fade * (it.occluded ? 0.28 : 1);
      const s = opacity.toFixed(2);
      if (it.opacity !== s) it.el.style.opacity = it.opacity = s;
      if (it.leaderEl && s !== '0.00') this.placeLeader(it, a);
    }
    if (check) this.declutter();
  }

  /** Keep higher-priority labels when pills crowd each other, the HUD, an obstacle or the frame's edge; fade the rest. */
  private declutter(): void {
    const order = this.items.filter((i) => i.object.visible).sort((a, b) => (b.opts.priority ?? 0) - (a.opts.priority ?? 0));
    const hud: DOMRect[] = [];
    for (const e of this.root.querySelectorAll(this.avoid)) {
      const r = e.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) hud.push(r);
    }
    // Obstacles report canvas pixels; pills are measured in the page.
    const o = this.root.getBoundingClientRect();
    const discs: { x: number; y: number; r: number; caption: Item | undefined }[] = [];
    for (const { obstacle, caption } of this.obstacles) {
      const c = obstacle.screenCircle(this.width, this.height);
      if (c) discs.push({ x: c.x + o.left, y: c.y + o.top, r: c.r, caption });
    }
    const kept: DOMRect[] = [];
    for (const it of order) {
      const r = it.el.getBoundingClientRect();
      const hit = r.width > 0 && (
        r.left < o.left + EDGE || r.right > o.right - EDGE || r.top < o.top + EDGE || r.bottom > o.bottom - EDGE
        || hud.some((k) => r.left < k.right + 4 && r.right > k.left - 4 && r.top < k.bottom + 3 && r.bottom > k.top - 3)
        || kept.some((k) => r.left < k.right + PILL_GAP && r.right > k.left - PILL_GAP && r.top < k.bottom + PILL_GAP && r.bottom > k.top - PILL_GAP)
        || discs.some((c) => Math.hypot(Math.max(r.left - c.x, 0, c.x - r.right), Math.max(r.top - c.y, 0, c.y - r.bottom)) < c.r + (it === c.caption ? 0 : OBSTACLE_GAP))
      );
      it.crowded = hit;
      if (!hit && r.width > 0) kept.push(r);
    }
  }

  render(): void {
    if (this.shown) this.renderer.render(this.scene, this.camera);
  }

  /** `a` is the anchor in normalized device coordinates. */
  private placeLeader(it: Item, a: THREE.Vector3): void {
    const l = it.object.getWorldPosition(this.projB).project(this.camera);
    if (it.half === 0) it.half = it.el.offsetHeight / 2;
    const dx = ((a.x - l.x) / 2) * this.width;
    // The leader leaves from the pill's edge that faces the anchor, so it never crosses the text.
    const cy = (-(a.y - l.y) / 2) * this.height;
    const above = cy < 0;
    const dy = above ? cy + it.half : cy - it.half;
    const len = Math.abs(cy) < it.half ? 0 : Math.max(0, Math.hypot(dx, dy) - 2);
    const ang = Math.atan2(dy, dx) - Math.PI / 2;
    it.leaderEl!.style.top = above ? '0' : '100%';
    it.leaderEl!.style.height = `${len.toFixed(1)}px`;
    it.leaderEl!.style.transform = `rotate(${ang.toFixed(4)}rad)`;
  }

  private collectOccluders(): void {
    this.occluderAge = 0;
    const list: THREE.Object3D[] = [];
    this.scene.traverseVisible((o) => {
      const m = o as THREE.Mesh;
      if (!m.isMesh || (o as THREE.InstancedMesh).isInstancedMesh || o.userData.occluder === false) return;
      const mat = m.material as THREE.Material;
      if (Array.isArray(mat) || mat.transparent || mat.blending === THREE.AdditiveBlending) return;
      list.push(o);
    });
    this.occluders = list;
  }

  private isOccluded(anchor: THREE.Vector3, cam: THREE.Vector3, dist: number): boolean {
    const dir = this.dir.copy(anchor).sub(cam).normalize();
    this.raycaster.set(cam, dir);
    this.raycaster.far = dist - 0.25;
    return this.raycaster.intersectObjects(this.occluders, false).length > 0;
  }
}
