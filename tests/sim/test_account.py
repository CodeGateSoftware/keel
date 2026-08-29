"""Tests for `keel.sim.account` -- the simulation account ledger and its spend-cap subset of
`execution.guards.check`.

`SimAccount.can_open` enforces exactly the spend-cap rails of `guards.check` (per-order, per-day,
exposure, per-asset%, USDC-funding, monthly-allowance) -- never the non-cap rails (allowlist,
min-move, averaging-into-losers, stop-widening, sell-only-on-rule, drawdown breaker,
stale-data/kill-switch, correlation-adjusted sizing). The critical `test_parity_with_guards_check_*`
tests build an equivalent `Repository`/`Config` state and assert `SimAccount.can_open` agrees with
`guards.check` across a grid of notionals spanning every cap boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from keel.config import (
    Caps,
    Config,
    MarketDataConfig,
    MoneyMgmtConfig,
    SubscriptionConfig,
)
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.execution import guards
from keel.execution.guards import OrderIntent
from keel.sim.account import OpenIntent, OpenPosition, SimAccount
from keel.types import Side
from tests.conftest import attest_subscription, attest_trade_scope


def _ts(year: int, month: int, day: int, hour: int = 12) -> int:
    return int(datetime(year, month, day, hour, 0, 0, tzinfo=UTC).timestamp())


DAY0 = _ts(2024, 1, 10)
JAN15 = _ts(2024, 1, 15)
JAN20 = _ts(2024, 1, 20)
FEB01 = _ts(2024, 2, 1)


def _config(
    *,
    max_per_order_usd: Decimal = Decimal("1000000"),
    max_per_day_usd: Decimal = Decimal("1000000"),
    max_exposure_usd: Decimal = Decimal("1000000"),
    max_per_asset_pct: Decimal = Decimal("1"),
    assumed_free_volume_usd: Decimal = Decimal("500"),
    pacing: str = "opportunistic",
    money_mgmt: MoneyMgmtConfig | None = None,
) -> Config:
    return Config(
        money_mgmt=money_mgmt or MoneyMgmtConfig(),
        allowlist=["BTC", "ETH", "PAXG"],
        target_weights={},
        risk_pct=Decimal("0.01"),
        caps=Caps(
            max_per_order_usd=max_per_order_usd,
            max_per_day_usd=max_per_day_usd,
            max_exposure_usd=max_exposure_usd,
            max_per_asset_pct=max_per_asset_pct,
        ),
        market_data=MarketDataConfig(granularities=[], history_days=365),
        subscription=SubscriptionConfig(
            assumed_free_volume_usd=assumed_free_volume_usd, pacing=pacing
        ),
    )


@pytest.fixture
def sim_config() -> Config:
    """Roomy on every cap except the $500 monthly allowance (the plan's illustrative default)."""
    return _config()


def _intent(
    *,
    asset: str = "BTC",
    qty: Decimal | None = None,
    entry: Decimal = Decimal("100"),
    stop: Decimal | None = None,
    notional: Decimal = Decimal("50"),
    is_dca: bool = True,
    rule_kind: str = "dca",
) -> OpenIntent:
    if qty is None:
        qty = notional / entry
    return OpenIntent(
        asset=asset,
        qty=qty,
        entry=entry,
        stop=stop,
        notional=notional,
        is_dca=is_dca,
        rule_kind=rule_kind,
    )


# -- deposit ---------------------------------------------------------------------------------


def test_deposit_adds_cash_and_contributed():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("500"), DAY0)
    acc.deposit(Decimal("250"), DAY0)

    assert acc.cash_usdc == Decimal("750")
    assert acc.contributed == Decimal("750")


def test_deposit_resets_month_spend_on_month_rollover(sim_config):
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), JAN15)
    acc.open(_intent(notional=Decimal("500")), fill_price=Decimal("100"), now_ts=JAN15)

    # month maxed at $500 -- same month (JAN20), still blocked
    ok, reasons = acc.can_open(_intent(notional=Decimal("1")), sim_config, JAN20)
    assert ok is False
    assert any("allowance" in r.lower() for r in reasons)

    # a new UTC month -- allowance is fresh again
    acc.deposit(Decimal("500"), FEB01)
    ok, reasons = acc.can_open(_intent(notional=Decimal("1")), sim_config, FEB01)
    assert ok is True
    assert reasons == []


# -- per-order cap ------------------------------------------------------------------------------


def test_per_order_cap_rejects_over_cap():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), DAY0)
    config = _config(max_per_order_usd=Decimal("100"))

    ok, reasons = acc.can_open(_intent(notional=Decimal("150")), config, DAY0)

    assert ok is False
    assert any("per_order_cap" in r for r in reasons)


def test_per_order_cap_allows_at_exact_boundary():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), DAY0)
    config = _config(max_per_order_usd=Decimal("100"))

    ok, reasons = acc.can_open(_intent(notional=Decimal("100")), config, DAY0)

    assert ok is True
    assert reasons == []


# -- per-day cap ----------------------------------------------------------------------------------


def test_per_day_cap_rejects_when_running_total_exceeds_cap():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), DAY0)
    acc.open(_intent(notional=Decimal("260")), fill_price=Decimal("100"), now_ts=DAY0)
    config = _config(max_per_day_usd=Decimal("300"))

    ok, reasons = acc.can_open(_intent(notional=Decimal("50")), config, DAY0)  # 260+50=310>300

    assert ok is False
    assert any("per_day_cap" in r for r in reasons)


def test_per_day_cap_ignores_spend_from_a_prior_day():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), DAY0)
    acc.open(_intent(notional=Decimal("260")), fill_price=Decimal("100"), now_ts=DAY0 - 1_000_000)
    config = _config(max_per_day_usd=Decimal("300"))

    ok, reasons = acc.can_open(_intent(notional=Decimal("50")), config, DAY0)

    assert ok is True
    assert reasons == []


