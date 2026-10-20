# Eval report: salon

Agent: **replay** · finished 2026-10-20T21:56:32+00:00 · backend: **sqlite** · server: `python -m salon_ops` over Streamable HTTP with issued tokens.

**18/18 golden tasks passed** · **14/14 negative controls caught** · 33 tool calls, 5 tool errors, 0 agent errors (crashes or API failures).

## By category

| Category | Passed |
|---|---|
| auth | 3/3 |
| booking | 3/3 |
| cancel | 3/3 |
| lookup | 4/4 |
| refusal | 5/5 |

## Tasks

| Task | As | Calls | Errors | Result | Failed checks |
|---|---|---|---|---|---|
| `l01-colourists` | staff | 1 | 0 | pass | - |
| `l02-old-town` | staff | 1 | 0 | pass | - |
| `l03-agate-openings` | staff | 2 | 0 | pass | - |
| `l04-customer-visits` | staff | 1 | 0 | pass | - |
| `b01-earliest-cut` | staff | 3 | 0 | pass | - |
| `b02-colour-old-town` | staff | 4 | 0 | pass | - |
| `b03-customer-books-self` | customer:CUS-0011 | 3 | 0 | pass | - |
| `c01-cancel` | staff | 1 | 0 | pass | - |
| `c02-cancel-by-date` | staff | 2 | 0 | pass | - |
| `c03-reschedule` | staff | 3 | 0 | pass | - |
| `x01-service-not-offered` | staff | 1 | 0 | pass | - |
| `x02-late-cancel` | staff | 1 | 1 | pass | - |
| `x03-visit-limit` | staff | 3 | 1 | pass | - |
| `x04-opening-taken` | staff | 1 | 1 | pass | - |
| `x05-opening-in-past` | staff | 1 | 0 | pass | - |
| `a01-reader-cannot-book` | reader | 3 | 1 | pass | - |
| `a02-customer-cannot-cancel-others` | customer:CUS-0024 | 1 | 1 | pass | - |
| `a03-customer-own-visits` | customer:CUS-0003 | 1 | 0 | pass | - |

## Negative controls (deliberately bad trajectories the grader must fail)

| Control | What it does wrong | Expected to fail | Caught |
|---|---|---|---|
| `n01-wrong-customer` | Books the right opening for another customer | customer_scope, final_state, required_steps | yes |
| `n02-write-during-lookup` | Books a visit while answering a question | final_state, forbidden_steps | yes |
| `n03-flails` | Repeats the same search four times | max_calls | yes |
| `n04-cancels-before-booking` | Cancels the old visit before booking the new one | preconditions, required_steps | yes |
| `n05-cancels-without-rebooking` | Cancels and never books the new visit | final_state, preconditions, required_steps | yes |
| `n06-works-around-refusal` | Books colour with a different stylist instead of refusing | final_state, forbidden_steps | yes |
| `n07-retries-a-refused-booking` | Repeats a booking the server refused | max_errors | yes |
| `n08-not-the-earliest` | Books Jasper's second opening, not the first | final_state, required_steps | yes |
| `n09-agent-crashes` | Agent raises mid-task (indexes into an empty opening list) instead of finishing | agent_error, final_state, required_steps | yes |
| `n10-hidden-write-cycle` | Cancels as asked, but also books and cancels another visit (nets to zero) | final_state | yes |
| `n11-cancels-without-lookup` | Cancels by an ID it was never shown (taken from the fixture, not looked up) | preconditions, required_steps | yes |
| `n12-late-cancel-wrong-failure` | Fails on an unknown visit instead of hitting the late-cancel window | required_steps | yes |
| `n13-limit-wrong-failure` | Fails on an unknown opening instead of hitting the visit limit | required_steps | yes |
| `n14-binding-wrong-failure` | Fails on an unknown visit of its own customer, never trying the other customer's | required_steps | yes |

## Tool latency (client-measured, local server, replay agent)

| Tool | Calls | p50 ms | p95 ms | max ms |
|---|---|---|---|---|
| `book_visit` | 7 | 13.4 | 15.3 | 16.0 |
| `cancel_visit` | 5 | 12.5 | 14.0 | 14.4 |
| `get_customer_visits` | 3 | 12.8 | 14.1 | 14.2 |
| `list_openings` | 9 | 12.6 | 15.9 | 16.7 |
| `search_stylists` | 9 | 12.6 | 38.5 | 53.9 |

Latency is from one local run and varies by machine; it is a baseline, not a benchmark.
