# PRD — The TUI as keel's operator console

**Status:** proposal (captured 2026-08-19 from the operator; not started) ·
**Tracking:** milestone *Phase 13 — TUI: the operator console* · **Predecessors:** the current
TUI (`keel/commands/tui.py`, the #345-era dashboard), Phase 12 B1/B2 (session awareness, venue
selection, the equities profile)

## 1. Problem

keel's features live in the CLI: 24 top-level commands across rule lifecycle, compliance
attestation, data maintenance, evidence, and trading control — each already a thin, tested,
logged service invocation. The TUI is a *viewer*: a beautiful dashboard with a handful of keys,
but the operator still drops to a shell for every action, and the actions that matter most
(admissions, attestations, promotions) are the ones with the most ceremony to remember.

The ask: make the TUI the **operator console** — every safe operation invokable from a
menu/sub-menu structure, every invokable action routed through the *same service layer the CLI
uses*, never a reinvented or duplicated function.

## 2. Feature inventory (what can be surfaced today)

**Deployments (4):** paper-forward (daily crypto, `keel.db`), live (`keel-live.db`),
paper-hourly (19 assets, `keel-paperhourly.db`), paper-equities (5 tickers, `keel-equities.db`)
— each a config+db pair with its own wrapper.

**Trading control:** `agent` (single cycle / loop), `monitor`, `kill` / `resume` (typed),
`resume-entries` (rail 16, typed), `autonomy`, `record-flow` (rail 11), `reset-hwm`.

**Rule lifecycle:** `rules add / seed / list / backtest / promote / demote / disable / enable`;
`simulate` (deterministic replay, DCA benchmark, GO-LIVE/TRAIN-MORE report); `insights`
(promotion-gate distance); the pooled promotion gate.

**Compliance:** `assets screen / propose / attest / attest-instrument / list / holdings /
discover / exempt / unexempt`; `subscription attest / set` (venue-bound since B2);
`withdrawals attest` (rail 17, typed); `purification` (KB §65.9 report); the kill switch.

**Data:** `fetch` (+ `--check`, `--repair-gaps`, `--reprobe-absent`, `--refresh`),
`db import` (Coinbase CSV), freshness/staleness (session-aware since B1), candle cache.

**Evidence & research:** `trials record / list / verify / pbo / deflate` (hash-chained ledger);
`docs/experiments/` and `docs/research/` corpora; promotion reports; `pnl` (FIFO);
the keel-asset-scout proposals (operator-local `~/keel/proposals/`, outside the repo).

**Observability:** `status` (rails, market session, positions, freshness), the current TUI
dashboard (rails/positions/freshness/activity), `versions`.

## 3. Objectives

- **O1 — Console, not viewer:** menu/sub-menu navigation covering the inventory above; every
  read is browsable, every safe write is invokable.
- **O2 — Thin by construction:** the TUI renders and dispatches. All behavior comes from the
  same services the CLI calls; where logic currently lives inside `keel/cli.py` command bodies
  (fetch, simulate, …), it is *extracted* into the shared service layer first — one
  implementation, two front-ends. No TUI-local re-implementations of sizing, screening,
  admission, gating, or reporting.
- **O3 — The typed-confirmation contract is sacred:** actions that are deliberately
  human-terminal (`resume`, `kill` is one-key-but-logged per its own contract,
  `withdrawals attest --enabled`, `rules promote --force`, `resume-entries`) are invoked from
  the TUI but keep their typed prompt, rendered in-console. The TUI never pipes, pre-fills, or
  bypasses. Live-profile selection is guarded the same way.
