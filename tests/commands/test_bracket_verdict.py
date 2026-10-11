"""The #641 bracket judgement, judged ONCE and read by three surfaces (#961).

`status.bracket_verdict` is the one place that decides whether an open tranche's missing bracket
is a hazard. `keel status` (`_bracket_display`), Pulse's roll-call and missing-bracket count
(`pulse.build_pulse` + `payload._bracket_field`) and Holdings (`payload._bracket_field` over a
`HoldingRow`) all read it. Every case below is seeded into a REAL ledger and pushed through all
three, so a surface that re-derives the rule on its own disagrees here, not on the operator's
screen.

The cases are the ones the live book actually holds (2026-10-11): attributed DCA tranches whose
`initial_stop` is the `Setup` sentinel `0`, pre-v21 DCA tranches with no stop at all, and the
deliberate PAXG `turtle_breakout` tranche held without a resting bracket (#811), which must keep
warning.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.commands import status as status_mod
from keel.commands.activity import ActivityFeed, apply_scope
from keel.commands.holdings import gather_holdings
from keel.commands.pulse import build_pulse
from keel.commands.status import (
    BRACKET_MISSING,
    BRACKETED,
    NO_STOP_BY_DESIGN,
    PAPER_NO_STOP,
    PAPER_STOP,
    bracket_verdict,
    gather_status,
    render_human,
)
from keel.config import AutoTradeConfig
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.types import Side
from keel.web import payload
from tests.commands.test_status import NOW_TS, _config

#: (mode, rule kind, has a bracket, initial_stop, the verdict). `confirm` is this codebase's
#: non-paper mode.
CASES = [
    # #961: DCA never brackets. Pre-v21 legs carry no stop; attributed live legs carry the
    # `Setup` sentinel 0 (`Dca.detect`: "stop=0 ... no stop-loss"). Both are by design.
    ("confirm", "dca", False, None, NO_STOP_BY_DESIGN),
    ("confirm", "dca", False, Decimal("0"), NO_STOP_BY_DESIGN),
    # A negative stop is no stop either (`recorded_stop` is `> 0`): by design for DCA, still a
    # warning for every other kind.
    ("confirm", "dca", False, Decimal("-1"), NO_STOP_BY_DESIGN),
    ("confirm", "turtle_breakout", False, Decimal("-1"), BRACKET_MISSING),
    # A DCA tranche that RECORDED a real stop and has no bracket is not the designed case.
    ("confirm", "dca", False, Decimal("5"), BRACKET_MISSING),
    ("confirm", "dca", True, None, BRACKETED),
    # Every non-DCA tranche without a bracket warns -- with a stop (PAXG, #811) or without one.
    ("confirm", "turtle_breakout", False, Decimal("4521.76"), BRACKET_MISSING),
    ("confirm", "turtle_breakout", False, None, BRACKET_MISSING),
    ("confirm", "turtle_breakout", True, Decimal("90000"), BRACKETED),
    # A kind keel does not know (or an empty one) is never excused: the safe reading is WARN.
    ("confirm", "", False, None, BRACKET_MISSING),
    ("confirm", "no_such_kind", False, None, BRACKET_MISSING),
    # Paper is unchanged by #961: the mode, not the kind, explains the absent bracket.
    ("paper", "dca", False, None, PAPER_NO_STOP),
    ("paper", "dca", False, Decimal("0"), PAPER_STOP),
    ("paper", "turtle_breakout", False, Decimal("90000"), PAPER_STOP),
    ("paper", "turtle_breakout", False, None, PAPER_NO_STOP),
    ("paper", "turtle_breakout", True, Decimal("90000"), BRACKETED),
]

_IDS = [
    f"{mode}-{kind or 'blank'}-{'br' if br else 'nobr'}-{stop}" for mode, kind, br, stop, _ in CASES
]

#: How each verdict reads on the CLI line and what state the console gives it. One table, so a
#: wording or tone change is made in the test and the code together.
_CLI = {
    BRACKETED: "(bracketed)",
    BRACKET_MISSING: "(NO bracket)",
    NO_STOP_BY_DESIGN: "(no stop by design)",
    PAPER_STOP: "(paper stop ",
    PAPER_NO_STOP: "(n/a -- paper resolves stop/target on candle touch)",
}
_STATE = {
    BRACKETED: "good",
    BRACKET_MISSING: "warn",
    NO_STOP_BY_DESIGN: "neutral",
    PAPER_STOP: "neutral",
    PAPER_NO_STOP: "neutral",
}


def _ledger(kind: str, has_bracket: bool, stop: Decimal | None) -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    repo = Repository(conn)
    repo.set_state("kill_switch", False)
    bracket = None
    if has_bracket:
        bracket = repo.insert_order(
            {
                "mode": "live",
                "product_id": "BTC-USD",
                "side": Side.SELL.value,
                "order_type": "stop_limit",
                "qty": Decimal("0.01"),
                "limit_price": Decimal("1"),
                "status": "open",
                "fee": None,
                "expected_fill": None,
                "actual_fill": None,
                "filled_quantity": None,
                "raw_response": None,
                "confirmation": "autonomous",
                "rule_id": None,
                "created_at": NOW_TS - 3600,
                "updated_at": NOW_TS - 3600,
            }
        )
    repo.open_position(
        product_id="BTC-USD",
        rule_name=kind,
        opened_at=NOW_TS - 3600,
        qty=Decimal("0.01"),
        entry_fill=Decimal("65000"),
        entry_fee=Decimal("1.5"),
        initial_stop=stop,
        bracket_order_id=bracket,
    )
    return repo


@pytest.mark.parametrize(("mode", "kind", "has_bracket", "stop", "verdict"), CASES, ids=_IDS)
def test_the_verdict(
    mode: str, kind: str, has_bracket: bool, stop: Decimal | None, verdict: str
) -> None:
    assert (
        bracket_verdict(has_bracket=has_bracket, initial_stop=stop, rule_kind=kind, mode=mode)
        == verdict
    )


@pytest.mark.parametrize(("mode", "kind", "has_bracket", "stop", "verdict"), CASES, ids=_IDS)
def test_status_pulse_and_holdings_read_the_one_verdict(
    mode: str, kind: str, has_bracket: bool, stop: Decimal | None, verdict: str
) -> None:
    repo = _ledger(kind, has_bracket, stop)
    config = _config(auto_trade=AutoTradeConfig(mode=mode, interval_sec=900))
    status = gather_status(repo, config, now_ts=NOW_TS)
    assert len(status.open_positions) == 1  # the tranche reached the reader

    # `keel status`
    (line,) = [ln for ln in render_human(status) if ln.startswith("  [")]
    assert _CLI[verdict] in line, line

    # Pulse: the roll-call cell and the missing-bracket count
    feed = apply_scope(ActivityFeed(status="empty", source="/x/keel.log"), "today", now_ts=NOW_TS)
    pulse = build_pulse(status, feed, (), now_ts=NOW_TS)
    missing = verdict == BRACKET_MISSING
    assert pulse.unbracketed_count == (1 if missing else 0)
    assert [p.id for p in pulse.unbracketed] == ([status.open_positions[0].id] if missing else [])
    pulse_doc = payload.pulse_payload(pulse)
    (pulse_row,) = pulse_doc["open_positions"]
    assert pulse_row["bracket"]["state"] == _STATE[verdict]
    assert pulse_doc["unbracketed_count"]["state"] == ("warn" if missing else "neutral")

    # Holdings
    holdings = gather_holdings(repo, config, now_ts=NOW_TS)
    (holding_row,) = payload.holdings_payload(holdings)["rows"]
    assert holding_row["bracket"] == pulse_row["bracket"]


def test_no_stop_by_design_says_so_on_the_wire() -> None:
    """The console's words for the designed case: neutral, and named as a design, not an absence."""
    field = payload._bracket_field(
        has_bracket=False, initial_stop=None, rule_kind="dca", mode="confirm"
    )
    assert field["display"] == "no stop by design"
    assert field["state"] == "neutral"


