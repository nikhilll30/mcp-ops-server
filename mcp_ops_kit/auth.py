"""Bearer-token auth: a versioned file of hashed tokens, a verifier for the SDK, and per-tool
checks.

A domain describes its tokens with one ``AuthConfig``: its scope names, its *subject* (the
record a token may be bound to: the clinic binds to a patient, the salon to a customer) and
its default *audience* (the server a token is for). Raw tokens are never stored: the tokens
file holds SHA-256 hashes, so leaking it does not leak credentials.

The tokens file (format 2) names its version, and every token names its audience, when it
expires, and when it was revoked, if it was::

    {"version": 2, "tokens": [{"client_id": "portal", "sha256": "<hex>",
      "scopes": ["read:schedule"], "audience": "mcp-ops-server",
      "expires_at": "2027-02-01T00:00:00Z", "patient_id": "PAT-0008"}]}

A bound token carries its subject under the domain's own claim name (``patient_id``), and
that is the claim the verified token carries. The verifier re-reads the file when it
changes, so ``revoke`` takes effect on the next request, with no restart. A file without a
version (format 1, before Part 9) is refused with the ``migrate`` command that fixes it.
"""

import argparse
import hashlib
import json
import logging
import math
import os
import re
import secrets
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cached_property
from pathlib import Path
from typing import Any, ClassVar

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mcp_ops_kit import audit
from mcp_ops_kit.crypto import atomic_write
from mcp_ops_kit.errors import ConfigError
from mcp_ops_kit.limits import RateLimiter

logger = logging.getLogger(__name__)

TOKENS_FILE_VERSION = 2
DEFAULT_LIFETIME_DAYS = 90
TOKEN_ID_CHARS = 12  # a token's public ID: the first 12 hex digits of its hash


@dataclass(frozen=True)
class AuthConfig:
    """What a domain's tokens can say.

    ``scopes``: every scope name the domain's tools check. ``subject_claim``: the claim
    (and tokens-file key) that binds a token to one subject, e.g. ``patient_id``.
    ``subject_pattern``: a full-match regex for subject IDs. ``subject_label``: the word
    used in messages and in the CLI flag (``--patient``). ``cli_prog``: how the domain's
    token CLI is invoked, for help text and start-up hints. ``audience``: the audience the
    domain's server accepts unless its settings name another (``<PREFIX>AUDIENCE``).
    """

    scopes: tuple[str, ...]
    subject_claim: str
    subject_pattern: str
    subject_label: str
    cli_prog: str
    audience: str

    def __post_init__(self) -> None:
        if not self.scopes or len(set(self.scopes)) != len(self.scopes):
            raise ValueError("scopes must be non-empty and unique")
        reserved = {"client_id", "sha256", "scopes", "subject", "audience", "expires_at"}
        reserved |= {"revoked_at", "iss", "aud", "sub", "exp", "scope", "jti"}
        if self.subject_claim in reserved:
            raise ValueError(f"subject_claim {self.subject_claim!r} collides with a token field")
        if not self.audience:
            raise ValueError("audience must be non-empty")
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

    def is_subject(self, value: object) -> bool:
        return isinstance(value, str) and re.fullmatch(self.subject_pattern, value) is not None

    def issue_hint(self) -> str:
        scopes = " ".join(f"--scope {s}" for s in self.scopes)
        return f"{self.cli_prog} issue --file tokens.json --client dev {scopes}"


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("must include a UTC offset, e.g. 2027-02-01T00:00:00Z")
    return value


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class TokenEntry(BaseModel):
    """One tokens-file entry. Use ``AuthConfig.token_entry``, which knows the domain's scopes
    and subject claim; unknown keys are refused, so a token file for another domain (or a
    typo in the subject claim) fails to load instead of yielding an unbound token."""

    model_config = ConfigDict(extra="forbid")
    config: ClassVar[AuthConfig]

    client_id: str = Field(min_length=1, max_length=64)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scopes: list[str]
    audience: str = Field(min_length=1, max_length=200)
    expires_at: datetime
    revoked_at: datetime | None = None
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
        if subject is not None and not cls.config.is_subject(subject):
            raise ValueError(f"{cls.config.subject_claim} must match {cls.config.subject_pattern}")
        return subject

    _expires_aware = field_validator("expires_at")(_aware)
    _revoked_aware = field_validator("revoked_at")(_aware)

    @property
    def token_id(self) -> str:
        return self.sha256[:TOKEN_ID_CHARS]

    def state(self, now: float) -> str:
        if self.revoked_at is not None:
            return "revoked"
        return "expired" if self.expires_at.timestamp() <= now else "active"

    def to_file(self) -> dict[str, Any]:
        out: dict[str, Any] = {"client_id": self.client_id, "sha256": self.sha256}
        out["scopes"] = list(self.scopes)
        out["audience"] = self.audience
        out["expires_at"] = _utc_text(self.expires_at)
        if self.revoked_at is not None:
            out["revoked_at"] = _utc_text(self.revoked_at)
        if self.subject is not None:
            out[self.config.subject_claim] = self.subject
        return out


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def migrate_hint(config: AuthConfig, path: str) -> str:
    return (
        f"{path} has no version: it is a format-1 tokens file, from before tokens named an "
        f"audience and an expiry. Migrate it once (every token keeps working until it "
        f"expires): {config.cli_prog} migrate --file {path} --audience {config.audience} "
        f"--expires-in-days {DEFAULT_LIFETIME_DAYS}"
    )


