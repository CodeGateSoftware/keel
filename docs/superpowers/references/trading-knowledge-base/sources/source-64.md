[← Knowledge Base index](../README.md)

## Source 64 — Three short academic papers: "Methods Matter" (Cliff & Rollins, IEEE CIFEr 2020), "Explainable AI (XAI) in Investment Decision-Making" (Babaei & Giudici, Academia AI & Applications 2025), and "Developing Actionable Trading Strategies for Trading Agents" (Cao, Luo & Zhang, IEEE IAT 2007)

> Three unrelated short papers (21pp total) bundled into one source file because each is thin on
> its own. None targets crypto, none is written for our long-only spot context, and none discusses
> the §5/§6.4 LLM-asymmetry directly (unlike §35, which is a purpose-built "AI trader" guide). Value
> here is indirect: one paper is a **methodology cautionary tale** that sharpens the project's
> existing prove-it-in-the-harness posture; one is a **compliance-adjacent explainability method**
> relevant only if a future ML/LLM component is ever built; one is a **1997-2006-vintage academic
> strategy paper** that mostly re-derives ground the KB already owns (breakout/filter rules, GA
> optimization, walk-forward testing) via a weaker, older toolset than Source 54 (Kaufman) already
> supplied. **Genuinely thin. No new rails, no new rule class.** The one item worth carrying forward
> concretely is §64.1 (methodology skepticism) and §64.7 (an explainability mechanism to keep in
> the back pocket for the deferred LLM feature).

---

### 64.1 ⭐ "Methods Matter" (Cliff & Rollins, 2020) — a methodology cautionary tale that sharpens the deterministic-first, prove-it-in-the-harness posture

The paper re-tests the "pecking order" of four well-known public-domain AI/ML trading-agent
algorithms from the research literature (ZIC → ZIP → GDX → AA, each published as an improvement on
the last, in venues including *Artificial Intelligence* and IJCAI) plus two deliberately "no
intelligence" strategies (SHVR — a parasitic one-tick-shaver; **GVWY** — an agent that does zero
computation and simply quotes its assigned limit price with no market awareness at all).

The published dominance hierarchy (**AA > GDX > ZIP > ZIC**) was established using what the authors
call a **"somewhat minimal approach to experimental evaluation"** — a few thousand test sessions, a
small number of market scenarios, a market simulator that is *sequential and synchronous* (S&S: only
one trader "thinks" at a time, unrealistically) rather than *parallel and asynchronous* (P&A, which
better reflects real markets where faster reaction times matter). The authors re-ran the comparison
with **over 1.1 million simulated market sessions** across a purpose-built P&A simulator (TBSE) with
a dynamically-varying equilibrium price, and found:

- Three to seven of the pairwise dominance relationships **invert** depending on simulator realism
  (S&S vs. P&A) and whether the market's clearing price is held static or allowed to move — i.e.,
  *the same algorithms rank differently purely as a function of evaluation methodology*, not
  algorithmic sophistication.
- **GVWY — a strategy with literally no logic beyond "quote my assigned price and do nothing else" —
  consistently outperforms ZIP and GDX**, two AI/ML strategies published in prestigious AI venues and
  once considered best-in-class, once the evaluation is done at adequate scale and realism.
- Their explicit conclusion: **"the AI/ML trading strategies were good answers to the wrong
  question"** — the original papers' methodological shortcuts (small samples, unrealistic
  simulators, a narrow slice of population ratios) produced results that "could easily have been
  revealed... more than a decade ago" had anyone tested at scale, and are now shown to be, in the
  authors' words, **"bluntly, just wrong."**

**Why this matters for `keel`, directly, not by analogy:** this is not a claim that "simple beats AI
in general" (see §64.2 — the specific algorithms don't transfer to our problem at all). It is a
**documented, adversarially-reproduced case of a whole sub-field's published superiority claims
collapsing under (a) larger sample size, (b) a more realistic simulator, and (c) testing across the
full range of population/parameter ratios instead of one or two convenient ones.** That is exactly
the failure mode the project's backtest/promotion pipeline is built to refuse to reproduce:

