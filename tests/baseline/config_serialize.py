"""Canonical, order-stable serialisation of a `Config` for golden-file comparison.

Mirrors `serialize.py`'s conventions: `Decimal` becomes a string so the comparison is exact
rather than float-tolerant, and every mapping is key-sorted so the golden is diff-stable.

This exists because the monorepo migration is about to move `keel-data` out of the root
package (spec step 4), and `load_config` is the widest behavioural surface that move touches:
it is pure input->value parsing with a large defaults table, so a regression is silent -- the
suite stays green while a default quietly changes underneath live trading.

Two configs are goldened, and the second matters more:

* FULL     -- every field set explicitly. Pins the parser.
* DEFAULTS -- the minimal legal document. Pins every default, which is precisely what a
              refactor can change without any test noticing.
"""

from __future__ import annotations

import dataclasses
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from keel_core.config import Config, load_config

FIXTURES = Path(__file__).parent.parent / "fixtures"
GOLDEN_FULL = FIXTURES / "config_golden_full.json"
GOLDEN_DEFAULTS = FIXTURES / "config_golden_defaults.json"
YAML_FULL = FIXTURES / "config_golden_full.yaml"
YAML_DEFAULTS = FIXTURES / "config_golden_defaults.yaml"


def _canonical(value: Any) -> Any:
    """Recursively convert `value` into JSON-safe, order-stable primitives.

    `Decimal` -> `str` deliberately: `float` would round-trip lossily and turn a real
    arithmetic regression into a passing test.
    """
    if isinstance(value, Decimal):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: _canonical(getattr(value, f.name))
            for f in sorted(dataclasses.fields(value), key=lambda f: f.name)
        }
    if isinstance(value, dict):
        return {str(k): _canonical(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    # Sets are SORTED, not just listed: `Config.settlement_currencies` is a frozenset, whose
    # iteration order is not stable across runs, and an unsorted golden would diff at random.
    if isinstance(value, (set, frozenset)):
        return sorted(_canonical(v) for v in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # Enums and anything else with a meaningful str() -- e.g. Granularity.
    return getattr(value, "value", str(value))


def serialize_config(config: Config) -> dict[str, Any]:
    """The canonical dict form of `config`."""
    return _canonical(config)


def load_golden_config(yaml_path: Path) -> dict[str, Any]:
    """Parse a committed fixture YAML and return its canonical serialisation."""
    return serialize_config(load_config(yaml_path))


def field_paths(value: Any, prefix: str = "") -> set[str]:
    """Every dotted leaf path in a serialised config.

    Used by the completeness guard: a new `Config` field that the golden does not cover would
    otherwise sit unpinned, and the golden would rot into a partial check nobody notices.
    """
    paths: set[str] = set()
    if isinstance(value, dict):
        for key, sub in value.items():
            paths |= field_paths(sub, f"{prefix}.{key}" if prefix else key)
    elif isinstance(value, list):
        paths.add(prefix)
    else:
        paths.add(prefix)
    return paths


def write_golden(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
