"""Dangerous-action authorization gate (main spec §14).

A local passphrase — stored as a **stdlib `hashlib.scrypt` hash + random salt, never
plaintext** — gates only the small set of *dangerous* actions: arming bypass/autonomous
mode, raising caps above config maxima, disabling the kill-switch/resume, and unlocking
the secrets vault. Read-only commands and confirm-mode trades need no passphrase at all.

This is local *authorization*, not a login: on a single-user machine the real security
boundary is the OS account + full-disk encryption. The gate is defense-in-depth — it
forces intentionality and blocks casual/accidental/shoulder-surf misuse of the autonomy
capability. It does not stop an attacker who already holds the OS account.

Failed attempts are tracked and rate-limited with an exponential backoff lockout,
persisted alongside the hash so restarts don't reset the counter.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path

DANGEROUS_ACTIONS = frozenset(
    {"arm_bypass", "raise_caps", "disable_killswitch", "unlock_vault"}
)

# scrypt cost parameters (interactive-login-grade; OWASP minimum: N=2**14, r=8, p=1).
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16

# Rate limiting: lock out after MAX_ATTEMPTS consecutive failures, with the lockout
# window growing exponentially for each additional failure beyond the threshold.
MAX_ATTEMPTS = 5
_LOCKOUT_BASE_SECONDS = 30.0

_DEFAULT_PATH = "authz.json"


class AuthzError(Exception):
    """Raised by `require()` when an action is dangerous and authorization fails."""


def _scrypt_hash(
    passphrase: str,
    salt: bytes,
    n: int = _SCRYPT_N,
    r: int = _SCRYPT_R,
    p: int = _SCRYPT_P,
    dklen: int = _SCRYPT_DKLEN,
) -> bytes:
    return hashlib.scrypt(
        passphrase.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=dklen,
    )


def _load_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    with path.open() as f:
        return json.load(f)


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))
    if os.name != "nt":
        os.chmod(path, 0o600)


def set_passphrase(passphrase: str, path: str | Path = _DEFAULT_PATH) -> None:
    """Hash `passphrase` with a fresh random salt and persist it at `path`.

    Overwrites any existing state (including the rate-limit counters) — setting a new
    passphrase starts a clean slate. The passphrase itself is never written to disk.
    """
    path = Path(path)
    salt = os.urandom(_SALT_BYTES)
    digest = _scrypt_hash(passphrase, salt)
    state = {
        "salt": salt.hex(),
        "hash": digest.hex(),
        "n": _SCRYPT_N,
        "r": _SCRYPT_R,
        "p": _SCRYPT_P,
        "dklen": _SCRYPT_DKLEN,
        "failed_attempts": 0,
        "locked_until": 0.0,
    }
    _save_state(path, state)


def verify(passphrase: str, path: str | Path = _DEFAULT_PATH, now: float | None = None) -> bool:
    """Check `passphrase` against the hash stored at `path`.

    Returns `False` (never raises) when: no passphrase has been set yet, the passphrase
    is wrong, or the gate is currently locked out from too many recent failures. `now`
    defaults to the wall clock; callers (and tests) may pass an explicit epoch timestamp.
    """
    path = Path(path)
    now = time.time() if now is None else now

    state = _load_state(path)
    if state is None:
        return False

    if now < state.get("locked_until", 0.0):
        return False  # rate limited — still inside the lockout window

    computed = _scrypt_hash(
        passphrase,
        bytes.fromhex(state["salt"]),
        n=state["n"],
        r=state["r"],
        p=state["p"],
        dklen=state["dklen"],
    )
    stored = bytes.fromhex(state["hash"])
    ok = hmac.compare_digest(computed, stored)

    if ok:
        state["failed_attempts"] = 0
        state["locked_until"] = 0.0
        _save_state(path, state)
        return True

    state["failed_attempts"] = state.get("failed_attempts", 0) + 1
    if state["failed_attempts"] >= MAX_ATTEMPTS:
        excess = state["failed_attempts"] - MAX_ATTEMPTS
        lockout_seconds = _LOCKOUT_BASE_SECONDS * (2**excess)
        state["locked_until"] = now + lockout_seconds
    _save_state(path, state)
    return False


def require(
    action: str,
    passphrase: str,
    path: str | Path = _DEFAULT_PATH,
    now: float | None = None,
) -> None:
    """Enforce the authorization gate for `action`.

    No-ops for anything outside `DANGEROUS_ACTIONS` (read-only/confirm-mode actions need
    no passphrase). For a dangerous action, raises `AuthzError` unless `passphrase`
    verifies — including while rate-limit locked out, even with the correct passphrase.
    """
    if action not in DANGEROUS_ACTIONS:
        return
    if not verify(passphrase, path=path, now=now):
        raise AuthzError(f"authorization denied for dangerous action {action!r}")
