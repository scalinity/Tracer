"""Worker pools and progress for long jobs.

`one_thread_children()` starts child processes with one thread per numeric library. N workers that each open a
thread pool the size of the machine oversubscribe the cores: Polars alone starts 64 threads per process on a
64-core allocation. `available_cores()` is the CPU set the scheduler gave this process.

`report()` writes the job's progress to the JSON file named by TRACER_PROGRESS, which the monitor reads; without
that variable it does nothing.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
               "NUMEXPR_NUM_THREADS", "POLARS_MAX_THREADS")


def available_cores() -> int:
    """The cores this process may use: the CPU set a scheduler such as SLURM gave it, not the whole machine."""
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:      # macOS has no affinity call; the whole machine is ours
        return os.cpu_count() or 2


@contextmanager
def one_thread_children():
    """Processes spawned inside the block see one thread per library (THREAD_VARS); the parent's values return
    after it. Use with the spawn start method, so each child reads the variables when it imports the libraries."""
    saved = {k: os.environ.get(k) for k in THREAD_VARS}
    os.environ.update({k: "1" for k in THREAD_VARS})
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


_PROGRESS = {"label": None, "stage": None, "started": None, "last": 0.0}


def label(text: str | None) -> None:
    """The step that the following reports belong to, such as "warning check, rung C"."""
    _PROGRESS["label"] = text


def report(stage: str, done: int, total: int, unit: str, *, every_s: float = 5.0) -> None:
    """Record that `done` of `total` `unit` of `stage` are finished. Writes at most every `every_s` seconds, and
    always at the start and end of a stage."""
    path = os.environ.get("TRACER_PROGRESS")
    if not path:
        return
    name = f"{_PROGRESS['label']}: {stage}" if _PROGRESS["label"] else stage
    now = time.monotonic()
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    if name != _PROGRESS["stage"]:
        _PROGRESS.update(stage=name, started=stamp, last=0.0)
    if 0 < done < total and now - _PROGRESS["last"] < every_s:
        return
    _PROGRESS["last"] = now
    record = {"stage": name, "done": int(done), "total": int(total), "unit": unit, "started": _PROGRESS["started"],
              "updated": stamp, "pid": os.getpid()}
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(record) + "\n", encoding="utf-8")
    os.replace(tmp, target)
