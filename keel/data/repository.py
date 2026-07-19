"""The sole write path to the keel SQLite schema (see `db.py` for DDL).

`Repository` wraps a `sqlite3.Connection` (built via `db.connect()` + `db.migrate()`) with typed
methods. Money and prices are always `Decimal` in the Python API and stored as `TEXT` holding
`str(Decimal(...))` so values round-trip exactly (no floating-point drift).
"""

from __future__ import annotations

import json
import sqlite3
import time
from decimal import Decimal
from typing import Any

from keel_core.subscription import BrokerSubscription, SubscriptionStatus

from keel.types import Candle, Granularity

_TRANSACTION_COLUMNS = (
    "coinbase_id",
    "source",
    "type",
    "asset",
    "ts",
    "qty",
    "price",
    "subtotal",
    "total",
    "fees",
    "notes",
    "rule_id",
    "order_id",
)
_TRANSACTION_MONEY_FIELDS = ("qty", "price", "subtotal", "total", "fees")

_ORDER_COLUMNS = (
    "mode",
    "product_id",
    "side",
    "order_type",
    "qty",
    "limit_price",
    "status",
    "fee",
    "expected_fill",
    "actual_fill",
    "raw_response",
    "confirmation",
    "rule_id",
    "created_at",
    "updated_at",
)
_ORDER_MONEY_FIELDS = ("qty", "limit_price", "fee", "expected_fill", "actual_fill")

_SIGNAL_COLUMNS = ("rule_id", "product_id", "ts", "indicators", "cts_score", "fired")


