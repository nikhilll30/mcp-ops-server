"""The synthetic seed as FHIR R4: export it as transaction Bundles, load it, reset to it.

    python -m mcp_ops_server.backends.fhir_seed export --out seed-bundle.json
    python -m mcp_ops_server.backends.fhir_seed load --url http://127.0.0.1:8080/fhir

Every resource has a deterministic ID from ``seed.py`` and every entry is a PUT, so loading
twice changes nothing. Patients carry an identifier only: no names, no birth dates, nothing
that could describe a person. ``reset`` puts back exactly the seeded bookings (tests and
the eval harness call it before each test or task); the server itself never calls either.
"""

import argparse
import hashlib
import json
import sys
from typing import Any

import httpx2

from mcp_ops_server import seed
from mcp_ops_server.backends.fhir import (
    FHIR_JSON,
    REFERRAL_TO_FHIR,
    appointment_resource,
    put,
    ref,
    sequence_resource,
    slot_resource,
    specialty_concept,
    to_instant,
    transaction,
)

MARKER_ID = "mcp-ops-seed"
MARKER_URL = "urn:mcp-ops:seed"
PATIENT_SYSTEM = "urn:mcp-ops:patient"
# Slots are the bulk (2,688); a few Bundles keep each transaction a reasonable size.
SLOTS_PER_BUNDLE = 1000


def bundles(seed_value: int = seed.SEED) -> list[dict[str, Any]]:
    """The seed as transaction Bundles, in reference order. The marker is last, so a load
    that stopped part-way is never mistaken for a complete one."""
    data = seed.generate(seed_value)
    busy = {a.slot_id for a in data.appointments}

    first = [put({"resourceType": "Location", "id": loc, "name": loc}) for loc in seed.LOCATIONS]
    for pid in data.patient_ids:
        first.append(
            put(
                {
                    "resourceType": "Patient",
                    "id": pid,
                    "identifier": [{"system": PATIENT_SYSTEM, "value": pid}],
                }
            )
        )
    for p in data.providers:
        first.append(put({"resourceType": "Practitioner", "id": p.id, "name": [{"text": p.name}]}))
        first.append(
            put(
                {
                    "resourceType": "PractitionerRole",
                    "id": p.id,
                    "practitioner": ref("Practitioner", p.id),
                    "specialty": [specialty_concept(p.specialty)],
                    "location": [ref("Location", p.location)],
                }
            )
        )
        first.append(
            put(
                {
                    "resourceType": "Schedule",
                    "id": p.id,
                    "actor": [ref("PractitionerRole", p.id), ref("Practitioner", p.id)],
                }
            )
        )
    for r in data.referrals:
        first.append(
            put(
                {
                    "resourceType": "ServiceRequest",
                    "id": r.id,
                    "status": REFERRAL_TO_FHIR[r.status],
                    "intent": "order",
                    "code": specialty_concept(r.specialty),
                    "subject": ref("Patient", r.patient_id),
                    "authoredOn": r.issued_on.isoformat(),
                }
            )
        )

    slot_entries = [
        put(
            slot_resource(s.id, s.provider_id, to_instant(s.start), to_instant(s.end), s.id in busy)
        )
        for s in data.slots
    ]
    out = [first]
    out += [
        slot_entries[i : i + SLOTS_PER_BUNDLE]
        for i in range(0, len(slot_entries), SLOTS_PER_BUNDLE)
    ]
    last = [put(a) for a in seeded_appointments(seed_value)]
    last.append(put(sequence_resource(len(data.appointments))))
    out.append(last)
    digest = hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()
    out[-1].append(
        put(
            {
                "resourceType": "Basic",
                "id": MARKER_ID,
                "code": {"coding": [{"system": MARKER_URL, "code": "seed"}]},
                "extension": [{"url": MARKER_URL, "valueString": f"{seed_value}:{digest}"}],
            }
        )
    )
    return [transaction(entries) for entries in out]


def seeded_appointments(seed_value: int = seed.SEED) -> list[dict[str, Any]]:
    """Seeded bookings as Appointment resources, numbered 1..n like the SQLite rows."""
    data = seed.generate(seed_value)
    slots = {s.id: s for s in data.slots}
    specialty = {p.id: p.specialty for p in data.providers}
    out = []
    for n, a in enumerate(data.appointments, start=1):
        s = slots[a.slot_id]
        out.append(
            appointment_resource(
                n,
                "booked",
                a.patient_id,
                s.provider_id,
                specialty[s.provider_id],
                s.id,
                to_instant(s.start),
                to_instant(s.end),
                a.referral_id,
            )
        )
    return out


