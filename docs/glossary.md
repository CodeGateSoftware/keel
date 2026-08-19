# The keel glossary

The single source for the console's vocabulary. Every term a keel screen can show is
defined HERE and nowhere else: the TUI's Help menu renders this file directly (bounded
read, cached by mtime -- the same reader discipline the Research corpus keeps), and the
docs link to it rather than restating a definition that could drift.

Two honesty rules this file inherits from `docs/fiqh-basis.md`:

- **Fiqh terms are anchored, never authored.** A fiqh term's definition is a verbatim
  passage of `docs/fiqh-basis.md`, and its `Source:` line names the document and the exact
  section it was quoted from. Where fiqh-basis does not state a term (gharar), the
  definition says so, rather than papering the gap with a paraphrase that sounds like a
  ruling -- the knowledge-base sources fiqh-basis indexes are the place to read it.
- **Rule parameters are not defined here.** What `turtle_breakout`'s `entry_lookback`
  means lives in the rule class that defines it, rendered by introspection through
  `keel.commands.rules.describe_params` -- the help system LINKS to that source; a second
  table in this file would drift the day a class changed. The entries below define only
  the vocabulary shared across screens.

Each entry is a `## term` heading, a definition, and a `Source:` line.

## rail

One of keel's numbered hard guards that every order passes through -- spend caps, drawdown
breakers, the allowlist, settlement-currency and spot-shape checks. Eighteen exist (1-14,
16, 17, 18, 19 -- there is no rail 15); each is un-overridable and audit-logged.

Source: keel's own vocabulary -- docs/fiqh-basis.md's rails table (the prudential rails 2-14, 16) plus its prose sections for rails 1, 17, 18 and 19

## attestation

market facts are computed, Shariah classifications are **ATTESTED, never inferred**.
Whether a token's core purpose is a haram sector (§28.4), whether it is asset-backed
`'ayn` or a claim `dayn` (§65.5/§67.2), and whether it pays a riba-like yield are
questions of fact-plus-scholarship about the world. No module in this repository derives
them from candles, and none pretends to. A human records them, with a source and a name,
via `keel assets attest`.

Source: docs/fiqh-basis.md -- "## What is attested versus what is computed"

## instrument attestation

Only `spot` is admitted; CFD, future, perpetual, option, and leveraged-token listings are
refused, recorded via `keel assets attest-instrument`. Unattested fails closed.

Source: docs/fiqh-basis.md -- "### The curation screen (`keel/compliance/screen.py`)"

## exemption

a documented exception (`keel assets exempt`) may waive only ONE criterion today:
`history`

Source: docs/fiqh-basis.md -- "### The curation screen (`keel/compliance/screen.py`)"

## screening

An unattested asset is not "probably fine" — it is unknown, and the screen fails closed
on unknown.

Source: docs/fiqh-basis.md -- "## What is attested versus what is computed"

## promotion gate

The thresholds a candidate rule must clear on out-of-sample evidence before `rules
promote` moves it toward live: the four performance floors -- min_trades (a minimum
number of trades), min_expectancy, min_rr (a minimum realised reward:risk ratio) and
min_win_rate -- AND the overfitting gate (G4): a probability of backtest overfitting
(PBO) above its bound TOGETHER WITH a steeply negative degradation slope, a
conjunction, not a bare PBO bound. Pooling is per parameter SET and covers only the
sample-size axis -- the same parameter set's paper evidence may count toward min_trades
across products -- and the G4/overfitting gate is NOT pooled. The DCA benchmark is not
a floor of this gate; a simulate report is where a rule is measured against it.
`keel insights` renders how far a rule sits from the gate.

Source: keel's own vocabulary -- keel/strategy/promotion.py and keel/research/cscv.py

## paper mode

Simulated execution against real prices: paper buys spend a paper cash balance, no order
ever reaches a venue, and every fill is synthetic. keel's paper deployments are separate
config+db pairs from live -- no figure on one describes the other.

Source: keel's own vocabulary -- the wrappers and docs/operator-runbook.md

## live mode

Real orders at a real venue. Live selection is guarded: choosing the live deployment in
the console asks an explicit y/N, and the engine's confirm mode gates every order behind
an interactive confirmation.

Source: keel's own vocabulary -- the wrappers and docs/operator-runbook.md

## kill switch

A stored halt that stops all trading immediately (`keel kill` -- one command, always
allowed, logged). Releasing it (`keel resume`) is deliberately harder than engaging it.

Source: keel's own vocabulary -- keel/commands/trading.py

## autonomy

The armed state in which the agent places orders unattended. Arming demands a typed yes
at the terminal; disarming only ever reduces capability and stays ungated.

Source: keel's own vocabulary -- keel/commands/autonomy.py

## qabd

possession is the ability to dispose, not physical custody

Source: docs/fiqh-basis.md -- "### Rail 17 — withdrawal capability, `qabd` §65.4"

## riba

Coinbase pays USDC rewards on idle balances, that interest is riba, and it accrues with
no order placed

Source: docs/fiqh-basis.md -- "### Purification (§65.9) and idle-balance rewards (§56.3)"

## gharar

not stated in docs/fiqh-basis.md -- the knowledge-base sources it indexes
(docs/superpowers/references/trading-knowledge-base/) are the place to read it

Source: docs/fiqh-basis.md -- not stated there; see "## How to read the citations"

## maysir

The commoner English transliteration of *maisir* -- see the maisir entry.

Source: keel's own vocabulary -- a spelling pointer, not a definition

## maisir

what makes speculation *maisir* is non-ownership, non-delivery, difference-settlement

Source: docs/fiqh-basis.md -- "### Rails 18/19 — settlement currency and spot-instrument shape"

## purification

interest/reward credits are segregated from realised P&L and the equity base, reported as
owed to charity, never recognised as profit

Source: docs/fiqh-basis.md -- "### Purification (§65.9) and idle-balance rewards (§56.3)"

## session-bound venue

A market with opening hours (equities): it closes for nights, weekends and holidays, and
its adapter declares `session_bound` so the engine consults the venue's own clock before
trading. Crypto venues are the 24/7 contrast -- always open, no clock to consult.

Source: keel's own vocabulary -- the adapters' `session_bound` capability declaration

## market clock

The venue's OWN clock read (`/v2/clock` on Alpaca; a constant OPEN on 24/7 venues) --
never a locally maintained calendar, which would drift from the venue on holidays and
half-days.

Source: keel's own vocabulary -- the broker port's `market_clock()`

## trust window

The interval a recorded market-clock state vouches for. Outside it the record is stale
and every surface renders CLOCK UNAVAILABLE, fail-loud -- exactly how `fetch --check`
treats a record that no longer vouches for anything.

Source: keel's own vocabulary -- keel.agent's recorded session state

## DCA benchmark

The honest alternative every simulated strategy is measured against: dollar-cost-average
buying of the same products over the same window. A strategy that cannot beat it has no
reason to exist.

Source: keel's own vocabulary -- keel/commands/simulate.py

## granularity

The candle bar size a rule runs on (one hour, one day, ...). It is a rule PARAMETER:
each kind's choices, default and meaning render from the rule class itself through
`keel commands.rules.describe_params` -- this glossary does not restate them.

Source: keel's own vocabulary -- the rule classes via describe_params

## trials ledger

The hash-chained record of every backtest trial, with its M/N decision accountings;
`keel trials verify` walks the chain and reports any break.

Source: keel's own vocabulary -- keel/research/ledger.py