# -- exposure cap ----------------------------------------------------------------------------------


def test_exposure_cap_rejects_over_cap():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), DAY0)
    acc.open(
        _intent(asset="ETH", notional=Decimal("960")),
        fill_price=Decimal("100"),
        now_ts=DAY0 - 1_000_000,
    )
    config = _config(max_exposure_usd=Decimal("1000"), max_per_asset_pct=Decimal("1"))

    ok, reasons = acc.can_open(
        _intent(asset="BTC", notional=Decimal("45")), config, DAY0
    )  # 960+45=1005>1000

    assert ok is False
    assert any("total_exposure_cap" in r for r in reasons)


# -- per-asset concentration cap -------------------------------------------------------------------


def test_per_asset_cap_rejects_over_cap_even_when_total_exposure_has_room():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), DAY0)
    config = _config(max_exposure_usd=Decimal("1000"), max_per_asset_pct=Decimal("0.1"))

    # per-asset limit = 0.1 * 1000 = 100; notional 150 exceeds it even though total exposure (0)
    # has plenty of room against max_exposure_usd (1000).
    ok, reasons = acc.can_open(_intent(notional=Decimal("150")), config, DAY0)

    assert ok is False
    assert any("per_asset_concentration_cap" in r for r in reasons)


# -- USDC-funding ----------------------------------------------------------------------------------


def test_usdc_funding_blocks_when_cash_below_notional(sim_config):
    acc = SimAccount(fee_pct=Decimal("0.006"), slippage_pct=Decimal("0.0005"))
    acc.deposit(Decimal("50"), now_ts=DAY0)

    ok, reasons = acc.can_open(_intent(notional=Decimal("100")), sim_config, DAY0)

    assert ok is False
    assert any("usdc" in r.lower() for r in reasons)


def test_usdc_funding_blocks_when_cash_is_zero(sim_config):
    acc = SimAccount(Decimal("0"), Decimal("0"))

    ok, reasons = acc.can_open(_intent(notional=Decimal("1")), sim_config, DAY0)

    assert ok is False
    assert any("usdc" in r.lower() for r in reasons)


def test_usdc_funding_passes_when_cash_exactly_covers_notional(sim_config):
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100"), DAY0)

    ok, reasons = acc.can_open(_intent(notional=Decimal("100")), sim_config, DAY0)

    assert ok is True
    assert reasons == []


# -- monthly subscription-allowance ----------------------------------------------------------------


def test_monthly_allowance_caps_cumulative_buys(sim_config):
    # sim_config.subscription.assumed_free_volume_usd == 500
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), DAY0)  # plenty of cash; allowance is the binding cap
    acc.open(_intent(notional=Decimal("450")), fill_price=Decimal("10"), now_ts=DAY0)

    ok, reasons = acc.can_open(_intent(notional=Decimal("100")), sim_config, DAY0)

    assert ok is False
    assert any("allowance" in r.lower() for r in reasons)


def test_monthly_allowance_ignores_spend_from_a_prior_month(sim_config):
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), DAY0)
    acc.open(
        _intent(notional=Decimal("450")), fill_price=Decimal("10"), now_ts=DAY0 - 40 * 86400
    )

    ok, reasons = acc.can_open(_intent(notional=Decimal("100")), sim_config, DAY0)

    assert ok is True
    assert reasons == []


def test_monthly_allowance_even_daily_pacing_vetoes_a_burst_within_the_flat_cap():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), JAN15)
    # 2024-01-15 (Monday) is business day 10 of 23 in Jan 2024 -> paced cap = 220/23*10 ~ 95.65,
    # tighter than the 220 flat monthly cap.
    config = _config(assumed_free_volume_usd=Decimal("220"), pacing="even_daily")

    ok, reasons = acc.can_open(_intent(notional=Decimal("150")), config, JAN15)

    assert ok is False
    assert any("allowance" in r.lower() for r in reasons)


def test_monthly_allowance_opportunistic_pacing_ignores_the_business_day_pace():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), JAN15)
    config = _config(assumed_free_volume_usd=Decimal("220"), pacing="opportunistic")

    ok, reasons = acc.can_open(_intent(notional=Decimal("150")), config, JAN15)

    assert ok is True
    assert reasons == []


# -- monthly/daily rail = trading VOLUME (buys + sells), Issue #85 ---------------------------------


def test_month_volume_includes_sell_notional_unlike_guards_buy_only_spend():
    """`SimAccount`'s monthly rail counts trading VOLUME (buys + sells) -- Coinbase One's actual
    fee-free perk is a monthly volume allowance, not a buy-only spend cap. `execution.guards.check`
    (rail 14, `_monthly_buy_spend_usd`) still counts BUY notional only and is untouched by this
    issue -- an intentional, documented sim/live divergence (see `sim/account.py`'s module
    docstring). This closed BTC round-trip (buy 200 + sell 200 = 400 volume) would leave a
    buy-only system with just 200 of month-to-date spend; the sim sees the full 400.
    """
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), DAY0)
    acc.open(
        _intent(asset="BTC", notional=Decimal("200"), entry=Decimal("100")), Decimal("100"), DAY0
    )
    acc.close("BTC", fill_price=Decimal("100"), now_ts=DAY0)  # +200 volume: 400 total this month

    config = _config(assumed_free_volume_usd=Decimal("350"))  # buy-only spend (200) fits; volume
    # (400) doesn't.

    ok, reasons = acc.can_open(_intent(notional=Decimal("1")), config, DAY0)

    assert ok is False
    assert any("allowance" in r.lower() for r in reasons)


def test_day_volume_includes_sell_notional_unlike_guards_buy_only_spend():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), DAY0)
    acc.open(
        _intent(asset="BTC", notional=Decimal("100"), entry=Decimal("100")), Decimal("100"), DAY0
    )
    acc.close("BTC", fill_price=Decimal("100"), now_ts=DAY0)  # +100 volume: 200 total today

    config = _config(max_per_day_usd=Decimal("250"))  # buy-only spend (100) fits; volume
    # (200) + this order (100) = 300 doesn't.

    ok, reasons = acc.can_open(_intent(notional=Decimal("100")), config, DAY0)

    assert ok is False
    assert any("per_day_cap" in r for r in reasons)


