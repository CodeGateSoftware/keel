"""`keel fetch` -- the scheduled data-refresh command.

Read-only with respect to money. `--check` must never open a network connection, which these
tests enforce by monkeypatching `_build_broker` to something that raises if called.
"""

from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path

from click.testing import CliRunner

import keel.cli as cli_module
from keel.cli import cli
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.types import Candle, Granularity
from tests.conftest import VALID_CONFIG_YAML

_DAY = 86400
_HOUR = 3600
_FIFTEEN = 900
_ASSETS = ("BTC", "ETH", "PAXG")


def _repo_at(db_path: Path) -> Repository:
    conn = connect(str(db_path))
    migrate(conn)
    return Repository(conn)


def _candle(ts: int) -> Candle:
    return Candle(
        ts=ts,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1"),
    )


def _seed(repo: Repository, product: str, granularity: Granularity, timestamps) -> None:
    repo.upsert_candles(product, granularity, [_candle(ts) for ts in timestamps])


def _seed_current(
    repo: Repository,
    assets: tuple[str, ...] = _ASSETS,
    *,
    day_lag: int = 0,
    skip_day: int | None = None,
) -> None:
    """Seed every configured granularity, 30 bars each, up to the newest COMPLETE bar.

    `day_lag` ages only the ONE_DAY series by that many bars (the tolerance fixture);
    `skip_day` drops that day's bar to punch one internal hole (the gap fixtures).
    """
    now = int(time.time())
    last_day = (now // _DAY) * _DAY - (1 + day_lag) * _DAY
    last_hour = (now // _HOUR) * _HOUR - _HOUR
    last_q = (now // _FIFTEEN) * _FIFTEEN - _FIFTEEN
    days = [last_day - i * _DAY for i in range(30) if i != skip_day]
    for asset in assets:
        product = f"{asset}-USD"
        _seed(repo, product, Granularity.ONE_DAY, days)
        _seed(repo, product, Granularity.ONE_HOUR, [last_hour - i * _HOUR for i in range(30)])
        _seed(repo, product, Granularity.FIFTEEN_MINUTE, [last_q - i * _FIFTEEN for i in range(30)])


def _seed_stale_days(repo: Repository, assets: tuple[str, ...] = _ASSETS) -> None:
    """ONE_DAY only and 40 days behind, so exactly one series is present and it is stale."""
    now = int(time.time())
    stale_day = (now // _DAY) * _DAY - 40 * _DAY
    for asset in assets:
        _seed(repo, f"{asset}-USD", Granularity.ONE_DAY, [stale_day - i * _DAY for i in range(30)])


class _ExplodingBroker:
    """Any use of the network in `--check` mode is a bug, so make it loud."""

    def __getattr__(self, name):
        raise AssertionError(f"--check must not touch the network (called {name!r})")


def _no_network(monkeypatch):
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: _ExplodingBroker())


def test_check_reports_current_series_and_exits_zero(
    tmp_path, valid_config_path, monkeypatch
):
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)

    # Seed right up to the newest COMPLETE bar for every configured granularity.
    _seed_current(repo)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--check"]
    )
    assert result.exit_code == 0, result.output
    assert "all series current" in result.output
    assert "STALE" not in result.output


def test_check_exits_nonzero_when_stale_so_a_scheduler_can_alert(
    tmp_path, valid_config_path, monkeypatch
):
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)

    _seed_stale_days(repo)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--check"]
    )
    assert result.exit_code != 0
    assert "STALE" in result.output
    assert "missing or stale" in result.output


def test_check_reports_missing_series(tmp_path, valid_config_path, monkeypatch):
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    _repo_at(db_path)  # migrated but empty

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--check"]
    )
    assert result.exit_code != 0
    assert "MISSING" in result.output


def test_check_reports_gaps_without_failing_unless_asked(
    tmp_path, valid_config_path, monkeypatch
):
    """Gaps are reported, but do not fail --check by default.

    `ensure_history` cannot repair internal holes, so failing on them would leave the alert
    permanently red. `--fail-on-gaps` is there for a caller who wants strictness.
    """
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)

    # Skip day 5 -> one internal gap.
    _seed_current(repo, skip_day=5)

    args = ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--check"]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert "GAPS" in result.output
    assert "internal gaps" in result.output

    strict = CliRunner().invoke(cli, [*args, "--fail-on-gaps"])
    assert strict.exit_code != 0
    assert "internal gaps" in strict.output


