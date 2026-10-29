// House materials (references/style-bible.md §6). Every factory returns a node material.
//
// Two rules the stage depends on:
// 1. Environment intensity is per material (glass 2.2 > brass 1.25 > lacquer 1.1 > bench 0.55 >
//    rubber 0.5 > steel 0.4). Node materials only honor envMapIntensity when material.envMap is
//    set, so every factory registers itself and the stage assigns the shared environment texture.
// 2. Only emitters write the emissive target that bloom reads: beams, LED strips, screens.
//    Lit materials keep emissive at zero unless you pass one on purpose.

import * as THREE from 'three/webgpu';
import {
  abs, attribute, Discard, emissive, exp, float, Fn, If, mix, normalWorld, reflector, smoothstep, texture,
  uniform, uv, vec3, vec4,
} from 'three/tsl';

type Lit = THREE.MeshStandardNodeMaterial | THREE.MeshPhysicalNodeMaterial;
type ColorLike = THREE.ColorRepresentation;

/**
 * Layer for beams and rays. The stage's AO pre-pass leaves it out (beams are light, not geometry)
 * and raycasts ignore it; the main pass draws it. Ribbon meshes are put on it automatically.
 */
export const BEAM_LAYER = 1;

/** Uniforms every house material reads; stage.setLook() writes them. */
export const house = {
  /** Gain on what emitters write to the emissive target (look.glow / 0.32). */
  glow: uniform(1),
  /** Beam brightness in the beauty pass (Studio dims beams). */
  beamGain: uniform(1),
  /** Planar reflection strength on bench tops and floors. */
  reflection: uniform(0.5),
  /** How far a beam's shade darkens what lies under it: light looks only, 0 in dark ones. */
  beamShade: uniform(0),
};

type ColorNode = THREE.Node<'vec4'>;
type FeedNode = THREE.Node<'vec3'>;

/**
 * Unlit emitter: `beauty` is what the color target gets, `feed` what the emissive target (bloom)
 * gets. Writing the standard emissive property instead of a per-material MRT keeps the material
 * valid in render targets without MRT (reflections, render-to-texture).
 */
function emitter(mat: THREE.MeshBasicNodeMaterial, beauty: ColorNode, feed: FeedNode, keep?: THREE.Node<'float'>): THREE.MeshBasicNodeMaterial {
  mat.colorNode = Fn(() => {
    if (keep) {
      If(keep.lessThan(0.04), () => {
        Discard();
      });
    }
    emissive.assign(feed);
    return beauty;
  })();
  return mat;
}

const envMaterials = new Set<Lit>();
let currentEnv: THREE.Texture | null = null;
let currentEnvIntensity = 1;

function registerEnv<M extends Lit>(mat: M, factor: number): M {
  mat.userData.envFactor = factor;
  envMaterials.add(mat);
  if (currentEnv) {
    mat.envMap = currentEnv;
    mat.envMapIntensity = factor * currentEnvIntensity;
  }
  return mat;
}

/** Called by the stage when the look changes. The texture object stays the same across looks. */
export function applyEnvironment(env: THREE.Texture, lookIntensity: number): void {
  currentEnv = env;
  currentEnvIntensity = lookIntensity;
  for (const mat of envMaterials) {
    mat.envMap = env;
    mat.envMapIntensity = (mat.userData.envFactor as number) * lookIntensity;
  }
}

/** Register a material you built yourself so it follows the look's environment. */
export function followEnvironment<M extends Lit>(mat: M, envFactor: number): M {
  return registerEnv(mat, envFactor);
}

function physical(params: THREE.MeshPhysicalNodeMaterialParameters, env: number): THREE.MeshPhysicalNodeMaterial {
  return registerEnv(new THREE.MeshPhysicalNodeMaterial(params), env);
}

/** Painted, clear-coated parts: console body, housings. */
export function lacquer(color: ColorLike = '#1d2129'): THREE.MeshPhysicalNodeMaterial {
  return physical({ color, roughness: 0.34, metalness: 0, clearcoat: 1, clearcoatRoughness: 0.16 }, 1.1);
}

/** Machined metal: knobs, dials, gear rings, bolts, plaques. */
export function machined(color: ColorLike = '#9aa3ad'): THREE.MeshPhysicalNodeMaterial {
  return physical({ color, metalness: 0.85, roughness: 0.32, clearcoat: 0.35, clearcoatRoughness: 0.3 }, 1.25);
}

/** Machined brass. Let the environment and the warm key make it sing; don't raise the color. */
export function brass(): THREE.MeshPhysicalNodeMaterial {
  return machined('#c08a3e');
}

/** Anodized or textured metal: engraved dials and plates. Pass a map for engravings. */
export function anodized(color: ColorLike = '#2a2f38', map?: THREE.Texture): THREE.MeshPhysicalNodeMaterial {
  const mat = physical({ color, metalness: 0.7, roughness: 0.42, clearcoat: 0.2, clearcoatRoughness: 0.35 }, 1.0);
  if (map) {
    map.colorSpace = THREE.SRGBColorSpace;
    mat.map = map;
  }
  return mat;
}

