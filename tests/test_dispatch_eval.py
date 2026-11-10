"""Scoring the decision layer (docs/neural/spec.md §11.3, §15.2): the place table of §5.4 on constructed truth
segments, the appropriate-action table, X2's metrics, counterfactual and adoption rule, X3's comparison and X1's
control AUROC and agreement, on constructed cases."""

from datetime import datetime, timedelta

import duckdb
import pytest

from src.decide import rules
from src.pipeline import dispatch_eval as de, evaluate

T0 = datetime(2026, 1, 6, 8, 0)


def truth_store(tmp_path, specimens: dict) -> object:
    """A store folder whose truth file holds only what truth_places_v2 reads: each specimen's collection time and
    its segments (state, substate, minutes from collection to start and end, end None for open-ended)."""
    con = duckdb.connect(str(tmp_path / "truth.duckdb"))
    con.execute("create table truth_specimens (specimen_id varchar, collected_at timestamp)")
    con.execute('create table truth_segments (specimen_id varchar, seq bigint, state varchar, start timestamp, '
                '"end" timestamp, location varchar, substate varchar)')
    for sid, segs in specimens.items():
        con.execute("insert into truth_specimens values (?, ?)", [sid, T0])
        for seq, (state, sub, a, b) in enumerate(segs):
            con.execute("insert into truth_segments values (?, ?, ?, ?, ?, 'x', ?)",
                        [sid, seq, state, T0 + timedelta(minutes=a), None if b is None else T0 + timedelta(minutes=b),
                         sub])
    con.close()
    return tmp_path


def test_the_place_table_of_section_5_4_on_constructed_segments(tmp_path):
    d = truth_store(tmp_path, {
        "SP-r1-1": [("at_unit", "pre_dispatch", 0, 10), ("in_tube", "transit", 10, 20), ("at_station", "lab_station", 20, 30),
                    ("stalled_at_station", "overlooked", 30, 40), ("at_station", "receiving_queue", 40, 50),
                    ("at_receiving_bench", None, 50, None)],
        "SP-r1-2": [("at_unit", "awaiting_pickup", 0, 10), ("hand_carry", "in_transit", 10, 20),
                    ("hand_carry", "scanner_backlog", 20, 30), ("hand_carry", "receiving_queue", 30, 40)],
        "SP-r1-3": [("at_unit", "outage_hold", 0, 10), ("in_tube", "stuck", 10, 20), ("misrouted", "wrong_station", 20, 30),
                    ("lost", "in_tube", 30, None)],
        "SP-r1-4": [("at_unit", "not_dispatched", 0, None)],
        "SP-r1-5": [("hand_carry", "walk", 0, 10), ("stalled_at_station", "unattended", 10, 20), ("lost", "in_transit", 20, None)],
    })
    ticks = {"SP-r1-1": [5, 15, 25, 35, 45, 55], "SP-r1-2": [5, 15, 25, 35], "SP-r1-3": [5, 15, 25, 300],
             "SP-r1-4": [500], "SP-r1-5": [5, 15, 25]}
    got = evaluate.truth_places_v2(ticks, d)
    assert got == {
        "SP-r1-1": ["still_at_unit", "in_tube_system", "at_lab_tube_station", "at_lab_tube_station",
                    "in_lab_awaiting_scan", None],
        "SP-r1-2": ["waiting_for_courier", "with_courier", "in_lab_awaiting_scan", "in_lab_awaiting_scan"],
        "SP-r1-3": ["still_at_unit", "in_tube_system", "misrouted_elsewhere", "lost"],
        "SP-r1-4": ["still_at_unit"],
        "SP-r1-5": ["with_courier", "at_lab_tube_station", "lost"]}
    assert set(p for v in got.values() for p in v if p) <= set(rules.PLACES)


