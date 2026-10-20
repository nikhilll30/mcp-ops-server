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
"""

import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime

from mcp_ops_kit.errors import BusyError, CallTimeoutError


class SqliteStore:
    """Thread-safe facade over one SQLite connection. Subclasses implement ``_is_seeded``
    and ``_seed``, and use ``_read`` / ``_write`` for every statement.

    ``label`` names the store in errors ("The clinic database is busy"). ``now`` returns
    the domain's current local time (naive), pinned or real.
    """

    def __init__(
        self, path: str, *, label: str, timeout_s: float, now: Callable[[], datetime]
    ) -> None:
        self._conn = sqlite3.connect(
            path, timeout=timeout_s, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.Lock()
        self._label = label
        self._timeout_s = timeout_s
        self._deadline = float("inf")
        self._conn.set_progress_handler(self._past_deadline, 1000)
        self._now = now
        self._initialize()

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

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """One write transaction. Always leaves the connection outside a transaction.

        COMMIT itself can fail (e.g. "database is locked" while another connection holds a
        read lock on a file database), so it sits inside the rollback path too.
        """
        with self._locked():
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
