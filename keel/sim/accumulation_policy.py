"""Accumulation policy (#831): does HOW a monthly deposit is deployed beat static DCA, net of fees?

The harness for `docs/superpowers/specs/2026-09-27-accumulation-policy-design.md` (revision 2,
D1-D6 settled). This is an ALLOCATOR, not a rule engine: deposits in, orders out. It is kept
apart from `sim.portfolio_sim` on purpose (spec §7) -- that module drives rules, rails, entries
and exits; nothing here opens a risk-defined position, and no rail runs (spec §6).

**Four arms, one pure function each** (spec §5), all sharing the `Allocator` signature
`(t, prices, holdings, cash, deposit, month_buy_notional, weights) -> list[Order]`, where `t` is
the 1-based month index, `prices` the decision-day closes and `holdings` the units held:

* `static_dca` (A, the baseline): buy `w_i * D`.
* `value_averaging` (B): target path `V = w_i * D * t` (g = 0); buy
  `clamp(V - H_i, C_min * w_i * D, C_max * w_i * D)` with C_min 0, C_max 3; scaled down
  proportionally when cash is short. Buy-only: a surplus is never sold.
* `shortfall_steered` (C): `s_i = max(0, w_i (H + D) - H_i)`, buy `D * s_i / sum s`.
* `dca_with_trimming` (D): A's buy, then a SELECTIVE trim of assets strictly above their upper
  band (`h_i / H > w_i + b_i`, `b_i = max(0.15 w_i, 0.015)`) down to `w_i`, then the trim's net
  proceeds redeployed to the assets below `w_i` by shortfall. A lower-band breach alone is no trade.

**The account** (`run_account`, spec §4) is common to every arm:

* **Deposits** land on the FIRST DAILY BAR OF EACH UTC CALENDAR MONTH in the panel -- the 1st
  whenever that bar exists. This is `benchmark.dca_into_allowlist`'s convention exactly
  (`execution.guards._utc_month_bounds`), so a window that opens mid-month deposits on its first
  day, as the benchmark does. Undeployed cash earns nothing (fiqh: no interest).
* **Decisions and fills:** decided on the completed bar of the deposit day, filled at the NEXT
  bar's open -- `report.accumulation_table`'s convention (#821). A decision on the panel's last
  bar has no next bar and is dropped, as there; the deposit stays as cash.

  ⚠️ This is the ONE place arm A parts from `benchmark.dca_into_allowlist`, which fills at the
  deposit day's close. The two agree to the cent whenever each open equals the prior close
  (`tests/sim/test_accumulation_policy.py` pins both the agreement and the divergence). Neither
  convention is bent to match the other.
* **Costs** (`FeeModel`): slippage worsens every fill price, per asset (buy `open * (1 + s)`,
  sell `open * (1 - s)`). A buy's fee is taken off the top of its dollar amount before it is
  converted to units -- the benchmark's convention -- so a buy costs exactly its amount in cash.
  In `ALLOWANCE` mode buys are free until the calendar month's gross buy notional reaches the
  allowance A, and only the excess pays taker; the month is the FILL's month, and buys consume
  the allowance in order (A's DCA leg first, D's redeploy last). Sells ALWAYS pay taker. `FLAT`
  mode (the sensitivity run) charges taker on every leg.
* **Execution order within a fill is the order list's order**: D's DCA buys, then its trims, then
  the redeploys, which share out the trims' ACTUAL net proceeds (the plan sized them at the
  decision close; the fill is at the next open). So a trim realises against an average cost that
  already includes that day's DCA buy.
* **Holdings:** one `Lot` per asset at average cost; a sale removes units at the average cost and
  realises the difference. No leverage, no shorting: a sale is capped at the units held, and a
  buy the cash cannot cover raises (beyond a 1e-12 USD rounding allowance for proportional
  splits) rather than letting cash go negative.

**Returns** (spec §7-§8). `twr_returns` is the daily time-weighted return with deposits
neutralised by `metrics.bar_pnl` (a deposit counts as landing at the close of its day, where it
first becomes decision cash), so Sharpe, Sortino and max drawdown -- all `sim.metrics`, rf = 0,
365 periods -- measure policy, not cash timing. `summarize` reports the money-weighted figures
beside it: `metrics.irr` (its own convention: a per-DEPOSIT-period, i.e. monthly, rate) and
`metrics.cagr_money_weighted` (annualised on the calendar span).

**Inference** (spec §8). `stationary_bootstrap_indices` is Politis-Romano's stationary block
bootstrap (geometric block lengths with the given mean, circular wrap). `resample_panel` applies
ONE index sequence to every asset, so each resampled day is a JOINT historical row and
cross-correlation survives. A row is the pair `(open_t / close_{t-1}, close_t / close_{t-1})`,
so a rebuilt path keeps an open for the next-open fill; the path is anchored at the historical
first bar and keeps the historical timestamps, so every arm sees the same deposit calendar.
`bootstrap_deltas` runs every arm on the same paths and returns `(delta Sortino, delta max
drawdown)` against A per path. `passes`/`verdict` are decision D6: better only if, under BOTH
block lengths, `P(delta Sortino > 0) >= 0.95` (a tie is not an improvement) AND the median delta
max drawdown is not worse (`<= 0`, drawdowns being positive fractions).

`Decimal` for every money quantity; the only floats are the bootstrap's `random` draws, which
choose indices and never touch money. No dependency on `keel.research` (the direction is
research -> sim, see `report.py`).
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from keel.execution.guards import _utc_month_bounds
from keel.sim import metrics

ZERO = Decimal(0)
ONE = Decimal(1)

#: Spec §4, decisions D2/D3.
DEPOSIT_USD = Decimal(500)
ALLOWANCE_USD = Decimal(500)
TAKER_PCT = Decimal("0.012")
#: Spec §5, decision D4.
VA_C_MIN = Decimal(0)
VA_C_MAX = Decimal(3)
#: Spec §5, decision D5.
BAND_RELATIVE = Decimal("0.15")
BAND_FLOOR = Decimal("0.015")
#: Spec §8, decision D6.
BLOCK_LENGTHS = (20, 60)
N_PATHS = 2000
P_SORTINO_THRESHOLD = Decimal("0.95")

#: A buy may exceed cash by at most this much (proportional splits round at 28 digits); the
#: buy is then trimmed to the cash. Anything larger is an allocator bug and raises.
CASH_ROUNDING_USD = Decimal("1e-12")

ALLOWANCE = "allowance"
FLAT = "flat"
FEE_MODES = (ALLOWANCE, FLAT)

BUY = "buy"
SELL = "sell"
REDEPLOY = "redeploy"

BETTER = "better than static DCA"
BLOCK_DEPENDENT = "block-length-dependent"
NOT_BETTER = "not better than static DCA after fees"


@dataclass(frozen=True)
class Order:
    """One leg of an allocation.

    `amount` depends on `side`: a BUY is US dollars to spend, a SELL is UNITS to sell, and a
    REDEPLOY is the FRACTION of this fill's realised net sale proceeds to spend on the asset.
    """

    asset: str
    side: str
    amount: Decimal


@dataclass(frozen=True)
class PricePanel:
    """Aligned daily bars: one timestamp list and, per asset, an open and a close per timestamp."""

    ts: list[int]
    opens: dict[str, list[Decimal]]
    closes: dict[str, list[Decimal]]

    def __post_init__(self) -> None:
        n = len(self.ts)
        if any(b <= a for a, b in zip(self.ts, self.ts[1:], strict=False)):
            raise ValueError("panel timestamps must be strictly ascending")
        if set(self.opens) != set(self.closes):
            raise ValueError("panel opens and closes must name the same assets")
        for asset in self.closes:
            if len(self.opens[asset]) != n or len(self.closes[asset]) != n:
                raise ValueError(f"panel series for {asset} is not aligned to the timestamps")


@dataclass(frozen=True)
class FeeModel:
    """How a fill is billed (spec §4, D3). `slippage` is per asset; an absent asset slips 0."""

    mode: str
    taker_pct: Decimal = TAKER_PCT
    allowance_usd: Decimal = ALLOWANCE_USD
    slippage: Mapping[str, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in FEE_MODES:
            raise ValueError(f"fee mode {self.mode!r} not in {FEE_MODES}")

    def buy_fee(self, notional: Decimal, month_used: Decimal) -> Decimal:
        """The fee on a buy of `notional` when `month_used` of this month's allowance is gone."""
        if self.mode == FLAT:
            return notional * self.taker_pct
        free_left = max(ZERO, self.allowance_usd - month_used)
        return max(ZERO, notional - free_left) * self.taker_pct

    def sell_fee(self, notional: Decimal) -> Decimal:
        """Sells always pay taker: rail 14's allowance covers buys only."""
        return notional * self.taker_pct

    def slip(self, asset: str) -> Decimal:
        return self.slippage.get(asset, ZERO)