@pytest.mark.parametrize("place,lost_in,actions", [
    ("still_at_unit", None, {"call_unit_confirm_sent"}),
    ("waiting_for_courier", None, {"contact_courier_coordinator", "call_unit_confirm_sent"}),
    ("with_courier", None, {"contact_courier_coordinator"}),
    ("in_tube_system", None, {"escalate_tube_system", "wait_and_recheck"}),
    ("at_lab_tube_station", None, {"check_lab_tube_station"}),
    ("misrouted_elsewhere", None, {"check_misroute_station"}),
    ("in_lab_awaiting_scan", None, {"check_receiving_queue"}),
    ("lost", "tube", {"request_recollection", "escalate_tube_system"}),
    ("lost", "transit", {"request_recollection", "contact_courier_coordinator"}),
])
def test_the_appropriate_action_table_of_section_11_3(place, lost_in, actions):
    assert de.appropriate_actions(place, lost_in) == actions
    assert actions <= set(rules.ACTIONS)
    assert de.appropriate_roles(place, lost_in) == {rules.role(a) for a in actions}


def test_every_place_but_other_has_an_appropriate_action():
    assert {p for p in rules.PLACES if not de.appropriate_actions(p, "tube")} == {"other"}


# --- X2 ----------------------------------------------------------------------------------------------------------------

CFG = {"counterfactual": {"found_after_alert_min": 10, "received_after_found_min": 5, "source": "assumed"},
       "decisions": {"noul_yes": 0.7}}


def decision(place, action, shared=False, escalate=False, p=None, source="drex-max"):
    return {"source": source, "place": place, "action": action, "role": rules.role(action),
            "shared_problem_p": p, "escalate_p": p, "shared_problem": shared, "escalate": escalate}


def alerts(n_scen=6, per=10, model=("in_tube_system", "escalate_tube_system"), r1=("at_lab_tube_station",
           "call_unit_confirm_sent"), r2=("still_at_unit", "call_unit_confirm_sent"), tick=100):
    rows, labels = [], {}
    for s in range(n_scen):
        for i in range(per):
            aid = f"SP-r{s}-{i}@{tick}"
            shared = i % 2 == 0
            rows.append({"alert_id": aid, "specimen_id": aid[:-4], "scenario_id": f"S{s}", "tick_min": tick,
                         "dispatch": decision(*model, shared=shared, escalate=shared, p=0.9 if shared else 0.1),
                         "r1": decision(*r1), "r2": decision(*r2, shared=not shared, escalate=False)})
            labels[aid] = {"place": "in_tube_system", "lost_in": "tube", "shared": shared, "unrecoverable": shared,
                           "breach": True, "receipt_min": None, "window_min": 120, "route_kind": "tube"}
    return rows, labels


def test_x2_metrics_on_a_constructed_set_and_the_adoption_rule_holds():
    rows, labels = alerts()
    s = de.x2_scores(rows, labels, "test", CFG, resamples=200)
    m = s["methods"]
    assert m["model"]["place_accuracy"]["point"] == 1.0 and m["R1"]["place_accuracy"]["point"] == 0.0
    assert m["model"]["action_appropriate"]["point"] == 1.0 and m["R2"]["role_accuracy"]["point"] == 0.0
    assert m["model"]["breaches_averted_per_100_alerts"]["point"] == 100.0          # 100 + 10 + 5 <= 120
    assert m["R1"]["breaches_averted_per_100_alerts"]["point"] == 0.0
    diff = s["differences"]["model - R2"]["role_accuracy"]
    assert diff["point"] == 1.0 and diff["low"] == 1.0 and diff["share_ahead"] == 1.0
    assert s["adoption"]["holds"] is True and s["adoption"]["binding"] is True
    assert s["adoption"]["dispatch_source"] == "decision model"
    assert s["shared_problem"]["model_auroc"]["point"] == 1.0 and s["shared_problem"]["R2_auroc"]["point"] == 0.0
    assert s["escalate"]["model_precision"]["point"] == 1.0 and s["escalate"]["model_recall"]["point"] == 1.0
    assert s["scenarios"] == 6 and s["slices"]["by_place"]["in_tube_system"]["alerts"] == 60