def test_sim_diverges_from_guards_on_monthly_allowance_once_a_sell_has_occurred():
    """Direct guards-vs-sim comparison demonstrating the documented divergence: a BTC round-trip
    (buy then sell, both today) leaves `guards.check` seeing only the buy leg (150) against its
    month-to-date BUY spend, while `SimAccount` sees the full round-trip (300) as volume -- so an
    order that guards would allow, the sim correctly (by its own, volume-based rail) rejects.
    """
    now_ts = JAN15
    month_start, _ = guards._utc_month_bounds(now_ts)

    conn = connect(":memory:")
    migrate(conn)
    repo = Repository(conn)
    repo.set_state("kill_switch", False)
    repo.set_state("last_feed_ts", now_ts)
    # Rail 20 (#233) fails closed without a trade-scope record; this comparison is about the
    # monthly allowance divergence, not rail 20, so confirm coinbase here.
    attest_trade_scope(repo, now_ts=now_ts)
    # Subscription is attested below, once `config` (and its assumed_free_volume_usd) exists --
    # guards.check derives its cap from the attested record, so it must match what `config`
    # gives the sim side for this to be a genuine guards-vs-sim comparison.
    _seed_filled_buy(
        repo, product_id="BTC-USD", qty=Decimal("1.5"), price=Decimal("100"), created_at=now_ts - 60
    )
    _seed_filled_sell(
        repo, product_id="BTC-USD", qty=Decimal("1.5"), price=Decimal("100"), created_at=now_ts - 30
    )

    account = SimAccount(fee_pct=Decimal("0"), slippage_pct=Decimal("0"))
    account.deposit(Decimal("500"), now_ts=now_ts - 60)
    account.open(
        OpenIntent(
            asset="BTC", qty=Decimal("1.5"), entry=Decimal("100"), stop=None,
            notional=Decimal("150"), is_dca=True, rule_kind="dca",
        ),
        fill_price=Decimal("100"),
        now_ts=now_ts - 60,
    )
    account.close("BTC", fill_price=Decimal("100"), now_ts=now_ts - 30)

    config = _config(assumed_free_volume_usd=Decimal("200"))  # guards: 150 buy-spend, room for 50
    # more; sim: 300 volume already exceeds 200 outright.
    _attest(
        repo,
        free_volume_usd=config.subscription.assumed_free_volume_usd,
        pacing=config.subscription.pacing,
        now_ts=now_ts,
    )
    order_intent = OrderIntent(
        product_id="BTC-USD", side=Side.BUY, qty=Decimal("0.5"), entry=Decimal("100"), stop=None,
        notional=Decimal("50"), is_dca=True, rule_kind="dca", available_quote=account.cash_usdc,
        # Rail 17 is a COMPLIANCE rail (§65.4), not a spend cap, so `SimAccount` deliberately
        # does not model it. Supplied so the spend-cap parity under test is what diverges.
        withdrawals_enabled=True,
    )
    sim_intent = OpenIntent(
        asset="BTC", qty=Decimal("0.5"), entry=Decimal("100"), stop=None,
        notional=Decimal("50"), is_dca=True, rule_kind="dca",
    )

    guard_result = guards.check(order_intent, repo, config, now_ts)
    sim_ok, sim_reasons = account.can_open(sim_intent, config, now_ts)

    assert guard_result.ok is True  # buy-only: 150 + 50 = 200, exactly at the cap
    assert sim_ok is False  # volume: 300 already exceeds the 200 cap before this order
    assert any("allowance" in r.lower() for r in sim_reasons)


# -- max_affordable_notional: the headroom `sim.portfolio_sim` clamps risk-sized orders to -------


def test_max_affordable_notional_bound_by_cash_when_it_is_the_tightest_cap():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("500"), DAY0)
    config = _config(assumed_free_volume_usd=Decimal("1000000"))

    headroom = acc.max_affordable_notional("BTC", config, DAY0)

    assert headroom == Decimal("500")


def test_max_affordable_notional_bound_by_per_asset_concentration():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), DAY0)
    config = _config(
        max_exposure_usd=Decimal("1000"),
        max_per_asset_pct=Decimal("0.1"),
        assumed_free_volume_usd=Decimal("1000000"),
    )

    headroom = acc.max_affordable_notional("BTC", config, DAY0)

    assert headroom == Decimal("100")  # 0.1 * 1000


def test_max_affordable_notional_bound_by_total_exposure():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), DAY0)
    config = _config(max_exposure_usd=Decimal("500"), assumed_free_volume_usd=Decimal("1000000"))
    acc.open(_intent(asset="ETH", notional=Decimal("300")), Decimal("100"), DAY0)

    headroom = acc.max_affordable_notional("BTC", config, DAY0)

    assert headroom == Decimal("200")  # 500 - 300 already open (in another asset)


def test_max_affordable_notional_bound_by_monthly_allowance():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), DAY0)
    config = _config(assumed_free_volume_usd=Decimal("75"))

    headroom = acc.max_affordable_notional("BTC", config, DAY0)

    assert headroom == Decimal("75")


def test_max_affordable_notional_combines_rule_and_dca_notional_for_the_same_asset():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), DAY0)
    config = _config(
        max_exposure_usd=Decimal("1000"),
        max_per_asset_pct=Decimal("0.5"),
        assumed_free_volume_usd=Decimal("1000000"),
    )
    acc.open(_intent(asset="BTC", notional=Decimal("100")), Decimal("100"), DAY0, dca=True)

    headroom = acc.max_affordable_notional("BTC", config, DAY0)

    assert headroom == Decimal("400")  # 0.5*1000 - 100 already-open DCA notional


