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
from keel.research import bias
from keel.strategy import backtest as backtest_mod
from keel.strategy.reduction import Reduction
from keel.strategy.rules.reverse_dca import ReverseDca
from keel.types import Candle, Granularity
from tests.data.test_sell_proposals import _row as _proposal_row
from tests.strategy.rule_conformance import minimal_params

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
            agent.build_rule_from_params(kind, minimal_params(kind)).describe()["params"]
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
        agent.build_rule_from_params("pullback_continuation", {"product_id": "BTC-USD"}).describe()[
            "params"
        ]
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


def test_describe_params_carries_the_declared_space_of_each_parameter() -> None:
    """#528: the parameter help says what a parameter is ALLOWED to be, not just what it
    currently is. `space` comes from the rule's own `param_space()` declaration (read off
    the same constructed rule the persisted-params set comes from), so the help can never
    restate a range the rule did not declare.

    A param that is one slot of a larger declared kwarg (pullback's `ema_periods`, whose
    three searched slots ride one tuple) carries ALL THREE specs; a param outside every
    declaration (a filter toggle, an unsearched period) carries none -- empty, the honest
    'no declared range', never a hand-typed range."""
    turtle = describe_params("turtle_breakout")
    (entry_space,) = turtle["entry_lookback"].space
    assert (entry_space.name, entry_space.type) == ("entry_lookback", "int")
    assert (entry_space.lo, entry_space.hi) == (20, 60)
    assert turtle["adx_period"].space == ()  # real kwarg, never declared sweepable
    assert turtle["use_macd_confirm"].space == ()  # a bool filter is not a dimension

    pullback = describe_params("pullback_continuation")
    fan = pullback["ema_periods"].space
    assert [spec.name for spec in fan] == ["ema_fast", "ema_mid", "ema_slow"]
    assert all(spec.kwarg == "ema_periods" for spec in fan)
    assert pullback["entry_zone"].space == ()

    assert all(help_.space == () for help_ in describe_params("dca").values())


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
    rule_id = repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="disabled", now_ts=NOW_TS)
    out, err = _collect()
    outcome = apply_rule_enable(repo, rule_id, echo=out.append, echo_err=err.append)
    assert outcome.new_status == "candidate"
    assert repo.get_rules()[0]["status"] == "candidate"
    assert "CANDIDATE" in "\n".join(out)


def test_apply_rule_enable_refuses_a_rule_that_is_not_disabled(repo: Repository) -> None:
    rule_id = repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="candidate", now_ts=NOW_TS)
    out, err = _collect()
    with pytest.raises(RulesRefused):
        apply_rule_enable(repo, rule_id, echo=out.append, echo_err=err.append)
    assert "not disabled" in "\n".join(err)
    assert repo.get_rules()[0]["status"] == "candidate"


def test_apply_rule_disable_and_demote_write_through_the_service(repo: Repository) -> None:
    live_id = repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="live", now_ts=NOW_TS)
    out, err = _collect()
    outcome = apply_rule_demote(repo, live_id, echo=out.append, echo_err=err.append)
    assert outcome.new_status == "paper"
    outcome = apply_rule_disable(repo, live_id, echo=out.append, echo_err=err.append)
    assert outcome.new_status == "disabled"
    assert repo.get_rules()[0]["status"] == "disabled"
    assert "status -> disabled" in "\n".join(out)


# -- the promotion gate prices fills per product (#335) -------------------------------------------


def _daily(repo, product_id: str, *, quote_volume: float, bars: int = 60) -> None:
    """`bars` ONE_DAY candles whose `volume * close` is `quote_volume` — the one statistic
    `screen.median_daily_quote_volume` reads, at the granularity it is named for."""
    price = Decimal("100")
    repo.upsert_candles(
        product_id,
        Granularity.ONE_DAY,
        [
            Candle(
                ts=1_700_000_000 + index * 86_400,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=Decimal(str(quote_volume)) / price,
            )
            for index in range(bars)
        ],
    )


