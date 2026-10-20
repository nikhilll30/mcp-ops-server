"""SQLite store for the synthetic salon, seeded from ``seed.py``.

Integrity rules live here, not in the tool layer. As in the clinic, the critical one is also
a database constraint: a partial unique index allows at most one *booked* visit per opening.
"""

import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import TypedDict

from salon_ops import seed
from salon_ops.models import (
    CustomerVisitsResult,
    OpeningInfo,
    OpeningsResult,
    SearchStylistsResult,
    StylistInfo,
    VisitResult,
    VisitStatus,
)

MAX_RANGE_DAYS = 14
EARLIEST_DATE = date(2000, 1, 1)
LATEST_DATE = date(2100, 12, 31)


class StoreError(Exception):
    """An anticipated failure whose message is safe and useful to show an agent."""


class NotFoundError(StoreError):
    pass


class ConflictError(StoreError):
    pass


class BusyError(StoreError):
    """The store could not serve the call in time (lock held or contention)."""


class CallTimeoutError(StoreError):
    """A call ran past the per-call deadline and was cancelled. Nothing was written."""


class VisitRow(TypedDict):
    """One visit as the eval harness snapshots it (keyed by its number, VIS-<n>)."""

    status: VisitStatus
    customer_id: str
    opening_id: str
    start: str
    stylist_id: str
    studio: str
    service: str


SCHEMA = """
CREATE TABLE stylists (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    studio TEXT NOT NULL
);
CREATE TABLE stylist_services (
    stylist_id TEXT NOT NULL REFERENCES stylists(id),
    service TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (stylist_id, service)
);
CREATE TABLE customers (id TEXT PRIMARY KEY);
CREATE TABLE openings (
    id TEXT PRIMARY KEY,
    stylist_id TEXT NOT NULL REFERENCES stylists(id),
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    UNIQUE (stylist_id, start)
);
CREATE TABLE visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opening_id TEXT NOT NULL REFERENCES openings(id),
    customer_id TEXT NOT NULL REFERENCES customers(id),
    service TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('booked', 'cancelled'))
);
CREATE UNIQUE INDEX one_booking_per_opening ON visits(opening_id) WHERE status = 'booked';
CREATE INDEX visits_by_customer ON visits(customer_id, status);
"""


def visit_id(n: int) -> str:
    return f"VIS-{n:05d}"


