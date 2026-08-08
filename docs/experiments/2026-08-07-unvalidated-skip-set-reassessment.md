# Re-assessing "SOL/LTC/LINK are the unvalidated skip set"

**Date:** 2026-08-07
**Status:** documentation correction only. **No allowlist changed, no asset attested, no rule
promoted, no database written.**
**Trigger:** a standing re-assessment of whether that claim — carried in a live-money config —
is always valid.

## The claim under test

`config.live-sandbox.yaml`, directly above the `allowlist:` that governs the real-money sandbox:

```yaml
# The 5-trend Turtle set (same as the paper-forward + the walk-forward/PBO analysis).
# SOL/LTC/LINK are deliberately excluded -- they're the unvalidated skip set.
allowlist: [BTC, ETH, PAXG, ADA, XLM]
```

**Verdict: not valid, and not valid at any point since it was written.** The exclusion is
*conservative in direction* — fewer live assets is the safe error — but every stated reason for
it is false, and the label misdescribes both which assets were validated and what mechanism does
the excluding. A comment sitting above a live allowlist is read as the reason for that
allowlist; this one would mislead anyone who trusted it.

## Provenance: there is no decision to find

The phrase "unvalidated skip set" appears **nowhere else in the repository**. The file entered
git fully formed on 2026-08-03 in `183dcdb` ("chore: track the deployment's operator configs"),
a *backup* commit — before that it lived only in `~/keel` on one laptop. No commit, PR, design
doc, or experiment note ever introduced or justified the exclusion. It is an inherited
assertion, not a recorded ruling.

## Four gates, and the comment names the wrong one

keel enforces four independent gates. Three are asset-level, one is rule-level:

| # | Gate | Object | Enforced by |
|---|---|---|---|
| 1 | Compliance / attestation | asset | `screen.py::screen_asset` — `attestation=None` fails closed |
| 2 | Mechanical screen | asset | same fn — history ≥1460 bars, liquidity, settlement, spot-only |
| 3 | Allowlist | asset | `guards.py` rail 1, every intent — **consults neither gate 1 nor 2** |
| 4 | Statistical evidence | **rule** | `agent.py` filters on `rules.status`; `promotion.py::can_promote` |

The comment attaches a **gate-4 (rule-level)** reason to a **gate-3 (asset-level)** control.
Rail 1 is a flat membership test against whatever list the loaded config contains; it does not
and cannot know whether anything was validated. What actually makes SOL/LTC/LINK inert is the
absence of a `live`-status `rules` row — a `keel rules seed --status` choice, not the allowlist.

## Gates 1 and 2: SOL/LTC/LINK passed, and have since 2026-07-23

