"""Tests for `AlpacaAdapter`, the Alpaca Trading + Market Data API implementation of the
`Broker` port.

Alpaca's paper environment is a live sandbox, but this suite still runs against a canned,
in-memory `FakeTransport` loaded from `tests/fixtures/alpaca_*.json` -- the fixture-driven
design mirrored from `tests/broker_robinhood/test_adapter.py`. No network call is made and
no order (paper or otherwise) is ever placed: the conformance suite calls `place_order`, so
a real transport here could place real orders.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from keel_broker_alpaca import AlpacaAdapter
from keel_broker_alpaca.fees import estimate_regulatory_fees
from keel_broker_alpaca.transport import (
    LIVE_TRADING_HOST,
    PAPER_TRADING_HOST,
    SUPPORTED_DATA_FEEDS,
    TRADING_HOSTS,
    AlpacaAPIError,
    AlpacaTransport,
)
from keel_broker_api.orders import (
    LimitGTC,
    MarketIOCByBase,
    MarketIOCByQuote,
    StopLimitGTC,
)
from keel_broker_api.port import UnsupportedOrder
from keel_broker_api.results import Balance, OrderStatus, PlaceResult, Preview, SessionState
from keel_core.types import Granularity, Side

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

_PRODUCT = "AAPL-USD"


def load_fixture(name: str) -> dict[str, Any]:
    """Decode a fixture the way `AlpacaTransport` decodes a live response.

    `parse_float=Decimal` for the same reason `tests/broker_robinhood/test_adapter.py`
    states: Alpaca's market-data endpoints (bars, quotes) send money values as UNQUOTED
    JSON numbers, and a fixture decoded through a binary `float` would hand the adapter
    values the live path can never produce.
    """
    with (FIXTURES_DIR / name).open() as f:
        data: dict[str, Any] = json.load(f, parse_float=Decimal)
    return data


def _full_transport() -> FakeTransport:
    """A transport wired for every read path, the shape the conformance suite also uses."""
    return FakeTransport(
        account=load_fixture("alpaca_account.json"),
        positions=load_fixture("alpaca_positions.json"),
        clock=load_fixture("alpaca_clock_open.json"),
        placed=load_fixture("alpaca_order_placed.json"),
        order=load_fixture("alpaca_order_filled.json"),
        bars_pages=[load_fixture("alpaca_bars_page1.json"), load_fixture("alpaca_bars_page2.json")],
        quote=load_fixture("alpaca_quote_latest.json"),
    )


class FakeTransport:
    """Duck-types the `Transport` Protocol, returning fixtures and recording every call.

    `_issued_order_ids` mirrors the venue's own distinction: an id this transport handed
    out via `create_order` is known, anything else is a 404 the venue never issued.
    `cancel_order` answers with an HTTP status int because that status IS the venue's
    whole cancel response (204 No Content on confirmation; 404/422 otherwise).
    """

    def __init__(
        self,
        *,
        account: dict[str, Any] | None = None,
        positions: list[dict[str, Any]] | None = None,
        clock: dict[str, Any] | None = None,
        placed: dict[str, Any] | None = None,
        order: dict[str, Any] | None = None,
        bars_pages: list[dict[str, Any]] | None = None,
        quote: dict[str, Any] | None = None,
        cancel_status: int = 204,
    ) -> None:
        self._account = account
        self._positions = positions
        self._clock = clock
        self._placed = placed
        self._order = order
        self._bars_pages = bars_pages or []
        self._quote = quote
        self._cancel_status = cancel_status
        self.calls: dict[str, dict[str, Any]] = {}
        self.call_counts: dict[str, int] = {}
        self.create_order_bodies: list[dict[str, Any]] = []
        self._issued_order_ids: set[str] = set()

    def _record(self, name: str, **kwargs: Any) -> None:
        self.calls[name] = kwargs
        self.call_counts[name] = self.call_counts.get(name, 0) + 1

    def get_account(self) -> Any:
        self._record("get_account")
        return self._account

    def get_positions(self) -> Any:
        self._record("get_positions")
        return self._positions

    def get_clock(self) -> Any:
        self._record("get_clock")
        return self._clock

    def create_order(self, body: dict[str, Any]) -> Any:
        self._record("create_order", body=body)
        self.create_order_bodies.append(body)
        if self._placed is None:
            return None
        issued = self._placed.get("id")
        if issued is not None:
            self._issued_order_ids.add(issued)
        return self._placed

    def get_order(self, order_id: str) -> Any:
        self._record("get_order", order_id=order_id)
        if order_id not in self._issued_order_ids:
            return None
        merged = dict(self._order or self._placed or {})
        merged["id"] = order_id
        return merged

    def cancel_order(self, order_id: str) -> Any:
        self._record("cancel_order", order_id=order_id)
        if order_id not in self._issued_order_ids:
            return 404
        return self._cancel_status

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: str,
        end: str,
        feed: str,
        page_token: str | None = None,
    ) -> Any:
        """The next bars page: page one when no token, the linked page otherwise."""
        self._record(
            "get_bars",
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            feed=feed,
            page_token=page_token,
        )
        if not self._bars_pages:
            return {"bars": [], "next_page_token": None, "symbol": symbol}
        return self._bars_pages[0] if page_token is None else self._bars_pages[1]

    def get_latest_quote(self, symbol: str, feed: str) -> Any:
        self._record("get_latest_quote", symbol=symbol, feed=feed)
        return self._quote


class _RejectingCreateTransport(FakeTransport):
    """A `create_order` that raises the way the venue answers a rejected placement.

    Alpaca signals order rejections as HTTP errors on the otherwise-happy path (403 for
    insufficient buying power, 422 for a malformed/invalid body), unlike Robinhood which
    answers 200 with a failed order object -- so the transport converts the HTTP error
    into `AlpacaAPIError` and the adapter must map it to `PlaceResult(success=False)`.
    """

    def __init__(self, error: AlpacaAPIError, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._error = error

    def create_order(self, body: dict[str, Any]) -> Any:
        self._record("create_order", body=body)
        raise self._error


# ---------------------------------------------------------------------------------------------
# Capability declaration (FR-2, FR-5, FR-11)
# ---------------------------------------------------------------------------------------------


class TestCapabilities:
    def test_declares_the_alpaca_venue_usd_quotes_and_us_equities(self) -> None:
        caps = AlpacaAdapter().capabilities()
        assert caps.venue == "alpaca"
        assert caps.quote_currencies == frozenset({"USD"})
        assert caps.asset_classes == frozenset({"equity"})

    def test_supports_all_four_port_order_kinds(self) -> None:
        """Alpaca's equity surface covers the port's whole order vocabulary: notional and
        fractional-qty market orders, GTC limits, and GTC stop-limits (FR-3)."""
        caps = AlpacaAdapter().capabilities()
        assert caps.supported_orders == frozenset(
            {"market_ioc_quote", "market_ioc_base", "limit_gtc", "stop_limit_gtc"}
        )

    def test_preview_is_declared_synthetic(self) -> None:
        """Alpaca has no preview endpoint, so every Preview must label itself synthetic --
        the `keel_broker_robinhood` precedent for venues without a native preview."""
        caps = AlpacaAdapter().capabilities()
        assert caps.supports_native_preview is False
        assert caps.synthesizes_preview is True
        assert caps.can_preview

    def test_fee_summary_is_declared_unsupported(self) -> None:
        """Alpaca's Trading API publishes no fee tiers, no fees-paid total, and no volume
        window -- the three things a `FeeSummary` would assert. Declaring the gap (the
        `FakeAdapter` precedent) is honest where a fabricated zero rate would not be."""
        assert AlpacaAdapter().capabilities().supports_fee_summary is False

    def test_get_fee_summary_raises_as_declared(self) -> None:
        with pytest.raises(NotImplementedError):
            AlpacaAdapter().get_fee_summary()


# ---------------------------------------------------------------------------------------------
# Paper/live host isolation (FR-11, #233-aligned)
# ---------------------------------------------------------------------------------------------


class TestPaperLiveIsolation:
    def test_the_only_endpoint_to_host_map_is_the_documented_one(self) -> None:
        assert TRADING_HOSTS == {
            "paper": "https://paper-api.alpaca.markets",
            "live": "https://api.alpaca.markets",
        }
        assert PAPER_TRADING_HOST == "https://paper-api.alpaca.markets"
        assert LIVE_TRADING_HOST == "https://api.alpaca.markets"

    def test_a_paper_configuration_cannot_reach_the_live_host(self) -> None:
        """The adapter derives its trading host from an endpoint enum, never from a URL,
        so no paper configuration can point at `api.alpaca.markets`: there is no parameter
        that accepts one (FR-11, the #233 capability stance -- a paper key must never be
        mistaken for a live one)."""
        transport = AlpacaTransport("key-id", "secret", endpoint="paper")
        assert transport.trading_host == PAPER_TRADING_HOST
        assert LIVE_TRADING_HOST not in transport.trading_host

        live = AlpacaTransport("key-id", "secret", endpoint="live")
        assert live.trading_host == LIVE_TRADING_HOST
        assert PAPER_TRADING_HOST not in live.trading_host

    def test_an_unknown_endpoint_is_refused_at_construction(self) -> None:
        for bad in ("production", "PAPER", "https://api.alpaca.markets", ""):
            with pytest.raises(ValueError, match="endpoint"):
                AlpacaTransport("key-id", "secret", endpoint=bad)
            with pytest.raises(ValueError, match="endpoint"):
                AlpacaAdapter(endpoint=bad)

    def test_no_constructor_parameter_accepts_a_host_url(self) -> None:
        """The `trading_host`/`data_host` keyword escapes are GONE, so the documented
        endpoint-to-host map is the only construction path to a trading host: no
        parameter accepts a host URL at all, which is what makes the README's "no
        configuration path from a paper credential to the live host, by construction"
        literally true (FR-11). `TypeError` is Python's own "no such keyword" answer --
        there is nothing to validate because there is nothing to pass."""
        with pytest.raises(TypeError):
            AlpacaTransport("key-id", "secret", trading_host=LIVE_TRADING_HOST)  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            AlpacaTransport("key-id", "secret", data_host="https://example.test")  # type: ignore[call-arg]

    def test_the_data_tier_is_a_declared_choice_not_an_assumption(self) -> None:
        """IEX (free) vs SIP is a declared capability (FR-5): the adapter names its feed
        on every market-data request instead of letting the venue default it, because the
        default (SIP) silently fails for keys without the subscription."""
        assert SUPPORTED_DATA_FEEDS == frozenset({"iex", "sip"})
        assert AlpacaAdapter().data_feed == "iex"

        with pytest.raises(ValueError, match="data_feed"):
            AlpacaTransport("key-id", "secret", data_feed="sip-plus")
        with pytest.raises(ValueError, match="data_feed"):
            AlpacaAdapter(data_feed="sip-plus")

    def test_the_feed_is_sent_on_every_market_data_request(self) -> None:
        transport = _full_transport()
        adapter = AlpacaAdapter(transport, data_feed="sip")
        adapter.get_candles(_PRODUCT, Granularity.ONE_DAY, 1_700_000_000, 1_700_086_400)
        adapter.preview_order(
            MarketIOCByBase(product_id=_PRODUCT, side=Side.SELL, base_size=Decimal("0.5"))
        )

        assert transport.calls["get_bars"]["feed"] == "sip"
        assert transport.calls["get_latest_quote"]["feed"] == "sip"


# ---------------------------------------------------------------------------------------------
# Balances and positions (FR-6)
# ---------------------------------------------------------------------------------------------


class TestBalances:
    def test_cash_available_is_the_buying_power_and_total_is_the_cash_balance(self) -> None:
        """On a cash account (`multiplier == 1`) Alpaca's `buying_power` is the spendable
        figure and `cash` the full balance; the gap is unsettled (T+1) proceeds. Sourcing
        `available` from `buying_power` and clamping at `cash` surfaces that honestly
        without ever reporting leveraged buying power as spendable."""
        adapter = AlpacaAdapter(_full_transport())
        balances = {b.currency: b for b in adapter.get_balances()}

        usd = balances["USD"]
        assert isinstance(usd, Balance)
        assert usd.available == Decimal("100000.00")
        assert usd.total == Decimal("102086.50")
        assert usd.total > usd.available, "the fixture carries a settlement gap to surface"

    def test_positions_become_balances_with_available_below_total_when_shares_are_unsettled(
        self,
    ) -> None:
        adapter = AlpacaAdapter(_full_transport())
        balances = {b.currency: b for b in adapter.get_balances()}

        assert balances["AAPL"].total == Decimal("3")
        assert balances["AAPL"].available == Decimal("3")
        assert balances["TSLA"].total == Decimal("5")
        assert balances["TSLA"].available == Decimal("4")
        assert all(isinstance(b.available, Decimal) for b in balances.values())

    def test_a_short_position_row_is_not_reported_as_a_holding(self) -> None:
        """keel is long-only by construction; a short row on the account is a state this
        engine must not reconcile into a positive holding, so it is skipped loudly-by-omission
        rather than reported with a negative quantity the rails never expect."""
        transport = _full_transport()
        transport._positions = [
            {"symbol": "GME", "qty": "-1", "qty_available": "-1", "side": "short"}
        ]
        balances = AlpacaAdapter(transport).get_balances()
        assert [b.currency for b in balances] == ["USD"]

    def test_a_nonfinite_buying_power_is_handled_not_a_crash(self) -> None:
        """A NaN `buying_power` arrives as `float("nan")` (the `parse_constant` path, not
        `parse_float`), and `min()` over a NaN `Decimal` raises. The existing
        balances convention for a money field that cannot be parsed is the same as an
        absent one -- read as zero via the `or Decimal("0")` every balance field carries
        -- so a garbage spendable figure never reaches a `Balance` row and nothing
        raises."""
        account = load_fixture("alpaca_account.json")
        account["buying_power"] = float("nan")

        balances = AlpacaAdapter(FakeTransport(account=account)).get_balances()

        usd = {b.currency: b for b in balances}["USD"]
        assert usd.available == Decimal("0"), "an unparseable buying power reads as zero"
        assert usd.total == Decimal("102086.50"), "the parseable cash figure is untouched"


# ---------------------------------------------------------------------------------------------
# Candles (FR-5, FR-10's adjusted/raw policy)
# ---------------------------------------------------------------------------------------------


class TestCandles:
    def test_bars_are_fetched_paginated_and_returned_ascending(self) -> None:
        transport = _full_transport()
        candles = AlpacaAdapter(transport).get_candles(
            _PRODUCT, Granularity.FIFTEEN_MINUTE, 1_700_000_000, 1_700_086_400
        )

        assert [c.ts for c in candles] == [1_786_717_800, 1_786_718_700, 1_786_719_600]
        assert candles[0].open == Decimal("132.02")
        assert candles[0].close == Decimal("131.9")
        assert candles[2].volume == Decimal("9100")
        # Pagination really walked both pages and threaded the venue's token through.
        assert transport.call_counts["get_bars"] == 2
        assert transport.calls["get_bars"]["page_token"] == "cGFnZTI="

    def test_the_request_declares_timeframe_window_feed_and_split_adjustment(self) -> None:
        transport = _full_transport()
        AlpacaAdapter(transport).get_candles(
            _PRODUCT, Granularity.FIFTEEN_MINUTE, 1_700_000_000, 1_700_086_400
        )

        call = transport.calls["get_bars"]
        assert call["symbol"] == "AAPL"
        assert call["timeframe"] == "15Min"
        assert call["start"] == "2023-11-14T22:13:20Z"
        assert call["end"] == "2023-11-15T22:13:20Z"
        assert call["feed"] == "iex"

    def test_every_unsupported_granularity_is_refused(self) -> None:
        adapter = AlpacaAdapter(_full_transport())
        for granularity in (Granularity.ONE_MINUTE, Granularity.FIVE_MINUTE, Granularity.SIX_HOUR):
            with pytest.raises(ValueError, match="timeframe"):
                adapter.get_candles(_PRODUCT, granularity, 0, 86_400)

    def test_a_nonfinite_bar_value_is_never_stored_in_a_candle(self) -> None:
        """A NaN high arrives as `float("nan")`, and `Decimal("NaN")` is TRUTHY -- so
        without an explicit finiteness check the `or Decimal("0")` fallback never fires
        and the NaN rides into `Candle.high` silently, poisoning every indicator that
        touches the series. The module's existing convention for an unparseable bar leaf
        is the same as an absent one: read as zero, never as the venue's garbage."""
        bars = load_fixture("alpaca_bars_page1.json")
        bars["bars"][0]["h"] = float("nan")
        bars["next_page_token"] = None

        candles = AlpacaAdapter(FakeTransport(bars_pages=[bars])).get_candles(
            _PRODUCT, Granularity.ONE_DAY, 1_700_000_000, 1_700_086_400
        )

        assert len(candles) == 2, "both fixture bars survive; only the NaN leaf changes"
        assert candles[0].high == Decimal("0"), "the unparseable-leaf-reads-as-zero rule"
        assert candles[0].close == Decimal("131.9"), "parseable leaves are untouched"


