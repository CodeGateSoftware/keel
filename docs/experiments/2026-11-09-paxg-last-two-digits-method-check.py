#!/usr/bin/env python
"""Method check for `2026-11-09-paxg-last-two-digits.py`: can it see a planted effect, and stay
quiet on noise?

Run before the real test, on synthetic windows only (no market data). Two cases, 3,000 windows
each, start prices uniform on $3,000-$5,000, every move +/-0.3%:
- effect 0.00: direction is a coin flip whatever the digits;
- effect 0.06: dollar digits 00-49 go up 56% of the time, 50-99 go up 44%.
300 permutations per test (the real run uses 2,000), so the smallest reachable p is 1/301.

    PYTHONPATH=. python docs/experiments/2026-11-09-paxg-last-two-digits-method-check.py
"""

from __future__ import annotations

import importlib.util
import random
from decimal import Decimal
from pathlib import Path

_DRIVER = Path(__file__).with_name("2026-11-09-paxg-last-two-digits.py")
_spec = importlib.util.spec_from_file_location("paxg_digits", _DRIVER)
assert _spec and _spec.loader
driver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(driver)
driver.N_PERM = 300


def synthetic(effect: float, n: int = 3000, seed: int = 0) -> list[tuple[int, Decimal, Decimal]]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        start = Decimal(rng.randint(300000, 500000)) / 100
        p_up = 0.5 + (effect if int(start) % 100 < 50 else -effect)
        move = Decimal("1.003") if rng.random() < p_up else Decimal("0.997")
        out.append((i * driver.WINDOW_S, start, start * move))
    return out


def main() -> None:
    for effect in (0.0, 0.06):
        r = driver.run_arm("synthetic", synthetic(effect), "dollars")
        print(
            f"effect {effect:.2f}: p_chi {r['p_chi']}  p_oos {r['p_oos']}  "
            f"oos hit {r['oos_hit_rate']}  shows_dependence {r['shows_dependence']}"
        )


if __name__ == "__main__":
    main()
