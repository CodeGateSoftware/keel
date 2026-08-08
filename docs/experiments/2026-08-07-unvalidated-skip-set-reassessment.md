# Re-assessing "SOL/LTC/LINK are the unvalidated skip set"

**Date:** 2026-08-07
**Status:** documentation correction + one measurement. **No allowlist changed, no asset
attested, no rule promoted, no production database written** (backtests ran against a copy).
**Trigger:** a standing re-assessment of whether that claim — carried in a live-money config —
is always valid.

## The claim under test

`config.live-sandbox.yaml`, directly above the `allowlist:` that governs the real-money sandbox:

```yaml
# The 5-trend Turtle set (same as the paper-forward + the walk-forward/PBO analysis).
# SOL/LTC/LINK are deliberately excluded -- they're the unvalidated skip set.
allowlist: [BTC, ETH, PAXG, ADA, XLM]
```

**Verdict: the decision is right; the stated reasons were mostly wrong; and "always valid" is
the wrong frame for it.** One half of the parenthetical is accurate, the other half describes
analysis that does not exist, the "unvalidated" label misdescribes which assets were validated,
and the whole thing named a gate that does not do the excluding. Then — on a check nobody had
run until this re-assessment — the *conclusion* turned out to be supported by evidence the
comment never cited.

## Provenance: no decision record exists

The phrase "unvalidated skip set" appears **nowhere else in the repository**. The file entered
git fully formed on 2026-08-03 in `183dcdb` ("chore: track the deployment's operator configs"),
a *backup* commit; `36c931a` had gitignored it on 2026-07-24. No commit, PR, or design doc ever
introduced or justified the exclusion.

That said, a coherent origin story does exist and the comment half-names it — see below.

## Four gates, and the comment names the wrong one

keel enforces four independent gates. Three are asset-level, one is rule-level:

| # | Gate | Object | Enforced by |
|---|---|---|---|
| 1 | Compliance / attestation | asset | `screen.py::screen_asset` — `attestation=None` fails closed |
| 2 | Mechanical screen | asset | same fn — history ≥1460 bars, liquidity, settlement, spot-only |
| 3 | Allowlist | asset | `guards.py:369` rail 1 — **consults neither gate 1 nor 2** |
| 4 | Statistical evidence | **rule** | `agent.py` filters on `rules.status`; `promotion.py::can_promote` |

The comment attaches a **gate-4 (rule-level)** reason to a **gate-3 (asset-level)** control.
Rail 1 is a flat membership test against whatever list the loaded config contains. What actually
makes SOL/LTC/LINK inert is the absence of a `live`-status `rules` row.

## Gates 1 and 2: SOL/LTC/LINK passed, and have since 2026-07-23

SOL, XLM, LTC and ADA were attested at the **same second** (2026-07-23 12:15:12, `attested_at`
1784823312), same attestor, `pays_yield=0`, "bare unstaked spot only". LINK followed 160s later
under the same governing source (KB source-86) — though LINK's attestation text is materially
more qualified, classifying it as a **utility token** (ERC-20, `native` chosen only because the
backing enum lacks a `utility` slot). That nuance is worth preserving; "same source" flattens it.

Screening a copy of `keel.db`, all three ADMIT — 1827 daily bars each, median notionals
$108,273,790 / $15,129,648 / $21,156,873 against a $1M floor.

| asset | daily bars | attested | screen verdict | in live allowlist |
|---|---:|:---:|:---:|:---:|
| ADA / XLM | 1833 | 2026-07-23 | ADMIT | yes |
| SOL / LTC / LINK | 1827 | 2026-07-23 | ADMIT | **no** |
| PAXG | **456** | 2026-07-23 | admit **only via waiver** | yes |

The excluded assets carry *more* history than an included one. PAXG is on the list solely via
the single `screen_exceptions` row in the table.

**There is no compliance asymmetry.** Whatever separates these two groups, it is not gates 1–2.

## The parenthetical: half true, half false

**True half — "same as the paper-forward."** The `paper`-status rules in `keel.db` are exactly
10 (BTC), 11 (ETH), 12 (PAXG), 22 (XLM), 24 (ADA) — precisely the live 5. `paper.py:15` loads
`repo.get_rules("paper")`. So at the *rule* level this is an accurate description, and it is a
perfectly coherent origin story for the exclusion: the sandbox mirrored the paper-forward's
promoted rule set. (Note `config.paperforward.yaml`'s *allowlist* is all eight assets — the
match is between rule sets, not config files.)

**False half — "the walk-forward/PBO analysis."** Every walk-forward, PBO/CSCV and ablation run
in `docs/experiments/` stops at **BTC/ETH/PAXG**. The `trials-ledger.jsonl` symbol census across
69 rows and 4 sessions: BTC 32, ETH 33, PAXG 32, and **0** for each of ADA, XLM, SOL, LTC, LINK.
ADA and XLM have been through that machinery exactly as much as SOL/LTC/LINK have: not at all.
The walk-forward that does exist tuned a *parameter* — `turtle_breakout.py:113`:

```python
entry_lookback: int = 40,  # Donchian-high entry (days); walk-forward OOS default (was 20)
```

## Gate 4: the live set didn't pass it either

`keel-live.db` rules 1–5 (BTC/ETH/PAXG/ADA/XLM) were created in the same second on 2026-07-24
13:42:24 at `status = live` with **`promoted_at IS NULL`** — seeded through the bypass
`rules seed` warns about in its own output:

> ⚠️ seeded at LIVE status, bypassing the promotion gate. This is for the supervised live-order
> test only … **Do not leave live-seeded rules in place afterwards.**

They have been in place 14 days. `rules seed` performs no backtest: `--status` is an
operator-supplied string written straight through by `insert_rule` (`commands/rules.py:351`).

In `keel.db`, XLM (22) and ADA (24) *do* carry a `promoted_at`, while SOL (21), LTC (23) and
LINK (25) remain `candidate`. That promotion cannot have been earned: `rules promote` gates on a
candle-driven backtest (`commands/rules.py:180`), and XLM/ADA return **n_trades = 8 and 6**
against `min_trades = 100`. It is inference, not record, that `--force` was used — the `journal`
table is empty and `log_event` writes to logs, not the DB — but the gate demonstrably could not
have passed.

> **Two corrections this investigation made to itself.**
> **(a)** The empty `backtests` table proves nothing. It is declared at `keel/data/db.py:140`
> and indexed, but there is no `INSERT INTO backtests` anywhere — `rules backtest` / `rules
> promote` compute in memory and never persist. An empty table is expected even where backtests
> *have* run. Consequently "SOL/LTC/LINK have never been backtested" is **unprovable** and is no
> longer claimed.
> **(b)** "Nothing has traded" was **false**. `trade_outcomes` is empty in both DBs, but
> `keel-live.db` holds `signals=1`, a **filled live order** (BTC-USD BUY 0.000778 @ 64267.30,
> `rule_id=6`) and an **open position**, both 2026-08-07 01:12:50, from the `dca` rule 6 —
> which *is* properly promoted. This deployment is not inert and does carry exposure.

## The measurement nobody had run

The original comment guessed. Running `keel rules backtest` — against a **copy** of `keel.db`,
never the original — settles the direction in three commands:

| rule | asset | n_trades | win rate | expectancy | profit factor | max DD |
|---|---|---:|---:|---:|---:|---:|
| 21 | SOL | 13 | 23.08% | **−12.41** | 0.259 | 161.3 |
| 23 | LTC | 5 | 0.00% | **−13.09** | 0.000 | 65.5 |
| 25 | LINK | 15 | 20.00% | **−0.665** | 0.630 | 19.4 |
| 22 | XLM | 8 | 37.50% | +0.056 | 11.44 | 0.04 |
| 24 | ADA | 6 | 50.00% | +0.121 | 5.52 | 0.10 |

All three excluded assets are negative-expectancy; LTC won none of five trades. So the earlier
draft of this document was wrong to say "there is no negative result anywhere for SOL, LTC or
LINK" — there is one, and it was ten seconds away in data already sitting in `keel.db`.

**Read it with the sample sizes in view.** n = 5–15 against `min_trades = 100`, in-sample, one
window, no walk-forward, and on candles six days stale for the excluded three. By this project's
own standards (`2026-07-20-first-pbo-run.md` on why high PBO with a flat slope is the *good*
shape; §79.13 on 47 of 55 assets failing `t = 1.65`) this is a cheap directional check, not
validation. It cannot promote anything and should not. **None of the five clears the gate.**

What it does do is remove the symmetry argument: the two groups are not indistinguishable on
evidence, and the difference runs the way the original comment assumed.

## What survives

- The exclusion of SOL/LTC/LINK is **correct**, and now for a stated, reproducible reason.
- The label "unvalidated skip set" is still **wrong**, because it implies the other five were
  validated. None of the five was. All five sit far below the promotion floor.
- "Same as the paper-forward" was **right**; "the walk-forward/PBO analysis" was **invented**.
- The exclusion is not, and never was, a compliance judgment.

## When does the claim stop holding?

The compliance half expired on 2026-07-23 when all eight assets were attested. The "unvalidated"
half was never accurate as a *distinguishing* claim. What now stands in for it — adverse
backtest expectancy — is itself provisional: it rests on 5–15 trades and would want re-running
after a `keel fetch`, and properly a walk-forward before anyone treats it as settled.

## Action taken

The comment in `config.live-sandbox.yaml` was rewritten to state the accurate reasons and to
carry the measured numbers. **The allowlist itself is unchanged** — the measurement supports the
status quo, and admission is the operator's call through the deterministic gate regardless.

## Open items for the operator

1. **Deployment drift.** Only the repo copy was edited; `~/keel/config.live-sandbox.yaml` is
   still byte-identical to the old version. Sync it.
2. **`keel-live.db` carries almost no compliance state.** Screening the live allowlist against a
   copy, **0 of 5 admit** — BTC/ETH/ADA/XLM reject on missing attestation, PAXG on attestation
   *and* history, because the attestations and the PAXG waiver exist only in `keel.db`.
   Ironically **SOL is the only asset that DB admits** (attested there 2026-08-07 20:29:43).
   Operationally harmless — rail 1 reads `config.allowlist`, not the DB — but anyone running
   `keel assets screen` against the live DB gets the opposite of the comment's picture.
3. **Live-seeded rules left in place 14 days**, against `rules seed`'s own instruction.
4. **Stale candles.** `keel fetch` follows the allowlist, so SOL/LTC/LINK daily bars in `keel.db`
   end 2026-07-31 vs 2026-08-06. Re-run the backtests above after a fetch before relying on them.
5. **Two open attestation questions**, neither a defect: LINK's staking floor is funded from
   emissions rather than user fees, and LTC has carried **MWEB** — opt-in confidential
   transactions — since May 2022, a narrower form of the ZEC privacy question.
