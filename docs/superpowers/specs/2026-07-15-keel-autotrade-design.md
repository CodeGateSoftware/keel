# keel (Auto-Trading Agent) — Design Spec

**Date:** 2026-07-15
**Status:** Draft for user review
**Supersedes:** `2026-07-14-keel-design.md` (the preview-only advisory MVP; this spec extends it into an autonomous agent)
**Author:** Elmehdi Aitbrahim (with Claude)
**Provenance for rule library & rails:** `docs/superpowers/references/trading-knowledge-base/` (22 sources; cross-refs use `§N.x` = source N §x)

---

## 1. Purpose

`keel` is a local Python agent that runs a **riba-free (interest-free), long-only, spot-crypto**
trading strategy on a dedicated Coinbase Advanced portfolio. It extends the original advisory tool into
a system that:

1. **Tracks everything in a SQLite database** — imports historical transactions from `transactions/*.csv`
   **and records every future transaction the agent conducts** (every previewed, placed, and filled order —
   paper and live — written to the DB before and after execution as part of the audit trail, §6/§14), then
   computes P&L over time. No agent-conducted transaction goes unrecorded.
2. **Monitors the market** on a schedule (spot + candles for the allowlist and current holdings) and
   evaluates a **curated, data-tuned rule library** via a **confluence-scoring engine (CTS)**.
3. **Executes trades** with a **confirm/bypass toggle** — confirmation required by default; when bypassed,
   the agent trades autonomously **subject to un-overridable safety rails**.

The design principle throughout: **the agent's intelligence lives in deterministic, backtested rules +
statistics, never in prediction.** It is a disciplined, measurable rule-executor — "a board-game set of
instructions" — not a market oracle. (See §17, honesty.)

## 2. Governing constraint — halal (non-negotiable)

All behaviour must avoid **riba (interest)** and other non-halal elements. Concretely, the system has
**zero code paths** that touch, and hard-excludes:

- **Leverage / margin / lots / borrowing** (borrowing = riba) — all sizing uses actual cash only.
- **Carry trades / rollover / funding** (interest differential = riba) — spot only, no overnight financing (§18).
- **Short selling / hedging** (requires borrowing; also not a spot mechanic) — **long-only**.
- **Staking / USDC rewards / Earn / lending / any yield APY** (detect & report for manual disable; never enable).
- **Bonds, forex carry, derivatives/futures/perps** (funding = riba-adjacent).

Permissible: owning spot assets outright, price appreciation, fee waivers (Coinbase One zero-fee).
**Short/bearish setups from the knowledge base are translated to exit rules for held assets + "don't-buy"
filters — never short entries.** Disclaimer: informational, not a religious ruling; binding questions defer
to a qualified scholar.

## 3. Scope

**In scope (v1):** SQLite tracking + CSV import + P&L; scheduled market monitoring over allowlist + holdings;
analysis primitives; one **parameterized pullback-continuation rule family** + an **RSI mean-reversion**
rule + a **DCA/dip-buy** backbone; **CTS confluence scoring → rail-bounded graded execution**; **backtesting**;
**mandatory paper-trading proving gate** with promotion/demotion; **confirm & bypass** auto-trade modes; the
full **hard-rail** set; journaling + insights.

**Deferred to v2 (§18):** harmonic patterns (Gartley/Bat/Cipher, §9/§15/§16), Fibonacci-Inversion strategy
(§9.3), macro-cycle/seasonality context (§8.4/§14/§21.2), automated rule *mining* (curated-only in v1),
news/event-blackout wiring (hook only, §3.6/§22.4).

**Out of scope:** external withdrawals (the API key has no withdrawal scope — the agent can never move funds);
enabling any yield feature; stocks/ETFs; tax filing; multi-exchange.

## 4. Run model & confirmation toggle

**Run model — scheduled polling loop** (`agent.py`): wake on an interval → pull fresh candles/spot for the
allowlist + holdings → evaluate rules on **closed** candles → (confirm|bypass) execute → sleep. Runs as a
background process or cron. Chosen over a WebSocket daemon for reproducibility (it trades the same candle
data it backtests, §2.3) and fewer failure modes.

