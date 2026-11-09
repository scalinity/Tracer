"""Seeds, random streams and the lognormal used by the simulator (docs/neural/spec.md §5.6).

Every draw in a replicate comes from np.random.SeedSequence([MASTER_SEED, scenario_index,
replicate]), split into named streams (arrivals, attributes, dispatch, tube, station, receiving,
courier, processing, faults, jitter). Each stream is split again by a key: one key per unit for the
specimens it collects, one per courier route for its rounds, one per instrument for its downtimes.
So one part of the laboratory never shifts the draws of another.

Common random numbers. Each unit draws its arrival candidates from a fixed upper-bound rate that no
disruption can exceed and keeps them by thinning, and every per-specimen latent value is indexed by
the candidate's position in that unit's candidate stream. Adding or removing a disruption, or
changing a capacity, therefore leaves every specimen that exists in both worlds with identical
draws of its own, which is what makes paired comparisons between worlds meaningful.

Lognormals are given by a median and a 90th percentile, as in v1: mu = ln(median) and
sigma = ln(p90 / median) / z90. The simulator draws standard normals and maps them, so a latent
draw keeps its quantile when a parameter changes.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[2]
NEURAL_CONFIG_PATH = ROOT / "config" / "neural.yaml"

STREAMS = ("arrivals", "attributes", "dispatch", "tube", "station", "receiving", "courier", "processing",
           "faults", "jitter")
Z90 = 1.2815515655446004  # the standard normal's 90th percentile


@lru_cache(maxsize=1)
def master_seed(path: Path = NEURAL_CONFIG_PATH) -> int:
    with path.open(encoding="utf-8") as f:
        return int(yaml.safe_load(f)["simulation"]["master_seed"])


class Streams:
    """The named random streams of one replicate."""

    def __init__(self, scenario_index: int, replicate: int, seed: int | None = None):
        root = np.random.SeedSequence([master_seed() if seed is None else seed, scenario_index, replicate])
        self._children = dict(zip(STREAMS, root.spawn(len(STREAMS))))

    def get(self, name: str, *key: int) -> np.random.Generator:
        """A generator for stream `name`, split by `key` (for example a unit's index)."""
        child = self._children[name]
        return np.random.default_rng(np.random.SeedSequence(child.entropy, spawn_key=child.spawn_key + key))


def params(median: float, p90: float) -> tuple[float, float]:
    """(mu, sigma) of the lognormal with this median and 90th percentile."""
    return math.log(median), math.log(p90 / median) / Z90


def lognormal(mu_sigma: tuple[float, float], z: float) -> float:
    """The lognormal value at standard normal draw z."""
    return math.exp(mu_sigma[0] + mu_sigma[1] * z)
