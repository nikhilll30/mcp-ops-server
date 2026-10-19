# Status — single source of truth

| Part | State | Brief |
|---|---|---|
| 1 Foundation | done | docs/briefs/part-1-foundation.pdf |
| 2 Core server | done (PR #3 merged) | docs/briefs/part-2-core-server.pdf |
| 3 Hardening | done (PR #4 merged) | docs/briefs/part-3-hardening.pdf |
| 4 Evals | done (PR #5 merged) | docs/briefs/part-4-evals.pdf |
| 5 CI gate + numbers | done (PRs #6-#11); Luna 30/31, Sol 30/31 (PR #12) | docs/briefs/part-5-ci-gate.pdf |
| 6 Ship | done (PR #13 merged; repo and image public) | docs/briefs/part-6-ship.pdf |

## Tasks
| # | Task | Built by | State | PR |
|---|---|---|---|---|
| 000 | Skeleton server + CI | Cursor agent (CI fix: Claude) | merged | #1 |
| 000r | Codex review fixes (1x P1, 2x P2) | Claude | merged | #2 |
| 002 | Core server: seed, store, 5 tools | Claude | merged | #3 |
| 003 | Hardening: auth, scopes, limits, clock, schema guard | Claude | merged | #4 |
| 004 | Evals: golden set, trajectory grader, replay agent, report | Claude | merged | #5 |
| 005 | CI eval gate, live agent, live runs and fixes | Claude | merged | #6-#12 |
| 006 | Ship: container + CI smoke + GHCR, README, demo GIF | Claude | merged | #13 |

## Workflow
Claude builds each part in a fresh Claude Code session (rules in `CLAUDE.md`); Codex reviews each
part via a prompt the owner pastes; the owner reads the brief and merges.
History: GitHub @-mention delegation to Codex and Cursor did not trigger reliably (see FAILURES.md).

## Owner decisions
- **Part 5 (2026-10-18):** the free replay eval gates every push and PR. The paid live eval runs
  only when the owner triggers it, on OpenAI. The key is the `OPENAI_API_KEY` secret of the
  `live-evals` environment (main only), with a $5 hard limit on the OpenAI project.
- **Part 6 (2026-10-19):** no AWS deploy. Ship a container image instead: CI builds it and
  smoke-tests it on every PR, and it is published to GHCR from main. The reasons: no monthly cost
  and no public write endpoint to operate, and the image runs on any container host unchanged.
- **Ruleset `main` (2026-10-19):** created by the owner with these settings:
  - PR required, 0 approvals;
  - required checks lint, test, evals and secrets;
  - force pushes blocked and deletions restricted.

  GitHub Free does not enforce rulesets on private repos, so it takes effect when the repo goes public.

## Part 5 results (details in FAILURES.md and evals/README.md)
`gpt-6-luna` and `gpt-6.1-sol` both passed **30/31**, and all 12 negative controls were caught.
Luna cost $0.046 and Sol $0.339 (upper bounds).

The live runs found 4 defects in my eval, all fixed in the server or the tasks (not the grader):
- the Responses API was needed;
- the report hid errors;
- the "31 days" wording was ambiguous;
- two tasks needed information no tool exposes.

Open: the system prompt's "slot taken → make no booking" rule is ambiguous for flexible
requests. Sol stopped on f01 because of it. Rewording it means re-running both models (owner's call).

## Status: all six parts done (2026-10-19)
- PR #13 merged. The first publish from main pushed the CI-tested image. `latest` and
  `sha-36ea0f6` both have digest `sha256:109fef72…`, the image the `container` job tested.
- Done by the owner:
  - `container` added to the ruleset's required checks;
  - repo made public;
  - GHCR package made public.
- Verified signed out (2026-10-19):
  - the repo API returns `public`;
  - the README is reachable;
  - the ruleset on `main` is enforced, with required checks `container`, `evals`, `lint`, `secrets` and `test`;
  - GHCR issues an anonymous pull token, and both tags resolve to the tested digest.
- Optional follow-ups (owner's call):
  - reword the system prompt's "slot taken" rule, then re-run both live models (about $0.40);
  - repeat live runs to measure variance;
  - token expiry, or reload without a restart.
- Known limits, also listed in the README:
  - tokens never expire, and revoking one needs a restart;
  - the rate limit is per process;
  - the in-memory database resets on restart;
  - live runs are single samples;
  - the container bind turns off the SDK's DNS-rebinding check (bearer auth is still required).
