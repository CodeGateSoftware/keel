"""Tests for keel.security.authz: set_passphrase, verify, require, AuthzError.

Main spec §14 "Dangerous-action authorization gate": a passphrase (stored as a scrypt hash,
rate-limited) is required only for {arm_bypass, raise_caps, disable_killswitch, unlock_vault}.
Read-only/confirm-mode actions require nothing.
"""

from __future__ import annotations

import json
import os

import pytest

from keel.security.authz import (
    DANGEROUS_ACTIONS,
    MAX_ATTEMPTS,
    AuthzError,
    require,
    set_passphrase,
    verify,
)


@pytest.fixture
def authz_path(tmp_path):
    return tmp_path / "authz.json"


def test_set_passphrase_persists_hash_not_plaintext(authz_path):
    set_passphrase("correct horse battery staple", path=authz_path)

    raw = authz_path.read_text()
    assert "correct horse battery staple" not in raw

    state = json.loads(raw)
    assert "hash" in state
    assert "salt" in state
    assert state["hash"] != "correct horse battery staple"


def test_set_passphrase_uses_a_random_salt_per_call(authz_path, tmp_path):
    other_path = tmp_path / "authz2.json"

    set_passphrase("same passphrase", path=authz_path)
    set_passphrase("same passphrase", path=other_path)

    state_a = json.loads(authz_path.read_text())
    state_b = json.loads(other_path.read_text())
    assert state_a["salt"] != state_b["salt"]
    assert state_a["hash"] != state_b["hash"]


def test_verify_correct_passphrase_returns_true(authz_path):
    set_passphrase("swordfish", path=authz_path)

    assert verify("swordfish", path=authz_path) is True


def test_verify_wrong_passphrase_returns_false(authz_path):
    set_passphrase("swordfish", path=authz_path)

    assert verify("wrong-guess", path=authz_path) is False


def test_verify_with_no_passphrase_configured_returns_false(authz_path):
    assert not authz_path.exists()

    assert verify("anything", path=authz_path) is False


def test_verify_resets_failed_attempts_after_success(authz_path):
    set_passphrase("swordfish", path=authz_path)

    verify("wrong-1", path=authz_path)
    verify("wrong-2", path=authz_path)
    assert verify("swordfish", path=authz_path) is True

    state = json.loads(authz_path.read_text())
    assert state["failed_attempts"] == 0


def test_require_correct_passphrase_passes_for_dangerous_action(authz_path):
    set_passphrase("swordfish", path=authz_path)

    require("arm_bypass", "swordfish", path=authz_path)  # must not raise


def test_require_wrong_passphrase_raises_authzerror(authz_path):
    set_passphrase("swordfish", path=authz_path)

    with pytest.raises(AuthzError, match="arm_bypass"):
        require("arm_bypass", "wrong-guess", path=authz_path)


@pytest.mark.parametrize("action", sorted(DANGEROUS_ACTIONS))
def test_require_gates_every_dangerous_action(authz_path, action):
    set_passphrase("swordfish", path=authz_path)

    require(action, "swordfish", path=authz_path)  # correct passphrase passes
    with pytest.raises(AuthzError):
        require(action, "wrong-guess", path=authz_path)


def test_require_readonly_action_needs_no_passphrase_even_when_unset(authz_path):
    assert not authz_path.exists()

    require("view_status", "", path=authz_path)  # must not raise


def test_require_readonly_action_needs_no_passphrase_even_when_wrong(authz_path):
    set_passphrase("swordfish", path=authz_path)

    require("confirm_trade", "totally-wrong", path=authz_path)  # must not raise


def test_dangerous_actions_constant_matches_spec():
    assert DANGEROUS_ACTIONS == frozenset(
        {"arm_bypass", "raise_caps", "disable_killswitch", "unlock_vault"}
    )


def test_require_with_no_passphrase_configured_raises_for_dangerous_action(authz_path):
    assert not authz_path.exists()

    with pytest.raises(AuthzError):
        require("unlock_vault", "whatever", path=authz_path)


def test_n_wrong_attempts_locks_out_further_attempts(authz_path):
    set_passphrase("swordfish", path=authz_path)
    now = 1_000_000.0

    for i in range(MAX_ATTEMPTS):
        assert verify(f"wrong-{i}", path=authz_path, now=now) is False

    # Locked out now: even the *correct* passphrase is rejected while locked.
    assert verify("swordfish", path=authz_path, now=now) is False

    state = json.loads(authz_path.read_text())
    assert state["locked_until"] > now


def test_lockout_expires_after_backoff_window(authz_path):
    set_passphrase("swordfish", path=authz_path)
    now = 2_000_000.0

    for i in range(MAX_ATTEMPTS):
        verify(f"wrong-{i}", path=authz_path, now=now)
    assert verify("swordfish", path=authz_path, now=now) is False

    state = json.loads(authz_path.read_text())
    locked_until = state["locked_until"]

    # Still locked just before the window elapses.
    assert verify("swordfish", path=authz_path, now=locked_until - 1) is False
    # Free again once the backoff window has passed.
    assert verify("swordfish", path=authz_path, now=locked_until + 1) is True


def test_require_raises_during_lockout_even_with_correct_passphrase(authz_path):
    set_passphrase("swordfish", path=authz_path)
    now = 3_000_000.0

    for i in range(MAX_ATTEMPTS):
        verify(f"wrong-{i}", path=authz_path, now=now)

    with pytest.raises(AuthzError):
        require("raise_caps", "swordfish", path=authz_path, now=now)


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions only")
def test_authz_file_is_not_world_readable(authz_path):
    set_passphrase("swordfish", path=authz_path)

    mode = authz_path.stat().st_mode & 0o777
    assert mode & 0o077 == 0
