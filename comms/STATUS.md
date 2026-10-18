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
| 000r | Codex review fixes (1x P1, 2x P2) | Claude | CI green, awaiting owner merge | #2 |

## Workflow
Claude builds each part in a fresh Claude Code session (rules in `CLAUDE.md`); Codex reviews each
part via a prompt the owner pastes; the owner reads the brief and merges.
History: GitHub @-mention delegation to Codex and Cursor did not trigger reliably (see FAILURES.md).

## Next action: Part 2 · Core server (branch `part-2-core-server`)
- `data/seed.py`: deterministic synthetic data (fixed RNG seed): ~12 providers across specialties,
  ~40 patients with synthetic IDs, 4 weeks of 30-min slots, referrals in states pending/approved/denied.
- `mcp_ops_server/store.py`: SQLite store, created fresh from the seed (in-memory for tests).
- Five tools, each with a TypedDict result and unit tests: `search_providers`, `list_open_slots`,
  `book_appointment` (rejects double-booking and missing approved referral where required),
  `cancel_appointment`, `get_referral_status`.
- Extend the HTTP integration test to a multi-step flow: find slot, check referral, book, cancel.
- Codex review focus: data-integrity bugs (double booking, race conditions), input validation, schema clarity for agents.