def test_the_gate_prices_a_liquid_product_near_the_floor(repo) -> None:
    """The model's floor is reached at its $500M/day anchor, so 600M/day lands on it."""
    _daily(repo, "BTC-USD", quote_volume=600_000_000.0)

    rate, measured = rules_mod.backtest_slippage(repo, "BTC-USD")

    assert measured is True
    assert rate == backtest_mod.SLIPPAGE_FLOOR_PCT


def test_the_gate_prices_a_thin_product_far_above_the_floor(repo) -> None:
    """**The whole point of #335.** The gate used to price every fill at the floor, and the
    floor is not a typical rate — it is the best case the model can produce. Measured over the
    real universe not one asset reaches it; TON-USD sits at 36.8x.
    """
    _daily(repo, "TON-USD", quote_volume=280_000.0)

    rate, measured = rules_mod.backtest_slippage(repo, "TON-USD")

    assert measured is True
    assert rate == backtest_mod.SLIPPAGE_CAP_PCT
    assert rate > backtest_mod.SLIPPAGE_FLOOR_PCT * 20


def test_no_daily_bars_falls_back_and_says_so(repo) -> None:
    """`measured=False` is the load-bearing half. An absent statistic must never be presented
    as a measured verdict — that is the distinction `simulate`'s report already draws, and the
    gate has to draw it too or the flat 5bp reads as a finding."""
    rate, measured = rules_mod.backtest_slippage(repo, "NOTHING-USD")

    assert measured is False
    assert rate == backtest_mod.SLIPPAGE_FLOOR_PCT


def test_the_statistic_comes_from_daily_bars_not_the_rules_own_granularity(repo) -> None:
    """The trap this sidesteps, which produces no error.

    `median_daily_quote_volume` returns a PER-BAR median despite its name. Reading it off an
    HOURLY series and handing it to a model anchored on a DAILY volume reports every asset as
    maximally thin — so a rule that trades hourly would price at the cap regardless of how
    liquid its product actually is. Reading ONE_DAY bars, as `simulate.slippage_assumptions`
    already does, means no scaling is needed and none can be forgotten.
    """
    _daily(repo, "BTC-USD", quote_volume=600_000_000.0)
    # An hourly series at 1/24th the daily figure. If the helper read THIS, the rate would be
    # far above the floor rather than on it.
    price = Decimal("100")
    repo.upsert_candles(
        "BTC-USD",
        Granularity.ONE_HOUR,
        [
            Candle(
                ts=1_700_000_000 + i * 3600,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=Decimal("25000000") / price,
            )
            for i in range(200)
        ],
    )

    rate, _measured = rules_mod.backtest_slippage(repo, "BTC-USD")

    assert rate == backtest_mod.SLIPPAGE_FLOOR_PCT, (
        "the gate is reading a per-bar statistic off the wrong granularity"
    )


def _spy_slippage(monkeypatch) -> list[Decimal]:
    """Capture the `slippage_pct` every gate backtest actually passes to the engine.

    The helper being right proves nothing about the CALL SITES using it — a mutation removing
    `slippage_pct=` from either one left every test above green, which is why this exists.
    """
    seen: list[Decimal] = []
    real = backtest_mod.backtest

    def spy(rule, candles, **kwargs):
        seen.append(kwargs.get("slippage_pct"))
        return real(rule, candles, **kwargs)

    monkeypatch.setattr(rules_mod.backtest_mod, "backtest", spy)
    return seen


def test_the_gate_backtest_path_passes_the_per_product_rate(repo, monkeypatch) -> None:
    _daily(repo, "TON-USD", quote_volume=280_000.0)
    rule = agent.build_rule_from_params("turtle_breakout", {"product_id": "TON-USD"})
    seen = _spy_slippage(monkeypatch)

    rules_mod._backtest_rule(repo, rule, "ONE_DAY", Decimal("0.012"), lambda _m: None)

    assert seen == [backtest_mod.SLIPPAGE_CAP_PCT], (
        f"_backtest_rule priced fills at {seen} — it is not passing the per-product rate"
    )


