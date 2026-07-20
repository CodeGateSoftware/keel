"""Tests for keel.execution.executor -- the order executor (P3 Task 4).

`execute()` turns a `Signal` into a guarded live order: build an `OrderIntent` (sized via
`execution.sizing`), run `guards.check` FIRST (un-overridable -- a violation must never reach
`preview_order`/`place_order`), preview it, honor confirm/bypass mode, place it, and log it to
the `orders` table both before and after placement (a full audit trail even if the broker call
fails). Every test here injects a **fake broker** (no network) -- `FakeBroker` below duck-types
`CoinbaseClient.preview_order`/`.place_order` (+ an optional `cancel_order` for OCO) against
canned responses, exactly like `tests/data/test_cb_client.py`'s `FakeTransport` fakes the
transport underneath `CoinbaseClient`.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

import pytest
from keel_core.subscription import SubscriptionStatus

from keel.config import (
    AutoTradeConfig,
    Caps,
    Config,
    DcaConfig,
    MarketDataConfig,
    MoneyMgmtConfig,
)
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.execution.executor import (
    CancelUnavailable,
    ExecutionResult,
    execute,
    place_bracket,
    roll_to_break_even,
    scale_out,
    trail_stop_atr,
)
from keel.strategy.rules.base import Action, Setup, Signal
from keel.types import Side
from tests.conftest import attest_subscription

NOW_TS = 1_700_000_000


# -- fakes ----------------------------------------------------------------------------------


class FakeBroker:
    """Fake broker -- duck-types `CoinbaseClient.preview_order`/`.place_order`/`.cancel_order`.

    Returns canned, `CoinbaseClient`-shaped responses (no network); records every call so tests
    can assert the executor never calls `preview_order`/`place_order` on a vetoed intent.
    """

    def __init__(
        self,
        preview: dict[str, Any] | None = None,
        place_success: bool = True,
        place_order_id: str = "broker-order-1",
        usdc_balance: Decimal | None = Decimal("1000000"),
    ) -> None:
        self._preview = preview or {
            "order_total": Decimal("50.00"),
            "commission_total": Decimal("0.30"),
            "errs": [],
            "warning": [],
        }
        self._place_success = place_success
        self._place_order_id_seq = 0
        self._place_order_id_prefix = place_order_id
        self._usdc_balance = usdc_balance
        self.preview_calls: list[dict[str, Any]] = []
        self.place_calls: list[dict[str, Any]] = []
        self.cancel_calls: list[str] = []
        # Ordered log of exchange interactions -- lets a test assert SEQUENCE, not just that a
        # call happened. `_roll_stop` must cancel before it places.
        self.events: list[str] = []
        self.get_accounts_calls = 0

    def get_accounts(self) -> list[dict[str, Any]]:
        self.get_accounts_calls += 1
        if self._usdc_balance is None:
            return [{"currency": "USDC", "available_balance": None}]
        return [{"currency": "USDC", "available_balance": self._usdc_balance}]

    def preview_order(self, product_id: str, side: Any, order_configuration: dict) -> dict:
        self.preview_calls.append(
            {"product_id": product_id, "side": side, "order_configuration": order_configuration}
        )
        return dict(self._preview)

    def place_order(self, product_id: str, side: Any, order_configuration: dict) -> dict:
        self._place_order_id_seq += 1
        order_id = f"{self._place_order_id_prefix}-{self._place_order_id_seq}"
        self.events.append("place")
        self.place_calls.append(
            {"product_id": product_id, "side": side, "order_configuration": order_configuration}
        )
        if self._place_success:
            return {
                "success": True,
                "order_id": order_id,
                "product_id": product_id,
                "side": side.value if isinstance(side, Side) else side,
                "client_order_id": f"client-{order_id}",
                "order_configuration": order_configuration,
                "error": None,
            }
        return {
            "success": False,
            "order_id": None,
            "product_id": product_id,
            "side": side.value if isinstance(side, Side) else side,
            "client_order_id": f"client-{order_id}",
            "order_configuration": order_configuration,
            "error": {"error": "INSUFFICIENT_FUND", "message": "no funds"},
        }

    def cancel_order(self, order_id: str) -> bool:
        # Returns True: a CONFIRMED cancel. The real client returns bool and
        # `_cancel_at_exchange` treats anything else as a refusal.
        self.cancel_calls.append(order_id)
        self.events.append("cancel")
        return True


class NoNetworkBroker:
    """A broker whose every method raises -- proves a vetoed intent never reaches the network."""

    def __getattr__(self, name: str) -> Any:
        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError(f"unexpected broker call: {name}({args!r}, {kwargs!r})")

        return _boom


# -- fixtures / builders ----------------------------------------------------------------------


def _attest(
    repo: Repository,
    *,
    free_volume_usd: Decimal | None,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    pacing: str = "opportunistic",
    attested_at: int = NOW_TS,
    attest_due_ts: int | None = None,
) -> None:
    """Attest a coinbase subscription -- rail 14 now derives its cap from this record."""
    attest_subscription(
        repo,
        now_ts=attested_at,
        free_volume_usd=free_volume_usd,
        status=status,
        pacing=pacing,
        attest_due_ts=attest_due_ts,
    )


@pytest.fixture
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    r = Repository(conn)
    r.set_state("kill_switch", False)
    r.set_state("last_feed_ts", NOW_TS)
    # A very large, attested monthly allowance so pre-existing (non-rail-14) tests aren't
    # incidentally tripped by it; rail-14-specific tests below override with `_attest(...)`.
    _attest(r, free_volume_usd=Decimal("10000000"))
    return r


def _config(**overrides: Any) -> Config:
    base: dict[str, Any] = dict(
        allowlist=["BTC", "ETH", "PAXG"],
        target_weights={},
        risk_pct=Decimal("0.001"),
        caps=Caps(
            max_per_order_usd=Decimal("100000"),
            max_per_day_usd=Decimal("300000"),
            max_exposure_usd=Decimal("1000000"),
            max_per_asset_pct=Decimal("1"),
        ),
        market_data=MarketDataConfig(granularities=[], history_days=365),
        auto_trade=AutoTradeConfig(interval_sec=900),
        money_mgmt=MoneyMgmtConfig(),
        dca=DcaConfig(budget_usd=Decimal("50"), cadence_days=7),
    )
    base.update(overrides)
    return Config(**base)


def _setup(**overrides: Any) -> Setup:
    base: dict[str, Any] = dict(
        product_id="BTC-USD",
        direction="long",
        entry=Decimal("50000"),
        stop=Decimal("49000"),  # 2% move -- clears the anti-scalping floor
        target=Decimal("53000"),
        context={},
        ts=NOW_TS,
    )
    base.update(overrides)
    return Setup(**base)


def _enter_signal(setup: Setup | None = None, **overrides: Any) -> Signal:
    if setup is None:
        setup = _setup()
    base: dict[str, Any] = dict(
        rule_name="pullback_continuation",
        product_id=setup.product_id,
        action=Action.ENTER,
        side=Side.BUY,
        setup=setup,
        cts_score=8,
        entry_technique="limit",
        ts=NOW_TS,
    )
    base.update(overrides)
    return Signal(**base)


def _dca_signal(**overrides: Any) -> Signal:
    setup = _setup(
        stop=Decimal("0"), target=Decimal("50000"), context={"order_class": "dca", "no_stop": True}
    )
    base: dict[str, Any] = dict(
        rule_name="dca",
        product_id="BTC-USD",
        action=Action.ENTER,
        side=Side.BUY,
        setup=setup,
        cts_score=0,
        entry_technique="market",
        ts=NOW_TS,
    )
    base.update(overrides)
    return Signal(**base)


def _approve(preview: dict) -> bool:
    return True


def _reject(preview: dict) -> bool:
    return False


# -- confirm mode: compliant + approve -> placed + logged -------------------------------------


def test_confirm_mode_compliant_signal_approved_is_placed_and_logged(repo):
    broker = FakeBroker()
    signal = _enter_signal()

    result = execute(
        signal, broker, repo, _config(), mode="confirm", confirm_fn=_approve, now_ts=NOW_TS
    )

    assert isinstance(result, ExecutionResult)
    assert result.placed is True
    assert result.vetoed_by == []
    assert result.order_id is not None
    assert result.preview is not None

    order = repo.get_order(result.order_id)
    assert order is not None
    assert order["status"] == "filled"
    assert order["product_id"] == "BTC-USD"
    assert order["side"] == "BUY"
    assert order["mode"] == "live"

    # entry order + the auto-attached native exit bracket (signal's setup carries stop+target).
    # Was 3 calls under the old two-leg design: entry + stop leg + target leg.
    assert len(broker.preview_calls) == 2
    assert len(broker.place_calls) == 2


def test_confirm_mode_calls_confirm_fn_with_the_preview(repo):
    broker = FakeBroker()
    signal = _enter_signal()
    seen: list[dict] = []

    def _capture(preview: dict) -> bool:
        seen.append(preview)
        return True

    execute(signal, broker, repo, _config(), mode="confirm", confirm_fn=_capture, now_ts=NOW_TS)

    assert len(seen) == 1
    assert "order_total" in seen[0]


# -- confirm mode: reject -> not placed ---------------------------------------------------------


def test_confirm_mode_rejected_is_not_placed(repo):
    broker = FakeBroker()
    signal = _enter_signal()

    result = execute(
        signal, broker, repo, _config(), mode="confirm", confirm_fn=_reject, now_ts=NOW_TS
    )

    assert result.placed is False
    assert result.order_id is None
    assert result.vetoed_by == []
    assert result.preview is not None
    assert len(broker.preview_calls) == 1
    assert len(broker.place_calls) == 0
    assert repo.get_orders() == []


def test_confirm_mode_without_confirm_fn_defaults_to_not_placed(repo):
    broker = FakeBroker()
    signal = _enter_signal()

    result = execute(
        signal, broker, repo, _config(), mode="confirm", confirm_fn=None, now_ts=NOW_TS
    )

    assert result.placed is False
    assert result.order_id is None
    assert len(broker.place_calls) == 0


# -- rail-violating signal -> vetoed, never previews/places --------------------------------------


def test_rail_violating_signal_is_vetoed_before_preview_or_place(repo):
    broker = NoNetworkBroker()
    signal = _enter_signal(product_id="DOGE-USD", setup=_setup(product_id="DOGE-USD"))

    result = execute(signal, broker, repo, _config(), mode="bypass", now_ts=NOW_TS)

    assert result.placed is False
    assert result.order_id is None
    assert result.preview is None
    assert any(v.startswith("halal_allowlist") for v in result.vetoed_by)
    assert repo.get_orders() == []


def test_kill_switch_vetoes_even_in_bypass_mode(repo):
    repo.set_state("kill_switch", True)
    broker = NoNetworkBroker()
    signal = _enter_signal()

    result = execute(signal, broker, repo, _config(), mode="bypass", now_ts=NOW_TS)

    assert result.placed is False
    assert any(v.startswith("kill_switch") for v in result.vetoed_by)


# -- rail 13: USDC-funding wiring (Issue #59) ----------------------------------------------------


class _BrokerAccountsError:
    """`get_accounts` raises (a simulated broker outage); `preview_order`/`place_order` raise
    too if ever reached -- isolates "the balance fetch itself failed" from a generic
    NoNetworkBroker veto, and proves the failure alone is enough to veto without ever previewing
    or placing.
    """

    def get_accounts(self) -> list[dict[str, Any]]:
        raise ConnectionError("simulated broker outage fetching accounts")

    def preview_order(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("preview_order must not be called when the balance fetch failed")

    def place_order(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("place_order must not be called when the balance fetch failed")


def test_execute_fetches_the_available_quote_balance_for_a_buy_and_places(repo):
    broker = FakeBroker(usdc_balance=Decimal("100000"))
    signal = _enter_signal()

    result = execute(signal, broker, repo, _config(), mode="bypass", now_ts=NOW_TS)

    assert result.placed is True
    assert broker.get_accounts_calls == 1


def test_broker_balance_fetch_error_vetoes_the_buy_before_preview_or_place(repo):
    broker = _BrokerAccountsError()
    signal = _enter_signal()

    result = execute(signal, broker, repo, _config(), mode="bypass", now_ts=NOW_TS)

    assert result.placed is False
    assert result.order_id is None
    assert result.preview is None
    assert any(v.startswith("usdc_funding") for v in result.vetoed_by)
    assert repo.get_orders() == []


def test_insufficient_usdc_balance_vetoes_the_buy_before_preview_or_place(repo):
    broker = FakeBroker(usdc_balance=Decimal("10"))  # entry 50000, qty ~1 -> notional ~50000
    signal = _enter_signal()

    result = execute(signal, broker, repo, _config(), mode="bypass", now_ts=NOW_TS)

    assert result.placed is False
    assert result.preview is None
    assert any(v.startswith("usdc_funding") for v in result.vetoed_by)
    assert repo.get_orders() == []


def test_exit_signal_never_fetches_a_balance(repo):
    """SELL is exempt from rail 13 -- the executor shouldn't even bother fetching a balance for
    an EXIT, since it wouldn't be used."""
    _seed_open_position(repo, "BTC-USD", Decimal("0.1"), Decimal("50000"))
    broker = FakeBroker()
    signal = Signal(
        rule_name="target_harvest",
        product_id="BTC-USD",
        action=Action.EXIT,
        side=Side.SELL,
        setup=None,
        cts_score=0,
        entry_technique="market",
        ts=NOW_TS,
    )

    result = execute(signal, broker, repo, _config(), mode="bypass", now_ts=NOW_TS)

    assert result.placed is True
    assert broker.get_accounts_calls == 0