def test_tolerance_bars_is_honoured(tmp_path, valid_config_path, monkeypatch):
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)

    # ONE_DAY seeded 5 bars behind the newest complete day bar.
    _seed_current(repo, day_lag=5)

    args = ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--check"]
    assert CliRunner().invoke(cli, args).exit_code != 0
    assert CliRunner().invoke(cli, [*args, "--tolerance-bars", "20"]).exit_code == 0


def test_fetch_skips_the_network_when_everything_is_current(
    tmp_path, valid_config_path, monkeypatch
):
    """Without --check the command may fetch -- but not when there is nothing to do."""
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)

    _seed_current(repo)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "fetch"]
    )
    assert result.exit_code == 0, result.output
    assert "nothing to fetch" in result.output


def test_fetch_calls_ensure_history_when_stale(tmp_path, valid_config_path, monkeypatch):
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)

    _seed_stale_days(repo)

    calls: list[tuple] = []

    def _fake_ensure(client, repo_arg, products, grans, years, now_ts, **kwargs):
        calls.append((tuple(products), tuple(grans), years))
        return {}

    monkeypatch.setattr(cli_module, "_build_broker", lambda config: object())
    monkeypatch.setattr(cli_module.history_mod, "ensure_history", _fake_ensure)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "fetch"]
    )
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    products, grans, years = calls[0]
    assert set(products) == {"BTC-USD", "ETH-USD", "PAXG-USD"}
    assert Granularity.ONE_DAY in grans and Granularity.ONE_HOUR in grans
    assert years == 5


# -- market_closed: a closed venue is not a stale feed (FR-9) ---------------------------------
#
# The session answer is RECORDED by the agent cycle (the one component with a broker) into
# `agent_state`; `--check` stays offline by reading that record. A stale series while the
# record says closed is the expected weekend state: displayed as CLOSED, not counted by the
# exit code. Missing series still alert (a closed venue serves history), and a record older
# than the feed-heartbeat window (`interval_sec * FEED_STALENESS_CYCLES`, the same trust
# window rail 12 gives the feed) is ignored, so a dead agent cannot silence the alerts.


def _record_session(repo: Repository, state: str, *, age_sec: int = 0) -> None:
    repo.set_state("market_session", state)
    repo.set_state("market_session_ts", int(time.time()) - age_sec)


def _seed_all_stale(repo: Repository, assets: tuple[str, ...] = _ASSETS) -> None:
    """Every configured granularity PRESENT but 40 bars behind -- stale with nothing
    missing, the shape a closed equities venue actually leaves behind (a weekend where the
    bars simply stopped, not a cache that was never warmed)."""
    now = int(time.time())
    series = (
        (_DAY, Granularity.ONE_DAY),
        (_HOUR, Granularity.ONE_HOUR),
        (_FIFTEEN, Granularity.FIFTEEN_MINUTE),
    )
    for asset in assets:
        product = f"{asset}-USD"
        for step, granularity in series:
            last = (now // step) * step - 40 * step
            _seed(repo, product, granularity, [last - i * step for i in range(30)])


def test_check_exits_zero_when_stale_only_because_the_market_is_closed(
    tmp_path, valid_config_path, monkeypatch
):
    """The FR-9 promise, at the operator surface: a weekend must not page anyone. Series
    that are present but behind exit zero once the recorded venue clock says closed -- and
    the display says WHY, so the quiet is legible rather than suspicious."""
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_all_stale(repo)
    _record_session(repo, "closed")

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--check"]
    )
    assert result.exit_code == 0, result.output
    assert "CLOSED" in result.output
    assert "STALE" not in result.output
    assert "missing or stale" not in result.output


def test_check_still_alerts_when_the_closed_record_has_gone_stale(
    tmp_path, valid_config_path, monkeypatch
):
    """The record is trusted only for the feed-heartbeat window (`interval_sec` 900 x
    `FEED_STALENESS_CYCLES` 3 = 45 minutes here): an agent that died Friday night must not
    silence Saturday's staleness alerts with its last reading."""
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_all_stale(repo)
    _record_session(repo, "closed", age_sec=10 * 3600)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--check"]
    )
    assert result.exit_code != 0
    assert "STALE" in result.output


