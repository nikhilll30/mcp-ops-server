#!/usr/bin/env bash
# The image's SBOM, signature and SBOM attestation: made in CI, checked by anyone.
#
#   scripts/supply_chain.sh sbom   IMAGE@DIGEST OUT.spdx.json   # syft, then a content check
#   scripts/supply_chain.sh sign   IMAGE@DIGEST SBOM.spdx.json  # signature + SBOM attestation
#   scripts/supply_chain.sh verify IMAGE@DIGEST                 # both, or exit non-zero
#
# Two modes, chosen by the environment:
# - keyless (the default; CI's publish job on main): Sigstore signs with a short-lived
#   certificate for this repo's ci.yml on refs/heads/main, recorded in the public Rekor log.
#   `verify` accepts only that identity, so an image signed by any other workflow, branch
#   or repo fails, even if its signature is valid.
# - key (COSIGN_KEY / COSIGN_PUBLIC_KEY set; CI's container job on every PR): a throwaway
#   key pair and a local registry, no transparency log. It runs the same commands on the
#   same image, so the PR proves the plumbing that main then runs keyless.
#
# Always by digest: a tag can move after it was signed, a digest cannot.
# Needs cosign (v3) and syft on PATH; scripts/install_supply_chain_tools.sh installs both.
set -euo pipefail

REPO="${MCP_OPS_REPO:-nikhilll30/mcp-ops-server}"
IDENTITY="https://github.com/${REPO}/.github/workflows/ci.yml@refs/heads/main"
ISSUER="https://token.actions.githubusercontent.com"

die() { echo "supply_chain: $*" >&2; exit 1; }

by_digest() {
  [[ "$1" =~ @sha256:[0-9a-f]{64}$ ]] || die "$1 is not IMAGE@sha256:<digest>"
}

key_mode() { [[ -n "${COSIGN_KEY:-}${COSIGN_PUBLIC_KEY:-}" ]]; }

cmd="${1:-}"; shift || true
case "$cmd" in
  sbom)
    [[ $# -eq 2 ]] || die "usage: sbom IMAGE@DIGEST OUT"
    by_digest "$1"
    syft scan "registry:$1" --quiet -o "spdx-json=$2"
    # An SBOM that misses the Python packages would still be valid SPDX; refuse it.
    python3 - "$2" <<'EOF'
import json, sys
doc = json.load(open(sys.argv[1]))
names = {p.get("name", "").lower() for p in doc.get("packages", [])}
missing = {"mcp", "cryptography", "pyjwt", "mcp-ops-server", "python"} - names
if doc.get("spdxVersion", "").startswith("SPDX-") is False or missing:
    sys.exit(f"SBOM {sys.argv[1]} lacks {sorted(missing)} ({len(names)} packages)")
print(f"SBOM: {len(names)} packages, {doc['spdxVersion']}")
EOF
    ;;
  sign)
    [[ $# -eq 2 ]] || die "usage: sign IMAGE@DIGEST SBOM"
    by_digest "$1"
    if key_mode; then
      # cosign v3 replaced --tlog-upload=false with a signing config that names no log.
      config="$(mktemp)"; trap 'rm -f "$config"' EXIT
      cosign signing-config create --out "$config"
      cosign sign --yes --key "$COSIGN_KEY" --signing-config "$config" "$1"
      cosign attest --yes --key "$COSIGN_KEY" --signing-config "$config" \
        --type spdxjson --predicate "$2" "$1"
    else
      cosign sign --yes "$1"
      cosign attest --yes --type spdxjson --predicate "$2" "$1"
    fi
    ;;
  verify)
    [[ $# -eq 1 ]] || die "usage: verify IMAGE@DIGEST"
    by_digest "$1"
    if key_mode; then
      who=(--key "${COSIGN_PUBLIC_KEY:?COSIGN_PUBLIC_KEY is required in key mode}" --insecure-ignore-tlog=true)
    else
      who=(--certificate-identity "$IDENTITY" --certificate-oidc-issuer "$ISSUER")
    fi
    # cosign v3 `verify` also accepts the SBOM attestation as "a signature" (checked: an
    # image with only an attestation passes it). So require a payload of the image
    # signature type itself, for this exact digest.
    cosign verify "${who[@]}" "$1" | python3 -c '
import json, sys
digest = sys.argv[1].rsplit("@", 1)[1]
payloads = [p["critical"] for p in json.load(sys.stdin)]
if not any(p["type"] == "https://sigstore.dev/cosign/sign/v1"
           and p["image"]["docker-manifest-digest"] == digest for p in payloads):
    types = [p["type"] for p in payloads]
    sys.exit(f"no image signature for {digest}, only {types}")
' "$1"
    cosign verify-attestation "${who[@]}" --type spdxjson "$1" > /dev/null
    echo "verified: $1 is signed, with an SPDX SBOM attestation, by ${COSIGN_PUBLIC_KEY:-$IDENTITY}"
    ;;
  *)
    die "usage: $0 sbom|sign|verify ..."
    ;;
esac
