"""How well does the location filter place a silent specimen? (plan task M8b; docs/architecture.md §5.3)

    python -m src.pipeline.location_eval

At 15, 30, 60 and 90 minutes after the collection scan, every held-out specimen that has still not been
received is placed by src/models/location.py from its group and route alone, and the most likely place
and the three most likely are compared with where it really was. The truth comes through
evaluate.truth_places; this module never opens the truth file. A specimen's silence is the only thing
the filter sees, so accuracy falls as the silent specimens become the faulty ones. The most common true
place at each landmark is reported beside it, since a filter that always names it scores that much.

The probability of lost is checked against how often specimens really were lost, pooled over every
5-minute tick from minute 5 to 240 at which a specimen was silent. Those ticks are not independent: a
specimen that is lost counts at every tick after it is lost. Writes results/location-accuracy.json and
results/location-calibration.svg. The data was drawn with the same structure the filter assumes, so
this is the best case; a deliberately wrong structure would score lower.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from src.generator import config
from src.models import location
from src.pipeline import evaluate, intervals, store

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "results"
LANDMARKS = (15, 30, 60, 90)
CALIBRATION_TICKS = range(5, 241, 5)
BINS = (0.0, 0.01, 0.02, 0.05, 0.1, 0.2, 0.4, 1.0000001)
LONGEST = max(max(LANDMARKS), max(CALIBRATION_TICKS))


def place_at(segments: list, minute: float) -> str | None:
    """The place of a specimen at a minute after collection, None once it has been received."""
    for begin, end, state in segments:
        if begin <= minute and (end is None or minute < end):
            return state if state in location.PLACES else None
    return None


def silent(spec, minute: float) -> bool:
    return spec.receipt_min is None or spec.receipt_min > minute


def place_series_by_key(specs, params: dict, gen: dict, tube_share: float) -> dict:
    series = {}
    for key in sorted({(s.group, s.route) for s in specs}):
        legs = location.legs_for(params, gen, key[0], key[1], tube_share)
        series[key] = location.place_series(*location.filter_series(legs, LONGEST))
    return series


def ranked(places: dict[str, np.ndarray], minute: int) -> list[str]:
    """The places from most to least likely at a minute; a tie goes to the earlier place in PLACES."""
    p = np.array([places[name][minute] for name in location.PLACES])
    return [location.PLACES[i] for i in np.argsort(-p, kind="stable")]


def accuracy(specs, truth: dict, series: dict, minute: int) -> dict:
    rows = []
    for s in specs:
        if silent(s, minute) and s.specimen_id in truth:
            actual = place_at(truth[s.specimen_id], minute)
            if actual is not None:
                rows.append((actual, ranked(series[(s.group, s.route)], minute)))
    counts = {p: sum(a == p for a, _ in rows) for p in location.PLACES}
    commonest = max(counts, key=counts.get)
    confusion = {p: {q: sum(a == p and r[0] == q for a, r in rows) for q in location.PLACES} for p in location.PLACES if counts[p]}
    return {"silent_specimens": len(rows), "top1": sum(r[0] == a for a, r in rows) / len(rows),
            "top3": sum(a in r[:3] for a, r in rows) / len(rows),
            "most_common_place": commonest, "most_common_share": counts[commonest] / len(rows),
            "true_places": {p: n for p, n in counts.items() if n}, "confusion_true_by_predicted": confusion}


def lost_calibration(specs, truth: dict, series: dict) -> dict:
    p, y, lost_specimens = [], [], set()
    for s in specs:
        for minute in CALIBRATION_TICKS:
            if s.specimen_id in truth and silent(s, minute):
                actual = place_at(truth[s.specimen_id], minute)
                if actual is not None:
                    p.append(series[(s.group, s.route)]["lost"][minute])
                    y.append(actual == "lost")
                    if actual == "lost":
                        lost_specimens.add(s.specimen_id)
    p, y = np.array(p), np.array(y, dtype=float)
    base = y.mean()
    bins = []
    for lo, hi in zip(BINS, BINS[1:]):
        m = (p >= lo) & (p < hi)
        if m.any():
            bins.append({"from": lo, "to": min(hi, 1.0), "n": int(m.sum()), "predicted": float(p[m].mean()),
                         "observed": float(y[m].mean())})
    return {"pooled_ticks": len(p), "specimens_really_lost": len(lost_specimens), "share_of_ticks_really_lost": float(base),
            "brier": float(np.mean((p - y) ** 2)), "brier_of_the_base_rate": float(np.mean((base - y) ** 2)), "bins": bins}


def run() -> dict:
    loaded = config.load()
    start, stop = intervals.clock_events(loaded["policy"])
    params = json.loads(intervals.PARAMS_PATH.read_text(encoding="utf-8"))
    if params["policy_hash"] != loaded["policy_hash"]:
        raise RuntimeError("the fitted parameters were made under a different policy; refit before scoring")
    obs = store.open_observed()
    fit_specs, _ = evaluate.load_specs(obs, None, start, stop, "fit")
    tube_share = sum(s.route == "tube" for s in fit_specs) / len(fit_specs)
    specs, counts = evaluate.load_specs(obs, None, start, stop, "test")
    truth = evaluate.truth_places("test")
    series = place_series_by_key(specs, params, loaded["config"], tube_share)
    runs = sorted({s.run for s in specs})
    return {"policy_hash": params["policy_hash"], "source_sha256": params["source_sha256"],
            "held_out_runs": f"{runs[0]} to {runs[-1]}", "specimens_with_a_clock": counts["with_a_clock"],
            "tube_share_from_the_fit_runs": tube_share,
            "note": "synthetic data with no clinical review; the filter's structure and fault mix are assumptions the data was drawn with",
            "landmarks": {str(m): accuracy(specs, truth, series, m) for m in LANDMARKS},
            "lost_calibration": lost_calibration(specs, truth, series)}


def svg(r: dict) -> str:
    w, h, top, bottom = 920, 400, 46, 56
    left1, right1 = 56, 420  # the calibration panel
    left2, right2 = 520, w - 24  # the accuracy panel
    cal = r["lost_calibration"]
    py = lambda v: h - bottom - v * (h - top - bottom)
    px1 = lambda v: left1 + v * (right1 - left1)
    grid = lambda lo, hi, px, label: "".join(
        f'<line x1="{lo}" x2="{hi}" y1="{py(g):.1f}" y2="{py(g):.1f}" stroke="#ddd"/>'
        f'<text x="{lo - 8}" y="{py(g) + 4:.1f}" text-anchor="end" font-size="11" fill="#555">{g:.0%}</text>'
        for g in (0, 0.25, 0.5, 0.75, 1.0)) + "".join(
        f'<text x="{px(g):.1f}" y="{h - bottom + 16}" text-anchor="middle" font-size="11" fill="#555">{g:.0%}</text>' for g in label)
    dots = "".join(f'<circle cx="{px1(b["predicted"]):.1f}" cy="{py(b["observed"]):.1f}" r="{3 + min(9, b["n"] ** 0.5 / 12):.1f}" '
                   f'fill="#d9541e" fill-opacity="0.8"><title>{b["n"]} ticks</title></circle>' for b in cal["bins"])
    line = " ".join(f'{px1(b["predicted"]):.1f},{py(b["observed"]):.1f}' for b in cal["bins"])
    marks = list(r["landmarks"])
    slot = (right2 - left2) / len(marks)
    bars = ""
    for i, m in enumerate(marks):
        x = left2 + i * slot
        a = r["landmarks"][m]
        for j, (key, color) in enumerate((("top1", "#1b6ca8"), ("top3", "#7bb3d9"), ("most_common_share", "#bbb"))):
            bw = slot / 4
            bars += (f'<rect x="{x + slot * 0.1 + j * bw:.1f}" y="{py(a[key]):.1f}" width="{bw - 3:.1f}" height="{h - bottom - py(a[key]):.1f}" fill="{color}"/>'
                     f'<text x="{x + slot * 0.1 + j * bw + (bw - 3) / 2:.1f}" y="{py(a[key]) - 4:.1f}" text-anchor="middle" font-size="10" fill="#333">{a[key]:.0%}</text>')
        bars += (f'<text x="{x + slot / 2:.1f}" y="{h - bottom + 16}" text-anchor="middle" font-size="11" fill="#555">{m} min</text>'
                 f'<text x="{x + slot / 2:.1f}" y="{h - bottom + 30}" text-anchor="middle" font-size="10" fill="#777">n = {a["silent_specimens"]}</text>')
    legend = "".join(f'<rect x="{left2 + k * 120}" y="{top - 22}" width="10" height="10" fill="{c}"/>'
                     f'<text x="{left2 + k * 120 + 14}" y="{top - 13}" font-size="11" fill="#333">{t}</text>'
                     for k, (c, t) in enumerate((("#1b6ca8", "top 1"), ("#7bb3d9", "top 3"), ("#bbb", "most common place"))))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'font-family="system-ui, sans-serif"><rect width="{w}" height="{h}" fill="#fff"/>'
            f'<text x="{left1}" y="18" font-size="13" font-weight="600" fill="#222">Chance of lost: predicted against real</text>'
            f'<text x="{left2}" y="18" font-size="13" font-weight="600" fill="#222">Where it is, by minutes of silence</text>'
            + grid(left1, right1, px1, (0, 0.25, 0.5, 0.75, 1.0)) + grid(left2, right2, lambda v: left2, ())
            + f'<line x1="{px1(0):.1f}" y1="{py(0):.1f}" x2="{px1(1):.1f}" y2="{py(1):.1f}" stroke="#999" stroke-dasharray="4 4"/>'
            f'<polyline fill="none" stroke="#d9541e" stroke-width="1.5" points="{line}"/>' + dots
            + f'<text x="{(left1 + right1) / 2:.0f}" y="{h - 10}" text-anchor="middle" font-size="12" fill="#444">predicted chance of lost (dashed: perfect)</text>'
            f'<text x="{left1 + 6}" y="{top + 8}" font-size="10" fill="#777">{cal["specimens_really_lost"]} specimens really lost; ticks pooled, minute 5 to 240</text>'
            + bars + legend + '</svg>\n')


def table(r: dict) -> str:
    lines = [f"location filter, held-out runs {r['held_out_runs']} ({r['specimens_with_a_clock']} specimens with a clock)",
             r["note"], "", f"{'minutes silent':>16s}{'still silent':>14s}{'top 1':>8s}{'top 3':>8s}   most common true place"]
    for m, a in r["landmarks"].items():
        lines.append(f"{m:>16s}{a['silent_specimens']:>14d}{a['top1']:>8.1%}{a['top3']:>8.1%}   "
                     f"{a['most_common_place']} ({a['most_common_share']:.1%})")
    c = r["lost_calibration"]
    lines += ["", f"probability of lost, pooled over {c['pooled_ticks']} specimen-ticks, {c['specimens_really_lost']} specimens really lost: "
              f"Brier {c['brier']:.5f} against {c['brier_of_the_base_rate']:.5f} for the base rate",
              f"  {'predicted':>10s}{'observed':>10s}{'ticks':>9s}"]
    lines += [f"  {b['predicted']:>10.3f}{b['observed']:>10.3f}{b['n']:>9d}" for b in c["bins"]]
    return "\n".join(lines)


def main() -> int:
    result = run()
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "location-accuracy.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    (RESULTS / "location-calibration.svg").write_text(svg(result), encoding="utf-8")
    print(table(result))
    print("\nwrote results/location-accuracy.json and results/location-calibration.svg")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        sys.exit(f"error: {e}")
