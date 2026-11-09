"""Explanations (docs/neural/spec.md §9, §15.2, §17 item 15): integrated gradients are complete on a small network and
on a planted toy whose group sums are known; deletion beats random on the toy; the baseline keeps the specimen and
resets the laboratory's state from history rows only; texts come from input values; Predictor.explain fills factors
and related specimens; a locked split needs its test run; worker counts give identical numbers; the steps double
until completeness holds; the checks feed the report; and the optional token path leaves the forward pass unchanged."""

import json
from pathlib import Path

import jsonschema
import numpy as np
import polars as pl
import pytest
import torch

import tinynet as T
from src.models.tracernet import explain as X
from src.models.tracernet import features as F
from src.models.tracernet import predict as PR
from src.models.tracernet.net import GROUPS, TracerNet
from src.models.tracernet.predictor import History, Predictor
from src.neural import report as R
from src.pipeline import store

ALERT_SCHEMA = Path(store.ROOT) / "docs" / "neural" / "schemas" / "alert.schema.json"
UNCHANGED = ("specimen", "route_unit", "time_of_day", "lab_history")


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    d = T.build_store(tmp_path_factory.mktemp("explain"))
    pred = T.predictor(d)
    val = T.runs(d, "val")
    risk = pred.score(d, val, workers=1)
    alerts = pred.alerts(risk.filter(pl.col("replicate") > 0), budget_per_shift=6,
                         tune_on=risk.filter(pl.col("replicate") == 0))
    return {"store": d, "pred": pred, "risk": risk, "alerts": alerts, "some": alerts.sort("alert_id").gather_every(6)}


@pytest.fixture(scope="module")
def explained(tiny):
    return X.explain_alerts(tiny["pred"], tiny["some"], store_dir=tiny["store"], checks=True)


# --- The network's optional token path -------------------------------------------------------------------------------

def original_attention(self, tokens, token_mask, query, token_emb=None):
    """BatchMateAttention.forward as it was before the optional token path: the reference for bit-identity (the
    default path passes no token_emb)."""
    assert token_emb is None
    n = tokens.shape[0]
    x = torch.cat([self.null.expand(n, -1, -1), self.token(tokens)], 1)
    ignore = torch.cat([token_mask.new_zeros(n, 1), ~token_mask.bool()], 1)
    h = self.norm(x)
    x = x + self.self_attn(h, h, h, key_padding_mask=ignore, need_weights=False)[0]
    kv = self.norm_kv(x)
    out, weights = self.cross(self.query(query)[:, None], kv, kv, key_padding_mask=ignore,
                              need_weights=True, average_attn_weights=True)
    return out[:, 0], weights[:, 0]


def a_batch(tiny, n=64):
    pred, d = tiny["pred"], tiny["store"]
    t = F.load_table(d, runs=T.runs(d, "val"))
    run = T.runs(d, "val", "scored")[0]
    rows = t.rows_of_run(run)[:n]
    batch = F.features_for_run(t, run, rows, np.full(len(rows), 15.0), pred.schema)
    return {k: torch.from_numpy(np.ascontiguousarray(v)) for k, v in batch.items() if k in pred.keys}


def test_the_default_forward_is_bit_identical_and_the_token_path_matches_it(tiny, monkeypatch):
    net = tiny["pred"].members[0]
    x = a_batch(tiny)
    assert x["token_mask"].any()
    with torch.no_grad():
        now = net(x)
        inputs = net.embed(x)
        inputs["token_emb"] = net.attention.token(inputs.pop("tokens"))
        via = net.forward_embedded(inputs, x["tick"], x["window"])
        monkeypatch.setattr(type(net.attention), "forward", original_attention)
        before = net(x)
    for key in ("logits", "probs", "p_miss", "logit_miss", "fused", "attention", "attention_null"):
        assert torch.equal(now[key], before[key]), key
        assert torch.equal(now[key], via[key]), key
    assert list(net.attention.state_dict()) == [
        "null", "token.0.weight", "token.0.bias", "token.2.weight", "token.2.bias", "norm.weight", "norm.bias",
        "self_attn.in_proj_weight", "self_attn.in_proj_bias", "self_attn.out_proj.weight", "self_attn.out_proj.bias",
        "norm_kv.weight", "norm_kv.bias", "query.weight", "query.bias", "cross.in_proj_weight", "cross.in_proj_bias",
        "cross.out_proj.weight", "cross.out_proj.bias"]
    Predictor.load()        # the committed artifact's state dict still loads, key for key


