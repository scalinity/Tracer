// Debug handle and URL parameters (webgpu-stack §8). Screenshots, tests and judges drive the
// exhibit through window.__exhibit; URL parameters reproduce any shot:
//   ?view=exploded&preset=2&ui=0&labels=1&instant=1&seed=1&tune=1
// plus ?pose=closeup (a named camera pose), ?orbit=180,8[,1.2] (yaw from the hero and pitch, in degrees,
// and a distance scale), ?compact=1, ?cinematic=0 or 1, ?motion=reduce, ?backend=webgl2.
// A plain first load plays the opening tour; any of view, preset, orbit, pose, ui=0, compact or instant
// opens still instead (cinematic.ts, opensWithTour).

export interface UrlParams {
  view: string | null;
  /** 1-based preset number. */
  preset: number | null;
  ui: boolean | null;
  labels: boolean | null;
  /** Skip transitions and tweens (captures). */
  instant: boolean;
  seed: number | null;
  tune: boolean;
  pose: string | null;
  /** Hero-relative camera pose: yaw added to the hero yaw and absolute pitch, both in degrees. */
  orbit: { yaw: number; pitch: number; distanceScale?: number } | null;
  /** Start with the HUD collapsed. */
  compact: boolean;
  /** Play the tour from load (true), open still (false), or leave it to opensWithTour (null). */
  cinematic: boolean | null;
  /** 'webgl2' forces the fallback backend on purpose, to test degraded support. */
  backend: string | null;
}

export function readUrlParams(search = typeof location !== 'undefined' ? location.search : ''): UrlParams {
  const q = new URLSearchParams(search);
  const bool = (k: string) => (q.has(k) ? !['0', 'false', 'off', 'no'].includes((q.get(k) ?? '').toLowerCase()) : null);
  const num = (k: string) => {
    const v = q.get(k);
    return v === null || v === '' || Number.isNaN(Number(v)) ? null : Number(v);
  };
  const orbit = (q.get('orbit') ?? '').split(',').map(Number);
  return {
    view: q.get('view'),
    preset: num('preset'),
    ui: bool('ui'),
    labels: bool('labels'),
    instant: bool('instant') ?? false,
    seed: num('seed'),
    tune: q.has('tune'),
    pose: q.get('pose'),
    orbit: orbit.length >= 2 && orbit.every((v) => Number.isFinite(v))
      ? { yaw: orbit[0], pitch: orbit[1], ...(orbit.length > 2 ? { distanceScale: orbit[2] } : {}) }
      : null,
    compact: bool('compact') ?? false,
    cinematic: bool('cinematic'),
    backend: q.get('backend'),
  };
}

export interface ExhibitHandle<S = unknown> {
  /** Resolves after compile plus two rendered frames. */
  ready: Promise<void>;
  backend: 'webgpu' | 'webgl2';
  fps(): number;
  fpsStats(ms?: number): { avg: number; min: number; frames: number };
  /** The model state plus every displayed number. */
  getState(): S;
  setState(partial: Partial<S>, opts?: { instant?: boolean }): void;
  /** 1-based, like the number keys. */
  applyPreset(i: number): void;
  setView(name: string, opts?: { instant?: boolean }): Promise<void>;
  setUi(visible: boolean): void;
  setLabels(visible: boolean): void;
  cinematic(on: boolean): void;
  /** Start the guided lesson from its first step, or end it. */
  lesson(on: boolean): void;
  /** Named camera poses, including 'hero' and 'closeup'. */
  pose(name: string): void;
  /** Hero-relative orbit, in degrees (yaw added to the hero yaw, absolute pitch). */
  orbit(yawDeg: number, pitchDeg: number, distanceScale?: number): void;
  /** Collapse or expand the HUD. */
  setCompact(on: boolean): void;
  /** The HUD and help text, for the evidence pack. */
  text(): { hud: string; help: string };
}

declare global {
  interface Window { __exhibit?: ExhibitHandle }
}

export function installDebugHandle<S>(handle: ExhibitHandle<S>): ExhibitHandle<S> {
  window.__exhibit = handle as ExhibitHandle;
  return handle;
}

/** Visible text of the HUD and of the help sheet, one block per line (used by capture.mjs). */
export function hudText(root: HTMLElement): { hud: string; help: string } {
  const clean = (t: string | null | undefined) => (t ?? '').replace(/\s+/g, ' ').trim();
  const pick = (sel: string) => Array.from(root.querySelectorAll<HTMLElement>(sel)).map((e) => e.innerText.trim()).filter(Boolean).join('\n');
  const help = root.querySelector<HTMLElement>('#help');
  const lines: string[] = [];
  help?.querySelectorAll<HTMLElement>('h2, h3, p, .keys > div').forEach((e) => {
    if (e.classList.contains('chips')) lines.push(Array.from(e.querySelectorAll('kbd')).map((k) => k.textContent).join(' ') + ' —');
    else if (e.parentElement?.classList.contains('keys')) lines[lines.length - 1] += ` ${clean(e.textContent)}`;
    else lines.push(e.tagName === 'H2' ? `# ${clean(e.textContent)}` : e.tagName === 'H3' ? `\n## ${clean(e.textContent)}` : clean(e.textContent));
  });
  return { hud: pick('.side, .ctl, .labels-layer'), help: lines.join('\n') };
}
