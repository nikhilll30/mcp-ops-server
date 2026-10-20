# Status — single source of truth

| Part | State | Brief |
|---|---|---|
| 1 Foundation | done | docs/briefs/part-1-foundation.pdf |
| 2 Core server | done (PR #3 merged) | docs/briefs/part-2-core-server.pdf |
| 3 Hardening | done (PR #4 merged) | docs/briefs/part-3-hardening.pdf |
| 4 Evals | done (PR #5 merged) | docs/briefs/part-4-evals.pdf |
| 5 CI gate + numbers | done (PRs #6-#11); Luna 30/31, Sol 30/31 (PR #12) | docs/briefs/part-5-ci-gate.pdf |
| 6 Ship | done (PR #13 merged; repo and image public) | docs/briefs/part-6-ship.pdf |

### Phase 2 (plan in `comms/PLAN.md`; rules in `CLAUDE.md`)
| Part | State | Brief |
|---|---|---|
| P2 Planning | done (PR #15 merged) | docs/briefs/phase-2-plan.pdf |
| 7 Backend protocol + FHIR R4 | done (PR #16 merged) | docs/briefs/part-7-fhir-backend.pdf |
| 8 Booking example, then extract mcp-ops-kit | **in review** (PR #18), awaiting Codex review | docs/briefs/part-8-mcp-ops-kit.pdf |
| 9 Audit log, OAuth 2.1 expiry/revocation, encryption at rest, data minimisation | next, after the owner's go | |
| 10 Terraform blueprint (HIPAA-eligible AWS, validated, never applied) | planned | |
| 11 Controls matrix, threat model, SECURITY.md, CodeQL, Dependabot, PyPI, signed images | planned | |
| 12 Repeated live runs, pass^k, prompt-injection suite | planned | |

## Tasks
| # | Task | Built by | State | PR |
|---|---|---|---|---|
| 000 | Skeleton server + CI | Cursor agent (CI fix: Claude) | merged | #1 |
| 000r | Codex review fixes (1x P1, 2x P2) | Claude | merged | #2 |
| 002 | Core server: seed, store, 5 tools | Claude | merged | #3 |
| 003 | Hardening: auth, scopes, limits, clock, schema guard | Claude | merged | #4 |
| 004 | Evals: golden set, trajectory grader, replay agent, report | Claude | merged | #5 |
| 005 | CI eval gate, live agent, live runs and fixes | Claude | merged | #6-#12 |
| 006 | Ship: container + CI smoke + GHCR, README, demo GIF | Claude | merged | #13 |
| P2 | Phase 2 planning: PLAN, CLAUDE rules, STATUS, README, brief | Claude | merged | #15 |
| 007 | Backend protocol, SqliteBackend, FhirBackend, HAPI in CI, parity | Claude | merged | #16 |
| 008 | Salon example (copy), then mcp-ops-kit extracted from both domains | Claude | in review | #18 |

## Workflow
Claude builds each part in a fresh Claude Code session (rules in `CLAUDE.md`); Codex reviews each
part via a prompt the owner pastes; the owner reads the brief and merges.
History: GitHub @-mention delegation to Codex and Cursor did not trigger reliably (see FAILURES.md).

## Owner decisions
- **Part 5 (2026-10-18):** the free replay eval gates every push and PR. The paid live eval runs
  only when the owner triggers it, on OpenAI. The key is the `OPENAI_API_KEY` secret of the
  `live-evals` environment (main only), with a $5 hard limit on the OpenAI project.
- **Part 6 (2026-10-19):** no AWS deploy. Ship a container image instead: CI builds it and
  smoke-tests it on every PR, and it is published to GHCR from main. The reasons: no monthly cost
  and no public write endpoint to operate, and the image runs on any container host unchanged.
- **Ruleset `main` (2026-10-19):** created by the owner with these settings:
  - PR required, 0 approvals;
  - required checks lint, test, evals and secrets;
  - force pushes blocked and deletions restricted.

  GitHub Free does not enforce rulesets on private repos, so it takes effect when the repo goes public.

## Part 5 results (details in FAILURES.md and evals/README.md)
`gpt-6-luna` and `gpt-6.1-sol` both passed **30/31**, and all 12 negative controls were caught.
Luna cost $0.046 and Sol $0.339 (upper bounds).

The live runs found 4 defects in my eval, all fixed in the server or the tasks (not the grader):
- the Responses API was needed;
- the report hid errors;
- the "31 days" wording was ambiguous;
- two tasks needed information no tool exposes.

Open: the system prompt's "slot taken → make no booking" rule is ambiguous for flexible
requests. Sol stopped on f01 because of it. Rewording it means re-running both models (owner's call).

## Status: all six parts done (2026-10-19)
- PR #13 merged. The first publish from main pushed the CI-tested image. `latest` and
  `sha-36ea0f6` both have digest `sha256:109fef72…`, the image the `container` job tested.
- Done by the owner:
  - `container` added to the ruleset's required checks;
  - repo made public;
  - GHCR package made public.
- Verified signed out (2026-10-19):
  - the repo API returns `public`;
  - the README is reachable;
  - the ruleset on `main` is enforced, with required checks `container`, `evals`, `lint`, `secrets` and `test`;
  - GHCR issues an anonymous pull token, and both tags resolve to the tested digest.
- Optional follow-ups (owner's call):
  - reword the system prompt's "slot taken" rule, then re-run both live models (about $0.40);
  - repeat live runs to measure variance;
  - token expiry, or reload without a restart.
- Known limits, also listed in the README:
  - tokens never expire, and revoking one needs a restart;
  - the rate limit is per process;
  - the in-memory database resets on restart;
  - live runs are single samples;
  - the container bind turns off the SDK's DNS-rebinding check (bearer auth is still required).

## Part 7 (done, PR #16 merged 2026-10-20)
Built on branch `claude/confident-brahmagupta-mz67te`, the branch this Claude Code session was
assigned (not `part-7-fhir-backend`).
- `Backend` protocol in `backends/base.py`; tools, server and harness depend only on it.
- `SqliteBackend` = old `store.py`, unchanged: 179 tests + snapshot + eval gate passed before any FHIR code.
- `FhirBackend` (FHIR R4 REST, `httpx2`), `fhir_seed` (deterministic Bundles, load/reset/export),
  `MCP_OPS_BACKEND` / `MCP_OPS_FHIR_URL`, fail-closed start-up.
- CI `fhir` job: HAPI `v8.4.0-2` pinned by digest, 294 tests on both backends, eval gate on FHIR.
- Local results: `pytest -q` 217 passed (17 FHIR tests deselected); with
  `MCP_OPS_TEST_BACKENDS=sqlite,fhir`, 294 passed; FHIR eval 31/31, 12/12, every trajectory
  identical to SQLite.
- Found and fixed a real double-booking window (sequence read after the slot), via mutation
  tests; see FAILURES.md.

Codex review (of 5a003a6): 3x P1, 3x P2, all reproduced and fixed (FAILURES.md), plus the
coverage gaps it named. Cursor Bugbot: 1 finding, the same as Codex's first P1, fixed in cc91d98.

Owner, 2026-10-20: merged PR #16 and added `fhir` to the `main` ruleset's required checks.
Verified signed out: `GET /repos/nikhilll30/mcp-ops-server/rules/branches/main` lists the
required checks `lint`, `test`, `evals`, `secrets`, `container` and `fhir`.

The Codex prompt used for Part 7:
```
Review PR #16 in nikhilll30/mcp-ops-server (branch claude/confident-brahmagupta-mz67te) as a senior
engineer. Focus: FHIR search paging (missing results past page 1), time zones on Slot start/end,
HAPI's eventual consistency of search indexes and its search cache after a write, whether the
concurrency tests prove the If-Match guards or could pass by luck, retry/deadline behaviour on
409/412, fhir_seed reset correctness, and any SQLite/FHIR behaviour difference the parametrised
tests miss. Also check test quality and anything that would embarrass this repo once public.
List findings as P0/P1/P2 with file and line. Do not change code.
```

## Part 8 (in review, PR #18)
Built on branch `claude/youthful-hawking-h8wsdc` (the branch this session was assigned).
- **8a (2b76211):** `salon_ops/`, a synthetic salon built by copying the clinic's code:
  - its own scopes and customer binding, 6 tools;
  - rules the clinic lacks: service offered, 24 h late-cancel, 3 upcoming visits;
  - 18 golden tasks and 11 negative controls, gated in the CI `evals` job.
- **8b (d2dd783):** `mcp_ops_kit/`, extracted from the diff between the copies:
  - `AuthConfig`, `SubjectRule`, `EvalDomain`;
  - limits, the SQLite deadline base, server wiring, harness, grader, agents, runner, smoke test.

  Both domains run on it, and the salon went from 2,150 lines to 954.
- Local results:
  - `pytest -q`: 286 passed;
  - `sqlite,fhir` against HAPI: 363 passed, with identical eval trajectories on both backends;
  - clinic 31/31, 12/12 on both backends; salon 18/18, 11/11;
  - Docker image built, and the smoke test passed.
- **Found:** a fail-open in the clinic's token loader. Unknown keys were ignored, so a foreign or mistyped binding key gave an unbound token. Fixed in the kit, with a test (FAILURES.md).
- The eval CLI flag is now `--backend-url`; `--fhir-url` is still accepted.

Codex prompt for Part 8:
```
Review PR #18 in nikhilll30/mcp-ops-server (branch claude/youthful-hawking-h8wsdc) as a senior
engineer. Focus: whether mcp_ops_kit is really domain-free (hidden clinic assumptions, defaults,
naming), whether the clinic's behaviour changed in the extraction (auth, tokens-file format and
validation, settings/env parsing, error messages, start-up failures, eval results), the
AuthConfig/TokenEntry design (unknown keys, subject claim, cross-domain token files), the salon's
integrity rules under concurrency (late-cancel boundary, visit limit, overlap), and whether the
salon's golden tasks and negative controls really test what they claim. Also check test quality
and anything that would embarrass this repo once public. List findings as P0/P1/P2 with file and
line. Do not change code.
```

## Next action
Owner: paste the Codex prompt above, then merge PR #18 once CI is green and findings are handled.
Part 9 starts only after the owner says "go".
