# Status — single source of truth

| Part | State | Brief |
|---|---|---|
| 1 Foundation | done | docs/briefs/part-1-foundation.pdf |
| 2 Core server | done (PR #3 merged) | docs/briefs/part-2-core-server.pdf |
| 3 Hardening | done (PR #4 merged) | docs/briefs/part-3-hardening.pdf |
| 4 Evals | done (PR #5 merged) | docs/briefs/part-4-evals.pdf |
| 5 CI gate + numbers | done pending PR #11 (results); gpt-6-luna 30/31 | docs/briefs/part-5-ci-gate.pdf | docs/briefs/part-5-ci-gate.pdf |
| 6 Ship | not started | — |

## Tasks
| # | Task | Built by | State | PR |
|---|---|---|---|---|
| 000 | Skeleton server + CI | Cursor agent (CI fix: Claude) | merged | #1 |
| 000r | Codex review fixes (1x P1, 2x P2) | Claude | merged | #2 |
| 002 | Core server: seed, store, 5 tools | Claude | merged | #3 |
| 003 | Hardening: auth, scopes, limits, clock, schema guard | Claude | merged | #4 |
| 004 | Evals: golden set, trajectory grader, replay agent, report | Claude | merged | #5 |
| 005 | CI eval gate, OpenAI live agent, manual live-evals workflow | Claude | in review | #6 |

## Workflow
Claude builds each part in a fresh Claude Code session (rules in `CLAUDE.md`); Codex reviews each
part via a prompt the owner pastes; the owner reads the brief and merges.
History: GitHub @-mention delegation to Codex and Cursor did not trigger reliably (see FAILURES.md).

## Owner decisions for Part 5 (2026-10-18)
- **Free replay evals run on every push and PR and block merges** (dedicated `evals` CI job).
- **Paid live eval runs only when the owner triggers it** (GitHub Actions "Run workflow", about weekly). No schedule.
- **Provider: OpenAI.** Key goes in secret `OPENAI_API_KEY` of the `live-evals` environment, deployment branches limited to `main` (owner adds it; never pasted in chat; not a repo-level secret).
  Model: owner names the exact model ID when the key is ready; it is config, not hard-coded.

## Next action: Part 5 · first live run (PR #7)
- Done by owner: PR #6 merged; `live-evals` environment (main only) with `OPENAI_API_KEY`; OpenAI project hard limit $5.
- Live run #1 (`gpt-6-luna`, 3 tasks): every call rejected, 400 "Function tools with reasoning_effort are not supported" on Chat Completions. $0 spent; the run failed red as designed.
- PR #7 moves the agent to the Responses API, adds an optional reasoning-effort input, and shows agent errors in full. Merged. the 3-task test, then all 31 (Sol for the headline number, Luna for comparison).
- Live run #2 (`gpt-6-luna`, 3 tasks): 2/3 pass; b01 booked the right slot but hit 2 tool errors (budget 0). PR #8 (merged) added per-call failure details to the report so the cause is visible; then re-run b01, then all 31.
- Live run #3 (b01 only): same fail; the report showed both errors were `list_open_slots` asking for 32 days. The description "Range at most 31 days" was ambiguous. PR #9 (merged) made the description and error exact (server fix; grader unchanged). Then: re-run b01, then all 31.
- Live run #4 (b01 only, after PR #9): pass, 4 calls, 0 errors.
- Live run #5 (all 31, `gpt-6-luna`, default effort): **29/31**, 12/12 controls, $0.038 (upper bound), task p50 5.5 s / p99 35.8 s. Both failures (k01, f01) needed information no tool exposes; PR #10 fixes them (server error names the slot's provider/time; f01 allows its one unavoidable error). PR #10 merged.
- Live run #6 (all 31, after PR #10): **30/31**, 12/12, $0.046 (upper bound), task p50 5.1 s / p99 25.0 s. k01 is a fair model failure (16 calls, limit 6). Results are in `evals/README.md` (PR #11).
- **Part 5 is done once PR #11 merges.** Optional: one full run on GPT-6.1 Sol adds a comparison row. Next is Part 6 (Ship), on the owner's "go".
- Claude: after the full runs, put pass rate, cost and latency into the README (can ride Part 6).
- Still open for owner: mark `evals` a required check on `main`.
- Do not start Part 6 (Ship) without the owner's "go".
- Known limits carried forward: tokens never expire; rate limit is per process; live runs are single-sample (no repeat runs or variance yet).
