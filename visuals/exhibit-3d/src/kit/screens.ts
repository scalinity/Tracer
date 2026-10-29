// Diegetic screens (style-bible §12): a canvas texture on an unlit plane, color multiplier about
// 1.5 so the traces bloom slightly. Charts: one cyan trace with a soft glow, a white marker for
// the current state, a mono uppercase caption, an annotation at the interesting point, coral
// shading for failure regimes. Redraw only when the inputs change.

import * as THREE from 'three/webgpu';
import { screen as screenMaterial } from './materials';

export const SCREEN_COLORS = {
  bg: '#08101e',
  bgHi: '#0d1a2e',
  grid: 'rgba(160, 190, 230, 0.10)',
  axis: 'rgba(170, 200, 240, 0.32)',
  text: '#9fb3d1',
  cyan: '#7fe3ff',
  amber: '#ffb454',
  coral: '#ff6b86',
  violet: '#b99cff',
  green: '#8fe8a8',
  white: '#f4f8ff',
};

export interface Rect { x: number; y: number; w: number; h: number }

export class DiegeticScreen {
  readonly mesh: THREE.Mesh;
  readonly canvas: HTMLCanvasElement;
  readonly ctx: CanvasRenderingContext2D;
  readonly texture: THREE.CanvasTexture;
  private lastDeps: unknown[] | null = null;

  /** `width` × `height` in world units; `pixels` is the canvas width (height follows the aspect). */
  constructor(width: number, height: number, pixels = 1024, opts: { gain?: number } = {}) {
    this.canvas = document.createElement('canvas');
    this.canvas.width = pixels;
    this.canvas.height = Math.round((pixels * height) / width);
    this.ctx = this.canvas.getContext('2d') as CanvasRenderingContext2D;
    this.texture = new THREE.CanvasTexture(this.canvas);
    this.texture.anisotropy = 8;
    this.texture.generateMipmaps = true;
    this.texture.minFilter = THREE.LinearMipmapLinearFilter;
    this.mesh = new THREE.Mesh(new THREE.PlaneGeometry(width, height), screenMaterial(this.texture, opts.gain ?? 1.5));
    this.mesh.userData.occluder = false;
  }

  get size(): { w: number; h: number } { return { w: this.canvas.width, h: this.canvas.height }; }

  /** Redraws when any of `deps` changed since the last call. Returns true if it drew. */
  draw(deps: unknown[], paint: (ctx: CanvasRenderingContext2D, size: { w: number; h: number }) => void): boolean {
    if (this.lastDeps && deps.length === this.lastDeps.length && deps.every((d, i) => Object.is(d, this.lastDeps![i]))) return false;
    this.lastDeps = deps.slice();
    const { w, h } = this.size;
    const g = this.ctx;
    g.save();
    g.clearRect(0, 0, w, h);
    const bg = g.createLinearGradient(0, 0, 0, h);
    bg.addColorStop(0, SCREEN_COLORS.bgHi);
    bg.addColorStop(1, SCREEN_COLORS.bg);
    g.fillStyle = bg;
    g.fillRect(0, 0, w, h);
    paint(g, { w, h });
    g.restore();
    this.texture.needsUpdate = true;
    return true;
  }

  /** Force the next draw() to repaint (for example after fonts load). */
  invalidate(): void { this.lastDeps = null; }
}

/** A chart frame mapping data coordinates into a canvas rectangle. */
export class Chart {
  readonly ctx: CanvasRenderingContext2D;
  readonly rect: Rect;
  readonly xr: [number, number];
  readonly yr: [number, number];
  /** Scale for line widths and fonts (canvas height / 256). */
  readonly k: number;

  constructor(ctx: CanvasRenderingContext2D, rect: Rect, x: [number, number], y: [number, number]) {
    this.ctx = ctx;
    this.rect = rect;
    this.xr = x;
    this.yr = y;
    this.k = ctx.canvas.height / 256;
  }

  px(x: number): number { return this.rect.x + ((x - this.xr[0]) / (this.xr[1] - this.xr[0])) * this.rect.w; }
  py(y: number): number { return this.rect.y + this.rect.h - ((y - this.yr[0]) / (this.yr[1] - this.yr[0])) * this.rect.h; }

