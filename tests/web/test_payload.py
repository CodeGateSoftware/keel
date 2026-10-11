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
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from keel.commands.activity import ActivityCycle, ActivityEvent, ActivityFeed
from keel.commands.holdings import ENTRY_ORDER_NOT_IDENTIFIED, HoldingsReport, gather_holdings
from keel.commands.insights import (
    AccountSummary,
    GateDistance,
    InsightsReport,
    RuleTrackRecord,
)
from keel.commands.jobs import JobStatus
from keel.commands.pulse import RESUME_COMMAND, NextDcaBuy, PulseReport, TodayCounts
from keel.commands.setup import ACTIONS, STEPS, DeploymentState, StepState
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
from keel.config import load_config
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.types import Side
from keel.venue_readiness import VenueReadiness, VenueReadinessRow
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
                fields={
                    "products": 1,
                    "budget_usd": Decimal("50.00"),
                    "dry_run": True,
                    # Structured values: a log line is parsed JSON, so a field can be a list or
                    # an object. `str()` would render these as Python reprs.
                    "symbols": ["BTC-USD", "ETH-USD"],
                    "limits": {"max": 1e50, "min": None, "on": False},
                },
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


def _pulse_report(**overrides: Any) -> PulseReport:
    """A populated `PulseReport`: today has a block and errors, the why-not list is non-empty, one
    DCA rule has its budget inferred, and one open tranche is unbracketed -- so the walkers reach
    every branch of `pulse_payload` instead of passing over empty collections."""
    status = overrides.pop("status", None) or _status_report()
    base: dict[str, Any] = dict(
        now_ts=NOW_TS,
        status=status,
        feed=_activity_feed(),
        today=TodayCounts(cycles=3, signals=2, blocked=1, entered=0, exited=0, errors=2),
        last_cycle_ts=float(NOW_TS - 600),
        why_not=("rail veto: per_asset_concentration_cap", "not placed: paper: vetoed by rails"),
        next_dca=(
            NextDcaBuy(
                rule_id=3,
                product_id="ETH-USD",
                cadence_days=7,
                budget_usd=Decimal("50"),
                next_cadence_day_ts=NOW_TS - NOW_TS % 86_400 + 86_400,
                budget_inferred=True,
            ),
        ),
        unbracketed=tuple(status.open_positions),
        unbracketed_count=len(status.open_positions),
        resume_command="",
        today_basis="complete",
        why_not_none="Nothing stopped keel today.",
        no_cycles_line="",
    )
    base.update(overrides)
    return PulseReport(**base)


def _pulse_payload(status: StatusReport, **overrides: Any) -> dict[str, Any]:
    """`pulse_payload` over a report built from `status`: how the status-shaped pins below read
    the same keys the retired `status_payload` served."""
    return payload.pulse_payload(_pulse_report(status=status, **overrides))


def _deployment_state() -> DeploymentState:
    """A half-built deployment: one step done, one outstanding, one that could not be determined.

    All three `done` values on purpose. `None` is NOT `False` -- an unreadable database is not an
    unseeded one -- and a fixture carrying only booleans would let the three-valued field be
    quietly collapsed into two without any guard here noticing."""
    observed = (True, False, None)
    return DeploymentState(
        root=Path("/tmp/keel"),
        config_path=Path("/tmp/keel/config.yaml"),
        db_path=Path("/tmp/keel/keel.db"),
        states=tuple(
            StepState(step=step, done=observed[index % 3], detail=f"observed {step.key}")
            for index, step in enumerate(STEPS)
        ),
    )


@dataclass(frozen=True)
class _BrokerInfo:
    """The `BrokerInfo` shape `venues_payload` reads, without importing an adapter into a
    serialisation test. Two rows are built from it below: one healthy, one that failed to
    construct -- the second is a row rather than an omission, because a missing row would read as
    "not installed", which is a different fact and a worse one to be wrong about."""

    name: str = "coinbase"
    venue: str = "coinbase"
    deployment: str = "spot"
    session_bound: bool = False
    cash_only: bool = True
    quote_currencies: tuple[str, ...] = ("USD",)
    asset_classes: tuple[str, ...] = ("crypto",)
    supported_orders: tuple[str, ...] = ("market", "limit")
    preview: str = "spot, USD quotes"
    supports_fee_summary: bool = True
    declared_endpoints: tuple[str, ...] = ("https://api.coinbase.com",)
    supported_data_feeds: tuple[str, ...] = ("candles",)
    package_version: str | None = "0.1.0"
    error: str | None = None


#: One row per state (#233 PR4), so `_every_payload`'s walkers (money-never-a-number, the
#: `state` vocabulary, the no-custom-encoder check) actually exercise every `_READINESS_STATE`
#: branch `_readiness_payload` can take -- not just whichever state an empty list would have
#: left untested.
_READINESS_ROWS = [
    VenueReadinessRow(
        venue="coinbase", state=VenueReadiness.READY, explanation="all clear", next_step=None
    ),
    VenueReadinessRow(
        venue="ghost",
        state=VenueReadiness.NOT_INSTALLED,
        explanation="no adapter for 'ghost' is installed",
        next_step="uv add keel-broker-ghost",
    ),
    VenueReadinessRow(
        venue="alpaca",
        state=VenueReadiness.NO_CREDENTIALS,
        explanation="none of ALPACA_API_KEY_ID, ALPACA_API_SECRET_KEY is set",
        next_step="keel credentials set <name>",
    ),
    VenueReadinessRow(
        venue="robinhood",
        state=VenueReadiness.MALFORMED_CREDENTIALS,
        explanation="ROBINHOOD_API_KEY_CREDENTIAL holds the base64 PUBLIC key",
        next_step="fix the value and re-set it",
    ),
    VenueReadinessRow(
        venue="kraken",
        state=VenueReadiness.NOT_PERMITTED,
        explanation="kraken has never attested or confirmed a live trade scope",
        next_step="keel scope attest --trading --venue kraken",
    ),
]


def _rule_rows() -> list[dict[str, Any]]:
    """`Repository.get_rules(None)`'s shape: one ledger row with open-ended params."""
    return [
        {
            "id": 1,
            "kind": "breakout",
            "status": "candidate",
            "created_at": NOW_TS,
            "promoted_at": None,
            "demoted_at": None,
            "params": {"product_id": "BTC-USD", "lookback": 20, "risk": Decimal("0.01")},
        }
    ]


def _every_payload() -> dict[str, Any]:
    """Every payload builder at once. The guards below run over the whole surface, because a
    contract that holds for `pulse` and leaks on `rules` is not a contract.

    #534 added five entries here -- the endpoints behind `/api/config`, `/api/setup`, `/api/rules`,
    `/api/venues` and the former `/api/gates` -- and that is the point of the helper being shared:
    a new serialiser joins the money-as-strings walk, the `state`-vocabulary check and the
    no-custom-encoder check by being listed once, rather than by its author remembering three
    separate guards."""
    return {
        "pulse": payload.pulse_payload(_pulse_report(), gate="gate-token"),
        "holdings": payload.holdings_payload(_holdings_report()),
        "activity": payload.activity_payload(_activity_feed()),
        "config": payload.config_payload(
            _build_info(),
            describe="keel 0.1.0+abc [checkout]",
            mode="paper",
            db_path="/tmp/keel/keel.db",
            config_path="/tmp/keel/config.yaml",
        ),
        "setup": payload.setup_payload(
            _deployment_state(),
            actions=ACTIONS,
            not_automated={"market_data": "runs for minutes; see keel fetch"},
            job=JobStatus(
                key="market_data",
                state="failed",
                started_ts=NOW_TS - 90,
                finished_ts=NOW_TS,
                lines=("fetching BTC-USD",),
                error="RuntimeError: no credential",
            ),
        ),
        "rules": payload.rules_payload(_rule_rows(), records=_insights_report()),
        "venues": payload.venues_payload(
            [_BrokerInfo(), _BrokerInfo(name="broken", error="ImportError: no module")],
            _READINESS_ROWS,
        ),
    }


class _build_info:  # noqa: N801 - a stand-in for `keel.version.BuildInfo`, not a public type
    """A resolved build, without shelling out to git in a serialisation test."""

    version = "0.1.0"
    commit = "abc123456789"
    dirty = False
    source = "checkout"
    full_version = "0.1.0+abc123456789"
    is_reproducible = True


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
    document = json.loads(json.dumps(payload.pulse_payload(_pulse_report(status=report))))

    offenders = [
        (path, leaf)
        for path, leaf in _walk(document)
        if isinstance(leaf, str) and ("E+" in leaf or "E-" in leaf or "e+" in leaf)
    ]

    assert offenders == []
    assert payload.money(Decimal("5E+1"))["value"] == "50"
    assert payload.money(Decimal("1E-9"), places=9)["display"] == "$0.000000001"


