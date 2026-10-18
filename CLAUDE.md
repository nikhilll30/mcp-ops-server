# CLAUDE.md

Portfolio repo for Nikhil (owner): a production-shaped MCP server for a **synthetic**
clinic-operations domain, with trajectory-level agent evals and a CI eval gate.

## Start of every session
1. Read `comms/STATUS.md` (where we are, what's next). Then `comms/PLAN.md` if you need architecture.
2. Skim the newest entries in `FAILURES.md` so you don't repeat a known mistake.
3. Do only the part STATUS names. Do not start the following part without the owner's "go".

## Roles
- **Claude (you):** plan and implement each part, keep CI green, write the brief.
- **Codex:** independent reviewer at the end of each part. The owner pastes your prompt into Codex and pastes findings back.
- **Owner:** reads the brief, merges the PR, says "go". You never merge PRs yourself.

## Workflow for one part
1. Branch `part-N-<slug>` from `main`. Small commits.
2. Build it. Every tool gets unit tests; anything with a network path gets an integration test.
3. `ruff check . && ruff format --check . && pytest -q` must pass locally.
4. Push, open a PR into `main` (REST: `gh api repos/nikhilll30/mcp-ops-server/pulls ...`; GraphQL / `gh pr create` is blocked in Claude Code sessions). Wait until all CI jobs pass on both the push and pull_request runs.
5. Log anything that broke or surprised you in `FAILURES.md` (what broke / what changed / what happened next, newest first). Never write an untested claim there.
6. Write the brief: add a `partN()` function to `docs/briefs/build_brief.py`, build `docs/briefs/part-N-<slug>.pdf`, check it is **one page** and renders cleanly, commit it to the PR. Sections: what was built, how it works, why this way, what went wrong, verify it yourself, interview talking point, next.
7. Give the owner a Codex review prompt (template below). When findings come back: fix what's right, push back with reasons on what isn't, update FAILURES.md and the brief.
8. Update `comms/STATUS.md` (part state, PR number, next action) in the same PR.

### Codex review prompt template
```
Review PR #<n> in nikhilll30/mcp-ops-server (branch <branch>) as a senior engineer.
Focus: <part-specific risks>. Also check test quality and anything that would embarrass
this repo once public. List findings as P0/P1/P2 with file and line. Do not change code.
```

## Hard rules
- **Synthetic data only.** No real names, patients, credentials, or keys. Ever.
- No secrets in code; config via env vars, documented in `.env.example`.
- No new dependencies without a reason in the PR description.
- Security checks (auth, scopes) are enforced in server code, never in prompts.

## MCP SDK v2 gotchas (mcp>=2.3,<3)
- Server: `from mcp.server.mcpserver import MCPServer`. `FastMCP` does not exist in v2.
- Client: `from mcp.client.streamable_http import streamable_http_client` (not `streamablehttp_client`).
- Tools must return a `TypedDict` / Pydantic model to get an output schema and `structured_content`. A plain `dict` return gives neither.
- Run: `build_server().run(transport="streamable-http", host=..., port=...)`, served at `/mcp`.

## Commands
```bash
pip install -e ".[dev]"          # add ,docs for the brief generator (reportlab)
ruff check . && ruff format --check . && pytest -q
python -m mcp_ops_server         # http://127.0.0.1:8000/mcp
python docs/briefs/build_brief.py
```

## CI notes
- Jobs: lint, test, secrets. Secrets = gitleaks CLI in a container on push **and** pull_request, read-only token. Do not scope it to push only (fork PRs would skip it; see FAILURES.md).
- Job logs are not downloadable from these sessions; use `gh api repos/.../check-runs/<job_id>/annotations` and reproduce failures locally.
