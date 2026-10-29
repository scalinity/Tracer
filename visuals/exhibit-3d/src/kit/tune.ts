// Tune panel (hud-spec §5): the developer-facing lookdev panel. Opens with Cmd+' (Ctrl+'
// elsewhere) or ?tune. Arrow keys select and change a row (Shift: coarse), 0 resets the row,
// Shift+0 resets all, C copies every value as JSON into the textarea; paste JSON there to apply.
// Write the tuned numbers back into looks.ts or the exhibit's look overrides. Never shipped open.

import type { Stage } from './stage';
import type { CameraRig } from './camera';
import { containFocus } from './a11y';

export interface TuneRow {
  name: string;
  min: number;
  max: number;
  step: number;
  get: () => number;
  set: (v: number) => void;
}

export interface TunePanel {
  open(): void;
  close(): void;
  toggle(): void;
  readonly isOpen: boolean;
  /** Current look and finish values as JSON. */
  json(): string;
}

export function mountTune(root: HTMLElement, deps: { stage: Stage; rig?: CameraRig; extra?: TuneRow[] }): TunePanel {
  const { stage, rig } = deps;
  const u = stage.uniforms;
  const f = stage.finish;
  const look = () => stage.looks[stage.look];
  const refresh = () => stage.refreshLook();

  const rows: TuneRow[] = [
    { name: 'exposure', min: 0.2, max: 2.5, step: 0.01, get: () => look().exposure, set: (v) => { look().exposure = v; u.exposure.value = v; } },
    {
      name: 'fov', min: 20, max: 45, step: 0.5,
      get: () => (rig ? rig.views[rig.view].fov ?? f.fov : stage.camera.fov),
      set: (v) => { if (rig) { rig.views[rig.view].fov = v; void rig.reset(0); } else { stage.camera.fov = v; stage.camera.updateProjectionMatrix(); } },
    },
    { name: 'key', min: 0, max: 6, step: 0.05, get: () => look().key.intensity, set: (v) => { look().key.intensity = v; refresh(); } },
    { name: 'fill', min: 0, max: 1.5, step: 0.01, get: () => look().fill.relative, set: (v) => { look().fill.relative = v; refresh(); } },
    { name: 'rim', min: 0, max: 3, step: 0.01, get: () => look().rim.relative, set: (v) => { look().rim.relative = v; refresh(); } },
    { name: 'fog', min: 0, max: 0.03, step: 0.0005, get: () => look().fog.density, set: (v) => { look().fog.density = v; refresh(); } },
    { name: 'saturation', min: 0.5, max: 1.8, step: 0.01, get: () => look().saturation, set: (v) => { look().saturation = v; u.saturation.value = f.saturation * v; } },
    { name: 'environment', min: 0, max: 2.5, step: 0.01, get: () => look().environmentIntensity, set: (v) => { look().environmentIntensity = v; refresh(); } },
    { name: 'reflection', min: 0, max: 1.5, step: 0.01, get: () => look().reflection, set: (v) => { look().reflection = v; refresh(); } },
    { name: 'bloom strength', min: 0, max: 2, step: 0.01, get: () => look().bloom.strength, set: (v) => { look().bloom.strength = v; u.bloomStrength.value = v; } },
    { name: 'bloom radius', min: 0, max: 1, step: 0.01, get: () => f.bloomRadius, set: (v) => { f.bloomRadius = v; u.bloomRadius.value = v; } },
    { name: 'bloom threshold', min: 0, max: 4, step: 0.01, get: () => look().bloom.threshold, set: (v) => { look().bloom.threshold = v; u.bloomThreshold.value = v; } },
    { name: 'ao intensity', min: 0, max: 1.5, step: 0.01, get: () => f.ao.intensity, set: (v) => { f.ao.intensity = v; u.aoIntensity.value = v; } },
    { name: 'ao radius', min: 0.05, max: 3, step: 0.01, get: () => f.ao.radius, set: (v) => { f.ao.radius = v; stage.aoNode.radius.value = v; } },
    { name: 'dof focus band', min: 1, max: 60, step: 0.25, get: () => f.dof.focusBand, set: (v) => { f.dof.focusBand = v; u.focusBand.value = v; } },
    { name: 'far blur', min: 0, max: 4, step: 0.02, get: () => f.dof.farBlur, set: (v) => { f.dof.farBlur = v; u.farBlur.value = v; } },
    { name: 'near blur', min: 0, max: 4, step: 0.02, get: () => f.dof.nearBlur, set: (v) => { f.dof.nearBlur = v; u.nearBlur.value = v; } },
    { name: 'max coc px', min: 0, max: 30, step: 0.5, get: () => f.dof.maxCoC, set: (v) => { f.dof.maxCoC = v; u.maxBlur.value = v * stage.pixelRatio; } },
    { name: 'grain', min: 0, max: 0.4, step: 0.005, get: () => f.grain, set: (v) => { f.grain = v; u.grain.value = v; } },
    ...(deps.extra ?? []),
  ];

  const panel = (root.querySelector<HTMLElement>('#tune') ?? root.appendChild(document.createElement('div')));
  panel.id = 'tune';
  panel.hidden = true;
  panel.tabIndex = -1;
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'Render tuning');
  panel.replaceChildren();
  const head = document.createElement('div');
  head.className = 'head';
  const title = document.createElement('span');
  const hintKeys = document.createElement('span');
  hintKeys.textContent = "⌘' closes";
  head.append(title, hintKeys);
  panel.append(head);

  const initial = rows.map((r) => r.get());
  let sel = 0;
  const rowEls = rows.map((r, i) => {
    const row = document.createElement('div');
    row.className = 'trow';
    const name = document.createElement('span');
    name.textContent = r.name;
    const mini = document.createElement('div');
    mini.className = 'mini';
    const knob = document.createElement('i');
    mini.append(knob);
    const val = document.createElement('span');
    val.className = 'tv';
    row.append(name, mini, val);
    row.addEventListener('pointerdown', (e) => {
      sel = i;
      paint();
      if (e.target === mini || mini.contains(e.target as Node)) {
        const setFrom = (ev: PointerEvent) => {
          const rect = mini.getBoundingClientRect();
          const k = Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width));
          set(i, r.min + k * (r.max - r.min));
        };
        setFrom(e);
        mini.setPointerCapture(e.pointerId);
        const move = (ev: PointerEvent) => setFrom(ev);
        const up = () => { mini.removeEventListener('pointermove', move); mini.removeEventListener('pointerup', up); };
        mini.addEventListener('pointermove', move);
        mini.addEventListener('pointerup', up);
      }
      panel.focus({ preventScroll: true });
    });
    panel.append(row);
    return { row, knob, val };
  });
  const area = document.createElement('textarea');
  area.hidden = true;
  area.spellcheck = false;
  area.setAttribute('aria-label', 'Look values as JSON');
  area.addEventListener('input', () => {
    try {
      apply(JSON.parse(area.value));
    } catch {
      /* keep typing */
    }
  });
  const hint = document.createElement('div');
  hint.className = 'hint';
  hint.textContent = '↑↓ select · ←→ change (⇧ coarse) · 0 reset · ⇧0 reset all · C copy JSON';
  panel.append(area, hint);

  const decimals = (step: number) => Math.max(0, Math.min(4, Math.ceil(-Math.log10(step))));
  function paint(): void {
    title.textContent = `Look · ${stage.look}`;
    rows.forEach((r, i) => {
      const v = r.get();
      const { row, knob, val } = rowEls[i];
      row.classList.toggle('sel', i === sel);
      knob.style.left = `${((v - r.min) / (r.max - r.min)) * 100}%`;
      val.textContent = v.toFixed(decimals(r.step));
    });
  }
  function set(i: number, v: number): void {
    const r = rows[i];
    const q = Math.round(Math.min(r.max, Math.max(r.min, v)) / r.step) * r.step;
    r.set(Number(q.toFixed(6)));
    paint();
  }
  function json(): string {
    return JSON.stringify({ look: look(), finish: f }, null, 2);
  }
  function apply(data: { look?: Record<string, unknown>; finish?: Record<string, unknown> }): void {
    if (data.look) Object.assign(look(), data.look);
    if (data.finish) {
      Object.assign(f, data.finish);
      u.bloomRadius.value = f.bloomRadius;
      u.aoIntensity.value = f.ao.intensity;
      stage.aoNode.radius.value = f.ao.radius;
      u.focusBand.value = f.dof.focusBand;
      u.farBlur.value = f.dof.farBlur;
      u.nearBlur.value = f.dof.nearBlur;
      u.maxBlur.value = f.dof.maxCoC * stage.pixelRatio;
      u.grain.value = f.grain;
    }
    refresh();
    paint();
  }

  panel.addEventListener('keydown', (e) => {
    if (e.target === area) return;
    let handled = true;
    const r = rows[sel];
    if (e.key === 'ArrowUp') sel = (sel + rows.length - 1) % rows.length;
    else if (e.key === 'ArrowDown') sel = (sel + 1) % rows.length;
    else if (e.key === 'ArrowRight') set(sel, r.get() + r.step * (e.shiftKey ? 10 : 1));
    else if (e.key === 'ArrowLeft') set(sel, r.get() - r.step * (e.shiftKey ? 10 : 1));
    else if (e.code === 'Digit0' && e.shiftKey) rows.forEach((_, i) => set(i, initial[i]));
    else if (e.code === 'Digit0') set(sel, initial[sel]);
    else if (e.code === 'KeyC' && !e.metaKey && !e.ctrlKey) {
      area.hidden = false;
      area.value = json();
      area.select();
      void navigator.clipboard?.writeText(area.value).catch(() => undefined);
    } else if (e.key === 'Escape') api.close();
    else handled = false;
    if (handled) {
      e.preventDefault();
      e.stopPropagation();
      paint();
    }
  });
  let release: (() => void) | null = null;
  const api: TunePanel = {
    open() {
      panel.hidden = false;
      paint();
      release = containFocus(panel);
      panel.focus({ preventScroll: true });
    },
    close() {
      panel.hidden = true;
      release?.();
      release = null;
    },
    toggle() { if (panel.hidden) api.open(); else api.close(); },
    get isOpen() { return !panel.hidden; },
    json,
  };
  window.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.code === 'Quote') {
      e.preventDefault();
      api.toggle();
    }
  });
  stage.onLook(() => { if (!panel.hidden) paint(); });
  if (new URLSearchParams(location.search).has('tune')) api.open();
  return api;
}
