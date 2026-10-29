// Stage: WebGPU renderer, lights, environments, the post pipeline and look presets.
//
// Pipeline (wiring copied from the r186 examples webgpu_postprocessing_ao / _traa / _dof /
// _bloom_emissive; see references/webgpu-stack.md §4):
//   pre-pass (opaque only: normals, velocity, depth)
//     → GTAO, fed into the main pass's ambient light through builtinAOContext
//   main pass (MRT: output + emissive)
//     → TRAA on the main color (resolves GTAO noise and edges; needs the pre-pass depth/velocity)
//     → depth of field: a depth-aware bokeh gather (see bokeh() below; r186's dof() lets sharp
//       foreground edges bleed into the blurred background), focus from the camera rig
//     + bloom of the emissive target only (so nothing but emitters can glow)
//     → saturation → tone mapping (ACES or Neutral, mixed by a uniform so views never recompile)
//     → film grain → sRGB output.  HTML vignette and labels sit above the canvas.

import * as THREE from 'three/webgpu';
import {
  acesFilmicToneMapping, builtinAOContext, clamp, cos, emissive, float, Fn, If, Loop,
  max, min, mix, mrt, neutralToneMapping, normalView, output, packNormalToRGB, pass,
  perspectiveDepthToViewZ, positionLocal, renderOutput, rtt, sample, saturation, screenSize, screenUV, select, sin,
  smoothstep, step, uniform, unpackRGBToNormal, vec2, vec3, vec4, velocity,
} from 'three/tsl';
import { ao } from 'three/addons/tsl/display/GTAONode.js';
import { bloom } from 'three/addons/tsl/display/BloomNode.js';
import { film } from 'three/addons/tsl/display/FilmNode.js';
import { traa } from 'three/addons/tsl/display/TRAANode.js';
import { smaa } from 'three/addons/tsl/display/SMAANode.js';
import { applyEnvironment, BEAM_LAYER, house } from './materials';
import { FINISH, type EnvironmentKind, type FinishSettings, type LookPreset } from './looks';
import { prefersReducedMotion } from './a11y';

export type Backend = 'webgpu' | 'webgl2';
type FrameCallback = (dt: number, elapsed: number) => void;
type Uniform = THREE.UniformNode<'float', number>;

export interface StageOptions {
  container: HTMLElement;
  looks: Record<string, LookPreset>;
  initialLook?: string;
  /** Device pixels per frame the adaptive pixel ratio aims for. */
  pixelBudget?: number;
  finish?: Partial<FinishSettings>;
  /** Element faded in and out between looks (#veil by default). */
  veil?: HTMLElement | null;
  /** Element holding the themed loading line (#loading by default). */
  loading?: HTMLElement | null;
  /** Point the key light and shadow camera aim at. */
  lightTarget?: THREE.Vector3Like;
  /** Test the WebGL2 fallback on purpose. Never the development target. */
  forceWebGL?: boolean;
  /**
   * Anti-aliasing. 'traa' (default) also resolves GTAO noise. 'smaa' runs after tone mapping and is
   * the fallback when thin beams ghost under camera motion.
   */
  aa?: 'traa' | 'smaa' | 'none';
}

export interface StageUniforms {
  exposure: Uniform;
  neutral: Uniform;
  saturation: Uniform;
  grain: Uniform;
  aoIntensity: Uniform;
  focusDistance: Uniform;
  focusBand: Uniform;
  farBlur: Uniform;
  nearBlur: Uniform;
  /** Largest blur radius in device pixels (FINISH.dof.maxCoC × pixel ratio). */
  maxBlur: Uniform;
  bloomStrength: Uniform;
  bloomRadius: Uniform;
  bloomThreshold: Uniform;
}

