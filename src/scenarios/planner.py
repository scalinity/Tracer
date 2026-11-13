"""The coverage planner: which laboratory-weeks get requested (docs/neural/spec.md §4.2 and §4.12).

plan(job, seed) returns a deterministic list of briefs. Each brief names one cell of the grid
(stratum, archetype, size band, transport mix, staffing, failure focus, edge tags), a few numeric
anchors drawn from seeded ranges, one quirk from a list of 40 texture lines, and the sentences
the scenario must include or avoid. Every include or avoid sentence that code can check is paired
with a named predicate and its parameters in `checks`; src/scenarios/validate.py runs them.

Balance. Within a stratum the planner turns each factor's shares into whole-number targets whose
rounding is seeded (every target is the floor or the ceiling of its exact value, so every marginal
is within one count) and fills the cells one factor at a time with a small max-flow assignment, so
the compatibility table holds and the targets are still met. If a tiny stratum cannot meet every
target under the table, it retries with another rounding and keeps the closest.

Jobs: O1 is 24 briefs (6 per stratum), O1b is 12 more (3 per stratum) that differ from O1's, O2 is
the main run (its count is set at run time from pilot costs), O3 is round 2 and takes a weak-spot
list: 60% of its briefs in the listed slices, 25% general edge briefs, 15% under-covered cells.
Out-of-distribution cells (archetype multi_site_system and failure focus scanner_downtime) appear
only in O2 and carry `ood: true`.

Every level, share, range and threshold lives in config/scenario_planner.yaml, all assumed.
"""

from __future__ import annotations

import zlib
from collections import deque
from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "scenario_planner.yaml"

STRATA = ("normal", "stressed", "disrupted", "edge")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MAX_ATTEMPTS = 60          # reroundings tried for a stratum before the closest plan is kept
SLICE_KEYS = ("archetype", "stratum", "failure_focus", "edge_tag", "size_band", "transport_mix", "staffing")
TARGET_KEYS = SLICE_KEYS + ("disruption_type", "count")
# A weak-spot slice named by disruption type maps to the failure focus that produces it.
FOCUS_OF_DISRUPTION = {
    "tube_line_outage": "tube_outage", "tube_line_degraded": "tube_degradation",
    "station_unattended": "station_neglect", "receiving_short_staffed": "understaffing_spikes",
    "courier_missed_rounds": "courier_unreliable", "courier_delayed": "courier_unreliable",
    "offsite_route_closed": "courier_unreliable", "unit_surge": "surge", "unit_batching_hold": "unit_batching",
    "instrument_down": "instrument_coupling"}
CELL_FACTORS = ("stratum", "archetype", "size_band", "transport_mix", "staffing", "failure_focus")


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


# --- Seeded building blocks -----------------------------------------------------------------

def _rng(seed: int, job: str, *parts: int) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence([seed, zlib.crc32(job.encode()), *parts]))


def balanced_counts(shares: dict[str, float], n: int, rng: np.random.Generator) -> dict[str, int]:
    """Whole-number targets that sum to n, each the floor or the ceiling of its exact value.

    The levels that round up are drawn with probability proportional to their fractional parts.
    """
    total = sum(shares.values())
    exact = {k: v / total * n for k, v in shares.items()}
    counts = {k: int(np.floor(x + 1e-9)) for k, x in exact.items()}
    left = n - sum(counts.values())
    fractions = {k: exact[k] - counts[k] for k in exact if exact[k] - counts[k] > 1e-9}
    if left > 0:
        keys = sorted(fractions)
        weights = np.array([fractions[k] for k in keys])
        for k in rng.choice(keys, size=left, replace=False, p=weights / weights.sum()):
            counts[str(k)] += 1
    return counts


