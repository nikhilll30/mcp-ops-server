"""A SQLite store base with one deadline per call. Domains subclass it with their schema.

The SDK runs sync tools in worker threads, so every statement goes through ``_lock``.
Writes use ``BEGIN IMMEDIATE`` so a check-then-insert is one transaction.

Every call gets one deadline, ``timeout_s`` from when it starts, covering waiting for the
in-process lock, waiting for another connection's database lock (SQLite's busy timeout is
set to the time left before BEGIN, reads and COMMIT), and statement execution (a progress
handler interrupts SQLite past the deadline). A write that reaches COMMIT after the
deadline is rolled back, so success is never reported late. Interrupting inside SQLite,
rather than abandoning a worker thread, means a timed-out write is rolled back and never
lands after the caller has been told it failed.

Encryption at rest (45 CFR 164.312(a)(2)(iv)): given a ``data_key`` and a file path, the
database lives in memory and the file on disk holds only AES-256-GCM ciphertext of it.
It is decrypted once at start-up, and every committed write that changed something
re-encrypts the whole database and replaces the file atomically before the call returns.
If that save fails, the in-memory database goes back to the last saved state and the call
fails, so what a caller is told always matches the file. Nothing plaintext touches the
disk: no journal, no WAL, temp tables in memory. The cost is a full rewrite per write
(fine for an ops database of a few MB, not for a large one) and one process per file,
enforced with an exclusive lock.
"""

import fcntl
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from mcp_ops_kit.crypto import DataKey, DecryptionError, atomic_write, seal, unseal
from mcp_ops_kit.errors import BusyError, CallTimeoutError, ConfigError, StoreError

ENCRYPTED_MAGIC = b"MCPOPS-ENC-SQLITE-1\n"
SQLITE_MAGIC = b"SQLite format 3\x00"


