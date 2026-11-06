"""Tests for the macOS notification sink (#1010).

The sink is the one channel that reaches the operator on a zero-exit cycle: a vetoed live DCA buy
is a normal cycle, so neither the wrapper's non-zero-exit alerts nor an unconfigured webhook ever
fire. It must be inert off macOS and under pytest, and must never raise into the cycle.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest
from keel_core.macos_notify import DISABLE_ENV, post


def _boom(_argv: list[str]) -> Any:
    raise AssertionError("the runner must not be called")


def _raises(_argv: list[str]) -> Any:
    raise OSError("osascript missing")


@pytest.fixture(autouse=True)
def _darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv(DISABLE_ENV, raising=False)


def test_post_is_noop_off_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert post("t", "m", runner=_boom) is False


def test_post_is_noop_when_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DISABLE_ENV, "1")
    assert post("t", "m", runner=_boom) is False


def test_post_is_noop_under_pytest_without_an_injected_runner() -> None:
    # PYTEST_CURRENT_TEST is set by pytest itself while this test runs; with no runner the
    # default osascript runner must never be reached.
    assert post("t", "m") is False


def test_post_runs_osascript_with_the_script() -> None:
    calls: list[list[str]] = []
    assert post("keel-live: DCA buy skipped", "PAXG-USD (rule 9): x", runner=calls.append) is True
    [argv] = calls
    assert argv[:2] == ["/usr/bin/osascript", "-e"]
    assert argv[2] == (
        'display notification "PAXG-USD (rule 9): x" with title "keel-live: DCA buy skipped"'
    )


def test_post_escapes_quotes_and_backslashes_in_both_strings() -> None:
    calls: list[list[str]] = []
    post('a"b', "c\\d", runner=calls.append)
    assert calls[0][2] == 'display notification "c\\\\d" with title "a\\"b"'


def test_post_flattens_newlines_so_a_reason_cannot_break_the_script() -> None:
    calls: list[list[str]] = []
    post("t", "line one\nline two", runner=calls.append)
    assert "\n" not in calls[0][2]


def test_post_swallows_runner_errors() -> None:
    assert post("t", "m", runner=_raises) is False


def test_default_runner_uses_a_ten_second_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        seen.update(argv=argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert post("t", "m") is True
    assert seen["timeout"] == 10
    assert seen["check"] is True