@dataclass
class Lot:
    """One asset's holding at average cost. `cost` is the cash paid for the units still held."""

    qty: Decimal = ZERO
    cost: Decimal = ZERO

    @property
    def avg_cost(self) -> Decimal:
        return self.cost / self.qty if self.qty > 0 else ZERO

    def buy(self, qty: Decimal, cost: Decimal) -> None:
        self.qty += qty
        self.cost += cost

    def sell(self, qty: Decimal, proceeds: Decimal) -> Decimal:
        """Remove `qty` units at the average cost; return the realised P&L."""
        if qty > self.qty:
            raise ValueError(f"cannot sell {qty} units from a lot of {self.qty}: no shorting")
        cost_out = qty * self.avg_cost
        self.qty -= qty
        self.cost -= cost_out
        return proceeds - cost_out


Allocator = Callable[
    [
        int,
        Mapping[str, Decimal],
        Mapping[str, Decimal],
        Decimal,
        Decimal,
        Decimal,
        Mapping[str, Decimal],
    ],
    list[Order],
]


def band(weight: Decimal) -> Decimal:
    """Arm D's half-width: 15% of the weight, never under 1.5 points (spec §5, D5)."""
    return max(BAND_RELATIVE * weight, BAND_FLOOR)


def renormalise(weights: Mapping[str, Decimal], exclude: Sequence[str] = ()) -> dict[str, Decimal]:
    """`weights` without `exclude` (and without zero weights), rescaled to sum to 1."""
    kept = {a: w for a, w in weights.items() if a not in exclude and w > 0}
    total = sum(kept.values(), ZERO)
    if total <= 0:
        raise ValueError("no positive weight left to renormalise")
    return {a: w / total for a, w in kept.items()}


