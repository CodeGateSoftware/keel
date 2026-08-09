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
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"

#: Read-only probes, in dependency order: `accounts` first because it resolves the account
#: number every later call needs, and because it is the cheapest possible proof that the
#: signature is accepted.
PROBES: tuple[tuple[str, str], ...] = (
    ("accounts", "rh_accounts.json"),
    ("trading_pairs", "rh_trading_pairs.json"),
    ("best_bid_ask", "rh_best_bid_ask.json"),
    ("estimated_price", "rh_estimated_price.json"),
    ("holdings", "rh_holdings.json"),
)

#: The symbol every marketdata probe is run against. BTC-USD is the one pair we can be confident
#: is tradable on any Robinhood Crypto account, so a failure here is a real finding rather than
#: "that account cannot trade that asset".
PROBE_SYMBOL = "BTC-USD"

#: A raw Ed25519 seed is 32 bytes, which is 44 base64 characters with padding. Checking this
#: before the first request turns the most likely operator error -- pasting the public key, or a
#: PEM, or a hex string -- into a precise message instead of a 401.
_SEED_B64_LEN = 44


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


def shape_of(value: Any) -> Any:
    """Reduce a decoded JSON value to its structure, discarding every leaf.

    A list collapses to a single-element summary rather than one entry per item: the question is
    what an element looks like, and a 90-pair `trading_pairs` response would otherwise bury the
    answer in 90 identical copies. An empty list is reported as such, since "the venue returned
    nothing" is itself a finding -- it is how an unfunded account presents, and it is what would
    make a shape comparison vacuously pass.
    """
    if isinstance(value, dict):
        return {key: shape_of(val) for key, val in sorted(value.items())}
    if isinstance(value, list):
        if not value:
            return ["<empty>"]
        return [shape_of(value[0]), f"... {len(value)} items"]
    if value is None:
        return "null"
    return type(value).__name__


def compare_shapes(live: Any, fixture: Any, path: str = "") -> list[str]:
    """Return one human-readable line per structural difference, recursing into dicts.

    Differences are reported in both directions on purpose. A key the fixture invented and the
    venue does not send is the dangerous one -- that is a field the adapter may already be
    reading -- but a key the venue sends and the fixture omits is how a capability gets missed,
    and `fees_usd` (issue #197) is exactly that shape of miss.
    """
    diffs: list[str] = []
    if isinstance(fixture, dict) and isinstance(live, dict):
        for key in sorted(set(fixture) | set(live)):
            where = f"{path}.{key}" if path else key
            if key not in live:
                diffs.append(f"  MISSING AT VENUE  {where}  (fixture has {fixture[key]!r})")
            elif key not in fixture:
                diffs.append(f"  NEW AT VENUE      {where}  (venue sends {live[key]!r})")
            else:
                diffs.extend(compare_shapes(live[key], fixture[key], where))
        return diffs
    if isinstance(fixture, list) and isinstance(live, list):
        if fixture and live and fixture[0] != "<empty>" and live[0] != "<empty>":
            diffs.extend(compare_shapes(live[0], fixture[0], f"{path}[]"))
        return diffs
    if live != fixture:
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

        fixture_path = FIXTURES / fixture_name
        fixture_shape = shape_of(json.loads(fixture_path.read_text()))
        diffs = compare_shapes(result["shape"], fixture_shape)
        if not diffs:
            print(f"  shape matches {fixture_name}")
        else:
            print(f"  {len(diffs)} difference(s) vs {fixture_name}:")
            for line in diffs:
                print(line)
            failures += 1

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
