"""The Robinhood Crypto adapter: `Broker` implemented against Robinhood's Crypto Trading API v2.

Every Robinhood-specific decision the engine must not know about lives in this package -- request
signing and pagination in `transport.py`, order-body and status shape in `translate.py`, and the
capability declaration below.

The transport is injected, never constructed here, so tests exercise the adapter against canned
fixtures with zero live network calls. It defaults to `None` so `RobinhoodAdapter()` is
constructible without credentials -- `capabilities()` is answerable offline, and any method that
actually needs the network raises a clear error rather than a confusing `AttributeError`. That
matters more here than it does for Coinbase: **Robinhood ships no sandbox**, so there is no
"harmless" configuration of this adapter that talks to a real endpoint. Canned or nothing.

⚠️ **This adapter cannot open positions under keel's current entry model, and that is not a bug
here -- it is a fact about the venue that this file refuses to paper over.**

keel places entries as `MarketIOCByQuote` ("spend 100 USD of BTC"). Robinhood's
`market_order_config` accepts `asset_quantity` and nothing else; there is no quote-sized market
order anywhere in the v2 API. The adapter therefore leaves `market_ioc_quote` out of
`supported_orders` and raises `UnsupportedOrder` for it.

The tempting alternative -- call `estimated_price`, divide the quote size by it, and place the
resulting `asset_quantity` -- is deliberately NOT implemented. It would mean the adapter accepted
an order sized in one basis and placed an order sized in another, on the live-money path, with
the substitution invisible to the caller. `UnsupportedOrder`'s own docstring calls this out: "an
adapter must still refuse rather than substitute a different order type." An estimate that moves
between the quote and the fill is not an implementation detail when the difference is the size of
the position. So this adapter is, for now, an EXIT and RESTING-ORDER venue: it can sell a
holding at market, rest a take-profit limit, and rest a protective stop-limit.

The other two gaps, stated once here and again in the package README:

* **No candles.** The v2 API exposes `best_bid_ask` and `estimated_price` and nothing else --
  there is no OHLC, historical, or candles endpoint at all. `get_candles` raises `ValueError`
  for every granularity, which is the port's sanctioned way to say "I serve no candles": the
  conformance suite's `_any_candles` helper catches `ValueError` per granularity and skips when
  none work. Robinhood is an EXECUTION venue as far as keel is concerned; bars come from
  elsewhere.
* **No sandbox.** Robinhood publishes no test environment. Every test against this adapter runs
  on a canned in-memory transport, and the conformance suite is the only end-to-end signal there
  will ever be short of real money.
"""

from __future__ import annotations

import time
import uuid
from decimal import Decimal, InvalidOperation

from keel_broker_api.capabilities import BrokerCapabilities
from keel_broker_api.orders import LimitGTC, MarketIOCByBase, OrderSpec, StopLimitGTC
from keel_broker_api.port import UnsupportedOrder
from keel_broker_api.results import Balance, FeeSummary, OrderStatus, PlaceResult, Preview
from keel_core.types import Candle, Granularity

from keel_broker_robinhood.translate import (
    to_order_body,
    to_port_status,
    to_price_side,
    to_symbol,
)
from keel_broker_robinhood.transport import Transport, _field, _results

_VENUE = "robinhood"

_CAPABILITIES = BrokerCapabilities(
    venue=_VENUE,
    # `market_ioc_quote` is absent on purpose -- see the module docstring. Its absence is what
    # stops the engine from routing an ENTRY here and getting something other than what it
    # asked for.
    #
    # `market_ioc_base` is present despite the port's name saying IOC, and Robinhood accepting no
    # `time_in_force` on a market order at all. That is a naming impedance, not a capability lie:
    # a market order is by construction immediate -- it either crosses the book now or it is
    # rejected -- so "immediate or cancel" describes what Robinhood's market order already does.
    # There is no resting-market-order variant to be confused with. The port kind that WOULD be a
    # lie is the quote-sized one, and it is not declared.
    supported_orders=frozenset({"market_ioc_base", "limit_gtc", "stop_limit_gtc"}),
    # No preview endpoint exists on this API, so every Preview this adapter returns is a number
    # it computed itself and must label `synthetic=True`.
    supports_native_preview=False,
    synthesizes_preview=True,
    supports_fee_summary=True,
    # Robinhood's docs say "Only USD symbols are accepted" -- not USDC, which is what Coinbase
    # settles keel's trades in. `translate.to_symbol` refuses a non-USD quote leg by name rather
    # than rewriting it, because rewriting `BTC-USDC` to `BTC-USD` would swap the settlement
    # asset underneath the caller.
    quote_currencies=frozenset({"USD"}),
    asset_classes=frozenset({"spot"}),
)