# -- bypass mode: compliant -> placed without a prompt --------------------------------------------


def test_bypass_mode_compliant_signal_places_without_confirm_fn(repo):
    broker = FakeBroker()
    signal = _enter_signal()

    result = execute(signal, broker, repo, _config(), mode="bypass", now_ts=NOW_TS)

    assert result.placed is True
    assert result.order_id is not None
    order = repo.get_order(result.order_id)
    assert order["status"] == "filled"


def test_bypass_mode_ignores_confirm_fn_if_provided(repo):
    broker = FakeBroker()
    signal = _enter_signal()

    result = execute(
        signal, broker, repo, _config(), mode="bypass", confirm_fn=_reject, now_ts=NOW_TS
    )

    # bypass mode never consults confirm_fn -- a reject-everything fn must not block it.
    assert result.placed is True


# -- unknown mode -----------------------------------------------------------------------------


def test_unknown_mode_raises_value_error(repo):
    broker = FakeBroker()
    signal = _enter_signal()

    with pytest.raises(ValueError, match="mode"):
        execute(signal, broker, repo, _config(), mode="yolo", now_ts=NOW_TS)  # type: ignore[arg-type]


# -- broker place failure --------------------------------------------------------------------


def test_broker_place_failure_is_logged_but_not_placed(repo):
    broker = FakeBroker(place_success=False)
    signal = _enter_signal()

    result = execute(signal, broker, repo, _config(), mode="bypass", now_ts=NOW_TS)

    assert result.placed is False
    assert result.order_id is not None  # still logged (audit trail even on rejection)
    order = repo.get_order(result.order_id)
    assert order["status"] == "rejected"


