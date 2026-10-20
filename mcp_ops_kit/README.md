# mcp-ops-kit

The domain-free half of this repo: what an MCP server needs to let agents act on a booking
system safely, plus the trajectory evals that gate it in CI. It was **extracted from two working
domains**, not designed up front. The synthetic clinic (`mcp_ops_server`) came first. Then a
synthetic salon (`salon_ops`) was built by copying the clinic's code. The kit is what the two
copies turned out to share (Part 8, see [`FAILURES.md`](../FAILURES.md)).

It lives in this repo for now. Publishing it to PyPI is Part 11.

## What the kit owns, and what a domain supplies

| Kit module | Gives you | Your domain supplies |
|---|---|---|
| `auth` | Hashed bearer tokens, a token CLI, scopes and a subject binding, all checked in server code (`Guard`) | An `AuthConfig`: scope names, the subject claim (`patient_id`, `customer_id`), its ID pattern |
| `limits` | A per-client token-bucket rate limit, applied before argument validation | Nothing |
| `server` | The rate-limit-first `OpsServer`, a `health` tool, `/mcp` with bearer auth, `/healthz`, env settings, a fail-closed `serve` | Your tools (`register(server, guard)`), your settings fields, your env prefix |
| `sqlite` | One deadline per call: lock wait, busy wait and execution, with a late COMMIT rolled back | Your schema, seed and queries (subclass `SqliteStore`) |
| `errors` | `StoreError` and its kinds. Tools turn them into messages an agent can act on (`store_errors`) | Your own rule errors, as subclasses |
| `evals` | The harness (fresh data and a real server per task), the trajectory grader, the replay and OpenAI agents, the report and the CI gate | An `EvalDomain`, golden tasks and negative controls |
| `smoke` | `/healthz`, 401 without a token, then an MCP session | Your tool round trip, with each write read back |

## Build a domain on it: the salon as the worked example

1. **Data and rules.** Write a deterministic synthetic seed (`salon_ops/seed.py`) and a store
   that subclasses `SqliteStore` (`salon_ops/store.py`). Enforce every rule in the store, and
   make the critical one a database constraint too. The salon keeps one booked visit per
   opening with a unique index.
2. **Auth.** One `AuthConfig` (`salon_ops/auth.py`): three scopes, and tokens bound to
   `customer_id` by the pattern `CUS-[0-9]{4}`. The token CLI is `kit.main(AUTH)`.
3. **Tools.** Thin wrappers (`salon_ops/tools.py`). Each calls `guard.check(scope, subject_id)`
   and then the store, inside `store_errors()`, and returns a `TypedDict` so it gets an output
   schema.
4. **Server.** `kit.build_server(name, version, AUTH, register)`, `kit.build_http_app`, and
   `serve` in `__main__` (`salon_ops/server.py`, `salon_ops/__main__.py`).
5. **Evals.** An `EvalDomain` (`salon_ops/evals/run.py`) says who the principals are, which
   argument names the subject (`SubjectRule`), the default clock, and how to get fresh data
   per task. Then write golden tasks and **negative controls for every grader check**
   (`salon_ops/evals/golden/make_tasks.py`) before you trust the gate.
6. **Smoke test.** Your `exercise(session)` for `kit.smoke.main` (`salon_ops/smoke.py`).

The salon example has the following parts:
- 6 tools;
- 3 rules the clinic does not have: a stylist must offer the service, a 24-hour late-cancel
  window, and at most 3 upcoming visits per customer;
- 18 golden tasks and 11 negative controls, gated in CI next to the clinic's.

## Guarantees the kit enforces, with tests

- **The kit never imports a domain.** `tests/test_kit.py` walks every import in `mcp_ops_kit/`.
- **A tokens file from another domain does not load.** Unknown keys are refused. Without that,
  a clinic token bound to a patient would load in the salon as an unbound token, its
  `patient_id` silently dropped. Before the kit, the clinic's own loader ignored unknown keys
  in exactly this way.
- **A scope a tool names but the domain never declared** raises an error. It is not quietly
  treated as "forbidden".
- Everything else is held to both domains' full suites and eval gates: the clinic's (on SQLite
  and on FHIR) and the salon's.

## Not in the kit (yet)

- The FHIR backend is clinic-specific by nature, and stays in `mcp_ops_server`.
- The audit log, OAuth 2.1 expiry and revocation, and encryption at rest are Part 9. They will
  be built in the kit, so both domains get them.
