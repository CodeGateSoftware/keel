[← Knowledge Base index](../README.md)

## Source 35 — "Quantified Edge: Using AI, ChatGPT & Python in Real Markets" (Metaverse Trading Academy, 2025, 65pp)

> A guide for a **discretionary human trader using ChatGPT/Python as an analysis assistant** (10 chapters:
> AI limits, prompt engineering, SMC, Market Profile, order flow, Python, psychology, case studies, system
> build, results). **Most valuable as a REFERENCE for the project's deferred LLM feature** (item 4 /
> roadmap #3; the "Role of LLMs" governing principle §5/§6.4 — see [[halal-cb-autotrade-project]]): its
> Chapter 1–2 thesis is an independent, concrete articulation of *exactly* our LLM-asymmetry stance, and
> it supplies craft (adversarial prompting, hallucination mitigations) we can reuse when we design that
> feature. Its strategy chapters (SMC / Market Profile / order flow) are mostly **discretionary or need
> data we don't have** → v2-deferral / N/A. One clean quantitative tool (§35.2) and a reinforcement of the
> §34 breakout/stop work (§35.3).

---

### 35.1 ⭐ AI = thinking tool, NOT signal generator → validates & sharpens the deferred LLM feature (§5/§6.4)
The book's Chapter 1 is our **"Role of LLMs" asymmetry principle**, independently derived:
- **"AI as THINKING TOOL ✓ / AI as SIGNAL GENERATOR ✗"** — AI is for *pre-trade hypothesis stress-testing,
  post-trade journal analysis, bias-spotting, explaining concepts, generating backtest code, refining the
  rules document*; **never** for *calling direction, predicting price, making real-time decisions, or
  replacing your edge.* → **verbatim match to our §5:** LLM = proposer/insights/explainer/veto, **never in
  the live decision or rails (§6.4).**
