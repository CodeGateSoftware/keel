"""Two rules of one kind on one asset must not share a row (#829).

`edge_table` and `accumulation_table` keyed rows `"{rule.name}:{asset}"`, so a second rule of
the same kind on the same asset overwrote the first. On the deployment's paper db that hid a
$50/week BTC DCA behind a $5 one. `group_trades_by_class` looked rules up by the same key, so
both rules then read the SURVIVOR's trades: one rule counted twice in G2's class pool, the
other not at all. A key that is unique already stays exactly as it was.
"""

from __future__ import annotations

from decimal import Decimal

from keel.sim import report
from keel.sim.report import POOLED_KEY, edge_table, group_trades_by_class
from keel.strategy.promotion import promotion_class_of
from keel.strategy.rules.dca import Dca
from tests.sim.test_dca_sleeve import _cadence_days, _market
from tests.sim.test_report import _HOUR, _OneShotRule, _winning_series

_ZERO = Decimal("0")


def _one_shot(product_id: str, trigger_ts: int, rule_id: int | None) -> _OneShotRule:
    rule = _OneShotRule(
        product_id, trigger_ts, entry=Decimal("100"), stop=Decimal("90"), target=Decimal("120")
    )
    rule.rule_id = rule_id
    return rule


def _dca(budget: str, rule_id: int | None) -> Dca:
    rule = Dca("BTC-USD", cadence_days=7, budget_usd=Decimal(budget))
    rule.rule_id = rule_id
    return rule


def _btc() -> dict:
    from keel.types import Granularity

    return {"BTC": {Granularity.ONE_HOUR: _winning_series("BTC")}}


def test_two_dca_rules_on_one_asset_get_a_row_each_at_their_own_budget() -> None:
    """The deployment's shape: rule 19 at $50 and rule 20 at $5, both on BTC."""
    n = len(_cadence_days())

    rows = report.accumulation_table(
        [_dca("50", 19), _dca("5", 20)], _market(), fee_pct=_ZERO, slippage_pct=_ZERO
    )

    assert sorted(rows) == ["dca#19:BTC", "dca#20:BTC"]
    assert (rows["dca#19:BTC"].buys, rows["dca#19:BTC"].cost_usd) == (n, Decimal("50") * n)
    assert (rows["dca#20:BTC"].buys, rows["dca#20:BTC"].cost_usd) == (n, Decimal("5") * n)


def test_two_edge_rules_on_one_asset_get_a_row_each_and_the_pool_counts_both() -> None:
    fires = _one_shot("BTC-USD", _HOUR, 7)
    never = _one_shot("BTC-USD", 99 * _HOUR, 8)

    edge = edge_table([fires, never], _btc(), fee_pct=_ZERO, slippage_pct=_ZERO)

    assert sorted(edge) == [POOLED_KEY, "one_shot#7:BTC", "one_shot#8:BTC"]
    assert edge["one_shot#7:BTC"].n_trades == 1
    assert edge["one_shot#8:BTC"].n_trades == 0
    assert edge[POOLED_KEY].n_trades == 1


def test_the_class_pool_counts_each_colliding_rule_once() -> None:
    """Before #829 both rules read the survivor's (the later rule's, zero-trade) result, so
    G2's class pool held 0 trades while the pooled row held 1."""
    fires = _one_shot("BTC-USD", _HOUR, 7)
    never = _one_shot("BTC-USD", 99 * _HOUR, 8)
    rules = [fires, never]

    edge = edge_table(rules, _btc(), fee_pct=_ZERO, slippage_pct=_ZERO)
    by_class = group_trades_by_class(edge, rules)

    assert by_class[promotion_class_of(fires)].n_trades == 1


def test_a_key_that_is_already_unique_is_unchanged() -> None:
    btc = _one_shot("BTC-USD", _HOUR, 7)
    eth = _one_shot("ETH-USD", _HOUR, 8)
    from keel.types import Granularity

    candles = {
        "BTC": {Granularity.ONE_HOUR: _winning_series("BTC")},
        "ETH": {Granularity.ONE_HOUR: _winning_series("ETH")},
    }

    edge = edge_table([btc, eth], candles, fee_pct=_ZERO, slippage_pct=_ZERO)

    assert sorted(edge) == [POOLED_KEY, "one_shot:BTC", "one_shot:ETH"]


def test_colliding_rules_without_an_id_are_still_told_apart() -> None:
    """A hand-built rule has `rule_id=None`; its position in `rules` disambiguates instead, so
    no row is ever lost for want of a database id."""
    first = _one_shot("BTC-USD", _HOUR, None)
    second = _one_shot("BTC-USD", 99 * _HOUR, None)

    edge = edge_table([first, second], _btc(), fee_pct=_ZERO, slippage_pct=_ZERO)

    assert len([key for key in edge if key != POOLED_KEY]) == 2
    assert edge[POOLED_KEY].n_trades == 1


def test_the_accumulation_section_renders_both_rows() -> None:
    rows = report.accumulation_table(
        [_dca("50", 19), _dca("5", 20)], _market(), fee_pct=_ZERO, slippage_pct=_ZERO
    )

    lines = report._render_accumulation_section(rows)

    data = [line for line in lines if line.startswith("| dca")]
    assert [line.split("|")[1].strip() for line in data] == ["dca#19:BTC", "dca#20:BTC"]