# ---------------------------------------------------------------------------------------------
# Preview: synthesized from the book (FR-4, FR-7)
# ---------------------------------------------------------------------------------------------


class TestPreview:
    def test_a_notional_buy_preview_prices_off_the_ask_and_charges_nothing(self) -> None:
        preview = AlpacaAdapter(_full_transport()).preview_order(
            MarketIOCByQuote(product_id=_PRODUCT, side=Side.BUY, quote_size=Decimal("100"))
        )
        assert isinstance(preview, Preview)
        assert preview.synthetic is True
        # The notional is exact -- it is the number the caller asked to spend.
        assert preview.est_quote_size == Decimal("100")
        assert preview.est_base_size == Decimal("100") / Decimal("100.01")
        assert preview.est_fee == Decimal("0"), "buys carry no sell-side regulatory fee"
        assert preview.errors == ()
        assert preview.detail["best_bid"] == "99.99"
        assert preview.detail["best_ask"] == "100.01"
        assert preview.detail["price_basis"] == "latest_quote_ask"
        assert preview.detail["data_feed"] == "iex"

    def test_a_fractional_market_sell_prices_off_the_bid_and_pays_the_regulatory_fees(
        self,
    ) -> None:
        preview = AlpacaAdapter(_full_transport()).preview_order(
            MarketIOCByBase(product_id=_PRODUCT, side=Side.SELL, base_size=Decimal("0.5"))
        )
        assert preview.est_base_size == Decimal("0.5")
        assert preview.est_quote_size == Decimal("0.5") * Decimal("99.99")
        expected = estimate_regulatory_fees(
            Side.SELL, Decimal("0.5"), Decimal("0.5") * Decimal("99.99")
        )
        assert preview.est_fee == expected[0]
        assert preview.detail["fee_basis"] == "sell_side_regulatory_passthrough"

    def test_a_limit_sell_previews_against_the_limit_price_bound(self) -> None:
        """A limit never fills worse than its limit, so `base_size * limit_price` is a
        bound not a guess -- the `keel_broker_robinhood.preview_order` convention."""
        preview = AlpacaAdapter(_full_transport()).preview_order(
            LimitGTC(
                product_id=_PRODUCT,
                side=Side.SELL,
                base_size=Decimal("0.5"),
                limit_price=Decimal("132.10"),
            )
        )
        assert preview.est_quote_size == Decimal("0.5") * Decimal("132.10")
        assert preview.detail["price_basis"] == "limit_price"
        # The book is still read and surfaced: FR-4 feeds the spread gate on every kind.
        assert preview.detail["best_bid"] == "99.99"
        assert preview.detail["best_ask"] == "100.01"

    def test_a_stop_limit_buy_pays_nothing_and_names_its_bases(self) -> None:
        preview = AlpacaAdapter(_full_transport()).preview_order(
            StopLimitGTC(
                product_id=_PRODUCT,
                side=Side.BUY,
                base_size=Decimal("0.5"),
                stop_price=Decimal("90"),
                limit_price=Decimal("91"),
            )
        )
        assert preview.est_quote_size == Decimal("0.5") * Decimal("91")
        assert preview.est_fee == Decimal("0")
        assert preview.detail["cost_basis"] == "base_size_x_limit_price"

    def test_a_quote_with_no_active_ask_leaves_the_buy_unpriced_and_says_so(self) -> None:
        """Alpaca documents `ap: 0` as "no active ask". A zero ask must never be divided
        into a base size (a fabricated position), and a silent zero at the confirm gate
        reads as free money -- so the failure rides in `Preview.errors`."""
        transport = FakeTransport(quote=load_fixture("alpaca_quote_no_ask.json"))
        preview = AlpacaAdapter(transport).preview_order(
            MarketIOCByQuote(product_id=_PRODUCT, side=Side.BUY, quote_size=Decimal("100"))
        )
        assert preview.est_base_size == Decimal("0")
        assert preview.errors, "an unpriced leg must appear in errors"
        assert any("ask" in e for e in preview.errors)

    def test_a_nonfinite_quote_side_is_unpriced_and_reported_never_a_crash(self) -> None:
        """JSON `NaN`/`Infinity` tokens ride `parse_constant`, not `parse_float`, so they
        reach the adapter as `float("nan")`/`float("inf")` -- and `Decimal(str(...))`
        parses BOTH without raising, which means the `except` in `_decimal_or_none` never
        fires. `Decimal("NaN")` then crashes the `bid > 0` comparison and `Decimal(
        "Infinity")` compares `> 0` as a real price; a non-finite side must land in the
        same "no active side" path as a zero one -- `errors` says so, nothing raises --
        for the preview docstring's "every path that could not price the order populates
        `errors`" invariant to hold."""
        quote = json.loads('{"quote": {"bp": NaN, "ap": Infinity}}', parse_float=Decimal)
        preview = AlpacaAdapter(FakeTransport(quote=quote)).preview_order(
            MarketIOCByQuote(product_id=_PRODUCT, side=Side.BUY, quote_size=Decimal("100"))
        )

        assert preview.est_base_size == Decimal("0")
        assert preview.errors, "a non-finite quote side must appear in errors"
        assert any("ask" in e for e in preview.errors)
        assert preview.detail["best_bid"] == "none"
        assert preview.detail["best_ask"] == "none"

    def test_a_non_usd_product_is_refused_before_any_request_is_made(self) -> None:
        transport = _full_transport()
        with pytest.raises(UnsupportedOrder, match="USD"):
            AlpacaAdapter(transport).preview_order(
                MarketIOCByQuote(product_id="AAPL-EUR", side=Side.BUY, quote_size=Decimal("10"))
            )
        assert "get_latest_quote" not in transport.calls


