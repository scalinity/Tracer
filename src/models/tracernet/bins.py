"""TracerNet's output classes: absolute minutes from collection to the receipt scan (docs/neural/spec.md §7.1).

Bins are 5 minutes wide up to 240, 30 up to 1,440, 120 up to 4,320 and 240 up to 7,200, then `beyond`
(no receipt within 7,200 minutes): 125 classes. Each finite bin is closed on the right, (lower, upper],
because a specimen misses its window when its receipt comes more than `window_min` minutes after
collection, so a receipt at exactly the window is on time. Every distinct `window_min` in
config/breach_policy.yaml is an edge, so the miss probability is an exact sum of whole bins; a new window
value adds an edge (one more class, and a retrain).

At tick t (minutes since collection, no receipt yet) the bins that end at or before t cannot hold the
receipt and are masked. Read off the distribution over the remaining bins:

    p_never   = mass in `beyond`
    p_late    = mass in the finite bins that start at or after the window
    p_miss    = p_late + p_never
    p_on_time = 1 - p_miss

The probability helpers take a numpy array or a torch tensor and return the same kind, batched over the
leading dimensions: `probs` and `logits` are [..., n_classes], `tick` and `window` are [...]. Arrival
times (`cdf`, `arrival_quantiles`) are numpy only and spread each bin's mass uniformly over the part of
the bin after the tick.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch

from src.generator import config

STEPS = ((240, 5), (1440, 30), (4320, 120), (7200, 240))  # (up to this minute, bin width)
MASK_LOGIT = -1e9  # logit of an impossible bin: its softmax mass underflows to exactly 0 in float32


def default_edges() -> list[int]:
    """The finite bin edges of the steps: 0, 5, ..., 240, 270, ..., 1,440, 1,560, ..., 7,200."""
    edges, start = [0], 0
    for stop, width in STEPS:
        edges += list(range(start + width, stop + 1, width))
        start = stop
    return edges


def _kind(like, values):
    """`values` as the same kind of array as `like`: a float tensor on its device, or a float64 array."""
    if isinstance(like, torch.Tensor):
        dtype = like.dtype if like.is_floating_point() else torch.float32
        return torch.as_tensor(values, dtype=dtype, device=like.device)
    return np.asarray(values, dtype=float)


def _numpy(x) -> np.ndarray:
    if isinstance(x, torch.Tensor):
        return x.detach().cpu().double().numpy()
    return np.asarray(x, dtype=float)


@dataclass(frozen=True)
class Bins:
    """`edges` are the finite edges, 0 first and the horizon last; class k < beyond is the bin
    (edges[k], edges[k + 1]] and the last class is `beyond`. `windows` are the policy windows the edges
    were built to hold; each must be an edge."""
    edges: tuple
    windows: tuple = ()

    def __post_init__(self):
        e = self.edges
        if e[0] != 0 or any(b <= a for a, b in zip(e, e[1:])):
            raise ValueError("bin edges must start at 0 and increase")
        missing = sorted(set(self.windows) - set(e))
        if missing:
            raise ValueError(f"windows {missing} are not bin edges, so p_miss would not be exact")

    @property
    def n_classes(self) -> int:
        return len(self.edges)

    @property
    def beyond(self) -> int:
        return len(self.edges) - 1

    @property
    def horizon(self):
        return self.edges[-1]

    @property
    def lower(self) -> np.ndarray:
        """[K] start of each class; `beyond` starts at the horizon."""
        return np.asarray(self.edges, dtype=float)

    @property
    def upper(self) -> np.ndarray:
        """[K] end of each class; `beyond` never ends."""
        return np.append(np.asarray(self.edges[1:], dtype=float), np.inf)

    def index(self, receipt_min) -> np.ndarray:
        """The class of each receipt time in minutes since collection. No receipt (None, NaN or inf) and a
        receipt after the horizon are `beyond`; a receipt at or before 0 goes to the first bin."""
        r = np.asarray(receipt_min, dtype=float)
        idx = np.clip(np.searchsorted(self.lower, r, side="left") - 1, 0, self.beyond)
        return np.where(np.isfinite(r), idx, self.beyond)

    def missed(self, cls, window):
        """True where class `cls` lies after the window: a miss, exactly, since the window is an edge."""
        return _kind(window, self.lower)[cls] >= _kind(window, window)

    def impossible(self, tick):
        """[..., K] True for the bins that end at or before the tick: they cannot hold a receipt that has
        not come by then."""
        return _kind(tick, self.upper) <= _kind(tick, tick)[..., None]

    def mask_logits(self, logits: torch.Tensor, tick: torch.Tensor) -> torch.Tensor:
        """The logits with impossible bins set to MASK_LOGIT, so the softmax is conditional on no receipt
        by the tick."""
        return logits.masked_fill(self.impossible(tick), MASK_LOGIT)

    def _late(self, like, window):
        late = _kind(like, self.lower) >= _kind(like, window)[..., None]
        late[..., -1] = False
        return late

    def p_never(self, probs):
        return probs[..., -1]

    def p_late(self, probs, window):
        return (probs * self._late(probs, window)).sum(-1)

    def p_miss(self, probs, window):
        return self.p_late(probs, window) + self.p_never(probs)

    def derived(self, probs, window) -> dict:
        late, never = self.p_late(probs, window), self.p_never(probs)
        miss = late + never
        return {"p_miss": miss, "p_late": late, "p_never": never, "p_on_time": 1 - miss}

    def logit_miss(self, logits: torch.Tensor, window: torch.Tensor) -> torch.Tensor:
        """log(p_miss / p_on_time) straight from the (masked) logits, finite even when one side is empty."""
        miss = _kind(logits, self.lower) >= _kind(logits, window)[..., None]
        return (torch.logsumexp(logits.masked_fill(~miss, MASK_LOGIT), -1)
                - torch.logsumexp(logits.masked_fill(miss, MASK_LOGIT), -1))

    def _span(self, tick):
        """[..., K] start and end of the part of each bin after the tick."""
        t = _numpy(tick)[..., None]
        lo = np.maximum(self.lower, t)
        return lo, np.broadcast_to(self.upper, lo.shape)

    def cdf(self, probs, tick, x) -> np.ndarray:
        """P(receipt within x minutes of collection | no receipt by the tick). `beyond` never counts, so
        the CDF rises to 1 - p_never as x passes the horizon."""
        p = _numpy(probs)
        lo, hi = self._span(tick)
        x = _numpy(x)[..., None]
        frac = np.clip((x - lo) / np.maximum(hi - lo, 1e-12), 0.0, 1.0)
        frac[..., -1] = 0.0
        return (p * frac).sum(-1)

    def arrival_quantiles(self, probs, tick, qs=(0.5, 0.9)) -> np.ndarray:
        """[..., len(qs)] minutes since collection by which a share q of the arrivals have come, given no
        receipt by the tick and given the specimen arrives (`beyond` left out). NaN where no finite bin
        holds mass."""
        p = _numpy(probs)[..., :-1]
        total = p.sum(-1)
        with np.errstate(invalid="ignore", divide="ignore"):
            cond = np.cumsum(p, -1) / total[..., None]
        lo, hi = (a[..., :-1] for a in self._span(tick))
        out = []
        for q in qs:
            k = np.minimum((cond < q).sum(-1), p.shape[-1] - 1)[..., None]
            c_hi = np.take_along_axis(cond, k, -1)[..., 0]
            c_lo = np.where(k[..., 0] > 0, np.take_along_axis(cond, np.maximum(k - 1, 0), -1)[..., 0], 0.0)
            with np.errstate(invalid="ignore", divide="ignore"):
                frac = np.clip((q - c_lo) / (c_hi - c_lo), 0.0, 1.0)
            a, b = np.take_along_axis(lo, k, -1)[..., 0], np.take_along_axis(hi, k, -1)[..., 0]
            out.append(np.where(total > 0, a + frac * (b - a), np.nan))
        return np.stack(out, -1)

    def to_json(self) -> dict:
        """The artifact's bins.json."""
        return {"unit": "minutes since collection", "closed": "right", "steps": [list(s) for s in STEPS],
                "edges": list(self.edges), "n_classes": self.n_classes, "beyond": self.beyond,
                "windows": list(self.windows)}

    @classmethod
    def from_json(cls, doc: dict) -> "Bins":
        return cls(tuple(doc["edges"]), tuple(doc.get("windows", ())))


def build(policy: dict | None = None) -> Bins:
    """The bins for a breach policy (`config.load()["policy"]` when none is given): the default steps plus
    an edge at every distinct window that is not one already."""
    if policy is None:
        policy = config.load()["policy"]
    windows = sorted({p["window_min"] for p in policy["policies"].values()})
    bins = Bins(tuple(sorted(set(default_edges()) | set(windows))), tuple(windows))
    assert set(windows) <= set(bins.edges)
    return bins
