// CameraRig: hero framing per view, orbit/pan/zoom with per-view limits and room bounds, idle drift,
// eased flights, W A S D / Q E flying, and the focus distance depth of field reads (style-bible §10).
//
// A pose orbits a target: position = target + pan + distance × (sin yaw cos pitch, sin pitch,
// cos yaw cos pitch). yaw 0 looks down −Z; positive pitch is above. The hero distance comes from a
// fit box (world units) and the free screen region the HUD leaves (safeArea), and a pure pan
// shifts the subject into the middle of that region. Yaw is unbounded by default, so it counts whole
// turns; flights take the short way round.

import * as THREE from 'three/webgpu';
import type { SafeArea } from './hud';
import { prefersReducedMotion } from './a11y';

export type Vec3 = [number, number, number];

export interface CameraPose {
  target: Vec3;
  yaw: number;
  pitch: number;
  distance: number;
  /** World-space pan offset added to target and position. */
  pan?: Vec3;
  fov?: number;
}

export interface HeroShot {
  target: Vec3;
  /** Radians; slightly off axis reads as three-quarter (about −0.2 to 0.14). */
  yaw: number;
  /** Radians; slightly elevated (about 0.03 to 0.12, higher for a bench seen from above). */
  pitch: number;
  /** World-unit box the subject must fill between the HUD columns. */
  fit: { width: number; height: number };
  fov?: number;
  /** Box the camera stays inside (a room's inner walls): the distance shortens to keep it there. */
  bounds?: THREE.Box3;
  limits?: {
    /** Yaw range relative to the hero yaw; null (the default) leaves yaw unbounded. */
    yaw?: [number, number] | null;
    pitch?: [number, number];
    /** Distance range as factors of the hero distance. */
    zoom?: [number, number];
    /** Largest pan from the hero framing, world units. */
    pan?: number;
  };
}

/** Shot-relative pose: any field missing comes from the view's hero; distanceScale multiplies the hero distance. */
export type PoseSpec = Partial<CameraPose> & { distanceScale?: number };

interface RigOptions {
  safeArea?: () => SafeArea;
  /** Return false to let the app take this pointer (dragging a part). */
  interactionFilter?: (e: PointerEvent) => boolean;
  /** Seconds without input before idle drift starts. */
  idleDelay?: number;
}

const DEFAULT_LIMITS = { yaw: null as [number, number] | null, pitch: [-0.08, 1.3] as [number, number], zoom: [0.4, 1.7] as [number, number] };

const easeInOutCubic = (k: number) => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2);

/** Clamp to [lo, hi] widened to include `keep`, so a value already outside the range never jumps into it. */
const clampAround = (v: number, lo: number, hi: number, keep: number) => THREE.MathUtils.clamp(v, Math.min(lo, keep), Math.max(hi, keep));

/** An angle wrapped to (−π, π]: the short way round between two yaws. */
export function wrapPi(a: number): number {
  return a - 2 * Math.PI * Math.ceil((a - Math.PI) / (2 * Math.PI));
}

/**
 * The largest distance up to `distance` that keeps origin + dir × d inside the box, never below
 * minDistance. `origin` is the look point (target + pan) and `dir` the unit vector toward the camera.
 * Slab method: the ray leaves the box at the nearest of the three far slab planes.
 */
/** How far apart two world points lie in depth along a unit `forward` (the camera's view direction). */
export function depthSpan(a: THREE.Vector3, b: THREE.Vector3, forward: THREE.Vector3): number {
  return Math.abs(a.dot(forward) - b.dot(forward));
}

export function clampDistanceToBox(origin: THREE.Vector3, dir: THREE.Vector3, distance: number, box: THREE.Box3, minDistance: number): number {
  let d = distance;
  for (const k of ['x', 'y', 'z'] as const) {
    if (dir[k] > 0) d = Math.min(d, (box.max[k] - origin[k]) / dir[k]);
    else if (dir[k] < 0) d = Math.min(d, (box.min[k] - origin[k]) / dir[k]);
  }
  return Math.max(minDistance, d);
}

interface State { target: THREE.Vector3; yaw: number; pitch: number; distance: number; pan: THREE.Vector3; fov: number }

function cloneState(s: State): State {
  return { target: s.target.clone(), yaw: s.yaw, pitch: s.pitch, distance: s.distance, pan: s.pan.clone(), fov: s.fov };
}

