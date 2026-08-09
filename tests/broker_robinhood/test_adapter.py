"""Tests for `RobinhoodAdapter`, the Robinhood Crypto Trading API v2 implementation of the
`Broker` port.

Robinhood ships no sandbox (see `RobinhoodTransport`'s docstring), so every test here injects a
`FakeTransport` returning canned, real-shaped JSON from `tests/fixtures/rh_*.json`. No live
network call is made, and no live order is ever placed -- that is the entire point of the
fixture-driven design this module mirrors from `tests/broker_coinbase/test_adapter.py`.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from keel_broker_api.orders import LimitGTC, MarketIOCByBase, MarketIOCByQuote, StopLimitGTC
from keel_broker_api.port import UnsupportedOrder
from keel_broker_api.results import Balance, FeeSummary, OrderStatus, PlaceResult, Preview
from keel_broker_robinhood import RobinhoodAdapter
from keel_core.types import Granularity, Side

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    with (FIXTURES_DIR / name).open() as f:
        data: dict[str, Any] = json.load(f)
    return data


def _contains_key(obj: Any, key: str) -> bool:
    """Recursively check `obj` for `key` at any nesting depth.

    Used to assert `time_in_force` is absent from a market-order body entirely -- not merely
    absent at the top level -- since a stray copy nested inside `market_order_config` would be
    just as wrong as one at the top, and Robinhood's market orders are IOC-by-construction and
    document no `time_in_force` field anywhere on that shape.
    """
    if isinstance(obj, dict):
        if key in obj:
            return True
        return any(_contains_key(v, key) for v in obj.values())
    return False


class FakeTransport:
    """Duck-types the `Transport` Protocol from the module contract, returning fixtures and
    recording every call's kwargs.

    `_issued_order_ids` mirrors the real venue's own distinction: an id this transport actually
    handed out via `create_order` (or that a test seeded directly, the same shortcut
    `tests/broker_coinbase/test_adapter.py` takes) is "known"; anything else is a 404 the venue
    has never heard of. `get_order`/`cancel_order` return `None` for an unknown id specifically
    because that is the contract's signal that the venue does not recognise it -- distinct from
    every other failure mode, which must raise instead (see `transport.py`'s contract point 1).
    """

    def __init__(
        self,
        accounts: dict[str, Any] | None = None,
        holdings: dict[str, Any] | None = None,
        trading_pairs: dict[str, Any] | None = None,
        best_bid_ask: dict[str, Any] | None = None,
        estimated_price: dict[str, Any] | None = None,
        placed: dict[str, Any] | None = None,
        order: dict[str, Any] | None = None,
    ) -> None:
        self._accounts = accounts
        self._holdings = holdings
        self._trading_pairs = trading_pairs
        self._best_bid_ask = best_bid_ask
        self._estimated_price = estimated_price
        self._placed = placed
        self._order = order
        self.calls: dict[str, dict[str, Any]] = {}
        #: How many times each method was actually called, keyed by method name. Kept separate
        #: from `self.calls` (which only remembers the latest kwargs, matching the Coinbase fake)
        #: because the mandatory-single-re-poll test needs a count, not just the last argument.
        self.call_counts: dict[str, int] = {}
        self._issued_order_ids: set[str] = set()

    def _record(self, name: str, **kwargs: Any) -> None:
        self.calls[name] = kwargs
        self.call_counts[name] = self.call_counts.get(name, 0) + 1

    def get_accounts(self) -> Any:
        self._record("get_accounts")
        return self._accounts

    def get_holdings(self) -> Any:
        self._record("get_holdings")
        return self._holdings

    def get_trading_pairs(self, symbol: str | None = None) -> Any:
        self._record("get_trading_pairs", symbol=symbol)
        return self._trading_pairs

    def get_best_bid_ask(self, symbol: str) -> Any:
        self._record("get_best_bid_ask", symbol=symbol)
        return self._best_bid_ask

    def get_estimated_price(self, symbol: str, side: str, quantity: str) -> Any:
        self._record("get_estimated_price", symbol=symbol, side=side, quantity=quantity)
        return self._estimated_price

    def create_order(self, body: dict[str, Any]) -> Any:
        self._record("create_order", body=body)
        if self._placed is None:
            return None
        issued_id = self._placed.get("id")
        if issued_id is not None:
            self._issued_order_ids.add(issued_id)
        return self._placed

    def get_order(self, order_id: str) -> Any:
        self._record("get_order", order_id=order_id)
        if order_id not in self._issued_order_ids:
            return None
        order = dict(self._order) if self._order is not None else dict(self._placed or {})
        order["id"] = order_id
        return order

    def cancel_order(self, order_id: str) -> Any:
        self._record("cancel_order", order_id=order_id)
        if order_id not in self._issued_order_ids:
            return None
        order = dict(self._order) if self._order is not None else dict(self._placed or {})
        order["id"] = order_id
        order["state"] = "canceled"
        return order


class _ReCancelTransport(FakeTransport):
    """A `cancel_order` that answers with a still-`open` order every time, regardless of what
    actually happened -- standing in for the real venue's cancel endpoint returning the order as
    it stood at the moment cancellation was requested, before the cancellation itself has
    settled. This is what makes the adapter's mandatory single re-poll of `get_order` observable:
    the FINAL answer must come from that re-poll, not from this method's own return value.
    """

    def cancel_order(self, order_id: str) -> Any:
        self._record("cancel_order", order_id=order_id)
        if order_id not in self._issued_order_ids:
            return None
        order = dict(self._order or {})
        order["id"] = order_id
        order["state"] = "open"
        return order


def test_capabilities_declare_robinhood() -> None:
    """The engine gates live spend on these declarations, so each clause here is a promise the
    rest of the suite must keep: no native preview, a synthesized one instead, fee summaries
    available, USD-only, spot-only."""
    caps = RobinhoodAdapter().capabilities()

    assert caps.venue == "robinhood"
    assert caps.supports_native_preview is False
    assert caps.synthesizes_preview is True
    assert caps.supports_fee_summary is True
    assert caps.quote_currencies == frozenset({"USD"})
    assert caps.asset_classes == frozenset({"spot"})
    assert caps.can_preview


def test_get_candles_refuses_every_granularity_by_name() -> None:
    """Robinhood's Crypto Trading API has no OHLC/historical endpoint at all -- there is nothing
    to page through, at any resolution. A `ValueError` that does not name this would look like a
    caller passed a bad argument rather than the true reason: this venue cannot serve candles,
    full stop, and strategy code must source them elsewhere."""
    adapter = RobinhoodAdapter(FakeTransport())
    for granularity in Granularity:
        with pytest.raises(ValueError, match="no (candle|OHLC|historical)"):
            adapter.get_candles("BTC-USD", granularity, 0, 86_400)


def test_get_balances_returns_one_balance_per_holding_plus_buying_power() -> None:
    """Buying power is not a holding -- it is the account's own USD balance -- so an adapter that
    forgot it would under-report available capital to anything sizing an order off `get_balances`
    alone."""
    transport = FakeTransport(
        accounts=load_fixture("rh_accounts.json"), holdings=load_fixture("rh_holdings.json")
    )
    adapter = RobinhoodAdapter(transport)

    balances = adapter.get_balances()

    assert balances
    assert all(isinstance(b, Balance) for b in balances)
    holding = load_fixture("rh_holdings.json")["results"][0]
    btc = next(b for b in balances if b.currency == holding["asset_code"])
    assert btc.available == Decimal(holding["quantity_available_for_trading"])
    assert btc.total == Decimal(holding["total_quantity"])

    account = load_fixture("rh_accounts.json")["results"][0]
    usd = next(b for b in balances if b.currency == account["buying_power_currency"])
    assert usd.available == Decimal(account["buying_power"])
    assert usd.total == Decimal(account["buying_power"])

    assert len(balances) == len(load_fixture("rh_holdings.json")["results"]) + 1


def test_preview_order_limit_gtc_prices_off_the_limit_price() -> None:
    """A LimitGTC preview needs no live quote at all -- the caller already named the price they
    will pay -- so `detail["price_basis"]` must say `"limit_price"` and the estimate must be
    exact arithmetic, not a market snapshot that could disagree with the order about to be
    placed."""
    transport = FakeTransport(accounts=load_fixture("rh_accounts.json"))
    adapter = RobinhoodAdapter(transport)
    spec = LimitGTC(
        product_id="BTC-USD", side=Side.SELL, base_size=Decimal("0.1"), limit_price=Decimal("65000")
    )

    preview = adapter.preview_order(spec)

    fee_ratio = Decimal(
        load_fixture("rh_accounts.json")["results"][0]["fee_tier_status"]["fee_ratio"]
    )
    assert isinstance(preview, Preview)
    assert preview.synthetic is True
    assert preview.est_base_size == Decimal("0.1")
    assert preview.est_quote_size == Decimal("0.1") * Decimal("65000")
    assert preview.est_fee == preview.est_quote_size * fee_ratio
    assert preview.detail["price_basis"] == "limit_price"


def test_preview_order_market_ioc_base_prices_off_the_estimated_price_endpoint() -> None:
    """Unlike a limit order, a market order names no price of its own -- the only honest estimate
    Robinhood can offer is its `estimated_price` endpoint, and `detail["price_basis"]` must say so
    plainly rather than let the caller mistake this for a firm quote."""
    transport = FakeTransport(
        accounts=load_fixture("rh_accounts.json"),
        estimated_price=load_fixture("rh_estimated_price.json"),
    )
    adapter = RobinhoodAdapter(transport)
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=Decimal("0.1"))

    preview = adapter.preview_order(spec)

    price = Decimal(load_fixture("rh_estimated_price.json")["results"][0]["price"])
    assert preview.synthetic is True
    assert preview.est_base_size == Decimal("0.1")
    assert preview.est_quote_size == Decimal("0.1") * price
    assert preview.detail["price_basis"] == "estimated_price"
    assert transport.calls["get_estimated_price"]["symbol"] == "BTC-USD"


def test_place_order_market_ioc_base_sends_asset_quantity_and_no_time_in_force() -> None:
    """Robinhood's `market_order_config` accepts only `asset_quantity` -- there is no
    quote-sized market order and no `time_in_force` on this shape at all (market orders are
    IOC-by-construction). A stray `time_in_force` key anywhere in the body would be a sign the
    translator leaked a field this order type does not carry."""
    transport = FakeTransport(placed=load_fixture("rh_order_open.json"))
    adapter = RobinhoodAdapter(transport)
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=Decimal("0.1"))

    adapter.place_order(spec)

    body = transport.calls["create_order"]["body"]
    assert body["market_order_config"] == {"asset_quantity": "0.1"}
    assert not _contains_key(body, "time_in_force")


def test_place_order_limit_gtc_sends_the_full_limit_config() -> None:
    transport = FakeTransport(placed=load_fixture("rh_order_open.json"))
    adapter = RobinhoodAdapter(transport)
    spec = LimitGTC(
        product_id="BTC-USD", side=Side.SELL, base_size=Decimal("0.1"), limit_price=Decimal("65000")
    )

    adapter.place_order(spec)

    body = transport.calls["create_order"]["body"]
    assert body["limit_order_config"] == {
        "asset_quantity": "0.1",
        "limit_price": "65000",
        "time_in_force": "gtc",
    }


def test_place_order_stop_limit_gtc_sends_the_full_stop_limit_config() -> None:
    transport = FakeTransport(placed=load_fixture("rh_order_open.json"))
    adapter = RobinhoodAdapter(transport)
    spec = StopLimitGTC(
        product_id="BTC-USD",
        side=Side.SELL,
        base_size=Decimal("0.1"),
        stop_price=Decimal("60000"),
        limit_price=Decimal("59900"),
    )

    adapter.place_order(spec)

    body = transport.calls["create_order"]["body"]
    assert body["stop_limit_order_config"] == {
        "asset_quantity": "0.1",
        "limit_price": "59900",
        "stop_price": "60000",
        "time_in_force": "gtc",
    }


def test_place_order_market_ioc_by_quote_is_refused_as_the_entry_path() -> None:
    """`MarketIOCByQuote` is how keel enters positions. Robinhood's `market_order_config` takes
    only `asset_quantity`, so there is no quote-sized market order on this API at all -- meaning
    this adapter cannot open positions under keel's current entry model, only size exits and rest
    limit/stop-limit orders. Synthesising a quote-sized order by dividing an estimated price is
    deliberately NOT done: that would substitute a different sizing basis (a snapshot estimate)
    for the one the caller actually asked for, on the live-money path, and it would do so
    silently. `UnsupportedOrder` here is the honest refusal; a fabricated fill would not be."""
    adapter = RobinhoodAdapter(FakeTransport())
    spec = MarketIOCByQuote(product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("100"))

    with pytest.raises(UnsupportedOrder):
        adapter.place_order(spec)


def test_place_order_generates_a_fresh_client_order_id_per_call() -> None:
    """Idempotency on Robinhood's side depends on this being unique per attempt -- a reused id on
    a retried call could be read as a duplicate and silently dropped, or worse, matched to the
    wrong attempt's outcome."""
    transport = FakeTransport(placed=load_fixture("rh_order_open.json"))
    adapter = RobinhoodAdapter(transport)
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=Decimal("0.1"))

    adapter.place_order(spec)
    first = transport.calls["create_order"]["body"]["client_order_id"]
    adapter.place_order(spec)
    second = transport.calls["create_order"]["body"]["client_order_id"]

    assert first != second
    uuid.UUID(first)
    uuid.UUID(second)


