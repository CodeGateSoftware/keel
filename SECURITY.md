# Security Policy

## Reporting a vulnerability

**Do not open a public issue for anything you believe is a security flaw.** Use GitHub's
**private vulnerability reporting** — the **Report a vulnerability** button under this
repository's *Security* tab. It is enabled, and it reaches the maintainer without disclosing
the finding to anyone else. There is no separate email address; the button is the channel,
precisely because it cannot be accidentally CC'd.

Please include what you found, how you found it, and — if you have it — a reproduction. If
you have already run it against live funds, say so; that changes the urgency, not the
welcome.

## What counts as a vulnerability

keel holds exchange API credentials and places live orders, so the defining class of defect
is anything that can make it trade what it should not, or expose what it holds:

- **A rail that can be bypassed.** The guards in `keel/execution/guards.py` are the product.
  A way around the halal allowlist, the spend or exposure caps, the drawdown breakers or the
  kill-switch is a **security issue, not merely a bug** — whether it is a race, a parsing
  quirk, or a default that fails open. If a rail can be made not to run, report it here.
- **Exposure of credentials or data**: anything that can leak API keys, secrets from
  configuration, or database contents to a place they should not reach.
- **Corruption of the audit trail**: the attestation and decision records are what make the
  engine auditable; anything that can rewrite or forge them silently is in scope.

## Out of scope

Please use a regular (public) issue, or no issue at all, for:

- **Strategy performance and market losses.** A rule that loses money is a bad rule, not a
  vulnerability; the backtest and experiments docs are where that conversation lives.
- **Your own key handling.** A key you pasted into a world-readable file, committed, or sent
  to the wrong place is a compromise of your handling, not of keel.
- The exchange's own outages, or losses caused by market movement.

## What to expect

This project has **one maintainer**, and the timings below are sized to stay honest rather
than to sound corporate:

- **Acknowledge within 3 days** of a private report.
- A severity call and a plan (fix, or mitigation with a timeline) **within 14 days**.
- Fixes are released as soon as they are verified; disclosure is coordinated with the
  reporter, and credit is given unless you ask not to be named.
