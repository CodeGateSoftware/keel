# Trials-ledger backfill — reconstruction note

**Date:** 2026-07-20
**Produces:** `docs/experiments/trials-ledger.jsonl` rows 1–39
**Spec:** `docs/superpowers/specs/2026-07-20-trials-ledger-pbo-design.md` §4.6

Every backfilled row carries `series_missing: true`. They count toward `M` and toward MinBTL;
they are refused by the CSCV matrix by construction. This is not a limitation to fix later —
§78.4's warning already came true for them: **the sweeps that produced these rows destroyed the
per-bar series needed to score them.** The ledger exists so that this stops being true going
forward.

## Method and its honest limits

Counts are reconstructed from the committed experiment records, the project memory, and the
git history. **The scratchpad scripts that produced the walk-forward and rank-markets results
no longer exist**, so two of the eleven experiments below have a genuinely uncertain
configuration count.

**Tie-break rule, applied throughout: where the count is ambiguous, over-count.** §78.7 is
asymmetric about this. *"Hiding trials will lead to an underestimation of the overfit"*, while
the opposite abuse — *"adding trials that are doomed to fail in order to make one particular
model configuration succeed"* — requires deliberately padding with known losers. Over-counting
genuine uncertainty errs toward the first concern and does not commit the second.

⚠️ **This reconstruction is not authoritative in the way a contemporaneous record would be.**
`M = 39` should be read as a floor with an uncertainty band, not a measurement. Its purpose is
to stop `M` being *silently zero*, which is what it was before.

## The rows

| # | experiment | rows | kind | provenance | source | count certain? |
|---|---|---:|---|---|---|---|
| 1 | 3 dip-buyer rules × 3 assets, retired | 9 | `rule_retirement` | `a_priori` | memory; `keel.db` rules 1–9 (backup `rules_backup.json`) | ✅ yes — nine seeded rows |
| 2 | CTS-score bucket diagnostic | 1 | `ablation` | `fitted` | memory (confluence-gate refutation) | ✅ yes |
| 3 | min-CTS confluence gate, refuted before build | 1 | `ablation` | `fitted` | memory | ✅ yes |
| 4 | Turtle on HOURLY bars w/ scaled ADX (zero trades) | 1 | `sweep_node` | `a_priori` | memory; issue #89 | ✅ yes |
| 5 | Turtle rebuilt DAILY (20/10) | 1 | `sweep_node` | `a_priori` | memory; PR #90 | ✅ yes |
| 6 | rank-markets / ER Donchian period diagnostic | 7 | `sweep_node` | `fitted` | memory: *"profitable at 6/7 Donchian entry periods"* | ⚠️ **7 is from "6/7 periods"; the specific periods are not recorded** |
| 7 | ETH keep/drop decision | 1 | `asset_prune` | `fitted` | memory; §58.4 | ✅ yes |
| 8 | entry-period walk-forward (Phase A) | 8 | `sweep_node` | `fitted` | memory: candidates ≥{20,40,55} + an adaptive variant | ⚠️ **UNCERTAIN — see below** |
| 9 | S1 profitable-trade filter, on/off | 2 | `ablation` | `a_priori` | PR #94; §54.14 | ✅ yes |
| 10 | S1(20/10 filtered) + S2(55/20) ensemble | 1 | `sweep_node` | `fitted` | memory; DB reseed + restore | ✅ yes |
| 11 | ADX ablation: thresholds 25 / −1 / 200 | 3 | `ablation` | `fitted` | `2026-07-20-adx-ablation-and-random-entry-control.md` | ✅ yes — all three documented |
| 12 | random-entry control arm (30 seeds) | 1 | `ablation` | `a_priori` | same doc; §58.11 | ✅ counted as ONE (see below) |
| 13 | `min_trades` 100 → 30 | 1 | `threshold_nudge` | `fitted` | commit `943a099` | ✅ yes |
| 14 | `min_trades` 30 → 100 (reverted) | 1 | `threshold_nudge` | `a_priori` | commit `943a099`; §73.3 | ✅ yes |
| 15 | `min_win_rate` 0.55 → 0.30 for trend-follow | 1 | `threshold_nudge` | `a_priori` | §25.5 | ✅ yes |
| | **total** | **39** | | | | |

### Row 8 — the walk-forward, the one genuinely uncertain count

Memory records the outcome (*"every entry lookback LONGER than 20 beat 20 OOS; fixed-40 =
best/most robust, OOS +1.08R vs fixed-20 +0.67R, fixed-55 +0.82R; adaptivity added nothing"*)
but not the candidate list. The floor is 4 — three fixed periods plus one adaptive variant are
named explicitly. The phrase *"every entry lookback longer than 20"* implies more than the two
longer ones named.

**Chosen: 8** (7 fixed periods, matching row 6's grid, plus 1 adaptive variant), the high end
of the plausible range. Per the tie-break rule.

### Row 12 — why 30 seeds is ONE trial, not 30

§78.7 limitation 2: *"the columns of matrix M should be the final outcome of each guided
search… and not the intermediate steps."* The 30 seeds are a Monte Carlo estimate of a single
null distribution, not 30 candidate configurations anyone chose between. Counting them as 30
would inflate `M` with pure sampling noise. This is the one place the over-count rule is
deliberately **not** applied, because the seeds are not decisions.

### Not counted

- **`exit_lookback = 20`** was adopted as part of the 40/20 pair, never independently tested
  (§79.6 flags this as an untested inheritance). It is not a separate trial because no
  comparison was ever run — it is folded into row 8's selected configuration.
- **`keel simulate` validation runs.** Re-running the shipped configuration to produce a report
  is a diagnostic, not a selection, and the CLI now records those with
  `decision: diagnostic_only` automatically (spec §4.4).

## What this means, immediately

`M = 39`, of which `N_decisions = 29` (ten rows are `diagnostic_only`). This is far below the `M ≈ 336` reconstruction the
project memory carried from §78.2 — that figure counted the *full grid of a queued sweep that
was never run*, not trials actually spent. The `N̂ = ρ̂ + (1−ρ̂)·M` correction and MinBTL
reporting (§78.13 items 1–3) are **not built yet**, so no MinBTL number should be quoted from
this ledger until they are.
