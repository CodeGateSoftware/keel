"""The serialisation contract (#533): frozen report dataclasses in, browser-ready JSON out.

This module is a FOURTH renderer over the same reports, never a second place that computes them.
`keel/commands/*` builds the frozen report dataclasses and renders them to terminal lines
(`gather_status`/`render_human`, `build_insights_report`/`render_summary`);
`keel/web/render.py` renders the same dataclasses to HTML; this renders them to JSON. Nothing
here re-gathers, re-derives or re-measures anything -- which is what keeps
`tests/commands/test_console_thinness.py` able to pin this layer with the rules it already
applies to the console and the HTML renderer.

Every endpoint in the web-UI milestone (#534 onwards) serialises through here, so the three
rules below are stated once, in one file a reviewer can read end to end.

RULE 1 -- MONEY CROSSES THE WIRE AS A STRING, NEVER AS A JSON NUMBER.
`JSON.parse` in a browser yields IEEE-754 doubles. keel is `Decimal`-only precisely because
binary floats corrupt money, and JSON's number type would silently undo that at the boundary --
silently being the operative word: nothing raises, nothing warns, and the error only shows up
past the seventeenth significant digit or in the last cent of a large notional.
`tests/web/test_payload.py::test_no_wire_value_is_ever_a_json_number` walks the parsed payload
and fails the build on ANY JSON number anywhere in it, which is deliberately stronger than "no
monetary field is a number": the weaker form needs a maintained list of which fields are money,
and that list is exactly the thing that rots.

**The one measured hazard this file is shaped around.** `Decimal.normalize()` renders
`Decimal("50")` as `Decimal("5E+1")`, and `"5E+1"` has previously reached the wire in this
codebase and broken real orders. `str(Decimal(...))` does the same for any Decimal with a
positive exponent. So there is exactly ONE place a `Decimal` becomes a string here -- `_plain`,
which uses `format(value, "f")`, the only formatting path that cannot emit an exponent -- and
`normalize()` appears nowhere. `tests/commands/test_console_thinness.py`'s Rule 6 fails the build
if `normalize`, `float()` or `round()` is ever added to this module; `test_payload.py` checks the
absence at the level of the rendered payload as well, because an AST rule and a string check fail
for different reasons and neither subsumes the other.

*An integer companion field scaled to cents was considered and rejected* (spec, § The data
contract). Precision here is PER-PRODUCT -- `base_increment` varies by instrument, which is what
#514 and #517 were about -- so a fixed 100x scale silently truncates anything finer than a cent,
and a 1e8 scale caps a USD notional near `Number.MAX_SAFE_INTEGER`. Should instant client-side
re-sorting ever be wanted, that field is named `sort`, is a plain JSON number, and is documented
as *ordering only -- never displayed, never summed*. It is not in this issue, and the guard test
above is where its allowance would have to be written down.

RULE 2 -- VALUES ARRIVE PRESENTATION-READY; THE CLIENT PLACES THEM, NEVER DERIVES THEM.
Not `{"qty": "0.01", "price": "50000"}` for a client to multiply. Every figure a user will see is
formatted here, by the process that holds the trading rails, so the client needs no decimal
library at all and a reviewer can confirm the absence of client-side money arithmetic by reading
one file.

The line between FORMATTING (allowed, and the whole job of this module) and COMPUTING
(forbidden) is: a figure on the wire must already exist on the report. `format`, grouping
separators, trailing-zero trimming, currency symbols and glyphs are formatting. Multiplication,
addition, rescaling and unit conversion are computing, and they belong upstream in the report
builder where the rails can see them. Two places where that line was live while writing this:

* **An open position has no notional here.** `OpenPositionStatus` carries `qty` and
  `entry_price` and nothing else numeric, so `qty * entry_price` was one multiplication away --
  and the spec's own illustrative payload shows a `notional` on a position. It is not emitted.
  If a notional is wanted, it is added to `gather_status`.
* **A drawdown is NOT rescaled into a percentage.** `drawdown_total_pct` is named for a
  percentage but holds a FRACTION: `_rail11_status` compares it against
  `max_total_dd_pct=Decimal("0.20")`, so `0.05` means five percent. Multiplying by 100 to make
  the name true would be arithmetic in the serialiser, so it crosses unchanged through `ratio`
  and the display carries no `%`. (`render.py`'s `pct()` appends a `%` to this same raw
  fraction and therefore prints "0.05%" for a 5% drawdown; the contract does not inherit that.)

RULE 3 -- SEMANTIC STATE IS AN EXPLICIT FIELD, NEVER AN INFERENCE.
A client must never decide "this is bad" by inspecting a minus sign: that is arithmetic by
another name, and it relocates a trading judgement into a browser. Every field therefore carries
`state`, drawn from the closed vocabulary `STATES`, and the `display` string carries a glyph so
the same distinction survives without colour -- #532's palette work rests on this, and
`--good`/`--bad` in today's stylesheet are separated by hue alone (luminance ratio 1.01:1), which
fails WCAG 1.4.1 in an application whose central signal is gain versus loss.

**`state` is present on every field, including ones with nothing interesting to say
(`"neutral"`) and ones with no value at all (`"unknown"`).** This is a deliberate departure from
the spec's abbreviated example, which shows `equity` with only `value` and `display`: a client
that must test whether `state` is present is branching on payload SHAPE, which is inference by
another route, and #532's styling table needs a word for every field it touches. Reversing it
would mean accepting that branch on the client, which is the thing this contract exists to
remove.

WHAT IS *NOT* A FIELD. Identifiers and enum words -- `product_id`, `rule_name`, `mode`,
`as_of` -- cross as bare JSON strings. They carry no precision hazard, no rounding decision and
no judgement, and wrapping them would be ceremony rather than contract. Bare JSON *numbers*
never appear at all: counts, timestamps and open-ended log fields all cross as strings, because
"it is only a count" is how the first double gets in.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, TypedDict

# The CLI's own words for "how long ago" and "how much longer". Imported rather than
# re-implemented so the browser and the terminal can never disagree about the age of the same
# candle -- `keel/commands/insights.py` reaches into `status.py` for `_human_age` the same way,
# for the same reason. `keel.commands.*` is the SERVICE layer, which this layer is allowed to
# read; the compute trees (`keel.strategy`, `keel.execution.*`, `keel.analysis`) are not.
from keel.commands.status import _human_age, _human_remaining

if TYPE_CHECKING:  # pragma: no cover - typing only
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


class Field(TypedDict):
    """One value, ready to place. `value` is machine input (exact, ungrouped, `Decimal`-parseable
    for figures and ISO-8601 for instants); `display` is the human's, already formatted; `state`
    is the judgement, from `STATES`."""

    value: str
    display: str
    state: str


#: The closed `state` vocabulary. Closed on purpose: #532's palette and glyph table key off
#: exactly these words, and a free-text state would push the client back into guessing.
#:
#: `"unknown"` is NOT a synonym for `"neutral"`. `_rail11_status` returns `"unknown"` when a
#: drawdown scalar was never written, explicitly because reporting an unwritten value as a
#: confident "ok" would be a lie -- there may be a real breach the agent has not computed yet.
#: Collapsing that into a calm-looking neutral would put the lie back.
STATES: frozenset[str] = frozenset({"good", "warn", "bad", "neutral", "unknown"})

GOOD = "good"
WARN = "warn"
BAD = "bad"
NEUTRAL = "neutral"
UNKNOWN = "unknown"

#: An em dash, for a value that was never recorded. `None` means "not recorded" and `0` means
#: "recorded as zero"; collapsing the first into the second is the shape of the always-passing
#: fee rail (#198) -- a missing number that reads as a real one.
ABSENT = "—"

#: U+25B2 / U+25BC, and U+2212 MINUS SIGN rather than the hyphen-minus a keyboard produces.
#: The glyphs are the non-colour half of #532's gain/loss signal; the true minus is typography.
UP = "▲"
DOWN = "▼"
MINUS = "−"


def absent() -> Field:
    """A value that was never recorded.

    A fresh dict each call, never a shared constant: every builder returns a `Field` a caller
    may put straight into a payload, and one shared mutable default would let an edit in one
    endpoint reach every other."""
    return {"value": "", "display": ABSENT, "state": UNKNOWN}


# -- the primitives ------------------------------------------------------------------------------


def _plain(value: Decimal) -> str:
    """THE one place a `Decimal` becomes a string. `format(value, "f")` is the only rendering
    that cannot emit an exponent: `str()` and `normalize()` both turn `Decimal("50")` into
    `"5E+1"`, which is the bug that has already reached the wire here. It preserves the full
    coefficient, so the result re-parses to a `Decimal` equal to the input, for any input --
    including quantities finer than a cent and notionals past a double's 17 digits."""
    return format(value, "f")


