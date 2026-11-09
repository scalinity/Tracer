"""TracerNet's feature builder and samplers (docs/neural/spec.md §7.1, §7.4, §7.7, §15.2): future invariance, the
tick set, the v1 store, the batch TracerNet reads, and the sampler's determinism and weighting."""

import duckdb
import numpy as np
import pytest
import torch

from src.models.tracernet import features as F
from src.models.tracernet.net import GROUPS, TracerNet
from src.neural import devlib, samplers as S
from src.pipeline import evaluate, store

INPUTS = ("cat", "tests", "num", "fingerprint", "series_lab", "series_unit", "series_route", "tokens", "token_mask",
          "tick", "window")


@pytest.fixture(scope="module")
def dev(tmp_path_factory):
    d = tmp_path_factory.mktemp("dev")
    devlib.build(d, indices=(0, 7, 22, 45), workers=2)   # one scenario in each of train, val, test, test_ood
    return d / "store"


@pytest.fixture(scope="module")
def table(dev):
    return F.load_table(dev)


@pytest.fixture(scope="module")
def schema(table):
    return S.fit_feature_schema(table, samples=20_000, workers=1)


# --- Future invariance ---------------------------------------------------------------------------------------------

ROW_KEYS = [k for k in F.ARRAYS]


def perturbed(t: F.Table, run: int, tau_s: float, rng: np.random.Generator) -> F.Table:
    """A copy of the table in which everything of `run` after tau is different: receipts and accessions after tau
    move, vanish or appear, and every specimen collected after tau gets a new collection time after tau and another
    specimen's attributes. Rows collected by tau keep their places."""
    a = {k: np.array(v) for k, v in t.a.items()}
    rows = t.rows_of_run(run)
    c, r, acc = a["collected"][rows], a["received"][rows], a["accessioned"][rows]
    future = c > tau_s
    later_r = np.isfinite(r) & (r > tau_s)
    r[later_r] = np.where(rng.random(later_r.sum()) < 0.2, np.nan, tau_s + rng.uniform(1, 6000 * 60, later_r.sum()))
    gain = ~np.isfinite(a["received"][rows]) & ~future & (rng.random(len(rows)) < 0.5)
    r[gain] = tau_s + rng.uniform(1, 6000 * 60, gain.sum())
    later_acc = np.isfinite(acc) & (acc > tau_s)
    acc[later_acc] = tau_s + rng.uniform(1, 9000 * 60, later_acc.sum())
    f = np.flatnonzero(future)
    newc = np.round(tau_s + rng.uniform(1, 4 * 1440 * 60, len(f)))
    c[f] = newc
    r[f] = np.where(rng.random(len(f)) < 0.1, np.nan, newc + np.round(rng.uniform(60, 3000 * 60, len(f))))
    acc[f] = r[f] + 600
    a["collected"][rows], a["received"][rows], a["accessioned"][rows] = c, r, acc
    a["ordered"][rows[f]] = np.where(rng.random(len(f)) < 0.5, np.nan, newc - 1800)
    perm = rng.permutation(len(f))
    for key in ("unit", "route", "route_type", "route_kind", "unit_kind", "window", "stat", "tier", "variant",
                "tests", "scored"):
        a[key][rows[f]] = a[key][rows[f]][perm]
    order = np.lexsort((a["serial"][rows], a["collected"][rows]))
    assert np.array_equal(order[~future[order]], np.flatnonzero(~future))   # past rows stay first, in place
    for key in ROW_KEYS:
        a[key][rows] = a[key][rows][order]
    return F.Table(a, t.names, t.rosters, t.meta, t.hist)


def test_every_feature_at_tau_ignores_everything_after_tau(table):
    rng = np.random.default_rng(7)
    run = int(next(r for r, h in zip(table.runs, table.a["run_history"]) if not h))
    rows = S.scored_rows(table, table.names["splits"][int(table.a["split"][table.rows_of_run(run)[0]])])
    rows = rows[np.isin(rows, table.rows_of_run(run))]
    picks = rng.choice(rows, 30, replace=False)
    changed_later = 0
    for i in picks:
        ticks = table.ticks(i)
        t = float(rng.choice(ticks))
        tau_s = float(table.a["collected"][i]) + 60 * t
        other = perturbed(table, run, tau_s, rng)
        assert table.a["serial"][i] == other.a["serial"][i]
        before = F.features_for_run(table, run, [i], [t])
        after = F.features_for_run(other, run, [i], [t])
        for key in INPUTS:
            assert np.array_equal(before[key], after[key]), (key, int(i), t)
        later = F.features_for_run(table, run, [i], [t + 120.0])
        later_other = F.features_for_run(other, run, [i], [t + 120.0])
        changed_later += any(not np.array_equal(later[k], later_other[k]) for k in INPUTS)
    assert changed_later >= 20   # the perturbation is real: features two hours on do see it