def test_the_adoption_rule_fails_when_a_rule_does_as_well_and_is_binding_only_on_test():
    rows, labels = alerts(r2=("in_tube_system", "wait_and_recheck"))
    s = de.x2_scores(rows, labels, "test", CFG, resamples=100)
    assert s["adoption"]["checks"]["R2"]["holds"] is False and s["adoption"]["holds"] is False
    assert s["adoption"]["dispatch_source"] == "rules R2"
    val = de.x2_scores(*alerts(), "val", CFG, resamples=50)
    assert val["adoption"]["binding"] is False and val["adoption"]["dispatch_source"] is None


def test_the_adoption_rule_needs_five_points_and_an_interval_above_zero():
    def scores(point, low):
        return {"differences": {f"model - {m}": {"role_accuracy": {"point": point, "low": low}} for m in ("R1", "R2")}}
    assert de.adoption(scores(0.05, 0.001), True)["holds"] is True
    assert de.adoption(scores(0.049, 0.01), True)["holds"] is False
    assert de.adoption(scores(0.08, 0.0), True)["holds"] is False
    assert de.adoption(scores(None, None), True)["holds"] is False


def test_the_counterfactual_needs_time_left_and_counts_lost_specimens_as_recollections():
    rows, labels = alerts(n_scen=2, per=2, tick=110)
    late = de.x2_scores(rows, labels, "val", CFG, resamples=0)
    assert late["methods"]["model"]["breaches_averted_per_100_alerts"]["point"] == 0.0      # 110 + 15 > 120
    rows, labels = alerts(n_scen=2, per=2, model=("lost", "request_recollection"))
    for v in labels.values():
        v["place"] = "lost"
    lost = de.x2_scores(rows, labels, "val", CFG, resamples=0)
    assert lost["methods"]["model"]["recollections_started_per_100_alerts"]["point"] == 100.0
    assert lost["methods"]["model"]["breaches_averted_per_100_alerts"]["point"] == 0.0


def test_alerts_with_no_true_place_are_counted_and_left_out():
    rows, labels = alerts(n_scen=2, per=3)
    labels[rows[0]["alert_id"]]["place"] = None
    s = de.x2_scores(rows, labels, "val", CFG, resamples=0)
    assert (s["alerts"], s["scored_alerts"], s["alerts_without_a_place"]) == (6, 5, 1)


def test_a_locked_split_is_scored_only_with_its_test_run(tmp_path):
    with pytest.raises(de.ne.LockError):
        de.x2_labels([], "test", tmp_path)
    with pytest.raises(de.ne.LockError):
        de.x3_labels([], "test_ood", tmp_path, test_run=object())


# --- X3 and X1 ----------------------------------------------------------------------------------------------------------

def test_x3_compares_the_three_forecasts_on_the_same_ticks():
    rows, labels, tn, an = [], {}, {}, {}
    for s in range(5):
        for i in range(20):
            iid = f"SP-r{s}-{i}@30"
            miss = i % 2 == 0
            rows.append({"item_id": iid, "scenario_id": f"S{s}", "stratum": "late|short|up to 30",
                         "p_miss": 0.9 if miss else 0.1})
            labels[iid] = {"miss": miss}
            tn[iid], an[iid] = 0.5, (0.6 if miss else 0.4)
    rows.append({"item_id": "refused", "scenario_id": "S0", "stratum": "x", "p_miss": None})
    s = de.x3_scores(rows, labels, tn, an, "test", resamples=100)
    assert s["scored_ticks"] == 100 and s["refused_or_unanswered"] == 1 and s["scenarios"] == 5
    assert s["methods"]["decision_model"]["auroc"]["point"] == 1.0 and s["methods"]["tracernet"]["auroc"]["point"] == 0.5
    assert s["methods"]["decision_model"]["brier"]["point"] == pytest.approx(0.01)
    assert s["methods"]["analytic_local"]["brier"]["point"] == pytest.approx(0.16)
    d = s["differences"]["decision_model - tracernet"]["auroc"]
    assert d["point"] == 0.5 and d["share_ahead"] == 1.0
    assert s["methods"]["decision_model"]["ece"]["point"] == pytest.approx(0.1)


