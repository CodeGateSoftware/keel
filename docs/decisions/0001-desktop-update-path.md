# 0001 — The desktop product updates per-release installer (no self-update)

Date: 2026-08-21 · Issue: #439 (PRD §4, D6 — "Desktop distribution") · Status: decided

## Context

Since v0.11 keel ships a packaged desktop product (macOS `.app`/`.dmg`, Windows `.zip`; built by
the release workflow, documented in `docs/desktop-install.md`). A frozen bundle is how the desktop
user runs keel — and `keel update` hard-refuses that layout, twice over:

- `deployment_layout_refusal` (`keel/commands/update.py`) requires the running package to resolve
  from `<launch>/.venv/…/site-packages/keel`. A frozen bundle has no `site-packages` in that shape.
- `_uv_install` requires `uv` on PATH. A desktop user has no `uv`, and never will.

The question (#439): make the updater bundle-aware, or scope the desktop product to per-release
installs. A product decision before a code change.

## Options

**A — Per-release installer.** The desktop product has no self-update. Each release ships a new
signed installer; the app tells the user when a newer version exists (a read-only check), and
updating means downloading and running the new installer. Costs the user a re-download; costs us
nothing beyond a version check.

**B — Bundle-aware self-update.** The bundle verifies a signed manifest, replaces the `.app`/`.exe`
in place, handles macOS's restrictions on an app replacing itself while running, and re-notarises
per release. The existing `os.execv` relaunch behaves less cleanly for a frozen GUI binary than
for a CLI script.

## Decision

**A. The installer is the update path.** Desktop bundles NEVER self-update; a packaged install
updates by downloading the new installer from
[the releases page](https://github.com/CodeGateSoftware/keel/releases/latest) and running it
(it never touches the deployment — config, databases, credentials, logs).

The issue's own reasoning, which is the reasoning:

- B buys convenience and costs an update channel that must itself be secured — a signing key the
  running app holds or verifies, a manifest format, an in-place replace path, re-notarisation —
  every piece of it a new attack surface on a tool that moves real money.
- For that tool, a user **deliberately downloading a signed installer** is the better trust
  posture anyway: the update arrives through the same channel as the original install, with the
  same user intent, rather than through code the previous build fetched and ran.
- The `os.execv` relaunch is poor for frozen GUI binaries (a replaced process is not a clean
  concept for an app the user launched from Finder).

**Standing rules this decision fixes:**

1. The `uv`-venv `keel update` path is unchanged and remains the answer for terminal deployments.
   Nothing about this decision relaxes its layout refusals, its typed TTY gate, or its procedure.
2. Desktop bundles never self-update, and `keel update` on a packaged install never offers one:
   `plan_update` short-circuits to the single packaged refusal, `keel update [--check]` reports
   the version comparison and where to download (`packaged_check_lines`), and an unreachable
   check is a calm "could not check", exit 0 — never an error state.
3. Every refusal a packaged user can see names the desktop path (the download and
   `docs/desktop-install.md`), never `uv` or `site-packages` vocabulary.

## Consequences

- **Re-download is the update cost** for desktop users. Accepted: it is money for a click, and it
  is the same ceremony as the first install.
- **The check is read-only and opt-in.** The version comparison queries the public releases API
  (no auth), never at startup, never on a schedule — only when a human asks. Network failure is
  calm, not an error.
- **No `update.available` notification event.** The #444 taxonomy is derived purely from local
  state (doctor's findings, cycle facts) and its default-off contract promises zero network; an
  update check requires a GitHub round-trip that would put network in the trading loop and burn
  the unauthenticated 60-requests/hour budget at cycle cadence. The check stays a human-asked
  surface (`keel update --check` / the console's update view). Revisit only if the desktop app
  grows its own non-loop check surface with a cached, opt-in result.
- **If we ever revisit B**, it is a new decision record superseding this one — not an
  incremental feature.