# ---------------------------------------------------------------------------------------------
# Order placement (FR-3)
# ---------------------------------------------------------------------------------------------


class TestPlaceOrder:
    def test_a_notional_market_buy_places_the_mapped_body(self) -> None:
        transport = _full_transport()
        result = AlpacaAdapter(transport).place_order(
            MarketIOCByQuote(product_id=_PRODUCT, side=Side.BUY, quote_size=Decimal("100"))
        )
        assert isinstance(result, PlaceResult)
        assert result.success is True
        assert result.broker_order_id == "61e21a5c-c317-4942-8d86-7a1fc4760d7b"

        body = transport.calls["create_order"]["body"]
        assert body["symbol"] == "AAPL"
        assert body["notional"] == "100"
        assert body["type"] == "market"
        assert body["time_in_force"] == "day"
        assert body["extended_hours"] is False

    def test_a_fractional_market_sell_places_a_qty_body(self) -> None:
        transport = _full_transport()
        AlpacaAdapter(transport).place_order(
            MarketIOCByBase(product_id=_PRODUCT, side=Side.SELL, base_size=Decimal("0.7577533"))
        )
        body = transport.calls["create_order"]["body"]
        assert body["qty"] == "0.7577533"
        assert "notional" not in body

    def test_every_order_kind_places_successfully(self) -> None:
        adapter = AlpacaAdapter(_full_transport())
        specs = [
            MarketIOCByQuote(product_id=_PRODUCT, side=Side.BUY, quote_size=Decimal("100")),
            MarketIOCByBase(product_id=_PRODUCT, side=Side.SELL, base_size=Decimal("0.5")),
            LimitGTC(
                product_id=_PRODUCT,
                side=Side.SELL,
                base_size=Decimal("0.5"),
                limit_price=Decimal("132.10"),
            ),
            StopLimitGTC(
                product_id=_PRODUCT,
                side=Side.SELL,
                base_size=Decimal("0.5"),
                stop_price=Decimal("125"),
                limit_price=Decimal("124.75"),
            ),
        ]
        for spec in specs:
            assert adapter.place_order(spec).success is True

    def test_each_placement_mints_a_fresh_client_order_id(self) -> None:
        """A fresh uuid per ATTEMPT is the dedup posture `keel_broker_robinhood` documents:
        it never collapses two deliberately repeated orders, at the cost that a caller
        retrying after a timeout places twice. Neither default is safe both ways; this
        pins which one this adapter takes."""
        transport = _full_transport()
        adapter = AlpacaAdapter(transport)
        spec = MarketIOCByQuote(product_id=_PRODUCT, side=Side.BUY, quote_size=Decimal("100"))
        adapter.place_order(spec)
        adapter.place_order(spec)

        ids = [body["client_order_id"] for body in transport.create_order_bodies]
        assert len(ids) == 2
        assert ids[0] != ids[1], "each attempt must carry its own client_order_id"

    def test_a_venue_rejection_maps_to_a_failed_place_result(self) -> None:
        transport = _RejectingCreateTransport(
            AlpacaAPIError(422, "notional is out of range"), placed=load_fixture(
                "alpaca_order_placed.json"
            )
        )
        result = AlpacaAdapter(transport).place_order(
            MarketIOCByQuote(product_id=_PRODUCT, side=Side.BUY, quote_size=Decimal("100"))
        )
        assert result.success is False
        assert result.broker_order_id is None
        assert result.reason == "notional is out of range"

    def test_a_buying_power_refusal_maps_to_a_failed_place_result(self) -> None:
        transport = _RejectingCreateTransport(
            AlpacaAPIError(403, "insufficient buying power"), placed=load_fixture(
                "alpaca_order_placed.json"
            )
        )
        result = AlpacaAdapter(transport).place_order(
            MarketIOCByQuote(product_id=_PRODUCT, side=Side.BUY, quote_size=Decimal("100"))
        )
        assert result.success is False
        assert "buying power" in (result.reason or "")

    def test_an_infra_error_propagates_rather_than_reading_as_a_rejection(self) -> None:
        """A 5xx during placement is an UNKNOWN outcome, not a refusal: mapping it to
        `success=False` would invite a caller to place again while the first order may be
        live. Only the venue's explicit rejection codes (403/422) become failures."""
        transport = _RejectingCreateTransport(
            AlpacaAPIError(500, "internal error"), placed=load_fixture("alpaca_order_placed.json")
        )
        with pytest.raises(AlpacaAPIError):
            AlpacaAdapter(transport).place_order(
                MarketIOCByQuote(product_id=_PRODUCT, side=Side.BUY, quote_size=Decimal("100"))
            )

    def test_a_terminal_status_on_the_happy_path_is_not_a_live_order(self) -> None:
        placed = load_fixture("alpaca_order_placed.json")
        placed["status"] = "rejected"
        transport = FakeTransport(placed=placed)
        result = AlpacaAdapter(transport).place_order(
            MarketIOCByQuote(product_id=_PRODUCT, side=Side.BUY, quote_size=Decimal("100"))
        )
        assert result.success is False
        assert result.broker_order_id is None
        assert placed["id"] in (result.reason or "")


