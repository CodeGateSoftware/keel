# LLM asset proposer — design

**Date:** 2026-07-24
**Status:** Approved design (pending user review)
**Workstream:** Roadmap direction #3 (LLM product-selection). First concrete slice of the broader
"LLM proposer" umbrella; see **Scope & decomposition** below.

## Context

> "In the future, our LLM will propose a list of assets to trade and must go through the same
> vetting process and test simulations before adding to the user's asset list."

The governing principle for LLMs in this project is the **§5/§6.4 asymmetry** (from
`2026-07-15-keel-autotrade-design.md`): fuzzy/LLM inputs may *reduce* risk directly (pause / veto /
flag / size-down) but may only *increase* activity (a new asset, a new rule, a buy) **after** the
deterministic backtest→paper→promotion / admission gate. An LLM is a *proposer*, never a decider,
and never sits in the reproducible core or the rails (non-reproducible → breaks backtestability).

The vetting gate this proposer must use **already exists**, and building it was the valuable part of
this codebase's compliance work:

- `compliance/screen.screen_asset(facts, attestation, policy) -> ScreenResult` — deterministic
  admission. Checks history depth, liquidity (median daily volume), settlement in the quote
  currency, and the **attested** shariah classification (sector / backing / yield).
  **`attestation=None` fails closed** — sector and backing cannot be derived from price data, so an
  unclassified asset is *unknown*, and unknown is a rejection.
- `_screen_product(repo, product, quote) -> (MarketFacts, ScreenResult)` (in `cli.py`) is the single
  helper behind that call. Its docstring already names this feature: *"`assets screen`,
  `assets holdings --screen` and any future proposer (an LLM shortlist, say) all route through here,
  so none of them can drift onto a laxer path."* The seam is built and waiting.
