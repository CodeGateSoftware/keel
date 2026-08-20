# PRD — Competitive gap analysis: Freqtrade, Hummingbot, Jesse

**Status:** proposal · **Date:** 2026-08-20 · **Milestone:** Competitive parity — validation & reach

## Method

Four independent capability inventories, each built from primary sources (repos at a pinned
version, plus official docs), all answering the same 13–15 headings so the comparison is
like-for-like:

- **Freqtrade** `2026.7` · **Hummingbot** `v2.16.0` · **Jesse** `3.0.7` (repo cloned)
- **keel** — read from source, with the reviewer asked to distinguish *deliberately absent* from
  *not built*

That last distinction is the reason this document is not a checklist. keel forbids leverage and
short selling on fiqh grounds; "Freqtrade has shorting, keel doesn't" is a design boundary, not a
gap, and a roadmap that missed the difference would be worse than none.

---

## 1. The finding that frames everything

**Every competitor's validation tooling is a diagnostic the user may ignore. keel's is a gate.**

Jesse's own documentation concedes the point: its train/test split, Monte Carlo and rule-significance
testing are real, "but none of it is a hard gate — a user can still deploy an over-tuned strategy
directly to live." Freqtrade's `lookahead-analysis` is a command you may choose to run. Hummingbot
has neither, and its backtester models no slippage, no latency and no partial fills at all.

keel's promotion ladder refuses. `min_trades=100`, expectancy `>0`, RR `>=1.5`, win rate `>=0.55`
(0.30 for `trend_follow`), plus a PBO/CSCV gate where `pbo=None` **blocks** promotion rather than
passing (`keel/strategy/promotion.py`). Eighteen un-overridable rails sit beneath that. Hummingbot's
kill switch is a single whole-portfolio P&L threshold; Jesse's open-source core has no
portfolio-level circuit breaker at all.

**So the strategy is not to import their features. It is to adopt their validation *evidence* as
inputs to a gate that already exists.**

---

## 2. Not gaps — design boundaries

Recorded explicitly so they are never re-proposed as roadmap items.

| absent from keel | why |
|---|---|
| Short selling, leverage, margin, futures | Charter. Enforced at four independent layers: `Setup.direction: Literal["long"]` at the type level, rails 18/19, `screen.py` rejecting wrapper types `{cfd, future, perpetual, option, leveraged_token}`, and a broker port with no margin/short order kind. No config field widens it. |
| Market making, arbitrage | keel is a long-only price-taker. Hummingbot's entire architecture assumes two-sided quoting; that is a different product. |
| ML / model-driven strategies | Deliberate "deterministic-first" posture, discussed and rejected in internal research notes. |
| Dynamic pair selection from market data | keel's universe changes only via human discovery → screen → **attested** admission. Freqtrade's `VolumePairList`/`MarketCapPairList` infer tradability from market data, which is precisely what keel's compliance model forbids. |
| **Parameter optimisation / hyperopt** | See below — the most important of these. |

### Why hyperopt is not on this roadmap

Freqtrade ships Optuna-backed hyperopt across seven samplers; Jesse ships Optuna + Ray. keel has
none, and that is structural rather than missing.

`PBOResult` **cannot carry which configuration was selected** — enforced by a test that fails if a
config-identity field is ever added (`keel/research/cscv.py`). The purpose is to make it impossible
for a PBO score to become a sweep's ranking key, which is exactly what an automated search requires.
Adding hyperopt would import the failure mode the gate exists to catch.

If parameter search is ever wanted, the honest framing is: it needs Jesse's *mandatory* train/test
split plus Monte Carlo plus significance testing wrapped around it — i.e. §3 first. That is the
correct sequencing, and it is why §3 is ranked first.

---

## 3. Real gaps, ranked

### C1 — Validation tooling *(highest value)*

keel has PBO/CSCV, Deflated Sharpe, Minimum Backtest Length, independence checks and a hash-chained
trials ledger — genuinely more research machinery than any competitor. It lacks three specific
things two competitors have:

| | who has it | what it does |
|---|---|---|
| **Lookahead / recursive bias detection** | Freqtrade (`lookahead-analysis`, `recursive-analysis`) | Re-runs on truncated slices and diffs signals, catching future-data leakage and indicators whose values shift with history length |
| **Monte Carlo** | Jesse (`monte_carlo_trades`, `monte_carlo_candles`) | Reshuffles completed-trade sequence and perturbs the candle series; asks whether the observed equity curve is an outlier |
| **Rule significance testing** | Jesse (`rule_significance_testing`) | Seeded simulations testing whether specific rules beat randomised null variants |

Jesse also ships **candle perturbation pipelines** — `gaussian_noise`, `moving_block_bootstrap`,
`gaussian_resampler` — pluggable onto a strategy so it is stress-tested against alternate price
paths rather than the one historical series.