def test_the_resolved_backtest_path_passes_the_per_product_rate(repo, monkeypatch) -> None:
    """`backtest_resolved` is the seam the strategy console runs, so it must price the same
    way the CLI does — two front-ends disagreeing about cost is exactly what #259's
    one-definition discipline exists to prevent."""
    _daily(repo, "TON-USD", quote_volume=280_000.0)
    # Params in their STORED (JSON-plain) form -- `build_rule_from_params` is the boundary that
    # turns these back into `Decimal`s, and writing a row with real Decimals in it would test a
    # shape the DB never holds.
    rule_id = repo.insert_rule(
        "turtle_breakout",
        {"product_id": "TON-USD", "granularity": "ONE_DAY"},
        status="candidate",
    )
    resolved = rules_mod.resolve_rule_backtest(repo, None, rule_id)
    seen = _spy_slippage(monkeypatch)

    rules_mod.backtest_resolved(resolved)

    assert resolved.slippage_pct == backtest_mod.SLIPPAGE_CAP_PCT
    assert resolved.slippage_measured is True
    assert seen == [backtest_mod.SLIPPAGE_CAP_PCT], (
        f"backtest_resolved priced fills at {seen} — the resolved rate is not reaching the engine"
    )


def test_run_rule_backtest_line_labels_the_units_of_every_figure(repo: Repository) -> None:
    """#820: the line used to print `expectancy=` and `max_drawdown=` bare -- price units for a
    one-coin position, which read as R beside the gate that (since #820) judges R. The R
    figures lead, and the price-unit ones carry a `_px` suffix and a legend saying what px is."""
    import re

    repo.insert_rule(
        "dca", {"product_id": "BTC-USD", "cadence_days": 7}, status="candidate", now_ts=NOW_TS
    )
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _daily_candles(30))
    out, err = _collect()
    _outcome, stats = run_rule_backtest(
        repo, _config(), 1, granularity_opt="ONE_DAY", echo=out.append, echo_err=err.append
    )

    keys = re.findall(r"(\w+)=", out[0])
    assert keys[:7] == [
        "n_trades",
        "win_rate",
        "expectancy_r",
        "max_drawdown_r",
        "profit_factor",
        "expectancy_px",
        "max_drawdown_px",
    ]
    assert f"expectancy_px={stats.expectancy} " in out[0]
    assert f"max_drawdown_px={stats.max_drawdown} (px = price units, 1-coin notional)" in out[0]
    assert stats.expectancy_r is None  # this flat series closes no trade that carries R
    assert "expectancy_r=n/a " in out[0]


# -- the sleeve_sell route through attempt_promotion (P12 Task 12.2, spec §3.7, plan R21) ---------

_DAY = 86_400
#: 2026-09-29T00:00:00Z: "now" for the paper-day arithmetic, fixed so no test reads the clock.
_SLEEVE_NOW = 1_790_640_000


def _sleeve_candle(day: int, close: str) -> Candle:
    c = Decimal(close)
    return Candle(ts=day * _DAY, open=c, high=c, low=c, close=c, volume=Decimal("1000"))


#: 121 daily candles whose close wanders (100 + (7d mod 13)) so a peek has signal: the last close
#: (day 120: 108) is above the one before it (day 119: 101), which is not above day 118's (107).
#: So a rule deciding about bar t-1 from bar t's close fires on the full series, claiming bar
#: 119, while the live view of bar 119 -- which cannot see bar 120 -- does not fire there.
_WANDER = [_sleeve_candle(d, str(100 + (d * 7) % 13)) for d in range(121)]


class _PeekingReduce(ReverseDca):
    """Decides about bar t-1 using bar t's close -- lookahead by construction, so the adapter
    must flag it (R21 is not vacuous). Its decision AT a bar changes once the next bar exists."""

    def reduce_signal(self, holding, candles_by_tf, costs):
        days = candles_by_tf.get(Granularity.ONE_DAY, [])
        if len(days) < 2 or days[-1].close <= days[-2].close:
            return None
        return Reduction(
            self.product_id, Decimal("0.001"), self.name, {}, days[-2].close, days[-2].ts
        )


