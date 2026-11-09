"""Freeze the accepted scenarios into the v2 library, its manifest and its splits (docs/neural/spec.md §4.11, §6.2).

    python -m src.neural.pipeline freeze --jobs O1,O2

Every accepted scenario of the given jobs is re-validated (layers 1 to 5), simulated once for its plausibility KPIs,
placed by its 32-number signature, and checked against near duplicates (layer 6): of two scenarios of one archetype
closer than delta, the later by item id is left out. Delta is half the median nearest-neighbor distance of the
accepted library itself. The spec fits delta on the pilot, but the library is about 85 times as dense as the pilot,
and scenarios fill a space of about three effective dimensions (the median nearest-neighbor distance falls by about
0.81 each time the pilot sample doubles), so a pilot delta would call about half the library duplicates and chain the
rest into one family. Fitted on the library, delta keeps the spec's meaning: closer than half the typical spacing.

Families are the union-find components of pairs closer than 1.5 delta, whatever the archetype. Splits are made by
family: every family holding a `multi_site_system` scenario or a `scanner_downtime` disruption goes to `test_ood`
(the pre-registered out-of-distribution set); the rest are ranked within each (archetype, stratum) cell by a seeded
hash of their id and split 70% train, 15% validation, 15% test. A later freeze (after round 2) keeps every existing
scenario, id, family and split; its new families go 85% to train and 15% to validation, never to a test set, and a
new scenario that would join a test family or bridge two families of different splits is left out.

Writes `datasets/scenarios-v2.jsonl.gz` (one sorted-key JSON line per scenario, gzip with mtime 0),
`datasets/scenarios-v2.manifest.json` and `datasets/scenarios-v2.splits.json`. Every value in the library is an
assumption written by a language model for a synthetic simulator; nothing in it had clinical review.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml

from src.generator import config
from src.scenarios import planner, prompt, random_baseline, runner, schema, signature, validate

ROOT = Path(__file__).resolve().parents[2]
LIBRARY_PATH = ROOT / "datasets" / "scenarios-v2.jsonl.gz"
MANIFEST_PATH = ROOT / "datasets" / "scenarios-v2.manifest.json"
SPLITS_PATH = ROOT / "datasets" / "scenarios-v2.splits.json"
SPLIT_SALT = "tracer-v2-splits-20261108"
SHARES = (("train", 0.70), ("val", 0.15), ("test", 0.15))
ROUND2_TRAIN_SHARE = 0.85
FIRST_FREEZE_JOBS = ("O1", "O2")
ROUND2_JOBS = ("O3", "O3b")
RANDOM_REFERENCE_N = 2000
VENDI_SAMPLE = 2000
TIMING_SECTIONS = ("units", "tube_system", "couriers", "receiving", "processing", "disruptions")
NOTICE = ("Every parameter in this library is an assumption written by a language model for a synthetic laboratory "
          "simulator. No value comes from a real laboratory, and nothing in it had clinical review.")


def is_ood(scenario: dict) -> bool:
    """The pre-registered out-of-distribution rule (spec §6.2)."""
    return scenario["archetype"] == "multi_site_system" or any(
        d["type"] == "scanner_downtime" for d in scenario["disruptions"])


def unit_hash(text: str) -> float:
    return int(hashlib.sha256(f"{SPLIT_SALT}:{text}".encode()).hexdigest()[:15], 16) / 16 ** 15


def _kpis(scenario: dict) -> dict:
    from src.sim import kpi
    return kpi.kpis(scenario)


def _revalidate(args: tuple[dict, dict]) -> str | None:
    """None when layers 1 to 5 pass, else the first failing layer and its errors."""
    from src.sim import kpi
    scenario, brief = args
    res = validate.validate(scenario, brief, plausibility=kpi.check)
    return None if res.ok else f"{res.layer}: {'; '.join(res.errors)[:200]}"


def _parallel(fn, items: list, workers: int) -> list:
    """fn over items in order, in worker processes (run from a module or script: spawn re-imports __main__)."""
    if workers <= 1:
        return [fn(x) for x in items]
    with ProcessPoolExecutor(workers) as pool:
        return list(pool.map(fn, items, chunksize=8))


def kpis_parallel(scenarios: list[dict], workers: int) -> list[dict]:
    return _parallel(_kpis, scenarios, workers)


def accepted_items(jobs: tuple[str, ...], paths: runner.Paths) -> tuple[list[dict], dict]:
    """The accepted items of the jobs, oldest item id first, with every count the manifest reports."""
    store = runner.ItemStore(paths.items, paths.items_log)
    cache = runner.ResponseCache(paths.responses)
    out, counts = [], {}
    for job in jobs:
        items = store.items(job)
        c = Counter(i["state"] for i in items)
        briefs = {i["brief_id"] for i in items}
        acc = [i for i in items if i["state"] == "accepted"]
        counts[job] = {"requests": sum(c[s] for s in c if s not in ("planned", "reserved", "refused")),
                       "briefs": len(briefs), "accepted": len(acc),
                       "repaired": sum(i["attempt"] > 1 for i in acc), "dropped": c.get("dropped", 0),
                       "refused": c.get("refused", 0), "unknown": c.get("unknown", 0),
                       "rejections_by_layer": dict(Counter(str(i.get("layer")) for i in items
                                                           if i["state"] in ("repair_planned", "dropped")))}
        for i in acc:
            scenario = runner.parse_response(json.loads(cache.read(i["request_hash"]))).scenario
            out.append({"item_id": i["item_id"], "job": job, "brief": i["brief"], "model": i["model"],
                        "effort": i["effort"], "tier": i.get("tier"), "scenario": scenario})
    store.close()
    return sorted(out, key=lambda r: r["item_id"]), counts


def read_library(path: Path = LIBRARY_PATH) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in gzip.decompress(path.read_bytes()).decode("utf-8").splitlines() if line]


def write_gzip_jsonl(path: Path, records: list[dict]) -> bytes:
    text = "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in records)
    data = gzip.compress(text.encode("utf-8"), mtime=0)
    path.write_bytes(data)
    return data


def assign_first_splits(families: dict[str, list[dict]]) -> dict[str, str]:
    """Family id -> split for a first freeze: test_ood by the rule, the rest 70/15/15 by hash rank within each
    (archetype, stratum) cell of the family's first member."""
    split, cells = {}, {}
    for fid, members in families.items():
        if any(is_ood(m["scenario"]) for m in members):
            split[fid] = "test_ood"
            continue
        first = members[0]
        cells.setdefault((first["scenario"]["archetype"], first["brief"]["stratum"]), []).append(fid)
    for cell, fids in sorted(cells.items()):
        ranked = sorted(fids, key=unit_hash)
        n = len(ranked)
        cut_train = round(n * SHARES[0][1])
        cut_val = cut_train + round(n * SHARES[1][1])
        for k, fid in enumerate(ranked):
            split[fid] = "train" if k < cut_train else "val" if k < cut_val else "test"
    return split