def _values(
    prices: Mapping[str, Decimal], holdings: Mapping[str, Decimal], weights: Mapping[str, Decimal]
) -> dict[str, Decimal]:
    return {a: holdings.get(a, ZERO) * prices[a] for a in weights}


def static_dca(
    t: int,
    prices: Mapping[str, Decimal],
    holdings: Mapping[str, Decimal],
    cash: Decimal,
    deposit: Decimal,
    month_buy_notional: Decimal,
    weights: Mapping[str, Decimal],
) -> list[Order]:
    """Arm A: buy `w_i * D` of every asset."""
    return [Order(a, BUY, w * deposit) for a, w in weights.items() if w * deposit > 0]


def value_averaging(
    t: int,
    prices: Mapping[str, Decimal],
    holdings: Mapping[str, Decimal],
    cash: Decimal,
    deposit: Decimal,
    month_buy_notional: Decimal,
    weights: Mapping[str, Decimal],
) -> list[Order]:
    """Arm B: bounded, buy-only value averaging on the zero-growth path `V = w_i * D * t`.

    `H_i` is valued at the decision close; `t` counts deposits so far, this one included. When
    the wanted buys exceed cash, every buy is scaled by the same factor `cash / wanted`.
    """
    values = _values(prices, holdings, weights)
    wants: dict[str, Decimal] = {}
    for a, w in weights.items():
        slice_ = w * deposit
        target = slice_ * t
        wants[a] = min(max(target - values[a], VA_C_MIN * slice_), VA_C_MAX * slice_)
    wanted = sum(wants.values(), ZERO)
    if wanted <= 0 or cash <= 0:
        return []
    if wanted > cash:
        wants = {a: want * cash / wanted for a, want in wants.items()}
    return [Order(a, BUY, want) for a, want in wants.items() if want > 0]


def _shortfall_shares(
    values: Mapping[str, Decimal], weights: Mapping[str, Decimal], total: Decimal
) -> dict[str, Decimal]:
    """`max(0, w_i * total - value_i)` per asset, for the assets with a positive shortfall."""
    shortfalls = {a: max(ZERO, w * total - values[a]) for a, w in weights.items()}
    return {a: s for a, s in shortfalls.items() if s > 0}


