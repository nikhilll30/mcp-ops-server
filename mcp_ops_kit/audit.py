"""Audit log (45 CFR 164.312(b)): one record per tool call, encrypted and hash-chained.

Each record says who called (client ID and token ID), which tool, which scope and subject
it was checked against, the outcome and when, and never the call's arguments or results.
The subject is recorded only if it is a well-formed ID of the domain's subject (e.g.
``PAT-0008``); anything else is dropped, so free text cannot reach the log.

Integrity: record *n* carries ``prev``, the chain value of record *n-1*, and its own chain
value is ``HMAC-SHA256(audit-mac key, record n)``. Editing, deleting, inserting or reordering
a record breaks the chain from that point, and without the key it cannot be recomputed.
Confidentiality: each record is stored as AES-256-GCM ciphertext bound to its sequence
number. One line per record::

    {"seq": 7, "ct": "<base64url of nonce || ciphertext || tag>"}

What it cannot show by itself: records cut off the *end* of the file. ``verify`` prints the
last sequence number and chain value; record them elsewhere (a ticket, a second log) to
detect truncation later. One process writes a log at a time (an exclusive lock).

    python -m mcp_ops_kit.audit verify --file audit.log --key-env MCP_OPS_DATA_KEY
    python -m mcp_ops_kit.audit show --file audit.log --key-env MCP_OPS_DATA_KEY
"""

import argparse
import base64
import fcntl
import hashlib
import hmac
import json
import logging
import os
import sys
import threading
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver.exceptions import ToolError

from mcp_ops_kit.crypto import DataKey, DecryptionError, seal, unseal
from mcp_ops_kit.errors import ConfigError

logger = logging.getLogger(__name__)

GENESIS = "0" * 64
OUTCOMES = ("ok", "error", "denied", "rate_limited", "invalid")
# Every key a record has, in the order ``show`` prints them. Nothing else is ever written.
RECORD_KEYS = (
    "seq",
    "time",
    "client_id",
    "token_id",
    "tool",
    "scope",
    "subject",
    "outcome",
    "error",
    "prev",
)


class AuditTamperedError(Exception):
    """The log failed verification at ``line`` (1-based)."""

    def __init__(self, line: int, reason: str) -> None:
        super().__init__(f"line {line}: {reason}")
        self.line = line


@dataclass
class Call:
    """What one tool call has told the audit log so far. The guard fills ``scope``,
    ``subject`` and ``checked``; the server fills ``outcome`` and ``error``."""

    tool: str
    client_id: str | None
    token_id: str | None
    scope: str | None = None
    subject: str | None = None
    checked: bool = False
    outcome: str | None = None
    error: str | None = None


_CURRENT: ContextVar[Call | None] = ContextVar("mcp_ops_kit_audit_call", default=None)


def mark(**updates: object) -> None:
    """Note facts about the call being audited (a no-op when nothing is audited). The
    first ``outcome`` set wins: a refusal stays a refusal whatever is raised after it."""
    call = _CURRENT.get()
    if call is None:
        return
    for name, value in updates.items():
        if name == "outcome" and call.outcome is not None:
            continue
        setattr(call, name, value)


def _canonical(record: dict[str, Any]) -> bytes:
    return json.dumps(record, sort_keys=True, separators=(",", ":")).encode()


def _aad(seq: int) -> bytes:
    return f"mcp-ops-kit/audit/v1/{seq}".encode()


class _Keys:
    def __init__(self, key: DataKey) -> None:
        self.enc = key.subkey("audit-enc")
        self.mac = key.subkey("audit-mac")

    def chain(self, record: dict[str, Any]) -> str:
        return hmac.new(self.mac, _canonical(record), hashlib.sha256).hexdigest()


def _decode_line(keys: _Keys, line: bytes, number: int) -> dict[str, Any]:
    try:
        outer = json.loads(line)
        seq, ct = outer["seq"], outer["ct"]
        if set(outer) != {"seq", "ct"} or not isinstance(seq, int) or not isinstance(ct, str):
            raise ValueError("unexpected fields")
        blob = base64.urlsafe_b64decode(ct + "=" * (-len(ct) % 4))
        record = json.loads(unseal(keys.enc, blob, _aad(seq)))
    except DecryptionError as exc:
        raise AuditTamperedError(number, f"does not decrypt ({exc})") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise AuditTamperedError(number, f"not an audit record ({exc})") from exc
    if not isinstance(record, dict) or tuple(sorted(record)) != tuple(sorted(RECORD_KEYS)):
        raise AuditTamperedError(number, "record has unexpected fields")
    if record["seq"] != seq:
        raise AuditTamperedError(number, "sequence number inside and outside differ")
    return record


def records(path: str, key: DataKey) -> Iterator[dict[str, Any]]:
    """Yield every record, checking the whole chain; raises ``AuditTamperedError`` at the
    first line that breaks it. A missing file is an empty log."""
    keys = _Keys(key)
    prev, expected = GENESIS, 1
    try:
        f = open(path, "rb")  # noqa: SIM115 - closed below; a generator holds it open
    except FileNotFoundError:
        return
    with f:
        for number, line in enumerate(f, start=1):
            if not line.endswith(b"\n"):
                raise AuditTamperedError(number, "incomplete last line")
            record = _decode_line(keys, line, number)
            if record["seq"] != expected:
                raise AuditTamperedError(
                    number, f"sequence {record['seq']} where {expected} was expected"
                )
            if not hmac.compare_digest(record["prev"], prev):
                raise AuditTamperedError(number, "chain broken: prev does not match")
            prev, expected = keys.chain(record), expected + 1
            yield record


