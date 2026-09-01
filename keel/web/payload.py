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

WHAT #534 ADDED, AND WHY IT IS HERE RATHER THAN IN THE ROUTING LAYER. Three things: the
`envelope`/`error_envelope` pair that wraps every `GET /api/*` response, the `engine_state` word
that lets a client say "keel isn't running" instead of rendering a blank view, and `order_rows`,
the server-side sort. All three touch the WIRE VOCABULARY -- the closed `state` words, the
no-exponent guarantee that makes `Field.value` re-parseable, the rule that a number crosses as a
string -- and a second file holding half of that vocabulary is how the two halves drift. The
routing layer's job is deciding WHICH rows and WHICH column from a query string; the ordering
itself sits next to the `_plain` that wrote the strings being ordered.

Note what `order_rows` does NOT do: it does not add the numeric `sort` companion field described
above. It re-parses `Field.value`, which is exactly what that value is for.

WHAT IS *NOT* A FIELD. Identifiers and enum words -- `product_id`, `rule_name`, `mode`,
`as_of` -- cross as bare JSON strings. They carry no precision hazard, no rounding decision and
no judgement, and wrapping them would be ceremony rather than contract. Bare JSON *numbers*
never appear at all: counts, timestamps and open-ended log fields all cross as strings, because
"it is only a count" is how the first double gets in.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, TypedDict

# The CLI's own words for "how long ago" and "how much longer". Imported rather than
# re-implemented so the browser and the terminal can never disagree about the age of the same
# candle -- `keel/commands/insights.py` reaches into `status.py` for `_human_age` the same way,
# for the same reason. `keel.commands.*` is the SERVICE layer, which this layer is allowed to
# read; the compute trees (`keel.strategy`, `keel.execution.*`, `keel.analysis`) are not.
from keel.commands.status import _human_age, _human_remaining

# A real import, not `TYPE_CHECKING`-only: `_readiness_payload` reads the ENUM MEMBERS at
# runtime to build `_READINESS_STATE`, not just the type. `keel.venue_readiness` is the #233
# PR4 module both `keel/commands/brokers.py` and this file read, so the CLI and the web cannot
# disagree about what each readiness state means.
from keel.venue_readiness import VenueReadiness

