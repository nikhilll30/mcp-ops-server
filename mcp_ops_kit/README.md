# mcp-ops-kit

The domain-free half of this repo: what an MCP server needs to let agents act on a booking
system safely, plus the trajectory evals that gate it in CI. It was **extracted from two working
domains**, not designed up front. The synthetic clinic (`mcp_ops_server`) came first. Then a
synthetic salon (`salon_ops`) was built by copying the clinic's code. The kit is what the two
copies turned out to share (Part 8, see [`FAILURES.md`](../FAILURES.md)).

```bash
pip install mcp-ops-kit            # add [live] for the OpenAI agent used in live eval runs
```

The source lives in this repo, next to the two domains built on it. Releases are published to
PyPI from a `kit-vX.Y.Z` tag on `main` by trusted publishing, with attestations, after CI has
built the wheel and used it from a fresh venv (`release-kit.yml`).

## What the kit owns, and what a domain supplies

| Kit module | Gives you | Your domain supplies |
|---|---|---|
| `auth` | Hashed bearer tokens in a versioned file: each names its audience and expiry and can be revoked; the file is re-read on change, so revocation needs no restart. A token CLI (`issue`, `revoke`, `list`, `migrate`), scopes and a subject binding, all checked in server code (`Guard`) | An `AuthConfig`: scope names, the subject claim (`patient_id`, `customer_id`), its ID pattern, the default audience |
| `oauth` | OAuth 2.1 resource-server tokens: JWT access tokens (RFC 9068, RS256/ES256) checked against your authorization server's JWKS (file or URL), with a `jti` revocation file | Your issuer and JWKS location (settings) |
| `audit` | One encrypted, HMAC-chained record per tool call (who, token, tool, scope, subject ID, outcome, time), never the arguments; `verify` and `show` CLIs | Nothing |
| `crypto` | The data key (`new-key`), per-purpose subkeys, AES-256-GCM, atomic writes | The key, from your secret store |
| `limits` | A per-client token-bucket rate limit, applied before argument validation | Nothing |
| `server` | The audit-and-rate-limit-first `OpsServer`, which also rebuilds every result's text from the validated output, so an agent sees only declared fields. A `health` tool, `/mcp` with bearer auth, `/healthz`, env settings, a fail-closed `serve` (no token source, audit log or data key: no start) | Your tools (`register(server, guard)`), your settings fields, your env prefix |
| `sqlite` | One deadline per call: lock wait, busy wait and execution, with a late COMMIT rolled back. With a data key, the file on disk is encrypted (the database lives in memory; each committed write re-encrypts it atomically) | Your schema, seed and queries (subclass `SqliteStore`) |
| `errors` | `StoreError` and its kinds. Tools turn them into messages an agent can act on (`store_errors`) | Your own rule errors, as subclasses |
| `evals` | The harness (fresh data and a real server per task), the trajectory grader, the replay and OpenAI agents, the report and the CI gate. Repeated runs (`--repeats k`) scored with pass^k; a prompt-injection suite (`--suite injection`): planted text in records, an `injection_exposed` check, and attempted vs carried-out outcomes | An `EvalDomain`, golden tasks and negative controls; for injection, `supports_plant` and a `start_task` that writes `TaskSpec.plant` into the task's data |
| `smoke` | `/healthz`, 401 without a token, then an MCP session | Your tool round trip, with each write read back |

## Build a domain on it: the salon as the worked example

1. **Data and rules.** Write a deterministic synthetic seed (`salon_ops/seed.py`) and a store
   that subclasses `SqliteStore` (`salon_ops/store.py`). Enforce every rule in the store, and
   make the critical one a database constraint too. The salon keeps one booked visit per
   opening with a unique index.
2. **Auth.** One `AuthConfig` (`salon_ops/auth.py`): three scopes, tokens bound to
   `customer_id` by the pattern `CUS-[0-9]{4}`, and the audience `salon-ops`. The token CLI
   is `kit.main(AUTH)`.
