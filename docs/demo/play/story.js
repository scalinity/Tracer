// The plain-language story of one specimen, from scenes.json. No three.js here, so it can be checked under node.

export const METHODS = [
  { key: "tracer", name: "Tracer", color: "#5cc8ff" },
  { key: "tracernet", name: "TracerNet", color: "#b49cff" },
  { key: "rule", name: "Fixed timer", color: "#9aa7b4" },
];

export const KIND = {
  on_time: { label: "On time", color: "#3ddc97" },
  stalled: { label: "Stalled", color: "#ffb547" },
  lost: { label: "Lost", color: "#ff6b6b" },
};

const PLACE = { unit: "ward", tube: "tube", station: "lab's tube station", lab: "lab", lost: "lost" };

// How long each scene keeps playing after its last event, in specimen minutes.
export function endMinute(scene) {
  if (scene.received_min != null) return scene.received_min + 12;
  return scene.window_min + 12;
}

// Where the specimen really is at minute t: the truth segment, and how far through it.
export function truthAt(scene, t) {
  const segs = scene.truth;
  for (let i = 0; i < segs.length; i++) {
    const [stop, start, end, raw] = segs[i];
    const last = i === segs.length - 1;
    if (t >= start && (end == null || t < end || last)) {
      const span = end == null ? Infinity : Math.max(end - start, 1e-6);
      return { stop, raw, start, end, frac: Math.min(Math.max((t - start) / span, 0), 1), next: segs[i + 1] ? segs[i + 1][0] : null, index: i };
    }
  }
  const [stop, start, end, raw] = segs[0];
  return { stop, raw, start, end, frac: 0, next: segs[1] ? segs[1][0] : null, index: 0 };
}

// A tick series (one value every tick_min minutes) read at minute t, linearly between ticks.
// Before the receipt scan the series runs on; after it the clock has stopped, so the last value holds.
export function seriesAt(series, tick, t) {
  if (!series.length) return null;
  const x = Math.max(t, 0) / tick;
  const i = Math.floor(x);
  if (i >= series.length - 1) return series[series.length - 1];
  const f = x - i;
  return series[i] * (1 - f) + series[i + 1] * f;
}

// The chance each model gives that the specimen misses its deadline, at minute t.
export function riskAt(scene, tick, t) {
  const silentEnd = scene.received_min ?? Infinity;
  const tt = Math.min(t, silentEnd);
  const out = {};
  for (const k of ["tracer", "tracernet"]) {
    let v = seriesAt(scene.risk[k], tick, tt);
    if (scene.received_min == null && t >= scene.window_min) v = 1;
    if (scene.received_min != null && t >= scene.received_min) v = scene.breach ? 1 : 0;
    out[k] = v;
  }
  return out;
}

// Tracer's belief about where the specimen is, at minute t (null once it is scanned in).
export function beliefAt(scene, stops, tick, t) {
  if (scene.received_min != null && t >= scene.received_min) return null;
  const rows = scene.places;
  const x = Math.max(t, 0) / tick;
  const i = Math.min(Math.floor(x), rows.length - 1);
  const j = Math.min(i + 1, rows.length - 1);
  const f = Math.min(x - i, 1);
  const out = {};
  stops.forEach((s, k) => { out[s] = rows[i][k] * (1 - f) + rows[j][k] * f; });
  return out;
}

export function alertsOf(scene) {
  return METHODS.filter((m) => scene.alerts[m.key] != null)
    .map((m) => ({ ...m, at: scene.alerts[m.key], spare: scene.window_min - scene.alerts[m.key] }))
    .sort((a, b) => a.at - b.at || METHODS.indexOf(a) - METHODS.indexOf(b));
}

function joinNames(list) {
  const n = list.map((a) => a.name);
  return n.length <= 1 ? n.join("") : n.slice(0, -1).join(", ") + " and " + n[n.length - 1];
}

// The captions, in order: { at, text, tone }. The one showing at minute t is the last with at <= t.
export function captions(scene) {
  const out = [];
  const what = scene.specimen;
  const pri = scene.priority === "stat" ? "A stat " : "A ";
  out.push({ at: 0, text: `${pri}${what} sample leaves the ${scene.unit === "ICU" ? "ICU" : scene.unit.toLowerCase()}`, tone: "info" });
  for (const [stop, start, end, raw] of scene.truth) {
    if (start === 0) continue;
    if (stop === "tube") out.push({ at: start, text: "Into the pneumatic tube", tone: "info" });
    else if (stop === "station" && raw !== "stalled_at_station") out.push({ at: start, text: "Lands at the lab's tube station", tone: "info" });
    else if (raw === "stalled_at_station") out.push({ at: start, text: "…and sits there. Nobody has scanned it in.", tone: "warn" });
    else if (stop === "lost") out.push({ at: start, text: "…and drops out of the system. No scan will ever come.", tone: "bad" });
    else if (stop === "lab") out.push({ at: start, text: `Scanned in at minute ${fmt(start)}`, tone: scene.breach ? "bad" : "good" });
  }
  // A long wait at the ward before the tube gets a line of its own.
  const first = scene.truth[0];
  if (first[0] === "unit" && first[2] != null && first[2] - first[1] >= 15) {
    out.push({ at: Math.min(10, first[2] - 5), text: "Still waiting at the ward. The lab can't see it.", tone: "warn" });
  }
  // Alerts that happen together share one caption.
  const groups = new Map();
  for (const a of alertsOf(scene)) {
    if (scene.received_min != null && a.at >= scene.received_min) continue;
    if (!groups.has(a.at)) groups.set(a.at, []);
    groups.get(a.at).push(a);
  }
  for (const [at, list] of groups) {
    const verb = list.length > 1 ? "raise an alert" : "raises an alert";
    out.push({ at, text: `${joinNames(list)} ${verb}: ${fmt(list[0].spare)} min before the deadline`, tone: "alert", methods: list.map((a) => a.key) });
  }
  if (scene.received_min == null || scene.received_min > scene.window_min) {
    out.push({ at: scene.window_min, text: "Deadline passes. The sample is no longer usable.", tone: "bad" });
  }
  return out.sort((a, b) => a.at - b.at);
}

export function captionAt(list, t) {
  let cur = list[0];
  for (const c of list) if (c.at <= t) cur = c;
  return cur;
}

// The closing card: one headline and one line on the warning each method gave.
export function verdict(scene) {
  const alerts = alertsOf(scene).filter((a) => scene.received_min == null || a.at < scene.received_min);
  let headline;
  if (scene.kind === "on_time") headline = `Arrived in ${fmt(scene.received_min)} min. No alarm needed.`;
  else if (scene.kind === "stalled") {
    const stall = scene.truth.find((s) => s[3] === "stalled_at_station");
    const mins = stall ? stall[2] - stall[1] : 0;
    headline = scene.breach ? `Stuck ${fmt(mins)} min and missed its deadline.` : `Stuck ${fmt(mins)} min, but found in time.`;
  } else headline = "Never arrived. The alert is the only way anyone finds out.";
  const lines = alerts.length
    ? alerts.map((a) => ({ name: a.name, color: a.color, text: `${fmt(a.spare)} min warning` }))
    : [{ name: "Every method", color: "#9aa7b4", text: "stayed quiet, correctly" }];
  return { headline, lines };
}

export function fmt(x) {
  return String(Math.round(x));
}