def verify(path: str, key: DataKey) -> tuple[int, str]:
    """Check the whole log. Returns (records, chain value of the last one)."""
    keys, count, head = _Keys(key), 0, GENESIS
    for record in records(path, key):
        count, head = record["seq"], keys.chain(record)
    return count, head


class AuditLog:
    """The append side. Opened once per server process; refuses to start on a log whose
    last record does not decrypt (wrong key, or tampered), or that another process holds.

    ``begin`` / ``end`` bracket one tool call (``server.OpsServer`` calls them). If a record
    cannot be written (disk full, file gone), the call that was just made still returns its
    real result, since it has already happened, but the log is marked failed and every
    later call is refused: the server does not act without an audit trail.
    """

    def __init__(
        self,
        path: str,
        key: DataKey,
        *,
        fsync: bool = True,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.path = path
        self._keys = _Keys(key)
        self._fsync = fsync
        self._clock = clock
        self._lock = threading.Lock()
        self.failed: str | None = None
        try:
            self._fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
        except OSError as exc:
            raise ConfigError(f"Cannot open audit log {path}: {exc.strerror}") from exc
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._fd)
            raise ConfigError(f"Audit log {path} is in use by another process") from exc
        try:
            self._seq, self._prev = self._tail()
        except BaseException:
            self.close()
            raise

    def _tail(self) -> tuple[int, str]:
        with open(self._fd, "rb", closefd=False) as f:
            lines = f.read().splitlines(keepends=True)
        if not lines:
            return 0, GENESIS
        if not lines[-1].endswith(b"\n"):
            raise ConfigError(
                f"Audit log {self.path}: the last line is incomplete (a crash during a write?). "
                "Run verify on it before doing anything else."
            )
        try:
            last = _decode_line(self._keys, lines[-1], len(lines))
        except AuditTamperedError as exc:
            raise ConfigError(
                f"Audit log {self.path} {exc}: the wrong data key, or the log was changed. "
                "Refusing to append to it."
            ) from exc
        if last["seq"] != len(lines):
            raise ConfigError(
                f"Audit log {self.path}: {len(lines)} lines but last seq {last['seq']}"
            )
        return last["seq"], self._keys.chain(last)

    def append(self, **values: Any) -> int:
        """Write one record; returns its sequence number."""
        with self._lock:
            seq = self._seq + 1
            record = {k: values.get(k) for k in RECORD_KEYS}
            stamp = self._clock().astimezone(UTC).isoformat(timespec="milliseconds")
            record |= {"seq": seq, "time": stamp.replace("+00:00", "Z"), "prev": self._prev}
            blob = seal(self._keys.enc, _canonical(record), _aad(seq))
            ct = base64.urlsafe_b64encode(blob).decode().rstrip("=")
            line = json.dumps({"seq": seq, "ct": ct}, separators=(",", ":")).encode() + b"\n"
            written = os.write(self._fd, line)
            if written != len(line):
                raise OSError(f"short write to audit log ({written} of {len(line)} bytes)")
            if self._fsync:
                os.fsync(self._fd)
            self._seq, self._prev = seq, self._keys.chain(record)
            return seq

    # ---- one tool call ---------------------------------------------------------------------

    def begin(self, tool: str) -> tuple[Call, object]:
        """Start auditing a call. Refuses it outright if an earlier record failed to write."""
        if self.failed is not None:
            raise ToolError("The audit log is unavailable, so this server is refusing calls.")
        token = get_access_token()
        token_id = hashlib.sha256(token.token.encode()).hexdigest()[:12] if token else None
        call = Call(tool, token.client_id if token else None, token_id)
        return call, _CURRENT.set(call)

    def end(self, call: Call, reset: object) -> None:
        _CURRENT.reset(reset)  # type: ignore[arg-type] - the token begin() returned
        values = {f.name: getattr(call, f.name) for f in fields(Call) if f.name != "checked"}
        try:
            self.append(**values)
        except OSError as exc:
            self.failed = str(exc)
            logger.critical("Audit log %s write failed; refusing further calls: %s", self.path, exc)

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)  # releases the lock
            self._fd = -1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m mcp_ops_kit.audit")
    parser.add_argument("command", choices=["verify", "show"])
    parser.add_argument("--file", required=True)
    parser.add_argument(
        "--key-env", required=True, help="the variable holding the data key, e.g. MCP_OPS_DATA_KEY"
    )
    args = parser.parse_args(argv)
    raw = os.environ.get(args.key_env)
    if not raw:
        sys.exit(f"{args.key_env} is not set")
    key = DataKey.from_text(raw, args.key_env)
    try:
        if args.command == "verify":
            count, head = verify(args.file, key)
            print(f"ok: {count} records, chain intact; last seq {count}, head {head}")  # noqa: T201
        else:
            for record in records(args.file, key):
                print(json.dumps({k: record[k] for k in RECORD_KEYS}))  # noqa: T201
    except AuditTamperedError as exc:
        sys.exit(f"TAMPERED: {args.file} {exc}")


if __name__ == "__main__":
    main()
