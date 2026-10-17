## Done

- Added `mcp_ops_server/server.py` with `build_server()` registering a single `health` tool on an `MCPServer` named `mcp-ops-server`.
- Added `mcp_ops_server/__main__.py` so `python -m mcp_ops_server` runs Streamable HTTP using `MCP_OPS_HOST` (default `127.0.0.1`) and `MCP_OPS_PORT` (default `8000`).
- Added `tests/test_health.py` covering tool listing and `health` invocation.
- Added `.github/workflows/ci.yml` with lint, test, and gitleaks jobs on Ubuntu Python 3.12.

## Decisions

- Used a `TypedDict` (`HealthResult`) as the `health` return annotation so the MCP SDK emits `structured_content` on `call_tool` results. A plain `dict` annotation leaves `structured_content` as `None` while still returning JSON text content.

## Test output

```
$ ruff check .
All checks passed!

$ ruff format --check .
10 files already formatted

$ pytest -q
..                                                                       [100%]
2 passed in 0.52s
```

## Questions / blockers

None
