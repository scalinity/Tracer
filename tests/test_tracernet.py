"""TracerNet's bins, rungs and loss (docs/neural/spec.md §7.1, §7.3, §7.5, §15.2)."""

import json
import math

import numpy as np
import pytest
import torch

from src.generator import config
from src.models.tracernet import bins as tbins
from src.models.tracernet.net import GROUPS, RUNGS, TracerNet, TracerNetConfig, n_parameters
from src.neural import losses

BINS = tbins.build()
WINDOWS = (60, 120, 1440, 2880, 4320, 7200)


def edge_index(minute):
    return BINS.edges.index(minute)


def random_batch(cfg: TracerNetConfig, n: int = 32, seed: int = 0) -> dict:
    """Random tensors of the shapes in net.py's docstring, with some ticks at and after the window."""
    g = torch.Generator().manual_seed(seed)
    window = torch.tensor(WINDOWS, dtype=torch.float32)[torch.randint(len(WINDOWS), (n,), generator=g)]
    tick = torch.ceil(torch.rand(n, generator=g) * window / 5) * 5
    tick[:3] = window[:3]  # the last tick of a window: everything left is a miss
    window[3], tick[3] = 7200.0, 7200.0  # nothing finite left: only `beyond`
    token_mask = torch.rand(n, 16, generator=g) < 0.5
    token_mask[0] = False  # an empty batch-mate set
    return {
        "cat": torch.stack([torch.randint(size, (n,), generator=g) for _, size, _ in cfg.vocab], 1),
        "tests": (torch.rand(n, cfg.n_tests, generator=g) < 0.2).float(),
        "num": torch.randn(n, sum(k for _, k in cfg.num_groups), generator=g),
        "fingerprint": torch.randn(n, cfg.n_fingerprint, generator=g),
        **{key: torch.randn(n, c, 36, generator=g) for key, c in cfg.series_channels},
        "tokens": torch.randn(n, 16, cfg.token_features, generator=g) * token_mask[..., None],
        "token_mask": token_mask,
        "tick": tick,
        "window": window,
    }


def model(rung: str, seed: int = 0) -> tuple[TracerNet, TracerNetConfig]:
    torch.manual_seed(seed)
    cfg = TracerNetConfig(rung=rung, edges=BINS.edges)
    return TracerNet(cfg), cfg


def test_there_are_125_classes_and_every_policy_window_is_an_edge():
    windows = {p["window_min"] for p in config.load()["policy"]["policies"].values()}
    widths = np.diff(BINS.edges).tolist()
    assert (BINS.n_classes, BINS.beyond, BINS.horizon) == (125, 124, 7200)
    assert [widths.count(w) for w in (5, 30, 120, 240)] == [48, 40, 24, 12]
    assert windows <= set(BINS.edges) and set(BINS.windows) == windows


def test_a_new_window_value_adds_an_edge_and_a_window_off_the_edges_is_refused():
    grown = tbins.build({"policies": {"a": {"window_min": 60}, "b": {"window_min": 1500}}})
    assert 1500 in grown.edges and grown.n_classes == 126 and grown.windows == (60, 1500)
    assert grown.index([1499, 1501]).tolist() == [grown.edges.index(1500) - 1, grown.edges.index(1500)]
    with pytest.raises(ValueError):
        tbins.Bins(tuple(tbins.default_edges()), (1500,))


def test_a_receipt_falls_in_the_bin_closed_on_its_right():
    got = BINS.index([0, 5, 5.5, 60, 60.01, 7200, 7200.5, None, np.nan, np.inf]).tolist()
    assert got == [0, 0, 1, 11, 12, 123, 124, 124, 124, 124]
    # A receipt at exactly the window is on time, as the label (minutes > window) has it.
    assert BINS.missed(BINS.index([60, 60.01]), 60).tolist() == [False, True]


def toy_probs():
    p = np.zeros(BINS.n_classes)
    p[edge_index(55)] = 0.5  # (55, 60]: on time for a 60-minute window
    p[edge_index(60)] = 0.25  # (60, 65]
    p[edge_index(1440)] = 0.125  # (1440, 1560]
    p[BINS.beyond] = 0.125
    return p


def test_p_miss_matches_a_hand_computed_toy_exactly():
    p = toy_probs()
    for probs in (p, torch.tensor(p, dtype=torch.float32)):
        d60 = BINS.derived(probs, 60.0 if isinstance(probs, np.ndarray) else torch.tensor(60.0))
        assert [float(d60[k]) for k in ("p_miss", "p_late", "p_never", "p_on_time")] == [0.5, 0.375, 0.125, 0.5]
        d1440 = BINS.derived(probs, 1440.0 if isinstance(probs, np.ndarray) else torch.tensor(1440.0))
        assert [float(d1440[k]) for k in ("p_miss", "p_late", "p_never")] == [0.25, 0.125, 0.125]
    batch = np.stack([p, p])
    assert BINS.p_miss(batch, np.array([60.0, 120.0])).tolist() == [0.5, 0.25]


