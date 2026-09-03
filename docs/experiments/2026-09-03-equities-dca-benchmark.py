"""The DCA benchmark on equities -- issue #371 (Alpaca Phase C), second half.

**What a benchmark is for.** The crypto side has one: every strategy claim keel has ever made
is read against what simply accumulating would have done over the same bars, because a rule
that underperforms scheduled buying has not earned the complexity it costs. Equities had no
such baseline, so any future equity result would have had nothing to be compared against and
would inevitably have been compared against zero. This run supplies it.

It follows [the cost-fidelity measurement](2026-09-02-equities-cost-fidelity.md), in that
order, deliberately: the PRD (§6.3, O4) makes the cost document a precondition -- "no strategy
claim is believed before it" -- and a DCA sleeve priced wrong is exactly the kind of claim it
was protecting against.

DECLARED BEFORE THE RUN:

* **Metric: terminal sleeve mark-to-market against deployed cost basis.** NEVER a profit
  factor. The DCA sleeve never closes (`sim/portfolio_sim` accumulates into `dca_positions`
  and never exits them), so there are no round trips to factor, and DCA is exempt from the
  promotion gate by design. This matches the crypto DCA measurement of 2026-08-17 exactly, so
  the two are readable side by side.
* **Constructor defaults, no sweep**: `cadence_days=7, budget_usd=50, dip_bonus_pct=0,
  lookback_days=90`. A benchmark that has been tuned is not a benchmark.
* **Three cost arms, same bars**, because the point of the preceding measurement was that the
  price you assume decides what you conclude:
    - `measured` -- the 2026-09-02 figure: 0% commission (Alpaca's real rate) and the
      per-ticker measured half-spread as one-way slippage.
    - `keel_today` -- what keel charges this profile right now: 0% commission and
      `slippage_for_quote_volume` on the cached (IEX-only) volume, ~7.5x the measured cost.
    - `crypto_regime` -- the SAME equity bars priced at Coinbase's cost structure (1.2%/leg,
      per-product slippage). Counterfactual by construction and labelled as such: it isolates
      how much of the crypto null is the venue rather than the series.
* **The sell-side regulatory pass-throughs do not appear, and that is not an omission.** SEC
  Section 31 and FINRA TAF are levied on SELLS; a DCA sleeve only ever buys. Its entire cost
  is spread.
* **Pre-declared expectation**: the three arms differ by very little in the `measured` vs
  `keel_today` comparison (a low-turnover sleeve pays the spread ~260 times over five years,
  not thousands), and by a lot under `crypto_regime`. If the first pair DOES differ materially,
  the cost model's equities error is not confined to strategy evaluation and reaches the
  passive baseline too.
* **What would refute the benchmark's usefulness**: a sleeve whose terminal value is dominated
  by one ticker, making "beat DCA" a statement about NVDA rather than about equities. Reported
  per ticker for exactly that reason.

**This is a benchmark, not a recommendation.** It says what accumulation did on five liquid
US large caps over one particular five-year window that contained a historic mega-cap run. It
is not evidence that accumulation works, is not advice to accumulate, and its window is short
enough and its universe narrow enough that the number is a yardstick, not a finding.

Re-run:
    KEEL_EQUITIES_DB=~/keel/keel-equities.db \
      python docs/experiments/2026-09-03-equities-dca-benchmark.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import statistics
from decimal import Decimal
from pathlib import Path

from keel_core.types import Candle, Granularity

from keel.research.spread import corwin_schultz_spread
from keel.strategy.backtest import TAKER_FEE_PCT, slippage_for_quote_volume
from keel.strategy.rules.dca import Dca

DB = os.environ.get("KEEL_EQUITIES_DB") or str(Path.home() / "keel" / "keel-equities.db")
OUT_DIR = Path(os.environ.get("KEEL_EXPERIMENT_OUT") or Path(__file__).resolve().parent / "_out")
OUT_DIR.mkdir(parents=True, exist_ok=True)
JSONL_PATH = OUT_DIR / "equities_dca_benchmark.jsonl"

TICKERS = ["MSFT-USD", "AAPL-USD", "GOOGL-USD", "NVDA-USD", "COST-USD"]

#: WHY NOT `sim/portfolio_sim`, WHICH THE CRYPTO DCA MEASUREMENT USED. That harness iterates
#: ONE_HOUR bars (`_window_bars` is `history_days * 24`, mirroring the live agent's hourly
#: account pass), and the equities profile is ONE_DAY only -- by configuration, because Alpaca
#: mints hourly bars only inside a session and the daily turtle rules never read them. Handing
#: it daily bars labelled as hourly would produce numbers that look right and mean nothing.
#:
#: So the loop is here, and it is deliberately the smallest thing that can be faithful: walk
#: the daily series, ask the SHIPPED `Dca` rule (not a reimplementation of it) whether this bar
#: is a cadence hit, and fill any Setup at the NEXT bar's open plus one-way slippage -- keel's
#: own market-order convention since #258, and what `execution/executor.py` really places.
#: There is no exit path to model: DCA never sells, which is also why the sell-side regulatory
#: fees never enter.
_FILL_CONVENTION = "next bar open + one-way slippage, market order (#258)"


def load(product_id: str) -> list[Candle]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
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


def arm_costs(product_id: str, candles: list[Candle]) -> dict[str, tuple[Decimal, Decimal]]:
    """`(fee_pct, slippage_pct)` per arm, per ticker. Slippage is a ONE-WAY number both here and
    in `slippage_for_quote_volume`, so the measured HALF-spread -- the cost of crossing from the
    mid to the touch on one leg -- is the like-for-like quantity."""
    est = corwin_schultz_spread(candles)
    assert est is not None, product_id
    adv = Decimal(str(statistics.median(float(c.volume * c.close) for c in candles)))
    modelled = slippage_for_quote_volume(adv)
    return {
        "measured": (Decimal("0"), est.half_spread_pct),
        "keel_today": (Decimal("0"), modelled),
        "crypto_regime": (TAKER_FEE_PCT, modelled),
    }


def run_arm(product_id: str, candles: list[Candle], fee: Decimal, slip: Decimal) -> dict:
    """Accumulate the shipped `Dca` rule over the series and mark the sleeve at the last close.

    No cash constraint is imposed. The benchmark measures what the SCHEDULE accumulates; an
    account that ran dry would be measuring the contribution rate instead, which is a different
    question and not one a benchmark should silently answer.
    """
    rule = Dca(product_id=product_id)
    qty = Decimal("0")
    deployed = Decimal("0")
    fees_paid = Decimal("0")
    buys = 0
    # `i` is the DECIDING bar; the fill lands on `i + 1`, so the last bar can never be a
    # decision -- there is no next open to fill against. No lookahead anywhere.
    for i in range(len(candles) - 1):
        setup = rule.detect({Granularity.ONE_DAY: candles[: i + 1]})
        if setup is None:
            continue
        size_usd = setup.context["size_usd"]
        fill_price = candles[i + 1].open * (Decimal("1") + slip)
        if fill_price <= 0:
            continue
        fee_usd = size_usd * fee
        qty += size_usd / fill_price
        deployed += size_usd + fee_usd
        fees_paid += fee_usd
        buys += 1

    last_close = candles[-1].close
    market_value = qty * last_close
    gain = ((market_value / deployed - 1) * 100) if deployed > 0 else Decimal("0")
    return {
        "buys": buys,
        "qty": str(qty.quantize(Decimal("0.00000001"))),
        "deployed": str(deployed.quantize(Decimal("0.01"))),
        "fees_paid": str(fees_paid.quantize(Decimal("0.01"))),
        "market_value": str(market_value.quantize(Decimal("0.01"))),
        "gain_pct": str(gain.quantize(Decimal("0.01"))),
    }


def main() -> None:
    rows: list[dict] = []
    for product_id in TICKERS:
        candles = load(product_id)
        costs = arm_costs(product_id, candles)
        for arm, (fee, slip) in costs.items():
            row = run_arm(product_id, candles, fee, slip)
            row |= {
                "product_id": product_id,
                "arm": arm,
                "bars": len(candles),
                "fee_pct": str(fee),
                "slippage_bp": str((slip * 10000).quantize(Decimal("0.01"))),
            }
            rows.append(row)

    with JSONL_PATH.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    for arm in ("measured", "keel_today", "crypto_regime"):
        sub = [r for r in rows if r["arm"] == arm]
        print(f"\n=== {arm} ===")
        print(f"{'ticker':<12}{'buys':>6}{'slip bp':>9}{'deployed':>13}{'value':>13}{'gain %':>10}")
        for r in sub:
            print(f"{r['product_id']:<12}{r['buys']:>6}{r['slippage_bp']:>9}{r['deployed']:>13}"
                  f"{r['market_value']:>13}{r['gain_pct']:>10}")
        dep = sum(Decimal(r["deployed"]) for r in sub)
        val = sum(Decimal(r["market_value"]) for r in sub)
        pooled = ((val / dep - 1) * 100) if dep > 0 else Decimal("0")
        print(f"{'POOLED':<12}{'':>6}{'':>9}{dep.quantize(Decimal('0.01')):>13}"
              f"{val.quantize(Decimal('0.01')):>13}{pooled.quantize(Decimal('0.01')):>10}")
    print(f"\nwrote {JSONL_PATH}")


if __name__ == "__main__":
    main()