def _decimalise(value: Decimal | float | None) -> Decimal | None:
    """A finite `Decimal`, or `None` for anything that cannot honestly become one.

    A `float` arrives only from a non-monetary statistic (`RuleTrackRecord.win_rate`); money is
    `Decimal` end to end and never takes this path. `repr()` gives the shortest string that
    round-trips the float, so the figure on the wire is the figure the report held -- re-encoded,
    not recomputed. NaN/Inf (from a corrupt log timestamp, or a `Decimal("NaN")` sentinel) become
    `None` and render as absent: a payload is a worse place to raise than a cell is to be empty.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    try:
        candidate = Decimal(repr(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return candidate if candidate.is_finite() else None


def _magnitude(value: Decimal, places: int) -> tuple[bool, str]:
    """`(is_negative, unsigned_grouped_text)`.

    The sign is taken off the FORMATTED string rather than off the number (`abs()`, `-value`),
    because this layer does no arithmetic on money at all -- Rule 3 of the thinness pin, and the
    property that makes "the browser never sees a computed figure" checkable rather than merely
    likely. Note the sign here is the sign of the *rendered* text: a value that rounds to zero at
    `places` still renders "0.00". The `state` a caller attaches is derived from the exact
    `Decimal` instead, so a `-0.001` P&L is still labelled a loss.
    """
    text = format(value, f",.{places}f")
    return text.startswith("-"), text.lstrip("-")


def _trim(text: str) -> str:
    """Drop a formatted decimal's trailing zeros -- `"0.01000000"` -> `"0.01"`, `"50.00000000"` ->
    `"50"`. String work on an already-formatted number, deliberately, because the obvious
    alternative is `Decimal.normalize()` and that is the `5E+1` bug."""
    if "." not in text:
        return text
    return text.rstrip("0").rstrip(".")


def _sign_state(value: Decimal) -> str:
    """Rule 3's judgement, made HERE. A comparison, not arithmetic: the browser is told the
    answer so it never has to look at the sign itself."""
    if value < 0:
        return BAD
    if value > 0:
        return GOOD
    return NEUTRAL


def stringify(value: Any) -> str:
    """Any open-ended value, as a JSON string.

    Used for the two places keel's own reports carry values of unconstrained type:
    `ActivityEvent.fields` (every `log_event` call site invents its own kwargs, and the parser
    keeps them whole rather than projecting onto a fixed schema) and `JournalReport.filters` (the
    query echoed back). Rule 1 applies to those exactly as it does to a named money field -- an
    echoed `limit` is a JSON number like any other, and "it is only a count" is how the first
    double gets in.

    Booleans render as `"true"`/`"false"` rather than Python's `"True"`/`"False"`: the reader is
    JavaScript.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return _plain(value) if value.is_finite() else str(value)
    if isinstance(value, float):
        decimalised = _decimalise(value)
        return _plain(decimalised) if decimalised is not None else str(value)
    return str(value)