def _read_json(path: str) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, dict) or not isinstance(raw.get("tokens"), list):
        raise ConfigError(f'{path}: expected {{"version": 2, "tokens": [...]}}')
    return raw


def load_tokens(config: AuthConfig, path: str, audience: str | None = None) -> list[TokenEntry]:
    """Every entry in a format-2 tokens file. With ``audience``, an entry for any other
    audience is refused too: a server never loads tokens meant for another server."""
    raw = _read_json(path)
    if "version" not in raw:
        raise ConfigError(migrate_hint(config, path))
    if raw["version"] != TOKENS_FILE_VERSION or set(raw) != {"version", "tokens"}:
        raise ConfigError(
            f'{path}: expected {{"version": {TOKENS_FILE_VERSION}, "tokens": [...]}} '
            f"and nothing else, got version {raw['version']!r} and keys {sorted(raw)}"
        )
    entries = [config.token_entry.model_validate(e) for e in raw["tokens"]]
    if audience is not None:
        foreign = sorted(
            {f"{e.client_id} ({e.audience})" for e in entries if e.audience != audience}
        )
        if foreign:
            raise ConfigError(
                f"{path}: tokens for another audience: {', '.join(foreign)}. This server's "
                f"audience is {audience!r}; give each server its own tokens file."
            )
    return entries


def save_tokens(path: str, entries: list[TokenEntry]) -> None:
    """Replace the file atomically (a running server re-reads it at any moment). A new file
    is 0644: it holds only hashes, and the server often runs as another user (the image's
    uid 10001 reads a file the operator issued into). A replaced file keeps its mode."""
    data = {"version": TOKENS_FILE_VERSION, "tokens": [e.to_file() for e in entries]}
    atomic_write(path, (json.dumps(data, indent=2) + "\n").encode(), mode=0o644)


def _index(entries: list[TokenEntry]) -> dict[str, TokenEntry]:
    by_hash = {e.sha256: e for e in entries}
    if len(by_hash) != len(entries):
        raise ConfigError("Duplicate token hash in tokens file.")
    return by_hash


