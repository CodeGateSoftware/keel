"""keel doctor (#443) — one command that answers 'is this deployment actually working'.

`keel status` reports what a deployment is DOING; doctor reports whether it actually
CAN. The motivating case: a paper profile vetoed 15 of 15 detected setups on
`subscription_unattested` for weeks while every status line looked healthy, and the
diagnosis meant hand-parsing the JSONL log and recomputing sizing by hand.

Two disciplines make this worth running:

* every finding names the COMMAND that fixes it -- the value is the next step, not
  the diagnosis;
* `halted` is a first-class status beside ok/warn/fail. An armed kill-switch or an
  unexpired streak halt is a CORRECT state, deliberately entered, and must not fail
  the run. Only genuine faults do.

The checks are pure functions over plain values and log lines, so they are testable
without a database; the thin click command at the bottom is the only place that
touches the repo, the config, and the log file.

Slice 2 adds the two checks the issue names as remaining:

* config-vs-reality admissibility -- does `risk_pct` sizing fit the profile's own
  `caps.max_per_order_usd` at CURRENT ATR, product by product;
* per-product data health -- staleness, gaps and cold caches per allowlisted
  `(product, granularity)` series, with a closed market's staleness defused (FR-9).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import click
from keel_core.telemetry import current_venue
from keel_core.trade_scope import READ_ONLY, TRADING, TradeScopeState, VenueTradeScope

from keel.data.freshness import Freshness
from keel.execution import sizing
from keel.types import Granularity
from keel.version import build_info, check_install

#: Rail 17's TTL is the executor's constant; doctor only READS it (7 days).
TTL_SEC = 7 * 86_400

#: The window doctor judges data health (gaps, staleness) over -- the same 7-day horizon
#: as the veto sweep, so every "recent" verdict in one run means the same thing.
DOCTOR_WINDOW_SEC = 7 * 86_400

OK = "ok"
WARN = "warn"
FAIL = "fail"
HALTED = "halted"


@dataclass(frozen=True)
class Finding:
    """One doctor verdict. `fix` names the command that resolves it ('-' when none
    is needed); `detail` carries the numbers (days remaining, dollars, counts).

    `products` is the machine-readable per-product identity a WRAPPER reads from
    `--json` (#642): the point is that a cold SOL series must be nameable without the
    caller re-parsing `detail` prose. Defaulted to `()` and trailing, so every existing
    positional `Finding(...)` construction in this module is untouched. Left empty on
    every OK finding -- there is nothing to gate a caller's per-product decision on when
    the check passed.
    """

    name: str
    status: str
    headline: str
    detail: str
    fix: str
    products: tuple[str, ...] = ()


def _days(seconds: float) -> int:
    return int(max(seconds, 0) // 86_400)


def attestation_findings(
    subscription: Any | None,
    withdrawals_attested_at: int,
    now_ts: int,
    ttl_sec: int = TTL_SEC,
) -> list[Finding]:
    """Rails 14 and 17 -- days remaining, not just valid/invalid."""
    findings: list[Finding] = []

    if subscription is None:
        findings.append(
            Finding(
                "attest.subscription",
                FAIL,
                "no subscription attested",
                "rail 14 falls back to the unsubscribed allowance; every BUY must fit it",
                "keel subscription attest --venue <venue> --tier <tier>",
            )
        )
    else:
        due = int(subscription.attest_due_ts)
        remaining = _days(due - now_ts)
        if remaining <= 0:
            findings.append(
                Finding(
                    "attest.subscription",
                    WARN,
                    "attestation overdue",
                    f"due {abs(_days(now_ts - due))} day(s) ago; the rail reads it as stale",
                    "keel subscription attest --venue <venue> --tier <tier>",
                )
            )
        elif remaining <= 2:
            findings.append(
                Finding(
                    "attest.subscription",
                    WARN,
                    "attestation due soon",
                    f"{remaining} day(s) of freshness remain",
                    "keel subscription attest --venue <venue> --tier <tier>",
                )
            )
        else:
            findings.append(
                Finding(
                    "attest.subscription",
                    OK,
                    "subscription attested",
                    f"{remaining} day(s) of freshness remain",
                    "-",
                )
            )

    if withdrawals_attested_at <= 0:
        findings.append(
            Finding(
                "attest.withdrawals",
                FAIL,
                "withdrawal capability never attested",
                "rail 17 (qabd) halts every BUY entry until it is",
                "keel withdrawals attest --enabled",
            )
        )
    else:
        age = now_ts - withdrawals_attested_at
        remaining = _days(ttl_sec - age)
        if remaining <= 0:
            findings.append(
                Finding(
                    "attest.withdrawals",
                    FAIL,
                    "withdrawal attestation expired",
                    f"expired {abs(_days(age - ttl_sec))} day(s) ago; rail 17 halts entries",
                    "keel withdrawals attest --enabled",
                )
            )
        elif remaining <= 2:
            findings.append(
                Finding(
                    "attest.withdrawals",
                    WARN,
                    "withdrawal attestation due soon",
                    f"{remaining} day(s) remain on the {ttl_sec // 86_400}-day TTL",
                    "keel withdrawals attest --enabled",
                )
            )
        else:
            findings.append(
                Finding(
                    "attest.withdrawals",
                    OK,
                    "withdrawal capability attested",
                    f"{remaining} day(s) remain on the {ttl_sec // 86_400}-day TTL",
                    "-",
                )
            )
    return findings


def _utc_date(ts: int) -> str:
    """`YYYY-MM-DD` -- an operator judging whether a refusal is old news cannot read a raw
    epoch like `1750000000`; `keel scope attest`'s own doctor-facing rendering uses this same
    shape, so a refusal date reads identically everywhere an operator sees one."""
    return datetime.fromtimestamp(ts, tz=UTC).date().isoformat()


def trade_scope_findings(record: VenueTradeScope | None, venue: str) -> list[Finding]:
    """Rail 20 (#233) -- whether `venue`'s credential has proven (or can at least claim) it may
    place a live entry. Takes the record directly, like `attestation_findings` takes
    `subscription`, so this is unit-testable without a database.

    Two independent findings can come back: the primary ok/fail verdict rail 20 itself enforces,
    and -- only when a record has been re-attested but still carries `refuted_ts` -- a WARN
    alongside it. That second finding is the specific operator surface the design calls out: a
    re-attestation is how an operator reports "I rotated the credential", and the record keeps
    `refuted_ts` through it (rather than clearing it) precisely so doctor can still say "you
    re-attested a venue that refuted a credential on <date>" instead of the history silently
    vanishing.
    """
    attest_fix = f"keel scope attest --trading --venue {venue}"

    if record is None:
        return [
            Finding(
                "scope.trade",
                FAIL,
                "no trade scope attested",
                f"rail 20 vetoes every live entry on {venue} until the credential is attested "
                "or the venue itself confirms one",
                attest_fix,
            )
        ]

    findings: list[Finding] = []

    if record.state is TradeScopeState.REFUTED:
        reason = f" ({record.refuted_reason})" if record.refuted_reason else ""
        findings.append(
            Finding(
                "scope.trade",
                FAIL,
                "trade scope refuted",
                f"{venue} refused a live placement on this credential{reason}; rail 20 vetoes "
                "every live entry until it is re-attested with a working credential",
                attest_fix,
            )
        )
    elif record.state is TradeScopeState.CONFIRMED:
        findings.append(
            Finding(
                "scope.trade",
                OK,
                "trade scope confirmed",
                f"{venue} itself proved this credential can place live entries",
                "-",
            )
        )
    elif record.state is TradeScopeState.ATTESTED and record.attested_scope == TRADING:
        findings.append(
            Finding(
                "scope.trade",
                OK,
                "trade scope attested for trading",
                f"{venue}'s credential is attested for trading, but not yet confirmed by the "
                "venue itself -- this is an unconfirmed operator claim",
                "-",
            )
        )
    elif record.state is TradeScopeState.ATTESTED and record.attested_scope == READ_ONLY:
        findings.append(
            Finding(
                "scope.trade",
                FAIL,
                "trade scope attested read-only",
                f"{venue}'s credential is attested read-only; rail 20 vetoes every live entry "
                "until it is attested for trading",
                attest_fix,
            )
        )
    else:
        # UNVERIFIED, or any other combination `may_place_live_entry()` fails closed on.
        findings.append(
            Finding(
                "scope.trade",
                FAIL,
                "trade scope unverified",
                f"{venue} has a trade-scope row but it is unverified; rail 20 vetoes every "
                "live entry",
                attest_fix,
            )
        )

    if record.refuted_ts is not None and record.state is not TradeScopeState.REFUTED:
        findings.append(
            Finding(
                "scope.trade_reattested",
                WARN,
                "re-attested after a refutation",
                "you re-attested a venue that refuted a credential on "
                f"{_utc_date(record.refuted_ts)}",
                "-",
            )
        )

    return findings


def rail_state_findings(
    kill_switch: bool,
    streak_halt_until: int,
    drawdown_total: Decimal,
    now_ts: int,
    total_threshold: Decimal = Decimal("20"),
) -> list[Finding]:
    """Armed halts, reported as the deliberate states they are."""
    findings: list[Finding] = []
    if kill_switch:
        findings.append(
            Finding(
                "rail.kill_switch",
                HALTED,
                "kill switch engaged",
                "every entry is vetoed; this is a correct state, not a fault",
                "keel autonomy on",
            )
        )
    else:
        findings.append(
            Finding("rail.kill_switch", OK, "kill switch clear", "entries may proceed", "-")
        )

    if streak_halt_until > now_ts:
        hours = (streak_halt_until - now_ts) / 3600
        findings.append(
            Finding(
                "rail.streak_halt",
                HALTED,
                "consecutive-loss halt armed",
                f"clears in {hours:.0f}h; it expires on its own",
                "wait for the window to pass (rail 16 clears itself)",
            )
        )
    else:
        findings.append(Finding("rail.streak_halt", OK, "no streak halt", "-", "-"))

    if drawdown_total >= total_threshold:
        findings.append(
            Finding(
                "rail.drawdown",
                FAIL,
                "total drawdown at or past the 20% rail",
                f"{drawdown_total}% -- rail 11 is vetoing entries at this level",
                "see the operator runbook: drawdown recovery procedure",
            )
        )
    else:
        findings.append(
            Finding(
                "rail.drawdown",
                OK,
                "drawdown inside the rail",
                f"{drawdown_total}% of the 20% total",
                "-",
            )
        )
    return findings


def allowance_findings(
    month_to_date_spend: Decimal,
    allowance: Decimal | None,
    mean_buy_notional: Decimal | None,
) -> list[Finding]:
    """Rail 14 headroom: what remains, and how many typical orders that is."""
    if allowance is None:
        return [
            Finding(
                "allowance.headroom",
                OK,
                "unlimited allowance in force",
                f"allowance unlimited; month-to-date BUY spend {month_to_date_spend}",
                "-",
            )
        ]
    remaining = allowance - month_to_date_spend
    if remaining <= 0:
        return [
            Finding(
                "allowance.headroom",
                WARN,
                "allowance exhausted",
                f"month-to-date BUY spend {month_to_date_spend} of {allowance}; the rail "
                "vetoes further BUYs until the month rolls over",
                "keel subscription show  (then wait for rollover or attest a higher tier)",
            )
        ]
    detail = f"{remaining} of {allowance} remains (spent {month_to_date_spend})"
    if mean_buy_notional and mean_buy_notional > 0:
        typical = (remaining / mean_buy_notional).quantize(Decimal("1"), ROUND_HALF_UP)
        detail += f" -- about {typical} typical order(s)"
    return [Finding("allowance.headroom", OK, "allowance headroom", detail, "-")]


def veto_findings(lines: Iterable[str], since_ts: float) -> list[Finding]:
    """The motivating case, made impossible to miss: aggregate `executor.order_vetoed`
    events since `since_ts` and name the dominant reason's fix. One reason vetoing
    everything is a FAIL; a spread of reasons is a WARN with the top three."""
    counts: dict[str, int] = {}
    total = 0
    for line in lines:
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if event.get("event") != "executor.order_vetoed":
            continue
        if float(event.get("ts", 0)) < since_ts:
            continue
        total += 1
        for violation in event.get("violations", []):
            reason = str(violation).split(":", 1)[0].strip()
            counts[reason] = counts.get(reason, 0) + 1

    if total == 0:
        return [
            Finding(
                "veto.recent",
                OK,
                "no recent vetoes",
                "no executor.order_vetoed events in the window",
                "-",
            )
        ]

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    top_reason, top_count = ranked[0]
    top_line = ", ".join(f"{reason} x{count}" for reason, count in ranked[:3])
    if top_count >= total:  # one reason vetoed EVERY entry in the window
        return [
            Finding(
                "veto.recent",
                FAIL,
                f"every entry vetoed by {top_reason}",
                f"{top_count} of {total} vetoed entries, all on {top_reason} -- this is "
                f"the pattern to catch ({top_line})",
                _fix_for_reason(top_reason),
            )
        ]
    return [
        Finding(
            "veto.recent",
            WARN,
            f"{total} vetoed entries in the window",
            f"top reasons: {top_line}",
            _fix_for_reason(top_reason),
        )
    ]


def _fix_for_reason(reason: str) -> str:
    if "subscription" in reason or "unattested" in reason:
        return "keel subscription attest --venue <venue> --tier <tier>"
    if "allowance" in reason:
        return "keel subscription show  (headroom vs the month's cap)"
    if "kill" in reason or "drawdown" in reason or "streak" in reason:
        return "see the operator runbook: the halt and how it clears"
    return "keel status  (then the operator runbook for the failing rail)"


# -- admissibility at current ATR (#443 slice 2) ------------------------------------------------


@dataclass(frozen=True)
class AdmissibilityRow:
    """One allowlisted product's market reality: last close and ATR(14).

    `atr` is None when there is too little candle data to compute one -- the verdict
    for that product is `no_data`, not a number guessed from nothing.
    """

    product_id: str
    price: Decimal
    atr: Decimal | None


#: The two stop conventions the shipped rule families size off: rsi_meanrev uses 1.5x
#: ATR, turtle_breakout 2x. A wider stop (2x) risks less per unit, so it sizes FEWER
#: units -- that end is the band's low edge, 1.5x the high edge.
_STOP_ATR_MULTS = (Decimal("1.5"), Decimal("2"))

#: Below this many candles an ATR(14) is noise seeded from a too-short window.
_MIN_ATR_CANDLES = 30


def _money(value: Decimal) -> str:
    return f"${value.quantize(Decimal('0.01'), ROUND_HALF_UP)}"


def admissibility_findings(
    rows: list[AdmissibilityRow],
    equity: Decimal,
    risk_pct: Decimal,
    max_per_order_usd: Decimal,
) -> list[Finding]:
    """Config vs reality: does `risk_pct` sizing fit the profile's own per-order cap at
    CURRENT ATR?

    The motivating failure this catches: a profile whose `risk_pct` and `caps` never
    disagreed on paper, but whose products' volatility grew until every sized order
    exceeded the cap and the rail vetoed everything. The check recomputes what the
    executor would size (`sizing.size` at the live path's equity stand-in) across the
    two stop conventions the shipped rules use, and compares the resulting notional
    band to `caps.max_per_order_usd`.
    """
    convention = (
        f"convention: equity = caps.max_exposure_usd ({_money(equity)}, the live-path "
        "convention); ATR(14) on the finest configured granularity; stop band 1.5-2x ATR "
        "(the 2x stop sizes smaller, 1.5x larger)"
    )

    bands: list[tuple[AdmissibilityRow, Decimal, Decimal]] = []
    for row in rows:
        if row.atr is None or row.atr <= 0:
            continue
        notionals = [
            sizing.spend(
                sizing.size(equity, risk_pct, row.price, row.price - mult * row.atr),
                row.price,
            )
            for mult in _STOP_ATR_MULTS
        ]
        bands.append((row, min(notionals), max(notionals)))

    no_data = [row for row in rows if row.atr is None or row.atr <= 0]
    fits: list[tuple[AdmissibilityRow, Decimal, Decimal]] = []
    cannot_fit: list[tuple[AdmissibilityRow, Decimal, Decimal]] = []
    marginal: list[tuple[AdmissibilityRow, Decimal, Decimal]] = []
    for band in bands:
        _, low, high = band
        if high <= max_per_order_usd:
            fits.append(band)
        elif low > max_per_order_usd:
            cannot_fit.append(band)
        else:
            marginal.append(band)

    if not bands:
        return [
            Finding(
                "sizing.admissible",
                WARN,
                "no ATR data for any allowlisted product",
                f"{convention}; fetch candles first, then re-run doctor",
                "keel fetch",
            )
        ]

    if cannot_fit and not fits and not marginal:
        over = "; ".join(
            f"{row.product_id}: {_money(low)}-{_money(high)}" for row, low, high in cannot_fit
        )
        return [
            Finding(
                "sizing.admissible",
                FAIL,
                "risk_pct sizing cannot fit max_per_order_usd on any allowlisted product",
                f"every band exceeds the {_money(max_per_order_usd)} cap -- {over}. {convention}",
                "lower risk_pct or raise caps.max_per_order_usd in the profile",
            )
        ]

    if cannot_fit or marginal or no_data:
        flagged = [
            f"{row.product_id}: {_money(low)}-{_money(high)} exceeds cap "
            f"{_money(max_per_order_usd)}"
            for row, low, high in cannot_fit
        ]
        flagged += [
            f"{row.product_id}: {_money(low)}-{_money(high)} straddles cap "
            f"{_money(max_per_order_usd)}"
            for row, low, high in marginal
        ]
        flagged += [f"{row.product_id}: no ATR data" for row in no_data]
        fixes = []
        if no_data:
            fixes.append("keel fetch")
        if cannot_fit or marginal:
            fixes.append("lower risk_pct or raise caps.max_per_order_usd in the profile")
        return [
            Finding(
                "sizing.admissible",
                WARN,
                f"{len(flagged)} of {len(rows)} allowlisted products do not clearly fit "
                f"max_per_order_usd at current ATR",
                f"{'; '.join(flagged)}. {convention}",
                "; ".join(fixes),
            )
        ]

    detail = "; ".join(f"{row.product_id}: {_money(low)}-{_money(high)}" for row, low, high in fits)
    return [
        Finding(
            "sizing.admissible",
            OK,
            f"risk_pct sizing fits max_per_order_usd ({_money(max_per_order_usd)}) on "
            f"{len(rows)}/{len(rows)} allowlisted products at current ATR",
            f"{detail}. {convention}",
            "-",
        )
    ]


# -- per-product data health (#443 slice 2) -----------------------------------------------------


@dataclass(frozen=True)
class SeriesHealth:
    """One `(product, granularity)` series' cached-data health, plus the gap count the
    freshness sweep could not explain (`unexplained_gaps`: holes the venue never
    accounted for, repairable only by `keel fetch --repair-gaps`)."""

    product: str
    granularity: str
    freshness: Freshness
    unexplained_gaps: int


def _product_ids(rows: list[SeriesHealth]) -> tuple[str, ...]:
    """Sorted, de-duplicated product ids involved in one finding -- one product can carry
    several flagged granularities, and a wrapper naming what is gated wants the product
    named once, not once per granularity."""
    return tuple(sorted({row.product for row in rows}))


def data_health_findings(series: list[SeriesHealth]) -> list[Finding]:
    """Staleness, cold caches and gaps per allowlisted series, at doctor's 7-day window.

    `market_closed` (FR-9) defuses staleness the same way `keel fetch` does: a closed
    session-bound venue is not minting bars, so a behind series on a Saturday is the
    expected state, not a fault. `missing` is never defused -- a closed venue still
    serves history, so a cold cache is the pipeline's problem whatever the calendar says.
    """
    findings: list[Finding] = []

    missing_rows = [s for s in series if s.freshness.missing]
    if missing_rows:
        pairs = ", ".join(f"{s.product} {s.granularity}" for s in missing_rows)
        findings.append(
            Finding(
                "data.missing",
                FAIL,
                f"{len(missing_rows)} series have nothing cached",
                f"cold cache: {pairs}",
                "keel fetch",
                products=_product_ids(missing_rows),
            )
        )
    else:
        findings.append(
            Finding(
                "data.missing",
                OK,
                f"all {len(series)} series have candles",
                f"{len(series)} (product, granularity) series carry cached candles",
                "-",
            )
        )

    judged = [s for s in series if not s.freshness.missing]
    stale_open = [s for s in judged if s.freshness.stale and not s.freshness.market_closed]
    defused = [s for s in judged if s.freshness.stale and s.freshness.market_closed]
    if not series:
        findings.append(Finding("data.stale", OK, "no series configured", "-", "-"))
    elif stale_open and len(stale_open) == len(judged):
        findings.append(
            Finding(
                "data.stale",
                FAIL,
                "the feed looks dead: every series is stale",
                f"all {len(judged)} judged series are behind beyond the fetch tolerance",
                "keel fetch",
                products=_product_ids(stale_open),
            )
        )
    elif stale_open:
        behind = ", ".join(
            f"{s.product} {s.granularity} {s.freshness.bars_behind} bars behind" for s in stale_open
        )
        findings.append(
            Finding(
                "data.stale",
                WARN,
                f"{len(stale_open)} of {len(judged)} series are stale",
                behind,
                "keel fetch",
                products=_product_ids(stale_open),
            )
        )
    else:
        if judged:
            verb = "is" if len(judged) == 1 else "are"
            detail = f"{len(judged)} judged series {verb} current within the fetch tolerance"
        else:
            detail = "0 series to judge -- every series is missing"
        if defused:
            detail += f"; market closed -- staleness defused on {len(defused)} series (FR-9)"
        findings.append(Finding("data.stale", OK, "series current", detail, "-"))

    gappy = [s for s in series if s.unexplained_gaps > 0]
    if gappy:
        counts = ", ".join(f"{s.product} {s.granularity}: {s.unexplained_gaps}" for s in gappy)
        findings.append(
            Finding(
                "data.gaps",
                WARN,
                f"{len(gappy)} series have unexplained gaps",
                counts,
                "keel fetch --repair-gaps",
                products=_product_ids(gappy),
            )
        )
    else:
        findings.append(
            Finding(
                "data.gaps",
                OK,
                "no unexplained gaps",
                "no unexplained gaps in the last 7 days",
                "-",
            )
        )

    return findings


def partial_fill_findings(orders: list[dict[str, Any]]) -> list[Finding]:
    """Partially-filled live orders (#446) -- the condition the brackets are sized wrong for.

    A row whose venue-observed `filled_quantity` is below its ordered `qty` means either a
    resting order the venue is partway through (`partially_filled`, still working, expected to
    resolve on its own) or a terminal order that filled short -- the one that matters, because
    everything sized from the order assumed it all filled and the exit bracket can be oversized
    for what is actually held. WARN, not FAIL: a partial is a real state a human should size the
    bracket to, not a fault in the deployment -- and the resting kind usually resolves itself.

    Rows with no `filled_quantity` (everything written before #446) are not judged: NULL means
    "not observed", and guessing a partial from `status` alone would flag resting orders the
    venue never began executing.
    """
    partials = [
        o
        for o in orders
        if o.get("mode") == "live"
        and o.get("filled_quantity") is not None
        and o.get("qty") is not None
        and Decimal(str(o["filled_quantity"])) < Decimal(str(o["qty"]))
    ]
    if not partials:
        return [
            Finding(
                "fill.partial",
                OK,
                "no partially-filled orders",
                "every observed fill matches its ordered size",
                "-",
            )
        ]
    described = ", ".join(
        f"{o.get('product_id')} order {o.get('id')}: {o.get('filled_quantity')} of "
        f"{o.get('qty')} filled"
        for o in partials
    )
    return [
        Finding(
            "fill.partial",
            WARN,
            f"{len(partials)} order(s) partially filled",
            f"{described} -- a bracket sized from the ORDERED quantity may be oversized for "
            "what is held",
            "cancel & re-place the bracket at the filled size (automated resize: #502)",
        )
    ]


def doctor_exit_code(findings: list[Finding]) -> int:
    """Faults fail the run; deliberate halts and warnings do not."""
    return 1 if any(f.status == FAIL for f in findings) else 0


_STATUS_MARK = {OK: "[ok]", WARN: "[warn]", FAIL: "[FAIL]", HALTED: "[halted]"}


def doctor_lines(findings: list[Finding]) -> list[str]:
    lines = ["keel doctor -- is this deployment actually working?", ""]
    for f in findings:
        lines.append(f"{_STATUS_MARK.get(f.status, '[?]')} {f.name}: {f.headline}")
        lines.append(f"       {f.detail}")
        if f.fix != "-":
            lines.append(f"       fix: {f.fix}")
    counts = {s: sum(1 for f in findings if f.status == s) for s in (OK, WARN, FAIL, HALTED)}
    lines.append("")
    lines.append(
        f"{counts[OK]} ok, {counts[WARN]} warn, {counts[FAIL]} fail, {counts[HALTED]} halted "
        "(halted = deliberate, not broken)"
    )
    return lines


def render_json(findings: list[Finding]) -> str:
    return json.dumps(
        [
            {
                "name": f.name,
                "status": f.status,
                "headline": f.headline,
                "detail": f.detail,
                "fix": f.fix,
                "products": list(f.products),
            }
            for f in findings
        ],
        indent=2,
    )


def _read_log_lines(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:  # noqa: SIM115
            return handle.readlines()
    except OSError:
        return []


def _admissibility_rows(
    repo: Any,
    products: list[str],
    granularities: list[Granularity],
    now_ts: int,
) -> list[AdmissibilityRow]:
    """Last close + ATR(14) per product, on the finest configured granularity (the series
    the agent's entry gate itself polls). Too few candles for a meaningful ATR becomes
    `atr=None` -- a `no_data` verdict, never a number invented to fill the row."""
    from keel.analysis.indicators import atr as atr_indicator
    from keel.data.history import GRANULARITY_SECONDS

    if not granularities:
        return []
    finest = min(granularities, key=lambda g: GRANULARITY_SECONDS[g])
    step = GRANULARITY_SECONDS[finest]
    rows: list[AdmissibilityRow] = []
    for product in products:
        candles = repo.get_candles(product, finest, start_ts=now_ts - 200 * step)
        if len(candles) < _MIN_ATR_CANDLES:
            rows.append(AdmissibilityRow(product_id=product, price=Decimal("0"), atr=None))
            continue
        atr_val = atr_indicator(candles, period=14)[-1]
        rows.append(
            AdmissibilityRow(product_id=product, price=candles[-1].close, atr=Decimal(str(atr_val)))
        )
    return rows


def gather_findings(repo: Any, config: Any, log_lines: Iterable[str], now_ts: int) -> list[Finding]:
    """Every doctor check, over an ALREADY-OPEN repo and an ALREADY-LOADED config.

    This is the seam `keel mcp`'s doctor tool shares with the click command (#477): one
    gather, two front-ends, so a research assistant and an operator at a terminal cannot be
    shown two different accounts of the same deployment. Repo reads only -- pinned by a test
    that counts `sqlite3`'s own change counter around a call, because "the tool is read-only"
    is a property of the gather, not of whoever happens to call it this time.

    The caller owns opening: the command migrates on the way in, the MCP tool deliberately
    does not (the `keel/web/server.py` rule -- a view must not take a schema write lock).
    `log_lines` are the engine log's own lines, read by the caller so each front-end can
    point at the file it was wired with.
    """
    from keel import agent
    from keel.commands import fetch
    from keel.commands._products import _default_sim_products
    from keel.execution import guards

    findings: list[Finding] = []

    info = build_info()
    report = check_install(source=info.source)
    versions_aligned = getattr(report, "consistent", True)
    identity = getattr(report, "identity", "")
    if versions_aligned and "DIRTY" not in str(identity) and "[checkout]" not in str(identity):
        findings.append(Finding("install.identity", OK, "build identity clean", str(identity), "-"))
    else:
        findings.append(
            Finding(
                "install.identity",
                FAIL,
                "install is skewed or not a release build",
                f"{identity}; a skewed install can run older siblings silently",
                "reinstall keel_trader BY PATH from the release; verify with `keel versions`",
            )
        )

    venue = current_venue() or guards.DEFAULT_VENUE
    subscription = repo.get_broker_subscription(venue)
    findings += attestation_findings(
        subscription=subscription,
        withdrawals_attested_at=int(repo.get_state("withdrawals_attested_at", default=0) or 0),
        now_ts=now_ts,
    )
    findings += trade_scope_findings(repo.get_venue_trade_scope(venue), venue)
    findings += rail_state_findings(
        kill_switch=bool(repo.get_state("kill_switch", default=False)),
        streak_halt_until=int(repo.get_state("streak_halt_until", default=0) or 0),
        drawdown_total=Decimal(str(repo.get_state("drawdown_total_pct", default=0) or 0)),
        now_ts=now_ts,
    )
    findings += allowance_findings(
        month_to_date_spend=guards._monthly_buy_spend_usd(repo, now_ts),
        allowance=(
            subscription.allowance_usd(now_ts, Decimal("0")) if subscription is not None else None
        ),
        mean_buy_notional=None,
    )
    findings += veto_findings(log_lines, since_ts=now_ts - 7 * 86_400)
    # A repo read, like every other check -- pinned read-only by the same change-counter test.
    findings += partial_fill_findings(repo.get_orders(mode="live"))

    from keel.data import freshness as freshness_mod

    products = _default_sim_products(config)
    granularities = list(config.market_data.granularities)
    market_closed = agent.recorded_market_closed(repo, config, now_ts)
    start_ts = now_ts - DOCTOR_WINDOW_SEC

    findings += data_health_findings(
        [
            SeriesHealth(
                product=row.product,
                granularity=row.granularity.value,
                freshness=row,
                unexplained_gaps=unexplained,
            )
            for row, unexplained in fetch.assess_products(
                repo,
                products,
                granularities,
                now_ts,
                start_ts,
                freshness_mod.DEFAULT_TOLERANCE_BARS,
                market_closed,
            )
        ]
    )
    findings += admissibility_findings(
        rows=_admissibility_rows(repo, products, granularities, now_ts),
        equity=config.caps.max_exposure_usd,
        risk_pct=config.risk_pct,
        max_per_order_usd=config.caps.max_per_order_usd,
    )

    return findings


# -- profile health: is every configured profile actually running? (#640, #642) -----------------
#
# Deliberately kept OUT of `gather_findings` above, appended here as a self-contained block
# `doctor_cmd` alone calls. `gather_findings` is the seam `keel mcp`'s doctor tool shares with
# this command (#477), pinned READ-ONLY by a change-counter test on the ONE repo it is handed --
# opening sibling deployment databases and shelling `launchctl` would change what a read-only
# MCP view of THIS repo does, and that is exactly the kind of scope creep that seam's test exists
# to catch. This is CLI-side, deployment-wide reporting; `gather_findings` stays single-repo.

#: A stalled profile is judged against a MULTIPLE of its own cadence, not a flat number of
#: seconds -- see `profile_findings`' docstring for why a flat threshold cannot work for both
#: an hourly and a daily profile at once.
STALL_WARN_INTERVALS = 2
STALL_FAIL_INTERVALS = 3

_STATUS_RANK = {OK: 0, WARN: 1, FAIL: 2}


@dataclass(frozen=True)
class ProfileHealth:
    """One launchd-scheduled profile's observed state, as `collect_profiles` assembles it from
    the plist + runner script + config + sibling database it wires together -- everything
    `profile_findings` needs to judge whether the profile is actually running."""

    label: str  #: launchd Label, e.g. "com.keel.paper-hourly"
    runner: str  #: basename of the runner script the plist executes
    db_file: str  #: the db that runner drives
    scheduled: bool | None  #: True loaded, False not loaded, None launchd unreadable
    last_cycle_ts: int | None  #: state['last_feed_ts'], None if never cycled / db unreadable
    interval_sec: int  #: the profile's own cadence (config.auto_trade.interval_sec)


def _human_duration(seconds: float) -> str:
    """`10d 4h`, `1h`, `45m` -- coarse enough to read at a glance. Used for both an AGE
    ("last cycle 10d 4h ago") and a CADENCE ("cadence 1h"), so a reader can compare the two at
    the same granularity without doing the arithmetic themselves."""
    total = max(int(seconds), 0)
    days, rem = divmod(total, 86_400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    if minutes:
        return f"{minutes}m"
    return f"{total}s"


def profile_findings(profiles: list[ProfileHealth], now_ts: int) -> list[Finding]:
    """Is every deployment profile launchd knows about actually cycling?

    This is #640 made structural. `com.keel.paper-hourly.plist` sat correctly written in the
    repo and in `~/keel` and was simply never `launchctl bootstrap`ped into
    `~/Library/LaunchAgents`; the real deployment's hourly profile last cycled 2026-08-21
    00:20:05Z against an `interval_sec` of 3600 -- ten days of silence is roughly 240 missed
    hourly cycles -- and nothing in `keel status` or the old `doctor` said so, because both only
    ever looked at the ONE db this process happened to be pointed at, never at the sibling
    profiles a deployment is supposed to be running. `profile_findings` is the check that closes
    that hole: given every plist -> runner -> config -> db a deployment declares
    (`collect_profiles` assembles the list), it asks two independent questions.

    `profile.scheduled` -- does launchd actually have a job for this profile? FAIL when any
    profile is confirmed NOT loaded (`scheduled is False`); WARN when launchd itself could not be
    asked (`scheduled is None`) and nothing is confirmed missing -- "cannot tell" must never
    read as "fine", which is exactly the gap a silently-broken `launchctl` call would otherwise
    hide behind. OK only when every profile is confirmed loaded.

    `profile.cycled` -- is each profile's `last_feed_ts` recent relative to ITS OWN cadence? The
    threshold is a MULTIPLE of `interval_sec`, not a flat number of seconds, because "how stale
    is too stale" only means something relative to how often a profile is supposed to cycle. A
    daily profile (interval_sec=86400) that is 10 days stale and an hourly profile
    (interval_sec=3600) that is 10 days stale are both clearly broken -- but a flat threshold
    tight enough to catch the daily one at, say, 2 hours would false-positive on every ordinary
    hourly cycle, and a flat threshold loose enough to tolerate the hourly profile's normal
    jitter would let the daily profile go silent for weeks unnoticed. Scaling by the profile's
    own `interval_sec` is the only way one pair of constants (`STALL_WARN_INTERVALS`,
    `STALL_FAIL_INTERVALS`) works for every cadence at once: 90 minutes stale is a FAIL on an
    hourly profile (it has missed its window more than twice over) and unremarkable on a daily
    one (it has not even missed its first cycle yet) -- that contrast is the whole point.
    """
    if not profiles:
        return [
            Finding("profile.scheduled", OK, "no profiles configured", "-", "-"),
            Finding("profile.cycled", OK, "no profiles configured", "-", "-"),
        ]

    findings: list[Finding] = []

    unscheduled = [p for p in profiles if p.scheduled is False]
    unknown = [p for p in profiles if p.scheduled is None]
    if unscheduled:
        commands = [
            f"launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/{p.label}.plist"
            for p in unscheduled
        ]
        findings.append(
            Finding(
                "profile.scheduled",
                FAIL,
                f"{len(unscheduled)} of {len(profiles)} profile(s) not loaded into launchd",
                "launchd has no job for: " + ", ".join(p.label for p in unscheduled),
                "; ".join(commands),
                products=(),
            )
        )
    elif unknown:
        findings.append(
            Finding(
                "profile.scheduled",
                WARN,
                f"cannot tell whether {len(unknown)} of {len(profiles)} profile(s) are scheduled",
                "launchctl is unreadable for: " + ", ".join(p.label for p in unknown),
                "check `launchctl list` by hand",
                products=(),
            )
        )
    else:
        findings.append(
            Finding(
                "profile.scheduled",
                OK,
                f"all {len(profiles)} profile(s) loaded into launchd",
                ", ".join(p.label for p in profiles),
                "-",
                products=(),
            )
        )

    worst = OK
    bad_lines: list[str] = []
    bad_labels: list[str] = []
    for p in profiles:
        if p.last_cycle_ts is None:
            status = FAIL
            bad_lines.append(f"{p.label}: has never cycled")
        else:
            age = now_ts - p.last_cycle_ts
            if age > STALL_FAIL_INTERVALS * p.interval_sec:
                status = FAIL
            elif age > STALL_WARN_INTERVALS * p.interval_sec:
                status = WARN
            else:
                status = OK
            if status != OK:
                bad_lines.append(
                    f"{p.label}: last cycle {_human_duration(age)} ago, "
                    f"cadence {_human_duration(p.interval_sec)}"
                )
        if status != OK:
            bad_labels.append(p.label)
        if _STATUS_RANK[status] > _STATUS_RANK[worst]:
            worst = status

    if worst == OK:
        findings.append(
            Finding(
                "profile.cycled",
                OK,
                f"all {len(profiles)} profile(s) cycling within their own cadence",
                ", ".join(p.label for p in profiles),
                "-",
                products=(),
            )
        )
    else:
        fix = "; ".join(
            f"check the {p.runner} runner and `launchctl list {p.label}`"
            for p in profiles
            if p.label in bad_labels
        )
        findings.append(
            Finding(
                "profile.cycled",
                worst,
                f"{len(bad_lines)} of {len(profiles)} profile(s) stalled or never cycled",
                "; ".join(bad_lines),
                fix,
                products=(),
            )
        )

    return findings


def _loaded_launchd_labels() -> frozenset[str] | None:
    """The set of Labels `launchctl list` currently reports, or `None` when the question could
    not be asked at all (non-macOS, no `launchctl` binary, a timeout, a nonzero exit, unparseable
    output). `None` and an empty set mean different things -- `profile_findings` reads `None` as
    "cannot tell" (WARN) and a label's ABSENCE from a non-None set as "confirmed not loaded"
    (FAIL) -- so a failure to ask must never be reported as if the answer were "nothing is
    loaded"; that would be a false positive doctor cannot afford.

    `launchctl list`'s output is three whitespace-separated columns, `PID  Status  Label`, with a
    header row of the same shape -- parsed positionally (column index 2) rather than by name
    matching, since the header text itself is not a stable contract to depend on.
    """
    import platform
    import subprocess

    if platform.system() != "Darwin":
        return None
    try:
        result = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    labels: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        if parts[0] == "PID" and parts[1] == "Status":
            continue
        labels.add(parts[2])
    return frozenset(labels)


def collect_profiles(
    deployment_dir: Path, loaded_labels: frozenset[str] | None, now_ts: int
) -> list[ProfileHealth]:
    """Every `com.keel.*.plist` in `deployment_dir`, turned into the `ProfileHealth` rows
    `profile_findings` judges.

    Follows `_profiles()`'s precedent in `keel/mcp/tools.py` (enumerate the profiles of a
    deployment by globbing its directory): one bad profile must never hide the rest, so any
    error resolving ONE plist's identity -- unparseable plist, missing `Label`/
    `ProgramArguments`, no `.sh` argument, unreadable runner script, no `--config` in it, or a
    config that fails to load -- skips that profile silently rather than raising. A missing or
    unreadable DATABASE is different: it is exactly the "never cycled" state doctor exists to
    report, so it becomes `last_cycle_ts=None` on an otherwise-valid profile rather than a skip.

    The plist's `ProgramArguments` carries the OPERATOR's absolute path to the runner script
    (whatever machine it was authored on); only the basename is trusted, re-resolved inside
    `deployment_dir`, which is this call's own source of truth for where the deployment lives.

    The sibling database is opened read-only and NEVER migrated -- `Repository` performs no
    schema writes on construction, and `PRAGMA query_only = ON` makes SQLite itself refuse any
    write on the connection, so reading one profile's `last_feed_ts` can never take the schema
    write lock `keel/web/server.py`'s read surfaces are forbidden from taking on a live db.

    THE RUNNER'S REAL INVOCATION -- not any text that happens to appear in the script -- is the
    only source of truth for a profile's config/db. Two things make a naive
    `re.search(r"--config (\\S+)", runner_text)` wrong: the wrappers invoke `keel` as
    `"$KEEL" --config "$CONFIG" --db "$DB" ...` (a shell-variable reference, never a literal
    filename), and a comment earlier in the script -- a worked example, a "check which one is
    live" note -- can carry its own unrelated `--config`/`--db` text that `re.search` would
    match FIRST, before ever reaching the real invocation. `keel-live-run.sh` hit both: its
    line 15 comment happened to name the right files in the right order, so the old regex
    "worked" by coincidence, and would have silently broken had that comment been reworded,
    reordered, or deleted. Because a config that fails to load is swallowed by the broad
    `except Exception: continue` below, that break would not raise -- it would just make the
    live, real-money profile vanish from `profile.scheduled`/`profile.cycled` with no error
    at all, which is worse than a loud failure and is exactly the silent-blind-spot failure
    mode this whole check exists to catch. So the match here runs only over comment-stripped
    text (any line whose first non-whitespace character is `#` is dropped first), and a
    captured token that is a shell-variable reference (`$NAME`, `${NAME}`, or either quoted)
    is traced back to that variable's own assignment elsewhere in the comment-stripped script;
    a variable with no matching assignment resolves to `None` and the whole profile is skipped,
    the same as today's "no `--config` at all" case, rather than being fed a raw shell
    expression `load_config` cannot open.
    """
    import plistlib
    import re

    from keel.config import load_config

    def _strip_comments(text: str) -> str:
        """Drop every line whose first non-whitespace character is `#`. A comment must never
        compete with the script's real invocation for a regex match -- see the docstring
        above for the incident this prevents."""
        return "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )

    def _resolve_token(token: str, code_text: str) -> str | None:
        """Resolve one captured `--config`/`--db` argument to a plain filename.

        A literal token (no wrapper actually ships this way, but nothing forbids it) passes
        through unchanged. A shell-variable reference -- `$NAME`, `${NAME}`, or either form
        quoted, which is what every tracked wrapper actually uses (`"$CONFIG"`, `"$DB"`) -- is
        traced back to that variable's own assignment (`NAME="value"` / `NAME=value`, on its
        own line) in `code_text`. No matching assignment means the invocation cannot be
        resolved, so this returns `None` and the caller skips the profile rather than handing
        `load_config` a shell expression it will raise on.
        """
        value = token.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        var_match = re.fullmatch(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", value)
        if var_match is None:
            return value
        name = var_match.group(1)
        assign_match = re.search(
            rf'^\s*{re.escape(name)}=(?:"([^"]*)"|\'([^\']*)\'|(\S+))\s*$',
            code_text,
            re.MULTILINE,
        )
        if assign_match is None:
            return None
        return next(g for g in assign_match.groups() if g is not None)

    profiles: list[ProfileHealth] = []
    for plist_path in sorted(deployment_dir.glob("com.keel.*.plist")):
        try:
            plist = plistlib.loads(plist_path.read_bytes())
            label = str(plist["Label"])
            args = [str(a) for a in plist["ProgramArguments"]]
            runner_arg = next(a for a in reversed(args) if a.endswith(".sh"))
            runner = Path(runner_arg).name
            runner_text = (deployment_dir / runner).read_text()
            code_text = _strip_comments(runner_text)
            config_match = re.search(r"--config (\S+)", code_text)
            if config_match is None:
                continue
            config_file = _resolve_token(config_match.group(1), code_text)
            if config_file is None:
                continue
            db_match = re.search(r"--db (\S+)", code_text)
            if db_match is None:
                db_file = "keel.db"
            else:
                resolved_db = _resolve_token(db_match.group(1), code_text)
                if resolved_db is None:
                    continue
                db_file = resolved_db
            config = load_config(deployment_dir / config_file)
            interval_sec = int(config.auto_trade.interval_sec)
        except Exception:  # one unreadable profile must not hide the rest
            continue

        last_cycle_ts: int | None = None
        db_path = deployment_dir / db_file
        if db_path.exists():
            try:
                from keel.data.db import connect
                from keel.data.repository import Repository

                conn = connect(str(db_path))
                try:
                    conn.execute("PRAGMA query_only = ON")
                    raw = Repository(conn).get_state("last_feed_ts", default=None)
                    last_cycle_ts = int(raw) if raw is not None else None
                finally:
                    conn.close()
            except Exception:
                last_cycle_ts = None

        scheduled = None if loaded_labels is None else (label in loaded_labels)
        profiles.append(
            ProfileHealth(
                label=label,
                runner=runner,
                db_file=db_file,
                scheduled=scheduled,
                last_cycle_ts=last_cycle_ts,
                interval_sec=interval_sec,
            )
        )
    return profiles


@click.command("doctor")
@click.option("--json", "as_json", is_flag=True, help="emit findings as JSON")
@click.option("--log", "log_path", default="logs/keel.log", show_default=True)
@click.pass_context
def doctor_cmd(ctx: click.Context, as_json: bool, log_path: str) -> None:
    """One command that answers 'is this deployment actually working' (#443)."""
    from keel.commands._common import _load_cfg, _open_repo

    try:
        config = _load_cfg(ctx)
        repo = _open_repo(ctx)
    except click.ClickException:
        startup: list[Finding] = [
            Finding(
                "install.identity",
                FAIL,
                "cannot load config/database",
                "doctor needs the deployment's config and repo before any check can run",
                "keel init  (fresh) or check the --config/--db path",
            )
        ]
        _emit(startup, as_json)
        raise SystemExit(1)

    now_ts = int(__import__("time").time())
    findings = gather_findings(repo, config, _read_log_lines(log_path), now_ts)
    # Deliberately NOT folded into `gather_findings`: that seam is shared with `keel mcp` and
    # pinned read-only by a change-counter test on ONE repo (see the comment above
    # `profile_findings`). Opening sibling databases and shelling `launchctl` belongs only here,
    # on the CLI side.
    findings += profile_findings(
        collect_profiles(Path(ctx.obj["config_path"]).parent, _loaded_launchd_labels(), now_ts),
        now_ts,
    )

    _emit(findings, as_json)
    raise SystemExit(doctor_exit_code(findings))


def _emit(findings: list[Finding], as_json: bool) -> None:
    if as_json:
        click.echo(render_json(findings))
    else:
        for line in doctor_lines(findings):
            click.echo(line)