def _gmt(ts: float | int | None, fmt: str) -> str:
    """`ts` in UTC under `fmt`, or `""` for anything unrenderable.

    UTC always, and labelled as such wherever it is shown: keel's day boundaries are UTC
    everywhere -- gates, scoping, the activity feed -- and rendering in local time is what made
    the activity feed show a stale date (#381), a "today" view that could be permanently empty.

    Total by construction. Log timestamps come from outside this process, and a NaN or an
    out-of-range float must cost one cell, not the whole response. `float()` here is the one
    conversion this module makes and it is on a TIMESTAMP, which has no cent to lose --
    `time.gmtime` accepts nothing else. `test_console_thinness.py`'s Rule 6b allows it by name
    and by argument, so `float(price)` at the same site would still fail the build.
    """
    if ts is None:
        return ""
    try:
        return time.strftime(fmt, time.gmtime(float(ts)))
    except (OverflowError, OSError, ValueError):
        return ""


def iso(ts: float | int | None) -> str:
    """An instant as ISO-8601 UTC with an explicit `Z`, or `""`.

    `Z` rather than a bare naive string so `new Date(value)` in a browser cannot read it as local.
    This is what the top-level `as_of` on every payload carries.
    """
    return _gmt(ts, "%Y-%m-%dT%H:%M:%SZ")


# -- the field builders --------------------------------------------------------------------------


def money(
    value: Decimal | None,
    *,
    places: int = 2,
    signed: bool = False,
    state: str | None = None,
    symbol: str = "$",
) -> Field:
    """A monetary figure. `value` is the exact source `Decimal`; `display` is the human's.

    `signed=True` marks a GAIN-OR-LOSS figure (a net P&L, an R-multiple): it gets the glyph, an
    explicit sign, and a `state` derived from the exact source value -- which is Rule 3's whole
    point, the judgement made in Python so the client never reads a minus sign. `signed=False` is
    a magnitude (a balance, a high-water mark, a fee): no glyph, `neutral` unless the caller knows
    better and passes `state`.

    Zero is deliberately glyph-less even when `signed=True`: `▲ +$0.00` claims a direction that
    the number does not have.
    """
    figure = _decimalise(value)
    if figure is None:
        return absent()
    negative, magnitude = _magnitude(figure, places)
    if signed and figure != 0:
        glyph = DOWN if negative else UP
        sign = MINUS if negative else "+"
        display = f"{glyph} {sign}{symbol}{magnitude}"
    elif negative:
        display = f"{MINUS}{symbol}{magnitude}"
    else:
        display = f"{symbol}{magnitude}"
    resolved = state if state is not None else (_sign_state(figure) if signed else NEUTRAL)
    return {"value": _plain(figure), "display": display, "state": resolved}


