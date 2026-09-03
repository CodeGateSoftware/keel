# 2026-09-03: The equities DCA benchmark (#371, Alpaca Phase C)

**The benchmark to beat is the MEDIAN TICKER at +70.62%, not the pooled sleeve at +145.10%.**
The pre-declared refutation condition fired: NVDA returned +449.35% and is 44.8% of the
terminal sleeve, so the pooled figure is very largely one name. Excluding it, the remaining
four returned +69.03% — within 1.6 percentage points of the median, which is why the median is
the honest summary.

Any future equity strategy claim is read against that number over these bars. A rule that
returns less than simply buying $50 of each name every Thursday has not earned the complexity
it costs.

**This is a benchmark, not a recommendation and not advice.** It says what accumulation did on
five liquid US large caps over one five-year window that happened to contain a historic
mega-cap run. It is not evidence that accumulation works, and the window is short enough and
the universe narrow enough that the number is a yardstick, nothing more.

## What was measured, on what

- Data: `keel-equities.db`, 5 tickers × 1,253 ONE_DAY bars (Alpaca IEX feed).
- Rule: the **shipped** `keel.strategy.rules.dca.Dca` at constructor defaults —
  `cadence_days=7, budget_usd=50, dip_bonus_pct=0, lookback_days=90`. Not a reimplementation,
  and not tuned: a benchmark that has been swept is not a benchmark.
- Fills: **next bar's open plus one-way slippage**, keel's market-order convention since #258
  and what `execution/executor.py` actually places. The deciding bar is always strictly before
  the filling bar, so there is no lookahead.
- 250 buys per ticker (weekly cadence lands on Thursdays — `Dca.detect` tests epoch-day
  divisibility, not bar count), $12,500 deployed per ticker.
- Driver: [`2026-09-03-equities-dca-benchmark.py`](2026-09-03-equities-dca-benchmark.py).

**Why not `sim/portfolio_sim`, which the crypto DCA measurement used.** That harness iterates
ONE_HOUR bars — `_window_bars` is `history_days * 24`, mirroring the live agent's hourly account
pass — and the equities profile is ONE_DAY only, by configuration, because Alpaca mints hourly
bars only inside a session and the daily turtle rules never read them. Handing it daily bars
labelled as hourly would have produced numbers that looked right and meant nothing. The
accumulation loop is therefore in the driver, and is the smallest thing that can be faithful.

## Result — the `measured` arm

| ticker | buys | deployed | terminal value | gain | share of sleeve |
| --- | --- | --- | --- | --- | --- |
| NVDA | 250 | $12,500.00 | $68,668.85 | **+449.35%** | 44.8% |
| GOOGL | 250 | $12,500.00 | $27,862.12 | +122.90% | 18.2% |
| AAPL | 250 | $12,500.00 | $21,327.77 | +70.62% | 13.9% |
| MSFT | 250 | $12,500.00 | $17,788.07 | +42.30% | 11.6% |
| COST | 250 | $12,500.00 | $17,537.84 | +40.30% | 11.4% |
| **median ticker** | | | | **+70.62%** | |
| pooled | 1,250 | $62,500.00 | $153,184.65 | +145.10% | |
| pooled, excluding NVDA | 1,000 | $50,000.00 | $84,515.80 | +69.03% | |

The spread between +40% and +449% across five liquid mega-caps over identical bars is itself
worth registering. Whatever a strategy on this universe is measuring, ticker selection is a
larger term than anything a rule is likely to add.

## The three cost arms — and why they barely differ

| arm | commission | slippage | pooled gain | vs measured |
| --- | --- | --- | --- | --- |
| `measured` | 0% (Alpaca's real rate) | measured half-spread, 0.64–2.25bp | +145.10% | — |
| `keel_today` | 0% | `slippage_for_quote_volume`, 6.45–16.78bp | +144.92% | −0.18pp |
| `crypto_regime` | 1.2%/leg | as above | +142.02% | −3.08pp |

**keel's 7.5× equities cost error is worth 0.18 percentage points over five years here.** That
is the pre-declared expectation, confirmed, and it is the most useful thing in this document.
A DCA sleeve pays its spread 250 times, one way, and never exits. The cost regime that
annihilated every crypto strategy result barely scratches it — even the fully counterfactual
`crypto_regime` arm, charging Coinbase's 1.2% per leg on equity bars, costs only 3.08pp.

This is the same finding the crypto work reached from the other side, and it is worth stating
plainly because it is easy to get backwards: **cost is levied on the SEARCH, not on the edge.**
An active rule pays the toll on every one of its round trips, and there is no edge in the
measured library large enough to survive ~241 of them at 2.5% each. Accumulation pays it 250
times too — but one way, on a position it never closes, so the toll is a rounding error against
a five-year hold. Cheap execution does not make a strategy good; expensive execution makes a
mediocre one impossible.

## What this does and does not license

It licenses the comparison: future equity results are read against **+70.62% median ticker**
over these bars, at the `measured` cost regime.

It licenses nothing about DCA. The sleeve's return is what these five names did in this window,
levered by nothing and skilled at nothing. A different five names, or the same five over
2000–2005, would produce a different number, and the concentration above shows how little it
takes to move it.

It says nothing about whether any keel rule can beat it, because no keel rule has yet been
measured on equities at all. That measurement is the next step and is deliberately not in this
document — running it here, against a benchmark computed in the same file, is precisely how a
result gets chosen after the fact.

One caveat inherited whole from [the cost-fidelity measurement](2026-09-02-equities-cost-fidelity.md):
the `keel_today` arm's slippage is computed from the cached IEX-only volume statistic, which is
structurally unable to report consolidated volume (#696). That arm is therefore "what keel
charges today", which is what it is labelled — not "what a correct volume-keyed model would
charge".
