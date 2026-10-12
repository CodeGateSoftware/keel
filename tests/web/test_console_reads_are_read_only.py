"""The console's reads open a WAL database whose sidecars are gone, and still cannot write (#976).

keel's database is in WAL mode. Once the last connection to it closes and checkpoints,
`keel.db-wal` and `keel.db-shm` may be gone, and a `mode=ro` open then fails with "unable to open
database file": SQLite would have to CREATE the `-shm`, and `mode=ro` forbids it. `setup.inspect`
opened that way, so on a running, set-up deployment the console answered "keel isn't running
here" -- and the stopped view has no HALT.

The fix is a read-write open (`mode=rw`, which creates no database file) with `PRAGMA query_only
= ON`, which makes SQLite itself refuse every write on the connection. Both halves are pinned
here: the read succeeds in that state, and a write through the console's read connection fails.
The halt is the console's one write to the trading database, and it keeps its own opener.

Every database in this module is a scratch copy under `tmp_path`.
"""

from __future__ import annotations

import http.client
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from keel.data.db import connect
from keel.data.repository import Repository
from keel.web import api as web_api
from keel.web import server as web_server
from keel.web.security import HALT_HEADER, SESSION_COOKIE, halt_token


def _strip_sidecars(db_path: str) -> None:
    """Leave the database the way a checkpointing last close leaves it: one file, no `-wal`, no
    `-shm`. Some SQLite builds keep the sidecars on close (persistent WAL), so the state is made
    explicitly rather than hoped for -- after a TRUNCATE checkpoint the WAL holds nothing, so
    removing the files loses no committed row.

    It then proves the state reaches the bug: a `mode=ro` open must fail here, or every test below
    would pass by vacuum."""
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    finally:
        conn.close()
    for suffix in ("-wal", "-shm"):
        Path(db_path + suffix).unlink(missing_ok=True)
    assert sorted(p.name for p in Path(db_path).parent.glob("keel.db*")) == ["keel.db"]
    ro = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError, match="unable to open"):
            ro.execute("SELECT 1 FROM rules").fetchall()
    finally:
        ro.close()


def _kill_switch(db_path: str) -> object:
    conn = connect(db_path)
    try:
        return Repository(conn).get_state("kill_switch")
    finally:
        conn.close()


def _request(
    cfg: web_server.ServeConfig, method: str, path: str, headers: dict[str, str] | None = None
) -> tuple[int, Any]:
    conn = http.client.HTTPConnection(cfg.host, cfg.port, timeout=10)
    sent = {"Host": f"{cfg.host}:{cfg.port}", "Cookie": f"{SESSION_COOKIE}={cfg.token}"}
    sent.update(headers or {})
    try:
        conn.request(method, path, headers=sent)
        response = conn.getresponse()
        body = response.read().decode("utf-8", "replace")
        return response.status, json.loads(body) if body else None
    finally:
        conn.close()


# -- the read succeeds without the sidecars --------------------------------------------------------


def test_inspect_reads_a_wal_database_whose_sidecars_are_gone(
    deployment: tuple[str, str],
) -> None:
    from keel.commands.setup import inspect

    db_path, config_path = deployment
    _strip_sidecars(db_path)

    state = inspect(config_path, db_path)

    assert state.has_usable_database, state


def test_pulse_on_a_deployment_whose_sidecars_are_gone_offers_halt(
    running: web_server.ServeConfig,
) -> None:
    """The issue's symptom, end to end: Pulse is the report and its HALT, not the stopped view --
    and the served token still halts."""
    _strip_sidecars(running.db_path)

    status, document = _request(running, "GET", "/api/pulse")

    assert status == 200
    assert document["engine"]["value"] != "stopped", document["engine"]
    assert document["data"] is not None, "the stopped view would hide HALT"
    assert document["data"]["halt_token"] == halt_token(running.token)

    status, _ = _request(
        running,
        "POST",
        "/api/gates/halt",
        {"X-Keel-Client": "1", HALT_HEADER: document["data"]["halt_token"]},
    )
    assert status == 200
    assert _kill_switch(running.db_path) is True


def test_a_sleeve_sell_lookup_reads_a_wal_database_whose_sidecars_are_gone(
    deployment: tuple[str, str],
) -> None:
    """The setup page's promote path reads the rule's class over the same kind of open, and it
    fails CLOSED -- so before #976 it refused every promote in this state."""
    from keel.commands.setup import _sleeve_sell_kind

    db_path, _config_path = deployment
    _strip_sidecars(db_path)

    assert _sleeve_sell_kind(Path(db_path), 999) is None


