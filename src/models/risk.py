"""The risk model: the chance a silent specimen misses its window (docs/architecture.md §5.2).

A specimen's time to reach the clock stop is a mixture: a normal lognormal, a stalled lognormal, and a
share that never arrives. The clock stops at the receipt scan (every row of the policy table), so a
specimen misses its window exactly when it has not been received by W minutes. Given that it has not
arrived after tau minutes, the chance it misses is

    (p_never + sum_k w_k * S_k(W)) / (p_never + sum_k w_k * S_k(tau))

with S_k the lognormal survival. As the normal transit passes, the surviving mass is more and more the
stalled and lost specimens, so the risk rises by itself, and it never falls while the silence lasts.
Once the receipt scan arrives the outcome is known and the specimen is resolved: nothing is scored after
it. The same function is in visuals/architecture.html and the 3D exhibit, and a test holds them to 1e-6
of each other at every minute of the page's scenario.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.special import ndtr


@dataclass(frozen=True)
class Mixture:
    """Components are (weight, mu, sigma) with mu the mean of the log minutes (median = exp(mu)).
    The weights and p_never add up to 1."""
    components: tuple[tuple[float, float, float], ...]
    p_never: float

    def __post_init__(self):
        total = self.p_never + sum(w for w, _, _ in self.components)
        if abs(total - 1) > 1e-6:
            raise ValueError(f"weights and p_never add up to {total}, not 1")


def ln_cdf(t: float, mu: float, sigma: float) -> float:
    return 0.0 if t <= 0 else float(ndtr((math.log(t) - mu) / sigma))


def p_not_received(t: float, mix: Mixture) -> float:
    return mix.p_never + sum(w * (1 - ln_cdf(t, mu, s)) for w, mu, s in mix.components)


def risk_unreceived(t: float, mix: Mixture, window: float) -> float:
    """P(the window is missed | no receipt after t minutes). A passed window is certain."""
    if t >= window:
        return 1.0
    not_by_window = mix.p_never + sum(w * (1 - ln_cdf(window, mu, s)) for w, mu, s in mix.components)
    return not_by_window / p_not_received(t, mix)


def mixture_from(params: dict, group: str) -> Mixture:
    """The fitted mixture for one group of results/interval_params.json."""
    pooled, g = params["pooled"], params["groups"][group]
    w = pooled["weights"]
    return Mixture(((w["normal"], g["mu"], g["sigma"]), (w["stalled"], pooled["stalled_mu"], pooled["stalled_sigma"])),
                   w["never"])
