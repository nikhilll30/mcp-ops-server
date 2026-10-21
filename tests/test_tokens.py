"""Tokens file format 2 (Part 9): audience, expiry and revocation, re-read without a restart.

The clinic's tokens are used throughout; the logic is the kit's (``mcp_ops_kit.auth``).
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mcp_ops_kit import auth as kit
from mcp_ops_kit.errors import ConfigError
from mcp_ops_server.auth import ALL_SCOPES, AUTH, StaticTokenVerifier, issue, main


def _verify(verifier: StaticTokenVerifier, token: str) -> bool:
    return asyncio.run(verifier.verify_token(token)) is not None


def _v1_file(path: Path, entries: list[dict]) -> None:
    path.write_text(json.dumps({"tokens": entries}))


# ---- registering a hash (the token never passes through the host; Part 10) ------------------


def test_issue_sha256_registers_a_token_made_elsewhere(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """An admin task's stdout goes to CloudWatch, so it must never print a token (Cursor
    Security, PR #22). The operator makes the token; the host sees only its hash."""
    path = str(tmp_path / "tokens.json")
    token = "made-on-the-operators-machine-" + "x" * 20
    digest = kit.hash_token(token)
    main(["issue", "--file", path, "--client", "portal", "--scope", "read:schedule",
          "--sha256", digest])  # fmt: skip
    out = capsys.readouterr().out
    assert out == f"registered {digest[:12]} (portal)\n"
    assert token not in out and digest not in out
    assert _verify(StaticTokenVerifier.from_file(path), token)


def test_issue_sha256_refuses_a_duplicate_and_leaves_the_file_loadable(tmp_path: Path) -> None:
    """Two entries with one hash make the whole file refuse every token (fail closed), so a
    repeated registration must be refused before it is written."""
    path = str(tmp_path / "tokens.json")
    digest = kit.hash_token("t" * 43)
    issue(path, "a", ["read:schedule"], None, sha256=digest)
    before = Path(path).read_text()
    with pytest.raises(ValueError, match="already in the tokens file"):
        issue(path, "b", ["read:schedule"], None, sha256=digest)
    assert Path(path).read_text() == before
    assert _verify(StaticTokenVerifier.from_file(path), "t" * 43)


def test_issue_sha256_refuses_a_malformed_hash(tmp_path: Path) -> None:
    path = str(tmp_path / "tokens.json")
    with pytest.raises(SystemExit):
        main(["issue", "--file", path, "--client", "a", "--scope", "read:schedule",
              "--sha256", "not-a-hash"])  # fmt: skip
    assert not Path(path).exists()


# ---- format and migration ------------------------------------------------------------------


