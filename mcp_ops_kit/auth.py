"""Bearer-token auth: a file of hashed tokens, a verifier for the SDK, and per-tool checks.

A domain describes its tokens with one ``AuthConfig``: its scope names and its *subject*,
the record a token may be bound to (the clinic binds to a patient, the salon to a customer).
Raw tokens are never stored: the tokens file holds SHA-256 hashes, so leaking it does not
leak credentials. In the file, a bound token carries the subject under the domain's own
claim name (``"patient_id": "PAT-0008"``), and that is the claim the verified token carries.
"""

import argparse
import hashlib
import json
import math
import re
import secrets
import sys
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any, ClassVar

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mcp_ops_kit.limits import RateLimiter


@dataclass(frozen=True)
class AuthConfig:
    """What a domain's tokens can say.

    ``scopes``: every scope name the domain's tools check. ``subject_claim``: the claim
    (and tokens-file key) that binds a token to one subject, e.g. ``patient_id``.
    ``subject_pattern``: a full-match regex for subject IDs. ``subject_label``: the word
    used in messages and in the CLI flag (``--patient``). ``cli_prog``: how the domain's
    token CLI is invoked, for help text and start-up hints.
    """

    scopes: tuple[str, ...]
    subject_claim: str
    subject_pattern: str
    subject_label: str
    cli_prog: str

    def __post_init__(self) -> None:
        if not self.scopes or len(set(self.scopes)) != len(self.scopes):
            raise ValueError("scopes must be non-empty and unique")
        if self.subject_claim in {"client_id", "sha256", "scopes", "subject"}:
            raise ValueError(f"subject_claim {self.subject_claim!r} collides with a token field")
        re.compile(self.subject_pattern)

    @cached_property
    def token_entry(self) -> type["TokenEntry"]:
        """The tokens-file entry model for this domain (validates scopes and subject IDs)."""
        name = f"{self.subject_label.title()}TokenEntry"
        return type(name, (TokenEntry,), {"__module__": __name__, "config": self})

    @cached_property
    def verifier(self) -> type["StaticTokenVerifier"]:
        """A ``StaticTokenVerifier`` whose ``from_file`` reads this domain's tokens file."""
        return type("StaticTokenVerifier", (StaticTokenVerifier,), {"config": self})

    def issue_hint(self) -> str:
        scopes = " ".join(f"--scope {s}" for s in self.scopes)
        return f"{self.cli_prog} issue --file tokens.json --client dev {scopes}"


class TokenEntry(BaseModel):
    """One tokens-file entry. Use ``AuthConfig.token_entry``, which knows the domain's scopes
    and subject claim; unknown keys are refused, so a token file for another domain (or a
    typo in the subject claim) fails to load instead of yielding an unbound token."""

    model_config = ConfigDict(extra="forbid")
    config: ClassVar[AuthConfig]

    client_id: str = Field(min_length=1, max_length=64)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scopes: list[str]
    subject: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _subject_from_claim(cls, data: Any) -> Any:  # noqa: ANN401 - pydantic hook
        if isinstance(data, dict):
            if "subject" in data:
                raise ValueError(f"use {cls.config.subject_claim!r}, not 'subject'")
            if cls.config.subject_claim in data:
                data = dict(data)
                data["subject"] = data.pop(cls.config.subject_claim)
        return data

    @field_validator("scopes")
    @classmethod
    def _known_scopes(cls, scopes: list[str]) -> list[str]:
        unknown = sorted(set(scopes) - set(cls.config.scopes))
        if unknown:
            raise ValueError(f"unknown scopes {unknown}; known: {list(cls.config.scopes)}")
        return scopes

    @field_validator("subject")
    @classmethod
    def _subject_id(cls, subject: str | None) -> str | None:
        if subject is not None and not re.fullmatch(cls.config.subject_pattern, subject):
            raise ValueError(f"{cls.config.subject_claim} must match {cls.config.subject_pattern}")
        return subject

    def to_file(self) -> dict[str, Any]:
        out: dict[str, Any] = {"client_id": self.client_id, "sha256": self.sha256}
        out["scopes"] = list(self.scopes)
        if self.subject is not None:
            out[self.config.subject_claim] = self.subject
        return out


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def load_tokens(config: AuthConfig, path: str) -> list[TokenEntry]:
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, dict) or set(raw) != {"tokens"} or not isinstance(raw["tokens"], list):
        raise ValueError(f'{path}: expected {{"tokens": [...]}}')
    return [config.token_entry.model_validate(e) for e in raw["tokens"]]


