"""Runs a domain's real server for evals: one fresh server and freshly seeded data per task.

Each task gets fresh data (the domain decides how: copy a seeded SQLite file, reset a FHIR
server) and its own server, built by the same app factory the domain's ``python -m`` entry
point uses (real HTTP, real bearer auth, real limits). So a misbehaving agent cannot leave
state behind for the next task, and every task's expectations can be computed from the
seed. The server runs in a background thread of this process: starting one is ~100 ms,
where a subprocess per task costs seconds.

The domain clock is pinned per task (``task["now"]``, default ``EvalDomain.default_now``)
and is never read from the environment, so the caller's shell cannot change results.
"""

import logging
import socket
import tempfile
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx2
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette

from mcp_ops_kit.auth import AuthConfig, issue
from mcp_ops_kit.evals.grader import Step, SubjectRule

# The server runs in this process; keep its per-request INFO logs out of eval output.
for _name in ("mcp", "httpx2", "httpx", "uvicorn", "uvicorn.error"):
    logging.getLogger(_name).setLevel(logging.WARNING)

# A snapshot: every record the tasks can change (appointments, visits), booked or cancelled,
# keyed by its number. Each row has at least a ``status`` of "booked" or "cancelled".
Snapshot = dict[int, Mapping[str, Any]]


@dataclass(frozen=True)
class TaskSpec:
    """What a domain needs to start one task's server."""

    workdir: Path  # this suite's scratch folder (seeded templates live here)
    number: int  # 1, 2, ... per task, for per-task file names
    tokens_file: str
    port: int
    now: datetime


@dataclass(frozen=True)
class TaskData:
    """One task's server app over fresh data, and how to read and release that data."""

    app: Starlette
    snapshot: Callable[[], Snapshot]  # read through the store the server uses, not MCP
    close: Callable[[], None]


@dataclass(frozen=True)
class EvalDomain:
    """Everything the harness, grader and runner need to know about a domain.

    ``principals`` maps a principal name to its scopes (``staff`` must exist: setup steps run
    as staff). ``bound_prefix`` names principals created on demand, bound to one subject
    with every scope: ``portal:PAT-0001``. ``prepare`` runs once per suite (seed a template,
    load a FHIR server); ``start_task`` gives each task fresh data and its app.
    ``record_key``/``record_id`` name a changed record in diffs (``appointment_id``,
    ``APT-00001``). ``system_prompt`` is a template with ``{now}`` for live agents.
    """

    name: str
    serve_command: str
    golden_dir: Path
    auth: AuthConfig
    principals: Mapping[str, tuple[str, ...]]
    bound_prefix: str
    subject: SubjectRule
    default_now: datetime
    record_key: str
    record_id: Callable[[int], str]
    prepare: Callable[[Path], None]
    start_task: Callable[[TaskSpec], TaskData]
    backend: str = "sqlite"
    system_prompt: str = ""

    def principal_spec(self, name: str) -> tuple[list[str], str | None]:
        if name.startswith(self.bound_prefix):
            return list(self.auth.scopes), name.removeprefix(self.bound_prefix)
        return list(self.principals[name]), None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Harness:
    """Per-suite resources (tokens, seeded data). Use as a context manager."""

    def __init__(self, domain: EvalDomain, principals: Iterable[str]) -> None:
        self.domain = domain
        self._dir = tempfile.TemporaryDirectory(prefix=f"{domain.name}-evals-")
        self._root = Path(self._dir.name)
        self._tokens_file = str(self._root / "tokens.json")
        self.tokens: dict[str, str] = {}
        for name in sorted(set(principals) | {"staff"}):
            scopes, subject = domain.principal_spec(name)
            self.tokens[name] = issue(
                domain.auth, self._tokens_file, f"evals-{name}", scopes, subject
            )
        domain.prepare(self._root)
        self._count = 0

    def __enter__(self) -> "Harness":
        return self

    def __exit__(self, *exc: object) -> None:
        self._dir.cleanup()

    @contextmanager
    def task_server(self, now: datetime | None = None) -> Iterator["TaskServer"]:
        self._count += 1
        port = _free_port()
        spec = TaskSpec(
            self._root, self._count, self._tokens_file, port, now or self.domain.default_now
        )
        data = self.domain.start_task(spec)
        server = uvicorn.Server(
            uvicorn.Config(data.app, host="127.0.0.1", port=port, log_level="warning")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        try:
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("eval server failed to start")
                time.sleep(0.01)
            yield TaskServer(f"http://127.0.0.1:{port}/mcp", data, self.tokens)
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            data.close()


class TaskServer:
    def __init__(self, url: str, data: TaskData, tokens: dict[str, str]) -> None:
        self.url = url
        self._data = data
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

    def snapshot(self) -> Snapshot:
        """Every record, read through the store the server uses (not via MCP)."""
        return self._data.snapshot()


def diff(
    before: Snapshot,
    after: Snapshot,
    *,
    record_key: str,
    record_id: Callable[[int], str],
) -> dict[str, list[dict[str, Any]]]:
    """What changed, in the booking lifecycle the kit grades: new ``booked`` records, existing
    booked ones that became ``cancelled`` (nothing else about them changed), and
    ``transient`` ones booked *and* cancelled within the task (net zero, but still writes the
    grader must see). Every other change is ``unexpected``: a record removed, a new record
    with another status, or an existing record changed in any other way (Codex review, PR
    #18: such changes used to vanish from the diff, so a "no change" grade could pass).

    Each row gets its public ID under ``record_key``, and a ``date`` when it has a ``start``.
    """

    def public(n: int, row: Mapping[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = dict(row)
        out[record_key] = record_id(n)
        if isinstance(row.get("start"), str):
            out["date"] = row["start"][:10]
        return out

    def only_cancelled(old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
        others = (set(old) | set(new)) - {"status"}
        return (
            old.get("status") == "booked"
            and new.get("status") == "cancelled"
            and all(old.get(k) == new.get(k) for k in others)
        )

    out: dict[str, list[dict[str, Any]]] = {
        "booked": [],
        "cancelled": [],
        "transient": [],
        "unexpected": [],
    }
    for i, row in after.items():
        if i not in before:
            kind = {"booked": "booked", "cancelled": "transient"}.get(row.get("status"))
            if kind:
                out[kind].append(public(i, row))
            else:
                out["unexpected"].append({"change": "added", **public(i, row)})
        elif row != before[i]:
            if only_cancelled(before[i], row):
                out["cancelled"].append(public(i, row))
            else:
                change = {"change": "changed", record_key: record_id(i)}
                out["unexpected"].append({**change, "before": dict(before[i]), "after": dict(row)})
    for i, row in before.items():
        if i not in after:
            out["unexpected"].append({"change": "removed", **public(i, row)})
    return out
