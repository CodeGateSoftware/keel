#!/usr/bin/env python
"""Re-runnable evidence probe for docs/experiments/2026-08-05-coinbase-asset-class-feasibility.md.

Every empirical claim in that document comes from this script. It is READ-ONLY: it calls
only product/market-data/account inspection endpoints plus `preview_order`, which does not
place an order. It never calls create_order, cancel_order, edit_order, or any transfer or
sweep endpoint.

Run against the deployment's venv and credentials:

    cd ~/keel && ./.venv/bin/python \\
        ~/Development/work/CodeGate/keel/docs/experiments/2026-08-05-coinbase-asset-class-probe.py

Counts and prices will have moved since 2026-08-05; the structural findings (which product
types exist, which endpoints answer, which 403) are what the document rests on.
"""

from __future__ import annotations

import collections
import json
import os
import time

from coinbase.rest import RESTClient
from dotenv import load_dotenv

PRODUCT_TYPES = ["SPOT", "FUTURE", "EQUITY", "FUTURE_GROUP", "OPTION_GROUP"]
FX_MARKERS = ["EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD"]


def client() -> RESTClient:
    load_dotenv(os.path.expanduser("~/keel/.env"))
    return RESTClient(api_key=os.environ["CDP_API_KEY"], api_secret=os.environ["CDP_API_SECRET"])


def header(title: str) -> None:
    print("\n" + "=" * 72 + f"\n{title}\n" + "=" * 72)


def attempt(label: str, call):
    """Run a read-only call, printing either a trimmed result or the exact API error."""
    print(f"\n--- {label}")
    try:
        result = call()
    except Exception as exc:  # noqa: BLE001 - the error text *is* the evidence
        print(f"ERROR {type(exc).__name__}: {str(exc)[:300]}")
        return None
    payload = result.to_dict() if hasattr(result, "to_dict") else result
    print(json.dumps(payload, default=str)[:700])
    return payload


def products(c: RESTClient, product_type: str) -> list[dict]:
    return c.get_products(product_type=product_type).to_dict().get("products") or []


def census(c: RESTClient) -> dict[str, list[dict]]:
    """Which product types exist, and how many of each."""
    header("PRODUCT TYPE CENSUS")
    found: dict[str, list[dict]] = {}
    for product_type in PRODUCT_TYPES:
        try:
            ps = products(c, product_type)
        except Exception as exc:  # noqa: BLE001
            print(f"{product_type:16} ERROR {str(exc)[:120]}")
            continue
        found[product_type] = ps
        venues = collections.Counter(p.get("product_venue") for p in ps)
        tradable = sum(1 for p in ps if not p.get("view_only"))
        print(f"{product_type:16} n={len(ps):5} tradable={tradable:4} venues={dict(venues)}")

    # `OPTION` is not a member of the enum at all — distinct from OPTION_GROUP returning zero.
    attempt("product_type=OPTION (expect 400: not a valid value)",
            lambda: c.get_products(product_type="OPTION"))
    return found


def fx_check(c: RESTClient, found: dict[str, list[dict]]) -> None:
    """No FX asset class exists. Fiat-quoted crypto spot (BTC-EUR) is crypto, not FX."""
    header("FX / FOREX")
    for product_type, ps in found.items():
        hits = [
            p["product_id"] for p in ps
            if any(p["product_id"].endswith(f"-{m}") or p["product_id"].startswith(f"{m}-")
                   for m in FX_MARKERS)
        ]
        crypto_quoted = [h for h in hits if product_type == "SPOT"]
        print(f"{product_type:16} currency-marker ids: {len(hits):4} "
              f"(e.g. {hits[:4]}) -- these are fiat-QUOTED crypto spot, not FX pairs"
              if crypto_quoted else f"{product_type:16} currency-marker ids: {len(hits)}")


def futures_taxonomy(c: RESTClient, futs: list[dict]) -> None:
    header("FUTURES TAXONOMY")
    expiry_types = collections.Counter(
        (p.get("future_product_details") or {}).get("contract_expiry_type") for p in futs
    )
    print(f"contract_expiry_type across {len(futs)} products: {dict(expiry_types)}")
    print("(A PERPETUAL count of zero is the point: the US 'perps' are long-dated EXPIRING "
          "contracts that carry funding.)")

    groups: dict[tuple, list[dict]] = collections.defaultdict(list)
    for p in futs:
        d = p["future_product_details"]
        groups[(d.get("group_short_description"), d.get("non_crypto"))].append(p)
    for (group, non_crypto), ps in sorted(groups.items(), key=lambda kv: str(kv[0])):
        d = ps[0]["future_product_details"]
        print(f"  {str(group):32} non_crypto={str(non_crypto):5} n={len(ps):3} "
              f"size={d.get('contract_size'):>7} funding={str(d.get('funding_interval')):>6} "
              f"24x7={d.get('twenty_four_by_seven')} ids={[q['product_id'] for q in ps[:3]]}")


