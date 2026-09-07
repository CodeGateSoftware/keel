"""The four console daemons: `com.keel.serve.*.plist` (#756).

The deployment runs four profiles, and until now each one's console was a terminal an operator had
to keep open. These plists make them survive a reboot and a crash, which is the whole of step 4 --
and which is only usable because `keel open` (#759) exists to hand back a URL nobody watched print.

WHAT THESE TESTS ARE FOR. A plist is data an operator copies to `~/Library/LaunchAgents`, and it
is the one kind of file in this repository where a typo is silent: launchd does not validate the
program it is given, and a wrong `--db` produces a console that reports the wrong deployment's
positions with total confidence. So the pairs are asserted here, in the repository, where a diff
shows them.

WHAT THEY CANNOT PROVE, stated so a green run is not read as more than it is: that launchd
actually loads these, that `RunAtLoad` fires at boot, that `KeepAlive` restarts a killed server,
or -- the assumption the whole `keel open` design rests on -- that a launchd-started process gets
a stdout that is not a tty. That last one is an OBSERVATION an operator makes once, and #756
records it.
"""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Every console daemon, with the deployment it serves. **The port is the identity**: one `keel
#: serve` process binds one port, so two profiles sharing a number means the second to start dies
#: on a bind error and the operator meets a missing console rather than a clash.
#:
#: The wrapper is what pins config to database (`keel-live` etc.), which is why these invoke the
#: wrapper rather than `keel` directly: `--db` defaults to `keel.db`, so a plist that spelled the
#: arguments itself could serve the PAPER ledger under the live profile's name. The wrappers exist
#: to make that unrepresentable and this table inherits the guarantee.
DAEMONS: tuple[tuple[str, str, int], ...] = (
    ("com.keel.serve.live", "keel-live", 8765),
    ("com.keel.serve.paperforward", "keel-paper", 8766),
    ("com.keel.serve.paper-hourly", "keel-paperhourly", 8767),
    ("com.keel.serve.paper-equities", "keel-equities", 8768),
)


def _plist(label: str) -> dict:
    return plistlib.loads((REPO_ROOT / f"{label}.plist").read_bytes())


@pytest.mark.parametrize(("label", "wrapper", "port"), DAEMONS)
def test_each_plist_is_well_formed_xml(label: str, wrapper: str, port: int) -> None:
    """Parsed with a STRICT parser, the requirement `com.keel.live.plist` already carries: XML
    forbids `--` inside a comment, and CoreFoundation's leniency about it would let a file through
    that ordinary tooling -- and these tests -- cannot read."""
    _plist(label)


@pytest.mark.parametrize(("label", "wrapper", "port"), DAEMONS)
def test_the_label_matches_the_filename(label: str, wrapper: str, port: int) -> None:
    """`launchctl` addresses a job by its Label, not by its path. A mismatch means the command in
    the runbook stops or restarts something other than the file the operator just edited."""
    assert _plist(label)["Label"] == label


@pytest.mark.parametrize(("label", "wrapper", "port"), DAEMONS)
def test_each_serves_its_own_deployment_through_its_own_wrapper(
    label: str, wrapper: str, port: int
) -> None:
    """The pairing that matters. `--db` defaults to `keel.db`, so a console started without the
    wrapper could show the paper ledger while the page says live."""
    arguments = _plist(label)["ProgramArguments"]
    assert arguments[0] == "/bin/bash"
    assert arguments[1].endswith(f"/{wrapper}"), arguments
    assert "serve" in arguments
    assert (REPO_ROOT / wrapper).is_file(), f"{wrapper} is not in this repository"


@pytest.mark.parametrize(("label", "wrapper", "port"), DAEMONS)
def test_each_pins_its_port_rather_than_taking_the_default(
    label: str, wrapper: str, port: int
) -> None:
    """Four servers, one default. Unpinned, all four would ask for 8765 and three would die."""
    arguments = _plist(label)["ProgramArguments"]
    assert "--port" in arguments, arguments
    assert arguments[arguments.index("--port") + 1] == str(port), arguments


def test_no_two_consoles_ask_for_the_same_port() -> None:
    """Asserted over the TABLE as well as per-file, because the failure is a collision and a
    per-file check cannot see one."""
    ports = [port for _label, _wrapper, port in DAEMONS]
    assert len(set(ports)) == len(ports), f"port collision: {ports}"
    on_disk = []
    for label, _wrapper, _port in DAEMONS:
        arguments = _plist(label)["ProgramArguments"]
        on_disk.append(arguments[arguments.index("--port") + 1])
    assert len(set(on_disk)) == len(on_disk), f"two plists bind the same port: {on_disk}"