def test_arrival_quantiles_spread_mass_uniformly_inside_a_bin():
    p = np.zeros((3, BINS.n_classes))
    p[0, edge_index(60)] = 1.0  # all in (60, 65]
    p[1, edge_index(240)] = 0.5  # (240, 270], of which (250, 270] is still open at tick 250
    p[1, BINS.beyond] = 0.5
    p[2, BINS.beyond] = 1.0  # never arrives
    q = BINS.arrival_quantiles(p, np.array([10.0, 250.0, 10.0]), (0.5, 0.9))
    assert q[0].tolist() == pytest.approx([62.5, 64.5])
    assert q[1].tolist() == pytest.approx([260.0, 268.0])
    assert np.isnan(q[2]).all()


@pytest.mark.parametrize("rung", RUNGS)
def test_the_masked_distribution_sums_to_one_and_puts_no_mass_before_the_tick(rung):
    net, cfg = model(rung)
    net.eval()
    batch = random_batch(cfg)
    with torch.no_grad():
        out = net(batch)
    impossible = BINS.impossible(batch["tick"])
    assert torch.allclose(out["probs"].sum(-1), torch.ones(len(batch["tick"])), atol=1e-6)
    assert (out["probs"][impossible] == 0).all() and (out["logits"][impossible] == tbins.MASK_LOGIT).all()
    assert torch.allclose(out["p_miss"][:3], torch.ones(3), atol=1e-6)  # tick at the window: certain miss
    assert float(out["p_never"][3]) == 1.0  # tick at the horizon: only `beyond` is left
    assert torch.equal(out["p_late"] + out["p_never"], out["p_miss"])
    assert torch.equal(out["p_miss"], BINS.p_miss(out["probs"], batch["window"]))
    on_time = (out["p_on_time"] > 1e-4) & (out["p_miss"] > 1e-4)
    assert torch.allclose(out["logit_miss"][on_time], torch.log(out["p_miss"] / out["p_on_time"])[on_time], atol=1e-3)
    assert torch.isfinite(out["logit_miss"]).all()


def test_the_conditional_cdf_is_monotone():
    net, cfg = model("C")
    net.eval()
    batch = random_batch(cfg)
    with torch.no_grad():
        probs = net(batch)["probs"].double().numpy()
    tick = batch["tick"].numpy()
    assert (np.diff(np.cumsum(probs, -1), axis=-1) >= 0).all()
    grid = np.concatenate([np.arange(0, 300, 2.5), np.arange(300, 7300, 60)])
    curve = np.stack([BINS.cdf(probs, tick, np.full_like(tick, x)) for x in grid], -1)
    assert (np.diff(curve, axis=-1) >= -1e-12).all()
    assert np.allclose(curve[:, -1], 1 - probs[:, -1])
    assert np.allclose(BINS.cdf(probs, tick, tick), 0.0)  # nothing arrives by a tick it was silent at
    q = BINS.arrival_quantiles(probs, tick, (0.1, 0.5, 0.9))
    finite = ~np.isnan(q).any(-1)
    assert (np.diff(q[finite], axis=-1) >= 0).all() and (q[finite, 0] >= tick[finite]).all()


def test_a_cpu_forward_is_deterministic_across_two_runs_with_the_same_seed():
    previous = torch.are_deterministic_algorithms_enabled()
    torch.use_deterministic_algorithms(True)
    try:
        runs = []
        for _ in range(2):
            net, cfg = model("C", seed=7)
            batch = random_batch(cfg, seed=3)
            net.eval()
            with torch.no_grad():
                frozen = net(batch)
            net.train()
            torch.manual_seed(11)
            dropped = net(batch)
            runs.append((frozen, dropped))
        for key in ("logits", "probs", "p_miss", "fused", "attention"):
            assert torch.equal(runs[0][0][key], runs[1][0][key]), key
            assert torch.equal(runs[0][1][key], runs[1][1][key]), key
    finally:
        torch.use_deterministic_algorithms(previous)


