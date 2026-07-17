"""Portable AES-GCM encrypted secrets vault (main spec §14 Part A).

The CDP API key/secret (and any future remote-control token) live in a single file,
`secrets.enc`, unlocked by a master passphrase: passphrase -> **scrypt KDF** -> symmetric key ->
**AES-GCM** encrypt/decrypt of a JSON secrets blob. Deliberately *not* the OS keychain — this file
is copyable between machines, which is the portability the design calls for.

On-disk layout (all bytes, no plaintext):
    MAGIC (7 bytes) | salt (16 bytes) | nonce (12 bytes) | AES-GCM ciphertext+tag

`MAGIC` is also passed as AES-GCM associated data, so tampering with it (or any other byte in the
file) fails authentication rather than silently decrypting garbage.

Secrets are never logged: no function in this module writes secret values to a log, exception
message, or `repr()`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

_MAGIC = b"HCBVLT1"
_SALT_LEN = 16
_NONCE_LEN = 12
_KEY_LEN = 32

# scrypt cost parameters for an interactive unlock (local CLI, not a server). ~16MB of memory.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1

DEFAULT_VAULT_PATH = "secrets.enc"


class VaultError(Exception):
    """Raised when a vault cannot be sealed/unlocked: wrong passphrase, tampering, or a missing
    or corrupt vault file. Never includes secret values in its message.
    """


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=_KEY_LEN, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return kdf.derive(passphrase.encode("utf-8"))


def save_vault(secrets: dict, passphrase: str, path: str | Path = DEFAULT_VAULT_PATH) -> None:
    """Encrypt `secrets` (a JSON-serializable dict) with a passphrase-derived key and write the
    result to `path`. A fresh random salt + nonce are generated on every call, so encrypting the
    same secrets twice yields different ciphertext. The file is `chmod 600`'d.
    """
    path = Path(path)
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    key = _derive_key(passphrase, salt)

    plaintext = json.dumps(secrets).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, associated_data=_MAGIC)

    path.write_bytes(_MAGIC + salt + nonce + ciphertext)
    path.chmod(0o600)


def load_vault(passphrase: str, path: str | Path = DEFAULT_VAULT_PATH) -> dict:
    """Decrypt the vault at `path` with `passphrase` and return the secrets dict.

    Raises `VaultError` for a missing file, a wrong passphrase, or a tampered/corrupt file.
    """
    path = Path(path)
    if not path.exists():
        raise VaultError(f"vault not found at {path}")

    blob = path.read_bytes()
    if not blob.startswith(_MAGIC):
        raise VaultError("not a valid secrets vault file")

    rest = blob[len(_MAGIC) :]
    if len(rest) < _SALT_LEN + _NONCE_LEN:
        raise VaultError("vault file is truncated or corrupt")

    salt = rest[:_SALT_LEN]
    nonce = rest[_SALT_LEN : _SALT_LEN + _NONCE_LEN]
    ciphertext = rest[_SALT_LEN + _NONCE_LEN :]

    key = _derive_key(passphrase, salt)
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, associated_data=_MAGIC)
    except InvalidTag as exc:
        raise VaultError("wrong passphrase or tampered vault file") from exc

    return json.loads(plaintext)


def migrate_from_env(
    env_path: str | Path = ".env",
    *,
    passphrase: str,
    path: str | Path = DEFAULT_VAULT_PATH,
) -> None:
    """Read CDP secrets out of a git-ignored `.env` file and seal them into the vault at `path`.

    Delegates to `config.load_secrets`, so it inherits the same absent/empty-file handling
    (returns `{}` rather than raising, so a fresh vault with no secrets configured is still
    valid). Does not delete or modify `env_path` — the caller decides when it's safe to remove it.
    """
    from keel.config import load_secrets

    secrets = load_secrets(env_path)
    save_vault(secrets, passphrase, path=path)
