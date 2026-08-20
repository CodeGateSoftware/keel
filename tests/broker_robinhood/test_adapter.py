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
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from keel_broker_api.orders import LimitGTC, MarketIOCByBase, MarketIOCByQuote, StopLimitGTC
from keel_broker_api.port import UnsupportedOrder
from keel_broker_api.results import (
    Balance,
    FeeSummary,
    OrderStatus,
    PlaceResult,
    Preview,
    SessionState,
)
from keel_broker_robinhood import RobinhoodAdapter
from keel_broker_robinhood.translate import STATE_TO_PORT_STATUS, TIME_IN_FORCE
from keel_core.types import Granularity, Side

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    """Decode a fixture the way `RobinhoodTransport._request` decodes a live response.

    `parse_float=Decimal` is not a stylistic flourish here, it is the whole point of the fixture.
    Live runs against a real credential (#217 F2/F6) established that money on this venue arrives
    as an UNQUOTED JSON number on some endpoints -- `"ask": 64975.78`, not `"ask": "64975.78"` --
    and the transport parses those with `parse_float=Decimal` precisely so the original digits
    reach the adapter instead of whatever a binary `float` rounded them to. A fixture decoded with
    a plain `json.load` would hand the adapter `float`s the live path can never produce, so the
    suite would be exercising a code path that does not exist in production and leaving the one
    that does untested. It also silently weakens equality assertions: `Decimal(0.00000001)` is
    `1.00000000000000002092256083497e-8`, which is not `Decimal("0.00000001")`.

    The fixtures are quoted field-for-field the way the venue quotes them, which means MIXED --
    see `test_this_venue_is_not_internally_consistent_about_quoting`. Normalizing them one way or
    the other would read better and would be a false claim about the API.
    """
    with (FIXTURES_DIR / name).open() as f:
        data: dict[str, Any] = json.load(f, parse_float=Decimal)
    return data


#: The size `rh_estimated_price*.json` was quoted FOR, read back out of the fixture.
#:
#: Every preview test that drives those fixtures sizes its spec from this rather than a literal,
#: because the adapter refuses to read `est_fee`/`est_total_cost` when the venue's echoed
#: `quantity` is not the size that was requested -- the two are a matched pair, and a literal in
#: the test would silently start exercising the mismatch path the day the fixture is re-captured
#: from a live run at a different size. Both fixtures are verbatim live rows (#217 F1/F7), and
#: `0.001 BTC` is the size the probe quotes at.
_QUOTED_SIZE = Decimal("0.001")


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
        orders: dict[str, Any] | None = None,
    ) -> None:
        self._accounts = accounts
        self._holdings = holdings
        self._trading_pairs = trading_pairs
        self._best_bid_ask = best_bid_ask
        self._estimated_price = estimated_price
        self._placed = placed
        self._order = order
        self._orders = orders
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

    def get_orders(self, updated_at_start: str | None = None) -> Any:
        """The order-history list, returned WHOLE regardless of `updated_at_start`.

        Deliberately not filtered here. `updated_at_start` is a SERVER-side filter in the real
        API, so an adapter that trusted a client-side reimplementation of it in this fake would
        be tested against a filter that does not exist in production. What the adapter owes is
        that it sends the right window, and that is asserted directly against
        `calls["get_orders"]["updated_at_start"]` instead.
        """
        self._record("get_orders", updated_at_start=updated_at_start)
        return self._orders

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


class _RaisingCancelTransport(FakeTransport):
    """A transport whose `cancel_order` raises the way a 5xx or a dropped connection does.

    `RobinhoodTransport._request` deliberately raises for every failure that is not a 404, so this
    is the realistic shape of a venue outage during a cancel -- and `cancel_order` runs on the
    EXIT path, where a raise can trap a position.
    """

    def cancel_order(self, order_id: str) -> Any:
        self._record("cancel_order", order_id=order_id)
        raise RuntimeError("503 Server Error: Service Unavailable")


class _RaisingRepollTransport(FakeTransport):
    """Cancel answers ambiguously (still `open`), and the mandatory re-poll is what blows up."""

    def cancel_order(self, order_id: str) -> Any:
        self._record("cancel_order", order_id=order_id)
        order = dict(self._order or {})
        order["id"] = order_id
        order["state"] = "open"
        return order

    def get_order(self, order_id: str) -> Any:
        self._record("get_order", order_id=order_id)
        raise RuntimeError("503 Server Error: Service Unavailable")


def _placed_with_state(state: str) -> dict[str, Any]:
    """An order-placement response carrying `state`, otherwise shaped like a real one."""
    placed = load_fixture("rh_order_open.json")
    placed["state"] = state
    return placed


def _pairs() -> dict:
    """The BTC-USD `trading_pairs` row, freshly loaded per call.

    `preview_order` reads the venue's sizing bounds, so a fake that answers `accounts` and
    `estimated_price` but not this one is an INCOMPLETE venue, not a neutral one -- it makes
    every preview report that the bounds could not be checked. Tests about pricing therefore
    supply it, and the ones that genuinely mean "the venue did not answer" leave it out on
    purpose and assert the note that produces.
    """
    return load_fixture("rh_trading_pairs.json")


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
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.BUY, base_size=_QUOTED_SIZE)

    preview = adapter.preview_order(spec)

    price = Decimal(load_fixture("rh_estimated_price.json")["results"][0]["ask"])
    assert preview.synthetic is True
    assert preview.est_base_size == _QUOTED_SIZE
    assert preview.est_quote_size == _QUOTED_SIZE * price
    assert preview.detail["price_basis"] == "estimated_price"
    assert transport.calls["get_estimated_price"]["symbol"] == "BTC-USD"


def test_preview_order_reads_the_ask_column_because_the_venue_sends_no_price_field() -> None:
    """The #217 F1 blocker, pinned as a test: the row has no `price` key at all.

    `_estimated_price` read `_field(rows[0], "price", "0")`, which the documentation supported and
    the venue does not. The first live run proved every market preview came back
    `est_quote_size = 0.000` with `errors` populated -- confirm mode was unusable against this
    venue, and the only reason it was survivable is the #194 S1 change that turned an unpriced
    lookup into `None` rather than a silent `Decimal("0")`.

    Asserting the fixture carries no `price` key is deliberate. Without it, someone could
    "fix" this by reading `price` with an `ask` fallback, the fixture would keep both keys, and
    the suite would go on passing against a shape the venue never sends.
    """
    row = load_fixture("rh_estimated_price.json")["results"][0]
    assert "price" not in row, "the venue sends no 'price' on estimated_price -- see #217 F1"
    assert "ask" in row

    transport = FakeTransport(
        trading_pairs=_pairs(),
        accounts=load_fixture("rh_accounts.json"),
        estimated_price=load_fixture("rh_estimated_price.json"),
    )
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.BUY, base_size=_QUOTED_SIZE)

    preview = RobinhoodAdapter(transport).preview_order(spec)

    assert preview.errors == (), "a fully-priced venue response must not report a pricing failure"
    assert preview.est_quote_size == _QUOTED_SIZE * Decimal(row["ask"])


def test_preview_order_reads_the_column_named_after_the_side_it_asked_for() -> None:
    """A SELL is priced from `bid` and a BUY from `ask` -- never whichever column happens to
    be present.

    The endpoint is asked for one side (`to_price_side`: buy -> ask, sell -> bid) and names the
    price column after it. Falling back to the *other* column when the requested one is absent
    would price a sell off the ask, overstating the proceeds of every exit -- the optimistic
    direction `to_price_side`'s docstring exists to prevent, and the one a human at a confirm gate
    is least likely to catch. So a row carrying only the wrong side is treated as UNPRICED.
    """
    ask_only = load_fixture("rh_estimated_price.json")
    bid_only = load_fixture("rh_estimated_price_bid.json")
    bid_row = bid_only["results"][0]
    accounts = load_fixture("rh_accounts.json")

    sell = MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=_QUOTED_SIZE)
    priced = RobinhoodAdapter(
        FakeTransport(trading_pairs=_pairs(), accounts=accounts, estimated_price=bid_only)
    ).preview_order(sell)
    assert priced.errors == ()
    assert priced.est_quote_size == _QUOTED_SIZE * Decimal(bid_row["bid"])

    unpriced = RobinhoodAdapter(
        FakeTransport(trading_pairs=_pairs(), accounts=accounts, estimated_price=ask_only)
    ).preview_order(sell)
    assert unpriced.errors, "a sell priced off an ask-only row must not be reported as priced"


