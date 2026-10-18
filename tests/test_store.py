"""Store behaviour not visible through a single tool call."""

from pathlib import Path

import pytest
from conftest import NO_REFERRAL_PATIENT, PRIMARY_PROVIDER

from mcp_ops_server import seed
from mcp_ops_server.store import NotFoundError, Store


def test_file_store_persists_bookings_and_is_not_reseeded(tmp_path: Path) -> None:
    path = str(tmp_path / "clinic.db")
    first = Store(path)
    slot_id = first.list_open_slots(PRIMARY_PROVIDER, seed.SCHEDULE_START)["slots"][0]["slot_id"]
    booked = first.book_appointment(NO_REFERRAL_PATIENT, slot_id)
    first.close()

    reopened = Store(path)
    open_ids = {
        s["slot_id"]
        for s in reopened.list_open_slots(PRIMARY_PROVIDER, seed.SCHEDULE_START)["slots"]
    }
    assert slot_id not in open_ids
    cancelled = reopened.cancel_appointment(booked["appointment_id"], NO_REFERRAL_PATIENT)
    assert cancelled["status"] == "cancelled"
    reopened.close()


def test_failed_write_rolls_back(store: Store) -> None:
    """An error mid-transaction must not leave the connection inside an open transaction."""
    with pytest.raises(NotFoundError):
        store.book_appointment("PAT-0999", "SLT-00001")
    slot_id = store.list_open_slots(PRIMARY_PROVIDER, seed.SCHEDULE_START)["slots"][0]["slot_id"]
    assert store.book_appointment(NO_REFERRAL_PATIENT, slot_id)["status"] == "booked"
