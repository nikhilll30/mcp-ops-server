# Failure log

What broke, what changed, what happened next. Newest first.

## 2026-10-18 · Part 2: Codex review found 2x P1, 3x P2 (all fixed)
Every bug finding was reproduced here before fixing, and each new test was checked by putting the bug back and watching the test fail.
- **P1 · A failed COMMIT wedged the store.** `COMMIT` sat outside the rollback path. With a file database and another connection holding a read lock, COMMIT failed ("database is locked") and left the transaction open, so every later write failed with "cannot start a transaction within a transaction". **Fix:** COMMIT is inside the `try`; any open transaction is rolled back before the lock is released; lock timeouts become `BusyError` ("busy, retry"). **Test:** a second connection holds a read lock; the booking fails cleanly, the slot stays open, the next booking succeeds.
- **P1 · The race tests never exercised `BEGIN IMMEDIATE`.** All racing calls shared one `Store`, so the Python lock serialized them; the test still passed with `BEGIN IMMEDIATE` removed. **Fix:** new races use separate `Store`s (separate connections) on one file: 8 patients for one slot, and one patient into 4 different slots at the same time (the unique index can't help there). Partial-overlap and back-to-back boundary tests added. **What the mutation showed:** with plain `BEGIN`, SQLite still prevented the double booking, but the losers got "database is locked" (read-to-write lock-upgrade deadlock) instead of a clear conflict. `BEGIN IMMEDIATE` makes them wait and then get "already booked".
- **P2 · Simultaneous first start crashed.** The "already seeded?" check ran before the write transaction, so two processes on a new file both tried to create the schema ("table specialties already exists"). **Fix:** the check runs inside `BEGIN IMMEDIATE`. **Test:** 6 concurrent constructors, no errors, seeded exactly once.
- **P2 · `APT-100000` could be booked but not cancelled.** IDs are zero-padded to *at least* 5 digits; the input pattern required *exactly* 5. **Fix:** pattern `APT-[0-9]{5,18}`. **Test:** fast-forward the sequence, book, cancel.
- **P2 · Extreme dates crashed with a generic error.** `9999-12-31` overflowed date arithmetic. **Fix:** dates must fall in 2000-01-01..2100-12-31, with a clear message. **Test:** three extreme inputs give a `ToolError` that isn't a crash.
- **Lesson:** my own race test proved the lock but not the transaction mode, and I described it as if it covered both. Concurrency tests need to use the same kind of connections production uses.

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
