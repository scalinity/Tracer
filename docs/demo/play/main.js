// Tracer, self-playing: three specimens, one after another, with no clicks needed.
// ?scene=2&t=60&paused opens a given moment (scene counts from 1, t in specimen minutes).
import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import * as S from "./story.js";

const MIN_PER_SEC = 7;            // specimen minutes per second of playback
const HOLD = { info: 0.8, warn: 1.4, alert: 1.9, bad: 1.6, good: 1.2 };  // pause on each caption, seconds
const INTRO_SEC = 2.4, OUTRO_SEC = 5.0;

const data = await (await fetch("scenes.json")).json();
const TICK = data.tick_min;
const params = new URLSearchParams(location.search);

// ---------- renderer, scene, camera ----------
const canvas = document.getElementById("scene");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.0;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;

const scene = new THREE.Scene();
scene.background = new THREE.Color("#080b10");
scene.fog = new THREE.Fog("#080b10", 26, 52);
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
scene.environmentIntensity = 0.28;

const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 200);
const LOOK = new THREE.Vector3(0.6, 1.5, 0.6);

scene.add(new THREE.HemisphereLight("#9fc4ff", "#0a0d12", 0.55));
const key = new THREE.DirectionalLight("#ffffff", 1.6);
key.position.set(-6, 14, 9);
key.castShadow = true;
key.shadow.mapSize.set(2048, 2048);
Object.assign(key.shadow.camera, { left: -16, right: 16, top: 10, bottom: -10, near: 1, far: 40 });
key.shadow.radius = 6;
scene.add(key);
const rim = new THREE.DirectionalLight("#5cc8ff", 0.6);
rim.position.set(8, 6, -10);
scene.add(rim);

// ---------- materials ----------
const M = {
  floor: new THREE.MeshStandardMaterial({ color: "#0d1219", roughness: 0.92, metalness: 0.0 }),
  plinth: new THREE.MeshStandardMaterial({ color: "#1a222d", roughness: 0.55, metalness: 0.15 }),
  plinthTop: new THREE.MeshStandardMaterial({ color: "#232d3a", roughness: 0.4, metalness: 0.2 }),
  steel: new THREE.MeshStandardMaterial({ color: "#8794a3", roughness: 0.32, metalness: 0.85 }),
  dark: new THREE.MeshStandardMaterial({ color: "#11161d", roughness: 0.6, metalness: 0.3 }),
  glass: new THREE.MeshPhysicalMaterial({ color: "#bfe6ff", roughness: 0.05, metalness: 0, transparent: true, opacity: 0.16,
    clearcoat: 1, clearcoatRoughness: 0.05, depthWrite: false, side: THREE.DoubleSide }),
  binGlass: new THREE.MeshPhysicalMaterial({ color: "#ff8a8a", roughness: 0.1, transparent: true, opacity: 0.14, depthWrite: false,
    side: THREE.DoubleSide, clearcoat: 1 }),
};

// ---------- floor ----------
const floor = new THREE.Mesh(new THREE.CircleGeometry(40, 96), M.floor);
floor.rotation.x = -Math.PI / 2;
floor.receiveShadow = true;
scene.add(floor);
const grid = new THREE.GridHelper(60, 60, "#1b2430", "#121922");
grid.material.transparent = true;
grid.material.opacity = 0.55;
grid.position.y = 0.002;
scene.add(grid);

// ---------- the places ----------
const POS = {
  unit: new THREE.Vector3(-8.6, 0, 0.3),
  station: new THREE.Vector3(5.4, 0, 0.3),
  lab: new THREE.Vector3(9.6, 0, 0.3),
  lost: new THREE.Vector3(-1.2, 0, 3.6),
};

function plinth(w, d, h = 0.32) {
  const g = new THREE.Group();
  const base = new THREE.Mesh(new RoundedBoxGeometry(w, h, d, 4, 0.1), M.plinth);
  base.position.y = h / 2;
  base.castShadow = base.receiveShadow = true;
  g.add(base);
  return g;
}

