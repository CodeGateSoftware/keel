"""Report builders and renderers for the DCA sleeve's sell side (#857).

The RENDERERS are pure on purpose: each takes rows and returns lines, and none opens the database,
builds a broker or reads config -- the CLI (`keel/commands/dca.py`), the per-cycle notification
(P8) and the confirm banner (P18) each fetch what they need and hand it in, so all three print one
proposal the same way and none of them can drift from the others.

The BUILDERS (`distribution_rows`, P10) are handed an already-open repository and a loaded config
and READ them -- never write, never open, never build a broker. Opening read-only is the caller's
(`_common._open_repo_ro`), and "writes nothing" is pinned on the command by the database's own
change counter.

**One proposal, one line.** `render_proposal_line` is the summary a list prints and a notification
carries:

    #<id> <YYYY-MM-DD> <product> <rule_kind> (rule <id>, <status>) sell <qty> @ <price>
      gross $<g>  fee $<f> (<fee source>)  net $<n>  legs <k>  -> <decision>

(one line; wrapped here), with `superseded by <kind>` and `reviewed <YYYY-MM-DD>` appended only
when set. The date is the proposal's UTC day, because R14's "one proposal per product per UTC day"
is counted in UTC and the log must read in the same days the rule is enforced in.

**One distribution, one line** (`render_distribution`, for `keel dca distribute --preview`):

    rule <id> (<status>) <product> cadence bar <YYYY-MM-DD>, proposed <YYYY-MM-DD>: sell <qty>
    over <k> leg(s)
      gross $<g>  fee $<f> (fallback:config.fees.taker_pct)  gates <gate>=open ...

(one line; wrapped here), or `no sale  gates ... <gate>=closed` naming the gate the rule stopped
at, or `no sale  no cached daily close`. The fee source is always the fallback: the CLI previews
ask no venue (R25).

**The bar and the day it is proposed are different dates, and both print (#921).** `Dca.detect`
and `ReverseDca.reduce_signal` decide on the latest COMPLETED daily bar (`completed_days`,
`keel/strategy/rules/base.py`): a bar stamped day `d` closes at `(d + 1) * 86_400`, so the
earliest any cycle judges it -- and records the proposal `render_proposal_line` shows -- is UTC
day `d + 1`, never day `d` itself. A line naming only the bar day reads as a promise of a sale on
that day, which no cycle ever makes; naming both is unambiguous.

**NULL is "unrecorded", never zero.** A proposal whose rule row id, cost basis or net P&L was not
recorded prints `unrecorded` there -- a `$0.00` net would read as a break-even sale that nobody
computed.

**One proposal replay, per fee line** (`proposal_replay` / `render_replay`, for `keel rules
backtest` on a sleeve-sell rule, spec §3.7): a head naming the bars and the synthetic holding, the
`REPLAY_DISCLAIMER`, then per fee rate a `fee line: <pct> (<source>)` followed by one line per
cadence bar the rule fired on -- `<YYYY-MM-DD> bar: sell ...` or `<YYYY-MM-DD> bar: vetoed (<cap>)`
-- and one `terminal` line. It is a MECHANICAL replay of the rule's own proposals: a description
with no threshold, never evidence for choosing parameters (the research freeze, 2026-09-27).

**The trim report, per tranche and per asset** (`lots_view`/`render_lots` and
`bands_view`/`render_bands`, for `keel dca trim --preview`, P13): the lots report lists every open
tranche in the FIFO order a sale consumes it, with its unrealised and if-sold-now P&L, and says
it is not tax advice (spec §8.2); the bands report shows each target asset's weight against its
band, and says on every run that band trimming was tested and not adopted (#831) -- a display,
never a proposal (spec §5). Both mark at the latest cached daily close and print `mark none`,
never a zero, without one.

**The fee source is always printed beside the fee.** A venue-previewed commission and the
`config.fees.taker_pct` fallback are different kinds of number (the plan's Global Constraints:
"every fallback records its source"), and a fee shown without its source reads as the venue's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from keel.commands.doctor import _money

if TYPE_CHECKING:
    from collections.abc import Sequence

    from keel.strategy.rules.base import Rule
    from keel.types import Candle

_UNRECORDED = "unrecorded"


def _day(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), UTC).strftime("%Y-%m-%d")


def _plain(value: Decimal | None) -> str:
    """A quantity or price as recorded -- never rounded, never in exponent form."""
    return _UNRECORDED if value is None else format(value, "f")


def _usd(value: Decimal | None) -> str:
    return _UNRECORDED if value is None else _money(value)


def render_proposal_line(row: dict[str, Any]) -> str:
    """The one-line summary of a `sell_proposals` row (as `Repository.get_sell_proposal` returns
    it): the module docstring's format."""
    rule = _UNRECORDED if row["rule_id"] is None else str(row["rule_id"])
    parts = [
        f"#{row['id']} {_day(row['ts'])} {row['product_id']} {row['rule_kind']} "
        f"(rule {rule}, {row['rule_status']}) "
        f"sell {_plain(row['qty'])} @ {_plain(row['expected_price'])}",
        f"gross {_usd(row['expected_gross'])}",
        f"fee {_usd(row['expected_fee'])} ({row['fee_source']})",
        f"net {_usd(row['expected_net_pnl'])}",
        f"legs {row['legs']}",
        f"-> {row['decision']}",
    ]
    if row.get("superseded_by"):
        parts.append(f"superseded by {row['superseded_by']}")
    if row.get("reviewed_ts") is not None:
        parts.append(f"reviewed {_day(row['reviewed_ts'])}")
    return "  ".join(parts)


