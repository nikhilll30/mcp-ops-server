# Failure log

What broke, what changed, what happened next. Newest first.

## 2026-10-19 · Part 5: the first full live run failed 2 tasks that no agent could pass cleanly
- **Result:** `gpt-6-luna`, default reasoning effort, all 31 tasks, server at 888ab41.
  - **29/31 passed** and 12/12 negative controls were caught.
  - 97 tool calls, 8 tool errors (most of them expected refusals), 0 agent errors.
  - 335,824 input and 9,348 output tokens, **$0.038** at $0.10/$0.50 per 1M. That is an upper bound, because cached input bills at $0.01.
  - Task latency: p50 5.5 s, p99 35.8 s.
- **What surprised me:** both failures were defects in my tasks, not in the model. Each depended on information that only the task's author had, from reading the seed. No tool exposes it.
  - **k01** says "book SLT-01557; if taken, book the next open slot with the same provider that day". The replay script went straight to PRV-007 on 2026-11-27 because I knew that from the seed. No tool looks up a slot by ID, and the "already booked" error did not say whose slot it was or when. Luna spent 15 calls searching, then honestly said it could not verify the slot's date. That is an impossible task, not a model failure.
  - **f01** has the earliest dermatology slot that week clash with PAT-0001's existing appointment. The replay skips 2026-11-09 for that reason. No tool lists a patient's appointments, so even a perfect agent could hit that clash once. Luna did, then booked a valid slot. The task allowed 0 errors.
- **Rule adopted:** a task may not depend on information no tool provides. This is a general rule, not a fix for this one run. The b01 off-by-one stays a model-plus-wording issue: the information was there, just ambiguous.
- **What changed:**
  - **Server:** "already booked" now names the slot's provider and start time, so "the same provider that day" is findable. It reveals nothing about who booked the slot. **Test:** the error names the provider and time; it fails against the old message.
  - **Task:** f01 allows 1 error, with the reason written in the generator.
  - Neither change touches any other task's expectations. The other referral tasks now state `max_errors: 0` explicitly (same as the default), and the replay gate is still 31/31 and 12/12.
- **Still open:** I fixed the two hidden-knowledge cases a model actually hit. I have not audited every task for more. That audit is a good Codex review item.

## 2026-10-19 · Part 5: a live model found an ambiguous tool contract (off-by-one on "31 days")
- **What broke:** In live run #3, `gpt-6-luna` failed b01 again on its error budget, and the new "Failed tasks" section showed why. Both errors were `list_open_slots` from 2026-11-02 to 2026-12-03. The description said "Last day to search, inclusive … Range at most 31 days", and the server rejects any span whose end − start is 31 days or more. So the limit is 31 days *counting both ends* (end ≤ start + 30), while the model read it as end ≤ start + 31. The error message ("Date range is limited to 31 days.") did not name the boundary either. The model repeated the mistake for a second provider, which is its own weakness, but the contract was genuinely ambiguous.
- **What surprised me:** No test covered this limit at all. The replay agent never asks for long ranges, so 31/31 passing said nothing about it.
- **What changed:**
  - **Server, not grader:** the description now says "At most 31 days counting both ends, so end_date is at most start_date + 30 days". The error names the latest valid end_date for the given start.
  - b01's zero-error budget is unchanged.
  - The schema snapshot was regenerated on purpose; its diff is exactly that description.
  - **Test:** start + 30 is accepted and start + 31 is rejected with the latest end_date named. It fails against the old message.
- **What happened next:** a re-run of b01 shows whether the clearer contract is enough. One run proves little either way, because the model is not deterministic.

## 2026-10-19 · Part 5: the first real failure could not be diagnosed from the report
- **What happened:** Live run #2 (`gpt-6-luna`, 3 tasks) passed 2 of 3 tasks. `b01-earliest-primary-care` booked exactly the right slot (final state correct), but it hit 2 tool errors along the way. That task allows 0, so it failed `max_errors`. The report gave each task's error count but not the calls or the server's messages. I could not tell whether the model stumbled or a tool description misled it, and those need opposite responses: a misleading description gets a server fix, a stumble keeps its fail.
- **What changed:** The report gains a "Failed tasks" section. For each failed task it lists the failing checks with their details, and every failed call with its arguments and the server's error. **Test:** a failed call's arguments and error appear in the section.
- **What I did not do:** loosen b01's error budget so the run passes. Changing the grader after seeing a model fail would grade the model on a curve. Any change waits for the actual errors.