**Confirmation toggle** (`config.auto_trade`):
```yaml
auto_trade:
  mode: confirm        # confirm (default) | bypass
  enabled: true        # master kill-switch; false halts ALL autonomous trades
  poll_interval_sec: 900   # match candle granularity (§2.8)
```
- **`confirm` (recommended default):** every intended trade pauses for interactive y/n — AI-as-assistant with
  human judgment in the loop (the "combine technology + human judgment" sweet spot, §6.5).
- **`bypass`:** trades without prompting. **Legitimate but strictly bounded** — its autonomy is safe *only
  because* the hard rails (§14) + proving gate + demotion structurally substitute for the missing human
  judgment. Bypass = "trust the tested rules within hard limits," **not** "trust the AI's judgment" (§6.5).
- `keel kill` sets `enabled:false` instantly, regardless of mode.

## 5. Architecture

Each module small, single-purpose, unit-testable. Pure-logic modules take data in / return values out (no
network); `cb_client` is the only module that touches the network.

```
keel/
  config.py              # load + validate config.yaml and .env
  data/
    db.py                # sqlite connection + schema migrations
    repository.py        # typed read/write helpers over tables
    csv_import.py        # idempotent import of transactions/*.csv -> transactions table
    cb_client.py         # thin coinbase-advanced-py wrapper (balances, candles, spot, preview, place)
    market_feed.py       # poll candles/spot -> candles table (multi-granularity, backfill)
  analysis/
    candles.py           # OHLC/body/wick; pin/doji/tweezer/marubozu/3-bar/rejection; V-top/double-bottom
    levels.py            # swing pivots; horizontal/angular/round-number/gap/magnet S/R; touch-count
    regime.py            # condition (bull/bear/range/choppy) + phase (run/pullback); choppy = no-trade
    indicators.py        # EMA, RSI(+divergence), MACD, ATR, Fibonacci retrace+extension, deceleration
    pnl.py               # FIFO cost basis; realized/unrealized P&L; drawdown + time-in-drawdown; recovery table
    insights.py          # behavioral/seasonality/edge-decay analysis; pivot-slice pruning (v1 basic)
  strategy/
    rules/               # curated library (each = detect + entry/exit), parameterized
      base.py  pullback_continuation.py  rsi_meanrev.py  dca.py
    indicators_cts.py    # CTS confluence scoring
    engine.py            # Identify->Predict->Decide->Execute; CTS score -> graded execution; kill-zone; multi-TF
    backtest.py          # historical test: intrabar resolution, no-overlap, spread/slippage, MFE/MAE
    paper.py             # forward paper-trade on live data -> orders(mode=paper)
    promotion.py         # candidate->paper->live->demoted lifecycle; expectancy/R:R/win/min-sample gates
    money_mgmt.py        # smooth-ratio sizing ramp, bounded by total + weekly DD caps
  execution/
    executor.py          # signal -> preview -> (confirm|bypass) -> place; order lifecycle; OCO/bracket; trailing
    guards.py            # THE HARD RAILS (§14) — enforced before every order, un-overridable
  journal.py             # decision + self-review journal (DB-backed)
  agent.py               # the scheduled polling loop
  cli.py                 # command wiring; rendering; disclaimer footer
tests/ ...
config.yaml
.env                     # git-ignored; CDP key scoped to a dedicated portfolio, NO withdrawal scope
```

## 6. SQLite database (ask #2)

Standard-library `sqlite3`, one file `keel.db`, schema in `data/db.py`. Core tables:

| Table | Purpose / key columns |
|---|---|
| `transactions` | Historical (CSV import) + future trades, unified; deduped by Coinbase tx id. `source` (`csv_import`/`live_fill`/`paper`), type, asset, qty, price, subtotal, total, fees, notes, `rule_id`, `order_id`. |
| `candles` | Market data, **multi-granularity**: `(product_id, granularity, ts, o,h,l,c,v)` PK. Single source for eval + backtest; supports intrabar resolution (§2.3) and multi-TF (§3.2). |
| `orders` | Every previewed/placed order: `mode` (paper/live), side, qty, limit, status, fee, `expected_fill`, `actual_fill` (slippage), raw response, `confirmation` (auto/user), `rule_id`. Full audit log. |
| `rules` | Each rule instance: kind, params (JSON), `status` (candidate/paper/live/disabled), created/promoted/demoted timestamps. |
| `signals` | Every rule firing with the indicator values + CTS score that caused it (explainability). |
| `backtests` | Per-rule results: n_trades, win-rate, avg win/loss, expectancy, max DD, max losing streak, MFE/MAE, period. |
| `pnl_daily` | Daily mark-to-market per asset: qty, avg cost (FIFO), price, realized + unrealized P&L. Dimensions (pair/date) for slicing (§20.7). |
| `agent_state` | Runtime: kill-switch, auto_trade mode, running daily/weekly spend + drawdown accumulators, DCA budget. |
| `journal` | Live self-review: emotion scores, `rules_followed`, `errors_made` + $impact, chart-note, screenshot ref (§2.5/§20.8). |

