"""Tests for keel.execution.executor -- the order executor (P3 Task 4).

`execute()` turns a `Signal` into a guarded live order: build an `OrderIntent` (sized via
`execution.sizing`), run `guards.check` FIRST (un-overridable -- a violation must never reach
`preview_order`/`place_order`), preview it, honor confirm/autonomous mode, place it, and log it to
the `orders` table both before and after placement (a full audit trail even if the broker call
fails). Every test here injects a **fake broker** (no network) -- `FakeBroker` below duck-types
`CoinbaseClient.preview_order`/`.place_order` (+ an optional `cancel_order` for OCO) against
canned responses, exactly like `tests/data/test_cb_client.py`'s `FakeTransport` fakes the
transport underneath `CoinbaseClient`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from decimal import Decimal
from typing import Any
from unittest import mock

import pytest
from keel_broker_api.orders import BracketGTC, LimitGTC, OrderSpec
from keel_broker_api.port import TradeScopeDenied
from keel_broker_api.results import Balance, OrderStatus, PlaceResult, Preview
from keel_core.quote_provenance import SYNTHETIC_ESTIMATE, UNPRICED, UNREADABLE, VENUE_QUOTED
from keel_core.subscription import SubscriptionStatus
from keel_core.telemetry import bind_venue, unbind_venue
from keel_core.trade_scope import READ_ONLY, TRADING, TradeScopeState

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
from keel.execution import executor, guards, sizing
from keel.execution.executor import (
    CancelPending,
    CancelUnavailable,
    ExecutionResult,
    execute,
    place_bracket,
    roll_stop_to,
    roll_to_break_even,
    scale_out,
    trail_stop_atr,
)
from keel.execution.guards import OrderIntent
from keel.strategy.rules.base import Action, Setup, Signal
from keel.types import Side
from tests.conftest import attest_cash_posture, attest_subscription, attest_trade_scope

NOW_TS = 1_700_000_000


# -- fakes ----------------------------------------------------------------------------------


def _preview_from(spec: OrderSpec, payload: dict[str, Any]) -> Preview:
    """A `Preview` from the dict shape these fakes have always described a quote in.

    Kept as a dict at the call sites deliberately: dozens of tests construct a bespoke preview to
    exercise one degraded field -- a missing `best_bid`, a non-numeric `best_ask`, an `errs` list
    -- and rewriting every one of them into a `Preview` constructor would have been a far larger
    diff than the behaviour change it accompanies, with more chances to alter a case by accident.
    This function is the one place the translation happens.

    The book goes into `detail` as STRINGS, which is what the port's `Preview` declares and what
    `executor._preview_book` reads: the spread gate (#350) and the entry-override warning (#332)
    both come through it, so a fake that carried the book anywhere else would silently stop
    exercising two safety paths.
    """
    return Preview(
        product_id=spec.product_id,
        side=spec.side,
        est_base_size=Decimal(str(payload.get("base_size", "0"))),
        est_quote_size=Decimal(str(payload.get("quote_size", "0"))),
        est_fee=Decimal(str(payload.get("commission_total", "0"))),
        synthetic=False,
        detail={
            key: str(payload[key])
            for key in ("best_bid", "best_ask", "order_total")
            if payload.get(key) is not None
        },
        errors=tuple(str(e) for e in (payload.get("errs") or [])),
    )


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
        balances: dict[str, Decimal | None] | None = None,
    ) -> None:
        # `balances` models a REAL account: one entry per currency. Without it the fake funds
        # both USD and USDC with `usdc_balance`, so tests that only mean "the account is funded"
        # keep meaning that -- the mismatch tests below set the two independently on purpose.
        self._balances = balances
        # The default preview carries BOTH sides of a TIGHT book, because that is what the
        # real venue returns (`cb_client.preview_order` maps `best_bid`/`best_ask` to
        # `Decimal`; see `tests/fixtures/cb_preview_order.json`) and #350's spread gate fails
        # closed on a preview without them. A test that means "a degraded/bookless response"
        # passes its own preview dict -- see the #332 warning tests and the gate's
        # `book_unreadable` tests.
        self._preview = preview or {
            "order_total": Decimal("50.00"),
            "commission_total": Decimal("0.30"),
            "errs": [],
            "warning": [],
            "best_bid": Decimal("49990"),
            "best_ask": Decimal("50000"),
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
        self.get_balances_calls = 0

    def get_balances(self) -> list[Balance]:
        """The port's shape since #524.

        The "no balance known" case is an EMPTY LIST rather than a row carrying `None`. That is
        not a shortcut around `Balance` requiring a `Decimal`: in the port's model a currency
        with no account simply is not in the list, and `_fetch_available_quote` returns `None`
        for it either way -- by falling off the loop instead of by testing a null field.
        """
        self.get_balances_calls += 1
        if self._balances is not None:
            return [Balance(currency=c, available=b, total=b) for c, b in self._balances.items()]
        if self._usdc_balance is None:
            return []
        return [
            Balance(currency="USD", available=self._usdc_balance, total=self._usdc_balance),
            Balance(currency="USDC", available=self._usdc_balance, total=self._usdc_balance),
        ]

    def preview_order(self, spec: OrderSpec) -> Preview:
        self.preview_calls.append({"spec": spec})
        return _preview_from(spec, self._preview)

    def place_order(self, spec: OrderSpec, *, idempotency_key: str | None = None) -> PlaceResult:
        self._place_order_id_seq += 1
        order_id = f"{self._place_order_id_prefix}-{self._place_order_id_seq}"
        self.events.append("place")
        self.place_calls.append({"spec": spec})
        if self._place_success:
            return PlaceResult(success=True, broker_order_id=order_id)
        return PlaceResult(success=False, broker_order_id=None, reason="no funds")

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
    # Rail 17 (§65.4) fails closed without a fresh withdrawal attestation, so every test that
    # is not ABOUT rail 17 supplies one -- same reason this fixture seeds the kill-switch and
    # feed timestamp for rails 12.
    r.set_state("withdrawals_enabled", True)
    r.set_state("withdrawals_attested_at", NOW_TS)
    # A very large, attested monthly allowance so pre-existing (non-rail-14) tests aren't
    # incidentally tripped by it; rail-14-specific tests below override with `_attest(...)`.
    _attest(r, free_volume_usd=Decimal("10000000"))
    # Rail 20 (#233) fails closed without a trade-scope record, so every test that is not ABOUT
    # rail 20 gets the CONFIRMED shape the v14 backfill produces for an already-live venue --
    # same reason this fixture seeds withdrawals and the subscription above.
    attest_trade_scope(r, now_ts=NOW_TS)
    # Rail 22 (#691) fails closed without a cash-posture record, same as rail 20.
    attest_cash_posture(r, now_ts=NOW_TS)
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
    assert "order_total" in seen[0].detail


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


# -- rule_id metadata (Phase-2 debt: orders.rule_id was always written NULL) ----------------------


def test_placed_order_carries_the_signals_rule_id(repo):
    """The fix under test: `orders.rule_id` used to be hardcoded `None` in `_order_row`
    regardless of the signal. It must now carry `signal.rule_id` end to end through
    `_build_intent`'s `OrderIntent.rule_id`.
    """
    rule_id = repo.insert_rule("pullback_continuation", {}, status="live")
    broker = FakeBroker()
    signal = _enter_signal(rule_id=rule_id)

    result = execute(signal, broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

    assert result.placed is True
    order = repo.get_order(result.order_id)
    assert order["rule_id"] == rule_id


def test_a_signal_with_no_rule_id_still_writes_none(repo):
    """Backward-compat: a signal from a hand-constructed rule (no `rule_id` supplied, the
    default) still writes `NULL`, exactly as before this fix."""
    broker = FakeBroker()
    signal = _enter_signal()  # no rule_id override -> defaults to None

    result = execute(signal, broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

    assert result.placed is True
    order = repo.get_order(result.order_id)
    assert order["rule_id"] is None


def test_rule_id_is_purely_additive_metadata_placement_and_guards_are_unchanged(repo):
    """The metadata-only guarantee: two otherwise-identical signals, differing only in
    `rule_id`, must produce byte-for-byte identical guard/placement outcomes -- same veto
    decisions, same `placed`, same broker calls/order-configuration, same sized qty/notional.
    The ONLY difference in the resulting order rows is the `rule_id` column.
    """
    rule_id = repo.insert_rule("pullback_continuation", {}, status="live")
    broker_a = FakeBroker()
    broker_b = FakeBroker()
    signal_no_id = _enter_signal(rule_id=None)
    signal_with_id = _enter_signal(rule_id=rule_id)

    result_a = execute(signal_no_id, broker_a, repo, _config(), mode="autonomous", now_ts=NOW_TS)
    result_b = execute(
        signal_with_id, broker_b, repo, _config(), mode="autonomous", now_ts=NOW_TS + 1
    )

    assert result_a.placed == result_b.placed is True
    assert result_a.vetoed_by == result_b.vetoed_by == []
    assert len(broker_a.preview_calls) == len(broker_b.preview_calls)
    assert len(broker_a.place_calls) == len(broker_b.place_calls)
    assert broker_a.place_calls[0]["spec"] == broker_b.place_calls[0]["spec"]

    order_a = repo.get_order(result_a.order_id)
    order_b = repo.get_order(result_b.order_id)
    # Every field EXCEPT rule_id/created_at/updated_at/id must match -- proving rule_id is the
    # only thing that changed.
    for field in (
        "mode",
        "product_id",
        "side",
        "order_type",
        "qty",
        "status",
        "confirmation",
    ):
        assert order_a[field] == order_b[field], f"{field} differs -- not metadata-only"
    assert order_a["rule_id"] is None
    assert order_b["rule_id"] == rule_id


def test_rail_violating_signal_with_a_rule_id_is_still_vetoed_the_same_way(repo):
    """`rule_id` must not influence guard decisions -- a vetoed intent stays vetoed."""
    rule_id = repo.insert_rule("pullback_continuation", {}, status="live")
    broker = NoNetworkBroker()
    signal = _enter_signal(
        product_id="DOGE-USD", setup=_setup(product_id="DOGE-USD"), rule_id=rule_id
    )

    result = execute(signal, broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

    assert result.placed is False
    assert result.order_id is None
    assert any(v.startswith("halal_allowlist") for v in result.vetoed_by)
    assert repo.get_orders() == []


# -- rail-violating signal -> vetoed, never previews/places --------------------------------------


def test_rail_violating_signal_is_vetoed_before_preview_or_place(repo):
    broker = NoNetworkBroker()
    signal = _enter_signal(product_id="DOGE-USD", setup=_setup(product_id="DOGE-USD"))

    result = execute(signal, broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

    assert result.placed is False
    assert result.order_id is None
    assert result.preview is None
    assert any(v.startswith("halal_allowlist") for v in result.vetoed_by)
    assert repo.get_orders() == []


def test_kill_switch_vetoes_even_in_bypass_mode(repo):
    repo.set_state("kill_switch", True)
    broker = NoNetworkBroker()
    signal = _enter_signal()

    result = execute(signal, broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

    assert result.placed is False
    assert any(v.startswith("kill_switch") for v in result.vetoed_by)


# -- rail 13: USDC-funding wiring (Issue #59) ----------------------------------------------------


class _BrokerAccountsError:
    """`get_accounts` raises (a simulated broker outage); `preview_order`/`place_order` raise
    too if ever reached -- isolates "the balance fetch itself failed" from a generic
    NoNetworkBroker veto, and proves the failure alone is enough to veto without ever previewing
    or placing.
    """

    def get_balances(self) -> list[Balance]:
        raise ConnectionError("simulated broker outage fetching balances")

    def preview_order(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("preview_order must not be called when the balance fetch failed")

    def place_order(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("place_order must not be called when the balance fetch failed")


def test_execute_fetches_the_available_quote_balance_for_a_buy_and_places(repo):
    broker = FakeBroker(usdc_balance=Decimal("100000"))
    signal = _enter_signal()

    result = execute(signal, broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

    assert result.placed is True
    # TWO reads, and they are different questions about different currencies (#667). The first
    # is rail 13's: how much QUOTE can this BUY spend. The second belongs to the exit bracket
    # `execute` places once the entry has filled -- how much BASE does the account actually hold
    # to protect. A bracket sized from the ordered quantity is the oversized-bracket condition.
    assert broker.get_balances_calls == 2


def test_broker_balance_fetch_error_vetoes_the_buy_before_preview_or_place(repo):
    broker = _BrokerAccountsError()
    signal = _enter_signal()

    result = execute(signal, broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

    assert result.placed is False
    assert result.order_id is None
    assert result.preview is None
    assert any(v.startswith("usdc_funding") for v in result.vetoed_by)
    assert repo.get_orders() == []


def test_insufficient_usdc_balance_vetoes_the_buy_before_preview_or_place(repo):
    broker = FakeBroker(usdc_balance=Decimal("10"))  # entry 50000, qty ~1 -> notional ~50000
    signal = _enter_signal()

    result = execute(signal, broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

    assert result.placed is False
    assert result.preview is None
    assert any(v.startswith("usdc_funding") for v in result.vetoed_by)
    assert repo.get_orders() == []


def test_exit_signal_fetches_the_held_base_not_the_quote_balance(repo):
    """A SELL is still exempt from rail 13, and since #667 it reads a balance anyway.

    This test asserted `get_balances_calls == 0` for six months, and the reasoning was sound
    while the only balance question was rail 13's: an EXIT spends no quote, so fetching one
    bought nothing. #667 asks a DIFFERENT question of the same endpoint -- not "can this order
    be funded" but "does the account still hold what the ledger says it holds" -- and that one
    an exit must ask, because the exit is the order that oversells when the answer is no.
    """
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

    result = execute(signal, broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

    assert result.placed is True
    assert broker.get_balances_calls == 1
    # And it is the BASE leg that was asked about: the default fake funds USD/USDC and carries
    # no BTC row, so the holding reads UNKNOWN and the exit goes out at the ledger quantity --
    # unchanged, un-vetoed. An unreadable balance never strands a position that wanted out.
    spec = broker.place_calls[-1]["spec"]
    assert spec.base_size == Decimal("0.1")


# -- autonomous mode: compliant -> placed without a prompt
# --------------------------------------------


def test_bypass_mode_compliant_signal_places_without_confirm_fn(repo):
    broker = FakeBroker()
    signal = _enter_signal()

    result = execute(signal, broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

    assert result.placed is True
    assert result.order_id is not None
    order = repo.get_order(result.order_id)
    assert order["status"] == "filled"


def test_bypass_mode_ignores_confirm_fn_if_provided(repo):
    broker = FakeBroker()
    signal = _enter_signal()

    result = execute(
        signal, broker, repo, _config(), mode="autonomous", confirm_fn=_reject, now_ts=NOW_TS
    )

    # autonomous mode never consults confirm_fn -- a reject-everything fn must not block it.
    assert result.placed is True


# -- unknown mode -----------------------------------------------------------------------------


def test_unknown_mode_raises_value_error(repo):
    broker = FakeBroker()
    signal = _enter_signal()

    with pytest.raises(ValueError, match="mode"):
        # type: ignore[arg-type]
        execute(signal, broker, repo, _config(), mode="yolo", now_ts=NOW_TS)


# -- broker place failure --------------------------------------------------------------------


def test_broker_place_failure_is_logged_but_not_placed(repo):
    broker = FakeBroker(place_success=False)
    signal = _enter_signal()

    result = execute(signal, broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

    assert result.placed is False
    assert result.order_id is not None  # still logged (audit trail even on rejection)
    order = repo.get_order(result.order_id)
    assert order["status"] == "rejected"


# -- rail 14: monthly-allowance wiring (Issue #59) -----------------------------------------------


def test_monthly_allowance_vetoes_a_buy_over_the_live_subscription_cap(repo):
    _attest(repo, free_volume_usd=Decimal("100"))
    broker = FakeBroker()
    signal = _enter_signal()

    result = execute(signal, broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

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

    first = execute(signal, broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)
    assert first.placed is False

    _attest(repo, free_volume_usd=Decimal("10000000"))
    second = execute(signal, broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)
    assert second.placed is True


# -- _build_intent equity override (Task 4, sizing fix part 2) ---------------------------------


def test_build_intent_uses_equity_override(repo):
    from keel.execution import executor

    signal = _enter_signal()
    config = _config()

    default_intent = executor._build_intent(signal, None, repo, config, now_ts=NOW_TS)
    override_intent = executor._build_intent(
        signal, None, repo, config, now_ts=NOW_TS, equity_override=Decimal("30000")
    )

    # qty scales linearly with equity: override (30000) vs config.caps.max_exposure_usd
    # (1000000 in _config()'s defaults).
    assert override_intent.qty != default_intent.qty
    assert override_intent.qty == default_intent.qty * (
        Decimal("30000") / config.caps.max_exposure_usd
    )


def test_live_sizing_equity_is_the_config_constant_immune_to_reward_accruals(repo):
    """#490: the live path's sizing-equity stand-in is `config.caps.max_exposure_usd` (see the
    module docstring) -- a config constant, not a balance read -- so accrued-but-unpurified
    reward income cannot inflate live sizing through `_build_intent`. Pinned so the invariant
    cannot silently regress: a repo full of reward income must leave the sized qty exactly
    `sizing.size(max_exposure_usd, ...)`. If this ever fails because sizing moved to a live
    balance read, the #490 purification exclusion must move with it."""
    from keel.execution import executor, sizing

    repo.upsert_transaction(
        {
            "coinbase_id": "rx1",
            "source": "coinbase",
            "type": "Reward Income",
            "asset": "USDC",
            "ts": 1_700_000_000,
            "qty": Decimal("1"),
            "price": Decimal("1"),
            "subtotal": Decimal("999999"),
            "total": Decimal("999999"),
            "fees": Decimal("0"),
        }
    )
    signal = _enter_signal()
    config = _config()

    intent = executor._build_intent(signal, None, repo, config, now_ts=NOW_TS)

    assert intent.qty == sizing.size(
        config.caps.max_exposure_usd, config.risk_pct, signal.setup.entry, signal.setup.stop
    )


