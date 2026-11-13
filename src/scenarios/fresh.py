"""Freeze, simulate and store the fresh test set, job T1 of docs/neural/preregistration-v2.md §5.

    python -m src.neural.pipeline freeze-fresh
    python -m src.neural.pipeline simulate --fresh
    python -m src.neural.pipeline store --fresh

Every accepted T1 scenario is re-validated (layers 1 to 5), simulated once for its plausibility KPIs and placed by its
32-number signature in the frozen library's own standardized space: the reference is fitted on the library as it is
(the O1 pilot for the components, every library scenario for the scaler, with the KPIs the library already records),
and delta is the library's. A fresh scenario is left out when it is out of distribution, a near duplicate of a library
scenario or of an earlier fresh one (same archetype, closer than delta), or within 1.5 delta of any library scenario,
since it would then share a family with train, validation or test scenarios. Every T1 item must carry the O2 prompt
hash the preregistration names. The rest are all scored, none dropped later.

The fresh scenarios take indices from 20001 up (the library uses 1 to 8,629 and the v1-world control 9000), so every
random stream SeedSequence([master_seed, index, replicate]) is new, and their split is `test_fresh`: replicate 0 is
history, 1 to 3 are scored. Writes datasets/scenarios-v2-fresh.jsonl.gz, its manifest and its splits; the simulation
goes to data/sim-fresh and the store to data/store-v2-fresh (observed and truth, as store v2). The set is frozen once:
a second freeze refuses.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np

from src.generator import config
from src.scenarios import freeze, prompt, runner, schema, signature

ROOT = Path(__file__).resolve().parents[2]
FRESH_LIBRARY = ROOT / "datasets" / "scenarios-v2-fresh.jsonl.gz"
FRESH_MANIFEST = ROOT / "datasets" / "scenarios-v2-fresh.manifest.json"
FRESH_SPLITS = ROOT / "datasets" / "scenarios-v2-fresh.splits.json"
FRESH_SIM_DIR = ROOT / "data" / "sim-fresh"
FRESH_STORE_DIR = ROOT / "data" / "store-v2-fresh"
FRESH_SPLIT = "test_fresh"
FRESH_JOBS = ("T1",)
FIRST_INDEX = 20001
PREREGISTERED_PROMPT_HASH = "5876a443517f72a4"   # O2's, the job that made `test` (preregistration-v2 §5)
REPLICATES = {FRESH_SPLIT: [0, 1, 2, 3]}


def reference_of_library(records: list[dict], delta: float) -> signature.Reference:
    """The signature reference fitted on the frozen library as it is, from the KPIs it records."""
    pilot = [r for r in records if r["meta"]["item_id"].startswith("O1-")]
    ref = signature.fit_reference([r["scenario"] for r in pilot], [r["meta"]["kpis"] for r in pilot],
                                  scaler_scenarios=[r["scenario"] for r in records],
                                  scaler_kpis=[r["meta"]["kpis"] for r in records])
    ref.delta = delta
    return ref


def placement(x_lib: np.ndarray, arch_lib: list[str], x_new: np.ndarray, arch_new: list[str], ids: list[str],
              delta: float, factor: float = signature.FAMILY_FACTOR) -> dict[str, str]:
    """Item id -> why it is left out, for fresh items placed among the library's: a near duplicate of a library
    scenario (same archetype, closer than delta), within factor * delta of any library scenario, or a near duplicate
    of an earlier fresh item (by item id)."""
    out: dict[str, str] = {}
    if len(x_new) and len(x_lib):
        from scipy.spatial.distance import cdist
        d = cdist(x_new, x_lib)
        same = np.asarray(arch_new)[:, None] == np.asarray(arch_lib)[None, :]
        for k, item in enumerate(ids):
            if ((d[k] < delta) & same[k]).any():
                out[item] = "near duplicate of a library scenario"
            elif (d[k] < factor * delta).any():
                out[item] = "would share a family with library scenarios"
    later = signature.later_duplicates(ids, x_new, arch_new, delta) if len(x_new) > 1 else set()
    for item in sorted(later):
        out.setdefault(item, "near duplicate of an earlier fresh scenario")
    return out


def freeze_fresh(*, paths: runner.Paths | None = None, workers: int = 16, library_path: Path = freeze.LIBRARY_PATH,
                 splits_path: Path = freeze.SPLITS_PATH, out_library: Path = FRESH_LIBRARY,
                 out_manifest: Path = FRESH_MANIFEST, out_splits: Path = FRESH_SPLITS, out=print) -> dict:
    paths = paths or runner.Paths()
    if out_library.exists():
        raise runner.JobError(f"{out_library.name} is already frozen; the fresh set is frozen once")
    loaded = config.load()
    records = freeze.read_library(library_path)
    lib_splits = json.loads(splits_path.read_text(encoding="utf-8"))
    lib_sha = hashlib.sha256(library_path.read_bytes()).hexdigest()
    if lib_splits["library_sha256"] != lib_sha:
        raise runner.JobError("the library's splits file was made for a different library")
    items, counts = freeze.accepted_items(FRESH_JOBS, paths)
    hashes = {prompt.prompt_hash(i["model"], i["effort"]) for i in items}
    if items and hashes != {PREREGISTERED_PROMPT_HASH}:
        raise runner.JobError(f"T1's prompt hashes {sorted(hashes)} are not the pre-registered "
                              f"{PREREGISTERED_PROMPT_HASH}")
    out(f"freeze-fresh: {len(items)} accepted T1 items against a library of {len(records)}")

    left_out: dict[str, str] = {}
    checks = freeze._parallel(freeze._revalidate, [(i["scenario"], i["brief"]) for i in items], workers)
    left_out |= {i["item_id"]: f"invalid: {why}" for i, why in zip(items, checks) if why is not None}
    left_out |= {i["item_id"]: "out of distribution" for i in items
                 if i["item_id"] not in left_out and freeze.is_ood(i["scenario"])}
    kept = [i for i in items if i["item_id"] not in left_out]
    kp = freeze.kpis_parallel([i["scenario"] for i in kept], workers)

    delta = float(lib_splits["delta"])
    ref = reference_of_library(records, delta)
    x_lib = ref.standardize(np.array([signature.signature(r["scenario"], r["meta"]["kpis"], ref) for r in records]))
    x_new = ref.standardize(np.array([signature.signature(i["scenario"], k, ref) for i, k in zip(kept, kp)])) \
        if kept else np.zeros((0, x_lib.shape[1]))
    ids = [i["item_id"] for i in kept]
    left_out |= placement(x_lib, [r["scenario"]["archetype"] for r in records], x_new,
                          [i["scenario"]["archetype"] for i in kept], ids, delta)
    keep = [k for k, i in enumerate(kept) if i["item_id"] not in left_out]
    labels = signature.families(x_new[keep], delta) if keep else np.zeros(0, dtype=int)

    fresh, scen_split = [], {}
    for n, (k, lab) in enumerate(zip(keep, labels)):
        i, index = kept[k], FIRST_INDEX + n
        meta = {"scenario_id": f"S{index:05d}", "scenario_index": index, "family_id": f"N{int(lab) + 1:04d}",
                "item_id": i["item_id"], "brief_id": i["brief"]["brief_id"], "job": i["job"],
                "stratum": i["brief"]["stratum"], "ood": False, "model": i["model"], "reasoning_effort": i["effort"],
                "tier": i["tier"], "prompt_hash": prompt.prompt_hash(i["model"], i["effort"]),
                "schema_hash": schema.schema_hash(), "policy_hash": loaded["policy_hash"], "brief": i["brief"],
                "display_name": f"Fresh scenario {index}, {i['scenario']['archetype'].replace('_', ' ')}",
                "kpis": kp[k], "source": "assumed", "sources": {s: "assumed" for s in freeze.TIMING_SECTIONS}}
        fresh.append({"meta": meta, "scenario": i["scenario"], "source": "assumed"})
        scen_split[meta["scenario_id"]] = FRESH_SPLIT
    data = freeze.write_gzip_jsonl(out_library, fresh)
    sha = hashlib.sha256(data).hexdigest()
    families = sorted({r["meta"]["family_id"] for r in fresh})
    splits = {"library_sha256": sha, "split": FRESH_SPLIT, "delta": delta, "first_index": FIRST_INDEX,
              "families": {f: FRESH_SPLIT for f in families}, "scenarios": dict(sorted(scen_split.items())),
              "counts": {FRESH_SPLIT: len(fresh)}, "family_counts": {FRESH_SPLIT: len(families)},
              "placed_against": {"library_sha256": lib_sha, "rule": "left out within 1.5 delta of any library "
                                 "scenario, or closer than delta to one of its archetype"}}
    out_splits.write_text(json.dumps(splits, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    manifest = build_manifest(fresh, sha, len(data), counts, paths, left_out, lib_sha, splits)
    out_manifest.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    out(f"froze {len(fresh)} fresh scenarios ({len(left_out)} left out: {dict(Counter(v.split(':')[0] for v in left_out.values()))})")
    return manifest


def build_manifest(fresh: list[dict], sha: str, size: int, counts: dict, paths: runner.Paths, left_out: dict,
                   lib_sha: str, splits: dict) -> dict:
    jobs_cfg = runner.load_jobs(paths.jobs_config)
    ledger = runner.ledger_mod.Ledger(paths.ledger, jobs=jobs_cfg)
    try:
        spent = sum(e["micros"] for e in ledger.entries("T1") if e["kind"] in ("settle", "adjust")) / 1e6
        account_spent = sum(e["micros"] for e in ledger.entries() if e["kind"] in ("settle", "adjust")
                            and e["account"] == "openai") / 1e6
    finally:
        ledger.close()
    metas = [r["meta"] for r in fresh]
    j = jobs_cfg["jobs"]["T1"]
    return {
        "file": FRESH_LIBRARY.name, "sha256": sha, "bytes": size, "scenarios": len(fresh),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "preregistration": "docs/neural/preregistration-v2.md",
        "split": FRESH_SPLIT, "first_index": FIRST_INDEX, "replicates": REPLICATES[FRESH_SPLIT],
        "placed_against_library_sha256": lib_sha,
        "job": {"name": "T1", "seed": j["seed"], "arms": j["arms"], "tier": j["tier"],
                "prompt_hashes": sorted({m["prompt_hash"] for m in metas}), "counts": counts.get("T1"),
                "spent_usd": round(spent, 6)},
        "accepted_by_stratum": dict(Counter(m["stratum"] for m in metas)),
        "accepted_by_archetype": dict(Counter(r["scenario"]["archetype"] for r in fresh)),
        "left_out": dict(Counter(v.split(":")[0] for v in left_out.values())),
        "left_out_items": dict(sorted(left_out.items())),
        "families": splits["family_counts"][FRESH_SPLIT],
        "ledger": {"openai_spent_usd": round(account_spent, 6),
                   "openai_cap_usd": float(jobs_cfg["accounts"]["openai"]["cap_usd"])},
        "policy_hash": config.load()["policy_hash"], "schema_hash": schema.schema_hash(),
        "clinical_review": "none", "notice": freeze.NOTICE,
    }


def simulation_inputs(records: list[dict] | None = None) -> list[dict]:
    records = freeze.read_library(FRESH_LIBRARY) if records is None else records
    return [r["scenario"] | {"scenario_id": r["meta"]["scenario_id"], "family_id": r["meta"]["family_id"],
                             "split": FRESH_SPLIT, "scenario_index": r["meta"]["scenario_index"]} for r in records]


def simulate_fresh(workers: int, out_dir: Path | None = None, out=print) -> int:
    from src.sim import run as sim_run
    records = freeze.read_library(FRESH_LIBRARY)
    if not records:
        raise runner.JobError("no fresh library; run `freeze-fresh` first")
    sha = hashlib.sha256(FRESH_LIBRARY.read_bytes()).hexdigest()
    if json.loads(FRESH_SPLITS.read_text(encoding="utf-8"))["library_sha256"] != sha:
        raise runner.JobError("the fresh splits file was made for a different fresh library")
    manifest = sim_run.simulate_library(simulation_inputs(records), out_dir or FRESH_SIM_DIR, REPLICATES, workers,
                                        library_sha256=sha)
    out(json.dumps({k: v for k, v in manifest.items() if not isinstance(v, (list, dict))}, indent=1))
    return 0


def store_fresh(workers: int | None = None, sim_dir: Path | None = None, store_dir: Path | None = None,
                out=print) -> int:
    from src.pipeline import store
    counts = store.build_v2(sim_dir or FRESH_SIM_DIR, FRESH_LIBRARY, FRESH_SPLITS, store_dir or FRESH_STORE_DIR,
                            workers=workers)
    out(json.dumps(counts, indent=1))
    return 0
