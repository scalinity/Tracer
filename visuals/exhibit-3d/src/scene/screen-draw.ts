// What the console screens draw. Every value comes from the model's reading and its risk curve; the
// screens redraw only when what they show changes (DiegeticScreen.draw deps).

import { Chart, SCREEN_COLORS } from '../kit/screens';
import type { Place } from '../model/fates';
import type { Reading } from '../model/state';

const MONO = '"DM Mono", ui-monospace, monospace';
const CREAM = '#f1e2c0';

/** The chart's plot area as fractions of the canvas, shared with the drag handles laid over it. */
export const PLOT = { x0: 0.085, x1: 0.955, y0: 0.25, y1: 0.8 };
/** The chart's time axis, minutes. */
export const CHART_T = 130;

export interface ChartData {
  reading: Reading;
  /** The risk Tracer computed at each whole minute so far, as the scans then allowed. */
  history: number[];
  /** riskUnreceived for the whole run at this window: the projection while silent. */
  curve: Float64Array;
}

const pct = (p: number, d = 1) => `${(p * 100).toFixed(d)}%`;

export function drawChart(g: CanvasRenderingContext2D, size: { w: number; h: number }, d: ChartData): void {
  const r = d.reading;
  const rect = { x: size.w * PLOT.x0, y: size.h * PLOT.y0, w: size.w * (PLOT.x1 - PLOT.x0), h: size.h * (PLOT.y1 - PLOT.y0) };
  const plot = new Chart(g, rect, [0, CHART_T], [0, 1]);
  const k = plot.k;
  plot.grid({ xStep: 30, yStep: 0.25 }).xLabels([0, 30, 60, 90, 120], (v) => `${v}`);
  g.save();
  g.fillStyle = SCREEN_COLORS.text;
  g.font = `500 ${Math.round(8.5 * k)}px ${MONO}`;
  g.textAlign = 'right';
  g.textBaseline = 'middle';
  for (const p of [0, 0.5, 1]) g.fillText(`${p * 100}%`, rect.x - 5 * k, plot.py(p));
  g.textAlign = 'left';
  g.textBaseline = 'top';
  g.fillText('MIN OF SILENCE', rect.x + rect.w - 74 * k, rect.y + rect.h + 15 * k);
  g.restore();
  if (r.windowMin < CHART_T) plot.region(r.windowMin, CHART_T, { label: 'window closed' });
  plot.caption('Risk of missing the window');
  // The current risk at the right of the caption, in the hot color.
  g.save();
  g.font = `500 ${Math.round(13 * k)}px ${MONO}`;
  g.fillStyle = SCREEN_COLORS.cyan;
  g.shadowColor = SCREEN_COLORS.cyan;
  g.shadowBlur = 5 * k;
  g.textAlign = 'right';
  g.textBaseline = 'bottom';
  g.fillText(pct(r.risk), rect.x + rect.w, rect.y - 5 * k);
  g.restore();

  // The alert line: amber, dashed, with its grip at the right end.
  const ly = plot.py(r.alertLine);
  g.save();
  g.strokeStyle = SCREEN_COLORS.amber;
  g.globalAlpha = 0.9;
  g.lineWidth = 1.6 * k;
  g.setLineDash([5 * k, 4 * k]);
  g.beginPath();
  g.moveTo(rect.x, ly);
  g.lineTo(rect.x + rect.w, ly);
  g.stroke();
  g.setLineDash([]);
  g.fillStyle = SCREEN_COLORS.amber;
  g.beginPath();
  g.arc(rect.x + rect.w, ly, 3.4 * k, 0, Math.PI * 2);
  g.fill();
  g.font = `500 ${Math.round(8.5 * k)}px ${MONO}`;
  g.textAlign = 'right';
  g.textBaseline = r.alertLine > 0.85 ? 'top' : 'bottom';
  g.fillText(`ALERT LINE ${pct(r.alertLine, 0)}`, rect.x + rect.w - 8 * k, ly + (r.alertLine > 0.85 ? 4 : -4) * k);
  g.restore();

  // While silent: what Tracer projects if no scan comes, dashed.
  if (r.phase === 'silent' && r.minute < CHART_T) {
    const pts: [number, number][] = [];
    for (let t = r.minute; t <= CHART_T; t++) pts.push([t, d.curve[t]]);
    plot.curve(pts, { color: SCREEN_COLORS.cyan, width: 1.3, glow: 0, dash: [3, 4] });
  }
  // What Tracer has computed so far, minute by minute.
  plot.curve(d.history.map((v, t) => [t, v] as [number, number]), { width: 2.4, glow: 9 });

  // The projected alert minute on the line.
  if (r.alert.projected !== null) {
    const ax = plot.px(r.alert.projected);
    g.save();
    g.fillStyle = SCREEN_COLORS.amber;
    g.shadowColor = SCREEN_COLORS.amber;
    g.shadowBlur = r.alert.fired ? 8 * k : 0;
    g.beginPath();
    g.moveTo(ax, ly - 6 * k);
    g.lineTo(ax + 4.5 * k, ly);
    g.lineTo(ax, ly + 6 * k);
    g.lineTo(ax - 4.5 * k, ly);
    g.closePath();
    g.fill();
    g.shadowBlur = 0;
    g.font = `500 ${Math.round(9 * k)}px ${MONO}`;
    g.textBaseline = 'top';
    // The risk crosses the line here rising left to right, so the label takes the side the curve leaves
    // clear: above the line to the left of the crossing, or below it to the right.
    const below = r.alertLine > 0.6;
    const text = r.alert.fired ? `ALERT AT ${r.alert.minute} MIN` : `ALERT AT ${r.alert.projected} MIN IF SILENT`;
    const tw = g.measureText(text).width;
    g.textAlign = below ? 'left' : 'right';
    const tx = below ? Math.min(ax + 8 * k, rect.x + rect.w - tw) : Math.max(ax - 8 * k, rect.x + tw);
    g.fillText(text, tx, below ? ly + 9 * k : ly - 20 * k);
    g.restore();
  }
  // Scans the lab has seen besides the collection: ticks on the time axis.
  for (const [t, name] of [[r.received, 'RECEIVED'], [r.plated, 'PLATED']] as [number | null, string][]) {
    if (t === null) continue;
    g.save();
    g.strokeStyle = CREAM;
    g.fillStyle = CREAM;
    g.lineWidth = 1.4 * k;
    g.beginPath();
    g.moveTo(plot.px(t), rect.y + rect.h);
    g.lineTo(plot.px(t), rect.y + rect.h - 9 * k);
    g.stroke();
    g.font = `500 ${Math.round(7.5 * k)}px ${MONO}`;
    g.textAlign = 'center';
    g.textBaseline = 'bottom';
    g.fillText(name, plot.px(t), rect.y + rect.h - 10 * k);
    g.restore();
  }
  plot.marker(r.minute, r.risk, { drop: true });
}

