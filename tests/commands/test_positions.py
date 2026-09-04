"""The positions report -- issue #701.

`positions` stores one row per TRANCHE, and until now the only projection of it was the small
table inside `keel status`: id, product, rule, qty, entry price, bracket. `web/payload.py`'s
`_position_payload` says in its own docstring why it carries no P&L -- "`OpenPositionStatus`
carries neither, so emitting one would mean this layer multiplied `qty` by `entry_price`... the
fix, if it is wanted, is upstream". This module is that fix, upstream.

The property the whole thing rests on is that the MARK is the one the rails used. `agent`
values equity from `repo.get_candles(product, finest)[-1].close`; so does this. A positions page
showing a different current price from the one that moved the drawdown scalars would be two
answers to "what is this worth", and the page would be the wrong one.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from keel_core.types import Candle, Granularity

from keel import agent as agent_mod
from keel.commands.positions import gather_positions
from keel.config import Config
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from tests.conftest import VALID_CONFIG_YAML

NOW_TS = 1_800_000_000
DAY = 86_400
PRODUCT = "BTC-USD"


@pytest.fixture()
def repo(tmp_path: Path) -> Repository:
    conn = connect(str(tmp_path / "keel.db"))
    migrate(conn)
    return Repository(conn)


def _config(tmp_path: Path) -> Config:
    from keel.config import load_config

    path = tmp_path / "config.yaml"
    path.write_text(VALID_CONFIG_YAML, encoding="utf-8")
    return load_config(str(path))


def _candle(ts: int, close: str) -> Candle:
    price = Decimal(close)
    return Candle(ts=ts, open=price, high=price, low=price, close=price, volume=Decimal("1"))


def _open_tranche(repo: Repository, **overrides: Any) -> int:
    row: dict[str, Any] = {
        "product_id": PRODUCT,
        "rule_name": "turtle_breakout",
        "opened_at": NOW_TS - DAY,
        "qty": Decimal("2"),
        "entry_fill": Decimal("100"),
        "entry_fee": Decimal("1.20"),
        "initial_stop": Decimal("90"),
    }
    row.update(overrides)
    return repo.open_position(**row)


#: The FINEST series `tests/conftest.py::VALID_CONFIG_YAML` configures, and therefore the one
#: the agent marks against. Not hardcoded out of habit: a helper that wrote any other series
#: would leave every mark `None` and every assertion below would fail for the wrong reason.
FINEST = Granularity.FIFTEEN_MINUTE


def _mark(repo: Repository, close: str, granularity: Granularity = FINEST) -> None:
    repo.upsert_candles(PRODUCT, granularity, [_candle(NOW_TS - 900, close)])


# -- the mark, and everything derived from it ---------------------------------------------------


def test_a_tranche_is_marked_at_the_latest_close(repo: Repository, tmp_path: Path) -> None:
    _open_tranche(repo)
    _mark(repo, "150")

    row = gather_positions(repo, _config(tmp_path), now_ts=NOW_TS).rows[0]

    assert row.mark == Decimal("150")
    assert row.market_value == Decimal("2") * Decimal("150")
    assert row.unrealized_pnl == Decimal("2") * (Decimal("150") - Decimal("100"))


def test_an_unmarked_product_reports_no_mark_rather_than_a_zero(
    repo: Repository, tmp_path: Path
) -> None:
    """No candle is "not observed", never "worth nothing". A zero here would render a held
    position as a total loss, which is the single most alarming thing a positions page could say
    and it would be saying it about missing data."""
    _open_tranche(repo)

    row = gather_positions(repo, _config(tmp_path), now_ts=NOW_TS).rows[0]

    assert row.mark is None
    assert row.market_value is None
    assert row.unrealized_pnl is None


def test_unrealized_is_negative_while_the_position_is_under_water(
    repo: Repository, tmp_path: Path
) -> None:
    _open_tranche(repo)
    _mark(repo, "80")

    row = gather_positions(repo, _config(tmp_path), now_ts=NOW_TS).rows[0]

    assert row.unrealized_pnl == Decimal("-40")


def test_the_mark_is_the_one_the_rails_valued_the_account_at(
    repo: Repository, tmp_path: Path
) -> None:
    """THE acceptance criterion. `agent._mark_to_market_parts` values a holding at
    `get_candles(product, finest)[-1].close`; a positions page quoting a different current price
    would disagree with the equity that moved the drawdown scalars.

    Two granularities are stored with DIFFERENT closes, so a reader of the wrong series fails.
    """
    _open_tranche(repo)
    repo.upsert_candles(PRODUCT, Granularity.ONE_DAY, [_candle(NOW_TS - DAY, "111")])
    repo.upsert_candles(PRODUCT, Granularity.ONE_HOUR, [_candle(NOW_TS - 3600, "222")])
    repo.upsert_candles(PRODUCT, FINEST, [_candle(NOW_TS - 900, "333")])

    row = gather_positions(repo, _config(tmp_path), now_ts=NOW_TS).rows[0]

    assert row.mark == Decimal("333"), "the FINEST configured series is the agent's mark"


def test_the_entry_fee_is_carried(repo: Repository, tmp_path: Path) -> None:
    """What the tranche cost to open, beside what it is worth. `keel status` never showed it and
    the fee is the half of a round trip a reader most often forgets."""
    _open_tranche(repo, entry_fee=Decimal("1.20"))

    assert gather_positions(repo, _config(tmp_path), now_ts=NOW_TS).rows[0].entry_fee == Decimal(
        "1.20"
    )


# -- the stop -----------------------------------------------------------------------------------


def test_the_stop_distance_is_reported_as_a_price_and_a_fraction(
    repo: Repository, tmp_path: Path
) -> None:
    """How far this tranche is from the protection it was sized against. Both forms, because
    "$60 away" and "40% away" answer different questions and neither is derivable in the
    browser."""
    _open_tranche(repo, initial_stop=Decimal("90"))
    _mark(repo, "150")

    row = gather_positions(repo, _config(tmp_path), now_ts=NOW_TS).rows[0]

    assert row.stop_distance == Decimal("60")
    assert row.stop_distance_pct == Decimal("60") / Decimal("150")


def test_a_tranche_with_no_recorded_stop_reports_no_distance(
    repo: Repository, tmp_path: Path
) -> None:
    """`initial_stop` is NULL for a DCA leg or a pre-v12 tranche -- "not on this row", not "no
    stop". A distance computed against a substituted zero would read as a position 100% clear of
    its stop, which is the most reassuring possible rendering of missing data."""
    _open_tranche(repo, initial_stop=None)
    _mark(repo, "150")

    row = gather_positions(repo, _config(tmp_path), now_ts=NOW_TS).rows[0]

    assert row.initial_stop is None
    assert row.stop_distance is None
    assert row.stop_distance_pct is None


def test_no_mark_means_no_stop_distance_either(repo: Repository, tmp_path: Path) -> None:
    """The distance is measured from the CURRENT price. With no mark there is nothing to measure
    from, and measuring from the entry instead would quietly answer a different question."""
    _open_tranche(repo, initial_stop=Decimal("90"))

    row = gather_positions(repo, _config(tmp_path), now_ts=NOW_TS).rows[0]

    assert row.stop_distance is None


def test_a_position_below_its_stop_reports_a_negative_distance(
    repo: Repository, tmp_path: Path
) -> None:
    """Signed, not absolute. A tranche trading THROUGH its stop is the state an operator most
    needs to see, and an absolute distance would render it identically to one safely above."""
    _open_tranche(repo, initial_stop=Decimal("90"))
    _mark(repo, "85")

    assert gather_positions(repo, _config(tmp_path), now_ts=NOW_TS).rows[0].stop_distance == (
        Decimal("-5")
    )


# -- partial exits --------------------------------------------------------------------------------


def test_realized_legs_are_reported_beside_the_running_position(
    repo: Repository, tmp_path: Path
) -> None:
    """A scaled-out tranche is one trade with legs already booked. `qty` is what is STILL held,
    so without the realized side the row understates what the tranche has done."""
    position_id = _open_tranche(repo, qty=Decimal("2"))
    repo.reduce_position(
        position_id,
        remaining_qty=Decimal("1"),
        realized_qty=Decimal("1"),
        realized_proceeds=Decimal("140"),
        realized_fees=Decimal("0.50"),
    )
    _mark(repo, "150")

    row = gather_positions(repo, _config(tmp_path), now_ts=NOW_TS).rows[0]

    assert row.qty == Decimal("1"), "qty is what is still held"
    assert row.realized_qty == Decimal("1")
    assert row.realized_proceeds == Decimal("140")
    assert row.realized_fees == Decimal("0.50")
    # And the unrealized side is measured on the REMAINING quantity only.
    assert row.unrealized_pnl == Decimal("1") * (Decimal("150") - Decimal("100"))


def test_a_tranche_that_never_partially_exited_reports_zeros_not_absences(
    repo: Repository, tmp_path: Path
) -> None:
    """The repository's own convention (`_position_row_to_dict`): there is no difference between
    "never partially exited" and "has realized nothing", so zero invents nothing here."""
    _open_tranche(repo)

    row = gather_positions(repo, _config(tmp_path), now_ts=NOW_TS).rows[0]

    assert row.realized_qty == Decimal("0")
    assert row.realized_proceeds == Decimal("0")


# -- what the report covers ------------------------------------------------------------------------


def test_only_open_tranches_are_reported(repo: Repository, tmp_path: Path) -> None:
    open_id = _open_tranche(repo)
    closed_id = _open_tranche(repo)
    repo.close_position(closed_id, closed_at=NOW_TS)

    rows = gather_positions(repo, _config(tmp_path), now_ts=NOW_TS).rows

    assert [row.id for row in rows] == [open_id]


def test_an_empty_book_is_a_real_answer(repo: Repository, tmp_path: Path) -> None:
    report = gather_positions(repo, _config(tmp_path), now_ts=NOW_TS)

    assert report.rows == ()
    assert report.open_count == 0


# -- the acceptance criterion: one mark, two readers ---------------------------------------------


def test_the_unrealized_total_reconciles_with_the_agents_own_equity_read(
    repo: Repository, tmp_path: Path
) -> None:
    """#701's acceptance criterion, checked across the module boundary rather than asserted.

    The two readers do NOT share a source for quantity: `agent._mark_to_market_parts` counts
    inventory from the FILLED ORDERS log (never from `positions`, deliberately -- see its own
    note on the phantom-drawdown bug that caused), while this report reads the `positions`
    tranche ledger. They share only the MARK. So this test is what says the two ledgers agree
    about what is held, and it fails the day they drift -- which is the failure that would put a
    different unrealized figure on the page from the one inside rail 11's equity.
    """
    from tests.test_agent import FakeBroker, _seed_open_position

    _seed_open_position(
        repo,
        PRODUCT,
        Decimal("2"),
        Decimal("100"),
        ts=NOW_TS - DAY,
        rule_name="turtle_breakout",
    )
    _mark(repo, "150")

    rows = gather_positions(repo, _config(tmp_path), now_ts=NOW_TS).rows
    page_total = sum((row.unrealized_pnl or Decimal("0") for row in rows), Decimal("0"))

    parts = agent_mod._mark_to_market_parts(
        repo, FakeBroker(), [PRODUCT], {PRODUCT: Decimal("150")}, "USD"
    )

    assert parts is not None
    assert page_total == parts.unrealized == Decimal("100")


# -- the chip must ask the question the AGENT asks (#701 review finding) ------------------------
#
# `_entry_gate_granularity` gates a rule on the timeframe the rule DECLARES, and falls back to
# the coarsest configured series only when it declares none. A chip that always asked about the
# coarsest would be reporting the fallback as though it were the answer -- right for a daily
# deployment, wrong for any rule seeded on a finer timeframe, and confidently worded either way.


def test_the_gate_verdict_follows_the_rules_own_declared_timeframe(
    repo: Repository, tmp_path: Path
) -> None:
    """A rule seeded on ONE_HOUR is gated on ONE_HOUR. Here the DAILY series is present and the
    HOURLY one is absent, so the two possible readings disagree: the coarsest series says
    something other than "missing", and the rule's own series says "missing"."""
    repo.insert_rule("turtle_breakout", {"product_id": PRODUCT, "granularity": "ONE_HOUR"})
    _open_tranche(repo, rule_name="turtle_breakout")
    repo.upsert_candles(PRODUCT, Granularity.ONE_DAY, [_candle(NOW_TS - DAY, "100")])

    row = gather_positions(repo, _config(tmp_path), now_ts=NOW_TS).rows[0]

    assert row.ready is False
    assert row.ready_reason == "missing", "the ONE_HOUR series this rule trades on has no bars"


