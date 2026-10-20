"""The salon seed is deterministic, synthetic, and obeys the rules the tools enforce."""

from collections import Counter
from datetime import timedelta

from salon_ops import seed


def test_seed_is_deterministic_and_pinned() -> None:
    a, b = seed.generate(), seed.generate()
    assert a == b
    assert (len(a.stylists), len(a.customer_ids), len(a.openings), len(a.visits)) == (
        6,
        30,
        384,
        42,
    )
    # Facts the salon tests and golden tasks rely on.
    held = Counter(v.customer_id for v in a.visits)
    for free in ("CUS-0005", "CUS-0008", "CUS-0011", "CUS-0015", "CUS-0024", "CUS-0027"):
        assert held[free] == 0, free
    assert held["CUS-0006"] == seed.MAX_UPCOMING_VISITS


def test_openings_are_hourly_inside_opening_hours_on_open_days() -> None:
    d = seed.generate()
    for o in d.openings:
        assert o.end - o.start == timedelta(minutes=seed.OPENING_MINUTES)
        assert o.start.weekday() in seed.OPEN_WEEKDAYS
        assert seed.OPEN_HOURS[0] <= o.start.time() and o.end.time() <= seed.OPEN_HOURS[1]
        assert o.start > seed.SALON_NOW
    worked = Counter((o.stylist_id, o.start.date()) for o in d.openings)
    for s in d.stylists:  # four days a week, eight openings a day
        days = {day for (sid, day) in worked if sid == s.id}
        assert len(days) == 4 * seed.SCHEDULE_WEEKS
        assert all(worked[(s.id, day)] == 8 for day in days)


def test_seeded_visits_obey_the_booking_rules() -> None:
    d = seed.generate()
    openings = {o.id: o for o in d.openings}
    offers = {s.id: s.services for s in d.stylists}
    assert len({v.opening_id for v in d.visits}) == len(d.visits)  # one per opening
    times = Counter((v.customer_id, openings[v.opening_id].start) for v in d.visits)
    assert max(times.values()) == 1  # nobody in two places at once
    assert max(Counter(v.customer_id for v in d.visits).values()) <= seed.MAX_UPCOMING_VISITS
    for v in d.visits:
        assert v.service in offers[openings[v.opening_id].stylist_id]


def test_names_are_obviously_synthetic() -> None:
    assert {s.name for s in seed.generate().stylists} == {
        "Agate", "Beryl", "Coral", "Jasper", "Onyx", "Topaz"
    }  # fmt: skip
