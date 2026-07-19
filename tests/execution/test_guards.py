"""Tests for keel.execution.guards -- the twelve §14 hard rails.

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
from keel_core.subscription import SubscriptionStatus

from keel.config import (
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
from keel.execution.guards import GuardResult, OrderIntent, check
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
