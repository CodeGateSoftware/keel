"""The data-pipeline service behind `keel fetch` -- freshness, ensure, and gap repair.

Issue #387 C1 (the TUI-operator-console PRD, O2): `fetch`'s whole flow -- the window-bounded
freshness sweep, the `--check` scheduler verdict, the closed-market defusal, the ensure/repair
pass -- lived inline in `keel/cli.py`'s command body, so any second front-end (the TUI's Data
menu, C5) would have had to re-implement it. It lives here now: the CLI wrapper parses options,
builds the broker at its `_build_broker` seam, and echoes; everything that DECIDES or DOES is in
this module, importable and unit-testable with no `click` anywhere.

Two layers, mirroring `keel/commands/status.py`:

- `assess_products` is the read-only sweep (no network): every (product, granularity) judged
  over the same fetch window. `run_fetch` is the flow: assess -> maybe skip or check -> warm ->
  maybe repair -> reassess, streaming its progress through an injected `echo`/`echo_err` pair
  (defaulting to no-ops) and returning a `FetchResult` so a front-end can render its own
  progress and still get the structured outcome.
- `render_freshness` is the pure renderer of the sweep's rows -- the exact lines the CLI prints,
  kept beside the data so the two front-ends cannot drift.

`keel.cli` re-exports `assess_products` as `_assess_products`; the existing CLI tests pin that
name directly, and re-importing keeps them resolving to this exact object.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from keel import agent
from keel.config import Config
from keel.data import freshness as freshness_mod
from keel.data import history as history_mod
from keel.data import repair as repair_mod
from keel.data.repository import Repository
from keel.types import Granularity

#: One year, in days, as the fetch/simulate windows reckon it. A shared constant because TWO
#: commands window their reads by it (`fetch --years`, `simulate --years`, and the discovery
#: probe's four-year lookback); independent `365` literals are exactly how one window silently
#: stops matching the bars the other fetches.
DAYS_PER_YEAR = 365


def assess_products(
    repo: Repository,
    products: list[str],
    granularities: list[Granularity],
    now_ts: int,
    start_ts: int,
    tolerance_bars: int,
    market_closed: bool = False,
) -> list[tuple[freshness_mod.Freshness, int]]:
    """Read-only sweep over every (product, granularity). No network.

    `granularities` is whatever the caller is keeping current -- `fetch` passes
    `config.market_data.granularities`, so the sweep judges exactly the series the warm step
    fetches and the agent polls.

    `market_closed` (FR-9) is the recorded venue-clock answer (`agent.recorded_market_closed`)
    threaded into every verdict, so a session-bound venue's weekend reads CLOSED rather than
    STALE. Defaulted False: a 24/7 venue never records a session and keeps yesterday's verdicts.

    Returns `(freshness, unexplained_gaps)`, BOTH bounded to the same window starting at
    `start_ts`. That shared window is the point: `render_freshness` subtracts the second from
    `freshness.gaps`, and `coverage()` counts those gaps over `get_candles(.., start_ts, None)`
    -- so an unexplained count taken over the WHOLE series goes negative the moment bars are
    missing older than `start_ts`. The field saw exactly that on 2026-08-17 (`keel fetch`
    printed `-2 proven absent at venue`: an impossible claim that made still-unexplained gaps
    look reconciled). `--fail-on-gaps` does NOT judge this count -- it keeps whole-series
    scope, see the `check` branch of `run_fetch`.
    """
    out: list[tuple[freshness_mod.Freshness, int]] = []
    for product in products:
        for granularity in granularities:
            info = history_mod.coverage(repo, product, granularity, start_ts)
            unexplained = repair_mod.unexplained_gap_count(
                repo, product, granularity, start_ts
            )
            out.append(
                (
                    freshness_mod.assess(
                        info, now_ts, tolerance_bars, market_closed=market_closed
                    ),
                    unexplained,
                )
            )
    return out


def render_freshness(rows: list[tuple[freshness_mod.Freshness, int]]) -> list[str]:
    """The sweep's rows as the exact lines `keel fetch` prints -- one line per series."""
    lines: list[str] = []
    for row, unexplained in rows:
        # A series can be BOTH stale and gapped. The state label reports the most urgent
        # condition, but the detail always carries BOTH numbers -- an earlier version showed
        # only the label and silently hid gaps behind staleness.
        #
        # A stale verdict under a closed market (FR-9) is its own state, ahead of plain
        # STALE: the bars ARE behind, but the venue's clock explains why, and the label must
        # say so rather than teach an operator to ignore STALE every weekend. MISSING stays
        # MISSING even when closed -- a closed venue still serves history, so a cold cache
        # remains the fetch pipeline's problem, not the calendar's.
        if row.missing:
            state = "MISSING"
        elif row.stale and row.market_closed:
            state = "CLOSED"
        elif row.stale:
            state = "STALE"
        elif row.gaps:
            state = "GAPS"
        else:
            state = "ok"
        if row.missing:
            detail = "nothing cached"
        elif row.stale and row.market_closed:
            proven = row.gaps - unexplained
            suffix = f" ({proven} proven absent at venue)" if proven else ""
            detail = (
                f"{row.bars_behind} bars behind, market closed -- not alerting, "
                f"{row.gaps} internal gaps{suffix}"
            )
        else:
            # No max(0, ...) clamp: `unexplained` comes from the same window-bounded read as
            # `row.gaps` (see `assess_products`), so the subtraction is consistent by
            # construction. A clamp would only re-hide the mismatch that once printed
            # "-2 proven absent at venue" for a series with real unexplained gaps.
            proven = row.gaps - unexplained
            suffix = f" ({proven} proven absent at venue)" if proven else ""
            detail = f"{row.bars_behind} bars behind, {row.gaps} internal gaps{suffix}"
        lines.append(
            f"  {state:<8} {row.product:<12} {row.granularity.value:<9} "
            f"n={row.n_candles:<7} {detail}"
        )
    return lines


