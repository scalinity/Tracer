"""Round 2's weak spots and the O3 plan (docs/neural/spec.md §4.12, Appendix B): the slice ranking on a constructed
validation set, the 60/25/15 brief mix, the count formula (it reproduces O2's committed count), validation-only reads,
and a plan that `plan --job O3` reads."""

import collections
import json
import math
from pathlib import Path

import duckdb
import polars as pl
import pytest
import yaml

from src.neural import pipeline, weakspots as W
from src.pipeline import store
from src.scenarios import planner, runner

ROOT = Path(__file__).resolve().parents[1]
JOBS = runner.load_jobs(ROOT / "config" / "openai_jobs.yaml")
# A two-arm mix (sol-medium writes the edge stratum), so the count's per-arm weighting is exercised whatever
# the live config chooses.
MIXED = json.loads(json.dumps(JOBS))
MIXED["jobs"]["O3"]["arms"]["edge"] = ["sol-medium"]
CFG = W.load_config()

# Three validation scenarios, 100 scored specimens each with 40 breaches. TracerNet is sharp in S1, no better than
# the base rate in S2 (p_miss 0.4 against a 0.4 breach share) and worse than it in S3 (0.6). S3's specimens have
# day-long windows; S2's carry a tube line outage.
GROUPS = {"S1": ("cancer_center", "normal", 60, [], None), "S2": ("community_hospital", "disrupted", 60,
                                                                     ["tube_line_outage"], 0.4),
          "S3": ("safety_net_urban", "stressed", 1440, [], 0.6)}
H40 = -(0.4 * math.log(0.4) + 0.6 * math.log(0.6))


def frames():
    rows, risk = [], []
    for sid, (archetype, stratum, window, types, p) in GROUPS.items():
        for i in range(100):
            breach = i < 40
            spec = f"{sid}-{i:03d}"
            rows.append({"specimen_id": spec, "scenario_id": sid, "run": int(sid[1:]), "breach": breach,
                         "window_min": window, "receipt_min": None if breach else 30.0, "route_kind": "tube",
                         "archetype": archetype, "stratum": stratum, "edge_tags": [],
                         "disruption_types": types})
            q = p if p is not None else (0.9 if breach else 0.1)
            risk += [{"specimen_id": spec, "run": int(sid[1:]), "scenario_id": sid, "replicate": 1, "tick_min": t,
                      "p_miss": q} for t in (5.0, 10.0)]
            risk.append({"specimen_id": spec, "run": int(sid[1:]), "scenario_id": sid, "replicate": 1,
                         "tick_min": 300.0, "p_miss": 0.999 if not breach else 0.001})   # past 240 min: never read
    schema = {"edge_tags": pl.List(pl.Utf8), "disruption_types": pl.List(pl.Utf8), "receipt_min": pl.Float64}
    return pl.DataFrame(rows, schema_overrides=schema), pl.DataFrame(risk)


def evaluation(set_name="val", with_tracernet=True):
    methods = {"window_rule": {"weighted": False}, "analytic_global": {"weighted": True},
               "analytic_local": {"weighted": True}}
    if with_tracernet:
        methods["tracernet"] = {"weighted": True}

    def cell(tn, wr, ag, al):
        return {"tracernet": {"3-2-1": {"median_warning_all_breaches_min": tn}},
                "window_rule": {"none": {"median_warning_all_breaches_min": wr}},
                "analytic_global": {"3-2-1": {"median_warning_all_breaches_min": ag}},
                "analytic_local": {"3-2-1": {"median_warning_all_breaches_min": al}}}
    return {"set": set_name, "methods": methods, "counts": {"clusters": 3}, "settings": {"headline": "6/shift"},
            "slices": {"volume_tercile": {"high": cell(10.0, 25.0, 30.0, 20.0)},
                       "route_kind": {"tube": cell(5.0, 0.0, 0.0, 0.0)}}}


@pytest.fixture(scope="module")
def doc():
    rows, risk = frames()
    manifest = {"coverage": {"cells": {"normal/academic_medical_center": {"count": 314, "target": 326, "ok": True},
                                       "normal/multi_site_system": {"count": 1, "target": 106, "ok": True},
                                       "stressed/cancer_center": {"count": 300, "target": 299, "ok": True}},
                             "edge_tags": {"holiday_skeleton": {"count": 53, "target": 54, "ok": True}}},
                "jobs": {"O2": {"arms": {s: ["terra-low"] for s in planner.STRATA}, "spent_usd": 43.865879,
                                "counts": {"accepted": 6023, "briefs": 6101}}}}
    pilot = {"models": {"sol-medium": {"briefs": 24, "accepted": 24, "cost_per_accepted_usd": 0.017452}}}
    return W.analyze(rows, risk.lazy(), evaluation(), cfg=CFG, jobs_cfg=MIXED, manifest=manifest, pilot=pilot)


