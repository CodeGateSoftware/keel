# keel — Software Architecture Record (SAR)

**Status:** living document. Facts below verified against `main` on 2026-08-31 (through
the #649 merge). Where a claim is about the future, it names the issue that carries it.
**Companions:** `docs/evolution-plan.md` (where this architecture is going),
`docs/decisions/` (the ADRs — individual load-bearing choices), the runbooks
(`docs/operator-runbook.md`, `docs/go-live-runbook.md`).

---

## 1. What keel is

A self-hosted trading engine whose defining property is that its evidence discipline is
part of the architecture, not a policy layered on top: a significance tool that cannot
say no is refused at review; scores report and gate but never rank; negative results are
published. Every architectural decision below is tested against that constitution.

It ships today in three shapes, all from one artifact set:

- **CLI** — `keel` commands over a local database (the operator's terminal).
- **Local web console** — `keel serve`, loopback-only, zero-dependency static UI.
- **Desktop distribution** — unsigned installers plus the `keel update` self-updater.

## 2. Package and module architecture

Monorepo, one distribution per concern, Python floor 3.14 (`requires-python = ">=3.14"`):

```
keel-trader (root)          the application package
├── keel/agent.py           the cadence: fires hourly, trades once per UTC day
├── keel/cli.py             command surface
├── keel/commands/          console modules — one concern per file (research, confirm,
│                           update, serve, brokers, …); thinness is pinned by test
├── keel/strategy/          rules, backtest engine, exit policy, CTS indicators,
│                           paper trading, the promotion ladder
├── keel/research/          thirteen evidence modules (significance, montecarlo, cscv,
│                           deflate, walkforward, independence, throughput, cts_factors,
│                           ledger, pooled_review, bias, matrix, tuning)
├── keel/analysis/          candles, indicators, levels, P&L, regime
├── keel/data/              db, history, market_feed, gaps, freshness, cb_client
├── keel/execution/         order placement and the fill path
├── keel/compliance/        screen.py (the attestation ceremony), purification.py
├── keel/sim/               simulation
├── keel/web/               server, api, payload, events (SSE), staticfiles, security
├── keel/mcp/               the read-only MCP server (query_only enforced)
└── keel/templates/         config templates

packages/keel-core             shared kernel
packages/keel-broker-api       the Broker port + capabilities contract
packages/keel-broker-coinbase  live venue
packages/keel-broker-alpaca    equities venue (cash-only rail enforced)
packages/keel-broker-kraken    port-complete stub, honest about implementing nothing (#313)
packages/keel-broker-robinhood dev-only; refuses to fake missing wiring
packages/keel-broker-fake      the test venue
```

The stub pattern above is architecture, not placeholder: an adapter that returned empty
lists would *look* working while answering every question with nothing. Refusal to fake
is a load-bearing property across the codebase.

## 3. The load-bearing decisions, and what each carries

| Decision (in the code today) | Carries |
|---|---|
| Wheel-per-concern; same artifacts for CLI, desktop, hosted | Free and paid tiers are literally the same bytes; the evolution's hosted tier needs no engine fork |
| Single-user engine + SQLite WAL per profile (ADR 0002) | Container-per-tenant hosting with zero engine rewrite; no phase requires in-process multi-tenancy |
| Broker port + capabilities (incl. the `multiplier == 1` cash rail, #372) | Venue breadth as extension, not surgery. The rail that blocks options (#637) is the product working as designed |
| Compliance as ceremony + data (`screen.py` refuses unsourced attestations) | Equities screening (#370), IKA feed attestation (#570–#578), options exceptions (#637) all reuse one mechanism |
| `keel serve` loopback **by design** with DNS-rebinding bind checks | `keel link` (#648) extends a threat model that exists; security is not bolted on later |
| Release pipeline: 5 wheels + desktop matrix, gated signing, attestations; `keel update` self-updater | Pro-tier artifacts and managed updates are built and waiting, not engineered |
| Experiment records reproducible (#265); docs under test (incl. `test_workflow_triggers.py`) | The Fee Reality benchmark's honesty (#646) is enforced by infrastructure |
| Evidence bound to the credential that earned it (#633/#635) | The audit primitive the hosted-confirm relay model requires |

## 4. Security architecture

- **Loopback bind by design** with Host/bind checks against DNS rebinding; remote
  exposure is deliberately absent until #648 lands the opt-in bind, tunnel Host
  allowlist, and off-loopback token posture.
- **One-time session token** URLs; CSP `default-src 'self'` with no `unsafe-inline`
  (pinned by test); the key-parity scanner ensures the payload emits every key the
  client reads.
- **MCP is read-only at the engine level** (`query_only`): it cannot attest, promote,
  arm autonomy, or place an order — by construction, not policy.
- **Credentials:** `.env` today; OS keychain via `keyring` is the #437 wizard's plan.
  Evidence is fingerprint-bound to credentials (#633).
- **Honest refusal as a security property:** Robinhood refuses rather than faking;
  Kraken stubs loudly; installer truths are pinned by tests.

## 5. Data architecture

- SQLite WAL, one database per profile — ADR 0002 records why and the trigger that
  would change it. Migrations via `keel migrate`; idempotent-on-start is #437's ask.
- **The trials ledger** — hash-chained JSONL, schema v11; the trials budget with
  provenance. Append-only: dated records never rewritten.
- **Freshness is measured** (`keel/data/freshness.py`) — the open gap is operational:
  wrappers do not gate on it (fetch → doctor → cycle is the filed shape).
- **Spread is unmeasured** (#626) — at this deployment's clip sizes, spread is the
  actual cost; the measurement program is the live book's purpose.

## 6. Serving and interface architecture

- `keel/web`: `server.py` (bind + routing), `api.py`/`payload.py` (one JSON contract,
  key-parity pinned), `events.py` (SSE live updates), `staticfiles.py`,
  `security.py` (header set).
- The web UI is **zero-dependency** static ES modules — view-source is a feature. Its
  constraints are pinned by tests: identity pins, palette contrast suite, deterministic
  icon generation, the CSP one-script pin.
- PWA: manifest + service worker ship for offline/install only. **Web Push is
  greenfield** (corrected 2026-08-31, #649): no push handler, subscription endpoint,
  or payload encryption exists; all are new work behind #648.

## 7. Release and distribution architecture

`release.yml` (workflow_dispatch): version-agreement tests → `uv lock` check → suite →
tag → build wheels + desktop matrix (macOS DMGs, Windows zip + Inno Setup setup.exe) →
SHA256SUMS + attestations → publish. **Signing is built but gated**: absent secrets
produce honest skip notes (the #438 Option D decision — activation is a secrets-drop,
not a project). CI runs on every merge by design (post-merge runs see semantic merge
conflicts); docs-only changes skip the workflows that read no documents (#644/#645),
while ci.yml stays unfiltered because the docs themselves are under test.

## 8. Verified open architecture work

| Gap | Issue |
|---|---|
| Remote-exposure security pass (bind opt-in, tunnel Host validation, off-loopback tokens) | #648 — prerequisite for all of Phase B |
| Approval protocol primitives (`idempotency_key` + `proposal_hash`, expiry, non-TTY confirm route) | design recorded in `docs/evolution-plan.md` §5 Phase B; implementation unfiled |
| Web Push (VAPID, subscription, RFC 8291, sw handler) | greenfield, gated on #648 |
| Operational freshness gating (fetch → doctor → cycle; per-product staleness gate) | the live-deployment session's open question; `freshness.py` measures, wrappers don't ask |
| Equities fundamentals pipeline (SEC XBRL `companyfacts`, pinned quarterly snapshot) | #370 |
| Options: amend or hold the constitution | #636 discovery → #637 ADR 0005 |
| Control plane, BTCPay rail, hosted-confirm signer (native app or WebCrypto/dumb-relay spike) | #638, `docs/evolution-plan.md` §5 Phase F — all external to the engine |

## 9. Architectural refusals on record

- **No shared-Postgres multi-tenant rewrite** — it would sacrifice ADR 0002 and the
  identical-artifact property between tiers.
- **No engine feature gates for paid tiers** — the free engine is never a demo.
- **No feed-derived ranking keys** — an assessment may inform a human attestation; it
  may never be one (#570–#578 rails).
- **No affiliate revenue, no strategies for sale, no hosted black box** — recorded in
  ADR 0004's scope (#603) and `docs/evolution-plan.md` §2.

## 10. Traceability

- ADRs: `docs/decisions/` — 0001 desktop update path, 0002 SQLite persistence,
  0003 commands-layer survey; 0004 (monetisation) pending #603; 0005 (options) pending #637.
- Strategy: `docs/evolution-plan.md`.
- Runbooks: `docs/operator-runbook.md`, `docs/go-live-runbook.md`, `docs/RELEASING.md`.
- This document's rule: a claim about the present is verified against `main` at the
  date above; a claim about the future names its issue.
