"""Shared fixtures: a fresh seeded backend and server per test, plus a call helper.

Backends under test come from ``MCP_OPS_TEST_BACKENDS`` (comma-separated, default
``sqlite``). With ``fhir`` in it, every test using ``backend`` or ``server`` also runs against
the FHIR R4 server at ``MCP_OPS_TEST_FHIR_URL``, reset to the seed before each test, and tests
marked ``fhir`` run. That server is overwritten: never point it at real data. An unreachable
FHIR server fails the run; it is never skipped. CI's ``fhir`` job runs both backends.
"""

import asyncio
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver import MCPServer

from mcp_ops_server import seed
from mcp_ops_server.backends import fhir_seed
from mcp_ops_server.backends.base import Backend
from mcp_ops_server.backends.fhir import FhirBackend
from mcp_ops_server.backends.sqlite import SqliteBackend
from mcp_ops_server.server import build_server

TEST_BACKENDS = [b.strip() for b in os.environ.get("MCP_OPS_TEST_BACKENDS", "sqlite").split(",")]
if not set(TEST_BACKENDS) <= {"sqlite", "fhir"} or not TEST_BACKENDS:
    raise ValueError(f"MCP_OPS_TEST_BACKENDS must list sqlite and/or fhir, not {TEST_BACKENDS}")
FHIR_ENABLED = "fhir" in TEST_BACKENDS
FHIR_URL = os.environ.get("MCP_OPS_TEST_FHIR_URL", "http://127.0.0.1:8080/fhir")

# Facts from the deterministic seed that tests rely on (pinned in test_seed.py).
DERM_PATIENT = "PAT-0001"  # approved dermatology referral REF-0001
DERM_REFERRAL = "REF-0001"
DERM_PROVIDER = "PRV-004"
PRIMARY_PROVIDER = "PRV-001"  # primary care: no referral needed
CARDIO_PROVIDER = "PRV-003"
PENDING_CARDIO = ("PAT-0002", "REF-0003")
DENIED_NEURO = ("PAT-0007", "REF-0005")
OTHER_PATIENTS_REFERRAL = "REF-0002"  # PAT-0002, orthopedics, approved
NO_REFERRAL_PATIENT = "PAT-0008"  # no referrals and no seeded bookings
SECOND_FREE_PATIENT = "PAT-0021"  # same
WEEK_ONE = "2026-11-02"

_SEED = seed.generate()
_SLOTS = {s.id: s for s in _SEED.slots}


ALL_SCOPES = ["read:schedule", "write:schedule", "read:referrals"]


@contextmanager
def as_principal(
    client_id: str = "test-staff",
    scopes: list[str] | None = None,
    patient_id: str | None = None,
) -> Iterator[None]:
    """Act as an authenticated caller, the way the HTTP auth middleware would set it."""
    token = AccessToken(
        token="test-token",  # noqa: S106 - not a credential; never checked in-process
        client_id=client_id,
        scopes=ALL_SCOPES if scopes is None else scopes,
        claims={"patient_id": patient_id} if patient_id else None,
    )
    reset = auth_context_var.set(AuthenticatedUser(token))
    try:
        yield
    finally:
        auth_context_var.reset(reset)


@pytest.fixture(autouse=True)
def staff_principal() -> Iterator[None]:
    """Unit tests run as fully-scoped staff unless they opt into another principal."""
    with as_principal():
        yield


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "fhir: needs the FHIR server (MCP_OPS_TEST_BACKENDS=...,fhir)"
    )


def pytest_report_header() -> str:
    extra = f" at {FHIR_URL}" if FHIR_ENABLED else "; fhir-marked tests deselected"
    return f"backends: {', '.join(TEST_BACKENDS)}{extra}"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if FHIR_ENABLED:
        return
    keep = [i for i in items if i.get_closest_marker("fhir") is None]
    config.hook.pytest_deselected(items=[i for i in items if i not in keep])
    items[:] = keep


@pytest.fixture(scope="session")
def fhir_url() -> str:
    """The FHIR test server, with the seed loaded once per session. Fails if unreachable."""
    if not FHIR_ENABLED:
        pytest.fail("fhir_url used without fhir in MCP_OPS_TEST_BACKENDS")
    fhir_seed.load(FHIR_URL)
    probe = FhirBackend(FHIR_URL)
    probe.check()
    probe.close()
    return FHIR_URL


@pytest.fixture
def store() -> Iterator[SqliteBackend]:
    """The SQLite backend specifically (tests of its locking, timeouts and constraints)."""
    s = SqliteBackend()
    yield s
    s.close()


@pytest.fixture(params=TEST_BACKENDS)
def backend(request: pytest.FixtureRequest) -> Iterator[Backend]:
    """Each enabled backend in turn, freshly seeded."""
    if request.param == "fhir":
        url = request.getfixturevalue("fhir_url")
        fhir_seed.reset(url)
        b: Backend = FhirBackend(url)
    else:
        b = SqliteBackend()
    yield b
    b.close()


@pytest.fixture
def server(backend: Backend) -> MCPServer:
    return build_server(backend)


def call(server: MCPServer, tool: str, **args: Any) -> dict[str, Any]:
    """Call a tool through the SDK (schema validation included) and return structured content."""
    result = asyncio.run(server.call_tool(tool, args))
    assert result.is_error is False
    assert result.structured_content is not None
    return result.structured_content


def seeded_busy_starts(patient_id: str | None) -> set[str]:
    """Start times (ISO) of a patient's seeded bookings."""
    mine = [a for a in _SEED.appointments if a.patient_id == patient_id]
    return {_SLOTS[a.slot_id].start.isoformat() for a in mine}


def first_open_slot(
    server: MCPServer, provider_id: str, patient_id: str | None = None, start_date: str = WEEK_ONE
) -> str:
    """First open slot for ``provider_id``; if ``patient_id`` is given, one they are free for.

    Seeded patients already have some bookings, so "free" is checked against the seed.
    """
    busy = seeded_busy_starts(patient_id)
    out = call(server, "list_open_slots", provider_id=provider_id, start_date=start_date, limit=100)
    return next(s["slot_id"] for s in out["slots"] if s["start"] not in busy)
