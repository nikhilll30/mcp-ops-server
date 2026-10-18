# Status — single source of truth

| Part | State | Brief |
|---|---|---|
| 1 Foundation | done | docs/briefs/part-1-foundation.pdf |
| 2 Core server | done (PR #3 merged) | docs/briefs/part-2-core-server.pdf |
| 3 Hardening | done (PR #4 merged) | docs/briefs/part-3-hardening.pdf |
| 4 Evals | built, awaiting Codex review (PR #5) | docs/briefs/part-4-evals.pdf |
| 5 CI gate + numbers | not started | — |
| 6 Ship | not started | — |

## Tasks
| # | Task | Built by | State | PR |
|---|---|---|---|---|
| 000 | Skeleton server + CI | Cursor agent (CI fix: Claude) | merged | #1 |
| 000r | Codex review fixes (1x P1, 2x P2) | Claude | merged | #2 |
| 002 | Core server: seed, store, 5 tools | Claude | merged | #3 |
| 003 | Hardening: auth, scopes, limits, clock, schema guard | Claude | merged | #4 |
| 004 | Evals: golden set, trajectory grader, replay agent, report | Claude | awaiting Codex review | #5 |

## Workflow
Claude builds each part in a fresh Claude Code session (rules in `CLAUDE.md`); Codex reviews each
part via a prompt the owner pastes; the owner reads the brief and merges.
History: GitHub @-mention delegation to Codex and Cursor did not trigger reliably (see FAILURES.md).

## Next action: Codex review of PR #5 (branch `part-4-evals`)
- Owner pastes the Codex prompt from the hand-off and pastes findings back; Claude fixes or pushes back.
- Then owner merges and says "go" for Part 5.

## After that: Part 5 · CI gate + numbers (branch `part-5-ci-gate`)
- Dedicated eval job in GitHub Actions that publishes evals/report.md and blocks merges on any failure.
- Live LLM agent behind the same `Agent` interface (evals/agents.py); measure pass rate, cost and
  latency p50/p99 on the 31 golden tasks. Needs an API key as a CI/env secret (owner decision).
- Known limits carried forward: tokens never expire; rate limit is per process; golden tasks share
  one database per run, so tasks must use disjoint patients/days.
