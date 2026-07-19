"""Dev script: regenerate the committed `load_config` golden files.

Read-only at test time by design (mirroring `regenerate_golden.py`): the golden must never
regenerate itself as a side effect of a test run, or a behaviour change would silently
rewrite its own baseline and pass. Run this deliberately, and review the resulting diff:

    uv run python tests/baseline/regenerate_config_golden.py

A diff here is a change in how `load_config` parses or what it defaults to. That is exactly
what this golden exists to surface -- treat an unexpected one as a bug, not as noise.
"""

from __future__ import annotations

from tests.baseline.config_serialize import (
    GOLDEN_DEFAULTS,
    GOLDEN_FULL,
    YAML_DEFAULTS,
    YAML_FULL,
    load_golden_config,
    write_golden,
)


def main() -> None:
    for yaml_path, golden_path in ((YAML_FULL, GOLDEN_FULL), (YAML_DEFAULTS, GOLDEN_DEFAULTS)):
        payload = load_golden_config(yaml_path)
        write_golden(golden_path, payload)
        print(f"wrote {golden_path.relative_to(golden_path.parent.parent.parent)}")


main()
