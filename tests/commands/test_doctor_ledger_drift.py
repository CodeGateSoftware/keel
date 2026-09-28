"""`ledger.drift` (#799 proposal 3) and `ledger.venue_drift` (#798): pure findings over plain
values. The wiring through `gather_findings` is tested in `test_doctor.py`."""

from __future__ import annotations

from decimal import Decimal

from keel.commands.doctor import OK, WARN, ledger_drift_findings


def _drift_parts(detail: str) -> list[str]:
    """The per-product clauses of a `ledger.drift` detail, before its trailing explanation."""
    return detail.split(" -- ")[0].split("; ")


def test_the_799_shape_a_filled_buy_with_no_tranche_warns() -> None:
    """#799 proposal 3: order id 4 filled 0.0132 PAXG and no positions row was written."""
    [f] = ledger_drift_findings(
        {"PAXG-USD": Decimal("0")}, {"PAXG-USD": Decimal("0.0132")}, {"PAXG-USD": None}
    )
    assert f.name == "ledger.drift" and f.status == WARN
    assert f.products == ("PAXG-USD",)
    assert _drift_parts(f.detail) == ["PAXG-USD: orders say 0.0132, ledger says 0"]


def test_a_product_only_the_orders_log_knows_still_drifts() -> None:
    """The ledger has NO key for a product whose fill never became a tranche -- that absence is
    the #799 shape itself, and must read as a ledger of zero, not be skipped."""
    [f] = ledger_drift_findings({}, {"PAXG-USD": Decimal("0.0132")}, {})
    assert f.status == WARN
    assert f.products == ("PAXG-USD",)


def test_only_the_drifting_products_are_named() -> None:
    [f] = ledger_drift_findings(
        {"BTC-USD": Decimal("0.001"), "PAXG-USD": Decimal("0")},
        {"BTC-USD": Decimal("0.001"), "PAXG-USD": Decimal("0.0132")},
        {"BTC-USD": None, "PAXG-USD": None},
    )
    assert f.products == ("PAXG-USD",)
    assert _drift_parts(f.detail) == ["PAXG-USD: orders say 0.0132, ledger says 0"]


def test_drift_within_one_base_increment_is_not_drift() -> None:
    [f] = ledger_drift_findings(
        {"BTC-USD": Decimal("0.00099")},
        {"BTC-USD": Decimal("0.000995")},
        {"BTC-USD": Decimal("0.00000001")},
    )
    assert f.status == WARN
    [g] = ledger_drift_findings(
        {"BTC-USD": Decimal("0.00099")},
        {"BTC-USD": Decimal("0.000990005")},
        {"BTC-USD": Decimal("0.00001")},
    )
    assert g.status == OK


def test_exactly_one_increment_of_drift_is_still_dust() -> None:
    """`>` not `>=`: one base increment is precisely the fee-in-base residue (#667)."""
    [f] = ledger_drift_findings(
        {"BTC-USD": Decimal("0.001")},
        {"BTC-USD": Decimal("0.00100001")},
        {"BTC-USD": Decimal("0.00000001")},
    )
    assert f.status == OK


def test_an_unknown_increment_uses_exact_equality() -> None:
    [f] = ledger_drift_findings({"X-USD": Decimal("1")}, {"X-USD": Decimal("1")}, {"X-USD": None})
    assert f.status == OK
    [g] = ledger_drift_findings(
        {"X-USD": Decimal("1")}, {"X-USD": Decimal("1.00000001")}, {"X-USD": None}
    )
    assert g.status == WARN


def test_nothing_to_compare_is_one_ok_finding_with_no_products() -> None:
    """The OK sentinel, so the name stays in the report on an empty deployment (#886)."""
    [f] = ledger_drift_findings({}, {}, {})
    assert (f.name, f.status, f.products) == ("ledger.drift", OK, ())


# -- ledger.venue_drift: the ledger against the venue's own holding (#798, plan R3) -------------


def _venue_parts(detail: str) -> list[str]:
    """The per-product clauses of a `ledger.venue_drift` detail, before its explanation."""
    return detail.split(" -- ")[0].split("; ")


def test_the_798_shape_the_ledger_holds_more_than_the_venue() -> None:
    """An out-of-band venue sale (#798): keel still counts the BUY, the venue does not."""
    from keel.commands.doctor import venue_drift_findings

    [f] = venue_drift_findings(
        {"BTC-USD": Decimal("0.001")},
        {"BTC-USD": {"total": "0.0004", "observed_at": 1_700_000_000}},
    )
    assert f.name == "ledger.venue_drift" and f.status == WARN
    assert f.products == ("BTC-USD",)
    assert _venue_parts(f.detail) == ["BTC-USD: ledger 0.001 > venue 0.0004 (observed 2023-11-14)"]


def test_the_venue_holding_more_than_the_ledger_is_not_this_finding() -> None:
    """Coins the operator bought outside keel are theirs, not drift."""
    from keel.commands.doctor import venue_drift_findings

    [f] = venue_drift_findings(
        {"BTC-USD": Decimal("0.001")}, {"BTC-USD": {"total": "0.002", "observed_at": 1}}
    )
    assert (f.status, f.products) == (OK, ())


def test_no_observation_reports_unknown_not_ok() -> None:
    from keel.commands.doctor import venue_drift_findings

    [f] = venue_drift_findings({"BTC-USD": Decimal("0.001")}, {})
    assert f.status == WARN
    assert f.products == ("BTC-USD",)
    assert _venue_parts(f.detail) == ["BTC-USD: no venue observation"]