def test_issue_writes_format_2_with_audience_and_expiry(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    before = datetime.now(UTC)
    token = issue(str(path), "desk", ["read:schedule"], "PAT-0008")
    data = json.loads(path.read_text())
    assert data["version"] == 2 and set(data) == {"version", "tokens"}
    (entry,) = data["tokens"]
    assert entry["audience"] == "mcp-ops-server" and "revoked_at" not in entry
    expires = datetime.fromisoformat(entry["expires_at"])
    assert timedelta(days=89) < expires - before <= timedelta(days=90, seconds=1)
    verified = asyncio.run(StaticTokenVerifier.from_file(str(path)).verify_token(token))
    assert verified is not None
    assert verified.expires_at == int(expires.timestamp())
    # Hashes only, and readable by the container's own user (uid 10001), which is not the
    # user who issued the token (Part 9: 0600 here broke the Docker flow; see FAILURES.md).
    assert path.stat().st_mode & 0o777 == 0o644
    path.chmod(0o640)
    issue(str(path), "second", ["read:schedule"], None)
    assert path.stat().st_mode & 0o777 == 0o640  # an operator's choice survives a rewrite


def test_an_unversioned_file_is_refused_with_the_fix(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    _v1_file(path, [{"client_id": "c", "sha256": "0" * 64, "scopes": ["read:schedule"]}])
    with pytest.raises(ConfigError) as exc:
        StaticTokenVerifier.from_file(str(path))
    message = str(exc.value)
    assert "format-1" in message
    assert f"python -m mcp_ops_server.auth migrate --file {path}" in message


def test_migrate_keeps_every_token_working(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    path = tmp_path / "tokens.json"
    raw = {"staff": "s3cret-staff", "portal": "s3cret-portal"}
    _v1_file(
        path,
        [
            {
                "client_id": "staff",
                "sha256": kit.hash_token(raw["staff"]),
                "scopes": ["read:schedule"],
            },
            {
                "client_id": "portal",
                "sha256": kit.hash_token(raw["portal"]),
                "scopes": list(ALL_SCOPES),
                "patient_id": "PAT-0001",
            },
        ],
    )
    main(["migrate", "--file", str(path), "--expires-in-days", "30"])
    assert "migrated 2 tokens to format 2" in capsys.readouterr().out
    verifier = StaticTokenVerifier.from_file(str(path))
    portal = asyncio.run(verifier.verify_token(raw["portal"]))
    assert _verify(verifier, raw["staff"]) and portal is not None
    assert portal.claims == {"patient_id": "PAT-0001"}
    assert portal.expires_at - datetime.now(UTC).timestamp() == pytest.approx(30 * 86400, abs=60)
    with pytest.raises(SystemExit, match="already has a version"):
        main(["migrate", "--file", str(path)])


def test_migrate_is_as_strict_as_the_loader(tmp_path: Path) -> None:
    """A format-1 entry with an unknown key (another domain's claim) is not migrated into
    an unbound token: the Part 8 fail-open must not come back through migration."""
    path = tmp_path / "tokens.json"
    _v1_file(
        path, [{"client_id": "c", "sha256": "0" * 64, "scopes": [], "customer_id": "CUS-0001"}]
    )
    with pytest.raises(SystemExit, match="Extra inputs"):
        main(["migrate", "--file", str(path)])
    assert "version" not in json.loads(path.read_text())  # left untouched


@pytest.mark.parametrize("field", ["expires_at", "revoked_at"])
def test_times_without_an_offset_are_refused(field: str) -> None:
    entry = {"client_id": "c", "sha256": "0" * 64, "scopes": [], "audience": "mcp-ops-server"}
    entry |= {"expires_at": "2099-01-01T00:00:00Z", field: "2099-01-01T00:00:00"}
    with pytest.raises(ValueError, match="UTC offset"):
        AUTH.token_entry.model_validate(entry)


def test_a_token_must_expire_in_the_future(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="future"):
        issue(str(tmp_path / "t.json"), "c", ["read:schedule"], None, expires_in=timedelta(0))


# ---- audience ------------------------------------------------------------------------------


def test_a_file_with_another_audiences_token_is_refused(tmp_path: Path) -> None:
    path = str(tmp_path / "tokens.json")
    issue(path, "desk", ["read:schedule"], None)
    issue(path, "elsewhere", ["read:schedule"], None, audience="staging-clinic")
    with pytest.raises(ConfigError, match=r"another audience: elsewhere \(staging-clinic\)"):
        StaticTokenVerifier.from_file(path)
    # The server configured for that audience refuses the other token instead.
    with pytest.raises(ConfigError, match=r"desk \(mcp-ops-server\)"):
        StaticTokenVerifier.from_file(path, audience="staging-clinic")


def test_the_verifier_checks_audience_per_token_too() -> None:
    entry = AUTH.token_entry(
        client_id="c",
        sha256=kit.hash_token("t"),
        scopes=["read:schedule"],
        audience="another-server",
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
    )
    assert not _verify(StaticTokenVerifier([entry]), "t")
    assert _verify(StaticTokenVerifier([entry], audience="another-server"), "t")


# ---- expiry --------------------------------------------------------------------------------


def test_an_expired_token_is_refused_on_wall_clock_time(tmp_path: Path) -> None:
    path = str(tmp_path / "tokens.json")
    token = issue(path, "desk", ["read:schedule"], None, expires_in=timedelta(hours=1))
    now = datetime.now(UTC).timestamp()
    at = {"t": now}
    verifier = StaticTokenVerifier.from_file(path, clock=lambda: at["t"])
    assert _verify(verifier, token)
    at["t"] = now + 3600 + 5
    assert not _verify(verifier, token)


# ---- revocation and reload, no restart -----------------------------------------------------


def test_revoke_takes_effect_on_the_next_request(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    path = str(tmp_path / "tokens.json")
    a1 = issue(path, "app-a", ["read:schedule"], None)
    a2 = issue(path, "app-a", ["read:schedule"], None)
    b = issue(path, "app-b", ["read:schedule"], None)
    verifier = StaticTokenVerifier.from_file(path)
    assert all(_verify(verifier, t) for t in (a1, a2, b))
    main(["revoke", "--file", path, "--client", "app-a"])
    assert capsys.readouterr().out.count("revoked ") == 2
    assert not _verify(verifier, a1) and not _verify(verifier, a2)
    assert _verify(verifier, b)
    main(["revoke", "--file", path, "--token-id", kit.hash_token(b)[:12]])
    assert not _verify(verifier, b)
    with pytest.raises(SystemExit, match="no unrevoked token matched"):
        main(["revoke", "--file", path, "--client", "app-a"])


def test_a_new_token_works_without_a_restart(tmp_path: Path) -> None:
    path = str(tmp_path / "tokens.json")
    issue(path, "first", ["read:schedule"], None)
    verifier = StaticTokenVerifier.from_file(path)
    later = issue(path, "second", ["read:schedule"], None)
    assert _verify(verifier, later)


@pytest.mark.parametrize(
    "breakage",
    [
        lambda p: p.write_text("{ not json"),
        lambda p: p.write_text(json.dumps({"tokens": json.loads(p.read_text())["tokens"]})),
        lambda p: p.unlink(),
    ],
    ids=["corrupt", "unversioned", "deleted"],
)
def test_a_broken_tokens_file_refuses_every_token_until_fixed(tmp_path: Path, breakage) -> None:  # noqa: ANN001
    """If an edit breaks the file, nothing is accepted, not the old set: the same edit may
    have been a revocation, and keeping the old tokens would skip it."""
    path = tmp_path / "tokens.json"
    token = issue(str(path), "desk", ["read:schedule"], None)
    good = path.read_text()
    verifier = StaticTokenVerifier.from_file(str(path))
    assert _verify(verifier, token)
    breakage(path)
    assert not _verify(verifier, token)
    assert verifier.error is not None
    path.write_text(good)
    assert _verify(verifier, token) and verifier.error is None


def test_list_shows_each_tokens_state(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    path = str(tmp_path / "tokens.json")
    issue(path, "live", ["read:schedule"], "PAT-0008")
    issue(path, "gone", ["read:schedule"], None)
    kit.revoke(AUTH, path, client_id="gone")
    main(["list", "--file", path])
    out = capsys.readouterr().out
    assert "active    live  aud=mcp-ops-server" in out and "patient_id=PAT-0008" in out
    assert "revoked   gone" in out


def test_the_tokens_file_is_replaced_atomically(tmp_path: Path) -> None:
    """A server reading the file mid-write must see the old file or the new one: each
    write is a new file renamed into place (so a new inode), never an in-place rewrite."""
    path = tmp_path / "tokens.json"
    issue(str(path), "a", ["read:schedule"], None)
    inode = path.stat().st_ino
    issue(str(path), "b", ["read:schedule"], None)
    assert path.stat().st_ino != inode
    # no temp file left; the lock file beside it serialises changes (Codex review, PR #22)
    assert sorted(p.name for p in tmp_path.iterdir()) == [".tokens.json.lock", "tokens.json"]


def test_any_failure_to_reload_refuses_rather_than_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cursor Bugbot, PR #20 found a TypeError escaping a reload (the JWT revocation file).
    The reloaders now refuse on *any* exception, not a list of the expected ones."""
    path = tmp_path / "tokens.json"
    token = issue(str(path), "desk", ["read:schedule"], None)
    verifier = StaticTokenVerifier.from_file(str(path))

    def unexpected(*args: object, **kwargs: object) -> None:
        raise TypeError("a shape nobody anticipated")

    monkeypatch.setattr(kit, "load_tokens", unexpected)
    issue_later = path.read_text()
    path.write_text(issue_later + " ")  # changed, so it is re-read
    assert not _verify(verifier, token)
    assert verifier.error is not None and "TypeError" in verifier.error


# ---- concurrent changes to the tokens file (Codex review, PR #22) -------------------------


def test_a_concurrent_issue_cannot_undo_a_revocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two admin tasks share the tokens file. An issue that read the file before a revoke,
    then saved its older copy, would make the revoked token active again. Every change holds
    the file's lock from read to write, so the revoke waits for the issue."""
    import threading
    import time

    path = str(tmp_path / "tokens.json")
    victim = issue(path, "victim", ["read:schedule"], None)
    loaded, real_load = threading.Event(), kit.load_tokens

    def slow_load(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        entries = real_load(*args, **kwargs)
        if threading.current_thread().name == "issuer":
            loaded.set()
            time.sleep(0.3)  # the revoke runs now, unless the lock makes it wait
        return entries

    monkeypatch.setattr(kit, "load_tokens", slow_load)
    issuer = threading.Thread(
        target=issue, args=(path, "new", ["read:schedule"], None), name="issuer"
    )
    issuer.start()
    assert loaded.wait(5)
    kit.revoke(AUTH, path, client_id="victim")
    issuer.join(5)
    verifier = StaticTokenVerifier.from_file(path)
    assert not _verify(verifier, victim)
    assert [e.client_id for e in kit.load_tokens(AUTH, path)] == ["victim", "new"]


def test_a_change_gives_up_if_another_holds_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stuck admin task must not make the next one hang forever, nor write unlocked."""
    import fcntl
    import os

    path = tmp_path / "tokens.json"
    issue(str(path), "a", ["read:schedule"], None)
    before = path.read_text()
    fd = os.open(tmp_path / ".tokens.json.lock", os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)  # another process mid-change
    monkeypatch.setattr(kit, "LOCK_TIMEOUT_S", 0.2)
    try:
        with pytest.raises(ValueError, match="has held its lock"):
            kit.revoke(AUTH, str(path), client_id="a")
    finally:
        os.close(fd)
    assert path.read_text() == before
