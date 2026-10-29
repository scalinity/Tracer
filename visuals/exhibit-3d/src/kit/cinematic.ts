// Cinematic tour (style-bible §10, exhibit-design §11): a looping shot list. Each shot sets a view,
// a pose, a slow move across its hold, and timed beats that drive the exhibit through the lesson.
// Between shots the screen fades to black, holds a moment, and fades up on the next shot while the
// camera keeps moving, so the tour flows rather than stopping and starting. A plain first load plays
// it as the opening; any input stops it. This is also how promotional videos get recorded: make shot
// one the best.

import { prefersReducedMotion } from './a11y';
import type { CameraPose, CameraRig, PoseSpec, Vec3 } from './camera';
import type { UrlParams } from './debug';

export interface Shot {
  view: string;
  /** Start pose, relative to the view's hero (missing fields come from the hero). */
  pose: PoseSpec;
  /** Change applied smoothly across the hold: yaw and pitch in radians, distanceScale multiplies distance. */
  dolly?: { yaw?: number; pitch?: number; distanceScale?: number; pan?: [number, number, number] };
  /** Seconds, about 8 to 12. */
  hold: number;
  /** Where the depth of field focuses during the shot (needs `rig.focusPoint`); the exhibit's own focus otherwise. */
  focus?: Vec3;
  /**
   * Sets the exhibit's state without tweens while the screen is black, before the shot fades up.
   * Shot one's setup is what makes the loop seamless: the bench is back at its start when the tour returns.
   */
  setup?: () => void;
  /** [seconds into the shot, action]. */
  beats?: [number, () => void][];
}

export interface CinematicDeps {
  rig: CameraRig;
  /** Switch view with the veil transition; resolves when the new view is showing. */
  setView: (name: string) => Promise<void>;
  /** The stage's veil fade (`stage.fade`). With it, shots fade through black instead of cutting. */
  fade?: (to: number, ms: number, color?: string) => Promise<void>;
  onChange?: (playing: boolean) => void;
  /** When the opening hands over: called under the black after shot one's setup, so the start lands unseen. */
  onHome?: () => void;
}

/** Milliseconds of the fade to black at the end of a shot, of the black between, and of the fade up. */
const DIP_OUT = 400;
const BLACK = 150;
const DIP_IN = 500;
const DIP_COLOR = '#000';
const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

// Half linear, half smoothstep: each move eases but never comes to rest, so the motion carries
// through the fades.
const flow = (k: number) => 0.5 * k + 0.5 * k * k * (3 - 2 * k);

/** A visitor's load: no parameter sets up a shot, as links, captures and judges do. */
export function plainLoad(p: UrlParams): boolean {
  return p.cinematic === null && !p.instant && !p.view && p.preset === null && !p.orbit && !p.pose && p.ui !== false && !p.compact;
}

/** Whether the page opens with the tour: a plain load does unless motion is reduced; `?cinematic=` decides outright. */
export function opensWithTour(p: UrlParams): boolean {
  return p.cinematic ?? (plainLoad(p) && !prefersReducedMotion());
}

export class Cinematic {
  readonly shots: Shot[];
  private deps: CinematicDeps;
  private index = 0;
  private t = 0;
  private fired = new Set<number>();
  private start: CameraPose | null = null;
  private end: CameraPose | null = null;
  private busy = false;
  /** A dissolve into the next shot is under way. */
  private leaving = false;
  private startedAt = 0;
  private active = false;
  /** Playing as the exhibit's opening: the input that stops it only takes over, and the view returns home. */
  private opening = false;
  private focusHome: Vec3 | null = null;
  /** The dolly pose, reused every frame (setPose copies it). */
  private pose: CameraPose & { pan: Vec3 } = { target: [0, 0, 0], yaw: 0, pitch: 0, distance: 1, pan: [0, 0, 0] };

  constructor(shots: Shot[], deps: CinematicDeps) {
    this.shots = shots;
    this.deps = deps;
    const stopOnInput = (e: Event) => {
      if (!this.active || performance.now() - this.startedAt < 150) return;
      if (e instanceof KeyboardEvent && (e.code === 'KeyC' || e.metaKey || e.ctrlKey)) return;
      const t = e.target;
      if (t instanceof Element && t.closest('[data-tool="cinematic"]')) return;
      // The opening's first input only takes over: it never reaches a part seen from wherever the tour was.
      if (this.opening) {
        e.preventDefault();
        e.stopImmediatePropagation();
      }
      this.stop();
    };
    window.addEventListener('pointerdown', stopOnInput, { capture: true });
    window.addEventListener('wheel', stopOnInput, { capture: true, passive: true });
    window.addEventListener('keydown', stopOnInput, { capture: true });
    deps.rig.onInput(() => { if (this.active && performance.now() - this.startedAt > 150) this.stop(); });
  }

  get playing(): boolean { return this.active; }

