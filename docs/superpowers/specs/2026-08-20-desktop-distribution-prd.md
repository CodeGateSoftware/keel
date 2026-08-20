# PRD — Desktop distribution: getting keel to a non-technical user

**Status:** proposal · **Date:** 2026-08-20 · **Milestone:** Desktop distribution

## The ask

> "Simplify the install to a single-click download for Windows or Mac that installs and launches the app with no commands from the end user."

## The answer in one paragraph

The packaging step is the cheap part and the last part. Four properties of keel today make a
double-clickable app impossible **regardless of which packaging tool is chosen** — CWD-relative
state, a deliberately un-overridable TTY gate, a curses-only console that cannot run on Windows,
and a first-run ceremony that includes decisions no installer can make. Fixing those is the work.
Once they are fixed, the installer is a thin wrapper, and the shape it wraps is the one every
comparable project converged on independently: **a local process serving a web UI on `localhost`,
opened in the user's own browser.**

---

## 1. Evidence: what comparable projects actually do

| project | what the user downloads | terminal required | UI |
|---|---|---|---|
| Freqtrade | pip / `setup.sh` / Docker | yes | FreqUI, browser at `127.0.0.1:8080` |
| Hummingbot | Docker Compose / source | yes | terminal client, or Dashboard at `localhost:8501` |
| Jesse | pip + Postgres + Redis | yes to start | React dashboard at `localhost:8080` |
| OctoBot | **per-OS executables** | no | **still a browser UI** — the binary starts a local server |
| 3Commas / Cryptohopper | nothing (cloud SaaS) | no | browser |
| Gunbot | portable archive, no installer | no | local web dashboard in browser |

**No established open-source trading bot ships a native windowed application.** OctoBot is the only
one shipping per-OS binaries, and even those launch a local web server reached through a browser —
a convenience wrapper around the same pattern, not a different paradigm. Its code signing could not
be verified; the only signature found was GPG on the git tag, which is source integrity, not
Authenticode or Gatekeeper notarisation.

### The counter-argument that decides it

An unsigned downloadable binary that then asks for exchange API keys **is the shape of malware
distribution.** Research surfaced a live example: a 3-star, ~5-commit repository with marketing-heavy
copy, "one-click installer for Win & Mac," requesting exchange keys plus an AI-provider key,
duplicated verbatim across two GitHub orgs. For a project whose entire proposition is auditability,
shipping something with that silhouette and no OS-level verification would be actively
counterproductive. A signed installer is therefore not optional polish — it is a precondition, and
it carries an ongoing cost (§5).

---

## 2. The four blockers, with citations

None of these is fixed by choosing PyInstaller over Briefcase over Nuitka.

### B1 — Every path is CWD-relative; a `.app` has no useful CWD

`DEFAULT_DB_PATH = "keel.db"`, `DEFAULT_CONFIG_PATH = "config.yaml"`
(`keel/commands/_common.py:45-46`); `.env` (`keel_core/config.py:956,974`); logs
(`keel/commands/activity.py:390-406`). Double-clicking a macOS bundle launches with **cwd = `/`**,
so these resolve to `/keel.db`, `/config.yaml`, `/.env` — none writable, none where the user
believes their data lives. A signed `.app` bundle is itself read-only, so it cannot hold them either.

The whole operator model — `~/keel` as one folder holding config + db + `.env` + logs, with sibling
folders for the paper/live/hourly/equities profiles — is built on CWD.

### B2 — The TTY gate is un-overridable *by design*, at 11 call sites

`_is_interactive()` is `sys.stdin.isatty()` with no env-var or flag seam, and the docstring says
why: *"any such seam would be settable from cron and would defeat every fail-closed built on it"*
(`keel/commands/_common.py:73-80`).

A GUI launcher has no stdin TTY, so **it is indistinguishable from a cron job and is refused
identically** — `resume`, `resume-entries`, `record-flow`, `reset-hwm`, `autonomy on`,
`withdrawals attest --enabled`, `keel update`, plus four console equivalents.

This is the safety model, not an obstacle to route around. A GUI needs its own gate that is
*architecturally distinct* from `_is_interactive`, never a bypass of it.

### B3 — `curses` does not exist in CPython on Windows

`keel/commands/tui.py` imports stdlib `curses`; `windows-curses` is declared nowhere in the
workspace. **`keel tui` will not run on Windows as shipped.** And `windows-curses`' own README states
it is unmaintained and seeking maintainers — a supply-chain risk for a multi-year product.

### B4 — First run is ~10 CLI invocations, hand-edited YAML, and steps outside keel entirely

Per `docs/go-live-runbook.md`: `keel init` / `migrate`, hand-edit `config.yaml`, `assets attest`,
`subscription attest`, `withdrawals attest`, promote a rule through the ladder, run one confirm-mode
cycle, verify the fill against the exchange UI, then optionally `autonomy on`.

Several are irreducible human judgement — and two happen on **someone else's website**: disabling
Coinbase USDC Rewards and Alpaca stock-lending/cash-sweep interest, because that interest is *riba*
and no rail can see it (`docs/operator-runbook.md:25-50`, `590-602`). An installer cannot automate a
compliance decision taken in a venue's dashboard.

### And the packaging-side finding that mirrors B2

**On macOS, an app launched from Finder has no controlling terminal at all.** The packaging research
rates this the single biggest technical risk in the project — above code signing, above Python 3.14.
Windows is the opposite: a console-subsystem `.exe` auto-spawns a console host, so curses genuinely
does "just appear" on double-click.

