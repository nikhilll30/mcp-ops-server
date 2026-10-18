"""Runs the real server for evals: subprocess, file database, issued tokens, MCP over HTTP.

The database is a file so the harness can diff the appointments table around each task,
which is how ``final_state`` is graded: by what actually changed, not by what the agent said.
"""

import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Iterable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from evals.grader import Step
from mcp_ops_server.auth import ALL_SCOPES, issue

# principal name -> (scopes, bound patient). "portal:PAT-XXXX" is created on demand.
PRINCIPALS: dict[str, tuple[list[str], str | None]] = {
    "staff": (list(ALL_SCOPES), None),
    "reader": (["read:schedule"], None),
}


def _principal_spec(name: str) -> tuple[list[str], str | None]:
    if name.startswith("portal:"):
        return list(ALL_SCOPES), name.split(":", 1)[1]
    return PRINCIPALS[name]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_APPOINTMENTS_SQL = (
    "SELECT a.id, a.status, a.patient_id, a.referral_id, a.slot_id, s.start, "
    "p.id AS provider_id, p.specialty, p.location FROM appointments a "
    "JOIN slots s ON s.id = a.slot_id JOIN providers p ON p.id = s.provider_id"
)


class Harness:
    """Start once, run many tasks. Use as a context manager."""

    def __init__(self, principals: Iterable[str]) -> None:
        self._dir = tempfile.TemporaryDirectory(prefix="mcp-ops-evals-")
        self.db_path = str(Path(self._dir.name) / "clinic.db")
        tokens_file = str(Path(self._dir.name) / "tokens.json")
        self.tokens = {}
        for name in sorted(set(principals) | {"staff"}):
            scopes, patient = _principal_spec(name)
            self.tokens[name] = issue(tokens_file, f"evals-{name}", scopes, patient)
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}/mcp"
        self._env = {
            **os.environ,
            "MCP_OPS_HOST": "127.0.0.1",
            "MCP_OPS_PORT": str(self.port),
            "MCP_OPS_DB": self.db_path,
            "MCP_OPS_TOKENS_FILE": tokens_file,
            # Evals measure agent behaviour, not the limiter.
            "MCP_OPS_RATE_LIMIT_PER_MIN": "100000",
        }
        self._proc: subprocess.Popen | None = None

    def __enter__(self) -> "Harness":
        self._proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-m", "mcp_ops_server"],
            env=self._env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                err = self._proc.stderr.read().decode() if self._proc.stderr else ""
                raise RuntimeError(f"server exited early:\n{err[-2000:]}")
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", self.port)) == 0:
                    return self
            time.sleep(0.1)
        raise TimeoutError("server did not start")

    def __exit__(self, *exc: object) -> None:
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._dir.cleanup()

    @asynccontextmanager
    async def session(self, principal: str) -> AsyncIterator[ClientSession]:
        headers = {"Authorization": f"Bearer {self.tokens[principal]}"}
        timeout = httpx2.Timeout(30, read=300)
        async with httpx2.AsyncClient(headers=headers, timeout=timeout) as http:
            async with streamable_http_client(self.url, http_client=http) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    yield session

    @asynccontextmanager
    async def sessions(self) -> AsyncIterator["Sessions"]:
        """One open MCP session per principal for a whole suite (opened on first use)."""
        async with AsyncExitStack() as stack:
            yield Sessions(self, stack)

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


class Sessions:
    def __init__(self, harness: Harness, stack: AsyncExitStack) -> None:
        self._harness = harness
        self._stack = stack
        self._open: dict[str, ClientSession] = {}

    async def get(self, principal: str) -> ClientSession:
        if principal not in self._open:
            self._open[principal] = await self._stack.enter_async_context(
                self._harness.session(principal)
            )
        return self._open[principal]


def _public(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in row.items() if k != "id"}
    out["appointment_id"] = f"APT-{row['id']:05d}"
    out["date"] = row["start"][:10]
    return out


def diff(before: dict[int, dict], after: dict[int, dict]) -> dict[str, list[dict[str, Any]]]:
    """Net change: new booked appointments, and existing ones that became cancelled.

    An appointment booked and cancelled within the same task is no net change.
    """
    booked = [_public(r) for i, r in after.items() if i not in before and r["status"] == "booked"]
    cancelled = [
        _public(r)
        for i, r in after.items()
        if i in before and before[i]["status"] == "booked" and r["status"] == "cancelled"
    ]
    return {"booked": booked, "cancelled": cancelled}