function copyState(to: State, s: State): void {
  to.target.copy(s.target);
  to.yaw = s.yaw;
  to.pitch = s.pitch;
  to.distance = s.distance;
  to.pan.copy(s.pan);
  to.fov = s.fov;
}

export class CameraRig {
  readonly camera: THREE.PerspectiveCamera;
  readonly dom: HTMLElement;
  views: Record<string, HeroShot>;
  view: string;
  enabled = true;
  /** Optional focus point override for depth of field (defaults to the orbit target). */
  focusPoint: THREE.Vector3 | null = null;
  /**
   * The two ends of the apparatus, kept sharp together: an exhibit widens its depth-of-field band by
   * focusSpanDepth, so a camera looking along the rail keeps the whole rail in focus.
   */
  focusSpan: [THREE.Vector3, THREE.Vector3] | null = null;
  /** Idle drift on or off (off for deterministic captures). Reduced motion always turns it off. */
  idleDrift = true;

  private opts: RigOptions;
  private goal: State;
  private cur: State;
  private hero: State;
  private flight: { from: State; to: State; t: number; dur: number; resolve: () => void } | null = null;
  private lastInput = -Infinity;
  private clock = 0;
  private drift = 0;
  private driftStart = 0;
  private atHero = true;
  private fly = new Set<string>();
  private pointers = new Map<number, { x: number; y: number }>();
  private dragMode: 'orbit' | 'pan' | null = null;
  private pinch = 0;
  /** The distance as drawn, after bounds (apply): what depth of field, pan and fly speeds read. */
  private drawn = 0;
  private inputCbs = new Set<() => void>();
  private cleanup: (() => void)[] = [];
  private tmp = new THREE.Vector3();
  private off = new THREE.Vector3();
  private origin = new THREE.Vector3();
  private axes = { dir: new THREE.Vector3(), right: new THREE.Vector3(), up: new THREE.Vector3() };
  /** The goal before the input being applied (pan: length of its pan offset); clampGoal widens each limit to include it. */
  private before = { yaw: 0, pitch: 0, distance: 0, pan: 0 };

  constructor(camera: THREE.PerspectiveCamera, dom: HTMLElement, views: Record<string, HeroShot>, opts: RigOptions = {}) {
    this.camera = camera;
    this.dom = dom;
    this.views = views;
    this.opts = opts;
    this.view = Object.keys(views)[0];
    this.hero = this.computeHero(this.view);
    this.goal = cloneState(this.hero);
    this.cur = cloneState(this.hero);
    this.bind();
    this.apply();
  }

  /** Called on every user camera input (stop the cinematic, stop drift). */
  onInput(cb: () => void): () => void {
    this.inputCbs.add(cb);
    return () => this.inputCbs.delete(cb);
  }

  /** View-space depth of the focus point (what depth of field compares against). */
  /** How far apart the focus span's ends lie in depth from the camera (0 without a span). */
  get focusSpanDepth(): number {
    return this.focusSpan ? depthSpan(this.focusSpan[0], this.focusSpan[1], this.camera.getWorldDirection(this.tmp)) : 0;
  }

  get focusDistance(): number {
    if (!this.focusPoint) return this.drawn || this.cur.distance;
    const forward = this.camera.getWorldDirection(this.tmp);
    return Math.max(0.1, this.focusPoint.dot(forward) - this.camera.position.dot(forward));
  }

  get pose(): CameraPose {
    const c = this.cur;
    return { target: c.target.toArray() as Vec3, yaw: c.yaw, pitch: c.pitch, distance: c.distance, pan: c.pan.toArray() as Vec3, fov: c.fov };
  }

  /** The hero pose of a view at the current viewport size. */
  heroPose(name = this.view): CameraPose {
    const h = this.computeHero(name);
    return { target: h.target.toArray() as Vec3, yaw: h.yaw, pitch: h.pitch, distance: h.distance, pan: h.pan.toArray() as Vec3, fov: h.fov };
  }

  /** Resolve a shot-relative pose against a view's hero. */
  resolve(spec: PoseSpec, name = this.view): CameraPose {
    const h = this.heroPose(name);
    return {
      target: spec.target ?? h.target,
      yaw: spec.yaw ?? h.yaw,
      pitch: spec.pitch ?? h.pitch,
      distance: spec.distance ?? h.distance * (spec.distanceScale ?? 1),
      pan: spec.pan ?? (spec.target ? [0, 0, 0] : h.pan),
      fov: spec.fov ?? h.fov,
    };
  }

