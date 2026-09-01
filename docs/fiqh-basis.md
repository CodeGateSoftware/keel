# The fiqh basis of keel's encoded rulings

## What this document is and is not

This document is written for a Muslim developer deciding whether to trust keel with money. It
states, ruling by ruling, what Shariah reasoning is encoded in this repository, and where in
the repository each ruling's source lives — so the basis is auditable by someone who does not
already know where to look. It is scholarship by reference: every ruling below carries a
citation to an in-repo source, and the enforcement code carries the same citations in its
comments.

What this document is not: a fatwa, or a claim that keel can produce one.

> keel is not a fatwa engine. It is an enforcement engine for a ruling you supply.

No scholar has reviewed this document; whether such a review happens is deliberately a
separate, still-open question (#289), and nothing here should be read as one having occurred.

## How to read the citations

`§N.x` means source N, section x → the file
`docs/superpowers/references/trading-knowledge-base/sources/source-NN.md`, section `N.x` —
the knowledge base's own convention, stated in its README index. The sources are extracts of
real books, papers, and council resolutions, kept in-repo; the README row for each source
records what was fetched and from where, so a claim is checkable against its origin.

Two honesty rules the knowledge base holds, and this document inherits:

- Where a source is silent on something, we say "not stated" — a gap is never papered over
  with a paraphrase that sounds like a ruling.
- Where schools and councils disagree, the disagreement is named, with both sides — never
  flattened into "scholars say". §71.7/§71.8's opinion map is the worked example:
  prohibitions, permissions, and the conditions each attaches, all recorded.

## What is attested versus what is computed

The core claim of keel's compliance design, in the screen's own words
(`keel/compliance/screen.py`): market facts are computed, Shariah classifications are
**ATTESTED, never inferred**. Whether a token's core purpose is a haram sector (§28.4),
whether it is asset-backed `'ayn` or a claim `dayn` (§65.5/§67.2), and whether it pays a
riba-like yield are questions of fact-plus-scholarship about the world. No module in this
repository derives them from candles, and none pretends to. A human records them, with a
source and a name, via `keel assets attest`.

And when the attestation is absent: **unknown is a rejection**. An unattested asset is not
"probably fine" — it is unknown, and the screen fails closed on unknown. The same posture
runs through the rails: rail 17 fails closed on a missing attestation because "silence is not
evidence of possession" (`keel/execution/guards.py`).

## The rulings encoded, and their sources

### The curation screen (`keel/compliance/screen.py`)

A CURATION gate — admission to the allowlist, checked once, not per-trade. §28.4 is explicit
that sector and backing are "a listing criterion, checked once when curating the allowlist,
not per-trade". The attested axes:

- **Sector (§28.4).** A token whose core business is a haram line — gambling, alcohol,
  riba-based lending, and the rest of `HARAM_SECTORS` — is rejected. Aave/Compound-class
  lending tokens fail here (§41.1's readings, confirmed at §65.10).
- **Backing (§65.5/§67.2).** `'ayn` (an owned thing) passes; `dayn` (a debt claim on an
  issuer) is refused — trading a pure claim is a different contract under different rules.
  An `'ayn` asset backed by gold or silver draws a warning that §65.5's stricter
  `bay' al-sarf` regime applies: no deferment, and a 72-hour settlement bound.
- **`pays_yield` (§28.4, the riba screen).** Rejects with the screen's exact failure wording:
  "the asset carries a guaranteed/expected return for holding it, which is riba-like
  (§28.4); holding it is not a bare spot position". The field's semantics are BARE HOLDER,
  not "staking exists": it asserts what holding the asset *without* staking or lending earns.
  Established by fetching the docs, not by assumption — Solana's staking documentation says
  rewards require delegation ("In order to earn staking rewards … the tokens in a stake
  account must be delegated to a validator"), with no rebasing, so "Bare holding earns
  nothing, which is exactly what the field asserts."
  (`docs/experiments/2026-08-07-unvalidated-skip-set-reassessment.md`).
- **Wrapper/instrument (§71.4a).** The allowlist is not juristically homogeneous, so admission
  names the CONTRACT, not just the underlying. Only `spot` is admitted; CFD, future,
  perpetual, option, and leveraged-token listings are refused, recorded via
  `keel assets attest-instrument`. Unattested fails closed.

The computed axes — history depth, liquidity, settlement quotability — are market facts
about our own cache, recomputed freely. Of everything the screen checks, a documented
exception (`keel assets exempt`) may waive only ONE criterion today: `history`
(`WAIVABLE_CRITERIA` is `frozenset({"history"})`). Liquidity, settlement, and the spot
instrument shape can NEVER be waived, and neither can any Shariah criterion — nothing in
the screen consults a waiver for them, and the CLI's `--criterion` choice is restricted to
that set. Expanding it is a deliberate future decision, not a default.

### Rail 1 — allowlist enforcement (`keel/execution/guards.py`)

Per-trade and un-overridable: every intent, DCA included, must be for an allowlisted asset.
This rail enforces the attested rulings above mechanically on every order; the ruling itself
lives in the attestation, never in the rail.

### Rail 17 — withdrawal capability, `qabd` §65.4

The one rail that encodes fiqh as an executable check. Ayub's constructive-possession test
holds that possession is completed when the vendor sets the asset aside and "there is nothing
to prevent the buyer from taking physical possession from the vendor whenever he desires";
the two-part test is "(i) the buyer bears the risk and reward, and (ii) nothing prevents the
buyer from taking delivery whenever he wishes" (§65.4). An asset we cannot withdraw is an
asset we may not validly POSSESS — so acquiring more of it is the thing to stop.

The operative test is tri-sourced: "Three sources now converge on the identical operative
test: possession is the ability to dispose, not physical custody (§65.4 Ayub · §67.1 OIC
53/4-6 · §71.5 AAOIFI SS 18 3/5 via SRB)" — §71.5's own summary; §67.1 is Al-Jarhi,
Abuzaid & Oweida's *Handbook of Islamic Finance* (2022) quoting the OIC Fiqh Academy
resolution (Res. 53/4-6) that holds electronic constructive possession sufficient.

Mechanics: the operator attests with `keel withdrawals attest`; the attestation is live-read
on every intent and expires after 7 days (`WITHDRAWAL_ATTESTATION_TTL_SEC`,
`keel/execution/executor.py`) — "a stale attestation is no better than none". ENTRIES ONLY,
like rails 11/16: existing holdings are already ours, and forcing a sale to "fix" a
withdrawal freeze would be strictly worse than holding through it. Unknown fails closed.

### Rails 18/19 — settlement currency and spot-instrument shape

Charter, not fiqh-derivation: "Spot-only is this agent's CHARTER, not an operator preference"
(`guards.py`, rail 19's comment). Rail 18 confines settlement to the operator's configured
currencies (default USD/USDC — a config field, the escape hatch); rail 19 requires the
product id to be a well-formed spot pair. Both are justified by measurement, not doctrine:
the feasibility study `docs/experiments/2026-08-05-coinbase-asset-class-feasibility.md`
verified by execution which instrument classes exist on the venue and what each rail closes.
The fiqh content — that derivatives and difference-settlement are impermissible — is real
(§65.6: what makes speculation *maisir* is non-ownership, non-delivery, difference-settlement)
but the RAILS are the charter enforcing it.

### Purification (§65.9) and idle-balance rewards (§56.3)

`keel/compliance/purification.py` implements Ayub §65.9: interest/reward credits are
segregated from realised P&L and the equity base, reported as owed to charity, never
recognised as profit — and zakat is computed on purified wealth (§33.1). It is REPORT-ONLY:
the agent never disposes of funds. The record of what it found in this project's own imported
history is `docs/experiments/2026-07-20-income-purification.md`.

The preventive half is §56.3: Coinbase pays USDC rewards on idle balances, that interest is
riba, and it accrues with no order placed — so no rail can catch it. Disabling rewards at the
account level is the operator's obligation, listed first in
`docs/operator-runbook.md`.

### The remaining rails — prudential, not fiqh

Twenty rails exist (1–14, 16–21 — there is no rail 15). Of these, rails 17 and 21 encode a
fiqh ruling, and rails 1/18/19 enforce what the screen and the charter admit. The rest are
PRUDENTIAL — risk and discipline, justified by trading evidence, carrying no religious
claim:

| rail | what it does | basis |
| --- | --- | --- |
| 2, 3 | per-order and per-day spend caps | risk discipline |
| 4, 5, 6 | exposure cap, correlation-aware sizing, concentration cap | risk discipline |
| 7 | min-move / anti-scalping floor | trading justification only — see below |
| 8, 9 | no averaging into losers, no stop-widening | risk discipline |
| 10 | sells must cite a defined rule | audit discipline |
| 11, 16 | drawdown and consecutive-loss breakers | risk discipline |
| 12 | stale-feed + kill-switch, fails closed | operational safety |
| 13, 14 | spend only the settled quote currency; monthly allowance cap | operational safety |
| 20 | trade-scope veto on live entries for a venue credential nobody has attested for trading | operational safety |

**Rail 21 is the second fiqh rail, and it was added because the ruling had a hole under it
(#667).** keel's refusal to go short is structural — no rule can express a short and the engine
builds every entry as a BUY — but structure governs what keel DECIDES, not the quantity that
reaches the venue. A SELL was sized from keel's own ledger, and the ledger runs high: a venue
that takes its taker fee out of the received base leaves less than the order said, a partial
fill leaves less still, and an operator who moves coins out of the account tells keel nothing.
Ask a venue for base that is not there and a cash account rejects it — but a margin-enabled one
fills the difference by opening a short.

That is *bay' ma la yamlik* (بيع ما لا يملك), "do not sell what you do not possess" — Ayub
Ch 6.5.1 (§65.4), with Ch 5.4.2 (§65.11) recording that "short-selling has been prohibited by
almost all scholars" because the subject matter must be "capable of ownership/title, capable of
delivery/possession". It is a more direct anchor than riba for this particular failure, and the
difference is load-bearing: the oversell is impermissible before any interest is charged, so a
riba-framed defence does not reach it at all.

Two mechanisms, deliberately split. `executor._clamp_to_held` reduces an order that is too big
for a position that really exists — down only, never up. Rail 21 refuses the one case the clamp
will not touch: a venue that affirmatively reports holding nothing while the ledger expects
something. Neither cancels a protective order, which is why
`_record_observed_fill_quantity`'s refusal to auto-resize still stands beside them unchanged.

⚠️ The rail fails **OPEN** on an unknown holding, the deliberate inverse of rails 12/13/17. A
refused BUY costs nothing; a refused SELL strands a position that wanted out. An unreadable
balance is not evidence the position is gone, and this is the one place in the engine where
"unknown is a rejection" would do more harm than the hole it closes.

What remains open at the venue boundary is #666: on a cash account every case above is a
rejected order rather than a short, and keel has no cash-account posture check on Coinbase —
`verify_cash_account` exists only on the Alpaca adapter.

Beside the rails — not among them, and not numbered — sits one routing-time check with the
same prudential character: the **max-spread entry gate** (#350, `keel/execution/executor.py`)
refuses a live BUY whose previewed book shows `(best_ask − best_bid) / mid` at or beyond
`execution.max_entry_spread_pct` (default 50bp). It is not a `guards.check` rail because the
rails are broker-less by design and the book exists only in the venue's preview response.
BUY-only (exits must execute), live-only (paper fills are synthetic and see no book), and
fails closed on an unreadable book — the same fail-closed family as rails 12/13/17, justified
by trading evidence (the per-leg cost the backtest assumes for a liquid book), carrying no
religious claim.

Rail 7 carries a correction this repository records prominently: §65.6 holds that
"speculation per se, which means sale/purchase keeping in mind possible change in prices in
the future, is not prohibited" — what makes speculation *maisir* is non-ownership,
non-delivery, or difference-settlement, **not frequency**. So the anti-scalping rail "keeps
its trading justification and LOSES its shariah claim" (the KB's words for §65.6). It stays
because churn costs taker fees, not because churn is haram.

## What keel deliberately does not decide

- **Whose ruling is right.** The ruling lives in your attestation, not in the code, so two
  operators following different schools get different answers from the same code, by design
  (see `CONTRIBUTING.md`, "Governance: rulings vs. machinery").
- **Whether a given token qualifies as *Māl*.** That is a judgement of fact-plus-scholarship
  the screen defers to the human attestor — the DOGE question below is the live example.
- **School differences.** Where sources diverge (§66.6 records identical retail FX ruled
  haram by one jurisdiction and halal by another), keel records both and enforces whichever
  ruling the operator supplies; it does not adjudicate.
- **Anything about an asset nobody has attested.** Unknown is a rejection, not a default
  pass — refusing to decide is the decision.

## Known open questions

Stated, not hidden — each is a place where keel's encoded behaviour could be wrong:

- **ATOM dilution.** Cosmos Hub's own documentation (docs.cosmos.network) says: "Delegate
  your ATOM to one or more of the validators on the Cosmos Hub blockchain to earn more ATOM
  through Proof-of-Stake"; and, per stakingrewards.com/asset/cosmos as examined at the
  time, ATOM has no supply cap, and its dynamic inflation rate adjusts algorithmically to
  target the staking ratio (~12.66% inflation, ~19.49% staking APY as of 2026-08-14, the
  date of examination). Inflation is uncapped and
  dynamic, and newly minted ATOM accrues only to bonded delegators — so a bare holder is
  structurally diluted, at an algorithmically maintained rate: value transfers from
  non-stakers to stakers, and the transfer does not fade. `pays_yield=NO` remains correct on
  the screen's own axis (bare holding pays nothing) and is a separate question from this one.
  There is no settled answer in this repository.
- **Staking generally (§65.14).** Contested, not settled: the honest position is "the
  question is genuinely contested, we have no scholarly determination in hand, our mandate has
  no need of it, and §29.2 directs us to the conservative branch where scholars diverge."
  keel stakes nothing — no module in this repository can stake, so no code excludes staked
  positions — which is why the §29.2 conservatism belongs to the premise question below,
  not to an exclusion keel performs. Stop implying staking is settled riba.
- **The foundational premise itself (§71.1/§29.2).** Every ruling above presupposes that
  crypto is Shariah-recognised tradable property, and on that the highest available
  authority has declined to rule. IIFA Resolution 237 (§71.1) convened a dedicated symposium
  on electronic currencies, debated the matter at its 24th session (Nov 2019), and ISSUED
  NO RULING — it identified as unresolved exactly this question ("Is cryptocurrency
  considered by Shariah a real-valued property and a tradable item?"), noted the
  significant risks and the instability of their transactions, and referred the matter back
  for further research. A withheld ruling is not a prohibition; it is also not a
  permission. keel's premise that BTC/ETH-class assets are tradable property is a
  well-supported INTERPRETIVE POSITION held on §29.2's conservative branch, not a settled
  ruling — and keel does not get to cite the same Academy's Res. 53/4-6 as authoritative
  on `qabd` (§67.1) while treating it as silent here.
- **DOGE (§86.4).** "A token that has no genuine use or benefit and survives only because
  people hope to sell it to someone else at a higher price may FAIL to qualify as *Māl*" —
  and the source's own lean is "Strong lean: EXCLUDE DOGE." DOGE also has no supply cap.
  Whether "no underlying purpose" is disqualifying "is exactly the kind of judgement the
  screen defers to a human" (`docs/experiments/2026-07-20-candidate-universe.md`) — deferred,
  not decided.
- **ZEC and the rest of the deferrals.** The candidate-universe record lists the open
  questions the attestation step has to answer and "which this agent must not answer".

## How to disagree

The route the architecture already provides — record your own ruling locally:

- **Attest your own classification.** `keel assets attest --asset --sector --backing
  --pays-yield --source --attested-by` writes to *your* database; `--source` and
  `--attested-by` are required, because an unsourced claim is not evidence. Your deployment
  then follows your ruling, upstream stays neutral, and the audit trail records exactly who
  said what.
- **Document exceptions where the screen allows them.** `keel assets exempt` may waive only
  one criterion today — `history`: never a Shariah criterion, and never liquidity,
  settlement, or the spot instrument shape.
- **To change a classification for everyone**, that is a PR of a different kind:
  `CONTRIBUTING.md` requires a cited source and discussion before merge — a classification
  with no source behind it is not mergeable, however confident the author.

## Scholarly review status

**No scholarly review of keel's fiqh basis has occurred.** Not by a named scholar, not by a
council, not by anyone. The basis is the operator's reading of the sources this document
cites — Ayub (§65), the OIC/AAOIFI/IIFA materials (§67, §71), Mufti Faraz Adam's papers
(§85, §86) — extracted into the knowledge base at
`docs/superpowers/references/trading-knowledge-base/` and mapped into code here, published
precisely so that reading can be audited and challenged. Each operator remains responsible
for their own attestations. Until this section gains a dated review addendum (below), the
status is plain: not reviewed.

### What a review would cover

A review, should one ever happen, is a review of the mapping from sources to code — and
its scope is this, in full:

- **The encoded-rulings table above** — whether each screen and rail axis faithfully reflects
  the section it cites: §28.4 for the sector and riba axes, §65.5/§67.2 for backing, §71.4a
  for the instrument shape, §65.4 for rail 17's `qabd` test.
- **The knowledge-base extractions** — whether each source file under
  `docs/superpowers/references/trading-knowledge-base/sources/` is faithful to the text it
  was extracted from, including where it records "not stated" rather than papering over a
  gap.
- **The open questions named above** — ATOM dilution, DOGE's *Māl* qualification, the
  premise itself — as questions about the reading, not about any operator's recorded
  attestations.

### What a reviewer is NOT endorsing

A reviewer would be reviewing the mapping from sources to code, nothing more. Explicitly
not endorsed:

- **Not the trading strategy or its performance.** The honest measured result is linked from
  the README's first screen and is nothing anyone is asked to endorse.
- **Not the prudential rails.** They are risk discipline carrying no religious claim (§65.6's
  correction); a fiqh review has nothing to say about them either way.
- **Not a ruling that crypto is tradable property.** The §71.1 non-ruling stands: reviewing
  the machinery does not settle the premise the machinery presupposes.
- **Not any particular operator's attestations.** Those carry the operator's own source and
  name; a review of this document does not review them — each operator owns their own.
- **Not an endorsement of trading anything.** keel states its honest result and disclaims
  advice; a review of the mapping adds no permission to trade.

### How a review is recorded

If a review ever happens, it is recorded as a dated addendum to this section, naming the
reviewer, the scope reviewed, the findings, and what changed as a result — versioned in git
like everything else in this repository. Until such an addendum exists, the status is "not
reviewed", and it can ratchet one way only: from not-reviewed to
reviewed-with-a-named-scope, never to an approval with no scope attached.

### The outreach shortlist (a plan, not a claim)

Approaching a reviewer is the operator's action, and it has not been taken as of this
writing. The shortlist, for that day if it comes: the Islamic finance programmes at IIUM,
INCEIF, and Durham, and established Islamic fintech practitioners — approached with this
note, ready to send:

> keel is an open-source enforcement engine for Shariah rulings an operator supplies —
> classifications are attested, never inferred, and enforced deterministically
> (https://github.com/CodeGateSoftware/keel). I am asking for a review of the mapping from
> the cited sources — Ayub, the OIC/AAOIFI/IIFA materials, Mufti Faraz Adam's papers — to
> the encoded screen and rail behaviour, as documented in docs/fiqh-basis.md. The review
> would signify only whether the mapping is faithful to those sources; it would not endorse
> the trading strategy, the prudential rails, the premise that crypto is tradable property,
> or trading at all.

## Sources index

- `docs/superpowers/references/trading-knowledge-base/sources/source-65.md` — Muhammad Ayub,
  *Understanding Islamic Finance* (Wiley 2007): the foundation source (§65.4 `qabd`, §65.5
  backing, §65.6 speculation, §65.9 purification, §65.14 staking).
- `docs/superpowers/references/trading-knowledge-base/sources/source-67.md` — Al-Jarhi,
  Abuzaid & Oweida, *Handbook of Islamic Finance* (ASBÜ Yayınları, 2022), quoting OIC Fiqh
  Academy Res. 53/4-6 on electronic constructive possession (§67.1), gold/`sarf` (§67.2).
- `docs/superpowers/references/trading-knowledge-base/sources/source-71.md` — IIFA Res. 237,
  SRB (AAOIFI SS 18 3/5), SC Malaysia's `ribawi` classifier (§71.4a), digital `qabd` (§71.5).
- `docs/superpowers/references/trading-knowledge-base/sources/source-85.md` — Mufti Faraz
  Adam, *Bitcoin: Shariah Compliant?* — the keystone for the BTC/ETH premise.
- `docs/superpowers/references/trading-knowledge-base/sources/source-86.md` — Mufti Faraz
  Adam, *Is Crypto Halal?* — *Māl* qualification and the DOGE reading (§86.4).
- `docs/superpowers/references/trading-knowledge-base/README.md` — the index: per-source
  rows, the citation convention, and the opinion maps.
- Experiment records cited above, under `docs/experiments/`:
  `2026-08-07-unvalidated-skip-set-reassessment.md` (bare-holder semantics),
  `2026-07-20-candidate-universe.md` (deferred questions),
  `2026-07-20-income-purification.md` (what purification found),
  `2026-08-05-coinbase-asset-class-feasibility.md` (rails 18/19).

### External published scholarship consulted (method support, not attestation sources)

Works consulted for method and framing — how possession and speculation are analyzed — with
the explicit note that **none is an attestation source, none has been reviewed by its author
for this repo, and none endorses trading or keel**. Analysis:
[`docs/research/`](research/).

- Abu Jib, Mu'taz & Hashem, Ashraf — *أنواع المعاملات الرقمية المشفرة* ("Types of Encrypted
  Digital Transactions"), research paper for the International Islamic Fiqh Academy (Jeddah)
  Seminar on Electronic Transactions, September 2019 (ARSI): taxonomy of crypto-assets; its
  central conclusion — per-type precise definition before any ruling — is the fiqh-side
  statement of this document's per-product attested screening.
- Lahlou, Mohamed Talal — *Marchés financiers islamiques et risque de spéculation*
  (doctoral thesis, Université Mohammed V – Rabat, 2020); "Speculative situations in an
  uncertain environment: innovative proposal of a definition and distinctive tree of
  speculative situations" (2019); "La règle de la récupération et ses fondements
  jurisprudentiels", *Cahiers de la Finance Islamique* n°7 (2014). The thesis line treats
  speculation as structurally definable situations (a classification tree, applied to the
  transaction's shape) — the same genus as this repo's rails; the 2014 article is the
  French-language jurisprudential pedigree of rail 17's possession (*qabd*) enforcement.
