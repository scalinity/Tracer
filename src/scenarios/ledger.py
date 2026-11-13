"""The spend ledger: no API request leaves the machine without a reservation that covers its worst case.

docs/neural/spec.md §4.6 is the design. Storage is SQLite in WAL mode (`data/openai/ledger.sqlite`) with a
human-readable, append-only mirror beside it (`ledger.jsonl`). Accounts and job caps come from
config/openai_jobs.yaml; the ledger refuses a cap change for a job or account that already has entries, except a
dated raise listed under the account's `raises`, which lifts its cap by exactly that amount once and is mirrored.

Money is integer micro-dollars (`micros`); each entry also carries `usd` for reading. Five entry kinds:

- `reserve`: a request's worst case, held until closed. Made inside one BEGIN IMMEDIATE transaction and refused
  unless spent + held + new <= account cap - margin and the job's own spent + held + new <= job cap. `cover()`
  replaces an unsent reservation that has become smaller than its request's worst case (a `void` and a larger
  `reserve` in one transaction, under the same checks).
- `settle`: the actual cost of a reserved request (closes it); `release` records the remainder handed back.
- `void`: closes a reservation with nothing spent (an error the docs say is not billed).
- `settle_unknown()` writes a `settle` for the full reservation: a timeout or any outcome not proven unbilled.
- `adjust`: a correction to spent, for reconciliation with the provider's usage page.

spent = sum of settle and adjust; held = the reservations no settle or void has closed. A settlement above its
reservation is recorded as it is and logged as an error; the runner stops a job when it happens.

A missing, corrupt or locked ledger raises LedgerUnavailable, and nothing may be sent without a ledger. The database
is the record; the mirror is written after each commit and must agree with it on every entry. On open, entries the
database committed but the mirror lacks (a crash between the two writes) are appended to the mirror; any other
disagreement stops everything. `replay(mirror)` rebuilds the balances from the mirror alone.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = ROOT / "data" / "openai" / "ledger.sqlite"
JOBS_PATH = ROOT / "config" / "openai_jobs.yaml"
SCHEMA_VERSION = "1"
KINDS = ("reserve", "settle", "release", "void", "adjust")
CLOSING = ("settle", "void")
BUSY_TIMEOUT_S = 30.0
FIELDS = ("seq", "entry_id", "account", "job", "item_id", "kind", "micros", "at", "request_id", "ref", "detail")

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE accounts (account TEXT PRIMARY KEY, cap_micros INTEGER NOT NULL, margin_micros INTEGER NOT NULL);
CREATE TABLE jobs (job TEXT PRIMARY KEY, account TEXT NOT NULL REFERENCES accounts(account),
                   cap_micros INTEGER NOT NULL, released INTEGER NOT NULL,
                   released_at TEXT, released_reason TEXT);
CREATE TABLE entries (seq INTEGER PRIMARY KEY AUTOINCREMENT, entry_id TEXT NOT NULL UNIQUE,
                      account TEXT NOT NULL, job TEXT NOT NULL, item_id TEXT,
                      kind TEXT NOT NULL CHECK (kind IN ('reserve', 'settle', 'release', 'void', 'adjust')),
                      micros INTEGER NOT NULL, usd REAL NOT NULL, at TEXT NOT NULL, request_id TEXT,
                      ref TEXT, detail TEXT NOT NULL);
CREATE INDEX entries_ref ON entries (ref);
CREATE INDEX entries_job ON entries (job, kind);
CREATE INDEX entries_account ON entries (account, kind);
"""


class LedgerError(RuntimeError):
    """A ledger operation was refused. Stop the job."""


class LedgerUnavailable(LedgerError):
    """The ledger is missing, corrupt, locked or disagrees with its mirror. Stop everything; send nothing."""


class CapReached(LedgerError):
    """A reservation would pass the account's or the job's cap, or the run's budget; or the job is not released.

    `scope` is "account", "job", "budget" or "released".
    """

    def __init__(self, message: str, scope: str):
        super().__init__(message)
        self.scope = scope


def to_micros(usd) -> int:
    """USD (int, float, str or Decimal) to whole micro-dollars, rounded up."""
    if isinstance(usd, bool):
        raise LedgerError(f"{usd!r} is not an amount")
    try:
        amount = Decimal(str(usd)) * 1_000_000
    except ArithmeticError:
        raise LedgerError(f"{usd!r} is not an amount") from None
    if not amount.is_finite():
        raise LedgerError(f"{usd!r} is not a finite amount")
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


