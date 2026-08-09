# QuantCrawler teardown — what transfers to keel, near-term and as a SaaS

**Date:** 2026-08-09
**Subject:** https://quantcrawler.com (299 URLs mapped; ~30 pages scraped across product, strategy, and GTM clusters)
**Question:** what strategies or context are useful to keel now, and later if keel becomes a SaaS?

## Summary

QuantCrawler sells webhook-driven trade execution to retail futures/prop-firm traders: **Ghost**
($69/mo) turns TradingView alerts into broker orders across 9 venues, **GhostGuard** is its risk
layer, **Trade Copier** ($69/mo) mirrors a leader account to unlimited followers, **Elite** ($169/mo)
bundles everything. It is wrapped in a large programmatic-SEO surface (23 free calculators, ~110
articles, a 24-firm prop-firm directory with faceted and pairwise pages, ~60 templated landing pages).

The useful yield is **asymmetric, and not where you'd expect**:

- Their **trading content is near-worthless to keel** — SEO recitation of textbook indicators. Their
  one rigor-claiming page advertises a methodology keel's own research harness is built to reject.
- Their **execution-safety product design is worth studying**, and comparing keel against it found a
  real P0: a broker-rejected bracket leaves a filled position unprotected for up to ~24h with no
  recovery path and no alert (§1.1, §1.2). keel's rails themselves came out of the comparison well.
- Their **GTM machine is the main long-term lesson** — and it is mostly buildable by one person.

The headline: keel is **methodologically ahead** of QuantCrawler and **operationally behind** it.

---

## 1. Short-term: engineering items for keel today

### 1.1 P0 — an unprotected-position hole on broker reject (verified)

QuantCrawler's **naked-position guard**: "Automated entries that cannot be given a broker stop loss
are refused outright rather than submitted unprotected," paired with **stop verification & flatten**
— confirm after the fill that the stop is really working at the broker, flatten if not.

There **is** a real hole in keel here — but it is reached through a different door than it first
appears, and two of the three obvious fixes are wrong. The design is mostly sound:

- `place_bracket()` (`executor.py:741`) sends **ONE native exchange-side trigger bracket**, so "the
  exchange owns the stop-vs-target race" — stronger than separate legs, which can double-fill.
- Rails 3, 4, 5, 6, 8, 11, 13, 14 (per-day cap, exposure, correlation, concentration, martingale,
  drawdown, USDC-funding, monthly allowance) are all **`is_buy`-gated** (`guards.py:366` onward) and
  cannot veto a protective sell. Rail 13 says so explicitly: *"SELL is exempt — it produces quote
  currency, it doesn't consume it."*
- At initial placement a veto is close to unreachable: the entry just cleared rails 1, 2 and 12
  moments earlier, and the bracket's notional (`qty × stop`) is strictly *smaller* than the entry's.
**But `if not result.placed` (`executor.py:772`) catches more than a rails veto — it also catches a
broker rejection** (min-size, precision, a venue error; `executor.py:527-541`). That is entirely
reachable, and it produces exactly the outcome the rails path cannot: a filled entry with no
protective bracket, recorded at `WARNING executor.bracket_not_placed` and otherwise tolerated per
the `agent.py:308-310` comment.

Three things make that worse than it looks:

- **No recovery.** `reconcile_open_orders` iterates only `status="pending"` (`reconcile.py:52`). A
  never-placed bracket writes **no order row at all** (the insert at `executor.py:481` is *after*
  the guard gate); a broker-rejected one is written `rejected`. Neither is `pending`, so
  `_rebracket_or_escalate` never sees it. That machinery only heals a bracket that *was* accepted
  and later died. Nothing scans held positions for a missing bracket.
- **The window is ~24 hours, not one.** `com.keel.live.plist` has 24 triggers, but
  `keel-live-run.sh:74` gates on a UTC day-stamp — "the trigger count is catch-up BREADTH, not
  cadence." One cycle per UTC day.
- **There is no flatten.** `keel kill` sets `kill_switch`, which halts new orders and closes
  nothing. No liquidate command exists.

Note also that `_roll_stop`'s carefully-written `CRITICAL executor.position_unprotected`
(`executor.py:913-929`) is **dead code in production** — `roll_to_break_even`, `trail_stop_atr` and
`scale_out` have no live callers, and `tests/execution/test_executor.py:1415` is an explicit
tripwire asserting exactly that. So on the reachable path there isn't even a CRITICAL; there's a
WARNING.

**The fix is not any of the three I first proposed.** Specifically:

- **Do not exempt protective sells from the notional/exposure rails.** They are already exempt
  (`is_buy`-gated), so it buys nothing — and rail 2 is the *only* per-order magnitude check that
  runs on the sell side at all. Removing it would leave an oversized SELL from a qty bug with
  nothing between it and the exchange.