def test_aggregates_series_and_tokens_equal_direct_counts(table):
    """The vectorized features against a direct count over the run's specimens for each sample."""
    rng = np.random.default_rng(5)
    scored_runs = [int(r) for r, h in zip(table.runs, table.a["run_history"]) if not h]
    for run in rng.choice(scored_runs, 2, replace=False):
        ctx = F.RunContext(table, int(run))
        rows = table.rows_of_run(int(run))
        rows = rows[table.a["scored"][rows] & (table.n_ticks(rows) > 0)]
        pick = rng.choice(rows, 25, replace=False)
        ticks = np.array([rng.choice(table.ticks(r)) for r in pick])
        raw = F.raw_features(table, ctx, pick - ctx.lo, ticks)
        c, r, w, n = ctx.c, ctx.r, ctx.w, ctx.n
        for q, (g, t) in enumerate(zip(pick, ticks)):
            i = g - ctx.lo
            tau, tau_s = c[i] + t, ctx.cs[i] + 60 * t
            m = int(np.floor(tau))
            received = np.isfinite(r) & (r <= m)
            out = (c <= m) & ~received & (m - c <= w + F.GRACE_MIN)
            route, unit = ctx.route_l == ctx.route_l[i], ctx.unit_l == ctx.unit_l[i]
            expect = {"out_lab": out.sum(), "out_unit": (out & unit).sum(), "out_route": (out & route).sum(),
                      "out_route_older": (out & route & (c < c[i])).sum(),
                      "rec15_route": (route & received & (r > m - 15)).sum(),
                      "rec60_lab": (received & (r > m - 60)).sum(),
                      "transit_route_n": (route & received & (r > m - 180)).sum()}
            for key, value in expect.items():
                assert raw["num"][q, F.NUM_INDEX[key]] == pytest.approx(np.log1p(value)), key
            recent = (r - c)[route & received & (r > m - 180)]
            if len(recent):
                assert raw["num"][q, F.NUM_INDEX["transit_route_p90"]] == pytest.approx(np.log1p(np.percentile(recent, 90)))
            mates = unit & route & (np.abs(c - c[i]) <= F.BATCH_MATE_MIN) & (ctx.cs <= tau_s) & (np.arange(n) != i)
            assert raw["num"][q, F.NUM_INDEX["mates"]] == pytest.approx(np.log1p(mates.sum()))
            for lag in (0, 20, 35):
                hi = m - F.LAG_MIN * (F.N_LAGS - 1 - lag)
                o = (c <= hi) & ~(np.isfinite(r) & (r <= hi)) & (hi - c <= w + F.GRACE_MIN)
                assert raw["series_lab"][q, 0, lag] == pytest.approx(np.log1p(((c <= hi) & (c > hi - 5)).sum()))
                oldest = (hi - c[o & route]).max() / 60 if (o & route).any() else 0.0
                assert raw["series_route"][q, 3, lag] == pytest.approx(min(oldest, F.OLDEST_CAP_H), abs=1e-4)
            near = (unit | route) & (np.arange(n) != i) & (c >= c[i] - 60) & (ctx.cs <= min(tau_s, ctx.cs[i] + 3600))
            j = np.flatnonzero(near)
            j = j[np.argsort(np.abs(c[j] - c[i]), kind="stable")][:F.N_TOKENS]
            assert np.allclose(np.sort((c[j] - c[i]) / 30), np.sort(raw["tokens"][q, raw["token_mask"][q], 2]))


# --- Ticks ---------------------------------------------------------------------------------------------------------

