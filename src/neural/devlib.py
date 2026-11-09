"""A development library: the worked example and about 45 variants of it, simulated into data/sim-dev/.

    python -m src.neural.devlib                  # library, splits and simulation in data/sim-dev/, store in data/store-v2-dev/
    python -m src.neural.devlib --no-store       # the simulation only

The real scenario library comes from the OpenAI runs (tasks N4 and N5). Until it exists, the store, the feature
builder and the samplers are developed on this one: config/scenario.example.json and variants made by
perturbing it inside the schema's bounds (volume, weekend factor, staffing and shift pattern, route mix,
transit and station times, the specimen mix, and disruptions of every type). Each variant gets the brief its
own content earns (size band, transport mix and staffing classified from the configuration; stratum, failure
focus and edge tags as the variant states them) and is kept only if src.scenarios.validate.validate passes it
with the plausibility KPIs of src.sim.kpi. Variants of one perturbation share a family; families are assigned
to train, val and test, and the scanner-downtime variant is the test_ood scenario. Replicates 0 to 2 are
simulated for train and 0 to 3 for the rest (replicate 0 is history).

Isolation (docs/neural/spec.md §15.2): this is the one module under src/neural/ that calls the simulator and
writes simulator records. It makes development data and is never imported by training code; the isolation test
checks that no other module imports it. Every parameter is an assumption for a synthetic simulator.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from src.generator import config
from src.pipeline import store
from src.scenarios import planner, validate
from src.sim import kpi, run as sim_run

ROOT = Path(__file__).resolve().parents[2]
DEV_DIR = ROOT / "data" / "sim-dev"
LIBRARY_NAME = "library.jsonl.gz"
SPLITS_NAME = "splits.json"
STORE_DEV_DIR = ROOT / "data" / "store-v2-dev"
EXAMPLE_PATH = ROOT / "config" / "scenario.example.json"
REPLICATES = {"train": [0, 1, 2], "val": [0, 1, 2, 3], "test": [0, 1, 2, 3], "test_ood": [0, 1, 2, 3]}
SIZE_RANGES = {"small": (30, 120), "medium": (121, 350), "large": (351, 900)}

_LOADED: dict | None = None


def _loaded() -> dict:
    global _LOADED
    if _LOADED is None:
        _LOADED = config.load()
    return _LOADED


def example() -> dict:
    return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


# --- Perturbations ----------------------------------------------------------------------------------------------

def _unit(s: dict, unit_id: str) -> dict:
    return next(u for u in s["units"] if u["unit_id"] == unit_id)


def _route(s: dict, route_id: str) -> dict:
    return next(r for r in s["couriers"]["routes"] if r["route_id"] == route_id)


def _line(s: dict, line_id: str) -> dict:
    return next(x for x in s["tube_system"]["lines"] if x["line_id"] == line_id)


def _add(s: dict, kind: str, target: str, day: int, start: str, duration: int, severity: float = 0.5, *,
         response: str = "none", noticed: int = 0, recurs: bool = False) -> None:
    s["disruptions"].append({"disruption_id": f"v{len(s['disruptions']) + 1}", "type": kind, "target_id": target,
                             "day": day, "start": start, "duration_min": duration, "severity": severity,
                             "response": response, "noticed_after_min": noticed, "recurs_daily": recurs})


def _keep(s: dict, *types: str) -> None:
    s["disruptions"] = [d for d in s["disruptions"] if d["type"] in types]


def _set(path: tuple, value):
    def fn(s: dict) -> None:
        node = s
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return fn


def _volume(n: int):
    return _set(("volume", "specimens_per_weekday"), n)


def _staff(weekday: dict, weekend: dict):
    def fn(s):
        s["receiving"]["staff_by_shift"] = {"weekday": dict(weekday), "weekend": dict(weekend)}
    return fn


def _seq(*fns):
    def fn(s):
        for f in fns:
            f(s)
    return fn


def _tube_dominant(s):
    for uid in ("med_surg_3", "periop"):
        u = _unit(s, uid)
        u.update(route="tube", tube_line_id="line_b", tube_share_if_mixed=None)
    _unit(s, "periop")["courier_route_id"] = None


def _courier_dominant(s):
    for uid in ("ed_main", "icu_2"):
        _unit(s, uid).update(route="courier", tube_line_id=None, courier_route_id="campus_loop")
    _route(s, "campus_loop")["capacity_per_round"] = 120
    _keep(s, "receiving_short_staffed")
    _add(s, "courier_delayed", "campus_loop", 2, "10:00", 120, 0.5)


def _offsite_heavy(s):
    _unit(s, "clinic_west")["volume_share"] = 0.40
    _route(s, "van_west")["capacity_per_round"] = 150
    _keep(s, "tube_line_outage", "receiving_short_staffed")
    _add(s, "courier_missed_rounds", "van_west", 2, "09:00", 180, 0.6)


def _no_disruptions(s):
    s["disruptions"] = []


def _tilt_tissue(s):
    s["specimen_mix"].update(tissue=0.20, body_fluid_room_temp=0.10, body_fluid_refrigerated=0.06,
                             urine_room_temp=0.12)


def _tilt_urine(s):
    s["specimen_mix"].update(urine_room_temp=0.35, urine_refrigerated=0.14, urine_boric_acid=0.12, tissue=0.03)


# (name, family, split, stratum, failure focus, edge tags, perturbation)
VARIANTS = [
    ("example", "base", "train", "disrupted", "tube_outage", [], lambda s: None),
    ("outage-hold", "base", "train", "disrupted", "tube_outage", [],
     lambda s: s["disruptions"][0].update(response="hold_at_unit")),
    ("outage-stuck", "base", "train", "disrupted", "tube_outage", [],
     lambda s: s["disruptions"][0].update(response="stuck_in_system", noticed_after_min=40)),
    ("volume-150", "volume", "train", "disrupted", "tube_outage", [], _volume(150)),
    ("volume-330", "volume", "train", "disrupted", "tube_outage", [], _seq(_volume(330), _staff(
        {"day": 5, "evening": 4, "night": 2}, {"day": 4, "evening": 3, "night": 2}))),
    ("volume-520", "volume", "train", "disrupted", "tube_outage", [], _seq(_volume(520), _staff(
        {"day": 7, "evening": 5, "night": 3}, {"day": 5, "evening": 4, "night": 2}))),
    ("volume-80", "volume", "train", "disrupted", "tube_outage", [], _volume(80)),
    ("weekend-050", "weekend", "val", "disrupted", "tube_outage", [], _set(("volume", "weekend_factor"), 0.5)),
    ("weekend-100", "weekend", "val", "disrupted", "tube_outage", [], _set(("volume", "weekend_factor"), 1.0)),
    ("staff-full", "staffing", "train", "disrupted", "tube_outage", [],
     _staff({"day": 4, "evening": 3, "night": 3}, {"day": 3, "evening": 3, "night": 2})),
    ("staff-weekend-skeleton", "staffing", "train", "disrupted", "tube_outage", [],
     _staff({"day": 4, "evening": 3, "night": 1}, {"day": 2, "evening": 1, "night": 1})),
    ("staff-chronic-short", "staffing", "train", "disrupted", "tube_outage", [], _seq(_volume(330), _staff(
        {"day": 2, "evening": 2, "night": 1}, {"day": 2, "evening": 2, "night": 1}))),
    ("shift-6-14-22", "shifts", "test", "disrupted", "tube_outage", [],
     _set(("receiving", "shift_pattern"), "6-14-22")),
    ("shift-7-19", "shifts", "test", "disrupted", "tube_outage", [], _seq(
        _set(("receiving", "shift_pattern"), "7-19"),
        _staff({"day": 4, "evening": 1, "night": 2}, {"day": 3, "evening": 1, "night": 1}))),
    ("route-tube-dominant", "routes-tube", "train", "disrupted", "tube_outage", [], _tube_dominant),
    ("route-tube-no-runner", "routes-tube", "train", "disrupted", "tube_outage", [], _seq(
        _tube_dominant, _set(("couriers", "on_call_runner"), False),
        _set(("couriers", "on_call_response_median_min"), None))),
    ("route-courier-dominant", "routes-courier", "val", "disrupted", "courier_unreliable", [], _courier_dominant),
    ("route-offsite-heavy", "routes-courier", "val", "disrupted", "courier_unreliable", [], _offsite_heavy),
    ("transit-slow-tube", "transit", "train", "disrupted", "tube_outage", [], _seq(
        lambda s: _line(s, "line_a").update(transit_median_min=8, transit_p90_min=20),
        lambda s: _line(s, "line_b").update(transit_median_min=12, transit_p90_min=30))),
    ("transit-slow-station", "transit", "train", "disrupted", "tube_outage", [],
     _set(("tube_system", "station_empty_interval_min"), {"day": 20, "evening": 30, "night": 60})),
    ("transit-slow-courier", "transit", "train", "disrupted", "tube_outage", [],
     lambda s: _route(s, "campus_loop").update(round_interval_min={"day": 60, "evening": 90, "night": 120},
                                               travel_median_min=15, travel_p90_min=35)),
    ("transit-misroute", "transit", "train", "disrupted", "tube_outage", [], _seq(
        lambda s: _line(s, "line_b").update(misroute_rate=0.03),
        _set(("tube_system", "misroute_found_min"), {"median": 60, "p90": 150}))),
    ("normal-random", "normal", "test", "normal", "random_faults_only", [], _no_disruptions),
    ("normal-batching", "normal", "test", "normal", "unit_batching", [], _seq(
        _no_disruptions, lambda s: _unit(s, "med_surg_3")["dispatch"].update(mode="batched", batch_interval_min=60))),
    ("normal-small", "normal", "test", "normal", "random_faults_only", [], _seq(_no_disruptions, _volume(90))),
    ("disrupt-degraded", "disrupt-tube", "train", "disrupted", "tube_degradation", [],
     lambda s: _add(s, "tube_line_degraded", "line_b", 2, "10:00", 240, 0.5)),
    ("disrupt-degraded-long", "disrupt-tube", "train", "disrupted", "tube_degradation", [],
     lambda s: _add(s, "tube_line_degraded", "line_a", 4, "07:00", 420, 0.4)),
    ("disrupt-missed-rounds", "disrupt-courier", "train", "disrupted", "courier_unreliable", [],
     lambda s: _add(s, "courier_missed_rounds", "campus_loop", 2, "14:00", 120, 0.6)),
    ("disrupt-courier-delayed", "disrupt-courier", "train", "disrupted", "courier_unreliable", [],
     lambda s: _add(s, "courier_delayed", "van_west", 4, "09:00", 180, 0.5)),
    ("disrupt-offsite-closed", "disrupt-courier", "train", "disrupted", "courier_unreliable", [], _seq(
        lambda s: _add(s, "courier_delayed", "campus_loop", 3, "11:00", 90, 0.4),
        lambda s: _add(s, "offsite_route_closed", "van_west", 5, "08:00", 240, 0.5))),
    ("disrupt-short-staffed", "disrupt-desk", "val", "disrupted", "understaffing_spikes", [], lambda s: None),
    ("disrupt-instrument", "disrupt-desk", "val", "disrupted", "instrument_coupling", [],
     lambda s: _add(s, "instrument_down", "maldi_tof", 2, "10:00", 120, 0.5)),
    ("disrupt-surge", "disrupt-unit", "train", "disrupted", "surge", [],
     lambda s: _add(s, "unit_surge", "ed_main", 1, "17:00", 120, 0.6)),
    ("disrupt-batching-hold", "disrupt-unit", "train", "disrupted", "unit_batching", [],
     lambda s: _add(s, "unit_batching_hold", "med_surg_3", 2, "20:00", 300, 0.5)),
    ("stressed-short-staffed", "stressed", "train", "stressed", "understaffing_spikes", [], _seq(
        _no_disruptions, lambda s: _add(s, "receiving_short_staffed", "receiving", 1, "09:00", 180, 0.5))),
    ("stressed-surge", "stressed", "train", "stressed", "surge", [], _seq(
        _no_disruptions, lambda s: _add(s, "unit_surge", "icu_2", 3, "08:00", 90, 0.5))),
    ("stressed-instrument", "stressed", "train", "stressed", "instrument_coupling", [], _seq(
        _no_disruptions, lambda s: _add(s, "instrument_down", "automated_plater", 4, "13:00", 90, 0.5))),
    ("stressed-random", "stressed", "train", "stressed", "random_faults_only", [], _seq(
        _no_disruptions, lambda s: _add(s, "receiving_short_staffed", "receiving", 5, "15:00", 120, 0.3))),
    ("edge-compound", "edge-a", "train", "edge", "compound", ["compound_failure"], lambda s: None),
    ("edge-unnoticed-outage", "edge-a", "train", "edge", "tube_outage", ["unnoticed_outage"],
     lambda s: s["disruptions"][0].update(noticed_after_min=70, response="stuck_in_system")),
    ("edge-night-station", "edge-a", "train", "edge", "station_neglect", ["night_station_neglect"],
     lambda s: s["disruptions"][2].update(duration_min=90)),
    ("edge-holiday-skeleton", "edge-b", "test", "edge", "understaffing_spikes", ["holiday_skeleton"],
     lambda s: s["disruptions"][1].update(severity=0.6)),
    ("edge-mass-casualty", "edge-b", "test", "edge", "surge", ["mass_casualty_surge"],
     lambda s: _add(s, "unit_surge", "ed_main", 2, "15:00", 180, 0.8)),
    ("mix-tissue", "mix", "val", "disrupted", "tube_outage", [], _tilt_tissue),
    ("mix-urine", "mix", "val", "disrupted", "tube_outage", [], _tilt_urine),
    ("ood-scanner", "ood", "test_ood", "disrupted", "scanner_downtime", [],
     lambda s: _add(s, "scanner_downtime", "receiving", 2, "10:00", 90)),
]


# --- Briefs and validation --------------------------------------------------------------------------------------

def classify(s: dict, focus: str, edge_tags: list[str]) -> None:
    """Set the labels a configuration earns from its content, plus the variant's failure focus and tags."""
    daily = s["volume"]["specimens_per_weekday"]
    s["size_band"] = next(b for b, (_, hi) in SIZE_RANGES.items() if daily <= hi)
    s["transport_mix"] = validate.classify_transport(validate.route_shares(s))
    s["staffing"] = validate.classify_staffing(s)
    s["failure_focus"] = focus
    s["edge_tags"] = list(edge_tags)


