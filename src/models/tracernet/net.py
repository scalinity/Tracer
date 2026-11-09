"""TracerNet, rungs L, A, B and C (docs/neural/spec.md §7.3 and §7.9).

    L  a linear map from rung A's inputs to the head (reference only)
    A  specimen encoder (embeddings, multi-hot tests, numeric inputs) and fingerprint MLP, through the
       fusion MLP
    B  A plus three dilated causal temporal-convolution encoders: laboratory, unit and route count series
    C  B plus batch-mate attention

The head gives 125 logits (one per class of `bins`); bins ending at or before the tick are set to
`bins.MASK_LOGIT` before the softmax, so the distribution is conditional on no receipt by the tick.

Batch: a dict of tensors, B the batch size, float32 unless stated. Values arrive already transformed by
the feature builder; keys a rung does not read may be absent.

    cat           long [B, 7]   categorical ids in CAT_COLUMNS order: variant, unit_kind, route_kind, tier,
                                priority, weekday (of collection), weekday_tau (of the tick); each id is in
                                [0, its vocabulary size) of `TracerNetConfig.vocab`
    tests         [B, 13]       multi-hot of the specimen's tests (`n_tests`)
    num           [B, 59]       numeric inputs as the named slices of `num_groups`, in order: specimen 2,
                                window 9, silence 1, time_of_day 6, route_unit 6, batch_mates 7,
                                route_flow 17, unit_flow 5, lab_flow 6
    fingerprint   [B, 30]       the laboratory's history fingerprint (`n_fingerprint`)
    series_lab    [B, 6, 36]    laboratory count series, channels first; lag 35 is the 5 minutes ending at
                                the tick (rungs B and C)
    series_unit   [B, 4, 36]    the specimen's unit, same layout (rungs B and C)
    series_route  [B, 4, 36]    the specimen's route, same layout (rungs B and C)
    tokens        [B, 16, 12]   batch-mate tokens, nearest in collection time first, zeros as padding
                                (rung C)
    token_mask    bool [B, 16]  True for a real token; a row may be all False (rung C)
    tick          [B]           minutes since collection at this landmark
    window        [B]           the specimen's window in minutes, an edge of the bins

Output: a dict.

    logits          [B, K]      masked logits
    probs           [B, K]      the receipt-time distribution given no receipt by the tick; sums to 1
    p_miss, p_late, p_never, p_on_time   [B]
    logit_miss      [B]         log(p_miss / p_on_time) from the logits (integrated gradients use it)
    fused           [B, width]  the fused representation; None for rung L
    attention       [B, 16]     cross-attention weight on each batch-mate token, averaged over heads, 0 on
                                padding; None below rung C
    attention_null  [B]         weight on the learned null token; None below rung C

`embed(batch)` turns the categorical ids into embeddings and returns the continuous inputs the rest of
the network reads; `forward_embedded(inputs, tick, window)` runs from there. `input_groups()` maps the
ten factor groups of spec §9.3 onto those inputs, for summing integrated-gradient attributions.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F
from torch import nn

from src.models.tracernet import bins as tbins

GROUPS = ("silence", "window", "specimen", "route_unit", "time_of_day", "batch_mates", "route_flow",
          "unit_flow", "lab_flow", "lab_history")
CAT_COLUMNS = ("variant", "unit_kind", "route_kind", "tier", "priority", "weekday", "weekday_tau")
CAT_GROUP = {"variant": "specimen", "unit_kind": "route_unit", "route_kind": "route_unit", "tier": "specimen",
             "priority": "specimen", "weekday": "time_of_day", "weekday_tau": "time_of_day"}
SERIES_GROUP = {"series_lab": "lab_flow", "series_unit": "unit_flow", "series_route": "route_flow"}
RUNGS = ("L", "A", "B", "C")


@dataclass
class TracerNetConfig:
    """Layer sizes of spec §7.3. `width`, `temporal_channels` and `dropout` are what the search varies;
    the input sizes follow the feature schema."""
    rung: str = "C"
    edges: tuple | None = None  # bin edges; None builds them from config/breach_policy.yaml
    vocab: tuple = (("variant", 12, 8), ("unit_kind", 12, 8), ("route_kind", 6, 4), ("tier", 3, 4),
                    ("priority", 2, 2), ("weekday", 7, 4), ("weekday_tau", 7, 4))  # (column, ids, dim)
    n_tests: int = 13
    tests_dim: int = 8
    num_groups: tuple = (("specimen", 2), ("window", 9), ("silence", 1), ("time_of_day", 6), ("route_unit", 6),
                         ("batch_mates", 7), ("route_flow", 17), ("unit_flow", 5), ("lab_flow", 6))
    n_fingerprint: int = 30
    fingerprint_dim: int = 32
    series_channels: tuple = (("series_lab", 6), ("series_unit", 4), ("series_route", 4))
    token_features: int = 12
    width: int = 128
    temporal_channels: int = 32
    temporal_dim: int = 64
    attention_dim: int = 64
    heads: int = 4
    dropout: float = 0.1


def causal_conv(x, conv: nn.Conv1d, dilation: int):
    """The causal dilated convolution `conv(F.pad(x, (2 * dilation, 0)))` as three shifted taps and one einsum.
    Same parameters and the same result to about 1e-6, but 2 to 28 times faster on CPU (PyTorch's dilated Conv1d
    takes a slow path there and slows down with more threads) and faster on MPS too."""
    xp = F.pad(x, (2 * dilation, 0))
    n = x.shape[-1]
    taps = torch.stack([xp[..., j * dilation:j * dilation + n] for j in range(3)], -1)
    return torch.einsum("bclk,ock->bol", taps, conv.weight) + conv.bias[None, :, None]


class TemporalBlock(nn.Module):
    """Two causal convolutions, kernel 3, one dilation, with a residual connection."""

    def __init__(self, channels: int, dilation: int, dropout: float):
        super().__init__()
        self.dilation = dilation
        self.conv1 = nn.Conv1d(channels, channels, 3, dilation=dilation)
        self.conv2 = nn.Conv1d(channels, channels, 3, dilation=dilation)
        self.drop = nn.Dropout(dropout)

    def forward(self, x):
        h = self.drop(F.gelu(causal_conv(x, self.conv1, self.dilation)))
        h = self.drop(F.gelu(causal_conv(h, self.conv2, self.dilation)))
        return x + h


class TemporalEncoder(nn.Module):
    """[B, C_in, L] count series -> [B, 2 * channels]: a 1x1 convolution, four residual blocks with
    dilations 1, 2, 4 and 8 (receptive field 61 lags), then the last step and the mean."""

    def __init__(self, in_channels: int, channels: int, dropout: float):
        super().__init__()
        self.inp = nn.Conv1d(in_channels, channels, 1)
        self.blocks = nn.ModuleList(TemporalBlock(channels, d, dropout) for d in (1, 2, 4, 8))

    def forward(self, x):
        h = torch.einsum("bcl,oc->bol", x, self.inp.weight[..., 0]) + self.inp.bias[None, :, None]  # 1x1 conv
        for block in self.blocks:
            h = block(h)
        return torch.cat([h[..., -1], h.mean(-1)], -1)


class BatchMateAttention(nn.Module):
    """Tokens through an MLP, one pre-norm self-attention layer over the learned null token and the tokens,
    then cross-attention with the query projected from the specimen encoder. The null token is always a
    key, so an empty set attends to it alone."""

    def __init__(self, token_features: int, query_dim: int, dim: int, heads: int):
        super().__init__()
        self.token = nn.Sequential(nn.Linear(token_features, dim), nn.GELU(), nn.Linear(dim, dim))
        self.null = nn.Parameter(torch.randn(1, 1, dim) * 0.02)
        self.norm = nn.LayerNorm(dim)
        self.self_attn = nn.MultiheadAttention(dim, heads, batch_first=True)
        self.norm_kv = nn.LayerNorm(dim)
        self.query = nn.Linear(query_dim, dim)
        self.cross = nn.MultiheadAttention(dim, heads, batch_first=True)

    def forward(self, tokens, token_mask, query):
        n = tokens.shape[0]
        x = torch.cat([self.null.expand(n, -1, -1), self.token(tokens)], 1)
        ignore = torch.cat([token_mask.new_zeros(n, 1), ~token_mask.bool()], 1)
        h = self.norm(x)
        x = x + self.self_attn(h, h, h, key_padding_mask=ignore, need_weights=False)[0]
        kv = self.norm_kv(x)
        out, weights = self.cross(self.query(query)[:, None], kv, kv, key_padding_mask=ignore,
                                  need_weights=True, average_attn_weights=True)
        return out[:, 0], weights[:, 0]


class TracerNet(nn.Module):
    def __init__(self, config: TracerNetConfig | None = None):
        super().__init__()
        cfg = config or TracerNetConfig()
        if cfg.rung not in RUNGS:
            raise ValueError(f"rung must be one of {RUNGS}, got {cfg.rung!r}")
        if tuple(c for c, _, _ in cfg.vocab) != CAT_COLUMNS:
            raise ValueError(f"vocab columns must be {CAT_COLUMNS}")
        unknown = [g for g, _ in cfg.num_groups if g not in GROUPS]
        if unknown:
            raise ValueError(f"num_groups names {unknown} are not factor groups {GROUPS}")
        if tuple(k for k, _ in cfg.series_channels) != tuple(SERIES_GROUP):
            raise ValueError(f"series_channels keys must be {tuple(SERIES_GROUP)}")
        self.config = cfg
        self.rung = cfg.rung
        self.bins = tbins.Bins(tuple(cfg.edges)) if cfg.edges is not None else tbins.build()
        n_classes, w = self.bins.n_classes, cfg.width
        cat_dim = sum(d for _, _, d in cfg.vocab)
        n_num = sum(n for _, n in cfg.num_groups)

        self.emb = nn.ModuleList(nn.Embedding(n, d) for _, n, d in cfg.vocab)
        if self.rung == "L":
            self.linear = nn.Linear(cat_dim + cfg.n_tests + n_num + cfg.n_fingerprint, n_classes)
            return
        self.tests = nn.Linear(cfg.n_tests, cfg.tests_dim)
        self.encoder = nn.Sequential(nn.Linear(cat_dim + cfg.tests_dim + n_num, 2 * w), nn.GELU(),
                                     nn.Dropout(cfg.dropout), nn.Linear(2 * w, w), nn.LayerNorm(w))
        self.fingerprint = nn.Sequential(nn.Linear(cfg.n_fingerprint, cfg.fingerprint_dim), nn.GELU())
        fusion_in = w + cfg.fingerprint_dim
        if self.rung in ("B", "C"):
            self.temporal = nn.ModuleDict({key: TemporalEncoder(c, cfg.temporal_channels, cfg.dropout)
                                           for key, c in cfg.series_channels})
            self.temporal_out = nn.Sequential(
                nn.Linear(2 * cfg.temporal_channels * len(cfg.series_channels), cfg.temporal_dim), nn.GELU())
            fusion_in += cfg.temporal_dim
        if self.rung == "C":
            self.attention = BatchMateAttention(cfg.token_features, w, cfg.attention_dim, cfg.heads)
            fusion_in += cfg.attention_dim
        self.fusion = nn.Sequential(nn.Linear(fusion_in, 2 * w), nn.GELU(), nn.Dropout(cfg.dropout),
                                    nn.Linear(2 * w, w), nn.GELU())
        self.drop = nn.Dropout(cfg.dropout)
        self.head = nn.Linear(w, n_classes)

    def embed(self, batch: dict) -> dict:
        """The continuous inputs of this rung: `cat_emb` [B, 34] (the embeddings of the `cat` columns,
        concatenated in order), `tests`, `num`, `fingerprint`, and the series, `tokens` and `token_mask`
        the rung reads."""
        cat = batch["cat"]
        inputs = {"cat_emb": torch.cat([e(cat[:, i]) for i, e in enumerate(self.emb)], -1),
                  "tests": batch["tests"], "num": batch["num"], "fingerprint": batch["fingerprint"]}
        if self.rung in ("B", "C"):
            inputs.update({key: batch[key] for key in SERIES_GROUP})
        if self.rung == "C":
            inputs.update(tokens=batch["tokens"], token_mask=batch["token_mask"])
        return inputs

    def forward_embedded(self, inputs: dict, tick: torch.Tensor, window: torch.Tensor) -> dict:
        fused = attention = null = None
        if self.rung == "L":
            logits = self.linear(torch.cat([inputs["cat_emb"], inputs["tests"], inputs["num"],
                                            inputs["fingerprint"]], -1))
        else:
            spec = self.encoder(torch.cat([inputs["cat_emb"], self.tests(inputs["tests"]), inputs["num"]], -1))
            parts = [spec, self.fingerprint(inputs["fingerprint"])]
            if self.rung in ("B", "C"):
                parts.append(self.temporal_out(torch.cat([enc(inputs[key]) for key, enc in self.temporal.items()],
                                                         -1)))
            if self.rung == "C":
                att, weights = self.attention(inputs["tokens"], inputs["token_mask"], spec)
                parts.append(att)
                null, attention = weights[:, 0], weights[:, 1:]
            fused = self.fusion(torch.cat(parts, -1))
            logits = self.head(self.drop(fused))
        logits = self.bins.mask_logits(logits, tick)
        probs = torch.softmax(logits, -1)
        return {"logits": logits, "probs": probs, **self.bins.derived(probs, window),
                "logit_miss": self.bins.logit_miss(logits, window), "fused": fused,
                "attention": attention, "attention_null": null}

    def forward(self, batch: dict) -> dict:
        return self.forward_embedded(self.embed(batch), batch["tick"], batch["window"])

    def input_groups(self) -> dict[str, dict[str, list[int] | None]]:
        """For each factor group, the inputs from `embed` it covers: key -> column indices along dim 1, or
        None for the whole tensor. Each input element of this rung is in exactly one group (`token_mask`
        is structure, not an input to attribute)."""
        groups: dict[str, dict] = {g: {} for g in GROUPS}
        col = 0
        for name, _, dim in self.config.vocab:
            groups[CAT_GROUP[name]].setdefault("cat_emb", []).extend(range(col, col + dim))
            col += dim
        groups["specimen"]["tests"] = None
        col = 0
        for name, n in self.config.num_groups:
            groups[name].setdefault("num", []).extend(range(col, col + n))
            col += n
        groups["lab_history"]["fingerprint"] = None
        if self.rung in ("B", "C"):
            for key, group in SERIES_GROUP.items():
                groups[group][key] = None
        if self.rung == "C":
            groups["batch_mates"]["tokens"] = None
        return groups


def n_parameters(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters())
