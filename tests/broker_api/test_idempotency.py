"""`resolve_client_order_id` -- the port's one rule for turning an intent into a venue id (#409).

This is the only place the derivation is defined, and that is the point: an adapter that invented
its own rule would make the same caller key mean different orders at different venues, which is
the exact failure the parameter exists to prevent.
"""

from __future__ import annotations

import uuid

from keel_broker_api.port import CLIENT_ORDER_ID_NAMESPACE, resolve_client_order_id


def test_no_key_mints_a_fresh_id_per_attempt() -> None:
    """The historical behaviour of every adapter, and still the default.

    It is the right answer to the opposite hazard: an id derived from the ORDER would silently
    collapse two orders a strategy genuinely meant to place twice into one, and a position at
    half the intended size is as wrong as one at twice it.
    """
    assert resolve_client_order_id(None) != resolve_client_order_id(None)


def test_the_same_key_always_resolves_to_the_same_id() -> None:
    """The whole mechanism. Two attempts under one key are one order at any venue that
    deduplicates on `client_order_id`."""
    assert resolve_client_order_id("cycle-7:pos-3:stop") == resolve_client_order_id(
        "cycle-7:pos-3:stop"
    )


def test_different_keys_resolve_to_different_ids() -> None:
    """Deduplication must not leak across intents: a take-profit and a stop for the same position
    are two orders, and collapsing them would leave one of them unplaced."""
    assert resolve_client_order_id("cycle-7:pos-3:stop") != resolve_client_order_id(
        "cycle-7:pos-3:take-profit"
    )


def test_a_derived_id_is_a_uuid_whatever_the_key_looked_like() -> None:
    """Robinhood's `client_order_id` is a UUID; Alpaca's is a string of up to 128 characters.
    Hashing to a UUID lands on the intersection, so a caller can use whatever natural key it has
    without knowing which venue the order will be routed to."""
    for key in ["", "a", "cycle-7:pos-3:stop", "x" * 500, "unicode -- ✓ ﷼", "  spaces  "]:
        assert uuid.UUID(resolve_client_order_id(key)).version == 5, key


def test_the_derivation_is_pinned_not_merely_deterministic_within_one_run() -> None:
    """A retry issued by a NEW process -- what a crash produces, and the case an in-memory table
    of "ids I already sent" cannot cover -- must derive the same id as the attempt that crashed.
    So the expected value is written out here rather than recomputed from the implementation:
    recomputing would pass just as happily if the namespace changed underneath it, and a changed
    namespace silently makes every previously-derived id unreachable.
    """
    assert resolve_client_order_id("cycle-7:pos-3:stop") == str(
        uuid.uuid5(uuid.UUID("6f1b6d4e-5a2c-4f8b-9c3d-1e0a7b5c9d42"), "cycle-7:pos-3:stop")
    )
    assert CLIENT_ORDER_ID_NAMESPACE == uuid.UUID("6f1b6d4e-5a2c-4f8b-9c3d-1e0a7b5c9d42")


def test_an_empty_key_is_a_key_and_not_the_absence_of_one() -> None:
    """`""` and `None` must not collapse. `None` means "mint per attempt"; an empty string is a
    caller that passed something, and silently treating it as no key would turn a deduplicated
    retry back into a second live order at the moment the caller thought it was protected."""
    assert resolve_client_order_id("") == resolve_client_order_id("")