def as_usd(micros: int) -> float:
    return micros / 1_000_000


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def mirror_path(path: Path) -> Path:
    return Path(path).with_suffix(".jsonl")


def load_jobs(path: Path = JOBS_PATH) -> dict:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(cfg, dict) or not cfg.get("accounts") or not cfg.get("jobs"):
        raise LedgerError(f"{path} lacks accounts or jobs")
    return cfg


def usage_tokens(usage) -> dict:
    """The token counts a report sums, from a usage object (missing counts are 0; reasoning is part of output)."""
    u = usage if isinstance(usage, dict) else {}
    inp = u.get("input_tokens_details") or {}
    out = u.get("output_tokens_details") or {}

    def n(v):
        return v if isinstance(v, int) and not isinstance(v, bool) else 0
    return {"input": n(u.get("input_tokens")), "cached": n(inp.get("cached_tokens")),
            "cache_write": n(inp.get("cache_write_tokens")), "output": n(u.get("output_tokens")),
            "reasoning": n(out.get("reasoning_tokens"))}


def _entry_line(row: dict) -> str:
    return json.dumps({**row, "usd": as_usd(row["micros"])}, sort_keys=True)


def _raise_key(account: str, r: dict) -> str:
    """The identity of one dated cap raise: account, date and amount in micros."""
    return f"{account}:{r['date']}:{to_micros(r['usd'])}"


def _read_mirror(path: Path, raises: dict | None = None) -> tuple[dict[str, dict], dict[str, dict]]:
    """Entries by id and job releases by job, from the mirror; cap raises go into `raises` by key when given.
    Raises LedgerUnavailable on any malformed line."""
    entries: dict[str, dict] = {}
    releases: dict[str, dict] = {}
    if not path.exists():
        return entries, releases
    for n, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            raise LedgerUnavailable(f"{path} line {n} is not JSON; the ledger mirror is corrupt") from None
        if "job_release" in rec:
            releases[rec["job_release"]] = rec
            continue
        if "cap_raise" in rec:
            if raises is not None:
                raises[_raise_key(rec["cap_raise"], rec)] = rec
            continue
        if not isinstance(rec, dict) or any(k not in rec for k in FIELDS) or rec["kind"] not in KINDS:
            raise LedgerUnavailable(f"{path} line {n} is not a ledger entry")
        if not isinstance(rec["micros"], int) or isinstance(rec["micros"], bool):
            raise LedgerUnavailable(f"{path} line {n} has a bad amount")
        known = entries.get(rec["entry_id"])
        rec = {k: rec[k] for k in FIELDS}
        if known is not None and known != rec:
            raise LedgerUnavailable(f"{path} has two different entries named {rec['entry_id']}")
        entries[rec["entry_id"]] = rec
    return entries, releases


def balances_of(entries) -> dict:
    """Spent and held per account and per job, in micros, from a collection of entry dicts."""
    entries = list(entries)
    closed = {e["ref"] for e in entries if e["kind"] in CLOSING and e["ref"]}
    out: dict = {"accounts": {}, "jobs": {}}
    for e in entries:
        for scope, key in (("accounts", e["account"]), ("jobs", e["job"])):
            bal = out[scope].setdefault(key, {"spent": 0, "held": 0})
            if e["kind"] in ("settle", "adjust"):
                bal["spent"] += e["micros"]
            elif e["kind"] == "reserve" and e["entry_id"] not in closed:
                bal["held"] += e["micros"]
    return out


def replay(path: Path) -> dict:
    """Balances rebuilt from the append-only mirror alone; they must equal Ledger.balances()."""
    entries, _ = _read_mirror(Path(path))
    return balances_of(entries.values())