def brief_for(s: dict, stratum: str, brief_id: str) -> dict:
    """The planner brief of the scenario's own cell, its anchors moved onto the scenario's main disruption."""
    cell = {"stratum": stratum, "archetype": s["archetype"], "size_band": s["size_band"],
            "transport_mix": s["transport_mix"], "staffing": s["staffing"], "failure_focus": s["failure_focus"],
            "edge_tags": list(s["edge_tags"]), "sub_seed": 1}
    brief = planner.make_brief(cell) | {"brief_id": brief_id}
    anchors = dict(brief["anchors"])
    types = {t for rule in planner.config()["focus_rules"].get(s["failure_focus"], [])
             for t in rule.get("params", {}).get("types", [])}
    main = next((d for d in s["disruptions"] if d["type"] in types), None)
    if main is not None:
        anchors.update(disruption_day=main["day"], disruption_hour=int(main["start"][:2]))
    brief["anchors"] = anchors
    brief["checks"] = planner.checks_for(cell, anchors)
    brief["must_include"] = [c["sentence"] for c in brief["checks"] if c["kind"] == "include"]
    return brief


def candidate(index: int) -> tuple[dict, dict]:
    """Variant `index` as (library record, brief)."""
    name, family, split, stratum, focus, tags, perturb = VARIANTS[index]
    s = example()
    perturb(s)
    classify(s, focus, tags)
    brief = brief_for(s, stratum, f"DEV-{index:03d}")
    record = copy.deepcopy(s) | {"scenario_id": f"dev-{index:02d}-{name}", "family_id": f"dev-{family}",
                                 "split": split, "scenario_index": index, "stratum": stratum}
    return record, brief