def parse_visit_id(value: str) -> int | None:
    prefix, _, digits = value.partition("-")
    ok = prefix == "VIS" and digits.isascii() and digits.isdigit() and len(digits) <= 18
    return int(digits) if ok else None


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SalonStore:
    """Thread-safe facade over one SQLite connection, with one deadline per call.

    The locking and deadline handling is the clinic's ``SqliteBackend`` pattern: the SDK runs
    sync tools in worker threads, so every statement goes through ``_lock``; writes use
    ``BEGIN IMMEDIATE``; and one deadline covers the lock wait, SQLite's busy wait and
    statement execution, with a write that reaches COMMIT late rolled back.

    ``now`` returns the current salon-local time.
    """

    def __init__(
        self,
        path: str = ":memory:",
        seed_value: int = seed.SEED,
        timeout_s: float = 5.0,
        now: Callable[[], datetime] = lambda: seed.SALON_NOW,
    ) -> None:
        self._conn = sqlite3.connect(
            path, timeout=timeout_s, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.Lock()
        self._timeout_s = timeout_s
        self._deadline = float("inf")
        self._conn.set_progress_handler(self._past_deadline, 1000)
        self._now = now
        self._seed_value = seed_value
        self._initialize()

    def _past_deadline(self) -> int:
        return 1 if time.monotonic() > self._deadline else 0

    def _check_deadline(self) -> None:
        if time.monotonic() > self._deadline:
            raise CallTimeoutError("The call took too long and was cancelled.")

    def _bound_busy_wait(self) -> None:
        self._check_deadline()
        remaining = min(self._deadline - time.monotonic(), self._timeout_s)
        self._conn.execute(f"PRAGMA busy_timeout = {max(1, int(remaining * 1000))}")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        deadline = time.monotonic() + self._timeout_s
        if not self._lock.acquire(timeout=self._timeout_s):
            raise BusyError("The salon database is busy. Retry the call.")
        self._deadline = deadline
        try:
            self._bound_busy_wait()
            yield
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc):
                raise CallTimeoutError("The call took too long and was cancelled.") from exc
            if "locked" in str(exc):
                raise BusyError("The salon database is busy. Retry the call.") from exc
            raise
        finally:
            self._deadline = float("inf")
            self._lock.release()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._locked():
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
                self._bound_busy_wait()
                self._conn.execute("COMMIT")
            except BaseException:
                if self._conn.in_transaction:
                    self._deadline = float("inf")
                    self._conn.execute("ROLLBACK")
                raise

    def _read(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._locked():
            return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        self._conn.close()

    def _now_iso(self) -> str:
        return self._now().isoformat()

    def _initialize(self) -> None:
        with self._write() as c:
            self._deadline = float("inf")
            if c.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'stylists'"
            ).fetchone():
                return
            self._load(c, seed.generate(self._seed_value))

    @staticmethod
    def _load(c: sqlite3.Connection, data: seed.SeedData) -> None:
        for stmt in SCHEMA.split(";"):
            if stmt.strip():
                c.execute(stmt)
        c.executemany(
            "INSERT INTO stylists VALUES (?, ?, ?)",
            [(s.id, s.name, s.studio) for s in data.stylists],
        )
        c.executemany(
            "INSERT INTO stylist_services VALUES (?, ?, ?)",
            [(s.id, svc, i) for s in data.stylists for i, svc in enumerate(s.services)],
        )
        c.executemany("INSERT INTO customers VALUES (?)", [(c_,) for c_ in data.customer_ids])
        c.executemany(
            "INSERT INTO openings VALUES (?, ?, ?, ?)",
            [(o.id, o.stylist_id, o.start.isoformat(), o.end.isoformat()) for o in data.openings],
        )
        c.executemany(
            "INSERT INTO visits (opening_id, customer_id, service, status) "
            "VALUES (?, ?, ?, 'booked')",
            [(v.opening_id, v.customer_id, v.service) for v in data.visits],
        )

    # ---- reads -------------------------------------------------------------------------

    def _services(self, stylist_id: str) -> list[str]:
        rows = self._read(
            "SELECT service FROM stylist_services WHERE stylist_id = ? ORDER BY position",
            (stylist_id,),
        )
        return [r["service"] for r in rows]

    def search_stylists(
        self,
        service: str | None = None,
        studio: str | None = None,
        name: str | None = None,
        limit: int = 20,
    ) -> SearchStylistsResult:
        sql = "SELECT * FROM stylists s WHERE 1 = 1"
        params: list = []
        if service:
            sql += " AND EXISTS (SELECT 1 FROM stylist_services x WHERE x.stylist_id = s.id "
            sql += "AND x.service = ?)"
            params.append(service)
        if studio:
            sql += " AND s.studio = ?"
            params.append(studio)
        if name:
            sql += " AND s.name LIKE ? ESCAPE '\\'"
            params.append(f"%{_escape_like(name.strip())}%")
        sql += " ORDER BY s.id LIMIT ?"
        params.append(limit)
        stylists = [
            StylistInfo(
                stylist_id=r["id"],
                name=r["name"],
                studio=r["studio"],
                services=self._services(r["id"]),  # type: ignore[typeddict-item]
            )
            for r in self._read(sql, tuple(params))
        ]
        return {"stylists": stylists, "count": len(stylists)}

    def list_openings(
        self, stylist_id: str, start_date: date, end_date: date | None = None, limit: int = 20
    ) -> OpeningsResult:
        end_date = date_range(start_date, end_date)
        if not self._read("SELECT 1 FROM stylists WHERE id = ?", (stylist_id,)):
            raise stylist_not_found(stylist_id)
        lo = datetime.combine(start_date, datetime.min.time()).isoformat()
        hi = datetime.combine(end_date + timedelta(days=1), datetime.min.time()).isoformat()
        rows = self._read(
            "SELECT o.id, o.start, o.end FROM openings o "
            "WHERE o.stylist_id = ? AND o.start >= ? AND o.start < ? AND o.start > ? "
            "AND NOT EXISTS (SELECT 1 FROM visits v "
            "                WHERE v.opening_id = o.id AND v.status = 'booked') "
            "ORDER BY o.start LIMIT ?",
            (stylist_id, lo, hi, self._now_iso(), limit + 1),
        )
        openings = [OpeningInfo(opening_id=r["id"], start=r["start"], end=r["end"]) for r in rows]
        return {
            "stylist_id": stylist_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "openings": openings[:limit],
            "count": min(len(openings), limit),
            "truncated": len(openings) > limit,
        }

    def get_customer_visits(self, customer_id: str) -> CustomerVisitsResult:
        if not self._read("SELECT 1 FROM customers WHERE id = ?", (customer_id,)):
            raise customer_not_found(customer_id)
        rows = self._read(
            _VISIT_SQL + " WHERE v.customer_id = ? AND v.status = 'booked' AND o.start > ? "
            "ORDER BY o.start, v.id",
            (customer_id, self._now_iso()),
        )
        visits = [_visit(r) for r in rows]
        return {"customer_id": customer_id, "visits": visits, "count": len(visits)}

    # ---- writes ------------------------------------------------------------------------

    def book_visit(self, customer_id: str, opening_id: str, service: str) -> VisitResult:
        now = self._now_iso()
        with self._write() as c:
            if not c.execute("SELECT 1 FROM customers WHERE id = ?", (customer_id,)).fetchone():
                raise customer_not_found(customer_id)
            opening = c.execute(
                "SELECT o.*, s.name FROM openings o JOIN stylists s ON s.id = o.stylist_id "
                "WHERE o.id = ?",
                (opening_id,),
            ).fetchone()
            if opening is None:
                raise NotFoundError(
                    f"Opening {opening_id} not found. Call list_openings to find openings."
                )
            if opening["start"] <= now:
                raise ConflictError(
                    f"Opening {opening_id} starts at {opening['start']}, which is in the past. "
                    "Call list_openings for an upcoming opening."
                )
            offered = [
                r["service"]
                for r in c.execute(
                    "SELECT service FROM stylist_services WHERE stylist_id = ? ORDER BY position",
                    (opening["stylist_id"],),
                )
            ]
            if service not in offered:
                raise ConflictError(
                    f"{opening['name']} ({opening['stylist_id']}) does not offer {service}; "
                    f"they offer {', '.join(offered)}. Call search_stylists with "
                    f"service={service} to find a stylist who does."
                )
            if c.execute(
                "SELECT 1 FROM visits WHERE opening_id = ? AND status = 'booked'", (opening_id,)
            ).fetchone():
                raise ConflictError(
                    f"Opening {opening_id} ({opening['stylist_id']}, {opening['start']}) is "
                    "already booked. Call list_openings for another opening."
                )
            clash = c.execute(
                "SELECT v.id FROM visits v JOIN openings o ON o.id = v.opening_id "
                "WHERE v.customer_id = ? AND v.status = 'booked' AND o.start < ? AND ? < o.end",
                (customer_id, opening["end"], opening["start"]),
            ).fetchone()
            if clash:
                raise ConflictError(
                    f"Customer {customer_id} already has visit {visit_id(clash['id'])} at an "
                    "overlapping time."
                )
            (upcoming,) = c.execute(
                "SELECT COUNT(*) FROM visits v JOIN openings o ON o.id = v.opening_id "
                "WHERE v.customer_id = ? AND v.status = 'booked' AND o.start > ?",
                (customer_id, now),
            ).fetchone()
            if upcoming >= seed.MAX_UPCOMING_VISITS:
                raise ConflictError(
                    f"Customer {customer_id} already holds {upcoming} upcoming visits, the "
                    f"most allowed ({seed.MAX_UPCOMING_VISITS}). A visit must be cancelled or "
                    "completed before another is booked."
                )
            try:
                cur = c.execute(
                    "INSERT INTO visits (opening_id, customer_id, service, status) "
                    "VALUES (?, ?, ?, 'booked')",
                    (opening_id, customer_id, service),
                )
            except sqlite3.IntegrityError as exc:  # backstop: unique index on booked openings
                raise ConflictError(f"Opening {opening_id} is already booked.") from exc
            row = c.execute(_VISIT_SQL + " WHERE v.id = ?", (cur.lastrowid,)).fetchone()
        return _visit(row)

    def cancel_visit(self, visit: str, customer_id: str) -> VisitResult:
        n = parse_visit_id(visit)
        cutoff = (self._now() + timedelta(hours=seed.LATE_CANCEL_HOURS)).isoformat()
        with self._write() as c:
            row = (
                c.execute(_VISIT_SQL + " WHERE v.id = ?", (n,)).fetchone()
                if n is not None
                else None
            )
            if row is None or row["customer_id"] != customer_id:
                # Same message whether the ID is unknown or belongs to someone else.
                raise NotFoundError(f"Visit {visit} not found for customer {customer_id}.")
            if row["status"] == "cancelled":
                raise ConflictError(f"Visit {visit} is already cancelled.")
            if row["start"] <= cutoff:
                raise ConflictError(
                    f"Visit {visit} starts at {row['start']}, less than "
                    f"{seed.LATE_CANCEL_HOURS} hours from now; it can no longer be cancelled "
                    "online. The customer must call the studio."
                )
            c.execute("UPDATE visits SET status = 'cancelled' WHERE id = ?", (n,))
            row = c.execute(_VISIT_SQL + " WHERE v.id = ?", (n,)).fetchone()
        return _visit(row)

    def snapshot_visits(self) -> dict[int, VisitRow]:
        """Every visit, booked or cancelled, keyed by its number."""
        return {
            r["id"]: {
                "status": r["status"],
                "customer_id": r["customer_id"],
                "opening_id": r["opening_id"],
                "start": r["start"],
                "stylist_id": r["stylist_id"],
                "studio": r["studio"],
                "service": r["service"],
            }
            for r in self._read(_VISIT_SQL + " ORDER BY v.id")
        }


