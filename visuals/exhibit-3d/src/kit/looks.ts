// Look presets: one data object per view, applied as a whole by stage.setLook().
// Values come from references/style-bible.md §4. Dial them with the tune panel (?tune),
// then write the tuned numbers back here or into the exhibit's own overrides.

export type Vec3Tuple = [number, number, number];
export type ToneMap = 'aces' | 'neutral';
export type EnvironmentKind = 'dusk' | 'studio';

export interface LookPreset {
  name: string;
  background: string;
  /**
   * Screen-space backdrop: a vertical gradient (top to bottom of the frame) plus an optional soft haze
   * (`at` in screen UV with y down, `radius` in screen heights). It reads the same from every orbit angle.
   * Without it the backdrop is the flat background color.
   */
  backdrop?: { top: string; bottom: string; haze?: { color: string; at: [number, number]; radius: number; strength: number } };
  fog: { color: string; density: number };
  toneMapping: ToneMap;
  exposure: number;
  environment: EnvironmentKind;
  environmentIntensity: number;
  hemisphere: { sky: string; ground: string; intensity: number };
  key: { color: string; intensity: number; direction: Vec3Tuple };
  /** Rim light intensity relative to the key, plus its color and direction (behind the apparatus). */
  rim: { relative: number; color: string; direction: Vec3Tuple };
  /** Second key (fill) intensity relative to the key. */
  fill: { relative: number; color: string; direction: Vec3Tuple };
  /** HDR linear threshold and strength. Bloom reads the emissive target only. */
  bloom: { threshold: number; strength: number };
  /** Planar reflection strength on the bench or floor (0 turns the reflector off). */
  reflection: number;
  /** View multiplier on FINISH.saturation. */
  saturation: number;
  /** Emissive glow level. 0.32 (Lab) feeds the emissive target at unit gain; 0 stops emitters blooming. */
  glow: number;
  /** Multiplier on beam brightness in the beauty pass (Studio dims beams). */
  beamGain: number;
  /** How far beam shades darken the background under beams (light looks; default 0). */
  beamShade?: number;
  /** Set groups shown in this view (stage.registerSet names). Everything else registered is hidden. */
  sets: string[];
  hudTheme: 'dark' | 'light';
  veil: string;
}

export interface FinishSettings {
  /** Vertical field of view in degrees. */
  fov: number;
  /** Global saturation, multiplied by each look's view multiplier. */
  saturation: number;
  bloomRadius: number;
  ao: { radius: number; samples: number; intensity: number; thickness: number; resolutionScale: number };
  dof: {
    enabled: boolean;
    /** World units over which blur ramps from sharp to full. */
    focusBand: number;
    /** How fast blur grows behind the focus plane (reference far aperture). */
    farBlur: number;
    /** How fast blur grows in front of the focus plane (reference near aperture). */
    nearBlur: number;
    /** Largest blur radius in CSS pixels. */
    maxCoC: number;
  };
  /** Film grain intensity; barely visible. */
  grain: number;
  shadowMapSize: number;
  /** World units from the key light's target to the light along its direction. */
  keyDistance: number;
  /** Half-size of the key light's shadow camera in world units. */
  shadowExtent: number;
}

/** 1 world unit = 10 cm at bench scale. The fog, AO radius and key distance below assume it. */
export const UNITS_PER_METER = 10;

export const FINISH: FinishSettings = {
  fov: 30,
  saturation: 1.16,
  bloomRadius: 0.55,
  ao: { radius: 0.9, samples: 12, intensity: 1, thickness: 1, resolutionScale: 0.5 },
  dof: { enabled: true, focusBand: 14, farBlur: 1.6, nearBlur: 0.9, maxCoC: 20 },
  grain: 0.05,
  shadowMapSize: 2048,
  keyDistance: 30,
  shadowExtent: 14,
};

export const LAB: LookPreset = {
  name: 'lab',
  background: '#10151f',
  // A lavender-navy haze: the room's far walls sit about a third into it from the first view and read as
  // colored depth rather than black.
  fog: { color: '#3a3d62', density: 0.009 },
  toneMapping: 'aces',
  exposure: 1.28,
  environment: 'dusk',
  environmentIntensity: 0.7,
  hemisphere: { sky: '#8fa6d8', ground: '#3a3026', intensity: 0.5 },
  key: { color: '#ffc98f', intensity: 2.1, direction: [0.62, 0.62, 0.48] },
  rim: { relative: 0.9, color: '#a8b8ff', direction: [-0.35, 0.45, -0.82] },
  fill: { relative: 0.55, color: '#c9d4ff', direction: [-0.7, 0.35, 0.6] },
  bloom: { threshold: 1.05, strength: 0.42 },
  reflection: 0.5,
  saturation: 1.3,
  glow: 0.32,
  beamGain: 1,
  sets: ['room', 'console', 'rail'],
  hudTheme: 'dark',
  veil: '#0b0f1a',
};

export const EXPLODED: LookPreset = {
  name: 'exploded',
  background: '#081026',
  // A navy void with a lighter haze low on the left (exemplar 02 reads about luma 56 to 59).
  backdrop: {
    top: '#3e5079',
    bottom: '#4b5470',
    haze: { color: '#7a90c0', at: [0.16, 0.8], radius: 0.62, strength: 0.5 },
  },
  fog: { color: '#0a1430', density: 0.006 },
  toneMapping: 'aces',
  exposure: 0.9,
  environment: 'dusk',
  environmentIntensity: 0.75,
  hemisphere: { sky: '#7f9ad8', ground: '#1c2436', intensity: 0.5 },
  key: { color: '#ffe0bc', intensity: 1.5, direction: [-0.35, 0.85, -0.45] },
  rim: { relative: 1.5, color: '#9fb4ff', direction: [0.4, 0.4, -0.82] },
  fill: { relative: 0.55, color: '#c9d4ff', direction: [0.6, 0.35, 0.7] },
  bloom: { threshold: 1.5, strength: 0.3 },
  reflection: 0.45,
  saturation: 1.1,
  glow: 0.36,
  beamGain: 1,
  sets: ['plinth', 'rail'],
  hudTheme: 'dark',
  veil: '#081226',
};

export const STUDIO: LookPreset = {
  name: 'studio',
  background: '#f1e5d8',
  fog: { color: '#f1e5d8', density: 0.0045 },
  toneMapping: 'neutral',
  exposure: 1,
  environment: 'studio',
  environmentIntensity: 1,
  hemisphere: { sky: '#fff4e6', ground: '#d9ccbb', intensity: 0.85 },
  key: { color: '#fff0dc', intensity: 2.8, direction: [-0.45, 0.8, 0.45] },
  rim: { relative: 0.25, color: '#ffffff', direction: [0.3, 0.5, -0.8] },
  fill: { relative: 0.45, color: '#fff6ea', direction: [0.75, 0.4, 0.5] },
  bloom: { threshold: 2.6, strength: 0.18 },
  reflection: 0,
  saturation: 0.93,
  glow: 0,
  beamGain: 0.55,
  beamShade: 0.55,
  sets: ['cyclorama'],
  hudTheme: 'light',
  veil: '#f1e5d8',
};

/** The three house looks keyed by view name. Spread and override per exhibit. */
export const HOUSE_LOOKS: Record<string, LookPreset> = { lab: LAB, exploded: EXPLODED, studio: STUDIO };

/** Deep copy so tuning one exhibit's looks never mutates the shared constants. */
export function cloneLooks(looks: Record<string, LookPreset>): Record<string, LookPreset> {
  return JSON.parse(JSON.stringify(looks)) as Record<string, LookPreset>;
}