@dataclass(frozen=True)
class FetchResult:
    """Everything `keel fetch` did, minus the printing.

    `error` is the message the CLI turns into a `click.ClickException` (and its exit code 1):
    it is set ONLY by the `--check` branch, for missing/stale series or (under
    `--fail-on-gaps`) series with unexplained gaps. A plain warm fetch never errors on
    short series -- see the closing note in `run_fetch`.
    """

    #: `(freshness, unexplained_gaps)` per series, from the BEFORE sweep.
    before: list[tuple[freshness_mod.Freshness, int]] = field(default_factory=list)
    #: The same sweep after the warm/repair pass; empty when nothing was fetched (`--check`,
    #: or the all-current skip).
    after: list[tuple[freshness_mod.Freshness, int]] = field(default_factory=list)
    #: The recorded venue-clock answer the verdicts were defused under (FR-9).
    market_closed: bool = False
    #: `--check`'s verdict, as the message the caller should fail with; `None` when there is
    #: nothing to fail on (and always for a plain warm fetch).
    error: str | None = None


def _check_verdict(
    repo: Repository,
    products: list[str],
    granularities: list[Granularity],
    before: list[tuple[freshness_mod.Freshness, int]],
    *,
    fail_on_gaps: bool,
) -> tuple[str | None, int]:
    """`--check`'s decision: `(message to fail with, whole-series unexplained count)`.

    The count is returned alongside because the (non-failing) summary below it needs the same
    number -- one read, one truth.
    """
    actionable = [r for r, _ in before if r.needs_fetch]
    if actionable:
        return f"{len(actionable)} series missing or stale", 0
    # `--fail-on-gaps` judges the WHOLE series, not the window the display above is bounded
    # to: a hole older than `start_ts` is invisible to those counts, yet `repair_series`
    # probes holes wherever they sit, so it is still fixable, still unproven, and still
    # this flag's business. That is also why `unexplained_gap_count`'s default stays
    # whole-series -- the two calls differ on purpose, and this is the one place that
    # wants the unbounded number.
    unexplained = sum(
        repair_mod.unexplained_gap_count(repo, product, granularity) > 0
        for product in products
        for granularity in granularities
    )
    if unexplained and fail_on_gaps:
        return (
            f"{unexplained} series have unexplained gaps -- run `keel fetch --repair-gaps`",
            unexplained,
        )
    return None, unexplained


