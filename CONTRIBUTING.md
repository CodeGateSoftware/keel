# Contributing to keel

## Licence: why Apache-2.0

keel is licensed under [Apache-2.0](LICENSE). That was a decision, not a default, and the
reasoning is recorded here so it can be challenged in place rather than dug out of a merged
pull request.

keel moves real money on a public exchange, so two properties of a licence matter more here
than they would for a typical library:

- **The warranty disclaimer.** Apache-2.0 disclaims warranties and liability in explicit,
  business-reviewed language. Software that places live orders needs that sentence to be as
  strong as it can be.
- **The patent grant.** Contributors and users get an explicit grant. MIT offers none.

The alternatives, and why they lost:

| option | why it lost |
| --- | --- |
| **AGPL-3.0** | Prevents a closed hosted fork, but deters exactly the contributors this project wants — many employers forbid AGPL code on work machines, and keel's audience includes people reading the source at work. |
| **MIT** | Shortest and most familiar, but no patent grant and a thinner warranty disclaimer — both matter more than brevity here. |

Apache-2.0 permits a closed hosted fork; we accept that. The compliance engine's value is the
audit trail it produces, which a hosted fork cannot hide.

All six distributions cut from this repo (`keel-trader`, `keel-core`, and the four broker
packages) declare `license = "Apache-2.0"` in their `pyproject.toml`; `tests/test_licensing.py`
fails the build if a seventh distribution appears without it.
