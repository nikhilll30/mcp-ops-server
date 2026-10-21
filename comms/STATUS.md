# Status — single source of truth

| Part | State | Brief |
|---|---|---|
| 1 Foundation | done | docs/briefs/part-1-foundation.pdf |
| 2 Core server | done (PR #3 merged) | docs/briefs/part-2-core-server.pdf |
| 3 Hardening | done (PR #4 merged) | docs/briefs/part-3-hardening.pdf |
| 4 Evals | done (PR #5 merged) | docs/briefs/part-4-evals.pdf |
| 5 CI gate + numbers | done (PRs #6-#11); Luna 30/31, Sol 30/31 (PR #12) | docs/briefs/part-5-ci-gate.pdf |
| 6 Ship | done (PR #13 merged; repo and image public) | docs/briefs/part-6-ship.pdf |

### Phase 2 (plan in `comms/PLAN.md`; rules in `CLAUDE.md`)
| Part | State | Brief |
|---|---|---|
| P2 Planning | done (PR #15 merged) | docs/briefs/phase-2-plan.pdf |
| 7 Backend protocol + FHIR R4 | done (PR #16 merged) | docs/briefs/part-7-fhir-backend.pdf |
| 8 Booking example, then extract mcp-ops-kit | done (PR #18 merged) | docs/briefs/part-8-mcp-ops-kit.pdf |
| 9 Audit log, OAuth 2.1 expiry/revocation, encryption at rest, data minimisation, audience binding | done (PR #20 merged) | docs/briefs/part-9-safeguards.pdf |
| 10 Terraform blueprint (HIPAA-eligible AWS, validated, never applied) | **in review** (PR #22; Codex review pending) | docs/briefs/part-10-terraform.pdf |
| 11 Controls matrix, threat model, SECURITY.md, CodeQL, Dependabot, PyPI, signed images | planned | |
| 12 Repeated live runs, pass^k, prompt-injection suite | planned | |

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
| P2 | Phase 2 planning: PLAN, CLAUDE rules, STATUS, README, brief | Claude | merged | #15 |
| 007 | Backend protocol, SqliteBackend, FhirBackend, HAPI in CI, parity | Claude | merged | #16 |
| 008 | Salon example (copy), then mcp-ops-kit extracted from both domains | Claude | merged | #18 |
| 009 | Safeguards in the kit: tokens v2 + audience, OAuth JWTs, audit log, encryption at rest, minimisation | Claude | merged | #20 |
| 010 | Terraform blueprint (infra/aws), CI terraform job, check_infra.py, hardened container run | Claude | in review | #22 |

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

## Part 7 (done, PR #16 merged 2026-10-20)
Built on branch `claude/confident-brahmagupta-mz67te`, the branch this Claude Code session was
assigned (not `part-7-fhir-backend`).
- `Backend` protocol in `backends/base.py`; tools, server and harness depend only on it.
- `SqliteBackend` = old `store.py`, unchanged: 179 tests + snapshot + eval gate passed before any FHIR code.
- `FhirBackend` (FHIR R4 REST, `httpx2`), `fhir_seed` (deterministic Bundles, load/reset/export),
  `MCP_OPS_BACKEND` / `MCP_OPS_FHIR_URL`, fail-closed start-up.
- CI `fhir` job: HAPI `v8.4.0-2` pinned by digest, 294 tests on both backends, eval gate on FHIR.
- Local results: `pytest -q` 217 passed (17 FHIR tests deselected); with
  `MCP_OPS_TEST_BACKENDS=sqlite,fhir`, 294 passed; FHIR eval 31/31, 12/12, every trajectory
  identical to SQLite.
- Found and fixed a real double-booking window (sequence read after the slot), via mutation
  tests; see FAILURES.md.

Codex review (of 5a003a6): 3x P1, 3x P2, all reproduced and fixed (FAILURES.md), plus the
coverage gaps it named. Cursor Bugbot: 1 finding, the same as Codex's first P1, fixed in cc91d98.

Owner, 2026-10-20: merged PR #16 and added `fhir` to the `main` ruleset's required checks.
Verified signed out: `GET /repos/nikhilll30/mcp-ops-server/rules/branches/main` lists the
required checks `lint`, `test`, `evals`, `secrets`, `container` and `fhir`.

The Codex prompt used for Part 7:
```
Review PR #16 in nikhilll30/mcp-ops-server (branch claude/confident-brahmagupta-mz67te) as a senior
engineer. Focus: FHIR search paging (missing results past page 1), time zones on Slot start/end,
HAPI's eventual consistency of search indexes and its search cache after a write, whether the
concurrency tests prove the If-Match guards or could pass by luck, retry/deadline behaviour on
409/412, fhir_seed reset correctness, and any SQLite/FHIR behaviour difference the parametrised
tests miss. Also check test quality and anything that would embarrass this repo once public.
List findings as P0/P1/P2 with file and line. Do not change code.
```

## Part 8 (done, PR #18 merged 2026-10-20)
Built on branch `claude/youthful-hawking-h8wsdc` (the branch this session was assigned).
- **8a (2b76211):** `salon_ops/`, a synthetic salon built by copying the clinic's code:
  - its own scopes and customer binding, 6 tools;
  - rules the clinic lacks: service offered, 24 h late-cancel, 3 upcoming visits;
  - 18 golden tasks and 14 negative controls, gated in the CI `evals` job.
- **8b (d2dd783):** `mcp_ops_kit/`, extracted from the diff between the copies:
  - `AuthConfig`, `SubjectRule`, `EvalDomain`;
  - limits, the SQLite deadline base, server wiring, harness, grader, agents, runner, smoke test.

  Both domains run on it, and the salon went from 2,150 lines to 954.
- Local results:
  - `pytest -q`: 297 passed;
  - `sqlite,fhir` against HAPI: 374 passed, with identical eval trajectories on both backends;
  - clinic 31/31, 12/12 on both backends; salon 18/18, 14/14;
  - Docker image built, and the smoke test passed.
- **Found:** a fail-open in the clinic's token loader. Unknown keys were ignored, so a foreign or mistyped binding key gave an unbound token. Fixed in the kit, with a test (FAILURES.md).
- The eval CLI flag is now `--backend-url`; `--fhir-url` is still accepted.

Codex prompt for Part 8:
```
Review PR #18 in nikhilll30/mcp-ops-server (branch claude/youthful-hawking-h8wsdc) as a senior
engineer. Focus: whether mcp_ops_kit is really domain-free (hidden clinic assumptions, defaults,
naming), whether the clinic's behaviour changed in the extraction (auth, tokens-file format and
validation, settings/env parsing, error messages, start-up failures, eval results), the
AuthConfig/TokenEntry design (unknown keys, subject claim, cross-domain token files), the salon's
integrity rules under concurrency (late-cancel boundary, visit limit, overlap), and whether the
salon's golden tasks and negative controls really test what they claim. Also check test quality
and anything that would embarrass this repo once public. List findings as P0/P1/P2 with file and
line. Do not change code.
```

Codex review (of e8cd861): 1x P1, 5x P2, all in FAILURES.md.
- **Fixed with tests that fail without the fix:**
  - the P1: a queued salon write read the clock before the lock;
  - refusal tasks passed on unrelated errors (error matcher, 3 new controls);
  - the diff hid edits (`unexpected`);
  - empty env values became defaults;
  - `--fhir-url=URL` stopped parsing.
- **Narrowed, not redesigned:** the cross-domain token claim. The kit README states the limit and a test pins it.
  Owner, 2026-10-20: no domain field now. It is closed in Part 9 by audience binding (below).

## Part 9 definition of done
Scope is in `comms/PLAN.md` (audit log, OAuth 2.1 expiry and revocation, encryption at rest,
data minimisation, all in the kit). Added by the owner, 2026-10-20:
- **Audience binding:** every token (JWT and static) names its audience, and the server rejects
  tokens for another audience. Static tokens files get a versioned format with audience, expiry
  and revocation in one migration. Files without a version are refused with a message naming the
  fix. This closes the Part 8 Codex P2 on cross-domain unbound tokens.

## Part 9 (done, PR #20 merged 2026-10-21)
Built on branch `claude/happy-lovelace-rdq5e6` (the branch this session was assigned). The owner's
"start the next part" on 2026-10-20 was the go. Everything is in `mcp_ops_kit`, so the clinic and
the salon both have it with no domain-specific code.
- **Tokens file format 2, with audience binding** (`auth.py`). Every token has `audience`,
  `expires_at` and an optional `revoked_at`. The verifier re-reads the file when it changes,
  so `revoke` works on a running server with no restart. A broken file refuses every token.
  An unversioned file is refused with the `migrate` command. A file holding another
  audience's token is refused. This closes the Part 8 Codex P2.
- **OAuth 2.1 resource server** (`oauth.py`). RS256/ES256 JWTs checked against a JWKS from a
  file or URL, with caching and rotation; iss, aud, exp, nbf, sub, client_id, jti and iat are
  checked, and revoked `jti` values come from a file re-read on change. A server can take
  static tokens, JWTs or both.
- **Audit log** (`audit.py`). One record per tool call, never the arguments. Each record is
  AES-256-GCM encrypted and bound to its sequence number, with an HMAC chain across records.
  `verify` and `show` CLIs. If a record cannot be written, the server stops acting.
- **Encryption at rest** (`sqlite.py`, `crypto.py`). A SQLite file holds only ciphertext and
  is re-encrypted atomically on every committed write. If a save fails, the write is undone.
  A plaintext file is refused. One data key (`<PREFIX>DATA_KEY`) with HKDF subkeys.
- **Data minimisation** (`tests/test_minimisation.py`). Every tool's fields are pinned with a
  reason. `OpsServer` rebuilds the text content from the validated output (FAILURES.md: the
  agent could read undeclared fields).
- **Fail-closed start-up.** No server starts without a token source, an audit log and a data key.
  The Docker image now needs `MCP_OPS_DATA_KEY`, and writes its audit log to `/audit`.
- One new dependency, `cryptography`: AES-GCM and HKDF, plus the RSA/EC signatures PyJWT
  needs. PyJWT itself was already required by `mcp`.
- **Local results:**
  - `pytest -q`: 521 passed. This includes every clinic tool test on encrypted SQLite
    too, and the salon tool tests in memory and in an encrypted file.
  - `sqlite,fhir` against HAPI: 599 passed.
  - Eval gates: clinic 31/31, 12/12 on SQLite and on FHIR; salon 18/18, 14/14.
  - Each eval task's audit chain is verified.
  - The Docker image was built from the real context, and every CI `container` step was
    replayed locally.

CI on PR #20 (head e461104): every job green on the push and pull_request runs (lint, test,
evals, fhir, container, secrets). Cursor Bugbot found 3 defects and Cursor Security 1, all
reproduced, fixed with tests that fail without the fix, and logged in FAILURES.md; both bots
pass on the final head.

Codex review (of a378fd0, 2026-10-21): 6x P1, 3x P2, each checked against the current head first.
- **Already fixed by later commits:** 3 (JWKS stale keys, save after rename, CI key).
- **Fixed now, each with a test that fails without the fix:** 5:
  - a deleted revocation file un-revoked tokens;
  - truncated JWT client IDs merged principals;
  - a deleted or replaced audit log did not stop the server;
  - an in-memory store could spill temp tables to `/tmp`;
  - returned error results were audited as `ok`.
- **Documented, not changed:** 1. The encrypted save runs after COMMIT, so it is outside the deadline; failing the call would claim a rollback that never happened.

Codex prompt for Part 9:
```
Review PR #20 in nikhilll30/mcp-ops-server (branch claude/happy-lovelace-rdq5e6) as a senior
engineer. Focus: token verification (audience, expiry on wall-clock time, revocation and the
file re-read on change, fail-closed behaviour on a broken file, migrate), the JWT verifier
(algorithm confusion, kid handling, JWKS caching and rotation, required claims, subject binding),
the audit log (what it records and never records, the chain and what it cannot detect, behaviour
when a write fails, the outcome classification), encryption at rest for SQLite (the save-after-
commit path, rollback to the last saved image, locking, plaintext refusal, temp files), the text
content rebuilt from validated output, and anything that weakens a Part 1-8 guarantee or behaves
differently between SQLite and FHIR. Also check test quality and anything that would embarrass this
repo once public. List findings as P0/P1/P2 with file and line. Do not change code.
```

Owner, 2026-10-21: merged PR #20. The final head (106a11d) was green on every CI job (push and
pull_request) and on both Cursor reviewers, with all review threads resolved. That head also
carries one later Bugbot fix: the JWT revocation file must exist at start-up.

## Part 10 (in review)
Built on branch `claude/nifty-ptolemy-l6whuk` (the branch this session was assigned). The owner's
"start the next part" on 2026-10-21 was the go. Everything is in `infra/aws/` (README there).
- **The blueprint:** one Fargate task in private subnets (image by digest from an immutable,
  scanned ECR repo; uid 10001, read-only root, no capabilities, no ECS Exec, no public IP),
  an internal ALB with TLS 1.2+ behind a WAF, EFS access points for tokens (read-only to the
  server), the audit log and SQLite, the data key in Secrets Manager (value set out of band,
  never in state), two rotated KMS keys, VPC endpoints, flow/WAF/ALB/container logs kept 400+
  days. No internet gateway, NAT or Elastic IP. IAM: the server cannot write its own tokens
  file; the admin task cannot write the audit log.
- **CI `terraform` job** (new; no AWS credentials anywhere): fmt, init against the committed
  lock file, validate, `terraform test` (17 runs on a mocked provider), tflint with the AWS
  ruleset, checkov (196 passed, 8 skips each with its reason), and `scripts/check_infra.py`.
  Terraform and tflint are pinned by version and SHA-256. `publish` now also needs `terraform`.
- **`scripts/check_infra.py`** (15 pytest tests): every resource type maps to a service in
  `infra/aws/hipaa-services.json` (HIPAA-eligible, or no PHI with a reason); forbidden types
  (IGW, NAT, EIP, secret values, IAM users) and `0.0.0.0/0` fail; `MCP_OPS_*` settings must be
  in `.env.example`; the uid must match the Dockerfile.
- **The `container` job** now runs the image with `--read-only --cap-drop ALL
  --security-opt no-new-privileges`, as the task definition does.
- **HIPAA eligibility was checked through web search only** (aws.amazon.com is blocked from
  this session). The catalog marks each service `official`, `secondary` or `unverified` (S3).
  Owner: check it against the live AWS page.
- **Local results:** `pytest -q` 536 passed; `terraform test` 17/17; tflint clean; checkov 0
  failed; `check_infra.py` ok; the image built and run with the task's hardening (smoke test,
  audit verify, revoke from an admin-style container, all passed); CI's `container` steps
  replayed from the workflow text.
- **No new Python dependency.** checkov is installed only inside the CI job, pinned.

## Next action
Owner: wait for CI, paste the Codex prompt below, then merge and add `terraform` to the `main`
ruleset's required checks. Part 11 starts after the owner's "go".

Codex prompt for Part 10:
```
Review PR #22 in nikhilll30/mcp-ops-server (branch claude/nifty-ptolemy-l6whuk) as a senior
engineer. Focus: infra/aws as an AWS deployment of the clinic server: IAM least privilege (task
vs execution vs admin roles, the EFS access-point conditions and file system policy), whether
the VPC truly has no internet path and the endpoints and security groups are enough for a
Fargate task to start, KMS key policies (CloudWatch Logs, Secrets Manager, ECR, EFS), the
single-task deployment and the audit log's exclusive lock on EFS, the data key never entering
state, settings the server would reject or misread (MCP_OPS_NOW, empty values, JWT files),
whether the terraform tests and scripts/check_infra.py could pass while a safeguard is broken,
the checkov skips and their reasons, the CI job's supply chain (pinned binaries, lock file,
tflint plugin), and any wording that claims compliance rather than HIPAA-ready. Also check test
quality and anything that would embarrass this repo once public. List findings as P0/P1/P2 with
file and line. Do not change code.
```