  grid(o: { xStep?: number; yStep?: number; color?: string } = {}): this {
    const g = this.ctx;
    g.save();
    g.strokeStyle = o.color ?? SCREEN_COLORS.grid;
    g.lineWidth = 1 * this.k;
    if (o.xStep) for (let x = this.xr[0]; x <= this.xr[1] + 1e-9; x += o.xStep) line(g, this.px(x), this.rect.y, this.px(x), this.rect.y + this.rect.h);
    if (o.yStep) for (let y = this.yr[0]; y <= this.yr[1] + 1e-9; y += o.yStep) line(g, this.rect.x, this.py(y), this.rect.x + this.rect.w, this.py(y));
    g.strokeStyle = SCREEN_COLORS.axis;
    line(g, this.rect.x, this.rect.y + this.rect.h, this.rect.x + this.rect.w, this.rect.y + this.rect.h);
    g.restore();
    return this;
  }

  /** Mono tick labels under the x axis. */
  xLabels(values: number[], format: (v: number) => string): this {
    const g = this.ctx;
    g.save();
    g.fillStyle = SCREEN_COLORS.text;
    g.font = `500 ${Math.round(9 * this.k)}px "DM Mono", ui-monospace, monospace`;
    g.textAlign = 'center';
    g.textBaseline = 'top';
    for (const v of values) g.fillText(format(v), this.px(v), this.rect.y + this.rect.h + 4 * this.k);
    g.restore();
    return this;
  }

  /** Shade an x interval (coral for failure regimes). */
  region(x0: number, x1: number, o: { color?: string; alpha?: number; label?: string } = {}): this {
    const g = this.ctx;
    g.save();
    g.globalAlpha = o.alpha ?? 0.18;
    g.fillStyle = o.color ?? SCREEN_COLORS.coral;
    const a = this.px(Math.max(x0, this.xr[0]));
    const b = this.px(Math.min(x1, this.xr[1]));
    g.fillRect(a, this.rect.y, b - a, this.rect.h);
    if (o.label) {
      g.globalAlpha = 0.95;
      g.font = `500 ${Math.round(8.5 * this.k)}px "DM Mono", ui-monospace, monospace`;
      g.textAlign = 'center';
      g.textBaseline = 'top';
      g.fillText(o.label.toUpperCase(), (a + b) / 2, this.rect.y + 3 * this.k);
    }
    g.restore();
    return this;
  }

  /** A trace: a function sampled across x, or explicit points. */
  curve(f: ((x: number) => number) | [number, number][], o: { color?: string; width?: number; glow?: number; samples?: number; dash?: number[] } = {}): this {
    const g = this.ctx;
    const pts: [number, number][] = typeof f === 'function'
      ? Array.from({ length: (o.samples ?? 240) + 1 }, (_, i) => {
        const x = this.xr[0] + ((this.xr[1] - this.xr[0]) * i) / (o.samples ?? 240);
        return [x, f(x)] as [number, number];
      })
      : f;
    g.save();
    g.beginPath();
    g.rect(this.rect.x, this.rect.y - 2 * this.k, this.rect.w, this.rect.h + 4 * this.k);
    g.clip();
    g.strokeStyle = o.color ?? SCREEN_COLORS.cyan;
    g.lineWidth = (o.width ?? 2.2) * this.k;
    g.lineJoin = 'round';
    g.lineCap = 'round';
    g.shadowColor = o.color ?? SCREEN_COLORS.cyan;
    g.shadowBlur = (o.glow ?? 8) * this.k;
    if (o.dash) g.setLineDash(o.dash.map((d) => d * this.k));
    g.beginPath();
    pts.forEach(([x, y], i) => (i === 0 ? g.moveTo(this.px(x), this.py(y)) : g.lineTo(this.px(x), this.py(y))));
    g.stroke();
    g.restore();
    return this;
  }

  /** The current state: a white dot with a soft ring. */
  marker(x: number, y: number, o: { color?: string; radius?: number; ring?: string; drop?: boolean } = {}): this {
    const g = this.ctx;
    const cx = this.px(x);
    const cy = this.py(y);
    g.save();
    if (o.drop) {
      g.strokeStyle = 'rgba(255,255,255,0.35)';
      g.setLineDash([3 * this.k, 3 * this.k]);
      g.lineWidth = 1 * this.k;
      line(g, cx, cy, cx, this.rect.y + this.rect.h);
      g.setLineDash([]);
    }
    g.fillStyle = o.ring ?? 'rgba(127,227,255,0.28)';
    g.beginPath();
    g.arc(cx, cy, (o.radius ?? 4.2) * 2.1 * this.k, 0, Math.PI * 2);
    g.fill();
    g.fillStyle = o.color ?? SCREEN_COLORS.white;
    g.shadowColor = '#ffffff';
    g.shadowBlur = 6 * this.k;
    g.beginPath();
    g.arc(cx, cy, (o.radius ?? 4.2) * this.k, 0, Math.PI * 2);
    g.fill();
    g.restore();
    return this;
  }