def test_max_affordable_notional_never_goes_negative():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), DAY0)
    config = _config(assumed_free_volume_usd=Decimal("100"))
    acc.open(_intent(asset="BTC", notional=Decimal("100000")), Decimal("100"), DAY0)  # blows
    # every cap except the ones already-open exposure doesn't touch

    headroom = acc.max_affordable_notional("ETH", config, DAY0)

    assert headroom >= Decimal("0")


# -- DCA is NOT exempt from any spend cap (matches rail 14) ----------------------------------------


def test_dca_is_not_exempt_from_the_per_order_cap():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), DAY0)
    config = _config(max_per_order_usd=Decimal("100"))

    ok, reasons = acc.can_open(
        _intent(notional=Decimal("150"), is_dca=True, rule_kind="dca"), config, DAY0
    )

    assert ok is False
    assert any("per_order_cap" in r for r in reasons)


# -- collects every violation, never short-circuits ------------------------------------------------


def test_can_open_collects_multiple_violations_without_short_circuiting(sim_config):
    acc = SimAccount(Decimal("0"), Decimal("0"))
    # no deposit -- cash_usdc stays 0, so usdc_funding always trips too
    config = _config(max_per_order_usd=Decimal("10"), assumed_free_volume_usd=Decimal("5"))

    ok, reasons = acc.can_open(_intent(notional=Decimal("50")), config, DAY0)

    assert ok is False
    keys = {r.split(":", 1)[0] for r in reasons}
    assert {"per_order_cap", "usdc_funding", "monthly_subscription_allowance"} <= keys


# -- open / close: fees, slippage, realized pnl ----------------------------------------------------


def test_open_debits_cash_including_fee_and_slippage():
    acc = SimAccount(fee_pct=Decimal("0.01"), slippage_pct=Decimal("0.01"))
    acc.deposit(Decimal("1000"), DAY0)

    acc.open(
        _intent(asset="BTC", qty=Decimal("1"), notional=Decimal("100")),
        fill_price=Decimal("100"),
        now_ts=DAY0,
    )

    entry_fill = Decimal("100") * (Decimal(1) + Decimal("0.01"))  # 101
    entry_fee = entry_fill * Decimal("1") * Decimal("0.01")  # 1.01
    expected_cash = Decimal("1000") - entry_fill - entry_fee
    assert acc.cash_usdc == expected_cash
    assert acc.position("BTC") is not None
    position = acc.position("BTC")
    assert position.entry_fill == entry_fill
    assert position.qty == Decimal("1")


def test_open_then_close_realizes_pnl_net_of_fees():
    acc = SimAccount(Decimal("0.006"), Decimal("0.0005"))
    acc.deposit(Decimal("1000"), DAY0)
    acc.open(
        _intent(asset="BTC", qty=Decimal("1"), entry=Decimal("100"), notional=Decimal("100")),
        Decimal("100"),
        DAY0,
    )

    pnl = acc.close("BTC", fill_price=Decimal("120"), now_ts=DAY0)

    assert pnl > 0
    assert acc.position("BTC") is None
    assert acc.realized_pnl == pnl


def test_close_realizes_a_loss_when_exit_is_below_entry():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("1000"), DAY0)
    acc.open(
        _intent(asset="BTC", qty=Decimal("1"), entry=Decimal("100"), notional=Decimal("100")),
        Decimal("100"),
        DAY0,
    )

    pnl = acc.close("BTC", fill_price=Decimal("80"), now_ts=DAY0)

    assert pnl == Decimal("-20")
    assert acc.cash_usdc == Decimal("1000") - Decimal("100") + Decimal("80")


def test_close_frees_up_exposure_for_a_subsequent_open(sim_config):
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), DAY0)
    config = _config(max_exposure_usd=Decimal("100"), max_per_asset_pct=Decimal("1"))
    acc.open(
        _intent(asset="BTC", qty=Decimal("1"), entry=Decimal("100"), notional=Decimal("100")),
        Decimal("100"),
        DAY0,
    )
    # fully deployed against the exposure cap -- a second open should be blocked
    blocked, _ = acc.can_open(_intent(asset="ETH", notional=Decimal("1")), config, DAY0)
    assert blocked is False

    acc.close("BTC", fill_price=Decimal("100"), now_ts=DAY0)

    allowed, reasons = acc.can_open(_intent(asset="ETH", notional=Decimal("50")), config, DAY0)
    assert allowed is True
    assert reasons == []


# -- exposure / mark-to-market reporting -----------------------------------------------------------


def test_exposure_usd_marks_open_positions_to_current_prices():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("1000"), DAY0)
    acc.open(
        _intent(asset="BTC", qty=Decimal("2"), entry=Decimal("100"), notional=Decimal("200")),
        Decimal("100"),
        DAY0,
    )

    exposure = acc.exposure_usd({"BTC": Decimal("150")})

    assert exposure == Decimal("300")  # 2 * 150


def test_mark_to_market_is_cash_plus_exposure():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("1000"), DAY0)
    acc.open(
        _intent(asset="BTC", qty=Decimal("2"), entry=Decimal("100"), notional=Decimal("200")),
        Decimal("100"),
        DAY0,
    )

    mtm = acc.mark_to_market({"BTC": Decimal("150")})

    assert mtm == acc.cash_usdc + Decimal("300")


# -- DCA sleeve: separate from the rule slot, accumulates, never auto-closes (Issue #85) -----------


def test_open_with_dca_true_lands_in_dca_positions_not_positions():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("1000"), DAY0)

    acc.open(
        _intent(asset="BTC", qty=Decimal("1"), notional=Decimal("100")),
        Decimal("100"),
        DAY0,
        dca=True,
    )

    assert acc.position("BTC") is None
    assert "BTC" in acc.dca_positions
    assert acc.dca_positions["BTC"].qty == Decimal("1")


