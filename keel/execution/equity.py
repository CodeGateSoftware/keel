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

from decimal import Decimal

from keel.data.repository import Repository

WEEK_SECONDS = 7 * 86_400


def update_drawdown(repo: Repository, *, equity: Decimal, now_ts: int) -> None:
    """Record `equity` and refresh the drawdown scalars rail 11 consumes."""
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