  /**
   * `opening` plays the tour as the exhibit's opening: it starts on shot one with a cut, which the loading
   * screen already covers, and the input that stops it returns the view home (stop()).
   */
  play(from = 0, o: { opening?: boolean } = {}): void {
    if (this.shots.length === 0) return;
    this.active = true;
    this.opening = o.opening ?? false;
    this.startedAt = performance.now();
    this.start = this.end = null;
    const fp = this.deps.rig.focusPoint;
    this.focusHome = fp ? [fp.x, fp.y, fp.z] : null;
    this.deps.onChange?.(true);
    if (this.dissolves && !this.opening) {
      this.leaving = true;
      void this.deps.fade?.(1, DIP_OUT * 0.6, DIP_COLOR).then(() => this.enter(from, true));
    } else {
      void this.enter(from);
    }
  }

  stop(): void {
    if (!this.active) return;
    this.active = false;
    this.focusOn(null);
    if (this.opening) {
      // The opening ends where the visitor starts: a quick fade through black to shot one's view at
      // its hero framing, with shot one's setup, never wherever the tour happened to be.
      this.opening = false;
      void this.returnHome();
    } else if (this.leaving || this.busy) {
      // Stopped mid-fade: lift the veil from wherever it got to.
      void this.deps.fade?.(0, 300);
    }
    this.leaving = false;
    this.deps.onChange?.(false);
  }

  toggle(): void {
    if (this.active) this.stop();
    else this.play();
  }

  update(dt: number): void {
    if (!this.active || this.busy || !this.start || !this.end) return;
    const shot = this.shots[this.index];
    this.t += dt;
    const k = flow(Math.min(1, this.t / shot.hold));
    const a = this.start;
    const b = this.end;
    const p = this.pose;
    p.target = a.target;
    p.yaw = a.yaw + (b.yaw - a.yaw) * k;
    p.pitch = a.pitch + (b.pitch - a.pitch) * k;
    p.distance = a.distance * Math.pow(b.distance / a.distance, k);
    for (let i = 0; i < 3; i++) p.pan[i] = (a.pan?.[i] ?? 0) + ((b.pan?.[i] ?? 0) - (a.pan?.[i] ?? 0)) * k;
    p.fov = a.fov;
    this.deps.rig.setPose(p);
    (shot.beats ?? []).forEach(([time, action], i) => {
      if (this.t >= time && !this.fired.has(i)) {
        this.fired.add(i);
        action();
      }
    });
    if (this.leaving) return;
    const next = (this.index + 1) % this.shots.length;
    if (this.dissolves && this.t >= shot.hold - DIP_OUT / 1000) {
      // The camera keeps moving as the screen goes black; the cut happens in the black.
      this.leaving = true;
      void this.deps.fade?.(1, DIP_OUT, DIP_COLOR).then(() => wait(BLACK)).then(() => this.enter(next, true));
    } else if (!this.dissolves && this.t >= shot.hold) {
      this.leaving = true;
      void this.enter(next);
    }
  }

  /** Reduced motion keeps the cuts: each shot holds its pose and the next one replaces it at once. */
  private get dissolves(): boolean {
    return !!this.deps.fade && !prefersReducedMotion();
  }

  private async returnHome(): Promise<void> {
    const home = this.shots[0];
    const fade = prefersReducedMotion() ? undefined : this.deps.fade;
    await fade?.(1, 220, DIP_COLOR);
    if (this.deps.rig.view !== home.view) await this.deps.setView(home.view);
    home.setup?.();
    this.deps.rig.setPose(this.deps.rig.heroPose());
    this.deps.onHome?.();
    await fade?.(0, 380, DIP_COLOR);
  }

  private focusOn(at: Vec3 | null): void {
    const fp = this.deps.rig.focusPoint;
    const to = at ?? this.focusHome;
    if (fp && to) fp.set(to[0], to[1], to[2]);
  }

  private async enter(i: number, dipped = false): Promise<void> {
    // Stopped during the dip: stop() has already lifted the veil.
    if (!this.active) return;
    this.busy = true;
    try {
      this.index = i;
      this.t = 0;
      this.fired.clear();
      const shot = this.shots[i];
      if (this.deps.rig.view !== shot.view) await this.deps.setView(shot.view);
      if (!this.active) return;
      this.focusOn(shot.focus ?? null);
      this.start = this.deps.rig.resolve(shot.pose, shot.view);
      const d = shot.dolly ?? {};
      const pan = d.pan ? (this.start.pan ?? [0, 0, 0]).map((v, j) => v + (d.pan?.[j] ?? 0)) as [number, number, number] : this.start.pan;
      // Reduced motion: each shot holds its pose. The cuts between shots stay; the dollies, the room
      // circle among them, do not run.
      this.end = prefersReducedMotion() ? { ...this.start } : {
        ...this.start,
        yaw: this.start.yaw + (d.yaw ?? 0),
        pitch: this.start.pitch + (d.pitch ?? 0),
        distance: this.start.distance * (d.distanceScale ?? 1),
        pan,
      };
      this.deps.rig.setPose(this.start);
      shot.setup?.();
    } finally {
      // A view change that throws must not leave the tour frozen (update() waits while busy).
      this.busy = false;
      this.leaving = false;
      if (dipped && this.active) void this.deps.fade?.(0, DIP_IN, DIP_COLOR);
    }
  }
}
