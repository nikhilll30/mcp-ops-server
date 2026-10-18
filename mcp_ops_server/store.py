"""SQLite store for the synthetic clinic, seeded from ``seed.py``.

Integrity rules live here, not in the tool layer, and the critical one is also a database
constraint: a partial unique index allows at most one *booked* appointment per slot, so a
double booking is impossible even if two calls race past the application-level check.
"""

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from mcp_ops_server import seed
from mcp_ops_server.models import (
    AppointmentResult,
    OpenSlotsResult,
    ProviderInfo,
    ReferralInfo,
    ReferralStatusResult,
    SearchProvidersResult,
    SlotInfo,
)

MAX_SLOT_RANGE_DAYS = 31
# Far wider than the 4-week schedule; keeps date arithmetic away from date.max.
EARLIEST_DATE = date(2000, 1, 1)
LATEST_DATE = date(2100, 12, 31)

SCHEMA = """
CREATE TABLE specialties (
    name TEXT PRIMARY KEY,
    requires_referral INTEGER NOT NULL CHECK (requires_referral IN (0, 1))
);
CREATE TABLE providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    specialty TEXT NOT NULL REFERENCES specialties(name),
    location TEXT NOT NULL
);
CREATE TABLE patients (id TEXT PRIMARY KEY);
CREATE TABLE slots (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL REFERENCES providers(id),
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    UNIQUE (provider_id, start)
);
CREATE TABLE referrals (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id),
    specialty TEXT NOT NULL REFERENCES specialties(name),
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'denied')),
    issued_on TEXT NOT NULL
);
CREATE TABLE appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id TEXT NOT NULL REFERENCES slots(id),
    patient_id TEXT NOT NULL REFERENCES patients(id),
    referral_id TEXT REFERENCES referrals(id),
    status TEXT NOT NULL CHECK (status IN ('booked', 'cancelled'))
);
CREATE UNIQUE INDEX one_booking_per_slot ON appointments(slot_id) WHERE status = 'booked';
CREATE INDEX appointments_by_patient ON appointments(patient_id, status);
"""


class StoreError(Exception):
    """An anticipated failure whose message is safe and useful to show an agent."""


class NotFoundError(StoreError):
    pass


class ConflictError(StoreError):
    pass


class ReferralError(StoreError):
    pass


class BusyError(StoreError):
    """Another connection held the database lock past the busy timeout."""


def _appointment_id(n: int) -> str:
    return f"APT-{n:05d}"


