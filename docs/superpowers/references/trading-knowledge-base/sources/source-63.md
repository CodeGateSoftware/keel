[← Knowledge Base index](../README.md)

# Source 63 — "Trading Strategies with Position Limits" (Valerii Salov, arXiv:1712.07649v1, 19 Dec 2017, 64pp)

**Type:** academic **combinatorics / abstract-algebra** paper on futures trading, illustrated with
E-mini S&P 500 (ES) tick data. Author is a chemist-turned-quant (Wiley 2007, *Modeling Maximum
Trading Profits with C++*) — this arXiv paper is the theoretical follow-up to that book.

> **Blunt top-line: the title over-promises relative to the content.** The task brief flagged this
> paper as "unusually on-point" because our agent enforces a family of hard position/exposure caps —
> but Salov's "position limit" `W` is not a risk-management topic here, it is a **combinatorial
> device**: fixing `|W_i| ≤ W` makes the *set of all possible trading-action sequences* finite,
> which lets the author count them, `(2W+1)^(n-1)`, derive their probability mass/characteristic
> functions, and study the resulting vector space's rank and a Cayley-table algebra (magma →
> quasigroup → loop) on positions. **None of Sections 3–8 (roughly half the paper) is about how a
> cap should be *set*, how it changes *optimal behavior*, or how to *allocate under it* across
> assets — it is pure enumeration and algebra of the strategy space, with zero backtestable output.**
> The one part of the paper with genuine trading content is **§9–10: the Maximum Profit Strategy
> (MPS)** — a **hindsight-only, hypothetical benchmark** (given the price chain and costs *after the
> fact*, what is the highest-profit sequence of actions achievable *under the same position limit*?)
> — and its decomposition into **Optimal Trading Elements (OTE)**, used here purely as an **intraday
> tick-level statistical/pattern tool** for a single futures contract, not a portfolio or capital-
> allocation framework. Everything about margin, shorting, leveraged futures contract sizing, and
> continuous/Brownian pricing is excluded or requires reformulation per the halal screen. What
> survives translation is small but genuinely useful: **a hindsight-benchmark diagnostic adaptable
> to the under-deployment defect (§63.2)**, one real nugget on caps being capital-derived and not
> necessarily constant (§63.1), and a couple of reinforcements. **Recommend not seeking out more
> from this author's arXiv series (Sources cited [93]–[103] are all self-citations of the same
> MPS/OTE program) unless the goal is specifically more tick-level statistical tooling.**

---

## §63.1 — Position limits are capital/margin-derived, and need not be constant
**Module: `execution/guards.py` (the hard rails), `strategy/money_mgmt.py`**

Salov's worked example (p. 8): a $10,000 futures account with per-contract initial margin $1,237.50
allows a position limit of `W = floor(10000/1237.50) = 8` contracts. After transaction costs, and
comparing post-trade equity to the **maintenance margin** requirement, he computes exactly how much
price move ($115.64/contract, <2.5 points) would force a margin call — a move ES can make "in a
matter of a few seconds or minutes." His conclusion:

> *"Establishing a position size up to the available account equity is too risky and can quickly
> ruin an account. The capital limit and margins dictate the position limit `W`. However, due to
> these factors only, `W` does not have to be constant."*