def quantity(value: Decimal | None, *, unit: str = "", places: int = 8) -> Field:
    """An instrument quantity. `value` keeps the ledger's own precision -- `0.01000000`, because
    `base_increment` is per-product and truncating it is what #514 and #517 were about -- while
    `display` trims the padding a human does not read: `0.01 BTC`.

    `places=8` is Coinbase's finest base increment, not a universal truth; it is the ceiling on
    what is shown, never a rescaling of what is sent.
    """
    figure = _decimalise(value)
    if figure is None:
        return absent()
    negative, magnitude = _magnitude(figure, places)
    trimmed = _trim(magnitude)
    display = f"{MINUS}{trimmed}" if negative else trimmed
    if unit:
        display = f"{display} {unit}"
    return {"value": _plain(figure), "display": display, "state": NEUTRAL}


def percent(
    value: Decimal | float | None,
    *,
    places: int = 2,
    signed: bool = False,
    state: str | None = None,
) -> Field:
    """A figure ALREADY IN PERCENT UNITS (`41.5` meaning 41.5%), suffixed with `%`.

    Never use this for a fraction: `ratio` exists for those, and the conversion between them is
    arithmetic this layer does not do. See `ratio`'s note on `drawdown_total_pct`.
    """
    figure = _decimalise(value)
    if figure is None:
        return absent()
    negative, magnitude = _magnitude(figure, places)
    if signed and figure != 0:
        glyph = DOWN if negative else UP
        sign = MINUS if negative else "+"
        display = f"{glyph} {sign}{magnitude}%"
    elif negative:
        display = f"{MINUS}{magnitude}%"
    else:
        display = f"{magnitude}%"
    resolved = state if state is not None else (_sign_state(figure) if signed else NEUTRAL)
    return {"value": _plain(figure), "display": display, "state": resolved}


def ratio(
    value: Decimal | float | None,
    *,
    places: int = 2,
    signed: bool = False,
    state: str | None = None,
) -> Field:
    """A bare dimensionless figure -- a drawdown FRACTION, an R-multiple, a profit factor.

    Kept separate from `percent` because of a real trap: `StatusReport.drawdown_total_pct` is
    named for a percentage but holds a fraction (`_rail11_status` compares it against
    `max_total_dd_pct=Decimal("0.20")`). Rescaling it by 100 here to make the name true would be
    the serialiser computing a figure the report never held -- the exact thing Rule 2 forbids --
    so it crosses unchanged and the display carries no `%`. Making the units honest is a change
    to `gather_status`, not to this file.
    """
    figure = _decimalise(value)
    if figure is None:
        return absent()
    negative, magnitude = _magnitude(figure, places)
    if signed and figure != 0:
        glyph = DOWN if negative else UP
        sign = MINUS if negative else "+"
        display = f"{glyph} {sign}{magnitude}"
    elif negative:
        display = f"{MINUS}{magnitude}"
    else:
        display = magnitude
    resolved = state if state is not None else (_sign_state(figure) if signed else NEUTRAL)
    return {"value": _plain(figure), "display": display, "state": resolved}


def count(value: int | None, *, state: str = NEUTRAL) -> Field:
    """A whole number of things -- trades, cycles, errors, lines read.

    A count would survive JSON's number type intact, and it still crosses as a string. Two
    reasons: a payload with "only a few" numbers in it needs a per-field rule about which ones,
    and that rule is what rots; and a count is displayed, so under Rule 2 it needs a `display`
    (grouped for reading) that a bare number cannot carry.
    """
    if value is None:
        return absent()
    return {"value": str(value), "display": f"{value:,}", "state": state}


def flag(
    value: bool | None,
    *,
    on: str,
    off: str,
    on_state: str = NEUTRAL,
    off_state: str = NEUTRAL,
) -> Field:
    """A boolean an operator has to act on -- the kill switch, autonomy, a bracket.

    It arrives with the WORD to show and the state to style it by, rather than as a bare `true`
    for a client to translate. Translating is a judgement ("engaged" is bad, "autonomous" is a
    warning), and Rule 3 keeps judgements in Python.
    """
    if value is None:
        return absent()
    return {
        "value": "true" if value else "false",
        "display": on if value else off,
        "state": on_state if value else off_state,
    }


def label(value: str | None, *, display: str | None = None, state: str = NEUTRAL) -> Field:
    """An enum word that carries a judgement -- a rail status, an attestation state, an outcome.

    Distinct from a bare JSON string (a `product_id`, a `rule_name`) precisely because of the
    judgement: `state` is why this is a field.
    """
    if value is None:
        return absent()
    return {"value": value, "display": display if display is not None else value, "state": state}


def moment(ts: float | int | None, *, state: str = NEUTRAL) -> Field:
    """An instant. `value` is ISO-8601 UTC so a client can hand it straight to `new Date(...)`
    with no arithmetic; `display` is the same instant already written out, so no client ever
    formats a date to put one in a table.

    Epoch seconds were the obvious alternative for `value` and were rejected: reading them means
    `new Date(Number(value) * 1000)`, and that multiplication is client arithmetic -- small, but
    exactly the category Rule 2 exists to keep out.

    A broken timestamp (NaN, an out-of-range float, a log line written by something else)
    degrades to absent, the same choice `render.utc` already makes: a dash in one cell beats a
    500 on the page.
    """
    text = _gmt(ts, "%Y-%m-%dT%H:%M:%SZ")
    human = _gmt(ts, "%Y-%m-%d %H:%M:%S UTC")
    if not text or not human:
        return absent()
    return {"value": text, "display": human, "state": state}


