"""A tiny development store and an untrained TracerNet over it, for the explanation, command-line and export tests.

The store has one train scenario (the global analytic fit), one validation scenario (what the tests score), and one
test and one test_ood scenario (the lock); the network is rung C at small widths with seeded weights, so every input
path, the batch-mate attention included, is exercised in seconds on CPU."""

from __future__ import annotations

from pathlib import Path

import torch

from src.models.tracernet import features as F
from src.models.tracernet.net import TracerNet
from src.models.tracernet.predictor import Predictor
from src.neural import devlib, samplers as S
from src.pipeline import store

INDICES = (0, 7, 22, 45)        # dev-00 (train), dev-07 (val), dev-22 (test), dev-45 (test_ood)
REPLICATES = {"train": [0, 1], "val": [0, 1, 2], "test": [0, 1], "test_ood": [0, 1]}
NOTICE = "Synthetic data for tests: an untrained network over a simulated development store."


def build_store(root: Path) -> Path:
    return devlib.build(root, indices=INDICES, checked=False, workers=2, replicates=REPLICATES)["store_dir"]


def runs(store_dir: Path, split: str, role: str | None = None) -> list[int]:
    con = store.open_observed_v2(store_dir)
    try:
        sql = "select run from runs where split = ?" + (" and role = ?" if role else "") + " order by run"
        return [r for (r,) in con.execute(sql, [split, role] if role else [split]).fetchall()]
    finally:
        con.close()


def scenario(store_dir: Path, split: str) -> str:
    con = store.open_observed_v2(store_dir)
    try:
        return con.execute("select min(scenario_id) from runs where split = ?", [split]).fetchone()[0]
    finally:
        con.close()


def predictor(store_dir: Path, *, rung: str = "C", seed: int = 0, members: int = 1) -> Predictor:
    """An untrained predictor whose feature schema is fitted on the store's train ticks."""
    t = F.load_table(store_dir)
    schema = S.fit_feature_schema(t, samples=2000, workers=1, cache_root=Path(store_dir) / "cache")
    nets = []
    for i in range(members):
        torch.manual_seed(seed + i)
        nets.append(TracerNet(F.net_config(schema, rung=rung, width=16, temporal_channels=8, temporal_dim=16,
                                           attention_dim=16, heads=2, dropout=0.0)))
    return Predictor(nets, schema, {"temperature": 1.0, "isotonic": None}, manifest={"notice": NOTICE})
