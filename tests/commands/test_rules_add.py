"""`keel rules add` -- one candidate rule from operator-supplied params.

The command exists so a scout's parameter proposal has a path into `keel rules backtest`
without hand-written Python against `Repository.insert_rule`. Everything asserted here is a
property that makes that safe on a system trading real money:

- the row lands at `candidate` and there is NO way to ask for anything else;
- params that cannot CONSTRUCT their rule are refused before anything is written;
- a row that is written can be rebuilt by `agent._build_rule` -- the same reconstruction the
  agent cycle and the backtester perform -- with the `Decimal`s the rule expects;
- an inadmissible product id (rails 18/19) is refused at the keyboard, as in `rules seed`.
"""

from __future__ import annotations

import inspect
import json
from decimal import Decimal

from click.testing import CliRunner

from keel import agent
from keel.agent import RULE_REGISTRY, _build_rule
from keel.cli import cli
from keel.commands import _common
from keel.commands.rules import _declared_choices, rules_add
from keel.data.db import connect, migrate
from keel.data.repository import Repository


def _repo(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    migrate(conn)
    return Repository(conn)


def _add(tmp_path, valid_config_path, *args):
    return CliRunner().invoke(
        cli,
        [
            "--db", str(tmp_path / "t.db"),
            "--config", str(valid_config_path),
            "rules", "add",
            *args,
        ],
    )


# -- the lifecycle floor -------------------------------------------------------------------
#
# `candidate` is what makes this command safe to add at all: the row must still clear
# `rules backtest` and `rules promote` before it can ever reach `paper` or `live`.


def test_add_inserts_one_candidate_rule_and_prints_its_id(tmp_path, valid_config_path):
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "turtle_breakout",
        "--product", "BTC-USD",
        "--params", '{"entry_lookback": 55}',
    )

    assert result.exit_code == 0, result.output
    rows = repo.get_rules()
    assert len(rows) == 1
    assert rows[0]["kind"] == "turtle_breakout"
    assert rows[0]["status"] == "candidate"
    assert rows[0]["params"]["entry_lookback"] == 55
    assert rows[0]["params"]["product_id"] == "BTC-USD"
    assert str(rows[0]["id"]) in result.output


def test_status_is_candidate_and_no_option_can_override_it(tmp_path, valid_config_path):
    """The hard requirement. `rules seed` has `--status live`; this command must not.

    A flag here would let a proposed, never-backtested parameter set be written straight to
    `live`, where the agent polls it and places real orders on the next cycle.
    """
    assert not [p for p in rules_add.params if p.name == "status"], (
        "`rules add` must expose no status option -- candidate is the lifecycle floor"
    )

    repo = _repo(tmp_path)
    refused = _add(
        tmp_path, valid_config_path,
        "--kind", "dca", "--product", "BTC-USD", "--params", "{}",
        "--status", "live",
    )

    assert refused.exit_code != 0
    assert repo.get_rules() == []

    added = _add(tmp_path, valid_config_path, "--kind", "dca", "--product", "BTC-USD")

    assert added.exit_code == 0, added.output
    assert [r["status"] for r in repo.get_rules()] == ["candidate"]


# -- the round trip: a stored row must rebuild into a working rule -------------------------


def test_the_stored_row_rebuilds_into_a_rule_with_the_params_asked_for(
    tmp_path, valid_config_path
):
    """A row that stores but cannot rebuild fails later, inside a backtest or an agent cycle.

    `_build_rule` is the reconstruction every consumer performs (`rules backtest`,
    `agent.run_once`), so running the written row back through it is the only assertion that
    the row is usable at all.
    """
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "turtle_breakout",
        "--product", "ETH-USD",
        "--params", '{"entry_lookback": 55, "exit_lookback": 20}',
    )
    assert result.exit_code == 0, result.output

    row = repo.get_rules()[0]
    rule = _build_rule(row)

    assert rule.product_id == "ETH-USD"
    assert rule.rule_id == row["id"]
    assert rule.params["entry_lookback"] == 55
    assert rule.params["exit_lookback"] == 20