def _marker(seed_value: int) -> str:
    return bundles(seed_value)[-1]["entry"][-1]["resource"]["extension"][0]["valueString"]


class _Client:
    def __init__(self, url: str, timeout_s: float) -> None:
        self.base = url.rstrip("/")
        self.http = httpx2.Client(
            headers={"Accept": FHIR_JSON, "Cache-Control": "no-cache"}, timeout=timeout_s
        )

    def get(self, path: str, **params: Any) -> httpx2.Response:
        return self.http.get(f"{self.base}/{path}", params=params or None)

    def transact(self, entries: list[dict]) -> None:
        if not entries:
            return
        resp = self.http.post(
            f"{self.base}/",
            json=transaction(entries),
            headers={"Content-Type": FHIR_JSON},
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"FHIR transaction failed: HTTP {resp.status_code} {resp.text[:500]}"
            )

    def search_all(self, resource_type: str, **params: Any) -> list[dict]:
        resp = self.get(resource_type, _count=500, **params)
        out = []
        while True:
            resp.raise_for_status()
            bundle = resp.json()
            out += [e["resource"] for e in bundle.get("entry", [])]
            nxt = next((x["url"] for x in bundle.get("link", []) if x["relation"] == "next"), None)
            if nxt is None:
                return out
            resp = self.http.get(nxt)


def load(url: str, seed_value: int = seed.SEED, timeout_s: float = 300.0) -> bool:
    """Load the seed unless this exact seed is already there. Returns True if it loaded."""
    c = _Client(url, timeout_s)
    try:
        marker = c.get(f"Basic/{MARKER_ID}")
        if marker.status_code == 200 and (
            marker.json()["extension"][0]["valueString"] == _marker(seed_value)
        ):
            return False
        for bundle in bundles(seed_value):
            c.transact(bundle["entry"])
        return True
    finally:
        c.http.close()


def reset(url: str, seed_value: int = seed.SEED, timeout_s: float = 60.0) -> None:
    """Put the bookings back to the seed: drop new appointments, restore seeded ones and the
    slots they hold, free every other slot, and rewind the appointment sequence."""
    seeded = {a["id"]: a for a in seeded_appointments(seed_value)}
    held = {a["slot"][0]["reference"].split("/")[1] for a in seeded.values()}
    c = _Client(url, timeout_s)
    try:
        entries: list[dict] = []
        present = {a["id"]: a for a in c.search_all("Appointment")}
        for i in present:
            if i not in seeded:
                entries.append({"request": {"method": "DELETE", "url": f"Appointment/{i}"}})
        entries += [
            put(a) for i, a in seeded.items() if present.get(i, {}).get("status") != "booked"
        ]
        # Busy slots the seed does not hold, then held slots that are free (looked up by ID:
        # fetching every free slot would mean ~2,500 resources per reset).
        wrong = [s for s in c.search_all("Slot", status="busy") if s["id"] not in held]
        wrong += c.search_all("Slot", status="free", _id=",".join(sorted(held)))
        for s in wrong:
            s.pop("meta", None)
            entries.append(put({**s, "status": "busy" if s["id"] in held else "free"}))
        entries.append(put(sequence_resource(len(seeded))))
        c.transact(entries)
    finally:
        c.http.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m mcp_ops_server.backends.fhir_seed")
    sub = parser.add_subparsers(dest="cmd", required=True)
    export = sub.add_parser("export", help="write the seed as a JSON array of transaction Bundles")
    export.add_argument("--out", required=True)
    for name in ("load", "reset"):
        p = sub.add_parser(name, help=f"{name} the seed on a FHIR server")
        p.add_argument("--url", required=True)
    for p in sub.choices.values():
        p.add_argument("--seed", type=int, default=seed.SEED)
    args = parser.parse_args(argv)
    if args.cmd == "export":
        with open(args.out, "w") as f:
            json.dump(bundles(args.seed), f, indent=1)
    elif args.cmd == "load":
        loaded = load(args.url, args.seed)
        reset(args.url, args.seed)
        print("loaded" if loaded else "already loaded; bookings reset to the seed", file=sys.stderr)
    else:
        reset(args.url, args.seed)


if __name__ == "__main__":
    main()
