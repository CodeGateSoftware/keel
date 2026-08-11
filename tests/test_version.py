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


# -- version <-> build hash binding --------------------------------------------


def test_full_version_binds_version_to_commit_as_build_metadata():
    info = BuildInfo(version="0.1.0", commit="c11baba726af", dirty=False, source="release")
    assert info.full_version == "0.1.0+c11baba726af"
    assert info.full_version in info.describe()


def test_full_version_omits_an_unknown_commit():
    info = BuildInfo(version="0.1.0", commit="unknown", dirty=False, source="unknown")
    assert info.full_version == "0.1.0"


def test_describe_still_flags_dirty_and_source():
    dirty = BuildInfo(version="0.1.0", commit="abc", dirty=True, source="checkout").describe()
    assert "DIRTY" in dirty and "checkout" in dirty
    clean = BuildInfo(version="0.1.0", commit="abc", dirty=False, source="release").describe()
    assert "DIRTY" not in clean and "release" in clean


# -- the whole install (InstallReport) ------------------------------------------------------


def _report(dists, source="release"):
    return version_mod.InstallReport(distributions=dict(dists), source=source)


def test_an_install_at_one_version_is_healthy():
    report = _report({"keel-trader": "0.6.0", "keel-core": "0.6.0"})
    assert report.is_consistent is True
    assert report.problems == []
    assert report.versions == ["0.6.0"]


def test_the_real_deployment_failure_is_caught():
    """`keel-trader 0.5.7` against `keel-core 0.5.5` -- what `--version` could not see."""
    report = _report({"keel-trader": "0.5.7", "keel-core": "0.5.5", "keel-broker-api": "0.5.5"})
    assert report.is_consistent is False
    assert len(report.problems) == 1
    assert "PARTIAL INSTALL" in report.problems[0]
    assert "0.5.5, 0.5.7" in report.problems[0]


def test_an_empty_install_is_not_invented_into_a_failure():
    """A source checkout with nothing installed is a legitimate state, not a partial upgrade."""
    report = _report({})
    assert report.is_consistent is True
    assert report.problems == []


def test_the_dev_only_fake_venue_fails_a_RELEASE_build():
    report = _report({"keel-trader": "0.6.0", "keel-broker-fake": "0.6.0"}, source="release")
    assert report.is_consistent is True  # versions agree; the package itself is the problem
    assert [p for p in report.problems if "keel-broker-fake" in p]


def test_the_dev_only_fake_venue_is_fine_in_a_CHECKOUT():
    """A checkout is exactly where it belongs; crying wolf there would train the check away."""
    report = _report({"keel-trader": "0.6.0", "keel-broker-fake": "0.6.0"}, source="checkout")
    assert report.dev_only_installed == ["keel-broker-fake"]
    assert report.problems == []


def test_a_dirty_or_checkout_build_is_NOT_an_install_problem():
    """`describe()` already reports build state; this report is about what is installed."""
    assert _report({"keel-trader": "0.6.0"}, source="checkout").problems == []
    assert _report({"keel-trader": "0.6.0"}, source="unknown").problems == []


def test_distribution_names_are_canonicalised():
    assert version_mod._canonical("Keel_Broker_API") == "keel-broker-api"


def test_only_the_keel_family_is_enumerated(monkeypatch):
    """The bare name `keel` on PyPI is a stranger's project -- its version means nothing here."""

    class FakeDist:
        def __init__(self, name, ver):
            self.metadata = {"Name": name}
            self.version = ver

    monkeypatch.setattr(
        version_mod.metadata,
        "distributions",
        lambda: [
            FakeDist("keel_trader", "0.6.0"),
            FakeDist("keel-core", "0.6.0"),
            FakeDist("keel", "9.9.9"),  # the unrelated PyPI project
            FakeDist("click", "8.4.2"),
        ],
    )
    assert version_mod.installed_distributions() == {"keel-trader": "0.6.0", "keel-core": "0.6.0"}


def test_the_first_copy_on_the_path_wins(monkeypatch):
    """A second copy further down `sys.path` is unreachable, so it must not be reported."""

    class FakeDist:
        def __init__(self, name, ver):
            self.metadata = {"Name": name}
            self.version = ver

    monkeypatch.setattr(
        version_mod.metadata,
        "distributions",
        lambda: [FakeDist("keel-core", "0.6.0"), FakeDist("keel-core", "0.1.0")],
    )
    assert version_mod.installed_distributions() == {"keel-core": "0.6.0"}


def test_enumeration_never_raises(monkeypatch):
    """A broken environment is a reason to report nothing, not to stop the CLI."""

    def boom():
        raise RuntimeError("no metadata here")

    monkeypatch.setattr(version_mod.metadata, "distributions", boom)
    assert version_mod.installed_distributions() == {}


def test_version_flag_warns_when_the_rest_of_the_install_disagrees(monkeypatch):
    """The whole point: the line still says 0.6.0, but it no longer says it alone."""
    monkeypatch.setattr(
        "keel.cli.build_info",
        lambda: BuildInfo(version="0.6.0", commit="deadbeef", dirty=False, source="release"),
    )
    monkeypatch.setattr(
        "keel.cli.check_install",
        lambda source=None: _report({"keel-trader": "0.6.0", "keel-core": "0.5.5"}),
    )
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0  # still a diagnostic; `keel versions` is the gate
    assert "keel 0.6.0+deadbeef [release]" in result.output
    assert "PARTIAL INSTALL" in result.output


def test_version_flag_says_nothing_extra_when_the_install_agrees(monkeypatch):
    monkeypatch.setattr(
        "keel.cli.check_install",
        lambda source=None: _report({"keel-trader": "0.6.0", "keel-core": "0.6.0"}),
    )
    result = CliRunner().invoke(cli, ["--version"])
    assert "PARTIAL INSTALL" not in result.output
