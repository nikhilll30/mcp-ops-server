"""Part 9 safeguards on real server processes: fail-closed start-up, and OAuth JWTs over HTTP.

Each server is ``python -m mcp_ops_server`` (or ``salon_ops``) in a subprocess, as deployed.
"""

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import DERM_PATIENT, safeguard_env
from test_http_integration import INIT, _free_port, _raw_post, _session, _wait_for_port
from test_oauth import ISSUER, Signer

from mcp_ops_kit import audit
from mcp_ops_kit.crypto import DataKey
from mcp_ops_server.auth import ALL_SCOPES, issue


def _start_fails(module: str, env: dict[str, str]) -> str:
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("MCP_OPS_", "SALON_OPS_"))}
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", module],
        env={**clean, **env},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    return proc.stderr


@pytest.mark.parametrize(
    ("module", "prefix"), [("mcp_ops_server", "MCP_OPS_"), ("salon_ops", "SALON_OPS_")]
)
@pytest.mark.parametrize("missing", ["AUDIT_LOG", "DATA_KEY"])
def test_no_server_starts_without_an_audit_log_and_a_key(
    tmp_path: Path, module: str, prefix: str, missing: str
) -> None:
    tokens = tmp_path / "tokens.json"
    tokens.write_text('{"version": 2, "tokens": []}')
    env = {f"{prefix}TOKENS_FILE": str(tokens), **safeguard_env(prefix, tmp_path)}
    del env[f"{prefix}{missing}"]
    err = _start_fails(module, env)
    assert f"{prefix}AUDIT_LOG and {prefix}DATA_KEY are both required" in err
    assert "python -m mcp_ops_kit.crypto new-key" in err
    assert not (tmp_path / "audit.log").exists()


def test_an_unversioned_tokens_file_stops_start_up_with_the_fix(tmp_path: Path) -> None:
    tokens = tmp_path / "tokens.json"
    tokens.write_text(json.dumps({"tokens": []}))
    env = {"MCP_OPS_TOKENS_FILE": str(tokens), **safeguard_env("MCP_OPS_", tmp_path)}
    err = _start_fails("mcp_ops_server", env)
    assert f"migrate --file {tokens}" in err and len(err.strip().splitlines()) == 1


def test_half_a_jwt_configuration_stops_start_up(tmp_path: Path) -> None:
    env = {"MCP_OPS_JWT_ISSUER": ISSUER, **safeguard_env("MCP_OPS_", tmp_path)}
    err = _start_fails("mcp_ops_server", env)
    assert "need both an issuer and a JWKS" in err


def test_a_wrong_data_key_stops_start_up(tmp_path: Path) -> None:
    tokens = str(tmp_path / "tokens.json")
    issue(tokens, "dev", list(ALL_SCOPES), None)
    first = safeguard_env("MCP_OPS_", tmp_path)
    log = audit.AuditLog(first["MCP_OPS_AUDIT_LOG"], DataKey.from_text(first["MCP_OPS_DATA_KEY"]))
    log.append(tool="health", outcome="ok")
    log.close()
    env = {"MCP_OPS_TOKENS_FILE": tokens, **safeguard_env("MCP_OPS_", tmp_path)}  # new key
    assert "wrong data key" in _start_fails("mcp_ops_server", env)


# ---- a server that accepts only OAuth JWTs ---------------------------------------------------


@dataclass
class JwtServer:
    port: int
    signer: Signer
    revoked: str
    audit_log: str
    key: DataKey

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"


@pytest.fixture(scope="module")
def jwt_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[JwtServer]:
    root = tmp_path_factory.mktemp("jwt")
    signer = Signer("as-key-1")
    (root / "jwks.json").write_text(json.dumps({"keys": [signer.jwk]}))
    revoked = str(root / "revoked.json")
    key = DataKey.generate()
    port = _free_port()
    clean = {k: v for k, v in os.environ.items() if not k.startswith("MCP_OPS_")}
    env = {
        **clean,
        "MCP_OPS_PORT": str(port),
        "MCP_OPS_JWT_ISSUER": ISSUER,
        "MCP_OPS_JWT_JWKS": str(root / "jwks.json"),
        "MCP_OPS_JWT_REVOKED_FILE": revoked,
        "MCP_OPS_AUDIT_LOG": str(root / "audit.log"),
        "MCP_OPS_DATA_KEY": key.to_text(),
    }
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "mcp_ops_server"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_port(port, proc)
        yield JwtServer(port, signer, revoked, str(root / "audit.log"), key)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


async def _portal_calls(url: str, token: str) -> tuple[bool, str]:
    async with _session(url, token) as session:
        own = await session.call_tool("get_referral_status", {"patient_id": DERM_PATIENT})
        other = await session.call_tool("get_referral_status", {"patient_id": "PAT-0008"})
    return own.is_error, other.content[0].text


def test_a_bound_jwt_acts_only_for_its_patient(jwt_server: JwtServer) -> None:
    token = jwt_server.signer.token(scope="read:referrals", patient_id=DERM_PATIENT)
    own_failed, other = asyncio.run(_portal_calls(jwt_server.url, token))
    assert own_failed is False
    assert f"may only act for patient {DERM_PATIENT}" in other
    records = list(audit.records(jwt_server.audit_log, jwt_server.key))
    assert [r["outcome"] for r in records if r["tool"] == "get_referral_status"][-2:] == [
        "ok",
        "denied",
    ]
    assert records[-1]["client_id"] == "portal-app"


@pytest.mark.parametrize(
    "claims",
    [{"aud": "salon-ops"}, {"exp": 1}, {"iss": "https://other.example.test"}],
    ids=["other-audience", "expired", "other-issuer"],
)
def test_a_bad_jwt_is_refused_at_the_http_layer(jwt_server: JwtServer, claims: dict) -> None:
    assert _raw_post(jwt_server.url, INIT, jwt_server.signer.token(**claims)) == 401


def test_a_static_token_is_refused_by_a_jwt_only_server(jwt_server: JwtServer) -> None:
    assert _raw_post(jwt_server.url, INIT, "a-static-token-without-dots") == 401


def test_a_revoked_jti_is_refused_without_a_restart(jwt_server: JwtServer) -> None:
    token = jwt_server.signer.token(jti="revoke-me-live")
    assert _raw_post(jwt_server.url, INIT, token) == 200
    subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "mcp_ops_kit.oauth", "revoke", "--file", jwt_server.revoked]
        + ["--jti", "revoke-me-live"],
        check=True,
        capture_output=True,
        timeout=30,
    )
    assert _raw_post(jwt_server.url, INIT, token) == 401
    assert _raw_post(jwt_server.url, INIT, jwt_server.signer.token()) == 200
