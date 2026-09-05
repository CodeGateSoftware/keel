"""`keel assets` -- the allowlist admission gate's CLI surface."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from click.testing import CliRunner

import keel.cli as cli_module
from keel.cli import cli
from keel.commands._common import DISCLAIMER
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.types import Candle, Granularity
from tests.conftest import VALID_CONFIG_YAML

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


def _attest_instrument(runner, db_path, config_path, product, **over):
    args = {
        "--product": product,
        "--wrapper": "spot",
        "--source": "coinbase product spec",
        "--attested-by": "tester",
    }
    args.update(over)
    flat = [item for pair in args.items() for item in pair]
    return runner.invoke(
        cli,
        ["--db", str(db_path), "--config", str(config_path), "assets", "attest-instrument", *flat],
    )


def test_an_unattested_asset_is_rejected_even_with_perfect_market_data(tmp_path, valid_config_path):
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
        assert _attest_instrument(runner, db_path, valid_config_path, f"{asset}-USD").exit_code == 0

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
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "screen",
            "--products",
            "BTC-USD",
        ],
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
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "screen",
            "--products",
            "PAXG-USD",
        ],
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


# -- attestation window (#718) --------------------------------------------------


def test_attest_records_the_supplied_attest_due_as_epoch_seconds(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    runner = CliRunner()

    result = _attest(
        runner, db_path, valid_config_path, "PAXG",
        **{"--backing": "ayn", "--attest-due": "2027-01-31"},
    )
    assert result.exit_code == 0, result.output

    row = repo.get_asset_attestation("PAXG")
    assert row is not None
    assert row["attest_due_ts"] == 1_801_353_600  # 2027-01-31T00:00:00Z


def test_attest_without_attest_due_records_null(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    runner = CliRunner()

    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    runner = CliRunner()

    result = _attest(runner, db_path, valid_config_path, "PAXG", **{"--backing": "ayn"})
    assert result.exit_code == 0, result.output

    assert repo.get_asset_attestation("PAXG")["attest_due_ts"] is None


def test_attest_instrument_records_the_supplied_attest_due(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    runner = CliRunner()

    result = _attest_instrument(
        runner, db_path, valid_config_path, "BTC-USD", **{"--attest-due": "2027-01-31"}
    )
    assert result.exit_code == 0, result.output

    row = repo.get_instrument_attestation("coinbase", "BTC-USD")
    assert row is not None
    assert row["attest_due_ts"] == 1_801_353_600


def test_attest_instrument_without_attest_due_records_null(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    runner = CliRunner()

    result = _attest_instrument(runner, db_path, valid_config_path, "BTC-USD")
    assert result.exit_code == 0, result.output

    assert repo.get_instrument_attestation("coinbase", "BTC-USD")["attest_due_ts"] is None


# -- documented allowlist-screen exceptions (waivers) --------------------------
#
# `assets exempt` records a DOCUMENTED, per-asset per-criterion waiver; `assets screen` surfaces
# it loudly (a warning, never a silent pass); `assets unexempt` revokes it. The CLI only lets a
# human waive a criterion in `screen_mod.WAIVABLE_CRITERIA` -- the shariah core is never reachable
# through this surface.


def _exempt(runner, db_path, config_path, **over):
    args = {
        "--asset": "PAXG",
        "--criterion": "history",
        "--rationale": "441 daily bars, human-reviewed",
        "--granted-by": "tester",
    }
    args.update(over)
    flat = [item for pair in args.items() for item in pair]
    return runner.invoke(
        cli, ["--db", str(db_path), "--config", str(config_path), "assets", "exempt", *flat]
    )


def _unexempt(runner, db_path, config_path, asset="PAXG", criterion="history"):
    return runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(config_path),
            "assets",
            "unexempt",
            "--asset",
            asset,
            "--criterion",
            criterion,
        ],
    )


def test_exempt_admits_a_history_failing_asset_and_screen_prints_WAIVED(
    tmp_path, valid_config_path
):
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "PAXG-USD", bars=400)
    runner = CliRunner()
    attested = _attest(runner, db_path, valid_config_path, "PAXG", **{"--backing": "ayn"})
    assert attested.exit_code == 0
    assert _attest_instrument(runner, db_path, valid_config_path, "PAXG-USD").exit_code == 0

    # Before the exception: REJECT on history.
    before = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "screen",
            "--products",
            "PAXG-USD",
        ],
    )
    assert "0/1 admitted" in before.output
    assert "history" in before.output

    result = _exempt(runner, db_path, valid_config_path)
    assert result.exit_code == 0, result.output
    assert "PAXG" in result.output and "history" in result.output

    after = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "screen",
            "--products",
            "PAXG-USD",
        ],
    )
    assert "1/1 admitted" in after.output
    assert "WAIVED" in after.output


def test_exempt_rejects_a_non_waivable_criterion_at_the_cli_boundary(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    result = _exempt(CliRunner(), db_path, valid_config_path, **{"--criterion": "bogus"})
    assert result.exit_code != 0


def test_exempt_rejects_a_shariah_criterion_at_the_cli_boundary(tmp_path, valid_config_path):
    """The Choice restricts to WAIVABLE_CRITERIA -- 'attestation' must never be a valid value."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    result = _exempt(CliRunner(), db_path, valid_config_path, **{"--criterion": "attestation"})
    assert result.exit_code != 0


@pytest.mark.parametrize("blank", ["", "   "])
def test_exempt_rejects_a_blank_rationale(tmp_path, valid_config_path, blank):
    """'documented, never silent' -- a blank rationale is not documentation, so the CLI must
    refuse to record it rather than write an undocumented 'documented exception.'"""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    result = _exempt(CliRunner(), db_path, valid_config_path, **{"--rationale": blank})
    assert result.exit_code != 0


