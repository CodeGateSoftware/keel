"""`keel credentials` -- the shape of each subcommand is a security decision, not a UI one."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from click.testing import CliRunner
from keel_core import secrets as secrets_mod

from keel.cli import cli
from keel.commands import credentials as credentials_mod

SECRET = "s3cret-value-that-must-never-be-printed"


@pytest.fixture(autouse=True)
def fake_keychain(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    store: dict[str, str] = {}
    monkeypatch.setattr(secrets_mod, "keychain_available", lambda: True)
    monkeypatch.setattr(secrets_mod, "_from_keychain", lambda name: store.get(name))
    monkeypatch.setattr(
        secrets_mod, "store_secret", lambda name, value: store.__setitem__(name, value)
    )
    monkeypatch.setattr(
        secrets_mod, "delete_secret", lambda name: store.pop(name, None) is not None
    )
    monkeypatch.setattr(credentials_mod, "store_secret", secrets_mod.store_secret)
    monkeypatch.setattr(credentials_mod, "delete_secret", secrets_mod.delete_secret)
    monkeypatch.setattr(credentials_mod, "keychain_available", secrets_mod.keychain_available)
    return store


def test_set_does_not_accept_the_value_as_an_argument() -> None:
    """A secret on a command line is in shell history, in `ps` output for every other process on
    the machine while the command runs, and in any terminal recording.

    Asserted off the signature, so a `--value` option added later fails here rather than shipping
    as a convenience."""
    params = {
        p.name
        for p in inspect.signature(credentials_mod.credentials_set.callback).parameters.values()
    }
    assert params == {"name", "from_stdin"}
    assert "value" not in params


def test_set_prompts_with_echo_off() -> None:
    """`hide_input` is the difference between typing a key and displaying it on a screen someone
    may be sharing."""
    source = inspect.getsource(credentials_mod.credentials_set.callback)
    assert "hide_input=True" in source


def test_set_stores_from_stdin_without_echoing_the_value(
    fake_keychain: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("CDP_API_KEY", raising=False)
    monkeypatch.setattr(secrets_mod, "default_env_path", lambda: tmp_path / "absent", raising=False)
    result = CliRunner().invoke(
        cli, ["credentials", "set", "CDP_API_KEY", "--stdin"], input=SECRET + "\n"
    )
    assert result.exit_code == 0, result.output
    assert fake_keychain["CDP_API_KEY"] == SECRET
    assert SECRET not in result.output


def test_an_empty_value_stores_nothing(fake_keychain: dict[str, str]) -> None:
    result = CliRunner().invoke(
        cli, ["credentials", "set", "CDP_API_KEY", "--stdin"], input="   \n"
    )
    assert result.exit_code != 0
    assert "CDP_API_KEY" not in fake_keychain


def test_show_never_prints_a_value(fake_keychain: dict[str, str]) -> None:
    """It answers "is it set, and which of the three places is keel reading it from" -- the only
    questions answerable without putting the secret on a screen."""
    fake_keychain["CDP_API_KEY"] = SECRET
    result = CliRunner().invoke(cli, ["credentials", "show"])
    assert result.exit_code == 0
    assert SECRET not in result.output
    assert "CDP_API_KEY" in result.output
    assert "keychain" in result.output


def test_show_names_the_precedence(fake_keychain: dict[str, str]) -> None:
    """ "keel cannot see your key" and "keel is using a different key than the one you just typed"
    are different problems, and only the source distinguishes them."""
    result = CliRunner().invoke(cli, ["credentials", "show"])
    assert "precedence" in result.output
    assert ".env" in result.output


def test_setting_a_shadowed_name_says_so(
    fake_keychain: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise the operator finds out at the first request that used the wrong key."""
    monkeypatch.setenv("CDP_API_KEY", "from-the-environment")
    result = CliRunner().invoke(
        cli, ["credentials", "set", "CDP_API_KEY", "--stdin"], input=SECRET + "\n"
    )
    assert result.exit_code == 0
    assert "takes precedence" in result.output
    assert SECRET not in result.output


def test_forget_says_when_a_file_still_holds_the_value(
    fake_keychain: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """keel does not edit a `.env`. Saying nothing would make "forget" appear not to work."""
    fake_keychain["CDP_API_KEY"] = SECRET
    monkeypatch.setenv("CDP_API_KEY", "from-the-environment")
    result = CliRunner().invoke(cli, ["credentials", "forget", "CDP_API_KEY"])
    assert "removed" in result.output
    assert "can still see" in result.output
    assert "CDP_API_KEY" not in fake_keychain


def test_forget_on_an_absent_name_is_not_an_error(fake_keychain: dict[str, str]) -> None:
    result = CliRunner().invoke(cli, ["credentials", "forget", "NOT_STORED"])
    assert result.exit_code == 0
    assert "nothing removed" in result.output


def test_every_known_credential_says_what_it_is_for() -> None:
    """A list of variable names is not help. `show` prints these unasked, because a blank report
    is not an answer."""
    assert credentials_mod.KNOWN
    for name, why in credentials_mod.KNOWN:
        assert name.isupper()
        assert len(why) > 15