def test_x1_scores_the_controls_the_review_list_and_agreement_with_the_planner():
    rows = []
    for i in range(8):
        rows.append({"kind": "control", "item_id": f"control:S{i}", "corruption": "rates_10x" if i < 4 else "clean",
                     "plausibility": {"score": 0.5 if i < 4 else 3.5, "probabilities": {}}})
    probs = {"0": 0.1, "1": 0.6, "2": 0.2, "3": 0.05, "4": 0.05}
    rows.append({"kind": "label", "item_id": "label:S10", "scenario_id": "S10",
                 "plausibility": {"score": 1.3, "probabilities": probs},
                 "dominant_failure": {"choice": "tube_outage", "probabilities": {}},
                 "planner": {"failure_focus": "tube_outage", "edge_tags": ["unnoticed_outage"]},
                 "edges": {"unnoticed_outage": 0.9}})
    rows.append({"kind": "label", "item_id": "label:S11", "scenario_id": "S11",
                 "plausibility": {"score": 3.9, "probabilities": {"0": 0, "1": 0, "2": 0, "3": 0.1, "4": 0.9}},
                 "dominant_failure": {"choice": "surge", "probabilities": {}},
                 "planner": {"failure_focus": "compound", "edge_tags": []}, "edges": {}})
    s = de.x1_scores(rows, resamples=100)
    assert s["control_auroc"]["point"] == 1.0 and s["soft_filter"]["works"] is True
    assert s["soft_filter"]["review_list"] == ["S10"]
    assert s["failure_focus_agreement"]["share"] == 0.5
    assert s["failure_focus_agreement"]["confusion"] == {"tube_outage": {"tube_outage": 1}, "compound": {"surge": 1}}
    assert s["edge_tag_agreement"] == {"asked": 1, "share_yes": 1.0, "by_tag": {"unnoticed_outage": {"asked": 1, "yes": 1}}}
    rows[0]["plausibility"]["score"] = 3.6                          # one corrupted control looks plausible
    for r in rows[1:4]:
        r["plausibility"]["score"] = 3.6
    weak = de.x1_scores(rows, resamples=50)
    assert weak["control_auroc"]["point"] < 0.8 and weak["soft_filter"]["works"] is False
    assert weak["soft_filter"]["review_list"] == []


def test_f010_slices_carry_intervals_and_every_metric_and_say_why_one_is_undefined():
    rows, labels = alerts(n_scen=4, per=6)
    for r in rows:
        if labels[r["alert_id"]]["shared"]:
            labels[r["alert_id"]]["route_kind"] = "courier"     # the courier slice holds shared delays only
    s = de.x2_scores(rows, labels, "val", CFG, resamples=100)
    tube = s["slices"]["by_route_kind"]["tube"]
    for m in ("model", "R1", "R2"):
        for metric in ("place_accuracy", "action_appropriate", "role_accuracy", "breaches_averted_per_100_alerts",
                       "recollections_started_per_100_alerts"):
            assert {"point", "low", "high"} <= set(tube["methods"][m][metric])
    assert tube["alerts"] == 12 and tube["scenarios"] == 4
    assert {"low", "high", "share_ahead"} <= set(tube["differences"]["model - R2"]["role_accuracy"])
    courier = s["slices"]["by_route_kind"]["courier"]
    assert courier["shared_problem"]["model_auroc"]["undefined"] == "no negatives"
    assert courier["shared_problem"]["model_auroc"]["point"] is None
    assert tube["shared_problem"]["model_recall"]["undefined"] == "no positives"
    assert set(s["slices"]["by_place"]) == {"in_tube_system"}
    assert "escalate" in s["slices"]["by_place"]["in_tube_system"]


def test_x2_scores_record_each_methods_choices_most_frequent_first():
    rows, labels = alerts(n_scen=2, per=4)
    rows[0]["dispatch"] = decision("still_at_unit", "call_unit_confirm_sent")
    s = de.x2_scores(rows, labels, "val", CFG, resamples=0)
    model = s["choices"]["model"]
    assert list(model["action"].items()) == [("escalate_tube_system", 7 / 8), ("call_unit_confirm_sent", 1 / 8)]
    assert sum(model["place"].values()) == pytest.approx(1.0)
    assert s["choices"]["R2"]["action"] == {"call_unit_confirm_sent": 1.0}