def test_exempt_normalizes_a_lowercase_asset_so_screening_still_finds_the_waiver(
    tmp_path, valid_config_path
):
    """A `--asset paxg` waiver must not silently no-op against the uppercase `PAXG` a product's
    asset code resolves to."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "PAXG-USD", bars=400)
    runner = CliRunner()
    attested = _attest(runner, db_path, valid_config_path, "PAXG", **{"--backing": "ayn"})
    assert attested.exit_code == 0
    assert _attest_instrument(runner, db_path, valid_config_path, "PAXG-USD").exit_code == 0

    result = _exempt(runner, db_path, valid_config_path, **{"--asset": "paxg"})
    assert result.exit_code == 0, result.output
    assert "PAXG" in result.output

    screened = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "screen",
            "--products",
            "PAXG-USD",
        ],
    )
    assert "1/1 admitted" in screened.output
    assert "WAIVED" in screened.output


def test_assets_list_shows_recorded_exceptions(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    runner = CliRunner()
    assert _exempt(runner, db_path, valid_config_path).exit_code == 0

    result = runner.invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "assets", "list"]
    )
    assert "exceptions:" in result.output
    assert "PAXG" in result.output
    assert "history" in result.output
    assert "tester" in result.output


def test_unexempt_revokes_and_screen_rejects_again(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "PAXG-USD", bars=400)
    runner = CliRunner()
    attested = _attest(runner, db_path, valid_config_path, "PAXG", **{"--backing": "ayn"})
    assert attested.exit_code == 0
    assert _attest_instrument(runner, db_path, valid_config_path, "PAXG-USD").exit_code == 0
    assert _exempt(runner, db_path, valid_config_path).exit_code == 0

    admitted = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "screen",
            "--products",
            "PAXG-USD",
        ],
    )
    assert "1/1 admitted" in admitted.output

    revoke = _unexempt(runner, db_path, valid_config_path)
    assert revoke.exit_code == 0, revoke.output
    assert "revoked exception" in revoke.output

    rejected = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "screen",
            "--products",
            "PAXG-USD",
        ],
    )
    assert "0/1 admitted" in rejected.output
    assert "✗" in rejected.output


def test_unexempt_on_a_nonexistent_row_reports_no_such_exception_not_false_success(
    tmp_path, valid_config_path
):
    """Revoking an exception that was never granted must not read as a successful revoke."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    runner = CliRunner()

    result = _unexempt(runner, db_path, valid_config_path)

    assert result.exit_code == 0, result.output
    assert "no such exception" in result.output
    assert "revoked exception" not in result.output


def test_unexempt_normalizes_a_lowercase_asset(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "PAXG-USD", bars=400)
    runner = CliRunner()
    attested = _attest(runner, db_path, valid_config_path, "PAXG", **{"--backing": "ayn"})
    assert attested.exit_code == 0
    assert _exempt(runner, db_path, valid_config_path).exit_code == 0

    revoke = _unexempt(runner, db_path, valid_config_path, asset="paxg")
    assert revoke.exit_code == 0, revoke.output
    assert "revoked exception" in revoke.output

    rejected = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "screen",
            "--products",
            "PAXG-USD",
        ],
    )
    assert "0/1 admitted" in rejected.output


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
        "quote_currency_id": "USD",
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
    venue = _FakeVenue([_venue_product("SOL-USD", "50000000")])
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: venue)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "assets", "discover"]
    )
    assert result.exit_code == 0, result.output
    assert "SOL" in result.output
    # The proposal/admission boundary must be unmissable in the output.
    assert "PROPOSALS, not admissions" in result.output
    assert "attest" in result.output


def test_discover_reports_the_exclusion_summary(tmp_path, valid_config_path, monkeypatch):
    """`discover_candidates` used to drop excluded products with a bare `continue`, so `keel
    assets discover`'s output never said WHY a product vanished between the venue's product
    count and the candidate table -- only the bare `900 -> 40` header line. This pins that the
    CLI now echoes a per-reason summary alongside that header: one product survives, one is
    excluded for the wrong quote currency, and one is excluded for sitting below the 24h
    volume floor."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    venue = _FakeVenue(
        [
            _venue_product("SOL-USD", "50000000"),
            _venue_product("EURPAIR-EUR", "50000000", quote_currency_id="EUR"),
            _venue_product("THIN-USD", "1"),
        ]
    )
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: venue)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "assets", "discover"]
    )

    assert result.exit_code == 0, result.output
    assert "SOL" in result.output
    assert "wrong quote currency 1" in result.output
    assert "below 24h volume floor 1" in result.output
    assert "excluded 2" in result.output


def test_discover_excludes_the_current_allowlist(tmp_path, valid_config_path, monkeypatch):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    venue = _FakeVenue(
        [_venue_product("BTC-USD", "90000000"), _venue_product("SOL-USD", "50000000")]
    )
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: venue)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "assets", "discover"]
    )
    assert "SOL" in result.output
    assert "BTC-USD" not in result.output


def test_discover_default_limit_is_100():
    """Pins the new default. Raised from 25 now that a lower --min-volume-24h surfaces ~130
    candidates instead of ~35 -- 25 would cut off most of a typical sweep before the operator
    ever sees it."""
    cli_option = next(p for p in cli_module.assets_discover.params if p.name == "limit")
    assert cli_option.default == 100


def test_discover_states_total_and_shown_when_limit_truncates(
    tmp_path, valid_config_path, monkeypatch
):
    """The load-bearing case, verified at the CLI: with more candidates than --limit shows, the
    output must say the true candidate count, how many are shown, and that --limit controls it --
    never let a truncated table read as the whole candidate set."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    products = [_venue_product(f"COIN{i}-USD", str(10_000_000 + i)) for i in range(5)]
    venue = _FakeVenue(products)
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: venue)

    result = CliRunner().invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "discover",
            "--limit",
            "3",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "5 venue products -> 5 candidates" in result.output
    assert "showing 3 of 5 candidates" in result.output
    assert "--limit" in result.output
    shown_rows = [ln for ln in result.output.splitlines() if "COIN" in ln and "-USD" in ln]
    assert len(shown_rows) == 3


