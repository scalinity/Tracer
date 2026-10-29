// The HUD of a whole exhibit as data. mountHud() renders it; keys.ts binds its key map and writes
// the help sheet's Keys section from it. Text fields accept a small inline markup (see hud.ts
// renderRich): **bold**, `code`, and {token:text} for a span colored with a CSS token
// (cyan, amber, coral, violet, green, or an exhibit token from semanticColors). No raw HTML.

/** A CSS color token name: house accents or one of the exhibit's semantic colors. */
export type ColorToken = 'cyan' | 'amber' | 'coral' | 'violet' | 'green' | (string & {});

export type IconName =
  | 'camera' | 'help' | 'sow' | 'reset' | 'rotate-ccw' | 'rotate-cw' | 'play' | 'pause' | 'close' | 'labels' | 'hide' | 'lesson';

export interface StatDef {
  id: string;
  /** Uppercase mono micro label, e.g. "THROUGH". Written as given; CSS uppercases it. */
  label: string;
  unit?: string;
  /** The headline quantity: value drawn in cyan. Exactly one stat should be hot. */
  hot?: boolean;
  /** Semantic object color for the value (a token name). */
  color?: ColorToken;
}

export interface PresetDef {
  id: string;
  /** One or two words, said the way a teacher would: "Aligned", "Crossed". Keys 1–4 by order. */
  label: string;
}

export interface SliderDef {
  kind: 'slider';
  id: string;
  label: string;
  min: number;
  max: number;
  /** Arrow-key step. */
  step: number;
  /** Shift+arrow step (defaults to 5 × step). */
  coarse?: number;
  value: number;
  unit?: string;
  decimals?: number;
  /** Show a + sign for positive values (signed quantities). */
  signed?: boolean;
  /** Tick positions in value units (0, presets, limits). */
  ticks?: number[];
  /** Amber event markers above the track (timelines). */
  markers?: number[];
  /** Key hint shown after the label on wide screens, e.g. "[ ]". */
  keyHint?: string;
  /** Live value text in the label. Defaults to value + unit. */
  format?: (v: number) => string;
  /** Spoken value for aria-valuetext, e.g. "45.0 degrees". */
  valueText?: (v: number) => string;
  disabled?: boolean;
}

export interface SegmentOption {
  value: string;
  label: string;
  icon?: IconName;
  /** Accessible name when the label is a glyph. */
  ariaLabel?: string;
}

export interface SegmentedDef {
  kind: 'segmented';
  id: string;
  label: string;
  options: SegmentOption[];
  value: string;
  keyHint?: string;
}

export interface TimeDef {
  kind: 'time';
  id: string;
  label: string;
  min: number;
  max: number;
  step: number;
  value: number;
  unit?: string;
  ticks?: number[];
  markers?: number[];
  /** Speed options as multipliers, e.g. [1, 4]. */
  speeds?: number[];
  /** Speed button text; defaults to "1×". Name the rate when time is scaled ("3 min/s"). */
  speedLabel?: (s: number) => string;
  speed?: number;
  playing?: boolean;
  keyHint?: string;
  format?: (v: number) => string;
  valueText?: (v: number) => string;
}

export type ControlDef = SliderDef | SegmentedDef | TimeDef;
/** One row of the control card; groups sit side by side. */
export type ControlRow = ControlDef[];

export type ViewName = 'lab' | 'exploded' | 'studio' | 'assembled';

export interface ToolDef {
  id: string;
  /** Accessible name, verb first: "Play the cinematic tour". */
  label: string;
  icon: IconName;
  /** Stays pressed while active (cinematic). */
  toggle?: boolean;
}

export interface KeyDef {
  /** Key combos as typed: "1", "[", "Shift+M", "Cmd+'". Each is bound to the action of the same name. */
  keys: string[];
  /** What it does, for the help sheet: "Turn the analyzer (Shift: 5°)". */
  does: string;
}

export interface HelpSection {
  h3: string;
  /** Paragraphs in the inline markup. */
  body: string[];
}

export interface HelpSheet {
  /** Names the idea, not the app: "Why two filters go dark". */
  h2: string;
  /** "What you are looking at", one paragraph. */
  intro: string;
  /** Two to four mechanism sections, each titled with an action or a question. */
  sections: HelpSection[];
  misconception?: HelpSection;
  /** "What is exact and what is not". */
  exact: HelpSection;
  /** Assumptions footnote. */
  footnote?: string;
}

export interface ExhibitSchema {
  id: string;
  brand: { kicker: string; title: string; titleSize?: number; lede: string };
  /** Canvas aria-label and the Lab art direction: "[apparatus] in a [place] at [time]". */
  setSentence: string;
  /** Themed loading line: "Aligning the filters…". */
  loading: string;
  stats: [StatDef, StatDef, StatDef];
  /** Three or four, keys 1–4. */
  presets: PresetDef[];
  /** Up to three rows of slider, segmented and time groups. TRY sits above them; VIEW and tools join the last row. */
  controls: ControlRow[];
  views: ViewName[];
  tools: ToolDef[];
  keys: KeyDef[];
  help: HelpSheet;
  /** Exhibit object colors, e.g. { leaf: '#9be07a' }; become CSS tokens --leaf. */
  semanticColors?: Record<string, string>;
  /** Daniel's mark, bottom right. */
  credit?: { label: string; href: string };
  /** Control card width in px (410 to 468). */
  cardWidth?: number;
}

export const VIEW_LABELS: Record<ViewName, string> = {
  lab: 'Lab',
  exploded: 'Exploded',
  studio: 'Studio',
  assembled: 'Assembled',
};
