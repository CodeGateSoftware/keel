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
import uuid
from contextvars import ContextVar, Token
from typing import Any

_cycle_id: ContextVar[str | None] = ContextVar("keel_cycle_id", default=None)

# Attribute name under which `log_event` stashes structured fields on a LogRecord.
_FIELDS_ATTR = "keel_fields"

# Stable payload keys written directly by `JsonFormatter.format`. A caller field with one of
# these names is renamed (not dropped, not allowed to overwrite) -- see module docstring.
_RESERVED = frozenset({"ts", "level", "logger", "event", "cycle_id", "exc"})


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


def log_exception(logger: logging.Logger, event: str, /, **fields: Any) -> None:
    """Emit a structured `event` at ERROR with the active exception's traceback attached.

    Use inside an `except` block. Equivalent to `log_event` at ERROR level plus `exc_info`,
    which `JsonFormatter` renders into the payload's `exc` key.
    """
    logger.log(logging.ERROR, event, exc_info=True, extra={_FIELDS_ATTR: fields})


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

        fields = getattr(record, _FIELDS_ATTR, None)
        if isinstance(fields, dict):
            for key, value in fields.items():
                # Reserved keys are renamed rather than dropped or allowed to overwrite the
                # stable payload key -- see module docstring.
                payload[f"field_{key}" if key in _RESERVED else key] = value

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)
