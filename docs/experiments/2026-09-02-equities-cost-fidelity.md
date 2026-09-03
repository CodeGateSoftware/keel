# 2026-09-02: What a round trip costs on a commission-free venue (#371, Alpaca Phase C)

**Headline: a round trip costs ~2.2bp on Alpaca equities and ~306bp on Coinbase spot — a
factor of 141.** keel's cost model is close to right on crypto (it charges 332bp against a
measured 306bp, conservative by 8%) and wrong by 7.5× on equities, where it charges 16.4bp
against a measured 2.2bp. Both errors are now explained, and the equities one has a specific
cause: the equities profile runs on a single-venue (IEX) feed, so keel's liquidity statistic
reads MSFT as a $186M/day product and prices it as thinner than the model's reference liquidity.

The PRD makes this a precondition — "no strategy claim is believed before it" (§6.3, O4) — and
that ordering earned its keep. `config.paper-equities.yaml` ships `taker_pct: 0.0`, honestly
labelled as awaiting this work. Priced literally, that models a free venue, and a free venue
makes everything look profitable.

**This measures COST ONLY.** It makes no claim about edge on either asset class. The crypto
intersection is empty; the equity rules have never been measured at all. Nothing below changes
either fact, and a cheaper venue is not an edge.

## What was measured, on what

- Data: the deployment's own cached ONE_DAY candles — `keel-equities.db` (5 tickers × 1,253
  bars, Alpaca IEX feed) and `keel.db` (24 assets, 248–1,871 bars, Coinbase).
- Spread: `keel.research.spread.corwin_schultz_spread` — Corwin & Schultz (2012), *"A Simple
  Way to Estimate Bid-Ask Spreads from Daily High and Low Prices"*, Journal of Finance 67(2),
  719–759. **One estimator, both asset classes, from data already on disk.** A comparison
  measured two different ways would be an artifact of the methods rather than a finding about
  the venues, and buying a quote history would make the number unreproducible by a reader.
- Overnight-gap adjustment ON (§I.B of the paper). Equities gap nightly; crypto trades
  continuously. Leaving it off inflates the equities side of exactly this comparison — the
  direction that flatters keel's existing crypto-heavy prior.
- Commission and regulatory fees are **computed, not estimated**: 1.2%/leg is Coinbase's
  published taker rate, $0 is Alpaca's published commission, and the sell-side pass-throughs
  come from `keel_broker_alpaca.fees`, whose constants carry their own provenance.
- Driver: [`2026-09-02-equities-cost-fidelity.py`](2026-09-02-equities-cost-fidelity.py).

## The result

Round trip = commission (both legs) + spread (half per leg) + regulatory (sell only).

| ticker | bars | spread bp | naive bp | raw bp | reg bp | measured RT bp | keel charges bp |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GOOGL | 1253 | 1.29 | 44.83 | -44.21 | 0.2339 | **1.52** | 18.46 |
| COST | 1253 | 1.36 | 34.81 | -27.66 | 0.2308 | **1.59** | 33.57 |
| MSFT | 1253 | 1.94 | 41.82 | -32.21 | 0.2323 | **2.17** | 16.38 |
| AAPL | 1253 | 2.83 | 43.55 | -32.96 | 0.2341 | **3.06** | 15.21 |
| NVDA | 1253 | 4.50 | 77.48 | -63.29 | 0.2364 | **4.74** | 12.90 |

| | median spread | median measured RT | median keel charges | model error |
| --- | --- | --- | --- | --- |
| equities (5) | 1.94bp | **2.17bp** | 16.38bp | **7.5× too high** |
| crypto (24) | 66.31bp | **306.31bp** | 332.27bp | 1.08× (conservative) |

**Ratio: 141×.** The pre-declared expectation was "one to two orders of magnitude"; the
measurement landed inside it. The refutation condition was an equities spread at or above 50bp
or a material regulatory term — neither occurred. The regulatory term is real but negligible at
this account's size: on a $1,000 sell, SEC Section 31 is $0.0229 and FINRA TAF is $0.000333,
together **0.23bp**, about a tenth of the spread.

