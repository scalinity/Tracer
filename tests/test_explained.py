"""The explainer page (docs/explained/index.html) against the files its figures come from.

The page copies the fitted numbers, the walkthrough specimen's series and about sixty figures by hand, so
nothing else fails when a rerun changes them. These tests slice the page by the text of its constants
(`var POOL = `, `var TYPES = [`, `var RISK = `, `var PLACES = `, `var SEGS = `), as tests/test_risk.py does for
visuals/architecture.html, so keep those.
"""

import csv
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "docs" / "explained" / "index.html").read_text(encoding="utf-8")
TEXT = re.sub(r"\s+", " ", PAGE)


def load(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


PARAMS = load("results/interval_params.json")
BUNDLE = load("docs/demo/bundle.json")
HEADLINE = BUNDLE["grid"][BUNDLE["headline_index"]]
HERO = next(s for s in BUNDLE["specimens"] if s["id"] == "SP-r27-000368")


def js_const(name):
    """The value of `var NAME = ...;` on one line, with bare object keys quoted for JSON."""
    src = re.search(rf"var {name} = (.+?);\n", PAGE).group(1)
    return json.loads(re.sub(r"(\w+):", r'"\1":', src))


def lab_types():
    block = PAGE[PAGE.index("var TYPES = ["):PAGE.index("];", PAGE.index("var TYPES = ["))]
    pattern = r'group: "(.+?)", .*?mu: ([\d.]+), sg: ([\d.]+), W: (\d+), tier: (\d)'
    return [(g, float(mu), float(sg), int(w), int(t)) for g, mu, sg, w, t in re.findall(pattern, block)]


def test_pooled_parameters_and_threshold_are_the_fit():
    pool, pooled = js_const("POOL"), PARAMS["pooled"]
    assert pool == {"wN": pooled["weights"]["normal"], "wS": pooled["weights"]["stalled"],
                    "pNever": pooled["weights"]["never"], "sMu": pooled["stalled_mu"], "sSg": pooled["stalled_sigma"]}
    assert float(re.search(r"var THRESH = ([\d.]+);", PAGE).group(1)) == pytest.approx(
        HEADLINE["model"]["harm_threshold"], abs=5e-6)


def test_lab_types_are_fitted_groups_with_their_policy_window_and_tier():
    rows = list(csv.DictReader((ROOT / "config" / "policy_table.csv").open(encoding="utf-8")))
    types = lab_types()
    assert len(types) == 5
    for group, mu, sg, window, tier in types:
        fit = PARAMS["groups"][group]
        assert (mu, sg) == (fit["mu"], fit["sigma"]), group
        policy = [r for r in rows if r["specimen_type"] == group.split(" | ")[0]]
        assert window == min(int(r["window_min"]) for r in policy), group  # shortest window governs
        assert {tier} == {int(r["urgency_tier"]) for r in policy}, group


def test_replay_is_the_bundles_specimen():
    assert js_const("RISK") == [round(r, 4) for r in HERO["risk"]]
    assert js_const("PLACES") == HERO["places"]
    order = BUNDLE["place_order"]
    silent = [s for s in HERO["truth"]["segments"] if s[1] < HERO["received_min"]]
    assert js_const("SEGS") == [[int(a), int(b), order.index(place)] for place, a, b in silent]
    assert (HERO["alerts"]["model"], HERO["alerts"]["plan_baseline"], HERO["alerts"]["window_baseline"]) == (20, 25, 25)
    for needle in ("Tracer alerts, 20", "Both simple rules, 25", f"Received, {HERO['received_min']:.0f}",
                   f"Window closes, {HERO['window_min']}"):
        assert needle in PAGE


def test_replay_curve_is_drawn_from_the_series():
    points = re.search(r'<polyline [^>]*points="([^"]+)"', PAGE).group(1).split()
    risk = js_const("RISK")
    assert len(points) == len(risk)
    for i, (point, r) in enumerate(zip(points, risk)):
        x, y = map(float, point.split(","))
        assert x == pytest.approx(40 + 4.5 * BUNDLE["tick_min"] * (i + 1), abs=0.06)
        assert y == pytest.approx(180 - 150 * r, abs=0.06)


def test_headline_figures_match_their_sources():
    manifest = load("datasets/manifest.json")
    faults = manifest["requested_by_fault"]
    source = BUNDLE["source"]
    location = load("results/location-accuracy.json")["landmarks"]["30"]
    recovery = load("results/parameter-recovery.json")
    at30 = next(p for p in recovery["survival_at"] if p["minutes"] == 30)
    pooled = PARAMS["pooled"]
    policy = (ROOT / "config" / "breach_policy.yaml").read_text(encoding="utf-8")
    rows, assumed = policy.count("\n    clock_stop:"), policy.count("\n    assumed: true")
    alerts = round(HEADLINE["model"]["alerts_per_shift"] * source["shifts"])
    equal = load("results/mvp-nov8.json")["headline_by_alerts_per_shift"]["6"]["median_warning_all_breaches_min"]
    needles = [
        f"{manifest['accepted']:,} of {manifest['requested']:,}", f"${manifest['cost_usd']:.2f}",
        f"{manifest['dropped']} dropped", f"({manifest['accepted_rate']:.1%})", f"{manifest['first_attempt_rate']:.1%} on the first try",
        f"{faults['stalled_at_station']} stall at the station", f"{faults['content_failure']} are mislabeled",
        f"{faults['not_dispatched']} are never sent", f"{faults['misrouted']} go to the wrong station",
        f"{faults['never_collected']} are never collected", f"{faults['lost_in_tube']} are lost in the tube",
        f"{faults['none']:,} have no fault",
        f"{source['specimens']:,} specimens with a clock, {source['breaches']} missed windows, {source['shifts']} shifts",
        f"{alerts} / {HEADLINE['model']['false_alerts']}",
        f"{pooled['n_started']:,} specimens", f"{pooled['weights']['normal']:.1%} of specimens",
        f"({pooled['weights']['stalled']:.1%},", f"never arriving ({pooled['weights']['never']:.1%})",
        f"{len(PARAMS['groups'])} groups", f"fewer than {PARAMS['min_group_size']} received",
        f"{location['top1']:.0%} / {location['top3']:.0%}",
        f"{at30['planted']:.2%} of specimens planted still out at 30 minutes against {at30['fitted']:.2%} fitted",
        f"{recovery['never_arrive']['planted']:.2%} planted never arriving against {recovery['never_arrive']['fitted']:.2%}",
        f"There are {rows} rows: {rows - assumed} have a source and {assumed} are marked assumed",
        f'<span class="v">{HEADLINE["model"]["median_warning_all_breaches_min"]:.0f}</span>',
        f'<span class="v">{HEADLINE["window_baseline"]["median_warning_all_breaches_min"]:.0f}</span>',
        f'<span class="v">{HEADLINE["plan_baseline"]["median_warning_all_breaches_min"]:.0f}</span>',
        f'Tracer, equal weights</span><div class="track"><div class="fill" style="width:75%"></div></div><span class="v">{equal["model_equal_weights"]:.0f}</span>',
        f"comes out at {HEADLINE['model']['harm_threshold']:.3f}",
    ]
    missing = [n for n in needles if n not in TEXT]
    assert not missing, missing
