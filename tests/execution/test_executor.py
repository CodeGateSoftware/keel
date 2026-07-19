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
    ExecutionResult,
    execute,
    handle_oco_fill,
    place_oco_bracket,
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

    def cancel_order(self, order_id: str) -> None:
        self.cancel_calls.append(order_id)


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

    # entry order + the auto-attached OCO stop/target legs (signal's setup carries both).
    assert len(broker.preview_calls) == 3
    assert len(broker.place_calls) == 3


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
    # entry + stop leg + target leg = 3 place_order calls
    assert len(broker.place_calls) == 3
    orders = repo.get_orders(product_id="BTC-USD")
    sell_orders = [o for o in orders if o["side"] == "SELL"]
    assert len(sell_orders) == 2
    assert repo.get_state("open_stop:BTC-USD") == Decimal("49000")


def test_oco_fill_of_target_cancels_the_stop_leg(repo):
    broker = FakeBroker()

    stop_id, target_id = place_oco_bracket(
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
    assert stop_id is not None
    assert target_id is not None

    cancelled = handle_oco_fill(broker, repo, target_id, now_ts=NOW_TS + 10)

    assert cancelled == stop_id
    target_order = repo.get_order(target_id)
    stop_order = repo.get_order(stop_id)
    assert target_order["status"] == "filled"
    assert stop_order["status"] == "canceled"
    assert len(broker.cancel_calls) == 1


def test_oco_fill_with_no_sibling_recorded_is_a_no_op(repo):
    broker = FakeBroker()
    order_id = repo.insert_order(
        dict(
            mode="live",
            product_id="BTC-USD",
            side="SELL",
            order_type="stop",
            qty=Decimal("0.01"),
            limit_price=Decimal("49000"),
            status="pending",
            fee=None,
            expected_fill=Decimal("49000"),
            actual_fill=None,
            raw_response=None,
            confirmation="oco",
            rule_id=None,
            created_at=NOW_TS,
            updated_at=NOW_TS,
        )
    )

    cancelled = handle_oco_fill(broker, repo, order_id, now_ts=NOW_TS)

    assert cancelled is None
    assert len(broker.cancel_calls) == 0


# -- partial scale-out ---------------------------------------------------------------------------


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
    stop_id, _target_id = place_oco_bracket(
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
    stop_id, _target_id = place_oco_bracket(
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
    stop_id, _target_id = place_oco_bracket(
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
    stop_id, _target_id = place_oco_bracket(
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