// Ward: a plinth, a counter and the tube's send port.
const ward = plinth(3.4, 3.0);
ward.position.copy(POS.unit);
{
  const counter = new THREE.Mesh(new RoundedBoxGeometry(1.9, 0.9, 0.8, 4, 0.08), M.plinthTop);
  counter.position.set(-0.55, 0.77, -0.5);
  counter.castShadow = counter.receiveShadow = true;
  ward.add(counter);
  const port = new THREE.Mesh(new THREE.CylinderGeometry(0.32, 0.36, 1.25, 32), M.steel);
  port.position.set(0.6, 0.95, -0.3);
  port.castShadow = true;
  ward.add(port);
}
scene.add(ward);

// Lab tube station: a steel receiver with a tray.
const station = plinth(2.4, 2.4);
station.position.copy(POS.station);
{
  const body = new THREE.Mesh(new THREE.CylinderGeometry(0.5, 0.56, 1.5, 40), M.steel);
  body.position.set(0.15, 1.07, -0.3);
  body.castShadow = true;
  station.add(body);
  const tray = new THREE.Mesh(new RoundedBoxGeometry(1.1, 0.12, 0.7, 3, 0.05), M.dark);
  tray.position.set(0.15, 0.4, 0.45);
  tray.receiveShadow = true;
  station.add(tray);
}
scene.add(station);

// Lab: a bench and a scanner arch that lights up green on the receipt scan.
const lab = plinth(3.0, 2.6);
lab.position.copy(POS.lab);
const scanMat = new THREE.MeshStandardMaterial({ color: "#0f2a1f", emissive: "#3ddc97", emissiveIntensity: 0.0, roughness: 0.4 });
{
  const bench = new THREE.Mesh(new RoundedBoxGeometry(2.4, 0.7, 1.4, 4, 0.08), M.plinthTop);
  bench.position.set(0, 0.67, 0);
  bench.castShadow = bench.receiveShadow = true;
  lab.add(bench);
  const arch = new THREE.Mesh(new THREE.TorusGeometry(0.55, 0.06, 16, 48, Math.PI), scanMat);
  arch.position.set(0.3, 1.02, 0);
  arch.rotation.y = Math.PI / 2;
  lab.add(arch);
}
scene.add(lab);

// The tube: ward port, over the floor, down into the station.
const tubeCurve = new THREE.CatmullRomCurve3([
  new THREE.Vector3(-8.0, 1.55, 0.0),
  new THREE.Vector3(-7.4, 3.0, 0.0),
  new THREE.Vector3(-4.5, 3.75, 0.0),
  new THREE.Vector3(0.0, 3.85, 0.0),
  new THREE.Vector3(3.6, 3.6, 0.0),
  new THREE.Vector3(5.5, 2.75, 0.0),
  new THREE.Vector3(5.55, 1.85, 0.0),
], false, "catmullrom", 0.3);
const tube = new THREE.Mesh(new THREE.TubeGeometry(tubeCurve, 200, 0.24, 24, false), M.glass);
scene.add(tube);
const tubeGlowMat = new THREE.MeshBasicMaterial({ color: "#5cc8ff", transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false });
const tubeGlow = new THREE.Mesh(new THREE.TubeGeometry(tubeCurve, 200, 0.12, 16, false), tubeGlowMat);
scene.add(tubeGlow);
// Thin steel bands along the tube, so it reads as a tube.
for (let i = 1; i < 12; i++) {
  const u = i / 12, p = tubeCurve.getPointAt(u), tan = tubeCurve.getTangentAt(u);
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.255, 0.025, 8, 32), M.steel);
  ring.position.copy(p);
  ring.lookAt(p.clone().add(tan));
  scene.add(ring);
}

// The lost branch: off the tube near its top, down into a bin in front.
const U_BRANCH = 0.44;
const branchStart = tubeCurve.getPointAt(U_BRANCH);
const branchCurve = new THREE.CatmullRomCurve3([
  branchStart.clone(),
  branchStart.clone().add(new THREE.Vector3(0.2, -0.4, 0.9)),
  new THREE.Vector3(-1.1, 1.9, 3.2),
  new THREE.Vector3(-1.2, 0.9, 3.6),
]);
scene.add(new THREE.Mesh(new THREE.TubeGeometry(branchCurve, 80, 0.2, 20, false), M.binGlass));
const bin = new THREE.Group();
bin.position.copy(POS.lost);
{
  const shell = new THREE.Mesh(new THREE.CylinderGeometry(0.8, 0.68, 0.9, 40, 1, true), new THREE.MeshStandardMaterial({ color: "#3a1c20", roughness: 0.7, metalness: 0.2, side: THREE.DoubleSide }));
  shell.position.y = 0.45;
  shell.castShadow = true;
  bin.add(shell);
  const bottom = new THREE.Mesh(new THREE.CircleGeometry(0.68, 40), M.dark);
  bottom.rotation.x = -Math.PI / 2;
  bottom.position.y = 0.03;
  bin.add(bottom);
}
scene.add(bin);

