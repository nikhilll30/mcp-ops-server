# Status — single source of truth

| Part | State | Brief |
|---|---|---|
| 1 Foundation | done | docs/briefs/part-1-foundation.pdf |
| 2 Core server | done (PR #3 merged) | docs/briefs/part-2-core-server.pdf |
| 3 Hardening | built, security review clean, awaiting Codex review (PR #4) | docs/briefs/part-3-hardening.pdf |
| 4 Evals | not started | — |
| 5 CI gate + numbers | not started | — |
| 6 Ship | not started | — |

## Tasks
| # | Task | Built by | State | PR |
|---|---|---|---|---|
| 000 | Skeleton server + CI | Cursor agent (CI fix: Claude) | merged | #1 |
| 000r | Codex review fixes (1x P1, 2x P2) | Claude | merged | #2 |
| 002 | Core server: seed, store, 5 tools | Claude | merged | #3 |
| 003 | Hardening: auth, scopes, limits, clock, schema guard | Claude | awaiting Codex review | #4 |

## Workflow
Claude builds each part in a fresh Claude Code session (rules in `CLAUDE.md`); Codex reviews each
part via a prompt the owner pastes; the owner reads the brief and merges.
History: GitHub @-mention delegation to Codex and Cursor did not trigger reliably (see FAILURES.md).

## Next action: Codex review of PR #4 (branch `part-3-hardening`)
- Owner pastes the Codex prompt from the PR #4 hand-off and pastes findings back; Claude fixes or pushes back.
- Then owner merges PR #4 and says "go" for Part 4.

## After that: Part 4 · Evals (branch `part-4-evals`)
- Golden set (~30 tasks): prompt → expected final state, built on the pinned seed IDs.
- Trajectory grader: step-level checks (right tools, right order, no unsafe or unauthorized calls).
- Replay agent (no LLM, free, deterministic) that runs in CI; report.md with results.
- Evals authenticate with issued tokens, including patient-bound ones, so the evals also test the auth rules.
- Known limits carried forward: tokens never expire (revoke = edit file + restart); rate limit is per process.
