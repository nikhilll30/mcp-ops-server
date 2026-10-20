"""Encryption at rest (Part 9): the data key, and SQLite files that hold only ciphertext."""

import os
from datetime import date
from pathlib import Path

import pytest
from conftest import NO_REFERRAL_PATIENT, PRIMARY_PROVIDER

from mcp_ops_kit import sqlite as kit_sqlite
from mcp_ops_kit.crypto import DataKey, DecryptionError, seal, unseal
from mcp_ops_kit.errors import ConfigError, ConflictError, StoreError
from mcp_ops_server.backends import make_backend
from mcp_ops_server.backends.sqlite import SqliteBackend
from mcp_ops_server.config import Settings
from salon_ops.config import Settings as SalonSettings
from salon_ops.server import make_store
from salon_ops.store import SalonStore

WEEK_ONE = date(2026, 11, 2)


@pytest.fixture
def key() -> DataKey:
    return DataKey.generate()


def _first_slot(b: SqliteBackend) -> str:
    return b.list_open_slots(PRIMARY_PROVIDER, WEEK_ONE)["slots"][0]["slot_id"]


# ---- the key -------------------------------------------------------------------------------


def test_a_key_round_trips_as_text_and_never_shows_in_a_repr(key: DataKey) -> None:
    assert DataKey.from_text(key.to_text()) == key
    assert key.to_text() not in repr(key)
    settings = Settings(data_key=key)
    assert key.to_text() not in repr(settings) and "data_key" not in repr(settings)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("dG9vLXNob3J0", "32 bytes"),  # valid base64url of 9 bytes
        ("A" * 43 + "!", "not base64url"),  # strict: a stray character is not skipped
        ("A" * 42 + " A", "not base64url"),
        ("A" * 44 + "AAAA", "32 bytes"),
    ],
)
def test_a_malformed_key_is_refused_naming_the_variable(text: str, reason: str) -> None:
    with pytest.raises(ConfigError, match=reason) as exc:
        DataKey.from_text(text, "MCP_OPS_DATA_KEY")
    assert "MCP_OPS_DATA_KEY" in str(exc.value) and text not in str(exc.value)


def test_settings_read_the_key_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, key: DataKey
) -> None:
    monkeypatch.setenv("MCP_OPS_DATA_KEY", key.to_text())
    monkeypatch.setenv("MCP_OPS_AUDIT_LOG", "/var/log/audit.log")
    s = Settings.from_env()
    assert s.data_key == key and s.audit_log == "/var/log/audit.log"
    monkeypatch.setenv("MCP_OPS_DATA_KEY", "nope")
    with pytest.raises(ConfigError, match="MCP_OPS_DATA_KEY"):
        Settings.from_env()


def test_subkeys_differ_by_purpose_and_blobs_are_bound_to_their_aad(key: DataKey) -> None:
    assert key.subkey("audit-enc") != key.subkey("audit-mac") != key.subkey("sqlite-db")
    assert key.subkey("audit-enc") == key.subkey("audit-enc")
    blob = seal(key.subkey("x"), b"secret", b"here")
    assert unseal(key.subkey("x"), blob, b"here") == b"secret"
    for wrong_key, aad in ((key.subkey("y"), b"here"), (key.subkey("x"), b"there")):
        with pytest.raises(DecryptionError):
            unseal(wrong_key, blob, aad)
    flipped = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(DecryptionError):
        unseal(key.subkey("x"), flipped, b"here")


# ---- encrypted SQLite ----------------------------------------------------------------------


def test_the_file_holds_no_plaintext_and_reopens(tmp_path: Path, key: DataKey) -> None:
    path = tmp_path / "clinic.db"
    b = SqliteBackend(str(path), data_key=key)
    booked = b.book_appointment(NO_REFERRAL_PATIENT, _first_slot(b))
    b.close()
    raw = path.read_bytes()
    assert raw.startswith(kit_sqlite.ENCRYPTED_MAGIC)
    for plaintext in (b"SQLite format 3", b"PAT-0", b"PRV-0", b"booked", b"cardiology", b"CREATE"):
        assert plaintext not in raw
    assert path.stat().st_mode & 0o777 == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == ["clinic.db", "clinic.db.lock"]
    again = SqliteBackend(str(path), data_key=key)
    n = int(booked["appointment_id"].removeprefix("APT-"))
    assert again.snapshot_appointments()[n]["status"] == "booked"
    again.close()


