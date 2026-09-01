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
    is needed); `detail` carries the numbers (days remaining, dollars, counts)."""

    name: str
    status: str
    headline: str
    detail: str
    fix: str


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



def unbooked_exit_findings(
    open_positions: list[dict[str, Any]], orders: list[dict[str, Any]]
) -> list[Finding]:
    """Tranches still OPEN behind an exit that already filled (#639) -- the state that made the
    promotion ladder unreachable and said nothing.

    Six paper round trips completed in the live deployment between 2026-08-21 and 08-28, every
    exit `status='filled'` with an `actual_fill`, and `trade_outcomes` held ZERO rows while all
    eight `positions` sat `status='open'`. `promotion.py`'s pooled sample-size axis (#338)
    counts that table, so pooled n was pinned at 0 and no rule could ever be promoted. Nothing
    surfaced it: it was found by asking how long promotion would take, which is not a diagnostic
    channel. This is the channel.

    The condition is stated over the two tables that must agree, not over the bug that broke
    them, so it still holds if a future path forgets the same edge: an OPEN tranche whose
    product has a FILLED SELL dated at or after the tranche opened has been sold and is not
    booked. Every clause earns its place --

    * `realized_qty > 0` is EXCLUDED. A deliberate scale-out (`executor.scale_out`) and a short
      market exit (#446) both leave a tranche legitimately open behind a filled SELL, and both
      record the leg on the tranche. A finding that flagged them would fire on correct behaviour
      every time a position was de-risked, which is how a check gets ignored.
    * The SELL must be at or after `opened_at`. A sale that closed an EARLIER tranche says
      nothing about one opened after it, and the ledger is FIFO so the ordering is meaningful.

    Modes are pooled on purpose: paper is where this was found, but the invariant is not
    paper's -- `agent._open_tranche` writes the ledger for both, and an unbooked LIVE exit is
    strictly worse.

    WARN, not FAIL: the deployment is trading correctly and the money is real either way; what
    is lost is the EVIDENCE, and that is a state a human resolves by deciding whether to
    backfill, not a fault that should stop a cycle.
    """
    sold_at_by_product: dict[str, list[int]] = {}
    for order in orders:
        if order.get("side") != "SELL" or order.get("status") != "filled":
            continue
        ts = order.get("created_at")
        if ts is None:
            continue
        sold_at_by_product.setdefault(str(order.get("product_id")), []).append(int(ts))

    stranded = [
        p
        for p in open_positions
        if Decimal(str(p.get("realized_qty") or 0)) == 0
        and any(
            ts >= int(p["opened_at"]) for ts in sold_at_by_product.get(str(p["product_id"]), [])
        )
    ]
    if not stranded:
        return [
            Finding(
                "ledger.unbooked_exit",
                OK,
                "no unbooked exits",
                "every filled exit closed the tranche it sold",
                "-",
            )
        ]
    described = ", ".join(
        f"{p['product_id']} tranche {p['id']} ({p.get('rule_name')})" for p in stranded
    )
    return [
        Finding(
            "ledger.unbooked_exit",
            WARN,
            f"{len(stranded)} tranche(s) open behind a filled exit",
            f"{described} -- the sale filled but no `trade_outcomes` row was written, so the "
            "promotion gate's pooled sample size (#338) does not count these trades",
            "inspect the orders behind each tranche and decide whether to backfill (#639)",
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
    # #639: modes are POOLED here, unlike the partial-fill sweep above -- the ledger
    # invariant belongs to `agent._open_tranche`, which writes it for paper and live alike.
    findings += unbooked_exit_findings(repo.get_open_positions(), repo.get_orders())

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

    _emit(findings, as_json)
    raise SystemExit(doctor_exit_code(findings))


def _emit(findings: list[Finding], as_json: bool) -> None:
    if as_json:
        click.echo(render_json(findings))
    else:
        for line in doctor_lines(findings):
            click.echo(line)
