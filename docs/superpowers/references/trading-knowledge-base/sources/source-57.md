[← Knowledge Base index](../README.md)

## Source 57 — "The Trader Business-Plan" (Christopher Terry, TRADERS' magazine, Jul/Aug 2003, 4pp)

> **A 4-page magazine article, half of it self-help — but it yields two rails we genuinely do not
> have.** Part I ("Personal growth") is Napoleon Hill / Norman Vincent Peale PMA reading lists,
> positive self-talk and exercise advice: discarded entirely. Part II is a 7-point business-plan
> outline (mission, goals, financial/time commitment, record keeping, methodologies, **drawdown
> rules**, compensation), and most of it is business-lifestyle scaffolding with no agent surface.
>
> The value is concentrated in **§57.1** (a consecutive-loss circuit breaker — a *sequence* breaker,
> which is a different failure mode from our *magnitude* breaker at rail 11) and **§57.2** (two exit
> parameters we have no equivalent for). Both are mechanically specifiable and testable.
>
> It also supplies a sharp **negative exemplar** (§57.4): the author's stated goal of becoming a
> "70%+ win-ratio trader" is precisely the target our per-class promotion floors reject.

---

### 57.1 ⭐ Consecutive-loss circuit breaker — a NEW rail, distinct from rail 11

> *"If three trades in a row are losers, trading for that day will come to a halt and no further
> trades will be made."*

We have **no equivalent**. `strategy/stats.py:41` computes `max_losing_streak` as a **backtest
statistic**, and `guards.py` rail 11 is an **account-drawdown** breaker (total + weekly **percentages**).
Nothing acts on a *streak*.

That is a real gap, because the two catch different things:

| | Trips on | Detects |
|---|---|---|
| Rail 11 (existing) | drawdown **magnitude** (20% total / 8% weekly) | "you have lost too much" |
| Streak breaker (new) | **N consecutive** losers | "your edge may have stopped working" — *before* the loss accumulates |

A streak breaker is a **regime-degradation detector**, and that is directly on-point right now: the
latest sim flagged consistently-losing buckets for `turtle_breakout` on **BTC/BEARISH**, **ETH/BEARISH**
and **ETH/CHOPPY**. A rule that stands the system down after N consecutive losses is a cheap, generic
proxy for "the current regime is hostile to this rule" that needs no regime classifier to work.