def check(index: int) -> dict:
    """Validate variant `index` with every layer up to brief adherence, the plausibility KPIs included."""
    record, brief = candidate(index)
    scenario = {k: v for k, v in record.items() if k not in ("scenario_id", "family_id", "split", "scenario_index",
                                                              "stratum")}
    result = validate.validate(scenario, brief, plausibility=lambda sc, st: kpi.check(sc, st, _loaded()))
    return {"index": index, "record": record, "ok": result.ok, "layer": result.layer, "errors": result.errors}


def library(indices=None, *, checked: bool = True, workers: int = 1) -> tuple[list[dict], list[dict]]:
    """(kept records, rejected checks). With checked=False every candidate is kept (tests use it for speed)."""
    indices = list(range(len(VARIANTS))) if indices is None else list(indices)
    if not checked:
        return [candidate(i)[0] for i in indices], []
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(check, indices))
    else:
        results = [check(i) for i in indices]
    return [r["record"] for r in results if r["ok"]], [r for r in results if not r["ok"]]


def splits_doc(records: list[dict]) -> dict:
    scen = {r["scenario_id"]: r["split"] for r in records}
    fam = {r["family_id"]: r["split"] for r in records}
    counts = {sp: sum(v == sp for v in scen.values()) for sp in REPLICATES}
    return {"scenarios": scen, "families": fam, "counts": counts,
            "ood_rule": "the scanner_downtime variant", "note": "development library splits, assigned by family"}