export interface Stage {
  renderer: THREE.WebGPURenderer;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  pipeline: THREE.RenderPipeline;
  uniforms: StageUniforms;
  lights: { hemi: THREE.HemisphereLight; key: THREE.DirectionalLight; rim: THREE.DirectionalLight; fill: THREE.DirectionalLight };
  /** GTAO node, for the tune panel (radius, samples, thickness). */
  aoNode: ReturnType<typeof ao>;
  backend: Backend;
  looks: Record<string, LookPreset>;
  finish: FinishSettings;
  /** Name of the active look. */
  readonly look: string;
  /** Resolves after compile plus two rendered frames. */
  ready: Promise<void>;
  /** Builds everything, compiles every look's materials, starts the loop. Call once the scene is built. */
  start(): Promise<void>;
  setLook(name: string, opts?: { instant?: boolean; onSwap?: () => void }): Promise<void>;
  /**
   * Fades the veil over the scene to `to` (0 clear, 1 opaque) over `ms`, in `color` or the active
   * look's veil color; a newer fade takes over from one in progress. setLook swaps under a veil that
   * is already opaque and leaves it for the caller to lift: the cinematic tour fades through black
   * between shots this way.
   */
  fade(to: number, ms: number, color?: string): Promise<void>;
  /** Re-applies the active look's values (after tuning). */
  refreshLook(): void;
  registerSet(group: string, object: THREE.Object3D): void;
  onFrame(cb: FrameCallback): () => void;
  onResize(cb: (width: number, height: number) => void): () => void;
  onLook(cb: (look: LookPreset) => void): () => void;
  /**
   * Re-render shadow maps every frame for the next `seconds` (default 1.5, long enough for an
   * eased move to settle). Call whenever a shadow caster starts to move.
   */
  invalidateShadows(seconds?: number): void;
  /** Average frames per second over the last second. */
  fps(): number;
  /** Average and minimum fps over the last `ms` milliseconds. */
  fpsStats(ms?: number): { avg: number; min: number; frames: number };
  readonly pixelRatio: number;
  readonly size: { width: number; height: number };
}

function hex(c: string): THREE.Color {
  return new THREE.Color(c);
}

function adaptivePixelRatio(width: number, height: number, budget: number): number {
  const dpr = window.devicePixelRatio || 1;
  const fit = Math.sqrt(budget / Math.max(1, width * height));
  return THREE.MathUtils.clamp(Math.min(dpr, 2, fit), 1, 2);
}

// ---------- environments (built once, prefiltered into one reused PMREM target) ----------

function gradientSphere(stops: { y: number; color: string; gain: number }[]): THREE.Mesh {
  const dir = positionLocal.normalize();
  const c0 = hex(stops[0].color);
  let col: THREE.Node<'vec3'> = vec3(c0.r, c0.g, c0.b).mul(stops[0].gain);
  for (let i = 1; i < stops.length; i++) {
    const a = stops[i - 1];
    const b = stops[i];
    const cc = hex(b.color);
    const cb = vec3(cc.r, cc.g, cc.b).mul(b.gain);
    col = mix(col, cb, smoothstep(a.y, b.y, dir.y));
  }
  const mat = new THREE.MeshBasicNodeMaterial({ side: THREE.BackSide, fog: false });
  mat.colorNode = col;
  return new THREE.Mesh(new THREE.SphereGeometry(50, 48, 24), mat);
}

function panel(w: number, h: number, color: string, gain: number, pos: [number, number, number]): THREE.Mesh {
  const c = hex(color).multiplyScalar(gain);
  const mat = new THREE.MeshBasicNodeMaterial({ side: THREE.DoubleSide, fog: false });
  mat.colorNode = vec3(c.r, c.g, c.b);
  const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), mat);
  m.position.set(...pos);
  m.lookAt(0, pos[1] * 0.4, 0);
  return m;
}

/** Dusk: deep navy zenith, a warm low horizon band, a faint lavender window, amber practicals. */
function buildDuskEnvironment(): THREE.Scene {
  const s = new THREE.Scene();
  s.add(gradientSphere([
    { y: -1, color: '#1a140f', gain: 1 },
    { y: -0.05, color: '#3a2a1e', gain: 1 },
    { y: 0.04, color: '#d9895a', gain: 0.55 },
    { y: 0.22, color: '#3b3a5c', gain: 0.6 },
    { y: 0.65, color: '#121a36', gain: 0.8 },
    { y: 1, color: '#0a0f22', gain: 0.8 },
  ]));
  s.add(panel(22, 34, '#c9b6ff', 1.6, [-38, 12, 18])); // tall dusk window, front left
  s.add(panel(1.4, 26, '#ffb454', 5, [30, 8, -28])); // amber light bar, back right
  s.add(panel(1.4, 26, '#ffb454', 5, [-30, 8, -30])); // amber light bar, back left
  s.add(panel(30, 1.2, '#ffc070', 3, [6, 18, -40])); // shelf strip
  return s;
}