#: Every `Granularity` the port defines, refused with the same reason. Kept as a single message
#: so the failure reads as a property of the VENUE rather than of the requested timeframe -- a
#: caller who reads "granularity not supported" will go looking for a supported one, and there
#: isn't one.
_NO_CANDLES = (
    "robinhood's crypto trading API v2 exposes no OHLC, candles, or historical endpoint "
    "(only best_bid_ask and estimated_price), so no granularity can be served -- this venue is "
    "an execution venue for keel, and candle data must come from another source"
)


class RobinhoodAdapter:
    """Implements the `Broker` port against the Robinhood Crypto Trading API v2.

    v2 exclusively, never v1. Two things force it and both are contract-level, not cosmetic:
    v1's cancel endpoint answers `text/plain` "Cancel request was submitted", which is an
    acknowledgement of a REQUEST and cannot satisfy `cancel_order`'s "return `True` only when the
    venue confirms the cancellation for THIS order id"; and v1 carries neither the per-order
    `fee_charged` that `get_order` needs for observed economics nor the `fee_tier_status` that
    `get_fee_summary` is built from. An adapter written against v1 would have to guess at all
    three, and guessing is the thing the port exists to prevent.
    """

    def __init__(self, transport: Transport | None = None) -> None:
        self._transport = transport

    def _require_transport(self) -> Transport:
        if self._transport is None:
            raise RuntimeError(
                "RobinhoodAdapter was constructed without a transport; "
                "inject one to make network-backed calls"
            )
        return self._transport

    def capabilities(self) -> BrokerCapabilities:
        return _CAPABILITIES

    def get_candles(
        self, product_id: str, granularity: Granularity, start_ts: int, end_ts: int
    ) -> list[Candle]:
        """Always raises `ValueError`: this API has no candles endpoint of any kind.

        Refusing is the only honest answer, and specifically it must not return `[]`. An empty
        list reads downstream as "this market had no trades in the window", which is a statement
        about the MARKET; the truth is a statement about the API. A rule evaluated against
        silently-empty bars does not error, it just decides nothing -- or worse, decides
        something from a window it thinks is flat.

        `ValueError` (rather than `NotImplementedError`) is deliberate: the conformance suite's
        `_any_candles` helper catches `ValueError` per granularity and skips when every one of
        them refuses, which is the port's sanctioned way for a venue to declare it serves no
        bars. `FakeAdapter` uses the same signal for the granularities it does not carry.
        """
        raise ValueError(_NO_CANDLES)

    def get_balances(self) -> list[Balance]:
        """Return per-currency balances as domain types, never Robinhood's holding dicts.

        Two different endpoints feed this, because Robinhood splits the answer in a way Coinbase
        does not. `holdings/` reports crypto positions and gives both a `total_quantity` and a
        `quantity_available_for_trading`, which map cleanly onto `total`/`available` -- the gap
        between them is the venue's hold, exactly what `available` is for. Cash is not a holding;
        it is the account's `buying_power`, so it is emitted separately under whatever
        `buying_power_currency` says (USD in practice).

        For the cash balance `total` equals `available`. That is not a shortcut: the v2 accounts
        payload exposes one spendable number and no separate "cash on hold" figure, so inventing
        a larger `total` would be asserting a number the venue never reported. Equality here says
        "nothing is known to be held back", which is what the payload actually supports.
        """
        transport = self._require_transport()

        balances: list[Balance] = []
        for raw in _results(transport.get_holdings()):
            total = Decimal(str(_field(raw, "total_quantity", "0") or "0"))
            available = Decimal(str(_field(raw, "quantity_available_for_trading", "0") or "0"))
            balances.append(
                Balance(
                    currency=str(_field(raw, "asset_code", "")),
                    available=available,
                    total=total,
                )
            )

        account = self._account()
        buying_power = Decimal(str(_field(account, "buying_power", "0") or "0"))
        currency = str(_field(account, "buying_power_currency", "USD") or "USD")
        balances.append(Balance(currency=currency, available=buying_power, total=buying_power))
        return balances

    def _account(self) -> object:
        """The first account from `GET /accounts/`, or `{}` when the response carries none.

        `{}` rather than a raise: the callers that need it (`get_balances`, `get_fee_summary`,
        the fee leg of `preview_order`) each have a documented degraded answer for missing data,
        and all three of those answers are safer than an exception thrown from a method the
        executor may be calling on an EXIT path. A raise on the way out of a position can trap
        it; that reasoning is written down in `BrokerCapabilities`' docstring and applies here.
        """
        accounts = _results(self._require_transport().get_accounts())
        return accounts[0] if accounts else {}

    def _fee_ratio(self) -> Decimal | None:
        """The account's fee ratio, or `None` when the venue did not report one.

        `None` is distinct from `Decimal("0")` on purpose and the two must not be collapsed:
        zero is a claim that this account trades free, and nothing in the payload supports that
        claim when the field is simply absent. Callers turn `None` into a zero fee ESTIMATE only
        where they also label the estimate's basis as unknown.
        """
        tier = _field(self._account(), "fee_tier_status") or {}
        raw = _field(tier, "fee_ratio")
        if raw is None:
            return None
        try:
            return Decimal(str(raw))
        except (InvalidOperation, ValueError):
            return None

    def _reject_unsupported(self, spec: OrderSpec) -> None:
        """Refuse an undeclared order kind before anything venue-shaped is built for it.

        `translate.to_order_body` refuses `MarketIOCByQuote` a second time. The duplication is
        deliberate defence in depth on the one path where a silent substitution would be a
        differently-sized live position: this gate is the one the capability declaration is
        derived from, and that one is the last statement before a body goes on the wire.
        """
        if spec.kind not in _CAPABILITIES.supported_orders:
            raise UnsupportedOrder(
                f"robinhood does not support order kind {spec.kind!r} "
                f"(supported: {', '.join(sorted(_CAPABILITIES.supported_orders))})"
            )

    def preview_order(self, spec: OrderSpec) -> Preview:
        """Synthesise a preview. Always `synthetic=True` -- there is no preview endpoint here.

        Coinbase answers `preview_order` with its own quote, so approving it is approving the
        venue's arithmetic. Robinhood answers nothing, so every number below is this adapter's
        arithmetic, and `Preview`'s docstring is explicit that "approving an estimate must never
        look identical to approving a broker's own quote". `synthetic=True` is what carries that
        distinction to whatever renders the confirm gate.

        The three fields, and how firm each one actually is:

        * `est_base_size` is exact. All three supported kinds are base-sized, so this is the
          number the caller asked for, not an estimate at all.
        * `est_quote_size` for `limit_gtc`/`stop_limit_gtc` is `base_size * limit_price`. That is
          a BOUND, not a prediction: a limit order does not trade worse than its limit, so this
          is the most quote currency a sell can realise or the most a buy can spend. For
          `market_ioc_base` there is no bound to quote, so it comes from `estimated_price` and
          is a genuine guess that the fill can and will miss.
        * `est_fee` is `est_quote_size * fee_tier_status.fee_ratio`. When the account reports no
          ratio it is `Decimal("0")` and `detail["fee_ratio"]` reads `"unknown"` -- a made-up
          rate would be worse than a visible zero, because a plausible-looking fee is one nobody
          checks. `detail["price_basis"]` names which of the two paths above produced the quote
          size, so the reader can tell a bound from a guess without inferring it from the kind.
        """
        self._reject_unsupported(spec)
        base_size = self._base_size(spec)

        if isinstance(spec, LimitGTC | StopLimitGTC):
            price, basis = spec.limit_price, "limit_price"
        else:
            price, basis = self._estimated_price(spec), "estimated_price"

        quote_size = base_size * price
        ratio = self._fee_ratio()
        return Preview(
            product_id=spec.product_id,
            side=spec.side,
            est_base_size=base_size,
            est_quote_size=quote_size,
            est_fee=quote_size * ratio if ratio is not None else Decimal("0"),
            synthetic=True,
            detail={
                "price_basis": basis,
                "price": str(price),
                "fee_ratio": str(ratio) if ratio is not None else "unknown",
            },
        )

    def _base_size(self, spec: OrderSpec) -> Decimal:
        """The spec's base size. Reachable only for the three base-sized kinds.

        `MarketIOCByQuote` has no `base_size` to read, and `_reject_unsupported` has already
        raised for it by the time anything calls this -- so this raises rather than returning a
        placeholder, on the principle that a size derived from nothing is the one value that must
        never reach a preview the human is about to approve.
        """
        if isinstance(spec, MarketIOCByBase | LimitGTC | StopLimitGTC):
            return spec.base_size
        raise UnsupportedOrder(f"robinhood cannot size order kind {spec.kind!r} in base units")

    def _estimated_price(self, spec: OrderSpec) -> Decimal:
        """Robinhood's estimated price for this size, or `Decimal("0")` if it reports none.

        `side` is translated, not passed through: this endpoint answers in book terms
        (`bid`/`ask`), not order terms (`buy`/`sell`), and a buyer is filled from the ASK. Asking
        for the wrong side of the spread would understate the cost of every buy preview -- which
        is exactly the direction of error a human at a confirm gate is least likely to catch.

        A zero on a missing price is a visible nonsense that surfaces as a zero-cost preview,
        rather than an exception thrown mid-confirm; `errors` on `Preview` is the port's channel
        for a soft failure and the price basis in `detail` says where the number came from.
        """
        response = self._require_transport().get_estimated_price(
            symbol=to_symbol(spec.product_id),
            side=to_price_side(spec.side),
            quantity=str(self._base_size(spec)),
        )
        rows = _results(response)
        if not rows:
            return Decimal("0")
        try:
            return Decimal(str(_field(rows[0], "price", "0") or "0"))
        except (InvalidOperation, ValueError):
            return Decimal("0")

    def place_order(self, spec: OrderSpec) -> PlaceResult:
        """Place a live order. A fresh `client_order_id` per call gives Robinhood idempotency.

        The uuid is required by the API, not optional as it is on some venues, and it must be
        fresh per ATTEMPT rather than per spec: reusing one across a retry is how a caller asks
        the venue to deduplicate, and generating one per spec would silently deduplicate two
        orders a strategy genuinely meant to place twice.

        Robinhood signals failure with an HTTP error rather than the success/error envelope
        Coinbase returns, so a placement that comes back at all came back as an order. The one
        thing still worth checking is that it carries an `id`: a `PlaceResult(success=True,
        broker_order_id=None)` would be an order nobody can later reconcile or cancel, which is
        worse than a reported failure.
        """
        self._reject_unsupported(spec)
        body = to_order_body(spec, client_order_id=str(uuid.uuid4()))
        response = self._require_transport().create_order(body)

        order_id = _field(response, "id")
        if order_id is None:
            return PlaceResult(
                success=False,
                broker_order_id=None,
                reason="robinhood accepted the request but returned no order id",
            )
        return PlaceResult(success=True, broker_order_id=str(order_id))

    def get_fee_summary(self) -> FeeSummary:
        """Map v2's `fee_tier_status` to a `FeeSummary`. Read the `fees_usd` note below.

        `volume_window` is `"trailing_30d"`, and unlike Coinbase's `"unknown"` that is a
        statement the docs actually support: the field is literally named `thirty_day_volume`.
        Coinbase's `advanced_trade_only_volume` names no window, so its adapter says so; here the
        name IS the window, and declaring `"unknown"` would throw away information the venue gave
        us and force reconciliation into a weaker test than it needs.

        `taker_rate` and `maker_rate` both carry the single `fee_ratio`. Robinhood publishes one
        ratio and does not split by liquidity role anywhere in the v2 docs, so this is not two
        numbers collapsed into one -- it is one number reported in both fields because it applies
        to both cases. The alternative, zeroing `maker_rate`, would claim resting orders trade
        free, which nothing supports.

        ⚠️ `fees_usd` is always `Decimal("0")`, and this is a REAL GAP, not a formality. The v2
        API exposes per-order `fee_charged` but no account-level fees-paid total, and this method
        has no order history to sum. `FeeSummary`'s docstring says subscription lapse detection
        leans on `fees_usd` -- specifically, a fee charged while the user claims a fee-free
        allowance contradicts the claim. A constant zero can never contradict anything, so
        against this venue that test is inert and detection falls back to attestation alone.
        Anything consuming this must treat a Robinhood `fees_usd` as "not reported", never as
        "no fees were charged". Closing this properly means paging order history and summing
        `fee_charged`, which is a follow-up with its own rate-limit design.
        """
        account = self._account()
        tier = _field(account, "fee_tier_status") or {}
        ratio = self._fee_ratio() or Decimal("0")
        return FeeSummary(
            venue=_VENUE,
            taker_rate=ratio,
            maker_rate=ratio,
            volume_usd=Decimal(str(_field(tier, "thirty_day_volume", "0") or "0")),
            fees_usd=Decimal("0"),
            volume_window="trailing_30d",
            fetched_at=int(time.time()),
        )

    def get_order(self, order_id: str) -> OrderStatus:
        """Observed state of a previously placed order, normalized to `Decimal` money fields.

        This is what makes exit reconciliation possible at all. A placement response only says
        the order was accepted; nothing in it reveals that a resting bracket later filled, at
        what price, or for what fee. Without this the executor records the EXPECTED price and a
        previewed commission, so realized P&L is modelled rather than observed -- and against
        this venue the modelled number would be worse than usual, since its preview is synthetic
        to begin with.

        An id the venue does not recognise comes back as a normal `OrderStatus` with status
        `"FAILED"` and zeroed money fields, never an exception. `FakeAdapter` set that precedent
        and the reason is the same one: `OrderStatus`'s contract is that callers do arithmetic on
        its money fields without special-casing, and making them catch a venue-shaped 404 just
        moves the special case one layer up. The transport is what makes this safe -- it returns
        `None` ONLY on a genuine 404 for this id and raises on everything else, so a network
        blip can never be laundered into "this order failed".
        """
        response = self._require_transport().get_order(order_id)
        if response is None:
            return _terminal_unknown(order_id)
        return OrderStatus(
            order_id=str(_field(response, "id", order_id) or order_id),
            status=to_port_status(_field(response, "state")),
            filled_size=Decimal(str(_field(response, "filled_asset_quantity", "0") or "0")),
            average_filled_price=Decimal(str(_field(response, "average_price", "0") or "0")),
            total_fees=Decimal(str(_field(response, "fee_charged", "0") or "0")),
        )

    def cancel_order(self, order_id: str) -> bool:
        """Cancel one resting order. `True` only if the venue CONFIRMS the cancellation.

        v2's cancel endpoint returns the full order object as JSON, which is the whole reason
        this adapter targets v2: v1 answers `text/plain` "Cancel request was submitted", an
        acknowledgement that the REQUEST arrived and not a statement about the order. Reading
        that as success would let `executor._cancel_at_exchange` record a cancel that never
        happened -- and the order it believes is gone is still resting, still able to fill.

        So the confirmation is read from the returned object's own `state`, and only
        `"canceled"` (Robinhood's spelling) counts. A cancel is asynchronous at this venue: the
        response can legitimately still read `open` because the request is queued behind the
        matching engine. That is not a failure and not a success -- it is an unanswered question,
        so the order is re-polled ONCE via `GET /orders/{id}/` and the answer taken from there.

        Once, not in a loop, and not with a sleep: this runs on the executor's path and a
        retry loop here would block an exit while an order it wants gone is still live. A `False`
        from a still-pending cancel is the conservative outcome -- the engine keeps believing the
        order might be resting, which is the belief that keeps it watching. `True` on a cancel
        that had not landed is the outcome with no recovery.

        An id the venue never issued returns `False` rather than raising, per the port docstring:
        absence of a refusal is not a confirmation, and neither is a 404.
        """
        transport = self._require_transport()

        response = transport.cancel_order(order_id)
        if response is None:
            return False
        if _confirms_cancel(response, order_id):
            return True

        polled = transport.get_order(order_id)
        if polled is None:
            return False
        return _confirms_cancel(polled, order_id)


