#!/usr/bin/env python
"""The 2026-09-30 pooled forward-trades review (#427, tracked in #353) — the driver.

PRE-REGISTERED BEFORE THE EVENT (this docstring is the pre-registration; the pure seams it
runs are `keel/research/pooled_review.py`, pinned by `tests/research/test_pooled_review.py`).

## The question
#359 scheduled a review of the FORWARD trades at a floor of n=100 pooled; #427 found that
floor dishonest as written (100 pooled is ~39 effective observations; the review can only
see an edge of 20 points or larger), and the correction of record — PR #503, discussion
#359's corrected comment — reframed the event as DESCRIPTIVE: report the pooled forward
win rate against its break-even with the n_eff correction at the ACTUAL n, the interval,
and ALWAYS the sentence "at this n_eff (N effective of M pooled), this review can only see
an edge of X points or larger". This driver runs that review in one command so the report
cannot be written imprecisely: the sentence is constructed into the artifact, never typed.

## The pre-registered pool (frozen)
1. **Profiles.** The deployment's own ledgers, read READ-ONLY (`file:...?mode=ro`, the
   significance/optuna house convention — the driver never writes to a deployment db):
   by default `~/keel/keel.db` (the deployment's paper/dca ledger), `~/keel/keel-live.db`
   (live) and `~/keel/keel-paperhourly.db` (the paper-hourly evidence profile #353 named).
   Override with repeatable `--db PATH`; every listed profile MUST be reachable read-only
   — a silently smaller pool than pre-registered would change the review without saying so.
2. **A pooled trade is ONE closed forward round trip, win/loss resolved.** Two records of
   it, deduplicated on (profile, product, quantity, exit fill):
   - a `trade_outcomes` row — the authoritative closed-trade ledger rails 11/16 read, its
     `pnl_net` carried VERBATIM (realized, net of fees, written by `streak.py`);
   - where the ledger has none, a filled BUY matched to a filled SELL of the same profile,
     product, rule, mode and quantity, FIFO by order id, priced with the ledger writer's
     own formula `(exit - entry) * qty - entry fee - exit fee`.
3. **Win/loss by the SIGN of fee-honest net pnl.** A net pnl of exactly 0 is a SCRATCH and
   counts toward nothing (`significance.py`'s contract). OPEN positions (filled BUY with
   no matching SELL) and unfilled/rejected orders are EXCLUDED and counted in the
   composition table. DCA round trips count (their forward P&L is real) and are labelled.
4. **The measurement** is `keel.research.significance.significance` on the pooled outcome
   rows: break-even `1/(1+b)` from the payoff of the SAME trades, one-sided 5%, the
   standard error on `throughput.n_eff` at the ACTUAL n, and the fee fraction MEASURED off
   the pool (fees paid over notional traded) rather than assumed — the forward trades'
   regime is an outcome.
5. **No pass/fail verdict on the edge.** The report renders the measurements but none of
   the significance machinery's verdict vocabulary; the only verdict-shaped statement is
   about POWER (the sentence). The owner's floor decision (n=100 descriptive vs
   `min_trades` 259+ confirmatory) stays with the owner — the report says so.
6. **Refusal.** Zero pooled trades refuses ("nothing to review"), writes only a refusal
   JSONL row, and exits 2 — never a degenerate report.

## Provenance and safety
- READ-ONLY against every db: `sqlite3.connect(f"file:{db}?mode=ro", uri=True)`. The only
  writes are the `--out` markdown report and the `--jsonl` row (one row per run; re-running
  truncates and rewrites both).
- A run before 2026-09-30 labels itself a PREVIEW and states that the event re-runs on
  2026-09-30 (#353) under this same pre-registration — the pool reported is what exists
  on the run date, honestly, whatever its size.

    .venv/bin/python docs/experiments/2026-09-30-pooled-review.py
    .venv/bin/python docs/experiments/2026-09-30-pooled-review.py \
        --db ~/keel/keel.db --db ~/keel/keel-live.db --db ~/keel/keel-paperhourly.db \
        --out docs/experiments/2026-08-27-pooled-review-preview.md \
        --jsonl docs/experiments/2026-08-27-pooled-review-preview.jsonl
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from keel.research.pooled_review import (
    EVENT_DATE,
    DescriptiveReview,
    LedgerRow,
    OrderRow,
    OrdersRead,
    build_sample,
    descriptive_review,
    is_refused,
    render_report,
    round_trips_from_orders,
)
from keel.research.throughput import design_effect

DEFAULT_DBS = (
    "/Users/elmehdiaitbrahim/keel/keel.db",
    "/Users/elmehdiaitbrahim/keel/keel-live.db",
    "/Users/elmehdiaitbrahim/keel/keel-paperhourly.db",
)
DEFAULT_OUT = "docs/experiments/2026-09-30-pooled-review.md"
DEFAULT_JSONL = "docs/experiments/2026-09-30-pooled-review.jsonl"


def _connect_ro(db_path: str) -> sqlite3.Connection:
    """The house read-only connection (`mode=ro`); the deployment dbs are never written."""
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def read_orders(db_path: str) -> tuple[list[OrderRow], dict[int, str]]:
    """The profile's `orders` rows (money as Decimal) and its `rule_id -> rules.kind` map.

    Ascending id — the ledger's own event sequencing, which the matcher relies on because
    the live `created_at` values demonstrably disagree with it.
    """
    connection = _connect_ro(db_path)
    try:
        order_rows = connection.execute(
            "SELECT id, mode, product_id, side, qty, status, actual_fill, fee, rule_id, "
            "created_at FROM orders ORDER BY id"
        ).fetchall()
        rule_rows = connection.execute("SELECT id, kind FROM rules ORDER BY id").fetchall()
    finally:
        connection.close()
    orders = [
        OrderRow(
            id=int(row["id"]),
            mode=str(row["mode"]),
            product_id=str(row["product_id"]),
            side=str(row["side"]),
            qty=Decimal(str(row["qty"])),
            status=str(row["status"]),
            actual_fill=None if row["actual_fill"] is None else Decimal(str(row["actual_fill"])),
            fee=None if row["fee"] is None else Decimal(str(row["fee"])),
            rule_id=None if row["rule_id"] is None else int(row["rule_id"]),
            created_at=int(row["created_at"]),
        )
        for row in order_rows
    ]
    return orders, {int(row["id"]): str(row["kind"]) for row in rule_rows}


def read_ledger(db_path: str) -> list[LedgerRow]:
    """The profile's `trade_outcomes` rows, oldest first (the ledger reader's convention)."""
    connection = _connect_ro(db_path)
    try:
        rows = connection.execute(
            "SELECT product_id, rule_name, opened_at, closed_at, qty, entry_fill, "
            "exit_fill, fees, pnl_net FROM trade_outcomes ORDER BY closed_at, id"
        ).fetchall()
    finally:
        connection.close()
    return [
        LedgerRow(
            product_id=str(row["product_id"]),
            rule_name=str(row["rule_name"]),
            opened_at=int(row["opened_at"]),
            closed_at=int(row["closed_at"]),
            qty=Decimal(str(row["qty"])),
            entry_fill=Decimal(str(row["entry_fill"])),
            exit_fill=Decimal(str(row["exit_fill"])),
            fees=Decimal(str(row["fees"])),
            pnl_net=Decimal(str(row["pnl_net"])),
        )
        for row in rows
    ]


def jsonl_row(review: DescriptiveReview) -> dict[str, Any]:
    """The one-row-per-run artifact record: every Decimal as a string, like the #475 run."""
    sample = review.sample
    stat = review.stat
    return {
        "run_date": review.run_date,
        "event_date": review.event_date,
        "profiles": list(review.profiles),
        "status": "refused" if review.refusal else "reported",
        "pooled_n": sample.n_pooled(),
        "wins": stat.wins,
        "losses": stat.n_trades - stat.wins,
        "scratches": sample.scratches(),
        "excluded_open": sample.excluded_open,
        "excluded_unfilled": sample.excluded_unfilled,
        "stray_sells": sample.stray_sells,
        "deduped": sample.deduped,
        "win_rate": str(stat.win_rate),
        "payoff_b": str(stat.payoff_b),
        "break_even": str(stat.break_even),
        "edge": str(stat.edge),
        "n_effective": str(stat.n_effective),
        "edge_ci_low": str(stat.edge_ci_low),
        "fee_pct": str(review.fee_pct),
        "detectable_edge": str(stat.detectable_edge),
        "power_sentence": review.sentence,
        "design_effect": str(design_effect()),
        "composition": [
            {
                "profile": s.profile,
                "modes": list(s.modes),
                "rules": list(s.rules),
                "closed": s.closed,
                "of_which_dca": s.of_which_dca,
                "open_buys": s.open_buys,
                "unfilled": s.unfilled,
                "stray_sells": s.stray_sells,
                "ledger_rows": s.ledger_rows,
                "deduped": s.deduped,
                "inversions": s.inversions,
            }
            for s in sample.summaries
        ],
        "notes": list(review.notes),
        "refusal": list(review.refusal) if review.refusal else None,
    }


