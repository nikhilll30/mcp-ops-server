# Failure log

What broke, what changed, what happened next. Newest first.

## 2026-10-18 · Part 4: a loosened server rule was invisible to every golden task
- **What surprised me:** As a mutation check, I made pending referrals bookable in `store.py`. All 31 golden tasks still passed: the replay agent behaves well, checks the referral, and never tries the forbidden booking, so it never hit the loosened rule. Only negative control `n02` (which tries to book with a pending referral) failed, and that failure is what failed the test suite.
- **What changed:** Negative controls are now required to cover every grader check (`test_negative_controls_cover_every_check`). They stay in CI next to the golden tasks.
- **What happened next:** Mutation checks on the suite: removing patient binding fails a golden task; making pending referrals bookable fails a negative control; disabling the grader's patient-scope check fails a negative control. Lesson: a suite of well-behaved agents only proves the happy paths. Something has to try the wrong thing.

## 2026-10-18 · Part 4: negative controls crashed on a shared database
- **What broke:** The first full run passed all 31 golden tasks, then crashed on negative control `n07`. It reuses task `k02`'s setup, which books a fixed slot, and the golden run had already booked it ("Slot SLT-00211 is already booked").
- **What changed:** Negative controls get their own freshly seeded server. A setup failure is now recorded as a failed `setup` check on that task instead of aborting the whole run.
- **What happened next:** 31/31 golden tasks pass and 7/7 controls are caught. The suite also reuses one MCP session per principal instead of opening one per task, cutting a full run from 35 s to 22 s on this container.

## 2026-10-18 · Part 3: Codex review found 2x P1, 1x P2 (all reproduced, then fixed)
- **P1 · The "per-call time limit" did not cover the whole call.** The deadline started *after* waiting for the in-process lock, and SQLite's busy wait used a fixed timeout outside it. **Reproduced:** a booking succeeded after 336 ms against a 200 ms limit (150 ms lock contention, then 150 ms of another connection's write lock). **Fix:** one deadline fixed before any waiting; SQLite's busy timeout is set to the time left before BEGIN, reads and COMMIT; a write that reaches COMMIT past the deadline is rolled back. After the fix the same scenario fails cleanly at ~200 ms. **Tests:** lock-plus-DB contention, work that overruns before COMMIT, and a reader blocking COMMIT. Each finishes within the budget plus 80 ms, and the slot stays open.
- **P1 · Tokens sharing a client_id shared sessions.** The SDK ties a Streamable HTTP session to (client_id, issuer, subject), and my verifier set no subject. **Reproduced:** a PAT-0008 token DELETEd a staff token's session (200), and the staff token's next request got 404. Tool calls were still checked per token, so no patient data leaked, but sessions weren't isolated. **Fix:** each token's subject is its own hash. **Tests:** a unit test that subjects differ; an HTTP test where a second token with the same client_id gets 404 for ping and DELETE on the first token's session, and the owner keeps working.
- **P2 · Invalid calls and `health` bypassed the rate limit.** The limit ran inside tool bodies, after the SDK validated arguments. **Reproduced:** with a 1-call budget, 4 invalid calls cost nothing, and `health` worked after the budget was spent. **Fix:** an `MCPServer` subclass rate-limits in `call_tool`, before validation, for every tool. `health` counts, and its description now says so (the schema snapshot flagged that change).
- **Lesson:** my Part 3 claims ("every call is bounded", "a token can't affect another's") were true for the paths I tested and false for paths I didn't think of: waits outside the statement, and session state outside the tools. Each fix above was checked by putting the bug back and watching its test fail.

## 2026-10-18 · Part 3: tool descriptions differed between Python 3.12 and 3.13
- **What broke:** The new schema snapshot test passed locally (Python 3.13) and failed in CI (Python 3.12). Python 3.13 strips docstring indentation at compile time; 3.12 does not. The SDK uses a tool's raw `__doc__` as its description, so on 3.12 every multi-line description an agent saw was full of stray indentation. This was already true in Part 2; nothing checked it until now.
- **What changed:** Tools are registered through a small `tool()` helper that passes `inspect.cleandoc(fn.__doc__)` as the description, so agents see the same text on every Python. The snapshot was regenerated; the only change is that 4 descriptions lose a trailing newline.
- **What happened next:** Reproduced in a fresh Python 3.12 venv with CI's dependency versions (failed there before the fix). After the fix, all 112 tests pass on both 3.12 and 3.13. The snapshot test caught a real cross-version contract difference on its first CI run.

## 2026-10-18 · Part 3: SDK defaults were far looser than this server needs
- **What surprised me:** The MCP SDK's Streamable HTTP defaults are a 4 MiB request body cap, 10,000 concurrent sessions and a 30-minute session idle timeout. The largest legitimate request to this server is well under 1 KiB.
- **What changed:** The entry point now builds the app itself, with a 64 KiB cap (413 above it), 100 sessions and a 10-minute idle timeout, all set by env vars. An HTTP test checks for the 413.
- **What happened next:** `python -m mcp_ops_server` serves the tightened app instead of `server.run()` with defaults.

## 2026-10-18 · Part 3: a naive per-call timeout would have lied about writes
- **What surprised me:** The obvious timeout, wrapping the worker thread in `anyio.fail_after`, can't stop a thread. The caller would be told "timed out" while the booking could still commit afterwards.
- **What changed:** The time limit is enforced inside SQLite instead. A progress handler interrupts the running statement past the deadline, the transaction rolls back, and the lock wait and busy wait use the same limit.
- **What happened next:** `test_runaway_write_is_rolled_back` shows a timed-out insert never lands. Removing the progress handler makes both timeout tests fail.

## 2026-10-18 · Part 3: 111 tests passed on the first run, so I tried to break them
- **What surprised me:** A brand-new auth layer with every test green on the first run looked too good, given that Part 2's race test had passed without testing what it claimed.
- **What changed:** Mutation checks, one at a time:
  - remove the guard from `book_appointment`: 4 tests fail;
  - disable patient binding: 3 fail;
  - remove the progress handler: 2 fail;
  - drop the past-slot filter: 1 fails;
  - disable the rate limiter: 2 fail;
  - change one word in a tool description: the schema snapshot fails.
- **What happened next:** Each protection now has at least one test that fails without it.

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
