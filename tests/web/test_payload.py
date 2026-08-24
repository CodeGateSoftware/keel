"""The serialisation contract (#533) -- the rule every endpoint in the web-UI milestone inherits.

These tests exist because the failure they guard against is SILENT. A `Decimal` that becomes a
JSON number does not raise, does not warn, and looks right for every value a developer happens to
try by hand: `0.1 + 0.2` only misbehaves at the seventeenth digit, and a notional only loses a
cent once it is large enough that nobody is checking. The contract is therefore pinned
mechanically -- a recursive walk over the real payload -- rather than by review.

Three properties are pinned, in the order the issue states them:

* **Rule 1** -- money crosses the wire as a STRING. `test_no_wire_value_is_ever_a_json_number`
  walks the parsed payload and fails on any `int`/`float` anywhere in it, which is a strictly
  stronger statement than "no monetary field is a number" and needs no per-field judgement about
  which fields are monetary. `test_the_number_walker_is_proven_false_capable` is its positive
  control: a walker that silently matched nothing would make the whole milestone vacuously safe.
* **Rule 2** -- values arrive presentation-ready.
  `test_the_serialiser_computes_nothing_every_wire_figure_came_from_the_report` proves the
  serialiser reads rather than derives: every `Decimal`-parseable value on the wire must equal a
  `Decimal`/`int` the report builder already put on the report.
* **Rule 3** -- semantic state is an explicit field. The `state` tests pin that a loss is
  labelled `"bad"` in Python and that the glyph in `display` carries the same distinction without
  colour (#532's non-colour signal).

Plus the hazard that has already cost this codebase real orders: `Decimal.normalize()` renders
`Decimal("50")` as `Decimal("5E+1")`, and `"5E+1"` on the wire is a number no `Decimal(...)`
constructor on the far side will read as fifty in a form a human recognises.
`test_scientific_notation_never_reaches_the_wire` feeds the serialiser the exact shapes that
produce it.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest

from keel.commands.activity import ActivityCycle, ActivityEvent, ActivityFeed
from keel.commands.insights import (
    AccountSummary,
    GateDistance,
    InsightsReport,
    JournalEntry,
    JournalReport,
    RuleTrackRecord,
)
from keel.commands.status import (
    AutonomyStatus,
    MarketSessionStatus,
    OpenPositionStatus,
    ProductFreshness,
    RuleSummary,
    StatusReport,
    SubscriptionStatusRow,
    WithdrawalAttestationStatus,
)
from keel.web import payload

NOW_TS = 1_756_000_000


# -- fixtures: real report dataclasses, populated ------------------------------------------------


def _status_report(**overrides: Any) -> StatusReport:
    """A fully populated `StatusReport` -- every list non-empty, so the recursive walkers below
    actually reach every branch of the payload instead of passing over empty collections."""
    base: dict[str, Any] = dict(
        now_ts=NOW_TS,
        mode="paper",
        kill_switch_engaged=False,
        autonomy=AutonomyStatus(
            live=True,
            autonomous=True,
            autonomous_until=NOW_TS + 3600,
            updated_ts=NOW_TS - 60,
            profile_readable=True,
        ),
        equity_state_mode="paper",
        high_water_mark=Decimal("12345.67"),
        drawdown_total_pct=Decimal("0.05"),
        drawdown_weekly_pct=Decimal("0.01"),
        max_total_dd_pct=Decimal("0.20"),
        max_weekly_dd_pct=Decimal("0.08"),
        rail11_status="ok",
        withdrawal_attestation=WithdrawalAttestationStatus(
            state="attested",
            enabled=True,
            attested_at=NOW_TS - 86400,
            expires_in_sec=6 * 86400,
            expired_for_sec=None,
        ),
        paper_cash_usdc=Decimal("955.25"),
        open_positions=[
            OpenPositionStatus(
                id=7,
                product_id="BTC-USD",
                rule_name="turtle_breakout",
                qty=Decimal("0.01000000"),
                entry_price=Decimal("50000.00"),
                opened_at=NOW_TS - 7200,
                has_bracket=True,
            )
        ],
        rule_counts={"live": 1, "paper": 2},
        live_rules=[
            RuleSummary(
                id=3, kind="dca", status="live", product_id="ETH-USD", params={"budget_usd": 50}
            )
        ],
        data_freshness=[
            ProductFreshness(
                product_id="BTC-USD", granularity="ONE_HOUR", last_ts=NOW_TS - 300, age_sec=300
            )
        ],
        subscriptions=[
            SubscriptionStatusRow(
                venue="coinbase",
                tier_name="advanced",
                pacing="steady",
                stored_status="active",
                effective_status="active",
                effective_cap=Decimal("2500.00"),
            )
        ],
        market_session=MarketSessionStatus(state="open", recorded_ts=NOW_TS - 120, defused=False),
    )
    base.update(overrides)
    return StatusReport(**base)


def _insights_report(**overrides: Any) -> InsightsReport:
    base: dict[str, Any] = dict(
        now_ts=NOW_TS,
        account=AccountSummary(
            mode="paper",
            equity_state_mode="paper",
            high_water_mark=Decimal("12345.67"),
            drawdown_total_pct=Decimal("0.05"),
            drawdown_weekly_pct=Decimal("0.01"),
            max_total_dd_pct=Decimal("0.20"),
            max_weekly_dd_pct=Decimal("0.08"),
            rail11_status="ok",
            paper_cash_usdc=Decimal("955.25"),
        ),
        rules=[
            RuleTrackRecord(
                rule_name="turtle_breakout",
                status="paper",
                promotion_class="trend",
                n_trades=12,
                win_rate=41.5,
                avg_win=Decimal("31.20"),
                avg_loss=Decimal("-14.05"),
                realized_rr=Decimal("2.22"),
                expectancy=Decimal("0.8410"),
                profit_factor=Decimal("1.57"),
                max_drawdown=Decimal("-88.40"),
                significant=False,
                gate=GateDistance(
                    rule_name="turtle_breakout",
                    promotion_class="trend",
                    n_trades=12,
                    min_trades=30,
                    trades_remaining=18,
                    win_rate=41.5,
                    min_win_rate=40.0,
                    realized_rr=Decimal("2.22"),
                    min_rr=Decimal("2.0"),
                    expectancy=Decimal("0.8410"),
                    min_expectancy=Decimal("0.5"),
                    passing=False,
                    blocking_reasons=["n_trades 12 < 30"],
                ),
            )
        ],
        closed_trade_count=12,
    )
    base.update(overrides)
    return InsightsReport(**base)


def _journal_report(**overrides: Any) -> JournalReport:
    base: dict[str, Any] = dict(
        now_ts=NOW_TS,
        mode="paper",
        entries=[
            JournalEntry(
                closed_at=NOW_TS - 3600,
                opened_at=NOW_TS - 86400,
                rule_name="turtle_breakout",
                product_id="BTC-USD",
                qty=Decimal("0.01000000"),
                entry_fill=Decimal("50000.00"),
                exit_fill=Decimal("48766.00"),
                pnl_net=Decimal("-12.34"),
                fees=Decimal("0.6150"),
                r_multiple=Decimal("-0.82"),
                is_dca=False,
                outcome="loss",
            ),
            JournalEntry(
                closed_at=NOW_TS - 1800,
                opened_at=NOW_TS - 90000,
                rule_name="dca",
                product_id="ETH-USD",
                qty=Decimal("0.50000000"),
                entry_fill=Decimal("2000.00"),
                exit_fill=None,
                pnl_net=Decimal("41.00"),
                fees=None,
                r_multiple=None,
                is_dca=True,
                outcome="dca",
            ),
        ],
        total_count=2,
        filters={
            "rule": None,
            "asset": None,
            "since_ts": NOW_TS - 604800,
            "until_ts": None,
            "limit": 50,
            "include_open": False,
        },
    )
    base.update(overrides)
    return JournalReport(**base)


def _activity_feed(**overrides: Any) -> ActivityFeed:
    cycle = ActivityCycle(
        cycle_id="c-1",
        started_ts=float(NOW_TS - 600),
        ended_ts=float(NOW_TS - 598),
        mode="paper",
        products=("BTC-USD",),
        rules=("turtle_breakout",),
        signals=1,
        blocked=1,
        entered=0,
        exited=0,
        errors=2,
        highlights=("rail 11 blocked an entry",),
        events=(
            ActivityEvent(
                ts=float(NOW_TS - 599),
                level="INFO",
                event="cycle_start",
                cycle_id="c-1",
                fields={"products": 1, "budget_usd": Decimal("50.00"), "dry_run": True},
            ),
        ),
        events_dropped=3,
    )
    base: dict[str, Any] = dict(
        status="ok",
        source="/tmp/keel.log",
        cycles=(cycle,),
        detail=None,
        lines_read=120,
        lines_skipped=2,
        window_truncated=True,
        cycles_dropped=1,
        scope="today",
        scope_start_ts=float(NOW_TS - 50000),
        now_ts=float(NOW_TS),
        cycles_out_of_scope=4,
        last_cycle_before_scope=cycle,
        scope_fully_covered=False,
    )
    base.update(overrides)
    return ActivityFeed(**base)


def _every_payload() -> dict[str, Any]:
    """All four payload builders at once. The guards below run over the whole surface, because a
    contract that holds for `status` and leaks on `journal` is not a contract."""
    return {
        "status": payload.status_payload(_status_report()),
        "insights": payload.insights_payload(_insights_report()),
        "journal": payload.journal_payload(_journal_report()),
        "activity": payload.activity_payload(_activity_feed()),
    }


# -- recursive walkers ---------------------------------------------------------------------------


def _walk(node: Any, path: str = "$") -> list[tuple[str, Any]]:
    """Every leaf in a parsed JSON document, with the path that reaches it. Written here rather
    than imported so the guard cannot be weakened by a change to production code."""
    if isinstance(node, dict):
        out: list[tuple[str, Any]] = []
        for key, value in node.items():
            out.extend(_walk(value, f"{path}.{key}"))
        return out
    if isinstance(node, list):
        out = []
        for index, value in enumerate(node):
            out.extend(_walk(value, f"{path}[{index}]"))
        return out
    return [(path, node)]


def _json_numbers(document: Any) -> list[str]:
    """Paths whose leaf is a JSON number. `bool` is excluded FIRST and deliberately: Python's
    `bool` is a subclass of `int`, so an `isinstance(x, int)` test alone would flag every JSON
    `true` as a number and make this guard fail for the wrong reason."""
    return [
        path
        for path, leaf in _walk(document)
        if not isinstance(leaf, bool) and isinstance(leaf, (int, float))
    ]


# -- Rule 1: money crosses the wire as a string ---------------------------------------------------


def test_no_wire_value_is_ever_a_json_number() -> None:
    """THE guard the whole milestone rests on.

    Stated as "no JSON numbers ANYWHERE" rather than "no monetary field is a number", because the
    second form needs a list of which fields are monetary -- and that list is exactly the thing
    that rots when a new field lands. The strong form needs no list and cannot rot.

    The spec's one reserved exception is the `sort` field (ordering only, never displayed, never
    summed), which #533 explicitly does not ship. If it is ever added, THIS is where its allowance
    goes, named and scoped, so that adding it is a decision rather than an accident.
    """
    document = json.loads(json.dumps(_every_payload()))

    assert _json_numbers(document) == []


def test_the_number_walker_is_proven_false_capable() -> None:
    """The guard's own positive control. An `_json_numbers` that matched nothing -- a typo in the
    isinstance, a walker that stops at the first list -- would make the test above green over a
    payload full of doubles. So: plant one at depth, under a list, and require it to be found;
    and require a JSON `true` NOT to be found, since `bool` is an `int` in Python."""
    planted = {"a": [{"b": {"c": 0.1}}], "flag": True, "text": "0.1"}

    assert _json_numbers(planted) == ["$.a[0].b.c"]


def test_a_decimal_round_trips_through_the_wire_string_without_loss() -> None:
    """Rule 1's actual point: the string form is not merely "not a float", it is EXACT.

    Every value below is one `float` would corrupt or one that has bitten this codebase before --
    a quantity at Coinbase's eight-decimal base increment, a notional past the 17 significant
    digits a double carries, and the `Decimal("50")` whose `normalize()` form is `5E+1`.
    """
    hazards = [
        Decimal("0.1"),
        Decimal("0.01000000"),
        Decimal("50"),
        Decimal("50.00"),
        Decimal("12345.67"),
        Decimal("-12.34"),
        Decimal("0.00000001"),
        Decimal("123456789012345678901234567890.123456789"),
        Decimal("5E+1"),
        Decimal("1E-9"),
        Decimal("-0.000000000000000001"),
    ]

    for source in hazards:
        wire = payload.money(source)["value"]
        assert Decimal(wire) == source, (source, wire)


def test_the_wire_value_is_decimal_parseable_and_the_display_string_is_not() -> None:
    """The two strings do different jobs and must not be conflated. `value` is machine input --
    no grouping separators, no currency symbol, no glyph, so `Decimal(value)` just works.
    `display` is the human's, and carries all of that."""
    field = payload.money(Decimal("12345.67"))

    assert field["value"] == "12345.67"
    assert Decimal(field["value"]) == Decimal("12345.67")
    assert field["display"] == "$12,345.67"


