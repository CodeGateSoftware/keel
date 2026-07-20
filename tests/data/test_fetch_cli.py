"""`keel fetch` -- the scheduled data-refresh command.

Read-only with respect to money. `--check` must never open a network connection, which these
tests enforce by monkeypatching `_build_broker` to something that raises if called.
"""

from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path

from click.testing import CliRunner

import keel.cli as cli_module
from keel.cli import cli
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.types import Candle, Granularity

_DAY = 86400
_HOUR = 3600


def _repo_at(db_path: Path) -> Repository:
    conn = connect(str(db_path))
    migrate(conn)
    return Repository(conn)


def _candle(ts: int) -> Candle:
    return Candle(
        ts=ts,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1"),
    )


def _seed(repo: Repository, product: str, granularity: Granularity, timestamps) -> None:
    repo.upsert_candles(product, granularity, [_candle(ts) for ts in timestamps])


class _ExplodingBroker:
    """Any use of the network in `--check` mode is a bug, so make it loud."""

    def __getattr__(self, name):
        raise AssertionError(f"--check must not touch the network (called {name!r})")


def _no_network(monkeypatch):
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: _ExplodingBroker())


def test_check_reports_current_series_and_exits_zero(
    tmp_path, valid_config_path, monkeypatch
):
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)

    now = int(time.time())
    # Seed right up to the newest COMPLETE bar for both granularities.
    last_day = (now // _DAY) * _DAY - _DAY
    last_hour = (now // _HOUR) * _HOUR - _HOUR
    for asset in ("BTC", "ETH", "PAXG"):
        _seed(repo, f"{asset}-USD", Granularity.ONE_DAY, [last_day - i * _DAY for i in range(30)])
        _seed(
            repo, f"{asset}-USD", Granularity.ONE_HOUR, [last_hour - i * _HOUR for i in range(30)]
        )

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--check"]
    )
    assert result.exit_code == 0, result.output
    assert "all series current" in result.output
    assert "STALE" not in result.output


def test_check_exits_nonzero_when_stale_so_a_scheduler_can_alert(
    tmp_path, valid_config_path, monkeypatch
):
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)

    now = int(time.time())
    stale_day = (now // _DAY) * _DAY - 40 * _DAY
    for asset in ("BTC", "ETH", "PAXG"):
        _seed(repo, f"{asset}-USD", Granularity.ONE_DAY, [stale_day - i * _DAY for i in range(30)])

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--check"]
    )
    assert result.exit_code != 0
    assert "STALE" in result.output
    assert "missing or stale" in result.output


def test_check_reports_missing_series(tmp_path, valid_config_path, monkeypatch):
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    _repo_at(db_path)  # migrated but empty

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--check"]
    )
    assert result.exit_code != 0
    assert "MISSING" in result.output


def test_check_reports_gaps_without_failing_unless_asked(
    tmp_path, valid_config_path, monkeypatch
):
    """Gaps are reported, but do not fail --check by default.

    `ensure_history` cannot repair internal holes, so failing on them would leave the alert
    permanently red. `--fail-on-gaps` is there for a caller who wants strictness.
    """
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)

    now = int(time.time())
    last_day = (now // _DAY) * _DAY - _DAY
    last_hour = (now // _HOUR) * _HOUR - _HOUR
    for asset in ("BTC", "ETH", "PAXG"):
        # Skip day 5 -> one internal gap.
        days = [last_day - i * _DAY for i in range(30) if i != 5]
        _seed(repo, f"{asset}-USD", Granularity.ONE_DAY, days)
        _seed(
            repo, f"{asset}-USD", Granularity.ONE_HOUR, [last_hour - i * _HOUR for i in range(30)]
        )

    args = ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--check"]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert "GAPS" in result.output
    assert "internal gaps" in result.output

    strict = CliRunner().invoke(cli, [*args, "--fail-on-gaps"])
    assert strict.exit_code != 0
    assert "internal gaps" in strict.output


def test_tolerance_bars_is_honoured(tmp_path, valid_config_path, monkeypatch):
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)

    now = int(time.time())
    last_day = (now // _DAY) * _DAY - 6 * _DAY
    last_hour = (now // _HOUR) * _HOUR - _HOUR
    for asset in ("BTC", "ETH", "PAXG"):
        _seed(repo, f"{asset}-USD", Granularity.ONE_DAY, [last_day - i * _DAY for i in range(30)])
        _seed(
            repo, f"{asset}-USD", Granularity.ONE_HOUR, [last_hour - i * _HOUR for i in range(30)]
        )

    args = ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--check"]
    assert CliRunner().invoke(cli, args).exit_code != 0
    assert CliRunner().invoke(cli, [*args, "--tolerance-bars", "20"]).exit_code == 0


def test_fetch_skips_the_network_when_everything_is_current(
    tmp_path, valid_config_path, monkeypatch
):
    """Without --check the command may fetch -- but not when there is nothing to do."""
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)

    now = int(time.time())
    last_day = (now // _DAY) * _DAY - _DAY
    last_hour = (now // _HOUR) * _HOUR - _HOUR
    for asset in ("BTC", "ETH", "PAXG"):
        _seed(repo, f"{asset}-USD", Granularity.ONE_DAY, [last_day - i * _DAY for i in range(30)])
        _seed(
            repo, f"{asset}-USD", Granularity.ONE_HOUR, [last_hour - i * _HOUR for i in range(30)]
        )

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "fetch"]
    )
    assert result.exit_code == 0, result.output
    assert "nothing to fetch" in result.output


def test_fetch_calls_ensure_history_when_stale(tmp_path, valid_config_path, monkeypatch):
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)

    now = int(time.time())
    stale_day = (now // _DAY) * _DAY - 40 * _DAY
    for asset in ("BTC", "ETH", "PAXG"):
        _seed(repo, f"{asset}-USD", Granularity.ONE_DAY, [stale_day - i * _DAY for i in range(30)])

    calls: list[tuple] = []

    def _fake_ensure(client, repo_arg, products, grans, years, now_ts, **kwargs):
        calls.append((tuple(products), tuple(grans), years))
        return {}

    monkeypatch.setattr(cli_module, "_build_broker", lambda config: object())
    monkeypatch.setattr(cli_module.history_mod, "ensure_history", _fake_ensure)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "fetch"]
    )
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    products, grans, years = calls[0]
    assert set(products) == {"BTC-USD", "ETH-USD", "PAXG-USD"}
    assert Granularity.ONE_DAY in grans and Granularity.ONE_HOUR in grans
    assert years == 5
