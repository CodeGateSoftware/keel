# Dr. Mohamed Talal Lahlou's scholarship on speculation risk — analysis for keel's support

**Date:** 2026-08-18 · **Status:** source analysis, proposer-never-decider. **This document claims
no endorsement, review, or involvement by Dr. Lahlou.** His publicly stated skeptical position on
trading stands unmodified by anything written here. What follows maps his *method* — how
speculation and possession are analyzed — onto keel's machinery, because the methods converge.
Whether trading *should happen* is a question this analysis does not touch.

## Sources analyzed (verified bibliography)

1. **Doctoral thesis:** *Marchés financiers islamiques et risque de spéculation* — defended
   March 2020, Faculté des Sciences Juridiques, Économiques et Sociales, Université Mohammed V
   (Rabat), supervisor Pr. Mohammed Nadif.
   [Full text on ResearchGate](https://www.researchgate.net/publication/340174275_Marches_financiers_islamiques_et_risque_de_speculation)
   (DOI 10.13140/RG.2.2.23496.26888).
2. **"Speculative situations in an uncertain environment: innovative proposal of a definition
   and distinctive tree of speculative situations"** (2019, pre-defense paper; listed on his
   [Google Scholar profile](https://scholar.google.com/citations?user=5BcgUGEAAAAJ&hl=fr)).
3. **"La règle de la récupération et ses fondements jurisprudentiels"** — *Cahiers de la
   Finance Islamique* n°7 (EMS Strasbourg, 2014) — the recovery/transfer rule: taking
   possession of commodities before resale, and its jurisprudential foundations
   ([issue recension](https://ribh.wordpress.com/2014/11/10/cahiers-de-la-finance-islamique-de-lems-strasbourg-n-7/);
   the English-titled line "The need to collect and transfer commodities and its economic
   impacts" is the same work family).
4. **"Confrontation analytique entre finance classique et islamique"** — *Cahiers de la
   Finance Islamique* (2014).
5. **"Explanatory theories of financial speculation"** (listed on Scholar).

**Access note, stated honestly:** the thesis and papers are bot-gated at ResearchGate; this
analysis is grounded in the verified titles, the thesis's defense record, the abstract
fragments visible in search, the issue recension, and secondary characterizations in citing
academic works (a 2023 ULiège master's thesis quoting his principles list, under "(Lahlou,
2018)"). Where a claim below rests on a title or a secondary source rather than a read full
text, it is marked *[inference]*. Nothing here should be represented as his words beyond
what is quoted.

## Findings, mapped to keel

**F1 — Speculation as a structurally definable object, with a decision tree.** The 2019
   paper's title is the finding: a proposed *definition* of speculative situations and a
   *distinctive tree* classifying them. That is, ontologically, what `keel/execution/guards.py`
   is: judgment compiled into a tree of checkable conditions. His scholarly method and keel's
   engineering method are the same genus — turn contested judgment into explicit, auditable
   structure so it can be applied consistently and criticized precisely. *[Mapping of method,
   not of content — the specific conditions of his tree were not readable at source.]*

**F2 — Situations over intentions.** The thesis fragment visible in search — "situations
   spéculatives, même si l'intention de l'opérateur est…" — and the very framing
   "speculative *situations*" indicate a structural test applied to the transaction's shape
   rather than the operator's inner state. This is exactly keel's posture: rails evaluate
   order and market structure (leverage present? churn rate? spread? drawdown?), never
   intent, and the promotion gate judges measured behavior, not claimed conviction. An engine
   *cannot* read intentions — a design that happens to match a fiqh methodology that does not
   require it. *[Inference from title + fragment; flagged.]*

**F3 — The recovery rule (*règle de la récupération* / transfer) is rail 17's French-language
   pedigree.** His 2014 Cahiers article works out the jurisprudential foundations of taking
   possession of goods before resale. keel's rail 17 encodes the same principle as an
   executable check: an asset that cannot be withdrawn from the venue may not have been
   validly possessed, so entries halt until withdrawal capability is attested. The
   knowledge-base sources for that rail are Anglophone (Ayub §65.4; OIC/IIFA resolutions);
   his article gives it an independent line of French-language scholarship to cite. *[The
   article's full argument was not readable; the mapping rests on the verified title and the
   recension's topic placement.]*

**F4 — The enumerated prohibitions match keel's encoded constraint set.** As characterized in
   a citing thesis (under "(Lahlou, 2018)"): usury, *gharar*, *maysir*, monopoly, price
   controls, and fraud are strictly prohibited; only real assets; licit value chains only;
   profit-and-loss sharing. keel's rails are the trading-execution subset of exactly this
   list: no leverage or interest-bearing instruments (*riba*), spot-only with no derivatives
   (*gharar* control), anti-churn minimum-move floor and no-martingale/no-stop-widening
   (*maysir*-adjacent protections), per-product attested screening excluding illicit
   value chains. Monopoly and price controls are state-scale phenomena keel deliberately
   does not decide (fiqh-basis.md, "What keel deliberately does not decide") — recorded
   honestly rather than silently dropped.

**F5 — Real-economy grounding and the anti-imposture stance support keel's honesty
   posture.** His recurring theme — finance anchored in the real economy, and open critique
   of Islamic-finance industry window-dressing — is the scholarly temperament keel's
   publishing discipline tries to imitate: net-negative results published in full, no
   compliance label for its own sake, "enforcement engine, not fatwa engine." keel makes no
   religious claim and states that no scholarly review has occurred.

**F6 — Islamic economics as a complete system ↔ executable institutions.** He characterizes
   Islamic economics as a system with "principles, rules, theories, axioms and institutions"
   interacting in a defined scope (as quoted in a citing thesis). keel is that idea taken one
   step down the stack: the rules-as-institutions made executable, deterministic, and
   auditable.

## What this analysis does NOT establish

- It does not suggest Dr. Lahlou approves of crypto trading, of trading at all, or of keel.
  His skeptical position is on record and is precisely why an adversarial review request
  (rather than an endorsement request) is the correct approach — see the operator's outreach
  draft of 2026-08-18.
- His works are **method and framing support for the reading list**, not attestation sources:
  the thesis analyzes speculation; it does not (so far as the accessible record shows) issue
  a per-instrument classification keel could cite in `keel assets attest`. Attestations
  remain operator-recorded from qualified sources, per fiqh-basis.md.
- No claim is made that his tree and keel's rails classify identically — the comparison is
  of genre (explicit, structural, auditable), validated only if and when a review compares
  the actual conditions.

## Actions taken from this analysis

1. `docs/fiqh-basis.md`'s sources index gains an *external published scholarship* subsection:
   the Abu Jib & Hashem 2019 Fiqh Academy taxonomy paper (completing the recommendation from
   the 2026-08-18 source review) and the Lahlou works above, each labeled by what they
   support (method/pedigree) and what they are not (attestation sources).
2. The operator's outreach asks him for an adversarial review of the mapping, explicitly not
   an endorsement.
3. Watch item, unchanged: AAOIFI/IFSB crypto-asset standards remain the natural future
   attestation sources.
