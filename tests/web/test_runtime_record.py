"""The serve runtime record: a live session token on disk, and the rule that bounds it (#756).

`keel serve` mints a session token per process and prints it in a URL. Under `launchd` nobody
reads stdout, so that URL -- token and all -- lands in `StandardOutPath`, a log file at whatever
umask the daemon runs under. An operator's only way back into their own console becomes `grep`.

`keel open` fixes that, and the only way it can is by the server leaving the token somewhere
readable. That is a real weakening of layer 3 in `keel/web/security.py` ("the token is minted per
process and never written to disk"), so it is bounded by one rule, which these tests are about:

    THE RECORD EXISTS ONLY WHEN THE URL IS NOT BEING SHOWN TO A HUMAN.

`sys.stdout.isatty()` is the test. Attached to a terminal, the operator already has the URL and
nothing is written -- today's posture, byte for byte. Not attached, the token is already going
somewhere persistent (a log, a pipe), and a `0600` file in a `0700` directory that is deleted on
shutdown is strictly *less* exposure than the log line that would otherwise be the only way in.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from keel.web import runtime


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("KEEL_HOME", str(tmp_path))
    return tmp_path


def test_nothing_is_written_when_stdout_is_a_terminal(home: Path) -> None:
    """The whole bound on this feature. An interactive `keel serve` keeps the posture its own
    output claims -- and the sentence it prints stays true."""
    runtime.record_serving(host="127.0.0.1", port=8765, token="tok", interactive=True)
    assert runtime.read_record(8765) is None
    assert not (home / "run").exists(), "an interactive run created the runtime directory"


def test_the_record_is_written_when_nobody_is_reading_stdout(home: Path) -> None:
    runtime.record_serving(host="127.0.0.1", port=8765, token="tok", interactive=False)
    record = runtime.read_record(8765)
    assert record is not None
    assert record["token"] == "tok"
    assert record["port"] == 8765
    assert record["pid"] == os.getpid()


def test_the_file_is_unreadable_by_anyone_else(home: Path) -> None:
    """`0600` in a `0700` directory. It holds a live bearer token; group or world read would put
    it within reach of every account on a shared machine, which is worse than the log line this
    exists to replace."""
    runtime.record_serving(host="127.0.0.1", port=8765, token="tok", interactive=False)
    path = runtime.record_path(8765)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600, oct(path.stat().st_mode)
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700, oct(path.parent.stat().st_mode)


def test_the_record_is_removed_on_shutdown(home: Path) -> None:
    """A token that outlives its process authenticates nothing, but it still READS as a way in,
    and an operator following a stale record would be told to open a URL that refuses them."""
    runtime.record_serving(host="127.0.0.1", port=8765, token="tok", interactive=False)
    runtime.forget(8765)
    assert runtime.read_record(8765) is None


def test_a_record_whose_process_is_gone_is_not_offered(home: Path) -> None:
    """Removal on shutdown covers the clean exit. A `SIGKILL`, a panic or a power cut leaves the
    file behind, so liveness is checked on the way OUT as well -- the file is a hint, never the
    authority on whether a server is running."""
    runtime.record_serving(host="127.0.0.1", port=8765, token="tok", interactive=False)
    path = runtime.record_path(8765)
    stale = json.loads(path.read_text())
    # A pid this process can prove is not serving: its own parent's parent is not `keel serve`,
    # and pid 0 is never a real process to signal.
    stale["pid"] = 0
    path.write_text(json.dumps(stale))
    assert runtime.live_record(8765) is None
    assert runtime.read_record(8765) is not None, "read_record must not silently drop a stale file"


def test_a_corrupt_record_is_ignored_rather_than_raising(home: Path) -> None:
    """A truncated write (a crash mid-flush) must not make `keel open` traceback at an operator
    who is already having a bad day."""
    runtime.record_serving(host="127.0.0.1", port=8765, token="tok", interactive=False)
    runtime.record_path(8765).write_text("{not json")
    assert runtime.read_record(8765) is None


def test_the_token_is_never_in_the_filename(home: Path) -> None:
    """Directory listings leak names. The port identifies the record; the secret is inside."""
    runtime.record_serving(host="127.0.0.1", port=8765, token="s3cret-token", interactive=False)
    assert "s3cret-token" not in str(runtime.record_path(8765))
    assert "s3cret-token" not in "".join(p.name for p in runtime.record_path(8765).parent.iterdir())


def test_records_are_per_port_so_several_deployments_coexist(home: Path) -> None:
    """The point of #756: four deployments, four consoles, four ports. One shared file would make
    the last server to start the only one reachable."""
    runtime.record_serving(host="127.0.0.1", port=8765, token="live", interactive=False)
    runtime.record_serving(host="127.0.0.1", port=8766, token="paper", interactive=False)
    live = runtime.read_record(8765)
    paper = runtime.read_record(8766)
    assert live is not None and paper is not None
    assert live["token"] == "live"
    assert paper["token"] == "paper"
    assert runtime.record_path(8765) != runtime.record_path(8766)


def test_the_url_is_rebuilt_from_the_record_and_carries_the_token(home: Path) -> None:
    runtime.record_serving(host="127.0.0.1", port=8765, token="tok", interactive=False)
    record = runtime.read_record(8765)
    assert record is not None
    assert runtime.url_for(record) == "http://127.0.0.1:8765/?token=tok"


def test_an_ipv6_host_is_bracketed_like_serve_prints_it(home: Path) -> None:
    """`ServeConfig.url` brackets it; a second spelling of one rule is a second chance to get it
    wrong, so this asserts the same shape."""
    runtime.record_serving(host="::1", port=8765, token="tok", interactive=False)
    record = runtime.read_record(8765)
    assert record is not None
    assert runtime.url_for(record) == "http://[::1]:8765/?token=tok"


# -- review findings (#759) ----------------------------------------------------------------------


def test_the_file_is_created_at_0600_rather_than_corrected_afterwards(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`write_text` then `chmod` leaves the token world-readable for the window between them.

    Measured under `umask 022`, a `write_text` file is born `0644`. The `0700` directory means no
    other account can traverse in, so the window was not exploitable -- but that is an incidental
    mitigation, and creating a secret file at the mode it needs is one argument to `os.open`.
    CodeQL points at this same line.

    THE TEST WORKS BY REMOVING THE SAFETY NET: with `chmod` neutered, a file that is merely
    corrected afterwards shows its umask mode, and one that is created correctly still shows
    `0600`.
    """
    monkeypatch.setattr(os, "chmod", lambda *args, **kwargs: None)
    monkeypatch.setattr(os, "umask", lambda mask: 0o022)
    runtime.record_serving(host="127.0.0.1", port=8765, token="tok", interactive=False)
    mode = stat.S_IMODE(runtime.record_path(8765).stat().st_mode)
    assert mode == 0o600, f"created at {oct(mode)}; the mode must not depend on a later chmod"


def test_recording_never_brings_a_deployment_root_into_existence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`state_root`: "it never creates a deployment folder, because a deployment folder that does
    not exist is not one this function chose."

    `mkdir(parents=True)` did exactly that. It is the same hazard `server.ensure_schema` refuses
    one directory over -- "a read-only view would bring a deployment into existence merely by
    being started" -- and a first-run `keel serve` on a machine with no deployment is a supported
    state, not an error.
    """
    missing = tmp_path / "no-such-deployment"
    monkeypatch.setenv("KEEL_HOME", str(missing))
    assert runtime.record_serving(host="127.0.0.1", port=8765, token="t", interactive=False) is None
    assert not missing.exists(), "serving created a deployment root as a side effect"