def test_discover_no_truncation_notice_when_everything_fits(
    tmp_path, valid_config_path, monkeypatch
):
    """No false alarm: when every candidate fits under --limit, nothing should claim a cut
    happened."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    products = [_venue_product(f"COIN{i}-USD", str(10_000_000 + i)) for i in range(3)]
    venue = _FakeVenue(products)
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: venue)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "assets", "discover"]
    )

    assert result.exit_code == 0, result.output
    assert "3 venue products -> 3 candidates" in result.output
    assert "showing" not in result.output


def test_probe_history_marks_candidates_without_a_four_year_series(
    tmp_path, valid_config_path, monkeypatch
):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    venue = _FakeVenue(
        [_venue_product("SOL-USD", "50000000"), _venue_product("NEW-USD", "40000000")],
        history_for=frozenset({"SOL-USD"}),
    )
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: venue)

    result = CliRunner().invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "discover",
            "--probe-history",
        ],
    )
    assert result.exit_code == 0, result.output
    assert set(venue.probe_calls) == {"SOL-USD", "NEW-USD"}
    sol_line = next(ln for ln in result.output.splitlines() if "SOL-USD" in ln)
    new_line = next(ln for ln in result.output.splitlines() if "NEW-USD" in ln)
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

    venue = _BrokenProbe([_venue_product("SOL-USD", "50000000")])
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: venue)

    result = CliRunner().invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "discover",
            "--probe-history",
        ],
    )
    assert result.exit_code == 0
    sol_line = next(ln for ln in result.output.splitlines() if "SOL-USD" in ln)
    assert "?" in sol_line
    assert "NO" not in sol_line


# -- assets holdings: the user's own broker balances as a candidate SOURCE ------
#
# A source, not a gate. Holding an asset is not a reason to trade it, so this command admits
# nothing and writes nothing -- it routes the user's balances through the SAME screen.


class _FakeBroker:
    """Duck-types the port read this command uses: `get_balances()` -> `list[Balance]`."""

    def __init__(self, accounts, fail=False):
        self._accounts = accounts
        self._fail = fail
        self.calls = 0

    def get_balances(self):
        self.calls += 1
        if self._fail:
            raise RuntimeError("venue unreachable")
        return self._accounts


def _account(currency, balance):
    from keel_broker_api.results import Balance

    return Balance(currency=currency, available=Decimal(balance), total=Decimal(balance))


def _with_broker(monkeypatch, broker):
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: broker)


def _holdings(db_path, config_path, *extra):
    return CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(config_path), "assets", "holdings", *extra],
    )


def test_holdings_lists_what_the_user_holds(tmp_path, valid_config_path, monkeypatch):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    _with_broker(monkeypatch, _FakeBroker([_account("BTC", "0.5"), _account("SOL", "12")]))

    result = _holdings(db_path, valid_config_path)

    assert result.exit_code == 0, result.output
    assert "BTC" in result.output and "SOL" in result.output


def test_holdings_excludes_the_settlement_currency_and_fiat(
    tmp_path, valid_config_path, monkeypatch
):
    """You cannot trade the currency you settle in; listing it as a candidate is noise."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    _with_broker(
        monkeypatch,
        _FakeBroker([_account("BTC", "0.5"), _account("USDC", "500"), _account("USD", "100")]),
    )

    result = _holdings(db_path, valid_config_path)

    holding_lines = [ln for ln in result.output.splitlines() if ln.startswith("  ")]
    assets_listed = {ln.split()[0] for ln in holding_lines if ln.split()}
    assert "BTC" in assets_listed
    assert "USD" not in assets_listed, "fiat is funding, not a position"
    assert "USDC" not in assets_listed, "a stablecoin is cash held between positions"


def test_holdings_filters_dust_by_min_balance(tmp_path, valid_config_path, monkeypatch):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    _with_broker(monkeypatch, _FakeBroker([_account("BTC", "0.5"), _account("XLM", "0.00001")]))

    result = _holdings(db_path, valid_config_path, "--min-balance", "0.001")

    assert "BTC" in result.output
    assert "XLM" not in result.output


def test_a_HELD_but_unattested_asset_is_still_REJECTED(tmp_path, valid_config_path, monkeypatch):
    """The point of the whole feature: owning it changes nothing about admission."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "SOL-USD")  # perfect market data...
    _with_broker(monkeypatch, _FakeBroker([_account("SOL", "12")]))

    result = _holdings(db_path, valid_config_path, "--screen")

    assert result.exit_code == 0, result.output
    assert "REJECT" in result.output
    assert "attestation: MISSING" in result.output


def test_holdings_screen_agrees_with_assets_screen_for_the_same_asset(
    tmp_path, valid_config_path, monkeypatch
):
    """One gate, shared by construction -- a proposer must not get a laxer path."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "BTC-USD")
    runner = CliRunner()
    assert _attest(runner, db_path, valid_config_path, "BTC").exit_code == 0
    assert _attest_instrument(runner, db_path, valid_config_path, "BTC-USD").exit_code == 0
    _with_broker(monkeypatch, _FakeBroker([_account("BTC", "0.5")]))

    screened = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "screen",
            "--products",
            "BTC-USD",
        ],
    )
    held = _holdings(db_path, valid_config_path, "--screen")

    # Assert the verdict POSITIVELY -- `x in a == x in b` also passes when both are False, or
    # when holdings prints ADMIT unconditionally.
    assert "ADMIT" in screened.output
    assert "ADMIT" in held.output


def test_no_local_history_is_reported_as_MISSING_DATA_not_a_bad_asset(
    tmp_path, valid_config_path, monkeypatch
):
    """The likeliest misreading: a rejection for zero cached bars says nothing about the asset."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)  # no candles seeded at all
    _with_broker(monkeypatch, _FakeBroker([_account("SOL", "12")]))

    result = _holdings(db_path, valid_config_path, "--screen")

    assert "no local history" in result.output
    assert "keel fetch" in result.output


def test_holdings_writes_nothing(tmp_path, valid_config_path, monkeypatch):
    """A read-only report: no attestation, no allowlist change, no DB mutation."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "SOL-USD")
    _with_broker(monkeypatch, _FakeBroker([_account("SOL", "12")]))

    _holdings(db_path, valid_config_path, "--screen")

    assert repo.get_asset_attestations() == []
    assert _repo_at(db_path).get_asset_attestation("SOL") is None


def test_a_broker_failure_is_an_ERROR_not_an_empty_clean_result(
    tmp_path, valid_config_path, monkeypatch
):
    """An unreachable venue must not read as 'you hold nothing suspicious'."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    _with_broker(monkeypatch, _FakeBroker([], fail=True))

    result = _holdings(db_path, valid_config_path)

    assert result.exit_code != 0
    assert "unreachable" in result.output.lower() or "error" in result.output.lower()


def test_holdings_auth_advice_names_the_coinbase_env_vars(tmp_path, valid_config_path, monkeypatch):
    """The auth hint is actionable only if it names the keys THIS deployment reads: on the
    default (coinbase) config that is the CDP pair -- the historical advice, unchanged."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    _with_broker(monkeypatch, _FakeBroker([], fail=True))

    result = _holdings(db_path, valid_config_path)

    assert result.exit_code != 0
    assert "CDP_API_KEY" in result.output
    assert "CDP_API_SECRET" in result.output
    assert "ALPACA_API_KEY_ID" not in result.output