def test_a_rule_that_declares_nothing_falls_back_to_the_coarsest_series(
    repo: Repository, tmp_path: Path
) -> None:
    """`_entry_gate_granularity`'s own fallback, and the reason for it: a rule silent about its
    timeframe is most likely keying off the coarsest series (DCA reads the daily bar directly),
    so gating on the finest would miss a weeks-stale daily bar entirely."""
    repo.insert_rule("dca", {"product_id": PRODUCT})
    _open_tranche(repo, rule_name="dca")

    row = gather_positions(repo, _config(tmp_path), now_ts=NOW_TS).rows[0]

    assert row.ready is False
    assert row.ready_reason == "missing"


def test_an_unmatched_rule_name_degrades_to_the_coarsest_rather_than_crashing(
    repo: Repository, tmp_path: Path
) -> None:
    """`positions.rule_name` is the rule's `name`, which is a separate constructor argument from
    the `kind` the rules table is keyed on -- they coincide by default and are not guaranteed to.
    An unmatched name must leave the chip on the safe fallback, not take the page down."""
    _open_tranche(repo, rule_name="a_rule_no_longer_in_the_book")

    row = gather_positions(repo, _config(tmp_path), now_ts=NOW_TS).rows[0]

    assert row.ready_reason == "missing"


def test_two_tranches_of_one_product_on_different_timeframes_get_their_own_verdicts(
    repo: Repository, tmp_path: Path
) -> None:
    """The per-product cache the first version used cannot express this: one product, two rules,
    two gate granularities, two answers. Caching by product alone would give the second tranche
    the first one's verdict."""
    repo.insert_rule("turtle_breakout", {"product_id": PRODUCT, "granularity": "ONE_DAY"})
    repo.insert_rule("pullback_continuation", {"product_id": PRODUCT, "granularity": "ONE_HOUR"})
    _open_tranche(repo, rule_name="turtle_breakout")
    _open_tranche(repo, rule_name="pullback_continuation")
    # Only the DAILY series exists, and it is at its expected bar.
    repo.upsert_candles(PRODUCT, Granularity.ONE_DAY, [_candle(NOW_TS - DAY, "100")])

    rows = gather_positions(repo, _config(tmp_path), now_ts=NOW_TS).rows
    by_rule = {row.rule_name: row for row in rows}

    assert by_rule["pullback_continuation"].ready_reason == "missing"
    assert by_rule["turtle_breakout"].ready_reason != "missing"


