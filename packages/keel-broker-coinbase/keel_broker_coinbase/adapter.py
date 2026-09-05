"""The Coinbase Advanced Trade adapter: `Broker` implemented against Coinbase's REST API.

Every Coinbase-specific decision the engine must not know about lives in this package --
order-configuration shape in `translate.py`, response-probing in `transport.py`, and the
capability declaration below.

The transport is injected, never constructed here, so tests exercise the adapter against canned
fixtures with zero live network calls. It defaults to `None` so `CoinbaseAdapter()` is
constructible without credentials -- `capabilities()` is answerable offline, and any method that
actually needs the network raises a clear error rather than a confusing `AttributeError`.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Any

from keel_broker_api.capabilities import BrokerCapabilities
from keel_broker_api.orders import OrderSpec
from keel_broker_api.port import (
    TradeScopeDenied,
    UnsupportedOrder,
    default_market_schedule,
    resolve_client_order_id,
)
from keel_broker_api.results import (
    Balance,
    CancelOutcome,
    FeeSummary,
    Instrument,
    MarketSchedule,
    OrderStatus,
    PlaceResult,
    Preview,
    SessionState,
)
from keel_core.telemetry import log_event, log_venue_failure
from keel_core.types import Candle, Granularity

from keel_broker_coinbase.translate import to_order_configuration
from keel_broker_coinbase.transport import Transport, _field

_CAPABILITIES = BrokerCapabilities(
    venue="coinbase",
    # `bracket_gtc` is declared because Coinbase Advanced Trade natively serves it:
    # `order_configuration.trigger_bracket_gtc` is one order carrying both exits, and the venue
    # disables the losing side when the other fills. It is the only venue keel targets that does.
    supported_orders=frozenset(
        {"market_ioc_quote", "market_ioc_base", "limit_gtc", "stop_limit_gtc", "bracket_gtc"}
    ),
    supports_native_preview=True,
    synthesizes_preview=False,
    supports_fee_summary=True,
    quote_currencies=frozenset({"USD", "USDC"}),
    asset_classes=frozenset({"spot"}),
    session_bound=False,
    # #372: this adapter spends spot balances only -- the Coinbase surface it speaks has
    # no borrowing path to declare.
    cash_only=True,
)


def _candle_from_raw(raw: object) -> Candle:
    return Candle(
        ts=int(_field(raw, "start")),
        open=Decimal(_field(raw, "open")),
        high=Decimal(_field(raw, "high")),
        low=Decimal(_field(raw, "low")),
        close=Decimal(_field(raw, "close")),
        volume=Decimal(_field(raw, "volume")),
    )


#: Coinbase's own predicate for "this key lacks the Trade scope", copied from the SDK rather
#: than inferred from documentation. `coinbase.rest.rest_base.handle_exception` branches on
#: `status_code == 403 and '"error_details":"Missing required scopes"' in response.text` AHEAD of
#: every other 4xx and rewrites the message to name the permissions. A hard-coded branch in the
#: venue's own client is the strongest evidence available offline that this is the shape the
#: venue sends, and #233's design is explicit that the classification must not be invented from
#: docs -- "one more reason the design must not pre-classify it from documentation".
#:
#: Matched case-insensitively on the body so a change in the SDK's message wording, or a caller
#: that re-raises with its own text, does not silently stop the record's second writer.
_MISSING_SCOPES = "missing required scopes"


def _trade_scope_refusal(exc: BaseException) -> str | None:
    """The venue's own words when `exc` is a CREDENTIAL-scope refusal, else `None`.

    Total on any input, and deliberately narrow. Three separate gates have to agree before this
    answers anything but `None`:

    1. the error carries a response object at all -- a timeout, a DNS failure or a socket reset
       has none, and those are facts about the network, not the credential;
    2. its status is exactly `403` -- a 5xx is an UNKNOWN outcome, and a 401 is a credential the
       venue does not recognise at all (whose fix is a new key, not an attestation);
    3. the body carries Coinbase's `Missing required scopes`.

    Gate 3 is what makes this safe on the live deployment, and it is not belt-and-braces on gate
    2. Coinbase answers `403 PERMISSION_DENIED` for PRODUCT entitlement as well -- observed live
    on 2026-08-05 against a futures product on a portfolio not onboarded for FCM
    (`docs/experiments/2026-08-05-coinbase-asset-class-feasibility.md`). That 403 says nothing
    about the key's scopes, and classifying it as one would write `REFUTED`, veto every live
    ENTRY through rail 20, and require an operator at a terminal to clear it -- an outage
    manufactured out of a fact about an asset class keel does not trade.

    ⚠️ **A residual this cannot see, recorded deliberately.** It only ever inspects an EXCEPTION.
    Advanced Trade also answers HTTP 200 with `success: false` and an `error_response` for a
    class of order errors, and `place_order` maps those to `PlaceResult(success=False)` without
    consulting this function -- so a scope refusal that ever arrived that way would be reported
    as an ordinary rejected order and would never refute the record. That is the design's stated
    trade: `error_response.error` is an open enum whose values are not documented as stable, and
    pre-classifying an unobserved body from documentation is exactly what #233 forbids. The cost
    of missing such a refusal is one more refused order; the cost of inventing one is an outage.

    Shape-typed, not `isinstance`-typed: the exception is a `requests.HTTPError` raised by
    `coinbase-advanced-py`, and `requests` is a TRANSITIVE dependency this package has never
    declared. Reaching past the SDK to import it would couple the adapter to a library it does
    not depend on, for a check two `getattr`s already answer.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None
    if getattr(response, "status_code", None) != 403:
        return None
    body = str(getattr(response, "text", "") or "")
    if _MISSING_SCOPES not in body.lower():
        return None
    # The venue's own words, verbatim -- they land in `venue_trade_scopes.refuted_reason` and are
    # what `doctor` and `keel scope show` read back to the operator. The body is preferred over
    # `str(exc)` because the body is what Coinbase said; the message is what the SDK made of it.
    return body


