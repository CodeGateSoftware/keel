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


def test_open_prints_the_url_when_a_server_is_recorded(home: Path) -> None:
    runtime.record_serving(host="127.0.0.1", port=8765, token="tok", interactive=False)
    result = CliRunner().invoke(cli, ["open", "--port", "8765", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert "http://127.0.0.1:8765/?token=tok" in result.output


def test_open_refuses_when_nothing_is_recorded(home: Path) -> None:
    """Non-zero, because a script that pipes this into a browser must be able to tell."""
    result = CliRunner().invoke(cli, ["open", "--port", "8765", "--no-browser"])
    assert result.exit_code != 0
    assert "8765" in result.output


def test_open_explains_the_interactive_case_rather_than_just_failing(home: Path) -> None:
    """The likeliest confusion this command will cause: a server IS running, started from a
    terminal, and deliberately left no record. "not found" would send the operator looking for a
    bug; naming the reason sends them to the terminal that has the URL."""
    result = CliRunner().invoke(cli, ["open", "--port", "8765", "--no-browser"])
    assert "terminal" in result.output.lower(), result.output


def test_open_does_not_offer_a_url_for_a_process_that_has_gone(home: Path) -> None:
    """A `SIGKILL` leaves the file. Handing over a URL that refuses the browser would read as keel
    being broken rather than as keel being stopped."""
    # A value no English sentence contains. `"tok"` was the first choice and it matched the word
    # "token" in the refusal itself -- a substring assertion failing on prose, which is the same
    # trap in the other direction from a substring assertion PASSING on prose.
    secret = "Z9-stale-secret-Z9"
    runtime.record_serving(host="127.0.0.1", port=8765, token=secret, interactive=False)
    path = runtime.record_path(8765)
    path.write_text(path.read_text().replace('"pid": ' + str(os.getpid()), '"pid": 0'))
    result = CliRunner().invoke(cli, ["open", "--port", "8765", "--no-browser"])
    assert result.exit_code != 0
    assert secret not in result.output, "a stale token was printed anyway"


def test_open_launches_a_browser_by_default_and_can_be_told_not_to(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symmetric with `keel serve --no-open`: the URL is printed either way, so a machine with no
    launcher loses nothing."""
    runtime.record_serving(host="127.0.0.1", port=8765, token="tok", interactive=False)
    opened: list[str] = []
    import keel.commands.open_console as open_mod

    monkeypatch.setattr(open_mod.webbrowser, "open", lambda url: opened.append(url) or True)

    CliRunner().invoke(cli, ["open", "--port", "8765", "--no-browser"])
    assert opened == []

    CliRunner().invoke(cli, ["open", "--port", "8765"])
    assert opened == ["http://127.0.0.1:8765/?token=tok"]


def test_a_browser_that_will_not_launch_does_not_fail_the_command(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`serve` treats the launch as best-effort for the same reason: the URL is already printed,
    and typing it in is a complete fallback."""
    runtime.record_serving(host="127.0.0.1", port=8765, token="tok", interactive=False)
    import keel.commands.open_console as open_mod

    def _boom(_url: str) -> bool:
        raise RuntimeError("no browser here")

    monkeypatch.setattr(open_mod.webbrowser, "open", _boom)
    result = CliRunner().invoke(cli, ["open", "--port", "8765"])
    assert result.exit_code == 0, result.output
    assert "http://127.0.0.1:8765/?token=tok" in result.output
