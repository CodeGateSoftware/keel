"""Dev script: export a fixed candle slice from a local keel.db into a committed fixture.

`keel.db` is gitignored (it holds personal trading data), so the regression baseline cannot
read it at test time. Run this manually only when the baseline corpus must be regenerated:

    uv run python tests/baseline/export_baseline.py --db keel.db

Candle OHLCV are stored as exact decimal TEXT, so this round-trips without precision loss.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

PRODUCT = "BTC-USD"
GRANULARITY = "ONE_DAY"
OUT = Path(__file__).parent.parent / "fixtures" / "baseline_candles.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="keel.db")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    rows = conn.execute(
        "SELECT ts, o, h, l, c, v FROM candles "
        "WHERE product_id = ? AND granularity = ? ORDER BY ts ASC",
        (PRODUCT, GRANULARITY),
    ).fetchall()
    conn.close()

    if not rows:
        raise SystemExit(f"no candles for {PRODUCT}/{GRANULARITY} in {args.db}")

    payload = {
        "product_id": PRODUCT,
        "granularity": GRANULARITY,
        "candles": [
            {
                "ts": row[0],
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
                "volume": row[5],
            }
            for row in rows
        ],
    }
    OUT.write_text(json.dumps(payload, indent=1) + "\n")
    print(f"wrote {len(rows)} candles to {OUT}")


if __name__ == "__main__":
    main()
