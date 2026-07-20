"""Round-trip tests for the trade_outcomes table.

The table is the substrate rails 11 and 16 both depend on, so exactness matters: `pnl_net`'s SIGN
decides win-vs-loss, and a Decimal that drifts through storage would silently misclassify trades.
"""

from __future__ import annotations

from decimal import Decimal

from keel.data import db
from keel.data.repository import Repository


def _repo() -> Repository:
    conn = db.connect(":memory:")
    db.migrate(conn)
    return Repository(conn)


def _outcome(**overrides: object) -> dict:
    base: dict = {
        "product_id": "BTC-USD",
        "rule_name": "turtle_breakout",
        "is_dca": False,
        "opened_at": 1_800_000_000,
        "closed_at": 1_800_086_400,
        "qty": Decimal("0.5"),
        "entry_fill": Decimal("50000"),
        "exit_fill": Decimal("51000"),
        "fees": Decimal("1.25"),
        "pnl_net": Decimal("498.75"),
    }
    base.update(overrides)
    return base


def test_schema_is_at_version_4() -> None:
    conn = db.connect(":memory:")
    db.migrate(conn)
    version = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
    assert version == db.SCHEMA_VERSION == 4


def test_fresh_database_has_no_outcomes() -> None:
    """No backfill by design: fabricated history would poison rail 16's threshold."""
    assert _repo().get_trade_outcomes() == []


def test_insert_then_get_round_trips_exactly() -> None:
    repo = _repo()
    repo.insert_trade_outcome(_outcome())
    rows = repo.get_trade_outcomes()
    assert len(rows) == 1
    assert rows[0]["pnl_net"] == Decimal("498.75")
    assert isinstance(rows[0]["pnl_net"], Decimal)
    assert rows[0]["rule_name"] == "turtle_breakout"
    assert rows[0]["is_dca"] is False


def test_high_precision_decimals_do_not_drift() -> None:
    repo = _repo()
    repo.insert_trade_outcome(_outcome(pnl_net=Decimal("-0.000000001")))
    assert repo.get_trade_outcomes()[0]["pnl_net"] == Decimal("-0.000000001")


def test_a_negative_pnl_survives_its_sign() -> None:
    """The sign is the whole signal — a loss must read back as a loss."""
    repo = _repo()
    repo.insert_trade_outcome(_outcome(pnl_net=Decimal("-12.5")))
    assert repo.get_trade_outcomes()[0]["pnl_net"] < 0


def test_is_dca_round_trips_as_a_bool_not_an_int() -> None:
    """SQLite has no bool; rail 16's exemption reads this, so it must not be 0/1."""
    repo = _repo()
    repo.insert_trade_outcome(_outcome(is_dca=True))
    assert repo.get_trade_outcomes()[0]["is_dca"] is True


def test_get_trade_outcomes_filters_by_since_ts() -> None:
    repo = _repo()
    repo.insert_trade_outcome(_outcome(closed_at=1_000))
    repo.insert_trade_outcome(_outcome(closed_at=2_000))
    assert len(repo.get_trade_outcomes(since_ts=1_500)) == 1
    assert len(repo.get_trade_outcomes(since_ts=None)) == 2


def test_outcomes_come_back_oldest_first() -> None:
    """Streak logic reads them in order; reverse order would invert the streak."""
    repo = _repo()
    repo.insert_trade_outcome(_outcome(closed_at=2_000, pnl_net=Decimal("5")))
    repo.insert_trade_outcome(_outcome(closed_at=1_000, pnl_net=Decimal("-5")))
    assert [r["closed_at"] for r in repo.get_trade_outcomes()] == [1_000, 2_000]
