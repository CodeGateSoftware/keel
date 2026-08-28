"""The 2026-09-30 pooled forward-trades review (#427, tracked in #353) — the pure seams.

The review was reframed as DESCRIPTIVE (#359's corrected recommendation): n_eff-corrected
intervals via `keel/research/significance.py`, and the sentence — "at this n_eff ... this
review can only see an edge of X points or larger" — printed in the report itself, where it
cannot be omitted. These tests pin the three things the issue says make the report honest:

1. **The sentence, exactly.** At n=100 pooled it must say "20.0 points", the display the
   discussion's corrected comment uses (`detectable_edge(n_eff(100))` = 0.19955... -> one
   decimal in points), not the raw 19.955 and not the PRD's at-39 table value 19.9 — the
   sentence is computed at the ACTUAL n_eff of the pool, which is the whole point of #427.
2. **The extraction mapping.** orders/trade_outcomes rows -> the `OutcomeRow` sequence
   `significance()` reads: win/loss by the SIGN of fee-honest net pnl, scratch (exactly 0)
   and open trades excluded but counted, FIFO matching, ledger dedup, streak's own pnl
   formula for orders-derived round trips.
3. **The refusal and the no-verdict shape.** Zero pooled trades refuses ("nothing to
   review") instead of emitting a degenerate report, and a real report NEVER carries a
   pass/fail verdict on the edge — the verdict vocabulary is about POWER.
"""

from __future__ import annotations

from decimal import Decimal

from keel.research.pooled_review import (
    EVENT_DATE,
    DescriptiveReview,
    LedgerRow,
    OrderRow,
    OrdersRead,
    PooledSample,
    build_sample,
    descriptive_review,
    is_refused,
    ledger_round_trips,
    power_sentence,
    render_report,
    round_trip_pnl,
    round_trips_from_orders,
)
from keel.research.throughput import design_effect, detectable_edge, n_eff


def _order(
    order_id: int,
    product_id: str,
    side: str,
    qty: str,
    fill: str | None,
    fee: str | None,
    rule_id: int = 1,
    mode: str = "paper",
    status: str = "filled",
    created_at: int = 1_000,
) -> OrderRow:
    """One `orders` row in the shape the read-only driver reads (money already Decimal)."""
    return OrderRow(
        id=order_id,
        mode=mode,
        product_id=product_id,
        side=side,
        qty=Decimal(qty),
        status=status,
        actual_fill=None if fill is None else Decimal(fill),
        fee=None if fee is None else Decimal(fee),
        rule_id=rule_id,
        created_at=created_at,
    )


def _ledger(
    product_id: str,
    qty: str,
    entry: str,
    exit_fill: str,
    fees: str,
    pnl_net: str,
    rule_name: str = "turtle_breakout",
    opened_at: int = 1_000,
    closed_at: int = 2_000,
) -> LedgerRow:
    """One `trade_outcomes` row (the closed-trade ledger rails 11/16 read)."""
    return LedgerRow(
        product_id=product_id,
        rule_name=rule_name,
        opened_at=opened_at,
        closed_at=closed_at,
        qty=Decimal(qty),
        entry_fill=Decimal(entry),
        exit_fill=Decimal(exit_fill),
        fees=Decimal(fees),
        pnl_net=Decimal(pnl_net),
    )


def _empty_read() -> OrdersRead:
    return OrdersRead(trips=[], open_buys=0, unfilled_orders=0, stray_sells=0)


# -- the sentence: the one line the report must never omit (#427, #359's correction) --------


def test_power_sentence_at_n_100_matches_the_published_display() -> None:
    # n_eff(100) = 38.8340... displayed to 0.01; detectable_edge at THAT n_eff is
    # 0.19955... -> "20.0 points" at one decimal in points -- the discussion's corrected
    # comment displays exactly this, and the sentence must scale with the actual n.
    assert power_sentence(100) == (
        "at this n_eff (38.83 effective of 100 pooled), this review can only see "
        "an edge of 20.0 points or larger (80% power, one-sided 5%)"
    )


def test_power_sentence_at_small_n_states_the_honest_tiny_power() -> None:
    # 12 pooled -> n_eff 4.6599... -> edge sqrt(1.5464/4.6599) = 0.5761... -> 57.6 points.
    # A small pool must produce a MORE honest sentence, never a silently weaker one.
    assert power_sentence(12) == (
        "at this n_eff (4.66 effective of 12 pooled), this review can only see "
        "an edge of 57.6 points or larger (80% power, one-sided 5%)"
    )


