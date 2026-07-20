[← Knowledge Base index](../README.md)

## Source 75 — Three Warrior Trading / Ross Cameron lead-magnet PDFs (small-cap US equity day trading)

(A) **"Small Account Challenge — My Small Account Strategy"** (`SAC2024-Strategy-Ross-Cameron.pdf`, Warrior
Trading, 2024, **3pp**) — a fill-in-the-blanks strategy worksheet.
(B) **"My Stock Selection Process & Criteria"** (`Warrior Trading - Stock Selection-Ross-Cameron.pdf`,
Warrior Trading, **6pp**) — the scanner spec; the only document of the three with mechanical content.
(C) **"Technical Analysis Series"** (`Technical-Analysis-v3-Ross-Cameron.pdf`, Warrior Trading, **11pp**) —
three chapters: *Gap and Go Strategy* (pp3–6), *The Micro Pullback* (pp7–9), *Strategy Decision* (pp10–11).

> **Provenance note, up front:** (A) is a **near-verbatim duplicate of (C)'s "Strategy Decision" chapter**
> (pp10–11) — same headings, same Warren Buffett quote, same "Rule 1/2/3", differing only in "$583 into
> $1mil" vs "$583 into $10mil" and a paragraph on T+1 settlement. Roughly **4 of the 20 pages in this batch
> are unique content.** All three are free lead-magnets whose function is to sell a **scanner subscription**
> and a **course**; the scanner criteria they disclose are simultaneously the product being advertised.
>
> **Evidence tier: the weakest in the knowledge base.** No backtest, no sample size, no out-of-sample split,
> no control, no transaction-cost accounting — one practitioner's retrospective inspection of his own trade
> log, published by the company that sells the tool derived from it. Under §73.6 (report `N` or the result
> is uninterpretable) and §58.11 (benchmark against random) **none of this clears the bar to be adopted**;
> it can only be logged as a hypothesis or as structural reasoning.

**Why it was worth reading anyway:** the brief flagged Cameron's stock-selection process as the closest
external analogue to §60.2's rank-and-fill deployment cadence, our lead candidate for the
**under-deployment / trade-frequency** defect. It is — and reading it produces a **negative structural
result** about §60.2 that is more valuable than anything the documents offer positively.

---

### §75.1 ⭐⭐ The scanner is a **breadth** engine, not a frequency engine — and this establishes a hard limit on §60.2 `strategy/money_mgmt.py`, `strategy/engine.py`

This is the one finding in the source worth the file.

Cameron's daily process, stated mechanically across (B) pp2–4 and (C) p3:

```
1. A real-time scanner sweeps the ENTIRE US equity universe (~5,000+ listed names)
2. Conjunctive filter (ALL must hold):
     relative_volume >= 5x        (demand)
     change_from_close >= +10%    (demand)
     news catalyst present        (demand)
     1.00 <= price <= 20.00       (demand — account-size artifact)
     float < 10,000,000 shares    (supply)
3. Sort surviving names DESCENDING by % change from previous close
4. Take the TOP 2–3 ranked names ("the leading percentage gainers")
5. Manual overlay: read the catalyst headline; check the daily chart vs 20/50/200 EMA
6. Pre-stage the order; enter on a level break; 2:1 R:R target
```

Steps 2–4 are exactly the shape of §60.2 — a mechanical scan producing a ranked candidate list, then
best-first selection into a small number of slots. **But note what supplies the frequency.** In (A) he
takes **one trade per day**; his slot count is *one*. The scanner is not there to increase how often he
trades — it is there to guarantee that on any given day **at least one** name clears an extremely demanding
bar. His per-name criteria are far **stricter** than ours: a 40-day Donchian high is a single condition,
while his gate is a five-way conjunction requiring a 5× volume anomaly *and* a 10% daily move *and* a news
catalyst *and* a sub-10M float, simultaneously. On any individual ticker that fires perhaps a handful of
times per decade.

