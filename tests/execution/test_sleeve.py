"""`keel.execution.sleeve`: the ledger-side quantities `ledger.drift` compares (#799, plan R2)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.execution import sleeve
from keel.strategy.reduction import Holding, Lot, Reduction, SellCosts
from keel.types import Side


def test_ledger_qty_sums_what_is_still_held() -> None:
    rows = [{"qty": Decimal("0.0005")}, {"qty": Decimal("0.00049")}]
    assert sleeve.ledger_qty(rows) == Decimal("0.00099")


def test_ledger_qty_of_nothing_is_zero_not_none() -> None:
    assert sleeve.ledger_qty([]) == Decimal("0")


def _repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _order(*, mode: str, side: str, qty: Decimal, status: str = "filled") -> dict[str, Any]:
    return dict(
        mode=mode,
        product_id="BTC-USD",
        side=side,
        order_type="market",
        qty=qty,
        limit_price=Decimal("100000"),
        status=status,
        fee=Decimal("0"),
        expected_fill=Decimal("100000"),
        actual_fill=Decimal("100000"),
        created_at=0,
        updated_at=0,
    )


def test_orders_qty_reads_the_requested_mode_only() -> None:
    """#881: on a paper profile every fill is `mode='paper'`. `orders_qty` must be told which
    mode to read, unlike `executor._held_position`, which hardcodes `mode='live'` on purpose."""
    repo = _repo()
    repo.insert_order(_order(mode="paper", side=Side.BUY.value, qty=Decimal("0.001")))
    repo.insert_order(_order(mode="live", side=Side.BUY.value, qty=Decimal("5")))
    assert sleeve.orders_qty(repo, "BTC-USD", mode="paper") == Decimal("0.001")
    assert sleeve.orders_qty(repo, "BTC-USD", mode="live") == Decimal("5")


def test_orders_qty_nets_sells_and_floors_at_zero() -> None:
    repo = _repo()
    repo.insert_order(_order(mode="paper", side=Side.BUY.value, qty=Decimal("0.001")))
    repo.insert_order(_order(mode="paper", side=Side.SELL.value, qty=Decimal("0.002")))
    assert sleeve.orders_qty(repo, "BTC-USD", mode="paper") == Decimal("0")


def test_orders_qty_counts_what_the_venue_delivered_when_it_said() -> None:
    """#900: `filled_quantity` before `qty`, on both sides -- the reading R33
    (`guards._open_exposure_by_asset`) and `executor._held_position` use. A quote-sized BUY
    ordered 0.001 and delivered 0.00099 (fee out of the quote); a row the venue never sized
    counts its ordered `qty`."""
    repo = _repo()
    repo.insert_order(
        _order(mode="live", side=Side.BUY.value, qty=Decimal("0.001"))
        | {"filled_quantity": Decimal("0.00099")}
    )
    repo.insert_order(_order(mode="live", side=Side.BUY.value, qty=Decimal("0.002")))
    repo.insert_order(
        _order(mode="live", side=Side.SELL.value, qty=Decimal("0.0005"))
        | {"filled_quantity": Decimal("0.0004")}
    )
    assert sleeve.orders_qty(repo, "BTC-USD", mode="live") == Decimal("0.00259")


def test_orders_qty_counts_filled_rows_only() -> None:
    """A `pending` BUY is not a holding -- the same filter `_held_position` applies."""
    repo = _repo()
    repo.insert_order(_order(mode="live", side=Side.BUY.value, qty=Decimal("0.001")))
    repo.insert_order(_order(mode="live", side=Side.BUY.value, qty=Decimal("7"), status="pending"))
    assert sleeve.orders_qty(repo, "BTC-USD", mode="live") == Decimal("0.001")


# --- holding_of: the Holding over the positions ledger (#857, plan Task 5.4) ----------------


def test_holding_of_is_the_ledger_and_its_qty_is_doctors_ledger_qty() -> None:
    """Every OPEN tranche of the product, no rule filter (R8), oldest first -- the turtle row is
    inserted first but opened later, so the order is the ledger's FIFO, not insertion order. A
    closed tranche and another product's tranche are not part of it."""
    repo = _repo()
    turtle = repo.open_position(
        product_id="BTC-USD",
        rule_name="turtle_breakout",
        opened_at=2,
        qty=Decimal("0.0004"),
        entry_fill=Decimal("110000"),
        entry_fee=Decimal("0.5"),
    )
    dca = repo.open_position(
        product_id="BTC-USD",
        rule_name="dca",
        opened_at=1,
        qty=Decimal("0.0005"),
        entry_fill=Decimal("100000"),
        entry_fee=Decimal("0.45"),
    )
    closed = repo.open_position(
        product_id="BTC-USD",
        rule_name="dca",
        opened_at=0,
        qty=Decimal("1"),
        entry_fill=Decimal("1"),
        entry_fee=Decimal("0"),
    )
    repo.close_position(closed, closed_at=3)
    repo.open_position(
        product_id="PAXG-USD",
        rule_name="dca",
        opened_at=1,
        qty=Decimal("1"),
        entry_fill=Decimal("4000"),
        entry_fee=Decimal("0"),
    )

    held = sleeve.holding_of(repo, "BTC-USD", mark=Decimal("120000"))

    assert [(lot.position_id, lot.rule_name) for lot in held.lots] == [
        (dca, "dca"),
        (turtle, "turtle_breakout"),
    ]
    assert held.qty == sleeve.ledger_qty(repo.get_open_positions("BTC-USD"))
    assert held.qty == Decimal("0.0009")
    assert (held.product_id, held.mark) == ("BTC-USD", Decimal("120000"))


