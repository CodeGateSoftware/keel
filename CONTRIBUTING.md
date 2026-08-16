# Contributing to keel

## Governance: rulings vs. machinery

**keel is not a fatwa engine. It is an enforcement engine for a ruling you supply.**

keel never derives a Shariah classification from market data. Every classification is a human
input — recorded with a source and an attributed name (`keel assets attest --source ...
--attested-by ...`) — and keel's job is to enforce what was recorded, deterministically,
rejecting anything unattested. That is what lets one codebase serve operators of different
schools and jurisdictions: the ruling lives in the attestation, not in the code.

For pull requests, that splits contributions into two kinds with different bars:

- **A PR that changes a *default classification*** — what sector a well-known token falls into,
  whether a wrapper counts as `spot`, which backings are admissible — is a ruling arriving in
  code's clothing. It would apply one contributor's fiqh to every operator who upgrades. Such a
  PR must cite a **source** (a scholar, a council, a standard) and is **discussed** before it
  merges; a classification with no source behind it is not mergeable, however confident the
  author.
- **A PR that changes the *mechanism*** — how attestations are recorded, checked, or audited;
  how the screen or the rails run; anything where the ruling stays in the operator's data — is
  **ordinary engineering** and needs only ordinary review.

**If you disagree with a classification, do not litigate it here.** The project does not
adjudicate fiqh and will not become a court with a merge button. Attest your own ruling
locally — `keel assets attest` writes to *your* database, with your source and your name on
it — and run the enforcement engine under it. The disagreement then costs nobody anything:
upstream stays neutral, your deployment follows your ruling, and the audit trail records
exactly who said what.

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
