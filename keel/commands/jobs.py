"""One background job at a time, so a setup step that takes minutes can still be a button (#437).

The first market-data fetch runs for minutes across an allowlist. A request that blocks that long
is not a button -- the browser gives up, the user reloads, and a second fetch starts on top of the
first. So this exists, and it is deliberately the smallest thing that could work.

**Exactly one slot.** Not a queue, not a pool. Two concurrent fetches write candles to one SQLite
database and race each other; a setup flow has no use for concurrency; and "is something running?"
with one answer is a question a page can render honestly. A second start is REFUSED and says so,
rather than being silently dropped or silently queued -- both of which look identical to a user
watching a page that is not changing.

**The progress is a bounded tail.** `run_fetch` already emits the same lines the CLI prints, in
the same order, through its `echo` parameter -- that contract is why this module needs no
knowledge of fetching at all. Keeping the last `_MAX_LINES` of them bounds the memory a long run
can consume in a process that is also serving pages.

**A failed job stays visible.** It is not cleared on read and not cleared by time: the whole point
of running something in the background is that nobody was watching when it broke, so the failure
has to still be there when they look. It is replaced only when the next job starts.

**Nothing here decides what to run.** A caller passes a callable; this module owns the thread, the
slot, the buffer and the status. That keeps it out of the argument about what a setup flow is
allowed to do -- which is `keel/commands/setup.py`'s business, and pinned there.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace

#: The tail kept from a job's progress stream. A first fetch across a wide allowlist emits a line
#: per product per granularity; forty is enough to show what is happening now without holding a
#: whole run's output in a process that is also serving pages.
_MAX_LINES = 40

RUNNING = "running"
DONE = "done"
FAILED = "failed"


@dataclass(frozen=True)
class JobStatus:
    key: str
    state: str
    started_ts: float
    finished_ts: float | None = None
    lines: tuple[str, ...] = ()
    #: Set only when `state == FAILED`. The exception's type and message, never a traceback: a
    #: traceback in a browser page is a stack of file paths from someone else's machine.
    error: str | None = None

    @property
    def is_running(self) -> bool:
        return self.state == RUNNING

    @property
    def elapsed_sec(self) -> float:
        return (self.finished_ts or time.time()) - self.started_ts


@dataclass
class _Slot:
    lock: threading.Lock = field(default_factory=threading.Lock)
    status: JobStatus | None = None
    thread: threading.Thread | None = None


_slot = _Slot()


def status() -> JobStatus | None:
    """The current or most recent job, or `None` when nothing has ever run."""
    with _slot.lock:
        return _slot.status


def is_running() -> bool:
    current = status()
    return current is not None and current.is_running


def start(key: str, run: Callable[[Callable[[str], None]], None]) -> bool:
    """Begin `run` in the background. `False` when a job is already running.

    `run` receives an `echo` callable and is expected to feed progress through it. Any exception
    it raises is captured into the status rather than propagating: the thread that would receive
    it belongs to nobody, and a background failure that only reaches stderr is a failure the
    person who started it never sees.
    """
    with _slot.lock:
        if _slot.status is not None and _slot.status.is_running:
            return False
        _slot.status = JobStatus(key=key, state=RUNNING, started_ts=time.time())

    def _append(line: str) -> None:
        text = str(line).rstrip()
        if not text:
            return
        with _slot.lock:
            current = _slot.status
            if current is None or current.key != key:
                return  # superseded by a later job; its lines are not ours to add to
            _slot.status = replace(current, lines=(current.lines + (text,))[-_MAX_LINES:])

    def _body() -> None:
        try:
            run(_append)
        except Exception as exc:
            with _slot.lock:
                current = _slot.status
                if current is not None and current.key == key:
                    _slot.status = replace(
                        current,
                        state=FAILED,
                        finished_ts=time.time(),
                        error=f"{type(exc).__name__}: {exc}",
                    )
            return
        with _slot.lock:
            current = _slot.status
            if current is not None and current.key == key:
                _slot.status = replace(current, state=DONE, finished_ts=time.time())

    thread = threading.Thread(target=_body, name=f"keel-job-{key}", daemon=True)
    with _slot.lock:
        _slot.thread = thread
    thread.start()
    return True


def wait(timeout: float | None = None) -> JobStatus | None:
    """Block until the running job finishes. For TESTS and for a caller that genuinely has
    nothing else to do -- never for a request handler, which is the entire reason this module
    exists."""
    with _slot.lock:
        thread = _slot.thread
    if thread is not None:
        thread.join(timeout)
    return status()


def reset() -> None:
    """Forget the slot. For tests: a module-level slot that persisted between them would make one
    test's job visible to the next."""
    with _slot.lock:
        _slot.status = None
        _slot.thread = None