def test_preview_order_prices_a_sell_without_the_est_total_cost_the_venue_omits() -> None:
    """#217 F7: `est_total_cost` comes back on the ASK side only. The bid row has no total.

    Observed live in the same minute, same credential, same symbol::

        side=ask -> {..., 'est_fee': ..., 'ask': ...,  'est_total_cost': ...}
        side=bid -> {..., 'est_fee': ..., 'bid': ...}                        # no total

    That is not a degraded response and must not read as one. A sell prices from `bid * quantity`
    with the venue's own `est_fee` beside it, which is a complete answer -- `errors` stays empty,
    and `detail["cost_basis"]` says `price_x_quantity` rather than naming a total that was never
    sent. Getting this wrong in the direction of caution would be its own failure: an exit preview
    that reports an error every single time is an exit preview nobody reads.

    The asymmetry is at least coherent with what the fields mean -- "total cost" is a buyer's
    concept -- but the venue documents neither field, so this is pinned as an observation.
    """
    fixture = load_fixture("rh_estimated_price_bid.json")
    row = fixture["results"][0]
    assert "est_total_cost" not in row, "the venue sends no total on the bid side -- see #217 F7"

    transport = FakeTransport(
        trading_pairs=_pairs(),
        accounts=load_fixture("rh_accounts.json"),
        estimated_price=fixture,
    )
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=_QUOTED_SIZE)

    preview = RobinhoodAdapter(transport).preview_order(spec)

    assert preview.errors == ()
    assert preview.est_quote_size == _QUOTED_SIZE * Decimal(row["bid"])
    assert preview.est_fee == Decimal(row["est_fee"])
    assert preview.detail["cost_basis"] == "price_x_quantity"
    assert preview.detail["fee_basis"] == "venue_est_fee"
    assert "get_accounts" not in transport.calls, (
        "the venue stated the fee, so the account round trip must be skipped"
    )


def test_preview_order_takes_the_fee_from_the_venue_rather_than_deriving_it() -> None:
    """`est_fee` comes back on the row, so deriving one from the account's tier is second-hand.

    The venue states the fee it will charge for THIS quantity on THIS side. Multiplying our own
    notional by `fee_tier_status.fee_ratio` reproduces a number the response already contains, and
    the two can disagree -- the account tier is an account-level rate, while the row's `fee_ratio`
    is the one quoted against this order. When the venue states it, the venue wins, and
    `detail["fee_basis"]` records which of the two was used so a reader never has to guess.
    """
    row = load_fixture("rh_estimated_price.json")["results"][0]
    transport = FakeTransport(
        accounts=load_fixture("rh_accounts.json"),
        estimated_price=load_fixture("rh_estimated_price.json"),
    )
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.BUY, base_size=_QUOTED_SIZE)

    preview = RobinhoodAdapter(transport).preview_order(spec)

    assert preview.est_fee == Decimal(row["est_fee"])
    assert preview.detail["fee_basis"] == "venue_est_fee"
    assert preview.detail["fee_ratio"] == str(Decimal(row["fee_ratio"]))


def test_preview_order_splits_a_fee_inclusive_est_total_cost_back_out() -> None:
    """`est_total_cost` is reconciled against the venue's own numbers, not assumed either way.

    `Preview` carries `est_quote_size` and `est_fee` as SEPARATE fields, and the limit path fills
    `est_quote_size` with `base_size * limit_price` -- a notional that excludes the fee. So a
    fee-INCLUSIVE `est_total_cost` assigned straight into `est_quote_size` would double-count the
    fee at the confirm gate (once inside the quote size, once in `est_fee`).

    Nothing here assumes which it is: the row carries `ask`, `quantity`, `est_fee` and
    `est_total_cost`, which is one equation with a single unknown, and the adapter reconciles the
    response it actually received.

    The fixture is a verbatim live row, and it settles the question empirically -- the venue's own
    numbers satisfy the fee-INCLUSIVE relation to the last digit::

        64975.78 * 0.001 + 0.61726991 == 65.59304991

    That is now an observation rather than a prior, and it is exactly the reading that would have
    double-counted the fee had `est_total_cost` been assigned straight into `est_quote_size`. The
    reconciliation still runs on every response: this is one symbol, one side, one moment, and the
    bid side does not even send the field (#217 F7).
    """
    row = load_fixture("rh_estimated_price.json")["results"][0]
    notional = Decimal(row["ask"]) * Decimal(row["quantity"])
    assert Decimal(row["est_total_cost"]) == notional + Decimal(row["est_fee"]), (
        "the committed fixture must encode a self-consistent fee-inclusive total, so a live run "
        "that disagrees falsifies it"
    )

    transport = FakeTransport(
        accounts=load_fixture("rh_accounts.json"),
        estimated_price=load_fixture("rh_estimated_price.json"),
    )
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.BUY, base_size=_QUOTED_SIZE)

    preview = RobinhoodAdapter(transport).preview_order(spec)

    assert preview.est_quote_size == notional
    assert preview.est_quote_size + preview.est_fee == Decimal(row["est_total_cost"])
    assert preview.detail["cost_basis"] == "est_total_cost_less_est_fee"


def test_preview_order_accepts_a_fee_exclusive_est_total_cost_unchanged() -> None:
    """The other reading of the same field, and the adapter must not force one onto the other.

    If `est_total_cost` turns out to be the fee-EXCLUSIVE notional, subtracting `est_fee` from it
    would understate the order by exactly one fee. The reconciliation is what tells the two apart,
    and `detail["cost_basis"]` reports which relation the venue's own numbers satisfied.
    """
    fixture = load_fixture("rh_estimated_price.json")
    row = dict(fixture["results"][0])
    row["est_total_cost"] = Decimal(row["ask"]) * Decimal(row["quantity"])
    transport = FakeTransport(
        trading_pairs=_pairs(),
        accounts=load_fixture("rh_accounts.json"), estimated_price={"results": [row]}
    )
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.BUY, base_size=_QUOTED_SIZE)

    preview = RobinhoodAdapter(transport).preview_order(spec)

    assert preview.errors == ()
    assert preview.est_quote_size == row["est_total_cost"]
    assert preview.detail["cost_basis"] == "est_total_cost"


def test_preview_order_reports_a_total_that_reconciles_with_nothing() -> None:
    """A total matching neither the notional nor the notional +/- the fee is not understood.

    This is the case that must never pass silently: the adapter has four numbers from the venue
    and no interpretation of `est_total_cost` that fits them. Rendering that as an ordinary
    preview would put a cost in front of a human with an unverified relationship to the order.
    `Preview.errors` is the port's channel for a soft failure, so the unreconciled total surfaces
    there rather than in `detail`, which a renderer is free not to show.
    """
    fixture = load_fixture("rh_estimated_price.json")
    row = dict(fixture["results"][0])
    row["est_total_cost"] = Decimal("9999.99")
    transport = FakeTransport(
        accounts=load_fixture("rh_accounts.json"), estimated_price={"results": [row]}
    )
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.BUY, base_size=_QUOTED_SIZE)

    preview = RobinhoodAdapter(transport).preview_order(spec)

    assert preview.errors
    assert any("est_total_cost" in error for error in preview.errors)
    assert preview.est_quote_size == Decimal("9999.99")
    assert preview.detail["cost_basis"] == "est_total_cost_unreconciled"