def duration(seconds: int | None, *, elapsed: bool = True, state: str = NEUTRAL) -> Field:
    """A span of seconds, in the CLI's own words.

    `_human_age` ("5m ago") and `_human_remaining` ("6d") come from `keel/commands/status.py` --
    the functions `keel status` itself prints -- rather than being re-derived here, so the browser
    and the terminal can never disagree about how old the same candle is or how long an
    attestation has left.
    """
    if seconds is None:
        return absent()
    human = _human_age(seconds) if elapsed else _human_remaining(seconds)
    return {"value": str(seconds), "display": human, "state": state}


# -- state classifiers ---------------------------------------------------------------------------


def _rail_state(status: str | None) -> str:
    """Rail 11's status word, judged.

    Deliberately NOT `render._tone_for_rail`: that helper matches on "breach"/"halt"/"warn" and
    falls THROUGH to `"good"`, so `"unknown"` -- the word `_rail11_status` returns when a drawdown
    scalar was never written -- is styled as if the rail had passed. `_rail11_status`'s own
    docstring says that would be a lie, so the contract keeps `unknown` as its own state. (The
    HTML renderer's version is a separate surface and a separate fix.)
    """
    lowered = (status or "").lower()
    if not lowered or "unknown" in lowered:
        return UNKNOWN
    if "halt" in lowered or "breach" in lowered or "trip" in lowered:
        return BAD
    if "warn" in lowered or "near" in lowered:
        return WARN
    return GOOD


#: Rail 17's four states, judged. `expired` and `suspended` are the two that halt live entries;
#: `unattested` has never been answered at all, which is a gap rather than a breach.
_ATTESTATION_STATES: Mapping[str, str] = {
    "attested": GOOD,
    "suspended": BAD,
    "expired": BAD,
    "unattested": WARN,
    "unknown": UNKNOWN,
}

#: The venue clock, judged. `clock_unavailable` is a WARNING and not an error: the agent
#: fails closed on it (cycles skip), so the deployment is safe but not trading, and an operator
#: needs to know which of those two it is looking at.
_SESSION_STATES: Mapping[str, str] = {
    "open": GOOD,
    "closed": NEUTRAL,
    "clock_unavailable": WARN,
}

#: `ActivityFeed.status`, judged. `missing` is the commonest state on a fresh install and is NOT
#: an error -- it also happens when keel is run from a directory that is not the deployment
#: folder -- so it is a warning, not a failure.
_FEED_STATES: Mapping[str, str] = {
    "ok": GOOD,
    "missing": WARN,
    "empty": NEUTRAL,
    "unparseable": WARN,
    "oversized": WARN,
    "unreadable": BAD,
}


# -- status ---------------------------------------------------------------------------------------


def _autonomy_payload(autonomy: AutonomyStatus) -> dict[str, Any]:
    return {
        "live": flag(
            autonomy.live,
            on="ON — orders placed without asking",
            off="off",
            on_state=WARN,
            off_state=NEUTRAL,
        ),
        "configured": flag(autonomy.autonomous, on="on", off="off"),
        "lapses_at": moment(autonomy.autonomous_until),
        "updated_at": moment(autonomy.updated_ts),
        # An unreadable profile row is reported as autonomy OFF (the safe reading) by
        # `_autonomy_status`; the browser is told the reading was degraded rather than being shown
        # a confident "off" it cannot distinguish from a measured one.
        "profile_readable": flag(
            autonomy.profile_readable,
            on="yes",
            off="unreadable — autonomy reported OFF",
            on_state=NEUTRAL,
            off_state=WARN,
        ),
    }


def _attestation_payload(attestation: WithdrawalAttestationStatus) -> dict[str, Any]:
    return {
        "state": label(
            attestation.state,
            state=_ATTESTATION_STATES.get(attestation.state or "", UNKNOWN),
        ),
        "enabled": flag(attestation.enabled, on="enabled", off="disabled"),
        "attested_at": moment(attestation.attested_at),
        "expires_in": duration(attestation.expires_in_sec, elapsed=False),
        "expired_for": duration(attestation.expired_for_sec),
    }


def _session_payload(session: MarketSessionStatus) -> dict[str, Any]:
    return {
        "state": label(
            session.state, state=_SESSION_STATES.get(session.state or "", UNKNOWN)
        ),
        "recorded_at": moment(session.recorded_ts),
        # `defused` is whether a recorded CLOSED still vouches for the quiet -- the reason a
        # weekend's stale data must not alert. It is a fact the report already carries, and the
        # freshness rows below key off the same word, so the two can never disagree.
        "defused": flag(session.defused, on="staleness explained", off="staleness alerts"),
    }


