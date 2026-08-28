"""The 2026-09-30 pooled forward-trades review (#427, tracked in #353) — the pure seams.

#359 scheduled a pooled review of the FORWARD trades at a floor of n=100, and #427 found
that floor dishonest as written: the signals fire in herds (~8 assets the same UTC day,
ICC 0.212), so 100 pooled trades carry ~39 independent observations and the review can only
see an edge of 20 points or larger. The correction of record (PR #503, discussion #359's
corrected comment) reframed the review as DESCRIPTIVE — n_eff-corrected intervals via
`keel/research/significance.py`, the "at this n_eff ... can only see an edge of X points or
larger" sentence printed IN the report, and no pass/fail verdict on the edge. This module
is the machinery that makes that report impossible to write imprecisely:

* **The extraction mapping** reads the deployment's own records — the `trade_outcomes`
  ledger (authoritative; `pnl_net` realized and net of fees) plus, where the ledger is
  empty, round trips reconstructed from filled orders using the ledger writer's own pnl
  formula (`keel/execution/streak.py`). Win/loss is the SIGN of fee-honest net pnl; a
  `pnl_net` of exactly zero is a scratch and counts toward nothing (significance.py's
  contract); OPEN positions are excluded and counted, never guessed at. The ledger's
  `fees` column is the EXIT order's fee only — `streak.record_closed_trade` nets it plus
  the entry fee into `pnl_net` and stores the entry leg nowhere — so a deduped orders
  twin's both-legs fees ride into the ledger row that wins the dedup, and a pure ledger
  trip's fee is rendered as the labelled lower bound it is.
* **The power sentence** is generated at the SAME n the measurement uses
  (`stat.n_trades` — wins+losses; a scratch counts toward nothing) through
  `throughput.n_eff`/`throughput.detectable_edge`, so the artifact never carries two
  different n_eff numbers and a 7-trade pool says something very different from a
  100-trade one — the honest sentence scales with reality, and a report assembled by
  `render_report` cannot omit it.
* **The verdict vocabulary is about POWER, not the edge.** The significance machinery
  measures (win rate, break-even, edge, interval) but this report renders none of its
  pass/fail verdicts: the descriptive reframing means the only verdict-shaped statement is
  "at this n_eff the review can only see an edge of X points or larger".
* **A pool with nothing counted refuses.** Zero pooled trades — or a non-empty pool whose
  every trip is a scratch, which has no win rate and no n to put a power sentence at —
  says "nothing to review" and produces no degenerate report.

`Decimal` for every rate, price and pnl — they are money — exactly as `significance.py`
documents. The driver (`docs/experiments/2026-09-30-pooled-review.py`) owns all I/O: it
opens the deployment dbs read-only and writes the artifacts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TypedDict

from keel.research.significance import FamilySignificance, OutcomeRow, significance
from keel.research.throughput import design_effect, detectable_edge, n_eff

#: The scheduled review event (#353 tracks it; #359 set the date). A run before this date
#: labels itself a preview and states that the event re-runs; the event run does not.
EVENT_DATE = "2026-09-30"

#: The label the pooled measurement carries into `significance`: the forward trades' pnl is
#: already net of the fees those trades actually paid, whatever regime that was — the
#: realized fee fraction is measured and reported beside it, never assumed.
FAMILY = "pooled_forward"
FEE_REGIME = "fees_as_recorded"


class OrderRow(TypedDict):
    """One `orders` row as the read-only driver reads it (money already `Decimal`).

    The exact shape `Repository._order_row_to_dict` produces for the money fields. A
    `status` of anything but 'filled' never becomes a trade; `actual_fill`/`fee` are None
    exactly on rows that never executed.
    """

    id: int
    mode: str
    product_id: str
    side: str
    qty: Decimal
    status: str
    actual_fill: Decimal | None
    fee: Decimal | None
    rule_id: int | None
    created_at: int


class LedgerRow(TypedDict):
    """One `trade_outcomes` row (the closed-trade ledger rails 11 and 16 read)."""

    product_id: str
    rule_name: str
    opened_at: int
    closed_at: int
    qty: Decimal
    entry_fill: Decimal
    exit_fill: Decimal
    fees: Decimal
    pnl_net: Decimal


def round_trip_pnl(
    entry_fill: Decimal,
    exit_fill: Decimal,
    qty: Decimal,
    entry_fee: Decimal,
    exit_fee: Decimal,
) -> Decimal:
    """`(exit - entry) * qty - both legs' fees` — `streak.record_closed_trade`'s own formula.

    Reused verbatim for orders-derived round trips so a reconstructed trip and a ledger row
    of the same fills price identically: fee-honest, both legs charged, the sign of the
    result is the win/loss the review counts.
    """
    return (exit_fill - entry_fill) * qty - entry_fee - exit_fee


#: The dedup identity tuple (see :meth:`RoundTrip.key`).
TripKey = tuple[str, str, Decimal, Decimal, Decimal]


@dataclass(frozen=True)
class RoundTrip:
    """One closed forward round trip, from either record of it.

    `pnl_net` is carried, not derived: an orders-derived trip computes it with
    :func:`round_trip_pnl`; a ledger trip carries the RECORDED `pnl_net` verbatim (the row
    folds the entry fee into pnl but does not store it separately, so recomputing from the
    fills would silently drop it). `rule` is `rules.kind`, or 'unknown' when the rule row is
    gone — the fill is real regardless, so the trip counts and the composition says so.

    `fees_both_legs` is False only on a PURE ledger row: the ledger's `fees` column is the
    exit order's fee (the entry fee lives in `pnl_net` and nowhere else), so
    :meth:`fees_paid` on such a trip is a lower bound the report must label, never a
    per-leg pretension. A ledger row whose orders twin was deduped away carries the twin's
    both-legs fees and is marked True.
    """

    profile: str
    source: str
    mode: str
    product_id: str
    rule: str
    qty: Decimal
    entry_fill: Decimal
    exit_fill: Decimal
    entry_fee: Decimal
    exit_fee: Decimal
    opened_at: int
    closed_at: int
    pnl_net: Decimal
    fees_both_legs: bool = True

    def key(self) -> TripKey:
        """The dedup identity: (profile, product, quantity, entry fill, exit fill).

        The entry fill is load-bearing: two DISTINCT trips of the same size can share an
        exit fill (a herd day exits at one price), and keying without the entry would drop
        both when the ledger recorded only one of them.
        """
        return (self.profile, self.product_id, self.qty, self.entry_fill, self.exit_fill)

    def timestamps_inverted(self) -> bool:
        """True when the recorded close precedes the recorded open (id order still governs).

        The live paper ledgers contain such rows; order id is the database's own event
        sequencing, so the trip still counts — but the report must say the ledger's
        `created_at` disagrees rather than silently trust it.
        """
        return self.closed_at < self.opened_at

    def outcome(self) -> OutcomeRow:
        """The `OutcomeRow` significance.py reads: win/loss/scratch by the sign of net pnl.

        The r-multiple is None by contract — a forward trade has no pre-registered risk
        unit to be a multiple OF, and the math never reads it anyway.
        """
        if self.pnl_net > 0:
            return ("win", self.pnl_net, None)
        if self.pnl_net < 0:
            return ("loss", self.pnl_net, None)
        return ("scratch", self.pnl_net, None)

    def fees_paid(self) -> Decimal:
        """Both legs' fees when :attr:`fees_both_legs`; the exit leg alone otherwise.

        On a pure ledger row this is a LOWER bound: the ledger's `fees` column is the exit
        order's fee and the entry fee is folded into `pnl_net`, stored nowhere.
        """
        return self.entry_fee + self.exit_fee


@dataclass(frozen=True)
class OrdersRead:
    """One profile's `orders` table mapped: closed trips plus what was excluded, counted."""

    trips: list[RoundTrip]
    open_buys: int
    unfilled_orders: int
    stray_sells: int


