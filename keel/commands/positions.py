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

    `_mark_for` already refuses a non-positive close, so the only caller cannot pass a zero
    denominator today -- this is the guard for the day a second caller arrives, and for the
    non-finite results `Decimal` can produce from values this module did not write. Stated as
    a guard rather than removed because a read-only page must not 500 over one bad row.
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
        # No mark AND no time for it. Returning the bar's ts beside a `None` close would put a
        # valuation time on the row for a valuation the row says it does not have.
        return None, None
    return newest.close, newest.ts


def _readiness_for(
    repo: Repository,
    product_id: str,
    granularity: Granularity | None,
    config: Config,
    now_ts: int,
) -> tuple[bool, str | None]:
    """`entry_bar_ready`'s verdict for one product on ONE gate granularity, as `(ready, reason)`.

    The ENTRY-GATE question, not a staleness alert: `freshness.assess` tolerates the normal
    forming-bar lag on purpose, and `entry_bar_ready` deliberately does not, because a one-bar-
    late finer series is exactly the condition that produces a duplicate real-money order. A
    positions page showing the softer verdict would tell a reader the feed is fine while the
    agent's own gate is refusing to trade on it.

    `granularity` is the caller's, and it must be the one `_entry_gate_granularity` would pick
    for THIS position's rule -- see `_gate_granularity_for`. Asking about the coarsest series for
    every row (the first version of this) reports that function's FALLBACK as though it were its
    answer: correct for a daily deployment, wrong for any rule seeded on a finer timeframe, and
    worded with the same confidence either way.
    """
    granularities = config.market_data.granularities
    if granularity is None or not granularities:
        return False, "missing"
    candles_by_tf = {g: repo.get_candles(product_id, g) for g in granularities}
    verdict = freshness_mod.entry_bar_ready(candles_by_tf, granularity, now_ts)
    return verdict.ready, verdict.reason


def _gate_granularities(repo: Repository, config: Config) -> dict[str, Granularity | None]:
    """`{rule name: the granularity the agent's entry gate would use}`, built ONCE.

    Keyed on the rule's `name` and NOT on `rules.kind`, because `positions.rule_name` holds the
    name -- a constructor argument that defaults to the kind and is not the same field. Keyed by
    kind, two `turtle_breakout` rows on different timeframes (a configuration this codebase
    supports) collapse to whichever row was read last, and one tranche silently inherits the
    other's granularity.

    Each rule is built once here rather than once per tranche: `_build_rule` runs the rule's real
    constructor with its validation, and a DCA book is one rule with many tranches, so per-row
    building is the common case rather than the edge one.

    A NAME THAT ANSWERS TO TWO DIFFERENT GRANULARITIES maps to `None`. `rules.name` is not unique
    in the schema, so which row opened a given tranche is genuinely unknowable from `rule_name`
    alone -- and a chip that picked one and stated it with the same confidence as a resolved one
    would be asserting something nobody can check. `None` sends the caller to the fallback, which
    is what the agent itself uses for a rule that declares nothing.

    A row whose params no longer build -- a renamed field, a kind since removed -- is skipped for
    the same reason: unknowable, so fall back rather than raise. A chip is not worth a 500.
    """
    granularities = list(config.market_data.granularities)
    gates: dict[str, Granularity | None] = {}
    seen: set[str] = set()
    for row in repo.get_rules():
        try:
            rule = agent_mod._build_rule(row)
        except Exception:
            continue
        name = str(getattr(rule, "name", "") or row.get("kind") or "")
        if not name:
            continue
        gate = agent_mod._entry_gate_granularity(rule, granularities)
        if name in seen and gates.get(name) != gate:
            gates[name] = None
        else:
            gates[name] = gate
        seen.add(name)
    return gates


def _fallback_granularity(config: Config) -> Granularity | None:
    """What the agent gates a rule on when the rule declares nothing: the COARSEST configured
    series. `_entry_gate_granularity`'s own fallback, and for its reason -- `Dca` reads the daily
    bar directly, so gating it on the finest would miss a weeks-stale daily bar entirely."""
    granularities = list(config.market_data.granularities)
    if not granularities:
        return None
    return max(granularities, key=_granularity_rank)


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
    mark_granularity = agent_mod._finest_granularity(list(config.market_data.granularities))
    # ONE read of the rules table and ONE build per rule, before the row loop -- see
    # `_gate_granularities`. A lookup per tranche would be one query and one constructor call per
    # row for an answer the rows share, and would be invisible on any fixture small enough to
    # read.
    gates = _gate_granularities(repo, config)
    fallback = _fallback_granularity(config)

    marks: dict[str, tuple[Decimal | None, int | None]] = {}
    # Keyed on (product, gate granularity), NOT on product alone: one product can hold tranches
    # opened by rules on different timeframes, and a per-product cache would hand the second
    # tranche the first one's verdict.
    readiness: dict[tuple[str, Granularity | None], tuple[bool, str | None]] = {}
    rows: list[PositionRow] = []
    for raw in repo.get_open_positions():
        product_id = str(raw.get("product_id") or "")
        if product_id not in marks:
            marks[product_id] = _mark_for(repo, product_id, mark_granularity)
        # `.get(...) or fallback` collapses the two unresolvable cases onto one answer: a name
        # nothing matches, and a name two rules answer to with different granularities.
        gate = gates.get(str(raw.get("rule_name") or "")) or fallback
        key = (product_id, gate)
        if key not in readiness:
            readiness[key] = _readiness_for(repo, product_id, gate, config, now_ts)
        mark, mark_ts = marks[product_id]
        ready, ready_reason = readiness[key]
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
    # Direct reads, not `raw.get(...) or Decimal("0")`. These three columns are NOT NULL in the
    # `positions` DDL, so the fallback could only ever rewrite a zero as itself -- while quietly
    # substituting one the day a column became nullable. That is the substitution
    # `_position_row_to_dict` deliberately refuses for `initial_stop`, for the same reason.
    qty = raw["qty"]
    entry_fill = raw["entry_fill"]
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
        opened_at=int(raw["opened_at"]),
        qty=qty,
        entry_fill=entry_fill,
        entry_fee=raw["entry_fee"],
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