def test_check_still_alerts_on_missing_even_when_the_market_is_closed(
    tmp_path, valid_config_path, monkeypatch
):
    """A closed venue still serves HISTORICAL bars, so a cache with nothing in it is a cold
    pipeline, not a session artifact -- it must keep alerting."""
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)  # migrated but empty
    _record_session(repo, "closed")

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--check"]
    )
    assert result.exit_code != 0
    assert "MISSING" in result.output


def test_check_does_not_defuse_staleness_on_an_unreadable_clock(
    tmp_path, valid_config_path, monkeypatch
):
    """Fail-closed for trading, fail-LOUD for alerting: a recorded clock_unavailable must
    not read as closed -- the venue state is unknown, and suppressing the staleness alert on
    an unknown clock would hide exactly the outage the clock failure itself hints at."""
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_all_stale(repo)
    _record_session(repo, "clock_unavailable")

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--check"]
    )
    assert result.exit_code != 0
    assert "STALE" in result.output


# -- the trust window's exact boundary (review finding on #385) --------------------------------
#
# The window is `interval_sec * FEED_STALENESS_CYCLES` (900 x 3 = 45 minutes for the fixture
# config). Its edges are load-bearing for the whole "a dead agent cannot silence the alerts"
# promise: one second inside the window the weekend is still defused, one second outside it
# the alert fires. Pinned with a frozen clock so the boundary cannot drift with real time.

_WINDOW_SEC = 900 * 3  # the fixture config's interval x FEED_STALENESS_CYCLES


def _record_session_at(repo: Repository, state: str, ts: int) -> None:
    """`_record_session` for the frozen-clock tests: stamp the record at an explicit ts."""
    repo.set_state("market_session", state)
    repo.set_state("market_session_ts", ts)


def test_a_record_exactly_at_the_window_edge_still_defuses(
    tmp_path, valid_config_path, monkeypatch
):
    """age == window is INSIDE the trust window (`<=`, matching rail 12's own staleness
    comparison): a record refreshed exactly one window ago is the newest reading a
    slow-cycling-but-alive deployment can have."""
    _no_network(monkeypatch)
    monkeypatch.setattr(cli_module, "time", _FrozenClock(_NOW))
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_all_stale(repo)
    _record_session_at(repo, "closed", _NOW - _WINDOW_SEC)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--check"]
    )
    assert result.exit_code == 0, result.output
    assert "CLOSED" in result.output


def test_a_record_one_second_past_the_window_no_longer_defuses(
    tmp_path, valid_config_path, monkeypatch
):
    _no_network(monkeypatch)
    monkeypatch.setattr(cli_module, "time", _FrozenClock(_NOW))
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_all_stale(repo)
    _record_session_at(repo, "closed", _NOW - _WINDOW_SEC - 1)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--check"]
    )
    assert result.exit_code != 0
    assert "STALE" in result.output


def test_a_fresh_record_with_a_nonzero_age_still_defuses(tmp_path, valid_config_path, monkeypatch):
    """Age zero is not a special case: a record written a minute ago by the last cycle is
    exactly the healthy weekend shape."""
    _no_network(monkeypatch)
    monkeypatch.setattr(cli_module, "time", _FrozenClock(_NOW))
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_all_stale(repo)
    _record_session_at(repo, "closed", _NOW - 60)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--check"]
    )
    assert result.exit_code == 0, result.output
    assert "CLOSED" in result.output


def test_an_unparseable_recorded_ts_does_not_defuse(tmp_path, valid_config_path, monkeypatch):
    """The junk branch of `recorded_market_closed`: a closed state whose stamp is not an
    int is a value nobody vouches for -- alerts resume rather than trusting it."""
    _no_network(monkeypatch)
    monkeypatch.setattr(cli_module, "time", _FrozenClock(_NOW))
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_all_stale(repo)
    repo.set_state("market_session", "closed")
    repo.set_state("market_session_ts", "not-a-timestamp")

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--check"]
    )
    assert result.exit_code != 0
    assert "STALE" in result.output


# -- the summary lines must be truthful while closed and behind (review finding on #385) ---------