**Translation for keel (excluding the margin machinery, which is riba/leverage and does not apply to
a spot cash account):** the underlying point — that a position cap is *derived from available
capital*, not handed down as an eternal constant — survives with margin stripped out. It reframes
the open judgment call in the task brief ("should exposure/concentration caps be static or
state-dependent?") with a concrete precedent: **express caps as a function of current equity/cash-
on-hand (percentage-of-equity), not a frozen dollar figure**, so they automatically loosen as the
account grows and automatically tighten after a drawdown, with no rule change required. This is
**reinforcement + one sharpening** of what the per-asset concentration cap (`% of max exposure`,
README hard-rails list) already does structurally — the new part is the explicit argument for
*why* a static dollar cap is the wrong default: it under-sizes as equity grows and over-sizes after
losses, i.e., it is state-blind in exactly the direction that matters for compounding safely. No new
rail; a design argument for making the **existing** per-order/per-day *dollar* caps float with
account equity (already true of the % based ones) rather than being pinned constants.

**Halal note:** the mechanism used to derive `W` here (initial margin ÷ maintenance margin ÷
leverage) is entirely leverage/margin/riba-based and must be discarded outright — only the
structural conclusion ("cap ∝ capital, not a constant") is portable, reformulated with margin
replaced by **cash actually held**.

---

## §63.2 — ⭐ MPS/OTE as a hindsight-benchmark diagnostic — a genuinely new tool for the under-deployment defect
**Module: `strategy/backtest.py`, `strategy/promotion.py`**

The paper's one real trading construct: the **Maximum Profit Strategy (MPS)** is the trading-action
sequence `U` that *would have* maximized realized P&L over a known, completed price chain, given the
cost structure `C` and position limit `W` — computed **after the fact**, with full hindsight, using
an `O(n)` left/right-sweep algorithm (not brute-force enumeration of the `(2W+1)^(n-1)` possibilities,
which the paper shows is astronomically larger than the number of hydrogen atoms in the sun for a
single day of ES ticks). MPS0 (no reinvestment) is explicitly proposed as a **performance benchmark**
and **moving indicator** (cites the author's own book, [93, pp. 151–155]): *"MPS is another face of
the same market"* — an **objective, hindsight ceiling on what any strategy could have extracted**,
against which a real (causal, no-lookahead) strategy's actual P&L can be measured as a fraction
captured.

**This is directly usable, reformulated, for keel's open under-deployment defect** (~23 trades in 5
years, mostly sitting in cash): compute a **long-only, position-capped MPS** over the same 5-year
backtest window —

```
MPS_longonly(P, C, W_actual) = the hindsight-optimal buy/sell/hold sequence
                                 subject to 0 ≤ position ≤ W_actual (our actual caps),
                                 using our actual per-trade cost model
```

— then compare it to the sim's realized P&L two ways:
1. **If hindsight-MPS-under-our-actual-caps is itself small** (close to what a "buy-and-hold within
   the cap" would produce), the caps aren't the bottleneck — the allowlist's price action in this
   window simply didn't offer much even to a perfect oracle, and the fix is elsewhere (more assets,
   different rules, different regimes).
2. **If hindsight-MPS-under-our-actual-caps is large but the realized result is tiny**, the caps are
   *not* the binding constraint — the entry-signal/allocation logic beneath the caps is (i.e., the
   agent is leaving a big, cap-compatible opportunity on the table because it isn't triggering/sizing
   into it), which points squarely at signal frequency and candidate-ranking/allocation logic as the
   thing to fix, not the caps themselves.
3. **Sweep `W`** (relax the cap in the backtest-only diagnostic, holding everything else fixed) and
   plot hindsight-MPS vs. `W`: if MPS is flat as `W` increases, the caps are already loose enough and
   are not the reason capital sits idle; if MPS rises steeply with `W`, tightening/looseness of the
   cap really is doing the limiting.

This is a **backtest-only diagnostic tool**, never a live decision input (it requires future prices
— it is definitionally non-causal) — same posture as any other hindsight/oracle construct the KB
already excludes from live decisioning (§6.4 no-prediction-oracle). Its entire value is as a
**yardstick computed once, offline, over completed history**, exactly analogous to how a Sharpe
ratio or max-drawdown figure is computed after the fact to *judge* a strategy, never to *run* it.

**Long-only reformulation required (halal + design constraint):** the paper's MPS0 freely reverses
long ↔ short (`W → -W` is the maximum-single-tick action, generating the extreme gain `2CW(n-1)`,
p. 16). For keel, the admissible position domain is `0 ≤ W_i ≤ W`, not `-W ≤ W_i ≤ W` — so the
long-only MPS is computed over **half the state space** the paper analyzes (see §63.3). MPS1
(reversal-based) and any variant that shorts are excluded outright; MPS2 (reinvest profits
immediately into new size, "if initial and maintenance futures margins permit") is margin-gated and
excluded as stated, but its spirit — **compound size only out of realized profit, never by
borrowing** — is exactly the reserves/pyramid-on-profits model already adopted from Kaufman (§54.19)
and needs no new rule.

---

## §63.3 — Long-only structurally forfeits half the hindsight benchmark (expectation-setting, not a defect)
**Module: `strategy/promotion.py` (interpreting §63.2's output)**

Because Salov's MPS is symmetric (profits from both up-legs, held long, *and* down-legs, held
short), and our agent is one-sided (`0 ≤ position ≤ W`), a long-only MPS computed on the same price
chain will **structurally forfeit the profit available from every down-leg** — not because of a bug,
under-sizing, or an overly tight cap, but because shorting is categorically excluded (gharar/riba
concerns aside, negative holdings simply aren't in the domain). This matters directly for reading the
§63.2 diagnostic correctly: **do not compare keel's realized P&L to the paper's own two-sided MPS
figures (Tables 4–6, Figures 16–17)** — those benchmark a strategy that can profit in both
directions. The correct comparison is realized-P&L vs. **long-only-MPS-under-our-caps**, computed
fresh on our own data; the two-sided MPS is not an apples-to-apples ceiling for a halal agent and
would make any long-only strategy look artificially far short of "optimal." This is purely an
interpretive caveat for building the §63.2 tool, not a new rule.

---

## §63.4 — Signal generation vs. money management: an explicit historical precedent (reinforcement)
**Module: `strategy/rules/` vs `strategy/money_mgmt.py` (existing separation)**

p. 9: fixed/small position-size strategies (`|W_i| ≤ 1` or constant) "can be too inefficient or
risky. Still, such strategies can be useful for **the evaluation of trading rules generating
individual trading signals and their separation from money management** answering which portion of
the capital should be devoted to next trade." This is exactly the architectural separation `keel`
already has (`strategy/rules/` emits a signal at fixed/nominal size; `strategy/money_mgmt.py` decides
how much capital that signal actually gets). No new rule — a citation-worthy external validation of
an existing design choice, useful if this separation is ever challenged.

---

## §63.5 — Dynkin-Neftci ("Markov") times: formal grounding for no-lookahead (reinforcement)
**Module: `strategy/backtest.py` (order-of-events / no-lookahead discipline)**

The paper's historical digression (§ "Markov time") lands on one usable idea: a trading rule's
signal-detection instant is only legitimate if it is a **stopping time** — decidable from information
available *up to and including* the current tick, never from the future (`I_t`-measurable, not
`I_{t+n}`). The author calls this a "Dynkin-Neftci time" to sidestep a genuine etymological dispute
in the literature (Dynkin vs. Neftci priority — extensively documented here, of zero agent
relevance). He also gives the cautionary flip side: a delayed-quote training simulator that lets a
strategy "look into the future" turns a liquid futures market into "a boring money machine" — a
vivid restatement of look-ahead bias. **This is a formal restatement of the no-prediction-oracle
principle (§6.4) and the sim/live parity posture already in the KB (§57.3)** — no new rail, but a
clean, rigorous vocabulary (*"is this signal detectable at a Dynkin-Neftci time?"*) worth borrowing
if the backtest harness's documentation ever needs to state the no-lookahead invariant more
precisely.

---

## §63.6 — Discrete, non-Gaussian market microstructure (reinforcement of the declined continuous/Gaussian quant-stack)
**Module: none — background validation only**

The introduction (pp. 1–2) is an extended argument that **real markets are irregularly-spaced,
discrete-tick processes**, not the continuous, infinitely-divisible Brownian motion that classical
derivative-pricing theory assumes — quoting Kolmogorov: *"it will become understood that in many
cases it is reasonable to study real phenomena without making use of [continuity]... passing
directly to discrete models."* Daily/hourly/minute bars are themselves an arbitrary aggregation
("financial atoms") of irregular ticks, and even the i.i.d. assumption behind daily return statistics
is shown to be shaky (different tick-counts per session violate the identical-distribution
assumption invoked to justify variance-additivity). **This independently reinforces the project's
existing rejection of continuous-time/Gaussian/risk-free-rate quant machinery (declined MPT/CAPM,
§33, §50) and its discrete daily-bar backtest design** — the paper's own author considers the
discrete, tick-level, cost-inclusive treatment *more practical* than a continuous no-arbitrage model.
No new rule; a citable authority for why keel is right to treat prices/bars as discrete entities
rather than reaching for continuous stochastic-calculus machinery.

---

## §63.7 — Maximum Loss Strategy (MLS) cost bound — N/A at our trade cadence
**Module: none**

p. 47 derives a **worst-case transaction-cost bound**: a strategy that reverses the position every
single tick between `-W` and `W` racks up a bounded maximum loss of `-2CW(n-1)` (`n` = number of
ticks, `C` = per-unit cost) — purely a fee-churn bound for a pathological every-tick whipsaw
strategy on **intraday tick data**. At keel's cadence (≈23 trades over 5 years, ~24-day average
hold), `n` (ticks between actions) is enormous and this bound is meaningless — it describes a
day-trading/HFT failure mode (churn destroys an account via costs alone) that the existing
anti-scalping rail and low-turnover-as-compliance-value (§28.3) already guard against structurally
by keeping trade count low. Logged only to confirm it adds nothing beyond what's already covered.

---

## Reconciliation with prior sources (especially §54)

**No overlap, no conflict — genuinely different territory.** Kaufman (§54) already owns every
*backtestable* risk-and-sizing topic this paper's title might suggest: optimal-f/risk-of-ruin
(§54.18), diversification/correlation-based unit caps (§54.14, §54.22), volatility-parity sizing
(§54.7), and market-ranking for capital allocation (§54.9, §54.17, §54.21). **Salov's paper does not
engage any of that** — it never discusses *how much* to allocate across a ranked candidate set,
never touches correlation, and never proposes a sizing formula. It is instead (a) an enumeration/
algebra exercise on the *space* of strategies a position cap permits, and (b) a hindsight-benchmark
tool (MPS/OTE) for a *single* instrument's tick chain. The one point of genuine contact is §63.1's
"cap is capital-derived, not constant" — which **sharpens, but does not contradict**, the
existing rails; §63.2's hindsight-MPS diagnostic is a **new tool class** (a benchmark-quality metric)
that nothing in §54's testing-rigor chapter (§54.10–11) covers — Kaufman's robustness/OOS framework
judges a strategy against *its own* historical distribution and against other backtests; it never
constructs a hindsight ceiling for the *same* price history under the *same* constraints. Recommend
folding §63.2 into the `strategy/promotion.py` / `keel simulate` reporting as an optional offline
diagnostic, clearly labeled non-causal/backtest-only, alongside Kaufman's existing rigor checklist.

## ⛔ Halal exclusions

- **All margin mechanics** (initial margin, maintenance margin, margin calls, "position size up to
  available equity") — leverage = riba (§28.1). The position-limit-from-capital *insight* (§63.1) is
  kept; its margin-based *derivation* is discarded and reformulated around cash-on-hand.
- **Symmetric two-sided position domain `[-W, W]`** throughout Sections 3–8 (all the combinatorics,
  the Cayley-table algebra, the orthogonal-vector bases) assumes shorting is admissible. Our
  position domain is one-sided `[0, W]` — as the task brief anticipated, this **materially changes**
  which of the paper's results even apply: half the "strategy universe," half the "optimal trading
  elements" (every SOTE — sell-to-open/short leg), and the two-sided MPS benchmark are structurally
  inadmissible, not merely undesirable (§63.2, §63.3).
- **MPS1** (long↔short reversal strategy) — excluded outright, shorting.
- **MPS2**'s reinvestment gate ("if initial and maintenance futures margins permit") — margin-gated,
  excluded as stated; its non-margin spirit (compound only realized profit) already covered by
  §54.19.
- **Futures contract mechanics generally** (contract multipliers, "full price point" `k`, spot-month/
  single-month CME position limits in physical bushels of corn, expiration/nearby-contract roll) —
  N/A: we hold spot coins directly, no contracts, no expiry, no roll.
- **Options-derivative pricing / no-arbitrage theory** (Black-Scholes, Bachelier, Ornstein-Uhlenbeck
  mean-reversion of interest rates, Russian lookback options) referenced in §6/§11's philosophical
  aside — excluded, options/derivatives (§27.4, §28.1); the interest-rate Ornstein-Uhlenbeck
  discussion is additionally riba (mean-reverting **rates**).
- **Mortgage-backed securities / PCA of 360 monthly interest rates** (§11 opener) — riba
  (interest-bearing instrument), and out of scope (fixed-income, not spot crypto).

## Discarded (no agent value)

- **Sections 3–8 in their entirety** (roughly 27 of 64 pages): the combinatorial counting of
  `(2W+1)^(n-1)` unique positions/strategies, their probability-mass/characteristic/moment functions,
  time-slice orthogonality theorems, the linear-algebra rank-of-the-strategy-space proofs, and the
  Cayley-table/magma/quasigroup/loop classification of the position-addition operation `⊕_W`. This is
  rigorous pure mathematics about the *shape of the possibility space* a position cap creates — it
  produces no decision rule, no parameter, no backtestable filter. Interesting only as a formal
  confirmation that "a position cap makes the strategy space finite," which is obvious and already
  assumed by construction in `execution/guards.py`.
- **Quantum computing aside** (qubits needed to represent `3^134908` superposed strategies,
  D-Wave/Harvard citations) — a rhetorical illustration of how large the strategy space is; zero
  agent relevance.
- **The entire "Markov time" etymology investigation** (pp. 35–39: Dynkin vs. Neftci priority,
  Bernstein/Zinin/Kolmogorov historical asides, the Markov-family-of-mathematicians genealogy) — one
  usable idea extracted in §63.5; the rest is historiography.
- **"Trading Places" orange-juice-futures insider-trading anecdote** (p. 39) — colorful, zero
  agent relevance (fictional, and about frozen-OJ futures delivery, not spot crypto).
- **OTE/BOTE/SOTE tick-level empirical distributions** (a-increments/b-increments/volume histograms,
  ECDFs of OTE profit by filtering cost, skew/kurtosis tables) — this is **intraday tick-level
  statistical characterization** for a discretionary/HFT tick-reversal trader deciding a filtering
  cost `FC`. Nothing here operates on daily bars or maps to a ~24-day-hold strategy; the underlying
  *concept* (MPS as hindsight ceiling) is kept (§63.2), the tick-level statistics apparatus around it
  is not.
- **"Why do speculative markets exist?" (§11)** — a philosophical closing argument that MPS is an
  "objective measure of market disequilibrium," tangential economic musing with no rule or rail
  attached.
- **The C++/std::vector implementation notes, gnuplot/Excel plotting mechanics, CME corn
  position-limit example (bushels/metric tons), Jesse Livermore "cornering the wheat market"
  anecdote** — production/illustrative detail, no agent surface.

## Net assessment (saturation-honest)

**Small, mostly-theoretical yield from a paper whose title over-promised.** The vast majority of this
64-page paper (Sections 3–8, the combinatorics/algebra core) has no path to a backtestable rule —
it studies the *shape* of the strategy space a position cap creates, not how to *use* the cap well.
The genuinely new, portable idea is **§63.2: reformulate the paper's hindsight Maximum Profit
Strategy as a long-only, cap-constrained backtest-only diagnostic**, run once over the 5-year window
against keel's actual caps, to disentangle "the caps are too tight" from "the allocation logic under
the caps is naive" for the open under-deployment defect — something Kaufman's testing-rigor chapter
(§54.10–11) doesn't provide because it benchmarks a strategy against its own history, not against a
hindsight ceiling for the *same* history. **§63.1** (caps should be capital-derived and float with
equity, not frozen dollar constants) sharpens the existing state-dependent-cap discussion with an
explicit precedent, once the margin machinery is stripped out. **§63.3** is a necessary interpretive
caveat for using §63.2 correctly (don't benchmark a long-only agent against a two-sided hindsight
optimum). §63.4–63.6 are reinforcements (signal/money-management separation, no-lookahead formalism,
discrete/non-Gaussian market grounding) that validate existing design choices without changing them.
§63.7 (MLS transaction-cost bound) is logically sound but operates at a trade cadence (every-tick
reversal) that doesn't exist in keel. **Recommendation:** prototype the §63.2 hindsight-MPS-under-cap
diagnostic as an optional, clearly-labeled-non-causal report in `keel simulate`'s output, sweep `W`
to see whether the under-deployment defect traces to the caps or to the allocation logic beneath
them; do not invest further time in this author's arXiv series (all self-referential citations of
the same MPS/OTE program, [93]–[103]) unless intraday tick-level tooling becomes a project goal.