## Finding 1 — the aggregation choice is worth 22×, and the obvious one is wrong

This is the part that nearly produced a wrong document. The first run of this measurement
reported equities at 43.6bp and a ratio of 4.1×, and it was the *plausibility* of MSFT quoting
41.8bp — roughly twenty times the penny it actually quotes — that exposed the error rather than
any test.

Corwin-Schultz two-day estimates go negative often, and no venue charges a negative spread, so
they must be handled. The tempting treatment is to floor every pair at zero and average the
survivors. On a series whose true spread is small relative to its volatility, the estimate is
symmetric noise about zero, and keeping the positive half while discarding the negative half
makes the mean converge on E[max(X,0)] > 0. **It reports a spread that is not there, and
reports a larger one the more volatile the series is.** The `raw bp` column is the giveaway:
every equity's unfloored mean is strongly NEGATIVE, which is the honest statement that the
series carries no spread the estimator can resolve at daily frequency.

Corwin & Schultz's own procedure averages within a month and floors the *monthly* mean, so the
negatives cancel against the positives inside the block instead of being discarded. Same data,
same estimator:

| | naive (floor each pair) | blocked (floor each month) | ratio |
| --- | --- | --- | --- |
| equities | 43.55bp | 1.94bp | **22.4×** |
| crypto | 177.74bp | 66.31bp | 2.7× |

The bias is worst exactly where the true spread is smallest — which is to say, exactly on the
asset class this measurement exists to price. `keel.research.spread` keeps both aggregations
reachable, because the comparison between them is itself the finding, and the biased one is
what makes the bias demonstrable rather than folklore.

## Finding 2 — keel reads MSFT as a thin asset, because a single-venue feed is not the market

`slippage_for_quote_volume` keys off `median_daily_quote_volume` computed from cached candles,
against a $500M/day anchor with a 5bp floor. What it sees:

| ticker | cached median daily quote volume | modelled slippage |
| --- | --- | --- |
| MSFT | $186,462,740 | 8.19bp |
| AAPL | $216,061,742 | 7.61bp |
| GOOGL | $146,760,641 | 9.23bp |
| NVDA | $300,409,740 | 6.45bp |
| COST | $44,370,750 | 16.78bp |

Every one sits below the anchor, so every one is priced as *thinner than the model's reference
liquidity* — including MSFT, which is not a thin instrument by any reading.

The mechanism does not need a market-share statistic to establish, and is stated structurally
here for that reason. **The equities profile runs on Alpaca's IEX feed by configuration, and
IEX is one exchange among the many US venues (plus off-exchange execution) that make up
consolidated volume.** A single-venue feed therefore reports a fraction of consolidated volume
BY CONSTRUCTION, however faithfully it reports its own executions. Any statistic that treats
that fraction as though it were the whole market will understate liquidity by whatever that
venue's share happens to be, on every symbol, silently — because nothing in the pipeline
distinguishes "this product is thin" from "this feed sees part of the market".

