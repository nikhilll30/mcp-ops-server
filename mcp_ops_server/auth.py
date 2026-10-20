"""The clinic's tokens: its scopes and the patient binding, over ``mcp_ops_kit.auth``.

Raw tokens are never stored; the tokens file holds SHA-256 hashes. Issue a token with:

    python -m mcp_ops_server.auth issue --file tokens.json --client front-desk \\
        --scope read:schedule --scope write:schedule --scope read:referrals

Add ``--patient PAT-0008`` to bind the token to one patient (a "patient portal" token).
"""

from typing import Literal

from mcp_ops_kit import auth as kit
from mcp_ops_kit.auth import Guard, hash_token

__all__ = ["ALL_SCOPES", "AUTH", "Guard", "Scope", "StaticTokenVerifier", "TokenEntry"]
__all__ += ["hash_token", "issue", "main"]

Scope = Literal["read:schedule", "write:schedule", "read:referrals"]
ALL_SCOPES: tuple[Scope, ...] = ("read:schedule", "write:schedule", "read:referrals")

AUTH = kit.AuthConfig(
    scopes=ALL_SCOPES,
    subject_claim="patient_id",
    subject_pattern=r"PAT-[0-9]{4}",
    subject_label="patient",
    cli_prog="python -m mcp_ops_server.auth",
)
TokenEntry = AUTH.token_entry
StaticTokenVerifier = AUTH.verifier


def issue(path: str, client_id: str, scopes: list[Scope], patient_id: str | None) -> str:
    """Append a new token's hash to ``path`` and return the raw token (shown once)."""
    return kit.issue(AUTH, path, client_id, list(scopes), patient_id)


def main(argv: list[str] | None = None) -> None:
    kit.main(AUTH, argv)


if __name__ == "__main__":
    main()
