# The keel research toolkit — thirteen modules, one front door, and what none of them will say

`keel/research/` is thirteen modules that measure whether a trading rule's edge is real, whether
a sweep that found it was overfit, whether an equity curve's shape was luck, and whether the
evidence for any of that is even large enough to trust. Jesse markets two of these as feature
cards — "Rule Significance Testing" and "Monte Carlo Analysis." keel ships both, plus eleven more
Jesse doesn't have, and the honest answer more often than not is *no* — no distinguishable edge,
no candidate worth proposing, no measurement large enough to say anything. That is not a defect
in the toolkit. It is the toolkit working, and `significance.py`'s own docstring states the rule
that governs everything below it:

> a significance tool here must be able to say "not distinguishable from zero" and mean it. A
> tool that cannot say no is a flattery tool.
> — `keel/research/significance.py:8`

`keel research` is the front door onto these thirteen modules. `keel research index` names all
thirteen — what each answers, what each cannot answer, and the command (or, for a module with no
subcommand of its own yet, the pre-registered `docs/experiments/` driver) that gets you the
number. Five of the thirteen already had a home under `keel trials`/`keel rules`
(`pbo`/`deflate`/`monte-carlo`/`walk-forward`/`lookahead`) and are registered a second time under
`research`, the same click command objects rather than a second implementation. It adds no
statistics of its own; every number on this page and every number the CLI prints comes out of
`keel/research/*`, unchanged. What follows is what each module answers, what it refuses to answer
even when asked nicely, and the command that runs it. Read the "cannot answer" column as
carefully as the "answers" column — that column is the actual product.

## The thirteen, at a glance

| module | answers | cannot answer | run it with |
| :--- | :--- | :--- | :--- |
| `significance.py` | is a family's edge distinguishable from its break-even, priced at the fee actually paid | whether the edge will hold going forward, or which family/regime to report — you name both | `keel research index --module significance`\* |
| `montecarlo.py` | was the equity curve's *path* (drawdown, time underwater) unusual for this set of trades, in some order or under resampled price history | whether the *final equity* was luck — that percentile is exactly 1/2 by construction, always | `keel research monte-carlo` (`keel trials monte-carlo`) |
| `cscv.py` | the probability a configuration selected in-sample degrades out-of-sample, over a matrix of configurations already tried | which configuration is the best one — it never returns that, by construction | `keel research pbo` (`keel trials pbo`) |
| `deflate.py` | given N trials tried, the Sharpe bar the winner had to clear, and how much data that needs | what N and correlation to assume — it reports a band across assumptions rather than guess one | `keel research deflate` (`keel trials deflate`) |
| `walkforward.py` | does a GIVEN fixed parameter set hold up across rolling train/test windows, and does it degrade | which parameter set, fold or window is best — none is ever computed | `keel research walk-forward` (`keel trials walk-forward`) |
| `independence.py` | how much two rules' (or two horizons') signals overlap in time, position and P&L | whether either rule is profitable, or which one to keep | `keel research index --module independence`\* |
| `throughput.py` | how much volume a fee-free allowance can honestly carry this month, and how long evidence takes to accumulate | it never enlarges an allowance to fit a plan — a product that doesn't fit is deferred, not squeezed in | `keel research index --module throughput`\* |
| `cts_factors.py` | do the 11 CTS confluence factors carry independent evidence, or is one momentum read counted three times | the biased ("obvious") conditional sample is computed but never allowed to carry the headline | `keel research index --module cts_factors`\* |
| `tuning.py` | for a declared parameter space, does a train/held-out study produce a candidate clearing held-out sign AND PBO ≤ 0.5 | it never auto-tunes a live/paper profile, and a pass is a hypothesis, not a promotion | `keel research index --module tuning`\* |
| `bias.py` | does a rule's decision at bar N change when bars after N become visible (lookahead / recursive drift) | whether the rule is profitable — this is about information leakage only | `keel research lookahead` (`keel rules lookahead`) |
| `ledger.py` | what experiments were run, in what order, tamper-evidently | it is tamper-*evident*, not tamper-*proof*, and it never touches money | `keel trials record` / `list` / `verify` (no `research` alias — see below) |
| `matrix.py` | assembles the T×N matrix `cscv.py` needs from ledger trials, enforcing the "true matrix" condition | anything about performance itself — it is plumbing, not a question of its own | no direct command — runs inside `keel research pbo` |
| `pooled_review.py` | the #427 pooled-review machinery: descriptive n_eff-corrected intervals, never a verdict on the edge | it renders no pass/fail on the edge, ever — see [the 2026-09-30 review](#the-2026-09-30-pooled-review-427) below | `keel research index --module pooled_review`\* |

