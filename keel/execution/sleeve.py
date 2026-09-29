"""The DCA sleeve's ledger-side logic (docs/superpowers/specs/2026-09-28-dca-sleeve-sell-side-
design.md §3.3).

THE `positions` LEDGER IS THE TRUTH FOR WHAT IS HELD, LOT BY LOT. `executor._held_position` derives
a quantity and an average from `orders`, and the rails size from that. The two legitimately
disagree only by dust. Beyond one base increment they are telling different stories: #799's
filled entry with no tranche (the orders say more), or #798's out-of-band sale (both say more than
the venue). Each is a doctor finding (`ledger.drift`, `ledger.venue_drift`), never an assumption
a sale is sized from.

Nothing here holds a broker: these are the quantities doctor compares, and doctor is pinned
read-only and broker-free (`gather_findings`). Everything is read-only but ONE writer,
`record_proposal`, which is the only function in keel that writes a `sell_proposals` row for the
sell pipeline (`executor.reduce` records through it; P8's arbitration will too).

**The sell pipeline's shared policy (P7, plan R11-R15, spec §3.4 and §3.6).** One module, so
every sleeve-sell kind, the live cycle and the sim size and refuse by one definition:

- **R11, rail 2 is a slicing obligation, and the slicer is here, not in each rule.**
  `slice_qty` floors to the venue's `base_increment` and caps each leg at `max_per_order_usd`;
  it returns the leg and how many legs (one per cycle, so days) the sale needs. A rule cannot
  know the increment or the config cap, and a rule's `qty` is an upper bound: the slicer only
  ever lowers it. The cap division FLOORS, so a leg is never rounded a hair over the cap and
  then vetoed by the very rail it was sliced for.
- **R12, `min_hold_days` reads the tranches the FIFO sale would CONSUME,** not the product's
  newest. Under a weekly DCA the newest tranche is always young, so the spec's literal "after
  the newest tranche" would refuse every distribution forever; its stated purpose -- never
  realise a tranche bought this week -- is about the consumed ones.
- **R13, `sleeve_exit` is exempt from `min_hold_days`** (`MIN_HOLD_EXEMPT_KINDS`): a
  whole-sleeve exit consumes this week's tranche by definition. The same-day-DCA refusal and
  the one-per-day cap still apply to it.
- **R14, "one sleeve SELL per product per UTC day" is one `sell_proposals` row per product per
  UTC day, whatever its decision, `superseded` rows excluded** (`proposed_today`). The
  LaunchAgent retries hourly after a non-zero exit; a proposal per run would spam the operator.
- **R15, a rule's `cooldown_days` is enforced here, from the rule's last proposal that could
  have BECOME a sale** -- decision `preview` or `placed` only (`last_proposal_ts`, #915). A rule
  has no state; the proposals table is the state. `vetoed` (the cooldown's own refusal, or any
  other sleeve cap), `declined` and `failed` rows are deliberately EXCLUDED: a rule that fires
  every day writes a fresh `vetoed` row at `now_ts` each time it is refused, and reading those
  would let a veto re-arm the very cooldown that produced it, so the cooldown would never expire.
- **§3.6, arbitration is a fixed order** (`ARBITRATION_ORDER`), not configurable. The kinds that
  are NOT built (`band_rebalance`, `rotation`; spec §5, §8) are named so rail 10's vocabulary
  and the order are decided once.

**Fees are never a literal (spec §2.2, Q5).** `sell_costs` prices at `config.fees.taker_pct`
and labels it `FALLBACK_FEE_SOURCE`; the venue's previewed commission, when there is one,
replaces it on the proposal (`VENUE_FEE_SOURCE`), and that is decided in `executor.reduce`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from typing import Any

from keel.data.repository import Repository
from keel.strategy.reduction import Holding, Reduction, SellCosts
from keel.types import Side

#: How a proposal's fee was priced when the venue did not quote one (spec §2.2).
FALLBACK_FEE_SOURCE = "fallback:config.fees.taker_pct"
#: The venue's own previewed commission (`Preview.est_fee`) -- the fact, when there is one.
VENUE_FEE_SOURCE = "venue_preview"

#: `sleeve_refusal`'s answers, which P8 records as `rails.sleeve` on a vetoed proposal.
SAME_DAY_DCA = "same_day_dca"
MIN_HOLD = "min_hold_days"
COOLDOWN = "cooldown_days"

DEFAULT_MIN_HOLD_DAYS = 30
#: R13: kinds whose sale consumes this week's tranche by definition.
MIN_HOLD_EXEMPT_KINDS = frozenset({"sleeve_exit"})
#: Spec §3.6, fixed and not configurable. `band_rebalance`/`rotation` are NOT built (spec §5,
#: §8); they are named so rail 10's vocabulary and this order are decided once.
ARBITRATION_ORDER = ("sleeve_exit", "reverse_dca", "profit_take", "band_rebalance", "rotation")

_DAY = 86_400


def ledger_qty(positions: Iterable[dict[str, Any]]) -> Decimal:
    """Sum of `qty` over the given OPEN tranches -- what is still held. `Holding.qty` is
    defined as exactly this, and a test pins the two equal (`holding_of`).

    `qty` on an open tranche is what REMAINS after any scale-out (the sold part moves to
    `realized_qty`), so the sum is the held quantity, not the quantity ever bought."""
    return sum((Decimal(p["qty"]) for p in positions), Decimal("0"))


def orders_qty(repo: Repository, product_id: str, mode: str) -> Decimal:
    """Net filled BUY - SELL qty for `product_id`, from `orders` tagged `mode` (#881).

    Mirrors `executor._held_position`'s arithmetic, but is NOT `_held_position`: that function
    hardcodes `mode="live"` deliberately (`agent._book_paper_exit`'s docstring -- the live exit
    path is unreachable from paper BY CONSTRUCTION, not by accident). `ledger.drift` is a
    read-only doctor comparison, not a placement path, and it must compare the ledger against
    the SAME mode's orders: on a paper profile every fill is `mode="paper"`, and comparing it
    against `_held_position`'s always-empty `mode="live"` total would WARN on every open paper
    tranche as though it were #799's stranded fill.

    Floored at zero, as `_held_position` is: a net short is not a holding keel models.

    Each row counts what the venue DELIVERED (`filled_quantity`) when it said, its ordered `qty`
    otherwise (#900) -- the same reading as `_held_position` and R33
    (`guards._open_exposure_by_asset`). A tranche is booked at that same delivered figure
    (`agent._open_tranche`), and `keel positions close` writes `qty == filled_quantity` from the
    tranche, so a product whose every tranche is closed nets to exactly zero here, and R-f
    (`declared_close_target`) compares a tranche against the figure it was booked from. Reading
    `qty` alone would leave the fee's worth of a quote-sized BUY as a phantom holding.
    """
    buy_qty = Decimal("0")
    sell_qty = Decimal("0")
    for order in repo.get_orders(mode=mode, product_id=product_id, status="filled"):
        qty = order.get("filled_quantity") or order["qty"] or Decimal("0")
        if order["side"] == Side.BUY.value:
            buy_qty += qty
        elif order["side"] == Side.SELL.value:
            sell_qty += qty
    net = buy_qty - sell_qty
    return net if net > 0 else Decimal("0")


def holding_of(repo: Repository, product_id: str, mark: Decimal | None = None) -> Holding:
    """The `Holding` a sleeve sale is proposed from: every OPEN `positions` tranche of
    `product_id`, oldest first (spec §3.3).

    **No `rule_name` filter (plan R8).** A sleeve-sell rule governs the product's holding, not
    its latest entrant, so a mixed-ownership product (PAXG: turtle tranche 3 is the oldest row)
    has one `Holding`, and a FIFO sale reaches the turtle tranche first. That leg is booked
    honestly, as a rule outcome, by `streak.book_exit(is_dca=None)` (R9, spec Q10).

    **Order is `get_open_positions`' FIFO contract**, kept as is by `Holding.from_rows`, so the
    tranches a preview names are the ones `book_exit` will consume. Its `qty` is `ledger_qty` over
    the same rows -- the quantity `ledger.drift` compares -- never `orders`.

    **It can overstate the venue by about a fee (#900).** Since #905 a tranche is booked at the
    venue-filled size, but the live tranches booked before it still carry the ORDERED size until a
    backfill runs, so for those `qty` exceeds the base the venue delivered by roughly the fee. This
    does not clamp to the venue (it holds no broker, like the rest of this module): the executor
    clamps every SELL to the venue's holding (`_clamped_sell_qty`), and `ledger.venue_drift`
    reports the gap before a sale is proposed.
    """
    return Holding.from_rows(product_id, repo.get_open_positions(product_id), mark)


def unverified_fill_orders(repo: Repository, product_id: str) -> list[int]:
    """The ids of `product_id`'s unsized filled LIVE BUYs that could have booked a lot the
    product STILL HOLDS open (#912).

    **Every filled live BUY is a candidate owner, sized or not.** `agent._open_tranche` books a
    tranche at `executor.delivered_qty(order) or order["qty"]` -- the venue's `filled_quantity`
    when it gave one, the ordered `qty` otherwise -- so a SIZED order owns a lot exactly as much
    as an unsized one; only its booked size differs. (`delivered_qty` is spelled out here as
    `order.get("filled_quantity") or order["qty"]` rather than imported: `executor` already
    imports this module, so the reverse import would cycle. The two reads agree -- `delivered_qty`
    returns `filled_quantity` only when it is not `None` and `> 0`, exactly what `or` gives for a
    `Decimal`.) Leaving sized orders out of the candidate pool -- the pre-#912-fix-up reading --
    lets an unsized order claim a SIZED fill's tranche merely because their ordered `qty` happens
    to match, which is wrong: that lot already has an owner, and the venue told us its size
    directly.

    **Each open lot has exactly one owner: the candidate closest before it opened.** A lot's
    ORIGINAL size is `qty + realized_qty` -- a partial exit only ever lowers `qty` and raises
    `realized_qty`, and a NULL `realized_qty` already reads as zero (`_position_row_to_dict`), so
    this is what the lot was booked at even after a scale-out shrank `qty`. A candidate matches a
    lot when its booked size equals that original size and the lot opened no earlier than the
    order and no more than one day after it (`0 <= lot.opened_at - order.created_at <= 86_400`):
    the deployment cycles once a UTC day, and the LaunchAgent's hourly retries after a non-zero
    exit land inside that same day. Candidates are walked closest-created_at-first (ties broken
    by the higher order id), and each claims the first still-unclaimed matching lot it finds --
    so the order nearest a lot's `opened_at` gets first claim on it. This is why "oldest order
    first" is wrong: a DCA that buys the same size on consecutive days has an order from
    YESTERDAY that is within the one-day window of TODAY's lot too, and oldest-first would let it
    claim today's lot before today's own, closer order is even considered -- leaving today's
    order to wrongly name a different, stale lot, or none.

    `positions` does not name its entry order (no backfill exists yet, #900 open), so none of
    this can say for CERTAIN which open lot a given order booked -- but it can rule out orders
    that could not have booked ANY open lot (a tranche closes, its entry order does not stop
    being unsized) and orders a sized fill's match already accounts for, which the naive "every
    unsized BUY the product ever had" reading does neither.

    Only UNSIZED owners are named in the RESULT (`filled_quantity is None`): a sized order's fill
    is already observed and needs no verification -- but it still competes for lots like any
    other candidate, which is the whole point of including it.
    """
    lots = [
        (int(p["id"]), p["qty"] + p["realized_qty"], int(p["opened_at"]))
        for p in repo.get_open_positions(product_id)
    ]
    candidates = sorted(
        (
            order
            for order in repo.get_orders(mode="live", product_id=product_id, status="filled")
            if order["side"] == Side.BUY.value
        ),
        key=lambda order: (int(order["created_at"]), int(order["id"])),
        reverse=True,
    )
    claimed: set[int] = set()
    owners: list[dict[str, Any]] = []
    for order in candidates:
        order_qty = order.get("filled_quantity") or order["qty"]
        created_at = int(order["created_at"])
        for lot_id, original_size, opened_at in lots:
            if lot_id in claimed:
                continue
            if original_size == order_qty and 0 <= opened_at - created_at <= _DAY:
                claimed.add(lot_id)
                owners.append(order)
                break
    return sorted(int(order["id"]) for order in owners if order.get("filled_quantity") is None)


def sell_costs(repo: Repository, config: Any, product_id: str) -> SellCosts:
    """The rates a sleeve rule sizes against (R6): `config.fees.taker_pct`, labelled as the
    fallback, and the product's one liquidity-scaled slippage (`backtest_slippage`)."""
    # Lazy, as `doctor.gather_findings` imports it: keeps click out of the cycle's import graph.
    from keel.commands.rules import backtest_slippage

    slippage, _measured = backtest_slippage(repo, product_id)
    return SellCosts(config.fees.taker_pct, slippage, FALLBACK_FEE_SOURCE)


def slice_qty(
    qty: Decimal, price: Decimal, *, max_per_order_usd: Decimal, base_increment: Decimal | None
) -> tuple[Decimal, int]:
    """`(leg_qty, legs)`: one leg at or under `max_per_order_usd`, floored to `base_increment`,
    and how many such legs sell `qty` (R11). `(0, 0)` means no leg can be expressed.

    `base_increment=None` means unknown, and the leg is sent unquantized -- `_order_spec`'s rule
    for a SELL (#516), never a refusal.

    **Without an increment, legs are counted as `ceil(qty * price / max_per_order_usd)`, not by
    dividing `qty` by the leg.** `cap_qty = max_per_order_usd / price` is itself a FLOORED,
    truncated decimal (28 significant digits) whenever the division does not terminate -- `50 /
    3` never does -- so it is already a hair under the true cap. Re-dividing `qty` by that
    already-short leg compounds the shortfall into ~1e-26 units of phantom remainder, which
    `ROUND_CEILING` then turns into a whole extra leg even when `qty` is an EXACT multiple of the
    cap ($300 at a $50 cap wants 6 legs, not 7). Computing legs straight from `qty * price` over
    the cap has no such intermediate rounding to compound. This does not apply once
    `base_increment` floors `qty` and `leg` to whole increments below: there the FLOORED
    remainder is real dust the venue cannot express, not an artefact of this function's own
    arithmetic, so `sellable / leg` is exactly what R11 means by "how many legs".
    """
    with localcontext() as ctx:
        # FLOOR, not the default HALF_EVEN: `50 / 3` would round UP to a leg worth a hair over
        # the cap, which rail 2 then vetoes -- the one outcome slicing exists to prevent.
        ctx.rounding = ROUND_FLOOR
        cap_qty = max_per_order_usd / price
    sellable = qty
    leg = min(qty, cap_qty)
    if base_increment is not None and base_increment > 0:
        increment = base_increment

        def _floor(value: Decimal) -> Decimal:
            return (value / increment).to_integral_value(rounding=ROUND_FLOOR) * increment

        # Legs are counted over what CAN be sold: a remainder under one increment is dust the
        # venue cannot express, and a leg for it would be a day the sale never completes.
        sellable = _floor(qty)
        leg = _floor(leg)
        if leg <= 0:
            return Decimal("0"), 0
        legs = int((sellable / leg).to_integral_value(rounding=ROUND_CEILING))
    else:
        if leg <= 0:
            return Decimal("0"), 0
        # No increment to floor against, so `sellable / leg` is not used here (see the docstring
        # above): count legs straight from `qty * price` over the cap instead.
        with localcontext() as ctx:
            ctx.rounding = ROUND_CEILING
            legs = int((qty * price / max_per_order_usd).to_integral_value(rounding=ROUND_CEILING))
        legs = max(legs, 1)
    return leg, legs


def proposed_today(repo: Repository, product_id: str, now_ts: int) -> bool:
    """R14: whether `product_id` already has a non-superseded proposal this UTC day."""
    start = now_ts - now_ts % _DAY
    return any(
        p["decision"] != "superseded"
        for p in repo.get_sell_proposals(product_id=product_id, since_ts=start)
    )


#: R15's cooldown reads only these decisions (#915): a proposal that could have BECOME a sale.
_COOLDOWN_DECISIONS = frozenset({"preview", "placed"})


def last_proposal_ts(repo: Repository, rule_id: int | None) -> int | None:
    """R15: the `ts` of rule `rule_id`'s newest `preview` or `placed` proposal, or `None`
    (#915).

    Deliberately NOT every non-superseded row: `vetoed` (whether from the cooldown itself or
    any other sleeve cap), `declined` and `failed` are refusals, not proposals a sale might
    have followed from. A rule that fires every cycle writes a FRESH `vetoed` row at `now_ts`
    each time it is refused, so counting those would let a veto re-arm its own cooldown --
    the cooldown would never expire.
    """
    if rule_id is None:
        return None
    rows = [
        p for p in repo.get_sell_proposals(rule_id=rule_id) if p["decision"] in _COOLDOWN_DECISIONS
    ]
    return int(rows[0]["ts"]) if rows else None


def _refusal_reason(proposal: Mapping[str, Any]) -> tuple[Any, ...]:
    """What a proposal was refused FOR, as a comparable key: its kind, its sleeve cap, and the
    NAMES of the rails that vetoed it -- each violation's text up to its first colon, the
    `name: detail` convention every rail in `guards.py` writes. The detail carries live numbers
    (a feed's age), so comparing whole strings would call a stale feed two days running news."""
    rails = proposal.get("rails") or {}
    return (
        proposal.get("rule_kind"),
        rails.get("sleeve"),
        tuple(sorted({str(v).split(":", 1)[0].strip() for v in rails.get("violations") or []})),
    )


def repeats_previous_refusal(repo: Repository, product_id: str, proposal_id: int | None) -> bool:
    """Whether proposal `proposal_id` is a REFUSAL that repeats its product's previous proposal:
    both `vetoed`, by the same kind, for the same reason (`_refusal_reason`). The P9 ruling on
    P8's `sleeve.proposal`: a 30-day cooldown vetoing daily sent 29 alerts in a row.

    The row is still recorded every day -- it is the audit trail, and R14/R15 read it -- and
    this answers only whether it is NEWS. "Previous" is the product's newest non-`superseded`
    proposal before this one (`get_sell_proposals`' own newest-first order), whatever its rule,
    so the first veto after a preview, a change of reason, and the preview after a run of vetoes
    are all transitions and all notify. A `preview` is never a repeat: each is a distinct sale
    the operator acts on by its own id.

    **No state beyond the proposals table.** The notification layer still writes exactly one key
    (R19): the table this reads is the one `executor.reduce` and `_handle_reductions` already
    write, so remembering what was said costs nothing new.

    Fails OPEN: an id the product does not have reads as news (`False`), never as silence.
    """
    if proposal_id is None:
        return False
    rows = [
        p for p in repo.get_sell_proposals(product_id=product_id) if p["decision"] != "superseded"
    ]
    position = next((i for i, p in enumerate(rows) if p["id"] == proposal_id), None)
    if position is None or position + 1 >= len(rows):
        return False
    current, previous = rows[position], rows[position + 1]
    return (
        current["decision"] == "vetoed"
        and previous["decision"] == "vetoed"
        and _refusal_reason(current) == _refusal_reason(previous)
    )


def sleeve_refusal(
    *,
    reduction: Reduction,
    holding: Holding,
    rule_kind: str,
    rule_params: Mapping[str, Any],
    dca_fires_today: bool,
    last_rule_proposal_ts: int | None,
    now_ts: int,
) -> str | None:
    """The sleeve cap that refuses this `Reduction`, or `None` (spec §3.4; R12, R13, R15).

    Checked in the pipeline BEFORE an intent is built, so a refusal here costs nothing at the
    venue. Returns `SAME_DAY_DCA`, `MIN_HOLD` or `COOLDOWN`; R14's one-per-day cap is
    `proposed_today`, read by the caller before a rule is even asked.
    """
    if dca_fires_today:
        return SAME_DAY_DCA
    if rule_kind not in MIN_HOLD_EXEMPT_KINDS:
        min_hold = int(rule_params.get("min_hold_days", DEFAULT_MIN_HOLD_DAYS))
        if any(
            now_ts - lot.opened_at < min_hold * _DAY for lot, _ in holding.fifo_legs(reduction.qty)
        ):
            return MIN_HOLD
    cooldown = int(rule_params.get("cooldown_days", 0))
    if (
        cooldown
        and last_rule_proposal_ts is not None
        and now_ts - last_rule_proposal_ts < cooldown * _DAY
    ):
        return COOLDOWN
    return None


def record_proposal(
    repo: Repository,
    *,
    reduction: Reduction,
    rule_id: int | None,
    rule_status: str,
    holding: Holding,
    costs: SellCosts,
    decision: str,
    rails: dict[str, Any],
    expected_fee: Decimal,
    fee_source: str,
    legs: int,
    now_ts: int,
    superseded_by: str | None = None,
) -> int:
    """THE one proposal writer (spec §3.8): one `sell_proposals` row for `reduction`.

    `expected_gross` is the leg's notional less slippage; `expected_net_pnl` is that less the
    fee and the FIFO, fee-inclusive basis of the units the sale consumes (`Holding.fifo_cost`).
    With no lot to consume, the net is NULL -- not recorded -- rather than a gross against a
    zero basis, which would read as a profit the ledger cannot show. The repository refuses a
    key that is not a column and a decision outside `SELL_PROPOSAL_DECISIONS`.
    """
    gross = reduction.qty * reduction.expected_price * (Decimal("1") - costs.slippage_pct)
    consumed = holding.fifo_legs(reduction.qty)
    net = gross - expected_fee - holding.fifo_cost(reduction.qty) if consumed else None
    return repo.insert_sell_proposal(
        dict(
            ts=now_ts,
            product_id=reduction.product_id,
            rule_id=rule_id,
            rule_kind=reduction.reason,
            rule_status=rule_status,
            qty=reduction.qty,
            expected_price=reduction.expected_price,
            vwae=holding.vwae,
            cost_basis=holding.cost_basis,
            expected_gross=gross,
            expected_fee=expected_fee,
            fee_source=fee_source,
            expected_net_pnl=net,
            legs=legs,
            trigger=reduction.trigger,
            rails=rails,
            decision=decision,
            superseded_by=superseded_by,
        )
    )