// ---------- Tracer's guess: halos under each place ----------
function haloTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 256;
  const g = c.getContext("2d");
  const grad = g.createRadialGradient(128, 128, 0, 128, 128, 128);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.35, "rgba(255,255,255,0.45)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, 256, 256);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}
const HALO_TEX = haloTexture();
const halos = {};
for (const [stop, p] of Object.entries({ unit: POS.unit, station: POS.station, lab: POS.lab, lost: POS.lost })) {
  const m = new THREE.Mesh(new THREE.PlaneGeometry(4.6, 4.6), new THREE.MeshBasicMaterial({ map: HALO_TEX, color: "#5cc8ff", transparent: true,
    opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }));
  m.rotation.x = -Math.PI / 2;
  m.position.set(p.x, 0.36, p.z);
  scene.add(m);
  halos[stop] = m;
}

// ---------- the specimen ----------
const specMat = new THREE.MeshStandardMaterial({ color: "#e9f6ff", emissive: "#bfe6ff", emissiveIntensity: 1.6, roughness: 0.25 });
const spec = new THREE.Mesh(new THREE.CapsuleGeometry(0.12, 0.3, 8, 20), specMat);
spec.castShadow = true;
const specLight = new THREE.PointLight("#bfe6ff", 6, 4.5, 2);
spec.add(specLight);
scene.add(spec);

// Alert lamp over the lab.
const lampMat = new THREE.MeshStandardMaterial({ color: "#1a1f27", emissive: "#5cc8ff", emissiveIntensity: 0, roughness: 0.3 });
const lamp = new THREE.Mesh(new THREE.SphereGeometry(0.22, 32, 16), lampMat);
lamp.position.set(POS.lab.x - 0.9, 2.15, POS.lab.z - 0.6);
const lampPost = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 1.2, 8), M.steel);
lampPost.position.set(lamp.position.x, 1.5, lamp.position.z);
scene.add(lamp, lampPost);
const lampLight = new THREE.PointLight("#5cc8ff", 0, 7, 2);
lampLight.position.copy(lamp.position);
scene.add(lampLight);

// ---------- post ----------
const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.55, 0.6, 0.82);
composer.addPass(bloom);
composer.addPass(new OutputPass());

// ---------- DOM ----------
const $ = (id) => document.getElementById(id);
const tagsEl = $("tags");
const TAGS = {};
function makeTag(stop, name) {
  const el = document.createElement("div");
  el.className = "tag";
  el.innerHTML = `<span class="t"></span><span class="p"></span>`;
  el.querySelector(".t").textContent = name;
  tagsEl.appendChild(el);
  TAGS[stop] = el;
}
makeTag("unit", "Ward");
makeTag("tube", "Pneumatic tube");
makeTag("station", "Lab tube station");
makeTag("lab", "Lab");
makeTag("lost", "Lost");
const ANCHOR = {
  unit: POS.unit.clone().add(new THREE.Vector3(0, 2.2, 0)),
  tube: tubeCurve.getPointAt(0.62).clone().add(new THREE.Vector3(0, 0.75, 0)),
  station: POS.station.clone().add(new THREE.Vector3(0.15, 2.35, 0)),
  lab: POS.lab.clone().add(new THREE.Vector3(0, 1.85, 0)),
  lost: POS.lost.clone().add(new THREE.Vector3(0, 1.55, 0.3)),
};

const pillsEl = $("pills");
data.scenes.forEach((sc, i) => {
  const b = document.createElement("button");
  b.innerHTML = `<span class="n">${i + 1}</span>${S.KIND[sc.kind].label}`;
  b.style.setProperty("--c", S.KIND[sc.kind].color);
  b.addEventListener("click", () => startScene(i));
  pillsEl.appendChild(b);
});