def test_the_excess_is_the_log_loss_over_the_slice_base_rate(doc):
    by = {(x["dimension"], x["slice"]): x for x in doc["ranking"]}
    sharp = by[("archetype_stratum", "cancer_center / normal")]
    flat = by[("archetype_stratum", "community_hospital / disrupted")]
    worse = by[("archetype_stratum", "safety_net_urban / stressed")]
    assert sharp["base_rate"] == worse["base_rate"] == 0.4 and sharp["breaches"] == 40
    assert sharp["base_rate_log_loss"] == pytest.approx(H40)
    assert sharp["tracernet_log_loss"] == pytest.approx(-math.log(0.9))      # the tick at 300 min is past 240
    assert flat["excess"] == pytest.approx(0.0, abs=1e-12)
    assert worse["excess"] == pytest.approx(0.4 * -math.log(0.6) + 0.6 * -math.log(0.4) - H40)
    assert worse["breach_share"] == pytest.approx(1 / 3)
    assert worse["loss_ratio"] == pytest.approx(worse["tracernet_log_loss"] / H40)
    assert worse["score"] == pytest.approx(worse["loss_ratio"] / 3)


def test_slices_rank_by_loss_ratio_times_share_and_the_top_four_plannable_are_chosen(doc):
    ranking = doc["ranking"]
    assert [x["score"] for x in ranking] == sorted((x["score"] for x in ranking), reverse=True)
    assert all(x["score"] == pytest.approx(x["loss_ratio"] * x["breach_share"]) for x in ranking)
    chosen = [(x["dimension"], x["slice"]) for x in doc["chosen"]]
    passed = {(x["dimension"], x["slice"]) for x in doc["passed_over"]}
    first = [(x["dimension"], x["slice"]) for x in ranking if (x["dimension"], x["slice"]) not in passed][:4]
    assert len(chosen) == 4 and chosen == first
    last = max(i for i, x in enumerate(ranking) if (x["dimension"], x["slice"]) in set(chosen))
    assert all(i < last for i, x in enumerate(ranking) if (x["dimension"], x["slice"]) in passed)


def test_when_tracernet_beats_every_base_rate_a_big_slice_where_it_adds_little_ranks_first():
    """Spec §4.12's excess x share would rank the small slice first here (its excess is closest to zero); the loss
    ratio ranks the slice holding most breaches, where TracerNet adds least over the base rate, first."""
    from src.neural import weakspots as W
    big = {"dimension": "route_kind", "slice": "tube", "ranked": True, "breach_share": 0.6,
           "tracernet_log_loss": 0.54, "base_rate_log_loss": 0.6}
    small = {"dimension": "route_kind", "slice": "courier", "ranked": True, "breach_share": 0.02,
             "tracernet_log_loss": 0.297, "base_rate_log_loss": 0.3}
    for s in (big, small):
        s["excess"] = s["tracernet_log_loss"] - s["base_rate_log_loss"]
        s["loss_ratio"] = s["tracernet_log_loss"] / s["base_rate_log_loss"]
        s["score"] = s["loss_ratio"] * s["breach_share"]
    assert big["excess"] * big["breach_share"] < small["excess"] * small["breach_share"] < 0
    assert [s["slice"] for s in W.rank([small, big])] == ["tube", "courier"]


def test_the_warning_gap_comes_from_the_evaluation_where_it_slices(doc):
    by = {(x["dimension"], x["slice"]): x for x in doc["ranking"]}
    gap = by[("volume_tercile", "high")]["warning_gap"]
    assert gap == {"available": True, "tracernet_min": 10.0, "best_baseline": "analytic_global",
                   "best_baseline_min": 30.0, "gap_min": -20.0}
    assert not by[("archetype_stratum", "cancer_center / normal")]["warning_gap"]["available"]
    assert "not by type" in by[("disruption_type", "tube_line_outage")]["warning_gap"]["why"]


