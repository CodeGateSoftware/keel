# keel vs Jesse — competitive plan

2026-10-01 · Elmehdi Aitbrahim

keel matches or beats Jesse on research rigour and safety, but trails it badly on breadth. Of the 22 rows below, keel does 9, partly does 6 and lacks 5. It refuses futures by design, and Jesse's IP limit doesn't apply to software that runs on the user's own device. The biggest gaps are exchanges (1 live vs Jesse's 16), user-written strategies, and notifications.

The plan: don't try to be a broader Jesse. Compete as the only auditable, Shariah-compliant spot engine. Close the four gaps a Muslim spot trader actually feels, refuse the futures half of Jesse's table out loud, and charge for services and a plugin marketplace around a free engine, never for the engine itself.

## Feature by feature

Jesse's rows and tiers come from its pricing page as of 2026-10-01 (screenshot). keel's status comes from the code on main at v0.21.0.

| Jesse feature | Jesse tier | keel today | What keel has |
| --- | --- | --- | --- |
| Backtesting | All tiers | Yes | `keel rules backtest` per rule; `keel simulate` replays a multi-product account under the live rails, real fees and slippage |
| Strategy optimization | Free: limited CPU | Partial | Optuna search in `keel research tuning`, single process; it proposes candidates, never tunes a live profile |
| Monte Carlo simulations | Free: limited CPU | Yes | `keel trials monte-carlo`: trade reshuffle and block bootstrap |
| Research module | All tiers | Yes | 14 research modules (PBO, deflated Sharpe, walk-forward, lookahead check) via CLI; no notebook API |
| Backtesting trading routes | Unlimited | Partial | Several products per run; no strategy × symbol × timeframe routes |
| Futures | All tiers | Refused by design | Rails 18 and 19 block derivatives; a perpetuals portfolio is refused |
| Spot | All tiers | Yes | The only instrument keel trades |
| DEX support | Paid | No | Centralized venues only |
| Backtest benchmarks | Paid | Yes | DCA into BTC and into the allowlist, same fees |
| Machine learning | All tiers | No | None |
| Rule significance testing | Free: research only | Yes | Hash-chained trials ledger (114 trials), significance at the fee actually paid, promotion gauntlet |
| Agentic workflow (MCP) | Free 100/day, paid unlimited | Yes | `keel mcp`: 8 read-only tools, no limit; cannot place or halt anything |
| Monthly GPT credits | 100–500 | No | `proposer.py` screens an LLM-made shortlist; no hosted LLM |
| Real-time support (Discord) | Community, premium paid | No | No chat channel |
| Help center and docs | All tiers | Yes | 215 docs, keeltrading.com in EN/AR/FR |
| Notifications | All tiers | Partial | One opt-in webhook (Slack/JSON; Discord via its Slack endpoint), macOS alerts; no Telegram or email |
| Exchange support | Free: testnets; paid: 16 venues | Partial | Coinbase live; Alpaca equities (paper in practice); Robinhood dev-only; Kraken a stub |
| Paper trading | Paid | Yes | Free; its own profile and database |
| IP limit | 1–5 | Not applicable | Runs on the user's device, no license server |
| Premium strategies | Paid | No | 9 built-in rule kinds; none is net-positive at real fees |
| Live trading tabs and routes | 1–20 tabs, 1–50 routes | Partial | One agent runs many rules × products; each profile is its own process and console |
| Timeframes | Free: 1h and 1D; paid: 1m–1D | Partial | 1m to 1D defined; daily and hourly used in practice |

Of Jesse's 16 venues, 9 are perpetual futures. The 7 spot ones are the only venues keel could ever add: Coinbase, Kraken Pro, Binance, Binance.US, Bybit, Gate.io, KuCoin.

## Where keel already wins

Jesse sells capacity: more routes, venues and CPU. keel's edge is what no Jesse tier sells at any price.

- **Shariah compliance you can audit.** An attested asset screen that fails closed, *qabd* (possession) enforced as rail 17, purification reports, and a published fiqh basis that invites challenge.
- **21 safety rails no order can bypass.** Per-order, per-day and exposure caps, drawdown and loss-streak breakers, no averaging into losers, no widening a stop, kill switch.
- **Honest research.** Every test goes into a hash-chained trials ledger. Results are deflated for the number of tries and measured at the fee actually paid. Jesse sells optimization; keel shows when optimization is fooling you.
- **Disciplined accumulation.** DCA plans with a buy cap, and a sell side that proposes trims and exits in preview before it ever places anything.
- **Privacy by construction.** Local, offline-first, no telemetry, no license server, Apache-2.0 for good.
- **An assistant that can't trade.** The MCP server is read-only by design, not by a usage cap.

## Gaps: close or refuse

Close the four gaps a Muslim spot trader hits in week one. Refuse the rows that would break keel's rules; saying no is part of the product.

