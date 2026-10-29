// Accessibility helpers: reduced motion, dialog focus, pressed state.
// Reduced motion means: no idle drift, no auto-cinematic, instant view swaps (style-bible §10).

const query = typeof window !== 'undefined' ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
const forced = typeof location !== 'undefined' ? new URLSearchParams(location.search).get('motion') : null;

/** True when the visitor asked for reduced motion (or ?motion=reduce forces it for testing). */
export function prefersReducedMotion(): boolean {
  if (forced === 'reduce') return true;
  if (forced === 'full') return false;
  return query?.matches ?? false;
}

export function onReducedMotionChange(cb: (reduced: boolean) => void): () => void {
  if (!query) return () => {};
  const handler = () => cb(prefersReducedMotion());
  query.addEventListener('change', handler);
  return () => query.removeEventListener('change', handler);
}

export function setPressed(el: HTMLElement, on: boolean): void {
  el.setAttribute('aria-pressed', on ? 'true' : 'false');
  el.classList.toggle('on', on);
}

/**
 * Native <dialog> wiring: showModal traps focus and Escape closes it; this adds focus restore and
 * closing on a backdrop click.
 */
export function wireDialog(dialog: HTMLDialogElement): { open: () => void; close: () => void } {
  let returnTo: HTMLElement | null = null;
  dialog.addEventListener('click', (e) => {
    // Clicks on the dialog's padding and between its paragraphs target it too: close only for a
    // click outside its box, on the backdrop.
    const r = dialog.getBoundingClientRect();
    const outside = e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
    if (e.target === dialog && outside) dialog.close();
  });
  dialog.addEventListener('close', () => {
    returnTo?.focus({ preventScroll: true });
    returnTo = null;
  });
  return {
    open() {
      if (dialog.open) return;
      returnTo = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      dialog.showModal();
      dialog.scrollTop = 0;
    },
    close() {
      dialog.close();
    },
  };
}

/** Keep keyboard focus inside a non-modal panel (the tune panel) while it is open. */
export function containFocus(panel: HTMLElement): () => void {
  const handler = (e: KeyboardEvent) => {
    if (e.key !== 'Tab') return;
    const items = Array.from(panel.querySelectorAll<HTMLElement>('button, [tabindex]:not([tabindex="-1"]), textarea, input'))
      .filter((el) => !el.hasAttribute('disabled') && el.offsetParent !== null);
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { last.focus(); e.preventDefault(); }
    else if (!e.shiftKey && document.activeElement === last) { first.focus(); e.preventDefault(); }
  };
  panel.addEventListener('keydown', handler);
  return () => panel.removeEventListener('keydown', handler);
}
