"""Income purification — segregating non-compliant credits (KB §65.9).

Ayub, Ch 8.8.1 / 5.5.3: *"Islamic asset management companies have to purify their income by
deducting from the returns on the investments the earnings emanating from any unacceptable
source… It is obligatory to dole away the prohibited income that is mixed up with the earnings."*
Institutions run a **Charity Account**: non-compliant income is segregated and given away, **never
recognised as profit**.

**Why this is machine-computable where §56.3 is not.** §56.3's obligation (disable USDC rewards) is
*preventive* and operator-attested — Advanced Trade exposes no rewards endpoint, so we cannot
verify zero accrual, only attest a setting. §65.9 is the remedy for the gap that leaves: incoming
interest/reward credits DO appear in the transaction ledger, so what actually accrued can be
counted after the fact.

**Two consequences worth stating plainly** (both from §65.9):

1. **P&L correctness is a compliance concern, not just an accounting one.** Riba credits silently
   included in equity would inflate the equity base that fixed-fractional sizing computes from —
   **riba compounding into position size.** Segregation is the fix.
2. It composes with §33.1's zakat estimate: **zakat is on purified wealth**, so purification runs
   first.

⛔ **REPORT-ONLY. The agent never disposes of funds.** This computes an amount owed and says so;
moving it is the operator's act, exactly as §33.1's zakat estimate is.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

CLEAN = "clean"
NON_COMPLIANT = "non_compliant"
REVIEW = "review"

#: Substrings of a transaction type that mark it as non-compliant income. Matched against real
#: Coinbase export strings -- "Reward Income" and "Incentives Rewards Payout" both appear in
#: this project's own imported history.
NON_COMPLIANT_MARKERS = (
    "reward",
    "interest",
    "staking",
    "incentive",
    "earn",
    "airdrop",
    "rebate",
    "yield",
    "inflation",
)

#: Substrings marking a credit as ordinary trading or custody activity.
CLEAN_MARKERS = (
    "buy",
    "sell",
    "convert",
    "deposit",
    "withdraw",
    "send",
    "receive",
    "transfer",
)


def classify(tx_type: str | None) -> str:
    """`CLEAN`, `NON_COMPLIANT`, or `REVIEW`.

    ⚠️ **An unrecognised type is `REVIEW`, never a silent default.** Calling it clean would let
    riba into P&L; calling it non-compliant would over-purify and misstate a religious obligation
    as fact. Neither is honest about an unknown, so it is surfaced for a human.
    """
    if not tx_type or not tx_type.strip():
        return REVIEW
    lowered = tx_type.strip().lower()
    # Non-compliant is checked FIRST: a type naming both (a "reward buy", say) is a reward.
    if any(marker in lowered for marker in NON_COMPLIANT_MARKERS):
        return NON_COMPLIANT
    if any(marker in lowered for marker in CLEAN_MARKERS):
        return CLEAN
    return REVIEW


@dataclass(frozen=True)
class PurificationEntry:
    ts: int
    asset: str
    tx_type: str
    qty: Decimal
    amount_usd: Decimal


@dataclass(frozen=True)
class PurificationReport:
    """What is owed to charity, and what nobody has classified yet."""

    entries: list[PurificationEntry] = field(default_factory=list)
    needs_review: list[PurificationEntry] = field(default_factory=list)

    @property
    def total_owed_usd(self) -> Decimal:
        return sum((e.amount_usd for e in self.entries), Decimal("0"))

    @property
    def owed_by_asset(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for entry in self.entries:
            out[entry.asset] = out.get(entry.asset, Decimal("0")) + entry.amount_usd
        return dict(sorted(out.items()))

    @property
    def qty_by_asset(self) -> dict[str, Decimal]:
        """Units received, not just their value -- what is actually held from a tainted source."""
        out: dict[str, Decimal] = {}
        for entry in self.entries:
            out[entry.asset] = out.get(entry.asset, Decimal("0")) + entry.qty
        return dict(sorted(out.items()))


def _decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value).replace("$", "").replace(",", "").strip() or "0")
    except (InvalidOperation, ValueError):
        return Decimal("0")


def build_report(transactions: Iterable[dict[str, Any]]) -> PurificationReport:
    """Segregate non-compliant credits from a transaction ledger. Report-only."""
    entries: list[PurificationEntry] = []
    review: list[PurificationEntry] = []

    for tx in transactions:
        verdict = classify(tx.get("type"))
        if verdict == CLEAN:
            continue
        entry = PurificationEntry(
            ts=int(tx.get("ts") or 0),
            asset=str(tx.get("asset") or "?"),
            tx_type=str(tx.get("type") or ""),
            qty=_decimal(tx.get("qty")),
            amount_usd=_decimal(tx.get("total")),
        )
        (entries if verdict == NON_COMPLIANT else review).append(entry)

    return PurificationReport(entries=entries, needs_review=review)
