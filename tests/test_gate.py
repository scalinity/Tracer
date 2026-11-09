"""The pilot gate's model rule (docs/neural/spec.md §4.10) on constructed models."""

from src.scenarios import gate


def model(cost, g2, g3, g3_edge, eligible=True):
    return {"eligible": eligible, "cost_per_accepted_usd": cost,
            "rates": {"G2": g2, "G3": g3, "G3_edge": g3_edge}}


def test_core_takes_the_cheapest_model_within_five_points_of_the_best_and_edge_reads_the_edge_briefs():
    models = {"luna": model(0.001, 0.92, 0.91, 0.83), "terra": model(0.007, 0.96, 1.0, 1.0),
              "sol": model(0.018, 1.0, 1.0, 1.0)}
    c = gate.choose(models)
    assert c["core"] == "terra"  # luna is 8 points behind on G2
    assert c["edge"] == "terra"  # luna's edge adherence is under 85%


def test_a_cheap_model_close_enough_on_both_rates_wins_the_core_strata():
    models = {"luna": model(0.001, 0.97, 0.96, 0.9), "terra": model(0.007, 1.0, 1.0, 1.0)}
    c = gate.choose(models)
    assert c["core"] == "luna" and c["edge"] == "luna"


def test_an_ineligible_model_is_never_chosen_and_no_eligible_model_fails_the_gate():
    assert gate.choose({"luna": model(0.001, 1.0, 1.0, 1.0, eligible=False), "sol": model(0.02, 1.0, 1.0, 1.0)})["core"] == "sol"
    c = gate.choose({"luna": model(0.001, 1.0, 1.0, 1.0, eligible=False)})
    assert c["core"] is None and c["edge"] is None
