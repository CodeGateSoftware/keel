[← Knowledge Base index](../README.md)

## Source 60 — "A Practical Guide to Swing Trading" (Larry Swing, mrswing.com, self-published web guide, ~2003–2005 vintage, 74pp)

> **A genuinely mechanical, fully-specified swing system — the most codeable source since the Turtle
> spec (§27.1/§54.14).** Larry Swing (MrSwing.com, SwingTracker/BestSwings) wrote this as a lead-magnet
> for his charting software and subscription picks, but unlike the Stanzione/Deriv ebook family it is
> **not** a rehash — it hands over an actual algebraic scan formula (`MAV20>=500000 AND CLOSE>12 AND
> CLOSE>SMAC10 AND CLOSE>SMAC20 AND HIGH<HIGH1 AND HIGH1<HIGH2 AND FORCE3<=0 AND FORCE13>=0 AND
> ADX20>30`), exact entry/stop/target arithmetic, a trailing-stop ratchet rule, and three worked
> historical trade examples (DFXI, PFG, PTEN, 2002) with entry/exit prices and realized P&L. Content
> pages 16–74 are almost entirely mechanical rules and software walkthroughs — very little discretionary
> chart-reading, no candlestick pattern catalog, no psychology chapter. That is exactly the shape the
> harness wants.
>
> **The one thing to be blunt about up front:** the book's headline method — *"Why does swing trading
> work? Because you are trading in the direction of the trend. You wait for a pullback before entering
> the trade"* — is, mechanically, a **pullback-continuation / buy-the-dip system**. That is the *same
> family* the project already tested and refuted on crypto (no edge, negative expectancy). This source
> does **not** get a free pass just because its horizon (days-to-weeks) matches our validated trend-
> following pivot — see §60.10 for the honest reconciliation. The swing **horizon** is a good match; the
> swing **entry logic** sits on the refuted side of the ledger and must be treated as an unproven
> hypothesis, not a new lead, until it clears the harness independently.

---

### 60.1 ⭐ The Master Plan — full mechanical entry/exit spec ("FORCESWING") → `strategy/rules/`

The book's core deliverable, called **The Master Plan**, is a single, fully mechanical long-swing recipe
(the short-swing version is its mirror image, §60.11). Universe filter first:

- Price ≥ **$12** (elsewhere in the book, $7; author notes he varies it "depending on market conditions")
- 20-day average volume ≥ **500,000 shares** (elsewhere 250,000) — a liquidity floor to avoid
  easily-manipulated low-float names

**Entry filter** (the `FORCESWING LONG` scan code, reproduced verbatim from p.55):

```
MAV20 >= 500000 AND CLOSE > 12 AND
CLOSE > SMAC10 AND CLOSE > SMAC20 AND
HIGH < HIGH1 AND HIGH1 < HIGH2 AND
FORCE3 <= 0 AND FORCE13 >= 0 AND
ADX20 > 30
```

Read as plain rules:
1. **Uptrend confirmation:** today's close above both the 10-day and 20-day SMA.
2. **3-day shallow pullback:** today's high < yesterday's high < the day before's high — a strictly
   decreasing 3-bar high sequence, the book's mechanical definition of "the stock is pulling back."
3. **Force-Index divergence gate** (§60.5): the 3-day MA of Force Index ≤ 0 (bears winning the
   short-term battle) **while** the 13-day MA of Force Index ≥ 0 (bulls still in control of the
   longer-term battle) — i.e., a pullback *within* an intact uptrend, not a trend change.
4. **Trend-strength gate:** ADX(20) > 30 — stricter than our existing ADX(14)>25 filter (§25.1/§27.1)
   on both period and threshold.

**Entry execution:** place a buy-stop **6 cents above yesterday's high** (or, if the stock gapped ≥ 50¢
from the prior close, 6¢ above the new day's high, with an intraday wait of 30 min if the gap is in the
trade's direction or 5 min if against it — pure equity-market-open microstructure, **N/A to 24/7 crypto**,
discard the timing, keep the underlying idea: *don't enter on the open, confirm price has broken back
above the most recent pullback high first* — this reinforces §34.3's first-retest-entry-timing). Retry
the unfilled order for up to **5 trading days**, then drop the candidate.