def test_the_judgement_reads_the_rule_classes_declaration_not_a_kind_name() -> None:
    """Keyed off `Rule.places_bracket` through the registry, so the word "dca" is spelled in no
    judgement. Flip the declaration and the verdict follows."""
    from keel.strategy.rules.dca import Dca

    before = bracket_verdict(has_bracket=False, initial_stop=None, rule_kind="dca", mode="confirm")
    Dca.places_bracket = True
    try:
        after = bracket_verdict(
            has_bracket=False, initial_stop=None, rule_kind="dca", mode="confirm"
        )
    finally:
        Dca.places_bracket = False
    assert (before, after) == (NO_STOP_BY_DESIGN, BRACKET_MISSING)


def test_no_surface_spells_the_judgement_itself() -> None:
    """The three surfaces call `bracket_verdict`; none compares `has_bracket` against the mode
    on its own (the shape #641 had, twice). Structural: count the call sites."""
    import inspect

    from keel.commands import pulse as pulse_mod

    assert inspect.getsource(status_mod._bracket_display).count("bracket_verdict(") == 1
    assert inspect.getsource(payload._bracket_field).count("bracket_verdict(") == 1
    assert inspect.getsource(pulse_mod.build_pulse).count("bracket_verdict(") == 1
    for fn in (status_mod._bracket_display, payload._bracket_field, pulse_mod.build_pulse):
        assert 'mode != "paper"' not in inspect.getsource(fn), fn.__name__
        assert 'mode == "paper"' not in inspect.getsource(fn), fn.__name__
