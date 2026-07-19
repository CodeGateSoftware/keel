"""Dev script: regenerate the committed backtest golden file.

Deliberately separate from the test, which is read-only. This baseline is the only thing
proving the monorepo migration does not change strategy output, so a test able to rewrite its
own expected values could silently launder a real behaviour change into the fixture.

Run this only when a strategy change is intended, and review the resulting diff:

    uv run python tests/baseline/regenerate_golden.py

Needs no database -- it reads the committed candle fixture.
"""

from __future__ import annotations

import json

from tests.baseline.serialize import GOLDEN, run_baseline_backtest


def main() -> None:
    payload = run_baseline_backtest()
    GOLDEN.write_text(json.dumps(payload, indent=1) + "\n")
    print(f"wrote {GOLDEN} ({payload['n_trades']} trades)")


if __name__ == "__main__":
    main()
