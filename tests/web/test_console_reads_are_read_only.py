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

import ast
import hashlib
import http.client
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import pytest

from keel.data.db import _read_only_uri, connect, connect_read_only, migrate
from keel.data.repository import Repository
from keel.web import api as web_api
from keel.web import server as web_server
from keel.web.security import HALT_HEADER, SESSION_COOKIE, halt_token


def _strip_sidecars(db_path: str) -> None:
    """Leave the database the way a checkpointing last close leaves it: one file, no `-wal`, no
    `-shm`. Some SQLite builds keep the sidecars on close (persistent WAL), so the state is made
    explicitly rather than hoped for -- after a TRUNCATE checkpoint the WAL holds nothing, so
    removing the files loses no committed row. Whether `mode=ro` can open this state depends on
    the SQLite build; `test_the_scratch_state_is_the_one_976_reported` says which one this is."""
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    finally:
        conn.close()
    for suffix in ("-wal", "-shm"):
        Path(db_path + suffix).unlink(missing_ok=True)
    assert sorted(p.name for p in Path(db_path).parent.glob("keel.db*")) == ["keel.db"]


def _mode_ro_opens(db_path: str) -> bool:
    ro = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        ro.execute("SELECT 1 FROM rules").fetchall()
    except sqlite3.OperationalError as exc:
        assert "unable to open" in str(exc), exc
        return False
    finally:
        ro.close()
    return True


def test_the_scratch_state_is_the_one_976_reported(deployment: tuple[str, str]) -> None:
    """The fixture reaches the bug: on a SQLite build like the one #976 was found on (macOS,
    3.51.0), `mode=ro` cannot open the sidecar-less file -- which is what every test below then
    reads successfully. Some builds (Linux CI) create the `-shm` even under `mode=ro`; there the
    old open worked too, the tests below still pin the read-only half, and this one says so
    rather than passing by vacuum."""
    db_path, _config_path = deployment
    _strip_sidecars(db_path)
    if _mode_ro_opens(db_path):
        pytest.skip("this SQLite build opens a sidecar-less WAL database under mode=ro")
    assert not Path(db_path + "-shm").exists()


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


# -- a reader closing last must not checkpoint the trading database (#977 review) ---------------


def _db_with_unckeckpointed_wal(path: Path) -> None:
    """A database whose recent rows live ONLY in the `-wal`: the shape the agent leaves while it
    runs. The writer turns auto-checkpoint and checkpoint-on-close off so the state is exact."""
    conn = connect(str(path))
    migrate(conn)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA wal_autocheckpoint = 0")
    writer.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
    Repository(writer).set_state("wal_only_row", {"n": 1})
    writer.close()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_console_reader_closing_last_leaves_the_database_file_untouched(tmp_path: Path) -> None:
    """**`query_only` does not stop the checkpoint a last close performs.** A console reader that
    closes after the agent has gone would fold the WAL into `keel.db`; that is a write to the
    trading database from a reader. `connect_read_only` disables checkpoint-on-close, so the main
    file is byte-identical and the `-wal` is left for the writer."""
    db = tmp_path / "keel.db"
    _db_with_unckeckpointed_wal(db)
    wal = Path(f"{db}-wal")
    assert wal.exists() and wal.stat().st_size > 0, (
        "the scratch state must hold an un-checkpointed WAL"
    )
    before = (_sha256(db), db.stat().st_size)

    conn = connect_read_only(db)
    try:
        assert Repository(conn).get_state("wal_only_row", default=None) == {"n": 1}
    finally:
        conn.close()

    assert (_sha256(db), db.stat().st_size) == before
    assert wal.exists() and wal.stat().st_size > 0


@pytest.mark.parametrize(
    ("path", "uri"),
    [
        (PureWindowsPath(r"\\server\share\keel.db"), "file:////server/share/keel.db?mode=rw"),
        (PureWindowsPath(r"C:\x y\keel.db"), "file:///C:/x%20y/keel.db?mode=rw"),
        (PurePosixPath("/a b/#?%/keel.db"), "file:///a%20b/%23%3F%25/keel.db?mode=rw"),
    ],
    ids=["unc", "drive-with-space", "posix-reserved-characters"],
)
def test_the_read_only_uri_is_built_for_every_path_shape(path: PurePosixPath, uri: str) -> None:
    """A URI is the one place a path's `#`, `?` and `%` change meaning, and a Windows path has no
    leading slash for `file://` to attach to. Pure paths, so no Windows is needed to pin it."""
    assert _read_only_uri(path) == uri


def test_a_database_under_a_directory_with_awkward_characters_opens(tmp_path: Path) -> None:
    awkward = tmp_path / "a b#?%\u00e9"
    awkward.mkdir()
    db = awkward / "keel.db"
    conn = connect(str(db))
    migrate(conn)
    Repository(conn).set_state("k", {"v": 1})
    conn.close()

    reader = connect_read_only(db)
    try:
        assert Repository(reader).get_state("k", default=None) == {"v": 1}
    finally:
        reader.close()


