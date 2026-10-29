// Keyboard map (references/exhibit-design.md §7). The schema's KeyDef list is the single source:
// bindKeys() wires each combo to the action of the same name, renderKeysSection() writes the
// help sheet's Keys section. Combos match physical keys (event.code), so "[" still matches when
// Shift is held and the handler can read event.shiftKey for a coarse step.

import type { KeyDef } from './schema';

export type KeyAction = (e: KeyboardEvent) => void;

const CODES: Record<string, string> = {
  '[': 'BracketLeft', ']': 'BracketRight', ',': 'Comma', '.': 'Period', '/': 'Slash', "'": 'Quote',
  ';': 'Semicolon', '-': 'Minus', '=': 'Equal', '`': 'Backquote', Space: 'Space', Backspace: 'Backspace',
  Enter: 'Enter', Escape: 'Escape', Left: 'ArrowLeft', Right: 'ArrowRight', Up: 'ArrowUp', Down: 'ArrowDown',
};

interface Combo { combo: string; code: string; shift: boolean; mod: boolean; alt: boolean }

export function parseCombo(combo: string): Combo {
  const parts = combo.split('+');
  const key = parts.pop() ?? '';
  const mods = parts.map((p) => p.toLowerCase());
  let code: string;
  if (/^[a-z]$/i.test(key)) code = `Key${key.toUpperCase()}`;
  else if (/^[0-9]$/.test(key)) code = `Digit${key}`;
  else code = CODES[key] ?? key;
  return {
    combo,
    code,
    shift: mods.includes('shift'),
    mod: mods.includes('cmd') || mods.includes('ctrl') || mods.includes('mod'),
    alt: mods.includes('alt') || mods.includes('option'),
  };
}

function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
}

/**
 * Bind every combo in `keys` that has an action in `actions` (looked up by the combo string, e.g.
 * actions['Shift+M']). Combos without an action are display-only (the camera rig owns W A S D).
 * Held keys repeat only for combos listed in `repeat`.
 */
export function bindKeys(keys: KeyDef[], actions: Record<string, KeyAction>, opts: { repeat?: string[] } = {}): () => void {
  const combos = keys.flatMap((k) => k.keys).filter((c) => actions[c]).map(parseCombo);
  const repeat = new Set(opts.repeat ?? []);
  const onKey = (e: KeyboardEvent) => {
    if (typing(e.target)) return;
    if (document.querySelector('dialog[open]')) return;
    const mod = e.metaKey || e.ctrlKey;
    const candidates = combos.filter((c) => c.code === e.code && c.mod === mod && c.alt === e.altKey);
    const hit = candidates.find((c) => c.shift === e.shiftKey) ?? candidates.find((c) => !c.shift);
    if (!hit) return;
    if (e.repeat && !repeat.has(hit.combo)) {
      e.preventDefault();
      return;
    }
    e.preventDefault();
    actions[hit.combo](e);
  };
  window.addEventListener('keydown', onKey);
  return () => window.removeEventListener('keydown', onKey);
}

/**
 * The help sheet's Keys section: chips for each combo, then what it does. One line follows in every
 * exhibit: what reduced motion changes (a11y.ts, camera.ts, stage.ts, cinematic.ts).
 */
export function renderKeysSection(keys: KeyDef[]): DocumentFragment {
  const grid = document.createElement('div');
  grid.className = 'keys';
  for (const k of keys) {
    const chips = document.createElement('div');
    chips.className = 'chips';
    for (const c of k.keys) {
      const kbd = document.createElement('kbd');
      kbd.textContent = c;
      chips.append(kbd);
    }
    const does = document.createElement('div');
    does.textContent = k.does;
    grid.append(chips, does);
  }
  const line = (lead: string, parts: (string | { code: string })[]): HTMLElement => {
    const p = document.createElement('p');
    const b = document.createElement('b');
    b.textContent = lead;
    p.append(b, ' ');
    for (const part of parts) {
      if (typeof part === 'string') {
        p.append(part);
      } else {
        const code = document.createElement('code');
        code.textContent = part.code;
        p.append(code);
      }
    }
    return p;
  };
  const section = document.createDocumentFragment();
  section.append(
    grid,
    line('Reduced motion.', [
      'When your system asks for less motion, or the address carries ', { code: 'motion=reduce' },
      ', the exhibit opens still instead of with the tour, the camera holds still and jumps instead of gliding, views switch without the fade, and the tour holds each shot and cuts between them.',
    ]),
  );
  return section;
}
