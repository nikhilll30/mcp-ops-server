"""The salon server over real Streamable HTTP: auth at the HTTP layer and the smoke test."""

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from test_http_integration import INIT, _free_port, _raw_post, _wait_for_port

from salon_ops.auth import ALL_SCOPES, issue


@pytest.fixture(scope="module")
def salon(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[int, str]]:
    tokens_file = str(tmp_path_factory.mktemp("salon-auth") / "tokens.json")
    staff = issue(tokens_file, "salon-desk", list(ALL_SCOPES), None)
    assert staff not in Path(tokens_file).read_text()
    port = _free_port()
    env = {
        **os.environ,
        "SALON_OPS_HOST": "127.0.0.1",
        "SALON_OPS_PORT": str(port),
        "SALON_OPS_TOKENS_FILE": tokens_file,
    }
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "salon_ops"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_port(port, proc)
        yield port, staff
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_salon_refuses_requests_without_a_valid_token(salon: tuple[int, str]) -> None:
    port, _ = salon
    url = f"http://127.0.0.1:{port}/mcp"
    assert _raw_post(url, INIT, None) == 401
    assert _raw_post(url, INIT, "not-a-token") == 401


def test_salon_smoke_test_passes_against_the_real_server(
    salon: tuple[int, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from salon_ops import smoke

    port, staff = salon
    monkeypatch.setenv("MCP_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("MCP_TOKEN", staff)
    smoke.main()
    out = capsys.readouterr().out
    assert "FAIL" not in out and out.rstrip().endswith("smoke test passed")


def test_salon_refuses_to_start_without_tokens_file() -> None:
    env = {k: v for k, v in os.environ.items() if k != "SALON_OPS_TOKENS_FILE"}
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "salon_ops"], env=env, capture_output=True, timeout=30, check=False
    )
    assert proc.returncode != 0
    assert b"SALON_OPS_TOKENS_FILE is not set" in proc.stderr
