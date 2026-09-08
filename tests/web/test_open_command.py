"""`keel open`, and what `keel serve` has to leave behind for it to work (#756).

Under `launchd` nobody reads stdout, so the URL carrying the session token lands in a log file and
an operator's only way back into their own console is `grep`. These tests hold both halves of the
fix: that `serve` records how to reach itself when — and only when — no human is watching, and
that `open` refuses clearly in every case where it cannot help.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from keel.cli import cli
from keel.web import runtime
from keel.web import server as web_server
from keel.web.security import new_session_token


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("KEEL_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def listening() -> object:
    """A port with something really bound to it, yielded as an int.

    `live_record` now probes the port, so every test that expects `keel open` to SUCCEED needs a
    real listener. Three of them used a bare 8765 and passed on the author's machine only because
    a real `keel serve` happened to be running there -- they would have failed in CI, which is the
    same class of environment-dependence as a fixture that reaches the network.

    Backlog room for several probes: nothing here ever `accept()`s, and a backlog of 1 refuses the
    second connection.
    """
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    try:
        yield int(sock.getsockname()[1])
    finally:
        sock.close()


class _StubServer:
    def __init__(self, address: tuple[str, int]) -> None:
        self.server_address = address
        self.RequestHandlerClass = type("H", (), {"cfg": None})
        self.closed = False

    def serve_forever(self) -> None:
        raise KeyboardInterrupt

    def server_close(self) -> None:
        self.closed = True


def _serve(monkeypatch: pytest.MonkeyPatch, home: Path, *, interactive: bool) -> list[str]:
    stub = _StubServer(("127.0.0.1", 8765))
    monkeypatch.setattr(web_server, "ensure_schema", lambda _path: None)
    monkeypatch.setattr(web_server, "build_server", lambda _cfg: stub)
    monkeypatch.setattr(runtime, "stdout_is_interactive", lambda: interactive)
    lines: list[str] = []
    cfg = web_server.ServeConfig(
        host="127.0.0.1",
        port=8765,
        token=new_session_token(),
        db_path=str(home / "keel.db"),
        config_path=str(home / "config.yaml"),
    )
    assert web_server.serve(cfg, echo=lines.append) == 0
    return lines


# -- what serve leaves behind -------------------------------------------------------------------


def test_an_interactive_serve_writes_nothing_and_says_the_token_never_lands(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    """The posture `security.py` describes, unchanged, and the sentence still true."""
    lines = _serve(monkeypatch, home, interactive=True)
    assert not (home / "run").exists()
    assert any("never written to disk" in line for line in lines), lines


def test_a_detached_serve_records_itself_and_says_so(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    """And it must NOT print the never-written sentence, which would then be false.

    The operator is owed the difference: on this path the token is on disk, and a line claiming
    otherwise is exactly the sort of false safety assurance `_session_banner` exists to refuse.
    """
    lines = _serve(monkeypatch, home, interactive=False)
    assert not any("never written to disk" in line for line in lines), lines
    assert any("keel open" in line for line in lines), lines


def test_a_clean_stop_removes_the_record(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    """`serve`'s stub raises `KeyboardInterrupt` from `serve_forever`, which is the Ctrl-C path."""
    _serve(monkeypatch, home, interactive=False)
    assert runtime.read_record(8765) is None, "the record outlived the server that wrote it"


# -- the command --------------------------------------------------------------------------------


def test_open_is_registered_and_documents_itself() -> None:
    result = CliRunner().invoke(cli, ["open", "--help"])
    assert result.exit_code == 0
    assert "--port" in result.output


def test_open_prints_the_url_when_a_server_is_recorded(home: Path, listening: int) -> None:
    runtime.record_serving(host="127.0.0.1", port=listening, token="tok", interactive=False)
    result = CliRunner().invoke(cli, ["open", "--port", str(listening), "--no-browser"])
    assert result.exit_code == 0, result.output
    assert f"http://127.0.0.1:{listening}/?token=tok" in result.output


def test_open_refuses_when_nothing_is_recorded(home: Path) -> None:
    """Non-zero, because a script that pipes this into a browser must be able to tell."""
    port = _a_closed_port()
    result = CliRunner().invoke(cli, ["open", "--port", str(port), "--no-browser"])
    assert result.exit_code != 0
    assert str(port) in result.output


def test_open_explains_the_interactive_case_rather_than_just_failing(home: Path) -> None:
    """The likeliest confusion this command will cause: a server IS running, started from a
    terminal, and deliberately left no record. "not found" would send the operator looking for a
    bug; naming the reason sends them to the terminal that has the URL."""
    result = CliRunner().invoke(cli, ["open", "--port", str(_a_closed_port()), "--no-browser"])
    assert "terminal" in result.output.lower(), result.output


