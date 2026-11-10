"""The dispatcher's vocabulary and the two rules the decision model has to beat (docs/neural/spec.md §11.3).

Places and actions are the options of X2's `place` and `action` questions; every action goes to one role of
config/triage.yaml. Rule R1 is today's routing: a tube specimen is a question for the receiving technologist at the
laboratory's tube station, a hand-carried one for the courier coordinator. Rule R2 is a short list of hand-written
rules over the same facts the decision model sees (src/decide/facts.py, plus TracerNet's p_never when X2 shows it),
tried in order; the first that holds decides place and action, and the role follows from the action. Each rule is a
function with its own test (tests/test_decide.py).

R2 also answers X2's two yes-no questions, so a refused question can fall back to it:
- shared_problem: the route has gone quiet (rule `route_quiet`), receiving is behind (rule `receiving_behind`), or at
  least two other specimens on the route are past its usual 90th percentile.
- escalate: the specimen looks lost (rule `very_long`), TracerNet gives it at least an even chance of never arriving,
  or a tube route has gone quiet (a system failure is likely).

Every threshold here is an assumption (source: assumed), written before any decision-model result was seen.
"""

from __future__ import annotations

PLACES = {
    "still_at_unit": "Still on the collecting unit: not yet sent, held, or never sent.",
    "waiting_for_courier": "Sent from the unit and waiting there for a courier pickup.",
    "with_courier": "Being carried: with a courier on a round, or walked over by staff.",
    "in_tube_system": "Inside the pneumatic tube system, moving or stuck.",
    "at_lab_tube_station": "Arrived at the laboratory's tube station and not yet taken from it.",
    "misrouted_elsewhere": "Sent to the wrong tube station elsewhere in the hospital.",
    "in_lab_awaiting_scan": "Inside the laboratory, waiting in the receiving queue for its receipt scan.",
    "lost": "Lost in a tube or in transit; it will not arrive without help.",
    "other": "Somewhere else.",
}

ACTIONS = {
    "call_unit_confirm_sent": "Call the unit to confirm the specimen was sent.",
    "contact_courier_coordinator": "Contact the courier coordinator about the round carrying it.",
    "check_lab_tube_station": "Check the laboratory's tube station.",
    "check_misroute_station": "Check the station where misrouted carriers end up.",
    "check_receiving_queue": "Check the receiving queue inside the laboratory.",
    "escalate_tube_system": "Report a tube system problem to facilities.",
    "request_recollection": "Ask the unit to collect the specimen again.",
    "wait_and_recheck": "Wait and check again at the next score.",
    "other": "Something else.",
}

RECEIVING, COURIER, CHARGE_NURSE = "receiving technologist", "courier coordinator", "charge nurse"
ROLE_OF = {action: RECEIVING for action in ACTIONS} | {
    "call_unit_confirm_sent": CHARGE_NURSE, "request_recollection": CHARGE_NURSE,
    "contact_courier_coordinator": COURIER}

# Thresholds (source: assumed).
LOST_P90_MULTIPLE = 3.0      # silent this many times the route's 90th percentile: looks lost
LOST_MIN_EXTRA = 120.0       # ... and at least this many minutes past it
QUIET_GAP_MULTIPLE = 2.0     # no receipt via the route for twice the usual gap at this hour: the route is quiet
BEHIND_SHARE = 0.5           # receipts in the last hour under half the usual for this hour: receiving is behind
OVERDUE_SHARED = 2           # other specimens on the route past its 90th percentile that make a shared problem
NEVER_ESCALATE = 0.5         # TracerNet's chance it never arrives at which R2 pages a supervisor


def role(action: str) -> str:
    return ROLE_OF.get(action, RECEIVING)


def r1(facts: dict) -> dict:
    """Today's routing: by route alone. It answers neither yes-no question."""
    if facts["specimen"]["route"] == "tube":
        place, action = "at_lab_tube_station", "check_lab_tube_station"
    else:
        place, action = "with_courier", "contact_courier_coordinator"
    return {"rule": "route", "place": place, "action": action, "role": role(action),
            "shared_problem": None, "escalate": None}


