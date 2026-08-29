"""The sole write path to the keel SQLite schema (see `db.py` for DDL).

`Repository` wraps a `sqlite3.Connection` (built via `db.connect()` + `db.migrate()`) with typed
methods. Money and prices are always `Decimal` in the Python API and stored as `TEXT` holding
`str(Decimal(...))` so values round-trip exactly (no floating-point drift).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from decimal import Decimal
from typing import Any

from keel_core.subscription import BrokerSubscription, SubscriptionStatus
from keel_core.trade_scope import TradeScopeState, VenueTradeScope

from keel.types import Candle, Granularity, Profile

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
    # The venue-observed fill (#446): `qty` is the ORDERED size, this is what actually
    # executed. NULL on rows written before the column existed -- "not observed", so readers
    # fall back to `qty`, the behaviour those rows always had.
    "filled_quantity",
    "raw_response",
    "confirmation",
    "rule_id",
    "created_at",
    "updated_at",
)
_ORDER_MONEY_FIELDS = (
    "qty",
    "limit_price",
    "fee",
    "expected_fill",
    "actual_fill",
    "filled_quantity",
)

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


def _trade_scope_from_row(row: sqlite3.Row) -> VenueTradeScope:
    """Map a `venue_trade_scopes` row to the domain record.

    `attested_scope`, `attested_ts`, `confirmed_ts`, and `refuted_ts` are all nullable: the
    record may hold only a subset of them depending on `state` (e.g. a backfilled `CONFIRMED`
    row has no attestation at all).
    """
    return VenueTradeScope(
        venue=row["venue"],
        state=TradeScopeState(row["state"]),
        attested_scope=row["attested_scope"],
        attested_ts=None if row["attested_ts"] is None else int(row["attested_ts"]),
        confirmed_ts=None if row["confirmed_ts"] is None else int(row["confirmed_ts"]),
        refuted_ts=None if row["refuted_ts"] is None else int(row["refuted_ts"]),
        refuted_reason=row["refuted_reason"],
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

    def get_state_keys(self, prefix: str) -> list[str]:
        """Every `agent_state` key starting with `prefix`, sorted -- a prefix DISCOVERY read
        for surfaces that hold no broker to ask which venue they serve: the venue-namespaced
        market-session records (`agent.market_session_key`) are found this way by `fetch
        --check`/`status`, which must stay offline and therefore cannot read a venue id off
        an adapter's `capabilities()`.

        `substr(key, 1, len(prefix)) = prefix` rather than `LIKE prefix || '%'` so a prefix
        containing `_` or `%` matches literally -- venue ids are slugs, but this read should
        not become a wildcard hazard the day one isn't.
        """
        rows = self._conn.execute(
            "SELECT key FROM agent_state WHERE substr(key, 1, ?) = ? ORDER BY key",
            (len(prefix), prefix),
        ).fetchall()
        return [row["key"] for row in rows]

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
        rows = self._conn.execute("SELECT * FROM broker_subscriptions ORDER BY venue").fetchall()
        return [_subscription_from_row(row) for row in rows]

    # -- venue trade scope (per-venue, rail 20) ----------------------------

    def get_venue_trade_scope(self, venue: str) -> VenueTradeScope | None:
        """Return `venue`'s trade-scope record, or `None` if it has never been recorded.

        `None` is meaningful, not an error: no row means nobody has attested this venue's
        credential and nothing has confirmed it either, so callers must treat it as unknown and
        therefore closed -- the same convention `get_broker_subscription` uses.
        """
        row = self._conn.execute(
            "SELECT * FROM venue_trade_scopes WHERE venue = ?", (venue,)
        ).fetchone()
        return None if row is None else _trade_scope_from_row(row)

    def upsert_venue_trade_scope(self, record: VenueTradeScope) -> None:
        """Insert or replace `record`, keyed on venue. One trade-scope record per venue."""
        self._conn.execute(
            """
            INSERT INTO venue_trade_scopes (
                venue, state, attested_scope, attested_ts, confirmed_ts, refuted_ts,
                refuted_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(venue) DO UPDATE SET
                state = excluded.state,
                attested_scope = excluded.attested_scope,
                attested_ts = excluded.attested_ts,
                confirmed_ts = excluded.confirmed_ts,
                refuted_ts = excluded.refuted_ts,
                refuted_reason = excluded.refuted_reason
            """,
            (
                record.venue,
                record.state.value,
                record.attested_scope,
                record.attested_ts,
                record.confirmed_ts,
                record.refuted_ts,
                record.refuted_reason,
            ),
        )
        self._conn.commit()

    def list_venue_trade_scopes(self) -> list[VenueTradeScope]:
        """Every recorded trade scope, ordered by venue."""
        rows = self._conn.execute("SELECT * FROM venue_trade_scopes ORDER BY venue").fetchall()
        return [_trade_scope_from_row(row) for row in rows]

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

    # -- positions: the per-tranche ledger ---------------------------------

    def open_position(
        self,
        *,
        product_id: str,
        rule_name: str,
        opened_at: int,
        qty: Decimal,
        entry_fill: Decimal,
        entry_fee: Decimal,
        initial_stop: Decimal | None = None,
        bracket_order_id: int | None = None,
    ) -> int:
        """Record a newly opened tranche and return its id.

        `initial_stop` is the stop this tranche was SIZED against (#520) -- the original
        per-unit risk the break-even threshold is computed from. `None` is a legitimate value
        and means "unknown", not "zero": DCA has no stop by design, and every tranche opened
        before v12 predates the column. Readers must disable the break-even arm for such a
        tranche rather than substitute the current stop, which is a different policy (see
        `db._migrate_v12_positions_initial_stop`).
        """
        cursor = self._conn.execute(
            """
            INSERT INTO positions
                (product_id, rule_name, opened_at, qty, entry_fill, entry_fee,
                 initial_stop, bracket_order_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open')
            """,
            (
                product_id,
                rule_name,
                int(opened_at),
                _dec_to_text(qty),
                _dec_to_text(entry_fill),
                _dec_to_text(entry_fee),
                None if initial_stop is None else _dec_to_text(initial_stop),
                bracket_order_id,
            ),
        )
        self._conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def get_open_positions(self, product_id: str | None = None) -> list[dict[str, Any]]:
        """Open tranches, OLDEST FIRST. FIFO is the attribution order a later exit uses, so the
        ordering is part of the contract, not an incidental detail of the query."""
        sql = "SELECT * FROM positions WHERE status = 'open'"
        params: list[Any] = []
        if product_id is not None:
            sql += " AND product_id = ?"
            params.append(product_id)
        sql += " ORDER BY opened_at, id"
        return [self._position_row_to_dict(row) for row in self._conn.execute(sql, params)]

    def get_position_for_bracket(self, bracket_order_id: int) -> dict[str, Any] | None:
        """The OPEN tranche whose bracket is `bracket_order_id`, or `None`.

        This is the ONE linkage direction: a position points at its bracket, never the reverse.
        Reconciliation starts from a filled order row and needs the tranche that owns it, so this
        is the lookup it uses instead of reading `position_rule:<product>`.

        Restricted to open tranches on purpose: a closed one's bracket id is history, and letting
        it answer would attribute a new fill to a trade already booked.
        """
        row = self._conn.execute(
            "SELECT * FROM positions WHERE bracket_order_id = ? AND status = 'open'",
            (bracket_order_id,),
        ).fetchone()
        return None if row is None else self._position_row_to_dict(row)

    def set_position_bracket(self, position_id: int, bracket_order_id: int | None) -> None:
        """Attach a (re-placed) bracket to an open tranche.

        Called both when the entry's bracket is first placed and when reconciliation replaces a
        dead one. Skipping the second case leaves the tranche naming an order that no longer
        exists, so the replacement's fill resolves to no tranche and its outcome is dropped.
        """
        self._conn.execute(
            "UPDATE positions SET bracket_order_id = ? WHERE id = ?",
            (bracket_order_id, position_id),
        )
        self._conn.commit()

    def close_position(self, position_id: int, *, closed_at: int) -> None:
        self._conn.execute(
            "UPDATE positions SET status = 'closed', closed_at = ? WHERE id = ?",
            (int(closed_at), position_id),
        )
        self._conn.commit()

    def _position_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        """Money columns are TEXT and decode to `Decimal`; ids and timestamps are INTEGER and
        must NOT go through the money decoder -- a `Decimal("11")` `bracket_order_id` compares
        unequal to the `orders.id` it names, which would silently orphan every tranche."""
        d = dict(row)
        for field in ("qty", "entry_fill", "entry_fee"):
            d[field] = _text_to_dec(d[field])
        # `initial_stop` (#520) decodes the same way but is NULLABLE, and the distinction is
        # load-bearing: `None` means "nobody recorded it" -- DCA, or a tranche predating v12 --
        # and the break-even arm must switch OFF for it rather than substitute a value.
        # `_text_to_dec` is not asked to invent a zero.
        raw_initial_stop = d.get("initial_stop")
        d["initial_stop"] = None if raw_initial_stop is None else _text_to_dec(raw_initial_stop)
        return d

    # -- profile (the user's own settings) ------------------------------------

    def get_profile(self) -> Profile:
        """The user's profile, defaulting to NOT autonomous.

        **Fails closed.** An absent row -- a fresh database, or one upgraded from before the
        `profile` table existed -- reports `autonomous=False`. The safe reading of "no record"
        is that the user never opted into unattended trading, never that they did.

        Callers must re-read this each cycle rather than caching it, so that
        `keel autonomy off` takes effect on the next cycle instead of the next restart.
        """
        try:
            row = self._conn.execute(
                "SELECT autonomous, autonomous_until, updated_ts FROM profile WHERE id = 1"
            ).fetchone()
        except sqlite3.Error as exc:
            # A missing or damaged `profile` table must read as "no consent recorded", not
            # propagate. But it must not be SILENT either: swallowing this indistinguishably
            # from "the user never opted in" hides a broken migration or a corrupt database
            # behind a reassuring `autonomy: off`. Fail closed AND say so.
            logging.getLogger(__name__).error(
                "profile unreadable (%s: %s) -- treating autonomy as OFF", type(exc).__name__, exc
            )
            return Profile()
        if row is None:
            return Profile()
        until = row["autonomous_until"]
        return Profile(
            autonomous=bool(row["autonomous"]),
            autonomous_until=None if until is None else int(until),
            updated_ts=int(row["updated_ts"]),
        )

    def profile_readable(self) -> bool:
        """Whether the `profile` row could actually be read.

        `get_profile()` fails closed on a damaged table, which is right for the trading path but
        indistinguishable from "the user never opted in". This lets a caller tell a human that
        the stored setting is UNKNOWN rather than confidently reporting it as off.
        """
        try:
            self._conn.execute("SELECT autonomous FROM profile WHERE id = 1").fetchone()
        except sqlite3.Error:
            return False
        return True

    def set_autonomous(self, value: bool, now_ts: int, expires_ts: int | None = None) -> None:
        """Record the user's autonomy choice, upserting the single profile row.

        `expires_ts=None` means the choice never lapses. Passing a timestamp makes autonomy
        expire on its own -- the time bound the removed bypass-arm token used to enforce.
        """
        self._conn.execute(
            """
            INSERT INTO profile (id, autonomous, autonomous_until, updated_ts)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET autonomous = excluded.autonomous,
                                          autonomous_until = excluded.autonomous_until,
                                          updated_ts = excluded.updated_ts
            """,
            (1 if value else 0, expires_ts, now_ts),
        )
        self._conn.commit()

    # -- candle gap probes ----------------------------------------------------
    # A row asserts: "we asked the venue for this exact window and it returned nothing new."
    # That is what lets `--fail-on-gaps` be satisfiable -- see `data/gaps.py`.

    def record_gap_probe(
        self,
        product: str,
        granularity: Granularity,
        start_ts: int,
        end_ts: int,
        n_missing: int,
        probed_at: int,
    ) -> None:
        """Mark a window as probed-and-empty. Idempotent; re-probing refreshes `probed_at`."""
        self._conn.execute(
            """
            INSERT INTO candle_gap_probes
                (product, granularity, start_ts, end_ts, n_missing, probed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(product, granularity, start_ts, end_ts)
            DO UPDATE SET probed_at = excluded.probed_at, n_missing = excluded.n_missing
            """,
            (product, Granularity(granularity).value, start_ts, end_ts, n_missing, probed_at),
        )
        self._conn.commit()

    def get_gap_probes(
        self, product: str | None = None, granularity: Granularity | None = None
    ) -> list[tuple[str, str, int, int]]:
        """Return probed-and-empty window keys, matching `gaps.GapWindow.key()`."""
        query = "SELECT product, granularity, start_ts, end_ts FROM candle_gap_probes"
        clauses: list[str] = []
        params: list[object] = []
        if product is not None:
            clauses.append("product = ?")
            params.append(product)
        if granularity is not None:
            clauses.append("granularity = ?")
            params.append(Granularity(granularity).value)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        rows = self._conn.execute(query, params).fetchall()
        return [(r["product"], r["granularity"], r["start_ts"], r["end_ts"]) for r in rows]

    def clear_gap_probes(self, product: str | None = None) -> int:
        """Forget probe results so they are re-attempted. Returns rows removed."""
        if product is None:
            cursor = self._conn.execute("DELETE FROM candle_gap_probes")
        else:
            cursor = self._conn.execute(
                "DELETE FROM candle_gap_probes WHERE product = ?", (product,)
            )
        self._conn.commit()
        return cursor.rowcount

    # -- asset attestations ---------------------------------------------------
    # Human-recorded shariah classification for allowlist admission (KB §28.4/§65.5). Absent =
    # unknown = rejected; see `keel/compliance/screen.py`.

    def upsert_asset_attestation(
        self,
        asset: str,
        sector: str,
        backing: str,
        pays_yield: bool,
        source: str,
        attested_by: str,
        attested_at: int,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO asset_attestations
                (asset, sector, backing, pays_yield, source, attested_by, attested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset) DO UPDATE SET
                sector = excluded.sector,
                backing = excluded.backing,
                pays_yield = excluded.pays_yield,
                source = excluded.source,
                attested_by = excluded.attested_by,
                attested_at = excluded.attested_at
            """,
            (asset, sector, backing, int(pays_yield), source, attested_by, attested_at),
        )
        self._conn.commit()

    def get_asset_attestation(self, asset: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM asset_attestations WHERE asset = ?", (asset,)
        ).fetchone()
        return dict(row) if row is not None else None

    def get_asset_attestations(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM asset_attestations ORDER BY asset").fetchall()
        return [dict(row) for row in rows]

    # -- instrument attestations --------------------------------------------------
    # Human-recorded claim about WHAT CONTRACT a venue listing is (spot/cfd/perpetual/future/
    # option/leveraged_token) -- keyed per (venue, product_id) rather than per asset, because one
    # venue lists both BTC-USD (spot) and BTC-PERP-USD (perpetual) against the same base leg, so a
    # per-asset wrapper claim would be factually wrong. Absent = unknown = rejected; see
    # `keel/compliance/screen.py`.

    def upsert_instrument_attestation(
        self,
        venue: str,
        product_id: str,
        wrapper: str,
        source: str,
        attested_by: str,
        attested_at: int,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO instrument_attestations
                (venue, product_id, wrapper, source, attested_by, attested_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(venue, product_id) DO UPDATE SET
                wrapper = excluded.wrapper,
                source = excluded.source,
                attested_by = excluded.attested_by,
                attested_at = excluded.attested_at
            """,
            (venue, product_id, wrapper, source, attested_by, attested_at),
        )
        self._conn.commit()

    def get_instrument_attestation(self, venue: str, product_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM instrument_attestations WHERE venue = ? AND product_id = ?",
            (venue, product_id),
        ).fetchone()
        return dict(row) if row is not None else None

    def get_instrument_attestations(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM instrument_attestations ORDER BY venue, product_id"
        ).fetchall()
        return [dict(row) for row in rows]

    # -- screen exceptions ------------------------------------------------------
    # Documented, per-asset per-criterion waivers of an allowlist-screen admission criterion (KB
    # PAXG/history case). See `keel/compliance/screen.py` -- only criteria in `WAIVABLE_CRITERIA`
    # are ever honoured, so a row here can never bypass the shariah core.

    def upsert_screen_exception(
        self,
        asset: str,
        criterion: str,
        rationale: str,
        granted_by: str,
        granted_at: int,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO screen_exceptions
                (asset, criterion, rationale, granted_by, granted_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(asset, criterion) DO UPDATE SET
                rationale = excluded.rationale,
                granted_by = excluded.granted_by,
                granted_at = excluded.granted_at
            """,
            (asset, criterion, rationale, granted_by, granted_at),
        )
        self._conn.commit()

    def get_screen_exceptions(self, asset: str) -> dict[str, str]:
        rows = self._conn.execute(
            "SELECT criterion, rationale FROM screen_exceptions WHERE asset = ?", (asset,)
        ).fetchall()
        return {row["criterion"]: row["rationale"] for row in rows}

    def list_screen_exceptions(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM screen_exceptions ORDER BY asset, criterion"
        ).fetchall()
        return [dict(row) for row in rows]

    def delete_screen_exception(self, asset: str, criterion: str) -> int:
        """Returns the number of rows removed (0 or 1), so a caller can tell a real revoke from
        a no-op on a row that never existed rather than echoing success either way."""
        cursor = self._conn.execute(
            "DELETE FROM screen_exceptions WHERE asset = ? AND criterion = ?", (asset, criterion)
        )
        self._conn.commit()
        return cursor.rowcount