def render_proposal(row: dict[str, Any]) -> list[str]:
    """The summary line, then one indented `key: value` line per detail a reviewer needs to judge
    the proposal: what fired it (`trigger`), what the rails said (`rails`), where the fee came
    from, how many legs rail 2's slicing needs, and the lots it was costed against."""
    order = "none" if row.get("order_id") is None else f"#{row['order_id']}"
    return [
        render_proposal_line(row),
        f"  trigger: {json.dumps(row['trigger'], sort_keys=True, default=str)}",
        f"  rails: {json.dumps(row['rails'], sort_keys=True, default=str)}",
        f"  fee source: {row['fee_source']}",
        f"  legs: {row['legs']}",
        f"  vwae: {_plain(row['vwae'])}",
        f"  cost basis: {_usd(row['cost_basis'])}",
        f"  order: {order}",
    ]


# -- keel dca distribute --preview (P10) -----------------------------------------------------

_DAY = 86_400

#: The order `ReverseDca.reduce_signal` checks its gates in. It stops at the first closed one, so
#: a row names the gates up to and including that one and no further: a later gate was never
#: judged, and reporting it open would be a verdict the rule did not reach.
_GATE_ORDER = ("price_floor", "drawdown", "floor_qty")

#: What `render_distribution` prints when no `reverse_dca` rule is at a status the cycle runs.
NO_DISTRIBUTION_RULES = "no reverse_dca rule at a status this profile's cycle runs."


@dataclass(frozen=True)
class DistributionRow:
    """What one `reverse_dca` rule's next cadence BAR would do, on today's cached close, and
    when that would actually be proposed.

    `next_cadence_ts` is the BAR's own ts (`next_cadence_day(...) * 86_400`) -- the completed
    daily candle, epoch-aligned to the rule's cadence, that `ReverseDca.reduce_signal` (and
    `Dca.detect`, for the collision check) would read. Neither rule decides the day it PROPOSES
    on that bar: a bar stamped day `d` closes at `(d + 1) * 86_400`, so the earliest cycle to
    judge it runs on UTC day `d + 1` (`completed_days`, `keel/strategy/rules/base.py`) -- one day
    after the date this field names. `render_distribution` prints both dates (#921); nothing on
    this row is renamed to hold the proposal day, because it is always `next_cadence_ts + 86_400`
    and a second field would only be able to disagree with it.

    `rule_id` is the `rules.id`; `None` only for a rule built without a row, printed
    `unrecorded`. `gates` holds the gates the rule judged, in its own order, up to the first
    closed one (`_GATE_ORDER`); empty when there is no cached daily close to judge on. `qty`,
    `gross_usd` and `fee_usd` are `None` unless every gate is open -- no size is invented for a
    sale the rule would not make. `qty` is the WHOLE sale; `legs` is how many cycles (days) rail
    2's slicing needs for it (`sleeve.slice_qty`), 0 with no sale.
    """

    rule_id: int | None
    status: str
    product_id: str
    next_cadence_ts: int
    gates: dict[str, bool]
    qty: Decimal | None
    gross_usd: Decimal | None
    fee_usd: Decimal | None
    legs: int
    dca_collision: bool


def next_cadence_day(today: int, cadence_days: int) -> int:
    """The next UTC day number a cadence-`cadence_days` rule's BAR will be judged on, seen from
    `today` (`now_ts // 86_400`).

    `Dca.detect` and `ReverseDca.reduce_signal` both decide on the latest COMPLETED daily bar
    (`completed_days`, `keel/strategy/rules/base.py`): a bar stamped day `d` closes at
    `(d + 1) * 86_400`, so the newest bar ANY cycle running today (or later) can already have
    judged is `today - 1` -- yesterday's, closed at this morning's UTC rollover. That is this
    search's anchor, not `today` itself: the smallest `d >= today - 1` with
    `d % cadence_days == 0`.

    A `d` equal to `today` is kept: that bar closes tonight, so tomorrow's cycle judges it and
    records its proposal on `d + 1`. It is the next distribution, and the rendered line says so
    with its two dates (`cadence bar d, proposed d + 1`).
    """
    anchor = today - 1
    return anchor + (-anchor) % cadence_days