def round_trips_from_orders(
    profile: str,
    orders: Sequence[OrderRow],
    rule_kinds: Mapping[int, str],
) -> OrdersRead:
    """Match filled BUYs to filled SELLs, FIFO by order id, into closed round trips.

    A BUY matches a SELL only within the SAME (profile, product, rule, mode, quantity) — a
    paper buy is never closed by a live sell, and a 2-lot buy never by a 1-lot sell.
    Ascending id is the ledger's own sequencing (the live `created_at` values demonstrably
    disagree with it; :meth:`RoundTrip.timestamps_inverted` flags that instead of trusting
    either clock blindly). What does not match is counted, not guessed at: a BUY with no
    SELL is an OPEN position (excluded from the pool), a SELL with no BUY is a stray (the
    entry is not on record, so no pnl can be formed honestly), and every non-filled status
    (rejected, pending) is an unfilled order.
    """
    pending: list[OrderRow] = []
    trips: list[RoundTrip] = []
    unfilled = 0
    strays = 0
    for row in sorted(orders, key=lambda r: r["id"]):
        if row["status"] != "filled":
            unfilled += 1
            continue
        if row["actual_fill"] is None or row["fee"] is None:
            # A 'filled' row without a fill/fee is a data error, named rather than priced.
            raise ValueError(
                f"orders row {row['id']} is filled but carries no actual_fill/fee -- "
                "a fill without a price cannot be priced into a round trip"
            )
        if row["side"] == "BUY":
            pending.append(row)
            continue
        key = (row["product_id"], row["rule_id"], row["mode"], row["qty"])
        match_index = next(
            (
                i
                for i, buy in enumerate(pending)
                if (buy["product_id"], buy["rule_id"], buy["mode"], buy["qty"]) == key
            ),
            None,
        )
        if match_index is None:
            strays += 1
            continue
        buy = pending.pop(match_index)
        rule_id = buy["rule_id"]
        trips.append(
            RoundTrip(
                profile=profile,
                source="orders",
                mode=row["mode"],
                product_id=row["product_id"],
                rule=rule_kinds.get(rule_id, "unknown") if rule_id is not None else "unknown",
                qty=buy["qty"],
                entry_fill=buy["actual_fill"] or Decimal("0"),
                exit_fill=row["actual_fill"],
                entry_fee=buy["fee"] or Decimal("0"),
                exit_fee=row["fee"],
                opened_at=buy["created_at"],
                closed_at=row["created_at"],
                pnl_net=round_trip_pnl(
                    buy["actual_fill"] or Decimal("0"),
                    row["actual_fill"],
                    buy["qty"],
                    buy["fee"] or Decimal("0"),
                    row["fee"],
                ),
            )
        )
    return OrdersRead(
        trips=trips,
        open_buys=len(pending),
        unfilled_orders=unfilled,
        stray_sells=strays,
    )