def test_power_sentence_numbers_come_from_throughput_not_a_local_copy() -> None:
    # The sentence's arithmetic IS throughput.py's -- if the module drifts, the sentence
    # drifts with it rather than disagreeing with the power table it cites.
    for n in (7, 25, 100, 259):
        effective = n_eff(Decimal(n))
        edge_points = (detectable_edge(effective) * Decimal(100)).quantize(Decimal("0.1"))
        assert f"{edge_points} points" in power_sentence(n)
        assert f"{effective.quantize(Decimal('0.01'))} effective of {n} pooled" in power_sentence(n)


# -- the pnl formula: the ledger writer's own, reused for orders-derived round trips --------


def test_round_trip_pnl_is_the_streak_formula_net_of_both_legs() -> None:
    # (exit - entry) * qty - entry fee - exit fee -- keel/execution/streak.py's
    # record_closed_trade arithmetic, so a reconstructed round trip and a ledger row of the
    # same fills price identically.
    assert round_trip_pnl(
        entry_fill=Decimal("100"),
        exit_fill=Decimal("110"),
        qty=Decimal("2"),
        entry_fee=Decimal("0.12"),
        exit_fee=Decimal("0.13"),
    ) == Decimal("19.75")


# -- extraction: orders rows -> closed round trips, oldest-first FIFO ------------------------


def test_orders_match_fifo_within_product_rule_mode_and_qty() -> None:
    orders = [
        _order(1, "BTC-USD", "BUY", "1", "100", "0.12", created_at=1_000),
        _order(2, "ETH-USD", "BUY", "1", "50", "0.06", rule_id=2, created_at=1_100),
        _order(3, "BTC-USD", "SELL", "1", "120", "0.14", created_at=1_200),
        _order(4, "ETH-USD", "SELL", "1", "40", "0.05", rule_id=2, created_at=1_300),
    ]
    read = round_trips_from_orders("keel.db", orders, {1: "turtle_breakout", 2: "dca"})
    assert len(read.trips) == 2
    btc = next(t for t in read.trips if t.product_id == "BTC-USD")
    assert btc.pnl_net == (Decimal("120") - Decimal("100")) * Decimal("1") - Decimal("0.26")
    assert btc.rule == "turtle_breakout"
    assert btc.mode == "paper"
    assert btc.source == "orders"
    eth = next(t for t in read.trips if t.product_id == "ETH-USD")
    assert eth.rule == "dca"
    assert read.open_buys == 0
    assert read.unfilled_orders == 0
    assert read.stray_sells == 0


def test_first_buy_matches_first_sell_when_quantities_repeat() -> None:
    # Two round trips of the same size: FIFO by order id, oldest buy to oldest sell.
    orders = [
        _order(1, "BTC-USD", "BUY", "1", "100", "0.12", created_at=1_000),
        _order(2, "BTC-USD", "BUY", "1", "200", "0.24", created_at=1_100),
        _order(3, "BTC-USD", "SELL", "1", "150", "0.18", created_at=1_200),
        _order(4, "BTC-USD", "SELL", "1", "240", "0.29", created_at=1_300),
    ]
    trips = round_trips_from_orders("keel.db", orders, {1: "turtle_breakout"}).trips
    assert [(t.entry_fill, t.exit_fill) for t in trips] == [
        (Decimal("100"), Decimal("150")),
        (Decimal("200"), Decimal("240")),
    ]


def test_open_unfilled_and_stray_rows_are_excluded_and_counted() -> None:
    orders = [
        _order(1, "PAXG-USD", "BUY", "2", "4500", "1.90", created_at=1_000),  # still open
        _order(2, "XLM-USD", "BUY", "10", None, None, status="rejected", created_at=1_100),
        _order(3, "XLM-USD", "BUY", "5", None, None, status="pending", created_at=1_150),
        _order(4, "SOL-USD", "SELL", "3", "100", "0.12", created_at=1_200),  # no entry on record
    ]
    read = round_trips_from_orders("keel.db", orders, {1: "turtle_breakout"})
    assert read.trips == []
    assert read.open_buys == 1
    assert read.unfilled_orders == 2
    assert read.stray_sells == 1


def test_matching_never_crosses_modes_products_rules_or_sizes() -> None:
    orders = [
        _order(1, "BTC-USD", "BUY", "1", "100", "0.12", mode="paper", created_at=1_000),
        _order(2, "BTC-USD", "SELL", "1", "120", "0.14", mode="live", created_at=1_100),
        _order(3, "ETH-USD", "SELL", "1", "60", "0.07", created_at=1_200),
        _order(4, "BTC-USD", "SELL", "2", "120", "0.14", created_at=1_300),
    ]
    read = round_trips_from_orders("keel.db", orders, {1: "turtle_breakout"})
    assert read.trips == []
    assert read.open_buys == 1
    assert read.stray_sells == 3