def distribution_rows(repo: Any, config: Any, now_ts: int) -> list[DistributionRow]:
    """One `DistributionRow` per `reverse_dca` rule this profile's cycle would ask (#857, spec §6
    "CLI"). READ-ONLY: it writes nothing and builds no broker (R25).

    **The rules are the cycle's own** (`agent._sleeve_rules`): `paper` and `live` on a live
    profile, `paper` alone on a paper one (R16). A candidate or disabled seller is not previewed,
    because no cycle would ask it.

    **The decision is the rule's own arithmetic, not a copy of it.** The latest cached daily bar
    is re-stamped to the next cadence BAR day (`next_cadence_day`, #921) and handed, alone with
    its history, to the rule's `reduce_signal`; the cadence gate therefore passes by construction
    and the floor, drawdown and `floor_qty` gates are judged on today's close. The rule stops at
    its first closed gate, and so does the row (`DistributionRow.gates`). The bar day is not the
    day the sale is proposed on -- see `DistributionRow`'s docstring.

    **The size is priced as the proposal would be on the fallback** (`sleeve.sell_costs`: the
    `config.fees.taker_pct` rate and the product's liquidity-scaled slippage): gross is the sale's
    notional less slippage and the fee is the notional at the fallback rate -- `record_proposal`'s
    and `executor.reduce`'s definitions. The live proposal carries the venue's previewed fee; this
    command asks no venue. `legs` comes from `sleeve.slice_qty`, with the venue increment the
    executor has CACHED (`executor._base_increment_for` with no broker: a fresh cached record or
    `None`, never a venue call).

    **`dca_collision` is the pipeline's same-day-DCA refusal, read ahead** (spec §3.4, Review
    Focus 1): a `dca` rule on the product at the status this profile's cycle runs, whose cadence
    also falls on the cadence BAR day (the same day `_dca_fires_today`'s `Dca.detect` would see,
    since both rules read the same restamped bar). The pipeline records that day's proposal
    `vetoed` (`same_day_dca`) and does not carry it forward.
    """
    # Lazy: the agent imports this package's siblings, and a report must not pull the cycle's
    # whole import graph in at module load.
    from keel import agent
    from keel.execution import executor, sleeve
    from keel.strategy.rules.reverse_dca import ReverseDca
    from keel.types import Granularity

    today = now_ts // _DAY
    buy_status = "paper" if config.auto_trade.mode == "paper" else "live"
    dca_cadences: dict[str, list[int]] = {}
    for row in repo.get_rules(buy_status):
        if row["kind"] == "dca":
            params = row.get("params") or {}
            dca_cadences.setdefault(str(params.get("product_id")), []).append(
                int(params.get("cadence_days", 7))
            )

    rows: list[DistributionRow] = []
    for rule, status in agent._sleeve_rules(repo, config):
        if not isinstance(rule, ReverseDca):
            continue
        product_id = rule.product_id
        day = next_cadence_day(today, int(rule.params["cadence_days"]))
        collision = any(day % cadence == 0 for cadence in dca_cadences.get(product_id, []))
        daily = repo.get_candles(product_id, Granularity.ONE_DAY)
        if not daily:
            rows.append(
                DistributionRow(
                    rule.rule_id, status, product_id, day * _DAY, {}, None, None, None, 0, collision
                )
            )
            continue
        restamped = [*daily[:-1], replace(daily[-1], ts=day * _DAY)]
        holding = sleeve.holding_of(repo, product_id, daily[-1].close)
        costs = sleeve.sell_costs(repo, config, product_id)
        reduction = rule.reduce_signal(holding, {Granularity.ONE_DAY: restamped}, costs)
        if reduction is None:
            closed = (rule.last_rejection or {}).get("gate")
            gates: dict[str, bool] = {}
            for gate in _GATE_ORDER:
                if gate == closed:
                    gates[gate] = False
                    break
                gates[gate] = True
            else:
                gates = {}  # no gate was judged (no usable close)
            rows.append(
                DistributionRow(
                    rule.rule_id,
                    status,
                    product_id,
                    day * _DAY,
                    gates,
                    None,
                    None,
                    None,
                    0,
                    collision,
                )
            )
            continue
        qty = min(reduction.qty, holding.qty)
        price = reduction.expected_price
        _leg, legs = sleeve.slice_qty(
            qty,
            price,
            max_per_order_usd=config.caps.max_per_order_usd,
            base_increment=executor._base_increment_for(None, repo, product_id, now_ts),
        )
        rows.append(
            DistributionRow(
                rule_id=rule.rule_id,
                status=status,
                product_id=product_id,
                next_cadence_ts=day * _DAY,
                gates=dict.fromkeys(_GATE_ORDER, True),
                qty=qty,
                gross_usd=qty * price * (Decimal("1") - costs.slippage_pct),
                fee_usd=qty * price * costs.fee_pct,
                legs=legs,
                dca_collision=collision,
            )
        )
    return rows


def render_distribution(rows: list[DistributionRow]) -> list[str]:
    """One line per row -- the module docstring's distribution format -- and, under a SALE whose
    bar is also a dca buy day, one indented line saying the pipeline will veto it. A row with a
    closed gate gets no such line: the rule proposes nothing, so there is nothing to veto.

    The head names the cadence BAR and the day it is proposed on (bar + 1 day, #921): the bar is
    what the rule judges, the proposal day is when a cycle judging it actually records something,
    and a line naming only one of the two reads as a promise for the wrong day."""
    if not rows:
        return [NO_DISTRIBUTION_RULES]
    from keel.execution.sleeve import FALLBACK_FEE_SOURCE, SAME_DAY_DCA

    lines: list[str] = []
    for row in rows:
        rule = _UNRECORDED if row.rule_id is None else str(row.rule_id)
        proposed_ts = row.next_cadence_ts + _DAY
        head = (
            f"rule {rule} ({row.status}) {row.product_id} "
            f"cadence bar {_day(row.next_cadence_ts)}, proposed {_day(proposed_ts)}: "
        )
        gates = "  gates " + " ".join(
            f"{name}={'open' if ok else 'closed'}" for name, ok in row.gates.items()
        )
        if not row.gates:
            lines.append(head + "no sale  no cached daily close")
        elif row.qty is None:
            lines.append(head + "no sale" + gates)
        else:
            legs = f"{row.legs} leg{'' if row.legs == 1 else 's'}"
            lines.append(
                head
                + f"sell {_plain(row.qty)} over {legs}"
                + f"  gross {_usd(row.gross_usd)}"
                + f"  fee {_usd(row.fee_usd)} ({FALLBACK_FEE_SOURCE})"
                + gates
            )
        if row.dca_collision and row.qty is not None:
            lines.append(
                f"  a dca buy falls on the same day: the pipeline records it vetoed "
                f"({SAME_DAY_DCA}), and it is not carried forward"
            )
    return lines