def test_the_tick_set_equals_the_scoring_ticks_of_spec(dev, table):
    for split in ("train", "val"):
        specs, _ = evaluate.load_specs_v2(split, store_dir=dev, labels=False)
        rows = table.rows_of([s.specimen_id for s in specs])
        for s, r in zip(specs, rows):
            assert np.array_equal(table.ticks(r), s.ticks), s.specimen_id
        expected = sorted((int(r), float(t)) for s, r in zip(specs, rows) for t in s.ticks)
        vr, vt = S.validation_ticks(table, split)
        assert list(zip(vr.tolist(), vt.tolist())) == expected
    plan = S.epoch_plan(table, S.scored_rows(table, "train"), 0, 0, 2000)
    for r, t in zip(plan["rows"][:3000], plan["ticks"][:3000]):
        assert t in table.ticks(r)


def test_val_sel_is_a_fixed_quarter_of_scenarios_with_ticks_up_to_240(table):
    names = S.val_sel_scenarios(table)
    assert names == S.val_sel_scenarios(table) and len(names) >= 1
    rows, ticks = S.validation_ticks(table, "val", subset="val_sel")
    assert ticks.max() <= 240 and np.all(ticks <= table.a["window"][rows])
    assert {table.names["scenarios"][s] for s in table.a["scenario"][rows]} == set(names)


# --- The batch -----------------------------------------------------------------------------------------------------

def test_the_batch_matches_tracernet_and_a_forward_pass_runs(table, schema):
    rows, ticks = S.validation_ticks(table, "val", subset="val_sel")
    pick = np.random.default_rng(0).choice(len(rows), 300, replace=False)
    batch = F.FeatureBuilder(table=table, schema=schema).build(rows[pick], ticks[pick])
    cfg = F.net_config(schema, rung="C")
    b = len(pick)
    assert batch["cat"].shape == (b, 7) and batch["cat"].dtype == np.int64
    for col, (name, size, _) in enumerate(cfg.vocab):
        assert batch["cat"][:, col].min() >= 0 and batch["cat"][:, col].max() < size, name
    assert batch["tests"].shape == (b, cfg.n_tests)
    assert batch["num"].shape == (b, sum(n for _, n in cfg.num_groups))
    assert [g for g, _ in cfg.num_groups] == [g for g in F.GROUP_ORDER] and set(F.GROUP_ORDER) <= set(GROUPS)
    assert batch["fingerprint"].shape == (b, cfg.n_fingerprint) == (b, 30)
    for key, channels in cfg.series_channels:
        assert batch[key].shape == (b, channels, F.N_LAGS)
    assert batch["tokens"].shape == (b, 16, cfg.token_features) and batch["token_mask"].shape == (b, 16)
    assert all(np.abs(batch[k]).max() <= F.CLIP for k in ("num", "fingerprint", "series_lab", "tokens"))
    assert np.all(batch["tick"] <= batch["window"])
    assert np.array_equal(batch["target_bin"], F.bins_cached().index(table.receipt_min(batch["specimen_index"])))
    torch.manual_seed(0)
    model = TracerNet(cfg).eval()
    with torch.no_grad():
        out = model(F.to_torch({k: batch[k] for k in INPUTS}))
    assert out["probs"].shape == (b, model.bins.n_classes)
    assert torch.allclose(out["probs"].sum(-1), torch.ones(b), atol=1e-5) and torch.isfinite(out["p_miss"]).all()
    assert set(model.input_groups()) == set(GROUPS)


def test_the_builder_runs_on_the_v1_store_with_indicators(tmp_path_factory):
    d = tmp_path_factory.mktemp("v1")
    store.build(store_dir=d)
    t1 = F.load_table(d)
    assert t1.meta["v1"] and t1.names["scenarios"] == [F.LEGACY]
    rows = S.scored_rows(t1, "test")
    assert set(np.unique(t1.a["run"][rows])) == set(range(24, 30))
    pick = np.random.default_rng(1).choice(rows, 400, replace=False)
    ticks = np.array([t1.ticks(r)[0] for r in pick])
    raw = F.FeatureBuilder(table=t1).build(pick, ticks, raw=True)
    assert np.all(raw["num"][:, F.NUM_INDEX["roster_missing"]] == 1)
    assert np.all(raw["num"][:, F.NUM_INDEX["no_round"]] == 1)
    kinds = {F.ROUTE_KINDS[k] for k in raw["cat"][:, 2]}
    assert kinds <= {"tube", "hand_carry"}
    assert np.all(raw["fingerprint"][:, F.FINGERPRINT_COLUMNS.index("history_available")] == 1)
    assert np.isfinite(raw["num"]).all() and np.isfinite(raw["fingerprint"]).all()
    schema = F.fit_schema(raw)
    model = TracerNet(F.net_config(schema, rung="C")).eval()
    with torch.no_grad():
        out = model(F.to_torch({k: v for k, v in F.transform(raw, schema).items() if k in INPUTS}))
    assert torch.isfinite(out["p_miss"]).all()