@pytest.mark.parametrize("rung", RUNGS)
def test_every_rung_runs_forward_and_backward_on_the_documented_shapes(rung):
    net, cfg = model(rung)
    net.train()
    batch = random_batch(cfg, n=64)
    out = net(batch)
    width = cfg.width
    assert out["logits"].shape == (64, 125) and out["p_miss"].shape == (64,)
    assert (out["fused"] is None) if rung == "L" else (out["fused"].shape == (64, width))
    if rung == "C":
        assert out["attention"].shape == (64, 16)
        assert float(out["attention_null"][0].detach()) == pytest.approx(1.0)  # empty set: all weight on the null token
        assert (out["attention"][~batch["token_mask"]] == 0).all()
        assert torch.allclose(out["attention"].sum(-1) + out["attention_null"], torch.ones(64), atol=1e-6)
    else:
        assert out["attention"] is None
    target = torch.as_tensor(BINS.index(batch["tick"].numpy() + 3.0))
    breach = BINS.missed(target, batch["window"])
    total, parts = losses.loss(out, target, breach, batch["tick"], BINS, lam=1.0, eta=0.1,
                               weight=losses.breach_weights(breach))
    total.backward()
    assert math.isfinite(parts["total"])
    for name, p in net.named_parameters():
        assert p.grad is not None and torch.isfinite(p.grad).all(), name


def test_parameter_counts_are_near_the_spec(capsys):
    counts = {r: n_parameters(model(r)[0]) for r in RUNGS}
    with capsys.disabled():
        print("\nTracerNet parameters:", ", ".join(f"{r} {n:,}" for r, n in counts.items()))
    assert 130_000 <= counts["A"] <= 170_000  # spec: about 0.15M
    assert 280_000 <= counts["C"] <= 340_000  # spec: about 0.31M
    assert counts["L"] < counts["A"] < counts["B"] < counts["C"]


def test_the_named_input_groups_cover_every_input_exactly_once_and_carry_gradients():
    for rung in RUNGS:
        net, cfg = model(rung)
        batch = random_batch(cfg, n=8)
        inputs = {k: v.detach().requires_grad_(k != "token_mask") for k, v in net.embed(batch).items()}
        groups = net.input_groups()
        assert tuple(groups) == GROUPS
        covered = {k: torch.zeros(v.shape[1], dtype=torch.long) for k, v in inputs.items() if k != "token_mask"}
        for parts in groups.values():
            for key, cols in parts.items():
                covered[key][slice(None) if cols is None else cols] += 1
        assert all((c == 1).all() for c in covered.values()), rung
        # Integrated gradients differentiate the logit of p_miss with respect to these inputs.
        net.eval()
        live = batch["tick"] < batch["window"]
        net.forward_embedded(inputs, batch["tick"], batch["window"])["logit_miss"][live].sum().backward()
        assert all(inputs[k].grad is not None and inputs[k].grad.abs().sum() > 0 for k in covered), rung


def test_bins_json_round_trips():
    assert tbins.Bins.from_json(json.loads(json.dumps(BINS.to_json()))) == BINS


def test_the_nll_reads_the_observed_bin_and_a_censored_case_reads_the_bins_after_it():
    logits = torch.randn(2, BINS.n_classes, generator=torch.Generator().manual_seed(0))
    logits = BINS.mask_logits(logits, torch.tensor([10.0, 10.0]))
    probs = torch.softmax(logits, -1)
    target = torch.tensor([edge_index(60), 0])
    censor = torch.tensor([float("nan"), 100.0])
    got = losses.nll(logits, target, BINS, censor)
    after = torch.as_tensor(BINS.upper > 100.0)
    assert float(got[0]) == pytest.approx(-math.log(float(probs[0, edge_index(60)])), rel=1e-5)
    assert float(got[1]) == pytest.approx(-math.log(float(probs[1, after].sum())), rel=1e-5)


def test_bce_and_the_ranking_term_on_a_hand_toy():
    p = torch.tensor([0.7, 0.5, 0.9, 0.2])
    breach = torch.tensor([True, False, True, False])
    tick = torch.tensor([30.0, 30.0, 60.0, 35.0])  # only (0, 1) share a tick with one breach each side
    assert float(losses.ranking(p, breach, tick)) == pytest.approx(math.exp(-2.0), rel=1e-5)
    assert float(losses.ranking(p, breach, torch.tensor([5.0, 10.0, 15.0, 20.0]))) == 0.0
    bce = losses.miss_bce(torch.logit(p), breach)
    assert float(bce[0]) == pytest.approx(-math.log(0.7), rel=1e-5)
    assert float(bce[1]) == pytest.approx(-math.log(0.5), rel=1e-5)
    assert losses.breach_weights(breach).tolist() == pytest.approx([1 / 3, 1, 1 / 3, 1])


# --- The shift ranking (docs/neural/preregistration-v2.md §7) -----------------------------------------------------