def test_open_does_not_offer_a_url_for_a_process_that_has_gone(home: Path) -> None:
    """A `SIGKILL` leaves the file. Handing over a URL that refuses the browser would read as keel
    being broken rather than as keel being stopped."""
    # A value no English sentence contains. `"tok"` was the first choice and it matched the word
    # "token" in the refusal itself -- a substring assertion failing on prose, which is the same
    # trap in the other direction from a substring assertion PASSING on prose.
    secret = "Z9-stale-secret-Z9"
    port = _a_closed_port()
    runtime.record_serving(host="127.0.0.1", port=port, token=secret, interactive=False)
    path = runtime.record_path(port)
    path.write_text(path.read_text().replace('"pid": ' + str(os.getpid()), '"pid": 0'))
    result = CliRunner().invoke(cli, ["open", "--port", str(port), "--no-browser"])
    assert result.exit_code != 0
    assert secret not in result.output, "a stale token was printed anyway"


def test_open_launches_a_browser_by_default_and_can_be_told_not_to(
    home: Path, monkeypatch: pytest.MonkeyPatch, listening: int
) -> None:
    """Symmetric with `keel serve --no-browser`: the URL is printed either way, so a machine with no
    launcher loses nothing."""
    runtime.record_serving(host="127.0.0.1", port=listening, token="tok", interactive=False)
    opened: list[str] = []
    import keel.commands.open_console as open_mod

    monkeypatch.setattr(open_mod.webbrowser, "open", lambda url: opened.append(url) or True)

    CliRunner().invoke(cli, ["open", "--port", str(listening), "--no-browser"])
    assert opened == []

    CliRunner().invoke(cli, ["open", "--port", str(listening)])
    assert opened == [f"http://127.0.0.1:{listening}/?token=tok"]


def test_a_browser_that_will_not_launch_does_not_fail_the_command(
    home: Path, monkeypatch: pytest.MonkeyPatch, listening: int
) -> None:
    """`serve` treats the launch as best-effort for the same reason: the URL is already printed,
    and typing it in is a complete fallback."""
    runtime.record_serving(host="127.0.0.1", port=listening, token="tok", interactive=False)
    import keel.commands.open_console as open_mod

    def _boom(_url: str) -> bool:
        raise RuntimeError("no browser here")

    monkeypatch.setattr(open_mod.webbrowser, "open", _boom)
    result = CliRunner().invoke(cli, ["open", "--port", str(listening)])
    assert result.exit_code == 0, result.output
    assert f"http://127.0.0.1:{listening}/?token=tok" in result.output


# -- review findings (#759) ----------------------------------------------------------------------


def _a_closed_port() -> int:
    """A port nothing is listening on: bound to get a free number, then released.

    NOT a hard-coded 8765. The first cut used it and failed on the author's own machine, where a
    real `keel serve` was running -- a test that passes only where the feature is unused is worse
    than no test.
    """
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


def test_a_recycled_pid_does_not_make_a_dead_server_look_alive(home: Path) -> None:
    """A pid check alone is not liveness.

    keel dies, the OS hands its pid to something else, and the record reads as live -- so `open`
    prints a token the server no longer honours and the browser gets a 403. That is exactly the
    "keel is broken rather than stopped" confusion the check exists to prevent, and the original
    spec asked for a process active ON THE DESIGNATED PORT.

    This record's pid is THIS process, which is certainly alive and certainly not serving.
    """
    port = _a_closed_port()
    runtime.record_serving(host="127.0.0.1", port=port, token="tok", interactive=False)
    assert runtime.live_record(port) is None, "a live pid was accepted with nothing on the port"


def test_a_server_that_is_actually_listening_is_offered(home: Path) -> None:
    """The other half. A port check that refused everything would pass the test above and break
    the feature outright, so the same record is accepted once something is really bound."""
    import socket

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    # Backlog room for more than one probe: `live_record` connects once per call and this test
    # calls it twice, and nothing here ever `accept()`s, so a backlog of 1 refused the second.
    listener.listen(8)
    port = int(listener.getsockname()[1])
    try:
        runtime.record_serving(host="127.0.0.1", port=port, token="tok", interactive=False)
        assert runtime.live_record(port) is not None
        result = CliRunner().invoke(cli, ["open", "--port", str(port), "--no-browser"])
        assert result.exit_code == 0, result.output
        assert "token=tok" in result.output
    finally:
        listener.close()


# -- stopping deliberately (#760 review) ---------------------------------------------------------