def test_a_set_of_null_tokens_gives_the_empty_set_output(tiny):
    net = tiny["pred"].members[0]
    x = a_batch(tiny)
    with torch.no_grad():
        nulls = net.embed(x)
        tokens = nulls.pop("tokens")
        nulls["token_emb"] = net.attention.null.expand(tokens.shape[0], tokens.shape[1], -1)
        empty = dict(x, token_mask=torch.zeros_like(x["token_mask"]))
        a = net.forward_embedded(nulls, x["tick"], x["window"])["logit_miss"]
        b = net(empty)["logit_miss"]
    assert torch.allclose(a, b, atol=1e-5)


# --- A planted toy: known group sums, completeness, deletion beats random ---------------------------------------------

PLANTED = {"route_flow": 1.0, "unit_flow": 0.6, "lab_flow": 0.3, "silence": 0.1, "window": 0.05, "batch_mates": 0.02}


def planted(tiny, sign: float = 1.0) -> tuple[Predictor, dict, dict, np.ndarray]:
    """Rung L with every weight zero except the `beyond` logit's weights on the numeric columns of six groups, and a
    batch whose input exceeds its baseline by 0.1 on exactly those columns: F rises with each, by a known share (with
    sign -1 it falls, and resetting a group to the baseline raises p_miss)."""
    schema = tiny["pred"].schema
    net = TracerNet(F.net_config(schema, rung="L"))
    cat_dim = sum(d for _, _, d in net.config.vocab)
    offset = cat_dim + net.config.n_tests
    with torch.no_grad():
        net.linear.weight.zero_()
        net.linear.bias.zero_()
        net.linear.bias[-1] = -2.0
        for j, (_, g, _) in enumerate(F.NUM_COLUMNS):
            net.linear.weight[-1, offset + j] = sign * PLANTED.get(g, 0.0)
    pred = Predictor([net], schema, None, manifest={"notice": T.NOTICE})
    n = 40
    rng = np.random.default_rng(0)
    x0 = {"cat": np.zeros((n, 7), dtype=np.int64), "tests": np.zeros((n, net.config.n_tests), dtype=np.float32),
          "num": rng.normal(size=(n, len(F.NUM_COLUMNS))).astype(np.float32),
          "fingerprint": rng.normal(size=(n, net.config.n_fingerprint)).astype(np.float32),
          "tick": np.full(n, 10.0, dtype=np.float32), "window": np.full(n, 60.0, dtype=np.float32)}
    planted_cols = np.array([g in PLANTED for _, g, _ in F.NUM_COLUMNS])
    x0["num"][:, planted_cols] = 0.0               # the beyond logit starts at its bias for every alert
    x1 = {k: v.copy() for k, v in x0.items()}
    x1["num"][:, planted_cols] += 0.1
    expected = np.array([PLANTED.get(g, 0.0) * sum(gg == g for _, gg, _ in F.NUM_COLUMNS) for g in GROUPS])
    return pred, x1, x0, expected


def test_the_planted_toy_gets_its_planted_group_sums_and_is_complete(tiny):
    pred, x1, x0, expected = planted(tiny)
    ig = X.integrated_gradients(pred, x1, x0, X.STEPS)
    delta = ig["f1"] - ig["f0"]
    assert np.all(delta > 0)
    assert np.allclose(ig["groups"].sum(1), delta, rtol=1e-3)                       # completeness
    for row in ig["groups"]:                                                        # each group's share is planted
        assert np.allclose(row / row.sum(), expected / expected.sum(), rtol=1e-4, atol=1e-7)
    assert np.all(ig["groups"][:, [GROUPS.index(g) for g in UNCHANGED]] == 0)


def test_deletion_beats_random_on_the_planted_toy(tiny):
    pred, x1, x0, _ = planted(tiny)
    ig = X.integrated_gradients(pred, x1, x0, X.STEPS)
    can = X.resettable(pred, x1, x0)
    assert can.sum(1).tolist() == [len(PLANTED)] * len(can)
    ids = [f"SP-r1-{i:06d}@10" for i in range(len(can))]
    p, chosen = X.deletion_resets(pred, x1, x0, ig["groups"], can, ids)
    top = {GROUPS[j] for j in np.flatnonzero(chosen[0, 1])}
    assert top == {"route_flow", "unit_flow", "lab_flow"}
    records = [{"deletion": {"full": r[0], "top": r[1], "random": r[2:].mean(), "groups_reset": 3}} for r in p]
    check = X.deletion_check(X.Explained(records, X.STEPS, 0.0))
    assert check["passed"] and check["share_top_beats_random"] >= 0.9
    assert check["mean_drop_top"] > check["mean_drop_random"] > 0
    again, _ = X.deletion_resets(pred, x1, x0, ig["groups"], can, ids)
    assert np.array_equal(p, again)                                                 # seeded per alert id


