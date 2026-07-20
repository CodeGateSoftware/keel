"""Forward paper-trading simulator.

Consumes `Signal`s (task 1's shared type, `keel.strategy.rules.base.Signal`) as
plain input -- this module does **not** import `engine.py` (task 7 builds in
parallel with this one, per the Phase 2 wave-C split; `evaluate()` there is what
produces the `Signal`s fed in here). Given a stream of `Signal`s and candle data,
`PaperTrader` simulates fills (entry, then stop/target/MFE/MAE tracking to a close,
mirroring `backtest.py`'s per-trade bookkeeping) and journals every fill to
`orders(mode='paper')` via `Repository`. No live order placement ever happens here
(Phase 3 `execution/*` owns that).

**Schema note:** `orders.rule_id` is an `INTEGER` foreign key into the `rules` table
(populated by promotion/demotion, task 9); paper fills from a rule that hasn't been
persisted there yet would violate that FK, so paper orders leave `rule_id` NULL and
instead carry `rule_name` plus this module's own reconstruction fields (`role`,
`entry_order_id`, pnl/mfe/mae/outcome/etc.) JSON-encoded in `orders.raw_response` --
the column live trading reserves for a broker's raw response, unused for paper
fills. `track_record()` reads that back out (via `Repository.get_orders(mode='paper')`,
P3 Task 1) to reconstruct `Trade`s and aggregates them via the shared
`strategy.stats.summarize` helper into the same `BacktestResult` shape `backtest.py`
produces, so paper and historical stats are directly comparable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

from keel.data.repository import Repository
from keel.strategy.rules.base import Action, Setup, Signal, Trade
from keel.strategy.stats import BacktestResult, summarize
from keel.types import Candle, Side

# Position sizing (money management) is out of scope here, same as backtest.py:
# every paper trade uses a fixed 1-unit notional, sufficient for win-rate/
# expectancy/drawdown/R-multiple stats.
_QTY = Decimal(1)
_DEFAULT_FEE_PCT = Decimal("0.006")
_DEFAULT_SLIPPAGE_PCT = Decimal("0.0005")


@dataclass
class _OpenPaperPosition:
    rule_name: str
    product_id: str
    setup: Setup
    entry_order_id: int
    entry_fill: Decimal
    entry_ts: int
    mfe: Decimal = Decimal(0)
    mae: Decimal = Decimal(0)


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
        self._load_open_positions()

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
        """
        orders = self._repo.get_orders(mode="paper")
        closed_entry_ids: set[int] = set()
        entries: list[tuple[int, dict, dict]] = []

        for order in orders:
            try:
                payload = json.loads(order.get("raw_response") or "{}")
            except (TypeError, ValueError):
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
            )

    def has_open_position(self, product_id: str) -> bool:
        return product_id in self._open

    def on_signal(self, signal: Signal, candle: Candle | None = None) -> int | None:
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
            return self._enter(signal)
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

    def _enter(self, signal: Signal) -> int | None:
        if signal.setup is None or signal.product_id in self._open:
            return None

        setup = signal.setup
        entry_fill = setup.entry * (Decimal(1) + self._slippage_pct)
        fee = entry_fill * _QTY * self._fee_pct
        payload = {
            "role": "entry",
            "rule_name": signal.rule_name,
            "entry": str(entry_fill),
            "stop": str(setup.stop),
            "target": str(setup.target),
            "qty": str(_QTY),
            "ts": setup.ts,
        }
        order_id = self._repo.insert_order(
            {
                "mode": "paper",
                "product_id": signal.product_id,
                "side": Side.BUY.value,
                "order_type": "market",
                "qty": _QTY,
                "limit_price": setup.entry,
                "status": "filled",
                "fee": fee,
                "expected_fill": setup.entry,
                "actual_fill": entry_fill,
                "raw_response": json.dumps(payload),
                "confirmation": "paper",
                "rule_id": None,
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
        )
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
        entry_fee = position.entry_fill * _QTY * self._fee_pct
        exit_fee = exit_fill * _QTY * self._fee_pct
        pnl = (exit_fill - position.entry_fill) * _QTY - entry_fee - exit_fee

        risk = (position.entry_fill - position.setup.stop) * _QTY
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
            "qty": str(_QTY),
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
                "qty": _QTY,
                "limit_price": exit_price,
                "status": "filled",
                "fee": exit_fee,
                "expected_fill": exit_price,
                "actual_fill": exit_fill,
                "raw_response": json.dumps(payload),
                "confirmation": "paper",
                "rule_id": None,
                "created_at": exit_ts,
                "updated_at": exit_ts,
            }
        )
        del self._open[position.product_id]
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