def _sleeve_rule(repo: Repository, *, status: str = "candidate", **params: Any) -> int:
    return repo.insert_rule(
        "reverse_dca",
        {"product_id": "BTC-USD", "target_usd": "10", "min_price_floor": "1", **params},
        status=status,
        now_ts=_SLEEVE_NOW,
    )


def _paper_reverse(repo: Repository, *, days_in_paper: int) -> int:
    rid = _sleeve_rule(repo, status="paper")
    repo._conn.execute(
        "UPDATE rules SET promoted_at = ? WHERE id = ?",
        (_SLEEVE_NOW - days_in_paper * _DAY, rid),
    )
    repo._conn.commit()
    return rid


def _review(repo: Repository, rule_id: int, *, rule_status: str = "paper") -> int:
    pid = repo.insert_sell_proposal(_proposal_row(rule_id=rule_id, rule_status=rule_status))
    repo.update_sell_proposal(pid, reviewed_ts=_SLEEVE_NOW)
    return pid


def _status(repo: Repository, rule_id: int) -> str:
    return {r["id"]: r["status"] for r in repo.get_rules()}[rule_id]


def _promote(repo: Repository, rule_id: int, **kwargs: Any):
    out, err = _collect()
    try:
        outcome = attempt_promotion(
            repo,
            _config(),
            rule_id,
            now_ts=_SLEEVE_NOW,
            echo=out.append,
            echo_err=err.append,
            **kwargs,
        )
    except RulesRefused:
        return None, out, err
    return outcome, out, err


@pytest.fixture
def btc_book(repo: Repository) -> Repository:
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _WANDER)
    return repo


def test_the_lookahead_adapter_flags_a_peeking_sell_rule() -> None:
    rule = _PeekingReduce("BTC-USD", target_usd=Decimal("10"), min_price_floor=Decimal("1"))
    report = bias.lookahead_analysis(
        rules_mod._reduction_as_detect(rule), {Granularity.ONE_DAY: _WANDER}, warmup=5
    )
    assert report.verdict == "lookahead_detected"
    assert [(d.bar_ts, d.field) for d in report.divergences] == [(119 * _DAY, "setup_present")]


def test_the_lookahead_adapter_passes_the_real_rule_over_the_same_bars() -> None:
    """The control: the same harness, the same bars, the shipped rule -- clean, over anchors
    actually walked, so the peeking verdict above is the peek and not the fixture."""
    rule = ReverseDca("BTC-USD", target_usd=Decimal("10"), min_price_floor=Decimal("1"))
    report = bias.lookahead_analysis(
        rules_mod._reduction_as_detect(rule), {Granularity.ONE_DAY: _WANDER}, warmup=5
    )
    assert report.verdict == "clean"
    assert report.n_bars_checked == 116


def test_the_adapter_maps_a_reduction_onto_entry_stop_and_target() -> None:
    """R21's mapping: entry = the reduction's price, stop = 0, target = its qty, ts = its bar;
    over a synthetic one-unit lot bought at the first close."""
    rule = ReverseDca("BTC-USD", target_usd=Decimal("10"), min_price_floor=Decimal("1"))
    setup = rules_mod._reduction_as_detect(rule)({Granularity.ONE_DAY: _WANDER})
    assert setup is not None
    # Day 120 is a 30-day cadence day; close 108. gross = 10 / (1 - 0.012 - 0.0005).
    gross = Decimal("10") / (Decimal("1") - Decimal("0.012") - Decimal("0.0005"))
    assert (setup.entry, setup.stop, setup.target, setup.ts) == (
        Decimal("108"),
        Decimal("0"),
        gross / Decimal("108"),
        120 * _DAY,
    )
    assert rules_mod._reduction_as_detect(rule)({}) is None