def test_the_schema_saves_loads_and_refuses_another_version_or_policy(schema, tmp_path):
    path = F.save_schema(schema, tmp_path / "feature_schema.json")
    assert F.load_schema(path) == schema
    for change in ({"version": "tracernet-features-0"}, {"policy_hash": "0" * 16}):
        F.save_schema(schema | change, tmp_path / "bad.json")
        with pytest.raises(RuntimeError):
            F.load_schema(tmp_path / "bad.json")


def test_the_builder_and_the_sampler_never_open_the_truth_file(dev, monkeypatch):
    opened = []
    real = duckdb.connect

    def spy(path=":memory:", *a, **k):
        opened.append(str(path))
        return real(path, *a, **k)

    def refuse(*a, **k):
        raise AssertionError("opened the truth file")
    monkeypatch.setattr(duckdb, "connect", spy)
    monkeypatch.setattr(store, "open_truth", refuse)
    t = F.load_table(dev)
    chunks = list(S.TrainSampler(t, None, specimens_per_epoch=300, workers=1, chunk=256).epoch(0))
    assert chunks and opened and not any(store.TRUTH_NAME in p for p in opened)


# --- The sampler ---------------------------------------------------------------------------------------------------

def test_the_sampler_is_deterministic_whatever_the_number_of_workers(table, schema, tmp_path):
    def run(seed, workers):
        with S.TrainSampler(table, schema, seed=seed, specimens_per_epoch=1500, workers=workers, chunk=512,
                            cache_root=tmp_path) as s:
            return list(s.epoch(1))
    a, b, c = run(3, 1), run(3, 2), run(4, 1)
    assert len(a) == len(b) and all(np.array_equal(x[k], y[k]) for x, y in zip(a, b) for k in x)
    assert [len(x["tick"]) for x in a][:-1] == [512] * (len(a) - 1)
    assert not all(np.array_equal(x["specimen_index"], y["specimen_index"]) for x, y in zip(a, c))
    order = np.concatenate([x["specimen_index"] for x in a])
    assert not np.all(np.diff(table.a["run"][order]) >= 0)   # shuffled across runs


def test_breaches_are_drawn_three_times_as_often_and_weighted_one_third(table):
    rows = S.scored_rows(table, "train")
    p = table.breach(rows).mean()
    plan = S.epoch_plan(table, rows, seed=0, epoch=0, n_specimens=200_000)
    assert plan["drawn_breach"].mean() == pytest.approx(3 * p / (3 * p + 1 - p), abs=0.006)
    w = np.where(plan["drawn_breach"], 1 / 3, 1.0)
    assert (w * plan["drawn_breach"]).sum() / w.sum() == pytest.approx(p, abs=0.004)
    assert np.allclose(np.unique(plan["weight"]), [1 / 3, 1.0])
    assert np.array_equal(plan["weight"] < 1, table.breach(plan["rows"]))
    per_draw = np.bincount(np.repeat(np.arange(len(plan["drawn"])), np.minimum(table.n_ticks(plan["drawn"]), 4)))
    assert per_draw.max() <= 4 and len(plan["ticks"]) == per_draw.sum()


def test_ticks_are_drawn_in_proportion_to_one_over_root_t_without_repeats():
    rng = np.random.default_rng(0)
    _, ticks = S.draw_ticks(rng, np.full(200_000, 12), k=1)
    freq = np.bincount((ticks / 5).astype(int), minlength=13)[1:] / len(ticks)
    expected = 1 / np.sqrt(5 * np.arange(1, 13))
    assert np.allclose(freq, expected / expected.sum(), atol=0.004)
    draw, ticks = S.draw_ticks(rng, np.array([2, 4, 9, 300, 1440]), k=4)
    assert np.array_equal(np.bincount(draw), [2, 4, 4, 4, 4])
    for d in range(5):
        assert len(set(ticks[draw == d])) == len(ticks[draw == d])