\* These six modules have no dedicated `keel research` subcommand of their own yet — only
`keel research index`, which names every module, and the five aliases above, are wired as of
this writing. Asking the index for one module by name (`--module NAME`, the bare filename minus
`.py`) prints its `runs as` line, which today names the pre-registered `docs/experiments/`
driver that exercises it — e.g. `keel research index --module significance` names
`docs/experiments/2026-08-21-rule-family-significance.py`. Until each of these six gets its own
subcommand, that driver (or a direct `import keel.research.<module>` call, as this page does
below) is how you actually run one.

`ledger.py` and `matrix.py` are the two modules that were never going to get a `keel research`
alias in the first place: `ledger.py`'s record-keeping commands (`record`/`list`/`verify`) stay
under `keel trials`, where they have lived since before this front door existed, and `matrix.py`
has never had a command of its own — it is the assembly step behind `keel trials pbo`/
`keel research pbo`, not a question a reader asks directly. The index still names both; the
command surface just doesn't duplicate what already works.

## The two Jesse markets, and what keel adds to each

### `significance.py` — is the edge real, at the price you actually pay?

The question is a one-proportion test against break-even, with the null set by the fee *actually
paid*: `keel/research/significance.py` prices the same reconstructed trades at both fee regimes
a keel deployment can be in — the 120 bp taker fee outside the venue's fee-free allowance, and
zero inside it — and never averages the two, because the cross-verification behind #475 found the
fee difference *is* the result (decisively negative outside, indistinguishable from break-even
inside). It also refuses to pool trades as if they were independent: signals fire in herds (about
eight assets the same UTC day, ICC 0.212), so `n_eff` divides the pooled count by
`throughput.design_effect()` before any standard error is formed — a pooled 100 comes out to
roughly 39 effective observations, not 100.

What it cannot answer: whether the edge will hold going forward (it is a test against a fixed
historical sample, not a forecast), and it will not pick which family or fee regime to headline —
every subcommand run names both explicitly. It also will not manufacture power a sample doesn't
have: an underpowered result is reported as "not distinguishable from zero," not massaged into
significance by choosing a friendlier n.

Run: no dedicated subcommand yet — `keel research index --module significance` names the
pre-registered driver, `docs/experiments/2026-08-21-rule-family-significance.py`; the transcript
below calls the module directly.

### `montecarlo.py` — trade reshuffling and candle bootstrap, and the invariant Jesse's marketing skips

Two nulls: `reshuffle` (the same closed trades, in different orders) and
`moving_block_bootstrap` (consecutive blocks of real candles resampled with wrap-around, then
re-backtested). Both are report-only, deterministic under an explicit seed, and neither scores or
gates anything.

The invariant `montecarlo.py` names and Jesse's copy doesn't: a permutation of a multiset sums to
the same number. Reshuffle the same trades into any order you like and every path ends at the
same final equity —

> so every reshuffled path ends at the observed final equity and THAT percentile reads exactly
> 1/2 (ties count half).
> — `keel/research/montecarlo.py:12-13`

That is not a weak result; it is mathematically guaranteed to be 1/2 before a single path is
drawn. Trade-order reshuffling therefore cannot tell you whether the final equity was luck — the
question it *can* answer lives in the shape of the path between start and end, which is why the
module reports `max_drawdown` as its headline statistic and keeps the final-equity lines in the
output rather than hiding a number that always reads the same. The candle bootstrap is the
module's honest answer to "what if reshuffling isn't enough" — it preserves local
autocorrelation a naive resample would destroy, at a stated cost: block stitching creates a price
discontinuity at each seam the real series never had.

Run: `keel research monte-carlo` — an alias of the existing `keel trials monte-carlo`, same
command object, registered a second time.

## The Strathern rail: `cscv.py`, `deflate.py`, `walkforward.py`

Three modules exist because trying many configurations and reporting the best one lies to you
about how good that configuration really is — that's overfitting, and PBO/CSCV, the Deflated
Sharpe family, and walk-forward validation each measure a different piece of it. All three carry
the same rail, named after Marilyn Strathern's observation that `cscv.py` quotes directly:

> PBO may gate or report; it may never be a sweep's ranking key, because "when a measure becomes
> a target, it ceases to be a good measure" (§78.7).
> — `keel/research/cscv.py:8-9`