def ledger_round_trips(profile: str, rows: Sequence[LedgerRow]) -> list[RoundTrip]:
    """Map `trade_outcomes` rows to round trips, carrying the recorded pnl verbatim.

    The ledger's `fees` column is the EXIT order's fee only — `streak.record_closed_trade`
    nets it plus the tranche's entry fee into `pnl_net` (`(exit - entry) * qty - fees -
    entry_fee`) and stores the entry leg nowhere — so the row rides in `exit_fee` with a
    zero entry leg and `fees_both_legs=False`: `fees_paid()` on a pure ledger row is a
    lower bound the report labels as one, never a both-legs pretension.
    """
    return [
        RoundTrip(
            profile=profile,
            source="ledger",
            # the ledger records no live/paper mode; `source` says where the row came from
            mode="unknown",
            product_id=row["product_id"],
            rule=row["rule_name"],
            qty=row["qty"],
            entry_fill=row["entry_fill"],
            exit_fill=row["exit_fill"],
            entry_fee=Decimal("0"),
            exit_fee=row["fees"],
            opened_at=row["opened_at"],
            closed_at=row["closed_at"],
            pnl_net=row["pnl_net"],
            fees_both_legs=False,
        )
        for row in rows
    ]


@dataclass(frozen=True)
class ProfileSummary:
    """One profile's row in the composition table — what counted and what did not."""

    profile: str
    modes: tuple[str, ...]
    rules: tuple[str, ...]
    closed: int
    of_which_dca: int
    open_buys: int
    unfilled: int
    stray_sells: int
    ledger_rows: int
    deduped: int
    inversions: int