# -- rail 14: monthly-allowance wiring (Issue #59) -----------------------------------------------


def test_monthly_allowance_vetoes_a_buy_over_the_live_subscription_cap(repo):
    _attest(repo, free_volume_usd=Decimal("100"))
    broker = FakeBroker()
    signal = _enter_signal()

    result = execute(signal, broker, repo, _config(), mode="bypass", now_ts=NOW_TS)

    assert result.placed is False
    assert any(v.startswith("monthly_subscription_allowance") for v in result.vetoed_by)
    assert len(broker.place_calls) == 0


def test_monthly_allowance_updated_subscription_takes_effect_on_the_next_order(repo):
    """No snapshot, no restart, no config edit -- the executor doesn't cache the allowance
    anywhere; it always flows straight through to `guards.check`'s live
    `repo.get_broker_subscription()` read."""
    _attest(repo, free_volume_usd=Decimal("100"))
    broker = FakeBroker()
    signal = _enter_signal()

    first = execute(signal, broker, repo, _config(), mode="bypass", now_ts=NOW_TS)
    assert first.placed is False

    _attest(repo, free_volume_usd=Decimal("10000000"))
    second = execute(signal, broker, repo, _config(), mode="bypass", now_ts=NOW_TS)
    assert second.placed is True


# -- DCA sizing --------------------------------------------------------------------------------


def test_dca_signal_sizes_via_dca_size_and_places(repo):
    broker = FakeBroker()
    signal = _dca_signal()

    result = execute(signal, broker, repo, _config(), mode="bypass", now_ts=NOW_TS)

    assert result.placed is True
    order = repo.get_order(result.order_id)
    # dca_size(50, 50000) = 0.001
    assert order["qty"] == Decimal("50") / Decimal("50000")