def freeze(jobs: tuple[str, ...], *, paths: runner.Paths | None = None, workers: int = 16,
           library_path: Path = LIBRARY_PATH, manifest_path: Path = MANIFEST_PATH, splits_path: Path = SPLITS_PATH,
           random_n: int = RANDOM_REFERENCE_N, out=print) -> dict:
    paths = paths or runner.Paths()
    loaded = config.load()
    existing = read_library(library_path)
    old_splits = json.loads(splits_path.read_text()) if existing and splits_path.exists() else None
    known = {r["meta"]["item_id"] for r in existing}
    items, counts = accepted_items(jobs, paths)
    new = [i for i in items if i["item_id"] not in known]
    round2 = bool(existing)
    if round2 and any(i["job"] not in ROUND2_JOBS for i in new):
        raise runner.JobError("a later freeze may only add round-2 (O3, O3b) scenarios")
    out(f"freeze: {len(existing)} in the library, {len(new)} new accepted items from {', '.join(jobs)}")

    # Re-validate layers 1 to 5 (the plausibility layer simulates the full week), keeping the first failure.
    checks = _parallel(_revalidate, [(i["scenario"], i["brief"]) for i in new], workers)
    invalid = {i["item_id"]: why for i, why in zip(new, checks) if why is not None}
    new = [i for i in new if i["item_id"] not in invalid]

    pool = [{"item_id": r["meta"]["item_id"], "scenario": r["scenario"], "brief": {"stratum": r["meta"]["stratum"]},
             "existing": r} for r in existing] + new
    kp = kpis_parallel([p["scenario"] for p in pool], workers)
    pilot = [k for p, k in zip(pool, kp) if p["item_id"].startswith("O1-")]
    pilot_sc = [p["scenario"] for p in pool if p["item_id"].startswith("O1-")]
    ref = signature.fit_reference(pilot_sc, pilot, scaler_scenarios=[p["scenario"] for p in pool], scaler_kpis=kp)
    x = ref.standardize(np.array([signature.signature(p["scenario"], k, ref) for p, k in zip(pool, kp)]))
    if round2:
        delta = old_splits["delta"]
    else:
        delta = 0.5 * float(np.median(signature.nearest_distances(x)))
    ref.delta = delta
    archetypes = [p["scenario"]["archetype"] for p in pool]
    ids = [p["item_id"] for p in pool]
    duplicates = signature.later_duplicates(ids, x, archetypes, delta) - known
    keep = [k for k, p in enumerate(pool) if p["item_id"] not in duplicates]
    pool, kp, x = [pool[k] for k in keep], [kp[k] for k in keep], x[keep]
    labels = signature.families(x, delta)

    # Ids: existing records keep theirs; new scenarios count on, in item-id order.
    next_index = 1 + max((r["meta"]["scenario_index"] for r in existing), default=0)
    fam_of_old = {r["meta"]["item_id"]: r["meta"]["family_id"] for r in existing}
    groups: dict[int, list[int]] = {}
    for k, lab in enumerate(labels):
        groups.setdefault(int(lab), []).append(k)
    split_of_family = dict(old_splits["families"]) if old_splits else {}
    next_family = 1 + max((int(f[1:]) for f in split_of_family), default=0)
    left_out = dict(invalid)
    family_of: dict[int, str] = {}
    for lab, members in sorted(groups.items(), key=lambda g: min(pool[k]["item_id"] for k in g[1])):
        old_fams = {fam_of_old[pool[k]["item_id"]] for k in members if pool[k]["item_id"] in fam_of_old}
        if len(old_fams) > 1 and len({split_of_family[f] for f in old_fams}) > 1:
            for k in members:
                if pool[k]["item_id"] not in fam_of_old:
                    left_out[pool[k]["item_id"]] = "would bridge families of different splits"
            continue
        if old_fams:
            fid = sorted(old_fams)[0]
            if split_of_family[fid] not in ("train", "val"):
                for k in members:
                    if pool[k]["item_id"] not in fam_of_old:
                        left_out[pool[k]["item_id"]] = f"would join a {split_of_family[fid]} family"
                continue
        else:
            fid = f"F{next_family:04d}"
            next_family += 1
        for k in members:
            family_of[k] = fid

    records = list(existing)
    new_members: dict[str, list[dict]] = {}
    for k, p in enumerate(pool):
        if "existing" in p or k not in family_of:
            continue
        sid_index = next_index
        next_index += 1
        meta = {"scenario_id": f"S{sid_index:04d}", "scenario_index": sid_index, "family_id": family_of[k],
                "item_id": p["item_id"], "brief_id": p["brief"]["brief_id"], "job": p["job"],
                "stratum": p["brief"]["stratum"], "ood": is_ood(p["scenario"]), "model": p["model"],
                "reasoning_effort": p["effort"], "tier": p["tier"],
                "prompt_hash": prompt.prompt_hash(p["model"], p["effort"]), "schema_hash": schema.schema_hash(),
                "policy_hash": loaded["policy_hash"], "brief": p["brief"],
                "display_name": f"Scenario {sid_index:04d}, {p['scenario']['archetype'].replace('_', ' ')}",
                "kpis": kp[k], "source": "assumed",
                "sources": {s: "assumed" for s in TIMING_SECTIONS}}
        rec = {"meta": meta, "scenario": p["scenario"], "source": "assumed"}
        records.append(rec)
        new_members.setdefault(family_of[k], []).append({"scenario": p["scenario"], "brief": p["brief"],
                                                         "scenario_id": meta["scenario_id"]})
    if round2:
        for fid, members in new_members.items():
            if fid not in split_of_family:
                split_of_family[fid] = "train" if unit_hash(fid) < ROUND2_TRAIN_SHARE else "val"
    else:
        split_of_family.update(assign_first_splits(new_members))
    if next_index > 9000:
        raise runner.JobError("scenario indices must stay below 9000 (9000 and 9001 are the v1 world's)")

    records.sort(key=lambda r: r["meta"]["scenario_index"])
    data = write_gzip_jsonl(library_path, records)
    sha = hashlib.sha256(data).hexdigest()
    scen_split = {r["meta"]["scenario_id"]: split_of_family[r["meta"]["family_id"]] for r in records}
    splits = {"library_sha256": sha, "salt": SPLIT_SALT, "delta": delta,
              "ood_rule": "archetype multi_site_system, or any scanner_downtime disruption, goes to test_ood",
              "shares": dict(SHARES), "round2_train_share": ROUND2_TRAIN_SHARE,
              "families": dict(sorted(split_of_family.items())), "scenarios": dict(sorted(scen_split.items())),
              "counts": dict(Counter(scen_split.values())),
              "family_counts": dict(Counter(split_of_family.values()))}
    splits_path.write_text(json.dumps(splits, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    manifest = build_manifest(records, sha, len(data), counts, jobs, paths, loaded, ref, x, kp, splits,
                              duplicates, left_out, random_n, workers)
    manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    out(f"froze {len(records)} scenarios ({len(duplicates)} near duplicates and {len(left_out)} others left out); "
        f"splits {splits['counts']}; {len(split_of_family)} families, largest "
        f"{max(Counter(r['meta']['family_id'] for r in records).values())}; delta {delta:.3f}")
    return manifest


def build_manifest(records, sha, size, counts, jobs, paths, loaded, ref, x, kp, splits, duplicates, left_out,
                   random_n, workers) -> dict:
    jobs_cfg = runner.load_jobs(paths.jobs_config)
    table = yaml.safe_load(paths.prices.read_text(encoding="utf-8"))
    ledger = runner.ledger_mod.Ledger(paths.ledger, jobs=jobs_cfg)
    entries = ledger.entries()
    spent = Counter()
    for e in entries:
        if e["kind"] in ("settle", "adjust") and e["account"] == "openai":
            spent[e["job"]] += e["micros"] / 1e6 if e.get("micros") is not None else float(e["usd"])
    metas = [r["meta"] for r in records]
    strata = [m["stratum"] for m in metas]
    planned = []
    for job in jobs:
        brief_path = paths.briefs(job)
        if brief_path.exists():
            planned += [json.loads(line) for line in brief_path.read_text().splitlines() if line]
    random_sc = [random_baseline.random_scenario(seed) for seed in range(random_n)]
    random_x = ref.standardize(np.array([signature.signature(s, k, ref) for s, k in
                                         zip(random_sc, kpis_parallel(random_sc, workers))])) if random_n else None
    rng = np.random.default_rng(0)
    sample = lambda a: a if len(a) <= VENDI_SAMPLE else a[rng.choice(len(a), VENDI_SAMPLE, replace=False)]
    by_job_model = Counter((m["job"], m["model"]) for m in metas)
    return {
        "file": LIBRARY_PATH.name, "sha256": sha, "bytes": size, "scenarios": len(records),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "policy_hash": loaded["policy_hash"], "schema_hash": schema.schema_hash(),
        "planner": {"config_hash": hashlib.sha256(planner.CONFIG_PATH.read_bytes()).hexdigest()[:16]
                    if hasattr(planner, "CONFIG_PATH") else None,
                    "seeds": {j: jobs_cfg["jobs"][j]["seed"] for j in jobs}},
        "jobs": {j: {"arms": jobs_cfg["jobs"][j]["arms"], "tier": jobs_cfg["jobs"][j]["tier"],
                     "prompt_hashes": sorted({m["prompt_hash"] for m in metas if m["job"] == j}),
                     "counts": counts.get(j), "spent_usd": round(spent.get(j, 0.0), 6)} for j in jobs},
        "accepted_by_job_and_model": {f"{j} {m}": n for (j, m), n in sorted(by_job_model.items())},
        "accepted_by_stratum": dict(Counter(strata)), "accepted_by_archetype": dict(Counter(
            r["scenario"]["archetype"] for r in records)),
        "near_duplicates": {"delta": splits["delta"], "left_out": len(duplicates),
                            "rule": "same archetype, standardized distance below delta; the later item id is left out",
                            "delta_fitted_on": "the accepted library (half its median nearest-neighbor distance)"},
        "left_out_other": dict(Counter(v.split(":")[0] for v in left_out.values())),
        "coverage": signature.coverage([{"brief_id": r["meta"]["brief_id"]} for r in records], planned)
        if planned else None,
        "dispersion": {"library": signature.dispersion(sample(x), None),
                       "library_by_stratum": {s: signature.dispersion(sample(x[[k for k, t in enumerate(strata)
                                                                                if t == s]]), None)
                                              for s in sorted(set(strata)) if strata.count(s) > 2},
                       "code_random": None if random_x is None else signature.dispersion(sample(random_x), None),
                       "code_random_n": random_n},
        "ledger": {"spent_usd_by_job": {j: round(v, 6) for j, v in sorted(spent.items())},
                   "spent_usd_total": round(sum(spent.values()), 6), "cap_usd": 76.43},
        "prices_verified_at": table.get("verified_at"),
        "splits": {"file": SPLITS_PATH.name, "counts": splits["counts"], "family_counts": splits["family_counts"],
                   "families": len(splits["families"])},
        "ood_definition": splits["ood_rule"],
        "clinical_review": "none",
        "notice": NOTICE,
    }


# Replicate 0 is every scenario's history; train scores replicates 1 and 2, every other split 1 to 3 (spec §6.1).
REPLICATES = {"train": [0, 1, 2], "val": [0, 1, 2, 3], "test": [0, 1, 2, 3], "test_ood": [0, 1, 2, 3]}


def simulation_inputs(records: list[dict] | None = None, splits: dict | None = None) -> list[dict]:
    """The library as src.sim.run.simulate_library takes it: each configuration with its id, family, split and
    index beside it. Only the simulator and the store read the library."""
    records = read_library() if records is None else records
    splits = json.loads(SPLITS_PATH.read_text()) if splits is None else splits
    out = []
    for r in records:
        m = r["meta"]
        out.append(r["scenario"] | {"scenario_id": m["scenario_id"], "family_id": m["family_id"],
                                    "split": splits["scenarios"][m["scenario_id"]],
                                    "scenario_index": m["scenario_index"]})
    return out


SIM_DIR = ROOT / "data" / "sim"


def simulate(workers: int, out_dir: Path | None = None, out=print) -> int:
    """Simulate every frozen scenario's replicates (history replicate 0; train scores 1 and 2, the other splits
    1 to 3) into data/sim/ with its manifest. Refuses a splits file made for another library."""
    import time

    from src.sim import run as sim_run

    records = read_library()
    if not records:
        raise runner.JobError("no frozen library; run `freeze` first")
    lib_sha = hashlib.sha256(LIBRARY_PATH.read_bytes()).hexdigest()
    splits = json.loads(SPLITS_PATH.read_text())
    if splits["library_sha256"] != lib_sha:
        raise runner.JobError("the splits file was made for a different library; refreeze")
    target = out_dir or SIM_DIR
    t0 = time.monotonic()
    manifest = sim_run.simulate_library(simulation_inputs(records, splits), target, REPLICATES, workers,
                                        library_sha256=lib_sha)
    out(f"simulated {len(records)} scenarios into {target} in {time.monotonic() - t0:.0f} s")
    out(json.dumps({k: v for k, v in manifest.items() if not isinstance(v, (list, dict))}, indent=1))
    return 0