def test_holdings_auth_advice_names_the_alpaca_env_vars(tmp_path, write_config, monkeypatch):
    """The alpaca mirror: an operator whose `broker:` section selects alpaca never reads a
    CDP credential, so the advice must point at the Alpaca pair instead (#386 review: advice
    that names the wrong venue's keys sends the operator hunting a credential this
    deployment never uses)."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    config_path = write_config(
        VALID_CONFIG_YAML + "\nbroker:\n  name: alpaca\n  endpoint: paper\n  data_feed: iex\n"
    )
    _with_broker(monkeypatch, _FakeBroker([], fail=True))

    result = _holdings(db_path, config_path)

    assert result.exit_code != 0
    assert "ALPACA_API_KEY_ID" in result.output
    assert "ALPACA_API_SECRET_KEY" in result.output
    assert "CDP_API_KEY" not in result.output


def test_holdings_marks_assets_already_on_the_allowlist(tmp_path, valid_config_path, monkeypatch):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    _with_broker(monkeypatch, _FakeBroker([_account("BTC", "0.5"), _account("SOL", "12")]))

    result = _holdings(db_path, valid_config_path)

    btc_line = next(ln for ln in result.output.splitlines() if ln.strip().startswith("BTC"))
    sol_line = next(ln for ln in result.output.splitlines() if ln.strip().startswith("SOL"))
    # "not-on-allowlist" CONTAINS "on-allowlist", so the negative must be excluded explicitly --
    # asserting the substring alone passes even if the check is inverted.
    assert "on-allowlist" in btc_line and "not-on-allowlist" not in btc_line
    assert "not-on-allowlist" in sol_line


def test_holdings_screen_does_not_DROP_compliance_warnings(
    tmp_path, valid_config_path, monkeypatch
):
    """`ScreenResult.warnings` carry constraints that bind even on an ADMITted asset (§65.5's
    bay' al-sarf regime for gold/silver backing). Dropping them made this command quietly less
    informative than `assets screen` for the very same asset."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "PAXG-USD")
    runner = CliRunner()
    assert (
        _attest(runner, db_path, valid_config_path, "PAXG", **{"--backing": "ayn"}).exit_code == 0
    )
    _with_broker(monkeypatch, _FakeBroker([_account("PAXG", "3")]))

    screened = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "screen",
            "--products",
            "PAXG-USD",
        ],
    )
    held = _holdings(db_path, valid_config_path, "--screen")

    assert "bay' al-sarf" in screened.output, "fixture no longer triggers the warning"
    assert "bay' al-sarf" in held.output, "holdings dropped a compliance warning"


def test_derivative_failures_are_not_asserted_as_verdicts_without_history(
    tmp_path, valid_config_path, monkeypatch
):
    """With zero cached bars, liquidity and settlement report on our DATA, not the asset:
    median volume is 0 because there are no bars. Printing them as findings would assert
    exactly what the missing-data message exists to deny.

    `history` used to be exempted from this and printed as `0 daily bars, need 1460` --
    itself the same lie in different clothes: zero bars means we never fetched the asset, not
    that it is too young. See `test_zero_cached_bars_never_prints_a_history_depth_failure`
    below for that invariant asserted head-on; this test was updated (not just extended) to
    stop asserting the old, wrong behaviour."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    _with_broker(monkeypatch, _FakeBroker([_account("SOL", "12")]))

    result = _holdings(db_path, valid_config_path, "--screen")

    assert "no local history" in result.output
    assert "✗ settlement" not in result.output, "settlement is a naming artifact here"
    assert "✗ liquidity" not in result.output, "median volume is 0 only because bars are 0"
    assert "✗ history" not in result.output, "zero bars is a cache gap, not a history verdict"
    assert "not assessable until then" in result.output


def test_zero_cached_bars_never_prints_a_history_depth_failure(
    tmp_path, valid_config_path, monkeypatch
):
    """The headline invariant for `keel assets holdings --screen`: at ZERO cached bars, the
    output must never contain a `✗ history: 0 daily bars, need 1460` line. That line reads as
    "this asset is too young" when the truth is "we have never fetched it" -- a candidate that
    was never fetched must be indistinguishable, in the failure list, from one this deployment
    has simply not pulled data for yet, and distinguishable from one that is genuinely too
    young (see the 400-bar counterpart test below)."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)  # no candles seeded at all
    _with_broker(monkeypatch, _FakeBroker([_account("SOL", "12")]))

    result = _holdings(db_path, valid_config_path, "--screen")

    assert "✗ history" not in result.output
    assert "no local history" in result.output
    assert "keel fetch" in result.output
    assert "not assessable until then" in result.output


def test_a_genuinely_young_asset_still_reports_history_as_a_real_verdict_via_holdings(
    tmp_path, valid_config_path, monkeypatch
):
    """The counterpart to the zero-bars invariant above: the suppression must be scoped to
    EXACTLY zero bars. An asset with SOME cached history that is still short of the 1460-bar
    floor is genuinely too young, and `holdings --screen` must keep saying so -- proving the fix
    closes one specific lie (zero bars misread as "too young") rather than becoming a blanket
    silencer for every `history` verdict."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "PAXG-USD", bars=400)  # real bars, genuinely short of the floor
    runner = CliRunner()
    assert (
        _attest(runner, db_path, valid_config_path, "PAXG", **{"--backing": "ayn"}).exit_code == 0
    )
    _with_broker(monkeypatch, _FakeBroker([_account("PAXG", "3")]))

    result = _holdings(db_path, valid_config_path, "--screen")

    assert "✗ history" in result.output
    assert "no local history" not in result.output


def test_zero_cached_bars_never_prints_a_history_depth_failure_via_assets_screen(
    tmp_path, valid_config_path
):
    """The same invariant as the `holdings --screen` and `propose` versions, for `assets screen`.

    This command is the SIBLING of the TUI's `s` screen overlay -- both screen the same
    `_default_sim_products(config)` set through the same `_screen_product` gate -- so if only one
    of them explains a zero-bar cache, an operator gets two different stories about the same
    allowlist depending on which surface they happened to look at. That is precisely the drift
    `_screen_product`'s docstring exists to prevent, applied to the REPORTING of a verdict rather
    than to the verdict itself.
    """
    db_path = tmp_path / "t.db"
    _repo_at(db_path)  # no candles seeded at all

    result = CliRunner().invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "screen",
            "--products",
            "SOL-USD",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "✗ history" not in result.output
    assert "no local history" in result.output
    assert "keel fetch" in result.output
    assert "not assessable until then" in result.output


def test_assets_screen_still_reports_a_genuinely_short_history_as_a_real_verdict(
    tmp_path, valid_config_path
):
    """The counterpart: the suppression is scoped to EXACTLY zero bars here too, so an asset that
    really is too young still gets told so by `assets screen`."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "PAXG-USD", bars=400)

    result = CliRunner().invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "screen",
            "--products",
            "PAXG-USD",
        ],
    )

    assert "✗ history" in result.output
    assert "no local history" not in result.output