def test_json_numbers_land_as_the_decimals_the_rule_expects(tmp_path, valid_config_path):
    """The one Decimal-coercion boundary, exercised end to end.

    JSON has no `Decimal`: `--params '{"atr_stop_mult": 2.5}'` arrives as a float, while
    `TurtleBreakout` computes stops with `Decimal` arithmetic (mixing the two raises
    `TypeError` mid-backtest). The stored row must stay JSON-plain, and the rebuilt rule must
    hold `Decimal`s -- via the SAME `_DECIMAL_PARAMS` table `agent._build_rule` uses, so the
    two can never drift.
    """
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "turtle_breakout",
        "--product", "BTC-USD",
        "--params", '{"atr_stop_mult": 2.5, "target_rr": 4}',
    )
    assert result.exit_code == 0, result.output

    row = repo.get_rules()[0]
    assert not isinstance(row["params"]["atr_stop_mult"], Decimal), (
        "the stored row must be JSON-plain -- `insert_rule` json.dumps() it"
    )

    rule = _build_rule(row)
    assert rule.params["atr_stop_mult"] == Decimal("2.5")
    assert isinstance(rule.params["atr_stop_mult"], Decimal)
    assert rule.params["target_rr"] == Decimal("4")
    assert isinstance(rule.params["target_rr"], Decimal)


def test_a_rules_own_defaults_fill_in_the_params_not_given(tmp_path, valid_config_path):
    """Storing `.describe()`'s params, not the raw JSON, is what makes the row rebuildable:
    a row holding only `{"entry_lookback": 55}` would still rebuild, but nothing would record
    which defaults the backtest actually ran with.
    """
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "dca", "--product", "BTC-USD", "--params", '{"cadence_days": 14}',
    )
    assert result.exit_code == 0, result.output

    stored = repo.get_rules()[0]["params"]
    assert stored["cadence_days"] == 14
    assert stored["budget_usd"] == "50", "the constructor default, JSON-plain"
    assert stored["lookback_days"] == 90


def test_params_may_be_omitted_to_add_a_defaults_baseline(tmp_path, valid_config_path):
    """Comparing a proposal against the current defaults needs a defaults row to compare to,
    and `rules seed` refuses to make a second one for a (kind, product) it already seeded.
    """
    repo = _repo(tmp_path)

    result = _add(tmp_path, valid_config_path, "--kind", "dca", "--product", "BTC-USD")

    assert result.exit_code == 0, result.output
    rows = repo.get_rules()
    assert len(rows) == 1
    defaults = agent.RULE_REGISTRY["dca"](product_id="BTC-USD").describe()["params"]
    assert rows[0]["params"]["cadence_days"] == defaults["cadence_days"]
    assert rows[0]["params"]["budget_usd"] == str(defaults["budget_usd"])
    assert rows[0]["status"] == "candidate"


# -- refusals: nothing is written when the params cannot construct their rule ---------------


def test_an_unknown_param_is_refused_and_nothing_is_written(tmp_path, valid_config_path):
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "dca", "--product", "BTC-USD", "--params", '{"cadance_days": 7}',
    )

    assert result.exit_code != 0
    assert "cadance_days" in result.output, "the refusal must name the offending param"
    assert repo.get_rules() == []


def test_an_out_of_range_param_is_refused_by_the_rules_own_validation(
    tmp_path, valid_config_path
):
    """`Dca.__init__` raises `ValueError` on `cadence_days <= 0`. Constructing the rule BEFORE
    writing is what turns that into a refusal instead of a row that explodes at backtest time.
    """
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "dca", "--product", "BTC-USD", "--params", '{"cadence_days": 0}',
    )

    assert result.exit_code != 0
    assert "cadence_days must be positive" in result.output
    assert repo.get_rules() == []


def test_a_quoted_number_is_refused_for_a_param_that_is_not_a_decimal(
    tmp_path, valid_config_path
):
    """Construction alone does NOT catch this, and the row it would write crashes a backtest.

    `RsiMeanReversion` is a plain dataclass: `oversold="10.0"` constructs happily, stores
    happily, rebuilds happily, and then raises `TypeError: can only concatenate str (not "int")
    to str` inside `detect()` the first time anyone backtests it -- the exact "fails later,
    inside a backtest" outcome a refusal exists to prevent. Quoting a number is the likeliest
    typo when hand-copying a params object out of a proposal, because for the `Decimal` params
    quoting is the RIGHT thing to do.
    """
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "rsi_meanrev", "--product", "BTC-USD",
        "--params", '{"oversold": "10.0", "rsi_period": "2"}',
    )

    assert result.exit_code != 0
    assert "oversold" in result.output
    assert "rsi_period" in result.output, "every offending param is named, not just the first"
    assert repo.get_rules() == []