def test_the_wrong_key_or_a_changed_file_is_refused(tmp_path: Path, key: DataKey) -> None:
    path = tmp_path / "clinic.db"
    SqliteBackend(str(path), data_key=key).close()
    with pytest.raises(ConfigError, match="wrong data key"):
        SqliteBackend(str(path), data_key=DataKey.generate())
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 1
    path.write_bytes(bytes(data))
    with pytest.raises(ConfigError, match="the file was changed"):
        SqliteBackend(str(path), data_key=key)
    path.unlink()  # a refused file was not left locked: a fresh one opens in this process
    SqliteBackend(str(path), data_key=key).close()


def test_a_plaintext_database_is_not_adopted(tmp_path: Path, key: DataKey) -> None:
    """Silently encrypting it would leave the plaintext original wherever it came from."""
    path = tmp_path / "clinic.db"
    SqliteBackend(str(path)).close()  # plaintext, as before Part 9
    with pytest.raises(ConfigError, match="unencrypted SQLite database"):
        SqliteBackend(str(path), data_key=key)


def test_one_process_per_encrypted_file(tmp_path: Path, key: DataKey) -> None:
    path = str(tmp_path / "salon.db")
    first = SalonStore(path, data_key=key)
    with pytest.raises(ConfigError, match="in use by another process"):
        SalonStore(path, data_key=key)
    first.close()
    SalonStore(path, data_key=key).close()


def test_a_failed_save_undoes_the_write(
    tmp_path: Path, key: DataKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caller is told the write failed, and that is true in memory and on disk."""
    path = tmp_path / "clinic.db"
    b = SqliteBackend(str(path), data_key=key)
    slot = _first_slot(b)
    before_disk, before_rows = path.read_bytes(), b.snapshot_appointments()

    def disk_full(*args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(kit_sqlite, "atomic_write", disk_full)
    with pytest.raises(StoreError, match="could not be saved, so nothing was changed"):
        b.book_appointment(NO_REFERRAL_PATIENT, slot)
    assert b.snapshot_appointments() == before_rows
    assert path.read_bytes() == before_disk
    assert _first_slot(b) == slot
    monkeypatch.undo()
    assert b.book_appointment(NO_REFERRAL_PATIENT, slot)["status"] == "booked"  # still usable
    b.close()


def test_a_refused_write_does_not_rewrite_the_file(tmp_path: Path, key: DataKey) -> None:
    path = tmp_path / "clinic.db"
    b = SqliteBackend(str(path), data_key=key)
    slot = _first_slot(b)
    b.book_appointment(NO_REFERRAL_PATIENT, slot)
    inode = os.stat(path).st_ino
    with pytest.raises(ConflictError):
        b.book_appointment("PAT-0021", slot)
    assert os.stat(path).st_ino == inode  # no new file was renamed into place
    b.close()


def test_servers_refuse_a_database_file_without_a_key(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="must be encrypted: set MCP_OPS_DATA_KEY"):
        make_backend(Settings(db_path=str(tmp_path / "c.db")))
    with pytest.raises(ConfigError, match="must be encrypted: set SALON_OPS_DATA_KEY"):
        make_store(SalonSettings(db_path=str(tmp_path / "s.db")))
    assert not any(tmp_path.iterdir())
    make_backend(Settings()).close()  # in memory: nothing on disk, no key needed


def test_a_save_that_fails_after_the_rename_is_kept(
    tmp_path: Path, key: DataKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cursor Bugbot, PR #20: atomic_write can fail after the rename (fsyncing the folder).
    The new state is then already the file, so undoing it in memory told the caller
    "nothing changed" while a restart showed the booking. Now caller, memory and disk agree."""
    import stat

    path = tmp_path / "clinic.db"
    b = SqliteBackend(str(path), data_key=key)
    slot = _first_slot(b)
    real_fsync = os.fsync

    def fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(5, "Input/output error")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    booked = b.book_appointment(NO_REFERRAL_PATIENT, slot)
    monkeypatch.undo()
    n = int(booked["appointment_id"].removeprefix("APT-"))
    assert b.snapshot_appointments()[n]["status"] == "booked"
    b.close()
    assert SqliteBackend(str(path), data_key=key).snapshot_appointments()[n]["status"] == "booked"
