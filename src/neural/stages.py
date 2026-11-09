"""Stage markers (docs/neural/spec.md §12.2).

Each stage writes `data/neural/stages/<stage>.json` with its input hashes, output hashes, git SHA, start, finish and
status. A deterministic stage reruns only when an input hash changed, an output changed or went missing, the marker
is older than the stage's maximum age (the dry run's 24 hours, since a spending command needs a dry run that recent),
or `--force` is given. Resumable stages (generate, collect) run every time and record each run.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def file_hash(path: Path) -> str | None:
    path = Path(path)
    return sha(path.read_bytes()) if path.is_file() else None


def obj_hash(obj) -> str:
    return sha(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode())


def git_sha(root: Path = ROOT) -> str | None:
    try:
        done = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None if done.returncode == 0 else None


def now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now().astimezone()).isoformat(timespec="seconds")


class Stage:
    def __init__(self, directory: Path, name: str):
        self.name = name
        self.path = Path(directory) / f"{name}.json"

    def marker(self) -> dict | None:
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text(encoding="utf-8"))

    def stale_reason(self, inputs: dict, *, max_age: timedelta | None = None,
                     now: datetime | None = None) -> str | None:
        """Why the stage must run, or None when it is up to date."""
        m = self.marker()
        if m is None:
            return "never run"
        if m.get("status") != "done":
            return f"last run {m.get('status')}"
        changed = sorted(k for k in set(inputs) | set(m.get("inputs") or {}) if (m.get("inputs") or {}).get(k)
                         != inputs.get(k))
        if changed:
            return f"input changed: {', '.join(changed)}"
        for label, rec in (m.get("outputs") or {}).items():
            if file_hash(Path(rec["path"])) != rec["hash"]:
                return f"output changed or missing: {label}"
        if max_age is not None and m.get("finished_at"):
            age = (now or datetime.now().astimezone()) - datetime.fromisoformat(m["finished_at"])
            if age > max_age:
                return f"older than {max_age.total_seconds() / 3600:.0f} hours"
        return None

    def _write(self, marker: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def start(self, inputs: dict, now: datetime | None = None) -> dict:
        marker = {"stage": self.name, "status": "running", "inputs": inputs, "outputs": {}, "git_sha": git_sha(),
                  "started_at": now_iso(now), "finished_at": None, "note": None}
        self._write(marker)
        return marker

    def finish(self, marker: dict, outputs: dict, status: str = "done", note: str | None = None,
               now: datetime | None = None) -> dict:
        marker = {**marker, "status": status, "note": note, "finished_at": now_iso(now),
                  "outputs": {label: {"path": str(p), "hash": file_hash(Path(p))} for label, p in outputs.items()}}
        self._write(marker)
        return marker


def run_stage(directory: Path, name: str, inputs: dict, fn: Callable[[], tuple[dict, object]], *, force: bool = False,
              always: bool = False, max_age: timedelta | None = None, now: datetime | None = None):
    """Run fn() -> (outputs {label: path}, result) unless the stage is up to date.

    Returns (ran, result, reason): result is None when the stage did not run.
    """
    stage = Stage(directory, name)
    reason = "forced" if force else "runs every time" if always else stage.stale_reason(inputs, max_age=max_age,
                                                                                         now=now)
    if reason is None:
        return False, None, "up to date"
    marker = stage.start(inputs, now)
    try:
        outputs, result = fn()
    except BaseException as e:
        stage.finish(marker, {}, status="failed", note=f"{type(e).__name__}: {e}"[:500], now=now)
        raise
    stopped = result.get("stopped") if isinstance(result, dict) else None
    stage.finish(marker, outputs, status="stopped" if stopped else "done", note=stopped, now=now)
    return True, result, reason


def markers(directory: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(directory).glob("*.json"))]