# -- keel rules backtest on a sleeve-sell rule: the proposal replay (P12, spec §3.7) -----------

_ZERO = Decimal("0")

#: Printed under every replay's head, verbatim: what the replay is, and what it is not.
REPLAY_DISCLAIMER = (
    "a mechanical replay of this rule's own proposals over the cached bars -- a description, "
    "not a pass mark (spec §3.7), and not evidence for choosing parameters"
)


@dataclass(frozen=True)
class ReplaySale:
    """One proposal the replay would have recorded as a `preview`, and what selling it books.

    `day` is the decided BAR's UTC day number (`ts // 86_400`); the proposal itself is made the
    day after (R43). `qty` is the proposal's leg -- the first leg of rail 2's slicing, as a live
    proposal row records it -- and `legs` is how many legs (days) the whole sale needs, `None`
    when no cap was supplied to slice against. `gross_usd` is the notional less slippage,
    `fee_usd` the notional at the line's fee rate, `cost_basis` the FIFO, fee-inclusive basis of
    the units consumed (`Holding.fifo_cost`), and `realised_pnl = gross - fee - basis`:
    `sleeve.record_proposal`'s definitions, so a replayed row reads like a recorded one."""

    day: int
    qty: Decimal
    price: Decimal
    gross_usd: Decimal
    fee_usd: Decimal
    cost_basis: Decimal
    realised_pnl: Decimal
    legs: int | None


@dataclass(frozen=True)
class ReplayVeto:
    """A bar the rule fired on that the pipeline would have recorded `vetoed`, and why (the
    `sleeve_refusal` cap, or `nothing_held`). Not carried forward, as live."""

    day: int
    reason: str


@dataclass(frozen=True)
class ProposalReplay:
    """`proposal_replay`'s result for one fee rate. `terminal_with` marks the units left at the
    last close and adds the cash the sales raised (gross less fee); `terminal_without` is the
    same synthetic holding never sold, at the same close. Both are marks, not exits: neither
    pays a fee to leave."""

    product_id: str
    fee_pct: Decimal
    slippage_pct: Decimal
    n_bars: int
    start_price: Decimal | None
    final_close: Decimal | None
    rows: tuple[ReplaySale, ...]
    vetoed: tuple[ReplayVeto, ...]
    units_left: Decimal
    cash_usd: Decimal

    @property
    def terminal_with(self) -> Decimal | None:
        if self.final_close is None:
            return None
        return self.units_left * self.final_close + self.cash_usd

    @property
    def terminal_without(self) -> Decimal | None:
        return None if self.final_close is None else self.final_close


