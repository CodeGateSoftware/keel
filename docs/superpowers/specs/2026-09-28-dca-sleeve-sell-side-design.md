# The DCA sleeve's sell side: trims, rebalancing, reverse DCA, protective exits, rotation: design

**Date:** 2026-09-28 · **Issue:** #857 · **Status:** DRAFT, for the operator's review. No code
until the recommendations in §10 are accepted or amended. §12 lists the decisions the review has
to make, each with a default.

## 1. Purpose and non-goals

**Question.** Five requests ask keel to *sell* from the DCA sleeve, each for a different reason:
take profit, rebalance, distribute cash, protect against a structural break, rotate out of
losers. They all sell the same holdings through the same executor under the same rails, so they
need one architecture before any of them gets an implementation. This document is that
architecture, plus a per-feature verdict: **build**, **build as preview-only**, or **don't build
(yet)**, with the evidence each verdict rests on.

**Non-goals.**
- **No implementation.** Nothing here changes a rail, a rule or a config file.
- **No new measurement.** Research is frozen (2026-09-27). Where a feature would need a backtest
  to justify itself, this document says what the pre-registration would have to state and which
  trial budget it would draw on. It runs nothing.
- **No tax advice, and no tax law.** §8 states what keel can compute from its own ledger and
  stops there.
- **Not a fatwa.** Where the fiqh sources in the knowledge base speak to a sale shape, §2.6
  cites them; where they are silent, it says "not stated", as `docs/fiqh-basis.md` requires.

## 2. What the repository already says

The requests arrive as hypotheses. Several are already measured, and the code already answers
some of the design questions. This section is the evidence the rest of the document engages
with; nothing below is assumed.

### 2.1 #831: accumulation policy after fees