def test_check_summary_names_the_closed_market_when_staleness_is_defused(
    tmp_path, valid_config_path, monkeypatch
):
    """Every series is behind and the closed record explains it: the closing line must SAY
    so, not claim a plain 'all series current' (or the older 'all series actionable') --
    the quiet has to be legible, which is the whole point of the summary."""
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_all_stale(repo)
    _record_session(repo, "closed")

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--check"]
    )
    assert result.exit_code == 0, result.output
    assert "market closed -- staleness does not alert" in result.output
    assert "all series actionable" not in result.output


def test_plain_fetch_does_not_claim_all_series_current_while_closed_and_behind(
    tmp_path, valid_config_path, monkeypatch
):
    """A plain `keel fetch` on a closed weekend skips the network (nothing can fetch bars a
    shut venue is not minting) -- but it must not print 'all series current -- nothing to
    fetch' over series that are 40 bars behind. The skip is kept; the wording tells the
    truth about why."""
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_all_stale(repo)
    _record_session(repo, "closed")

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "fetch"]
    )
    assert result.exit_code == 0, result.output
    assert "all series current -- nothing to fetch" not in result.output
    assert "market closed" in result.output
    assert "nothing to fetch" in result.output  # the skip itself is unchanged


# -- gap repair via the CLI ---------------------------------------------------


def test_check_reports_unexplained_gaps_and_points_at_the_repair_command(
    tmp_path, valid_config_path, monkeypatch
):
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_current(repo, skip_day=5)

    args = ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--check"]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert "UNEXPLAINED" in result.output
    assert "--repair-gaps" in result.output

    strict = CliRunner().invoke(cli, [*args, "--fail-on-gaps"])
    assert strict.exit_code != 0


def test_a_gap_proven_absent_no_longer_fails_the_strict_check(
    tmp_path, valid_config_path, monkeypatch
):
    """The whole point of the probe record: --fail-on-gaps becomes SATISFIABLE.

    Before this, a hole the venue does not have would keep the strict check red forever.
    """
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_current(repo, skip_day=5)

    args = ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--check"]
    assert CliRunner().invoke(cli, [*args, "--fail-on-gaps"]).exit_code != 0

    # Record every detected window as probed-and-empty, as a real repair pass would.
    from keel.data import gaps as gaps_mod

    now = int(time.time())
    for asset in ("BTC", "ETH", "PAXG"):
        product = f"{asset}-USD"
        for gran in (Granularity.ONE_DAY, Granularity.ONE_HOUR):
            for window in gaps_mod.detect(repo.get_candles(product, gran), product, gran):
                repo.record_gap_probe(
                    product, gran, window.start_ts, window.end_ts, window.n_missing, now
                )

    passed = CliRunner().invoke(cli, [*args, "--fail-on-gaps"])
    assert passed.exit_code == 0, passed.output
    assert "proven absent at venue" in passed.output


def test_repair_gaps_runs_the_repair_pass(tmp_path, valid_config_path, monkeypatch):
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_current(repo, skip_day=5)

    calls: list[tuple] = []

    def _fake_repair(client, repo_arg, product, granularity, **kwargs):
        calls.append((product, granularity))
        from keel.data.repair import RepairResult

        return RepairResult(product=product, granularity=granularity)

    monkeypatch.setattr(cli_module, "_build_broker", lambda config: object())
    monkeypatch.setattr(cli_module.history_mod, "ensure_history", lambda *a, **k: {})
    monkeypatch.setattr(cli_module.repair_mod, "repair_series", _fake_repair)

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--repair-gaps"],
    )
    assert result.exit_code == 0, result.output
    assert len(calls) == 9  # 3 products x 3 configured granularities
    assert "repairing interior gaps" in result.output


# -- the gap-proven suffix must share the fetch window (field repro, 2026-08-17) ---------------
#
# `keel fetch` printed, for real series:
#
#     GAPS  SOL-USD  ONE_DAY  n=1819  0 bars behind, 5 internal gaps (-1 proven absent at venue)
#
# A NEGATIVE "proven absent" count is an impossible claim -- you cannot prove fewer than zero
# gaps absent -- and it masked the real state: the suffix subtracted a WHOLE-SERIES unexplained
# count from a WINDOW-BOUNDED gap count, so bars missing older than the fetch window drove the
# number negative and the still-unexplained in-window gaps looked reconciled. The two counts
# must describe the same slice.