# --- On the store: completeness, the baseline, texts ----------------------------------------------------------------

def test_completeness_on_a_small_network(explained):
    assert explained.median_relative_gap <= X.COMPLETENESS_GAP
    assert X.completeness_check(explained)["passed"]
    for r in X.attributed(explained):
        assert abs(sum(r["groups"]) - (r["f1"] - r["f0"])) == pytest.approx(r["gap"], abs=1e-12)
        assert all(r["groups"][GROUPS.index(g)] == 0 for g in UNCHANGED)          # the same specimen and laboratory


def test_the_baseline_keeps_the_specimen_and_takes_the_state_from_history_rows(tiny):
    d, pred, a = tiny["store"], tiny["pred"], tiny["some"]
    t = F.load_table(d, runs=T.runs(d, "val"))
    rows, ticks = t.rows_of(a["specimen_id"].to_list()), a["tick_min"].to_numpy().astype(float)
    hist = np.flatnonzero(np.asarray(t.a["history"]) & np.asarray(t.a["scored"]))
    ctx: dict = {}
    raw = X._raw_at(t, rows, ticks, ctx, token_rows=True)
    base, notes = X.typical_state(t, raw, rows, ticks, hist, ctx)
    kept = np.array([g not in X.TYPICAL_GROUPS and g not in ("silence", "window") for _, g, _ in F.NUM_COLUMNS])
    assert np.array_equal(base["num"][:, kept], raw["num"][:, kept])
    for key in ("cat", "tests", "fingerprint", "tokens", "token_mask", "tick", "window"):
        assert np.array_equal(base[key], raw[key]), key
    median = np.expm1(raw["fingerprint"][:, F.FINGERPRINT_COLUMNS.index("route_p50")])
    assert np.allclose(base["num"][:, F.NUM_INDEX["silence"]], np.log1p(np.minimum(ticks, median)))
    assert np.all(base["num"][:, F.NUM_INDEX["silence"]] <= raw["num"][:, F.NUM_INDEX["silence"]])
    assert any(not np.array_equal(base[k], raw[k]) for k in F.SERIES)
    for row, tick, note in zip(rows, ticks, notes):
        probes, scope, hours = X._probes(t, int(row), float(tick), hist)
        assert note["scope"] == scope and note["probes"] == len(probes) >= X.MIN_PROBES
        assert np.asarray(t.a["history"])[probes].all()                           # never a scored replicate
        if scope == "unit and route":
            assert (np.asarray(t.a["unit"])[probes] == t.a["unit"][row]).all()


def test_texts_come_from_input_values_never_from_attribution_sizes(tiny, explained):
    d, pred, a = tiny["store"], tiny["pred"], tiny["some"]
    t = F.load_table(d, runs=T.runs(d, "val"))
    rows, ticks = t.rows_of(a["specimen_id"].to_list()), a["tick_min"].to_numpy().astype(float)
    raw = X._raw_at(t, rows, ticks, {}, token_rows=True)
    clock = np.asarray(t.a["collected"])[rows] / 60 + ticks
    s = X.texts(raw, raw, 0, pred.schema, clock[0])
    w, tick = int(raw["window"][0]), int(ticks[0])
    assert s["silence"].startswith(f"Silent {tick} min")
    assert s["window"] == f"{w - tick:,} min left of a {w:,}-min window"
    assert set(s) == set(GROUPS) and all(isinstance(v, str) and v for v in s.values())
    one = X.factors_of(np.linspace(1, -1, len(GROUPS)), s)
    other = X.factors_of(np.linspace(-1, 1, len(GROUPS)) * 7, s)
    for f in one + other:
        assert f["text"] == s[f["group"]]
    assert [f["direction"] for f in one] == ["raises"] * 3 + ["lowers"]
    assert sum(f["share"] for f in one if f["direction"] == "raises") <= 1 + 1e-12


# --- Predictor.explain, the guard, workers, steps ------------------------------------------------------------------