  /** A pose relative to the view's hero, in degrees: yaw added to the hero yaw, absolute pitch (URL orbit=). */
  orbitPose(yawDeg: number, pitchDeg: number, distanceScale?: number): CameraPose {
    return this.resolve({ yaw: this.hero.yaw + THREE.MathUtils.degToRad(yawDeg), pitch: THREE.MathUtils.degToRad(pitchDeg), distanceScale });
  }

  /** Switch view; instant jumps (used under the veil), otherwise flies. */
  setView(name: string, o: { instant?: boolean; seconds?: number } = {}): Promise<void> {
    if (!this.views[name]) throw new Error(`Unknown view "${name}"`);
    this.view = name;
    this.hero = this.computeHero(name);
    this.atHero = true;
    if (o.instant || prefersReducedMotion()) {
      this.flight?.resolve();
      this.flight = null;
      this.goal = cloneState(this.hero);
      this.cur = cloneState(this.hero);
      this.apply();
      return Promise.resolve();
    }
    return this.flyToState(cloneState(this.hero), o.seconds ?? 1.1);
  }

  reset(seconds = 0.9): Promise<void> {
    this.hero = this.computeHero(this.view);
    this.atHero = true;
    return this.flyToState(cloneState(this.hero), prefersReducedMotion() ? 0 : seconds);
  }

  flyTo(pose: CameraPose, seconds = 1.2): Promise<void> {
    this.atHero = false;
    return this.flyToState(this.toState(pose), prefersReducedMotion() ? 0 : seconds);
  }

  /** Jump without easing (cinematic dollies set the pose every frame). */
  setPose(pose: CameraPose): void {
    this.flight?.resolve();
    this.drift = 0;
    this.flight = null;
    this.atHero = false;
    this.toState(pose, this.goal);
    copyState(this.cur, this.goal);
    this.apply();
  }

  /** Re-fit hero framing after a resize; keeps the visitor's own framing if they moved. */
  refit(): void {
    this.hero = this.computeHero(this.view);
    if (this.atHero && !this.flight) {
      this.goal = cloneState(this.hero);
      this.cur = cloneState(this.hero);
      this.apply();
    }
  }

  update(dt: number): void {
    this.clock += dt;
    if (this.flight) {
      const f = this.flight;
      f.t = Math.min(f.dur, f.t + dt);
      const k = f.dur > 0 ? easeInOutCubic(f.t / f.dur) : 1;
      this.lerpState(this.cur, f.from, f.to, k);
      copyState(this.goal, this.cur);
      if (f.t >= f.dur) {
        this.flight = null;
        f.resolve();
      }
    } else {
      this.stepFly(dt);
      const a = 1 - Math.exp(-dt * 9);
      const c = this.cur;
      const g = this.goal;
      c.yaw += (g.yaw - c.yaw) * a;
      c.pitch += (g.pitch - c.pitch) * a;
      c.distance += (g.distance - c.distance) * a;
      c.target.lerp(g.target, a);
      c.pan.lerp(g.pan, a);
      c.fov += (g.fov - c.fov) * a;
    }
    // Idle drift: ±0.025 rad on an 8 s sine, eased in, never with reduced motion.
    const idle = this.clock - this.lastInput > (this.opts.idleDelay ?? 3.5);
    if (idle && this.idleDrift && !this.flight && !prefersReducedMotion()) {
      if (this.driftStart === 0) this.driftStart = this.clock;
      const ramp = Math.min(1, (this.clock - this.driftStart) / 2.5);
      this.drift = 0.025 * ramp * Math.sin(((this.clock - this.driftStart) * 2 * Math.PI) / 8);
    } else {
      this.bakeDrift();
      this.driftStart = 0;
    }
    this.apply();
  }

  dispose(): void {
    for (const c of this.cleanup) c();
  }

  // ---------- internals ----------

  /** Fold the idle drift into the pose so stopping it never jumps. */
  private bakeDrift(): void {
    if (this.drift === 0) return;
    this.cur.yaw += this.drift;
    this.goal.yaw += this.drift;
    this.drift = 0;
  }

