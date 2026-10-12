"""Shared pytest fixtures for keel tests."""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from keel_core import telemetry
from keel_core.cash_posture import (
    ATTESTATION_TTL_SEC,
    SPOT_CASH,
    CashPostureState,
    VenueCashPosture,
)
from keel_core.subscription import BrokerSubscription, SubscriptionStatus
from keel_core.trade_scope import TradeScopeState, VenueTradeScope

if TYPE_CHECKING:
    from keel.data.repository import Repository

ATTESTATION_PERIOD_SEC = 365 * 24 * 3600


def attest_subscription(
    repo: Repository,
    *,
    now_ts: int,
    free_volume_usd: Decimal | None,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    pacing: str = "opportunistic",
    attest_due_ts: int | None = None,
    venue: str = "coinbase",
    tier_name: str = "Preferred",
) -> None:
    """Write an attested subscription record -- the single source of truth for its shape.

    Three test modules each built this record inline with near-identical bodies, so any change
    to `BrokerSubscription`'s fields had to land in three places or the copies drifted apart
    silently. Their `_attest` wrappers survive (each module's call convention differs) but now
    delegate here rather than re-constructing the record.

    `now_ts` is required rather than defaulted: each caller module defines its own `NOW_TS`,
    and a default here would silently bind the wrong clock into one of them.
    """
    repo.upsert_broker_subscription(
        BrokerSubscription(
            venue=venue,
            tier_name=tier_name,
            free_volume_usd=free_volume_usd,
            pacing=pacing,
            subscription_usd_month=Decimal("29.99"),
            status=status,
            attested_at=now_ts,
            attest_due_ts=(
                attest_due_ts if attest_due_ts is not None else now_ts + ATTESTATION_PERIOD_SEC
            ),
        )
    )


def attest_cash_posture(
    repo: Repository,
    *,
    now_ts: int,
    state: CashPostureState = CashPostureState.ATTESTED,
    attested_posture: str | None = SPOT_CASH,
    venue: str = "coinbase",
    attested_ts: int | None = None,
    attest_due_ts: int | None = None,
    refuted_ts: int | None = None,
    refuted_reason: str | None = None,
    credential_fingerprint: str | None = None,
) -> None:
    """Write a venue cash-posture record -- rail 22's baseline (#691), the `attest_trade_scope`
    counterpart.

    Defaults to an in-force `ATTESTED` / `spot_cash` record: the shape that lets rail 22 admit a
    live entry, so a test not ABOUT rail 22 is not incidentally vetoed. Same reason this file
    already defaults `attest_trade_scope` to `CONFIRMED` and `attest_subscription` to a roomy
    allowance.

    `attest_due_ts` defaults to `now_ts + ATTESTATION_TTL_SEC`, i.e. freshly attested. A test
    about EXPIRY passes its own, in the past.

    `credential_fingerprint` defaults to `None` -- the "recorded without fingerprinting" value,
    which reads as MATCHING, so a test that does not care about #633 is not vetoed by it.
    """
    repo.upsert_venue_cash_posture(
        VenueCashPosture(
            venue=venue,
            state=state,
            attested_posture=attested_posture,
            attested_ts=now_ts if attested_ts is None else attested_ts,
            attest_due_ts=(
                now_ts + ATTESTATION_TTL_SEC if attest_due_ts is None else attest_due_ts
            ),
            refuted_ts=refuted_ts,
            refuted_reason=refuted_reason,
            credential_fingerprint=credential_fingerprint,
        )
    )