class StaticTokenVerifier:
    """Implements the SDK's ``TokenVerifier`` protocol over hashed tokens from a file.

    A token is accepted only if its hash is in the file, its audience is this server's, it
    has not expired (wall-clock UTC, never the domain's simulated clock) and it is not
    revoked. Lookup is by hash, so response time does not depend on how much of a guess
    matches.

    Built with ``from_file``, the verifier checks the file's identity (inode, size, mtime)
    on every request and re-reads it when it changed. If the new content is invalid, every
    token is refused until the file is fixed: a revocation must never be skipped because
    the same edit contained a typo.
    """

    config: ClassVar[AuthConfig]

    def __init__(
        self,
        entries: list[TokenEntry],
        *,
        audience: str | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.audience = audience or self.config.audience
        self._clock = clock
        self._by_hash = _index(entries)
        self._path: str | None = None
        self._signature: object = None
        self._lock = threading.Lock()
        self.error: str | None = None  # why the file on disk is being refused, if it is

    @classmethod
    def from_file(
        cls, path: str, *, audience: str | None = None, clock: Callable[[], float] = time.time
    ) -> "StaticTokenVerifier":
        """A verifier over ``path``, which must load now (start-up fails closed)."""
        verifier = cls([], audience=audience, clock=clock)
        verifier._path = path
        verifier._signature = _signature(path)
        verifier._by_hash = _index(load_tokens(cls.config, path, verifier.audience))
        return verifier

    def _refresh(self) -> None:
        if self._path is None:
            return
        signature = _signature(self._path)
        if signature == self._signature:
            return
        with self._lock:
            if signature == self._signature:
                return
            try:
                by_hash = _index(load_tokens(self.config, self._path, self.audience))
            except (OSError, ValueError) as exc:  # ConfigError, JSON and validation errors
                by_hash, self.error = {}, f"{type(exc).__name__}: {exc}"
                logger.error(
                    "Tokens file %s rejected; refusing every token until it is fixed. %s",
                    self._path,
                    self.error,
                )
            else:
                self.error = None
                logger.info("Tokens file %s reloaded: %d tokens", self._path, len(by_hash))
            self._by_hash, self._signature = by_hash, signature

    async def verify_token(self, token: str) -> AccessToken | None:
        self._refresh()
        entry = self._by_hash.get(hash_token(token))
        if entry is None or entry.audience != self.audience:
            return None
        if entry.state(self._clock()) != "active":
            return None
        return AccessToken(
            token=token,
            client_id=entry.client_id,
            # The SDK ties a Streamable HTTP session to (client_id, issuer, subject). A
            # per-token subject keeps two tokens that share a client_id (e.g. two bound
            # tokens of one app) from using or deleting each other's sessions.
            subject=entry.sha256,
            scopes=list(entry.scopes),
            expires_at=math.floor(entry.expires_at.timestamp()),
            claims={entry.config.subject_claim: entry.subject} if entry.subject else None,
        )


def _signature(path: str) -> object:
    try:
        st = os.stat(path)
    except OSError as exc:
        return ("unreadable", exc.errno)
    return (st.st_ino, st.st_size, st.st_mtime_ns)


class Guard:
    """Authorization and rate limiting, enforced in server code.

    ``rate_limit`` runs once per tool call before the SDK validates arguments (see
    ``server.OpsServer``), so every call counts: invalid arguments, refused scopes and
    ``health`` included. ``check`` runs inside each data tool: authenticated, has the scope,
    and (for a bound token) acts only for its own subject. Both tell the audit log (if the
    call is being audited) which scope and subject the call was checked against, and
    whether it was refused.
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
            audit.mark(outcome="rate_limited")
            raise ToolError(
                f"Rate limit exceeded for client {token.client_id}; "
                f"retry in {math.ceil(retry_after)}s."
            )

    def check(self, scope: str, subject_id: str | None = None) -> AccessToken:
        if scope not in self._config.scopes:  # a typo in a tool must not become "no access"
            raise ValueError(f"unknown scope {scope!r}")
        subject = subject_id if self._config.is_subject(subject_id) else None
        audit.mark(scope=scope, subject=subject, checked=True)
        token = get_access_token()
        if token is None:
            audit.mark(outcome="denied")
            raise ToolError("Unauthenticated: this server requires a bearer token.")
        if scope not in token.scopes:
            audit.mark(outcome="denied")
            raise ToolError(f"Forbidden: this token lacks the {scope} scope.")
        bound = (token.claims or {}).get(self._config.subject_claim)
        if bound is not None and subject_id is not None and subject_id != bound:
            audit.mark(outcome="denied")
            raise ToolError(
                f"Forbidden: this token may only act for {self._config.subject_label} {bound}."
            )
        return token


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def issue(
    config: AuthConfig,
    path: str,
    client_id: str,
    scopes: list[str],
    subject: str | None,
    *,
    audience: str | None = None,
    expires_in: timedelta = timedelta(days=DEFAULT_LIFETIME_DAYS),
) -> str:
    """Add a new token's hash to ``path`` (created if missing) and return the raw token,
    which is shown once and never stored."""
    if expires_in <= timedelta(0):
        raise ValueError("a token must expire in the future")
    token = secrets.token_urlsafe(32)
    entries = load_tokens(config, path) if Path(path).exists() else []
    fields: dict[str, Any] = {"client_id": client_id, "sha256": hash_token(token)}
    fields |= {"scopes": scopes, "audience": audience or config.audience}
    fields["expires_at"] = _now_utc() + expires_in
    if subject is not None:
        fields[config.subject_claim] = subject
    entries.append(config.token_entry.model_validate(fields))
    save_tokens(path, entries)
    return token


def revoke(
    config: AuthConfig, path: str, *, client_id: str | None = None, token_id: str | None = None
) -> list[TokenEntry]:
    """Mark matching tokens revoked (by client, or by token ID). A running server refuses
    them from its next request. Returns the tokens newly revoked; raises if none matched."""
    if (client_id is None) == (token_id is None):
        raise ValueError("revoke by exactly one of client_id or token_id")
    entries = load_tokens(config, path)
    now = _now_utc()
    changed, out = [], []
    for e in entries:
        match = e.client_id == client_id if client_id is not None else e.token_id == token_id
        if match and e.revoked_at is None:
            e = e.model_copy(update={"revoked_at": now})
            changed.append(e)
        out.append(e)
    if not changed:
        raise ValueError("no unrevoked token matched")
    save_tokens(path, out)
    return changed


def migrate(
    config: AuthConfig, path: str, audience: str, expires_in: timedelta
) -> list[TokenEntry]:
    """Rewrite a format-1 tokens file as format 2: every token gets ``audience`` and an
    expiry ``expires_in`` from now. Validated as strictly as before (unknown keys refused)."""
    raw = _read_json(path)
    if "version" in raw:
        raise ValueError(f"{path} already has a version ({raw['version']!r}); nothing to migrate")
    if set(raw) != {"tokens"}:
        raise ValueError(f'{path}: expected {{"tokens": [...]}}')
    expires_at = _now_utc() + expires_in
    entries = []
    for old in raw["tokens"]:
        if not isinstance(old, dict) or {"audience", "expires_at", "revoked_at"} & set(old):
            raise ValueError(f"{path}: not a format-1 entry: {old!r}")
        entries.append(
            config.token_entry.model_validate(
                old | {"audience": audience, "expires_at": expires_at}
            )
        )
    _index(entries)
    save_tokens(path, entries)
    return entries


def main(config: AuthConfig, argv: list[str] | None = None) -> None:
    """The domain's token CLI: ``issue``, ``revoke``, ``list`` and ``migrate``."""
    parser = argparse.ArgumentParser(prog=config.cli_prog)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("issue", help="Create a token and add its hash to the tokens file.")
    p.add_argument("--file", required=True)
    p.add_argument("--client", required=True)
    p.add_argument("--scope", action="append", choices=config.scopes, required=True)
    p.add_argument(f"--{config.subject_label}", dest="subject", default=None)
    p.add_argument("--audience", default=config.audience)
    p.add_argument("--expires-in-days", type=float, default=DEFAULT_LIFETIME_DAYS)
    r = sub.add_parser("revoke", help="Revoke tokens; a running server refuses them at once.")
    r.add_argument("--file", required=True)
    who = r.add_mutually_exclusive_group(required=True)
    who.add_argument("--client")
    who.add_argument("--token-id")
    ls = sub.add_parser("list", help="Show every token's ID, client, audience and state.")
    ls.add_argument("--file", required=True)
    m = sub.add_parser("migrate", help="Convert a format-1 tokens file to format 2.")
    m.add_argument("--file", required=True)
    m.add_argument("--audience", default=config.audience)
    m.add_argument("--expires-in-days", type=float, default=DEFAULT_LIFETIME_DAYS)
    args = parser.parse_args(argv)
    try:
        if args.command == "issue":
            token = issue(
                config,
                args.file,
                args.client,
                args.scope,
                args.subject,
                audience=args.audience,
                expires_in=timedelta(days=args.expires_in_days),
            )
            print(token)  # noqa: T201 - the CLI's output is the token itself
        elif args.command == "revoke":
            for e in revoke(config, args.file, client_id=args.client, token_id=args.token_id):
                print(f"revoked {e.token_id} ({e.client_id})")  # noqa: T201
        elif args.command == "list":
            now = time.time()
            for e in load_tokens(config, args.file):
                subject = f" {config.subject_claim}={e.subject}" if e.subject else ""
                print(  # noqa: T201
                    f"{e.token_id}  {e.state(now):8}  {e.client_id}  aud={e.audience}  "
                    f"expires={_utc_text(e.expires_at)}  scopes={','.join(e.scopes)}{subject}"
                )
        else:
            entries = migrate(
                config, args.file, args.audience, timedelta(days=args.expires_in_days)
            )
            print(f"migrated {len(entries)} tokens to format {TOKENS_FILE_VERSION}")  # noqa: T201
    except PermissionError as exc:
        sys.exit(
            f"Cannot write {args.file}: {exc.strerror}. In Docker, run this command with "
            '--user "$(id -u):$(id -g)" so the file is written as you.'
        )
    except (ValueError, OSError) as exc:  # ConfigError, validation, a missing file
        sys.exit(f"{config.cli_prog} {args.command}: {exc}")