**Exit — split target + capital-preserving stop:**
- **Stop-loss:** whichever is **tighter (higher)** of `entry_price × 0.96` (4% below entry) or
  `low_of_setup_day − 0.06` — a percentage floor combined with a structural (swing-low) floor, taking
  the more conservative of the two.
- **Target:** a sell-limit at `entry_price × 1.07` (7% above entry) for **half the position only**.
- **Remainder:** stays open to "ride the wave"; its stop is a **daily ratchet**: each day, if
  `today's_low − 0.06` (or, on a gap day, the current day's low) is higher than yesterday's stop, raise
  the stop to that new level — never lower it. This is a **1-day-low-minus-tick trailing stop**,
  materially *tighter* than the 10-day Donchian/channel-low trail (§23.2/§27.1) and even tighter than the
  2-bar trail already logged as a lower bound in §55.3. Expect the same "too tight for crypto" failure
  mode noted there unless scaled by ATR (see §60.10).

**Worked validation (book's own case studies, pp.60–63, 2002 data):** DFXI (entry $42.48, stop $41.35,
target $45.45, exit at target + trailing stop, net **+4.63%**), PFG (entry $27.26, stop $26.35, exit via
trail at $27.94 — **target never hit**, net **+2.49%**), PTEN (entry $26.84, stop $25.94, half exits at
target $28.72, remainder trails out at $29.99, net **+9.37%**). Three trades, one publisher, no
disclosed win-rate or sample size — directionally consistent with the rule as specified, but **not a
backtest**; treat as illustration, not evidence.

- → **New, testable as specified:** the whole rule (filter + entry trigger + split-exit + ratchet trail)
  is one coherent `strategy/rules/` candidate, distinct in its exit shape from every prior source's
  Donchian/ATR-based exits — the **fixed-%-target-for-half + tight-trail-for-remainder** split is not
  currently in the KB.
- ⚠️ See §60.10 before prioritizing this — the entry side is the same *shape* as the refuted
  pullback-continuation rule.

### 60.2 ⭐⭐ Deployment cadence — rank-pick N-way equal-weight slots from a daily candidate list → `strategy/money_mgmt.py`, targets **under-deployment**

Separate from the entry/exit mechanics, the book describes an explicit **capital-deployment discipline**
(Introduction, pp.7–8, reinforced p.53):

> *"I divide my trading capital by 15. This is the amount I put into each trade... Each day I identify
> 20 to 25 candidates for swing trading. If I have 10 trades active and enough additional investment
> capital for 5 more trades, I pick the best 10 from my list of 25, and place the orders. Only some of
> orders will get filled. I don't worry about running out of money — if there is no cash left in the
> account, additional orders will simply not get filled."*

Mechanically, this is: **(a)** divide total capital into N equal-sized slots (author uses 15, sometimes
20); **(b)** every trading day, generate a ranked candidate list from the mechanical scan (~20–25 names);
**(c)** for every currently-empty slot, place an entry order on the next-best unfilled candidate; **(d)**
accept that only a fraction of orders fill (price must trade through the buy-stop) — this is the
system's own risk-limiter, not a bug.

- → **This is the most concrete lever in this source against the under-deployment defect.** Our current
  sim places only ~23 trades in 5 years, mostly sitting in cash, because the rule set has too few
  qualifying setups and (implicitly) no mechanism that actively keeps a target number of slots filled by
  *ranking* multiple simultaneous candidates. This source's discipline is exactly that mechanism: instead
  of asking "does any one asset qualify today?", it asks "of all qualifying assets today, fill as many of
  my N slots as I can, best-ranked first." On our narrow BTC/ETH-scale allowlist N would be small (2–4,
  not 15–20), but the **rank-and-fill-empty-slots** logic is allowlist-size-independent and is a direct,
  harness-testable answer to "why is capital sitting idle when there could be a valid entry we're not
  taking because we only evaluate one asset/rule pairing at a time."