def test_scientific_notation_never_reaches_the_wire() -> None:
    """The `5E+1` hazard, at the level of the whole payload rather than one helper.

    `Decimal.normalize()` turns `Decimal("50")` into `Decimal("5E+1")`, and `str()` on any Decimal
    with a positive exponent does the same. A client reading `"5E+1"` as a price has read fifty as
    five, or as nothing at all. Every string leaf of every payload is checked, not just the ones
    the author remembered were money.
    """
    report = _status_report(
        high_water_mark=Decimal("5E+1"),
        paper_cash_usdc=Decimal("1E-9"),
        drawdown_total_pct=Decimal("2E-3"),
    )
    document = json.loads(json.dumps(payload.status_payload(report)))

    offenders = [
        (path, leaf)
        for path, leaf in _walk(document)
        if isinstance(leaf, str) and ("E+" in leaf or "E-" in leaf or "e+" in leaf)
    ]

    assert offenders == []
    assert payload.money(Decimal("5E+1"))["value"] == "50"
    assert payload.money(Decimal("1E-9"), places=9)["display"] == "$0.000000001"


# -- Rule 2: the serialiser computes nothing ------------------------------------------------------


def _report_figures(node: Any, seen: set[int] | None = None) -> set[Decimal]:
    """Every `Decimal`/`int` the report builder already put on the report, as `Decimal`.

    Walks dataclasses, mappings and sequences generically rather than naming fields, so a field
    added upstream is covered the day it appears instead of the day someone remembers to add it
    here."""
    seen = set() if seen is None else seen
    if id(node) in seen:
        return set()
    seen.add(id(node))
    if isinstance(node, bool):
        return set()
    if isinstance(node, Decimal):
        return {node}
    if isinstance(node, int):
        return {Decimal(node)}
    if isinstance(node, str):
        return set()
    if isinstance(node, dict):
        out: set[Decimal] = set()
        for key, value in node.items():
            out |= _report_figures(key, seen) | _report_figures(value, seen)
        return out
    if isinstance(node, (list, tuple, set, frozenset)):
        out = set()
        for item in node:
            out |= _report_figures(item, seen)
        return out
    fields = getattr(node, "__dataclass_fields__", None)
    if fields is not None:
        out = set()
        for name in fields:
            out |= _report_figures(getattr(node, name), seen)
        return out
    return set()