@dataclass(frozen=True)
class PooledSample:
    """The pre-registered pool: every counted closed round trip, oldest first.

    `trips` includes scratches (they are in the pool's composition); `significance()`
    applies the win/loss/scratch contract the same as it does for backtest trades. `deduped`
    counts orders-derived twins of ledger rows, dropped so a deployment that records BOTH
    never counts a trade twice.
    """

    profiles: tuple[str, ...]
    trips: list[RoundTrip]
    summaries: tuple[ProfileSummary, ...]
    excluded_open: int
    excluded_unfilled: int
    stray_sells: int
    deduped: int

    def n_pooled(self) -> int:
        return len(self.trips)

    def counted(self) -> int:
        """Trades the MEASUREMENT counts (wins+losses; a scratch counts toward nothing).

        This is the n `significance()` puts its standard error at and the n the power
        sentence is generated at — one basis per artifact, never two.
        """
        return self.n_pooled() - self.scratches()

    def outcomes(self) -> list[OutcomeRow]:
        """The `OutcomeRow` sequence `significance()` reads, oldest first."""
        return [trip.outcome() for trip in self.trips]

    def scratches(self) -> int:
        return sum(1 for o, _pnl, _r in self.outcomes() if o == "scratch")

    def significance(self) -> FamilySignificance:
        """The pooled measurement at the fees actually paid (see :data:`FEE_REGIME`)."""
        return significance(FAMILY, FEE_REGIME, realized_fee_pct(self.trips), self.outcomes())


def realized_fee_pct(trips: Sequence[RoundTrip]) -> Decimal:
    """The pool's realized fee fraction: fees paid over notional traded, both legs.

    The forward trades' regime is an OUTCOME, not an assumption — inside the fee-free
    allowance this is ~0, at the taker rate ~120 bp — so it is measured off the same round
    trips the win rate is, and reported beside it. Where a pure ledger row contributes (no
    orders twin), its fee is the exit leg only, so the fraction is a lower bound —
    `render_report` labels it rather than letting it pose as the both-legs figure.
    """
    fees = sum((t.fees_paid() for t in trips), Decimal(0))
    notional = sum(((t.entry_fill + t.exit_fill) * t.qty for t in trips), Decimal(0))
    if notional == 0:
        return Decimal(0)
    return fees / notional


def build_sample(
    per_profile: Sequence[tuple[str, OrdersRead, Sequence[LedgerRow]]],
) -> PooledSample:
    """Union the profiles into one pool, deduped, oldest first.

    Each profile contributes its `OrdersRead` (already matched) and its raw ledger rows
    (converted here). Dedup drops the ORDERS-derived twin — the ledger row is the
    authoritative record of the same round trip, keyed on profile, product, quantity,
    entry fill and exit fill — BUT the twin's both-legs fees ride into the kept row: the
    ledger records only the exit leg's fee (the entry fee is folded into `pnl_net` and
    stored nowhere), while the twin observed both legs, so the measured-fee line cannot
    silently understate once ledger rows exist. Ordering is `closed_at` then product — the
    ledger reader's own oldest-first convention — with fill and source as tie-breakers so
    the order is a pure function of the input.
    """
    trips: list[RoundTrip] = []
    summaries: list[ProfileSummary] = []
    total_open = 0
    total_unfilled = 0
    total_strays = 0
    total_deduped = 0
    for profile, read, ledger_rows in per_profile:
        ledger = ledger_round_trips(profile, ledger_rows)
        ledger_by_key: dict[TripKey, RoundTrip] = {trip.key(): trip for trip in ledger}
        upgraded: dict[TripKey, RoundTrip] = {}
        kept: list[RoundTrip] = []
        deduped = 0
        for trip in read.trips:
            ledger_twin = ledger_by_key.get(trip.key())
            if ledger_twin is None:
                kept.append(trip)
                continue
            deduped += 1
            upgraded[trip.key()] = replace(
                ledger_twin,
                entry_fee=trip.entry_fee,
                exit_fee=trip.exit_fee,
                fees_both_legs=True,
            )
        combined = [upgraded.get(trip.key(), trip) for trip in ledger] + kept
        combined.sort(key=lambda t: (t.closed_at, t.product_id, t.exit_fill, t.source))
        trips += combined
        total_open += read.open_buys
        total_unfilled += read.unfilled_orders
        total_strays += read.stray_sells
        total_deduped += deduped
        summaries.append(
            ProfileSummary(
                profile=profile,
                modes=tuple(sorted({t.mode for t in combined})) or ("none",),
                rules=tuple(sorted({t.rule for t in combined})) or ("none",),
                closed=len(combined),
                of_which_dca=sum(1 for t in combined if t.rule == "dca"),
                open_buys=read.open_buys,
                unfilled=read.unfilled_orders,
                stray_sells=read.stray_sells,
                ledger_rows=len(ledger),
                deduped=deduped,
                inversions=sum(1 for t in combined if t.timestamps_inverted()),
            )
        )
    trips.sort(key=lambda t: (t.closed_at, t.product_id, t.exit_fill, t.source))
    return PooledSample(
        profiles=tuple(profile for profile, _read, _ledger in per_profile),
        trips=trips,
        summaries=tuple(summaries),
        excluded_open=total_open,
        excluded_unfilled=total_unfilled,
        stray_sells=total_strays,
        deduped=total_deduped,
    )


