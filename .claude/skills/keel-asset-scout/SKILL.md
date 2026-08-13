---
name: keel-asset-scout
description: Use when scouting new tradeable assets or trading-strategy ideas for keel — sweeping Coinbase USD spot listings for candidates, re-examining assets scouted in a prior run, or researching new rule parameters/techniques. Triggers on "scout assets", "find candidates", "new strategies for keel", "what should keel add", "keel asset scout".
---

# Keel Asset Scout

## The proposer-never-decider principle

**This skill produces INPUT to keel's existing, unmodified deterministic gate. It never admits, attests, trades, or writes to keel's databases.** Per the governing design
(`docs/superpowers/specs/2026-07-24-llm-asset-proposer-design.md` §5/§6.4): an LLM may reduce
risk directly, but may only *increase* activity — a new asset, a new rule, a buy — by passing
through a deterministic gate a human built and reviews. **You are a proposer. You are never a
decider.** Every output of this skill is a file for a human to read, run `keel assets propose` /
`keel assets attest` / `keel rules backtest` against, and decide on. If you catch yourself about
to run `keel assets attest`, edit an allowlist, or write to a `keel-*.db`, stop — that is the
human's call, not yours.

## When to use

- Operator asks to find new candidate assets, "what's out there we're not trading."
- Operator asks to re-check assets scouted before (has anything changed — liquidity, yield
  mechanism, allowlist).
- Operator asks for new strategy/parameter ideas, or "any new research worth backtesting."