#: Robinhood's own spelling of the terminal cancelled state. Compared against raw venue JSON,
#: so it is the venue's single-`l` spelling and NOT the port's `"CANCELLED"` -- the two meet
#: only in `translate.STATE_TO_PORT_STATUS`, and reading the port's spelling here would silently
#: never match, turning every confirmed cancel into a `False`.
_CANCELED = "canceled"


def _confirms_cancel(order: object, order_id: str) -> bool:
    """Whether `order` is a confirmation that THIS id is cancelled.

    The id is checked, not assumed. A response for a different order would be a venue bug rather
    than an expected case, but "the object came back" is not the same claim as "the object I
    asked about came back cancelled", and this boolean is the one the executor writes local state
    from.
    """
    returned_id = _field(order, "id")
    if returned_id is not None and str(returned_id) != order_id:
        return False
    return str(_field(order, "state", "") or "") == _CANCELED


def _terminal_unknown(order_id: str) -> OrderStatus:
    """The answer for an id the venue does not recognise: `FAILED`, with money fields zeroed."""
    return OrderStatus(
        order_id=order_id,
        status="FAILED",
        filled_size=Decimal("0"),
        average_filled_price=Decimal("0"),
        total_fees=Decimal("0"),
    )


__all__ = ["RobinhoodAdapter"]
