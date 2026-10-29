// Guided lesson (exhibit-design §5): the "let me try" beside the presets' "show me". Each step asks
// for one action in the caption and points at the part with an amber hint pill, reads the model
// every frame until the visitor has done it, then says what they caused, with their own numbers,
// and asks for the next. The HUD stays as it is: no panel, no counter, no Next button. The tour
// performs the same steps (perform()), so the lesson is written once.

/** Anything with setVisible: a WorldLabels handle for an amber hint pill. */
export interface LessonHint { setVisible(on: boolean): void }

export interface LessonStep {
  /** One imperative sentence, in the caption's inline markup. */
  ask: string;
  /** The amber hint pill on the part to use, shown while the step waits. */
  hint?: LessonHint | null;
  /** Arranges what the step needs as it begins, with tweens: a filter lifted out, an angle to start from. */
  enter?: () => void;
  /**
   * True while the visitor's result stands. Read the model's state (what the visitor set), never the
   * HUD, and wait for the displayed values to settle so the answer quotes the numbers on screen.
   */
  done: () => boolean;
  /** Seconds `done` must hold before the step counts (default 0.5), so a drag passing through does not. */
  hold?: number;
  /** One or two sentences on what the visitor caused, with their numbers; called once, as the step completes. */
  said: () => string;
  /** What the tour does to perform this step itself. */
  perform: () => void;
}

/** Seconds the last answer stays at least, before the live caption may take over again. */
const LINGER = 8;

export class Lesson {
  readonly steps: LessonStep[];
  private onChange: (active: boolean) => void;
  private index = -1;
  private held = 0;
  private text = '';
  /** Set when the last step completes: the lesson keeps the caption a while, then until the live one changes. */
  private finished: { t: number; live: string | null } | null = null;

  constructor(steps: LessonStep[], o: { onChange?: (active: boolean) => void } = {}) {
    this.steps = steps;
    this.onChange = o.onChange ?? (() => {});
  }

  get active(): boolean { return this.index >= 0; }

  /** The caption is the lesson's: a step waits, or the last answer lingers. The HUD shows the caption in every layout meanwhile. */
  get speaking(): boolean { return this.index >= 0 || this.finished !== null; }

  start(): void {
    this.finished = null;
    this.hideHints();
    this.begin(0, '');
    this.onChange(true);
  }

  stop(): void {
    this.finished = null;
    if (this.index < 0) return;
    this.index = -1;
    this.hideHints();
    this.onChange(false);
  }

  toggle(): void {
    if (this.active) this.stop();
    else this.start();
  }

  /** A tour beat that performs step i, so the tour shows what the lesson will ask the visitor to do. */
  perform(i: number): () => void {
    return () => this.steps[i]?.perform();
  }

  update(dt: number): void {
    if (this.finished) this.finished.t += dt;
    if (this.index < 0) return;
    const step = this.steps[this.index];
    if (!step.done()) {
      this.held = 0;
      return;
    }
    this.held += dt;
    if (this.held < (step.hold ?? 0.5)) return;
    step.hint?.setVisible(false);
    const said = step.said();
    if (this.index + 1 < this.steps.length) {
      this.begin(this.index + 1, said);
      return;
    }
    this.text = said;
    this.index = -1;
    this.finished = { t: 0, live: null };
    this.onChange(false);
  }

  /** What the caption shows: the lesson's words while it runs; its last words a while after, until `live` changes. */
  caption(live: string): string {
    if (this.index >= 0) return this.text;
    const f = this.finished;
    if (!f) return live;
    if (f.t < LINGER || f.live === null) {
      f.live = live;
      return this.text;
    }
    if (live === f.live) return this.text;
    this.finished = null;
    return live;
  }

  private begin(i: number, said: string): void {
    this.index = i;
    this.held = 0;
    const step = this.steps[i];
    step.enter?.();
    step.hint?.setVisible(true);
    // The answer to the last step, then the next request in bold: the caption always says both.
    this.text = said ? `${said} **${step.ask}**` : `**${step.ask}**`;
  }

  private hideHints(): void {
    for (const s of this.steps) s.hint?.setVisible(false);
  }
}
