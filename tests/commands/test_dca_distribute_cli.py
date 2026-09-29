"""`keel dca distribute --preview` -- the read-only view of what each `reverse_dca` rule's next
cadence day would do (#857, plan P10 Task 10.1; spec §6 "CLI").

Read-only ALWAYS, like `keel dca proposals`: it opens the database through
`_common._open_repo_ro` whatever the terminal, writes nothing (no proposal row, no state), and
builds no broker -- the fee is the fallback rate, labelled as such (R25).
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from click.testing import CliRunner

from keel.cli import cli
from keel.commands import _common, sleeve_report
from keel.commands import dca as dca_cli
from keel.config import load_config
from keel.data import db as keel_db
from keel.types import Granularity
from tests.commands.test_dca_cli import _repo, deployment  # noqa: F401 - the fixture
from tests.commands.test_sleeve_report import _candle

DAY = 86_400
_NOW = 201 * DAY


def _invoke(deployment_pair, *args: str):
    db, config = deployment_pair
    return CliRunner().invoke(
        cli, ["--db", str(db), "--config", str(config), "dca", "distribute", *args]
    )


def _seed(db: Path) -> None:
    """A paper `reverse_dca` beside a paper weekly `dca` -- the fixture config is a paper
    profile, so these are the rules its cycle would run."""
    repo = _repo(db)
    repo.insert_rule(
        "reverse_dca",
        {"product_id": "BTC-USD", "target_usd": "100", "min_price_floor": "60000"},
        status="paper",
    )
    repo.insert_rule(
        "dca", {"product_id": "BTC-USD", "cadence_days": 7, "budget_usd": "50"}, status="paper"
    )
    repo.open_position(
        product_id="BTC-USD",
        rule_name="dca",
        opened_at=0,
        qty=Decimal("0.01"),
        entry_fill=Decimal("50000"),
        entry_fee=Decimal("0"),
    )
    repo.upsert_candles(
        "BTC-USD", Granularity.ONE_DAY, [_candle(d, "100000") for d in range(195, 201)]
    )


@pytest.fixture
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dca_cli.time, "time", lambda: float(_NOW))


def _no_broker(*_a, **_k):
    raise AssertionError("distribute --preview built a broker (R25: the CLI previews build none)")


@pytest.mark.parametrize("interactive", [False, True])
def test_preview_prints_the_rows_and_writes_nothing(
    deployment,  # noqa: F811
    monkeypatch,
    frozen_now,
    interactive,
) -> None:
    monkeypatch.setattr(_common, "_is_interactive", lambda: interactive)
    monkeypatch.setattr(_common, "_build_broker", _no_broker)
    db, config_path = deployment
    _seed(db)
    # `PRAGMA data_version` on ONE connection held across the run changes iff ANOTHER connection
    # committed (`test_dca_cli.py`'s watcher). A second, independent count: every table's rows.
    watcher = sqlite3.connect(str(db))
    before = watcher.execute("PRAGMA data_version").fetchone()[0]
    tables = [
        name for (name,) in watcher.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    ]
    counts = {t: watcher.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}

    result = _invoke(deployment, "--preview")

    assert result.exit_code == 0, result.output
    expected = sleeve_report.render_distribution(
        sleeve_report.distribution_rows(_repo(db), load_config(config_path), now_ts=_NOW)
    )
    lines = result.output.splitlines()
    assert [line for line in lines if line in expected] == expected
    assert len(expected) == 2, "one row, and its same-day-DCA line"
    assert "(fallback:config.fees.taker_pct)" in expected[0]
    # The footer follows the rows directly, once (the disclaimer `with_disclaimer` prints comes
    # after it).
    assert lines.count(dca_cli.DISTRIBUTE_PREVIEW_FOOTER) == 1
    assert lines.index(dca_cli.DISTRIBUTE_PREVIEW_FOOTER) == lines.index(expected[-1]) + 1
    assert watcher.execute("PRAGMA data_version").fetchone()[0] == before
    assert {
        t: watcher.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables
    } == counts
    assert _repo(db).get_sell_proposals() == []
    watcher.close()
    # For the report: the exact CLI output.
    print(result.output)


def test_the_footer_says_nothing_is_placed() -> None:
    assert dca_cli.DISTRIBUTE_PREVIEW_FOOTER == "preview only: nothing is placed."


def test_without_preview_it_is_a_usage_error(deployment) -> None:  # noqa: F811
    result = _invoke(deployment)
    assert result.exit_code == 2
    assert "--preview" in result.output


def test_no_seller_says_so(deployment, frozen_now) -> None:  # noqa: F811
    result = _invoke(deployment, "--preview")
    assert result.exit_code == 0, result.output
    assert sleeve_report.NO_DISTRIBUTION_RULES in result.output.splitlines()


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


def test_a_stale_schema_is_refused_not_migrated(tmp_path: Path, valid_config_path) -> None:
    db = tmp_path / "v21.db"
    conn = keel_db.connect(db)
    keel_db.migrate(conn)
    conn.execute("DROP TABLE sell_proposals")
    conn.execute("UPDATE schema_version SET version = 21")
    conn.commit()
    conn.close()

    result = _invoke((db, valid_config_path), "--preview")

    assert result.exit_code == 1
    assert "schema version 21" in result.output


def test_it_really_reads_the_clock(deployment, monkeypatch) -> None:  # noqa: F811
    """The fixture above freezes `time.time`; this pins that the command is reading it."""
    calls: list[int] = []
    monkeypatch.setattr(dca_cli.time, "time", lambda: calls.append(1) or float(_NOW))
    _invoke(deployment, "--preview")
    assert calls