def attest_trade_scope(
    repo: Repository,
    *,
    now_ts: int,
    state: TradeScopeState = TradeScopeState.CONFIRMED,
    attested_scope: str | None = None,
    venue: str = "coinbase",
    attested_ts: int | None = None,
    confirmed_ts: int | None = None,
    refuted_ts: int | None = None,
    refuted_reason: str | None = None,
    credential_fingerprint: str | None = None,
) -> None:
    """Write a venue trade-scope record -- rail 20's baseline, the `attest_subscription`
    counterpart for #233.

    Defaults to `CONFIRMED` with `attested_scope=None`: the exact shape the v14 backfill
    produces for an already-live venue, and the state that lets rail 20 admit a live entry --
    the quiet default every test not ABOUT rail 20 needs so it is not incidentally vetoed, same
    reason `attest_subscription` defaults to a roomy allowance.

    `credential_fingerprint` (#633) defaults to `None` for the same reason: the v14-backfill
    shape this helper otherwise mimics never had one, and `None` is also the value rail 20's
    fingerprint check treats as "no evidence to disagree with", so tests not specifically ABOUT
    the fingerprint check are not incidentally vetoed by it either.

    `now_ts` is required rather than defaulted, like `attest_subscription`: each caller module
    defines its own clock, and a default here would silently bind the wrong one.
    """
    if confirmed_ts is None and state is TradeScopeState.CONFIRMED:
        confirmed_ts = now_ts
    if attested_ts is None and state is TradeScopeState.ATTESTED:
        attested_ts = now_ts
    repo.upsert_venue_trade_scope(
        VenueTradeScope(
            venue=venue,
            state=state,
            attested_scope=attested_scope,
            attested_ts=attested_ts,
            confirmed_ts=confirmed_ts,
            refuted_ts=refuted_ts,
            refuted_reason=refuted_reason,
            credential_fingerprint=credential_fingerprint,
        )
    )


VALID_CONFIG_YAML = """
allowlist:
  - BTC
  - ETH
  - PAXG

target_weights:
  BTC: 0.40
  ETH: 0.30
  PAXG: 0.30

risk_pct: 0.01

caps:
  max_per_order_usd: 100
  max_per_day_usd: 300
  max_exposure_usd: 5000
  max_per_asset_pct: 0.50

market_data:
  granularities:
    - ONE_DAY
    - ONE_HOUR
    - FIFTEEN_MINUTE
  history_days: 365

auto_trade:
  mode: paper
  enabled: false
  interval_sec: 900

promotion:
  min_trades: 100
  min_expectancy: 0.0
  min_rr: 1.5
  min_win_rate: 0.55

money_mgmt:
  profit_trigger_pct: 0.10
  acceleration_pct: 0.05
  max_total_dd_pct: 0.20
  max_weekly_dd_pct: 0.08

dca:
  budget_usd: 50
  cadence_days: 7

quote_currency: USD

subscription:
  assumed_free_volume_usd: 500
  unsubscribed_allowance_usd: 0
  pacing: opportunistic
"""


@pytest.fixture(autouse=True)
def _isolate_telemetry_context():
    """Clear the telemetry ContextVars between tests.

    `venue` and `cycle_id` are process-lifetime bindings by design -- the CLI binds venue once
    in `_load_cfg` and never unbinds, which is right for a real process but leaks across tests
    sharing one interpreter. Without this, whether `test_venue_absent_when_unbound` passes
    depends on whether a CLI test ran first, which is exactly the kind of order-dependence that
    makes a suite untrustworthy.
    """
    yield
    telemetry.bind_venue(None)
    telemetry.bind_cycle(None)


@pytest.fixture
def write_config(tmp_path: Path):
    """Factory fixture: write arbitrary YAML text to a temp config.yaml, return its path."""

    def _write(text: str) -> Path:
        path = tmp_path / "config.yaml"
        path.write_text(text)
        return path

    return _write


@pytest.fixture
def valid_config_path(write_config) -> Path:
    return write_config(VALID_CONFIG_YAML)


@pytest.fixture(autouse=True)
def _isolate_trials_ledger(tmp_path, monkeypatch):
    """Never let a test write to the REAL trials ledger.

    `docs/experiments/trials-ledger.jsonl` is a git-tracked, append-only, hash-chained audit
    record. A test run that appends to it corrupts a research artifact with fixture noise --
    which is exactly what happened once before this fixture existed: the `simulate` CLI tests
    silently wrote six junk rows into the committed ledger. Autouse, so no future test has to
    remember.
    """
    monkeypatch.setattr(
        "keel.research.ledger.DEFAULT_LEDGER_PATH", tmp_path / "trials-ledger.jsonl"
    )