const capEl = $("caption-text");
const card = $("card");
const minuteEl = $("minute");
const fillEl = $("tl-fill");
const deadlineEl = $("tl-deadline");
const scanEl = $("tl-scan");
const flagsEl = $("tl-flags");
const bars = {};
for (const b of document.querySelectorAll(".bar")) bars[b.dataset.k] = { i: b.querySelector("i"), v: b.querySelector("b") };

// ---------- playback state ----------
const st = {
  i: 0, sc: null, caps: [], t: 0, phase: "intro", phaseT: 0, hold: 0, passed: -1, paused: params.has("paused"),
  xmax: 1, flags: [], capShown: null, lampT: 0, lampColor: new THREE.Color("#5cc8ff"),
};

function pct(x) { return Math.round(x * 100) + "%"; }

function startScene(i, atMinute = null) {
  st.i = (i + data.scenes.length) % data.scenes.length;
  const sc = (st.sc = data.scenes[st.i]);
  st.caps = S.captions(sc);
  st.t = 0;
  st.passed = -1;
  st.hold = 0;
  st.capShown = null;
  st.end = S.endMinute(sc);
  st.xmax = Math.max(sc.window_min, st.end) * 1.04;
  document.documentElement.style.setProperty("--kind", S.KIND[sc.kind].color);
  [...pillsEl.children].forEach((b, k) => b.classList.toggle("on", k === st.i));

  // timeline
  deadlineEl.style.left = (sc.window_min / st.xmax) * 100 + "%";
  deadlineEl.querySelector("span").textContent = `Deadline · ${sc.window_min} min`;
  scanEl.classList.remove("on");
  if (sc.received_min != null) scanEl.style.left = (sc.received_min / st.xmax) * 100 + "%";
  flagsEl.innerHTML = "";
  st.flags = S.alertsOf(sc).filter((a) => sc.received_min == null || a.at < sc.received_min).map((a, row) => {
    const f = document.createElement("div");
    f.className = "flag";
    f.style.left = (a.at / st.xmax) * 100 + "%";
    f.style.setProperty("--c", a.color);
    f.style.height = 30 + row * 22 + "px";
    f.innerHTML = `<span></span><i></i>`;
    f.querySelector("span").textContent = a.name;
    flagsEl.appendChild(f);
    return { el: f, at: a.at };
  });

  if (atMinute != null) {
    st.t = atMinute;
    st.passed = st.caps.filter((c) => c.at <= atMinute).length - 1;
    st.phase = "play";
    hideCard();
  } else {
    st.phase = "intro";
    st.phaseT = 0;
    showIntro(sc);
  }
}

function showIntro(sc) {
  const k = S.KIND[sc.kind];
  card.style.setProperty("--c", k.color);
  card.innerHTML = `<div class="in"><div class="kicker">${st.i + 1} of ${data.scenes.length}</div><h1></h1><p></p></div>`;
  card.querySelector("h1").textContent = k.label;
  const pri = sc.priority === "stat" ? "Stat " : "";
  card.querySelector("p").textContent = `${pri}${sc.specimen} · ${sc.unit} · must reach the lab within ${sc.window_min} min`;
  card.hidden = false;
}

function showOutro(sc) {
  const v = S.verdict(sc);
  card.style.setProperty("--c", S.KIND[sc.kind].color);
  card.innerHTML = `<div class="in"><h2></h2><div class="rows"></div></div>`;
  card.querySelector("h2").textContent = v.headline;
  const rows = card.querySelector(".rows");
  for (const l of v.lines) {
    const r = document.createElement("div");
    r.className = "row";
    r.style.setProperty("--m", l.color);
    r.innerHTML = `<b></b><span></span>`;
    r.querySelector("b").textContent = l.name;
    r.querySelector("span").textContent = l.text;
    rows.appendChild(r);
  }
  card.hidden = false;
}
function hideCard() { card.hidden = true; }

function setCaption(c) {
  if (st.capShown === c) return;
  st.capShown = c;
  capEl.classList.add("swap");
  setTimeout(() => {
    capEl.className = c.tone;
    if (c.methods) {
      // Name each method in its own color.
      capEl.textContent = "";
      const parts = c.text.split(/(Tracer(?:Net)?|Fixed timer)/);
      for (const part of parts) {
        const m = S.METHODS.find((x) => x.name === part);
        if (m) {
          const s = document.createElement("span");
          s.className = "who";
          s.style.color = m.color;
          s.textContent = part;
          capEl.appendChild(s);
        } else capEl.appendChild(document.createTextNode(part));
      }
    } else capEl.textContent = c.text;
  }, 180);
  if (c.tone === "alert") {
    st.lampT = 1.6;
    st.lampColor.set(S.METHODS.find((m) => m.key === c.methods[0]).color);
  }
}