def market_data(c: RESTClient, product_id: str, label: str) -> None:
    """Futures answer all of these; equities answer none of them."""
    header(f"MARKET DATA — {label} ({product_id[:40]})")
    now = int(time.time())
    for granularity, span in [("ONE_HOUR", 86400 * 3), ("ONE_DAY", 86400 * 30)]:
        try:
            candles = c.get_candles(product_id=product_id, start=str(now - span),
                                    end=str(now), granularity=granularity).to_dict()["candles"]
            newest = candles[0] if candles else None
            print(f"candles {granularity:10} n={len(candles)} newest={newest}")
        except Exception as exc:  # noqa: BLE001
            print(f"candles {granularity:10} ERROR {str(exc)[:160]}")
    attempt("product_book", lambda: c.get_product_book(product_id=product_id, limit=2))
    attempt("best_bid_ask", lambda: c.get_best_bid_ask(product_ids=[product_id]))
    attempt("market_trades", lambda: c.get_market_trades(product_id=product_id, limit=2))


def equities(c: RESTClient, eqs: list[dict]) -> None:
    header("EQUITIES — identity, pagination, and the price-field gap")
    print(f"returned={len(eqs)} (a flat 1000 is the page cap, not the universe)")
    subtypes = collections.Counter(
        (p.get("equity_product_details") or {}).get("equity_subtype") for p in eqs
    )
    print("subtypes:", dict(subtypes))
    print("quote currencies:", dict(collections.Counter(p["quote_currency_id"] for p in eqs)))
    print("price field populated:", dict(collections.Counter(bool(p.get("price")) for p in eqs)))

    second = {p["product_id"] for p in products(c, "EQUITY")}
    first = {p["product_id"] for p in eqs}
    print(f"set stability across two calls: |A|={len(first)} |B|={len(second)} "
          f"|A∩B|={len(first & second)} |A\\B|={len(first - second)}")

    sample = eqs[0]
    print("\nidentity shape — product_id is an opaque hash, the ticker hides in a sub-object:")
    print(json.dumps({k: sample.get(k) for k in
                      ("product_id", "alias", "base_currency_id", "display_name",
                       "quote_currency_id", "price", "best_bid_price", "mid_market_price")},
                     default=str, indent=2))
    details = sample.get("equity_product_details") or {}
    print("ticker:", details.get("ticker"), "| fractionable:", details.get("fractionable"),
          "| sessions:", [s["session_type"] for s in
                          (details.get("trading_day_info") or {}).get("trading_sessions", [])])


def order_paths(c: RESTClient, futures_id: str, equity_id: str) -> None:
    """preview_order does not place an order. The 403s are the finding."""
    header("ORDER PATH (preview only — nothing is placed)")
    attempt("preview futures (expect 403: FCM onboarding)",
            lambda: c.preview_order(product_id=futures_id, side="BUY",
                                    order_configuration={"market_market_ioc": {"base_size": "1"}}))
    attempt("preview equity (expect 403: unsupported for equities)",
            lambda: c.preview_order(
                product_id=equity_id, side="BUY",
                order_configuration={"market_market_ioc": {"quote_size": "10"}}))
    attempt("cfm balance_summary (null => not onboarded)",
            lambda: c.get("/api/v3/brokerage/cfm/balance_summary"))
    attempt("cfm positions", lambda: c.list_futures_positions())
    attempt("api key permissions", lambda: c.get_api_key_permissions())


def main() -> None:
    c = client()
    found = census(c)
    fx_check(c, found)

    futs = found.get("FUTURE", [])
    if futs:
        futures_taxonomy(c, futs)
        for pid, label in [("BIT-28AUG26-CDE", "dated crypto future"),
                           ("BIP-20DEC30-CDE", "perp-style future"),
                           ("GOL-25NOV26-CDE", "gold future")]:
            market_data(c, pid, label)

    eqs = found.get("EQUITY", [])
    if eqs:
        equities(c, eqs)
        for p in eqs[:3]:
            ticker = (p.get("equity_product_details") or {}).get("ticker") or p["display_name"]
            market_data(c, p["product_id"], f"equity {ticker}")

    if futs and eqs:
        order_paths(c, "BIT-28AUG26-CDE", eqs[0]["product_id"])


if __name__ == "__main__":
    main()
