# Trials Ledger + PBO/CSCV — Design

**Date:** 2026-07-20
**Status:** Approved, pending implementation plan
**Source basis:** KB §78 (Bailey/Lopez de Prado DSR + PBO/CSCV + Harvey-Liu haircut), §73 (backtest
overfitting), §54.10 (OOS firewall), §54.22 / §73.4 (verdict statistic), §58.11 (random-entry null).
**Scope:** §78.13 build items **0, 4, 5**. Items 1–3 (`E[max_N]`, `N̂ = ρ̂ + (1−ρ̂)·M`, MinBTL reporting),
6 (DSR) and 7 (haircut Sharpe) are explicitly **out of scope** and follow later.

---

## 1. Why this lands before anything else

§78.4 establishes that this is a **precondition, not good practice**: a sweep run before the ledger exists
*destroys* the `V[{SR_n}]` needed to score it. The project's recorded next-build order therefore puts the
ledger first, ahead of the `donchian_entry_n` lookback work, the `exit_lookback` ratio test, and the NR7
squeeze gate — none of which may run until this ships.

The ledger is also the shared substrate for three separate tools. §78.13 item 0 constrains its schema
accordingly: `V[{SR_n}]` (§78.1), `ρ̂` (§78.2) and the CSCV matrix (§78.6) all need **per-trial P&L
series**, not per-trial summary numbers. Storing the series is the only expensive part of the design
(a few KB per trial) and it is non-negotiable — without it, every trial recorded before the schema
changes is permanently unavailable to all three tools, and §78.4 showed `V` alone swings DSR by 3×.

## 2. Non-goals

- **Deflated Sharpe Ratio, haircut Sharpe, MinBTL reporting.** Later builds; §78.13 orders PBO first
  because it has fewer unmeasured inputs and no distributional assumptions.
- **Any of these statistics as a sweep ranking key.** See §6, the Strathern rail.
- **Blockchain / distributed ledger.** Assessed and declined; see §4.3.
- **HLZ correlation-adjusted simulation, entropy-based `N̂`, PCA reduction, per-configuration
  single-trial p-values.** All explicitly not-built per §78.13.
- **Pausing deployment.** §78.12 rule 2: the ledger must land before the next *sweep*, but not before
  the next *trade*. Running the shipped 40/20 rule increments `T` and increments nothing else.

## 2.1 Three things called "ledger" — and why they must not converge

The word is overloaded in this project. Three distinct records exist or are planned, and conflating
them is the most likely way a future reader misreads this spec.

| | what it records | store | in git? |
|---|---|---|---|
| **Trials ledger** *(this spec)* | experiments: configurations tried, provenance, outcome | JSONL, hash-chained | **yes** |
| **Trade record** *(already built)* | live orders, fills, positions, realised P&L | `keel.db` — `orders`, `positions`, `transactions`, `trade_outcomes`, `pnl_daily`, `journal` | **never** |
| **Purification ledger** *(§65.9, queued)* | interest/reward/yield requiring purification | TBD, compliance-owned | **never** |

**This spec covers only the first.** It records experiments, not money. A row is *"swept
`donchian_entry_n` over {20,40,55}, selected 40, provenance `fitted`"* — never a fill, a balance, or a
position.

⚠️ **The git-tracking decision in §4.1 is safe only because of that boundary.** The trials ledger holds
parameter dicts and P&L series derived from simulations over public candle data — no personal or
financial information. The trade record holds the opposite, and **this repository has already had a PII
incident on exactly that data**: `transactions/` was purged from all history with `git-filter-repo` and
force-pushed. Routing live trades into a git-tracked file would walk directly back into it.

The two also differ on every operational axis: the trials ledger is small (hundreds of rows over years),
reviewed by humans in diffs, must survive `keel.db` being deleted, and its adversary is motivated
self-deception. The trade record is high-volume, machine-consumed, private, and its adversary is data
loss. Different threat models, different stores.

### Scoping: per research program, not per user

