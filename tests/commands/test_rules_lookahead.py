"""`keel rules lookahead` and the promotion refusal it feeds (issue #440, C1a).

The command is the operator-facing rendering of `keel.research.bias.lookahead_analysis` over
a SAVED rule -- resolved through the same seam `rules backtest` uses -- and the promotion
gate refuses a rule whose verdict is `lookahead_detected`. Pinned here:

- a clean saved rule exits 0 and prints its verdict;
- a leaky rule exits 1 with the divergences (which bar, which field, both values);
- the higher-TF poison axis reads EVERY cached coarser series (the deployment shape caches
  ONE_HOUR + ONE_DAY with no SIX_HOUR), and a rule blindly reading the last ONE_DAY bar --
  the canonical engine-veto leak -- is caught through the real service seam;
- when only the trading TF is cached, the render and the promote gate SAY the higher-TF
  axis did not run rather than implying coverage;
- `--recursive` maps each rule family to its own ATR exactly as the family computes it
  (turtle's bounded tail window, rsi's full history) or reports honestly that the family
  configures no recursive-suspect indicator (dca);
- a detect that RAISES inside the promote gate's lookahead analysis is a graceful fail-closed
  refusal, not a traceback;
- `rules promote` refuses a leaky rule without mutating the row, and `--force` remains the
  documented bypass, exactly as it is for the rest of the gate.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from click.testing import CliRunner

from keel import agent
from keel.cli import cli
from keel.commands.rules import RulesRefused, attempt_promotion, run_rule_lookahead
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.strategy.rules.base import Rule, Setup
from keel.types import Candle, Granularity

DAY = 86400
HOUR = 3600
NOW_TS = 1_800_000_000
START = 1_700_000_000


def _repo(tmp_path) -> Repository:
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    return Repository(conn)


def _daily(n: int = 120, *, start: int = 1_700_000_000) -> list[Candle]:
    """Peak-close shape: max close at bar 60, a high spike at bar 110 -- so a rule targeting
    the max high of everything it can see is fed a different target by every prefix."""
    candles = []
    for i in range(n):
        close = Decimal(100 + (i if i <= 60 else 120 - i))
        high = Decimal(500) if i == 110 else close + Decimal(1)
        candles.append(
            Candle(
                ts=start + i * DAY,
                open=close - Decimal(1),
                high=high,
                low=close - Decimal(2),
                close=close,
                volume=Decimal(10),
            )
        )
    return candles


def _flat_daily(n: int = 300, *, start: int = 1_700_000_000) -> list[Candle]:
    """Constant true range -- the converging case for the recursive ATR check."""
    return [
        Candle(
            ts=start + i * DAY,
            open=Decimal(100),
            high=Decimal(110),
            low=Decimal(100),
            close=Decimal(110),
            volume=Decimal(1),
        )
        for i in range(n)
    ]


def _hourly(n: int = 240, *, start: int = START) -> list[Candle]:
    """Hourly bars with a 3-bar close cycle (100/101/102) -- fires a `close[-1] >
    close[-2]` momentum condition on two bars out of three."""
    return [
        Candle(
            ts=start + j * HOUR,
            open=Decimal(100),
            high=Decimal(103),
            low=Decimal(99),
            close=Decimal(100 + (j % 3)),
            volume=Decimal(1),
        )
        for j in range(n)
    ]


def _coarse_daily(n: int = 10, *, start: int = START) -> list[Candle]:
    """`n` distinct daily closes, spaced a day apart over the hourly span -- every bar
    differs, so a blind last-bar read sees a different value at every prefix length."""
    return [
        Candle(
            ts=start + k * DAY,
            open=Decimal(100 + 10 * k),
            high=Decimal(101 + 10 * k),
            low=Decimal(99 + 10 * k),
            close=Decimal(100 + 10 * k),
            volume=Decimal(10),
        )
        for k in range(n)
    ]


def _trending_hourly(n: int = 300, *, start: int = START) -> list[Candle]:
    """A steady ~1%/bar uptrend: each close clears the prior 40-bar Donchian high (once the
    base is past 100) and +DI dominates -DI on every bar, so ADX runs near 100 and the REAL
    turtle at `granularity=ONE_HOUR` actually FIRES -- synthetic trending candles built to
    make the rule enter, not merely to keep it quiet."""
    candles = []
    close = 100.0
    for i in range(n):
        candles.append(
            Candle(
                ts=start + i * HOUR,
                open=Decimal(str(round(close - 0.5, 8))),
                high=Decimal(str(round(close + 1, 8))),
                low=Decimal(str(round(close - 1, 8))),
                close=Decimal(str(round(close, 8))),
                volume=Decimal(10),
            )
        )
        close *= 1.01
    return candles


class _LeakyRule(Rule):
    """A saved-rule stand-in whose target is the max high of the ENTIRE series it is handed
    -- the classic 'target the future swing' leak. Reconstructed via a monkeypatched
    `agent._build_rule`, the same seam `rules backtest`/`promote` rebuild rules through."""

    def __init__(self) -> None:
        self.name = "leaky"
        self.params: dict[str, Any] = {"product_id": "BTC-USD", "granularity": "ONE_DAY"}
        self.product_id = "BTC-USD"
        self.granularity = Granularity.ONE_DAY

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        candles = candles_by_tf[Granularity.ONE_DAY]
        signal = max(candles, key=lambda c: c.close)
        return Setup(
            product_id="BTC-USD",
            direction="long",
            entry=signal.close,
            stop=signal.close - Decimal(10),
            target=max(c.high for c in candles),
            context={},
            ts=signal.ts,
        )

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        return False

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


class _BlindCoarseHourlyRule(Rule):
    """An ONE_HOUR rule that reads the LAST ONE_DAY close blindly at every firing hourly
    bar -- the canonical engine-veto leak (`engine._higher_tf_bias_ok` reads the coarsest
    CACHED higher TF, which a deployment shaped [ONE_DAY, ONE_HOUR, FIFTEEN_MINUTE] serves
    as ONE_DAY, with no SIX_HOUR between them). Rebuilt through a monkeypatched
    `agent._build_rule`, the saved-rule seam the service resolves through."""

    def __init__(self) -> None:
        self.name = "blind_coarse_hourly"
        self.params: dict[str, Any] = {"product_id": "BTC-USD", "granularity": "ONE_HOUR"}
        self.product_id = "BTC-USD"
        self.granularity = Granularity.ONE_HOUR

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        hourly = candles_by_tf[Granularity.ONE_HOUR]
        daily = candles_by_tf.get(Granularity.ONE_DAY, [])
        if not daily or len(hourly) < 2 or hourly[-1].close <= hourly[-2].close:
            return None
        entry = daily[-1].close  # blind: may still be forming at the anchor
        return Setup(
            product_id="BTC-USD",
            direction="long",
            entry=entry,
            stop=entry - Decimal(10),
            target=entry + Decimal(20),
            context={},
            ts=hourly[-1].ts,
        )

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        return False

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


class _ExplodingRule(Rule):
    """A rule whose detect RAISES on early (short) views: the full series is fine, but the
    walk's first live views are below the rule's own guard -- the shape that used to escape
    `attempt_promotion` as a traceback."""

    def __init__(self) -> None:
        self.name = "exploding"
        self.params: dict[str, Any] = {"product_id": "BTC-USD", "granularity": "ONE_DAY"}
        self.product_id = "BTC-USD"
        self.granularity = Granularity.ONE_DAY

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        candles = candles_by_tf[Granularity.ONE_DAY]
        if len(candles) < 60:
            raise ValueError(f"cannot decide on {len(candles)} bars")
        return None

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        return False

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


def _invoke(tmp_path, config_path, *args):
    return CliRunner().invoke(
        cli, ["--db", str(tmp_path / "t.db"), "--config", str(config_path), *args]
    )


# -- the diagnostic command -------------------------------------------------------------------


def test_lookahead_on_a_clean_saved_rule_exits_zero(tmp_path, valid_config_path) -> None:
    repo = _repo(tmp_path)
    repo.insert_rule(
        "turtle_breakout", {"product_id": "BTC-USD"}, status="candidate", now_ts=NOW_TS
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _flat_daily())

    result = _invoke(tmp_path, valid_config_path, "rules", "lookahead", "1", "--sample-step", "10")

    assert result.exit_code == 0, result.output
    assert "rule 1" in result.output
    assert "clean" in result.output


def test_lookahead_on_a_leaky_saved_rule_exits_one_with_divergences(
    tmp_path, valid_config_path, monkeypatch
) -> None:
    repo = _repo(tmp_path)
    repo.insert_rule(
        "turtle_breakout", {"product_id": "BTC-USD"}, status="candidate", now_ts=NOW_TS
    )
    candles = _daily()
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, candles)
    monkeypatch.setattr(agent, "_build_rule", lambda row: _LeakyRule())

    result = _invoke(tmp_path, valid_config_path, "rules", "lookahead", "1", "--sample-step", "5")

    assert result.exit_code == 1, result.output
    assert "lookahead_detected" in result.output
    assert f"ts={candles[60].ts}" in result.output
    assert "field=target" in result.output
    assert "161" in result.output and "500" in result.output


def test_lookahead_refuses_an_unknown_id(tmp_path, valid_config_path) -> None:
    _repo(tmp_path)
    result = _invoke(tmp_path, valid_config_path, "rules", "lookahead", "99")
    assert result.exit_code == 1
    assert "no rule with id 99" in result.output


def test_recursive_flag_runs_the_rules_own_atr(tmp_path, valid_config_path) -> None:
    repo = _repo(tmp_path)
    repo.insert_rule(
        "turtle_breakout", {"product_id": "BTC-USD"}, status="candidate", now_ts=NOW_TS
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _flat_daily())

    result = _invoke(
        tmp_path, valid_config_path, "rules", "lookahead", "1", "--recursive", "--sample-step", "10"
    )

    assert result.exit_code == 0, result.output
    assert "recursive" in result.output
    assert "stable" in result.output
    assert "atr" in result.output


def test_recursive_flag_reports_honestly_when_no_suspect_indicator(
    tmp_path, valid_config_path
) -> None:
    repo = _repo(tmp_path)
    repo.insert_rule(
        "dca", {"product_id": "BTC-USD", "cadence_days": 7}, status="candidate", now_ts=NOW_TS
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _flat_daily(60))

    result = _invoke(
        tmp_path,
        valid_config_path,
        "rules",
        "lookahead",
        "1",
        "--granularity",
        "ONE_DAY",
        "--recursive",
        "--sample-step",
        "5",
    )

    assert result.exit_code == 0, result.output
    assert "no recursive-suspect indicator" in result.output


def test_recursive_flag_windows_turtles_atr_like_the_rule_does(tmp_path, valid_config_path) -> None:
    """Turtle sizes its stop from ATR over a bounded tail (`work = series[-needed:]`,
    `needed = max(entry+1, exit+1, adx*4, atr*4)` = 80 at the defaults), not full history --
    the recursive check must replay the indicator the rule actually trades, and say so in
    the indicator's name."""
    repo = _repo(tmp_path)
    repo.insert_rule(
        "turtle_breakout", {"product_id": "BTC-USD"}, status="candidate", now_ts=NOW_TS
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _flat_daily())

    result = _invoke(
        tmp_path, valid_config_path, "rules", "lookahead", "1", "--recursive", "--sample-step", "10"
    )

    assert result.exit_code == 0, result.output
    assert "atr[-80:](20)[-1]" in result.output


