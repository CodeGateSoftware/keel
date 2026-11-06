# Rail 17 reads Coinbase, and a skipped DCA buy is retried until the next window

Status: design, 2026-11-06. Supersedes the open options in #1010.

## Why

Rail 17 (withdrawal capability, KB §65.4 *qabd*) blocks every live BUY unless the operator typed
`keel withdrawals attest --enabled` at a terminal within the last 7 days. Since August it has
lapsed at least five times (2026-08-14, 08-25, 10-09, 10-17, 11-02). Each time the operator found
out days later, and each lapse cost DCA windows outright, because `Dca.detect` fires only on
`(bar_ts // 86400) % cadence_days == 0` and a vetoed window is never retried. As of 2026-11-06 the
five 14-day rules (PAXG, ADA, XLM, DOGE, FET; live rules 9-13) have lost all three of their
windows (10-09, 10-23, 11-06) and have never placed a live order.

The operator's words: the problem is "the silent failure and losing the buy". The rule they want
is: buy when the Coinbase balance covers the order, never when it is short or empty. Rail 13
already enforces exactly that and stays unchanged. The §65.4 possession test stays too, but its
input changes from a human assertion to the venue's own answer.

## What the probe established (2026-11-06, live key, read-only)

- `GET /v2/accounts` (Coinbase App API) returns `allow_withdrawals` and `allow_deposits` on every
  wallet. With the existing CDP key it returned `true` for BTC, ETH, PAXG, XLM, USDC, USD.
- `GET /api/v3/brokerage/key_permissions` -> `can_view: true, can_trade: true,
  can_transfer: false`. The read needs no new permission, and keel still cannot move funds.
- ADA, DOGE and FET have no wallet in either `/v2/accounts` or Advanced Trade `get_accounts`
  (never held). There is no flag to read for them until a first buy creates the wallet.
- The public `GET https://api.exchange.coinbase.com/currencies` reports `status: online` per
  currency and per network. Recorded here; not used by this design (see Non-goals).
- Not established: that `allow_withdrawals` actually turns `false` during a real account
  restriction. It has only been observed `true`. The design treats it as the venue's statement
  and keeps the manual `--suspended` override for the case where it does not flip.

The probe script is not committed; its calls are the three above.

## Goals

1. Rail 17's input on a live BUY is read from Coinbase on every intent, not from a dated
   attestation. No TTL, no weekly ceremony, no expiry warnings.