| Gap | Decision | Why | Size |
| --- | --- | --- | --- |
| More spot venues: finish Kraken (a stub today) and Robinhood (needs credential wiring), then one of Binance, Bybit or KuCoin spot | Close first | One live venue is the first thing a buyer compares; each needs its own screen of the venue's terms | L, about M per venue |
| User-written strategies: a plugin entry point for rule kinds (today a fixed registry in `agent.py`) | Close first | Jesse's core promise, and the marketplace can't exist without it | M |
| Notifications: Telegram, email, native Discord | Close first | Cheap, and expected on every tier | S |
| Community and support channel (Discord or similar), plus a help center over the docs | Close first | Jesse sells support; keel has no channel at all | S |
| 4-hour and weekly timeframes | Close later | Fits keel's daily-first cadence; minute bars don't | S |
| Parallel research runs and a notebook-friendly research API | Close later | Speeds honest research; must still log every trial | M |
| Multi-route live view: several profiles in one console | Close later | Matches Jesse's live tabs without merging processes | M |
| Futures, perpetuals, leverage, shorting | Refuse | Riba and gharar; rails 18 and 19 forbid them. Half of Jesse's venue list is here | — |
| DEX perpetuals (Hyperliquid, Apex, Lighter) | Refuse | Same reason. DEX *spot* is an open question below | — |
| "Premium strategies" sold for their returns | Refuse | keel's own record shows none net-positive at real fees; marketplace strategies must publish their trials instead | — |
| Hosted GPT credits and ML models | Refuse | keel stays local; analysis comes through marketplace plugins such as IKA | — |

## Offer and pricing

Jesse gates the engine itself: paper trading, live venues and timeframes sit behind $899–$1,599 lifetime plans. keel can't copy that: Apache-2.0 makes the engine free for good, and the website already promises that paid offers will be services around it. So charge for what saves the user work or adds a lens, never for a rail or a venue.

| Tier | Price (to validate) | What's in it |
| --- | --- | --- |
| Community | Free, forever | The whole engine: every rail, the compliance screen, all venue connectors, backtests, research toolkit, web console, MCP, paper and live trading |
| keel Pro | Starting hypothesis: about $299 lifetime or $99 a year, well under Jesse's $899 | A maintained attestation feed (asset screens kept current against named sources), purification and zakat reports, hosted Telegram and email alerts, install help, priority support |
| Marketplace | Each plugin priced by its author; keel keeps a share (e.g. 30%) | Analysis feeds such as IKA, strategy packs that publish their trials ledger, extra connectors. Every plugin passes a compliance review before listing |

Jesse's prices are from its pricing page as of 2026-10-01. keel's numbers are placeholders until the open questions below are answered.

## Roadmap

Free engine first; paid offers only after a stable, reviewed 1.0.

| Phase | What ships | Done when |
| --- | --- | --- |
| 1. Stable 1.0 (under way) | Sell side decided · rule plugin API · Telegram and email · community channel | 90 days live, no incident, doctor clean |
| 2. Breadth | Kraken spot live · Robinhood wired · third spot venue · 4h and weekly bars · multi-profile view | Each venue passes conformance tests |
| 3. keel Pro | Attestation feed · purification and zakat reports · hosted alerts · priority support | Scholar review recorded, price validated |
| 4. Marketplace | IKA listed first · strategy packs with trial ledgers · revenue share · plugin sandbox | Legal advice in, plugin review live |

Each phase starts only when the one before passes its gate. No dates yet: phase 1 is under way, and its 90-day live record sets the earliest start for the rest.

## Risks and open questions

- **No rule makes money at real fees.** keel's own published result. Selling it as a profit tool would contradict it; sell compliance, discipline and accumulation instead.
- **One developer against a funded team.** Jesse has been shipping since 2019. Breadth is unwinnable head-on, which is why the plan stays narrow.
- **Venue risk.** Each new exchange needs its own check: spot-only, withdrawable, legal for the user's country.
- **Regulation.** Selling trading software, and a share of strategy plugins, may count as investment advice in some markets. Needs legal advice before the marketplace opens.
- **Plugin trust.** A third-party plugin runs next to real money. It needs a sandbox and a review, or one bad plugin sinks the brand.

Open questions:

- [ ] Who is the first paying user: a Muslim retail DCA investor, an active trader, or an Islamic fintech?
- [ ] Lifetime price or annual? Jesse sells lifetime; Pro's maintained feed is a recurring cost.
- [ ] Is DEX *spot* worth exploring? Self-custody may meet *qabd* more fully than an exchange account.
- [ ] Does the scholarly review (Dr. Lahlou or others) come before Pro launches?
- [ ] Which third spot venue: Binance, Bybit or KuCoin?

## Sources

- [Jesse pricing page](https://jesse.trade/pricing): plans and comparison table, from a screenshot taken 2026-10-01. The page blocks automated reads, so the figures weren't re-checked.
- keel on main at v0.21.0: rails in `keel/execution/guards.py`, brokers in `packages/`, research in `keel/research/`, MCP in `keel/mcp/`.