def test_dca_open_accumulates_qty_and_weighted_average_cost():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("10000"), DAY0)

    acc.open(
        _intent(asset="BTC", qty=Decimal("1"), entry=Decimal("100"), notional=Decimal("100")),
        Decimal("100"),
        DAY0,
        dca=True,
    )
    acc.open(
        _intent(asset="BTC", qty=Decimal("1"), entry=Decimal("200"), notional=Decimal("200")),
        Decimal("200"),
        DAY0,
        dca=True,
    )

    lot = acc.dca_positions["BTC"]
    assert lot.qty == Decimal("2")
    assert lot.entry_fill == Decimal("150")  # (100*1 + 200*1) / 2


def test_dca_position_and_rule_position_coexist_on_the_same_asset():
    """The bug this fix retires: DCA and a risk-defined rule trade used to share one per-asset
    slot, so an asset accumulating DCA permanently froze the rule slot. They're now independent."""
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("10000"), DAY0)

    acc.open(
        _intent(asset="BTC", qty=Decimal("1"), notional=Decimal("100")), Decimal("100"), DAY0,
        dca=True,
    )
    acc.open(
        _intent(asset="BTC", qty=Decimal("1"), notional=Decimal("100"), is_dca=False),
        Decimal("100"),
        DAY0,
    )

    assert "BTC" in acc.dca_positions
    assert acc.position("BTC") is not None

    pnl = acc.close("BTC", fill_price=Decimal("110"), now_ts=DAY0)  # closes the RULE slot only

    assert pnl > 0
    assert acc.position("BTC") is None
    assert "BTC" in acc.dca_positions  # the DCA lot is untouched by closing the rule slot


def test_dca_and_rule_notional_both_count_toward_the_same_per_asset_concentration_cap():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), DAY0)
    config = _config(max_exposure_usd=Decimal("1000"), max_per_asset_pct=Decimal("0.5"))
    acc.open(
        _intent(asset="BTC", notional=Decimal("400")), Decimal("100"), DAY0, dca=True
    )  # dca=400

    # 400 (dca) + 150 (rule) = 550 > 500 (0.5*1000)
    blocked, reasons = acc.can_open(_intent(asset="BTC", notional=Decimal("150")), config, DAY0)
    assert blocked is False
    assert any("per_asset_concentration_cap" in r for r in reasons)

    allowed, reasons = acc.can_open(_intent(asset="BTC", notional=Decimal("100")), config, DAY0)
    assert allowed is True
    assert reasons == []


def test_exposure_usd_includes_dca_positions():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("1000"), DAY0)
    acc.open(
        _intent(asset="BTC", qty=Decimal("1"), entry=Decimal("100"), notional=Decimal("100")),
        Decimal("100"),
        DAY0,
        dca=True,
    )
    acc.open(
        _intent(asset="ETH", qty=Decimal("2"), entry=Decimal("100"), notional=Decimal("200"),
                is_dca=False),
        Decimal("100"),
        DAY0,
    )

    exposure = acc.exposure_usd({"BTC": Decimal("150"), "ETH": Decimal("50")})

    assert exposure == Decimal("250")  # 1*150 (dca) + 2*50 (rule)


# -- dataclasses -----------------------------------------------------------------------------------


def test_open_position_and_open_intent_are_plain_dataclasses():
    position = OpenPosition(
        asset="BTC",
        qty=Decimal("1"),
        entry_fill=Decimal("100"),
        entry_ts=DAY0,
        stop=Decimal("95"),
        rule_kind="pullback_continuation",
    )
    intent = OpenIntent(
        asset="BTC",
        qty=Decimal("1"),
        entry=Decimal("100"),
        stop=Decimal("95"),
        notional=Decimal("100"),
        is_dca=False,
        rule_kind="pullback_continuation",
    )

    assert position.asset == "BTC"
    assert intent.notional == Decimal("100")


# -- parity with execution.guards.check ------------------------------------------------------------


def _attest(
    repo: Repository,
    *,
    free_volume_usd: Decimal | None,
    pacing: str = "opportunistic",
    now_ts: int,
) -> None:
    """Attest a coinbase subscription -- rail 14 (`guards.check`) now derives its cap from this
    record rather than from `config.subscription`, so a guards/sim parity comparison must attest
    a record matching whatever `config.subscription` the sim side reads."""
    attest_subscription(
        repo, now_ts=now_ts, free_volume_usd=free_volume_usd, pacing=pacing
    )