def test_holding_of_a_product_with_no_open_tranche_is_empty() -> None:
    held = sleeve.holding_of(_repo(), "BTC-USD")
    assert (held.lots, held.qty, held.vwae, held.mark) == ((), Decimal("0"), None, None)


# --- costs, rail-2 slicing, the sleeve caps and the one proposal writer (P7 Task 7.1) --------

D = Decimal
DAY = 86_400


def _config(taker: str = "0.012") -> Any:
    from keel_core.config import FeesConfig

    from tests.execution.test_executor import _config as executor_config

    return executor_config(fees=FeesConfig(taker_pct=D(taker)))


def test_slice_keeps_every_leg_under_the_per_order_cap_and_counts_the_days() -> None:
    """Rail 2 is a slicing obligation (spec §3.4, Q3): $450 of BTC at a $200 cap is 3 legs."""
    leg, legs = sleeve.slice_qty(
        D("0.0045"), D("100000"), max_per_order_usd=D("200"), base_increment=D("0.00000001")
    )
    assert (leg, legs) == (D("0.002"), 3)
    assert leg * D("100000") <= D("200")


def test_slice_floors_to_the_increment_and_reports_unexpressible_as_zero() -> None:
    assert sleeve.slice_qty(
        D("0.00123456789"), D("1"), max_per_order_usd=D("1000"), base_increment=D("0.0001")
    ) == (D("0.0012"), 1)
    assert sleeve.slice_qty(
        D("0.00001"), D("1"), max_per_order_usd=D("1000"), base_increment=D("0.0001")
    ) == (D("0"), 0)


def test_slice_without_an_increment_never_rounds_a_leg_over_the_cap() -> None:
    """`cap / price` does not terminate for 50 / 3, and the context's default HALF_EVEN rounds
    16.666...6667 UP -- a leg worth a hair over $50, which rail 2 would then veto. The cap
    division floors, so the leg is at or under the cap exactly."""
    leg, legs = sleeve.slice_qty(D("100"), D("3"), max_per_order_usd=D("50"), base_increment=None)
    assert leg * D("3") <= D("50")
    assert legs == 7  # 100 / 16.666... = 6.000...0x, so a seventh leg carries the remainder


def test_a_sale_under_the_cap_is_one_leg_of_its_own_size() -> None:
    assert sleeve.slice_qty(
        D("0.0004"), D("100000"), max_per_order_usd=D("200"), base_increment=None
    ) == (D("0.0004"), 1)


def _h(*opened_days: int) -> Holding:
    return Holding(
        "BTC-USD",
        tuple(
            Lot(i, "dca", d * DAY, D("0.001"), D("100000"), D("0"))
            for i, d in enumerate(opened_days)
        ),
    )


def _refusal(red: Reduction, holding: Holding, kind: str, **kw: Any) -> str | None:
    base: dict[str, Any] = dict(
        rule_params={}, dca_fires_today=False, last_rule_proposal_ts=None, now_ts=100 * DAY
    )
    base.update(kw)
    return sleeve.sleeve_refusal(reduction=red, holding=holding, rule_kind=kind, **base)


def test_min_hold_reads_the_consumed_tranches_not_the_newest() -> None:
    """R12: under a weekly DCA the newest tranche is always young; FIFO sells the OLD one."""
    red = Reduction("BTC-USD", D("0.001"), "reverse_dca", {}, D("110000"), 100 * DAY)
    assert _refusal(red, _h(10, 97), "reverse_dca") is None
    assert _refusal(red, _h(80, 97), "reverse_dca") == sleeve.MIN_HOLD