def test_scientific_notation_cannot_reach_the_wire_through_an_open_ended_field_either() -> None:
    """The same hazard by the other road.

    The test above only feeds Decimals through NAMED report fields, so it can say nothing about
    `ActivityEvent.fields` and `JournalReport.filters` -- the two places a value of unconstrained
    type crosses. Those took a `str(value)` fallback in the first draft, and `str(1e+50)` is
    `"1e+50"`: an exponent on the wire that every `Decimal` precaution in the module was
    irrelevant to, because no `Decimal` was involved.
    """
    hazards = [1e50, 1e-30, Decimal("5E+1"), [1e50], {"deep": [{"deeper": 1e-30}]}]

    for value in hazards:
        rendered = payload.stringify(value)
        assert "e+" not in rendered and "e-" not in rendered, (value, rendered)
        assert "E+" not in rendered and "E-" not in rendered, (value, rendered)


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
    if isinstance(node, float):
        # A statistic (`RuleTrackRecord.win_rate`), never money. Its shortest round-trip repr is
        # the figure the report holds, and that is what `payload._plain` re-encodes it as.
        return {Decimal(repr(node))}
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
        # PROPERTIES count as figures the report returns, exactly as fields do. A frozen report
        # here derives some of its readings from its own data rather than storing them --
        # `ActivityCycle.is_quiet`/`.key` and `JournalReport.shown_count` are the established
        # pattern -- and a stored duplicate of a derived reading is state that can drift out of
        # agreement with what it describes. What matters for this guard is that the REPORT vouches
        # for the figure, not which side of a `@property` it was written on.
        for name, attribute in vars(type(node)).items():
            if not isinstance(attribute, property):
                continue
            try:
                out |= _report_figures(getattr(node, name), seen)
            except Exception:  # pragma: no cover - a property that raises is not a figure
                continue
        return out
    return set()


def _rules_case() -> tuple[str, tuple[Any, ...], dict[str, Any]]:
    """The Rules case for the guard below: the ledger rows AND the track-record report, against
    the payload of both (console PR3). Both go in, and `_report_figures` walks mappings and
    dataclasses generically, so a figure the SERIALISER invented would still be on neither."""
    rows, records = _rule_rows(), _insights_report()
    return ("rules", (rows, records), payload.rules_payload(rows, records=records))


def test_the_serialiser_computes_nothing_every_wire_figure_came_from_the_report() -> None:
    """Rule 2, enforced rather than asserted in prose.

    Every `Decimal`-parseable `value` on the wire must equal a figure the report ALREADY held. A
    serialiser that multiplied `qty` by `entry_price` to offer the client a notional, or scaled a
    drawdown fraction by 100 to make it read as a percentage, would produce a number that appears
    nowhere on the report -- and would fail here, by name and path.

    `PulseReport` and the Rules pair (ledger rows plus `InsightsReport`) are used. The track
    records carry `float` win rates, which `_report_figures` reads through their shortest
    round-trip repr -- the exact string the serialiser writes -- and which have their own test
    below.
    """
    for name, reports, built in (
        ("pulse", (_pulse_report(),), payload.pulse_payload(_pulse_report())),
        _rules_case(),
    ):
        available: set[Decimal] = set()
        for report in reports:
            available |= _report_figures(report)
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


def _position(**overrides: Any) -> OpenPositionStatus:
    base: dict[str, Any] = dict(
        id=7,
        product_id="BTC-USD",
        rule_name="turtle_breakout",
        qty=Decimal("0.01000000"),
        entry_price=Decimal("50000.00"),
        opened_at=NOW_TS - 7200,
        has_bracket=False,
        initial_stop=None,
    )
    base.update(overrides)
    return OpenPositionStatus(**base)


# -- bracket / #641: paper's "NO bracket" is the mode, not a hazard ------------------------------


def test_live_position_with_no_bracket_still_warns() -> None:
    """The acceptance criterion this fix must not touch: a LIVE position (`auto_trade.mode ==
    "confirm"`, this codebase's only non-paper mode -- see `_VALID_AUTO_TRADE_MODES`) with a
    NULL `bracket_order_id` is a real venue-order gap -- the exact state `reconcile_unbracketed_
    positions` and the `unbracketed:` crash ledger (#519, #502) exist to heal. Whatever paper's
    rendering becomes, this one must render byte-identically to before #641."""
    report = _status_report(mode="confirm", open_positions=[_position(has_bracket=False)])
    bracket = _pulse_payload(report)["open_positions"][0]["bracket"]
    assert bracket["display"] == "NO bracket"
    assert bracket["state"] == "warn"


def test_live_position_with_a_bracket_still_reads_good() -> None:
    """The other half of the live case, unchanged: a real bracket still reads GOOD."""
    report = _status_report(mode="confirm", open_positions=[_position(has_bracket=True)])
    bracket = _pulse_payload(report)["open_positions"][0]["bracket"]
    assert bracket["display"] == "bracketed"
    assert bracket["state"] == "good"


def test_paper_position_with_no_bracket_does_not_warn() -> None:
    """The bug: paper never places a venue order, so `has_bracket` is False on every paper
    position by construction, and the old rendering WARNed on all of them -- unactionable, nine
    of nine. A paper row without a bracket must not read `warn`."""
    report = _status_report(mode="paper", open_positions=[_position(has_bracket=False)])
    bracket = _pulse_payload(report)["open_positions"][0]["bracket"]
    assert bracket["state"] != "warn"


def test_paper_position_with_a_recorded_stop_shows_it_instead_of_the_absent_bracket() -> None:
    """Option 1 (the issue's preference order): report the protection that exists. Paper's stop
    is known once `initial_stop` (#520, since 0.12.2) is on the row -- `PaperTrader.on_candle`
    is what actually enforces it -- so the column names that stop rather than a NULL venue order
    paper can never place."""
    report = _status_report(
        mode="paper", open_positions=[_position(has_bracket=False, initial_stop=Decimal("64000"))]
    )
    bracket = _pulse_payload(report)["open_positions"][0]["bracket"]
    assert bracket["state"] == "neutral"
    assert "64,000" in bracket["display"]


def test_paper_position_without_a_recorded_stop_falls_back_to_mode_aware_text() -> None:
    """A tranche that predates the v12 migration, or a DCA leg that never recorded one
    (`repository._position_row_to_dict`'s own note), has no stop to show -- it names the
    mechanism instead of inventing a number (option 2 of the issue's preference order)."""
    report = _status_report(
        mode="paper", open_positions=[_position(has_bracket=False, initial_stop=None)]
    )
    bracket = _pulse_payload(report)["open_positions"][0]["bracket"]
    assert bracket["state"] == "neutral"
    assert "candle touch" in bracket["display"]


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
    position = _pulse_payload(_status_report())["open_positions"][0]

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
    built = _pulse_payload(_status_report())

    assert built["drawdown"]["total"]["value"] == "0.05"
    assert "%" not in built["drawdown"]["total"]["display"]
    assert built["drawdown"]["max_total"]["value"] == "0.20"


def test_a_win_rate_float_is_re_encoded_not_recomputed() -> None:
    """`RuleTrackRecord.win_rate` is a `float` upstream -- a statistic, never money. It reaches the
    wire through its own shortest round-trip repr, so the figure on the wire is the figure the
    report held and nothing was recomputed on the way."""
    built = payload.rules_payload(_rule_rows(), records=_insights_report())

    assert built["records"][0]["win_rate"]["value"] == "41.5"
    assert built["records"][0]["win_rate"]["display"] == "41.5%"


# -- Rule 3: semantic state is a field, never an inference ----------------------------------------


def _signed_records() -> list[dict[str, Any]]:
    """Two track records, one losing and one winning, as `rules_payload` sends them."""
    losing = replace(_insights_report().rules[0], rule_name="losing", expectancy=Decimal("-12.34"))
    winning = replace(_insights_report().rules[0], rule_name="winning", expectancy=Decimal("41"))
    records = _insights_report(rules=[losing, winning])
    return payload.rules_payload([], records=records)["records"]


def test_a_loss_is_labelled_bad_in_python_not_by_the_sign_on_the_wire() -> None:
    """Rule 3. The client must never read a minus sign and conclude "bad" -- that is arithmetic by
    another name, and it relocates a trading judgement into a browser."""
    loss, gain = _signed_records()

    assert loss["expectancy"]["state"] == "bad"
    assert loss["expectancy"]["value"] == "-12.34"
    assert gain["expectancy"]["state"] == "good"


def test_state_survives_without_colour() -> None:
    """#532: colour must never be the only signal. `display` carries a glyph and an explicit sign,
    so profit and loss stay distinguishable in greyscale, on e-ink, in sunlight, and to the
    roughly one man in twelve who cannot separate the palette's red from its green."""
    loss, gain = _signed_records()

    assert loss["expectancy"]["display"] == "▼ −$12.3400"
    assert gain["expectancy"]["display"] == "▲ +$41.0000"


