// The loupe's drawing: why silence raises the risk. Of 100 specimens still silent at this minute, how
// many are on a normal trip (cream), stalled and arriving late (violet), or never arriving (coral):
// the risk mixture's posterior weights (componentWeights), rounded to whole carriers. A drawing of the
// model's numbers, not a simulation of 100 specimens. Its words are the trip model's; the vessels'
// (station, lost) belong to the location model.

import type { Reading } from '../model/state';

const COLORS = ['#e8dcc4', '#b99cff', '#ff6b86'];
const NAMES = ['normal', 'late', 'never arrive'];
const MONO = '"DM Mono", ui-monospace, monospace';

/** Largest-remainder rounding of shares to whole counts that sum to `total`. */
export function wholeCounts(shares: number[], total = 100): number[] {
  const raw = shares.map((s) => s * total);
  const counts = raw.map(Math.floor);
  let left = total - counts.reduce((a, b) => a + b, 0);
  const order = raw.map((v, i) => [v - Math.floor(v), i] as const).sort((a, b) => b[0] - a[0]);
  for (let j = 0; left > 0; j = (j + 1) % order.length, left--) counts[order[j][1]]++;
  return counts;
}

/** Of 100 specimens still silent: on a normal trip, late (every late component), never arriving. */
export function silentCounts(r: Reading): number[] {
  const c = r.components!;
  return wholeCounts([c.parts[0], c.parts.slice(1).reduce((a, b) => a + b, 0), c.lost]);
}

function capsule(g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number): void {
  const r = w / 2;
  g.beginPath();
  g.moveTo(x - r, y - h / 2 + r);
  g.arc(x, y - h / 2 + r, r, Math.PI, 0);
  g.lineTo(x + r, y + h / 2 - r);
  g.arc(x, y + h / 2 - r, r, 0, Math.PI);
  g.closePath();
  g.fill();
}

export function silentDrawing(read: () => Reading): (g: CanvasRenderingContext2D, size: number) => void {
  return (g, size) => {
    const r = read();
    g.fillStyle = '#0a1220';
    g.fillRect(0, 0, size, size);
    const silent = r.components !== null;
    const counts = silent ? silentCounts(r) : [100, 0, 0];
    const cols = 10;
    const x0 = size * 0.21;
    const x1 = size * 0.79;
    const y0 = size * 0.29;
    const y1 = size * 0.73;
    const dx = (x1 - x0) / (cols - 1);
    const dy = (y1 - y0) / 9;
    let kind = 0;
    let used = 0;
    for (let i = 0; i < 100; i++) {
      while (kind < 2 && used >= counts[kind]) {
        kind++;
        used = 0;
      }
      used++;
      const c = i % cols;
      const row = Math.floor(i / cols);
      g.globalAlpha = silent ? 1 : 0.25;
      g.fillStyle = COLORS[kind];
      if (kind > 0 && silent) {
        g.shadowColor = COLORS[kind];
        g.shadowBlur = size * 0.012;
      } else g.shadowBlur = 0;
      capsule(g, x0 + c * dx, y0 + row * dy, size * 0.03, size * 0.04);
    }
    g.globalAlpha = 1;
    g.shadowBlur = 0;
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.font = `500 ${Math.round(size * 0.04)}px ${MONO}`;
    if (silent) {
      g.fillStyle = '#9fb3d1';
      g.fillText(`STILL SILENT AT ${r.minute} MIN`, size / 2, size * 0.785);
      const parts = counts.map((n, i) => `${n} ${NAMES[i]}`);
      g.font = `500 ${Math.round(size * 0.033)}px ${MONO}`;
      let x = size / 2 - g.measureText(parts.join('  ')).width / 2;
      g.textAlign = 'left';
      parts.forEach((p, i) => {
        g.fillStyle = COLORS[i];
        g.fillText(p, x, size * 0.835);
        x += g.measureText(`${p}  `).width;
      });
    } else {
      g.fillStyle = '#9fb3d1';
      g.fillText('SCANNED: NO LONGER SILENT', size / 2, size * 0.83);
    }
  };
}