- It is independent, external confirmation of why `strategy/promotion.py`'s **min-sample floor
  (100 trades / 5yr)** and **walk-forward + OOS/feedback firewall** (§54.10) are load-bearing, not
  bureaucratic — a strategy that "wins" on a thin sample or a single convenient backtest window is
  exactly what this paper shows can invert with more rigorous testing.
- It is a **direct, mechanical argument against ever trusting a paper's or vendor's reported AI-trading
  edge at face value** — whether that's a Substack backtest, an academic AI/ML trading paper, or (most
  relevant to the deferred LLM feature) an LLM's own claim that a strategy "should" work. Under the
  §5 asymmetry, an LLM-proposed strategy gets **no credibility discount** for being AI-proposed — it
  must clear the *identical* deterministic backtest → paper → promotion gate as a human-authored rule,
  and this paper is the sharpest available evidence for *why*: even peer-reviewed, IJCAI/AI-journal
  published AI/ML trading algorithms, taken at face value from their original papers, do not hold up.
- Practically, it reinforces (does not add) the standing rule: `keel simulate` results should be
  read at **the largest feasible sample size** and **the actual market-scenario diversity we can
  generate**, not a single backtest window — the same lesson Kaufman's §54.10/§54.11 robustness bar
  (`% profitable tests ≥ ~70%`, plateau not peak) already encodes.

**Maps to:** `strategy/backtest.py`, `strategy/promotion.py` (methodology rigor — reinforces §54.10,
§54.11); the deferred LLM feature's governing principle §5/§6.4 (an LLM's proposal earns no
evaluation shortcut).

### 64.2 ⛔ Scope caveat — the specific algorithms and market model do not transfer (halal + instrument-model flag)

The paper's test-bed is a **Continuous Double Auction (CDA)** — the mechanism behind most real-world
exchanges, but here modeled as **symmetric populations of buyers and sellers, each side of which
posts live two-sided bid/ask quotes into a shared limit order book (LOB)**, competing to transact at
the best available price. ZIC/ZIP/GDX/AA/SHVR/GVWY are **quote-setting algorithms for agents acting as
both liquidity-providers and liquidity-takers on both sides of the book simultaneously** — i.e., this
is **market-making / two-sided quoting research**, not directional entry/exit signal research.

This is exactly the caveat flagged in the brief: *"much agent-based-market literature assumes short
selling and market-making — flag where that makes a result inapplicable."* It applies squarely here:

- `keel` is a **long-only, spot-only, price-taking** agent — it submits market/limit *orders against
  an existing book*, never posts continuous two-sided quotes, never has a "seller" role requiring
  inventory it doesn't own, and never profits from bid-ask spread capture. None of ZIC/ZIP/GDX/AA/
  SHVR/GVWY's *quoting logic* is adoptable — there is no analogous "our side of the trade" for a
  spot, buy-and-hold-then-sell agent.
- No riba/leverage/derivative content in this paper specifically (it is pure market-microstructure
  auction theory, not an investment product), so nothing to exclude on those grounds — the exclusion
  here is **structural/instrument-model inapplicability**, not a shariah violation.
- The only transferable content is the **methodology** finding in §64.1, which is evaluation-of-claims
  reasoning, not a strategy.

### 64.3 ⧉ Filter Rule FR(δ) (Cao, Luo & Zhang, 2007) — reinforces the existing Donchian/breakout family, no new rule