- Concretely: a `target_concurrent_positions` (or per-asset slot count) config value in
  `strategy/money_mgmt.py`, driven off a **ranked** signal list (CTS score, §34.4) rather than a
  first-qualifying-signal-wins scan — reuses infrastructure we already have (CTS scoring exists; this
  adds a *ranking-and-fill* consumption pattern on top of it).
  Fits directly under the existing "equal-risk-by-ATR allocation across allowlist" line (§54.22) — this
  source's contribution is the **daily rank-and-fill cadence**, not the sizing formula itself.
- ⚠️ The **1/15 equal-weight** number itself does not port — it presumes a broad multi-hundred-stock
  universe. Our allowlist is narrow (2–6 assets under the halal `haram_sector` screen). The number to
  test is **not** 15; it's whatever the sim's own asset count and correlation-cap rails (§54.14) already
  bound. What ports is the **behavior**, not the constant.
- ⚠️ **Unvalidated novelty risk:** more trades is not automatically better — it is only valuable if the
  *additional* trades taken to fill idle slots still clear the per-class promotion floor
  (`win_rate > 1/(1+R:R)`, §35.2). Rank-and-fill must be swept through `keel simulate`, not adopted on
  faith; a naive implementation could just add more losing trades faster.

### 60.3 Split-exit structure (partial fixed-target + tight trail on the remainder) → `execution/executor.py`

Distinct from §60.1's specific numbers, the **shape** of the exit is itself a reusable idea: take a fixed
partial profit at a modest target (7%) on **half** the position, and let the other half run under a
tight, ratchet-only trailing stop that only ever moves in the favorable direction. This is a concrete,
parameterizable variant of the "partial exits" capability already noted in the module map — the specific
recipe (50% at fixed target, 50% on a 1-bar-low trail) had not previously been logged as a named pattern.
- → Add as an `exit_method` sweep option: `partial_fixed_then_trail(fraction=0.5, target_pct, trail_lookback=1)`.
- The **stop = max(entry×(1−stop_pct), swing_low−tick)** formula (take the *tighter* of a % floor and a
  structural floor) is itself reusable independent of the specific 4%/7% numbers — it is a clean,
  one-line rule for combining a percentage stop with a structural stop, and could be applied to any
  ATR-scaled stop as `max(entry − k·ATR, swing_low − tick)`.

### 60.4 Entry-confirmation timing → reinforces §34.3; gap-wait mechanics N/A to crypto