def test_preview_order_falls_back_to_price_times_quantity_without_a_total() -> None:
    """No `est_total_cost` on the row is not a failure -- the price and the size still price it."""
    fixture = load_fixture("rh_estimated_price.json")
    row = {k: v for k, v in fixture["results"][0].items() if k != "est_total_cost"}
    transport = FakeTransport(
        trading_pairs=_pairs(),
        accounts=load_fixture("rh_accounts.json"), estimated_price={"results": [row]}
    )
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.BUY, base_size=_QUOTED_SIZE)

    preview = RobinhoodAdapter(transport).preview_order(spec)

    assert preview.errors == ()
    assert preview.est_quote_size == _QUOTED_SIZE * Decimal(row["ask"])
    assert preview.detail["cost_basis"] == "price_x_quantity"


def test_preview_order_refuses_venue_totals_quoted_for_a_different_quantity() -> None:
    """`est_fee` and `est_total_cost` describe the quantity the VENUE echoed, not ours.

    If the echoed `quantity` is not the size that was asked for, the row's totals are answers to a
    different question and must not be read as this order's cost -- scaling them would be exactly
    the "estimate that moves between the quote and the fill" this package refuses everywhere else.
    The unit price still prices the order, so the preview degrades to `price * base_size` and says
    why, rather than failing outright on an EXIT path.
    """
    fixture = load_fixture("rh_estimated_price.json")
    row = dict(fixture["results"][0])
    row["quantity"] = _QUOTED_SIZE * 10
    transport = FakeTransport(
        accounts=load_fixture("rh_accounts.json"), estimated_price={"results": [row]}
    )
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.BUY, base_size=_QUOTED_SIZE)

    preview = RobinhoodAdapter(transport).preview_order(spec)

    assert preview.est_quote_size == _QUOTED_SIZE * Decimal(row["ask"])
    assert preview.detail["cost_basis"] == "price_x_base_size"
    assert preview.detail["fee_basis"] == "account_fee_ratio"
    assert any("quantity" in error for error in preview.errors)


def test_preview_order_is_never_a_native_preview_however_much_the_venue_states() -> None:
    """Reading the venue's own `est_fee`/`est_total_cost` does NOT make this a broker quote.

    `/estimated_price/` prices a QUANTITY. It does not validate the order, check buying power,
    check the venue's own size bounds, or reserve anything -- an order this endpoint prices
    happily can still be rejected the instant it is placed. That gap is exactly what
    `Preview.synthetic` exists to carry, so it stays `True` and `supports_native_preview` stays
    `False` no matter how many of the numbers came from the venue.
    """
    transport = FakeTransport(
        accounts=load_fixture("rh_accounts.json"),
        estimated_price=load_fixture("rh_estimated_price.json"),
    )
    adapter = RobinhoodAdapter(transport)
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.BUY, base_size=_QUOTED_SIZE)

    assert adapter.capabilities().supports_native_preview is False
    assert adapter.preview_order(spec).synthetic is True


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


def test_get_order_reads_the_same_money_whether_the_venue_quotes_it_or_not() -> None:
    """`rh_order_filled.json` is the one order fixture this repository has never seen live, on a
    venue that is demonstrably inconsistent about quoting (#217 F6).

    Observing a FILLED order means placing an order that can fill, which the probe refuses by
    construction -- it prices its order 50% below the bid for exactly that reason -- so
    `average_price` and a non-zero `fee_charged` could still arrive either way and this package
    has no basis to prefer one. The 2026-08-20 live run (#412) proved the concern is not
    hypothetical: the same order object quotes `filled_asset_quantity` as a string while sending
    `fee_charged` as an unquoted number. Rather than commit to a guess in the fixture and leave
    the other half untested, this drives BOTH forms through `get_order` and requires identical
    `Decimal`s out. `Decimal(str(value))` is what makes that true -- exact for a `str`, a
    round-trip no-op for a `Decimal` -- and this is the test that fails if anyone "simplifies" it
    to `Decimal(value)`.
    """
    unquoted = load_fixture("rh_order_filled.json")
    quoted = {
        key: (str(value) if isinstance(value, Decimal) else value)
        for key, value in unquoted.items()
    }
    assert quoted != unquoted, "the fixture must carry unquoted numbers for this to prove anything"

    orders = []
    for fixture in (unquoted, quoted):
        transport = FakeTransport(order=fixture)
        transport._issued_order_ids.add(fixture["id"])
        orders.append(RobinhoodAdapter(transport).get_order(fixture["id"]))

    assert orders[0] == orders[1]
    assert orders[0].average_filled_price == Decimal("65420.75")
    assert orders[0].total_fees == Decimal("1.6355")


# --- what the venue actually sends (#412, observed 2026-08-20) -------------------------------
# `rh_order_open.json` and `rh_order_canceled.json` are one real BTC-USD limit buy's own
# responses -- placed 50% below the bid so it could not fill, polled, cancelled. Before that run
# both were transcribed from Robinhood's documentation, and the documentation was wrong about
# four fields. These pin the corrections, because the failure mode they guard is silent: an
# adapter reading a field the venue does not send gets `None`, and `None` money reads as zero.

_OBSERVED_ORDER_FIXTURES = ("rh_order_open.json", "rh_order_canceled.json")


@pytest.mark.parametrize("name", _OBSERVED_ORDER_FIXTURES)
def test_the_venue_quotes_order_sizes_and_prices_as_strings(name: str) -> None:
    """Sizes and prices on an ORDER object arrive QUOTED, padded to 18 decimal places.

    The doc-derived fixtures had all three of these as unquoted JSON numbers, which
    `load_fixture`'s `parse_float=Decimal` would have turned into `Decimal`s. That is the wrong
    half of #217 F6 for this endpoint, and it matters beyond tidiness: `_decimal_or_none` and
    `Decimal(str(...))` are written to accept both forms precisely because this venue mixes them
    within ONE object, and a fixture that carries only the unquoted form stops exercising the
    branch that production actually takes.
    """
    order = load_fixture(name)

    assert isinstance(order["filled_asset_quantity"], str)
    assert isinstance(order["limit_order_config"]["asset_quantity"], str)
    assert isinstance(order["limit_order_config"]["limit_price"], str)


@pytest.mark.parametrize("name", _OBSERVED_ORDER_FIXTURES)
def test_the_venue_does_not_echo_time_in_force_back_on_an_order(name: str) -> None:
    """`time_in_force` is accepted on the way IN and absent on the way OUT.

    `to_order_body` sends `"time_in_force": "gtc"` and the venue accepted it -- the observed order
    was created. It simply does not appear in `limit_order_config` on any response. The
    doc-derived fixtures invented it, and an invented field is the dangerous direction: it is how
    a reader concludes the venue confirms a time-in-force it never states.
    """
    order = load_fixture(name)

    assert "time_in_force" not in order["limit_order_config"]
    assert TIME_IN_FORCE == "gtc", "still sent on the way in; only the echo is absent"


@pytest.mark.parametrize("name", _OBSERVED_ORDER_FIXTURES)
def test_fee_charged_is_spelled_that_way_and_arrives_unquoted(name: str) -> None:
    """The single sharpest risk #412 names, closed by observation.

    If the venue spelled this field anything else, every row would parse to `None`,
    `_fees_paid` would skip every row, and `get_fee_summary().fees_usd` would be a confident
    `Decimal("0")` -- an always-passing fee rail, indistinguishable from a correct zero and
    therefore worse than no rail at all. It is spelled `fee_charged`, it is present on every order
    object observed, and it is an unquoted JSON number, so `load_fixture` yields a `Decimal`.
    """
    order = load_fixture(name)

    assert "fee_charged" in order
    assert isinstance(order["fee_charged"], Decimal)
    assert isinstance(order["estimated_fee_remaining"], Decimal)