def test_the_serialiser_computes_nothing_every_wire_figure_came_from_the_report() -> None:
    """Rule 2, enforced rather than asserted in prose.

    Every `Decimal`-parseable `value` on the wire must equal a figure the report ALREADY held. A
    serialiser that multiplied `qty` by `entry_price` to offer the client a notional, or scaled a
    drawdown fraction by 100 to make it read as a percentage, would produce a number that appears
    nowhere on the report -- and would fail here, by name and path.

    `StatusReport` and `JournalReport` are used because every figure they carry is a `Decimal` or
    an `int`; `InsightsReport` carries `float` win rates, which have no exact decimal form and are
    covered by their own test below.
    """
    for name, report, built in (
        ("status", _status_report(), payload.status_payload(_status_report())),
        ("journal", _journal_report(), payload.journal_payload(_journal_report())),
    ):
        available = _report_figures(report)
        for path, leaf in _walk(json.loads(json.dumps(built))):
            if not path.endswith(".value") or not isinstance(leaf, str) or not leaf:
                continue
            try:
                figure = Decimal(leaf)
            except ArithmeticError:
                continue  # an ISO-8601 instant or an enum word, not a figure
            except ValueError:
                continue
            assert figure in available, f"{name} {path}: {leaf!r} is on no report field"