def _dec_to_text(value: Any) -> str | None:
    """Convert a `Decimal` (or decimal-like value) to its exact TEXT storage form."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    return str(Decimal(str(value)))


def _text_to_dec(value: str | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(value)


def _subscription_from_row(row: sqlite3.Row) -> BrokerSubscription:
    """Map a `broker_subscriptions` row to the domain record.

    `free_volume_usd` is the one nullable money column: NULL means unlimited (Premium), which is
    deliberately not the same as `'0'`.
    """
    return BrokerSubscription(
        venue=row["venue"],
        tier_name=row["tier_name"],
        free_volume_usd=_text_to_dec(row["free_volume_usd"]),
        pacing=row["pacing"],
        subscription_usd_month=Decimal(row["subscription_usd_month"]),
        status=SubscriptionStatus(row["status"]),
        attested_at=int(row["attested_at"]),
        attest_due_ts=int(row["attest_due_ts"]),
    )


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return {"__decimal__": str(obj)}
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _json_object_hook(d: dict[str, Any]) -> Any:
    if set(d.keys()) == {"__decimal__"}:
        return Decimal(d["__decimal__"])
    return d


class Repository:
    """Typed wrapper around a migrated `sqlite3.Connection` for the keel schema."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # -- transactions ---------------------------------------------------

    def upsert_transaction(self, tx: dict[str, Any]) -> None:
        """Insert `tx`, or update it in place if `tx["coinbase_id"]` already exists.

        Dedup key is the `transactions.coinbase_id` UNIQUE constraint.
        """
        values: dict[str, Any] = {col: tx.get(col) for col in _TRANSACTION_COLUMNS}
        for field in _TRANSACTION_MONEY_FIELDS:
            values[field] = _dec_to_text(values[field])

        columns_sql = ", ".join(_TRANSACTION_COLUMNS)
        placeholders_sql = ", ".join(f":{c}" for c in _TRANSACTION_COLUMNS)
        update_sql = ", ".join(
            f"{c} = excluded.{c}" for c in _TRANSACTION_COLUMNS if c != "coinbase_id"
        )
        self._conn.execute(
            f"""
            INSERT INTO transactions ({columns_sql})
            VALUES ({placeholders_sql})
            ON CONFLICT(coinbase_id) DO UPDATE SET {update_sql}
            """,
            values,
        )
        self._conn.commit()

    def get_transactions(self, asset: str | None = None) -> list[dict[str, Any]]:
        if asset is None:
            rows = self._conn.execute("SELECT * FROM transactions ORDER BY ts, id").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM transactions WHERE asset = ? ORDER BY ts, id", (asset,)
            ).fetchall()
        return [self._transaction_row_to_dict(row) for row in rows]

    def _transaction_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        for field in _TRANSACTION_MONEY_FIELDS:
            d[field] = _text_to_dec(d[field])
        return d

    # -- candles ----------------------------------------------------------

    def upsert_candles(
        self, product_id: str, granularity: Granularity, candles: list[Candle]
    ) -> int:
        """Upsert `candles` keyed on `(product_id, granularity, ts)`. Returns rows written."""
        gran_value = Granularity(granularity).value
        rows = [
            (
                product_id,
                gran_value,
                candle.ts,
                str(candle.open),
                str(candle.high),
                str(candle.low),
                str(candle.close),
                str(candle.volume),
            )
            for candle in candles
        ]
        self._conn.executemany(
            """
            INSERT INTO candles (product_id, granularity, ts, o, h, l, c, v)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_id, granularity, ts) DO UPDATE SET
                o = excluded.o, h = excluded.h, l = excluded.l, c = excluded.c, v = excluded.v
            """,
            rows,
        )
        self._conn.commit()
        return len(rows)

    def get_candles(
        self,
        product_id: str,
        granularity: Granularity,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> list[Candle]:
        gran_value = Granularity(granularity).value
        query = "SELECT ts, o, h, l, c, v FROM candles WHERE product_id = ? AND granularity = ?"
        params: list[Any] = [product_id, gran_value]
        if start_ts is not None:
            query += " AND ts >= ?"
            params.append(start_ts)
        if end_ts is not None:
            query += " AND ts <= ?"
            params.append(end_ts)
        query += " ORDER BY ts"

        rows = self._conn.execute(query, params).fetchall()
        return [
            Candle(
                ts=row["ts"],
                open=Decimal(row["o"]),
                high=Decimal(row["h"]),
                low=Decimal(row["l"]),
                close=Decimal(row["c"]),
                volume=Decimal(row["v"]),
            )
            for row in rows
        ]

    # -- orders -------------------------------------------------------------

    def insert_order(self, order: dict[str, Any]) -> int:
        """Insert a new order row and return its `id`."""
        values: dict[str, Any] = {col: order.get(col) for col in _ORDER_COLUMNS}
        for field in _ORDER_MONEY_FIELDS:
            values[field] = _dec_to_text(values[field])
        if values["status"] is None:
            values["status"] = "pending"

        columns_sql = ", ".join(_ORDER_COLUMNS)
        placeholders_sql = ", ".join(f":{c}" for c in _ORDER_COLUMNS)
        cursor = self._conn.execute(
            f"INSERT INTO orders ({columns_sql}) VALUES ({placeholders_sql})", values
        )
        self._conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def update_order(self, order_id: int, **fields: Any) -> None:
        """Partially update the order row `order_id` with `fields`."""
        if not fields:
            return
        for money_field in _ORDER_MONEY_FIELDS:
            if money_field in fields:
                fields[money_field] = _dec_to_text(fields[money_field])

        set_sql = ", ".join(f"{k} = :{k}" for k in fields)
        params = dict(fields)
        params["order_id"] = order_id
        self._conn.execute(f"UPDATE orders SET {set_sql} WHERE id = :order_id", params)
        self._conn.commit()

    def get_order(self, order_id: int) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if row is None:
            return None
        return self._order_row_to_dict(row)

    def held_products(self) -> list[str]:
        """Every `live` product with at least one filled order, ascending.

        Feeds `agent._mark_to_market_equity`'s product union. That caller cannot iterate the
        live rule set alone: retiring or disabling a rule while its position is still open would
        drop the holding out of equity in one step, and against a monotonic high-water mark that
        cliff registers as a permanent drawdown. Filled-only and live-only -- a `pending` order
        is not a holding, and paper fills must never reach live equity.
        """
        rows = self._conn.execute(
            "SELECT DISTINCT product_id FROM orders WHERE mode = 'live' AND status = 'filled' "
            "ORDER BY product_id"
        ).fetchall()
        return [row["product_id"] for row in rows]

    def get_orders(
        self,
        mode: str | None = None,
        product_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List orders, optionally filtered by `mode`/`product_id`/`status`, oldest first."""
        query = "SELECT * FROM orders WHERE 1=1"
        params: list[Any] = []
        if mode is not None:
            query += " AND mode = ?"
            params.append(mode)
        if product_id is not None:
            query += " AND product_id = ?"
            params.append(product_id)
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY id"

        rows = self._conn.execute(query, params).fetchall()
        return [self._order_row_to_dict(row) for row in rows]

    def _order_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        for field in _ORDER_MONEY_FIELDS:
            d[field] = _text_to_dec(d[field])
        return d

    # -- rules -----------------------------------------------------------------

    def insert_rule(
        self,
        kind: str,
        params: dict[str, Any],
        status: str = "candidate",
        now_ts: int | None = None,
    ) -> int:
        """Insert a new `rules` row (JSON-encoding `params`) and return its `id`.

        `now_ts` stamps `created_at`; defaults to `int(time.time())` when the caller doesn't
        supply one, but a CLI-boundary caller (e.g. `keel rules seed`) should pass its own single
        `now_ts` so every row from one invocation shares an identical `created_at`.
        """
        created_at = now_ts if now_ts is not None else int(time.time())
        cursor = self._conn.execute(
            "INSERT INTO rules (kind, params, status, created_at) VALUES (?, ?, ?, ?)",
            (kind, json.dumps(params), status, created_at),
        )
        self._conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def get_rules(self, status: str | None = None) -> list[dict[str, Any]]:
        """List rules (JSON-decoding `params`), optionally filtered by `status`."""
        if status is None:
            rows = self._conn.execute("SELECT * FROM rules ORDER BY id").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM rules WHERE status = ? ORDER BY id", (status,)
            ).fetchall()
        return [self._rule_row_to_dict(row) for row in rows]

    def update_rule_status(self, rule_id: int, status: str) -> None:
        """Set `rules.status`, stamping `demoted_at` (status `disabled`) or `promoted_at`
        (any other status -- `paper`/`live`) with the current time.
        """
        column = "demoted_at" if status == "disabled" else "promoted_at"
        self._conn.execute(
            f"UPDATE rules SET status = ?, {column} = ? WHERE id = ?",
            (status, int(time.time()), rule_id),
        )
        self._conn.commit()

    def _rule_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["params"] = json.loads(d["params"]) if d["params"] else {}
        return d

    # -- signals -----------------------------------------------------------------

    def insert_signal(self, signal: dict[str, Any]) -> int:
        """Insert a new `signals` row and return its `id`."""
        values: dict[str, Any] = {col: signal.get(col) for col in _SIGNAL_COLUMNS}
        if values["fired"] is None:
            values["fired"] = 0

        columns_sql = ", ".join(_SIGNAL_COLUMNS)
        placeholders_sql = ", ".join(f":{c}" for c in _SIGNAL_COLUMNS)
        cursor = self._conn.execute(
            f"INSERT INTO signals ({columns_sql}) VALUES ({placeholders_sql})", values
        )
        self._conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    # -- agent_state (KV) -----------------------------------------------------

    def get_state(self, key: str, default: Any = None) -> Any:
        row = self._conn.execute("SELECT value FROM agent_state WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        return json.loads(row["value"], object_hook=_json_object_hook)

    def set_state(self, key: str, value: Any) -> None:
        encoded = json.dumps(value, default=_json_default)
        self._conn.execute(
            """
            INSERT INTO agent_state (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, encoded),
        )
        self._conn.commit()

    # -- broker subscriptions (per-venue, rail 14) -------------------------

    def get_broker_subscription(self, venue: str) -> BrokerSubscription | None:
        """Return `venue`'s attested subscription, or `None` if it has never been attested.

        `None` is meaningful, not an error: the design spec §7 treats a missing row as unknown
        and therefore closed. Callers must not substitute a default allowance of their own.
        """
        row = self._conn.execute(
            "SELECT * FROM broker_subscriptions WHERE venue = ?", (venue,)
        ).fetchone()
        return None if row is None else _subscription_from_row(row)

    def upsert_broker_subscription(self, record: BrokerSubscription) -> None:
        """Insert or replace `record`, keyed on venue. One subscription per venue."""
        self._conn.execute(
            """
            INSERT INTO broker_subscriptions (
                venue, tier_name, free_volume_usd, pacing,
                subscription_usd_month, status, attested_at, attest_due_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(venue) DO UPDATE SET
                tier_name = excluded.tier_name,
                free_volume_usd = excluded.free_volume_usd,
                pacing = excluded.pacing,
                subscription_usd_month = excluded.subscription_usd_month,
                status = excluded.status,
                attested_at = excluded.attested_at,
                attest_due_ts = excluded.attest_due_ts
            """,
            (
                record.venue,
                record.tier_name,
                _dec_to_text(record.free_volume_usd),
                record.pacing,
                _dec_to_text(record.subscription_usd_month),
                record.status.value,
                record.attested_at,
                record.attest_due_ts,
            ),
        )
        self._conn.commit()

    def list_broker_subscriptions(self) -> list[BrokerSubscription]:
        """Every attested subscription, ordered by venue -- what `keel subscription show`
        renders."""
        rows = self._conn.execute(
            "SELECT * FROM broker_subscriptions ORDER BY venue"
        ).fetchall()
        return [_subscription_from_row(row) for row in rows]

    # -- trade outcomes (closed round-trips; rails 11 and 16) ---------------

    def insert_trade_outcome(self, outcome: dict[str, Any]) -> int:
        """Append one CLOSED trade. `pnl_net` is realized and NET OF FEES — its sign is what
        rail 16 counts, so a fee-dominated "winner" must arrive here already negative.
        """
        cursor = self._conn.execute(
            """
            INSERT INTO trade_outcomes (
                product_id, rule_name, is_dca, opened_at, closed_at,
                qty, entry_fill, exit_fill, fees, pnl_net
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                outcome["product_id"],
                outcome["rule_name"],
                1 if outcome["is_dca"] else 0,
                int(outcome["opened_at"]),
                int(outcome["closed_at"]),
                _dec_to_text(outcome["qty"]),
                _dec_to_text(outcome["entry_fill"]),
                _dec_to_text(outcome["exit_fill"]),
                _dec_to_text(outcome["fees"]),
                _dec_to_text(outcome["pnl_net"]),
            ),
        )
        self._conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def get_trade_outcomes(self, since_ts: int | None = None) -> list[dict[str, Any]]:
        """Closed trades, OLDEST FIRST -- streak logic depends on that order."""
        if since_ts is None:
            rows = self._conn.execute(
                "SELECT * FROM trade_outcomes ORDER BY closed_at, id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM trade_outcomes WHERE closed_at >= ? ORDER BY closed_at, id",
                (since_ts,),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "product_id": row["product_id"],
                "rule_name": row["rule_name"],
                "is_dca": bool(row["is_dca"]),
                "opened_at": int(row["opened_at"]),
                "closed_at": int(row["closed_at"]),
                "qty": Decimal(row["qty"]),
                "entry_fill": Decimal(row["entry_fill"]),
                "exit_fill": Decimal(row["exit_fill"]),
                "fees": Decimal(row["fees"]),
                "pnl_net": Decimal(row["pnl_net"]),
            }
            for row in rows
        ]

    # -- bypass arm token (Issue #60, in-process bypass hardening) ---------

    def arm_bypass(self, now_ts: int, ttl_sec: int) -> None:
        """Arm autonomous bypass mode for `ttl_sec` seconds starting at `now_ts`.

        Overwrites any previous arm token outright -- there is only ever one live token, and
        arming again (e.g. re-running `keel arm-bypass`) always resets the window from `now_ts`
        rather than extending the old one. `agent.run_once`'s own `is_bypass_armed` check reads
        this token fresh on every cycle, so it is the one place bypass mode can be authorized
        from -- CLI (`keel arm-bypass`, passphrase-gated) or any other authenticated caller.
        """
        self.set_state(
            "bypass_arm",
            {"armed_at": now_ts, "armed_until": now_ts + ttl_sec},
        )

    def is_bypass_armed(self, now_ts: int) -> bool:
        """True iff a bypass-arm token exists and `now_ts` is still inside its window.

        Freshness is a strict `now_ts < armed_until` (matching `market_feed.is_fresh`'s own
        convention elsewhere in this codebase) -- the instant `armed_until` is reached, the
        token is treated as expired, not one tick still-good.
        """
        token = self.get_state("bypass_arm")
        if token is None:
            return False
        return now_ts < token["armed_until"]

    def disarm_bypass(self) -> None:
        """Clear the bypass-arm token immediately.

        Fail-safe direction: disarming only ever *reduces* capability, so unlike `arm_bypass`
        this needs no passphrase gate at the CLI layer -- it is always safe to call, including
        when nothing is currently armed (a no-op).
        """
        self.set_state("bypass_arm", None)
