"""The Alpaca adapter: `Broker` implemented against Alpaca's Trading + Market Data APIs.

An ORIGINAL implementation against Alpaca's publicly documented API
(https://docs.alpaca.markets/): no code from Alpaca's SDK or any third-party adapter, raw
REST over an injected transport, no `alpaca-py` dependency. Not affiliated with,
endorsed by, or sponsored by Alpaca.

Every Alpaca-specific decision the engine must not know about lives in this package --
order-body and status shape in `translate.py`, hosts/auth/backoff in `transport.py`, and
the sell-side regulatory fee model in `fees.py`. The transport is injected, never
constructed here, so tests exercise the adapter against canned fixtures with zero network
calls. It defaults to `None` so `AlpacaAdapter()` is constructible without credentials:
`capabilities()` is answerable offline, and any method that needs the network raises a
clear error rather than a confusing `AttributeError`.

Scope posture (the PRD's Phase A, FR-1-FR-8, plus the FR-11 rate-limit and host rules):

* **Cash account, long-only, regular session.** No margin, no shorting, no extended
  hours -- `extended_hours: False` is pinned on every order body, and the session's
  open/closed state comes from the venue's own clock (`market_clock`, the port method
  `is_market_open` now delegates to), never a locally maintained calendar that drifts
  (FR-9's posture, wired ahead of the staleness rails).
* **Preview is synthesized** (`synthesizes_preview=True`, `supports_native_preview=
  False`): Alpaca has no preview endpoint, so `preview_order` reads the venue's latest
  quote (best bid/ask, FR-4) and prices the order itself, with the regulatory fee model
  on sells -- the `keel_broker_robinhood` precedent for venues without a native preview.
* **Fee summary is a declared gap.** Alpaca's Trading API publishes no fee tiers, no
  fees-paid total, and no volume window -- the three things a `FeeSummary` would assert
  -- so `supports_fee_summary` is False and `get_fee_summary` raises, exactly as the fake
  venue does for its gap. A fabricated zero rate would read as coverage (the lesson of
  #197, recorded in `keel_broker_robinhood.adapter.get_fee_summary`).
* **Bracket/OCO and stop-market are not declared** because the port's `OrderSpec` has no
  bracket concept and no stop-market kind: keel's stop-loss + take-profit exit legs ride
  as the separate `StopLimitGTC`/`LimitGTC` orders the port already models. This adapter
  does not invent venue-side order kinds the engine cannot ask for; the gap is declared
  here and in the package README rather than papered over.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from keel_broker_api.capabilities import BrokerCapabilities
from keel_broker_api.orders import (
    LimitGTC,
    MarketIOCByQuote,
    OrderSpec,
    StopLimitGTC,
)
from keel_broker_api.port import UnsupportedOrder, resolve_client_order_id
from keel_broker_api.results import (
    Balance,
    CancelOutcome,
    FeeSummary,
    MarketSchedule,
    OrderStatus,
    PlaceResult,
    Preview,
    SessionState,
)
from keel_core.types import Candle, Granularity, Side

from keel_broker_alpaca.fees import estimate_regulatory_fees
from keel_broker_alpaca.translate import (
    _render,
    to_order_body,
    to_port_status,
    to_rfc3339,
    to_symbol,
    to_timeframe,
    to_unix_seconds,
)
from keel_broker_alpaca.transport import (
    SUPPORTED_DATA_FEEDS,
    TRADING_HOSTS,
    AlpacaAPIError,
    Transport,
    _field,
)

_VENUE = "alpaca"

_CAPABILITIES = BrokerCapabilities(
    venue=_VENUE,
    # All four port kinds are declared because Alpaca really serves all four: notional
    # market orders (`market_ioc_quote`), fractional-qty market orders (`market_ioc_base`),
    # GTC limits, and GTC stop-limits. The port has no bracket/OCO or stop-market kind to
    # declare or refuse -- see the module docstring's "Bracket" note.
    supported_orders=frozenset(
        {"market_ioc_quote", "market_ioc_base", "limit_gtc", "stop_limit_gtc"}
    ),
    supports_native_preview=False,
    synthesizes_preview=True,
    supports_fee_summary=False,
    quote_currencies=frozenset({"USD"}),
    asset_classes=frozenset({"equity"}),
    # The regular session binds everything this venue serves (FR-9): weekends and holidays
    # exist here, so the engine must consult `market_clock()` before trading -- unlike the
    # 24/7 crypto venues, whose always-open answer is a constant.
    session_bound=True,
)

#: Alpaca's own page caps a bars query at 10,000 rows per page; twenty pages is already
#: 200k bars (three years of dailies). The cap exists because `next_page_token` is
#: server-controlled: a venue bug handing back a token that never ends must not be able
#: to loop this adapter against a live credential forever.
_MAX_BAR_PAGES = 20

#: Order statuses that mean a just-placed order is NOT live at the venue. Alpaca signals
#: most rejections as HTTP 403/422 (handled in `place_order`), but a 200 response can
#: still carry a terminal status -- recording those as resting would be a live-order
#: hallucination, the exact failure the Robinhood adapter documents for its own
#: happy-path `failed` state.
_PLACEMENT_REJECTED_STATUSES: frozenset[str] = frozenset(
    {"rejected", "canceled", "stopped", "suspended", "expired"}
)

#: The HTTP statuses Alpaca answers an explicit order REFUSAL with: 403 (e.g.
#: insufficient buying power) and 422 (invalid/unsatisfiable order body). Only these
#: become `PlaceResult(success=False)`; any other error propagates, because a 5xx during
#: placement is an UNKNOWN outcome -- mapping it to a refusal would invite a caller to
#: place again while the first order may be live.
_VENUE_REFUSAL_STATUSES: frozenset[int] = frozenset({403, 422})


def _optional_unix_seconds(clock: Any, field: str) -> int | None:
    """One `/v2/clock` schedule field as epoch seconds, or `None` when the venue did not
    send a usable one -- the schedule half of `market_schedule()`'s fail-soft rule. Absent,
    null, non-string and unparseable values all answer `None`; only a real RFC3339 timestamp
    with an explicit offset (`to_unix_seconds`'s own refusal rule) is claimed."""
    raw = _field(clock, field)
    if not isinstance(raw, str):
        return None
    try:
        return to_unix_seconds(raw)
    except ValueError:
        return None


class AlpacaAdapter:
    """Implements the `Broker` port against Alpaca's Trading + Market Data APIs."""

    #: The endpoint and data-feed VOCABULARIES this adapter declares, for the capability
    #: display surfaces (`keel brokers list`, the console's Venues browser, issue #394
    #: C7). Derived from the transport's own maps -- `TRADING_HOSTS`' keys and
    #: `SUPPORTED_DATA_FEEDS` -- so the declared vocabulary is the one the constructor
    #: actually validates against, never a second list; an adapter with no such knobs
    #: (the 24/7 crypto venues) simply does not declare these attributes.
    DECLARED_ENDPOINTS: frozenset[str] = frozenset(TRADING_HOSTS)
    DECLARED_DATA_FEEDS: frozenset[str] = frozenset(SUPPORTED_DATA_FEEDS)

    def __init__(
        self, transport: Transport | None = None, *, endpoint: str = "paper", data_feed: str = "iex"
    ) -> None:
        """`endpoint` ("paper" | "live") and `data_feed` ("iex" | "sip") are validated
        here even when a transport is injected, because they are declared properties of
        the ADAPTER (FR-11's host posture, FR-5's data tier), not implementation details
        of one transport: a configuration mistake should fail at load, not first request.
        """
        if endpoint not in TRADING_HOSTS:
            raise ValueError(f"endpoint must be one of {sorted(TRADING_HOSTS)}, got {endpoint!r}")
        if data_feed not in SUPPORTED_DATA_FEEDS:
            raise ValueError(
                f"data_feed must be one of {sorted(SUPPORTED_DATA_FEEDS)}, got {data_feed!r}"
            )
        self._transport = transport
        self._endpoint = endpoint
        self._data_feed = data_feed

    @property
    def endpoint(self) -> str:
        """The declared environment: "paper" or "live". The live `AlpacaTransport`
        derives its host from this choice, and no adapter-level configuration accepts a
        host URL, so a paper configuration cannot reach the live venue."""
        return self._endpoint

    @property
    def data_feed(self) -> str:
        """The declared market-data tier ("iex" | "sip"), sent on every data request."""
        return self._data_feed

    def _require_transport(self) -> Transport:
        if self._transport is None:
            raise RuntimeError(
                "AlpacaAdapter was constructed without a transport; "
                "inject one to make network-backed calls"
            )
        return self._transport

    def capabilities(self) -> BrokerCapabilities:
        return _CAPABILITIES

    def market_clock(self) -> SessionState:
        """The regular session's state, from the venue's own `/v2/clock` (the port method).

        Equities are not 24/7, and the PRD's session-awareness rule (FR-9) is that a
        weekend or market holiday reads "market closed", never "feed stale". The clock
        endpoint is the source so holidays and half-days come from the venue, not a local
        calendar that drifts.

        **A clock that cannot be read is `CLOCK_UNAVAILABLE`, never an exception and never
        a guess of open** -- fail-closed (FR-9): a transport error, a missing transport, or
        a response that is not a clock are all "unknown session", and trading on an unknown
        session state is precisely what the fail-closed rule exists to prevent. The caller
        decides what to do; this method's answer is the venue's, or an honest "could not
        read".

        **Only an actual boolean `is_open` answers OPEN/CLOSED.** A 2xx body whose
        `is_open` is absent, null, or not a bool is `CLOCK_UNAVAILABLE` too, not CLOSED:
        CLOSED defuses staleness alerting (re-recorded fresh each cycle, so effectively
        forever), and a malformed body must never buy that silence. Unreadable clocks are
        fail-loud for alerting and fail-closed for trading -- the same split the engine's
        session gate applies to `clock_unavailable`.
        """
        try:
            clock = self._require_transport().get_clock()
            if clock is None:
                return SessionState.CLOCK_UNAVAILABLE
            is_open = _field(clock, "is_open")
            if not isinstance(is_open, bool):
                return SessionState.CLOCK_UNAVAILABLE
            return SessionState.OPEN if is_open else SessionState.CLOSED
        except Exception:
            return SessionState.CLOCK_UNAVAILABLE

    def is_market_open(self) -> bool:
        """Phase A's adapter-specific extra, kept answerable and now derived from the port's
        `market_clock()` so the two can never disagree: True only when the venue's own clock
        says the regular session is open. `CLOCK_UNAVAILABLE` reads False here -- fail-closed
        -- so a caller still using the boolean form gets the same posture the port expresses.
        """
        return self.market_clock() is SessionState.OPEN

    def market_schedule(self) -> MarketSchedule:
        """The regular session's state WITH its schedule, from the venue's own `/v2/clock`
        (issue #388 C2, the console session banner's port read).

        `/v2/clock` already carries `next_open`/`next_close` as RFC3339 strings; this is the
        SAME endpoint `market_clock()` reads, with those two fields crossed as epoch ints
        instead of dropped. The state half keeps `market_clock()`'s exact posture -- a
        transport error, a missing transport or a body without a usable boolean `is_open`
        answers `CLOCK_UNAVAILABLE`, never an exception and never a guess -- and an
        unreadable clock claims NO schedule (nulls, not timestamps nobody vouches for).

        The schedule half fails SOFT where the state fails closed: a `next_open`/`next_close`
        that is absent or unparseable degrades to `None` for that field alone, because the
        venue's open/closed answer stands on its own and a data nit in an extra field must
        not launder a readable clock into an unreadable one.
        """
        try:
            clock = self._require_transport().get_clock()
            if clock is None:
                return MarketSchedule(state=SessionState.CLOCK_UNAVAILABLE)
            is_open = _field(clock, "is_open")
            if not isinstance(is_open, bool):
                return MarketSchedule(state=SessionState.CLOCK_UNAVAILABLE)
            return MarketSchedule(
                state=SessionState.OPEN if is_open else SessionState.CLOSED,
                next_open_ts=_optional_unix_seconds(clock, "next_open"),
                next_close_ts=_optional_unix_seconds(clock, "next_close"),
            )
        except Exception:
            return MarketSchedule(state=SessionState.CLOCK_UNAVAILABLE)

    def get_candles(
        self, product_id: str, granularity: Granularity, start_ts: int, end_ts: int
    ) -> list[Candle]:
        """Fetch split-adjusted bars between `start_ts`/`end_ts` (epoch seconds),
        ascending, following the venue's pagination to the end of the window.

        The data tier this adapter was constructed with is sent on every page, and the
        adjustment policy is pinned in `transport.BAR_ADJUSTMENT` so a cached series can
        always state which policy produced it (FR-10's recorded-policy rule).
        """
        timeframe = to_timeframe(granularity)
        symbol = to_symbol(product_id)
        transport = self._require_transport()

        candles: list[Candle] = []
        page_token: str | None = None
        for _ in range(_MAX_BAR_PAGES):
            response = transport.get_bars(
                symbol=symbol,
                timeframe=timeframe,
                start=to_rfc3339(start_ts),
                end=to_rfc3339(end_ts),
                feed=self._data_feed,
                page_token=page_token,
            )
            candles.extend(
                Candle(
                    ts=to_unix_seconds(str(_field(raw, "t"))),
                    open=_decimal_or_none(_field(raw, "o")) or Decimal("0"),
                    high=_decimal_or_none(_field(raw, "h")) or Decimal("0"),
                    low=_decimal_or_none(_field(raw, "l")) or Decimal("0"),
                    close=_decimal_or_none(_field(raw, "c")) or Decimal("0"),
                    volume=_decimal_or_none(_field(raw, "v")) or Decimal("0"),
                )
                for raw in _field(response, "bars", []) or []
            )
            page_token = _field(response, "next_page_token")
            if page_token is None:
                candles.sort(key=lambda c: c.ts)
                return candles
        raise RuntimeError(
            f"alpaca bars pagination did not terminate within {_MAX_BAR_PAGES} pages "
            f"for {symbol!r} at {timeframe!r}; refusing to loop further"
        )

    def get_balances(self) -> list[Balance]:
        """Cash and share balances as domain types, never Alpaca's raw dicts.

        **The USD row surfaces T+1 settlement honestly** (FR-6): `available` is the
        account's `buying_power` clamped at `cash`. On the cash accounts this adapter is
        scoped to (`multiplier == 1`), Alpaca documents `buying_power == cash` -- and when
        they differ, the gap is unsettled proceeds from a T+1 sale, spendable only after
        settlement. Sourcing `available` from `buying_power` reports that spendable figure
        without keel ever simulating settlement itself; clamping at `cash` means a margin
        account (which this adapter does not trade) can never report leveraged buying
        power as spendable either.

        Each position becomes one `Balance` under its symbol, `available` from
        `qty_available` (shares free of holds) and `total` from `qty`. Short rows are
        skipped: keel is long-only by construction, and reconciling a negative quantity
        into rails that never expect one would mis-report the account worse than omitting
        a state the engine cannot act on.
        """
        transport = self._require_transport()
        account = transport.get_account()

        cash = _decimal_or_none(_field(account, "cash")) or Decimal("0")
        buying_power = _decimal_or_none(_field(account, "buying_power")) or Decimal("0")
        balances = [
            Balance(
                currency=str(_field(account, "currency", "USD") or "USD"),
                available=min(buying_power, cash),
                total=cash,
            )
        ]
        for raw in transport.get_positions() or []:
            if str(_field(raw, "side", "long") or "long") != "long":
                continue
            balances.append(
                Balance(
                    currency=str(_field(raw, "symbol")),
                    available=_decimal_or_none(_field(raw, "qty_available")) or Decimal("0"),
                    total=_decimal_or_none(_field(raw, "qty")) or Decimal("0"),
                )
            )
        return balances

    def _reject_unsupported(self, spec: OrderSpec) -> None:
        if spec.kind not in _CAPABILITIES.supported_orders:
            raise UnsupportedOrder(
                f"alpaca does not support order kind {spec.kind!r} "
                f"(supported: {', '.join(sorted(_CAPABILITIES.supported_orders))})"
            )

    def preview_order(self, spec: OrderSpec) -> Preview:
        """Synthesize a preview. Always `synthetic=True` -- Alpaca has no preview
        endpoint, so no number below is a quote the venue stands behind.

        This follows the `keel_broker_robinhood` synthesized-preview precedent: read the
        venue's own latest quote (the book), price the order off the side the order will
        cross -- the ask for a buy, the bid for a sell -- and compute the fee ourselves.

        What is exact versus estimated, field by field:

        * `est_quote_size` for `market_ioc_quote` is the notional itself (the number the
          caller asked to spend); for `limit_gtc`/`stop_limit_gtc` it is
          `base_size * limit_price`, a BOUND rather than a prediction (a limit never
          fills worse than its limit); for `market_ioc_base` it is `base_size *` the
          crossed side of the quote -- a genuine guess the fill can and will miss.
        * `est_base_size` is exact for every base-sized kind; for `market_ioc_quote` it
          is the notional divided by the crossed side of the quote, an estimate.
        * `est_fee` is zero on buys (commission-free, and every pass-through fee this
          venue charges is sell-side) and the `fees.py` regulatory model on sells.

        **Every path that could not price the order populates `errors`**: Alpaca
        documents `ap`/`bp` as 0 when there is no active ask/bid, and a zero side must
        never be divided or multiplied into a size -- a fabricated position at the
        confirm gate is the most approvable thing a preview can display. `detail`
        carries `best_bid`/`best_ask` (feeding the #332 warning and the #350 spread
        gate), the price/cost/fee bases, and the declared data tier.
        """
        self._reject_unsupported(spec)
        symbol = to_symbol(spec.product_id)
        response = self._require_transport().get_latest_quote(symbol, self._data_feed)
        quote = _field(response, "quote") or {}

        bid = _decimal_or_none(_field(quote, "bp"))
        ask = _decimal_or_none(_field(quote, "ap"))
        errors: list[str] = []
        detail: dict[str, str] = {
            "best_bid": _render(bid) if bid is not None and bid > 0 else "none",
            "best_ask": _render(ask) if ask is not None and ask > 0 else "none",
            "data_feed": self._data_feed,
        }

        # The side of the book this order crosses: a buy lifts the ask, a sell hits the
        # bid. The other side is never used to price it -- that would report the wrong
        # side of the spread, optimistic in exactly the direction that flatters a
        # synthesized preview (translate's `to_price_side` rule at Robinhood).
        is_buy = spec.side is Side.BUY
        crossed = ask if is_buy else bid
        price_basis = "latest_quote_ask" if is_buy else "latest_quote_bid"
        side_name = "ask" if is_buy else "bid"

        base_size: Decimal
        quote_size: Decimal
        if isinstance(spec, MarketIOCByQuote):
            # The notional is exact; the share count is derived from the crossed side.
            quote_size = spec.quote_size
            if crossed is None or crossed <= 0:
                base_size = Decimal("0")
                detail["cost_basis"] = "unpriced"
                errors.append(
                    f"alpaca reported no active {side_name} for {symbol}; est_base_size is "
                    "NOT priced and must not be read as a position size"
                )
            else:
                base_size = quote_size / crossed
                detail["cost_basis"] = "notional_over_quote"
        elif isinstance(spec, LimitGTC | StopLimitGTC):
            base_size = spec.base_size
            quote_size = spec.base_size * spec.limit_price
            price_basis = "limit_price"
            detail["cost_basis"] = "base_size_x_limit_price"
        else:
            base_size = spec.base_size
            if crossed is None or crossed <= 0:
                quote_size = Decimal("0")
                detail["cost_basis"] = "unpriced"
                errors.append(
                    f"alpaca reported no active {side_name} for {symbol}; est_quote_size and "
                    "est_fee are NOT priced and must not be read as a cost or a proceeds figure"
                )
            else:
                quote_size = base_size * crossed
                detail["cost_basis"] = "base_size_x_quote"

        detail["price_basis"] = price_basis
        if price_basis.startswith("latest_quote") and crossed is not None and crossed > 0:
            detail["price"] = _render(crossed)
        else:
            detail["price"] = "unpriced"

        total_fee, sec_fee, taf = estimate_regulatory_fees(spec.side, base_size, quote_size)
        detail["fee_basis"] = (
            "sell_side_regulatory_passthrough" if not is_buy else "commission_free_buy"
        )
        detail["commission"] = "0"
        detail["sec_fee"] = _render(sec_fee)
        detail["taf"] = _render(taf)

        return Preview(
            product_id=spec.product_id,
            side=spec.side,
            est_base_size=base_size,
            est_quote_size=quote_size,
            est_fee=total_fee,
            synthetic=True,
            detail=detail,
            errors=tuple(errors),
        )

    def place_order(self, spec: OrderSpec, *, idempotency_key: str | None = None) -> PlaceResult:
        """Place a live order, with the venue's explicit refusals mapped to a failed
        `PlaceResult`.

        **`idempotency_key` removes the retry hazard** (#409). Omitted, the id is minted per
        ATTEMPT and a caller retrying after a timeout may place twice, because the retry
        carries a different id and Alpaca has nothing to match it against. Supplied, every
        attempt under that key resolves to one `client_order_id`. Alpaca accepts a string of
        up to 128 characters here, but `resolve_client_order_id` hashes to a UUID for every
        venue alike so one caller key means one order wherever it is routed.

        **Alpaca answers rejections as HTTP errors, unlike Robinhood's happy-path failed
        state.** 403 (insufficient buying power) and 422 (invalid body) are the venue
        saying "no" to THIS order, so they become `PlaceResult(success=False, reason=...)`
        -- but every other error propagates: a 5xx or a timeout during placement is an
        UNKNOWN outcome, and mapping it to a refusal would read as "safe to try again"
        while the first order may be live at the venue.

        A 200 response whose status is terminal (`_PLACEMENT_REJECTED_STATUSES`) is a
        not-live order, not a placed one -- recorded as failure with the id in `reason`,
        never handed back as a handle on a resting order.
        """
        self._reject_unsupported(spec)
        body = to_order_body(spec, client_order_id=resolve_client_order_id(idempotency_key))
        try:
            response = self._require_transport().create_order(body)
        except AlpacaAPIError as exc:
            if exc.status_code in _VENUE_REFUSAL_STATUSES:
                return PlaceResult(success=False, broker_order_id=None, reason=exc.message)
            raise

        order_id = _field(response, "id")
        if order_id is None:
            return PlaceResult(
                success=False,
                broker_order_id=None,
                reason="alpaca accepted the request but returned no order id",
            )
        status = str(_field(response, "status", "") or "")
        if status in _PLACEMENT_REJECTED_STATUSES:
            return PlaceResult(
                success=False,
                broker_order_id=None,
                reason=(
                    f"alpaca returned order {order_id} in status {status!r}: the venue "
                    "rejected this order, so it is not resting and must not be recorded as placed"
                ),
            )
        return PlaceResult(success=True, broker_order_id=str(order_id))

    def get_fee_summary(self) -> FeeSummary:
        """Alpaca's Trading API publishes nothing a `FeeSummary` would assert: no fee
        tiers (commission is a flat zero), no fees-paid total, and no volume window.
        Declaring the gap and refusing is the honest answer -- the `FakeAdapter`
        precedent -- where a fabricated `fees_usd=0` would read as coverage. The fee
        honesty this venue needs lives in `preview_order` via `fees.py`."""
        raise NotImplementedError("alpaca's trading API reports no fee or volume summary")

    def get_order(self, order_id: str) -> OrderStatus:
        """Observed state of a previously placed order, money fields as `Decimal`.

        `total_fees` is always zero, and that is a statement about the API, not a claim
        that orders trade free: Alpaca's order object carries no fee field -- sell-side
        regulatory fees are netted from proceeds and only surface account-wide
        (`pending_reg_taf_fees` on the account). Zero here means "not observable per
        order", and the preview's `est_fee` carries the modelled cost instead.

        An id the venue does not recognise comes back as a normal `OrderStatus` with
        status `FAILED` and zeroed money, never an exception -- `OrderStatus`'s contract
        is arithmetic without special-casing, and the 404-to-`None`-to-FAILED split
        (transport-to-adapter) keeps a network blip from ever becoming "order gone".
        """
        response = self._require_transport().get_order(order_id)
        if response is None:
            return _terminal_unknown(order_id)
        return OrderStatus(
            order_id=str(_field(response, "id", order_id) or order_id),
            status=to_port_status(_field(response, "status")),
            filled_size=_decimal_or_none(_field(response, "filled_qty")) or Decimal("0"),
            average_filled_price=_decimal_or_none(_field(response, "filled_avg_price"))
            or Decimal("0"),
            total_fees=Decimal("0"),
        )

    def cancel_order(self, order_id: str) -> CancelOutcome:
        """Cancel one resting order and report what the venue said.

        Alpaca is the venue this is simplest at: DELETE /v2/orders/{id} answers 204 No Content
        on confirmation -- a status about the ORDER, not an acknowledgement of the request (the
        Robinhood v1 text-ack failure the port exists to prevent). So Alpaca never produces
        `ACCEPTED`: its answer is always about the order itself.

        404 ("order not found") and 422 ("order status is not cancelable", e.g. already filled)
        are the venue declining, which is `REFUSED` -- both come back as statuses rather than
        raising, precisely so this method can tell them apart from a failure.

        **A transport failure is `UNKNOWN`, not a raise.** This runs on the executor's exit path
        while unwinding a position; an exception escaping here can abort the unwind partway and
        leave the position and its resting orders live. `UNKNOWN` keeps the engine believing the
        order may still be resting -- the belief that keeps it watching -- and the reconciliation
        poll re-reads the order from the venue either way.
        """
        try:
            status = int(self._require_transport().cancel_order(order_id))
        except Exception:
            return CancelOutcome.UNKNOWN
        if status == 204:
            return CancelOutcome.CONFIRMED
        if status in (404, 422):
            return CancelOutcome.REFUSED
        # The transport raises for every other 4xx/5xx, so this is a status it was told to pass
        # through and we have no mapping for. Claiming nothing is the only honest answer.
        return CancelOutcome.UNKNOWN


def _decimal_or_none(value: Any) -> Decimal | None:
    """Parse one JSON leaf as a `Decimal`, or `None` if absent or not a number.

    The venue mixes quoted (`"131.56"`) and unquoted (`131.56`) money fields -- the
    transport already parses unquoted numbers as `Decimal`, and `Decimal(str(value))`
    here lands both shapes on the same exact number. `None` rather than zero: an absent
    number and a zero number must never be the same value at a preview gate.

    Non-finite values are `None` too, and the check is explicit because the `except`
    below never fires for them: JSON's `NaN`/`Infinity` tokens arrive via
    `parse_constant` (not `parse_float`) as `float("nan")`/`float("inf")`, and
    `Decimal(str(...))` parses BOTH without raising -- `Decimal("NaN")` then crashes any
    ordering comparison (`bid > 0`, `min(buying_power, cash)`) and `Decimal("Infinity")`
    compares as a real price. A non-finite number is not a money value; refusing it here
    is what keeps the preview's "every path that could not price the order populates
    `errors`" invariant true for these rows too.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not parsed.is_finite():
        return None
    return parsed


def _terminal_unknown(order_id: str) -> OrderStatus:
    """The answer for an id the venue does not recognise: `FAILED`, money zeroed."""
    return OrderStatus(
        order_id=order_id,
        status="FAILED",
        filled_size=Decimal("0"),
        average_filled_price=Decimal("0"),
        total_fees=Decimal("0"),
    )


__all__ = ["AlpacaAdapter"]
