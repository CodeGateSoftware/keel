"""Marked-to-market equity, the high-water mark, and the drawdown scalars rail 11 reads.

Rail 11 shipped DORMANT: it reads `drawdown_total_pct`/`drawdown_weekly_pct` from `agent_state`
with a default of 0, and nothing wrote them. It read as enforced in `guards.py` and in the design
docs and could not trip. This module is the missing producer.

Unrealized P&L is INCLUDED deliberately. A drawdown breaker that saw only realized P&L would sit at
0% while a position bled and would notice only after the loss was booked -- backwards for a circuit
breaker, which must fire WHILE you are losing. That is what forces mark-to-market, and therefore
why this lives agent-side (the agent has prices; `guards.check` does not).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from decimal import Decimal

from keel_core.telemetry import log_event
from keel_core.types import CycleBalance, EquityReading

from keel.compliance.purification import build_report
from keel.data.repository import Repository

logger = logging.getLogger(__name__)

WEEK_SECONDS = 7 * 86_400

# Relative equity move between two cycles above which an undeclared external cash flow is
# suspected and WARNED about. Deliberately loose: this only ever logs, so a false positive costs
# a log line, while a missed real flow costs a permanently wrong high-water mark.
UNEXPLAINED_JUMP_PCT = Decimal("0.25")


def mark_positions(
    cash: Decimal,
    positions: list[tuple[Decimal, Decimal]],
    price_by_product: dict[str, Decimal],
    product_ids: list[str],
) -> Decimal:
    """Mark-to-market equity = cash + Σ qty·mark, with a cost-basis fallback.

    `positions[i]` is `(qty, cost_basis)` for `product_ids[i]`. A product with no fresh
    price in `price_by_product` is valued at its `cost_basis` rather than dropped -- dropping
    a held position understates equity and would trip a drawdown breaker on a data gap rather
    than a loss (mirrors agent._mark_to_market_equity's fallback).
    """
    total = cash
    for (qty, cost_basis), product_id in zip(positions, product_ids):
        if qty <= 0:
            continue
        mark = price_by_product.get(product_id)
        if mark is None or mark <= 0:
            mark = cost_basis
        if mark <= 0:
            continue
        total += qty * mark
    return total


def unrealized_on_marks(
    positions: list[tuple[Decimal, Decimal]],
    price_by_product: dict[str, Decimal],
    product_ids: list[str],
) -> Decimal:
    """Unrealized P&L = Σ qty·(mark − cost_basis), on the SAME marks `mark_positions` used (#698).

    Deliberately a sibling of `mark_positions` with an identical shape and identical guards: the
    two are written into one `equity_points` row for one cycle, so a position the equity valued
    at cost must contribute ZERO here. A helper that skipped the unpriced position instead, or
    marked it differently, would file a `cash`/`unrealized`/`equity` triple that does not add up
    -- and the row is only worth keeping if it reconciles (see
    `test_cash_plus_cost_basis_plus_unrealized_reconstructs_the_equity`).

    Signed: negative while a position is under water, which is the direction that matters, since
    the whole reason equity is marked to market is to see a loss WHILE it is happening.
    """
    total = Decimal("0")
    for (qty, cost_basis), product_id in zip(positions, product_ids):
        if qty <= 0:
            continue
        mark = price_by_product.get(product_id)
        if mark is None or mark <= 0:
            # Valued at cost by `mark_positions`, so there is no observed gain or loss to book.
            # (When the basis is non-positive too, `mark_positions` drops the position outright
            # and this `continue` has already matched it -- the two stay in step either way.)
            continue
        # No guard on a non-positive `cost_basis`: `mark_positions` values a fresh-priced
        # position at `qty * mark` whatever it cost, so a zero-basis holding is ALL unrealized
        # gain. Skipping it here would leave the row's parts short of its own equity.
        total += qty * (mark - cost_basis)
    return total


def pending_purification_usd(repo: Repository) -> Decimal:
    """Accrued-but-unpurified non-compliant income, in USD, from the repo's own ledger (#490).

    `build_report(repo.get_transactions()).total_owed_usd` -- the same figure
    `keel purification` renders as "TOTAL OWED TO CHARITY". Only entries classified
    NON_COMPLIANT count: CLEAN trading activity is not owed, and `REVIEW` (unclassified) is
    deliberately excluded -- over-purifying would misstate a religious obligation as fact
    (`compliance.purification.classify`'s own posture). That exclusion cuts the other way for
    the sizing base below: a reward-type string `classify` does not recognize stays in sizing
    equity -- the safe direction for the fiqh report, the wrong direction for sizing -- and is
    bounded only by observation (every Coinbase reward string seen to date matches a
    `NON_COMPLIANT_MARKERS` entry).

    The report is CUMULATIVE over the imported ledger and carries no discharge record, so
    "pending" here means "everything the ledger shows as owed". For the sizing use below that
    is the conservative direction: the equity base can only come out smaller, never larger.

    Zero on a clean or empty ledger -- never a default haircut.
    """
    return build_report(repo.get_transactions()).total_owed_usd


def sizing_equity(mark_to_market: Decimal, pending_purification: Decimal) -> Decimal:
    """The equity base position sizing reads: mark-to-market minus pending purification (#490).

    Discussion #472's invariant, stated as code: "interest left sitting in the balance would
    inflate the equity the sizing formula reads from" -- riba compounding into position size,
    a correctness bug independent of the fiqh point (KB §65.9: non-compliant income is given
    away, never recognised as trading capital). Every path that derives sizing equity from a
    LIVE balance read must go through this helper; config-constant sizing inputs
    (`caps.max_exposure_usd`, `dca.budget_usd`, a funded `paper.starting_equity_usd`) are
    immune by construction and pass through unchanged.

    Floored at zero: pending purification can exceed the mark-to-market read (a reward-heavy
    ledger against a mostly-withdrawn account), and a negative equity base would size a
    negative position -- zero risks zero, the correct no-trade answer.
    """
    return max(mark_to_market - pending_purification, Decimal("0"))


def record_external_flow(repo: Repository, *, amount: Decimal) -> None:
    """Rebase the high-water mark and the rolling weekly peak by an external cash flow.

    `amount` is signed: positive for a deposit, negative for a withdrawal.

    Deposits and withdrawals are not P&L, but equity is `cash + positions`, so both move it.
    Left unaccounted, a deposit ratchets the MONOTONIC high-water mark up and a later withdrawal
    then reads as a drawdown that can never recover -- rail 11 vetoes every entry on an account
    that never lost anything. Shifting the HWM by the same amount keeps the drawdown measuring
    trading performance, which is the only thing a drawdown breaker should react to.

    The rolling `equity_history` is shifted too: `drawdown_weekly_pct` is measured against a
    7-day peak held there, so leaving it unshifted would reintroduce the identical phantom
    drawdown on the weekly rail for a week.

    A no-op before the first cycle: with no HWM yet there is nothing to rebase, and the first
    `update_drawdown` seeds it from observed equity, which already includes the flow.

    This is DECLARED by the operator (`keel record-flow`), never inferred. `update_drawdown`
    warns about a suspicious jump but must never adjust on its own: raising the HWM is the
    conservative direction, but LOWERING it on a misread balance movement would silently mask a
    real trading drawdown and disarm the breaker.
    """
    hwm = repo.get_state("equity_high_water_mark")
    if hwm is not None:
        repo.set_state("equity_high_water_mark", hwm + amount)

    history = repo.get_state("equity_history", default=[]) or []
    if history:
        repo.set_state(
            "equity_history",
            [{"ts": point["ts"], "equity": Decimal(str(point["equity"])) + amount}
             for point in history],
        )

    log_event(
        logger,
        logging.INFO,
        "equity.external_flow_recorded",
        amount=str(amount),
        rebased_hwm=None if hwm is None else str(hwm + amount),
    )


def update_drawdown(
    repo: Repository,
    *,
    equity: Decimal,
    now_ts: int,
    cash: Decimal | None = None,
    unrealized: Decimal | None = None,
    balances: Sequence[tuple[str, Decimal | None, Decimal | None]] = (),
) -> None:
    """Record `equity` and refresh the drawdown scalars rail 11 consumes.

    Also appends one row to the durable `equity_points` series (#698). The two records are NOT
    redundant: `equity_history` below is a 7-day window that `record_external_flow` REWRITES on
    a declared deposit, because the weekly rail must keep measuring trading performance. That
    makes it a working set, not a record -- so the series is written alongside it rather than
    derived from it, and neither is reconstructable from the other.

    `cash` and `unrealized` are the optional split of `equity`. They are passed, never derived
    here: this function has a total and no positions, and inventing the split from the total is
    exactly the fabrication `None` exists to avoid. Callers that know it (the agent's paper and
    live branches both do) pass it; callers that do not leave it unrecorded.

    `balances` is the per-CURRENCY detail behind `cash` (#719): `(currency, available, total)`
    tuples, one per currency the caller observed this cycle. Written to `cycle_balances` in the
    SAME call as the `equity_points` row below, under the SAME mode read -- see
    `_append_equity_point` for why that matters. The paper branch never has a venue balance to
    observe and so never passes this; the default `()` writes nothing, which is the correct
    answer for paper, not a special case of it.
    """
    _warn_on_unexplained_jump(repo, equity=equity)

    hwm = repo.get_state("equity_high_water_mark")
    if hwm is None or equity > hwm:
        hwm = equity
        repo.set_state("equity_high_water_mark", hwm)

    repo.set_state(
        "drawdown_total_pct",
        Decimal("0") if hwm <= 0 else max((hwm - equity) / hwm, Decimal("0")),
    )

    history = [
        point
        for point in (repo.get_state("equity_history", default=[]) or [])
        if int(point["ts"]) >= now_ts - WEEK_SECONDS
    ]
    history.append({"ts": now_ts, "equity": equity})
    repo.set_state("equity_history", history)

    weekly_peak = max(Decimal(str(p["equity"])) for p in history)
    repo.set_state(
        "drawdown_weekly_pct",
        Decimal("0")
        if weekly_peak <= 0
        else max((weekly_peak - equity) / weekly_peak, Decimal("0")),
    )

    _append_equity_point(
        repo,
        equity=equity,
        now_ts=now_ts,
        hwm=hwm,
        cash=cash,
        unrealized=unrealized,
        balances=balances,
    )


def _append_equity_point(
    repo: Repository,
    *,
    equity: Decimal,
    now_ts: int,
    hwm: Decimal,
    cash: Decimal | None,
    unrealized: Decimal | None,
    balances: Sequence[tuple[str, Decimal | None, Decimal | None]] = (),
) -> None:
    """Append this cycle's reading to the durable series, stamped with the mode that produced it.

    The mode comes from `equity_state_mode` -- the SAME stamp `agent._clear_live_mode_if_needed`
    and `agent._seed_paper_account_if_needed` read before wiping the shared HWM on a flip.
    Deriving it a second way here (from a passed flag, say) would let two answers to one question
    drift apart, and the failure would be silent: a row filed under the wrong mode does not go
    missing, it lands in the other account's curve.

    An UNSTAMPED mode writes nothing, and does not raise. Every agent path stamps it
    unconditionally before this runs, so the unstamped case is not a real cycle (a direct call in
    a test, a caller yet to be written). Two things it must not do: guess a mode -- that is the
    mislabelling above, manufactured -- or fail the call, which would take rail 11's scalars down
    with the chart. The drawdown scalars are already written by the time this is reached, so a
    skipped point costs a gap in a chart and nothing else.

    LAST in `update_drawdown` for that reason: the rail is served before the record is kept.

    `balances` (#719) is written AFTER the `equity_points` row, off the SAME `mode` local this
    function already read for it -- one `get_state` call, two tables, so a cycle cannot record
    its equity under one mode and its balances under another. Same unstamped-mode rescue as the
    equity point: an unstamped call writes neither.
    """
    mode = repo.get_state("equity_state_mode")
    if mode is None:
        return
    repo.record_equity_point(
        EquityReading(
            ts=now_ts,
            mode=str(mode),
            equity=equity,
            cash=cash,
            unrealized=unrealized,
            hwm=hwm,
        )
    )
    for currency, available, total in balances:
        repo.record_cycle_balance(
            CycleBalance(
                ts=now_ts,
                mode=str(mode),
                currency=currency,
                available=available,
                total=total,
            )
        )


def _warn_on_unexplained_jump(repo: Repository, *, equity: Decimal) -> None:
    """Log a WARNING when equity moves more than `UNEXPLAINED_JUMP_PCT` between cycles.

    Detection only -- it NEVER adjusts anything. The two directions are not symmetric: inferring
    a deposit and raising the HWM is conservative, but inferring a WITHDRAWAL and lowering it
    would mask a real trading drawdown and silently disarm rail 11. A wrong guess in that
    direction is exactly the failure this rail exists to prevent, so flows are declared by the
    operator (`keel record-flow`) and this is only here to make a forgotten one loud instead of
    silent.
    """
    history = repo.get_state("equity_history", default=[]) or []
    if not history:
        return
    previous = Decimal(str(history[-1]["equity"]))
    if previous <= 0:
        return
    move = abs(equity - previous) / previous
    if move < UNEXPLAINED_JUMP_PCT:
        return
    log_event(
        logger,
        logging.WARNING,
        "equity.unexplained_jump",
        previous=str(previous),
        current=str(equity),
        move_pct=str(move),
        hint=(
            "if this was a deposit or withdrawal, run `keel record-flow --amount <signed>` -- "
            "an unrecorded flow permanently skews the high-water mark rail 11 reads"
        ),
    )