def build(out_dir: Path = DEV_DIR, *, indices=None, checked: bool = True, workers: int | None = None,
          with_store: bool = True, replicates: dict | None = None, store_dir: Path | None = None) -> dict:
    """Write library.jsonl.gz and splits.json, simulate every replicate, and (with_store) build the v2 store."""
    out_dir = Path(out_dir)
    workers = workers if workers is not None else max(1, (os.cpu_count() or 2) - 1)
    records, rejected = library(indices, checked=checked, workers=workers)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = store.library_bytes(records)
    (out_dir / LIBRARY_NAME).write_bytes(raw)
    splits = splits_doc(records)
    (out_dir / SPLITS_NAME).write_text(json.dumps(splits, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    sha = store._sha256_bytes(raw)
    manifest = sim_run.simulate_library(records, out_dir, replicates or REPLICATES, workers, library_sha256=sha)
    counts = None
    if with_store:
        store_dir = Path(store_dir) if store_dir is not None else \
            (STORE_DEV_DIR if out_dir.resolve() == DEV_DIR.resolve() else out_dir / "store")
        counts = store.build_v2(out_dir, out_dir / LIBRARY_NAME, out_dir / SPLITS_NAME, store_dir, workers=workers)
    return {"records": records, "rejected": rejected, "splits": splits, "manifest": manifest, "store": counts,
            "store_dir": store_dir if with_store else None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the development scenario library, its simulation and store.")
    ap.add_argument("--out", type=Path, default=DEV_DIR)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--no-store", action="store_true")
    args = ap.parse_args(argv)
    out = build(args.out, workers=args.workers, with_store=not args.no_store)
    m = out["manifest"]
    print(f"wrote {args.out}: {len(out['records'])} of {len(VARIANTS)} variants kept "
          f"({', '.join(f'{k} {v}' for k, v in out['splits']['counts'].items())})")
    for r in out["rejected"]:
        print(f"  rejected {r['record']['scenario_id']}: layer {r['layer']}: {'; '.join(r['errors'])[:300]}")
    print(f"simulated {m['replicates_written']} replicates ({m['replicates_dropped']} dropped): {m['specimens']} "
          f"specimens, {m['scored']} scored, {m['scored_breaches']} scored breaches, {m['timing']['wall_seconds']} s")
    if out["store"]:
        print(f"store {out['store_dir']}: " + ", ".join(f"{k} {v}" for k, v in out["store"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
