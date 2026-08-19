"""Tests for the rules SERVICE layer (issue #390 C4) -- the extractions `keel rules`' console
dispatches to, and the O8 parameter-help introspection.

Two surfaces:

* **The extracted services** (`add_rule_row`, `run_rule_backtest`, `attempt_promotion`,
  `apply_rule_enable`/`disable`/`demote`) -- the exact validation/write logic that used to
  live only inside the click command bodies, now callable with a repo, a config and values.
  The CLI wrappers keep their byte-identical output (pinned by the untouched
  `tests/commands/test_rules_add.py` and the group's other pre-existing tests); these tests
  pin the SERVICE seam the console dispatches through: same refusals, same messages, same
  writes, no click anywhere.
* **`describe_params`** -- the O8 parameter-level help, derived by introspection from the
  rule classes themselves: the per-parameter docstrings ADDED AT THE CLASS (`PARAM_DOCS`),
  the constructor's own defaults and types, the `Literal` choices the rule declares, and the
  quotable set from `agent.coerced_param_keys`. Never a hand-maintained table: two params are
  pinned VERBATIM against the class source, and every registered kind must document every
  operator-facing param.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from keel import agent
from keel.commands import rules as rules_mod
from keel.commands.rules import (
    RulesRefused,
    RulesUsageError,
    add_rule_row,
    apply_rule_demote,
    apply_rule_disable,
    apply_rule_enable,
    attempt_promotion,
    describe_params,
    run_rule_backtest,
)
from keel.config import (
    AutoTradeConfig,
    Caps,
    Config,
    DcaConfig,
    MarketDataConfig,
    MoneyMgmtConfig,
)
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.types import Candle, Granularity

NOW_TS = 1_800_000_000


@pytest.fixture
def repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    return Repository(conn)


def _config(**overrides: Any) -> Config:
    base: dict[str, Any] = dict(
        allowlist=["BTC", "ETH"],
        target_weights={},
        risk_pct=Decimal("0.01"),
        caps=Caps(
            max_per_order_usd=Decimal("100000"),
            max_per_day_usd=Decimal("300000"),
            max_exposure_usd=Decimal("1000000"),
            max_per_asset_pct=Decimal("1"),
        ),
        market_data=MarketDataConfig(granularities=[], history_days=365),
        auto_trade=AutoTradeConfig(mode="paper", interval_sec=900),
        money_mgmt=MoneyMgmtConfig(
            max_total_dd_pct=Decimal("0.20"), max_weekly_dd_pct=Decimal("0.08")
        ),
        dca=DcaConfig(budget_usd=Decimal("50"), cadence_days=7),
    )
    base.update(overrides)
    return Config(**base)


def _collect() -> tuple[list[str], list[str]]:
    out: list[str] = []
    err: list[str] = []
    return out, err


# -- describe_params (O8: parameter help, single-sourced from the classes) -----------------------


def test_describe_params_pins_two_turtle_docstrings_verbatim_from_the_class() -> None:
    """The O8 amendment names turtle_breakout's params exactly; two of them are pinned here
    VERBATIM against the class source (`TurtleBreakout.PARAM_DOCS`) so the help can never
    drift into a second, hand-maintained copy -- if the class docstring changes, this test
    and the class change together or not at all."""
    from keel.strategy.rules.turtle_breakout import TurtleBreakout

    params = describe_params("turtle_breakout")
    assert params["entry_lookback"].doc == TurtleBreakout.PARAM_DOCS["entry_lookback"]
    assert params["entry_lookback"].doc == (
        "Donchian-high entry channel, in bars of the rule's granularity; the walk-forward "
        "OOS default is 40 (was 20). Longer = fewer, later entries."
    )
    assert params["atr_stop_mult"].doc == TurtleBreakout.PARAM_DOCS["atr_stop_mult"]
    assert params["atr_stop_mult"].doc == (
        'Stop distance in ATRs ("N"; default 2N). Wider = fewer stop-outs, bigger risk '
        "per trade -- feeds the R:R the promotion gate floors."
    )


def test_describe_params_derives_default_and_type_from_the_constructor() -> None:
    params = describe_params("turtle_breakout")
    entry = params["entry_lookback"]
    assert entry.default == 40
    assert entry.type_name == "int"
    assert entry.quotable is False
    stop = params["atr_stop_mult"]
    assert stop.default == Decimal("2")
    assert stop.type_name == "Decimal"
    assert stop.quotable is True  # arrives JSON-plain as a string; keel coerces it
    gran = params["granularity"]
    assert gran.default is Granularity.ONE_DAY
    assert gran.quotable is True  # stored as its .value string, coerced on the way in


def test_describe_params_carries_the_literals_the_rule_itself_declares() -> None:
    params = describe_params("pullback_continuation")
    assert params["entry_zone"].choices == ("ema_touch", "ema_band")
    assert params["stop_method"].choices == ("fixed", "atr")
    assert params["signal_patterns"].choices is not None
    assert "pin_bar" in params["signal_patterns"].choices


def test_describe_params_covers_every_kind_and_every_param_minus_identity() -> None:
    """Every registered kind documents every operator-facing param it PERSISTS: the
    identity pair (`product_id`, supplied by --product, and `name`) is excluded, and so is
    any constructor kwarg the kind does not persist (the same `describe()["params"]` source
    `add_rule_row`'s dropped-param refusal reads) -- everything the row can actually carry
    must carry a doc, because a missing doc is a missing O8 help line, not a calm blank."""
    for kind, rule_cls in agent.RULE_REGISTRY.items():
        params = describe_params(kind)
        persisted = set(
            agent.build_rule_from_params(kind, {"product_id": "BTC-USD"})
            .describe()["params"]
        )
        accepted = {
            name
            for name in rules_mod._accepted_params(rule_cls)
            if name not in ("product_id", "name")
        }
        assert set(params) == accepted & persisted, f"{kind}: params mismatch"
        for name, help_ in params.items():
            assert help_.doc.strip(), f"{kind}.{name} carries no PARAM_DOCS entry"


def test_describe_params_offers_only_params_the_kind_persists() -> None:
    """The help never offers a param the add flow would REFUSE: pullback_continuation
    ACCEPTS `granularity` but does not persist it (`describe()["params"]` carries no such
    key -- `add_rule_row` refuses it as silently-lost), so the form must not offer it;
    turtle_breakout persists its `granularity` and keeps offering it."""
    pullback = describe_params("pullback_continuation")
    assert "granularity" not in pullback
    # Every offered pullback param is one the row persists.
    persisted = set(
        agent.build_rule_from_params(
            "pullback_continuation", {"product_id": "BTC-USD"}
        ).describe()["params"]
    )
    assert set(pullback) <= persisted

    turtle = describe_params("turtle_breakout")
    assert "granularity" in turtle  # turtle DOES persist it (params carries the key)


def test_describe_params_quotable_matches_the_coercion_tables() -> None:
    """The 'may be quoted' answer comes from `agent.coerced_param_keys` -- the coercion
    boundary itself -- so the help can never disagree with what `rules add` accepts."""
    for kind in agent.RULE_REGISTRY:
        quotable = agent.coerced_param_keys(kind)
        for name, help_ in describe_params(kind).items():
            assert help_.quotable == (name in quotable), f"{kind}.{name}"


# -- add_rule_row: the `rules add` service --------------------------------------------------------


def test_add_rule_row_writes_the_candidate_and_returns_the_cli_lines(repo: Repository) -> None:
    out, err = _collect()
    outcome = add_rule_row(
        repo,
        _config(),
        kind="turtle_breakout",
        product="BTC-USD",
        params_json='{"entry_lookback": 55}',
        now_ts=NOW_TS,
        echo=out.append,
        echo_err=err.append,
    )
    rows = repo.get_rules()
    assert len(rows) == 1
    assert rows[0]["status"] == "candidate"
    assert rows[0]["params"]["entry_lookback"] == 55
    assert outcome.rule_id == rows[0]["id"]
    assert outcome.lines == tuple(out)
    assert any("added rule" in line and "status=candidate" in line for line in out)
    assert err == []


def test_add_rule_row_surfaces_the_services_own_validation_messages(repo: Repository) -> None:
    """A quoted number for a non-Decimal param is refused with the SAME message the CLI
    prints -- the console renders the service's own words, never a TUI-authored variant."""
    out, err = _collect()
    with pytest.raises(RulesRefused):
        add_rule_row(
            repo,
            _config(),
            kind="rsi_meanrev",
            product="BTC-USD",
            params_json='{"oversold": "10.0"}',
            now_ts=NOW_TS,
            echo=out.append,
            echo_err=err.append,
        )
    assert repo.get_rules() == []
    joined = "\n".join(err)
    assert "rsi_meanrev cannot use these params" in joined
    assert "oversold" in joined
    assert "quoted" in joined


def test_add_rule_row_usage_errors_carry_the_cli_param_hints(repo: Repository) -> None:
    out, err = _collect()
    with pytest.raises(RulesUsageError) as excinfo:
        add_rule_row(
            repo,
            _config(),
            kind="turtle_breakout",
            product="BTC-USD,ETH-USD",
            params_json=None,
            now_ts=NOW_TS,
            echo=out.append,
            echo_err=err.append,
        )
    assert excinfo.value.param_hint == "--product"
    assert "exactly ONE product" in str(excinfo.value)
    with pytest.raises(RulesUsageError) as excinfo:
        add_rule_row(
            repo,
            _config(),
            kind="turtle_breakout",
            product="BTC-USD",
            params_json='{"product_id": "ETH-USD"}',
            now_ts=NOW_TS,
            echo=out.append,
            echo_err=err.append,
        )
    assert excinfo.value.param_hint == "--params"
    assert "disagrees with --product" in str(excinfo.value)
    assert repo.get_rules() == []


def test_add_rule_row_refuses_an_unknown_kind_before_writing(repo: Repository) -> None:
    out, err = _collect()
    with pytest.raises(RulesRefused):
        add_rule_row(
            repo,
            _config(),
            kind="not_a_kind",
            product="BTC-USD",
            params_json=None,
            now_ts=NOW_TS,
            echo=out.append,
            echo_err=err.append,
        )
    assert "unknown rule kind" in "\n".join(err)
    assert repo.get_rules() == []


# -- run_rule_backtest / attempt_promotion: the retry services ------------------------------------


def _daily_candles(n: int, *, start: int = 1_700_000_000) -> list[Candle]:
    return [
        Candle(
            ts=start + i * 86400,
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("10"),
        )
        for i in range(n)
    ]


def test_run_rule_backtest_returns_the_stats_and_the_fee_line(repo: Repository) -> None:
    repo.insert_rule(
        "dca", {"product_id": "BTC-USD", "cadence_days": 7}, status="candidate", now_ts=NOW_TS
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _daily_candles(30))
    out, err = _collect()
    outcome, stats = run_rule_backtest(
        repo, _config(), 1, granularity_opt="ONE_DAY", echo=out.append, echo_err=err.append
    )
    # The line IS the stats: the exact `rules backtest` shape, fee provenance included --
    # and the returned BacktestResult is the same run the line reports.
    assert len(out) == 1
    assert out[0].startswith("rule 1 (dca): n_trades=")
    assert f"n_trades={stats.n_trades}" in out[0]
    assert "fee_pct=" in out[0]


def test_run_rule_backtest_refuses_an_unknown_id(repo: Repository) -> None:
    out, err = _collect()
    with pytest.raises(RulesRefused):
        run_rule_backtest(repo, _config(), 99, echo=out.append, echo_err=err.append)
    assert "no rule with id 99" in "\n".join(err)


def test_attempt_promotion_without_pbo_reports_the_machines_own_reasons(
    repo: Repository,
) -> None:
    """No `--pbo-session`: the gate's OWN wording -- the overfitting check NOT RUN reason --
    is what the caller gets, and the status does not move."""
    repo.insert_rule(
        "dca",
        {"product_id": "BTC-USD", "cadence_days": 7, "budget_usd": "50"},
        status="candidate",
        now_ts=NOW_TS,
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _daily_candles(60))
    out, err = _collect()
    outcome = attempt_promotion(
        repo,
        _config(),
        1,
        granularity_opt="ONE_DAY",
        echo=out.append,
        echo_err=err.append,
    )
    assert outcome.new_status == "candidate"
    joined = "\n".join(out)
    assert "overfitting check" in joined
    assert "NOT RUN" in joined
    assert repo.get_rules()[0]["status"] == "candidate"


def test_attempt_promotion_force_advances_and_warns(repo: Repository) -> None:
    repo.insert_rule(
        "dca",
        {"product_id": "BTC-USD", "cadence_days": 7, "budget_usd": "50"},
        status="candidate",
        now_ts=NOW_TS,
    )
    out, err = _collect()
    outcome = attempt_promotion(
        repo, _config(), 1, force=True, echo=out.append, echo_err=err.append
    )
    assert outcome.new_status == "paper"
    assert repo.get_rules()[0]["status"] == "paper"
    assert "FORCE-PROMOTING" in "\n".join(out)
    assert "BYPASSING" in "\n".join(out)


# -- enable / disable / demote services ------------------------------------------------------------


def test_apply_rule_enable_restores_a_disabled_rule_at_candidate(repo: Repository) -> None:
    rule_id = repo.insert_rule(
        "dca", {"product_id": "BTC-USD"}, status="disabled", now_ts=NOW_TS
    )
    out, err = _collect()
    outcome = apply_rule_enable(repo, rule_id, echo=out.append, echo_err=err.append)
    assert outcome.new_status == "candidate"
    assert repo.get_rules()[0]["status"] == "candidate"
    assert "CANDIDATE" in "\n".join(out)


def test_apply_rule_enable_refuses_a_rule_that_is_not_disabled(repo: Repository) -> None:
    rule_id = repo.insert_rule(
        "dca", {"product_id": "BTC-USD"}, status="candidate", now_ts=NOW_TS
    )
    out, err = _collect()
    with pytest.raises(RulesRefused):
        apply_rule_enable(repo, rule_id, echo=out.append, echo_err=err.append)
    assert "not disabled" in "\n".join(err)
    assert repo.get_rules()[0]["status"] == "candidate"


def test_apply_rule_disable_and_demote_write_through_the_service(repo: Repository) -> None:
    live_id = repo.insert_rule(
        "dca", {"product_id": "BTC-USD"}, status="live", now_ts=NOW_TS
    )
    out, err = _collect()
    outcome = apply_rule_demote(repo, live_id, echo=out.append, echo_err=err.append)
    assert outcome.new_status == "paper"
    outcome = apply_rule_disable(repo, live_id, echo=out.append, echo_err=err.append)
    assert outcome.new_status == "disabled"
    assert repo.get_rules()[0]["status"] == "disabled"
    assert "status -> disabled" in "\n".join(out)
