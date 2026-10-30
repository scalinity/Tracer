// mountHud(root, schema): renders the glass-card HUD (references/hud-spec.md) from an
// ExhibitSchema and returns a small API. The HUD reads state and emits intents; it never owns
// exhibit state. All text goes in through textContent: data never becomes raw HTML.

import './tokens.css';
import './hud.css';
import type {
  ControlDef, ExhibitSchema, IconName, SegmentedDef, SliderDef, StatDef, TimeDef, ViewName,
} from './schema';
import { VIEW_LABELS } from './schema';
import { setPressed, wireDialog } from './a11y';
import { renderKeysSection } from './keys';

// ---------- icons (one stroke family, 24 × 24) ----------

type IconPart = ['path', string] | ['circle', number, number, number] | ['rect', number, number, number, number, number];

/** The schema's icons plus the HUD's own chevrons for the compact toggle. */
type HudIcon = IconName | 'collapse' | 'expand';

const ICONS: Record<HudIcon, IconPart[]> = {
  camera: [['rect', 3, 7, 12.5, 10, 2.2], ['path', 'M15.5 10.6 21 7.8v8.4l-5.5-2.8z']],
  help: [['circle', 12, 12, 9], ['path', 'M9.6 9.4a2.5 2.5 0 1 1 3.4 2.4c-.6.3-1 .8-1 1.5v.6'], ['path', 'M12 16.9v.1']],
  sow: [['path', 'M12 20.5v-7.5'], ['path', 'M12 13c.2-3.6 2.7-5.8 6.8-5.8-.1 3.7-2.6 5.8-6.8 5.8z'], ['path', 'M12 15.2c-.2-2.9-2.3-4.7-5.6-4.7.1 3 2.2 4.7 5.6 4.7z']],
  reset: [['path', 'M4.5 12a7.5 7.5 0 1 0 2.2-5.3'], ['path', 'M4.5 4.5v4.2h4.2']],
  'rotate-ccw': [['path', 'M4.5 12a7.5 7.5 0 1 0 2.2-5.3'], ['path', 'M4.5 4.5v4.2h4.2']],
  'rotate-cw': [['path', 'M19.5 12a7.5 7.5 0 1 1-2.2-5.3'], ['path', 'M19.5 4.5v4.2h-4.2']],
  play: [['path', 'M7.5 5.2v13.6L18.5 12z']],
  // Two filled bars: the play button fills its icon and draws no stroke, so open lines would vanish.
  pause: [['rect', 6.5, 5.2, 3.8, 13.6, 1], ['rect', 13.7, 5.2, 3.8, 13.6, 1]],
  close: [['path', 'M6.5 6.5l11 11M17.5 6.5l-11 11']],
  labels: [['path', 'M4 7.5h10.5l4 4.5-4 4.5H4z'], ['circle', 8, 12, 1.2]],
  hide: [['path', 'M3 12s3.2-6 9-6 9 6 9 6-3.2 6-9 6-9-6-9-6z'], ['circle', 12, 12, 2.6], ['path', 'M4 20 20 4']],
  lesson: [['path', 'M4 19.5h5v-5h5v-5h5'], ['circle', 19, 5.2, 1.7]],
  collapse: [['path', 'M6.5 14.5 12 9l5.5 5.5']],
  expand: [['path', 'M6.5 9.5 12 15l5.5-5.5']],
};

const SVG_NS = 'http://www.w3.org/2000/svg';

export function icon(name: HudIcon): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  for (const part of ICONS[name]) {
    const node = document.createElementNS(SVG_NS, part[0]);
    if (part[0] === 'path') node.setAttribute('d', part[1]);
    else if (part[0] === 'circle') {
      node.setAttribute('cx', String(part[1]));
      node.setAttribute('cy', String(part[2]));
      node.setAttribute('r', String(part[3]));
    } else {
      node.setAttribute('x', String(part[1]));
      node.setAttribute('y', String(part[2]));
      node.setAttribute('width', String(part[3]));
      node.setAttribute('height', String(part[4]));
      node.setAttribute('rx', String(part[5]));
    }
    svg.append(node);
  }
  return svg;
}

// ---------- inline markup: **bold**, `code`, {token:text} ----------

