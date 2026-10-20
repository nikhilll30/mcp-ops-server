"""Entry point for ``python -m mcp_ops_server``."""

from mcp_ops_kit.server import serve
from mcp_ops_server.auth import AUTH
from mcp_ops_server.backends.fhir import BackendUnavailableError
from mcp_ops_server.config import ENV_PREFIX, Settings
from mcp_ops_server.server import build_http_app


def main() -> None:
    # Fails closed: never serves patient-like data without a tokens file, and refuses to
    # start (with one line, not a traceback) if the FHIR backend is unusable.
    serve(
        Settings.from_env(),
        build_http_app,  # type: ignore[arg-type] - called with the clinic Settings above
        auth=AUTH,
        env_prefix=ENV_PREFIX,
        startup_errors=(BackendUnavailableError,),
    )


if __name__ == "__main__":
    main()
