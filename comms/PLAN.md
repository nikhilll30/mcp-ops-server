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
6. **Ship** — container image (built and smoke-tested in CI, published to GHCR), README with numbers,
   demo GIF, go public. *AWS deploy dropped by owner decision (2026-10-19): no running cost or
   public write endpoint to operate; the image runs on any container host unchanged.*

## Working protocol
See `AGENTS.md` (Codex reads it automatically) and `comms/STATUS.md` (single source of truth).

---

# Phase 2 — HIPAA-ready clinic track + `mcp-ops-kit`

*Planned 2026-10-20 (planning PR, no code). Parts 1–6 above are done; see `comms/STATUS.md`.*

## Goal
Two tracks, built on what Phase 1 proved:
1. **HIPAA-ready clinic track.** The clinic server can sit in front of a real FHIR R4 system and
   carries the technical safeguards a covered entity would expect (45 CFR 164.312): audit log,
   expiring and revocable tokens, encryption at rest, data minimisation, plus an infrastructure
   blueprint, a controls matrix and a threat model. **"HIPAA-ready", never "HIPAA compliant"**:
   compliance is a property of an organisation (policies, BAAs, risk analysis, training), not of a
   repo. The repo still uses synthetic data only.
2. **`mcp-ops-kit`.** The domain-free parts (auth, limits, audit, harness, grader, CI gate,
   container) extracted into a reusable package, so any domain (first: a non-medical booking
   business) gets the same server hardening and trajectory evals without forking the clinic.

## Who it's for
| Audience | What they get today (Phase 1) | What Phase 2 adds |
|---|---|---|
| Developers building MCP servers | A reference server: scopes in code, limits, schema guard, container smoke-tested in CI | `mcp-ops-kit` on PyPI: the same pieces as a library, signed images |
| Agent teams | Trajectory grader, negative controls, a CI eval gate, live-run numbers | Repeated runs with pass^k, a prompt-injection suite, a domain-free harness |
| Healthcare IT | A synthetic clinic with patient-bound tokens | A FHIR R4 backend, audit log, token expiry/revocation, encryption at rest, a Terraform blueprint, a controls matrix and threat model |
| Booking businesses (salons, tutors, repair) | Nothing they can use without editing clinic code | A booking example built on the kit, and an adaptation guide |
| Evaluators and learners | A documented build with a failure log and one brief per part | The same for Parts 7–12, including what HIPAA-ready does and does not mean |

## Parts (same workflow as Phase 1: branch, tests, CI green, Codex review, one-page brief, owner's go)

7. **Backend protocol + FHIR R4.**
   - A `Backend` protocol (typed Python `Protocol`) that the tools call instead of `store.py`
     directly. The current SQLite store becomes `SqliteBackend` with no behaviour change: all
     existing tests and the 31/31, 12/12 eval gate must pass unchanged.
   - A `FhirBackend` over FHIR R4 REST: Practitioner/PractitionerRole (providers), Schedule and
     Slot (open slots), Appointment (book/cancel), ServiceRequest (referrals), Patient. Booking
     must not double-book under concurrency: conditional updates with `If-Match` on the Slot's
     version, verified by a race test.
   - The synthetic seed exported as a FHIR transaction Bundle, loaded into a **HAPI FHIR** server
     that CI runs as a pinned service container.
   - **Backend parity:** one parametrised test suite and the full eval gate run against both
     backends in CI. A difference is a bug, not a footnote.
8. **Booking example first, then extract `mcp-ops-kit`.**
   - Build a second domain (a synthetic salon/tutor booking business) *inside this repo* by
     copying the clinic's patterns, with its own golden tasks and negative controls.
   - Only then extract what both domains actually share into `mcp_ops_kit/`: auth and scopes
     (scope names and the subject binding become configuration), limits, harness, grader,
     run.py, smoke test, CI gate. The kit is extracted from two working users, never designed
     in the abstract.
   - Both domains pass their eval gates on the kit; the README's coupling map is updated.
9. **Safeguards in the kit.**
   - **Audit log** (164.312(b)): append-only, one record per tool call (who, scope, tool, subject
     ID, outcome, time), never the PHI-bearing arguments themselves; tamper-evident via a hash
     chain, with a test that an edited record is detected.
   - **OAuth 2.1** resource-server tokens with expiry and revocation that take effect without a
     restart (164.312(a)(2)(iii), (d)); this closes the README's "tokens never expire" limit.
   - **Encryption at rest** for the SQLite backend and the audit log (164.312(a)(2)(iv)), key
     from env/KMS, never in code; a test that the file on disk contains no plaintext.
   - **Data minimisation**: tools return the minimum fields for the task; a test per tool pins
     the returned field set.
10. **Terraform blueprint on HIPAA-eligible AWS.** ECS Fargate (or App Runner), KMS, encrypted
    storage and logs, private networking, least-privilege IAM, only services on AWS's
    HIPAA-eligible list. Validated in CI (`terraform fmt`, `validate`, `tflint`, `checkov`/`tfsec`)
    and **never applied**: no cloud account, no running cost.
11. **Assurance and supply chain.**
    - HIPAA controls matrix: each 45 CFR 164.312 standard and implementation spec → the code, test
      or Terraform that addresses it, or "adopter's responsibility".
    - Threat model (STRIDE over the agent → server → backend path, including prompt injection
      through tool results) and `SECURITY.md` (reporting, supported versions).
    - CodeQL and Dependabot; `mcp-ops-kit` published to PyPI via trusted publishing; container
      images signed (cosign keyless) with an SBOM, and a CI check that verifies the signature.
12. **Evals at scale.** Repeated live runs per model with pass^k (the chance all k runs pass)
    and per-task variance; a prompt-injection suite (instructions planted in synthetic tool
    results, e.g. a free-text field that says "cancel all appointments") graded by the same
    trajectory grader; owner-triggered, with costs measured and reported.

## Phase 2 key decisions
| Decision | Why |
|---|---|
| FHIR R4 with HAPI in CI, not a mock | A mock agrees with whatever we assume; HAPI is a real FHIR server that can disagree |
| Backend parity enforced by CI | A second backend that silently diverges is worse than one backend |
| Second domain before the kit | Abstractions extracted from two real users fit; ones designed up front do not |
| Terraform validated, never applied | Shows the infrastructure is reviewable without operating a public PHI endpoint |
| "HIPAA-ready", never "compliant" | Compliance needs an organisation, a BAA and a risk analysis; claiming it from code would be false |