# -- the higher-TF poison axis over the deployment-shaped cache -----------------------------------


def test_deployment_shaped_cache_catches_a_blind_coarse_reader(tmp_path, monkeypatch) -> None:
    """THE pin for the MAJOR fix: a ONE_HOUR rule that blindly reads the last ONE_DAY
    close -- the engine-veto leak, against the granularity pair deployments actually cache
    (ONE_HOUR + ONE_DAY; SIX_HOUR is cached by no shipped config). The next-coarser pick
    used to query SIX_HOUR, find nothing, and hand the poison axis an empty series: this
    exact rule sailed through clean. Through the REAL service seam (`run_rule_lookahead`),
    not a hand-assembled harness call."""
    repo = _repo(tmp_path)
    repo.insert_rule(
        "turtle_breakout",
        {"product_id": "BTC-USD", "granularity": "ONE_HOUR"},
        status="candidate",
        now_ts=NOW_TS,
    )
    hourly = _hourly(240)
    repo.upsert_candles("BTC-USD", Granularity.ONE_HOUR, hourly)
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _coarse_daily(10))
    # SIX_HOUR deliberately NOT cached: the deployment shape every shipped config ships.
    monkeypatch.setattr(agent, "_build_rule", lambda row: _BlindCoarseHourlyRule())

    _outcome, report = run_rule_lookahead(repo, None, 1)

    assert report.verdict == "lookahead_detected", report.divergences
    first = report.divergences[0]
    assert first.field == "entry"
    # The live view reads the last ONE_DAY bar CLOSED by the anchor; the poison view hands
    # the rule the full daily series -- different bars, different entry.
    assert Decimal(first.prefix_value) != Decimal(first.full_value)