/**
 * Studio: warm paper sweep, a large overhead softbox and two side boxes. Kept dim enough that the
 * key's soft shadows still read on the paper under the Studio preset's 0.85 hemisphere fill.
 */
function buildStudioEnvironment(): THREE.Scene {
  const s = new THREE.Scene();
  s.add(gradientSphere([
    { y: -1, color: '#b9aa98', gain: 0.26 },
    { y: 0, color: '#e9dccd', gain: 0.38 },
    { y: 1, color: '#fff6ec', gain: 0.48 },
  ]));
  const top = panel(40, 40, '#fff4e8', 1.35, [0, 44, 0]);
  top.lookAt(0, 0, 0);
  s.add(top);
  s.add(panel(18, 24, '#fff1e0', 0.95, [-40, 10, 14]));
  s.add(panel(18, 24, '#f4f6ff', 0.7, [40, 10, 10]));
  return s;
}

// ---------- depth of field ----------

const BOKEH_SAMPLES = 80;
const GOLDEN_ANGLE = 2.39996323;
/** Tap radius, in device pixels, beyond which the gather reads the prefiltered copy. */
const PREFILTER_FROM = 4;

/**
 * Depth-aware bokeh (single gather, after Dennis Gustafsson's 2018 "Bokeh depth of field in a
 * single pass"): every sample counts only if its own blur radius reaches the pixel, and samples
 * behind the pixel may not out-blur it by more than 2×. Sharp parts therefore never smear into
 * the blurred background, and a blurred foreground still spreads over what is behind it. The
 * gather runs at half resolution; a full-resolution composite keeps in-focus pixels crisp.
 */
function bokeh(
  colorTex: THREE.TextureNode, depthTex: THREE.TextureNode, u: StageUniforms,
  invSize: THREE.UniformNode<'vec2', THREE.Vector2>, clip: { near: Uniform; far: Uniform },
) {
  // The scene camera's clip planes come in as uniforms: inside a full-screen pass the TSL
  // cameraNear/cameraFar nodes describe the quad's camera, not the scene's.
  const viewDist = (uvNode: THREE.Node<'vec2'>) => perspectiveDepthToViewZ(depthTex.sample(uvNode), clip.near, clip.far).negate();
  const blurSize = (dist: THREE.Node<'float'>) => {
    const signed = dist.sub(u.focusDistance);
    const k = select(signed.greaterThan(0), u.farBlur, u.nearBlur);
    return clamp(signed.abs().mul(k).div(u.focusBand), 0, 1).mul(u.maxBlur);
  };

  // Outer taps sit 5 to 10 pixels apart, so point samples skip across thin bright strips and a
  // defocused strip breaks into regular beads. Taps past PREFILTER_FROM pixels read a 4×4
  // box-filtered copy instead: four bilinear taps, each on the corner of a 2×2 block.
  const prefiltered = rtt(Fn(() => {
    const at = (dx: number, dy: number) => colorTex.sample(screenUV.add(vec2(dx, dy).mul(invSize))).rgb;
    return vec4(at(-1, -1).add(at(1, -1)).add(at(-1, 1)).add(at(1, 1)).mul(0.25), 1);
  })(), null, null, { resolutionScale: 0.25, type: THREE.HalfFloatType });

  const gather = Fn(() => {
    const uvC = screenUV;
    const dC = viewDist(uvC);
    const sC = blurSize(dC);
    const acc = colorTex.sample(uvC).rgb.toVar();
    const total = float(1).toVar();
    const nearCover = float(0).toVar();
    const tap = vec3(0).toVar();
    Loop(BOKEH_SAMPLES, ({ i }) => {
      const fi = float(i);
      const r = u.maxBlur.mul(fi.add(0.5).div(BOKEH_SAMPLES).pow(0.75));
      const a = fi.mul(GOLDEN_ANGLE);
      const tc = uvC.add(vec2(cos(a), sin(a)).mul(r).mul(invSize));
      const dS = viewDist(tc);
      const sS = blurSize(dS).toVar();
      If(dS.greaterThan(dC), () => {
        sS.assign(min(sS, sC.mul(2)));
      });
      // r depends only on the loop index, so every pixel takes the same branch.
      If(r.lessThan(PREFILTER_FROM), () => {
        tap.assign(colorTex.sample(tc).level(float(0)).rgb);
      }).Else(() => {
        tap.assign(prefiltered.sample(tc).level(float(0)).rgb);
      });
      const m = smoothstep(r.sub(0.75), r.add(0.75), sS);
      acc.addAssign(mix(acc.div(total), tap, m));
      total.addAssign(1);
      nearCover.addAssign(m.mul(step(dS, dC.mul(0.97))));
    });
    return vec4(acc.div(total), nearCover.div(BOKEH_SAMPLES));
  });

  const blurred = rtt(gather(), null, null, { resolutionScale: 0.5, type: THREE.HalfFloatType });

  return Fn(() => {
    const uvN = screenUV;
    const sharp = colorTex.sample(uvN);
    const soft = blurred.sample(uvN);
    const t = max(smoothstep(1, 4, blurSize(viewDist(uvN))), smoothstep(0.03, 0.25, soft.a));
    return vec4(mix(sharp.rgb, soft.rgb, t), 1);
  })();
}