class _FrozenClock:
    """Fixed `time` stand-in for `keel.cli`, so the rendered line is deterministic.

    `fetch` reads `now_ts` itself, so a real clock can cross a bar boundary between the test
    seeding its fixture and the command computing `bars_behind` -- midnight-aligned so ONE_DAY
    bars land exactly on the window edge.
    """

    def __init__(self, now_ts: int):
        self._now_ts = now_ts

    def time(self) -> float:
        return float(self._now_ts)

    def sleep(self, seconds: float) -> None:
        return None


_NOW = 1_799_971_200  # midnight UTC, day-aligned
_START = _NOW - 365 * _DAY  # what `fetch --years 1` computes as the window start


def _seed_sol_field_repro(repo: Repository) -> None:
    """SOL-USD ONE_DAY with the field's exact shape: (a) an in-window hole recorded absent in
    `candle_gap_probes`, (b) an in-window hole NOT recorded, and (c) a 2-bar hole entirely
    OLDER than the fetch window."""
    day = Granularity.ONE_DAY
    last_day = _NOW - _DAY  # newest complete bar: 0 bars behind

    # (c): 8 bars ending the day before the window starts, with a 2-bar hole inside them.
    old = [_START - i * _DAY for i in range(1, 11) if i not in (5, 6)]
    # (a)+(b): every window bar, minus one hole at +10d and one at +20d.
    n_window_days = (last_day - _START) // _DAY + 1
    window = [
        _START + i * _DAY
        for i in range(n_window_days)
        if i not in (10, 20)
    ]
    _seed(repo, "SOL-USD", day, old + window)

    # The venue was asked about the +10d hole and had nothing -- as `repair_series` records.
    repo.record_gap_probe("SOL-USD", day, _START + 10 * _DAY, _START + 10 * _DAY, 1, _NOW)

    # ONE_HOUR and FIFTEEN_MINUTE current, so `--check` is not distracted by a missing series.
    last_hour = _NOW - _HOUR
    _seed(repo, "SOL-USD", Granularity.ONE_HOUR, [last_hour - i * _HOUR for i in range(48)])
    last_q = _NOW - _FIFTEEN
    _seed(
        repo, "SOL-USD", Granularity.FIFTEEN_MINUTE, [last_q - i * _FIFTEEN for i in range(48)]
    )


def test_the_gap_suffix_shares_the_fetch_window_so_it_cannot_go_negative(
    tmp_path, valid_config_path, monkeypatch
):
    """The exact rendered line for the field fixture.

    Window-bounded truth: 2 in-window gaps, exactly 1 of them proven absent. The 2 bars
    missing BEFORE the window must perturb neither number -- under the old whole-series
    subtraction they turned the suffix into `(-1 proven absent at venue)`, and the unproven
    +20d hole rode along looking reconciled.
    """
    _no_network(monkeypatch)
    monkeypatch.setattr(cli_module, "time", _FrozenClock(_NOW))
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_sol_field_repro(repo)

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path),
         "fetch", "--check", "--years", "1", "--products", "SOL-USD"],
    )

    assert (
        "  GAPS     SOL-USD      ONE_DAY   n=363     0 bars behind, "
        "2 internal gaps (1 proven absent at venue)" in result.output
    ), result.output
    # The whole-series truth the display no longer pretends to state: bars are still missing
    # outside the window, and the closing message still says so.
    assert "1 have UNEXPLAINED gaps" in result.output


def test_every_assessed_row_keeps_proven_absent_never_negative(tmp_path, monkeypatch):
    """`row.gaps >= unexplained` in every `_assess_products` row, by construction.

    The two counts now read the same window, so `proven = gaps - unexplained` cannot go
    negative -- the field printed `(-2 proven absent at venue)` from exactly this pair
    disagreeing about scope. Pinned directly so the invariant survives refactors of the
    renderer that no longer visibly subtract.
    """
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)
    _seed_sol_field_repro(repo)

    rows = cli_module._assess_products(
        repo,
        ["SOL-USD"],
        [Granularity.ONE_DAY, Granularity.ONE_HOUR, Granularity.FIFTEEN_MINUTE],
        _NOW,
        _START,
        tolerance_bars=2,
    )
    assert rows, "fixture must assess something for this test to mean anything"
    for row, unexplained in rows:
        assert row.gaps >= unexplained, (row.product, row.granularity, row.gaps, unexplained)