⚠️ **Do NOT port the parameters — the timeframe frame is wrong for us.** "Three in a row, halt **for
that day**" is a *day-trader's* reset. Our agent holds for **~24 days on average** (sim: `avg hold 575
hrs`) and may place only a handful of trades a month, so "the rest of the day" is close to a no-op.
The reset condition has to be re-derived for our cadence — candidates to backtest:
- halt new **ENTRIES** until a configurable cool-off elapses (days, not hours),
- halt until the next **winning** trade on any rule (self-clearing), or
- halt until an **operator resumes** (fail-closed, consistent with the kill-switch posture).

Also note **scope**: this should gate **ENTRIES only**. Exits, stop-outs and DCA must remain live — a
breaker that blocked exits would trap capital in a losing position, inverting its own purpose. Compare
rail 11, which already exempts DCA (§12.6).

→ Candidate **rail 16**, `config.money_mgmt`-driven (`max_consecutive_losses`, `cooloff_*`), swept by
the backtester. **Unvalidated** — the source offers no evidence, only a practice.

**Where the threshold comes from.** `stats.max_losing_streak` (`strategy/stats.py:41`) is currently a
statistic with **no consumer** — this rail is its consumer. The breaker's threshold should be set
*above* the strategy's own historically-tested max losing streak, or it will fire on normal variance and
stand the system down during runs it was designed to survive. A separate coaching source (tier1trading,
not extracted — psychology saturated per §26) independently names *"the largest losing streak you've
tested"* as one of four numbers a trader must know before risking money, alongside win rate, average
R:R and expectancy — all four of which `BacktestResult` already computes (`stats.py:35–41`). So the
sizing input already exists and is already produced by the backtester; it simply has nothing reading it
yet.

### 57.2 Two exit parameters we have no equivalent for (T3 list)

The article's Table 3 lists 11 exit strategies. Nine are saturated (retest of swing high/low, fixed
profit objective, objective chart point, trail off the low/high, exit on close, range expansion —
all covered by §7.4/§17.3/§23.2/§24.2/§54.6). Two are not:

- **Time-based exit** — *"Exit at a time interval (i.e. four days)."* We have **no** `max_hold` /
  time-stop anywhere (`grep` for `max_hold|time_stop|bars_held` → nothing). A time stop is a standard
  trend-system component: it recycles capital out of positions that neither hit target nor stop and are
  simply going nowhere. Relevant given the sim's 24-day average hold and 1.4% total return — dead
  positions occupying rule slots is a plausible contributor.
- **Close-strength exit** — *"Exit if the day's close is less than a certain percent of the daily range
  in the direction you are trading."* i.e. exit when the bar closes weakly within its own range even if
  no stop was hit. Cheap to compute from `analysis/candles.py` primitives (`range_`, close position
  within range — the same geometry `_in_top_zone`/`_in_bottom_zone` already implement for pin bars).
  Complements **§34.1's close-based stop confirmation**: §34.1 says *don't* exit on an intraday wick;
  this says *do* exit when the **close** itself is weak. The two are consistent — both make the close
  the decision point.

→ Both are `exit_method` options for the backtester's sweep, not defaults.

### 57.3 Contingency failure-modes → reinforces the sim/live parity posture

The article's contingency list names four ways a plan fails: *"Not executing trades when signals occur /
Executing trades when there is no entry signal / Not having an exit plan for a win or loss scenario /
Allowing losses to exceed predetermined amounts."*

→ The first two are exactly the **sim/live divergence** class that `tests/sim/test_account.py`'s parity
tests exist to catch, and that the independent order-validator (broker-abstraction spec §4) is designed
to catch in live trading. Reinforcement of an existing posture, not new — but a tidy statement of *why*
that validator is worth building.

### 57.4 ⚠️ NEGATIVE exemplar — the "70%+ win-ratio" goal contradicts our promotion floors

The author's stated development goal: *"work to become a **70%+ win-ratio trader**."*

→ **This is the wrong target for our system, and the KB already says so.** §23.1/§25.5/§35.2 replace a
flat win-rate bar with the **breakeven-winrate formula** `win_rate > 1/(1+R:R)` — at R:R 3, a 25% win
rate suffices. Our live `turtle_breakout` runs at **38.7% win with R:R ≈ 2.5** and correctly passes its
`trend_follow` class floor (`promotion.py:62`, `min_win_rate=0.30`).

Chasing 70% would push the system toward **short-target mean-reversion or scalping** — straight into the
anti-scalping rail (§4.1) and away from the trend bias §54 validated. Logged because it is a *plausible-
sounding* goal that would quietly undo a deliberate design decision.

### 57.5 ⛔ Excluded (halal / spot / scope)

- **"A maximum 4-1 margin"** (§3a financial commitment) — leverage, riba (§4.9, §28.1).
- **Shorting**, assumed throughout: *"Short sells are simply a reverse of the above"*, *"Shorts are
  reversed"*, and T3 item 6, *"Don't exit but just reverse"* — long-only spot; a reverse is a short.
- **First-hour-range breakout + one-minute bull flags** (breakout entry plan) — intraday scalping
  (§4.1), and structurally **N/A to 24/7 crypto**, which has no "first hour of the session".
- **"ABC corrective wave"** (entry plan, trending environment) — Elliott-family wave counting, excluded
  under no-oracle alongside Elliott/Gann (README §271).
- **Stochastic oscillator** (*"stochastic has reached oversold condition of 20 or lower"*) — a near
  duplicate of the RSI we already have; **KISS (§26)** says keep the confluence set minimal and
  price-first. Not adopted.
- **50%-drawdown-of-base-capital halt** — noted only to record that **our rail 11 is far stricter**
  (20% total / 8% weekly). No change; a 50% breaker would be a loosening.

### 57.6 Discarded (no agent value)

All of Part I "Personal growth": the PMA reading list (Napoleon Hill *Think and Grow Rich* etc., Norman
Vincent Peale), positive self-talk scripts (*"I am a winner. I am a success."*), "associate with
like-minded people", chat rooms, and the 30–45-minutes-of-exercise-three-times-weekly advice. Also the
technical-analysis reading list (T2: Shabacker, Murphy, Edwards & Magee, Plummer, Gartley); mission
statements and career aspirations ("manage money for clients", "write a book"); **§3a/3b financial and
time commitment** ($100,000 account, $3,000/yr seminars, "8-10:00 pm" analysis windows); **§4 record
keeping** (filing cabinets, expense reports, reconciling statements by hand — our `orders`/`journal`
audit trail already exceeds this); and **§7 compensation** (paying yourself a monthly salary).

### Net assessment (saturation-honest)

**Small source, two real rails.** Roughly half the article is self-help and another quarter is
business-lifestyle scaffolding, but the drawdown section earns its place:

- **New:** §57.1 the **consecutive-loss circuit breaker** (a sequence breaker, complementing rail 11's
  magnitude breaker — with the explicit warning that its parameters must be re-derived for a 24-day
  average hold, not ported from a day-trading frame), and §57.2's **time-based exit** and
  **close-strength exit**, neither of which has any equivalent in the codebase.
- **Reinforces:** §57.3 (contingency failure-modes = the sim/live parity and order-validator posture).
- **Negative exemplar:** §57.4 the 70%-win-rate goal, which contradicts the breakeven-winrate floors.
- **Excluded/discarded:** leverage, shorts/reverses, first-hour + 1-minute scalping, ABC waves,
  stochastic, and all of Part I.

**Recommendation:** prototype §57.1 as a rail-16 candidate — it is the one item that addresses a defect
the current sim actually surfaced (regime-hostile losing buckets). Backtest the reset condition rather
than adopting "halt for the day". §57.2's two exits go into the `exit_method` sweep. Everything else is
noise.