def test_a_sleeve_rule_is_never_judged_by_a_trade_floor(btc_book, monkeypatch) -> None:
    def _no_backtest(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a sleeve_sell rule must never be backtested for a floor")

    monkeypatch.setattr(backtest_mod, "backtest", _no_backtest)
    rid = _sleeve_rule(btc_book)
    outcome, out, err = _promote(btc_book, rid)
    assert outcome is not None and outcome.new_status == "paper"
    assert _status(btc_book, rid) == "paper"
    assert err == []
    joined = "\n".join(out)
    for word in ("min_trades", "n_trades", "overfitting", "PBO"):
        assert word not in joined
    assert out[-1] == f"rule {rid} (reverse_dca): status -> paper"


def test_a_peeking_sleeve_rule_is_refused_at_candidate(btc_book, monkeypatch) -> None:
    monkeypatch.setitem(agent.RULE_REGISTRY, "reverse_dca", _PeekingReduce)
    rid = _sleeve_rule(btc_book)
    outcome, _out, err = _promote(btc_book, rid)
    assert outcome is None
    assert _status(btc_book, rid) == "candidate"
    assert any("lookahead" in line for line in err)


def test_no_cached_daily_bars_is_a_refusal_not_a_clean_lookahead(repo) -> None:
    """A check that walked no bar is not a pass (#440's fail-closed rule)."""
    rid = _sleeve_rule(repo)
    outcome, _out, err = _promote(repo, rid)
    assert outcome is None
    assert _status(repo, rid) == "candidate"
    assert any("no cached ONE_DAY candles" in line for line in err)


def test_paper_to_live_refused_without_a_reviewed_proposal(btc_book) -> None:
    rid = _paper_reverse(btc_book, days_in_paper=61)
    outcome, _out, err = _promote(btc_book, rid)
    assert outcome is None
    assert _status(btc_book, rid) == "paper"
    assert any("keel dca proposals review" in line for line in err)


def test_paper_to_live_refused_before_sixty_days(btc_book) -> None:
    rid = _paper_reverse(btc_book, days_in_paper=59)
    _review(btc_book, rid)
    outcome, _out, _err = _promote(btc_book, rid)
    assert outcome is None
    assert _status(btc_book, rid) == "paper"


def test_only_this_rules_own_reviewed_paper_proposals_count(btc_book) -> None:
    rid = _paper_reverse(btc_book, days_in_paper=61)
    other = _sleeve_rule(btc_book, status="paper")
    _review(btc_book, other)  # another rule's proposal
    _review(btc_book, rid, rule_status="live")  # this rule's, but not made in paper
    unreviewed = btc_book.insert_sell_proposal(_proposal_row(rule_id=rid, rule_status="paper"))
    assert unreviewed
    outcome, _out, _err = _promote(btc_book, rid)
    assert outcome is None
    assert _status(btc_book, rid) == "paper"

    _review(btc_book, rid)
    outcome, _out, _err = _promote(btc_book, rid)
    assert outcome is not None and outcome.new_status == "live"


def test_paper_to_live_refused_beside_a_live_dca_without_the_flag(btc_book) -> None:
    rid = _paper_reverse(btc_book, days_in_paper=61)
    _review(btc_book, rid)
    btc_book.insert_rule(
        "dca", {"product_id": "BTC-USD", "cadence_days": 7, "budget_usd": "40"}, status="live"
    )
    outcome, _out, err = _promote(btc_book, rid)
    assert outcome is None
    assert _status(btc_book, rid) == "paper"
    assert any("--allow-concurrent-dca" in line for line in err)

    outcome, out, _err = _promote(btc_book, rid, allow_concurrent_dca=True)
    assert outcome is not None and outcome.new_status == "live"
    assert _status(btc_book, rid) == "live"
    assert out[-1] == f"rule {rid} (reverse_dca): status -> live"


@pytest.mark.parametrize(
    ("product", "status"), [("BTC-USD", "paper"), ("BTC-USD", "candidate"), ("ETH-USD", "live")]
)
def test_only_a_live_dca_on_the_same_product_is_concurrent(btc_book, product, status) -> None:
    rid = _paper_reverse(btc_book, days_in_paper=61)
    _review(btc_book, rid)
    btc_book.insert_rule(
        "dca", {"product_id": product, "cadence_days": 7, "budget_usd": "40"}, status=status
    )
    outcome, _out, _err = _promote(btc_book, rid)
    assert outcome is not None and outcome.new_status == "live"


def test_force_is_refused_for_a_sleeve_rule(btc_book) -> None:
    """Spec §3.7: `--force` exists for a rule whose backtest cannot REACH the floor, not for one
    that has no floor -- it would skip the 60 days and the reviewed proposal entirely."""
    rid = _paper_reverse(btc_book, days_in_paper=1)
    outcome, _out, err = _promote(btc_book, rid, force=True)
    assert outcome is None
    assert _status(btc_book, rid) == "paper"
    assert any("--force" in line for line in err)


def test_allow_concurrent_dca_is_refused_on_a_rule_it_cannot_apply_to(repo) -> None:
    rid = repo.insert_rule(
        "dca", {"product_id": "BTC-USD", "cadence_days": 7}, status="candidate", now_ts=NOW_TS
    )
    outcome, _out, err = _promote(repo, rid, allow_concurrent_dca=True)
    assert outcome is None
    assert _status(repo, rid) == "candidate"
    assert any("--allow-concurrent-dca" in line for line in err)


def test_the_cli_flag_reaches_the_gate(tmp_path, valid_config_path) -> None:
    """`keel rules promote <id> --allow-concurrent-dca`: refused without the flag, promoted with
    it -- the option is wired through to `attempt_promotion`, not just declared."""
    import time

    from click.testing import CliRunner

    from keel.cli import cli

    db = tmp_path / "t.db"
    conn = connect(str(db))
    migrate(conn)
    file_repo = Repository(conn)
    file_repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _WANDER)
    rid = _sleeve_rule(file_repo, status="paper")
    file_repo._conn.execute(
        "UPDATE rules SET promoted_at = ? WHERE id = ?", (int(time.time()) - 61 * _DAY, rid)
    )
    file_repo._conn.commit()
    _review(file_repo, rid)
    file_repo.insert_rule(
        "dca", {"product_id": "BTC-USD", "cadence_days": 7, "budget_usd": "40"}, status="live"
    )
    args = ["--db", str(db), "--config", str(valid_config_path), "rules", "promote", str(rid)]

    refused = CliRunner().invoke(cli, args)
    assert refused.exit_code == 1
    assert _status(file_repo, rid) == "paper"

    promoted = CliRunner().invoke(cli, [*args, "--allow-concurrent-dca"])
    assert promoted.exit_code == 0, promoted.output
    assert _status(file_repo, rid) == "live"