def test_a_fractional_number_is_refused_where_the_rule_counts_bars(tmp_path, valid_config_path):
    """`lookback_days` indexes a candle list (`candles[-lookback_days:]`). A float there
    constructs, passes `> 0`, stores, rebuilds -- and raises `TypeError: slice indices must be
    integers` in `detect()`. `1e400` is the same bug wearing a hat: JSON parses it to `inf`,
    which is positive, which is a float.
    """
    repo = _repo(tmp_path)

    for value in ("90.5", "1e400"):
        result = _add(
            tmp_path, valid_config_path,
            "--kind", "dca", "--product", "BTC-USD",
            "--params", f'{{"lookback_days": {value}}}',
        )
        assert result.exit_code != 0, value
        assert "lookback_days" in result.output
    assert repo.get_rules() == []


def test_a_list_param_is_refused_when_its_elements_are_the_wrong_type(
    tmp_path, valid_config_path
):
    """`build_rule_from_params` does a blind `tuple(value)` for `ema_periods`. Quoted numbers
    survive it as strings, and `ema()` then computes `2.0 / (period + 1)` on a `str`:
    `TypeError: can only concatenate str (not "int") to str`, inside a backtest. Quoting the
    numbers in a list is the same typo as quoting a scalar, and has to be refused the same way.
    """
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "pullback_continuation", "--product", "BTC-USD",
        "--params", '{"ema_periods": ["8", "20", "50"]}',
    )

    assert result.exit_code != 0
    assert "ema_periods" in result.output
    assert repo.get_rules() == []


def test_a_string_is_refused_where_a_list_param_is_expected(tmp_path, valid_config_path):
    """The nastiest shape of that same blind `tuple(value)`: `tuple("abc")` does not raise, it
    CHAR-SPLITS into `('a', 'b', 'c')` -- a plausible-looking three-EMA fan made of letters.
    """
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "pullback_continuation", "--product", "BTC-USD",
        "--params", '{"ema_periods": "abc"}',
    )

    assert result.exit_code != 0
    assert "ema_periods" in result.output
    assert repo.get_rules() == []


def test_a_non_finite_number_is_refused(tmp_path, valid_config_path):
    """JSON's non-standard `Infinity` literal parses, and `Decimal('Infinity') <= 0` is `False`
    -- so `Dca`'s budget guard passes it. `TurtleBreakout` does not validate `target_rr` at all,
    so nothing there stops it either. Infinity is not a parameter value under test; it is a
    number that got away, and no backtest can report on it.
    """
    repo = _repo(tmp_path)

    budget = _add(
        tmp_path, valid_config_path,
        "--kind", "dca", "--product", "BTC-USD", "--params", '{"budget_usd": Infinity}',
    )
    target = _add(
        tmp_path, valid_config_path,
        "--kind", "turtle_breakout", "--product", "BTC-USD",
        "--params", '{"target_rr": Infinity}',
    )
    lookback = _add(
        tmp_path, valid_config_path,
        "--kind", "dca", "--product", "BTC-USD", "--params", '{"lookback_days": 1e400}',
    )

    assert budget.exit_code != 0
    assert "budget_usd" in budget.output
    assert target.exit_code != 0
    assert "target_rr" in target.output
    assert lookback.exit_code != 0
    assert repo.get_rules() == []


def test_the_non_finite_refusal_states_the_reason_that_is_true_for_that_param(
    tmp_path, valid_config_path
):
    """The two param types fail differently, and a refusal that gives the wrong reason for its
    own worked example teaches the operator something false.

    A FLOAT param stores as a bare `Infinity` token, which really is unparseable by a strict
    JSON reader. A `_DECIMAL_PARAMS` param stores as the STRING `"Infinity"` -- perfectly valid
    JSON, which is worse rather than better: it round-trips silently into an infinite `Decimal`
    that no `<= 0` guard rejects, so `budget_usd: Infinity` yields `size_usd=Decimal('Infinity')`
    without anything raising at all.
    """
    assert json.dumps({"budget_usd": str(Decimal("Infinity"))}) == '{"budget_usd": "Infinity"}'
    json.loads(json.dumps({"budget_usd": str(Decimal("Infinity"))}))  # valid JSON, no raise

    decimal_param = _add(
        tmp_path, valid_config_path,
        "--kind", "dca", "--product", "BTC-USD", "--params", '{"budget_usd": Infinity}',
    )
    float_param = _add(
        tmp_path, valid_config_path,
        "--kind", "turtle_breakout", "--product", "BTC-USD",
        "--params", '{"volume_mult": 1e400}',
    )

    assert "not valid JSON" not in decimal_param.output, (
        "the stored value is the string \"Infinity\", which IS valid JSON -- claiming otherwise "
        "for the very example the refusal cites is simply false"
    )
    assert "Decimal" in decimal_param.output
    assert "not valid JSON" in float_param.output, (
        "a float param really does store a bare `Infinity` token"
    )