def test_an_open_position_carries_no_notional_because_the_report_holds_none() -> None:
    """The single most tempting place to break Rule 2, pinned so the temptation fails loudly.

    `qty * entry_price` is one multiplication away, and the spec's illustrative payload even shows
    a `notional` on a position. But `OpenPositionStatus` (`keel/commands/status.py`) carries
    `qty`, `entry_price`, `opened_at`, `rule_name` and `has_bracket` -- and NOT a notional, and
    not a P&L. Serialising one would mean this layer computed a figure the trading rails never
    saw, which is precisely the invariant `test_console_thinness.py` exists to keep checkable.

    If a notional is wanted, it is added to `gather_status`, where the rails can see it. Then this
    test changes -- deliberately, in a commit that says so.
    """
    position = payload.status_payload(_status_report())["open_positions"][0]

    assert "notional" not in position
    assert "pnl" not in position
    assert position["qty"]["value"] == "0.01000000"
    assert position["entry_price"]["value"] == "50000.00"


def test_a_drawdown_fraction_is_not_rescaled_into_a_percentage() -> None:
    """`drawdown_total_pct` is named for a percentage but is a FRACTION: `_rail11_status` compares
    it against `max_total_dd_pct=Decimal("0.20")`, so `0.05` means five percent, not five basis
    points. Multiplying by 100 to make the label true would be arithmetic in the serialiser -- so
    the value crosses unchanged and the display says what it is.

    (`keel/web/render.py:150` renders this same field through `pct()`, which appends a `%` to the
    raw fraction and therefore prints "0.05%" for a 5% drawdown. The contract does not inherit
    that; fixing the HTML renderer is a separate change against a separate surface.)
    """
    built = payload.status_payload(_status_report())

    assert built["drawdown"]["total"]["value"] == "0.05"
    assert "%" not in built["drawdown"]["total"]["display"]
    assert built["drawdown"]["max_total"]["value"] == "0.20"