const RICH = /\*\*(.+?)\*\*|`([^`]+)`|\{([a-z][a-z0-9-]*):([^{}]+)\}/g;

/** Render the inline markup into `el` with text nodes and spans only. */
export function renderRich(el: HTMLElement, text: string): void {
  el.replaceChildren();
  let last = 0;
  for (const m of text.matchAll(RICH)) {
    const at = m.index ?? 0;
    if (at > last) el.append(text.slice(last, at));
    if (m[1] !== undefined) {
      const b = document.createElement('b');
      b.textContent = m[1];
      el.append(b);
    } else if (m[2] !== undefined) {
      const c = document.createElement('code');
      c.textContent = m[2];
      el.append(c);
    } else {
      const token = m[3];
      const body = m[4];
      if (token === 'b') {
        const b = document.createElement('b');
        b.textContent = body;
        el.append(b);
      } else {
        const span = document.createElement('span');
        span.style.color = `var(--${token})`;
        if (/\d/.test(body)) span.className = 'n';
        span.textContent = body;
        el.append(span);
      }
    }
    last = at + m[0].length;
  }
  if (last < text.length) el.append(text.slice(last));
}

// ---------- number formats (hud-spec §11) ----------

export const fmt = {
  /** "45.0°", "+13.3°" when signed. */
  angle(deg: number, o: { decimals?: number; signed?: boolean } = {}): string {
    const d = o.decimals ?? 1;
    const v = Math.abs(deg) < 0.5 * 10 ** -d ? 0 : deg;
    const s = v.toFixed(d);
    return `${o.signed && v > 0 ? '+' : ''}${s}°`;
  },
  /** "12.5%" with sensible precision: one decimal below 100. */
  percent(p: number, decimals = 1): string {
    const v = Math.abs(p) < 0.5 * 10 ** -decimals ? 0 : p;
    return `${v.toFixed(decimals)}%`;
  },
  /** A plain number with fixed decimals and an optional unit separated by a thin space. */
  num(v: number, decimals = 1, unit?: string): string {
    const x = Math.abs(v) < 0.5 * 10 ** -decimals ? 0 : v;
    return unit ? `${x.toFixed(decimals)} ${unit}` : x.toFixed(decimals);
  },
  /** Multiplier "4×" and magnification "×40". */
  times(v: number): string { return `${v}×`; },
  mag(v: number): string { return `×${v.toLocaleString('en-US')}`; },
};

// ---------- the API ----------

export type HudEvent = 'control' | 'preset' | 'view' | 'tool';
type Listener = (id: string, value?: number | string | boolean) => void;

export interface SafeArea { top: number; right: number; bottom: number; left: number; phone: boolean }

export interface Hud {
  root: HTMLElement;
  setStat(id: string, value: string | number | null, unit?: string): void;
  /** Recolor a stat's value with a token (null restores its schema color). */
  setStatColor(id: string, color: string | null): void;
  /** Move a slider's or time scrubber's amber event markers. */
  setMarkers(id: string, values: number[]): void;
  /** One causal sentence in the inline markup; colored spans name tokens. */
  setCaption(template: string): void;
  setControl(id: string, value: number | string | boolean): void;
  setControlEnabled(id: string, enabled: boolean): void;
  setPreset(id: string | null): void;
  setView(name: string): void;
  setTool(id: string, on: boolean): void;
  setLightTheme(on: boolean): void;
  hint(text: string, ms?: number): void;
  tip(text: string | null, x?: number, y?: number): void;
  openHelp(): void;
  closeHelp(): void;
  readonly helpOpen: boolean;
  /** Hide or show every HUD element except canvas, veil, vignette and toast. `quiet` skips the hint (captures). */
  setUiVisible(on: boolean, o?: { quiet?: boolean }): void;
  readonly uiVisible: boolean;
  /** Fold the card into a pill naming the preset and the view, and the title column down to its stats. */
  setCompact(on: boolean): void;
  readonly compact: boolean;
  /** The opening: the control card and the corner buttons fade away while the title column stays as the title card. */
  setPresenting(on: boolean): void;
  readonly presenting: boolean;
  on(event: HudEvent, cb: Listener): () => void;
  /** The free screen region the camera rig fits the subject into (CSS px). */
  safeArea(): SafeArea;
}

interface SliderHandle { el: HTMLElement; set(v: number): void; value(): number; }

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function ensure(root: HTMLElement, selector: string, make: () => HTMLElement): HTMLElement {
  return root.querySelector<HTMLElement>(selector) ?? root.appendChild(make());
}

function quantize(v: number, min: number, max: number, step: number): number {
  const q = Math.round((v - min) / step) * step + min;
  return Math.min(max, Math.max(min, Number(q.toFixed(6))));
}

/** Set by setControlEnabled on every slider and button of a disabled group. */
function disabled(e: Element): boolean {
  return e.getAttribute('aria-disabled') === 'true';
}

function makeSlider(def: SliderDef | TimeDef, emit: (v: number) => void): SliderHandle {
  const sl = el('div', 'sl');
  sl.tabIndex = 0;
  sl.setAttribute('role', 'slider');
  sl.setAttribute('aria-label', def.label);
  sl.setAttribute('aria-valuemin', String(def.min));
  sl.setAttribute('aria-valuemax', String(def.max));
  const track = el('div', 'track');
  const fill = el('div', 'fill');
  const knob = el('div', 'knob');
  sl.append(track);
  const pct = (v: number) => ((v - def.min) / (def.max - def.min)) * 100;
  for (const t of def.ticks ?? []) {
    const tick = el('div', 'tick');
    tick.style.left = `${pct(t)}%`;
    sl.append(tick);
  }
  for (const m of def.markers ?? []) {
    const mark = el('div', 'mark');
    mark.style.left = `${pct(m)}%`;
    sl.append(mark);
  }
  sl.append(fill, knob);
  let value = def.value;
  // setControl feeds the eased display value every frame, so a held arrow's repeats step from the
  // value the slider last sent: re-quantising a display that lags behind would undershoot.
  let sent = value;
  const send = (v: number) => {
    sent = v;
    emit(v);
  };
  const coarse = (def as SliderDef).coarse ?? def.step * 5;
  const spoken = def.valueText ?? ((v: number) => `${v}${def.unit ? ' ' + def.unit : ''}`);
  const set = (v: number) => {
    value = v;
    const p = pct(v);
    fill.style.width = `${p}%`;
    knob.style.left = `${p}%`;
    sl.setAttribute('aria-valuenow', String(v));
    sl.setAttribute('aria-valuetext', spoken(v));
  };
  const fromPointer = (e: PointerEvent) => {
    const r = sl.getBoundingClientRect();
    const k = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    return quantize(def.min + k * (def.max - def.min), def.min, def.max, def.step);
  };
  sl.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || disabled(sl)) return;
    sl.setPointerCapture(e.pointerId);
    sl.focus({ preventScroll: true });
    send(fromPointer(e));
    e.preventDefault();
  });
  sl.addEventListener('pointermove', (e) => {
    if (sl.hasPointerCapture(e.pointerId) && !disabled(sl)) send(fromPointer(e));
  });
  sl.addEventListener('keydown', (e) => {
    if (disabled(sl)) return;
    const from = e.repeat ? sent : value;
    const big = e.shiftKey ? coarse : def.step;
    let next: number | null = null;
    if (e.key === 'ArrowRight' || e.key === 'ArrowUp') next = from + big;
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') next = from - big;
    else if (e.key === 'PageUp') next = from + coarse;
    else if (e.key === 'PageDown') next = from - coarse;
    else if (e.key === 'Home') next = def.min;
    else if (e.key === 'End') next = def.max;
    if (next === null) return;
    e.preventDefault();
    e.stopPropagation();
    send(quantize(next, def.min, def.max, def.step));
  });
  set(value);
  return { el: sl, set, value: () => value };
}

export function mountHud(root: HTMLElement, schema: ExhibitSchema): Hud {
  const listeners: Record<HudEvent, Set<Listener>> = { control: new Set(), preset: new Set(), view: new Set(), tool: new Set() };
  const emit = (ev: HudEvent, id: string, value?: number | string | boolean) => {
    for (const cb of listeners[ev]) cb(id, value);
  };

  for (const [name, value] of Object.entries(schema.semanticColors ?? {})) {
    document.documentElement.style.setProperty(`--${name}`, value);
  }

  const world = ensure(root, '#world', () => el('main'));
  world.id = 'world';
  world.setAttribute('role', 'img');
  world.setAttribute('aria-label', schema.setSentence);
  const loading = world.querySelector<HTMLElement>('#loading');
  if (loading) loading.textContent = schema.loading;
  ensure(root, '#veil', () => Object.assign(el('div'), { id: 'veil' }));
  ensure(root, '.vignette', () => el('div', 'vignette'));

  // ---- title column ----
  const side = ensure(root, 'aside.side', () => el('aside', 'side'));
  side.replaceChildren();
  const brand = el('div', 'brand');
  const h1 = el('h1');
  h1.append(el('span', undefined, schema.brand.kicker), ' ', el('b', undefined, schema.brand.title));
  if (schema.brand.titleSize) h1.style.setProperty('--title-size', `${schema.brand.titleSize}px`);
  brand.append(h1, el('small', undefined, schema.brand.lede));
  const stats = el('div', 'stats');
  const statEls = new Map<string, { v: HTMLElement; def: StatDef }>();
  for (const s of schema.stats) {
    const tile = el('div', `stat${s.hot ? ' hot' : ''}`);
    if (s.color && !s.hot) tile.style.setProperty('--stat-color', `var(--${s.color})`);
    const v = el('div', 'v', '—');
    tile.append(el('div', 'k', s.label), v);
    stats.append(tile);
    statEls.set(s.id, { v, def: s });
  }
  const why = el('p', 'why');
  why.setAttribute('aria-live', 'polite');
  side.append(brand, stats, why);

  // ---- control card ----
  const card = ensure(root, 'section.ctl', () => el('section', 'ctl card'));
  card.replaceChildren();
  card.setAttribute('aria-label', 'Controls');
  if (schema.cardWidth) card.style.setProperty('--card-w', `${schema.cardWidth}px`);

  const sliders = new Map<string, { handle: SliderHandle; def: SliderDef | TimeDef; val: HTMLElement; grp: HTMLElement; last?: number }>();
  const segs = new Map<string, { buttons: Map<string, HTMLButtonElement>; grp: HTMLElement }>();
  const playButtons = new Map<string, HTMLButtonElement>();

  const label = (text: string, keyHint?: string, withValue = false) => {
    const l = el('div', 'lbl');
    const name = el('span', undefined, text);
    if (keyHint) name.append(el('span', 'kh', keyHint));
    l.append(name);
    const val = el('span', 'val');
    if (withValue) l.append(val);
    return { l, val };
  };

  const segmented = (def: SegmentedDef | { id: string; label: string; options: { value: string; label: string; icon?: IconName; ariaLabel?: string }[]; value: string; keyHint?: string }, onPick: (v: string) => void) => {
    const grp = el('div', 'grp');
    const { l } = label(def.label, def.keyHint);
    const seg = el('div', 'seg');
    seg.setAttribute('role', 'group');
    seg.setAttribute('aria-label', def.label);
    const buttons = new Map<string, HTMLButtonElement>();
    for (const o of def.options) {
      const b = el('button');
      b.type = 'button';
      if (o.icon) b.append(icon(o.icon));
      b.append(o.label);
      if (o.ariaLabel) b.setAttribute('aria-label', o.ariaLabel);
      setPressed(b, o.value === def.value);
      b.addEventListener('click', () => { if (!disabled(b)) onPick(o.value); });
      buttons.set(o.value, b);
      seg.append(b);
    }
    grp.append(l, seg);
    segs.set(def.id, { buttons, grp });
    return grp;
  };

  const control = (def: ControlDef): HTMLElement => {
    if (def.kind === 'segmented') return segmented(def, (v) => emit('control', def.id, v));
    const grp = el('div', `grp grow ${def.kind === 'time' ? 'timegrp' : 'slider'}`);
    const { l, val } = label(def.label, def.keyHint, true);
    const handle = makeSlider(def, (v) => emit('control', def.id, v));
    if (def.kind === 'slider') {
      grp.append(l, handle.el);
    } else {
      const row = el('div', 'time');
      const play = el('button', 'play');
      play.type = 'button';
      play.setAttribute('aria-label', 'Play');
      play.append(icon(def.playing ? 'pause' : 'play'));
      play.addEventListener('click', () => { if (!disabled(play)) emit('control', `${def.id}.play`, play.getAttribute('aria-pressed') !== 'true'); });
      setPressed(play, !!def.playing);
      playButtons.set(def.id, play);
      row.append(play, handle.el);
      if (def.speeds?.length) {
        const speedSeg = segmented({
          id: `${def.id}.speed`, label: '', value: String(def.speed ?? def.speeds[0]),
          options: def.speeds.map((s) => ({ value: String(s), label: def.speedLabel?.(s) ?? `${s}×`, ariaLabel: def.speedLabel ? def.speedLabel(s) : `${s} times speed` })),
        }, (v) => emit('control', `${def.id}.speed`, Number(v)));
        const segEl = speedSeg.querySelector('.seg');
        if (segEl) row.append(segEl);
      }
      grp.append(l, row);
    }
    sliders.set(def.id, { handle, def, val, grp });
    val.textContent = (def.format ?? ((v: number) => `${v}${def.unit ?? ''}`))(def.value);
    return grp;
  };

  if (schema.presets.length) {
    const row = el('div', 'row');
    const presetGrp = segmented({
      id: '__presets', label: 'Try', value: '',
      options: schema.presets.map((p) => ({ value: p.id, label: p.label })),
      keyHint: schema.presets.length > 1 ? `1–${schema.presets.length}` : '1',
    }, (v) => emit('preset', v));
    presetGrp.classList.add('grow', 'presets');
    row.append(presetGrp);
    card.append(row);
  }
  const rows = schema.controls.map((r) => {
    const row = el('div', 'row');
    for (const def of r) row.append(control(def));
    card.append(row);
    return row;
  });
  let lastRow = rows[rows.length - 1];
  if (!lastRow || lastRow.querySelector('.slider, .timegrp')) {
    lastRow = el('div', 'row');
    card.append(lastRow);
  }
  if (schema.views.length > 1) {
    lastRow.append(segmented({
      id: '__views', label: 'View', value: schema.views[0], keyHint: '⇧V',
      options: schema.views.map((v: ViewName) => ({ value: v, label: VIEW_LABELS[v] })),
    }, (v) => emit('view', v)));
  }
  const tools = el('div', 'tools');
  const toolEls = new Map<string, HTMLButtonElement>();
  for (const t of schema.tools) {
    const b = el('button', 'tool');
    b.type = 'button';
    b.setAttribute('aria-label', t.label);
    b.title = t.label;
    b.dataset.tool = t.id;
    b.append(icon(t.icon));
    if (t.toggle) setPressed(b, false);
    b.addEventListener('click', () => emit('tool', t.id));
    toolEls.set(t.id, b);
    tools.append(b);
  }
  const collapse = el('button', 'tool collapse');
  collapse.type = 'button';
  collapse.append(icon('collapse'));
  tools.append(collapse);
  lastRow.append(tools);
  // The card's compact form: the active preset and view, and a chevron to unfold it.
  const pill = el('button', 'pill');
  pill.type = 'button';
  pill.title = 'Expand the controls (H)';
  pill.setAttribute('aria-expanded', 'false');
  const pillText = el('span');
  pill.append(pillText, icon('expand'));
  card.append(pill);

  // ---- utilities ----
  const br = ensure(root, '.br', () => el('div', 'br'));
  br.replaceChildren();
  const hideBtn = el('button', 'ib');
  hideBtn.type = 'button';
  hideBtn.setAttribute('aria-label', 'Hide the interface (/)');
  hideBtn.title = 'Hide the interface (/)';
  hideBtn.append(icon('hide'));
  br.append(hideBtn);
  if (schema.credit) {
    const a = el('a', 'credit', schema.credit.label);
    a.href = schema.credit.href;
    a.target = '_blank';
    a.rel = 'noopener';
    br.append(a);
  }

  // ---- toast, tip, help ----
  const hintEl = ensure(root, '#ui-hint', () => Object.assign(el('div'), { id: 'ui-hint' }));
  hintEl.setAttribute('role', 'status');
  const tipEl = ensure(root, '#tip', () => Object.assign(el('div'), { id: 'tip' }));
  const help = ensure(root, 'dialog#help', () => Object.assign(el('dialog'), { id: 'help' })) as HTMLDialogElement;
  help.setAttribute('aria-labelledby', 'help-title');
  help.replaceChildren();
  const close = el('button', 'close', '×');
  close.type = 'button';
  close.setAttribute('aria-label', 'Close');
  close.addEventListener('click', () => help.close());
  const h2 = el('h2', undefined, schema.help.h2);
  h2.id = 'help-title';
  help.append(close, h2);
  const section = (h3: string, body: string[]) => {
    help.append(el('h3', undefined, h3));
    for (const para of body) {
      const p = el('p');
      renderRich(p, para);
      help.append(p);
    }
  };
  section('What you are looking at', [schema.help.intro]);
  for (const s of schema.help.sections) section(s.h3, s.body);
  if (schema.help.misconception) section(schema.help.misconception.h3, schema.help.misconception.body);
  section(schema.help.exact.h3 || 'What is exact and what is not', schema.help.exact.body);
  help.append(el('h3', undefined, 'Keys'), renderKeysSection(schema.keys));
  if (schema.help.footnote) {
    const f = el('p', 'foot');
    renderRich(f, schema.help.footnote);
    help.append(f);
  }
  const dialog = wireDialog(help);

  // ---- behavior ----
  let hintTimer = 0;
  let uiVisible = true;
  let compact = false;
  let presenting = false;
  let tipVisible = false;
  let presetNow: string | null = null;
  let viewNow: string = schema.views[0];
  const syncPill = () => {
    const view = VIEW_LABELS[viewNow as ViewName] ?? viewNow;
    const preset = schema.presets.length ? schema.presets.find((p) => p.id === presetNow)?.label ?? 'Custom' : null;
    if (pillText.textContent === (preset ? `${preset} · ${view}` : view)) return;
    // The separator is its own span: Outfit's word space alone crowds the dot.
    pillText.replaceChildren(...(preset ? [preset, el('i', undefined, ' · '), view] : [view]));
  };

  const hud: Hud = {
    root,
    setStat(id, value, unit) {
      const s = statEls.get(id);
      if (!s) return;
      s.v.replaceChildren();
      if (value === null || value === undefined || value === '') {
        s.v.textContent = '—';
        return;
      }
      s.v.append(String(value));
      const u = unit ?? s.def.unit;
      if (u) s.v.append(el('i', undefined, u));
    },
    setStatColor(id, color) {
      const s = statEls.get(id);
      const tile = s?.v.parentElement;
      if (!s || !tile || s.def.hot) return;
      const c = color ?? s.def.color;
      if (c) tile.style.setProperty('--stat-color', `var(--${c})`);
      else tile.style.removeProperty('--stat-color');
    },
    setMarkers(id, values) {
      const s = sliders.get(id);
      if (!s) return;
      const sl = s.handle.el;
      sl.querySelectorAll('.mark').forEach((m) => m.remove());
      for (const v of values) {
        const mark = el('div', 'mark');
        mark.style.left = `${((v - s.def.min) / (s.def.max - s.def.min)) * 100}%`;
        sl.insertBefore(mark, sl.querySelector('.fill'));
      }
    },
    setCaption(template) {
      renderRich(why, template);
    },
    setControl(id, value) {
      const sl = sliders.get(id);
      if (sl && typeof value === 'number') {
        // Called every frame with eased values: touch the DOM only when the value moved.
        if (sl.last !== undefined && Math.abs(sl.last - value) < 1e-4) return;
        sl.last = value;
        sl.handle.set(value);
        sl.val.textContent = (sl.def.format ?? ((v: number) => `${v}${sl.def.unit ?? ''}`))(value);
        return;
      }
      const play = playButtons.get(id.replace(/\.play$/, ''));
      if (play && id.endsWith('.play')) {
        // The label stays "Play": aria-pressed alone says whether it is playing.
        setPressed(play, !!value);
        play.replaceChildren(icon(value ? 'pause' : 'play'));
        return;
      }
      const seg = segs.get(id);
      if (seg) for (const [v, b] of seg.buttons) setPressed(b, v === String(value));
    },
    setControlEnabled(id, enabled) {
      const g = sliders.get(id)?.grp ?? segs.get(id)?.grp;
      if (!g) return;
      g.classList.toggle('disabled', !enabled);
      g.querySelectorAll<HTMLElement>('.sl, button').forEach((c) => {
        c.setAttribute('aria-disabled', String(!enabled));
        if (c.classList.contains('sl')) c.tabIndex = enabled ? 0 : -1;
      });
    },
    setPreset(id) {
      const seg = segs.get('__presets');
      if (seg) for (const [v, b] of seg.buttons) setPressed(b, v === id);
      presetNow = id;
      syncPill();
    },
    setView(name) {
      const seg = segs.get('__views');
      if (seg) for (const [v, b] of seg.buttons) setPressed(b, v === name);
      viewNow = name;
      syncPill();
    },
    setTool(id, on) {
      const b = toolEls.get(id);
      if (b) setPressed(b, on);
    },
    setLightTheme(on) {
      root.classList.toggle('light', on);
    },
    hint(text, ms = 2600) {
      hintEl.textContent = text;
      hintEl.classList.add('show');
      window.clearTimeout(hintTimer);
      hintTimer = window.setTimeout(() => hintEl.classList.remove('show'), ms);
    },
    tip(text, x = 0, y = 0) {
      if (!text) {
        if (tipVisible) tipEl.classList.remove('show');
        tipVisible = false;
        return;
      }
      if (tipEl.textContent !== text) tipEl.textContent = text;
      const r = root.getBoundingClientRect();
      tipEl.style.left = `${x - r.left}px`;
      tipEl.style.top = `${y - r.top}px`;
      if (!tipVisible) tipEl.classList.add('show');
      tipVisible = true;
    },
    openHelp: () => dialog.open(),
    closeHelp: () => dialog.close(),
    get helpOpen() { return help.open; },
    setUiVisible(on, o = {}) {
      uiVisible = on;
      // The "bring it back" toast has done its job once the interface is back.
      if (on) hintEl.classList.remove('show');
      root.classList.toggle('ui-off', !on);
      // What ui-off fades also leaves the tab order and the accessibility tree.
      root.querySelectorAll<HTMLElement>('.side, .ctl, .br, .labels-layer, #tip').forEach((e) => { e.inert = !on; });
      if (!on && !o.quiet) hud.hint(matchMedia('(pointer: coarse)').matches ? 'Tap the scene to bring the interface back' : 'Press / to bring the interface back', 3200);
    },
    get uiVisible() { return uiVisible; },
    setCompact(on) {
      // Focus on a part about to fold away moves to the control that takes its place.
      const refocus = on !== compact && card.contains(document.activeElement);
      compact = on;
      root.classList.toggle('compact', on);
      // What compact hides also leaves the tab order and the accessibility tree.
      for (const e of [brand, why, ...card.querySelectorAll<HTMLElement>(':scope > .row')]) e.inert = on;
      pill.inert = !on;
      const name = on ? 'Expand the controls' : 'Collapse the controls';
      collapse.setAttribute('aria-label', name);
      collapse.title = `${name} (H)`;
      collapse.setAttribute('aria-expanded', String(!on));
      if (refocus) (on ? pill : collapse).focus({ preventScroll: true });
    },
    get compact() { return compact; },
    setPresenting(on) {
      presenting = on;
      root.classList.toggle('presenting', on);
      // What the opening fades also leaves the tab order and the accessibility tree.
      for (const e of [card, br]) e.inert = on || !uiVisible;
    },
    get presenting() { return presenting; },
    on(event, cb) {
      listeners[event].add(cb);
      return () => listeners[event].delete(cb);
    },
    safeArea() {
      const w = root.clientWidth;
      const h = root.clientHeight;
      const phone = w <= 900;
      const sideR = side.getBoundingClientRect();
      const cardR = card.getBoundingClientRect();
      if (phone) {
        return { top: sideR.bottom + 8, bottom: Math.max(16, h - cardR.top + 8), left: 12, right: 12, phone };
      }
      const inset = Math.min(sideR.right + 8, w * 0.3);
      return { top: Math.min(cardR.bottom + 8, h * 0.42), bottom: 18, left: inset, right: inset, phone };
    },
  };

  for (const [id, s] of sliders) if (s.def.kind === 'slider' && s.def.disabled) hud.setControlEnabled(id, false);
  hud.setCompact(false);
  syncPill();
  collapse.addEventListener('click', () => hud.setCompact(true));
  pill.addEventListener('click', () => hud.setCompact(false));
  hideBtn.addEventListener('click', () => hud.setUiVisible(false));
  // Touch screens have no / key: a tap on the scene brings the interface back.
  let tapAt: { x: number; y: number } | null = null;
  world.addEventListener('pointerdown', (e) => { tapAt = e.pointerType === 'mouse' ? null : { x: e.clientX, y: e.clientY }; });
  world.addEventListener('pointerup', (e) => {
    if (tapAt && !uiVisible && Math.hypot(e.clientX - tapAt.x, e.clientY - tapAt.y) < 10) hud.setUiVisible(true);
    tapAt = null;
  });
  return hud;
}
