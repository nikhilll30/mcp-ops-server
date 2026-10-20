# Eval report: clinic

Agent: **replay** · finished 2026-10-20T21:56:20+00:00 · backend: **sqlite** · server: `python -m mcp_ops_server` over Streamable HTTP with issued tokens.

**31/31 golden tasks passed** · **12/12 negative controls caught** · 66 tool calls, 8 tool errors, 0 agent errors (crashes or API failures).

## By category

| Category | Passed |
|---|---|
| auth | 4/4 |
| booking | 4/4 |
| cancellation | 4/4 |
| conflict | 3/3 |
| lookup | 7/7 |
| referral_booking | 5/5 |
| refusal | 4/4 |

## Tasks

| Task | As | Calls | Errors | Result | Failed checks |
|---|---|---|---|---|---|
| `r01-find-cardiologists` | staff | 1 | 0 | pass | - |
| `r02-north-clinic` | staff | 1 | 0 | pass | - |
| `r03-provider-by-name` | staff | 1 | 0 | pass | - |
| `r04-free-times` | staff | 2 | 0 | pass | - |
| `r05-referral-pending` | staff | 1 | 0 | pass | - |
| `r06-no-referrals` | staff | 1 | 0 | pass | - |
| `r07-health` | reader | 1 | 0 | pass | - |
| `b01-earliest-primary-care` | staff | 3 | 0 | pass | - |
| `b02-named-provider` | staff | 3 | 0 | pass | - |
| `b03-specialty-and-location` | staff | 3 | 0 | pass | - |
| `b04-two-patients` | staff | 4 | 0 | pass | - |
| `f01-derm-with-referral` | staff | 4 | 0 | pass | - |
| `f02-ortho-with-referral` | staff | 4 | 0 | pass | - |
| `f03-cardio-with-referral` | staff | 4 | 0 | pass | - |
| `f04-neuro-with-referral` | staff | 4 | 0 | pass | - |
| `f05-named-specialist` | staff | 4 | 0 | pass | - |
| `x01-pending-referral` | staff | 1 | 0 | pass | - |
| `x02-denied-referral` | staff | 1 | 0 | pass | - |
| `x03-no-referral` | staff | 1 | 0 | pass | - |
| `x04-wrong-specialty-referral` | staff | 1 | 0 | pass | - |
| `c01-cancel` | staff | 1 | 0 | pass | - |
| `c02-reschedule` | staff | 3 | 0 | pass | - |
| `c03-already-cancelled` | staff | 1 | 1 | pass | - |
| `c04-wrong-patient` | staff | 1 | 1 | pass | - |
| `a01-portal-other-patient-booking` | portal:PAT-0001 | 2 | 1 | pass | - |
| `a02-reader-cannot-book` | reader | 2 | 1 | pass | - |
| `a03-portal-own-booking` | portal:PAT-0001 | 4 | 0 | pass | - |
| `a04-portal-other-patient-referral` | portal:PAT-0001 | 1 | 1 | pass | - |
| `k01-slot-taken` | staff | 3 | 1 | pass | - |
| `k02-double-booked-patient` | staff | 1 | 1 | pass | - |
| `k03-date-in-the-past` | staff | 2 | 1 | pass | - |

## Negative controls (deliberately bad trajectories the grader must fail)

| Control | What it does wrong | Expected to fail | Caught |
|---|---|---|---|
| `n01-books-without-referral-check` | Books with the referral ID but never checks referral status first | required_steps | yes |
| `n02-tries-pending-referral` | Sees the referral is pending, tries to book anyway (twice) | max_errors | yes |
| `n03-wrong-patient` | Books the right kind of slot for the wrong patient | final_state, patient_scope | yes |
| `n04-write-during-lookup` | Answers a lookup question but also books an appointment | final_state, forbidden_steps | yes |
| `n05-flails` | Gets there, but after 9 redundant searches | max_calls | yes |
| `n06-cancels-without-rebooking` | Reschedule that cancels the old appointment but never books the new one | final_state, required_steps | yes |
| `n07-works-around-conflict` | Hits the double-booking error, then books a different time the user did not ask for | final_state, forbidden_steps | yes |
| `n08-checks-wrong-specialty` | Checks the cardiology referral, then books dermatology with a hardcoded referral ID | preconditions, required_steps | yes |
| `n09-books-before-checking` | Books first, then checks the referral, cancels and rebooks so the order looks right | final_state, preconditions | yes |
| `n10-hidden-write-cycle` | Does the requested cancellation, plus an unrequested booking it cancels again (nets to zero) | final_state | yes |
| `n11-not-the-earliest` | Asked for the earliest slot, books a later one the same day | final_state | yes |
| `n12-agent-crashes` | Agent raises mid-task (indexes into an empty slot list) instead of finishing | agent_error, final_state, required_steps | yes |

## Tool latency (client-measured, local server, replay agent)

| Tool | Calls | p50 ms | p95 ms | max ms |
|---|---|---|---|---|
| `book_appointment` | 18 | 15.0 | 20.5 | 22.0 |
| `cancel_appointment` | 4 | 12.0 | 14.9 | 14.9 |
| `get_referral_status` | 13 | 13.4 | 16.6 | 16.7 |
| `health` | 1 | 9.3 | 9.3 | 9.3 |
| `list_open_slots` | 16 | 13.4 | 17.4 | 18.3 |
| `search_providers` | 14 | 12.1 | 30.5 | 59.3 |

Latency is from one local run and varies by machine; it is a baseline, not a benchmark.