def test_a_win_rate_float_is_re_encoded_not_recomputed() -> None:
    """`RuleTrackRecord.win_rate` is a `float` upstream -- a statistic, never money. It reaches the
    wire through its own shortest round-trip repr, so the figure on the wire is the figure the
    report held and nothing was recomputed on the way."""
    built = payload.insights_payload(_insights_report())

    assert built["rules"][0]["win_rate"]["value"] == "41.5"
    assert built["rules"][0]["win_rate"]["display"] == "41.5%"


# -- Rule 3: semantic state is a field, never an inference ----------------------------------------


def test_a_loss_is_labelled_bad_in_python_not_by_the_sign_on_the_wire() -> None:
    """Rule 3. The client must never read a minus sign and conclude "bad" -- that is arithmetic by
    another name, and it relocates a trading judgement into a browser."""
    entries = payload.journal_payload(_journal_report())["entries"]
    loss, gain = entries[0], entries[1]

    assert loss["pnl"]["state"] == "bad"
    assert loss["pnl"]["value"] == "-12.34"
    assert gain["pnl"]["state"] == "good"


def test_state_survives_without_colour() -> None:
    """#532: colour must never be the only signal. `display` carries a glyph and an explicit sign,
    so profit and loss stay distinguishable in greyscale, on e-ink, in sunlight, and to the
    roughly one man in twelve who cannot separate the palette's red from its green."""
    entries = payload.journal_payload(_journal_report())["entries"]

    assert entries[0]["pnl"]["display"] == "▼ −$12.34"
    assert entries[1]["pnl"]["display"] == "▲ +$41.00"


def test_every_field_carries_all_three_keys_always() -> None:
    """Schema uniformity is itself part of the contract: a client that has to test whether `state`
    is present is branching on payload SHAPE, which is inference by another route. Every field
    object has `value`, `display` and `state`, in every payload, for every value including
    absent ones."""
    for name, built in _every_payload().items():
        leaves = _walk(built)
        by_parent: dict[str, set[str]] = {}
        for path, _leaf in leaves:
            parent, _, key = path.rpartition(".")
            by_parent.setdefault(parent, set()).add(key)
        for parent, keys in by_parent.items():
            if "value" in keys:
                assert keys == {"value", "display", "state"}, f"{name} {parent}: {sorted(keys)}"


def test_every_state_word_is_one_the_contract_declares() -> None:
    """A free-text `state` would push the client back into guessing. The vocabulary is closed, and
    #532's palette and glyph table key off exactly these words."""
    for name, built in _every_payload().items():
        for path, leaf in _walk(built):
            if path.endswith(".state"):
                assert leaf in payload.STATES, f"{name} {path}: {leaf!r}"


