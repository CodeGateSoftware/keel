"""Where a credential comes from, and what must never happen to it on the way (#437).

These do NOT touch the real keychain. CI has no usable backend, and a test suite that wrote to a
developer's login keychain would be leaving state on their machine to make an assertion.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from keel_core import secrets as secrets_mod
from keel_core.secrets import (
    ResolvedSecret,
    SecretSource,
    delete_secret,
    read_secret,
    store_secret,
)


@pytest.fixture
def fake_keychain(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """An in-memory stand-in, wired at the two seams every function here goes through."""
    store: dict[str, str] = {}
    monkeypatch.setattr(secrets_mod, "keychain_available", lambda: True)
    monkeypatch.setattr(secrets_mod, "_from_keychain", lambda name: store.get(name))
    return store


# -- precedence, which is the whole design -----------------------------------------------------


def test_the_environment_wins_over_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_keychain: dict[str, str]
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SOME_KEY=from-file\n")
    fake_keychain["SOME_KEY"] = "from-keychain"
    monkeypatch.setenv("SOME_KEY", "from-environment")

    resolved = read_secret("SOME_KEY", env_path=env_file)
    assert resolved.value == "from-environment"
    assert resolved.source is SecretSource.ENVIRONMENT


def test_the_env_file_wins_over_the_keychain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_keychain: dict[str, str]
) -> None:
    """Deliberately, and it is the decision that keeps every existing deployment byte-identical.

    A value the operator can SEE beats one they cannot when the two disagree. A stale keychain
    entry silently overriding an edited `.env` is a debugging session nobody should have to have.
    """
    monkeypatch.delenv("SOME_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("SOME_KEY=from-file\n")
    fake_keychain["SOME_KEY"] = "from-keychain"

    resolved = read_secret("SOME_KEY", env_path=env_file)
    assert resolved.value == "from-file"
    assert resolved.source is SecretSource.ENV_FILE


def test_the_keychain_answers_where_the_file_is_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_keychain: dict[str, str]
) -> None:
    """The case the desktop product depends on: no terminal, so no `.env` was ever created."""
    monkeypatch.delenv("SOME_KEY", raising=False)
    fake_keychain["SOME_KEY"] = "from-keychain"

    resolved = read_secret("SOME_KEY", env_path=tmp_path / "does-not-exist")
    assert resolved.value == "from-keychain"
    assert resolved.source is SecretSource.KEYCHAIN


def test_nothing_anywhere_is_absent_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_keychain: dict[str, str]
) -> None:
    monkeypatch.delenv("SOME_KEY", raising=False)
    resolved = read_secret("SOME_KEY", env_path=tmp_path / "nope")
    assert resolved.source is SecretSource.ABSENT
    assert not resolved.found


def test_an_empty_value_never_satisfies_a_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_keychain: dict[str, str]
) -> None:
    """An empty environment variable is how a credential goes missing while looking configured --
    it must fall through to the next source, not shadow it with nothing."""
    monkeypatch.setenv("SOME_KEY", "")
    fake_keychain["SOME_KEY"] = "from-keychain"
    assert read_secret("SOME_KEY", env_path=tmp_path / "nope").source is SecretSource.KEYCHAIN


# -- what must never happen to a secret --------------------------------------------------------


def test_repr_never_contains_the_value() -> None:
    """A dataclass that prints its own secret in a traceback has published it to every log that
    traceback reaches -- and tracebacks travel further than anything else in a program."""
    resolved = ResolvedSecret("SOME_KEY", "super-secret-value", SecretSource.KEYCHAIN)
    text = repr(resolved)
    assert "super-secret-value" not in text
    assert "SOME_KEY" in text and "keychain" in text
    assert "set" in text


def test_str_and_format_do_not_leak_either() -> None:
    """`repr` is not the only way a value reaches a log line."""
    resolved = ResolvedSecret("SOME_KEY", "super-secret-value", SecretSource.KEYCHAIN)
    assert "super-secret-value" not in str(resolved)
    assert "super-secret-value" not in f"{resolved}"
    assert "super-secret-value" not in f"{resolved!r}"


def test_storing_an_empty_value_is_refused(fake_keychain: dict[str, str]) -> None:
    """Storing "" would look like success and read back as absent."""
    with pytest.raises(ValueError):
        store_secret("SOME_KEY", "")


def test_storing_without_a_keychain_raises_rather_than_discarding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A form that appears to save a credential and does not is worse than one that refuses: the
    operator only finds out at the first request that needed it."""
    monkeypatch.setattr(secrets_mod, "keychain_available", lambda: False)
    with pytest.raises(RuntimeError, match="NOT saved"):
        store_secret("SOME_KEY", "value")