## 2026-10-19 · Part 5: the first live run was rejected by the API on every call
- **What broke:** Live run #1 (`gpt-6-luna`, 3 tasks) failed all 3 tasks before any tool call, with `400 BadRequestError: "Function tools with reasoning_effort are not su…"`. The agent sent no reasoning setting, so the model's own default triggered it. The same error is reported publicly for other current reasoning models on `/v1/chat/completions`, with two workarounds: set reasoning to `none`, or use `/v1/responses`.
- **What worked:** the safety net. The report shows 0 tokens used, the run went red instead of reporting "0/3" as a measurement, and nothing leaked. The key, the environment and the model ID all checked out (a bad key gives 401, an unknown model 404).
- **What surprised me:**
  - My fake client and local stand-in accepted any request, so no test could know what a real model rejects.
  - The report cut the error message at 120 characters, which hid the part that names the fix.
  - This session's network blocked downloading the artifact. I found the full message by searching for its first half.
- **What changed:**
  - The agent uses the Responses API, chaining turns with `previous_response_id` so reasoning carries across tool calls. Turning reasoning off was rejected, because it would understate the model.
  - Reasoning effort is an optional input and is shown in the report.
  - Agent errors are printed in full in their own report section and in the job log, where they can be read without the UI.
  - **Tests:** the local stand-in now speaks the Responses format and does a real tool round trip through the real SDK. Mutation checks pass: dropping the response chaining, the re-sent instructions, the non-strict schema, the effort parameter, or the call ID each fails a test.
- **Not yet verified:** that `gpt-6-luna` accepts this request. Only a paid run can show that; it is the next step.