@pytest.fixture(autouse=True)
def _restore_keel_logger():
    """Undo `configure_logging`'s global mutation of the "keel" logger after every test.

    `keel_core.logging_setup.configure_logging` sets `propagate = False` and (with the default
    `verbose: false`) level `ERROR` on `logging.getLogger("keel")`. Any test that invokes the
    CLI therefore leaks that state into every later test: `caplog` attaches its handler to the
    ROOT logger, so `propagate = False` silently starves it and `caplog.text` comes back empty.

    That is a latent ordering bug, not a theoretical one -- it stayed hidden only because the
    existing CLI tests live in `tests/test_cli.py`, which sorts AFTER `tests/execution/`.
    Adding CLI tests under `tests/data/` (which sorts before) broke two equity/reconcile
    assertions that had nothing to do with the change. Restore the logger instead of relying
    on collection order.
    """
    logger = logging.getLogger("keel")
    level, propagate, handlers = logger.level, logger.propagate, list(logger.handlers)
    try:
        yield
    finally:
        logger.setLevel(level)
        logger.propagate = propagate
        logger.handlers[:] = handlers


#: The checkout a run must leave as it found it (#964), resolved once at import -- before any
#: fixture has moved the working directory -- so the guard below watches the repository and the
#: directory pytest was started in, not wherever a test happens to be standing.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_INVOCATION_CWD = Path.cwd().resolve()


def _logs_snapshot(root: Path) -> dict[str, tuple[int, int]] | None:
    """Every file under `root/logs` with its size and mtime, or `None` when there is no `logs/`.

    Size AND mtime: a leak into an existing file (the operator ran `keel` in this checkout once)
    grows it or touches it, and a leak that creates an EMPTY file -- the shape #964 reported --
    still changes the answer from `None` to a dict."""
    logs = root / "logs"
    if not logs.is_dir():
        return None
    return {
        str(path.relative_to(logs)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(logs.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(autouse=True)
def _the_checkouts_logs_are_untouched():
    """Fail the test that wrote into the checkout's `logs/`, by name, the moment it does (#964).

    `logging.file` defaults to the RELATIVE `logs/keel.log`, and `configure_logging` opens it
    against the working directory. Every CLI test that loads a config without a `logging:`
    section used to open it in the checkout, leaving a file that the NEXT run's timeline export
    read back as "the engine log could not be read (empty)" -- red on the second run, green
    after a manual `rm -rf logs`, and false kills in every mutation run in between.

    Per test rather than once per session: a session check would say only that SOMETHING
    leaked, and the leak is found by bisecting 8,000 tests. A `stat` per test is free.
    `_run_in_tmp_path` below is what keeps it quiet; this is what notices when it stops."""
    roots = sorted({_REPO_ROOT, _INVOCATION_CWD})
    before = {root: _logs_snapshot(root) for root in roots}
    yield
    touched = [str(root / "logs") for root in roots if _logs_snapshot(root) != before[root]]
    if touched:
        pytest.fail(
            f"this test wrote into {touched}: a test must log under its own tmp_path, never the "
            "checkout (#964) -- the next run reads that file back as this deployment's engine log",
            pytrace=False,
        )


@pytest.fixture(autouse=True)
def _run_in_tmp_path(tmp_path, monkeypatch):
    """Every test stands in its own `tmp_path`, with `KEEL_HOME` pinned to it (#964).

    The working directory is where every relative path in a loaded config lands --
    `logging.file`'s `logs/keel.log` first among them, which `configure_logging` opens as given.
    Fixing that one test at a time is a game of whack-a-mole: dozens of CLI tests load a config
    with no `logging:` section, and the next one written will too.

    `KEEL_HOME` goes with it because leaving the checkout changes what `paths.state_root` picks.
    The checkout holds a `config.yaml`, so it counted as a deployment root; a bare `tmp_path`
    does not, and an unpinned lookup would fall through to the operator's REAL app-data
    directory. Pinned, the writer (`configure_logging`, relative to the cwd) and the readers
    (`resolve_under_state_root`) agree on one file inside the test's own directory. A test about
    `state_root` itself unsets it or re-points it, as `tests/core/test_paths.py` already does.

    A test that reads a repository file opens it from `__file__`, never from the cwd."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KEEL_HOME", str(tmp_path))
