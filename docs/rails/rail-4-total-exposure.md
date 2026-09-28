# Rail 4 — the total open-exposure cap

Rail 4 is one of keel's hard rails: checks in `keel/execution/guards.py` that run before every
order, in every mode, and cannot be switched off or widened from outside that module. This one
caps the **summed open notional** across every asset — everything bought and not yet sold, from
the orders audit log — plus the order being placed, at `caps.max_exposure_usd` in config.yaml.

It is a **notional** cap, not an at-risk cap: $5k behind a 2% stop and $5k behind a 20% stop
count the same (KB §83.3).

## Who it binds: rule-trading BUYs, not DCA

Since #841 (the operator's decision, 2026-09-27), **DCA BUYs are exempt from this rail**:

> DCA is bounded by the venue plan's cap (rail 14, the attested monthly buy cap), not by
> `caps.max_exposure_usd`.

A fixed total-holdings cap stops a multi-rule accumulation sleeve within weeks whatever the plan
is: with 7 DCA rules, about $213 held against a $400 cap left room for about one more buy.

What the exemption does and does not change:

| Rail | Rule-trading BUY | DCA BUY |
|---|---|---|
| 4 — total open exposure (`total_exposure_cap`) | binds | **exempt** (#841) |
| 6 — per-asset concentration (`per_asset_concentration_cap`) | binds | binds |
| 14 — monthly buy cap (`monthly_subscription_allowance`) | binds | binds — the limit that bounds DCA |
| 8 — no averaging into losers | binds | exempt (§8/§12.1) |
| 11 — account-drawdown breaker | binds | exempt (§12.6) |
| 16 — consecutive-loss breaker | binds | exempt (§12.6) |
| every other rail | binds | binds |

Two consequences worth knowing:

- **DCA holdings still count toward the total.** A rule-trading entry sees the whole book,
  DCA lots included, so a growing DCA sleeve shrinks the room rule trades have under
  `max_exposure_usd`.
- **Rail 6 still uses `max_exposure_usd`.** The per-asset limit is
  `max_per_asset_pct × max_exposure_usd`, so `max_exposure_usd` still bounds DCA per asset,
  just not in total.

`keel simulate` applies the same exemption (`SimAccount.can_open`), so a backtest models what
live does.

## What happens when it is exceeded

A rule-trading BUY is **vetoed** before it reaches the venue:

- `total_exposure_cap: open exposure … + … = … exceeds max_exposure_usd …`

SELLs are never checked by this rail — they reduce exposure.
