"""What a fill is assumed to cost, per product -- issue #708, view 4.

The exhibit #686 produced and nothing has published: priced from each asset's own liquidity,
**not one product in the universe reaches the 5bp floor** every experiment document in this
repository prices its fills at. The range runs from BTC at roughly 1.1x the floor to the thin
end pinned against the 183.8bp cap.

**Every number here is an ASSUMPTION and the report says so.** `slippage_for_quote_volume`'s own
docstring is unambiguous -- keel stores no book snapshots and no realised spreads, so liquidity
is proxied by the one statistic it does compute. A page that called these figures "measured"
would be making the claim the model refuses to make.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from keel.commands.slippage import gather_slippage
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.strategy.backtest import (
    SLIPPAGE_CAP_PCT,
    SLIPPAGE_FLOOR_PCT,
    SLIPPAGE_REFERENCE_QUOTE_VOLUME,
)

SLIP_NOW_TS = 1_800_000_000

#: A day's worth of quote volume that maps exactly to the floor -- the model's own anchor.
AT_ANCHOR = SLIPPAGE_REFERENCE_QUOTE_VOLUME


#: The fixture config's own allowlist, as product ids. Used rather than a bespoke allowlist per
#: test: `target_weights` must name the same assets, so swapping the list means keeping two
#: sections in step for no gain -- the report keys on the DERIVATION, not on which tickers.
FIXTURE_PRODUCTS = ("BTC-USD", "ETH-USD", "PAXG-USD")


def _config(tmp_path: Path) -> Any:
    """The shipped fixture config, loaded through the real parser.

    Through `load_config` rather than a stub: the product ids this report reads are derived by
    `_default_sim_products` from the allowlist AND the settlement currency, and a stub that
    supplied ids directly would skip the derivation the whole thing keys on.
    """
    from keel.config import load_config
    from tests.conftest import VALID_CONFIG_YAML

    path = tmp_path / "config.yaml"
    path.write_text(VALID_CONFIG_YAML, encoding="utf-8")
    return load_config(str(path))


def _repo(tmp_path: Path) -> Repository:
    conn = connect(str(tmp_path / "slippage.db"))
    migrate(conn)
    return Repository(conn)


def _daily(repo: Repository, product_id: str, *, volume_usd: str, bars: int = 5) -> None:
    """`bars` daily candles whose quote volume (`volume * close`) is `volume_usd` on each.

    Equal on every bar so the MEDIAN is exactly that figure -- these tests are about the mapping
    and the reporting, and a spread of volumes would make each expected rate a second
    calculation the test itself had to get right.
    """
    from keel_core.types import Granularity

    close = Decimal("100")
    volume = Decimal(volume_usd) / close
    repo.upsert_candles(
        product_id,
        Granularity.ONE_DAY,
        [
            _candle(SLIP_NOW_TS - (86_400 * (bars - index)), close, volume)
            for index in range(bars)
        ],
    )


def _daily_varying(repo: Repository, product_id: str, *volumes_usd: str) -> None:
    """Daily candles with DIFFERENT quote volumes, so the mean and the median differ.

    The distinguishing fixture. `_daily` gives every bar the same volume, which makes mean and
    median identical -- so a report that quietly computed its own mean instead of calling
    `screen.median_daily_quote_volume` would pass every test written on it. That mutation
    survived until this existed.
    """
    from keel_core.types import Granularity

    close = Decimal("100")
    repo.upsert_candles(
        product_id,
        Granularity.ONE_DAY,
        [
            _candle(SLIP_NOW_TS - (86_400 * (len(volumes_usd) - index)), close,
                    Decimal(volume) / close)
            for index, volume in enumerate(volumes_usd)
        ],
    )


def _candle(ts: int, close: Decimal, volume: Decimal) -> Any:
    from keel_core.types import Candle

    return Candle(ts=ts, open=close, high=close, low=close, close=close, volume=volume)




def _row(report: Any, product_id: str) -> Any:
    """One row by product id. Named rather than indexed: the fixture allowlist has three assets
    and a positional pick would silently follow the config the day it gains a fourth."""
    for row in report.rows:
        if row.product_id == product_id:
            return row
    raise AssertionError(f"{product_id} is not in the report")


# -- the exhibit ----------------------------------------------------------------------------------


def test_every_configured_product_gets_a_row(tmp_path: Path) -> None:
    """The universe, not the products that happen to have candles. An asset with no cached
    history is a row that says so -- dropping it would make the table look complete while the
    thing an operator most needs to see (this asset is priced on no evidence) disappears."""
    repo = _repo(tmp_path)
    _daily(repo, "BTC-USD", volume_usd=str(AT_ANCHOR))

    report = gather_slippage(repo, _config(tmp_path), now_ts=SLIP_NOW_TS)

    assert [row.product_id for row in report.rows] == list(FIXTURE_PRODUCTS)


def test_a_product_at_the_anchor_prices_at_the_floor(tmp_path: Path) -> None:
    """The model's own calibration point: `$500M/day` maps to `floor * sqrt(1)`."""
    repo = _repo(tmp_path)
    _daily(repo, "BTC-USD", volume_usd=str(AT_ANCHOR))

    row = _row(gather_slippage(repo, _config(tmp_path), now_ts=SLIP_NOW_TS), "BTC-USD")

    assert row.slippage_pct == SLIPPAGE_FLOOR_PCT
    assert row.floor_multiple == Decimal(1)
    assert row.capped is False
    assert row.fallback is False