def test_get_order_maps_fill_quantity_average_price_and_fee() -> None:
    """Reconciliation needs OBSERVED fill data, not the expected price and previewed fee the
    executor recorded at placement time -- `filled_asset_quantity`, `average_price`, and
    `fee_charged` are the only fields on this venue that can supply it."""
    fixture = load_fixture("rh_order_filled.json")
    transport = FakeTransport(order=fixture)
    transport._issued_order_ids.add(fixture["id"])
    adapter = RobinhoodAdapter(transport)

    order = adapter.get_order(fixture["id"])

    assert isinstance(order, OrderStatus)
    assert order.status == "FILLED"
    assert order.filled_size == Decimal(fixture["filled_asset_quantity"])
    assert order.average_filled_price == Decimal(fixture["average_price"])
    assert order.total_fees == Decimal(fixture["fee_charged"])


def test_get_order_maps_canceled_state_to_the_ports_doubled_l_spelling() -> None:
    """Robinhood spells it `canceled` (American, one `l`); the port spells it `CANCELLED`. This
    is the one place those two spellings must actually agree, or a genuinely cancelled order
    would read as an unrecognised state to every downstream consumer of `OrderStatus.status`."""
    fixture = load_fixture("rh_order_canceled.json")
    transport = FakeTransport(order=fixture)
    transport._issued_order_ids.add(fixture["id"])
    adapter = RobinhoodAdapter(transport)

    order = adapter.get_order(fixture["id"])

    assert order.status == "CANCELLED"