def test_a_lowercase_settlement_currency_is_still_excluded(
    tmp_path, valid_config_path, monkeypatch
):
    """A casing accident must not present the currency you settle in as tradable."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    _with_broker(monkeypatch, _FakeBroker([_account("btc", "0.5"), _account("usdc", "500")]))

    result = _holdings(db_path, valid_config_path)

    listed = {ln.split()[0].upper() for ln in result.output.splitlines() if ln.startswith("  ")}
    assert "USDC" not in listed
    assert "BTC" in listed


def test_min_balance_rejects_garbage_and_non_finite_values(
    tmp_path, valid_config_path, monkeypatch
):
    """A NaN floor makes every comparison raise; a negative one lists every zero balance."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    _with_broker(monkeypatch, _FakeBroker([_account("BTC", "0.5")]))

    for bad in ("abc", "nan", "-1", "inf"):
        result = _holdings(db_path, valid_config_path, "--min-balance", bad)
        assert result.exit_code != 0, f"--min-balance {bad} should be rejected: {result.output}"


def test_the_derived_failure_tags_actually_match_screen_asset_output():
    """Pins the string coupling that the zero-bars suppression depends on.

    `screen.split_failures` suppresses cache-derived failures by matching `failure.split(":")[0]`
    against `DATA_DERIVED_FAILURES`. Renaming a tag in `screen_asset` would silently stop the
    suppression -- reintroducing 'data artifacts printed as verdicts about the asset' with a
    fully green suite. This asserts the tags are real.

    Reads the constant from `screen.py` rather than through `keel.cli`: the split moved into
    `screen.py` beside the tag set, so `cli.py` no longer names the constant at all, and an
    alias kept alive purely to be asserted on here would prove nothing about live code.
    """
    from keel.compliance import screen as screen_mod

    facts = screen_mod.MarketFacts(
        asset="SOL",
        daily_bars=0,
        median_daily_volume=Decimal(0),
        quotable_in_settlement_currency=False,
        product_id="SOL-EUR",
        venue="coinbase",
    )
    tags = {f.split(":")[0] for f in screen_mod.screen_asset(facts, None).failures}
    # `liquidity` and `liquidity_unmeasured` are mutually exclusive by construction (#696): which
    # one a below-floor series produces depends on whether its feed sees the whole market, so no
    # single set of facts can emit both. The partial-feed arm is screened here too, or the tag
    # would look unreachable and the suppression would be dropped as dead.
    partial = replace(facts, volume_feed="alpaca:iex", volume_feed_is_consolidated=False)
    tags |= {f.split(":")[0] for f in screen_mod.screen_asset(partial, None).failures}

    missing = screen_mod.DATA_DERIVED_FAILURES - tags
    assert not missing, (
        f"{missing} no longer appear as failure tags in screen_asset -- the zero-bars "
        "suppression is now silently inert; update DATA_DERIVED_FAILURES"
    )


def test_a_lowercase_holding_is_screened_as_the_attested_uppercase_asset(
    tmp_path, valid_config_path, monkeypatch
):
    """A `btc` balance must not read as UNATTESTED while `BTC` is attested, nor be handed a
    `btc-USD` fetch hint that will never resolve."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "BTC-USD")
    runner = CliRunner()
    assert _attest(runner, db_path, valid_config_path, "BTC").exit_code == 0
    assert _attest_instrument(runner, db_path, valid_config_path, "BTC-USD").exit_code == 0
    _with_broker(monkeypatch, _FakeBroker([_account("btc", "0.5")]))

    result = _holdings(db_path, valid_config_path, "--screen")

    assert "UNATTESTED" not in result.output
    assert "btc-USD" not in result.output
    assert "ADMIT" in result.output


def test_an_account_with_no_currency_field_does_not_crash(tmp_path, valid_config_path, monkeypatch):
    """The port's `Balance.currency` is a str, but a venue row with no currency coerces to the
    empty string -- an unusable asset code must degrade to a blank row, never a crash."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    broken = _account("", "1")
    _with_broker(monkeypatch, _FakeBroker([broken, _account("BTC", "0.5")]))

    result = _holdings(db_path, valid_config_path)

    assert result.exit_code == 0, result.output
    assert "BTC" in result.output


# -- product ids derive from the configured settlement currency ----------------


def test_product_ids_derive_from_the_configured_settlement_currency(tmp_path):
    """Regression for the legacy-config break: hardcoding `-USD` here while the settlement check
    compared against config made every `quote_currency: USDC` deployment reject every asset on a
    settlement failure it could never fix. Both derivations must share one source."""
    from keel.cli import _default_sim_products, _history_product
    from keel.config import load_config
    from tests.conftest import VALID_CONFIG_YAML

    assert _history_product("BTC", "USD") == "BTC-USD"
    assert _history_product("BTC", "usdc") == "BTC-USDC"

    legacy = tmp_path / "legacy.yaml"
    legacy.write_text(VALID_CONFIG_YAML.replace("quote_currency: USD", "quote_currency: USDC"))
    assert all(p.endswith("-USDC") for p in _default_sim_products(load_config(str(legacy))))

    default = tmp_path / "default.yaml"
    default.write_text(VALID_CONFIG_YAML)
    assert all(p.endswith("-USD") for p in _default_sim_products(load_config(str(default))))