def test_lookahead_says_when_the_higher_tf_axis_did_not_run(tmp_path, valid_config_path) -> None:
    """Only the trading TF cached: no coarser series exists, the poison axis cannot run,
    and the render SAYS so instead of letting a clean verdict imply full coverage."""
    repo = _repo(tmp_path)
    repo.insert_rule(
        "turtle_breakout", {"product_id": "BTC-USD"}, status="candidate", now_ts=NOW_TS
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _flat_daily())

    result = _invoke(tmp_path, valid_config_path, "rules", "lookahead", "1", "--sample-step", "10")

    assert result.exit_code == 0, result.output
    assert "clean" in result.output
    assert "higher-TF axis not run: no coarser series cached" in result.output


def test_firing_real_hourly_turtle_is_clean_with_a_poisoned_daily_view(
    tmp_path, valid_config_path
) -> None:
    """A REAL turtle at ONE_HOUR on trending candles (it actually FIRES -- pinned first, so
    this can never rot into a verdict about a silent rule) is clean through the real
    service seam even with a poisoned ONE_DAY view beside its own series: the rule keys
    every read on its own declared granularity, so the future's closed coarse bars are
    invisible to it by construction."""
    repo = _repo(tmp_path)
    repo.insert_rule(
        "turtle_breakout",
        {"product_id": "BTC-USD", "granularity": "ONE_HOUR"},
        status="candidate",
        now_ts=NOW_TS,
    )
    hourly = _trending_hourly(300)
    repo.upsert_candles("BTC-USD", Granularity.ONE_HOUR, hourly)
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _coarse_daily(13))

    turtle = agent._build_rule(repo.get_rules()[0])
    assert (
        turtle.detect({Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: _coarse_daily(13)})
        is not None
    )

    _outcome, report = run_rule_lookahead(repo, None, 1, sample_step=10)

    assert report.anchor_granularity == "ONE_HOUR"
    assert report.verdict == "clean", report.divergences
    assert report.notes == ()  # the ONE_DAY view ran the higher-TF axis