def test_get_order_on_an_unknown_id_reports_failed_with_zeroed_money_fields_not_a_raise() -> None:
    """An id the venue has never heard of is a 404, and `get_order` must turn that into an
    ordinary `OrderStatus` the caller can do arithmetic on -- never an exception a reconciliation
    loop would have to special-case, and never `None` money fields a caller would have to guard
    before every calculation."""
    adapter = RobinhoodAdapter(FakeTransport())

    order = adapter.get_order("an-id-this-venue-never-issued")

    assert order.status == "FAILED"
    assert order.filled_size == Decimal("0")
    assert order.average_filled_price == Decimal("0")
    assert order.total_fees == Decimal("0")


def test_get_order_on_an_unrecognised_venue_state_reports_pending_not_failed() -> None:
    """An unrecognised `state` string means the adapter does not actually know the order's
    outcome -- not that the order failed. Reporting `FAILED` here would declare a terminal
    outcome nobody observed, and the engine could then re-enter a position whose original order
    is, for all this adapter knows, still live at the venue. `PENDING` keeps it under
    observation instead, which is the only honest answer to "I don't know"."""
    fixture = dict(load_fixture("rh_order_open.json"))
    fixture["state"] = "a_future_state_this_adapter_predates"
    transport = FakeTransport(order=fixture)
    transport._issued_order_ids.add(fixture["id"])
    adapter = RobinhoodAdapter(transport)

    order = adapter.get_order(fixture["id"])

    assert order.status == "PENDING"