The mechanism is specific, not a vibe. A diagnostic score is allowed to *report* ("PBO is 0.62")
and allowed to *gate* (a proposal study can require `pbo <= 0.5` before it may even suggest a
candidate — `tuning.py`'s `OverfittingGate` does exactly this). What it may never do is become the
thing a sweep sorts, maxes, or picks a winner by — because the moment a score is optimized against,
the people running the sweep start (consciously or not) selecting for configurations that game the
score rather than configurations that are actually good, and the score stops measuring what it was
built to measure.

**`cscv.py`** enforces it at the return type: `PBOResult` "carries probabilities and slopes
only," and `tests/research/test_cscv.py::test_result_exposes_no_configuration_field` asserts the
dataclass's own field names never include `best_config`, `best_column`, `argmax`, `selected`,
`winner`, or half a dozen synonyms — a mutation that adds any of them to `PBOResult` fails this
test immediately. The CLI carries the same discipline one layer up:
`tests/research/test_trials_cli.py::test_pbo_command_reports_but_never_names_a_winner` runs
`keel trials pbo` (the command `keel research pbo` aliases) against a ledger seeded with six
`entry_lookback` values and asserts none of the candidate values — `entry=20` through `entry=45`
— ever appears in the printed output.

**`walkforward.py`** states the same rail for its own question — "a walk-forward validator that
reported a 'winning window' … would reintroduce exactly the selection-over-configurations the
rail exists to forbid" — and is pinned two ways in `tests/research/test_walkforward.py`:
`test_refusal_to_rank_enforced_by_source_scan` reads the module's own source text and asserts it
contains no keyed sort (`key=lambda`), no in-place `.sort(`, and none of the words "best",
"winner" or "optimal" anywhere in the file — not just in the rendered output, in the source
itself — and `test_report_dataclass_has_no_selection_fields` asserts `WalkForwardReport` carries
no field starting with `best` and none containing `select` or `chosen`.

**`deflate.py`** states the rail in its own header — "Reporting only — ⛔ per §78.7's Strathern
rail none of these may ever be a sweep's ranking key" — but has no equivalent structural pin: its
public functions return plain floats (`SR_0`, `DSR`, `MinBTL`), not a dataclass that could grow a
configuration-bearing field, so there is nothing for a source or field scan to catch. The
guarantee here is architectural rather than test-pinned — there is no return value shaped like a
winner to leak.

The front door inherits the same discipline, and it too is pinned mechanically rather than
promised: `tests/commands/test_research_front_door.py::test_research_module_never_sorts_ranks_or_maxes`
is an AST scan over `keel/commands/research.py` itself, and it fails on any `sorted()`/`max()`/
`min()` call with a `key=` argument, any `.sort(key=...)`, or an import of `heapq` or
`operator.itemgetter`/`attrgetter` — anywhere in the module, whether or not the value being
ranked looks rail-bearing. The blanket ban is deliberate: a scanner that only objects when the
sorted field *looks* like `pbo` or `dsr` is a scanner a rename defeats; the front door's job is
to place values in an order it was given, never an order a score chooses for itself, so the rule
has no exception clause. `test_rail_marked_on_exactly_the_three_strathern_modules` pins the other
half — that `cscv.py`, `deflate.py` and `walkforward.py` are exactly the three modules the
index marks as rail-bearing, no more and no fewer.

## The refusal, as a first-class result

Every evidence subcommand in `keel research` can print a refusal on stdout and exit 0. A
well-formed question the evidence cannot answer is the tool working, not the tool failing — the
alternative is a tool that always finds *something* to say, which is another name for a flattery
tool. This is not new behaviour invented for the front door: `keel trials deflate` already prints

```
refused: only 1 decision trial(s) recorded -- need >= 2 to form a trial count for
E[max SR_n]/MinBTL; record more trials with `trials record` and retry
```

and returns exit code 0 (`keel/commands/trials.py:169-172`, the comment beside it: "A refusal is
this command working, not this command failing — print it on stdout and exit 0").

Here is `significance.py` refusing an honestly underpowered sample, run for real against the
library rather than invented for this page — twelve closed trades (seven wins, five losses) at
the 120 bp taker fee:

```
turtle_breakout @ outside_allowance_taker (fee 120 bp per leg):
  closed trades n=12 pooled -> 4.66 effective (design effect 2.575, #427)
  payoff b=1.2500 -> break-even win rate 0.4444; observed 0.5833 -> edge 0.1389 points
  edge z=0.6034, one-sided p=0.2731; 95% one-sided lower bound on the edge: -0.2397
  smallest edge detectable at this n_eff: 0.5761 (80% power, alpha 5%)
  verdict: not distinguishable from zero at the 120 bp taker fee
```

An observed edge of 13.9 points looks encouraging until the n_eff correction is applied: twelve
pooled trades collapse to 4.66 effective observations (design effect 2.575 — herding again), the
95% one-sided lower bound on the edge is *negative* (-0.2397), and the sample could only detect an
edge of 57.6 points or larger at 80% power in the first place. `render_family` prints all of that
rather than the bare verdict, so the reader sees exactly why the answer is no, and the command
still exits 0 — the question was well-formed and the evidence answered it honestly.

## What is NOT here

This front door is surfacing, not new statistics. `keel research` assembles inputs, calls a
function in `keel/research/*`, and prints what comes back — the command layer is not permitted to
compute anything the module doesn't already compute, and every number this page or the CLI prints
traces back to one of the thirteen files above.

It is also not where a bespoke, one-off sweep belongs. keel's ad-hoc pre-registered research
drivers — parameter sweeps, ablations, factor studies, feasibility probes, each with a `.py`
driver committed beside the document that narrates it — live in `docs/experiments/`, not here.
`keel research` is the toolkit; `docs/experiments/` is the record of what the toolkit (and
sometimes a purpose-built script sitting on top of it) found when someone asked it a specific
question on a specific date. See [`docs/experiments/README.md`](experiments/README.md) for the
index and for why the drivers are committed rather than run-and-discarded.

## The 2026-09-30 pooled review (#427)

`docs/experiments/2026-09-30-pooled-review.py` is a standing, pre-registered event: at midnight
UTC on 2026-09-30, every deployment's forward trades (paper, live, and the paper-hourly evidence
profile) are pooled and reviewed. The pre-registration is the driver's own module docstring, not
a separate document — "PRE-REGISTERED BEFORE THE EVENT (this docstring is the pre-registration…)"
— and it is frozen: the pool definition, the dedup rule, the exclusions, all fixed before the
first forward trade closes.

It exists because #359 originally scheduled the review at a floor of n=100 pooled trades, and
#427 found that floor **dishonest as written**. Signals fire in herds — roughly eight assets the
same UTC day, ICC 0.212 — so 100 pooled trades are not 100 independent observations; they are
about 39 effective ones (the same `design_effect()`/`n_eff` correction `significance.py` and
`throughput.py` apply everywhere else). At 39 effective observations the review can only detect
an edge of about 20 points or larger. A pass/fail verdict written against a floor that ignores
that correction would be reporting confidence the sample doesn't have.

The correction of record — PR #503, and the corrected comment on discussion #359 — reframed the
event as **descriptive**: `keel/research/pooled_review.py` runs `significance.py`'s n_eff-corrected
math over the pooled forward trades and always prints the sentence #427 requires, generated at
the same n the measurement itself uses so the artifact never carries two different n_eff numbers
side by side:

> "at this n_eff (N effective of M pooled), this review can only see an edge of X points or
> larger (80% power, one-sided 5%)"

There is no pass/fail verdict on the edge anywhere in the rendered report — the only
verdict-shaped sentence in it is about power, not about whether the edge is real. A pool with
nothing counted (empty, or every trip a scratch) refuses outright rather than render a degenerate
report: `is_refused`/`descriptive_review` in `keel/research/pooled_review.py` produce a
`DescriptiveReview` whose `refusal` is a tuple of reasons instead of a report, and
`render_report` raises if asked to render one anyway — a refused review has no report to print.

That refusal is exactly where the standalone driver is the prior art any future
`keel research pooled-review` subcommand must deliberately break with, not the pattern to copy:
`docs/experiments/2026-09-30-pooled-review.py` prints its refusal to **stderr** and calls
`sys.exit(2)` when a pre-registered profile database isn't reachable. A front-door command must
not repeat that — under the rule stated above, a refusal belongs on stdout at exit 0, because
"nothing to review" is this command answering a well-formed question honestly, not the command
failing to run.

As of this writing `pooled_review.py` has no dedicated `keel research` subcommand either —
`keel research index --module pooled_review` names the same driver, and running the review
today still means running `docs/experiments/2026-09-30-pooled-review.py` directly, stderr/exit-2
refusal included. Before 2026-09-30 it runs the same machinery as a labelled preview, and it
says so.
