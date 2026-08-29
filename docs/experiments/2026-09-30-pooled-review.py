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
   it, deduplicated on (profile, product, quantity, entry fill, exit fill):
   - a `trade_outcomes` row — the authoritative closed-trade ledger rails 11/16 read, its
     `pnl_net` carried VERBATIM (realized, net of fees, written by `streak.py`). Its
     `fees` column is the EXIT order's fee only (the entry fee is folded into `pnl_net`
     and stored nowhere), so when the row's orders twin is deduped away the twin's
     both-legs fees ride into the kept trip, and a ledger row with no twin renders its
     fee as the labelled lower bound it is;
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
   regime is an outcome. The n is the COUNTED one (wins+losses; a scratch counts toward
   nothing), and the power sentence is generated at that same n so the artifact carries
   one n_eff basis, never two.
5. **No pass/fail verdict on the edge.** The report renders the measurements but none of
   the significance machinery's verdict vocabulary; the only verdict-shaped statement is
   about POWER (the sentence). The owner's floor decision (n=100 descriptive vs
   `min_trades` 259+ confirmatory) stays with the owner — the report says so.
6. **Refusal.** Zero counted trades refuses ("nothing to review" — an empty pool, or a
   pool whose every trip is a scratch), writes only a refusal JSONL row, and exits 2 —
   never a degenerate report. A db that cannot be read as pre-registered (not reachable
   read-only, a missing `orders`/`trade_outcomes` table, a 'filled' order row with no
   fill/fee) refuses the same way, with a named reason — never a traceback.

## Provenance and safety
- READ-ONLY against every db: `sqlite3.connect(f"file:{db}?mode=ro", uri=True)`. The only
  writes are the `--out` markdown report and the `--jsonl` row (one row per run; a
  non-refused re-run truncates and rewrites both). A REFUSED run rewrites only the JSONL
  (its refusal row is the record of record): a markdown report from an earlier run is
  left in place, STALE — read the JSONL, and delete the stale markdown by hand.
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
from typing import Any

# `_connect_ro`/`read_orders`/`read_ledger` used to be defined here; #601 moved them into
# `keel.commands.research` (the new `keel research pooled-review` front door) and this driver
# now IMPORTS them, so there is exactly one reader of a deployment database and the CLI and this
# pre-registered driver structurally cannot diverge on what "the pool" means. `DEFAULT_DBS` moves
# with them for the same reason -- one literal, not two copies that could drift apart.
from keel.commands.research import DEFAULT_POOLED_REVIEW_DBS as DEFAULT_DBS
from keel.commands.research import _connect_ro, read_ledger, read_orders
from keel.research.pooled_review import (
    EVENT_DATE,
    DescriptiveReview,
    LedgerRow,
    OrdersRead,
    build_sample,
    descriptive_review,
    is_refused,
    render_report,
    round_trips_from_orders,
)
from keel.research.throughput import design_effect

DEFAULT_OUT = "docs/experiments/2026-09-30-pooled-review.md"
DEFAULT_JSONL = "docs/experiments/2026-09-30-pooled-review.jsonl"


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
        try:
            orders, rule_kinds = read_orders(db)
            read = round_trips_from_orders(db, orders, rule_kinds)
            ledger = read_ledger(db)
        except (ValueError, sqlite3.Error) as exc:
            # A db that cannot be read as pre-registered -- a missing orders/rules/
            # trade_outcomes table, or a 'filled' order row carrying no fill/fee -- refuses
            # with a named reason on the exit-2 path, never a traceback: the review never
            # silently shrinks the pool and never crashes on a deployment's bad rows.
            print(f"REFUSED: {db} cannot be read as pre-registered: {exc}", file=sys.stderr)
            sys.exit(2)
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