// ---------- the specimen's place in the scene ----------
const tmp = new THREE.Vector3();
const STATION_TRAY = POS.station.clone().add(new THREE.Vector3(0.15, 0.6, 0.75));
const LAB_BENCH = POS.lab.clone().add(new THREE.Vector3(0.3, 1.14, 0.0));
const WARD_SPOT = new THREE.Vector3(-8.0, 0.55, 0.65).add(new THREE.Vector3(0, 0, 0));
const BIN_SPOT = POS.lost.clone().add(new THREE.Vector3(0, 0.2, 0));
const ease = (x) => x * x * (3 - 2 * x);

function placeSpecimen(sc, t, clock) {
  const tr = S.truthAt(sc, t);
  let color = "#bfe6ff";
  spec.rotation.set(0, 0, 0);
  if (tr.stop === "unit") {
    spec.position.copy(WARD_SPOT);
    spec.position.y += 0.06 * Math.sin(clock * 2.2);
    spec.rotation.z = Math.PI / 2;
  } else if (tr.stop === "tube") {
    const uEnd = tr.next === "lost" ? U_BRANCH : 1;
    const u = ease(tr.frac) * uEnd;
    tubeCurve.getPointAt(u, spec.position);
    tubeCurve.getTangentAt(u, tmp);
    spec.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), tmp);
  } else if (tr.stop === "station") {
    const k = ease(Math.min((t - tr.start) / 2.5, 1));
    tmp.copy(tubeCurve.getPointAt(1));
    spec.position.lerpVectors(tmp, STATION_TRAY, tr.raw === "stalled_at_station" ? 1 : k);
    spec.rotation.z = Math.PI / 2;
    if (tr.raw === "stalled_at_station") color = S.KIND.stalled.color;
  } else if (tr.stop === "lab") {
    const k = ease(Math.min((t - tr.start) / 4, 1));
    spec.position.lerpVectors(STATION_TRAY, LAB_BENCH, k);
    spec.position.y += Math.sin(k * Math.PI) * 0.9;
    spec.rotation.z = Math.PI / 2;
    color = sc.breach ? S.KIND.lost.color : S.KIND.on_time.color;
  } else if (tr.stop === "lost") {
    const k = Math.min((t - tr.start) / 3, 1);
    if (k < 1) {
      branchCurve.getPointAt(ease(k), spec.position);
      branchCurve.getTangentAt(ease(k), tmp);
      spec.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), tmp);
    } else {
      spec.position.copy(BIN_SPOT);
      spec.rotation.z = Math.PI / 2.4;
    }
    color = S.KIND.lost.color;
  }
  specMat.emissive.set(color);
  specLight.color.set(color);
  const stalled = tr.raw === "stalled_at_station" || tr.stop === "lost";
  specMat.emissiveIntensity = stalled ? 1.3 + 0.6 * Math.sin(clock * 4) : 1.6;
  return tr;
}

// ---------- frame ----------
function resize() {
  const w = innerWidth, h = innerHeight;
  renderer.setSize(w, h, false);
  composer.setSize(w, h);
  camera.aspect = w / h;
  // Pull back on narrow screens so the whole bench stays in view.
  const dist = 21 * Math.max(1, 1.55 / camera.aspect);
  camera.userData.dist = dist;
  camera.updateProjectionMatrix();
}
addEventListener("resize", resize);
resize();

function project(v) {
  tmp.copy(v).project(camera);
  return { x: (tmp.x * 0.5 + 0.5) * innerWidth, y: (-tmp.y * 0.5 + 0.5) * innerHeight, behind: tmp.z > 1 };
}