def test_absence_is_unknown_and_never_a_zero() -> None:
    """`None` means "not recorded"; `0` means "recorded as zero". Collapsing the first into the
    second is the shape of the always-passing fee rail (#198) -- a missing number that reads as a
    real one. On the wire, absence is an empty `value`, a dash, and `state: "unknown"`."""
    absent = payload.money(None)

    assert absent == {"value": "", "display": "—", "state": "unknown"}
    assert payload.money(Decimal("0.00"))["value"] == "0.00"
    assert payload.money(Decimal("0.00"))["state"] == "neutral"


def test_a_kill_switch_carries_its_own_judgement() -> None:
    """A boolean the operator must act on is not a bare `true`: it arrives with the word to show
    and the state to style it by."""
    engaged = payload.status_payload(_status_report(kill_switch_engaged=True))["kill_switch"]
    clear = payload.status_payload(_status_report(kill_switch_engaged=False))["kill_switch"]

    assert engaged["display"] == "ENGAGED"
    assert engaged["state"] == "bad"
    assert clear["state"] == "good"


def test_an_unknown_rail_state_is_unknown_and_not_quietly_good() -> None:
    """`_rail11_status` returns `"unknown"` when a drawdown scalar was never written -- explicitly,
    because reporting an unwritten value as a confident "ok" would be a lie. The contract keeps
    that distinction: `unknown` is its own state, not a green one.

    This is why the classifier here is NOT `render._tone_for_rail`, which matches on the words
    "breach"/"halt"/"warn" and falls through to `"good"` -- so it styles a rail nobody has measured
    as if it had passed.
    """
    halted = payload.status_payload(_status_report(rail11_status="HALTED"))
    unknown = payload.status_payload(_status_report(rail11_status="unknown"))
    ok = payload.status_payload(_status_report(rail11_status="ok"))

    assert halted["drawdown"]["rail11"]["state"] == "bad"
    assert unknown["drawdown"]["rail11"]["state"] == "unknown"
    assert ok["drawdown"]["rail11"]["state"] == "good"


def test_an_expired_withdrawal_attestation_reads_as_bad() -> None:
    """Rail 17's state is the one an operator must act on within a TTL; it arrives judged."""
    expired = payload.status_payload(
        _status_report(
            withdrawal_attestation=WithdrawalAttestationStatus(
                state="expired",
                enabled=True,
                attested_at=NOW_TS - 12 * 86400,
                expires_in_sec=None,
                expired_for_sec=5 * 86400,
            )
        )
    )["withdrawal_attestation"]

    assert expired["state"]["state"] == "bad"
    # `_human_age`, exactly as `_rail17_line` prints it -- "EXPIRED 5d ago", not "5d".
    assert expired["expired_for"]["display"] == "5d ago"


# -- presentation-ready: displays are built here, never derivable there ---------------------------


def test_a_quantity_shows_its_unit_and_drops_its_padding_without_normalize() -> None:
    """`0.01000000 BTC` is how the ledger stores it and `0.01 BTC` is how a human reads it. The
    trim is string work on an already-formatted decimal, NOT `Decimal.normalize()` -- normalize is
    what turns `50` into `5E+1`."""
    assert payload.quantity(Decimal("0.01000000"), unit="BTC")["display"] == "0.01 BTC"
    assert payload.quantity(Decimal("0.01000000"), unit="BTC")["value"] == "0.01000000"
    assert payload.quantity(Decimal("50"), unit="BTC")["display"] == "50 BTC"
    assert payload.quantity(Decimal("1234.5"), unit="ETH")["display"] == "1,234.5 ETH"


def test_counts_are_grouped_for_reading_and_exact_on_the_wire() -> None:
    assert payload.count(1234567)["value"] == "1234567"
    assert payload.count(1234567)["display"] == "1,234,567"
    assert payload.count(None)["state"] == "unknown"


def test_an_instant_arrives_as_utc_in_both_forms() -> None:
    """Every day boundary in keel is UTC. `value` is ISO-8601 with an explicit `Z` so a client can
    hand it straight to `new Date(...)` without arithmetic; `display` is the same instant already
    written out, so no client ever formats a date to show it in a table."""
    field = payload.moment(0)

    assert field["value"] == "1970-01-01T00:00:00Z"
    assert field["display"] == "1970-01-01 00:00:00 UTC"


def test_a_broken_instant_degrades_instead_of_taking_the_payload_down() -> None:
    """Log timestamps come from outside this process. A corrupt one renders as absent -- the same
    choice `render.utc` already makes -- because a 500 on the whole page is a worse answer than a
    dash in one cell."""
    for broken in (None, float("nan"), 1e30, float("inf")):
        assert payload.moment(broken)["state"] == "unknown"