def test_cancel_order_returns_true_when_the_venue_confirms_immediately() -> None:
    fixture = load_fixture("rh_order_open.json")
    transport = FakeTransport(order=fixture)
    transport._issued_order_ids.add(fixture["id"])
    adapter = RobinhoodAdapter(transport)

    assert adapter.cancel_order(fixture["id"]) is True


def test_cancel_order_re_polls_once_and_true_comes_from_the_poll() -> None:
    """Robinhood's cancel endpoint can hand back the order as it stood the instant cancellation
    was requested, before the cancellation itself has settled -- so a cancel response that is not
    yet `canceled` is not evidence of failure either. The adapter must re-poll `get_order` exactly
    once and trust THAT answer, not retry forever and not give up after the first ambiguous
    response."""
    fixture = load_fixture("rh_order_canceled.json")
    transport = _ReCancelTransport(order=fixture)
    transport._issued_order_ids.add(fixture["id"])
    adapter = RobinhoodAdapter(transport)

    assert adapter.cancel_order(fixture["id"]) is True
    assert transport.call_counts.get("get_order", 0) == 1


def test_cancel_order_re_polls_once_and_false_comes_from_the_poll() -> None:
    """The mirror of the case above: if the single re-poll still shows the order resting `open`,
    the cancel must be reported as failed rather than optimistically assumed -- a `True` the venue
    never actually confirmed would let `executor._cancel_at_exchange` record a cancel that never
    happened."""
    fixture = load_fixture("rh_order_open.json")
    transport = _ReCancelTransport(order=fixture)
    transport._issued_order_ids.add(fixture["id"])
    adapter = RobinhoodAdapter(transport)

    assert adapter.cancel_order(fixture["id"]) is False
    assert transport.call_counts.get("get_order", 0) == 1