def test_a_null_is_refused_rather_than_constructing_a_rule_around_it(
    tmp_path, valid_config_path
):
    """No rule param has a `None` default, and the coercion boundary deliberately passes `None`
    through untouched -- so `{"oversold": null}` reaches a validation-free dataclass intact and
    dies comparing a float to `None`, at backtest time.
    """
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "rsi_meanrev", "--product", "BTC-USD", "--params", '{"oversold": null}',
    )

    assert result.exit_code != 0
    assert "oversold" in result.output
    assert repo.get_rules() == []


def test_a_json_container_is_refused_where_the_rule_wants_a_single_value(
    tmp_path, valid_config_path
):
    """The same param and the same failure as `{"oversold": "10.0"}` and `{"oversold": null}`,
    arriving in the one JSON shape neither of those checks looks at. `[1, 2]` for a float field
    constructs, stores, rebuilds, and then raises `TypeError: '<' not supported between
    instances of 'float' and 'list'` inside `detect()` -- reproduced end-to-end: `rules add
    --kind rsi_meanrev --params '{"oversold": [1,2]}'` printed `added rule 28`, and `rules
    backtest 28` died. A JSON object is the same hole wearing a different bracket.
    """
    repo = _repo(tmp_path)

    cases = [
        ("rsi_meanrev", '{"oversold": [1, 2]}', "oversold"),
        ("rsi_meanrev", '{"oversold": {"a": 1}}', "oversold"),
        ("rsi_meanrev", '{"require_divergence": [true]}', "require_divergence"),
        ("turtle_breakout", '{"adx_threshold": []}', "adx_threshold"),
    ]
    for kind, params, name in cases:
        result = _add(
            tmp_path, valid_config_path, "--kind", kind, "--product", "BTC-USD",
            "--params", params,
        )
        assert result.exit_code != 0, (kind, params, result.output)
        assert name in result.output, params
    assert repo.get_rules() == []


def test_a_value_outside_a_params_declared_choices_is_refused(tmp_path, valid_config_path):
    """`stop_method`/`target_method` are declared `Literal["fixed", "atr"]` -- the rule's own
    statement of what it accepts. `RsiMeanReversion` does enforce them, but in
    `_compute_stop`/`_compute_target`, i.e. at `detect()` time: `"banana"` constructs, stores,
    rebuilds and then raises `ValueError: unknown stop_method: 'banana'` mid-backtest. The
    declared choices are named in the refusal so the operator can see the spelling they meant.
    """
    repo = _repo(tmp_path)

    stop = _add(
        tmp_path, valid_config_path,
        "--kind", "rsi_meanrev", "--product", "BTC-USD", "--params", '{"stop_method": "banana"}',
    )
    target = _add(
        tmp_path, valid_config_path,
        "--kind", "rsi_meanrev", "--product", "BTC-USD",
        "--params", '{"target_method": "banana"}',
    )

    assert stop.exit_code != 0
    assert "stop_method" in stop.output
    assert "atr" in stop.output and "fixed" in stop.output, "the choices must be named"
    assert target.exit_code != 0
    assert "nearest_resistance" in target.output and "fixed_rr" in target.output
    assert repo.get_rules() == []


def test_a_typod_choice_is_refused_rather_than_backtesting_a_different_branch(
    tmp_path, valid_config_path
):
    """`PullbackContinuation` validates NOTHING here: an unknown `entry_zone` picks the
    `ema_band` branch by fallthrough and an unknown `stop_method` picks `atr`, both silently.
    Measured on real candles: `entry_zone="banana"` produces 7 trades, byte-identical to
    `ema_band`, where the `ema_touch` default gives 13; `stop_method="banana"` gives 15,
    identical to `atr`. An operator who fat-fingers `ema_touch` is handed another rule's numbers
    with no warning -- the same failure as the `granularity` param the row cannot carry.
    """
    repo = _repo(tmp_path)

    zone = _add(
        tmp_path, valid_config_path,
        "--kind", "pullback_continuation", "--product", "BTC-USD",
        "--params", '{"entry_zone": "banana"}',
    )
    stop = _add(
        tmp_path, valid_config_path,
        "--kind", "pullback_continuation", "--product", "BTC-USD",
        "--params", '{"stop_method": "banana"}',
    )

    assert zone.exit_code != 0
    assert "ema_touch" in zone.output and "ema_band" in zone.output
    assert stop.exit_code != 0
    assert "stop_method" in stop.output
    assert repo.get_rules() == []


