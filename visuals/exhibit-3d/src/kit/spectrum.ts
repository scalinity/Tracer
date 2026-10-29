// Wavelength to color. CIE 1931 2° color matching functions from the multi-lobe analytic fit of
// Wyman, Sloan and Shirley (JCGT 2013), then XYZ to linear sRGB (D65). Out-of-gamut spectral
// colors are pulled into gamut by adding white, then normalized so the brightest channel is 1.
// The ends of the spectrum (violet, deep red) come out as bright as the middle; declare that in
// the help sheet when an exhibit shows a spectrum.

export type RGB = [number, number, number];

function lobe(x: number, mu: number, s1: number, s2: number): number {
  const t = (x - mu) / (x < mu ? s1 : s2);
  return Math.exp(-0.5 * t * t);
}

/** CIE 1931 2° observer, analytic fit. Returns [X, Y, Z] for a wavelength in nanometers. */
export function cie1931(nm: number): RGB {
  const x = 1.056 * lobe(nm, 599.8, 37.9, 31.0) + 0.362 * lobe(nm, 442.0, 16.0, 26.7) - 0.065 * lobe(nm, 501.1, 20.4, 26.2);
  const y = 0.821 * lobe(nm, 568.8, 46.9, 40.5) + 0.286 * lobe(nm, 530.9, 16.3, 31.1);
  const z = 1.217 * lobe(nm, 437.0, 11.8, 36.0) + 0.681 * lobe(nm, 459.0, 26.0, 13.8);
  return [x, y, z];
}

export function xyzToLinearSRGB([x, y, z]: RGB): RGB {
  return [
    3.2404542 * x - 1.5371385 * y - 0.4985314 * z,
    -0.969266 * x + 1.8760108 * y + 0.041556 * z,
    0.0556434 * x - 0.2040259 * y + 1.0572252 * z,
  ];
}

/** Saturated linear-sRGB color for a wavelength, brightest channel = 1. */
export function wavelengthToLinearSRGB(nm: number): RGB {
  const rgb = xyzToLinearSRGB(cie1931(nm));
  const lift = Math.min(0, rgb[0], rgb[1], rgb[2]);
  const r = rgb[0] - lift;
  const g = rgb[1] - lift;
  const b = rgb[2] - lift;
  const m = Math.max(r, g, b, 1e-6);
  return [r / m, g / m, b / m];
}

/** Relative luminous efficiency (the ȳ curve, peak about 1 at 555 nm). */
export function luminousEfficiency(nm: number): number {
  return cie1931(nm)[1];
}

/** Evenly spaced wavelengths with colors. The reference samples 24 from 400 to 700 nm. */
export function spectrumSamples(count = 24, from = 400, to = 700): { nm: number; color: RGB }[] {
  const out: { nm: number; color: RGB }[] = [];
  for (let i = 0; i < count; i++) {
    const nm = count === 1 ? from : from + ((to - from) * i) / (count - 1);
    out.push({ nm, color: wavelengthToLinearSRGB(nm) });
  }
  return out;
}
