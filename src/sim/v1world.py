"""The v1 world as a simulator scenario: the control that ties v2 to v1 (spec §5.9).

scenario() builds, from config/config.yaml, a scenario the engine accepts: one unit per v1
collecting unit with v1's weights, v1's hourly arrival profile and weekend factor, v1's specimen mix
and stat share, v1's leg distributions and v1's fault rates, with no disruptions. Where the v2
schema cannot say what v1 did, a `v1world` block switches the engine to v1's components:

- one categorical fault per specimen at v1's rates, and a tube route forced for tube faults;
- uniform stall and misroute delays (v1's delay_min), and misroutes that land at
  tube_station_pharmacy and wait there with no second transit;
- continuous station emptying, so the station dwell is v1's station_dwell lognormal, and an
  instant receipt scan at the desk (ample receiving), so nothing is added after the dwell;
- hand carries walk at v1's hand_carry_transit lognormal.

Its trails pass src.generator.validate.validate (on records.v1_view), each leg matches its
configured median and 90th percentile, and its breach share sits with v1's. It also serves as a
world with no shared context for the negative control (spec §8.8). It runs as scenario index 9000;
index 9001 is reserved, so the run keys of its 20 replicates (190000 to 190019) never collide with
a library scenario's (library indices are below 9000).
"""

from __future__ import annotations

from src.generator import config

SCENARIO_INDEX = 9000
RESERVED_INDEX = 9001
REPLICATES = 20
SCENARIO_ID = "v1-world"
LINE_ID = "v1_tube"
UNIT_KINDS = {"ed": "ed", "icu": "icu", "med_surg": "med_surg", "periop": "periop", "clinic": "clinic_onsite"}
AMPLE_STAFF = 12  # the schema's largest desk; unused, since the v1 world's receipt scan is instant


def _leg(legs: dict, name: str) -> tuple[float, float]:
    return legs[name]["median_min"], legs[name]["p90_min"]


def scenario(loaded: dict | None = None, *, specimens_per_weekday: float | None = None) -> dict:
    """The v1-world scenario. `specimens_per_weekday` scales volume (v1's own is the profile's sum);
    the v1 world has no shared resources, so volume changes nothing about any one specimen."""
    loaded = loaded or config.load()
    cfg = loaded["config"]
    legs, rates = cfg["legs"], cfg["arrivals"]["rate_per_hour"]
    dispatch, transit = _leg(legs, "dispatch_wait"), _leg(legs, "tube_transit")
    accession, plating = _leg(legs, "accession_wait"), _leg(legs, "plating_wait")
    staff = {"day": AMPLE_STAFF, "evening": AMPLE_STAFF, "night": AMPLE_STAFF}
    return {
        "scenario_id": SCENARIO_ID,
        "family_id": SCENARIO_ID,
        "split": "control",
        "scenario_index": SCENARIO_INDEX,
        "source": "assumed",
        "volume": {"specimens_per_weekday": specimens_per_weekday or sum(rates), "hourly_profile": list(rates),
                   "weekend_factor": cfg["arrivals"]["weekend_factor"]},
        "specimen_mix": {k: e["weight"] for k, e in cfg["specimen_mix"]["entries"].items()},
        "units": [{"unit_id": name, "kind": UNIT_KINDS[name], "volume_share": weight,
                   "stat_fraction": cfg["priority"]["stat_fraction"], "route": "mixed", "tube_line_id": LINE_ID,
                   "courier_route_id": None, "tube_share_if_mixed": cfg["route"]["tube_probability"],
                   "dispatch": {"mode": "immediate", "median_min": dispatch[0], "p90_min": dispatch[1],
                                "batch_interval_min": None},
                   "not_dispatched_rate": 0.0, "mix_tilt": []}
                  for name, weight in cfg["collecting_units"]["weights"].items()],
        "tube_system": {"lines": [{"line_id": LINE_ID, "transit_median_min": transit[0], "transit_p90_min": transit[1],
                                   "misroute_rate": 0.0, "lost_rate": 0.0}],
                        "station_empty_interval_min": {"day": 2, "evening": 2, "night": 2},
                        "stalled_rate": 0.0, "stall_extra_min": {"median": 60, "p90": 120},
                        "misroute_found_min": {"median": 60, "p90": 90}, "source": "assumed"},
        "couriers": {"routes": [], "on_call_runner": False, "on_call_response_median_min": None},
        "receiving": {"staff_by_shift": {"weekday": dict(staff), "weekend": dict(staff)}, "shift_pattern": "7-15-23",
                      "scan_service_min": {"median": 0.3, "p90": 0.6}, "shift_change_gap_min": 0, "source": "assumed"},
        "processing": {"accession_min": {"median": accession[0], "p90": accession[1]},
                       "plating_min": {"median": plating[0], "p90": plating[1]}, "instruments": [],
                       "staff_pull_from_receiving": 0.0, "source": "assumed"},
        "independent_faults": {"never_collected_rate": 0.0, "content_failure_rate": 0.0},
        "disruptions": [],
        "edge_tags": [],
        "v1world": {
            "faults": {name: f["rate"] for name, f in cfg["faults"].items()},
            "delays": {name: list(cfg["faults"][name]["delay_min"]) for name in config.FAULTS_WITH_DELAY},
            "station_dwell": list(_leg(legs, "station_dwell")),
            "hand_carry_transit": list(_leg(legs, "hand_carry_transit")),
            "misroute_station": "tube_station_pharmacy",
        },
    }
