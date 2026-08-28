# The 2026-09-30 pooled forward-trades review — a descriptive report, NOT a pass/fail gate

Run 2026-08-27 over 3 pre-registered profiles: `/Users/elmehdiaitbrahim/keel/keel.db`, `/Users/elmehdiaitbrahim/keel/keel-live.db`, `/Users/elmehdiaitbrahim/keel/keel-paperhourly.db`.
Event tracked in #353; the descriptive reframing is #427's correction of record (discussion #359's corrected
comment). Method: `keel/research/significance.py` with the `n_eff` correction from `keel/research/throughput.py` (design effect 2.57516, #427).

**Preview run on 2026-08-27: the review event re-runs on 2026-09-30 (#353) under this same pre-registration.** The pool below is what exists today, not what the event will see.

## What counts as a pooled trade (pre-registered in the driver's docstring)

- one CLOSED forward round trip, win/loss resolved by the sign of fee-honest net
  pnl: a `trade_outcomes` row (the closed-trade ledger rails 11/16 read, `pnl_net`
  realized and net of fees), or — where the ledger has none — a filled BUY matched
  to a filled SELL of the same profile, product, rule, mode and quantity (FIFO by
  order id), priced with the ledger writer's own formula
  `(exit - entry) * qty - entry fee - exit fee`;
- deduplicated on (profile, product, quantity, exit fill): a round trip the ledger
  already recorded is never counted twice (0 deduped this run);
- excluded and counted instead: OPEN positions (4), unfilled
  or rejected orders (1); a net pnl of exactly zero is a
  SCRATCH and counts toward nothing (0);
- DCA round trips count — their forward P&L is real (`streak.py` records them too) —
  and the composition labels them.

## The pool as it stands

| profile | modes | closed pooled | of which dca | open (excl.) | unfilled (excl.) | stray sells | ledger rows | deduped |
|---|---|---|---|---|---|---|---|---|
| `/Users/elmehdiaitbrahim/keel/keel.db` | paper | 5 | 1 | 1 | 0 | 0 | 0 | 0 |
| `/Users/elmehdiaitbrahim/keel/keel-live.db` | none | 0 | 0 | 3 | 1 | 0 | 0 | 0 |
| `/Users/elmehdiaitbrahim/keel/keel-paperhourly.db` | paper | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| **pooled** | — | **7** | — | 4 | 1 | 0 | — | 0 |

Rules in the pool: `dca` x1, `turtle_breakout` x6.

## The measurement (descriptive — `keel/research/significance.py` at the fees actually paid)

- closed trades n=7 pooled -> 2.72 effective (design effect 2.57516, #427)
- payoff b=0.0000 -> break-even win rate 1.0000; observed 0.0000 -> edge -1.0000 (-100.0 points)
- 95% one-sided lower bound on the edge: -1.0000
- fees as recorded across the pool: 120.0 bp per leg (realized fees over notional traded, both legs — the forward trades' regime is measured, not assumed)
- note: 2 round trip(s) have a recorded close before their recorded open (order id governed the match; the ledger's created_at disagrees with itself)

**at this n_eff (2.72 effective of 7 pooled), this review can only see an edge of 75.4 points or larger (80% power, one-sided 5%)**

## What this report does not say

This is not a pass/fail gate. Nothing here promotes, demotes, or blocks a rule: the
edge, its interval and its z are descriptive measurements of the pool above, and no
verdict is pronounced on them. The only verdict-shaped statement this report makes is
about POWER — the sentence above — because at this n_eff a null result means
"the review could not have seen it", not "there is no edge". That distinction is
#427's entire finding.

The owner's floor decision remains open (#427): keep n=100 pooled as a descriptive
trigger (the sentence above is what that buys), or raise `min_trades` to 259+ pooled
(n_eff 101) before any future review is allowed to be confirmatory.