def shortfall_steered(
    t: int,
    prices: Mapping[str, Decimal],
    holdings: Mapping[str, Decimal],
    cash: Decimal,
    deposit: Decimal,
    month_buy_notional: Decimal,
    weights: Mapping[str, Decimal],
) -> list[Order]:
    """Arm C: deploy D, split by shortfall `s_i = max(0, w_i (H + D) - H_i)`; buy-only.

    When every `s_i` is 0 the deposit is split by weight (spec §5). With weights summing to 1
    and D > 0 that cannot happen -- `sum s_i >= sum (w_i (H + D) - H_i) = D` -- but the spec
    states the fallback, so it is kept rather than assumed away. The budget is `min(D, cash)`,
    which is D whenever the arm has deployed no more than it was given.
    """
    budget = min(deposit, cash)
    if budget <= 0:
        return []
    values = _values(prices, holdings, weights)
    holding_value = sum(values.values(), ZERO)
    shortfalls = _shortfall_shares(values, weights, holding_value + deposit)
    total = sum(shortfalls.values(), ZERO)
    if total <= 0:
        return [Order(a, BUY, w * budget) for a, w in weights.items() if w > 0]
    return [Order(a, BUY, budget * s / total) for a, s in shortfalls.items()]


def dca_with_trimming(
    t: int,
    prices: Mapping[str, Decimal],
    holdings: Mapping[str, Decimal],
    cash: Decimal,
    deposit: Decimal,
    month_buy_notional: Decimal,
    weights: Mapping[str, Decimal],
) -> list[Order]:
    """Arm D: A's buy, then a selective upper-band trim to target, proceeds to underweights.

    The spec orders the steps buy -> trim -> redeploy, so the band is checked on the POST-BUY
    book valued at the decision close: `h_i = H_i + w_i D`, `H = sum h_i`. An asset is trimmed
    only when `h_i > (w_i + b_i) H` (strictly: sitting on the edge is inside), and only by
    `h_i - w_i H`, sized in units at the decision close. The redeploy shares are the
    shortfalls of the assets below `w_i`, against the same `H`. Nothing is traded for a
    lower-band breach alone: an underweight is filled only by the regular buy or by trim
    proceeds.
    """
    orders = static_dca(t, prices, holdings, cash, deposit, month_buy_notional, weights)
    values = _values(prices, holdings, weights)
    post = {a: values[a] + w * deposit for a, w in weights.items()}
    total = sum(post.values(), ZERO)
    if total <= 0:
        return orders
    trims = [
        Order(a, SELL, (post[a] - w * total) / prices[a])
        for a, w in weights.items()
        if post[a] > (w + band(w)) * total
    ]
    if not trims:
        return orders
    # A trimmed asset sits above `w_i H` by definition, so its shortfall is already 0: the
    # proceeds can only reach assets below target.
    shortfalls = _shortfall_shares(post, weights, total)
    short_total = sum(shortfalls.values(), ZERO)
    if short_total <= 0:
        # Unreachable with weights summing to 1 (a trim implies an equal total shortfall);
        # conservatively hold the proceeds as cash rather than invent a split.
        return orders + trims
    redeploys = [Order(a, REDEPLOY, s / short_total) for a, s in shortfalls.items()]
    return orders + trims + redeploys


ARMS: dict[str, Allocator] = {
    "A": static_dca,
    "B": value_averaging,
    "C": shortfall_steered,
    "D": dca_with_trimming,
}


@dataclass
class AccountResult:
    """One arm's account path over one panel."""

    equity_curve: list[tuple[int, Decimal]]
    cash_curve: list[tuple[int, Decimal]]
    deposits: list[tuple[int, Decimal]]
    lots: dict[str, Lot]
    buy_fees: Decimal = ZERO
    sell_fees: Decimal = ZERO
    buy_notional: Decimal = ZERO
    sell_notional: Decimal = ZERO
    realized_pnl: Decimal = ZERO


