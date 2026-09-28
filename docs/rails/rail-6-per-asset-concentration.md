# Rail 6 — the per-asset concentration cap

Rail 6 is one of keel's hard rails: checks in `keel/execution/guards.py` that run before every
order, in every mode, and cannot be switched off or widened from outside that module. This one
caps the **summed open notional in one asset** — everything bought and not yet sold, from the
orders audit log — plus the order being placed, at `max_per_asset_pct × caps.max_exposure_usd`
in config.yaml. keel has no equity oracle, so the configured total-exposure ceiling stands in for
funded trading capital, and `max_per_asset_pct` is a fraction of it (§10.3).

Like rail 4, it is a **notional** cap, not an at-risk cap.

## Who it binds: rule-trading BUYs, not DCA

Since #853 (the operator's decision, 2026-09-28), **DCA BUYs are exempt from this rail too**,
mirroring rail 4's #841 exemption:

> DCA is bounded by the venue plan's cap (rail 14, the attested monthly buy cap) and available
> cash, not by `max_per_asset_pct` (a fraction of `max_exposure_usd`).

A fixed per-asset ceiling stops a single-asset accumulation sleeve within weeks whatever the plan
is, for the same reason a fixed total-holdings ceiling does (#841): live BTC DCA was on track to
be refused by this rail from around end October 2026.

What the exemption does and does not change:

| Rail | Rule-trading BUY | DCA BUY |
|---|---|---|
| 4 — total open exposure (`total_exposure_cap`) | binds | **exempt** (#841, [rail-4-total-exposure.md](rail-4-total-exposure.md)) |
| 6 — per-asset concentration (`per_asset_concentration_cap`) | binds | **exempt** (#853) |
| 14 — monthly buy cap (`monthly_subscription_allowance`) | binds | binds — the limit that bounds DCA |
| 13 — USDC-funding (cash floor) | binds | binds |
| 2/3 — per-order / per-day caps | binds | binds |
| 5 — correlation-adjusted sizing | binds | binds |
| 8 — no averaging into losers | binds | exempt (§8/§12.1) |
| 11 — account-drawdown breaker | binds | exempt (§12.6) |
| 16 — consecutive-loss breaker | binds | exempt (§12.6) |
| every other rail | binds | binds |

Two consequences worth knowing:

- **DCA holdings still count toward the per-asset total.** A rule-trading entry in the same asset
  sees the whole book, DCA lots included, so a growing DCA position in BTC shrinks the room a
  rule-trading BTC entry has left under `max_per_asset_pct × max_exposure_usd`. The exemption is
  from being *gated* by rail 6 as a DCA candidate, not a blind spot in what the rail measures.
- **Rail 4 is exempt too, since #841.** DCA is bounded by rail 14 and available cash on both the
  total and the per-asset axis now — see
  [rail-4-total-exposure.md](rail-4-total-exposure.md).

`keel simulate` applies the same exemption (`SimAccount.can_open`), so a backtest models what
live does.

## What happens when it is exceeded

A rule-trading BUY is **vetoed** before it reaches the venue:

- `per_asset_concentration_cap: <asset> exposure … exceeds … of max_exposure_usd (…)`

SELLs are never checked by this rail — they reduce exposure.
