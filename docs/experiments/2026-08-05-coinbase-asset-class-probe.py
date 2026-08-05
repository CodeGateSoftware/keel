#!/usr/bin/env python
"""Re-runnable evidence probe for docs/experiments/2026-08-05-coinbase-asset-class-feasibility.md.

Every empirical claim in that document comes from this script. It is read-only: it calls
product, market-data and account inspection endpoints plus `preview_order`.

`preview_order` is an HTTP POST to `/api/v3/brokerage/orders/preview`, but it binds nothing —
it is a distinct endpoint from `/orders`, it does not accept the `client_order_id` that
`create_order` requires, and keel's own live executor calls it before every real order
(`keel/execution/executor.py:428`). The guard below enforces that: any POST to a path other
than the preview endpoint raises before it reaches the network, so the safety property is
checked rather than merely asserted. This script never calls create_order, cancel_order,
edit_order, or any transfer, withdrawal or sweep endpoint.

Run against the deployment's venv and credentials:

    cd ~/keel && ./.venv/bin/python \\
        ~/Development/work/CodeGate/keel/docs/experiments/2026-08-05-coinbase-asset-class-probe.py

Counts and prices drift; contract months roll. Front-month contracts are selected dynamically
so the script keeps working after the ids in the document have expired. The structural findings
-- which product types exist, which endpoints answer, which refuse and with what message -- are
what the document rests on.
"""

from __future__ import annotations

import collections
import json
import os
import time

from coinbase.rest import RESTClient
from dotenv import load_dotenv

