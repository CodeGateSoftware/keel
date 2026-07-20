# Allowlist admission screen — first run rejects our own allowlist

**Date:** 2026-07-20
**KB basis:** §28.4 (haram-sector screen), §65.5/§67.2 (`'ayn` vs `dayn`), §78.12 rule 3 / §73.3
(more assets on an unchanged rule = pure `T` gain, zero trials)
**Status:** gate built and run. **No asset attested, no allowlist changed.**

## Why this exists

Every frequency fix measured this session died the same way — it added *correlated* trades by
reshuffling the same three assets (ranking §75.1, per-asset frequency §79.1, the S1+S2 ensemble,
the horizon ladder). §78.12 rule 3 names the one frequency change that is genuinely free: **more
assets on the same unchanged rule**, which §73.3's inheritance rule makes zero-trials because
nothing is fitted and nothing is selected.

The arithmetic is the argument. At ~2.6 trades/year/asset:

| allowlist size | trades/year | 100 trades in |
|---:|---:|---:|
| 3 (today) | ~6 | **~16 years** |
| 10 | ~26 | ~4 years |

No parameter available to us moves that number. This does.

But admitting assets needs a gate, and the allowlist has never had one — `config.yaml` lists three
codes and `guards.py` rail 1 enforces them mechanically. Nothing decided what was allowed *in*.

## What the gate does

`keel/compliance/screen.py`, exposed as `keel assets screen|attest|list`. §28.4 is explicit that
this is a **curation gate, not a per-trade rail** — sector and backing are *"a listing criterion,
checked once when curating the allowlist"* — so nothing here runs on the hot path.

**The design turns on what is knowable:**

- **Market facts are COMPUTED** — history depth, median daily volume, settlement quotability. No
  judgement, recomputed freely from data we already hold.
- **Shariah classifications are ATTESTED, never inferred.** Whether a token's purpose is a haram
  sector, whether it is `'ayn` (an owned thing) or `dayn` (a claim on an issuer), whether holding
  it earns a riba-like return — these are facts about the world plus scholarship. Candles cannot
  answer them and the code does not pretend otherwise.

**Absent attestation fails closed.** An unattested asset is not "probably fine"; it is unknown, and
unknown is a rejection. This mirrors `broker_subscriptions`, where an un-attested venue is
`suspect` and blocks live buys rather than defaulting to a guess.

The v6 migration deliberately backfills **nothing** — seeding attestations for BTC/ETH/PAXG because
they happen to be in the current allowlist would fabricate exactly the attestation the screen
exists to demand, for the three assets the project is least likely to keep questioning.

## First run — 0 of 3 admitted

```
REJECT  BTC   bars=1828  median_daily_volume=573,798,366
    ✗ attestation: MISSING
REJECT  ETH   bars=1828  median_daily_volume=337,806,992
    ✗ attestation: MISSING
REJECT  PAXG  bars=439   median_daily_volume=1,224,122
    ✗ history: 439 daily bars < 1460 required
    ✗ attestation: MISSING
```

**BTC and ETH fail on paperwork only** — their market facts are comfortable (5 years of daily bars,
hundreds of millions in median daily volume). They need a recorded classification with a source,
which is a human's job, not this agent's.

⚠️ **PAXG fails on substance.** It carries **439 daily bars — about 1.2 years — against a 4-year
floor**, and it has been in the live allowlist, in every backtest, in the engine validation, and in
both PBO runs the whole time. The exit-lookback test already found it contributes *no* information
to that question because its exit channel never binds. It is now measurably below the history bar
this project would apply to any *new* candidate.

**That asymmetry is the finding worth keeping:** an asset admitted before a gate existed was held
to a standard no new candidate would clear. The gate did not change PAXG; it made a pre-existing
fact legible.

## Deliberately not done

**No asset was attested.** Recording BTC as `sector=payments, backing=native` would take ten
seconds and would defeat the gate on its first use — the point is that a human establishes the
classification against a named source. The screen is built; the attestations are the user's.

**No allowlist changed, and no candidate was proposed.** Expanding the universe is the next step
and needs (a) attestations for what we already hold, (b) a candidate list screened on the same
terms. Per §5's asymmetry, an LLM may *propose* candidates but the deterministic screen decides —
and admission increases activity, so it goes through the gate, never around it.

## Open question this raises

If PAXG cannot clear a 4-year history bar, the honest options are to hold it to the same standard
as any new candidate (drop it, at the cost of the diversifier §83.10 says it uniquely provides), or
to record an explicit, reasoned exception. **The one thing not to do is leave it silently exempt
because it was already there.**
