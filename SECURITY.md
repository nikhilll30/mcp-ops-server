# Security policy

This is a portfolio project: a **synthetic** clinic and salon, never real patient or
customer data. It is built to be HIPAA-ready (see
[`docs/hipaa-controls.md`](docs/hipaa-controls.md)), which is not the same as compliant.
Security reports are still welcome and taken seriously, because the code is meant to be
reused: the `mcp-ops-kit` package and the container image are published.

## Supported versions

| What | Supported |
|---|---|
| `main`, and the image `ghcr.io/nikhilll30/mcp-ops-server:latest` built from it | Yes |
| `mcp-ops-kit` on PyPI: the latest release | Yes |
| Older images (`sha-*` tags) and older kit releases | No: upgrade to the latest |

Fixes land on `main` and ship in the next image and kit release; there are no backports.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting: the **Security** tab of this repository, then
**Report a vulnerability**. Please do not open a public issue, pull request or discussion
for a vulnerability.

Include what you can of: the affected component (server, kit module, workflow or Terraform),
the version or commit, the steps to reproduce, and what an attacker gains. Use only
synthetic data in a report, never real personal data.

What to expect:
- an acknowledgement within 7 days;
- an assessment, and a fix or a decision with reasons, within 30 days for confirmed issues;
- credit in the fix's entry in [`FAILURES.md`](FAILURES.md) and in the advisory, unless you
  prefer not to be named.

## In scope

- Authentication and authorisation: tokens, scopes, subject and audience binding, JWTs.
- The audit log, encryption at rest, data minimisation, rate limits and deadlines.
- The image, the kit package, the CI workflows and their supply chain (signing, SBOM,
  pinned actions, trusted publishing).
- The Terraform blueprint in `infra/aws` (it is validated but never applied, so a finding
  there is a design flaw, not a live exposure).

The threat model is [`docs/threat-model.md`](docs/threat-model.md); its residual risks are
known and need no report, though better ideas for them are welcome.

## Out of scope

- Anything that needs real patient data, or a deployment this repo does not run (there is
  no hosted instance).
- How a language model behaves on its own. Prompt injection that makes an agent misuse
  the tools its token allows is a known residual risk (threat model E2), measured by
  the prompt-injection suite (`evals/README.md`). A way to make the *server* exceed a token's scopes or subject binding is in
  scope.
- Denial of service by volume against your own deployment.

## Verifying what you run

Images published from `main` are signed keyless with Sigstore and carry a signed SPDX SBOM:

```bash
scripts/supply_chain.sh verify ghcr.io/nikhilll30/mcp-ops-server@sha256:<digest>
```

This accepts only a signature made by this repository's `ci.yml` workflow on `main`.
`mcp-ops-kit` is uploaded to PyPI through trusted publishing with attestations, so PyPI
shows the workflow and commit that built each file.
