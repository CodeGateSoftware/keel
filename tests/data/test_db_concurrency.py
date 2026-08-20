"""Reading this database while something writes it must not break the write (#437).

`keel serve` polls the database every few seconds to render a page, and a background fetch writes
to it for minutes. In SQLite's default rollback journal those two cannot coexist: a writer takes
an EXCLUSIVE lock, readers take SHARED ones. Measured against a real Coinbase fetch:

    page polling every 5s, rollback  -> FAILED at 45s, 31,709 candles ("disk I/O error")
    nobody polling,        rollback  -> ran 150s, 108,202 candles
    polling every 0.2s,    WAL       -> ran 150s, 108,501 candles, 694 clean reads

The failure took the path most likely to be taken: the page tells the operator it will show the
fetch's progress, so watching it is the encouraged behaviour, and watching it is what killed it.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from keel.data.db import BUSY_TIMEOUT_MS, connect, migrate


def test_a_file_database_is_opened_in_wal(tmp_path: Path) -> None:
    """Journal mode is a property of the FILE: the first connection converts it and every later
    one inherits it, so this is what makes an existing deployment safe on next start."""
    db = tmp_path / "keel.db"
    conn = connect(str(db))
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_a_busy_timeout_is_set(tmp_path: Path) -> None:
    """SQLite's default is ZERO -- it raises immediately -- which is the wrong default for a
    process that now reads and writes this file at the same time."""
    conn = connect(str(tmp_path / "keel.db"))
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS
    finally:
        conn.close()


def test_an_in_memory_database_is_not_asked_for_wal() -> None:
    """There is no file to journal, SQLite refuses WAL there, and a shared in-memory database is
    single-connection by nature -- there is nothing to protect."""
    conn = connect(":memory:")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
    finally:
        conn.close()


def test_a_reader_polling_hard_does_not_break_a_writer(tmp_path: Path) -> None:
    """The regression itself, in miniature: one connection writing in a loop while another opens,
    reads and closes as fast as it can. Under a rollback journal this is the shape that produced
    `disk I/O error` against the real venue."""
    db = tmp_path / "keel.db"
    setup = connect(str(db))
    migrate(setup)
    setup.close()

    errors: list[str] = []
    stop = threading.Event()
    reads = {"n": 0}

    def _read() -> None:
        while not stop.is_set():
            try:
                # Read-only, exactly as `keel.commands.setup.inspect` opens it.
                ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                try:
                    ro.execute("SELECT COUNT(*) FROM rules").fetchone()
                    reads["n"] += 1
                finally:
                    ro.close()
            except Exception as exc:  # noqa: BLE001 - the failure being tested is any of them
                errors.append(f"reader: {type(exc).__name__}: {exc}")
                return

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    try:
        writer = connect(str(db))
        try:
            for index in range(400):
                writer.execute(
                    "INSERT INTO rules (kind, params, status, created_at) VALUES (?,?,?,?)",
                    ("turtle_breakout", "{}", "candidate", index),
                )
                writer.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"writer: {type(exc).__name__}: {exc}")
        finally:
            writer.close()
    finally:
        stop.set()
        reader.join(5)

    assert not errors, errors
    assert reads["n"] > 0, "the reader never ran, so this proves nothing"

    check = connect(str(db))
    try:
        assert check.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 400
    finally:
        check.close()


def test_the_sidecar_files_are_not_mistaken_for_databases(tmp_path: Path) -> None:
    """WAL adds `keel.db-wal` and `keel.db-shm`. Both the updater's backup set and the
    deployment-root detector glob `keel*.db`, which must not match them -- a `-wal` treated as a
    database would be backed up as one and, worse, counted as one."""
    from keel_core import paths

    db = tmp_path / "keel.db"
    conn = connect(str(db))
    migrate(conn)
    conn.execute(
        "INSERT INTO rules (kind, params, status, created_at) VALUES ('k','{}','candidate',1)"
    )
    conn.commit()
    conn.close()

    names = {path.name for path in tmp_path.iterdir()}
    assert "keel.db" in names
    matched = {path.name for path in tmp_path.glob("keel*.db")}
    assert matched == {"keel.db"}, matched
    assert paths.is_deployment_root(tmp_path)
