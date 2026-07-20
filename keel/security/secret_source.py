"""Decide WHERE the live API credentials come from, and fail closed when unsure.

Two sources, in a deliberate precedence:

1. **The encrypted vault** (`security/secrets.py`), when `secrets.enc` exists. This is the
   intended home for a **trade-enabled** key -- a plaintext `.env` is the wrong risk class for a
   credential that can move money.
2. **`.env`**, when no vault exists. Fine for the read-only key and for anyone who has not opted
   into the vault; preserves the pre-vault behaviour exactly.

⛔ **The precedence is not a fallback chain.** If a vault EXISTS but cannot be unlocked -- no
passphrase, wrong passphrase, tampered file -- this raises rather than quietly reading `.env`.
Falling through would let a stale or lower-privilege `.env` key silently stand in for the vault
the operator deliberately created, which is the exact downgrade the vault exists to prevent.

**The passphrase never comes from a config file or the DB.** It is read from the environment
variable `KEEL_VAULT_PASSPHRASE` (for a headless agent loop) or, failing that, an interactive TTY
prompt. A non-interactive run with no env var and a vault present fails closed with guidance --
it does not guess and does not degrade to `.env`.

Secret VALUES never appear in a log, exception message, or return-path other than the dict handed
to the broker constructor.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from keel.security.secrets import DEFAULT_VAULT_PATH, VaultError, load_vault

PASSPHRASE_ENV = "KEEL_VAULT_PASSPHRASE"


class SecretResolutionError(Exception):
    """A vault exists but could not be unlocked. Never contains a secret or a passphrase."""


def vault_exists(vault_path: str | Path = DEFAULT_VAULT_PATH) -> bool:
    return Path(vault_path).exists()


def _resolve_passphrase(explicit: str | None, prompt: bool) -> str | None:
    """Passphrase from (1) an explicit argument, (2) `KEEL_VAULT_PASSPHRASE`, (3) a TTY prompt.

    Returns `None` when none is available and no prompt is possible -- the caller turns that into
    a fail-closed error, rather than this function inventing an empty passphrase that would just
    produce a confusing "wrong passphrase" downstream.
    """
    if explicit:
        return explicit
    from_env = os.environ.get(PASSPHRASE_ENV)
    if from_env:
        return from_env
    if prompt and sys.stdin is not None and sys.stdin.isatty():
        import click

        return click.prompt("Vault passphrase", hide_input=True)
    return None


def resolve_secrets(
    *,
    vault_path: str | Path = DEFAULT_VAULT_PATH,
    env_path: str | Path = ".env",
    passphrase: str | None = None,
    allow_prompt: bool = True,
) -> dict:
    """Return `{"api_key": ..., "api_secret": ...}` (or `{}` if nothing is configured).

    Vault-present-but-unlockable raises `SecretResolutionError`; it never silently reads `.env`.
    """
    if vault_exists(vault_path):
        resolved = _resolve_passphrase(passphrase, allow_prompt)
        if resolved is None:
            raise SecretResolutionError(
                f"a vault exists at {vault_path} but no passphrase is available. Set "
                f"{PASSPHRASE_ENV} or run interactively. Refusing to fall back to .env -- a "
                "vault that cannot be unlocked must not be silently downgraded."
            )
        try:
            return load_vault(resolved, path=vault_path)
        except VaultError as exc:
            # The VaultError message never contains the secret or the passphrase; safe to chain.
            raise SecretResolutionError(
                f"vault at {vault_path} could not be unlocked: {exc}"
            ) from exc

    # No vault: the pre-vault path, unchanged. `.env` (or empty for offline commands).
    from keel.config import load_secrets

    return load_secrets(env_path)
