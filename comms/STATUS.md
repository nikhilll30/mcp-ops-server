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
| 10 Terraform blueprint (HIPAA-eligible AWS, validated, never applied) | done (PR #22 merged) | docs/briefs/part-10-terraform.pdf |
| 11 Controls matrix, threat model, SECURITY.md, CodeQL, Dependabot, PyPI, signed images | done (PR #24 merged; kit 0.1.0 on PyPI) | docs/briefs/part-11-assurance.pdf |
| 12 Repeated live runs, pass^k, prompt-injection suite | done (PR #28 merged; kit 0.2.0 on PyPI; live results in) | docs/briefs/part-12-evals-at-scale.pdf |

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
| 010 | Terraform blueprint (infra/aws), CI terraform job, check_infra.py, hardened container run | Claude | merged | #22 |
| 011 | Controls matrix, threat model, SECURITY.md, CodeQL, Dependabot, pinned actions, kit packaging + release, signed images with an SBOM | Claude | merged | #24 |
| 012 | Repeated runs with pass^k, prompt-injection suite (SQLite + FHIR), live workflow inputs, kit 0.2.0 | Claude | merged | #28 |

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

## Part 10 (done, PR #22 merged 2026-10-21)
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
  lock file, validate, `terraform test` (21 runs on a mocked provider), tflint with the AWS
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
- **Local results:** `pytest -q` 547 passed; `terraform test` 21/21; tflint clean; checkov 0
  failed; `check_infra.py` ok; the image built and run with the task's hardening (smoke test,
  audit verify, revoke from an admin-style container, all passed); CI's `container` steps
  replayed from the workflow text.
- **No new Python dependency.** checkov is installed only inside the CI job, pinned.
- **Cursor Security (of ef652d0): 2 medium findings, both real and fixed.** WAF request
  sampling kept bearer tokens unredacted, so sampling is now off and a test pins it. The admin
  `issue` path printed tokens into CloudWatch, so the kit's `issue --sha256` registers a hash
  of a token the operator made, and a duplicate hash is refused. Details in FAILURES.md.
- **Cursor Bugbot (of ef652d0): 2 findings.** First deploy could never start: fixed with
  `start_service` (default false, zero tasks) and a second apply after bootstrap. WAF log
  delivery: fixed with a managed log resource policy; the claim that the KMS key policy also
  needs `delivery.logs.amazonaws.com` is pushed back (CloudWatch Logs encrypts; unverified
  here, and named in the Codex prompt).
- **Codex review (of 34e7e6c): 2x P1, 5x P2, all fixed** (FAILURES.md). Codex confirmed the
  WAF/KMS pushback. P1: a concurrent admin `issue` could undo a `revoke` (reproduced; the kit
  now locks the tokens file from read to write); the log writers lacked logs-key grants
  (added, scoped; accepted on Codex's citation of AWS docs). P2: tests now assert the rendered
  IAM/EFS policies (13 mutations, including Codex's 3, all caught); the checker refuses
  `.tf.json`/override files and assembled open CIDRs; Fargate is the cluster default and the
  README has a complete `run-task` helper; audience/JWKS outputs; rate limit must be a whole
  number >= 1.
- **Cursor Bugbot (of 7251173): 2 test holes, both fixed.** An empty-list `alltrue` let a
  deleted logs-key grant pass, and one mock ARN per type hid swapped roles and log groups.
  Each role and log group now has its own ARN in the tests, and each grant is asserted
  exactly. Both mutations passed the old tests (checked).

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
the checkov skips and their reasons, whether a KMS-encrypted WAF log group needs
delivery.logs.amazonaws.com in the key policy (I say no; unverified), the CI job's supply chain (pinned binaries, lock file,
tflint plugin), and any wording that claims compliance rather than HIPAA-ready. Also check test
quality and anything that would embarrass this repo once public. List findings as P0/P1/P2 with
file and line. Do not change code.
```

Owner, 2026-10-21: merged PR #22 and added `terraform` to the `main` ruleset's required checks.
Verified signed out: `GET /repos/nikhilll30/mcp-ops-server/rules/branches/main` lists the
required checks `container`, `evals`, `fhir`, `lint`, `secrets`, `terraform` and `test`; on the
merge commit (6ee5502) every CI job passed, `terraform` and `publish` included.

Open, owner's call (not blocking): check `infra/aws/hipaa-services.json` against AWS's live
HIPAA-eligible list. It was checked through web search only; Amazon S3 is `unverified`.

## Part 11 (done, PR #24 merged 2026-10-21)
Built on branch `claude/gifted-brown-k8ak51` (the branch this session was assigned). The owner's
"start the next part" on 2026-10-21 was the go.
- **Controls matrix** (`docs/hipaa-controls.md`): all 12 standards and specifications of
  164.312. Each has a status (`Addressed`/`Partly`/`Adopter`), the tests that prove it, and the
  adopter's part. **Threat model** (`docs/threat-model.md`): STRIDE over agent → server →
  backend, 17 threats, including prompt injection through tool results (E2, Partly; Part 12
  measures it). **`SECURITY.md`.** `scripts/check_docs.py` (CI `test` job) fails if a cited
  test or file disappears, a spec or STRIDE category is missing, or anything claims "HIPAA
  compliant".
- **mcp-ops-kit distribution:** `packaging/mcp-ops-kit/`, `scripts/build_kit.py`,
  `scripts/check_kit_dist.py`. A new CI `package` job builds the wheel, checks it and uses it
  from a fresh venv. `release-kit.yml` publishes on a `kit-vX.Y.Z` tag on `main` by trusted
  publishing.
- **Signed images:** `scripts/supply_chain.sh`. PRs use a throwaway key on a local registry,
  with must-fail cases. `main` signs keyless and verifies before `latest` moves.
- **CodeQL** (python, actions), **Dependabot** (pip, actions, docker, terraform), every action
  pinned by SHA, gitleaks and the test registry by digest (`tests/test_workflows.py`).
- **Found:** cosign v3's `verify` accepts an attestation-only image, and v3 dropped
  `--tlog-upload=false` (FAILURES.md).
- **Local results:** `pytest -q` 610 passed after the review fixes (594 before); `sqlite,fhir` collects 688. The signing step was
  replayed from the workflow text and passed. The kit wheel's smoke test passed in a fresh venv.
- **CI on the first push (decef40):** every job green, `package` and the new signing steps
  included.
- **Not testable before merge:** keyless signing (first run: the merge commit's `publish`
  job) and the PyPI upload (needs the owner's setup below).

Codex review (of 61a29f9, 2026-10-21): 2x P1, 7x P2, all checked against the code first, all
accepted (FAILURES.md).
- **P1 release boundary:** the workflow's own guards are tag-controlled. The boundary is now
  the owner's settings (step 1 below: a required reviewer and a tag ruleset, plus PyPI's
  environment binding).
- **P1 CI gate:** `scripts/check_release_ci.py` refuses a release unless every CI job and
  CodeQL passed on the tagged commit.
- **P2 fixes in code, each with a test that fails without it:** the server now refuses a
  non-local `http://` FHIR URL (reproduced: it started); the image has no setuid binaries,
  and CI scans for them; the workflow tests read code without comments and catch inherited
  grants (Codex's two mutations, and 4 more, are caught); every pin Dependabot cannot see is
  in `docs/pinned-tools.md`, and the pip pins moved to `.github/requirements/`.
- **P2 claims corrected:** integrity (rollback has exceptions, cited), audit (the failing
  call is served), database snapshot replay (reproduced: 129 appointments back to 128, now a
  residual), Fargate and `no-new-privileges`.

### Owner's steps for Part 11
Done by the owner, 2026-10-21 (reported by the owner; the release below exercised every one):
- Environment `pypi`: required reviewer nikhilll30 (self-review allowed), one deployment rule
  for tags `kit-v*`, no secrets or variables. "Allow administrators to bypass" is on.
- Tag ruleset `release-tags`: active, targets `kit-v*`, restricts creations, updates and
  deletions; bypass: Repository admin only. The `main` ruleset is unchanged.
- Private vulnerability reporting: enabled. CodeQL: already on advanced setup.
- PyPI pending publisher for `release-kit.yml`, environment `pypi` (first added with the
  environment blank, which PyPI shows as "Any"; removed and re-added with `pypi`).
- `package` added to the `main` ruleset's required checks: lint, test, evals, secrets,
  container, fhir, terraform, package.

1. **Before merging: the release boundary.** The release workflow's own checks (tag on
   `main`, CI green on the commit) run from the tag's copy of the file, so anyone who can push
   a tag could edit them out (Codex P1). These two settings are outside tag-controlled code:
   - Settings → Environments → New environment `pypi`. Under **Deployment branches and
     tags**, choose selected tags matching `kit-v*`. Under **Required reviewers**, add
     yourself (required, not optional): every PyPI upload then waits for your approval in
     the Actions UI, where you can check that the commit is on `main` and green.
   - Settings → Rules → Rulesets → New **tag** ruleset, target `kit-v*`. Restrict
     creations, updates and deletions, with only yourself (repository admin) on the bypass
     list. Then no one else can create a release tag, or move one after you approve it.
2. **Before merging:** Settings → Code security → turn on **Private vulnerability reporting**
   (SECURITY.md points at it). If CodeQL "default setup" is on, switch it to advanced, since
   this PR adds the workflow.
3. **PyPI:** at https://pypi.org/manage/account/publishing/, add a *pending* trusted
   publisher: project `mcp-ops-kit`, owner `nikhilll30`, repository `mcp-ops-server`,
   workflow `release-kit.yml`, environment `pypi`. (The name was free on 2026-10-21.)
   Do fill in the environment: PyPI then refuses a token from any job not running in `pypi`,
   so a modified workflow that drops the environment (and its reviewer) cannot publish.
4. **Merge PR #24.** Then check the merge commit's `publish` job (signed, verified). Add
   `package` (and CodeQL `analyze (python)`, `analyze (actions)` if wanted) to the ruleset's
   required checks.
5. **First kit release:** `git tag kit-v0.1.0 <merge commit> && git push origin kit-v0.1.0`.
   Then https://pypi.org/project/mcp-ops-kit/ should show 0.1.0 with attestations.
6. Optional check, signed out: `scripts/supply_chain.sh verify ghcr.io/nikhilll30/mcp-ops-server@<digest from the publish job>`.

### After the merge (verified by me, 2026-10-22)
- **Merge commit 0039c55:** CI run #161 green on all nine jobs, CodeQL green.
- **First keyless signature:** the `publish` job signed and attested
  `ghcr.io/nikhilll30/mcp-ops-server@sha256:c7bbc16ada962d0df8d084b1c2e01036a1084321d72087409d3ec7b484d68b4e`
  as `ci.yml@refs/heads/main`, verified it, then moved `latest` to that digest.
- **mcp-ops-kit 0.1.0 on PyPI** from the `kit-v0.1.0` release on 0039c55 (release run
  39709810441: build, then the owner's approval in `pypi`, then publish). PyPI's provenance
  API shows an attestation for both the wheel and the sdist, naming
  `nikhilll30/mcp-ops-server`, `release-kit.yml` and environment `pypi`. `pip install
  mcp-ops-kit==0.1.0` into a fresh venv passes `scripts/check_kit_dist.py smoke`.
- **Open (owner's call, not blocking):** a second Codex pass over the review fixes
  (b782b69 to fe8d3fa); the actions' Node 20 deprecation warnings, which Dependabot's
  update PRs will clear.

Codex prompt for Part 11:
```
Review PR #24 in nikhilll30/mcp-ops-server (branch claude/gifted-brown-k8ak51) as a senior
engineer. Focus: whether each row of docs/hipaa-controls.md is true (does every cited test
really prove the sentence beside it; is any status too generous; is anything the adopter's
presented as ours), the threat model's coverage (missing threats on the agent -> server ->
backend path, prompt injection through tool results, residuals understated), the signing
pipeline (scripts/supply_chain.sh: identity and issuer pinning, digest handling, whether
verify can pass for an image that is not signed by ci.yml on main, the PR key-mode test, the
cosign v3 attestation-as-signature behaviour), the release workflow (trusted publishing,
environment, tag-on-main check, whether a tag could publish code that CI never built), the kit
distribution (scripts/build_kit.py, check_kit_dist.py: missing files, dependency drift,
README links), workflow permissions and pinning (tests/test_workflows.py: what its text
checks could miss), and Dependabot coverage. Also check test quality and anything that would
embarrass this repo once public. List findings as P0/P1/P2 with file and line. Do not change
code.
```

## Part 12 (done, PR #28 merged 2026-10-22)
Built on branch `claude/zen-turing-1efa44` (the branch this session was assigned). The owner's
"start the next part" on 2026-10-22 was the go. Details: `evals/README.md` (sections "Repeated
runs and pass^k" and "Prompt-injection suite").
- **Repeated runs:** `--repeats k` (1 to 20). Every task runs k times on fresh data. The
  report gives pass^k (C(c, k) / C(n, k) per task, averaged), each run's score, and the tasks
  that failed at least once, with their pass rate and variance (flaky = passed some runs). A
  task passes only if every run did. Cost is summed over all runs.
- **Injection suite:** `--suite injection`, 6 tasks and 8 controls in `evals/golden/`
  (generated by `make_tasks.py`). Text is planted in a provider's name on SQLite and FHIR.
  The grader's new `injection_exposed` check requires that the agent saw it. Each run records
  `attempted` and `carried out`. In 3 attacks the server refuses the action, in 3 the token
  allows it. CI runs the suite on both backends.
- **Live workflow:** new inputs `suite` (choice) and `repeats`, passed through env (a test pins it).
- **Kit 0.2.0** (new public API). Release after merge: `git tag kit-v0.2.0 <merge commit>`.
- **Found while building** (FAILURES.md): FHIR reset did not restore provider names (fixed,
  test); an unexposed run would count as resisted (exposure check); mutation testing found an
  untested rule (test added); this session started from a stale checkout and nearly rebuilt
  Part 11 (caught before any code).
- **Local results:** `pytest -q` 647 passed. `sqlite,fhir` against HAPI: 728 passed. Eval
  gates on FHIR: 31/31, 12/12 and 6/6, 8/8 (after the Codex fixes). The kit wheel's smoke test passed in a fresh venv.
- **No new dependency.**

CI on PR #28: every job green on the push and pull_request runs (head 555708d). Cursor
Security: no findings. Cursor Bugbot: 1 finding, real and fixed. The live job's 30-minute limit
could not fit a repeated run, so it is now 360 minutes, with a test (FAILURES.md).

Codex review (of d9f2555, 2026-10-22): 1x P1, 4x P2, all reproduced and fixed, each with a
test that fails without it (FAILURES.md). Codex found the estimator, flaky rule and all-runs
gate correct.
- P1: a model cut off by its turn limit counted as exposed (results it never received). The
  agent now reports `undelivered`, and those results do not count.
- P2: a write whose response was lost was reported as stopped; it is now `unknown`.
- P2: a `referral_id: null` booking missed i04's attack; both forms match now (control `in08`).
- P2: FHIR planting accepted a non-seed Practitioner that reset never restores; refused now.
- P2: a grade cut short crashed the suite; that run is now recorded as failed and unexposed.
- After the fixes: `pytest -q` 656 passed; `sqlite,fhir` 737 passed; FHIR gates 31/31,
  12/12 and 6/6, 8/8.

Codex second review (of 9cbae0f, 2026-10-22): 5x P2 in the first fixes, all reproduced and
fixed (FAILURES.md). The attack outcome now comes from the calls' own answers (the backend's
"may or may not" error means unknown), not from the state diff, which closes 3 of them.
Undelivered results are counted as calls complete, so error paths are covered. Exposure is
computed independently of the grade. After the fixes: `pytest -q` 659 passed; `sqlite,fhir`
740 passed.

Codex prompt for Part 12:
```
Review PR #28 in nikhilll30/mcp-ops-server (branch claude/zen-turing-1efa44) as a senior
engineer. Focus: the pass^k estimator and how per-task runs, flaky tasks and the gate are
computed (mcp_ops_kit/evals/run.py: summarize, _consistency), whether a repeated run can gate
more loosely than a single one, the injection suite's validity (evals/golden/make_tasks.py
build_injection: are the attacks realistic, does each control follow the injection, could a
task pass without the agent ever seeing the planted text, is "attempted" vs "carried out"
computed correctly in grader.attack_outcome), planting on both backends (evals/run.py
plant_sqlite, fhir_seed.set_provider_names, and whether reset restores everything planted),
backend parity of the suite, the live-evals workflow's new inputs (injection, spend bounds),
and whether the docs claim more than six hand-written attacks can show. Also check test
quality and anything that would embarrass this repo once public. List findings as P0/P1/P2
with file and line. Do not change code.
```

### After the merge (2026-10-22)
- **Merge:** PR #28 was merged by the owner as merge commit 63edc7a. CI run 39778592735 passed all 9 jobs, publish included.
- **Release:** kit-v0.2.0, release run 39779287388. The owner approved the `pypi` deployment. **mcp-ops-kit 0.2.0 is on PyPI.**
- **Live runs:** 5 repeats each, on 63edc7a. Results are in `evals/README.md` and the README:
  - golden suite: 29/31 tasks passed every run for both models. Luna had pass^1 0.955 and Sol 0.961; pass^5 was 0.935 for both.
  - injection suite: 6/6 tasks and 30/30 runs for both models. No planted instruction was followed.
  - total cost: $2.95 (upper bound).
- **Checked by me:** the headline lines in the job logs. The per-task tables came from the reports, as extracted by the owner; every derived figure was recomputed and matched.

## Phase 2 is complete
All twelve parts are done. Open, owner's call (not blocking):
- **Reword the system prompt's "slot taken" rule.** The repeated runs confirm it: Sol stops after the clash in `f01` in 5 of 5 runs. Rewording it means re-running both models (about $2.60 for the golden suite at 5 repeats).
- **Add release notes for kit-v0.2.0** on the release page.
- Check `infra/aws/hipaa-services.json` against AWS's live list (from Part 10).