def test_drifted_and_unobserved_products_are_each_named_once() -> None:
    from keel.commands.doctor import venue_drift_findings

    [f] = venue_drift_findings(
        {
            "BTC-USD": Decimal("0.001"),
            "ETH-USD": Decimal("1"),
            "PAXG-USD": Decimal("0.0132"),
        },
        {
            "BTC-USD": {"total": "0.001", "observed_at": 1_700_000_000},
            "PAXG-USD": {"total": "0", "observed_at": 1_700_000_000},
        },
    )
    assert f.products == ("ETH-USD", "PAXG-USD")
    assert _venue_parts(f.detail) == [
        "ETH-USD: no venue observation",
        "PAXG-USD: ledger 0.0132 > venue 0 (observed 2023-11-14)",
    ]


def test_venue_dust_within_one_base_increment_is_not_drift() -> None:
    """A venue that takes its fee in the base asset leaves the account one increment short of
    the ledger on every fill (#667) -- the same tolerance `ledger.drift` allows, and for the
    same reason. Unknown increment is exact comparison."""
    from keel.commands.doctor import venue_drift_findings

    ledger = {"BTC-USD": Decimal("0.001")}
    venue = {"BTC-USD": {"total": "0.00099999", "observed_at": 1}}
    [within] = venue_drift_findings(ledger, venue, increments={"BTC-USD": Decimal("0.00000001")})
    [unknown] = venue_drift_findings(ledger, venue, increments={"BTC-USD": None})
    assert within.status == OK
    assert unknown.status == WARN


def test_an_unparseable_venue_total_is_no_observation() -> None:
    """The record is JSON from `agent_state`; a total that is not a number says nothing about
    the holding, and must not raise out of doctor."""
    from keel.commands.doctor import venue_drift_findings

    [f] = venue_drift_findings(
        {"BTC-USD": Decimal("0.001")}, {"BTC-USD": {"total": "nope", "observed_at": 1}}
    )
    assert _venue_parts(f.detail) == ["BTC-USD: no venue observation"]


def test_nothing_held_is_one_ok_venue_finding() -> None:
    from keel.commands.doctor import venue_drift_findings

    [f] = venue_drift_findings({}, {"BTC-USD": {"total": "1", "observed_at": 1}})
    assert (f.name, f.status, f.products) == ("ledger.venue_drift", OK, ())


# -- #900: a fee-sized gap is a BUY booked at its ordered size, not a sale ----------------------

#: The three live BTC tranches #900 found: $50 each, fees $0.59/$0.59/$0.45, each booked at
#: quote / price. Summed in base, their fees are the gap a fee-in-quote venue leaves.
_BTC_FEE_BASE = Decimal("0.0000253")


def test_a_gap_within_the_open_tranches_fees_names_900_and_stays_a_warn() -> None:
    """#900's shape: the ledger holds quote / price per tranche, the venue delivered
    (quote - fee) / price. The gap is the fees' worth of base -- a booking error, not a sale
    made by hand, and the finding must not say "sale" alone. The TOLERANCE is untouched: still
    one increment, so this is still a WARN naming the product."""
    from keel.commands.doctor import venue_drift_findings

    [f] = venue_drift_findings(
        {"BTC-USD": Decimal("0.0021177")},
        {"BTC-USD": {"total": "0.0020930", "observed_at": 1_700_000_000}},
        increments={"BTC-USD": Decimal("0.00000001")},
        fee_base={"BTC-USD": _BTC_FEE_BASE},
    )
    assert (f.name, f.status, f.products) == ("ledger.venue_drift", WARN, ("BTC-USD",))
    assert _venue_parts(f.detail) == [
        "BTC-USD: ledger 0.0021177 > venue 0.0020930 (observed 2023-11-14), no more than the "
        "0.0000253 its open tranches paid in fees (#900)"
    ]


def test_a_gap_beyond_the_fees_keeps_the_sale_clause_and_still_names_900() -> None:
    """A gap larger than every fee paid is not explained by #900 alone, so its clause is the
    #798 one -- but the explanation lists fee-in-quote overstatement among the causes, because
    a sale on top of it is not the only possibility."""
    from keel.commands.doctor import venue_drift_findings

    [f] = venue_drift_findings(
        {"BTC-USD": Decimal("0.0021177")},
        {"BTC-USD": {"total": "0.0010000", "observed_at": 1_700_000_000}},
        fee_base={"BTC-USD": _BTC_FEE_BASE},
    )
    assert _venue_parts(f.detail) == [
        "BTC-USD: ledger 0.0021177 > venue 0.0010000 (observed 2023-11-14)"
    ]
    assert f.detail.split(" -- ", 1)[1] == (
        "an out-of-band sale or transfer (#798), a BUY booked at its ordered size though the "
        "venue took its fee out of the quote (#900), or a venue holding never observed; the "
        "rails still count what the ledger says"
    )


def test_fee_base_is_each_open_tranches_entry_fee_in_base() -> None:
    """`entry_fee / entry_fill` per open tranche, summed per product: the base a fee-in-quote
    venue withheld from each BUY. A tranche with no recorded fee or price adds nothing -- NULL
    is "not recorded", never zero, and never a guess."""
    from keel.commands.doctor import open_tranche_fee_base

    assert open_tranche_fee_base(
        [
            {"product_id": "BTC-USD", "entry_fee": Decimal("0.45"), "entry_fill": Decimal("45000")},
            {"product_id": "BTC-USD", "entry_fee": Decimal("0.9"), "entry_fill": Decimal("90000")},
            {"product_id": "PAXG-USD", "entry_fee": None, "entry_fill": Decimal("4673.23")},
            {"product_id": "ETH-USD", "entry_fee": Decimal("0.5"), "entry_fill": None},
        ]
    ) == {"BTC-USD": Decimal("0.00002")}
