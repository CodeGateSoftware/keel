"""`keel dca exit --preview` -- the sleeve exit monitor's levels, read-only (#857, plan P15 Task
15.3; spec §7).

Read-only ALWAYS, like `keel dca distribute --preview` and `trim --preview`: it opens the database
through `_common._open_repo_ro` whatever the terminal, writes nothing -- not even the monitor's own
`sleeve_exit:` record, which is the cycle's -- and builds no broker. It alerts; an automatic sale
is not built (S2), and the footer says so.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal as D
from pathlib import Path

import pytest
from click.testing import CliRunner

from keel.cli import cli
from keel.commands import _common, sleeve_report
from keel.commands import dca as dca_cli
from keel.config import load_config
from keel.types import Granularity
from tests.commands.test_dca_cli import _repo, deployment  # noqa: F401 - the fixture
from tests.commands.test_sleeve_report import _candle

DAY = 86_400
_NOW = 30 * DAY + 3_600


def _invoke(deployment_pair, *args: str):
    db, config = deployment_pair
    return CliRunner().invoke(cli, ["--db", str(db), "--config", str(config), "dca", "exit", *args])


def _seed(db: Path) -> None:
    """A BTC dca tranche and 30 cached daily bars, and PAXG tranche 3 with no bar at all."""
    repo = _repo(db)
    repo.open_position(
        product_id="BTC-USD",
        rule_name="dca",
        opened_at=0,
        qty=D("0.001"),
        entry_fill=D("100000"),
        entry_fee=D("0"),
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, [_candle(d, "100000") for d in range(30)])
    repo.open_position(
        product_id="PAXG-USD",
        rule_name="turtle_breakout",
        opened_at=0,
        qty=D("0.0132"),
        entry_fill=D("4673.23"),
        entry_fee=D("0.73"),
        initial_stop=D("4521.76"),
    )


@pytest.fixture
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dca_cli.time, "time", lambda: float(_NOW))


def _no_broker(*_a, **_k):
    raise AssertionError("exit --preview built a broker (R25: the CLI previews build none)")


@pytest.mark.parametrize("interactive", [False, True])
def test_exit_preview_prints_levels_and_writes_nothing(
    deployment,  # noqa: F811
    monkeypatch,
    frozen_now,
    interactive,
) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: interactive)
    monkeypatch.setattr(_common, "_build_broker", _no_broker)
    db, config_path = deployment
    _seed(db)
    watcher = sqlite3.connect(str(db))
    before = watcher.execute("PRAGMA data_version").fetchone()[0]
    tables = [
        name for (name,) in watcher.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    ]
    counts = {t: watcher.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}

    result = _invoke(deployment, "--preview")

    assert result.exit_code == 0, result.output
    expected = sleeve_report.render_exit_watch(
        sleeve_report.exit_watch_view(_repo(db), load_config(config_path), now_ts=_NOW)
    )
    lines = result.output.splitlines()
    start = lines.index(expected[0])
    assert lines[start : start + len(expected)] == expected
    assert lines[start + len(expected)] == dca_cli.EXIT_PREVIEW_FOOTER
    assert lines.count(dca_cli.EXIT_PREVIEW_FOOTER) == 1
    assert [line.split()[0] for line in expected if line.startswith("  ")] == [
        "BTC-USD",
        "PAXG-USD",
    ]
    assert watcher.execute("PRAGMA data_version").fetchone()[0] == before
    assert {
        t: watcher.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables
    } == counts
    watcher.close()
    print(result.output)  # for the PR body: the exact CLI output


def test_the_footer_says_no_automatic_sale_is_built() -> None:
    assert dca_cli.EXIT_PREVIEW_FOOTER == (
        "preview only: an automatic sale is not built -- the monitor alerts, and nothing is placed."
    )


def test_without_preview_it_is_a_usage_error(deployment) -> None:  # noqa: F811
    result = _invoke(deployment)
    assert result.exit_code == 2
    assert "--preview" in result.output


def test_nothing_held_says_nothing_is_watched(deployment, frozen_now) -> None:  # noqa: F811
    result = _invoke(deployment, "--preview")
    assert result.exit_code == 0, result.output
    assert sleeve_report.NO_EXIT_WATCH in result.output.splitlines()


@pytest.mark.parametrize("interactive", [False, True])
def test_a_missing_database_is_refused_not_created(
    tmp_path: Path, valid_config_path, monkeypatch, interactive
) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: interactive)
    db = tmp_path / "absent.db"
    result = _invoke((db, valid_config_path), "--preview")
    assert result.exit_code == 1
    assert "read-only command" in result.output
    assert not db.exists()


def test_a_malformed_product_prints_a_skipped_line_and_the_rest(
    deployment,  # noqa: F811
    frozen_now,
) -> None:
    """P15's held item b, end to end: a record the view cannot read no longer crashes the
    command -- it prints one SKIPPED line for that product and every other row."""
    db, _config_path = deployment
    _seed(db)
    _repo(db).set_state("sleeve_exit:BTC-USD", "breached")

    result = _invoke(deployment, "--preview")

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    skipped = sleeve_report.skipped_product_line(
        sleeve_report.SkippedProduct(
            "BTC-USD", "ValueError: its sleeve_exit record is not a mapping: 'breached'"
        )
    )
    assert lines.count(skipped) == 1
    assert [line.split()[0] for line in lines if line.startswith("  ")] == ["PAXG-USD"]
    assert lines.count(dca_cli.EXIT_PREVIEW_FOOTER) == 1