So macOS needs a launcher that opens Terminal.app and re-execs the binary — or the app must stop
needing a terminal at all. **The second is the same fix B2 and B3 need.** That convergence is what
makes the web-UI path cheaper than it looks.

---

## 3. Decision

**Build the local web UI first. The installer wraps it.**

Rationale:

1. It solves B2, B3 and the macOS-no-TTY problem *with one artifact* — a browser needs no curses,
   runs identically on Windows, and gives the human gate a real home (an in-app typed-confirmation
   modal, architecturally distinct from `_is_interactive`).
2. It is the pattern every comparable project reached independently, so users arriving from any of
   them already know the shape.
3. It reuses the existing service layer. keel's front-ends are already thin over
   `keel/commands/*` — pinned by `tests/commands/test_console_thinness.py` — which is exactly the
   seam a second front-end needs.
4. Doing the installer first produces a signed app that launches a terminal into `/`.

**Non-goals.** No native windowed GUI (Tauri/Electron + pty bridge reproduces what a browser gives
free). No cloud/hosted keel — credentials and the ledger stay on the user's machine. No removal or
weakening of any rail, attestation, or confirmation gate. No MSIX (its virtualised filesystem is a
poor fit for an app that creates and migrates a SQLite database).

---

## 4. Phases

### D1 — App-data paths *(prerequisite for everything)*
Resolve config, database, `.env` and logs to an OS-standard writable location —
`~/Library/Application Support/keel`, `%APPDATA%\keel` — with the current CWD behaviour retained as
an explicit override so the four-profile deployment keeps working unchanged. Includes a migration
path for existing deployments.

### D2 — Local web UI over the existing service layer
An HTTP server (`keel serve`) binding `127.0.0.1` by default, rendering the console's existing
menus. Must reach parity with the TUI's read surface before any write surface is exposed.

### D3 — A GUI human gate
An in-app typed-confirmation modal for capability-increasing actions, distinct from
`_is_interactive`, with the same fail-closed property: absent an authenticated interactive session,
refuse. This is the piece that most needs review — it is the only change in this PRD that touches
the safety model.

### D4 — First-run wizard
Presents the ceremony rather than skipping it: config creation, migration, credential entry (via OS
keychain — `keyring` wraps macOS Keychain and Windows Credential Manager — not plaintext `.env`),
and the attestations as explicit steps with their reasoning shown. **Defaults to paper mode.**
Hummingbot's paper trading needs no API keys at all; that is the norm worth copying given keel's
posture.

### D5 — Packaging and signing
PyInstaller `--onedir` (not `--onefile`: faster start, simpler per-binary signing, lower AV
false-positive rate), wrapped as a signed+notarised `.dmg` on macOS and an Inno Setup `setup.exe`
installing per-user to `%LOCALAPPDATA%` on Windows. A new job in `release.yml` consuming the same
four `PRODUCTION_WHEEL_PREFIXES` the updater already encodes, on a `macos-14` / `macos-15-intel` /
`windows-latest` matrix, behind a protected environment.

### D6 — Update path for a packaged install
`keel update` hard-refuses any layout that is not `<launch>/.venv/…/site-packages/keel` and requires
`uv` on PATH (`keel/commands/update.py:280-317`, `577-581`) — neither holds in a bundle. Either make
it bundle-aware or scope the desktop product to "download a new installer per release." The latter
is far cheaper and is a product decision.

---

## 5. Costs and risks

| item | cost |
|---|---|
| Apple Developer Program | $99/yr — Developer ID Application cert, plus Installer cert if shipping `.pkg` |
| Windows Authenticode | OV cert ~$70–500/yr, **or** Azure Trusted Signing at $9.99/mo |
| macOS runners | `macos-14` is arm64-only; `macos-15-intel` sunsets ~Aug 2027. Ship two DMGs, not universal2 |

**EV certificates no longer buy a SmartScreen bypass** — Microsoft ended that in 2024; OV and EV are
now equivalent for reputation, which accrues from download volume over time. EV remains necessary
only for kernel-mode drivers. Azure Trusted Signing may be restricted to US/Canada signing
identities — verify before budgeting.

**Risks.** `windows-curses` unmaintained (mitigated by D2 removing the need for it). PyInstaller's
Python 3.14 support should be confirmed with a smoke build against the pinned release. Widening the
audience for a tool whose measured result is that no rule family is net-positive, and whose fiqh
basis has had no scholarly review — mitigated by D4's paper-mode default, and worth an explicit
product decision.

---

## 6. Open questions

1. **Paper-mode default on first run** — recommended, needs a decision.
2. **Self-update or per-release installer** (D6) — cheaper is per-release.
3. **Does the desktop product ship all four deployment profiles, or one simplified profile?** The
   four-profile model is an operator concept; a first-time user needs one.
4. **Who is the desktop user?** If it is "an operator who would otherwise use the CLI," D1+D5 alone
   may suffice and D2–D4 are optional. If it is genuinely a non-technical user, all six phases are
   load-bearing. This answer changes the scope by roughly a factor of three.

---

## Appendix — one correction found while researching

`.github/workflows/release.yml:93-96` states *"The wheel carries `Requires-Python: >=3.14.4`."* Every
`pyproject.toml` in the workspace declares `>=3.11`, pinned by `tests/test_python_floor.py:46-59`.
Harmless today — the workflow still builds with the pinned 3.14.4 interpreter — but it should be
reconciled before that workflow becomes the base for a packaging job.
