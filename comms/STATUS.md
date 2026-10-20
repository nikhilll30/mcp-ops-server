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
| P2 Planning | in review (branch `plan-phase-2`) | docs/briefs/phase-2-plan.pdf |
| 7 Backend protocol + FHIR R4 | **next**, waiting for owner's merge + go | docs/briefs/part-7-fhir-backend.pdf |
| 8 Booking example, then extract mcp-ops-kit | planned | |
| 9 Audit log, OAuth 2.1 expiry/revocation, encryption at rest, data minimisation | planned | |
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
| P2 | Phase 2 planning: PLAN, CLAUDE rules, STATUS, README, brief | Claude | in review | plan-phase-2 |

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

## Next action: Part 7 (Backend protocol + FHIR R4), after the owner merges the planning PR and says "go"
Branch `part-7-fhir-backend` from `main`. Done means all of the following, CI green on push and PR:
1. **`Backend` protocol** (`mcp_ops_server/backends/base.py`): typed methods for every operation the
   tools use today (search providers, list open slots, book, cancel, referral status, plus what the
   harness needs to snapshot appointments). Tools depend on the protocol, not on `store.py`.
2. **`SqliteBackend`**: today's store behind the protocol, no behaviour change. Gate: the 179 existing
   tests, the schema snapshot and the eval gate (31/31, 12/12) pass unchanged.
3. **`FhirBackend`** over FHIR R4 REST using `httpx2` (already installed by `mcp` and used by the harness; no FHIR library
   unless the PR justifies it). Mapping: Practitioner/PractitionerRole → providers, Schedule + Slot →
   open slots, Appointment → book/cancel, ServiceRequest → referrals, Patient → patient IDs.
   Booking updates the Slot with `If-Match` on its version, so two concurrent books of one slot
   give one success and one clean error (race test against HAPI).
4. **Synthetic seed as FHIR**: `seed.py` data exported to a transaction Bundle (deterministic IDs,
   synthetic names only), loaded into HAPI before tests.
5. **HAPI in CI**: a pinned `hapiproject/hapi` image as a service container in a new `fhir` job (or a
   matrix on `test` and `evals`), with a health wait that fails loudly. Locally: `docker run` command
   documented in the README and `.env.example` (`MCP_OPS_BACKEND`, `MCP_OPS_FHIR_URL`).
6. **Parity**: the tool tests are parametrised over both backends, and the eval gate runs on both.
   Any divergence is fixed or kept as a failing test with a FAILURES.md entry, never skipped.
7. **Config**: `MCP_OPS_BACKEND=sqlite|fhir` (default `sqlite`); the server fails closed on an
   unreachable FHIR URL at start, with one clear line.
8. README: Who it's for + Roadmap updated, coupling map row for `backends/`. FAILURES.md, the
   one-page brief `docs/briefs/part-7-fhir-backend.pdf`, a Codex review prompt, STATUS updated.

Part 7 risks for the Codex review: FHIR search paging (missing results past page 1), time zones on
Slot start/end, HAPI's eventual consistency of search indexes after a write, and the concurrency test
being flaky rather than proving the `If-Match` guard.