`asset_attestations` in `keel.db` holds all eight assets. SOL, XLM, LTC and ADA were attested at
the **same second** (2026-07-23 12:15:12), same attestor, same source (Mufti Faraz Adam, "Is
Crypto Halal?"), all `pays_yield=0`, "bare unstaked spot only". LINK followed 2.5 minutes later.
No `screen_exceptions` row exists for any of the five.

Screening copies of the databases, all three ADMIT: 1827 daily bars each, median volumes
$108M / $15.1M / $21.2M against a $1M floor.

There is **no negative result anywhere** for SOL, LTC or LINK. They were not tested and rejected.

The comparison that settles it:

| asset | daily bars | attested | screen verdict | in live allowlist |
|---|---:|:---:|:---:|:---:|
| ADA / XLM | 1833 | 2026-07-23 | ADMIT | yes |
| SOL / LTC / LINK | 1827 | 2026-07-23 | ADMIT | **no** |
| PAXG | **456** | 2026-07-23 | admit **only via waiver** | yes |

The excluded assets carry *more* history than an included one. PAXG is on the list solely
through a documented `screen_exceptions` history waiver.

## The parenthetical is false: no walk-forward/PBO ever covered ADA or XLM

Every walk-forward, PBO/CSCV and ablation run recorded in `docs/experiments/` — the first PBO
run, exit-lookback, ADX ablation, horizon independence, Yang-Zhang, and all four
`trials-ledger.jsonl` sessions — stops at **BTC/ETH/PAXG**. Nothing dated after 2026-07-20 runs
any such analysis, and the 8-asset expansion landed 2026-07-23.

So ADA and XLM have been through that machinery exactly as much as SOL, LTC and LINK have:
**not at all.** The walk-forward that does exist tuned a *parameter* — `turtle_breakout.py`
still annotates `entry_lookback: int = 40  # walk-forward OOS default (was 20)` — it never
validated a set of assets.

## Gate 4: the live set didn't pass it either

`keel-live.db` rules 1–5 (BTC/ETH/PAXG/ADA/XLM) were all created in the same second on
2026-07-24 13:42:24 at `status = live` with **`promoted_at IS NULL`** — seeded directly through
the bypass `rules seed` warns about in its own output:

> ⚠️ seeded at LIVE status, bypassing the promotion gate. This is for the supervised live-order
> test only … **Do not leave live-seeded rules in place afterwards.**

They have been in place 14 days.

In `keel.db`, XLM (22) and ADA (24) do carry a `promoted_at`, while SOL (21), LTC (23) and LINK
(25) remain `candidate`. But `trade_outcomes` and `signals` are **empty for every product** and
`min_trades` is 100 — so that promotion cannot have been earned on evidence either. It is the
same documented `--force` bypass. `rules seed` performs no backtest at all: `--status` is an
operator-supplied string written straight through by `insert_rule`.

**The live 5-set is not the validated set. It is the gate-bypassed set.** SOL/LTC/LINK are the
three that were *not* fast-tracked.

> **Note on the empty `backtests` table.** It proves nothing either way. The table is declared
> at `keel/data/db.py:140` and indexed, but there is no `INSERT INTO backtests` anywhere in the
> repo — `rules backtest` / `rules promote` compute in memory and never persist. An empty table
> is the expected state even where backtests *have* run. This corrected an earlier reading in
> this same investigation.

## What survives

Exactly one narrow reading is true: **SOL/LTC/LINK's turtle rules have never been backtested.**
That is a real and sufficient reason to keep them off a live allowlist. It is also true of the
five assets that *are* on it, which is what makes "the unvalidated skip set" the wrong name for
them.

## When does the claim stop holding?

It already has, partially — the compliance half expired on 2026-07-23 when all eight were
attested. The remaining half expires the moment `keel rules backtest 21 / 23 / 25` is run. Since
nothing in the codebase re-checks this comment, it will stay wrong until edited by hand.

## Action taken

The comment in `config.live-sandbox.yaml` was rewritten to state the accurate reason. **The
allowlist itself is unchanged** — correcting a false rationale is not grounds to act on the
decision it described, and admission is the operator's call through the deterministic gate.

## Open items for the operator

1. **Deployment drift.** Only the repo copy was edited. `~/keel/config.live-sandbox.yaml` still
   carries the old comment; sync it.
2. **`keel-live.db` carries almost no compliance state.** Screening the live allowlist against
   it, **0 of 5 admit** — BTC/ETH/ADA/XLM reject on missing attestation, PAXG on attestation
   *and* history, because the attestations and the PAXG waiver exist only in `keel.db`.
   Ironically **SOL is the only asset that DB admits**, having been attested there 2026-08-07
   20:29:43. Operationally harmless (rail 1 reads `config.allowlist`, not the DB), but anyone
   running `keel assets screen` against the live DB would conclude the opposite of the comment.
3. **Live-seeded rules left in place** 14 days, against `rules seed`'s own instruction.
4. **Stale candles.** `keel fetch` follows the allowlist, so SOL/LTC/LINK daily bars in
   `keel.db` end 2026-07-31 vs 2026-08-06 for the allowlisted set. A backtest run today would
   be six days stale — fetch first.
5. **Two open attestation questions** surfaced this run and not yet answered: LINK's staking
   floor is funded from emissions rather than user fees, and LTC has carried **MWEB**, an
   opt-in confidential-transaction layer, since May 2022 — a narrower form of the ZEC privacy
   question. Neither is a defect; both are questions a human should settle before treating the
   2026-07-23 attestations as closed.