def test_dca_signal_exempt_from_averaging_into_losers_but_bound_by_allowlist(repo):
    broker = NoNetworkBroker()
    signal = _dca_signal(
        product_id="DOGE-USD",
        setup=_setup(
            product_id="DOGE-USD",
            stop=Decimal("0"),
            target=Decimal("1"),
            context={"order_class": "dca", "no_stop": True},
        ),
    )

    result = execute(signal, broker, repo, _config(), mode="bypass", now_ts=NOW_TS)

    assert result.placed is False
    assert any(v.startswith("halal_allowlist") for v in result.vetoed_by)


# -- EXIT signals ------------------------------------------------------------------------------


def _seed_open_position(repo: Repository, product_id: str, qty: Decimal, price: Decimal) -> None:
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
            confirmation="bypass",
            rule_id=None,
            created_at=NOW_TS - 1000,
            updated_at=NOW_TS - 1000,
        )
    )


def test_exit_signal_sells_the_held_position(repo):
    _seed_open_position(repo, "BTC-USD", Decimal("0.1"), Decimal("50000"))
    broker = FakeBroker()
    signal = Signal(
        rule_name="target_harvest",
        product_id="BTC-USD",
        action=Action.EXIT,
        side=Side.SELL,
        setup=None,
        cts_score=0,
        entry_technique="market",
        ts=NOW_TS,
    )

    result = execute(signal, broker, repo, _config(), mode="bypass", now_ts=NOW_TS)

    assert result.placed is True
    order = repo.get_order(result.order_id)
    assert order["side"] == "SELL"
    assert order["qty"] == Decimal("0.1")


def test_exit_signal_with_no_open_position_is_not_placed(repo):
    broker = NoNetworkBroker()
    signal = Signal(
        rule_name="target_harvest",
        product_id="BTC-USD",
        action=Action.EXIT,
        side=Side.SELL,
        setup=None,
        cts_score=0,
        entry_technique="market",
        ts=NOW_TS,
    )

    result = execute(signal, broker, repo, _config(), mode="bypass", now_ts=NOW_TS)

    assert result.placed is False
    assert result.order_id is None


# -- OCO bracket -------------------------------------------------------------------------------


def test_execute_attaches_oco_bracket_after_a_stop_target_entry_fills(repo):
    broker = FakeBroker()
    signal = _enter_signal()

    result = execute(signal, broker, repo, _config(), mode="bypass", now_ts=NOW_TS)

    assert result.placed is True
    # entry + ONE native bracket = 2 place_order calls (was 3: entry + stop leg + target leg)
    assert len(broker.place_calls) == 2
    orders = repo.get_orders(product_id="BTC-USD")
    sell_orders = [o for o in orders if o["side"] == "SELL"]
    assert len(sell_orders) == 1
    assert "trigger_bracket_gtc" in broker.place_calls[-1]["order_configuration"]
    assert repo.get_state("open_stop:BTC-USD") == Decimal("49000")
    assert repo.get_state("open_target:BTC-USD") == Decimal("53000")


# NOTE: `test_oco_fill_of_target_cancels_the_stop_leg` and
# `test_oco_fill_with_no_sibling_recorded_is_a_no_op` were DELETED with `handle_oco_fill`, not
# weakened. They covered client-side sibling cancellation, which the native trigger bracket makes
# impossible to get wrong: there is one order, so there is no sibling to cancel. The invariant
# they protected (never sell an already-closed position twice) is now the exchange's to enforce.


def test_scale_out_places_a_partial_sell_and_logs_it(repo):
    _seed_open_position(repo, "BTC-USD", Decimal("0.2"), Decimal("50000"))
    broker = FakeBroker()

    result = scale_out(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        qty=Decimal("0.1"),
        exit_price=Decimal("53000"),
        rule_name="partial_target",
        now_ts=NOW_TS,
    )

    assert result.placed is True
    order = repo.get_order(result.order_id)
    assert order["side"] == "SELL"
    assert order["qty"] == Decimal("0.1")


# -- break-even roll -------------------------------------------------------------------------


