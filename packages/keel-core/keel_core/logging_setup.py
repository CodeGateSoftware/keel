"""Central engine-activity logging setup.

`configure_logging` is the one place `LoggingConfig` becomes real `logging` machinery: it
attaches a rotating file handler to the package logger (`logging.getLogger("keel")`), which
every module's own `logging.getLogger(__name__)` child logger propagates up to.

**Toggle semantics.** `cfg.verbose=False` (the default) sets the "keel" logger to `ERROR` level
-- major-operation/decision `logger.info(...)` calls sprinkled through the codebase are then
suppressed at the logger itself (never even reach the handler), while `logger.error`/
`logger.exception` calls always get through regardless of the toggle. `cfg.verbose=True` sets
`INFO`, so both classes of log line are recorded.

**Rotation.** `RotatingFileHandler(maxBytes=cfg.max_file_mb * 1024 * 1024, backupCount=...)`
caps each file at `cfg.max_file_mb` and keeps `cfg.file_count` files TOTAL (the active file plus
its rotated backups) -- hence `backupCount = cfg.file_count - 1`.

**Format.** Log lines are single-line JSON objects (see `keel_core.telemetry.JsonFormatter`), not
plaintext `%(asctime)s ...` text -- this lets events be aggregated and queried across processes.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from keel_core.alerting import install_alerting, resolve_webhook_url
from keel_core.config import LoggingConfig
from keel_core.telemetry import JsonFormatter


def configure_logging(cfg: LoggingConfig) -> logging.Logger:
    """Attach a rotating file handler to the `"keel"` package logger and return it.

    Idempotent: calling this more than once (e.g. once per CLI invocation) never accumulates
    duplicate handlers -- any existing `RotatingFileHandler`s on the logger are removed first --
    and each call still updates the logger's level from `cfg.verbose`.
    """
    logger = logging.getLogger("keel")

    for handler in list(logger.handlers):
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            logger.removeHandler(handler)
            handler.close()

    Path(cfg.file).parent.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        cfg.file,
        maxBytes=cfg.max_file_mb * 1024 * 1024,
        backupCount=cfg.file_count - 1,
    )
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)

    logger.setLevel(logging.INFO if cfg.verbose else logging.ERROR)
    logger.propagate = False

    # CRITICAL escalations off this machine, when a webhook is configured. A no-op otherwise, so
    # an offline-first install makes no network call it did not ask for. Attached here because
    # this is already the one place handlers are owned and de-duplicated; the level lives on the
    # handler, so `cfg.verbose` cannot turn the alert channel into a firehose.
    install_alerting(logger, resolve_webhook_url())

    return logger


__all__ = ["configure_logging"]
