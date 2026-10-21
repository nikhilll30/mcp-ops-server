"""SQLite backend for the synthetic clinic, seeded from ``seed.py``.

Integrity rules live here, not in the tool layer, and the critical one is also a database
constraint: a partial unique index allows at most one *booked* appointment per slot, so a
double booking is impossible even if two calls race past the application-level check.
"""

import sqlite3
from collections.abc import Callable
from datetime import date, datetime, timedelta

from mcp_ops_kit.crypto import DataKey
from mcp_ops_kit.sqlite import SqliteStore, escape_like, has_table, run_script
from mcp_ops_server import seed
from mcp_ops_server.backends import base
from mcp_ops_server.backends.base import (
    AppointmentRow,
    ConflictError,
    appointment_id,
    parse_appointment_id,
)
from mcp_ops_server.models import (
    AppointmentResult,
    OpenSlotsResult,
    ProviderInfo,
    ReferralInfo,
    ReferralStatusResult,
    SearchProvidersResult,
    SlotInfo,
)

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


class SqliteBackend(SqliteStore):
    """The clinic over one SQLite connection, with the kit's per-call deadline and locking
    (``mcp_ops_kit.sqlite.SqliteStore``: lock wait, busy wait and execution share one
    deadline, and a write that reaches COMMIT late is rolled back).

    ``now`` returns the current clinic-local time; slots at or before it are in the past.
    With ``data_key``, a file database is encrypted at rest (the kit's ``SqliteStore``).
    """

    def __init__(
        self,
        path: str = ":memory:",
        seed_value: int = seed.SEED,
        timeout_s: float = 5.0,
        now: Callable[[], datetime] = lambda: seed.CLINIC_NOW,
        data_key: DataKey | None = None,
    ) -> None:
        self._seed_value = seed_value
        super().__init__(path, label="clinic", timeout_s=timeout_s, now=now, data_key=data_key)

    def _is_seeded(self, c: sqlite3.Connection) -> bool:
        return has_table(c, "providers")

    def _seed(self, c: sqlite3.Connection) -> None:
        self._load(c, seed.generate(self._seed_value))

    @staticmethod
    def _load(c: sqlite3.Connection, data: seed.SeedData) -> None:
        run_script(c, SCHEMA)
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
            params.append(f"%{escape_like(name.strip())}%")
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
        end_date = base.slot_range(start_date, end_date)
        if not self._read("SELECT 1 FROM providers WHERE id = ?", (provider_id,)):
            raise base.provider_not_found(provider_id)
        lo = datetime.combine(start_date, datetime.min.time()).isoformat()
        hi = datetime.combine(end_date + timedelta(days=1), datetime.min.time()).isoformat()
        rows = self._read(
            "SELECT s.id, s.start, s.end FROM slots s "
            "WHERE s.provider_id = ? AND s.start >= ? AND s.start < ? AND s.start > ? "
            "AND NOT EXISTS (SELECT 1 FROM appointments a "
            "                WHERE a.slot_id = s.id AND a.status = 'booked') "
            "ORDER BY s.start LIMIT ?",
            (provider_id, lo, hi, self._now_iso(), limit + 1),
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
            raise base.patient_not_found(patient_id)

    # ---- writes ------------------------------------------------------------------------

    def book_appointment(
        self, patient_id: str, slot_id: str, referral_id: str | None = None
    ) -> AppointmentResult:
        with self._write() as c:
            if not c.execute("SELECT 1 FROM patients WHERE id = ?", (patient_id,)).fetchone():
                raise base.patient_not_found(patient_id)
            slot = c.execute(
                "SELECT s.*, p.specialty, sp.requires_referral FROM slots s "
                "JOIN providers p ON p.id = s.provider_id "
                "JOIN specialties sp ON sp.name = p.specialty WHERE s.id = ?",
                (slot_id,),
            ).fetchone()
            if slot is None:
                raise base.slot_not_found(slot_id)
            if slot["start"] <= self._now_iso():
                raise base.slot_in_past(slot_id, slot["start"])
            if c.execute(
                "SELECT 1 FROM appointments WHERE slot_id = ? AND status = 'booked'", (slot_id,)
            ).fetchone():
                raise base.slot_taken(slot_id, slot["provider_id"], slot["start"])
            clash = c.execute(
                "SELECT a.id FROM appointments a JOIN slots s ON s.id = a.slot_id "
                "WHERE a.patient_id = ? AND a.status = 'booked' AND s.start < ? AND ? < s.end",
                (patient_id, slot["end"], slot["start"]),
            ).fetchone()
            if clash:
                raise base.patient_clash(patient_id, clash["id"])
            ref = None
            if referral_id is not None:
                ref = c.execute(
                    "SELECT patient_id, specialty, status FROM referrals WHERE id = ?",
                    (referral_id,),
                ).fetchone()
            base.check_referral(
                patient_id,
                slot["specialty"],
                bool(slot["requires_referral"]),
                referral_id,
                tuple(ref) if ref else None,
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

    def cancel_appointment(self, appointment_id: str, patient_id: str) -> AppointmentResult:
        n = parse_appointment_id(appointment_id)
        with self._write() as c:
            row = c.execute(_APPOINTMENT_SQL, (n,)).fetchone() if n is not None else None
            if row is None or row["patient_id"] != patient_id:
                raise base.appointment_not_found(appointment_id, patient_id)
            if row["status"] == "cancelled":
                raise base.already_cancelled(appointment_id)
            if row["start"] <= self._now_iso():
                raise base.already_started(appointment_id, row["start"])
            c.execute("UPDATE appointments SET status = 'cancelled' WHERE id = ?", (n,))
            row = c.execute(_APPOINTMENT_SQL, (n,)).fetchone()
        return _appointment(row)

    def snapshot_appointments(self) -> dict[int, AppointmentRow]:
        return {r["id"]: _snapshot_row(r) for r in self._read(_SNAPSHOT_SQL)}


_SNAPSHOT_SQL = (
    "SELECT a.id, a.status, a.patient_id, a.referral_id, a.slot_id, s.start, "
    "p.id AS provider_id, p.specialty, p.location FROM appointments a "
    "JOIN slots s ON s.id = a.slot_id JOIN providers p ON p.id = s.provider_id ORDER BY a.id"
)


def _snapshot_row(r: sqlite3.Row) -> AppointmentRow:
    return {
        "status": r["status"],
        "patient_id": r["patient_id"],
        "referral_id": r["referral_id"],
        "slot_id": r["slot_id"],
        "start": r["start"],
        "provider_id": r["provider_id"],
        "specialty": r["specialty"],
        "location": r["location"],
    }


_APPOINTMENT_SQL = (
    "SELECT a.id, a.status, a.patient_id, a.referral_id, a.slot_id, s.start, s.end, "
    "p.id AS provider_id, p.name AS provider_name, p.specialty FROM appointments a "
    "JOIN slots s ON s.id = a.slot_id JOIN providers p ON p.id = s.provider_id WHERE a.id = ?"
)


def _appointment(r: sqlite3.Row) -> AppointmentResult:
    return {
        "appointment_id": appointment_id(r["id"]),
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
