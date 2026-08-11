"""Validate the Robinhood adapter's *assumptions* against the live venue, reading only.

Every test under `tests/broker_robinhood/` runs against a canned transport, so what the suite
proves is that the adapter is internally consistent with fixtures **we wrote ourselves**. Three
things it therefore cannot prove, and which only a real credential can:

1. that Robinhood accepts our Ed25519 signature at all (the signing tests verify it against our
   own verify-key, which is circular with respect to the venue);
2. that the endpoint paths are the real ones -- they are pinned against our *reading* of
   https://docs.robinhood.com/crypto/trading/, and PR #194 showed that reading can be wrong in
   both directions;
3. that the response *shapes* are real. `tests/fixtures/rh_accounts.json`'s nested
   `fee_tier_status` is the load-bearing case: every number in `get_fee_summary` and every
   `Preview.est_fee` is derived from a shape nothing outside this repository has corroborated.

This script closes 1-3 without placing an order and without risking a cent. It is deliberately
NOT a conformance suite and NOT part of the shipped wheel -- it is an operator tool, run by hand
when a credential exists, and its only output is a shape report.

## What "the shape matched" is worth, exactly

A collection is validated across ALL of its elements, not sampled. `shape_of` unions the keys of
every element of a list and marks a key carried by only some of them `key (63/89)`, so a field
that 71% of `trading_pairs` rows carry can no longer hide behind a `results[0]` that lacks it.
That is not a hypothetical: it is #230. Until it was fixed this script reported 5/5 and then 6/6
matched while blind to `min_order_amount`, a field BTC-USD and ETH-USD both carry, and #218
deleted that field from the fixture believing the report.

⚠️ **A run corroborates only what the account's own data exercises.** The probe cannot validate
field names it never receives, and no report line distinguishes "the venue has no such field"
from "this account produced no row carrying it". The `orders` probe is the standing example: on
an account with no crypto order history `results` comes back empty, so that probe proves the
path, the signature and the pagination envelope and **nothing whatsoever about field names**. The
same caveat applies in miniature to any endpoint whose rows vary -- a key absent from all 89 rows
this account can see is absent from THIS observation, which is weaker than absent from the API.

The first run of it (#217) settled all three: ten requests, zero 401s, every endpoint path
correct, `fee_tier_status` corroborated key for key -- and four fixture shapes wrong, one of them
a live defect that left every market preview unpriced. It also produced five false positives of
its own, which `fixture_shape` below exists to prevent recurring. What it still cannot fully
reach is the ORDER lifecycle. The `orders` probe added for #197 stays inside the read-only
guarantee -- it is a GET, so `_ReadOnly` still enforces it at the request layer -- and it DOES
verify the list endpoint's path, that the signature is accepted, and the pagination envelope.
What it verifies only conditionally is the order OBJECT's field names: that happens **only if
the account happens to have order history**. On an account that has never traded crypto on
Robinhood, `results` comes back empty, and `compare_shapes` skips comparing a list whose rows
are `<empty>` -- so a bare account reports a shape match it has not actually earned, and an
operator reading the report needs to know that a match there is not corroboration.
`fee_charged`'s JSON quoting -- a quoted string vs an unquoted number, the exact ambiguity #197
turns on -- is precisely what the probe would settle if a single order row came back. So
`rh_order_open.json`, `rh_order_filled.json` and `rh_order_canceled.json` remain unverified
whenever the account has no order history; placing an order is still the only way to guarantee
an observation.

## Why it cannot place an order

`_ReadOnly` wraps the transport's request method and raises on any method other than GET, so the
guarantee does not rest on this module merely *declining* to call `create_order`. A future edit
that adds a POST fails loudly here rather than quietly placing something. That matters more than
usual: this is the one script in the repository intended to run against live credentials, and
Robinhood publishes no sandbox, so "live" is the operator's real money.

## Why it prints shapes, not values

The question being asked is structural ("does `fee_tier_status` exist, and what keys does it
carry"), so rendering the account's actual balances and holdings would leak private financial
data into a terminal, a CI log, or a pasted bug report to buy nothing. Every leaf is replaced by
its type. `--show-values` exists for the one case where a value IS the answer -- confirming a
`symbol` string's exact spelling, say -- and even then it never prints the credential.

Usage::

    uv run python scripts/robinhood_smoke.py            # shape report vs the fixtures
    uv run python scripts/robinhood_smoke.py --json     # machine-readable, for an issue comment

Requires `ROBINHOOD_API_KEY` and `ROBINHOOD_PRIVATE_KEY` in a git-ignored `.env`. The latter is
the base64 of the raw 32-byte Ed25519 *seed* generated locally -- NOT the base64 public key that
was pasted into Robinhood's credential page. Transposing the two produces a 401 that is
indistinguishable from a signing bug, so this script checks the key's shape before spending a
request on it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"

#: Read-only probes, in dependency order: `accounts` first because it resolves the account
#: number every later call needs, and because it is the cheapest possible proof that the
#: signature is accepted.
#:
#: `estimated_price` is probed on the ASK side only, so `tests/fixtures/rh_estimated_price_bid.json`
#: is deliberately absent from this list. The two sides do not answer with the same shape -- the
#: bid side omits `est_total_cost` entirely (#217 F7) -- so probing both would need a sixth entry
#: keyed by side rather than by endpoint. The bid shape is pinned by the adapter's tests against
#: that fixture instead; adding a side-aware probe here is a follow-up, not a nit.
PROBES: tuple[tuple[str, str], ...] = (
    ("accounts", "rh_accounts.json"),
    ("trading_pairs", "rh_trading_pairs.json"),
    ("best_bid_ask", "rh_best_bid_ask.json"),
    ("estimated_price", "rh_estimated_price.json"),
    ("holdings", "rh_holdings.json"),
    ("orders", "rh_orders.json"),
)

#: The symbol every marketdata probe is run against. BTC-USD is the one pair we can be confident
#: is tradable on any Robinhood Crypto account, so a failure here is a real finding rather than
#: "that account cannot trade that asset".
PROBE_SYMBOL = "BTC-USD"

#: A raw Ed25519 seed is 32 bytes, which is 44 base64 characters with padding. Checking this
#: before the first request turns the most likely operator error -- pasting the public key, or a
#: PEM, or a hex string -- into a precise message instead of a 401.
_SEED_B64_LEN = 44

#: The keys `RobinhoodTransport._paginate` consumes and does not pass on.
#:
#: Every probe below is a paginated read, so every probe's response reaches this script already
#: resolved into `{"results": [...]}` with the cursor stripped -- that is what `_paginate` is FOR.
#: The committed fixtures, by contrast, are single RAW pages and still carry both keys. The first
#: live run compared the two directly and reported `next` and `previous` `MISSING AT VENUE` on all
#: five probes, on a run where the venue had sent both every time. That was this script's bug, not
#: a finding, and it buried four real findings underneath ten lines of noise. A report that cries
#: wolf on every run is worse than no report at all, so the fixture is normalized to the shape a
#: probe can actually be compared against before anything is compared.
_PAGINATION_ENVELOPE_KEYS = frozenset({"next", "previous"})


class ReadOnlyViolation(RuntimeError):
    """Raised when anything in this script attempts a non-GET request."""


class _ReadOnly:
    """Installs a GET-only guard onto a `RobinhoodTransport` and records what it issues.

    The refusal is at the REQUEST layer, not the method layer: a method-level allowlist only
    holds while nobody adds a method, whereas `_request` is the single choke point every call in
    the transport passes through.

    Note the guard is **installed onto the transport instance**, not merely wrapped around it.
    Wrapping with `__getattr__` alone is a trap that looks correct and is not: `get_accounts()`
    would be forwarded to the wrapped object, whose body then calls its OWN `self._request`,
    sailing straight past the wrapper. The guarantee would hold only for calls made directly
    against the wrapper -- which is to say, for the tests and not for the probes. Rebinding the
    instance attribute is what makes the transport's internal calls route through here too.
    """

    def __init__(self, transport: Any) -> None:
        self._transport = transport
        self.calls: list[tuple[str, str]] = []
        # Captured BEFORE rebinding, or `_request` below would recurse into itself.
        self._inner = transport._request
        transport._request = self._request

    def __getattr__(self, name: str) -> Any:
        return getattr(self._transport, name)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if method.upper() != "GET":
            raise ReadOnlyViolation(
                f"robinhood_smoke.py attempted a {method.upper()} to {path!r}. This script is "
                f"read-only by construction; it must never place, amend or cancel an order."
            )
        self.calls.append((method.upper(), path))
        return self._inner(method, path, **kwargs)


def _annotate(text: str, note: str) -> str:
    """Attach a human note to a shape token without changing what the token IS.

    Everything a shape carries beyond the bare structure -- `(63/89)` on a partially present key,
    the per-type tally on a key the venue quotes inconsistently -- lives in a trailing ` (...)`
    suffix, and `_bare` below strips it. That split is the single place where "how many elements
    carried this" is decided to be INFORMATION rather than a difference: `compare_shapes` compares
    bare tokens, so a fixture that carries the key matches a venue that sends it on 63 of 89 rows,
    while the count still reaches the operator's terminal and the `--json` output verbatim.

    The suffix is stored in the token rather than in a parallel structure so it survives
    `json.dumps` and so a partially present key whose value is an OBJECT is annotated the same way
    as one whose value is a leaf -- the note rides on the key, which every value type has.
    """
    return f"{text} ({note})"


def _bare(token: Any) -> Any:
    """A shape token with its ` (...)` note removed, which is the form comparisons use."""
    if isinstance(token, str):
        return token.split(" (", 1)[0]
    return token


def _kind(item: Any) -> str:
    """One word for what an element IS, for the mixed-type tally: a type name, or the container."""
    if isinstance(item, dict):
        return "object"
    if isinstance(item, list):
        return "array"
    return shape_of(item)


def _merged(items: list[Any]) -> Any:
    """Summarise what EVERY element of a list looks like, as one element-shaped value.

    This is the fix for #230 D1. The previous implementation reduced a list to
    `[shape_of(value[0]), "... N items"]`, so every probe validated one element and reported a
    match for the whole collection. `trading_pairs` returns 89 pairs in two distinct key-sets --
    63 carry `min_order_amount`, 26 do not, and `results[0]` is one of the 26 -- so the probe
    reported 6/6 matched while blind to a field present on every asset keel trades. A field
    carried by SOME elements is an ordinary API shape; the probe has to model it, not collapse it.

    Three merges, by what the elements are:

    * **objects** -> the UNION of their keys. A key present on only some of them is annotated
      `key (63/89)`, which makes partial presence visible without dropping the key from the shape
      or claiming it is always there. The key is still in the union, so a fixture that omits it
      is reported `NEW AT VENUE` -- which is exactly the miss #218 shipped.
    * **lists** -> the elements are flattened and merged as one population. A nested list is
      summarised across the whole parent collection rather than per parent, because the question
      is still "what can a row look like".
    * **anything else** -> the distinct type names, joined with `|` and tallied, e.g.
      `Decimal|str (77 str, 12 Decimal)`. Taking the first element's type instead would hide a
      real venue inconsistency at a venue that has already been caught quoting the same kind of
      value two ways in one object (#217 F6), so a mixed type is deliberately NOT equal to either
      of its halves and reports as a `TYPE DIFFERS`.

    Only ONE shape comes back however long the list is: an 89-pair response prints one merged row
    and a count, never 89 rows.
    """
    total = len(items)

    if all(isinstance(item, dict) for item in items):
        merged: dict[str, Any] = {}
        for key in sorted({key for item in items for key in item}):
            present = [item[key] for item in items if key in item]
            label = key if len(present) == total else _annotate(key, f"{len(present)}/{total}")
            merged[label] = _merged(present)
        return merged

    if all(isinstance(item, list) for item in items):
        flattened = [element for item in items for element in item]
        if not flattened:
            return ["<empty>"]
        return [_merged(flattened), f"... {len(flattened)} items"]

    tallies = Counter(_kind(item) for item in items)
    if len(tallies) == 1:
        return next(iter(tallies))
    counts = ", ".join(f"{count} {kind}" for kind, count in tallies.most_common())
    return _annotate("|".join(sorted(tallies)), counts)


def shape_of(value: Any) -> Any:
    """Reduce a decoded JSON value to its structure, discarding every leaf.

    A list collapses to ONE summary of all of its elements plus a count, rather than one entry per
    item: the question is what an element can look like, and a 89-pair `trading_pairs` response
    would otherwise bury the answer in 89 near-identical copies. That summary is a union, not a
    sample -- see `_merged` for why the difference cost this repository a real field. An empty list
    is reported as such, since "the venue returned nothing" is itself a finding: it is how an
    unfunded account presents, and it is what would make a shape comparison vacuously pass.
    """
    if isinstance(value, dict):
        return {key: shape_of(val) for key, val in sorted(value.items())}
    if isinstance(value, list):
        if not value:
            return ["<empty>"]
        return [_merged(value), f"... {len(value)} items"]
    if value is None:
        return "null"
    return type(value).__name__


def annotations_in(shape: Any, path: str = "") -> list[str]:
    """Every note `shape_of` attached, as report lines -- partial presence and mixed types.

    These are NOT differences, and printing them among the differences would be the cry-wolf
    failure `_PAGINATION_ENVELOPE_KEYS` exists to avoid, one layer up. They are what the operator
    needs to read a clean run honestly: "the shape matched" plus "and `min_order_amount` was on 63
    of the 89 rows" is a true statement about the venue, where either half alone is not.
    """
    where = path or "<root>"
    if isinstance(shape, str):
        if shape == _bare(shape):
            return []
        return [f"  note: {where}  mixed types across elements: {shape}"]
    if isinstance(shape, dict):
        notes: list[str] = []
        for key, val in shape.items():
            child = f"{path}.{_bare(key)}" if path else _bare(key)
            if key != _bare(key):
                notes.append(f"  note: {child}  present on {key.split(' (', 1)[1][:-1]} elements")
            notes.extend(annotations_in(val, child))
        return notes
    if isinstance(shape, list) and shape:
        return annotations_in(shape[0], f"{path}[]")
    return []


def fixture_shape(path: Path) -> Any:
    """The shape of a committed fixture, as a PROBE could ever observe it.

    Two normalizations, and both exist because a probe's response has already been through the
    transport by the time this script sees it, while the fixture on disk has not:

    1. **The pagination envelope is dropped.** `_paginate` resolves `next`/`previous` and hands
       back `{"results": [...]}`, so comparing a post-pagination aggregate against a raw single
       page reports both keys missing from a venue that sent them -- see
       `_PAGINATION_ENVELOPE_KEYS`. Only a payload that actually has `results` is normalized: an
       order object is left alone, so a genuine `next` field on some future endpoint would still
       be compared rather than silently eaten.
    2. **Numbers are decoded with `parse_float=Decimal`,** matching `RobinhoodTransport._request`
       exactly. Since #217 the fixtures quote nothing that the venue sends unquoted, so a plain
       `json.loads` here would type every money field `float` while the live side reports
       `Decimal` -- one `TYPE DIFFERS` line per money field per probe, which is the same
       cry-wolf failure as the envelope, one layer down.
    """
    payload = json.loads(path.read_text(), parse_float=Decimal)
    if isinstance(payload, dict) and "results" in payload:
        payload = {k: v for k, v in payload.items() if k not in _PAGINATION_ENVELOPE_KEYS}
    return shape_of(payload)


def compare_shapes(live: Any, fixture: Any, path: str = "") -> list[str]:
    """Return one human-readable line per structural difference, recursing into dicts.

    Differences are reported in both directions on purpose. A key the fixture invented and the
    venue does not send is the dangerous one -- that is a field the adapter may already be
    reading -- but a key the venue sends and the fixture omits is how a capability gets missed,
    and `fees_usd` (issue #197) is exactly that shape of miss.

    Both sides are compared through `_bare`, which drops the ` (...)` notes `shape_of` attaches.
    That is the decision #230 turns on, and it cuts two ways deliberately:

    * A key the venue sends on only SOME elements is compared as an ordinary key. A fixture is one
      representative object and cannot say "63 of 89", so the fixture is expected to carry the
      UNION of the keys the venue can send -- `rh_trading_pairs.json`'s single row is BTC-USD, and
      BTC-USD is sent `min_order_amount`. A fixture that carries it matches cleanly; a fixture
      that omits it is reported `NEW AT VENUE`, which is precisely the #218 regression this
      restores the ability to catch. The `63/89` itself reaches the operator through
      `annotations_in`, as information rather than as a difference.
    * A key the venue types INCONSISTENTLY across elements is not bare-equal to either of its
      types, so `Decimal|str` against a fixture's `str` still reports `TYPE DIFFERS` -- with both
      tallies in the message, because at this venue that is a finding and not a formatting detail.
    """
    diffs: list[str] = []
    if isinstance(fixture, dict) and isinstance(live, dict):
        live_by_key = {_bare(key): val for key, val in live.items()}
        fixture_by_key = {_bare(key): val for key, val in fixture.items()}
        for key in sorted(set(fixture_by_key) | set(live_by_key)):
            where = f"{path}.{key}" if path else key
            if key not in live_by_key:
                diffs.append(f"  MISSING AT VENUE  {where}  (fixture has {fixture_by_key[key]!r})")
            elif key not in fixture_by_key:
                diffs.append(f"  NEW AT VENUE      {where}  (venue sends {live_by_key[key]!r})")
            else:
                diffs.extend(compare_shapes(live_by_key[key], fixture_by_key[key], where))
        return diffs
    if isinstance(fixture, list) and isinstance(live, list):
        if fixture and live and fixture[0] != "<empty>" and live[0] != "<empty>":
            diffs.extend(compare_shapes(live[0], fixture[0], f"{path}[]"))
        return diffs
    if _bare(live) != _bare(fixture):
        diffs.append(f"  TYPE DIFFERS      {path or '<root>'}  fixture={fixture!r} venue={live!r}")
    return diffs


def load_credentials(env_path: Path) -> tuple[str, str]:
    """Read the credential from `.env`, failing with instructions rather than a stack trace.

    The private key's length is checked here because the overwhelmingly likely operator error --
    pasting the base64 PUBLIC key that Robinhood's credential page asked for -- yields a 401 that
    looks exactly like a signing bug, and chasing that costs far more than this check.
    """
    values = dotenv_values(env_path)
    api_key = (values.get("ROBINHOOD_API_KEY") or "").strip()
    private_key = (values.get("ROBINHOOD_PRIVATE_KEY") or "").strip()

    missing = [
        name
        for name, val in (("ROBINHOOD_API_KEY", api_key), ("ROBINHOOD_PRIVATE_KEY", private_key))
        if not val
    ]
    if missing:
        raise SystemExit(
            f"missing {' and '.join(missing)} in {env_path}.\n\n"
            "Robinhood signs EVERY request, including read-only ones, so an API key alone "
            "cannot make a single call.\n"
            "ROBINHOOD_PRIVATE_KEY is the base64 of the raw 32-byte Ed25519 seed generated "
            "locally -- not the base64 public key pasted into Robinhood's credential page.\n"
            "See packages/keel-broker-robinhood/README.md for the pynacl snippet."
        )
    if len(private_key) != _SEED_B64_LEN:
        raise SystemExit(
            f"ROBINHOOD_PRIVATE_KEY is {len(private_key)} characters; a base64-encoded 32-byte "
            f"Ed25519 seed is {_SEED_B64_LEN}.\n"
            "This is almost always the PUBLIC key, a PEM, or a hex string. Sending it would "
            "produce a 401 indistinguishable from a signing bug."
        )
    return api_key, private_key


def run_probes(transport: _ReadOnly, symbol: str) -> dict[str, Any]:
    """Run every read-only probe, recording a per-probe result rather than aborting on the first.

    One probe failing is a finding about that endpoint, not a reason to learn nothing about the
    other four -- and the first failure is usually the least informative, since a bad credential
    fails all of them identically.
    """
    results: dict[str, Any] = {}
    calls = {
        "accounts": lambda: transport.get_accounts(),
        "trading_pairs": lambda: transport.get_trading_pairs(),
        "best_bid_ask": lambda: transport.get_best_bid_ask(symbol),
        "estimated_price": lambda: transport.get_estimated_price(symbol, "ask", "0.001"),
        "holdings": lambda: transport.get_holdings(),
        "orders": lambda: transport.get_orders(),
    }
    for name, call in calls.items():
        try:
            results[name] = {"ok": True, "shape": shape_of(call())}
        except ReadOnlyViolation:
            raise
        except Exception as exc:  # noqa: BLE001 -- a probe report wants the failure, not a trace
            results[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return results


def report(results: dict[str, Any], as_json: bool) -> int:
    """Print the shape report and return the process exit code."""
    if as_json:
        print(json.dumps(results, indent=2, sort_keys=True))
        return 0 if all(r["ok"] for r in results.values()) else 1

    failures = 0
    for name, fixture_name in PROBES:
        result = results[name]
        print(f"\n=== {name} ===")
        if not result["ok"]:
            print(f"  FAILED  {result['error']}")
            failures += 1
            continue

        diffs = compare_shapes(result["shape"], fixture_shape(FIXTURES / fixture_name))
        if not diffs:
            print(f"  shape matches {fixture_name}")
        else:
            print(f"  {len(diffs)} difference(s) vs {fixture_name}:")
            for line in diffs:
                print(line)
            failures += 1
        # Printed on a clean probe too, and after the differences rather than among them: a key on
        # 63 of 89 rows is a true fact about the venue, not a fault, and a match is only honestly
        # readable next to it.
        for note in annotations_in(result["shape"]):
            print(note)

    print(
        f"\n{len(PROBES) - failures}/{len(PROBES)} probes matched their fixture."
        if failures
        else f"\nall {len(PROBES)} probes matched their fixtures."
    )
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--symbol", default=PROBE_SYMBOL, help=f"default {PROBE_SYMBOL}")
    parser.add_argument("--env", type=Path, default=REPO_ROOT / ".env")
    args = parser.parse_args(argv)

    api_key, private_key = load_credentials(args.env)

    # Imported here, not at module scope: `argparse --help` and the credential error above must
    # work in an environment where the optional adapter is not installed.
    from keel_broker_robinhood.transport import RobinhoodTransport

    transport = _ReadOnly(RobinhoodTransport(api_key=api_key, private_key_b64=private_key))
    results = run_probes(transport, args.symbol)

    print(f"issued {len(transport.calls)} request(s), all GET:")
    for method, path in transport.calls:
        print(f"  {method} {path}")

    return report(results, args.json)


if __name__ == "__main__":
    sys.exit(main())