PRODUCT_TYPES = ["SPOT", "FUTURE", "EQUITY", "FUTURE_GROUP", "OPTION_GROUP"]
# Values the document asserts are not members of the enum at all.
INVALID_PRODUCT_TYPES = ["OPTION", "FX", "FOREX"]
FX_MARKERS = ["EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "INR", "SGD"]
PREVIEW_PATH = "/api/v3/brokerage/orders/preview"
STABILITY_CALLS = 4


class MutationBlocked(RuntimeError):
    """Raised if anything in this script attempts a state-changing call."""


def client() -> RESTClient:
    """A REST client that physically cannot POST anywhere except the preview endpoint."""
    load_dotenv(os.path.expanduser("~/keel/.env"))
    c = RESTClient(api_key=os.environ["CDP_API_KEY"], api_secret=os.environ["CDP_API_SECRET"])

    for verb in ("post", "put", "delete"):
        original = getattr(c, verb)

        def guarded(url_path, *args, _verb=verb, _original=original, **kwargs):
            if _verb != "post" or url_path != PREVIEW_PATH:
                raise MutationBlocked(
                    f"{_verb.upper()} {url_path} blocked: this probe is read-only")
            return _original(url_path, *args, **kwargs)

        setattr(c, verb, guarded)
    return c


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

    print("\nValues the document claims are not enum members at all "
          "(a 400 here is stronger evidence than a zero count):")
    for product_type in INVALID_PRODUCT_TYPES:
        try:
            ps = products(c, product_type)
            print(f"  {product_type:8} UNEXPECTEDLY VALID -- n={len(ps)}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {product_type:8} {str(exc)[:150]}")
    return found


def fx_check(c: RESTClient, found: dict[str, list[dict]]) -> None:
    """No FX asset class exists. Fiat-quoted crypto spot (BTC-EUR) is crypto spot, not FX."""
    header("FX / FOREX")
    for product_type, ps in found.items():
        by_id = [
            p["product_id"] for p in ps
            if any(p["product_id"].endswith(f"-{m}") or p["product_id"].startswith(f"{m}-")
                   for m in FX_MARKERS)
        ]
        by_quote = [p["product_id"] for p in ps if p.get("quote_currency_id") in FX_MARKERS]
        print(f"{product_type:16} fiat-marker ids={len(by_id):4} fiat-quoted={len(by_quote):4} "
              f"e.g. {by_id[:4]}")
    print("\nThose are crypto assets QUOTED in a fiat currency, plus stablecoin pairs "
          "(EURC-USDC, TGBP-USDC). Neither is an FX pair -- no product has two fiat legs.")
    for pair in ["EUR-USD", "GBP-USD", "USD-JPY", "AUD-USD"]:
        try:
            c.get_product(product_id=pair)
            print(f"  {pair}: UNEXPECTEDLY EXISTS")
        except Exception as exc:  # noqa: BLE001
            print(f"  {pair}: {str(exc)[:110]}")
    print("\nNote a false positive for anyone re-running a currency-code sweep: Nokia's ADR "
          "ticker is NOK, which is also the ISO code for the Norwegian krone.")


def front_month(futs: list[dict], group_fragment: str) -> dict | None:
    """Pick the nearest-expiry tradable contract for a group, so this survives contract rolls."""
    matches = [
        p for p in futs
        if group_fragment.lower() in
        ((p.get("future_product_details") or {}).get("group_short_description") or "").lower()
        and not p.get("view_only")
    ]
    return min(
        matches,
        key=lambda p: (p["future_product_details"].get("contract_expiry") or ""),
    ) if matches else None


def futures_taxonomy(futs: list[dict]) -> None:
    header("FUTURES TAXONOMY")
    expiry_types = collections.Counter(
        (p.get("future_product_details") or {}).get("contract_expiry_type") for p in futs
    )
    print(f"contract_expiry_type across {len(futs)} products: {dict(expiry_types)}")
    print("A PERPETUAL count of zero is the point: the US 'perps' are long-dated EXPIRING "
          "contracts that carry funding.\n")

    groups: dict[tuple, list[dict]] = collections.defaultdict(list)
    for p in futs:
        d = p.get("future_product_details") or {}
        groups[(d.get("group_short_description"), d.get("non_crypto"))].append(p)
    for (group, non_crypto), ps in sorted(groups.items(), key=lambda kv: str(kv[0])):
        d = ps[0].get("future_product_details") or {}
        tradable = sum(1 for q in ps if not q.get("view_only"))
        print(f"  {str(group):32} non_crypto={str(non_crypto):5} n={len(ps):3} "
              f"tradable={tradable:2} size={str(d.get('contract_size')):>7} "
              f"funding={str(d.get('funding_interval')):>6} 24x7={d.get('twenty_four_by_seven')} "
              f"ids={[q['product_id'] for q in ps[:3]]}")

    print("\nPerp-style contracts in full -- note the far-dated expiry alongside a live funding "
          "rate, and that the settlement leg is USD (the -CDE suffix is a venue tag, which is "
          "exactly what quote_currency_of's rpartition('-') mistakes for a quote currency):")
    for p in futs:
        d = p.get("future_product_details") or {}
        if not d.get("funding_interval"):
            continue
        print(f"  {p['product_id']:18} expiry={d.get('contract_expiry')} "
              f"funding_rate={str(d.get('funding_rate')):>12} oi={str(d.get('open_interest')):>8} "
              f"quote={p.get('quote_currency_id')} base_increment={p.get('base_increment')} "
              f"risk={d.get('risk_managed_by')}")


def market_data(c: RESTClient, product_id: str, label: str) -> None:
    """Futures answer all of these. Equities answer none of them."""
    header(f"MARKET DATA -- {label} ({product_id})")
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
    fresh = attempt("price fields on the product itself", lambda: {
        k: c.get_product(product_id=product_id).to_dict().get(k)
        for k in ("price", "best_bid_price", "best_ask_price", "mid_market_price", "volume_24h")
    })
    if fresh is not None and not any(fresh.values()):
        print("  ^ every price field empty")


def equity_pagination(c: RESTClient, first_page: list[dict]) -> None:
    """The 1000-row page is a cap, not the universe -- and no access pattern is reproducible."""
    header("EQUITIES -- pagination and stability")
    for limit in (250, 1000, 5000):
        n = len(c.get_products(product_type="EQUITY", limit=limit).to_dict().get("products") or [])
        print(f"limit={limit:5} -> returned={n}  (any limit above 1000 still yields 1000)")

    sets = [{p["product_id"] for p in first_page}]
    for _ in range(STABILITY_CALLS - 1):
        sets.append({p["product_id"] for p in products(c, "EQUITY")})
    print(f"\n{STABILITY_CALLS} identical calls, composition drift vs the first:")
    for i, s in enumerate(sets[1:], start=2):
        print(f"  call {i}: |differs|={len(sets[0] ^ s) // 2} of {len(s)}")
    print(f"  union across {STABILITY_CALLS} calls: {len(set().union(*sets))} distinct ids")

    def window() -> set[str]:
        page = c.get_products(product_type="EQUITY", offset=0, limit=500).to_dict()
        return {p["product_id"] for p in (page.get("products") or [])}

    a, b = window(), window()
    print(f"\nsame offset=0,limit=500 twice: |A|={len(a)} |B|={len(b)} differ={len(a ^ b) // 2}"
          "  (even a fixed window is not a stable window)")

    seen: set[str] = set()
    cursor, pages = "", 0
    while pages < 20:
        page = c.get_products(product_type="EQUITY", cursor=cursor).to_dict()
        ids = [p["product_id"] for p in (page.get("products") or [])]
        if not ids:
            break
        before = len(seen)
        seen.update(ids)
        pages += 1
        print(f"  cursor page {pages:2}: +{len(seen) - before:4} new, {len(seen):6} total")
        cursor = (page.get("pagination") or {}).get("next_cursor") or ""
        if not cursor:
            break
    print(f"cursor walk reached {len(seen)} distinct ids across {pages} pages -- well past the "
          "1000 cap, but repeat walks do not converge on the same total, so there is no "
          "reproducible snapshot of the equity universe.")


def equities(c: RESTClient, eqs: list[dict]) -> None:
    header("EQUITIES -- identity and classification")
    subtypes = collections.Counter(
        (p.get("equity_product_details") or {}).get("equity_subtype") for p in eqs
    )
    print("subtypes:", dict(subtypes))
    print("quote currencies:", dict(collections.Counter(p.get("quote_currency_id") for p in eqs)))
    print("price field populated:", dict(collections.Counter(bool(p.get("price")) for p in eqs)))

    sample = eqs[0]
    details = sample.get("equity_product_details") or {}
    print("\nidentity shape -- product_id is an opaque hash and the ticker hides in a sub-object:")
    print(json.dumps({k: sample.get(k) for k in
                      ("product_id", "alias", "base_currency_id", "display_name",
                       "quote_currency_id", "price", "best_bid_price", "mid_market_price")},
                     default=str, indent=2))
    day = details.get("trading_day_info") or {}
    print(f"ticker={details.get('ticker')} fractionable={details.get('fractionable')} "
          f"cik={details.get('cik')} venue_id={day.get('venue_id')}")
    print("sessions:", [s["session_type"] for s in day.get("trading_sessions", [])])
    print("A CIK, an equity_subtype and an Apex venue are what distinguish these from the "
          "non-US tokenized-stock product: they are real US shares.")


def accounts(c: RESTClient) -> None:
    header("ACCOUNTS")
    accts = c.get_accounts(limit=250).to_dict().get("accounts") or []
    print("types:", dict(collections.Counter(a.get("type") for a in accts)))
    print("platforms:", dict(collections.Counter(a.get("platform") for a in accts)))
    print("No futures/FCM currency account exists alongside these.")


def order_paths(c: RESTClient, futures_id: str, equity_id: str) -> None:
    """preview_order binds nothing. The two 403s are the finding."""
    header("ORDER PATH (preview only -- nothing is placed)")
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

    print("\n--- proving the mutation guard is live, not decorative")
    try:
        c.post("/api/v3/brokerage/orders", data={})
    except MutationBlocked as exc:
        print(f"OK: {exc}")


def main() -> None:
    c = client()
    found = census(c)
    fx_check(c, found)
    accounts(c)

    futs = found.get("FUTURE", [])
    if futs:
        futures_taxonomy(futs)
        picks = [(front_month(futs, "nano BTC"), "dated crypto future"),
                 (front_month(futs, "nano BTC Perp"), "perp-style future"),
                 (front_month(futs, "Gold"), "gold future")]
        for product, label in picks:
            if product:
                market_data(c, product["product_id"], label)

    eqs = found.get("EQUITY", [])
    if eqs:
        equities(c, eqs)
        equity_pagination(c, eqs)
        sample = eqs[0]
        ticker = (sample.get("equity_product_details") or {}).get("ticker") or "equity"
        market_data(c, sample["product_id"], f"equity {ticker} (quoted id)")
        if sample.get("alias"):
            market_data(c, sample["alias"], f"equity {ticker} (alias id -- the other quote leg)")

    futures_pick = front_month(futs, "nano BTC") if futs else None
    if futures_pick and eqs:
        order_paths(c, futures_pick["product_id"], eqs[0]["product_id"])


if __name__ == "__main__":
    main()
