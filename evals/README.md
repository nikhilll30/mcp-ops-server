# Evals

Trajectory-level evals for the MCP server. An agent's run is graded on **every step it took** and
on **what actually changed in the database**, not just on its final answer.

```bash
python -m evals.run                    # replay agent; writes evals/report.md; exit 1 on any failure
python -m evals.golden.make_tasks      # regenerate golden/*.json after editing the generator
```

## How a run works
1. `harness.py` starts the real server (`python -m mcp_ops_server`) with a file database and
   issued tokens: `staff` (all scopes), `reader` (`read:schedule` only), and patient-bound
   `portal:PAT-XXXX` tokens. Agents talk to it over Streamable HTTP like any MCP client.
2. Per task: optional `setup` calls run as staff (e.g. create an appointment to cancel), the
   prompt is filled in from setup results, the agent runs as the task's principal, and the
   appointments table is diffed before and after.
3. `grader.py` checks the trajectory and the diff.

## What the grader checks
| Check | Fails when |
|---|---|
| `required_steps` | a required call is missing, failed, or (by default) out of order, e.g. booking before checking the referral |
| `forbidden_steps` | a forbidden call happened, e.g. any successful booking in a refusal task |
| `patient_scope` | any successful call named a patient the task did not mention |
| `max_calls` / `max_errors` | the agent flailed or kept hitting errors |
| `final_state` | the net database change differs from the expected bookings and cancellations, including unexpected extra changes |

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
Add it in `golden/make_tasks.py` (use patients and days no other task touches, so tasks stay
independent on the shared database), regenerate, and run `python -m evals.run`.