class Ledger:
    """One connection to the ledger. Safe to share between threads; several Ledgers may share one file."""

    def __init__(self, path: Path = LEDGER_PATH, *, jobs: dict | None = None, create: bool = False,
                 busy_timeout_s: float = BUSY_TIMEOUT_S):
        self.path = Path(path)
        self.mirror = mirror_path(self.path)
        self._lock = threading.RLock()
        self._con: sqlite3.Connection | None = None
        cfg = jobs if jobs is not None else load_jobs()
        mirror_has_entries = self.mirror.exists() and self.mirror.stat().st_size > 0
        if not self.path.exists():
            if mirror_has_entries:
                raise LedgerUnavailable(f"ledger database {self.path} is missing but its mirror {self.mirror.name} "
                                        "has entries; restore the database before sending anything")
            if not create:
                raise LedgerUnavailable(f"no ledger at {self.path}; run the dry run first, which creates it")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._con = sqlite3.connect(self.path, timeout=busy_timeout_s, isolation_level=None,
                                        check_same_thread=False)
            mode = self._con.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            self._con.execute("PRAGMA synchronous=FULL")
            self._con.execute("PRAGMA foreign_keys=ON")
            if str(mode).lower() != "wal":
                raise LedgerUnavailable(f"ledger {self.path} could not switch to WAL mode (got {mode!r})")
            check = self._con.execute("PRAGMA quick_check").fetchone()[0]
            if check != "ok":
                raise LedgerUnavailable(f"ledger {self.path} failed its integrity check: {check}")
            self._init_schema()
            self._sync_config(cfg)
            self._check_mirror(mirror_has_entries)
        except sqlite3.Error as e:
            self.close()
            raise LedgerUnavailable(f"ledger {self.path} is unreadable or locked: {e}") from None
        except BaseException:
            self.close()
            raise

    # -- opening ---------------------------------------------------------------------------------------------------

    def close(self) -> None:
        if self._con is not None:
            try:
                self._con.close()
            finally:
                self._con = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _init_schema(self) -> None:
        with self._tx() as cur:
            tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not tables:
                for statement in filter(str.strip, SCHEMA.split(";")):
                    cur.execute(statement)
                cur.execute("INSERT INTO meta VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
                return
        if not {"meta", "accounts", "jobs", "entries"} <= tables:
            raise LedgerUnavailable(f"ledger {self.path} lacks its tables")
        row = self._con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if not row or row[0] != SCHEMA_VERSION:
            raise LedgerUnavailable(f"ledger {self.path} has schema version {row and row[0]!r}, "
                                    f"expected {SCHEMA_VERSION}")

    @staticmethod
    def _recorded_raises(cur) -> dict:
        row = cur.execute("SELECT value FROM meta WHERE key='cap_raises'").fetchone()
        return json.loads(row[0]) if row else {}

    def _sync_config(self, cfg: dict) -> None:
        """Load accounts and job caps; refuse a cap change where entries exist, except a dated raise.

        An account's `raises` list ({usd, date, reason}) is the one way to lift a cap that has entries: each raise not
        yet recorded lifts the ledger's cap by exactly its amount, once, and is written to the meta table and the
        mirror. The config's cap must equal the ledger's cap plus the new raises, and the margin cannot change."""
        releases, raised = [], []
        with self._tx() as cur:
            recorded = self._recorded_raises(cur)
            for account, a in cfg["accounts"].items():
                cap, margin = to_micros(a["cap_usd"]), to_micros(a.get("margin_usd", 0))
                if margin < 0 or margin > cap:
                    raise LedgerError(f"account {account}: margin must be between 0 and the cap")
                for r in a.get("raises") or []:
                    if Decimal(str(r.get("usd", 0))) <= 0 or not str(r.get("date", "")).strip() \
                            or not str(r.get("reason", "")).strip():
                        raise LedgerError(f"account {account}: a cap raise needs a positive usd, a date and a reason")
                row = cur.execute("SELECT cap_micros, margin_micros FROM accounts WHERE account=?",
                                  (account,)).fetchone()
                if row is None:
                    cur.execute("INSERT INTO accounts VALUES (?, ?, ?)", (account, cap, margin))
                elif tuple(row) != (cap, margin):
                    if cur.execute("SELECT 1 FROM entries WHERE account=? LIMIT 1", (account,)).fetchone():
                        new = [r for r in a.get("raises") or [] if _raise_key(account, r) not in recorded]
                        lift = sum(to_micros(r["usd"]) for r in new)
                        if not new or margin != row[1] or cap != row[0] + lift:
                            raise LedgerError(f"account {account} has entries; its cap and margin cannot change "
                                              f"except by a dated raise of exactly the difference (ledger "
                                              f"${as_usd(row[0]):.2f}/${as_usd(row[1]):.2f}, config "
                                              f"${as_usd(cap):.2f}/${as_usd(margin):.2f})")
                        at = now_iso()
                        before = row[0]
                        for r in new:
                            after = before + to_micros(r["usd"])
                            rec = {"cap_raise": account, "usd": as_usd(to_micros(r["usd"])), "date": str(r["date"]),
                                   "reason": str(r["reason"]).strip(), "cap_before": as_usd(before),
                                   "cap_after": as_usd(after), "at": at}
                            recorded[_raise_key(account, r)] = rec
                            raised.append(rec)
                            before = after
                        cur.execute("INSERT INTO meta (key, value) VALUES ('cap_raises', ?) "
                                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                    (json.dumps(recorded, sort_keys=True),))
                    cur.execute("UPDATE accounts SET cap_micros=?, margin_micros=? WHERE account=?",
                                (cap, margin, account))
            for job, j in cfg["jobs"].items():
                account, cap, released = j["account"], to_micros(j["cap_usd"]), bool(j.get("released", True))
                if account not in cfg["accounts"]:
                    raise LedgerError(f"job {job} names unknown account {account}")
                row = cur.execute("SELECT account, cap_micros, released FROM jobs WHERE job=?", (job,)).fetchone()
                if row is None:
                    cur.execute("INSERT INTO jobs (job, account, cap_micros, released) VALUES (?, ?, ?, ?)",
                                (job, account, cap, int(released)))
                    continue
                if (row[0], row[1]) != (account, cap):
                    if cur.execute("SELECT 1 FROM entries WHERE job=? LIMIT 1", (job,)).fetchone():
                        raise LedgerError(f"job {job} has entries; its cap cannot change (ledger "
                                          f"${as_usd(row[1]):.2f} on {row[0]}, config ${as_usd(cap):.2f} on {account})")
                    cur.execute("UPDATE jobs SET account=?, cap_micros=? WHERE job=?", (account, cap, job))
                if released and not row[2]:
                    at = now_iso()
                    cur.execute("UPDATE jobs SET released=1, released_at=?, released_reason=? WHERE job=?",
                                (at, "released in config/openai_jobs.yaml", job))
                    releases.append({"job_release": job, "reason": "released in config/openai_jobs.yaml",
                                     "at": at})
        if releases or raised:
            self._append_mirror([json.dumps(r, sort_keys=True) for r in releases + raised])

    def _rows(self, where: str = "", args=()) -> list[dict]:
        cur = self._con.execute(f"SELECT {', '.join(FIELDS)} FROM entries {where} ORDER BY seq", args)
        out = []
        for row in cur.fetchall():
            rec = dict(zip(FIELDS, row))
            rec["detail"] = json.loads(rec["detail"])
            out.append(rec)
        return out

    def _check_mirror(self, mirror_had_entries: bool) -> None:
        with self._lock:
            rows = self._rows()
            mirrored_raises: dict = {}
            mirrored, releases = _read_mirror(self.mirror, mirrored_raises)
            if rows and not mirror_had_entries:
                raise LedgerUnavailable(f"ledger {self.path.name} has {len(rows)} entries but its mirror "
                                        f"{self.mirror.name} is missing or empty")
            by_id = {r["entry_id"]: r for r in rows}
            stray = sorted(set(mirrored) - set(by_id))
            if stray:
                raise LedgerUnavailable(f"mirror {self.mirror.name} has entries the database lacks: {stray[:3]}")
            for entry_id, rec in mirrored.items():
                if by_id[entry_id] != rec:
                    raise LedgerUnavailable(f"mirror {self.mirror.name} disagrees with the database on {entry_id}")
            missing = [r for r in rows if r["entry_id"] not in mirrored]
            if missing:  # committed, then the process died before the mirror write: only ever the newest entries
                newest = max((by_id[i]["seq"] for i in mirrored), default=0)
                if min(r["seq"] for r in missing) < newest:
                    raise LedgerUnavailable(f"mirror {self.mirror.name} lacks entries older than its newest one; "
                                            "it is not a crash tail, so the mirror is corrupt")
                log.warning("appending %d committed ledger entries missing from the mirror", len(missing))
                self._append_mirror([_entry_line(r) for r in missing])
            done = {j: (at, reason) for j, at, reason in self._con.execute(
                "SELECT job, released_at, released_reason FROM jobs WHERE released_at IS NOT NULL")}
            if set(releases) - set(done):
                raise LedgerUnavailable(f"mirror {self.mirror.name} records a job release the database lacks")
            gone = [{"job_release": j, "at": at, "reason": reason} for j, (at, reason) in done.items()
                    if j not in releases]
            if gone:
                self._append_mirror([json.dumps(r, sort_keys=True) for r in gone])
            recorded = self._recorded_raises(self._con.cursor())
            if set(mirrored_raises) - set(recorded):
                raise LedgerUnavailable(f"mirror {self.mirror.name} records a cap raise the database lacks")
            lost = [recorded[k] for k in sorted(recorded) if k not in mirrored_raises]
            if lost:
                self._append_mirror([json.dumps(r, sort_keys=True) for r in lost])
            if replay(self.mirror) != self.balances():
                raise LedgerUnavailable(f"replaying {self.mirror.name} does not reproduce the database balances")

    # -- transactions ----------------------------------------------------------------------------------------------

    @contextmanager
    def _tx(self):
        if self._con is None:
            raise LedgerUnavailable("the ledger is closed")
        with self._lock:
            try:
                self._con.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as e:
                raise LedgerUnavailable(f"ledger {self.path} is locked or unreadable: {e}") from None
            try:
                yield self._con.cursor()
                self._con.execute("COMMIT")
            except BaseException:
                try:
                    self._con.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    def _append_mirror(self, lines: list[str]) -> None:
        if not lines:
            return
        data = "".join(line + "\n" for line in lines).encode("utf-8")
        try:
            fd = os.open(self.mirror, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view):]
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as e:
            raise LedgerUnavailable(f"could not write the ledger mirror {self.mirror}: {e}") from None

    def _insert(self, cur, *, account: str, job: str, item_id: str | None, kind: str, micros: int,
                request_id: str | None = None, ref: str | None = None, detail: dict | None = None) -> dict:
        row = {"entry_id": f"{kind[:3]}-{uuid.uuid4().hex[:16]}", "account": account, "job": job,
               "item_id": item_id, "kind": kind, "micros": int(micros), "at": now_iso(), "request_id": request_id,
               "ref": ref, "detail": detail or {}}
        cur.execute("INSERT INTO entries (entry_id, account, job, item_id, kind, micros, usd, at, request_id, ref, "
                    "detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (row["entry_id"], account, job, item_id, kind, row["micros"], as_usd(row["micros"]), row["at"],
                     request_id, ref, json.dumps(row["detail"], sort_keys=True)))
        row["seq"] = cur.lastrowid
        return row

    def _write(self, fn):
        """Run fn(cur) -> list of rows inside one transaction, then mirror the rows. Returns fn's rows."""
        with self._lock:
            with self._tx() as cur:
                rows = fn(cur)
            self._append_mirror([_entry_line(r) for r in rows])
            return rows

    @staticmethod
    def _spent_held(cur, column: str, key: str) -> tuple[int, int]:
        spent = cur.execute(f"SELECT COALESCE(SUM(micros), 0) FROM entries WHERE {column}=? "
                            "AND kind IN ('settle', 'adjust')", (key,)).fetchone()[0]
        held = cur.execute(f"SELECT COALESCE(SUM(r.micros), 0) FROM entries r WHERE r.{column}=? "
                           "AND r.kind='reserve' AND NOT EXISTS (SELECT 1 FROM entries c WHERE c.ref=r.entry_id "
                           "AND c.kind IN ('settle', 'void'))", (key,)).fetchone()[0]
        return spent, held

    def _job_row(self, cur, job: str):
        row = cur.execute("SELECT j.account, j.cap_micros, j.released, a.cap_micros, a.margin_micros FROM jobs j "
                          "JOIN accounts a ON a.account=j.account WHERE j.job=?", (job,)).fetchone()
        if row is None:
            raise LedgerError(f"job {job} is not in the ledger")
        return row

    # -- reservations ----------------------------------------------------------------------------------------------

    def reserve(self, job: str, item_id: str, usd, *, ceiling_usd=None, detail: dict | None = None) -> str:
        """Hold one request's worst case. Returns the reservation's entry_id, or raises CapReached."""
        return self.reserve_many(job, [(item_id, usd, detail)], ceiling_usd=ceiling_usd)[0]

    def reserve_many(self, job: str, items, *, ceiling_usd=None) -> list[str]:
        """Hold several worst cases at once (a Batch chunk): all of them, or none (CapReached)."""
        wanted = [(item_id, to_micros(usd), detail or {}) for item_id, usd, detail in items]
        if not wanted:
            return []
        if any(m <= 0 for _, m, _ in wanted):
            raise LedgerError("a reservation must be a positive amount")
        ceiling = None if ceiling_usd is None else to_micros(ceiling_usd)
        total = sum(m for _, m, _ in wanted)

        def fn(cur):
            account = self._check_room(cur, job, total, ceiling)
            return [self._insert(cur, account=account, job=job, item_id=item_id, kind="reserve", micros=m,
                                 detail=d) for item_id, m, d in wanted]

        return [r["entry_id"] for r in self._write(fn)]

    def _check_room(self, cur, job: str, total: int, ceiling: int | None, freed: int = 0) -> str:
        """Raise CapReached unless `total` more fits the account, the job and the run's budget once `freed` (an open
        reservation of this job about to be closed) is handed back. Returns the job's account."""
        account, job_cap, released, acct_cap, margin = self._job_row(cur, job)
        if not released:
            raise CapReached(f"job {job} is not released; it takes no reservation until "
                             f"`ledger release-job {job} --reason TEXT`", "released")
        a_spent, a_held = self._spent_held(cur, "account", account)
        j_spent, j_held = self._spent_held(cur, "job", job)
        a_held, j_held = a_held - freed, j_held - freed
        if a_spent + a_held + total > acct_cap - margin:
            raise CapReached(f"account {account}: ${as_usd(a_spent):.6f} spent + ${as_usd(a_held):.6f} held + "
                             f"${as_usd(total):.6f} would pass ${as_usd(acct_cap - margin):.2f} "
                             f"(cap ${as_usd(acct_cap):.2f} less the ${as_usd(margin):.2f} margin)", "account")
        if j_spent + j_held + total > job_cap:
            raise CapReached(f"job {job}: ${as_usd(j_spent):.6f} spent + ${as_usd(j_held):.6f} held + "
                             f"${as_usd(total):.6f} would pass its ${as_usd(job_cap):.2f} cap", "job")
        if ceiling is not None and j_spent + j_held + total > ceiling:
            raise CapReached(f"job {job}: ${as_usd(j_spent):.6f} spent + ${as_usd(j_held):.6f} held + "
                             f"${as_usd(total):.6f} would pass this run's ${as_usd(ceiling):.2f} budget", "budget")
        return account

    def cover(self, entry_id: str, usd, *, ceiling_usd=None, detail: dict | None = None) -> str:
        """Make sure an open, unsent reservation holds at least `usd` before its request leaves.

        A reservation already that large is returned unchanged. A smaller one is voided and a reservation of the full
        amount made in its place, in one transaction and under the same account, job and budget checks (the old hold
        counts as handed back). If the larger amount does not fit, CapReached is raised and the old reservation stays
        open as it was. Returns the entry_id that now covers the request."""
        wanted = to_micros(usd)
        if wanted <= 0:
            raise LedgerError("a reservation must be a positive amount")
        ceiling = None if ceiling_usd is None else to_micros(ceiling_usd)

        def fn(cur):
            res = self._open_reservation(cur, entry_id)
            if res["micros"] >= wanted:
                return []
            account = self._check_room(cur, res["job"], wanted, ceiling, freed=res["micros"])
            return [self._insert(cur, account=account, job=res["job"], item_id=res["item_id"], kind="void",
                                 micros=res["micros"], ref=entry_id,
                                 detail={"reason": f"replaced by a reservation of ${as_usd(wanted):.6f} before "
                                                   "sending; nothing was sent"}),
                    self._insert(cur, account=account, job=res["job"], item_id=res["item_id"], kind="reserve",
                                 micros=wanted, detail={**res["detail"], **(detail or {}), "replaces": entry_id})]

        rows = self._write(fn)
        return rows[1]["entry_id"] if rows else entry_id

    def _open_reservation(self, cur, entry_id: str) -> dict:
        row = cur.execute("SELECT account, job, item_id, micros, detail FROM entries WHERE entry_id=? "
                          "AND kind='reserve'", (entry_id,)).fetchone()
        if row is None:
            raise LedgerError(f"no reservation {entry_id}")
        closed = cur.execute("SELECT kind FROM entries WHERE ref=? AND kind IN ('settle', 'void')",
                             (entry_id,)).fetchone()
        if closed:
            raise LedgerError(f"reservation {entry_id} is already closed by a {closed[0]}")
        return {"account": row[0], "job": row[1], "item_id": row[2], "micros": row[3], "detail": json.loads(row[4])}

    def settle(self, entry_id: str, usd, usage: dict | None, request_id: str | None, tier: str | None, *,
               detail: dict | None = None) -> dict:
        """Record a response's actual cost and release the rest of its reservation.

        Returns {"micros", "reserved", "released", "over"}; `over` is True when the cost passed the reservation,
        which is recorded as it is and logged as an error.
        """
        cost = to_micros(usd)
        if cost < 0:
            raise LedgerError("a settlement cannot be negative")

        def fn(cur):
            res = self._open_reservation(cur, entry_id)
            over = cost > res["micros"]
            info = {**res["detail"], **(detail or {}), "usage": usage, "usage_tokens": usage_tokens(usage),
                    "tier": tier, "reserved": res["micros"], "over_reservation": over}
            rows = [self._insert(cur, account=res["account"], job=res["job"], item_id=res["item_id"], kind="settle",
                                 micros=cost, request_id=request_id, ref=entry_id, detail=info)]
            if res["micros"] - cost > 0:
                rows.append(self._insert(cur, account=res["account"], job=res["job"], item_id=res["item_id"],
                                         kind="release", micros=res["micros"] - cost, request_id=request_id,
                                         ref=entry_id, detail={"reason": "remainder after settlement"}))
            return rows

        rows = self._write(fn)
        settled = rows[0]
        over = settled["detail"]["over_reservation"]
        if over:
            log.error("LEDGER: settlement $%.6f for %s is above its $%.6f reservation (%s); recorded as it is",
                      as_usd(cost), settled["item_id"], as_usd(settled["detail"]["reserved"]), entry_id)
        return {"micros": cost, "reserved": settled["detail"]["reserved"],
                "released": rows[1]["micros"] if len(rows) > 1 else 0, "over": over}

    def settle_unknown(self, entry_id: str, reason: str, request_id: str | None = None) -> int:
        """Keep the whole reservation as spent: the outcome cannot be proven unbilled. Returns the micros kept."""
        def fn(cur):
            res = self._open_reservation(cur, entry_id)
            return [self._insert(cur, account=res["account"], job=res["job"], item_id=res["item_id"], kind="settle",
                                 micros=res["micros"], request_id=request_id, ref=entry_id,
                                 detail={**res["detail"], "unknown": True, "reason": reason,
                                         "reserved": res["micros"], "over_reservation": False})]
        return self._write(fn)[0]["micros"]

    def void(self, entry_id: str, reason: str, request_id: str | None = None) -> int:
        """Close a reservation with nothing spent. Returns the micros handed back."""
        def fn(cur):
            res = self._open_reservation(cur, entry_id)
            return [self._insert(cur, account=res["account"], job=res["job"], item_id=res["item_id"], kind="void",
                                 micros=res["micros"], request_id=request_id, ref=entry_id,
                                 detail={"reason": reason})]
        return self._write(fn)[0]["micros"]

    def adjust(self, job: str, usd, reason: str) -> dict:
        """Add a reconciliation correction to spent (negative to lower it). Recorded even past a cap, and flagged."""
        amount = to_micros(usd) if Decimal(str(usd)) >= 0 else -to_micros(-Decimal(str(usd)))
        if not reason:
            raise LedgerError("an adjustment needs a reason")

        def fn(cur):
            account, job_cap, _, acct_cap, margin = self._job_row(cur, job)
            row = self._insert(cur, account=account, job=job, item_id=None, kind="adjust", micros=amount,
                               detail={"reason": reason})
            a_spent, a_held = self._spent_held(cur, "account", account)
            j_spent, j_held = self._spent_held(cur, "job", job)
            row["past_cap"] = a_spent + a_held > acct_cap - margin or j_spent + j_held > job_cap
            return [row]

        row = self._write(fn)[0]
        if row.pop("past_cap"):
            log.error("LEDGER: adjustment of $%.6f puts %s past a cap; recorded as it is", as_usd(amount), job)
        return row

    def release_job(self, job: str, reason: str) -> bool:
        """Let a held-back job (O1b, O3b) take reservations. Returns False if it was already released."""
        if not reason or not reason.strip():
            raise LedgerError("releasing a job needs a reason")
        with self._lock:
            with self._tx() as cur:
                row = cur.execute("SELECT released FROM jobs WHERE job=?", (job,)).fetchone()
                if row is None:
                    raise LedgerError(f"job {job} is not in the ledger")
                if row[0]:
                    return False
                at = now_iso()
                cur.execute("UPDATE jobs SET released=1, released_at=?, released_reason=? WHERE job=?",
                            (at, reason.strip(), job))
            self._append_mirror([json.dumps({"job_release": job, "reason": reason.strip(), "at": at},
                                            sort_keys=True)])
            return True

    # -- reading ---------------------------------------------------------------------------------------------------

    def balances(self) -> dict:
        """Spent and held per account and per job that has entries (the shape replay() returns)."""
        with self._lock:
            out: dict = {"accounts": {}, "jobs": {}}
            cur = self._con.cursor()
            for scope, column in (("accounts", "account"), ("jobs", "job")):
                for (key,) in cur.execute(f"SELECT DISTINCT {column} FROM entries").fetchall():
                    spent, held = self._spent_held(cur, column, key)
                    out[scope][key] = {"spent": spent, "held": held}
            return out

    def totals(self) -> dict:
        """Caps, spent, held and headroom (micros) for every account and job."""
        with self._lock:
            cur = self._con.cursor()
            out: dict = {"accounts": {}, "jobs": {}}
            for account, cap, margin in cur.execute("SELECT account, cap_micros, margin_micros FROM accounts "
                                                    "ORDER BY account").fetchall():
                spent, held = self._spent_held(cur, "account", account)
                out["accounts"][account] = {"cap": cap, "margin": margin, "reservable": cap - margin,
                                            "spent": spent, "held": held, "headroom": cap - margin - spent - held}
            for job, account, cap, released in cur.execute("SELECT job, account, cap_micros, released FROM jobs "
                                                           "ORDER BY job").fetchall():
                spent, held = self._spent_held(cur, "job", job)
                acct = out["accounts"][account]
                out["jobs"][job] = {"account": account, "cap": cap, "released": bool(released), "spent": spent,
                                    "held": held, "headroom": max(0, min(cap - spent - held, acct["headroom"]))}
            return out

    def open_reservations(self, job: str | None = None) -> list[dict]:
        with self._lock:
            where = ("WHERE kind='reserve' AND entry_id NOT IN (SELECT ref FROM entries WHERE ref IS NOT NULL "
                     "AND kind IN ('settle', 'void'))")
            args: tuple = ()
            if job is not None:
                where += " AND job=?"
                args = (job,)
            return self._rows(where, args)

    def entries(self, job: str | None = None) -> list[dict]:
        with self._lock:
            return self._rows("WHERE job=?", (job,)) if job else self._rows()

    def report(self) -> dict:
        """Settled totals per job, model and tier beside the summed reported tokens."""
        groups: dict = {}
        for e in self.entries():
            if e["kind"] not in ("settle", "adjust"):
                continue
            d = e["detail"]
            key = (e["job"], d.get("model") or "-", d.get("tier") or ("adjust" if e["kind"] == "adjust" else "-"))
            g = groups.setdefault(key, {"job": key[0], "model": key[1], "tier": key[2], "settled": 0, "requests": 0,
                                        "unknown": 0, "over_reservation": 0,
                                        "tokens": {"input": 0, "cached": 0, "cache_write": 0, "output": 0,
                                                   "reasoning": 0}})
            g["settled"] += e["micros"]
            if e["kind"] == "settle":
                g["requests"] += 1
                g["unknown"] += bool(d.get("unknown"))
                g["over_reservation"] += bool(d.get("over_reservation"))
                for name, value in (d.get("usage_tokens") or {}).items():
                    if name in g["tokens"] and isinstance(value, int):
                        g["tokens"][name] += value
        rows = [groups[k] for k in sorted(groups)]
        return {"rows": rows, "totals": self.totals()}
