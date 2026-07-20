"""Build identity (`keel --version`)."""

from __future__ import annotations

import subprocess

import pytest
from click.testing import CliRunner

from keel import version as version_mod
from keel.cli import cli
from keel.version import BuildInfo, build_info


def test_a_dirty_checkout_is_not_reproducible():
    info = BuildInfo(version="0.1.0", commit="abc", dirty=True, source="checkout")
    assert info.is_reproducible is False
    assert "DIRTY" in info.describe()


def test_a_clean_checkout_is_reproducible():
    info = BuildInfo(version="0.1.0", commit="abc", dirty=False, source="checkout")
    assert info.is_reproducible is True
    assert "DIRTY" not in info.describe()


def test_a_clean_release_build_is_reproducible_even_without_git():
    info = BuildInfo(version="1.2.3", commit="abc", dirty=False, source="release")
    assert info.is_reproducible is True
    assert "release" in info.describe()


def test_a_release_build_reporting_DIRTY_is_NOT_reproducible():
    """A stale stamp in a modified checkout must not pass as a release."""
    info = BuildInfo(version="1.2.3", commit="abc", dirty=True, source="release")
    assert info.is_reproducible is False


def test_an_unknown_build_is_NOT_treated_as_reproducible():
    """Failing to identify the build must never read as 'fine'."""
    info = BuildInfo(version="unknown", commit="unknown", dirty=False, source="unknown")
    assert info.is_reproducible is False


def test_build_info_never_raises_when_git_is_unavailable(monkeypatch):
    monkeypatch.setattr(version_mod, "_embedded", lambda: None)
    monkeypatch.setattr(version_mod, "_git", lambda *a: None)
    info = build_info()
    assert info.source == "unknown"


def test_a_failed_status_call_reads_as_DIRTY_not_clean(monkeypatch):
    """"We could not tell" must not be reported as a clean tree."""
    calls = {"n": 0}

    def fake_git(*args):
        calls["n"] += 1
        return "abc123def456" if args[0] == "rev-parse" else None

    monkeypatch.setattr(version_mod, "_embedded", lambda: None)
    monkeypatch.setattr(version_mod, "_git", fake_git)
    info = build_info()
    assert info.source == "checkout"
    assert info.dirty is True


def test_git_timeout_is_bounded(monkeypatch):
    """A hung git must not hang the CLI."""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert version_mod._git("rev-parse", "HEAD") is None
    assert seen.get("timeout") == version_mod._GIT_TIMEOUT_SEC


# -- CLI -----------------------------------------------------------------------


def test_version_flag_prints_and_exits_before_any_command():
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "keel" in result.output


def test_version_flag_warns_loudly_when_the_build_is_not_reproducible(monkeypatch):
    monkeypatch.setattr(
        "keel.cli.build_info",
        lambda: BuildInfo(version="0.1.0", commit="abc", dirty=True, source="checkout"),
    )
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "NOT reproducible" in result.output
    assert "live funds" in result.output


def test_version_flag_is_silent_about_reproducibility_for_a_release_build(monkeypatch):
    monkeypatch.setattr(
        "keel.cli.build_info",
        lambda: BuildInfo(version="1.0.0", commit="deadbeef", dirty=False, source="release"),
    )
    result = CliRunner().invoke(cli, ["--version"])
    assert "NOT reproducible" not in result.output
    assert "1.0.0" in result.output


@pytest.mark.parametrize("flag", ["--version"])
def test_version_does_not_require_a_database(tmp_path, flag):
    """`--version` must work before anything is configured -- it is a diagnostic."""
    result = CliRunner().invoke(cli, [flag])
    assert result.exit_code == 0


def test_a_STALE_release_stamp_in_a_modified_checkout_is_reported_dirty(monkeypatch):
    """The misreport this module exists to prevent.

    A leftover `_build_info.py` from a local build would otherwise make a dev checkout claim
    `[release]` and hide a dirty tree. When git disagrees with the stamp, believe git.
    """

    class _Stamp:
        VERSION = "9.9.9"
        COMMIT = "aaaaaaaaaaaa"
        DIRTY = False

    monkeypatch.setattr(version_mod, "_embedded", lambda: _Stamp)
    monkeypatch.setattr(
        version_mod, "_git", lambda *a: "bbbbbbbbbbbb" if a[0] == "rev-parse" else ""
    )
    info = build_info()
    assert info.source == "release"
    assert info.dirty is True
    assert info.is_reproducible is False


def test_a_matching_stamp_on_a_clean_tree_stays_reproducible(monkeypatch):
    class _Stamp:
        VERSION = "9.9.9"
        COMMIT = "aaaaaaaaaaaa"
        DIRTY = False

    monkeypatch.setattr(version_mod, "_embedded", lambda: _Stamp)
    monkeypatch.setattr(
        version_mod, "_git", lambda *a: "aaaaaaaaaaaa" if a[0] == "rev-parse" else ""
    )
    info = build_info()
    assert info.dirty is False
    assert info.is_reproducible is True