def assign(allowed: list[list[str]], targets: dict[str, int],
           rng: np.random.Generator) -> tuple[list[str], int]:
    """Give every item a level from its allowed list so level counts equal the targets.

    Items are placed in seeded order. A full level is relieved by moving another item to a level
    with room (a breadth-first search over levels, the augmenting path of a max-flow). If no
    assignment meets the targets, the item goes to its allowed level with the most room and the
    shortfall is counted in the second return value.
    """
    count = {level: 0 for level in targets}
    members: dict[str, list[int]] = {level: [] for level in targets}
    placed: list[str | None] = [None] * len(allowed)
    violations = 0

    def put(i: int, level: str) -> None:
        placed[i] = level
        count[level] += 1
        members[level].append(i)

    for i in rng.permutation(len(allowed)):
        i = int(i)
        room = [lv for lv in allowed[i] if count[lv] < targets[lv]]
        if room:
            weights = np.array([targets[lv] - count[lv] for lv in room], dtype=float)
            put(i, str(rng.choice(room, p=weights / weights.sum())))
            continue
        # No room anywhere this item may go: search for a chain of moves that frees a slot.
        parent: dict[str, tuple[str, int] | None] = {lv: None for lv in allowed[i]}
        queue = deque(allowed[i])
        free = None
        while queue and free is None:
            here = queue.popleft()
            for j in members[here]:
                for there in allowed[j]:
                    if there in parent:
                        continue
                    parent[there] = (here, j)
                    if count[there] < targets[there]:
                        free = there
                        break
                    queue.append(there)
                if free is not None:
                    break
        if free is None:
            violations += 1
            put(i, max(allowed[i], key=lambda lv: targets[lv] - count[lv]))
            continue
        level = free
        while parent[level] is not None:
            origin, moved = parent[level]
            members[origin].remove(moved)
            members[level].append(moved)
            placed[moved] = level
            count[level] += 1
            count[origin] -= 1
            level = origin
        put(i, level)
    return [str(p) for p in placed], violations


def _choose(rng: np.random.Generator, levels: list[str], shares: dict[str, float], tally: dict[str, int]) -> str:
    """The level furthest behind its share so far, with a seeded nudge to break ties and runs."""
    total = sum(tally.values()) + 1
    norm = sum(shares[lv] for lv in levels)
    score = {lv: shares[lv] / norm * total - tally.get(lv, 0) + rng.random() * 0.5 for lv in levels}
    pick = max(levels, key=score.get)
    tally[pick] = tally.get(pick, 0) + 1
    return pick


# --- The grid -------------------------------------------------------------------------------

def level_shares(stratum: str, ood: bool) -> dict[str, dict[str, float]]:
    """The share table of every factor in one stratum, with the OOD cells added when the job has them."""
    cfg = config()
    archetype = dict(cfg["archetypes"])
    if ood:
        keep = 1 - cfg["ood"]["archetype_share"]
        archetype = {k: v * keep for k, v in archetype.items()}
        archetype[cfg["ood"]["archetype"]] = cfg["ood"]["archetype_share"]
    focus = dict(cfg["failure_focus"][stratum])
    scanner = cfg["ood"]["focus_share"].get(stratum, 0)
    if ood and scanner:
        focus = {k: v * (1 - scanner) for k, v in focus.items()}
        focus[cfg["ood"]["focus"]] = scanner
    staffing = cfg["staffing"]["normal_shares"] if stratum == "normal" else cfg["staffing"]["shares"]
    return {"archetype": archetype, "size_band": dict(cfg["size_bands"]["shares"]),
            "failure_focus": focus, "transport_mix": dict(cfg["transport_mix"]["shares"]),
            "staffing": dict(staffing)}


def sizes_for(archetype: str) -> list[str]:
    return list(config()["compatibility"]["size_by_archetype"][archetype])


def transports_for(focus: str) -> list[str]:
    forbidden = config()["compatibility"]["transport_forbidden_by_focus"].get(focus, [])
    return [t for t in config()["transport_mix"]["shares"] if t not in forbidden]


def staffings_for(size_band: str) -> list[str]:
    limits = config()["compatibility"]["staffing_sizes"]
    return [s for s in config()["staffing"]["shares"] if size_band in limits.get(s, [size_band])]


def tag_fits(tag: str, focus: str, transport: str, ood: bool) -> bool:
    rule = config()["edge_tags"][tag]
    if rule.get("ood_only") and not ood:
        return False
    if rule.get("focus") is not None and focus not in rule["focus"]:
        return False
    return rule.get("transport") is None or transport in rule["transport"]