- **O4 — Profile switching:** the console can point at any of the four deployments (operator
  examples #1), showing which config/db pair is active before any action; switching is
  explicit and visible in every screen's header.
- **O5 — Readers for the evidence corpus:** experiments, research docs, promotion reports, the
  trials ledger (`list`/`verify`), and scout proposals are browsable in-console (operator
  example #2).
- **O6 — The scout-results handler:** after a keel-asset-scout run writes its proposals
  (operator-local, proposer-never-decider by design), the console lists them and drives the
  EXISTING admission flow — `assets propose` → `screen` → human-typed `attest` → allowlist —
  reusing `keel/commands/admission.py` end to end (operator example #3). It never auto-attests
  and never writes outside supported service paths.
- **O7 — Venues/brokers visibility (service-first):** a small `brokers` service (over the
  existing entry-point registry, `discover_brokers()`, and `BrokerCapabilities`) listing every
  installed adapter — name, venue id, wired-for-deployment vs optional-dev-venue, session-bound
  or 24/7, quote currency, asset classes, paper/live endpoints where declared, preview
  synthesis, supported data feeds — plus a `keel brokers list` CLI surface; the TUI's Profile
  area renders it as a Venues browser with the SELECTED adapter highlighted and the active
  deployment's binding shown. Capability display, not key-presence inference (#233-aligned);
  no secret values ever shown.
- **O8 — Newbie-friendly help & glossary:** every screen carries "what am I looking at" help
  and every invokable action a plain-English "what will this do" description — written for a
  newcomer (what a rail IS, what an attestation records, what the promotion gate demands, what
  the kill switch does, what paper mode means). Definitions live in ONE source (a glossary the
  TUI help renders and the docs link to — not a second, drifting copy), and the typed actions'
  help text says explicitly that the prompt cannot be pre-filled.

## Menu tree (v1 shape — final naming in implementation)

```
Dashboard            the current live view (rails, session, positions, freshness, activity)
Profile              switch paper-forward | paper-hourly | paper-equities | live (guarded)
  └─ Venues          installed adapters + capabilities (O7); selected one highlighted
Trading              agent cycle (confirm) · monitor poll · autonomy · record-flow ·
                     reset-hwm · resume-entries (typed) · kill · resume (typed)
Rules                list/select · backtest · promote (confirm/--force typed) ·
                     disable · enable · demote · add · insights
Compliance           screen · propose · attest (typed) · attest-instrument · exempt/unexempt ·
                     holdings · discover · [Scout results…] · subscription (show/attest) ·
                     withdrawals attest (typed) · purification
Data                 fetch · fetch --check · repair gaps · freshness overview · db import
Research             experiments · research docs · promotion reports · trials (list/verify) ·
Account              pnl · versions
Help                 glossary · per-screen "what am I looking at" · per-action "what will
                     this do" (O8) — also reachable contextually from every screen
```

## 4. Non-objectives

No new business logic, no strategy/rail/gate changes, no LLM anywhere, no auto-trading or
scheduled actions born in the TUI, no network beyond what the services already do, no mobile/web
ambitions. The TUI is a front-end to what exists; if a feature is missing, the fix lands in the
service layer and both front-ends get it.

## 5. Phasing (one issue each)

- **C1 — Service extraction audit.** Enumerate every CLI command body; extract CLI-coupled
  logic (fetch, simulate, others found) into the shared layer the `keel/commands/*` modules
  already represent; CLI behavior byte-compatible; pin with tests that both front-ends call one
  implementation. *Foundation — everything else depends on it.*
- **C2 — Console shell + profile switching.** Menu/sub-menu framework over the current
  dashboard (which remains the landing screen); the Profile menu (O4); active-profile header on
  every screen.
- **C3 — Compliance menu + the scout-results handler (O6).** The admission flow through real
  services; the proposals browser reading the operator-local path via config.
- **C4 — Rules + Research menus.** Rule lifecycle actions; the evidence readers (O5).
- **C5 — Trading + Data menus.** Cycle/poll invocations with their confirmations; fetch/repair
  surfaces; every typed action contract preserved (O3).
- **C6 — Safety & polish pass.** Keybinding/help consistency; an adversarial review dedicated to
  the typed-confirmation contract and live-profile guards; docs (runbook TUI section).
- **C7 — Venues/brokers visibility + the help & glossary system.** The `brokers` service +
  `keel brokers list` CLI (O7) with the TUI Venues browser under Profile; the single-source
  glossary and the contextual help framework (O8), whose per-screen/per-action strings land
  with each menu slice (C2–C5) and are consolidated + audited here.

## 6. Success criteria

1. Every inventory section is reachable in the menu tree; no dead menu item.
2. `grep`-able proof of no duplicated logic: the TUI module imports services, contains no
   sizing/screening/gating/reporting math (pinned by an architectural test if feasible).
3. Typed actions behave identically to CLI (same prompts, same logs, same audit events).
4. The scout handler drives propose→screen→attest for a real proposal file end-to-end in paper.
5. Profile switching visibly rebinds config/db everywhere in one action.
6. `keel brokers list` and the TUI Venues browser show identical information from one service,
   including capabilities and the selected adapter — with no secret material.
7. Every screen and action has help text a newcomer can understand; glossary definitions have
   exactly one source and no drifted duplicates.

## 7. Risks

- **Extraction regressions** (C1 touching `fetch`/`simulate`) — byte-compatible pins + the full
  suite; extract in small PRs.
- **Console scope creep into an auto-pilot** — O3's contract is the fence; C6 reviews it
  adversarially.
- **The current TUI's rendering complexity** — the shell lands *around* the existing dashboard,
  which stays the landing screen; no rewrite of working views.
- **Scout-proposal path assumptions** (operator-local, outside the repo) — read via config with
  a clear empty state, never a hardcoded home-dir guess.