def test_live_execute_sizing_is_immune_to_reward_income_through_the_public_entry(repo):
    """#490 live-path pin at the layer that matters. The `_build_intent` pin above proves the
    HELPER reads `config.caps.max_exposure_usd`, but nothing in it would catch someone wiring a
    balance-derived `equity_override` into the live path. This drives the PUBLIC entry --
    `execute(...)`, no `equity_override` -- with reward income in both places it can reach
    sizing: a broker whose balances are inflated by accrued reward income, and a repo whose
    transactions ledger carries the matching reward rows. The sized qty/notional must be
    IDENTICAL to a run with no reward income anywhere: live sizing reads a config constant,
    immune by construction. If this ever fails because live sizing moved to a balance read, the
    #490 purification subtraction must move with it (`equity.sizing_equity`)."""
    from keel.execution import sizing

    signal = _enter_signal()
    config = _config()

    clean_broker = FakeBroker(usdc_balance=Decimal("1000000"))
    clean = execute(signal, clean_broker, repo, config, mode="autonomous", now_ts=NOW_TS)
    assert clean.placed is True, clean.vetoed_by

    # Reward income everywhere: accrued INSIDE the trading account (both balance legs read high
    # by the accrued rewards) and recorded in the imported ledger the purification report reads.
    # A balance-derived equity base would size off the inflated read; the config cap cannot.
    repo.upsert_transaction(
        {
            "coinbase_id": "rx1",
            "source": "coinbase",
            "type": "Reward Income",
            "asset": "USDC",
            "ts": 1_700_000_000,
            "qty": Decimal("1"),
            "price": Decimal("1"),
            "subtotal": Decimal("999999"),
            "total": Decimal("999999"),
            "fees": Decimal("0"),
        }
    )
    reward_broker = FakeBroker(balances={"USD": Decimal("1999999"), "USDC": Decimal("1999999")})
    reward = execute(signal, reward_broker, repo, config, mode="autonomous", now_ts=NOW_TS + 1)
    assert reward.placed is True, reward.vetoed_by

    expected = sizing.size(
        config.caps.max_exposure_usd, config.risk_pct, signal.setup.entry, signal.setup.stop
    )
    clean_order = repo.get_order(clean.order_id)
    reward_order = repo.get_order(reward.order_id)
    assert reward_order["qty"] == clean_order["qty"] == expected
    assert (
        reward_order["qty"] * reward_order["expected_fill"]
        == clean_order["qty"] * clean_order["expected_fill"]
        == expected * signal.setup.entry
    )
    # The order actually sent to the venue is identical -- same sized base_size.
    assert reward_broker.place_calls[0]["spec"] == clean_broker.place_calls[0]["spec"]


# -- DCA sizing --------------------------------------------------------------------------------