def test_too_few_daily_bars_to_reach_one_anchor_is_a_refusal(repo) -> None:
    """Past `bias.DEFAULT_WARMUP` the walk starts; a cache shorter than that walks no bar, and a
    check that walked no bar reads `clean` in the harness -- the gate must not take it as one."""
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _WANDER[: bias.DEFAULT_WARMUP])
    rid = _sleeve_rule(repo)
    outcome, out, err = _promote(repo, rid)
    assert outcome is None
    assert _status(repo, rid) == "candidate"
    assert any("over 0 daily bars" in line for line in out)
    assert any("lookahead" in line for line in err)


def test_the_adapter_hands_every_view_the_same_synthetic_holding() -> None:
    """The holding must not be the thing that differs between a prefix view and the full one:
    one lot of 1 unit at the FIRST close, fee-free, in every call the harness makes."""
    from keel.strategy.reduction import Holding, Lot

    seen: list[Holding] = []

    class _Spy(ReverseDca):
        def reduce_signal(self, holding, candles_by_tf, costs):
            seen.append(holding)
            return super().reduce_signal(holding, candles_by_tf, costs)

    rule = _Spy("BTC-USD", target_usd=Decimal("10"), min_price_floor=Decimal("1"))
    bias.lookahead_analysis(
        rules_mod._reduction_as_detect(rule), {Granularity.ONE_DAY: _WANDER}, warmup=5
    )
    assert len(seen) > 100
    first = _WANDER[0]
    expected = Holding(
        "BTC-USD", (Lot(0, "dca", first.ts, Decimal("1"), first.close, Decimal("0")),)
    )
    assert set(seen) == {expected}