def test_timestamp_inversion_is_flagged_not_hidden() -> None:
    # The paper ledger has recorded sells whose created_at precedes their buy's. Id order is
    # the ledger's own sequencing, so the trip still counts -- but the report must say it.
    orders = [
        _order(1, "BCH-USD", "BUY", "1", "219", "2.41", created_at=1_200),
        _order(2, "BCH-USD", "SELL", "1", "214", "2.36", created_at=1_100),
    ]
    (trip,) = round_trips_from_orders("keel.db", orders, {1: "turtle_breakout"}).trips
    assert trip.timestamps_inverted()


def test_unknown_rule_labels_the_composition_without_dropping_the_trade() -> None:
    orders = [
        _order(1, "BTC-USD", "BUY", "1", "100", "0.12", rule_id=99, created_at=1_000),
        _order(2, "BTC-USD", "SELL", "1", "110", "0.13", rule_id=99, created_at=1_100),
    ]
    (trip,) = round_trips_from_orders("keel.db", orders, {}).trips
    assert trip.rule == "unknown"
    assert trip.pnl_net > 0


# -- extraction: the trade_outcomes ledger is the authoritative record ----------------------


def test_ledger_rows_carry_their_recorded_pnl_verbatim() -> None:
    # A ledger row IS the record (pnl_net realized, net of fees, written by streak.py) --
    # it is never re-derived from the fills, which would drop the entry fee the row folded
    # into pnl_net but does not store separately.
    (trip,) = ledger_round_trips("keel.db", [_ledger("BTC-USD", "1", "100", "110", "0.30", "9.60")])
    assert trip.source == "ledger"
    assert trip.pnl_net == Decimal("9.60")
    assert trip.rule == "turtle_breakout"


# -- pooling: dedup, scratch/open exclusion, oldest-first order -----------------------------


def _sample_with_two_profiles() -> PooledSample:
    orders_a = round_trips_from_orders(
        "keel.db",
        [
            _order(1, "BTC-USD", "BUY", "1", "100", "0.12", created_at=1_000),
            _order(2, "BTC-USD", "SELL", "1", "110", "0.13", created_at=1_100),
            _order(3, "PAXG-USD", "BUY", "2", "4500", "1.90", created_at=1_200),
        ],
        {1: "turtle_breakout"},
    )
    ledger_a = [_ledger("CRV-USD", "100", "0.30", "0.35", "1.20", "-3.70", closed_at=900)]
    # Same fills recorded in BOTH the ledger and the orders of profile B: counted once.
    orders_b = round_trips_from_orders(
        "keel-paperhourly.db",
        [
            _order(5, "BCH-USD", "BUY", "1", "219", "2.41", rule_id=7, created_at=1_300),
            _order(6, "BCH-USD", "SELL", "1", "214", "2.36", rule_id=7, created_at=1_400),
        ],
        {7: "turtle_breakout"},
    )
    ledger_b = [_ledger("BCH-USD", "1", "219", "214", "4.77", "-9.83", closed_at=1_400)]
    sample = build_sample(
        [
            ("keel.db", orders_a, ledger_a),
            ("keel-paperhourly.db", orders_b, ledger_b),
        ]
    )
    assert sample.profiles == ("keel.db", "keel-paperhourly.db")
    return sample


def test_pool_dedups_a_ledger_row_against_the_same_round_trip_in_orders() -> None:
    sample = _sample_with_two_profiles()
    # BTC (orders) + CRV (ledger) + BCH (ledger, its orders twin deduped away): 3, not 4.
    assert sample.n_pooled() == 3
    assert sample.deduped == 1
    # Oldest first -- the ledger reader's own ordering convention.
    assert [t.product_id for t in sample.trips] == ["CRV-USD", "BTC-USD", "BCH-USD"]


def test_pool_excludes_open_positions_and_counts_them() -> None:
    sample = _sample_with_two_profiles()
    assert sample.excluded_open == 1  # the PAXG buy with no sell