def power_sentence(n: int) -> str:
    """The sentence #427 requires in the report, generated at the n the MEASUREMENT uses.

    Exact wording, pinned by test: "at this n_eff (N effective of M pooled), this review
    can only see an edge of X points or larger (80% power, one-sided 5%)". `n` is the
    counted basis (`stat.n_trades`, wins+losses — a scratch counts toward nothing), the
    SAME n `significance()` puts its standard error at, so the artifact never carries two
    different n_eff numbers beside each other. The numbers are
    `throughput.n_eff`/`throughput.detectable_edge` on the real count — n=100 says
    "20.0 points" (the corrected discussion's display), n=12 says "57.6 points", and the
    sentence is constructed into the report, not typed into it.
    """
    if n <= 0:
        raise ValueError("n must be > 0 -- a pool with nothing counted refuses; no sentence")
    effective = n_eff(Decimal(n))
    edge_points = (detectable_edge(effective) * Decimal(100)).quantize(Decimal("0.1"))
    return (
        f"at this n_eff ({effective.quantize(Decimal('0.01'))} effective of {n} "
        f"pooled), this review can only see an edge of {edge_points} points or larger "
        "(80% power, one-sided 5%)"
    )


@dataclass(frozen=True)
class DescriptiveReview:
    """The assembled review: the sample, the measurement, the sentence, or the refusal.

    `refusal` is None exactly when the pool has at least one COUNTED trade — a non-empty
    pool whose every trip is a scratch has no win rate and no n to put a power sentence at,
    so it refuses too; a refused review has no report to render and the driver exits
    non-zero rather than write a degenerate artifact.
    """

    run_date: str
    event_date: str
    profiles: tuple[str, ...]
    sample: PooledSample
    stat: FamilySignificance
    fee_pct: Decimal
    sentence: str
    refusal: tuple[str, ...] | None
    notes: tuple[str, ...]


def descriptive_review(
    sample: PooledSample,
    run_date: str,
    event_date: str = EVENT_DATE,
) -> DescriptiveReview:
    """Assemble the review — or its refusal — for a pool read on `run_date`."""
    stat = sample.significance()
    if is_refused(sample):
        refusal = (
            f"nothing to review: {sample.counted()} counted win/loss trades of "
            f"{sample.n_pooled()} pooled match the pre-registered definition "
            "(a scratch counts toward nothing)",
            f"excluded as open positions: {sample.excluded_open}; unfilled orders: "
            f"{sample.excluded_unfilled}; stray sells: {sample.stray_sells}",
            f"the {event_date} review REFUSES rather than report a degenerate sample "
            "(#427: a pool with nothing counted has no win rate, no interval, and no "
            "sentence)",
        )
        return DescriptiveReview(
            run_date=run_date,
            event_date=event_date,
            profiles=sample.profiles,
            sample=sample,
            stat=stat,
            fee_pct=Decimal(0),
            sentence="",
            refusal=refusal,
            notes=(),
        )
    notes: list[str] = []
    inversions = sum(1 for s in sample.summaries for _ in range(s.inversions))
    if inversions:
        notes.append(
            f"{inversions} round trip(s) have a recorded close before their recorded open "
            "(order id governed the match; the ledger's created_at disagrees with itself)"
        )
    if sample.stray_sells:
        notes.append(
            f"{sample.stray_sells} filled SELL(s) with no BUY on record -- no entry, so no "
            "pnl can be formed honestly; excluded and counted, never priced by guess"
        )
    return DescriptiveReview(
        run_date=run_date,
        event_date=event_date,
        profiles=sample.profiles,
        sample=sample,
        stat=stat,
        fee_pct=realized_fee_pct(sample.trips),
        sentence=power_sentence(stat.n_trades),
        refusal=None,
        notes=tuple(notes),
    )