`TRADING STRATEGY 1` in the paper: track a rolling `high(t)`/`low(t)` (highest close-to-date /
lowest close-to-date, reset on new extremes), go long when price closes above `high(t)·(1-δ)`,
exit/reverse when price closes below `low(t)·(1+δ)`, with `δ` the one tunable parameter (a
percentage band, optimized via GA/Sharpe — see §64.5). This is **the same structural idea as the
Donchian-channel breakout already fully specified and shipped** via §23.1/§27.1/§54.11/§54.14 (Turtle
spec) — a rolling-extreme breakout with a percentage/ATR-scaled confirmation band. Nothing new here;
logged only to note the family recurs independently in the academic literature (further evidence, in
the spirit of §54's "trend-following works across markets" finding, that breakout-style entries are a
recurring, independently-discovered edge rather than one author's idiosyncratic invention).

Long-only translation note (per KB convention): the paper's `FR(δ)` reverses to a short position on
the low-side signal (`position(t) = -1`) — under our adaptation rules this becomes the existing
**exit/don't-buy filter**, not a short entry. Already the KB's standard translation; no new work.

### 64.4 Enhanced filter's "time hold filter" `h` — a minor, distinct anti-whipsaw debounce (new nugget, low priority)

`TRADING STRATEGY 2` (the "enhanced" filter rule) adds, alongside percentage-band filters for high
and low sides separately (`δH`, `δL` — already covered by the ATR/percentage-band concept in
§54.3/§54.15), a **time hold filter `h`**: once a position is opened, all *new* trading signals are
ignored until `h` transactions/time-periods have elapsed. The paper's own ablation (Fig. 3) shows this
measurably changes cumulative payoff vs. the un-filtered version, though it doesn't isolate whether
`h` alone was responsible or the interaction with the band filters.

This is subtly **different from anything currently in the KB**:
- It is *not* `max_hold` / a time-based exit (§57.2) — that recycles capital out of a stale position
  after too long; `h` is the opposite: a **minimum time before the strategy will act on any new
  signal at all**, i.e., a re-entry/re-signal cooldown or "debounce" that suppresses rapid signal
  flip-flopping right after entry.
- It is *not* the anti-scalping min-move rail (§4.1) either — that rail is **price-magnitude**-based
  (ignore moves smaller than X%); `h` is **time**-based (ignore *any* new signal, regardless of size,
  for `h` periods after the last one fired).

**Candidate for the `exit_method`/entry-filter sweep, not a default:** a post-entry cooldown window
during which the strategy does not re-evaluate for a flip/re-entry signal, distinct from both the
time-stop (§57.2) and the anti-scalping rail (§4.1). **Unvalidated** — the source offers only one
non-isolated ablation on 2003-2004 ASX data, no statistical significance testing, no crypto relevance.
Low priority; log and move on.

**Maps to:** `strategy/rules/` (breakout family, §64.3) and `execution/executor.py` /
`strategy/backtest.py` (entry-cooldown sweep candidate, §64.4).

### 64.5 ⛔ GA-optimized Sharpe-ratio fitness — reinforces the already-declined Sharpe/Rf stance (riba exemplar)

The paper's optimization fitness function is explicit:

> SR = (Rp − Rf) / σp

where `Rf` is **the risk-free rate**. The KB has already declined Sharpe-ratio-as-primary-metric
**specifically because of this term** (§33, §50.1, §54.22 all independently arrive at "Sharpe
anchors on `Rf` = riba; use Sortino/drawdown/expectancy instead"). This paper is simply another
data point of the same pattern in the wild — a 2007 academic paper using GA (also already declined
per §54's GASP-genetic-optimizer discussion, non-reproducible/overfit-prone) to hill-climb a
Rf-anchored fitness function. **No new decision required — reinforces the existing exclusion.**
Logged as an additional negative exemplar, not a new finding.

### 64.6 Actionability framework + walk-forward "Lift" metric — thin reinforcement of the promotion gate

The paper's formalism — a strategy is "actionable" only if it clears both **technical
interestingness** (a statistical criterion, e.g., min-support/min-confidence) *and* **business
interestingness** (a profit/ROI criterion) — is an academic restatement of "a rule must be both
statistically real and economically worth trading," which is precisely what `strategy/promotion.py`'s
expectancy + min-sample + breakeven-win-rate gate (§35.2) already operationalizes, just without the
formal logic notation. Their evaluation method — sliding-window train/test across 10 years of data
in 5 stock markets (1997-2006), comparing an optimized strategy's "lift" over 100 randomly-drawn
parameter combinations — is a **primitive precursor to the walk-forward + OOS-firewall + robustness-
plateau rigor already specified in far more detail by Kaufman (§54.10, §54.11)**. Nothing to add;
logged only because the report should note it was checked, not silently skipped.

### 64.7 ⭐ XAI/Shapley (Babaei & Giudici, 2025) — a concrete explainability mechanism for IF a future ML/LLM component ever scores candidates

This paper is a credit-risk/equity-fundamentals case study (SHAP/Shapley values used to explain an
XGBoost model's predictions of SME default probability and expected return from balance-sheet
ratios) — the **application domain is entirely out of scope** for crypto (see §64.8). But the
**mechanism** is worth logging against the project's real, already-documented requirement that the
agent maintain an audit trail and that every decision be explainable/attributable.

The paper's core argument, stripped of its SME context, is a clean statement of a problem `keel`
does not currently have (the engine is deterministic, so every decision is already attributable to a
named rule/parameter) but **would acquire the moment any statistical/tabular scoring model is
introduced anywhere in the pipeline** — including inside the deferred LLM feature, if that feature is
ever built to use anything beyond a pure chat-style LLM call (e.g., a numeric screener/ranking model
scoring candidate products or strategies before an LLM or human reviews them):

- A "black box" model's **accuracy does not substitute for explanation** — "despite their high
  accuracy, ML models do not provide sufficient explanation and, thus, may not be adequate for
  informed investment decision-making." This is the paper's whole thesis, and it is a direct,
  domain-independent statement of *why* keel's audit-trail requirement exists: a number (a
  probability, a score, a veto) with no attached reason is not auditable and is not debuggable when
  it's wrong.
- **Shapley values (SHAP)** are offered as the fix: a **model-agnostic, post-hoc** method that
  decomposes any single prediction into an additive per-feature contribution
  (`φᵢ = Σ_S [|S|!(|F|−|S|−1)!/|F|!] · [f(X_{S∪{i}}) − f(X_S)]`), giving **both a global
  ranking** ("which factors matter most overall") **and a local, per-instance explanation** ("why did
  *this specific* prediction come out this way"). Crucially it is **not tied to any particular model
  architecture** — it works on any `f()` that returns a score, tree ensemble or otherwise.

**Concrete, actionable implication for the LLM-feature spec (item 4 / roadmap):** §35.1 already
establishes the governing behavioral principle (LLM = thinking tool, never signal generator; feed
data don't ask it to recall; prompt it to argue against a thesis; treat its output as a document to
backtest, not a verdict). §64.7 supplies the **complementary technical requirement, not previously
present in the KB**: **if the screening/proposing role of that feature is ever implemented as (or
alongside) a numeric/tabular scoring model** — e.g., an ML classifier ranking candidate tokens for
allowlist admission, or scoring candidate strategy variants before they reach the LLM or a human —
**that scoring model's output must ship with a per-decision feature-attribution (Shapley-style or
equivalent), not just a bare score**, so that (a) the audit trail captures *why* a candidate was
proposed or vetoed, not merely *that* it was, and (b) a human reviewer can catch a spurious/
overfit driver (e.g., a feature that's an artifact of the training window) before it reaches the
backtest gate. This is a debuggability/compliance requirement, not a nice-to-have, and it applies
regardless of whether the eventual implementation is an LLM chat call (which needs the §35.1
craft — adversarial prompting, feed-don't-recall) or a trained scoring model (which needs *this* —
attribution). **Neither §35 nor any prior source specifies this for the scoring-model case; log it
against the day the feature is actually designed.**

**Maps to:** `analysis/insights.py` (deferred LLM feature module — appends to the §35.1 entry, does
not replace it); the audit-trail / explainability requirement generally.

### 64.8 ⛔ XAI paper's application domain — out of scope (equity/credit fundamentals, same as §50)

The paper's actual case study — predicting SME default probability and expected return from balance-
sheet ratios (Turnover, EBITDA, Loans, Shareholders' funds, Leverage, Net_income_on_Total_Assets,
etc.) via XGBoost, validated on Italian SME data with region/industry breakdowns — is **equity/credit
fundamental analysis**, structurally inapplicable to spot crypto tokens (no balance sheets, no equity,
no corporate loans/leverage ratios to compute), exactly the standing exclusion already established at
§50 ("fundamental analysis is not our lane"). Logged, not adopted. The `Loans`/`Leverage` features in
this model are conventional interest-bearing-debt metrics used only as *predictors* of a company's
credit risk — not a strategy or instrument we would ever hold — so this is a scope exclusion, not a
fresh riba violation to police.

---

### Reconciliation with §35 and the §5/§6.4 LLM asymmetry (explicit, per KB convention)

None of these three papers re-derives or contradicts the §5/§6.4 asymmetry (LLM proposes/screens,
never decides; every LLM-touched candidate must clear the same deterministic gate as a human rule) —
they arrive at complementary conclusions from three unrelated directions, none of which overlaps
with §35's territory:

- **§35** (Quantified Edge) is a *discretionary-trader's* guide to using an LLM as a thinking tool; it
  independently derived the asymmetry itself and supplied prompting/workflow craft (feed-don't-
  recall, adversarial prompting, output-as-backtestable-document, LLM-outside-the-live-loop).
- **§64.1** (Methods Matter) never mentions LLMs at all — it is a methodology critique of AI/ML
  *market-making* research from 1993-2020. Its relevance is that it **independently arms the "never
  grant an AI-proposed strategy a credibility shortcut" half of §5** with a documented, adversarially-
  reproduced case where a whole sequence of AI-published superiority claims collapsed under more
  rigorous testing — reinforcing *why* the deterministic backtest→paper→promotion gate must be
  applied without exception, including (especially) to anything an LLM proposes.
- **§64.7** (XAI/Shapley) never mentions LLMs either — it is about explaining *tabular ML classifiers*.
  Its relevance is a **new technical requirement that would attach to the LLM feature only in the
  specific case that its screening role is ever backed by a numeric scoring model**: attach a
  per-decision feature-attribution to any such score before it reaches the audit trail or the
  promotion gate. This is additive to §35.1, not a restatement of it — §35 covers the *behavioral*
  contract for an LLM's chat-style output; §64.7 covers the *technical* contract for a tabular
  model's numeric output, a case §35 doesn't address because its source material never uses one.
- **§64.3-§64.6** (Developing Actionable Trading Strategies) predates the LLM discussion entirely
  (2007) and is pure classical strategy-optimization content — no bearing on §5 either way.

**No dedupe conflict, no re-litigation needed — nothing here requires revising §35's conclusions.**

### Halal exclusions (explicit screen applied to all three papers)

- **§64.2** — "Methods Matter"'s entire test-bed (ZIC/ZIP/GDX/AA/SHVR/GVWY, CDA/LOB market model) is
  **two-sided market-making / quote-setting research**, structurally inapplicable to a long-only,
  spot-only, price-taking agent — flagged as scope-inapplicable, not a shariah violation (no
  leverage/derivatives/interest content in the paper itself).
- **§64.5** — the GA-optimization fitness function `SR = (Rp − Rf)/σp` uses **the risk-free rate
  (`Rf`)**, an interest-rate construct — reinforces the already-declined Sharpe/CAPM stance (§33,
  §50.1, §54.22); not re-excluded, just re-confirmed.
- **§64.8** — the XAI paper's case-study features include **Loans** and **Leverage** (interest-bearing
  corporate debt metrics) as model inputs for a credit-risk score — out of scope by instrument
  (equity/credit fundamentals, no crypto analog, same as §50), not something we would hold or
  compute for our allowlist; no new riba exposure since it's not proposed as a product or strategy.
- No hedging, short-selling-as-a-strategy (as opposed to the auction mechanism's symmetric buyer/
  seller *roles*), options/futures/derivatives, or carry content appears in any of the three papers.

### Discarded (no agent value)

- **"Methods Matter"**: implementation-level detail of ZIC/ZIP/GDX/AA/SHVR/GVWY's quoting logic, the
  BSE/TBSE simulator internals and LOB mechanics, the historical narrative (Vernon Smith / 1962 JPE /
  Nobel Prize), the specific win-count tables and dominance-graph figures — all scaffolding for the
  methodology point already extracted in §64.1.
- **"Developing Actionable Trading Strategies"**: the formal logic notation for Definitions 1-3
  (technical/business interestingness, actionability) beyond what's already paraphrased in §64.6; the
  36-strategy taxonomy (MA/OBV/CB/SR classes) — generic indicator families already in
  `analysis/indicators.py`; the specific ASX/HK/LSX/NYSE/SXE 1997-2006 dataset and Lift-value table;
  GA implementation mechanics (declined per §54's GASP discussion).
- **"XAI in Investment Decision-Making"**: the SME dataset specifics (2049 Italian companies, region/
  industry default-rate breakdowns, Figures 1-8), VIF/multicollinearity feature-selection procedure,
  SMOTE class-imbalance handling, XGBoost/AUC/MSE implementation details, the credit-scoring
  literature review (P2P lending, loan underwriting) — all specific to a use case (SME credit
  scoring) this project doesn't have.

---

### Net assessment (saturation-honest)

- **Genuinely NEW:** §64.4's time-hold/re-entry-cooldown filter (minor, unvalidated, sweep-candidate
  only) and §64.7's Shapley-attribution requirement for any future numeric scoring model inside the
  LLM feature (a real gap — nothing prior specifies this for the tabular-model case; §35 only covers
  the LLM-chat case).
- **Reinforces:** §64.1 sharpens (does not add to) the existing backtest-rigor/no-oracle posture with
  a strong external example of published AI-trading claims collapsing under real scrutiny — this is
  the "negative-exemplar" the brief anticipated, and it lands squarely in support of the
  deterministic-first, prove-it-in-the-harness design, not against it. §64.3/§64.5/§64.6 reinforce
  the Donchian/breakout family, the declined Sharpe/Rf metric, and the walk-forward promotion gate
  respectively — no new decisions.
- **Excluded/out of scope:** §64.2 (market-making/two-sided-quoting research, whole paper's strategy
  content), §64.8 (SME credit/equity fundamentals, whole paper's application domain).
- **Answering the brief's two specific questions:**
  1. *Does "Methods Matter" support or undercut the deterministic-first posture?* **Strongly
     supports it.** It is independent, rigorously-reproduced evidence that published AI/ML
     trading-algorithm superiority claims can be — and in this case were — simply wrong once tested
     at adequate scale and realism, which is exactly the failure mode the promotion gate's min-sample
     floor and walk-forward/OOS firewall exist to prevent, and exactly why the §5 asymmetry grants an
     LLM-proposed strategy no evaluation shortcut.
  2. *Does XAI impose a concrete requirement on the unbuilt LLM feature?* **Yes, one concrete
     addition:** if that feature's screening/proposing role is ever backed by a numeric/tabular
     scoring model (not just an LLM chat call), its output must carry a per-decision Shapley-style
     feature attribution before it reaches the audit trail or the promotion gate — a technical
     complement to §35.1's behavioral craft, not a restatement of it.
- **Recommendation:** this bundle is exhausted after one pass — none of the three papers has further
  chapters or follow-on content worth a part-2 extraction. No change to the sim, no new rail. File
  §64.4 and §64.7 as backlog notes for, respectively, the entry-filter sweep and the LLM-feature
  design spec; nothing else requires action now.