The trials ledger is **not per-user**. A trials budget is spent by whoever develops the rule library, so
the ledger is a property of **the shipped rule library** — closer to a nutrition label than to user
data. Anyone trading the Turtle inherits the same `M`, the same MinBTL and the same PBO, because they
are trading the same searched-for parameters. Trade records, by contrast, are per-user by definition.

⚠️ **Recorded for the future, not scoped here:** if end users were ever allowed to sweep their own
parameters, per-user ledgers would *systematically understate* the problem. A thousand users each
keeping an individually-correct ledger yields a population-level `M` a thousand times larger than any
single ledger shows, while the winners are selected across the whole population. That is §78.7's
file-drawer problem at platform scale, hidden by construction rather than revealed. ⇒ **"let users tune
their own parameters" carries a hidden statistical cost** and must not be adopted as an obvious feature
without addressing it. (SaaS remains unscheduled and gated on legal/licensing/broker-ToS.)

## 3. Module layout

A new package `keel/research/` — validation *of the research process*, distinct from `keel/sim/`
(validates strategies) and `keel/strategy/` (runs them). Keeping it separate matters because §6's
rail forbids these statistics from reaching the selection path, and a package boundary makes that
dependency direction visible.

`ledger.py`'s API is deliberately narrow — `append()`, `read()`, `verify_chain()` — so the JSONL store
can be replaced without touching `cscv.py` or `promotion.py`. That is the whole of the concession to a
possible future multi-tenant backend (§2.1); nothing further is built for it.

```
keel/research/ledger.py    TrialRecord, append(), read(), verify_chain()   ← narrow API on purpose
keel/research/cscv.py      pbo() + the three §78.8 companions
keel/research/matrix.py    builds the (T × N) matrix from a declared candidate grid
```

Touched existing modules:

| module | change |
|---|---|
| `keel/strategy/promotion.py` | G4 conjunction gate (§7) |
| `keel/sim/report.py` | PBO reporting section; `keel simulate` auto-records its trial |
| `keel/cli.py` | `keel trials {record,list,verify,pbo}` |
| `config.yaml` / `keel/config.py` | `research:` block holding the two G4 constants |

Stdlib only, `Decimal` throughout, consistent with the existing codebase constraint (no NumPy/Pandas;
see the main spec §10). The only transcendental is `log`, applied to a ratio of ranks; `Decimal.ln()`
is native.

## 4. The ledger

### 4.1 Location and format

**Git-tracked JSONL at `docs/experiments/trials-ledger.jsonl`.**

Not a `keel.db` table. `keel.db` is gitignored and regenerable; a ledger whose entire purpose is
auditability must live inside the audit trail. JSONL because it is append-only by nature, diffable in
review, and needs no migration when the schema grows.

`Decimal` values serialise as strings, matching the repo's existing TEXT-money convention.

### 4.2 Schema

Per §78.13 item 0:

```
trial_id        stable identifier
timestamp       unix seconds
session         free-text session/experiment label
rule            rule name
params          full parameter dict
provenance      a_priori | fitted                                   (§73.12 #4)
kind            sweep_node | ablation | rule_retirement |
                asset_prune | threshold_nudge
decision        selected | rejected | diagnostic_only
per_trade_pnl   list[Decimal]
per_bar_pnl     list[Decimal]
series_missing  bool
summary         {sr_trade, expectancy, trade_count}
prev_hash       sha256 of the previous row
row_hash        sha256 of this row
```

`row_hash = sha256(canonical_json(row minus row_hash))`, canonical JSON being sorted keys with fixed
separators. `prev_hash` chains to the immediately preceding row; the first row's `prev_hash` is the
zero hash.

### 4.3 Tamper-evidence, and why not a blockchain

A blockchain's product is trustless agreement among mutually distrusting parties. There is one party
here, running a local agent against a private repo, so the consensus machinery is pure cost. It also
fails on this threat model: the realistic adversary is **future-us quietly dropping an inconvenient
trial** so a rule clears its floor, and a private chain does not prevent that because we hold the keys.
A public chain would raise the cost but requires publishing strategy research permanently, paying gas,
and adding a network dependency to a stdlib-only stack.

§78.7 is explicit that this is a **discipline** problem — *"The researcher must provide full information
regarding the actual trials conducted… Hiding trials will lead to an underestimation of the overfit"* —
and technology that appears to solve it mostly launders the discipline question into a technical one.

