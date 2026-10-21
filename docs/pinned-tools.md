# Pinned tools and who updates them

Everything CI runs is pinned: actions by commit SHA, images by digest, binaries by version and
hash, Python tools by exact version. Dependabot (`.github/dependabot.yml`) opens update PRs
for the pins it can read. The rest are listed here and updated **by hand by the owner**, at
least once a month and at once on a security advisory for any of them.
`tests/test_workflows.py` fails if a workflow or the install script pins something that this
page does not list, so a new pin cannot slip past both.

## Updated by Dependabot

| What | Where it is pinned |
|---|---|
| GitHub Actions (every `uses:`) | `.github/workflows/*.yml`, by commit SHA with the version in a comment |
| Python dependencies of the image and the kit | `pyproject.toml`, `packaging/mcp-ops-kit/pyproject.toml` |
| CI's Python tools: build, twine, checkov | `.github/requirements/*/requirements.txt` |
| The image's base (`python:3.12-slim`) | `Dockerfile`, by digest |
| The Terraform AWS provider | `infra/aws/.terraform.lock.hcl` |

## Updated by hand

| Tool | Where it is pinned | Pinned as | How to update |
|---|---|---|---|
| hapi | `ci.yml`, the `fhir` job's service | `hapiproject/hapi` tag and digest | Pick a release tag, resolve its digest (`docker buildx imagetools inspect`), replace both; the `fhir` job must pass |
| registry | `ci.yml`, the `container` job's signing step | `registry:3` and digest | As above; the signing step must pass |
| gitleaks | `ci.yml`, the `secrets` job | `ghcr.io/gitleaks/gitleaks` tag and digest | As above; the `secrets` job must pass |
| cosign | `scripts/install_supply_chain_tools.sh` | `COSIGN_VERSION` | Bump the version; `go install` checks it against sum.golang.org, and the CI cache key changes with the script |
| syft | `scripts/install_supply_chain_tools.sh` | `SYFT_VERSION` | As for cosign |
| terraform | `ci.yml`, the `terraform` job | `TF_VERSION` and `TF_SHA256` | Take both from HashiCorp's release page (the `SHA256SUMS` file is signed) |
| tflint | `ci.yml`, the `terraform` job | `TFLINT_VERSION` and `TFLINT_SHA256` | Take both from the release's `checksums.txt` |