#: Coinbase's international/perpetuals portfolio type. `DEFAULT` and `CONSUMER` are the spot
#: portfolios a probe of a live spot-only account returned on 2026-09-02; `INTX` is the one that
#: means derivatives are available on this account.
logger = logging.getLogger(__name__)

_DERIVATIVE_PORTFOLIO_TYPES = frozenset({"INTX"})


class CashAccountRequired(RuntimeError):
    """This account has derivative capability keel is not scoped to (#666).

    Named to match `keel_broker_alpaca.CashAccountRequired` because `_build_broker` treats both
    the same way and an operator meeting one should recognise the other. The two are raised on
    DIFFERENT evidence and with different confidence -- see `verify_cash_account` -- and the
    difference is a property of the venues, not of the engine.
    """


class CoinbaseAdapter:
    """Implements the `Broker` port against Coinbase Advanced Trade."""

    #: Where this adapter's credentials live (#233 PR4), for the capability-display readiness
    #: surfaces (`keel brokers list`, `/api/venues`) -- matches
    #: `keel_core.config.load_secrets`'s own names, read with `getattr` and never imported: a
    #: second list keyed by venue name would be the thing #233's display exists to avoid.
    DECLARED_CREDENTIAL_ENV: tuple[str, str] = ("CDP_API_KEY", "CDP_API_SECRET")

    @property
    def volume_feed_id(self) -> str:
        """Which feed this adapter's candles come from, for `candle_series_feed` (#696).

        Coinbase serves one feed: its own book. That is DECLARED here rather than assumed,
        because keel's liquidity floor was calibrated against exactly this number -- a crypto
        exchange's own reported volume -- and the claim "venue volume is market volume for this
        purpose" should be written down somewhere a reader can find it, not left as the silent
        default it used to be.
        """
        return "coinbase"
    def __init__(self, transport: Transport | None = None) -> None:
        self._transport = transport

    def _require_transport(self) -> Transport:
        if self._transport is None:
            raise RuntimeError(
                "CoinbaseAdapter was constructed without a transport; "
                "inject one to make network-backed calls"
            )
        return self._transport

    def capabilities(self) -> BrokerCapabilities:
        return _CAPABILITIES

    def market_clock(self) -> SessionState:
        """Crypto trades 24/7: `SessionState.OPEN` as a constant, with no transport call.

        FR-9's 24/7 half -- this venue has no session to consult, and answering from the
        transport would spend a request to learn nothing (this deliberately never touches
        `self._transport`, which is why it works on a credential-less adapter too).
        """
        return SessionState.OPEN

    def market_schedule(self) -> MarketSchedule:
        """The port's DEFAULT schedule read, verbatim (issue #388 C2): this venue's clock
        answer -- the constant `OPEN` -- with NO next_open/next_close claimed. A 24/7 market
        has no calendar to carry, so synthesizing timestamps would be inventing the
        locally-maintained calendar FR-9 forbids; `default_market_schedule(self)` is the one
        shared derivation, not a copy of it.
        """
        return default_market_schedule(self)

    def get_candles(
        self, product_id: str, granularity: Granularity, start_ts: int, end_ts: int
    ) -> list[Candle]:
        """Fetch candles between `start_ts`/`end_ts` (epoch seconds), ascending."""
        response = self._require_transport().get_candles(
            product_id=product_id,
            start=str(start_ts),
            end=str(end_ts),
            granularity=granularity.value,
        )
        candles = [_candle_from_raw(raw) for raw in _field(response, "candles", []) or []]
        candles.sort(key=lambda c: c.ts)
        return candles

    def get_balances(self) -> list[Balance]:
        """Return per-currency balances as domain types, never Coinbase's account dicts.

        Coinbase reports `available_balance` and a separate `hold`; `total` is their sum, since
        the API exposes no single "total" field.
        """
        response = self._require_transport().get_accounts()
        balances: list[Balance] = []
        for raw in _field(response, "accounts", []) or []:
            available = Decimal(_field(_field(raw, "available_balance") or {}, "value", "0"))
            hold = Decimal(_field(_field(raw, "hold") or {}, "value", "0"))
            balances.append(
                Balance(
                    currency=_field(raw, "currency"),
                    available=available,
                    total=available + hold,
                )
            )
        return balances

    def _reject_unsupported(self, spec: OrderSpec) -> None:
        if spec.kind not in _CAPABILITIES.supported_orders:
            raise UnsupportedOrder(f"coinbase does not support order kind {spec.kind!r}")

    def get_instrument(self, product_id: str) -> Instrument | None:
        """One product's `base_increment`, read from Coinbase's per-product endpoint.

        `get_product` rather than `get_products`: the caller
        (`executor._base_increment_for`) needs ONE product and caches ONE, and asking the venue
        for the whole catalogue -- about 900 rows -- inside the order-placement path to use a
        single field of it is the wrong shape. The transport has carried `get_product` since
        before this method existed.

        `None` for a product this venue does not list, or whose `base_increment` is missing,
        unparseable or non-positive. All four are the same fact to a caller -- no usable
        granularity -- and none of them is an error worth raising on: a product id comes from an
        operator's allowlist and may simply not be listed here.
        """
        response = self._require_transport().get_product(product_id)
        raw = _field(response, "product", response)
        increment = _field(raw, "base_increment")
        if increment is None:
            return None
        try:
            value = Decimal(str(increment))
        except ArithmeticError, TypeError, ValueError:
            return None
        if value <= 0:
            return None
        return Instrument(product_id=product_id, base_increment=value)

    def list_products(self, product_type: str = "SPOT") -> list[dict[str, Any]]:
        """Every tradable product on the venue, as plain dicts. READ-ONLY market metadata.

        A Coinbase-extra, not a port method, on purpose (#524): the port's catalogue surface is
        the per-product `get_instrument` above, which is what the ORDER path reads; a ~900-row
        catalogue sweep is a DISCOVERY concern (`keel assets discover`), and elevating it to the
        port would hand every adapter a bulk endpoint only the discovery tool wants. Used only
        by the allowlist DISCOVERY stage, which proposes candidates for human attestation -- it
        decides nothing. Per §5's asymmetry, a proposal may come from anywhere; admission goes
        through `compliance/screen.py`.

        `base_increment`/`quote_increment` ride along as the venue's own strings because the
        sweep surfaces them to the operator; the caller decides what is a Decimal.
        """
        raw = self._require_transport().get_products(product_type=product_type)
        products = raw["products"] if isinstance(raw, dict) else raw.products
        out: list[dict[str, Any]] = []
        for product in products:
            fields = product if isinstance(product, dict) else vars(product)
            out.append(
                {
                    "product_id": fields.get("product_id"),
                    "base_name": fields.get("base_name"),
                    "quote_currency_id": fields.get("quote_currency_id"),
                    "status": fields.get("status"),
                    "trading_disabled": bool(fields.get("trading_disabled")),
                    "is_disabled": bool(fields.get("is_disabled")),
                    "view_only": bool(fields.get("view_only")),
                    "quote_24h_volume": fields.get("approximate_quote_24h_volume"),
                    # #516: the venue has always sent these, and the sweep surfaces them so an
                    # operator can eyeball a product's granularity before fetching anything.
                    "base_increment": fields.get("base_increment"),
                    "quote_increment": fields.get("quote_increment"),
                }
            )
        return out

    def preview_order(self, spec: OrderSpec) -> Preview:
        """Preview via Coinbase's own endpoint -- hence `synthetic=False`."""
        self._reject_unsupported(spec)
        # #233: classified HERE and not only in `place_order`, because on THIS venue preview is a
        # real call under the same scope and the executor previews first. A key with View but not
        # Trade is refused at this line and `place_order` is never reached -- so classifying only
        # placement would ship a gate that cannot fire on the one venue this deployment trades.
        try:
            response = self._require_transport().preview_order(
                product_id=spec.product_id,
                side=spec.side.value,
                order_configuration=to_order_configuration(spec),
            )
        except Exception as exc:
            refusal = _trade_scope_refusal(exc)
            if refusal is None:
                raise
            raise TradeScopeDenied(refusal) from exc
        errs = tuple(str(e) for e in (_field(response, "errs", []) or []))
        return Preview(
            product_id=spec.product_id,
            side=spec.side,
            est_base_size=Decimal(_field(response, "base_size", "0")),
            est_quote_size=Decimal(_field(response, "quote_size", "0")),
            est_fee=Decimal(_field(response, "commission_total", "0")),
            synthetic=False,
            detail={
                key: str(value)
                for key in ("best_bid", "best_ask", "order_total")
                if (value := _field(response, key)) is not None
            },
            errors=errs,
        )

    def place_order(self, spec: OrderSpec, *, idempotency_key: str | None = None) -> PlaceResult:
        """Place a live order.

        ⚠️ The previous wording here -- "a fresh `client_order_id` per call gives Coinbase
        idempotency" -- was backwards, and #409 corrects it. A fresh id per call gives the venue
        nothing to deduplicate ON: idempotency is exactly what a per-ATTEMPT id withholds. Pass
        `idempotency_key` to get it; omit it and two attempts are two orders, which stays the
        default for the reasons `resolve_client_order_id` sets out.
        """
        self._reject_unsupported(spec)
        client_order_id = resolve_client_order_id(idempotency_key)
        try:
            response = self._require_transport().create_order(
                client_order_id=client_order_id,
                product_id=spec.product_id,
                side=spec.side.value,
                order_configuration=to_order_configuration(spec),
            )
        except Exception as exc:
            # #233: the venue's half of the trade-scope record. ONLY a `Missing required scopes`
            # 403 becomes `TradeScopeDenied` -- see `_trade_scope_refusal` for the three gates
            # and why the near-miss 403 this venue really sends must not trip it. Everything
            # else re-raises unchanged, so a 5xx or a timeout reaches the executor exactly as it
            # did before and touches the record not at all.
            refusal = _trade_scope_refusal(exc)
            if refusal is None:
                raise
            raise TradeScopeDenied(refusal) from exc
        success = bool(_field(response, "success", False))
        if success:
            success_response = _field(response, "success_response") or {}
            return PlaceResult(
                success=True,
                broker_order_id=_field(success_response, "order_id"),
                client_order_id=client_order_id,
            )
        error_response = _field(response, "error_response") or {}
        reason = _field(error_response, "message") or _field(error_response, "error")
        return PlaceResult(
            success=False, broker_order_id=None, reason=reason, client_order_id=client_order_id
        )

    def verify_cash_account(self) -> None:
        """Refuse an account with derivative capability. **Refutes only; never proves (#666).**

        Coinbase exposes NO cash-vs-margin field for spot. A probe of the live account on
        2026-09-02 established what it does expose: every margin/borrow/leverage/liquidation
        field in the SDK lives in `futures_types`, `perpetuals_types` or the derivative order
        fields, and `margin_rate` on the transaction summary is present-and-NULL -- it is in the
        response schema for every account, so its presence signals nothing at all.

        What IS unambiguous is the portfolio list. `DEFAULT` and `CONSUMER` are the spot
        portfolios; `INTX` is the international/perpetuals one, and its presence is derivative
        capability on this account. That is the one signal strong enough to refuse on.

        **A pass means NO CONTRADICTION WAS FOUND -- it is never proof of a cash posture, and a
        future reader must not take it as one.** There is no affirmative flag to read, so the
        residual unknown stays with the operator's attestation on rail 17's pattern: venue
        evidence can refute an attestation, it cannot issue one.

        ⚠️ **Passes on an unreadable response, which is the OPPOSITE of
        `keel_broker_alpaca.verify_cash_account` and is deliberate.** Alpaca fails closed
        because `multiplier` IS the classification, so a readable answer is definitive and
        silence is a distinct third state worth refusing on. Here the check can only refute, so
        an unreadable response proves nothing a readable one would not also have failed to
        prove -- and failing closed would refuse a compliant deployment on a network blip while
        establishing nothing. A gate that fires on the compliant case is the gate that gets
        disabled in anger.

        One `get_portfolios` request per broker construction, alongside Alpaca's one
        `/v2/account` read, well inside the venue's rate budget at this cadence.
        """
        try:
            response = self._require_transport().get_portfolios()
            portfolios = _field(response, "portfolios", []) or []
            found = sorted(
                {
                    str(_field(p, "type", "") or "").upper()
                    for p in portfolios
                    if str(_field(p, "type", "") or "").upper() in _DERIVATIVE_PORTFOLIO_TYPES
                }
            )
        except Exception:
            # Every failure is the same answer: no contradiction found. See the docstring --
            # this must not become a refusal, and it must not raise into broker construction.
            log_venue_failure(logger, "coinbase.portfolio_posture_unreadable")
            return
        if found:
            raise CashAccountRequired(
                f"this Coinbase account holds a {', '.join(found)} portfolio -- derivatives are "
                "available on it, and keel is spot-only by charter (rails 18/19). Trade from an "
                "account without one, or remove the portfolio. keel cannot verify the reverse: "
                "Coinbase exposes no cash-account flag for spot, so the absence of this "
                "portfolio is not proof of a cash posture and never will be."
            )

    def get_fee_summary(self) -> FeeSummary:
        """Map Coinbase's `transaction_summary` to a `FeeSummary`.

        `volume_window` is `"unknown"` deliberately. Coinbase's documentation does not state
        whether `advanced_trade_only_volume` is trailing-30-day or calendar-month, and the honest
        declaration is the one that stops a caller comparing it against a calendar-month cap.
        The subscription spec's §10 tracks confirming this against a live account.
        """
        response = self._require_transport().get_transaction_summary()
        # #666: surfaced HERE rather than in `verify_cash_account`, because this call already
        # fetches the response -- so the warning costs no extra request. A WARNING and not a
        # refusal: `margin_rate` is present-and-null on a spot-only account, so presence is not
        # a signal, and a NON-null value is plausibly one but could not be verified against a
        # margin-enabled account. Shipping an untested refusal branch on a compliance gate is
        # how a gate ends up firing on the compliant case.
        if _field(response, "margin_rate") is not None:
            log_event(
                logger,
                logging.WARNING,
                "coinbase.margin_rate_present",
                detail=(
                    "this account's transaction summary carries a non-null `margin_rate`, "
                    "which may mean margin is available on it. keel does not refuse on this "
                    "because the signal is unverified -- confirm the account's posture and "
                    "your rail 17 attestation"
                ),
            )
        fee_tier = _field(response, "fee_tier") or {}
        return FeeSummary(
            venue="coinbase",
            taker_rate=Decimal(_field(fee_tier, "taker_fee_rate", "0")),
            maker_rate=Decimal(_field(fee_tier, "maker_fee_rate", "0")),
            volume_usd=Decimal(str(_field(response, "advanced_trade_only_volume", "0"))),
            fees_usd=Decimal(str(_field(response, "advanced_trade_only_fees", "0"))),
            volume_window="unknown",
            fetched_at=int(time.time()),
        )

    def get_order(self, order_id: str) -> OrderStatus:
        """Observed state of a previously placed order, normalized to `Decimal` money fields.

        This is what makes exit reconciliation possible at all. A placement response only says
        the order was ACCEPTED; nothing in it reveals that a resting bracket later filled, at
        what price, or for what fee. Without this the executor has to record the *expected*
        price and the *previewed* commission, so realized P&L is modelled rather than observed --
        and a stop-out closes a position the loop never notices.

        `filled_size`/`average_filled_price`/`total_fees` are absent (not zero) on an order with
        no fills yet, so they are defaulted to `Decimal("0")`: callers do arithmetic on these and
        should never have to special-case `None`. `status` is passed through verbatim -- see
        `execution.reconcile` for the values that matter.
        """
        response = self._require_transport().get_order(order_id=order_id)
        order = _field(response, "order") or {}
        return OrderStatus(
            order_id=_field(order, "order_id", order_id),
            status=_field(order, "status"),
            filled_size=Decimal(_field(order, "filled_size", "0") or "0"),
            average_filled_price=Decimal(_field(order, "average_filled_price", "0") or "0"),
            total_fees=Decimal(_field(order, "total_fees", "0") or "0"),
        )

    def cancel_order(self, order_id: str) -> CancelOutcome:
        """Cancel one resting order and report what the exchange said about THIS id.

        Coinbase's `batch_cancel` reports success per order, so a 200 response does not mean the
        order is gone -- it can come back `{"success": false, "failure_reason": ...}` for an
        order that already filled or does not exist. Reading only the HTTP status would let
        `executor._cancel_at_exchange` record a cancel that never happened, which is precisely
        the failure this mapping exists to prevent.

        `success: true` is `CONFIRMED` and `success: false` is `REFUSED` -- the venue answering
        about this order either way.

        An answer with NO row for this id is `UNKNOWN` rather than `REFUSED`, and that is the one
        behavioural change here. It was `False` before, which fails closed identically; the
        difference is only what gets logged, and "the exchange did not answer about this order"
        is a different operational fact from "the exchange refused". Neither permits acting on
        the cancel.
        """
        response = self._require_transport().cancel_orders(order_ids=[order_id])
        results = list(_field(response, "results", []) or [])
        for result in results:
            if _field(result, "order_id") == order_id:
                return _cancel_outcome_from_success(_field(result, "success", False))
        # Some responses omit the echoed id; a single result for a single-id request is it.
        if len(results) == 1:
            return _cancel_outcome_from_success(_field(results[0], "success", False))
        return CancelOutcome.UNKNOWN


def _cancel_outcome_from_success(success: object) -> CancelOutcome:
    """Coinbase states the outcome per order, so its answer is never merely an acknowledgement:
    `success` is about the order and maps straight onto confirmed/refused."""
    return CancelOutcome.CONFIRMED if bool(success) else CancelOutcome.REFUSED


__all__ = ["CoinbaseAdapter"]
