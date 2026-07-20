"""Where live credentials come from, and the fail-closed precedence (spec §14)."""

from __future__ import annotations

import pytest

from keel.security import secret_source
from keel.security.secret_source import SecretResolutionError, resolve_secrets
from keel.security.secrets import save_vault


def _env(tmp_path, key="k", secret="s"):
    p = tmp_path / ".env"
    p.write_text(f"CDP_API_KEY={key}\nCDP_API_SECRET={secret}\n")
    return str(p)


def _vault(tmp_path, passphrase="correct-horse", key="vk", secret="vs"):
    p = tmp_path / "secrets.enc"
    save_vault({"api_key": key, "api_secret": secret}, passphrase, path=p)
    return str(p)


def test_no_vault_falls_back_to_env(tmp_path):
    env = _env(tmp_path, key="envkey")
    out = resolve_secrets(vault_path=str(tmp_path / "absent.enc"), env_path=env)
    assert out["api_key"] == "envkey"


def test_no_vault_and_no_env_is_empty_not_an_error(tmp_path):
    out = resolve_secrets(vault_path=str(tmp_path / "absent.enc"), env_path=str(tmp_path / "x"))
    assert out == {}


def test_a_vault_takes_precedence_over_env(tmp_path):
    """The whole point: once a vault exists, .env is not consulted -- even if it has keys."""
    env = _env(tmp_path, key="envkey")
    vault = _vault(tmp_path, passphrase="pw", key="vaultkey")
    out = resolve_secrets(vault_path=vault, env_path=env, passphrase="pw")
    assert out["api_key"] == "vaultkey"


def test_a_vault_that_cannot_be_unlocked_RAISES_never_reads_env(tmp_path):
    """⛔ The downgrade this module exists to prevent.

    A vault present but not unlockable must fail closed, not silently stand in a stale/
    lower-privilege .env key for the vault the operator deliberately created.
    """
    env = _env(tmp_path, key="envkey")
    vault = _vault(tmp_path, passphrase="right")

    with pytest.raises(SecretResolutionError):
        resolve_secrets(vault_path=vault, env_path=env, passphrase="WRONG", allow_prompt=False)


def test_a_vault_with_no_passphrase_available_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv(secret_source.PASSPHRASE_ENV, raising=False)
    vault = _vault(tmp_path, passphrase="pw")
    with pytest.raises(SecretResolutionError, match="no passphrase"):
        resolve_secrets(vault_path=vault, env_path=_env(tmp_path), allow_prompt=False)


def test_the_passphrase_env_var_unlocks_a_headless_run(tmp_path, monkeypatch):
    vault = _vault(tmp_path, passphrase="pw", key="headless")
    monkeypatch.setenv(secret_source.PASSPHRASE_ENV, "pw")
    out = resolve_secrets(vault_path=vault, env_path=_env(tmp_path), allow_prompt=False)
    assert out["api_key"] == "headless"


def test_an_explicit_passphrase_beats_the_env_var(tmp_path, monkeypatch):
    vault = _vault(tmp_path, passphrase="explicit-pw", key="x")
    monkeypatch.setenv(secret_source.PASSPHRASE_ENV, "wrong-env-pw")
    out = resolve_secrets(vault_path=vault, env_path=_env(tmp_path), passphrase="explicit-pw")
    assert out["api_key"] == "x"


def test_the_error_never_contains_the_passphrase_or_a_secret(tmp_path):
    vault = _vault(tmp_path, passphrase="right", key="topsecretkey")
    try:
        resolve_secrets(vault_path=vault, passphrase="hunter2secretpw", allow_prompt=False)
    except SecretResolutionError as exc:
        assert "hunter2secretpw" not in str(exc)
        assert "topsecretkey" not in str(exc)
    else:
        raise AssertionError("expected a resolution error")