def test_min_hold_reaches_every_consumed_tranche_and_honours_the_rules_own_param() -> None:
    """A sale spilling into the young second lot is refused; a rule's `min_hold_days` replaces
    the default of 30 (which would refuse the 80-day-old lot below)."""
    spill = Reduction("BTC-USD", D("0.0015"), "reverse_dca", {}, D("110000"), 100 * DAY)
    assert _refusal(spill, _h(10, 97), "reverse_dca") == sleeve.MIN_HOLD
    red = Reduction("BTC-USD", D("0.001"), "reverse_dca", {}, D("110000"), 100 * DAY)
    assert _refusal(red, _h(80), "reverse_dca", rule_params={"min_hold_days": 10}) is None
    assert sleeve.DEFAULT_MIN_HOLD_DAYS == 30


def test_sleeve_exit_is_exempt_from_min_hold_but_not_from_the_dca_day() -> None:
    red = Reduction("BTC-USD", D("0.002"), "sleeve_exit", {}, D("60000"), 100 * DAY)
    assert _refusal(red, _h(97, 99), "sleeve_exit") is None
    assert _refusal(red, _h(97, 99), "sleeve_exit", dca_fires_today=True) == sleeve.SAME_DAY_DCA
    assert frozenset({"sleeve_exit"}) == sleeve.MIN_HOLD_EXEMPT_KINDS


def test_cooldown_is_the_rules_own_param() -> None:
    red = Reduction("BTC-USD", D("0.001"), "profit_take", {}, D("130000"), 100 * DAY)
    kw: dict[str, Any] = dict(rule_params={"cooldown_days": 30})
    assert _refusal(red, _h(10), "profit_take", last_rule_proposal_ts=80 * DAY, **kw) == (
        sleeve.COOLDOWN
    )
    assert _refusal(red, _h(10), "profit_take", last_rule_proposal_ts=70 * DAY, **kw) is None
    assert _refusal(red, _h(10), "profit_take", last_rule_proposal_ts=None, **kw) is None
    # No param, no cooldown: a proposal yesterday does not refuse a kind that declares none.
    assert _refusal(red, _h(10), "profit_take", last_rule_proposal_ts=99 * DAY) is None


def test_the_arbitration_order_is_the_specs_and_names_the_unbuilt_kinds() -> None:
    """Spec §3.6, fixed: rail 10's vocabulary and the order are decided once."""
    assert sleeve.ARBITRATION_ORDER == (
        "sleeve_exit",
        "reverse_dca",
        "profit_take",
        "band_rebalance",
        "rotation",
    )


def test_sell_costs_use_the_configured_fallback_never_a_literal() -> None:
    """The fee is `config.fees.taker_pct` whatever it is set to (0.9% is never hardcoded, spec
    §2.2), and the slippage is the ONE liquidity-scaled definition, `backtest_slippage`."""
    from keel.commands.rules import backtest_slippage

    repo = _repo()
    for taker in ("0.012", "0.0137"):
        costs = sleeve.sell_costs(repo, _config(taker), "BTC-USD")
        assert costs.fee_pct == D(taker)
        assert costs.fee_source == sleeve.FALLBACK_FEE_SOURCE == "fallback:config.fees.taker_pct"
        assert costs.slippage_pct == backtest_slippage(repo, "BTC-USD")[0]


def _proposal_kw(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        reduction=Reduction("BTC-USD", D("0.001"), "reverse_dca", {"k": 1}, D("110000"), 5),
        rule_id=7,
        rule_status="live",
        holding=Holding("BTC-USD", (Lot(1, "dca", 0, D("0.002"), D("100000"), D("0.60")),)),
        costs=SellCosts(D("0.012"), D("0.0005"), sleeve.FALLBACK_FEE_SOURCE),
        decision="preview",
        rails={"violations": []},
        expected_fee=D("1.32"),
        fee_source="venue_preview",
        legs=1,
        now_ts=9,
    )
    base.update(over)
    return base


