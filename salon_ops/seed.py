"""Deterministic synthetic salon data. Nothing here describes a real person or business.

The same ``SEED`` always yields the same rows, so tests and evals can rely on exact IDs.
"""

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

SEED = 20261103
SCHEDULE_START = date(2026, 11, 3)  # a Tuesday; the salon opens Tuesday to Saturday
SCHEDULE_WEEKS = 2
OPEN_WEEKDAYS = (1, 2, 3, 4, 5)  # Tue..Sat
# Simulated salon time, so the dataset never goes stale: Monday evening before the first
# opening day. Override with SALON_OPS_NOW (see config.py).
SALON_NOW = datetime(2026, 11, 2, 18, 0)
# One fixed UTC offset all year (no daylight saving); tools speak salon-local time.
SALON_TZ = timezone(timedelta(hours=1), "salon")
OPENING_MINUTES = 60
OPEN_HOURS = (time(10, 0), time(18, 0))
# Visits starting sooner than this cannot be cancelled online.
LATE_CANCEL_HOURS = 24
# A customer may hold at most this many upcoming booked visits.
MAX_UPCOMING_VISITS = 3

SERVICES = ("cut", "colour", "blow_dry", "beard_trim")
STUDIOS = ("riverside", "old_town")
# Gemstone names: obviously synthetic. (id, name, studio, services offered)
STYLISTS = (
    ("STY-01", "Agate", "riverside", ("cut", "blow_dry")),
    ("STY-02", "Beryl", "old_town", ("cut", "colour")),
    ("STY-03", "Coral", "riverside", ("colour", "blow_dry")),
    ("STY-04", "Jasper", "old_town", ("cut", "beard_trim")),
    ("STY-05", "Onyx", "riverside", ("beard_trim", "cut")),
    ("STY-06", "Topaz", "old_town", ("colour", "cut", "blow_dry")),
)
CUSTOMER_COUNT = 30
PREBOOKED_FRACTION = 0.15


@dataclass(frozen=True)
class Stylist:
    id: str
    name: str
    studio: str
    services: tuple[str, ...]


@dataclass(frozen=True)
class Opening:
    id: str
    stylist_id: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Visit:
    opening_id: str
    customer_id: str
    service: str


@dataclass
class SeedData:
    stylists: list[Stylist] = field(default_factory=list)
    customer_ids: list[str] = field(default_factory=list)
    openings: list[Opening] = field(default_factory=list)
    visits: list[Visit] = field(default_factory=list)


def _days() -> list[date]:
    days = [SCHEDULE_START + timedelta(days=i) for i in range(SCHEDULE_WEEKS * 7)]
    return [d for d in days if d.weekday() in OPEN_WEEKDAYS]


def generate(seed: int = SEED) -> SeedData:
    """Build the full synthetic dataset from ``seed``."""
    rng = random.Random(seed)  # noqa: S311 - reproducible test data, not security
    data = SeedData(stylists=[Stylist(*s) for s in STYLISTS])
    data.customer_ids = [f"CUS-{i + 1:04d}" for i in range(CUSTOMER_COUNT)]
    step = timedelta(minutes=OPENING_MINUTES)
    for stylist in data.stylists:
        day_off = rng.choice(OPEN_WEEKDAYS)  # each stylist works four of the five days
        for day in _days():
            if day.weekday() == day_off:
                continue
            t, stop = datetime.combine(day, OPEN_HOURS[0]), datetime.combine(day, OPEN_HOURS[1])
            while t + step <= stop:
                data.openings.append(
                    Opening(f"OPN-{len(data.openings) + 1:05d}", stylist.id, t, t + step)
                )
                t += step
    data.visits = _prebook(rng, data)
    return data


def _prebook(rng: random.Random, data: SeedData) -> list[Visit]:
    """Fill a fraction of openings with visits that obey the rules the tools enforce."""
    services = {s.id: s.services for s in data.stylists}
    busy: set[tuple[str, datetime]] = set()
    held: dict[str, int] = {}
    out = []
    for opening in data.openings:
        if rng.random() >= PREBOOKED_FRACTION:
            continue
        customer_id = rng.choice(data.customer_ids)
        service = rng.choice(services[opening.stylist_id])
        if (customer_id, opening.start) in busy or held.get(customer_id, 0) >= MAX_UPCOMING_VISITS:
            continue
        busy.add((customer_id, opening.start))
        held[customer_id] = held.get(customer_id, 0) + 1
        out.append(Visit(opening.id, customer_id, service))
    return out