def test_every_choice_a_rule_declares_is_accepted(tmp_path, valid_config_path):
    """The choice check must be a guard, not an obstacle: every value the rule's own `Literal`
    names has to go through, or a legitimate parameter sweep across `target_method` is blocked
    by the very check meant to protect it. Read off the rule, so a kind that gains a branch
    needs no change here or in the command.
    """
    repo = _repo(tmp_path)
    kinds = {"pullback_continuation", "rsi_meanrev"}
    checked = 0

    for kind in kinds:
        signature = inspect.signature(RULE_REGISTRY[kind]).parameters
        for param, choices in _declared_choices(RULE_REGISTRY[kind]).items():
            is_sequence = isinstance(signature[param].default, tuple)
            for choice in choices:
                result = _add(
                    tmp_path, valid_config_path, "--kind", kind, "--product", "BTC-USD",
                    "--params", json.dumps({param: [choice] if is_sequence else choice}),
                )
                assert result.exit_code == 0, (kind, param, choice, result.output)
                checked += 1

    assert checked >= 12, "all five Literal params of the two kinds must have been swept"
    assert len(repo.get_rules()) == checked


def test_an_unknown_signal_pattern_is_refused_like_an_empty_pattern_list(
    tmp_path, valid_config_path
):
    """`signal_patterns: []` is already refused because "it never signals and reads as a rule
    that simply does not work". `["nonexistent"]` produces exactly that -- `_match_signal_pattern`
    compares the name against seven literals, matches none, and returns `None` on every bar
    forever -- so it has to be refused for the same reason. The accepted names come from the
    rule's own `SignalPattern` declaration, not a copy kept here.
    """
    repo = _repo(tmp_path)

    empty = _add(
        tmp_path, valid_config_path,
        "--kind", "pullback_continuation", "--product", "BTC-USD",
        "--params", '{"signal_patterns": []}',
    )
    unknown = _add(
        tmp_path, valid_config_path,
        "--kind", "pullback_continuation", "--product", "BTC-USD",
        "--params", '{"signal_patterns": ["pin_bar", "nonexistent"]}',
    )

    assert empty.exit_code != 0
    assert unknown.exit_code != 0
    assert "nonexistent" in unknown.output
    assert "hammer" in unknown.output, "the names it could have meant are listed"
    assert repo.get_rules() == []


def test_a_whole_number_is_still_fine_for_a_param_whose_default_is_a_float(
    tmp_path, valid_config_path
):
    """`adx_threshold=25` for a `25.0` default is not a mistake -- Python treats them alike --
    and refusing it would make the check an obstacle instead of a guard.
    """
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "turtle_breakout", "--product", "BTC-USD",
        "--params", '{"adx_threshold": 30, "volume_mult": 2}',
    )

    assert result.exit_code == 0, result.output
    assert _build_rule(repo.get_rules()[0]).params["adx_threshold"] == 30


def test_a_number_is_refused_for_a_param_the_rule_declares_as_a_string(
    tmp_path, valid_config_path
):
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "rsi_meanrev", "--product", "BTC-USD", "--params", '{"stop_method": 5}',
    )

    assert result.exit_code != 0
    assert "stop_method" in result.output
    assert repo.get_rules() == []


def test_the_params_that_legitimately_take_strings_are_not_refused(tmp_path, valid_config_path):
    """The type check must not fire on the params the coercion boundary exists for: a `Decimal`
    field and a `Granularity` field are BOTH written as JSON strings, correctly.
    """
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "rsi_meanrev", "--product", "BTC-USD",
        "--params", '{"atr_mult": "2.5", "timeframe": "ONE_DAY", "stop_method": "fixed"}',
    )

    assert result.exit_code == 0, result.output
    rule = _build_rule(repo.get_rules()[0])
    assert rule.params["atr_mult"] == Decimal("2.5")
    assert rule.params["timeframe"] == "ONE_DAY"


def test_a_param_the_row_cannot_carry_is_refused_rather_than_silently_dropped(
    tmp_path, valid_config_path
):
    """`PullbackContinuation(granularity=...)` constructs fine and `describe()["params"]` does
    NOT carry it -- so the row would rebuild at the DEFAULT ONE_HOUR, and the operator would be
    told their ONE_DAY rule was added and then read backtest numbers for a different rule than
    the one they asked for. Accepting a param the round trip loses is worse than refusing it.
    """
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "pullback_continuation", "--product", "BTC-USD",
        "--params", '{"granularity": "ONE_DAY"}',
    )

    assert result.exit_code != 0
    assert "granularity" in result.output
    assert repo.get_rules() == []