`analysis/pnl.py` computes FIFO P&L from `transactions` → drives `pnl_daily` and the `pnl` report.

## 7. Market monitoring & analysis primitives (ask #3, part 1)

- `market_feed.py` polls candles + spot for **allowlist + current holdings**, backfilling `history_days`
  on first run, at **multiple granularities** (a higher-TF for bias, the trading TF, and a finer TF for
  intrabar resolution). Interval matches granularity (§2.8, §22.1 crypto-calibrated).
- **Primitives** (pure functions, offline-testable):
  - `candles.py` — body/wick math; **pin bar** (open+close in outer 30%, §2.1), **doji**, **tweezer** (equal
    highs/lows, §7.2), **marubozu**, **three-bar reversal** (§8.2), **rejection/hammer/shooting-star**,
    **V-top / double-bottom** (full rules: RSI-extreme + shallow-pullback >0.382 + divergence, §21.1). Pattern
    reliability weights: low-test > tweezer > doji (§7.2).
  - `levels.py` — swing pivots; **horizontal + angular (trendline)** S/R; **round-number** ("even handle");
    **gap** levels; **magnet** levels (well-tested HTF levels attract price, §9.2). Strength = **touch count
    (min 3 on a higher TF)** (§7.3).
  - `regime.py` — condition ∈ {bullish, bearish, ranging, **choppy → no-trade**} from swing structure; phase
    ∈ {run, pullback}; entries only in pullbacks (§1.2).
  - `indicators.py` — **EMA** (fan 8/20/50 or 20/50/200, §7.1), **RSI** at **80/20 extremes** + **divergence**
    (§4.4), **MACD**, **ATR** (crypto-calibrated, §22.1), **Fibonacci** retrace (0.382/0.5/0.618/0.786/0.886)
    + extension (1.272/1.618, §3.1), **deceleration** (shrinking bodies, §1.4).

## 8. Rule library (ask #3, part 2)

Curated, interpretable, **data-tuned** — the *structure* is fixed; *parameters* are optimized on our data and
forward-proven via the paper gate. **No black-box mining in v1** (validated by 4 sources, §3.7/§5.4/§7.6).

**Rule 1 — Parameterized pullback-continuation family** (unifies Sources 2 & 7, §7.1). Long-tradeable
(bullish); the bearish mirror is an **exit/don't-buy filter**. Tunable knobs: `ema_periods`, `trend_filter`
(EMA fan + optional 200-EMA macro filter), `entry_zone` (EMA touch or 20–50 band), `signal_patterns`,
`buffer_ticks` (2–5), `stop_method` (fixed-ticks | **ATR**, §17.3), `target_method` (**1:1 measured** | **prev
swing high** | **Fib 1.272/1.618 ext**). Framework: **Identify → Predict → Decide → Execute** (§2.0).

**Rule 2 — RSI mean-reversion** (§3.3): oversold (RSI<20) bounce at support = buy; overbought (RSI>80) = exit/
don't-buy filter. Confluence with divergence (§4.4).

**Rule 3 — DCA / dip-buy backbone** (§10.8/§12.1): scheduled accumulation of allowlist assets; **benefits from
buying through drawdowns** (proven: continuing-through-drawdown beat perfectly-timed DCA, §12.1). Distinct
order class with its own small capped budget — **exempt from the rule-trading DD breaker but still bounded by
allowlist + per-asset cap + kill-switch** (§12.6).

## 9. Confluence engine (CTS) & execution logic

`engine.py` scores each candidate setup with a **Confluence Trading Score (CTS)** — points per factor: market
condition, phase, S/R strength (touch count), round-number/magnet proximity, deceleration, EMA alignment,
RSI extreme + divergence, candlestick pattern, Fib confluence, seasonality (low-weight, v2). Signals only
count **at a level** (§1.2). The **total score selects graded execution** (§8.1) via the **4 entry techniques**
(§17.1):