def test_the_displayed_sign_and_the_state_never_disagree() -> None:
    """Rule 3's premise, checked rather than assumed: if `display` shows a minus, `state` must not
    be calling the same value neutral.

    It failed on negative zero. `format(Decimal("-0.00"), ",.2f")` is `"-0.00"`, so a sign read
    off the formatted TEXT said negative while `state`, read off the exact value, said neutral --
    `Decimal("-0.00") == 0`. A client styling by `state` and showing `display` printed a minus
    sign in a neutral colour. There is now one reading of the sign, `_is_negative`, feeding both.

    A value that is genuinely negative but rounds to zero at the display precision keeps its
    minus, and keeps `bad` alongside it. That is not a disagreement: it is a small loss, honestly
    labelled and honestly styled.
    """
    cases = [
        Decimal("-0.00"),
        Decimal("0.00"),
        Decimal("-0.001"),
        Decimal("-12.34"),
        Decimal("41.00"),
        Decimal("0E-8"),
    ]

    for value in cases:
        field = payload.money(value, signed=True)
        shows_negative = payload.MINUS in field["display"] or payload.DOWN in field["display"]
        assert shows_negative == (field["state"] == "bad"), (value, field)

        unsigned = payload.money(value)
        assert (payload.MINUS in unsigned["display"]) == (value < 0), (value, unsigned)


def test_negative_zero_is_not_negative_anywhere_it_is_rendered() -> None:
    """`Decimal("-0.00")` is zero, and every builder that renders a sign must agree."""
    assert payload.money(Decimal("-0.00"))["display"] == "$0.00"
    assert payload.money(Decimal("-0.00"), signed=True)["state"] == "neutral"
    assert payload.percent(Decimal("-0.00"))["display"] == "0.00%"
    assert payload.ratio(Decimal("-0.00"))["display"] == "0.00"
    assert payload.quantity(Decimal("-0.00"))["display"] == "0"
    # ...and the exact value still crosses untouched: nothing was normalised away to achieve it.
    assert payload.money(Decimal("-0.00"))["value"] == "-0.00"


def test_a_loss_too_small_to_show_keeps_its_sign_and_its_judgement() -> None:
    """The other side of the same rule. `-0.001` at two places renders `0.00`, and suppressing the
    minus there would report a loss as a break-even. It shows, and `state` agrees."""
    field = payload.money(Decimal("-0.001"), signed=True)

    assert field["display"] == "▼ −$0.00"
    assert field["state"] == "bad"
    assert field["value"] == "-0.001"


def test_the_closed_trade_count_is_read_from_the_report_not_measured_here() -> None:
    """The count beside the track records is `InsightsReport.closed_trade_count`, a reading the
    report makes -- not a `len()` of anything the serialiser holds (Rule 6e). One record over 812
    closed trades makes a measured count impossible to pass off as the read one."""
    built = payload.rules_payload(_rule_rows(), records=_insights_report(closed_trade_count=812))

    assert built["closed_trade_count"]["value"] == "812"
    assert len(built["records"]) == 1


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
    engaged = _pulse_payload(_status_report(kill_switch_engaged=True))["kill_switch"]
    clear = _pulse_payload(_status_report(kill_switch_engaged=False))["kill_switch"]

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
    halted = _pulse_payload(_status_report(rail11_status="HALTED"))
    unknown = _pulse_payload(_status_report(rail11_status="unknown"))
    ok = _pulse_payload(_status_report(rail11_status="ok"))

    assert halted["drawdown"]["rail11"]["state"] == "bad"
    assert unknown["drawdown"]["rail11"]["state"] == "unknown"
    assert ok["drawdown"]["rail11"]["state"] == "good"


