"""The location filter: where a silent specimen probably is (docs/architecture.md §5.3).

A hidden semi-Markov filter on a one-minute grid. A specimen moves through legs, each with an explicit
lognormal duration and exits to the next leg, to an absorbing place (lost, left at the unit) or to the
receipt scan. The only observation after the collection scan is silence, so each minute the mass that
should already have left a leg moves on, and the whole distribution is conditioned on "not received":
mass that reaches the receipt scan is dropped and the rest is renormalized. Absence is the input, not a
special case.

filter_series is a port of `locationSeries` in visuals/architecture.html and is held to it by a parity
test: it keeps the page's one-minute grid, its 180-minute age cap and its hazard formula. Mass at the
cap is dropped, as the page drops it, which only matters for a leg that lasts three hours.

legs_for builds the legs for one specimen group and route from the fitted parameters. Nothing observes
the inside of the interval, so the sub-leg durations are not identifiable (§5.3): the proportional
split comes from the workflow's assumed leg times (config/config.yaml) and the absolute scale is fixed
by matching the mean of the sub-legs to the fitted normal transit. The mix of failures is the fitted
never-arrive and stalled shares, divided among places by the assumed fault rates. Every one of those
splits is an assumption, and the data was drawn with the same ones.

This module reads no labels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.special import ndtr

CAP = 180  # minutes in one leg that the filter tracks, as in the page
Z90 = 1.2815515655446004  # the 90th percentile of the standard normal
PLACES = ("at_unit", "hand_carry", "in_tube", "at_station", "stalled_at_station", "misrouted", "lost")
RECEIVED = "received"


@dataclass(frozen=True)
class Leg:
    """One place a specimen waits. `exits` is a tuple of (target, probability): target is the index of
    the next leg, the name of an absorbing place, or RECEIVED. The probabilities add up to 1."""
    name: str
    median: float
    sigma: float
    exits: tuple[tuple[int | str, float], ...]

    def __post_init__(self):
        total = sum(p for _, p in self.exits)
        if abs(total - 1) > 1e-9:
            raise ValueError(f"{self.name}: exit probabilities add up to {total}, not 1")
        if self.median <= 0 or self.sigma <= 0:
            raise ValueError(f"{self.name}: median and sigma must be positive")


def hazards(leg: Leg) -> np.ndarray:
    """P(leaving at the next minute | still here after a minutes), for a = 0 to CAP - 2."""
    age = np.arange(CAP, dtype=float)
    mu = math.log(leg.median)
    with np.errstate(divide="ignore"):
        cdf = np.where(age > 0, ndtr((np.log(np.maximum(age, 1e-300)) - mu) / leg.sigma), 0.0)
    surv = np.maximum(1 - cdf[:-1], 1e-9)
    return np.clip((cdf[1:] - cdf[:-1]) / surv, 0.0, 1.0)


def absorbing(legs: list[Leg]) -> list[str]:
    """The absorbing places the legs exit to, in the order they first appear."""
    names: list[str] = []
    for leg in legs:
        for target, _ in leg.exits:
            if isinstance(target, str) and target != RECEIVED and target not in names:
                names.append(target)
    return names


def filter_series(legs: list[Leg], max_t: int) -> tuple[list[str], np.ndarray]:
    """The posterior at every minute from 0 to max_t after collection, given silence. Columns are the
    legs in order, then the absorbing places; each row sums to 1."""
    sinks = absorbing(legs)
    n = len(legs)
    haz = [hazards(leg) for leg in legs]
    alpha = np.zeros((n, CAP))
    alpha[0, 0] = 1.0
    sink_mass = dict.fromkeys(sinks, 0.0)
    out = np.zeros((max_t + 1, n + len(sinks)))

    def snapshot(t: int) -> None:
        mass = np.concatenate([alpha.sum(axis=1), [sink_mass[s] for s in sinks]])
        out[t] = mass / mass.sum()

    snapshot(0)
    for t in range(1, max_t + 1):
        nxt = np.zeros((n, CAP))
        for i, leg in enumerate(legs):
            leaving = alpha[i, :-1] * haz[i]
            nxt[i, 1:] += alpha[i, :-1] - leaving
            gone = leaving.sum()
            for target, p in leg.exits:
                if target == RECEIVED:
                    continue
                if isinstance(target, str):
                    sink_mass[target] += gone * p
                else:
                    nxt[target, 0] += gone * p
        alpha = nxt
        snapshot(t)
    return [leg.name for leg in legs] + sinks, out


def place_series(names: list[str], posterior: np.ndarray) -> dict[str, np.ndarray]:
    """The posterior by place. A specimen left at its unit is still at the unit, so it joins `at_unit`."""
    col = {n: posterior[:, i] for i, n in enumerate(names)}
    places = {p: col.get(p, np.zeros(len(posterior))) for p in PLACES}
    places["at_unit"] = places["at_unit"] + col.get("left_at_unit", 0.0)
    return places


# ---- the legs for the dataset's workflow ----

def sigma_of(median: float, p90: float) -> float:
    return math.log(p90 / median) / Z90


def mean_of(median: float, sigma: float) -> float:
    return median * math.exp(sigma * sigma / 2)


def legs_for(params: dict, gen: dict, group: str, route: str, tube_share: float) -> list[Leg]:
    """The legs of one group and route. `params` is results/interval_params.json, `gen` the generator
    config (its assumed leg times and fault rates), `tube_share` the share of the fit runs' specimens
    sent by tube. The posterior is over legs: at_unit, then in_tube, at_station, stalled_at_station and
    misrouted for a tube specimen, or hand_carry for one carried by hand."""
    if route not in ("tube", "hand_carry"):
        raise ValueError(f"unknown route {route!r}")
    base = {k: (v["median_min"], sigma_of(v["median_min"], v["p90_min"])) for k, v in gen["legs"].items()}
    chain = {"tube": ("dispatch_wait", "tube_transit", "station_dwell"), "hand_carry": ("dispatch_wait", "hand_carry_transit")}
    chain_mean = {r: sum(mean_of(*base[leg]) for leg in legs) for r, legs in chain.items()}
    g, pooled = params["groups"][group], params["pooled"]
    # one scale for the group: the route-weighted mean of the chains matches the fitted normal transit
    scale = math.exp(g["mu"] + g["sigma"] ** 2 / 2) / (tube_share * chain_mean["tube"] + (1 - tube_share) * chain_mean["hand_carry"])
    med = {k: base[k][0] * scale for k in base}

    w, f = pooled["weights"], gen["faults"]
    never_lost = f["lost_in_tube"]["rate"] / (f["lost_in_tube"]["rate"] + f["not_dispatched"]["rate"])
    late_stalled = f["stalled_at_station"]["rate"] / (f["stalled_at_station"]["rate"] + f["misrouted"]["rate"])
    q_left = w["never"] * (1 - never_lost)  # not dispatched, whatever the route
    q_lost = w["never"] * never_lost / tube_share  # lost in the tube, tube specimens only
    q_mis = w["stalled"] * (1 - late_stalled) / tube_share
    q_stall = w["stalled"] * late_stalled / tube_share / (1 - q_lost - q_mis)

    unit = Leg("at_unit", med["dispatch_wait"], base["dispatch_wait"][1],
               ((1, 1 - q_left), ("left_at_unit", q_left)))
    if route == "hand_carry":
        return [unit, Leg("hand_carry", med["hand_carry_transit"], base["hand_carry_transit"][1], ((RECEIVED, 1.0),))]
    # a stalled or misrouted specimen's total is the fitted stalled lognormal; the time it spends held up
    # is what is left after the legs it passes through on the way
    sigma_late = pooled["stalled_sigma"]
    held = max(mean_of(math.exp(pooled["stalled_mu"]), sigma_late) - sum(mean_of(med[k], base[k][1]) for k in chain["tube"]), 10.0)
    median_late = held / math.exp(sigma_late ** 2 / 2)
    return [unit,
            Leg("in_tube", med["tube_transit"], base["tube_transit"][1],
                ((2, 1 - q_lost - q_mis), ("lost", q_lost), (4, q_mis))),
            Leg("at_station", med["station_dwell"], base["station_dwell"][1],
                ((RECEIVED, 1 - q_stall), (3, q_stall))),
            Leg("stalled_at_station", median_late, sigma_late, ((RECEIVED, 1.0),)),
            Leg("misrouted", median_late, sigma_late, ((2, 1.0),))]