- **A rails pre-flight is near-worthless** — there is nothing for it to catch. A `broker.preview_order`
  pre-flight of the *bracket* before placing the entry would catch the real cause (min-size and
  precision rejects). Build that one.
- **Auto-flatten is the wrong reflex**: a market sell fired on a bracket reject dumps into whatever
  book exists, and the likeliest reject cause — a degraded broker API — is exactly when the flatten
  also fails.

**Recommended instead:** drive `_rebracket_or_escalate` off the `positions` ledger — tranches whose
bracket order is absent or `rejected` — rather than off `status="pending"`. That reuses machinery
that already exists, bounds the window to one cycle, and needs no rail exemption. Pair it with
GhostGuard's **stop verification after fill** (confirm the bracket is really live at the exchange
rather than trusting the accepted order).

A related latent gap surfaced on the way: because `place_bracket` passes the stop level as `entry`
with `stop=None`, **rail 9 (no-stop-widening) never evaluates the bracket's own stop level**. The
problem on this path is too *little* rail coverage, not too much.

### 1.2 P0 — off-machine alerting (what makes §1.1 dangerous)

keel's only alerting is `osascript display notification` in `keel-live-run.sh`. The live deployment
is unmonitorable from anywhere except that one Mac.

§1.1 is what makes this urgent rather than merely untidy. `reconcile.py:199` raises `CRITICAL
reconcile.position_unprotected` saying "Re-place one or close it before trading on" — an escalation
addressed to a human, delivered to a log file on a machine nobody is watching. And on §1.1's
reachable path the signal is only `WARNING executor.bracket_not_placed`, which would not stand out
even if someone were reading.

The fix is **one webhook** (ntfy/Pushover/Telegram/email) fired on a small set of events:
`bracket_not_placed`, `position_unprotected`, kill-switch trip, drawdown breaker, stale feed, cycle
failure. Perhaps a day's work, no dependencies of consequence. Given a once-daily cycle and no
flatten command, the difference between a WARNING in a log and a push notification is the
difference between a bad trade and a 24-hour unhedged position.

### 1.3 Capability honesty for the in-flight Robinhood adapter

Their recurring claim — *"where unsupported, Ghost refuses rather than silently substituting"*
(limit entries are live on Tradovate/ProjectX; other brokers **refuse** rather than quietly
market-fill) — is the right answer to the Robinhood problem. Robinhood supports only base-quantity
market orders, so keel's quote-sized entry model (`MarketIOCByQuote`) can't open a position there.

Declare it in `BrokerCapabilities.supported_orders` and let the adapter refuse. Do **not** approximate
a quote-size entry by converting to base quantity at a fetched price — that silently changes order
semantics on a live-money path. keel's port already has the vocabulary to express this honestly;
use it. (Same for `fees_usd` hardcoded to `0`, which currently breaks subscription-lapse detection —
better to declare fee reporting unsupported than to report a wrong number.)

### 1.4 Small hardening items worth stealing

| Their control | keel status | Note |
|---|---|---|
| `rejectAfter` — ignore alerts older than N seconds | keel has a stale-feed rail | Adjacent, probably covered |
| `test: true` — full validation, no broker call | Not present | A true dry-run that exercises every rail without ordering is genuinely useful for config changes |
| Price sanity: alert price >5% off live market → substitute live price | Not present | keel's signals are internally generated so risk is lower, but a divergence check between signal-time and execution-time price is a cheap guard against stale-candle entries |
| Duplicate suppression: identical signal within 60s ignored | Runner-script day-stamp idempotency | keel's dedupe lives *only* in the shell wrapper — `client_order_id` is a fresh uuid4 each call. Worth moving into the app |
| 3-strike confirmation before squaring state on a broker read | `reconcile.py` exists | The "never act on a single bad read" pattern is worth checking against keel's reconciler |
| Exits are never blocked by any risk control | Effectively already true | keel's cap/exposure/drawdown rails are all `is_buy`-gated; rail 13 states "SELL is exempt". Worth writing down as an explicit invariant with a test, since it currently holds by convention across ten separate rails |
| Distance-to-limit telemetry (their "Command Center") | Not present | Surface headroom to each drawdown breaker / cap in `keel status`, not just tripped/not-tripped |

### 1.5 Trading content — take almost nothing

The strategy sweep was deliberately skeptical, and most of the library is generic:

- **Filler:** AI Strategy Lab (zero disclosed mechanics), scalping, breakout, RSI, and MA pages —
  textbook definitions, no validated edge.