def test_slices_below_thirty_breaches_are_listed_but_not_ranked():
    rows, risk = frames()
    small = rows.with_columns(edge_tags=pl.when(pl.col("specimen_id").is_in(["S1-000", "S1-050"]))
                              .then(pl.lit(["misroute_storm"])).otherwise(pl.col("edge_tags")))
    losses = W.specimen_losses(small, risk.lazy(), 240)
    table = W.slice_table(losses, small, ["edge_tag"], 30)
    storm = next(x for x in table if x["slice"] == "misroute_storm")
    assert storm["breaches"] == 1 and not storm["ranked"]
    assert all(x["slice"] != "misroute_storm" for x in W.rank(table))


def test_the_o3_targets_are_the_slices_then_the_under_covered_cells_without_ood(doc):
    assert doc["targets"][:4] == [x["pins"] | {"count": 1} for x in doc["chosen"]]
    assert doc["targets"][4] == {"under_covered": [{"stratum": "normal", "archetype": "academic_medical_center"},
                                                   {"edge_tag": "holiday_skeleton"}]}


def test_the_briefs_follow_the_60_25_15_mix_with_equal_slice_shares(doc):
    n = doc["briefs"]
    s = doc["plan"]
    assert s["briefs"] == n and s["ood_briefs"] == 0
    assert s["by_origin"] == {"slice": round(0.6 * n), "edge": round(0.25 * n),
                              "coverage": n - round(0.6 * n) - round(0.25 * n)}
    per_slice = list(s["by_slice"].values())
    assert len(per_slice) == 4 and max(per_slice) - min(per_slice) <= 1


def test_the_count_is_appendix_b_with_each_arm_weighted_by_its_strata(doc):
    c, m = doc["count"], doc["measured"]
    assert m["terra-low"]["cost_per_accepted_usd"] == pytest.approx(43.865879 / 6023)
    assert m["terra-low"]["acceptance"] == pytest.approx(6023 / 6101) and "job O2" in m["terra-low"]["source"]
    assert m["sol-medium"]["acceptance"] == 1.0 and "pilot" in m["sol-medium"]["source"]
    provisional = collections.Counter(b["stratum"] for b in planner.plan("O3", 301, n=600, targets=doc["targets"]))
    edge = provisional["edge"] / 600
    assert c["arm_share"] == pytest.approx({"sol-medium": edge, "terra-low": 1 - edge})
    per_brief = edge * 1.0 + (1 - edge) * 6023 / 6101
    spend = edge * 0.017452 + (1 - edge) * 43.865879 / 6101
    accepted = math.floor(18.0 * 0.95 / (spend / per_brief))
    assert (c["accepted"], c["briefs"]) == (accepted, math.ceil(accepted / per_brief))


def test_the_count_formula_reproduces_o2s_committed_count():
    """O2: ceil(floor(44 x 0.95 / $0.007150) / (23 / 24)) = 6,101 briefs (config/openai_jobs.yaml)."""
    jobs = json.loads(json.dumps(JOBS))
    jobs["jobs"]["O3"] |= {"cap_usd": 44.0, "arms": {s: ["terra-low"] for s in planner.STRATA}}
    measured = {"terra-low": {"cost_per_accepted_usd": 0.00715, "acceptance": 23 / 24}}
    c = W.count([{"archetype": "cancer_center", "count": 1}], jobs_cfg=jobs, measured=measured, cap_share=0.95)
    assert (c["accepted"], c["briefs"]) == (5846, 6101)


def test_measured_costs_fall_back_to_the_pilot_and_refuse_when_missing():
    pilot = {"models": {"terra-low": {"briefs": 24, "accepted": 23, "cost_per_accepted_usd": 0.00715},
                        "sol-medium": {"briefs": 24, "accepted": 24, "cost_per_accepted_usd": 0.017452}}}
    m = W.measured_costs(None, pilot, JOBS)
    assert m["terra-low"]["acceptance"] == pytest.approx(23 / 24) and "pilot" in m["terra-low"]["source"]
    with pytest.raises(W.WeakSpotError, match="no measured cost"):
        W.measured_costs(None, {"models": {}}, JOBS)


def test_under_covered_cells_skip_out_of_distribution_ones_and_put_the_largest_deficit_first(doc):
    assert [c["pins"] for c in doc["under_covered"]] == [{"stratum": "normal", "archetype": "academic_medical_center"},
                                                         {"edge_tag": "holiday_skeleton"}]
    assert [c["deficit"] for c in doc["under_covered"]] == [12, 1]


# --- Validation only, end to end ---------------------------------------------------------------------------------------

