"""What a round trip actually costs on a commission-free venue -- issue #371 (Alpaca Phase C).

**The question.** Every cost claim keel has ever made is about Coinbase, where the taker fee
(1.2%/leg) dwarfs everything else and the spread is a rounding error beside it. Alpaca charges
NO commission on US equities, so the entire cost is the two terms keel has never had to measure
carefully: the sell-side regulatory pass-throughs, and the spread. The PRD (§6.3, O4) makes
this measurement a PRECONDITION -- "no strategy claim is believed before it" -- because the
failure mode is obvious and seductive: an equities backtest priced at `taker_pct: 0.0` (which is
what `config.paper-equities.yaml` ships today, honestly labelled as awaiting this work) models a
FREE venue, and a free venue makes everything look profitable.

DECLARED BEFORE THE RUN:

* **One estimator, both asset classes, same data type.** Corwin-Schultz (2012) on daily
  highs/lows, from candles already cached. A comparison measured two different ways would be an
  artifact of the methods, not a finding about the venues. Overnight-gap adjustment ON (equities
  gap, crypto does not; leaving it off would inflate the equities side of exactly this
  comparison -- the direction that flatters keel's existing prior).
* **The regulatory term is computed, not estimated**: `keel_broker_alpaca.fees` already encodes
  SEC Section 31 and FINRA TAF from published formulas, with provenance.
* **Primary quantity: total round-trip friction in basis points**, equities vs crypto, and
  against what keel's model charges each today.
* **Pre-declared expectation**: equities friction lands one to two ORDERS OF MAGNITUDE below
  crypto's ~250bp, and keel's current equities pricing is wrong in BOTH directions at once --
  under-charging fees (0 instead of the pass-throughs) and over-charging slippage (the 5bp
  floor of a crypto-anchored model, on books far deeper than its $500M/day anchor).
* **What would refute it**: an equities spread estimate at or above 50bp, or a regulatory term
  material against the spread. Either would mean commission-free is a marketing claim rather
  than a cost structure, and the equities profile would need the same null treatment as crypto.

This measures COST ONLY. It makes no claim about edge on either asset class, and nothing here
should be read as one -- the crypto intersection is empty, and the equity rules have never been
measured at all.

Re-run:
    KEEL_EQUITIES_DB=~/keel/keel-equities.db KEEL_CRYPTO_DB=~/keel/keel.db \
      python docs/experiments/2026-09-02-equities-cost-fidelity.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import statistics
from decimal import Decimal
from pathlib import Path

from keel_broker_alpaca.fees import estimate_regulatory_fees
from keel_core.types import Candle, Side

from keel.research.spread import corwin_schultz_spread
from keel.strategy.backtest import SLIPPAGE_FLOOR_PCT, TAKER_FEE_PCT, slippage_for_quote_volume

EQUITIES_DB = os.environ.get("KEEL_EQUITIES_DB") or str(Path.home() / "keel" / "keel-equities.db")
CRYPTO_DB = os.environ.get("KEEL_CRYPTO_DB") or str(Path.home() / "keel" / "keel.db")
OUT_DIR = Path(os.environ.get("KEEL_EXPERIMENT_OUT") or Path(__file__).resolve().parent / "_out")
OUT_DIR.mkdir(parents=True, exist_ok=True)
JSONL_PATH = OUT_DIR / "equities_cost_fidelity.jsonl"

EQUITIES = ["MSFT-USD", "AAPL-USD", "GOOGL-USD", "NVDA-USD", "COST-USD"]
CRYPTO = [
    "BTC-USD", "ETH-USD", "ADA-USD", "LINK-USD", "LTC-USD", "SOL-USD",
    "XLM-USD", "PAXG-USDT", "BCH-USD", "AAVE-USD", "DOGE-USD", "DOT-USD",
    "UNI-USD", "ZEC-USD", "ALGO-USD", "FET-USD", "CRV-USD", "ICP-USD",
    "AVAX-USD", "NEAR-USD", "XRP-USD", "PAXG-USD", "WLD-USD", "TON-USD",
]

#: The order size the regulatory term is expressed at. keel's equities profile caps exposure at
#: $5,000 over five names at flat 20% weights, so ~$1,000 is the position this account actually
#: takes -- and the per-share TAF makes the answer size-dependent, so a size must be named.
ORDER_USD = Decimal("1000")


def load(db: str, product_id: str) -> list[Candle]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT ts, o, h, l, c, v FROM candles WHERE product_id=? AND granularity='ONE_DAY'"
            " ORDER BY ts",
            (product_id,),
        ).fetchall()
    finally:
        con.close()
    return [
        Candle(ts=r[0], open=Decimal(r[1]), high=Decimal(r[2]), low=Decimal(r[3]),
               close=Decimal(r[4]), volume=Decimal(r[5]))
        for r in rows
    ]


def median_daily_quote_volume(candles: list[Candle]) -> Decimal:
    """The statistic `slippage_for_quote_volume` reads. These are ONE_DAY bars, so the
    per-bar median IS the daily median -- no bars-per-day scaling, and none of the unit trap
    that bit the triple-barrier work."""
    if not candles:
        return Decimal("0")
    return Decimal(str(statistics.median(float(c.volume * c.close) for c in candles)))


def measure(db: str, product_id: str, asset_class: str) -> dict[str, object] | None:
    candles = load(db, product_id)
    if len(candles) < 2:
        return None
    est = corwin_schultz_spread(candles)
    naive = corwin_schultz_spread(candles, block_size=None)
    if est is None or naive is None:
        return None
    adv = median_daily_quote_volume(candles)
    modelled_slip = slippage_for_quote_volume(adv)
    last_close = candles[-1].close

    # The regulatory term, at the size this account trades. Sells only: a BUY pays nothing.
    shares = ORDER_USD / last_close if last_close > 0 else Decimal("0")
    reg_total, sec31, taf = (
        estimate_regulatory_fees(Side.SELL, shares, ORDER_USD)
        if asset_class == "equities"
        else (Decimal("0"), Decimal("0"), Decimal("0"))
    )
    reg_bp = (reg_total / ORDER_USD * 10000) if ORDER_USD else Decimal("0")

    # What keel charges this product TODAY, round trip: two legs of commission/fee plus two
    # legs of slippage. Equities run at `taker_pct: 0.0`; crypto at the 1.2% taker rate.
    modelled_fee = Decimal("0") if asset_class == "equities" else TAKER_FEE_PCT
    modelled_rt_bp = (2 * modelled_fee + 2 * modelled_slip) * 10000

    # What it plausibly costs. THE COMMISSION IS NOT AN ESTIMATE -- 1.2%/leg on Coinbase and
    # $0 on Alpaca are published rates actually charged, so they belong in the measured total
    # beside the estimated spread. Omitting them would compare an equities total that is
    # ~all spread against a crypto total that is ~all fee, and understate crypto 5-fold.
    commission_bp = (Decimal("0") if asset_class == "equities" else 2 * TAKER_FEE_PCT) * 10000
    measured_rt_bp = commission_bp + 2 * est.half_spread_bp + reg_bp

    return {
        "product_id": product_id,
        "asset_class": asset_class,
        "bars": len(candles),
        "pairs": est.pairs,
        "negative_pair_share": str(est.negative_pair_share),
        "gap_adjusted_pairs": est.gap_adjusted_pairs,
        "spread_bp": str(est.spread_bp),
        "naive_spread_bp": str(naive.spread_bp),
        "raw_spread_bp": str(est.raw_spread_bp),
        "blocks": est.blocks,
        "half_spread_bp": str(est.half_spread_bp),
        "median_daily_quote_volume": str(adv.quantize(Decimal("1"))),
        "modelled_slippage_bp": str((modelled_slip * 10000).quantize(Decimal("0.01"))),
        "modelled_roundtrip_bp": str(modelled_rt_bp.quantize(Decimal("0.01"))),
        "regulatory_bp": str(reg_bp.quantize(Decimal("0.0001"))),
        "sec_section_31_usd": str(sec31.quantize(Decimal("0.000001"))),
        "taf_usd": str(taf.quantize(Decimal("0.000001"))),
        "commission_bp": str(commission_bp.quantize(Decimal("0.01"))),
        "measured_roundtrip_bp": str(measured_rt_bp.quantize(Decimal("0.01"))),
    }


def main() -> None:
    rows: list[dict[str, object]] = []
    for pid in EQUITIES:
        row = measure(EQUITIES_DB, pid, "equities")
        if row:
            rows.append(row)
    for pid in CRYPTO:
        row = measure(CRYPTO_DB, pid, "crypto")
        if row:
            rows.append(row)

    with JSONL_PATH.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    hdr = (f"{'product':<12}{'bars':>6}{'spread bp':>11}{'naive bp':>10}{'raw bp':>10}"
           f"{'neg share':>11}{'gaps':>7}{'model rt bp':>13}{'measured rt bp':>16}")
    for klass in ("equities", "crypto"):
        sub = [r for r in rows if r["asset_class"] == klass]
        print(f"\n=== {klass} ({len(sub)}) ===")
        print(hdr)
        for r in sorted(sub, key=lambda r: Decimal(str(r["spread_bp"]))):
            print(
                f"{r['product_id']:<12}{r['bars']:>6}{r['spread_bp']:>11}{r['naive_spread_bp']:>10}"
                f"{r['raw_spread_bp']:>10}"
                f"{r['negative_pair_share']:>11}{r['gap_adjusted_pairs']:>7}"
                f"{r['modelled_roundtrip_bp']:>13}{r['measured_roundtrip_bp']:>16}"
            )
        meas = sorted(Decimal(str(r["measured_roundtrip_bp"])) for r in sub)
        modl = sorted(Decimal(str(r["modelled_roundtrip_bp"])) for r in sub)
        print(f"  median measured round trip: {statistics.median(meas):>10} bp")
        print(f"  median modelled round trip: {statistics.median(modl):>10} bp")

    eq = [r for r in rows if r["asset_class"] == "equities"]
    cr = [r for r in rows if r["asset_class"] == "crypto"]
    eq_med = statistics.median(sorted(Decimal(str(r["measured_roundtrip_bp"])) for r in eq))
    cr_med = statistics.median(sorted(Decimal(str(r["measured_roundtrip_bp"])) for r in cr))
    print(f"\ncrypto / equities measured round-trip ratio: {(cr_med / eq_med):.1f}x")
    print(f"equities: modelled {statistics.median(sorted(Decimal(str(r['modelled_roundtrip_bp'])) for r in eq))} bp"
          f" vs measured {eq_med} bp")
    print(f"floor reference: SLIPPAGE_FLOOR_PCT = {SLIPPAGE_FLOOR_PCT * 10000} bp one way")
    for klass, sub in (("equities", eq), ("crypto", cr)):
        blocked = statistics.median(sorted(Decimal(str(r["spread_bp"])) for r in sub))
        naive = statistics.median(sorted(Decimal(str(r["naive_spread_bp"])) for r in sub))
        print(f"aggregation bias, {klass}: naive {naive} bp vs blocked {blocked} bp"
              f" ({naive / blocked:.1f}x)" if blocked else f"{klass}: blocked is zero")
    print(f"\nwrote {JSONL_PATH}")


if __name__ == "__main__":
    main()
