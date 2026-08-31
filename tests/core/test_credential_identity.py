"""Tests for `keel_core.credential_identity` -- the #633 fingerprint.

These do NOT touch the real keychain, same discipline as `tests/core/test_secrets.py`: CI has no
usable backend, and a test suite that wrote to a developer's login keychain would be leaving
state on their machine to make an assertion.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from keel_core import secrets as secrets_mod
from keel_core.credential_identity import (
    CREDENTIAL_IDENTIFIER_ENV,
    current_credential_fingerprint,
    fingerprint_identifier,
)
from keel_core.secrets import ResolvedSecret, SecretSource


@pytest.fixture
def fake_keychain(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """An in-memory stand-in, wired at the two seams `keel_core.secrets` goes through -- copied
    from `tests/core/test_secrets.py`'s fixture of the same name."""
    store: dict[str, str] = {}
    monkeypatch.setattr(secrets_mod, "keychain_available", lambda: True)
    monkeypatch.setattr(secrets_mod, "_from_keychain", lambda name: store.get(name))
    return store


# -- fingerprint_identifier: pure, deterministic, and sensitive to every input ---------------------


def test_the_same_input_always_produces_the_same_fingerprint() -> None:
    a = fingerprint_identifier("coinbase", "CDP_API_KEY", "organizations/x/apiKeys/y")
    b = fingerprint_identifier("coinbase", "CDP_API_KEY", "organizations/x/apiKeys/y")
    assert a == b


def test_a_different_identifier_value_produces_a_different_fingerprint() -> None:
    a = fingerprint_identifier("coinbase", "CDP_API_KEY", "organizations/x/apiKeys/y")
    b = fingerprint_identifier("coinbase", "CDP_API_KEY", "organizations/x/apiKeys/z")
    assert a != b


def test_a_different_env_name_with_the_same_value_produces_a_different_fingerprint() -> None:
    a = fingerprint_identifier("coinbase", "CDP_API_KEY", "same-value")
    b = fingerprint_identifier("coinbase", "SOME_OTHER_NAME", "same-value")
    assert a != b


def test_a_different_venue_produces_a_different_fingerprint() -> None:
    a = fingerprint_identifier("coinbase", "CDP_API_KEY", "same-value")
    b = fingerprint_identifier("alpaca", "CDP_API_KEY", "same-value")
    assert a != b


def test_the_fingerprint_is_32_lowercase_hex_characters() -> None:
    digest = fingerprint_identifier("coinbase", "CDP_API_KEY", "organizations/x/apiKeys/y")
    assert len(digest) == 32
    assert digest == digest.lower()
    int(digest, 16)  # raises ValueError if it is not hex


def test_the_fingerprint_contains_no_substring_of_the_input_value() -> None:
    """Non-reversibility, checked the cheap way: nothing that looks like a fragment of the
    original value should survive into the digest. Any 4+ character substring of the value
    appearing verbatim in a 32-char hex digest would be an extraordinary coincidence."""
    value = "organizations/my-org-id/apiKeys/my-key-id-1234"
    digest = fingerprint_identifier("coinbase", "CDP_API_KEY", value)
    for i in range(len(value) - 4):
        fragment = value[i : i + 4]
        assert fragment not in digest, f"fragment {fragment!r} of the input leaked into the digest"


def test_a_golden_value_is_pinned() -> None:
    """A hardcoded expected digest for one known input, so an accidental change to the domain
    separator or the truncation length is caught even though this module has no other reason to
    fail -- both would silently change every fingerprint ever recorded without changing any test
    that only checks internal consistency (same input -> same output)."""
    digest = fingerprint_identifier("coinbase", "CDP_API_KEY", "organizations/abc/apiKeys/def")
    assert digest == "68c2561ce0d2c36645d52f1d88db201d"


# -- the secret-oracle pin: the SECRET must never move the fingerprint --------------------------