def test_an_expired_withdrawal_attestation_reads_as_bad() -> None:
    """Rail 17's state is the one an operator must act on within a TTL; it arrives judged."""
    expired = _pulse_payload(
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

    assert event["fields"]["products"] == "1"
    assert event["fields"]["budget_usd"] == "50.00"
    assert event["fields"]["dry_run"] == "true"


def test_a_nested_log_field_crosses_as_json_never_as_a_python_repr() -> None:
    """A log line is parsed JSON, so a field's value can be a list or an object.

    The first draft fell through to `str(value)` for those, which produced `"{'a': 1, 'b': None}"`
    -- single quotes, `None`, `True`. No JSON number leaks out of that (it is one string), so the
    recursive number-walker stayed green while the payload carried text no client can parse and
    none should display. Rule 2 says values arrive presentation-ready, and a Python repr is not.

    The numbers INSIDE a nested value go through the same rendering as a top-level one, which is
    the half that actually bites: `str([1e+50])` is `"[1e+50]"` -- scientific notation reaching
    the wire through a path no money field touches, so every `Decimal` precaution in the module
    would have been irrelevant to it.
    """
    fields = payload.activity_payload(_activity_feed())["cycles"][0]["events"][0]["fields"]

    assert fields["symbols"] == '["BTC-USD", "ETH-USD"]'
    assert fields["limits"] == (
        '{"max": "100000000000000000000000000000000000000000000000000", "min": "", "on": "false"}'
    )
    assert "1e+50" not in fields["limits"]
    assert "'" not in fields["limits"] and "None" not in fields["limits"]


def test_a_pathologically_nested_log_field_cannot_recurse_the_response_to_death() -> None:
    """Depth in a log line is attacker-adjacent input: `json.loads` cannot build a cycle, but it
    can build a thousand nested lists, and a `RecursionError` while rendering one field would take
    down a whole response for a row nobody needed. Log fields are shallow by construction -- each
    is a `log_event` kwarg -- so the cap costs nothing real and the depth of the input never
    decides how deep this process goes."""
    deep: Any = "bottom"
    for _ in range(200):
        deep = [deep]

    rendered = payload.stringify(deep)

    assert "(nested)" in rendered
    assert "bottom" not in rendered


def test_an_error_count_is_bad_and_a_quiet_cycle_is_neutral() -> None:
    """A cycle in which the agent looked and nothing happened is a POSITIVE observation, not an
    absence of one -- it is how the feed answers "is it alive". So quiet is `neutral`, never
    `warn`; errors are what turn a row `bad`."""
    cycle = payload.activity_payload(_activity_feed())["cycles"][0]

    assert cycle["errors"]["state"] == "bad"
    assert cycle["quiet"]["state"] == "neutral"


# -- the config payload (#597) --------------------------------------------------------------------


def test_the_config_payload_names_the_deployment_without_judging_it() -> None:
    """`mode`, `db_path` and `config_path` cross as BARE STRINGS, not `Field`s (#597).

    Two reasons, and both are the payload's own conventions:

      * `mode` is an enum word and the paths are identifiers -- the module note on "what is NOT
        a Field" already names `mode` in that class, and `setup_payload` already sends
        `config_path`/`db_path` the same way.
      * A `Field` would force a `state`, and there is no honest one: `confirm` beside `paper` is
        neither good nor bad, and the badge's whole brief is to REPORT the served deployment.

    **The browser never changes the mode, and no key here grants it a way to.** This payload is
    served by a GET-only route (`test_no_api_route_answers_a_post`); changing `auto_trade.mode`
    is a config-file edit plus a terminal action by design, and the badge is a readout of that
    decision, never a control for it.
    """
    document = payload.config_payload(
        _build_info(),
        describe="keel 0.1.0+abc [checkout]",
        mode="confirm",
        db_path="/tmp/keel/keel.db",
        config_path="/tmp/keel/config.yaml",
    )

    assert document["mode"] == "confirm"
    assert document["db_path"] == "/tmp/keel/keel.db"
    assert document["config_path"] == "/tmp/keel/config.yaml"
    # And every new key is a string, so the no-JSON-numbers walk holds over the growth.
    assert not _json_numbers(document)


# -- venue readiness (#233 PR4) --------------------------------------------------------------------


def test_readiness_is_a_sibling_key_never_a_field_on_a_venues_row() -> None:
    """The separation `keel.venue_readiness`'s module docstring states: `venues` stays exactly
    the capability-declaration shape `_venue_payload` always built; `readiness` is a second,
    top-level key, never merged onto a `venues` row."""
    document = payload.venues_payload([_BrokerInfo()], _READINESS_ROWS)
    assert set(document) == {"venues", "readiness"}
    for row in document["venues"]:
        assert "state" not in row
        assert "readiness" not in row
        assert "explanation" not in row


def test_readiness_defaults_to_empty_so_every_existing_caller_keeps_working() -> None:
    """`venues_payload(infos)` with no second argument -- the call every caller made before
    #233 PR4 -- must still answer, with `readiness: []` rather than a `TypeError`."""
    document = payload.venues_payload([_BrokerInfo()])
    assert document["readiness"] == []


@pytest.mark.parametrize(
    ("state", "expected_tone"),
    [
        (VenueReadiness.READY, "good"),
        (VenueReadiness.NOT_INSTALLED, "warn"),
        (VenueReadiness.NO_CREDENTIALS, "warn"),
        (VenueReadiness.MALFORMED_CREDENTIALS, "bad"),
        (VenueReadiness.NOT_PERMITTED, "bad"),
    ],
)
def test_each_readiness_state_carries_the_brief_s_good_warn_bad_judgement(
    state: VenueReadiness, expected_tone: str
) -> None:
    """Pinned exactly as the brief states it: `ready` good; `malformed_credentials` and
    `not_permitted` bad; `no_credentials` and `not_installed` warn."""
    row = VenueReadinessRow(venue="v", state=state, explanation="x", next_step=None)
    document = payload.venues_payload([], [row])
    assert document["readiness"][0]["state"]["state"] == expected_tone


def test_a_readiness_row_names_its_state_explanation_and_next_step() -> None:
    row = VenueReadinessRow(
        venue="robinhood",
        state=VenueReadiness.MALFORMED_CREDENTIALS,
        explanation="ROBINHOOD_API_KEY_CREDENTIAL holds the base64 PUBLIC key",
        next_step="fix the value and re-set it",
    )
    document = payload.venues_payload([], [row])
    carried = document["readiness"][0]
    assert carried["venue"] == "robinhood"
    assert carried["state"]["value"] == "malformed_credentials"
    assert carried["state"]["display"] == "malformed credentials"
    assert carried["explanation"] == "ROBINHOOD_API_KEY_CREDENTIAL holds the base64 PUBLIC key"
    assert carried["next_step"] == "fix the value and re-set it"


def test_a_ready_row_carries_an_empty_next_step_not_null() -> None:
    """`next_step` is `None` on the dataclass for `READY` (nothing left to do); on the wire it
    is `""`, the same absent-but-a-string convention `_venue_payload`'s own `package_version`
    uses -- never a JSON `null` a client would have to special-case."""
    row = VenueReadinessRow(venue="v", state=VenueReadiness.READY, explanation="x", next_step=None)
    document = payload.venues_payload([], [row])
    assert document["readiness"][0]["next_step"] == ""


# -- pulse (console PR1) -------------------------------------------------------------------------


def test_the_pulse_payload_carries_no_json_number() -> None:
    document = json.loads(json.dumps(payload.pulse_payload(_pulse_report(), gate="g")))
    assert _json_numbers(document) == []
    # the walk saw the sections it is meant to guard, not an empty shell
    assert document["today"]["cycles"]["value"] == "3"
    assert document["next_dca"] and document["open_positions"] and document["activity"]["cycles"]


def test_the_halt_block_names_the_resume_command_only_when_engaged() -> None:
    engaged = payload.pulse_payload(
        _pulse_report(
            status=_status_report(kill_switch_engaged=True), resume_command=RESUME_COMMAND
        )
    )
    clear = payload.pulse_payload(
        _pulse_report(status=_status_report(kill_switch_engaged=False), resume_command="")
    )
    assert engaged["kill_switch"]["value"] == "true"
    assert engaged["halt"]["resume_command"] == RESUME_COMMAND  # the service's constant, not a copy
    assert clear["kill_switch"]["value"] == "false"
    assert clear["halt"]["resume_command"] == ""


def test_the_gate_is_a_bare_string_and_never_a_field() -> None:
    carried = payload.pulse_payload(_pulse_report(), gate="tok-123")
    assert carried["gate"] == "tok-123"
    assert isinstance(carried["gate"], str)
    # absent unless given: the default is the empty string, not a missing key or a Field
    assert payload.pulse_payload(_pulse_report())["gate"] == ""
    # and it appears on this payload alone -- no other builder names it
    others = {k: v for k, v in _every_payload().items() if k != "pulse"}
    assert others  # the sweep reached other payloads
    for name, built in others.items():
        assert "gate" not in built, name


def test_why_not_crosses_as_a_list_of_sentences_unchanged() -> None:
    report = _pulse_report()
    assert report.why_not  # the fixture reached the builder
    assert payload.pulse_payload(report)["why_not"] == list(report.why_not)


def test_a_blocked_or_erroring_day_is_judged_here() -> None:
    busy = payload.pulse_payload(_pulse_report())["today"]
    assert busy["blocked"]["state"] == "warn"
    assert busy["errors"]["state"] == "bad"
    quiet = payload.pulse_payload(
        _pulse_report(
            today=TodayCounts(cycles=3, signals=0, blocked=0, entered=0, exited=0, errors=0)
        )
    )["today"]
    assert quiet["blocked"]["state"] == "neutral"
    assert quiet["errors"]["state"] == "neutral"


def test_an_unread_log_crosses_as_absent_counts_and_a_bad_basis_never_as_zeros() -> None:
    """**Review F3.** Every figure absent (state unknown), the basis judged `bad`, and the
    empty-reasons sentence the service's own."""
    blank = TodayCounts(
        cycles=None, signals=None, blocked=None, entered=None, exited=None, errors=None
    )
    document = payload.pulse_payload(
        _pulse_report(
            today=blank,
            today_basis="unavailable",
            why_not=(),
            why_not_none="Today's log could not be read, so reasons may be missing.",
            no_cycles_line="",
        )
    )

    assert document["no_cycles_line"] == ""
    assert sorted(document["today"]) == [
        "basis", "blocked", "cycles", "entered", "errors", "exited", "signals"
    ]  # fmt: skip
    for key in ("cycles", "signals", "blocked", "entered", "exited", "errors"):
        assert document["today"][key] == payload.absent(), key
    assert document["today"]["basis"]["state"] == "bad"
    assert document["today"]["basis"]["value"] == "unavailable"
    assert document["why_not_none"] == "Today's log could not be read, so reasons may be missing."


def test_a_partial_day_is_judged_warn_on_every_figure_and_on_the_basis() -> None:
    document = payload.pulse_payload(_pulse_report(today_basis="partial"))["today"]

    assert document["basis"]["state"] == "warn"
    for key in ("cycles", "signals", "entered", "exited"):
        assert document[key]["state"] == "warn", key
    assert document["blocked"]["state"] == "warn"
    assert document["errors"]["state"] == "bad"  # a seen error is an error whatever the coverage


def test_a_complete_day_keeps_its_neutral_judgements() -> None:
    document = payload.pulse_payload(_pulse_report())["today"]
    assert document["basis"]["state"] == "good"
    assert document["cycles"]["state"] == "neutral"


def test_next_dca_rows_carry_the_cadence_day_as_a_moment_and_the_note_once() -> None:
    built = payload.pulse_payload(_pulse_report())
    assert len(built["next_dca"]) == 1  # the fixture's one live rule reached the payload
    row = built["next_dca"][0]
    assert row["rule_id"] == "3"
    assert row["product_id"] == "ETH-USD"
    assert row["cadence"]["display"] == "every 7 days"
    assert row["budget"]["value"] == "50"
    assert row["budget_inferred"]["state"] == "warn"
    assert row["next_cadence_day"]["value"].endswith("T00:00:00Z")  # a day, not an order time
    assert built["next_dca_note"] == payload.NEXT_DCA_NOTE
    assert "daily bar closes" in payload.NEXT_DCA_NOTE
    assert "next_dca_note" not in row  # said once, beside the rows


def test_the_unbracketed_count_is_the_reports_and_judged_here() -> None:
    warned = payload.pulse_payload(_pulse_report(unbracketed_count=2))["unbracketed_count"]
    assert (warned["value"], warned["state"]) == ("2", "warn")
    none = payload.pulse_payload(_pulse_report(unbracketed_count=0))["unbracketed_count"]
    assert (none["value"], none["state"]) == ("0", "neutral")


def test_the_pulse_payload_serialises_the_status_sections_through_the_shared_helpers() -> None:
    """Pulse replaced Status, and the sections Status served are served by the SAME helpers
    (`_equity_payload`, `_drawdown_payload`, `_rule_counts_payload`) rather than copies: a Pulse
    that re-worded the drawdown would be two answers to one question."""
    status = _status_report()
    built = payload.pulse_payload(_pulse_report(status=status))

    assert built["equity"] == payload._equity_payload(status)
    assert built["drawdown"] == payload._drawdown_payload(status)
    assert built["rule_counts"] == payload._rule_counts_payload(status)
    assert built["activity"] == payload.activity_payload(_activity_feed())


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
        "pulse": payload.pulse_payload(
            _pulse_report(
                status=empty_status,
                feed=ActivityFeed(status="missing", source="/tmp/nope.log"),
                today=TodayCounts(
                    cycles=None, signals=None, blocked=None, entered=None, exited=None, errors=None
                ),
                today_basis="unavailable",
                why_not_none="Today's log could not be read, so reasons may be missing.",
                no_cycles_line="",
                last_cycle_ts=None,
                why_not=(),
                next_dca=(),
                unbracketed=(),
                unbracketed_count=0,
            )
        ),
        "holdings": payload.holdings_payload(HoldingsReport(now_ts=NOW_TS, mode="paper", rows=())),
        "rules": payload.rules_payload(
            [], records=_insights_report(rules=[], closed_trade_count=0)
        ),
        "activity": payload.activity_payload(
            ActivityFeed(status="missing", source="/tmp/nope.log")
        ),
    }

    parsed = json.loads(json.dumps(documents))
    assert _json_numbers(parsed) == []