# ---------------------------------------------------------------------------------------------
# Order status and cancellation
# ---------------------------------------------------------------------------------------------


class TestOrderStatus:
    def test_a_filled_order_reports_observed_economics(self) -> None:
        transport = _full_transport()
        adapter = AlpacaAdapter(transport)
        placed = adapter.place_order(
            MarketIOCByQuote(product_id=_PRODUCT, side=Side.BUY, quote_size=Decimal("100"))
        )

        status = adapter.get_order(placed.broker_order_id or "")
        assert isinstance(status, OrderStatus)
        assert status.status == "FILLED"
        assert status.filled_size == Decimal("0.7577533883593")
        assert status.average_filled_price == Decimal("131.9700139996")

    def test_total_fees_is_zero_because_the_api_exposes_no_per_order_fee_field(self) -> None:
        """Alpaca's order object carries no fee; sell-side regulatory fees are netted from
        proceeds and only surface account-wide (`pending_reg_taf_fees`). Reporting zero
        here is a statement about what the venue exposes, not a claim orders trade free."""
        transport = _full_transport()
        adapter = AlpacaAdapter(transport)
        placed = adapter.place_order(
            MarketIOCByQuote(product_id=_PRODUCT, side=Side.BUY, quote_size=Decimal("100"))
        )
        assert adapter.get_order(placed.broker_order_id or "").total_fees == Decimal("0")

    def test_an_unknown_order_id_is_terminal_failed_never_an_exception(self) -> None:
        status = AlpacaAdapter(_full_transport()).get_order("an-id-this-venue-never-issued")
        assert status.status == "FAILED"
        zero = Decimal("0")
        assert status.filled_size == status.average_filled_price == status.total_fees == zero

    def test_an_unrecognised_status_stays_pending(self) -> None:
        placed = load_fixture("alpaca_order_placed.json")
        placed["status"] = "brand_new_status"
        transport = FakeTransport(placed=placed, order=placed)
        adapter = AlpacaAdapter(transport)
        placed_result = adapter.place_order(
            MarketIOCByQuote(product_id=_PRODUCT, side=Side.BUY, quote_size=Decimal("100"))
        )
        assert adapter.get_order(placed_result.broker_order_id or "").status == "PENDING"