def test_the_rules_table_is_read_once_however_many_tranches(
    repo: Repository, tmp_path: Path
) -> None:
    """Resolving a gate granularity per row would be one rules read per tranche. The map is built
    once, like `gather_orders`' rule-name map."""
    repo.insert_rule("turtle_breakout", {"product_id": PRODUCT, "granularity": "ONE_DAY"})
    for _ in range(6):
        _open_tranche(repo, rule_name="turtle_breakout")

    reads = {"n": 0}
    original = repo.get_rules

    def counting(*args: Any, **kwargs: Any) -> Any:
        reads["n"] += 1
        return original(*args, **kwargs)

    repo.get_rules = counting  # type: ignore[method-assign]
    gather_positions(repo, _config(tmp_path), now_ts=NOW_TS)

    assert reads["n"] == 1


# -- the rule lookup is by NAME, and names can collide (#701 review) ----------------------------


def test_two_rules_of_one_kind_on_different_timeframes_keep_their_own_verdicts(
    repo: Repository, tmp_path: Path
) -> None:
    """The map is keyed on what `positions.rule_name` actually holds -- the rule's `name`, a
    constructor argument -- and NOT on `rules.kind`. Two `turtle_breakout` rows on different
    timeframes is a configuration this codebase supports; keyed by kind they collapse to
    whichever row was read last, and one tranche silently inherits the other's granularity."""
    repo.insert_rule(
        "turtle_breakout",
        {"product_id": PRODUCT, "granularity": "ONE_DAY", "name": "turtle_daily"},
    )
    repo.insert_rule(
        "turtle_breakout",
        {"product_id": PRODUCT, "granularity": "ONE_HOUR", "name": "turtle_hourly"},
    )
    _open_tranche(repo, rule_name="turtle_daily")
    _open_tranche(repo, rule_name="turtle_hourly")
    repo.upsert_candles(PRODUCT, Granularity.ONE_DAY, [_candle(NOW_TS - DAY, "100")])

    rows = gather_positions(repo, _config(tmp_path), now_ts=NOW_TS).rows
    by_rule = {row.rule_name: row for row in rows}

    assert by_rule["turtle_hourly"].ready_reason == "missing", "gated on the absent ONE_HOUR"
    assert by_rule["turtle_daily"].ready_reason != "missing", "gated on the present ONE_DAY"