- **The hallucination problem** — LLMs "confidently state incorrect information" (fabricated levels/stats).
  → the reason LLM output can **never** sit in the deterministic path. Concrete **mitigations to bake into
  our LLM feature design:**
  1. **Feed data, never recall** — "always provide the data yourself; do not ask AI to recall market data"
     (it fabricates plausible numbers). → our LLM gets *structured data we supply*, never asked to remember
     prices/stats.
  2. **Adversarial prompting** — "ask AI to argue *against* your thesis, not confirm it" (it amplifies
     confirmation bias / "will often agree with your framing"); *"what are three reasons this could be
     completely wrong?"* → matches our **LLM = veto-research / anomaly-flag** role; prompt it to *refute*,
     not endorse, a candidate.
  3. **Output = a backtestable document, not a signal** ("becomes a document you backtest against, not a
     journal entry") → matches our design: **LLM proposes → deterministic backtest→paper→promotion gate**
     before anything enters the rule library.
- **"No AI during active trades"** (mid-trade AI = "decision noise") → for our automated agent this is
  *stronger*: the LLM is **entirely outside the live execution loop** — it can pause/veto/flag or propose
  (offline, gated), but the deterministic engine + rails run the trade. Reinforces §6.4.
- **Platform reality:** only *ChatGPT-API + Python + real structured data* gives AI usable market context.
  → our LLM feature (when ON) needs an **API key + our structured-data pipeline** (already the plan;
  OFF-by-default, API-gated). Chapter 6's OpenAI-SDK code is a build-time reference.

### 35.2 ⭐ Breakeven win-rate formula: 1/(1+R:R) → quantifies the per-rule-class promotion floor (§23.1/§25.5)
Clean tool: **breakeven win rate = 1 / (1 + R:R)**. So R:R 1.5 → 40%, R:R 2 → 33%, **R:R 3 → 25%.** →
**Directly operationalizes the per-class promotion floor** we flagged for trend-followers: a Turtle-style
rule at R:R≈3 is positive-expectancy at just **>25% win rate** — proof the global 55%-win bar is wrong for
that class. Use `expectancy > 0` ⇔ `win_rate > 1/(1+R:R)` as the **class-agnostic floor** (win-rate bar
*derived from* each rule's realized R:R), instead of one fixed 55%. Ties together §23.1 (per-class floor),
§25.5 ("good trade vs winning trade"), §34.4 (graded sizing).

### 35.3 Liquidity sweep vs Break-of-Structure → reinforces close-based stop + failed-breakout (§34.1/§34.6)
Chapter 3 (SMC): *"a spike beyond a swing that reverses is a **liquidity sweep** (stop hunt), **not** a
break of structure; treating sweeps as BOS leads to losses."* → **Same insight as §34.1/§34.6, from the
order-flow side:** a **wick beyond a level that doesn't hold = a stop-hunt sweep, not a real breakout.**
→ Strongly reinforces **requiring a CLOSE beyond the channel/level (§34.1 `stop_trigger=close`) for the
Turtle breakout entry AND exit** — a wick-only penetration is a sweep to be ignored, not a signal. Also the
**failed-breakout filter (§34.6):** breakout that closes back inside = failed → don't chase. This is the
single most useful mechanical idea from the strategy chapters, and it's a *reinforcement*, not new.

### 35.4 Possible-future analysis layer (low priority, backtest-first): Market Profile
Chapter 4 (Auction Market Theory): **POC** (point of control = most-traded price), **value area (VAH/VAL)**,
"fade the extremes of the value area," "**POC migration** higher/lower = buyer/seller control / trend day."
→ A legit **volume-at-price** framework; POC/VAH/VAL are computable from Coinbase trade data and could serve
as **dynamic S/R levels** + a trend cue (POC migration). → **Note as a possible future `analysis` layer**
(volume-profile levels feeding `levels.py`/regime), but **lower priority, discretionary-ish, backtest-first**
— our current level model (§4.8) + ADX/Donchian trend model (§25/§27) already cover this ground. Not now.

### 35.5 Reinforced (psychology / expectancy / process)
Cognitive-bias auditing, **expectancy/profit-factor focus**, **process goals over P&L goals**, honest
performance expectations (realistic win rates + drawdowns, max-consecutive-losses), position-size-down when
readiness is low → all reinforce our expectancy-based promotion gate, journaling, DD breakers, and the
automated agent's structural immunity to in-session emotion (§4.10/§6.1). Nothing new.

### 35.6 ⛔ / N/A — deferred or out of data scope
- **SMC order blocks / fair value gaps (FVG) / CHOCH** — **discretionary, subjective** (like harmonics &
  chart-pattern geometry) → **v2-deferral**, backtest-first if ever. The sweep-vs-BOS idea (§35.3) is the
  one mechanizable takeaway.
- **Delta / footprint / order-flow / Level-2 DOM (Chapter 5)** — requires **tick-level order-flow data we
  don't ingest** (Coinbase spot via `cb_client` gives OHLC candles, not footprint/DOM). **Out of data
  scope / N/A**; the book itself admits AI "cannot read footprint/delta/volume-profile reliably."
- **Context is leveraged index futures (ES/NQ) intraday discretionary** — we take only the AI-role +
  mechanical concepts; leverage/futures context excluded per the standing rails (§4.9).

### 35.7 Discarded (no agent value)
The 25 ChatGPT prompt templates (human-workflow scripts — useful as *style* reference when we author our
LLM feature's prompts, not as agent rules); the daily/weekly/monthly human routine (30-45 min/day manual
workflow — our agent is automated); Academy marketing; simulated case-study war-stories; legal disclaimer.

---

### Net assessment (saturation-honest)
- **PRIMARY VALUE = a reference for the deferred LLM feature (§35.1):** independently validates our
  §5/§6.4 asymmetry ("thinking tool, not signal generator") and supplies concrete craft — **feed-don't-
  recall data, adversarial/refute prompting, output-as-backtestable-document, LLM-outside-the-live-loop** —
  to fold into that feature's brainstorm→spec when we build it (OFF-by-default, API-gated). Cite this when
  designing item 4.
- **NEW tool:** **breakeven win-rate = 1/(1+R:R)** (§35.2) → makes the per-rule-class promotion floor a
  formula (`win_rate > 1/(1+R:R)`), not a guess.
- **REINFORCES:** the §34.1/§34.6 close-based-stop / failed-breakout via SMC's **liquidity-sweep-vs-BOS**
  (a wick beyond a level is a stop-hunt, not a breakout — require a close) (§35.3); expectancy/process
  discipline (§35.5).
- **POSSIBLE-FUTURE (low priority):** Market Profile POC/value-area volume-at-price layer (§35.4).
- **DEFERRED/N-A:** SMC order-blocks/FVG (discretionary → v2); delta/footprint/order-flow (no data).
- **Action:** none to build now; tag §35.1 for the LLM-feature spec and §35.2 for the promotion-floor
  implementation alongside the Turtle rule. See [[halal-cb-autotrade-project]], [[halal-cb-transcript-workflow]].