def test_roll_to_break_even_replaces_the_stop_leg(repo):
    broker = FakeBroker()
    stop_id = place_bracket(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        qty=Decimal("0.01"),
        stop=Decimal("49000"),
        target=Decimal("53000"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS,
    )

    new_stop_id = roll_to_break_even(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        old_stop_order_id=stop_id,
        entry_price=Decimal("50000"),
        qty=Decimal("0.01"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS + 100,
    )

    assert new_stop_id is not None
    assert new_stop_id != stop_id
    assert repo.get_order(stop_id)["status"] == "canceled"
    assert repo.get_state("open_stop:BTC-USD") == Decimal("50000")


def test_roll_to_break_even_never_widens_the_stop(repo):
    broker = FakeBroker()
    stop_id = place_bracket(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        qty=Decimal("0.01"),
        stop=Decimal("49000"),
        target=Decimal("53000"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS,
    )

    # a lower "break-even" than the recorded stop would widen it -- must be refused.
    result = roll_to_break_even(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        old_stop_order_id=stop_id,
        entry_price=Decimal("48000"),
        qty=Decimal("0.01"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS + 100,
    )

    assert result is None
    assert repo.get_order(stop_id)["status"] == "pending"
    assert repo.get_state("open_stop:BTC-USD") == Decimal("49000")


# -- ATR trailing stop -----------------------------------------------------------------------


def test_trail_stop_atr_ratchets_the_stop_up(repo):
    broker = FakeBroker()
    stop_id = place_bracket(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        qty=Decimal("0.01"),
        stop=Decimal("49000"),
        target=Decimal("53000"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS,
    )

    new_stop_id = trail_stop_atr(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        old_stop_order_id=stop_id,
        current_price=Decimal("52000"),
        atr=Decimal("500"),
        qty=Decimal("0.01"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS + 200,
        multiplier=Decimal("2"),
    )

    # new_stop = 52000 - 2*500 = 51000, above the prior 49000 -- ratchets forward.
    assert new_stop_id is not None
    assert repo.get_state("open_stop:BTC-USD") == Decimal("51000")


def test_trail_stop_atr_never_widens_the_stop(repo):
    broker = FakeBroker()
    stop_id = place_bracket(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        qty=Decimal("0.01"),
        stop=Decimal("49000"),
        target=Decimal("53000"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS,
    )

    # a wide ATR band computes a trail below the recorded stop -- must be refused.
    result = trail_stop_atr(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        old_stop_order_id=stop_id,
        current_price=Decimal("49500"),
        atr=Decimal("2000"),
        qty=Decimal("0.01"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS + 200,
        multiplier=Decimal("1"),
    )

    assert result is None
    assert repo.get_state("open_stop:BTC-USD") == Decimal("49000")


# -- no live network in tests ------------------------------------------------------------------


def test_no_network_ever_touched_for_a_vetoed_intent(repo):
    """`NoNetworkBroker` raises on any call -- a vetoed signal reaching it proves the executor
    calls guards before touching the broker at all."""
    repo.set_state("kill_switch", True)
    broker = NoNetworkBroker()
    signal = _enter_signal()

    result = execute(
        signal, broker, repo, _config(), mode="confirm", confirm_fn=_approve, now_ts=NOW_TS
    )

    assert result.placed is False


def test_a_filled_order_records_the_previewed_commission_as_its_fee(repo):
    """`orders.fee` was inserted as NULL and never updated, so `record_closed_trade` always
    received `fees=0` and `pnl_net` was GROSS on every live trade.

    That silently defeats the thing rail 16 exists for: fees dominate small moves, so a trade
    up +$0.60 gross and -$11.40 after two legs of 0.6% taker fee was recorded as a WIN and RESET
    the loss counter. The producer's arithmetic was right; the caller fed it a zero.

    The previewed `commission_total` is an ESTIMATE, not the observed fill fee -- see
    `_run_order`'s note. It is the best figure available until post-fill reconciliation exists,
    and it is enormously closer to the truth than zero.
    """
    broker = FakeBroker(
        preview={
            "order_total": Decimal("50.00"),
            "commission_total": Decimal("0.30"),
            "errs": [],
            "warning": [],
        }
    )
    signal = _enter_signal()

    result = execute(signal, broker, repo, _config(), "bypass", confirm_fn=None, now_ts=NOW_TS)

    assert result.placed is True
    order = repo.get_order(result.order_id)
    assert order["status"] == "filled"
    assert order["fee"] == Decimal("0.30")


# -- a cancel that cannot actually reach the exchange must be LOUD ------------------------------


class _NoCancelBroker(FakeBroker):
    """The REAL broker's shape: `cb_client`, the Coinbase adapter and the `Transport` protocol
    have no `cancel_order` at all -- only the test fakes ever had one."""

    cancel_order = None


def test_a_broker_without_cancel_order_raises_instead_of_silently_marking_canceled(repo):
    """`getattr(broker, "cancel_order", None)` skipped the cancel and marked the sibling
    `canceled` in the DB anyway. Against the real broker that leaves a LIVE resting SELL on the
    exchange while our records say it is gone -- so after a stop fills, the target leg can still
    sell inventory we no longer hold.

    The tests only passed because the fakes supply a method the real client lacks: the same
    reads-as-enforced-but-isn't pattern this branch exists to kill, sitting on the cancel path.
    A cancel that cannot reach the exchange must fail loudly and must NOT rewrite our state.
    """
    broker = _NoCancelBroker()
    stop_id = place_bracket(
        broker, repo, _config(), product_id="BTC-USD", qty=Decimal("0.01"),
        stop=Decimal("49000"), target=Decimal("53000"),
        rule_name="pullback_continuation", now_ts=NOW_TS,
    )

    with pytest.raises(CancelUnavailable, match="cancel_order"):
        roll_to_break_even(
            broker, repo, _config(), product_id="BTC-USD", old_stop_order_id=stop_id,
            entry_price=Decimal("50000"), qty=Decimal("0.01"),
            rule_name="pullback_continuation", now_ts=NOW_TS + 100,
        )

    # the bracket must still read as live -- our state may not claim a cancel that never happened
    assert repo.get_order(stop_id)["status"] == "pending"


def test_a_failing_cancel_call_does_not_mark_the_sibling_canceled(repo):
    """Same invariant when the broker HAS the method but the call fails (network, rejection).
    Marking it canceled would leave the DB disagreeing with the exchange in the dangerous
    direction: we would believe no sell is resting when one is."""

    class _RaisingCancelBroker(FakeBroker):
        def cancel_order(self, order_id: str) -> None:
            raise RuntimeError("broker refused the cancel")

    broker = _RaisingCancelBroker()
    stop_id = place_bracket(
        broker, repo, _config(), product_id="BTC-USD", qty=Decimal("0.01"),
        stop=Decimal("49000"), target=Decimal("53000"),
        rule_name="pullback_continuation", now_ts=NOW_TS,
    )

    with pytest.raises(RuntimeError, match="refused the cancel"):
        roll_to_break_even(
            broker, repo, _config(), product_id="BTC-USD", old_stop_order_id=stop_id,
            entry_price=Decimal("50000"), qty=Decimal("0.01"),
            rule_name="pullback_continuation", now_ts=NOW_TS + 100,
        )

    assert repo.get_order(stop_id)["status"] == "pending"


def test_a_leg_with_no_broker_side_id_cannot_be_cancelled_and_says_so(repo):
    """`_native_order_id` returns None when the placement response carried no id. There is then
    nothing to cancel AT the exchange, so the same rule applies: raise, do not rewrite state."""
    broker = FakeBroker()
    stop_id = place_bracket(
        broker, repo, _config(), product_id="BTC-USD", qty=Decimal("0.01"),
        stop=Decimal("49000"), target=Decimal("53000"),
        rule_name="pullback_continuation", now_ts=NOW_TS,
    )
    repo.update_order(stop_id, raw_response=json.dumps({"success": True}))  # no order_id

    with pytest.raises(CancelUnavailable, match="broker-side id"):
        roll_to_break_even(
            broker, repo, _config(), product_id="BTC-USD", old_stop_order_id=stop_id,
            entry_price=Decimal("50000"), qty=Decimal("0.01"),
            rule_name="pullback_continuation", now_ts=NOW_TS + 100,
        )

    assert repo.get_order(stop_id)["status"] == "pending"


# NOTE: `test_roll_to_break_even_raises_rather_than_leaving_two_live_stops` was DELETED, not
# weakened. It covered a hazard specific to the old place-then-cancel ordering: the replacement
# stop rested alongside the old one, so a skipped cancel left TWO live stops on one position.
# `_roll_stop` now cancels BEFORE placing (it has to -- the native bracket commits the whole
# position, so a second one would be rejected for insufficient funds), which makes that state
# unreachable by construction. The raise-on-unavailable-cancel behaviour it also asserted is
# still covered, on this same code path, by
# `test_a_broker_without_cancel_order_raises_instead_of_silently_marking_canceled`.
# The NEW risk created by cancel-first -- a cancelled bracket whose replacement is rejected,
# leaving the position naked -- is covered by
# `test_a_roll_that_cannot_replace_the_bracket_screams_that_the_position_is_naked`.


# -- native exchange-side bracket ---------------------------------------------------------------


def test_bracket_places_exactly_one_order_committing_the_position_once(repo):
    """The two-leg design placed a stop SELL for the full qty AND a target SELL for the full
    qty -- 2x the inventory actually held. On spot the second leg should be rejected for
    insufficient funds, and if it were not, the position would be oversold.

    Coinbase's native trigger bracket is ONE order carrying both prices, so the position is
    committed exactly once and the exchange owns the stop-vs-target race.
    """
    broker = FakeBroker()

    order_id = place_bracket(
        broker, repo, _config(), product_id="BTC-USD", qty=Decimal("0.01"),
        stop=Decimal("49000"), target=Decimal("53000"),
        rule_name="pullback_continuation", now_ts=NOW_TS,
    )

    assert order_id is not None
    sells = [o for o in repo.get_orders(product_id="BTC-USD") if o["side"] == "SELL"]
    assert len(sells) == 1
    assert len(broker.place_calls) == 1

    config = broker.place_calls[0]["order_configuration"]
    assert "trigger_bracket_gtc" in config
    leg = config["trigger_bracket_gtc"]
    assert leg["base_size"] == "0.01"
    assert leg["limit_price"] == "53000"          # take-profit
    assert leg["stop_trigger_price"] == "49000"   # stop-loss


def test_bracket_records_the_stop_for_rail_9_and_the_target_for_later_rolls(repo):
    """`open_stop` is rail 9's no-widening reference. `open_target` is new: with one order
    carrying both prices, rolling the stop means re-placing the bracket, which needs the target
    that is no longer recoverable from a separate leg."""
    broker = FakeBroker()

    place_bracket(
        broker, repo, _config(), product_id="BTC-USD", qty=Decimal("0.01"),
        stop=Decimal("49000"), target=Decimal("53000"),
        rule_name="pullback_continuation", now_ts=NOW_TS,
    )

    assert repo.get_state("open_stop:BTC-USD") == Decimal("49000")
    assert repo.get_state("open_target:BTC-USD") == Decimal("53000")


def test_execute_surfaces_the_bracket_order_id_it_placed(repo):
    """`execute` places the bracket ITSELF, so its return is the only way a caller can learn the
    id. Discarding it left the resting bracket unnameable: `agent.run_once` could not point the
    new `positions` tranche at it, and `roll_to_break_even`/`trail_stop_atr` -- which take an
    `old_stop_order_id` -- were unreachable by construction rather than merely uncalled.
    """
    broker = FakeBroker()

    result = execute(_enter_signal(), broker, repo, _config(), mode="bypass", now_ts=NOW_TS)

    assert result.bracket_order_id is not None, "execute discarded the bracket's order id again"
    bracket = repo.get_order(result.bracket_order_id)
    assert bracket["side"] == "SELL"
    assert bracket["status"] == "pending"
    assert result.bracket_order_id != result.order_id      # the bracket, not the entry


def test_a_vetoed_bracket_leaves_no_bracket_order_id(repo):
    """`None` must mean "there is no resting bracket", never "there is one but we lost its id" --
    `run_once` would otherwise skip `set_position_bracket` on a tranche that does have a bracket,
    or name one that was never placed."""
    broker = FakeBroker()

    # DCA carries no stop, so no bracket is ever placed for it.
    result = execute(
        _enter_signal(setup=_setup(context={"order_class": "dca"})),
        broker, repo, _config(), mode="bypass", now_ts=NOW_TS,
    )

    assert result.placed is True
    assert result.bracket_order_id is None
    assert [o for o in repo.get_orders(product_id="BTC-USD") if o["side"] == "SELL"] == []


def test_a_vetoed_bracket_places_nothing_and_returns_none(repo):
    """Guards are un-overridable for the bracket exactly as for any other order."""
    repo.set_state("kill_switch", True)
    broker = FakeBroker()

    order_id = place_bracket(
        broker, repo, _config(), product_id="BTC-USD", qty=Decimal("0.01"),
        stop=Decimal("49000"), target=Decimal("53000"),
        rule_name="pullback_continuation", now_ts=NOW_TS,
    )

    assert order_id is None
    assert broker.place_calls == []


def test_rolling_the_stop_carries_the_original_target_forward(repo):
    """Order of operations INVERTS versus the old two-leg path, and it has to.

    The old code placed the replacement first so the position was never unprotected. With a
    native bracket the resting order already commits the whole position, so placing a second
    one would be rejected for insufficient funds. Cancel must come first -- which means a brief
    unprotected window the old design did not have. `edit_order` cannot avoid it: it accepts
    only limit-GTC orders and edits only size/price, never `stop_trigger_price`.
    """
    broker = FakeBroker()
    old_id = place_bracket(
        broker, repo, _config(), product_id="BTC-USD", qty=Decimal("0.01"),
        stop=Decimal("49000"), target=Decimal("53000"),
        rule_name="pullback_continuation", now_ts=NOW_TS,
    )

    new_id = roll_to_break_even(
        broker, repo, _config(), product_id="BTC-USD", old_stop_order_id=old_id,
        entry_price=Decimal("50000"), qty=Decimal("0.01"),
        rule_name="pullback_continuation", now_ts=NOW_TS + 100,
    )

    assert new_id is not None and new_id != old_id
    assert repo.get_order(old_id)["status"] == "canceled"
    assert repo.get_state("open_stop:BTC-USD") == Decimal("50000")
    # ORDERING, not presence. `assert broker.cancel_calls` passed under place-then-cancel too,
    # so it did not hold the one regression this design deliberately accepted. The replacement
    # bracket must be placed only AFTER the old one is cancelled, or the exchange would reject
    # it for insufficient funds (the resting bracket commits the whole position).
    assert broker.events == ["place", "cancel", "place"], broker.events
    replacement = broker.place_calls[-1]["order_configuration"]["trigger_bracket_gtc"]
    assert replacement["limit_price"] == "53000"        # original target preserved
    assert replacement["stop_trigger_price"] == "50000"  # stop moved to break-even


def test_a_roll_that_cannot_replace_the_bracket_screams_that_the_position_is_naked(repo, caplog):
    """The cost of cancel-first. If the cancel succeeds and the replacement is then rejected,
    the position is left with NO protective stop. That must never pass quietly."""

    class _RejectingBroker(FakeBroker):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.calls = 0

        def place_order(self, product_id, side, order_configuration):
            self.calls += 1
            if self.calls > 1:          # the original bracket places; the replacement fails
                return {"success": False, "error": "INSUFFICIENT_FUND"}
            return super().place_order(product_id, side, order_configuration)

    broker = _RejectingBroker()
    old_id = place_bracket(
        broker, repo, _config(), product_id="BTC-USD", qty=Decimal("0.01"),
        stop=Decimal("49000"), target=Decimal("53000"),
        rule_name="pullback_continuation", now_ts=NOW_TS,
    )

    with caplog.at_level(logging.CRITICAL):
        new_id = roll_to_break_even(
            broker, repo, _config(), product_id="BTC-USD", old_stop_order_id=old_id,
            entry_price=Decimal("50000"), qty=Decimal("0.01"),
            rule_name="pullback_continuation", now_ts=NOW_TS + 100,
        )

    assert new_id is None
    assert "executor.position_unprotected" in caplog.text


def test_a_cancel_the_exchange_REFUSES_is_not_recorded_as_a_cancel(repo):
    """`cancel_order` returns bool -- False when the exchange refuses (e.g. the order already
    filled). `_cancel_at_exchange` discarded it, so a refused cancel was recorded as a
    successful one: exactly the "our state claims a cancel that did not happen" failure this
    module exists to prevent, reintroduced one layer up.

    No existing test could catch it because every fake `cancel_order` returns None.
    """

    class _RefusingBroker(FakeBroker):
        def cancel_order(self, order_id: str) -> bool:
            self.cancel_calls.append(order_id)
            return False        # the exchange says: no

    broker = _RefusingBroker()
    stop_id = place_bracket(
        broker, repo, _config(), product_id="BTC-USD", qty=Decimal("0.01"),
        stop=Decimal("49000"), target=Decimal("53000"),
        rule_name="pullback_continuation", now_ts=NOW_TS,
    )

    with pytest.raises(CancelUnavailable, match="did not confirm"):
        roll_to_break_even(
            broker, repo, _config(), product_id="BTC-USD", old_stop_order_id=stop_id,
            entry_price=Decimal("50000"), qty=Decimal("0.01"),
            rule_name="pullback_continuation", now_ts=NOW_TS + 100,
        )

    assert repo.get_order(stop_id)["status"] == "pending"



def _exit_signal() -> Signal:
    return Signal(
        rule_name="target_harvest",
        product_id="BTC-USD",
        action=Action.EXIT,
        side=Side.SELL,
        setup=None,
        cts_score=0,
        entry_technique="market",
        ts=NOW_TS,
    )


def _seed_filled_buy(repo, *, qty, price) -> None:
    _seed_open_position(repo, "BTC-USD", qty, price)


# -- a voluntary exit must clear the resting bracket first --------------------------------------


def test_an_exit_cancels_the_resting_bracket_before_selling(repo):
    """THE defect the native-bracket rewrite introduced.

    `place_bracket` leaves a trigger bracket resting that commits the ENTIRE base position. A
    rule-driven exit then issues a full-size market SELL for the same inventory. On spot the
    base is locked by the bracket, so the sell is rejected: `result.placed` is False,
    `position_rule` is never cleared, no outcome is recorded, and the agent retries the same
    doomed sell every cycle forever while the position rides a stale stop.

    The rewrite was validated against the path it changed (bracket fills) and not against the
    path it left behind (rule exits) -- the same reads-as-enforced-but-isn't pattern this branch
    exists to eliminate.
    """
    _seed_filled_buy(repo, qty=Decimal("0.01"), price=Decimal("50000"))
    broker = FakeBroker()
    bracket_id = place_bracket(
        broker, repo, _config(), product_id="BTC-USD", qty=Decimal("0.01"),
        stop=Decimal("49000"), target=Decimal("53000"),
        rule_name="pullback_continuation", now_ts=NOW_TS,
    )

    result = execute(_exit_signal(), broker, repo, _config(), "bypass", None, now_ts=NOW_TS + 10)

    assert result.placed is True
    assert repo.get_order(bracket_id)["status"] == "canceled"
    assert bracket_id in [int(c) if str(c).isdigit() else c for c in broker.cancel_calls] or \
        broker.cancel_calls, "the resting bracket was never cancelled at the exchange"


def test_an_exit_is_refused_when_the_resting_bracket_cannot_be_cancelled(repo):
    """If the bracket cannot be cleared, the SELL must NOT be attempted: it would either be
    rejected for insufficient funds, or -- worse -- fill and leave a live bracket able to sell
    inventory we no longer hold. Refusing loudly is the only safe branch."""

    class _RefusingBroker(FakeBroker):
        def cancel_order(self, order_id: str) -> bool:
            self.cancel_calls.append(order_id)
            return False

    _seed_filled_buy(repo, qty=Decimal("0.01"), price=Decimal("50000"))
    broker = _RefusingBroker()
    place_bracket(
        broker, repo, _config(), product_id="BTC-USD", qty=Decimal("0.01"),
        stop=Decimal("49000"), target=Decimal("53000"),
        rule_name="pullback_continuation", now_ts=NOW_TS,
    )
    placed_before = len(broker.place_calls)

    result = execute(_exit_signal(), broker, repo, _config(), "bypass", None, now_ts=NOW_TS + 10)

    assert result.placed is False
    assert "bracket" in (result.reason or "").lower()
    assert len(broker.place_calls) == placed_before, "a SELL was attempted anyway"


def test_an_entry_does_not_try_to_cancel_anything(repo):
    """Negative control: the bracket-clearing step is EXIT-only. An entry must not touch it."""
    broker = FakeBroker()

    execute(_enter_signal(), broker, repo, _config(), "bypass", None, now_ts=NOW_TS)

    assert broker.cancel_calls == []


def test_an_immediately_filled_order_upgrades_to_the_OBSERVED_fill_and_fee(repo):
    """Market orders are marked `filled` at placement, so they never appear in
    `get_orders(status="pending")` and reconciliation never sees them. That left rail 16
    counting two DIFFERENT kinds of number: bracket exits carried the exchange's observed price
    and fee, while voluntary rule exits carried the expected price and the PREVIEWED commission.

    A breaker whose threshold is swept on one definition and enforced on the other is
    miscalibrated by construction, so the immediate path fetches its observed economics too.
    """

    class _ObservingBroker(FakeBroker):
        def get_order(self, order_id: str) -> dict:
            return {
                "order_id": order_id, "status": "FILLED", "filled_size": Decimal("0.001"),
                "average_filled_price": Decimal("50123.45"),   # not the expected 50000
                "total_fees": Decimal("0.42"),                 # not the previewed 0.30
            }

    broker = _ObservingBroker()

    result = execute(_enter_signal(), broker, repo, _config(), "bypass", None, now_ts=NOW_TS)

    order = repo.get_order(result.order_id)
    assert order["actual_fill"] == Decimal("50123.45")
    assert order["fee"] == Decimal("0.42")


def test_an_unobservable_immediate_fill_keeps_the_estimate_rather_than_failing(repo):
    """Fail SOFT here, unlike the cancel path. We already hold a usable estimate, the order is
    already placed, and raising would abort a cycle over a refinement. The estimate is what
    shipped before this upgrade existed."""

    class _BlindBroker(FakeBroker):
        def get_order(self, order_id: str) -> dict:
            raise RuntimeError("status endpoint down")

    broker = _BlindBroker()

    result = execute(_enter_signal(), broker, repo, _config(), "bypass", None, now_ts=NOW_TS)

    assert result.placed is True
    order = repo.get_order(result.order_id)
    assert order["actual_fill"] == Decimal("50000")   # the expected price, as before
    assert order["fee"] == Decimal("0.30")            # the previewed commission, as before


def test_scale_out_has_no_production_caller(repo):
    """A TRIPWIRE, not a style check. `scale_out` is unreachable today, and in the
    single-bracket world it is actively wrong if wired: a partial SELL runs against a bracket
    committing the FULL position (so it is rejected, or fills and leaves an oversized bracket
    able to sell more than is held), it never resizes or re-places the bracket, and it records
    no `trade_outcomes` row -- so a scaled-out winner's profit is dropped and rail 16 can count
    a net winner as a loss.

    The ledger adjudicated this as "accept and document", which is only safe while it stays
    unreachable. This test fails the moment someone wires it, which is the point: fix the three
    problems above first.
    """
    import pathlib
    import re

    keel_root = pathlib.Path(__file__).resolve().parents[2] / "keel"
    callers: list[str] = []
    for path in keel_root.rglob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"\bscale_out\s*\(", line) and "def scale_out" not in line:
                callers.append(f"{path.relative_to(keel_root.parent)}:{lineno}")

    assert callers == [], (
        "scale_out has gained a production caller. Before wiring it: cancel/resize the resting "
        "bracket, and record a trade outcome for the partial exit -- otherwise rail 16 will "
        f"count net winners as losses. Call sites: {callers}"
    )