def _shift(logit, breach, run, tau, tick, window, tier=None, weight=None):
    tier = torch.zeros(len(logit), dtype=torch.long) if tier is None else torch.tensor(tier)
    log_w = torch.tensor(losses.TIER_LOG_WEIGHTS)[tier]
    return losses.shift_ranking(torch.tensor(logit, dtype=torch.float32), torch.tensor(breach),
                                torch.tensor(run), torch.tensor(tau, dtype=torch.float32),
                                torch.tensor(tick, dtype=torch.float32), torch.tensor(window, dtype=torch.float32),
                                log_w, weight)


def test_the_shift_ranking_pairs_a_breach_only_with_on_time_specimens_of_its_run_and_shift():
    # one pair counts: breach 0 against specimen 1 (same run, 60 minutes apart); specimen 2 is another run and
    # specimen 3 is 300 minutes away
    logit = [0.0, 1.0, 5.0, 5.0]
    got = float(_shift(logit, [True, False, False, False], [7, 7, 8, 7], [100, 160, 100, 400], [5, 5, 5, 5],
                       [60, 60, 60, 60]))
    h = torch.nn.functional.logsigmoid(torch.tensor(logit))
    assert got == pytest.approx(float(torch.nn.functional.softplus(-(h[0] - h[1]))), rel=1e-6)
    # no pair at all: zero, with a gradient path
    zero = _shift([0.0, 1.0], [False, False], [7, 7], [0, 0], [5, 5], [60, 60])
    assert float(zero) == 0.0


def test_the_shift_ranking_values_a_tick_by_the_capped_warning_it_would_buy():
    # two breaches against one on-time specimen: the breach at tick 55 of a 60-minute window buys 5 minutes, the one
    # at tick 5 buys 55, so their pairs weigh 5 and 55 (the cap of 120 is not reached)
    logit = [-1.0, -1.0, 0.0]
    early = float(_shift([logit[0], logit[2]], [True, False], [1, 1], [0, 0], [5, 5], [60, 60]))
    both = float(_shift(logit, [True, True, False], [1, 1, 1], [0, 0, 0], [5, 55, 5], [60, 60, 60]))
    assert both == pytest.approx(early, rel=1e-6)  # equal pair losses, so the weights cancel in the mean
    at_close = _shift([-1.0, 0.0], [True, False], [1, 1], [0, 0], [60, 5], [60, 60])
    assert float(at_close) == 0.0  # a tick at the window's close buys nothing


def test_the_shift_ranking_averages_the_equal_and_the_3_2_1_weighting():
    # a tier-1 breach against a tier-3 on-time specimen: the 3-2-1 half adds log 3 to the breach's harm
    logit = [0.0, 0.5]
    got = float(_shift(logit, [True, False], [1, 1], [0, 0], [5, 5], [120, 120], tier=[0, 2]))
    h = torch.nn.functional.logsigmoid(torch.tensor(logit))
    sp = torch.nn.functional.softplus
    want = (sp(-(h[0] - h[1])) + sp(-(h[0] + math.log(3.0) - h[1]))) / 2
    assert got == pytest.approx(float(want), rel=1e-6)


def test_the_shift_ranking_pushes_a_breach_up_and_an_on_time_specimen_down():
    logit = torch.tensor([0.0, 0.0], requires_grad=True)
    loss = losses.shift_ranking(logit, torch.tensor([True, False]), torch.tensor([1, 1]), torch.tensor([0.0, 0.0]),
                                torch.tensor([5.0, 5.0]), torch.tensor([60.0, 60.0]), torch.zeros(2))
    loss.backward()
    assert logit.grad[0] < 0 < logit.grad[1]


def test_the_full_loss_adds_rho_times_the_shift_ranking_and_needs_its_tensors():
    net, cfg = model("A")
    batch = random_batch(cfg, n=16, seed=3)
    out = net(batch)
    target = torch.as_tensor(BINS.index(batch["tick"].numpy() + 3.0))
    breach = torch.arange(16) % 3 == 0
    base, parts0 = losses.loss(out, target, breach, batch["tick"], BINS, lam=1.0, eta=0.0)
    shift = {"run": torch.zeros(16, dtype=torch.long), "tau": batch["tick"], "window": batch["window"],
             "log_w": torch.zeros(16)}
    total, parts = losses.loss(out, target, breach, batch["tick"], BINS, lam=1.0, eta=0.0, rho=2.0, shift=shift)
    assert parts0["shift"] == 0.0
    assert parts["total"] == pytest.approx(parts0["total"] + 2.0 * parts["shift"], rel=1e-6)
    with pytest.raises(ValueError, match="rho"):
        losses.loss(out, target, breach, batch["tick"], BINS, rho=1.0)