export interface BarsData {
  reading: Reading;
  /** The real carrier's place, while the truth is shown. */
  truth: Place | null;
}

const ROWS: { place: Place; name: string }[] = [
  { place: 'ward', name: 'WARD' },
  { place: 'tube', name: 'TUBE' },
  { place: 'station', name: 'STATION' },
  { place: 'lost', name: 'LOST' },
  { place: 'lab', name: 'LAB' },
];

export function drawBars(g: CanvasRenderingContext2D, size: { w: number; h: number }, d: BarsData): void {
  const r = d.reading;
  const k = size.h / 256;
  const left = size.w * 0.07;
  const barX = size.w * 0.3;
  const barW = size.w * 0.42;
  g.save();
  g.fillStyle = SCREEN_COLORS.text;
  g.font = `500 ${Math.round(10 * k)}px ${MONO}`;
  g.textBaseline = 'alphabetic';
  g.fillText(r.phase === 'silent' ? 'WHERE IS IT NOW?' : 'LOCATION KNOWN', left, size.h * 0.17);
  g.restore();
  const top = size.h * 0.26;
  const rowH = size.h * 0.13;
  ROWS.forEach((row, i) => {
    const y = top + i * rowH;
    const p = r.places[row.place];
    const best = r.likely.place === row.place;
    const lost = row.place === 'lost';
    const color = lost ? SCREEN_COLORS.coral : SCREEN_COLORS.cyan;
    g.save();
    g.font = `${best ? 600 : 500} ${Math.round(9.5 * k)}px ${MONO}`;
    g.fillStyle = best ? (lost ? SCREEN_COLORS.coral : SCREEN_COLORS.white) : SCREEN_COLORS.text;
    g.textBaseline = 'middle';
    g.fillText(row.name, left, y + rowH * 0.42);
    // Track, then the share, the station's stalled part in violet.
    const bh = rowH * 0.42;
    const by = y + rowH * 0.42 - bh / 2;
    g.fillStyle = 'rgba(160,190,230,0.12)';
    g.fillRect(barX, by, barW, bh);
    const stalled = row.place === 'station' ? r.stalled : 0;
    g.shadowColor = color;
    g.shadowBlur = best ? 8 * k : 3 * k;
    g.globalAlpha = best ? 1 : 0.62;
    g.fillStyle = color;
    g.fillRect(barX, by, barW * (p - stalled), bh);
    if (stalled > 0) {
      g.fillStyle = SCREEN_COLORS.violet;
      g.shadowColor = SCREEN_COLORS.violet;
      g.fillRect(barX + barW * (p - stalled), by, barW * stalled, bh);
    }
    g.globalAlpha = 1;
    g.shadowBlur = 0;
    g.font = `500 ${Math.round(9.5 * k)}px ${MONO}`;
    g.fillStyle = best ? color : SCREEN_COLORS.text;
    g.textAlign = 'right';
    g.fillText(pct(p), barX + barW + 52 * k, y + rowH * 0.42);
    if (d.truth === row.place) {
      g.fillStyle = CREAM;
      g.textAlign = 'left';
      g.font = `600 ${Math.round(8.5 * k)}px ${MONO}`;
      g.fillText('◂ TRUTH', barX + barW + 60 * k, y + rowH * 0.42);
    }
    g.restore();
  });
  if (r.stalled > 0.005) {
    g.save();
    g.font = `500 ${Math.round(8 * k)}px ${MONO}`;
    g.fillStyle = SCREEN_COLORS.violet;
    g.fillText(`STALLED ${pct(r.stalled)} OF IT`, barX, top + 5 * rowH + 2 * k);
    g.restore();
  }
}

