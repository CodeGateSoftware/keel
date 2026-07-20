"""`keel assets` -- the allowlist admission gate's CLI surface."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from click.testing import CliRunner

import keel.cli as cli_module
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


# -- discover ------------------------------------------------------------------


class _FakeVenue:
    """Serves canned product metadata and canned history probes. No network."""

    def __init__(self, products, history_for=frozenset()):
        self._products = products
        self._history_for = history_for
        self.probe_calls: list[str] = []

    def list_products(self, product_type="SPOT"):
        return self._products

    def get_candles(self, product_id, granularity, start, end):
        self.probe_calls.append(product_id)
        return [1] if product_id in self._history_for else []


def _venue_product(pid, volume, **over):
    base = {
        "product_id": pid,
        "base_name": pid.split("-")[0],
        "quote_currency_id": "USDC",
        "status": "online",
        "trading_disabled": False,
        "is_disabled": False,
        "view_only": False,
        "quote_24h_volume": volume,
    }
    base.update(over)
    return base


def test_discover_proposes_and_says_so_loudly(tmp_path, valid_config_path, monkeypatch):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    venue = _FakeVenue([_venue_product("SOL-USDC", "50000000")])
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: venue)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "assets", "discover"]
    )
    assert result.exit_code == 0, result.output
    assert "SOL" in result.output
    # The proposal/admission boundary must be unmissable in the output.
    assert "PROPOSALS, not admissions" in result.output
    assert "attest" in result.output


def test_discover_excludes_the_current_allowlist(tmp_path, valid_config_path, monkeypatch):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    venue = _FakeVenue(
        [_venue_product("BTC-USDC", "90000000"), _venue_product("SOL-USDC", "50000000")]
    )
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: venue)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "assets", "discover"]
    )
    assert "SOL" in result.output
    assert "BTC-USDC" not in result.output


def test_probe_history_marks_candidates_without_a_four_year_series(
    tmp_path, valid_config_path, monkeypatch
):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    venue = _FakeVenue(
        [_venue_product("SOL-USDC", "50000000"), _venue_product("NEW-USDC", "40000000")],
        history_for=frozenset({"SOL-USDC"}),
    )
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: venue)

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path),
         "assets", "discover", "--probe-history"],
    )
    assert result.exit_code == 0, result.output
    assert set(venue.probe_calls) == {"SOL-USDC", "NEW-USDC"}
    sol_line = next(ln for ln in result.output.splitlines() if "SOL-USDC" in ln)
    new_line = next(ln for ln in result.output.splitlines() if "NEW-USDC" in ln)
    assert "yes" in sol_line
    assert "NO" in new_line


def test_a_failed_probe_reads_as_UNKNOWN_not_as_a_rejection(
    tmp_path, valid_config_path, monkeypatch
):
    """A request that did not complete is not evidence of absence."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)

    class _BrokenProbe(_FakeVenue):
        def get_candles(self, *a, **k):
            raise RuntimeError("timeout")

    venue = _BrokenProbe([_venue_product("SOL-USDC", "50000000")])
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: venue)

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path),
         "assets", "discover", "--probe-history"],
    )
    assert result.exit_code == 0
    sol_line = next(ln for ln in result.output.splitlines() if "SOL-USDC" in ln)
    assert "?" in sol_line
    assert "NO" not in sol_line