# -- the promotion refusal --------------------------------------------------------------------


def test_promote_refuses_a_leaky_rule_without_mutating_the_row(
    tmp_path, valid_config_path, monkeypatch
) -> None:
    repo = _repo(tmp_path)
    repo.insert_rule(
        "turtle_breakout", {"product_id": "BTC-USD"}, status="candidate", now_ts=NOW_TS
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _daily())
    monkeypatch.setattr(agent, "_build_rule", lambda row: _LeakyRule())

    result = _invoke(tmp_path, valid_config_path, "rules", "promote", "1")

    assert result.exit_code == 1, result.output
    assert "lookahead" in result.output
    assert "keel rules lookahead 1" in result.output
    # Fail-closed: nothing was written.
    assert repo.get_rules()[0]["status"] == "candidate"


def test_promote_force_remains_the_documented_bypass_for_a_leaky_rule(
    tmp_path, valid_config_path, monkeypatch
) -> None:
    repo = _repo(tmp_path)
    repo.insert_rule(
        "turtle_breakout", {"product_id": "BTC-USD"}, status="candidate", now_ts=NOW_TS
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _daily())
    monkeypatch.setattr(agent, "_build_rule", lambda row: _LeakyRule())

    result = _invoke(tmp_path, valid_config_path, "rules", "promote", "1", "--force")

    assert result.exit_code == 0, result.output
    assert repo.get_rules()[0]["status"] == "paper"
    assert "FORCE-PROMOTING" in result.output