@pytest.mark.parametrize(("label", "wrapper", "port"), DAEMONS)
def test_no_daemon_launches_a_browser(label: str, wrapper: str, port: int) -> None:
    """`keel serve` opens one by default. At boot that is a browser window nobody asked for, on a
    machine that may have no session at all -- and under launchd the launch would be attributed to
    a process with no GUI context."""
    assert "--no-open" in _plist(label)["ProgramArguments"]


@pytest.mark.parametrize(("label", "wrapper", "port"), DAEMONS)
def test_it_comes_back_after_a_reboot_and_after_a_crash(
    label: str, wrapper: str, port: int
) -> None:
    """The two halves of the request, and they are two different keys.

    `RunAtLoad` covers the boot; `KeepAlive` covers the crash. Neither implies the other, and a
    file with only the first is the shape that looks right until something dies at 3am.
    """
    parsed = _plist(label)
    assert parsed["RunAtLoad"] is True
    assert parsed["KeepAlive"] is True


@pytest.mark.parametrize(("label", "wrapper", "port"), DAEMONS)
def test_each_writes_its_own_log_and_none_share_a_file(label: str, wrapper: str, port: int) -> None:
    """Two daemons appending to one file interleave, and the console log is where the URL lands on
    a build that predates `keel open` -- or where an operator looks when `keel open` says the
    server is gone."""
    parsed = _plist(label)
    assert parsed["StandardOutPath"] != parsed["StandardErrorPath"]
    assert label.rsplit(".", 1)[-1] in parsed["StandardOutPath"]


def test_every_log_path_is_unique_across_the_four() -> None:
    paths = []
    for label, _wrapper, _port in DAEMONS:
        parsed = _plist(label)
        paths.extend([parsed["StandardOutPath"], parsed["StandardErrorPath"]])
    assert len(set(paths)) == len(paths), f"two daemons share a log file: {paths}"


@pytest.mark.parametrize(("label", "wrapper", "port"), DAEMONS)
def test_the_working_directory_is_the_deployment_the_wrapper_expects(
    label: str, wrapper: str, port: int
) -> None:
    """The wrappers `cd` to their own directory, so this is belt and braces -- and it is what makes
    the relative `./.venv/bin/keel` inside them resolve if that ever changes."""
    parsed = _plist(label)
    assert parsed["WorkingDirectory"].endswith("/keel"), parsed["WorkingDirectory"]
    assert parsed["ProgramArguments"][1].startswith(parsed["WorkingDirectory"] + "/")


@pytest.mark.parametrize(("label", "wrapper", "port"), DAEMONS)
def test_no_plist_carries_a_credential_VALUE(label: str, wrapper: str, port: int) -> None:
    """These files are committed, so anything secret in one is secret in git history forever.

    Asserted over the PARSED VALUES, not over the file's text. The first cut forbade the substring
    "token" and failed on the four plists' own prose, which explains what the session token is and
    how to get a fresh one -- a scan that cannot tell an explanation from a secret is the same trap
    that has bitten this repository's client tests repeatedly, arriving here in reverse.

    `EnvironmentVariables` is refused outright rather than inspected: a console needs no secret to
    read a database, `keel serve` holds no venue credential by design (#707), and the day this
    file grows a place to put one is the day that design has quietly changed.
    """
    parsed = _plist(label)
    assert "EnvironmentVariables" not in parsed, (
        "a read-only console needs no environment secret; keel serve holds no venue credential"
    )

    def _strings(value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [s for v in value.values() for s in _strings(v)]
        if isinstance(value, list):
            return [s for v in value for s in _strings(v)]
        return []

    for text in _strings(parsed):
        # A session token is 32 bytes of url-safe base64 (`security.SESSION_TOKEN_BYTES`), so ~43
        # characters with no spaces or path separators. Nothing this plist legitimately carries
        # looks like that: the longest real values are paths, which contain "/".
        bare = text.strip()
        looks_secret = len(bare) >= 32 and "/" not in bare and " " not in bare and "." not in bare
        assert not looks_secret, f"{label} carries something secret-shaped: {bare[:12]}..."
