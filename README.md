# keel

**English** | [العربية](README.ar.md)

**Website:** [keeltrading.com](https://keeltrading.com) — the plain-English (and العربية, Français) tour: what keel is, how to start on paper, and the honest results.

**keel is an auditable Shariah-compliance engine for spot crypto trading** — deterministic
safety rails, attested asset screening that fails closed, and §65.4 *qabd* (constructive
possession) encoded as an executable check — with a reference auto-trading agent built on top
of it for Coinbase. Plenty of people have a trading bot; almost nobody has this compliance
machinery, which is the part worth reading.

**The honest result, stated by us first:** no shipped rule family is net-positive at the
taker fee actually paid on this venue — cost is the binding constraint, and the viable
parameter/fee intersection is empty under production-faithful execution
([the experiment record](docs/experiments/2026-08-13-restated-under-a-production-faithful-engine.md)).
The point of this project is the enforcement machinery and the honest measurement of what
runs through it, not a claim of alpha. A visitor who finds that out themselves feels misled;
one who is told upfront can read it as rigour.

**The cadence problem, and the pipeline built to solve it:** the promotion gate's 100-trade
floor is honest only if the sample is collectable — and at the daily clock's measured
2.15 signals per asset-year, it is 31–84 years away per asset. Waiting is not a slower
path; it is no path. The same rules evaluated on `ONE_HOUR` bars fire **49.4 signals per
asset-year** (median n=268 per rule-product over the 5-year cached window;
[the hourly experiment](docs/experiments/2026-08-11-hourly-backtest-turtle-breakout.md)),
so keel runs a third deployment profile — **paper-hourly**: its own database and config,
one cycle per UTC hour, 19 allowlisted assets admitted through a measured 15-minute
data-health screen — accruing **~940 entry signals per year pooled (~2.6/day)**, which
puts the n=100 pooled forward-evidence review weeks away instead of decades (milestone
[#353](https://github.com/CodeGateSoftware/keel/issues/353), scheduled 2026-09-30). The
hourly configuration is **also measured net-negative** — that profile exists to collect
admissible forward evidence (rail vetoes, outcomes, pending lifespans, intent divergence:
the things a backtest cannot observe), not profit. The full story is in
[the announcement](https://github.com/CodeGateSoftware/keel/discussions/359) and the
[operator runbook](docs/operator-runbook.md).

> **keel is not a fatwa engine. It is an enforcement engine for a ruling you supply.** keel
> never derives a Shariah classification from market data. You record one — with a source and an
> attributed name (`keel assets attest`) — and keel enforces it deterministically, rejecting
> anything unattested. The ruling lives in your attestation, not in the code, so two operators
> following different schools get different answers from the same code, by design. See
> `CONTRIBUTING.md` for what that means for pull requests.

**No scholarly review of keel's fiqh basis has occurred.** The basis is one operator's
sourced reading — published as [`docs/fiqh-basis.md`](docs/fiqh-basis.md) precisely so it
can be audited and challenged — and each operator is responsible for their own
attestations. What a scholarly review would cover, and what it would and would not
signify, is defined in [that document's review-status
section](docs/fiqh-basis.md#scholarly-review-status); unless someone walks that path, the
status is: not reviewed.

keel is a personal tool, not financial advice and not religious (Shariah) advice — see the
disclaimers below.

## Try it in five minutes

No terminal? The macOS/Windows app is on the [releases page](https://github.com/CodeGateSoftware/keel/releases)
(with [`docs/desktop-install.md`](docs/desktop-install.md) for the first launch). Terminal
person, no cloning? The installer does this from the published wheels instead:
`curl -fsSL https://raw.githubusercontent.com/CodeGateSoftware/keel/main/scripts/install.sh | bash`.

Everything here is read-only and paper-side: no funds, and nothing in this path can place an
order. Verified on a clean clone. You need [uv](https://docs.astral.sh/uv/) and a **free,
read-only Coinbase Developer Platform (CDP) API key** — candle history is fetched through the
authenticated client, so `keel fetch` without a key fails with an `AuthenticationError`; say
so upfront rather than let step 4 be a surprise.

```bash
git clone https://github.com/CodeGateSoftware/keel.git && cd keel
uv sync --all-extras --dev        # any Python 3.11+ (the repo develops on 3.14)
cp .env.example .env              # put the CDP key/secret in it — market data only
uv run keel rules seed            # register the rule families as candidates
uv run keel fetch                 # pull candle history for the default allowlist
uv run keel simulate --years 1 --skip-within-cap
```

`keel simulate` replays the real rules deterministically over the fetched history, compares
against a DCA benchmark, and writes a GO-LIVE/TRAIN-MORE report with the gates and their
numbers. On the default rules it will very likely tell you **TRAIN MORE** and name the gates
that fail — that is the engine working, not broken; the honesty is the feature. The next
steps from there — promoting a rule through the gate (`keel rules promote`, which refuses to
promote without an overfitting check), running the paper agent (`keel agent`, paper mode is
the default), and eventually a supervised first live order — are in
[`docs/go-live-runbook.md`](docs/go-live-runbook.md).

## How keel works

keel runs as a scheduled **agent loop** (`keel agent`). Each cycle, for every allowlisted
product, it: polls fresh candles, asks each active **rule** to `detect()` a setup, sends any
signal through the **rails**, previews the order with the broker (the broker's own numbers),
applies the confirm/autonomy gate, then places and logs. There is deliberately no manual
"place an order" command — every order is the output of a rule that cleared the rails.

- **Rules** (`keel/agent.py::RULE_REGISTRY`) — four families: `dca` (scheduled accumulation,
  no stop) and three risk-defined entry patterns (`turtle_breakout`,
  `pullback_continuation`, `rsi_meanrev`). A rule must walk `candidate → paper → live`, and
  promotion clears a two-part gate: performance floors *and* an overfitting check
  (PBO/CSCV). A rule that clears four floors on one in-sample parameter set is exactly what
  the second gate exists to be suspicious of. The sample-size floor keeps its 100-trade bar
  but may be met two ways: the rule's own backtest, or — when its own sample is short — the
  same parameters pooled across other products in paper, provided at least five products
  each contribute ten trades (a diversity floor: crypto assets correlate, and a pool of
  correlated samples overstates its power).
- **The rails** (`keel/execution/guards.py`) — eighteen deterministic checks no order can
  skip and nothing can override, not even autonomy: the halal allowlist, per-order and
  per-day spend caps, exposure and concentration caps, correlation-aware sizing, a
  minimum-move floor, no-martingale/no-stop-widening, a fails-closed kill-switch, total and
  weekly drawdown breakers, a consecutive-loss/edge-decay breaker, feed-staleness and
  quote-balance checks, venue **subscription/withdrawal attestations** — rail 17 encodes
  §65.4 *qabd*: an asset that cannot be withdrawn may not have been validly possessed, so
  withdrawal capability is attested and enforced, not assumed. A rail veto names itself and
  the command that clears it. Beside the rails sits one routing-time check that needs the
  venue's own book, which a broker-less rail cannot see: the **max-spread entry gate** (#350)
  refuses a live BUY whose previewed `(best_ask − best_bid) / mid` is at or beyond
  `execution.max_entry_spread_pct` (default 50bp) — BUY-only, live-only, and fail-closed on
  an unreadable book.
- **Screening** (`keel/compliance/screen.py`) — allowlist admission is split by what is
  knowable: market facts are computed; Shariah classifications are **attested, never
  inferred**, and an absent attestation is a rejection, not a default pass.
- **Confirm vs. autonomy** — by default keel previews each order and asks at the terminal;
  headless, it declines. `keel autonomy on` changes **who is asked, never what is allowed**.
  To stop trading, `keel kill` — the kill-switch fails closed.

keel ships **inert**: nothing trades until you promote a rule, attest the venue subscription
(rail 14 refuses live BUYs otherwise), fund the account, and — in confirm mode — type `y`.
Long-only spot only: no leverage, no shorting, no derivatives, and sizing uses actual cash,
so no riba. Account-level obligations no rail can see (disabling USDC rewards on idle
balances, chiefly) are the operator's to verify —
[`docs/operator-runbook.md`](docs/operator-runbook.md) lists them.

One mechanic worth knowing before any number surprises you: **a tighter stop produces a
LARGER position**, because `size = risk ÷ stop-distance`. `risk_pct` bounds what you lose if
the stop holds, not what you spend.

## Architecture

```
keel/                          the agent and CLI
├── agent.py                   the loop and RULE_REGISTRY (where rules live)
├── execution/guards.py        the 18 rails (where enforcement lives)
├── execution/sizing.py        position sizing
├── compliance/screen.py       attested allowlist admission (fails closed)
└── commands/                  CLI command implementations

packages/
├── keel-core/                 shared domain types, config, logging
├── keel-broker-api/           the broker PORT: the contract every adapter codes against
├── keel-broker-coinbase/      Coinbase Advanced Trade adapter
├── keel-broker-robinhood/     Robinhood adapter (optional venue)
└── keel-broker-fake/          deliberately divergent fake venue, dev-only

tests/                         ~3,000 tests, including the port's conformance suite
```

A new broker plugs in as a package under `packages/keel-broker-*`, implementing the
`keel-broker-api` port and registering itself under the `keel.brokers` entry point — no
changes to `keel/` itself. The fake venue exists precisely to keep that port honest: a second
adapter, deliberately divergent, that the conformance suite runs against.

## Documentation map

- [`docs/fiqh-basis.md`](docs/fiqh-basis.md) — the Shariah reasoning keel encodes, ruling by
  ruling, each with its in-repo source: what is attested vs computed, the open questions,
  and how to disagree.
- [`docs/operator-runbook.md`](docs/operator-runbook.md) — operating a deployment: the
  account-level compliance obligations no rail can enforce, deploying/upgrading releases,
  and the three deployment profiles — daily paper, live, and the hourly evidence profile —
  and their distinctions (accounts that share nothing).
- [`docs/go-live-runbook.md`](docs/go-live-runbook.md) — the first supervised live order.
- [`docs/experiments/`](docs/experiments) — the experiment record, including the honest
  result linked above; every document states what was measured, on what engine, with the
  defect that forced a restatement.
- [`docs/launch.md`](docs/launch.md) — the pre-launch gate and the announcement plan:
  what must be true before anything is announced, where, in what order, and what the
  post says (the honest result included).
- [`docs/desktop-install.md`](docs/desktop-install.md) — installing the macOS/Windows app,
  and why your computer warns you about it: the builds are not code-signed, because Apple's
  certificate costs $99/yr and keel cannot currently afford it. Says what to do, and how to
  verify what you downloaded instead.
- [`docs/RELEASING.md`](docs/RELEASING.md) — how a release is cut.

## Asking questions, and contributing

- **Questions, ideas, and classification discussion** →
  [Discussions](https://github.com/CodeGateSoftware/keel/discussions), including the
  *Compliance & classification* category for "should X be treated this way" — which is a
  question, not a bug, and must not be triaged as one.
- **Contributing** → [`CONTRIBUTING.md`](CONTRIBUTING.md) — the documentation standard (this
  repo's bar is unusually high, and stated, with a worked example), the gates a PR must
  pass, tests-first, and scope guidance. Newcomers: look for issues labelled
  `good first issue`.
- **Behaviour** → [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).
- **Anything that could make keel misbehave** → [`SECURITY.md`](SECURITY.md), privately —
  a rail that can be bypassed is a security issue, not merely a bug.

## Disclaimers

keel is a personal tool, not financial advice and not religious (Shariah) advice. Consult a
qualified financial advisor and a knowledgeable scholar before trading. You are solely
responsible for your own trading decisions. Licensed under
[Apache-2.0](LICENSE).

**Trademarks:** Alpaca, Coinbase, and Robinhood are trademarks of their respective owners.
keel has no affiliation with any of them, no endorsement from any of them, and no
sponsorship from any of them. Every `keel-broker-*`
package is an independent, original open-source implementation of keel's broker port against
that venue's publicly documented API — a client of the venue, not a product of it. Venue
names appear here solely to identify what the code talks to.