@pytest.mark.parametrize(
    "builder",
    [
        "pulse_payload",
        "holdings_payload",
        "rules_payload",
        "activity_payload",
    ],
)
def test_every_payload_is_json_serialisable_without_a_custom_encoder(builder: str) -> None:
    """The builders return plain `dict`/`list`/`str` -- no `Decimal` reaches `json.dumps`. That
    matters beyond tidiness: `json.dumps(Decimal(...))` raises, and the natural fix a hurried
    author reaches for is `default=float`, which is the contract's exact failure mode installed as
    a convenience."""
    if builder == "rules_payload":
        # A required `records` keyword: a default would serve a Rules view with no track records
        # and no way to tell that from a deployment whose rules have not traded.
        json.dumps(payload.rules_payload(_rule_rows(), records=_insights_report()))
        return

    if builder == "pulse_payload":
        # Its own required `PulseReport` plus the optional gate: spelled out, like
        # `rules_payload`'s `records`, so no default ever stands in for either.
        json.dumps(payload.pulse_payload(_pulse_report(), gate="gate-token"))
        return

    if builder == "holdings_payload":
        json.dumps(payload.holdings_payload(_holdings_report()))
        return

    other = {
        "activity_payload": _activity_feed(),
    }[builder]

    json.dumps(getattr(payload, builder)(other))  # no cls=, no default=


# -- the audit chain on the wire (#721) ---------------------------------------------------------


def _timeline_report(**overrides: object):
    from keel.commands.timeline import TimelineReport, TimelineRow

    row = TimelineRow(
        ts=1_000,
        kind="trade",
        provenance="venue-reported",
        source="orders",
        reference="1",
        summary="filled buy BTC-USD (live)",
        product_id="BTC-USD",
        amount=None,
        amount_kind="",
        row_hash="a" * 64,
        chain_status="chained",
    )
    fields: dict[str, object] = {
        "now_ts": 2_000,
        "scope": "all",
        "scope_start_ts": None,
        "kind": "",
        "limit": None,
        "scoped_count": 1,
        "filtered_count": 1,
        "rows": (row,),
        "kinds_present": ("trade",),
        "chain_recorded": True,
        "chain_errors": (),
    }
    fields.update(overrides)
    return TimelineReport(**fields)  # type: ignore[arg-type]


def test_a_chained_row_is_green_and_shortened() -> None:
    """The hash is truncated for a cell -- the full value lives in the CSV, which is what anyone
    actually verifying one would be working from."""
    body = payload.timeline_payload(_timeline_report())
    cell = body["rows"][0]["row_hash"]

    assert cell["state"] == "good"
    assert cell["value"] == "a" * 64
    assert cell["display"].endswith("…")
    assert len(cell["display"]) < 64


def test_not_recorded_is_shown_whole_because_it_is_a_sentence() -> None:
    """Truncating "NOT RECORDED" to twelve characters and an ellipsis would make it LOOK like a
    short hash in a column of long ones -- the absence of evidence dressed as some."""
    from keel.commands.timeline import HASH_NOT_RECORDED

    report = _timeline_report()
    row = replace(report.rows[0], row_hash=HASH_NOT_RECORDED, chain_status="not chained")
    body = payload.timeline_payload(replace(report, rows=(row,)))

    cell = body["rows"][0]["row_hash"]
    assert cell["display"] == HASH_NOT_RECORDED
    assert cell["state"] == "unknown"


def test_a_row_past_a_break_is_bad_even_though_it_has_a_hash() -> None:
    """The state must come from the CHAIN, not from whether a hash is present. A client inferring
    the verdict from a 64-character string would call an unverified row evidence."""
    report = _timeline_report(chain_errors=("row 1 (1): content does not match row_hash",))
    row = replace(report.rows[0], chain_status="chain broken")
    body = payload.timeline_payload(replace(report, rows=(row,)))

    assert body["rows"][0]["row_hash"]["state"] == "bad"
    assert body["rows"][0]["chain_status"]["state"] == "bad"


def test_every_chain_status_carries_a_sentence_not_only_the_term() -> None:
    """The same rule `_PROVENANCE_NOTES` follows: on an audit surface the term of art is not the
    thing a reader can act on."""
    from keel.commands.timeline import CHAIN_STATUSES

    report = _timeline_report()
    for status in CHAIN_STATUSES:
        row = replace(report.rows[0], chain_status=status)
        cell = payload.timeline_payload(replace(report, rows=(row,)))["rows"][0]["chain_status"]
        assert cell["value"] == status
        assert cell["display"] != status, f"{status} has no sentence beside it"


def test_an_empty_chain_is_never_green() -> None:
    """No events means nothing was checked -- not that everything verified. The exact trap
    `_chain_payload` documents for the research ledger, on the trading side."""
    body = payload.timeline_payload(_timeline_report(chain_recorded=False))
    assert body["chain"]["state"] == "unknown"
    assert body["chain"]["value"] == "unverified"


def test_a_verified_chain_over_recorded_events_is_the_only_green() -> None:
    intact = payload.timeline_payload(_timeline_report())
    assert intact["chain"]["state"] == "good"

    broken = payload.timeline_payload(_timeline_report(chain_errors=("row 3: broken",)))
    assert broken["chain"]["state"] == "bad"
    assert broken["chain_errors"] == ["row 3: broken"]


# -- session identity (#704) --------------------------------------------------------------------
#
# Alpaca's one genuinely good organizing idea: paper-vs-live is visible on every page rather than
# buried in settings. Take the spine, refuse what rides on it -- their paper banner carries an
# "Open Live Account" CTA, which is a growth funnel wrapped around real money.


def _config(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "describe": "",
        "mode": "paper",
        "profile": "keel",
        "equity_state_mode": "paper",
        "autonomous": False,
        "db_path": "/x/keel.db",
        "config_path": "/x/config.yaml",
    }
    fields.update(overrides)
    return payload.config_payload(None, **fields)  # type: ignore[arg-type]


def test_the_chip_carries_the_profile_and_both_modes() -> None:
    """The three facts that answer "which deployment is this browser looking at", on the one
    endpoint every view reads."""
    body = _config()
    assert body["profile"] == "keel"
    assert body["mode"] == "paper"
    assert body["equity_state"]["value"] == "paper"


def test_an_unrecorded_equity_state_is_unknown_not_paper() -> None:
    """`equity_state_mode` is set on the first mode flip, so a deployment that has never run has
    none. Rendering that as `paper` would be the console inventing the safer of the two answers
    about which account drove the drawdown scalars."""
    body = _config(equity_state_mode="")
    assert body["equity_state"] == {
        "value": "",
        "display": "not recorded",
        "state": "unknown",
    }, "an em-dash here drops the half the banner asks the operator to verify"


def test_paper_mode_gets_the_exact_banner_sentence() -> None:
    """Fixed wording, asserted verbatim. This is the sentence that stands between an operator and
    mistaking a simulation for their account."""
    assert _config(mode="paper")["banner"] == "PAPER — no real money is involved"


def test_confirm_mode_states_the_pairing_it_is_about_to_be_verified_against() -> None:
    """Not a warning -- the configuration the operator is about to check against the venue's own
    UI. Both halves, because the mode and the equity state are separately settable and a mismatch
    between them is exactly what this banner exists to make visible."""
    banner = _config(mode="confirm", equity_state_mode="live")["banner"]
    assert "CONFIRM" in banner
    assert "live" in banner


def test_a_confirm_banner_says_so_when_the_equity_state_is_unrecorded() -> None:
    """The pairing has two halves and one of them can be missing. Printing the mode alone would
    quietly drop the half the operator is being asked to verify."""
    banner = _config(mode="confirm", equity_state_mode="")["banner"]
    assert "CONFIRM" in banner
    assert "not recorded" in banner


def test_an_unreadable_config_gets_no_banner_at_all() -> None:
    """The same refusal `modeBadge` already makes: an absent answer is not `paper`, and guessing
    a mode on a trading console is the one thing this surface must never do."""
    assert _config(mode="")["banner"] == ""


def test_an_autonomous_session_never_claims_orders_wait_for_you() -> None:
    """The finding that made this banner dangerous rather than merely incomplete.

    `keel autonomy on` is a supported, deliberate configuration, and under it orders place
    unattended. A permanent full-bleed statement saying they wait for the operator's approval is
    a false safety assurance about real money -- on the surface built to stop exactly that
    confusion, and worse than the growth funnel this issue refuses.
    """
    banner = _config(mode="confirm", equity_state_mode="live", autonomous=True)["banner"]
    assert "AUTONOMOUS" in banner
    assert "waits for your approval" not in banner
    assert "without asking" in banner


