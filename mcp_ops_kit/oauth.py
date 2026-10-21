"""OAuth 2.1 resource-server tokens: JWT access tokens (RFC 9068) from your authorization
server, verified here against its published keys (JWKS).

The server never issues these; it only checks them. A token is accepted when:

- it is signed with RS256 or ES256 by a key in the JWKS (``none`` and HMAC algorithms are
  refused, whatever the token's header says);
- ``iss`` is the configured issuer and ``aud`` names this server's audience;
- ``exp`` is in the future (``nbf``, if present, in the past), within 30 s of clock skew;
- it has ``sub``, ``client_id``, ``jti`` and ``iat``, and its ``jti`` is not revoked;
- a subject claim, if present (``patient_id``), is a well-formed ID; it then binds the
  token exactly as a bound static token does.

``scope`` is the space-separated list from RFC 9068; scopes this domain does not know are
ignored (an authorization server may grant scopes for other resources in one token).

Revocation takes effect without a restart: the revocation file (a JSON list of revoked
``jti`` values, ``{"version": 1, "revoked": [{"jti": "...", "revoked_at": "..."}]}``) is
re-read whenever it changes, and short token lifetimes bound the rest.

The JWKS is a file (re-read when it changes) or an ``https://`` URL (``http://`` only for
localhost), cached for 5 minutes and re-fetched early, at most every 30 s, when a token
names a key ID the cache lacks (the authorization server rotated its keys). If fetching
fails, the last good keys are kept and the fetch retried every 30 s, but only for up to an
hour after they were fetched; after that every JWT is refused until a fetch succeeds.
"""

import argparse
import hashlib
import json
import logging
import math
import os
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import anyio.to_thread
import httpx2
import jwt
from mcp.server.auth.provider import AccessToken

from mcp_ops_kit.auth import AuthConfig
from mcp_ops_kit.crypto import atomic_write
from mcp_ops_kit.errors import ConfigError

logger = logging.getLogger(__name__)

ALGORITHMS = ("RS256", "ES256")
LEEWAY_S = 30
JWKS_TTL_S = 300.0
JWKS_REFETCH_MIN_S = 30.0
JWKS_MAX_AGE_S = 3600.0  # while fetches fail, keys older than this are dropped
MAX_CLIENT_ID = 64  # the same limit as a tokens-file client_id
REQUIRED_CLAIMS = ("exp", "iat", "iss", "aud", "sub", "jti", "client_id")


def _file_signature(path: str) -> object:
    try:
        st = os.stat(path)
    except OSError as exc:
        return ("unreadable", exc.errno)
    return (st.st_ino, st.st_size, st.st_mtime_ns)