| CTS score | Entry technique | Posture |
|---|---|---|
| Low | require **3-bar-reversal confirmation** | smaller size, wider stop |
| Mid | wait for the **single signal candle** | base size |
| High | **aggressive** market/limit on level touch | larger size (toward cap), tighter stop |

**⚠️ Rail-bounded:** "more aggressive" scales size *up toward* the caps — **never beyond** the §14 rails
(§8.1/§10.6). Sizing varies **only** along the documented CTS ladder — arbitrary under- or over-sizing is a
rule violation (§10.6). **Kill zone:** entries are only valid inside the price band that yields ≥ the R:R floor
(§17.2); confluence narrows it. **Multi-timeframe:** bias gate on a higher TF, trigger on the trading TF (§3.2).
**News/event blackout** hook (skip entries around high-impact events) — wiring deferred (§3.6/§22.4).

## 10. Execution & order lifecycle

`executor.py`: signal → `preview_order` → (confirm prompt | bypass) → place → log to `orders`.
- **Order types & roles** (§1.5): market / limit / stop each serve entry, stop-loss, or target by context.
- **Validate on candle close only**; the entry order is good for the **next candle only**, then cancel &
  re-evaluate (§2.2). Binary, no "close-enough" (§2.2/§21.3).
- **OCO / bracket:** stop + target linked so filling one auto-cancels the other (prevents silent re-entry, §3.5).
- **Partial scale-out** (e.g. half at T1) then **roll stop to break-even** (risk-free runner) — place entry+stop
  first, add target legs only after fill (§19.1).
- **Trailing stop** (trend-continuation runners): trail 1 ATR below each **confirmed** new structure low on a
  lower TF; full exit at major structure. Trailing only ratchets **toward profit** — consistent with the
  no-stop-widening rail (§5.1/§19.3).
- **Front-running:** place orders a tick/% beyond the level to ensure fill (§1.5); crypto uses %/ATR, not pips (§22.1).

## 11. Proving gate, promotion/demotion & metrics

**Lifecycle:** `candidate → (backtest passes) → paper → (forward track record clears floor) → live →
(rolling stats drop below floor) → demoted`.

- **Paper gate is mandatory** — new/changed rules shadow-trade live data to `orders(mode=paper)` before any
  real money (a locked brainstorming decision; the strongest protection against overfit rules losing money).
- **Promotion floor:** positive expectancy `E=(1+W/L)·P−1 > 0` (§1.6) **and** **R:R ≥ 1.5–2 with win-rate ≥ 55%**
  (§4.5), over a **minimum sample of 100 trades OR 5 years** (§20.6), with **max losing streak & drawdown**
  within tested bounds (§5.2).
- **Demotion path (essential, not optional):** simple mechanical edges decay as markets get efficient (3 sources:
  §5.2/§6.3/§8.5). A live rule (or pair/time bucket, §20.7) whose rolling stats fall below floor auto-reverts to
  paper/disabled.
- **Metrics tracked:** win-rate, avg win/loss, expectancy, profit factor, max & avg losing streak, **max drawdown
  + time-in-drawdown** (§4.6/§10.5), **MFE/MAE** (for target/stop tuning without re-running, §20.2).

## 12. Backtesting

`backtest.py` runs a rule over historical `candles`:
- **Intrabar order-of-events:** when one candle spans both entry and stop, **drop to a finer granularity** to
  determine which hit first (or whether the stop was breached before entry, invalidating the trade) — else
  results are optimistically wrong (§2.3).
