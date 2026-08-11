"""Tests for `keel versions` -- the deploy check that must be able to FAIL.

The rules it enforces are `keel.version.InstallReport`'s and are tested as pure values in
`tests/test_version.py`. What is left here is the part a deploy script depends on: the exit code,
and that every installed distribution actually reaches the operator's screen. Both are driven
through `CliRunner` with the environment scan stubbed, so the tests describe an install rather
than requiring one.
"""

from __future__ import annotations

from click.testing import CliRunner

from keel.cli import cli
from keel.version import BuildInfo, InstallReport

_RELEASE = BuildInfo(version="0.6.0", commit="deadbeef", dirty=False, source="release")


def _install(monkeypatch, dists, source="release", info=_RELEASE):
    monkeypatch.setattr("keel.commands.versions.build_info", lambda: info)
    monkeypatch.setattr(
        "keel.commands.versions.check_install",
        lambda source=source: InstallReport(distributions=dict(dists), source=source),
    )


def test_a_healthy_install_exits_zero_and_lists_every_distribution(monkeypatch):
    _install(
        monkeypatch,
        {"keel-trader": "0.6.0", "keel-core": "0.6.0", "keel-broker-api": "0.6.0"},
    )
    result = CliRunner().invoke(cli, ["versions"])
    assert result.exit_code == 0
    assert "keel 0.6.0+deadbeef [release]" in result.output
    for name in ("keel-trader", "keel-core", "keel-broker-api"):
        assert name in result.output
    assert "ok: 3 keel distributions, all at 0.6.0." in result.output


def test_a_partial_upgrade_exits_NON_ZERO(monkeypatch):
    """A check that cannot fail is worse than none. This is the one that has to."""
    _install(monkeypatch, {"keel-trader": "0.6.0", "keel-core": "0.5.5"})
    result = CliRunner().invoke(cli, ["versions"])
    assert result.exit_code == 1
    assert "PARTIAL INSTALL" in result.output
    assert "0.5.5" in result.output and "0.6.0" in result.output


def test_the_dev_only_fake_venue_exits_NON_ZERO_in_a_deployment(monkeypatch):
    _install(monkeypatch, {"keel-trader": "0.6.0", "keel-broker-fake": "0.6.0"})
    result = CliRunner().invoke(cli, ["versions"])
    assert result.exit_code == 1
    assert "keel-broker-fake" in result.output
    assert "uv pip uninstall" in result.output


def test_the_dev_only_fake_venue_does_not_fail_a_checkout(monkeypatch):
    checkout = BuildInfo(version="0.6.0", commit="deadbeef", dirty=False, source="checkout")
    _install(
        monkeypatch,
        {"keel-trader": "0.6.0", "keel-broker-fake": "0.6.0"},
        source="checkout",
        info=checkout,
    )
    result = CliRunner().invoke(cli, ["versions"])
    assert result.exit_code == 0


def test_an_uninstalled_checkout_reports_nothing_to_compare(monkeypatch):
    checkout = BuildInfo(version="0.6.0", commit="deadbeef", dirty=False, source="checkout")
    _install(monkeypatch, {}, source="checkout", info=checkout)
    result = CliRunner().invoke(cli, ["versions"])
    assert result.exit_code == 0
    assert "nothing to compare" in result.output


def test_a_non_reproducible_build_still_warns(monkeypatch):
    dirty = BuildInfo(version="0.6.0", commit="deadbeef", dirty=True, source="checkout")
    _install(monkeypatch, {"keel-trader": "0.6.0"}, source="checkout", info=dirty)
    result = CliRunner().invoke(cli, ["versions"])
    assert "NOT reproducible" in result.output
    assert result.exit_code == 0  # build state is `--version`'s claim, not this command's gate


def test_it_needs_no_config_and_no_database(tmp_path, monkeypatch):
    """Nothing environmental may make this fail, or a non-zero exit stops meaning anything."""
    _install(monkeypatch, {"keel-trader": "0.6.0"})
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["--db", "nope.db", "--config", "nope.yaml", "versions"])
    assert result.exit_code == 0
