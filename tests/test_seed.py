"""The synthetic dataset is deterministic and obeys the same rules the tools enforce."""

from collections import Counter
from datetime import time

from mcp_ops_server import seed


def test_same_seed_same_data() -> None:
    assert seed.generate() == seed.generate()


def test_different_seed_different_data() -> None:
    assert seed.generate(1) != seed.generate(2)


def test_shape() -> None:
    d = seed.generate()
    assert len(d.providers) == 12
    assert len(d.patient_ids) == 40
    assert Counter(p.specialty for p in d.providers) == {s: 2 for s in seed.SPECIALTIES}
    assert {r.status for r in d.referrals} == set(seed.REFERRAL_STATUSES)
    assert len({s.id for s in d.slots}) == len(d.slots)
    assert 0 < len(d.appointments) < len(d.slots)


def test_slots_are_weekday_clinic_hours() -> None:
    for s in seed.generate().slots:
        assert s.start.weekday() < 5
        assert (s.end - s.start).total_seconds() == seed.SLOT_MINUTES * 60
        assert any(lo <= s.start.time() and s.end.time() <= hi for lo, hi in seed.SESSIONS)
        assert not (time(12) <= s.start.time() < time(13))


def test_prebooked_appointments_obey_booking_rules() -> None:
    d = seed.generate()
    slot = {s.id: s for s in d.slots}
    specialty = {p.id: p.specialty for p in d.providers}
    referral = {r.id: r for r in d.referrals}
    seen_slots, seen_patient_times = set(), set()
    for a in d.appointments:
        s = slot[a.slot_id]
        assert a.slot_id not in seen_slots
        assert (a.patient_id, s.start) not in seen_patient_times
        seen_slots.add(a.slot_id)
        seen_patient_times.add((a.patient_id, s.start))
        if seed.SPECIALTIES[specialty[s.provider_id]]:
            r = referral[a.referral_id]
            assert (r.patient_id, r.specialty, r.status) == (
                a.patient_id,
                specialty[s.provider_id],
                "approved",
            )


def test_fixtures_used_by_tests_and_evals() -> None:
    """If this fails, the seed changed: update tests/conftest.py and any eval golden files."""
    d = seed.generate()
    refs = {r.id: (r.patient_id, r.specialty, r.status) for r in d.referrals}
    assert refs["REF-0001"] == ("PAT-0001", "dermatology", "approved")
    assert refs["REF-0002"] == ("PAT-0002", "orthopedics", "approved")
    assert refs["REF-0003"] == ("PAT-0002", "cardiology", "pending")
    assert refs["REF-0005"] == ("PAT-0007", "neurology", "denied")
    for free in ("PAT-0008", "PAT-0021"):
        assert not [r for r in d.referrals if r.patient_id == free]
        assert not [a for a in d.appointments if a.patient_id == free]
    providers = {p.id: p.specialty for p in d.providers}
    assert providers["PRV-001"] == "primary_care"
    assert providers["PRV-003"] == "cardiology"
    assert providers["PRV-004"] == "dermatology"