- **No overlapping trades:** one open position per instrument; skip signals while in a trade (§20.5).
- **Model costs:** spread + a slippage assumption + fees; the live executor logs expected-vs-actual fill (§4.2).
- Mantra surfaced in output: **"backtesting exists to lower expectations, not raise them"** (§2.3).
- **Automation caveat (§20.9):** we automate backtesting (it's software); the substitutes for a manual tester's
  earned trust are the paper gate + conservative modeling + demotion + confirm-mode default. Automation ≠ blind trust.

## 13. Money management

`money_mgmt.py` — **fixed-fractional on current equity** (§1.5, base 1%/trade) with an optional **smooth-ratio
ramp**: a **profit-trigger** (grow equity by $X before increasing size) + **acceleration** (units added per
trigger), **bounded by max-total AND max-weekly drawdown** (§20.3/§20.4). Compares fixed-size vs ramped equity
curves; a ramp that would breach a DD bound is rejected. Bounded by all §14 rails. Compounding is intrinsic
(sizing on current equity, §4.7), capped by the funded portfolio (§2.8).

## 14. Safety rails (the hard rails)

Enforced in `guards.py` **before every order**, **un-overridable in any mode (incl. bypass)**:

1. **Halal allowlist** — only configured assets; never staking/interest/margin.
2. **Per-order + per-day $ caps.**
3. **Total open-exposure cap** — sum of at-risk capital across all open positions (§4.1).
4. **Correlation-adjusted sizing** — scale down correlated simultaneous positions (crypto is highly
   correlated — BTC/ETH/… ≈ one "long crypto beta" bet, §4.1).
5. **Per-asset concentration cap** — max % of portfolio in any single allowlist asset (§10.3).
6. **Min-move / anti-scalping** — reject targets that don't clear spread+fees by a margin (§4.1).
7. **No averaging into losers (no martingale)** — never add to an underwater position (§5.1).
8. **No stop-loss widening** — stops only ratchet toward profit (§5.1).
9. **Sell-only-on-rule** — no arbitrary liquidation; sells only on a defined exit/harvest.
10. **Account-drawdown circuit breaker — total AND weekly** — halt new rule entries if DD breaches config
    thresholds; DCA continues within its own capped budget (§10.3/§20.4/§12.6).
11. **Stale-data / feed-health guard** — never evaluate/trade on stale candles or an erroring API; skip &
    alert (§10.4/§22.1).
12. **Kill-switch + full audit log** — one flag halts all autonomy; every order logged before/after.

**Operational security (built in Phase 3):**
- **API key scoping:** CDP key **scoped to a dedicated portfolio, no withdrawal scope** (agent can never move
  funds out); exchange counterparty risk (Coinbase custody) is a named, accepted risk; fund with only a small
  losable slice (§22.3).
- **No classic user authentication.** For a local single-user CLI/daemon, logins/accounts/sessions are YAGNI
  (OS access already opens that door). We invest instead in the two controls below.
- **Portable encrypted secrets vault** (`security/secrets.py`): the CDP key/secret (and any remote-control token)
  live in an **AES-GCM-encrypted `secrets.enc`** unlocked by a **master passphrase** (passphrase → scrypt KDF →
  key). One file, **copyable between machines** (explicitly *not* the machine-bound OS Keychain, so it's
  transportable). `chmod 600`; secrets never logged; `.enc`/`.env` git-ignored. An optional per-machine keychain
  *cache* of the derived key may be added for convenience, but the portable `.enc` is the source of truth.
- **Dangerous-action authorization gate** (`security/authz.py`): a passphrase (stored as a scrypt hash,
  rate-limited) required **only** to **arm bypass/autonomous mode**, **raise caps above config maxima**, or
  **disable the kill-switch / resume**. Read-only commands and confirm-mode trades need none. This is local
  *authorization*, not a login — it forces intentionality and blocks casual/accidental/shoulder-surf misuse of
  the autonomy capability.
- **Honest boundary:** on a single-user machine the real security boundary is the **OS account + full-disk
  encryption**. These controls are defense-in-depth (reduce plaintext exposure, prevent casual misuse); they do
  **not** stop an attacker who already holds the OS account. Stated so they aren't mistaken for more.
- **Remote control & phone notifications** are a **deferred future enhancement** (off by default; local-only until
  toggled) — designed separately in `2026-07-16-keel-remote-control-design.md`, to be implemented later.

## 15. Insights & journaling

- `insights.py` (v1 basic) — pivot-slice performance by pair × day × time × entry-type × timeframe to surface
  **weak buckets** → feed the demotion path (auto-prune, §20.7); behavioral flags in confirm mode (loss-aversion/
  herd if the human repeatedly rejects rule-approved dip-buys, §6.1/§12.3); seasonality/edge-decay (low-weight).
- `journal.py` — two tracks (§2.4): **system-performance** (live vs backtest, detect edge decay) and
  **self-review** (emotion 0–10, `rules_followed`, `errors_made` — "winning by not following rules is not to be
  celebrated; approximate zero performance discrepancy", §20.8). The mechanical agent is structurally immune to
  FOMO/overtrading/panic (§4.10/§12.3); journaling matters most for the human in confirm mode.

## 16. Config, commands, error handling

**Config** (`config.yaml`): `allowlist`, `target_weights`, `caps` (per-order/day, exposure, per-asset),
`risk_pct`, `auto_trade` (mode/enabled/interval), `promotion` (min trades, expectancy, R:R, win floors),
`money_mgmt` (profit-trigger, acceleration, max-total-DD, max-weekly-DD), `market_data` (granularities,
history_days), `dca` (budget, cadence). Secrets in git-ignored `.env`.

**Commands:** `db import`, `monitor [--loop]`, `agent --loop [--confirm|--bypass]`, `rules list|backtest|promote|
demote|disable`, `pnl`, `insights`, `journal [add|list]`, `kill`/`resume`.

**Error handling:** missing/invalid config → name the key, never silent defaults for allowlist/caps; API failure
on a live command → explain, offline commands still work; malformed CSV rows → skipped with a counted warning;
unexpected fee/price → abort that order, surface raw response, place nothing; stale feed → skip cycle + alert.

## 17. What this system will NOT promise (honesty)

The engine measures and enforces discipline; it does **not** guarantee profit. The paper gate reduces — but
cannot eliminate — overfit rules underperforming live. Fees + a slippage assumption are modeled; live fills
differ. Fixed polling misses intra-poll moves by design. **Simple mechanical edges decay** — hence the demotion
path. **No point forecasts** — the agent trades tested rules + probabilities, never a predicted price/date; an
LLM may explain/summarize/flag but **never decides an entry** (§6.4/§12.5/§14.4). These are stated in the spec
and in the tool's output.

## 18. Deferred to v2 (recorded, not built)

Harmonic patterns with full ratios — **Gartley** (§15), **Bat** (§16.2), Cipher, ABCD/equal-measured-move;
**Fibonacci-Inversion** strategy (§9.3); **macro-cycle + seasonality** context (BTC ~4yr cycle, monthly
seasonality, drawdown-from-ATH regime — §8.4/§14/§21.2), **low-weight, backtested, never standalone**; automated
**rule mining**; **news/event-blackout** wiring (halving/upgrades/regulation, §22.4). All would pass the same
paper gate, long-mirror only.

## 19. Implementation phasing (for the plan)

The system is large; build in dependency waves so each is independently testable:
- **Phase 1 (offline):** `db` + `csv_import` + `pnl` + `analysis/*` primitives + `market_feed`. Fully unit-tested,
  no live money. Delivers the DB, P&L reports, and data ingestion (ask #2 + monitoring).
- **Phase 2 (paper):** `strategy/rules` + `indicators_cts` + `engine` + `backtest` + `paper` + `promotion`.
  Rules backtest and paper-trade; nothing live.
- **Phase 3 (live, confirm):** `execution/executor` + `guards` (all rails) + `agent` loop in **confirm mode**.
- **Phase 4 (autonomy + polish):** `bypass` mode, `money_mgmt` ramp, `insights`, journaling.

## 20. Testing strategy (TDD)

Tests-first for each pure-logic module against fixtures: `csv_import` (dedupe, malformed rows), `pnl` (FIFO,
realized/unrealized, drawdown), `candles`/`levels`/`regime`/`indicators` (each pattern/level/regime),
`rules`/`engine` (weights, CTS tiers, kill-zone, allowlist/caps), `backtest` (intrabar resolution, no-overlap,
MFE/MAE), `promotion` (gates, demotion), `guards` (every rail rejects correctly, incl. bypass mode),
`money_mgmt` (ramp bounded by DD caps). `cb_client` exercised against canned response fixtures — no live calls
in tests.

## 21. Open items (user to confirm)

- Real `allowlist` + `target_weights` (spec assumes BTC/ETH/PAXG placeholder), `risk_pct` (default 1%), and
  cap values (per-order/day/exposure/per-asset).
- Confirmation-toggle framing in §4 (bypass = "trust tested rules within rails, not AI judgment") — confirm OK.
- The DCA-exempt-from-DD-breaker nuance (§12.6) — confirm OK.
- Candle granularities + `poll_interval_sec` (crypto-calibrated, §22.1).
- Whether any deferred-v2 item (§18) should be promoted into v1.

---

*Informational, not financial or religious advice. Confirm compliance questions with a qualified scholar.*