## 2026-10-18 · Part 5: Codex review found the live eval broken or misleading 6 ways (4x P1, 2x P2, all fixed)
I reproduced the event-loop bug with the real SDK before fixing it. Each fix has a test that fails with the bug put back.
- **P1 · The live agent failed from the second task on.** Every task ran in its own `asyncio.run()`, but one `AsyncOpenAI` client served them all, and its connection pool is bound to the first loop. **Reproduced:** the real SDK against a local stand-in endpoint; task 2 failed with `RuntimeError: Event loop is closed`. My fake client had no pool, so my tests could not see it. **Fix:** the whole suite runs in one event loop, and the agent's client is closed after the last task. **Tests:**
  - the real SDK against a local endpoint (CI's test job now installs it);
  - a fake client that, like the real pool, refuses a second loop.

  Both fail with a fresh loop per task.
- **P1 · Malformed tool arguments were invisible to the grader.** The agent rejected them itself, so they never reached the recorder and cost no calls or errors. **Fix:** the agent passes them to the harness's `call`, which records a failed step without sending it to the server. **Test:** two malformed calls now fail r01's error budget (the old test asserted the misleading pass).
- **P1 · Dispatching the live workflow on an unreviewed branch would run that branch's code with the key.** An in-file `if: ref == main` check is not enough, because the dispatched branch's own copy of the file could delete it. **Fix:** the key is a secret of a `live-evals` environment that only `main` may use; GitHub enforces that server-side. The in-file check stays as a clear error message. *Not testable from here: the environment is configured by the owner in repo settings.*
- **P1 · A live run where every API call failed exited 0.** API errors became task failures, and live runs gate on no pass rate. **Fix:** any agent error (crash, bad key, unknown model, outage after the SDK's retries) fails the run, and agent errors are counted in the report. **Test:** the CLI with a failing client exits 1.
- **P2s:**
  - Tokens spent before a failure vanished from the report. They are now attached to the error and reported, with a test where the failure comes after one successful call.
  - The system prompt said "never work around a taken slot", while task k01 explicitly asks for the next open slot as a fallback. The prompt now allows fallbacks the user asked for.
- **Lesson:** I tested the agent loop against a fake that was simpler than the real client in exactly the way that mattered (no connection state). Where a dependency has state, test against the real one at least once, even if only on localhost.

## 2026-10-18 · Part 5: Part 4's evals never tested the tools as a model sees them
- **What surprised me:** The replay agent follows a script and never reads tool names, descriptions or schemas. So in Part 4 a misleading or wrong tool description would pass every eval; only the schema snapshot test would notice it changed, not whether it is any good.
- **What changed:** The harness now lists the server's tools in each task's session and hands them to the agent. The live OpenAI agent builds its tool definitions from that list only. `test_correct_run_passes_and_is_accounted` checks that the model receives the server's `book_appointment` schema.
- **What happened next:** Description quality now affects the live score. It is measured only once the owner triggers a live run; no live number exists yet.

## 2026-10-18 · Part 5: the CI test job does not install the OpenAI SDK
- **What surprised me:** `openai` is an optional `live` extra, and CI installs `[dev]` only. One top-level `import openai` in the agent module or the tests would pass locally (where the SDK is installed) and fail CI.
- **What changed:** The SDK is imported inside `OpenAIAgent.__init__`, only when no client is injected. The tests inject a fake client.
- **What happened next:** I ran the full suite in a Python 3.12 venv without `openai` (`import openai` fails there): 167 passed. I also mutation-checked the agent loop. Each of these mutations fails a test: dropping the JSON-object check, token accounting, the tool-call ID mapping, the turn cap, or error feedback.

## 2026-10-18 · Part 4: Codex review found the grader could be fooled 9 ways (5x P1, 5x P2, all fixed)
I reproduced the grader-level holes directly (all four passed bad trajectories before the fix), then fixed each and added a test or negative control that fails with the hole put back.
- **P1 · The agent chose what got graded.** The harness graded the step list the agent returned, so an agent could hide a call or invent one. **Fix:** the harness records every call made through `call()` and grades that; the agent's return value is ignored. **Test:** an agent that does an out-of-scope lookup and reports nothing fails `patient_scope`.
- **P1 · "Earliest" and "next" were checked only by date.** b01 accepted any slot that day. k01's own reference booked the wrong slot (09:00 instead of the next one after 10:00) and still passed. **Fix:** both expectations are exact slot IDs computed from the seed. **Control:** n11 books a later slot.
- **P1 · The referral check could be the wrong one, or come too late.** A cardiology check satisfied a dermatology booking, and "book, check, cancel, rebook" satisfied the ordered requirement. **Fix:** a new `preconditions` check (every successful booking must follow a check of that specialty's referral) and an `$optional` matcher for the specialty argument. **Controls:** n08 and n09.
- **P1 · Net-change grading hid write cycles.** An appointment booked and cancelled within a task vanished from the diff. **Fix:** the diff reports `transient` appointments, which fail unless expected, and every refusal and auth task forbids successful writes. **Control:** n10.
- **P1 · One bad run could poison later tasks.** Tasks shared one database. **Reproduced:** with an agent that books k02's setup slot during b01, k02 then failed `setup`. **Fix:** every task gets its own freshly seeded database and server (in-process, ~100 ms). **Test:** that sabotage now fails only b01.
- **P2s:**
  - unordered requirements could reuse one step: now one-to-one assignment;
  - greedy state matching rejected valid sets of changes: now one-to-one assignment;
  - a bad `$ref` crashed the suite: now an `agent_error` failure, which caught my own wrong-day a01 script on its first run;
  - the harness inherited `MCP_OPS_NOW`: the clock is now pinned per task, with a test;
  - a01 never attempted a cross-patient booking: it does now;
  - k03 queried a day with no slots: it now uses a real 09:00 slot, made past by a task clock of 12:00.
- **Lesson:** I claimed the grader checks "every step" but tested it only with well-behaved and obviously bad trajectories. Codex attacked it like an agent trying to pass would. Negative controls should include adversarial ones, not just clumsy ones (now 12, covering every check).

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