def test_venue_readiness_reads_a_wal_database_whose_sidecars_are_gone(
    deployment: tuple[str, str],
) -> None:
    """`/api/venues` reads each venue's trade scope over the same kind of open; before #976 this
    state read as "unreadable" on a database that was fine."""
    from keel.venue_readiness import _read_only_trade_scope

    db_path, _config_path = deployment
    _strip_sidecars(db_path)

    assert _read_only_trade_scope(db_path, "coinbase") == (None, False)


# -- and it still cannot write ---------------------------------------------------------------------


def test_a_write_through_the_console_read_connection_fails(
    deployment: tuple[str, str],
) -> None:
    """The console's read seam is read-only at the ENGINE level, not by call discipline: a row
    write, a schema write and a header write are each refused by SQLite, and the switch the row
    write aimed at is unchanged."""
    db_path, _config_path = deployment
    before = _kill_switch(db_path)
    repo = web_api.open_repo(db_path)
    try:
        conn = repo._conn
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            repo.set_state("kill_switch", True)
        for statement in ("CREATE TABLE intruder (x)", "PRAGMA user_version = 99"):
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                conn.execute(statement)
    finally:
        web_api.close_repo(repo)
    assert _kill_switch(db_path) == before


def test_the_read_only_opener_creates_no_database(tmp_path: Path) -> None:
    """A read of a database that is not there must not bring one into existence -- the console
    would then report a healthy empty install instead of offering setup."""
    from keel.data.db import connect_read_only

    missing = tmp_path / "keel.db"
    with pytest.raises(sqlite3.OperationalError):
        connect_read_only(missing)
    assert list(tmp_path.iterdir()) == []


class _Recorded(sqlite3.Connection):
    """A connection that remembers `query_only` as it stood when it was closed."""

    closed_query_only: int | None = None

    def close(self) -> None:
        try:
            self.closed_query_only = int(self.execute("PRAGMA query_only").fetchone()[0])
        except sqlite3.ProgrammingError:  # already closed
            pass
        super().close()


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[_Recorded]]:
    """Every sqlite3 connection opened while the test runs, server thread included."""
    real = sqlite3.connect
    seen: list[_Recorded] = []

    def spy(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        conn = real(*args, factory=_Recorded, **kwargs)
        seen.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", spy)
    yield seen


def _query_only(conn: _Recorded) -> int:
    if conn.closed_query_only is not None:
        return conn.closed_query_only
    return int(conn.execute("PRAGMA query_only").fetchone()[0])


def test_every_connection_a_console_read_opens_refuses_writes(
    running: web_server.ServeConfig, opened: list[_Recorded]
) -> None:
    """Every JSON read route, `/api/config` and the setup inspection with it, each over a
    sidecar-less WAL database: every connection the console opened to answer them is
    `query_only`. A reader that slipped back to the read-write `connect` fails here by name."""
    _strip_sidecars(running.db_path)
    opened.clear()

    for path in web_api.API_ROUTES:
        status, document = _request(running, "GET", path)
        assert status == 200, (path, document)
        assert document["engine"]["value"] != "stopped", path

    assert len(web_api.API_ROUTES) >= 10, "the sweep must cover the table, not an empty one"
    assert len(opened) >= len(web_api.API_ROUTES), "the spy must see the reads it judges"
    writable = [conn for conn in opened if _query_only(conn) != 1]
    assert writable == []


def test_the_halt_still_writes_after_the_reads(
    running: web_server.ServeConfig, opened: list[_Recorded]
) -> None:
    """The halt is the console's one write to the trading database, and it is not routed through
    the read seam: it engages the switch, over a connection that is not `query_only`."""
    _strip_sidecars(running.db_path)
    _request(running, "GET", "/api/pulse")
    opened.clear()

    status, _ = _request(
        running,
        "POST",
        "/api/gates/halt",
        {"X-Keel-Client": "1", HALT_HEADER: halt_token(running.token)},
    )

    halt_connections = list(opened)
    assert status == 200
    assert halt_connections, "the spy must see the halt's open"
    assert all(_query_only(conn) == 0 for conn in halt_connections)
    assert _kill_switch(running.db_path) is True