[`docs/experiments/2026-09-27-accumulation-policy.md`](../../experiments/2026-09-27-accumulation-policy.md),
pre-registered as [`2026-09-27-accumulation-policy-design.md`](2026-09-27-accumulation-policy-design.md)
(#834), tested four arms on identical $500 monthly deposits: **A** static DCA, **B** bounded
value averaging, **C** deposits steered to underweights by shortfall, **D** DCA plus selective
upper-band trimming with band `max(0.15·w, 0.015)` and proceeds redeployed to underweights.

Under rule D6 (`P(ΔSortino > 0) ≥ 0.95` and median Δmax-drawdown not worse, at both 20- and
60-day bootstrap blocks), **no arm beats static DCA after fees**, in either run or fee model. No
arm reaches even P = 0.62. Band trimming (D) finished the one historical 5-year path **+13.5%**
ahead of A and improved Sortino in only **36–43%** of 2,000 resampled paths: the bootstrap took
the lead back. Steering (C) was "effectively A". On the short run that includes PAXG, D was the
weakest alternative, because PAXG's slippage is about 100 bp per leg.

**Requests 1 (weight-drift trims) and 2 (steering and band rebalancing) are therefore largely
tested hypotheses, and the test said no.** The one variant of request 1 that #831 did *not* test
is a profit-take keyed on gain over average entry rather than on weight drift (§4). The record's
own §6 says what a re-test would have to look like: pre-registered on **drawdown**, not Sortino,
and **counted against the 3 trials accumulation policy has already spent**.

### 2.2 Fees, and which rate each computation uses

| Where | Taker | Maker | Source |
|---|---|---|---|
| Live account, Coinbase Advanced Trade, Intro tier, measured 2026-09-27 | **0.9%** | 0.5% | `config.yaml` fees comment (#836) |
| Sim and backtest default | **1.2%** | 0.6% | `config.yaml` `fees:`, `backtest.TAKER_FEE_PCT` |
| The request text | 1.20% | | |

The request's 1.20% is the **sim** figure. The operator chose to stay on market orders, so the
live rate is the taker rate, 0.9%. This design uses:

- **for a live preview or a live gate** (trim net-of-fee test, reverse-DCA sizing): the venue's
  own previewed commission from `broker.preview_order`, exactly as the executor already records
  it in `orders.fee`; if the preview carries none, `config.fees.taker_pct` (1.2%, conservative)
  is the fallback. **0.9% is never hardcoded**: the tier can change, and the preview is the fact;
- **for any backtest**: 1.2% headline with a 0.9% sensitivity row, so the record stays
  comparable with every record in `docs/experiments/`.

**Every sell pays the fee.** There is no sell-side allowance anywhere. **Rail 14 is a monthly
BUY cap only** (#836 retitled it; `guards.py` rail 14 is under `if is_buy`). It does not touch a
sell. It *does* touch the redeploy leg of a rebalance or a rotation, which is a BUY (§2.5).

A round trip (trim, then redeploy or rebuy) costs two legs: at live rates about 1.8% plus two
slippages; at sim rates 2.4%. #831's D arm paid $462 of buy fees and $468 of sell fees over five
years to end ahead on one path and behind on most.

### 2.3 What the sleeve is, and what protects it

- DCA is a **distinct order class**: a market buy with `stop=0` sentinel, `no_stop=True`,
  `Dca.exit_signal` hardcoded `False`, exempt from rails 4 (#842), 8, 11 and 16, bound by rails
  1, 2, 3, 5, 6 (until #853 lands), 12, 13, 14, 17, 18, 19, 20, 22
  (`keel/strategy/rules/dca.py`, `keel/execution/guards.py`).
- **Every DCA tranche is unbracketed and has no stop, by design.** `reconcile_unbracketed_positions`
  skips them silently for exactly that reason, and #811's proposed `position.unprotected` finding
  excludes them (`initial_stop` absent).
- **PAXG tranche 3** (turtle, opened 2026-08-25, no resting bracket, owning rule demoted to paper)
  is held without a stop **on purpose**, decided 2026-09-22. The gap is #811, whose two doctor
  findings (`position.unmanaged`, `position.unprotected`) are **not yet implemented**:
  `keel/commands/doctor.py` has no such finding today.
- **#799** is a different defect: a filled entry whose bracket preview threw was never recorded
  as a position, so nothing could ever exit it. Its proposal 2, "record the position first, and
  let a bracket failure downgrade to *open, unbracketed*", is a prerequisite for any monitor
  that reads the `positions` ledger (§7).
- Live rails as tracked in `config.live-sandbox.yaml`: `max_exposure_usd` 400,
  `max_per_order_usd` 200, `max_per_day_usd` 200, `max_per_asset_pct` 0.75 ($300). The DCA book
  was 7 rules and about $213 at cost on 2026-09-27 (#842), BTC $151.81 at cost across three $50
  buys (#819, #853).

### 2.4 How a sell happens today

There is exactly one sell pipeline and three callers:

1. `agent._handle_exits`: asks the **one owning rule** recorded in
   `agent_state["position_rule:<product>"]` for `exit_signal(held, candles)`; on `True` it builds
   an `Action.EXIT` `Signal` with `side=SELL` and hands it to `executor.execute`, which sells the
   **whole** held quantity (`_build_intent`, reconstructed from the orders ledger, then clamped to
   what the venue reports holding, #667). On fill, `streak.book_exit` closes tranches **FIFO** and
   writes `trade_outcomes` with `is_dca` derived from the owning rule.
2. `executor.scale_out` (#502): sells a **fraction** and resizes the bracket. "No policy here
   drives it yet: deciding WHEN to take half off is rule-side work this module does not do"
   (`keel/strategy/exit_policy.py`).
3. Protective brackets and stop rolls: `place_bracket`, `_roll_stop`, ratchet-only under rail 9.

Consequences for this design:

- **`Rule` cannot express a partial sell.** `Setup.direction` is pinned to `"long"`,
  `strategy.engine` builds every entry with `side=BUY` unconditionally, and `exit_signal` is a
  boolean meaning "sell all". A rule kind whose *output* is "sell $100 of BTC" needs a new hook
  (§3.2).
- **Ownership is single.** `position_rule:<product>` names one rule. A second live rule kind on
  BTC-USD is never consulted by `_handle_exits`. For the DCA sleeve the owner is `dca`, whose
  exit is hardcoded `False`, so today **no live path can sell a DCA tranche at all**. That is the
  gap the five requests are really about.
- **Rail 2 gates sells too.** `per_order_cap` has no `is_buy` guard. The live cap is $200; BTC is
  $151.81 at cost and the per-asset limit is $300 (or unbounded after #853). A whole-sleeve sell
  above the per-order cap is **vetoed**, so any "sell it all" path must slice into legs of at
  most `max_per_order_usd`, one per cycle, or the operator must accept a rail change (§12, Q3).
- **Rail 10** (`sell_only_on_rule`) requires `rule_kind` on every SELL. #831's design flagged
  that a rebalancing trim "is neither an exit nor a harvest in today's sense" and needs a
  decision. This design's answer: every sell-side feature is a **named rule kind** (§3.1), so
  rail 10 is satisfied by construction and the audit trail names the reason.
- **The mode is global.** `_effective_mode` returns `autonomous` when the profile says so, for
  every order. The live deployment runs with autonomy ON deliberately (the wrapper is unattended;
  `confirm` mode with no TTY places nothing). A new sell rule promoted to `live` today would
  therefore **sell unattended**. §3.5 adds a second, sell-only gate so that is not the default.
- **Two ledgers, two averages.** `positions` holds per-tranche lots (`qty`, `entry_fill`,
  `entry_fee`, FIFO by contract); `executor._held_position` derives quantity and average cost
  from `orders`. They can disagree (#798: an out-of-band venue sale is invisible to both). §3.3
  picks one.
- **No web or MCP verb reaches a sell**, and none is added here. `keel/web/api.py`'s DCA card is
  read-only (#850); `capabilities.py` records that the browser can increase nothing beyond the two
  #781 actions. Every command in this document is a TTY command.

### 2.5 The redeploy leg is a BUY, and the BUY rails are strict

Both rebalancing (request 2) and rotation (request 5) sell one asset and buy another. The buy
leg is a non-DCA BUY unless the design says otherwise, so it meets:

- **rail 8**, no averaging into losers: a rebalance buy of an *underweight* asset is very often
  a buy **below its average cost**, which is exactly what rail 8 vetoes for non-DCA intents;
- **rail 14**: the buy consumes the month's attested buy cap, which is now **the** DCA limit
  (#842). A $100 redeploy is two weeks of the BTC DCA;
- **rails 4, 5, 6**: exposure, correlation and concentration, at cost.

Flagging the leg `is_dca=True` to escape rails 4 and 8 would make a rebalance look like
accumulation to the rails. This design does **not** do that (§3.4).

### 2.6 What the knowledge base says about the sale shapes

- **Trimming into a rising position** has a theoretical case: KB §62 (Bebbington & Kühn) shows
  that under positive autocorrelation the variance-optimal policy is "reduce exposure as the run
  extends", translated long-only. The KB logs it as an independent justification for exits
  already adopted, "reinforces, does not extend". #442 then measured an ATR trail and a
  break-even roll on the rule families at 120 bp and found trailing **worse**; both knobs ship
  default-off. #830 measured a 200-day SMA filter on the daily turtle: **no arm distinguishable
  from the baseline**.
- **Fixed-weight rebalancing** is marked "wrong paradigm" for this universe (KB §51: correlated
  alts are near-single exposure). #831 did not contradict it.
- **Accumulation through drawdowns is the sleeve's thesis.** `dca.py`'s module docstring:
  "continuing-through-drawdown beat perfectly-timed DCA". The rail 8 and 11 exemptions exist so
  the sleeve keeps buying when a stop would be selling. A structural stop on the sleeve is the
  direct negation of that thesis (§7).
- **Wash trades and sale-and-buyback**: KB §65.11 (Ayub Ch 6.11), "Bay' al-'Inah / buy-back /
  wash trades: prohibited by the majority; maps to a no-self-dealing / no-fabricated-round-trip
  posture"; §28.1 the same, "minor"; §66 notes the structures are "disputed" and out of scope
  because keel "never sell[s]-then-repurchase[s] the same asset from the same counterparty as a
  financing device". None of these sources address selling to realise a loss for a tax
  authority; that is **not stated** in the knowledge base (§8.3).
- **PAXG is `bay' al-sarf`** (KB §65.5, §65.3): no deferment, settlement inside the OIC's 72-hour
  tolerance. An exchange spot fill clears it; a rotation *into* PAXG is spot and immediate and
  raises nothing new, but PAXG's ~100 bp per-leg slippage does (#831 §5).

## 3. The architecture: one sell pipeline for five reasons

### 3.1 Every sleeve sell is a named rule kind on a live rule row

Sells are not commands with flags; they are rules, because rules are what the lifecycle, the
rails, the audit trail and the promotion gate already govern. Five kinds, one per request:

| Kind | Request | Sells | Buys | Status in this design |
|---|---|---|---|---|
| `profit_take` | 1 | 10–20% of a product's sleeve | nothing (to cash) | preview-only (§4) |
| `band_rebalance` | 1, 2 | upper-band breach down to target | underweights, by shortfall | not built (§5) |
| `reverse_dca` | 3 | `target_usd` net per cadence | nothing | **build** (§6) |
| `sleeve_exit` | 4 | the product's whole sleeve, sliced | nothing | preview-only (§7) |
| `rotation` | 5 | a losing alt's sleeve | BTC / ETH / PAXG | not built (§8) |

A kind that is "not built" still gets its name here so that, if it is ever built, rail 10's
`rule_kind` and the audit trail carry a vocabulary decided once.

Each kind is a `Rule` subclass in `keel/strategy/rules/`, registered in `RULE_REGISTRY`, with
`PARAM_DOCS`, `decimal_params`, `accumulates = True` (no R, no backtest edge claim; the edge
report gives it an accumulation-style row, not a trade table) and `promotion_class =
"sleeve_sell"` (§3.7). It is added with `keel rules add --kind <kind> --product <id> --params
'{...}'`, lands at `candidate`, and can place nothing until promoted, exactly like every rule.

### 3.2 A new action, `REDUCE`, and a new hook, `Rule.reduce_signal`

`Action` gains `REDUCE`. `Rule` gains one method with a default:

```python
def reduce_signal(self, holding: Holding, candles_by_tf) -> Reduction | None:
    return None
```

`Holding` is the sleeve view of §3.3. `Reduction` is a frozen dataclass:

```python
@dataclass(frozen=True)
class Reduction:
    product_id: str
    qty: Decimal                 # base units to sell, <= holding.qty
    reason: str                  # the rule kind
    trigger: dict[str, Any]      # the numbers that fired, for the audit row
    expected_price: Decimal      # the completed bar's close the rule decided on
    ts: int
```

A `Reduction` never names a buy. The redeploy leg, where a kind has one, is a **separate**
`Action.ENTER` signal built by the same rule on the next cycle from the cash the sale produced,
and it walks the full BUY pipeline (§3.4). A rule that needs both legs to succeed for its
accounting to make sense cannot have them; the two legs are two orders and either can be vetoed.

The engine step is `agent._handle_reductions(product_id, product_rules, ...)`, run **after**
`_handle_exits` and **before** entries, for every product with an open tranche. It does not read
`position_rule`: a sell-side rule is not the owner of the position, it is a policy over it. It
asks every live rule on the product for `reduce_signal`, applies arbitration (§3.6), and hands at
most one `Reduction` to `executor.reduce`.

`executor.reduce` is `scale_out` without the bracket half: it builds a SELL `OrderIntent` with
`rule_kind=reduction.reason`, `rule_id` threaded (#803), `is_dca=False` on the **intent** (no rail
reads `is_dca` on a sell, so the flag is irrelevant there), sizes through `_clamped_sell_qty`
(#667), runs `guards.check`, previews, passes the mode gate (§3.5), places, logs, and on fill calls
`streak.book_exit(sold_qty=reduction.qty, is_dca=<derived>)`. `is_dca` for the **outcome** is
derived from the tranches consumed, exactly as `_book_paper_exit` derives it: a sale from `dca`
tranches writes `trade_outcomes.is_dca = 1`, so rail 16's streak counter never sees a sleeve
sale as a losing trade. If a DCA tranche still had a resting bracket (none does, but #799's fix
may leave one), `_clear_resting_bracket` runs first, as it does for every SELL.

A `Reduction` that is **not** executed (preview-only, vetoed, declined at the gate) is still
recorded (§3.8). The proposal is the product; the fill is optional.

### 3.3 Lots and average entry: the `positions` ledger is the truth

`Holding` is computed by one pure function, `sleeve.holding_of(repo, product_id, marks)`, over
the **open `positions` rows** for the product, never over `orders`:

- `qty` = Σ tranche `qty` (the quantity **still held**; `scale_out` already mutates it);
- `cost_basis` = Σ (`qty_i · entry_fill_i` + `entry_fee_i · qty_i / original_qty_i`), entry fees
  included so that break-even is honest;
- `vwae` (volume-weighted average entry) = `cost_basis / qty`;
- `tranches` = the rows, oldest first, because `book_exit` consumes them FIFO and any preview must
  show which tranche a sale would hit and what it would realise;
- `mark`, `unrealised`, `weight` (if `target_weights` are configured and marks exist for every
  sleeve product).

Two invariants, each a `keel doctor` finding rather than a silent assumption:

- `sleeve.ledger_drift`: `holding_of(...).qty` differs from `executor._held_position` by more than
  the venue's `base_increment` for the product. That is #798's phantom exposure surfacing where a
  sell would be sized from it.
- `sleeve.venue_drift`: `holding_of(...).qty` exceeds the venue's `Balance.total`. The executor
  already clamps a SELL to the venue and writes `executor.sell_clamped_to_held`; this makes the
  drift visible before a sale, not during one.

FIFO stays the accounting order. §8 explains why specific-lot selection is not offered.

### 3.4 Which rails apply, per leg

**The SELL leg** meets every rail `guards.check` runs on a sell today, unchanged: 1 allowlist, **2
per-order cap**, 9 (only if a `protective_stop` is carried, which `sleeve_exit`'s resting variant
would), 10 `sell_only_on_rule` (satisfied by the kind), 12 kill-switch and stale feed, 18
settlement currency, 19 spot shape, 21 base balance. It is exempt from 3, 4, 5, 6, 7, 8, 11, 13,
14, 16, 17, 20, 22, all of which are buy-only or stop-only. **No rail is added, relaxed or
DCA-exempted for sells** in this design. Rail 2's consequence is accepted and designed around: a
sale larger than `max_per_order_usd` is emitted as a sequence of legs, one per cycle, and the
preview says how many days the sequence takes.

**The redeploy BUY leg** (`band_rebalance`, `rotation`) meets every BUY rail as a **non-DCA**
buy: 4 total exposure, 5 correlation, 6 concentration, **8 no averaging into losers**, 11
drawdown breaker, 13 funding, **14 monthly buy cap**, 16 streak, 17, 20, 22. This is deliberate
and is the main reason those two kinds are not built (§5, §8): a rebalance into a fallen
underweight asset is, to rail 8, averaging into a loser; and the leg spends the DCA cap.

**Sleeve-level caps**, enforced in `_handle_reductions` before the intent is built, so a veto
there costs nothing at the venue:

- at most **one sleeve SELL per product per UTC day**, across all kinds;
- a sell-side rule never fires on a day the product's `dca` rule fires (a same-day buy and sell of
  one asset is a round trip that pays two fees for nothing);
- `min_hold_days` after the newest tranche in the product (default 30): a sale never realises a
  tranche bought this week.

### 3.5 Confirmation and autonomy: sells get their own gate

Today one profile flag releases every order. This design keeps that flag exactly as it is for
buys and for the existing whole-position exits, and adds a **second, narrower** gate for the
`REDUCE` path:

- **Default: `execution: preview`.** Every sell-side rule row carries an `execution` param whose
  only value in v1 is `preview`. In preview, `_handle_reductions` records the proposal (§3.8),
  sends a notification, and places nothing, in **both** `confirm` and `autonomous` mode.
- **Placing at a terminal:** `keel dca trim --confirm <proposal-id>` and `keel dca exit
  --confirm <proposal-id>` re-evaluate the proposal against the current book and rails, show the
  venue preview, and require the typed `yes` through `_require_interactive_confirmation`. These
  are new rows in `capabilities.CAPABILITIES` (surface `cli`, gate `tty`), and
  `tests/test_capabilities.py` fails until they are declared.
- **Unattended sells** need two things: the rule row set to `execution: auto` **and** a profile
  window armed by `keel autonomy on --sells [--for-hours N]`, a separate TTY-gated capability
  row whose `increases` line reads "sleeve SELLs place with no further prompt, for the window
  named". `_effective_mode` is unchanged; `_handle_reductions` consults a new
  `Profile.is_autonomous_for_sells(now_ts)` instead. `keel autonomy off` clears both windows.
- **The browser and the MCP server get no verb.** `capabilities.py` states the invariant and its
  test scans `keel/web/` for it.
- **`--force` promotion** is untouched and does not release execution: a force-promoted sell rule
  is live in `preview` until the two switches above are set.

So: a new sell path reaches the venue only after a human has typed `yes` at a terminal at least
twice, once to promote the rule and once to either confirm the sale or arm the sells window. That
is what "preview and confirmation by default before any live market sell" means here, made
mechanical.

### 3.6 Arbitration between kinds

Several kinds may fire on the same product in the same cycle. The order is fixed, not
configurable, and the first that fires wins the day:

1. `sleeve_exit`: a structural break sells everything; a trim beneath it is noise.
2. `reverse_dca`: a scheduled distribution the operator is relying on.
3. `profit_take`.
4. `band_rebalance`.
5. `rotation`.

A lower-priority kind that also fired is recorded as `superseded_by` in its proposal row, so the
audit trail shows that it fired and why nothing happened. Across products there is no
arbitration: each product is independent, subject to `max_per_day_usd`, which counts buys only,
and to rail 2 per leg.

### 3.7 Candidate → paper → live for a sleeve-sell rule

`rules promote`'s gate is three things: lookahead, G2 floors on a backtest's R sample, G4 PBO.
A sleeve-sell rule has no R and no trades in the backtester's sense, exactly as `dca` has none
(`accumulates = True`; the go-live runbook: "A DCA rule cannot clear the gate at all,
structurally"). Force-promoting it is the wrong shape too: `--force` exists for a rule whose
backtest *cannot reach the floor*, not for one that has no floor.

`promotion_class = "sleeve_sell"` therefore gets its own gate, `promotion.sleeve_sell_gate`:

- **candidate → paper:** the rule constructs (`rules add` already guarantees this), the lookahead
  check passes on `reduce_signal` (it reads completed bars only; `completed_days` is reused), and
  `keel rules backtest` prints the rule's **proposal replay**: every `Reduction` it would have
  emitted over the cached history, each with its realised P&L against FIFO lots at 1.2% and 0.9%,
  and the sleeve's terminal value with and without the rule. That is a *description*, not a pass
  mark; there is no threshold to tune to.
- **paper → live:** at least `min_paper_days` (default 60, two monthly checks) in `paper` with the
  proposals log (§3.8) showing at least one proposal the operator has marked `reviewed` with
  `keel dca proposals review <id>`. A rule that never fired in paper is not promoted on silence.
- **live:** starts in `execution: preview` regardless (§3.5).

Whether the paper→live reading is stricter than that is an operator decision (§12, Q6).

### 3.8 The audit trail

Existing tables carry it; one is added.

- **`sell_proposals`** (new): one row per `Reduction` evaluated, executed or not. Columns:
  `id`, `ts`, `product_id`, `rule_id`, `rule_kind`, `qty`, `expected_price`, `vwae`,
  `cost_basis`, `expected_gross`, `expected_fee` (previewed, or the fallback rate and which),
  `expected_net_pnl`, `trigger` (JSON), `rails` (JSON: violations, skipped), `decision`
  (`preview` / `superseded` / `vetoed` / `declined` / `placed`), `superseded_by`, `order_id`
  (when placed), `reviewed_ts`. Money is TEXT, timestamps INTEGER, per the schema's conventions.
- **`orders`**: the placed SELL, with `rule_kind`, `rule_id`, `confirmation` (`confirm`,
  `autonomous`, or the new `confirm_sells` token so a reader can tell which gate released it),
  the submit book and the provenance, as today.
- **`trade_outcomes`**: written by `book_exit`, `is_dca` derived from the tranches, so the sleeve's
  realised P&L is in the same table the journal reads.
- **`audit_events`** (#721, hash-chained): `sleeve.proposal`, `sleeve.placed`, `sleeve.superseded`,
  `sleeve.autonomy_sells_on/off`, so a proposal cannot be deleted without breaking the chain.
- **Notifications** (`keel/notifications.py`): a `sleeve.proposal` event per new proposal, and a
  `sleeve.exit_watch` event per **level transition** of the exit monitor (§7), never per cycle.
- **`keel dca proposals [list|show|review]`** reads the table; the web `/rules` page may render it
  read-only, as the DCA card is.

## 4. Feature 1: profit-taking and trimming

**Request.** Trigger on gain above X% over the volume-weighted average entry, or on weight drift
more than 15% above target; trim 10–20% to cash; trim only when expected net profit exceeds fee
drag; spot only, constructive possession, no shorting; `keel dca trim --preview`; propose the rule
architecture, CLI and rails before implementation.

**Rule kind: `profit_take`.** Params: `product_id`, `gain_pct` (X, over `vwae`, default 25),
`trim_pct` (10–20, default 15), `min_net_usd` (the net profit a trim must clear after the
previewed fee and per-product slippage, default 5), `cooldown_days` (default 30),
`execution: preview`. The drift trigger is **not** a `profit_take` param: drift is
`band_rebalance`'s trigger (§5) and the two are kept apart so a trim can be read as one thing.

**Trigger, on the completed daily bar:** `close ≥ vwae · (1 + gain_pct/100)`. **Size:**
`qty = holding.qty · trim_pct/100`, floored to `base_increment`, capped at `max_per_order_usd`.
**Fee gate:** `expected_net = qty · (close · (1 − slippage) − vwae) − previewed_fee ≥
min_net_usd`, where `vwae` includes entry fees (§3.3) so "net profit" means net of both legs'
fees. Below the gate: no proposal, and the reason is logged once per product per day.

**CLI.** `keel dca trim --preview [--product BTC-USD] [--gain-pct 25] [--trim-pct 15]`: a
**read-only** report, no rule row needed, that prints for each sleeve product the `Holding`
(qty, vwae, cost, mark, unrealised), whether the trigger is met, the tranche a FIFO trim would
hit, the venue-previewed fee, the net, the verdict, and the days a sale above the per-order cap
would take. Off a TTY it prints and writes nothing, as `keel dca plan` does.
`keel dca trim --confirm <proposal-id>` places one, gated (§3.5).

**Rails.** §3.4 sell leg. No buy leg.

**Failure modes.** (a) The trim realises a tranche bought recently at a higher price than the
average because FIFO does not pick the lot: the preview shows which tranche, and `min_hold_days`
bounds it. (b) The sleeve trims into a run that continues: this is the cost the KB's §62 argument
accepts and #442's measurement disliked. (c) Trim, then the weekly DCA rebuys: a round trip at
1.8% plus slippage. The same-day exclusion (§3.4) prevents the worst case; the monthly netting
finding (§6) reports the rest.

**Tests.** Unit: trigger at, above, below the threshold; size floors and caps; the fee gate at the
boundary with a previewed fee and with the fallback; `vwae` with and without entry fees pinned
against a hand-built ledger; never a `Reduction` above `holding.qty`; `None` on a same-day DCA
buy; the rule-conformance suite (`tests/strategy/rule_conformance.py`). Replay: the proposal
replay over the BTC history prints and is deterministic. Doctor: `sleeve.ledger_drift` fires on
a fabricated out-of-band sale.

**Evidence status.** The drift half is #831's arm D: not better than static DCA. The gain-over-
entry half is **untested**, and the nearest measurements (#442 trailing exits worse; #831 D's
lead on one path taken back by the bootstrap) do not favour it. KB §62 supplies a theoretical
motive under positive autocorrelation, which the KB itself treats as already served by exits.

**Recommendation: build as preview-only.** The report (`keel dca trim --preview`) is cheap, reuses
`Holding` that every other feature needs, and gives the operator the fee arithmetic they asked for
before they sell by hand. The rule kind and the `--confirm` path are specified so they can be
built later, but **no `profit_take` rule is promoted and no automatic trim ships** until a
pre-registered trial says it helps. That trial would be **accumulation policy trial 4**: arm E
"DCA plus profit-take at `gain_pct` over VWAE, `trim_pct` to cash" against A, judged on
**drawdown** per #831 §6, at 1.2% and 0.9%, pre-registered in `trials-ledger.jsonl` before the
run. **Cost if wrong:** if trimming does help, the operator forgoes it until the trial runs; the
report still shows them when they could trim by hand. If it is built as an automatic seller and
is wrong, the sleeve pays two fees per cycle to underperform the baseline it exists to be.

## 5. Feature 2: contribution steering and band rebalancing

**Request.** Steer deposits to underweights first; check monthly for drift beyond ±15% and
generate a candidate sell for the upper band; quantify the fee drag; backtest against static DCA.

**What already exists.** `keel dca plan` writes one `dca` rule per asset from `target_weights`
and a monthly budget. Steering would re-weight those rules' `budget_usd` monthly by shortfall
(arm C's formula). Band rebalancing would be `band_rebalance`: params `product_id`, `band_rel`
(0.15), `band_abs_floor` (0.015), `check_cadence_days` (30), `execution: preview`; trigger `weight
> target + max(band_rel·target, band_abs_floor)`; size down to target; the redeploy leg on the
next cycle as a non-DCA BUY to the underweights by shortfall, meeting rails 4, 5, 6, **8** and
**14** (§2.5).

**Fee drag, quantified.** Per rebalance event: sell leg fee (0.9% live, 1.2% sim) plus sell
slippage, plus buy leg fee plus buy slippage, plus the buy's consumption of the month's rail-14
cap. #831 measured $930 of fees over five years on the D arm at $500 a month (3.0% of deposits),
for a lead that held on one path. At the live sleeve's scale (about $213), one full rebalance of a
$30 overweight costs about $0.55 in fees and $0.30–$0.60 in slippage, and blocks $30 of the
month's DCA cap.

**Evidence status.** **Tested.** #831 arms C and D are these two features, and the request's
"backtest against static DCA" has been run, pre-registered, with a bootstrap: neither is better
than static DCA after fees; D's +13.5% on the historical path is not a property the resampled
paths share. Its design already flagged the rail 10 question (answered in §3.1) and "sleeve
accounting" (answered in §3.3).

**Recommendation: don't build (yet).** Not the steering, not the sell. The monthly drift *view*
(weights against targets, the band, the candidate sell, its fee drag) is added to `keel dca trim
--preview --view bands` as a read-only report, because it is a few lines over `Holding` and
answers the operator's question without placing anything. **No new backtest**: it was done. If
the question is re-opened, #831 §6 names the shape: value averaging on drawdown, not trimming on
Sortino, pre-registered, trial 4 of 3 already spent. **Cost if wrong:** the historical path's
13.5% over five years at $500 a month, if that path's ordering recurs and the bootstrap was the
one that was wrong. Against that: two fees per event, rail 8 and rail 14 collisions on every
redeploy, and PAXG's 100 bp slippage on the one leg where rebalancing had a theoretical case.

## 6. Feature 3: `reverse_dca`, cash distributions

**Request.** Params `{"product_id": "BTC-USD", "cadence_days": 30, "target_usd": 100,
"min_price_floor": 60000}`; price-floor and maximum-drawdown gates so it never panic-sells at a
bottom; integrated with the live confirmation gates and the execution audit trail; added with
`keel rules add --kind reverse_dca`; unit and backtest tests.

**Why this one is different.** It is not an edge claim and not a rebalancing hypothesis. It is
the mirror of `dca`: a spend plan, decided by the operator's need for cash, bounded by caps, and
evaluable without a backtest of alpha. #831's null does not bear on it, any more than it bears on
whether to deposit $500 a month.

**Rule kind: `reverse_dca`.** Params and `PARAM_DOCS`:

| Param | Meaning | Default |
|---|---|---|
| `cadence_days` | days between distributions, epoch-aligned like `dca` | 30 |
| `target_usd` | **net** cash wanted per distribution; gross is `target / (1 − fee − slippage)` | required |
| `min_price_floor` | no sale when the completed bar's close is below it | required |
| `max_drawdown_pct` | no sale when close is more than this below the `lookback_days` high | 25 |
| `lookback_days` | window for that high | 200 |
| `floor_qty` | units never sold below; a distribution that would breach it is skipped | 0 |
| `execution` | `preview` in v1 | `preview` |

**Trigger, on completed daily bars:** on cadence, `close ≥ min_price_floor`, `close ≥ high_N ·
(1 − max_drawdown_pct/100)`, `holding.qty − qty ≥ floor_qty`, not a `dca` buy day for the
product, and the sleeve's one-sell-per-day cap free. **Size:** `qty = min(gross / close,
holding.qty − floor_qty)`, floored to `base_increment`, then capped at `max_per_order_usd` (a
$100 target is under the $200 live cap; a larger target slices). A skipped distribution is **not**
carried forward: the next cadence day distributes `target_usd`, not two of them, because a
catch-up sale after a drawdown gate lifts is exactly the sale the gate exists to avoid.

**Gates and audit.** §3.5 verbatim: `preview` records and notifies; `keel dca distribute
--confirm <proposal-id>` places one at a TTY; `execution: auto` plus `keel autonomy on --sells`
places unattended. Every proposal, placed or not, is a `sell_proposals` row; every fill is an
`orders` row with `rule_kind="reverse_dca"` and a `trade_outcomes` row with `is_dca=1`.

**CLI.** `keel rules add --kind reverse_dca --product BTC-USD --params '{"cadence_days": 30,
"target_usd": 100, "min_price_floor": 60000}'` writes a `candidate` row, validated by
construction (`build_rule_from_params`). Promotion per §3.7. `keel dca distribute --preview` is
the read-only view of what the next cadence day would do.

**Rails.** §3.4 sell leg only.

**Failure modes.** (a) A live `dca` and a live `reverse_dca` on the same product buy $50 a week
and sell $100 a month: legal, and a round trip at 1.8%. The design refuses `paper → live`
promotion of a `reverse_dca` while a `live` `dca` exists on the product unless
`--allow-concurrent-dca` is typed, and `keel doctor` reports `sleeve.buy_and_sell_same_asset`
monthly when both fire. (b) The price floor is set once and the market runs far above it, so the
floor stops protecting anything: `keel doctor` warns when `min_price_floor < 0.5 · close`.
(c) The venue holds less than the ledger: the clamp sells what is there and the drift finding
fires. (d) The month's sale is vetoed by rail 12 (stale feed) on the cadence day and lost: the
proposal row says `vetoed`, and the notification says why.

**Tests.** Unit, written first: cadence alignment against `dca`'s; each gate at its boundary
(floor, drawdown, `floor_qty`, same-day DCA); gross-from-net sizing with a previewed fee and with
the fallback; never a `Reduction` above `holding.qty − floor_qty`; no carry-forward; `None` on no
daily candles; `describe()`/`build_rule_from_params` round trip; `rules add` refuses
`target_usd ≤ 0`, `cadence_days ≤ 0`, a floor above the current close is *allowed* (it is a
choice, not an error); the rule-conformance suite. Executor: `reduce` writes the proposal row and
the order row, books FIFO with `is_dca=1`, respects rail 2 slicing, refuses when `available_base`
is zero (rail 21). Sim: `sim.portfolio_sim._process_dca_signals` gains the reverse path, and
`report.accumulation_table`'s `DcaSleeve` gains `distributed_usd`, `units_sold`,
`realised_pnl`, `sell_fees`; a pinned run on gapless candles reproduces a hand computation; a
determinism test under one seed; a parity test that the sim and `guards.check` veto the same
oversized distribution. Doctor: the two findings above.

**Evidence status.** Not a measurable claim about returns and not presented as one. The
"backtest tests" are fidelity tests of the harness, not a verdict.

**Recommendation: build.** It is the one request with a use the ledger can honour without a
hypothesis, it is bounded by `target_usd × 12` a year, and it forces the shared architecture
(§3.2–§3.8) to exist, which every other feature then reuses. Ships in `preview` and stays there
until the operator arms it. **Cost if wrong:** a distribution at a locally bad price, bounded by
`target_usd`, at 0.9% plus slippage; and, if the operator runs it beside the weekly buy, a round
trip the doctor finding names every month.

## 7. Feature 4: a protective exit monitor for live DCA holdings

**Request.** A wide structural stop: a trailing 35% stop or a 200-day SMA break; reconcile with
#799 so unbracketed spot holdings can carry passive stop monitors; preview and confirmation by
default before any live market sell.

**What "reconcile with #799" has to mean.** #799 is a stranded *rule* position (the entry filled,
the bracket preview threw, no `positions` row was written). #811 is the passive-monitoring gap
(an open tranche nobody watches; the only alert was driven off a retry record and went silent
when the record was cleared). A monitor for DCA holdings must therefore: (a) read the
**`positions` ledger**, never `agent_state` retry records, so it cannot be silenced the way #811
describes; (b) depend on #799's proposal 2, "record the position first", so a holding is in the
ledger to be monitored; (c) not duplicate #811's `position.unprotected`, which stays the finding
for **rule** tranches with a recorded `initial_stop`, and which excludes DCA on purpose.

**Rule kind: `sleeve_exit`**, plus a monitor that is pure and rule-less. Two layers:

1. **The monitor** (`keel/execution/sleeve_exit.py`, pure): for every open tranche without a
   resting bracket (`reconcile._has_resting_bracket` is `False`), compute on completed daily bars
   `level_dd = high_{lookback} · (1 − dd_pct/100)` (the "trailing 35%") and `sma_break = close <
   SMA_200 for `confirm_days` consecutive bars`, and classify the product `clear` / `near`
   (within `warn_pct` of a level) / `breached`. It writes the classification to
   `agent_state["sleeve_exit:<product>"]` and emits a `sleeve.exit_watch` **event on transition
   only**. It runs every cycle for every unbracketed tranche, **PAXG tranche 3 included**: the
   2026-09-22 decision was to hold PAXG without a stop, not to stop being told where it is, and
   transition-only reporting is what keeps the alert from training the operator to ignore it.
2. **The rule** `sleeve_exit`, params `product_id`, `dd_pct` (35), `lookback_days` (200),
   `sma_period` (200), `confirm_days` (3), `arms` (`["drawdown", "sma"]`, either or both),
   `execution: preview`. Its `reduce_signal` returns a `Reduction` for the **whole** sleeve
   (sliced per rail 2) when the monitor says `breached`. Everything in §3.5 applies:
   `preview` by default, `keel dca exit --confirm <proposal-id>` at a TTY, or `execution: auto`
   plus `keel autonomy on --sells`.

**Why not a resting stop order.** A native stop resting at the venue is the strongest
protection and the design allows it as a later `mode: resting` (rail 9 would then govern it,
ratchet-only). It is not the default because a resting stop **is** the automatic sale the request
says should be previewed first, and because a 35% stop on an accumulation sleeve resting through a
crypto drawdown is, on #831's numbers, a sale that would have fired in 2022 in every arm.

**Rails.** §3.4 sell leg. Rail 2 slicing matters most here: at the live cap of $200 the BTC sleeve
is one leg today and would be two at $300.

**Failure modes.** (a) **The stop contradicts the sleeve's thesis.** `dca.py` exempts DCA from
rails 8 and 11 so it keeps buying through the drawdown a stop would sell into; an automatic
`sleeve_exit` would sell the sleeve and the weekly `dca` would start rebuying it the next week,
two fees apart. This is why v1 is alert-and-preview, never auto. (b) A 200-day SMA on an asset with
less than 200 days of cached history is undefined: the arm reports `insufficient_history` and
does not fire. (c) The monitor is silent because a cycle did not run: that is #811's
`position.unmanaged` territory and stays there. (d) A transition flaps around a level: `near`
has hysteresis (`warn_pct` in, `2 · warn_pct` out).

**Tests.** Pure monitor: each arm at, above, below its level on hand-built candles; transition
events fire once per transition and never per cycle; hysteresis; insufficient history;
unbracketed detection reuses `_has_resting_bracket`; a tranche with a resting bracket is skipped;
PAXG-shaped tranche (rule tranche, no bracket) is included; a cleared retry record changes
nothing (the #811 regression, stated as a test). Rule: `reduce_signal` fires only on `breached`;
whole-sleeve size; slicing at `max_per_order_usd`; `preview` places nothing under `autonomous`.
Doctor: the `sleeve_exit:<product>` state is rendered.

**Evidence status.** #442: trailing exits measured worse on the rule families at 120 bp. #830: a
200-day SMA filter on the daily turtle is not distinguishable from the baseline. #831: every arm
lived through a 78–80% drawdown; no deployment policy tested changes that. None of these is a
test of a 35% structural stop on an accumulation sleeve, and this design proposes none: the
monitor is an operator alert, which needs no edge to justify it.

**Recommendation: build as preview-only** (the monitor and the `sleeve_exit` kind in `preview`),
**after** #811's two doctor findings and #799's "record the position first" land, which are its
prerequisites and are already specified in their issues. Automatic selling stays off, and turning
it on is the sells gate of §3.5, an explicit operator act. **Cost if wrong:** if a structural
break comes and the operator does not act on the alert, the sleeve rides it down exactly as it
does today; the monitor has cost a notification. If instead it were built as an automatic seller
and the level was a false break, the sleeve sells at the bottom, pays 0.9% plus slippage, and the
weekly DCA buys it back higher.

## 8. Feature 5: asset rotation and tax-loss harvesting

**Request.** Identify underperforming altcoins with capturable unrealised losses and rotate
them into BTC / ETH / PAXG; stay Shariah-compliant (no synthetic leverage, direct spot
settlement); report the net tax and fee benefit before proposing anything.

### 8.1 What keel can compute

From the `positions` ledger and the venue's previewed fee, per tranche and per product: quantity,
entry fill, entry fee, FIFO cost basis, mark, **unrealised gain or loss**, the **realised** gain
or loss a sale of `q` units would book, the sell fee, the slippage, the buy leg's fee and
slippage into the target asset, and therefore the **fee cost** of a rotation. `keel pnl` already
prints realised and unrealised per asset from average cost; a `--lots` view over tranches is a
small extension.

### 8.2 What keel cannot compute

A **tax** benefit. Nothing in this repository records a jurisdiction: `grep` over the docs,
configs and code finds none (the only hits are the fiqh basis noting rulings differ by
jurisdiction, and two feasibility studies about venues). The original 2026-07-14 design listed
`keel harvest --preview` as "the only non-buy trade path", to "realize a tax loss", and put "tax
filing" **out of scope** in the same section; the command was never built and the jurisdiction
was never written down. Whether an unrealised loss is "capturable" depends on the operator's
residence, filing status, holding period, the venue's own cost-basis method (which need not be
FIFO), and any wash-sale or bed-and-breakfast rule, none of which keel can source. **This design
asserts no tax law and computes no tax number.** If the operator records a jurisdiction, the
report can label lots with it; it still cannot say what is owed or saved.

### 8.3 The fiqh finding

The knowledge base does not address selling to realise a loss for a tax authority; that is
**not stated**. It does address the shape such a sale commonly takes: KB §65.11 (Ayub Ch 6.11),
"Bay' al-'Inah / buy-back / wash trades: prohibited by the majority", with the repo's reading "a
no-self-dealing / no-fabricated-round-trip posture" (§28.1, §66). A harvest that sells an asset
and buys it back to keep the exposure is a fabricated round trip in that sense, and would be the
first thing keel does that the KB's "form over substance" scrutiny (§66) could not pass
trivially. A **rotation** into a *different* asset is not that shape. This document records the
objection as a **finding** for the operator and a candidate question for the scholarly review
that `docs/fiqh-basis.md` says has not happened, and does not build the buy-back form.

### 8.4 The fee arithmetic, which keel can report

A rotation is two taker legs and two slippages: about **1.8%** at live rates, 2.4% at sim rates,
plus per-product slippage on both sides (PAXG about 100 bp per leg). On a $30 ADA sleeve that
has lost 40%, the rotation costs about $0.75–$1.20 to move $18 into BTC. The buy leg meets rail 8
(a BTC buy below BTC's average cost is vetoed unless flagged DCA), rails 4/5/6, and **spends the
month's rail-14 DCA cap**. The "underperforming altcoin" judgement is a **plan** decision the
operator already makes in `keel dca plan` by setting a weight to zero; that stops the buying. What
is missing for the selling is #798's `keel positions close`, so a hand sale at the venue is
reflected in keel's ledger.

**Evidence status.** No measurement exists or is proposed. The allowlist's small caps were
admitted on weak evidence and the operator was told so (`config.live-sandbox.yaml`'s DOGE note);
that argues for a plan change, not a sell rule.

**Recommendation: don't build (yet).** Build the two read-only pieces it would need anyway:
`keel pnl --lots` (per-tranche unrealised and hypothetical realised P&L with fee cost, labelled
"not tax advice"), and #798's `keel positions close` so a manual rotation does not leave phantom
exposure. Do not build `rotation` as a rule kind, and do not build a same-asset harvest at all
until (a) a jurisdiction is on record, (b) the fiqh question in §8.3 is answered by someone
entitled to answer it, and (c) the operator has decided the buy leg's treatment under rails 8 and
14. **Cost if wrong:** the operator rotates by hand at the venue, which they can do today, and
keel's ledger drifts until `positions close` exists; the fee cost is the same either way.

## 9. Failure modes shared by every sell path

| Failure | Where it is caught | What the operator sees |
|---|---|---|
| Ledger says more than the venue holds | `_clamped_sell_qty` (#667), rail 21, `sleeve.venue_drift` | clamped sale, doctor WARN |
| Out-of-band venue sale (#798) | `sleeve.ledger_drift` | doctor WARN before any sale |
| Sale larger than `max_per_order_usd` | rail 2 | preview shows N legs over N days |
| Buy and sell of one asset in one month | same-day exclusion, `sleeve.buy_and_sell_same_asset` | doctor WARN monthly |
| Fee assumed instead of previewed | `expected_fee` records its source | proposal row says `fallback:1.2%` |
| Stale feed / kill-switch on the cadence day | rail 12 | proposal `vetoed`, notification names the rail |
| Two kinds fire together | arbitration §3.6 | `superseded_by` on the loser's row |
| Sell released from a browser | no verb exists; `test_capabilities` scans `keel/web/` | nothing |
| Sell placed unattended without a sells window | `Profile.is_autonomous_for_sells` | proposal `preview`, notification |
| A monitor silenced by clearing a retry key (#811) | monitor reads `positions` only | unchanged alert |

## 10. Recommendations, in one table

| # | Feature | Kind | Verdict | Why | Cost if wrong |
|---|---|---|---|---|---|
| 1 | Profit-taking and trimming | `profit_take` | **preview-only**: build `keel dca trim --preview`; no automatic trim | drift half tested by #831 (D, not better); gain half untested; #442 against trailing exits | forgone trims until trial 4 runs; the report shows them anyway |
| 2 | Steering and band rebalancing | `band_rebalance` | **don't build (yet)**; read-only bands view only | #831 arms C and D, pre-registered and bootstrapped: not better after fees; rails 8 and 14 on every redeploy | one path's +13.5% over five years if the bootstrap was wrong |
| 3 | `reverse_dca` | `reverse_dca` | **build**, ships in `preview` | a spend plan, not an edge claim; bounded; forces the shared architecture | a bounded sale at a bad price; a round trip beside the weekly buy, reported |
| 4 | Protective exit monitor | `sleeve_exit` | **preview-only**: monitor plus alerts; no automatic sell; after #811 and #799 | contradicts the sleeve's own thesis if automatic; #442, #830, #831 give it no support | a missed alert leaves the sleeve where it is today |
| 5 | Rotation and tax-loss harvesting | `rotation` | **don't build (yet)**; build `keel pnl --lots` and #798's `positions close` | no jurisdiction on record; tax position uncomputable; fiqh finding on buy-backs; rails 8 and 14 on the buy leg | operator rotates by hand, as today |

## 11. Implementation plan, PR by PR

Prerequisites, already specified in their own issues and not re-specified here: **#811** (the
`position.unmanaged` and `position.unprotected` doctor findings) and **#799** proposal 2 (record
a filled entry before its bracket leg, downgrade a bracket failure to *open, unbracketed*).
**#798**'s `keel positions close` is a prerequisite for §8 and is useful to every other feature.

Each PR is TDD, one concern, mypy and ruff clean, with the module docstring carrying the rule it
encodes (this repository keeps its rules in neighbouring docstrings).

1. **`sleeve.holding_of` and `keel pnl --lots`.** Pure module `keel/execution/sleeve.py`:
   `Holding`, `holding_of`, `net_proceeds`, `fee_drag`; the two doctor findings
   `sleeve.ledger_drift`, `sleeve.venue_drift`; `keel pnl --lots`. No sell path. Tests over
   hand-built ledgers, including entry fees in the basis and the FIFO tranche a sale would hit.
2. **`Action.REDUCE`, `Reduction`, `Rule.reduce_signal`, `executor.reduce`,
   `agent._handle_reductions`, `sell_proposals`.** The pipeline with arbitration, the sleeve
   caps, rail 2 slicing, `preview` as the only execution, the proposal table and its
   `audit_events`, `keel dca proposals`. No rule kind uses it except a test double. Tests: every
   row of §9's table that the pipeline owns; `test_capabilities` unchanged (no gate yet).
3. **`reverse_dca`.** The rule kind, registry, `PARAM_DOCS`, conformance, `rules add`, the sim's
   reverse path and `DcaSleeve` columns, `keel dca distribute --preview`, the concurrent-DCA
   promotion refusal and doctor finding. Tests per §6.
4. **The sells gate.** `keel dca {trim,distribute,exit} --confirm <id>` TTY paths and
   `keel autonomy on --sells`; the `Profile` column; the `capabilities.CAPABILITIES` rows; the
   `promotion_class = "sleeve_sell"` gate and `keel dca proposals review`. Tests:
   `test_capabilities` in both directions; `preview` still places nothing under `autonomous`;
   the sells window expires; `autonomy off` clears it; the browser scan.
5. **`keel dca trim --preview`** with `--view gain` and `--view bands`, read-only, over PR 1.
   The `profit_take` kind is added in `preview` with `reduce_signal`, but no promotion of one is
   part of the plan.
6. **The exit monitor and `sleeve_exit`.** Pure `sleeve_exit.py`, the per-cycle step, transition
   events through `notifications.py`, `keel dca exit --preview`, the kind in `preview`. Lands
   only after #811 and #799 are merged.
7. **Trial 4 pre-registration (doc only, optional).** A design revision, in the format of
   #834, for arm E (profit-take over VWAE) judged on drawdown, counted as accumulation policy's
   4th trial. Written when the operator wants it; nothing runs until it is approved and its driver
   is pushed.

## 12. Open questions for the operator, each with a default

| # | Question | Default |
|---|---|---|
| Q1 | Should the existing `keel autonomy on` release sleeve sells too? | **No.** A separate `--sells` window (§3.5). The live flag was armed for buys and whole-position rule exits; a new sell class should not inherit it silently. |
| Q2 | May a `reverse_dca` go live on a product with a live `dca`? | **Refuse at promotion** unless `--allow-concurrent-dca`; the doctor names the round trip monthly. |
| Q3 | Rail 2 vetoes a whole-sleeve sell above `max_per_order_usd`. Slice, or exempt whole-position exits? | **Slice**, one leg per cycle; rail 2 is unchanged for sells. A rail change is its own decision and its own PR. |
| Q4 | Does the average entry include entry fees? | **Yes.** Break-even means both legs' fees. The venue's statement may differ; the report says which basis it uses. |
| Q5 | Which fee does a live gate use? | **The venue's preview**, with `config.fees.taker_pct` (1.2%) as the recorded fallback; 0.9% is never hardcoded. |
| Q6 | The `sleeve_sell` paper → live gate: is 60 paper days and one reviewed proposal enough? | **Yes for `preview` execution**, which is all v1 allows. Revisit before any `execution: auto`. |
| Q7 | Does the exit monitor cover PAXG tranche 3? | **Yes, on transitions only.** The 2026-09-22 decision holds the position; it does not stop the level being reported when it changes state. No automatic sell. |
| Q8 | Is a rebalancing trim a permitted sale kind under rail 10? | **Yes, as `band_rebalance`**, if it is ever built; not built now (§5). |
| Q9 | Spend accumulation policy's 4th trial on a profit-take arm now? | **Not now.** The design is written so it can be pre-registered the day the operator wants it. |
| Q10 | Is a sleeve sale a DCA outcome for rail 16? | **Yes**, derived from the tranches (`is_dca=1`), so a distribution can never trip the streak breaker. |
| Q11 | Record a tax jurisdiction in config? | **Operator's call.** Without one, §8 stays a fee report; with one, lots are labelled and still nothing is advised. |
| Q12 | Should a same-asset harvest (sell and buy back) ever be offered? | **No**, pending the fiqh finding in §8.3 being answered by someone entitled to answer it. |
