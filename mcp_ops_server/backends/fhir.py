"""FHIR R4 backend over plain REST (``httpx2``, already installed by ``mcp``).

Mapping (all IDs are the same IDs the SQLite backend uses):

- ``Practitioner/PRV-001`` holds the name; ``PractitionerRole/PRV-001`` holds specialty and
  ``Location``. A provider ID is the PractitionerRole ID.
- ``Schedule/PRV-001`` belongs to that role; each ``Slot`` points at it and is ``free`` or
  ``busy``. Slot times are UTC instants; tools see clinic-local time (``seed.CLINIC_TZ``).
- ``Appointment/APT-00001`` is ``booked`` or ``cancelled`` and points at its Slot, Patient,
  PractitionerRole and (if any) ``ServiceRequest``. A ServiceRequest is a referral: status
  ``draft``/``active``/``revoked`` is pending/approved/denied, specialty is its code.
- ``Basic/appointment-sequence`` holds the last appointment number, so new appointments are
  numbered APT-<n+1> like the SQLite autoincrement.

Integrity. A booking first reads the sequence, then makes every check, then commits one
transaction Bundle: the Slot goes ``busy`` with ``If-Match`` on the version that was checked
as free, the sequence moves on with ``If-Match`` on the version read first, and the
Appointment is created. If either changed, the server rejects the whole Bundle (412 or 409)
and the booking is re-checked from the start, so two concurrent books of one slot give one
success and one "already booked". Every booking moves the sequence, so a booking committed
while the checks ran always fails the sequence ``If-Match``: bookings are serialised the way
SQLite's ``BEGIN IMMEDIATE`` serialises them, and the per-patient overlap check cannot be
raced either. (Read the sequence after the slot and this breaks: see FAILURES.md.) Either
guard alone stops a double booking; both are kept. Cancelling moves the Appointment and its
Slot together, both with ``If-Match``.

Reads follow every ``next`` page, send ``Cache-Control: no-cache`` (HAPI otherwise reuses
cached search results for up to a minute, so a just-booked slot could still look open), and
never follow a link to a different server.
"""

import random
import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx2

