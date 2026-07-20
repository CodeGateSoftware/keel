"""Income purification (KB §65.9)."""

from __future__ import annotations

from decimal import Decimal

from keel.analysis.pnl import realized_pnl
from keel.compliance.purification import (
    CLEAN,
    NON_COMPLIANT,
    REVIEW,
    build_report,
    classify,
)


def _tx(tx_type, asset="BTC", qty="0.001", total="3.00", ts=1_700_000_000):
    return {"type": tx_type, "asset": asset, "qty": qty, "total": total, "ts": ts}


# -- classification against REAL Coinbase export strings -----------------------


def test_the_real_reward_types_in_our_own_history_are_non_compliant():
    """Both of these appear in this project's actual imported transaction history."""
    assert classify("Reward Income") == NON_COMPLIANT
    assert classify("Incentives Rewards Payout") == NON_COMPLIANT


def test_the_real_trading_types_in_our_own_history_are_clean():
    for tx_type in ("Buy", "Advanced Trade Buy", "Convert", "Deposit"):
        assert classify(tx_type) == CLEAN, tx_type


def test_other_yield_shapes_are_non_compliant():
    for tx_type in ("Staking Income", "Interest", "Inflation Reward", "Learning Reward", "Rebate"):
        assert classify(tx_type) == NON_COMPLIANT, tx_type


def test_an_unrecognised_type_is_REVIEW_never_a_silent_default():
    """Clean would let riba into P&L; non-compliant would misstate an obligation as fact."""
    assert classify("Some New Coinbase Thing") == REVIEW
    assert classify("") == REVIEW
    assert classify(None) == REVIEW


def test_non_compliant_wins_when_a_type_names_both():
    assert classify("Reward Buy") == NON_COMPLIANT


def test_classification_is_case_and_whitespace_insensitive():
    assert classify("  rEwArD iNcOmE  ") == NON_COMPLIANT


# -- the report ----------------------------------------------------------------


def test_report_totals_only_the_non_compliant_credits():
    report = build_report(
        [
            _tx("Buy", total="500"),
            _tx("Reward Income", total="3.00"),
            _tx("Reward Income", total="1.50"),
            _tx("Advanced Trade Buy", total="200"),
        ]
    )
    assert len(report.entries) == 2
    assert report.total_owed_usd == Decimal("4.50")


def test_report_breaks_down_by_asset_in_both_value_and_units():
    report = build_report(
        [
            _tx("Reward Income", asset="BTC", qty="0.001", total="3.00"),
            _tx("Reward Income", asset="BTC", qty="0.002", total="6.00"),
            _tx("Reward Income", asset="ETH", qty="0.10", total="30.00"),
        ]
    )
    assert report.owed_by_asset == {"BTC": Decimal("9.00"), "ETH": Decimal("30.00")}
    assert report.qty_by_asset == {"BTC": Decimal("0.003"), "ETH": Decimal("0.10")}


def test_unknown_types_land_in_needs_review_not_in_the_amount_owed():
    report = build_report([_tx("Mystery Credit", total="99")])
    assert report.entries == []
    assert len(report.needs_review) == 1
    assert report.total_owed_usd == Decimal("0")


def test_currency_formatting_from_the_csv_is_parsed():
    report = build_report([_tx("Reward Income", total="$1,234.56")])
    assert report.total_owed_usd == Decimal("1234.56")


def test_a_malformed_amount_degrades_to_zero_rather_than_aborting_the_report():
    report = build_report([_tx("Reward Income", total="n/a")])
    assert len(report.entries) == 1
    assert report.total_owed_usd == Decimal("0")


def test_an_empty_ledger_owes_nothing():
    report = build_report([])
    assert report.total_owed_usd == Decimal("0")
    assert report.owed_by_asset == {}


# -- the compliance/accounting interlock ---------------------------------------


def test_reward_income_is_EXCLUDED_from_realised_pnl():
    """§65.9's first consequence, pinned deliberately.

    `analysis/pnl._classify` currently ignores reward types because they match neither its buy
    nor its sell keywords -- correct, but INCIDENTAL. If someone later broadened those keywords,
    riba would silently enter realised P&L and, through it, the equity base that fixed-fractional
    sizing computes from: riba compounding into position size. This test makes the exclusion
    intentional so that change would fail here.
    """
    # `Repository.get_transactions` yields Decimal qty/price, so the fixture must too.
    trades = [
        {"type": "Buy", "asset": "BTC", "qty": Decimal("1"), "price": Decimal("100"), "ts": 1},
        {"type": "Sell", "asset": "BTC", "qty": Decimal("1"), "price": Decimal("150"), "ts": 2},
    ]
    reward = {
        "type": "Reward Income",
        "asset": "BTC",
        "qty": Decimal("5"),
        "price": Decimal("100"),
        "ts": 3,
    }

    assert realized_pnl(trades) == realized_pnl([*trades, reward])


# -- CLI -----------------------------------------------------------------------


def test_purification_cli_reports_owed_and_flags_unknowns(tmp_path):
    from click.testing import CliRunner

    from keel.cli import cli
    from keel.data.db import connect, migrate
    from keel.data.repository import Repository

    db_path = tmp_path / "t.db"
    conn = connect(str(db_path))
    migrate(conn)
    repo = Repository(conn)
    for tx in (
        {"coinbase_id": "a", "source": "t", "type": "Buy", "asset": "BTC",
         "ts": 1, "qty": Decimal("1"), "price": Decimal("100"), "total": Decimal("100")},
        {"coinbase_id": "b", "source": "t", "type": "Reward Income", "asset": "USDC",
         "ts": 2, "qty": Decimal("0.5"), "price": Decimal("1"), "total": Decimal("0.5")},
        {"coinbase_id": "c", "source": "t", "type": "Mystery", "asset": "ETH",
         "ts": 3, "qty": Decimal("1"), "price": Decimal("9"), "total": Decimal("9")},
    ):
        repo.upsert_transaction(tx)

    result = CliRunner().invoke(cli, ["--db", str(db_path), "purification"])
    assert result.exit_code == 0, result.output
    assert "TOTAL OWED TO CHARITY: $0.50" in result.output
    assert "need review" in result.output
    assert "Mystery" in result.output


def test_purification_cli_on_a_clean_ledger_says_so(tmp_path):
    from click.testing import CliRunner

    from keel.cli import cli
    from keel.data.db import connect, migrate
    from keel.data.repository import Repository

    db_path = tmp_path / "t.db"
    conn = connect(str(db_path))
    migrate(conn)
    Repository(conn).upsert_transaction(
        {"coinbase_id": "a", "source": "t", "type": "Buy", "asset": "BTC",
         "ts": 1, "qty": Decimal("1"), "price": Decimal("100"), "total": Decimal("100")}
    )
    result = CliRunner().invoke(cli, ["--db", str(db_path), "purification"])
    assert result.exit_code == 0, result.output
    assert "no non-compliant credits" in result.output