class _Book:
    """The mutable account `run_account` drives; see the module docstring for its conventions."""

    def __init__(self, assets: Sequence[str], fees: FeeModel) -> None:
        self.fees = fees
        self.lots = {a: Lot() for a in assets}
        self.cash = ZERO
        self.month_used: dict[int, Decimal] = {}
        self.result = AccountResult(equity_curve=[], cash_curve=[], deposits=[], lots=self.lots)

    def _spend(self, amount: Decimal) -> Decimal:
        if amount > self.cash:
            if amount - self.cash > CASH_ROUNDING_USD:
                raise ValueError(
                    f"an allocation spends {amount} with {self.cash} cash: cash may never go "
                    "negative (no leverage, no borrowing)"
                )
            amount = self.cash
        return amount

    def fill(self, orders: Sequence[Order], opens: Mapping[str, Decimal], month: int) -> None:
        used = self.month_used.get(month, ZERO)
        proceeds = ZERO
        r = self.result
        for order in orders:
            lot = self.lots[order.asset]
            slip = self.fees.slip(order.asset)
            if order.side == SELL:
                qty = min(order.amount, lot.qty)
                if qty <= 0:
                    continue
                gross = qty * (opens[order.asset] * (ONE - slip))
                fee = self.fees.sell_fee(gross)
                net = gross - fee
                r.realized_pnl += lot.sell(qty, net)
                self.cash += net
                proceeds += net
                r.sell_fees += fee
                r.sell_notional += gross
                continue
            if order.side == BUY:
                amount = order.amount
            elif order.side == REDEPLOY:
                amount = proceeds * order.amount
            else:
                raise ValueError(f"unknown order side {order.side!r}")
            if amount <= 0:
                continue
            amount = self._spend(amount)
            fee = self.fees.buy_fee(amount, used)
            used += amount
            lot.buy((amount - fee) / (opens[order.asset] * (ONE + slip)), amount)
            self.cash -= amount
            r.buy_fees += fee
            r.buy_notional += amount
        self.month_used[month] = used


def run_account(
    panel: PricePanel,
    weights: Mapping[str, Decimal],
    allocator: Allocator,
    fees: FeeModel,
    deposit: Decimal = DEPOSIT_USD,
) -> AccountResult:
    """Drive one arm over `panel`: deposits, next-open fills, fees, average cost, daily marks.

    Every positively weighted asset must be in the panel -- a silently missing asset would
    change the universe under the arm, so it raises instead.
    """
    live = {a: w for a, w in weights.items() if w > 0}
    missing = [a for a in live if a not in panel.closes]
    if missing:
        raise ValueError(f"weighted assets missing from the panel: {missing}")
    book = _Book(list(live), fees)
    result = book.result
    n = len(panel.ts)
    t = 0
    current_month: int | None = None
    pending: list[Order] = []
    for i, ts in enumerate(panel.ts):
        if pending:
            opens = {a: panel.opens[a][i] for a in live}
            book.fill(pending, opens, _utc_month_bounds(ts)[0])
            pending = []

        month, _ = _utc_month_bounds(ts)
        decide = month != current_month
        if decide:
            current_month = month
            t += 1
            book.cash += deposit
            result.deposits.append((ts, deposit))

        closes = {a: panel.closes[a][i] for a in live}
        held = sum((book.lots[a].qty * closes[a] for a in live), ZERO)
        result.equity_curve.append((ts, held + book.cash))
        result.cash_curve.append((ts, book.cash))

        if decide and i + 1 < n:
            fill_month, _ = _utc_month_bounds(panel.ts[i + 1])
            pending = allocator(
                t,
                closes,
                {a: book.lots[a].qty for a in live},
                book.cash,
                deposit,
                book.month_used.get(fill_month, ZERO),
                live,
            )
    return result


def twr_returns(result: AccountResult) -> list[Decimal]:
    """Daily time-weighted returns, deposits neutralised, from the first day with equity."""
    curve = result.equity_curve
    start = next((k for k, (_, v) in enumerate(curve) if v > 0), None)
    if start is None:
        return []
    curve = curve[start:]
    pnl = metrics.bar_pnl(curve, result.deposits)
    return [p / prev if prev > 0 else ZERO for (_, prev), p in zip(curve, pnl, strict=False)]