def test_a_param_that_the_row_does_carry_is_still_accepted(tmp_path, valid_config_path):
    """The same rule kind, via a param `describe()` does persist -- the check above must not
    turn into a blanket refusal of `pullback_continuation`.
    """
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "pullback_continuation", "--product", "BTC-USD",
        "--params", '{"ema_periods": [10, 20, 50], "buffer_ticks": "0.05"}',
    )

    assert result.exit_code == 0, result.output
    rule = _build_rule(repo.get_rules()[0])
    assert rule.params["ema_periods"] == (10, 20, 50), "a JSON list rebuilds as the tuple"
    assert rule.params["buffer_ticks"] == Decimal("0.05")


def test_an_unknown_kind_is_refused_naming_the_kinds_that_exist(tmp_path, valid_config_path):
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "turtle_breakouts", "--product", "BTC-USD", "--params", "{}",
    )

    assert result.exit_code != 0
    for kind in RULE_REGISTRY:
        assert kind in result.output, "the refusal must list the registry's kinds"
    assert repo.get_rules() == []


def test_malformed_params_json_is_refused(tmp_path, valid_config_path):
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "dca", "--product", "BTC-USD", "--params", "cadence_days=7",
    )

    assert result.exit_code != 0
    assert "JSON" in result.output
    assert repo.get_rules() == []


def test_an_explicitly_empty_params_is_refused_not_read_as_the_defaults(
    tmp_path, valid_config_path
):
    """"Flag absent" and "flag given but empty" are different intentions and must not collapse
    into the same defaults row. The empty string is what a shell hands over when the proposal
    plumbing misfires -- `--params "$(jq -c .params proposal.json)"` yields `""` when the key
    is missing or jq errors -- and the silent-default outcome is the worst one available: `added
    rule 32` is printed, the operator backtests it, and the numbers they read are the stock
    rule's, not their proposal's. `'{}'` remains the explicit way to ask for the defaults.
    """
    repo = _repo(tmp_path)

    for empty in ("", "   "):
        result = _add(
            tmp_path, valid_config_path,
            "--kind", "dca", "--product", "BTC-USD", "--params", empty,
        )
        assert result.exit_code != 0, repr(empty)
        assert repo.get_rules() == [], "an empty --params must never write a defaults row"

    explicit = _add(
        tmp_path, valid_config_path,
        "--kind", "dca", "--product", "BTC-USD", "--params", "{}",
    )
    omitted = _add(tmp_path, valid_config_path, "--kind", "dca", "--product", "ETH-USD")

    assert explicit.exit_code == 0, explicit.output
    assert omitted.exit_code == 0, omitted.output
    assert len(repo.get_rules()) == 2


def test_params_that_are_not_a_json_object_are_refused(tmp_path, valid_config_path):
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "dca", "--product", "BTC-USD", "--params", "[7]",
    )

    assert result.exit_code != 0
    assert repo.get_rules() == []


def test_a_product_id_inside_params_that_disagrees_with_product_is_refused(
    tmp_path, valid_config_path
):
    """Two sources of truth for the product would make the printed id and the stored id differ
    -- and the stored one is the one that trades.
    """
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "dca", "--product", "BTC-USD", "--params", '{"product_id": "ETH-USD"}',
    )

    assert result.exit_code != 0
    assert "ETH-USD" in result.output
    assert repo.get_rules() == []


# -- product admission: rails 18/19, asked at the keyboard (as in `rules seed`) -------------


def test_a_futures_contract_is_refused_and_named(tmp_path, valid_config_path):
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "dca", "--product", "XLM-28AUG26-CDE", "--params", "{}",
    )

    assert result.exit_code != 0
    assert "XLM-28AUG26-CDE" in result.output
    assert repo.get_rules() == []


def test_a_pair_settling_outside_the_settlement_currencies_is_refused(
    tmp_path, valid_config_path
):
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "dca", "--product", "BTC-EUR", "--params", "{}",
    )

    assert result.exit_code != 0
    assert "settles in EUR" in result.output
    assert repo.get_rules() == []


def test_a_lowercase_id_gets_a_hint_and_is_never_silently_uppercased(
    tmp_path, valid_config_path
):
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "dca", "--product", "btc-USD", "--params", "{}",
    )

    assert result.exit_code != 0
    assert "did you mean BTC-USD" in result.output
    assert repo.get_rules() == []


def test_more_than_one_product_is_refused(tmp_path, valid_config_path):
    """One invocation inserts ONE row, and the printed `rules backtest <id>` names one id."""
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "dca", "--product", "BTC-USD,ETH-USD", "--params", "{}",
    )

    assert result.exit_code != 0
    assert repo.get_rules() == []