For scale, and flagged as an order-of-magnitude expectation rather than a measurement: IEX
publishes its own overall share as roughly 3.8% for Q2 2026 ([iex.io](https://www.iex.io/news),
the operator's own figure, not independently verified here), and the cached MSFT series is a
low-single-digit percentage of the consolidated volume a mega-cap of that size is generally
understood to trade. **This document does not measure either quantity**, and no number in the
tables above depends on them — the finding is that the statistic is structurally unable to
answer the question, not that it is off by a specific factor. Establishing the actual factor is
part of #696, not of this document.

This is the SIP-vs-IEX data-tier implication #371 asked about, and it is more consequential than
the fee question it was filed beside. Two consequences worth separating:

1. **For cost modelling** (this document): the 5bp floor is the binding term for mega-caps
   anyway, and even the floor is ~2.5× the measured 1.94bp spread. Both the floor and the
   volume statistic are crypto-shaped.
2. **For anything else keyed on volume** — liquidity screens, admission floors, the asset
   scout's `--probe-liquidity` — the same understatement applies, and a genuinely liquid
   equity could be refused admission for thinness it does not have. That direction fails
   closed, so it is safe — but safe for the wrong reason, and it will misinform any equities
   universe decision. Filed as **#696**; nothing in this document changes it.

> **Amendment (2026-09-03), per #696.** Records are appended to, never rewritten, so nothing
> above has been altered — but the framing of this finding was sharper than the evidence, and
> the resolution improved on it.
>
> Two claims here were asserted rather than measured: that the cached figure is "roughly 2% …
> approximately IEX's share of US equity volume", and that the understatement is "~50×". IEX
> publishes its own overall share as roughly 3.8% for Q2 2026, so the 2% was simply wrong, and
> neither figure was derived from anything in the tables above. **No number in this document
> depends on either**; the finding is that the statistic is structurally unable to answer the
> question asked of it, which needs no percentage at all.
>
> What shipped is better than what this section proposed. The fix is not a fail-closed refusal
> of every partial-feed series — that would have made a data-vendor pricing tier a prerequisite
> for running the engine, and would have banned MSFT and AAPL for thinness they do not have.
> It is an **asymmetric lower-bound gate**: venue volume is a lower bound on consolidated
> volume, so at or above the admission floor a partial feed is CONCLUSIVE, while below it the
> screen refuses as `liquidity_unmeasured` rather than asserting an asset is thin. The bound
> holds for any venue share below 100%, so no percentage is encoded anywhere in the code —
> `keel/data/feed_scope.py` carries the argument, and a test greps its body to keep a market
> share from ever being multiplied into a volume statistic.
>
> Provenance is now recorded per series at fetch time (`candle_series_feed`, schema v17), so
> the feed is no longer inferred from whatever config is loaded when someone reads the series,
> and `doctor`'s `data.feed_scope` reports which cached series carry a bound rather than a
> measurement. Series cached before v17 read as *unrecorded* — deliberately distinct from
> *partial*, since one should be re-fetched and the other may already be consolidated.

## Finding 3 — the crypto model is validated, in passing

332.27bp modelled against 306.31bp measured, conservative by 8%, using an estimator with no
knowledge of Coinbase's fee schedule. The two agree because the fee dominates: 240bp of the
306bp is published commission, and the spread estimate only has to be roughly right for the
total to be. This is the first independent check on keel's crypto cost assumption that does not
reuse keel's own fee constant, and it passes.

Read the crypto spread column on its own terms, though: PAXG at ~20bp and FET at ~113bp is a
5.6× dispersion that a single global slippage figure cannot express. That is the same argument
[the per-product restatement](2026-09-01-per-product-slippage-restatement.md) made from the
volume side, arriving independently from the price side.

## What this does and does not license

It licenses one sentence: **the cost regime that killed every crypto result is not present on
equities.** 2.2bp is not 306bp, and a strategy needing 300bp of gross edge to break even is
playing a different game from one needing 3bp.

It licenses nothing about edge. Every measured rule is negative on crypto for three unrelated
reasons, and only one of them — `turtle_breakout`'s real gross edge destroyed by cost — is even
addressable by a cheaper venue. `pullback_continuation` has essentially no gross edge (median
0.77) and will not acquire one on a different asset class; `rsi_meanrev` fails on sample size.
A 141× cost reduction is a necessary condition for those first-family results, not a sufficient
one, and the equity rules remain entirely unmeasured. The DCA benchmark on these same tickers is
the next step and is deliberately not in this document.

One caveat on the equity numbers specifically: ~150 of each ticker's ~1,250 pairs are
gap-adjusted, and a gap-adjusted pair floors to zero by construction (the adjustment leaves day
2 sitting exactly atop day 1, the most trend-like geometry two ranges can have). The equity
estimates therefore rest on ~1,100 effective pairs, not 1,250. This does not bias them upward —
gap days contribute zero rather than noise — but it widens their uncertainty relative to the
crypto series, which barely gap at all (0–3 pairs each).
