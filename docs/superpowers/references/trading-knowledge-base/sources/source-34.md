[← Knowledge Base index](../README.md)

## Source 34 — "The Trader's Bible" (T3 Live, 2025, 28pp; Redler, Romero, Prince, Abusaad, Mesh)

> **New authors, new market** — a US **equities + options** day/swing-trading guide from T3 Live pro
> traders. First non-Stanzione, non-Islamic-finance source in a while. Real content, but **much is
> excluded or N/A** for a halal spot-crypto agent: two whole sections are **options** (excluded), two are
> **gaps / earnings** (largely N/A to 24/7 crypto). The value concentrates in the money-management /
> discipline sections + **a few genuinely new refinements** — the standout being **close-based stop
> confirmation (§34.1)**, which directly targets the milestone-6 "stops too tight for crypto" defect.

---

### 34.1 ⭐ Close-based stop confirmation — "be wrong only at the close, not on every wiggle" → executor/backtest stop model
David Prince's rule: *"A stock is allowed to breathe. Where am I really wrong? Not 30 cents below the
intraday low… I'm really wrong if it **closes** a dollar below the level — the close, not intraday noise."*
He calls traders who exit on every wiggle **"stop-out artists"** — *"the cost of getting shaken out of six
winners a month is higher than the occasional real loss when you're actually wrong."*
- → **NEW stop-model refinement, directly on-defect:** trigger a stop on a **candle CLOSE beyond the
  level/stop**, not an intraday touch/wick. This **reduces whipsaw/shakeout in crypto's high intraday
  volatility** — the exact failure mode behind the negative edge (stops "blown through at ~-2R" =
  intraday spikes stopping out otherwise-good trades). Pairs perfectly with the **Turtle/Donchian exits
  (§23.2/§27.1)**: exit on a *close* below the 10-day channel low, not an intraday penetration.
- → Expose as `stop_trigger = close | intraday` (+ optional buffer), **swept by the backtester** against
  the ATR-stop (§17.3). ⚠️ Balance: close-confirmation gives room but a gap/fast-move can close far past
  the stop (larger single loss) — this is the deliberate trade-off Prince names (fewer shakeouts, bigger
  occasional loss). Backtest both; it complements ATR-scaled stops, doesn't replace them.