The arithmetic is:

```
trades_per_period  ≈  |universe| × P(name fires)
```

Cameron holds `P` near zero and makes `|universe|` ≈ 5,000. **We have `|universe|` = 3** (BTC, ETH, PAXG),
fixed by the halal `haram_sector`/allowlist screen (§41.1, §71.6) and by liquidity. There is no term left to
grow.

**⇒ The sharpening of §60.2, stated plainly: rank-and-fill is a CAPITAL-ALLOCATION mechanism, not a
FREQUENCY mechanism.** It answers *"given more qualifying candidates than slots, which do I fund?"* With
three assets and a slot count of three or more, the ranking step is a **no-op** — we are never
candidate-constrained. Our binding constraint is upstream: **the rule fires 2.6×/yr/asset** (§73.3), so the
candidate list is empty on ~99% of bars. Ranking an empty list produces nothing.

This does **not** retire §60.2 — it is still the right shape for allocating across BTC/ETH/PAXG when two or
three qualify at once, and §73.13's `MinBTL ∝ 1/(SR² × trades_per_year)` still makes any frequency gain
arithmetically valuable. But it removes §60.2 from the position it currently occupies in the module map as
*"the first mechanism that targets the under-deployment defect directly."* **It targets the deployment of
capital, not the production of signals.** The README row for `strategy/money_mgmt.py` should be softened
accordingly (see suggestions at the end).

**The remaining levers on frequency, after this, are only three:**
1. **A second, genuinely uncorrelated rule class** — MACD-family per §74.5, with the correlation
   **measured** not assumed per §74.12. This is the only lever that adds signals without degrading the
   entry criterion (which the 2026-07-20 ADX ablation showed is a bad trade).
2. **Longer holds / pyramiding** (§26.1, §54.19) — raises *deployment* (capital-days at work) without
   raising *trade count*, so it helps returns but **not** the §73.3 knowability problem, which counts trades.
3. **A broader allowlist** — structurally the same move Cameron makes, and the only one that touches
   `|universe|`. Bounded hard by the halal screen and by our own liquidity floor (§60.9); realistically
   worth a handful of names, not thousands. Note this pushes *against* §51's "redundancy doesn't diversify"
   caution, since admitted large-cap alts would be highly correlated with BTC.

None of these is new information. What is new is the **elimination**: the ranked-scanner idea, examined in
its native habitat, turns out to require a breadth we cannot have, and its transferable part (ranking) does
not touch the defect.

### §75.2 The scanner's demand/supply decomposition — sound reasoning, untransferable instantiation `analysis/`

(B) p4 organises the five criteria explicitly as **four demand indicators + one supply indicator**, with the
thesis that outsized moves come from a *supply/demand imbalance*, so a screen should measure both sides.
*"Companies with less supply will tend to experience more dramatic imbalances… These imbalances are what
create 50%–100%+ intraday moves."*

The **framing** is the only durable part. The instantiation does not survive contact with our universe:

- **Price band $1–$20** is openly an artifact of a small cash account (*"if a stock only has the potential
  to go up 5–10 cents per share, it's really not worth it"*) — meaningless for a fractional-unit spot asset.
- **News catalyst** requires a headline pipeline we do not have, and admitting one would brush **§6.4
  (no prediction oracle)**: a rule that says *"buy because a headline justifies the move"* is an LLM/NLP
  judgement inside the entry path, which the rails forbid. Note his own honest caveat cuts the other way
  too: *"stocks going up on no news can offer opportunities but would carry more risk of a sudden drop"* —
  i.e. the catalyst is a risk filter, not an edge, in his own telling.
- **Float < 10M** — see §75.4.
- **Already up ≥10% today** is momentum-confirmation-before-entry, which is what a breakout *is*. Saturated
  (§23.1, §27.1, §54.14, §74.1). His formulation is quotable — *"I'll never buy a stock that's not already
  moving"* — and is a clean statement of the anti-dip-buy principle, which sits **oddly** beside his own
  entry method (§75.6a).

⚠️ One structural caution: his **sort key is % change from close**, i.e. a pure momentum ranking. Per
**§74.12** (Zakamulin & Giner), momentum and moving-average/trend rules converge precisely when trends are
strong. So ranking our three assets by recent % change and entering on a Donchian breakout are **not two
independent pieces of evidence** — they are the same signal read twice. If §60.2's ranking key is ever
specified, prefer a *structurally different* key (§54.1 Efficiency Ratio, §59.9 relative-strength ratio, or
§54.9 ADXR/CSI) over raw % change, for the same reason §74.5 prefers MACD-divergence as the second rule class.

### §75.3 ⭐ Relative Volume as a **continuous ratio** — the one metric with no KB equivalent `analysis/indicators.py`, `strategy/engine.py`

**Novelty check (grepped before claiming this):** `grep -rin "relative volume\|relvol\|rvol"` over
`README.md` + all of `sources/` returns **zero hits**. `grep -rin "volume ratio\|average volume\|volume
spike\|volume surge"` returns only §54.23 (*"only take a Turtle breakout if it fires on above-average
volume"*), §54's volume-spike-as-exhaustion note (*"≥2× (often 3–4×) the recent norm"*), §61.3 (narrow-range
+ above-average volume), §60.5 (Force Index = `Volume × ΔClose`), and §60.9 (a static 500k-share liquidity
floor). So the KB has volume as a **boolean filter** and as a **signed force**, but nowhere as a **normalised
ratio used as a graded quantity**.

(B) p4 defines it precisely:

```
RVOL_t = Volume_t / mean(Volume_{t-30 … t-1})        # 30-day average
threshold: RVOL >= 5
```

and — unusually for this source — attaches an actual (if crude) empirical claim: his retrospective
per-trade histogram *"Performance by Instrument Relative Volume (% of 50ma)"* concentrates essentially all
cumulative profit in the **"500% and over"** bucket, with adjacent buckets near zero. That is one trader's
unsegmented, in-sample, self-selected trade log — **it is not evidence**, and it must not be treated as one.
It is a reason to *sweep* the variable, nothing more.

**Adaptation.** Halal-neutral (a volume statistic, no instrument or financing implication); long-only-neutral.
The natural use is **not** a standalone entry — it is:
- **(a)** an upgrade to **§54.23's** binary above-average-volume breakout filter into a graded confirmation,
  i.e. `rvol_grade` as an ordinal CTS input, exactly the move §55.1 made for divergence strength; and
- **(b)** a candidate ranking key for §60.2 that is **structurally different from % change** (§75.2), though
  Force Index (§60.5) already partially occupies that slot.

**⚠️ The 5× threshold does not port and must not be carried across.** It is calibrated to a $1–$20 small-cap
with a news catalyst, where volume is wildly heteroskedastic. Daily BTC volume is comparatively stable — a
5× day on BTC is a multi-year event, so a `RVOL ≥ 5` gate on our universe would fire essentially never and
would *worsen* under-deployment. The threshold is a **fitted** parameter for us, not `a_priori` (§73.12), and
per §73.3's trials budget (`N ≤ 3` on our 5yr window) **adding it costs budget we may not have.** Log it as a
low-priority sweep candidate that competes for `N` against the §74.2 lookback re-derivation and the §58.1
limit-entry change — both of which have far stronger external support.

### §75.4 The float / supply-side criterion does not transfer — logged as a non-lead `analysis/`

*"Arguably my most important criteria is the float"* (C p4) — shares available to trade, with a preference
for **<10M** (tolerating up to 50M in volatile conditions).

The crypto analogue would be **liquid free float**: circulating supply net of long-locked coins (exchange
reserves, staked ETH, vaulted PAXG). Three reasons this is not a lead:

1. **No data pipeline**, and the on-chain estimates that exist are noisy and vendor-dependent.
2. **It cannot discriminate.** Float for BTC/ETH/PAXG moves on a multi-month timescale; as a per-bar signal
   over a 3-name universe it carries almost no information. Cameron's float criterion works because it
   *selects across thousands of names*, which returns us to §75.1.
3. **It points the wrong direction for us.** He wants low float *because* it is easy to move — thin,
   manipulable, capable of 50–100% intraday swings. Our **§60.9 liquidity floor** and our whole risk posture
   want the opposite. Deliberately seeking manipulable, thin instruments also sits badly with the
   **gharar** concern that grounds our instrument screen (§28.1) — this is a preference for *engineered
   fragility*, and it is the profile of the exact assets the allowlist exists to keep out.

### §75.5 Entry triggers — level-break on the first confirming candle; entirely saturated `strategy/engine.py`

(B) p6 gives two diagrams: **"Buy first candle to make a new high"** and **"Buy first candle that breaks flat
top."** (C) p4 gives the *Gap and Go* version: *"enter the stock as it is crossing above the premarket high,
or the high of a premarket bull flag."*

Mechanically these are **channel-breakout entries** — a "flat top" is a horizontal resistance, i.e. a
short-lookback Donchian high, and "first candle to make a new high" is the candle-trigger-last ordering the
KB already holds. Fully covered by **§34.2** (structure→location→pattern→candle-trigger-last), **§23.1**,
**§17**, **§54.14**, **§74.1**. **Nothing new.** The timeframe (1-min / 5-min / **10-second**) is off by
three to four orders of magnitude from our daily bars, and §74.10 supplies crypto-specific evidence that
**daily beats intraday explicitly**.

Two small notes, neither actionable:
- The *Gap and Go* premise is **structurally N/A** — gaps require a session close, and continuous 24/7 spot
  has none (§59.4; any large "gap" on our feed is a feed-health question, §24.3).
- *"Breakout or bailout… I generally would not wait longer than 5 minutes for a trade to start working"*
  (C p4) is a fast-invalidation flavour of **§57.2's** still-unbuilt `max_hold` time-stop. It is a **third**
  independent endorsement of the *idea* (after §57.2 and §58.15), and like §58.15 the scale is unusable —
  5 minutes against our ~24-day average hold. **Recalibrate from our own MFE/MAE distribution, never port.**

### §75.6 ⚠️ Where this source CONTRADICTS the knowledge base — three negative exemplars

**(a) The core entry is the refuted family.** *"Pullback patterns — a pullback pattern is when I buy a dip.
It's sort of like 'buy low sell high'"* (A p2, C p11). The entire entry method in all three documents is
**dip-buying / pullback-into-support**, which this project refuted on its own crypto data (16% win, PF 0.17),
which §58.10a found the worst model in a controlled 36-market study, which §74.3 found *significantly
negative* on Bitcoin (p<0.01), and which §62.2 explains theoretically (scale-in is variance-optimal only
under a verified `a<0` mean-reverting regime, never established on crypto). Per the standing instruction
this is **not logged as a lead**. Note it is the identical situation to **§60.10** — a strong-context filter
wrapped around a refuted entry core; stricter filters do not un-refute it. Note also the **internal
inconsistency**: §75.2's *"I'll never buy a stock that's not already moving"* and *"buy high and sell higher"*
sit in the same documents as *"buy a dip… buy low sell high."*

**(b) ⛔ Explicit endorsement of averaging down.** (C) p7: *"I don't want to hold it if it turns into a 1-min
or 5-min pullback **unless I already have a profit cushion and can afford to average down during the
pullback**."* This directly contradicts **§54.19** (*pyramid on PROFITS only / NEVER average down*) and the
standing refutation of martingale-style scale-in. The "profit cushion" qualifier does not rescue it — it is
adding to a position that is moving against the entry thesis, mid-trade, with no pre-stipulated level.
**Logged as a negative exemplar; take no part of it.**

**(c) The 75% win-rate goal, contradicted by the source's own arithmetic.** (A) p3 / (C) p11: *"I will aim
for 75% accuracy with average winners being twice the size of my average losers."* (B) p5 reproduces a
**breakeven win-rate table** — the same `win_rate > 1/(1+R:R)` relation the KB already holds from §23.1 /
§25.5 / §35.2 — which shows a 2:1 reward:risk breaks even at **33%**. Targeting 75% at 2:1 is a demand for
roughly 2.3× the required edge, and joins **§57.4's** 70% goal as the second such negative exemplar. The
table itself is a picture of a formula we already have; **no new content.**

### §75.7 Reinforcements without novelty (logged for completeness)

- **"Rule 3: Three consecutive losers and I'm done"** (A p3) — a **second independent instance** of §57.1's
  consecutive-loss circuit breaker, which the KB flags as a rail we do not have. Same `N=3` threshold as
  §57.1. ⚠️ Both sources reset **per session/day**, which is meaningless at our ~24-day hold; §57.1's
  standing caveat that the reset window must be re-derived (bars, or a rolling trade count) is unchanged.
  This adds an author, not information.
- **"Rule 2: Daily max loss at −$100"** — magnitude circuit breaker, = rail 11. Saturated.
- **2:1 profit/loss ratio** as the standing target — = the R:R floor already in `strategy/promotion.py`.
- **20/50/200 EMA stack as a strength/weakness read** before taking a trade (C p4) — = §26.2 / §60's
  10/20/50 SMA stack. Saturated.
- **"Trade the obvious setups"** (B p4, A p2) — the claim that a widely-visible level produces better
  follow-through because more participants act on it. The KB already carries this self-fulfilling-level
  logic (§70, §59); Cameron adds a plausible mechanism and zero measurement. Worth one line only because it
  is an argument *for* using canonical, un-tuned parameter values (a 20- or 200-day high is "obvious"; a
  37-day high is not) — a soft, non-quantitative echo of §73.12's `a_priori` preference. Do not weight it.

---

## Halal exclusions and screening

- ⛔ **6× leverage.** (A) p2: *"International brokers will allow unlimited day trading and up to 6x
  leverage."* Margin borrowing = **riba**, excluded by design (§28.1, §56). The entire "which broker gives
  me more buying power" discussion is inapplicable — we trade settled cash only. Note the irony worth
  recording: his US-cash-account constraint (*"I can only trade once my previous trade has settled"*) is
  **structurally our position**, and he treats it as a handicap to be arbitraged away offshore.
- ⛔ **Averaging down** (§75.6b) — not a shariah exclusion but a hard rail exclusion (§54.19).
- ⛔ **The 10-second / 1-minute frame and the Micro Pullback chapter in its entirety** — scalping, excluded
  by the anti-scalping rail (§4.1) and independently unsupported by §74.10 (daily beats intraday on BTC,
  measured). Note §65.6 established the *shariah* argument for the anti-scalping rail was overstated; the
  *trading* argument (§74.10) stands on its own and is what excludes this material.
- ⚠️ **Deliberate selection of thin, low-float, manipulable instruments** (§75.4) — sits uneasily with the
  gharar reasoning behind our instrument screen (§28.1) and squarely against §60.9's liquidity floor.
  Excluded on both counts.
- N/A: penny-stock minimum price, T+1 settlement mechanics, US-vs-offshore broker selection, PDT rule,
  platform/commission/wiring fees, lack of US deposit insurance.

## Discarded (no agent value)

- **All of (A)**, as a document — it is a near-verbatim duplicate of (C) pp10–11 (§75 provenance note).
  Logged, not separately extracted.
- The **fill-in-the-blank workbook prompts** (*"How will you find the right stocks?"*, *"What patterns are
  you comfortable recognizing?"*, *"What will your pre-market trading routine be?"*) — course-funnel devices.
- The **"I hit rock bottom" narrative arc** and its beginner's-luck→overconfidence→despair→discipline curve
  (B p1) — psychology, and among the most saturated content in the KB (§5, §26, §57 Part I, §69).
- **"Being Present…"** (A p3, C p11) — accept market conditions, manage emotion. Saturated.
- All **CTAs and promotional links**: the scanner subscription (`warriortrading.com/scanners/`), the free
  day-trading class, the "verified earnings"/audited-broker-statement pages, the Warrior Starter / Warrior
  Pro course upsells, the live-stream pitch, and the repeated *"my results are not typical"* disclaimers.
- **Platform/UI screenshots** — eSignal chart windows, the Top Gainers scanner panel, the AVTX/NAV/BIOC/
  AHPI worked examples. The AVTX case (a +422% mover on 1,108× relative volume near all-time lows) is
  vivid and evidentially worthless: one hand-picked winning trade, chosen after the fact.
- The **breakeven win-rate table and chart** (B p5) — a graphic of `win_rate > 1/(1+R:R)`, already held from
  §23.1 / §25.5 / §35.2.
- **Bull flag / flat top pattern diagrams**, premarket bull-flag trendline drawing, VWAP break commentary —
  saturated by §24, §34, §54.23, §61.
- **Level 2 / time-and-sales exit cues** (*"heavy resistance on the level 2 in the form of a big sell
  order"*) — order-book microstructure on a 1-minute frame; no data pipeline, wrong timeframe, and
  order-book depth on Coinbase spot is not comparable to a Nasdaq level 2 book.
- The **Warren Buffett "diversification is protection against ignorance… when I see something I like, I'm
  all in"** quote, deployed to justify single-name concentration — contradicts our per-asset concentration
  cap (§10.3, §33) and is an appeal to authority with no argument attached.

## Net assessment

**This batch is near-empty of positive content, and I want to be unambiguous about that.** Twenty pages, of
which ~16 are duplicated, promotional, psychological, or off-timeframe. Its entry method is the
dip-buy/pullback family the KB has refuted three independent times; it explicitly endorses averaging down;
it sets a win-rate target its own reproduced table contradicts; and it carries no backtest, no sample count,
and no control of any kind, published by the vendor of the tool it describes.

**The extraction is worth keeping for exactly one thing, and it is a negative result: §75.1.** Reading the
canonical scanner-driven high-frequency process in its native setting shows that its frequency comes from
**universe breadth** (~5,000 names filtered brutally hard), not from a permissive rule — his per-name bar is
several times stricter than our Donchian entry. Since our universe is fixed at three assets by the halal
screen, **the mechanism does not port**, and §60.2's rank-and-fill — currently carried in the module map as
*"the first mechanism that targets the under-deployment defect directly"* — is revealed as a
**capital-allocation** mechanism that becomes a **no-op** when candidates never outnumber slots. Under-deployment
is a **signal-production** problem, and ranking cannot produce signals.

That leaves the frequency defect where §74.5/§74.12 already pointed: **a second, measurably uncorrelated rule
class is the only lever that adds trades without degrading the entry.** This source, which looked like the
best available analogue, closes a door rather than opening one — and closing it correctly is worth the file.

Secondary, and much weaker: **§75.3's relative-volume ratio** is genuinely absent from the KB (grep-verified)
and would upgrade §54.23's boolean volume filter to a graded one — but its threshold does not transfer, it
would be a *fitted* parameter competing for a trials budget of `N ≤ 3`, and it queues behind the §74.2
lookback re-derivation and the §58.1 limit-entry change, both far better supported.

**Publisher stream assessment: exhausted.** Three documents from one publisher yielding one structural
negative and one low-priority sweep candidate. Further Warrior Trading lead-magnets should not be fed in —
they are marketing artifacts for a small-cap intraday method whose timeframe, universe, instrument class,
and entry family are all outside or against this project.