def test_the_observed_states_both_translate_and_neither_is_the_ports_spelling() -> None:
    """`open` and `canceled` are the two states the live run actually produced.

    Every other entry in `STATE_TO_PORT_STATUS` is still read from Robinhood's docs -- the probe
    cannot produce a `filled` or a `failed` without placing an order that can fill or an order it
    expects to be refused. Pinning the two that ARE observed keeps the American single-`l`
    `canceled` from drifting toward the port's `CANCELLED`, which is the one rename that would
    silently turn every confirmed cancel into a `False`.
    """
    assert load_fixture("rh_order_open.json")["state"] == "open"
    assert load_fixture("rh_order_canceled.json")["state"] == "canceled"
    assert STATE_TO_PORT_STATUS["open"] == "OPEN"
    assert STATE_TO_PORT_STATUS["canceled"] == "CANCELLED"


def test_the_observed_order_fixtures_carry_no_real_account_number() -> None:
    """The observations are real responses from a real funded account, so the one field that
    identifies it is replaced by the repository's existing placeholder before it is committed.
    The order UUIDs are kept: they are random and identify nothing once the account does not."""
    for name in (*_OBSERVED_ORDER_FIXTURES, "rh_orders.json"):
        payload = load_fixture(name)
        rows = payload.get("results", [payload])
        for row in rows:
            assert row["account_number"] == "AB1234567890"


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


def _fee_summary_adapter(orders: dict[str, Any] | None = None) -> tuple[Any, RobinhoodAdapter]:
    """A transport carrying the real accounts fixture plus an order history, and its adapter."""
    transport = FakeTransport(
        accounts=load_fixture("rh_accounts.json"),
        orders=load_fixture("rh_orders.json") if orders is None else orders,
    )
    return transport, RobinhoodAdapter(transport)


def test_get_fee_summary_sums_fee_charged_across_the_order_history() -> None:
    """`fees_usd` must be OBSERVED, not pinned at zero (#197).

    A constant zero cannot contradict anything, so subscription-lapse detection -- whose whole
    job is to notice a fee charged while the user claims a fee-free allowance -- did not merely
    fail to run against this venue, it silently PASSED every time. That is worse than an absent
    rail, because it reads as coverage.

    The expected total is summed off the fixture rather than written as a literal, so the day
    that fixture is re-captured from a live order this test still states the property (every
    charged fee is counted) instead of a stale number.
    """
    _, adapter = _fee_summary_adapter()

    expected = sum(
        (Decimal(str(row["fee_charged"])) for row in load_fixture("rh_orders.json")["results"]),
        Decimal("0"),
    )
    assert expected > 0, "the fixture must carry a real charged fee or this proves nothing"
    assert adapter.get_fee_summary().fees_usd == expected


def test_get_fee_summary_counts_a_fee_charged_on_a_canceled_order() -> None:
    """Filtering the sweep to `state == "filled"` would UNDER-report, and an under-report is a
    lapse-detection false negative -- exactly the failure #197 is about.

    An order that partially fills and is then cancelled ends in state `canceled` while having
    been charged a real fee on the part that executed. `fee_charged` is documented as "the total
    fee amount that was charged for this order based on EXECUTED FILLS", so the field is already
    its own state filter: it reads zero on an order that never traded. Adding a state filter on
    top of it can only remove real charges.
    """
    _, adapter = _fee_summary_adapter()

    rows = load_fixture("rh_orders.json")["results"]
    canceled = [r for r in rows if r["state"] == "canceled" and Decimal(str(r["fee_charged"])) > 0]
    assert canceled, "the fixture must carry a charged-but-cancelled order or this proves nothing"

    filled_only = sum(
        (Decimal(str(r["fee_charged"])) for r in rows if r["state"] == "filled"), Decimal("0")
    )
    fees = adapter.get_fee_summary().fees_usd
    assert fees > filled_only
    assert fees == filled_only + sum(
        (Decimal(str(r["fee_charged"])) for r in canceled), Decimal("0")
    )


def test_get_fee_summary_never_counts_estimated_fee_remaining() -> None:
    """`estimated_fee_remaining` is a fee that has NOT been charged, and counting it would invent
    a contradiction of a fee-free claim out of an order that has not traded yet.

    The v2 docs describe it as "the estimated fee amount that will be charged on the remaining
    unfilled quantity", explicitly conditional and explicitly an estimate. `fees_usd` is read as
    an observation, so an estimate must never reach it.
    """
    _, adapter = _fee_summary_adapter()

    rows = load_fixture("rh_orders.json")["results"]
    estimated = sum((Decimal(str(r["estimated_fee_remaining"])) for r in rows), Decimal("0"))
    assert estimated > 0, "the fixture must carry an un-charged estimate or this proves nothing"

    charged = sum((Decimal(str(r["fee_charged"])) for r in rows), Decimal("0"))
    assert adapter.get_fee_summary().fees_usd == charged