  private notifyInput(): void {
    this.lastInput = this.clock;
    this.atHero = false;
    this.bakeDrift();
    if (this.flight) {
      this.flight.resolve();
      this.flight = null;
      this.goal = cloneState(this.cur);
    }
    for (const cb of this.inputCbs) cb();
  }

  private flyToState(to: State, seconds: number): Promise<void> {
    this.flight?.resolve();
    this.bakeDrift();
    // Yaw counts whole turns: land on the target's yaw nearest the current one, so the flight takes
    // the short way round instead of unwinding every turn.
    to.yaw = this.cur.yaw + wrapPi(to.yaw - this.cur.yaw);
    if (seconds <= 0) {
      this.flight = null;
      this.goal = cloneState(to);
      this.cur = cloneState(to);
      this.apply();
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      this.flight = { from: cloneState(this.cur), to, t: 0, dur: seconds, resolve };
    });
  }

  private lerpState(out: State, a: State, b: State, k: number): void {
    out.target.lerpVectors(a.target, b.target, k);
    out.yaw = a.yaw + (b.yaw - a.yaw) * k;
    out.pitch = a.pitch + (b.pitch - a.pitch) * k;
    out.distance = Math.exp(Math.log(a.distance) + (Math.log(b.distance) - Math.log(a.distance)) * k);
    out.pan.lerpVectors(a.pan, b.pan, k);
    out.fov = a.fov + (b.fov - a.fov) * k;
  }

  private toState(p: CameraPose, out: State = { target: new THREE.Vector3(), yaw: 0, pitch: 0, distance: 1, pan: new THREE.Vector3(), fov: 30 }): State {
    out.target.fromArray(p.target);
    out.yaw = p.yaw;
    out.pitch = p.pitch;
    out.distance = p.distance;
    if (p.pan) out.pan.fromArray(p.pan);
    else out.pan.set(0, 0, 0);
    out.fov = p.fov ?? this.views[this.view]?.fov ?? 30;
    return out;
  }

  /** Camera axes for a yaw and pitch, in shared scratch vectors: copy them to keep them. */
  private basis(yaw: number, pitch: number): { dir: THREE.Vector3; right: THREE.Vector3; up: THREE.Vector3 } {
    const { dir, right, up } = this.axes;
    dir.set(Math.sin(yaw) * Math.cos(pitch), Math.sin(pitch), Math.cos(yaw) * Math.cos(pitch));
    right.set(Math.cos(yaw), 0, -Math.sin(yaw));
    up.crossVectors(dir, right).normalize();
    return this.axes;
  }

  private computeHero(name: string): State {
    const v = this.views[name];
    const fov = v.fov ?? 30;
    const w = Math.max(1, this.dom.clientWidth);
    const h = Math.max(1, this.dom.clientHeight);
    const sa = this.opts.safeArea?.() ?? { top: 0, right: 0, bottom: 0, left: 0, phone: false };
    const aw = Math.max(80, w - sa.left - sa.right);
    const ah = Math.max(80, h - sa.top - sa.bottom);
    const tanHalf = Math.tan(THREE.MathUtils.degToRad(fov) / 2);
    const dH = (v.fit.height * h) / (2 * tanHalf * ah);
    const dW = (v.fit.width * h) / (2 * tanHalf * aw);
    const distance = Math.max(dH, dW);
    // Shift the subject from the screen center to the center of the free region.
    const dx = sa.left + aw / 2 - w / 2;
    const dy = sa.top + ah / 2 - h / 2;
    const worldPerPx = (2 * distance * tanHalf) / h;
    const { right, up } = this.basis(v.yaw, v.pitch);
    const pan = new THREE.Vector3().addScaledVector(right, -dx * worldPerPx).addScaledVector(up, dy * worldPerPx);
    return { target: new THREE.Vector3(...v.target), yaw: v.yaw, pitch: v.pitch, distance, pan, fov };
  }

  private apply(): void {
    const c = this.cur;
    // Bounds last, whatever set the pose (input, flight, setPose, refit): the camera never leaves the box.
    // Only the drawn distance shortens; the chosen one is kept, so the camera moves back out as it orbits
    // away from a wall.
    const distance = this.boxDistance(c, c.yaw + this.drift);
    this.drawn = distance;
    const { dir } = this.basis(c.yaw + this.drift, c.pitch);
    const look = this.tmp.copy(c.target).add(c.pan);
    this.camera.position.copy(look).addScaledVector(dir, distance);
    // With the look point on a face (flying down at a low pitch) the distance floor wins: slide along the face.
    const bounds = this.views[this.view].bounds;
    if (bounds) this.camera.position.clamp(bounds.min, bounds.max);
    this.camera.up.set(0, 1, 0);
    this.camera.lookAt(look);
    if (Math.abs(this.camera.fov - c.fov) > 1e-4) {
      this.camera.fov = c.fov;
      this.camera.updateProjectionMatrix();
    }
  }

  /** Snapshot the goal before an input changes it (see clampGoal). */
  private beforeInput(): void {
    const g = this.goal;
    const b = this.before;
    b.yaw = g.yaw;
    b.pitch = g.pitch;
    b.distance = g.distance;
    b.pan = this.panOffset().length();
  }

  /** The goal's look point relative to the hero's: what the pan limit bounds. */
  private panOffset(): THREE.Vector3 {
    const g = this.goal;
    return this.off.copy(g.pan).sub(this.hero.pan).add(g.target).sub(this.hero.target);
  }

  /**
   * Hold the goal inside the view's limits, each widened to include the goal from before this input
   * (beforeInput): input moves a pose that sits outside them, such as a close-up or a cinematic
   * dolly, toward them and never jumps it in.
   */
  private clampGoal(): void {
    const v = this.views[this.view];
    const lim = { ...DEFAULT_LIMITS, ...v.limits };
    const g = this.goal;
    const b = this.before;
    if (lim.yaw) g.yaw = clampAround(g.yaw, this.hero.yaw + lim.yaw[0], this.hero.yaw + lim.yaw[1], b.yaw);
    g.pitch = clampAround(g.pitch, lim.pitch[0], lim.pitch[1], b.pitch);
    g.distance = clampAround(g.distance, this.hero.distance * lim.zoom[0], this.hero.distance * lim.zoom[1], b.distance);
    const maxPan = Math.max(v.limits?.pan ?? Math.max(v.fit.width, v.fit.height) * 0.6, b.pan);
    const off = this.panOffset();
    if (off.length() > maxPan) {
      off.setLength(maxPan);
      g.pan.copy(this.hero.pan).add(off);
      g.target.copy(this.hero.target);
    }
    // Bounds come last and are never widened: the look point stays inside the box (W A S D / Q E
    // move it). The distance is shortened only where the camera is drawn (apply).
    if (v.bounds) {
      const look = this.origin.copy(g.target).add(g.pan);
      g.pan.add(this.off.copy(look).clamp(v.bounds.min, v.bounds.max).sub(look));
    }
  }

  /** Scale the chosen distance. Zooming in starts from the distance as drawn, so it answers at once beside a wall. */
  private zoomBy(factor: number): void {
    const g = this.goal;
    if (factor < 1) g.distance = Math.min(g.distance, this.boxDistance(g, g.yaw));
    g.distance *= factor;
  }

  /** The state's distance, shortened to keep the camera inside the view's bounds (unchanged without them). */
  private boxDistance(s: State, yaw: number): number {
    const v = this.views[this.view];
    if (!v.bounds) return s.distance;
    const origin = this.origin.copy(s.target).add(s.pan);
    // Down to the closest zoom, and never longer: a pose already closer (a close-up) stays as it is.
    const zoom = v.limits?.zoom ?? DEFAULT_LIMITS.zoom;
    const floor = Math.min(s.distance, this.hero.distance * zoom[0]);
    return clampDistanceToBox(origin, this.basis(yaw, s.pitch).dir, s.distance, v.bounds, floor);
  }

  private stepFly(dt: number): void {
    if (!this.enabled || this.fly.size === 0) return;
    const { dir, right } = this.basis(this.cur.yaw, this.cur.pitch);
    // Forward is −dir (dir points from the look point to the camera).
    const move = this.tmp.set(0, 0, 0);
    if (this.fly.has('KeyW')) move.sub(dir);
    if (this.fly.has('KeyS')) move.add(dir);
    if (this.fly.has('KeyD')) move.add(right);
    if (this.fly.has('KeyA')) move.sub(right);
    if (this.fly.has('KeyE')) move.y += 1;
    if (this.fly.has('KeyQ')) move.y -= 1;
    if (move.lengthSq() === 0) return;
    this.beforeInput();
    this.goal.pan.addScaledVector(move.normalize(), (this.drawn || this.cur.distance) * 0.55 * dt);
    this.clampGoal();
    this.lastInput = this.clock;
  }

  private bind(): void {
    const dom = this.dom;
    const onDown = (e: PointerEvent) => {
      // Any press on the scene holds the idle drift, a part drag (filtered out below) included.
      this.lastInput = this.clock;
      if (!this.enabled) return;
      if (this.opts.interactionFilter && this.opts.interactionFilter(e) === false) return;
      this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      dom.setPointerCapture(e.pointerId);
      if (this.pointers.size === 2) {
        const [a, b] = [...this.pointers.values()];
        this.pinch = Math.hypot(a.x - b.x, a.y - b.y);
        this.dragMode = null;
      } else {
        this.dragMode = e.button === 2 || e.shiftKey ? 'pan' : 'orbit';
      }
      dom.classList.add('grabbing');
      this.notifyInput();
    };
    const onMove = (e: PointerEvent) => {
      if (e.buttons) this.lastInput = this.clock;
      const p = this.pointers.get(e.pointerId);
      if (!p) return;
      const dx = e.clientX - p.x;
      const dy = e.clientY - p.y;
      p.x = e.clientX;
      p.y = e.clientY;
      this.beforeInput();
      if (this.pointers.size === 2) {
        const [a, b] = [...this.pointers.values()];
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        if (this.pinch > 0) this.zoomBy(this.pinch / Math.max(1, d));
        this.pinch = d;
      } else if (this.dragMode === 'orbit') {
        this.goal.yaw -= dx * 0.0055;
        this.goal.pitch += dy * 0.0045;
      } else if (this.dragMode === 'pan') {
        const tanHalf = Math.tan(THREE.MathUtils.degToRad(this.cur.fov) / 2);
        const worldPerPx = (2 * (this.drawn || this.cur.distance) * tanHalf) / Math.max(1, dom.clientHeight);
        const { right, up } = this.basis(this.cur.yaw, this.cur.pitch);
        this.goal.pan.addScaledVector(right, -dx * worldPerPx).addScaledVector(up, dy * worldPerPx);
      }
      this.clampGoal();
    };
    const onUp = (e: PointerEvent) => {
      if (!this.pointers.delete(e.pointerId)) return;
      if (dom.hasPointerCapture(e.pointerId)) dom.releasePointerCapture(e.pointerId);
      if (this.pointers.size === 0) {
        this.dragMode = null;
        dom.classList.remove('grabbing');
      }
    };
    const onWheel = (e: WheelEvent) => {
      if (!this.enabled) return;
      e.preventDefault();
      this.notifyInput();
      this.beforeInput();
      this.zoomBy(Math.exp(e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0012)));
      this.clampGoal();
    };
    const typing = (t: EventTarget | null) => t instanceof HTMLElement && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName));
    const flyCodes = new Set(['KeyW', 'KeyA', 'KeyS', 'KeyD', 'KeyQ', 'KeyE']);
    const onKeyDown = (e: KeyboardEvent) => {
      if (!this.enabled || !flyCodes.has(e.code) || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey || typing(e.target)) return;
      if (document.querySelector('dialog[open]')) return;
      if (!this.fly.has(e.code)) this.notifyInput();
      this.fly.add(e.code);
    };
    const onKeyUp = (e: KeyboardEvent) => {
      this.fly.delete(e.code);
    };
    const onBlur = () => this.fly.clear();
    const noMenu = (e: Event) => e.preventDefault();
    dom.addEventListener('pointerdown', onDown);
    dom.addEventListener('pointermove', onMove);
    dom.addEventListener('pointerup', onUp);
    dom.addEventListener('pointercancel', onUp);
    dom.addEventListener('wheel', onWheel, { passive: false });
    dom.addEventListener('contextmenu', noMenu);
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    window.addEventListener('blur', onBlur);
    this.cleanup.push(
      () => dom.removeEventListener('pointerdown', onDown),
      () => dom.removeEventListener('pointermove', onMove),
      () => dom.removeEventListener('pointerup', onUp),
      () => dom.removeEventListener('pointercancel', onUp),
      () => dom.removeEventListener('wheel', onWheel),
      () => dom.removeEventListener('contextmenu', noMenu),
      () => window.removeEventListener('keydown', onKeyDown),
      () => window.removeEventListener('keyup', onKeyUp),
      () => window.removeEventListener('blur', onBlur),
    );
  }
}
