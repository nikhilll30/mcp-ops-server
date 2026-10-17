# Status — single source of truth

| Part | State | Brief |
|---|---|---|
| 1 Foundation | done, awaiting owner go-ahead | docs/briefs/part-1-foundation.pdf |
| 2 Core server | not started | — |
| 3 Hardening | not started | — |
| 4 Evals | not started | — |
| 5 CI gate + numbers | not started | — |
| 6 Ship | not started | — |

## Tasks
| # | Task | Built by | State | PR |
|---|---|---|---|---|
| 000 | Skeleton server + CI | Cursor agent (CI fix: Claude) | merged | #1 |

## Workflow change (2026-10-17)
GitHub @-mention delegation did not trigger reliably (Codex: no response; Cursor: gated behind Bugbot).
From Part 2 on, Claude implements; Codex reviews each part independently via a prompt the owner pastes.

## Next action
Owner reads the Part 1 brief and says go. Then Part 2: synthetic data, store, 5 tools.
