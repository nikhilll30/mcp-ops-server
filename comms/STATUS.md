# Status — single source of truth

| Part | State | Brief |
|---|---|---|
| 1 Foundation | done | docs/briefs/part-1-foundation.pdf |
| 2 Core server | done (PR #3 merged) | docs/briefs/part-2-core-server.pdf |
| 3 Hardening | done (PR #4 merged) | docs/briefs/part-3-hardening.pdf |
| 4 Evals | done (PR #5 merged) | docs/briefs/part-4-evals.pdf |
| 5 CI gate + numbers | in progress (branch `part-5-ci-gate`) | — |
| 6 Ship | not started | — |

## Tasks
| # | Task | Built by | State | PR |
|---|---|---|---|---|
| 000 | Skeleton server + CI | Cursor agent (CI fix: Claude) | merged | #1 |
| 000r | Codex review fixes (1x P1, 2x P2) | Claude | merged | #2 |
| 002 | Core server: seed, store, 5 tools | Claude | merged | #3 |
| 003 | Hardening: auth, scopes, limits, clock, schema guard | Claude | merged | #4 |
| 004 | Evals: golden set, trajectory grader, replay agent, report | Claude | merged | #5 |

## Workflow
Claude builds each part in a fresh Claude Code session (rules in `CLAUDE.md`); Codex reviews each
part via a prompt the owner pastes; the owner reads the brief and merges.
History: GitHub @-mention delegation to Codex and Cursor did not trigger reliably (see FAILURES.md).

## Owner decisions for Part 5 (2026-10-18)
- **Free replay evals run on every push and PR and block merges** (dedicated `evals` CI job).
- **Paid live eval runs only when the owner triggers it** (GitHub Actions "Run workflow", about weekly). No schedule.
- **Provider: OpenAI.** Key goes in the repo secret `OPENAI_API_KEY` (owner adds it; never pasted in chat).
  Model: owner names the exact model ID when the key is ready; it is config, not hard-coded.

## Next action: Part 5 · CI gate + numbers (branch `part-5-ci-gate`)
- Build everything that needs no key: eval gate job, OpenAI agent (tested with a fake client), live workflow.
- Then: owner adds `OPENAI_API_KEY` and names the model; owner triggers the first live run; numbers go in the report.
- Known limits carried forward: tokens never expire; rate limit is per process.