def test_explain_fills_factors_and_related_specimens(tiny):
    a = tiny["some"]
    out = tiny["pred"].explain(a, store_dir=tiny["store"])
    assert out.drop("factors", "related").equals(a.drop("factors", "related"))
    validator = jsonschema.Draft202012Validator(json.loads(ALERT_SCHEMA.read_text()))
    with_related = 0
    for rec in out.to_dicts():
        validator.validate(rec)
        f = rec["factors"]
        assert f and sum(x["direction"] == "raises" for x in f) <= 3 and sum(x["direction"] == "lowers" for x in f) <= 1
        assert [x["attribution"] for x in f if x["direction"] == "raises"] == \
            sorted((x["attribution"] for x in f if x["direction"] == "raises"), reverse=True)
        r = rec["related"]
        with_related += bool(r)
        assert [x["weight"] for x in r] == sorted((x["weight"] for x in r), reverse=True)
        assert all(x["specimen_id"].startswith(f"SP-r{rec['run']}-") and x["specimen_id"] != rec["specimen_id"]
                   for x in r)
    assert with_related > 0


def test_a_locked_split_is_explained_only_with_its_test_run(tiny):
    d = tiny["store"]
    run = T.runs(d, "test", "scored")[0]
    con = store.open_observed_v2(d)
    sid = con.execute("select min(specimen_id) from specimens where run = ?", [run]).fetchone()[0]
    con.close()
    fake = pl.DataFrame({"alert_id": [f"{sid}@5"], "specimen_id": [sid], "run": [run], "tick_min": [5]})
    with pytest.raises(RuntimeError, match="test lock"):
        X.explain_alerts(tiny["pred"], fake, store_dir=d)
    with pytest.raises(RuntimeError, match="test lock"):
        tiny["pred"].explain(fake, store_dir=d)
    with pytest.raises(ValueError, match="history runs"):
        X.explain_alerts(tiny["pred"], fake, store_dir=d, history=History(store_dir=d / "elsewhere"))


def test_explanations_are_bit_identical_across_worker_counts(tiny):
    """Two scenarios, so two tasks: in this process and in two worker processes."""
    d, pred = tiny["store"], tiny["pred"]
    train = T.runs(d, "train")
    risk = pred.score(d, train, workers=1)
    more = pred.alerts(risk.filter(pl.col("replicate") > 0), budget_per_shift=2,
                       tune_on=risk.filter(pl.col("replicate") == 0)).sort("alert_id").head(12)
    both = pl.concat([tiny["some"].head(12), more])
    one = X.explain_alerts(pred, both, store_dir=d, checks=True, workers=1)
    two = X.explain_alerts(pred, both, store_dir=d, checks=True, workers=2)
    assert one.records == two.records and one.steps == two.steps


def test_the_steps_double_until_the_median_alert_is_complete(tiny, monkeypatch):
    a = tiny["some"].head(5)
    assert X.explain_alerts(tiny["pred"], a, store_dir=tiny["store"]).steps == X.STEPS
    monkeypatch.setattr(X, "COMPLETENESS_GAP", 0.0)       # never met: doubling stops at the cap
    monkeypatch.setattr(X, "MAX_STEPS", 4 * X.STEPS)
    assert X.explain_alerts(tiny["pred"], a, store_dir=tiny["store"]).steps == 4 * X.STEPS


# --- The checks and the report ---------------------------------------------------------------------------------------

def test_the_checks_write_the_reports_input_and_section_7_renders_it(tiny, tmp_path):
    path = tmp_path / "results" / "neural" / "explanations.json"
    doc = X.run_checks(tiny["pred"], tiny["some"], store_dir=tiny["store"], set_name="val", out_path=path)
    assert json.loads(path.read_text()) == json.loads(json.dumps(doc))
    assert set(doc["checks"]) == {"completeness", "deletion", "randomization"}
    assert doc["checks"]["completeness"]["passed"] and doc["checks"]["randomization"]["alerts"] > 0
    assert doc["alerts"] == doc["alerts_with_factors"] == tiny["some"].height
    assert len(doc["examples"]) == 3 and doc["budget_per_shift"] == 6 and doc["weights"] == "default"
    inputs = R.default_inputs(tmp_path)
    text = R.section_explanations(R.load(inputs))
    assert "| Completeness |" in "\n".join(text) and "| Randomization |" in "\n".join(text)
    for e in doc["examples"]:
        assert f"**{e['alert_id']}**" in "\n".join(text)
    missing = R.section_explanations(R.load(R.default_inputs(tmp_path / "none")))
    assert any(line.startswith(R.NOT_RUN) for line in missing)


def test_the_checks_can_run_on_a_seeded_sample_and_the_report_says_so(tiny, tmp_path):
    path = tmp_path / "results" / "neural" / "explanations.json"
    doc = X.run_checks(tiny["pred"], tiny["some"], store_dir=tiny["store"], set_name="val", out_path=path, limit=5)
    again = X.run_checks(tiny["pred"], tiny["some"], store_dir=tiny["store"], set_name="val", limit=5)
    assert doc["alerts"] == 5 and doc["alerts_given"] == tiny["some"].height
    assert [e["alert_id"] for e in doc["examples"]] == [e["alert_id"] for e in again["examples"]]
    text = "\n".join(R.section_explanations(R.load(R.default_inputs(tmp_path))))
    assert f"a seeded sample of 5 of its {tiny['some'].height} alerts" in text


