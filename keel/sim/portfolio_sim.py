"""Multi-asset portfolio simulator (plan Task 6,
`docs/superpowers/plans/2026-07-17-engine-validation-simulation.md`).

Walks the ascending UNION of `ONE_HOUR` timestamps across every asset in `candles_by_asset`
between `start_ts` and `end_ts`, driving each asset's assigned `Rule`(s) through
`strategy.engine.evaluate()` on a rolling, lookahead-free window (`WINDOW_BARS` hourly bars plus
every daily bar with `ts <= t`), opening/closing at most one RULE (risk-defined) position per
asset on a single shared `sim.account.SimAccount`, and recording per-trade (`SimTrade`) and
portfolio-level (`SimTelemetry`) data for the report/verdict stage (Task 7, `sim/report.py`).

**Sizing is CLAMPED, not rejected (Issue #85):** a rule signal's fixed-fractional risk size
(`execution.sizing.size`) is clamped down to `account.max_affordable_notional()` -- the tightest
of available USDC cash, per-asset concentration headroom, and total-exposure headroom -- before
`can_open` ever sees it, instead of being rejected outright whenever the raw risk-sized notional
happened to exceed one of those. `can_open` remains the hard safety veto (a clamped intent is
only skipped if it still doesn't pass, or clamps below `DUST_FLOOR`); it is never weakened.

**DCA is a separate sleeve, not a slot-occupant (Issue #85):** a DCA-class setup (`no_stop` /
`order_class == "dca"` context, `strategy/rules/dca.py`) is scheduled accumulation, not a
risk-defined trade -- it is evaluated on every bar regardless of whether that asset's RULE
slot is currently held (and bought at most once per UTC day, see below), via
`account.open(..., dca=True)`, which accumulates into a separate per-asset DCA lot
(`SimAccount.dca_positions`) that this simulator never closes (DCA has no exit signal by
design; a sleeve distribution only SHRINKS it, see below). Before this fix, DCA and rule
trades shared the single per-asset `held` slot, so an asset accumulating DCA (which never exits)
permanently froze that asset's rule evaluation.

**One DCA decision per UTC day, per (asset, rule) (#821):** the loop is hourly but a DCA rule
decides on DAILY candles, so its latest completed day -- and therefore its `detect` result -- is
the same on all 24 bars of a day. Without a guard every cadence day bought 24 times (a $50 budget
over 9 cadence days spent ~$10,768, not $450). `_process_dca_signals` therefore records the UTC
day of each DCA decision and takes at most one per (asset, rule) per day -- whether it filled or
was vetoed, exactly as the live agent trades once per UTC day. Each buy is logged
(`SimResult.dca_buys`), and `dca_sleeve` marks the never-closed lots at each asset's final close
(`SimResult.final_prices`) so the report can show the sleeve instead of leaving it only inside
`ending_value`.

**The reverse path (#857, P11):** a sleeve-sell rule (`promotion_class == "sleeve_sell"`,
`reverse_dca`) sells from that DCA lot through `_process_reductions`, at most once per asset per
UTC day, after the asset's buys -- the live cycle's order (`agent._handle_reductions` runs
last). Its decision is `decide_sleeve_sale`, shared with `report.accumulation_table`, and made
of the live pipeline's own `sleeve.sleeve_refusal` and `sleeve.slice_qty`, asked over per-buy
FIFO lots (`_reduction_check_holding`, #924) so the refusal sees the same tranche ages live's own
check would; each fill is a `DcaSell` (`SimResult.dca_sells`) and `dca_sleeve` carries the
totals. The BOOKING still reduces one averaged lot, so the account's realised P&L on a
distribution is average-cost (plan R32); the FIFO-faithful row is the edge pass's. Both are
fidelity checks of the harness on synthetic
candles, not verdicts (spec §6, "Evidence status").

**Interpretive notes** (the plan's Task 6 prose leaves a few specifics implicit):

- **`cts_factor_populated` / `rejected_for_missing_input`** (both `dict[str, int]`, keyed by CTS
  context-key name -- see `strategy.indicators_cts.DEFAULT_WEIGHTS`): for *every* ENTER signal
  `evaluate()` emits (whether or not it ends up opened), the engine's own CTS context assembly is
  reused verbatim (`engine.assemble_cts_context` + `indicators_cts.score`, not reimplemented) to
  determine, per factor, whether it was present or absent on that bar. Present factors increment
  `cts_factor_populated[name]`; absent ones increment `rejected_for_missing_input[name]`. This is
  symmetric by construction (`populated[k] + missing[k]` == the number of signals a given `k` was
  ever evaluated on) and feeds Task 7's "unfed CTS factors" gap-analysis detector directly. The
  plan text ties the second counter to `can_open`'s `not ok` outcome, but `can_open`'s rejection
  reasons are always spend-cap strings (never confluence-related, see `sim/account.py`), so a
  literal "not ok AND missing-confluence" condition could never fire; tallying every evaluated
  signal's absent factors is the reading that actually serves the stated purpose.
- **`per_bucket_pnl` regime key**: bucketed by the market `Condition` (`analysis.regime`) of the
  exit-time ONE_HOUR window -- the regime the trade *closed* into, not the one it opened into.
- **Idle-span gating**: the plan calls for a move-threshold (`MOVE_THRESHOLD_PCT`) AND "a gap
  exceeds a threshold span"; the latter is `IDLE_SPAN_MIN_HOURS` here (undocumented exact value
  in the plan prose), gating idle-span detection to genuinely quiet stretches rather than
  single-bar noise.
- **`SimResult.coverage`**: `run()`'s signature (per the plan) takes no coverage/history input,
  so this is always `{}` here -- a passthrough placeholder for the CLI (Task 8), which does have
  access to `data/history.py`'s per-asset `CoverageInfo` and can attach it after calling `run()`.
- **Managed stops (#442):** a rule whose `params` carry `trail_atr_mult`/`be_roll_rr` has its
  stop RATCHETED by `strategy.exit_policy` at each bar the position survives (see
  `_process_held`): touch checks run against the level carried INTO the bar, management runs
  at bar end on the completed bar, and the stop never widens; a bar that gaps entirely
  through the stop exits at its OPEN (`strategy.backtest._stop_exit_price`, the shared
  convention). A rule without the knobs trades exactly as before the wiring existed --
  identity pinned by the unit-identity tests plus the unchanged pre-existing suite with the
  wiring live. This is the sim-side expression of the `executor` stop-management primitives,
  which the live cycle drives too since #502 stage 2 (`agent._manage_stops`, default off).

**No lookahead:** the per-bar `candles_by_tf` window handed to `Rule.detect`/`exit_signal` and to
`engine.evaluate` only ever contains candles with `ts <= t` (the current bar). The one deliberate
exception -- a fill-cost model, not a lookahead violation of the *decision* -- is that a passed
`can_open` check fills at the *next* hourly bar's `open` (a market order placed on bar `t` can't
fill at that same bar's price); if there is no next bar, the signal is dropped unfilled.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from keel.analysis import regime
from keel.config import Config
from keel.execution import sizing, sleeve
from keel.execution.executor import DcaSizeInvalid, _dca_budget
from keel.execution.guards import _asset, _utc_day_bounds, _utc_month_bounds
from keel.sim.account import OpenIntent, OpenPosition, SimAccount
from keel.strategy import engine, indicators_cts
from keel.strategy.backtest import (
    TAKER_FEE_PCT,
    _resolve_order,
    _rule_trading_tf,
    _stop_exit_price,
    _touches,
)
from keel.strategy.exit_policy import ExitPolicy, next_stop, policy_for, trailing_atr
from keel.strategy.promotion import SLEEVE_SELL
from keel.strategy.reduction import Holding, Lot, Reduction, SellCosts
from keel.strategy.rules.base import Rule, Setup, Signal, initial_risk_of, r_multiple_of
from keel.strategy.rules.dca import Dca
from keel.types import Candle, Granularity

__all__ = [
    "BELOW_ONE_LEG",
    "DUST_FLOOR",
    "IDLE_SPAN_MIN_HOURS",
    "MOVE_THRESHOLD_PCT",
    "SIM_FEE_SOURCE",
    "WINDOW_BARS",
    "DcaBuy",
    "DcaSell",
    "DcaSleeve",
    "SimResult",
    "SimTelemetry",
    "SimTrade",
    "SleeveSale",
    "dca_on_cadence",
    "dca_sleeve",
    "decide_sleeve_sale",
    "run",
    "sleeve_sellers",
]

#: How a simulated sale's fee is priced: the sim's own configured rate (`run`'s `fee_pct`),
#: never a venue quote and never a literal (spec §2.2). It labels the `SellCosts` a sleeve rule
#: sizes against, as `sleeve.FALLBACK_FEE_SOURCE` labels the live cycle's.
SIM_FEE_SOURCE = "sim:fee_pct"

# Floor for the rolling ONE_HOUR window size handed to `Rule.detect`/`engine.evaluate` -- large
# enough for every existing rule's longest lookback (EMA-200, 90-bar-equivalent structure) even
# when `config.market_data.history_days` is tiny. The window actually used by `run()` is
# `_window_bars(config)` (`max(WINDOW_BARS, history_days * 24)`), which for the default
# `history_days=365` grows to ~8760 hourly bars -- matching what the LIVE agent
# (`agent.run_once`) evaluates against, instead of hardcoding a much shorter, unfaithful cap.
WINDOW_BARS = 300

# Idle-span telemetry: a "no signal fired while price moved a lot" span is recorded once both
# (a) the gap since the last signal spans at least this many hours, and (b) the cumulative move
# across it exceeds MOVE_THRESHOLD_PCT (5%).
IDLE_SPAN_MIN_HOURS = 24
MOVE_THRESHOLD_PCT = Decimal("0.05")

# Issue #85: a risk-sized notional CLAMPED down to available headroom (cash / concentration /
# exposure) below this floor isn't worth opening -- fee+slippage would dominate a sub-$1 order.
# The entry is skipped, not rejected outright (matching every other clamp-not-reject in this
# module), so the next bar gets a fresh chance once headroom recovers.
DUST_FLOOR = Decimal("1")

_SECONDS_PER_HOUR = 3600
_SECONDS_PER_DAY = 86_400


@dataclass
class SimTrade:
    """One round-trip: a closed trade has every field populated; a position still open at the
    end of the simulated window is recorded with `exit_ts=exit=pnl=r_multiple=None` and
    `outcome="open"` (mirrors `strategy.backtest`'s open-position convention)."""

    asset: str
    entry_ts: int
    exit_ts: int | None
    entry: Decimal
    exit: Decimal | None
    qty: Decimal
    pnl: Decimal | None
    r_multiple: Decimal | None
    mfe: Decimal
    mae: Decimal
    outcome: str
    rule_kind: str
    cts_score: int
    entry_technique: str
    #: Per-unit `|entry_fill - setup.stop|` against the ORIGINAL stop (#820), the risk
    #: `r_multiple` divides by (times `qty`). `None` on a record with none; last and
    #: defaulted so existing constructors keep working.
    initial_risk: Decimal | None = None


@dataclass
class SimTelemetry:
    """Portfolio-level bookkeeping alongside the trade log, feeding Task 7's gap analysis."""

    bars: int = 0
    signals_emitted: int = 0
    idle_spans: list[tuple[int, int, str, Decimal]] = field(default_factory=list)
    cts_factor_populated: dict[str, int] = field(default_factory=dict)
    rejected_for_missing_input: dict[str, int] = field(default_factory=dict)
    per_bucket_pnl: dict[tuple[str, str, str], Decimal] = field(default_factory=dict)
    mae_samples: list[Decimal] = field(default_factory=list)
    mfe_giveback_samples: list[Decimal] = field(default_factory=list)


@dataclass(frozen=True)
class DcaBuy:
    """One DCA accumulation buy (#821). `decision_ts` is the hourly bar the decision was taken
    on (its UTC day is the once-per-day key); `notional` is the budgeted spend at the decision
    price (`sizing.spend(qty, setup.entry)`); `cost_usd` is the cash the fill actually took --
    the slipped next-bar open times `qty`, plus the entry fee."""

    asset: str
    rule_kind: str
    decision_ts: int
    fill_ts: int
    qty: Decimal
    notional: Decimal
    cost_usd: Decimal


@dataclass(frozen=True)
class DcaSleeve:
    """An accumulated DCA holding, marked to market (#821) -- the account sim's sleeve for one
    asset, or the edge pass's accumulation row for one accumulating rule. NOT a round trip.

    `cost_usd` is what the units STILL HELD cost, fees included, so `unrealized_pnl` is net of
    entry costs; `value_usd` is `qty * last_close`. With no distribution that is every buy's cash
    outlay, exactly as before #857.

    **The sell columns (#857, P11)** -- `distributions`, `units_sold`, `distributed_usd` (the NET
    cash the sales delivered, after their fee), `sell_fees` and `realised_pnl` (net proceeds less
    the fee-inclusive basis of the units sold) -- are zero on a sleeve nothing sold from. On the
    edge pass they sit on the SELLING rule's row (`reverse_dca`), whose own buys, qty and cost are
    zero, and the basis is FIFO over the asset's lots (`report.accumulation_table`); on the
    account sim they sit on the asset's row, and the basis is its one averaged lot's (plan R32,
    `SimAccount.reduce_dca`). Either way they are a fidelity figure of the harness, not a verdict
    about returns (spec §6, "Evidence status")."""

    buys: int
    qty: Decimal
    cost_usd: Decimal
    last_close: Decimal
    value_usd: Decimal
    unrealized_pnl: Decimal
    distributions: int = 0
    units_sold: Decimal = Decimal("0")
    distributed_usd: Decimal = Decimal("0")
    realised_pnl: Decimal = Decimal("0")
    sell_fees: Decimal = Decimal("0")

    @classmethod
    def marked(
        cls,
        buys: int,
        qty: Decimal,
        cost_usd: Decimal,
        last_close: Decimal,
        *,
        distributions: int = 0,
        units_sold: Decimal = Decimal("0"),
        distributed_usd: Decimal = Decimal("0"),
        realised_pnl: Decimal = Decimal("0"),
        sell_fees: Decimal = Decimal("0"),
    ) -> DcaSleeve:
        value = qty * last_close
        return cls(
            buys,
            qty,
            cost_usd,
            last_close,
            value,
            value - cost_usd,
            distributions=distributions,
            units_sold=units_sold,
            distributed_usd=distributed_usd,
            realised_pnl=realised_pnl,
            sell_fees=sell_fees,
        )


@dataclass(frozen=True)
class DcaSell:
    """One sleeve distribution the account sim filled (#857, P11) -- the reverse of `DcaBuy`.

    `expected_price` is the completed daily close the rule decided on, which rail 2's slicing
    reads (`sleeve.slice_qty`), as `executor.reduce` reads it live; `fill_price` is the next
    hourly bar's open less slippage. `net_usd` is `gross_usd - fee_usd`, the cash credited.
    `cost_basis` and `realised_pnl` are AVERAGE-cost, against the account's one averaged lot
    (plan R32): the FIFO-faithful figure is `report.accumulation_table`'s."""

    asset: str
    rule_kind: str
    decision_ts: int
    fill_ts: int
    qty: Decimal
    expected_price: Decimal
    fill_price: Decimal
    gross_usd: Decimal
    fee_usd: Decimal
    net_usd: Decimal
    cost_basis: Decimal
    realised_pnl: Decimal


@dataclass(frozen=True)
class SleeveSale:
    """A decided sleeve sale: the arbitration winner, its `Reduction`, and either the sleeve cap
    that refused it (`refusal`, one of `sleeve_refusal`'s answers or `BELOW_ONE_LEG`) or the one
    leg rail 2 lets it sell today (`leg_qty`, zero when refused)."""

    rule: Rule
    reduction: Reduction
    refusal: str | None
    leg_qty: Decimal


#: The sim's answer when rail 2's slicer can express no leg -- `executor.reduce`'s own
#: `rails.sleeve` word for the same case.
BELOW_ONE_LEG = "below_one_increment"


def sleeve_sellers(rules: list[Rule]) -> list[Rule]:
    """The sleeve-sell rules in `rules` (`promotion_class == SLEEVE_SELL`), in spec §3.6's fixed
    arbitration order (`sleeve.ARBITRATION_ORDER`, keyed on the kind as `agent._handle_reductions`
    keys it), ties kept in the order given."""
    order = {kind: i for i, kind in enumerate(sleeve.ARBITRATION_ORDER)}
    return sorted(
        (rule for rule in rules if rule.promotion_class == SLEEVE_SELL),
        key=lambda rule: order.get(rule.name, len(order)),
    )


def dca_on_cadence(rules: list[Rule], candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
    """The cadence half of `agent._dca_fires_today`: a `Dca` rule among `rules` is on cadence on
    this view. A `detect` that raises counts as firing -- refusing the sale is the direction that
    costs nothing, as it is live. (The ledger half, "a buy already filled today", adds nothing
    in either sim pass: both buy only on a `Dca` cadence day, so this half already covers it.)"""
    for rule in rules:
        if not isinstance(rule, Dca):
            continue
        try:
            if rule.detect(candles_by_tf) is not None:
                return True
        except Exception:  # noqa: BLE001 -- fail toward refusing the sale, as live does
            return True
    return False


def decide_sleeve_sale(
    sellers: list[Rule],
    holding: Holding,
    candles_by_tf: dict[Granularity, list[Candle]],
    costs: SellCosts,
    *,
    dca_fires_today: bool,
    last_sale_ts: Callable[[Rule], int | None],
    now_ts: int,
    max_per_order_usd: Decimal,
) -> SleeveSale | None:
    """The live pipeline's decision for one product on one day, over a sim-built `holding`
    (#857, P11): `None` when no seller fires, else the winner and what the sleeve caps and rail 2
    make of it. ONE definition for both sim passes -- the edge pass's FIFO lots and the account
    sim's own per-buy FIFO lots (`_reduction_check_holding`, #924; the account's BOOKING still
    reduces its one averaged lot, `SimAccount.reduce_dca`) -- and every step is the live one's
    own code, not a copy of it:

    1. `sellers` (from `sleeve_sellers`) are asked in arbitration order; the first `Reduction`
       wins (`agent._handle_reductions`), so there is at most one sale per product per day (R14).
       A rule that raises costs that rule only.
    2. `sleeve.sleeve_refusal` -- same-day DCA, `min_hold_days` on the lots the FIFO sale would
       consume (R12), `cooldown_days` (R15) read from `last_sale_ts`, the sim's record of the
       rule's last sale, as live reads its last `preview`/`placed` proposal.
    3. The sale is capped at the holding (`executor.reduce`'s `min(qty, holding.qty)`), then
       sliced by `sleeve.slice_qty` at the decision close with no venue increment (`None`: the
       sim has no instrument, which is `_order_spec`'s unquantized rule for a SELL). One leg is
       sold; the rest is not carried, because the rule is off cadence tomorrow -- live proposes
       one leg per day the same way.
    """
    fired: tuple[Rule, Reduction] | None = None
    for rule in sellers:
        try:
            reduction = rule.reduce_signal(holding, candles_by_tf, costs)
        except Exception:  # noqa: BLE001 -- one broken rule must not cost the other kinds
            continue
        if reduction is not None:
            fired = (rule, reduction)
            break
    if fired is None:
        return None
    rule, reduction = fired
    refusal = sleeve.sleeve_refusal(
        reduction=reduction,
        holding=holding,
        rule_kind=rule.name,
        rule_params=rule.params,
        dca_fires_today=dca_fires_today,
        last_rule_proposal_ts=last_sale_ts(rule),
        now_ts=now_ts,
    )
    if refusal is not None:
        return SleeveSale(rule, reduction, refusal, Decimal("0"))
    leg_qty, legs = sleeve.slice_qty(
        min(reduction.qty, holding.qty),
        reduction.expected_price,
        max_per_order_usd=max_per_order_usd,
        base_increment=None,
    )
    if legs == 0:
        return SleeveSale(rule, reduction, BELOW_ONE_LEG, Decimal("0"))
    return SleeveSale(rule, reduction, None, leg_qty)


@dataclass
class SimResult:
    trades: list[SimTrade]
    equity_curve: list[tuple[int, Decimal]]
    contributions: list[tuple[int, Decimal]]
    coverage: dict
    telemetry: SimTelemetry
    # DCA-sleeve holdings at the end of the run, keyed by asset (Issue #85) -- accumulated,
    # marked-to-market lots that never generate a `SimTrade` (DCA never exits by design, see the
    # module docstring); exposed here so callers/tests can see DCA and rule trades coexisting.
    dca_positions: dict[str, OpenPosition] = field(default_factory=dict)
    # Every opened AND closed order's notional (trading VOLUME, Issue #85's buys+sells
    # convention), aggregated by UTC calendar month and keyed by each month's start ts
    # (`SimAccount.monthly_volume`) -- feeds the Coinbase One tier/fee analysis (Issue #86,
    # `sim.tiers`), which needs per-month volume to compute over-cap fees correctly.
    monthly_volume: dict[int, Decimal] = field(default_factory=dict)
    # Every DCA buy, in order (#821) -- the sleeve's buy count and fee-inclusive cost basis.
    dca_buys: list[DcaBuy] = field(default_factory=list)
    # Each asset's last hourly close seen by the run: what `dca_sleeve` marks the sleeve at.
    final_prices: dict[str, Decimal] = field(default_factory=dict)
    # Every sleeve distribution filled, in order (#857, P11) -- the reverse of `dca_buys`.
    dca_sells: list[DcaSell] = field(default_factory=list)


def dca_sleeve(result: SimResult) -> dict[str, DcaSleeve]:
    """The run's DCA sleeve per asset, marked at that asset's final close (#821).

    `qty` is the account's own lot (`result.dca_positions`); the buy count comes from
    `result.dca_buys`, and the cost basis is theirs less the average-cost basis of every unit
    distributed since (`result.dca_sells`, #857), whose totals fill the sell columns. An asset
    sold down to nothing keeps its row (qty 0), so its distributions stay visible. An asset with
    a lot but no final price is marked at zero rather than silently at cost -- a missing mark
    must look like one."""
    rows: dict[str, DcaSleeve] = {}
    assets = set(result.dca_positions) | {s.asset for s in result.dca_sells}
    for asset in sorted(assets):
        lot = result.dca_positions.get(asset)
        buys = [b for b in result.dca_buys if b.asset == asset]
        sells = [s for s in result.dca_sells if s.asset == asset]
        rows[asset] = DcaSleeve.marked(
            buys=len(buys),
            qty=Decimal("0") if lot is None else lot.qty,
            cost_usd=sum((b.cost_usd for b in buys), Decimal("0"))
            - sum((s.cost_basis for s in sells), Decimal("0")),
            last_close=result.final_prices.get(asset, Decimal("0")),
            distributions=len(sells),
            units_sold=sum((s.qty for s in sells), Decimal("0")),
            distributed_usd=sum((s.net_usd for s in sells), Decimal("0")),
            realised_pnl=sum((s.realised_pnl for s in sells), Decimal("0")),
            sell_fees=sum((s.fee_usd for s in sells), Decimal("0")),
        )
    return rows


@dataclass
class _Held:
    """The sim-local record of a currently open position -- `SimAccount.OpenPosition` doesn't
    carry `target`, the originating `Rule`/`Setup`, or the entry `Signal`'s CTS grade, all needed
    for exit resolution and the closed `SimTrade`'s audit fields."""

    rule: Rule
    setup: Setup
    entry_ts: int
    entry_fill: Decimal
    qty: Decimal
    cts_score: int
    entry_technique: str
    #: The protective stop CURRENTLY in force -- starts at the setup's own stop and is
    #: ratcheted by the rule's exit policy (#442, `strategy.exit_policy`) at each bar the
    #: position survives. Touch checks run against THIS level; `setup.stop` stays the
    #: ORIGINAL risk reference the closed trade's R-multiple divides by. Required, not
    #: defaulted: a silent 0 stop would be a stop that never triggers.
    stop: Decimal
    #: The rule's stop-management policy, resolved ONCE here at open (`policy_for(rule)`)
    #: rather than per bar in `_process_held` -- matching `strategy.backtest`'s
    #: once-per-run resolution. A rule's params never mutate mid-run, and knob-less
    #: rules share the `EXIT_POLICY_OFF` singleton either way.
    policy: ExitPolicy
    mfe: Decimal = Decimal("0")
    mae: Decimal = Decimal("0")


@dataclass
class _IdleAnchor:
    start_ts: int
    start_price: Decimal


def _is_dca_setup(setup: Setup) -> bool:
    """Mirrors `engine._is_market_buy_class`: a no-stop, market-buy accumulation setup."""
    context = setup.context
    return bool(context.get("no_stop")) or context.get("order_class") == "dca"


def _union_hourly_ts(
    candles_by_asset: dict[str, dict[Granularity, list[Candle]]], start_ts: int, end_ts: int
) -> list[int]:
    ts_set: set[int] = set()
    for per_tf in candles_by_asset.values():
        for candle in per_tf.get(Granularity.ONE_HOUR, []):
            if start_ts <= candle.ts <= end_ts:
                ts_set.add(candle.ts)
    return sorted(ts_set)


def _window_bars(config: Config) -> int:
    """Rolling ONE_HOUR window length for the account pass: `history_days * 24` hourly bars/day
    (`ONE_HOUR` is the trading TF), floored at `WINDOW_BARS` so tiny configs still warm up
    indicators. Mirrors the LIVE agent's `agent.run_once`, which evaluates against
    `config.market_data.history_days` (default 365 -> ~8760 hourly bars) -- deriving this from
    config keeps the account pass faithful to production instead of a hardcoded, much shorter
    cap."""
    return max(WINDOW_BARS, config.market_data.history_days * 24)


def _window_1h(hourly: list[Candle], idx: int, window_bars: int) -> list[Candle]:
    start = max(0, idx - window_bars + 1)
    return hourly[start : idx + 1]


def run(
    rules: list[Rule],
    candles_by_asset: dict[str, dict[Granularity, list[Candle]]],
    config: Config,
    start_ts: int,
    end_ts: int,
    monthly_contribution: Decimal,
    fee_pct: Decimal = TAKER_FEE_PCT,
    slippage_pct: Decimal = Decimal("0.0005"),
    monthly_volume_cap: Decimal | None = None,
) -> SimResult:
    """Simulate `rules` (each bound to one asset via `Rule.product_id`) over `candles_by_asset`.

    See the module docstring for the loop's exact semantics (no-lookahead window assembly,
    conservative stop-vs-target resolution, next-bar-open fills, monthly contributions, daily
    equity sampling, idle-span telemetry).

    `monthly_volume_cap` (Issue #86, Coinbase One tier/fee analysis): when `None` (default), the
    account trades naturally -- sizing is clamped only by `SimAccount.max_affordable_notional`'s
    existing six caps (cash / concentration / exposure / etc, Issue #85), which can push a
    month's trading VOLUME (buys+sells) past any particular subscription tier's monthly
    volume allowance. When set to a `Decimal`, every order's clamp ALSO floors headroom to the
    volume
    remaining before that ceiling this UTC month, so the account never trades enough in a month
    to exceed `monthly_volume_cap` -- i.e. it never owes a fee under a tier whose free allowance
    equals `monthly_volume_cap`. This throttles both the RULE-slot clamp and the DCA sleeve (DCA
    is skipped for the cycle, not partially filled, if it would breach the remaining volume --
    consistent with DCA's fixed-budget-per-cycle semantics elsewhere in this module).
    """
    account = SimAccount(fee_pct, slippage_pct)
    window_bars = _window_bars(config)
    telemetry = SimTelemetry()
    trades: list[SimTrade] = []
    equity_curve: list[tuple[int, Decimal]] = []
    contributions: list[tuple[int, Decimal]] = []

    rules_by_asset: dict[str, list[Rule]] = defaultdict(list)
    for rule in rules:
        rules_by_asset[_asset(rule.product_id)].append(rule)

    hourly_by_asset: dict[str, list[Candle]] = {
        asset: per_tf.get(Granularity.ONE_HOUR, []) for asset, per_tf in candles_by_asset.items()
    }
    hourly_index: dict[str, dict[int, int]] = {
        asset: {c.ts: i for i, c in enumerate(series)} for asset, series in hourly_by_asset.items()
    }
    daily_by_asset: dict[str, list[Candle]] = {
        asset: per_tf.get(Granularity.ONE_DAY, []) for asset, per_tf in candles_by_asset.items()
    }
    daily_ts_by_asset: dict[str, list[int]] = {
        asset: [c.ts for c in series] for asset, series in daily_by_asset.items()
    }

    # Keyed by (asset, rule_name): one position per RULE per asset, so an asset can hold
    # several concurrent rule positions. Deliberately NOT multiple positions from the SAME rule
    # -- that is pyramiding (§26.1), a separate feature with its own exposure-rail implications.
    held: dict[tuple[str, str], _Held] = {}
    latest_price: dict[str, Decimal] = {}
    # (asset, rule_name, utc_day) of every DCA decision taken -- at most one per day (#821).
    dca_decided: set[tuple[str, str, int]] = set()
    dca_buys: list[DcaBuy] = []
    # (asset, utc_day) of every sleeve-sale decision taken -- one per product per day (R14) --
    # the distributions filled, and each sleeve rule's last sale (R15's cooldown reads it).
    sleeve_decided: set[tuple[str, int]] = set()
    dca_sells: list[DcaSell] = []
    last_sale: dict[int, int] = {}
    idle: dict[str, _IdleAnchor] = {}

    last_month_start: int | None = None
    last_day_start: int | None = None

    timestamps = _union_hourly_ts(candles_by_asset, start_ts, end_ts)
    telemetry.bars = len(timestamps)

    for t in timestamps:
        month_start, _ = _utc_month_bounds(t)
        if month_start != last_month_start:
            account.deposit(monthly_contribution, t)
            contributions.append((t, monthly_contribution))
            last_month_start = month_start

        # Rail 11's inputs, refreshed BEFORE any signal evaluation this bar -- mirrors
        # `agent.run_once`'s reconcile -> equity -> entries ordering (there is no reconcile step
        # here; the sim has no broker to reconcile against). `latest_price` at this point in the
        # loop still holds the PREVIOUS bar's closes (it is only updated for an asset further
        # down, inside this same iteration's per-asset loop) -- that is deliberate, not an
        # off-by-one: marking to market against bar `t`'s own not-yet-seen close would be
        # lookahead, letting `can_open`'s drawdown check see price information this bar's signals
        # haven't been evaluated against yet. `latest_price` is empty on the very first bar (no
        # asset has been priced yet); `mark_to_market` sums over `positions`/`dca_positions`,
        # both empty at that point too (nothing can have opened before the first bar's signals
        # are even evaluated), so an empty `latest_price` is safe here, not merely assumed to be.
        account.update_equity(latest_price, t)

        for asset, hourly in hourly_by_asset.items():
            idx = hourly_index[asset].get(t)
            if idx is None:
                continue  # this asset has no bar at this timestamp

            current = hourly[idx]
            latest_price[asset] = current.close

            daily_idx = bisect.bisect_right(daily_ts_by_asset[asset], t)
            candles_by_tf: dict[Granularity, list[Candle]] = {
                Granularity.ONE_HOUR: _window_1h(hourly, idx, window_bars),
                Granularity.ONE_DAY: daily_by_asset[asset][:daily_idx],
            }

            # The RULE slot's exit check runs first and independently of DCA (Issue #85): DCA
            # positions are never in `held`, so this only ever resolves a risk-defined position.
            # Snapshot the keys first: `_process_held` mutates `held` when a position closes.
            for key in [key for key in held if key[0] == asset]:
                _process_held(
                    key,
                    idx,
                    hourly,
                    current,
                    candles_by_tf,
                    held,
                    account,
                    config,
                    trades,
                    telemetry,
                )

            asset_rules = rules_by_asset.get(asset)
            if not asset_rules:
                continue

            signals = engine.evaluate(asset_rules, candles_by_tf)
            telemetry.signals_emitted += len(signals)
            _record_cts_telemetry(signals, candles_by_tf, telemetry)

            # DCA runs regardless of the RULE slot's state -- a DCA sleeve keeps buying on its
            # own cadence even while a rule position is held -- but decides once per UTC day.
            _process_dca_signals(
                asset,
                idx,
                hourly,
                signals,
                account,
                config,
                t,
                monthly_volume_cap,
                decided=dca_decided,
                buys=dca_buys,
            )

            fired = _process_rule_signals(
                asset,
                idx,
                hourly,
                signals,
                rules_by_asset[asset],
                account,
                config,
                latest_price,
                held,
                t,
                monthly_volume_cap,
            )
            _track_idle(asset, current, fired, idle, telemetry)

            # Sleeve distributions LAST, after the buys, as `agent.run_once` runs
            # `_handle_reductions` last (#857) -- see `_process_reductions`.
            _process_reductions(
                asset,
                idx,
                hourly,
                asset_rules,
                candles_by_tf,
                account,
                config,
                t,
                monthly_volume_cap,
                decided=sleeve_decided,
                buys=dca_buys,
                sells=dca_sells,
                last_sale=last_sale,
            )

        day_start, _ = _utc_day_bounds(t)
        if day_start != last_day_start:
            equity_curve.append((t, account.mark_to_market(latest_price)))
            last_day_start = day_start

    for (asset, _slot), h in held.items():
        trades.append(
            SimTrade(
                asset=asset,
                entry_ts=h.entry_ts,
                exit_ts=None,
                entry=h.entry_fill,
                exit=None,
                qty=h.qty,
                pnl=None,
                r_multiple=None,
                mfe=h.mfe,
                mae=h.mae,
                outcome="open",
                rule_kind=h.rule.name,
                cts_score=h.cts_score,
                entry_technique=h.entry_technique,
            )
        )

    return SimResult(
        trades=trades,
        equity_curve=equity_curve,
        contributions=contributions,
        coverage={},
        telemetry=telemetry,
        dca_positions=dict(account.dca_positions),
        monthly_volume=account.monthly_volume(),
        dca_buys=dca_buys,
        final_prices=dict(latest_price),
        dca_sells=dca_sells,
    )


# ---------------------------------------------------------------------------
# Per-bar processing
# ---------------------------------------------------------------------------


def _process_held(
    key: tuple[str, str],
    idx: int,
    hourly: list[Candle],
    current: Candle,
    candles_by_tf: dict[Granularity, list[Candle]],
    held: dict[tuple[str, str], _Held],
    account: SimAccount,
    config: Config,
    trades: list[SimTrade],
    telemetry: SimTelemetry,
) -> None:
    """Resolve `asset`'s held RULE position against the current bar: conservative intrabar
    stop-vs-target resolution first (via `strategy.backtest`'s reused `_touches`/`_resolve_order`
    -- with no finer-than-hourly series ever available here, ambiguity always resolves to the
    stop, exactly matching `backtest.py`'s documented no-finer-data fallback), then
    `Rule.exit_signal` if neither level was touched. `held` only ever contains risk-defined RULE
    positions (Issue #85) -- DCA setups are filtered out before ever reaching `held` (see
    `_process_rule_signals`), so every position resolved here has a real stop/target."""
    asset, slot = key
    h = held[key]
    setup = h.setup

    h.mfe = max(h.mfe, current.high - h.entry_fill)
    h.mae = max(h.mae, h.entry_fill - current.low)

    exit_price: Decimal | None = None

    # The MANAGED stop (the level carried into this bar), not the setup's original: the
    # rule's exit policy (#442) may have ratcheted it on a prior bar. R-multiples still
    # divide by the ORIGINAL risk (`risk` below reads `setup.stop`). The stop's exit fill
    # comes from the shared `strategy.backtest._stop_exit_price`: `None` when the bar
    # never reached it, the bar's OPEN when it gapped entirely through it.
    stop_exit = _stop_exit_price(current, h.stop)
    stop_touched = stop_exit is not None
    target_touched = _touches(current, setup.target)
    if stop_touched and target_touched:
        order = _resolve_order(idx, hourly, None, {"target": setup.target, "stop": h.stop})
        # `stop_exit` is exactly `h.stop` in this branch: a bar that gapped through the
        # stop cannot also touch the target (target > stop for a long), so the
        # both-touched case is always an in-range touch.
        exit_price = setup.target if order == "target" else stop_exit
    elif stop_touched:
        exit_price = stop_exit
    elif target_touched:
        exit_price = setup.target

    if exit_price is None and h.rule.exit_signal(setup, candles_by_tf):
        exit_price = current.close

    if exit_price is None:
        # Stop management runs at BAR END, on the bar that just completed, and the
        # resulting level binds from the NEXT bar (#442 -- `strategy.exit_policy`'s
        # module docstring states the no-lookahead/live-parity sequencing). An OFF
        # policy (turtle by design, every rule without the knobs) leaves `h.stop`
        # untouched, so only a trailing arm pays for the ATR read. The ATR window is
        # the RULE's own trading timeframe (ONE_HOUR for both knob-carrying families),
        # resolved the same way `strategy.backtest` resolves it.
        policy = h.policy
        series = candles_by_tf.get(_rule_trading_tf(h.rule))
        h.stop = next_stop(
            policy,
            h.entry_fill,
            setup.stop,
            h.stop,
            current,
            trailing_atr(series, policy.atr_period)
            if (policy.trail_atr_mult is not None and series is not None)
            else None,
        )
        return

    pnl = account.close(asset, exit_price, current.ts, slot=slot)
    exit_fill = exit_price * (Decimal(1) - account.slippage_pct)

    # Rail 16: feed the closed trade to the sim-side streak producer, so a `keel simulate` sweep
    # over `max_consecutive_losses` actually changes the backtest. `held` is RULE-slot only (DCA
    # never exits here, Issue #85), so `is_dca` is always False -- passed explicitly anyway to
    # keep the call site honest against `execution.streak.record_closed_trade`'s signature.
    account.record_trade_outcome(pnl, config, current.ts, is_dca=False)

    initial_risk = initial_risk_of(h.entry_fill, setup.stop)
    r_multiple = r_multiple_of(pnl, initial_risk, h.qty)
    outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "scratch"

    trades.append(
        SimTrade(
            asset=asset,
            entry_ts=h.entry_ts,
            exit_ts=current.ts,
            entry=h.entry_fill,
            exit=exit_fill,
            qty=h.qty,
            pnl=pnl,
            r_multiple=r_multiple,
            mfe=h.mfe,
            mae=h.mae,
            outcome=outcome,
            rule_kind=h.rule.name,
            cts_score=h.cts_score,
            entry_technique=h.entry_technique,
            initial_risk=initial_risk,
        )
    )

    telemetry.mae_samples.append(h.mae)
    telemetry.mfe_giveback_samples.append(max(Decimal(0), h.mfe - max(pnl, Decimal(0))))

    condition = regime.detect_condition(candles_by_tf[Granularity.ONE_HOUR])
    bucket_key = (h.rule.name, asset, condition.value)
    telemetry.per_bucket_pnl[bucket_key] = (
        telemetry.per_bucket_pnl.get(bucket_key, Decimal(0)) + pnl
    )

    del held[key]


def _record_cts_telemetry(
    signals: list[Signal], candles_by_tf: dict[Granularity, list[Candle]], telemetry: SimTelemetry
) -> None:
    """For *every* ENTER signal `evaluate()` emits this bar (DCA or rule-class, opened or not),
    tally which CTS confluence factors were present/absent -- feeds Task 7's "unfed CTS factors"
    gap-analysis detector. Runs once per bar regardless of the RULE slot's held/flat state."""
    window_1h = candles_by_tf[Granularity.ONE_HOUR]
    for signal in signals:
        setup = signal.setup
        if setup is None:
            continue
        cts_result = indicators_cts.score(engine.assemble_cts_context(setup, window_1h))
        for factor in cts_result.factors:
            bucket = (
                telemetry.cts_factor_populated
                if factor.present
                else telemetry.rejected_for_missing_input
            )
            bucket[factor.name] = bucket.get(factor.name, 0) + 1


def _process_dca_signals(
    asset: str,
    idx: int,
    hourly: list[Candle],
    signals: list[Signal],
    account: SimAccount,
    config: Config,
    now_ts: int,
    monthly_volume_cap: Decimal | None = None,
    *,
    decided: set[tuple[str, str, int]],
    buys: list[DcaBuy],
) -> None:
    """Buy every DCA-class signal this bar into the separate DCA sleeve (`account.dca_positions`,
    via `account.open(..., dca=True)`), regardless of whether the asset's RULE slot is currently
    held -- DCA is scheduled accumulation on its own cadence (`strategy/rules/dca.py`'s `detect`
    already gates *when* it fires), not a risk-defined trade competing for the rule slot. Skips a
    signal only when it fails `can_open`'s hard safety veto, there's no next bar to fill at, or
    (Issue #86) it would push this UTC month's trading volume past `monthly_volume_cap` -- DCA is
    SKIPPED for the cycle in that case, not partially filled, matching its fixed-budget-per-cycle
    semantics (unlike the RULE slot's risk-sized notional, which IS clamped, see
    `_process_rule_signals`).

    **Once per UTC day (#821).** A DCA rule decides on daily candles, so its signal repeats on
    every hourly bar of the day. `decided` holds `(asset, rule, utc_day)` for every decision
    already taken; a repeat is dropped before anything else runs. The day is marked when the
    decision is TAKEN, not only when it fills: a vetoed or unfillable buy is that day's
    decision, as it is live, where the agent trades once per UTC day."""
    day = now_ts // _SECONDS_PER_DAY
    for signal in signals:
        setup = signal.setup
        if setup is None or not _is_dca_setup(setup):
            continue
        key = (asset, signal.rule_name, day)
        if key in decided:
            continue
        decided.add(key)

        try:
            # Shares `executor._dca_budget` with the live and paper paths rather than
            # re-deriving the same predicate here (orchestrator ruling 2026-09-27): `size_usd`
            # ABSENT (missing or `None`) falls back to `config.dca.budget_usd`; a `size_usd`
            # that is PRESENT but not usable (0, negative, non-finite, a bool, non-numeric)
            # raises `DcaSizeInvalid` instead, and this cycle's decision is SKIPPED (`continue`)
            # rather than sized from a config value the rule never referenced -- exactly like
            # the live/paper skip, so all three paths agree on what "usable" means.
            budget, _source = _dca_budget(setup.context, config.dca.budget_usd)
            qty = sizing.dca_size(budget, setup.entry)
        except DcaSizeInvalid, ValueError:
            continue

        notional = sizing.spend(qty, setup.entry)

        if monthly_volume_cap is not None:
            remaining = monthly_volume_cap - account.month_volume(now_ts)
            if notional > remaining:
                continue  # would exceed the tier's monthly volume allowance -- skip this cycle

        intent = OpenIntent(
            asset=asset,
            qty=qty,
            entry=setup.entry,
            stop=None,
            notional=notional,
            is_dca=True,
            rule_kind=signal.rule_name,
        )

        ok, _reasons = account.can_open(intent, config, now_ts)
        if not ok:
            continue

        fill_idx = idx + 1
        if fill_idx >= len(hourly):
            continue  # no next bar to fill at -- the signal is lost, not a rejection

        fill_bar = hourly[fill_idx]
        cash_before = account.cash_usdc
        account.open(intent, fill_bar.open, fill_bar.ts, dca=True)
        buys.append(
            DcaBuy(
                asset=asset,
                rule_kind=signal.rule_name,
                decision_ts=now_ts,
                fill_ts=fill_bar.ts,
                qty=qty,
                notional=notional,
                cost_usd=cash_before - account.cash_usdc,
            )
        )


def _reduction_check_holding(
    asset: str, product_id: str, buys: list[DcaBuy], sells: list[DcaSell], fee_pct: Decimal
) -> Holding:
    """The account sim's REFUSAL-check `Holding` for `asset` (#924): every one of its DCA buys,
    FIFO by fill time, each `opened_at=buy.fill_ts` and its qty reduced by the cumulative units
    already sold for this asset (`sells`), FIFO -- a lot sold down to nothing is dropped, exactly
    as `report._accumulate_asset` drops one. This mirrors the edge pass's per-buy pool instead of
    the account's one averaged lot, so `sleeve.sleeve_refusal`'s `min_hold_days` walk (over
    `holding.fifo_legs`) sees the age of every lot the sale would actually reach, not only the
    oldest buy's -- the bug that let the sim book a sale live's own `sleeve_refusal` would refuse.

    Each lot's `entry_fill`/`entry_fee` is recovered from the buy's own fill economics
    (`DcaBuy.cost_usd == qty * entry_fill * (1 + fee_pct)`, `_process_dca_signals`'s charge)."""
    already_sold = sum((s.qty for s in sells if s.asset == asset), Decimal("0"))
    lots: list[Lot] = []
    next_id = 0
    for buy in buys:
        if buy.asset != asset:
            continue
        take = min(buy.qty, already_sold)
        already_sold -= take
        remaining = buy.qty - take
        if remaining <= 0:
            continue
        entry_fill = buy.cost_usd / (buy.qty * (Decimal(1) + fee_pct))
        lots.append(
            Lot(
                position_id=next_id,
                rule_name=buy.rule_kind,
                opened_at=buy.fill_ts,
                qty=remaining,
                entry_fill=entry_fill,
                entry_fee=entry_fill * buy.qty * fee_pct,
                realized_qty=take,
            )
        )
        next_id += 1
    return Holding(product_id, tuple(lots))


def _process_reductions(
    asset: str,
    idx: int,
    hourly: list[Candle],
    asset_rules: list[Rule],
    candles_by_tf: dict[Granularity, list[Candle]],
    account: SimAccount,
    config: Config,
    now_ts: int,
    monthly_volume_cap: Decimal | None = None,
    *,
    decided: set[tuple[str, int]],
    buys: list[DcaBuy],
    sells: list[DcaSell],
    last_sale: dict[int, int],
) -> None:
    """The reverse path (#857, P11): sell at most one sleeve distribution of `asset` this UTC
    day from the DCA sleeve, the way `agent._handle_reductions` proposes one live.

    **Where it runs.** After the asset's buys, as the live cycle runs `_handle_reductions` last
    (`agent.run_once`). The plan drafted it before `_process_dca_signals`; the order cannot
    change a number either way, because a sale is refused on any day a DCA is on cadence or has
    bought (the same-day-DCA cap), so no day has both.

    **What it decides** -- `decide_sleeve_sale`, the pipeline shared with the edge pass: nothing
    held, nothing asked; the sleeve-sell rules in arbitration order; `sleeve.sleeve_refusal`
    (same-day DCA from `dca_on_cadence` -- live's ledger half, a buy already filled today, adds
    nothing here, because the sim buys only on a cadence day; `min_hold_days`; `cooldown_days`
    from `last_sale`); `sleeve.slice_qty` at `config.caps.max_per_order_usd` --
    rail 2, from the same config the rails read. `decided` takes the day once a rule FIRES,
    whatever becomes of it (R14): a refused or unfillable distribution is lost, not carried to
    the next hour or day, as live.

    **What it books.** The one leg fills at the next hourly bar's open less slippage and pays
    this account's fee (`SimAccount.reduce_dca`). Like a DCA buy (#86), a leg whose notional at
    the decision price would push the month past `monthly_volume_cap` is skipped, not partly
    filled.

    **The refusal reads per-buy lots; the booking is average-cost (#924, plan R32).**
    `decide_sleeve_sale` is asked over `_reduction_check_holding`'s FIFO lots -- one per DCA buy,
    each aged from its own fill -- so `min_hold_days` and `cooldown_days` see exactly what live's
    `sleeve.sleeve_refusal` would, over the same tranches `book_exit` would actually walk. Once
    a sale clears that check, it is BOOKED against `SimAccount.dca_positions[asset]`, the
    account's one averaged lot (Issue #85: the sleeve cannot be re-plumbed to FIFO lots without
    being out of proportion to a fidelity test) -- so the realised P&L `reduce_dca` returns is
    AVERAGE-cost, not FIFO. The FIFO-faithful figure is `report.accumulation_table`'s row, which
    carries the pinned hand computation.
    """
    day = now_ts // _SECONDS_PER_DAY
    lot = account.dca_positions.get(asset)
    sellers = sleeve_sellers(asset_rules)
    if not sellers or lot is None or lot.qty <= 0 or (asset, day) in decided:
        return
    holding = _reduction_check_holding(asset, sellers[0].product_id, buys, sells, account.fee_pct)
    fill_idx = idx + 1
    fill_bar = hourly[fill_idx] if fill_idx < len(hourly) else None
    sale = decide_sleeve_sale(
        sellers,
        holding,
        candles_by_tf,
        SellCosts(account.fee_pct, account.slippage_pct, SIM_FEE_SOURCE),
        dca_fires_today=dca_on_cadence(asset_rules, candles_by_tf),
        last_sale_ts=lambda rule: last_sale.get(id(rule)),
        now_ts=now_ts if fill_bar is None else fill_bar.ts,
        max_per_order_usd=config.caps.max_per_order_usd,
    )
    if sale is None:
        return
    decided.add((asset, day))
    if sale.refusal is not None or fill_bar is None:
        return  # refused, or no next bar to fill at -- the distribution is lost, not carried
    leg = sale.leg_qty
    if monthly_volume_cap is not None:
        remaining = monthly_volume_cap - account.month_volume(now_ts)
        if leg * sale.reduction.expected_price > remaining:
            return
    fill_price = fill_bar.open * (Decimal(1) - account.slippage_pct)
    realised_before = account.realized_pnl
    net, fee = account.reduce_dca(asset, leg, fill_price, fill_bar.ts, account.fee_pct)
    last_sale[id(sale.rule)] = fill_bar.ts
    sells.append(
        DcaSell(
            asset=asset,
            rule_kind=sale.rule.name,
            decision_ts=now_ts,
            fill_ts=fill_bar.ts,
            qty=leg,
            expected_price=sale.reduction.expected_price,
            fill_price=fill_price,
            gross_usd=net + fee,
            fee_usd=fee,
            net_usd=net,
            cost_basis=leg * lot.entry_fill * (Decimal(1) + account.fee_pct),
            realised_pnl=account.realized_pnl - realised_before,
        )
    )


def _process_rule_signals(
    asset: str,
    idx: int,
    hourly: list[Candle],
    signals: list[Signal],
    asset_rules: list[Rule],
    account: SimAccount,
    config: Config,
    latest_price: dict[str, Decimal],
    # Keyed by (asset, rule_name) -- one slot per RULE per asset, matching `run()`'s own `held`
    # and `_process_dca_signals`. This said `dict[str, _Held]`, which no caller ever passed and
    # which contradicted both the `(asset, signal.rule_name)` membership test and the assignment
    # under the same key in this function's body.
    held: dict[tuple[str, str], _Held],
    now_ts: int,
    monthly_volume_cap: Decimal | None = None,
) -> bool:
    """Open at most one risk-defined RULE position for `asset` this bar from `signals` (only
    called while `asset`'s rule slot is flat -- DCA-class signals are handled separately by
    `_process_dca_signals` and never compete for this slot).

    The risk-sized notional (`execution.sizing.size`) is CLAMPED down to
    `account.max_affordable_notional()` -- the tightest of available USDC cash, per-asset
    concentration headroom, and total-exposure headroom -- instead of being rejected outright
    when it exceeds one of those (Issue #85); `can_open` remains the hard safety veto a clamped
    intent must still pass. A signal is skipped (not opened) only if the clamped notional falls
    below `DUST_FLOOR`, or the clamped intent still somehow fails `can_open`, or there's no next
    bar to fill at.

    `monthly_volume_cap` (Issue #86), when set, additionally floors the clamp to the volume
    remaining before that ceiling this UTC month (`account.max_affordable_notional`'s own
    `monthly_volume_cap` param) -- see `run()`'s docstring.

    Returns `True` iff `signals` contained at least one non-DCA (rule-class) ENTER signal this
    bar (opened or not) -- the idle-span tracker resets its anchor whenever a rule signal fires,
    whether or not it was ultimately filled. DCA firing on its own cadence deliberately does NOT
    count here: a regular DCA heartbeat shouldn't mask a genuine rule-signal drought.
    """
    rules_by_name = {rule.name: rule for rule in asset_rules}
    rule_signal_fired = False

    for signal in signals:
        setup = signal.setup
        if setup is None or _is_dca_setup(setup):
            continue
        rule_signal_fired = True

        if (asset, signal.rule_name) in held:
            continue  # one position per RULE per asset; re-entry waits for this one to close

        rule = rules_by_name.get(signal.rule_name)
        if rule is None:
            continue

        try:
            equity = account.mark_to_market(latest_price)
            qty = sizing.size(equity, config.risk_pct, setup.entry, setup.stop)
        except ValueError:
            continue

        risk_notional = sizing.spend(qty, setup.entry)
        headroom = account.max_affordable_notional(
            asset, config, now_ts, monthly_volume_cap=monthly_volume_cap
        )
        clamped_notional = min(risk_notional, headroom)
        if clamped_notional < DUST_FLOOR:
            continue
        if clamped_notional < risk_notional:
            qty = clamped_notional / setup.entry

        intent = OpenIntent(
            asset=asset,
            qty=qty,
            entry=setup.entry,
            stop=setup.stop,
            notional=clamped_notional,
            is_dca=False,
            rule_kind=rule.name,
        )

        ok, _reasons = account.can_open(intent, config, now_ts)
        if not ok:
            continue

        fill_idx = idx + 1
        if fill_idx >= len(hourly):
            continue  # no next bar to fill at -- the signal is lost, not a rejection

        fill_bar = hourly[fill_idx]
        account.open(intent, fill_bar.open, fill_bar.ts, slot=signal.rule_name)
        pos = account.positions[(asset, signal.rule_name)]
        held[(asset, signal.rule_name)] = _Held(
            rule=rule,
            setup=setup,
            entry_ts=pos.entry_ts,
            entry_fill=pos.entry_fill,
            qty=pos.qty,
            cts_score=signal.cts_score,
            entry_technique=signal.entry_technique,
            stop=setup.stop,
            policy=policy_for(rule),
        )

    return rule_signal_fired


def _track_idle(
    asset: str,
    current: Candle,
    fired: bool,
    idle: dict[str, _IdleAnchor],
    telemetry: SimTelemetry,
) -> None:
    """Record an idle span once a signal-free gap spans at least `IDLE_SPAN_MIN_HOURS` AND the
    cumulative move since the anchor exceeds `MOVE_THRESHOLD_PCT`; a fired signal always resets
    the anchor to the current bar."""
    anchor = idle.get(asset)
    if anchor is None or fired:
        idle[asset] = _IdleAnchor(start_ts=current.ts, start_price=current.close)
        return

    if anchor.start_price == 0:
        return

    elapsed_hours = (current.ts - anchor.start_ts) / _SECONDS_PER_HOUR
    if elapsed_hours < IDLE_SPAN_MIN_HOURS:
        return

    move_pct = abs(current.close - anchor.start_price) / anchor.start_price
    if move_pct > MOVE_THRESHOLD_PCT:
        telemetry.idle_spans.append((anchor.start_ts, current.ts, asset, move_pct))
        idle[asset] = _IdleAnchor(start_ts=current.ts, start_price=current.close)