- Existing candidate **sources** feed that gate: `keel assets discover` (venue-wide metadata →
  shortlist), `keel assets holdings` (the user's own broker balances). `keel assets attest` records
  a **human** shariah classification with a source; `keel assets list` shows what has been attested.

What is missing is a source that answers *"what should we be looking at that we aren't yet?"* —
scouted from the outside world (news, market data) rather than from the venue list or the user's
wallet. That is what the LLM proposer adds.

## Scope & decomposition

"The LLM proposer" is three distinct subsystems that share only the §5 asymmetry principle. They are
**not** built together:

- **(A) Asset/product proposer — THIS spec.** LLM scouts → shortlist of candidate *assets* → the
  existing `_screen_product` admission gate. Lowest-risk (admits nothing by construction), highest
  alignment with existing seams.
- **(B) Strategy proposer — future, own spec.** LLM proposes candidate *rules/strategies* → the
  backtest→paper→promotion gate. Larger and riskier (effectively rule/parameter generation); a
  different gate. Any such spec inherits the externally-sourced-strategy hazard: its proposals
  must clear the fill-model checklist in
  [`docs/experiments/2026-08-27-external-strategy-evaluation-hazard.md`](../../experiments/2026-08-27-external-strategy-evaluation-hazard.md)
  (#529) before porting effort is spent on them.
- **(C) Insights / veto / anomaly-flagger — future, own spec.** The "may reduce risk directly" side
  of the asymmetry (explain, pause, flag). Overlaps with insights/journaling.

## Goals

1. **A new candidate source, not a new gate.** Ingest an externally-produced shortlist and run each
   candidate through the *existing, unmodified* `_screen_product` path.
2. **Outside-first hybrid.** The non-deterministic scouting (LLM + web search) happens **outside**
   keel — the operator, or the operator's Claude using the already-installed firecrawl skills,
   produces a structured shortlist file. keel owns only the deterministic half: validate → screen →
   report. keel stays dependency-free, secret-free (no new API key), and fully unit-testable. An
   embedded scouting adapter is a noted future extension, never a thing keel's correctness depends on.
3. **Provenance is enforced by code, not intention.** Every proposed candidate must carry at least
   one source citation, or it is rejected at schema validation — the §5 "trace what the LLM
   proposed" requirement made structural.
4. **Admit nothing.** The command writes nothing, attests nothing, and never mutates
   `config.allowlist`. Its entire output is a report of gate verdicts and next steps.

## Non-goals

- **No change to `screen_asset`, `ScreenPolicy`, `_screen_product`, or the attestation requirement.**
  If admitting an LLM pick required weakening the gate, that would be evidence against the pick, not
  against the gate.
- **No embedded LLM / web-search API, no new dependency, no second secret** in this slice. The
  scouting step is external. (Pluggable embedded adapter = future extension.)
- **No proposal-audit DB table** in v1. Provenance flows into the system when a **human** later runs
  `keel assets attest <asset> --source <url>` for a candidate they judge worth classifying. YAGNI
  until an audit trail is actually needed.
- **No source fetching / verification.** keel validates that citations are present and well-formed
  URLs; it does **not** fetch them (that is a non-deterministic network call, and judging a source
  is the human's job).
- No automatic attestation, no automatic `keel fetch`, no allowlist mutation.
- Strategy proposal (B) and insights/veto (C) are out of scope (separate specs).

## Design

### 3.1 `keel assets propose`

```
keel assets propose --from <shortlist.json> [--json]
```

Unlike `assets holdings` (where `--screen` is opt-in), `propose` **always screens** — surfacing a
candidate without running it through the gate would defeat the point — so there is no `--screen`
flag to forget.

1. **Ingest & validate** the shortlist file (schema §3.2). A malformed file is a clean
   `ClickException` naming the problem; an individual entry missing required fields (esp. an empty
   `sources` list) is reported as an **invalid entry** and excluded from screening — never silently
   dropped.
2. For each valid candidate `asset`, form the product `"<asset>-<quote_currency>"` and:
   - report whether it is already on `config.allowlist`;
   - report whether an attestation exists (`repo.get_asset_attestation`);
   - echo the proposal's `rationale`, its `sources`, and — clearly labeled **UNVERIFIED** — its
     `shariah_hypothesis` if present;
   - the full `ScreenResult` from `_screen_product` — the same `ADMIT`/`REJECT` + failure reasons
     that `keel assets screen` prints.
3. Print the **next steps** for any candidate that is not already admitted, reusing the holdings
   spec's wording: the `no local history` → `run keel fetch --products <asset>-<quote>` guidance
   (a MISSING-DATA verdict, not a verdict about the asset), and, for an unattested candidate,
   `then human-classify with keel assets attest <asset> --sector … --source …`, then backtest.

The command **never** writes: no attestation, no allowlist change, no DB mutation. It is a read-only
report, in the same family as `assets discover` / `assets holdings`.

### 3.2 Proposal schema (JSON)

A single JSON object with a `candidates` array. JSON (not YAML) because the shortlist is
machine-produced by an LLM, and it joins the existing `--json` ecosystem.

```jsonc
{
  "candidates": [
    {
      "asset": "SOL",                        // required; symbol, quote is inferred
      "rationale": "High developer activity and liquidity; ...",  // required, non-empty
      "sources": [                            // required, >= 1 well-formed URL
        "https://www.coindesk.com/...",
        "https://coinmarketcap.com/currencies/solana/"
      ],
      "shariah_hypothesis": "utility L1, no interest-bearing mechanism"  // optional, UNVERIFIED
    }
  ]
}
```

Validation rules (all deterministic, unit-tested):
- top-level must have a `candidates` list; else clean error.
- each entry: `asset` non-empty string; `rationale` non-empty string; `sources` a non-empty list of
  strings that parse as `http(s)` URLs. A failing entry is collected into an `invalid` list with the
  reason and **excluded from screening** (reported, not screened, never admitted).
- `shariah_hypothesis` is optional and, if present, is displayed with an explicit `UNVERIFIED —
  never used for admission` prefix. **It is never passed to `screen_asset` and can never become an
  attestation.** Only `keel assets attest`, run by a human, creates an attestation.

### 3.3 One gate, shared by construction

`assets propose` calls the **same** `_screen_product(repo, product, quote)` helper as
`assets screen` and `assets holdings --screen`. A test asserts all three produce the identical
verdict for the same asset. Because `attestation=None` fails closed, a freshly-scouted asset with no
human attestation and no cached history will `REJECT` — which is correct: the proposer surfaces
candidates for human attestation + data-fetch + backtest, it does not admit them. This is the
mechanism that makes "the same vetting process" a property of the code rather than an intention.

### 3.4 Module layout

- `keel/proposer.py` — pure, dependency-free: `Proposal`/`Candidate` dataclasses, `parse_proposal(
  raw: dict) -> ParsedProposal` (returns valid candidates + invalid entries with reasons), and a
  `ProposalReport` builder that composes each candidate with its `_screen_product` verdict.
  Fully unit-testable with fixture dicts + an in-memory repo; no I/O, no network, no LLM.
- The thin `assets propose` click command (alongside the existing `assets` subcommands) does file
  read + JSON parse + calls the pure builder + renders (human or `--json`). Mirrors the
  testable-core / thin-I/O split used by `status`/`tui`/`insights`.

### 3.5 "Off by default", honestly

In the outside-first hybrid there is no embedded LLM and no network call inside keel, so there is
nothing to *disable*: the command is inert until the operator runs it against a file, and it admits
nothing when they do. No config `enabled` flag and no key gate are needed for this slice — a flag
would be friction on a read-only report. (An embedded scouting adapter, if ever added, is where an
`enabled: false` default + API-key gate would live — see Future extensions.)

## Testing

Deterministic and fully unit-testable (no LLM, no network):

- **Schema validation:** valid file parses to the expected candidates; an entry with empty/missing
  `sources` lands in `invalid` with a citation reason and is excluded from screening; a
  non-URL source is rejected; a malformed top-level structure raises a clean error.
- **Shared-gate equivalence:** `assets propose` and `assets screen` return the same
  `ScreenResult` for the same asset (one gate, by construction).
- **Admits-nothing:** after a run, no attestation, no allowlist entry, no DB row was written
  (assert repo state unchanged).
- **`no local history` vs bad asset:** a scouted asset with zero cached bars produces the
  MISSING-DATA verdict + the `keel fetch` next-step, not a verdict about the asset (reused from the
  holdings path).
- **`shariah_hypothesis` never admits:** an entry whose hypothesis claims a compliant class still
  `REJECT`s when `attestation=None`; assert the hypothesis is never passed into `screen_asset`.
- **CLI:** `--json` is valid `json.loads` output with no trailing prose; human output ends with the
  standard disclaimer; a missing `--from` file is a clean error.

## Future extensions (explicitly deferred)

- **Embedded scouting adapter** — a pluggable, off-by-default, API-key-gated component that produces
  the same shortlist schema from inside keel (LLM + firecrawl/coinmarketcap API). keel's ingest +
  screen half is unchanged; only the *source* of the JSON changes. This is where an `enabled: false`
  default and a secrets-vault key would live.
- **Proposal audit log** — an append-only record of what was proposed, when, and with what citations,
  if a provenance trail beyond the human's `attest --source` is ever wanted.
- **Strategy proposer (B)** and **insights/veto (C)** — separate specs.