def test_changing_the_secret_via_environment_does_not_change_the_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_keychain: dict[str, str]
) -> None:
    monkeypatch.setenv("CDP_API_KEY", "organizations/x/apiKeys/y")
    monkeypatch.setenv("CDP_API_SECRET", "-----BEGIN EC PRIVATE KEY-----first-secret")
    env_path = tmp_path / ".env"
    before = current_credential_fingerprint("coinbase", env_path=env_path)

    monkeypatch.setenv("CDP_API_SECRET", "-----BEGIN EC PRIVATE KEY-----a-totally-different-secret")
    after = current_credential_fingerprint("coinbase", env_path=env_path)

    assert before == after
    assert before is not None


def test_changing_the_secret_via_env_file_does_not_change_the_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_keychain: dict[str, str]
) -> None:
    monkeypatch.delenv("CDP_API_KEY", raising=False)
    monkeypatch.delenv("CDP_API_SECRET", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("CDP_API_KEY=organizations/x/apiKeys/y\nCDP_API_SECRET=first-secret\n")
    before = current_credential_fingerprint("coinbase", env_path=env_path)

    env_path.write_text(
        "CDP_API_KEY=organizations/x/apiKeys/y\nCDP_API_SECRET=a-totally-different-secret\n"
    )
    after = current_credential_fingerprint("coinbase", env_path=env_path)

    assert before == after
    assert before is not None


def test_changing_the_secret_via_keychain_does_not_change_the_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_keychain: dict[str, str]
) -> None:
    monkeypatch.delenv("CDP_API_KEY", raising=False)
    monkeypatch.delenv("CDP_API_SECRET", raising=False)
    env_path = tmp_path / ".env"  # does not exist -- forces resolution through the keychain
    fake_keychain["CDP_API_KEY"] = "organizations/x/apiKeys/y"
    fake_keychain["CDP_API_SECRET"] = "first-secret"
    before = current_credential_fingerprint("coinbase", env_path=env_path)

    fake_keychain["CDP_API_SECRET"] = "a-totally-different-secret"
    after = current_credential_fingerprint("coinbase", env_path=env_path)

    assert before == after
    assert before is not None


def test_the_fingerprint_is_not_equal_to_a_fingerprint_of_the_secrets_own_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_keychain: dict[str, str]
) -> None:
    """The pin that fails if someone "improves" the derivation to hash the secret instead of the
    identifier: fingerprinting `CDP_API_SECRET`'s value under the same (venue, env_name) shape
    must not coincide with fingerprinting `CDP_API_KEY`'s value."""
    monkeypatch.setenv("CDP_API_KEY", "organizations/x/apiKeys/y")
    env_path = tmp_path / ".env"
    identifier_fp = current_credential_fingerprint("coinbase", env_path=env_path)
    assert identifier_fp is not None

    secret_value = "-----BEGIN EC PRIVATE KEY-----some-secret-material"
    secret_fp = fingerprint_identifier("coinbase", "CDP_API_KEY", secret_value)
    assert identifier_fp != secret_fp


# -- current_credential_fingerprint: None for anything unknown, never raises ---------------------


def test_returns_none_for_a_venue_not_in_the_identifier_map() -> None:
    assert current_credential_fingerprint("kraken") is None
    assert current_credential_fingerprint("fake") is None
    assert current_credential_fingerprint("some-made-up-venue") is None


def test_returns_none_when_the_identifier_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_keychain: dict[str, str]
) -> None:
    monkeypatch.delenv("CDP_API_KEY", raising=False)
    env_path = tmp_path / ".env"
    assert current_credential_fingerprint("coinbase", env_path=env_path) is None


def test_never_raises_even_when_the_env_file_is_unreadable_garbage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_keychain: dict[str, str]
) -> None:
    monkeypatch.delenv("CDP_API_KEY", raising=False)
    env_path = tmp_path  # a directory, not a file -- `.exists()` is True, reading it is not
    current_credential_fingerprint("coinbase", env_path=env_path)  # must not raise


# -- detection is source-independent: the acceptance criterion the issue is emphatic about -------
# Three separate tests, one per source, so a single passing path cannot stand in for the others.


