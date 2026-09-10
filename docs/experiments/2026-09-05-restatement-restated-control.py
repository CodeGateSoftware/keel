"""Sec.2's control: is it the ENGINE that moved the 08-13 numbers, or the candles?

`~/keel/keel.db` has grown 565 ONE_HOUR bars on the products it kept current since the 08-13
restatement ran. A naive re-run absorbs that silently and reports it as an engine effect. This
driver separates the two by running each of 08-13 sec.3's printed cells three ways:

* as 08-13 printed it (the `PRINTED` table below, transcribed from that document);
* on TODAY's engine over the 08-13 CORPUS -- candles truncated to `ts < 2026-08-13`;
* on TODAY's engine over the FULL corpus.

The middle column isolates every engine change since (#442's gap-through-stop exit fill and its
ratchet exit policy, #523's re-derived slippage cap). The gap between the middle and right
columns is the new data. Slippage is held at the flat 5bp floor throughout, because that is what
the 08-13 figures were priced at -- this is a like-for-like bridge, not a cost restatement, and
the cost restatement is the main driver's Arm A.

Result (2026-09-05): three cells reproduce BIT-IDENTICALLY and the two that move differ only by
the trades the new candles added. Both engine changes are inert for these rules -- `turtle_breakout`
carries a static stop, and neither rule declares `trail_atr_mult`/`be_roll_rr`, so the ratchet is
identity by construction.

Re-run:
    KEEL_EXPERIMENT_DB=~/keel/keel.db \
      python docs/experiments/2026-09-05-restatement-restated-control.py
"""

import datetime
import os
from decimal import Decimal
from pathlib import Path

DB = os.environ.get("KEEL_EXPERIMENT_DB") or str(Path.home() / "keel" / "keel.db")

#: The 08-13 corpus boundary. An APPROXIMATION of that run's fetch state, and stated as one: the
#: deployment fetches products on different days, so truncating at midnight UTC on the document's
#: own date is the closest reconstruction available from a database that keeps no fetch history.
#: The three exact identities it produces are what justify it -- a boundary that were materially
#: wrong could not reproduce three cells to the digit.
CUTOFF_TS = int(datetime.datetime(2026, 8, 13, tzinfo=datetime.UTC).timestamp())

#: Transcribed from `2026-08-13-restated-under-a-production-faithful-engine.md` sec.3:
#: (n, gross PF at fee 0, net at the 0.6% maker rate, net at the 1.2% taker rate).
PRINTED = {
    ("turtle", "ZEC-USD"): (268, 1.442, 0.968, 0.685),
    ("turtle", "XRP-USD"): (157, 1.223, 0.688, 0.438),
    ("turtle", "FET-USD"): (269, 1.206, 0.825, 0.599),
    ("turtle", "PAXG-USDT"): (238, 1.145, 0.194, 0.051),
    ("turtle", "CRV-USD"): (260, 1.017, 0.648, 0.445),
    ("pullback", "ZEC-USD"): (170, 1.044, 0.344, 0.144),
}
FEES = ["0", "0.006", "0.012"]
FLAT_SLIPPAGE = Decimal("0.0005")


def main():
    from keel_core.types import Granularity

    from keel.data.db import connect
    from keel.data.repository import Repository
    from keel.strategy import backtest as bt
    from keel.strategy.rules.pullback_continuation import PullbackContinuation
    from keel.strategy.rules.turtle_breakout import TurtleBreakout

    repo = Repository(connect(DB))
    header = (
        "cell",
        "n 0813",
        "n cut",
        "n full",
        "g 0813",
        "g cut",
        "g full",
        "mk 0813",
        "mk cut",
        "mk full",
        "tk 0813",
        "tk cut",
        "tk full",
    )
    print(("{:22}" + "{:>8}" * 12).format(*header))

    for (kind, product), (n0, gross0, maker0, taker0) in PRINTED.items():
        full = repo.get_candles(product, Granularity.ONE_HOUR)
        cut = [c for c in full if c.ts < CUTOFF_TS]
        rule_cls = TurtleBreakout if kind == "turtle" else PullbackContinuation
        out = {}
        for tag, candles in (("cut", cut), ("full", full)):
            for fee in FEES:
                result = bt.backtest(
                    rule_cls(product_id=product),
                    candles,
                    fee_pct=Decimal(fee),
                    slippage_pct=FLAT_SLIPPAGE,
                )
                out[(tag, fee)] = (int(result.n_trades), float(result.profit_factor))
        print(
            ("{:22}" + "{:8}" * 3 + "{:8.3f}" * 9).format(
                f"{kind} {product}",
                n0,
                out[("cut", "0")][0],
                out[("full", "0")][0],
                gross0,
                out[("cut", "0")][1],
                out[("full", "0")][1],
                maker0,
                out[("cut", "0.006")][1],
                out[("full", "0.006")][1],
                taker0,
                out[("cut", "0.012")][1],
                out[("full", "0.012")][1],
            )
        )
        print(f"    bars: cut={len(cut)} full={len(full)} (+{len(full) - len(cut)})")


if __name__ == "__main__":
    main()
