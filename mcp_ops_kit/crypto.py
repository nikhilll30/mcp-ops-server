"""The data key, authenticated encryption, and atomic file writes, for data at rest.

One 256-bit key per deployment (``<PREFIX>DATA_KEY``, base64url) is the root; each use gets
its own subkey via HKDF (``DataKey.subkey("audit-mac")``), so no two purposes share a key.
The key comes from the environment, which a deployment fills from its secret store or KMS
(Part 10's blueprint); it is never in code or in a file the server writes.

Encryption is AES-256-GCM with a random 96-bit nonce per message: a blob is
``nonce || ciphertext || tag``. ``aad`` binds a blob to where it belongs (a file's magic,
an audit record's sequence number), so a blob moved elsewhere fails to decrypt.

    python -m mcp_ops_kit.crypto new-key    # prints a fresh key, once
"""

import base64
import binascii
import os
import secrets
import sys
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from mcp_ops_kit.errors import ConfigError

KEY_BYTES = 32
NONCE_BYTES = 12


class DecryptionError(Exception):
    """A blob did not decrypt: the wrong key, or it was changed or moved."""


class DataKey:
    """The deployment's root data key. Its ``repr`` never shows the key."""

    __slots__ = ("_raw",)

    def __init__(self, raw: bytes) -> None:
        if len(raw) != KEY_BYTES:
            raise ValueError(f"a data key is {KEY_BYTES} bytes, not {len(raw)}")
        self._raw = raw

    @classmethod
    def generate(cls) -> "DataKey":
        return cls(secrets.token_bytes(KEY_BYTES))

    @classmethod
    def from_text(cls, text: str, var: str = "the data key") -> "DataKey":
        """Parse a base64url key (as ``new-key`` prints it). Strict: any character outside
        the alphabet is an error, not skipped. Errors name ``var``, never the value."""
        body = text.strip()
        try:
            raw = base64.b64decode(body + "=" * (-len(body) % 4), altchars=b"-_", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ConfigError(f"{var} is not base64url. {_NEW_KEY_HINT}") from exc
        if len(raw) != KEY_BYTES:
            raise ConfigError(
                f"{var} must decode to {KEY_BYTES} bytes, not {len(raw)}. {_NEW_KEY_HINT}"
            )
        return cls(raw)

    def to_text(self) -> str:
        return base64.urlsafe_b64encode(self._raw).decode().rstrip("=")

    def subkey(self, purpose: str) -> bytes:
        """A 256-bit key for one purpose only, derived with HKDF-SHA256."""
        hkdf = HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=None, info=_info(purpose))
        return hkdf.derive(self._raw)

    def __repr__(self) -> str:
        return "DataKey(<redacted>)"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, DataKey) and secrets.compare_digest(self._raw, other._raw)

    def __hash__(self) -> int:
        return hash(DataKey)


_NEW_KEY_HINT = "Make one with: python -m mcp_ops_kit.crypto new-key"


def _info(purpose: str) -> bytes:
    return b"mcp-ops-kit/v1/" + purpose.encode()


def seal(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    nonce = secrets.token_bytes(NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def unseal(key: bytes, blob: bytes, aad: bytes) -> bytes:
    if len(blob) < NONCE_BYTES + 16:
        raise DecryptionError("too short to be an encrypted blob")
    try:
        return AESGCM(key).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], aad)
    except InvalidTag as exc:
        raise DecryptionError("wrong key, or the data was changed") from exc


def atomic_write(path: str | Path, data: bytes, mode: int = 0o600) -> None:
    """Replace ``path`` with ``data`` so a reader sees the old file or the new one, never
    half of one, and a crash leaves one of the two on disk (written, fsynced, renamed).
    A new file gets ``mode``; a replaced one keeps its own (an operator's chmod stays)."""
    target = Path(path)
    try:
        mode = target.stat().st_mode & 0o777
    except FileNotFoundError:
        pass
    tmp = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        os.fchmod(fd, mode)  # O_CREAT's mode is reduced by the umask; this is not
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    dir_fd = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if args != ["new-key"]:
        sys.exit("usage: python -m mcp_ops_kit.crypto new-key")
    print(DataKey.generate().to_text())  # noqa: T201 - the CLI's output is the key itself


if __name__ == "__main__":
    main()