def _position_payload(position: OpenPositionStatus) -> dict[str, Any]:
    """One open position.

    NO `notional` and NO `pnl`. `OpenPositionStatus` carries neither, so emitting one would mean
    this layer multiplied `qty` by `entry_price` and put a figure on the wire that the trading
    rails never saw. See the module docstring; the fix, if it is wanted, is upstream.

    NO unit on `qty` either, for a quieter version of the same reason. The spec's example shows
    `"0.01 BTC"`, but the base asset is not on the report -- it would have to be parsed out of
    `product_id`, and the audited place that decodes a product id into an asset is
    `keel/execution/guards.py::_asset`, which this layer may not reach (Rules 1 and 2) precisely
    so that a malformed id is decoded in ONE place with one set of consequences. `quantity` takes
    a `unit` for the day `OpenPositionStatus` carries a `base_asset`; until then the exact
    quantity sits beside the `product_id` and nothing is guessed.
    """
    return {
        "id": str(position.id),
        "product_id": position.product_id,
        "rule_name": position.rule_name,
        "qty": quantity(position.qty),
        "entry_price": money(position.entry_price),
        "opened_at": moment(position.opened_at),
        "bracket": flag(
            position.has_bracket,
            on="bracketed",
            off="NO bracket",
            on_state=GOOD,
            off_state=WARN,
        ),
    }


def _rule_payload(rule: RuleSummary) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "kind": rule.kind,
        "status": label(rule.status),
        "product_id": rule.product_id or "",
        # Rule params are operator-supplied and open-ended (any rule kind may invent its own), so
        # they cross as strings for the same reason `ActivityEvent.fields` does.
        "params": {str(key): stringify(value) for key, value in sorted(rule.params.items())},
    }


def _freshness_payload(row: ProductFreshness, session_defused: bool) -> dict[str, Any]:
    """One product's data freshness.

    The staleness judgement is NOT made from the age here -- there is no threshold in this file.
    It reads `MarketSessionStatus.defused`, which the report already computed: closed AND inside
    its trust window is the state under which staleness does not alert, and the TUI's freshness
    styling keys off the same field so the cells and the session line can never disagree about
    the same weekend.
    """
    state = NEUTRAL if (row.last_ts is not None or session_defused) else WARN
    return {
        "product_id": row.product_id,
        "granularity": row.granularity or "",
        "last_candle_at": moment(row.last_ts),
        "age": duration(row.age_sec, state=state),
    }


def _subscription_payload(row: SubscriptionStatusRow) -> dict[str, Any]:
    return {
        "venue": row.venue,
        "tier_name": row.tier_name,
        "pacing": row.pacing,
        "stored_status": label(row.stored_status),
        "effective_status": label(
            row.effective_status,
            state=GOOD if row.effective_status == "active" else WARN,
        ),
        # `None` means UNLIMITED here, not "not recorded" -- the one place in this file where an
        # absent Decimal is a fact rather than a gap, so it is spelled out instead of dashed.
        "effective_cap": (
            label("unlimited", state=NEUTRAL)
            if row.effective_cap is None
            else money(row.effective_cap)
        ),
    }


def status_payload(report: StatusReport) -> dict[str, Any]:
    """`gather_status`'s `StatusReport`, as JSON. Serialises; never re-gathers."""
    session_defused = bool(report.market_session.defused)
    return {
        "as_of": iso(report.now_ts),
        "generated_at": moment(report.now_ts),
        "mode": report.mode,
        "kill_switch": flag(
            report.kill_switch_engaged,
            on="ENGAGED",
            off="clear",
            on_state=BAD,
            off_state=GOOD,
        ),
        "autonomy": _autonomy_payload(report.autonomy),
        "market_session": _session_payload(report.market_session),
        "equity": {
            "state_mode": label(report.equity_state_mode),
            "high_water_mark": money(report.high_water_mark),
            "paper_cash": money(report.paper_cash_usdc),
        },
        "drawdown": {
            "total": ratio(report.drawdown_total_pct),
            "weekly": ratio(report.drawdown_weekly_pct),
            "max_total": ratio(report.max_total_dd_pct),
            "max_weekly": ratio(report.max_weekly_dd_pct),
            "rail11": label(report.rail11_status, state=_rail_state(report.rail11_status)),
        },
        "withdrawal_attestation": _attestation_payload(report.withdrawal_attestation),
        "open_positions": [_position_payload(p) for p in report.open_positions],
        # A LIST of pairs rather than an object, because the order is a presentation decision and
        # Rule 2 says presentation decisions are made here. `sorted` matches `render_human`'s own
        # ordering, so the two front-ends list the statuses the same way.
        "rule_counts": [
            {"status": status, "count": count(number)}
            for status, number in sorted(report.rule_counts.items())
        ],
        "live_rules": [_rule_payload(r) for r in report.live_rules],
        "data_freshness": [_freshness_payload(f, session_defused) for f in report.data_freshness],
        "subscriptions": [_subscription_payload(s) for s in report.subscriptions],
    }