def test_fail_on_gaps_still_judges_holes_older_than_the_fetch_window(
    tmp_path, valid_config_path, monkeypatch
):
    """`--fail-on-gaps` scope is deliberately UNCHANGED: the whole series.

    The window display cannot see a hole before `start_ts` (neither of its counts covers it),
    but `repair_series` probes holes wherever they sit, so such a hole is still fixable, still
    unproven, and must still fail the strict check. Narrowing the flag to the window would
    green-light a series the repair command itself still lists work for. Born green, on
    purpose: it pins preserved semantics, not the regression the sibling tests reproduce.
    """
    _no_network(monkeypatch)
    monkeypatch.setattr(cli_module, "time", _FrozenClock(_NOW))
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)

    # Current and CONTIGUOUS inside the window (displays "ok"); 4 bars missing before it.
    last_day = _NOW - _DAY
    n_window_days = (last_day - _START) // _DAY + 1
    _seed(
        repo,
        "SOL-USD",
        Granularity.ONE_DAY,
        [_START - 6 * _DAY, _START - _DAY]
        + [_START + i * _DAY for i in range(n_window_days)],
    )
    _seed(repo, "SOL-USD", Granularity.ONE_HOUR, [_NOW - _HOUR - i * _HOUR for i in range(48)])
    _seed(
        repo,
        "SOL-USD",
        Granularity.FIFTEEN_MINUTE,
        [_NOW - _FIFTEEN - i * _FIFTEEN for i in range(48)],
    )

    args = ["--db", str(db_path), "--config", str(valid_config_path),
            "fetch", "--check", "--years", "1", "--products", "SOL-USD"]

    plain = CliRunner().invoke(cli, args)
    assert plain.exit_code == 0, plain.output
    # The display is window-bounded and honest about it: the day series shows "ok"...
    assert "  ok       SOL-USD      ONE_DAY   n=365     0 bars behind, 0 internal gaps" in (
        plain.output
    )
    # ...while the whole-series judgment still counts the outside-window hole.
    assert "1 have UNEXPLAINED gaps" in plain.output

    strict = CliRunner().invoke(cli, [*args, "--fail-on-gaps"])
    assert strict.exit_code != 0
    assert "unexplained gaps" in strict.output


# -- --products validation: a SHAPE error is fatal, a SETTLEMENT mismatch is not ---------------
#
# Feasibility study R2, corrected. Validating `--products` where the operator types it is right
# for `rules seed`, which WRITES a row the agent then polls. `fetch` places no orders and needs
# no rail -- and making a settlement mismatch fatal here broke the screening workflow outright:
# `assets screen --products BTC-EUR` is exempt from validation so an operator CAN ask about a
# cross-settled pair, but the screen's answer is dominated by "0 daily bars < 1460 required",
# and there was no way to fetch that history without first widening `settlement_currencies`.
# You had to make the config change before you could evaluate whether to make it.


def test_fetch_refuses_a_malformed_product_id_because_that_is_always_a_typo(
    tmp_path, valid_config_path, monkeypatch
):
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    _repo_at(db_path)

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path),
         "fetch", "--check", "--products", "XLM-28AUG26-CDE"],
    )

    assert result.exit_code != 0
    assert "XLM-28AUG26-CDE" in result.output
    assert "not a spot product id" in result.output


def test_fetch_WARNS_on_a_cross_settled_pair_and_proceeds(
    tmp_path, valid_config_path, monkeypatch
):
    """The history has to be fetchable before the screen can say anything about the asset.

    `BTC-EUR` is a real Coinbase spot pair. Rail 18 vetoes an ORDER for it under the shipped
    settlement set -- which is why the warning is loud -- but `fetch` writes candles, and
    refusing to cache them makes `settlement_currencies` a decision the operator has to take
    before they can gather the evidence for it.
    """
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    _repo_at(db_path)

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path),
         "fetch", "--check", "--products", "BTC-EUR"],
    )

    assert "BTC-EUR" in result.output
    assert "settles in EUR" in result.output
    assert "MISSING" in result.output, "it must actually go on to assess the product"
    # `--check` exits non-zero on a missing series, which is the assessment, not the refusal.
    assert "Invalid value for --products" not in result.output


