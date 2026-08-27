# PRD — Analysis feed: consuming Ibn Khaldun Analyzer

**Status:** proposed · **Date:** 2026-08-27 · **Counterpart:**
[Ibn-Khaldun-Analyser](https://github.com/CodeGateSoftware/Ibn-Khaldun-Analyser) §3.10

---

## 1. What this is

Ibn Khaldun Analyzer reads macro-financial and geopolitical news through pluggable historical
frameworks and emits **assessments with citations** — never instructions. Its PRD names keel as
its reference subscriber and has already shaped two of its own decisions around keel's
constraints: SSE exists as a first-class transport so keel needs no new dependency, and §3.8.4
binds the platform never to emit an order, a size, a price, or an instruction to trade.

This document is keel's side of that contract.

**In one sentence:** the feed proposes assets for human attestation; keel's existing deterministic
gate decides; nothing new touches admission, sizing, or order placement.

---

## 2. Why this is worth doing — and the honest case against

### 2.1 The case for

keel already has an ingestion path for externally-produced candidate shortlists.
`keel assets propose --from <file>` parses a JSON list of `{asset, rationale, sources[]}`, routes
each candidate through the **same** admission gate as `assets screen`, and admits nothing. It is
pure, dependency-free and tested.

Today that file is produced by hand, or by an operator's own LLM session. The feed replaces a
manual step with a subscription to a service built to produce exactly that shape — the Analyzer's
PRD commits to a "Keel adapter" output rendering assessments into the candidate structure
`proposer.py` already validates.

The value is **scouting reach**, not signal: a wider, cited, continuously-updated view of which
assets are in play, arriving as proposals a human still has to attest.

### 2.2 The case against, stated first

**keel's binding constraint is cost, and this adds cost.** The README's honest result is that no
shipped rule family is net-positive at the taker fee actually paid, and that the viable
parameter/fee intersection is empty. A paid analysis subscription does not move that number by a
basis point. It is an operating expense against a system that is not yet profitable.

**It also cannot pay for itself in the way a reader might assume.** The feed cannot improve entry
timing, exits, or sizing without becoming a trading signal — which §5 forbids outright. Its only
sanctioned effect is to widen the candidate pool that a human then attests.

So the honest framing is: **this buys evidence and reach, not edge.** If, after a measured period,
it has not improved the quality or breadth of attested candidates, it should be cancelled. §7
makes that a scheduled decision rather than a drift.

---

## 3. The finding that decides the tier

`proposer.Candidate` requires `asset`, `rationale` and a non-empty `sources[]`. Mapping those onto
the Analyzer's dual payload (their PRD §3.8.3):

| keel needs | comes from | tier |
| :--- | :--- | :--- |
| `sources[]` | `signal.sources` | **Desk** |
| `asset` | `signal.entities[kind="ticker"]` | **Desk** |
| `rationale` | `prose.conclusion` | Free |

**Both machine-readable fields live in the `signal` block, and `signal` is the Desk-tier
boundary.** The free and Analyst tiers deliver prose, and keel cannot extract an asset symbol or a
citation list from prose without an LLM — a dependency keel does not have and will not add.

**Therefore the integration requires the Desk tier or it does not work at all.** That is a
purchasing decision, not an engineering one, and it gates every issue in the milestone. It is not
a detail to discover halfway through Phase A.

---

## 4. How it fits keel's architecture

Four constraints the Analyzer's §3.10 already names, and what each means on this side:

**4.1 Advisory only, by construction.** `proposer.build_proposal_report` injects the gate as
`screen_fn(repo, product, quote)` — three arguments, none of which is the rationale. Its docstring
states the intent: "the LLM's rationale and shariah_hypothesis are never passed to the gate, so
they cannot influence admission (asymmetry, by construction)." The feed inherits that asymmetry
unchanged. It does not get a widened interface because its prose is better sourced.

**4.2 Zero new dependencies.** keel's runtime dependency list is four entries, heavily commented
against growth. SSE over `text/event-stream` is line-oriented text over HTTP and is consumable
from `urllib.request` in the standard library. No websocket client, no SSE library, no HTTP client.

**4.3 The `subscription:` collision is real and must be avoided.** keel already uses
`subscription:` in config for a per-venue Coinbase One fee-tier attestation enforced by **rail
14** — a trading rail. A second, unrelated meaning of "subscription" in the same config file is a
genuine hazard: an operator reading `subscription: lapsed` must not have to work out which
subscription. This configures under **`analysis_feed:`**, and the integration guide says why.

**4.4 Fail-closed on absence.** Unreachable feed, stale frames, or a lapsed entitlement all
degrade to *no analysis input* and normal keel behaviour. Never block, never retry into the
trading loop, never treat a stale assessment as fresh.

---

## 5. Non-goals — binding

* **The feed never becomes a trading signal.** No frame may influence entry, exit, sizing, stop
  placement, rule promotion, autonomy, or the kill-switch. The only sanctioned effect of an
  assessment is to appear in a shortlist a human reads.
* **The feed never attests.** Attestation is an operator act with a named source
  (`docs/fiqh-basis.md`); an assessment is not a qualified Shariah source and must never be
  recorded as one.
* **The feed never edits the allowlist.** `assets propose` admits nothing today and continues to
  admit nothing.
* **The feed never runs inside the trading loop.** `agent.run_once` must not be able to reach it.
* **keel does not re-derive the Analyzer's rulings.** The counterpart's non-goals already state
  that Shariah rulings are keel's domain, not theirs; the converse holds too — keel does not
  second-guess their historical analysis, it simply does not let it into the gate.

---

## 6. What an operator actually does

1. `keel credentials set ANALYSIS_FEED_API_KEY` — stored in the OS keychain, as every other
   secret is.
2. `analysis_feed:` block in config: base URL, analyzers to subscribe to, max frame age.
3. `keel analysis fetch` — connects, collects for a bounded window, writes a shortlist file, and
   prints what it got and what it dropped.
4. `keel assets propose --from <that file>` — the existing, tested path. Unchanged.
5. The operator reads the verdicts and attests whatever earns it, by hand, as today.

Steps 3 and 4 may later be one command. They are two here on purpose: the shortlist is a file a
human can read, diff, and keep, and the gate's input stays inspectable.

---

## 7. How we will know whether to keep paying

A measured decision at a fixed date, not a renewal that happens by default:

* **Count**: candidates proposed, candidates that passed the gate, candidates a human actually
  attested. The third number is the only one that matters.
* **Compare** against the same period's manual scouting.
* **Publish the result in `docs/experiments/` whichever way it lands**, in the same shape as the
  cost-fidelity restatements — including "this did not improve on manual scouting", if that is
  what the numbers say.

The subscription is prepaid in fixed periods precisely so this decision has a natural date.

---

## 8. Risks

| risk | mitigation |
| :--- | :--- |
| The feed becomes a de-facto signal by habit — an operator attests because the analysis was persuasive | The gate is unchanged and prose never reaches it; attestation still requires a named qualified source, which an assessment is not |
| A paid dependency in a system that is not profitable | §2.2 states it plainly; §7 schedules the decision |
| An outage or lapse is read as market quiet | Every frame carries `emitted_at`; entitlement `paid_through`/`grace` is surfaced in `keel status` before it bites |
| Config confusion with rail 14's `subscription:` | Distinct `analysis_feed:` namespace, stated in the guide |
| Scope creep into the trading loop | A test asserts `agent.run_once` cannot reach the feed |

---

## 9. Open questions

1. **Desk tier purchase** (§3) — gates everything.
2. **Which analyzers to subscribe to.** All four lenses, or Ibn Khaldun alone? The counterpart's
   product thesis is that divergence between frameworks is the intelligence; keel's use is asset
   discovery, where divergence may be noise. Cheaper to start with one.
3. **Where the shortlist lands.** A file in the deployment folder, or a `agent_state` row? A file
   keeps it inspectable and matches today's `--from` flow.
4. **Crypto relevance.** The Analyzer ingests SEC EDGAR, Federal Reserve, BLS/BEA — macro and
   equities sources. How much of its output names crypto assets at all is unmeasured, and
   materially affects §7's answer. Worth a read-only trial on the free tier before buying Desk.