  /** Mono uppercase caption above the chart. */
  caption(text: string, o: { align?: 'left' | 'right'; color?: string } = {}): this {
    const g = this.ctx;
    g.save();
    g.fillStyle = o.color ?? SCREEN_COLORS.text;
    g.font = `500 ${Math.round(10 * this.k)}px "DM Mono", ui-monospace, monospace`;
    g.textBaseline = 'bottom';
    g.textAlign = o.align ?? 'left';
    const x = o.align === 'right' ? this.rect.x + this.rect.w : this.rect.x;
    g.fillText(text.toUpperCase(), x, this.rect.y - 6 * this.k);
    g.restore();
    return this;
  }

  /** A short note at a point of interest, with a tick from the point. */
  annotate(x: number, y: number, text: string, o: { color?: string; dx?: number; dy?: number } = {}): this {
    const g = this.ctx;
    const cx = this.px(x);
    const cy = this.py(y);
    const tx = cx + (o.dx ?? 10) * this.k;
    const ty = cy + (o.dy ?? -12) * this.k;
    g.save();
    g.strokeStyle = o.color ?? SCREEN_COLORS.text;
    g.globalAlpha = 0.8;
    g.lineWidth = 1 * this.k;
    line(g, cx, cy, tx, ty);
    g.globalAlpha = 1;
    g.fillStyle = o.color ?? SCREEN_COLORS.white;
    g.font = `500 ${Math.round(9.5 * this.k)}px "DM Mono", ui-monospace, monospace`;
    g.textBaseline = 'middle';
    g.textAlign = (o.dx ?? 10) >= 0 ? 'left' : 'right';
    g.fillText(text, tx + ((o.dx ?? 10) >= 0 ? 3 : -3) * this.k, ty);
    g.restore();
    return this;
  }
}

/** Small multiples: `frames` boxes across `rect`, the current one ringed in cyan. */
export function filmstrip(ctx: CanvasRenderingContext2D, rect: Rect, frames: number, current: number, drawFrame: (ctx: CanvasRenderingContext2D, r: Rect, i: number) => void, o: { gap?: number; labels?: string[] } = {}): void {
  const k = ctx.canvas.height / 256;
  const gap = (o.gap ?? 8) * k;
  const fw = (rect.w - gap * (frames - 1)) / frames;
  for (let i = 0; i < frames; i++) {
    const r = { x: rect.x + i * (fw + gap), y: rect.y, w: fw, h: rect.h };
    ctx.save();
    ctx.beginPath();
    ctx.rect(r.x, r.y, r.w, r.h);
    ctx.clip();
    drawFrame(ctx, r, i);
    ctx.restore();
    ctx.save();
    ctx.lineWidth = (i === current ? 2.5 : 1) * k;
    ctx.strokeStyle = i === current ? SCREEN_COLORS.cyan : 'rgba(170,200,240,0.25)';
    if (i === current) {
      ctx.shadowColor = SCREEN_COLORS.cyan;
      ctx.shadowBlur = 10 * k;
    }
    ctx.strokeRect(r.x, r.y, r.w, r.h);
    ctx.restore();
    if (o.labels?.[i]) {
      ctx.save();
      ctx.fillStyle = i === current ? SCREEN_COLORS.cyan : SCREEN_COLORS.text;
      ctx.font = `500 ${Math.round(9 * k)}px "DM Mono", ui-monospace, monospace`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.fillText(o.labels[i], r.x + r.w / 2, r.y + r.h + 4 * k);
      ctx.restore();
    }
  }
}

function line(g: CanvasRenderingContext2D, x0: number, y0: number, x1: number, y1: number): void {
  g.beginPath();
  g.moveTo(x0, y0);
  g.lineTo(x1, y1);
  g.stroke();
}