def test_dca_signal_sizes_via_dca_size_and_places(repo):
    broker = FakeBroker()
    signal = _dca_signal()

    result = execute(signal, broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

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

    result = execute(signal, broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

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
            confirmation="autonomous",
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

    result = execute(signal, broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

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

    result = execute(signal, broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

    assert result.placed is False
    assert result.order_id is None


# -- OCO bracket -------------------------------------------------------------------------------


def test_execute_attaches_oco_bracket_after_a_stop_target_entry_fills(repo):
    broker = FakeBroker()
    signal = _enter_signal()

    result = execute(signal, broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

    assert result.placed is True
    # entry + ONE native bracket = 2 place_order calls (was 3: entry + stop leg + target leg)
    assert len(broker.place_calls) == 2
    orders = repo.get_orders(product_id="BTC-USD")
    sell_orders = [o for o in orders if o["side"] == "SELL"]
    assert len(sell_orders) == 1
    assert isinstance(broker.place_calls[-1]["spec"], BracketGTC)
    assert repo.get_state("open_stop:BTC-USD") == Decimal("49000")
    assert repo.get_state("open_target:BTC-USD") == Decimal("53000")


# NOTE: `test_oco_fill_of_target_cancels_the_stop_leg` and
# `test_oco_fill_with_no_sibling_recorded_is_a_no_op` were DELETED with `handle_oco_fill`, not
# weakened. They covered client-side sibling cancellation, which the native trigger bracket makes
# impossible to get wrong: there is one order, so there is no sibling to cancel. The invariant
# they protected (never sell an already-closed position twice) is now the exchange's to enforce.


# NOTE: `test_scale_out_places_a_partial_sell_and_logs_it` moved to
# `tests/execution/test_scale_out.py` and grew into a module. #502 turned `scale_out` from a
# bare SELL into a four-step sequence -- crash ledger, cancel, sell, re-place at the remainder,
# with the sold fraction booked against the `positions` ledger -- and a single "it placed a
# SELL" assertion no longer describes it.


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


def test_rolling_the_stop_cancels_a_PARTIALLY_FILLED_bracket_first(repo, caplog):
    """The stop-roll half of the same #446 regression. `_roll_stop` cancelled the old bracket
    only while its row read `pending`; a `partially_filled` row was left RESTING and the
    replacement was placed anyway -- TWO brackets, each committing the whole position. (On
    the real venue the replacement is instead REJECTED for insufficient funds -- the base is
    locked by the old bracket -- and the roll lands in the naked-position CRITICAL below; the
    permissive fake surfaces the sibling failure, the double bracket.)"""
    broker = FakeBroker()
    old_id = place_bracket(
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
    repo.update_order(
        old_id,
        status="partially_filled",
        filled_quantity=Decimal("0.004"),
        actual_fill=Decimal("48900"),
        updated_at=NOW_TS + 1,
    )

    with caplog.at_level(logging.CRITICAL):
        new_id = roll_to_break_even(
            broker,
            repo,
            _config(),
            product_id="BTC-USD",
            old_stop_order_id=old_id,
            entry_price=Decimal("50000"),
            qty=Decimal("0.01"),
            rule_name="pullback_continuation",
            now_ts=NOW_TS + 100,
        )

    assert new_id is not None and new_id != old_id
    assert repo.get_order(old_id)["status"] == "canceled"
    # NO DOUBLE BRACKET: exactly one non-terminal SELL rests after the roll -- the
    # replacement. Pre-fix the unfetched old bracket stayed `partially_filled` beside it.
    resting = [
        o
        for o in repo.get_orders(product_id="BTC-USD")
        if o["side"] == Side.SELL.value and o["status"] in ("pending", "partially_filled")
    ]
    assert [o["id"] for o in resting] == [new_id]
    # ...and the naked-position branch never fired: the replacement placed, so there was
    # nothing to scream about.
    assert "executor.position_unprotected" not in caplog.text
    # Cancel BEFORE the replacement place, for the same base-locked reason as the rule exit.
    assert broker.events == ["place", "cancel", "place"], broker.events


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


def test_a_roll_that_reaches_the_target_is_refused_and_the_bracket_stays(repo):
    """**A stop at or above the target is not a tighter stop; it is a coin flip.**

    The replacement is a single native bracket carrying both prices, so a stop that has caught up
    with the target describes two exits racing at the same level -- whichever side the venue
    evaluates first decides whether this position took a profit or a loss.
    `keel_broker_api.orders.BracketGTC` refuses exactly this at construction, and every roll
    IS one of those since #524/#569 -- but #560 added this earlier, explicit refusal for the
    same hazard, and it stays because it refuses BEFORE the cancel, leaving the existing
    bracket resting rather than relying on the construction error after the cancel landed.

    Refusing is the conservative half, and this asserts that half: the roll is abandoned, the
    EXISTING bracket is untouched (`pending`, not `canceled`), and the recorded stop is unchanged,
    so the position keeps the protection it already had. The alternative -- cancel a working
    bracket to install an inverted one -- risks a venue refusal that leaves the position naked.
    """
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

    # A break-even roll to a price ABOVE the recorded target. It tightens (53500 > 49000, so the
    # ratchet is satisfied) and is still nonsense.
    result = roll_to_break_even(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        old_stop_order_id=stop_id,
        entry_price=Decimal("53500"),
        qty=Decimal("0.01"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS + 100,
    )

    assert result is None
    assert repo.get_order(stop_id)["status"] == "pending", "a working bracket was cancelled"
    assert repo.get_state("open_stop:BTC-USD") == Decimal("49000")
    assert repo.get_state("open_target:BTC-USD") == Decimal("53000")


def test_a_roll_exactly_onto_the_target_is_refused_too(repo):
    """`>=`, not `>`. Equal is the subtler half: two equal prices read as an ordinary pair of
    numbers, and what they describe is a stop and a target racing at the SAME price. `BracketGTC`
    refuses equal legs as firmly as inverted ones, for the same reason."""
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

    result = roll_to_break_even(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        old_stop_order_id=stop_id,
        entry_price=Decimal("53000"),
        qty=Decimal("0.01"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS + 100,
    )

    assert result is None
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


def test_a_ratchet_only_trail_can_never_trip_rail_9(repo):
    """THE rail-9 compatibility proof for `trail_stop_atr` (#442, hypothesis 1).

    Rail 9 (`guards.check`, "no stop-loss widening") vetoes any protective stop strictly
    LOWER than the last recorded `open_stop:<product>`. A ratchet-only trail is safe by
    construction: `_roll_stop` refuses a widening proposal BEFORE `guards.check` ever runs,
    so the rail only ever sees `proposed >= prior` -- which passes. This test drives an
    ADVERSARIAL (price, atr) sequence -- a ratchet step, a crash, a partial recovery, a
    rally, and an exact-tie -- and asserts the two halves of that argument as observed
    behavior:

    1. the recorded `open_stop` sequence is NON-DECREASING across the whole walk (the
       ratchet holds even when the computed trail collapses), and
    2. rail 9 never fires: every roll that places clears `guards.check` with no
       `no_stop_widening` violation, and every widening proposal is refused LOCALLY
       (no cancel, no placement, no state change) before the rail could see it.
    """
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
    assert stop_id is not None

    # (label, price, atr, should_place) -- computed trail = price - 2*atr.
    walk: list[tuple[str, Decimal, Decimal, bool]] = [
        ("ratchet_up", Decimal("52000"), Decimal("500"), True),  # 51000 > 49000
        ("crash", Decimal("47000"), Decimal("1500"), False),  # 44000 < 51000
        ("partial_recovery", Decimal("50500"), Decimal("600"), False),  # 49300 < 51000
        ("rally", Decimal("53500"), Decimal("700"), True),  # 52100 > 51000
        ("stall", Decimal("53100"), Decimal("700"), False),  # 51700 < 52100
    ]

    recorded_stops: list[Decimal] = [Decimal("49000")]  # the bracket's own placement
    current_stop_order = stop_id
    events_before = 0

    for label, price, atr, should_place in walk:
        events_before = len(broker.events)
        result = trail_stop_atr(
            broker,
            repo,
            _config(),
            product_id="BTC-USD",
            old_stop_order_id=current_stop_order,
            current_price=price,
            atr=atr,
            qty=Decimal("0.01"),
            rule_name="pullback_continuation",
            now_ts=NOW_TS + 300 + len(recorded_stops),
            multiplier=Decimal("2"),
        )

        if should_place:
            assert result is not None, label
            # guards.check ran for this placement (un-overridable) and cleared rail 9 --
            # a vetoed replacement leaves no resting order behind.
            assert repo.get_order(result)["status"] == "pending", label
            current_stop_order = result
        else:
            # Refused LOCALLY by _roll_stop's ratchet, BEFORE guards.check: no cancel,
            # no placement, the old bracket stays live and the state is untouched.
            assert result is None, label
            assert broker.events[events_before:] == [], label
            assert repo.get_order(current_stop_order)["status"] == "pending", label

        # The rail-level half of the proof, checked DIRECTLY against the rail for every
        # step: the ratchet-clamped proposal rail 9 would see (`max(prior, computed)`)
        # never trips `no_stop_widening` -- the strict `<` in the rail and the `max` in
        # the ratchet are exact complements.
        clamped = max(recorded_stops[-1], price - Decimal("2") * atr)
        guard_view = guards.check(
            OrderIntent(
                product_id="BTC-USD",
                side=Side.SELL,
                qty=Decimal("0.01"),
                entry=clamped,
                stop=None,
                notional=clamped * Decimal("0.01"),
                is_dca=False,
                rule_kind="pullback_continuation",
                protective_stop=clamped,
            ),
            repo,
            _config(),
            NOW_TS,
        )
        assert "no_stop_widening" not in guard_view.violations, label

        recorded_stops.append(repo.get_state("open_stop:BTC-USD"))
        assert recorded_stops[-1] >= recorded_stops[-2], (
            f"{label}: open_stop widened {recorded_stops[-2]} -> {recorded_stops[-1]}"
        )

    # The walk actually exercised both directions: at least one ratchet step happened
    # and at least one widening proposal was refused (otherwise this proves nothing).
    assert len({str(s) for s in recorded_stops}) > 1
    assert any(not placed for _, _, _, placed in walk)


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
            # Both book sides, as the real venue returns them: #350's spread gate fails
            # closed on a preview without them, and this test is about the FEE, not the book.
            "best_bid": Decimal("49990"),
            "best_ask": Decimal("50000"),
        }
    )
    signal = _enter_signal()

    result = execute(signal, broker, repo, _config(), "autonomous", confirm_fn=None, now_ts=NOW_TS)

    assert result.placed is True
    order = repo.get_order(result.order_id)
    assert order["status"] == "filled"
    assert order["fee"] == Decimal("0.30")


# -- partially filled market entries (#446) -------------------------------------------------------


class _PartiallyFillingBroker(FakeBroker):
    """The venue's answer for a market IOC that only partly filled: the IOC cancelled the
    remainder at the venue, `filled_size` is what actually executed, and `average_filled_price`
    is the running average over those fills."""

    def __init__(self, filled_size: Decimal, average_price: Decimal) -> None:
        super().__init__()
        self._observed = OrderStatus(
            order_id="broker-order-1",
            status="FILLED",
            filled_size=filled_size,
            average_filled_price=average_price,
            total_fees=Decimal("0.18"),
        )

    def get_order(self, order_id: str) -> OrderStatus:
        return self._observed


def test_a_partially_filled_entry_records_the_filled_quantity_and_warns(repo, caplog):
    """The entry-side half of #446. A market IOC entry that only partly filled still leaves a
    row claiming the FULL ordered size was bought -- and `execute` then places the exit bracket
    for that ordered size, i.e. for more than is held.

    The bracket AMEND/cancel-and-replace policy is #502's (the port has no bracket kind), so
    what this path owes today is DETECTION: record the observed filled quantity on the row and
    warn loudly enough that a human sizes the bracket to reality. The bracket itself is still
    placed for the ordered size -- detect-and-surface, not auto-resize."""
    # The standard enter signal sizes to qty = 1 (equity 1,000,000 x risk 0.001 / the 1000
    # entry-to-stop distance); the venue reports only 0.6 of it executed.
    broker = _PartiallyFillingBroker(filled_size=Decimal("0.6"), average_price=Decimal("50010"))
    signal = _enter_signal()

    with caplog.at_level(logging.WARNING):
        result = execute(
            signal, broker, repo, _config(), "autonomous", confirm_fn=None, now_ts=NOW_TS
        )

    assert result.placed is True
    order = repo.get_order(result.order_id)
    assert order["status"] == "filled"  # terminal for an IOC, as before
    assert order["qty"] == Decimal("1")  # the ordered size, unchanged
    assert order["filled_quantity"] == Decimal("0.6")  # ...but the observed fill is recorded
    assert order["actual_fill"] == Decimal("50010")
    assert order["fee"] == Decimal("0.18")
    assert "executor.entry_partially_filled" in caplog.text
    # Detect-and-surface, NOT auto-resize: the bracket is still placed for the ORDERED size
    # (resizing it is the amend-vs-replace decision #502 owns).
    bracket = broker.place_calls[-1]["spec"]
    assert bracket.base_size == Decimal("1.000")


def test_a_fully_filled_entry_records_the_filled_quantity_without_warning(repo, caplog):
    """The negative control: `filled_quantity == qty` is the ordinary case and must not warn --
    a warning on every entry trains the alert to be ignored."""
    broker = _PartiallyFillingBroker(filled_size=Decimal("1"), average_price=Decimal("50010"))
    signal = _enter_signal()

    with caplog.at_level(logging.WARNING):
        result = execute(
            signal, broker, repo, _config(), "autonomous", confirm_fn=None, now_ts=NOW_TS
        )

    order = repo.get_order(result.order_id)
    assert order["filled_quantity"] == Decimal("1") == order["qty"]
    assert "executor.entry_partially_filled" not in caplog.text


def test_a_partially_filled_EXIT_records_the_fill_but_does_not_fire_the_entry_warning(repo, caplog):
    """The warning is ENTRY-only (#446 review): its advice is about the ENTRY's exit bracket
    being placed for the ordered size, and an immediately-filled market SELL exit rides the
    same observed-economics upgrade. Firing entry wording there would send an operator to
    resize a bracket this side never placed -- the exit-side over-booking is #502's to flag.

    The OBSERVATION is still recorded whatever the side: `filled_quantity` is what actually
    executed, and hiding it because the warning is entry-specific would re-create the
    blindness #446 exists to end."""
    _seed_filled_buy(repo, qty=Decimal("0.01"), price=Decimal("50000"))
    # The venue's answer for a market SELL that only partly filled on a thin book: 0.006 of
    # the 0.01 sold, IOC cancelled the remainder. (`_PartiallyFillingBroker`'s canned
    # `side`/`status` fields are not read by this path -- only the fill figures are.)
    broker = _PartiallyFillingBroker(filled_size=Decimal("0.006"), average_price=Decimal("49980"))

    with caplog.at_level(logging.WARNING):
        result = execute(_exit_signal(), broker, repo, _config(), "autonomous", None, now_ts=NOW_TS)

    assert result.placed is True
    order = repo.get_order(result.order_id)
    assert order["status"] == "filled"  # terminal for an IOC, as before
    assert order["qty"] == Decimal("0.01")  # the ordered size, unchanged
    assert order["filled_quantity"] == Decimal("0.006")  # ...and the observed fill is recorded
    assert order["actual_fill"] == Decimal("49980")
    assert "executor.entry_partially_filled" not in caplog.text


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

    with pytest.raises(CancelUnavailable, match="cancel_order"):
        roll_to_break_even(
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

    with pytest.raises(RuntimeError, match="refused the cancel"):
        roll_to_break_even(
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

    assert repo.get_order(stop_id)["status"] == "pending"


def test_a_leg_with_no_broker_side_id_cannot_be_cancelled_and_says_so(repo):
    """`_native_order_id` returns None when the placement response carried no id. There is then
    nothing to cancel AT the exchange, so the same rule applies: raise, do not rewrite state."""
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
    repo.update_order(stop_id, raw_response=json.dumps({"success": True}))  # no order_id

    with pytest.raises(CancelUnavailable, match="broker-side id"):
        roll_to_break_even(
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

    assert order_id is not None
    sells = [o for o in repo.get_orders(product_id="BTC-USD") if o["side"] == "SELL"]
    assert len(sells) == 1
    assert len(broker.place_calls) == 1

    spec = broker.place_calls[0]["spec"]
    assert isinstance(spec, BracketGTC)
    assert spec.base_size == Decimal("0.01")
    assert spec.take_profit_price == Decimal("53000")  # take-profit
    assert spec.stop_trigger_price == Decimal("49000")  # stop-loss
    # The port names the take-profit `take_profit_price`, not Coinbase's `limit_price` -- so a
    # second venue's translation never starts from Coinbase's vocabulary (#521).


def test_bracket_records_the_stop_for_rail_9_and_the_target_for_later_rolls(repo):
    """`open_stop` is rail 9's no-widening reference. `open_target` is new: with one order
    carrying both prices, rolling the stop means re-placing the bracket, which needs the target
    that is no longer recoverable from a separate leg."""
    broker = FakeBroker()

    place_bracket(
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

    assert repo.get_state("open_stop:BTC-USD") == Decimal("49000")
    assert repo.get_state("open_target:BTC-USD") == Decimal("53000")


def test_execute_surfaces_the_bracket_order_id_it_placed(repo):
    """`execute` places the bracket ITSELF, so its return is the only way a caller can learn the
    id. Discarding it left the resting bracket unnameable: `agent.run_once` could not point the
    new `positions` tranche at it, and `roll_to_break_even`/`trail_stop_atr` -- which take an
    `old_stop_order_id` -- were unreachable by construction rather than merely uncalled.
    """
    broker = FakeBroker()

    result = execute(_enter_signal(), broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

    assert result.bracket_order_id is not None, "execute discarded the bracket's order id again"
    bracket = repo.get_order(result.bracket_order_id)
    assert bracket["side"] == "SELL"
    assert bracket["status"] == "pending"
    assert result.bracket_order_id != result.order_id  # the bracket, not the entry


def test_a_vetoed_bracket_leaves_no_bracket_order_id(repo):
    """`None` must mean "there is no resting bracket", never "there is one but we lost its id" --
    `run_once` would otherwise skip `set_position_bracket` on a tranche that does have a bracket,
    or name one that was never placed."""
    broker = FakeBroker()

    # DCA carries no stop, so no bracket is ever placed for it.
    result = execute(
        _enter_signal(setup=_setup(context={"order_class": "dca"})),
        broker,
        repo,
        _config(),
        mode="autonomous",
        now_ts=NOW_TS,
    )

    assert result.placed is True
    assert result.bracket_order_id is None
    assert [o for o in repo.get_orders(product_id="BTC-USD") if o["side"] == "SELL"] == []


def test_a_vetoed_bracket_places_nothing_and_returns_none(repo):
    """Guards are un-overridable for the bracket exactly as for any other order."""
    repo.set_state("kill_switch", True)
    broker = FakeBroker()

    order_id = place_bracket(
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

    new_id = roll_to_break_even(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        old_stop_order_id=old_id,
        entry_price=Decimal("50000"),
        qty=Decimal("0.01"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS + 100,
    )

    assert new_id is not None and new_id != old_id
    assert repo.get_order(old_id)["status"] == "canceled"
    assert repo.get_state("open_stop:BTC-USD") == Decimal("50000")
    # ORDERING, not presence. `assert broker.cancel_calls` passed under place-then-cancel too,
    # so it did not hold the one regression this design deliberately accepted. The replacement
    # bracket must be placed only AFTER the old one is cancelled, or the exchange would reject
    # it for insufficient funds (the resting bracket commits the whole position).
    assert broker.events == ["place", "cancel", "place"], broker.events
    replacement = broker.place_calls[-1]["spec"]
    assert replacement.take_profit_price == Decimal("53000")  # original target preserved
    assert replacement.stop_trigger_price == Decimal("50000")  # stop moved to break-even


def test_roll_stop_to_rolls_to_an_explicit_policy_computed_level(repo):
    """`roll_stop_to` is the single-roll entry point the agent's live management step drives
    (#502 stage 2). `roll_to_break_even` and `trail_stop_atr` each perform their OWN
    cancel-and-replace; a policy carrying both arms can win on both in one cycle, and calling
    the two named primitives back-to-back would walk the naked-position window (#519's
    cancel-before-place) TWICE for no benefit. The step therefore computes ONE ratcheted level
    (`strategy.exit_policy.next_stop` -- max over the arms, the same function the sim and
    backtester apply) and hands it here.
    """
    broker = FakeBroker()
    old_id = place_bracket(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        qty=Decimal("0.01"),
        stop=Decimal("49000"),
        target=Decimal("54000"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS,
    )

    new_id = roll_stop_to(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        old_stop_order_id=old_id,
        new_stop=Decimal("50750"),
        qty=Decimal("0.01"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS + 100,
    )

    assert new_id is not None and new_id != old_id
    assert repo.get_order(old_id)["status"] == "canceled"
    assert repo.get_state("open_stop:BTC-USD") == Decimal("50750")
    # The #519 protocol, unchanged by the new entry point: cancel BEFORE place.
    assert broker.events == ["place", "cancel", "place"], broker.events
    replacement = broker.place_calls[-1]["spec"]
    assert isinstance(replacement, BracketGTC)
    assert replacement.stop_trigger_price == Decimal("50750")
    assert replacement.take_profit_price == Decimal("54000")


def test_a_roll_repoints_the_owning_tranche_at_the_replacement_bracket(repo):
    """The tranche<->bracket link is how a bracket FILL resolves back to the trade it closed
    (`Repository.get_position_for_bracket`, reconciliation's one lookup direction). Until the
    live management step (#502) rolls were unreachable, so `_roll_stop` never had to maintain
    it; with rolls live, a roll that cancels the bracket a tranche names and does not repoint
    it leaves every LATER fill of the replacement bracket resolving to no tranche -- its
    `trade_outcomes` row is dropped and rail 16 miscounts a managed winner.
    """
    broker = FakeBroker()
    old_id = place_bracket(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        qty=Decimal("0.01"),
        stop=Decimal("49000"),
        target=Decimal("54000"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS,
    )
    position_id = repo.open_position(
        product_id="BTC-USD",
        rule_name="pullback_continuation",
        opened_at=NOW_TS,
        qty=Decimal("0.01"),
        entry_fill=Decimal("50000"),
        entry_fee=Decimal("0"),
        initial_stop=Decimal("49000"),
        bracket_order_id=old_id,
    )

    new_id = roll_stop_to(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        old_stop_order_id=old_id,
        new_stop=Decimal("50750"),
        qty=Decimal("0.01"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS + 100,
    )

    assert new_id is not None
    # The replacement is the tranche's bracket now, and the cancelled order no longer answers.
    assert repo.get_position_for_bracket(new_id) is not None
    assert repo.get_position_for_bracket(new_id)["id"] == position_id
    assert repo.get_position_for_bracket(old_id) is None


def test_a_roll_that_cannot_replace_the_bracket_screams_that_the_position_is_naked(repo, caplog):
    """The cost of cancel-first. If the cancel succeeds and the replacement is then rejected,
    the position is left with NO protective stop. That must never pass quietly."""

    class _RejectingBroker(FakeBroker):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.calls = 0

        def place_order(self, spec, *, idempotency_key=None):  # noqa: ANN001, ANN202
            self.calls += 1
            if self.calls > 1:  # the original bracket places; the replacement fails
                return PlaceResult(success=False, broker_order_id=None, reason="INSUFFICIENT_FUND")
            return super().place_order(spec, idempotency_key=idempotency_key)

    broker = _RejectingBroker()
    old_id = place_bracket(
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

    with caplog.at_level(logging.CRITICAL):
        new_id = roll_to_break_even(
            broker,
            repo,
            _config(),
            product_id="BTC-USD",
            old_stop_order_id=old_id,
            entry_price=Decimal("50000"),
            qty=Decimal("0.01"),
            rule_name="pullback_continuation",
            now_ts=NOW_TS + 100,
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
            return False  # the exchange says: no

    broker = _RefusingBroker()
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

    with pytest.raises(CancelUnavailable, match="REFUSED"):
        roll_to_break_even(
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

    assert repo.get_order(stop_id)["status"] == "pending"


def test_a_legacy_boolean_adapter_still_fails_closed(repo) -> None:
    """An adapter written against the OLD boolean contract must not become more permissive by
    accident. `False` meant "not confirmed, fail closed", and `coerce_cancel_outcome` maps it to
    `REFUSED`, which fails closed identically -- the broker above returns a bare `False` and this
    is the test that says so (#412)."""
    from keel_broker_api.results import CancelOutcome, coerce_cancel_outcome

    assert coerce_cancel_outcome(False) is CancelOutcome.REFUSED
    assert not coerce_cancel_outcome(False).settled
    assert coerce_cancel_outcome(True).settled
    # Anything that is not an outcome and not a bool claims nothing.
    for value in (None, "yes", 1, object()):
        assert not coerce_cancel_outcome(value).settled


def test_a_cancel_the_venue_ACCEPTS_but_has_not_settled_still_stops_the_caller(repo) -> None:
    """The #412 case. `ACCEPTED` is not a failure and must not be logged as one -- but it is also
    not permission to proceed: the order can still consume inventory until the venue settles it,
    and the very next thing both cancel sites do is place another order against that same
    inventory. So the caller is still stopped, by a `CancelPending` that IS-A `CancelUnavailable`
    so every existing handler keeps working unchanged."""
    from keel_broker_api.results import CancelOutcome

    class _AcceptingBroker(FakeBroker):
        def cancel_order(self, order_id: str) -> CancelOutcome:
            self.cancel_calls.append(order_id)
            return CancelOutcome.ACCEPTED

    broker = _AcceptingBroker()
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

    with pytest.raises(CancelPending, match="accepted"):
        roll_to_break_even(
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

    # Still pending, so the reconciliation poll at the top of the next cycle reads the terminal
    # state from the venue -- which is where establishing it belongs.
    assert repo.get_order(stop_id)["status"] == "pending"


def test_cancel_pending_is_caught_by_every_existing_cancel_unavailable_handler() -> None:
    """The subclassing is the whole reason this distinction is safe to introduce on the live
    money path: control flow is provably unchanged, and only the reporting differs."""
    assert issubclass(CancelPending, CancelUnavailable)


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

    result = execute(
        _exit_signal(), broker, repo, _config(), "autonomous", None, now_ts=NOW_TS + 10
    )

    assert result.placed is True
    assert repo.get_order(bracket_id)["status"] == "canceled"
    assert (
        bracket_id in [int(c) if str(c).isdigit() else c for c in broker.cancel_calls]
        or broker.cancel_calls
    ), "the resting bracket was never cancelled at the exchange"


def test_an_exit_cancels_a_PARTIALLY_FILLED_bracket_before_selling(repo):
    """The #446 regression, found in review: pre-#446 a partially-filled bracket stayed
    `pending`, so this cancel-before-SELL sweep -- which queried `status="pending"` only --
    still caught it. The distinct `partially_filled` state took the row OUT of that query,
    and the exit then placed a full-position SELL against base currency the resting remainder
    still commits: rejected on spot for insufficient funds, or filled with a live bracket
    left able to sell inventory we no longer hold.

    A partially-filled bracket is still a RESTING bracket -- its unfilled remainder is
    working at the exchange exactly like a `pending` bracket's whole size -- so it must be
    cancelled before the SELL, same as any other."""
    _seed_filled_buy(repo, qty=Decimal("0.01"), price=Decimal("50000"))
    broker = FakeBroker()
    bracket_id = place_bracket(
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
    # What reconciliation records once the venue has begun executing the bracket (#446): the
    # distinct non-terminal state, the observed fill, the running average price.
    repo.update_order(
        bracket_id,
        status="partially_filled",
        filled_quantity=Decimal("0.004"),
        actual_fill=Decimal("48900"),
        updated_at=NOW_TS + 1,
    )

    result = execute(
        _exit_signal(), broker, repo, _config(), "autonomous", None, now_ts=NOW_TS + 10
    )

    assert result.placed is True
    assert repo.get_order(bracket_id)["status"] == "canceled"
    # CANCEL before the SELL, not merely at some point: the bracket's resting remainder still
    # commits the base, so a SELL placed first is the base-locked rejection this sweep exists
    # to prevent. (Pre-fix the sequence was ["place", "place"] -- no cancel ever issued.)
    assert broker.events == ["place", "cancel", "place"], broker.events


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
    placed_before = len(broker.place_calls)

    result = execute(
        _exit_signal(), broker, repo, _config(), "autonomous", None, now_ts=NOW_TS + 10
    )

    assert result.placed is False
    assert "bracket" in (result.reason or "").lower()
    assert len(broker.place_calls) == placed_before, "a SELL was attempted anyway"


def test_an_entry_does_not_try_to_cancel_anything(repo):
    """Negative control: the bracket-clearing step is EXIT-only. An entry must not touch it."""
    broker = FakeBroker()

    execute(_enter_signal(), broker, repo, _config(), "autonomous", None, now_ts=NOW_TS)

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
        def get_order(self, order_id: str) -> OrderStatus:
            return OrderStatus(
                order_id=order_id,
                status="FILLED",
                filled_size=Decimal("0.001"),
                average_filled_price=Decimal("50123.45"),  # not the expected 50000
                total_fees=Decimal("0.42"),  # not the previewed 0.30
            )

    broker = _ObservingBroker()

    result = execute(_enter_signal(), broker, repo, _config(), "autonomous", None, now_ts=NOW_TS)

    order = repo.get_order(result.order_id)
    assert order["actual_fill"] == Decimal("50123.45")
    assert order["fee"] == Decimal("0.42")


def test_an_unobservable_immediate_fill_keeps_the_estimate_rather_than_failing(repo):
    """Fail SOFT here, unlike the cancel path. We already hold a usable estimate, the order is
    already placed, and raising would abort a cycle over a refinement. The estimate is what
    shipped before this upgrade existed."""

    class _BlindBroker(FakeBroker):
        def get_order(self, order_id: str) -> OrderStatus:
            raise RuntimeError("status endpoint down")

    broker = _BlindBroker()

    result = execute(_enter_signal(), broker, repo, _config(), "autonomous", None, now_ts=NOW_TS)

    assert result.placed is True
    order = repo.get_order(result.order_id)
    assert order["actual_fill"] == Decimal("50000")  # the expected price, as before
    assert order["fee"] == Decimal("0.30")  # the previewed commission, as before


# `test_scale_out_has_no_production_caller` is RETIRED, not weakened, and it is worth recording
# what it was for and why it stopped being true.
#
# It scanned `keel/` for any call to `scale_out` and failed if one appeared. Its stated bar was
# three things a caller would have had to do first: cancel/resize the resting bracket, record a
# `trade_outcomes` row for the partial exit, and not let rail 16 count a scaled-out net winner
# as a loss. All three are discharged in #502 -- and discharged INSIDE `scale_out` rather than
# handed to its caller as obligations, which is the part that makes retiring it safe. A caller
# cannot forget an obligation it does not carry: `scale_out` cancels the bracket itself, books
# the sale itself, and re-places the remainder's bracket itself, behind #519's crash ledger.
#
# What replaces it is `tests/execution/test_scale_out.py` -- fourteen pins over the sequence,
# the two load-bearing ones being that the crash ledger is written before the FIRST venue touch
# and that a scaled-out net winner produces ONE outcome row that rail 16 reads as a win.
#
# What the tripwire also did, incidentally, was assert that nothing in `keel/` drives a
# scale-out. That is STILL true and is not pinned any more, deliberately: it was a statement
# about an unfinished capability, and the capability is finished. Deciding when to take half off
# is rule-side work, and a rule that does it will be reviewed on its own merits rather than by
# tripping a test written about a different problem.


# -- rail 13 guards the PRODUCT's quote leg, not config.quote_currency ----------
#
# The currency an order spends is a property of the product: BTC-USD spends USD whatever
# `config.quote_currency` says. Checking the configured currency instead could PASS an order the
# account cannot fund -- the exact case rail 13 exists to prevent.


def test_ample_configured_currency_does_NOT_fund_a_differently_quoted_product(repo):
    """THE hole. config.quote_currency=USDC with a large USDC balance, zero USD, and a BTC-USD
    order: the old code checked USDC, passed, and let an unfundable order through to the broker.
    """
    broker = FakeBroker(balances={"USDC": Decimal("1000000"), "USD": Decimal("0")})
    signal = _enter_signal()  # BTC-USD -> spends USD

    result = execute(
        signal,
        broker,
        repo,
        _config(quote_currency="USDC"),
        mode="autonomous",
        now_ts=NOW_TS,
    )

    assert result.placed is False, "an order spending USD was funded from a USDC balance"
    assert result.preview is None, "rails must veto before the broker is touched"
    assert any(v.startswith("usdc_funding") for v in result.vetoed_by)
    assert broker.place_calls == []
    assert repo.get_orders() == []


def test_the_products_own_quote_balance_is_what_permits_the_buy(repo):
    """The mirror case, and the false-veto the operator actually hit: funds are in USD, the
    configured currency is empty, and a BTC-USD order should proceed."""
    broker = FakeBroker(balances={"USDC": Decimal("0"), "USD": Decimal("1000000")})
    signal = _enter_signal()

    result = execute(
        signal,
        broker,
        repo,
        _config(quote_currency="USDC"),
        mode="autonomous",
        now_ts=NOW_TS,
    )

    assert result.placed is True, result.vetoed_by


def test_a_product_id_with_no_resolvable_quote_leg_fails_closed(repo):
    broker = FakeBroker(balances={"USD": Decimal("1000000")})
    signal = _enter_signal(product_id="BTCUSD")  # no separator -> unknown quote leg

    result = execute(signal, broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

    assert result.placed is False
    assert any(v.startswith("usdc_funding") for v in result.vetoed_by)


def test_the_veto_message_names_the_currency_actually_required(repo):
    """'insufficient USDC' on a USD-settled order sends the operator to fund the wrong thing."""
    broker = FakeBroker(balances={"USD": Decimal("1"), "USDC": Decimal("1000000")})
    signal = _enter_signal()

    result = execute(
        signal,
        broker,
        repo,
        _config(quote_currency="USDC"),
        mode="autonomous",
        now_ts=NOW_TS,
    )

    message = next(v for v in result.vetoed_by if v.startswith("usdc_funding"))
    # Strip the rail's own lowercase tag before matching currency codes -- otherwise
    # `"USDC" not in message` passes only by accident of the tag's casing.
    body = message.split(":", 1)[1]
    assert "USD" in body
    assert "USDC" not in body, f"message names the wrong currency: {message}"


def test_no_account_at_all_for_the_required_currency_fails_closed(repo):
    """Distinct from a zero balance: the broker returns accounts, none of them the one this
    order settles in. Silence about a currency is not evidence of funds in it."""
    broker = FakeBroker(balances={"EUR": Decimal("1000000")})  # no USD account
    signal = _enter_signal()  # BTC-USD

    result = execute(signal, broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

    assert result.placed is False
    assert result.preview is None
    assert any("unknown/unavailable" in v for v in result.vetoed_by)


# --- _fetch_available_quote failure severity ----------------------------------------------
#
# The second half of the 2026-08-06 log-noise pair: every `get_accounts` failure was logged
# twice at ERROR with a full traceback -- once by `cb_client`, then again here. Rail 13 still
# fails closed (`None`) either way; only the severity of the record changes.


class _UnreachableBroker:
    """A broker whose `get_balances` raises as an offline HTTP stack does."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def get_balances(self) -> list[Balance]:
        raise self._exc


def _quote_failure_payload(caplog, exc: BaseException) -> tuple[Decimal | None, dict]:
    from keel_core import telemetry

    from keel.execution.executor import _fetch_available_quote

    formatter = telemetry.JsonFormatter()
    with caplog.at_level(logging.DEBUG, logger="keel.execution.executor"):
        result = _fetch_available_quote(_UnreachableBroker(exc), "USD")
    records = [r for r in caplog.records if r.getMessage() == "executor.quote_fetch_failed"]
    assert len(records) == 1
    return result, json.loads(formatter.format(records[0]))


def test_the_quote_read_takes_available_and_never_total() -> None:
    """**Rail 13 is about SPENDABLE funds, and `Balance` carries two numbers that differ.**

    `available` is what the venue will let an order draw on; `total` includes funds on hold --
    settling proceeds, collateral behind a resting order. Reading `total` would let rail 13 pass
    an order the account cannot actually fund, which is the precise failure the rail exists to
    prevent, and it would do it silently because both numbers are plausible balances.

    Pinned with a balance whose two figures DIFFER. Every other fake in this file sets them
    equal, so before this test existed, swapping `.available` for `.total` in
    `_fetch_available_quote` passed the entire suite -- verified by making that change.
    """
    from keel.execution.executor import _fetch_available_quote

    class _OnHold:
        def get_balances(self) -> list[Balance]:
            # 900 of the 1000 is on hold: spendable is 100.
            return [Balance(currency="USD", available=Decimal("100"), total=Decimal("1000"))]

    assert _fetch_available_quote(_OnHold(), "USD") == Decimal("100")


def test_the_quote_read_matches_the_currency_case_insensitively() -> None:
    """Venues disagree about casing and the product's quote leg is derived from a product id.
    A `usd` row must satisfy a `USD` question -- the same comparison `gather_holdings` makes."""
    from keel.execution.executor import _fetch_available_quote

    class _Lowercase:
        def get_balances(self) -> list[Balance]:
            return [Balance(currency="usd", available=Decimal("42"), total=Decimal("42"))]

    assert _fetch_available_quote(_Lowercase(), "USD") == Decimal("42")


def test_the_quote_read_answers_none_when_the_currency_has_no_account() -> None:
    """`None` means UNKNOWN and rail 13 fails closed on it. In the port's model a currency with
    no account simply is not in the list -- there is no row carrying a null balance to inspect."""
    from keel.execution.executor import _fetch_available_quote

    class _NoUsd:
        def get_balances(self) -> list[Balance]:
            return [Balance(currency="EUR", available=Decimal("500"), total=Decimal("500"))]

    assert _fetch_available_quote(_NoUsd(), "USD") is None


def test_quote_fetch_logs_an_unreachable_venue_as_a_warning(caplog) -> None:
    exc = type("ConnectionError", (Exception,), {})("api.coinbase.com unreachable")

    result, payload = _quote_failure_payload(caplog, exc)

    assert result is None  # rail 13 still fails closed
    assert payload["level"] == "WARNING"
    assert payload["unreachable"] is True
    assert "exc" not in payload
    assert payload["quote_currency"] == "USD"


def test_quote_fetch_keeps_a_real_broker_error_at_error_with_its_traceback(caplog) -> None:
    exc = type("HTTPError", (Exception,), {})("401 Client Error: Unauthorized")

    result, payload = _quote_failure_payload(caplog, exc)

    assert result is None
    assert payload["level"] == "ERROR"
    assert "Traceback" in payload["exc"]


def test_a_bracket_that_could_not_be_placed_records_the_levels_it_failed_at(repo):
    """The entry has already filled by the time the bracket is attempted, so a refusal leaves a
    real position with no stop at the exchange. `open_stop`/`open_target` are deliberately NOT
    written here -- they mean "this is resting at the exchange" and rail 9 reads them as its
    no-widening reference. The retry needs the levels anyway, so they go somewhere of their own.

    Without this record there is nothing to retry FROM: reconcile refuses to invent a stop, and
    inventing one would re-risk the position on a level no rule produced (issue #195).
    """
    broker = FakeBroker(place_success=False)

    order_id = place_bracket(
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

    assert order_id is None
    assert repo.get_state("open_stop:BTC-USD") is None
    assert repo.get_state("open_target:BTC-USD") is None
    assert repo.get_state("unbracketed:BTC-USD") == {
        "stop": Decimal("49000"),
        "target": Decimal("53000"),
        "qty": Decimal("0.01"),
    }


def test_a_placed_bracket_clears_an_earlier_unprotected_record(repo):
    """A retry that succeeds must retire the trigger that drove it, or the sweep re-places a
    bracket the position already holds on every subsequent cycle."""
    repo.set_state(
        "unbracketed:BTC-USD",
        {
            "stop": Decimal("49000"),
            "target": Decimal("53000"),
            "qty": Decimal("0.01"),
        },
    )

    place_bracket(
        FakeBroker(),
        repo,
        _config(),
        product_id="BTC-USD",
        qty=Decimal("0.01"),
        stop=Decimal("49000"),
        target=Decimal("53000"),
        rule_name="pullback_continuation",
        now_ts=NOW_TS,
    )

    assert repo.get_state("unbracketed:BTC-USD") is None


def _divergence_fields(caplog) -> dict:
    """The structured payload of the last `executor.intent_divergence` record.

    `log_event` attaches fields via `extra`, not the message, so `caplog.text` shows only the
    event name -- asserting on it would pass for any values at all.
    """
    from keel_core.telemetry import _FIELDS_ATTR

    records = [r for r in caplog.records if r.getMessage() == "executor.intent_divergence"]
    assert records, "no executor.intent_divergence record was emitted"
    return getattr(records[-1], _FIELDS_ATTR)


class TestIntentDivergenceLog:
    """#260 mitigation: the executor records the rule's intended entry and the achieved fill
    side by side and never compared them. That missing subtraction is how #257 stayed invisible.
    """

    @staticmethod
    def _intent(entry: str = "50000") -> OrderIntent:
        return OrderIntent(
            product_id="BTC-USD",
            side=Side.BUY,
            qty=Decimal("0.001"),
            entry=Decimal(entry),
            stop=Decimal("49000"),
            notional=Decimal("50"),
            is_dca=False,
            rule_kind="pullback_continuation",
        )

    def test_divergence_is_logged_with_signed_basis_points(self, caplog) -> None:
        from keel.execution.executor import _log_intent_divergence

        with caplog.at_level(logging.INFO):
            # Filled 50 above a 50,000 intent -> +10.00 bps.
            _log_intent_divergence(order_id=7, intent=self._intent(), realized=Decimal("50050"))

        assert _divergence_fields(caplog)["divergence_bps"] == "10.00"

    def test_divergence_is_signed_so_direction_is_legible(self, caplog) -> None:
        """Negative means the fill came in BELOW the rule's intended entry.

        Signedness is the point: an unsigned magnitude cannot distinguish "we paid up" from "we
        got filled cheaper", and for a rule whose entry encodes a confirmation condition those
        are opposite failures.
        """
        from keel.execution.executor import _log_intent_divergence

        with caplog.at_level(logging.INFO):
            _log_intent_divergence(order_id=8, intent=self._intent(), realized=Decimal("49950"))

        assert _divergence_fields(caplog)["divergence_bps"] == "-10.00"

    def test_logged_unconditionally_even_when_divergence_is_zero(self, caplog) -> None:
        """No threshold, deliberately.

        A basis-point cutoff right for BTC is wrong for a thin book, and the per-asset liquidity
        model that would set one does not exist yet (#259). Gating this on that work would block
        the cheap half behind the expensive half -- so every fill reports, exactly as #247 made
        the fee rate report on every backtest.
        """
        from keel.execution.executor import _log_intent_divergence

        with caplog.at_level(logging.INFO):
            _log_intent_divergence(order_id=9, intent=self._intent(), realized=Decimal("50000"))

        fields = _divergence_fields(caplog)
        assert fields["divergence_bps"] == "0.00"
        assert fields["product"] == "BTC-USD"

    def test_never_raises_on_unusable_input(self, caplog) -> None:
        """Telemetry on an already-settled order must not be able to fail a cycle."""
        from keel.execution.executor import _log_intent_divergence

        with caplog.at_level(logging.INFO):
            _log_intent_divergence(order_id=10, intent=None, realized=Decimal("50000"))
            _log_intent_divergence(order_id=11, intent=self._intent("0"), realized=Decimal("1"))
            _log_intent_divergence(order_id=12, intent=self._intent(), realized="not-a-number")


# -- #260: the routing-time entry-override warning --------------------------------------------


#: The stable event id the routing-time warning emits -- a name, never a sentence, per
#: `keel_core.telemetry`'s contract, so tests (and any aggregation) key on it.
_OVERRIDE_EVENT = "executor.entry_override_market_routed"


def _override_fields(caplog) -> dict:
    """The structured payload of the last `executor.entry_override_market_routed` record.

    Same shape as `_divergence_fields` above: `log_event` attaches fields via `extra`, so
    `caplog.text` shows only the event name and asserting on it would pass for any values.
    """
    from keel_core.telemetry import _FIELDS_ATTR

    records = [r for r in caplog.records if r.getMessage() == _OVERRIDE_EVENT]
    assert records, f"no {_OVERRIDE_EVENT} record was emitted"
    return getattr(records[-1], _FIELDS_ATTR)


def _quoted_preview(best_ask: str) -> dict[str, Any]:
    """A `CoinbaseClient.preview_order`-shaped dict carrying the venue's own ASK side only.

    The real client maps `best_bid`/`best_ask` to `Decimal` when the venue returns them. This
    helper carries only the ask -- everything the #332 warning reads -- so it doubles as the
    half-readable shape #350's spread gate must treat as `book_unreadable` while the warning
    still reads its reference (a preview with no book is not an error, it is just not a
    spread).
    """
    return {
        "order_total": Decimal("50.00"),
        "commission_total": Decimal("0.30"),
        "errs": [],
        "warning": [],
        "best_ask": Decimal(best_ask),
    }


def _quoted_quote(intent: Any, best_ask: str) -> Preview:
    """`_quoted_preview`'s payload as the port's `Preview` -- the ONE shape the warning and the
    spread gate read since #524 deleted their dict arms. Same `detail`-as-strings the real
    adapter carries, so the safety paths exercise the same parsing they do live."""
    return _preview_from(intent, _quoted_preview(best_ask))


def test_preview_book_reads_the_ports_shape_only() -> None:
    """#524's deletion proof: the dict arm of `_preview_book` is gone. A dict is no longer a
    shape anything on the live path produces -- every constructible broker answers
    `preview_order` with the port's `Preview` -- so the helper must read NO book out of one,
    fail-closed exactly like a preview whose `detail` carries no sides."""
    from keel.execution.executor import _preview_book

    legacy_dict = _quoted_preview("50000")
    assert _preview_book(legacy_dict) == (None, None)


class TestEntryOverrideWarningAtRouting:
    """#260's minimum viable mitigation, at ROUTING time (the divergence class above reports
    after the fill; this warns before/at placement).

    Every entry is routed market (`_order_configuration` -> `market_market_ioc`), so a rule
    whose `Setup.entry` encodes a CONDITION -- `pullback_continuation` demands follow-through
    via `signal_candle.high + buffer_ticks` -- has that condition silently bypassed in
    production. The warning makes the override visible using the one market price already in
    the hot path: the `best_ask` the executor's own `preview_order` call just returned.
    """

    @staticmethod
    def _intent(entry: str = "50000") -> OrderIntent:
        return OrderIntent(
            product_id="BTC-USD",
            side=Side.BUY,
            qty=Decimal("0.001"),
            entry=Decimal(entry),
            stop=Decimal("49000"),
            notional=Decimal("50"),
            is_dca=False,
            rule_kind="pullback_continuation",
        )

    def test_routing_an_offset_entry_warns_loudly_at_warning_level(self, repo, caplog) -> None:
        """The pullback case: entry ABOVE the market by more than the threshold.

        50,300 intended against a 50,000 ask is +60bp -- beyond `ENTRY_OVERRIDE_WARN_BP` -- so
        the order the rule meant to make conditional is about to go out unconditional, and the
        log must say so at WARNING (loud, not the divergence class's after-the-fact INFO).
        """
        broker = FakeBroker(preview=_quoted_preview("50000"))
        signal = _enter_signal(_setup(entry=Decimal("50300")))

        with caplog.at_level(logging.WARNING):
            execute(signal, broker, repo, _config(), "autonomous", now_ts=NOW_TS)

        fields = _override_fields(caplog)
        assert fields["rule"] == "pullback_continuation"
        assert fields["product"] == "BTC-USD"
        assert fields["expected_fill"] == "50300"
        assert fields["market_ref"] == "50000"
        assert fields["deviation_bps"] == "60.00"
        assert fields["market_ref_source"] == "preview_best_ask"
        # The sentence is the point: an operator must read WHAT was overridden, not just that
        # a number differed -- the event id alone cannot say "your rule's design was bypassed".
        assert "OVERRIDDEN" in fields["detail"]
        assert "market" in fields["detail"]
        records = [r for r in caplog.records if r.getMessage() == _OVERRIDE_EVENT]
        assert records[-1].levelno == logging.WARNING

    def test_a_deviation_within_the_threshold_is_silent(self, repo, caplog) -> None:
        """A warning that fires every order is a warning nobody reads.

        50,010 against a 50,000 ask is +2bp -- the microstructure drift any enter-at-close rule
        (`turtle_breakout`, `rsi_meanrev`) accumulates by routing one cycle after its signal
        bar. That is noise, and noise must not train the operator to skip this line.
        """
        broker = FakeBroker(preview=_quoted_preview("50000"))
        signal = _enter_signal(_setup(entry=Decimal("50010")))

        with caplog.at_level(logging.WARNING):
            execute(signal, broker, repo, _config(), "autonomous", now_ts=NOW_TS)

        assert not [r for r in caplog.records if r.getMessage() == _OVERRIDE_EVENT]

    def test_exactly_at_the_threshold_does_not_warn(self, caplog) -> None:
        """The boundary is pinned: strictly greater than `ENTRY_OVERRIDE_WARN_BP` warns.

        `>` rather than `>=` so an operator comparing a logged deviation against the documented
        threshold reads "warned" as "beyond", never "at". Computed FROM the constant so this
        test keeps pinning the boundary if the constant is ever retuned.
        """
        from keel.execution.executor import (
            ENTRY_OVERRIDE_WARN_BP,
            _warn_if_market_routing_overrides_entry,
        )

        market = Decimal("50000")
        at_the_line = market * (Decimal(1) + ENTRY_OVERRIDE_WARN_BP / Decimal(10_000))

        at_the_line_intent = self._intent(entry=str(at_the_line))
        with caplog.at_level(logging.WARNING):
            _warn_if_market_routing_overrides_entry(
                at_the_line_intent, _quoted_quote(at_the_line_intent, "50000")
            )

        assert not [r for r in caplog.records if r.getMessage() == _OVERRIDE_EVENT]

    def test_entry_below_market_warns_with_a_negative_sign(self, caplog) -> None:
        """The other direction: a rule whose entry rests BELOW the market (a limit at support).

        49,700 intended against a 50,000 ask is -60bp. Signed so direction is legible without
        recomputing: positive = the rule demanded follow-through ABOVE the market (pullback's
        case), negative = it wanted a dip the market has not offered.
        """
        from keel.execution.executor import _warn_if_market_routing_overrides_entry

        below = self._intent(entry="49700")
        with caplog.at_level(logging.WARNING):
            _warn_if_market_routing_overrides_entry(below, _quoted_quote(below, "50000"))

        fields = _override_fields(caplog)
        assert fields["deviation_bps"] == "-60.00"
        assert fields["expected_fill"] == "49700"

    def test_a_preview_without_a_book_quote_is_silent_not_fatal(self, caplog) -> None:
        """No `best_ask`, no honest reference -- and a warning built on a guess would be noise.

        A preview with no bid/ask keys models a degraded venue response; the warning must be
        silent and the cycle must proceed exactly as before this warning existed. (Constructed
        explicitly rather than borrowed from `FakeBroker`'s default, which -- since #350's
        spread gate made a bookless live BUY a REFUSAL -- models the real venue and carries a
        book.)
        """
        from keel.execution.executor import _warn_if_market_routing_overrides_entry

        bookless = {
            "order_total": Decimal("50.00"),
            "commission_total": Decimal("0.30"),
            "errs": [],
            "warning": [],
        }
        with caplog.at_level(logging.WARNING):
            _warn_if_market_routing_overrides_entry(
                self._intent(entry="50300"), _preview_from(self._intent(), bookless)
            )
            _warn_if_market_routing_overrides_entry(
                self._intent(entry="50300"),
                _preview_from(
                    self._intent(), {**_quoted_preview("50000"), "best_ask": "not-a-number"}
                ),
            )
            # "nan" PARSES -- Decimal('NaN') constructs fine, and NaN > 0 raises
            # InvalidOperation. cb_client does Decimal(value) on venue strings with no
            # finiteness check, so this input is reachable; telemetry must swallow it,
            # not abort the routing (the sibling intent_divergence path guards the same
            # hazard inside its try).
            _warn_if_market_routing_overrides_entry(
                self._intent(entry="50300"),
                _preview_from(self._intent(), {**_quoted_preview("50000"), "best_ask": "nan"}),
            )
            # A zero/unusable intended entry has no meaningful deviation either.
            _warn_if_market_routing_overrides_entry(
                self._intent(entry="0"), _quoted_quote(self._intent(), "50000")
            )
            # An extreme-but-finite exponent (a rule bug, not venue data): parses, is_finite,
            # and compares fine -- the DIVISION is what raises (Decimal Overflow, an
            # ArithmeticError). Telemetry must swallow it, matching intent_divergence's
            # inside-the-try arithmetic.
            _warn_if_market_routing_overrides_entry(
                self._intent(entry="1E+999999999"), _quoted_preview("50000")
            )

        assert not [r for r in caplog.records if r.getMessage() == _OVERRIDE_EVENT]

    def test_the_port_preview_shape_is_read_too(self, caplog) -> None:
        """`preview` arrives as the port's `Preview` once Phase B migrates the call, and the
        Coinbase adapter carries the same book in `detail` -- the warning must survive the
        migration (values are strings there, not Decimals)."""
        from keel_broker_api.results import Preview

        from keel.execution.executor import _warn_if_market_routing_overrides_entry

        preview = Preview(
            product_id="BTC-USD",
            side=Side.BUY,
            est_base_size=Decimal("0.001"),
            est_quote_size=Decimal("50"),
            est_fee=Decimal("0.30"),
            synthetic=False,
            detail={"best_ask": "50000"},
        )

        with caplog.at_level(logging.WARNING):
            _warn_if_market_routing_overrides_entry(self._intent(entry="50300"), preview)

        assert _override_fields(caplog)["market_ref"] == "50000"

    def test_sell_intents_never_warn(self, caplog) -> None:
        """Only the ENTRY routing is the override. A SELL intent's `entry` is a trigger or an
        average cost, and the bracket/scale-out configurations carry their prices to the venue
        verbatim -- warning there would be noise about orders that were NOT overridden."""
        from keel.execution.executor import _warn_if_market_routing_overrides_entry

        sell_intent = OrderIntent(
            product_id="BTC-USD",
            side=Side.SELL,
            qty=Decimal("0.001"),
            entry=Decimal("40000"),  # 20,000bp off the ask -- deliberately absurd
            stop=None,
            notional=Decimal("50"),
            is_dca=False,
            rule_kind="position_rule",
        )

        with caplog.at_level(logging.WARNING):
            _warn_if_market_routing_overrides_entry(
                sell_intent, _quoted_quote(sell_intent, "50000")
            )

        assert not [r for r in caplog.records if r.getMessage() == _OVERRIDE_EVENT]

    def test_an_explicitly_non_market_configuration_never_warns(self, caplog) -> None:
        """A caller that passes its own order configuration (the bracket, a stop roll, and any
        FUTURE resting-entry routing from #260's remediation plan) is not on the
        market-override path -- its prices reach the venue, and this warning must not fire."""
        from keel.execution.executor import _warn_if_market_routing_overrides_entry

        # A resting LIMIT spec, not the market routing -- the override warning is scoped to
        # market orders, and a caller passing its own non-market spec is not on that path.
        resting = LimitGTC(
            product_id="BTC-USD",
            side=Side.BUY,
            base_size=Decimal("0.001"),
            limit_price=Decimal("50300"),
        )

        with caplog.at_level(logging.WARNING):
            _warn_if_market_routing_overrides_entry(
                self._intent(entry="50300"),
                _quoted_quote(self._intent(entry="50300"), "50000"),
                resting,
            )

        assert not [r for r in caplog.records if r.getMessage() == _OVERRIDE_EVENT]


# -- #350: the routing-time maximum-spread entry gate -------------------------------------------


#: The two stable event ids the spread gate emits -- names, never sentences, per
#: `keel_core.telemetry`'s contract, so tests (and any aggregation) key on them.
_SPREAD_REFUSED_EVENT = "executor.entry_spread_refused"
_BOOK_UNREADABLE_EVENT = "executor.entry_book_unreadable"

#: The `vetoed_by` reason strings the gate records on `ExecutionResult` -- deliberately the
#: same "one legible token" shape `GuardResult.violations` uses for rail vetoes.
SPREAD_GATE_VETO = "max_entry_spread"
BOOK_UNREADABLE_VETO = "book_unreadable"


def _gate_fields(caplog, event: str) -> dict:
    """The structured payload of the last `event` record -- same rationale as
    `_override_fields`: `log_event` attaches fields via `extra`, so `caplog.text` shows only
    the event name and asserting on it would pass for any values."""
    from keel_core.telemetry import _FIELDS_ATTR

    records = [r for r in caplog.records if r.getMessage() == event]
    assert records, f"no {event} record was emitted"
    return getattr(records[-1], _FIELDS_ATTR)


def _book_preview(best_bid: Decimal | str, best_ask: Decimal | str) -> dict[str, Any]:
    """A `CoinbaseClient.preview_order`-shaped dict carrying BOTH sides of the venue's book.

    Like `_quoted_preview` above, but with `best_bid` too: the spread gate needs both sides
    (the #332 warning reads only the ask). Values are passed through VERBATIM -- `Decimal` for
    the good path (what the real client maps venue strings to), raw strings for the degraded
    cases (`"nan"`, `"not-a-number"`), which the port's `Preview.detail` can carry un-parsed.
    """
    return {
        "order_total": Decimal("50.00"),
        "commission_total": Decimal("0.30"),
        "errs": [],
        "warning": [],
        "best_bid": best_bid,
        "best_ask": best_ask,
    }


class TestMaxSpreadEntryGate:
    """#350: a live BUY whose previewed book is too wide is REFUSED at routing time.

    The gate runs AFTER `guards.check` and AFTER the preview (guards are broker-less by
    design; the book exists only in `broker.preview_order`'s result -- the same preview
    #332's warning reads), and BEFORE the confirm gate and placement. SELLs are never gated
    (exits must execute -- the same principle that makes rail 17 halt entries only), and
    paper mode never runs it (paper fills are synthetic and see no book, which is exactly why
    the paper-hourly profile accrues NO evidence about this gate).

    Default threshold under test: `execution.max_entry_spread_pct` = 0.005 (50bp), set by #334
    to equal `SLIPPAGE_CAP_PCT` and left at 50bp when #523 moved that cap to 183.8bp -- so the
    gate is now the stricter of the two, refusing books the backtest would price. If the spread
    ALONE costs a whole leg, the fill economics are materially worse than modeled.
    """

    def test_a_wide_book_refuses_the_live_buy_before_any_placement(self, repo, caplog) -> None:
        """50,000 bid / 50,300 ask is a 59.8bp spread -- beyond the 50bp default -- so the
        entry is refused at routing: no `place_order`, no order row, the refusal recorded in
        `vetoed_by` with the gate's own reason token, and a WARNING carrying the measured
        spread, the threshold and the product."""
        broker = FakeBroker(preview=_book_preview(Decimal("50000"), Decimal("50300")))
        signal = _enter_signal(_setup(entry=Decimal("50150")))  # at the mid: the #332
        # warning below must stay silent so this test isolates the GATE.

        with caplog.at_level(logging.WARNING):
            result = execute(signal, broker, repo, _config(), "autonomous", now_ts=NOW_TS)

        assert result.placed is False
        assert result.vetoed_by == [SPREAD_GATE_VETO]
        assert result.order_id is None
        assert broker.place_calls == []
        assert repo.get_orders() == []
        fields = _gate_fields(caplog, _SPREAD_REFUSED_EVENT)
        assert fields["product"] == "BTC-USD"
        assert fields["best_bid"] == "50000"
        assert fields["best_ask"] == "50300"
        assert fields["spread_pct"] == "0.005982053838484546360917248255"
        assert fields["threshold_pct"] == "0.005"
        assert fields["veto"] == SPREAD_GATE_VETO

    def test_a_tight_book_places_and_the_332_warning_stays_independent(self, repo, caplog) -> None:
        """10bp spread passes the gate. The #332 warning is a SEPARATE consumer of the same
        book: an entry materially off the ask still warns (and places), and an entry at the
        market stays silent -- the gate changes neither behavior."""
        tight = FakeBroker(preview=_book_preview(Decimal("50000"), Decimal("50010")))

        with caplog.at_level(logging.WARNING):
            placed = execute(
                _enter_signal(_setup(entry=Decimal("50005"))),
                tight,
                repo,
                _config(),
                "autonomous",
                now_ts=NOW_TS,
            )

        assert placed.placed is True
        assert placed.vetoed_by == []
        assert not [r for r in caplog.records if r.getMessage() == _SPREAD_REFUSED_EVENT]
        assert not [r for r in caplog.records if r.getMessage() == _OVERRIDE_EVENT]

        # Same tight book, entry 58bp ABOVE the ask: the #332 warning fires, the gate still
        # passes, and the order places -- one book, two independent consumers.
        with caplog.at_level(logging.WARNING):
            warned = execute(
                _enter_signal(_setup(entry=Decimal("50300"))),
                FakeBroker(preview=_book_preview(Decimal("50000"), Decimal("50010"))),
                repo,
                _config(),
                "autonomous",
                now_ts=NOW_TS,
            )

        assert warned.placed is True
        assert _override_fields(caplog)["market_ref"] == "50010"
        assert not [r for r in caplog.records if r.getMessage() == _SPREAD_REFUSED_EVENT]

    def test_a_spread_exactly_at_the_threshold_is_refused(self, repo) -> None:
        """The boundary is pinned: >= refuses, a hair under passes.

        49,000/51,000 is a 2,000-wide book on a 50,000 mid -- exactly 0.04. AT the line the
        spread alone already costs more per leg than the model assumes for a liquid book,
        leaving the taker fee wholly outside it, so "at" is already too wide -- the fail-closed
        side of the line, unlike #332's visibility-only strictly-greater.
        """
        from keel.config import ExecutionConfig

        at_the_line = _config(execution=ExecutionConfig(max_entry_spread_pct=Decimal("0.04")))
        broker = FakeBroker(preview=_book_preview(Decimal("49000"), Decimal("51000")))
        result = execute(
            _enter_signal(_setup(entry=Decimal("50000"))),
            broker,
            repo,
            at_the_line,
            "autonomous",
            now_ts=NOW_TS,
        )
        assert result.placed is False
        assert result.vetoed_by == [SPREAD_GATE_VETO]
        assert broker.place_calls == []

        a_hair_under = _config(execution=ExecutionConfig(max_entry_spread_pct=Decimal("0.0401")))
        broker = FakeBroker(preview=_book_preview(Decimal("49000"), Decimal("51000")))
        result = execute(
            _enter_signal(_setup(entry=Decimal("50000"))),
            broker,
            repo,
            a_hair_under,
            "autonomous",
            now_ts=NOW_TS,
        )
        assert result.placed is True
        assert result.vetoed_by == []

    def test_a_sell_with_a_monstrous_spread_is_never_gated(self, repo, caplog) -> None:
        """Exits must execute: a SELL through the same preview/place pipeline sees a 400bp
        spread and places anyway. Trapping an exit in a wide book would strand the position
        exactly when the rule says leave."""
        broker = FakeBroker(preview=_book_preview(Decimal("48000"), Decimal("50000")))  # 400bp
        # `scale_out` is used here only because it is the shortest SELL through this pipeline.
        # Since #502 it resizes a bracket, so it needs a held, bracketed position to scale out
        # of; the setup below is the state, not the subject.
        _seed_open_position(repo, "BTC-USD", Decimal("0.002"), Decimal("50000"))
        place_bracket(
            broker,
            repo,
            _config(),
            product_id="BTC-USD",
            qty=Decimal("0.002"),
            stop=Decimal("49000"),
            target=Decimal("53000"),
            rule_name="position_rule",
            now_ts=NOW_TS,
        )
        broker.place_calls.clear()

        with caplog.at_level(logging.WARNING):
            result = scale_out(
                broker,
                repo,
                _config(),
                product_id="BTC-USD",
                qty=Decimal("0.001"),
                exit_price=Decimal("50000"),
                rule_name="position_rule",
                now_ts=NOW_TS,
            )

        assert result.placed is True
        assert result.vetoed_by == []
        # The partial SELL and the remainder's replacement bracket; neither was spread-gated.
        assert len(broker.place_calls) == 2
        assert not [r for r in caplog.records if r.getMessage() == _SPREAD_REFUSED_EVENT]
        assert not [r for r in caplog.records if r.getMessage() == _BOOK_UNREADABLE_EVENT]

    def test_an_unreadable_book_refuses_the_live_buy_with_a_distinct_reason(
        self, repo, caplog
    ) -> None:
        """Fail-closed: a live BUY whose preview carries no readable bid/ask is refused with
        `book_unreadable` -- missing keys (the degraded venue shape), a NaN or non-numeric
        side, or a non-positive one. The gate must not guess a spread, and must say loudly
        WHY it refused, distinguishing 'too wide' from 'cannot know'."""
        bookless = FakeBroker(
            preview={
                "order_total": Decimal("50.00"),
                "commission_total": Decimal("0.30"),
                "errs": [],
                "warning": [],
                # no best_bid/best_ask keys at all -- the degraded response shape
            }
        )

        with caplog.at_level(logging.WARNING):
            result = execute(
                _enter_signal(), bookless, repo, _config(), "autonomous", now_ts=NOW_TS
            )

        assert result.placed is False
        assert result.vetoed_by == [BOOK_UNREADABLE_VETO]
        assert bookless.place_calls == []
        fields = _gate_fields(caplog, _BOOK_UNREADABLE_EVENT)
        assert fields["product"] == "BTC-USD"
        assert fields["veto"] == BOOK_UNREADABLE_VETO

        # Each individual way a side can be unreadable, through the same `execute` path.
        for bad in ("nan", "not-a-number", "0", "-50000"):
            broker = FakeBroker(preview=_book_preview(bad, "50000"))
            with caplog.at_level(logging.WARNING):
                refused = execute(
                    _enter_signal(), broker, repo, _config(), "autonomous", now_ts=NOW_TS
                )
            assert refused.placed is False, f"bid={bad!r} must be unreadable, not traded"
            assert refused.vetoed_by == [BOOK_UNREADABLE_VETO]
            assert broker.place_calls == []

        broker = FakeBroker(preview=_book_preview("50000", "nan"))
        with caplog.at_level(logging.WARNING):
            refused = execute(_enter_signal(), broker, repo, _config(), "autonomous", now_ts=NOW_TS)
        assert refused.vetoed_by == [BOOK_UNREADABLE_VETO]
        assert broker.place_calls == []

    def test_the_port_preview_shape_is_gated_too(self, repo, caplog) -> None:
        """`Preview` (Phase B's shape) carries the book as strings inside `detail`; the gate
        must read it the same way #332's warning does, or the migration would silently disarm
        the gate."""
        from keel_broker_api.results import Preview

        preview = Preview(
            product_id="BTC-USD",
            side=Side.BUY,
            est_base_size=Decimal("0.001"),
            est_quote_size=Decimal("50"),
            est_fee=Decimal("0.30"),
            synthetic=False,
            detail={"best_bid": "50000", "best_ask": "50300"},
        )
        broker = FakeBroker()
        broker.preview_order = lambda *a, **k: preview  # type: ignore[method-assign]

        with caplog.at_level(logging.WARNING):
            result = execute(_enter_signal(), broker, repo, _config(), "autonomous", now_ts=NOW_TS)

        assert result.placed is False
        assert result.vetoed_by == [SPREAD_GATE_VETO]
        assert broker.place_calls == []

    def test_extreme_magnitude_books_are_refused_not_swallowed(self, repo, caplog) -> None:
        """The arithmetic arm of fail-closed: a book whose sides PARSE, are finite, and are
        positive can still make the spread itself uncomputable. Decimal admits extreme
        exponents (`1E+999999999` constructs and compares fine -- #336's lesson, mirrored by
        #332's sibling test above), and `bid + ask` on such magnitudes raises Overflow; a
        subnormal pair can round the mid to zero and raise DivisionByZero on the divide.
        #332's warning SWALLOWS both (telemetry must never fail a routing); a money gate
        must REFUSE on them -- an uncomputable spread is an unreadable book, never an abort
        and never a pass."""
        huge = FakeBroker(preview=_book_preview(Decimal("1E+999999999"), Decimal("9E+999999999")))

        with caplog.at_level(logging.WARNING):
            result = execute(_enter_signal(), huge, repo, _config(), "autonomous", now_ts=NOW_TS)

        assert result.placed is False
        assert result.vetoed_by == [BOOK_UNREADABLE_VETO]
        assert "spread uncomputable" in (result.reason or "")
        assert huge.place_calls == []

        # A subnormal pair: the sum rounds to one ulp, half of which half-even rounds the mid
        # to ZERO, while the difference survives as nonzero -- so `(ask - bid) / mid` raises
        # DivisionByZero. Both raises are ArithmeticErrors; both must land in the same
        # fail-closed arm, refusing (not crashing) exactly like the overflow case.
        subnormal = FakeBroker(
            preview=_book_preview(Decimal("4E-1000028"), Decimal("1.44E-1000026"))
        )

        with caplog.at_level(logging.WARNING):
            refused = execute(
                _enter_signal(), subnormal, repo, _config(), "autonomous", now_ts=NOW_TS
            )

        assert refused.placed is False
        assert refused.vetoed_by == [BOOK_UNREADABLE_VETO]
        assert "spread uncomputable" in (refused.reason or "")
        assert subnormal.place_calls == []
        fields = _gate_fields(caplog, _BOOK_UNREADABLE_EVENT)
        assert fields["product"] == "BTC-USD"
        assert fields["veto"] == BOOK_UNREADABLE_VETO

    def test_a_wide_book_refuses_in_confirm_mode_without_ever_consulting_the_approver(
        self, repo
    ) -> None:
        """Gate BEFORE confirm, pinned: every other gate test runs autonomous, which never
        exercises the ordering. Here a wide-book BUY runs in `mode="confirm"` with an
        approver attached -- and is STILL refused, with the approver NEVER consulted: the
        spread gate sits upstream of the confirm gate in `_run_order`, so a human (or any
        approving `confirm_fn`) cannot approve around a book the gate judged too wide. A
        gate an operator could overrule is a suggestion, not a gate."""
        consulted: list[dict] = []

        def _approve(preview) -> bool:  # would approve -- and must never get the chance
            consulted.append(preview)
            return True

        broker = FakeBroker(preview=_book_preview(Decimal("50000"), Decimal("50300")))

        result = execute(
            _enter_signal(_setup(entry=Decimal("50150"))),  # at the mid: isolate the GATE
            broker,
            repo,
            _config(),
            mode="confirm",
            confirm_fn=_approve,
            now_ts=NOW_TS,
        )

        assert result.placed is False
        assert result.vetoed_by == [SPREAD_GATE_VETO]
        assert broker.place_calls == []
        assert repo.get_orders() == []
        assert consulted == []


def test_unserialisable_size_refuses_the_order_without_raising(repo, monkeypatch):
    """#513: a size the venue's units cannot express refuses THIS order, and only this order.

    `agent.run_once` does not wrap its `executor.execute` call, so an exception escaping here
    would abort the whole cycle and skip every product after it -- turning one unserialisable
    order into a silent outage. Refuse-and-log is what every other unknown in this engine does.

    Reached by monkeypatching the increment lookup rather than by contriving a config: rails 1
    and 18 already confine live orders to the allowlist and the configured settlement currency,
    so an unknown quote currency cannot reach serialisation in practice. This path is
    defence-in-depth, and it still must not be the thing that crashes.
    """
    monkeypatch.setattr(sizing, "quote_increment_for", lambda _product_id: None)
    broker = FakeBroker()

    result = execute(
        _enter_signal(), broker, repo, _config(), mode="confirm", confirm_fn=_approve, now_ts=NOW_TS
    )

    assert result.placed is False
    assert "size precision unavailable" in result.reason
    assert broker.place_calls == []
    assert broker.preview_calls == []
    assert repo.get_orders() == []


def test_a_roll_writes_its_crash_ledger_before_touching_the_venue(repo, monkeypatch):
    """#519: the window between cancel and replace must never be SILENT.

    Before this, `_roll_stop` cancelled the old bracket and then placed the replacement without
    ever writing an `unbracketed:` record -- `place_bracket` writes one only on a refused
    PLACEMENT. A process dying in between left no resting bracket and no intent, so
    `reconcile_unbracketed_positions` took the branch that exists for DCA and skipped the position
    SILENTLY. Naked, and indistinguishable from a tranche that carries no stop by design.

    Asserted by observing the state at the moment of the cancel, which is the crash point.
    """
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
    # place_bracket clears its own record on success -- so we start from nothing to find.
    assert not repo.get_state(f"{executor.UNBRACKETED_PREFIX}BTC-USD")

    seen: dict = {}
    real_cancel = executor._cancel_at_exchange

    def _spy(*args, **kwargs):
        # The crash point: the old bracket is about to stop resting.
        seen["intent"] = repo.get_state(f"{executor.UNBRACKETED_PREFIX}BTC-USD")
        return real_cancel(*args, **kwargs)

    monkeypatch.setattr(executor, "_cancel_at_exchange", _spy)

    roll_to_break_even(
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

    assert seen["intent"], "no crash ledger existed at the cancel -- the sweep would skip silently"
    assert seen["intent"]["stop"] == Decimal("50000")
    assert seen["intent"]["target"] == Decimal("53000")


def test_a_successful_roll_clears_its_crash_ledger(repo):
    """Left standing, the sweep would re-place a bracket that already rests."""
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
    new_id = roll_to_break_even(
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

    assert new_id is not None
    assert not repo.get_state(f"{executor.UNBRACKETED_PREFIX}BTC-USD")


def test_a_failed_roll_RETAINS_its_crash_ledger_for_the_sweep(repo, caplog):
    """The naked case. The record is what the next cycle re-places from, so it must survive --
    and the CRITICAL must survive with it: the deployment cycles once per UTC day, so "the sweep
    will fix it" can be a day away. Recovery is not a reason to downgrade the alert."""

    class _RejectingBroker(FakeBroker):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.calls = 0

        def place_order(self, spec, *, idempotency_key=None):  # noqa: ANN001, ANN202
            self.calls += 1
            if self.calls > 1:  # the original bracket places; the replacement fails
                return PlaceResult(success=False, broker_order_id=None, reason="INSUFFICIENT_FUND")
            return super().place_order(spec, idempotency_key=idempotency_key)

    broker = _RejectingBroker()
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

    with caplog.at_level(logging.CRITICAL):
        result = roll_to_break_even(
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

    assert result is None
    intent = repo.get_state(f"{executor.UNBRACKETED_PREFIX}BTC-USD")
    assert intent, "a naked position with no ledger is the exact #519 hole"
    assert intent["stop"] == Decimal("50000")
    assert any("position_unprotected" in r.message for r in caplog.records)


# -- #524: the registry serves the executor; the executor speaks only the port -----------------


class _FixtureTransport:
    """A `coinbase.rest.RESTClient` duck answering with the canned, real-shaped JSON the
    `tests/fixtures/cb_*.json` files hold -- the same fixtures `tests/data/test_cb_client.py`
    drives the legacy client with, so the adapter resolved below runs against data captured
    from the venue's own response shapes. No network, no credentials."""

    def __init__(self) -> None:
        self.preview_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []

    def _fixture(self, name: str) -> Any:
        from pathlib import Path

        with (Path(__file__).parent.parent / "fixtures" / name).open() as f:
            return json.load(f)

    def get_accounts(self, **kwargs: Any) -> Any:
        accounts = self._fixture("cb_accounts.json")
        # The captured fixture holds USD 1042.55; the order the executor sizes from
        # `_enter_signal` needs more, and rail 13 fails closed on the shortfall. Fund the
        # account rather than shrink the order -- the point of this test is the full guarded
        # path, not rail 13's arithmetic (that rail's own tests cover it).
        for row in accounts["accounts"]:
            if row["currency"] == "USD":
                row["available_balance"]["value"] = "1000000.00"
        return accounts

    def get_product(self, product_id: str, **kwargs: Any) -> Any:
        return self._fixture("cb_product.json")

    def preview_order(
        self, product_id: str, side: str, order_configuration: dict[str, Any], **kwargs: Any
    ) -> Any:
        self.preview_calls.append(
            {
                "product_id": product_id,
                "side": side,
                "order_configuration": order_configuration,
            }
        )
        return self._fixture("cb_preview_order.json")

    def create_order(
        self,
        client_order_id: str,
        product_id: str,
        side: str,
        order_configuration: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        self.create_calls.append(
            {
                "client_order_id": client_order_id,
                "product_id": product_id,
                "side": side,
                "order_configuration": order_configuration,
            }
        )
        return self._fixture("cb_place_order_market.json")

    def get_order(self, order_id: str, **kwargs: Any) -> Any:
        return {
            "order": {
                "order_id": order_id,
                "status": "FILLED",
                "filled_size": "0.001",
                "average_filled_price": "65440.00",
                "total_fees": "0.60",
            }
        }


def test_the_registry_resolved_coinbase_adapter_serves_execute_end_to_end(repo) -> None:
    """#524's headline proof: the default venue's broker, resolved the way `_build_broker`
    now resolves it, drives one full guarded BUY through the executor. The balance read feeds
    rail 13, the instrument read sizes the order, the preview is the port's `Preview`, the
    placement a `PlaceResult`, and the reconciliation read observes the fill -- with no dict
    shape probed anywhere on the way through."""
    from keel_broker_api.registry import load_broker

    transport = _FixtureTransport()
    broker = load_broker("coinbase")(transport)

    result = execute(_enter_signal(), broker, repo, _config(), "autonomous", now_ts=NOW_TS)

    assert result.placed is True
    assert result.preview is not None and result.preview.synthetic is False
    assert result.bracket_order_id is not None
    # The entry and its bracket both reached the venue through the port's specs
    assert [list(c["order_configuration"]) for c in transport.create_calls] == [
        ["market_market_ioc"],
        ["trigger_bracket_gtc"],
    ]
    assert transport.create_calls[0]["product_id"] == "BTC-USD"


def test_the_registry_resolved_coinbase_adapter_serves_the_reconcile_sweep(repo) -> None:
    """The third leg of the #524 pin. The same registry-built adapter that served the guarded
    place and the preview also serves the sweep that later observes the resting bracket's fill:
    the tranche is recorded the way `run_once` records it, the venue's answer arrives as the
    port's `OrderStatus`, and the observed economics land on the order row and in the outcome --
    with no dict shape probed anywhere on the way through."""
    from keel_broker_api.registry import load_broker

    from keel.execution.reconcile import reconcile_open_orders

    transport = _FixtureTransport()
    broker = load_broker("coinbase")(transport)

    result = execute(_enter_signal(), broker, repo, _config(), "autonomous", now_ts=NOW_TS)
    assert result.placed and result.bracket_order_id is not None
    # What `run_once` leaves behind after a filled entry: the tranche, pointed at its bracket.
    entry = repo.get_order(result.order_id)
    position_id = repo.open_position(
        product_id="BTC-USD",
        rule_name="pullback_continuation",
        opened_at=NOW_TS,
        qty=entry["qty"],
        entry_fee=entry["fee"] or Decimal("0"),
        entry_fill=entry["actual_fill"],
    )
    repo.set_position_bracket(position_id, result.bracket_order_id)

    changed = reconcile_open_orders(broker, repo, _config(), now_ts=NOW_TS + 900)

    # The market entry filled at placement; the resting bracket is the sweep's one row.
    assert changed == [result.bracket_order_id]
    bracket = repo.get_order(result.bracket_order_id)
    assert bracket["status"] == "filled"
    assert bracket["actual_fill"] == Decimal("65440.00")  # observed, not the stop it rested at
    assert bracket["fee"] == Decimal("0.60")
    outcomes = repo.get_trade_outcomes()
    assert len(outcomes) == 1 and outcomes[0]["exit_fill"] == Decimal("65440.00")


def test_a_registry_resolved_fake_venue_serves_the_executors_port_reads(repo) -> None:
    """The second adapter the registry can hand the executor: the fake venue, whose deliberate
    divergences are the port's design pressure. Its balances and instrument reads serve the
    executor's two pre-order port calls, and its refusal to preview is the port's honest
    exception -- a capability-declined `NotImplementedError`, never a shape mismatch."""
    from keel_broker_api.orders import MarketIOCByQuote
    from keel_broker_api.registry import load_broker

    fake = load_broker("fake")()

    assert executor._fetch_available_quote(fake, "USD") == Decimal("1000")
    assert executor._base_increment_for(fake, repo, "BTC-USD", NOW_TS) == Decimal("0.00000001")

    with pytest.raises(NotImplementedError, match="no order preview"):
        fake.preview_order(
            MarketIOCByQuote(product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("50"))
        )


# -- #233: the venue's half of the trade-scope record ------------------------------------------
#
# The record has two writers. `keel scope attest` is the operator's; everything below is the
# venue's. Before this, `_run_order` stood exactly where venue truth arrived and threw it away --
# `place_order` failures were `log_exception` + re-raise, recorded nowhere -- so a confidently
# wrong attestation stayed wrong forever and a working venue was indistinguishable from a
# read-only key on the next run. That is the 2026-08-19 incident's actual cost, and these tests
# are the whole of the fix.
#
# ⚠️ The negative cases below matter MORE than the positive ones. `REFUTED` latches: rail 20 then
# vetoes every live ENTRY on this venue until a human types `yes` at a terminal. A transient 5xx
# classified as a refusal would take a healthy live deployment off the market and require
# physical presence to restore it -- strictly worse than the failure this design exists to fix.


class _RefusingBroker(FakeBroker):
    """A broker whose PLACEMENT raises `exc`; its reads and preview all succeed.

    That asymmetry is the shape of the failure being modelled: a credential without trade scope
    reads balances, prices and books perfectly and is refused only at the order.
    """

    def __init__(self, exc: Exception, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._exc = exc

    def place_order(self, spec: OrderSpec, *, idempotency_key: str | None = None) -> PlaceResult:
        self.events.append("place")
        self.place_calls.append({"spec": spec})
        raise self._exc


class _PreviewRefusingBroker(FakeBroker):
    """A broker whose PREVIEW raises `exc` -- Coinbase's shape, where preview is a real call
    under the same scope and the executor previews before it places."""

    def __init__(self, exc: Exception, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._exc = exc

    def preview_order(self, spec: OrderSpec) -> Preview:
        self.preview_calls.append({"spec": spec})
        raise self._exc


def _scope(repo: Repository, venue: str = "coinbase"):
    return repo.get_venue_trade_scope(venue)


def test_a_successful_live_placement_is_recorded_as_confirmed_by_the_venue(repo):
    """The venue proving the operator right. A placement the venue ACCEPTED is the strongest
    evidence available that this credential can trade -- stronger than the attestation, because
    the venue supplied it -- so the record moves from the operator's claim to the venue's fact."""
    attest_trade_scope(
        repo,
        now_ts=NOW_TS,
        state=TradeScopeState.ATTESTED,
        attested_scope=TRADING,
        attested_ts=NOW_TS,
        confirmed_ts=None,
    )
    broker = FakeBroker()

    result = execute(_enter_signal(), broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

    assert result.placed is True
    record = _scope(repo)
    assert record is not None
    assert record.state is TradeScopeState.CONFIRMED
    assert record.confirmed_ts == NOW_TS


def test_confirming_keeps_the_operators_attestation_and_the_refusal_history(repo):
    """`confirmed` is a NEW fact, not a reset. `attested_scope`/`attested_ts` say what a human
    claimed and `refuted_ts` says a credential on this venue was once refused -- doctor renders
    both, and `apply_scope_attest` deliberately carries them forward for the same reason."""
    attest_trade_scope(
        repo,
        now_ts=NOW_TS,
        state=TradeScopeState.ATTESTED,
        attested_scope=TRADING,
        attested_ts=NOW_TS - 500,
        confirmed_ts=None,
        refuted_ts=NOW_TS - 9000,
        refuted_reason="an older credential was refused here",
    )

    execute(_enter_signal(), FakeBroker(), repo, _config(), mode="autonomous", now_ts=NOW_TS)

    record = _scope(repo)
    assert record is not None
    assert record.attested_scope == TRADING
    assert record.attested_ts == NOW_TS - 500
    assert record.refuted_ts == NOW_TS - 9000
    assert record.refuted_reason == "an older credential was refused here"


# -- #633: confirm and refute both stamp the current credential fingerprint ---------------------


def test_confirming_stamps_the_current_credential_fingerprint(repo):
    """The venue's acceptance is evidence about WHICHEVER credential just placed it -- the write
    must record that credential's fingerprint, not leave the column untouched."""
    attest_trade_scope(
        repo,
        now_ts=NOW_TS,
        state=TradeScopeState.ATTESTED,
        attested_scope=TRADING,
        attested_ts=NOW_TS,
        confirmed_ts=None,
        credential_fingerprint=None,
    )

    with mock.patch.object(executor, "current_credential_fingerprint", return_value="c" * 32):
        result = execute(
            _enter_signal(), FakeBroker(), repo, _config(), mode="autonomous", now_ts=NOW_TS
        )

    assert result.placed is True
    record = _scope(repo)
    assert record is not None
    assert record.credential_fingerprint == "c" * 32


def test_confirming_replaces_a_stale_fingerprint_rather_than_carrying_it_forward(repo):
    """Unlike `attested_scope`/`refuted_ts`, the fingerprint is a NEW fact about which credential
    produced THIS evidence -- a confirmation must overwrite whatever fingerprint an earlier write
    left behind, not preserve it."""
    attest_trade_scope(
        repo,
        now_ts=NOW_TS,
        state=TradeScopeState.ATTESTED,
        attested_scope=TRADING,
        attested_ts=NOW_TS,
        confirmed_ts=None,
        credential_fingerprint="stale" + "0" * 27,
    )

    with mock.patch.object(
        executor, "current_credential_fingerprint", return_value="fresh" + "0" * 27
    ):
        execute(_enter_signal(), FakeBroker(), repo, _config(), mode="autonomous", now_ts=NOW_TS)

    record = _scope(repo)
    assert record is not None
    assert record.credential_fingerprint == "fresh" + "0" * 27


def test_refuting_stamps_the_current_credential_fingerprint(repo):
    """The venue's refusal is evidence about the credential it just refused -- same discipline as
    the confirm side."""
    with mock.patch.object(executor, "current_credential_fingerprint", return_value="d" * 32):
        with pytest.raises(TradeScopeDenied):
            execute(
                _enter_signal(),
                _RefusingBroker(TradeScopeDenied("nope")),
                repo,
                _config(),
                mode="autonomous",
                now_ts=NOW_TS,
            )

    record = _scope(repo)
    assert record is not None
    assert record.credential_fingerprint == "d" * 32


def test_refuting_still_latches_when_fingerprinting_is_unavailable(repo):
    """`current_credential_fingerprint` never raises -- an unresolvable credential (a locked
    keychain, a momentarily unreadable `.env`) comes back as `None`, not an exception. This pins
    that the refusal write still proceeds and still latches `REFUTED` in that case: losing the
    venue's REFUSAL to a fingerprinting hiccup (`_try_record_trade_scope_refuted`'s own
    reasoning) would be strictly worse than writing a `None` fingerprint alongside it."""
    with mock.patch.object(executor, "current_credential_fingerprint", return_value=None):
        with pytest.raises(TradeScopeDenied):
            execute(
                _enter_signal(),
                _RefusingBroker(TradeScopeDenied("nope")),
                repo,
                _config(),
                mode="autonomous",
                now_ts=NOW_TS,
            )

    record = _scope(repo)
    assert record is not None
    assert record.state is TradeScopeState.REFUTED, (
        "an unresolvable current fingerprint must not swallow the venue's own refusal -- the "
        "REFUTED state is what stops the next cycle from repeating the same denied placement"
    )
    assert record.credential_fingerprint is None


def test_a_venue_rejecting_the_ORDER_does_not_confirm_the_scope(repo):
    """`PlaceResult(success=False)` is the venue refusing THIS ORDER -- no funds, a bad size, a
    price out of band. It is not the venue accepting a placement, so it proves nothing about the
    credential and must not move the record forward."""
    attest_trade_scope(
        repo,
        now_ts=NOW_TS,
        state=TradeScopeState.ATTESTED,
        attested_scope=TRADING,
        attested_ts=NOW_TS,
        confirmed_ts=None,
    )

    result = execute(
        _enter_signal(),
        FakeBroker(place_success=False),
        repo,
        _config(),
        mode="autonomous",
        now_ts=NOW_TS,
    )

    assert result.placed is False
    record = _scope(repo)
    assert record is not None
    assert record.state is TradeScopeState.ATTESTED
    assert record.confirmed_ts is None


def test_a_permission_refusal_at_placement_writes_refuted_with_the_venues_own_words(repo):
    """The motivating case, end to end. Observed live: `403 {"detail": "You do not have
    permission to perform this action."}` under a credential whose every read succeeded."""
    denial = "You do not have permission to perform this action."
    broker = _RefusingBroker(TradeScopeDenied(denial))

    with pytest.raises(TradeScopeDenied):
        execute(_enter_signal(), broker, repo, _config(), mode="autonomous", now_ts=NOW_TS + 77)

    record = _scope(repo)
    assert record is not None
    assert record.state is TradeScopeState.REFUTED
    assert record.refuted_ts == NOW_TS + 77
    assert record.refuted_reason == denial
    assert record.may_place_live_entry(None) is False


def test_a_permission_refusal_at_PREVIEW_also_writes_refuted(repo):
    """Coinbase's preview is a real venue call under the same scope, and this deployment's venue
    IS coinbase. Handling only placement would leave the record's second writer unreachable on
    the one venue that trades live."""
    broker = _PreviewRefusingBroker(TradeScopeDenied("Missing required scopes"))

    with pytest.raises(TradeScopeDenied):
        execute(_enter_signal(), broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

    record = _scope(repo)
    assert record is not None
    assert record.state is TradeScopeState.REFUTED
    assert record.refuted_reason == "Missing required scopes"


def test_refuting_keeps_what_the_operator_attested_and_any_earlier_confirmation(repo):
    """The refutation is what changed; the attestation is what a human said, and a past
    confirmation is what the venue once did. Doctor's most useful sentence -- "you attested this
    for trading and the venue then refused it" -- needs both to survive."""
    attest_trade_scope(
        repo,
        now_ts=NOW_TS,
        state=TradeScopeState.ATTESTED,
        attested_scope=TRADING,
        attested_ts=NOW_TS - 100,
        confirmed_ts=NOW_TS - 4000,
    )

    with pytest.raises(TradeScopeDenied):
        execute(
            _enter_signal(),
            _RefusingBroker(TradeScopeDenied("nope")),
            repo,
            _config(),
            mode="autonomous",
            now_ts=NOW_TS,
        )

    record = _scope(repo)
    assert record is not None
    assert record.attested_scope == TRADING
    assert record.attested_ts == NOW_TS - 100
    assert record.confirmed_ts == NOW_TS - 4000


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("503 Server Error: Service Unavailable"),
        TimeoutError("connection timed out"),
        ConnectionError("connection reset by peer"),
        OSError("Network is unreachable"),
        ValueError("could not decode the venue's response"),
    ],
    ids=["5xx", "timeout", "connection-reset", "unreachable", "unparseable"],
)
def test_a_transport_failure_at_placement_NEVER_touches_the_record(repo, exc):
    """**THE constraint of this PR.** Anything not classified by the ADAPTER as a permission
    refusal stays a plain raise and leaves the record exactly as it found it.

    A `refuted` row written here would veto every live ENTRY on a healthy deployment through rail
    20 and stay vetoed until an operator re-attested at a terminal -- an outage manufactured out
    of a dropped packet, on a deployment that trades unattended and daily. The order row is still
    written and the exception still propagates: only the SCOPE record is untouched, because a
    network failure is evidence about the network and about nothing else.
    """
    before = _scope(repo)
    assert before is not None

    with pytest.raises(type(exc)):
        execute(
            _enter_signal(),
            _RefusingBroker(exc),
            repo,
            _config(),
            mode="autonomous",
            now_ts=NOW_TS + 500,
        )

    after = _scope(repo)
    assert after == before


@pytest.mark.parametrize(
    "exc",
    [RuntimeError("500 Server Error"), TimeoutError("connection timed out")],
    ids=["5xx", "timeout"],
)
def test_a_transport_failure_at_PREVIEW_never_touches_the_record_either(repo, exc):
    before = _scope(repo)

    with pytest.raises(type(exc)):
        execute(
            _enter_signal(),
            _PreviewRefusingBroker(exc),
            repo,
            _config(),
            mode="autonomous",
            now_ts=NOW_TS + 500,
        )

    assert _scope(repo) == before


def test_a_guard_veto_never_touches_the_record(repo):
    """Rail 20 reads this record; the executor writes it. A vetoed intent never reaches the
    venue, so there is no venue truth to record -- and a write here would let a rail's own veto
    feed back into the record the rail reads."""
    repo.set_state("kill_switch", True)
    before = _scope(repo)

    result = execute(
        _enter_signal(), NoNetworkBroker(), repo, _config(), mode="autonomous", now_ts=NOW_TS
    )

    assert result.placed is False
    assert result.vetoed_by
    assert _scope(repo) == before


def test_the_record_is_written_against_the_BOUND_venue_not_a_frozen_default(repo):
    """Keyed exactly like rail 20 (`current_venue() or DEFAULT_VENUE`). Writing coinbase's row
    from an alpaca deployment would refute a venue that refused nothing and leave the venue that
    did refuse still permitted -- wrong in both directions at once."""
    attest_trade_scope(
        repo,
        now_ts=NOW_TS,
        venue="alpaca",
        state=TradeScopeState.ATTESTED,
        attested_scope=TRADING,
        attested_ts=NOW_TS,
    )
    # Rail 22 (#691) is venue-keyed for the same reason: an alpaca-bound cycle needs
    # alpaca's posture row, or it is vetoed before it reaches the venue.
    attest_cash_posture(repo, now_ts=NOW_TS, venue="alpaca")
    # Rail 14 is venue-keyed too, so an alpaca-bound cycle needs alpaca's subscription row or it
    # is vetoed before it ever reaches the venue -- which would pass this test for the wrong
    # reason (no placement, hence no refusal, hence no write).
    attest_subscription(repo, now_ts=NOW_TS, free_volume_usd=Decimal("10000000"), venue="alpaca")
    token = bind_venue("alpaca")
    try:
        with pytest.raises(TradeScopeDenied):
            execute(
                _enter_signal(),
                _RefusingBroker(TradeScopeDenied("alpaca said no")),
                repo,
                _config(),
                mode="autonomous",
                now_ts=NOW_TS,
            )
    finally:
        unbind_venue(token)

    assert _scope(repo, "alpaca").state is TradeScopeState.REFUTED
    # coinbase's row -- the fixture's -- is untouched.
    assert _scope(repo, "coinbase").state is TradeScopeState.CONFIRMED


def test_a_refusal_on_an_EXIT_writes_a_row_where_there_was_none(repo):
    """The path rail 20 deliberately cannot reach, and the one that makes the design converge.

    Rail 20 is ENTRIES-ONLY, so a venue with no trade-scope row is vetoed on every BUY and never
    placed -- the record could never learn anything from an entry it prevented. An EXIT is not
    gated (vetoing one would strand a position that wanted out), so it reaches the venue, and a
    read-only credential is refused there. THAT is where the venue's answer comes from on a
    deployment that has attested nothing, and the row this writes is what turns the next
    `doctor` run from "nobody has attested" into "the venue refused this credential, saying X".
    """
    _seed_open_position(repo, "BTC-USD", Decimal("0.1"), Decimal("50000"))
    attest_subscription(
        repo, now_ts=NOW_TS, free_volume_usd=Decimal("10000000"), venue="someplace"
    )
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

    token = bind_venue("someplace")
    try:
        assert repo.get_venue_trade_scope("someplace") is None
        with pytest.raises(TradeScopeDenied):
            execute(
                signal,
                _RefusingBroker(TradeScopeDenied("no permission")),
                repo,
                _config(),
                mode="autonomous",
                now_ts=NOW_TS,
            )
    finally:
        unbind_venue(token)

    record = _scope(repo, "someplace")
    assert record is not None
    assert record.state is TradeScopeState.REFUTED
    assert record.refuted_reason == "no permission"
    assert record.attested_scope is None
    assert record.confirmed_ts is None


def test_an_enormous_refusal_body_is_truncated_before_it_reaches_the_record(repo):
    """A venue that answers with an HTML error page must not put a page into a column `doctor`
    and `keel scope show` print to a terminal. Truncated, and marked as truncated, so nobody
    reads the cut as the venue's full answer."""
    with pytest.raises(TradeScopeDenied):
        execute(
            _enter_signal(),
            _RefusingBroker(TradeScopeDenied("x" * 5000)),
            repo,
            _config(),
            mode="autonomous",
            now_ts=NOW_TS,
        )

    reason = _scope(repo).refuted_reason
    assert reason is not None
    assert len(reason) < 1000
    assert reason.endswith("...")


def test_the_refusal_is_logged_at_ERROR_with_the_venues_words(repo, caplog):
    """Loud, once, at the moment the venue said it. This event is the only place the refusal
    appears in the log stream, and an operator grepping for why entries stopped needs it above
    INFO."""
    with pytest.raises(TradeScopeDenied):
        with caplog.at_level(logging.DEBUG):
            execute(
                _enter_signal(),
                _RefusingBroker(TradeScopeDenied("You do not have permission")),
                repo,
                _config(),
                mode="autonomous",
                now_ts=NOW_TS,
            )

    refusals = [r for r in caplog.records if r.message == "executor.trade_scope_refuted"]
    assert len(refusals) == 1
    assert refusals[0].levelno == logging.ERROR
    assert refusals[0].exc_info is not None


# -- #233: only an ENTRY confirms, because only an ENTRY is what the record claims -------------
#
# `_run_order` is shared by entries, exits, brackets, scale-outs and stop rolls. Rail 20 is
# ENTRIES-ONLY, so every one of those SELL-side paths reaches the venue on a credential the rail
# would have refused an entry on -- and if a successful placement of any kind confirmed, the
# engine would clear its own safety latch with no human in the loop.
#
# `_manage_stops` makes that concrete rather than theoretical: it rolls stops EVERY CYCLE on any
# open position, through this same pipeline. A latched REFUTED would survive exactly until the
# next successful roll.
#
# The record's own question is `may_place_live_entry`. A successful SELL is evidence about SELL
# scope and says nothing about BUY scope, so treating it as proof is a category error -- and it
# is the category error that happens to unlatch the safety state.


def _exit_signal_for(product_id: str = "BTC-USD") -> Signal:
    return Signal(
        rule_name="target_harvest",
        product_id=product_id,
        action=Action.EXIT,
        side=Side.SELL,
        setup=None,
        cts_score=0,
        entry_technique="market",
        ts=NOW_TS,
    )


def test_a_successful_EXIT_does_not_confirm_a_credential_attested_READ_ONLY(repo):
    """`keel scope attest --read-only` is UNGATED precisely because it only ever REDUCES
    capability -- it needs no typed `yes` at a terminal because it cannot release anything. If a
    successful exit confirmed, the engine would hand back the capability the operator had just
    given up, from a cron line, and leave a self-contradictory row saying both `state=confirmed`
    and `attested_scope=read_only`."""
    attest_trade_scope(
        repo,
        now_ts=NOW_TS,
        state=TradeScopeState.ATTESTED,
        attested_scope=READ_ONLY,
        attested_ts=NOW_TS,
        confirmed_ts=None,
    )
    _seed_open_position(repo, "BTC-USD", Decimal("0.1"), Decimal("50000"))

    result = execute(
        _exit_signal_for(), FakeBroker(), repo, _config(), mode="autonomous", now_ts=NOW_TS
    )

    assert result.placed is True, "the exit itself must still go through -- rail 20 is entries-only"
    record = _scope(repo)
    assert record is not None
    assert record.state is TradeScopeState.ATTESTED
    assert record.attested_scope == READ_ONLY
    assert record.confirmed_ts is None
    assert record.may_place_live_entry(None) is False


def test_a_successful_EXIT_does_not_clear_a_LATCHED_REFUSAL(repo):
    """The latch is the whole safety property. `_manage_stops` rolls stops every cycle on any
    open position through this same pipeline, so a refutation cleared by a successful SELL would
    survive until the next cycle and no further -- the venue's own refusal erased by the engine,
    unattended, hours later."""
    attest_trade_scope(
        repo,
        now_ts=NOW_TS,
        state=TradeScopeState.REFUTED,
        refuted_ts=NOW_TS - 60,
        refuted_reason="Missing required scopes",
    )
    _seed_open_position(repo, "BTC-USD", Decimal("0.1"), Decimal("50000"))

    result = execute(
        _exit_signal_for(), FakeBroker(), repo, _config(), mode="autonomous", now_ts=NOW_TS
    )

    assert result.placed is True
    record = _scope(repo)
    assert record is not None
    assert record.state is TradeScopeState.REFUTED
    assert record.refuted_reason == "Missing required scopes"
    assert record.may_place_live_entry(None) is False


def test_the_bracket_placed_alongside_an_entry_is_not_a_second_confirmation(repo):
    """One entry, one confirmation. `execute` on a stop+target setup places TWO orders through
    `_run_order` -- the BUY, then the protective bracket, which is SELL-side -- and only the
    first is evidence about entry scope."""
    attest_trade_scope(
        repo,
        now_ts=NOW_TS,
        state=TradeScopeState.ATTESTED,
        attested_scope=TRADING,
        attested_ts=NOW_TS,
        confirmed_ts=None,
    )
    broker = FakeBroker()

    result = execute(_enter_signal(), broker, repo, _config(), mode="autonomous", now_ts=NOW_TS)

    assert result.placed is True
    assert len(broker.place_calls) == 2, "entry + bracket both go through _run_order"
    record = _scope(repo)
    assert record is not None
    assert record.state is TradeScopeState.CONFIRMED
    assert record.confirmed_ts == NOW_TS


def test_a_stop_ROLL_on_a_refuted_venue_does_not_clear_the_refusal(repo):
    """The path `_manage_stops` actually walks every cycle, exercised through its own public
    entry point rather than inferred from the exit test above."""
    attest_trade_scope(
        repo,
        now_ts=NOW_TS,
        state=TradeScopeState.REFUTED,
        refuted_ts=NOW_TS - 60,
        refuted_reason="Missing required scopes",
    )
    broker = FakeBroker()
    old_id = place_bracket(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        qty=Decimal("0.1"),
        stop=Decimal("49000"),
        target=Decimal("53000"),
        rule_name="test",
        now_ts=NOW_TS,
    )
    assert old_id is not None

    rolled = roll_stop_to(
        broker,
        repo,
        _config(),
        product_id="BTC-USD",
        old_stop_order_id=old_id,
        new_stop=Decimal("50500"),
        qty=Decimal("0.1"),
        rule_name="test",
        now_ts=NOW_TS + 60,
    )

    assert rolled is not None, "the roll itself must still work -- rail 20 never gated it"
    record = _scope(repo)
    assert record is not None
    assert record.state is TradeScopeState.REFUTED
    assert record.may_place_live_entry(None) is False


# -- #233: the scope record is metadata and must never outrank the money path ------------------


class _ScopeWriteFailsRepo:
    """A `Repository` whose trade-scope UPSERT raises, and whose everything-else works.

    `upsert_venue_trade_scope` commits (`repository.py`), so it is a real write against a file the
    live agent may be mid-cycle on -- `sqlite3.OperationalError: database is locked` is its
    ordinary failure, not an exotic one.
    """

    def __init__(self, inner: Repository) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def upsert_venue_trade_scope(self, record: Any) -> None:
        raise sqlite3.OperationalError("database is locked")


def test_a_failed_confirm_write_does_not_cost_the_position_its_bracket(repo, caplog):
    """The hazard is the ORDER of what happens after a live fill: the entry fills, then
    `_upgrade_to_observed_economics` runs, then the CALLER places the protective bracket. An
    unguarded metadata write between them turns a locked database into an unprotected position.

    Every other write on this path is an audit record of money that actually moved. This one
    describes the credential that moved it, and metadata must never outrank a bracket.
    """
    attest_trade_scope(
        repo,
        now_ts=NOW_TS,
        state=TradeScopeState.ATTESTED,
        attested_scope=TRADING,
        attested_ts=NOW_TS,
        confirmed_ts=None,
    )
    broker = FakeBroker()

    with caplog.at_level(logging.DEBUG):
        result = execute(
            _enter_signal(),
            broker,
            _ScopeWriteFailsRepo(repo),
            _config(),
            mode="autonomous",
            now_ts=NOW_TS,
        )

    assert result.placed is True, "a metadata write must not abort a placement that succeeded"
    assert len(broker.place_calls) == 2, "the protective bracket must still have been placed"
    assert any(
        r.message == "executor.trade_scope_confirm_write_failed" and r.levelno == logging.ERROR
        for r in caplog.records
    ), "the lost confirmation must still be loud"


def test_a_failed_refute_write_does_not_replace_the_venues_own_refusal(repo, caplog):
    """`TradeScopeDenied` is the one signal an operator needs out of this path. If the write that
    records it raises, the database error would propagate IN ITS PLACE -- and the ERROR log
    naming the refusal would never fire either, because it sits after the write.

    Failing to record costs one repeated refusal next cycle, which is exactly the pre-#233
    behaviour. Losing the venue's answer is not survivable in the same way.
    """
    broker = _RefusingBroker(TradeScopeDenied("You do not have permission"))

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(TradeScopeDenied):
            execute(
                _enter_signal(),
                broker,
                _ScopeWriteFailsRepo(repo),
                _config(),
                mode="autonomous",
                now_ts=NOW_TS,
            )

    assert any(r.message == "executor.trade_scope_refute_write_failed" for r in caplog.records)
    assert any(r.message == "executor.trade_scope_refuted" for r in caplog.records)


def test_it_is_the_ENTRY_placement_that_confirms_not_the_bracket_that_follows_it(repo):
    """Which placement wrote the confirmation, not merely that one did.

    `execute` on a stop+target setup runs `_run_order` TWICE -- the BUY, then the SELL-side
    protective bracket -- and both end with a CONFIRMED-shaped record, so asserting the final
    state cannot tell the two apart. An implementation that confirmed only from SELLs would leave
    exactly the same row behind and pass every other test in this file.

    So this records how many placements the broker had seen at the moment of each scope write.
    One write, and it happened when exactly one order had been placed: the entry.
    """
    attest_trade_scope(
        repo,
        now_ts=NOW_TS,
        state=TradeScopeState.ATTESTED,
        attested_scope=TRADING,
        attested_ts=NOW_TS,
        confirmed_ts=None,
    )
    broker = FakeBroker()
    writes_at: list[int] = []

    class _SpyRepo:
        def __getattr__(self, name: str) -> Any:
            return getattr(repo, name)

        def upsert_venue_trade_scope(self, record: Any) -> None:
            writes_at.append(len(broker.place_calls))
            repo.upsert_venue_trade_scope(record)

    result = execute(
        _enter_signal(), broker, _SpyRepo(), _config(), mode="autonomous", now_ts=NOW_TS
    )

    assert result.placed is True
    assert len(broker.place_calls) == 2, "entry + bracket, both through _run_order"
    assert writes_at == [1], (
        "exactly one scope write, taken after the ENTRY and before the bracket -- "
        f"got writes after placement counts {writes_at}"
    )


# -- #626: the venue's book at submit, recorded on the order row --------------------------------


class TestBookAtSubmit:
    """#626 option 1: every live order row carries the venue's own `best_bid`/`best_ask` from
    the preview that immediately precedes its placement.

    Why it exists. `config.live-sandbox.yaml` sets `max_per_order_usd: 100` and the three real
    live fills were $50.00, $50.00 and $61.71. At $50 the square-root law puts market impact
    under 5bp for every product in the corpus while `slippage_for_quote_volume` charges 50bp --
    so essentially the whole modelled cost is SPREAD, and keel stored nothing that measured it.
    #523's merged measurement reports every participation arm as a LOWER BOUND on cost for
    exactly that reason.

    Why it is cheap. keel has ALWAYS fetched this: `_run_order` previews before it places, and
    #350's max-entry-spread gate reads these two fields out of that preview to decide whether to
    enter at all. Option 1 is pure persistence of a value already in hand -- no port method, no
    extra venue call, and the write rides the `insert_order` that already precedes placement.
    """

    def test_a_live_buy_records_the_book_the_preview_carried(self, repo) -> None:
        """The headline row: one live entry, one spread observation, and NO second venue call
        to get it -- the same single preview #350's gate consumed."""
        broker = FakeBroker(preview=_book_preview(Decimal("49990"), Decimal("50000")))

        result = execute(_enter_signal(), broker, repo, _config(), "autonomous", now_ts=NOW_TS)

        assert result.placed is True
        order = repo.get_order(result.order_id)
        assert order["submit_best_bid"] == Decimal("49990")
        assert order["submit_best_ask"] == Decimal("50000")
        # The ENTRY's preview only. A bracket follows on this path and previews too, so this
        # counts the entry's own call rather than asserting a global total of one.
        assert broker.preview_calls[0]["spec"].side == Side.BUY

    def test_the_raw_pair_is_stored_not_a_derived_spread(self, repo) -> None:
        """The pair, digit for digit, the way `expected_fill`/`actual_fill` keep two numbers
        rather than one delta.

        A stored `(ask - bid) / mid` cannot be re-derived differently later, and half-spread
        off the mid, the half actually crossed, and relative spread are three different
        questions off one pair. The awkward precision here is the point: the venue's own string
        survives the round trip, so a reader can compute any of them.
        """
        broker = FakeBroker(preview=_book_preview(Decimal("0.000012340"), Decimal("0.000012341")))
        signal = _enter_signal(
            _setup(
                product_id="ETH-USD",
                entry=Decimal("0.000012341"),
                stop=Decimal("0.000012000"),
                target=Decimal("0.000014000"),
            )
        )

        result = execute(signal, broker, repo, _config(), "autonomous", now_ts=NOW_TS)

        order = repo.get_order(result.order_id)
        bid, ask = order["submit_best_bid"], order["submit_best_ask"]
        assert str(bid) == "0.000012340"
        assert str(ask) == "0.000012341"
        # Re-derivable, which is the whole reason the pair is what is stored. The trailing zero
        # on the bid survives too: a venue string, not a normalised number.
        assert (ask - bid) / ((ask + bid) / 2) == Decimal("0.00008103399376038248045054900531")

    def test_a_sell_records_its_book_even_though_the_gate_never_runs_on_one(
        self, repo, caplog
    ) -> None:
        """Deliberately WIDER scope than #350's gate, which is BUY-and-live only.

        A gate that refused an exit would strand a position in exactly the book the rule said
        to leave, so #350 stops at BUYs. Measurement has the opposite requirement: a round trip
        costs the entry half-spread AND the exit half-spread, so recording only entries would
        hand #523 half a number and call it whole. This 400bp book is not refused -- it is
        written down.
        """
        _seed_open_position(repo, "BTC-USD", Decimal("0.1"), Decimal("50000"))
        broker = FakeBroker(preview=_book_preview(Decimal("48000"), Decimal("50000")))

        with caplog.at_level(logging.WARNING):
            result = execute(_exit_signal(), broker, repo, _config(), "autonomous", now_ts=NOW_TS)

        assert result.placed is True
        assert result.vetoed_by == []
        order = repo.get_order(result.order_id)
        assert order["side"] == "SELL"
        assert order["submit_best_bid"] == Decimal("48000")
        assert order["submit_best_ask"] == Decimal("50000")

    def test_a_bracket_leg_records_the_book_it_was_submitted_into(self, repo) -> None:
        """`place_bracket` comes through the same `_run_order`, so its row carries a book too.

        The columns are named `submit_` because for a RESTING order this is not the book it
        eventually fills in -- a `BracketGTC` waits at the exchange for hours or days. Note what
        the assertions below do NOT rely on: `order_type` is `'market'` on every row
        `_order_row` writes, so a reader separating the resting legs from the market IOCs that
        actually crossed a book does it by `positions.bracket_order_id`. The observation is
        still real data about the venue at submit and is kept.
        """
        broker = FakeBroker(preview=_book_preview(Decimal("50000"), Decimal("50010")))
        _seed_open_position(repo, "BTC-USD", Decimal("0.002"), Decimal("50000"))

        bracket_id = place_bracket(
            broker,
            repo,
            _config(),
            product_id="BTC-USD",
            qty=Decimal("0.002"),
            stop=Decimal("49000"),
            target=Decimal("53000"),
            rule_name="position_rule",
            now_ts=NOW_TS,
        )

        assert bracket_id is not None
        bracket = repo.get_order(bracket_id)
        assert bracket["status"] == "pending", "a RESTING order -- it has not crossed this book"
        assert bracket["submit_best_bid"] == Decimal("50000")
        assert bracket["submit_best_ask"] == Decimal("50010")

    def test_a_preview_with_no_book_records_nothing_and_still_places(self, repo) -> None:
        """The venue that supplies no book is a DEGRADED response, not a refusal: `_preview_book`
        already answers `(None, None)` for it, and NULL here means "not observed" -- the same
        meaning `filled_quantity` NULL carries. A SELL is the reachable case: a BUY with no
        readable book is already refused upstream by #350's fail-closed gate.
        """
        _seed_open_position(repo, "BTC-USD", Decimal("0.1"), Decimal("50000"))
        bookless = FakeBroker(
            preview={"order_total": Decimal("50.00"), "commission_total": Decimal("0.30")}
        )

        result = execute(_exit_signal(), bookless, repo, _config(), "autonomous", now_ts=NOW_TS)

        assert result.placed is True, "a missing book must never refuse an exit"
        assert bookless.place_calls, "the order really went to the venue"
        order = repo.get_order(result.order_id)
        assert order["submit_best_bid"] is None
        assert order["submit_best_ask"] is None

    def test_recording_the_book_can_never_raise_into_placement(self, repo, caplog) -> None:
        """THE safety property. A diagnostic column must not be able to decide whether an order
        is placed -- the `_record_trade_scope_confirmed` precedent, wrapped so a failing metadata
        write could not cost a live position its protective bracket.

        `_preview_book` is safe against the FIELDS it reads but not against a `detail` that is
        not a mapping at all (`.get` on a list raises `AttributeError`), and until #626 nothing
        called it on the SELL path -- so this change is what newly exposes exits to that shape.
        The order places, the row carries NULLs, and the failure is LOGGED rather than swallowed
        silently, because a preview shape that stopped being readable is a venue change an
        operator needs to see.
        """
        _seed_open_position(repo, "BTC-USD", Decimal("0.1"), Decimal("50000"))
        broker = FakeBroker()
        hostile = Preview(
            product_id="BTC-USD",
            side=Side.SELL,
            est_base_size=Decimal("0.1"),
            est_quote_size=Decimal("5000"),
            est_fee=Decimal("0.30"),
            synthetic=False,
            detail=["best_bid", "50000"],  # type: ignore[arg-type]
        )
        broker.preview_order = lambda *a, **k: hostile  # type: ignore[method-assign]

        with caplog.at_level(logging.ERROR):
            result = execute(_exit_signal(), broker, repo, _config(), "autonomous", now_ts=NOW_TS)

        assert result.placed is True, "an unreadable preview shape must not refuse an exit"
        order = repo.get_order(result.order_id)
        assert order["submit_best_bid"] is None
        assert order["submit_best_ask"] is None
        assert [r for r in caplog.records if r.getMessage() == "executor.submit_book_unreadable"]

    def test_a_rejected_placement_still_carries_the_book(self, repo) -> None:
        """The row is written BEFORE `place_order`, so a venue refusal leaves the observation
        intact. A book that was too wide to fill in is exactly the book worth having recorded."""
        broker = FakeBroker(
            preview=_book_preview(Decimal("49990"), Decimal("50000")), place_success=False
        )

        result = execute(_enter_signal(), broker, repo, _config(), "autonomous", now_ts=NOW_TS)

        assert result.placed is False
        order = repo.get_order(result.order_id)
        assert order["status"] == "rejected"
        assert order["submit_best_bid"] == Decimal("49990")
        assert order["submit_best_ask"] == Decimal("50000")


# -- #715: quote_provenance -- WHERE the price on this order came from --------------------------


class TestQuoteProvenance:
    """`_order_row` records `quote_provenance` (#715) from the SAME preview that already drives
    `submit_best_bid`/`submit_best_ask` above -- a property of the PREVIEW, not of the confirm
    gate, so it is recorded in `mode="autonomous"` exactly like every test below runs.
    `tests/test_confirm_gate.py::test_the_recorded_token_and_the_rendered_banner_correspond` pins
    the token against the banner a human would have read at the confirm gate for the same
    preview, so the two cannot silently disagree.
    """

    def test_a_venue_quoted_preview_records_venue_quoted(self, repo) -> None:
        broker = FakeBroker(
            preview={
                **_book_preview(Decimal("49990"), Decimal("50000")),
                "base_size": "0.002",
                "quote_size": "100",
            }
        )

        result = execute(_enter_signal(), broker, repo, _config(), "autonomous", now_ts=NOW_TS)

        assert result.placed is True
        order = repo.get_order(result.order_id)
        assert order["quote_provenance"] == VENUE_QUOTED

    def test_a_synthetic_preview_records_synthetic_estimate(self, repo) -> None:
        broker = FakeBroker(preview=_book_preview(Decimal("49990"), Decimal("50000")))
        synthetic_preview = Preview(
            product_id="BTC-USD",
            side=Side.BUY,
            est_base_size=Decimal("0.002"),
            est_quote_size=Decimal("100"),
            est_fee=Decimal("0.30"),
            synthetic=True,
            detail={"best_bid": "49990", "best_ask": "50000"},
        )
        broker.preview_order = lambda *a, **k: synthetic_preview  # type: ignore[method-assign]

        result = execute(_enter_signal(), broker, repo, _config(), "autonomous", now_ts=NOW_TS)

        assert result.placed is True
        order = repo.get_order(result.order_id)
        assert order["quote_provenance"] == SYNTHETIC_ESTIMATE

    def test_an_unpriced_preview_records_unpriced(self, repo) -> None:
        """The default `_book_preview` carries no `base_size`/`quote_size` -- exactly the shape
        most of this module's fakes already use, and exactly what an unpriced preview is: a
        readable, non-synthetic preview whose size could not be determined."""
        broker = FakeBroker(preview=_book_preview(Decimal("49990"), Decimal("50000")))

        result = execute(_enter_signal(), broker, repo, _config(), "autonomous", now_ts=NOW_TS)

        assert result.placed is True
        order = repo.get_order(result.order_id)
        assert order["quote_provenance"] == UNPRICED

    def test_an_unreadable_preview_records_unreadable(self, repo) -> None:
        """Exercised at `_order_row` directly, not through `execute()`: every broker the `Broker`
        protocol admits answers `preview_order` with a real `Preview` (`confirm.py`'s own
        docstring makes the same point -- the dict shape is a pre-port relic nothing on the live
        path produces anymore), so `execute()` never actually hands `_order_row` anything other
        than a `Preview`. `_order_row`'s own signature still types `preview` as `Preview | None`,
        and `provenance_of` must still answer something sane for that case -- this pins it
        directly, the same isolation `test_build_intent_uses_equity_override` above uses for
        other private helpers.
        """
        from keel.execution import executor

        intent = executor._build_intent(_enter_signal(), None, repo, _config(), now_ts=NOW_TS)
        assert intent is not None

        row = executor._order_row(intent, "autonomous", NOW_TS, preview=None)

        assert row["quote_provenance"] == UNREADABLE

    def test_a_paper_order_records_no_provenance(self, repo) -> None:
        """Paper has no venue preview at all (`keel.strategy.paper` builds its own row and never
        calls `_order_row`) -- NULL, never a guessed provenance, the same posture
        `submit_best_bid` already takes for paper rows."""
        order_id = repo.insert_order(
            {
                "mode": "paper",
                "product_id": "BTC-USD",
                "side": "BUY",
                "order_type": "market",
                "qty": Decimal("0.001"),
                "limit_price": Decimal("50000"),
                "status": "filled",
                "fee": Decimal("0.03"),
                "expected_fill": Decimal("50000"),
                "actual_fill": Decimal("50000"),
                "raw_response": "{}",
                "confirmation": "paper",
                "rule_id": None,
                "created_at": NOW_TS,
                "updated_at": NOW_TS,
            }
        )

        order = repo.get_order(order_id)
        assert order["quote_provenance"] is None


# -- #715: client_order_id -- the id the adapter actually SENT the venue ------------------------


class TestClientOrderId:
    """`PlaceResult.client_order_id` (#715) round-trips into `orders.client_order_id` through the
    `repo.update_order` call that follows placement -- the id `resolve_client_order_id` minted for
    THIS attempt, not a fresh value invented afterwards.
    """

    def test_the_placed_client_order_id_is_recorded(self, repo) -> None:
        class _Broker(FakeBroker):
            def place_order(self, spec, *, idempotency_key=None):  # noqa: ANN001, ANN202
                self.place_calls.append({"spec": spec})
                return PlaceResult(
                    success=True, broker_order_id="venue-1", client_order_id="coid-abc-123"
                )

        result = execute(_enter_signal(), _Broker(), repo, _config(), "autonomous", now_ts=NOW_TS)

        assert result.placed is True
        order = repo.get_order(result.order_id)
        assert order["client_order_id"] == "coid-abc-123"

    def test_a_rejected_placement_still_records_the_client_order_id(self, repo) -> None:
        """The id was still sent to the venue even though the venue refused the order -- the
        column records what was SENT, not what succeeded."""

        class _Broker(FakeBroker):
            def place_order(self, spec, *, idempotency_key=None):  # noqa: ANN001, ANN202
                self.place_calls.append({"spec": spec})
                return PlaceResult(
                    success=False,
                    broker_order_id=None,
                    reason="no funds",
                    client_order_id="coid-rejected-1",
                )

        result = execute(_enter_signal(), _Broker(), repo, _config(), "autonomous", now_ts=NOW_TS)

        assert result.placed is False
        order = repo.get_order(result.order_id)
        assert order["client_order_id"] == "coid-rejected-1"

    def test_an_adapter_that_reports_no_client_order_id_stores_null_not_empty_string(
        self, repo
    ) -> None:
        """`PlaceResult.client_order_id` defaults to `None` -- an adapter that never populates it
        must round-trip to NULL, never `''`, which this repository's convention reserves for "not
        recorded" and nothing else."""
        broker = FakeBroker()
        result = execute(_enter_signal(), broker, repo, _config(), "autonomous", now_ts=NOW_TS)

        assert result.placed is True
        order = repo.get_order(result.order_id)
        assert order["client_order_id"] is None


class TestProvenanceCannotBlockAPlacement:
    """`_order_row` runs BEFORE `broker.place_order`, so anything raising in it aborts an order
    that would otherwise have gone through.

    `_submit_book` states the rule thirty lines away: "a DIAGNOSTIC column must never be able to
    decide whether an order is placed." `provenance_of` is defensive too -- it maps an
    incomparable size to `unreadable` rather than raising -- but a classifier is not the last
    line here: this test uses a preview whose attribute access itself explodes, which no amount
    of care inside `provenance_of` can catch, and which the wrapper must.
    """

    def test_a_preview_whose_fields_raise_records_nothing_and_builds_the_row_anyway(
        self, repo
    ) -> None:
        """Through `_order_row`, not through the helper: the guard has to be AT THE CALL SITE to
        be worth anything, and a test that called `_safe_provenance` directly passed just as well
        with the call site left unguarded."""
        from keel.execution import executor as executor_mod

        class _Exploding:
            synthetic = False
            est_quote_size = Decimal("100")

            @property
            def est_base_size(self):  # type: ignore[no-untyped-def]
                raise RuntimeError("this venue's preview object is broken")

        intent = executor_mod._build_intent(
            _enter_signal(), None, repo, _config(), now_ts=NOW_TS
        )
        assert intent is not None

        row = executor_mod._order_row(intent, "autonomous", NOW_TS, preview=_Exploding())

        assert row["quote_provenance"] is None

    def test_a_non_finite_size_is_unreadable_rather_than_an_exception(self) -> None:
        """`Decimal("NaN") <= 0` raises `InvalidOperation`. A venue that returns a NaN size must
        not be able to stop a placement with it."""
        from keel_core.quote_provenance import UNREADABLE, provenance_of

        class _Nan:
            synthetic = False
            est_base_size = Decimal("NaN")
            est_quote_size = Decimal("NaN")

        assert provenance_of(_Nan()) == UNREADABLE