- **Marginally reusable:** MACD settings-by-style table and the "only take crossovers on the
  matching side of the zero line" filter; volume-surge 1.5–2× breakout confirmation; ADX > 25 trend
  gate. All are *candidate baselines to test*, not findings.
- **Actually worth borrowing (as design, not alpha):** QC-Trend's **execution scaffold** —
  a fixed exit-priority order per bar (stop → target → trailing → opposite flip) with ties resolved
  conservatively as a stop; breakeven arming delayed one bar after entry; breakeven scratches
  bucketed separately so they don't pollute the win-rate denominator. These are good backtest
  conventions and cost nothing to adopt.
Three items from the fuller sweep *are* worth acting on, and none are "strategies":

- **Funding rate as a crowding signal.** Perp funding settles every 8h; sustained high positive
  funding means the market is over-levered long, and is floated as a contrarian indicator. keel is
  spot-only so it never *pays* funding — but that's exactly why this is interesting: funding is a
  free sentiment/positioning read on the same assets keel trades, available without taking any
  derivative exposure. A testable regime filter for spot entries, and unlike the rest of the site's
  content it isn't a repackaged textbook indicator. Worth a trials-ledger entry.
- **Volume Profile is unreliable on crypto** because volume is fragmented across venues — their own
  page says so. A useful negative result: it rules out a family of levels-based features for keel
  rather than adding one.
- **"Confluence, not consensus."** Their stated design rule is one indicator *per category*
  (trend/momentum/volume/volatility) with 3-of-4 agreement, explicitly warning that stacking
  redundant same-category indicators (RSI + Stochastic) manufactures false confirmation. This is a
  fair challenge to keel's 11-factor CTS (`strategy/indicators_cts.py`), which carries RSI extreme,
  RSI divergence, and deceleration — arguably three reads of the same momentum axis. If several CTS
  factors are collinear, the score overstates confluence exactly when the factors agree because
  they're measuring one thing. Cheap to check: a correlation matrix across CTS factor contributions
  on historical signals. That is a real, testable critique of existing keel code.

Also noted for the risk backlog: the **trailing-drawdown ratchet** — a high-water-mark floor that
rises with equity and *never falls*, so a hot start permanently tightens the floor. keel already
applies ratchet-only logic to stop rolls but its drawdown breakers are not high-water-marked in this
way. Whether that's desirable for keel is a genuine open question, not an obvious win.

- **Their one rigor claim fails inspection.** `/orb-results` is badged "TICK VALIDATED" (736M CME
  ticks, 70/30 walk-forward, **50,000 trials per ticker**), but the five public rows — MGC, MNQ, YM,
  RTY, MES — are *identical* ($18,434 / $1,993 / 1.80 / 41.1% / 146, same 04:10–10:15 window). That's
  placeholder teaser data behind a signup gate. And 50k trials/ticker with no multiple-testing
  correction is precisely the setup `keel/research/cscv.py` (PBO) and `deflate.py` (Deflated Sharpe)
  exist to defend against. **keel's research discipline is the thing keel has that they don't** —
  it should be treated as a differentiator, not a cost.

---

## 2. Long-term: if keel becomes a SaaS

### 2.1 Positioning — lead with what they structurally cannot copy

Two assets, both already built:

1. **Halal / Sharia-compliant by construction.** Long-only, spot-only, no leverage, no riba,
   settled-cash sizing, a screening + purification module, withdrawal/qabd compliance rails — all
   enforced structurally in `guards.py`, not as a toggle. This is a genuine, underserved, global
   segment with almost no credible algorithmic offering, and a compliance story competitors can't
   retrofit. QuantCrawler's own playbook validates the shape of the move: they beat broader rivals
   (TradersPost, 17+ brokers) by being *narrower* and owning an audience.
2. **Provable research discipline.** PBO, Deflated Sharpe, a trials ledger that makes
   multiple-testing correction auditable, and an independence harness. In a market where the
   competition ships "50,000 trials per ticker" as a *selling point*, publishing honest,
   deflated, multiple-testing-corrected results is a real differentiator — and the trials ledger
   means keel can substantiate it.

### 2.2 Multi-tenancy — the real blockers

From the architecture profile, keel is hard single-tenant in ways that are structural, not cosmetic:
the `profile` table is a schema-enforced singleton (`CHECK (id = 1)`), state lives in one SQLite file,
credentials in one flat `.env`, `agent_state` is a flat global key-value namespace, rail 14 hardcodes
`DEFAULT_VENUE = "coinbase"`, and scheduling is one macOS LaunchAgent per deployment. Serving N users
today means N processes, N DB files, N plists.

The order that matters: **a credentials vault and per-tenant isolation come before anything else** —
custodying other people's exchange API keys is the entire risk surface of this business. Note also
that keel's per-user economics differ fundamentally from QuantCrawler's: they relay webhooks
(near-zero marginal cost), whereas keel *generates* signals and needs market data and a compute
cycle per tenant.