def test_the_settlement_criterion_still_catches_an_EXTERNALLY_supplied_product(
    tmp_path, valid_config_path
):
    """Honesty about what this criterion does. For a product WE derive it is true by
    construction. It is not dead: it catches a product supplied from outside that derivation --
    `--products`, or a future venue/LLM-sourced list -- whose quote leg would need a second
    exchange leg (a cross) to settle. That is the §65.7 case it exists for."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "BTC-EUR")
    runner = CliRunner()
    assert _attest(runner, db_path, valid_config_path, "BTC").exit_code == 0

    result = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "screen",
            "--products",
            "BTC-EUR",
        ],  # settlement is USD
    )

    assert "settlement" in result.output, "a cross-settled product must fail the settlement check"
    assert "REJECT" in result.output


def test_screen_REPORTS_on_a_futures_id_rather_than_refusing_the_option(
    tmp_path, valid_config_path
):
    """`assets screen` is the ONE `--products` caller that does not validate its option, and this
    pins that exception (feasibility study R2).

    Every other caller -- `fetch`, `monitor`, `simulate`, `rules seed` -- refuses an id keel
    cannot trade at the keyboard. Screening must not, because screening is the command that
    ANSWERS "may keel trade this, and why not". A usage error would make the one tool whose job
    is to explain an inadmissible asset the one tool that cannot be asked about one. It writes
    nothing and orders nothing; rails 18/19 stop the id if it ever reaches an order.
    """
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "ADA-28AUG26-CDE")
    runner = CliRunner()
    assert _attest(runner, db_path, valid_config_path, "ADA").exit_code == 0

    result = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "screen",
            "--products",
            "ADA-28AUG26-CDE",
        ],
    )

    assert result.exit_code == 0, "screening must report a verdict, not a usage error"
    assert "REJECT" in result.output
    # The verdict carries the REASON -- which is the whole point of not refusing the option.
    assert "settlement" in result.output


def test_screen_REJECTS_the_derivative_shaped_id_rail_19_exists_to_refuse(
    tmp_path, valid_config_path
):
    """The residual, asked of the screen instead of the rails (feasibility study R2).

    `ADA-28AUG26-CDE` above fails on SETTLEMENT (`CDE` is not a configured currency), so it
    never exercised the shape question at all. `BTC-PERP-USD` is the id that does: its quote leg
    IS `USD`, so the settlement criterion admits it, and with attested BTC and cached history
    every other criterion admitted it too. The one command whose job is answering "may keel
    trade this" said ADMIT about the one product this rail exists to refuse.

    The exemption from `--products` validation is preserved -- the screen still REPORTS rather
    than refusing -- but what it reports is now the truth.
    """
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "BTC-PERP-USD")
    runner = CliRunner()
    assert _attest(runner, db_path, valid_config_path, "BTC").exit_code == 0

    result = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "screen",
            "--products",
            "BTC-PERP-USD",
        ],
    )

    assert result.exit_code == 0, "screening must report a verdict, not a usage error"
    assert "REJECT" in result.output
    assert "spot_instrument" in result.output
    assert "BTC-PERP-USD" in result.output, "the verdict must name the id it is about"


def test_screen_still_ADMITS_a_well_formed_spot_pair(tmp_path, valid_config_path):
    """The new criterion must not cost the screen its ordinary answer: the same seeded,
    attested BTC on a real spot id is still ADMITted."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "BTC-USD")
    runner = CliRunner()
    assert _attest(runner, db_path, valid_config_path, "BTC").exit_code == 0
    assert _attest_instrument(runner, db_path, valid_config_path, "BTC-USD").exit_code == 0

    result = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "screen",
            "--products",
            "BTC-USD",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "ADMIT" in result.output
    assert "spot_instrument" not in result.output


# -- instrument attestations (issue #202): the LISTING is a separate claim from the ASSET --------
#
# `keel assets attest` says what the underlying is; `keel assets attest-instrument` says what
# CONTRACT this venue listing actually is. Admission now needs both, and the point of splitting
# them is that a spot-admissible underlying says nothing about a CFD/perp/future wrapped around
# it -- leverage, swap financing and counterparty exposure are properties of the CONTRACT.


def test_screen_REJECTS_a_fully_asset_attested_product_with_no_instrument_attestation(
    tmp_path, valid_config_path
):
    """The fail-closed default has to be ACTIONABLE, not just correct. An operator who has done
    the asset-side work (sector, backing, source) and sees REJECT must be told the ONE remaining
    thing they owe -- the exact command, not just the word 'unattested' -- or the fail-closed
    default becomes a dead end instead of a checklist."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "BTC-USD")
    runner = CliRunner()
    assert _attest(runner, db_path, valid_config_path, "BTC").exit_code == 0

    result = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "screen",
            "--products",
            "BTC-USD",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "REJECT" in result.output
    assert "instrument_wrapper" in result.output
    assert "keel assets attest-instrument" in result.output, (
        "a missing instrument attestation must name the exact remedy, not just say 'unattested'"
    )


def test_asset_and_spot_instrument_attestation_together_ADMIT(tmp_path, valid_config_path):
    """The two attestations are complementary, not redundant -- both must be present to admit,
    and both present with `--wrapper spot` is precisely the case that should clear the gate."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "BTC-USD")
    runner = CliRunner()
    assert _attest(runner, db_path, valid_config_path, "BTC").exit_code == 0
    assert _attest_instrument(runner, db_path, valid_config_path, "BTC-USD").exit_code == 0

    result = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "screen",
            "--products",
            "BTC-USD",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "ADMIT" in result.output


