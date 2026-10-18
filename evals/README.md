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
- An LLM agent plugs into the same `Agent` interface in Part 5.

## Negative controls
`golden/negative.json` holds deliberately bad trajectories (wrong patient, skipped referral
check, a write during a lookup, flailing, ...). Each must fail for its stated reason; together they
cover every check. They prove the grader can fail, and they catch server regressions a
well-behaved agent never trips (making pending referrals bookable broke only a control).

## Adding a task
Add it in `golden/make_tasks.py`. Compute exact expectations from the seed there (see `b01`
and `k01`), regenerate, and run `python -m evals.run`.
