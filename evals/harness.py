"""Runs the real server for evals: one fresh server and database per task.

Each task gets a copy of a freshly seeded database and its own server, built by the same
``build_http_app`` that ``python -m mcp_ops_server`` uses (real HTTP, real bearer auth,
real limits). So a misbehaving agent cannot leave state behind for the next task, and every
task's expectations can be computed from the seed. The server runs in a background thread
of this process: starting one is ~100 ms, where a subprocess per task costs seconds.

The clinic clock is pinned per task (``task["now"]``, default ``seed.CLINIC_NOW``) and is
never read from the environment, so ``MCP_OPS_NOW`` in the caller's shell cannot change
results.
"""

import logging
import shutil
import socket
import sqlite3
import tempfile
import threading
import time
from collections.abc import AsyncIterator, Iterable, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx2
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from evals.grader import Step
from mcp_ops_server import seed
from mcp_ops_server.auth import ALL_SCOPES, issue
from mcp_ops_server.backends.sqlite import SqliteBackend
from mcp_ops_server.config import Settings
from mcp_ops_server.server import build_http_app

# principal name -> (scopes, bound patient). "portal:PAT-XXXX" is created on demand.
PRINCIPALS: dict[str, tuple[list[str], str | None]] = {
    "staff": (list(ALL_SCOPES), None),
    "reader": (["read:schedule"], None),
}

# The server runs in this process; keep its per-request INFO logs out of eval output.
for _name in ("mcp", "httpx2", "httpx", "uvicorn", "uvicorn.error"):
    logging.getLogger(_name).setLevel(logging.WARNING)

_APPOINTMENTS_SQL = (
    "SELECT a.id, a.status, a.patient_id, a.referral_id, a.slot_id, s.start, "
    "p.id AS provider_id, p.specialty, p.location FROM appointments a "
    "JOIN slots s ON s.id = a.slot_id JOIN providers p ON p.id = s.provider_id"
)


def _principal_spec(name: str) -> tuple[list[str], str | None]:
    if name.startswith("portal:"):
        return list(ALL_SCOPES), name.split(":", 1)[1]
    return PRINCIPALS[name]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Harness:
    """Per-suite resources (tokens, seeded template database). Use as a context manager."""

    def __init__(self, principals: Iterable[str]) -> None:
        self._dir = tempfile.TemporaryDirectory(prefix="mcp-ops-evals-")
        root = Path(self._dir.name)
        self._tokens_file = str(root / "tokens.json")
        self.tokens: dict[str, str] = {}
        for name in sorted(set(principals) | {"staff"}):
            scopes, patient = _principal_spec(name)
            self.tokens[name] = issue(self._tokens_file, f"evals-{name}", scopes, patient)
        self._template = root / "template.db"
        SqliteBackend(str(self._template)).close()  # seed once; each task copies this file
        self._count = 0

    def __enter__(self) -> "Harness":
        return self

    def __exit__(self, *exc: object) -> None:
        self._dir.cleanup()

    @contextmanager
    def task_server(self, now: datetime = seed.CLINIC_NOW) -> Iterator["TaskServer"]:
        self._count += 1
        db_path = str(Path(self._dir.name) / f"task-{self._count}.db")
        shutil.copyfile(self._template, db_path)
        port = _free_port()
        settings = Settings(
            host="127.0.0.1",
            port=port,
            db_path=db_path,
            tokens_file=self._tokens_file,
            rate_limit_per_minute=100_000,  # evals measure agent behaviour, not the limiter
            now=now,
        )
        server = uvicorn.Server(
            uvicorn.Config(
                build_http_app(settings), host="127.0.0.1", port=port, log_level="warning"
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("eval server failed to start")
            time.sleep(0.01)
        try:
            yield TaskServer(f"http://127.0.0.1:{port}/mcp", db_path, self.tokens)
        finally:
            server.should_exit = True
            thread.join(timeout=10)


class TaskServer:
    def __init__(self, url: str, db_path: str, tokens: dict[str, str]) -> None:
        self.url = url
        self.db_path = db_path
        self._tokens = tokens

    @asynccontextmanager
    async def session(self, principal: str) -> AsyncIterator[ClientSession]:
        headers = {"Authorization": f"Bearer {self._tokens[principal]}"}
        timeout = httpx2.Timeout(30, read=300)
        async with httpx2.AsyncClient(headers=headers, timeout=timeout) as http:
            async with streamable_http_client(self.url, http_client=http) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    yield session

    @staticmethod
    async def call(session: ClientSession, tool: str, args: dict[str, Any]) -> Step:
        start = time.perf_counter()
        result = await session.call_tool(tool, args)
        latency_ms = (time.perf_counter() - start) * 1000
        if result.is_error:
            text = result.content[0].text if result.content else ""
            return Step(tool, args, ok=False, error=text, latency_ms=latency_ms)
        return Step(tool, args, ok=True, result=result.structured_content, latency_ms=latency_ms)

    def snapshot(self) -> dict[int, dict[str, Any]]:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            return {r["id"]: dict(r) for r in conn.execute(_APPOINTMENTS_SQL)}
        finally:
            conn.close()


def _public(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in row.items() if k != "id"}
    out["appointment_id"] = f"APT-{row['id']:05d}"
    out["date"] = row["start"][:10]
    return out


def diff(before: dict[int, dict], after: dict[int, dict]) -> dict[str, list[dict[str, Any]]]:
    """What changed: new booked appointments, existing ones that became cancelled, and
    ``transient`` ones booked *and* cancelled within the task (net zero, but still writes
    the grader must see)."""
    new = [r for i, r in after.items() if i not in before]
    return {
        "booked": [_public(r) for r in new if r["status"] == "booked"],
        "cancelled": [
            _public(r)
            for i, r in after.items()
            if i in before and before[i]["status"] == "booked" and r["status"] == "cancelled"
        ],
        "transient": [_public(r) for r in new if r["status"] == "cancelled"],
    }
