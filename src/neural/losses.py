"""TracerNet's training loss (docs/neural/spec.md §7.5).

    loss = NLL of the distribution + lam * BCE of the derived p_miss + eta * DeepHit ranking term

- NLL: -log of the mass in the observed receipt class; for a censored case (`censor_min` finite), -log
  of the mass in the bins ending after the censoring time. Censoring is implemented and tested, though
  scenario data has none.
- BCE: binary cross-entropy of p_miss against the breach label, computed from `logit_miss` so it stays
  finite when p_miss is 0 or 1.
- Ranking: over pairs at the same tick with one breach i and one non-breach j,
  exp(-(p_miss_i - p_miss_j) / sigma), sigma = 0.1.

Per-sample weights undo the imbalance sampling: a breach drawn 3 times as often weighs 1/3. Each term is
a weighted mean (a pair weighs w_i * w_j). Censored rows enter the NLL only; their breach label may not
be known.

Inputs: the model's output dict (`logits` [B, K], `logit_miss` [B], `p_miss` [B]); `target` long [B], the
class of the receipt (`Bins.index`), ignored on censored rows; `breach` bool [B]; `tick` [B];
`weight` [B] or None; `censor_min` [B] with NaN for an observed receipt, or None.
"""

from __future__ import annotations

import torch
import torch.nn.functional as F

from src.models.tracernet.bins import MASK_LOGIT, Bins

SIGMA = 0.1
OVERSAMPLE = 3.0


def breach_weights(breach: torch.Tensor, oversample: float = OVERSAMPLE) -> torch.Tensor:
    """1 / oversample for a breach drawn `oversample` times as often, 1 otherwise."""
    return torch.where(breach.bool(), 1.0 / oversample, 1.0).to(torch.float32)


def nll(logits: torch.Tensor, target: torch.Tensor, bins: Bins, censor_min: torch.Tensor | None = None):
    """[B] negative log-likelihood per row."""
    logp = torch.log_softmax(logits, -1)
    observed = -logp.gather(1, target.clamp(0, bins.n_classes - 1)[:, None])[:, 0]
    if censor_min is None:
        return observed
    upper = torch.as_tensor(bins.upper, dtype=logits.dtype, device=logits.device)
    after = upper > censor_min[:, None]
    censored = -torch.logsumexp(logp.masked_fill(~after, MASK_LOGIT), -1)
    return torch.where(torch.isfinite(censor_min), censored, observed)


def miss_bce(logit_miss: torch.Tensor, breach: torch.Tensor) -> torch.Tensor:
    """[B] binary cross-entropy of p_miss = sigmoid(logit_miss)."""
    return F.binary_cross_entropy_with_logits(logit_miss, breach.to(logit_miss.dtype), reduction="none")


def ranking(p_miss: torch.Tensor, breach: torch.Tensor, tick: torch.Tensor,
            weight: torch.Tensor | None = None, sigma: float = SIGMA) -> torch.Tensor:
    """The DeepHit ranking term: weighted mean of exp(-(p_i - p_j) / sigma) over pairs at the same tick
    with i a breach and j not. 0 when the batch has no such pair."""
    b = breach.bool()
    w = torch.ones_like(p_miss) if weight is None else weight.to(p_miss.dtype)
    pairs = (tick[:, None] == tick[None, :]) & b[:, None] & ~b[None, :]
    pw = (w[:, None] * w[None, :]) * pairs
    total = pw.sum()
    if total <= 0:
        return p_miss.sum() * 0.0
    return (pw * torch.exp(-(p_miss[:, None] - p_miss[None, :]) / sigma)).sum() / total


def _wmean(values: torch.Tensor, w: torch.Tensor) -> torch.Tensor:
    total = w.sum()
    return (values * w).sum() / total if total > 0 else values.sum() * 0.0


def loss(out: dict, target: torch.Tensor, breach: torch.Tensor, tick: torch.Tensor, bins: Bins, *,
         lam: float = 1.0, eta: float = 0.05, weight: torch.Tensor | None = None,
         censor_min: torch.Tensor | None = None, sigma: float = SIGMA) -> tuple[torch.Tensor, dict]:
    """The scalar loss and its parts (floats: nll, bce, ranking, total)."""
    logits = out["logits"]
    w = torch.ones(logits.shape[0], dtype=logits.dtype, device=logits.device) if weight is None \
        else weight.to(logits.dtype)
    known = torch.ones_like(w, dtype=torch.bool) if censor_min is None else ~torch.isfinite(censor_min)
    part_nll = _wmean(nll(logits, target, bins, censor_min), w)
    part_bce = _wmean(miss_bce(out["logit_miss"], breach), w * known)
    part_rank = ranking(out["p_miss"], breach, tick, w * known, sigma) if eta else logits.new_zeros(())
    total = part_nll + lam * part_bce + eta * part_rank
    parts = {"nll": part_nll, "bce": part_bce, "ranking": part_rank, "total": total}
    return total, {k: float(v.detach()) for k, v in parts.items()}
