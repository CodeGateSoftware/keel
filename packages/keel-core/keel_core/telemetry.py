"""Structured event logging shared by every keel package and app.

Log records are emitted as one JSON object per line with a stable field set, so that events can
be grouped and queried across processes once engine/ingest/sim run separately. `event` is a
stable identifier (`agent.cycle_start`), never an interpolated sentence -- interpolated messages
cannot be aggregated, and fixing that later means rewriting every call site.

`cycle_id` correlates every event emitted during one engine loop. Once apps are separate
processes it becomes the trace ID, so it is carried in a `ContextVar` rather than threaded
through call signatures.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextvars import ContextVar
from typing import Any

_cycle_id: ContextVar[str | None] = ContextVar("keel_cycle_id", default=None)

# Attribute name under which `log_event` stashes structured fields on a LogRecord.
_FIELDS_ATTR = "keel_fields"

# LogRecord attributes that are never structured fields.
_RESERVED = frozenset(
    {"args", "exc_info", "exc_text", "msg", "message", "stack_info", _FIELDS_ATTR}
)


def new_cycle_id() -> str:
    """Generate a fresh correlation id for one engine cycle."""
    return uuid.uuid4().hex[:16]


def bind_cycle(cycle_id: str | None) -> None:
    """Bind (or clear, with `None`) the cycle id attached to subsequent events."""
    _cycle_id.set(cycle_id)


def current_cycle() -> str | None:
    """The currently bound cycle id, if any."""
    return _cycle_id.get()


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """Emit a structured `event` with arbitrary `fields` attached.

    `event` must be a stable identifier. `fields` values must be JSON-serialisable or have a
    useful `str()` -- `JsonFormatter` falls back to `str()` rather than raising, because a
    logging call must never take down the trade loop.
    """
    logger.log(level, event, extra={_FIELDS_ATTR: fields})


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
                if key not in _RESERVED:
                    payload[key] = value

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)
