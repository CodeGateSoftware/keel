# PRD — Strategy-API expressiveness, learned from Jesse

**Date:** 2026-08-23 · **Status:** proposal, not accepted · **Source:** comparison against the
[Jesse](https://github.com/jesse-ai/jesse) framework's strategy API and its published example
strategies (`jesse-ai/example-strategies`).

---

## 0. The filter this whole document is written through

**keel is cost-bound, not signal-bound.** That is measured, not asserted:

- Round-trip friction on this venue is **~2.5% of notional** (1.2% taker per leg + 5bp slippage
  each way) — the same order of magnitude as the per-trade edge of everything ever measured.
- **No shipped rule family is net-positive** at that cost. Zero of 90 in the shipped-defaults
  intersection; zero of 82 in the `rsi_meanrev` grid.
- `turtle_breakout` is negative on **all 24 assets** measured. Seven assets showed gross PF > 1.0
  and **all seven died at the maker rate**, before the taker rate was reached.
- A 144-cell hourly sweep across six assets produced **8 winning cells out of 864 trials**, all in
  one asset, in a liquidity surge.

**Therefore: the lesson from Jesse is NOT its strategies.** Jesse ships DUAL_THRUST, KDJ,
MACD_EMA, SMACrossover, SimpleBollinger, RSI2, IFR2 and others that keel does not have. Adding
them is the known dead end — a rule negative on 24 assets is not fixed by a 25th signal, and
signal count has never been the constraint.

**The lesson is the API.** Jesse's `Strategy` interface can express things keel's `Rule` cannot,
and those specific expressive gaps are — independently — already keel's open issues. That
convergence is the finding.

---

## 1. What was examined

`jesse.trade/strategies` is Cloudflare-protected and could not be read. The analysis is instead
from the **source of record**: the `jesse-ai/example-strategies` repository (DUAL_THRUST, Donchian,
IFR2, KDJstrategy, MACD_EMA, MAGen, RSI2, SMACrossover, SimpleBollinger, TradingView_RSI,
TurtleRules) and the API surface those strategies exercise.

Reading the code rather than a marketing page is the better source anyway: what matters here is
what the framework lets a strategy *say*, not what its strategies claim to earn.

---

## 2. The API comparison

### What Jesse can express that keel cannot

| Jesse | keel today |
|---|---|
| `self.buy = qty, entry` — a **conditional entry at a chosen price** | `Setup(entry, stop, target)` is advisory; the executor places a **market IOC** and `backtest()` fills at the next bar's open |
| `update_position()` — a per-bar hook on an **open** position | no live equivalent; exits are a boolean `exit_signal()` |
| Pyramiding — `self.buy = ...` again inside `update_position` | a second entry is a separate tranche; no rule can request one |
| Partial exits — a list of `(qty, price)` take-profits | `scale_out()` exists but has **no caller**, pinned by a tripwire test |
| `on_increased_position(order)`, `on_stop_loss(order)` — **fill-event hooks** | no rule-level event hooks at all |
| `self.vars` — per-strategy mutable state across bars | `Rule.detect()` is a pure function of candles |
| `hyperparameters()` — a **declared search space** with min/max/default | parameters are constructor kwargs; the search space lives in ad-hoc sweep scripts |
| `should_cancel_entry()` | no equivalent |

### What keel does that Jesse does not — stated so this is a trade, not a wishlist

| keel | Jesse |
|---|---|
| 18 un-overridable rails run before **every** order | strategy-level risk only |
| `detect()` has **no account, balance or venue access** — a rule physically cannot size a position | `self.balance` is available inside the strategy |
| Compliance screening, attestation with provenance, `qabd` possession as an executable check | none |
| Fill model deliberately matches what the executor actually places | strategies assume their chosen price |
| Deflated Sharpe / PBO / trials budget as first-class discipline | optimisation is offered; the multiple-testing correction is the user's problem |

**keel's statelessness is a deliberate property, not a deficiency.** `detect()` being a pure
function of candles is what makes rules testable with no fixtures, and what guarantees a rule
cannot size its own position. Any expressiveness added must not trade that away wholesale.

---

## 3. The convergence — most of these gaps are already filed

This is the honest headline. The comparison did not reveal a missing roadmap; it **independently
re-derived the one keel already has**:

| Jesse capability | keel issue |
|---|---|
| conditional entry at a price | **#333** — "Route a rule's conditional entry as a genuine resting order (limit/stop)" |
| `update_position()` per-bar management | **#502** stage 3 — the live stop-management step |
| partial exits | **#502** — `scale_out`'s two prerequisites |
| pluggable strategies | **#447** — "Rules are not pluggable, though brokers are" |

An external framework arriving at the same four gaps is evidence those issues are correctly
prioritised. **No new issue is warranted for any of them.**

---

## 4. What IS genuinely new

### 4.1 A declared hyperparameter search space (the strongest finding)

Jesse's `hyperparameters()` returns the space itself:

```python
def hyperparameters(self):
    return [
        {'name': 'stop_loss_atr_rate', 'type': float, 'min': 0.1, 'max': 2.0, 'default': 2},
        {'name': 'up_length',          'type': int,   'min': 3,   'max': 30,  'default': 21},
        ...
    ]
```

keel already has the machinery this feeds:

- `research/deflate.py::expected_max_sharpe(n_trials)` — the Sharpe expected from the luckiest of
  N zero-skill trials.
- `research/independence.py` — "two rules that fire together are one rule counted twice,
  **consuming trials budget twice**".
- A **hand-maintained** trials ledger (`keel/research/ledger.py`, recorded through the CLI with
  explicit `DECISIONS`/`PROVENANCE`), plus source comments reasoning about whether a given choice
  "increments the trials budget (§73.12)".

**So `n_trials` — the input to keel's own overfitting correction — is currently a number a human
remembers to record.** Declaring the search space on the rule makes it *derivable*: the size of
the grid a sweep could explore is a property of the rule, not of the operator's diligence.

This is the one place Jesse's design is straightforwardly better for a discipline keel already
cares about more than Jesse does.

### 4.2 A warning worth encoding, not a feature

Jesse strategies routinely do `self.buy = qty, entry` and assume the fill. keel measured what that
assumption costs: correcting `pullback_continuation` to a market fill **dropped its gross profit
factor from 0.92 to 0.77** and doubled its trade count, because a market order takes the trades
the strategy meant to decline.

**Any Jesse strategy ported naively inherits an optimism keel has already priced.** That belongs in
the docs as a stated hazard, so the next person evaluating an external strategy knows to check the
fill model before the returns.

> **Documented (#529):**
> [`docs/experiments/2026-08-27-external-strategy-evaluation-hazard.md`](../../experiments/2026-08-27-external-strategy-evaluation-hazard.md)
> — the four questions (fill model, cost regime, sample size, capability) with their measured keel
> anchors and a pre-port checklist, cross-referenced from the proposal entry points (the
> keel-asset-scout skill and the proposer design's strategy-proposer scope).

---

## 5. Proposal

**Adopt one thing, document one thing, decline the rest.**

1. **Adopt:** a declared parameter space on `Rule` (§4.1), wired to the trials budget.
2. **Document:** the fill-model hazard when evaluating any externally-sourced strategy (§4.2).
3. **Decline, explicitly:** porting Jesse's signal families. Not because they are bad, but because
   signal count is not the binding constraint and importing them would consume review effort on
   the axis already measured closed.
4. **Do not re-file** #333 / #447 / #502 — the comparison validates them; it does not add to them.

### Non-goals

- Adopting Jesse's stateful `Strategy` base class wholesale. Its `self.vars` and `self.balance`
  access would give a rule the ability to size its own position, which is precisely the separation
  keel's architecture is built on.
- Adding pyramiding. It is expressible today as a second tranche, and rail 8 already governs
  averaging up.
- A hyperparameter *optimiser*. Declaring the space is cheap and improves an existing correction;
  running an optimiser against a cost-bound engine would manufacture exactly the overfitting the
  deflated-Sharpe machinery exists to detect.

---

## 6. Risks

- **The declared space becomes a licence to search it.** The point is to make the trials budget
  honest, not to encourage sweeps. Mitigation: the field is consumed by the *correction*, and any
  sweep still records to the ledger with its provenance.
- **A declared space that drifts from the real one.** If a sweep script explores values outside the
  declaration, the correction under-counts. Mitigation: the sweep should read the declaration
  rather than restate it.
- **Scope creep toward Jesse's model.** Each capability in §2 is individually reasonable and
  collectively a different architecture. The rails, the pure `detect()`, and the fill fidelity are
  the things not to trade.

## 7. Success criteria

- [ ] `n_trials` for any rule family is derivable from the rule itself, not from a human's memory.
- [ ] The fill-model hazard is documented where someone evaluating an external strategy will meet it.
- [ ] No new signal family is added as a result of this comparison.
- [ ] #333, #447 and #502 are unchanged — the comparison is recorded as corroboration, not new scope.
