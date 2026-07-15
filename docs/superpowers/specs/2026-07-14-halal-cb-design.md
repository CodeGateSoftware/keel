# halal-cb — Design Spec

**Date:** 2026-07-14
**Status:** Approved design — ready for implementation planning
**Author:** Elmehdi Aitbrahim (with Claude)

---

## 1. Purpose

`halal-cb` is a local Python CLI that helps the user run and enforce a **riba-free (interest-free) crypto investing strategy** on their existing Coinbase retail account. It is a thin **compliance + strategy layer** on top of the official `coinbase-advanced-py` SDK — it does not reinvent trading plumbing.

The tool answers three questions on every run:
1. **Where do I stand?** (allocation, cost basis, P&L, fees)
2. **Am I clean?** (is anything earning interest / staking; what must I purify)
3. **What halal buys should I make now?** (previewed DCA limit orders, with the exact fee shown before anything is placed)

## 2. The governing constraint (non-negotiable)

All behavior must comply with the user's rule: **avoid riba (interest) and other non-halal elements per the Islamic view.**

Concretely, the tool has **zero code paths** that touch:
- staking / unstaking
- USDC rewards / "Earn" / any deposit-yield APY
- lending / borrowing / margin / leverage
- Forex / CFDs / derivatives / futures

Permissible and in-scope: owning spot assets outright, fee waivers/discounts (Coinbase One zero-fee trading), and gains from price appreciation. The tool **detects** riba features on the account and reports them for the user to disable manually (they are not exposed by the trading API); it never enables them.

Disclaimer: the tool is informational, not a religious ruling. Binding questions defer to a qualified scholar.

## 3. Scope

**In scope (MVP):** analyze, compliance scan, live fee check, DCA preview (dry-run), decision journal, guarded loss-harvest preview.

**Out of scope (MVP):**
- Auto-trading / scheduled unattended order placement (execution is preview-only; a guarded `--place` exists but requires interactive confirmation).
- Toggling USDC rewards, staking, or the Coinbase One subscription (not exposed by the API — reported as manual actions).
- External withdrawals (the API cannot do this; the tool never attempts it).
- Stock/ETF screening, zakat, tax filing, multi-exchange support.

## 4. Commands

| Command | Description | Data source |
|---|---|---|
| `halal-cb analyze` | Portfolio allocation, cost basis, realized/unrealized P&L, fee audit (pre/post Coinbase One), and % progress toward the configured goal. | Local CSVs (offline) |
| `halal-cb compliance` | Riba scan: flags received USDC "rewards" income and any staking; computes the **purification total** (interest $ to donate); prints a manual-action checklist (turn off rewards, keep assets unstaked). Disclaimer-first. | Local CSVs (offline) |
| `halal-cb fees` | Live maker/taker fee rate + trailing 30-day Advanced volume via `get_transaction_summary`; shows **remaining Coinbase One $500/mo zero-fee allowance**. | Coinbase API |
| `halal-cb dca --preview` | Computes this period's halal buys from target weights + budget + live prices; builds **limit orders** just below market; shows the **exact per-order fee** from `preview_order`. `--place` submits, but only after interactive confirmation + allowlist + cap checks. | Coinbase API + CSVs |
| `halal-cb journal [add\|list]` | Logs each previewed/placed buy with date, price, amount, and rationale; reviewable later (anti-panic discipline). | Local file |
| `halal-cb harvest --preview` | The **only** non-buy trade path: previews selling flagged losers (e.g. XRP/WLD) to realize a tax loss. Guarded like `dca --place`. | Coinbase API + CSVs |

## 5. Configuration (`config.yaml`)

```yaml
goals:
  target_usd: 5000          # placeholder — user edits
  horizon_months: 24        # placeholder — user edits
  purpose: "long-term halal wealth"
allowlist: [BTC, ETH, PAXG] # ONLY these may be bought
target_weights: {BTC: 0.50, ETH: 0.35, PAXG: 0.15}
weekly_budget_usd: 50
cash_buffer_usd: 200        # held aside, interest-free (never a yield account)
limit_offset_pct: 0.3       # place limit orders 0.3% below market (maker side)
caps:
  max_per_order_usd: 25
  max_per_day_usd: 60
```

Secrets (CDP API key/secret) live in a git-ignored `.env`, never in `config.yaml`, never logged.

## 6. Architecture

Each module is small, single-purpose, and unit-testable in isolation.