def test_fetch_reports_a_shape_error_even_when_a_settlement_warning_rides_along(
    tmp_path, valid_config_path, monkeypatch
):
    """One fatal id in the list still stops the run, and the warning-worthy one is not what
    decides that -- otherwise the two kinds would have to be typed in separate invocations."""
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    _repo_at(db_path)

    result = CliRunner().invoke(
        cli,
        ["--db", str(db_path), "--config", str(valid_config_path),
         "fetch", "--check", "--products", "BTC-EUR,XLM-28AUG26-CDE"],
    )

    assert result.exit_code != 0
    assert "XLM-28AUG26-CDE" in result.output


# -- fetch honors config.market_data.granularities (Issue #349) -------------------------------
#
# `fetch` is the data pipeline: what it warms must be exactly what the deployment's agent and
# monitor poll (`config.market_data.granularities`), or the runbook's documented warm step
# silently leaves the FIFTEEN_MINUTE confirmation series at ZERO candles in a fresh deployment
# -- every shipped config lists ONE_DAY/ONE_HOUR/FIFTEEN_MINUTE -- and the agent's first
# cycles then inherit a full multi-hundred-request catch-up. `simulate` is the deliberate
# exception (the backtest engine supports only ONE_HOUR/ONE_DAY); that asymmetry is pinned in
# tests/test_cli.py.


class _RecordingClient:
    """Duck-typed venue client that records every candle request and serves none.

    Serving none keeps `ensure_history` to a single inception probe per series -- the tests
    below care about WHICH granularities get asked for, not about paging.
    """

    def __init__(self) -> None:
        self.requests: list[Granularity] = []

    def get_candles(self, product_id, granularity, start, end):
        self.requests.append(granularity)
        return []


def test_fetch_warms_every_configured_granularity(tmp_path, valid_config_path, monkeypatch):
    """The warm fetch must ask the venue for FIFTEEN_MINUTE too.

    The fixture config lists three granularities exactly like every shipped config, so a
    fresh deployment following the runbook (`keel fetch`) gets all three series warm --
    including the confirmation series the agent polls every cycle.
    """
    client = _RecordingClient()
    monkeypatch.setattr(cli_module, "time", _FrozenClock(_NOW))
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: client)
    db_path = tmp_path / "t.db"
    _repo_at(db_path)  # empty DB: every series is missing, so the fetch proceeds

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "fetch"]
    )
    assert result.exit_code == 0, result.output
    assert set(client.requests) == {
        Granularity.ONE_DAY,
        Granularity.ONE_HOUR,
        Granularity.FIFTEEN_MINUTE,
    }


def test_check_reports_freshness_for_every_configured_granularity(
    tmp_path, valid_config_path, monkeypatch
):
    """`--check` is what a scheduler alerts on, so its rows must cover the same three series
    the warm step fetches -- a granularity `--check` never mentions is one nobody notices
    staying empty."""
    _no_network(monkeypatch)
    db_path = tmp_path / "t.db"
    repo = _repo_at(db_path)

    _seed_current(repo)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(valid_config_path), "fetch", "--check"]
    )
    assert result.exit_code == 0, result.output
    assert "FIFTEEN_MINUTE" in result.output
    assert "all series current" in result.output


def _config_with_granularities(write_config, *grans: str) -> Path:
    """The fixture config rewritten to list exactly `grans` under market_data."""

    def _block(names: str) -> str:
        return "".join(f"    - {name}\n" for name in names.split())

    text = VALID_CONFIG_YAML.replace(
        "    - ONE_DAY\n    - ONE_HOUR\n    - FIFTEEN_MINUTE\n", _block(" ".join(grans))
    )
    assert text != VALID_CONFIG_YAML, "the granularities block must have matched"
    return write_config(text)


def test_fetch_touches_only_the_configured_granularities(tmp_path, write_config, monkeypatch):
    """The config is the single source of truth in the other direction too: a deployment
    listing only ONE_DAY must not pay for series it never polls."""
    client = _RecordingClient()
    config_path = _config_with_granularities(write_config, "ONE_DAY")
    monkeypatch.setattr(cli_module, "time", _FrozenClock(_NOW))
    monkeypatch.setattr(cli_module, "_build_broker", lambda config: client)
    db_path = tmp_path / "t.db"
    _repo_at(db_path)

    result = CliRunner().invoke(
        cli, ["--db", str(db_path), "--config", str(config_path), "fetch"]
    )
    assert result.exit_code == 0, result.output
    assert set(client.requests) == {Granularity.ONE_DAY}
