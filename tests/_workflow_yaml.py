"""Strict YAML parsing for GitHub workflow files -- shared by the tests that read them.

GitHub's own workflow parser REJECTS duplicate mapping keys; PyYAML's default is
last-key-wins, so a workflow edit that leaves a step's `shell:`/`run:` behind after
rewriting it still parses with `yaml.safe_load`, still passes every test that read the
file that way -- and is then rejected by GitHub's STRICT parser at dispatch time,
disabling the workflow on main outright (v0.11.0's first re-dispatch, exactly).

The discipline was born in tests/test_desktop_packaging.py for `release.yml`; any test
that parses a `.github/workflows/*.yml` into a structure must load it the same way, or
its "the workflow says X" pin is only pinned against the keys PyYAML happened to keep.
GitHub is the authority; this loader matches it.
"""

from __future__ import annotations

import yaml


class StrictLoader(yaml.SafeLoader):
    """A SafeLoader that REFUSES duplicate mapping keys, like GitHub's parser."""


def strict_load(text: str, *, source: str = "workflow") -> dict:
    """Parse workflow YAML the way GitHub does: duplicate keys are an error, not a merge.

    `source` names the file in the failure message so the assert tells the reader which
    workflow would be rejected at dispatch time, not merely that some YAML was odd.
    """
    errors: list[str] = []

    def construct_mapping(loader: yaml.Loader, node: yaml.MappingNode, deep: bool = False) -> dict:
        seen: set[str] = set()
        for key_node, _ in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in seen:
                errors.append(f"duplicate key {key!r} at line {key_node.start_mark.line + 1}")
            seen.add(key)
        return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)

    StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping)
    data = yaml.load(text, Loader=StrictLoader)
    assert not errors, f"{source} would be rejected by GitHub's parser: " + "; ".join(errors)
    return data
