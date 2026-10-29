// Loupe (style-bible §12): a circular magnified inset placed in the scene near the part it
// explains, with a ring bezel, a title such as "PART ×40" and a leader to its anchor. Content is
// either a canvas drawing (schematic; the help sheet must call it a drawing) or the view of a
// second camera rendered to a texture.

import * as THREE from 'three/webgpu';
import { cameraFar, cameraNear, float, positionView, smoothstep, texture as tslTexture, uniform, uv, vec4, viewZToPerspectiveDepth } from 'three/tsl';
import { darkSteel } from './materials';

export interface LoupeOptions {
  /** Radius in world units. */
  radius: number;
  title: string;
  /** Canvas or render-target size in pixels. */
  pixels?: number;
  /** Drawing mode: paint the magnified mechanism. `t` is seconds. */
  draw?: (ctx: CanvasRenderingContext2D, size: number, t: number) => void;
  /** Render-target mode: what a second camera sees. */
  rtt?: { renderer: THREE.WebGPURenderer; scene: THREE.Scene; camera: THREE.Camera };
  /** Part the leader points at. */
  anchor?: THREE.Object3D;
  /** Redraw rate for animated drawings. */
  fps?: number;
  /**
   * The stage's focus distance uniform. When given, the loupe's depth is shifted so its center
   * sits on the focus plane and depth of field keeps it sharp (it is an inset, not a thing at a
   * distance). Its parts keep their depth relative to each other.
   */
  focus?: THREE.UniformNode<'float', number>;
}

export class Loupe {
  readonly group = new THREE.Group();
  readonly disc: THREE.Mesh;
  private canvas: HTMLCanvasElement | null = null;
  private ctx: CanvasRenderingContext2D | null = null;
  private tex: THREE.Texture;
  private rt: THREE.RenderTarget | null = null;
  private titleTex: THREE.CanvasTexture;
  /** A hairline: a cylinder would swell into a wedge when the camera comes close. */
  private leader: THREE.Line | null = null;
  private ends = new Float32Array(6);
  private tmpA = new THREE.Vector3();
  private tmpB = new THREE.Vector3();
  private tmpC = new THREE.Vector3();
  /** 0 to 1: how far the loupe has grown in. It shrinks away while its disc does not fit the frame. */
  private shown = 1;
  private opts: LoupeOptions;
  private acc = 0;
  private t = 0;
  private depthShift = uniform(0);
  private center = new THREE.Vector3();
  /** The magnified face on screen: its center (x, y) and radius (z) in normalized device units. */
  private screen = new THREE.Vector3();