# -- duplicates and the allowlist: reported, never refused ----------------------------------


def test_a_second_rule_for_the_same_kind_and_product_is_allowed_and_reported(
    tmp_path, valid_config_path
):
    """Comparing two parameter sets for one (kind, product) is the entire use case, so this
    does NOT take `rules seed`'s idempotency skip -- but the operator must be told they now
    have several, or the extra rows are a surprise at backtest time.
    """
    repo = _repo(tmp_path)

    first = _add(
        tmp_path, valid_config_path,
        "--kind", "turtle_breakout", "--product", "BTC-USD",
        "--params", '{"entry_lookback": 20}',
    )
    assert first.exit_code == 0, first.output
    first_id = repo.get_rules()[0]["id"]

    second = _add(
        tmp_path, valid_config_path,
        "--kind", "turtle_breakout", "--product", "BTC-USD",
        "--params", '{"entry_lookback": 55}',
    )

    assert second.exit_code == 0, second.output
    rows = repo.get_rules()
    assert len(rows) == 2
    assert {r["params"]["entry_lookback"] for r in rows} == {20, 55}
    assert str(first_id) in second.output
    assert "candidate" in second.output


def test_the_report_shows_how_an_existing_rule_DIFFERS_not_its_whole_params(
    tmp_path, valid_config_path
):
    """Two rows for one (kind, product) differ in the one or two params under test and agree on
    the other eleven. Printing both in full makes the operator diff them by eye at exactly the
    moment they must pick the right id; printing only the difference answers the question they
    have. (`keel rules list` is still there for the full picture.)
    """
    repo = _repo(tmp_path)
    first = _add(
        tmp_path, valid_config_path,
        "--kind", "turtle_breakout", "--product", "BTC-USD",
        "--params", '{"entry_lookback": 20}',
    )
    assert first.exit_code == 0, first.output
    first_id = repo.get_rules()[0]["id"]

    second = _add(
        tmp_path, valid_config_path,
        "--kind", "turtle_breakout", "--product", "BTC-USD",
        "--params", '{"entry_lookback": 55}',
    )

    assert second.exit_code == 0, second.output
    report = [ln for ln in second.output.splitlines() if f"[{first_id}]" in ln]
    assert len(report) == 1, second.output
    assert "entry_lookback=20" in report[0], "the param that differs must be named"
    assert "adx_period" not in report[0], "params identical to the new row are noise"


def test_a_param_the_older_row_predates_is_not_reported_as_a_parameter_difference(
    tmp_path, valid_config_path
):
    """A row written before `turtle_breakout` grew `s1_filter`/`min_volume_filter`/
    `volume_ma_period`/`volume_mult` has no such keys, and a plain key-union diff calls all four
    a difference. Against a real pre-existing row that read:

        [10] status=paper entry_lookback=40, min_volume_filter='<absent>',
             s1_filter='<absent>', volume_ma_period='<absent>', volume_mult='<absent>'

    -- four of five "differences" are schema drift, and they bury `entry_lookback`, the one
    parameter the operator is actually choosing between. The absent keys still get said (the
    two rows are genuinely not comparable on them), but as their own, counted note.
    """
    repo = _repo(tmp_path)
    old_id = repo.insert_rule(
        "turtle_breakout",
        {
            "product_id": "BTC-USD",
            "entry_lookback": 40,
            "exit_lookback": 20,
            "adx_period": 14,
            "adx_threshold": 25.0,
            "atr_period": 20,
            "atr_stop_mult": "2",
            "use_macd_confirm": False,
            "target_rr": "6",
        },
        status="paper",
    )

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "turtle_breakout", "--product", "BTC-USD",
        "--params", '{"entry_lookback": 55}',
    )

    assert result.exit_code == 0, result.output
    report = [ln for ln in result.output.splitlines() if f"[{old_id}]" in ln]
    assert len(report) == 1, result.output
    assert "entry_lookback=40" in report[0], "the real difference must still be named"
    assert "'<absent>'" not in report[0], (
        "a param that row predates is not a parameter difference and must not read as one"
    )
    for grown in ("s1_filter", "min_volume_filter", "volume_ma_period", "volume_mult"):
        assert grown in report[0], "the incomparable params are still disclosed, just apart"
    assert report[0].index("entry_lookback=40") < report[0].index("s1_filter"), (
        "the parameter under test comes first; the schema note follows it"
    )