def date_range(start_date: date, end_date: date | None) -> date:
    """Validate a list_openings date range and return its (defaulted) end date."""
    for d in (start_date, end_date):
        if d is not None and not EARLIEST_DATE <= d <= LATEST_DATE:
            raise StoreError(
                f"Dates must be between {EARLIEST_DATE.isoformat()} and {LATEST_DATE.isoformat()}."
            )
    end_date = end_date or start_date + timedelta(days=6)
    if end_date < start_date:
        raise StoreError("end_date must be on or after start_date.")
    if (end_date - start_date).days >= MAX_RANGE_DAYS:
        latest = start_date + timedelta(days=MAX_RANGE_DAYS - 1)
        raise StoreError(
            f"Date range is limited to {MAX_RANGE_DAYS} days counting both ends: for "
            f"start_date {start_date.isoformat()} the latest end_date is {latest.isoformat()}."
        )
    return end_date


def stylist_not_found(stylist_id: str) -> NotFoundError:
    return NotFoundError(
        f"Stylist {stylist_id} not found. Call search_stylists to find stylist IDs."
    )


def customer_not_found(customer_id: str) -> NotFoundError:
    return NotFoundError(f"Customer {customer_id} not found.")


_VISIT_SQL = (
    "SELECT v.id, v.status, v.customer_id, v.service, v.opening_id, o.start, o.end, "
    "s.id AS stylist_id, s.name AS stylist_name, s.studio FROM visits v "
    "JOIN openings o ON o.id = v.opening_id JOIN stylists s ON s.id = o.stylist_id"
)


def _visit(r: sqlite3.Row) -> VisitResult:
    return {
        "visit_id": visit_id(r["id"]),
        "status": r["status"],
        "customer_id": r["customer_id"],
        "stylist_id": r["stylist_id"],
        "stylist_name": r["stylist_name"],
        "studio": r["studio"],
        "service": r["service"],
        "opening_id": r["opening_id"],
        "start": r["start"],
        "end": r["end"],
    }