class SqliteStore:
    """Thread-safe facade over one SQLite connection. Subclasses implement ``_is_seeded``
    and ``_seed``, and use ``_read`` / ``_write`` for every statement.

    ``label`` names the store in errors ("The clinic database is busy"). ``now`` returns
    the domain's current local time (naive), pinned or real. With ``data_key`` and a file
    ``path``, the file is encrypted (see the module docstring).
    """

    def __init__(
        self,
        path: str,
        *,
        label: str,
        timeout_s: float,
        now: Callable[[], datetime],
        data_key: DataKey | None = None,
    ) -> None:
        self._encrypted_path = path if data_key is not None and path != ":memory:" else None
        self._lock_fd = -1
        image = None
        if self._encrypted_path is not None:
            assert data_key is not None  # noqa: S101 - narrowed above
            self._db_key = data_key.subkey("sqlite-db")
            self._lock_fd = _lock_exclusive(self._encrypted_path)
            path = ":memory:"
        try:
            if self._encrypted_path is not None:
                image = self._read_encrypted()
            self._conn = sqlite3.connect(
                path, timeout=timeout_s, check_same_thread=False, isolation_level=None
            )
            if self._encrypted_path is not None:
                if image is not None:
                    self._conn.deserialize(image)
                self._saved_image = image
                self._conn.execute("PRAGMA temp_store = MEMORY")
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._lock = threading.Lock()
            self._label = label
            self._timeout_s = timeout_s
            self._deadline = float("inf")
            self._conn.set_progress_handler(self._past_deadline, 1000)
            self._now = now
            self._initialize()
        except BaseException:
            self.close_lock()  # a refused or failed file must not stay locked for the next try
            raise

    # ---- for subclasses ------------------------------------------------------------------

    def _is_seeded(self, c: sqlite3.Connection) -> bool:
        raise NotImplementedError

    def _seed(self, c: sqlite3.Connection) -> None:
        """Create the schema and load the seed, inside the caller's transaction."""
        raise NotImplementedError

    # ---- deadline and locking ------------------------------------------------------------

    def _busy(self) -> BusyError:
        return BusyError(f"The {self._label} database is busy. Retry the call.")

    def _past_deadline(self) -> int:
        return 1 if time.monotonic() > self._deadline else 0

    def _check_deadline(self) -> None:
        if time.monotonic() > self._deadline:
            raise CallTimeoutError("The call took too long and was cancelled.")

    def _bound_busy_wait(self) -> None:
        """Let SQLite wait for another connection's lock only as long as the call has left."""
        self._check_deadline()
        remaining = min(self._deadline - time.monotonic(), self._timeout_s)
        self._conn.execute(f"PRAGMA busy_timeout = {max(1, int(remaining * 1000))}")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        # One deadline for the whole call, fixed before waiting for anything.
        deadline = time.monotonic() + self._timeout_s
        if not self._lock.acquire(timeout=self._timeout_s):
            raise self._busy()
        self._deadline = deadline
        try:
            self._bound_busy_wait()
            yield
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc):
                raise CallTimeoutError("The call took too long and was cancelled.") from exc
            if "locked" in str(exc):
                raise self._busy() from exc
            raise
        finally:
            self._deadline = float("inf")
            self._lock.release()

    def _now_iso(self) -> str:
        return self._now().isoformat()

    def close(self) -> None:
        self._conn.close()
        self.close_lock()

    def close_lock(self) -> None:
        if self._lock_fd >= 0:
            os.close(self._lock_fd)
            self._lock_fd = -1

    # ---- encryption at rest --------------------------------------------------------------

    def _read_encrypted(self) -> bytes | None:
        """The decrypted database image, or None for a new file. Refuses a plaintext
        SQLite file (it would be silently re-encrypted, leaving a plaintext copy behind
        wherever it came from) and a file this key cannot decrypt."""
        path = self._encrypted_path
        try:
            blob = Path(path).read_bytes()  # type: ignore[arg-type] - set when encrypting
        except FileNotFoundError:
            return None
        if blob.startswith(SQLITE_MAGIC):
            raise ConfigError(
                f"{path} is an unencrypted SQLite database. Move it away (it holds plaintext) "
                "and start again to create an encrypted one."
            )
        if not blob.startswith(ENCRYPTED_MAGIC):
            raise ConfigError(f"{path} is not an encrypted database file")
        try:
            return unseal(self._db_key, blob[len(ENCRYPTED_MAGIC) :], ENCRYPTED_MAGIC)
        except DecryptionError as exc:
            raise ConfigError(
                f"Cannot decrypt {path}: the wrong data key, or the file was changed"
            ) from exc

    def _save_encrypted(self) -> None:
        """After a COMMIT: write the new state, or put the old one back and fail the call."""
        image = self._conn.serialize()
        try:
            blob = ENCRYPTED_MAGIC + seal(self._db_key, image, ENCRYPTED_MAGIC)
            atomic_write(self._encrypted_path, blob)  # type: ignore[arg-type]
        except OSError as exc:
            if self._saved_image is not None:
                self._conn.deserialize(self._saved_image)
                self._conn.execute("PRAGMA foreign_keys = ON")
                self._conn.execute("PRAGMA temp_store = MEMORY")
            raise StoreError(
                f"The {self._label} database could not be saved, so nothing was changed. "
                "Retry later."
            ) from exc
        self._saved_image = image

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """One write transaction. Always leaves the connection outside a transaction.

        COMMIT itself can fail (e.g. "database is locked" while another connection holds a
        read lock on a file database), so it sits inside the rollback path too.
        """
        with self._locked():
            changes = self._conn.total_changes
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
                # Never report success late: past the deadline, roll back instead of committing.
                self._bound_busy_wait()
                self._conn.execute("COMMIT")
            except BaseException:
                if self._conn.in_transaction:
                    self._deadline = float("inf")  # never let the rollback itself be interrupted
                    self._conn.execute("ROLLBACK")
                raise
            if self._encrypted_path is not None and self._conn.total_changes != changes:
                self._save_encrypted()

    def _read(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._locked():
            return self._conn.execute(sql, params).fetchall()

    def _initialize(self) -> None:
        """Create and seed the schema once, even if several processes start together."""
        with self._write() as c:
            self._deadline = float("inf")  # one-off seeding is not a per-call operation
            # Checked inside BEGIN IMMEDIATE: a second process waits here, then sees the tables.
            if not self._is_seeded(c):
                self._seed(c)


def _lock_exclusive(path: str) -> int:
    """Hold ``<path>.lock`` for the store's lifetime: one process per encrypted file, since
    each keeps the database in its own memory."""
    fd = os.open(f"{path}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        raise ConfigError(f"{path} is in use by another process") from exc
    return fd


def require_key_for_file(path: str, data_key: DataKey | None, var: str) -> None:
    """A server never keeps its database in a plaintext file: a file path needs a key."""
    if path != ":memory:" and data_key is None:
        raise ConfigError(
            f"The database {path} is a file, so it must be encrypted: set {var}. "
            "Make a key with: python -m mcp_ops_kit.crypto new-key"
        )


def escape_like(text: str) -> str:
    """Escape ``text`` for ``LIKE ... ESCAPE '\\'`` so wildcards in it match literally."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def has_table(c: sqlite3.Connection, name: str) -> bool:
    sql = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?"
    return c.execute(sql, (name,)).fetchone() is not None


def run_script(c: sqlite3.Connection, schema: str) -> None:
    """Run ``;``-separated DDL inside the current transaction (``executescript`` would COMMIT)."""
    for stmt in schema.split(";"):
        if stmt.strip():
            c.execute(stmt)
