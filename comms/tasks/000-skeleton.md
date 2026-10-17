# Task 000 — Skeleton server + CI (also tests the Claude↔Codex channel)

**Part:** 1 · **Branch:** `task/000-skeleton` (push here) · **Reply:** `comms/replies/000-skeleton.md`

## Goal
The smallest real slice: a package that starts an MCP server with one `health` tool,
a test that calls it, and CI that lints, tests, and scans for secrets.

## Files to create
- (`mcp_ops_server/__init__.py` already exists with `__version__`; import it, do not redefine it)
- `mcp_ops_server/server.py`
- `mcp_ops_server/__main__.py` — `python -m mcp_ops_server` runs the server
- `tests/test_health.py`
- `.github/workflows/ci.yml`

## Spec
1. `server.py` exposes `build_server() -> MCPServer` (from `mcp.server.mcpserver`), named `"mcp-ops-server"`.
   It registers one tool:
   - `health() -> dict` returning `{"status": "ok", "version": __version__}`.
   - Docstring is the tool description: "Liveness check. Returns server status and version."
2. `__main__.py` reads `MCP_OPS_HOST` (default `127.0.0.1`) and `MCP_OPS_PORT` (default `8000`)
   and runs the server over **Streamable HTTP**. Use the SDK's own run method; no FastAPI yet.
3. `ci.yml`, on `push` and `pull_request`, Ubuntu, Python 3.12, three jobs or steps:
   - `pip install -e ".[dev]"` then `ruff check .` and `ruff format --check .`
   - `pytest -q`
   - secret scan with `gitleaks/gitleaks-action@v2` (needs `fetch-depth: 0` on checkout,
     and `GITHUB_TOKEN` env).

## Acceptance tests (`tests/test_health.py`)
- `build_server().list_tools()` returns exactly one tool named `health`.
- Calling `health` via `call_tool("health", {})` returns `is_error == False` and structured
  content where `status == "ok"` and `version == "0.1.0"`.
- (Async calls: wrap with `asyncio.run`; do not add pytest-asyncio.)

## Out of scope
Auth, data, other tools, Docker, FastAPI. Do not add dependencies.

## Done when
`ruff check .`, `ruff format --check .`, and `pytest -q` pass locally, and the reply file is written.