def test_a_duration_uses_the_cli_s_own_words() -> None:
    """`_human_age`/`_human_remaining` live in `keel/commands/status.py` and are what `keel status`
    prints. The web payload calls them rather than re-deriving the ladder, so the browser and the
    terminal can never disagree about how old the same candle is."""
    assert payload.duration(300)["display"] == "5m ago"
    assert payload.duration(6 * 86400, elapsed=False)["display"] == "6d"
    assert payload.duration(300)["value"] == "300"


def test_the_activity_feed_stringifies_the_open_ended_event_fields() -> None:
    """`ActivityEvent.fields` is deliberately open -- every `log_event` call site invents its own
    kwargs, and the parser keeps them whole rather than projecting onto a fixed schema. That means
    arbitrary ints and Decimals arrive here, and Rule 1 applies to them exactly as it does to a
    known field: they cross as strings."""
    event = payload.activity_payload(_activity_feed())["cycles"][0]["events"][0]

    assert event["fields"] == {"products": "1", "budget_usd": "50.00", "dry_run": "true"}


def test_the_journal_filters_cross_as_strings_too() -> None:
    """`JournalReport.filters` holds the query that produced the report -- `limit`, `since_ts` and
    friends, which are ints. They are echoed back for the client to show, and an echoed int is a
    JSON number like any other."""
    filters = payload.journal_payload(_journal_report())["filters"]

    assert filters["limit"] == "50"
    assert filters["since_ts"] == str(NOW_TS - 604800)
    assert filters["rule"] == ""


def test_an_error_count_is_bad_and_a_quiet_cycle_is_neutral() -> None:
    """A cycle in which the agent looked and nothing happened is a POSITIVE observation, not an
    absence of one -- it is how the feed answers "is it alive". So quiet is `neutral`, never
    `warn`; errors are what turn a row `bad`."""
    cycle = payload.activity_payload(_activity_feed())["cycles"][0]

    assert cycle["errors"]["state"] == "bad"
    assert cycle["quiet"]["state"] == "neutral"


# -- totality ------------------------------------------------------------------------------------


def test_every_builder_survives_a_completely_empty_report() -> None:
    """A fresh install is the commonest state there is, and it is not an error. Every list empty,
    every optional `None`, no exception, and the guards still hold."""
    empty_status = _status_report(
        autonomy=AutonomyStatus(
            live=False,
            autonomous=False,
            autonomous_until=None,
            updated_ts=None,
            profile_readable=False,
        ),
        equity_state_mode=None,
        high_water_mark=None,
        drawdown_total_pct=None,
        drawdown_weekly_pct=None,
        rail11_status="unknown",
        withdrawal_attestation=WithdrawalAttestationStatus(
            state="unattested",
            enabled=None,
            attested_at=None,
            expires_in_sec=None,
            expired_for_sec=None,
        ),
        paper_cash_usdc=None,
        open_positions=[],
        rule_counts={},
        live_rules=[],
        data_freshness=[],
        subscriptions=[],
        market_session=MarketSessionStatus(state=None, recorded_ts=None),
    )
    documents = {
        "status": payload.status_payload(empty_status),
        "insights": payload.insights_payload(_insights_report(rules=[], closed_trade_count=0)),
        "journal": payload.journal_payload(_journal_report(entries=[], total_count=0, filters={})),
        "activity": payload.activity_payload(
            ActivityFeed(status="missing", source="/tmp/nope.log")
        ),
    }

    parsed = json.loads(json.dumps(documents))
    assert _json_numbers(parsed) == []


@pytest.mark.parametrize(
    "builder",
    ["status_payload", "insights_payload", "journal_payload", "activity_payload"],
)
def test_every_payload_is_json_serialisable_without_a_custom_encoder(builder: str) -> None:
    """The builders return plain `dict`/`list`/`str` -- no `Decimal` reaches `json.dumps`. That
    matters beyond tidiness: `json.dumps(Decimal(...))` raises, and the natural fix a hurried
    author reaches for is `default=float`, which is the contract's exact failure mode installed as
    a convenience."""
    report = {
        "status_payload": _status_report(),
        "insights_payload": _insights_report(),
        "journal_payload": _journal_report(),
        "activity_payload": _activity_feed(),
    }[builder]

    json.dumps(getattr(payload, builder)(report))  # no cls=, no default=
