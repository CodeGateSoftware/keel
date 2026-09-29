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

    rule <id> (<status>) <product> next cadence <YYYY-MM-DD>: sell <qty> over <k> leg(s)
      gross $<g>  fee $<f> (fallback:config.fees.taker_pct)  gates <gate>=open ...

(one line; wrapped here), or `no sale  gates ... <gate>=closed` naming the gate the rule stopped
at, or `no sale  no cached daily close`. The fee source is always the fallback: the CLI previews
ask no venue (R25).

**NULL is "unrecorded", never zero.** A proposal whose rule row id, cost basis or net P&L was not
recorded prints `unrecorded` there -- a `$0.00` net would read as a break-even sale that nobody
computed.

**The fee source is always printed beside the fee.** A venue-previewed commission and the
`config.fees.taker_pct` fallback are different kinds of number (the plan's Global Constraints:
"every fallback records its source"), and a fee shown without its source reads as the venue's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from keel.commands.doctor import _money

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
    """What one `reverse_dca` rule's NEXT cadence day would do, on today's cached close.

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
    """The smallest UTC day number `d >= today` with `d % cadence_days == 0` -- the epoch-aligned
    cadence `ReverseDca` and `Dca` both read (`latest.ts // 86400 % cadence_days == 0`)."""
    return today + (-today) % cadence_days


def distribution_rows(repo: Any, config: Any, now_ts: int) -> list[DistributionRow]:
    """One `DistributionRow` per `reverse_dca` rule this profile's cycle would ask (#857, spec §6
    "CLI"). READ-ONLY: it writes nothing and builds no broker (R25).

    **The rules are the cycle's own** (`agent._sleeve_rules`): `paper` and `live` on a live
    profile, `paper` alone on a paper one (R16). A candidate or disabled seller is not previewed,
    because no cycle would ask it.

    **The decision is the rule's own arithmetic, not a copy of it.** The latest cached daily bar
    is re-stamped to the next cadence day and handed, alone with its history, to the rule's
    `reduce_signal`; the cadence gate therefore passes by construction and the floor, drawdown and
    `floor_qty` gates are judged on today's close. The rule stops at its first closed gate, and so
    does the row (`DistributionRow.gates`).

    **The size is priced as the proposal would be on the fallback** (`sleeve.sell_costs`: the
    `config.fees.taker_pct` rate and the product's liquidity-scaled slippage): gross is the sale's
    notional less slippage and the fee is the notional at the fallback rate -- `record_proposal`'s
    and `executor.reduce`'s definitions. The live proposal carries the venue's previewed fee; this
    command asks no venue. `legs` comes from `sleeve.slice_qty`, with the venue increment the
    executor has CACHED (`executor._base_increment_for` with no broker: a fresh cached record or
    `None`, never a venue call).

    **`dca_collision` is the pipeline's same-day-DCA refusal, read ahead** (spec §3.4, Review
    Focus 1): a `dca` rule on the product at the status this profile's cycle runs, whose cadence
    also falls on that day. The pipeline records that day's proposal `vetoed` (`same_day_dca`)
    and does not carry it forward.
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
    day is also a dca buy day, one indented line saying the pipeline will veto it. A row with a
    closed gate gets no such line: the rule proposes nothing, so there is nothing to veto."""
    if not rows:
        return [NO_DISTRIBUTION_RULES]
    from keel.execution.sleeve import FALLBACK_FEE_SOURCE, SAME_DAY_DCA

    lines: list[str] = []
    for row in rows:
        rule = _UNRECORDED if row.rule_id is None else str(row.rule_id)
        head = (
            f"rule {rule} ({row.status}) {row.product_id} "
            f"next cadence {_day(row.next_cadence_ts)}: "
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