def test_deletion_fails_when_resetting_the_top_groups_raises_the_risk(tiny):
    """F002: with the planted effects reversed, resetting any planted group raises p_miss. The top 3 by attribution
    (the least negative) raise it less than random draws, which once counted as a success; a reset that does not
    lower p_miss now never does, and the check fails."""
    pred, x1, x0, _ = planted(tiny, sign=-1.0)
    ig = X.integrated_gradients(pred, x1, x0, X.STEPS)
    assert np.all(ig["f1"] < ig["f0"])
    can = X.resettable(pred, x1, x0)
    ids = [f"SP-r1-{i:06d}@10" for i in range(len(can))]
    p, _ = X.deletion_resets(pred, x1, x0, ig["groups"], can, ids)
    records = [{"deletion": {"full": r[0], "top": r[1], "random": r[2:].mean(), "groups_reset": 3}} for r in p]
    check = X.deletion_check(X.Explained(records, X.STEPS, 0.0))
    assert check["mean_drop_top"] < 0 and check["mean_drop_top"] > check["mean_drop_random"]   # the old rule's trap
    assert check["share_top_beats_random"] == 0.0 and not check["passed"]


def test_closed_window_alerts_get_one_window_factor_and_stay_out_of_the_checks(tiny, tmp_path):
    """F003: at tick == window p_miss is 1 by construction and F sits at the mask sentinel; such an alert gets no
    integrated gradients, one window factor with a null attribution, its related specimens, and no place in the
    completeness, deletion or randomization statistics, which count it apart."""
    pred, d = tiny["pred"], tiny["store"]
    at_close = (tiny["risk"].filter((pl.col("replicate") > 0) & (pl.col("tick_min") == pl.col("window_min")))
                .sort("specimen_id").head(4))
    closed = pred.alerts(PR.bounded(at_close), budget_per_shift=6, knob=0.0)       # as the records are built
    assert closed.height == 4 and (closed["tick_min"] == closed["window_min"]).all()
    ex = X.explain_alerts(pred, closed, store_dir=d)
    for r, row in zip(ex.records, closed.to_dicts()):
        assert r["unattributed"] == "closed_window" and r["groups"] is None and r["relative_gap"] is None
        assert r["factors"] == [{"group": "window", "text": f"The {row['window_min']:,}-min window has closed with no "
                                 "receipt", "share": 1.0, "attribution": None, "direction": "raises"}]
    assert ex.median_relative_gap is None and ex.steps == X.STEPS          # nothing to integrate, no doubling
    validator = jsonschema.Draft202012Validator(json.loads(ALERT_SCHEMA.read_text()))
    for rec in pred.explain(closed, store_dir=d).to_dicts():
        validator.validate(rec)
    assert any(r["related"] for r in ex.records)

    both = pl.concat([tiny["some"], closed])
    shut = int((both["tick_min"] >= both["window_min"]).sum())
    path = tmp_path / "results" / "neural" / "explanations.json"
    doc = X.run_checks(pred, both, store_dir=d, set_name="val", out_path=path)
    assert doc["closed_window_alerts"] == shut >= 4 and doc["saturated_alerts"] == 0
    assert doc["attributed_alerts"] == both.height - shut == doc["checks"]["completeness"]["alerts"]
    assert doc["checks"]["deletion"]["alerts"] == doc["attributed_alerts"]
    assert doc["checks"]["randomization"]["alerts"] <= doc["attributed_alerts"]
    assert doc["checks"]["completeness"]["median_relative_gap"] <= X.COMPLETENESS_GAP
    assert all(f["attribution"] is not None for e in doc["examples"] for f in e["factors"])
    text = "\n".join(R.section_explanations(R.load(R.default_inputs(tmp_path))))
    assert f"Closed-window alerts: {shut}." in text

    assert X.attributable(np.array([0.5, -30.0, 1e9, -1e9, np.nan, np.inf, 2e6])).tolist() == \
        [True, True, False, False, False, False, False]
    raw = {"window": np.array([60.0]), "tick": np.array([45.0])}
    rec = X.unattributed("SP-r1-000001@45", "saturated", raw, 0, {}, [])
    assert rec["factors"][0]["attribution"] is None and "15 min left of a 60-min window" in rec["factors"][0]["text"]
