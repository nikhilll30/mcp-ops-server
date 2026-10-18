# Failure log

What broke, what changed, what happened next. Newest first.

## 2026-10-18 · Part 2: pydantic `\d` accepts non-ASCII digits
- **What broke:** ID patterns like `^PAT-\d{4}$` accepted `PAT-٠٠٠١` (Arabic-Indic digits). pydantic's regex engine treats `\d` as any Unicode digit. Verified with a direct `TypeAdapter` check.
- **What changed:** Patterns use `[0-9]`, with a comment explaining why, and a regression test (`test_ids_reject_non_ascii_digits`).
- **What happened next:** No functional harm in this case (the lookup just misses), but tool input schemas are an API contract for agents and should mean what they say.

## 2026-10-18 · Part 2: shared SQLite connection across SDK worker threads
- **What broke:** The SDK runs sync tools with `anyio.to_thread`, so tool calls can hit one connection concurrently. In a mutation test with the store lock removed, the 20-thread booking race failed with `sqlite3.InterfaceError: bad parameter or other API misuse`.
- **What changed:** Every statement goes through one `threading.Lock`. Writes use `BEGIN IMMEDIATE`, and a partial unique index (`slot_id WHERE status = 'booked'`) is the database-level backstop against double booking.
- **What happened next:** The race test passes with the lock (exactly one winner of 20). The unique-index test fails if the index is dropped.

## 2026-10-18 · Part 2: test fixtures collided with seeded bookings
- **What broke:** 5 tests failed on the first run. The fixture patient (PAT-0001) already had a seeded appointment at the first open slot's time, so booking failed on the overlap rule before it reached the referral rule under test.
- **What changed:** Tests use patients with no seeded bookings (PAT-0008, PAT-0021, pinned in `test_seed.py`), and `first_open_slot()` skips slots where the patient is busy.
- **What happened next:** The overlap rule had proven itself against data it wasn't written for. Lesson: fixtures from a randomized seed need their assumptions pinned by a test.

## 2026-10-17 · CI: secret scan failed only on pull requests
- **What broke:** `gitleaks-action@v2` passed on the `push` run but failed on the `pull_request` run for the same commit.
- **What changed (attempt 1):** Scoped the secrets job to `push` events, claiming coverage was unchanged.
- **What was wrong with that:** Codex's independent review (P1) caught that fork PRs never trigger this repo's push workflow, so external contributions would skip the scan until after merge.
- **What changed (attempt 2):** Replaced the action with the gitleaks CLI in a container, run on both `push` and `pull_request`, with a read-only token (`permissions: contents: read`) and no secrets exposed to PR code.
- **Lesson:** "Coverage is unchanged" was an untested claim. Independent review is worth it.

## 2026-10-17 · Workflow: agent-to-agent delegation over GitHub mentions
- **What broke:** `@codex` PR comments got no response after the environment was set up; `@cursor` replied only with a Bugbot upsell.
- **What changed:** Claude implements; Codex is used as an independent reviewer at the end of each part.
- **What happened next:** Fewer hand-offs, and every part now gets a second-model review.

## 2026-10-17 · Planning: SDK major-version drift
- **What broke:** Most MCP tutorials (and model training data) use `FastMCP` from the v1 SDK. In `mcp` 2.x it was renamed to `MCPServer`, so v1-style code fails on import.
- **What changed:** Pinned `mcp>=2.3,<3` and added an explicit rule to `AGENTS.md` so the implementer does not fall back to v1 APIs.
- **What happened next:** Caught before any code was written.
