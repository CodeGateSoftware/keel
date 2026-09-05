"""What a fill is assumed to cost, per product -- issue #708, view 4.

**The exhibit.** Every experiment document in this repository prices its fills at
`slippage_pct=0.0005` -- the FLOOR of `slippage_for_quote_volume`, reached only at the model's
$500M/day anchor. Priced from each asset's own cached liquidity, almost nothing reaches it. That
is #686's finding, and until now it existed as a sentence in a markdown file; this puts the
per-product table on a screen, derived rather than restated, so it cannot go stale the way a
hard-coded "0 of 24" would the first time the allowlist changed.

**EVERY NUMBER HERE IS AN ASSUMPTION, AND THE REPORT SAYS SO IN THE PAYLOAD.** This is the one
thing this module must not get wrong. `slippage_for_quote_volume`'s own docstring:

    An ASSUMPTION, not a measurement -- keel stores no book snapshots or realised spreads, so
    liquidity is proxied by the one statistic it does compute.

#708's scope note calls these figures "per-product **measured** bp". They are not measured, and a
page that said so would be making exactly the claim the model refuses to make. `sim/report.py`'s
own table carries the correction in bold; so does this. The floor, cap and anchor travel with the
rows for the reason that file states: "a number whose assumptions cannot be recovered is not
evidence", so a reader can recompute any row from what is on the page.

**Nothing here computes a statistic of its own.** The product list is
`_products._default_sim_products`, the liquidity figure is `screen.median_daily_quote_volume`, the
mapping is `backtest.slippage_for_quote_volume`, and the `fallback`/`capped` flags are
`backtest.SlippageAssumption`'s own derived properties. Four borrowed definitions and no fifth:
a web page reporting a different liquidity figure from the one the admission gate applies is the
same drift `median_daily_quote_volume`'s docstring exists to prevent, seen from the other side.

**Read-only, no broker, no network.** One `get_candles` per configured product against the same
SQLite file every other view reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from keel_core.config import Config
from keel_core.types import Granularity

from keel.data.repository import Repository

#: Basis points per unit fraction. The conversion lives HERE and not in `keel/web/payload.py`,
#: and that placement is a rule rather than a preference: `payload.ratio`'s docstring refuses the
#: identical x100 on `drawdown_total_pct` because rescaling in the serialiser would be "the
#: serialiser computing a figure the report never held -- the exact thing Rule 2 forbids", and
#: says the fix belongs in the report builder. This is the report builder.
BASIS_POINTS = Decimal(10_000)


@dataclass(frozen=True)
class SlippageRow:
    """One product's assumed per-leg cost, with everything needed to check it.

    Mirrors `backtest.SlippageAssumption` rather than replacing it -- the flags are that class's
    derived properties, copied onto a frozen row here so the web payload reads one flat shape.
    """

    product_id: str

    #: The liquidity statistic the rate was scaled from, or `None` when no daily candles were
    #: cached for this product. `None` is the fallback case, never zero: `median_daily_quote_
    #: volume` returns `Decimal(0)` for empty input and 0 maps to the CAP, so writing the
    #: statistic as zero here would turn "we have no history" into "this is the thinnest asset
    #: in the universe" -- a different and much louder claim.
    median_daily_quote_volume: Decimal | None

    #: The per-leg rate applied, as a fraction (`0.0005` is 5bp). What the engine multiplies a
    #: fill by -- kept alongside `slippage_bp` because it is the number the figure IS.
    slippage_pct: Decimal

    #: The same rate in basis points, the unit the page and the terminal both show it in.
    #: `Decimal`, so the cap reads `183.8` and not float arithmetic's `183.79999999999998`.
    slippage_bp: Decimal

    #: `slippage_pct / floor`, or `None` for a fallback row. `None` and not `1` on a fallback:
    #: the row is priced AT the floor rate, but it got there by having no evidence rather than by
    #: being liquid, and a `1.0x` in that cell reads as "as cheap as BTC".
    floor_multiple: Decimal | None

    #: How many daily candles the statistic came from. Zero is the fallback case. Carried because
    #: a median over three bars and a median over three hundred are not the same evidence, and
    #: nothing else on the page would say which this is.
    bars: int

    #: No cached liquidity statistic; the flat floor rate was applied. The FLATTERING direction --
    #: the cheapest rate in the model, handed to the asset keel knows least about -- which is why
    #: it is flagged rather than left to be read off the number.
    fallback: bool

    #: The mapping's thin-end bound did the deciding, not the asset's own liquidity. Also the
    #: flattering direction: the true cost is at least this, and `slippage_for_quote_volume`
    #: records that the curve is an overstatement the cap does not fix.
    capped: bool


@dataclass(frozen=True)
class SlippageReport:
    now_ts: int

    rows: tuple[SlippageRow, ...]

    #: The model's parameters, carried so a reader can recompute any row rather than trust it.
    floor_pct: Decimal
    cap_pct: Decimal
    anchor_quote_volume: Decimal

    @property
    def floor_bp(self) -> Decimal:
        return self.floor_pct * BASIS_POINTS

    @property
    def cap_bp(self) -> Decimal:
        return self.cap_pct * BASIS_POINTS

    @property
    def product_count(self) -> int:
        """Held here rather than derived in the payload -- Rule 6e bans `len()` there."""
        return len(self.rows)

    @property
    def at_floor_count(self) -> int:
        """How many products actually reach the floor every experiment document assumes.

        **Fallback rows do not count**, and that exclusion is the whole honesty of this number. A
        fallback row IS priced at the floor rate numerically, so a naive `slippage_pct ==
        floor_pct` would count "we cached no history for this asset" as "this asset is as liquid
        as the model's anchor" -- turning missing data into the headline's best case, which is
        the inversion the flag exists to prevent.
        """
        return sum(
            1 for row in self.rows if not row.fallback and row.slippage_pct == self.floor_pct
        )

    @property
    def capped_count(self) -> int:
        return sum(1 for row in self.rows if row.capped)

    @property
    def fallback_count(self) -> int:
        return sum(1 for row in self.rows if row.fallback)

    @property
    def priced_count(self) -> int:
        """Products with a liquidity statistic behind their rate -- the denominator
        `at_floor_count` is honestly out of. `product_count` would flatter it by counting the
        rows that were never priced at all."""
        return sum(1 for row in self.rows if not row.fallback)


def _row_for(repo: Repository, product_id: str, floor_pct: Decimal) -> SlippageRow:
    """One product, priced from its own cached daily bars.

    ONE_DAY specifically, matching `simulate._slippage_for_products`: the statistic is a MEDIAN
    DAILY quote volume, and computing it over hourly bars would answer a different question with
    the same name -- and answer it about a twenty-fourth of the volume.
    """
    from keel.compliance import screen as screen_mod
    from keel.strategy import backtest as backtest_mod

    daily = repo.get_candles(product_id, Granularity.ONE_DAY)
    if not daily:
        return SlippageRow(
            product_id=product_id,
            median_daily_quote_volume=None,
            slippage_pct=floor_pct,
            slippage_bp=floor_pct * BASIS_POINTS,
            floor_multiple=None,
            bars=0,
            fallback=True,
            capped=False,
        )

    median = screen_mod.median_daily_quote_volume(daily)
    assumption = backtest_mod.SlippageAssumption(
        product_id, median, backtest_mod.slippage_for_quote_volume(median)
    )
    return SlippageRow(
        product_id=product_id,
        median_daily_quote_volume=assumption.median_daily_quote_volume,
        slippage_pct=assumption.slippage_pct,
        slippage_bp=assumption.slippage_pct * BASIS_POINTS,
        floor_multiple=assumption.slippage_pct / floor_pct,
        bars=len(daily),
        # From `SlippageAssumption`'s own derived properties rather than recomputed: the class's
        # docstring makes the point that deriving them is what keeps "the flag and the number it
        # explains" from disagreeing, and a second derivation here would be the disagreement.
        fallback=assumption.fallback,
        capped=assumption.capped,
    )


def gather_slippage(repo: Repository, config: Config, *, now_ts: int) -> SlippageReport:
    """The configured universe, each product priced from its own cached liquidity.

    Rows come back ordered by PRODUCT ID -- the same order `keel simulate` already prints its
    slippage table in. Not by cost, and that is deliberate under the Strathern rail this whole
    `/research` surface sits under: a cost table sorted cheapest-first reads as a shortlist of
    what to trade, and a page that ranks the universe by anything is doing selection while
    calling itself reporting. Alphabetical is also stable, so the table does not reshuffle
    between polls as candles arrive.
    """
    from keel.commands._products import _default_sim_products
    from keel.strategy import backtest as backtest_mod

    floor_pct = backtest_mod.SLIPPAGE_FLOOR_PCT
    rows = tuple(
        _row_for(repo, product_id, floor_pct)
        for product_id in sorted(_default_sim_products(config))
    )
    return SlippageReport(
        now_ts=now_ts,
        rows=rows,
        floor_pct=floor_pct,
        cap_pct=backtest_mod.SLIPPAGE_CAP_PCT,
        anchor_quote_volume=backtest_mod.SLIPPAGE_REFERENCE_QUOTE_VOLUME,
    )
