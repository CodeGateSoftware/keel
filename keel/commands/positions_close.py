"""`keel positions close <id>` -- record a sale the operator made on the venue, out of band (#798).

**Why this exists.** `guards._open_exposure_by_asset` builds rails 4/5/6's per-asset figure from
keel's own `orders` table. Sell a tranche on the venue by hand and keel still counts its BUY: the
rails keep vetoing entries against inventory that is gone, and nothing keel reads can learn
otherwise. #798's first proposal is an auditable operator path for exactly that, "written in a way
guard 6 actually reads (an order row, not only a `positions` mutation)".

**What it writes (plan R4).** ONE `orders` row -- `mode='live'`, `side='SELL'`,
`order_type='out_of_band'`, `status='filled'`, `confirmation='operator_declared'`, `qty` and
`filled_quantity` both the tranche's held qty, `actual_fill` the operator's `--price`, `fee` the
operator's `--fee`, `rule_id` the tranche's -- then the tranche's outcome through
`streak.record_closed_trade` (with `is_dca` from the tranche's OWN `rule_name`, so a DCA leg stays
out of rail 16's streak and a rule's loss counts toward it), then the tranche closes. THE ORDER
ROW IS THE POINT: it is what rails 4/5/6 (`_open_exposure_by_asset`), `executor._held_position`
and doctor's `ledger.drift` (`sleeve.orders_qty`) read. A `positions` mutation alone would move
the ledger and leave all three counting the BUY. `insert_order` audit-chains the row, so the
declaration is on the record with the rest of the order history.

**The SELL is sized from the LEDGER, never from the orders log (#900).** The tranche's own `qty`
is what a declared close books, on the order row and on the outcome alike. That keeps R33's rule
-- "a product whose net filled qty is `<= 0` releases all its exposure" -- true for as long as
the tranche and the BUY that opened it agree on a quantity, whichever of the two a later fix
corrects: a close that sold the orders log's figure instead would carry any ledger/orders
disagreement into the SELL row where no doctor check compares it.

**It places NOTHING, and it builds no broker.** It is bookkeeping of a sale that already
happened elsewhere; the venue is never asked, and the sell-side invariants
(`tests/execution/test_sell_side_invariants.py`) pin that no new placement path exists. Because
shrinking measured exposure buys the rails headroom for new ENTRIES, it is still a capability
increase: the CLI verb is typed-`yes` gated and has a row in `keel/capabilities.py`, and
`close_declared_position` is on S4's list of names `keel/web` and `keel/mcp` may never carry.

**Why it is not in `keel/commands/positions.py`.** That module's docstring says "NO CLOSE ACTION,
EVER": it is the read-only report the console renders, and a close verb living beside it would
put a write one import away from a browser surface. There is no `keel positions` report command
(plan R30); this group holds `close` and nothing else.

**LIVE profiles only.** A paper profile has no venue to have sold on, and a `mode='live'` SELL in
a paper database would sit in front of rails that never saw its BUY. Refused, not translated.

**The tranche's product needs a live BUY behind it too (H1).** `positions` has no `mode` column,
so a tranche opened while the profile was `paper` can still be sitting open, `qty > 0`, in a LIVE
database -- nothing on the row says which era opened it. `declared_close_target` refuses to book
a SELL when the product's LIVE net filled quantity (`sleeve.orders_qty`) is less than the
tranche's own `qty`: writing the SELL anyway would drive that net negative, and a negative net
reads the same as "nothing held" to `guards._open_exposure_by_asset` and
`executor._held_position` alike, hiding a LATER real live BUY on the same product from rails
4/5/6.
"""

from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from typing import Any

import click

from keel.commands._common import (
    _load_cfg,
    _open_repo,
    _require_interactive_confirmation,
    with_disclaimer,
)
from keel.config import Config
from keel.data.repository import Repository
from keel.execution import executor, sleeve, streak
from keel.types import Side

#: `orders.order_type` of a declared close. No placement path writes it, so a reader can tell a
#: row the venue executed for keel from one an operator reported after the fact.
DECLARED_ORDER_TYPE = "out_of_band"