def test_the_banner_and_the_engine_agree_about_who_is_asked() -> None:
    """Two statements of ONE rule, pinned together rather than hoped about.

    `agent._effective_mode` is the authority for EXECUTION -- `"autonomous"` only when the config
    is `confirm` and the profile says so. This banner states the same pairing for DISPLAY. They
    live in different modules for good reasons (Rule 2 keeps the sentence in the payload), which
    is exactly the shape that drifts, so the sweep covers every reachable combination.
    """
    from keel.web.payload import _session_banner

    for mode in ("paper", "confirm"):
        for autonomous in (False, True):
            # `_effective_mode`'s rule, restated as the predicate rather than re-derived: an
            # autonomous cycle happens only when BOTH switches are thrown.
            engine_asks = not (mode == "confirm" and autonomous)
            banner = _session_banner(mode, "live", autonomous)
            if mode == "paper":
                continue  # paper reaches no executor mode at all; its banner says so instead
            claims_you_are_asked = "waits for your approval" in banner
            assert claims_you_are_asked == engine_asks, (
                f"mode={mode} autonomous={autonomous}: banner says "
                f"{'asked' if claims_you_are_asked else 'not asked'}, engine says "
                f"{'asked' if engine_asks else 'not asked'}"
            )


def test_the_engines_own_rule_still_matches_the_predicate_this_pins_against() -> None:
    """The sweep above restates `_effective_mode`'s rule as a predicate, so it is only worth
    anything while that restatement is true. Driven through the REAL function."""
    from keel.agent import _effective_mode

    class _Cfg:
        class auto_trade:  # noqa: N801 - mirrors the config object's attribute shape
            mode = "confirm"

    class _Profile:
        def __init__(self, autonomous: bool) -> None:
            self._autonomous = autonomous

        def is_autonomous(self, _now: int) -> bool:
            return self._autonomous

    class _Repo:
        def __init__(self, autonomous: bool) -> None:
            self._autonomous = autonomous

        def get_profile(self) -> _Profile:
            return _Profile(self._autonomous)

    assert _effective_mode(_Cfg(), _Repo(True), 0) == "autonomous"  # type: ignore[arg-type]
    assert _effective_mode(_Cfg(), _Repo(False), 0) == "confirm"  # type: ignore[arg-type]


def test_the_complete_set_of_banner_sentences_is_pinned() -> None:
    """EXHAUSTIVE, and deliberately not a blocklist of funnel words.

    The first version of this refusal listed forbidden phrases -- "open live", "go live",
    "upgrade" -- and claimed a later edit could not add a call to action quietly. It could:
    "Start real trading", "Enable live trading", "Switch to your real account" and four other
    realistic sentences all passed it. A blocklist can only refuse the wordings someone thought
    of.

    `_session_banner` is a pure function of three values with a small finite domain, so the honest
    pin is the whole output set. Any new sentence -- a call to action among them -- fails this
    test and has to be added here deliberately, in a diff a reviewer reads.
    """
    from keel.web.payload import _session_banner

    produced = {
        _session_banner(mode, state, autonomous)
        for mode in ("paper", "confirm", "live", "")
        for state in ("paper", "live", "")
        for autonomous in (False, True)
    }
    assert produced == {
        "",
        "PAPER — no real money is involved",
        "CONFIRM — every order is previewed and waits for your approval; equity state paper",
        "CONFIRM — every order is previewed and waits for your approval; equity state live",
        "CONFIRM — every order is previewed and waits for your approval; equity state not recorded",
        "CONFIRM · AUTONOMOUS — orders place without asking you; equity state paper",
        "CONFIRM · AUTONOMOUS — orders place without asking you; equity state live",
        "CONFIRM · AUTONOMOUS — orders place without asking you; equity state not recorded",
        # `live` is not a config mode today (`_VALID_AUTO_TRADE_MODES` is paper/confirm) and is
        # carried the way `MODE_CLASS` carries it: forward-compatibly, ungraded, still rendering.
        "LIVE — every order is previewed and waits for your approval; equity state paper",
        "LIVE — every order is previewed and waits for your approval; equity state live",
        "LIVE — every order is previewed and waits for your approval; equity state not recorded",
    }


# -- the plans page (#706) -----------------------------------------------------------------------


def test_a_shipped_tier_reads_as_something_that_exists() -> None:
    """#706. The state comes off the STATUS WORD, not off `available_now`.

    The first cut read `GOOD if tier.available_now else UNKNOWN`, so the day a Pro tier shipped,
    the one row that had become real would have worn the same "not a thing yet" badge as the three
    that had not -- while the docstring beside it asserted every other row is unknown because it is
    "simply not a thing yet". `running` and `shipped` are both things that EXIST; the difference
    between them is price, not reality.
    """
    from dataclasses import replace

    from keel.commands.plans import gather_plans

    report = gather_plans()
    shipped = replace(report, tiers=tuple(replace(t, shipped=True) for t in report.tiers))
    statuses = {
        row["status"]["value"]: row["status"] for row in payload.plans_payload(shipped)["tiers"]
    }

    assert set(statuses) == {"shipped"}
    assert statuses["shipped"]["state"] == "good"
    # And a sentence, not the word again: this is the one status a future PR will actually set.
    assert statuses["shipped"]["display"] != "shipped"


def test_a_planned_tier_is_unknown_and_never_a_warning() -> None:
    """There is no product to caution anyone about. `WARN` would read as a caveat on something a
    reader might buy."""
    from keel.commands.plans import gather_plans

    rows = payload.plans_payload(gather_plans())["tiers"]
    planned = [row["status"] for row in rows if row["status"]["value"] == "planned"]
    assert len(planned) == 3
    assert {status["state"] for status in planned} == {"unknown"}


# -- the DCA plan card (`/api/dca-plan`, read-only) -----------------------------------------------

_DCA_KEYS = {
    "state",
    "command",
    "summary",
    "cap_check",
    "fee_rate",
    "cap_note",
    "buys",
    "buy_count",
    "excluded",
    "existing",
    "blockers",
    "warnings",
    "screen_age",
}


def _dca_plan(valid_config_path: Path, *, cap: str | None = "600", rejected: tuple[str, ...] = ()):
    """A real `build_dca_plan` over conftest's VALID_CONFIG_YAML (BTC/ETH/PAXG at .4/.3/.3),
    through the service's own test helpers -- the payload is checked against figures the SERVICE
    computed, never against literals restated here."""
    from keel.commands.dca_plan import build_dca_plan, parse_plan_inputs
    from tests.commands.test_dca_plan import NOW_TS as DCA_NOW_TS
    from tests.commands.test_dca_plan import _config, _repo, _screen
    from tests.conftest import attest_subscription

    repo = _repo()
    if cap is not None:
        attest_subscription(
            repo,
            now_ts=DCA_NOW_TS,
            free_volume_usd=None if cap == "unlimited" else Decimal(cap),
        )
    return build_dca_plan(
        repo,
        _config(valid_config_path),
        parse_plan_inputs("500", "0.1"),
        venue="coinbase",
        now_ts=DCA_NOW_TS,
        screen_fn=_screen(*rejected),
    )


def test_the_dca_payload_has_one_shape_in_both_states(valid_config_path: Path) -> None:
    """Rule 3: a client reads one shape and never branches on which state produced it."""
    from keel.commands.dca_plan import apply_command

    ready = payload.dca_plan_payload(_dca_plan(valid_config_path), command="keel dca plan ...")
    awaiting = payload.dca_plan_awaiting_payload(command=apply_command(None))
    assert set(ready) == set(awaiting) == _DCA_KEYS
    assert set(ready["summary"]) == set(awaiting["summary"])
    assert awaiting["state"]["value"] == "awaiting_budget"
    assert awaiting["state"]["state"] == "unknown"
    assert awaiting["buys"] == [] and awaiting["summary"]["spend"]["state"] == "unknown"
    assert awaiting["cap_check"]["state"] == "unknown"


#: #856 review: what `screen_age` carries when no screen was run for the response -- a judged
#: field with its own words, so the card's note line never shows a lone, unlabeled "—".
_NOT_SCREENED = {
    "value": "not_screened",
    "display": "Asset screen: not run yet -- it runs once a budget is entered.",
    "state": "unknown",
}


def test_the_dca_payload_awaiting_state_says_no_screen_ran(valid_config_path: Path) -> None:
    """#856: no budget means `read_dca_plan` never reaches the screen cache, so `screen_age` names
    that in words -- never a manufactured age for a screen that was not built, and never a bare
    "—" the card would place as an unlabeled line."""
    from keel.commands.dca_plan import apply_command

    awaiting = payload.dca_plan_awaiting_payload(command=apply_command(None))
    assert awaiting["screen_age"] == _NOT_SCREENED


