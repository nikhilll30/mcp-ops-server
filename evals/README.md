# Evals

Trajectory-level evals for the MCP server. An agent's run is graded on **every step it took** and
on **what actually changed in the database**, not just on its final answer.

```bash
python -m evals.run                    # replay agent; writes evals/report.md; exit 1 on any failure
python -m evals.golden.make_tasks      # regenerate golden/*.json after editing the generator
```

## How a run works
1. **Every task gets its own freshly seeded database and server.** `harness.py` builds the app
   with the same `build_http_app` that `python -m mcp_ops_server` uses (real HTTP, real bearer
   auth, real limits) and runs it in a background thread. So a bad run cannot leave state for
   the next task, and expectations can be exact (e.g. "the earliest slot is SLT-00002").
2. The clinic clock is pinned per task (`now`, default Mon 2026-11-02 08:00) and never read from
   the environment.
3. Per task: optional `setup` calls run as staff (e.g. create an appointment to cancel), the
   prompt is filled in from setup results, and the agent runs as the task's principal
   (`staff`, `reader`, or a patient-bound `portal:PAT-XXXX` token).
4. **The harness records every call the agent makes**; that record, not anything the agent
   returns, is graded. An agent that crashes fails its task (`agent_error`), not the run.
5. `grader.py` checks the recorded trajectory and the before/after diff of the appointments
   table.

## What the grader checks
| Check | Fails when |
|---|---|
| `agent_error` | the agent raised instead of finishing |
| `required_steps` | a required call is missing, failed, or (by default) out of order; each requirement needs its own step |
| `forbidden_steps` | a forbidden call happened, e.g. any successful write in a refusal or auth task |
| `preconditions` | a step happened without its prerequisite before it, e.g. *any* successful specialist booking not preceded by a check of *that specialty's* referral |
| `patient_scope` | any successful call named a patient the task did not mention |
| `max_calls` / `max_errors` | the agent flailed or kept hitting errors |
| `final_state` | the database change differs from the expected bookings, cancellations and transient (booked-then-cancelled) appointments; matched one-to-one, extra changes fail |

## Agents
- `ReplayAgent` (no LLM): replays each task's reference trajectory, resolving `$ref` values
  against earlier results. Free and deterministic, so it runs in CI on every push: a server
  change that breaks a known-good flow fails the build.
- `OpenAIAgent` (`agents_openai.py`): an OpenAI model driving the server through the
  Responses API's function calling (Chat Completions rejects tools plus reasoning on current
  reasoning models). Optional `--reasoning-effort`; the report records which was used. It sees exactly the tools the server advertises (names,
  descriptions, input schemas), so a vague description costs score. Its tool calls go through
  the same recorded `call`, so it is graded exactly like the replay agent. Tokens, cost,
  latency and final answers are reported, never graded.

## CI gate and live runs
- **Every push and PR** runs the `evals` job: all golden tasks with the replay agent and all
  negative controls. Free, no secrets. It fails on any failed task or uncaught control, and the
  report goes to the job summary and an artifact.
- **Live runs are paid and owner-triggered only**: Actions > *Live evals* > Run workflow, with
  the exact model ID (and optionally task IDs and per-1M-token prices for a cost line). The key
  is the secret `OPENAI_API_KEY` of the `live-evals` environment, which only `main` can use, so
  neither PRs nor a dispatch on an unreviewed branch can reach it. A live run reports a pass
  rate and does not fail on it unless `--min-pass-rate` is set, but it does fail on any agent
  error (bad key, unknown model, API outage), since that run measured nothing.

Locally:

```bash
pip install -e ".[dev,live]"
export OPENAI_API_KEY=...   # your own key; never commit it
python -m evals.run --agent openai --model <model-id> --tasks r01-find-cardiologists \
    --price-in <usd-per-1M-in> --price-out <usd-per-1M-out>   # report: evals/results/live-report.md
```

## Live results

One run each, all 31 tasks, `gpt-6-luna` at its default reasoning effort, through the
Responses API (2026-10-19):

| Server version | Passed | Controls caught | Tool calls (errors) | Cost* | Task latency p50 / p99 |
|---|---|---|---|---|---|
| 888ab41 | 29/31 | 12/12 | 97 (8) | $0.038 | 5.5 s / 35.8 s |
| 6d723c9 (after PR #10) | 30/31 | 12/12 | 101 (8) | $0.046 | 5.1 s / 25.0 s |

\*At $0.10 / $0.50 per 1M input/output tokens, pricing every input token at the full rate.
This is an upper bound, because cached input is billed at $0.01.
Most tool errors are expected refusals, such as a cancelled appointment or a clash with the
patient's existing booking.

- **Run 1:** both failures (k01, f01) were task defects. Each depended on information no tool
  exposes, and PR #10 fixed the server's error message and f01's error budget.
- **Run 2:** the one failure is the model's. k01 booked the right slot, but searched 12 times
  before trying the booking it was asked to make (16 calls; the limit is 6).
- Every refusal, auth and referral-safety task passed in both runs.
- One run per configuration says nothing about run-to-run variance; treat these as single
  samples. The full story is in `FAILURES.md`.

## Negative controls
`golden/negative.json` holds deliberately bad trajectories (wrong patient, skipped referral
check, a write during a lookup, flailing, ...). Each must fail for its stated reason; together they
cover every check. They prove the grader can fail, and they catch server regressions a
well-behaved agent never trips (making pending referrals bookable broke only a control).

## Adding a task
Add it in `golden/make_tasks.py`. Compute exact expectations from the seed there (see `b01`
and `k01`), regenerate, and run `python -m evals.run`.
