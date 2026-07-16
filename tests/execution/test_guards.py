"""Tests for halal_cb.execution.guards -- the twelve §14 hard rails.

Each rail gets a focused test: a fully compliant baseline intent (`_intent()` against an empty,
freshly-seeded `repo`) passes every rail; each rail test perturbs exactly the dimension that rail
checks (and, where a rail's inputs are shared with another rail -- e.g. exposure/concentration
both read from open positions -- tunes the config/seed data so only the rail under test trips),
so `result.violations` names precisely the rail expected.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from halal_cb.config import (
    AutoTradeConfig,
    Caps,
    Config,
    MarketDataConfig,
    MoneyMgmtConfig,
)
from halal_cb.data.db import connect, migrate
from halal_cb.data.repository import Repository
from halal_cb.execution.guards import GuardResult, OrderIntent, check
from halal_cb.types import Side

NOW_TS = 1_700_000_000  # 2023-11-14T22:13:20Z -- well inside its UTC day for boundary tests


@pytest.fixture
def repo() -> Repository:
    """A freshly migrated repo, pre-seeded with a fully compliant `agent_state`."""
    conn = connect(":memory:")
    migrate(conn)
    r = Repository(conn)
    r.set_state("kill_switch", False)
    r.set_state("last_feed_ts", NOW_TS)
    return r


def _config(
    *,
    allowlist: tuple[str, ...] = ("BTC", "ETH", "PAXG"),
    max_per_order_usd: Decimal = Decimal("100"),
    max_per_day_usd: Decimal = Decimal("300"),
    max_exposure_usd: Decimal = Decimal("1000"),
    max_per_asset_pct: Decimal = Decimal("0.5"),
    max_total_dd_pct: Decimal = Decimal("0.20"),
    max_weekly_dd_pct: Decimal = Decimal("0.08"),
    interval_sec: int = 900,
) -> Config:
    return Config(
        allowlist=list(allowlist),
        target_weights={},
        risk_pct=Decimal("0.01"),
        caps=Caps(
            max_per_order_usd=max_per_order_usd,
            max_per_day_usd=max_per_day_usd,
            max_exposure_usd=max_exposure_usd,
            max_per_asset_pct=max_per_asset_pct,
        ),
        market_data=MarketDataConfig(granularities=[], history_days=365),
        auto_trade=AutoTradeConfig(interval_sec=interval_sec),
        money_mgmt=MoneyMgmtConfig(
            max_total_dd_pct=max_total_dd_pct, max_weekly_dd_pct=max_weekly_dd_pct
        ),
    )


def _intent(**overrides: Any) -> OrderIntent:
    base: dict[str, Any] = dict(
        product_id="BTC-USD",
        side=Side.BUY,
        qty=Decimal("0.001"),
        entry=Decimal("50000"),
        stop=Decimal("49000"),  # 2% move -- clears the min-move floor
        notional=Decimal("50"),
        is_dca=False,
        rule_kind="pullback_continuation",
    )
    base.update(overrides)
    return OrderIntent(**base)


def _keys(result: GuardResult) -> set[str]:
    return {v.split(":", 1)[0] for v in result.violations}


def _seed_filled_order(
    repo: Repository,
    *,
    product_id: str,
    side: Side,
    qty: Decimal,
    price: Decimal,
    created_at: int,
) -> None:
    repo.insert_order(
        dict(
            mode="live",
            product_id=product_id,
            side=side.value,
            order_type="market",
            qty=qty,
            limit_price=price,
            status="filled",
            fee=Decimal("0"),
            expected_fill=price,
            actual_fill=price,
            raw_response=None,
            confirmation="auto",
            rule_id=None,
            created_at=created_at,
            updated_at=created_at,
        )
    )


# -- compliant baseline -------------------------------------------------------------------------


def test_compliant_intent_passes_all_rails(repo):
    result = check(_intent(), repo, _config(), NOW_TS)

    assert result.ok is True
    assert result.violations == []


# -- rail 1: halal allowlist ---------------------------------------------------------------------


def test_rail1_halal_allowlist_rejects_non_allowlisted_asset(repo):
    intent = _intent(product_id="DOGE-USD")

    result = check(intent, repo, _config(), NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"halal_allowlist"}


# -- rail 2: per-order $ cap ----------------------------------------------------------------------


def test_rail2_per_order_cap_rejects_over_cap(repo):
    intent = _intent(notional=Decimal("150"))

    result = check(intent, repo, _config(), NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"per_order_cap"}


# -- rail 3: per-day $ cap -------------------------------------------------------------------------


def test_rail3_per_day_cap_rejects_when_running_total_exceeds_cap(repo):
    _seed_filled_order(
        repo,
        product_id="BTC-USD",
        side=Side.BUY,
        qty=Decimal("2.6"),
        price=Decimal("100"),  # notional 260, spent earlier today
        created_at=NOW_TS - 50,
    )
    intent = _intent(notional=Decimal("50"))  # 260 + 50 = 310 > 300 day cap

    result = check(intent, repo, _config(), NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"per_day_cap"}


def test_rail3_per_day_cap_ignores_spend_from_a_prior_day(repo):
    _seed_filled_order(
        repo,
        product_id="BTC-USD",
        side=Side.BUY,
        qty=Decimal("2.6"),
        price=Decimal("100"),
        created_at=NOW_TS - 1_000_000,  # a prior UTC day
    )
    intent = _intent(notional=Decimal("50"))

    result = check(intent, repo, _config(), NOW_TS)

    assert result.ok is True
    assert result.violations == []


# -- rail 4: total open-exposure cap ---------------------------------------------------------------


def test_rail4_total_exposure_cap_rejects_over_cap(repo):
    _seed_filled_order(
        repo,
        product_id="ETH-USD",
        side=Side.BUY,
        qty=Decimal("9.6"),
        price=Decimal("100"),  # 960 notional already open, a prior day (no day-cap overlap)
        created_at=NOW_TS - 1_000_000,
    )
    intent = _intent(product_id="BTC-USD", notional=Decimal("45"))  # 960 + 45 = 1005 > 1000
    config = _config(max_exposure_usd=Decimal("1000"), max_per_asset_pct=Decimal("0.6"))

    result = check(intent, repo, config, NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"total_exposure_cap"}


# -- rail 5: correlation-adjusted sizing -----------------------------------------------------------


def test_rail5_correlation_adjusted_sizing_rejects_oversized_correlated_add(repo):
    _seed_filled_order(
        repo,
        product_id="ETH-USD",
        side=Side.BUY,
        qty=Decimal("2"),
        price=Decimal("100"),  # 200 already open in a correlated asset
        created_at=NOW_TS - 1_000_000,
    )
    # correlated cap = max_per_order_usd(100) * 0.5 = 50; 80 exceeds it
    intent = _intent(product_id="BTC-USD", notional=Decimal("80"))

    result = check(intent, repo, _config(), NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"correlation_adjusted_sizing"}


def test_rail5_correlation_adjusted_sizing_exempts_uncorrelated_gold(repo):
    _seed_filled_order(
        repo,
        product_id="ETH-USD",
        side=Side.BUY,
        qty=Decimal("2"),
        price=Decimal("100"),
        created_at=NOW_TS - 1_000_000,
    )
    # PAXG (gold) is not "long crypto beta" -- no correlation scale-down applies to it.
    intent = _intent(product_id="PAXG-USD", notional=Decimal("80"))

    result = check(intent, repo, _config(), NOW_TS)

    assert result.ok is True
    assert result.violations == []


# -- rail 6: per-asset concentration cap -----------------------------------------------------------


def test_rail6_per_asset_concentration_cap_rejects_over_cap(repo):
    # raise the per-order cap so only concentration (not rail 2) trips at notional=150
    config = _config(max_per_order_usd=Decimal("500"), max_per_asset_pct=Decimal("0.1"))
    intent = _intent(notional=Decimal("150"))  # per-asset limit = 0.1 * 1000 = 100 < 150

    result = check(intent, repo, config, NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"per_asset_concentration_cap"}


# -- rail 7: min-move / anti-scalping --------------------------------------------------------------


def test_rail7_min_move_anti_scalping_rejects_tight_stop(repo):
    intent = _intent(entry=Decimal("50000"), stop=Decimal("49990"))  # 0.02% move

    result = check(intent, repo, _config(), NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"min_move_anti_scalping"}


def test_rail7_min_move_anti_scalping_skipped_when_no_stop(repo):
    intent = _intent(stop=None)

    result = check(intent, repo, _config(), NOW_TS)

    assert result.ok is True
    assert result.violations == []


# -- rail 8: no averaging into losers --------------------------------------------------------------


def test_rail8_no_averaging_into_losers_rejects_add_to_underwater_position(repo):
    config = _config(max_exposure_usd=Decimal("1000000"), max_per_asset_pct=Decimal("1"))
    _seed_filled_order(
        repo,
        product_id="BTC-USD",
        side=Side.BUY,
        qty=Decimal("1"),
        price=Decimal("50000"),  # existing average cost basis
        created_at=NOW_TS - 1_000_000,
    )
    # current price 40000 is below the 50000 average cost -- adding here is martingale
    intent = _intent(entry=Decimal("40000"), stop=Decimal("39000"), notional=Decimal("50"))

    result = check(intent, repo, config, NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"no_averaging_into_losers"}


def test_rail8_dca_exempt_from_averaging_into_losers(repo):
    config = _config(max_exposure_usd=Decimal("1000000"), max_per_asset_pct=Decimal("1"))
    _seed_filled_order(
        repo,
        product_id="BTC-USD",
        side=Side.BUY,
        qty=Decimal("1"),
        price=Decimal("50000"),
        created_at=NOW_TS - 1_000_000,
    )
    intent = _intent(
        entry=Decimal("40000"),
        stop=None,
        notional=Decimal("50"),
        is_dca=True,
        rule_kind="dca",
    )

    result = check(intent, repo, config, NOW_TS)

    assert result.ok is True
    assert result.violations == []


# -- rail 9: no stop-loss widening -----------------------------------------------------------------


def test_rail9_no_stop_widening_rejects_wider_stop_than_prior(repo):
    repo.set_state("open_stop:BTC-USD", Decimal("49500"))
    intent = _intent(stop=Decimal("49000"))  # lower than the recorded 49500 -- widening

    result = check(intent, repo, _config(), NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"no_stop_widening"}


def test_rail9_no_stop_widening_allows_tightening_stop(repo):
    repo.set_state("open_stop:BTC-USD", Decimal("49000"))
    intent = _intent(stop=Decimal("49500"))  # ratchets toward profit -- allowed

    result = check(intent, repo, _config(), NOW_TS)

    assert result.ok is True
    assert result.violations == []


# -- rail 10: sell-only-on-rule -------------------------------------------------------------------


def test_rail10_sell_only_on_rule_rejects_sell_without_rule_kind(repo):
    intent = _intent(side=Side.SELL, stop=None, rule_kind="")

    result = check(intent, repo, _config(), NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"sell_only_on_rule"}


def test_rail10_sell_only_on_rule_allows_sell_with_rule_kind(repo):
    intent = _intent(side=Side.SELL, stop=None, rule_kind="target_harvest")

    result = check(intent, repo, _config(), NOW_TS)

    assert result.ok is True
    assert result.violations == []


# -- rail 11: account-drawdown circuit breaker -----------------------------------------------------


def test_rail11_account_drawdown_breaker_total_rejects_new_entries(repo):
    repo.set_state("drawdown_total_pct", Decimal("0.25"))  # >= 0.20 max_total_dd_pct

    result = check(_intent(), repo, _config(), NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"account_dd_breaker_total"}


def test_rail11_account_drawdown_breaker_weekly_rejects_new_entries(repo):
    repo.set_state("drawdown_weekly_pct", Decimal("0.10"))  # >= 0.08 max_weekly_dd_pct

    result = check(_intent(), repo, _config(), NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"account_dd_breaker_weekly"}


def test_rail11_dca_exempt_from_drawdown_breaker(repo):
    repo.set_state("drawdown_total_pct", Decimal("0.9"))
    repo.set_state("drawdown_weekly_pct", Decimal("0.9"))
    intent = _intent(is_dca=True, rule_kind="dca", stop=None)

    result = check(intent, repo, _config(), NOW_TS)

    assert result.ok is True
    assert result.violations == []


# -- rail 12: stale-data / feed-health + kill-switch -----------------------------------------------


def test_rail12_kill_switch_halts_all_orders_including_dca(repo):
    repo.set_state("kill_switch", True)
    rule_intent = _intent()
    dca_intent = _intent(is_dca=True, rule_kind="dca", stop=None)

    for intent in (rule_intent, dca_intent):
        result = check(intent, repo, _config(), NOW_TS)
        assert result.ok is False
        assert _keys(result) == {"kill_switch"}


def test_rail12_stale_data_rejects_stale_feed(repo):
    repo.set_state("last_feed_ts", NOW_TS - 10_000)  # threshold is 900 * 3 = 2700s

    result = check(_intent(), repo, _config(), NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"stale_data"}


def test_rail12_missing_feed_timestamp_treated_as_stale(repo):
    conn = connect(":memory:")
    migrate(conn)
    fresh_repo = Repository(conn)
    fresh_repo.set_state("kill_switch", False)
    # last_feed_ts intentionally never recorded

    result = check(_intent(), fresh_repo, _config(), NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"stale_data"}


# -- collects every violation, never short-circuits ------------------------------------------------


def test_check_collects_multiple_violations_without_short_circuiting(repo):
    repo.set_state("kill_switch", True)
    intent = _intent(product_id="DOGE-USD", notional=Decimal("150"))

    result = check(intent, repo, _config(), NOW_TS)

    assert result.ok is False
    keys = _keys(result)
    assert {"halal_allowlist", "per_order_cap", "kill_switch"} <= keys
    assert len(result.violations) == 3