def test_a_cfd_wrapper_on_an_admissible_underlying_still_REJECTS(tmp_path, valid_config_path):
    """Issue #202's acceptance case at the CLI level: the underlying (BTC, spot-admissible) is
    fully attested and would ADMIT as spot, but this listing is attested as a CFD. The contract,
    not the underlying, is what this criterion polices -- leverage, swap financing and
    counterparty exposure survive any care taken over the asset attestation, so ADMIT here would
    be exactly the leak issue #202 was filed to close."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "BTC-USD")
    runner = CliRunner()
    assert _attest(runner, db_path, valid_config_path, "BTC").exit_code == 0
    assert (
        _attest_instrument(
            runner, db_path, valid_config_path, "BTC-USD", **{"--wrapper": "cfd"}
        ).exit_code
        == 0
    )

    result = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "screen",
            "--products",
            "BTC-USD",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "REJECT" in result.output
    assert "instrument_wrapper" in result.output
    assert "'cfd'" in result.output, "the verdict must name the wrapper it refused"


def test_attest_instrument_rejects_an_unknown_wrapper_at_the_cli_boundary(
    tmp_path, valid_config_path
):
    """`--wrapper` is a `click.Choice` driven by `screen_mod.KNOWN_WRAPPERS` -- a made-up wrapper
    name must be refused at the keyboard, the same boundary `assets attest --backing` and `assets
    exempt --criterion` already enforce for their own Choice-typed options, rather than being
    recorded and only failing later at screen time."""
    from keel.compliance import screen as screen_mod

    assert "banana" not in screen_mod.KNOWN_WRAPPERS, "fixture must actually be an unknown value"

    # The Choice must be DRIVEN by `screen_mod.KNOWN_WRAPPERS`, not a second, hardcoded list that
    # could silently drift from it -- inspect the live Click param rather than assuming.
    attest_instrument_cmd = cli.commands["assets"].commands["attest-instrument"]
    wrapper_param = next(p for p in attest_instrument_cmd.params if p.name == "wrapper")
    assert set(wrapper_param.type.choices) == screen_mod.KNOWN_WRAPPERS

    db_path = tmp_path / "t.db"
    _repo_at(db_path)

    result = _attest_instrument(
        CliRunner(), db_path, valid_config_path, "BTC-USD", **{"--wrapper": "banana"}
    )

    assert result.exit_code != 0
    assert "--wrapper" in result.output


def test_assets_list_renders_an_instrument_attestation(tmp_path, valid_config_path):
    """Both KINDS of attestation must be visible from `assets list`, or an operator reading only
    the asset half sees a fully-attested allowlist that still screens REJECT for a reason the
    listing never shows them."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    runner = CliRunner()
    assert _attest_instrument(runner, db_path, valid_config_path, "BTC-USD").exit_code == 0

    result = runner.invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "assets", "list"]
    )

    assert "instruments:" in result.output
    assert "BTC-USD" in result.output
    assert "coinbase" in result.output
    assert "spot" in result.output


def test_attest_instrument_normalizes_a_lowercase_product_so_screening_still_finds_it(
    tmp_path, valid_config_path
):
    """Mirrors `test_exempt_normalizes_a_lowercase_asset_so_screening_still_finds_the_waiver`: a
    `--product btc-usd` statement must not silently no-op against the uppercase `BTC-USD` that
    `_screen_product` looks the row up by -- an operator who typed the id in lowercase must not
    be told UNATTESTED for a statement they already recorded."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "BTC-USD")
    runner = CliRunner()
    assert _attest(runner, db_path, valid_config_path, "BTC").exit_code == 0

    result = _attest_instrument(runner, db_path, valid_config_path, "btc-usd")
    assert result.exit_code == 0, result.output
    assert "BTC-USD" in result.output

    screened = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "screen",
            "--products",
            "BTC-USD",
        ],
    )
    assert "ADMIT" in screened.output


# -- assets propose -----------------------------------------------------------------------------


def _write_shortlist(tmp_path, candidates):
    path = tmp_path / "shortlist.json"
    path.write_text(json.dumps({"candidates": candidates}))
    return path


_SOL = {
    "asset": "SOL",
    "rationale": "high liquidity",
    "sources": ["https://coinmarketcap.com/currencies/solana/"],
}


def test_propose_rejects_an_unattested_candidate(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    shortlist = _write_shortlist(tmp_path, [_SOL])
    result = CliRunner().invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "propose",
            "--from",
            str(shortlist),
        ],
    )
    assert result.exit_code == 0
    assert "REJECT" in result.output
    assert "0/1 admitted" in result.output


def test_zero_cached_bars_never_prints_a_history_depth_failure_via_propose(
    tmp_path, valid_config_path
):
    """Same invariant as the `holdings --screen` version above, for `keel assets propose`: a
    candidate with ZERO cached bars must not print a `✗ history: 0 daily bars, need 1460` line.
    That line is exactly the lie the MISSING-DATA explanation two lines above it exists to
    deny -- "too young" and "never fetched" must not be indistinguishable in the failure list."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)  # no candles seeded -- SOL has zero cached bars
    shortlist = _write_shortlist(tmp_path, [_SOL])

    result = CliRunner().invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "propose",
            "--from",
            str(shortlist),
        ],
    )

    assert result.exit_code == 0
    assert "✗ history" not in result.output
    assert "no local history" in result.output
    assert "keel fetch" in result.output
    assert "not assessable until then" in result.output


def test_propose_and_screen_agree_for_the_same_asset(tmp_path, valid_config_path):
    """One gate, shared by construction -- the proposer must not get a laxer path."""
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_history(repo, "BTC-USD")
    runner = CliRunner()
    assert _attest(runner, db_path, valid_config_path, "BTC").exit_code == 0
    assert _attest_instrument(runner, db_path, valid_config_path, "BTC-USD").exit_code == 0
    shortlist = _write_shortlist(
        tmp_path,
        [{"asset": "BTC", "rationale": "reserve asset", "sources": ["https://bitcoin.org"]}],
    )
    proposed = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "propose",
            "--from",
            str(shortlist),
        ],
    )
    screened = runner.invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "screen",
            "--products",
            "BTC-USD",
        ],
    )
    assert "ADMIT" in proposed.output
    assert "ADMIT" in screened.output


def test_propose_writes_nothing(tmp_path, valid_config_path):
    """A read-only report: no attestation, no allowlist change, no DB mutation."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    shortlist = _write_shortlist(tmp_path, [_SOL])
    CliRunner().invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "propose",
            "--from",
            str(shortlist),
        ],
    )
    # Reopen from the path (not the handle held from before the run) so a stray write to ANY
    # asset/table would actually be caught, not just the one candidate we happened to propose.
    assert _repo_at(db_path).get_asset_attestations() == []


def test_propose_json_is_valid_and_has_no_trailing_prose(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    shortlist = _write_shortlist(tmp_path, [_SOL])
    result = CliRunner().invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "propose",
            "--from",
            str(shortlist),
            "--json",
        ],
    )
    payload = json.loads(result.output)  # must parse cleanly
    assert payload["admitted_count"] == 0
    assert payload["screened"][0]["asset"] == "SOL"


def test_propose_json_tells_the_same_zero_bar_story_the_human_output_tells(
    tmp_path, valid_config_path
):
    """End-to-end companion to `test_zero_cached_bars_never_prints_a_history_depth_failure_via_
    propose`, through the REAL gate: the human surface suppresses `history: 0 daily bars < 1460
    required` and prints the MISSING-DATA explanation, so `--json` -- the surface a script trusts
    -- must not hand back that same line as an unflagged verdict about the asset."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)  # no candles seeded -- SOL has zero cached bars
    shortlist = _write_shortlist(tmp_path, [_SOL])

    result = CliRunner().invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "propose",
            "--from",
            str(shortlist),
            "--json",
        ],
    )

    row = json.loads(result.output)["screened"][0]
    assert row["daily_bars"] == 0
    assert row["missing_history"] is True
    assert not any(f.startswith("history") for f in row["failures"])
    assert any(f.startswith("history") for f in row["not_assessable"])