"Buy 6 cents above yesterday's high" (or the current day's high, on a gap) is a **confirmation-of-
break-above-the-pullback-high** entry trigger — conceptually the same *first-retest-of-a-level* family as
§34.3 and the Raschke First-Cross (§54.16): don't buy the dip itself, wait for price to prove the pullback
is over. **Reinforces**, does not add.

The specific "wait 30 minutes if the gap is with the trade, 5 minutes if against it" timing is pure
equity-market-open microstructure (assumes a 9:30am open and pre-market gap) and has **no crypto analog**
in a 24/7 market with no open/close — discard the timing rule entirely, keep only the underlying
principle already captured by §34.3.

### 60.5 ⭐ Force Index (Elder) — new indicator + a 3-day/13-day divergence discriminator → `analysis/indicators.py`

**Force Index** (Alexander Elder, *Trading for a Living*) is not currently in the KB (§54.23 covers OBV,
MFI, VW-MACD, Force Index is a distinct construction):

```
Force Index = Volume(today) × (Close(today) − Close(yesterday))
```

The book smooths it with a **3-day MA** (short-term battle) and a **13-day MA** (longer-term battle) and
reads the *combination*, not either alone:
- **Buy setup:** FI-13MA ≥ 0 (bulls in control long-term) AND FI-3MA ≤ 0 (bears winning short-term) AND
  price still in an uptrend → "a pullback within a trend," §60.1's confluence gate.
- **Sell setup (mirror):** FI-13MA ≤ 0 AND FI-3MA ≥ 0 AND price still in a downtrend.

- → **New, mechanically testable indicator.** Force Index is cheap to compute from data we already have
  (close, volume — same inputs as OBV/MFI), and the 3-vs-13-day dual-MA divergence read is a genuinely
  different confluence shape than anything currently in `indicators.py` (it is not a bare oscillator
  level, it's a **short-term-vs-long-term momentum disagreement** signal, structurally closer to a MACD
  histogram than to RSI). Worth adding as a standalone indicator regardless of what happens to §60.1's
  full rule, since it could plug into other entry/exit confluence sets (e.g., as an additional filter
  layer on the Turtle breakout, or as an early-warning exit discriminator on a held long — "FI-3MA just
  turned negative while FI-13MA is still positive" is a graded pullback-vs-reversal read worth testing
  independent of this source's specific buy rule).
- ⚠️ Unvalidated in isolation — the book gives no backtest for Force Index alone, only as embedded in
  §60.1's full rule.

### 60.6 ADX(20)>30 trend-strength filter → reinforces §25.1/§27.1, logs a parameter variant

The book's trend-strength gate is **ADX with a 20-day period, threshold 30** — both period and threshold
differ from our existing ADX(14)>25 (§25.1) and the Turtle's own ADX(14)>25 (§27.1/§54). **Reinforces**
the ADX-as-trend-strength-gate concept (three independent sources now: CFI textbook, Turtle spec, this
one) but logs a **new parameter combination worth including in the ADX-period/threshold sweep** already
implied by the existing gate — do not assume 14/25 is optimal for crypto without testing 20/30 as an
alternative.

### 60.7 SMA10/20/50 trend-stack + volume-confirms-trend → reinforces §26.2/§54

*"Two indicators that a stock is in an uptrend: today's closing price is above both the 10-day and
20-day moving averages; the 10-day moving average is above the 20-day moving average"* — plus, in the
"Essentials of TA" chapter: an uptrend additionally shows **higher volume on the upward legs, lower
volume on the downward (corrective) legs**, and **finds support at its own 20- or 50-day MA**.

**Reinforces** the existing 20-SMA exit rule (§26.2) and the general MA-stack trend-confirmation idea
(§37, §54) — three-MA-stack (10/20/50, ordered and price-above-all) is a slightly richer confirmation
than the single 20-SMA already logged, but adds no new rule, just a stricter confluence variant worth
noting alongside §60.6 for the trend-confirmation sweep. The volume-asymmetry-confirms-trend detail
reinforces §54.23's volume-confirms-price thesis, not new.

### 60.8 Up/Down/In/Out bar classifier + Equivolume "power box" → reinforces `analysis/candles.py` / §54.23

- **Up/Down/In/Out**: a 4-state single-bar classifier relative to the *prior* bar — Up (higher high AND
  higher low), Down (lower high AND lower low), In (lower high, higher low — an inside bar), Out (higher
  high, lower low — an outside bar). This is a trivial, already-implicit primitive (our candle module
  already computes highs/lows relative to prior bars for pivot detection) — worth a one-line
  `bar_classification()` helper for readability, but **not a new capability**.
- **Equivolume "power box"** (Richard Arms): a box chart where width = volume, height = range; a "power
  box" (tall AND wide — big range, high volume) confirms a breakout, a narrow box (light volume) casts
  doubt on one. This is a **visualization**, not a new rule — its substance (volume must confirm a
  breakout's range, or the breakout is suspect) is already captured mechanically by the low-volume
  breakout filter (§54.23). **Reinforces, not new.**

### 60.9 Liquidity/universe floor ($12 price, 500k-share volume) → reinforces allowlist admission logic

The book's blanket universe filter — minimum price and minimum 20-day average volume, explicitly to
"stay away from low price, low volume stocks [that] market makers can more easily manipulate" — is an
equity-market liquidity screen. It has no literal port (crypto has no analogous "penny stock" price
floor), but the **underlying principle** — don't trade illiquid names where price can be moved cheaply —
already underlies the halal allowlist's narrow BTC/ETH-scale curation (§28.3, §33) and the low-volume
breakout filter (§54.23). Logged as reinforcement of an existing posture, no new rail.

### 60.10 ⚠️ Reconciliation — is this the refuted pullback-continuation family, or something new?

This needs a direct answer, not a hand-wave, because the project brief flags swing trading as "a
particularly good fit" and it would be easy to over-read that into "this source's rule is validated."
**It is not, and the two claims must be kept separate:**

- **The swing *horizon* (holding ~days to a few weeks, daily-bar cadence, anti-scalping) is a genuinely
  good structural fit** for `keel` — nothing here changes that; if anything the book independently
  reinforces it (explicit "not day traders," "typical trade is a few days to a few weeks," no intraday
  monitoring required).
- **The swing *entry logic* in §60.1 is, mechanically, a pullback-continuation / dip-buy rule** — its
  own stated rationale (*"you wait for a pullback before entering the trade"*) is the textbook definition
  of the family the project already backtested and refuted on crypto (negative expectancy; RSI
  mean-reversion and pullback-continuation both failed). The confluence stack here (ADX(20)>30 + Force
  Index short/long divergence + strict 3-bar declining-high sequence + a break back above the pullback
  high, not a naive oversold-RSI trigger) is **stricter and more mechanically specific** than whatever
  was tested before — but stricter filters do not automatically resurrect a refuted edge; they are a
  hypothesis to test, not a result.
- **Recommendation:** if this rule is prototyped, run it through the harness **exactly as its own
  refuted sibling was** — same backtest → paper → promotion pipeline, same breakeven-winrate floor
  (§35.2), same walk-forward/OOS discipline (§54.10) — and go in expecting it to fail for the same
  structural reason dip-buying failed on crypto (mean-reversion assumptions don't hold in
  trend-persistent, momentum-driven crypto microstructure the way they did in 2002-era large-cap
  equities). If it clears the floor anyway, that is useful *because* it was tested skeptically, not
  because the source asserted it works.
- **What is NOT tainted by this caution:** §60.2 (deployment cadence — asset/rule-agnostic), §60.3 (exit
  shape — applicable to any entry method, including the validated Turtle breakout), §60.5 (Force Index —
  a standalone indicator, useful even if never used as this source's specific gate), §60.6/§60.7
  (parameter variants for gates we already use elsewhere). These survive independent of whether §60.1's
  full pullback rule itself ever passes promotion.

### 60.11 ⛔ Excluded (halal / spot / long-only)

- **The entire "Short Swing" system (§6.9, pp.32–33)** and **Appendix A — Short Selling (pp.69–72, the
  book's single longest technical appendix)**: short selling requires a **margin account**
  (*"you must make sure your brokerage account is approved for trading on margin"*), a
  **hypothecation/rehypothecation agreement** (pledging your own stock as collateral, and the broker
  re-lending it — a securitized-lending structure), and **interest charged on the outstanding short
  position** (*"your account will be charged interest against the value of the short position"*) — this
  is **leverage + riba + shorting**, triply excluded (§4.9, §10.10, §28.1, §30.1). Per the standing
  adaptation lens, the short-swing mirror-image rule is **not** ported as a short entry. Its one
  salvageable fragment: a **downtrend + rally-into-resistance** setup is structurally a **don't-buy /
  exit filter** on a held long (the FI-3MA≥0/FI-13MA≤0 mirror reads as "a relief rally within an intact
  downtrend" — useful as an exit-confidence signal on a position already held, never as a new entry).
  The **up-tick rule** discussion (§12.1.4) is a US equity-market-structure detail with no crypto analog
  (spot crypto has no short-locate/uptick mechanism because there is no short leg) — N/A, not excluded
  so much as inapplicable.
- **Margin account requirement generally** (needed even to *combine* buy+sell-stop+sell-limit as an OCO
  bracket in some of the book's broker recommendations) — no leverage; our OCO/bracket execution
  (`execution/executor.py`) already achieves the same order-grouping without margin.
- The **Zitel short-selling case study** (Appendix A, pp.71–72) — a worked short-sale example — excluded
  as an instance of the above, no separate treatment needed.

### 60.12 Discarded (no agent value)

Dedication, three testimonial-pages of subscriber quotes, the "Meet Larry" biography, both forewords
(Suri Duddella / sixer.com, Dr. Sergey Perminov / OptionSmart.com — promotional, not technical), the
**"Preferred Brokers"** chapter (Interactive Brokers, optionsXpress feature lists — commissions,
autotrading via "Xecute", options-trading feature bullets N/A to spot crypto), the entire **SwingTracker
software walkthrough** (§9 — screenshots of watchlists, real-time quote panels, the Scan/Query-Builder
UI, Portfolio Tracker) and the **"cut and paste a scan into SwingTracker"** step-by-step (§10.1.2) — all
product documentation for a discontinued charting tool, not trading rules. The **bare list of "available
technical indicators"** in SwingTracker (Bollinger Bands, CCI, Linear Regression, McClellan Oscillator,
Momentum, Money Flow, RSR, RSI/RSI-Classic, Stochastics, Ultimate Oscillator, Volatility, W%R,
Parabolic SAR, OBV) is named but **never operationalized** anywhere in the book — no formulas, no
thresholds, no rules given for any of them beyond the ones already extracted (Force Index, DMI/ADX,
SMAs) — nothing to extract, they are marketing copy for the software's indicator count. **Appendix B —
Resources** (three book-jacket recommendations: Velez & Capra's *Tools and Tactics for the Master Day
Trader*, Elder's *Trading for a Living*, Natenberg's *Option Volatility & Pricing*) — a reading list, the
last of which (options pricing) is excluded by instrument (§27.4/§28.1) and the first is a **day-trading**
book, out of our horizon. The "why does technical analysis work?" psychology paragraph (§8.2 — "you
trade people, not stocks... traders keep making the same mistakes") is generic and saturated
(§5/§23/§24/§26).

### Net assessment (saturation-honest)

**A genuinely mechanical source with one important caveat, not a saturation dud.** Roughly a third of
the book is software marketing (SwingTracker screenshots, broker recommendations, subscriber
testimonials) and one chapter is squarely excluded (short-selling mechanics), but the remainder is the
most fully-specified, arithmetic-complete trading rule since the Turtle spec:

- **New:** §60.2 the **rank-and-fill deployment cadence** (the strongest single item in this source —
  a direct, asset/rule-agnostic answer to the under-deployment defect); §60.3 the **partial-fixed-target
  + tight-ratchet-trail split-exit shape**; §60.5 the **Force Index indicator** (new to the KB,
  standalone-useful regardless of §60.1's fate).
- **Refines/reinforces:** §60.6 (ADX(20)/30 parameter variant on the existing ADX gate), §60.7 (3-MA
  trend stack, extends the single 20-SMA rule), §60.4 (confirms §34.3's first-retest-entry-timing),
  §60.8/§60.9 (candle classifier + volume-confirms-breakout + liquidity floor, all already covered).
- **A hypothesis requiring skeptical testing, not a validated new lead:** §60.1's full entry rule is
  structurally a pullback-continuation / dip-buy system — the exact family already refuted on crypto.
  Test it exactly as skeptically as its refuted sibling (§60.10); do not fast-track it on the strength of
  its unusually precise specification.
- **Excluded:** the short-swing mirror system + Appendix A (margin, hypothecation, interest-on-short,
  up-tick rule — riba/leverage/shorting, triply excluded).

**Recommendation:** prioritize prototyping §60.2 (deployment cadence) against the current under-deployment
finding — it is cheap to test (a config change plus a ranking-consumption loop on top of existing CTS
scoring) and does not require validating the pullback entry rule first. Log §60.5 (Force Index) as a
standalone indicator addition. If bandwidth allows, run §60.1's full rule through the harness as a
labeled hypothesis, explicitly expecting the same failure mode as the refuted dip-buy family unless the
tighter confluence proves otherwise.