class JwksSource:
    """The authorization server's signing keys, from a file or a URL."""

    def __init__(
        self,
        location: str,
        *,
        timeout_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        self.location = location
        self._is_url = "://" in location
        if self._is_url:
            parts = urlsplit(location)
            local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
            if parts.scheme != "https" and not (parts.scheme == "http" and local):
                raise ConfigError(
                    f"JWKS URL must be https:// (http:// only for localhost): {location}"
                )
        elif not Path(location).is_file():
            raise ConfigError(f"JWKS file {location} not found")
        self._timeout_s = timeout_s
        self._clock = clock
        self._transport = transport
        self._keys: dict[str, jwt.PyJWK] = {}
        self._signature: object = None
        self._fetched_at = -math.inf
        self._loaded_at = -math.inf  # the last *successful* fetch
        self._refresh_after = JWKS_TTL_S  # after a failed fetch, JWKS_REFETCH_MIN_S
        self._lock = threading.Lock()

    def _load(self) -> dict[str, jwt.PyJWK]:
        if self._is_url:
            with httpx2.Client(timeout=self._timeout_s, transport=self._transport) as http:
                resp = http.get(self.location, headers={"Accept": "application/json"})
                resp.raise_for_status()
                data = resp.json()
        else:
            data = json.loads(Path(self.location).read_text())
        if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
            raise ValueError('a JWKS is {"keys": [...]}')
        if not data["keys"]:
            return {}  # every key retired: valid, and nothing verifies
        keys = {}
        for k in jwt.PyJWKSet.from_dict(data).keys:
            if not k.key_id:
                raise ValueError("every JWKS key needs a kid")
            if k.algorithm_name not in ALGORITHMS:
                continue  # a key for an algorithm we never accept cannot verify anything
            keys[k.key_id] = k
        return keys

    def get(self, kid: str) -> jwt.PyJWK | None:
        """The key ``kid``, refreshing first if the source changed or the key is unknown.
        Blocking (file or network I/O): call it from a worker thread."""
        with self._lock:
            now = self._clock()
            if self._is_url:
                since = now - self._fetched_at  # since the last attempt, failed or not
                stale = since > self._refresh_after
                unknown = kid not in self._keys and since > JWKS_REFETCH_MIN_S
                if stale or unknown:
                    self._fetched_at = now
                    try:
                        self._keys = self._load()
                        self._loaded_at, self._refresh_after = now, JWKS_TTL_S
                    except Exception as exc:  # any failure: keep or drop by age, never a 500
                        # An outage at the authorization server keeps the last good keys for
                        # a while, but never indefinitely: a key retired during the outage
                        # must stop verifying (Cursor Security Reviewer, PR #20).
                        if now - self._loaded_at > JWKS_MAX_AGE_S:
                            self._keys = {}
                        logger.error(
                            "JWKS fetch from %s failed (%s); %s",
                            self.location,
                            exc,
                            "keeping keys fetched %.0f s ago" % (now - self._loaded_at)
                            if self._keys
                            else "refusing JWTs",
                        )
                        # Retry in JWKS_REFETCH_MIN_S, not after a full TTL, and never sooner:
                        # backdating _fetched_at instead made every request fetch while the
                        # cache was empty (Cursor Bugbot, PR #20).
                        self._refresh_after = JWKS_REFETCH_MIN_S
            else:
                signature = _file_signature(self.location)
                if signature != self._signature:
                    self._signature = signature
                    try:
                        self._keys = self._load()
                    except Exception as exc:  # any failure refuses, never a 500
                        self._keys = {}
                        logger.error("JWKS file %s rejected; refusing JWTs: %s", self.location, exc)
            return self._keys.get(kid)


class RevocationList:
    """Revoked ``jti`` values from a file, re-read whenever it changes. If the file becomes
    invalid, or disappears after start-up, every JWT is refused until it is fixed: a
    revocation is never skipped. (It may be missing at start-up: nothing revoked yet.)"""

    def __init__(self, path: str) -> None:
        self.path = path
        self._signature: object = None
        self._revoked: frozenset[str] | None = frozenset()
        self._lock = threading.Lock()
        self._missing_ok = True
        self._refresh()
        self._missing_ok = False  # from now on, a vanished file would un-revoke tokens
        if self._revoked is None:
            raise ConfigError(f"JWT revocation file {path} is not valid; see the log")

    @staticmethod
    def read(path: str, missing_ok: bool = True) -> list[dict[str, str]]:
        if not Path(path).exists():
            if missing_ok:
                return []
            raise FileNotFoundError(f"{path} disappeared")
        data = json.loads(Path(path).read_text())
        if (
            not isinstance(data, dict)
            or data.get("version") != 1
            or set(data) != {"version", "revoked"}
        ):
            raise ValueError(f'{path}: expected {{"version": 1, "revoked": [...]}}')
        if not isinstance(data["revoked"], list):
            raise ValueError(f"{path}: revoked must be a list")
        if not all(isinstance(r, dict) and isinstance(r.get("jti"), str) for r in data["revoked"]):
            raise ValueError(f"{path}: every revoked entry needs a jti")
        return data["revoked"]

    def _refresh(self) -> None:
        signature = _file_signature(self.path)
        if signature == self._signature:
            return
        with self._lock:
            if signature == self._signature:
                return
            try:
                revoked = self.read(self.path, missing_ok=self._missing_ok)
                self._revoked = frozenset(r["jti"] for r in revoked)
            except Exception as exc:  # any failure refuses, never a 500
                self._revoked = None
                logger.error("JWT revocation file %s rejected; refusing JWTs: %s", self.path, exc)
            self._signature = signature

    def is_revoked(self, jti: str) -> bool:
        self._refresh()
        return self._revoked is None or jti in self._revoked


def revoke_jti(path: str, jti: str) -> None:
    entries = RevocationList.read(path)
    if any(r["jti"] == jti for r in entries):
        raise ValueError(f"jti {jti} is already revoked")
    stamp = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    data = {"version": 1, "revoked": [*entries, {"jti": jti, "revoked_at": stamp}]}
    atomic_write(path, (json.dumps(data, indent=2) + "\n").encode(), mode=0o644)  # not secret


class JwtVerifier:
    """The SDK's ``TokenVerifier`` for JWT access tokens (see the module docstring)."""

    def __init__(
        self,
        config: AuthConfig,
        *,
        issuer: str,
        audience: str,
        jwks: JwksSource,
        revocations: RevocationList | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not issuer:
            raise ConfigError("a JWT issuer is required")
        self.config = config
        self.issuer = issuer
        self.audience = audience
        self._jwks = jwks
        self._revocations = revocations
        self._clock = clock

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            return None
        kid, alg = header.get("kid"), header.get("alg")
        if not isinstance(kid, str) or alg not in ALGORITHMS:
            return None
        key = await anyio.to_thread.run_sync(self._jwks.get, kid)
        if key is None or key.algorithm_name != alg:
            return None
        try:
            claims = self._decode(token, key, alg)
        except jwt.PyJWTError as exc:
            logger.info("JWT refused: %s", exc)
            return None
        return self._access_token(token, claims)

    def _decode(self, token: str, key: jwt.PyJWK, alg: str) -> dict[str, Any]:
        # PyJWT checks exp/nbf/iat against time.time(); a test clock is applied on top
        # (below), so the stricter of the two wins.
        claims = jwt.decode(
            token,
            key.key,
            algorithms=[alg],
            audience=self.audience,
            issuer=self.issuer,
            leeway=LEEWAY_S,
            options={"require": list(REQUIRED_CLAIMS), "strict_aud": False},
        )
        now = self._clock()
        if claims["exp"] + LEEWAY_S <= now:
            raise jwt.ExpiredSignatureError("expired")
        if "nbf" in claims and claims["nbf"] - LEEWAY_S > now:
            raise jwt.ImmatureSignatureError("not yet valid")
        return claims

    def _access_token(self, token: str, claims: dict[str, Any]) -> AccessToken | None:
        sub, client_id, jti = claims["sub"], claims["client_id"], claims["jti"]
        if not all(isinstance(v, str) and v for v in (sub, client_id, jti)):
            return None
        if len(client_id) > MAX_CLIENT_ID:
            # Refused, not truncated: a cut ID would merge two clients into one principal,
            # sharing Streamable HTTP sessions (Codex review, PR #20).
            logger.info("JWT refused: client_id longer than %d characters", MAX_CLIENT_ID)
            return None
        if self._revocations is not None and self._revocations.is_revoked(jti):
            logger.info("JWT refused: jti %s is revoked", jti)
            return None
        scope = claims.get("scope", "")
        if not isinstance(scope, str):
            return None
        scopes = [s for s in scope.split() if s in self.config.scopes]
        bound = claims.get(self.config.subject_claim)
        if bound is not None and not self.config.is_subject(bound):
            logger.info("JWT refused: malformed %s claim", self.config.subject_claim)
            return None
        out_claims: dict[str, Any] = {"iss": self.issuer}
        if bound is not None:
            out_claims[self.config.subject_claim] = bound
        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(claims["exp"]),
            # (client_id, iss, subject) owns a Streamable HTTP session. Folding the binding
            # into the subject keeps two tokens of one user bound to different records apart.
            subject=hashlib.sha256(f"{sub}\0{bound or ''}".encode()).hexdigest(),
            claims=out_claims,
        )


class EitherVerifier:
    """Static tokens and JWTs on one server: a token shaped like a JWT (three dot-separated
    parts) goes to the JWT verifier, anything else to the static one. A static token never
    contains a dot (``secrets.token_urlsafe``), so neither can be mistaken for the other."""

    def __init__(self, static: Any | None, jwt_verifier: JwtVerifier | None) -> None:  # noqa: ANN401
        if static is None and jwt_verifier is None:
            raise ConfigError("no token verifier configured")
        self.static = static
        self.jwt = jwt_verifier

    async def verify_token(self, token: str) -> AccessToken | None:
        verifier = self.jwt if token.count(".") == 2 else self.static
        return None if verifier is None else await verifier.verify_token(token)


def main(argv: list[str] | None = None) -> None:
    """``python -m mcp_ops_kit.oauth revoke --file revoked.json --jti <jti>``"""
    parser = argparse.ArgumentParser(prog="python -m mcp_ops_kit.oauth")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("revoke", help="Revoke a JWT by its jti; servers refuse it at once.")
    r.add_argument("--file", required=True)
    r.add_argument("--jti", required=True)
    args = parser.parse_args(argv)
    try:
        revoke_jti(args.file, args.jti)
    except (OSError, ValueError) as exc:
        sys.exit(f"revoke: {exc}")
    print(f"revoked jti {args.jti}")  # noqa: T201


if __name__ == "__main__":
    main()
