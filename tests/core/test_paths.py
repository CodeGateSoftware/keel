"""`keel_core.paths` — where a bare invocation resolves its state (#434).

The property under test is not "app-data works". It is that **an existing deployment always wins**,
because the failure mode of getting that wrong is not an error: it is keel opening a fresh empty
database beside a populated one and reporting a healthy deployment with no history.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from keel_core import paths

# --- deployment detection -------------------------------------------------------------------------


@pytest.mark.parametrize("marker", ["config.yaml", ".env"])
def test_a_folder_with_a_marker_file_is_a_deployment(tmp_path: Path, marker: str) -> None:
    (tmp_path / marker).write_text("")
    assert paths.is_deployment_root(tmp_path)


@pytest.mark.parametrize("db", ["keel.db", "keel-live.db", "keel-paperhourly.db"])
def test_a_folder_holding_any_keel_database_is_a_deployment(tmp_path: Path, db: str) -> None:
    """Profile deployments name their config `config.live-sandbox.yaml` and friends, so the
    database is the only marker present. Missing this would send `keel status` in `~/keel` to an
    app-data directory while the real ledger sat beside it."""
    (tmp_path / db).write_text("")
    assert paths.is_deployment_root(tmp_path)


def test_an_empty_folder_is_not_a_deployment(tmp_path: Path) -> None:
    assert not paths.is_deployment_root(tmp_path)


def test_detection_never_raises_on_an_unreadable_path(tmp_path: Path) -> None:
    """This runs inside every path lookup, so an OSError here would surface as a traceback from
    something as innocuous as `keel --help`."""
    assert paths.is_deployment_root(tmp_path / "does" / "not" / "exist") is False


# --- precedence -----------------------------------------------------------------------------------


def test_a_deployment_cwd_wins_over_app_data(tmp_path, monkeypatch) -> None:
    """The property that keeps every existing install byte-identical."""
    (tmp_path / "config.yaml").write_text("")
    monkeypatch.delenv(paths.HOME_ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)

    assert paths.state_root() == tmp_path
    assert paths.default_db_path() == tmp_path / "keel.db"
    assert paths.default_config_path() == tmp_path / "config.yaml"
    assert paths.default_env_path() == tmp_path / ".env"


def test_a_populated_deployment_is_never_shadowed_by_app_data(tmp_path, monkeypatch) -> None:
    """The failure this module exists to prevent, stated as a test: a folder holding a real
    ledger must resolve to that ledger, never to an empty one somewhere else."""
    (tmp_path / "keel-live.db").write_text("not really sqlite, but present")
    monkeypatch.delenv(paths.HOME_ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)

    assert paths.state_root() == tmp_path
    assert paths.default_db_path().parent == tmp_path


def test_a_non_deployment_cwd_falls_back_to_app_data(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(paths.HOME_ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)

    assert paths.state_root() == paths.app_data_dir()


def test_keel_home_outranks_everything(tmp_path, monkeypatch) -> None:
    """The escape hatch a wrapper reaches for when it must not depend on cwd."""
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    (deployment / "config.yaml").write_text("")
    pinned = tmp_path / "pinned"
    monkeypatch.chdir(deployment)
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(pinned))

    assert paths.state_root() == pinned


def test_keel_home_expands_a_tilde(monkeypatch) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, "~/somewhere")
    assert paths.state_root() == Path.home() / "somewhere"


# --- app-data location ----------------------------------------------------------------------------


def test_app_data_dir_is_platform_appropriate(monkeypatch) -> None:
    if sys.platform == "darwin":
        assert paths.app_data_dir() == Path.home() / "Library" / "Application Support" / "keel"
    elif sys.platform == "win32":
        assert paths.app_data_dir().name == "keel"
    else:
        assert paths.app_data_dir().name == "keel"


def test_windows_prefers_local_appdata_over_roaming(monkeypatch) -> None:
    """A roaming profile would copy the SQLite database between machines on login -- slow, and a
    corruption risk for a file a running process is writing."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\x\AppData\Local")
    monkeypatch.setenv("APPDATA", r"C:\Users\x\AppData\Roaming")

    assert "Local" in str(paths.app_data_dir())
    assert "Roaming" not in str(paths.app_data_dir())


def test_state_root_creates_app_data_only_when_asked(tmp_path, monkeypatch) -> None:
    target = tmp_path / "made-on-demand"
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(target))

    assert paths.state_root() == target and not target.exists()
    assert paths.state_root(create=True) == target and target.is_dir()


# --- resolving configured relative paths ----------------------------------------------------------


def test_a_relative_configured_path_resolves_against_the_state_root(tmp_path, monkeypatch) -> None:
    """`logging.file` defaults to the relative `logs/keel.log` so a deployment's log lands beside
    its database. Under an app-bundle launch a bare relative path would mean `/logs/keel.log`."""
    (tmp_path / "config.yaml").write_text("")
    monkeypatch.delenv(paths.HOME_ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)

    assert paths.resolve_under_state_root("logs/keel.log") == tmp_path / "logs" / "keel.log"


def test_an_absolute_configured_path_is_returned_unchanged(tmp_path) -> None:
    pinned = tmp_path / "elsewhere" / "keel.log"
    assert paths.resolve_under_state_root(pinned) == pinned


# --- the CLI defaults -----------------------------------------------------------------------------


def test_cli_defaults_resolve_per_invocation_not_per_import(tmp_path, monkeypatch) -> None:
    """Click evaluates a literal `default=` once, at decoration time. A literal here would freeze
    whatever directory the process started in -- and `--db`'s default would then ignore both
    `KEEL_HOME` and the deployment folder the operator is standing in."""
    from keel.commands._common import default_config_path, default_db_path

    first = tmp_path / "one"
    second = tmp_path / "two"
    for d in (first, second):
        d.mkdir()
        (d / "config.yaml").write_text("")

    monkeypatch.delenv(paths.HOME_ENV_VAR, raising=False)
    monkeypatch.chdir(first)
    assert default_db_path() == str(first / "keel.db")

    monkeypatch.chdir(second)
    assert default_db_path() == str(second / "keel.db")
    assert default_config_path() == str(second / "config.yaml")


def test_init_config_still_writes_to_the_current_directory(tmp_path, monkeypatch) -> None:
    """`init` and `init-config` CREATE a deployment folder, so "here" is what the operator means.
    Routing them through the state-root resolver would make `mkdir x && cd x && keel init` write
    somewhere else entirely, because an empty folder is not yet a deployment root."""
    from click.testing import CliRunner

    from keel.cli import cli

    monkeypatch.delenv(paths.HOME_ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)
    assert not paths.is_deployment_root(tmp_path)

    result = CliRunner().invoke(cli, ["init-config"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "config.yaml").is_file()
    # ...and having written it, the folder is now a deployment root, so everything else follows.
    assert paths.is_deployment_root(tmp_path)
    assert paths.default_db_path() == tmp_path / "keel.db"