/** Dark steel: stands, posts, brackets. */
export function darkSteel(color: ColorLike = '#5a5f68'): THREE.MeshPhysicalNodeMaterial {
  return physical({ color, metalness: 0.2, roughness: 0.7 }, 0.4);
}

/** Matte rubber or fabric: grips, cables, feet. */
export function rubber(color: ColorLike = '#16181c'): THREE.MeshPhysicalNodeMaterial {
  return physical({ color, metalness: 0, roughness: 0.85 }, 0.5);
}

/** Low-poly naturals: flat shaded, slightly desaturated (the grade brings them up). */
export function organic(color: ColorLike, roughness = 0.7): THREE.MeshStandardNodeMaterial {
  return registerEnv(new THREE.MeshStandardNodeMaterial({ color, roughness, metalness: 0, flatShading: true }), 0.6);
}

/** The one glass part the exhibit is about. Real transmission, real IOR, dispersion. */
export function heroGlass(opts: { ior?: number; dispersion?: number; tint?: ColorLike; thickness?: number } = {}): THREE.MeshPhysicalNodeMaterial {
  return physical({
    color: '#ffffff',
    transmission: 1,
    roughness: 0.01,
    metalness: 0,
    thickness: opts.thickness ?? 0.85,
    ior: opts.ior ?? 1.785,
    dispersion: opts.dispersion ?? 0.8,
    clearcoat: 1,
    clearcoatRoughness: 0.02,
    specularIntensity: 1,
    attenuationColor: new THREE.Color(opts.tint ?? '#eaf8ff'),
    attenuationDistance: 8,
  }, 2.2);
}

/**
 * Thin glass or acrylic: chamber panes, cover glass. Plain transparency instead of transmission so
 * the frame keeps one transmission material (webgpu-stack §9). Pass { transmission: true } to opt in.
 * It writes no depth, so depth of field blurs it with whatever lies behind it. A tinted sheet that
 * must read in focus (a filter, a slide) needs depthWrite: true.
 */
export function thinGlass(opts: { opacity?: number; transmission?: boolean; tint?: ColorLike } = {}): THREE.MeshPhysicalNodeMaterial {
  const tint = opts.tint ?? '#e6fbff';
  if (opts.transmission) {
    return physical({ color: tint, transmission: 0.95, roughness: 0.05, thickness: 0.12, ior: 1.45, metalness: 0 }, 1.3);
  }
  return physical({
    color: tint, transparent: true, opacity: opts.opacity ?? 0.16, roughness: 0.05, metalness: 0,
    ior: 1.45, specularIntensity: 1, clearcoat: 1, clearcoatRoughness: 0.05, depthWrite: false,
  }, 1.3);
}

/** Faceted decorative crystal (shelves, background). Flat shaded, transparent, bright environment. */
export function crystal(tint: ColorLike = '#e9f6ff'): THREE.MeshPhysicalNodeMaterial {
  return physical({
    color: tint, transparent: true, opacity: 0.55, roughness: 0.04, metalness: 0, ior: 1.5,
    specularIntensity: 1, clearcoat: 1, clearcoatRoughness: 0.04, flatShading: true,
  }, 1.6);
}

/** Bench top with an optional planar reflection (strength follows the look). */
export function benchTop(opts: { reflect?: boolean; color?: ColorLike; resolutionScale?: number } = {}): {
  material: THREE.MeshPhysicalNodeMaterial;
  /** Add this to the scene and place it on the reflecting surface, facing up (+Y). */
  reflectorTarget: THREE.Object3D | null;
} {
  const material = physical({ color: opts.color ?? '#1b2130', metalness: 0.5, roughness: 0.28 }, 0.55);
  if (!opts.reflect) return { material, reflectorTarget: null };
  const reflection = reflector({ resolutionScale: opts.resolutionScale ?? 0.5, bounces: false, generateMipmaps: true });
  reflection.target.rotateX(-Math.PI / 2);
  // The reflection is added as unlit light; it reaches the emissive target only where it mirrors
  // an emitter bright enough to pass the bloom threshold.
  // Only up-facing surfaces mirror; the sides of a slab must not show the reflection texture.
  material.emissiveNode = reflection.rgb.mul(house.reflection).mul(smoothstep(0.85, 0.97, normalWorld.y));
  return { material, reflectorTarget: reflection.target };
}

/** Practical LED strip. HDR, allowed to bloom. Intensity is in multiples of white. */
export function emissiveStrip(color: ColorLike = '#ffb454', intensity = 4): THREE.MeshBasicNodeMaterial {
  const c = new THREE.Color(color);
  const base = vec3(c.r, c.g, c.b).mul(intensity);
  return emitter(new THREE.MeshBasicNodeMaterial(), vec4(base, 1), base.mul(house.glow));
}

/**
 * A custom emitter: color × intensity, optionally shaped by a mask node (0 to 1) for arcs, gauges
 * and bars. Fragments where the mask is below 0.04 are discarded.
 */
