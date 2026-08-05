"""Tests for keel.execution.guards -- the twelve §14 hard rails.

Each rail gets a focused test: a fully compliant baseline intent (`_intent()` against an empty,
freshly-seeded `repo`) passes every rail; each rail test perturbs exactly the dimension that rail
checks (and, where a rail's inputs are shared with another rail -- e.g. exposure/concentration
both read from open positions -- tunes the config/seed data so only the rail under test trips),
so `result.violations` names precisely the rail expected.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import pytest
from keel_core.subscription import SubscriptionStatus

from keel.config import (
    DEFAULT_SETTLEMENT_CURRENCIES,
    AutoTradeConfig,
    Caps,
    Config,
    MarketDataConfig,
    MoneyMgmtConfig,
    SubscriptionConfig,
)
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.execution import guards
from keel.execution.guards import LIVE_STATE_RAILS, GuardResult, OrderIntent, check
from keel.types import Side
from tests.conftest import attest_subscription

NOW_TS = 1_700_000_000  # 2023-11-14T22:13:20Z -- well inside its UTC day for boundary tests


_LARGE_ALLOWANCE = Decimal("10000000")


@pytest.fixture
def repo() -> Repository:
    """A freshly migrated repo, pre-seeded with a fully compliant `agent_state`.

    The subscription is seeded with a very large, attested allowance so pre-existing rail tests
    (which don't exercise rail 14) aren't incidentally tripped by it; rail-14-specific tests below
    override it with `_attest(...)` to exercise realistic caps.
    """
    conn = connect(":memory:")
    migrate(conn)
    r = Repository(conn)
    r.set_state("kill_switch", False)
    r.set_state("last_feed_ts", NOW_TS)
    attest_subscription(r, now_ts=NOW_TS, free_volume_usd=_LARGE_ALLOWANCE)
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
    unsubscribed_allowance_usd: Decimal = Decimal("0"),
    pacing: str = "opportunistic",
    max_consecutive_losses: int = 0,
    settlement_currencies: frozenset[str] = DEFAULT_SETTLEMENT_CURRENCIES,
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
            max_total_dd_pct=max_total_dd_pct,
            max_weekly_dd_pct=max_weekly_dd_pct,
            max_consecutive_losses=max_consecutive_losses,
        ),
        subscription=SubscriptionConfig(
            unsubscribed_allowance_usd=unsubscribed_allowance_usd,
            pacing=pacing,
        ),
        settlement_currencies=settlement_currencies,
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
        available_quote=_LARGE_ALLOWANCE,  # comfortably covers every notional used below
        # Rail 17 fails closed on None, so every test that is not ABOUT rail 17 supplies a
        # fresh attestation -- same reason this helper supplies `available_quote` for rail 13.
        withdrawals_enabled=True,
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


# -- P4 Task 8: Rail 11 enforced in PAPER (offline=True) -- the headline acceptance test ---------


def test_paper_drawdown_halt_vetoes_buys(repo):
    """A paper account drawn down past `max_total_dd_pct` gets BUYs vetoed by Rail 11.

    Same shape as `test_rail11_account_drawdown_breaker_total_rejects_new_entries` above, but
    explicit about the two things that distinguish a PAPER check from a live one: `offline=True`
    (paper never has a live broker balance to read) and `equity_state_mode="paper"` (the stamp
    Task 5/6 write so the shared HWM/drawdown keys are known to belong to the synthetic account)
    -- proving Rail 11 still fires on that path, not just on the live one.
    """
    repo.set_state("equity_state_mode", "paper")
    repo.set_state("drawdown_total_pct", Decimal("0.25"))  # 25% > 20% ceiling
    repo.set_state("kill_switch", False)
    repo.set_state("last_feed_ts", NOW_TS)
    intent = _intent()

    verdict = check(intent, repo, _config(), NOW_TS, offline=True)

    assert not verdict.ok
    assert any("account_dd_breaker_total" in v for v in verdict.violations)


def test_paper_weekly_drawdown_halt_vetoes_buys(repo):
    """The weekly twin of the test above: `drawdown_weekly_pct` past `max_weekly_dd_pct` vetoes
    a paper BUY too, not just the total-drawdown scalar."""
    repo.set_state("equity_state_mode", "paper")
    repo.set_state("drawdown_weekly_pct", Decimal("0.10"))  # 10% > 8% ceiling
    repo.set_state("kill_switch", False)
    repo.set_state("last_feed_ts", NOW_TS)
    intent = _intent()

    verdict = check(intent, repo, _config(), NOW_TS, offline=True)

    assert not verdict.ok
    assert any("account_dd_breaker_weekly" in v for v in verdict.violations)


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
    # attested so only rail 12 (not rail 14's unattested-fallback) trips
    _attest(fresh_repo, free_volume_usd=_LARGE_ALLOWANCE)

    result = check(_intent(), fresh_repo, _config(), NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"stale_data"}


# -- rail 13: USDC-funding (never draw from bank/ACH) ----------------------------------------------


def test_rail13_usdc_funding_passes_when_balance_covers_notional(repo):
    intent = _intent(available_quote=Decimal("100"), notional=Decimal("50"))

    result = check(intent, repo, _config(), NOW_TS)

    assert result.ok is True
    assert result.violations == []


def test_rail13_usdc_funding_rejects_when_balance_is_short_of_notional(repo):
    intent = _intent(available_quote=Decimal("30"), notional=Decimal("50"))

    result = check(intent, repo, _config(), NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"usdc_funding"}
    assert any("20" in v for v in result.violations)  # shortfall = 50 - 30


def test_rail13_usdc_funding_rejects_zero_balance(repo):
    intent = _intent(available_quote=Decimal("0"), notional=Decimal("50"))

    result = check(intent, repo, _config(), NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"usdc_funding"}


def test_rail13_usdc_funding_fails_closed_when_balance_is_unknown(repo):
    """`available_quote=None` -- broker error or missing quote account -- vetoes the BUY. Silence
    is not consent to draw funds, same fail-closed posture as rail 12's kill-switch."""
    intent = _intent(available_quote=None, notional=Decimal("50"))

    result = check(intent, repo, _config(), NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"usdc_funding"}


def test_rail13_usdc_funding_exempts_sell_even_with_no_balance(repo):
    intent = _intent(
        side=Side.SELL, stop=None, rule_kind="target_harvest", available_quote=None
    )

    result = check(intent, repo, _config(), NOW_TS)

    assert result.ok is True
    assert result.violations == []


# -- rail 14: monthly subscription-allowance -------------------------------------------------------


def _roomy_config() -> Config:
    """A config with every other cap raised out of the way, so only rail 14 can trip."""
    return _config(
        max_per_order_usd=Decimal("100000"),
        max_per_day_usd=Decimal("100000"),
        max_exposure_usd=Decimal("100000000"),
        max_per_asset_pct=Decimal("1"),
    )


def test_rail14_monthly_allowance_passes_under_cap(repo):
    _attest(repo, free_volume_usd=Decimal("500"))
    intent = _intent(notional=Decimal("100"))

    result = check(intent, repo, _roomy_config(), NOW_TS)

    assert result.ok is True
    assert result.violations == []


def test_rail14_monthly_allowance_rejects_over_cap(repo):
    _attest(repo, free_volume_usd=Decimal("500"))
    _seed_filled_order(
        repo,
        product_id="BTC-USD",
        side=Side.BUY,
        qty=Decimal("4.5"),
        price=Decimal("100"),  # notional 450, already spent this month
        created_at=NOW_TS - 50,
    )
    intent = _intent(notional=Decimal("100"))  # 450 + 100 = 550 > 500

    result = check(intent, repo, _roomy_config(), NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"monthly_subscription_allowance"}


def test_rail14_monthly_allowance_ignores_spend_from_a_prior_month(repo):
    _attest(repo, free_volume_usd=Decimal("500"))
    _seed_filled_order(
        repo,
        product_id="BTC-USD",
        side=Side.BUY,
        qty=Decimal("10"),
        price=Decimal("100"),  # notional 1000, but in October -- a prior calendar month
        created_at=NOW_TS - 20 * 86400,
    )
    intent = _intent(notional=Decimal("100"))

    result = check(intent, repo, _roomy_config(), NOW_TS)

    assert result.ok is True
    assert result.violations == []


def test_rail14_updated_subscription_is_read_live_at_the_next_check(repo):
    """The allowance is read fresh from `repo.get_broker_subscription()` on every call -- no
    snapshot, no restart, no config edit needed for a re-attestation to take effect."""
    _attest(repo, free_volume_usd=Decimal("500"))
    intent = _intent(notional=Decimal("150"))
    config = _roomy_config()

    before = check(intent, repo, config, NOW_TS)
    assert before.ok is True

    _attest(repo, free_volume_usd=Decimal("100"))
    after_lowered = check(intent, repo, config, NOW_TS)
    assert after_lowered.ok is False
    assert _keys(after_lowered) == {"monthly_subscription_allowance"}

    _attest(repo, free_volume_usd=Decimal("1000"))
    after_raised = check(intent, repo, config, NOW_TS)
    assert after_raised.ok is True


def test_rail14_even_daily_pacing_vetoes_a_burst_within_the_monthly_cap(repo):
    # NOW_TS (2023-11-14, a Tuesday) is business day 10 of 22 in November -> paced cap =
    # 220 / 22 * 10 = 100, tighter than the 220 flat monthly cap.
    _attest(repo, free_volume_usd=Decimal("220"), pacing="even_daily")
    intent = _intent(notional=Decimal("150"))  # under the 220 monthly cap, over the paced 100

    result = check(intent, repo, _roomy_config(), NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"monthly_subscription_allowance"}


def test_rail14_even_daily_pacing_allows_spend_within_the_paced_cap(repo):
    _attest(repo, free_volume_usd=Decimal("220"), pacing="even_daily")
    intent = _intent(notional=Decimal("100"))  # exactly at the paced cap (220/22*10 = 100)

    result = check(intent, repo, _roomy_config(), NOW_TS)

    assert result.ok is True
    assert result.violations == []


def test_rail14_opportunistic_pacing_ignores_the_business_day_pace(repo):
    # Same numbers as the pacing-burst test above, but pacing="opportunistic" -- only the flat
    # monthly cap applies, so the same burst that trips even_daily passes here.
    _attest(repo, free_volume_usd=Decimal("220"), pacing="opportunistic")
    intent = _intent(notional=Decimal("150"))

    result = check(intent, repo, _roomy_config(), NOW_TS)

    assert result.ok is True
    assert result.violations == []


def test_rail14_dca_is_bound_by_the_monthly_allowance(repo):
    """Unlike rails 8/11, DCA is NOT exempt from the subscription allowance -- DCA orders are
    exactly the recurring "subscription" spend the rail exists to cap."""
    _attest(repo, free_volume_usd=Decimal("500"))
    intent = _intent(
        notional=Decimal("600"), is_dca=True, rule_kind="dca", stop=None
    )  # 600 > 500

    result = check(intent, repo, _roomy_config(), NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"monthly_subscription_allowance"}


# -- rail 14: derives its cap from the attested subscription record --------------------------------


def _attest(
    repo: Repository,
    *,
    free_volume_usd: Decimal | None,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    pacing: str = "opportunistic",
    attested_at: int = NOW_TS,
    attest_due_ts: int | None = None,
) -> None:
    """Attest a coinbase subscription -- the setup every rail-14 test now needs."""
    attest_subscription(
        repo,
        now_ts=attested_at,
        free_volume_usd=free_volume_usd,
        status=status,
        pacing=pacing,
        attest_due_ts=attest_due_ts,
    )


def _unattested_repo() -> Repository:
    """A compliant repo with NO subscription -- a fresh install, before any attestation."""
    conn = connect(":memory:")
    migrate(conn)
    r = Repository(conn)
    r.set_state("kill_switch", False)
    r.set_state("last_feed_ts", NOW_TS)
    return r


def test_rail14_refuses_a_buy_when_nothing_has_been_attested() -> None:
    """keel ships inert: no record means no live BUY."""
    result = guards.check(_intent(), _unattested_repo(), _roomy_config(), NOW_TS)
    assert not result.ok
    assert "subscription_unattested" in _keys(result)


def test_rail14_inert_message_tells_the_user_to_attest() -> None:
    """A bare "0 exceeds cap 0" is arithmetically true and practically useless."""
    result = guards.check(_intent(), _unattested_repo(), _roomy_config(), NOW_TS)
    violation = next(v for v in result.violations if v.startswith("subscription_unattested"))
    assert "attest" in violation
    assert "coinbase" in violation


def test_rail14_allows_a_buy_inside_an_attested_allowance(repo: Repository) -> None:
    _attest(repo, free_volume_usd=Decimal("10000"))
    result = guards.check(_intent(notional=Decimal("50")), repo, _roomy_config(), NOW_TS)
    assert result.ok


def test_rail14_still_caps_at_the_attested_allowance(repo: Repository) -> None:
    _attest(repo, free_volume_usd=Decimal("40"))
    result = guards.check(_intent(notional=Decimal("50")), repo, _roomy_config(), NOW_TS)
    assert "monthly_subscription_allowance" in _keys(result)


def test_rail14_passes_unconditionally_for_an_unlimited_tier(repo: Repository) -> None:
    """Premium has no cap, and pacing a cap that does not exist is meaningless."""
    _attest(repo, free_volume_usd=None, pacing="even_daily")
    result = guards.check(_intent(notional=Decimal("50")), repo, _roomy_config(), NOW_TS)
    assert "monthly_subscription_allowance" not in _keys(result)
    assert "subscription_unattested" not in _keys(result)


@pytest.mark.parametrize("status", [SubscriptionStatus.SUSPECT, SubscriptionStatus.LAPSED])
@pytest.mark.parametrize(
    "free_volume_usd",
    [Decimal("10000"), None],
    ids=["finite_allowance", "unlimited_allowance"],
)
def test_rail14_fails_closed_on_a_degraded_subscription(
    repo: Repository, status: SubscriptionStatus, free_volume_usd: Decimal | None
) -> None:
    """The `None` case is the one that would be an actual real-money hole.

    `free_volume_usd is None` means unlimited, and rail 14 skips the cap entirely when the
    allowance is `None` -- so a degraded record whose stored allowance is unlimited must NOT
    reach that branch. The policy closes it in `BrokerSubscription.allowance_usd`, but until
    now that was only pinned upstream in `tests/test_subscription_record.py`; nothing pinned
    the composition at the rail, which is where the spending actually happens.
    """
    _attest(repo, free_volume_usd=free_volume_usd, status=status)
    result = guards.check(_intent(notional=Decimal("50")), repo, _roomy_config(), NOW_TS)
    assert "subscription_unattested" in _keys(result)


def test_rail14_fails_closed_on_an_overdue_attestation(repo: Repository) -> None:
    _attest(
        repo,
        free_volume_usd=Decimal("10000"),
        attested_at=NOW_TS - 40_000_000,
        attest_due_ts=NOW_TS - 1,
    )
    result = guards.check(_intent(notional=Decimal("50")), repo, _roomy_config(), NOW_TS)
    violation = next(v for v in result.violations if v.startswith("subscription_unattested"))
    assert "overdue" in violation


def test_rail14_reports_lapsed_over_overdue_when_a_record_is_both(repo: Repository) -> None:
    """LAPSED is a definite statement the subscription ended; overdue is merely an unrefreshed
    assertion -- when a record is both, the message must name the more serious one."""
    _attest(
        repo,
        free_volume_usd=Decimal("10000"),
        status=SubscriptionStatus.LAPSED,
        attested_at=NOW_TS - 40_000_000,
        attest_due_ts=NOW_TS - 1,
    )
    result = guards.check(_intent(notional=Decimal("50")), repo, _roomy_config(), NOW_TS)
    violation = next(v for v in result.violations if v.startswith("subscription_unattested"))
    assert "lapsed" in violation


def test_rail14_honours_a_raised_unsubscribed_allowance() -> None:
    """A user content to pay fees may raise it -- deliberately, not by accident."""
    config = _config(
        max_per_order_usd=Decimal("10000"),
        max_per_day_usd=Decimal("10000"),
        max_exposure_usd=Decimal("100000"),
        unsubscribed_allowance_usd=Decimal("200"),
    )
    result = guards.check(_intent(notional=Decimal("50")), _unattested_repo(), config, NOW_TS)
    assert "subscription_unattested" not in _keys(result)


def test_rail14_raised_unsubscribed_allowance_still_binds() -> None:
    """The raised allowance is a ceiling, not an escape hatch -- it must still veto once
    exceeded. Otherwise a refactor that made the unattested branch skip the cap comparison
    entirely would pass every existing test."""
    config = _config(
        max_per_order_usd=Decimal("10000"),
        max_per_day_usd=Decimal("10000"),
        max_exposure_usd=Decimal("100000"),
        unsubscribed_allowance_usd=Decimal("200"),
    )
    result = guards.check(_intent(notional=Decimal("250")), _unattested_repo(), config, NOW_TS)
    assert "subscription_unattested" in _keys(result)


def test_rail14_unattested_uses_configured_pacing_not_a_hardcoded_default() -> None:
    """No record means no record-level pacing to read -- the configured pacing is the best
    available statement of intent, so a raised unsubscribed_allowance_usd is still paced when
    the user configured pacing="even_daily", not silently given a flat, unpaced cap."""
    config = _config(
        max_per_order_usd=Decimal("10000"),
        max_per_day_usd=Decimal("10000"),
        max_exposure_usd=Decimal("100000"),
        unsubscribed_allowance_usd=Decimal("220"),
        pacing="even_daily",
    )
    # Same numbers as the attested even_daily pacing test: NOW_TS is business day 10 of 22 in
    # November -> paced cap = 220 / 22 * 10 = 100. 150 is inside the flat 220 cap but outside it.
    result = guards.check(_intent(notional=Decimal("150")), _unattested_repo(), config, NOW_TS)
    assert not result.ok
    violation = next(v for v in result.violations if v.startswith("subscription_unattested"))
    assert "even_daily pacing" in violation


def test_rail14_unattested_opportunistic_pacing_ignores_the_business_day_pace() -> None:
    """Negative control for the test above, mirroring the attested pair at
    `test_rail14_opportunistic_pacing_ignores_the_business_day_pace`.

    Same repo, same allowance, same notional -- only `pacing` differs. Without this, nothing
    pins that the veto above comes from the *pacing* rather than from the flat 220 cap or from
    being unattested at all, and the pair would still pass if the unattested branch ignored
    pacing entirely."""
    config = _config(
        max_per_order_usd=Decimal("10000"),
        max_per_day_usd=Decimal("10000"),
        max_exposure_usd=Decimal("100000"),
        unsubscribed_allowance_usd=Decimal("220"),
        pacing="opportunistic",
    )
    result = guards.check(_intent(notional=Decimal("150")), _unattested_repo(), config, NOW_TS)
    assert result.ok is True
    assert result.violations == []


def test_rail14_reads_pacing_from_the_record_not_config(repo: Repository) -> None:
    """even_daily paces the attested allowance across elapsed business days."""
    _attest(repo, free_volume_usd=Decimal("10000"), pacing="even_daily")
    result = guards.check(_intent(notional=Decimal("9000")), repo, _roomy_config(), NOW_TS)
    violation = next(
        v for v in result.violations if v.startswith("monthly_subscription_allowance")
    )
    assert "even_daily pacing" in violation


def test_rail14_does_not_gate_sells() -> None:
    """SELL produces quote currency; the rail exists to cap spend, so it must not fire."""
    result = guards.check(
        _intent(side=Side.SELL), _unattested_repo(), _roomy_config(), NOW_TS
    )
    assert "subscription_unattested" not in _keys(result)
    assert "monthly_subscription_allowance" not in _keys(result)


# -- collects every violation, never short-circuits ------------------------------------------------


def test_check_collects_multiple_violations_without_short_circuiting(repo):
    repo.set_state("kill_switch", True)
    intent = _intent(product_id="DOGE-USD", notional=Decimal("150"))

    result = check(intent, repo, _config(), NOW_TS)

    assert result.ok is False
    keys = _keys(result)
    assert {"halal_allowlist", "per_order_cap", "kill_switch"} <= keys
    assert len(result.violations) == 3


# -- rail 16: consecutive-loss circuit breaker -------------------------------------------------


def test_rail16_vetoes_a_buy_while_the_streak_halt_is_active(repo: Repository) -> None:
    repo.set_state("streak_halt_until", NOW_TS + 3600)
    config = _config(max_consecutive_losses=3)

    result = check(_intent(), repo, config, NOW_TS)

    assert result.ok is False
    assert _keys(result) == {"consecutive_loss_breaker"}


def test_rail16_does_not_veto_once_the_halt_has_expired(repo: Repository) -> None:
    """The negative control: same repo, same config, halt one second in the past."""
    repo.set_state("streak_halt_until", NOW_TS - 1)
    config = _config(max_consecutive_losses=3)

    result = check(_intent(), repo, config, NOW_TS)

    assert result.ok is True
    assert result.violations == []


def test_rail16_boundary_halt_expires_exactly_at_now(repo: Repository) -> None:
    """`now_ts < halt_until` -- due-at is the moment it expires, matching rail 14's convention."""
    repo.set_state("streak_halt_until", NOW_TS)
    result = check(_intent(), repo, _config(max_consecutive_losses=3), NOW_TS)
    assert result.ok is True


def test_rail16_never_vetoes_a_sell(repo: Repository) -> None:
    """A breaker that blocked EXITS would trap capital in a losing position, inverting its
    own purpose. Entries only."""
    repo.set_state("streak_halt_until", NOW_TS + 3600)
    result = check(_intent(side=Side.SELL), repo, _config(max_consecutive_losses=3), NOW_TS)
    assert "consecutive_loss_breaker" not in _keys(result)


def test_rail16_never_vetoes_dca(repo: Repository) -> None:
    repo.set_state("streak_halt_until", NOW_TS + 3600)
    result = check(_intent(is_dca=True), repo, _config(max_consecutive_losses=3), NOW_TS)
    assert "consecutive_loss_breaker" not in _keys(result)


def test_rail16_is_inert_when_no_halt_was_ever_set(repo: Repository) -> None:
    """The shipped default: nothing set, nothing vetoed."""
    result = check(_intent(), repo, _config(), NOW_TS)
    assert "consecutive_loss_breaker" not in _keys(result)


def test_rail16_violation_message_names_the_cause_and_the_override(repo: Repository) -> None:
    """A bare veto is arithmetically true and operationally useless (the rail-14 lesson)."""
    repo.set_state("streak_halt_until", NOW_TS + 3600)
    result = check(_intent(), repo, _config(max_consecutive_losses=3), NOW_TS)
    violation = next(v for v in result.violations if v.startswith("consecutive_loss_breaker"))
    assert "consecutive" in violation
    assert "Exits" in violation
    assert "resume-entries" in violation



# -- rail 17: withdrawal capability (§65.4 qabd) --------------------------------


def test_rail17_vetoes_a_buy_when_withdrawals_are_suspended(repo: Repository) -> None:
    result = check(_intent(withdrawals_enabled=False), repo, _config(), NOW_TS)
    assert "withdrawal_capability" in _keys(result)


def test_rail17_fails_CLOSED_on_unknown(repo: Repository) -> None:
    """Silence is not evidence of possession -- same posture as rails 12/13."""
    result = check(_intent(withdrawals_enabled=None), repo, _config(), NOW_TS)
    assert "withdrawal_capability" in _keys(result)


def test_rail17_passes_when_withdrawals_are_attested_enabled(repo: Repository) -> None:
    result = check(_intent(withdrawals_enabled=True), repo, _config(), NOW_TS)
    assert "withdrawal_capability" not in _keys(result)


def test_rail17_is_ENTRIES_ONLY_sells_are_never_blocked(repo: Repository) -> None:
    """Existing holdings are already ours; forcing a sale to 'fix' a freeze is strictly worse."""
    for state in (None, False):
        intent = _intent(side=Side.SELL, withdrawals_enabled=state)
        result = check(intent, repo, _config(), NOW_TS)
        assert "withdrawal_capability" not in _keys(result), state


# -- rail 18: settlement currency (instrument admission, every mode, both sides) ----------------
#
# These exist because of `docs/experiments/2026-08-05-coinbase-asset-class-feasibility.md` (R1),
# which established by execution that a SELL of `ADA-28AUG26-CDE` -- a Coinbase futures contract
# -- passed EVERY rail on the real live config. `_asset` reduces it to `ADA`, which is
# allowlisted, so rail 1 waves it through; the only rail that stopped the BUY (rail 13) is
# BUY-only and skipped in paper. Rail 18 is the class gate that was missing.

#: The futures contract from the study. `_asset` -> "ADA" (allowlisted), `quote_currency_of` ->
#: "CDE", which is a Coinbase venue suffix, not a settlement leg.
FUTURES_PRODUCT_ID = "ADA-28AUG26-CDE"
#: A Coinbase EQUITY product id: an opaque 64-char hash with no separator at all, so
#: `quote_currency_of` returns None and the rail must fail CLOSED rather than pass it.
EQUITY_PRODUCT_ID = "ac568fb9e6c5a67da94f065a49fb7b0c59b7b258cfdf0a3b1560849071c3b05e"

#: The live deployment's allowlist verbatim (`~/keel/config.live-sandbox.yaml`) -- the point of
#: the regression tests is that this allowlist does NOT stop the contract, and rail 18 does.
LIVE_ALLOWLIST = ("BTC", "ETH", "PAXG", "ADA", "XLM")


def test_rail18_a_SELL_of_a_futures_contract_on_an_allowlisted_asset_is_vetoed(
    repo: Repository,
) -> None:
    """The exact hole the feasibility study found: SELL `ADA-28AUG26-CDE`, live config, no veto.

    Rail 1 is not the defence here and never was -- assert that too, so a future reader cannot
    mistake this for a duplicate allowlist test.
    """
    intent = _intent(product_id=FUTURES_PRODUCT_ID, side=Side.SELL)
    result = check(intent, repo, _config(allowlist=LIVE_ALLOWLIST), NOW_TS)

    assert "settlement_currency" in _keys(result)
    assert "halal_allowlist" not in _keys(result), "rail 1 passes the contract -- that is the hole"
    assert result.ok is False


def test_rail18_a_futures_contract_is_vetoed_offline_too(repo: Repository) -> None:
    """Paper/offline is where the compensating rail (13) is skipped, so rail 18 must NOT be one
    of `LIVE_STATE_RAILS`: it needs no broker and no live account state."""
    intent = _intent(
        product_id=FUTURES_PRODUCT_ID,
        side=Side.SELL,
        available_quote=None,
        withdrawals_enabled=None,
    )
    result = check(intent, repo, _config(allowlist=LIVE_ALLOWLIST), NOW_TS, offline=True)

    assert "settlement_currency" in _keys(result)
    assert "settlement_currency" not in result.skipped_rails
    assert "settlement_currency" not in LIVE_STATE_RAILS


def test_rail18_a_futures_contract_is_vetoed_on_a_BUY(repo: Repository) -> None:
    result = check(
        _intent(product_id=FUTURES_PRODUCT_ID), repo, _config(allowlist=LIVE_ALLOWLIST), NOW_TS
    )
    assert "settlement_currency" in _keys(result)


def test_rail18_an_equity_hash_product_id_fails_CLOSED(repo: Repository) -> None:
    """`quote_currency_of` returns None for a 64-hex equity id. Unknown is not permission."""
    for side in (Side.BUY, Side.SELL):
        for offline in (False, True):
            intent = _intent(product_id=EQUITY_PRODUCT_ID, side=side)
            result = check(intent, repo, _config(), NOW_TS, offline=offline)
            assert "settlement_currency" in _keys(result), (side, offline)


def test_rail18_never_raises_on_a_malformed_product_id(repo: Repository) -> None:
    """A veto, never an exception: the rail machinery also runs over historical filled orders
    (`_open_exposure_by_asset`), where one bad audit row must not crash the agent cycle."""
    for product_id in ("", "-", "BTC-", "-USD", "   ", "BTCUSD"):
        result = check(_intent(product_id=product_id), repo, _config(), NOW_TS)
        assert "settlement_currency" in _keys(result), product_id


def test_rail18_passes_ordinary_usd_and_usdc_spot(repo: Repository) -> None:
    for product_id in ("ADA-USD", "BTC-USDC"):
        result = check(
            _intent(product_id=product_id), repo, _config(allowlist=LIVE_ALLOWLIST), NOW_TS
        )
        assert "settlement_currency" not in _keys(result), product_id


def test_rail18_passes_a_lowercase_settlement_leg(repo: Repository) -> None:
    """`quote_currency_of` uppercases, and the configured set is uppercased at parse, so the
    comparison is case-insensitive by construction rather than by luck."""
    result = check(_intent(product_id="BTC-usdc"), repo, _config(), NOW_TS)
    assert "settlement_currency" not in _keys(result)


def test_rail18_reads_the_allowed_set_from_config_not_a_hardcode(repo: Repository) -> None:
    """The configured set is the operator's escape hatch -- widening it admits `-EUR` spot, and
    narrowing it below the default takes `-USDC` away. Neither is hardcoded in guards."""
    config = _config(settlement_currencies=frozenset({"EUR"}))

    admitted = check(_intent(product_id="BTC-EUR"), repo, config, NOW_TS)
    rejected = check(_intent(product_id="BTC-USD"), repo, config, NOW_TS)

    assert "settlement_currency" not in _keys(admitted)
    assert "settlement_currency" in _keys(rejected)


def test_rail18_default_rejects_non_usd_usdc_spot(repo: Repository) -> None:
    """A DELIBERATE, accepted behaviour change: ~120 non-USD/USDC spot pairs Coinbase lists are
    now rejected by default. Nothing in the live deployment trades one."""
    for product_id in ("BTC-EUR", "ETH-GBP", "BTC-USDT"):
        result = check(_intent(product_id=product_id), repo, _config(), NOW_TS)
        assert "settlement_currency" in _keys(result), product_id


def test_rail18_violation_names_the_product_the_currency_and_the_allowed_set(
    repo: Repository,
) -> None:
    """An operator must be able to act on the message without reading this module."""
    result = check(
        _intent(product_id=FUTURES_PRODUCT_ID), repo, _config(allowlist=LIVE_ALLOWLIST), NOW_TS
    )
    violation = next(v for v in result.violations if v.startswith("settlement_currency"))
    assert FUTURES_PRODUCT_ID in violation
    assert "CDE" in violation
    assert "USD" in violation and "USDC" in violation


# -- rail 19: spot instrument shape (instrument admission, every mode, both sides) --------------
#
# Rail 18 closed the CLASS hole the study found by execution. Its residual -- R2 in
# `docs/experiments/2026-08-05-coinbase-asset-class-feasibility.md` -- is that it checks the
# settlement LEG, not the instrument SHAPE. `quote_currency_of("BTC-PERP-USD")` returns `"USD"`,
# which is a configured settlement currency, and `_asset` returns the allowlisted `"BTC"`, so a
# derivative-shaped id with a legitimate final segment passes BOTH shipped defences. Rail 19 is
# the shape gate that was missing.

#: The residual, as one id: derivative-shaped, USD-settled, allowlisted base. Coinbase does not
#: list this product today -- that is the point. Rail 18 is a check on the settlement leg and
#: cannot see the middle segment, so a venue that ever listed one would find keel's rails open.
DERIVATIVE_SHAPED_USD_ID = "BTC-PERP-USD"


def test_rail19_a_usd_settled_derivative_shaped_id_is_vetoed(repo: Repository) -> None:
    """The R2 residual, stated as a test: SELL `BTC-PERP-USD` on the live allowlist.

    Both shipped defences pass it -- rail 1 because `_asset` reduces it to the allowlisted
    `BTC`, rail 18 because its settlement leg really is `USD`. Assert BOTH of those, so a future
    reader cannot mistake this for a duplicate of either, and only rail 19 stops it.
    """
    intent = _intent(product_id=DERIVATIVE_SHAPED_USD_ID, side=Side.SELL)
    result = check(intent, repo, _config(allowlist=LIVE_ALLOWLIST), NOW_TS)

    assert "spot_instrument" in _keys(result)
    assert "settlement_currency" not in _keys(result), (
        "rail 18 passes it -- its settlement leg is genuinely USD; that is the residual"
    )
    assert "halal_allowlist" not in _keys(result), "rail 1 passes it too -- `_asset` sees BTC"
    assert result.ok is False


def test_rail19_is_vetoed_offline_too_and_is_never_skipped(repo: Repository) -> None:
    """Like rail 18, this needs no broker and no live account state, so paper cannot skip it.
    A rehearsal that admitted a derivative would prove a track record live trading would veto."""
    intent = _intent(
        product_id=DERIVATIVE_SHAPED_USD_ID,
        side=Side.SELL,
        available_quote=None,
        withdrawals_enabled=None,
    )
    result = check(intent, repo, _config(allowlist=LIVE_ALLOWLIST), NOW_TS, offline=True)

    assert "spot_instrument" in _keys(result)
    assert "spot_instrument" not in result.skipped_rails
    assert "spot_instrument" not in LIVE_STATE_RAILS


def test_rail19_vetoes_both_sides_in_every_mode(repo: Repository) -> None:
    for side in (Side.BUY, Side.SELL):
        for offline in (False, True):
            result = check(
                _intent(product_id=DERIVATIVE_SHAPED_USD_ID, side=side),
                repo,
                _config(allowlist=LIVE_ALLOWLIST),
                NOW_TS,
                offline=offline,
            )
            assert "spot_instrument" in _keys(result), (side, offline)


def test_rail19_vetoes_DCA_too(repo: Repository) -> None:
    """DCA is exempt from rails 8 and 11, never from instrument admission (§12.6)."""
    result = check(
        _intent(product_id=DERIVATIVE_SHAPED_USD_ID, is_dca=True, rule_kind="dca"),
        repo,
        _config(allowlist=LIVE_ALLOWLIST),
        NOW_TS,
    )
    assert "spot_instrument" in _keys(result)


def test_rail19_vetoes_a_futures_contract_and_an_equity_hash(repo: Repository) -> None:
    """The two classes rail 18 already stops are stopped here too -- belt and braces, and the
    reason rail 19 can be read on its own without tracing what rail 18 happens to catch."""
    for product_id in (FUTURES_PRODUCT_ID, EQUITY_PRODUCT_ID):
        result = check(
            _intent(product_id=product_id), repo, _config(allowlist=LIVE_ALLOWLIST), NOW_TS
        )
        assert "spot_instrument" in _keys(result), product_id


def test_rail19_never_raises_on_a_malformed_product_id(repo: Repository) -> None:
    """A veto, never an exception -- same contract as rail 18, for the same reason: the rail
    machinery also walks historical filled orders, where one bad row must not crash the cycle."""
    for product_id in ("", "-", "BTC-", "-USD", "BTC--USD", "btc-usd", "   ", "BTCUSD"):
        result = check(_intent(product_id=product_id), repo, _config(), NOW_TS)
        assert "spot_instrument" in _keys(result), product_id


def test_rail19_passes_every_live_deployment_product(repo: Repository) -> None:
    """The six rules in the live DB verbatim (five turtle + the BTC DCA rule). Rail 19 must be
    invisible to the deployment as it stands -- blast radius nil, exactly as rail 18's was."""
    for product_id in ("BTC-USD", "ETH-USD", "PAXG-USD", "ADA-USD", "XLM-USD"):
        result = check(
            _intent(product_id=product_id), repo, _config(allowlist=LIVE_ALLOWLIST), NOW_TS
        )
        assert "spot_instrument" not in _keys(result), product_id

    dca = check(
        _intent(product_id="BTC-USD", is_dca=True, rule_kind="dca"),
        repo,
        _config(allowlist=LIVE_ALLOWLIST),
        NOW_TS,
    )
    assert "spot_instrument" not in _keys(dca)


def test_rail19_passes_a_well_formed_spot_pair_rail_18_rejects(repo: Repository) -> None:
    """Shape and settlement are separate questions and must stay separately reported: `BTC-EUR`
    is a perfectly well-formed spot pair, vetoed only by the settlement set."""
    result = check(_intent(product_id="BTC-EUR"), repo, _config(allowlist=LIVE_ALLOWLIST), NOW_TS)

    assert "spot_instrument" not in _keys(result)
    assert "settlement_currency" in _keys(result)


def test_rail19_violation_names_the_product_and_says_what_shape_is_required(
    repo: Repository,
) -> None:
    """An operator must be able to act on the message without reading this module."""
    result = check(
        _intent(product_id=DERIVATIVE_SHAPED_USD_ID),
        repo,
        _config(allowlist=LIVE_ALLOWLIST),
        NOW_TS,
    )
    violation = next(v for v in result.violations if v.startswith("spot_instrument"))
    assert DERIVATIVE_SHAPED_USD_ID in violation
    assert "BASE-QUOTE" in violation


# -- `_asset` and the history walk are total ---------------------------------------------------


def test__asset_is_total(repo: Repository) -> None:
    """`_asset` runs over every historical filled order, so it must never raise on anything the
    audit log can hold. `_asset(None)` used to raise `AttributeError`."""
    for weird in (
        None,
        "",
        "-",
        "BTC-",
        "-USD",
        "BTC--USD",
        "btc-usd",
        "   ",
        EQUITY_PRODUCT_ID,
        FUTURES_PRODUCT_ID,
        42,
        3.5,
        b"BTC-USD",
        ["BTC-USD"],
        {"BTC": "USD"},
    ):
        guards._asset(weird)  # must not raise


def test__asset_still_returns_exactly_what_it_returned_before(repo: Repository) -> None:
    """Totality is the ONLY behaviour change. `_asset` stays the loose parse on purpose.

    Tightening it to `parse_spot_product_id` would silently change rail 1's verdict on a futures
    id -- destroying the "rail 1 passes the contract, that is the hole" assertion above -- and
    would split a derivative's exposure out of its root's bucket, under-stating the figure rails
    4/5/6 cap. Rail 19 is where an unparseable id is refused; this is only a grouping key.
    """
    assert guards._asset("BTC-USD") == "BTC"
    assert guards._asset(FUTURES_PRODUCT_ID) == "ADA"
    assert guards._asset(DERIVATIVE_SHAPED_USD_ID) == "BTC"
    assert guards._asset(EQUITY_PRODUCT_ID) == EQUITY_PRODUCT_ID  # no separator: the whole hash
    assert guards._asset(None) == "None"  # a key, not an AttributeError


def test_open_exposure_walk_survives_a_malformed_history_row(
    repo: Repository, caplog: pytest.LogCaptureFixture
) -> None:
    """One unparseable audit row must not crash the cycle -- and must not be SKIPPED either.

    Skipping would REDUCE measured exposure, loosening rails 4/5/6: that is fail-OPEN, which is
    why this deliberately departs from the feasibility doc's "skip-or-flag" wording. Counting it
    can only over-state exposure, which is the closed direction.
    """
    _seed_filled_order(
        repo,
        product_id=FUTURES_PRODUCT_ID,
        side=Side.BUY,
        qty=Decimal("1"),
        price=Decimal("400"),
        created_at=NOW_TS - 86_400,
    )
    _seed_filled_order(
        repo,
        product_id=EQUITY_PRODUCT_ID,
        side=Side.BUY,
        qty=Decimal("2"),
        price=Decimal("50"),
        created_at=NOW_TS - 86_400,
    )

    with caplog.at_level(logging.WARNING):
        exposure = guards._open_exposure_by_asset(repo)
        result = check(_intent(), repo, _config(allowlist=LIVE_ALLOWLIST), NOW_TS)

    # Counted under `_asset`'s key: the futures contract lands in its root's bucket (merging can
    # only over-state ADA, the closed direction), the separator-less hash under itself.
    assert exposure == {"ADA": Decimal("400"), EQUITY_PRODUCT_ID: Decimal("100")}
    # The WARNING is how an operator finds out -- and it must NAME the row, or it cannot be
    # acted on. `log_event` carries fields in the `keel_fields` extra, not in the message.
    warned = {
        r.keel_fields["product"]
        for r in caplog.records
        if r.getMessage() == "guards.exposure_row_unparseable"
    }
    assert warned == {FUTURES_PRODUCT_ID, EQUITY_PRODUCT_ID}
    assert "guards.exposure_row_unparseable" in caplog.text
    assert isinstance(result, GuardResult)  # no raise


def test_a_malformed_history_row_still_counts_toward_the_exposure_cap(repo: Repository) -> None:
    """The rail that matters: the $400 above is real money at risk, and rail 4 must see it."""
    _seed_filled_order(
        repo,
        product_id=FUTURES_PRODUCT_ID,
        side=Side.BUY,
        qty=Decimal("1"),
        price=Decimal("960"),
        created_at=NOW_TS - 86_400,
    )

    result = check(_intent(notional=Decimal("50")), repo, _config(), NOW_TS)

    assert "total_exposure_cap" in _keys(result), (
        "an unparseable row was dropped from exposure -- that is fail-OPEN"
    )


# -- offline mode (paper trading only) -----------------------------------------


def test_offline_skips_ONLY_the_live_state_rails_and_records_them(repo: Repository) -> None:
    """Paper has no live account, so rails 13/17 cannot be evaluated -- but the skip is RECORDED.

    A paper track record that silently omitted checks would promote a strategy on evidence of
    trades live trading would have vetoed. That is what the proving gate exists to prevent.
    """
    intent = _intent(available_quote=None, withdrawals_enabled=None)

    live = check(intent, repo, _config(), NOW_TS)
    assert "usdc_funding" in _keys(live)
    assert "withdrawal_capability" in _keys(live)
    assert live.skipped_rails == []

    offline = check(intent, repo, _config(), NOW_TS, offline=True)
    assert "usdc_funding" not in _keys(offline)
    assert "withdrawal_capability" not in _keys(offline)
    assert set(offline.skipped_rails) == set(LIVE_STATE_RAILS)


def test_offline_still_enforces_every_other_rail(repo: Repository) -> None:
    """The whole point: offline is not "rails off"."""
    intent = _intent(product_id="DOGE-USD", available_quote=None, withdrawals_enabled=None)
    offline = check(intent, repo, _config(), NOW_TS, offline=True)
    assert "halal_allowlist" in _keys(offline)
    assert offline.ok is False


def test_offline_still_honours_the_kill_switch(repo: Repository) -> None:
    """Killing the agent must stop paper too, or the kill-switch means less than it says."""
    repo.set_state("kill_switch", True)
    offline = check(
        _intent(available_quote=None, withdrawals_enabled=None), repo, _config(), NOW_TS,
        offline=True,
    )
    assert offline.ok is False


def test_a_clean_intent_passes_offline_without_live_state(repo: Repository) -> None:
    intent = _intent(available_quote=None, withdrawals_enabled=None)
    assert check(intent, repo, _config(), NOW_TS, offline=True).ok is True