def run_fetch(
    repo: Repository,
    config: Config,
    build_client: Callable[[], Any],
    *,
    db_path: str,
    products: list[str],
    years: int,
    now_ts: int,
    tolerance_bars: int,
    check: bool = False,
    fail_on_gaps: bool = False,
    refresh: bool = False,
    repair_gaps: bool = False,
    reprobe_absent: bool = False,
    echo: Callable[[str], None] = lambda _message: None,
    echo_err: Callable[[str], None] = lambda _message: None,
) -> FetchResult:
    """THE fetch flow: assess, then check / skip / warm / repair / reassess.

    READ-ONLY with respect to money: this fetches market data and writes candles. It places no
    orders, touches no rails and reads no credentials beyond the venue's public market-data
    endpoints -- which is why it is safe to schedule (see `docs/operations/scheduled-fetch.md`).

    `build_client` is a lazy factory rather than a client argument because construction order is
    behavior: `--check` and the all-current skip must never construct the broker at all (a
    scheduler's `--check` has no business loading credentials), and the tests pin exactly that.
    The CLI passes `lambda: _build_broker(config)`; a front-end passes whatever it gates behind
    an explicit ask.

    `echo`/`echo_err` receive the same lines the CLI has always printed, in the same order --
    they are the progress stream, defaulted to no-ops so a caller that only wants the
    `FetchResult` stays silent. `--check`'s failure is RETURNED as `FetchResult.error`, not
    raised: how a front-end fails (exit code, toast, red row) is its business; WHAT failed is
    this module's.
    """
    start_ts = now_ts - years * DAYS_PER_YEAR * 86400

    # fetch is the data pipeline: it warms exactly what this deployment polls, i.e.
    # `config.market_data.granularities` -- the same list `agent` and `monitor` use -- so the
    # runbook's `keel fetch` warm step honestly covers the FIFTEEN_MINUTE confirmation series
    # every shipped config lists. `simulate` deliberately keeps its own ONE_HOUR/ONE_DAY pair
    # instead: that pair is the backtest ENGINE's supported timeframes, an engine limit rather
    # than a data choice (Issue #349).
    granularities = list(config.market_data.granularities)

    # FR-9: the venue's session answer, as recorded by the last cycle of whatever loop is
    # running (the agent, or `keel monitor --loop`). A closed session-bound venue defuses
    # STALE (nothing can fetch bars a shut venue is not minting, and a weekend must not page
    # an operator); see `agent.recorded_market_closed` for the trust window -- derived from
    # the interval the recording deployment actually cycles at, config only as the fallback
    # -- and the deliberate clock_unavailable/missing carve-outs. A 24/7 venue never records
    # a session, so this is False and every verdict stays byte-identical.
    market_closed = agent.recorded_market_closed(repo, config, now_ts)

    before = assess_products(
        repo, products, granularities, now_ts, start_ts, tolerance_bars, market_closed
    )
    echo(f"data cached in: {db_path}")
    for line in render_freshness(before):
        echo(line)

    if check:
        error, unexplained = _check_verdict(
            repo, products, granularities, before, fail_on_gaps=fail_on_gaps
        )
        if error is not None:
            return FetchResult(before=before, market_closed=market_closed, error=error)
        # Truthful, not reassuring: series that ARE behind must not be called "current".
        # When the closed record explains the staleness, the summary says so -- the quiet
        # has to be legible, which is the whole point of a closing line (and the older
        # "all series actionable" wording said the opposite of what it meant).
        closed_explained = market_closed and any(r.stale for r, _ in before)
        summary = (
            "all series current or closed-explained"
            if closed_explained
            else "all series current"
        )
        if unexplained:
            echo(
                f"\n{summary}. {unexplained} have UNEXPLAINED gaps -- run "
                "`keel fetch --repair-gaps` to probe them."
            )
        elif closed_explained:
            echo(f"\n{summary} (market closed -- staleness does not alert)")
        else:
            echo(f"\n{summary}")
        return FetchResult(before=before, market_closed=market_closed)

    if not refresh and not repair_gaps and not freshness_mod.any_needs_fetch(
        [r for r, _ in before]
    ):
        # The no-network skip is deliberate (nothing a fetch does can produce bars a closed
        # venue is not minting) -- but a behind series must not be called "current" to
        # justify it. Name the closure instead.
        if market_closed and any(r.stale for r, _ in before):
            echo(
                "\nmarket closed -- staleness does not alert; behind series are "
                "expected, nothing to fetch"
            )
        else:
            echo("\nall series current -- nothing to fetch")
        return FetchResult(before=before, market_closed=market_closed)

    echo("\nfetching...")
    client = build_client()
    history_mod.ensure_history(
        client,
        repo,
        products,
        granularities,
        years,
        now_ts,
        sleep_fn=time.sleep,
        refresh=refresh,
    )

    if repair_gaps:
        echo("\nrepairing interior gaps...")
        for product in products:
            for granularity in granularities:
                outcome = repair_mod.repair_series(
                    client,
                    repo,
                    product,
                    granularity,
                    now_ts=now_ts,
                    reprobe_known_absent=reprobe_absent,
                    sleep_fn=time.sleep,
                )
                if not outcome.windows_found:
                    continue
                echo(
                    f"  {product:<12} {granularity.value:<9} "
                    f"windows={outcome.windows_found} probed={outcome.windows_probed} "
                    f"skipped={outcome.windows_skipped_known_absent} "
                    f"recovered={outcome.bars_recovered} "
                    f"absent_at_source={outcome.windows_absent_at_source}"
                )
                for error in outcome.errors:
                    echo_err(f"    error: {error}")

    after = assess_products(
        repo, products, granularities, now_ts, start_ts, tolerance_bars, market_closed
    )
    echo("\nafter fetch:")
    for line in render_freshness(after):
        echo(line)
    if freshness_mod.any_needs_fetch([r for r, _ in after]):
        # Not an error: an asset younger than the window, or a venue simply not serving the
        # most recent bar yet, both land here legitimately. Say so rather than failing a
        # scheduled run that did everything it could.
        echo(
            "\nsome series are still short. Common and usually benign: an asset younger than "
            "the requested window (PAXG-USD), or a bar the venue has not published yet."
        )
    return FetchResult(before=before, after=after, market_closed=market_closed)