export function emitterMaterial(color: ColorLike, intensity: number | THREE.Node<'float'>, mask?: THREE.Node<'float'>): THREE.MeshBasicNodeMaterial {
  const c = new THREE.Color(color);
  const level = typeof intensity === 'number' ? float(intensity) : intensity;
  const rgb = vec3(c.r, c.g, c.b).mul(level).mul(mask ?? float(1));
  const mat = new THREE.MeshBasicNodeMaterial({ fog: false });
  return emitter(mat, vec4(rgb, 1), rgb.mul(house.glow), mask);
}

/** Translucent hologram panel for comparison panels and wall diagrams (below the bloom threshold). */
export function hologram(map?: THREE.Texture): THREE.MeshStandardNodeMaterial {
  const mat = registerEnv(new THREE.MeshStandardNodeMaterial({
    color: '#1e4a5c', roughness: 0.3, metalness: 0, emissive: new THREE.Color('#2b8fb3'),
    emissiveIntensity: 0.8, transparent: true, opacity: 0.85, depthWrite: false,
  }), 0.8);
  if (map) {
    map.colorSpace = THREE.SRGBColorSpace;
    mat.emissiveMap = map;
  }
  return mat;
}

/** Diegetic screen: unlit canvas texture times about 1.5 so the traces bloom slightly. */
export function screen(map: THREE.Texture, gain = 1.5): THREE.MeshBasicNodeMaterial {
  map.colorSpace = THREE.SRGBColorSpace;
  const color = texture(map).rgb.mul(gain);
  return emitter(new THREE.MeshBasicNodeMaterial(), vec4(color, 1), color.mul(house.glow));
}

/**
 * Beam or ray: unlit, additive, HDR, soft falloff across the ribbon width, no fog.
 * Ribbon geometry from beams.ts supplies uv.y across the width and a per-vertex 'beamColor'
 * (linear HDR, intensity baked in). `color` and `intensity` scale the whole beam.
 */
export function beam(color: ColorLike = '#ffffff', intensity = 1, opts: { core?: number; halo?: number } = {}): THREE.MeshBasicNodeMaterial {
  const c = new THREE.Color(color);
  const across = abs(uv().y.mul(2).sub(1)); // 0 at the center line, 1 at the edges
  const coreSharp = float(opts.core ?? 26);
  const halo = float(opts.halo ?? 0.35);
  const profile = exp(across.mul(across).mul(coreSharp).negate()).add(
    float(1).sub(smoothstep(0, 1, across)).mul(halo),
  );
  const rgb = attribute<'vec3'>('beamColor', 'vec3').mul(vec3(c.r, c.g, c.b)).mul(intensity).mul(profile);
  // Beams draw with the opaque parts, after them (Ribbon sets renderOrder), so the transmission
  // pass sees them and hero glass refracts the real beam behind it. They write depth where they
  // are visibly lit, so depth of field focuses them at their own distance; faint edges are
  // discarded and write nothing.
  const mat = new THREE.MeshBasicNodeMaterial({
    transparent: false, depthWrite: true, blending: THREE.AdditiveBlending, side: THREE.DoubleSide, fog: false,
  });
  return emitter(mat, vec4(rgb.mul(house.beamGain), 1), rgb.mul(house.glow), profile);
}

/**
 * A beam's shade: a soft dark band under a ribbon, so a beam still reads on light paper, where
 * added light barely shows. It multiplies what is behind it by 1 − house.beamShade × falloff,
 * fading with the ribbon's own brightness, and leaves the emissive target as it was. With
 * beamShade at 0 it changes nothing. Ribbons create it with `shade: true`.
 */
export function beamShade(): THREE.MeshBasicNodeMaterial {
  const across = abs(uv().y.mul(2).sub(1));
  const lit = attribute<'vec3'>('beamColor', 'vec3').g.min(1);
  const dark = float(1).sub(smoothstep(0, 1, across)).pow(1.5).mul(lit).mul(house.beamShade);
  const mat = new THREE.MeshBasicNodeMaterial({
    transparent: false, depthWrite: false, side: THREE.DoubleSide, fog: false,
    blending: THREE.CustomBlending, blendSrc: THREE.ZeroFactor, blendDst: THREE.SrcColorFactor,
  });
  return emitter(mat, vec4(vec3(float(1).sub(dark)), 1), vec3(1));
}

/** Soft additive glow card (meter spots, lamp apertures). Radial falloff over a plane's uv. */
export function glowSpot(color: ColorLike = '#ffffff', intensity = 2): {
  material: THREE.MeshBasicNodeMaterial;
  intensity: THREE.UniformNode<'float', number>;
} {
  const c = new THREE.Color(color);
  const level = uniform(intensity);
  const d = uv().sub(0.5).length().mul(2);
  const falloff = mix(float(1), float(0), smoothstep(0, 1, d)).pow(2.2);
  const rgb = vec3(c.r, c.g, c.b).mul(level).mul(falloff);
  const material = new THREE.MeshBasicNodeMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, fog: false,
  });
  emitter(material, vec4(rgb.mul(house.beamGain), 1), rgb.mul(house.glow));
  return { material, intensity: level };
}
