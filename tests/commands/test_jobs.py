"""One background job at a time (#437).

The first market-data fetch runs for minutes across an allowlist. A request that blocks that long
is not a button -- the browser gives up, the user reloads, and a second fetch starts on top of the
first. These pin the properties that make the single slot safe rather than merely simple.
"""

from __future__ import annotations

import threading

import pytest

from keel.commands import jobs


@pytest.fixture(autouse=True)
def _clean_slot() -> None:
    """A module-level slot that persisted between tests would make one test's job visible to the
    next."""
    jobs.reset()
    yield
    jobs.wait(5)
    jobs.reset()


def test_a_job_runs_and_reports_its_progress() -> None:
    def work(echo):
        echo("one")
        echo("two")

    assert jobs.start("probe", work) is True
    status = jobs.wait(5)
    assert status is not None
    assert status.state == jobs.DONE
    assert status.lines == ("one", "two")
    assert status.finished_ts is not None


def test_a_second_start_is_refused_while_one_runs() -> None:
    """Not queued, not silently dropped: both look identical to a user watching a page that is
    not changing. Two concurrent fetches also write candles to one SQLite database and race."""
    gate = threading.Event()

    def slow(echo):
        echo("working")
        gate.wait(5)

    assert jobs.start("first", slow) is True
    assert jobs.is_running()
    assert jobs.start("second", lambda echo: None) is False
    assert jobs.status().key == "first"
    gate.set()
    assert jobs.wait(5).state == jobs.DONE


def test_a_failure_is_captured_and_stays_visible() -> None:
    """The whole point of running something in the background is that nobody was watching when it
    broke, so the failure has to still be there when they look. And it must not escape onto a
    thread that belongs to nobody."""

    def boom(echo):
        echo("about to fail")
        raise ValueError("the venue said no")

    jobs.start("failing", boom)
    status = jobs.wait(5)
    assert status.state == jobs.FAILED
    assert status.error == "ValueError: the venue said no"
    assert status.lines == ("about to fail",)
    # Still there on a second read -- not cleared by being observed.
    assert jobs.status().state == jobs.FAILED


def test_the_error_is_a_type_and_message_not_a_traceback() -> None:
    """A traceback in a browser page is a stack of file paths from someone else's machine."""

    def boom(_echo):
        raise RuntimeError("plain message")

    jobs.start("failing", boom)
    error = jobs.wait(5).error
    assert error == "RuntimeError: plain message"
    assert "Traceback" not in error
    assert "/" not in error


def test_a_finished_job_lets_the_next_one_start() -> None:
    jobs.start("first", lambda echo: echo("done"))
    jobs.wait(5)
    assert not jobs.is_running()
    assert jobs.start("second", lambda echo: echo("also done")) is True
    assert jobs.wait(5).key == "second"


def test_the_progress_tail_is_bounded() -> None:
    """A long run must not hold its whole output in a process that is also serving pages."""

    def chatty(echo):
        for index in range(jobs._MAX_LINES * 3):
            echo(f"line {index}")

    jobs.start("chatty", chatty)
    status = jobs.wait(10)
    assert len(status.lines) == jobs._MAX_LINES
    # The TAIL, not the head: what is happening now is what a watcher needs.
    assert status.lines[-1] == f"line {jobs._MAX_LINES * 3 - 1}"


def test_blank_progress_lines_are_dropped() -> None:
    """`run_fetch` emits blank separator lines the CLI renders as spacing; in a bounded tail they
    would push real output out of view."""

    def work(echo):
        echo("real")
        echo("")
        echo("   ")
        echo("also real")

    jobs.start("spaced", work)
    assert jobs.wait(5).lines == ("real", "also real")


def test_nothing_has_run_reads_as_none() -> None:
    assert jobs.status() is None
    assert jobs.is_running() is False
