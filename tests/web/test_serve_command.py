"""`keel serve` itself -- registration, the defaults, and the warning that must not be quiet."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from keel.cli import cli
from keel.web import server as web_server
from keel.web.security import new_session_token


def test_serve_is_registered_and_documents_itself() -> None:
    result = CliRunner().invoke(cli, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--host" in result.output
    assert "--port" in result.output
    assert "--no-open" in result.output


def test_the_default_bind_is_loopback() -> None:
    from keel.commands.serve import DEFAULT_HOST, DEFAULT_PORT

    assert DEFAULT_HOST == "127.0.0.1"
    # Not 8080: FreqUI and Jesse's dashboard both live there, and an operator running one
    # alongside keel should not meet the clash as a bind error.
    assert DEFAULT_PORT != 8080


class _StubServer:
    def __init__(self, address: tuple[str, int]) -> None:
        self.server_address = address
        self.RequestHandlerClass = type("H", (), {"cfg": None})
        self.closed = False

    def serve_forever(self) -> None:
        raise KeyboardInterrupt

    def server_close(self) -> None:
        self.closed = True


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, host: str) -> list[str]:
    stub = _StubServer((host, 8765))
    monkeypatch.setattr(web_server, "ensure_schema", lambda _path: None)
    monkeypatch.setattr(web_server, "build_server", lambda _cfg: stub)
    lines: list[str] = []
    cfg = web_server.ServeConfig(
        host=host,
        port=8765,
        token=new_session_token(),
        db_path=str(tmp_path / "keel.db"),
        config_path=str(tmp_path / "config.yaml"),
    )
    assert web_server.serve(cfg, echo=lines.append) == 0
    assert stub.closed
    return lines


def test_a_non_loopback_bind_says_exactly_what_it_exposes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--host 0.0.0.0` is allowed -- an operator on a headless box has a real reason. It must
    not be quiet: what is on the other side is positions, equity and the full trade history, and
    the session token travels in cleartext over plain http."""
    output = "\n".join(_run(monkeypatch, tmp_path, "0.0.0.0"))
    assert "WARNING" in output
    assert "NOT loopback" in output
    assert "cleartext" in output


def test_a_loopback_bind_does_not_warn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    output = "\n".join(_run(monkeypatch, tmp_path, "127.0.0.1"))
    assert "WARNING" not in output
    assert "http://127.0.0.1:8765/?token=" in output


def test_an_unopenable_database_is_a_message_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _boom(_path: str) -> None:
        raise OSError("unable to open database file")

    monkeypatch.setattr(web_server, "ensure_schema", _boom)
    lines: list[str] = []
    cfg = web_server.ServeConfig(
        host="127.0.0.1",
        port=8765,
        token=new_session_token(),
        db_path="/nope/keel.db",
        config_path=str(tmp_path / "config.yaml"),
    )
    assert web_server.serve(cfg, echo=lines.append) == 1
    assert "could not open the database" in "\n".join(lines)


def test_a_port_already_in_use_is_a_message_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(web_server, "ensure_schema", lambda _path: None)

    def _boom(_cfg: Any) -> None:
        raise OSError(48, "Address already in use")

    monkeypatch.setattr(web_server, "build_server", _boom)
    lines: list[str] = []
    cfg = web_server.ServeConfig(
        host="127.0.0.1",
        port=8765,
        token=new_session_token(),
        db_path=str(tmp_path / "keel.db"),
        config_path=str(tmp_path / "config.yaml"),
    )
    assert web_server.serve(cfg, echo=lines.append) == 1
    assert "could not bind 127.0.0.1:8765" in "\n".join(lines)