2. The decision:
   - the venue reports `allow_withdrawals: true` for the **quote wallet the order spends**
     (USDC by default, whatever rail 13 checks) **and** for the **base wallet being bought**
     when that wallet exists -> rail 17 passes;
   - either reports `false` -> veto, reason names the wallet;
   - the base wallet does not exist yet -> decided on the quote wallet alone (operator-accepted
     guess: a coin never held has nothing to be frozen, and the order's funds are the USDC);
   - the read fails, times out, or the quote wallet is missing from the response -> veto
     (fails closed, operator's decision 2026-11-06: "skip the buy").
3. A manual `keel withdrawals attest --suspended` still halts entries, with no expiry, until
   cleared. Clearing it keeps the typed-`yes` TTY gate (it releases a halt). A stored
   suspension beats a venue `true`.
4. Every skipped live DCA buy (rail-17 veto or any other reason) raises a **macOS
   notification posted by keel itself** (operator's decision 2026-11-06), the same cycle: which
   product, and why (venue `false` naming the wallet, venue unreadable, manual suspension, or
   the other rail's name). Once per rule per day, repeating every day the buy stays skipped; a
   later fill ends it. This is the fix for "found out very late".
   - The existing `notify_after_cycle` webhook path sends nothing on the live deployment (no
     `notifications:` block, no `KEEL_ALERT_WEBHOOK`), and the wrapper's `osascript` alerts fire
     only on non-zero exits. A vetoed buy is a zero-exit cycle, so neither reaches the operator.
   - Do not signal through a new wrapper exit code: non-zero exits drive the wrapper's same-day
     retries and failure counter, and `keel-live-run.sh` is not in the repo.
   - Do not reuse #793's `unreported()` once-per-window dedupe; it would silence the daily
     repeats.
   - The sink must be a no-op off macOS and in tests, and a failure to post must never fail the
     cycle.
5. **DCA catch-up.** A DCA buy that was due and did not fill, for any reason (any rail veto,
   a failed cycle, a venue error, the machine being off), is attempted again on every later
   daily cycle until it fills or the rule's next window opens. At most one fill per rule per
   window, ever.
6. Rails 13, 14 and every other rail are unchanged. SELLs are still never touched by rail 17.

## Non-goals

- Using the public `/currencies` network status as a rail input. A network outage is not an
  account freeze; it could be added later as its own signal.
- Changing DCA cadence, budgets, or the epoch-day anchoring of windows.
- Catch-up for non-DCA rules (Turtle etc.). Their signals are price-dependent and a late entry is
  a different trade.
- Paper and backtest behaviour of rail 17: it stays "skipped, recorded as skipped" as today.

## Anti-goals (would be a failure even if it works)

- A retry that double-buys a window. The ledger, not the candle, decides "already bought".
- A catch-up buy that fires on the day after a window was already filled (e.g. a cycle that
  re-runs after a crash).
- A venue read failure that is indistinguishable, in the notification, from a real freeze.
- Any path where a cron line or unattended process can clear a manual suspension.
- Silence: a skipped buy with no notification.

## The DCA window and catch-up

Define for a DCA rule with `cadence_days = N` and a completed daily bar at epoch day `d`:

- the rule's current window starts at `w = d - (d % N)` and ends before `w + N`;
- the rule is **due** on bar `d` if no BUY order for this rule (`orders.rule_id`) with status
  `filled` has `created_at` within `[w * 86400 + the cycle offset, ...)` — exact boundary is the
  builder's call, but it must be keyed to the window, not to "today";
- today's behaviour (`d % N == 0`) is the special case where the window opens.

Constraints the builder must respect:

- `Dca.detect` is pure and is called by the backtester, the account sim, paper, and
  `_dca_fires_today` (`keel/agent.py`). Keep the window arithmetic pure and put the ledger
  lookup in the live/paper engine path, so backtests keep their current semantics unless the
  builder shows they should change. The sizing (`size_usd`, dip bonus) on a catch-up day is
  computed from that day's bar, not the window-opening bar.
- `_dca_fires_today` drives spec §3.4's same-day-DCA exclusion for sleeve sells. A catch-up day
  is a buy day for that exclusion.
- A partially filled or `open` DCA order counts as "attempted, not filled": the builder decides
  whether that blocks a retry. Recommended: an order in a non-terminal state blocks retry until
  it resolves, so two buys are never in flight.
- Rail 14 (monthly buy cap) and rail 2/3 (per-order, per-day) may legitimately veto a catch-up;
  it just retries next day within the window. *Designer's call:* a catch-up that spills a buy
  into the next calendar month is accepted, and rail 14 remains the bound. `keel dca-plan`'s
  worst-month figure (`_worst_month_buy_days`) is left alone, but its output must say that
  catch-up can add up to one buy per rule to a month. For scale: the live cap is $500/month and
  the seven live rules already plan up to ~$580 in a month with five Thursdays and three
  biweekly windows, so rail 14 can bind today, independent of this change.
- *Designer's call:* a DCA order still `pending`/`open` blocks a retry for that rule, so two buys
  are never in flight. If it is still unresolved on the next daily cycle, that is a rail-17-class
  "buy skipped" notification for that day (never silent). The builder must verify that
  reconcile settles a `pending` row with no venue order id (the `place_order` raised path in
  `executor._run_order`); if it does not, looking it up at the venue by `client_order_id` is in
  scope.

## Surfaces that change

- `keel/execution/guards.py` rail 17 block and its message (drop "attest with" advice for the
  venue case; drop "DCA-exempt paths are unaffected", #1010 notes it is misleading).
- `keel/execution/executor.py`: replace `_withdrawals_enabled` / `WITHDRAWAL_ATTESTATION_TTL_SEC`
  with a venue read through the broker adapter. The adapter port (`keel_broker_api`) gets a
  method for withdrawal capability per currency; Coinbase implements it via `/v2/accounts`;
  Alpaca and any other venue must answer explicitly (the builder decides; it must not silently
  pass).
- `keel/commands/withdrawals.py`: `attest --enabled` becomes "clear manual suspension";
  `--suspended` sets it with no TTL; `show` prints the venue's live answer per wallet plus any
  manual suspension.
- Expiry machinery that only exists for this TTL: `keel/attestations.py` (rail 17 entries),
  `keel/commands/doctor.py` rail-17 check, `keel/notifications.py` expiry banner for rail 17,
  `keel/commands/setup.py` wizard step, `keel status` rail-17 line, the web UI's attestation
  display if it reads the same keys.
- Docs: `docs/rails/rail-17-withdrawal-attestation.md` (rename/rewrite), `docs/operator-runbook.md`
  item 3 (weekly attest), `docs/go-live-runbook.md`, `docs/glossary.md`, `docs/fiqh-basis.md`
  only where it describes the mechanism.
- Existing stored state `withdrawals_enabled` / `withdrawals_attested_at`: a stored
  `withdrawals_enabled = false` migrates to a manual suspension; a stored `true` is ignored.

## Decided by the operator

- Remove the dated attestation as a buy blocker.
- Keel reads withdrawability from Coinbase itself.
- Venue unreadable -> skip the buy and notify.
- A skipped DCA buy is retried daily until the next window.
- Keep rail 13's balance check as is.
- Alerts are macOS notifications posted by keel.

## Left to the builder

- Where the window/ledger logic lives, and the exact window boundary arithmetic.
- The adapter port method's name and shape, and what non-Coinbase venues answer.
- How the notification is worded and deduplicated across retry days (one per day per rule is
  fine; it must not go silent after the first).
- Whether `withdrawals show` is renamed.

Closes #1010 when shipped.