# -- issue #929: the sleeve-sell lookahead gate truncates at every sampled end -------------------
#
# The OLD check ran the ONE harness once over the whole cached series: Axis A only compares at
# the full run's own claimed anchor, so for `reverse_dca` that anchor exists only when the LAST
# cached bar happens to be a cadence day (~1 in `cadence_days`). Any other day, the full run
# claims nothing, nothing is compared, and `n_bars_checked > 0` (ONE_DAY walked some anchors
# regardless) read a vacuous "clean". `_sleeve_lookahead` instead re-runs the harness on every
# sampled TRUNCATION `daily[: e + 1]`, so a comparison happens at every day the rule actually
# fired on, and `n_compared` says how many of those truncations had one.


def test_a_peeking_rule_is_refused_even_when_the_cache_ends_off_its_firing_day(
    repo, monkeypatch
) -> None:
    """Issue #929's repro: over 120 bars (whose LAST bar, day 119, is not a day the peeking rule
    fires on -- day 119's close 101 is not above day 118's 107, see `_WANDER`'s own docstring),
    the OLD full-run-only check compared nothing there and read a vacuous 'clean'. This must
    fail against the unpatched code (the old vacuous promotion) before the fix, and pass after."""
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _WANDER[:120])
    monkeypatch.setitem(agent.RULE_REGISTRY, "reverse_dca", _PeekingReduce)
    rid = _sleeve_rule(repo)
    outcome, _out, err = _promote(repo, rid)
    assert outcome is None
    assert _status(repo, rid) == "candidate"
    assert any("lookahead" in line for line in err)


def test_reverse_dca_over_a_non_cadence_ending_cache_still_compares_and_promotes(repo) -> None:
    """The shipped (non-peeking) `ReverseDca`, same 120-bar cache whose last bar is off cadence:
    truncating at every sampled end still finds the two cadence bars actually walked in range
    (days 60 and 90 -- the only multiples of 30 in the walked window 50..119), so the gate has a
    real comparison, and promotes on it."""
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _WANDER[:120])
    rid = _sleeve_rule(repo)
    outcome, out, err = _promote(repo, rid)
    assert outcome is not None and outcome.new_status == "paper"
    assert _status(repo, rid) == "paper"
    assert err == []
    [line] = [line for line in out if "sleeve_sell gate" in line]
    assert "lookahead clean over 70 daily bars (2 compared)" in line


def test_a_rule_that_never_fires_over_the_cache_compares_nothing_and_is_refused(repo) -> None:
    """A `min_price_floor` above every cached close: `reduce_signal` never fires at any
    truncation, so nothing is ever compared -- an UN-RUN check, refused by name, not a vacuous
    clean pass."""
    repo.upsert_candles("BTC-USD", Granularity.ONE_DAY, _WANDER)
    rid = _sleeve_rule(repo, min_price_floor="99999")
    outcome, _out, err = _promote(repo, rid)
    assert outcome is None
    assert _status(repo, rid) == "candidate"
    assert any(
        "never fired" in line and "compared nothing" in line and "121" in line for line in err
    )


# -- issue #931: the fail-closed branch when the lookahead harness itself raises ------------------


def test_a_raising_lookahead_harness_is_a_refusal_not_a_crash(btc_book, monkeypatch) -> None:
    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(rules_mod.bias_mod, "lookahead_analysis", _boom)
    rid = _sleeve_rule(btc_book)
    outcome, _out, err = _promote(btc_book, rid)
    assert outcome is None
    assert _status(btc_book, rid) == "candidate"
    assert any("lookahead analysis could not run" in line for line in err)


# -- review finding 4c: --allow-concurrent-dca at a step it cannot affect ------------------------


def test_allow_concurrent_dca_at_candidate_has_no_effect_and_says_so(btc_book) -> None:
    """Q2 gates only the paper -> live step; passed at candidate -> paper it does nothing, and
    the gate says so rather than silently accepting it."""
    rid = _sleeve_rule(btc_book)
    outcome, out, _err = _promote(btc_book, rid, allow_concurrent_dca=True)
    assert outcome is not None and outcome.new_status == "paper"
    assert _status(btc_book, rid) == "paper"
    notice = [line for line in out if "--allow-concurrent-dca" in line and "no effect" in line]
    assert len(notice) == 1