def _seed_filled_buy(
    repo: Repository, *, product_id: str, qty: Decimal, price: Decimal, created_at: int
) -> None:
    repo.insert_order(
        dict(
            mode="live",
            product_id=product_id,
            side=Side.BUY.value,
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


def _seed_filled_sell(
    repo: Repository, *, product_id: str, qty: Decimal, price: Decimal, created_at: int
) -> None:
    repo.insert_order(
        dict(
            mode="live",
            product_id=product_id,
            side=Side.SELL.value,
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


def _parity_scenario(now_ts: int) -> tuple[Repository, SimAccount]:
    """A repo + account pair with equivalent spend-cap state:

    - BTC: one order filled earlier this month (not today) -- exposure=200, month volume +=200.
    - PAXG: bought (not sold) *today* -- exposure=150, contributes +150 to today's (and this
      month's) volume. PAXG (not ETH) specifically -- it's gold-backed and excluded from rail 5
      (correlation-adjusted sizing, `guards.UNCORRELATED_ASSETS`), which is outside the spend-cap
      subset `SimAccount` enforces; using a correlated asset here would trip rail 5 on the guards
      side with no sim-side equivalent to compare against.
    - cash_usdc == 150 after all of the above (a clean USDC-funding boundary).

    Deliberately BUY-only (Issue #85): `SimAccount`'s day/month rail now counts VOLUME (buys +
    sells, see `sim/account.py`'s module docstring), while `guards.check` counts BUY notional
    only -- an intentional, documented sim/live divergence. Keeping this SHARED scenario free of
    any SELL keeps every rail (including per-day/monthly-allowance) in genuine full parity here;
    the divergence itself is exercised directly by
    `test_month_and_day_volume_include_sell_notional_unlike_guards_buy_only_spend` below.
    """
    month_start, _ = guards._utc_month_bounds(now_ts)

    conn = connect(":memory:")
    migrate(conn)
    repo = Repository(conn)
    repo.set_state("kill_switch", False)
    repo.set_state("last_feed_ts", now_ts)
    # Rail 20 (#233) fails closed without a trade-scope record; these parity tests are about
    # the spend-cap rails, not rail 20, so confirm coinbase here.
    attest_trade_scope(repo, now_ts=now_ts)
    # No subscription attestation here -- each caller attests a record matching its own
    # `config.subscription` (see `_attest`), since guards.check now derives its cap from the
    # attested record while SimAccount reads `config.subscription` directly; parity requires
    # them to agree, as an explicit setup step rather than an ambient coincidence.

    _seed_filled_buy(
        repo, product_id="BTC-USD", qty=Decimal("2"), price=Decimal("100"),
        created_at=month_start + 3600,
    )
    _seed_filled_buy(
        repo, product_id="PAXG-USD", qty=Decimal("1.5"), price=Decimal("100"),
        created_at=now_ts - 60,
    )

    account = SimAccount(fee_pct=Decimal("0"), slippage_pct=Decimal("0"))
    account.deposit(Decimal("500"), now_ts=month_start + 3600)
    account.open(
        OpenIntent(
            asset="BTC", qty=Decimal("2"), entry=Decimal("100"), stop=None,
            notional=Decimal("200"), is_dca=True, rule_kind="dca",
        ),
        fill_price=Decimal("100"),
        now_ts=month_start + 3600,
    )
    account.open(
        OpenIntent(
            asset="PAXG", qty=Decimal("1.5"), entry=Decimal("100"), stop=None,
            notional=Decimal("150"), is_dca=True, rule_kind="dca",
        ),
        fill_price=Decimal("100"),
        now_ts=now_ts - 60,
    )

    assert account.cash_usdc == Decimal("150")  # sanity: matches the docstring above
    return repo, account


def _assert_parity(repo, account, config, now_ts, notionals):
    for notional in notionals:
        notional = Decimal(notional)
        order_intent = OrderIntent(
            product_id="BTC-USD",
            side=Side.BUY,
            qty=notional / Decimal("100"),
            entry=Decimal("100"),
            stop=None,
            notional=notional,
            is_dca=True,
            rule_kind="dca",
            available_quote=account.cash_usdc,
            withdrawals_enabled=True,  # compliance rail, not a spend cap -- see above
        )
        sim_intent = OpenIntent(
            asset="BTC",
            qty=notional / Decimal("100"),
            entry=Decimal("100"),
            stop=None,
            notional=notional,
            is_dca=True,
            rule_kind="dca",
        )

        guard_result = guards.check(order_intent, repo, config, now_ts)
        sim_ok, sim_reasons = account.can_open(sim_intent, config, now_ts)

        assert sim_ok == guard_result.ok, (
            f"parity mismatch at notional={notional}: "
            f"sim_ok={sim_ok} sim_reasons={sim_reasons} "
            f"guards_ok={guard_result.ok} guards_violations={guard_result.violations}"
        )


def test_parity_with_guards_check_opportunistic_pacing():
    now_ts = JAN15
    repo, account = _parity_scenario(now_ts)
    config = _config(
        max_per_order_usd=Decimal("120"),
        max_per_day_usd=Decimal("280"),
        max_exposure_usd=Decimal("1000"),
        max_per_asset_pct=Decimal("0.55"),
        assumed_free_volume_usd=Decimal("500"),
        pacing="opportunistic",
    )
    # Attest a record matching config.subscription exactly -- guards.check derives its cap from
    # the attested record while SimAccount reads config.subscription directly, so parity requires
    # an explicit attestation here rather than an ambient coincidence.
    _attest(
        repo,
        free_volume_usd=config.subscription.assumed_free_volume_usd,
        pacing=config.subscription.pacing,
        now_ts=now_ts,
    )

    # boundaries: per_order=120, per_day=130 (280-150), monthly=150 (500-350) -- coincides with
    # usdc=150 (cash=150) here, per_asset=350 (0.55*1000-200), exposure=650 (1000-350, since ETH
    # (150) is left open in this BUY-only scenario -- see `_parity_scenario`'s docstring).
    boundaries = (120, 130, 150, 350, 650)
    notionals = sorted(
        {1, 2000}
        | {b - 1 for b in boundaries}
        | {b for b in boundaries}
        | {b + 1 for b in boundaries}
    )

    _assert_parity(repo, account, config, now_ts, notionals)


def test_parity_with_guards_check_even_daily_pacing():
    now_ts = JAN15
    repo, account = _parity_scenario(now_ts)
    config = _config(
        max_per_order_usd=Decimal("120"),
        max_per_day_usd=Decimal("280"),
        max_exposure_usd=Decimal("1000"),
        max_per_asset_pct=Decimal("0.55"),
        assumed_free_volume_usd=Decimal("2000"),
        pacing="even_daily",
    )
    # Attest a record matching config.subscription exactly -- guards.check derives its cap (and
    # its pacing) from the attested record while SimAccount reads config.subscription directly,
    # so parity requires an explicit attestation here rather than an ambient coincidence.
    _attest(
        repo,
        free_volume_usd=config.subscription.assumed_free_volume_usd,
        pacing=config.subscription.pacing,
        now_ts=now_ts,
    )

    # derive the paced boundary from the same helpers the implementation is required to reuse,
    # so this test doesn't hardcode a hand-computed business-day count.
    dt = datetime.fromtimestamp(now_ts, tz=UTC)
    biz_in_month = guards._business_days_in_month(dt.year, dt.month)
    biz_elapsed = guards._business_days_elapsed(dt.year, dt.month, dt.day)
    paced_cap = (Decimal("2000") / biz_in_month) * biz_elapsed
    month_spend_baseline = Decimal("350")
    paced_boundary = int(min(paced_cap, Decimal("2000")) - month_spend_baseline)

    # usdc=150 (cash=150), exposure=650 (1000-350) -- see `_parity_scenario`'s docstring for why
    # ETH (150) is left open rather than netted to ~0 in this BUY-only shared scenario.
    boundaries = {120, 130, 150, 350, 650, paced_boundary}
    notionals = sorted(
        {1}
        | {b - 1 for b in boundaries}
        | {b for b in boundaries}
        | {b + 1 for b in boundaries}
    )

    _assert_parity(repo, account, config, now_ts, notionals)


# -- rail 16 parity: consecutive-loss circuit breaker -----------------------------------------


def test_parity_with_guards_check_streak_halt_active():
    """`SimAccount.can_open` and `guards.check` must agree while the consecutive-loss halt is
    active, and agree once it clears -- each side told about the same `streak_halt_until` value
    (repo state for guards, the mirrored instance attribute for the sim, per the module
    docstring's "Rail 16 parity" note). Every other cap is kept roomy so only rail 16 can trip.
    """
    now_ts = JAN15

    conn = connect(":memory:")
    migrate(conn)
    repo = Repository(conn)
    repo.set_state("kill_switch", False)
    repo.set_state("last_feed_ts", now_ts)
    repo.set_state("streak_halt_until", now_ts + 3600)
    # Rail 20 (#233) fails closed without a trade-scope record; this comparison is about rail
    # 16, not rail 20, so confirm coinbase here.
    attest_trade_scope(repo, now_ts=now_ts)

    config = _config()  # roomy on every spend cap ($500 monthly allowance is the only real cap)
    _attest(
        repo,
        free_volume_usd=config.subscription.assumed_free_volume_usd,
        pacing=config.subscription.pacing,
        now_ts=now_ts,
    )

    account = SimAccount(fee_pct=Decimal("0"), slippage_pct=Decimal("0"))
    account.deposit(Decimal("100000"), now_ts=now_ts)
    account.streak_halt_until = now_ts + 3600

    order_intent = OrderIntent(
        product_id="BTC-USD",
        side=Side.BUY,
        qty=Decimal("1"),
        entry=Decimal("100"),
        stop=None,
        notional=Decimal("100"),
        is_dca=False,
        rule_kind="pullback_continuation",
        available_quote=account.cash_usdc,
        # Rail 17 is a COMPLIANCE rail (§65.4), not a spend cap, so `SimAccount` deliberately
        # does not model it. Supplied so the spend-cap parity under test is what diverges.
        withdrawals_enabled=True,
    )
    sim_intent = OpenIntent(
        asset="BTC",
        qty=Decimal("1"),
        entry=Decimal("100"),
        stop=None,
        notional=Decimal("100"),
        is_dca=False,
        rule_kind="pullback_continuation",
    )

    guard_result = guards.check(order_intent, repo, config, now_ts)
    sim_ok, sim_reasons = account.can_open(sim_intent, config, now_ts)

    assert guard_result.ok is False
    assert {v.split(":", 1)[0] for v in guard_result.violations} == {"consecutive_loss_breaker"}
    assert sim_ok is False
    assert {r.split(":", 1)[0] for r in sim_reasons} == {"consecutive_loss_breaker"}

    # negative control: same repo/account/config, halt cleared -- both must now agree it's OK.
    repo.set_state("streak_halt_until", now_ts - 1)
    account.streak_halt_until = now_ts - 1

    guard_result_cleared = guards.check(order_intent, repo, config, now_ts)
    sim_ok_cleared, sim_reasons_cleared = account.can_open(sim_intent, config, now_ts)

    assert guard_result_cleared.ok is True
    assert sim_ok_cleared is True
    assert sim_reasons_cleared == []


# ---------------------------------------------------------------------------
# Rail 16: the sim-side streak PRODUCER (mirrors execution/streak.py's live semantics)
# ---------------------------------------------------------------------------


def _streak_config(threshold: int, cooloff_days: int = 2) -> Config:
    return _config(
        money_mgmt=MoneyMgmtConfig(
            max_consecutive_losses=threshold, streak_cooloff_days=cooloff_days
        )
    )


def test_losing_trade_increments_the_counter_without_tripping_below_threshold():
    account = SimAccount(fee_pct=Decimal("0"), slippage_pct=Decimal("0"))
    config = _streak_config(threshold=3)

    account.record_trade_outcome(Decimal("-10"), config, JAN15, is_dca=False)
    account.record_trade_outcome(Decimal("-10"), config, JAN15, is_dca=False)

    assert account.consecutive_losses == 2
    assert account.streak_halt_until == 0


def test_reaching_the_threshold_sets_streak_halt_until_by_the_cooloff():
    account = SimAccount(fee_pct=Decimal("0"), slippage_pct=Decimal("0"))
    config = _streak_config(threshold=3, cooloff_days=2)

    for _ in range(3):
        account.record_trade_outcome(Decimal("-10"), config, JAN15, is_dca=False)

    assert account.consecutive_losses == 3
    assert account.streak_halt_until == JAN15 + 2 * 86_400


def test_a_winning_trade_resets_the_consecutive_loss_counter():
    account = SimAccount(fee_pct=Decimal("0"), slippage_pct=Decimal("0"))
    config = _streak_config(threshold=3)

    account.record_trade_outcome(Decimal("-10"), config, JAN15, is_dca=False)
    account.record_trade_outcome(Decimal("-10"), config, JAN15, is_dca=False)
    account.record_trade_outcome(Decimal("1"), config, JAN15, is_dca=False)

    assert account.consecutive_losses == 0
    assert account.streak_halt_until == 0


def test_a_scratch_trade_resets_the_counter_matching_the_live_producer():
    """`execution.streak.record_closed_trade` resets on `pnl_net >= 0`, so an exactly-flat
    trade is NOT a loss. The sim must agree or the two counters drift on a break-even exit."""
    account = SimAccount(fee_pct=Decimal("0"), slippage_pct=Decimal("0"))
    config = _streak_config(threshold=2)

    account.record_trade_outcome(Decimal("-10"), config, JAN15, is_dca=False)
    account.record_trade_outcome(Decimal("0"), config, JAN15, is_dca=False)

    assert account.consecutive_losses == 0
    assert account.streak_halt_until == 0


def test_threshold_of_zero_is_completely_inert():
    """The SHIPPED default. However long the losing streak, the sim must never halt."""
    account = SimAccount(fee_pct=Decimal("0"), slippage_pct=Decimal("0"))
    config = _streak_config(threshold=0, cooloff_days=7)

    for _ in range(20):
        account.record_trade_outcome(Decimal("-10"), config, JAN15, is_dca=False)

    assert account.consecutive_losses == 20
    assert account.streak_halt_until == 0


def test_dca_losses_are_exempt_from_the_streak():
    """DCA is exempt from the STREAK (§12.6), matching `execution.streak.record_closed_trade`:
    it is designed to buy through drawdowns on a fixed budget."""
    account = SimAccount(fee_pct=Decimal("0"), slippage_pct=Decimal("0"))
    config = _streak_config(threshold=2)

    account.record_trade_outcome(Decimal("-10"), config, JAN15, is_dca=True)
    account.record_trade_outcome(Decimal("-10"), config, JAN15, is_dca=True)

    assert account.consecutive_losses == 0
    assert account.streak_halt_until == 0


def test_dca_win_does_not_reset_a_live_rule_streak():
    """Exempt means invisible in BOTH directions -- a DCA lot's profit must not silently clear a
    rule-trade losing streak, or the breaker could never trip on an account also running DCA."""
    account = SimAccount(fee_pct=Decimal("0"), slippage_pct=Decimal("0"))
    config = _streak_config(threshold=2)

    account.record_trade_outcome(Decimal("-10"), config, JAN15, is_dca=False)
    account.record_trade_outcome(Decimal("100"), config, JAN15, is_dca=True)

    assert account.consecutive_losses == 1


# -- concurrent RULE slots on one asset ----------------------------------------


def test_two_rules_hold_concurrent_positions_in_the_same_asset():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), 0)

    acc.open(
        _intent(asset="BTC", qty=Decimal("1"), notional=Decimal("1000")),
        Decimal("1000"), 0, slot="turtle_s1",
    )
    acc.open(
        _intent(asset="BTC", qty=Decimal("2"), notional=Decimal("2000")),
        Decimal("1000"), 0, slot="turtle_s2",
    )

    assert acc.position("BTC", "turtle_s1").qty == Decimal("1")
    assert acc.position("BTC", "turtle_s2").qty == Decimal("2")
    assert len(acc.positions_for("BTC")) == 2


def test_closing_one_slot_leaves_the_other_open():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), 0)
    acc.open(
        _intent(asset="BTC", qty=Decimal("1"), notional=Decimal("1000")),
        Decimal("1000"), 0, slot="a",
    )
    acc.open(
        _intent(asset="BTC", qty=Decimal("2"), notional=Decimal("2000")),
        Decimal("1000"), 0, slot="b",
    )

    acc.close("BTC", Decimal("1100"), 100, slot="a")

    assert acc.position("BTC", "a") is None
    assert acc.position("BTC", "b") is not None
    assert len(acc.positions_for("BTC")) == 1