def test_a_thin_product_pins_against_the_cap_and_says_so(tmp_path: Path) -> None:
    """The cap flag is not decoration. Where it fires, the mapping's bound did the deciding
    rather than the asset's own liquidity, so the rate shown is a LOWER bound on the real cost --
    and `slippage_for_quote_volume` says plainly that the curve is already an overstatement it
    does not fix. A capped row read as a measurement is the flattering error."""
    repo = _repo(tmp_path)
    _daily(repo, "PAXG-USD", volume_usd="1000")

    row = _row(gather_slippage(repo, _config(tmp_path), now_ts=SLIP_NOW_TS), "PAXG-USD")

    assert row.slippage_pct == SLIPPAGE_CAP_PCT
    assert row.capped is True


def test_a_product_with_no_cached_candles_is_a_fallback_row_not_a_missing_one(
    tmp_path: Path,
) -> None:
    """No statistic means the FLAT floor rate is applied -- which is the cheapest rate in the
    model, handed to the asset keel knows least about. That is the flattering direction, so the
    row carries the flag rather than the number alone."""
    repo = _repo(tmp_path)

    row = _row(gather_slippage(repo, _config(tmp_path), now_ts=SLIP_NOW_TS), "ETH-USD")

    assert row.fallback is True
    assert row.median_daily_quote_volume is None
    assert row.slippage_pct == SLIPPAGE_FLOOR_PCT
    assert row.bars == 0
    assert row.floor_multiple is None, "a fallback rate is not a multiple OF anything measured"


def test_the_statistic_comes_from_the_one_definition(tmp_path: Path) -> None:
    """`screen.median_daily_quote_volume`, not a second median computed here. Its docstring
    names the failure a copy causes: "a sweep that proposes an asset the screen then rejects on
    liquidity". A web page reporting a different figure from the gate is the same drift."""
    from keel_core.types import Granularity

    from keel.compliance import screen as screen_mod

    repo = _repo(tmp_path)
    # Volumes chosen so the MEAN and the MEDIAN are far apart: median 250M, mean 1.05B. A fixture
    # with equal volumes cannot tell the two apart, and a report computing its own mean would
    # then pass this test while reporting a figure the admission gate would never agree with.
    _daily_varying(repo, "BTC-USD", "1000000", "2000000", "250000000", "4000000000", "5000000000")

    row = _row(gather_slippage(repo, _config(tmp_path), now_ts=SLIP_NOW_TS), "BTC-USD")
    candles = repo.get_candles("BTC-USD", Granularity.ONE_DAY)
    expected = screen_mod.median_daily_quote_volume(candles)
    mean = sum((c.volume * c.close for c in candles), Decimal(0)) / len(candles)

    assert row.median_daily_quote_volume == expected
    assert row.median_daily_quote_volume != mean, "a mean would pass an equal-volume fixture"
    assert row.bars == 5


def test_a_product_between_the_bounds_scales_with_its_own_liquidity(tmp_path: Path) -> None:
    """The mapping itself, end to end: a quarter of the anchor's volume is twice the floor,
    because cost scales with the square root of the inverse volume ratio."""
    repo = _repo(tmp_path)
    _daily(repo, "BTC-USD", volume_usd=str(AT_ANCHOR / 4))

    row = _row(gather_slippage(repo, _config(tmp_path), now_ts=SLIP_NOW_TS), "BTC-USD")

    assert row.slippage_pct == SLIPPAGE_FLOOR_PCT * 2
    assert row.floor_multiple == Decimal(2)
    assert row.capped is False


# -- the headline, which is a count and not a claim ------------------------------------------------


def test_the_report_counts_how_many_products_reach_the_floor(tmp_path: Path) -> None:
    """#686's finding, as a number the page derives rather than a sentence it repeats: every
    experiment document in this repository prices fills at the floor, and priced over the real
    universe almost nothing reaches it. A hard-coded "0 of 24" would go stale the first time the
    allowlist changed."""
    repo = _repo(tmp_path)
    _daily(repo, "BTC-USD", volume_usd=str(AT_ANCHOR))
    _daily(repo, "PAXG-USD", volume_usd="1000")
    _daily(repo, "ETH-USD", volume_usd=str(AT_ANCHOR / 4))

    report = gather_slippage(repo, _config(tmp_path), now_ts=SLIP_NOW_TS)

    assert report.product_count == 3
    assert report.at_floor_count == 1
    assert report.capped_count == 1
    assert report.fallback_count == 0