def tiny_store(d: Path, splits: dict) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(d / store.OBSERVED_NAME))
    con.execute("create table runs (scenario_id varchar, split varchar)")
    con.executemany("insert into runs values (?, ?)", list(splits.items()))
    con.close()
    return d


def files(tmp_path, *, risk_extra=None, ev=None):
    rows, risk = frames()
    if risk_extra is not None:
        risk = pl.concat([risk, pl.DataFrame([risk_extra])], how="vertical_relaxed")
    rows.write_parquet(tmp_path / "rows.parquet")
    (tmp_path / "risk").mkdir(exist_ok=True)
    risk.write_parquet(tmp_path / "risk" / "part-0000.parquet")
    (tmp_path / "evaluation.json").write_text(json.dumps(ev or evaluation()))
    manifest = {"coverage": {"cells": {}}, "ledger": {"spent_usd_by_job": {"O1": 0.6, "O2": 43.87},
                                                      "spent_usd_total": 44.47, "cap_usd": 76.43}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    tiny_store(tmp_path / "store", {"S1": "val", "S2": "val", "S3": "val", "T1": "test"})
    return dict(rows_path=tmp_path / "rows.parquet", risk=str(tmp_path / "risk" / "*.parquet"),
                evaluation_path=tmp_path / "evaluation.json", store_dir=tmp_path / "store",
                out_dir=tmp_path / "generation", manifest_path=tmp_path / "manifest.json",
                ledger_path=tmp_path / "no-ledger.sqlite", stages_dir=tmp_path / "stages")


def test_a_test_scenario_in_the_risk_table_is_refused_before_anything_is_computed(tmp_path):
    kw = files(tmp_path, risk_extra={"specimen_id": "T1-000", "run": 9, "scenario_id": "T1", "replicate": 1,
                                     "tick_min": 5.0, "p_miss": 0.5})
    with pytest.raises(W.WeakSpotError, match="not validation scenarios"):
        W.weakspots_command(out=lambda *_: None, **kw)
    assert not (tmp_path / "generation").exists()


@pytest.mark.parametrize("ev, match", [(evaluation("test"), "validation only"),
                                       (evaluation(with_tracernet=False), "no TracerNet")])
def test_an_evaluation_of_another_set_or_without_tracernet_is_refused(tmp_path, ev, match):
    with pytest.raises(W.WeakSpotError, match=match):
        W.weakspots_command(out=lambda *_: None, **files(tmp_path, ev=ev))


def test_rows_of_a_test_scenario_are_refused():
    with pytest.raises(W.WeakSpotError, match="not validation scenarios"):
        W.check_validation(evaluation(), {"S1", "T1"}, {"S1"}, {"S1", "S2"})


def test_the_command_writes_a_plan_that_plan_job_o3_reads(tmp_path):
    kw = files(tmp_path)
    lines = []
    assert W.weakspots_command(out=lines.append, **kw) == 0
    doc = json.loads((tmp_path / "generation" / "round2-plan.json").read_text())
    md = (tmp_path / "generation" / "round2.md").read_text()
    assert "safety_net_urban / stressed" in md and "No test set was read" in md and "$43.8700" in md
    assert W.weakspots_command(out=lines.append, **kw) == 0 and "up to date" in lines[-1]
    # plan --job O3 reads the record: its targets and its count
    paths = runner.Paths(root=tmp_path, jobs_config=ROOT / "config" / "openai_jobs.yaml")
    paths.weak_spot_plan.parent.mkdir(parents=True, exist_ok=True)
    paths.weak_spot_plan.write_text(json.dumps(doc))
    briefs = pipeline.Pipeline(paths=paths, seams=runner.real_seams(), out=lambda *_: None).plan("O3")
    assert len(briefs) == doc["briefs"] == doc["plan"]["briefs"]
    assert collections.Counter(b["origin"].split(":")[0] for b in briefs) == doc["plan"]["by_origin"]
    assert briefs == planner.plan("O3", 301, n=doc["briefs"], targets=doc["targets"])


def test_weak_spot_settings_are_the_spec_values():
    assert (CFG["min_breaches"], CFG["top"], CFG["cap_share"], CFG["budget"]) == (30, 4, 0.95, "6/shift")
    assert yaml.safe_load((ROOT / "config" / "scenario_planner.yaml").read_text())["round2_shares"] == \
        {"slices": 0.60, "general_edge": 0.25, "under_covered": 0.15}