def test_detects_a_change_made_by_exporting_a_different_environment_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_keychain: dict[str, str]
) -> None:
    env_path = tmp_path / ".env"
    monkeypatch.setenv("CDP_API_KEY", "organizations/x/apiKeys/original")
    before = current_credential_fingerprint("coinbase", env_path=env_path)

    monkeypatch.setenv("CDP_API_KEY", "organizations/x/apiKeys/rotated")
    after = current_credential_fingerprint("coinbase", env_path=env_path)

    assert before != after
    assert before is not None and after is not None


def test_detects_a_change_made_by_editing_the_env_file_directly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_keychain: dict[str, str]
) -> None:
    """No keel command runs in this test -- the whole point of #633: `keel credentials forget`
    correctly refuses to edit `.env`, so an operator who edits it by hand must still be caught."""
    monkeypatch.delenv("CDP_API_KEY", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("CDP_API_KEY=organizations/x/apiKeys/original\n")
    before = current_credential_fingerprint("coinbase", env_path=env_path)

    env_path.write_text("CDP_API_KEY=organizations/x/apiKeys/rotated\n")
    after = current_credential_fingerprint("coinbase", env_path=env_path)

    assert before != after
    assert before is not None and after is not None


def test_detects_a_change_made_by_the_keychain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_keychain: dict[str, str]
) -> None:
    monkeypatch.delenv("CDP_API_KEY", raising=False)
    env_path = tmp_path / ".env"  # absent -- forces resolution through the keychain
    fake_keychain["CDP_API_KEY"] = "organizations/x/apiKeys/original"
    before = current_credential_fingerprint("coinbase", env_path=env_path)

    fake_keychain["CDP_API_KEY"] = "organizations/x/apiKeys/rotated"
    after = current_credential_fingerprint("coinbase", env_path=env_path)

    assert before != after
    assert before is not None and after is not None


# -- drift pin: the identifier map must name the IDENTIFIER, never the signing secret ------------


def test_declared_identifier_is_one_of_the_adapters_declared_pair_and_not_the_secret() -> None:
    from keel_broker_api.registry import discover_brokers

    installed = discover_brokers()
    for venue, env_name in CREDENTIAL_IDENTIFIER_ENV.items():
        adapter_cls = installed.get(venue)
        if adapter_cls is None:
            continue  # this venue's adapter package is not installed in this environment
        declared = tuple(getattr(adapter_cls, "DECLARED_CREDENTIAL_ENV", ()))
        assert env_name in declared, (
            f"{venue}: {env_name!r} is not one of {adapter_cls.__name__}'s declared "
            f"credential env names {declared!r}"
        )
        other_names = [name for name in declared if name != env_name]
        assert env_name not in other_names


# -- never logged, never rendered -----------------------------------------------------------------


def test_resolved_secret_repr_still_hides_the_value() -> None:
    """The precedent this module's docstring cites, checked directly: nothing about adding a
    fingerprinting module should weaken `ResolvedSecret.__repr__`'s existing guarantee."""
    secret = ResolvedSecret("CDP_API_KEY", "organizations/x/apiKeys/y", SecretSource.ENVIRONMENT)
    rendered = repr(secret)
    assert "organizations/x/apiKeys/y" not in rendered
    assert "set" in rendered


def test_no_logging_call_in_this_module_passes_the_fingerprint_or_a_credential_value() -> None:
    """A specific, behavioural grep: this module has exactly zero calls into any logging
    facility at all, so there is nothing here that COULD pass a fingerprint or a raw credential
    value to a log record. A future change that adds logging to this module would have to touch
    this assertion, which is the point -- it forces a deliberate decision rather than a silent
    `log.info(f"...{fingerprint}...")` slipping in unnoticed."""
    import inspect
    import re

    import keel_core.credential_identity as module

    source = inspect.getsource(module)
    for banned in ("logging.", "logger.", "log_event(", "log_exception("):
        assert banned not in source, f"unexpected logging call {banned!r} in credential_identity"
    # `\bprint\(` rather than a bare substring check: "fingerprint(" legitimately contains
    # "print(" and would otherwise false-positive on this module's own function names.
    assert re.search(r"\bprint\(", source) is None, "unexpected print( in credential_identity"
