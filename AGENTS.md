# AGENTS.md — instructions for non-Claude coding agents

> Current workflow: Claude implements (see `CLAUDE.md`); Codex reviews. The task/reply protocol below
> applies only if a part is ever delegated to another agent again.

You are the implementer on this repo. Claude (the planner) writes task specs; you write the code.
Read `comms/PLAN.md` once for context, then work only on the task you are given.

## Message board protocol
- Tasks live in `comms/tasks/NNN-name.md`. Each has: goal, files to touch, spec, acceptance tests, out of scope.
- When you finish, write `comms/replies/NNN-name.md` with exactly these sections:
  `## Done`, `## Decisions` (anything you chose that the spec didn't fix, and why),
  `## Test output` (paste the final `pytest -q` summary line and `ruff check .` result),
  `## Questions / blockers` (write "None" if none).
- Push your commits to the **same branch as the PR you were mentioned on**. Do not open a new PR.
- Do not edit `comms/PLAN.md`, `comms/STATUS.md`, `comms/tasks/`, or `AGENTS.md`. Ask in your reply instead.
- If the spec is ambiguous, pick the simplest option that passes the acceptance tests and note it under Decisions.

## Hard rules
- **Synthetic data only.** Never add real names, real patient data, real credentials, or real API keys.
- No secrets in code. Config comes from environment variables; document new ones in `.env.example`.
- MCP SDK is **v2**: `from mcp.server.mcpserver import MCPServer`. `FastMCP` does not exist in v2. Do not pin `mcp<2`.
- Python 3.12+, type hints on all public functions, keep modules small.
- Every task must leave `ruff check .` and `pytest -q` passing.
- Do not add dependencies beyond those the task names. If you need one, ask under Questions.

## Commands
```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest -q
```

## Review guidelines (for `@codex review`)
- Treat any secret, real-looking personal data, or missing scope check as P0.
- Treat untested tool behavior as P1.