def test_propose_missing_file_is_a_clean_error(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    result = CliRunner().invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "propose",
            "--from",
            str(tmp_path / "nope.json"),
        ],
    )
    assert result.exit_code != 0


def test_propose_non_utf8_shortlist_is_a_clean_error_not_a_traceback(tmp_path, valid_config_path):
    """`UnicodeDecodeError` subclasses **ValueError, not OSError**, so the original
    `except (OSError, json.JSONDecodeError)` did not catch it: a UTF-16LE+BOM shortlist (valid
    JSON, and what a scout run on a Windows box writes) crashed out of `assets propose` with a
    raw traceback instead of the `could not read/parse <file>` message every other unreadable
    input gets. Exit code alone is not enough here -- an uncaught exception also exits non-zero,
    which is why the message and the absence of a traceback are both pinned."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    shortlist = tmp_path / "shortlist.json"
    shortlist.write_bytes(json.dumps({"candidates": [_SOL]}).encode("utf-16"))

    result = CliRunner().invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "propose",
            "--from",
            str(shortlist),
        ],
    )

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "could not read/parse" in result.output
    assert str(shortlist) in result.output


def test_propose_hypothesis_never_admits(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    shortlist = _write_shortlist(
        tmp_path,
        [
            {
                "asset": "SOL",
                "rationale": "x",
                "sources": ["https://x.invalid"],
                "shariah_hypothesis": "definitely halal",
            }
        ],
    )
    result = CliRunner().invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "propose",
            "--from",
            str(shortlist),
        ],
    )
    assert "REJECT" in result.output  # unattested + no history => rejected despite the hypothesis
    assert "UNVERIFIED" in result.output


def test_propose_human_output_ends_with_the_disclaimer(tmp_path, valid_config_path):
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    shortlist = _write_shortlist(tmp_path, [_SOL])
    result = CliRunner().invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "propose",
            "--from",
            str(shortlist),
        ],
    )
    assert DISCLAIMER in result.output


class _VolumeVenue(_FakeVenue):
    """Serves canned daily candles per product so the liquidity probe has something to measure."""

    def __init__(self, products, quote_volume_for):
        super().__init__(products)
        self._quote_volume_for = quote_volume_for

    def get_candles(self, product_id, granularity, start, end):
        self.probe_calls.append(product_id)
        per_bar = self._quote_volume_for.get(product_id)
        if per_bar is None:
            return []
        return [
            Candle(
                ts=i * _DAY,
                open=Decimal("1"),
                high=Decimal("1"),
                low=Decimal("1"),
                close=Decimal("1"),
                volume=Decimal(per_bar),
            )
            for i in range(30)
        ]


def test_probe_liquidity_flags_a_candidate_whose_24h_snapshot_beats_its_median(
    tmp_path, valid_config_path, monkeypatch
):
    """The BICO case: one spike day clears the sweep's floor while the typical day fails the gate.

    BICO was shortlisted 2026-08-08 on a reported $12.81M/24h and then rejected by the screen at
    a median daily volume of 108,004 -- 9x under the floor. The sweep and the gate were measuring
    different statistics, so the pre-filter could not see it.
    """
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    venue = _VolumeVenue(
        [_venue_product("BICO-USD", "12810000"), _venue_product("SOL-USD", "50000000")],
        quote_volume_for={"BICO-USD": "108004", "SOL-USD": "40000000"},
    )
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: venue)

    result = CliRunner().invoke(
        cli,
        [
            "--db",
            str(db_path),
            "--config",
            str(valid_config_path),
            "assets",
            "discover",
            "--probe-liquidity",
        ],
    )

    assert result.exit_code == 0, result.output
    bico_line = next(ln for ln in result.output.splitlines() if "BICO-USD" in ln)
    sol_line = next(ln for ln in result.output.splitlines() if "SOL-USD" in ln)
    assert "LOW" in bico_line, bico_line
    assert "LOW" not in sol_line, sol_line


def test_a_compact_date_is_refused_rather_than_read_as_a_1970_timestamp(
    tmp_path, valid_config_path
) -> None:
    """`--attest-due 20270131` is the obvious shorthand for a command whose other accepted form
    is `YYYY-MM-DD`. `_parse_ts` tries `int()` first, so it was accepted as a unix timestamp and
    recorded a window closing in August 1970 -- an attestation already expired on the day it was
    made, at exit 0 with no output saying so.

    This is the first operator-supplied expiry in the codebase (subscriptions, scopes and cash
    postures all compute `now + TTL`), so it is the first one that could be typed wrong.
    """
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    runner = CliRunner()

    result = _attest(
        runner, db_path, valid_config_path, "PAXG",
        **{"--backing": "ayn", "--attest-due": "20270131"},
    )

    assert result.exit_code != 0
    assert "2027-01-31" in result.output, "the refusal must show the form that works"
    assert repo.get_asset_attestation("PAXG") is None


def test_the_recorded_window_is_echoed_back(tmp_path, valid_config_path) -> None:
    """`commands/posture.py` and `commands/brokers.py` both echo `expires=<utc date>` for exactly
    this reason: a window the operator cannot see is a window they cannot notice is wrong."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    runner = CliRunner()

    result = _attest(
        runner, db_path, valid_config_path, "PAXG",
        **{"--backing": "ayn", "--attest-due": "2027-01-31"},
    )

    assert result.exit_code == 0, result.output
    assert "expires=2027-01-31" in result.output


def test_an_attestation_without_a_window_echoes_a_dash(tmp_path, valid_config_path) -> None:
    """Not `expires=None` and not a silently absent field -- the same `-` the sibling commands
    print, so "no window recorded" is visible rather than inferred from what is missing."""
    db_path = tmp_path / "t.db"
    _repo_at(db_path)
    runner = CliRunner()

    result = _attest(runner, db_path, valid_config_path, "PAXG", **{"--backing": "ayn"})

    assert result.exit_code == 0, result.output
    assert "expires=-" in result.output