There is also a regulatory question that dwarfs the engineering one. QuantCrawler executes what the
*user's own* strategy tells it to and papers this with heavy disclosure boilerplate (futures risk,
hypothetical-performance, testimonial disclaimers on every page, plus Terms/Privacy/Security/Risk
Disclosure pages, Stripe, GDPR/CCPA). keel **decides what to trade** — that is much closer to
discretionary advice/management, and in most jurisdictions is a licensed activity. **This needs a
real legal answer before any paid multi-tenant launch, not a disclaimer footer.** The plausible
routes: ship keel as self-hosted software the user runs against their own keys (software, not
advice), or license/register properly. This is the single biggest gating item for the SaaS path.

### 2.3 The GTM playbook worth copying

Ranked by leverage-per-effort for a solo builder:

1. **Free ungated calculators.** Their 23 calculators are pure top-of-funnel: no email wall, soft
   post-result CTA. For keel: position sizing, drawdown-recovery (the 10%→11%, 20%→25%, 50%→100%
   table), risk-of-ruin, DCA, zakat/purification. That last one is *unique to keel's niche and
   nobody else will build it*. Cheap, evergreen, zero maintenance.
2. **A maintained data asset.** Their prop-firm directory — rules, payouts, offers, and notably a
   **rule-changes changelog** — is the one thing competitors can't clone quickly, because it's
   ongoing monitoring work. keel's analogue: a **halal-screening registry** for crypto assets with a
   dated changelog of status changes and the reasoning. keel already generates this internally
   (`asset_attestations`, `screen_exceptions`, the allowlist). Publishing it is close to free and is
   both an SEO moat and a credibility artifact.
3. **Radical transparency on results.** They publish live platform counters and a Trustpilot badge;
   their community leaderboard even carries an honest "not ranked below 30 trades / 30 days"
   small-sample threshold. keel can go further and publish *deflated* results with methodology.
4. **Competitor-conquest pages** (vs-3Commas, vs-Coinrule, vs-TradingView-bots). Cheap, and their
   version works because it reframes audience fit rather than attacking.
5. **Community as retention** (Discord + video). Highest ongoing cost; defer.

### 2.4 Pricing architecture

Their ladder — free tools → $9.99/mo → $69/mo → $169/mo, with the cheap tier *bundled free* into the
expensive one to kill the "paying twice" objection — is a clean pattern. Their sharpest move is
**flat pricing where competitors meter**: unlimited accounts at $69 against rivals' per-account fees.
keel's analogue is flat pricing per user regardless of capital or asset count — but note keel's
marginal cost per tenant is real (data + compute), so this needs modelling, not copying.

---

## 3. Decisions and non-goals

- **Not recommending** adopting any QuantCrawler *strategy* content as a keel rule. It would not
  survive keel's own PBO/DSR gate, and the ORB page's placeholder rows undercut the one rigorous
  claim on the site.
- **Not recommending** a webhook/TradingView ingestion feature. It's their core product, but keel
  generates its own signals; accepting external alerts would import an unvalidated edge and bypass
  the research discipline that is keel's actual advantage.
- **This week: §1.1 and §1.2.** Re-point `_rebracket_or_escalate` at the `positions` ledger, and add
  the alert webhook. Both are small, both are safety, and together they close a hole that currently
  runs for a full day unobserved. Everything else here can wait.
- **How this document was reached, because it bears on trusting it.** The first draft asserted a P0
  naked-position defect caused by a *rails veto*. That was wrong — rails 3/4/5/6/8/11/13/14/16/17
  are `is_buy`-gated and cannot touch a protective sell. The second draft then over-corrected to
  "keel handles this well, reconcile heals it" — also wrong, because `reconcile` scans only
  `pending` orders and never sees a bracket that was never placed. The hole is real, but it is
  reached via **broker rejection**, and the fix is neither of the two obvious ones. Three passes
  over the same code gave three different answers; only the third survives reading `guards.py`,
  `reconcile.py`, and `keel-live-run.sh` together. Worth recording, because a competitor teardown
  is a genre that rewards confident conclusions over verified ones.

## Provenance

Site mapped with `firecrawl map` (299 URLs). ~30 pages scraped across four parallel research agents
(product/platform, strategy/risk, GTM/SEO, plus a keel architecture profile). `/faq` is auth-walled
and was not read. Homepage "live" counters render as animated odometers and were captured
mid-transition (several scrape as negative); the figures quoted anywhere from them should be treated
as approximate marketing numbers, not audited metrics. All claims about keel's own behaviour in §1
were verified against source and are cited by file and line.
