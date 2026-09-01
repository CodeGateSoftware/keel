"""A reader that hangs up must not produce a traceback (#663).

`keel brokers list | head -1` is an ordinary thing to type, and so is piping into `less` and
pressing `q`. Both close the pipe while keel is still writing. On POSIX the next write raises
`BrokenPipeError`; **on Windows it raises `OSError` with `errno.EINVAL`** -- errno 22, not 32 --
which is why nothing caught it and why the 0.13.0 Windows release leg died mid-render:

    File "keel\\commands\\brokers.py", line 396, in brokers_list
        click.echo(line)
    OSError: [Errno 22] Invalid argument

The trigger was `release.yml`'s `brokers list | head -1`, reachable only because #624 grew that
command's output from 4 lines to 35 -- with four it finished before `head` exited. Nothing about
the failure is specific to `brokers list`; it is merely the command whose output outgrew the
buffer first.

**What this module can and cannot establish.** The `BrokenPipeError` path runs here. The Windows
`EINVAL` path cannot -- this suite runs on Linux and macOS in CI, where a closed pipe never
raises errno 22 -- so it is covered by calling the handler with a constructed `OSError` rather
than by a real closed pipe. That is a weaker guarantee and is stated rather than implied.
"""

from __future__ import annotations

import errno
import subprocess
import sys

import pytest

from keel.cli import _is_closed_stdout


def _raise(exc: BaseException):
    """A stand-in for `cli` that fails the way a closed reader makes it fail."""

    def _fail(*_a: object, **_k: object) -> None:
        raise exc

    return _fail


def test_a_posix_broken_pipe_is_recognised() -> None:
    assert _is_closed_stdout(BrokenPipeError()) is True


@pytest.mark.parametrize("code", [errno.EPIPE, errno.EINVAL])
def test_the_windows_and_posix_errnos_are_both_recognised(code: int) -> None:
    """`EINVAL` is the Windows spelling and the whole reason #663 escaped. `EPIPE` is here so a
    future refactor cannot drop it while "fixing" the Windows case."""
    assert _is_closed_stdout(OSError(code, "closed")) is True


def test_an_unrelated_oserror_is_not_swallowed() -> None:
    """The handler must not become a blanket `except OSError`. A disk error during a write is a
    real failure and must still reach the operator."""
    assert _is_closed_stdout(OSError(errno.ENOSPC, "no space left on device")) is False
    assert _is_closed_stdout(ValueError("not an OSError at all")) is False


def test_main_swallows_a_closed_pipe_and_exits_141() -> None:
    """The whole handler, in a subprocess, because it really does rebind fd 1.

    Run in-process this destroys pytest's own captured stdout (`OSError: [Errno 9]` out of
    `capture.py`) -- the redirect working as designed. A subprocess is where that is harmless,
    and it is also the only place the devnull rebind is genuinely exercised rather than observed.

    An end-to-end `| head -1` does NOT reach here on POSIX: `brokers list` is 35 short lines and
    fits the 64 KiB pipe buffer, so the write never fails. That version of this test passed with
    the handler deleted -- found by mutation, which is the only reason this one exists.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import keel.cli as c\n"
            "def boom(*a, **k):\n"
            "    raise BrokenPipeError()\n"
            "c.cli = boom\n"
            "c.main()\n",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 141, (proc.returncode, proc.stderr)
    assert "Traceback" not in proc.stderr, proc.stderr
    assert "BrokenPipeError" not in proc.stderr, proc.stderr
    assert "Exception ignored" not in proc.stderr, (
        "the interpreter raised again while flushing at shutdown -- the devnull redirect is the "
        "half of the fix that prevents that trailing failure (#663)"
    )


def test_main_redirects_stdout_so_shutdown_cannot_raise_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the devnull redirect the interpreter flushes the dead stream at shutdown and
    raises a SECOND time, AFTER the handler ran -- `Exception ignored while flushing sys.stdout`
    in #663's traceback is exactly that failure.

    Asserted by observing the `dup2` rather than by rebinding fd 1: taking over fd 1 in-process
    breaks pytest's own capture (`OSError: [Errno 9] Bad file descriptor` from `capture.py`),
    which is a worse test, not a stricter one.
    """
    from keel import cli as cli_mod

    redirected: list[tuple[int, int]] = []

    def _record(fd: int, target: int, *a: object, **k: object) -> None:
        redirected.append((fd, target))
        # Do not actually rebind: the real call would close pytest's captured stdout.

    monkeypatch.setattr(cli_mod.os, "dup2", _record)
    monkeypatch.setattr(cli_mod, "cli", _raise(BrokenPipeError()))
    with pytest.raises(SystemExit):
        cli_mod.main()

    assert redirected, (
        "main() exited without redirecting stdout -- the interpreter will flush the dead stream "
        "at shutdown and raise a second time, which is the trailing failure in #663"
    )
    _src, target = redirected[0]
    assert target == sys.stdout.fileno(), f"redirected {target}, not stdout"


def test_a_real_error_still_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """The handler must not become a catch-all. A disk error during a write is a real failure."""
    from keel import cli as cli_mod

    monkeypatch.setattr(cli_mod, "cli", _raise(OSError(errno.ENOSPC, "no space left on device")))
    with pytest.raises(OSError) as caught:
        cli_mod.main()
    assert caught.value.errno == errno.ENOSPC