let last = performance.now();
let clock = 0;
function frame(now) {
  const dt = Math.min((now - last) / 1000, 0.1);
  last = now;
  clock += dt;
  const sc = st.sc;

  if (!st.paused) {
    if (st.phase === "intro") {
      st.phaseT += dt;
      if (st.phaseT >= INTRO_SEC) { st.phase = "play"; hideCard(); }
    } else if (st.phase === "play") {
      if (st.hold > 0) st.hold -= dt;
      else {
        const next = st.caps[st.passed + 1];
        let t2 = st.t + dt * MIN_PER_SEC;
        if (next && t2 >= next.at) {
          t2 = next.at;
          st.passed += 1;
          st.hold = HOLD[next.tone] ?? 1;
        }
        st.t = t2;
        if (st.t >= st.end) { st.t = st.end; st.phase = "outro"; st.phaseT = 0; showOutro(sc); }
      }
    } else if (st.phase === "outro") {
      st.phaseT += dt;
      if (st.phaseT >= OUTRO_SEC) startScene(st.i + 1);
    }
  }

  const t = st.t;
  // caption
  setCaption(S.captionAt(st.caps, t));
  // clock and timeline
  minuteEl.textContent = S.fmt(Math.floor(t));
  fillEl.style.width = Math.min(t / st.xmax, 1) * 100 + "%";
  scanEl.classList.toggle("on", sc.received_min != null && t >= sc.received_min);
  for (const f of st.flags) f.el.classList.toggle("on", t >= f.at);
  // risk bars
  const r = S.riskAt(sc, TICK, t);
  for (const k of ["tracer", "tracernet"]) {
    bars[k].i.style.width = r[k] * 100 + "%";
    bars[k].v.textContent = pct(r[k]);
  }
  // specimen and Tracer's guess
  placeSpecimen(sc, t, clock);
  const b = S.beliefAt(sc, data.stops, TICK, t);
  for (const stop of ["unit", "station", "lab", "lost"]) {
    const p = b ? b[stop] : 0;
    const m = halos[stop];
    m.material.opacity += ((p * 1.1) - m.material.opacity) * Math.min(dt * 6, 1);
    const s = 0.55 + p * 0.9;
    m.scale.setScalar(s);
  }
  tubeGlowMat.opacity += (((b ? b.tube : 0) * 1.4) - tubeGlowMat.opacity) * Math.min(dt * 6, 1);
  scanMat.emissiveIntensity = sc.received_min != null && t >= sc.received_min ? 2.2 : 0.05;

  // alert lamp
  st.lampT = Math.max(st.lampT - dt, 0);
  const lampOn = st.flags.some((f) => t >= f.at);
  const pulse = st.lampT > 0 ? 3 + 2 * Math.sin(clock * 14) : lampOn ? 1.2 : 0;
  lampMat.emissive.copy(st.lampColor);
  lampMat.emissiveIntensity = pulse;
  lampLight.color.copy(st.lampColor);
  lampLight.intensity = pulse * 3;

  // labels
  for (const [stop, el] of Object.entries(TAGS)) {
    const q = project(ANCHOR[stop]);
    el.style.left = q.x + "px";
    el.style.top = q.y + "px";
    const p = b ? b[stop] : 0;
    const pe = el.querySelector(".p");
    pe.textContent = p >= 0.1 ? pct(p) : "";
    pe.style.opacity = p >= 0.1 ? 1 : 0;
  }

  // calm camera
  const d = camera.userData.dist;
  const a = Math.sin(clock * 0.07) * 0.09;
  camera.position.set(LOOK.x + Math.sin(a) * d, LOOK.y + d * 0.42, LOOK.z + Math.cos(a) * d);
  camera.lookAt(LOOK);

  composer.render();
  requestAnimationFrame(frame);
}

// ---------- controls ----------
const pauseBtn = $("pause");
function setPaused(p) { st.paused = p; pauseBtn.textContent = p ? "▶" : "❚❚"; pauseBtn.setAttribute("aria-label", p ? "Play" : "Pause"); }
pauseBtn.addEventListener("click", () => setPaused(!st.paused));
addEventListener("keydown", (e) => {
  if (e.code === "Space") { e.preventDefault(); setPaused(!st.paused); }
  else if (e.key === "ArrowRight") startScene(st.i + 1);
  else if (e.key === "ArrowLeft") startScene(st.i - 1);
});

const startAt = params.has("scene") ? Number(params.get("scene")) - 1 : 0;
startScene(startAt, params.has("t") ? Number(params.get("t")) : null);
setPaused(st.paused);
requestAnimationFrame(frame);
