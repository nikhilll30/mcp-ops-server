"""Runs the real salon server for evals: one fresh server and freshly seeded data per task.

Copied from the clinic's ``evals/harness.py`` (SQLite only here): each task gets a copy of a
seeded database file and its own server, built by the same ``build_http_app`` that
``python -m salon_ops`` uses, in a background thread of this process. The salon clock is
pinned per task (``task["now"]``, default ``seed.SALON_NOW``).
"""

import logging
import shutil
import socket
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

from salon_ops import seed
from salon_ops.auth import ALL_SCOPES, issue
from salon_ops.config import Settings
from salon_ops.evals.grader import Step
from salon_ops.server import build_http_app, make_store
from salon_ops.store import SalonStore, VisitRow, visit_id

# principal name -> (scopes, bound customer). "customer:CUS-XXXX" is created on demand.
PRINCIPALS: dict[str, tuple[list[str], str | None]] = {
    "staff": (list(ALL_SCOPES), None),
    "reader": (["read:catalog"], None),
}

for _name in ("mcp", "httpx2", "httpx", "uvicorn", "uvicorn.error"):
    logging.getLogger(_name).setLevel(logging.WARNING)


def _principal_spec(name: str) -> tuple[list[str], str | None]:
    if name.startswith("customer:"):
        return list(ALL_SCOPES), name.split(":", 1)[1]
    return PRINCIPALS[name]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Harness:
    """Per-suite resources (tokens, seeded data). Use as a context manager."""

    def __init__(self, principals: Iterable[str]) -> None:
        self._dir = tempfile.TemporaryDirectory(prefix="mcp-ops-evals-")
        root = Path(self._dir.name)
        self._tokens_file = str(root / "tokens.json")
        self.tokens: dict[str, str] = {}
        for name in sorted(set(principals) | {"staff"}):
            scopes, customer = _principal_spec(name)
            self.tokens[name] = issue(self._tokens_file, f"evals-{name}", scopes, customer)
        self._template = root / "template.db"
        SalonStore(str(self._template)).close()  # seed once; each task copies this file
        self._count = 0

    def __enter__(self) -> "Harness":
        return self

    def __exit__(self, *exc: object) -> None:
        self._dir.cleanup()

    @contextmanager
    def task_server(self, now: datetime = seed.SALON_NOW) -> Iterator["TaskServer"]:
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
        backend = make_store(settings)
        server = uvicorn.Server(
            uvicorn.Config(
                build_http_app(settings, backend), host="127.0.0.1", port=port, log_level="warning"
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
            yield TaskServer(f"http://127.0.0.1:{port}/mcp", backend, self.tokens)
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            backend.close()


class TaskServer:
    def __init__(self, url: str, backend: SalonStore, tokens: dict[str, str]) -> None:
        self.url = url
        self.backend = backend
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

    def snapshot(self) -> dict[int, VisitRow]:
        """Every visit, read through the store the server uses (not via MCP)."""
        return self.backend.snapshot_visits()


def _public(n: int, row: VisitRow) -> dict[str, Any]:
    out: dict[str, Any] = dict(row)
    out["visit_id"] = visit_id(n)
    out["date"] = row["start"][:10]
    return out


def diff(
    before: dict[int, VisitRow], after: dict[int, VisitRow]
) -> dict[str, list[dict[str, Any]]]:
    """What changed: new booked visits, existing ones that became cancelled, and
    ``transient`` ones booked *and* cancelled within the task (net zero, but still writes
    the grader must see)."""
    new = [(i, r) for i, r in after.items() if i not in before]
    return {
        "booked": [_public(i, r) for i, r in new if r["status"] == "booked"],
        "cancelled": [
            _public(i, r)
            for i, r in after.items()
            if i in before and before[i]["status"] == "booked" and r["status"] == "cancelled"
        ],
        "transient": [_public(i, r) for i, r in new if r["status"] == "cancelled"],
    }
