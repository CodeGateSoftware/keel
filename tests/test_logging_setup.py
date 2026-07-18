"""Tests for keel.logging_setup: configure_logging (engine-activity logging feature).

`configure_logging` gates two requirements: major-operation/decision INFO logs only fire when
`LoggingConfig.verbose` is `True` (level `INFO`), while errors/exceptions always fire (level
`ERROR` is always enabled, even when `verbose=False`). Rotation is enforced via a stdlib
`RotatingFileHandler` sized from `max_file_mb`/`file_count`.
"""

from __future__ import annotations

import logging
import logging.handlers

from keel.config import LoggingConfig
from keel.logging_setup import configure_logging


def _reset_keel_logger() -> None:
    """Undo any handlers a prior test's `configure_logging` call left on the "keel" logger."""
    logger = logging.getLogger("keel")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def test_configure_logging_builds_rotating_file_handler_with_configured_size_and_backups(
    tmp_path,
):
    _reset_keel_logger()
    log_path = tmp_path / "keel.log"
    cfg = LoggingConfig(verbose=False, file=str(log_path), max_file_mb=10, file_count=4)

    logger = configure_logging(cfg)

    handlers = [h for h in logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(handlers) == 1
    handler = handlers[0]
    assert handler.maxBytes == 10 * 1024 * 1024
    assert handler.backupCount == 3  # file_count - 1: active file + 3 backups = 4 total


def test_configure_logging_level_error_when_not_verbose(tmp_path):
    _reset_keel_logger()
    cfg = LoggingConfig(verbose=False, file=str(tmp_path / "keel.log"))

    logger = configure_logging(cfg)

    assert logger.level == logging.ERROR


def test_configure_logging_level_info_when_verbose(tmp_path):
    _reset_keel_logger()
    cfg = LoggingConfig(verbose=True, file=str(tmp_path / "keel.log"))

    logger = configure_logging(cfg)

    assert logger.level == logging.INFO


def test_configure_logging_idempotent_no_duplicate_handlers(tmp_path):
    _reset_keel_logger()
    cfg = LoggingConfig(verbose=False, file=str(tmp_path / "keel.log"))

    configure_logging(cfg)
    logger = configure_logging(cfg)

    handlers = [h for h in logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(handlers) == 1


def test_configure_logging_second_call_updates_level(tmp_path):
    _reset_keel_logger()
    log_path = str(tmp_path / "keel.log")

    configure_logging(LoggingConfig(verbose=False, file=log_path))
    logger = configure_logging(LoggingConfig(verbose=True, file=log_path))

    assert logger.level == logging.INFO


def test_configure_logging_creates_parent_directory(tmp_path):
    _reset_keel_logger()
    log_path = tmp_path / "nested" / "dir" / "keel.log"
    cfg = LoggingConfig(verbose=False, file=str(log_path))

    configure_logging(cfg)

    assert log_path.parent.is_dir()


def test_configure_logging_verbose_false_suppresses_info_but_logs_error(tmp_path):
    _reset_keel_logger()
    log_path = tmp_path / "keel.log"
    cfg = LoggingConfig(verbose=False, file=str(log_path))

    logger = configure_logging(cfg)
    child = logging.getLogger("keel.somemodule")
    child.info("an info message that should not appear")
    child.error("an error message that should appear")
    for handler in logger.handlers:
        handler.flush()

    content = log_path.read_text()
    assert "an info message" not in content
    assert "an error message that should appear" in content


def test_configure_logging_verbose_true_logs_info_and_error(tmp_path):
    _reset_keel_logger()
    log_path = tmp_path / "keel.log"
    cfg = LoggingConfig(verbose=True, file=str(log_path))

    logger = configure_logging(cfg)
    child = logging.getLogger("keel.somemodule")
    child.info("an info message that should appear")
    child.error("an error message that should appear too")
    for handler in logger.handlers:
        handler.flush()

    content = log_path.read_text()
    assert "an info message that should appear" in content
    assert "an error message that should appear too" in content
