"""Bearer-token auth: a file of hashed tokens, a verifier for the SDK, and per-tool checks.

Raw tokens are never stored. The tokens file holds SHA-256 hashes, so leaking it does not
leak credentials. Issue a token with:

    python -m salon_ops.auth issue --file tokens.json --client front-desk \\
        --scope read:catalog --scope write:bookings --scope read:customers

Add ``--customer CUS-0005`` to bind the token to one customer (a "customer app" token).
"""

import argparse
import hashlib
import json
import math
import secrets
import sys
from pathlib import Path
from typing import Literal

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from salon_ops.limits import RateLimiter

Scope = Literal["read:catalog", "write:bookings", "read:customers"]
ALL_SCOPES: tuple[Scope, ...] = ("read:catalog", "write:bookings", "read:customers")


class TokenEntry(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scopes: list[Scope]
    customer_id: str | None = Field(default=None, pattern=r"^CUS-[0-9]{4}$")


class TokensFile(BaseModel):
    tokens: list[TokenEntry]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class StaticTokenVerifier:
    """Implements the SDK's ``TokenVerifier`` protocol over a fixed set of hashed tokens.

    Lookup is by hash, so response time does not depend on how much of a guess matches.
    """

    def __init__(self, entries: list[TokenEntry]) -> None:
        self._by_hash = {e.sha256: e for e in entries}
        if len(self._by_hash) != len(entries):
            raise ValueError("Duplicate token hash in tokens file.")

    @classmethod
    def from_file(cls, path: str) -> "StaticTokenVerifier":
        return cls(TokensFile.model_validate_json(Path(path).read_text()).tokens)

    async def verify_token(self, token: str) -> AccessToken | None:
        entry = self._by_hash.get(hash_token(token))
        if entry is None:
            return None
        return AccessToken(
            token=token,
            client_id=entry.client_id,
            # The SDK ties a Streamable HTTP session to (client_id, issuer, subject). A
            # per-token subject keeps two tokens that share a client_id (e.g. two customer
            # app tokens) from using or deleting each other's sessions.
            subject=entry.sha256,
            scopes=list(entry.scopes),
            claims={"customer_id": entry.customer_id} if entry.customer_id else None,
        )


class Guard:
    """Authorization and rate limiting, enforced in server code.

    ``rate_limit`` runs once per tool call before the SDK validates arguments (see
    ``server.OpsServer``), so every call counts: invalid arguments, refused scopes and
    ``health`` included. ``check`` runs inside each data tool: authenticated, has the scope,
    and (for a customer-bound token) acts only for that customer.
    """

    def __init__(self, limiter: RateLimiter) -> None:
        self._limiter = limiter

    def rate_limit(self) -> None:
        token = get_access_token()
        if token is None:
            return  # unauthenticated: never reaches a tool over HTTP; refused by check()
        retry_after = self._limiter.acquire(token.client_id)
        if retry_after is not None:
            raise ToolError(
                f"Rate limit exceeded for client {token.client_id}; "
                f"retry in {math.ceil(retry_after)}s."
            )

    def check(self, scope: Scope, customer_id: str | None = None) -> AccessToken:
        token = get_access_token()
        if token is None:
            raise ToolError("Unauthenticated: this server requires a bearer token.")
        if scope not in token.scopes:
            raise ToolError(f"Forbidden: this token lacks the {scope} scope.")
        bound = (token.claims or {}).get("customer_id")
        if bound is not None and customer_id is not None and customer_id != bound:
            raise ToolError(f"Forbidden: this token may only act for customer {bound}.")
        return token


def issue(path: str, client_id: str, scopes: list[Scope], customer_id: str | None) -> str:
    """Append a new token's hash to ``path`` and return the raw token (shown once)."""
    token = secrets.token_urlsafe(32)
    file = Path(path)
    data = (
        TokensFile.model_validate_json(file.read_text()) if file.exists() else TokensFile(tokens=[])
    )
    data.tokens.append(
        TokenEntry(
            client_id=client_id, sha256=hash_token(token), scopes=scopes, customer_id=customer_id
        )
    )
    file.write_text(json.dumps(data.model_dump(exclude_none=True), indent=2) + "\n")
    return token


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m salon_ops.auth")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("issue", help="Create a token and add its hash to the tokens file.")
    p.add_argument("--file", required=True)
    p.add_argument("--client", required=True)
    p.add_argument("--scope", action="append", choices=ALL_SCOPES, required=True)
    p.add_argument("--customer", default=None)
    args = parser.parse_args(argv)
    try:
        token = issue(args.file, args.client, args.scope, args.customer)
    except PermissionError as exc:
        sys.exit(
            f"Cannot write {args.file}: {exc.strerror}. In Docker, run this command with "
            '--user "$(id -u):$(id -g)" so the file is written as you.'
        )
    print(token)  # noqa: T201 - the CLI's output is the token itself


if __name__ == "__main__":
    main()