def test_a_locked_or_broken_keychain_reads_as_absent_rather_than_crashing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A locked keychain, a denied prompt, a backend that broke after being selected -- none is a
    reason to take the process down. The caller falls through to "absent" and says so, which is
    the same message it would give for a credential that was never set.

    Patched at `keyring.get_password` rather than at `_from_keychain`, because `_from_keychain`
    IS the code under test: swapping it out would test the stub instead.
    """
    keyring = pytest.importorskip("keyring")
    monkeypatch.delenv("SOME_KEY", raising=False)
    monkeypatch.setattr(secrets_mod, "keychain_available", lambda: True)

    def _boom(_service: str, _name: str) -> str:
        raise RuntimeError("the keychain is locked")

    monkeypatch.setattr(keyring, "get_password", _boom)

    assert secrets_mod._from_keychain("SOME_KEY") is None
    assert read_secret("SOME_KEY", env_path=tmp_path / "nope").source is SecretSource.ABSENT


def test_the_broken_keychain_test_is_not_vacuous(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one above passes trivially if `_from_keychain` never calls `get_password`. This proves
    it does."""
    keyring = pytest.importorskip("keyring")
    monkeypatch.setattr(secrets_mod, "keychain_available", lambda: True)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        keyring, "get_password", lambda service, name: calls.append((service, name)) or "v"
    )
    assert secrets_mod._from_keychain("SOME_KEY") == "v"
    assert calls == [(secrets_mod.KEYCHAIN_SERVICE, "SOME_KEY")]


def test_delete_never_touches_the_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_keychain: dict[str, str]
) -> None:
    """A `.env` is the operator's own artifact. Deleting a line out of a file someone hand-wrote,
    on their behalf, is not something a credential command should do."""
    monkeypatch.delenv("SOME_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("SOME_KEY=from-file\nOTHER=keep-me\n")
    before = env_file.read_text()

    monkeypatch.setattr(secrets_mod, "delete_secret", lambda name: True)
    delete_secret("SOME_KEY")

    assert env_file.read_text() == before
    assert read_secret("SOME_KEY", env_path=env_file).source is SecretSource.ENV_FILE


def test_no_keychain_means_delete_reports_nothing_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secrets_mod, "keychain_available", lambda: False)
    assert delete_secret("SOME_KEY") is False


# -- backward compatibility ---------------------------------------------------------------------


def test_load_secrets_still_reads_a_plain_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every pre-existing deployment must behave byte-identically."""
    from keel_core.config import load_secrets

    monkeypatch.delenv("CDP_API_KEY", raising=False)
    monkeypatch.delenv("CDP_API_SECRET", raising=False)
    monkeypatch.setattr(secrets_mod, "keychain_available", lambda: False)

    env_file = tmp_path / ".env"
    env_file.write_text("CDP_API_KEY=k\nCDP_API_SECRET=s\n")
    assert load_secrets(env_file) == {"api_key": "k", "api_secret": "s"}


def test_load_secrets_is_empty_when_nothing_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Offline commands keep working with no secrets at all."""
    from keel_core.config import load_secrets

    monkeypatch.delenv("CDP_API_KEY", raising=False)
    monkeypatch.delenv("CDP_API_SECRET", raising=False)
    monkeypatch.setattr(secrets_mod, "keychain_available", lambda: False)
    assert load_secrets(tmp_path / "absent") == {}


def test_load_secrets_falls_back_to_the_keychain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_keychain: dict[str, str]
) -> None:
    """The desktop case: a machine with no `.env` at all."""
    from keel_core.config import load_secrets

    monkeypatch.delenv("CDP_API_KEY", raising=False)
    monkeypatch.delenv("CDP_API_SECRET", raising=False)
    fake_keychain["CDP_API_KEY"] = "k"
    fake_keychain["CDP_API_SECRET"] = "s"
    assert load_secrets(tmp_path / "absent") == {"api_key": "k", "api_secret": "s"}
