# The fill-model hazard in externally-sourced strategies: what to establish before a foreign number means anything here

> **Cost note (added 2026-09-02).** The figures below are priced at the flat 5bp
> slippage floor. [the per-product restatement](2026-09-01-per-product-slippage-restatement.md) later measured that **no
> asset in keel's universe reaches that floor** — the range is 1.1× to 36.8× — so every
> profit factor here is optimistic by roughly 0.09 at the median. **The verdict is
> unaffected:** the correction only ever moves a number *down*, and every result here was
> already negative. Nothing on this page has been rewritten; records are appended to, not
> revised.

**Date:** 2026-08-27 · **Issue:** #529 · **Origin:**
[`docs/superpowers/specs/2026-08-23-strategy-api-expressiveness-prd.md`](../superpowers/specs/2026-08-23-strategy-api-expressiveness-prd.md)
§4.2 ("a warning worth encoding, not a feature").

**This document reports no new measurement.** Every number in it is cited to the experiment
record that produced it. It exists because the hazard it describes is not in any engine — the
engines are correct — but in how a person reads someone else's backtest. Where a strategy
candidate comes from (the [scout skill](../../.claude/skills/keel-asset-scout/SKILL.md), a
paper, another framework's example gallery), the questions below are what make its published
numbers interpretable in keel, and they are cheaper to answer than a port is to write.

---

## 1. The hazard

Strategies published for other frameworks routinely **name their own entry price and assume the
fill**. Jesse's idiom is literally:

```python
def go_long(self):
    entry = self.price
    stop  = entry - self.atr * self.hp['stop_loss_atr_rate']
    qty   = utils.risk_to_qty(self.balance, 2, entry, stop)
    self.buy = qty, entry          # <- a resting order at a chosen price
    self.stop_loss = qty, stop
```

**keel does not work that way, and has measured what the difference costs.** Since #258 (the
fix for #257), `backtest()` fills an entry at the **next bar's open plus slippage**
(`keel/strategy/backtest.py`: *"`candles[i+1].open` plus slippage, the first price obtainable
once the signal exists"*), because that is what `execution/executor.py` actually places live —
`order_type="market"`, `limit_price=None`, an unconditional market IOC. It does not rest an
order and wait for a level; #260 records that the executor discards a rule's conditional entry
price entirely.

Correcting `pullback_continuation` to that fill model — the worked example, from
[`2026-08-13-restated-under-a-production-faithful-engine.md`](2026-08-13-restated-under-a-production-faithful-engine.md)
§3 — **dropped its median gross profit factor from 0.9292 to 0.7736 (§3.1 prints the same pair
as 0.9219 → 0.7736) and doubled its median trade count from 60 to 124** (§3.1: 58 → 124). The
mechanism is legible: `entry = signal_candle.high + buffer_ticks` was a *confirmation
condition*, and a market fill removes it, so the rule takes the trades it was designed to
decline. The doubling *is* the count of those trades.

So: **any externally-sourced strategy ported naively inherits an optimism keel has already
priced.** Its published numbers are not wrong for its own framework; they are unreachable in
this one. This compounds with the cost picture — round-trip friction here is **~2.5% of
notional** at taker (`2 × fee_pct + 2 × slippage_pct`: 2.50% at the 1.2% taker rate, 1.30% at
maker, per
[`2026-08-12-fee-curve-and-rsi-meanrev.md`](2026-08-12-fee-curve-and-rsi-meanrev.md)). A
strategy whose edge depends on getting a chosen price, evaluated in a framework that assumes it
does, is optimistic on **both axes at once** — and neither optimism is visible in the headline
return.

### 1.1 A caveat the worked example must not lose

The 0.92 → 0.77 drop is **not** evidence that resting entries add edge. Both sides of that
comparison lose money gross (0.9219 with the filter, 0.7736 without): the entry condition
separated **bad from worse**, not good from toxic, and the 08-13 record explicitly forbids
citing it as evidence that offset entries add alpha. What it *is* evidence for is #260 —
production silently overrides a rule's stated entry logic, so **the landmine is the next rule
that expresses a condition through its entry price**. The number makes the hazard's size legible;
it does not make the remedy "support resting entries" (that is #333, prerequisite-gated on its
own terms).

## 2. Why a document and not code

There is nothing to enforce. The fill model is already correct — deliberately matched to what
the executor places, a property the Jesse comparison explicitly names as a keel side of the
trade. The hazard is in how a **human** reads someone else's results: a reasonable person sees a
good backtest elsewhere and does not know which questions to ask. The failure mode has already
occurred once at the engine level (#257: two engine defects produced "plausible, internally
consistent output" for the life of the project while 2,712 tests passed); this note is the
reading-side analogue of the invariants the 08-13 record demanded — make the assumption visible
in the ordinary output of an ordinary evaluation, to a reader who is not hunting for it.

## 3. The four questions

Each below: what to establish, why it matters *here* (with keel's own measurements), and what a
wrong answer costs. They are asked **before** porting effort, not after a disappointing
backtest.

### Q1 — What fill model produced the published numbers?

**Establish:** whether entries were resting limit/stop orders at a chosen price or market
orders, and whether fills are assumed at the chosen level. If the strategy's code names an entry
price it expects to get (Jesse's `self.buy = qty, entry`), that is a resting entry.

**Why it matters here:** keel's engine fills market-style at the next bar's open (#258) and its
live executor never rests an order (#260). The worked example above is the measured cost of
ignoring the difference: gross PF 0.9292 → 0.7736 and median trade count 60 → 124 for
`pullback_continuation` — a strategy whose edge lives in entry timing loses that edge entirely,
and *takes extra trades in the process*, because the confirmation it was demanding no longer
gates anything.

**What a wrong answer costs:** the backtest measures a different strategy than the one
published, and the shortfall is invisible — keel's engine is internally consistent, so the
output looks perfectly plausible while bearing no relation to the source's claim. And per §1.1,
the correction is not "add resting entries" (#333 is deferred until a price-conditional rule
earns it); it is declining to trust the number.

### Q2 — What cost regime were they developed under?

**Establish:** fee per leg (maker or taker), slippage assumption, and whether the source states
them at all. "Not stated" is a real answer and a finding.

**Why it matters here:** keel is **cost-bound, not signal-bound** (PRD §0). Round-trip friction
is `2 × fee_pct + 2 × slippage_pct` — 2.50% of notional at taker, 1.30% at maker
([`2026-08-12-fee-curve-and-rsi-meanrev.md`](2026-08-12-fee-curve-and-rsi-meanrev.md)) — the
same order of magnitude as the per-trade edge of everything ever measured here. Seven cells
(five distinct assets) showed gross PF > 1.0 in the restated intersection and **all seven died
at the maker rate**,
before the taker rate actually paid was reached
([`2026-08-13-restated-under-a-production-faithful-engine.md`](2026-08-13-restated-under-a-production-faithful-engine.md)
§3).

**What a wrong answer costs:** a result developed on equities at 5bp, or crypto perps at 2bp,
does not transfer to a 1.2% taker venue, and nothing in the abstract will say so. Combined with
Q1 this is the compounding: a chosen-price entry *and* a cheap-cost assumption are both
optimistic at once, multiplicatively.

### Q3 — What sample size and evaluation window?

**Establish:** the trade count the headline rests on, the period it was measured over, and
whether that period spans more than one regime.

**Why it matters here:** `rsi_meanrev` showed the best gross PF of the three shipped rules —
median **1.1631** on a median of **38 trades** (the pre-correction anchor) — and collapsed to
**0.8396** across 82 cells at `n≥100` on the corrected engine (1.1251 at median n=42 on the same
engine; the level shift appears within either engine: 1.1631 → 0.8938 old, 1.1251 → 0.8396
faithful)
([`2026-08-13-restated-under-a-production-faithful-engine.md`](2026-08-13-restated-under-a-production-faithful-engine.md)
§4). It was not a decline as trades accumulated; it was a level shift at the measurability
floor. The window trap bit here too: ZEC-`turtle` printed 1.555 gross across 2021–26 while
running three consecutive losing years and compressing 92.7% of its lifetime PnL into 2025–26
([`2026-08-12-shipped-defaults-intersection.md`](2026-08-12-shipped-defaults-intersection.md)
§3.2/§6 — the faithful engine later restated the same cell at 1.442, which strengthens rather
than weakens the point).

**What a wrong answer costs:** a headline profit factor on a small sample is a lottery ticket,
not an edge. Porting effort is spent on variance, and the resulting backtest — even run honestly
at keel's own costs and fill model — cannot distinguish the two, because n is exactly what it
lacks.

### Q4 — Does it need a capability keel does not have?

**Establish:** whether the strategy uses pyramiding, partial exits, conditional entries at a
price, per-bar position management (`update_position`-style hooks), per-strategy mutable state,
or fill-event callbacks.

**Why it matters here:** the Jesse comparison (PRD §2/§3) mapped each of these to its keel
issue — conditional entry at a chosen price is **#333** (deferred until a price-conditional rule
earns it); partial exits and per-bar live stop management are **#502** (blocked on the
bracket/OCO order kind — `scale_out` exists with no caller); pluggable rules are **#447**; and
the executor's discarding of conditional entry prices is **#260**. `detect()` is deliberately a
pure function of candles with no account, balance or venue access — statefulness is a trade
keel makes on purpose, not a gap to paper over.

**What a wrong answer costs:** the port silently approximates the strategy — no pyramiding, no
partial exits, entries at the open — and the approximation is *not* the published strategy. The
backtest then measures a strawman of your own construction, and neither a good nor a bad result
from it is evidence about the original claim.

## 4. The checklist — answer before porting effort

Copy this into the evaluation. **"Unknown" is a real answer: for Q1–Q3 it means treat the
published number as unusable; for Q4 it means the strategy is not evaluable here yet.**

```markdown
External strategy evaluation — fill-model hazard checklist (#529)

Strategy / source:
Date evaluated:

1. Fill model. Were the published numbers produced with resting limit/stop
   entries at a chosen price (rather than market-style fills)?
   [ ] no   [ ] yes — keel's next-open fill takes the trades the strategy
                meant to decline; budget the pullback_continuation example
                (gross PF 0.92 -> 0.77, trade count doubled)
   [ ] unknown — treat as yes

2. Cost regime. Does the source state fees (per leg, maker/taker) and
   slippage?
   [ ] yes — stated: ___________ (compare: keel round-trip is 2.50% taker)
   [ ] no / not stated — the number is unusable as evidence

3. Sample size and window. Does the source state its trade count and
   evaluation window, and does the window span more than one regime?
   [ ] yes — n = ___, window = ___, regimes = ___
   [ ] no — a small-sample headline is a lottery ticket
                (rsi_meanrev: 1.1631 at median n=38 vs 0.8396 at n>=100)

4. Capability. Does it require pyramiding, partial exits, conditional
   entries, or per-bar position hooks?
   [ ] no — evaluable as-is
   [ ] yes — which: ___________ (#333 / #502 / #447 / #260 territory;
                not evaluable here until those land)

Verdict: [ ] proceed to port   [ ] do not port — reason: ___________
```

A "proceed" here does not mean the strategy is good; it means its published numbers are
*comparable*. Everything after that is keel's own gate: backtest at the real fee and fill model,
`n≥100`, and the promotion floors — which is where every candidate so far has died.

## 5. Where this is enforced for machine-generated proposals

The [keel-asset-scout skill](../../.claude/skills/keel-asset-scout/SKILL.md) (the proposing side
of the §5 proposer/decider asymmetry) already requires its param-proposal citations to carry
`cost_regime`, `sample_size` and `evaluation_window` fields, and frames performance claims
against the next-open fill model. This document is the human-facing statement of the same
discipline; the two are kept in step.

## 6. Provenance of every number

| number | source |
|---|---|
| gross PF 0.9292 → 0.7736; median n 60 → 124; n≥100 cells 4 → 14 | [`2026-08-13-restated-under-a-production-faithful-engine.md`](2026-08-13-restated-under-a-production-faithful-engine.md) §3 table |
| gross 0.9219 → 0.7736; median n 58 → 124; "the doubling *is* the count of those trades" | same, §3.1 |
| next-bar-open market fill; `order_type="market"`, `limit_price=None`; "free optionality on the entry price … and unbounded patience" | same, §1 (#258); `keel/strategy/backtest.py` module docstring; `keel/execution/executor.py` ("Entry routing is unconditional market (#258, #260)") |
| round-trip friction 2.50% taker / 1.30% maker (`2 × fee_pct + 2 × slippage_pct`) | [`2026-08-12-fee-curve-and-rsi-meanrev.md`](2026-08-12-fee-curve-and-rsi-meanrev.md) |
| `rsi_meanrev` 1.1631 at median n=38 (old engine); 0.8396 across 82 cells at n≥100, 1.1251 at median n=42 (faithful engine); old-engine n≥100 pair 1.1631 → 0.8938 across 76 cells | [`2026-08-13-restated-under-a-production-faithful-engine.md`](2026-08-13-restated-under-a-production-faithful-engine.md) §4 |
| ZEC-`turtle` 1.555 gross, three consecutive losing years, 92.7% of PnL in 2025–26 | [`2026-08-12-shipped-defaults-intersection.md`](2026-08-12-shipped-defaults-intersection.md) §3.2/§6 (faithful-engine restatement: 1.442, 08-13 §2) |
| "keel is cost-bound, not signal-bound"; Jesse `self.buy = qty, entry`; capability-to-issue map (#333/#447/#502) | [`docs/superpowers/specs/2026-08-23-strategy-api-expressiveness-prd.md`](../superpowers/specs/2026-08-23-strategy-api-expressiveness-prd.md) §0/§2/§3 |
| Jesse `go_long` snippet | issue #529, quoting the idiom documented in the PRD §2 comparison |

**Related:** #333 (resting-order routing, deferred), #502 (bracket/OCO — partial exits and live
stop management), #447 (pluggable rules), #260 (executor discards conditional entries), #259 /
#523 (slippage model and its cap), #257/#258 (the fill-model fix itself).