def test_a_fallback_row_is_not_counted_as_reaching_the_floor(tmp_path: Path) -> None:
    """The count that would be easiest to get wrong and hardest to notice. A fallback row IS
    priced at the floor rate, numerically -- but it reached that rate by having no evidence, not
    by being liquid. Counting it as "reaches the floor" would turn missing data into the
    headline's best case, which is the exact inversion the flag exists to prevent."""
    repo = _repo(tmp_path)
    _daily(repo, "BTC-USD", volume_usd=str(AT_ANCHOR))

    report = gather_slippage(repo, _config(tmp_path), now_ts=SLIP_NOW_TS)

    assert report.fallback_count == 2, "ETH and PAXG have no cached candles"
    assert report.at_floor_count == 1, "a fallback row is not evidence of liquidity"


def test_the_model_parameters_are_reported_beside_the_rows(tmp_path: Path) -> None:
    """"A number whose assumptions cannot be recovered is not evidence" -- the model's own
    words. Floor, cap and anchor travel with the table so a reader can recompute any row."""
    report = gather_slippage(_repo(tmp_path), _config(tmp_path), now_ts=SLIP_NOW_TS)

    assert report.floor_pct == SLIPPAGE_FLOOR_PCT
    assert report.cap_pct == SLIPPAGE_CAP_PCT
    assert report.anchor_quote_volume == SLIPPAGE_REFERENCE_QUOTE_VOLUME


def test_rows_are_ordered_by_product_id_and_never_by_cost(tmp_path: Path) -> None:
    """Alphabetical, matching the table `keel simulate` already prints. Not a ranking: the
    Strathern rail is about ordering by a result, and this page is under it -- ordering the
    universe cheapest-first would read as a shortlist of what to trade, which is what a cost
    table must not become."""
    repo = _repo(tmp_path)
    # The alphabetically FIRST product is deliberately the most expensive one. Seeded the other
    # way round the two orderings coincide -- cheap BTC sorts first either way -- and the test
    # passes against a report that ranks by cost, which is the thing it exists to forbid.
    _daily(repo, "BTC-USD", volume_usd="1000")
    _daily(repo, "PAXG-USD", volume_usd=str(AT_ANCHOR))

    report = gather_slippage(repo, _config(tmp_path), now_ts=SLIP_NOW_TS)

    assert [row.product_id for row in report.rows] == sorted(FIXTURE_PRODUCTS)
    assert report.rows[0].capped is True, "the first row is the DEAREST -- ordering is not by cost"


# -- units, decided here and not in the serialiser -------------------------------------------------


def test_the_rate_is_carried_in_basis_points_as_well_as_as_a_fraction(tmp_path: Path) -> None:
    """The unit the page shows, computed HERE.

    `keel/web/payload.py::ratio` states the rule in its own docstring, about the exact same
    temptation: rescaling `drawdown_total_pct` by 100 there "would be the serialiser computing a
    figure the report never held -- the exact thing Rule 2 forbids ... Making the units honest is
    a change to `gather_status`, not to this file". A slippage rate shown as `5.0bp` from a
    stored `0.0005` is that same x10000, so it happens in this module.

    Both forms are kept: the fraction is what the engine applies to a fill, and a page that
    carried only basis points would have dropped the number the figure IS.
    """
    repo = _repo(tmp_path)
    _daily(repo, "BTC-USD", volume_usd=str(AT_ANCHOR))

    report = gather_slippage(repo, _config(tmp_path), now_ts=SLIP_NOW_TS)
    row = _row(report, "BTC-USD")

    assert row.slippage_pct == SLIPPAGE_FLOOR_PCT
    assert row.slippage_bp == Decimal("5")
    assert report.floor_bp == Decimal("5")
    assert report.cap_bp == Decimal("183.8")


def test_the_basis_point_figure_is_exact_and_never_a_float(tmp_path: Path) -> None:
    """`Decimal` all the way through. The cap is `0.01838`, and `0.01838 * 10000` in binary
    floating point is `183.79999999999998` -- which would reach a table cell as either that or a
    silently rounded `183.8` whose provenance nobody could recover."""
    repo = _repo(tmp_path)
    _daily(repo, "PAXG-USD", volume_usd="1000")

    row = _row(gather_slippage(repo, _config(tmp_path), now_ts=SLIP_NOW_TS), "PAXG-USD")

    assert isinstance(row.slippage_bp, Decimal)
    assert row.slippage_bp == Decimal("183.8")
