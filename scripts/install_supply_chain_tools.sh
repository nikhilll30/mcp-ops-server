#!/usr/bin/env bash
# Installs cosign and syft at pinned versions into DIR (default ~/.local/bin).
#
#   scripts/install_supply_chain_tools.sh [DIR]
#
# Built from source with `go install`: Go checks every module it downloads against the
# public checksum database (sum.golang.org), so a tampered or re-tagged release fails the
# install. CI caches DIR by these versions; change a version here to rebuild.
set -euo pipefail

COSIGN_VERSION=v3.1.3
SYFT_VERSION=v1.54.0

DIR="${1:-$HOME/.local/bin}"
mkdir -p "$DIR"
export GOBIN="$DIR" GOFLAGS=-trimpath GOTOOLCHAIN=auto
[[ -x "$DIR/cosign" ]] || go install "github.com/sigstore/cosign/v3/cmd/cosign@${COSIGN_VERSION}"
[[ -x "$DIR/syft" ]] || go install "github.com/anchore/syft/cmd/syft@${SYFT_VERSION}"
# A cached binary must be the pinned version (go records the module version it built).
go version -m "$DIR/cosign" | grep -Eq "^\s+mod\s+github.com/sigstore/cosign/v3\s+${COSIGN_VERSION}\s" \
  || { echo "cosign in $DIR is not ${COSIGN_VERSION}" >&2; exit 1; }
go version -m "$DIR/syft" | grep -Eq "^\s+mod\s+github.com/anchore/syft\s+${SYFT_VERSION}\s" \
  || { echo "syft in $DIR is not ${SYFT_VERSION}" >&2; exit 1; }
