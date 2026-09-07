# Alpaca options: the venue reality, for ADR 0005

**Date:** 2026-09-07 · **Status:** desk research, discovery-only (#636). **This document decides
nothing.** It is the factual input ADR 0005 (#637) consumes; it contains no recommendation on
whether keel should trade options, because that is a jurisprudential question this pass has no
standing to answer.

## ⚠️ This does not close #636

#636's own rule of engagement is that *"the decision must not be made from documentation alone"* —
#412's lesson, where Robinhood's order-object shapes were doc-only until one real order was
observed and the first live placement 403'd anyway. **Everything below is documentation.** No API
call was made; this session holds no Alpaca credential and should not.

Each finding is therefore marked:

| mark | meaning |
| :--- | :--- |
| **[DOC]** | stated in Alpaca's own documentation or fee schedule, quoted |
| **[DOC-GAP]** | the documentation does not answer it — a finding in its own right, per #636 |
| **[KEEL]** | a fact about this repository, verified in the source |
| **[ARITH]** | derived here from quoted figures; the arithmetic is shown so it can be checked |

The account-level questions (F1 especially) are the ones a paper account can settle in an hour,
and §7 says exactly how.

---

## F1 — Alpaca has no cash accounts, and whether options survive keel's proxy is undocumented

**[DOC]** Alpaca Support, *"Can I have a cash account with Alpaca?"*:

> "No, we do not offer cash accounts. All accounts are set up as margin accounts."

**[KEEL]** This is already known here and already handled. `verify_cash_account`
(`packages/keel-broker-alpaca/keel_broker_alpaca/adapter.py:262`) reads `/v2/account`'s
`multiplier` as the posture classification and refuses anything but `1`, because Alpaca exposes no
`account_type` field. Its docstring states the position plainly: *"Alpaca opens every account as
margin and offers no true cash designation; multiplier 1 is as cash as it gets."* The operator
reaches that state by setting `max_margin_multiplier: 1` in trading configuration.

**[DOC]** Options approval is a separate axis, with its own levels:

| level | permits |
| :--- | :--- |
| 0 | disabled |
| 1 | sell covered calls, sell cash-secured puts |
| 2 | level 1 + buy calls, buy puts |
| 3 | level 1–2 + spreads (multi-leg) |

Effective level is `min(options_approved_level, max_options_trading_level)`.

**[DOC-GAP] The load-bearing question is not answered anywhere in Alpaca's documentation:** can a
live account held at `max_margin_multiplier = 1` obtain options approval at level 1, or does
options approval require the margin agreement that setting exists to neutralise? The docs describe
the two settings independently and never state their interaction.

This matters more than it looks. keel's whole Alpaca posture is a *proxy* — the venue offers no
cash account, so `multiplier == 1` stands in for one. If enabling options forces the multiplier
above 1, then `verify_cash_account` raises `CashAccountRequired` at broker construction and **the
adapter refuses to build at all** — not just for options, but for the equities trading that
already works. That would make options a strictly destructive change to a shipped capability, and
it is the single fact most likely to end the ADR early.

**Per #636's rules, the absence of an answer is itself weighted toward holding the line.**

## F2 — Assignment arrives by polling only. There is no stream.

**[DOC]** From the options-trading documentation, quoted:

> "Options assignments are not delivered through websocket events."

> "To check for assignment activity (non-trade activity, or NTA events), you'll need to poll the
> REST API endpoints."

> "Websocket support for NTAs is not currently available."

Assignments surface as **non-trade activities** on `GET /v2/account/activities`, as paired records:
`OPASN` (assignment) or `OPEXC` (exercise) removes the option position, and a paired `OPTRD`
records the resulting underlying stock transaction.

**[KEEL] This collides with the cadence, not with a rail.** keel's live agent evaluates once per
UTC day (the wrapper trades once per day; launchd merely fires hourly). An assignment is an
*inventory conversion keel did not initiate* — a short put becomes 100 shares of the underlying and
a cash debit — and with no stream, keel would learn about it whenever it next polled. Between
assignment and the next cycle, keel's book and the venue's book disagree about what is owned and
what cash exists.

That is not a novel hazard here; it is `reconcile`'s existing job. But every reconciliation keel
does today is over positions keel *placed*. An assignment is the first inventory event with no
originating keel order, and the port has no shape for it.

## F3 — The venue will liquidate a position on keel's behalf

**[DOC]** Two automatic behaviours at expiry, quoted:

> "In the event no instruction is provided on an ITM contract, the Alpaca system will exercise the
> contract as long as it is ITM by at least $0.01 USD."

> "In the event the account does not have sufficient buying power to exercise an ITM position,
> Alpaca will sell-out the position within 1 hour before expiry."

**[KEEL]** The second is the one to sit with. keel's design premise throughout is that **nothing
disposes of inventory except keel's own exit policy** — that is what rail 11's drawdown halt, the
bracket discipline, and the typed-phrase cancel ceremony (#707) all assume. An automatic venue
sell-out is a disposal keel neither ordered nor approved, executed on the venue's timetable, inside
a one-hour window keel's daily cadence cannot observe.

Whether that is acceptable is ADR 0005's question. That it *happens* is documented fact.

## F4 — Multi-leg exists, and keel's port has no shape for it

**[DOC]** Multi-leg orders post to the same `POST /v2/orders` endpoint with `order_class: "mleg"`
and a `legs` array carrying **at least 2 and no more than 4 legs**, each with its own `symbol`,
`side`, `position_intent` and `ratio_qty`. Level 3 only. `stop` and `stop_limit` are single-leg
only.

Collateral for a spread uses a *"universal spread rule"* / piecewise-payoff method: the margin
requirement is the absolute value of the most negative point of the net payoff.

**[KEEL]** `BrokerCapabilities` (`packages/keel-broker-api/keel_broker_api/capabilities.py:47`)
carries `supported_orders: frozenset[str]` and no notion of a composite order. Nothing in the port
expresses "these N legs fill or none do." Adding multi-leg is a port change affecting every
adapter, not an Alpaca-local one.

Level 1 (covered calls, cash-secured puts) needs none of this — both are single-leg. **If the ADR
admits options at all, admitting level 1 only is by far the smaller structural change.**

## F5 — Rail 19 refuses an OCC symbol today, and says spot-only is the charter

**[KEEL]** This is the concrete collision, and it is not a matter of configuration.

An OCC-format option symbol is `SPY250127C00608000` — no hyphen, 18 characters, strike encoded in
the tail. `parse_spot_product_id` requires `BASE-QUOTE`, uppercase, exactly one hyphen, so it
returns `None` and rail 19 appends a violation (`keel/execution/guards.py:905`):

> `spot_instrument: ... is not a well-formed spot product id (BASE-QUOTE, uppercase, exactly one
> hyphen). keel is spot-only: futures (BASE-DDMMMYY-CDE), equities (an opaque 64-char hash) and any
> other instrument shape are refused here regardless of what they settle in.`

Three properties of that rail decide how large the change would be:

- **It runs in every mode, both sides, DCA included** — deliberately excluded from
  `LIVE_STATE_RAILS` so paper cannot skip it. There is no rehearsal path: keel could not paper-trade
  an option to gather evidence without amending the rail first.
- **It needs no broker handle**, so it cannot be softened per-venue.
- **There is no config field to widen it**, and the comment says why: *"Spot-only is this agent's
  CHARTER, not an operator preference."*

So options are not blocked by a missing feature. They are blocked by a rail whose own source says
it encodes the constitution. **That is precisely the amendment #637 exists to consider, and this
finding sizes it: one rail, one grammar, and the charter sentence behind it.**

Note in passing that Alpaca *equities* pass rail 19 only because keel represents them as
`AAPL-USD` (`_history_product`, via `_default_sim_products`). The rail's mention of "equities (an
opaque 64-char hash)" refers to Coinbase's tokenised-equity shape, not Alpaca's.

## F6 — The fees are trivial. The spread is the cost, and it is unmeasured.

**[DOC]** From Alpaca's *Brokerage Fee Schedule*, **revised 2026-09-01** (six days before this
document; read from the PDF directly, because an automated extraction of the same file misreported
OCC as `$0.65` against an actual `$0.025` — a doc-only hazard reproducing itself inside this very
pass):

| fee | when | amount |
| :--- | :--- | :--- |
| SEC Transaction Fee | sells only | `$0.0000206 × trade value` |
| FINRA TAF | sells only | `$0.00329` per contract |
| FINRA CAT | buys and sells | `$0.000003` per equivalent share; **1 contract = 100** → `$0.0003` |
| Options Regulatory Fee (ORF) | buys and sells | `$0.015` per contract |
| OCC Clearing Fee | buys and sells | `$0.025` per contract |

Alpaca charges **no commission** on equity options for retail flow. (Index options are `$0.50`
per contract plus exchange fees, and a customer averaging ≥390 orders/day in a month is
reclassified "Professional" under the CBOE 390 Rule and pays `$0.40`–`$0.10` per contract. keel's
once-daily cadence is nowhere near that threshold.)

**[ARITH]** Per contract, retail, equity options:

```
buy  leg: 0.015 + 0.025 + 0.0003                     = $0.0403
sell leg: 0.015 + 0.025 + 0.0003 + 0.00329 + SEC     = $0.0436 + SEC
round trip                                            ≈ $0.0839 + SEC
```

A cash-secured put sold for `$1.00` premium (`$100` notional) pays `0.0000206 × 100 = $0.00206`
SEC, so the opening sell costs **`$0.0457`** — **4.6 bp of premium**. If it expires worthless there
is no closing leg at all.

**That number is not the finding. This is:** the pass-through fees are negligible, so the true cost
of an options position is the **bid-ask spread**, and no documentation states it. On the thin
strikes a `$50`-clip deployment could afford, a spread of 10–50% of premium is ordinary. keel's own
cost-fidelity work is the precedent — `docs/experiments/2026-09-05-restatement-restated.md`
measured **0 of 24 crypto assets reaching the 5bp slippage floor, median 52.3bp**, and that
restatement is what turned a "roughly break-even" claim into `0 of 240`. **An options ADR argued on
the fee schedule alone would repeat exactly the error that document corrected.**

## F7 — The capital floor is the practical blocker, before any fiqh question

**[DOC]** `qty` must be a whole number; contracts are multiplier-100. Level 1 requires *"sufficient
underlying shares"* (covered call) or *"sufficient options buying power"* (cash-secured put).

**[ARITH]** A cash-secured put is collateralised at `strike × 100` per contract, and cannot be
fractionalised:

| underlying | strike | collateral for **one** contract |
| :--- | ---: | ---: |
| a `$20` stock | `$18` | `$1,800` |
| a `$50` stock | `$45` | `$4,500` |
| a `$200` stock | `$180` | `$18,000` |

A covered call needs **100 shares** of the underlying held outright — same floor, paid in stock.

**[KEEL]** `keel/templates/config.live.yaml:82` sets `budget_usd: 50` and line 37 `risk_pct: 0.01`.
**The smallest possible options position is roughly 36× the deployment's DCA clip**, and that is for
the cheapest underlying in the table.

This is a finding about *feasibility*, not permissibility, and it is worth stating first because it
is decidable without any jurisprudence: **at current deployment size, keel cannot open a single
compliant options position on any ordinary underlying.** Whatever ADR 0005 concludes about
cash-secured puts in principle, nothing could be executed until the account is one to two orders of
magnitude larger.

---

## What a paper account settles for free, and what it cannot

**[DOC]** Paper accounts get options **automatically, at Level 3**:

> "In the Paper environment, options trading capability will be enabled by default — there's
> nothing you need to do!"

So the mechanical half of #636 is verifiable at zero cost and zero risk, with no live options
agreement and no money at stake. Read-only and paper placement are both inside #636's stated scope.

**What paper can answer** — F2 (activity records for an assignment, and their exact shape), F4
(multi-leg accept/reject and the real payload), the quote geometry and observed bid-ask on the
strikes keel would actually touch, and the contract/quantity semantics of F7.

**What paper cannot answer** — **F1**, the one that matters most. Paper grants Level 3 to everyone
automatically; it therefore says nothing about whether a *live* account pinned at
`max_margin_multiplier = 1` can be approved for options at all. That requires either Alpaca support
confirming it in writing, or a live application. Until then F1 stays **[DOC-GAP]**.

Note that a paper assignment must be *observed*, not simulated on demand: it requires a short
option held to expiry ITM, so the check has a calendar on it.

## Where this leaves ADR 0005

Three of the findings are decidable without opening the jurisprudential question at all, and all
three point the same way:

1. **F7** — the capital floor makes options unexecutable at present deployment size, by ~36×.
2. **F5** — admitting them means amending a rail whose source calls itself the charter, and which
   deliberately admits no configuration override.
3. **F1** — the venue may not permit options on the posture keel requires, and if enabling them
   moves the multiplier, it breaks the **equities** adapter that already ships.

**F3** is the one that should be argued on its merits rather than triaged: a venue that
autonomously exercises, or liquidates, without keel's instruction is a different relationship to
inventory than anything keel has accepted so far.

None of that is a recommendation. It is the ground truth #637 asked for, with the doc-only parts
marked so the ADR cannot mistake a documentation claim for an observed one.

## Sources

- [Alpaca Support — Can I have a cash account with Alpaca?](https://alpaca.markets/support/alpaca-cash-accounts)
- [Alpaca Docs — Options Trading Overview](https://docs.alpaca.markets/us/docs/options-trading-overview)
- [Alpaca Docs — Options Trading](https://docs.alpaca.markets/us/docs/options-trading)
- [Alpaca Docs — Options Level 3 Trading](https://docs.alpaca.markets/us/docs/options-level-3-trading)
- [Alpaca Docs — Regulatory Fees](https://docs.alpaca.markets/us/docs/regulatory-fees)
- [Alpaca — Brokerage Fee Schedule (PDF, revised 2026-09-01)](https://files.alpaca.markets/disclosures/BrokFeeSched.pdf)
- [Alpaca Support — What determines the margin for my account?](https://alpaca.markets/support/determine-margin-account)
- [Alpaca Support — How to enable options trading if I already have an account?](https://alpaca.markets/support/how-to-enable-options-trading-if-i-already-have-an-account)
- [Alpaca Docs — Multi-leg (Level 3) Options Trading in Paper](https://docs.alpaca.markets/changelog/multi-leg-level-3-options-trading-in-paper)