def proposal_replay(
    rule: Rule,
    daily: Sequence[Candle],
    *,
    fee_pct: Decimal,
    slippage_pct: Decimal,
    max_per_order_usd: Decimal | None = None,
    dca_rules: Sequence[Rule] = (),
) -> ProposalReplay:
    """Walk `daily` bar by bar and replay what `rule`'s `reduce_signal` would have proposed,
    through the live pipeline's own sleeve policy (spec §3.7). Pure and deterministic: no repo,
    no config, no clock.

    **The holding is synthetic, and says so:** one lot of 1 unit bought at the FIRST close, on
    the first bar, fee-free -- a sleeve-sell rule needs something to sell, and the ledger's real
    lots are not history. Each sale consumes it FIFO (`Holding.fifo_legs`), so a later bar sees
    what the earlier sales left.

    **Each bar is decided as a cycle would decide it:** `reduce_signal` over the prefix ending at
    that bar (it reads completed daily bars only), at `SellCosts(fee_pct, slippage_pct)`. The
    proposal is made the day after the bar (R43), so that is `now_ts` for the sleeve caps:
    `sleeve.sleeve_refusal` -- the same-day-DCA exclusion, from each of `dca_rules`' own `detect`
    over the same prefix (as `agent._dca_fires_today` asks it -- **a `detect` that RAISES counts
    as firing**, mirroring that function's own rule verbatim: "the refusal is the direction that
    costs nothing"), `min_hold_days` on the lots the sale would consume (R12), and the rule's
    `cooldown_days` from its last replayed sale (R15). A refused bar is a `ReplayVeto` and is not
    carried forward. A bar that passes sells
    `min(reduction.qty, held)`, sliced by `sleeve.slice_qty` when `max_per_order_usd` is given
    (no venue increment: this asks no venue), and the replay books that first leg -- what a
    proposal row records -- with the whole sale's leg count beside it.

    **A fidelity check, not research.** The rule is replayed at ONE parameter set, the operator's;
    nothing is searched, scored against a threshold, or appended to the trials ledger.
    """
    from keel.execution import sleeve
    from keel.strategy.reduction import Holding, Lot, SellCosts
    from keel.types import Granularity

    costs = SellCosts(fee_pct, slippage_pct, sleeve.FALLBACK_FEE_SOURCE)
    product_id = str(getattr(rule, "product_id", ""))
    params = getattr(rule, "params", {}) or {}

    def _dca_fires(d: Rule, prefix: dict[Granularity, list[Candle]]) -> bool:
        """`agent._dca_fires_today`'s own rule, mirrored: a `detect` that RAISES counts as
        firing -- refusing the sale costs nothing, and a broken dca rule must not silently let
        a round trip through."""
        try:
            return d.detect(prefix) is not None
        except Exception:  # noqa: BLE001 -- fail toward vetoing the sale, never the replay
            return True

    if not daily:
        return ProposalReplay(
            product_id, fee_pct, slippage_pct, 0, None, None, (), (), Decimal("0"), Decimal("0")
        )

    first = daily[0]
    holding = Holding(product_id, (Lot(0, "dca", first.ts, Decimal("1"), first.close, _ZERO),))
    sales: list[ReplaySale] = []
    vetoes: list[ReplayVeto] = []
    cash = _ZERO
    last_sale_ts: int | None = None
    for i, bar in enumerate(daily):
        prefix = {Granularity.ONE_DAY: list(daily[: i + 1])}
        reduction = rule.reduce_signal(holding, prefix, costs)
        if reduction is None:
            continue
        day = reduction.ts // _DAY
        now_ts = bar.ts + _DAY
        total = min(reduction.qty, holding.qty)
        if total <= 0:
            vetoes.append(ReplayVeto(day, "nothing_held"))
            continue
        refusal = sleeve.sleeve_refusal(
            reduction=reduction,
            holding=holding,
            rule_kind=reduction.reason,
            rule_params=params,
            dca_fires_today=any(_dca_fires(d, prefix) for d in dca_rules),
            last_rule_proposal_ts=last_sale_ts,
            now_ts=now_ts,
        )
        if refusal is not None:
            vetoes.append(ReplayVeto(day, refusal))
            continue
        price = reduction.expected_price
        legs: int | None = None
        qty = total
        if max_per_order_usd is not None:
            qty, legs = sleeve.slice_qty(
                total, price, max_per_order_usd=max_per_order_usd, base_increment=None
            )
            if legs == 0:
                vetoes.append(ReplayVeto(day, "below_one_increment"))
                continue
        notional = qty * price
        gross = notional * (Decimal("1") - slippage_pct)
        fee = notional * fee_pct
        basis = holding.fifo_cost(qty)
        sales.append(ReplaySale(day, qty, price, gross, fee, basis, gross - fee - basis, legs))
        cash += gross - fee
        last_sale_ts = now_ts
        holding = _after_sale(holding, qty)

    return ProposalReplay(
        product_id=product_id,
        fee_pct=fee_pct,
        slippage_pct=slippage_pct,
        n_bars=len(daily),
        start_price=first.close,
        final_close=daily[-1].close,
        rows=tuple(sales),
        vetoed=tuple(vetoes),
        units_left=holding.qty,
        cash_usd=cash,
    )


def _after_sale(holding: Any, qty: Decimal) -> Any:
    """`holding` less a FIFO sale of `qty`: each consumed lot keeps what it did not sell, its
    sold units moved to `realized_qty` -- the ledger's own reading of a partial exit, so the
    entry fee stays prorated by `Lot.entry_fee_share` exactly as on a real tranche."""
    taken = {lot.position_id: take for lot, take in holding.fifo_legs(qty)}
    lots = tuple(
        replace(
            lot,
            qty=lot.qty - taken.get(lot.position_id, _ZERO),
            realized_qty=lot.realized_qty + taken.get(lot.position_id, _ZERO),
        )
        for lot in holding.lots
    )
    return replace(holding, lots=tuple(lot for lot in lots if lot.qty > 0))


def _pct(rate: Decimal) -> str:
    return f"{rate * 100:.4f}%"


def render_replay(
    *,
    rule_id: int,
    kind: str,
    replays: Sequence[tuple[ProposalReplay, str]],
    slippage_measured: bool,
    max_per_order_usd: Decimal | None,
    dca_note: str,
) -> list[str]:
    """The replay's lines (the module docstring's format): one head, then per `(replay,
    fee_label)` a `fee line:` and its bars and terminal. Every replay shares the bars and the
    synthetic holding, so the head is printed once from the first."""
    head = replays[0][0]
    if head.n_bars == 0 or head.start_price is None:
        return [f"rule {rule_id} ({kind}) {head.product_id}: no cached daily bars to replay."]
    slippage = "measured" if slippage_measured else "floor, no daily volume to measure"
    cap = (
        "no config: legs not sliced"
        if max_per_order_usd is None
        else f"rail 2 cap {_usd(max_per_order_usd)} per leg (config.caps.max_per_order_usd)"
    )
    lines = [
        f"rule {rule_id} ({kind}) {head.product_id}: proposal replay over {head.n_bars} cached "
        "ONE_DAY bars",
        REPLAY_DISCLAIMER,
        f"  holding: a synthetic 1 unit bought at the first close {_plain(head.start_price)}, "
        f"fee-free; slippage {_pct(head.slippage_pct)} per leg ({slippage}); {cap}",
        f"  same-day dca: {dca_note}",
    ]
    for replay, fee_label in replays:
        lines.append(f"fee line: {_pct(replay.fee_pct)} ({fee_label})")
        events: list[tuple[int, str]] = [
            (
                sale.day,
                f"  {_day(sale.day * _DAY)} bar: sell {_plain(sale.qty)} @ {_plain(sale.price)}"
                f"  gross {_usd(sale.gross_usd)}  fee {_usd(sale.fee_usd)}"
                f"  basis {_usd(sale.cost_basis)}  realised {_usd(sale.realised_pnl)}"
                f"  legs {'unsliced' if sale.legs is None else sale.legs}",
            )
            for sale in replay.rows
        ]
        events += [
            (veto.day, f"  {_day(veto.day * _DAY)} bar: vetoed ({veto.reason})")
            for veto in replay.vetoed
        ]
        if not events:
            lines.append("  the rule proposed nothing over these bars")
        lines.extend(text for _day_no, text in sorted(events, key=lambda e: e[0]))
        lines.append(
            f"  terminal at the last close {_plain(replay.final_close)}: with the rule "
            f"{_usd(replay.terminal_with)} ({_plain(replay.units_left)} units + "
            f"{_usd(replay.cash_usd)} cash), without {_usd(replay.terminal_without)}"
        )
    return lines