export interface LogData {
  reading: Reading;
}

/** The receiving desk's scan log: everything the lab sees of this specimen. */
export function drawLog(g: CanvasRenderingContext2D, size: { w: number; h: number }, d: LogData): void {
  const r = d.reading;
  const k = size.h / 256;
  g.save();
  g.fillStyle = SCREEN_COLORS.text;
  g.font = `500 ${Math.round(16 * k)}px ${MONO}`;
  g.fillText('SCAN LOG', size.w * 0.08, size.h * 0.2);
  const rows: [string, string, string][] = [['0 min', 'COLLECTED', SCREEN_COLORS.amber]];
  if (r.received !== null) rows.push([`${r.received} min`, 'RECEIVED', SCREEN_COLORS.amber]);
  if (r.plated !== null) rows.push([`${r.plated} min`, 'PLATED', SCREEN_COLORS.amber]);
  g.font = `500 ${Math.round(15 * k)}px ${MONO}`;
  rows.forEach(([t, what, c], i) => {
    const y = size.h * (0.42 + i * 0.17);
    g.fillStyle = c;
    g.fillText(t.padStart(6, ' '), size.w * 0.08, y);
    g.fillStyle = SCREEN_COLORS.white;
    g.fillText(what, size.w * 0.42, y);
  });
  if (r.phase === 'silent') {
    const y = size.h * (0.42 + rows.length * 0.17);
    g.fillStyle = r.alert.fired ? SCREEN_COLORS.amber : SCREEN_COLORS.text;
    g.fillText(`${String(r.minute).padStart(3, ' ')} min`, size.w * 0.08, y);
    g.fillText(r.alert.fired ? 'SILENT · ALERT' : 'SILENT', size.w * 0.42, y);
  }
  g.restore();
}
