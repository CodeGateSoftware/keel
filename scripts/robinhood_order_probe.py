"""Place ONE deliberately-unfillable Robinhood order, observe it, cancel it (#412).

`scripts/robinhood_smoke.py` closes everything a GET can close. What it cannot reach is the half
of this adapter that only exists once an order does: the placement response's `state` values, the
per-order `fee_charged` that `get_fee_summary` sums, whether a cancel `200` actually confirms, and
every field name in `tests/fixtures/rh_order_open.json`, `rh_order_filled.json`,
`rh_order_canceled.json` and `rh_orders.json`.

This script has now been run once, on 2026-08-20, against a real credential. It placed one
BTC-USD limit buy -- 0.0001 BTC at $36,352.78, 50% below the bid -- observed it, and cancelled
it. `rh_order_open.json`, `rh_order_canceled.json` and `rh_orders.json`'s `results[]` shape are
that order's own responses. `rh_order_filled.json` is still documentation-derived and cannot be
anything else while this script works the way it does: an order that CAN fill is not a probe.

Robinhood publishes no sandbox (`adapter.py`'s module docstring), and the conformance suite calls
`place_order` and so must never see live credentials (`conformance/suite.py`). So one real order
is the only way, and this script is the smallest, most heavily-fenced version of that.

## What it does

A LIMIT BUY placed far below the best bid, polled, then cancelled. A limit order that cannot cross
the book rests until cancelled, which is what makes it observable at every state transition an
adapter cares about -- and structurally unable to fill while it is observed.

## The fences, and why each one exists

**It refuses to run without `--place`.** The default is a dry run that prints the exact JSON body
and stops. Nothing about the money path should be reachable by forgetting a flag.

**It permits exactly one order-creating request, at the transport layer.** `_OneOrderOnly` rebinds
`_request` the way `robinhood_smoke._ReadOnly` does -- rebinds, never wraps, because a wrapper's
`__getattr__` is bypassed by the transport's own internal `self._request` calls. A second POST to
`/orders/` raises. A retry loop, a bug, or a copy-paste cannot turn this into two live orders.

**It refuses a price that could fill.** `_MIN_DISCOUNT` (25%) is checked against the venue's own
best bid, read moments before. An order that could cross the book is not a shape probe, it is a
trade.

**It refuses a notional above `--max-notional` (default $10).** Belt to the discount's braces:
even if the discount check were somehow satisfied by a stale or crossed quote, the amount at risk
is bounded by a number the operator typed.

⚠️ `best_bid_ask`'s two legs are sampled independently and can cross by up to ~1.4 bps on the
tightest pairs (#413), so the bid this reads is not a firm price. That is why the discount is 25%
and not 1%: the fence has to survive a quote that is slightly wrong in either direction.

**It checks the venue's own bounds before sending.** Below `min_order_amount` (quote-denominated,
$0.10 -- #410) or off `asset_increment`, the venue would reject and the run would learn nothing
except that we can build a bad body.

**It cancels in a `finally`.** An exception between placement and cancellation must not leave a
live resting order behind. The cancel is attempted regardless, and its outcome is reported
separately from the placement's.

**It passes an `idempotency_key`** (#409), so that if this script is ever re-run after an
ambiguous failure the venue sees one order rather than two. The key is derived from the run's
`--tag`, which the operator sets deliberately.

## Output

Every raw response is written to `--out-dir` as JSON, ready to become a fixture. `Decimal` is
serialised as an unquoted number and `str` stays quoted, which is exactly the distinction this
venue is inconsistent about (#217 F6) and exactly what the fixtures must preserve.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from keel_broker_api.port import TradeScopeDenied

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Fraction below the best bid the limit price is placed at, unless `--discount` says otherwise.
_DEFAULT_DISCOUNT = Decimal("0.5")

#: The smallest discount this script will place at, whatever `--discount` asks for. A limit buy
#: 25% below the bid cannot cross the book on any venue behaving remotely normally, and the margin
#: absorbs a `best_bid_ask` read that is slightly wrong (#413).
_MIN_DISCOUNT = Decimal("0.25")

#: Ceiling on the order's notional, unless `--max-notional` says otherwise. Small enough that the
#: worst conceivable outcome -- an unfillable order somehow filling -- is an annoyance.
_DEFAULT_MAX_NOTIONAL = Decimal("10")

#: The base size probed, unless `--base-size` says otherwise. 0.0001 BTC is ~$6.80 at recent
#: prices: comfortably under the notional cap and ~68x above the venue's $0.10 minimum.
_DEFAULT_BASE_SIZE = Decimal("0.0001")

_DEFAULT_SYMBOL = "BTC-USD"

#: How many times, and how far apart, `run` re-reads a cancelled order waiting for it to leave
#: `open`. The 2026-08-20 run (#412) saw the venue take ~1.1s; ~15s of patience is far more than
#: that and still bounded, and unlike `adapter.cancel_order` nothing here is on an exit path.
_CANCEL_SETTLE_POLLS = 10
_CANCEL_SETTLE_INTERVAL = 1.5


class OrderProbeRefused(RuntimeError):
    """A fence rejected the run. Never means the venue said no -- it means we did."""


class _OneOrderOnly:
    """Caps this process at ONE order-creating request, at the request layer.

    Installed onto the transport instance rather than wrapped around it, for the reason
    `robinhood_smoke._ReadOnly` documents: the transport's methods call their own
    `self._request`, which a `__getattr__` wrapper never sees. Rebinding the attribute is what
    makes the guarantee cover the probe and not merely the tests.

    Cancels and reads are unlimited -- the danger is exclusively in creating orders, and a guard
    that also fenced the cancel could strand the very order it was protecting against.
    """

    def __init__(self, transport: Any) -> None:
        self._transport = transport
        self.calls: list[tuple[str, str]] = []
        #: Last raw decoded response per `(METHOD, path-shape)`, so `run` can record the bodies
        #: that `adapter.place_order` and `adapter.cancel_order` consume and reduce to a
        #: `PlacementResult` and a `bool`. The 2026-08-20 run (#412) lost both: the cancel `200`
        #: turned out to be an acknowledgement rather than a confirmation, and the evidence for
        #: that had to be reconstructed from the surrounding polls because the body itself was
        #: never written down. A probe whose entire purpose is recording shapes must not discard
        #: the two responses only it can reach.
        self.responses: dict[str, Any] = {}
        self.orders_created = 0
        self._inner = transport._request
        transport._request = self._request

    def __getattr__(self, name: str) -> Any:
        return getattr(self._transport, name)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        creating = method.upper() == "POST" and path.rstrip("/").endswith("/orders")
        if creating:
            if self.orders_created:
                raise OrderProbeRefused(
                    "robinhood_order_probe.py attempted a SECOND order-creating request. This "
                    "script places exactly one order per run, by construction."
                )
            self.orders_created += 1
        self.calls.append((method.upper(), path))
        response = self._inner(method, path, **kwargs)
        if creating:
            self.responses["placement"] = response
        elif path.rstrip("/").endswith("/cancel"):
            self.responses["cancel"] = response
        return response


#: Wraps a `Decimal`'s exact digits so `dumps_venue_json` can unquote them afterwards. Chosen to
#: be something no venue payload could contain; `dumps_venue_json` verifies that rather than
#: assuming it.
_DECIMAL_SENTINEL = "@@keel-decimal:{}@@"


class _DecimalJSON(json.JSONEncoder):
    """Emit a `Decimal` as its exact digits, wrapped in `_DECIMAL_SENTINEL` for `dumps_venue_json`.

    ⚠️ **The obvious implementation does not work, and fails silently.** Returning a `float`
    subclass whose `__repr__` carries the original text does not survive: `json` renders floats by
    calling `float.__repr__` directly, not `repr(o)`, so the override is bypassed and
    `Decimal("0.00000001")` is written as `1e-08`. That is the same malformed-number hazard
    `translate._render` exists to prevent, and here it would be baked into a FIXTURE -- a wrong
    claim about what the venue sends, which is precisely what #414 and #230 were.

    So the digits go out as a string and are unquoted textually afterwards. Ugly, and correct.
    """

    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return _DECIMAL_SENTINEL.format(format(o, "f"))
        return super().default(o)


def dumps_venue_json(payload: Any, **kwargs: Any) -> str:
    """Serialise a decoded venue response back into fixture-shaped JSON.

    The transport decodes with `parse_float=Decimal`, so a value that arrived UNQUOTED is a
    `Decimal` here and one that arrived QUOTED is a `str`. Writing each back in its own form is
    what makes a recorded response a faithful fixture: this venue quotes some money fields and not
    others in the same object (#217 F6), and a dump that quoted everything would erase the one
    distinction the fixtures exist to preserve.
    """
    text = json.dumps(payload, cls=_DecimalJSON, **kwargs)
    marker = _DECIMAL_SENTINEL.format("")[:15]
    if text.count(marker) != _count_decimals(payload):
        raise ValueError(
            "a venue payload contained this module's Decimal sentinel; refusing to rewrite it "
            "rather than risk corrupting a recorded fixture"
        )
    pattern = r'"' + re.escape(_DECIMAL_SENTINEL).replace(r"\{\}", r"(-?[\d.]+)") + r'"'
    return re.sub(pattern, r"\1", text)


def _count_decimals(payload: Any) -> int:
    """How many `Decimal`s `payload` holds, so `dumps_venue_json` can prove every sentinel it
    finds is one it put there."""
    if isinstance(payload, Decimal):
        return 1
    if isinstance(payload, dict):
        return sum(_count_decimals(v) for v in payload.values())
    if isinstance(payload, (list, tuple)):
        return sum(_count_decimals(v) for v in payload)
    return 0


def quantize_to(value: Decimal, increment: Decimal) -> Decimal:
    """`value` rounded DOWN to a multiple of `increment`.

    Down, never nearest: rounding a price up moves a deliberately-unfillable buy TOWARD the book,
    and rounding a size up spends more than was authorised. Both errors are small and both are in
    the one direction this script must not err.
    """
    if increment <= 0:
        return value
    # `.normalize()` because the venue sends its increments padded (`asset_increment` arrives as
    # `0.000000010000000000`), and multiplying by one inherits that exponent -- which `_render`
    # would then faithfully write onto the wire as `0.000100000000000000`. Harmless arithmetically
    # and needless noise in a live-money body; normalizing emits the digits a human would.
    return ((value // increment) * increment).normalize()


def plan_order(
    *,
    bid: Decimal,
    base_size: Decimal,
    discount: Decimal,
    max_notional: Decimal,
    min_order_amount: Decimal | None,
    asset_increment: Decimal | None,
    max_order_size: Decimal | None,
    quote_increment: Decimal | None,
) -> dict[str, Decimal]:
    """Everything the order needs, or `OrderProbeRefused` naming the fence that stopped it.

    Pure, and separated from every network call, so each fence is testable without a venue and
    without a credential -- which for this script matters more than usual: the fences are the only
    thing standing between a shape probe and a trade.
    """
    if discount < _MIN_DISCOUNT:
        raise OrderProbeRefused(
            f"discount {discount} is below the floor {_MIN_DISCOUNT}. A limit buy nearer than "
            f"that to the bid could cross the book, and an order that can fill is not a probe."
        )
    if bid <= 0:
        raise OrderProbeRefused(f"the venue reported a non-positive bid ({bid}); cannot price")

    limit_price = bid * (Decimal(1) - discount)
    if quote_increment is not None:
        limit_price = quantize_to(limit_price, quote_increment)
    if asset_increment is not None:
        base_size = quantize_to(base_size, asset_increment)

    if base_size <= 0:
        raise OrderProbeRefused("base_size rounded to zero against the venue's asset_increment")
    if limit_price <= 0:
        raise OrderProbeRefused("limit_price rounded to zero against the venue's quote_increment")

    notional = base_size * limit_price
    if notional > max_notional:
        raise OrderProbeRefused(
            f"notional {notional} exceeds --max-notional {max_notional}. Lower --base-size."
        )
    if min_order_amount is not None and notional < min_order_amount:
        raise OrderProbeRefused(
            f"notional {notional} is below the venue's min_order_amount {min_order_amount} "
            f"(quote currency -- see #410). The venue would reject this and the run would learn "
            f"nothing about order shapes."
        )
    if max_order_size is not None and base_size > max_order_size:
        raise OrderProbeRefused(
            f"base_size {base_size} exceeds the venue's max_order_size {max_order_size}"
        )
    # Recomputed against the FINAL, rounded price rather than the raw one: rounding down moves the
    # price away from the book, so a discount computed before rounding understates the true one.
    effective = (bid - limit_price) / bid
    if effective < _MIN_DISCOUNT:
        raise OrderProbeRefused(
            f"after rounding, the limit price is only {effective:.4f} below the bid"
        )
    return {
        "bid": bid,
        "limit_price": limit_price,
        "base_size": base_size,
        "notional": notional,
        "discount": effective,
    }



#: Statuses on a placement POST that mean the venue certainly did NOT create an order. Everything
#: else -- a 5xx, a timeout, a dropped connection -- is an UNKNOWN outcome, which is a different
#: and far more dangerous thing to report.
_CERTAIN_NO_ORDER_STATUSES = {400, 401, 403, 404, 422}


#: The 403 finding, module-level so the raw-`HTTPError` path and the `TradeScopeDenied` path
#: below report it with the SAME words. Two phrasings of one finding is how a reader ends up
#: believing they are two different findings.
_NO_TRADE_SCOPE_NOTE = (
    "403 -- this credential has no TRADE scope. Reads succeed on it and placement "
    "does not, which is the exact state #233 exists about. No order was created."
)


def _classify_placement_error(exc: Exception, client_order_id: str) -> dict[str, Any]:
    """What a failed placement actually tells us, which is not always "no order exists".

    This is the same split `transport._request` draws for reads and `adapter.place_order` draws
    for a rejected `state`, applied to the one request where getting it wrong costs money. A 403
    is the venue refusing THIS request -- certainly no order. A 5xx or a dropped connection is the
    venue not answering, which is silence, not refusal: the order may be resting right now.

    Reporting silence as "nothing happened" is how a probe leaves a live order behind and tells
    the operator it did not. So the unknown case names the `client_order_id` and says to go and
    look, because that id is the only handle anyone has on an order this process never saw.
    """
    # #233 PR3: the adapter now translates this venue's 403 into `TradeScopeDenied` before it
    # reaches here, and that exception carries no `.response` to read a status off. Without this
    # branch the refusal would fall through to UNKNOWN -- "an order may be resting right now, go
    # and look" -- for an order the venue was explicit about never creating. UNKNOWN is the
    # outcome that makes a human stop and search the order history, and spending it on a refusal
    # teaches the operator to discount the one signal this script exists to raise honestly.
    if isinstance(exc, TradeScopeDenied):
        return {
            "outcome": "refused",
            "status": 403,
            "detail": str(exc)[:400],
            "note": _NO_TRADE_SCOPE_NOTE,
        }

    status = getattr(getattr(exc, "response", None), "status_code", None)
    detail = ""
    response = getattr(exc, "response", None)
    if response is not None:
        detail = (getattr(response, "text", "") or "")[:400]

    if status in _CERTAIN_NO_ORDER_STATUSES:
        note = (
            "the venue refused this request; no order was created"
            if status != 403
            else _NO_TRADE_SCOPE_NOTE
        )
        return {"outcome": "refused", "status": status, "detail": detail, "note": note}

    return {
        "outcome": "UNKNOWN",
        "status": status,
        "detail": detail or f"{type(exc).__name__}: {exc}",
        "note": (
            "the venue did not answer, which is SILENCE and not refusal -- an order may be "
            f"resting right now. Look for client_order_id {client_order_id} in the venue's order "
            "history before re-running, and do NOT assume nothing happened."
        ),
    }

def record(out_dir: Path, name: str, payload: Any) -> Path:
    """Write one raw response, ready to become a fixture. Returns the path written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.json"
    path.write_text(dumps_venue_json(payload, indent=2) + "\n")
    return path


def run(
    transport: Any,
    *,
    symbol: str,
    plan: dict[str, Decimal],
    idempotency_key: str,
    out_dir: Path,
) -> dict[str, Any]:
    """Place, observe, and cancel. The cancel runs in `finally` and is reported separately.

    Returns a report rather than printing one, so the whole sequence is testable against a fake
    transport -- which for a function that places live orders is not a nicety.
    """
    from keel_broker_api.orders import LimitGTC
    from keel_broker_api.port import resolve_client_order_id
    from keel_broker_robinhood.adapter import RobinhoodAdapter
    from keel_core.types import Side

    adapter = RobinhoodAdapter(transport)
    spec = LimitGTC(
        product_id=symbol,
        side=Side.BUY,
        base_size=plan["base_size"],
        limit_price=plan["limit_price"],
    )
    report: dict[str, Any] = {
        "client_order_id": resolve_client_order_id(idempotency_key),
        "placed": None,
        "placement_error": None,
        "placement_response": None,
        "observed": None,
        "cancel_confirmed": None,
        "cancel_response": None,
        "after_cancel": None,
        "settled": None,
        "orders_list": None,
    }
    tee: dict[str, Any] = getattr(transport, "responses", {})

    try:
        placed = adapter.place_order(spec, idempotency_key=idempotency_key)
    except Exception as exc:  # noqa: BLE001 -- classified immediately below, never swallowed
        report["placement_error"] = _classify_placement_error(exc, report["client_order_id"])
        return report

    report["placed"] = {
        "success": placed.success,
        "broker_order_id": placed.broker_order_id,
        "reason": placed.reason,
    }
    # The POST's own body, which `place_order` reduces to the three fields above. It is the only
    # sighting of a placement response there will ever be, and it is the one that carries the
    # venue's rejection vocabulary when `state` comes back already terminal.
    if "placement" in tee:
        report["placement_response"] = tee["placement"]
        record(out_dir, "rh_order_placement_observed", tee["placement"])
    order_id = placed.broker_order_id
    if not placed.success or order_id is None:
        # Nothing is resting, so there is nothing to cancel and no `finally` to enter. A refusal
        # is still a RESULT: it carries the venue's own rejection vocabulary, which is one of the
        # things this run exists to observe.
        return report

    try:
        report["observed"] = transport.get_order(order_id)
        record(out_dir, "rh_order_open_observed", report["observed"])
        report["orders_list"] = transport.get_orders()
        record(out_dir, "rh_orders_observed", report["orders_list"])
    finally:
        # Unconditional. An exception above must not leave a live resting order behind, and this
        # is the only process that knows the id.
        report["cancel_confirmed"] = adapter.cancel_order(order_id)
        if "cancel" in tee:
            report["cancel_response"] = tee["cancel"]
            record(out_dir, "rh_order_cancel_response_observed", tee["cancel"])
        report["after_cancel"] = transport.get_order(order_id)
        record(out_dir, "rh_order_after_cancel_observed", report["after_cancel"])
        # A SECOND read, after a bounded wait. On 2026-08-20 (#412) the cancel took ~1.1s to
        # settle, so `after_cancel` above recorded the order still `open` and the run produced no
        # observation of the terminal state at all -- the fixture named `rh_order_canceled.json`
        # was filled in from a hand-written poll afterwards. Waiting here is safe in a way it is
        # not inside `adapter.cancel_order`: this is a recording script, not the executor's exit
        # path, so a few seconds costs nothing but a few seconds.
        for attempt in range(_CANCEL_SETTLE_POLLS):
            settled = transport.get_order(order_id)
            report["settled"] = settled
            if settled is not None and settled.get("state") != "open":
                break
            if attempt + 1 < _CANCEL_SETTLE_POLLS:
                time.sleep(_CANCEL_SETTLE_INTERVAL)
        if report["settled"] is not None:
            record(out_dir, "rh_order_canceled_observed", report["settled"])
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbol", default=_DEFAULT_SYMBOL)
    parser.add_argument("--base-size", type=Decimal, default=_DEFAULT_BASE_SIZE)
    parser.add_argument("--discount", type=Decimal, default=_DEFAULT_DISCOUNT)
    parser.add_argument("--max-notional", type=Decimal, default=_DEFAULT_MAX_NOTIONAL)
    parser.add_argument("--env", type=Path, default=REPO_ROOT / ".env")
    parser.add_argument(
        "--api-key-var",
        default="ROBINHOOD_API_KEY",
        help="which .env variable holds the API key identifier",
    )
    parser.add_argument("--private-key-var", default="ROBINHOOD_PRIVATE_KEY")
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / ".robinhood-probe")
    parser.add_argument(
        "--tag",
        default="order-probe",
        help="seeds the idempotency key; a re-run under the same tag is ONE order at the venue",
    )
    parser.add_argument(
        "--place",
        action="store_true",
        help="actually place the order. Without this the run stops after printing the body.",
    )
    args = parser.parse_args(argv)

    from dotenv import dotenv_values
    from keel_broker_robinhood.transport import RobinhoodTransport, _results

    values = dotenv_values(args.env)
    api_key = (values.get(args.api_key_var) or "").strip()
    private_key = (values.get(args.private_key_var) or "").strip()
    if not api_key or not private_key:
        print(f"missing {args.api_key_var} or {args.private_key_var} in {args.env}")
        return 2

    transport = RobinhoodTransport(api_key=api_key, private_key_b64=private_key)
    guard = _OneOrderOnly(transport)

    quote = _results(transport.get_best_bid_ask(args.symbol))[0]
    pair = _results(transport.get_trading_pairs(args.symbol))[0]

    def _dec(value: Any) -> Decimal | None:
        return None if value is None else Decimal(str(value))

    try:
        plan = plan_order(
            bid=Decimal(str(quote["bid"])),
            base_size=args.base_size,
            discount=args.discount,
            max_notional=args.max_notional,
            min_order_amount=_dec(pair.get("min_order_amount")),
            asset_increment=_dec(pair.get("asset_increment")),
            max_order_size=_dec(pair.get("max_order_size")),
            quote_increment=_dec(pair.get("quote_increment")),
        )
    except OrderProbeRefused as exc:
        print(f"REFUSED: {exc}")
        return 3

    from keel_broker_api.orders import LimitGTC
    from keel_broker_api.port import resolve_client_order_id
    from keel_broker_robinhood.translate import to_order_body
    from keel_core.types import Side

    body = to_order_body(
        LimitGTC(
            product_id=args.symbol,
            side=Side.BUY,
            base_size=plan["base_size"],
            limit_price=plan["limit_price"],
        ),
        client_order_id=resolve_client_order_id(args.tag),
    )

    print(f"symbol        {args.symbol}")
    print(f"best bid      {quote['bid']}   ask {quote['ask']}")
    print(f"limit price   {plan['limit_price']}   ({plan['discount']:.2%} below bid)")
    print(f"base size     {plan['base_size']}")
    print(f"notional      {plan['notional']:.2f}  (cap {args.max_notional})")
    print("\nexact body that would be POSTed to /api/v2/crypto/trading/orders/:")
    print(dumps_venue_json(body, indent=2))

    if not args.place:
        print("\nDRY RUN -- nothing was sent. Re-run with --place to place this order.")
        return 0

    report = run(
        guard,
        symbol=args.symbol,
        plan=plan,
        idempotency_key=args.tag,
        out_dir=args.out_dir,
    )
    print("\n" + dumps_venue_json(report, indent=2))
    print(f"\nresponses written to {args.out_dir}")
    print(f"order-creating requests issued: {guard.orders_created}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