def test_outcomes_map_to_the_significance_contract() -> None:
    sample = _sample_with_two_profiles()
    outcomes = sample.outcomes()
    # win/loss by the sign of fee-honest net pnl; every row carries its pnl; r-multiple is
    # None (a forward trade has no pre-registered risk unit to be a multiple OF).
    assert [o for o, _pnl, _r in outcomes] == ["loss", "win", "loss"]
    assert all(o in ("win", "loss", "scratch") for o, _p, _r in outcomes)
    assert all(r is None for _o, _p, r in outcomes)
    stat = sample.significance()
    assert stat.n_trades == 3
    assert stat.wins == 1


def test_scratch_pnl_exactly_zero_counts_toward_nothing() -> None:
    ledger = [
        _ledger("BTC-USD", "1", "100", "100", "0", "0"),  # scratch: not evidence either way
        _ledger("ETH-USD", "1", "50", "60", "0.10", "9.90"),
    ]
    sample = build_sample([("keel.db", _empty_read(), ledger)])
    assert sample.n_pooled() == 2  # the row is IN the pool's composition...
    assert sample.scratches() == 1  # ...but counts toward no win, no loss, no n
    stat = sample.significance()
    assert stat.n_trades == 1
    assert stat.wins == 1


# -- the refusal: zero pooled trades is "nothing to review", never a degenerate report -------


def test_zero_pooled_trades_refuses() -> None:
    empty = build_sample(
        [("keel.db", OrdersRead(trips=[], open_buys=2, unfilled_orders=1, stray_sells=0), [])]
    )
    assert empty.n_pooled() == 0
    assert is_refused(empty)
    review = descriptive_review(empty, run_date="2026-08-27")
    assert review.refusal is not None
    refusal_text = "\n".join(review.refusal)
    assert "nothing to review" in refusal_text
    assert "0 pooled" in refusal_text


def test_nonempty_pool_does_not_refuse() -> None:
    assert not is_refused(_sample_with_two_profiles())
    assert descriptive_review(_sample_with_two_profiles(), run_date="2026-08-27").refusal is None


# -- the report skeleton: title, citation, table, THE sentence, not-a-gate --------------------


def _review() -> DescriptiveReview:
    return descriptive_review(_sample_with_two_profiles(), run_date="2026-08-27")


def test_report_states_title_event_date_and_method_citation() -> None:
    text = "\n".join(render_report(_review()))
    assert EVENT_DATE == "2026-09-30"
    assert f"# The {EVENT_DATE} pooled forward-trades review" in text
    assert "descriptive" in text.lower()
    assert "keel/research/significance.py" in text
    assert "#427" in text
    assert "#353" in text


def test_report_carries_the_power_sentence_with_the_pools_own_numbers() -> None:
    text = "\n".join(render_report(_review()))
    # n=3 pooled -> n_eff 1.16 -> edge 115.2 points: the sentence is generated from the
    # pool's ACTUAL n, so a 3-trade pool says something very different from a 100-trade one.
    assert power_sentence(3) in text
    assert "1.16 effective of 3 pooled" in text
    assert "115.2 points" in text


def test_report_states_raw_n_beside_n_eff_and_the_ci() -> None:
    text = "\n".join(render_report(_review()))
    assert "n=3 pooled" in text
    assert str(design_effect()) in text  # the correction is cited, not implied
    assert "lower bound" in text.lower()


def test_report_composition_table_counts_what_was_excluded() -> None:
    text = "\n".join(render_report(_review()))
    assert "keel-paperhourly.db" in text
    assert "open" in text.lower()
    assert "scratch" in text.lower()


def test_report_has_no_pass_fail_verdict_on_the_edge() -> None:
    text = "\n".join(render_report(_review())).lower()
    # The descriptive reframing: the significance machinery MEASURES (win rate, break-even,
    # edge, CI) but the report carries no verdict vocabulary about the edge -- "verdict" is
    # about POWER only.
    assert "distinguishable" not in text
    assert "insufficient_n" not in text
    assert "not a pass/fail gate" in text


def test_report_says_the_event_reruns_when_run_before_the_event_date() -> None:
    text = "\n".join(render_report(_review()))
    assert "re-runs on 2026-09-30" in text


def test_preview_note_is_absent_when_run_on_the_event_date() -> None:
    review = descriptive_review(_sample_with_two_profiles(), run_date="2026-09-30")
    text = "\n".join(render_report(review))
    assert "preview" not in text.lower()


# -- determinism -------------------------------------------------------------------------------


def test_same_pool_gives_identical_reports() -> None:
    a = descriptive_review(_sample_with_two_profiles(), run_date="2026-08-27")
    b = descriptive_review(_sample_with_two_profiles(), run_date="2026-08-27")
    assert render_report(a) == render_report(b)