def test_record_proposal_computes_gross_and_fifo_net() -> None:
    repo = _repo()
    pid = sleeve.record_proposal(repo, **_proposal_kw())
    row = repo.get_sell_proposal(pid)
    assert row is not None
    assert row["expected_gross"] == D("0.001") * D("110000") * (1 - D("0.0005"))
    # The consumed half of the lot: 0.001 @ 100000 plus half its $0.60 entry fee.
    assert row["expected_net_pnl"] == row["expected_gross"] - D("1.32") - (D("100") + D("0.30"))
    assert (row["product_id"], row["rule_id"], row["rule_kind"], row["rule_status"]) == (
        "BTC-USD",
        7,
        "reverse_dca",
        "live",
    )
    assert (row["qty"], row["expected_price"], row["legs"], row["ts"]) == (
        D("0.001"),
        D("110000"),
        1,
        9,
    )
    assert (row["vwae"], row["cost_basis"]) == (D("100300"), D("200.60"))
    assert (row["expected_fee"], row["fee_source"], row["decision"]) == (
        D("1.32"),
        "venue_preview",
        "preview",
    )
    assert (row["trigger"], row["rails"], row["superseded_by"]) == (
        {"k": 1},
        {"violations": []},
        None,
    )
    assert sleeve.proposed_today(repo, "BTC-USD", 9)


def test_a_proposal_over_an_empty_holding_records_no_net_rather_than_a_false_one() -> None:
    """NULL means NOT RECORDED (the table's rule): with no lot to consume, a net figure would be
    the gross less the fee against a zero basis -- a profit the ledger cannot show."""
    repo = _repo()
    pid = sleeve.record_proposal(repo, **_proposal_kw(holding=Holding("BTC-USD", ())))
    row = repo.get_sell_proposal(pid)
    assert row is not None
    assert (row["expected_net_pnl"], row["vwae"]) == (None, None)


def test_proposed_today_is_this_utc_day_only_and_ignores_superseded() -> None:
    """R14: one row per product per UTC day, whatever its decision, `superseded` excluded."""
    repo = _repo()
    now = 100 * DAY + 3600
    assert not sleeve.proposed_today(repo, "BTC-USD", now)
    sleeve.record_proposal(repo, **_proposal_kw(now_ts=100 * DAY - 1))  # yesterday
    sleeve.record_proposal(repo, **_proposal_kw(now_ts=now, decision="superseded"))
    assert not sleeve.proposed_today(repo, "BTC-USD", now)
    sleeve.record_proposal(
        repo,
        **_proposal_kw(
            now_ts=now,
            decision="vetoed",
            reduction=Reduction("PAXG-USD", D("1"), "reverse_dca", {}, D("4000"), now),
        ),
    )
    assert not sleeve.proposed_today(repo, "BTC-USD", now), "another product's row"
    sleeve.record_proposal(repo, **_proposal_kw(now_ts=100 * DAY, decision="vetoed"))
    assert sleeve.proposed_today(repo, "BTC-USD", now)


def test_last_proposal_ts_is_the_rules_newest_non_superseded_row() -> None:
    """R15's input: the pipeline enforces a rule's cooldown from the proposals table."""
    repo = _repo()
    assert sleeve.last_proposal_ts(repo, 7) is None
    assert sleeve.last_proposal_ts(repo, None) is None
    sleeve.record_proposal(repo, **_proposal_kw(now_ts=10))
    sleeve.record_proposal(repo, **_proposal_kw(now_ts=20, decision="vetoed"))
    sleeve.record_proposal(repo, **_proposal_kw(now_ts=30, decision="superseded"))
    sleeve.record_proposal(repo, **_proposal_kw(now_ts=40, rule_id=8))
    assert sleeve.last_proposal_ts(repo, 7) == 20
    assert sleeve.last_proposal_ts(repo, 8) == 40


def test_unverified_fill_orders_names_the_filled_buys_the_venue_never_sized() -> None:
    """P7 carried item c (#900): a tranche booked from a BUY with no `filled_quantity` carries
    the ORDERED size, which overstates the base by about the fee until the backfill runs."""
    repo = _repo()
    unsized = repo.insert_order(_order(mode="live", side=Side.BUY.value, qty=D("0.001")))
    repo.insert_order(
        _order(mode="live", side=Side.BUY.value, qty=D("0.001")) | {"filled_quantity": D("0.00099")}
    )
    repo.insert_order(_order(mode="live", side=Side.SELL.value, qty=D("0.001")))
    repo.insert_order(_order(mode="live", side=Side.BUY.value, qty=D("1"), status="pending"))
    repo.insert_order(_order(mode="paper", side=Side.BUY.value, qty=D("1")))
    assert sleeve.unverified_fill_orders(repo, "BTC-USD") == [unsized]
    assert sleeve.unverified_fill_orders(repo, "PAXG-USD") == []
