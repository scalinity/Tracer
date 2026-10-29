// Part dragging: the primary interaction (exhibit-design §4). Raycasts registered parts, shows a
// verb-first hover tooltip and a cursor change, and turns pointer motion into a rotation about a
// world axis, a vertical or horizontal drag, or a click. Pass `filter` to the camera rig as its
// interactionFilter so a drag that starts on a part never orbits the camera.

import * as THREE from 'three/webgpu';
import type { Hud } from './hud';

export type DragMode =
  /**
   * Turn about a world axis through `center`. Face-on, the pointer's angle around the center drives
   * it; edge-on, dragging along the direction the part's front moves on screen does.
   */
  | { kind: 'rotate'; axis: THREE.Vector3; center: () => THREE.Vector3; onTurn: (radians: number) => void; pixelsPerRadian?: number }
  /** Drag up or down; positive when dragging up. */
  | { kind: 'vertical'; onDrag: (pixels: number) => void }
  /** Drag left or right; positive when dragging right. */
  | { kind: 'horizontal'; onDrag: (pixels: number) => void }
  | { kind: 'click'; onClick: () => void };

export interface DragTarget {
  /** Raycasts hit this object and its children. */
  object: THREE.Object3D;
  /** Verb first: "Drag to turn the analyzer". */
  tip: string | (() => string);
  mode: DragMode;
  enabled?: () => boolean;
  onStart?: () => void;
  onEnd?: () => void;
}

export class PartDragger {
  private targets: DragTarget[] = [];
  private raycaster = new THREE.Raycaster();
  private ndc = new THREE.Vector2();
  private active: { target: DragTarget; id: number; x: number; y: number; angle: number; moved: number; t0: number } | null = null;
  private hovered: DragTarget | null = null;
  private camera: THREE.Camera;
  private dom: HTMLElement;
  private hud: Hud | null;
  /** Called on any part interaction (stop the cinematic). */
  onInput: (() => void) | null = null;

  constructor(camera: THREE.Camera, dom: HTMLElement, hud: Hud | null = null) {
    this.camera = camera;
    this.dom = dom;
    this.hud = hud;
    dom.addEventListener('pointerdown', this.down, { capture: true });
    dom.addEventListener('pointermove', this.move);
    dom.addEventListener('pointerup', this.up);
    dom.addEventListener('pointercancel', this.up);
    dom.addEventListener('pointerleave', () => this.setHover(null));
  }

  add(target: DragTarget): () => void {
    this.targets.push(target);
    return () => { this.targets = this.targets.filter((t) => t !== target); };
  }

  /** For CameraRig's interactionFilter: false when the pointer is on (or dragging) a part. */
  readonly filter = (e: PointerEvent): boolean => !(this.active?.id === e.pointerId || this.pick(e));

  get dragging(): boolean { return this.active !== null; }

  private pick(e: { clientX: number; clientY: number }): DragTarget | null {
    const r = this.dom.getBoundingClientRect();
    this.ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    this.raycaster.setFromCamera(this.ndc, this.camera);
    let best: { t: DragTarget; d: number } | null = null;
    for (const t of this.targets) {
      if (t.enabled && !t.enabled()) continue;
      if (!t.object.visible) continue;
      const hit = this.raycaster.intersectObject(t.object, true).find((h) => isVisible(h.object));
      if (hit && (!best || hit.distance < best.d)) best = { t, d: hit.distance };
    }
    return best?.t ?? null;
  }

  private screenAngle(target: DragTarget, x: number, y: number): number {
    if (target.mode.kind !== 'rotate') return 0;
    const c = target.mode.center().clone().project(this.camera);
    const r = this.dom.getBoundingClientRect();
    const cx = r.left + ((c.x + 1) / 2) * r.width;
    const cy = r.top + ((1 - c.y) / 2) * r.height;
    return Math.atan2(-(y - cy), x - cx);
  }

  private edgeOn(target: DragTarget): boolean {
    if (target.mode.kind !== 'rotate') return false;
    const toCam = this.camera.position.clone().sub(target.mode.center()).normalize();
    return Math.abs(toCam.dot(target.mode.axis)) < 0.6;
  }