3. **Tools.** Thin wrappers (`salon_ops/tools.py`). Each calls `guard.check(scope, subject_id)`
   and then the store, inside `store_errors()`, and returns a `TypedDict` so it gets an output
   schema.
4. **Server.** `kit.build_server(name, version, AUTH, register, audit=...)`, with
   `kit.build_verifier(settings, AUTH)` and `kit.open_audit(settings)` in your
   `build_http_app`, then `kit.build_http_app`, and `serve` in `__main__`
   (`salon_ops/server.py`, `salon_ops/__main__.py`). A database file goes through
   `require_key_for_file`, so it is never plaintext.
5. **Evals.** An `EvalDomain` (`salon_ops/evals/run.py`) says who the principals are, which
   argument names the subject (`SubjectRule`), the default clock, and how to get fresh data
   per task. Then write golden tasks and **negative controls for every grader check**
   (`salon_ops/evals/golden/make_tasks.py`) before you trust the gate. For a
   prompt-injection suite, set `supports_plant=True`, write each task's `TaskSpec.plant`
   (record ID -> text) into its fresh data in `start_task`, and add `injection.json` and
   `injection-negative.json` (the clinic's are in `evals/golden/`). A domain without
   `supports_plant` refuses a planted task, so an attack never runs on data it did not reach.
6. **Smoke test.** Your `exercise(session)` for `kit.smoke.main` (`salon_ops/smoke.py`).

The salon example has the following parts:
- 6 tools;
- 3 rules the clinic does not have: a stylist must offer the service, a 24-hour late-cancel
  window, and at most 3 upcoming visits per customer;
- 18 golden tasks and 14 negative controls, gated in CI next to the clinic's.

## Guarantees the kit enforces, with tests

- **The kit never imports a domain.** `tests/test_kit.py` walks every import in `mcp_ops_kit/`.
- **A bound token never loads unbound.** Unknown keys are refused, so a clinic token bound to
  a patient does not load in the salon as an unbound token with its `patient_id` silently
  dropped. Before the kit, the clinic's own loader ignored unknown keys in exactly this way.
  A token whose scopes another domain does not declare is refused too.
- **A token is for one server.** Every token names its audience (static tokens in the file,
  JWTs in `aud`). A tokens file holding another audience's token does not load, and the
  verifier checks the audience of each token again. This closes the Part 8 limit, where an
  unbound token loaded in any domain declaring the same scope names (`tests/test_kit.py`).
- **Tokens expire and can be revoked on a running server.** Static tokens by `revoke`, JWTs
  by `jti`; both files are re-read on change. If either file becomes invalid, every token it
  governs is refused until it is fixed, so a revocation is never skipped
  (`tests/test_tokens.py`, `tests/test_oauth.py`).
- **Every tool call is audited, or no call runs.** A record that cannot be written stops the
  server from acting (the call already made still returns its real result). An edited,
  deleted or reordered record fails `verify` at its line (`tests/test_audit.py`).
- **No plaintext database on disk.** A server refuses a database file without a key, and a
  plaintext SQLite file with one (`tests/test_encryption.py`).
- **An agent sees only declared fields** (`tests/test_minimisation.py`), each pinned with a reason.
- **The state check sees every change.** The record diff grades the booking lifecycle (booked,
  cancelled, booked-then-cancelled), and reports anything else (an edited, removed or
  odd-status record) as `unexpected`. That fails the task unless the task expects it.
- **A refusal can be pinned to its error.** A step pattern may carry `"error": {"$icontains": ...}`,
  so a refusal task fails when the agent hits some other error.
- **A scope a tool names but the domain never declared** raises an error. It is not quietly
  treated as "forbidden".
- Everything else is held to both domains' full suites and eval gates: the clinic's (on SQLite
  and on FHIR) and the salon's.

## Not in the kit (yet)

- The FHIR backend is clinic-specific by nature, and stays in `mcp_ops_server`. Encryption at
  rest for FHIR data is the FHIR server's storage, not this code.
- Key rotation: there is no re-encrypt command yet. A new data key means a fresh audit log
  and database file (keep the old key to read the old ones).
- Token introspection (RFC 7662) and a shared rate limit across replicas.