# -- keel dca trim --preview --view lots (P13, spec §8.1) -------------------------------------

#: Printed once on every lots report, spec §8.2: keel records no jurisdiction, asserts no tax law
#: and computes no tax number. A realised figure here is P&L, not a tax outcome.
NOT_TAX_ADVICE = (
    "not tax advice: keel records no jurisdiction and computes no tax (spec §8.2); "
    "'if sold now' is P&L after fee and slippage, nothing more"
)
#: Printed once under the head: the order IS the information (Review Focus 3).
LOTS_FIFO_NOTE = "FIFO order -- a sale consumes these top to bottom"
#: What the lots report prints when the ledger holds no open tranche.
NO_OPEN_LOTS = "no open tranches in the positions ledger."


@dataclass(frozen=True)
class LotRow:
    """One open `positions` tranche as a sale would meet it (spec §8.1).

    `entry_fee_share` and `cost` are `Lot`'s own (`keel/strategy/reduction.py`): the entry fee
    prorated to the units still held, and the fee-inclusive basis of those units (spec Q4).
    `fee_pct` and `slippage_pct` are the rates the figures below are priced at --
    `sleeve.sell_costs`, the fallback fee and the product's liquidity-scaled slippage (R25).

    `mark` is the product's latest cached daily close, and every figure that needs one is
    `None` without it -- `unrealised` (marked value less `cost`) and `realised_if_sold` (what
    selling the tranche alone at the mark would book: notional less slippage, less the fee on
    the notional, less `cost` -- `sleeve.record_proposal`'s definitions). A missing mark is
    missing data, not a total loss (`keel/commands/positions.py`, "what is absent stays
    absent")."""

    product_id: str
    position_id: int
    rule_name: str
    opened_at: int
    qty: Decimal
    entry_fill: Decimal
    entry_fee_share: Decimal
    cost: Decimal
    fee_pct: Decimal
    slippage_pct: Decimal
    mark: Decimal | None
    unrealised: Decimal | None
    realised_if_sold: Decimal | None


def _daily_mark(repo: Any, product_id: str) -> Decimal | None:
    """The latest cached ONE_DAY close, or `None`: the bar the sleeve-sell rules decide on."""
    from keel.types import Granularity

    daily = repo.get_candles(product_id, Granularity.ONE_DAY)
    return daily[-1].close if daily else None


def lots_view(repo: Any, config: Any, product_id: str | None = None) -> list[LotRow]:
    """Every open tranche, one `LotRow` each (spec §8.1). READ-ONLY: it writes nothing and builds
    no broker (R25).

    **The tranches are `sleeve.holding_of`'s** -- the `positions` ledger, every rule's tranche
    (no `rule_name` filter, R8), oldest first -- so the order printed is the order `book_exit`
    consumes: on PAXG, turtle tranche 3 is the first row (Review Focus 3). Products are listed
    in product-id order; `product_id` narrows to one.

    **Not `keel pnl`** (spec §8.1): that command is FIFO over the imported `transactions`
    table. This reads `positions` only, and the two are kept apart rather than reconciled
    under one flag.
    """
    from keel.execution import sleeve

    products = sorted({str(p["product_id"]) for p in repo.get_open_positions(product_id)})
    rows: list[LotRow] = []
    for product in products:
        mark = _daily_mark(repo, product)
        costs = sleeve.sell_costs(repo, config, product)
        for lot in sleeve.holding_of(repo, product, mark).lots:
            cost = lot.cost
            if mark is None:
                unrealised = realised = None
            else:
                notional = lot.qty * mark
                unrealised = notional - cost
                realised = (
                    notional * (Decimal("1") - costs.slippage_pct) - notional * costs.fee_pct - cost
                )
            rows.append(
                LotRow(
                    product_id=product,
                    position_id=lot.position_id,
                    rule_name=lot.rule_name,
                    opened_at=lot.opened_at,
                    qty=lot.qty,
                    entry_fill=lot.entry_fill,
                    entry_fee_share=lot.entry_fee_share,
                    cost=cost,
                    fee_pct=costs.fee_pct,
                    slippage_pct=costs.slippage_pct,
                    mark=mark,
                    unrealised=unrealised,
                    realised_if_sold=realised,
                )
            )
    return rows