def test_two_rules_sharing_a_name_degrade_to_the_fallback(
    repo: Repository, tmp_path: Path
) -> None:
    """`name` is not unique in the schema. When two rows answer to one name with DIFFERENT gate
    granularities, which one opened a tranche is genuinely unknowable from `rule_name` alone --
    so the chip takes the fallback rather than picking one and stating it with confidence."""
    repo.insert_rule(
        "turtle_breakout", {"product_id": PRODUCT, "granularity": "ONE_DAY", "name": "same"}
    )
    repo.insert_rule(
        "turtle_breakout", {"product_id": PRODUCT, "granularity": "ONE_HOUR", "name": "same"}
    )
    _open_tranche(repo, rule_name="same")
    repo.upsert_candles(PRODUCT, Granularity.ONE_DAY, [_candle(NOW_TS - DAY, "100")])

    row = gather_positions(repo, _config(tmp_path), now_ts=NOW_TS).rows[0]

    # The fallback is the COARSEST series, which is present here -- so "not missing" is the
    # observable consequence of having declined to guess.
    assert row.ready_reason != "missing"


def test_each_rule_is_built_once_however_many_tranches_it_opened(
    repo: Repository, tmp_path: Path, monkeypatch: Any
) -> None:
    """`_build_rule` runs a rule's real constructor, with its validation. Doing that per TRANCHE
    is the same waste as a per-row query, and on a DCA book (one rule, many tranches) it is the
    common case rather than the edge one."""
    repo.insert_rule(
        "turtle_breakout",
        {"product_id": PRODUCT, "granularity": "ONE_DAY", "name": "turtle_breakout"},
    )
    for _ in range(6):
        _open_tranche(repo, rule_name="turtle_breakout")

    builds = {"n": 0}
    original = agent_mod._build_rule

    def counting(row: Any) -> Any:
        builds["n"] += 1
        return original(row)

    monkeypatch.setattr(agent_mod, "_build_rule", counting)
    gather_positions(repo, _config(tmp_path), now_ts=NOW_TS)

    assert builds["n"] == 1, f"built the rule {builds['n']} times for 6 tranches"


