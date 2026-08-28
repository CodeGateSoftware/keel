"""What a venue can do, declared by its adapter and checked before the engine sizes an order."""

from __future__ import annotations

from dataclasses import dataclass

from keel_broker_api.orders import ORDER_KINDS

#: The instrument classes an adapter may declare: one KEEL-side name per class the 2026-08-05
#: Coinbase asset-class study found at the venue
#: (`docs/experiments/2026-08-05-coinbase-asset-class-feasibility.md`).
#:
#: ⚠️ These are **keel's spellings, not the venue's.** Coinbase's `product_type` field reads
#: `SPOT` / `FUTURE` / `EQUITY`; this vocabulary is lowercase, and plural for futures. That is
#: deliberate -- an adapter declares what it can do in the port's words, so a second venue with
#: its own casing has one obvious answer rather than a choice -- and it is why the venue's own
#: spellings are REFUSED here rather than accepted as synonyms. `SPOT` and `future` are near
#: misses that a `frozenset` would otherwise carry silently into a set gating nothing, so
#: `__post_init__` rejects them by name, exactly as `ORDER_KINDS` does. An adapter author who
#: pastes the venue's value gets an error at construction naming what they pasted.
ASSET_CLASSES: frozenset[str] = frozenset({"spot", "futures", "equity"})


@dataclass(frozen=True)
class BrokerCapabilities:
    """An adapter's self-declaration. The conformance suite verifies it does not lie.

    ⚠️ `asset_classes` is **not** what keeps keel spot-only today, and no engine code reads it.
    The spot gate on the live path is **rail 19 (`spot_instrument`)** in
    `keel/execution/guards.py`, which checks the product id's shape and needs no broker handle.
    That is deliberate, not an oversight: `guards.check` is broker-less BY DESIGN (its rails
    must hold in paper mode, where the executor passes `broker=None`), so a gate built on this
    field cannot live there. Every broker the live path constructs since #524 finished the
    broker-port migration IS an adapter that answers `capabilities()` -- the grandfather clause
    for the pre-port client, which had no `capabilities()` at all, retired with it -- but
    reachable is not the same as read, and a capabilities gate that no path consults is still
    dead code that reads as a defence. That exact pattern was built and deleted once already
    (R1's "what was deliberately NOT shipped").

    This field's job until something consumes it is to keep the declaration honest and
    checkable, so the first consumer inherits a vocabulary rather than a free-form set. When
    that happens the reconciliation belongs at LOAD time, not as a per-order raise -- a raise
    on the exit path can trap a position.
    """

    venue: str
    supported_orders: frozenset[str]
    supports_native_preview: bool
    synthesizes_preview: bool
    supports_fee_summary: bool
    quote_currencies: frozenset[str]
    asset_classes: frozenset[str]
    #: Whether this venue CLOSES (FR-9): equities and other session-bound markets have
    #: weekends and holidays, crypto does not. Deliberately REQUIRED, not defaulted -- a
    #: default would be an answer to a question only the venue knows, and the likeliest
    #: default (the 24/7 crypto posture keel grew up on) is exactly the one that reads a
    #: closed equities venue as a stale feed. An adapter declaring `True` must answer
    #: `market_clock()` from the venue's own clock, never a locally maintained calendar.
    session_bound: bool
    #: Whether this adapter spends settled cash only and has NO borrowing path (#372, PRD
    #: §5 "Cash-account discipline": margin borrowing is riba -- the posture's whole
    #: claim; it sidesteps nothing on PDT, where keel's safety is the CADENCE -- one
    #: evaluation per session bar, holds overnight by construction -- not the posture).
    #: Deliberately REQUIRED, not defaulted -- same rule as `session_bound`: the borrowing
    #: question has no default answer, and the likeliest default (silently reading as
    #: compliant) is exactly the posture violation this field exists to name. Every
    #: first-party adapter declares `True`, and that uniformity is the contract: the one
    #: adapter that someday declares `False` is declaring a posture the engine can refuse
    #: AT LOAD TIME rather than an omission quietly reading as compliance.
    #:
    #: ⚠️ Like `asset_classes`, this is a DECLARATION, not a gate: no engine path reads it
    #: today, and the ENFORCED half of the posture lives adapter-side where the venue's
    #: own account state is reachable -- `keel_broker_alpaca.verify_cash_account` refuses
    #: a margin-postured account at broker build, fail-closed on an unreadable one. A rail
    #: cannot do that (guards are broker-less by design), and this field must not pretend
    #: one does.
    cash_only: bool

    def __post_init__(self) -> None:
        unknown = self.supported_orders - ORDER_KINDS
        if unknown:
            raise ValueError(f"unknown order kinds: {sorted(unknown)}")
        unknown_classes = self.asset_classes - ASSET_CLASSES
        if unknown_classes:
            raise ValueError(f"unknown asset classes: {sorted(unknown_classes)}")

    @property
    def can_preview(self) -> bool:
        """Whether `confirm` mode is usable against this venue at all."""
        return self.supports_native_preview or self.synthesizes_preview


__all__ = ["ASSET_CLASSES", "BrokerCapabilities"]
