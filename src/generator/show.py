"""Write a plain-language review sheet of generated timelines, for the clinical reviewer.

    python -m src.generator.show --n 20

Reads the accepted specimens in data/claude/pilot/run-*.jsonl and writes review-sheet.md beside
them. The sample is deterministic: about a third of the entries are specimens with a planted
problem, shared round-robin across the kinds present, and the rest are normal specimens spread
across specimen types and routes. The sheet uses no commands or code names, and it says plainly
that it is made-up data.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from itertools import zip_longest
from pathlib import Path

from . import prompt

PASS_SHARE = 0.85  # 17 of 20 (plan task K2)

UNITS = {"ed": "the emergency department", "icu": "the ICU", "med_surg": "a medical-surgical ward",
         "periop": "the operating room area", "clinic": "an outpatient clinic"}
ROUTES = {"tube": "the pneumatic tube", "hand_carry": "a runner carrying it"}
STATES = {"at_unit": "waiting on the unit", "in_tube": "travelling in the tube",
          "at_station": "at the lab's tube station, waiting to be collected",
          "stalled_at_station": "stuck at the lab's tube station", "misrouted": "sent to the wrong tube station",
          "hand_carry": "being carried by a runner", "at_receiving_bench": "at the receiving bench, waiting to be logged in",
          "accessioned": "logged in, waiting to be plated", "plated": "plated on media", "lost": "lost",
          "not_collected": "never collected"}
ACTIVITIES = {"ordered": "ordered", "collected": "collected", "received": "received at the lab",
              "accessioned": "logged in at the lab", "plated": "plated on media", "rejected": "rejected by the lab"}
FAULTS = {"none": "", "lost_in_tube": "The specimen was lost in the tube system.",
          "stalled_at_station": "The tube carrier sat at the lab's tube station before anyone collected it.",
          "misrouted": "The tube carrier went to the wrong station and had to be re-sent.",
          "not_dispatched": "The specimen was collected but never sent to the lab.",
          "never_collected": "The sample was ordered but never collected.",
          "content_failure": "The lab rejected the specimen after it arrived."}

INTRO = """\
# {n} specimen timelines to review

These are made-up specimens, written by a computer program. None of them is a real patient, and no data from any workplace is used. Each entry tells one specimen's story twice: what really happened, which no scanner records, and what the scanners saw.

For each entry, please decide whether it could happen in a real hospital laboratory with a pneumatic tube system. Tick Plausible or Not plausible. If it is not plausible, add one short phrase saying why, for example "receipt too soon after dispatch". The times between steps matter most. At least {passing} of the {n} plausible counts as a pass.

Some entries are deliberately failures, such as a stuck or lost specimen, so you can judge those too. They are more common here than in a real laboratory.
"""


def clock(text: str) -> str:
    return datetime.fromisoformat(text).strftime("%-I:%M %p")


def day(text: str) -> str:
    return datetime.fromisoformat(text).strftime("%A, %B %-d")


def minutes(a: str, b: str) -> int:
    return round((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 60)


def span(m: int) -> str:
    if m < 120:
        return f"{m} min"
    return f"{m // 60} h" + (f" {m % 60} min" if m % 60 else "")


def pretty(location: str) -> str:
    return location.replace("_", " ")


def entry(number: int, record: dict) -> str:
    brief, trail = record["brief"], record["trail"]
    fixed, label, fault = brief["fixed"], brief["label"], brief["planted_fault"]
    lines = [f"## {number}. {fixed['type']}",
             f"Tests: {', '.join(fixed['tests'])}. Collected in {UNITS[fixed['unit']]} on {day(fixed['collected_at'])} at "
             f"{clock(fixed['collected_at'])}. Priority: {fixed['priority']}. Sent by {ROUTES[fixed['route']]}."]
    if FAULTS[fault]:
        lines.append(f"What went wrong: {FAULTS[fault]}")
    lines += ["", "What really happened (no scanner records this):"]
    for s in trail["truth_segments"]:
        if s["end"] is None:
            lines.append(f"- from {clock(s['start'])} onward: {STATES[s['state']]} ({pretty(s['location'])})")
        else:
            lines.append(f"- {clock(s['start'])} to {clock(s['end'])} ({span(minutes(s['start'], s['end']))}): "
                         f"{STATES[s['state']]} ({pretty(s['location'])})")
    lines += ["", "What the scanners saw:"]
    for e in trail["observed_events"]:
        who = f"{e['actor_role']}, {pretty(e['location'])}"
        note = f' Note: "{e["note"]}".' if e.get("note") else ""
        lines.append(f"- {clock(e['timestamp'])}: {ACTIVITIES[e['activity']]} ({who}).{note}")

    lines.append("")
    if label["elapsed_min"] is None:
        lines.append(f"Window: {span(label['window_min'])} from collection to receipt. It never reached the lab, so the window was missed.")
    else:
        verdict = "missed the window" if label["breach"] else "made it in time"
        lines.append(f"Window: {span(label['window_min'])} from collection to receipt. It arrived after "
                     f"{span(label['elapsed_min'])} and {verdict}.")
    lines += ["", "[ ] Plausible    [ ] Not plausible    Why not, in a few words: ______________________________", ""]
    return "\n".join(lines)


def load_records(pilot: Path) -> list[dict]:
    records = []
    for path in sorted(pilot.glob("run-*.jsonl")):
        records += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return records


def sample(records: list[dict], n: int) -> list[dict]:
    """Deterministic: about a third planted problems, round-robin over the kinds; the rest spread over type and route."""
    faulty: dict[str, list[dict]] = {}
    for r in records:
        if r["brief"]["planted_fault"] != "none":
            faulty.setdefault(r["brief"]["planted_fault"], []).append(r)
    ring = [r for group in zip_longest(*(faulty[k] for k in sorted(faulty))) for r in group if r]
    picked = ring[: n // 3]
    normal = sorted((r for r in records if r["brief"]["planted_fault"] == "none"),
                    key=lambda r: (r["brief"]["fixed"]["type"], r["brief"]["fixed"]["route"], r["brief"]["specimen_id"]))
    need = n - len(picked)
    if need > len(normal):
        picked += ring[len(picked): len(picked) + need - len(normal)]
        need = len(normal)
    step = len(normal) / need if need else 1
    picked += [normal[int(i * step)] for i in range(need)]
    return sorted(picked, key=lambda r: r["brief"]["specimen_id"])


def sheet(records: list[dict], n: int) -> str:
    chosen = sample(records, n)
    intro = INTRO.format(n=n, passing=math.ceil(n * PASS_SHARE))
    return intro + "\n" + "\n".join(entry(i, r) for i, r in enumerate(chosen, 1))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write a plain-language review sheet of generated timelines.")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--pilot", type=Path, default=prompt.ROOT / "data" / "claude" / "pilot")
    args = ap.parse_args(argv)
    records = load_records(args.pilot)
    if len(records) < args.n:
        raise SystemExit(f"error: only {len(records)} accepted specimens in {args.pilot}, need {args.n}")
    out = args.pilot / "review-sheet.md"
    out.write_text(sheet(records, args.n), encoding="utf-8")
    print(f"wrote {out} ({args.n} timelines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
