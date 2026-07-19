"""Regression baseline: `load_config` output must not change when keel-data moves.

The monorepo spec's step 4 extracts `keel-data` out of the root package. `load_config` is the
widest behavioural surface that move touches and the most silent one: it is pure
input->value parsing over a large defaults table, so a regression does not raise -- the suite
stays green while a default quietly changes underneath live trading.

Read-only by design. Regenerate deliberately with:

    uv run python tests/baseline/regenerate_config_golden.py
"""

from __future__ import annotations

import dataclasses
import json
from decimal import Decimal

from keel_core.config import Config

from tests.baseline.config_serialize import (
    GOLDEN_DEFAULTS,
    GOLDEN_FULL,
    YAML_DEFAULTS,
    YAML_FULL,
    load_golden_config,
)


def test_fully_specified_config_matches_baseline() -> None:
    assert load_golden_config(YAML_FULL) == json.loads(GOLDEN_FULL.read_text())


def test_defaulted_config_matches_baseline() -> None:
    """The more valuable half: every value here that isn't `allowlist`/`caps` is a default."""
    assert load_golden_config(YAML_DEFAULTS) == json.loads(GOLDEN_DEFAULTS.read_text())


def test_golden_covers_every_config_field() -> None:
    """Guard against a truncated golden silently making the comparison partial.

    A new `Config` field would already fail the comparisons above, but it would fail with an
    opaque dict diff; this names the missing field. It also catches the worse case -- a golden
    file emptied or clipped to `{}`, which would otherwise still "match" nothing in particular.
    """
    expected = {f.name for f in dataclasses.fields(Config)}
    for golden in (GOLDEN_FULL, GOLDEN_DEFAULTS):
        assert set(json.loads(golden.read_text())) == expected, golden.name


def test_the_two_goldens_actually_differ() -> None:
    """Otherwise the 'full' fixture would be pinning defaults twice and proving nothing.

    If a parser regression made supplied values fall through to their defaults, the full
    golden would converge on the defaults golden -- this asserts they have not.
    """
    full = json.loads(GOLDEN_FULL.read_text())
    defaults = json.loads(GOLDEN_DEFAULTS.read_text())
    same = {k for k in full if full[k] == defaults.get(k)}
    # EVERY top-level section is set to a non-default value in the full fixture, so any
    # coincidental match means that section is no longer being proven to parse.
    assert same == set(), sorted(same)


def test_the_fail_closed_subscription_default_is_zero() -> None:
    """Called out explicitly because it is the one default that authorises live spending.

    `unsubscribed_allowance_usd` is what rail 14 permits on an unattested, suspect, lapsed or
    overdue venue. It defaults to 0 so keel ships inert. A refactor that changed this default
    to anything non-zero would hand spending authority to a venue nobody attested.

    Asserted against the LIVE parse rather than the committed golden on purpose: reading the
    golden here would only fail *after* someone regenerated it, which is one step too late.
    Exact string, not `Decimal`, because the serialisation stores exact text.
    """
    live = load_golden_config(YAML_DEFAULTS)
    assert live["subscription"]["unsubscribed_allowance_usd"] == "0"


def test_dataclass_defaults_agree_with_the_parser_defaults() -> None:
    """Every config section has TWO defaults, and nothing else makes them agree.

    A dataclass field default (`SubscriptionConfig.unsubscribed_allowance_usd = Decimal("0")`)
    is what direct construction uses -- tests, mostly. A separate parser default
    (`subscription_raw.get("unsubscribed_allowance_usd", "0")`) is what `load_config` uses.
    `load_config` never consults the dataclass default, so the two can drift apart silently:
    change one and every test that constructs the object directly keeps agreeing with itself
    while the shipped parse says something else.

    This was found by mutating the dataclass default and watching the whole baseline suite
    stay green. Comparing a defaults-only parse against a default-constructed `Config` closes
    it for every section at once.
    """
    parsed = load_golden_config(YAML_DEFAULTS)
    drifted: list[str] = []
    for f in dataclasses.fields(Config):
        if f.default_factory is not dataclasses.MISSING:
            default = f.default_factory()
        else:
            default = f.default
        if default is dataclasses.MISSING or not dataclasses.is_dataclass(default):
            continue  # required field, or a scalar the minimal document supplies itself
        for g in dataclasses.fields(default):
            constructed = _canonical_scalar(getattr(default, g.name))
            from_parse = parsed[f.name][g.name]
            if constructed != from_parse:
                drifted.append(
                    f"{f.name}.{g.name}: dataclass={constructed!r} parser={from_parse!r}"
                )
    assert drifted == [], "\n".join(drifted)


def _canonical_scalar(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return getattr(value, "value", str(value))