def test_get_fee_summary_sweeps_the_same_thirty_day_window_volume_usd_reports() -> None:
    """`fees_usd` and `volume_usd` must describe the SAME window or they cannot be compared.

    `volume_usd` is the venue's own `thirty_day_volume`, so the fee sweep asks the venue for
    exactly 30 days, cut from the same instant the summary reports as `fetched_at`. Equality
    (rather than "roughly 30 days ago") is the assertion because the two must come from ONE
    reading of the clock: taking `time.time()` twice would let the window and the timestamp it is
    reported against drift apart for no reason.
    """
    transport, adapter = _fee_summary_adapter()

    summary = adapter.get_fee_summary()

    sent = transport.calls["get_orders"]["updated_at_start"]
    start = datetime.strptime(sent, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert int(start.timestamp()) == summary.fetched_at - 30 * 24 * 60 * 60
    assert summary.volume_window == "trailing_30d"


def test_get_fee_summary_filters_on_updated_at_not_created_at() -> None:
    """The window is cut on `updated_at`, and the choice is load-bearing rather than arbitrary.

    A fee is charged when an execution happens, and an execution always bumps `updated_at`. So
    the `updated_at_start` result set is a SUPERSET of the orders carrying an in-window fee --
    it can never omit one. `created_at_start` has no such property: a GTC stop resting for forty
    days and filling today was created outside the window and charged its fee inside it, so
    filtering on creation drops a real charge. Under-reporting is the false negative #197 exists
    to close, so the filter that cannot under-report is the correct one.
    """
    transport, adapter = _fee_summary_adapter()

    adapter.get_fee_summary()

    assert set(transport.calls["get_orders"]) == {"updated_at_start"}


@pytest.mark.parametrize("quoted", [True, False])
def test_get_fee_summary_reads_fee_charged_whether_the_venue_quotes_it_or_not(
    quoted: bool,
) -> None:
    """`fee_charged`'s JSON quoting is UNVERIFIED, so both shapes have to land on the same number.

    This venue is not internally consistent about quoting (#217 F6) -- `accounts` sends
    `buying_power` quoted beside an unquoted `fee_tier_status.fee_ratio` in the SAME object -- and
    no order object has ever been observed live, because observing one requires placing a real
    order and there is no sandbox. The v2 schema types `fee_charged` as an unquoted `number`, but
    it types the neighbouring `executions[].effective_price` as a quoted decimal STRING, so the
    documentation does not settle it either. Reading both is required, not defensive breadth.
    """
    raw = "1.6355"
    orders = {"results": [{"state": "filled", "fee_charged": raw if quoted else Decimal(raw)}]}
    _, adapter = _fee_summary_adapter(orders)

    assert adapter.get_fee_summary().fees_usd == Decimal(raw)


def test_get_fee_summary_does_not_let_a_negative_fee_cancel_out_a_real_charge() -> None:
    """A negative `fee_charged` is not a fee charged, and must not net a real one back to zero.

    Nothing in the v2 docs says this field can go negative, so a negative is either a rebate or a
    venue bug -- and under both readings, letting it subtract would hide a charge that really did
    happen from the one check that exists to notice it.
    """
    orders = {
        "results": [
            {"state": "filled", "fee_charged": Decimal("1.6355")},
            {"state": "filled", "fee_charged": Decimal("-5.00")},
        ]
    }
    _, adapter = _fee_summary_adapter(orders)

    assert adapter.get_fee_summary().fees_usd == Decimal("1.6355")


def test_get_fee_summary_reports_zero_only_when_the_venue_reported_no_charges() -> None:
    """Zero is still a legitimate answer -- but now it is an OBSERVATION rather than a constant.

    An account that has traded nothing in thirty days genuinely paid nothing, and that zero does
    contradict nothing for the right reason. The difference from the old behaviour is the whole
    point of #197: this zero moves when the venue's answer moves.
    """
    _, adapter = _fee_summary_adapter({"results": []})

    assert adapter.get_fee_summary().fees_usd == Decimal("0")


def test_get_fee_summary_propagates_a_truncated_sweep_instead_of_under_reporting() -> None:
    """An incomplete sweep must raise, because `FeeSummary` has nowhere to say "partial".

    `RobinhoodTransport._paginate` raises once a list refuses to terminate within `_MAX_PAGES`,
    and `get_fee_summary` deliberately does NOT catch it. `fees_usd` is a bare `Decimal` with no
    companion field for confidence, so a truncated sum is indistinguishable from a complete one
    and would be read as an observation -- a silent under-report, which is the same
    always-passing false negative #197 is about, merely arrived at by a different route. An
    exception is visible; a confidently wrong number is not.

    This is the opposite of the rule `_account`/`cancel_order` follow, and the asymmetry is the
    point: those run on the EXIT path, where a raise can trap a position. `get_fee_summary` is a
    reconciliation read on no position's critical path, so failing loudly costs nothing here.
    """

    class _TruncatingTransport(FakeTransport):
        def get_orders(self, updated_at_start: str | None = None) -> Any:
            self._record("get_orders", updated_at_start=updated_at_start)
            raise RuntimeError(
                "robinhood pagination did not terminate within 20 pages following "
                "'/api/v2/crypto/trading/orders/'; refusing to loop further"
            )

    adapter = RobinhoodAdapter(_TruncatingTransport(accounts=load_fixture("rh_accounts.json")))

    with pytest.raises(RuntimeError, match="did not terminate"):
        adapter.get_fee_summary()


def test_a_transportless_adapter_refuses_network_calls_clearly() -> None:
    """`capabilities()` must work offline -- the engine needs it to decide whether to even wire
    this venue up before any credentials exist. Anything that actually needs the network must say
    why it cannot, rather than fail with an opaque `AttributeError` on `self._transport`."""
    adapter = RobinhoodAdapter()

    assert adapter.capabilities().venue == "robinhood"
    with pytest.raises(RuntimeError, match="without a transport"):
        adapter.get_balances()


def test_robinhood_crypto_is_not_session_bound_and_answers_open_offline() -> None:
    """This adapter is scoped to Robinhood's CRYPTO api (24/7), so the venue declares
    `session_bound=False` and the clock answers OPEN as a constant. Constructed with NO
    transport -- the proof that a 24/7 clock answer touches no network: a transport call
    here would raise the RuntimeError the test above pins (FR-9: crypto venues are always
    open)."""
    assert RobinhoodAdapter().capabilities().session_bound is False
    assert RobinhoodAdapter().market_clock() is SessionState.OPEN


def test_market_schedule_is_the_port_default_open_with_no_times() -> None:
    """Issue #388 C2: the 24/7 venues ship the port's DEFAULT schedule read -- the clock's
    OPEN answer with NO next_open/next_close claimed. Constructed with NO transport, for the
    same reason the clock test above is: a schedule read that touched the network would
    raise here, so this passing IS the no-call guarantee. Crypto has no calendar to carry."""
    from keel_broker_api.port import default_market_schedule
    from keel_broker_api.results import MarketSchedule

    adapter = RobinhoodAdapter()
    assert adapter.market_schedule() == MarketSchedule(state=SessionState.OPEN)
    assert adapter.market_schedule() == default_market_schedule(adapter)


def test_entry_point_discovery_finds_the_robinhood_adapter() -> None:
    """Installing this package must be sufficient to make it discoverable -- `keel add
    keel-broker-robinhood` and nothing else. A broken entry point here would silently strand the
    adapter unreachable by `load_broker`, which is the only path the (future) broker-port
    migration uses to find it."""
    from keel_broker_api.registry import load_broker

    assert load_broker("robinhood").__name__ == "RobinhoodAdapter"


@pytest.mark.parametrize("state", ["failed", "canceled"])
def test_place_order_reports_failure_when_the_venue_rejected_the_order(state: str) -> None:
    """An HTTP 200 carrying `"state": "failed"` is a REJECTION, not a placement.

    Robinhood answers a rejected order on the happy HTTP path -- 200, with a real order object
    whose `state` says it never became live. Checking only that an `id` came back therefore
    reports `success=True` for an order the venue refused. The concrete failure: a protective
    `StopLimitGTC` answered `{"id": ..., "state": "failed"}` makes the engine record a stop that
    does not exist at the venue, so the position it believes is protected is running naked, and
    nothing will contradict that belief until the stop fails to fire.

    `canceled` is treated the same way. A placement that comes back already cancelled is equally
    not a resting order, and recording it as one has the identical consequence.

    `broker_order_id` is `None` on this path, matching `CoinbaseAdapter.place_order`: a caller
    that reads a non-`None` id as "there is a live order to manage" must not be handed one for an
    order that is not live. The id is named in `reason` instead, so it survives for debugging.
    """
    transport = FakeTransport(placed=_placed_with_state(state))
    adapter = RobinhoodAdapter(transport)
    spec = StopLimitGTC(
        product_id="BTC-USD",
        side=Side.SELL,
        base_size=Decimal("0.1"),
        stop_price=Decimal("60000"),
        limit_price=Decimal("59900"),
    )

    result = adapter.place_order(spec)

    assert result.success is False
    assert result.broker_order_id is None
    assert result.reason is not None
    assert state in result.reason


@pytest.mark.parametrize("state", ["open", "filled", "pending", "partially_filled"])
def test_place_order_reports_success_for_a_state_that_is_or_may_become_live(state: str) -> None:
    """The mirror of the rejection test: a live or in-flight state must still be a success.

    `partially_filled` is in this list on purpose -- it appears in Robinhood's v1 state enum but
    not v2's, and a partially filled order is unambiguously a real order that was placed.
    """
    transport = FakeTransport(placed=_placed_with_state(state))
    adapter = RobinhoodAdapter(transport)
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=Decimal("0.1"))

    result = adapter.place_order(spec)

    assert result.success is True
    assert result.broker_order_id == load_fixture("rh_order_open.json")["id"]


def test_place_order_treats_an_unrecognised_state_as_placed_not_rejected() -> None:
    """An unknown `state` means the adapter does not know the outcome -- and here, unlike
    `get_order`, "I don't know" must resolve to SUCCESS rather than failure.

    The asymmetry is deliberate and follows the consequences. Reporting `success=False` for an
    order that is actually live invites the caller to place it again, and a duplicate live order
    is unrecoverable. Reporting `success=True` hands back the id, and reconciliation then polls
    `get_order`, which maps the same unrecognised state to `PENDING` and keeps the order under
    observation until the venue says something the adapter understands. Only the two states
    Robinhood documents as not-live (`failed`, `canceled`) are treated as rejections.
    """
    transport = FakeTransport(placed=_placed_with_state("a_future_state_this_adapter_predates"))
    adapter = RobinhoodAdapter(transport)
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=Decimal("0.1"))

    result = adapter.place_order(spec)

    assert result.success is True
    assert result.broker_order_id is not None


def test_preview_order_reports_an_error_when_the_venue_returned_no_price() -> None:
    """A preview that could not be priced must SAY so, not render as a free order.

    `_estimated_price` falls back to zero when the endpoint answers nothing, and a zero price
    makes `est_quote_size` and `est_fee` both zero. At the human confirm gate that is
    indistinguishable from an order that genuinely costs nothing -- the single most approvable
    thing a preview can look like. `Preview.errors` is the port's channel for exactly this, and
    leaving it empty is what makes the failure invisible.
    """
    transport = FakeTransport(
        accounts=load_fixture("rh_accounts.json"), estimated_price={"results": []}
    )
    adapter = RobinhoodAdapter(transport)
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=Decimal("0.1"))

    preview = adapter.preview_order(spec)

    assert preview.errors, "an unpriced preview must not come back with empty errors"
    assert any("price" in e.lower() for e in preview.errors)