**Why this ranks first.** keel's live question right now is *"is a 14.9% win rate against a 14.9%
break-even real, or one lucky path?"* A moving-block bootstrap attacks that directly, and
complements the Bayesian `π_edge` proposed in the Quant Lab note (#432). It is also the only
category where adopting a competitor's tooling strengthens rather than dilutes keel's enforcement
model — these become gate inputs, not optional reports.

Lookahead detection deserves separate emphasis: it catches the failure that makes a backtest look
brilliant and a live run lose money, and it is a *different* failure mode from overfitting, which is
all PBO/CSCV covers.

### C2 — Walk-forward automation

All three competitors have some form. keel ran a walk-forward *procedure* manually, once, to pick
`turtle_breakout`'s 40/20 lookback (`turtle_breakout.py:181`); there is no reusable rolling-origin
validator. Note Freqtrade also lacks a scheduler here — its hyperopt overfitting control is manual
too — so this is parity with the best of a weak field.

### C3 — Wire the dormant exit primitives *(cheapest real win)*

`trail_stop_atr` (ratchet-only ATR trailing stop), `roll_to_break_even` and `scale_out` are
**implemented and unit-tested** in `keel/execution/executor.py` — and have **zero callers** in the
live agent loop. Built, tested, dormant. Reading the executor in isolation overstates what actually
runs.

Freqtrade's trailing stop (`trailing_stop_positive`, `trailing_only_offset_is_reached`) and
`adjust_trade_position` partial exits are core capabilities. keel has the primitives and simply has
not wired them.

### C4 — Notifications

keel has one integration: a generic outbound webhook, opt-in, **CRITICAL log level only**
(`keel_core/alerting.py`). Freqtrade has a near-complete Telegram *control* surface (`/status`,
`/profit`, `/forceexit`, `/reload_config`) plus configurable per-event webhooks. Jesse has Telegram,
Discord and Slack drivers with per-message routing.

Relevant beyond convenience: the rail-17 attestation expiring silently is exactly the class of event
that should reach an operator. Today it surfaces only if someone opens the TUI.

**Scope note:** notify-only. A remote *control* surface would need the #436 gate question answered
first.

### C5 — `keel doctor`

Hummingbot ships `hbot doctor`, a health-diagnostics command. keel has `status`, `versions` and
`insights`, but nothing that answers "is this deployment correctly configured" in one call. Today's
session produced a concrete case: a paper profile detecting signals and recording nothing, for two
independent reasons neither of which any single command surfaced.

### C6 — Partial-fill state

keel models market-IOC orders as fill-or-reject; reconciliation tracks terminal states only.
Freqtrade tracks partial fills as first-class state with an `order_filled()` callback and average
entry price across fills; Jesse has a `PARTIALLY FILLED` order status.

### C7 — Rules are not pluggable

Brokers are: a venue registers under the `keel.brokers` entry point and is a `pip install` away.
`RULE_REGISTRY` is a hardcoded four-entry dict in `keel/agent.py` — a fifth rule requires a core
change. All three competitors let users drop in strategies without touching core.

**Tension to resolve first:** keel's promotion gate assumes rules it can evaluate. An external rule
plugin must still pass the same ladder, and "arbitrary Python in-process" is what Freqtrade and
Jesse both accept with no sandboxing. Design before building.

---

## 4. Where keel already leads

Worth recording so it is not traded away:

- **Enforcement, not diagnostics** — the promotion gate refuses; nobody else's does.
- **18 un-overridable rails.** Jesse's OSS core has no portfolio-level breaker; Hummingbot's is one global P&L threshold.
- **Slippage modelling** — liquidity-scaled `floor × sqrt(anchor/volume)` clamped 5–50bp, versus Hummingbot's single flat scalar.
- **Production-faithful backtesting** — the fill model was rebuilt (#257/#258) after finding it modelled optionality never available live, and the fix is documented as *worsening* a reported edge.
- **A hash-chained trials ledger** with tamper-evident provenance. No competitor has an equivalent.
- **Fully open live execution.** Jesse's live and paper trading are a closed-source paid plugin; its MIT repo cannot place an order on any real exchange.

---

## 5. Phasing

| | items | rationale |
|---|---|---|
| **Phase 1** | C1 (lookahead detection, Monte Carlo, candle bootstrap, significance testing) | Directly attacks the open question; strengthens an existing gate |
| **Phase 2** | C3 (wire exits), C5 (`keel doctor`) | Cheap, high-value, low-risk |
| **Phase 3** | C4 (notifications), C2 (walk-forward) | Operator reach and validation depth |
| **Phase 4** | C6 (partial fills), C7 (rule plugins) | Larger design work, needs decisions first |

Web UI and REST API are real gaps against all three competitors but belong to the **Desktop
distribution** milestone, where they are the load-bearing piece.

## 6. Open questions

1. Do C1's outputs become **gate inputs** (blocking promotion) or reports? Recommendation: gate
   inputs — that is keel's differentiator.
2. Is a remote **control** surface ever wanted, or notify-only forever? Changes C4's scope entirely.
3. Rule plugins (C7) — is arbitrary third-party rule code acceptable in-process, given every
   competitor accepts it with no sandboxing?