from mcp_ops_server import seed
from mcp_ops_server.backends import base
from mcp_ops_server.backends.base import (
    AppointmentRow,
    BusyError,
    CallTimeoutError,
    StoreError,
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

SPECIALTY_SYSTEM = "urn:mcp-ops:specialty"
SEQUENCE_ID = "appointment-sequence"
SEQUENCE_URL = "urn:mcp-ops:appointment-sequence"
REFERRAL_TO_FHIR = {"pending": "draft", "approved": "active", "denied": "revoked"}
REFERRAL_FROM_FHIR = {v: k for k, v in REFERRAL_TO_FHIR.items()}
FHIR_JSON = "application/fhir+json"
_FHIR_ID = re.compile(r"^[A-Za-z0-9\-.]{1,64}$")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


class BackendUnavailableError(Exception):
    """The FHIR server cannot be used at start-up. The message is one line for the operator."""


class _VersionConflict(Exception):
    """A transaction lost an ``If-Match`` race; the caller re-checks and retries."""


# ---- time ------------------------------------------------------------------------------


def to_instant(local: datetime) -> str:
    """Clinic-local wall time (naive) to a FHIR instant in UTC."""
    return local.replace(tzinfo=seed.CLINIC_TZ).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_instant(value: str) -> datetime:
    """A FHIR instant with any offset (or Z) to naive clinic-local wall time."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"FHIR instant without an offset: {value!r}")
    return parsed.astimezone(seed.CLINIC_TZ).replace(tzinfo=None)


def _local_iso(value: str) -> str:
    return from_instant(value).isoformat()


def _ascii_lower(text: str) -> str:
    # SQLite's LIKE folds ASCII case only; match it exactly.
    return text.translate(_ASCII_LOWER)


# ---- resources (shared with fhir_seed) -------------------------------------------------


def ref(resource_type: str, resource_id: str) -> dict[str, str]:
    return {"reference": f"{resource_type}/{resource_id}"}


def ref_id(reference: dict[str, Any] | None, resource_type: str) -> str | None:
    value = (reference or {}).get("reference", "")
    prefix = f"{resource_type}/"
    return value[len(prefix) :] if value.startswith(prefix) else None


def specialty_concept(specialty: str) -> dict[str, Any]:
    return {"coding": [{"system": SPECIALTY_SYSTEM, "code": specialty}]}


def specialty_of(concepts: list[dict[str, Any]] | None) -> str | None:
    for concept in concepts or []:
        for coding in concept.get("coding", []):
            if coding.get("system") == SPECIALTY_SYSTEM:
                return coding.get("code")
    return None


def slot_resource(slot_id: str, provider_id: str, start: str, end: str, busy: bool) -> dict:
    return {
        "resourceType": "Slot",
        "id": slot_id,
        "schedule": ref("Schedule", provider_id),
        "status": "busy" if busy else "free",
        "start": start,
        "end": end,
    }


def appointment_resource(
    n: int,
    status: str,
    patient_id: str,
    provider_id: str,
    specialty: str,
    slot_id: str,
    start: str,
    end: str,
    referral_id: str | None,
) -> dict:
    resource: dict[str, Any] = {
        "resourceType": "Appointment",
        "id": appointment_id(n),
        "status": status,
        "specialty": [specialty_concept(specialty)],
        "start": start,
        "end": end,
        "slot": [ref("Slot", slot_id)],
        "participant": [
            {"actor": ref("Patient", patient_id), "status": "accepted"},
            {"actor": ref("PractitionerRole", provider_id), "status": "accepted"},
        ],
    }
    if referral_id is not None:
        resource["basedOn"] = [ref("ServiceRequest", referral_id)]
    return resource


def sequence_resource(last: int) -> dict:
    return {
        "resourceType": "Basic",
        "id": SEQUENCE_ID,
        "code": {"coding": [{"system": SEQUENCE_URL, "code": "appointment-sequence"}]},
        "extension": [{"url": SEQUENCE_URL, "valueInteger": last}],
    }


def put(resource: dict, if_match: str | None = None) -> dict:
    request: dict[str, str] = {
        "method": "PUT",
        "url": f"{resource['resourceType']}/{resource['id']}",
    }
    if if_match is not None:
        request["ifMatch"] = f'W/"{if_match}"'
    return {"resource": resource, "request": request}


def transaction(entries: list[dict]) -> dict:
    return {"resourceType": "Bundle", "type": "transaction", "entry": entries}


def _version(resource: dict) -> str:
    return resource["meta"]["versionId"]


def _number(appointment: dict) -> int:
    n = parse_appointment_id(appointment["id"])
    if n is None:
        raise StoreError(f"Unexpected appointment ID {appointment['id']!r} on the FHIR server.")
    return n


def _participant(appointment: dict, resource_type: str) -> str | None:
    for p in appointment.get("participant", []):
        found = ref_id(p.get("actor"), resource_type)
        if found is not None:
            return found
    return None


def _referral_of(appointment: dict) -> str | None:
    for r in appointment.get("basedOn", []):
        found = ref_id(r, "ServiceRequest")
        if found is not None:
            return found
    return None


# ---- backend ---------------------------------------------------------------------------


class FhirBackend:
    """``Backend`` over a FHIR R4 server. Thread-safe: no state but an ``httpx2.Client``.

    Every call gets one deadline, ``timeout_s`` from when it starts, covering every request
    and retry it makes. ``page_size`` is the ``_count`` sent with each search; results past
    the first page are always fetched (tests set it small to prove that).
    """

    def __init__(
        self,
        url: str,
        timeout_s: float = 5.0,
        now: Callable[[], datetime] = lambda: seed.CLINIC_NOW,
        page_size: int = 200,
        client: httpx2.Client | None = None,
    ) -> None:
        self._base = url.rstrip("/")
        self._origin = urlsplit(self._base)[:2]
        self._timeout_s = timeout_s
        self._now = now
        self._page_size = page_size
        self._http = client or httpx2.Client(
            headers={"Accept": FHIR_JSON, "Cache-Control": "no-cache"}, follow_redirects=False
        )
        self._local = threading.local()

    def close(self) -> None:
        self._http.close()

    # ---- start-up check ----------------------------------------------------------------

    def check(self) -> None:
        """Fail closed at start: the server must answer and hold the appointment sequence."""
        try:
            meta = self._http.get(f"{self._base}/metadata", timeout=self._timeout_s)
            seq = self._http.get(f"{self._base}/Basic/{SEQUENCE_ID}", timeout=self._timeout_s)
        except httpx2.HTTPError as exc:
            raise BackendUnavailableError(
                f"FHIR server {self._base} is unreachable ({type(exc).__name__}). "
                "Check MCP_OPS_FHIR_URL and that the server is up."
            ) from exc
        if meta.status_code != 200 or meta.json().get("fhirVersion", "").split(".")[0] != "4":
            raise BackendUnavailableError(
                f"{self._base}/metadata did not return an R4 CapabilityStatement "
                f"(HTTP {meta.status_code}). Check MCP_OPS_FHIR_URL."
            )
        if seq.status_code != 200:
            raise BackendUnavailableError(
                f"FHIR server {self._base} has no Basic/{SEQUENCE_ID} (HTTP {seq.status_code}). "
                f"Load the synthetic seed: python -m mcp_ops_server.backends.fhir_seed load "
                f"--url {self._base}"
            )

    # ---- HTTP --------------------------------------------------------------------------

    @contextmanager
    def _call(self) -> Iterator[None]:
        """One deadline for a whole tool call, kept per thread (calls run in parallel)."""
        self._local.deadline = time.monotonic() + self._timeout_s
        try:
            yield
        finally:
            self._local.deadline = None

    def _remaining(self) -> float:
        deadline = getattr(self._local, "deadline", None)
        if deadline is None:
            return self._timeout_s
        left = deadline - time.monotonic()
        if left <= 0:
            raise CallTimeoutError("The call took too long and was cancelled.")
        return left

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx2.Response:
        if not url.startswith(("http://", "https://")):
            url = f"{self._base}/{url}"
        elif urlsplit(url)[:2] != self._origin:
            raise StoreError("The clinic record system returned a link to another server.")
        try:
            return self._http.request(method, url, timeout=self._remaining(), **kwargs)
        except httpx2.TimeoutException as exc:
            raise CallTimeoutError("The call took too long and was cancelled.") from exc
        except httpx2.TransportError as exc:
            raise BusyError("The clinic record system is unavailable. Retry the call.") from exc

    def _read(self, resource_type: str, resource_id: str) -> dict | None:
        """One resource, or None if it does not exist (or was deleted)."""
        if not _FHIR_ID.match(resource_id):
            return None
        resp = self._request("GET", f"{resource_type}/{resource_id}")
        if resp.status_code in (404, 410):
            return None
        self._raise_for(resp)
        return resp.json()

    def _search(
        self, resource_type: str, params: dict[str, Any], want: int | None = None
    ) -> list[dict]:
        """Every match across all pages (or the first ``want``), included resources too."""
        params = {
            **params,
            "_count": self._page_size if want is None else min(self._page_size, want),
        }
        resp = self._request("GET", resource_type, params=params)
        out: list[dict] = []
        while True:
            self._raise_for(resp)
            bundle = resp.json()
            out.extend(e["resource"] for e in bundle.get("entry", []))
            matches = sum(1 for r in out if r["resourceType"] == resource_type)
            nxt = next(
                (link["url"] for link in bundle.get("link", []) if link["relation"] == "next"), None
            )
            if nxt is None or (want is not None and matches >= want):
                return out
            resp = self._request("GET", nxt)

    def _transact(self, entries: list[dict]) -> None:
        resp = self._request(
            "POST", "", json=transaction(entries), headers={"Content-Type": FHIR_JSON}
        )
        if resp.status_code in (409, 412):
            raise _VersionConflict(resp.text[:200])
        self._raise_for(resp)

    @staticmethod
    def _raise_for(resp: httpx2.Response) -> None:
        if resp.status_code >= 500 or resp.status_code == 429:
            raise BusyError("The clinic record system is unavailable. Retry the call.")
        if resp.status_code >= 400:
            # Not shown verbatim: an OperationOutcome can echo request data.
            raise StoreError(
                f"The clinic record system rejected the request (HTTP {resp.status_code})."
            )

    def _retrying(self) -> Iterator[int]:
        """Attempts for an optimistic write, with jittered backoff, until the deadline."""
        attempt = 0
        while True:
            yield attempt
            attempt += 1
            time.sleep(min(self._remaining(), random.uniform(0.005, 0.02) * min(attempt, 10)))  # noqa: S311 - jitter

    # ---- lookups -----------------------------------------------------------------------

    def _providers(self, params: dict[str, Any]) -> list[ProviderInfo]:
        """Matching PractitionerRoles with their Practitioner's name, sorted by ID."""
        found = self._search(
            "PractitionerRole", {**params, "_include": "PractitionerRole:practitioner"}
        )
        names = {
            r["id"]: r.get("name", [{}])[0].get("text", "")
            for r in found
            if r["resourceType"] == "Practitioner"
        }
        out = []
        for role in found:
            if role["resourceType"] != "PractitionerRole":
                continue
            specialty = specialty_of(role.get("specialty"))
            location = ref_id((role.get("location") or [{}])[0], "Location")
            practitioner = ref_id(role.get("practitioner"), "Practitioner")
            if specialty not in seed.SPECIALTIES or practitioner not in names:
                raise StoreError(f"Provider {role['id']} is incomplete on the FHIR server.")
            info = ProviderInfo(
                provider_id=role["id"],
                name=names[practitioner],
                specialty=specialty,  # type: ignore[typeddict-item]
                location=location,  # type: ignore[typeddict-item]
                requires_referral=seed.SPECIALTIES[specialty],
            )
            out.append(info)
        return sorted(out, key=lambda p: p["provider_id"])

    def _provider(self, provider_id: str) -> ProviderInfo | None:
        if not _FHIR_ID.match(provider_id):
            return None
        found = self._providers({"_id": provider_id})
        return found[0] if found else None

    def _require_patient(self, patient_id: str) -> None:
        if self._read("Patient", patient_id) is None:
            raise base.patient_not_found(patient_id)

    # ---- reads -------------------------------------------------------------------------

    def search_providers(
        self,
        specialty: str | None = None,
        location: str | None = None,
        name: str | None = None,
        limit: int = 20,
    ) -> SearchProvidersResult:
        params: dict[str, Any] = {}
        if specialty:
            params["specialty"] = f"{SPECIALTY_SYSTEM}|{specialty}"
        if location:
            params["location"] = f"Location/{location}"
        with self._call():
            found = self._providers(params)
        if name:
            # Filtered here, not with FHIR's name:contains: that is server-defined (accent and
            # case folding vary) and must match SQLite's literal, ASCII-case-folded substring.
            needle = _ascii_lower(name.strip())
            found = [p for p in found if needle in _ascii_lower(p["name"])]
        providers = found[:limit]
        return {"providers": providers, "count": len(providers)}

    def list_open_slots(
        self,
        provider_id: str,
        start_date: date,
        end_date: date | None = None,
        limit: int = 20,
    ) -> OpenSlotsResult:
        end_date = base.slot_range(start_date, end_date)
        with self._call():
            if self._provider(provider_id) is None:
                raise base.provider_not_found(provider_id)
            lo = datetime.combine(start_date, datetime.min.time())
            hi = datetime.combine(end_date + timedelta(days=1), datetime.min.time())
            found = self._search(
                "Slot",
                {
                    "schedule": f"Schedule/{provider_id}",
                    "status": "free",
                    "start": [f"ge{to_instant(lo)}", f"lt{to_instant(hi)}"],
                    "_sort": "start",
                },
                want=limit + 1,
            )
        now = self._now()
        slots = sorted(
            (
                SlotInfo(slot_id=s["id"], start=_local_iso(s["start"]), end=_local_iso(s["end"]))
                for s in found
                if s["resourceType"] == "Slot" and from_instant(s["start"]) > now
            ),
            key=lambda s: s["start"],
        )
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
        params = {"subject": f"Patient/{patient_id}"}
        if specialty:
            params["code"] = f"{SPECIALTY_SYSTEM}|{specialty}"
        with self._call():
            self._require_patient(patient_id)
            found = self._search("ServiceRequest", params)
        referrals = [
            ReferralInfo(
                referral_id=r["id"],
                specialty=specialty_of([r.get("code", {})]),  # type: ignore[typeddict-item]
                status=REFERRAL_FROM_FHIR[r["status"]],  # type: ignore[typeddict-item]
                issued_on=r["authoredOn"][:10],
            )
            for r in found
            if r["resourceType"] == "ServiceRequest"
        ]
        referrals.sort(key=lambda r: r["referral_id"])
        referrals.sort(key=lambda r: r["issued_on"], reverse=True)
        return {"patient_id": patient_id, "referrals": referrals, "count": len(referrals)}

    # ---- writes ------------------------------------------------------------------------

    def book_appointment(
        self, patient_id: str, slot_id: str, referral_id: str | None = None
    ) -> AppointmentResult:
        with self._call():
            for _ in self._retrying():
                try:
                    return self._try_book(patient_id, slot_id, referral_id)
                except _VersionConflict:
                    continue  # something moved: check everything again
        raise AssertionError("unreachable")  # pragma: no cover

    def _try_book(
        self, patient_id: str, slot_id: str, referral_id: str | None
    ) -> AppointmentResult:
        # The sequence is read before anything else: every booking moves it, so if its version
        # still matches at commit, no booking landed while these checks ran.
        seq = self._read("Basic", SEQUENCE_ID)
        if seq is None:
            raise StoreError("The clinic record system has no appointment sequence.")
        # Same checks, in the same order, as the SQLite backend.
        self._require_patient(patient_id)
        slot = self._read("Slot", slot_id)
        if slot is None:
            raise base.slot_not_found(slot_id)
        start, end = _local_iso(slot["start"]), _local_iso(slot["end"])
        provider_id = ref_id(slot.get("schedule"), "Schedule") or ""
        if from_instant(slot["start"]) <= self._now():
            raise base.slot_in_past(slot_id, start)
        if slot["status"] != "free":
            raise base.slot_taken(slot_id, provider_id, start)
        provider = self._provider(provider_id)
        if provider is None:
            raise StoreError(f"Slot {slot_id} has no provider on the FHIR server.")
        clash = [
            _number(a)
            for a in self._search(
                "Appointment", {"patient": f"Patient/{patient_id}", "status": "booked"}
            )
            if a["resourceType"] == "Appointment"
            and from_instant(a["start"]) < from_instant(slot["end"])
            and from_instant(slot["start"]) < from_instant(a["end"])
        ]
        if clash:
            raise base.patient_clash(patient_id, min(clash))
        referral = None
        if referral_id is not None:
            sr = self._read("ServiceRequest", referral_id)
            if sr is not None:
                referral = (
                    ref_id(sr.get("subject"), "Patient") or "",
                    specialty_of([sr.get("code", {})]) or "",
                    REFERRAL_FROM_FHIR.get(sr.get("status", ""), sr.get("status", "")),
                )
        base.check_referral(
            patient_id, provider["specialty"], provider["requires_referral"], referral_id, referral
        )
        n = seq["extension"][0]["valueInteger"] + 1
        appointment = appointment_resource(
            n,
            "booked",
            patient_id,
            provider_id,
            provider["specialty"],
            slot_id,
            slot["start"],
            slot["end"],
            referral_id,
        )
        self._transact(
            [
                put({**slot, "status": "busy"}, if_match=_version(slot)),
                put(sequence_resource(n), if_match=_version(seq)),
                put(appointment),
            ]
        )
        return _result(appointment, provider, start, end)

    def cancel_appointment(self, appointment_id: str, patient_id: str) -> AppointmentResult:
        with self._call():
            for _ in self._retrying():
                try:
                    return self._try_cancel(appointment_id, patient_id)
                except _VersionConflict:
                    continue
        raise AssertionError("unreachable")  # pragma: no cover

    def _try_cancel(self, appointment_id: str, patient_id: str) -> AppointmentResult:
        n = parse_appointment_id(appointment_id)
        appt = self._read("Appointment", base.appointment_id(n)) if n is not None else None
        if appt is None or _participant(appt, "Patient") != patient_id:
            raise base.appointment_not_found(appointment_id, patient_id)
        start, end = _local_iso(appt["start"]), _local_iso(appt["end"])
        if appt["status"] == "cancelled":
            raise base.already_cancelled(appointment_id)
        if from_instant(appt["start"]) <= self._now():
            raise base.already_started(appointment_id, start)
        slot_id = ref_id(appt["slot"][0], "Slot") or ""
        slot = self._read("Slot", slot_id)
        provider = self._provider(_participant(appt, "PractitionerRole") or "")
        if slot is None or provider is None:
            raise StoreError(f"Appointment {appointment_id} is incomplete on the FHIR server.")
        cancelled = {**appt, "status": "cancelled"}
        self._transact(
            [
                put(cancelled, if_match=_version(appt)),
                put({**slot, "status": "free"}, if_match=_version(slot)),
            ]
        )
        return _result(cancelled, provider, start, end)

    # ---- harness -----------------------------------------------------------------------

    def snapshot_appointments(self) -> dict[int, AppointmentRow]:
        with self._call():
            providers = {p["provider_id"]: p for p in self._providers({})}
            found = self._search("Appointment", {})
        out: dict[int, AppointmentRow] = {}
        for a in sorted((a for a in found if a["resourceType"] == "Appointment"), key=_number):
            provider = providers[_participant(a, "PractitionerRole") or ""]
            out[_number(a)] = {
                "status": a["status"],
                "patient_id": _participant(a, "Patient") or "",
                "referral_id": _referral_of(a),
                "slot_id": ref_id(a["slot"][0], "Slot") or "",
                "start": _local_iso(a["start"]),
                "provider_id": provider["provider_id"],
                "specialty": provider["specialty"],
                "location": provider["location"],
            }
        return out


def _result(appt: dict, provider: ProviderInfo, start: str, end: str) -> AppointmentResult:
    return {
        "appointment_id": appt["id"],
        "status": appt["status"],
        "patient_id": _participant(appt, "Patient") or "",
        "provider_id": provider["provider_id"],
        "provider_name": provider["name"],
        "specialty": provider["specialty"],
        "slot_id": ref_id(appt["slot"][0], "Slot") or "",
        "start": start,
        "end": end,
        "referral_id": _referral_of(appt),
    }
