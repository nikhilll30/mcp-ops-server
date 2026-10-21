# Threat model (STRIDE)

What can go wrong on the path from an agent to the record system, what in this repo stops
it, and what is left over. It covers the clinic server and the kit it is built on; the salon
example shares every control in the kit. Every mitigation cites the test that fails if it
breaks; `scripts/check_docs.py` (CI `test` job) checks that those tests still exist.

## The system and its trust boundaries

```
 user ──> agent (LLM + MCP client) ──B1──> MCP server ──B2──> backend
                   ^                        |   |              (SQLite file or a FHIR R4
                   |  tool results (text    |   |               server over HTTPS)
                   |  an agent will read)   |   └──> audit log (encrypted, chained)
                   └────────────────────────┘
 operator ──B3──> tokens file, admin CLI          authorization server ──B4──> JWKS
 CI ──B5──> container image (GHCR), mcp-ops-kit (PyPI)
```

- **B1, agent to server:** the agent is not trusted. It may be wrong, confused, or steered
  by text it read (prompt injection). Every request carries a bearer token, and every rule
  is enforced by the server in code. Nothing depends on the model following instructions.
- **B2, server to backend:** the backend holds the records. Its data is trusted for
  correctness, but its free text is not trusted as instructions (see E2).
- **B3, operator:** whoever can write the tokens file can grant access. It is protected
  like a secret, and the server only reads it.
- **B4, authorization server:** trusted to sign JWTs. Its keys are fetched from a JWKS
  that is pinned by location.
- **B5, build and release:** what CI builds is what users run, checkable by signature.

## Threats

*Status:* `Mitigated` (a control stops it, with a test), `Partly` (a control limits it;
the rest is under *Residual*), `Open` (not handled yet; the plan or the adopter's part is
under *Residual*).