def twr_index(result: AccountResult) -> list[tuple[int, Decimal]]:
    """The TWR index: 1 on the first day with equity, compounded by `twr_returns` after it."""
    curve = result.equity_curve
    start = next((k for k, (_, v) in enumerate(curve) if v > 0), None)
    if start is None:
        return []
    level = ONE
    index = [(curve[start][0], level)]
    for (ts, _), r in zip(curve[start + 1 :], twr_returns(result), strict=True):
        level = level * (ONE + r)
        index.append((ts, level))
    return index


def summarize(result: AccountResult) -> dict[str, Decimal]:
    """The spec §8 per-arm figures, all `Decimal`.

    `turnover` is traded notional (buys + sells) over deposits: the spec names the metric but
    not its denominator, and deposits are the one base identical across arms (static DCA reads
    ~1). `irr` is `metrics.irr`'s per-deposit-period (monthly) rate; `cagr_money_weighted` is its
    calendar-annualised sibling. `avg_cash_share` is the mean over days with equity of cash /
    account value.
    """
    curve = result.equity_curve
    terminal = curve[-1][1] if curve else ZERO
    deposited = sum((amount for _, amount in result.deposits), ZERO)
    returns = twr_returns(result)
    index = twr_index(result)
    cashflows = [(ts, -amount) for ts, amount in result.deposits]
    shares = [
        cash / value
        for (_, value), (_, cash) in zip(curve, result.cash_curve, strict=True)
        if value > 0
    ]
    traded = result.buy_notional + result.sell_notional
    return {
        "terminal_value": terminal,
        "deposited": deposited,
        "irr": metrics.irr(cashflows, terminal),
        "cagr_money_weighted": (
            metrics.cagr_money_weighted(cashflows, terminal, curve[0][0], curve[-1][0])
            if curve
            else ZERO
        ),
        "twr_total": index[-1][1] - ONE if index else ZERO,
        "sharpe": metrics.sharpe(returns),
        "sortino": metrics.sortino(returns),
        "max_drawdown": metrics.max_drawdown_pct(index),
        "buy_fees": result.buy_fees,
        "sell_fees": result.sell_fees,
        "buy_notional": result.buy_notional,
        "sell_notional": result.sell_notional,
        "realized_pnl": result.realized_pnl,
        "turnover": traded / deposited if deposited > 0 else ZERO,
        "avg_cash_share": sum(shares, ZERO) / len(shares) if shares else ZERO,
    }