Do NOT use this to actually add an asset or rule — that's `keel assets attest` / `keel rules
backtest` / `keel rules promote`, run by the human, never by this skill.

## Environment

- **Deployment** (read allowlist from, write outputs to): `~/keel/`. This is the live trading
  deployment — see the `keel-deployment-layout` memory. **Never write anywhere in `~/keel/`
  except `~/keel/proposals/`. Never touch `~/keel/*.db` or `~/keel/config*.yaml`.**
- **Dev repo** (read-only, for the exact schema/gate source of truth):
  `~/Development/work/CodeGate/keel/`. Never edit files here for this skill — if the schema
  ever looks like it's drifted from this document, re-read `keel/proposer.py` and
  `keel/compliance/screen.py`, don't guess.

### Where this file lives

**This is a PROJECT-scoped skill, and this file is the only copy.** It lives at
`.claude/skills/keel-asset-scout/SKILL.md` in the dev repo and loads only for sessions working in
that repo — which is correct, since every path, gate and experiment it references is keel's. There
is deliberately no copy under `~/.claude/skills/`; a second copy is a second source of truth, and
the two drift silently.

`.claude/` is gitignored (`.gitignore:34`), so this file is tracked by explicit `git add -f` — the
same exception the two `review-fix-merge-pr` skills already use. **Being gitignored does not mean
untracked here; check before assuming an edit is uncommitted.**

Version control matters more for this file than for a typical skill, because most of it is not
procedure but *recorded reasoning* about measurements that cost real compute to produce — why
stationarity is never a filter, why a small-sample profit factor is a lottery ticket, why the
engine's own invariants are unobservable from a proposer. An untracked file loses the argument
along with the rule, and the next person re-derives both the hard way.

## Why the constraints exist

- **No embedded LLM/API key inside keel.** The design is an "outside-first hybrid": keel stays
  dependency-free, secret-free, and fully unit-testable. All non-deterministic scouting (this
  skill) happens outside keel; keel owns only ingest → screen → report
  (`keel assets propose`, backed by `keel/proposer.py`).
- **Mandatory citations, enforced only at the schema level.** `proposer.py`'s `_is_http_url`
  checks that a source is a well-formed `http(s)` URL — it does **not** and cannot check that
  you actually read it. A fabricated-but-well-formed URL passes keel's validation silently. That
  gap is why *this skill*, not keel's code, is the only thing standing between a plausible-looking
  lie and a human's attestation decision. Treat citation honesty as load-bearing, not a formality.
- **Closed `RULE_REGISTRY`.** `keel/agent.py` maps exactly four rule kinds to Python classes and
  raises `ValueError` on anything else. A new rule kind is a human-reviewed change to the
  deterministic trading core, not something this skill can propose into existence — this is what
  keeps the §5 asymmetry intact for strategies, the same way `attestation=None` fails closed for
  assets.
- **`attestation=None` fails closed.** Sector/backing/yield cannot be derived from candles.
  `shariah_hypothesis` is explicitly UNVERIFIED and is never passed to `screen_asset` — it exists
  so the human attesting has a head start, not so the gate can be skipped.
- **Taker math controls the field.** Round-trip friction here is ~2.5% of notional, the same order
  as the per-trade edge of everything ever measured, and it is levied on the *search* rather than
  on the *edge* — an account pays ~241 tolls to be present for the handful of trades that pay. As
  of the 2026-08-13 re-measurement the intersection of `n>=100`, gross PF > 1.0 and net PF > 1.0
  is **vacant across the entire 24-asset universe**, at the maker tier as well as the taker one.
  A candidate is not interesting because it is liquid and admissible; those are entry conditions,
  not evidence. See "What has already been measured" below before writing anything.

## Tiering — route every step to the cheapest model that can do it

| Step | Model | Job |
|---|---|---|
| Sweep Coinbase USD spot listings, web search, read sources, write raw findings to scratch files | **Haiku** | Breadth. Cheap, parallel, no judgment calls. |
| Shariah plausibility (sector/backing/yield), liquidity plausibility, drift vs. prior runs | **Opus** | Judgment. One agent, reads all Haiku output + prior run history. |
| Emit the final JSON in keel's exact schema | **Sonnet** | Composition. Validates every field against the rules below before writing. |

Dispatch with the `Agent` tool, `model` param set per tier, in parallel where independent (see
`superpowers:dispatching-parallel-agents`). Give each Haiku agent a narrow, self-contained brief
(one venue sweep, or one small cluster of candidate assets) — Haiku doesn't need context, it needs
a fetch-and-report task.

## Output 1 — asset candidates

**File:** `~/keel/proposals/YYYY-MM-DD-shortlist.json` (today's date; if the file already exists
for today, treat this as a rerun and overwrite it — the run log is what accumulates).

**Schema — exactly what `keel/proposer.py::parse_proposal` accepts:**
```json
{
  "candidates": [
    {
      "asset": "SOL",
      "rationale": "High developer activity and liquidity; ...",
      "sources": ["https://www.coinbase.com/price/solana", "https://coinmarketcap.com/currencies/solana/"],
      "shariah_hypothesis": "utility L1, no interest-bearing mechanism"
    }
  ]
}
```

Rules `proposer.py` enforces (violate these and the entry is dropped into `invalid`, never
screened — match them before writing):
- `asset`: non-empty, **alphanumeric only** — `SOL`, never `SOL-USD` or `wSOL`. (`_candidate_error`
  checks `.isalnum()`; a dash builds a malformed product id downstream.)
- `rationale`: non-empty string.
- `sources`: **at least one** string that parses as `http(s)://...netloc...`. **Every URL here
  must be one you (or a Haiku sub-agent) actually fetched this run** — quote the exact URL your
  fetch tool returned, never one recalled from training or guessed by pattern (e.g.
  `coinmarketcap.com/currencies/<slug>/` is a plausible-looking guess, not a citation, unless you
  fetched it and it resolved).
- `shariah_hypothesis`: optional, always UNVERIFIED, never a substitute for attestation.

### Screen BEFORE composing — not after

**Run `keel assets discover --probe-liquidity --probe-history` and drop everything marked `LOW` or
`NO` before a single candidate reaches the judgment step.** Composing first and screening later is
how the 2026-08-08 fifth run shortlisted BICO on a reported "$12.81M/24h" that the gate then
rejected at a median daily volume of 108,004 — 9× under the floor — and nearly dropped DOGE for
sitting 1.7% under a *discovery* floor while being ~30× over the *admission* one.

`--min-volume-24h` is a **one-day venue snapshot**. The admission criterion is the **median of
`volume × close` over cached history** (`screen.py::median_daily_quote_volume`). Same units, wildly
different statistics — on a live sweep the snapshot ran 227× above the median for BICO and ~4×
below it for DOGE. **The 24h number is a cheap way to bound the request count and nothing more;
never treat it as a proxy for liquidity.** On that same sweep, three of the top fourteen by 24h
volume (BICO, IMU, GWEI) were sub-floor on the real criterion.

`--probe-liquidity` computes the gate's own statistic, one extra request per candidate. Two honest
limits, both of which belong in the run log if they bite:

- It samples the **last 180 days**; the screen medians over **all** cached history. On that sweep
  ZEC probed at 33.1M against a full-history median of 1.23M — a 27× spread, because ZEC surged
  recently. So `ok` means "worth pulling candles for", never "will be admitted".
- The recent window makes a false `LOW` unlikely for a currently-active asset, but one that was
  liquid years ago and is quiet now can probe `LOW` and still clear the gate. If you drop
  something on `LOW` alone, name it in the run log so it can be revisited.

Anything that survives and has cached candles should then go through `keel assets screen`, which is
fully offline. **Attestation should be the only rejection a shortlist entry still carries by the
time the operator reads it.**

### Pre-filter before anything reaches Opus/Sonnet

- **Spot only.** Rails 18/19 (`keel/compliance/screen.py`'s `spot_instrument` criterion, via
  `parse_spot_product_id`) admit **USD-settled SPOT only**: `BASE-USD`, uppercase, exactly one
  hyphen. Never propose a perp, a dated future (`BTC-28AUG26-CDE`), an index/basket, or an
  FX/commodity wrapper — these fail mechanically and waste the human's attestation time.
- **Already allowlisted.** Read `allowlist:` from `~/keel/config.live-sandbox.yaml` **at run
  time** — do not hardcode it (it changes; at last read it was `BTC, ETH, PAXG, ADA, XLM`, but
  trust the file, not this sentence).
- **No source, or source you didn't fetch** → drop before composition, don't emit a candidate
  with a placeholder citation.

## Output 2 — re-examine prior runs (keel has no rejection ledger)

keel's `asset_attestations` and `screen_exceptions` tables don't persist scout-time outcomes —
there's no "why did we reject SOL last time" anywhere in keel's DB, and there deliberately never
will be (§ Non-goals: "No proposal-audit DB table in v1... YAGNI until an audit trail is actually
needed"). So **this skill keeps its own history**, outside keel, in `~/keel/proposals/`:

- The dated shortlist files themselves (`YYYY-MM-DD-shortlist.json`) are the record.
- **`~/keel/proposals/run-log.md`** — append (never rewrite) a dated section each run:
  - **New**: assets in today's shortlist not in the previous one.
  - **Dropped**: assets in the previous shortlist not in today's — and why, if known (thin
    liquidity that never deepened, sector concern, no longer worth the operator's attestation
    time).
  - **Changed**: assets present in both runs whose `rationale`/`shariah_hypothesis`/sourced
    liquidity picture materially shifted (e.g. "thin-liquidity asset that has since deepened," "a
    yield mechanism that changed"). This is Opus's judgment call, made by diffing today's Haiku
    findings against the last shortlist file, not a mechanical string diff.

To diff: list `~/keel/proposals/*-shortlist.json`, sorted by filename (dates sort lexically),
take the most recent one before today's, hand both to the Opus judgment step.

## What has already been measured — read this before proposing anything

**Read `~/Development/work/CodeGate/keel/docs/experiments/` before writing any output.** As of
2026-08-13 the shipped strategy library has been measured to exhaustion, and a proposal that
re-treads it wastes the operator's time. The state of play, with the documents that establish it:

- **Every signal rule the codebase ships has been measured at its shipped constructor defaults
  across all 24 assets with hourly history** (`2026-08-12-shipped-defaults-intersection.md`), and
  `rsi_meanrev` additionally along its own frequency axis
  (`2026-08-12-rsi-meanrev-scale-vs-selectivity.md`). Both were re-run on a corrected engine
  (`2026-08-13-restated-under-a-production-faithful-engine.md`) — read that one first, since the
  other two carry numbers their engine produced and are annotated rather than restated.
- **No asset-rule-parameter combination is simultaneously measurable (`n>=100`), gross-positive,
  and net-positive at any fee this venue offers.** Zero of 90 in the intersection matrix, zero of
  82 in the rsi grid — including the 0.6% maker tier the account cannot currently reach anyway.
- The three rules fail for **three unrelated reasons**: `turtle_breakout` has real, broad gross
  edge destroyed by cost; `pullback_continuation` has essentially none (median gross 0.77);
  `rsi_meanrev` has the best gross distribution of the three but never fires enough to be
  admitted, and loses that edge the moment it is made to.

### Asset expansion is refuted as a PERFORMANCE fix — and still valid for power

This matters for what this skill is *for*. `turtle_breakout` is negative on every one of the 24
assets it has been measured on, so **adding a 25th cannot fix a rule negative on all of them.**
Never justify a candidate on the theory that it might be the one that works.

What expansion is still legitimately for, and why this skill is not retired:

- **statistical power** — more assets means more independent series for a walk-forward or a PBO
  matrix, which is a different argument from "one of them will be profitable";
- **the compliance pipeline** — attestation, screening and the allowlist are real work regardless
  of whether any rule is currently viable;
- **future rules** — a technique from §3b that does not yet exist will need a universe to test on.

Say which of these a shortlist serves. A shortlist implying "this asset may be profitable under
the current rules" is contradicted by measurement.

### Cost is the binding constraint, so every performance claim needs its cost regime

Round-trip friction on this venue is **~2.5% of notional** (1.2% taker per leg plus 5bp slippage
each way). That is the same order of magnitude as the per-trade edge of everything measured, and
it is what killed every otherwise-promising result — seven assets showed gross profit factors
above 1.0 and **all seven died at the maker rate**, before the taker rate actually paid was even
reached.

- **A performance number without its cost assumption is unusable.** A result developed on equities
  at 5bp, or on crypto perps at 2bp, does not transfer to a 1.2% taker venue, and nothing in the
  abstract will say so. Extract the cost regime, or state that the source omits it — the same
  standard this skill already applies to replication status.
- **Prefer techniques that reduce trade count or raise per-trade edge** over ones that raise win
  rate. `pullback_continuation` wins 52–75% of its trades at a profit factor near 1.0 — small wins
  against small losses — which is the profile most completely destroyed by a fixed per-trade toll.
  A high win rate is not evidence of robustness here; it is frequently the opposite.

### Thin assets carry flattered backtests

The liquidity guidance above governs *admission*. It does not govern *accuracy*. `backtest()`
applies one global `slippage_pct = 0.0005` to every product from BTC to TON (issue #259), so an
asset at the thin end is credited with execution it cannot achieve — by an unmeasured amount, and
always in the favourable direction. When a shortlist entry sits near the liquidity floor, say so
in the rationale: its eventual backtest will look better than it should.

## Output 3 — strategy research (two outputs; the split is load-bearing)

Scout recent quant/finance publications, university material, and reputable quant blogs. Every
citation, for both outputs below, must include **publication date** and note **whether the result
was independently replicated** (state "not stated" if the source doesn't say — quant literature is
full of unreplicated backtests, and omitting that flag is worse than not citing at all).

### 3a. Parameter proposals — backtestable today

**File:** `~/keel/proposals/YYYY-MM-DD-param-proposals.json`. Only for the four registered kinds
— nothing else is backtestable without a core code change:

| kind | class (dev repo) | example tunable params |
|---|---|---|
| `turtle_breakout` | `keel/strategy/rules/turtle_breakout.py::TurtleBreakout` | `entry_lookback`, `exit_lookback`, `adx_threshold`, `atr_stop_mult`, `target_rr` |
| `dca` | `keel/strategy/rules/dca.py::Dca` | `cadence_days`, `budget_usd`, `dip_bonus_pct`, `lookback_days` |
| `pullback_continuation` | `keel/strategy/rules/pullback_continuation.py::PullbackContinuation` | `ema_periods`, `entry_zone`, `stop_method`, `target_method` |
| `rsi_meanrev` | `keel/strategy/rules/rsi_meanrev.py::RsiMeanReversion` | `oversold`, `overbought`, `rsi_period`, `atr_mult`, `fixed_rr` |

```json
{
  "proposals": [
    {
      "kind": "turtle_breakout",
      "params": {"entry_lookback": 55, "atr_stop_mult": "2.5"},
      "rationale": "why these values, tied to the citation below",
      "not_already_covered": "why this is not inside a grid already swept -- name the experiment document checked",
      "citation": {
        "url": "https://...",
        "publication_date": "2024-03",
        "independently_replicated": false,
        "cost_regime": "the fees/slippage the cited result assumed, or 'not stated'",
        "sample_size": "trades the cited result rests on, or 'not stated'",
        "evaluation_window": "the period it was measured over, or 'not stated'"
      }
    }
  ]
}
```

**`not_already_covered` and `cost_regime` are required, and both exist because of measured
history.** A `turtle_breakout` proposal of `entry_lookback: 55` is inside a grid that has already
been swept — 144 configurations across six assets, whose mean-across-assets winner then
transferred to eighteen unseen assets and reproduced its *losing* profit factor to within 0.034.
Proposing another cell of that grid is not new information. If a proposal lands inside swept
territory, either drop it or say explicitly what makes this cell different from the ones measured.

`cost_regime` is the same discipline this skill already applies to `independently_replicated`: a
number whose assumptions cannot be recovered is not evidence. State "not stated" when the source
omits it — that is a real answer and a useful one.

**Frame any performance claim against the fill model the human's backtest will actually use.**
Since #258, `backtest()` fills an entry at the **next bar's open plus slippage** — a market order,
because that is what `execution/executor.py` places live (`order_type="market"`,
`limit_price=None`). It does *not* rest an order and wait for a level. Two consequences for a
proposal:

- **A technique that assumes a resting limit or stop entry will not be executed as described.** Its
  edge, whatever the source measured, depends on getting a chosen price; keel takes the next open,
  favourable or not. `pullback_continuation` is the worked example — its `entry = signal_bar.high +
  buffer` demands follow-through, and a market fill takes the trades it meant to decline, which
  doubled its trade count and dropped its gross PF from 0.92 to 0.77.
- **Say so when a proposal depends on entry timing.** That is a real finding for the operator and
  it points at issue #260 (making the executor honour a conditional entry), not at a parameter.

**`sample_size` and `evaluation_window` exist because both traps have already bitten this project,
and both are visible in a paper's own abstract if you look.**

- *Sample size.* `rsi_meanrev` showed the best gross profit factor of the three shipped rules —
  median **1.1631** — on a median of **38 trades**. Forced to fire enough to be admitted, the same
  rule collapsed to **0.8396** across 82 cells at `n>=100`, and not one was net-positive at any
  fee. That was not a decline as trades accumulated; it was a level shift at the floor. **A
  headline profit factor on a small sample is a lottery ticket, not an edge.** If a source does
  not state its trade count, say so — that omission is itself the finding.
- *Evaluation window.* ZEC-`turtle` printed **1.555 gross** over five years while running **three
  consecutive losing years** and compressing 92.7% of its lifetime PnL into 2025–26. A result
  measured over one regime is a claim about that regime. Record the window so the operator can see
  whether the claim spans more than one.

Report these; do **not** invent a rejection threshold from them. The counter-example is in the
merged record: ZEC under `pullback_continuation` is the only asset-rule pair in the study with **no
losing complete year** — and a gross profit factor of **0.875**. Perfectly stationary at losing
slightly, reliably. A stationarity gate passes that and rejects ZEC-`turtle`, which at least made
money gross. Stationarity is a diagnostic, never a filter.

This skill still never runs a backtest — it frames the numbers so the human's run is interpretable.
Verifying the engine's own invariants (the pending-lifespan assertion, the intent-divergence log)
belongs to whoever runs that backtest and that live cycle; both are unobservable from here. The
lifespan assertion in particular cannot be "checked in the log": it raises, so a violation ends the
run rather than printing anything.

Verify each param name against the class's actual constructor in the dev repo before writing —
don't invent a kwarg. `keel rules seed` only seeds each kind's *constructor defaults*; turning a
proposal into a backtest today means a human either edits a seeded row's params or uses
`Repository.insert_rule` (`keel/data/repository.py`) directly, then runs `keel rules backtest
<rule_id>` or `keel simulate`. That mechanic is the human's call — this skill only supplies the
params + citation, never runs a backtest or seeds a row itself.

### 3b. Research briefs — genuinely new techniques

**File:** `~/keel/proposals/YYYY-MM-DD-research-briefs.md`. For anything `RULE_REGISTRY` doesn't
implement. Each brief: what the technique is, the evidence (with date + replication status), what
implementing it in keel would require (roughly — new `Rule` subclass, registry entry, human
review), and honest weaknesses/failure modes. **Never write or propose code for the trading core.**
A brief is prose and citations only — no diffs, no Python, not even a sketch that reads like a
patch. If you're tempted to draft the class, stop: that's the exact boundary that keeps activity
gated on human review.

## Worked examples

**Good candidate entry** (goes in the shortlist):
```json
{
  "asset": "SOL",
  "rationale": "Top-10 by market cap, deep USD spot liquidity on Coinbase, 5+ years of price history, base SOL-USD listing carries no lending/staking-derivative wrapper.",
  "sources": [
    "https://www.coinbase.com/price/solana",
    "https://coinmarketcap.com/currencies/solana/"
  ],
  "shariah_hypothesis": "UNVERIFIED: utility L1 smart-contract platform (sector: infrastructure); native base-layer token, not a claim on an issuer (backing: native); base SOL carries no protocol-level guaranteed yield for bare holding — this is distinct from staked-SOL derivatives, which are NOT this candidate and should be scouted separately if ever considered."
}
```

**Correctly rejected — never reaches the shortlist:**
- `SOL-PERP` or `BTC-28AUG26-CDE` surfaced during the Coinbase sweep — dropped at the mechanical
  pre-filter (not spot; rail 19 vetoes it regardless of what a human decides), never even reaches
  the Opus judgment step. Log this in the run log's "considered, mechanically excluded" note so
  the operator knows the sweep saw it and why it's absent.
- A DeFi lending-protocol governance token whose Haiku research surfaces a documented
  interest-bearing yield mechanism — Opus drops it before composition and logs why ("plausible
  `riba_yield`/`haram_sector` per §28.4; not worth the operator's attestation time"). This is a
  judgment call to save the human time, **not** a substitute for `screen_asset` — if the operator
  disagrees, they can still hand-run `keel assets screen` on it themselves.

## Do NOT

- **Fabricate a citation.** Every URL in every output must be one a fetch tool actually returned
  this run. A well-formed-but-unfetched URL passes keel's schema check and is the single most
  damaging failure mode this skill can produce — the human trusts it precisely because it looks
  real.
- **Propose a non-spot instrument.** No perps, dated futures, indexes, or FX/commodity wrappers —
  ever, regardless of liquidity or narrative.
- **Write or propose code into the trading core.** No `Rule` subclass drafts, no `RULE_REGISTRY`
  edits, not even "just as an example" — that's a human-reviewed change, full stop.
- **Write to any `keel-*.db`, `config*.yaml`, or anything under `~/keel/` other than
  `~/keel/proposals/`.**
- **Run `keel assets attest`, edit `allowlist`, or place/confirm a trade.** Attestation and
  admission are the human's call, downstream of your output, never inside this skill.
- **Hardcode the allowlist.** Read it from `~/keel/config.live-sandbox.yaml` every run.
- **Pass `shariah_hypothesis` (or any LLM judgment) into an admission decision.** It's a head
  start for the human, labeled UNVERIFIED, and nothing more.

## Rationalizations to catch yourself making

| Excuse | Reality |
|---|---|
| "I recall this fact, close enough to a citation" | Memory isn't a source. If you didn't fetch a URL this run, it isn't a source. |
| "This URL pattern is probably right" | Never construct a plausible URL. Paste back exactly what the fetch tool returned, or omit the candidate. |
| "One candidate's missing a source, I'll just drop it quietly" | Name it and the reason in the run log — same spirit as `proposer.py`'s own `invalid` list: excluded, never silently vanished. |
| "It's basically the same asset as the perp, just propose the base" | Fine — but only if you can independently source the spot listing itself, not by relabeling the perp finding. |
| "I'll sketch the Rule subclass so the human can see what I mean" | That's code into the core. Describe it in prose in the research brief; do not write it. |
| "This asset looks strong — it might be the one the rules finally work on" | `turtle_breakout` is negative on all 24 assets measured. A 25th cannot fix a rule negative on every one. Justify candidates on power, compliance or future rules — never on hoped-for performance. |
| "The paper reports a profit factor of 1.8, that's well above break-even" | Above break-even *at its own costs*. At 2.5% round-trip, seven assets with gross PF > 1.0 all died at the maker tier. A number without its cost regime is not evidence. |
| "High win rate, so the edge is robust" | Often the opposite here. `pullback_continuation` wins 52–75% at PF ≈ 1.0 — small wins against small losses, the profile a fixed per-trade toll destroys most completely. |
| "This backtest looks clean, the numbers are all plausible" | Two engine defects (#254, #256; #257, #258) produced plausible, internally consistent output for the life of the project while 2,712 tests passed. Plausible output is not evidence of a working engine — check what the numbers *cannot* distinguish. |

## After writing outputs

Report to the operator: what's in each file, the run-log diff (new/dropped/changed), and the
explicit next human steps — `keel assets propose --from ~/keel/proposals/<date>-shortlist.json`,
then `keel assets attest ...` for anything worth pursuing, then `keel fetch` + `keel rules
backtest` / `keel simulate` for strategy params. Never phrase the report as "I added" or "I
proposed for trading" — you scouted; the human decides.
