"""`keel serve` itself -- registration, the defaults, and the warning that must not be quiet."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from keel.cli import cli
from keel.web import security
from keel.web import server as web_server
from keel.web.security import new_session_token


def test_serve_is_registered_and_documents_itself() -> None:
    result = CliRunner().invoke(cli, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--host" in result.output
    assert "--port" in result.output
    assert "--no-browser" in result.output


def test_serve_help_names_the_write_surface_the_server_actually_has() -> None:
    """`keel serve --help` said "the server implements GET and HEAD and nothing else" long after
    `do_POST` grew the setup steps (#437) and the gate routes (#781). The routes named in the help
    are compared against the ones `server.do_POST` dispatches, DERIVED from its own constants and
    from `gates.TIER1_ACTIONS`, so a new gated action that the help forgets fails here."""
    from keel.commands.serve import serve_cmd
    from keel.web import gates
    from keel.web.server import API_GATES_PREFIX, API_SETUP_PREFIX, HALT_KEY

    assert serve_cmd.help is not None
    paragraphs = [" ".join(p.split()) for p in serve_cmd.help.split("\n\n")]
    assert paragraphs[0] == "Serve keel's console on localhost and open it in your browser."

    writes = [p for p in paragraphs if "`POST " in p]
    named = [route for p in writes for route in re.findall(r"`POST (/api/[^`]+)`", p)]
    expected = {
        API_GATES_PREFIX + HALT_KEY,
        *(API_GATES_PREFIX + key for key in gates.TIER1_ACTIONS),
        API_SETUP_PREFIX + "<step>",
    }
    assert sorted(named) == sorted(expected)
    assert [p for p in paragraphs if "GET and HEAD and nothing else" in p] == []

    # And the help a user actually sees carries the same opening line.
    result = CliRunner().invoke(cli, ["serve", "--help"])
    assert result.exit_code == 0
    assert result.output.splitlines()[2].strip() == paragraphs[0]


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


def test_the_startup_line_does_not_call_the_console_read_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The console halts the engine (Pulse) and runs setup steps (Audit & Setup), so the line an
    operator reads at startup must not promise a read-only view."""
    lines = _run(monkeypatch, tmp_path, "127.0.0.1")

    assert lines[0] == "keel is serving its console at:\n\n    " + lines[0].split("    ")[-1]
    assert "read-only" not in "\n".join(lines)


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


# -- --external-host reaches the policy (#648) ----------------------------------------------------


def _policy_from_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *args: str):
    """Invoke `keel serve` for real and hand back the `HostPolicy` it built.

    Captured from the `ServeConfig` the command actually constructs, because the option being
    parsed proves nothing about it reaching the check -- a flag wired to a field nothing reads
    is a security control that exists only in `--help`.
    """
    captured: dict[str, object] = {}

    class _Stub(_StubServer):
        pass

    def build(cfg):  # type: ignore[no-untyped-def]
        captured["cfg"] = cfg
        return _Stub((cfg.host, cfg.port))

    monkeypatch.setattr(web_server, "ensure_schema", lambda _path: None)
    monkeypatch.setattr(web_server, "build_server", build)
    result = CliRunner().invoke(
        cli,
        # DELIBERATELY the deprecated spelling. `--no-browser` is the documented flag since the
        # #760 review; keeping one call site on `--no-open` means the hidden alias is exercised
        # end to end by a test that was not written for it, which is the cheapest kind of
        # regression cover for a flag that must keep working for already-installed plists.
        ["--db", str(tmp_path / "keel.db"), "serve", "--no-open", *args],
    )
    return result, captured.get("cfg")


def test_external_host_from_the_cli_reaches_the_host_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result, cfg = _policy_from_cli(monkeypatch, tmp_path, "--external-host", "keel.example.com")

    assert result.exit_code == 0, result.output
    assert cfg is not None
    policy = cfg.host_policy
    assert policy.permits("keel.example.com:8765"), (
        "--external-host was accepted by Click and never reached the Host check"
    )
    assert not policy.permits("evil.example:8765")
    assert policy.permits("127.0.0.1:8765"), "the loopback rules must be untouched"


def test_no_external_host_flag_leaves_the_server_loopback_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The default is the posture. If this ever passes an external name, the flag has grown a
    default and every other test in this section is decoration."""
    result, cfg = _policy_from_cli(monkeypatch, tmp_path)

    assert result.exit_code == 0, result.output
    assert cfg is not None
    assert cfg.external_hosts == frozenset()
    assert not cfg.host_policy.permits("keel.example.com:8765")


def test_a_wildcard_external_host_stops_the_server_starting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Refused at startup, visibly, once -- not per request. A server that answered everything
    for an hour before anyone noticed is the failure the defence exists to prevent."""
    result, _cfg = _policy_from_cli(monkeypatch, tmp_path, "--external-host", "*.example.com")

    assert result.exit_code != 0, result.output


def test_repeating_the_flag_admits_each_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result, cfg = _policy_from_cli(
        monkeypatch,
        tmp_path,
        "--external-host",
        "a.example.com",
        "--external-host",
        "b.example.com",
    )

    assert result.exit_code == 0, result.output
    assert cfg is not None
    assert cfg.host_policy.permits("a.example.com:8765")
    assert cfg.host_policy.permits("b.example.com:8765")
    assert not cfg.host_policy.permits("c.example.com:8765")