What is worth taking is the **chain**, not the consensus:

1. **Hash chain in the file** — editing or deleting any row breaks verification for every row after it.
2. **Git** — the ledger is committed, so commit history independently timestamps every append and
   detects wholesale replacement of the file.

These cross-verify. Neither is cryptographically binding against a determined author — both could be
regenerated, and this repo has force-pushed before. The property delivered is **tamper-evident, not
tamper-proof**: it converts a silent deletion into a visible one, which is the right target given the
actual adversary.

### 4.4 Two `N` accountings

Computed and reported separately, never conflated:

- **`M`** — every row. Feeds MinBTL and (later) DSR.
- **`N_decisions`** — rows where `decision != diagnostic_only`.

§78.6 resolves the apparent MinBTL-vs-CSCV contradiction: MinBTL's `N` counts trials whose outcome
**influenced a shipped decision**; CSCV's `N` counts **columns in a diagnostic matrix**. A CSCV run
does not increment the decision budget *provided its selected column is discarded* — enforced in §6.

### 4.5 Recording paths

Both, per approved decision:

- **Automatic** from the production `keel simulate` path.
- **Manual** via `keel trials record` for scratchpad experiments.

Both are needed. Auto-only would have silently missed nearly every trial in this project's actual
history — the period walk-forward, the ADX ablation, the rank-markets diagnostic — all of which ran as
scratchpad scripts by design.

### 4.6 Backfill

Historical trials are reconstructed from the experiment records in `docs/experiments/` and the project
memory, and appended as **summary-only rows with `series_missing: true`**.

- They **count toward `M`** and therefore toward MinBTL.
- They are **excluded from the CSCV matrix** by construction (`matrix.py` refuses them), because
  §78.4's warning already came true: those sweeps destroyed the per-bar series needed to score them.

Sources to reconstruct, at minimum: the entry-period walk-forward, the ADX ablation and random-entry
control arm, the S1 profitable-trade filter test, the S1+S2 ensemble run, the rank-markets / ER
diagnostic, the CTS-bucket diagnostic, and the confluence-gate refutation.

Reconstruction is interpretive — "how many configurations did the walk-forward actually try" is not
always crisply recorded. **Where ambiguous, over-count.** §78.7 is asymmetric about this: hiding trials
underestimates overfit, and padding with deliberate losers is the opposite abuse. Over-counting genuine
uncertainty errs toward the conservative side of the first failure and does not commit the second.

## 5. CSCV

### 5.1 Algorithm

Algorithm 2.3 as specified in §78.6, unmodified:

1. Matrix `M` of order `(T × N)`; column `n` is configuration `n`'s per-bar P&L, rows synchronous.
2. Partition rows into an even `S` of disjoint equal submatrices.
3. Form all `C(S, S/2)` combinations.
4. For each combination: training set `J` = the chosen `S/2` submatrices **joined in original order**;
   testing set `J̄` = the complement, also in original order. Compute performance per column on each,
   rank both. Let `n*` be the IS-best. Take `ω̄ = r̄_{n*} / (N+1)`, then `λ = ln(ω̄ / (1 − ω̄))`.
5. `PBO = φ = fraction of combinations with λ ≤ 0`.