#: `orders.confirmation` of a declared close -- who vouches for the row.
DECLARED_CONFIRMATION = "operator_declared"

#: The exit-state keys retired with a product's LAST tranche, exactly the set every other full
#: close clears (`agent._handle_exits`, `reconcile`'s bracket booking): left behind, they describe
#: a bracket for a position that no longer exists and poison the next trade in the product.
_OWNERSHIP_PREFIXES = ("position_rule:", "open_stop:", "open_target:", executor.UNBRACKETED_PREFIX)


class PositionCloseRefused(Exception):
    """The declared close cannot be recorded; the message is shown verbatim."""


def _open_tranche(repo: Repository, position_id: int) -> dict[str, Any] | None:
    return next((p for p in repo.get_open_positions() if p["id"] == position_id), None)


def declared_close_target(repo: Repository, config: Config, position_id: int) -> dict[str, Any]:
    """The OPEN tranche a declared close would book, or `PositionCloseRefused`.

    Split out so the CLI can show the operator the tranche they are about to close BEFORE the
    typed gate asks -- the gate's detail line is only worth reading if it names the real row.

    Also refuses (H1) when the product's LIVE net filled quantity in the orders log is less than
    this tranche's own `qty`. `positions` has no `mode` column: a tranche opened while the
    profile was `paper` can still sit `qty > 0` in a LIVE database, and nothing on the row itself
    says so. Booking a `mode='live'` SELL against it anyway would have no live BUY behind it,
    driving the product's live net negative -- and both `guards._open_exposure_by_asset` (nets
    per product, drops qty <= 0) and `executor._held_position` treat a non-positive net as no
    holding at all, hiding a LATER real live BUY on the same product from rails 4/5/6."""
    if config.auto_trade.mode == "paper":
        raise PositionCloseRefused(
            "a declared close records a sale made on the venue; this is a paper profile "
            "(auto_trade.mode = paper), which has no venue to have sold on"
        )
    position = _open_tranche(repo, position_id)
    if position is None:
        raise PositionCloseRefused(
            f"tranche {position_id} is not open (see the console's Positions view)"
        )
    product_id = position["product_id"]
    tranche_qty = position["qty"]
    live_qty = sleeve.orders_qty(repo, product_id, "live")
    if live_qty < tranche_qty:
        raise PositionCloseRefused(
            f"the live orders log holds only {live_qty} of {product_id}, less than the "
            f"tranche's {tranche_qty} -- recording this SELL would push the live net below "
            "zero and hide later buys from the exposure rails; check `keel doctor`'s "
            "`ledger.drift`"
        )
    return position


def _check_amounts(price: Decimal, fee: Decimal) -> None:
    if not price.is_finite() or price <= 0:
        raise PositionCloseRefused(f"--price must be a positive number, got {price}")
    if not fee.is_finite() or fee < 0:
        raise PositionCloseRefused(f"--fee must be zero or a positive number, got {fee}")


def close_declared_position(
    repo: Repository,
    config: Config,
    *,
    position_id: int,
    price: Decimal,
    fee: Decimal,
    now_ts: int,
) -> int:
    """Record tranche `position_id` as sold out of band at `price` for `fee`; return the new
    `orders.id`. Every refusal happens before anything is written.

    Only the NAMED tranche is booked -- not FIFO across the product the way `streak.book_exit`
    attributes an executed sale. An operator declaring a close knows which lot they sold; the
    rails need only the product's net quantity, which is the same either way.

    When the product keeps other open tranches, every exit-state key stays, because it describes
    those. That includes the `unbracketed:` retry record: its recorded `qty` is not what a retry
    places -- `reconcile.reconcile_unbracketed_positions` sizes each re-bracket from the TRANCHE
    it walks -- so closing one tranche leaves nothing in it to correct.
    """
    _check_amounts(price, fee)
    position = declared_close_target(repo, config, position_id)
    product_id = position["product_id"]
    qty = position["qty"]

    order_id = repo.insert_order(
        dict(
            mode="live",
            product_id=product_id,
            side=Side.SELL.value,
            order_type=DECLARED_ORDER_TYPE,
            qty=qty,
            status="filled",
            fee=fee,
            expected_fill=price,
            actual_fill=price,
            filled_quantity=qty,
            confirmation=DECLARED_CONFIRMATION,
            rule_id=position.get("rule_id"),
            created_at=now_ts,
            updated_at=now_ts,
        )
    )
    streak.record_closed_trade(
        repo,
        config,
        product_id=product_id,
        position=position,
        exit_fill=price,
        exit_qty=qty,
        fees=fee,
        is_dca=position["rule_name"] == "dca",
        now_ts=now_ts,
    )
    repo.close_position(position_id, closed_at=now_ts)

    if not repo.get_open_positions(product_id):
        for prefix in _OWNERSHIP_PREFIXES:
            repo.set_state(f"{prefix}{product_id}", None)
    return order_id