def test_preview_order_reports_an_error_when_the_account_reports_no_fee_ratio() -> None:
    """`est_fee` of zero, from a missing `fee_ratio`, is a claim this account trades free.

    `_fee_ratio` already distinguishes `None` from `Decimal("0")` precisely so this claim is never
    made by accident, and `detail["fee_ratio"]` says `"unknown"`. But `detail` is free-form text a
    renderer may not show, while `errors` is the field the port defines for a soft failure -- so
    the unpriced fee has to surface there too.
    """
    accounts = load_fixture("rh_accounts.json")
    del accounts["results"][0]["fee_tier_status"]
    transport = FakeTransport(accounts=accounts)
    adapter = RobinhoodAdapter(transport)
    spec = LimitGTC(
        product_id="BTC-USD", side=Side.SELL, base_size=Decimal("0.1"), limit_price=Decimal("65000")
    )

    preview = adapter.preview_order(spec)

    assert preview.errors
    assert any("fee" in e.lower() for e in preview.errors)
    assert preview.detail["fee_ratio"] == "unknown"


def test_preview_order_on_a_fully_priced_order_reports_no_errors() -> None:
    """The control case: `errors` must stay empty when everything priced, or it means nothing."""
    transport = FakeTransport(
        trading_pairs=_pairs(),
        accounts=load_fixture("rh_accounts.json"),
        estimated_price=load_fixture("rh_estimated_price.json"),
    )
    adapter = RobinhoodAdapter(transport)
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.BUY, base_size=_QUOTED_SIZE)

    assert adapter.preview_order(spec).errors == ()


@pytest.mark.parametrize("kind", ["limit", "stop_limit"])
def test_preview_order_refuses_a_non_usd_symbol_on_every_path(kind: str) -> None:
    """`preview_order` must not approve a symbol `place_order` will refuse.

    The resting-order paths price off `spec.limit_price` and never call `to_symbol`, so
    `ETH-USDC` previews cleanly and then raises `UnsupportedOrder` at placement. That ordering is
    the problem: the human has already approved at the confirm gate by then, and the port
    explicitly forbids catching `UnsupportedOrder` and retrying with a different spec -- so the
    approved order simply cannot be placed. Validating the symbol on every preview path moves the
    refusal to before the human is asked.
    """
    spec: LimitGTC | StopLimitGTC
    if kind == "limit":
        spec = LimitGTC(
            product_id="ETH-USDC",
            side=Side.SELL,
            base_size=Decimal("1"),
            limit_price=Decimal("3000"),
        )
    else:
        spec = StopLimitGTC(
            product_id="ETH-USDC",
            side=Side.SELL,
            base_size=Decimal("1"),
            stop_price=Decimal("2900"),
            limit_price=Decimal("2890"),
        )
    adapter = RobinhoodAdapter(FakeTransport(accounts=load_fixture("rh_accounts.json")))

    with pytest.raises(UnsupportedOrder, match="USDC"):
        adapter.preview_order(spec)


def test_cancel_order_returns_false_instead_of_raising_when_the_venue_errors() -> None:
    """A 5xx during a cancel must fail safe to `False`, never propagate out of this method.

    This adapter already writes the rule down at `_account`: "a raise on the way out of a position
    can trap it". `cancel_order` is the method most exposed to it -- `executor._cancel_at_exchange`
    calls it while unwinding, and an exception there can abort the unwind partway through, leaving
    the position AND the resting orders it was trying to clear both live.

    `False` is the honest answer regardless of what went wrong, because the port's contract is
    already "`True` ONLY when the venue CONFIRMS" -- and an exception is definitionally not a
    confirmation. Nothing is claimed here that was not observed; the caller keeps believing the
    order may still be resting, which is the belief that keeps it watching.
    """
    fixture = load_fixture("rh_order_open.json")
    transport = _RaisingCancelTransport(order=fixture)
    transport._issued_order_ids.add(fixture["id"])
    adapter = RobinhoodAdapter(transport)

    assert adapter.cancel_order(fixture["id"]) is False


def test_cancel_order_returns_false_when_the_mandatory_re_poll_raises() -> None:
    """Same rule, one layer deeper: the re-poll is on the exit path too and cannot be allowed to
    escape as an exception either."""
    fixture = load_fixture("rh_order_open.json")
    transport = _RaisingRepollTransport(order=fixture)
    transport._issued_order_ids.add(fixture["id"])
    adapter = RobinhoodAdapter(transport)

    assert adapter.cancel_order(fixture["id"]) is False


def test_place_order_returns_a_domain_type() -> None:
    """No Robinhood-native order object may cross the port -- a caller must only ever see
    `PlaceResult`."""
    transport = FakeTransport(placed=load_fixture("rh_order_open.json"))
    adapter = RobinhoodAdapter(transport)
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=Decimal("0.1"))

    result = adapter.place_order(spec)

    assert isinstance(result, PlaceResult)


# ---------------------------------------------------------------------------------------------
# The fixtures themselves, held to the shapes the first live run actually observed (#217).
#
# These assert on `tests/fixtures/rh_*.json` rather than on adapter behaviour, which is unusual
# and deliberate. Robinhood ships no sandbox, so a fixture is the ONLY statement this repository
# makes about what the venue sends -- and #217 found three of them stating things it does not:
# an `estimated_price.price` that made every market preview unpriced and a `best_bid_ask` row that
# was invented outright. A wrong fixture is not a test-data nit here; it is a false claim about a
# live-money venue that the rest of the suite then confirms.
#
# It cuts the other way too, and #230 is the proof: #217 F3 read `results[0]` alone, concluded the
# venue publishes no minimum order size, and #218 deleted a REAL field from `rh_trading_pairs.json`
# on the strength of it. A fixture can be wrong by omission, and a probe that samples one row will
# not tell you.
# ---------------------------------------------------------------------------------------------

#: Money and size fields the venue sends as UNQUOTED JSON numbers, keyed by fixture. Paths are
#: `results[]`-relative.
_NUMERIC_FIELDS: dict[str, tuple[str, ...]] = {
    "rh_holdings.json": ("total_quantity", "quantity_available_for_trading"),
    "rh_estimated_price.json": ("quantity", "fee_ratio", "est_fee", "ask", "est_total_cost"),
    "rh_estimated_price_bid.json": ("quantity", "fee_ratio", "est_fee", "bid"),
}

#: Money and size fields the venue sends as QUOTED STRINGS. Same run, same credential, same
#: minute -- see `test_this_venue_is_not_internally_consistent_about_quoting`.
_QUOTED_FIELDS: dict[str, tuple[str, ...]] = {
    "rh_accounts.json": ("buying_power",),
    "rh_trading_pairs.json": (
        "asset_increment",
        "quote_increment",
        "max_order_size",
        "min_order_amount",
    ),
    "rh_best_bid_ask.json": ("bid", "ask"),
    "rh_best_bid_ask_uncrossed.json": ("bid", "ask"),
}


@pytest.mark.parametrize(("fixture_name", "fields"), sorted(_NUMERIC_FIELDS.items()))
def test_the_venues_unquoted_money_fields_decode_as_decimal(
    fixture_name: str, fields: tuple[str, ...]
) -> None:
    """These arrive from the venue as JSON numbers, so the fixtures must send them as numbers.

    This is what makes #194's `parse_float=Decimal` load-bearing rather than defensive: with the
    values quoted, `Decimal(str(v))` operated on a `str` that was already exact and the parser
    setting was never exercised. Unquoted, the same value is a JSON number, and any decoder that
    is not told otherwise routes it through a binary `float` before a `Decimal` ever sees it.

    The assertion is on the DECODED type, not on the file's bytes, because that is the property
    the adapter depends on -- and it fails loudly if `load_fixture` ever loses its `parse_float`.
    """
    rows = load_fixture(fixture_name)["results"]
    assert rows
    for field_name in fields:
        value = rows[0][field_name]
        assert isinstance(value, Decimal), f"{fixture_name}:{field_name} decoded as {type(value)}"