def test_attempt_promotion_service_refuses_leaky_rules(monkeypatch) -> None:
    """The service seam the console also dispatches through refuses the same way."""
    from tests.commands.test_rules_services import _config  # the existing pinned Config shape

    conn = connect(":memory:")
    migrate(conn)
    repo = Repository(conn)
    repo.insert_rule(
        "turtle_breakout", {"product_id": "BTC-USD"}, status="candidate", now_ts=NOW_TS
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _daily())
    monkeypatch.setattr(agent, "_build_rule", lambda row: _LeakyRule())

    out: list[str] = []
    err: list[str] = []
    try:
        attempt_promotion(
            repo,
            _config(),
            1,
            echo=out.append,
            echo_err=err.append,
        )
        raise AssertionError("expected RulesRefused")
    except RulesRefused:
        pass
    joined = "\n".join(err)
    assert "lookahead" in joined
    assert "keel rules lookahead 1" in joined
    assert repo.get_rules()[0]["status"] == "candidate"


def test_promote_refuses_gracefully_when_lookahead_analysis_raises(
    tmp_path, valid_config_path, monkeypatch
) -> None:
    """A detect that RAISES on the walk's early views is a fail-closed refusal naming the
    error -- never a traceback out of a promotion command, and never a silent pass."""
    repo = _repo(tmp_path)
    repo.insert_rule(
        "turtle_breakout", {"product_id": "BTC-USD"}, status="candidate", now_ts=NOW_TS
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _daily())
    monkeypatch.setattr(agent, "_build_rule", lambda row: _ExplodingRule())

    result = _invoke(tmp_path, valid_config_path, "rules", "promote", "1")

    assert result.exit_code == 1, result.output
    assert "lookahead analysis could not run" in result.output
    assert "cannot decide on" in result.output  # the named error, not just the fact of one
    # Graceful: the only exception that escaped the command is click's own SystemExit; a
    # traceback escaping would leave the ORIGINAL error (a ValueError) as result.exception.
    assert isinstance(result.exception, SystemExit)
    # Fail-closed: nothing was written.
    assert repo.get_rules()[0]["status"] == "candidate"


def test_attempt_promotion_service_refuses_gracefully_on_a_raising_detect(
    monkeypatch,
) -> None:
    """The service seam the console dispatches through fails closed the same way."""
    from tests.commands.test_rules_services import _config

    conn = connect(":memory:")
    migrate(conn)
    repo = Repository(conn)
    repo.insert_rule(
        "turtle_breakout", {"product_id": "BTC-USD"}, status="candidate", now_ts=NOW_TS
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _daily())
    monkeypatch.setattr(agent, "_build_rule", lambda row: _ExplodingRule())

    err: list[str] = []
    try:
        attempt_promotion(repo, _config(), 1, echo_err=err.append)
        raise AssertionError("expected RulesRefused")
    except RulesRefused:
        pass
    joined = "\n".join(err)
    assert "lookahead analysis could not run" in joined
    assert "cannot decide on" in joined
    assert repo.get_rules()[0]["status"] == "candidate"


def test_promote_surfaces_the_axis_not_run_note(tmp_path, valid_config_path) -> None:
    """The promote gate carries the report's coverage note too: a promotion judged over a
    single time frame says the higher-TF axis never ran, rather than reading as full
    coverage."""
    repo = _repo(tmp_path)
    repo.insert_rule(
        "turtle_breakout", {"product_id": "BTC-USD"}, status="candidate", now_ts=NOW_TS
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _flat_daily())

    result = _invoke(tmp_path, valid_config_path, "rules", "promote", "1")

    assert result.exit_code == 0, result.output
    assert "higher-TF axis not run: no coarser series cached" in result.output
    assert repo.get_rules()[0]["status"] == "candidate"


def test_promote_gate_lookahead_step_passes_a_firing_real_turtle(
    tmp_path, valid_config_path
) -> None:
    """The promote gate's real-rule pin: a FIRING real hourly turtle with a poisoned
    ONE_DAY view beside it gets PAST the lookahead step (the gate's later checks -- PBO
    not run, sample size -- may still hold it at candidate, and that refusal is theirs)."""
    repo = _repo(tmp_path)
    repo.insert_rule(
        "turtle_breakout",
        {"product_id": "BTC-USD", "granularity": "ONE_HOUR"},
        status="candidate",
        now_ts=NOW_TS,
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_HOUR, _trending_hourly(300))
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _coarse_daily(13))

    result = _invoke(tmp_path, valid_config_path, "rules", "promote", "1")

    assert result.exit_code == 0, result.output
    assert "fails the lookahead check" not in result.output
    assert "lookahead analysis could not run" not in result.output
    assert repo.get_rules()[0]["status"] == "candidate"