class StaticTokenVerifier:
    """Implements the SDK's ``TokenVerifier`` protocol over a fixed set of hashed tokens.

    Lookup is by hash, so response time does not depend on how much of a guess matches.
    """

    config: ClassVar[AuthConfig]

    def __init__(self, entries: list[TokenEntry]) -> None:
        self._by_hash = {e.sha256: e for e in entries}
        if len(self._by_hash) != len(entries):
            raise ValueError("Duplicate token hash in tokens file.")

    @classmethod
    def from_file(cls, path: str) -> "StaticTokenVerifier":
        return cls(load_tokens(cls.config, path))

    async def verify_token(self, token: str) -> AccessToken | None:
        entry = self._by_hash.get(hash_token(token))
        if entry is None:
            return None
        return AccessToken(
            token=token,
            client_id=entry.client_id,
            # The SDK ties a Streamable HTTP session to (client_id, issuer, subject). A
            # per-token subject keeps two tokens that share a client_id (e.g. two bound
            # tokens of one app) from using or deleting each other's sessions.
            subject=entry.sha256,
            scopes=list(entry.scopes),
            claims={entry.config.subject_claim: entry.subject} if entry.subject else None,
        )


class Guard:
    """Authorization and rate limiting, enforced in server code.

    ``rate_limit`` runs once per tool call before the SDK validates arguments (see
    ``server.OpsServer``), so every call counts: invalid arguments, refused scopes and
    ``health`` included. ``check`` runs inside each data tool: authenticated, has the scope,
    and (for a bound token) acts only for its own subject.
    """

    def __init__(self, config: AuthConfig, limiter: RateLimiter) -> None:
        self._config = config
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

    def check(self, scope: str, subject_id: str | None = None) -> AccessToken:
        if scope not in self._config.scopes:  # a typo in a tool must not become "no access"
            raise ValueError(f"unknown scope {scope!r}")
        token = get_access_token()
        if token is None:
            raise ToolError("Unauthenticated: this server requires a bearer token.")
        if scope not in token.scopes:
            raise ToolError(f"Forbidden: this token lacks the {scope} scope.")
        bound = (token.claims or {}).get(self._config.subject_claim)
        if bound is not None and subject_id is not None and subject_id != bound:
            raise ToolError(
                f"Forbidden: this token may only act for {self._config.subject_label} {bound}."
            )
        return token


def issue(
    config: AuthConfig, path: str, client_id: str, scopes: list[str], subject: str | None
) -> str:
    """Append a new token's hash to ``path`` and return the raw token (shown once)."""
    token = secrets.token_urlsafe(32)
    file = Path(path)
    entries = load_tokens(config, path) if file.exists() else []
    fields = {"client_id": client_id, "sha256": hash_token(token), "scopes": scopes}
    if subject is not None:
        fields[config.subject_claim] = subject
    entries.append(config.token_entry.model_validate(fields))
    file.write_text(json.dumps({"tokens": [e.to_file() for e in entries]}, indent=2) + "\n")
    return token


def main(config: AuthConfig, argv: list[str] | None = None) -> None:
    """The domain's token CLI: ``<cli_prog> issue --file F --client C --scope S [...]``."""
    parser = argparse.ArgumentParser(prog=config.cli_prog)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("issue", help="Create a token and add its hash to the tokens file.")
    p.add_argument("--file", required=True)
    p.add_argument("--client", required=True)
    p.add_argument("--scope", action="append", choices=config.scopes, required=True)
    p.add_argument(f"--{config.subject_label}", dest="subject", default=None)
    args = parser.parse_args(argv)
    try:
        token = issue(config, args.file, args.client, args.scope, args.subject)
    except PermissionError as exc:
        sys.exit(
            f"Cannot write {args.file}: {exc.strerror}. In Docker, run this command with "
            '--user "$(id -u):$(id -g)" so the file is written as you.'
        )
    print(token)  # noqa: T201 - the CLI's output is the token itself
