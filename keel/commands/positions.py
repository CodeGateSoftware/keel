"""`keel`'s positions report -- what is held, what it is worth, and how close it is to its stop.

**Strictly read-only.** No broker call, no write, no rail touched: a pure view over
`repo.get_open_positions()`, the candle cache, and `keel.data.freshness`. Same two layers as
`status.py`/`orders.py` -- a pure `gather_positions(repo, config, now_ts) -> PositionsReport`
that any renderer can call, and no click dependency in the builder.

WHY THIS MODULE EXISTS RATHER THAN MORE FIELDS ON `OpenPositionStatus` (#701). `web/payload.py`'s
`_position_payload` carries no P&L and says why: "`OpenPositionStatus` carries neither, so
emitting one would mean this layer multiplied `qty` by `entry_price`... the fix, if it is wanted,
is upstream." This is that fix. It is a separate report rather than a wider status row because
the arithmetic here needs the candle cache and the config's granularities, which `gather_status`
does not read and should not start reading for the sake of one table inside it.

THE MARK IS THE RAILS' MARK, AND THAT IS THE POINT. `agent._mark_to_market_parts` values a
holding at `repo.get_candles(product, finest)[-1].close`, and so does this, through the same
`_finest_granularity`. A positions page quoting a different current price would be a second
answer to "what is this worth" -- and since the first answer is the one that moved rail 11's
drawdown scalars, the page would be the wrong one. Everything derived below (market value,
unrealized P&L, stop distance) is derived from THAT number or from nothing at all.

WHAT IS ABSENT STAYS ABSENT. A product with no cached candle has no mark, and a row with no mark
reports `None` for every figure that depends on one -- never zero. Zero market value renders a
held position as a total loss, which is the most alarming thing this page could say, and it
would be saying it about missing data. `initial_stop` is `None` for a DCA leg or a pre-v12
tranche, so its distance is `None` too: a distance measured against a substituted zero would
read as a position comfortably clear of a stop it does not have.

NO CLOSE ACTION, EVER (#701's own refusal). This module places nothing and cancels nothing.
Exits degrade to the typed-phrase friction path; a panic tap on a table row must never be the
last line of defence.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Any

from keel_core.types import Granularity

from keel import agent as agent_mod
from keel.config import Config
from keel.data import freshness as freshness_mod
from keel.data.repository import Repository


@dataclass(frozen=True)
class PositionRow:
    """One open TRANCHE, projected onto what a reader needs.

    `qty` is what is STILL HELD -- `reduce_position` shrinks it on a scale-out and carries the
    sold legs in the `realized_*` accumulators, so the two sides together are the tranche's whole
    story and either alone understates it.
    """

    id: int
    product_id: str
    rule_name: str
    opened_at: int

    #: The quantity still held, after any partial exits.
    qty: Decimal

    #: What this tranche paid, and what it paid to get in. The fee is here because `keel status`
    #: never showed it and it is the half of a round trip a reader most often forgets.
    entry_fill: Decimal
    entry_fee: Decimal

    #: The latest close of the FINEST configured series -- the agent's own mark. `None` when the
    #: product has no cached candle at all: not observed, never "worth nothing".
    mark: Decimal | None

    #: The instant that mark was recorded, so a reader can see how old the valuation is without
    #: this module choosing a staleness threshold (`freshness` owns that judgement; see `ready`).
    mark_ts: int | None

    #: `qty * mark`, and `qty * (mark - entry_fill)`. Both `None` without a mark.
    market_value: Decimal | None
    unrealized_pnl: Decimal | None

    #: The stop this tranche was SIZED against (`positions.initial_stop`). `None` means "not on
    #: this row" -- a DCA leg, or a tranche predating v12 -- and NOT "no stop".
    initial_stop: Decimal | None

    #: `mark - initial_stop`, SIGNED: a tranche trading THROUGH its stop is the state an operator
    #: most needs to see, and an absolute distance would render it identically to one safely
    #: above. `stop_distance_pct` is that distance as a fraction OF THE MARK. Both `None` when
    #: either the stop or the mark is missing.
    stop_distance: Decimal | None
    stop_distance_pct: Decimal | None

    #: The legs already sold (#502). Zero rather than `None` when nothing has been: the
    #: repository's own convention, because "never partially exited" and "has realized nothing"
    #: are the same fact.
    realized_qty: Decimal
    realized_proceeds: Decimal
    realized_fees: Decimal

    #: `entry_bar_ready`'s verdict for this product's entry-gate series -- the gate outcome the
    #: agent itself would compute, not a data age. `ready_reason` is `"missing" | "behind" |
    #: "unconfirmed"`, or `None` when ready.
    ready: bool
    ready_reason: str | None


@dataclass(frozen=True)
class PositionsReport:
    now_ts: int
    rows: tuple[PositionRow, ...]

    @property
    def open_count(self) -> int:
        """How many tranches this report holds.

        Derived rather than stored, for the reason `OrdersReport.shown_count` is: a stored count
        can drift from the list it describes, and `keel/web/payload.py` may not call `len()`
        (Rule 6e of `tests/commands/test_console_thinness.py`).
        """
        return len(self.rows)

    @property
    def products(self) -> tuple[str, ...]:
        """Every product with an open tranche, in first-seen order -- what a grouped view keys
        on. Held here so no renderer builds its own list and reaches a different one."""
        seen: list[str] = []
        for row in self.rows:
            if row.product_id not in seen:
                seen.append(row.product_id)
        return tuple(seen)


def _safe_ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """`numerator / denominator`, or `None` when that is not a finite answer.

    A non-positive or non-finite mark reaches here from the candle cache, which is data this
    module did not write. `Decimal` raises on a zero denominator rather than returning an
    infinity, and an unguarded division would take a read-only page down over one bad row.
    """
    if denominator == 0:
        return None
    try:
        ratio = numerator / denominator
    except (DivisionByZero, InvalidOperation):
        return None
    return ratio if ratio.is_finite() else None


def _mark_for(
    repo: Repository, product_id: str, granularity: Granularity | None
) -> tuple[Decimal | None, int | None]:
    """The agent's mark for `product_id`: the newest close of the finest configured series.

    Deliberately the same read `agent._mark_to_market_parts` makes. `None` when no series is
    configured or nothing is cached -- and `None` also when the cached close is non-positive,
    because a zero or negative price is not a valuation this page should publish as one.
    """
    if granularity is None:
        return None, None
    candles = repo.get_candles(product_id, granularity)
    if not candles:
        return None, None
    newest = candles[-1]
    if newest.close <= 0:
        return None, newest.ts
    return newest.close, newest.ts


def _readiness_for(
    repo: Repository, product_id: str, config: Config, now_ts: int
) -> tuple[bool, str | None]:
    """`entry_bar_ready`'s verdict for this product, as `(ready, reason)`.

    The ENTRY-GATE question, not a staleness alert: `freshness.assess` tolerates the normal
    forming-bar lag on purpose, and `entry_bar_ready` deliberately does not, because a one-bar-
    late finer series is exactly the condition that produces a duplicate real-money order. A
    positions page showing the softer verdict would tell a reader the feed is fine while the
    agent's own gate is refusing to trade on it.
    """
    granularities = config.market_data.granularities
    coarsest = max(granularities, key=_granularity_rank) if granularities else None
    if coarsest is None:
        return False, "missing"
    candles_by_tf = {g: repo.get_candles(product_id, g) for g in granularities}
    verdict = freshness_mod.entry_bar_ready(candles_by_tf, coarsest, now_ts)
    return verdict.ready, verdict.reason


def _granularity_rank(granularity: Granularity) -> int:
    """Ordering over granularities, read from the agent's own table so the two cannot disagree
    about which series is finer."""
    return agent_mod._GRANULARITY_ORDER.get(granularity, 0)


def gather_positions(repo: Repository, config: Config, *, now_ts: int) -> PositionsReport:
    """Every OPEN tranche, marked and judged.

    Open only: a closed tranche is a `trade_outcomes` row and belongs to the journal, which
    already reports it with its realised P&L. Showing both here would put one trade in two places
    with two different figures for it.

    The candle cache is read ONCE PER PRODUCT rather than once per tranche -- a book can hold
    several tranches of the same product (that is what tranches are for), and a per-row read
    would be one query per tranche for one answer they all share.
    """
    granularity = agent_mod._finest_granularity(list(config.market_data.granularities))

    marks: dict[str, tuple[Decimal | None, int | None]] = {}
    readiness: dict[str, tuple[bool, str | None]] = {}
    rows: list[PositionRow] = []
    for raw in repo.get_open_positions():
        product_id = str(raw.get("product_id") or "")
        if product_id not in marks:
            marks[product_id] = _mark_for(repo, product_id, granularity)
            readiness[product_id] = _readiness_for(repo, product_id, config, now_ts)
        mark, mark_ts = marks[product_id]
        ready, ready_reason = readiness[product_id]
        rows.append(_row_from_dict(raw, mark, mark_ts, ready, ready_reason))
    return PositionsReport(now_ts=now_ts, rows=tuple(rows))


def _row_from_dict(
    raw: dict[str, Any],
    mark: Decimal | None,
    mark_ts: int | None,
    ready: bool,
    ready_reason: str | None,
) -> PositionRow:
    """One repository dict, projected. Every judgement this report makes is made here, once, so
    no renderer has to make it twice."""
    qty = raw.get("qty") or Decimal("0")
    entry_fill = raw.get("entry_fill") or Decimal("0")
    initial_stop = raw.get("initial_stop")

    market_value = None if mark is None else qty * mark
    unrealized = None if mark is None else qty * (mark - entry_fill)

    if mark is None or initial_stop is None:
        stop_distance: Decimal | None = None
        stop_distance_pct: Decimal | None = None
    else:
        stop_distance = mark - initial_stop
        stop_distance_pct = _safe_ratio(stop_distance, mark)

    return PositionRow(
        id=int(raw["id"]),
        product_id=str(raw.get("product_id") or ""),
        rule_name=str(raw.get("rule_name") or ""),
        opened_at=int(raw.get("opened_at") or 0),
        qty=qty,
        entry_fill=entry_fill,
        entry_fee=raw.get("entry_fee") or Decimal("0"),
        mark=mark,
        mark_ts=mark_ts,
        market_value=market_value,
        unrealized_pnl=unrealized,
        initial_stop=initial_stop,
        stop_distance=stop_distance,
        stop_distance_pct=stop_distance_pct,
        realized_qty=raw.get("realized_qty") or Decimal("0"),
        realized_proceeds=raw.get("realized_proceeds") or Decimal("0"),
        realized_fees=raw.get("realized_fees") or Decimal("0"),
        ready=ready,
        ready_reason=ready_reason,
    )