def render_lots(rows: Sequence[LotRow]) -> list[str]:
    """The lots report: a head naming the fee source, `LOTS_FIFO_NOTE`, one heading per product
    over its tranches, and `NOT_TAX_ADVICE` last. One line per tranche:

        #<id> <rule> opened <YYYY-MM-DD> qty <q> @ <fill>  fee share $<f>  cost $<c>
        mark <m>  unrealised $<u>  if sold now $<r> (fee <pct>, slippage <pct>)

    (one line; wrapped here), or `mark none (no cached daily close)` in place of everything
    after `cost` when there is no mark."""
    from keel.execution.sleeve import FALLBACK_FEE_SOURCE

    lines = [
        f"lots -- fees at the fallback rate ({FALLBACK_FEE_SOURCE}); no venue asked",
        LOTS_FIFO_NOTE,
    ]
    if not rows:
        lines.append(NO_OPEN_LOTS)
    product: str | None = None
    for row in rows:
        if row.product_id != product:
            product = row.product_id
            lines.append(product)
        head = (
            f"  #{row.position_id} {row.rule_name} opened {_day(row.opened_at)} "
            f"qty {_plain(row.qty)} @ {_plain(row.entry_fill)}"
            f"  fee share {_usd(row.entry_fee_share)}  cost {_usd(row.cost)}"
        )
        if row.mark is None:
            lines.append(head + "  mark none (no cached daily close)")
            continue
        lines.append(
            head
            + f"  mark {_plain(row.mark)}  unrealised {_usd(row.unrealised)}"
            + f"  if sold now {_usd(row.realised_if_sold)}"
            + f" (fee {_pct(row.fee_pct)}, slippage {_pct(row.slippage_pct)})"
        )
    lines.append(NOT_TAX_ADVICE)
    return lines


# -- keel dca trim --preview --view bands (P13, spec §5, read-only) ---------------------------

#: Spec §5's band: `max(BAND_REL x target, BAND_ABS_FLOOR)` either side of the target weight --
#: the parameters the spec gives the `band_rebalance` kind it recommends NOT building. They are
#: display constants here, not a rule's params: nothing trades on them.
BAND_REL = Decimal("0.15")
BAND_ABS_FLOOR = Decimal("0.015")

#: Printed once under the bands head: what the view is, and what it is not.
BANDS_NOT_A_RECOMMENDATION = (
    "a read-only drift display, not a recommendation: 'size to target' is arithmetic on the "
    "band, and nothing here proposes, records or places a trade"
)
#: Printed once on every bands report, after the rows (spec §5, "Evidence status"; the research
#: freeze of 2026-09-27). It claims no edge for trading to these bands, because none was found.
BANDS_NOT_ADOPTED = (
    "band trimming was tested and not adopted (#831): pre-registered and bootstrapped, it was "
    "not better than static DCA after fees, so no performance edge is claimed for these bands "
    "and keel builds no band_rebalance rule"
)
#: Printed once on every bands report (spec §5 and §2.5): the redeploy leg is a BUY.
BANDS_REDEPLOY_NOTE = (
    "a redeploy leg would spend this month's rail-14 buy cap and meet rail 8; the fee drag "
    "counts both legs"
)
#: What the bands report prints when no target asset is held.
NO_BAND_ROWS = "nothing held in the target_weights assets: no weights to compare."

BandStatus = Literal["over", "under", "within"]


def _weight_pct(fraction: Decimal) -> str:
    return f"{fraction * 100:.2f}%"


def bands_incomplete_line(assets: Sequence[str]) -> str:
    return (
        f"incomplete: no cached daily close for {', '.join(assets)} -- no weight is computed "
        "from a partial sleeve"
    )


def bands_untargeted_line(products: Sequence[str]) -> str:
    return f"held but not in target_weights, not weighed: {', '.join(products)}"


@dataclass(frozen=True)
class BandRow:
    """One target asset's weight against its target (spec §5).

    `target_weight` is the config's `target_weights` entry normalised over the positive
    weights (as `keel dca plan` renormalises); `weight` is `value_usd` over the sleeve's total
    marked value; `band` is `max(BAND_REL x target, BAND_ABS_FLOOR)`. `status` is `over` only
    when `weight > target + band` and `under` only when `weight < target - band` -- the spec's
    strict trigger, so a weight ON the edge is `within`.

    `candidate_sell_usd` and `fee_drag_usd` are set only when `over`: the dollars that would
    bring the weight back to the target, and what the two legs of that round trip would cost --
    the sell leg at this product's slippage, the redeploy BUY leg at the shortfall-weighted
    slippage of the underweight assets, both at the fallback fee (R25). Numbers for the
    operator's question, never a proposal (spec §5: band rebalancing is not built)."""

    asset: str
    target_weight: Decimal
    weight: Decimal
    band: Decimal
    status: BandStatus
    value_usd: Decimal
    candidate_sell_usd: Decimal | None
    fee_drag_usd: Decimal | None


@dataclass(frozen=True)
class BandsReport:
    """`bands_view`'s result. `incomplete` names the held target assets with no cached daily
    close; when it is non-empty `rows` is empty, because every weight's denominator would be
    wrong. `untargeted` names held products whose asset has no positive target weight: they
    are not in the sleeve the weights are taken over, and the report says so rather than
    dropping them silently."""

    rows: tuple[BandRow, ...]
    incomplete: tuple[str, ...]
    untargeted: tuple[str, ...]


