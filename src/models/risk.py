"""The risk model: the chance a silent specimen misses its window (docs/architecture.md §5.2).

A specimen's time to reach the clock stop is a mixture: a normal lognormal, a stalled lognormal, and a
share that never arrives. Given that it has not arrived after tau minutes, the chance it never makes
the window W is

    1 - sum_k w_k * (F_k(W) - F_k(tau)) / (p_never + sum_k w_k * S_k(tau))

with F_k and S_k the lognormal CDF and survival. As the normal transit passes, the surviving mass is
more and more the stalled and lost specimens, so the risk rises by itself.

Under the receipt clock (every row of the policy table) that is the whole model, and a received
specimen is resolved. risk_unreceived(..., post=(median, sigma)) is the older shape that adds a step
after receipt, with the same one-minute midpoint sum as visuals/architecture.html, so the two can be
held to 1e-6 of each other. Its sum dips by up to 5e-6 over the first minutes, so compare it with a
tolerance, never for monotonicity. risk_after_receipt is the conditional form for a clock that stops
after receipt: given the step has already run for t - received_at minutes.
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


def ln_pdf(t: float, mu: float, sigma: float) -> float:
    if t <= 0:
        return 0.0
    return math.exp(-((math.log(t) - mu) ** 2) / (2 * sigma * sigma)) / (t * sigma * math.sqrt(2 * math.pi))


def p_not_received(t: float, mix: Mixture) -> float:
    return mix.p_never + sum(w * (1 - ln_cdf(t, mu, s)) for w, mu, s in mix.components)


def risk_unreceived(t: float, mix: Mixture, window: float, post: tuple[float, float] | None = None) -> float:
    """P(the window is missed | no receipt after t minutes). post=(median, sigma) adds a step after receipt."""
    if post is None:
        if t >= window:
            return 1.0
        arrives_in_time = sum(w * (ln_cdf(window, mu, s) - ln_cdf(t, mu, s)) for w, mu, s in mix.components)
        return 1 - arrives_in_time / p_not_received(t, mix)
    post_mu, post_sigma = math.log(post[0]), post[1]
    safe = 0.0
    for w, mu, s in mix.components:
        u = max(t, 0) + 0.5
        while u < window:
            safe += w * ln_pdf(u, mu, s) * ln_cdf(window - u, post_mu, post_sigma)
            u += 1
    return 1 - safe / p_not_received(t, mix)


def risk_after_receipt(t: float, received_at: float, window: float, post: tuple[float, float]) -> float:
    """P(the step after receipt finishes too late | it has run for t - received_at minutes and is not done)."""
    mu, sigma = math.log(post[0]), post[1]
    if t >= window:
        return 1.0
    return (1 - ln_cdf(window - received_at, mu, sigma)) / (1 - ln_cdf(t - received_at, mu, sigma))


def mixture_from(params: dict, group: str) -> Mixture:
    """The fitted mixture for one group of results/interval_params.json."""
    pooled, g = params["pooled"], params["groups"][group]
    w = pooled["weights"]
    return Mixture(((w["normal"], g["mu"], g["sigma"]), (w["stalled"], pooled["stalled_mu"], pooled["stalled_sigma"])),
                   w["never"])
