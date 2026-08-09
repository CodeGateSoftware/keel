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
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from keel_broker_api.capabilities import BrokerCapabilities
from keel_broker_api.orders import LimitGTC, MarketIOCByBase, OrderSpec, StopLimitGTC
from keel_broker_api.port import UnsupportedOrder
from keel_broker_api.results import Balance, FeeSummary, OrderStatus, PlaceResult, Preview
from keel_core.types import Candle, Granularity

from keel_broker_robinhood.translate import (
    _render,
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
    # No preview endpoint exists on this API, so every Preview this adapter returns must label
    # itself `synthetic=True`.
    #
    # `/estimated_price/` is NOT a counter-example, and the first live run (#217) is why this
    # comment now says so explicitly: that endpoint hands back `est_fee` and `est_total_cost`,
    # which look exactly like a broker's own quote and are not one. It prices a QUANTITY. It does
    # not validate the order, check buying power, check this account's own size bounds, or reserve
    # anything -- an order it prices happily can be rejected the instant it is placed. Reading the
    # venue's numbers instead of deriving our own makes a preview more ACCURATE; it does not make
    # it a quote, and `Preview.synthetic` is the field that carries exactly that difference.
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

#: How far `est_total_cost` may sit from a candidate relation and still be taken as satisfying it,
#: as an absolute floor and as a fraction of the notional (one cent, or one basis point, whichever
#: is larger).
#:
#: A tolerance is required rather than fastidious: the venue rounds its own `est_fee` and
#: `est_total_cost` to some undisclosed precision, so `price * quantity + est_fee` computed here
#: at full `Decimal` precision will routinely miss the venue's rounded total by sub-cent amounts.
#: Demanding exact equality would report every healthy response as unreconciled, and a check that
#: fires on every run is a check nobody reads (see #217 F5 for what that costs).
#:
#: It is bounded in the other direction by what it must still catch: the readings it discriminates
#: between differ by a whole `est_fee`, which at this venue's ~25bp taker rate is 25x a one-basis-
#: point tolerance. A fee small enough to hide inside the tolerance is a fee too small to
#: materially mis-state the order either way.
_TOTAL_TOLERANCE_ABS = Decimal("0.01")
_TOTAL_TOLERANCE_RATIO = Decimal("0.0001")

#: How far back `get_fee_summary` sums `fee_charged`, in seconds.
#:
#: Thirty days, and it is not a tunable: it is pinned to the window `volume_usd` already reports.
#: `FeeSummary` carries ONE `volume_window` for both figures, so a fee total covering a different
#: span than `fee_tier_status.thirty_day_volume` would be mislabelled by the very field that
#: exists to stop a caller comparing incompatible windows (see `FeeSummary`'s own docstring). Any
#: change here is a change to `volume_window`'s truthfulness, not a knob.
_FEE_WINDOW_SECONDS = 30 * 24 * 60 * 60

#: How the fee window's start is rendered for `updated_at_start`.
#:
#: The v2 docs specify ISO 8601 for this parameter. Seconds precision with an explicit `Z` is the
#: unambiguous form: no offset to be misread as local time, and no fractional part for the venue
#: to parse differently than we wrote it. `datetime.isoformat()` is deliberately not used -- it
#: renders UTC as `+00:00`, and that `+` on a SIGNED query string is the exact hazard
#: `transport._request` documents at `quote_via=quote` (a raw `+` decodes server-side as a space).
_WINDOW_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class _VenueEstimate:
    """One `/estimated_price/` row, reconciled into the terms `Preview` is defined in.

    This exists because the endpoint answers with four related numbers (`ask`/`bid`, `quantity`,
    `est_fee`, `est_total_cost`) and `Preview` has two slots for them, under a convention the port
    fixes and the venue does not share: `est_quote_size` excludes the fee, and `est_fee` sits
    beside it. Returning a bare `Decimal` price, as this used to, threw away the venue's own
    statement of the fee and total and forced `preview_order` to re-derive both -- which is how a
    preview ends up asserting arithmetic the venue never agreed to.

    `errors` rides along rather than being raised: every one of them is a soft failure that still
    leaves a usable (if less certain) estimate, and this runs on a path the executor uses while
    unwinding a position, where a raise can trap it. `preview_order` folds them into
    `Preview.errors`, which is the field the port defines for exactly this.
    """

    #: The unit price from the column named after the requested side. Never from `price`, which
    #: this venue does not send (#217 F1).
    price: Decimal
    #: Fee-EXCLUSIVE notional, in quote currency. This is what `Preview.est_quote_size` means:
    #: the limit path fills it with `base_size * limit_price`, and `est_fee` is a separate field.
    quote_size: Decimal
    #: The venue's own `est_fee`, or `None` when it did not state a usable one for this size.
    fee: Decimal | None
    #: The venue's own per-order `fee_ratio`, for `detail` only -- `fee` is never derived from it.
    fee_ratio: Decimal | None
    #: How `quote_size` was arrived at, verbatim into `Preview.detail["cost_basis"]`.
    cost_basis: str
    errors: tuple[str, ...]


class RobinhoodAdapter:
    """Implements the `Broker` port against the Robinhood Crypto Trading API v2.

    v2 exclusively, never v1. Two things force it and both are contract-level, not cosmetic:
    v1's cancel endpoint answers `text/plain` "Cancel request was submitted", which is an
    acknowledgement of a REQUEST and cannot satisfy `cancel_order`'s "return `True` only when the
    venue confirms the cancellation for THIS order id"; and v1 carries neither the per-order
    `fee_charged` that `get_order` needs for observed economics -- and that `get_fee_summary` now
    sums into a real `fees_usd` (#197) -- nor the `fee_tier_status` its rates come from. An
    adapter written against v1 would have to guess at all three, and guessing is the thing the
    port exists to prevent.
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

    def _fee_ratio(self, account: object) -> Decimal | None:
        """The account's fee ratio, or `None` when the venue did not report one.

        `None` is distinct from `Decimal("0")` on purpose and the two must not be collapsed:
        zero is a claim that this account trades free, and nothing in the payload supports that
        claim when the field is simply absent. Callers turn `None` into a zero fee ESTIMATE only
        where they also label the estimate's basis as unknown AND say so in `Preview.errors`.

        `account` is a PARAMETER rather than a `self._account()` call inside this method, which
        is what keeps a single public call to one `GET /accounts/` round trip. It previously
        fetched its own, so `get_fee_summary` -- which also needs the account for
        `thirty_day_volume` -- spent two requests to answer one question, and `preview_order`
        spent one more on top of its `estimated_price` call. Robinhood allows 100 requests/minute
        sustained and this transport does not throttle, so duplicated reads are not free. The
        account is deliberately NOT memoized on the instance: `get_balances` reads `buying_power`
        off the same payload, and a stale buying power would misreport available capital to
        anything sizing an order from it. Per call, not per adapter.
        """
        tier = _field(account, "fee_tier_status") or {}
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

        ⚠️ **`/estimated_price/` is not a preview and this method must never present it as one.**
        It prices a QUANTITY on one side of the book. It does not validate the order, does not
        check buying power, does not check this account's own size or increment bounds, and
        reserves nothing -- an order it prices happily can be rejected the instant it is placed.
        Coinbase's `preview_order` is a broker quote, so approving one is approving the venue's
        arithmetic about THIS order; approving a Robinhood preview is approving an estimate that
        the venue has never been asked to stand behind. `Preview`'s own docstring requires those
        two never to look identical, and `synthetic=True` (with `supports_native_preview=False`)
        is the field that carries the difference. Reading more of the venue's numbers, as this
        method now does, makes the estimate more accurate and changes nothing about that.

        The three fields, and how firm each one actually is:

        * `est_base_size` is exact. All three supported kinds are base-sized, so this is the
          number the caller asked for, not an estimate at all.
        * `est_quote_size` for `limit_gtc`/`stop_limit_gtc` is `base_size * limit_price`. That is
          a BOUND, not a prediction: a limit order does not trade worse than its limit, so this
          is the most quote currency a sell can realise or the most a buy can spend. For
          `market_ioc_base` there is no bound to quote, so it comes from `/estimated_price/` and
          is a genuine guess that the fill can and will miss.
        * `est_fee` is the venue's own `est_fee` when the response carries one, and only
          otherwise `est_quote_size * fee_tier_status.fee_ratio`. Deriving a fee we were handed
          would reproduce a number the response already contains, from an ACCOUNT-level rate
          rather than the rate quoted against this order, and the two can disagree. When the
          account reports no ratio either, `est_fee` is `Decimal("0")` and `detail["fee_ratio"]`
          reads `"unknown"` -- a made-up rate would be worse than a visible zero, because a
          plausible-looking fee is one nobody checks.

        `detail` names every basis rather than leaving it to be inferred: `price_basis` (which
        endpoint or field the unit price came from), `cost_basis` (how `est_quote_size` was
        arrived at, including which reading of `est_total_cost` the venue's numbers supported),
        and `fee_basis` (venue-stated vs account-derived).

        **Every path that could not price the order populates `errors`.** A zero from a failed
        `estimated_price` lookup, or a zero fee from a missing ratio, renders at the confirm gate
        as an order that costs nothing -- which is the single most approvable thing a preview can
        look like, and is indistinguishable from a genuinely free order unless the preview says
        otherwise. `detail` is free-form text a renderer may not show; `errors` is the field the
        port defines for a soft failure, so an unpriced leg has to appear there. The same applies
        to a partially-understood one: an `est_total_cost` that reconciles with nothing is not a
        pricing failure, but it is not a number to put in front of a human unqualified either.

        **The symbol is validated on every path, including the resting-order ones.** They price
        off `spec.limit_price` and have no other reason to call `to_symbol`, which is exactly how
        `ETH-USDC` used to preview cleanly and then raise `UnsupportedOrder` at placement -- after
        the human had already approved it, and with the port forbidding a caller from catching
        that and retrying. A preview must never approve what placement will refuse.

        `GET /accounts/` is fetched only when it is actually needed -- that is, when the venue did
        not state the fee itself. On the market path against a healthy response it is now not
        fetched at all, which halves this method's request count on the one path the executor
        calls while unwinding a position.
        """
        self._reject_unsupported(spec)
        to_symbol(spec.product_id)
        base_size = self._base_size(spec)

        errors: list[str] = []
        estimate: _VenueEstimate | None = None
        if isinstance(spec, LimitGTC | StopLimitGTC):
            price = spec.limit_price
            quote_size = base_size * price
            price_basis, cost_basis = "limit_price", "base_size_x_limit_price"
        else:
            price_basis = "estimated_price"
            estimate = self._estimated_price(spec)
            if estimate is None:
                price = Decimal("0")
                quote_size = Decimal("0")
                cost_basis = "unpriced"
                errors.append(
                    "robinhood returned no usable estimated price for this order; "
                    "est_quote_size and est_fee are NOT priced and must not be read as a cost"
                )
            else:
                price = estimate.price
                quote_size = estimate.quote_size
                cost_basis = estimate.cost_basis
                errors.extend(estimate.errors)

        ratio: Decimal | None
        if estimate is not None and estimate.fee is not None:
            fee, fee_basis, ratio = estimate.fee, "venue_est_fee", estimate.fee_ratio
        else:
            fee_basis = "account_fee_ratio"
            ratio = self._fee_ratio(self._account())
            if ratio is None:
                errors.append(
                    "robinhood reported no fee_tier_status.fee_ratio for this account; est_fee is "
                    "zero because the rate is UNKNOWN, not because this order trades free"
                )
                fee = Decimal("0")
            else:
                fee = quote_size * ratio

        return Preview(
            product_id=spec.product_id,
            side=spec.side,
            est_base_size=base_size,
            est_quote_size=quote_size,
            est_fee=fee,
            synthetic=True,
            detail={
                "price_basis": price_basis,
                # `_render`, not `str`: `str(Decimal("1E-8"))` is `"1E-8"`, and this string is
                # rendered to a human deciding whether to spend money.
                "price": _render(price),
                "cost_basis": cost_basis,
                "fee_basis": fee_basis,
                "fee_ratio": str(ratio) if ratio is not None else "unknown",
            },
            errors=tuple(errors),
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

    def _estimated_price(self, spec: OrderSpec) -> _VenueEstimate | None:
        """Everything `GET /estimated_price/` states about this order, or `None` if it states no
        usable price.

        ⚠️ **The unit price is read from the column named after the side that was ASKED for --
        `ask` for a buy, `bid` for a sell -- and never from `price`.** The venue sends no `price`
        field. This method read one for the whole life of the package, on the strength of the
        documentation, and the first live run against a real credential (#217 F1) proved every
        single market preview came back `est_quote_size = 0.000` with `errors` populated: confirm
        mode was unusable against this venue. The observed row is::

            {'symbol', 'side', 'quantity', 'timestamp',
             'fee_ratio', 'est_fee', 'ask', 'est_total_cost'}

        There is deliberately NO fallback to the other side's column. If a sell is answered with
        only an `ask`, pricing it off that ask overstates the proceeds of an exit -- the exact
        optimistic direction `to_price_side` exists to prevent -- so a row that does not carry the
        requested side is treated as unpriced instead. Silence is recoverable; a flattering number
        at a confirm gate is not.

        **The venue's own `est_fee` and `est_total_cost` are read rather than derived**, but only
        after checking they describe the order that was asked about. Two checks, both of which
        can only be made because the venue sends four related numbers:

        1. `quantity` must be the size that was requested. If the venue echoes a different one,
           its `est_fee` and `est_total_cost` are answers about a different order; scaling them
           would be precisely the "estimate that moves between the quote and the fill" this
           package refuses everywhere else, so only the unit price is used and `errors` says so.
        2. `est_total_cost` must reconcile with `price * quantity`, either exactly (fee-exclusive)
           or offset by `est_fee` in one direction or the other. Robinhood's documentation settles
           none of this, so nothing here assumes: `_reconcile_total` reads the relation off the
           numbers in each response. A total that fits none of the three is reported through
           `errors` rather than quietly priced -- see `_reconcile_total` for why all three
           readings recover the same fee-exclusive notional, and why that is the number
           `Preview.est_quote_size` wants.

        ⚠️ **`est_total_cost` is sent on the ASK side only** (#217 F7). The bid row carries
        `bid`, `quantity`, `fee_ratio` and `est_fee` and simply has no total. That is a complete
        answer, not a degraded one, and the sell path must not treat it as a failure: `price *
        quantity` prices the order and the venue's own `est_fee` sits beside it, so `errors` stays
        empty and `cost_basis` reads `price_x_quantity`. An exit preview that reported a problem
        on every single call is an exit preview nobody would read.

        **`None`, never `Decimal("0")`.** This used to answer zero for "no rows", "unparseable
        price", and "the venue really did quote zero" alike, and `preview_order` had no way to
        tell them apart -- so a pricing FAILURE rendered at the confirm gate as an order that
        costs nothing. That is not a visible nonsense; it is the most approvable thing a preview
        can display. `None` forces the caller to decide, and `preview_order` turns it into a
        populated `Preview.errors` rather than a silent zero. A quoted price of zero is treated
        as unusable for the same reason: nothing on this venue costs nothing. #217 is what proved
        that fix load-bearing rather than theoretical -- it is the only reason a completely
        unpriced preview was survivable at all.

        `quantity` renders through `translate._render`, not `str()` -- `str(Decimal("1E-8"))` is
        `"1E-8"`, and this value goes on a query string the signature is computed over, so an
        exponent here is both a malformed request and a signature mismatch.
        """
        base_size = self._base_size(spec)
        side = to_price_side(spec.side)
        response = self._require_transport().get_estimated_price(
            symbol=to_symbol(spec.product_id), side=side, quantity=_render(base_size)
        )
        rows = _results(response)
        if not rows:
            return None
        row = rows[0]

        price = _decimal_or_none(_field(row, side))
        if price is None or price <= 0:
            return None

        quantity = _decimal_or_none(_field(row, "quantity"))
        if quantity is None or quantity != base_size:
            quoted = "no quantity" if quantity is None else f"quantity {_render(quantity)}"
            return _VenueEstimate(
                price=price,
                quote_size=price * base_size,
                fee=None,
                fee_ratio=None,
                cost_basis="price_x_base_size",
                errors=(
                    f"robinhood's estimated_price row carries {quoted} for a requested size of "
                    f"{_render(base_size)}; its est_fee and est_total_cost describe a different "
                    f"order and are NOT used -- est_quote_size is price x the requested size",
                ),
            )

        fee = _positive_or_none(_decimal_or_none(_field(row, "est_fee")), allow_zero=True)
        ratio = _positive_or_none(_decimal_or_none(_field(row, "fee_ratio")), allow_zero=True)
        total = _positive_or_none(_decimal_or_none(_field(row, "est_total_cost")))
        quote_size, cost_basis, errors = _reconcile_total(price * quantity, total, fee)
        return _VenueEstimate(
            price=price,
            quote_size=quote_size,
            fee=fee,
            fee_ratio=ratio,
            cost_basis=cost_basis,
            errors=errors,
        )

    def place_order(self, spec: OrderSpec) -> PlaceResult:
        """Place a live order. A fresh `client_order_id` per call; the returned `state` is read.

        ⚠️ **A fresh uuid per call means NO placement retry is ever deduplicated.** The id is
        required by this API, and minting one per ATTEMPT is what stops two orders a strategy
        genuinely meant to place twice from collapsing into one. The cost of that choice is the
        opposite failure: a caller that retries after a timeout -- where the first request may
        well have reached the venue -- places a SECOND live order, because the retry carries a
        different id and Robinhood has nothing to match it against. Neither default is safe in
        both directions and the port has no "retry of" concept to disambiguate them, so the
        behaviour is documented rather than silently chosen (README, "must fix before wiring").

        **Two things are checked, not one.** An `id` must come back -- a `success=True` with no
        id is an order nobody can later reconcile or cancel. But the `id` alone is not evidence
        the order is live, and treating it as such was the bug this docstring used to justify:
        Robinhood does NOT signal every rejection with an HTTP error. It answers a rejected order
        on the happy path, 200, with a real order object whose `state` reads `failed`. So a
        protective `StopLimitGTC` could return `{"id": ..., "state": "failed"}` and be recorded as
        a resting stop that does not exist at the venue -- the position it protects running naked,
        with nothing to contradict the belief until the stop fails to fire.

        Only the states Robinhood documents as not-live (`_REJECTED_PLACEMENT_STATES`) are
        rejections. An UNRECOGNISED state resolves to success, which is the opposite of what
        `get_order` does with one, and the asymmetry is deliberate: reporting failure for an order
        that is actually live invites the caller to place it again, and a duplicate live order has
        no recovery, whereas reporting success hands back the id and lets reconciliation poll --
        where `to_port_status` maps the same unknown state to `PENDING` and keeps it observed.
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
        state = str(_field(response, "state", "") or "")
        if state in _REJECTED_PLACEMENT_STATES:
            # `broker_order_id` is None here, matching `CoinbaseAdapter.place_order`'s failure
            # path: a caller that reads a non-None id as "there is a live order to manage" must
            # not be handed one for an order that is not live. The id rides in `reason` so it
            # survives for debugging without being mistaken for a handle on a resting order.
            return PlaceResult(
                success=False,
                broker_order_id=None,
                reason=(
                    f"robinhood returned order {order_id} in state {state!r}: the venue rejected "
                    f"this order, so it is not resting and must not be recorded as placed"
                ),
            )
        return PlaceResult(success=True, broker_order_id=str(order_id))

    def get_fee_summary(self) -> FeeSummary:
        """Map v2's `fee_tier_status` to a `FeeSummary`, with `fees_usd` summed from order history.

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

        **`fees_usd` used to be a hardcoded `Decimal("0")` and is now observed (#197).** That
        constant was not a cosmetic gap. `FeeSummary`'s docstring names subscription lapse
        detection as its consumer, and the contradiction it looks for is a fee charged while the
        user claims a fee-free allowance. A constant zero can never contradict anything, so the
        check did not error against this venue -- it silently PASSED, for every account, every
        time. A rail that always passes is worse than an absent one, because it reads as coverage.

        The v2 API still publishes no account-level fees-paid total, so the number is built the
        only way the API allows: `GET /orders/` filtered to the same trailing 30 days
        `thirty_day_volume` covers, with each order's `fee_charged` summed. See `_fees_paid` for
        the window, the cost, and the two places this total can be wrong.

        **One reading of the clock feeds both the window and `fetched_at`.** Calling `time.time()`
        twice would let the window the fees were summed over drift away from the timestamp the
        summary is reported against, for no benefit -- and a consumer comparing `fees_usd` to
        `volume_usd` has no way to notice that drift.

        ⚠️ **This method can now raise, and that is deliberate.** `_fees_paid` lets a failed or
        non-terminating sweep propagate rather than returning a partial sum. That inverts the rule
        `_account` and `cancel_order` follow ("a raise on the way out of a position can trap it"),
        and the inversion is safe for one specific reason: `get_fee_summary` is a reconciliation
        read, not a step in an unwind. Nothing calls it while a position is being closed. On the
        exit path a raise costs a trapped position; here it costs an error message, and the
        alternative -- an under-reported total -- costs exactly the false negative this issue is
        about.
        """
        # One reading of the clock for the window and the reported timestamp. See the docstring.
        fetched_at = int(time.time())
        # One `GET /accounts/` for the rate and volume: the account is resolved once and passed to
        # `_fee_ratio`, which used to fetch its own and made this two round trips for one answer.
        account = self._account()
        tier = _field(account, "fee_tier_status") or {}
        ratio = self._fee_ratio(account) or Decimal("0")
        return FeeSummary(
            venue=_VENUE,
            taker_rate=ratio,
            maker_rate=ratio,
            volume_usd=Decimal(str(_field(tier, "thirty_day_volume", "0") or "0")),
            fees_usd=self._fees_paid(fetched_at - _FEE_WINDOW_SECONDS),
            volume_window="trailing_30d",
            fetched_at=fetched_at,
        )

    def _fees_paid(self, since: int) -> Decimal:
        """Total `fee_charged` across every order the venue reports touched since `since`.

        This is the whole of #197's fix, and it is worth reading for what it can still get wrong
        as much as for what it does.

        **Window: `updated_at`, not `created_at`, and the choice is load-bearing.** Both filters
        are documented and either would compile. A fee is charged when an execution happens, and
        an execution necessarily bumps `updated_at` -- so the `updated_at_start` result set is a
        SUPERSET of the orders carrying an in-window fee and can never omit one. `created_at_start`
        has no such property: a `StopLimitGTC` resting for forty days and filling this morning was
        created outside the window and charged its fee inside it, so filtering on creation drops a
        real charge. keel rests GTC brackets by design, so that is this engine's normal case
        rather than a corner. Under-reporting is the false negative this issue exists to close, so
        between two imperfect filters the correct one is the one that cannot under-report.

        **Every state is counted, and no `state` filter is sent.** `state` is the obvious-looking
        narrowing and it is a trap: an order that partially fills and is then cancelled ends
        `canceled` while having been charged a real fee on the part that executed, and `filled`
        would drop it. No filter is needed anyway, because `fee_charged` is documented as "the
        total fee amount that was charged for this order based on executed fills" -- the field is
        already its own state filter, reading zero on an order that never traded. A state filter
        layered on top could only remove real charges.

        **`estimated_fee_remaining` is never read.** The neighbouring v2 field is the fee that
        will be charged on an order's UNFILLED remainder, explicitly conditional and explicitly an
        estimate. `fees_usd` is consumed as an observation contradicting a fee-free claim, and an
        estimate cannot honestly contradict anything.

        **Cost: 1 + N requests, where N is the number of history pages in the window.** Against a
        100 req/min sustained limit and a transport with no backoff, the bound matters: the
        account read is one request and the sweep is `RobinhoodTransport._paginate`'s page walk,
        capped at `_MAX_PAGES` (20). So the worst case is 21 requests per call, and the realistic
        case for a keel account trading a handful of times a month is 2. The server-side window
        filter is what keeps that from growing with the account's total age.

        **A sweep that cannot complete RAISES rather than returning what it collected.**
        `_paginate` already raises past `_MAX_PAGES` and this method does not catch it. There is
        no field on `FeeSummary` to mark a total as partial -- `fees_usd` is a bare `Decimal` --
        so a truncated sum is indistinguishable from a complete one and would be consumed as an
        observation. That is the same always-passing false negative in a new costume. An exception
        is visible; a confidently wrong number is not.

        ⚠️ **The one thing this cannot get exactly right: an order straddling the window edge.**
        `fee_charged` is an ORDER-level total, and the v2 `executions[]` rows carry only
        `effective_price`, `quantity` and `timestamp` -- no per-execution fee. So a fee cannot be
        split across the boundary even in principle. An order that filled partially before `since`
        and again after it contributes its WHOLE fee. That over-counts, never under-counts, which
        is the survivable direction: an over-count makes lapse detection point at a fee that was
        genuinely charged, merely slightly earlier than the window claims, whereas an under-count
        hides one entirely.

        ⚠️ **Nor is the window provably identical to the venue's own.** `thirty_day_volume`'s
        boundary is not documented -- it may be calendar-day aligned, it may exclude today -- while
        this window is cut from the local clock. The two match in LENGTH and intent; they are not
        guaranteed to match to the second. `fees_usd` and `volume_usd` are therefore comparable as
        magnitudes over the same nominal window and must not be used to derive an exact effective
        rate.

        A negative `fee_charged` is skipped rather than subtracted. Nothing documents this field
        going negative, so a negative is a rebate or a venue bug -- and under both readings,
        letting it net out a real charge would hide the very thing being looked for.
        """
        started = datetime.fromtimestamp(since, tz=UTC).strftime(_WINDOW_FORMAT)
        rows = _results(self._require_transport().get_orders(updated_at_start=started))

        total = Decimal("0")
        for row in rows:
            # `_decimal_or_none` reads a quoted string and an unquoted number identically, which
            # is required rather than merely tolerant here: this venue mixes the two (#217 F6) and
            # `fee_charged`'s own quoting has never been observed, because observing an order
            # object requires placing a real order and there is no sandbox. The v2 schema types it
            # as an unquoted number and types the neighbouring `executions[].effective_price` as a
            # quoted decimal string, so the documentation does not settle it either.
            fee = _decimal_or_none(_field(row, "fee_charged"))
            if fee is None or fee <= 0:
                continue
            total += fee
        return total

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

        **A venue error also returns `False` rather than propagating.** The transport raises for
        every failure that is not a 404 -- a 5xx, a timeout, a dropped connection -- and this
        method runs on the EXIT path, where `executor._cancel_at_exchange` calls it while
        unwinding a position. This adapter already writes the rule down at `_account`: "a raise on
        the way out of a position can trap it." An exception escaping here can abort an unwind
        partway through and leave both the position and the resting orders it was clearing live,
        which is strictly worse than a `False`.

        Nothing is claimed by swallowing it. The port's contract is already "`True` ONLY when the
        venue CONFIRMS", and an exception is definitionally not a confirmation -- so `False` is
        the same answer this method would give for any other unconfirmed cancel, and it keeps the
        caller believing the order may still be resting, which is the belief that keeps it
        watching. The failure is not hidden either: the order stays in local state as
        possibly-live and the next reconciliation poll re-reads it from the venue.
        """
        transport = self._require_transport()

        try:
            response = transport.cancel_order(order_id)
            if response is None:
                return False
            if _confirms_cancel(response, order_id):
                return True

            polled = transport.get_order(order_id)
        # Intentionally broad. The transport raises whatever the HTTP stack raises -- a
        # `requests` exception, a socket timeout, a JSON decode error -- and narrowing this to a
        # guessed list would let the one unguessed type escape onto the exit path, which is the
        # exact failure this catch exists to prevent. See the docstring for why `False` claims
        # nothing the port does not already treat as "unconfirmed".
        except Exception:
            return False
        if polled is None:
            return False
        return _confirms_cancel(polled, order_id)


#: Robinhood order states that mean a just-placed order is NOT resting at the venue.
#:
#: Robinhood answers a rejected order on the HAPPY HTTP path -- 200, with a real order object
#: whose `state` says it never became live -- so `place_order` cannot infer success from the
#: absence of an HTTP error. Both spellings here come from the v2 docs' order-state enum
#: (https://docs.robinhood.com/crypto/trading/, "Get Orders" `state` filter: `open`, `canceled`,
#: `filled`, `failed`, `pending`), and Robinhood's `canceled` is the American single-`l` spelling
#: -- the port's `CANCELLED` must never be compared against raw venue JSON.
#:
#: Deliberately NOT a denylist of everything unfamiliar: an unrecognised state resolves to
#: success. See `place_order` for why that asymmetry with `get_order` is the safe direction.
_REJECTED_PLACEMENT_STATES: frozenset[str] = frozenset({"failed", "canceled"})

#: Robinhood's own spelling of the terminal cancelled state. Compared against raw venue JSON,
#: so it is the venue's single-`l` spelling and NOT the port's `"CANCELLED"` -- the two meet
#: only in `translate.STATE_TO_PORT_STATUS`, and reading the port's spelling here would silently
#: never match, turning every confirmed cancel into a `False`.
_CANCELED = "canceled"


def _decimal_or_none(value: Any) -> Decimal | None:
    """Parse one JSON leaf as a `Decimal`, or `None` if it is absent or not a number.

    `Decimal(str(value))` handles both shapes this venue produces without a branch: since #194 the
    transport decodes with `parse_float=Decimal`, so an unquoted `64975.78` already arrives as a
    `Decimal` (and `str()` of one round-trips exactly), while a quoted `"64975.78"` arrives as a
    `str`.

    Accepting both is not defensive breadth here, it is required: **this venue mixes the two**
    (#217 F6). `estimated_price` sends every money field unquoted; `trading_pairs` and
    `best_bid_ask` quote every one of theirs; and `accounts` does both at once, sending
    `buying_power` quoted beside an unquoted `fee_tier_status.fee_ratio`. There is no rule to
    branch on, so this does not branch.

    `None` rather than a zero default, everywhere. A zero here would flow into a preview as a real
    price, a real fee, or a real cost, and this whole module is built on the principle that an
    absent number and a zero number must never be the same value.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _positive_or_none(value: Decimal | None, *, allow_zero: bool = False) -> Decimal | None:
    """Drop a value the venue cannot have meant. A negative fee or a zero total is not data."""
    if value is None:
        return None
    if value < 0 or (value == 0 and not allow_zero):
        return None
    return value


def _reconcile_total(
    notional: Decimal, total: Decimal | None, fee: Decimal | None
) -> tuple[Decimal, str, tuple[str, ...]]:
    """Work out what `est_total_cost` MEANS from the venue's own numbers, rather than assuming.

    Robinhood's documentation does not say whether `est_total_cost` includes `est_fee`, so this
    does not choose. The response states `price`, `quantity`, `est_fee` and `est_total_cost`,
    which is one equation with one unknown, and exactly one of three readings fits any
    self-consistent response:

    ============================ ===================================== ======================
    reading                      relation                              fee-exclusive notional
    ============================ ===================================== ======================
    ``est_total_cost``           ``total == notional``                 ``total``
    ``est_total_cost_less_...``  ``total == notional + fee``           ``total - fee``
    ``est_total_cost_plus_...``  ``total == notional - fee``           ``total + fee``
    ============================ ===================================== ======================

    A live ask-side row (#217) satisfies the SECOND reading exactly::

        64975.78 * 0.001 + 0.61726991 == 65.59304991

    so `est_total_cost` is fee-INCLUSIVE there, and assigning it straight into
    `Preview.est_quote_size` would have double-counted the fee at the confirm gate. That is one
    symbol, one side, one moment, and the venue does not send the field on the bid side at all
    (#217 F7) -- which is exactly why the relation is re-derived per response rather than being
    hardcoded now that it is known once. The third reading is not padding either: a BUY's "total
    cost" plausibly adds the fee while a SELL's plausibly nets it out of the proceeds, and if the
    venue ever starts answering `bid` with a total, this is what will read it correctly.

    All three recover the same fee-exclusive notional, which is the number `Preview.est_quote_size`
    is defined to carry (the limit path fills it with `base_size * limit_price`, and `est_fee` is
    a separate field beside it). Assigning a fee-INCLUSIVE `est_total_cost` straight into
    `est_quote_size` would double-count the fee at the confirm gate: once inside the quote size and
    once in `est_fee`. The venue's own arithmetic is still what is returned -- `total`, `total -
    fee`, `total + fee` -- rather than the locally multiplied `notional`, so whatever precision or
    rounding Robinhood applied survives.

    **A total fitting none of the three returns it unchanged AND an error.** That combination is
    the point: refusing to price would degrade an exit preview over a number that is probably
    right, while pricing it silently would put a cost in front of a human with an unverified
    relationship to the order they are approving. The port has a field for exactly this middle
    case, and it is `Preview.errors`.
    """
    # No total is the NORMAL bid-side answer, not an edge case: this venue sends `est_total_cost`
    # on the ask side only (#217 F7). `price * quantity` with the venue's own `est_fee` beside it
    # is a complete estimate, so this returns no error -- every sell preview would carry one
    # otherwise, and an error on every call is an error nobody reads.
    if total is None:
        return notional, "price_x_quantity", ()

    tolerance = max(_TOTAL_TOLERANCE_ABS, abs(notional) * _TOTAL_TOLERANCE_RATIO)
    if abs(total - notional) <= tolerance:
        return total, "est_total_cost", ()
    if fee is not None:
        if abs(total - (notional + fee)) <= tolerance:
            return total - fee, "est_total_cost_less_est_fee", ()
        if abs(total - (notional - fee)) <= tolerance:
            return total + fee, "est_total_cost_plus_est_fee", ()
    return (
        total,
        "est_total_cost_unreconciled",
        (
            f"robinhood's est_total_cost ({total}) matches neither price x quantity "
            f"({notional}) nor that notional offset by est_fee ({fee}); est_quote_size is the "
            f"venue's est_total_cost exactly as sent, and whether it already includes the fee "
            f"is UNVERIFIED -- do not read est_quote_size + est_fee as this order's total",
        ),
    )


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