@pytest.mark.parametrize(("fixture_name", "fields"), sorted(_QUOTED_FIELDS.items()))
def test_the_venues_quoted_money_fields_decode_as_str(
    fixture_name: str, fields: tuple[str, ...]
) -> None:
    """And these arrive QUOTED, so the fixtures must not "improve" them into numbers.

    A fixture that is uniformly one or the other is easier to look at and is a lie about this
    venue either way. The point of a fixture here is to be the shape the adapter will really meet.
    """
    rows = load_fixture(fixture_name)["results"]
    assert rows
    for field_name in fields:
        value = rows[0][field_name]
        assert isinstance(value, str), f"{fixture_name}:{field_name} decoded as {type(value)}"


def test_this_venue_is_not_internally_consistent_about_quoting() -> None:
    """⚠️ The finding worth carrying forward from #217 F6, stated as an executable claim.

    `buying_power` is a quoted string and `fee_tier_status.fee_ratio` is an unquoted number **in
    the same JSON object**, from the same request. `trading_pairs` and `best_bid_ask` quote every
    money value; `estimated_price` and `holdings` quote none of them.

    So there is no venue-wide rule to code against, and no field can be assumed to be one or the
    other -- not even two fields sitting side by side. The only safe reads are the two this
    package already performs: `parse_float=Decimal` in the transport, so an unquoted number never
    passes through a binary `float`, and `Decimal(str(value))` in the adapter, which is exact for
    a `str` and a round-trip no-op for a `Decimal`. Neither `Decimal(x)` on a raw value nor an
    `isinstance` branch is safe anywhere in this package.
    """
    account = load_fixture("rh_accounts.json")["results"][0]
    assert isinstance(account["buying_power"], str)
    assert isinstance(account["fee_tier_status"]["fee_ratio"], Decimal)


def test_the_account_fee_tier_status_is_numeric_throughout() -> None:
    """`fee_tier_status` is the shape #216 called the most load-bearing guess in the package; the
    live run corroborated every key name, and every one of its values is unquoted."""
    tier = load_fixture("rh_accounts.json")["results"][0]["fee_tier_status"]
    assert set(tier) == {
        "fee_ratio",
        "thirty_day_volume",
        "next_fee_tier_ratio",
        "next_fee_tier_threshold",
    }
    assert all(isinstance(value, Decimal) for value in tier.values())


def test_trading_pairs_publishes_a_minimum_order_amount_for_the_assets_keel_trades() -> None:
    """⚠️ #230 D2, reversing #217 F3 -- which was wrong, and this file asserted it for two PRs.

    `min_order_amount` **exists**, and BTC-USD carries it (`0.1`). #217 F3 concluded otherwise, and
    #218 deleted the field from this fixture, because the probe that "confirmed it live across
    four cursor pages" only ever inspected `results[0]` -- which is BILL-USD, one of the 26 pairs
    of 89 that genuinely lack the field. The other 63, BTC-USD and ETH-USD among them, carry it.

    So the venue publishes a minimum for every asset keel trades, the pre-flight sizing check
    proposed in #198 does have a lower-bound source, and a fixture missing it is a fixture missing
    a field the venue sends on the only row it claims to represent. `min_order_size` really is
    absent -- that half of F3 held up.

    The fixture's single row is BTC-USD deliberately: `scripts/robinhood_smoke.py` compares a
    merged shape of all 89 live rows against this one object, so the object has to carry the union
    of what a row can hold or the probe reports a difference it should not.
    """
    pair = load_fixture("rh_trading_pairs.json")["results"][0]
    assert pair["symbol"] == "BTC-USD"
    assert pair["min_order_amount"] == "0.1"
    assert "min_order_size" not in pair
    assert set(pair) == {
        "symbol",
        "asset_code",
        "quote_code",
        "asset_increment",
        "quote_increment",
        "max_order_size",
        "min_order_amount",
        "status",
        "is_api_tradable",
    }


def test_best_bid_ask_carries_a_bid_and_an_ask_and_nothing_invented() -> None:
    """#217 F4: the previous fixture was invented almost in full.

    It carried `price`, `buy_spread`, `sell_spread`, `ask_inclusive_of_buy_spread` and
    `bid_inclusive_of_sell_spread` -- five keys, none of which the venue sends. Nothing read them,
    which is why it survived; the danger was entirely in what would be written against them next.

    `timestamp` is the mirror-image miss (#217 F8): a field the venue DOES send that the first
    correction left out. Both directions matter, which is why `compare_shapes` reports both.
    """
    row = load_fixture("rh_best_bid_ask.json")["results"][0]
    assert set(row) == {"symbol", "timestamp", "bid", "ask"}


def test_best_bid_ask_legs_cross_on_a_tight_pair_and_that_is_the_venue_not_the_fixture() -> None:
    """#413. The previous fixture asserted `bid < ask` and BTC-USD does not honour it.

    This is the inverse of #217 F4's failure and the same category of harm. That fixture invented
    keys the venue never sends; this one invented an ORDERING the venue does not produce, which is
    harder to spot because a tidy 15 bps spread is exactly what a reader expects to see.

    Measured 2026-08-19, three samples ~2s apart, five pairs: BTC-USD and DOGE-USD crossed every
    time, ETH-USD twice of three, XLM-USD and ADA-USD never. The crossings are all under 1.4 bps
    and the pairs that never cross are the ones whose real spread (2-5 bps) is wider than that.
    So the legs are sampled independently and stamped with one `timestamp` -- the row asserts a
    simultaneity it does not have, and where the true spread is thinner than the sampling jitter
    the two legs land out of order.

    Both fixtures exist because ONE of them would be a fresh false claim: a lone crossed row says
    "always crossed" as confidently as the old one said "never". That is #230's lesson exactly --
    a fixture can be wrong by being unrepresentative, not only by being invented -- and it is why
    the assertion below is about the endpoint admitting both orderings rather than about either
    row's own ordering being the rule.
    """
    crossed = load_fixture("rh_best_bid_ask.json")["results"][0]
    uncrossed = load_fixture("rh_best_bid_ask_uncrossed.json")["results"][0]

    # `Decimal`, never a string comparison. These arrive quoted from this endpoint, and
    # `"9" > "10"` lexically -- an ordering bug that surfaces only once a price crosses a digit
    # boundary.
    assert Decimal(crossed["bid"]) > Decimal(crossed["ask"]), (
        "BTC-USD's legs cross; a fixture that hides it lets a spread check be written against an "
        "ordering the venue does not guarantee"
    )
    assert Decimal(uncrossed["bid"]) < Decimal(uncrossed["ask"]), (
        "XLM-USD's do not; the endpoint is not uniformly inverted either"
    )
    # The crossing is jitter-sized, not a semantic inversion -- a swapped pair of labels would be
    # a whole spread wide. Pinned so a future row that crosses by percent gets read as the
    # different, worse finding it would be.
    gap = abs(Decimal(crossed["bid"]) - Decimal(crossed["ask"])) / Decimal(crossed["bid"])
    assert gap < Decimal("0.0005"), "a crossing this wide would not be a sampling artefact"


# --- pre-flight sizing (#410) ------------------------------------------------------------------
# Reported through `Preview.errors`, never enforced in `place_order`. Every order this adapter can
# place is an exit or a protective leg, and the venue's behaviour on an out-of-bounds order has
# never been observed (#412) -- so refusing locally could invent a failure the venue would not
# have produced. These pin the reporting, and the denominations, which are the part that bites.


def _priced_transport(**kwargs: Any) -> Any:
    return FakeTransport(
        accounts=load_fixture("rh_accounts.json"),
        estimated_price=load_fixture("rh_estimated_price.json"),
        **kwargs,
    )