def test_an_existing_rule_with_identical_params_is_reported_as_identical(
    tmp_path, valid_config_path
):
    """The likeliest way to end up with two rows by accident is adding the same thing twice;
    an empty difference list would read as "nothing to see here".
    """
    repo = _repo(tmp_path)
    args = ("--kind", "dca", "--product", "BTC-USD", "--params", '{"cadence_days": 7}')
    first = _add(tmp_path, valid_config_path, *args)
    assert first.exit_code == 0, first.output
    first_id = repo.get_rules()[0]["id"]

    second = _add(tmp_path, valid_config_path, *args)

    assert second.exit_code == 0, second.output
    report = [ln for ln in second.output.splitlines() if f"[{first_id}]" in ln]
    assert len(report) == 1, second.output
    assert "identical params" in report[0]


def test_existing_rules_are_reported_with_their_status_whatever_it_is(
    tmp_path, valid_config_path
):
    """A `live` sibling is the one an operator most needs to hear about before backtesting."""
    repo = _repo(tmp_path)
    live_id = repo.insert_rule("dca", {"product_id": "BTC-USD"}, status="live")

    result = _add(tmp_path, valid_config_path, "--kind", "dca", "--product", "BTC-USD")

    assert result.exit_code == 0, result.output
    assert f"{live_id}" in result.output
    assert "live" in result.output


def test_a_product_outside_the_allowlist_is_added_and_flagged_not_refused(
    tmp_path, valid_config_path
):
    """Backtesting SOL before deciding whether to admit it is the intended workflow, and a
    `candidate` rule never trades regardless -- rail 1 stands between it and any order.
    """
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "dca", "--product", "SOL-USD", "--params", '{"cadence_days": 7}',
    )

    assert result.exit_code == 0, result.output
    rows = repo.get_rules()
    assert len(rows) == 1
    assert rows[0]["params"]["product_id"] == "SOL-USD"
    assert "allowlist" in result.output


def test_an_allowlisted_product_draws_no_allowlist_warning(tmp_path, valid_config_path):
    repo = _repo(tmp_path)

    result = _add(tmp_path, valid_config_path, "--kind", "dca", "--product", "BTC-USD")

    assert result.exit_code == 0, result.output
    assert len(repo.get_rules()) == 1
    assert "allowlist" not in result.output


# -- the operator's next step ---------------------------------------------------------------


def test_it_prints_the_exact_next_command(tmp_path, valid_config_path):
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "dca", "--product", "BTC-USD", "--params", '{"cadence_days": 7}',
    )

    assert result.exit_code == 0, result.output
    rule_id = repo.get_rules()[0]["id"]
    assert f"keel rules backtest {rule_id}" in result.output


def test_the_added_rule_actually_backtests(tmp_path, valid_config_path):
    """End to end, through the command the output tells the operator to run: the id printed
    here is accepted by `rules backtest`, which rebuilds the rule and runs it over candles.
    """
    add = _add(
        tmp_path, valid_config_path,
        "--kind", "dca", "--product", "BTC-USD", "--params", '{"cadence_days": 3}',
    )
    assert add.exit_code == 0, add.output
    rule_id = _repo(tmp_path).get_rules()[0]["id"]

    result = CliRunner().invoke(
        cli,
        [
            "--db", str(tmp_path / "t.db"),
            "--config", str(valid_config_path),
            "rules", "backtest", str(rule_id),
            "--granularity", "ONE_DAY",
        ],
    )

    assert result.exit_code == 0, result.output
    assert f"rule {rule_id} (dca)" in result.output


# -- read-only w.r.t. the exchange -----------------------------------------------------------


def test_it_never_constructs_a_broker(tmp_path, valid_config_path, monkeypatch):
    def _boom(config):
        raise AssertionError("`rules add` must never touch the network")

    monkeypatch.setattr(_common, "_build_broker", _boom)
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "dca", "--product", "BTC-USD", "--params", '{"cadence_days": 7}',
    )

    assert result.exit_code == 0, result.output
    assert len(repo.get_rules()) == 1, "the row is written without a broker ever existing"


def test_the_printed_params_are_the_stored_params(tmp_path, valid_config_path):
    """The operator's record of what was added has to be the row, not the input echoed back."""
    repo = _repo(tmp_path)

    result = _add(
        tmp_path, valid_config_path,
        "--kind", "turtle_breakout", "--product", "BTC-USD",
        "--params", '{"entry_lookback": 55}',
    )

    assert result.exit_code == 0, result.output
    stored = repo.get_rules()[0]["params"]
    assert json.dumps(stored, sort_keys=True) in result.output