def test_the_dca_payload_without_a_screen_age_never_claims_a_fresh_screen(
    valid_config_path: Path,
) -> None:
    """#856 review: a caller that passes no screen age gets the not-screened field, not a silent
    default of "0s ago" claiming a screen nobody measured."""
    body = payload.dca_plan_payload(_dca_plan(valid_config_path), command="x")
    assert body["screen_age"] == _NOT_SCREENED


def test_the_dca_payload_carries_the_screen_age_as_structure(valid_config_path: Path) -> None:
    """#856: the exact field shape a stale-but-cached screen sends -- checked field by field, not
    by scanning the rendered sentence for a substring. The refresh interval is the caller's TTL,
    written by the same `_human_remaining` ladder, never a second copy of the constant."""
    plan = _dca_plan(valid_config_path)
    body = payload.dca_plan_payload(
        plan, command="x", screen_age_seconds=125, screen_ttl_seconds=300
    )
    assert body["screen_age"] == {
        "value": "125",
        "display": "Asset screen from 2m ago; refreshes every 5m.",
        "state": "neutral",
    }
    # A freshly-built screen (age 0) is phrased the same way `_human_age(0)` phrases any instant.
    fresh = payload.dca_plan_payload(
        plan, command="x", screen_age_seconds=0, screen_ttl_seconds=300
    )
    assert fresh["screen_age"] == {
        "value": "0",
        "display": "Asset screen from 0s ago; refreshes every 5m.",
        "state": "neutral",
    }
    # The interval follows the TTL it is handed.
    longer = payload.dca_plan_payload(
        plan, command="x", screen_age_seconds=0, screen_ttl_seconds=600
    )
    assert longer["screen_age"]["display"] == "Asset screen from 0s ago; refreshes every 10m."


def test_the_dca_payload_places_every_figure_the_plan_computed(valid_config_path: Path) -> None:
    from keel.commands.dca_plan import RAIL14_NOTE, REASON_TEXT

    plan = _dca_plan(valid_config_path, rejected=("PAXG",))
    body = payload.dca_plan_payload(plan, command="keel dca plan --budget 500 --buffer-pct 0.1")
    assert body["state"]["value"] == "ready" and body["state"]["state"] == "good"
    assert body["buy_count"]["value"] == str(plan.buy_count) == "2"
    # Pairings, row by row, against the plan's own buys -- never a count alone.
    assert [row["asset"] for row in body["buys"]] == [b.asset for b in plan.buys] == ["BTC", "ETH"]
    for row, buy in zip(body["buys"], plan.buys, strict=True):
        assert row["product_id"] == buy.product_id
        assert row["per_buy"]["value"] == format(buy.per_buy_usd, "f")
        assert row["monthly"]["value"] == format(buy.monthly_usd, "f")
        assert row["fee"]["value"] == format(buy.est_monthly_fee_usd, "f")
        assert row["weight"]["display"] == format(buy.weight_pct, "f") + "%"
        assert row["cadence"]["display"] == "every 7 days"
        assert row["min_order"]["state"] == "unknown"
    assert body["summary"]["spend"]["value"] == format(plan.spend_usd, "f") == "450.00"
    assert body["summary"]["planned"]["value"] == format(plan.planned_monthly_usd, "f")
    assert body["summary"]["fees"]["value"] == format(plan.est_monthly_fees_usd, "f")
    assert plan.cap.allowance_usd is not None
    assert body["summary"]["cap"]["value"] == format(plan.cap.allowance_usd, "f")
    assert body["summary"]["cap"]["display"] == "$600.00"
    assert body["summary"]["cap"]["state"] == "good"
    assert body["fee_rate"]["display"] == "1.2% (configured fees.taker_pct)"
    assert body["cap_note"] == RAIL14_NOTE
    (excluded,) = body["excluded"]
    (reason,) = plan.excluded[0].reasons
    assert excluded["asset"] == "PAXG" and excluded["reasons"] == REASON_TEXT[reason]
    assert body["warnings"] == list(plan.warnings)
    assert body["blockers"] == []


def test_the_dca_payload_carries_the_worst_month_cap_check(valid_config_path: Path) -> None:
    """#847: the figure rail 14 is checked against is the WORST calendar month, and the card must
    say so -- the same sentence the CLI prints, with the plan's own checked total as its value."""
    from keel.commands.dca_plan import worst_month_text

    fits = _dca_plan(valid_config_path, cap="600")
    body = payload.dca_plan_payload(fits, command="x")
    assert body["cap_check"]["display"] == worst_month_text(fits)
    assert body["cap_check"]["value"] == format(fits.worst_month_spend_usd, "f") == "517.35"
    assert body["cap_check"]["state"] == "good"

    over = _dca_plan(valid_config_path, cap="500")
    blocked = payload.dca_plan_payload(over, command="x")
    assert blocked["cap_check"]["state"] == "bad"
    assert blocked["cap_check"]["display"] == worst_month_text(over)


def test_an_unlimited_cap_has_nothing_to_check_the_worst_month_against(
    valid_config_path: Path,
) -> None:
    """An unlimited tier (`free_volume_usd=None`): the sentence is still shown, the cap reads
    "unlimited", and the check is judged neither good nor bad -- there is nothing to exceed."""
    from keel.commands.dca_plan import worst_month_text

    plan = _dca_plan(valid_config_path, cap="unlimited")
    assert plan.cap.allowance_usd is None  # the fixture reached the unlimited branch
    body = payload.dca_plan_payload(plan, command="x")
    assert body["cap_check"]["state"] == "neutral"
    assert body["cap_check"]["display"] == worst_month_text(plan)
    assert body["summary"]["cap"]["display"] == "unlimited"


def test_a_blocked_plan_says_so_in_state(valid_config_path: Path) -> None:
    plan = _dca_plan(valid_config_path, cap="100")
    body = payload.dca_plan_payload(plan, command="x")
    assert body["state"]["value"] == "blocked" and body["state"]["state"] == "bad"
    assert body["blockers"] == list(plan.blockers) and len(body["blockers"]) == 1


def test_an_unattested_cap_is_judged_bad(valid_config_path: Path) -> None:
    body = payload.dca_plan_payload(_dca_plan(valid_config_path, cap=None), command="x")
    assert body["summary"]["cap"]["state"] == "bad"
    assert body["state"]["value"] == "blocked"


def test_a_degraded_cap_says_why_as_the_cli_does(valid_config_path: Path) -> None:
    """Review of #850: the CLI prints `$0.00 (because no subscription has been attested)`; the
    card must carry the same reason, in the cap figure itself -- the service puts it in a blocker
    only when the worst month exceeds the cap."""
    plan = _dca_plan(valid_config_path, cap=None)
    assert plan.cap.degraded_reason != ""  # the fixture reached the degraded branch
    cap = payload.dca_plan_payload(plan, command="x")["summary"]["cap"]
    assert cap["display"] == f"$0.00 (because {plan.cap.degraded_reason})"
    assert plan.cap.allowance_usd is not None
    assert cap["value"] == format(plan.cap.allowance_usd, "f") and cap["state"] == "bad"


def test_an_existing_dca_rule_is_sent_as_a_row(valid_config_path: Path) -> None:
    from keel.commands.dca_plan import build_dca_plan, parse_plan_inputs
    from tests.commands.test_dca_plan import NOW_TS as DCA_NOW_TS
    from tests.commands.test_dca_plan import _config, _insert_dca, _repo, _screen

    repo = _repo()
    rule_id = _insert_dca(repo, "ETH-USD", "candidate", budget="40")
    plan = build_dca_plan(
        repo,
        _config(valid_config_path),
        parse_plan_inputs("500", "0.1"),
        venue="coinbase",
        now_ts=DCA_NOW_TS,
        screen_fn=_screen(),
    )
    (row,) = payload.dca_plan_payload(plan, command="x")["existing"]
    assert row == {
        "rule_id": str(rule_id),
        "product_id": "ETH-USD",
        "status": "candidate",
        "per_buy": payload.money(Decimal("40")),
        "cadence": payload.label("7", display="every 7 days"),
        "dip_bonus": "",
    }


