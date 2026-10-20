"""Data backends behind one protocol (``base.Backend``): SQLite (default) and FHIR R4."""

from collections.abc import Callable
from datetime import datetime

from mcp_ops_kit.server import make_clock
from mcp_ops_server import seed
from mcp_ops_server.backends.base import Backend
from mcp_ops_server.config import Settings


def clock(settings: Settings) -> Callable[[], datetime]:
    """The clinic clock both backends get: pinned (``settings.now``) or the real one, as
    clinic-local wall time (``seed.CLINIC_TZ``), never the host's time zone."""
    return make_clock(settings.now, seed.CLINIC_TZ)


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