def test_the_mark_cache_does_not_leak_between_products(
    repo: Repository, tmp_path: Path
) -> None:
    """A cache keyed carelessly would hand product B product A's price, and every figure derived
    from it would be confidently wrong with nothing in the row to show it."""
    _open_tranche(repo, product_id="BTC-USD")
    _open_tranche(repo, product_id="ETH-USD")
    repo.upsert_candles("BTC-USD", FINEST, [_candle(NOW_TS - 900, "150")])
    repo.upsert_candles("ETH-USD", FINEST, [_candle(NOW_TS - 900, "20")])

    rows = gather_positions(repo, _config(tmp_path), now_ts=NOW_TS).rows
    by_product = {row.product_id: row for row in rows}

    assert by_product["BTC-USD"].mark == Decimal("150")
    assert by_product["ETH-USD"].mark == Decimal("20")


def test_the_products_list_is_in_first_seen_order(repo: Repository, tmp_path: Path) -> None:
    """What a grouped view renders sections from. Sorted or set-ordered, the page would reorder
    itself between reads for no reason a reader could see."""
    _open_tranche(repo, product_id="SOL-USD")
    _open_tranche(repo, product_id="BTC-USD")
    _open_tranche(repo, product_id="SOL-USD")

    assert gather_positions(repo, _config(tmp_path), now_ts=NOW_TS).products == (
        "SOL-USD",
        "BTC-USD",
    )
