# Status — single source of truth

| Part | State | Brief |
|---|---|---|
| 1 Foundation | done | docs/briefs/part-1-foundation.pdf |
| 2 Core server | done (PR #3 merged) | docs/briefs/part-2-core-server.pdf |
| 3 Hardening | done (PR #4 merged) | docs/briefs/part-3-hardening.pdf |
| 4 Evals | Codex review fixed, awaiting owner merge (PR #5) | docs/briefs/part-4-evals.pdf |
| 5 CI gate + numbers | not started | — |
| 6 Ship | not started | — |

## Tasks
| # | Task | Built by | State | PR |
|---|---|---|---|---|
| 000 | Skeleton server + CI | Cursor agent (CI fix: Claude) | merged | #1 |
| 000r | Codex review fixes (1x P1, 2x P2) | Claude | merged | #2 |
| 002 | Core server: seed, store, 5 tools | Claude | merged | #3 |
| 003 | Hardening: auth, scopes, limits, clock, schema guard | Claude | merged | #4 |
| 004 | Evals: golden set, trajectory grader, replay agent, report | Claude | review fixed (5x P1, 5x P2), awaiting merge | #5 |

## Workflow
Claude builds each part in a fresh Claude Code session (rules in `CLAUDE.md`); Codex reviews each
part via a prompt the owner pastes; the owner reads the brief and merges.
History: GitHub @-mention delegation to Codex and Cursor did not trigger reliably (see FAILURES.md).

## Next action: owner merges PR #5, then says "go" for Part 5
- Codex review (5x P1, 5x P2) reproduced and fixed; details in FAILURES.md.
- Owner decision needed for Part 5: which model/API for the live agent, and run frequency (every PR or manual).

## After that: Part 5 · CI gate + numbers (branch `part-5-ci-gate`)
- Dedicated eval job in GitHub Actions that publishes evals/report.md and blocks merges on any failure.
- Live LLM agent behind the same `Agent` interface (evals/agents.py); measure pass rate, cost and
  latency p50/p99 on the 31 golden tasks. Needs an API key as a CI/env secret (owner decision).
- Known limits carried forward: tokens never expire; rate limit is per process.
