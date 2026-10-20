"""The salon's tokens: its scopes and the customer binding, over ``mcp_ops_kit.auth``.

    python -m salon_ops.auth issue --file tokens.json --client front-desk \\
        --scope read:catalog --scope write:bookings --scope read:customers

Add ``--customer CUS-0005`` to bind the token to one customer (a "customer app" token).
"""

from typing import Any

from mcp_ops_kit import auth as kit

ALL_SCOPES = ("read:catalog", "write:bookings", "read:customers")

AUTH = kit.AuthConfig(
    scopes=ALL_SCOPES,
    subject_claim="customer_id",
    subject_pattern=r"CUS-[0-9]{4}",
    subject_label="customer",
    cli_prog="python -m salon_ops.auth",
    audience="salon-ops",
)


def issue(
    path: str, client_id: str, scopes: list[str], customer_id: str | None, **kwargs: Any
) -> str:
    """Add a new token's hash to ``path`` and return the raw token (shown once)."""
    return kit.issue(AUTH, path, client_id, scopes, customer_id, **kwargs)


if __name__ == "__main__":
    kit.main(AUTH)