// ---------- the stage ----------

export async function createStage(opts: StageOptions): Promise<Stage> {
  const finish: FinishSettings = { ...FINISH, ...opts.finish, ao: { ...FINISH.ao, ...opts.finish?.ao }, dof: { ...FINISH.dof, ...opts.finish?.dof } };
  const looks = opts.looks;
  const container = opts.container;
  const veil = opts.veil === undefined ? document.getElementById('veil') : opts.veil;
  const loading = opts.loading === undefined ? document.getElementById('loading') : opts.loading;
  const budget = opts.pixelBudget ?? 3_000_000;

  const renderer = new THREE.WebGPURenderer({ antialias: false, forceWebGL: opts.forceWebGL ?? false });
  let width = Math.max(1, container.clientWidth);
  let height = Math.max(1, container.clientHeight);
  let pixelRatio = adaptivePixelRatio(width, height, budget);
  renderer.setPixelRatio(pixelRatio);
  renderer.setSize(width, height);
  renderer.toneMapping = THREE.NoToneMapping; // tone mapping happens inside the pipeline
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  container.appendChild(renderer.domElement);
  await renderer.init();

  const backend: Backend = (renderer.backend as { isWebGPUBackend?: boolean }).isWebGPUBackend ? 'webgpu' : 'webgl2';
  if (backend !== 'webgpu') showFallbackBadge();

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(finish.fov, width / height, 0.3, 400);
  camera.position.set(0, 8, 30);

  // Lights
  const lightTarget = new THREE.Vector3().copy(opts.lightTarget ?? { x: 0, y: 2, z: 0 });
  const hemi = new THREE.HemisphereLight('#8fa6d8', '#3a3026', 0.5);
  const key = new THREE.DirectionalLight('#ffc98f', 2.1);
  key.castShadow = true;
  key.shadow.mapSize.set(finish.shadowMapSize, finish.shadowMapSize);
  const sc = key.shadow.camera;
  sc.left = -finish.shadowExtent; sc.right = finish.shadowExtent;
  sc.top = finish.shadowExtent; sc.bottom = -finish.shadowExtent;
  sc.near = 1; sc.far = finish.keyDistance * 2.2;
  key.shadow.bias = -0.0004;
  key.shadow.normalBias = 0.02;
  key.shadow.autoUpdate = false;
  key.target.position.copy(lightTarget);
  const rim = new THREE.DirectionalLight('#a8b8ff', 1);
  rim.target.position.copy(lightTarget);
  const fill = new THREE.DirectionalLight('#c9d4ff', 1);
  fill.target.position.copy(lightTarget);
  scene.add(hemi, key, key.target, rim, rim.target, fill, fill.target);

  // Environments
  const pmrem = new THREE.PMREMGenerator(renderer);
  const envScenes: Record<EnvironmentKind, THREE.Scene> = { dusk: buildDuskEnvironment(), studio: buildStudioEnvironment() };
  let envTarget: THREE.RenderTarget | null = null;
  let envKind: EnvironmentKind | null = null;
  function setEnvironment(kind: EnvironmentKind): THREE.Texture {
    if (envKind !== kind || !envTarget) {
      envTarget = pmrem.fromScene(envScenes[kind], 0.02, 0.1, 100, { size: 256, renderTarget: envTarget }) as THREE.RenderTarget;
      envKind = kind;
    }
    return envTarget.texture;
  }

  // Uniforms
  const u: StageUniforms = {
    exposure: uniform(1.28),
    neutral: uniform(0),
    saturation: uniform(finish.saturation * 1.3),
    grain: uniform(finish.grain),
    aoIntensity: uniform(finish.ao.intensity),
    focusDistance: uniform(30),
    focusBand: uniform(finish.dof.focusBand),
    farBlur: uniform(finish.dof.farBlur),
    nearBlur: uniform(finish.dof.nearBlur),
    maxBlur: uniform(finish.dof.enabled ? finish.dof.maxCoC * pixelRatio : 0),
    bloomStrength: uniform(0.42),
    bloomRadius: uniform(finish.bloomRadius),
    bloomThreshold: uniform(1.05),
  };

  // ---- pipeline ----
  const pipeline = new THREE.RenderPipeline(renderer);
  pipeline.outputColorTransform = false;

  // three keys render lists by (scene, camera): a pass with the main camera that renders nested inside the
  // main pass (GTAO asks for the pre-pass while the main pass draws, unless TRAA asked for it first) resets
  // the list the main pass is still drawing, dropping or repeating objects. Without TRAA the pre-pass gets
  // its own camera, a copy synced every frame, so the two lists never alias. TRAA jitters the main camera
  // and needs its depth and velocity to match, so it keeps the shared camera.
  const aaMode = opts.aa ?? 'traa';
  const preCamera = aaMode === 'traa' ? camera : camera.clone();
  const prePass = pass(scene, preCamera);
  prePass.name = 'Pre-Pass';
  prePass.transparent = false;
  // Geometry only: beams (BEAM_LAYER) are light, so they cast no ambient occlusion.
  const geometryLayers = new THREE.Layers();
  geometryLayers.set(0);
  prePass.setLayers(geometryLayers);
  prePass.setMRT(mrt({ output: packNormalToRGB(normalView), velocity }));
  prePass.getTexture('output').type = THREE.UnsignedByteType;
  const preNormal = sample((uvNode) => unpackRGBToNormal(prePass.getTextureNode().sample(uvNode)));
  const preDepth = prePass.getTextureNode('depth');
  const preVelocity = prePass.getTextureNode('velocity');

  const aoNode = ao(preDepth, preNormal, camera);
  aoNode.resolutionScale = finish.ao.resolutionScale;
  aoNode.radius.value = finish.ao.radius;
  aoNode.samples.value = finish.ao.samples;
  aoNode.thickness.value = finish.ao.thickness;

  const scenePass = pass(scene, camera);
  scenePass.name = 'Scene';
  const allLayers = new THREE.Layers();
  allLayers.set(0);
  allLayers.enable(BEAM_LAYER);
  scenePass.setLayers(allLayers);
  const sceneMRT = mrt({ output, emissive: vec4(emissive, output.a) });
  // Emitters blend into the emissive target exactly as into the color target: additive beams add,
  // a filter in front of a beam attenuates its bloom, opaque parts occlude it.
  sceneMRT.setBlendMode('emissive', new THREE.BlendMode(THREE.MaterialBlending));
  scenePass.setMRT(sceneMRT);
  scenePass.contextNode = builtinAOContext(mix(float(1), aoNode.getTextureNode().sample(screenUV).r, u.aoIntensity));

  const color = scenePass.getTextureNode('output');
  const emissiveTex = scenePass.getTextureNode('emissive');

  let resolved: THREE.TextureNode = color;
  if (aaMode === 'traa') {
    const t = traa(color, preDepth, preVelocity, camera);
    t.useSubpixelCorrection = false;
    // getTextureNode() exists on TRAANode in r186 but is missing from @types/three 0.186.
    resolved = (t as unknown as { getTextureNode(): THREE.TextureNode }).getTextureNode();
  }

  // Depth of field reads the main pass depth, which includes beams (they write depth where they
  // are visible), so a beam stays sharp at its own distance instead of taking the wall's blur.
  const invSize = uniform(new THREE.Vector2(1 / (width * pixelRatio), 1 / (height * pixelRatio)));
  const clip = { near: uniform(camera.near), far: uniform(camera.far) };
  const dofNode = bokeh(resolved, scenePass.getTextureNode('depth'), u, invSize, clip);
  const bloomNode = bloom(emissiveTex, 0.42, finish.bloomRadius, 1.05);
  bloomNode.strength = u.bloomStrength;
  bloomNode.radius = u.bloomRadius;
  bloomNode.threshold = u.bloomThreshold;

  const hdr = dofNode.rgb.add(bloomNode.rgb);
  const graded = saturation(hdr, u.saturation).max(0);
  // The tone-mapping functions are typed as plain Node in @types/three 0.186; they return vec3.
  const aces = acesFilmicToneMapping(graded, u.exposure) as THREE.Node<'vec3'>;
  const neutral = neutralToneMapping(graded, u.exposure) as THREE.Node<'vec3'>;
  const toned = mix(aces, neutral, u.neutral);
  const grained = film(vec4(toned, 1), u.grain);
  const display = renderOutput(grained, THREE.NoToneMapping, THREE.SRGBColorSpace);
  pipeline.outputNode = aaMode === 'smaa' ? smaa(display) : display;

  // ---- state ----
  const sets = new Map<string, THREE.Object3D[]>();
  // A set's own lights stay in the scene's light set in every view and only go dark when the set is
  // hidden: hiding a light changes the light set, which rebuilds every lit material on each switch.
  const setLights = new THREE.Group();
  setLights.name = 'set-lights';
  scene.add(setLights);
  const setLightBase = new Map<THREE.Light, { group: string; intensity: number }>();
  const frameCbs = new Set<FrameCallback>();
  const resizeCbs = new Set<(w: number, h: number) => void>();
  const lookCbs = new Set<(l: LookPreset) => void>();
  let lookName = opts.initialLook ?? Object.keys(looks)[0];
  let transition: Promise<void> | null = null;

  // One fog and one backdrop, mutated in place: a new fog object would change every material's fog
  // node and force them all to recompile on a view switch. The backdrop is drawn in screen space (a
  // vertical gradient plus a soft haze), so it reads the same from every orbit angle.
  const fog = new THREE.FogExp2('#1a2130', 0.0065);
  scene.fog = fog;
  const backdrop = {
    top: uniform(new THREE.Color('#10151f')),
    bottom: uniform(new THREE.Color('#10151f')),
    haze: uniform(new THREE.Color('#000000')),
    hazeAt: uniform(new THREE.Vector2(0.16, 0.8)),
    hazeRadius: uniform(0.6),
  };
  const toHaze = screenUV.sub(backdrop.hazeAt).mul(vec2(screenSize.x.div(screenSize.y), 1)).length().div(backdrop.hazeRadius);
  scene.backgroundNode = mix(backdrop.top, backdrop.bottom, screenUV.y).add(backdrop.haze.mul(float(1).sub(smoothstep(0, 1, toHaze))));

  function applyLook(look: LookPreset): void {
    const bd = look.backdrop;
    backdrop.top.value.set(bd?.top ?? look.background);
    backdrop.bottom.value.set(bd?.bottom ?? look.background);
    backdrop.haze.value.set(bd?.haze?.color ?? '#000000').multiplyScalar(bd?.haze?.strength ?? 0);
    if (bd?.haze) {
      backdrop.hazeAt.value.set(bd.haze.at[0], bd.haze.at[1]);
      backdrop.hazeRadius.value = bd.haze.radius;
    }
    fog.color.set(look.fog.color);
    fog.density = look.fog.density;
    u.neutral.value = look.toneMapping === 'neutral' ? 1 : 0;
    u.exposure.value = look.exposure;
    const env = setEnvironment(look.environment);
    scene.environment = env;
    scene.environmentIntensity = look.environmentIntensity;
    applyEnvironment(env, look.environmentIntensity);
    hemi.color.set(look.hemisphere.sky);
    hemi.groundColor.set(look.hemisphere.ground);
    hemi.intensity = look.hemisphere.intensity;
    const place = (light: THREE.DirectionalLight, dir: [number, number, number], dist: number) => {
      light.position.copy(lightTarget).add(new THREE.Vector3(...dir).normalize().multiplyScalar(dist));
    };
    key.color.set(look.key.color);
    key.intensity = look.key.intensity;
    place(key, look.key.direction, finish.keyDistance);
    rim.color.set(look.rim.color);
    rim.intensity = look.key.intensity * look.rim.relative;
    place(rim, look.rim.direction, finish.keyDistance);
    fill.color.set(look.fill.color);
    fill.intensity = look.key.intensity * look.fill.relative;
    place(fill, look.fill.direction, finish.keyDistance);
    u.bloomThreshold.value = look.bloom.threshold;
    u.bloomStrength.value = look.bloom.strength;
    u.saturation.value = finish.saturation * look.saturation;
    house.reflection.value = look.reflection;
    house.glow.value = look.glow / 0.32;
    house.beamGain.value = look.beamGain;
    house.beamShade.value = look.beamShade ?? 0;
    for (const [group, objects] of sets) {
      const on = look.sets.includes(group);
      for (const o of objects) o.visible = on;
    }
    for (const [light, base] of setLightBase) light.intensity = look.sets.includes(base.group) ? base.intensity : 0;
    key.shadow.needsUpdate = true;
    for (const cb of lookCbs) cb(look);
  }

  let veilFade = 0;
  function fadeVeil(to: number, ms: number): Promise<void> {
    if (!veil) return Promise.resolve();
    const id = ++veilFade;
    return new Promise((resolve) => {
      const from = parseFloat(veil.style.opacity || '0');
      const t0 = performance.now();
      const step = () => {
        // A newer fade owns the veil now: this one ends where it is.
        if (id !== veilFade) return resolve();
        const k = Math.min(1, (performance.now() - t0) / ms);
        const e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
        veil.style.opacity = String(from + (to - from) * e);
        if (k < 1) requestAnimationFrame(step);
        else resolve();
      };
      requestAnimationFrame(step);
    });
  }

  async function setLook(name: string, o: { instant?: boolean; onSwap?: () => void } = {}): Promise<void> {
    const look = looks[name];
    if (!look) throw new Error(`Unknown look "${name}"`);
    if (transition) await transition.catch(() => {});
    lookName = name;
    // Under a veil someone else made opaque (the tour's dissolve), swap without fading: they lift it.
    if (o.instant || prefersReducedMotion() || !veil || parseFloat(veil.style.opacity || '0') > 0.99) {
      applyLook(look);
      o.onSwap?.();
      return;
    }
    transition = (async () => {
      try {
        veil.style.background = look.veil;
        await fadeVeil(1, 200);
        applyLook(look);
        o.onSwap?.();
        await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      } finally {
        await fadeVeil(0, 220);
      }
    })();
    try {
      await transition;
    } finally {
      transition = null;
    }
  }

  // ---- loop ----
  const timer = new THREE.Timer();
  timer.connect(document);
  const frameTimes: number[] = [];
  let shadowsUntil = 0;
  function tick(timestamp: number): void {
    timer.update(timestamp);
    const dt = Math.min(timer.getDelta(), 0.1);
    const elapsed = timer.getElapsed();
    frameTimes.push(timestamp);
    while (frameTimes.length > 0 && timestamp - frameTimes[0] > 10_000) frameTimes.shift();
    for (const cb of frameCbs) cb(dt, elapsed);
    if (elapsed < shadowsUntil) key.shadow.needsUpdate = true;
    clip.near.value = camera.near;
    clip.far.value = camera.far;
    if (preCamera !== camera) preCamera.copy(camera, false);
    pipeline.render();
  }

  function fpsStats(ms = 1000): { avg: number; min: number; frames: number } {
    const now = frameTimes[frameTimes.length - 1] ?? performance.now();
    const recent = frameTimes.filter((t) => now - t <= ms);
    if (recent.length < 2) return { avg: 0, min: 0, frames: recent.length };
    let maxGap = 0;
    for (let i = 1; i < recent.length; i++) maxGap = Math.max(maxGap, recent[i] - recent[i - 1]);
    const span = recent[recent.length - 1] - recent[0];
    return { avg: ((recent.length - 1) * 1000) / span, min: 1000 / maxGap, frames: recent.length };
  }

  // ---- resize ----
  function resize(): void {
    width = Math.max(1, container.clientWidth);
    height = Math.max(1, container.clientHeight);
    pixelRatio = adaptivePixelRatio(width, height, budget);
    renderer.setPixelRatio(pixelRatio);
    renderer.setSize(width, height);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    u.maxBlur.value = finish.dof.enabled ? finish.dof.maxCoC * pixelRatio : 0;
    invSize.value.set(1 / (width * pixelRatio), 1 / (height * pixelRatio));
    for (const cb of resizeCbs) cb(width, height);
  }
  new ResizeObserver(resize).observe(container);

  // ---- start: compile every look's set pieces behind the loading line ----
  let resolveReady!: () => void;
  const ready = new Promise<void>((r) => { resolveReady = r; });

  async function start(): Promise<void> {
    resize();
    const culled: THREE.Object3D[] = [];
    scene.traverse((o) => { if (o.frustumCulled) { o.frustumCulled = false; culled.push(o); } });
    for (const objects of sets.values()) for (const o of objects) o.visible = true;
    await renderer.compileAsync(scene, camera);
    for (const name of Object.keys(looks)) {
      applyLook(looks[name]);
      for (const objects of sets.values()) for (const o of objects) o.visible = true;
      if (preCamera !== camera) preCamera.copy(camera, false);
      pipeline.render();
    }
    for (const o of culled) o.frustumCulled = true;
    applyLook(looks[lookName]);
    await renderer.setAnimationLoop(tick);
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(r))));
    if (loading) {
      loading.style.transition = 'opacity .5s ease';
      loading.style.opacity = '0';
      setTimeout(() => loading.remove(), 600);
    }
    resolveReady();
  }

  const stage: Stage = {
    renderer, scene, camera, pipeline, uniforms: u, lights: { hemi, key, rim, fill }, aoNode, backend, looks, finish,
    get look() { return lookName; },
    get pixelRatio() { return pixelRatio; },
    get size() { return { width, height }; },
    ready,
    start,
    setLook,
    fade(to, ms, color) {
      if (veil && parseFloat(veil.style.opacity || '0') < 0.01) veil.style.background = color ?? looks[lookName].veil;
      return fadeVeil(to, ms);
    },
    refreshLook: () => applyLook(looks[lookName]),
    registerSet(group, object) {
      const list = sets.get(group) ?? [];
      list.push(object);
      sets.set(group, list);
      // Lights inside the set (a desk lamp in the Lab room) move to an always-visible group, keeping
      // their place in the world; register a set after positioning it.
      object.updateMatrixWorld(true);
      const lights: THREE.Light[] = [];
      object.traverse((o) => { if ((o as THREE.Light).isLight) lights.push(o as THREE.Light); });
      for (const l of lights) {
        setLights.attach(l);
        setLightBase.set(l, { group, intensity: l.intensity });
      }
      object.visible = looks[lookName]?.sets.includes(group) ?? true;
      for (const l of lights) l.intensity = object.visible ? setLightBase.get(l)!.intensity : 0;
    },
    onFrame(cb) { frameCbs.add(cb); return () => frameCbs.delete(cb); },
    onResize(cb) { resizeCbs.add(cb); return () => resizeCbs.delete(cb); },
    onLook(cb) { lookCbs.add(cb); return () => lookCbs.delete(cb); },
    invalidateShadows(seconds = 1.5) {
      key.shadow.needsUpdate = true;
      shadowsUntil = Math.max(shadowsUntil, timer.getElapsed() + seconds);
    },
    fps: () => fpsStats(1000).avg,
    fpsStats,
  };
  return stage;
}

function showFallbackBadge(): void {
  const b = document.createElement('div');
  b.className = 'dev-badge';
  b.textContent = 'WebGL2 fallback · not the target';
  document.body.appendChild(b);
}