def _parse_appointment_id(appointment_id: str) -> int | None:
    prefix, _, digits = appointment_id.partition("-")
    ok = prefix == "APT" and digits.isascii() and digits.isdigit() and len(digits) <= 18
    return int(digits) if ok else None


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Store:
    """Thread-safe facade over one SQLite connection.

    The SDK runs sync tools in worker threads, so every statement goes through ``_lock``.
    Writes additionally use ``BEGIN IMMEDIATE`` so a check-then-insert is one transaction.
    """

    def __init__(
        self, path: str = ":memory:", seed_value: int = seed.SEED, busy_timeout: float = 5.0
    ) -> None:
        self._conn = sqlite3.connect(
            path, timeout=busy_timeout, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.Lock()
        self._seed_value = seed_value
        self._initialize()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """One write transaction. Always leaves the connection outside a transaction.

        COMMIT itself can fail (e.g. "database is locked" while another connection holds a
        read lock on a file database), so it sits inside the rollback path too.
        """
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException as exc:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                if isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc):
                    raise BusyError("The clinic database is busy. Retry the call.") from exc
                raise

    def _read(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _initialize(self) -> None:
        """Create and seed the schema once, even if several processes start together."""
        with self._write() as c:
            # Checked inside BEGIN IMMEDIATE: a second process waits here, then sees the tables.
            if c.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'providers'"
            ).fetchone():
                return
            self._load(c, seed.generate(self._seed_value))

    @staticmethod
    def _load(c: sqlite3.Connection, data: seed.SeedData) -> None:
        # Not executescript(): it would COMMIT in the middle of this transaction.
        for stmt in SCHEMA.split(";"):
            if stmt.strip():
                c.execute(stmt)
        c.executemany("INSERT INTO specialties VALUES (?, ?)", seed.SPECIALTIES.items())
        c.executemany(
            "INSERT INTO providers VALUES (?, ?, ?, ?)",
            [(p.id, p.name, p.specialty, p.location) for p in data.providers],
        )
        c.executemany("INSERT INTO patients VALUES (?)", [(p,) for p in data.patient_ids])
        c.executemany(
            "INSERT INTO slots VALUES (?, ?, ?, ?)",
            [(s.id, s.provider_id, s.start.isoformat(), s.end.isoformat()) for s in data.slots],
        )
        c.executemany(
            "INSERT INTO referrals VALUES (?, ?, ?, ?, ?)",
            [
                (r.id, r.patient_id, r.specialty, r.status, r.issued_on.isoformat())
                for r in data.referrals
            ],
        )
        c.executemany(
            "INSERT INTO appointments (slot_id, patient_id, referral_id, status) "
            "VALUES (?, ?, ?, 'booked')",
            [(a.slot_id, a.patient_id, a.referral_id) for a in data.appointments],
        )

    # ---- reads -------------------------------------------------------------------------

    def search_providers(
        self,
        specialty: str | None = None,
        location: str | None = None,
        name: str | None = None,
        limit: int = 20,
    ) -> SearchProvidersResult:
        sql = (
            "SELECT p.*, s.requires_referral FROM providers p "
            "JOIN specialties s ON s.name = p.specialty WHERE 1 = 1"
        )
        params: list = []
        if specialty:
            sql += " AND p.specialty = ?"
            params.append(specialty)
        if location:
            sql += " AND p.location = ?"
            params.append(location)
        if name:
            sql += " AND p.name LIKE ? ESCAPE '\\'"
            params.append(f"%{_escape_like(name.strip())}%")
        sql += " ORDER BY p.id LIMIT ?"
        params.append(limit)
        providers = [
            ProviderInfo(
                provider_id=r["id"],
                name=r["name"],
                specialty=r["specialty"],
                location=r["location"],
                requires_referral=bool(r["requires_referral"]),
            )
            for r in self._read(sql, tuple(params))
        ]
        return {"providers": providers, "count": len(providers)}

    def list_open_slots(
        self,
        provider_id: str,
        start_date: date,
        end_date: date | None = None,
        limit: int = 20,
    ) -> OpenSlotsResult:
        for d in (start_date, end_date):
            if d is not None and not EARLIEST_DATE <= d <= LATEST_DATE:
                raise StoreError(
                    f"Dates must be between {EARLIEST_DATE.isoformat()} and "
                    f"{LATEST_DATE.isoformat()}."
                )
        end_date = end_date or start_date + timedelta(days=6)
        if end_date < start_date:
            raise StoreError("end_date must be on or after start_date.")
        if (end_date - start_date).days >= MAX_SLOT_RANGE_DAYS:
            raise StoreError(f"Date range is limited to {MAX_SLOT_RANGE_DAYS} days.")
        if not self._read("SELECT 1 FROM providers WHERE id = ?", (provider_id,)):
            raise NotFoundError(
                f"Provider {provider_id} not found. Call search_providers to find provider IDs."
            )
        lo = datetime.combine(start_date, datetime.min.time()).isoformat()
        hi = datetime.combine(end_date + timedelta(days=1), datetime.min.time()).isoformat()
        rows = self._read(
            "SELECT s.id, s.start, s.end FROM slots s "
            "WHERE s.provider_id = ? AND s.start >= ? AND s.start < ? "
            "AND NOT EXISTS (SELECT 1 FROM appointments a "
            "                WHERE a.slot_id = s.id AND a.status = 'booked') "
            "ORDER BY s.start LIMIT ?",
            (provider_id, lo, hi, limit + 1),
        )
        slots = [SlotInfo(slot_id=r["id"], start=r["start"], end=r["end"]) for r in rows]
        return {
            "provider_id": provider_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "slots": slots[:limit],
            "count": min(len(slots), limit),
            "truncated": len(slots) > limit,
        }

    def get_referral_status(
        self, patient_id: str, specialty: str | None = None
    ) -> ReferralStatusResult:
        self._require_patient(patient_id)
        sql = "SELECT * FROM referrals WHERE patient_id = ?"
        params: tuple = (patient_id,)
        if specialty:
            sql += " AND specialty = ?"
            params += (specialty,)
        referrals = [
            ReferralInfo(
                referral_id=r["id"],
                specialty=r["specialty"],
                status=r["status"],
                issued_on=r["issued_on"],
            )
            for r in self._read(sql + " ORDER BY issued_on DESC, id", params)
        ]
        return {"patient_id": patient_id, "referrals": referrals, "count": len(referrals)}

    def _require_patient(self, patient_id: str) -> None:
        if not self._read("SELECT 1 FROM patients WHERE id = ?", (patient_id,)):
            raise NotFoundError(f"Patient {patient_id} not found.")

    # ---- writes ------------------------------------------------------------------------

    def book_appointment(
        self, patient_id: str, slot_id: str, referral_id: str | None = None
    ) -> AppointmentResult:
        with self._write() as c:
            if not c.execute("SELECT 1 FROM patients WHERE id = ?", (patient_id,)).fetchone():
                raise NotFoundError(f"Patient {patient_id} not found.")
            slot = c.execute(
                "SELECT s.*, p.specialty, sp.requires_referral FROM slots s "
                "JOIN providers p ON p.id = s.provider_id "
                "JOIN specialties sp ON sp.name = p.specialty WHERE s.id = ?",
                (slot_id,),
            ).fetchone()
            if slot is None:
                raise NotFoundError(
                    f"Slot {slot_id} not found. Call list_open_slots to find open slots."
                )
            if c.execute(
                "SELECT 1 FROM appointments WHERE slot_id = ? AND status = 'booked'", (slot_id,)
            ).fetchone():
                raise ConflictError(
                    f"Slot {slot_id} is already booked. Call list_open_slots for another slot."
                )
            clash = c.execute(
                "SELECT a.id FROM appointments a JOIN slots s ON s.id = a.slot_id "
                "WHERE a.patient_id = ? AND a.status = 'booked' AND s.start < ? AND ? < s.end",
                (patient_id, slot["end"], slot["start"]),
            ).fetchone()
            if clash:
                raise ConflictError(
                    f"Patient {patient_id} already has appointment "
                    f"{_appointment_id(clash['id'])} at an overlapping time."
                )
            self._check_referral(
                c, patient_id, slot["specialty"], slot["requires_referral"], referral_id
            )
            try:
                cur = c.execute(
                    "INSERT INTO appointments (slot_id, patient_id, referral_id, status) "
                    "VALUES (?, ?, ?, 'booked')",
                    (slot_id, patient_id, referral_id),
                )
            except sqlite3.IntegrityError as exc:  # backstop: unique index on booked slots
                raise ConflictError(f"Slot {slot_id} is already booked.") from exc
            row = c.execute(_APPOINTMENT_SQL, (cur.lastrowid,)).fetchone()
        return _appointment(row)

    @staticmethod
    def _check_referral(
        c: sqlite3.Connection,
        patient_id: str,
        specialty: str,
        required: int,
        referral_id: str | None,
    ) -> None:
        if referral_id is None:
            if required:
                raise ReferralError(
                    f"{specialty} requires an approved referral. Call get_referral_status for "
                    f"patient {patient_id} and pass an approved referral_id."
                )
            return
        ref = c.execute(
            "SELECT * FROM referrals WHERE id = ? AND patient_id = ?", (referral_id, patient_id)
        ).fetchone()
        if ref is None:
            raise ReferralError(f"Referral {referral_id} not found for patient {patient_id}.")
        if ref["specialty"] != specialty:
            raise ReferralError(
                f"Referral {referral_id} is for {ref['specialty']}, not {specialty}."
            )
        if ref["status"] != "approved":
            raise ReferralError(f"Referral {referral_id} is {ref['status']}, not approved.")

    def cancel_appointment(self, appointment_id: str, patient_id: str) -> AppointmentResult:
        n = _parse_appointment_id(appointment_id)
        with self._write() as c:
            row = c.execute(_APPOINTMENT_SQL, (n,)).fetchone() if n is not None else None
            # Same message whether the ID is unknown or belongs to someone else.
            if row is None or row["patient_id"] != patient_id:
                raise NotFoundError(
                    f"Appointment {appointment_id} not found for patient {patient_id}."
                )
            if row["status"] == "cancelled":
                raise ConflictError(f"Appointment {appointment_id} is already cancelled.")
            c.execute("UPDATE appointments SET status = 'cancelled' WHERE id = ?", (n,))
            row = c.execute(_APPOINTMENT_SQL, (n,)).fetchone()
        return _appointment(row)


_APPOINTMENT_SQL = (
    "SELECT a.id, a.status, a.patient_id, a.referral_id, a.slot_id, s.start, s.end, "
    "p.id AS provider_id, p.name AS provider_name, p.specialty FROM appointments a "
    "JOIN slots s ON s.id = a.slot_id JOIN providers p ON p.id = s.provider_id WHERE a.id = ?"
)


def _appointment(r: sqlite3.Row) -> AppointmentResult:
    return {
        "appointment_id": _appointment_id(r["id"]),
        "status": r["status"],
        "patient_id": r["patient_id"],
        "provider_id": r["provider_id"],
        "provider_name": r["provider_name"],
        "specialty": r["specialty"],
        "slot_id": r["slot_id"],
        "start": r["start"],
        "end": r["end"],
        "referral_id": r["referral_id"],
    }