# --- R2: each rule returns (place, action) when it holds, else None -----------------------------------------------

def _kind(facts: dict) -> str:
    return facts["specimen"]["route_kind"]


def very_long(facts: dict):
    """Silent three times the route's usual 90th percentile, and two hours past it: it looks lost."""
    h = facts.get("route_history")
    if h and facts["silence_min"] >= max(LOST_P90_MULTIPLE * h["p90"], h["p90"] + LOST_MIN_EXTRA):
        return "lost", "request_recollection"
    return None


def route_quiet(facts: dict):
    """No receipt via the route for more than twice its usual gap at this hour: the tube system has a problem or the
    courier is late."""
    since, gap = facts.get("route_last_receipt_min"), facts.get("route_usual_gap_min")
    if since is None or gap is None or since <= QUIET_GAP_MULTIPLE * gap:
        return None
    if _kind(facts) == "tube":
        return "in_tube_system", "escalate_tube_system"
    return "with_courier", "contact_courier_coordinator"


def receiving_behind(facts: dict):
    """Receipts in the last hour under half the usual for this hour, and the specimen past its route's 90th
    percentile: it is likely inside the laboratory waiting for its scan."""
    usual, h = facts.get("lab_usual_receipts_60"), facts.get("route_history")
    if not usual or not h or facts["silence_min"] <= h["p90"]:
        return None
    if facts["lab_receipts_60"] < BEHIND_SHARE * usual:
        return "in_lab_awaiting_scan", "check_receiving_queue"
    return None


def mates_arrived(facts: dict):
    """A batch-mate (same unit and route, collected within 20 minutes) has arrived and this one has not: at the
    laboratory's tube station on a tube route, with the courier otherwise."""
    if facts["mates"]["received"] < 1:
        return None
    if _kind(facts) == "tube":
        return "at_lab_tube_station", "check_lab_tube_station"
    return "with_courier", "contact_courier_coordinator"


def early_silence(facts: dict):
    """Silent less than the route's usual (median) arrival time and no batch-mate has arrived: still on the unit."""
    h = facts.get("route_history")
    if h and facts["silence_min"] < h["p50"] and facts["mates"]["received"] == 0:
        return "still_at_unit", "call_unit_confirm_sent"
    return None


def by_route(facts: dict):
    """Otherwise, by route: the tube station for a tube, the courier's pickup for a courier route (or the round in
    progress when the route has no rounds this shift), and the unit for a walk-in."""
    kind = _kind(facts)
    if kind == "tube":
        return "at_lab_tube_station", "check_lab_tube_station"
    if kind == "courier":
        return "waiting_for_courier", "contact_courier_coordinator"
    return "still_at_unit", "call_unit_confirm_sent"


R2_RULES = (very_long, route_quiet, receiving_behind, mates_arrived, early_silence, by_route)


def shared_problem(facts: dict) -> bool:
    return (route_quiet(facts) is not None or receiving_behind(facts) is not None
            or facts.get("route_overdue", 0) >= OVERDUE_SHARED)


def escalate(facts: dict, p_never: float | None = None) -> bool:
    quiet_tube = route_quiet(facts) is not None and _kind(facts) == "tube"
    return very_long(facts) is not None or quiet_tube or (p_never is not None and p_never >= NEVER_ESCALATE)


def r2(facts: dict, p_never: float | None = None) -> dict:
    """The first rule that holds decides place and action; the yes-no answers follow the definitions above."""
    for rule in R2_RULES:
        hit = rule(facts)
        if hit is not None:
            place, action = hit
            return {"rule": rule.__name__, "place": place, "action": action, "role": role(action),
                    "shared_problem": shared_problem(facts), "escalate": escalate(facts, p_never)}
    raise AssertionError("by_route always holds")