def test_min_order_amount_is_measured_against_the_QUOTE_size_not_the_base_size() -> None:
    """The finding this whole check turns on, as an executable claim.

    `min_order_amount` is `0.1` on all 63 pairs that carry it, from BTC at ~$68,000 to DOGE at
    ~$0.07 -- a venue-wide $0.10 floor in QUOTE currency, not a base-denominated minimum. Read as
    base it would mean 0.1 BTC, so a check written `base_size >= min_order_amount` would reject
    every BTC order under ~$6,800, on the exit path included. This order is 0.001 BTC -- far below
    `0.1` as a base size, far above it as a dollar amount -- so it separates the two readings.
    """
    adapter = RobinhoodAdapter(_priced_transport(trading_pairs=_pairs()))
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.BUY, base_size=_QUOTED_SIZE)

    preview = adapter.preview_order(spec)

    assert preview.est_base_size < Decimal("0.1")  # below the bound read as BASE
    assert preview.est_quote_size > Decimal("0.1")  # above it read as QUOTE, which is correct
    assert preview.errors == ()
    assert preview.detail["min_order_amount_quote"] == "0.1"


def test_an_order_under_the_quote_minimum_is_reported() -> None:
    """A genuinely sub-minimum order: one satoshi, worth a small fraction of a cent."""
    adapter = RobinhoodAdapter(_priced_transport(trading_pairs=_pairs()))
    spec = LimitGTC(
        product_id="BTC-USD",
        side=Side.SELL,
        base_size=Decimal("0.00000001"),
        limit_price=Decimal("65000"),
    )

    errors = adapter.preview_order(spec).errors

    assert any("below the venue's min_order_amount" in e for e in errors)
    assert any("QUOTE currency (USD) -- not in base units" in e for e in errors)


def test_an_order_over_the_base_maximum_is_reported() -> None:
    """`max_order_size` is base-denominated -- 20 BTC here -- so this compares against base."""
    adapter = RobinhoodAdapter(_priced_transport(trading_pairs=_pairs()))
    spec = LimitGTC(
        product_id="BTC-USD",
        side=Side.SELL,
        base_size=Decimal("21"),
        limit_price=Decimal("65000"),
    )

    errors = adapter.preview_order(spec).errors

    assert any("exceeds the venue's max_order_size" in e for e in errors)
    assert any("both in base units" in e for e in errors)


def test_an_off_increment_size_is_reported_without_claiming_the_venue_will_reject_it() -> None:
    """Half a satoshi. The note must NOT assert an outcome: whether Robinhood rounds or refuses
    has never been observed, and stating either would be a guess on a live-money path."""
    adapter = RobinhoodAdapter(_priced_transport(trading_pairs=_pairs()))
    spec = LimitGTC(
        product_id="BTC-USD",
        side=Side.SELL,
        base_size=Decimal("0.000000005"),
        limit_price=Decimal("65000"),
    )

    errors = adapter.preview_order(spec).errors

    note = next(e for e in errors if "asset_increment" in e)
    assert "never been observed" in note


def test_an_unpriced_market_order_is_not_accused_of_being_below_the_minimum() -> None:
    """`quote_size` is zero when the venue priced nothing, and zero is an ABSENCE here, not a
    number. Comparing it against the minimum would manufacture a second, false error underneath
    the real one -- the module's cardinal sin, stated in `_decimal_or_none`."""
    adapter = RobinhoodAdapter(
        FakeTransport(accounts=load_fixture("rh_accounts.json"), trading_pairs=_pairs())
    )
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.BUY, base_size=_QUOTED_SIZE)

    preview = adapter.preview_order(spec)

    assert preview.est_quote_size == 0
    assert any("no usable estimated price" in e for e in preview.errors)
    assert not any("min_order_amount" in e for e in preview.errors)


def test_a_pair_without_a_minimum_says_so_rather_than_checking_against_nothing() -> None:
    """26 of the venue's 89 pairs carry no `min_order_amount` (#230). Absent must read as
    "unchecked", never as a bound of zero that everything trivially clears."""
    row = dict(load_fixture("rh_trading_pairs.json")["results"][0])
    del row["min_order_amount"]
    adapter = RobinhoodAdapter(_priced_transport(trading_pairs={"results": [row]}))
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.BUY, base_size=_QUOTED_SIZE)

    preview = adapter.preview_order(spec)

    assert any("no min_order_amount" in e for e in preview.errors)
    assert preview.detail["min_order_amount_quote"] == "unknown"


def test_bounds_the_venue_never_answered_are_reported_as_unchecked_not_as_passing() -> None:
    """A `trading_pairs` call that fails or returns nothing must not read as "size is fine"."""
    adapter = RobinhoodAdapter(_priced_transport())
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.BUY, base_size=_QUOTED_SIZE)

    preview = adapter.preview_order(spec)

    assert any("did not state this pair's sizing bounds" in e for e in preview.errors)
    assert preview.detail["max_order_size_base"] == "unknown"


def test_the_sizing_read_asks_the_venue_to_filter_rather_than_picking_results_zero() -> None:
    """#230's lesson, pinned. The unfiltered endpoint returns 89 rows and `results[0]` is
    BILL-USD, one of the 26 without a minimum -- which is exactly how a real field came to be
    declared non-existent and deleted from a fixture."""
    transport = _priced_transport(trading_pairs=_pairs())
    adapter = RobinhoodAdapter(transport)

    adapter.preview_order(
        MarketIOCByBase(product_id="BTC-USD", side=Side.BUY, base_size=_QUOTED_SIZE)
    )

    assert transport.calls["get_trading_pairs"] == {"symbol": "BTC-USD"}


def test_place_order_does_not_consult_the_sizing_bounds() -> None:
    """The boundary, pinned so it cannot erode by accident. Enforcement is a separate decision
    that needs #412's observation of a real rejection first; until then `place_order` must place
    exactly what it was given, and must not spend a request deciding not to."""
    transport = FakeTransport(placed=load_fixture("rh_order_open.json"), trading_pairs=_pairs())
    adapter = RobinhoodAdapter(transport)

    result = adapter.place_order(
        LimitGTC(
            product_id="BTC-USD",
            side=Side.SELL,
            base_size=Decimal("0.00000001"),  # below the $0.10 minimum by any reading
            limit_price=Decimal("65000"),
        )
    )

    assert result.success
    assert "get_trading_pairs" not in transport.calls


def test_an_idempotency_key_pins_the_client_order_id_across_attempts() -> None:
    """#409. Robinhood requires a `client_order_id` on every order and will deduplicate on it,
    so this is the parameter that makes a placement retry safe: two attempts under one key reach
    the venue as one id, and the retry is collapsed instead of becoming a second live order."""
    transport = FakeTransport(placed=load_fixture("rh_order_open.json"))
    adapter = RobinhoodAdapter(transport)
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=Decimal("0.1"))

    adapter.place_order(spec, idempotency_key="cycle-7:pos-3:exit")
    first = transport.calls["create_order"]["body"]["client_order_id"]
    adapter.place_order(spec, idempotency_key="cycle-7:pos-3:exit")

    assert transport.calls["create_order"]["body"]["client_order_id"] == first
    # A UUID, because that is what this venue's field is -- see `resolve_client_order_id` for why
    # the caller's key is hashed rather than passed through.
    assert uuid.UUID(first).version == 5


def test_without_a_key_robinhood_still_mints_one_id_per_attempt() -> None:
    """The default is unchanged, and deliberately so: two orders a strategy genuinely meant to
    place twice must not collapse into one."""
    transport = FakeTransport(placed=load_fixture("rh_order_open.json"))
    adapter = RobinhoodAdapter(transport)
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=Decimal("0.1"))

    adapter.place_order(spec)
    first = transport.calls["create_order"]["body"]["client_order_id"]
    adapter.place_order(spec)

    assert transport.calls["create_order"]["body"]["client_order_id"] != first
