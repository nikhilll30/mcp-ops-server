# Master Plan — mcp-ops-server

**Owner:** Nikhil (approves each part) · **Planner + implementer:** Claude · **Independent reviewer:** Codex (end of each part)

## Goal
A production-shaped MCP server for a **synthetic clinic-operations domain** (appointments,
providers, referrals), plus a **trajectory-level eval harness** and a **CI gate** that blocks
merges when agent quality drops. Every number in the README is measured, every failure is logged.

Why this domain: realistic multi-step agent tasks (find slot → check referral → book) and
real security stakes (scoped access to patient-like records), with **no real data, ever**.

## Architecture (target)
```
agent (LLM or scripted replay)
   │  MCP over Streamable HTTP
   ▼
mcp_ops_server/
   server.py      MCPServer (mcp SDK 2.x) — tool registration
   auth.py        bearer tokens → scopes (read:schedule, write:schedule, read:referrals)
   tools/         one module per tool group; pure functions over the store
   store.py       SQLite, seeded deterministically from data/seed.py (synthetic only)
   limits.py      rate limits, max payload size, per-call timeouts
evals/
   golden/        labelled tasks: prompt → expected final state
   trajectory/    expected tool-call sequences; step-level grading
   run.py         runs agent vs server, writes results JSON + report.md
```

## Key decisions (and why)
| Decision | Why |
|---|---|
| MCP Python SDK **2.x** (`MCPServer`, not `FastMCP`) | Current major version; v1 names are gone |
| SQLite + deterministic seed | Zero infra, reproducible evals, easy to reset per test |
| Scopes enforced in the server, not the prompt | Security must not depend on model behavior |
| Two eval modes: **replay** (no LLM, free, runs in CI) and **live** (real LLM, measured cost) | CI stays free and deterministic; live runs produce the real numbers |
| Step-level (trajectory) grading | Final-answer-only evals miss wrong-tool and unsafe-call failures |
| `FAILURES.md` written as we go | The strongest hiring signal is documented iteration |

## Parts (each ends with a 1-page PDF brief in `docs/briefs/` and Nikhil's go-ahead)
1. **Foundation** — plan, Codex protocol, repo config, skeleton server, CI (lint, test, secret scan)
2. **Core server** — synthetic data + store, 5 tools (search_providers, list_open_slots,
   book_appointment, cancel_appointment, get_referral_status), unit tests, Streamable HTTP
3. **Hardening** — bearer auth + scopes, rate limits, payload/timeouts, schema-drift guard
   (snapshot test of tool schemas), security review
4. **Evals** — golden set (~30 tasks), trajectory grader, replay agent, report.md
5. **CI gate + numbers** — eval gate in GitHub Actions, live run with cost/latency p50/p99
6. **Ship** — AWS deploy (container), README with numbers, demo GIF, go public

## Working protocol
See `AGENTS.md` (Codex reads it automatically) and `comms/STATUS.md` (single source of truth).