# -- the gated CLI verb ------------------------------------------------------------------------


def close_gate_wording(position: dict[str, Any], price: Decimal) -> tuple[str, str]:
    """The typed gate's action and detail for closing `position` at `price` -- one home, so the
    prompt and the tests that pin it cannot drift apart. The detail names the row (product, owning
    kind, held qty, entry) the operator is vouching for, and says outright that nothing is sold:
    the sale happened on the venue already, and this only tells keel so."""
    action = f"record tranche {position['id']} as closed out-of-band"
    detail = (
        f"{position['product_id']} tranche {position['id']} ({position['rule_name']}, qty "
        f"{position['qty']}, entry {position['entry_fill']}) is booked as SOLD at {price}. No "
        "order is placed. Rails 4/5/6 stop counting it, which frees room for new entries."
    )
    return action, detail


def positions_close_gate(
    repo: Repository,
    config: Config,
    *,
    position_id: int,
    price: Decimal,
    fee: Decimal,
    now_ts: int,
) -> int:
    """`keel positions close`'s typed gate AND the close it releases -- the capability row's call
    site. Refusals the service would make anyway come FIRST, so the operator is never asked to
    confirm a row that cannot be written; then a typed `yes` at a terminal; then the write.

    The gate and the effect share a function on purpose: `tests/web/test_server.py` derives the
    operations `keel/web` may never reach from the calls inside each gated function, and this is
    what puts `close_declared_position` in that derived set rather than only in S4's hand list."""
    _check_amounts(price, fee)
    position = declared_close_target(repo, config, position_id)
    _require_interactive_confirmation(*close_gate_wording(position, price))
    return close_declared_position(
        repo, config, position_id=position_id, price=price, fee=fee, now_ts=now_ts
    )


def _decimal(_ctx: click.Context, _param: click.Parameter, value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise click.BadParameter(f"not a number: {value!r}") from exc


@click.group("positions")
def positions_group() -> None:
    """Held tranches. `close` records a sale you made on the venue yourself (#798)."""


@positions_group.command("close")
@click.argument("position_id", type=int)
@click.option(
    "--price",
    required=True,
    callback=_decimal,
    help="The price the venue sold it at, per unit of base.",
)
@click.option(
    "--fee",
    default="0",
    show_default=True,
    callback=_decimal,
    help="The fee the venue charged for that sale, in quote currency.",
)
@click.pass_context
@with_disclaimer
def positions_close(ctx: click.Context, position_id: int, price: Decimal, fee: Decimal) -> None:
    """Record tranche POSITION_ID as sold out of band (dangerous: asks for confirmation).

    For a sale made on the venue by hand. It places NO order: it writes the SELL keel never saw,
    books the tranche's outcome, and closes it, so rails 4/5/6 stop counting inventory that is
    gone. Take the tranche id from the console's Positions view or `keel doctor`.
    """
    repo = _open_repo(ctx)
    config = _load_cfg(ctx)
    try:
        order_id = positions_close_gate(
            repo,
            config,
            position_id=position_id,
            price=price,
            fee=fee,
            now_ts=int(time.time()),
        )
    except PositionCloseRefused as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"recorded order {order_id}: tranche {position_id} closed out-of-band")
