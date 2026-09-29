"""Pure report builders and renderers for the DCA sleeve's sell side (#857).

Pure on purpose: every function here takes rows and returns lines. None opens the database, builds
a broker or reads config -- the CLI (`keel/commands/dca.py`), the per-cycle notification (P8) and
the confirm banner (P18) each fetch what they need and hand it in, so all three print one proposal
the same way and none of them can drift from the others.

**One proposal, one line.** `render_proposal_line` is the summary a list prints and a notification
carries:

    #<id> <YYYY-MM-DD> <product> <rule_kind> (rule <id>, <status>) sell <qty> @ <price>
      gross $<g>  fee $<f> (<fee source>)  net $<n>  legs <k>  -> <decision>

(one line; wrapped here), with `superseded by <kind>` and `reviewed <YYYY-MM-DD>` appended only
when set. The date is the proposal's UTC day, because R14's "one proposal per product per UTC day"
is counted in UTC and the log must read in the same days the rule is enforced in.

**NULL is "unrecorded", never zero.** A proposal whose rule row id, cost basis or net P&L was not
recorded prints `unrecorded` there -- a `$0.00` net would read as a break-even sale that nobody
computed.

**The fee source is always printed beside the fee.** A venue-previewed commission and the
`config.fees.taker_pct` fallback are different kinds of number (the plan's Global Constraints:
"every fallback records its source"), and a fee shown without its source reads as the venue's.
"""

from __future__ import annotations

import json
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
