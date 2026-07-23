"""Bankroll-sizing strategies: pure functions returning a FRACTION OF BANKROLL TO RISK.

This module is a stdlib-only, educational study of capital-allocation mathematics (the Kelly
Criterion and neighboring formulas from the `keeks` library and classic portfolio theory). It is
NOT betting advice and is not wired into `keel`'s execution path -- it exists to compare the
Kelly-optimal risk fraction against `keel`'s fixed-fractional risk sizing
(`keel/execution/sizing.py::size`, default `risk_pct = 0.01`).

Convention used throughout: every function returns a fraction `f` in `[0, 1]` meaning "risk `f`
of current bankroll on this trade." Under the simplified binary/R-multiple trade model used by
the accompanying simulation (`simulate.py`):
  - a LOSING trade costs the trader `f * bankroll` (the full risked amount), and
  - a WINNING trade pays `b * f * bankroll` (b = reward:risk multiple, i.e. R:R).

All functions clamp their output to `[0, 1]` and guard against degenerate inputs (zero/negative
odds, zero bankroll, etc.) rather than raising, so they are safe to call in a tight simulation
loop. Where guarding a bad input by clamping/zeroing would silently hide a real bug (e.g.
probabilities outside [0, 1]), we raise `ValueError` instead.
"""

from __future__ import annotations


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp `x` into `[lo, hi]`."""
    return max(lo, min(hi, x))


def kelly_fraction(p: float, b: float) -> float:
    """Full Kelly criterion: the growth-optimal fraction of bankroll to risk per bet.

    Formula (Kelly, 1956; as used throughout the `keeks` library):

        f* = (b*p - q) / b,   where q = 1 - p

    `p` is the win probability, `b` is the reward:risk multiple (a win pays `b` units per unit
    risked; a loss costs 1 unit risked -- i.e. `b` is the trade's R:R). This is the fraction of
    bankroll that maximizes the expected logarithm of terminal wealth (long-run geometric growth)
    for a sequence of independent, identically-distributed bets with known `p` and `b`.

    Returns 0 if the edge is non-positive (`b*p - q <= 0`, i.e. no edge or a losing edge) rather
    than a negative fraction (negative Kelly would mean "bet the other side," which does not
    apply to a long-only, no-shorting instrument). Result is always clamped to `[0, 1]`.

    Raises `ValueError` if `p` is not in `[0, 1]` or `b <= 0`.
    """
    if not (0.0 <= p <= 1.0):
        raise ValueError(f"kelly_fraction: p must be in [0, 1], got {p}")
    if b <= 0.0:
        raise ValueError(f"kelly_fraction: b must be > 0, got {b}")

    q = 1.0 - p
    edge = b * p - q
    if edge <= 0.0:
        return 0.0
    return _clamp(edge / b)


def fractional_kelly(p: float, b: float, lam: float) -> float:
    """Fractional ("lambda") Kelly: `lam * kelly_fraction(p, b)`.

    Betting a fixed fraction `lam` (e.g. 0.5 for "half-Kelly", 0.25 for "quarter-Kelly") of the
    full-Kelly stake trades away some geometric growth for a large reduction in variance and
    drawdown depth -- the classic risk-management refinement of Kelly betting, since full Kelly
    is highly sensitive to estimation error in `p`/`b` and produces violent equity swings even
    when `p`/`b` are known exactly.

    Raises `ValueError` if `lam < 0` (a negative multiplier is not a fractional-Kelly scheme).
    Result is clamped to `[0, 1]`.
    """
    if lam < 0.0:
        raise ValueError(f"fractional_kelly: lam must be >= 0, got {lam}")

    return _clamp(lam * kelly_fraction(p, b))


def drawdown_adjusted_kelly(p: float, b: float, current_dd: float, max_dd: float) -> float:
    """Dynamic, drawdown-scaled Kelly: shrink the Kelly stake as the current drawdown approaches
    a tolerance ceiling `max_dd`.

        f = clamp(1 - current_dd/max_dd, 0, 1) * kelly_fraction(p, b)

    At `current_dd = 0` (no drawdown) this equals full Kelly. As `current_dd -> max_dd` the
    multiplier linearly decays to 0, so a path that has already suffered its maximum tolerated
    drawdown stops risking anything until it recovers. `current_dd >= max_dd` returns exactly 0.

    `current_dd` and `max_dd` are both fractions of peak equity (e.g. `current_dd = 0.10` means
    "10% below the equity high-water mark"). Raises `ValueError` if `max_dd <= 0` or
    `current_dd < 0`.
    """
    if max_dd <= 0.0:
        raise ValueError(f"drawdown_adjusted_kelly: max_dd must be > 0, got {max_dd}")
    if current_dd < 0.0:
        raise ValueError(f"drawdown_adjusted_kelly: current_dd must be >= 0, got {current_dd}")

    if current_dd >= max_dd:
        return 0.0

    scale = _clamp(1.0 - current_dd / max_dd)
    return _clamp(scale * kelly_fraction(p, b))


def fixed_fraction(c: float = 0.01) -> float:
    """Constant fixed-fractional risk: always risk `c` of current bankroll.

    This is `keel`'s current live sizing scheme (`keel/execution/sizing.py::size`, config default
    `risk_pct = 0.01` i.e. `c = 0.01`). It ignores `p` and `b` entirely -- the same fraction is
    risked win-streak or lose-streak, edge-rich regime or edge-poor regime. Included here purely
    as the baseline every Kelly-family strategy in this study is compared against.

    Raises `ValueError` if `c` is negative. Result is clamped to `[0, 1]`.
    """
    if c < 0.0:
        raise ValueError(f"fixed_fraction: c must be >= 0, got {c}")

    return _clamp(c)


def cppi_fraction(bankroll: float, floor: float, multiplier: float) -> float:
    """Constant Proportional Portfolio Insurance (CPPI): risk a multiple of the "cushion" above a
    protected floor.

        f = clamp(multiplier * (bankroll - floor) / bankroll, 0, 1)

    `floor` is a dollar level the strategy tries never to breach (in the accompanying simulation
    this floor ratchets upward with new equity highs -- that ratcheting is external state managed
    by the caller/simulation loop, not by this pure function). `multiplier` ("m") controls how
    aggressively the cushion (`bankroll - floor`) is levered into risk; CPPI research/practice
    commonly uses m in the 2-5 range. If `bankroll <= floor` (cushion exhausted or breached) this
    returns 0 -- there is nothing left to safely risk.

    Raises `ValueError` if `bankroll <= 0` or `multiplier < 0`.
    """
    if bankroll <= 0.0:
        raise ValueError(f"cppi_fraction: bankroll must be > 0, got {bankroll}")
    if multiplier < 0.0:
        raise ValueError(f"cppi_fraction: multiplier must be >= 0, got {multiplier}")

    cushion = bankroll - floor
    if cushion <= 0.0:
        return 0.0
    return _clamp(multiplier * cushion / bankroll)


def naive_flat_fraction(fixed_stake: float, bankroll: float) -> float:
    """Flat-stake baseline: risk a constant DOLLAR amount `fixed_stake`, expressed as a fraction
    of current `bankroll`.

        f = fixed_stake / bankroll

    Unlike `fixed_fraction` (which risks a constant *percentage* and therefore compounds), a flat
    dollar stake risks a *shrinking* fraction of bankroll as bankroll grows, and a *growing*
    fraction as bankroll shrinks -- the opposite of geometric (fractional-fractional) sizing. It
    is a common naive baseline ("just risk $10 a trade") worth contrasting against the
    Kelly family.

    Raises `ValueError` if `fixed_stake < 0` or `bankroll <= 0`. Result is clamped to `[0, 1]`
    (a stake larger than the whole bankroll is capped at "risk everything").
    """
    if fixed_stake < 0.0:
        raise ValueError(f"naive_flat_fraction: fixed_stake must be >= 0, got {fixed_stake}")
    if bankroll <= 0.0:
        raise ValueError(f"naive_flat_fraction: bankroll must be > 0, got {bankroll}")

    return _clamp(fixed_stake / bankroll)


def merton_fraction(exp_return: float, variance: float, gamma: float) -> float:
    """Merton portfolio fraction: the continuous-time analogue of Kelly for an investor with
    constant relative risk aversion `gamma`, allocating to a single risky asset with expected
    excess return `exp_return` and return `variance` against a riskless asset.

        f* = exp_return / (gamma * variance)

    (Merton, 1969/1971.) `gamma = 1` recovers the log-utility / Kelly-equivalent investor;
    `gamma > 1` is more risk-averse and allocates less than Kelly-equivalent; `gamma < 1` (but
    still > 0) allocates more. Included here as a second, independently-derived formula for
    "how much should a growth-optimizing (or risk-averse) investor allocate," to cross-check the
    discrete-bet Kelly formula above.

    Returns 0 if `exp_return <= 0` (no edge -> allocate nothing) rather than a negative fraction.
    Raises `ValueError` if `variance <= 0` or `gamma <= 0`. Result is clamped to `[0, 1]`.
    """
    if variance <= 0.0:
        raise ValueError(f"merton_fraction: variance must be > 0, got {variance}")
    if gamma <= 0.0:
        raise ValueError(f"merton_fraction: gamma must be > 0, got {gamma}")

    if exp_return <= 0.0:
        return 0.0
    return _clamp(exp_return / (gamma * variance))
