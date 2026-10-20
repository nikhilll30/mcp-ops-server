"""OAuth 2.1 resource-server tokens (Part 9): JWT access tokens checked against a JWKS.

Keys are generated per test; the authorization server is simulated by signing tokens here.
The JWKS is served from a file and from a real local HTTP server (the network path).
"""

import asyncio
import http.server
import json
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from mcp_ops_kit import oauth
from mcp_ops_kit.auth import issue
from mcp_ops_kit.errors import ConfigError
from mcp_ops_kit.oauth import EitherVerifier, JwksSource, JwtVerifier, RevocationList
from mcp_ops_server.auth import AUTH

ISSUER = "https://auth.example.test"
AUDIENCE = AUTH.audience


class Signer:
    """A stand-in authorization server: one signing key, published as a JWK."""

    def __init__(self, kid: str, alg: str = "RS256") -> None:
        self.kid, self.alg = kid, alg
        if alg == "RS256":
            self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            self.jwk = RSAAlgorithm.to_jwk(self.private.public_key(), as_dict=True)
        else:
            self.private = ec.generate_private_key(ec.SECP256R1())
            self.jwk = ECAlgorithm.to_jwk(self.private.public_key(), as_dict=True)
        self.jwk |= {"kid": kid, "alg": alg, "use": "sig"}

    def token(self, **overrides: Any) -> str:  # noqa: ANN401 - claim values
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "user-1",
            "client_id": "portal-app",
            "jti": str(uuid.uuid4()),
            "iat": now,
            "exp": now + 300,
            "scope": "read:schedule read:referrals openid",
        }
        claims |= overrides
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, self.private, algorithm=self.alg, headers={"kid": self.kid})


def _jwks(*signers: Signer) -> dict:
    return {"keys": [s.jwk for s in signers]}


@pytest.fixture
def signer() -> Signer:
    return Signer("key-1")


@pytest.fixture
def jwks_file(tmp_path: Path, signer: Signer) -> Path:
    path = tmp_path / "jwks.json"
    path.write_text(json.dumps(_jwks(signer)))
    return path


def _verifier(jwks: str, revoked: str | None = None, **kwargs: Any) -> JwtVerifier:  # noqa: ANN401
    return JwtVerifier(
        AUTH,
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks=JwksSource(jwks, **kwargs),
        revocations=RevocationList(revoked) if revoked else None,
    )


def _verify(verifier: Any, token: str) -> Any:  # noqa: ANN401
    return asyncio.run(verifier.verify_token(token))


# ---- what a valid token gives ----------------------------------------------------------------


@pytest.mark.parametrize("alg", ["RS256", "ES256"])
def test_a_valid_token_is_accepted(tmp_path: Path, alg: str) -> None:
    s = Signer("k", alg)
    (tmp_path / "jwks.json").write_text(json.dumps(_jwks(s)))
    token = s.token(patient_id="PAT-0008")
    at = _verify(_verifier(str(tmp_path / "jwks.json")), token)
    assert at is not None
    assert at.client_id == "portal-app"
    assert at.scopes == ["read:schedule", "read:referrals"]  # "openid" is not ours
    assert at.claims == {"iss": ISSUER, "patient_id": "PAT-0008"}
    assert at.expires_at == jwt.decode(token, options={"verify_signature": False})["exp"]


def test_aud_may_be_a_list_that_names_us(jwks_file: Path, signer: Signer) -> None:
    assert _verify(_verifier(str(jwks_file)), signer.token(aud=["other-api", AUDIENCE]))


def test_bound_and_unbound_tokens_of_one_user_get_separate_sessions(
    jwks_file: Path, signer: Signer
) -> None:
    v = _verifier(str(jwks_file))
    a = _verify(v, signer.token())
    b = _verify(v, signer.token(patient_id="PAT-0001"))
    c = _verify(v, signer.token())
    assert a.subject == c.subject != b.subject


# ---- what is refused -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://evil.example.test"},
        {"aud": "salon-ops"},  # another server's token
        {"aud": ["other-api"]},
        {"exp": int(time.time()) - 60},
        {"nbf": int(time.time()) + 600},
        {"jti": None},
        {"client_id": None},
        {"sub": None},
        {"iat": None},
        {"exp": None},
        {"patient_id": "Jane Doe"},  # a malformed binding never becomes "unbound"
        {"patient_id": "PAT-0008\n"},
        {"scope": ["read:schedule"]},  # RFC 9068: a space-separated string
    ],
    ids=lambda o: next(iter(o)) + "=" + str(next(iter(o.values()))),
)
def test_bad_claims_are_refused(jwks_file: Path, signer: Signer, overrides: dict) -> None:
    assert _verify(_verifier(str(jwks_file)), signer.token(**overrides)) is None


