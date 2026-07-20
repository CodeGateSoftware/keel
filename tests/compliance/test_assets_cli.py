"""`keel assets` -- the allowlist admission gate's CLI surface."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from click.testing import CliRunner

from keel.cli import cli
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.types import Candle, Granularity

_DAY = 86400


def _repo_at(db_path: Path) -> Repository:
    conn = connect(str(db_path))
    migrate(conn)
    return Repository(conn)


def _seed_history(repo: Repository, product: str, bars: int = 2000) -> None:
    repo.upsert_candles(
        product,
        Granularity.ONE_DAY,
        [
            Candle(
                ts=i * _DAY,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal("100000"),
            )
            for i in range(bars)
        ],
    )


def _attest(runner, db_path, config_path, asset, **over):
    args = {
        "--asset": asset,
        "--sector": "payments",
        "--backing": "native",
        "--source": "https://example.invalid/ruling",
        "--attested-by": "tester",
    }
    args.update(over)
    flat = [item for pair in args.items() for item in pair]
    return runner.invoke(
        cli, ["--db", str(db_path), "--config", str(config_path), "assets", "attest", *flat]
    )


def test_an_unattested_asset_is_rejected_even_with_perfect_market_data(
    tmp_path, valid_config_path
):
    """The gate's whole point: good candles do not substitute for a classification."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    for asset in ("BTC", "ETH", "PAXG"):
        _seed_history(repo, f"{asset}-USD")

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "assets", "screen"]
    )
    assert result.exit_code == 0, result.output
    assert "0/3 admitted" in result.output
    assert "attestation: MISSING" in result.output


def test_attesting_admits_an_otherwise_clean_asset(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    for asset in ("BTC", "ETH", "PAXG"):
        _seed_history(repo, f"{asset}-USD")

    runner = CliRunner()
    for asset in ("BTC", "ETH", "PAXG"):
        assert _attest(runner, db_path, valid_config_path, asset).exit_code == 0

    result = runner.invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "assets", "screen"]
    )
    assert "3/3 admitted" in result.output


def test_a_haram_sector_attestation_still_rejects(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "BTC-USD")

    runner = CliRunner()
    _attest(runner, db_path, valid_config_path, "BTC", **{"--sector": "gambling"})

    result = runner.invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path),
         "assets", "screen", "--products", "BTC-USD"],
    )
    assert "0/1 admitted" in result.output
    assert "haram_sector" in result.output


def test_short_history_rejects_regardless_of_attestation(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "PAXG-USD", bars=400)

    runner = CliRunner()
    _attest(runner, db_path, valid_config_path, "PAXG", **{"--backing": "ayn"})

    result = runner.invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path),
         "assets", "screen", "--products", "PAXG-USD"],
    )
    assert "0/1 admitted" in result.output
    assert "history" in result.output


def test_attest_rejects_an_unknown_backing_at_the_cli_boundary(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    result = _attest(
        CliRunner(), db_path, valid_config_path, "BTC", **{"--backing": "probably-fine"}
    )
    assert result.exit_code != 0


def test_attestations_round_trip_through_list(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    runner = CliRunner()
    _attest(runner, db_path, valid_config_path, "PAXG", **{"--backing": "ayn"})

    result = runner.invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "assets", "list"]
    )
    assert "PAXG" in result.output
    assert "ayn" in result.output