def test_the_flag_normalises_case_and_whitespace_before_the_policy_sees_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A name typed with the casing DNS allows, or pasted with a stray space, must still work.

    `HostPolicy.permits` lowercases the incoming `Host:` and then compares by set membership, so
    an un-normalised entry silently never matches: the operator configured the name, it looks
    right in `--help`, and every request is refused for a reason nothing reports. Normalising at
    the boundary is what keeps the comparison a plain membership test rather than a loop.
    """
    result, cfg = _policy_from_cli(monkeypatch, tmp_path, "--external-host", "  KEEL.Example.COM  ")

    assert result.exit_code == 0, result.output
    assert cfg is not None
    assert cfg.external_hosts == frozenset({"keel.example.com"})
    assert cfg.host_policy.permits("keel.example.com:8765")


def test_an_empty_flag_value_is_dropped_rather_than_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--external-host ''` is an unset variable expanding, not a request to serve everything.

    It must not reach `HostPolicy`, where the empty string is a wildcard spelling and would stop
    the server -- and it must not become an allowlist entry either. Dropping it leaves the
    default posture, which is what an empty value meant.
    """
    result, cfg = _policy_from_cli(monkeypatch, tmp_path, "--external-host", "")

    assert result.exit_code == 0, result.output
    assert cfg is not None
    assert cfg.external_hosts == frozenset()


# -- a wildcard bind must name what it expects (#648) ---------------------------------------------


@pytest.mark.parametrize("wildcard", ["0.0.0.0", "::", "[::]"])
def test_a_wildcard_bind_without_a_named_host_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, wildcard: str
) -> None:
    """The confusing failure, turned into an explanation.

    Binding a wildcard used to produce a server that refused EVERY request: `HostPolicy` then
    expects `Host: 0.0.0.0`, which no browser sends, so the bind succeeded, the URL printed, and
    nothing worked behind a 403 that blamed the address. It failed closed -- right direction,
    wrong explanation. The operator concludes "keel is broken", not "keel does not know which
    name to expect".
    """
    result, _cfg = _policy_from_cli(monkeypatch, tmp_path, "--host", wildcard)

    assert result.exit_code != 0, result.output
    assert "every interface" in result.output


def test_a_wildcard_bind_is_permitted_once_a_host_is_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Not a blocklist. A wildcard bind is exactly the case where the name cannot be derived --
    every interface has a different one -- so it is the one bind that requires stating it."""
    result, cfg = _policy_from_cli(
        monkeypatch, tmp_path, "--host", "0.0.0.0", "--external-host", "keel.example.com"
    )

    assert result.exit_code == 0, result.output
    assert cfg is not None
    assert cfg.host_policy.permits("keel.example.com:8765")


def test_a_specific_non_loopback_bind_is_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--host 10.0.0.5` derives its own name and always worked. This must not become collateral
    damage of the wildcard refusal."""
    result, cfg = _policy_from_cli(monkeypatch, tmp_path, "--host", "10.0.0.5")

    assert result.exit_code == 0, result.output
    assert cfg is not None
    assert cfg.host_policy.permits("10.0.0.5:8765")


# -- the remote session lifetime (#648) -----------------------------------------------------------


def test_a_loopback_server_has_no_session_expiry(tmp_path: Path) -> None:
    """A decision, not an omission. On loopback the population who could use a leaked token is
    software already running as this operator, and no session lifetime helps against that."""
    cfg = web_server.ServeConfig(
        host="127.0.0.1",
        port=8765,
        token=new_session_token(),
        db_path=str(tmp_path / "keel.db"),
        config_path=str(tmp_path / "config.yaml"),
    )

    assert cfg.session_expired_at is None


def test_configuring_a_remote_host_bounds_the_session(tmp_path: Path) -> None:
    """A remote origin changes the population to anyone who can reach the tunnel, and the
    30-day cookie is a BROWSER hint an attacker holding the token ignores entirely -- so the
    bound has to be enforced on this side of the wire or it is not a bound."""
    cfg = web_server.ServeConfig(
        host="127.0.0.1",
        port=8765,
        token=new_session_token(),
        db_path=str(tmp_path / "keel.db"),
        config_path=str(tmp_path / "config.yaml"),
        external_hosts=frozenset({"keel.example.com"}),
        started_at=1_000_000.0,
    )

    assert cfg.session_expired_at == 1_000_000.0 + security.REMOTE_SESSION_MAX_AGE_SECONDS


def test_the_remote_lifetime_is_bounded_at_both_ends() -> None:
    """A lifetime is only useful between two limits, and both are claims the docstring makes.

    **Shorter than the cookie** the browser keeps, or the enforced bound never bites before the
    browser stops sending the cookie anyway and the mechanism is decorative.

    **Long enough for a working day**, or it is not a session lifetime but an outage: a value
    small enough to expire mid-use would be indistinguishable, to the operator, from the
    "refuses every request" failure the wildcard-bind refusal exists to prevent. A mutation run
    set this to one second and every test still passed -- which is why the floor is here.
    """
    assert security.REMOTE_SESSION_MAX_AGE_SECONDS < security.SESSION_COOKIE_MAX_AGE_SECONDS
    assert security.REMOTE_SESSION_MAX_AGE_SECONDS >= 8 * 60 * 60


def test_the_entropy_arithmetic_is_recorded_beside_the_token() -> None:
    """#648 asked for a brute-force posture and the honest answer is that there is no brute-force
    threat: 256 bits is ~1.2e77 values. A rate limiter added to stop guessing would be theatre,
    and this constant exists so the number travels with that claim instead of being re-derived
    by whoever proposes one."""
    assert security.TOKEN_ENTROPY_BITS == security._TOKEN_BYTES * 8
    assert security.TOKEN_ENTROPY_BITS >= 128