# -- insights --------------------------------------------------------------------------------------


def _account_payload(account: AccountSummary) -> dict[str, Any]:
    return {
        "mode": account.mode,
        "state_mode": label(account.equity_state_mode),
        "high_water_mark": money(account.high_water_mark),
        "paper_cash": money(account.paper_cash_usdc),
        "drawdown_total": ratio(account.drawdown_total_pct),
        "drawdown_weekly": ratio(account.drawdown_weekly_pct),
        "max_total_dd": ratio(account.max_total_dd_pct),
        "max_weekly_dd": ratio(account.max_weekly_dd_pct),
        "rail11": label(account.rail11_status, state=_rail_state(account.rail11_status)),
    }


def _gate_payload(gate: GateDistance) -> dict[str, Any]:
    """The promotion gate's distance. `passing` is the engine's verdict, copied; the floors are
    the config's own values, copied. Nothing here re-runs `check_floors`."""
    return {
        "rule_name": gate.rule_name,
        "promotion_class": gate.promotion_class,
        "n_trades": count(gate.n_trades),
        "min_trades": count(gate.min_trades),
        "trades_remaining": count(
            gate.trades_remaining, state=GOOD if gate.trades_remaining == 0 else WARN
        ),
        "win_rate": percent(gate.win_rate, places=1),
        "min_win_rate": percent(gate.min_win_rate, places=1),
        "realized_rr": ratio(gate.realized_rr),
        "min_rr": ratio(gate.min_rr),
        "expectancy": money(gate.expectancy, places=4, signed=True),
        "min_expectancy": money(gate.min_expectancy, places=4),
        "passing": flag(gate.passing, on="passing", off="blocked", on_state=GOOD, off_state=WARN),
        "blocking_reasons": list(gate.blocking_reasons),
    }


def _track_record_payload(record: RuleTrackRecord) -> dict[str, Any]:
    return {
        "rule_name": record.rule_name,
        "status": label(record.status),
        "promotion_class": record.promotion_class,
        "n_trades": count(record.n_trades),
        "win_rate": percent(record.win_rate, places=1),
        "avg_win": money(record.avg_win),
        "avg_loss": money(record.avg_loss),
        "realized_rr": ratio(record.realized_rr),
        "expectancy": money(record.expectancy, places=4, signed=True),
        "profit_factor": ratio(record.profit_factor),
        "max_drawdown": money(record.max_drawdown),
        # `significant` is `n_trades >= 30`, computed by the report. Below that floor a win rate
        # is not distinguishable from random entry, so the payload SAYS so rather than leaving the
        # number to speak for itself -- the same choice `render_insights` makes in HTML.
        "significant": flag(
            record.significant,
            on="n≥30",
            off="below the n=30 floor",
            on_state=NEUTRAL,
            off_state=WARN,
        ),
        "gate": _gate_payload(record.gate) if record.gate is not None else None,
    }


def insights_payload(report: InsightsReport) -> dict[str, Any]:
    """`build_insights_report`'s `InsightsReport`, as JSON."""
    return {
        "as_of": iso(report.now_ts),
        "generated_at": moment(report.now_ts),
        "account": _account_payload(report.account),
        "closed_trade_count": count(report.closed_trade_count),
        "rules": [_track_record_payload(r) for r in report.rules],
    }


# -- journal ---------------------------------------------------------------------------------------


def _journal_entry_payload(entry: JournalEntry) -> dict[str, Any]:
    """One closed (or, with `--include-open`, one open) trade.

    This is where the spec's worked `pnl` example actually lands: `pnl_net` is a figure
    `build_journal_report` computed off the fee-honest `trade_outcomes` ledger, so it can be
    serialised without this layer deriving anything. `None` stays absent rather than becoming
    `0.00` -- a trade with no recorded net is not a break-even trade, which is the same
    distinction `render_insights` protects in HTML.
    """
    return {
        "closed_at": moment(entry.closed_at),
        "opened_at": moment(entry.opened_at),
        "rule_name": entry.rule_name or "",
        "product_id": entry.product_id,
        "qty": quantity(entry.qty),
        "entry_fill": money(entry.entry_fill),
        "exit_fill": money(entry.exit_fill),
        "pnl": money(entry.pnl_net, signed=True),
        "fees": money(entry.fees, places=4),
        "r_multiple": ratio(entry.r_multiple, signed=True),
        "is_dca": flag(entry.is_dca, on="DCA", off="rule"),
        "outcome": label(entry.outcome, state=_OUTCOME_STATES.get(entry.outcome, NEUTRAL)),
    }


#: `JournalEntry.outcome`'s vocabulary, judged. `dca` is neutral by design: a DCA row has no stop
#: to measure against, so `build_journal_report` labels it `"dca"` regardless of the P&L sign and
#: calling it a win or a loss here would invent a verdict the ledger declined to make.
_OUTCOME_STATES: Mapping[str, str] = {"win": GOOD, "loss": BAD, "dca": NEUTRAL, "open": NEUTRAL}