  constructor(o: LoupeOptions) {
    this.opts = o;
    const px = o.pixels ?? 512;
    if (o.rtt) {
      this.rt = new THREE.RenderTarget(px, px, { type: THREE.HalfFloatType });
      this.tex = this.rt.texture;
    } else {
      this.canvas = document.createElement('canvas');
      this.canvas.width = this.canvas.height = px;
      this.ctx = this.canvas.getContext('2d') as CanvasRenderingContext2D;
      const ct = new THREE.CanvasTexture(this.canvas);
      ct.colorSpace = THREE.SRGBColorSpace;
      this.tex = ct;
    }
    // Title band drawn on its own transparent canvas so both modes share it.
    const tc = document.createElement('canvas');
    tc.width = tc.height = px;
    this.titleTex = new THREE.CanvasTexture(tc);
    this.titleTex.colorSpace = THREE.SRGBColorSpace;
    const drawTitle = () => {
      const g = tc.getContext('2d') as CanvasRenderingContext2D;
      g.clearRect(0, 0, px, px);
      const grad = g.createLinearGradient(0, 0, 0, px * 0.36);
      grad.addColorStop(0, 'rgba(6,10,20,0.82)');
      grad.addColorStop(1, 'rgba(6,10,20,0)');
      g.fillStyle = grad;
      g.fillRect(0, 0, px, px * 0.36);
      g.fillStyle = '#e8f6ff';
      // Fit the title inside the circle's chord at its height.
      const y = px * 0.2;
      const chord = 2 * Math.sqrt(Math.max(0, (px / 2) ** 2 - (px / 2 - y) ** 2)) * 0.84;
      let fontPx = Math.round(px * 0.06);
      g.font = `500 ${fontPx}px "DM Mono", ui-monospace, monospace`;
      const w = g.measureText(o.title.toUpperCase()).width;
      if (w > chord) {
        fontPx = Math.floor((fontPx * chord) / w);
        g.font = `500 ${fontPx}px "DM Mono", ui-monospace, monospace`;
      }
      g.textAlign = 'center';
      g.textBaseline = 'middle';
      g.fillText(o.title.toUpperCase(), px / 2, y);
      this.titleTex.needsUpdate = true;
    };
    drawTitle();
    if (typeof document !== 'undefined' && document.fonts) void document.fonts.ready.then(drawTitle);

    const r = o.radius;
    const contentMat = new THREE.MeshBasicNodeMaterial({ fog: false });
    const content = tslTexture(this.tex, uv());
    const title = tslTexture(this.titleTex, uv());
    // Darken toward the rim like a lens, then lay the title band over it.
    const d = uv().sub(0.5).length().mul(2);
    const rim = float(1).sub(smoothstep(0.78, 1.0, d).mul(0.55));
    const base = content.rgb.mul(rim);
    contentMat.colorNode = vec4(base.mul(float(1).sub(title.a)).add(title.rgb.mul(title.a)), 1);
    this.disc = new THREE.Mesh(new THREE.CircleGeometry(r, 96), contentMat);
    this.disc.userData.occluder = false;
    // A cool, unlit steel ring: a brass bezel catches the warm key and blooms into a second glow.
    const bezel = new THREE.Mesh(new THREE.TorusGeometry(r * 1.02, r * 0.055, 16, 128), darkSteel('#69717d'));
    const backing = new THREE.Mesh(new THREE.CircleGeometry(r * 1.06, 96), new THREE.MeshBasicNodeMaterial({ color: '#05070d' }));
    backing.position.z = -0.01;
    this.group.add(backing, this.disc, bezel);
    if (o.focus) {
      const depth = viewZToPerspectiveDepth(positionView.z.add(this.depthShift), cameraNear, cameraFar);
      for (const m of [contentMat, bezel.material, backing.material] as THREE.NodeMaterial[]) m.depthNode = depth;
    }
    if (o.anchor) {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.BufferAttribute(this.ends, 3));
      const lm = new THREE.LineBasicNodeMaterial({ color: '#dfe7f2', transparent: true, opacity: 0.9, depthWrite: false, fog: false });
      this.leader = new THREE.Line(geo, lm);
      this.leader.frustumCulled = false;
      this.leader.userData.occluder = false;
    }
  }

  /** Add the leader to the scene root (it spans between two world points). */
  addTo(scene: THREE.Object3D): void {
    scene.add(this.group);
    if (this.leader) scene.add(this.leader);
  }

  setVisible(on: boolean): void {
    this.group.visible = on;
    if (this.leader) this.leader.visible = on && this.shown > 0.6;
  }

  /**
   * The magnified face's circle on screen in CSS pixels (the bezel is outside it), or null while the loupe
   * is hidden. World labels fade rather than cover it (WorldLabels.keepClearOf).
   */
  screenCircle(width: number, height: number): { x: number; y: number; r: number } | null {
    if (!this.group.visible || this.screen.z <= 0) return null;
    return { x: ((this.screen.x + 1) / 2) * width, y: ((1 - this.screen.y) / 2) * height, r: (this.screen.z / 2) * width };
  }

  update(dt: number, camera: THREE.Camera): void {
    if (!this.group.visible) return;
    this.t += dt;
    this.acc += dt;
    this.group.quaternion.copy(camera.quaternion);
    // A loupe cut by the frame's edge reads as clutter, and close poses push it off screen: it
    // shrinks away until the whole disc fits again.
    camera.updateMatrixWorld();
    const c = this.group.getWorldPosition(this.tmpB);
    const p = this.tmpA.copy(c).project(camera);
    const e = this.tmpC.setFromMatrixColumn(camera.matrixWorld, 0).multiplyScalar(this.opts.radius * 1.1).add(c).project(camera);
    const rx = Math.abs(e.x - p.x);
    const ry = rx * ((camera as THREE.PerspectiveCamera).aspect ?? 1);
    const fits = p.z < 1 && Math.abs(p.x) + rx < 1 && Math.abs(p.y) + ry < 1;
    this.shown += ((fits ? 1 : 0) - this.shown) * Math.min(1, dt * 7);
    const k = this.shown * this.shown * (3 - 2 * this.shown);
    this.group.scale.setScalar(Math.max(0.001, k));
    this.screen.set(p.x, p.y, p.z < 1 ? (rx / 1.1) * k : 0);
    if (this.leader) this.leader.visible = k > 0.6;
    if (this.opts.focus) {
      // View z of the loupe's center (negative in front of the camera) mapped onto the focus plane.
      const viewZ = this.group.getWorldPosition(this.center).applyMatrix4(camera.matrixWorldInverse).z;
      this.depthShift.value = -this.opts.focus.value - viewZ;
    }
    if (this.ctx && this.opts.draw && this.acc >= 1 / (this.opts.fps ?? 30)) {
      this.acc = 0;
      const size = this.canvas!.width;
      this.ctx.save();
      this.opts.draw(this.ctx, size, this.t);
      this.ctx.restore();
      this.tex.needsUpdate = true;
    }
    if (this.rt && this.opts.rtt) {
      const { renderer, scene, camera: cam } = this.opts.rtt;
      const prev = renderer.getRenderTarget();
      renderer.setRenderTarget(this.rt);
      renderer.render(scene, cam);
      renderer.setRenderTarget(prev);
    }
    if (this.leader && this.opts.anchor) {
      // From the bezel's edge toward the anchor, to the anchor itself.
      const a = this.opts.anchor.getWorldPosition(this.tmpA);
      const c = this.group.getWorldPosition(this.tmpB);
      const r = this.opts.radius * 1.08;
      const dx = a.x - c.x, dy = a.y - c.y, dz = a.z - c.z;
      const k = r / Math.max(1e-6, Math.hypot(dx, dy, dz));
      this.ends[0] = c.x + dx * k; this.ends[1] = c.y + dy * k; this.ends[2] = c.z + dz * k;
      this.ends[3] = a.x; this.ends[4] = a.y; this.ends[5] = a.z;
      (this.leader.geometry.getAttribute('position') as THREE.BufferAttribute).needsUpdate = true;
    }
  }
}