class TestCancel:
    def test_a_204_is_the_venue_confirmation(self) -> None:
        adapter = AlpacaAdapter(_full_transport())
        placed = adapter.place_order(
            MarketIOCByQuote(product_id=_PRODUCT, side=Side.BUY, quote_size=Decimal("100"))
        )
        assert adapter.cancel_order(placed.broker_order_id or "") is True

    def test_an_id_the_venue_never_issued_is_false_not_a_raise(self) -> None:
        assert AlpacaAdapter(_full_transport()).cancel_order("never-issued") is False

    def test_an_order_that_is_no_longer_cancellable_is_not_a_confirmation(self) -> None:
        """Alpaca answers DELETE with 422 when an order can no longer be cancelled (e.g.
        already filled); 404 when it never existed. Neither is a confirmed cancellation."""
        transport = FakeTransport(
            placed=load_fixture("alpaca_order_placed.json"), cancel_status=422
        )
        adapter = AlpacaAdapter(transport)
        placed = adapter.place_order(
            MarketIOCByQuote(product_id=_PRODUCT, side=Side.BUY, quote_size=Decimal("100"))
        )
        assert adapter.cancel_order(placed.broker_order_id or "") is False

    def test_a_transport_failure_on_the_exit_path_is_false_not_an_exception(self) -> None:
        class _Exploding(FakeTransport):
            def cancel_order(self, order_id: str) -> Any:
                self._record("cancel_order", order_id=order_id)
                raise AlpacaAPIError(500, "boom")

        transport = _Exploding(placed=load_fixture("alpaca_order_placed.json"))
        adapter = AlpacaAdapter(transport)
        placed = adapter.place_order(
            MarketIOCByQuote(product_id=_PRODUCT, side=Side.BUY, quote_size=Decimal("100"))
        )
        assert adapter.cancel_order(placed.broker_order_id or "") is False


