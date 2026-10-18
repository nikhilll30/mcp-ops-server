"""Deterministic synthetic clinic data. Nothing here describes a real person or clinic.

The same ``SEED`` always yields the same rows, so tests and evals can rely on exact IDs.
"""

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

SEED = 20261102
SCHEDULE_START = date(2026, 11, 2)  # a Monday; slots cover 4 weeks of weekdays from here
SCHEDULE_WEEKS = 4
SLOT_MINUTES = 30
# Clinic-local wall-clock time. 09:00-12:00 and 13:00-17:00, lunch break in between.
SESSIONS = ((time(9, 0), time(12, 0)), (time(13, 0), time(17, 0)))

# specialty -> requires an approved referral before booking
SPECIALTIES: dict[str, bool] = {
    "primary_care": False,
    "pediatrics": False,
    "cardiology": True,
    "dermatology": True,
    "orthopedics": True,
    "neurology": True,
}
LOCATIONS = ("north", "south", "downtown")
# Tree names: obviously synthetic, no first names.
PROVIDER_NAMES = (
    "Dr. Alder",
    "Dr. Birch",
    "Dr. Cedar",
    "Dr. Dogwood",
    "Dr. Elm",
    "Dr. Fir",
    "Dr. Hazel",
    "Dr. Juniper",
    "Dr. Larch",
    "Dr. Linden",
    "Dr. Maple",
    "Dr. Rowan",
)
PATIENT_COUNT = 40
REFERRAL_STATUSES = ("pending", "approved", "denied")
PREBOOKED_FRACTION = 0.12


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    specialty: str
    location: str


@dataclass(frozen=True)
class Slot:
    id: str
    provider_id: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Referral:
    id: str
    patient_id: str
    specialty: str
    status: str
    issued_on: date


@dataclass(frozen=True)
class Appointment:
    slot_id: str
    patient_id: str
    referral_id: str | None


@dataclass
class SeedData:
    providers: list[Provider] = field(default_factory=list)
    patient_ids: list[str] = field(default_factory=list)
    slots: list[Slot] = field(default_factory=list)
    referrals: list[Referral] = field(default_factory=list)
    appointments: list[Appointment] = field(default_factory=list)


def _schedule_days() -> list[date]:
    days = [SCHEDULE_START + timedelta(days=i) for i in range(SCHEDULE_WEEKS * 7)]
    return [d for d in days if d.weekday() < 5]


def _day_slots(day: date) -> list[tuple[datetime, datetime]]:
    out = []
    step = timedelta(minutes=SLOT_MINUTES)
    for start, end in SESSIONS:
        t, stop = datetime.combine(day, start), datetime.combine(day, end)
        while t + step <= stop:
            out.append((t, t + step))
            t += step
    return out


def generate(seed: int = SEED) -> SeedData:
    """Build the full synthetic dataset from ``seed``."""
    rng = random.Random(seed)  # noqa: S311 - reproducible test data, not security
    data = SeedData()

    specialties = list(SPECIALTIES)
    for i, name in enumerate(PROVIDER_NAMES):
        data.providers.append(
            Provider(
                id=f"PRV-{i + 1:03d}",
                name=name,
                specialty=specialties[i % len(specialties)],  # two providers per specialty
                location=LOCATIONS[i % len(LOCATIONS)],
            )
        )

    data.patient_ids = [f"PAT-{i + 1:04d}" for i in range(PATIENT_COUNT)]

    days = _schedule_days()
    for provider in data.providers:
        day_off = rng.randrange(5)  # each provider works four weekdays
        for day in days:
            if day.weekday() == day_off:
                continue
            for start, end in _day_slots(day):
                data.slots.append(Slot(f"SLT-{len(data.slots) + 1:05d}", provider.id, start, end))

    referral_specialties = [s for s, needed in SPECIALTIES.items() if needed]
    for patient_id in data.patient_ids:
        for specialty in rng.sample(referral_specialties, rng.choice((0, 1, 1, 2))):
            data.referrals.append(
                Referral(
                    id=f"REF-{len(data.referrals) + 1:04d}",
                    patient_id=patient_id,
                    specialty=specialty,
                    status=rng.choices(REFERRAL_STATUSES, weights=(3, 5, 2))[0],
                    issued_on=SCHEDULE_START - timedelta(days=rng.randint(1, 60)),
                )
            )

    data.appointments = _prebook(rng, data)
    return data


def _prebook(rng: random.Random, data: SeedData) -> list[Appointment]:
    """Fill a fraction of slots with bookings that obey the same rules the tools enforce."""
    specialty_of = {p.id: p.specialty for p in data.providers}
    approved = {(r.patient_id, r.specialty): r.id for r in data.referrals if r.status == "approved"}
    busy: set[tuple[str, datetime]] = set()
    out = []
    for slot in data.slots:
        if rng.random() >= PREBOOKED_FRACTION:
            continue
        specialty = specialty_of[slot.provider_id]
        patient_id = rng.choice(data.patient_ids)
        referral_id = approved.get((patient_id, specialty))
        if SPECIALTIES[specialty] and referral_id is None:
            continue
        if (patient_id, slot.start) in busy:
            continue
        busy.add((patient_id, slot.start))
        out.append(Appointment(slot.id, patient_id, referral_id))
    return out