def test_concurrent_slots_BOTH_count_toward_the_per_asset_cap():
    """⚠️ The rail-weakening risk of this change, pinned.

    `_position_notional` used to be keyed by asset alone, so a second concurrent position would
    have REPLACED the first's notional and the per-asset concentration cap would have seen only
    the newer one. Two $2k positions must read as $4k of exposure, not $2k.
    """
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), 0)
    acc.open(
        _intent(asset="BTC", qty=Decimal("2"), notional=Decimal("2000")),
        Decimal("1000"), 0, slot="a",
    )
    acc.open(
        _intent(asset="BTC", qty=Decimal("2"), notional=Decimal("2000")),
        Decimal("1000"), 0, slot="b",
    )

    assert acc._asset_notional("BTC") == Decimal("4000")
    assert acc._total_notional() == Decimal("4000")


def test_a_second_slot_is_refused_when_it_would_breach_the_per_asset_cap():
    """End-to-end: the cap must bind across slots, not per slot."""
    # Everything else roomy so the PER-ASSET cap is the binding constraint under test.
    config = _config(
        max_exposure_usd=Decimal("10000"),
        max_per_asset_pct=Decimal("0.30"),
        assumed_free_volume_usd=Decimal("1000000"),
    )
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), 0)

    # 30% of 10000 = 3000 of per-asset headroom.
    acc.open(
        _intent(asset="BTC", qty=Decimal("2"), notional=Decimal("2500")),
        Decimal("1250"), 0, slot="a",
    )
    headroom = acc.max_affordable_notional("BTC", config, 0)
    assert headroom == Decimal("500")


def test_the_default_slot_preserves_single_position_behaviour():
    acc = SimAccount(Decimal("0"), Decimal("0"))
    acc.deposit(Decimal("100000"), 0)
    acc.open(_intent(asset="BTC", qty=Decimal("1"), notional=Decimal("1000")), Decimal("1000"), 0)
    acc.open(_intent(asset="BTC", qty=Decimal("3"), notional=Decimal("3000")), Decimal("1000"), 0)
    # No slot given -> the second open REPLACES the first, exactly as before.
    assert len(acc.positions_for("BTC")) == 1
    assert acc.position("BTC").qty == Decimal("3")