def test_unsigned_and_hmac_tokens_are_refused(jwks_file: Path, signer: Signer) -> None:
    """alg=none, and the classic confusion attack: HS256 'signed' with the public key."""
    claims = jwt.decode(signer.token(), options={"verify_signature": False})
    unsigned = jwt.encode(claims, None, algorithm="none", headers={"kid": "key-1"})
    public_pem = signer.private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    import base64
    import hashlib
    import hmac

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode().rstrip("=")

    head = b64(json.dumps({"alg": "HS256", "kid": "key-1", "typ": "JWT"}).encode())
    body = b64(json.dumps(claims).encode())
    sig = b64(hmac.new(public_pem, f"{head}.{body}".encode(), hashlib.sha256).digest())
    confused = f"{head}.{body}.{sig}"
    v = _verifier(str(jwks_file))
    assert _verify(v, unsigned) is None
    assert _verify(v, confused) is None
    assert _verify(v, "not.a.jwt") is None


def test_a_token_from_another_key_with_the_same_kid_is_refused(jwks_file: Path) -> None:
    impostor = Signer("key-1")  # same kid, different private key
    assert _verify(_verifier(str(jwks_file)), impostor.token()) is None


def test_a_key_published_for_one_algorithm_is_not_used_for_another(tmp_path: Path) -> None:
    ec_signer = Signer("k", "ES256")
    jwk = dict(ec_signer.jwk)
    (tmp_path / "jwks.json").write_text(json.dumps({"keys": [jwk]}))
    claims = jwt.decode(ec_signer.token(), options={"verify_signature": False})
    rs = Signer("k")
    forged = jwt.encode(claims, rs.private, algorithm="RS256", headers={"kid": "k"})
    assert _verify(_verifier(str(tmp_path / "jwks.json")), forged) is None


# ---- revocation and key rotation, no restart -----------------------------------------------