def test_cancel_order_on_an_unknown_id_returns_false_and_does_not_raise() -> None:
    """Absence of a refusal is not a confirmation, and an id the venue never issued is not a
    network failure either -- it must fail closed as an ordinary `False`, matching the same
    contract Coinbase's adapter is held to."""
    adapter = RobinhoodAdapter(FakeTransport())

    assert adapter.cancel_order("an-id-this-venue-never-issued") is False


def test_get_fee_summary_maps_fee_ratio_to_both_taker_and_maker() -> None:
    """Robinhood's `fee_tier_status` publishes a single `fee_ratio`, not separate maker/taker
    rates -- so both must be populated from the same field rather than one silently defaulting to
    zero, which would understate the venue's true cost on whichever side went unmapped."""
    transport = FakeTransport(accounts=load_fixture("rh_accounts.json"))
    adapter = RobinhoodAdapter(transport)

    summary = adapter.get_fee_summary()

    fee_tier = load_fixture("rh_accounts.json")["results"][0]["fee_tier_status"]
    assert isinstance(summary, FeeSummary)
    assert summary.venue == "robinhood"
    assert summary.taker_rate == Decimal(fee_tier["fee_ratio"])
    assert summary.maker_rate == Decimal(fee_tier["fee_ratio"])
    assert summary.volume_usd == Decimal(fee_tier["thirty_day_volume"])


def test_get_fee_summary_declares_a_trailing_30d_window_by_name() -> None:
    """Coinbase's adapter declares `"unknown"` here because Coinbase's own docs never state the
    window, and guessing would let reconciliation compare a possibly-trailing-30-day volume
    against a calendar-month allowance. Robinhood is different: `fee_tier_status.thirty_day_volume`
    names its own window in the FIELD NAME itself, so declaring `"trailing_30d"` is not a guess --
    it is reading what the venue already told us, and withholding it as `"unknown"` would be the
    less honest choice here, not the more cautious one."""
    transport = FakeTransport(accounts=load_fixture("rh_accounts.json"))
    adapter = RobinhoodAdapter(transport)

    assert adapter.get_fee_summary().volume_window == "trailing_30d"


def test_get_fee_summary_reports_zero_fees_paid() -> None:
    """Robinhood's `fee_tier_status` exposes a rate and a volume, but no account-level
    fees-PAID total -- so `fees_usd` must be `Decimal("0")` rather than derived (rate * volume
    would be an estimate dressed up as an observation). Subscription lapse detection reads this
    field, and a nonzero value here that was never actually charged could mask a real lapse."""
    transport = FakeTransport(accounts=load_fixture("rh_accounts.json"))
    adapter = RobinhoodAdapter(transport)

    assert adapter.get_fee_summary().fees_usd == Decimal("0")


def test_a_transportless_adapter_refuses_network_calls_clearly() -> None:
    """`capabilities()` must work offline -- the engine needs it to decide whether to even wire
    this venue up before any credentials exist. Anything that actually needs the network must say
    why it cannot, rather than fail with an opaque `AttributeError` on `self._transport`."""
    adapter = RobinhoodAdapter()

    assert adapter.capabilities().venue == "robinhood"
    with pytest.raises(RuntimeError, match="without a transport"):
        adapter.get_balances()


def test_entry_point_discovery_finds_the_robinhood_adapter() -> None:
    """Installing this package must be sufficient to make it discoverable -- `keel add
    keel-broker-robinhood` and nothing else. A broken entry point here would silently strand the
    adapter unreachable by `load_broker`, which is the only path the (future) broker-port
    migration uses to find it."""
    from keel_broker_api.registry import load_broker

    assert load_broker("robinhood").__name__ == "RobinhoodAdapter"


def test_place_order_returns_a_domain_type() -> None:
    """No Robinhood-native order object may cross the port -- a caller must only ever see
    `PlaceResult`."""
    transport = FakeTransport(placed=load_fixture("rh_order_open.json"))
    adapter = RobinhoodAdapter(transport)
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=Decimal("0.1"))

    result = adapter.place_order(spec)

    assert isinstance(result, PlaceResult)