def bands_view(repo: Any, config: Any) -> BandsReport:
    """Each target asset's weight against `config.target_weights`, its band, and -- when over
    -- the size back to target and its two-leg fee drag (spec §5). READ-ONLY: it writes
    nothing, builds no broker and proposes nothing (R25).

    **Only a display.** Spec §5 recommends building neither steering nor band rebalancing, and
    #831's bootstrap found band trimming no better than static DCA after fees; the rendered
    report says so on every run (`BANDS_NOT_ADOPTED`).

    **The sleeve is the target assets' holdings.** Each is `sleeve.holding_of` over the asset's
    product in the settlement currency (`_history_product`), every rule's tranches (R8), marked
    at the latest cached daily close. A held target with no mark makes the whole report
    `incomplete` -- one missing value would mis-state every weight -- and a target that is not
    held weighs zero and needs no mark.
    """
    from keel.commands._products import _history_product
    from keel.commands.dca_plan import _weights_by_asset
    from keel.execution import sleeve
    from keel.execution.guards import _asset

    weights_raw = _weights_by_asset(config.target_weights, "target_weights")
    positive = {asset: w for asset, w in weights_raw.items() if w > 0}
    total_weight = sum(positive.values(), Decimal("0"))
    targets = {asset: w / total_weight for asset, w in positive.items()}
    quote = config.quote_currency

    held_products = sorted({str(p["product_id"]) for p in repo.get_open_positions()})
    untargeted = tuple(p for p in held_products if _asset(p).upper() not in targets)

    values: dict[str, Decimal] = {}
    products: dict[str, str] = {}
    incomplete: list[str] = []
    for asset in targets:
        product = _history_product(asset, quote)
        products[asset] = product
        holding = sleeve.holding_of(repo, product)
        if holding.qty <= 0:
            values[asset] = Decimal("0")
            continue
        mark = _daily_mark(repo, product)
        if mark is None:
            incomplete.append(asset)
            continue
        values[asset] = holding.qty * mark
    total = sum(values.values(), Decimal("0"))
    if incomplete or total <= 0:
        return BandsReport((), tuple(incomplete), untargeted)

    weights = {asset: values[asset] / total for asset in targets}
    slippage = {
        asset: sleeve.sell_costs(repo, config, products[asset]).slippage_pct for asset in targets
    }
    fee = config.fees.taker_pct
    shortfalls = {a: targets[a] - weights[a] for a in targets if weights[a] < targets[a]}
    shortfall_total = sum(shortfalls.values(), Decimal("0"))
    buy_slippage = (
        sum((slippage[a] * s for a, s in shortfalls.items()), Decimal("0")) / shortfall_total
        if shortfall_total > 0
        else Decimal("0")
    )

    rows: list[BandRow] = []
    for asset, target in targets.items():
        weight = weights[asset]
        band = max(BAND_REL * target, BAND_ABS_FLOOR)
        status: BandStatus
        candidate = drag = None
        if weight > target + band:
            status = "over"
            candidate = (weight - target) * total
            drag = candidate * (fee + slippage[asset]) + candidate * (fee + buy_slippage)
        elif weight < target - band:
            status = "under"
        else:
            status = "within"
        rows.append(BandRow(asset, target, weight, band, status, values[asset], candidate, drag))
    return BandsReport(tuple(rows), (), untargeted)


def render_bands(report: BandsReport) -> list[str]:
    """The bands report: a head naming the band and the fee source,
    `BANDS_NOT_A_RECOMMENDATION`, one line per target asset --

        <asset> target <t>%  weight <w>%  band ±<b>%  <status>  value $<v>
        [size to target $<c>  two-leg fee drag $<d>]

    (one line; wrapped here; the bracketed part only when over) -- or the incomplete / empty
    line, the untargeted line when there is one, then `BANDS_REDEPLOY_NOTE` and
    `BANDS_NOT_ADOPTED`, on every run."""
    from keel.execution.sleeve import FALLBACK_FEE_SOURCE

    lines = [
        f"bands -- weights against config target_weights, band = max({_weight_pct(BAND_REL)} x "
        f"target, {_weight_pct(BAND_ABS_FLOOR)}); fees at the fallback rate "
        f"({FALLBACK_FEE_SOURCE}), per-product slippage; no venue asked",
        BANDS_NOT_A_RECOMMENDATION,
    ]
    if report.incomplete:
        lines.append(bands_incomplete_line(report.incomplete))
    elif not report.rows:
        lines.append(NO_BAND_ROWS)
    for row in report.rows:
        line = (
            f"  {row.asset} target {_weight_pct(row.target_weight)}  "
            f"weight {_weight_pct(row.weight)}  band ±{_weight_pct(row.band)}  {row.status}  "
            f"value {_usd(row.value_usd)}"
        )
        if row.candidate_sell_usd is not None:
            line += (
                f"  size to target {_usd(row.candidate_sell_usd)}"
                f"  two-leg fee drag {_usd(row.fee_drag_usd)}"
            )
        lines.append(line)
    if report.untargeted:
        lines.append(bands_untargeted_line(report.untargeted))
    lines.append(BANDS_REDEPLOY_NOTE)
    lines.append(BANDS_NOT_ADOPTED)
    return lines