def test_revoking_a_jti_takes_effect_at_once(
    jwks_file: Path, signer: Signer, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    revoked = str(tmp_path / "revoked.json")
    v = _verifier(str(jwks_file), revoked)
    token = signer.token(jti="jti-to-revoke")
    other = signer.token()
    assert _verify(v, token) and _verify(v, other)
    oauth.main(["revoke", "--file", revoked, "--jti", "jti-to-revoke"])
    assert "revoked jti jti-to-revoke" in capsys.readouterr().out
    assert _verify(v, token) is None
    assert _verify(v, other) is not None
    with pytest.raises(SystemExit, match="already revoked"):
        oauth.main(["revoke", "--file", revoked, "--jti", "jti-to-revoke"])


def test_a_broken_revocation_file_refuses_every_jwt(
    jwks_file: Path, signer: Signer, tmp_path: Path
) -> None:
    revoked = tmp_path / "revoked.json"
    v = _verifier(str(jwks_file), str(revoked))
    assert _verify(v, signer.token())
    revoked.write_text("{ broken")
    assert _verify(v, signer.token()) is None
    revoked.write_text(json.dumps({"version": 1, "revoked": []}))
    assert _verify(v, signer.token())
    revoked.write_text("{ broken")
    with pytest.raises(ConfigError, match="not valid"):  # and a server will not start on it
        RevocationList(str(revoked))


def test_a_rotated_key_in_the_jwks_file_is_picked_up(jwks_file: Path, signer: Signer) -> None:
    v = _verifier(str(jwks_file))
    new = Signer("key-2")
    assert _verify(v, new.token()) is None
    jwks_file.write_text(json.dumps(_jwks(signer, new)))
    assert _verify(v, new.token()) is not None
    jwks_file.write_text(json.dumps(_jwks(new)))  # the old key retired
    assert _verify(v, signer.token()) is None


@pytest.fixture
def jwks_server() -> Iterator[tuple[str, dict]]:
    """A real HTTP server publishing a JWKS that tests can change: ``state["jwks"]``."""
    state: dict[str, Any] = {"jwks": {"keys": []}, "hits": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server's naming
            state["hits"] += 1
            body = json.dumps(state["jwks"]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/jwks.json", state
    finally:
        httpd.shutdown()


def test_jwks_over_http_with_caching_and_rotation(
    jwks_server: tuple[str, dict], signer: Signer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    url, state = jwks_server
    state["jwks"] = _jwks(signer)
    now = {"t": 1000.0}
    v = _verifier(url, clock=lambda: now["t"])
    assert _verify(v, signer.token()) is not None
    assert _verify(v, signer.token()) is not None
    assert state["hits"] == 1  # cached
    new = Signer("key-2")
    state["jwks"] = _jwks(signer, new)
    now["t"] += 10  # a new kid within 30 s of the last fetch: not fetched again yet
    assert _verify(v, new.token()) is None and state["hits"] == 1
    now["t"] += 30
    assert _verify(v, new.token()) is not None and state["hits"] == 2
    state["jwks"] = {"keys": []}
    now["t"] += 301  # stale: re-fetched, and the cached keys are replaced
    assert _verify(v, signer.token()) is None and state["hits"] == 3


def test_an_unreachable_jwks_keeps_the_cached_keys(
    jwks_server: tuple[str, dict], signer: Signer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    url, state = jwks_server
    state["jwks"] = _jwks(signer)
    now = {"t": 1000.0}
    source = JwksSource(url, clock=lambda: now["t"], timeout_s=1)
    v = JwtVerifier(AUTH, issuer=ISSUER, audience=AUDIENCE, jwks=source)
    assert _verify(v, signer.token()) is not None
    source.location = "http://127.0.0.1:9/jwks.json"  # nothing listens on the discard port
    now["t"] += 301
    assert _verify(v, signer.token()) is not None


@pytest.mark.parametrize(
    "location", ["http://auth.example.test/jwks.json", "ftp://127.0.0.1/jwks", "/no/such/jwks.json"]
)
def test_jwks_locations_must_be_https_local_http_or_a_file(location: str) -> None:
    with pytest.raises(ConfigError):
        JwksSource(location)


# ---- one server, both kinds of token -------------------------------------------------------


def test_static_tokens_and_jwts_side_by_side(
    tmp_path: Path, jwks_file: Path, signer: Signer
) -> None:
    tokens = str(tmp_path / "tokens.json")
    static_token = issue(AUTH, tokens, "desk", ["read:schedule"], None)
    v = EitherVerifier(AUTH.verifier.from_file(tokens), _verifier(str(jwks_file)))
    assert _verify(v, static_token).client_id == "desk"
    assert _verify(v, signer.token()).client_id == "portal-app"
    assert "." not in static_token
    jwt_only = EitherVerifier(None, _verifier(str(jwks_file)))
    assert _verify(jwt_only, static_token) is None
    with pytest.raises(ConfigError):
        EitherVerifier(None, None)


@pytest.mark.parametrize(
    "jwks",
    [
        {"keys": [{"kty": "nope", "kid": "key-1"}]},
        {"keys": [{"kty": "oct", "kid": "key-1", "k": "c2VjcmV0"}]},
        {"keys": "x"},
        [],
    ],
    ids=["unparseable", "hmac-only", "keys-not-a-list", "not-an-object"],
)
def test_a_jwks_with_no_usable_key_refuses_tokens_without_crashing(
    tmp_path: Path, signer: Signer, jwks: object
) -> None:
    """An empty or unusable key set once raised PyJWKSetError out of verify_token (it is
    not a PyJWKError), which would have been a 500 instead of a 401."""
    path = tmp_path / "jwks.json"
    path.write_text(json.dumps(jwks))
    assert _verify(_verifier(str(path)), signer.token()) is None


@pytest.mark.parametrize(
    "body",
    [
        {"version": 1, "revoked": None},
        {"version": 1, "revoked": "jti-1"},
        {"version": 1, "revoked": [1]},
        {"version": 1, "revoked": [{"jti": 1}]},
    ],
    ids=["null", "string", "not-an-object", "jti-not-a-string"],
)
def test_a_malformed_revocation_file_refuses_without_crashing(
    jwks_file: Path, signer: Signer, tmp_path: Path, body: dict
) -> None:
    """Cursor Bugbot, PR #20: ``"revoked": null`` raised TypeError out of verify_token
    (a 500), where every other malformed file already refused (a 401)."""
    revoked = tmp_path / "revoked.json"
    v = _verifier(str(jwks_file), str(revoked))
    assert _verify(v, signer.token()) is not None
    revoked.write_text(json.dumps(body))
    assert _verify(v, signer.token()) is None