**Parameters:** `S = 16` (§78.6's recommendation; `C(16,8) = 12,870`, σ[f(λ)] ≤ 0.0044, ~quarterly
blocks — the paper's own reasoning about a 4-year daily sample transfers directly to our 5 years).

Note the paper's arithmetic slip: it states 12,780 combinations for `S = 16`; the correct value is
`C(16,8) = 12,870`. Its σ estimate is right for 12,870. Recorded so nobody chases the discrepancy.

**Unit:** daily bars, `T = 1,819`, **not** the per-trade series. §78.6 is explicit — `T` in trades is
13–31, which halved is 6–15, not enough to compute a per-period statistic on. This is the one place
where §73.4's per-trade unit choice is set aside.

`T` truncates to 1,808 (a multiple of 16 → 113 rows/block), **dropping the 11 oldest bars** so the
recent window stays intact.

### 5.2 Metric choice, and the performance decision behind it

The naive implementation is too slow: 12,870 combinations × ~12 columns × 904 rows ≈ 140M operations,
which in `Decimal` is tens of minutes.

**Sortino is fully decomposable across blocks; drawdown metrics are not.** Sortino needs only `count`,
`Σr` and `Σ(r⁻)²` against a fixed zero target — all additive. Precomputing those three aggregates once
per (block × column) makes each combination `O(S/2)` instead of `O(T)`: ~1.2M operations, seconds, in
exact `Decimal` with no numerical compromise.

This is not a trade-off against fidelity. §78.6 explicitly blesses Sortino — the procedure is *"generic
and can be applied to any performance evaluation metric R (Sortino ratio, Jensen's Alpha, Probabilistic
Sharpe Ratio, etc.)"* — and §73.4 already makes Sortino the endorsed verdict statistic over Sharpe
(§54.22's intermittent-returns objection to Sharpe). Sortino is additionally order-independent, which
sidesteps step (b)'s join-order caveat.

Submatrices are nonetheless **joined in original order** regardless of metric, so that
`return / max-drawdown` can be supplied as an opt-in slower metric without being subtly wrong.

**Reported caveat, not silently resolved:** running on daily bars reintroduces §54.22's mostly-cash-days
objection — zero-return days on an intermittent book flatter the denominator. §78.6 acknowledges this
directly. The report therefore prints Sortino **both all-days and exposed-days-only**, rather than
picking one.

### 5.3 The three free companions (§78.8)

1. **Performance degradation.** OLS of OOS on IS across combinations: `R̄_{n*} = α + β·R_{n*} + ε`.
   Measured, not assumed — §73.7 could only argue the sign from AR(1) theory. Calibration from the
   paper: real strategy **−0.35**, pure random walk **−0.61**, overfit real strategy **−0.75**.
2. **Probability of loss** `Prob[R̄_{n*} < 0]`. Reported **separately** from PBO: §78.8 warns that even
   at `φ ≈ 0` this can be high, meaning poor OOS performance *for reasons other than overfitting*.
3. **Stochastic dominance.** Does the distribution of `R̄_{n*}` dominate that of all `R̄`? First-order:
   `Prob[R̄_{n*} ≥ x] ≥ Prob[Mean(R̄) ≥ x]` for all `x`. Second-order via the cumulative integral.
   This is the direct test of *"is our parameter selection better than picking a configuration at
   random?"* — §58.11's random-entry-null question lifted from entries to the selection process itself.
   §78.8 calls it the most under-rated item in the source.

### 5.4 The matrix driver

`keel trials pbo` runs a **pre-declared** candidate grid, writes every column to the ledger as
`decision: diagnostic_only`, and computes φ.

`N ≫ 10` is required for `ω̄` to have enough granularity (§78.6); the driver warns below `N = 10`.
This does not conflict with §73's `N ≤ 3` budget — see §4.4.

## 6. The Strathern rail, enforced structurally

> *"We must warn the reader against applying CSCV to guide the search for an optimal strategy… when a
> measure becomes a target, it ceases to be a good measure… PBO should not be the objective function on
> which such selection relies."* — §78.7

**PBO, and later DSR and the haircut, may gate or report. None may ever appear in a sweep's ranking
key.** Minimising PBO across configurations is itself a sweep and manufactures apparent edge the same
way (§73.1).

Enforced in code, not only in process:

- `PBOResult` carries `pbo, logits, is_oos_pairs, degradation_slope, degradation_intercept, prob_loss,
  dominance_1st, dominance_2nd, n_combinations` — and **no field holding a configuration or parameter
  dict**. There is no `best_config` and no argmax return path, so a caller cannot read a parameter
  choice out of a diagnostic run.
- A test introspects the dataclass fields and fails if such a field is added later.
- `promotion.py` never sorts by PBO.

## 7. The G4 gate

```
G4 fails  iff  pbo > pbo_max  AND  degradation_slope < slope_floor
             (defaults: pbo_max = 0.05, slope_floor = −0.5)
```

**Conjunction, not the scalar.** §78.7 limitation #4: *"it is entirely possible that all the N
strategies have high but similar Sharpe ratios… PBO will be high. Here overfitting is among many
'skillful' strategies."* That is this project's plateau case exactly — §54.10 and §73.13 direct us to
*prefer* a broad plateau, and a broad plateau is a set of near-identical configurations, which produces
high PBO **by construction**. A bare 0.05 gate would penalise the robust choice.

§78.7 supplies the correct reading rule, which the conjunction encodes: high PBO with a flat, positive
OOS scatter is the *good* outcome; high PBO with a steeply negative slope is the bad one.

**Justifying `slope_floor = −0.5`:** from §78.8's calibration — real strategy −0.35, pure random walk
−0.61, overfit real strategy −0.75 — `−0.5` sits between the real-strategy case and the noise/overfit
cases.

Both constants live in `config.yaml` and are marked in-code as **never to be tuned**. Tuning them to
obtain a desired verdict is itself the Strathern violation this section exists to prevent.

**Expected first result:** the Turtle's 40/20 was selected from a walk-forward across a candidate set,
so a **high PBO is the likely outcome**. The degradation slope decides whether G4 actually bites. The
verdict is already `TRAIN MORE` on G2 (`n_trades 31 < 100`); a G4 failure would add a second, different
failure mode. That is information, not a setback — but the report should be expected to get worse
before it gets better, and per §78.7 that must not be answered by relaxing a threshold.

## 8. Testing

TDD throughout. The load-bearing test is the **power replication** from §78.8: CSCV must separate a
seeded pure-noise matrix (expect PBO ≈ 0.55) from the same matrix with a signal injected (expect
≈ 0.13). §78.6 notes this validation matters precisely because it demonstrates *power*, not merely
conservatism, on a sample barely shorter than ours. An implementation that cannot reproduce the
separation is wrong regardless of what it reports on the Turtle.

Alongside:

| area | test |
|---|---|
| CSCV bounds | perfect IS/OOS rank consistency → PBO 0; perfect inversion → PBO 1 |
| combinatorics | `C(16,8) == 12870` exactly |
| ordering | drawdown-metric result differs under shuffled vs original join order (proves order is preserved) |
| decomposition | block-aggregate Sortino equals the direct full-series computation |
| hash chain | append 3 rows → verify passes; tamper row 2 → verify fails at row 2 and every row after |
| backfill | `series_missing` rows counted in `M`, refused by `matrix.py` |
| G4 | four-case truth table over (pbo above/below, slope above/below) |
| Strathern rail | `PBOResult` exposes no configuration-carrying field |

Determinism is itself a property worth asserting: §78.7 notes *"running CSCV twice on the same inputs
generates identical results"*, which matters for an agent whose artifacts are meant to be auditable.

## 9. Sequencing

1. `ledger.py` + hash chain + `keel trials {record,list,verify}`
2. Backfill of historical trials
3. `cscv.py` — `pbo()` and the three companions
4. `matrix.py` + `keel trials pbo`
5. G4 in `promotion.py` + `config.yaml` constants
6. `report.py` section + `keel simulate` auto-record

The backfill lands early so that the first real PBO run is scored against an honest `M`.

## 10. What this does not do

§78.7 limitation #3: *"It does not check whether the backtest is correct… If the backtest is flawed due
to bad assumptions, such as incorrect transaction costs or using data not available at the moment of
making a decision, our approach will be making an assessment based on flawed information."*

**PBO is orthogonal to look-ahead bias and fee realism.** It does not substitute for
`strategy/backtest.py`'s order-of-events handling or the fee/slippage model, and it does not retire
§54.10's OOS firewall — §78.7 argues the firewall is *necessary but not sufficient*, since hold-out is
silent about the search process while CSCV is not. Both are needed.

Two known-open defects remain untouched by this work and are recorded here so they are not mistaken for
things PBO addresses: `sim/report.py:175`'s pooled concatenation without fixed effects (§79.11, biased
upward), and `sim/benchmark.py`'s DCA-accumulation-only null (§79.12, wants a same-capital
buy-and-hold comparator).