def compatibility_errors(cell: dict) -> list[str]:
    """Every way a cell breaks the compatibility table (empty means it holds)."""
    cfg, errors = config(), []
    if cell["size_band"] not in sizes_for(cell["archetype"]):
        errors.append(f"{cell['archetype']} cannot be {cell['size_band']}")
    if cell["transport_mix"] not in transports_for(cell["failure_focus"]):
        errors.append(f"{cell['failure_focus']} cannot pair with {cell['transport_mix']}")
    if cell["staffing"] not in staffings_for(cell["size_band"]):
        errors.append(f"{cell['staffing']} cannot pair with a {cell['size_band']} laboratory")
    if cell["failure_focus"] not in cfg["failure_focus"][cell["stratum"]] \
            and cell["failure_focus"] != cfg["ood"]["focus"]:
        errors.append(f"{cell['failure_focus']} does not occur in the {cell['stratum']} stratum")
    if cell["stratum"] == "normal" and cell["failure_focus"] not in ("random_faults_only", "unit_batching"):
        errors.append("a normal week pairs only with random faults or one mild chronic focus")
    if cell["failure_focus"] == cfg["ood"]["focus"] and cell["archetype"] == cfg["ood"]["archetype"]:
        errors.append("the two out-of-distribution cells do not overlap")
    ood = is_ood(cell)
    for tag in cell["edge_tags"]:
        if not tag_fits(tag, cell["failure_focus"], cell["transport_mix"], ood):
            errors.append(f"edge tag {tag} does not fit {cell['failure_focus']} with {cell['transport_mix']}")
    if cell["edge_tags"] and cell["stratum"] != "edge":
        errors.append("edge tags belong to the edge stratum only")
    if cell["stratum"] == "edge" and not 1 <= len(cell["edge_tags"]) <= 3:
        errors.append("an edge brief carries 1 to 3 edge tags")
    return errors


def is_ood(cell: dict) -> bool:
    ood = config()["ood"]
    return cell["archetype"] == ood["archetype"] or cell["failure_focus"] == ood["focus"]


def _assign_tags(cells: list[dict], rng: np.random.Generator) -> None:
    """Give every edge cell 1 to 3 compatible tags, always the least-used compatible tag next."""
    cfg = config()
    shares = {str(k): v for k, v in cfg["edge_tag_counts"].items()}
    wanted = balanced_counts(shares, len(cells), rng)
    sizes = [int(s) for s in rng.permutation([int(k) for k, c in wanted.items() for _ in range(c)])]
    tally = {t: 0 for t, r in cfg["edge_tags"].items() if not r.get("ood_only")}
    for cell, size in zip(cells, sizes):
        if cell["failure_focus"] == cfg["ood"]["focus"]:
            cell["edge_tags"] = ["scanner_backlog"]
            continue
        picked: list[str] = []
        for _ in range(size):
            fits = [t for t in tally if t not in picked and tag_fits(t, cell["failure_focus"], cell["transport_mix"], False)]
            if not fits:
                break
            low = min(tally[t] for t in fits)
            tag = str(rng.choice([t for t in fits if tally[t] == low]))
            picked.append(tag)
            tally[tag] += 1
        cell["edge_tags"] = picked


def plan_stratum(job: str, seed: int, stratum: str, n: int, *, ood: bool, avoid: set | None = None) -> list[dict]:
    """n cells for one stratum, each factor's marginal within one count of its target."""
    if n == 0:
        return []
    cfg, shares = config(), level_shares(stratum, ood)
    best, best_violations = None, None
    for attempt in range(MAX_ATTEMPTS):
        rng = _rng(seed, job, STRATA.index(stratum), attempt)
        violations = 0
        cells = [{"stratum": stratum} for _ in range(n)]

        def fill(factor: str, allowed_for) -> None:
            nonlocal violations
            targets = balanced_counts(shares[factor], n, rng)
            levels, bad = assign([[lv for lv in allowed_for(cell) if lv in targets] for cell in cells], targets, rng)
            violations += bad
            for cell, level in zip(cells, levels):
                cell[factor] = level

        fill("archetype", lambda c: list(shares["archetype"]))
        fill("size_band", lambda c: [s for s in sizes_for(c["archetype"])])
        fill("failure_focus", lambda c: [f for f in shares["failure_focus"]
                                         if not (f == cfg["ood"]["focus"] and c["archetype"] == cfg["ood"]["archetype"])])
        fill("transport_mix", lambda c: transports_for(c["failure_focus"]))
        fill("staffing", lambda c: staffings_for(c["size_band"]))
        for cell in cells:
            cell["edge_tags"] = []
        if stratum == "edge":
            _assign_tags(cells, rng)
        sub = rng.integers(0, 2**31 - 1, size=n)
        for cell, s in zip(cells, sub):
            cell["sub_seed"] = int(s)
        if avoid and any(cell_key(c) in avoid for c in cells):
            violations += 1000   # an O1b brief repeating an O1 cell: try again
        if best is None or violations < best_violations:
            best, best_violations = cells, violations
        if violations == 0:
            break
    return best