| ID | STRIDE | Where | Threat | Mitigation | Evidence | Status | Residual |
|---|---|---|---|---|---|---|---|
| S1 | Spoofing | B1 | A caller guesses or replays a static token. | 256-bit random tokens, stored only as SHA-256 hashes and looked up by hash. Each expires on wall-clock time and can be revoked on a running server. | `tests/test_auth.py::test_issue_stores_only_the_hash`<br>`tests/test_http_integration.py::test_missing_or_wrong_token_is_rejected_at_http_layer`<br>`tests/test_tokens.py::test_an_expired_token_is_refused_on_wall_clock_time`<br>`tests/test_tokens.py::test_revoke_takes_effect_on_the_next_request` | Partly | Tokens are bearer tokens: a stolen one works until it expires or is revoked. There is no sender binding (DPoP, mTLS). |
| S2 | Spoofing | B1, B4 | A forged JWT: `alg: none`, HMAC signed with the public key, or a key with a reused `kid`. | Only RS256 and ES256, and only with a JWKS key whose own algorithm matches. Issuer, audience, expiry and subject are required, and `nbf` is checked when present. | `tests/test_oauth.py::test_unsigned_and_hmac_tokens_are_refused`<br>`tests/test_oauth.py::test_a_token_from_another_key_with_the_same_kid_is_refused`<br>`tests/test_oauth.py::test_a_key_published_for_one_algorithm_is_not_used_for_another`<br>`tests/test_oauth.py::test_bad_claims_are_refused` | Mitigated | |
| S3 | Spoofing | B1 | A token issued for another server or domain is replayed here. | Every token names its audience. A tokens file holding another audience's token is refused at start-up, and each token's audience is checked again on use. | `tests/test_tokens.py::test_a_file_with_another_audiences_token_is_refused`<br>`tests/test_tokens.py::test_the_verifier_checks_audience_per_token_too`<br>`tests/test_kit.py::test_one_domains_tokens_file_does_not_load_in_another` | Mitigated | |
| S4 | Spoofing | B2, B4 | The server is pointed at, or redirected to, a server it should not trust. | The FHIR URL and a JWKS URL must be `https://` (local HTTP only for tests). FHIR paging links to another server are refused. | `tests/test_fhir.py::test_search_refuses_a_next_link_to_another_server`<br>`tests/test_oauth.py::test_jwks_locations_must_be_https_local_http_or_a_file`<br>`infra/aws/tests/validation.tftest.hcl::refuses_plain_http_to_fhir` | Mitigated | Whoever controls the server's settings controls what it trusts; settings are deployment secrets. |
| T1 | Tampering | B2 | Someone with disk access edits the SQLite file or the audit log. | Both are AES-256-GCM: a changed file fails to decrypt and is refused. Audit records are chained by an HMAC and bound to their sequence numbers. | `tests/test_encryption.py::test_the_wrong_key_or_a_changed_file_is_refused`<br>`tests/test_audit.py::test_an_edited_record_is_detected_by_the_chain`<br>`tests/test_audit.py::test_tampering_is_detected_at_its_line` | Partly | Removing records from the end of the log is not detectable from the file alone. `verify` prints the last sequence and chain value for the operator to keep elsewhere. |
| T2 | Tampering | B3 | Someone adds a token to the tokens file, or the server is used to write one. | The file holds hashes only. In the blueprint the server's mount is read-only; only the admin task can write. A concurrent `issue` cannot undo a `revoke`. | `infra/aws/tests/blueprint.tftest.hcl::server_cannot_write_its_own_tokens`<br>`tests/test_tokens.py::test_a_concurrent_issue_cannot_undo_a_revocation` | Partly | Write access to the tokens file is the power to grant access: restrict it like a secret. |
| T3 | Tampering | B2 | Two agents race to book one slot, or a write half-completes. | A unique index (SQLite) and `If-Match` on the Slot (FHIR) give one winner. A write past its deadline is rolled back. A failed encrypted save undoes the write. | `tests/test_tools.py::test_concurrent_bookings_of_one_slot_yield_one_winner`<br>`tests/test_fhir.py::test_server_enforces_if_match_on_the_slot`<br>`tests/test_limits.py::test_write_past_deadline_is_rolled_back_not_committed`<br>`tests/test_encryption.py::test_a_failed_save_undoes_the_write` | Mitigated | |
| T4 | Tampering | B5 | A tampered image, action or package reaches users. | Images are signed keyless by `ci.yml` on `main`, with a signed SBOM, and verified before `latest` moves. Every action is pinned by commit, and every image a workflow runs by digest. The kit goes to PyPI by trusted publishing, from a tag on `main`, with attestations. CodeQL and Dependabot run on the code. | `scripts/supply_chain.sh`<br>`tests/test_workflows.py::test_the_publish_job_signs_what_it_pushed_and_verifies_it`<br>`tests/test_workflows.py::test_every_action_is_pinned_to_a_commit`<br>`tests/test_workflows.py::test_every_image_a_workflow_runs_is_pinned_by_digest`<br>`tests/test_workflows.py::test_only_the_publishing_jobs_can_sign_or_write_packages` | Partly | A user must verify the signature before running the image. The Python dependencies installed into the image are pinned by range, not by hash. |
| R1 | Repudiation | B1 | A client denies having made a call, or a call leaves no trace. | One audit record per call: the client, the token (hash prefix), tool, scope, subject and outcome, including refused, failed and cancelled calls. If a record cannot be written, the server stops acting. | `tests/test_audit.py::test_each_outcome_is_recorded`<br>`tests/test_audit.py::test_a_cancelled_call_is_still_recorded_with_an_outcome`<br>`tests/test_audit.py::test_if_a_record_cannot_be_written_the_server_stops_acting`<br>`tests/test_http_integration.py::test_every_call_is_audited_without_its_arguments` | Mitigated | Records identify the client. Tying a client to a person is the authorization server's job. |
| I1 | Information disclosure | B1 | A tool returns more about a patient than the task needs, or a store adds a field. | Every tool's fields are pinned with a reason. The text an agent reads is rebuilt from the validated output, so an undeclared field never reaches it. No tool returns a patient's name or contact details. | `tests/test_minimisation.py::test_clinic_responses_carry_only_the_pinned_fields`<br>`tests/test_minimisation.py::test_an_undeclared_field_from_a_store_never_reaches_the_agent` | Mitigated | |
| I2 | Information disclosure | B1 | A patient-bound token reads or cancels another patient's records, or probes which IDs exist. | The subject binding is checked in every tool. Another patient's appointment looks the same as one that does not exist. | `tests/test_auth.py::test_patient_bound_token_cannot_act_for_others`<br>`tests/test_tools.py::test_cancel_someone_elses_appointment_looks_like_not_found` | Mitigated | |
| I3 | Information disclosure | B1, B3 | Secrets or PHI leak through logs, errors or the health probe. | The audit log never records arguments or results. Backend error text is never echoed to the agent. `/healthz` reveals nothing. An admin registers a token's hash, so the token never reaches the admin task's logs. | `tests/test_audit.py::test_arguments_and_results_never_reach_the_log`<br>`tests/test_fhir.py::test_server_error_text_is_not_echoed_to_the_agent`<br>`tests/test_http_integration.py::test_healthz_needs_no_token_and_reveals_no_data`<br>`tests/test_tokens.py::test_issue_sha256_registers_a_token_made_elsewhere` | Mitigated | |
| D1 | Denial of service | B1, B2 | A client floods the server, sends huge bodies, or a slow backend ties it up. | A per-client rate limit, applied before argument validation. A body size limit. One deadline per call over the lock, the database and FHIR, which a slowly dribbled response cannot stretch. | `tests/test_auth.py::test_rate_limit_is_per_client`<br>`tests/test_http_integration.py::test_oversized_body_is_rejected`<br>`tests/test_limits.py::test_runaway_read_is_interrupted_and_store_recovers`<br>`tests/test_fhir.py::test_a_dribbled_response_cannot_stretch_the_deadline` | Partly | The rate limit is per process (README, Known limits). The blueprint adds a WAF rate rule in front. |
| D2 | Denial of service | B2 | The audit log fills its disk or becomes unwritable. | By design the server then stops acting: no unaudited call is ever served. | `tests/test_audit.py::test_if_a_record_cannot_be_written_the_server_stops_acting`<br>`tests/test_audit.py::test_a_log_deleted_or_replaced_while_open_stops_the_server` | Partly | This trades availability for accountability. Monitor the audit volume. |
| E1 | Elevation of privilege | B1 | An agent calls a tool its token's scopes do not allow. | Each tool checks its scope in server code before doing anything; a refused call has no side effect. | `tests/test_auth.py::test_every_tool_requires_its_scope`<br>`tests/test_auth.py::test_rejected_call_has_no_side_effect` | Mitigated | |
| E2 | Elevation of privilege | B1, B2 | **Prompt injection through tool results:** text the backend returns (a provider's name or location from a real FHIR system) tells the agent to "cancel all appointments", and the agent does so with its own token. | The server cannot make a model ignore text, so it bounds what any instruction can achieve. Scopes and the subject binding are enforced in code, so a patient-bound token reaches one patient's records whatever the agent is told. Each write acts on one ID per call, under a per-client rate limit, and every call is audited. Arguments must match strict patterns, so injected text cannot become a query. | `tests/test_auth.py::test_patient_bound_token_cannot_act_for_others`<br>`tests/test_auth.py::test_rate_limit_is_per_client`<br>`tests/test_tools.py::test_ids_reject_non_ascii_digits`<br>`tests/test_audit.py::test_each_outcome_is_recorded` | Partly | An agent whose token may write for every patient can still be steered into cancelling what that token can reach. How often models follow planted instructions is not measured yet: that is Part 12's prompt-injection suite (instructions in synthetic tool results, graded by the trajectory grader). Give agents the narrowest token their task needs. |
| E3 | Elevation of privilege | B2 | A compromised server process escalates on its host. | In the image and the blueprint: a non-root uid, a read-only root file system, no Linux capabilities, no privilege escalation, no ECS Exec, no public IP. CI runs the image with the same flags. | `infra/aws/tests/blueprint.tftest.hcl::task_runs_hardened`<br>`infra/aws/tests/blueprint.tftest.hcl::private_network_only`<br>`Dockerfile` | Mitigated | |

## Out of scope

- The agent's own host, the model provider, and the user's device.
- The FHIR system's internals and its own access controls (it sees the server as one client).
- Physical and administrative safeguards: see [`hipaa-controls.md`](hipaa-controls.md).

To report a weakness in any of the above that this repo does control, see
[`SECURITY.md`](../SECURITY.md).