def test_a_sigterm_stops_cleanly_and_takes_the_record_with_it(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    """`launchctl bootout` sends SIGTERM, and that is the documented way to stop a console.

    Python's default SIGTERM disposition terminates the process WITHOUT unwinding, so `serve`'s
    `finally` never ran and `runtime.forget` never fired: every deliberate stop left the record
    behind, holding a dead token, while the plists and the runbook both said the file is deleted
    on shutdown. Measured with a probe process before this was written: marker still present.

    THE SIGNAL IS NOT ACTUALLY DELIVERED HERE. A first cut called `os.kill(os.getpid(), SIGTERM)`
    and, in the un-fixed state, killed pytest itself -- a red that takes the runner with it is
    worse than useless, because a later regression would look like a crashed suite rather than a
    failing test. Invoking the handler `serve` installed is what delivery does, and it fails
    politely.
    """
    import signal

    class _Terminating(_StubServer):
        def serve_forever(self) -> None:
            handler = signal.getsignal(signal.SIGTERM)
            assert callable(handler), f"serve installed no SIGTERM handler (got {handler!r})"
            handler(signal.SIGTERM, None)  # what the kernel does, minus the risk
            raise AssertionError("the SIGTERM handler did not interrupt serve_forever")

    stub = _Terminating(("127.0.0.1", 8765))
    monkeypatch.setattr(web_server, "ensure_schema", lambda _path: None)
    monkeypatch.setattr(web_server, "build_server", lambda _cfg: stub)
    monkeypatch.setattr(runtime, "stdout_is_interactive", lambda: False)
    lines: list[str] = []
    cfg = web_server.ServeConfig(
        host="127.0.0.1",
        port=8765,
        token=new_session_token(),
        db_path=str(home / "keel.db"),
        config_path=str(home / "config.yaml"),
    )
    assert web_server.serve(cfg, echo=lines.append) == 0
    assert stub.closed, "the socket was not closed"
    assert runtime.read_record(8765) is None, "a deliberate stop left its record behind"
    assert any("stopped" in line for line in lines), lines


def test_the_previous_sigterm_handler_is_put_back(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    """`serve` is a library function as well as a command; leaving its handler installed would
    change the behaviour of whatever called it."""
    import signal

    def _mine(_signum: int, _frame: object) -> None:
        return None

    previous = signal.signal(signal.SIGTERM, _mine)
    try:
        _serve(monkeypatch, home, interactive=False)
        assert signal.getsignal(signal.SIGTERM) is _mine, "serve kept the handler it installed"
    finally:
        signal.signal(signal.SIGTERM, previous)


# -- saying WHERE it looked (found by an install that worked) -------------------------------------


def test_the_refusal_names_the_directory_it_searched(home: Path) -> None:
    """The reason the first real install read as a failure when it had actually succeeded.

    `keel open` resolves the deployment from the CURRENT DIRECTORY (`state_root`), and the dev
    repo is itself a deployment root -- it has a `keel.db` and a `config.yaml`. So a command
    chained as `cd <repo> && ... && keel open` searched `<repo>/run/` while four healthy daemons
    were writing to `~/keel/run/`. The message offered three explanations and not the true one,
    because none of them could be: it never said where it had looked.

    A path in the refusal turns that from a hunt into a glance.
    """
    port = _a_closed_port()
    result = CliRunner().invoke(cli, ["open", "--port", str(port), "--no-browser"])
    assert result.exit_code != 0
    assert str(runtime.run_dir()) in result.output, result.output


def test_the_stale_refusal_names_it_too(home: Path) -> None:
    """The other branch. A crashed server and a wrong directory are different problems and an
    operator staring at either one needs the same fact to tell them apart."""
    port = _a_closed_port()
    runtime.record_serving(host="127.0.0.1", port=port, token="Z9-stale-Z9", interactive=False)
    path = runtime.record_path(port)
    path.write_text(path.read_text().replace('"pid": ' + str(os.getpid()), '"pid": 0'))
    result = CliRunner().invoke(cli, ["open", "--port", str(port), "--no-browser"])
    assert result.exit_code != 0
    assert str(runtime.run_dir()) in result.output, result.output
    assert "Z9-stale-Z9" not in result.output, "a stale token was printed anyway"


def test_the_help_says_the_deployment_comes_from_the_working_directory() -> None:
    """`keel open` takes no `--db` and no `--config`, so nothing in its signature hints that the
    answer depends on where you are standing. That is invisible until it bites."""
    result = CliRunner().invoke(cli, ["open", "--help"])
    assert result.exit_code == 0
    lowered = result.output.lower()
    assert "directory" in lowered or "deployment" in lowered, result.output