def cell_key(cell: dict) -> tuple:
    return tuple(cell[f] for f in CELL_FACTORS) + (tuple(cell["edge_tags"]),)


# --- Briefs ---------------------------------------------------------------------------------

def _hour_anchor(rng: np.random.Generator, focus: str) -> int:
    cfg = config()["anchors"]
    if focus == "station_neglect":
        lo, hi = cfg["station_neglect_hours"]
        return int(rng.integers(lo, hi + 1))
    bands = cfg["disruption_hour_bands"]
    band = bands[int(rng.choice(len(bands), p=[b[2] for b in bands]))]
    return int(rng.integers(band[0], band[1] + 1))


def _needs_tube(cell: dict) -> bool:
    cfg = config()
    return cell["failure_focus"] in cfg["needs_tube_focus"] + ["compound"] or any(
        t.startswith(("outage", "unnoticed", "slow", "misroute", "night_station")) for t in cell["edge_tags"])


def _needs_courier(cell: dict) -> bool:
    return cell["failure_focus"] in config()["needs_courier_focus"] or any(
        t in ("weekend_courier_no_show", "offsite_road_closure", "courier_capacity_crunch") for t in cell["edge_tags"])


def draw_anchors(cell: dict) -> dict:
    """Numeric anchors from seeded ranges: the model realizes them but is not held to them."""
    cfg = config()
    rng = np.random.default_rng(cell["sub_seed"])
    lo, hi = cfg["size_bands"]["specimens_per_weekday"][cell["size_band"]]
    inset = cfg["size_bands"]["inset"] * (hi - lo)
    volume = int(5 * round(rng.uniform(lo + inset, hi - inset) / 5))
    a = cfg["anchors"]

    def between(rng_pair, floor=0):
        return max(floor, int(rng.integers(rng_pair[0], rng_pair[1] + 1)))

    tube = between(a["tube_line_count"][cell["transport_mix"]], 1 if _needs_tube(cell) else 0)
    van = between(a["offsite_van_count"][cell["transport_mix"]], 1 if "offsite_road_closure" in cell["edge_tags"] else 0)
    courier = max(between(a["courier_route_count"][cell["transport_mix"]], 1 if _needs_courier(cell) else 0), van)
    patterns = list(a["shift_pattern_shares"])
    weights = np.array([a["shift_pattern_shares"][p] for p in patterns])
    interval_lo, interval_hi = a["courier_round_interval_day_min"]
    weekday = rng.random() < a["disruption_weekday_share"]
    return {
        "specimens_per_weekday": volume,
        "unit_count": between(a["unit_count"][cell["size_band"]]),
        "tube_line_count": tube,
        "courier_route_count": courier,
        "offsite_van_count": van,
        "courier_round_interval_day_min": int(5 * rng.integers(interval_lo // 5, interval_hi // 5 + 1)),
        "shift_pattern": str(rng.choice(patterns, p=weights / weights.sum())),
        "disruption_day": int(rng.integers(0, 5) if weekday else rng.integers(5, 7)),
        "disruption_hour": _hour_anchor(rng, cell["failure_focus"]),
    }


def _anchor_clause(mode: str, anchors: dict) -> str:
    tolerance = config()["anchors"]["hour_tolerance_h"]
    hour = f"{anchors['disruption_hour']:02d}:00"
    if mode == "hour":
        return f" that starts within {tolerance} hours of {hour} each day"
    return f" that starts on {WEEKDAYS[anchors['disruption_day']]} within {tolerance} hours of {hour}"


def checks_for(cell: dict, anchors: dict) -> list[dict]:
    """The include and avoid rules of a cell as {kind, predicate, params, sentence}."""
    cfg = config()
    out: list[dict] = []

    def add(kind: str, rule: dict, **extra) -> None:
        out.append({"kind": kind, "predicate": rule["predicate"], "params": dict(rule["params"]) | extra,
                    "sentence": rule["sentence"]})

    add("include", cfg["transport_rules"][cell["transport_mix"]])
    add("include", cfg["staffing_rules"][cell["staffing"]])
    for rule in cfg["stratum_rules"][cell["stratum"]]:
        add(rule["kind"], rule)
    anchored = cell["stratum"] in ("stressed", "disrupted")
    for rule in cfg["focus_rules"][cell["failure_focus"]]:
        mode = rule.get("anchored") if anchored else None
        if mode == "day_hour":
            extra = {"day": anchors["disruption_day"], "hour": anchors["disruption_hour"]}
        elif mode == "hour":
            extra = {"hour": anchors["disruption_hour"]}
        else:
            extra = {}
        if extra:
            extra["hour_tolerance_h"] = cfg["anchors"]["hour_tolerance_h"]
        entry = dict(rule) | {"sentence": rule["sentence"] + (_anchor_clause(mode, anchors) if mode else "")}
        add("include", entry, **extra)
    for tag in cell["edge_tags"]:
        add("include", {"predicate": f"tag_{tag}", "params": {}, "sentence": f"edge tag {tag}: {cfg['edge_tags'][tag]['sentence']}"})
    if cell["failure_focus"] != cfg["ood"]["focus"]:
        add("avoid", cfg["avoid_scanner"])
    return out


def make_brief(cell: dict) -> dict:
    """A brief without its job, id and quirk (the job assigns those)."""
    cfg = config()
    anchors = draw_anchors(cell)
    checks = checks_for(cell, anchors)
    avoid = [c["sentence"] for c in checks if c["kind"] == "avoid"] + list(cfg["avoid_text"])
    return {
        "stratum": cell["stratum"], "archetype": cell["archetype"], "size_band": cell["size_band"],
        "transport_mix": cell["transport_mix"], "staffing": cell["staffing"],
        "failure_focus": cell["failure_focus"], "edge_tags": list(cell["edge_tags"]), "anchors": anchors,
        "must_include": [c["sentence"] for c in checks if c["kind"] == "include"], "must_avoid": avoid,
        "ood": is_ood(cell), "checks": checks}


def _interleave(counts: dict[str, int]) -> list[str]:
    """Strata in a smooth weighted order, so every prefix of a job is balanced."""
    total = sum(counts.values())
    credit = {s: 0.0 for s in counts}
    used = {s: 0 for s in counts}
    out = []
    for _ in range(total):
        for s in counts:
            credit[s] += counts[s] / total
        pick = max((s for s in counts if used[s] < counts[s]), key=lambda s: credit[s])
        credit[pick] -= 1
        used[pick] += 1
        out.append(pick)
    return out


def _finish(job: str, seed: int, briefs: list[dict]) -> list[dict]:
    """Number the briefs, deal out the quirks and order the keys."""
    quirks = config()["quirks"]
    order = _rng(seed, job, 9999).permutation(len(quirks))
    done = []
    for i, brief in enumerate(briefs):
        origin = brief.pop("origin", "grid")
        head = {"brief_id": f"{job}-{i + 1:04d}", "job": job}
        done.append(head | {k: brief[k] for k in ("stratum", "archetype", "size_band", "transport_mix", "staffing",
                                                  "failure_focus", "edge_tags", "anchors")}
                    | {"quirk": quirks[int(order[i % len(quirks)])], "must_include": brief["must_include"],
                       "must_avoid": brief["must_avoid"], "ood": brief["ood"], "origin": origin,
                       "checks": brief["checks"]})
    return done


# --- Counts -----------------------------------------------------------------------------------

def stratum_counts(job: str, n: int | dict | None, seed: int) -> dict[str, int]:
    spec = config()["jobs"][job]
    if isinstance(n, dict):
        unknown = set(n) - set(STRATA)
        if unknown:
            raise ValueError(f"unknown strata {sorted(unknown)}")
        return {s: int(n.get(s, 0)) for s in STRATA}
    if n is None and "counts" in spec:
        return dict(spec["counts"])
    total = int(spec["total"] if n is None else n)
    return balanced_counts(config()["strata"], total, _rng(seed, job, 7777))


def _grid_briefs(job: str, seed: int, counts: dict[str, int], *, ood: bool, avoid: set | None) -> list[dict]:
    cells = {s: plan_stratum(job, seed, s, counts[s], ood=ood, avoid=avoid) for s in STRATA}
    nxt = {s: 0 for s in STRATA}
    briefs = []
    for s in _interleave({k: v for k, v in counts.items() if v}):
        briefs.append(make_brief(cells[s][nxt[s]]))
        nxt[s] += 1
    return briefs


# --- Round 2 ----------------------------------------------------------------------------------

def _validate_target(target: dict) -> dict:
    unknown = set(target) - set(TARGET_KEYS) - {"under_covered"}
    if unknown:
        raise ValueError(f"unknown target keys {sorted(unknown)}; use {TARGET_KEYS}")
    pins = {k: v for k, v in target.items() if k in SLICE_KEYS}
    if "disruption_type" in target:
        pins["failure_focus"] = FOCUS_OF_DISRUPTION[target["disruption_type"]]
    cfg = config()
    levels = {"archetype": cfg["archetypes"], "stratum": cfg["strata"], "size_band": cfg["size_bands"]["shares"],
              "transport_mix": cfg["transport_mix"]["shares"], "staffing": cfg["staffing"]["shares"],
              "edge_tag": {t: 1 for t, r in cfg["edge_tags"].items() if not r.get("ood_only")}}
    for key, value in pins.items():
        if key == "failure_focus":
            if value not in cfg["focus_rules"] or value == cfg["ood"]["focus"]:
                raise ValueError(f"failure_focus {value!r} cannot be planned in round 2")
        elif value not in levels[key]:
            raise ValueError(f"{key} {value!r} is not a level (out-of-distribution cells are never planned in round 2)")
    return pins


def _pinned_cell(rng: np.random.Generator, pins: dict, tally: dict[str, dict[str, int]]) -> dict:
    """One cell with the pinned factors fixed and the rest filled by whichever level is furthest behind."""
    cfg = config()
    strata = cfg["strata"]
    if "stratum" in pins:
        stratum = pins["stratum"]
    elif "edge_tag" in pins:
        stratum = "edge"
    elif "failure_focus" in pins:
        stratum = _choose(rng, [s for s in STRATA if pins["failure_focus"] in cfg["failure_focus"][s]], strata,
                          tally.setdefault("stratum", {}))
    else:
        stratum = _choose(rng, list(STRATA), strata, tally.setdefault("stratum", {}))
    shares = level_shares(stratum, False)
    if "failure_focus" in pins and pins["failure_focus"] not in shares["failure_focus"]:
        raise ValueError(f"failure_focus {pins['failure_focus']} does not occur in the {stratum} stratum")
    if "edge_tag" in pins and stratum != "edge":
        raise ValueError("an edge_tag slice belongs to the edge stratum")

    def pick(factor: str, levels: list[str]) -> str:
        if factor in pins:
            if pins[factor] not in levels:
                raise ValueError(f"the slice {pins} breaks the compatibility table at {factor}")
            return pins[factor]
        if not levels:
            raise ValueError(f"the slice {pins} leaves no {factor} level")
        return _choose(rng, levels, shares[factor], tally.setdefault(factor, {}))

    staffing_sizes = [sz for sz in shares["size_band"] if "staffing" not in pins or pins["staffing"] in staffings_for(sz)]
    archetypes = [a for a in shares["archetype"] if any(sz in sizes_for(a) for sz in staffing_sizes)
                  and ("size_band" not in pins or pins["size_band"] in sizes_for(a))]
    archetype = pick("archetype", archetypes)
    size = pick("size_band", [sz for sz in sizes_for(archetype) if sz in staffing_sizes])
    foci = [f for f in shares["failure_focus"]
            if "transport_mix" not in pins or pins["transport_mix"] in transports_for(f)]
    if "edge_tag" in pins:
        foci = [f for f in foci if tag_fits(pins["edge_tag"], f, pins.get("transport_mix", "mixed"), False)]
    focus = pick("failure_focus", foci)
    transport = pick("transport_mix", [t for t in transports_for(focus)
                                       if "edge_tag" not in pins or tag_fits(pins["edge_tag"], focus, t, False)])
    staffing = pick("staffing", [s for s in shares["staffing"] if s in staffings_for(size)])
    cell = {"stratum": stratum, "archetype": archetype, "size_band": size, "transport_mix": transport,
            "staffing": staffing, "failure_focus": focus, "edge_tags": [], "sub_seed": int(rng.integers(0, 2**31 - 1))}
    if stratum == "edge":
        _assign_tags([cell], rng)
        if "edge_tag" in pins and pins["edge_tag"] not in cell["edge_tags"]:
            cell["edge_tags"] = [pins["edge_tag"]] + cell["edge_tags"][:2]
    return cell


def _round2(job: str, seed: int, n: int, targets: list[dict]) -> list[dict]:
    shares = config()["round2_shares"]
    n_slice = int(round(shares["slices"] * n))
    n_edge = int(round(shares["general_edge"] * n))
    n_cover = n - n_slice - n_edge
    slices = [t for t in targets if "under_covered" not in t]
    cover = [t for t in targets if "under_covered" in t]
    if not slices:
        raise ValueError("round 2 needs at least one slice in targets")
    rng = _rng(seed, job, 5555)
    weights = {str(k): float(t.get("count", 1)) for k, t in enumerate(slices)}
    per_slice = balanced_counts(weights, n_slice, rng)
    briefs: list[dict] = []
    for k, target in enumerate(slices):
        pins, tally = _validate_target(target), {}
        for _ in range(per_slice[str(k)]):
            brief = make_brief(_pinned_cell(rng, pins, tally))
            brief["origin"] = f"slice:{k}"
            briefs.append(brief)
    for cell in plan_stratum(job, seed, "edge", n_edge, ood=False):
        brief = make_brief(cell)
        brief["origin"] = "edge"
        briefs.append(brief)
    cells = [pins for t in cover for pins in t["under_covered"]]
    if cells:
        tally: dict[str, dict[str, int]] = {}
        for i in range(n_cover):
            brief = make_brief(_pinned_cell(rng, _validate_target(cells[i % len(cells)]), tally))
            brief["origin"] = "coverage"
            briefs.append(brief)
    else:
        general = balanced_counts({s: v for s, v in config()["strata"].items() if s != "edge"}, n_cover, rng)
        for cell in (c for s in general for c in plan_stratum(job, seed, s, general[s], ood=False)):
            brief = make_brief(cell)
            brief["origin"] = "coverage"
            briefs.append(brief)
    return [briefs[int(i)] for i in _rng(seed, job, 6666).permutation(len(briefs))]


# --- Coverage fill ----------------------------------------------------------------------------

def _fill(job: str, seed: int, cells: dict[str, int]) -> list[dict]:
    """Briefs pinned to stratum-by-archetype cells ({"normal/community_hospital": 84, ...}), every other factor
    filled by the level furthest behind its share, as round 2 fills its slices. Out-of-distribution cells are refused."""
    rng = _rng(seed, job, 4444)
    briefs: list[dict] = []
    for key in sorted(cells):
        stratum, archetype = key.split("/")
        pins, tally = _validate_target({"stratum": stratum, "archetype": archetype}), {}
        for _ in range(int(cells[key])):
            brief = make_brief(_pinned_cell(rng, pins, tally))
            brief["origin"] = f"coverage:{key}"
            briefs.append(brief)
    return [briefs[int(i)] for i in _rng(seed, job, 6666).permutation(len(briefs))]


# --- The entry point --------------------------------------------------------------------------

def plan(job: str, seed: int, *, n: int | dict | None = None, targets: list | None = None,
         exclude_ids: set | None = None) -> list[dict]:
    """Deterministic briefs for a job.

    n is a total (split by the stratum shares), a dict of counts per stratum, or None for the job's
    default. targets (O3 only) lists weak-spot slices, for example {"archetype": "cancer_center",
    "stratum": "disrupted", "count": 40}, {"failure_focus": "surge"} or {"edge_tag": "misroute_storm"};
    a {"under_covered": [slice, ...]} entry supplies the under-covered cells. exclude_ids drops
    briefs by id after planning, so every other brief keeps its id.
    """
    spec = config()["jobs"].get(job)
    if spec is None:
        raise ValueError(f"unknown job {job!r}; use one of {sorted(config()['jobs'])}")
    if targets is not None and job != "O3":
        raise ValueError("targets belong to job O3")
    if job == "O3":
        if not targets:
            raise ValueError("job O3 is planned from a weak-spot list: pass targets")
        total = int(spec["total"] if n is None else n)
        briefs = _round2(job, seed, total, targets)
    elif "cells" in spec:
        if n is not None:
            raise ValueError(f"job {job} is planned from its cells; it takes no n")
        briefs = _fill(job, seed, spec["cells"])
    else:
        avoid = None
        if job == "O1b":
            avoid = {cell_key(b) for b in plan("O1", seed)}
        counts = stratum_counts(job, n, seed)
        briefs = _grid_briefs(job, seed, counts, ood=spec["ood"], avoid=avoid)
    out = _finish(job, seed, briefs)
    return [b for b in out if not exclude_ids or b["brief_id"] not in exclude_ids]