def is_refused(sample: PooledSample) -> bool:
    """True when the pool has no counted trades — empty, or every trip a scratch.

    The honest answer to nothing to review: a scratch counts toward nothing, so a pool of
    only scratches has no measurement to render any more than an empty pool does.
    """
    return sample.counted() == 0


def render_report(review: DescriptiveReview) -> list[str]:
    """The report skeleton, with the sentence and the not-a-gate statement built in.

    Every block is constructed: the title names the event, the method citation names
    `significance.py`/`throughput.py`, raw n is never shown without its n_eff, the power
    sentence is `review.sentence` verbatim, and the closing statement says what this report
    does NOT say — there is no pass/fail verdict on the edge anywhere in it.
    """
    if review.refusal is not None:
        raise ValueError("a refused review renders no report -- print its refusal instead")
    sample = review.sample
    stat = review.stat
    fee_bp = (review.fee_pct * Decimal(10000)).quantize(Decimal("0.1"))
    edge_points = (stat.edge * Decimal(100)).quantize(Decimal("0.1"))
    lines = [
        f"# The {review.event_date} pooled forward-trades review — a descriptive report, "
        "NOT a pass/fail gate",
        "",
        f"Run {review.run_date} over {len(review.profiles)} pre-registered profiles: "
        + ", ".join(f"`{p}`" for p in review.profiles)
        + ".",
        "Event tracked in #353; the descriptive reframing is #427's correction of record "
        "(discussion #359's corrected",
        "comment). Method: `keel/research/significance.py` with the `n_eff` correction from "
        f"`keel/research/throughput.py` (design effect {design_effect()}, #427).",
    ]
    if review.run_date < review.event_date:
        lines += [
            "",
            f"**Preview run on {review.run_date}: the review event re-runs on "
            f"{review.event_date} (#353) under this same pre-registration.** The pool below "
            "is what exists today, not what the event will see.",
        ]
    lines += [
        "",
        "## What counts as a pooled trade (pre-registered in the driver's docstring)",
        "",
        "- one CLOSED forward round trip, win/loss resolved by the sign of fee-honest net",
        "  pnl: a `trade_outcomes` row (the closed-trade ledger rails 11/16 read, `pnl_net`",
        "  realized and net of fees), or — where the ledger has none — a filled BUY matched",
        "  to a filled SELL of the same profile, product, rule, mode and quantity (FIFO by",
        "  order id), priced with the ledger writer's own formula",
        "  `(exit - entry) * qty - entry fee - exit fee`;",
        "- deduplicated on (profile, product, quantity, entry fill, exit fill): a round trip",
        f"  the ledger already recorded is never counted twice ({sample.deduped} deduped this",
        "  run), and a deduped twin's both-legs fees ride into the kept ledger row;",
        f"- excluded and counted instead: OPEN positions ({sample.excluded_open}), unfilled",
        f"  or rejected orders ({sample.excluded_unfilled}); a net pnl of exactly zero is a",
        f"  SCRATCH and counts toward nothing ({sample.scratches()});",
        "- DCA round trips count — their forward P&L is real (`streak.py` records them too) —",
        "  and the composition labels them.",
        "",
        "## The pool as it stands",
        "",
        "| profile | modes | closed pooled | of which dca | open (excl.) | unfilled (excl.) "
        "| stray sells | ledger rows | deduped |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in sample.summaries:
        lines.append(
            f"| `{row.profile}` | {'/'.join(row.modes)} | {row.closed} | {row.of_which_dca} "
            f"| {row.open_buys} | {row.unfilled} | {row.stray_sells} | {row.ledger_rows} "
            f"| {row.deduped} |"
        )
    lines += [
        f"| **pooled** | — | **{sample.n_pooled()}** | — | {sample.excluded_open} "
        f"| {sample.excluded_unfilled} | {sample.stray_sells} | — | {sample.deduped} |",
        "",
        "Rules in the pool: "
        + ", ".join(
            f"`{rule}` x{n}"
            for rule, n in sorted(
                (r, sum(1 for t in sample.trips if t.rule == r))
                for r in {t.rule for t in sample.trips}
            )
        )
        + ".",
        "",
        "## The measurement (descriptive — `keel/research/significance.py` at the fees "
        "actually paid)",
        "",
        _n_line(sample, stat),
        f"- payoff b={_fmt(stat.payoff_b)} -> break-even win rate {_fmt(stat.break_even)}; "
        f"observed {_fmt(stat.win_rate)} -> edge {_fmt(stat.edge)} ({edge_points} points)",
        f"- 95% one-sided lower bound on the edge: {_fmt(stat.edge_ci_low)}",
        _fee_line(sample, fee_bp),
    ]
    if review.notes:
        lines += [f"- note: {note}" for note in review.notes]
    lines += [
        "",
        f"**{review.sentence}**",
        "",
        "## What this report does not say",
        "",
        "This is not a pass/fail gate. Nothing here promotes, demotes, or blocks a rule: the",
        "edge, its interval and its z are descriptive measurements of the pool above, and no",
        "verdict is pronounced on them. The only verdict-shaped statement this report makes is",
        "about POWER — the sentence above — because at this n_eff a null result means",
        '"the review could not have seen it", not "there is no edge". That distinction is',
        "#427's entire finding.",
        "",
        "The owner's floor decision remains open (#427): keep n=100 pooled as a descriptive",
        "trigger (the sentence above is what that buys), or raise `min_trades` to 259+ pooled",
        "(n_eff 101) before any future review is allowed to be confirmatory.",
    ]
    return lines


def _n_line(sample: PooledSample, stat: FamilySignificance) -> str:
    """The raw-n line — n pooled, its counted split when scratches exist, then n_eff.

    `stat.n_effective` is `n_eff(stat.n_trades)` (wins+losses basis), so when the pool has
    scratches the split is printed WITH a label: the same n the power sentence below is
    generated at, never a second, disagreeing n_eff beside it.
    """
    line = f"- closed trades n={sample.n_pooled()} pooled"
    if sample.scratches():
        line += (
            f" ({stat.n_trades} win/loss + {sample.scratches()} scratch counting toward nothing)"
        )
    return (
        f"{line} -> {stat.n_effective.quantize(Decimal('0.01'))} effective "
        f"(design effect {design_effect()}, #427)"
    )


def _fee_line(sample: PooledSample, fee_bp: Decimal) -> str:
    """The measured-fee line — per leg when every trip knows both legs, a lower bound else.

    A pure ledger row's `fees` is the exit order's fee only (the entry fee is folded into
    `pnl_net` and stored nowhere), so when any such trip is in the pool the line says LOWER
    bound and why, rather than letting the exit-leg figure pose as the both-legs one.
    """
    exit_leg_only = sum(1 for t in sample.trips if not t.fees_both_legs)
    if not exit_leg_only:
        return (
            f"- fees as recorded across the pool: {fee_bp} bp per leg (realized fees over "
            "notional traded, both legs — the forward trades' regime is measured, not assumed)"
        )
    return (
        f"- fees as recorded across the pool: at least {fee_bp} bp per leg — a LOWER bound: "
        f"{exit_leg_only} of {sample.n_pooled()} pooled trip(s) are ledger rows whose recorded "
        "fee is the exit leg only (the entry fee is folded into pnl_net and not stored), so "
        "the true both-legs figure is higher than this; the regime is measured, not assumed"
    )


def _fmt(value: Decimal) -> str:
    """Plain number or the two infinities a degenerate payoff can produce."""
    if value.is_infinite():
        return "inf" if value > 0 else "-inf"
    return str(value.quantize(Decimal("0.0001")))