def journal_payload(report: JournalReport) -> dict[str, Any]:
    """`build_journal_report`'s `JournalReport`, as JSON.

    `total_count` is the full filtered count BEFORE `--limit` truncated `entries`; both cross, so
    a client showing "50 of 812" needs no subtraction to know it is looking at a page.
    """
    return {
        "as_of": iso(report.now_ts),
        "generated_at": moment(report.now_ts),
        "mode": report.mode,
        "total_count": count(report.total_count),
        "shown_count": count(len(report.entries)),
        # The query echoed back. Open-ended by shape (`limit` and `since_ts` are ints), so it
        # crosses as strings -- see `stringify`.
        "filters": {str(key): stringify(value) for key, value in sorted(report.filters.items())},
        "entries": [_journal_entry_payload(e) for e in report.entries],
    }


# -- activity ------------------------------------------------------------------------------------


def _event_payload(event: ActivityEvent) -> dict[str, Any]:
    return {
        "at": moment(event.ts),
        "level": event.level,
        "event": event.event,
        "cycle_id": event.cycle_id or "",
        # Kept whole rather than projected onto a fixed schema: the event vocabulary grows with
        # every `log_event` call site, and an overlay that silently dropped a field it had not
        # been taught about would be worse than one that renders it as key=value.
        "fields": {str(key): stringify(value) for key, value in event.fields.items()},
    }


def _cycle_payload(cycle: ActivityCycle) -> dict[str, Any]:
    """One engine cycle.

    `quiet` is `neutral`, never `warn`. A cycle in which the agent looked at the market and
    nothing happened is a POSITIVE observation -- a long run of them is how this feed answers "is
    it alive" -- so it is rendered muted but never omitted and never flagged.
    """
    return {
        "key": cycle.key,
        "cycle_id": cycle.cycle_id or "",
        "started_at": moment(cycle.started_ts),
        "ended_at": moment(cycle.ended_ts),
        "mode": cycle.mode or "",
        "products": list(cycle.products),
        "rules": list(cycle.rules),
        "signals": count(cycle.signals),
        "blocked": count(cycle.blocked),
        "entered": count(cycle.entered),
        "exited": count(cycle.exited),
        "errors": count(cycle.errors, state=BAD if cycle.errors else NEUTRAL),
        "highlights": list(cycle.highlights),
        "quiet": flag(cycle.is_quiet, on="quiet", off="active"),
        "uncorrelated": flag(cycle.is_uncorrelated, on="uncorrelated", off="correlated"),
        "events": [_event_payload(e) for e in cycle.events],
        "events_dropped": count(
            cycle.events_dropped, state=WARN if cycle.events_dropped else NEUTRAL
        ),
    }


def activity_payload(feed: ActivityFeed) -> dict[str, Any]:
    """`build_activity_feed`'s `ActivityFeed`, as JSON.

    Every non-`ok` status crosses as a judged state rather than being suppressed: `missing` is the
    commonest state on a fresh install and is not an error, and hiding it would leave a user
    staring at a blank panel with nothing to act on.

    `scope_fully_covered` is the one field a client must not ignore. False means the bounded tail
    read could not prove it reached back past the scope boundary, so an empty feed cannot be read
    as "the scope was quiet" -- which is why it arrives already judged as a warning.
    """
    return {
        "as_of": iso(feed.now_ts),
        "generated_at": moment(feed.now_ts),
        "status": label(feed.status, state=_FEED_STATES.get(feed.status, UNKNOWN)),
        "source": feed.source,
        "detail": feed.detail or "",
        "scope": feed.scope,
        "scope_start_at": moment(feed.scope_start_ts),
        "scope_fully_covered": flag(
            feed.scope_fully_covered,
            on="complete",
            off="window may not cover the whole scope",
            on_state=NEUTRAL,
            off_state=WARN,
        ),
        "lines_read": count(feed.lines_read),
        # Lines read but unusable -- a crash's half-written JSON, a record with no timestamp.
        # Surfaced rather than swallowed: silently discarding input is how a feed comes to
        # under-report reality while looking healthy.
        "lines_skipped": count(feed.lines_skipped, state=WARN if feed.lines_skipped else NEUTRAL),
        "window_truncated": flag(
            feed.window_truncated,
            on="window truncated",
            off="whole window read",
            on_state=WARN,
            off_state=NEUTRAL,
        ),
        "cycles_dropped": count(
            feed.cycles_dropped, state=WARN if feed.cycles_dropped else NEUTRAL
        ),
        "cycles_out_of_scope": count(feed.cycles_out_of_scope),
        "last_cycle_before_scope": (
            _cycle_payload(feed.last_cycle_before_scope)
            if feed.last_cycle_before_scope is not None
            else None
        ),
        "cycles": [_cycle_payload(c) for c in feed.cycles],
    }