```
halal_cb/
  config.py             # load + validate config.yaml and .env
  data/
    csv_loader.py       # parse + dedupe Coinbase transaction CSVs -> normalized records
    cb_client.py        # thin coinbase-advanced-py wrapper: balances, prices,
                        #   fee summary, preview_order, place (guarded)
  analysis/
    portfolio.py        # allocation, cost basis, realized/unrealized P&L, goal progress
    fees.py             # fee audit (pre/post membership, per type)
  compliance/
    riba.py             # detect reward income + staking; compute purification total; checklist
  strategy/
    dca.py              # budget + weights + prices -> limit orders (allowlist-enforced)
  journal.py            # append/read decision journal (JSONL)
  cli.py                # command wiring (click); rendering; disclaimer footer
tests/
  fixtures/             # sample CSVs + canned API responses
  test_csv_loader.py  test_portfolio.py  test_fees.py
  test_riba.py         test_dca.py       test_journal.py
config.yaml
.env                    # git-ignored
```

**Dependency boundaries:**
- Pure-logic modules (`csv_loader`, `portfolio`, `fees`, `riba`, `dca`, `journal`) have **no network** dependency — they take data in, return values out. Fully unit-tested offline.
- `cb_client` is the **only** module that talks to the network. Thin by design so the logic above it is testable without live money.
- `cli` orchestrates; contains no business logic worth testing beyond wiring.

## 7. Data flow

- **Offline commands** (`analyze`, `compliance`): `csv_loader` → normalized records → `portfolio` / `fees` / `riba` → `cli` renders.
- **Live commands** (`fees`, `dca`, `harvest`): `cb_client` fetches balances/prices/fee-summary → `strategy.dca` builds orders (allowlist + caps enforced) → `cb_client.preview_order` attaches exact fee → `cli` renders. `--place` adds an interactive confirm + re-check of caps/allowlist before `cb_client.place_order`.

## 8. Safety model (defaults)

1. **Read-only by default.** Analysis/preview need no write scope.
2. **Preview-only execution.** Orders are dry-run unless `--place` + interactive `yes` confirmation.
3. **Product allowlist.** Only pairs derived from `allowlist` (e.g. `BTC-USDC`) may be ordered. Any other product is rejected in code.
4. **Spend caps.** `max_per_order_usd` and `max_per_day_usd` enforced before every order (a running daily total is tracked in the journal).
5. **No external withdrawals.** The tool never calls transfer/withdraw endpoints; scope the API key to a dedicated "Halal" Advanced portfolio funded with only trading capital.
6. **Maker-preferred limits.** Orders placed below market (maker side) — lowest-fee path and no spread.
7. **Fee gate.** If `preview_order` ever returns a commission `> 0`, the tool flags it loudly and refuses to auto-proceed (protects the "covered by Coinbase One" assumption).

## 9. Fee verification (why this tool is trustworthy on cost)

The Coinbase One zero-fee benefit covers the trades this tool places (the user's history shows $0.00 on Advanced USDC-pair buys, well within the $500/mo Basic allowance). Rather than assume that, the tool **measures it every run**:
- `fees` shows the live maker/taker rate + 30-day volume + remaining allowance.
- `dca --preview` shows the **exact commission per order** from `preview_order`.
- A monthly-volume guard warns at ~80% of the $500 cap.

## 10. Error handling

- Missing/invalid config → clear message naming the offending key; never proceed with defaults silently for `allowlist`/`caps`.
- Missing `.env` / API failure on a live command → explain plainly; offline commands still work.
- Malformed CSV rows → skipped with a counted warning, never silently dropped.
- API returns unexpected fee/price → abort the affected order, surface the raw response, place nothing.

## 11. Testing strategy (TDD)

Write tests first for each pure-logic module against fixtures:
- `csv_loader`: dedupe by ID; correct type/fee parsing; malformed-row handling.
- `portfolio`: allocation %, cost basis, realized vs unrealized P&L, goal progress.
- `fees`: pre/post-membership split; per-type totals.
- `riba`: detects reward income + staking; correct purification total; empty case.
- `dca`: respects weights, budget, allowlist, caps; limit price = market × (1 − offset); rejects non-allowlisted assets.
- `journal`: append/read round-trip; daily-cap accumulation.

`cb_client` is exercised against canned response fixtures (no live calls in tests).

## 12. Open items (user to confirm)

- Real `goals.target_usd` and `goals.horizon_months` (placeholders: $5,000 / 24 months).
- The project directory is **not** a git repo. Recommend `git init` so the spec/plan/code are versioned (pending user go-ahead).

---

*This tool is informational and is not financial or religious advice. Confirm compliance questions with a qualified scholar.*