  /** Screen direction (unit, y down) in which the front of a part moves for a positive turn. */
  private tangent(target: DragTarget): THREE.Vector2 {
    const m = target.mode as Extract<DragMode, { kind: 'rotate' }>;
    const c = m.center();
    const toCam = this.camera.position.clone().sub(c).normalize();
    const v = new THREE.Vector3().crossVectors(m.axis, toCam);
    if (v.lengthSq() < 1e-8) return new THREE.Vector2(0, -1);
    const a = c.clone().project(this.camera);
    const b = c.clone().add(v.normalize().multiplyScalar(0.1)).project(this.camera);
    // NDC to pixels before normalising: the two NDC axes span the canvas width and height.
    const r = this.dom.getBoundingClientRect();
    const d = new THREE.Vector2((b.x - a.x) * r.width, -(b.y - a.y) * r.height);
    return d.lengthSq() < 1e-12 ? new THREE.Vector2(0, -1) : d.normalize();
  }

  private setHover(t: DragTarget | null, e?: PointerEvent): void {
    this.hovered = t;
    this.dom.classList.toggle('pointer', !!t);
    if (!this.hud) return;
    if (t && e) this.hud.tip(typeof t.tip === 'function' ? t.tip() : t.tip, e.clientX, e.clientY);
    else this.hud.tip(null);
  }

  private down = (e: PointerEvent): void => {
    // One drag at a time: a second touch would orphan the first drag's onEnd.
    if (e.button !== 0 || this.active) return;
    const t = this.pick(e);
    if (!t) return;
    this.active = { target: t, id: e.pointerId, x: e.clientX, y: e.clientY, angle: this.screenAngle(t, e.clientX, e.clientY), moved: 0, t0: performance.now() };
    this.dom.setPointerCapture(e.pointerId);
    this.dom.classList.add('grabbing');
    t.onStart?.();
    this.onInput?.();
  };

  private move = (e: PointerEvent): void => {
    const a = this.active;
    if (!a || a.id !== e.pointerId) {
      if (e.buttons === 0) {
        const t = this.pick(e);
        if (t !== this.hovered || t) this.setHover(t, e);
      }
      return;
    }
    const dx = e.clientX - a.x;
    const dy = e.clientY - a.y;
    a.moved += Math.abs(dx) + Math.abs(dy);
    a.x = e.clientX;
    a.y = e.clientY;
    const m = a.target.mode;
    if (m.kind === 'rotate') {
      if (this.edgeOn(a.target)) {
        const t = this.tangent(a.target);
        m.onTurn((dx * t.x + dy * t.y) / (m.pixelsPerRadian ?? 160));
      } else {
        const ang = this.screenAngle(a.target, e.clientX, e.clientY);
        let d = ang - a.angle;
        d = Math.atan2(Math.sin(d), Math.cos(d));
        a.angle = ang;
        const toCam = this.camera.position.clone().sub(m.center());
        m.onTurn(d * (toCam.dot(m.axis) >= 0 ? 1 : -1));
      }
    } else if (m.kind === 'vertical') m.onDrag(-dy);
    else if (m.kind === 'horizontal') m.onDrag(dx);
    if (this.hud) this.hud.tip(typeof a.target.tip === 'function' ? a.target.tip() : a.target.tip, e.clientX, e.clientY);
  };

  private up = (e: PointerEvent): void => {
    const a = this.active;
    if (!a || a.id !== e.pointerId) return;
    if (this.dom.hasPointerCapture(e.pointerId)) this.dom.releasePointerCapture(e.pointerId);
    this.dom.classList.remove('grabbing');
    if (a.target.mode.kind === 'click' && a.moved < 6 && performance.now() - a.t0 < 600) a.target.mode.onClick();
    a.target.onEnd?.();
    this.active = null;
  };
}

function isVisible(o: THREE.Object3D): boolean {
  for (let p: THREE.Object3D | null = o; p; p = p.parent) if (!p.visible) return false;
  return true;
}
