"""Tests for halal_cb.security.secrets: the portable AES-GCM encrypted secrets vault.

Main spec §14 Part A: master passphrase -> scrypt KDF -> key -> AES-GCM encrypt/decrypt a JSON
secrets blob in `secrets.enc`, copyable between machines.
"""

from __future__ import annotations

import stat

import pytest

from halal_cb.security.secrets import VaultError, load_vault, migrate_from_env, save_vault

SECRETS = {
    "api_key": "organizations/abc/apiKeys/def",
    "api_secret": "-----BEGIN EC PRIVATE KEY-----\nverysecret\n-----END EC PRIVATE KEY-----",
}


def test_save_then_load_vault_round_trips_secrets(tmp_path):
    vault_path = tmp_path / "secrets.enc"

    save_vault(SECRETS, "correct horse battery staple", path=vault_path)
    loaded = load_vault("correct horse battery staple", path=vault_path)

    assert loaded == SECRETS


def test_save_vault_chmods_file_600(tmp_path):
    vault_path = tmp_path / "secrets.enc"

    save_vault(SECRETS, "correct horse battery staple", path=vault_path)

    mode = stat.S_IMODE(vault_path.stat().st_mode)
    assert mode == 0o600


def test_load_vault_with_wrong_passphrase_raises_vaulterror(tmp_path):
    vault_path = tmp_path / "secrets.enc"
    save_vault(SECRETS, "correct horse battery staple", path=vault_path)

    with pytest.raises(VaultError):
        load_vault("wrong passphrase", path=vault_path)


def test_load_vault_with_tampered_file_raises_vaulterror(tmp_path):
    vault_path = tmp_path / "secrets.enc"
    save_vault(SECRETS, "correct horse battery staple", path=vault_path)

    raw = bytearray(vault_path.read_bytes())
    raw[-1] ^= 0xFF  # flip the last byte of the ciphertext/tag
    vault_path.write_bytes(bytes(raw))

    with pytest.raises(VaultError):
        load_vault("correct horse battery staple", path=vault_path)


def test_load_vault_missing_file_raises_vaulterror(tmp_path):
    vault_path = tmp_path / "does-not-exist.enc"

    with pytest.raises(VaultError):
        load_vault("any passphrase", path=vault_path)


def test_on_disk_blob_is_ciphertext_not_plaintext_values(tmp_path):
    vault_path = tmp_path / "secrets.enc"

    save_vault(SECRETS, "correct horse battery staple", path=vault_path)

    raw = vault_path.read_bytes()
    assert b"organizations/abc/apiKeys/def" not in raw
    assert b"BEGIN EC PRIVATE KEY" not in raw
    assert b"verysecret" not in raw


def test_two_saves_of_the_same_secrets_produce_different_ciphertext(tmp_path):
    """Fresh salt+nonce per save (no key/nonce reuse) even for identical plaintext."""
    path_a = tmp_path / "a.enc"
    path_b = tmp_path / "b.enc"

    save_vault(SECRETS, "correct horse battery staple", path=path_a)
    save_vault(SECRETS, "correct horse battery staple", path=path_b)

    assert path_a.read_bytes() != path_b.read_bytes()


def test_migrate_from_env_seals_env_secrets_into_vault(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text('CDP_API_KEY="organizations/abc/apiKeys/def"\nCDP_API_SECRET="shh-dont-log-me"\n')
    vault_path = tmp_path / "secrets.enc"

    migrate_from_env(env_path=env_path, passphrase="correct horse battery staple", path=vault_path)

    loaded = load_vault("correct horse battery staple", path=vault_path)
    assert loaded == {
        "api_key": "organizations/abc/apiKeys/def",
        "api_secret": "shh-dont-log-me",
    }


def test_migrate_from_env_missing_file_yields_empty_vault(tmp_path):
    env_path = tmp_path / ".env"
    vault_path = tmp_path / "secrets.enc"

    migrate_from_env(env_path=env_path, passphrase="correct horse battery staple", path=vault_path)

    assert load_vault("correct horse battery staple", path=vault_path) == {}