_REPO = Path(__file__).resolve().parents[2]
#: What the console's read path must never send to SQLite: each of these either undoes
#: `query_only` (it is per-connection and reversible by the connection itself), reaches another
#: file, or writes the database file outside an ordinary statement.
_FORBIDDEN_SQL = ("query_only", "attach", "wal_checkpoint", "vacuum")


def _console_read_path_files() -> list[Path]:
    return [
        *sorted((_REPO / "keel" / "web").glob("*.py")),
        _REPO / "keel" / "commands" / "setup.py",
        _REPO / "keel" / "commands" / "pulse.py",
        _REPO / "keel" / "venue_readiness.py",
        # #977 round 2: the readers' own SQL lives HERE, not in the modules above (the web
        # modules hold five execute calls between them; `Repository` holds ~70). A banned
        # statement added to a Repository read method reaches the read-only connection.
        _REPO / "keel" / "data" / "repository.py",
        _REPO / "keel" / "data" / "audit.py",
        _REPO / "keel" / "data" / "db.py",
    ]


#: (file, enclosing function) -> the exact SQL strings allowed there. `connect_read_only` is the
#: one place `query_only` is SET (to ON); allow-listed by function AND exact string, never by
#: substring, so `PRAGMA query_only = OFF` in the same function still fails.
_ALLOWED_SQL = {("db.py", "connect_read_only"): {"PRAGMA query_only = ON"}}

#: db.py functions that are WRITE paths by construction and never run on a read-only connection:
#: `connect` opens the read-write connection, `migrate` and the `_migrate_*` steps change schema.
#: They are excluded by name (not by file), and none of them issues a banned statement today --
#: the exclusion only keeps a future checkpoint-on-purpose there from tripping the reader ban.
_WRITE_ONLY_FUNCTIONS = {("db.py", "connect"), ("db.py", "migrate")}


def _is_write_only(file_name: str, function: str | None) -> bool:
    return (file_name, function) in _WRITE_ONLY_FUNCTIONS or (
        file_name == "db.py" and function is not None and function.startswith("_migrate")
    )


def _execute_string_constants(tree: ast.AST) -> list[tuple[str | None, str]]:
    """Every `.execute`/`.executemany`/`.executescript` call, as (enclosing function, SQL
    string) -- one row per string constant inside its args, and one `(function, "")` row for a
    call with none (so the call count is `len(result)` at least)."""
    rows: list[tuple[str | None, str]] = []

    def visit(node: ast.AST, function: str | None) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            function = node.name
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"execute", "executemany", "executescript"}
        ):
            strings = [
                n.value
                for arg in node.args
                for n in ast.walk(arg)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            ]
            rows.extend([(function, text) for text in strings] or [(function, "")])
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(tree, None)
    return rows


def test_the_console_read_path_never_asks_sqlite_to_lift_read_only() -> None:
    """**`query_only` is per-connection and the connection can switch it off; ATTACH and the
    checkpoint pragmas are not blocked by it.** Read-only therefore rests on SQLite refusing
    ordinary writes PLUS no console reader issuing those statements. This is the second half,
    pinned structurally over the AST of every module on the console's read path."""
    files = _console_read_path_files()
    assert all(f.exists() for f in files), [str(f) for f in files if not f.exists()]
    total = 0
    offenders: list[tuple[str, str | None, str]] = []
    for file in files:
        for function, text in _execute_string_constants(ast.parse(file.read_text())):
            if _is_write_only(file.name, function):
                continue
            total += 1
            if text in _ALLOWED_SQL.get((file.name, function), set()):
                continue
            if any(word in text.lower() for word in _FORBIDDEN_SQL):
                offenders.append((file.name, function, text))

    assert total >= 50, f"the walker found only {total} SQL calls: it would pass by vacuum"
    assert offenders == []


def test_the_forbidden_sql_pin_sees_a_banned_statement_in_a_repository_method() -> None:
    """The walker itself, on a planted source: an `OFF` pragma beside the one allowed `ON`, and
    one in a Repository-shaped method, are both reported."""
    src = (
        "def connect_read_only(c):\n"
        "    c.execute('PRAGMA query_only = ON')\n"
        "    c.execute('PRAGMA query_only = OFF')\n"
        "class R:\n"
        "    def read(self):\n"
        "        self.c.executemany('VACUUM', [])\n"
    )
    rows = _execute_string_constants(ast.parse(src))
    flagged = [
        (f, t)
        for f, t in rows
        if t not in _ALLOWED_SQL.get(("db.py", f), set())
        and any(w in t.lower() for w in _FORBIDDEN_SQL)
    ]
    assert flagged == [("connect_read_only", "PRAGMA query_only = OFF"), ("read", "VACUUM")]