if TYPE_CHECKING:  # pragma: no cover - typing only
    from keel.commands.activity import ActivityCycle, ActivityEvent, ActivityFeed
    from keel.commands.insights import (
        AccountSummary,
        EquityCurve,
        EquityPoint,
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
    from keel.venue_readiness import VenueReadinessRow


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


def _magnitude(value: Decimal, places: int) -> str:
    """The unsigned, grouped, human-readable text for `value`.

    The sign is stripped here and decided elsewhere -- by `_is_negative` below, from the exact
    `Decimal` -- so that ONE reading of the sign feeds both the displayed glyph and the `state`.

    The first draft read the sign off this formatted string instead, and the two readings could
    disagree: `format(Decimal("-0.00"), ",.2f")` is `"-0.00"`, so the display said `−$0.00` while
    `state` said `neutral`, because `Decimal("-0.00") == 0`. A client styling by `state` and
    showing `display` would then have printed a minus sign in a neutral colour -- a small thing,
    but Rule 3's whole premise is that the two never disagree, and a premise with one exception
    is not a premise.

    Stripping rather than negating is still deliberate: `abs()`/`-value` would be arithmetic on
    money in this layer, which Rule 3 of the thinness pin forbids outright.
    """
    return format(value, f",.{places}f").lstrip("-")


def _is_negative(value: Decimal) -> bool:
    """THE reading of a money value's sign, for both the glyph and the state.

    A comparison, not arithmetic. Negative zero is NOT negative here -- `Decimal("-0.00") == 0` --
    which is the whole point of having one reading: a value that is not negative never displays a
    minus. A value that IS negative but rounds to `0.00` at the requested precision still shows
    its minus, and (when the field is a gain/loss) still reads `bad`; the two agree, which is what
    matters.
    """
    return value < 0


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


#: How deep `stringify` will follow a nested structure before giving up.
#:
#: Log fields are shallow by construction -- each one is a `log_event` kwarg -- so six levels is
#: already generous. The cap exists so that the depth of a value parsed from a line keel did not
#: write cannot decide how deep this process recurses: `json.loads` cannot build a cycle, but it
#: can build a thousand nested lists, and a `RecursionError` while rendering a log line would take
#: down the whole response for a row nobody needed.
_MAX_NESTING = 6

#: What a value too deep to render shows as. A marker, not the raw structure: this layer's job is
#: to hand the client something it can display, and "there is more here than we will render" is a
#: displayable fact where a truncated Python repr is not.
_TOO_DEEP = "(nested)"


def _normalise(value: Any, depth: int = 0) -> Any:
    """A JSON-safe mirror of `value` with every SCALAR already rendered as a string.

    Structure is kept (a list stays a list, an object stays an object) so the shape a log line
    recorded survives; only the leaves change. Because every leaf is already a string by the time
    `json.dumps` sees it, no encoder is needed -- which matters more than it looks: the encoder a
    hurried author reaches for is `default=float`, and that single keyword is the whole contract
    dying quietly. Rule 6d of `test_console_thinness.py` fails the build on it.
    """
    if depth >= _MAX_NESTING:
        return _TOO_DEEP
    if isinstance(value, Mapping):
        return {str(key): _normalise(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise(item, depth + 1) for item in value]
    return stringify(value, _depth=depth)


def stringify(value: Any, *, _depth: int = 0) -> str:
    """Any open-ended value, as ONE display string.

    Used for the two places keel's own reports carry values of unconstrained type:
    `ActivityEvent.fields` (every `log_event` call site invents its own kwargs, and the parser
    keeps them whole rather than projecting onto a fixed schema) and `JournalReport.filters` (the
    query echoed back). Rule 1 applies to those exactly as it does to a named money field -- an
    echoed `limit` is a JSON number like any other, and "it is only a count" is how the first
    double gets in.

    Booleans render as `"true"`/`"false"` rather than Python's `"True"`/`"False"`: the reader is
    JavaScript.

    **A nested value crosses as JSON text, never as a Python repr.** `ActivityEvent.fields` is
    parsed out of the engine's own log lines, so a value can be a list or an object, and the
    obvious `str(value)` produced `"{'a': 1, 'b': None}"` -- single quotes, `None`, `True`: text
    no client can parse and none should display. Worse, `str([1e+50])` is `"[1e+50]"`, which puts
    scientific notation on the wire through a path no money field touches, so none of the Decimal
    care taken elsewhere in this file would have caught it. The structure is normalised leaf by
    leaf through this same function first, so the numbers inside a nested value are rendered by
    exactly the rules the top-level ones are, and only then dumped as JSON.

    Flattened to a string rather than kept as a nested object because of Rule 2: an open-ended
    structure handed to the client is a structure the CLIENT has to decide how to format, and
    deciding how to format is what this layer exists to do instead. The activity view places one
    string per field.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return _plain(value) if value.is_finite() else str(value)
    if isinstance(value, float):
        decimalised = _decimalise(value)
        # A non-finite float has no decimal form at all; `str` gives "nan"/"inf", which is at
        # least a word rather than a number a client would try to read as one.
        return _plain(decimalised) if decimalised is not None else str(value)
    if isinstance(value, (Mapping, list, tuple)):
        if _depth >= _MAX_NESTING:
            return _TOO_DEEP
        # Plain `json.dumps`: `_normalise` has already turned every leaf into a string, so there
        # is nothing left for an encoder to convert. `ensure_ascii=False` because the payload is
        # UTF-8 and a product id or rule name has no business becoming an escape sequence.
        return json.dumps(_normalise(value, _depth), ensure_ascii=False)
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
    negative, magnitude = _is_negative(figure), _magnitude(figure, places)
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
    negative, magnitude = _is_negative(figure), _magnitude(figure, places)
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
    negative, magnitude = _is_negative(figure), _magnitude(figure, places)
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
    negative, magnitude = _is_negative(figure), _magnitude(figure, places)
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


def _bracket_field(position: OpenPositionStatus, mode: str) -> Field:
    """The `bracket` column for one open position (#641).

    Live's meaning is untouched and load-bearing: `NO bracket` there names a real venue-order
    gap -- exactly the state `reconcile_unbracketed_positions` and the `unbracketed:` crash
    ledger (#519, #502) exist to heal, so it stays WARN, unchanged, for every non-paper mode
    regardless of `has_bracket`.

    Paper never places a venue order, so `has_bracket` is False on every paper position by
    construction; WARNing on that reports the MODE, not a hazard, and desensitises the reader to
    the live case that matters. Paper is not unprotected, though -- `PaperTrader.on_candle`
    resolves the setup's stop/target against each candle's range -- so a paper row without a
    bracket names the protection that mechanism actually enforces: the stop the tranche was sized
    against, when the row recorded one (`initial_stop`, since 0.12.2 / #520). A tranche that
    predates that migration, or a DCA leg that never got one, has no number to show and says so
    (NEUTRAL, not WARN) rather than asserting one it does not have.
    """
    if position.has_bracket or mode != "paper":
        return flag(
            position.has_bracket,
            on="bracketed",
            off="NO bracket",
            on_state=GOOD,
            off_state=WARN,
        )
    if position.initial_stop is not None:
        stop = money(position.initial_stop)
        return label(stop["value"], display=f"paper stop {stop['display']}", state=NEUTRAL)
    return label(
        "n/a", display="n/a -- paper resolves stop/target on candle touch", state=NEUTRAL
    )


def _position_payload(position: OpenPositionStatus, mode: str) -> dict[str, Any]:
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

    `mode` is `report.mode` (`status_payload`'s own `StatusReport.mode`), passed down purely so
    `_bracket_field` can tell paper from live -- it is never stored on the position itself and
    never used for anything but that one judgement (#641).
    """
    return {
        "id": str(position.id),
        "product_id": position.product_id,
        "rule_name": position.rule_name,
        "qty": quantity(position.qty),
        "entry_price": money(position.entry_price),
        "opened_at": moment(position.opened_at),
        "bracket": _bracket_field(position, mode),
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
        "open_positions": [_position_payload(p, report.mode) for p in report.open_positions],
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

    `point_index` (#602) is NOT set here -- it depends on every entry BEFORE this one in the same
    list, which a function given one entry at a time cannot see. `journal_payload` sets it once it
    has the whole list, right beside the loop that decides it.
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


def _equity_point_payload(point: EquityPoint) -> dict[str, Any]:
    """One point of the curve: where to draw it, and what it says.

    `x` and `y` are BARE STRINGS, not `Field`s, and that is the one place in this module where a
    figure crosses with no `display` beside it. They are not values a human reads -- there is
    nothing to format, no judgement to carry and no unit -- they are positions inside an SVG
    `viewBox`, decided by `keel.commands.insights.build_equity_curve` where a `Decimal` is in
    scope and where the choice of axis can be tested. `_plain` still renders them, so a
    coordinate cannot reach the wire in exponent notation any more than a price can.

    Everything a reader is actually TOLD -- the instant, the trade's net, the running total --
    arrives as a `Field`, and those are what the chart's text equivalent is built from.
    """
    return {
        "x": _plain(point.x),
        "y": _plain(point.y),
        "at": moment(point.closed_at),
        "product_id": point.product_id,
        "rule_name": point.rule_name or "",
        "pnl": money(point.pnl, signed=True),
        "cumulative": money(point.cumulative, signed=True),
    }


def equity_curve_payload(curve: EquityCurve) -> dict[str, Any]:
    """`build_equity_curve`'s `EquityCurve`, as JSON.

    **`reading` is the chart's text equivalent, and it is written HERE rather than in the browser
    for the same reason every other sentence on this wire is.** A chart is data made visible, so a
    reader who cannot see it has to be told the same thing in words. Assembling that sentence in
    JavaScript would mean the client deciding what a curve says -- a judgement -- and doing it
    from figures `render.js` is not allowed to read. It is a `label` rather than a bare string
    because it carries the curve's verdict in its `state`, taken from the CLOSING figure, so the
    spoken summary and the last point can never disagree about whether this is a profitable track
    record.

    Both text equivalents ship, and neither is a fallback for the other: the sentence is what a
    screen reader hears from the chart's `aria-label`, and the per-point rows are what someone
    reads when they want the numbers rather than the shape.

    `net` is the LAST point's cumulative rather than a sum computed here -- `build_equity_curve`
    already ran the running total, and re-adding it in this module would be a second arithmetic
    of the same figure in the one place that is not allowed to hold one.
    """
    net = money(curve.points[-1].cumulative, signed=True) if curve.points else absent()
    low, high = money(curve.low), money(curve.high)
    trades = count(curve.point_count)
    if curve.points:
        reading = (
            f"Cumulative net profit and loss over {trades['display']} closed trades, "
            f"ending at {net['display']}, ranging from {low['display']} to {high['display']}."
        )
    else:
        # Not "the curve is flat", and not an empty string. A deployment with no closed trades has
        # no track record at all, and which of those two a reader is looking at is the entire
        # difference between an empty chart and a broken one.
        reading = "No closed trades yet, so there is no curve to draw."
    return {
        "width": _plain(curve.width),
        "height": _plain(curve.height),
        "baseline_y": _plain(curve.baseline_y),
        "point_count": trades,
        "low": low,
        "high": high,
        "net": net,
        "reading": label("curve", display=reading, state=net["state"]),
        "points": [_equity_point_payload(point) for point in curve.points],
    }


def journal_payload(report: JournalReport, *, curve: EquityCurve) -> dict[str, Any]:
    """`build_journal_report`'s `JournalReport`, as JSON.

    `curve` is passed in rather than built here, and the direction is the point: `keel/web/api.py`
    calls `build_equity_curve` on the entries THIS report carries, so the chart and the table
    beneath it are two views of one list and cannot come to describe different trades. It is a
    REQUIRED keyword, never a defaulted one -- a default would let every existing caller keep
    working while quietly serving a journal with no chart, which is the shape of a suite that is
    green because it stopped asking the question.

    `total_count` is the full filtered count BEFORE `--limit` truncated `entries`, and
    `shown_count` is how many survived; both cross, so a client showing "50 of 812" needs no
    subtraction to know it is looking at a page.

    `shown_count` is READ from the report, never measured here. It shipped in the first draft as
    `count(len(report.entries))`, which is a figure `JournalReport` did not hold -- numerically
    harmless, since a list length is exact, but a breach of the rule this module claims to keep,
    and one whose guard passed only by coincidence (the fixture happened to set
    `total_count == len(entries)`). It is now a property of the report, and Rule 6e of
    `test_console_thinness.py` bans `len()` here so the shortcut cannot be taken again.

    **`point_index` (#602) is the one place this function counts rather than copies**, and the
    count is position bookkeeping, not a figure: `build_equity_curve` draws one point per entry
    whose `pnl_net` is not `None`, oldest first, so the position a kept entry lands at in
    `curve.points` is exactly how many kept entries came before it -- the SAME list, in the SAME
    order, `equity_curve_payload` below serialises `curve.points` from. Walking `report.entries`
    here with the same skip condition gives each entry the array index of its own point in
    `curve.points`, or `None` for a row the curve skipped (no net recorded -- nothing to
    highlight). This mirrors `build_equity_curve`'s skip rule rather than re-deriving a figure
    from one, and `js/main.js` trusts the mirror rather than re-deriving it a third time: it reads
    `curve.points[Number(entry.point_index)]` directly, with no lookup of its own.
    """
    entries = []
    plotted = 0
    for entry in report.entries:
        built = _journal_entry_payload(entry)
        if entry.pnl_net is None:
            built["point_index"] = None
        else:
            built["point_index"] = str(plotted)
            plotted += 1
        entries.append(built)

    return {
        "as_of": iso(report.now_ts),
        "generated_at": moment(report.now_ts),
        "mode": report.mode,
        "total_count": count(report.total_count),
        "shown_count": count(report.shown_count),
        # The query echoed back. Open-ended by shape (`limit` and `since_ts` are ints), so it
        # crosses as strings -- see `stringify`.
        "filters": {str(key): stringify(value) for key, value in sorted(report.filters.items())},
        "entries": entries,
        "curve": equity_curve_payload(curve),
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


#: What each non-`ok` `ActivityFeed.status` means, in the words `render.py::render_activity`
#: already uses. Copied verbatim rather than paraphrased: two front-ends offering an operator two
#: different accounts of the same state is worse than either account alone, and `missing` in
#: particular has to keep the second sentence -- "it also happens when keel is run from a
#: directory that is not the deployment folder" -- because that is the actual cause most of the
#: time and it is not guessable from the word.
#:
#: `ok` is absent, and `.get(..., "")` is what that means: a healthy log needs no paragraph.
_FEED_STATUS_NOTES: Mapping[str, str] = {
    "missing": (
        "No log file yet. This is normal before the first cycle -- it also happens when keel is "
        "run from a directory that is not the deployment folder."
    ),
    "empty": "The log exists but the window held no records.",
    "unparseable": "Lines were read, but none of them was a JSON record.",
    "oversized": (
        "The bounded tail read landed inside a single record, so nothing whole survived it."
    ),
    "unreadable": "The log could not be read.",
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
        # The prose for a non-`ok` status, chosen HERE rather than in the client.
        #
        # `render.py::render_activity` keeps the same table and picks from it with
        # `explain.get(feed.status, "")`, which is a lookup keyed on the raw status WORD -- and a
        # browser client cannot do that: `tests/web/test_client_assets.py::
        # test_render_never_judges_a_value_itself` forbids `render.js` from reading `Field.value`
        # at all, because a client that branches on a value is a client re-deriving a judgement.
        # Prose selected by a state word is exactly that branch, so the selection crosses the wire
        # already made. It is `""` for `ok`, which is not a state anyone needs a paragraph about.
        "status_note": _FEED_STATUS_NOTES.get(feed.status, ""),
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


# -- the envelope (#534) -------------------------------------------------------------------------
#
# Every `GET /api/*` success is wrapped in the same four keys, so #536's single `fetch` wrapper
# needs no per-endpoint branch and no knowledge of which endpoints can report a dead engine.


#: The closed `engine` vocabulary. Two words, and deliberately not three.
#:
#: A third word for "the report raised" was drafted and dropped. The CLIENT behaviour required by
#: a stopped engine and by an unbuildable report is identical -- show no figures, say why -- so a
#: third word would force every view to write three branches to get two behaviours, and the
#: difference between the two is already carried by the HTTP status and by `error.detail`. Add one
#: the day a client would DO something different with it.
ENGINE_STATES: frozenset[str] = frozenset({"running", "stopped"})

RUNNING = "running"
STOPPED = "stopped"

#: What `engine: "stopped"` says when the caller has nothing more specific. The wording has to be
#: true both for a machine with no deployment on it and for one whose report could not be built,
#: because those are the two ways this value is reached.
_STOPPED_DISPLAY = "keel isn't running here — there is no deployment to read"
_RUNNING_DISPLAY = "keel is set up on this machine"


def engine_state(*, running: bool, detail: str = "") -> Field:
    """Whether there is a keel deployment behind this response, as a judged field.

    **What this does NOT answer, on purpose: "did the agent run recently".** That question needs a
    THRESHOLD -- how many hours of silence is too many -- and this file holds no thresholds by
    design (`_freshness_payload` refuses the same temptation and says so). The evidence for it is
    already on the wire in two places a client can render without arithmetic: `data_freshness`
    carries each product's candle age in the CLI's own words, and the activity feed carries
    `last_cycle_before_scope`, which exists precisely so an empty view can say *when keel last
    ran*. The day a report builder holds an agent heartbeat WITH its staleness verdict, this
    vocabulary gains a third word and the verdict is copied here, never computed here.

    `warn` rather than `bad` for a stopped engine: on the commonest path to this value nothing is
    broken at all -- it is a first run, and the correct next action is the setup checklist, not an
    incident. A caller that knows better passes its own `detail`.
    """
    if running:
        return {"value": RUNNING, "display": _RUNNING_DISPLAY, "state": GOOD}
    return {"value": STOPPED, "display": detail or _STOPPED_DISPLAY, "state": WARN}


def envelope(
    now_ts: float | int | None,
    *,
    running: bool,
    data: dict[str, Any] | None,
    detail: str = "",
    sort: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One `GET /api/*` success.

    `data` is `null` -- never `{}` -- when the endpoint could not be answered because there is no
    deployment. An empty object is a payload in which every figure is missing, and a view handed
    one renders zeros; `null` cannot be rendered by accident, which is the whole requirement.

    The key set is CONSTANT across every endpoint and every state, `sort` included on endpoints
    that sort nothing. A client that has to test whether a key is present is branching on payload
    SHAPE, which Rule 3 rejects for `state` and which is rejected here for the same reason.

    `as_of` is the instant this response was built, and it is present even when `data` is not --
    that pairing is the requirement: a client showing "keel isn't running" should be able to say
    since when it was looking.
    """
    return {
        "as_of": iso(now_ts),
        "engine": engine_state(running=running, detail=detail),
        "data": data,
        "sort": sort,
    }


def error_envelope(
    now_ts: float | int | None, *, status: int, title: str, detail: str
) -> dict[str, Any]:
    """One `GET /api/*` refusal or failure.

    **The discriminator between this document and `envelope` is the HTTP STATUS, deliberately, and
    not a field.** A uniform envelope with a nullable `error` was the first shape and it does not
    survive contact with admission: most refusals happen BEFORE the session cookie is checked, and
    filling in `engine` there would mean an unauthenticated request reading the deployment state
    off disk. `res.ok` is a check every `fetch` client already makes.

    `status` crosses as a STRING like every other number here. It would survive JSON's number type
    intact, and that is exactly the argument `count` refuses: a payload with "only a few" numbers
    in it needs a per-field rule about which ones, and that rule is what rots.

    `data` is present and `null` so that a client reading `.data` on any response gets a value
    rather than `undefined` -- the key set stays constant across the two documents for the same
    reason it stays constant across endpoints.
    """
    return {
        "as_of": iso(now_ts),
        "data": None,
        "error": {"status": str(status), "title": title, "detail": detail},
    }


# -- ordering (#534) -----------------------------------------------------------------------------
#
# Server-side sort, ordered with `Decimal`. The spec's own words: "On loopback the round trip is
# sub-millisecond, so there is nothing to optimise and no client arithmetic to audit."


def _cell_text(row: Mapping[str, Any], column: str) -> str:
    """The exact, ungrouped text a row carries in `column`, or `""` for nothing to order by.

    Reads `Field.value` for a field and the string itself for a bare identifier, which is exactly
    what `Field.value` is documented to be: "machine input (exact, ungrouped, `Decimal`-parseable
    for figures and ISO-8601 for instants)". A list, an object or a `None` yields `""` and sorts
    with the absent rows -- an endpoint should not be declaring such a column sortable, and the
    routing layer refuses a column it does not declare, but a total function here means a mistake
    there costs an ordering rather than a 500.
    """
    cell = row.get(column)
    if isinstance(cell, Mapping):
        raw = cell.get("value", "")
        return raw if isinstance(raw, str) else ""
    if isinstance(cell, str):
        return cell
    return ""


def _finite_decimal(text: str) -> Decimal | None:
    """`text` as a finite `Decimal`, or `None` for anything that cannot be ordered as a number.

    Non-finite is `None` on purpose, and it is not hypothetical: `stringify` renders a
    `Decimal("NaN")` sentinel as `"NaN"`, and `Decimal("NaN")` PARSES. Ordering by it would make
    the result depend on the comparison order the sort happened to use, because NaN compares false
    to everything including itself -- so a column containing one is ordered as text instead, as a
    whole column, and the ordering stays deterministic.
    """
    if not text:
        return None
    try:
        candidate = Decimal(text)
    except (InvalidOperation, ValueError, TypeError):
        return None
    return candidate if candidate.is_finite() else None


def _ordering_key(pair: tuple[Any, Mapping[str, Any]]) -> Any:
    """The first element of a (key, row) pair.

    Sorting is keyed on this rather than on the pair, because the second element is a `dict` and
    comparing two of them raises the moment two keys tie -- which is exactly the case a
    tie-breaking sort reaches."""
    return pair[0]


def order_rows(
    rows: Sequence[Mapping[str, Any]], *, column: str, descending: bool = False
) -> list[Mapping[str, Any]]:
    """`rows` ordered by `column`, with `Decimal` wherever the column is numeric.

    **Why `Decimal` and not `float`, with the number in it.** Two ERC-20 quantities one wei apart
    -- `0.100000000000000001` and `0.100000000000000002`, both legal at an 18-decimal base
    increment -- map to the SAME IEEE-754 double, because adjacent doubles near 0.1 are about
    1.4e-17 apart. A float-keyed sort cannot separate them, so their order becomes whatever order
    they arrived in; `Decimal` orders them. The same collapse takes the last cent of a large
    notional. `tests/web/test_api.py` asserts that disagreement directly, before the endpoint test
    relies on it, so the endpoint test cannot be green for the trivial reason that sorting sorts.

    **The SERIALISED rows are sorted, not the source dataclasses.** The alternative -- ordering
    `report.open_positions` before serialising -- needs a map from every payload key back to the
    report attribute it came from, held in a second file, and that map rots the first time a key
    is renamed here. Sorting the output means `?sort=qty` names exactly the field the client can
    see in the response, and `_plain` guarantees `value` re-parses to the `Decimal` it was written
    from, for any input, so the round trip loses nothing.

    **Numeric or text is decided per COLUMN, never per row.** A per-row decision would put a
    `Decimal` and a `str` in one comparison, which raises, and a per-row fallback would make the
    order depend on which values happened to look like numbers. So: numeric only when every
    present value parses finite, text otherwise. Instants land in the text branch and are still
    chronological, because `moment().value` is fixed-width ISO-8601 UTC.

    **An absent value sorts last in BOTH directions.** `None` means "not recorded" and `0` means
    "recorded as zero"; collapsing the first into the second is the shape of the always-passing fee
    rail (#198). Giving an absent cell a numeric key would make it the largest thing in a
    descending sort, which is that same collapse wearing a different hat.
    """
    present: list[tuple[str, Mapping[str, Any]]] = []
    missing: list[Mapping[str, Any]] = []
    for row in rows:
        text = _cell_text(row, column)
        if text:
            present.append((text, row))
        else:
            missing.append(row)

    figures = [_finite_decimal(text) for text, _row in present]
    keyed: list[tuple[Any, Mapping[str, Any]]]
    if all(figure is not None for figure in figures):
        keyed = [(figure, row) for figure, (_text, row) in zip(figures, present, strict=True)]
    else:
        keyed = [(text.casefold(), row) for text, row in present]

    # `list.sort` is stable in both directions, so rows that tie keep the order the report built
    # them in -- which for `open_positions` is FIFO, the attribution order a later exit uses, and
    # therefore an order that must not be scrambled by a display sort.
    keyed.sort(key=_ordering_key, reverse=descending)
    return [row for _key, row in keyed] + missing


# -- config (#534) -------------------------------------------------------------------------------


def config_payload(
    build: Any,
    *,
    describe: str = "",
    mode: str = "",
    db_path: str = "",
    config_path: str = "",
) -> dict[str, Any]:
    """The running build and the deployment it serves, for the consumers that need either by
    name.

    `version` is what #539 carries as `?v=` on a documentation link, so version skew between the
    engine and the docs is visible rather than silent. `build` is `full_version` -- the version
    bound to the commit -- and is what #538 keys its service-worker cache name to, because a
    version alone is ambiguous (many commits share one between bumps) and a cache key that does
    not move when the code does is exactly how an upgraded engine gets met by a stale shell
    holding an older contract.

    `build` arrives already RESOLVED, from `ServeConfig`, rather than being looked up here.
    `keel.version.build_info()` shells out to git twice, and an endpoint a service worker polls
    must not fork a subprocess to answer. `None` -- no package metadata and no git, the same
    environment in which the page footer is already empty -- reports absent rather than inventing
    a version: a cache key of `""` still keys a cache, whereas a wrong version in a `?v=` link
    would send a reader to documentation for a build that does not exist.

    ── THE DEPLOYMENT, NOT THE BINARY (#597) ───────────────────────────────────────────────────
    `mode`, `db_path` and `config_path` name the ONE deployment this process serves, and they
    ride this payload because it is the one endpoint the client reads on EVERY view (it keys the
    worker and the docs links at boot), so the header's mode badge is present on every screen
    rather than only where a status report happens to load. All three are bare strings, not
    `Field`s: `mode` is an enum word and the paths are identifiers (the "what is NOT a Field"
    note above names `mode` in that class), and a `Field` would force a `state` -- `confirm`
    beside `paper` is neither good nor bad, and the badge's whole job is to REPORT the served
    deployment, never to grade it.

    **`mode` is a readout, not a control.** Nothing in the web package writes it: changing
    `auto_trade.mode` is a config-file edit plus a terminal action by design (the TTY gate
    `keel.commands._common._require_interactive_confirmation` exists for), and the browser can
    display that decision and cannot make it. An absent `mode` (`""`) means the config could
    not be read -- the first-run state -- and the badge hides rather than guessing.
    """
    return {
        "version": str(getattr(build, "version", "") or ""),
        "build": str(getattr(build, "full_version", "") or ""),
        "commit": str(getattr(build, "commit", "") or ""),
        "source": str(getattr(build, "source", "") or ""),
        "describe": describe,
        # The served deployment (#597): the config's own word for `auto_trade.mode`, and the two
        # paths that answer "where am I" for a process serving one --db/--config pair. See the
        # module note above for why these are bare strings and why mode is read-only.
        "mode": mode,
        "db_path": db_path,
        "config_path": config_path,
        # keel's central honesty signal, and the one judgement this payload carries: `False` means
        # the running code corresponds to no commit (a dirty tree, or no idea), and `keel.version`
        # treats saying so as more important than looking tidy.
        "reproducible": flag(
            None if build is None else bool(getattr(build, "is_reproducible", False)),
            on="reproducible",
            off="NOT reproducible — this build corresponds to no commit",
            on_state=GOOD,
            off_state=WARN,
        ),
    }


# -- setup (#534) --------------------------------------------------------------------------------

#: `StepKind`, judged -- and the judgement is "can a machine do this for you", never "is something
#: wrong". `judgement` warns because a human must decide it and keel must never decide it for
#: them; `off_venue` warns because keel can neither perform nor VERIFY it, and `render.py`'s own
#: note is the reason: "a green check that verifies nothing turns an open risk into a false
#: assurance".
_STEP_KIND_STATES: Mapping[str, str] = {
    "mechanical": NEUTRAL,
    "operator_input": NEUTRAL,
    "judgement": WARN,
    "off_venue": WARN,
}

#: What each kind of step means for the operator -- what keel may do, what it may only collect,
#: and what it cannot touch at all. `render.py::_STEP_KIND_NOTE`'s wording, carrying #437's whole
#: argument, moved onto the wire so a browser client places it rather than holding a fourth copy.
#:
#: **`operator_input` is here and is NOT in `render.py`'s table.** That table has three entries
#: against `StepKind`'s four, and the missing one is the kind of the real `credentials` step -- so
#: `_STEP_KIND_NOTE.get(kind, "")` renders that step with an EMPTY note today, silently dropping
#: the one line that says what a wizard may and may not do on the step where it matters most. It
#: is the same shape as #548's five: a lookup that misses, defaulted, with nothing raised. Found
#: while porting `/setup` to the client (#537), and fixed here rather than in `render.py` because
#: #540 deletes that function; the browser gets the fourth note, the rendered page keeps its gap
#: until it goes.
_STEP_KIND_NOTES: Mapping[str, str] = {
    "mechanical": "keel can do this for you.",
    "operator_input": (
        "keel needs something only you have -- a credential, a path, a value from the venue. It "
        "records what you supply and asks for nothing it does not need."
    ),
    "judgement": (
        "Yours to decide. keel can record it; it must never choose it for you, and an "
        "attestation without a cited source is refused exactly like a missing one."
    ),
    "off_venue": (
        "Happens in the venue's own dashboard, and keel cannot verify it -- the venue's API "
        "does not expose it. Never shown as done here, because a green check that verifies "
        "nothing turns an open risk into a false assurance."
    ),
}

#: `JobStatus.state`, judged.
_JOB_STATES: Mapping[str, str] = {"running": WARN, "done": GOOD, "failed": BAD}


def _step_payload(item: Any) -> dict[str, Any]:
    """One checklist step.

    `done` is three-valued and stays three-valued: `None` means "could not be determined", which is
    NOT `False`. An unreadable database is not an unseeded one, and reporting it as incomplete
    would send an operator to re-run a step that may already be done -- `StepState.done`'s own
    comment. `flag(None)` renders it absent, so the browser shows a dash where the CLI shows
    `[?]`, and neither claims to know."""
    return {
        "key": item.step.key,
        "title": item.step.title,
        "kind": label(
            item.step.kind.value, state=_STEP_KIND_STATES.get(item.step.kind.value, NEUTRAL)
        ),
        # Selected here for the same reason `activity_payload`'s `status_note` is: prose chosen by
        # a state word is a branch on `Field.value`, and `render.js` may not perform one.
        "kind_note": _STEP_KIND_NOTES.get(item.step.kind.value, ""),
        "stage": item.step.stage.value,
        "why": item.step.why,
        "how": item.step.how,
        "done": flag(item.done, on="done", off="outstanding", on_state=GOOD, off_state=WARN),
        "detail": item.detail,
    }


def _action_input_payload(field: Any) -> dict[str, Any]:
    return {
        "name": field.name,
        "label": field.label,
        "hint": field.hint,
        # `secret=True` is the difference between a `password` input and a `text` one, and between
        # a form that can be submitted safely and one that leaks its own contents into browser
        # history. The client is told the answer rather than deriving it from the field's name.
        "secret": flag(field.secret, on="never echoed back", off="shown as typed"),
        # The SAME fact as `secret`, in the form the client needs to act on rather than to show.
        #
        # Two keys for one boolean looks like duplication and is the opposite: `secret` is a
        # `Field`, and a `Field` is a thing the client DISPLAYS -- Rule 3 of the client's pins
        # forbids `render.js` from reading `.value` at all, precisely so no view can start
        # deciding what a payload means. Choosing between `type="password"` and `type="text"` is
        # not a judgement about a value, it is a rendering instruction, and the server is the one
        # that gives it. Without this the client would have had to read `secret.value`, which is
        # the whole rule.
        "kind": "secret" if field.secret else "text",
        # A closed set of answers, rendered with NOTHING pre-selected: an action that could fill in
        # a field the operator left blank is one that could record something they never supplied.
        "choices": list(field.choices),
    }


def _action_payload(action: Any) -> dict[str, Any]:
    return {
        "key": action.key,
        "title": action.title,
        "detail": action.detail,
        "needs_input": flag(
            action.needs_input, on="needs your input", off="keel can do this unaided"
        ),
        "inputs": [_action_input_payload(field) for field in action.inputs],
    }


def _job_payload(job: Any) -> dict[str, Any]:
    """A background setup job (`keel.commands.jobs`), as JSON.

    `int(job.elapsed_sec)` is the one integer conversion in this file outside `_gmt`, and it is on
    SECONDS, which have no cent to lose -- the same argument Rule 6b's timestamp allowance makes.
    `duration` takes whole seconds because `_human_age`, the CLI's own phrasing, does. A failure
    stays in the payload rather than being cleared: the whole point of running something in the
    background is that nobody was watching when it broke."""
    return {
        "key": job.key,
        "state": label(job.state, state=_JOB_STATES.get(job.state, NEUTRAL)),
        "started_at": moment(job.started_ts),
        "finished_at": moment(job.finished_ts),
        "elapsed": duration(int(job.elapsed_sec)),
        "running": flag(job.is_running, on="running", off="finished"),
        "error": job.error or "",
        # Newest last, unscrolled, exactly as the CLI prints them: an operator who has run `keel
        # fetch` in a terminal should recognise what they are looking at rather than have to learn
        # a second vocabulary for the same thing.
        "lines": list(job.lines),
    }


def setup_payload(
    state: Any,
    *,
    actions: Sequence[Any] = (),
    not_automated: Mapping[str, str] | None = None,
    job: Any = None,
    csrf: str = "",
) -> dict[str, Any]:
    """`keel.commands.setup.inspect`'s `DeploymentState`, as JSON.

    **`csrf` is here since #540, and an earlier revision of this docstring argued it should not
    be.** That argument is recorded rather than deleted, because reversing it was a decision:

        "No CSRF token, and that is the point rather than an omission. `render_setup` takes one
        because it emits `<form method=post>`; this issue ships reads only, and minting a live
        write credential into a GET response would put it into every cached copy, every proxy log
        and every paste of 'here is what the API returned'."

    Two of those three concerns were already answered by the time the form was deleted: `/api/*`
    is `Cache-Control: no-store` and #538's service worker refuses to cache it, so there is no
    cached copy; and the server binds loopback, so there is no proxy. The third -- an operator
    pasting API output into an issue -- is real, and it is what settles the question rather than
    what blocks it: **this token is not a credential on its own.** It authorises nothing without
    the session cookie, and anyone holding that cookie can read this endpoint and mint the same
    token for themselves. A paste of it grants exactly what a paste of a random hex string grants.

    What the alternative would have cost is the reason not to be clever here: delivering it in a
    second, script-readable cookie would take a live token out of a body and put it into
    `document.cookie`, which is a strictly worse place for it on an origin whose session cookie is
    deliberately `HttpOnly`.

    `actions` and `not_automated` are read from `keel.commands.setup`'s own closed registries and
    copied, never filtered here: an action appears only where the registry carries one, which is
    what stops "attest this asset" appearing because somebody edited a front-end.

    """
    from keel.commands.setup import Stage

    return {
        # A bare string, not a `Field`: it is a credential the client SENDS, never a value it
        # displays, and giving it a `display` would invite exactly that.
        "csrf": str(csrf),
        "root": str(state.root),
        "config_path": str(state.config_path),
        "db_path": str(state.db_path),
        "is_new": flag(
            state.is_new,
            on="nothing set up here yet",
            off="a deployment exists here",
            on_state=WARN,
            off_state=NEUTRAL,
        ),
        "has_usable_database": flag(
            state.has_usable_database,
            on="readable",
            off="no schema here yet",
            on_state=GOOD,
            off_state=WARN,
        ),
        # `ready_for(LIVE)` is deliberately absent: `OFF_VENUE` steps can never be OBSERVED, so the
        # answer would be a permanent `False` that says nothing about the deployment. The honest
        # last word on going live belongs to the operator who checked the venue dashboard.
        "ready_for_paper": flag(
            state.ready_for(Stage.PAPER),
            on="ready to run in paper",
            off="not yet ready for paper",
            on_state=GOOD,
            off_state=WARN,
        ),
        "next_step": state.next_step.step.key if state.next_step is not None else "",
        "steps": [_step_payload(item) for item in state.states],
        "actions": [_action_payload(action) for action in actions],
        # Mechanical steps deliberately NOT offered as one-click actions, and why. Carried as data
        # rather than omitted silently, so a gap is visible to the next person rather than looking
        # like an oversight -- and empty is a fine value.
        "not_automated": [
            {"key": key, "why": why} for key, why in sorted((not_automated or {}).items())
        ],
        "job": _job_payload(job) if job is not None else None,
    }


# -- rules (#534) --------------------------------------------------------------------------------


def _rule_row_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        # An identifier, so a bare string -- and one that still sorts numerically, because
        # `order_rows` reads a column as `Decimal` when every value in it parses as one.
        "id": str(row.get("id", "")),
        "kind": str(row.get("kind") or ""),
        "status": label(str(row.get("status") or "")),
        "created_at": moment(row.get("created_at")),
        "promoted_at": moment(row.get("promoted_at")),
        "demoted_at": moment(row.get("demoted_at")),
        # Operator-supplied and open-ended (any rule kind may invent its own), so they cross as
        # strings for the same reason `ActivityEvent.fields` does.
        "params": {
            str(key): stringify(value) for key, value in sorted((row.get("params") or {}).items())
        },
    }


def rules_payload(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """`Repository.get_rules(None)`'s rows, as JSON.

    Read-only, and the page it mirrors says so out loud: promotion happens in the CLI, behind the
    TTY gate, and nothing this API serves can change a rule's status."""
    return {"rules": [_rule_row_payload(row) for row in rows]}


# -- venues (#534) -------------------------------------------------------------------------------


def _venue_payload(info: Any) -> dict[str, Any]:
    """One installed adapter's DECLARED capabilities.

    What the adapter says it can do, never an inference about the operator's keys: a row here is
    not a claim that the venue is configured or reachable (#233). An adapter that failed to
    construct still gets a row, with the failure judged `bad` -- a missing row would read as "not
    installed", which is a different fact and a worse one to be wrong about."""
    return {
        "name": info.name,
        "venue": info.venue,
        "deployment": info.deployment,
        "asset_classes": list(info.asset_classes),
        "supported_orders": list(info.supported_orders),
        "quote_currencies": list(info.quote_currencies),
        "supported_data_feeds": list(info.supported_data_feeds),
        "declared_endpoints": list(info.declared_endpoints),
        "package_version": info.package_version or "",
        "preview": info.preview,
        "session_bound": flag(info.session_bound, on="session-bound", off="stateless"),
        # The #372 funding posture, beside the session one: "cash only" is the declaration
        # every first-party adapter makes, and the off word is the loud one for the day a
        # row ever carries it -- the venueCard's funding cell mirrors `capability_facts`'s
        # "MARGIN-CAPABLE" declaration without inheriting its shout.
        "cash_only": flag(info.cash_only, on="cash only", off="margin-capable"),
        "supports_fee_summary": flag(
            info.supports_fee_summary, on="fee summary", off="no fee summary"
        ),
        # Not `absent()` when there is no error: absent means "not recorded", and "constructed
        # cleanly" is a positive observation. Spelling it out is the same choice
        # `_subscription_payload` makes for an unlimited cap.
        "error": (
            label(info.error, state=BAD)
            if info.error
            else label("none", display="constructed cleanly", state=GOOD)
        ),
    }


#: GOOD/WARN/BAD/NEUTRAL for each readiness state (#233 PR4). `ready` is the success state. `not_
#: permitted` and `malformed_credentials` are genuine faults: a live entry is blocked TODAY by
#: a record rail 20 will veto, or by a credential LOCALLY proven wrong -- the same BAD judgement
#: `_venue_payload`'s own `error` field gives a construction failure, because both describe
#: something actively wrong rather than merely unset. `no_credentials` and `not_installed` are
#: WARN: neither is a deployment FAULT so much as an absence an operator has simply not gotten
#: to yet (an uninstalled optional venue, a credential never set) -- milder than a value that is
#: present and provably wrong, or a venue that has actively refused a placement.
_READINESS_STATE: dict[VenueReadiness, str] = {
    VenueReadiness.READY: GOOD,
    # A dev/stub adapter with no credentials to present is not a FAULT, it is a venue this
    # deployment was never going to trade -- `fake` and `kraken` are always installed, so any
    # non-neutral colour here would put a permanent warning on every venues card for a row whose
    # honest answer is "not applicable".
    VenueReadiness.NOT_TRADEABLE: NEUTRAL,
    VenueReadiness.NOT_INSTALLED: WARN,
    VenueReadiness.NO_CREDENTIALS: WARN,
    # Ignorance, not a fault: the database could not be read, so nothing here is a claim about
    # the venue at all. WARN says "look at this" without asserting anything about the record.
    VenueReadiness.RECORD_UNREADABLE: WARN,
    # Present-and-provably-wrong, unlike the two WARNs above which are absences. Half a
    # credential pair cannot authenticate a single request.
    VenueReadiness.PARTIAL_CREDENTIALS: BAD,
    VenueReadiness.MALFORMED_CREDENTIALS: BAD,
    VenueReadiness.NOT_PERMITTED: BAD,
}


def _readiness_payload(row: VenueReadinessRow) -> dict[str, Any]:
    """One venue's readiness verdict (#233 PR4), as JSON -- a SIBLING of `_venue_payload`, never
    merged into it. `_venue_payload`'s own docstring says a row there "is not a claim that the
    venue is configured or reachable" -- THIS is that claim, kept in its own collection so a
    client reading `venues` alone still gets that #233 guarantee unchanged."""
    return {
        "venue": row.venue,
        "state": label(
            row.state.value,
            display=row.state.value.replace("_", " "),
            state=_READINESS_STATE[row.state],
        ),
        "explanation": row.explanation,
        "next_step": row.next_step or "",
    }


def venues_payload(
    infos: Sequence[Any], readiness: Sequence[VenueReadinessRow] = ()
) -> dict[str, Any]:
    """`keel.commands.brokers.list_installed_brokers`'s rows, plus this deployment's venue
    readiness (#233 PR4) -- two SIBLING top-level keys, `venues` and `readiness`, never merged
    into one row (see `_readiness_payload`). `readiness` defaults to empty so every existing
    caller of this function keeps working unchanged; `keel/web/api.py::read_venues` is the one
    caller that supplies it for real."""
    return {
        "venues": [_venue_payload(info) for info in infos],
        "readiness": [_readiness_payload(row) for row in readiness],
    }


# -- gates (#534) --------------------------------------------------------------------------------


def _capability_payload(capability: Any) -> dict[str, Any]:
    return {
        "surface": capability.surface,
        "invocation": capability.invocation,
        "increases": capability.increases,
        "call_site": f"{capability.module}.{capability.function}",
        "mirrors": (
            f"{capability.mirrors[0]}.{capability.mirrors[1]}" if capability.mirrors else ""
        ),
    }


def gates_payload(gates: Sequence[Any], capabilities: Sequence[Any]) -> dict[str, Any]:
    """`keel/capabilities.py`'s declaration, as JSON.

    A pure declaration -- no config, no database, no network. It describes the BINARY that is
    answering, which is why this endpoint has data to serve on a machine with no deployment on it.

    **No `count` of covered actions**, unlike the HTML page's `esc(len(covered))`. Rule 6e of
    `test_console_thinness.py` bans `len()` in this module, because a count on the wire must be one
    the report already holds -- and `Gate` holds none. Unlike `JournalReport.shown_count` there is
    no report builder to add it to either, `keel/capabilities.py` being a declaration rather than a
    report. A client renders `actions.length`, which is a list length in the language that owns the
    list, not a figure this layer invented.
    """
    return {
        "gates": [
            {
                "name": gate.name,
                "evidence": gate.evidence,
                "fails_closed_against": gate.fails_closed_against,
                "implementation": gate.implementation,
                "actions": [
                    _capability_payload(capability)
                    for capability in capabilities
                    if capability.gate == gate.name
                ],
            }
            for gate in gates
        ]
    }
