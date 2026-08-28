"""Structured event logging shared by every keel package and app.

Log records are emitted as one JSON object per line with a stable field set, so that events can
be grouped and queried across processes once engine/ingest/sim run separately. `event` is a
stable identifier (`agent.cycle_start`), never an interpolated sentence -- interpolated messages
cannot be aggregated, and fixing that later means rewriting every call site.

`cycle_id` correlates every event emitted during one engine loop. Once apps are separate
processes it becomes the trace ID, so it is carried in a `ContextVar` rather than threaded
through call signatures.

The stable payload keys (`ts`, `level`, `logger`, `event`, `cycle_id`, `exc`) are reserved: a
caller-supplied field with one of those names is never dropped and never allowed to overwrite the
real value. It is renamed to a `field_`-prefixed key instead (`ts` -> `field_ts`). Raising on
collision was rejected -- a logging call must never take down the trade loop, so a rare
caller/library naming clash degrades to a renamed field rather than an exception.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from contextvars import ContextVar, Token
from typing import Any

_cycle_id: ContextVar[str | None] = ContextVar("keel_cycle_id", default=None)

# The venue this process is trading (spec §10.2 names `venue` a stable field). Carried in a
# ContextVar for the same reason `cycle_id` is: threading it through every `log_event` call
# would mean touching every call site now, and touching them all again when the engine stops
# being single-venue -- which is exactly the double revisit §10.2 warns about. Bound once at
# app startup today; bound per-cycle when a process drives more than one venue.
_venue: ContextVar[str | None] = ContextVar("keel_venue", default=None)

# Attribute name under which `log_event` stashes structured fields on a LogRecord.
_FIELDS_ATTR = "keel_fields"

# Stable payload keys written directly by `JsonFormatter.format`. A caller field with one of
# these names is renamed (not dropped, not allowed to overwrite) -- see module docstring.
_RESERVED = frozenset({"ts", "level", "logger", "event", "cycle_id", "exc"})

# Exception type NAMES that mean "the venue was unreachable" rather than "something is wrong" --
# see `is_venue_unreachable` for why this is a name match and not an `isinstance` check. Covers
# the builtin socket errors plus the `requests`/`urllib3` wrappers a broker's HTTP stack raises.
_UNREACHABLE_EXC_NAMES = frozenset(
    {
        "ConnectionError",  # builtin, and requests.exceptions.ConnectionError
        "ConnectionResetError",
        "ConnectionRefusedError",
        "ConnectionAbortedError",
        "TimeoutError",  # builtin, and requests.exceptions.Timeout's socket cause
        "Timeout",
        "ConnectTimeout",
        "ConnectTimeoutError",
        "ReadTimeout",
        "ReadTimeoutError",
        "ProxyError",
        "MaxRetryError",
        "NewConnectionError",
        "NameResolutionError",
        "gaierror",  # socket.gaierror -- DNS not up yet after a wake
    }
)

# Cap on the one-line `error` summary that replaces a traceback on the unreachable path. Long
# enough to keep the host and the underlying cause `requests` nests into its message, short
# enough that the event stays one readable line.
_ERROR_SUMMARY_MAX_CHARS = 200


def new_cycle_id() -> str:
    """Generate a fresh correlation id for one engine cycle."""
    return uuid.uuid4().hex[:16]


def bind_cycle(cycle_id: str | None) -> Token[str | None]:
    """Bind (or clear, with `None`) the cycle id attached to subsequent events.

    Returns a token: pass it to `unbind_cycle` to restore whatever was bound before, rather
    than clobbering it. That matters as soon as anything wraps an outer trace around a cycle
    (an ingest or LLM span, say) -- clearing to `None` on the way out would silently drop the
    outer correlation id, and every event after the inner cycle would go uncorrelated.
    """
    return _cycle_id.set(cycle_id)


def unbind_cycle(token: Token[str | None]) -> None:
    """Restore the cycle id bound before the matching `bind_cycle`."""
    _cycle_id.reset(token)


def bind_venue(venue: str | None) -> Token[str | None]:
    """Bind (or clear, with `None`) the venue attached to subsequent events.

    Returns a token for `unbind_venue`, same contract as `bind_cycle`.

    This is a *default*, not a computed field: a caller that passes `venue=` to `log_event`
    overrides it for that event. That is deliberate -- `subscription.attestation_overdue`
    reports on a specific venue's record, which need not be the one the process is driving.
    """
    return _venue.set(venue)


def unbind_venue(token: Token[str | None]) -> None:
    """Restore the venue bound before the matching `bind_venue`."""
    _venue.reset(token)


def current_venue() -> str | None:
    """The currently bound venue, if any."""
    return _venue.get()


def current_cycle() -> str | None:
    """The currently bound cycle id, if any."""
    return _cycle_id.get()


def log_event(logger: logging.Logger, level: int, event: str, /, **fields: Any) -> None:
    """Emit a structured `event` with arbitrary `fields` attached.

    `logger`, `level`, and `event` are positional-only so a caller field of the same name
    (e.g. `event="x"`) can never collide with these parameters and raise `TypeError` -- a
    logging call must never take down the trade loop.

    `event` must be a stable identifier. `fields` values must be JSON-serialisable or have a
    useful `str()` -- `JsonFormatter` falls back to `str()` rather than raising, for the same
    reason. A field whose name collides with a reserved payload key (`ts`, `level`, `logger`,
    `event`, `cycle_id`, `exc`) is renamed to `field_<name>` by `JsonFormatter`, never dropped
    and never allowed to overwrite the real value.
    """
    logger.log(level, event, extra={_FIELDS_ATTR: fields})


def log_exception(
    logger: logging.Logger, event: str, /, *, level: int = logging.ERROR, **fields: Any
) -> None:
    """Emit a structured `event` at ERROR (or `level`) with the active exception's traceback.

    Use inside an `except` block. Equivalent to `log_event` at the given level plus
    `exc_info`, which `JsonFormatter` renders into the payload's `exc` key. `level` exists
    for the one severity a traceback must not downgrade from: a caller reporting a
    possibly-half-completed action on live money (#502's stop-management roll) logs at
    CRITICAL and still keeps the stack. Like `log_event`'s positional-only guard, `level`
    is keyword-only; no existing caller passes a field of that name.
    """
    logger.log(level, event, exc_info=True, extra={_FIELDS_ATTR: fields})


def is_venue_unreachable(exc: BaseException | None) -> bool:
    """True when `exc` means "could not reach the venue", not "something is wrong".

    Matched on the exception type's NAME, walked over the `__cause__`/`__context__` chain,
    because `requests` wraps the underlying socket/DNS error and the outermost type is not
    always the signal. Matching by name (rather than importing `requests`/`urllib3` and using
    `isinstance`) keeps `keel-core` free of an HTTP dependency it otherwise does not need, and
    keeps the classification working for any broker adapter's HTTP stack.

    `SSLError` is deliberately absent: a failed TLS handshake can mean interception or a bad
    certificate, which an operator must see at ERROR rather than have filed as "wifi is down".
    """
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if type(exc).__name__ in _UNREACHABLE_EXC_NAMES:
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def log_venue_failure(logger: logging.Logger, event: str, /, **fields: Any) -> bool:
    """Emit a broker-call failure at a severity that matches what it actually cost. Returns
    whether the active exception was classified unreachable.

    Use inside an `except` block, in place of `log_exception`, for any call that crosses the
    network to a venue.

    - **Unreachable, outside a trade cycle** (a dashboard's balance refresh while the laptop is
      asleep) -> WARNING, one line, no traceback. Nothing was lost; the caller already fails
      soft. This is the case that motivated the helper: a 35-minute offline window on
      2026-08-06 wrote 60 twenty-frame ERROR tracebacks through `get_accounts`, around a single
      real `401 Unauthorized` that no operator would ever have spotted in the noise.
    - **Unreachable, inside a trade cycle** (`cycle_id` bound) -> ERROR. Here it did cost
      something: rail 13 fails closed on a missing balance, so an order did not go out. Still
      no traceback -- the cause is known and the frames say nothing the summary does not.
    - **Anything else** (auth, malformed response, a bug) -> ERROR with the full traceback,
      byte-for-byte what `log_exception` would have emitted.

    The unreachable paths add `unreachable=True` and a truncated `error` summary. Caller fields
    win over both, on the same principle as `log_event`: a logging call must never raise.
    """
    exc = sys.exc_info()[1]
    if not is_venue_unreachable(exc):
        log_exception(logger, event, **fields)
        return False

    level = logging.ERROR if _cycle_id.get() is not None else logging.WARNING
    summary = f"{type(exc).__name__}: {exc}"
    if len(summary) > _ERROR_SUMMARY_MAX_CHARS:
        summary = summary[:_ERROR_SUMMARY_MAX_CHARS] + "..."
    log_event(logger, level, event, **{"unreachable": True, "error": summary, **fields})
    return True


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }

        cycle = _cycle_id.get()
        if cycle is not None:
            payload["cycle_id"] = cycle

        # Written BEFORE caller fields, and deliberately not in `_RESERVED`: the bound venue is
        # an ambient default that an explicit `venue=` on the call site overrides. Contrast
        # `cycle_id`, which is computed and must not be forgeable -- a caller field named
        # `cycle_id` is renamed rather than allowed to win.
        venue = _venue.get()
        if venue is not None:
            payload["venue"] = venue

        fields = getattr(record, _FIELDS_ATTR, None)
        if isinstance(fields, dict):
            for key, value in fields.items():
                # Reserved keys are renamed rather than dropped or allowed to overwrite the
                # stable payload key -- see module docstring.
                payload[f"field_{key}" if key in _RESERVED else key] = value

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)