def stationary_bootstrap_indices(n: int, mean_block: int, rng: random.Random) -> list[int]:
    """`n` row indices from Politis-Romano's stationary bootstrap with mean block `mean_block`.

    Each step starts a new block at a uniform index with probability `1 / mean_block`, else
    continues to the next row, wrapping circularly. Deterministic in `rng`.
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    if mean_block < 1:
        raise ValueError(f"mean_block must be >= 1, got {mean_block}")
    p = 1.0 / mean_block
    out = [rng.randrange(n)]
    while len(out) < n:
        out.append(rng.randrange(n) if rng.random() < p else (out[-1] + 1) % n)
    return out


@dataclass(frozen=True)
class _Rows:
    """Per asset, the joint daily rows `open_t / close_{t-1}` and `close_t / close_{t-1}`."""

    gaps: dict[str, list[Decimal]]
    rets: dict[str, list[Decimal]]


def _joint_rows(panel: PricePanel) -> _Rows:
    gaps: dict[str, list[Decimal]] = {}
    rets: dict[str, list[Decimal]] = {}
    for a, closes in panel.closes.items():
        opens = panel.opens[a]
        gaps[a] = [opens[k] / closes[k - 1] for k in range(1, len(closes))]
        rets[a] = [closes[k] / closes[k - 1] for k in range(1, len(closes))]
    return _Rows(gaps=gaps, rets=rets)


def _rebuild(panel: PricePanel, rows: _Rows, indices: Sequence[int]) -> PricePanel:
    n_rows = len(panel.ts) - 1
    if len(indices) != n_rows:
        raise ValueError(f"need {n_rows} row indices, got {len(indices)}")
    if any(not 0 <= i < n_rows for i in indices):
        raise ValueError("a row index is out of range")
    opens: dict[str, list[Decimal]] = {}
    closes: dict[str, list[Decimal]] = {}
    for a in panel.closes:
        gaps, rets = rows.gaps[a], rows.rets[a]
        prev = panel.closes[a][0]
        o, c = [panel.opens[a][0]], [prev]
        for i in indices:
            o.append(prev * gaps[i])
            prev = prev * rets[i]
            c.append(prev)
        opens[a], closes[a] = o, c
    return PricePanel(ts=list(panel.ts), opens=opens, closes=closes)


def resample_panel(panel: PricePanel, indices: Sequence[int]) -> PricePanel:
    """A path rebuilt from `panel`'s joint daily rows in the order `indices` gives.

    Day 0 is the historical first bar; day `k >= 1` takes row `indices[k - 1]` for EVERY asset
    (row `r` is historical day `r + 1` relative to day `r`), so cross-asset correlation is kept.
    Timestamps are the historical ones, so the deposit calendar is unchanged.
    """
    return _rebuild(panel, _joint_rows(panel), indices)


def _path_stats(result: AccountResult) -> tuple[Decimal, Decimal]:
    return metrics.sortino(twr_returns(result)), metrics.max_drawdown_pct(twr_index(result))


def bootstrap_deltas(
    panel: PricePanel,
    weights: Mapping[str, Decimal],
    fees: FeeModel,
    *,
    mean_block: int,
    n_paths: int,
    seed: int,
    arms: Mapping[str, Allocator] = ARMS,
    baseline: str = "A",
    deposit: Decimal = DEPOSIT_USD,
) -> dict[str, list[tuple[Decimal, Decimal]]]:
    """Per arm, `(Sortino - baseline's, max drawdown - baseline's)` on each resampled path.

    Every arm runs on the SAME `n_paths` paths, drawn from one `random.Random(seed)`, so the same
    seed gives the same paths under any fee model -- the fee modes are compared on identical
    markets. The baseline's own deltas are zero by construction.
    """
    if baseline not in arms:
        raise ValueError(f"baseline {baseline!r} is not one of the arms")
    rng = random.Random(seed)
    rows = _joint_rows(panel)
    out: dict[str, list[tuple[Decimal, Decimal]]] = {name: [] for name in arms}
    for _ in range(n_paths):
        indices = stationary_bootstrap_indices(len(panel.ts) - 1, mean_block, rng)
        path = _rebuild(panel, rows, indices)
        stats = {
            name: _path_stats(run_account(path, weights, fn, fees, deposit))
            for name, fn in arms.items()
        }
        base_sortino, base_mdd = stats[baseline]
        for name, (sortino, mdd) in stats.items():
            out[name].append((sortino - base_sortino, mdd - base_mdd))
    return out


def p_sortino_positive(deltas: Sequence[tuple[Decimal, Decimal]]) -> Decimal:
    """The share of paths whose Sortino delta is strictly positive (a tie is no improvement)."""
    if not deltas:
        raise ValueError("no bootstrap deltas to judge")
    return Decimal(sum(1 for d_sortino, _ in deltas if d_sortino > 0)) / Decimal(len(deltas))


def median_delta_mdd(deltas: Sequence[tuple[Decimal, Decimal]]) -> Decimal:
    if not deltas:
        raise ValueError("no bootstrap deltas to judge")
    return Decimal(statistics.median([d_mdd for _, d_mdd in deltas]))


def passes(deltas: Sequence[tuple[Decimal, Decimal]]) -> bool:
    """D6 at one block length: `P(delta Sortino > 0) >= 0.95` and median delta max DD `<= 0`."""
    return p_sortino_positive(deltas) >= P_SORTINO_THRESHOLD and median_delta_mdd(deltas) <= ZERO


def verdict(by_block: Mapping[int, Sequence[tuple[Decimal, Decimal]]]) -> str:
    """D6: better only when BOTH block lengths pass; one of two is block-length-dependent."""
    if set(by_block) != set(BLOCK_LENGTHS):
        raise ValueError(
            f"the decision rule needs exactly the block lengths {BLOCK_LENGTHS}, got "
            f"{sorted(by_block)}: judging one length would let it be chosen after the fact"
        )
    passed = [passes(by_block[length]) for length in BLOCK_LENGTHS]
    if all(passed):
        return BETTER
    if any(passed):
        return BLOCK_DEPENDENT
    return NOT_BETTER
