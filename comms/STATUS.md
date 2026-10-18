# Status — single source of truth

| Part | State | Brief |
|---|---|---|
| 1 Foundation | done | docs/briefs/part-1-foundation.pdf |
| 2 Core server | Codex review fixed, awaiting owner merge (PR #3) | docs/briefs/part-2-core-server.pdf |
| 3 Hardening | not started | — |
| 4 Evals | not started | — |
| 5 CI gate + numbers | not started | — |
| 6 Ship | not started | — |

## Tasks
| # | Task | Built by | State | PR |
|---|---|---|---|---|
| 000 | Skeleton server + CI | Cursor agent (CI fix: Claude) | merged | #1 |
| 000r | Codex review fixes (1x P1, 2x P2) | Claude | merged | #2 |
| 002 | Core server: seed, store, 5 tools | Claude | review fixed (2x P1, 3x P2), awaiting merge | #3 |

## Workflow
Claude builds each part in a fresh Claude Code session (rules in `CLAUDE.md`); Codex reviews each
part via a prompt the owner pastes; the owner reads the brief and merges.
History: GitHub @-mention delegation to Codex and Cursor did not trigger reliably (see FAILURES.md).

## Next action: owner merges PR #3, then says "go" for Part 3
- Codex review (2x P1, 3x P2) reproduced and fixed; details in FAILURES.md.

## After that: Part 3 · Hardening (branch `part-3-hardening`)
- Bearer auth → scopes (read:schedule, write:schedule, read:referrals), enforced in server code.
- Rate limits, max payload size, per-call timeouts.
- Schema-drift guard: snapshot test of all tool input/output schemas.
- Known gaps from Part 2: no clock (past slots bookable); any caller can act for any patient ID.