def _not_reachable(db_path: str) -> bool:
    """True when the db cannot be opened read-only (missing or unreadable)."""
    try:
        connection = _connect_ro(db_path)
    except sqlite3.Error:
        return True
    connection.close()
    return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="The 2026-09-30 pooled forward-trades review (#427/#353), read-only",
    )
    parser.add_argument(
        "--db",
        action="append",
        dest="dbs",
        default=None,
        help=f"Deployment profile db, repeatable (default: {list(DEFAULT_DBS)}). Every "
        "listed profile must be reachable read-only -- a silently smaller pool would "
        "change the review.",
    )
    parser.add_argument(
        "--out", default=DEFAULT_OUT, help=f"Markdown report (default: {DEFAULT_OUT})"
    )
    parser.add_argument(
        "--jsonl",
        default=DEFAULT_JSONL,
        help=f"One-row artifact record (default: {DEFAULT_JSONL})",
    )
    parser.add_argument(
        "--run-date",
        default=None,
        help=f"Run date label, ISO (default: today UTC). A date before {EVENT_DATE} labels "
        "the report a preview of the event.",
    )
    args = parser.parse_args()

    dbs = args.dbs if args.dbs else list(DEFAULT_DBS)
    run_date = args.run_date or datetime.now(UTC).date().isoformat()

    missing = [db for db in dbs if _not_reachable(db)]
    if missing:
        print("REFUSED: pre-registered profile db(s) not reachable read-only:", file=sys.stderr)
        for db in missing:
            print(f"  {db}", file=sys.stderr)
        print(
            "The pool cannot be read as pre-registered; refusing rather than review a "
            "silently smaller pool.",
            file=sys.stderr,
        )
        sys.exit(2)

    per_profile: list[tuple[str, OrdersRead, list[LedgerRow]]] = []
    for db in dbs:
        orders, rule_kinds = read_orders(db)
        read = round_trips_from_orders(db, orders, rule_kinds)
        ledger = read_ledger(db)
        per_profile.append((db, read, ledger))
        print(
            f"{db}: {len(orders)} orders -> {len(read.trips)} closed round trips "
            f"({read.open_buys} open, {read.unfilled_orders} unfilled, "
            f"{read.stray_sells} stray sells); {len(ledger)} ledger rows"
        )

    sample = build_sample(per_profile)
    review = descriptive_review(sample, run_date=run_date)

    with open(args.jsonl, "w") as fh:
        fh.write(json.dumps(jsonl_row(review)) + "\n")

    if is_refused(sample):
        print()
        for line in review.refusal or ():
            print(line)
        print(f"\nartifact (refusal row only): {args.jsonl}")
        sys.exit(2)

    lines = render_report(review)
    with open(args.out, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    print()
    print("\n".join(lines))
    print(f"\nartifacts: {args.out} + {args.jsonl}")


if __name__ == "__main__":
    main()
