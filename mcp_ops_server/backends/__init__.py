"""Data backends behind one protocol (``base.Backend``): SQLite (default) and FHIR R4."""

from collections.abc import Callable
from datetime import datetime

from mcp_ops_server import seed
from mcp_ops_server.backends.base import Backend
from mcp_ops_server.config import Settings


def system_clinic_now() -> datetime:
    """The real clock as clinic-local wall time (naive, like every time the tools use).

    Not ``datetime.now()``: that is the host's local time, which is UTC in a container, five
    hours off the clinic (Codex review, PR #16).
    """
    return datetime.now(seed.CLINIC_TZ).replace(tzinfo=None)


def clock(settings: Settings) -> Callable[[], datetime]:
    """The clinic clock both backends get: pinned (``settings.now``) or the real one."""
    fixed_now = settings.now
    return (lambda: fixed_now) if fixed_now is not None else system_clinic_now


def make_backend(settings: Settings) -> Backend:
    """The backend ``settings`` selects. A FHIR backend is checked before it is returned, so
    the server fails closed (``fhir.BackendUnavailableError``) on an unusable FHIR URL."""
    now = clock(settings)
    if settings.backend == "fhir":
        from mcp_ops_server.backends.fhir import BackendUnavailableError, FhirBackend

        if not settings.fhir_url:
            raise BackendUnavailableError("MCP_OPS_BACKEND=fhir needs MCP_OPS_FHIR_URL.")
        backend = FhirBackend(settings.fhir_url, timeout_s=settings.call_timeout_s, now=now)
        try:
            backend.check()
        except BaseException:
            backend.close()
            raise
        return backend
    from mcp_ops_server.backends.sqlite import SqliteBackend

    return SqliteBackend(settings.db_path, timeout_s=settings.call_timeout_s, now=now)
