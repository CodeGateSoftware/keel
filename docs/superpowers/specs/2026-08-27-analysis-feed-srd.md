# SRD — Analysis feed: consuming Ibn Khaldun Analyzer

**Status:** proposed · **Date:** 2026-08-27 · **PRD:**
[`2026-08-27-analysis-feed-prd.md`](2026-08-27-analysis-feed-prd.md) · **Counterpart:**
[Ibn-Khaldun-Analyser](https://github.com/CodeGateSoftware/Ibn-Khaldun-Analyser) SRD §5

---

## 1. Architecture

```
  Ibn Khaldun Analyzer                     keel
  ────────────────────                     ────
  GET /v1/stream/sse   ──frames──▶  keel/analysis/feed.py     (I/O: urllib, stdlib only)
  text/event-stream                          │
                                             ▼  AnalysisFrame (typed, no venue/vendor shapes)
                                    keel/analysis/adapter.py  (PURE: frame → Candidate)
                                             │
                                             ▼  shortlist.json  ({asset, rationale, sources[]})
                                    keel assets propose --from …   ← UNCHANGED, already tested
                                             │
                                             ▼  screen_fn(repo, product, quote)
                                    keel/compliance/screen.py       ← UNCHANGED
                                             │
                                             ▼
                                    a report a human reads. Admits nothing.
```

Three properties this shape buys, each of which is a requirement rather than a nicety:

* **The gate's input never widens.** `screen_fn` still takes `(repo, product, quote)`. The
  rationale and the citations are carried alongside for a human to read and are not arguments to
  admission.
* **The existing ingestion path is reused, not reimplemented.** `proposer.py` already validates
  this exact structure and rejects malformed entries per-candidate. A second parser would be a
  second place to get validation wrong.
* **The boundary is a file.** Inspectable, diffable, keepable — and the same `--from` flow an
  operator uses today.

### 1.1 Module placement

| module | kind | rules it lives under |
| :--- | :--- | :--- |
| `keel/analysis/feed.py` | I/O — HTTP, streaming, retries | not in the thinness pin's file set; may open a socket |
| `keel/analysis/adapter.py` | pure — frame → `Candidate` | no I/O, no clock, no config; unit-testable on recorded frames |
| `keel/commands/analysis.py` | service — orchestrates fetch, writes the shortlist | the `_build_broker`-style seam pattern |
| `keel/cli.py` | composition — `keel analysis fetch|status` | thin: parse options, call the service, echo |

`keel/analysis/` is a new top-level subpackage rather than a `keel/commands/` module because
`keel/commands/` is 13,606 lines and #525 exists about that; a feed client is not a command.

---

## 2. The wire contract we consume

From the counterpart's SRD §5.3 and PRD §3.8.3. keel consumes **only** these fields:

| field | use | required |
| :--- | :--- | :--- |
| `type` | frames other than `analysis` are ignored | ✓ |
| `emitted_at` | staleness, judged by keel (§4.2) | ✓ |
| `analysis_id` | idempotency — the dedupe key | ✓ |
| `analyzer.id` | provenance, recorded in the rationale | ✓ |
| `signal.entities[kind="ticker"]` | → `Candidate.asset` | ✓ (Desk) |
| `signal.sources[]` | → `Candidate.sources` | ✓ (Desk) |
| `prose.conclusion` | → `Candidate.rationale` (never reaches the gate) | ✓ |
| `entitlement.paid_through` / `.grace` | surfaced in `keel status` (§5) | ✓ |

Everything else in the frame — `dimension_scores`, `confidence`, `direction`, `horizon`,
`historical_parallels` — is **deliberately not consumed**. Each is a number or a judgement that a
future reader could be tempted to route into sizing or timing, and PRD §5 forbids that. A field
keel does not read is a field keel cannot misuse.

> `direction`, `horizon` and `confidence` are exactly the shape of a trading signal. They are the
> reason the "we consume only these fields" list is a *contract* and not documentation.

---

## 3. Data structures

```python
@dataclass(frozen=True)
class AnalysisFrame:
    """One `analysis` frame, narrowed to what keel reads. Nothing vendor-shaped survives."""
    analysis_id: int
    emitted_at: int          # unix seconds, parsed from the frame's ISO-8601
    analyzer_id: str
    tickers: tuple[str, ...]
    sources: tuple[str, ...]
    conclusion: str

@dataclass(frozen=True)
class Entitlement:
    paid_through: int | None   # unix seconds; None when the frame did not carry one
    grace: bool

@dataclass(frozen=True)
class FetchReport:
    """What one `keel analysis fetch` did — every number an operator needs to trust the file."""
    frames_seen: int
    candidates_written: int
    dropped_stale: int
    dropped_no_ticker: int
    dropped_no_sources: int
    dropped_duplicate: int
    entitlement: Entitlement | None
    error: str | None          # a reachability/auth failure, stated; never raised
```

`dropped_*` are counted and reported rather than logged and forgotten: a fetch that silently
produced three candidates from four hundred frames is indistinguishable from a working one unless
the drops are on screen.

---

## 4. Behaviour

### 4.1 Connection

* `GET {base_url}/v1/stream/sse` with `Authorization: Bearer <key>` and filters as query
  parameters, per the counterpart's §5.3.
* `urllib.request.urlopen` with an explicit timeout. Read line-by-line; an SSE frame is
  `data: <json>` lines terminated by a blank line.
* **Bounded by construction**: a maximum wall-clock duration, a maximum frame count, and a
  maximum bytes-per-frame. The command returns when any bound is reached. It is not a daemon.
* **No retry into the trading loop.** A single connection attempt; on failure the report carries
  `error` and zero candidates. Retrying is the operator's to do by running the command again.

### 4.2 Staleness — keel's judgement, not the server's

The counterpart is explicit that "the server does not decide on the subscriber's behalf what
counts as too old", and every frame carries `emitted_at` for that reason.

`analysis_feed.max_age_sec` (default: 24h, matching the live agent's daily cadence) drops older
frames and counts them in `dropped_stale`. A frame with an absent or unparseable `emitted_at` is
dropped, not defaulted to "now" — the failure mode of guessing is treating an old assessment as
current.

### 4.3 Idempotency

`analysis_id` is the dedupe key. A shortlist never contains the same analysis twice, and a
re-fetch over an overlapping window does not re-propose what a human already screened. The set of
seen ids is persisted in `agent_state` under a namespaced key.

### 4.4 Mapping a frame to a candidate

```
asset      ← the first entity with kind == "ticker"   (no ticker ⇒ dropped, counted)
sources    ← signal.sources                            (empty ⇒ dropped, counted)
rationale  ← "{analyzer_id}: {prose.conclusion}"       (provenance carried in the text a human reads)
```

`shariah_hypothesis` is left **unset**. `Candidate` has the field, and the temptation is to fill it
from the analysis. Nothing in the Analyzer's output is a qualified Shariah source — its own
non-goals say so — and a hypothesis sourced from it would be a ruling wearing a citation.

### 4.5 Failure modes, and what each does

| condition | behaviour |
| :--- | :--- |
| feed unreachable / DNS / timeout | `error` set, zero candidates, exit 0 — nothing was learned, nothing broke |
| 401 / 403 | `error` names authentication; the operator checks `keel credentials show` |
| entitlement lapsed (frames carry no `signal`) | every frame drops for no ticker; the report says so and prints `paid_through` |
| malformed JSON in a frame | that frame drops, counted; the stream continues |
| no frames within the window | zero candidates, no error — a quiet feed is not a failure |

Every one of these ends in the same place: **a shortlist with fewer candidates, and a normal keel.**

---

## 5. Entitlement visibility

The counterpart built `entitlement.paid_through`/`grace` into every frame specifically so an
unattended agent can see its own expiry rather than meet it as unexplained silence.

* `keel analysis status` prints the last-seen entitlement and the age of the last frame.
* `keel status` gains one line when a feed is configured, in the same shape as the withdrawal
  attestation's expiry line — which is the existing precedent for "a credential with a clock on it".
* The browser's Status view renders it through the existing `payload.moment`/`flag` helpers; no new
  display vocabulary.

Absent configuration, none of this appears. A deployment that does not subscribe sees nothing.

---

## 6. Configuration

```yaml
# NOT `subscription:` — that key is rail 14's per-venue fee-tier attestation and is enforced
# against live trading. These two have nothing to do with each other and must never be confused.
analysis_feed:
  base_url: "https://…"
  analyzers: ["ibn_khaldun"]      # start with one; see PRD §9.2
  max_age_sec: 86400
  max_frames: 200
  max_duration_sec: 60
```

The API key is **not** in config. `ANALYSIS_FEED_API_KEY` resolves through the existing
`keel_core.secrets` chain — environment, `.env`, OS keychain — exactly as the venue credentials do.

---

## 7. Security

* **Key handling** is the existing one: `keel credentials set`, keychain-backed, never logged,
  never echoed, `ResolvedSecret.__repr__` never prints a value.
* **TLS is required.** An `http://` base URL is refused rather than warned about; the key is a
  bearer credential.
* **Bounded reads throughout.** Frame size, frame count and duration are all capped, for the same
  reason `server._read_json_object` caps a request body: an unbounded read from a remote party is
  a memory-exhaustion primitive.
* **The feed is never a capability.** It appears in no `keel/capabilities.py` row, because it
  increases nothing: it cannot attest, promote, arm, spend or place. If a future change gives it
  any of those, that change adds the row *and* the gate.
* **Sources are recorded, not fetched.** keel stores the citation URLs and never retrieves them.
  Rendering a remote document would be an injection surface for no gain.

---

## 8. Testing

| property | how |
| :--- | :--- |
| adapter correctness | recorded frames → expected `Candidate`s; pure, no network |
| every drop reason | one test per `dropped_*` counter, asserting the count and that nothing was written |
| stale frames are dropped, not defaulted | a frame with `emitted_at` older than `max_age_sec`, and one with it missing |
| the gate's input never widens | assert `screen_fn` is called with exactly `(repo, product, quote)` — mutation: passing the rationale fails |
| **the trading loop cannot reach the feed** | AST scan: no module reachable from `agent.run_once` imports `keel.analysis.feed` |
| no path to attestation or the allowlist | AST scan over `keel/analysis/`: no `set_state` on an attestation key, no allowlist write, no `place_order` |
| TLS is required | an `http://` base URL is refused |
| bounded reads | an oversized frame is dropped without being read whole |
| fail-closed | each row of §4.5 exits 0 with zero candidates |

The AST scans matter more than the unit tests. The unit tests prove the adapter maps fields
correctly today; the scans prove that a later change cannot quietly wire an assessment into
admission or into a cycle.

---

## 9. What this does not build

* No WebSocket client. SSE is the sanctioned path and needs no dependency.
* No comparison consumption. `/v1/events/{id}/comparison` is Analyst+ and its product — divergence
  between frameworks — is not an asset-discovery input.
* No local storage of prose beyond the shortlist file. keel is not a news archive.
* No automatic `assets propose` invocation. Two commands, on purpose (PRD §6).
