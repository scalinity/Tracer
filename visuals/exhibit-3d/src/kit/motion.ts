// Motion helpers (style-bible §13, law 10: nothing snaps). Displayed values ease toward the state:
// presets tween over about a second, direct manipulation follows quickly, reduced motion and
// ?instant=1 jump.

export const ease = {
  inOutCubic: (k: number) => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2),
  outCubic: (k: number) => 1 - Math.pow(1 - k, 3),
  smooth: (k: number) => k * k * (3 - 2 * k),
};

/** Exponential approach toward `target` at `rate` per second (frame-rate independent). */
export function approach(current: number, target: number, rate: number, dt: number): number {
  return target + (current - target) * Math.exp(-rate * dt);
}

/** A number that eases to its target: tweens for presets, quick follow for drags. */
export class Eased {
  value: number;
  target: number;
  private from = 0;
  private t = 0;
  private dur = 0;
  private rate: number;

  constructor(value: number, rate = 16) {
    this.value = value;
    this.target = value;
    this.rate = rate;
  }

  /** Tween to `v` over `seconds` with ease-in-out (presets, cinematic beats). */
  to(v: number, seconds = 0.9): void {
    this.from = this.value;
    this.target = v;
    this.t = 0;
    this.dur = seconds;
  }

  /** Follow `v` quickly (sliders, drags, keys). */
  follow(v: number): void {
    this.target = v;
    this.dur = 0;
  }

  /** Jump with no motion (reduced motion, captures). */
  jump(v: number): void {
    this.value = this.target = v;
    this.dur = 0;
  }

  get settled(): boolean {
    return Math.abs(this.value - this.target) < 1e-4;
  }

  update(dt: number): number {
    if (this.dur > 0) {
      this.t = Math.min(this.dur, this.t + dt);
      this.value = this.from + (this.target - this.from) * ease.inOutCubic(this.t / this.dur);
      if (this.t >= this.dur) this.dur = 0;
    } else {
      this.value = approach(this.value, this.target, this.rate, dt);
      if (Math.abs(this.value - this.target) < 1e-5) this.value = this.target;
    }
    return this.value;
  }
}
