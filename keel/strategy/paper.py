"""Forward paper-trading simulator.

Consumes `Signal`s (task 1's shared type, `keel.strategy.rules.base.Signal`) as
plain input -- this module does **not** import `engine.py` (task 7 builds in
parallel with this one, per the Phase 2 wave-C split; `evaluate()` there is what
produces the `Signal`s fed in here). Given a stream of `Signal`s and candle data,
`PaperTrader` simulates fills (entry, then stop/target/MFE/MAE tracking to a close,
mirroring `backtest.py`'s per-trade bookkeeping) and journals every fill to
`orders(mode='paper')` via `Repository`. No live order placement ever happens here
(Phase 3 `execution/*` owns that).

**Schema note:** `orders.rule_id` is an `INTEGER` foreign key into the `rules` table. A paper
`Signal` carries `rule_id` only when it was emitted for a rule reconstructed from a persisted
`rules` row (`agent._build_rule`) -- which is always true for a real paper cycle (paper mode
loads its rules the same way live mode does, from `repo.get_rules("paper")`), so the FK is
satisfied by construction; a hand-built `Signal` with no `rule_id` (most tests) still writes
`NULL`, exactly as before. Either way, `rule_name` plus this module's own reconstruction fields
(`role`, `entry_order_id`, pnl/mfe/mae/outcome/etc.) are still JSON-encoded in
`orders.raw_response` -- the column live trading reserves for a broker's raw response, unused for
paper fills -- since `rule_id` alone doesn't carry that bookkeeping. `track_record()` reads that
back out (via `Repository.get_orders(mode='paper')`, P3 Task 1) to reconstruct `Trade`s and
aggregates them via the shared `strategy.stats.summarize` helper into the same `BacktestResult`
shape `backtest.py` produces, so paper and historical stats are directly comparable.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import Decimal

from keel_core.telemetry import log_event

from keel.data.repository import Repository
from keel.strategy.backtest import TAKER_FEE_PCT
from keel.strategy.rules.base import Action, Setup, Signal, Trade
from keel.strategy.stats import BacktestResult, summarize
from keel.types import Candle, Side

logger = logging.getLogger(__name__)

# Position sizing (money management) is out of scope here, same as backtest.py:
# every paper trade uses a fixed 1-unit notional, sufficient for win-rate/
# expectancy/drawdown/R-multiple stats.
_QTY = Decimal(1)
# The paper account's execution friction. TAKER, because `PaperTrader` fills a `Signal` against
# the next bar the way `backtest` does -- marketable, crossing the spread.
#
# ⚠️ THIS ONE IS NOT RETROACTIVE, and that asymmetry matters when reading the paper record.
# Fees here are charged at fill time and journalled into `orders(mode='paper')` as realized
# cash; changing the rate changes what the paper account records **from the next fill onward**
# and rewrites nothing already stored. So the paper-forward's history is now spliced: fills
# journalled before #247 were priced at the maker rate (0.006) and are optimistic by ~1.2% of
# notional per round trip; fills after it are priced correctly. `track_record()` pools both,
# which means its stats stay mildly flattered until the pre-#247 fills age out of the window.
# That splice is deliberate -- restating a journalled account's realized cash would be
# falsifying its own audit trail, which is a worse defect than the one being fixed.
_DEFAULT_FEE_PCT = TAKER_FEE_PCT
_DEFAULT_SLIPPAGE_PCT = Decimal("0.0005")


@dataclass
class _OpenPaperPosition:
    rule_name: str
    product_id: str
    setup: Setup
    entry_order_id: int
    entry_fill: Decimal
    entry_ts: int
    qty: Decimal = _QTY
    mfe: Decimal = Decimal(0)
    mae: Decimal = Decimal(0)
    costed: bool = False
    """Whether this position's entry was debited against synthetic cash.

    Cash can be seeded (`seed_cash`) at any time relative to a position's open, so the
    "is cash seeded?" check at entry and at exit can disagree for the SAME position (opened
    while unseeded, then seeded before it closes). Without this flag, `_close` would credit
    an exit that was never debited, and `equity()` would mark a position on top of cash that
    never paid for it -- both manufacture equity out of nothing. Recording, at open time,
    whether a debit actually happened keeps the close-side credit and the equity mark
    correct-by-construction regardless of seed timing.
    """
    #: The entry order's `rule_id` (its DB row's real value, `None` if it had none) -- carried
    #: so the paired exit order writes the SAME `rule_id`, mirroring how `rule_name` above is
    #: taken from the position rather than re-read off the exit signal.
    rule_id: int | None = None


def _touches(candle: Candle, price: Decimal) -> bool:
    return candle.low <= price <= candle.high


class PaperTrader:
    """Simulates paper fills for `Signal`s and journals them to `orders(mode='paper')`.

    Tracks at most one open paper position per `product_id` (no overlap): a second
    ENTER `Signal` for an instrument that already has an open paper position is
    ignored.
    """

    def __init__(
        self,
        repo: Repository,
        fee_pct: Decimal = _DEFAULT_FEE_PCT,
        slippage_pct: Decimal = _DEFAULT_SLIPPAGE_PCT,
    ) -> None:
        self._repo = repo
        self._fee_pct = fee_pct
        self._slippage_pct = slippage_pct
        self._open: dict[str, _OpenPaperPosition] = {}
        self._ledger_start_ts = self._repo.get_state("paper_ledger_start_ts")
        self._ledger_start_order_id = self._repo.get_state("paper_ledger_start_order_id")
        self._load_open_positions()
        self._cash = self._repo.get_state("paper_cash_usdc")

    def _load_open_positions(self) -> None:
        """Rebuild open paper positions from the orders table.

        ⚠️ **Required for scheduled operation, not a nicety.** Open positions used to live only
        in this object's memory, so a per-cycle agent (a cron/launchd run, or any process
        restart) would construct a fresh trader that had forgotten every open position: it would
        never exit them, and would re-enter the same instrument on the next signal. The paper
        track record -- which the promotion gate is scored on -- would have been silently full
        of unclosed entries.

        Pairing is exact rather than heuristic: every exit payload carries the
        `entry_order_id` it closed, so an entry whose id never appears in an exit is open.

        The epoch cutoff below is ID-based, not timestamp-based: an order's `ts` comes
        from BAR/candle time (always at or before wall-clock `now_ts`, since the latest
        closed bar can't be in the future), while `paper_ledger_start_order_id` is
        stamped once, at `seed_cash` time, off the max paper order id then on record.
        Autoincrement ids cleanly separate legacy/pre-seed orders from genuinely new ones
        regardless of bar time -- a ts-based cutoff would wrongly drop a position opened
        off an earlier bar during the very seeding cycle.
        """
        orders = self._repo.get_orders(mode="paper")
        closed_entry_ids: set[int] = set()
        entries: list[tuple[int, dict, dict]] = []

        for order in orders:
            try:
                payload = json.loads(order.get("raw_response") or "{}")
            except (TypeError, ValueError):
                continue
            if self._ledger_start_order_id is not None:
                if int(order["id"]) <= self._ledger_start_order_id:
                    # Pre-epoch legacy order (written before this synthetic account's
                    # seed) -- never rehydrate it, so a legacy 1-unit paper position
                    # can't silently reappear in the synthetic ledger.
                    continue
            if payload.get("role") == "exit":
                entry_id = payload.get("entry_order_id")
                if entry_id is not None:
                    closed_entry_ids.add(int(entry_id))
            elif payload.get("role") == "entry":
                entries.append((int(order["id"]), order, payload))

        for order_id, order, payload in entries:
            if order_id in closed_entry_ids:
                continue
            product_id = order["product_id"]
            setup = Setup(
                product_id=product_id,
                direction="long",
                entry=Decimal(payload["entry"]),
                stop=Decimal(payload["stop"]),
                target=Decimal(payload["target"]),
                context={},
                ts=int(payload.get("ts") or order.get("created_at") or 0),
            )
            self._open[product_id] = _OpenPaperPosition(
                rule_name=payload.get("rule_name") or "",
                product_id=product_id,
                setup=setup,
                entry_order_id=order_id,
                entry_fill=Decimal(payload["entry"]),
                entry_ts=setup.ts,
                qty=Decimal(payload["qty"]),
                # A surviving (non-filtered) order is always post-epoch: the cutoff above
                # already dropped anything with id <= `paper_ledger_start_order_id`, and
                # that id is only ever stamped by `seed_cash` -- so every rehydrated
                # position was opened while cash was seeded, and was costed at the time.
                costed=True,
                # The entry order's own `rule_id` column -- a real DB value, not something the
                # JSON payload needs to carry -- so a restart-rehydrated position still closes
                # with the same `rule_id` a same-process exit would have written.
                rule_id=order.get("rule_id"),
            )

    def has_open_position(self, product_id: str) -> bool:
        return product_id in self._open

    def get_cash(self) -> Decimal | None:
        return self._cash

    def seed_cash(self, amount: Decimal, now_ts: int) -> None:
        """Set the synthetic cash balance, opting this repo into the funding check.

        Also stamps `paper_ledger_start_order_id` the first time it's called (never
        overwritten after) -- the ID-based epoch cutoff `_load_open_positions` uses to
        ignore legacy pre-epoch orders written before the synthetic account existed.
        ID-based rather than `now_ts`-based: `now_ts` is wall-clock time, but an order's
        own `ts` comes from bar/candle time, which always predates wall-clock `now_ts` --
        a ts cutoff would wrongly drop a position opened off an earlier bar during the
        very cycle that seeded the account. `paper_ledger_start_ts` is still stamped too
        (harmless, kept for any external readers) but no longer drives the cutoff.
        """
        self._cash = amount
        self._repo.set_state("paper_cash_usdc", amount)
        if self._repo.get_state("paper_ledger_start_ts") is None:
            self._ledger_start_ts = now_ts
            self._repo.set_state("paper_ledger_start_ts", now_ts)
        if self._repo.get_state("paper_ledger_start_order_id") is None:
            start_id = max((o["id"] for o in self._repo.get_orders(mode="paper")), default=0)
            self._ledger_start_order_id = start_id
            self._repo.set_state("paper_ledger_start_order_id", start_id)

    def deposit(self, amount: Decimal) -> None:
        if self._cash is None:
            return
        self._cash += amount
        self._repo.set_state("paper_cash_usdc", self._cash)

    def equity(self, price_by_product: dict[str, Decimal]) -> Decimal | None:
        """Mark-to-market equity: synthetic cash plus costed open paper positions.

        `None` iff cash is unseeded -- there is no synthetic account to mark yet. An open
        position that was never debited against cash (`costed=False`, opened before cash was
        seeded) is excluded -- marking it would inflate equity with a position nothing paid
        for.
        """
        if self._cash is None:
            return None
        from keel.execution.equity import mark_positions

        product_ids = [p for p in self._open if self._open[p].costed]
        positions = [(self._open[p].qty, self._open[p].entry_fill) for p in product_ids]
        return mark_positions(self._cash, positions, price_by_product, product_ids)

    def on_signal(
        self, signal: Signal, candle: Candle | None = None, qty: Decimal = _QTY
    ) -> int | None:
        """Apply one `Signal`, writing a paper order if it results in a fill.

        ENTER: opens a paper position for `signal.product_id` and immediately writes
        a filled entry order (slippage-adjusted fill at `signal.setup.entry`) --
        unless a paper position is already open for that instrument (no overlap), in
        which case the signal is ignored. EXIT: closes an open position for
        `signal.product_id` at `candle.close` (a signal-driven exit, distinct from
        the stop/target touches `on_candle` resolves); requires `candle` and a
        currently-open position, else it's a no-op. NONE is always a no-op.

        Returns the written order's id, or `None` if nothing was written.
        """
        if signal.action == Action.ENTER:
            return self._enter(signal, qty)
        if signal.action == Action.EXIT:
            if candle is None:
                return None
            return self._exit_on_signal(signal, candle)
        return None

    def on_candle(self, product_id: str, candle: Candle) -> int | None:
        """Advance MFE/MAE for `product_id`'s open paper position (if any) and close
        it -- writing an exit order -- if `candle`'s range touches the setup's stop
        or target (a bar touching both resolves conservatively to the stop, matching
        `backtest.py`'s no-finer-data fallback). Returns the exit order id if a close
        happened, else `None` (including when there is no open position).
        """
        position = self._open.get(product_id)
        if position is None:
            return None

        position.mfe = max(position.mfe, candle.high - position.entry_fill)
        position.mae = max(position.mae, position.entry_fill - candle.low)

        stop = position.setup.stop
        target = position.setup.target
        stop_touched = _touches(candle, stop)
        target_touched = _touches(candle, target)

        if stop_touched:
            exit_price = stop
        elif target_touched:
            exit_price = target
        else:
            return None

        return self._close(position, exit_price, candle.ts)

    def _enter(self, signal: Signal, qty: Decimal = _QTY) -> int | None:
        if signal.setup is None or signal.product_id in self._open:
            return None

        setup = signal.setup
        entry_fill = setup.entry * (Decimal(1) + self._slippage_pct)
        fee = entry_fill * qty * self._fee_pct

        costed = self._cash is not None
        if costed:
            # Gate on the ACTUAL debit (fill + slippage + fee), not the coarser intent
            # notional (entry * qty) -- gating on notional alone would let cash go
            # negative for any seed strictly between the two, defeating the check's
            # purpose (spec Sec. 4.2: keep cash from going negative).
            fill_cost = entry_fill * qty + fee
            if self._cash < fill_cost:
                # Paper-path guard only -- a rejection here just means no synthetic
                # fill; it never touches guards.py's live-order checks.
                log_event(
                    logger,
                    logging.INFO,
                    "paper.funding_skip",
                    product_id=signal.product_id,
                    cash=str(self._cash),
                    fill_cost=str(fill_cost),
                )
                return None

        payload = {
            "role": "entry",
            "rule_name": signal.rule_name,
            "entry": str(entry_fill),
            "stop": str(setup.stop),
            "target": str(setup.target),
            "qty": str(qty),
            "ts": setup.ts,
        }
        order_id = self._repo.insert_order(
            {
                "mode": "paper",
                "product_id": signal.product_id,
                "side": Side.BUY.value,
                "order_type": "market",
                "qty": qty,
                "limit_price": setup.entry,
                "status": "filled",
                "fee": fee,
                "expected_fill": setup.entry,
                "actual_fill": entry_fill,
                "raw_response": json.dumps(payload),
                "confirmation": "paper",
                "rule_id": signal.rule_id,
                "created_at": signal.ts,
                "updated_at": signal.ts,
            }
        )
        self._open[signal.product_id] = _OpenPaperPosition(
            rule_name=signal.rule_name,
            product_id=signal.product_id,
            setup=setup,
            entry_order_id=order_id,
            entry_fill=entry_fill,
            entry_ts=setup.ts,
            qty=qty,
            costed=costed,
            rule_id=signal.rule_id,
        )
        if costed:
            self._cash -= entry_fill * qty + fee
            self._repo.set_state("paper_cash_usdc", self._cash)
        return order_id

    def _exit_on_signal(self, signal: Signal, candle: Candle) -> int | None:
        position = self._open.get(signal.product_id)
        if position is None:
            return None
        position.mfe = max(position.mfe, candle.high - position.entry_fill)
        position.mae = max(position.mae, position.entry_fill - candle.low)
        return self._close(position, candle.close, candle.ts)

    def _close(self, position: _OpenPaperPosition, exit_price: Decimal, exit_ts: int) -> int:
        exit_fill = exit_price * (Decimal(1) - self._slippage_pct)
        entry_fee = position.entry_fill * position.qty * self._fee_pct
        exit_fee = exit_fill * position.qty * self._fee_pct
        pnl = (exit_fill - position.entry_fill) * position.qty - entry_fee - exit_fee

        risk = (position.entry_fill - position.setup.stop) * position.qty
        r_multiple = pnl / risk if risk != 0 else None

        if pnl > 0:
            outcome = "win"
        elif pnl < 0:
            outcome = "loss"
        else:
            outcome = "scratch"

        payload = {
            "role": "exit",
            "rule_name": position.rule_name,
            "entry_order_id": position.entry_order_id,
            "entry": str(position.entry_fill),
            "exit": str(exit_fill),
            "qty": str(position.qty),
            "pnl": str(pnl),
            "r_multiple": str(r_multiple) if r_multiple is not None else None,
            "mfe": str(position.mfe),
            "mae": str(position.mae),
            "outcome": outcome,
            "entry_ts": position.entry_ts,
            "exit_ts": exit_ts,
        }
        order_id = self._repo.insert_order(
            {
                "mode": "paper",
                "product_id": position.product_id,
                "side": Side.SELL.value,
                "order_type": "market",
                "qty": position.qty,
                "limit_price": exit_price,
                "status": "filled",
                "fee": exit_fee,
                "expected_fill": exit_price,
                "actual_fill": exit_fill,
                "raw_response": json.dumps(payload),
                "confirmation": "paper",
                "rule_id": position.rule_id,
                "created_at": exit_ts,
                "updated_at": exit_ts,
            }
        )
        del self._open[position.product_id]
        if position.costed and self._cash is not None:
            self._cash += exit_fill * position.qty - exit_fee
            self._repo.set_state("paper_cash_usdc", self._cash)
        return order_id


def track_record(repo: Repository, rule_name: str) -> BacktestResult:
    """Aggregate `rule_name`'s paper trades (from `orders(mode='paper')`) into a
    `BacktestResult`-shaped summary, directly comparable to `backtest.backtest()`'s
    historical output.
    """
    orders = repo.get_orders(mode="paper")

    entries: dict[int, dict] = {}
    exits: list[dict] = []
    for order in orders:
        raw = order["raw_response"]
        if not raw:
            continue
        payload = json.loads(raw)
        if payload.get("rule_name") != rule_name:
            continue
        if payload.get("role") == "entry":
            entries[order["id"]] = payload
        elif payload.get("role") == "exit":
            exits.append(payload)

    trades: list[Trade] = []
    for payload in exits:
        r_multiple = payload["r_multiple"]
        trades.append(
            Trade(
                entry_ts=payload["entry_ts"],
                exit_ts=payload["exit_ts"],
                entry=Decimal(payload["entry"]),
                exit=Decimal(payload["exit"]),
                qty=Decimal(payload["qty"]),
                side=Side.BUY,
                pnl=Decimal(payload["pnl"]),
                r_multiple=Decimal(r_multiple) if r_multiple is not None else None,
                mfe=Decimal(payload["mfe"]),
                mae=Decimal(payload["mae"]),
                outcome=payload["outcome"],
            )
        )
        entries.pop(payload["entry_order_id"], None)

    for payload in entries.values():
        trades.append(
            Trade(
                entry_ts=payload["ts"],
                exit_ts=None,
                entry=Decimal(payload["entry"]),
                exit=None,
                qty=Decimal(payload["qty"]),
                side=Side.BUY,
                pnl=None,
                r_multiple=None,
                mfe=Decimal(0),
                mae=Decimal(0),
                outcome="open",
            )
        )

    return summarize(trades)