def test_an_existing_rules_dip_bonus_is_sent_as_the_cli_prints_it(valid_config_path: Path) -> None:
    """Review of #850: the CLI marks the row `(dip_bonus_pct X)`, since such a rule's buys can
    exceed its flat per-buy amount; the card's row must say so too."""
    from keel.commands.dca_plan import build_dca_plan, parse_plan_inputs
    from tests.commands.test_dca_plan import NOW_TS as DCA_NOW_TS
    from tests.commands.test_dca_plan import _config, _insert_dca, _repo, _screen

    repo = _repo()
    _insert_dca(repo, "ETH-USD", "candidate", budget="40", dip="2.5")
    plan = build_dca_plan(
        repo,
        _config(valid_config_path),
        parse_plan_inputs("500", "0.1"),
        venue="coinbase",
        now_ts=DCA_NOW_TS,
        screen_fn=_screen(),
    )
    (rule,) = plan.existing
    assert rule.dip_bonus_pct == Decimal("2.5")  # the fixture's bonus reached the plan
    (row,) = payload.dca_plan_payload(plan, command="x")["existing"]
    assert row["dip_bonus"] == "dip_bonus_pct 2.5"


def test_no_dca_wire_value_is_a_json_number_and_none_says_fee_free(
    valid_config_path: Path,
) -> None:
    from keel.commands.dca_plan import apply_command

    for body in (
        payload.dca_plan_payload(_dca_plan(valid_config_path, rejected=("PAXG",)), command="x"),
        payload.dca_plan_awaiting_payload(command=apply_command(None)),
    ):
        assert _json_numbers(body) == []
        assert "fee-free" not in json.dumps(body).lower()


def test_the_no_cycles_line_crosses_exactly_as_the_service_worded_it() -> None:
    """**Review R2-F1.** The client appends the empty-today card on the PRESENCE of this string."""
    line = "keel has not run yet today."
    assert payload.pulse_payload(_pulse_report(no_cycles_line=line))["no_cycles_line"] == line
    assert payload.pulse_payload(_pulse_report(no_cycles_line=""))["no_cycles_line"] == ""


# -- holdings: the Holdings view's tranche rows -------------------------------------------------

_HOLDING_FILL = Decimal("100050")


def _holdings_report(
    *,
    order: dict[str, Any] | None = None,
    tranche: dict[str, Any] | None = None,
    mode: str | None = None,
    extra_tranche: dict[str, Any] | None = None,
) -> HoldingsReport:
    """A real `gather_holdings` over a throwaway ledger, so every verdict on the wire was made by
    the service. The order is seeded in the spelling the real writers use (`Side.BUY.value`)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        from tests.conftest import VALID_CONFIG_YAML

        cfg_path = Path(tmp) / "config.yaml"
        cfg_path.write_text(VALID_CONFIG_YAML, encoding="utf-8")
        config = load_config(str(cfg_path))
        conn = connect(str(Path(tmp) / "keel.db"))
        migrate(conn)
        repo = Repository(conn)
        if order is not None:
            row: dict[str, Any] = {
                "mode": "live",
                "product_id": "BTC-USD",
                "side": Side.BUY.value,
                "order_type": "market",
                "qty": Decimal("2"),
                "limit_price": None,
                "status": "filled",
                "fee": Decimal("1.20"),
                "expected_fill": Decimal("100000"),
                "actual_fill": _HOLDING_FILL,
                "filled_quantity": Decimal("2"),
                "raw_response": None,
                "confirmation": "autonomous",
                "rule_id": None,
                "created_at": NOW_TS - 7200,
                "updated_at": NOW_TS - 7200,
            }
            row.update(order)
            repo.insert_order(row)
        for spec in (tranche or {}, *([extra_tranche] if extra_tranche is not None else [])):
            fields: dict[str, Any] = {
                "product_id": "BTC-USD",
                "rule_name": "turtle_breakout",
                "opened_at": NOW_TS - 7200,
                "qty": Decimal("2"),
                "entry_fill": _HOLDING_FILL,
                "entry_fee": Decimal("1.20"),
                "initial_stop": Decimal("90000"),
            }
            fields.update(spec)
            repo.open_position(**fields)
        report = gather_holdings(repo, config, now_ts=NOW_TS)
        conn.close()
    return replace(report, mode=mode) if mode is not None else report


_HOLDING_ROW_KEYS = [
    "id",
    "product_id",
    "rule_name",
    "opened_at",
    "qty",
    "entry_fill",
    "entry_fee",
    "cost",
    "stop",
    "bracket",
    "freshness",
    "attestation",
    "attest_due_at",
    "placement",
    "expected_fill",
    "actual_fill",
    "fill_divergence",
    "entry_order_id",
    "realized_qty",
    "realized_proceeds",
    "realized_fees",
]

_HOLDINGS_TOP_KEYS = [
    "as_of",
    "generated_at",
    "mode",
    "open_count",
    "rules",
    "stop_rows_present",
    "rows",
]


def test_no_wire_value_in_the_holdings_payload_is_a_json_number() -> None:
    document = json.loads(
        json.dumps(payload.holdings_payload(_holdings_report(order={}, tranche={})))
    )
    assert document["rows"]  # the fixture reached the serialiser
    assert _json_numbers(document) == []


def test_the_holdings_payload_and_its_rows_have_exactly_these_keys() -> None:
    document = payload.holdings_payload(_holdings_report(order={}))
    assert sorted(document) == sorted(_HOLDINGS_TOP_KEYS)
    (row,) = document["rows"]
    assert sorted(row) == sorted(_HOLDING_ROW_KEYS)


def test_the_holdings_top_level_reads_the_report() -> None:
    document = payload.holdings_payload(_holdings_report(order={}, mode="confirm"))
    assert document["mode"] == "confirm"
    assert document["open_count"]["value"] == "1"
    assert document["rules"] == ["turtle_breakout"]
    assert document["stop_rows_present"] == [document["rows"][0]["id"]]


def test_a_matched_tranche_carries_its_entry_orders_figures() -> None:
    (row,) = payload.holdings_payload(_holdings_report(order={}))["rows"]
    assert row["entry_order_id"] != ""
    assert row["placement"]["display"] == "AUTONOMOUS"
    assert row["expected_fill"]["value"] == "100000"
    assert row["actual_fill"]["value"] == "100050"
    assert row["fill_divergence"]["value"] == "50"


def test_a_tranche_without_an_entry_order_crosses_as_unknown_placement_and_unknown_slippage() -> (
    None
):
    (row,) = payload.holdings_payload(_holdings_report(order=None))["rows"]
    assert row["entry_order_id"] == ""
    assert row["placement"]["display"] == ENTRY_ORDER_NOT_IDENTIFIED
    assert row["placement"]["state"] == "unknown"
    for key in ("expected_fill", "actual_fill", "fill_divergence"):
        assert row[key] == {"value": "", "display": "unknown", "state": "unknown"}, key
        assert row[key]["display"] not in ("0", "$0.00", "—")


@pytest.mark.parametrize(
    ("actual", "state"),
    [("100050", "bad"), ("99950", "good")],
)
def test_the_holdings_slippage_state_is_the_services_verdict_not_the_sign(
    actual: str, state: str
) -> None:
    """A buy that filled above its expectation is adverse (BAD); below it, favourable (GOOD)."""
    row = payload.holdings_payload(
        _holdings_report(
            order={"actual_fill": Decimal(actual)}, tranche={"entry_fill": Decimal(actual)}
        )
    )["rows"][0]
    assert row["fill_divergence"]["state"] == state


def test_a_stop_is_absent_not_zero_when_none_was_recorded() -> None:
    document = payload.holdings_payload(_holdings_report(tranche={"initial_stop": None}))
    (row,) = document["rows"]
    assert row["stop"]["state"] == "unknown" and row["stop"]["value"] == ""
    assert row["stop"]["display"] != "$0.00"
    assert document["stop_rows_present"] == []


def test_cost_crosses_as_the_services_figure() -> None:
    (row,) = payload.holdings_payload(_holdings_report())["rows"]
    assert row["cost"]["value"] == "200101.20"


def test_the_holdings_payload_drops_every_mark_derived_key() -> None:
    (row,) = payload.holdings_payload(_holdings_report())["rows"]
    banned = ("mark", "market_value", "unrealized", "stop_distance", "mark_at")
    assert not [k for k in row if k.startswith(banned)]


@pytest.mark.parametrize(
    ("mode", "bracket", "stop", "display", "state"),
    [
        ("confirm", False, None, "NO bracket", "warn"),
        ("confirm", True, None, "bracketed", "good"),
        ("paper", False, Decimal("90000"), "paper stop", "neutral"),
        ("paper", False, None, "candle touch", "neutral"),
    ],
)
def test_the_bracket_judgement_is_unchanged_for_live_and_paper(
    mode: str, bracket: bool, stop: Decimal | None, display: str, state: str
) -> None:
    """The #641 judgement, now driven from a holdings row: live `NO bracket` stays WARN, paper's
    absent bracket is the mode and names its protection."""
    tranche: dict[str, Any] = {"initial_stop": stop}
    report = _holdings_report(tranche=tranche, mode=mode)
    if bracket:
        report = replace(report, rows=tuple(replace(r, has_bracket=True) for r in report.rows))
    (row,) = payload.holdings_payload(report)["rows"]
    assert display in row["bracket"]["display"]
    assert row["bracket"]["state"] == state