### 34.2 Top-down confluence hierarchy (Wyckoff) → `strategy/engine.py` evaluation order
JR Romero's sequence — *check in this order, candle LAST:* **(1) market structure** (Wyckoff phase:
accumulation / markup / distribution / markdown — fractal across TFs) → **(2) location in range** ("thou
shalt not diddle in the middle — the edges are where reward-to-risk lives") → **(3) pattern** (breakout,
pullback, bull/bear flag, channel) → **(4) candlestick signal = the trigger, only after 1–3 pass.**
- → **Formalizes the confluence *ordering* we already do** (regime → levels → pattern → candle): a candle
  means nothing without structure+location+pattern behind it ("a shooting star in the middle of nowhere
  means nothing"). Reinforces **signals-only-at-a-level (§8) + candle-as-trigger-not-thesis**. Useful as
  the engine's explicit evaluation order. **"Trade the edges, not the middle"** = only act at S/R
  extremes, never mid-range → reinforces the levels gate (§4.8).
- ⚠️ **Wyckoff 4-phase regime** is richer than our trend/range/choppy states but **phase-ID is
  discretionary** (like chart patterns → v2-deferral territory); and **"effort vs result" (volume-price
  divergence) needs reliable volume**, which crypto lacks across fragmented venues → **low-weight /
  caution**, don't build phase-detection now.

### 34.3 First-tap-of-a-level = best entry → refines the levels rule (§4.8)
Prince: *"The **first** time a stock touches a level is the best time… by the third bounce the level isn't
special — the buyers waiting there have already filled. The first tap is where the institutional bids
sit."* → **Reconciles with our ≥3-touches rule (§4.8), not contradicts it:** ≥3 touches **validates a
level exists**; the **first *retest* of a validated level is the freshest/best entry**. → Add an
entry-timing note: prefer the **first retest** of an established level; deprioritize the Nth bounce
(exhausted). Testable as a `touch_index` feature in the backtester.

### 34.4 Conviction-graded sizing (A+/B/C) → reinforces CTS graded sizing (§8.1)
Both Romero & Prince: *"itemize every idea — A+, B, or C — and size accordingly"*; *"your best 10–15% of
ideas produce 80% of gains"*; A+ = **multiple factors aligned** (Prince's "great chart + great earnings +
great sector"). → **Exactly our CTS confluence-score → graded sizing** (§8.1): the CTS grade *is* the
A+/B/C, and size scales with it **within the rails**. Strong reinforcement; the "top 10% of ideas = 80% of
gains" is the rationale for graded (not flat) sizing. Also: **size into trades / partial then add on
confirmation** = **pyramiding (§26.1)**; **trim into strength, leave a 1/3 runner** = **partial exits
(§3.5) + let-winner-run**.

### 34.5 ⚠️ Concentration vs preservation — a TENSION we resolve toward preservation
Prince: *"Concentrated risk — vs owning 15 stocks at 5% each — is how you create alpha… be willing to
size up on the one or two tremendous opportunities."* → **This runs against our diversification / per-asset
concentration cap / correlation-sizing rails (§4.1/§10.3).** Deliberate divergence: T3's goal is aggressive
account *growth* (discretionary human, $100k→$1M); **our success bar is risk-adjusted *preservation*** (beat
DCA on drawdown/Sortino, milestone-6). So we **keep the concentration caps** and let CTS size up only
*within* them. Noted as a conscious choice, **not adopted.** (His "$100k→$1M via concentration" is
explicitly *not* the "preservation" mandate — he says so himself.)

### 34.6 Event handling → reinforces the news/event-blackout filter (§3.6)
Redler/Prince earnings framework: *"the report is already priced in; don't trade **into** the binary
event — trade the day **after**."* + *"news is already factored into the price"* (Abusaad). → Reinforces
**(a) no-prediction-oracle / price-first (§6.4)** and **(b) the news/event-blackout filter (§3.6):** don't
open into a scheduled catalyst; act on the confirmed reaction after. The **"failed gap reversal"** (gap
holds but closes mid-range → next session fails to reclaim the high → trade the failure) is a **failed-
breakout reversal** pattern → useful as a **failed-breakout filter** for the Turtle rule (a breakout that
closes back inside the range = failed → stop-out, don't chase; possible mean-revert). **Focused watchlist:**
*"only trade the 15–20 names you know cold"* → reinforces a **focused allowlist** (§10.3/§33.2).

### 34.7 ⛔ Excluded (halal/spot) & N/A (market structure)
- **All options strategies — EXCLUDED** (not spot; premium-selling / spreads = gharar/maisir, §27–28):
  Section 4 (call spreads, rolling, binary-event spreads, IV-crush), Section 7 (cash-secured puts, credit
  spreads, diagonal calendars, directional options), and the options parts of Section 6 (expected-move via
  ATM straddle pricing). None apply — we hold spot only.
- **Gap trading (Section 5) — N/A:** a gap is *"yesterday's 4 PM close vs today's 9:30 open"* — an
  artifact of exchange **overnight closure**. **Crypto trades 24/7** (Coinbase spot is continuous, incl.
  weekends) → **no meaningful gaps.** Transferable scraps only: pro-vs-novice "gap" ≈ conviction-move-
  against-trend vs exhaustion-move-with-trend (general momentum idea); 5-point gap score ≈ confluence
  scoring; "news already priced in." The gap *mechanics* don't port.
- **Earnings trading (Section 6) — N/A:** crypto tokens have **no earnings reports.** Transferable
  principles folded into §34.6 (event-blackout, failed-breakout, focused watchlist); the earnings-specific
  machinery doesn't port.
- **Short setups** (Evil Knievel upthrust, bear flags, day-after gap-down) → **exit/don't-buy only**, never
  shorts. Long-side setups that DO port: **bull-flag continuation in power trends** (≈ pullback-continuation
  family §7.1), **morning-star reversal after a selling climax** at range bottom (a bottoming long, but
  discretionary → treat as low-weight/backtest-first).

### 34.8 Discarded (no agent value)
T3 Live room/service marketing & CTAs (Momentum Express, Inner Circle, Power Plays, Strategic Day Trader,
MeshPrime, Mesh Delta 30); author bios & TV-credit name-drops; anecdotal trade war-stories (AOI, Fastly,
SanDisk, Rocket, Nvidia, Reminiscences quotes) — motivation, no mechanics; disclosures.

---

### Net assessment (saturation-honest)
- **NEW & actionable:** **close-based stop confirmation** (§34.1 — trigger on close beyond the level, not
  an intraday wick; directly attacks the "stops too tight for crypto" defect; sweep `stop_trigger=close|
  intraday`); the **top-down confluence *ordering*** as the engine's explicit evaluation sequence (§34.2);
  **first-retest-of-a-validated-level** entry timing (§34.3).
- **REINFORCES:** CTS graded A+/B/C sizing (§34.4), pyramiding + partial-exits/runner, low-win/high-R:R
  (Romero's 3:1 min), do-less/low-turnover, cut-losses/let-winners-run, no-oracle/price-first, news/event-
  blackout, focused allowlist.
- **TENSION (resolved toward preservation):** T3 preaches concentration for growth; we keep concentration
  caps for preservation (§34.5) — conscious non-adoption.
- **EXCLUDED / N/A:** all options (gharar/maisir/not-spot); gap trading (24/7 crypto has no gaps); earnings
  trading (crypto has no earnings); all shorts → exit/don't-buy.
- **Action:** fold **close-based stop** into the Turtle-rule build (`stop_trigger=close`, backtest vs
  intraday) and the confluence-ordering + first-retest notes into the engine spec. A useful new-author
  source despite heavy exclusions. See [[halal-cb-autotrade-project]], [[halal-cb-transcript-workflow]].