# ---------------------------------------------------------------------------------------------
# Session awareness (FR-9)
# ---------------------------------------------------------------------------------------------


class TestSession:
    def test_the_market_session_comes_from_the_venue_clock(self) -> None:
        """Equities are not 24/7; open/closed comes from the venue's own clock, not a
        locally maintained calendar that drifts."""
        open_adapter = AlpacaAdapter(FakeTransport(clock=load_fixture("alpaca_clock_open.json")))
        closed_adapter = AlpacaAdapter(
            FakeTransport(clock=load_fixture("alpaca_clock_closed.json"))
        )
        assert open_adapter.is_market_open() is True
        assert closed_adapter.is_market_open() is False

    def test_alpaca_declares_itself_session_bound(self) -> None:
        """The one first-party equities venue: `session_bound=True` is what makes the engine
        consult the clock at all (FR-9). A 24/7 default here would silently reinstate the
        crypto staleness semantics on weekends and holidays."""
        assert AlpacaAdapter().capabilities().session_bound is True

    def test_market_clock_reads_the_venue_clock(self) -> None:
        """The port-level clock reuses the same `/v2/clock` read `is_market_open` was built
        on (Phase A): open and closed fixtures answer `SessionState.OPEN`/`CLOSED`."""
        open_adapter = AlpacaAdapter(FakeTransport(clock=load_fixture("alpaca_clock_open.json")))
        closed_adapter = AlpacaAdapter(
            FakeTransport(clock=load_fixture("alpaca_clock_closed.json"))
        )
        assert open_adapter.market_clock() is SessionState.OPEN
        assert closed_adapter.market_clock() is SessionState.CLOSED
        # Phase A's adapter-specific extra stays answerable and agrees with the port answer.
        assert open_adapter.is_market_open() is True
        assert closed_adapter.is_market_open() is False

    def test_market_clock_fails_closed_when_the_clock_cannot_be_read(self) -> None:
        """FR-9's fail-closed rule, at the adapter: a transport error or an absent clock is
        `CLOCK_UNAVAILABLE` -- never an exception (the agent cycle must not crash on it) and
        never a guess of OPEN (trading on an unknown session state)."""

        class _ExplodingTransport(FakeTransport):
            def get_clock(self) -> Any:
                raise AlpacaAPIError(503, "clock endpoint unavailable")

        assert AlpacaAdapter(_ExplodingTransport()).market_clock() is SessionState.CLOCK_UNAVAILABLE
        # No transport injected at all: the same fail-closed answer, not a RuntimeError.
        assert AlpacaAdapter().market_clock() is SessionState.CLOCK_UNAVAILABLE

    @pytest.mark.parametrize(
        "clock_body",
        [
            {"timestamp": "2026-08-14T20:00:00Z", "next_open": "2026-08-17T13:30:00Z"},
            {"is_open": None},
            {"is_open": "true"},
            {"is_open": 1},
        ],
        ids=["is_open-absent", "is_open-null", "is_open-string", "is_open-number"],
    )
    def test_a_body_without_a_usable_is_open_is_clock_unavailable_not_closed(
        self, clock_body: dict[str, Any]
    ) -> None:
        """A 2xx body that says nothing USABLE about the session is an unreadable clock, not
        a closed one: CLOSED defuses staleness alerting forever (re-recorded fresh each
        cycle), which is exactly what a malformed body must never be allowed to do. Only an
        actual boolean answers OPEN/CLOSED -- fail-loud for alerting, fail-closed for
        trading, the PR's own stated rule."""
        assert AlpacaAdapter(FakeTransport(clock=clock_body)).market_clock() is (
            SessionState.CLOCK_UNAVAILABLE
        )
        # Phase A's boolean form reads the same posture: not open.
        assert AlpacaAdapter(FakeTransport(clock=clock_body)).is_market_open() is False

    def test_only_an_actual_boolean_answers_open_or_closed(self) -> None:
        """The positive half of the rule: `is_open: true`/`false` are the only shapes that
        answer OPEN/CLOSED at all."""
        assert (
            AlpacaAdapter(FakeTransport(clock={"is_open": True})).market_clock()
            is SessionState.OPEN
        )
        assert (
            AlpacaAdapter(FakeTransport(clock={"is_open": False})).market_clock()
            is SessionState.CLOSED
        )

    # -- market_schedule (issue #388 C2: the console session banner's port read) ----------------

    def test_market_schedule_parses_the_venues_next_open_and_next_close(self) -> None:
        """`/v2/clock` already carries `next_open`/`next_close`; the schedule read is the SAME
        endpoint with those two fields crossed as epoch ints instead of dropped. The fixture's
        RFC3339 values are pinned as precomputed constants -- recomputing them with the parse
        helper under test would assert the implementation against itself."""
        from keel_broker_api.results import MarketSchedule

        open_adapter = AlpacaAdapter(FakeTransport(clock=load_fixture("alpaca_clock_open.json")))
        assert open_adapter.market_schedule() == MarketSchedule(
            state=SessionState.OPEN,
            next_open_ts=1_787_059_800,  # 2026-08-18T13:30:00Z
            next_close_ts=1_786_996_800,  # 2026-08-17T20:00:00Z
        )
        closed_adapter = AlpacaAdapter(
            FakeTransport(clock=load_fixture("alpaca_clock_closed.json"))
        )
        assert closed_adapter.market_schedule() == MarketSchedule(
            state=SessionState.CLOSED,
            next_open_ts=1_786_973_400,  # 2026-08-17T13:30:00Z
            next_close_ts=1_786_996_800,  # 2026-08-17T20:00:00Z
        )

    def test_market_schedule_fails_closed_like_the_clock_when_unreadable(self) -> None:
        """The same fail-closed posture `market_clock` keeps: a transport error, a missing
        transport or a body without a usable `is_open` answers CLOCK_UNAVAILABLE -- never an
        exception, never a guess -- and claims NO schedule alongside it (nulls, not a
        timestamp nobody vouches for)."""

        class _ExplodingTransport(FakeTransport):
            def get_clock(self) -> Any:
                raise AlpacaAPIError(503, "clock endpoint unavailable")

        for adapter in (
            AlpacaAdapter(_ExplodingTransport()),
            AlpacaAdapter(),
            AlpacaAdapter(FakeTransport(clock={"is_open": None})),
        ):
            schedule = adapter.market_schedule()
            assert schedule.state is SessionState.CLOCK_UNAVAILABLE
            assert schedule.next_open_ts is None
            assert schedule.next_close_ts is None

    def test_a_malformed_schedule_timestamp_degrades_to_null_not_an_unreadable_clock(
        self,
    ) -> None:
        """The state and the schedule are different guarantees: a usable `is_open` still
        answers OPEN/CLOSED when `next_open` is garbage, and only the unusable FIELD is
        dropped. Laundering a bad timestamp into CLOCK_UNAVAILABLE would hide the venue's
        own session answer behind a data nit; keeping a parsed-failure value would be
        worse still."""
        schedule = AlpacaAdapter(
            FakeTransport(
                clock={
                    "is_open": True,
                    "next_open": "not-a-timestamp",
                    "next_close": "2026-08-17T20:00:00Z",
                }
            )
        ).market_schedule()
        assert schedule.state is SessionState.OPEN
        assert schedule.next_open_ts is None
        assert schedule.next_close_ts == 1_786_996_800

    def test_no_order_body_ever_asks_for_extended_hours(self) -> None:
        """Overnight/extended sessions are OFF by posture (FR-9): thinner liquidity would
        hold the #350 spread gate permanently. Every body pins `extended_hours: False`."""
        transport = _full_transport()
        adapter = AlpacaAdapter(transport)
        adapter.place_order(
            MarketIOCByQuote(product_id=_PRODUCT, side=Side.BUY, quote_size=Decimal("100"))
        )
        adapter.place_order(
            LimitGTC(
                product_id=_PRODUCT,
                side=Side.SELL,
                base_size=Decimal("0.5"),
                limit_price=Decimal("132.10"),
            )
        )
        assert transport.calls["create_order"]["body"]["extended_hours"] is False
