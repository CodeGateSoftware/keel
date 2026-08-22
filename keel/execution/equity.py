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
from decimal import Decimal

from keel_core.telemetry import log_event

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


def pending_purification_usd(repo: Repository) -> Decimal:
    """Accrued-but-unpurified non-compliant income, in USD, from the repo's own ledger (#490).

    `build_report(repo.get_transactions()).total_owed_usd` -- the same figure
    `keel purification` renders as "TOTAL OWED TO CHARITY". Only entries classified
    NON_COMPLIANT count: CLEAN trading activity is not owed, and `REVIEW` (unclassified) is
    deliberately excluded -- over-purifying would misstate a religious obligation as fact
    (`compliance.purification.classify`'s own posture).

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


def update_drawdown(repo: Repository, *, equity: Decimal, now_ts: int) -> None:
    """Record `equity` and refresh the drawdown scalars rail 11 consumes."""
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
