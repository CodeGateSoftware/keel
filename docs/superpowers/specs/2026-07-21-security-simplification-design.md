# Security simplification — design

**Date:** 2026-07-21
**Status:** Approved design (pending user review)
**Workstream:** A of three. Items 1 and 2 of the 2026-07-21 requirements.

## Context

`keel` grew two local security mechanisms from the original spec §14:

- an **encrypted secrets vault** (`keel/security/secrets.py`) — AES-GCM over a scrypt-derived key,
  holding CDP credentials in `secrets.enc`;
- a **dangerous-action passphrase gate** (`keel/security/authz.py`) — scrypt-hashed passphrase with
  rate limiting, gating `{arm_bypass, raise_caps, disable_killswitch, unlock_vault}`.

Both are being removed. The app will be **server-hosted with real user authentication** in the
future; between here and there, a local single-user vault and passphrase buy little. The honest
assessment was already in `authz.py`'s own docstring: it "does not stop an attacker who already
holds the OS account."

**Two findings materially shrink this work:**

1. **The vault is already dead code.** `data/cb_client.py` and `cli.py` both load credentials via
   `config.load_secrets()` from a git-ignored `.env`. The vault is reachable only through
   `migrate_from_env`. Removing it deletes a *competing* credential path; it does not change how
   credentials are actually loaded today. (PR #115, which would have wired it up, was closed.)
2. **PR #117 (merged) already did half of item 2.** Confirm mode now places orders via an
   interactive prompt with no passphrase and no bypass token, rails running first, failing closed
   on a non-TTY. What remains is the *autonomous* half.

## Goals

1. **Item 1 — `.env` only.** Delete the vault. Credentials come from a git-ignored `.env`.
2. **Item 2 — autonomy is a profile choice.** Confirmation before every order is the default. When
   the user turns **autonomous mode** on, there is no passphrase and no per-order confirmation. The
   choice is **tracked in the user's profile**, persisted in the database and read live.
3. **Delete the passphrase gate entirely**, re-gating the four halt-releasing commands with the
   same interactive confirmation #117 established for orders.

## Non-goals

- No user authentication, accounts, or sessions — those arrive with the hosted deployment.
- **No change to the 17 hard rails.** They remain un-overridable in every mode. This work changes
  *who is asked*, never *what is allowed*.
- No change to the kill-switch's fail-closed default.

## Core safety invariants (must hold after this work)

These are the properties the removed ceremony was standing in for. Each gets a test.

1. **Rails run first, always.** Guards veto before any preview or placement, in every mode.
2. **Autonomy never clears a safety halt.** `resume`, `resume-entries`, `record-flow` and
   `reset-hwm` require an interactive confirmation **even when autonomous mode is on**. "Trade
   without asking me" and "un-stick your own drawdown breaker" are different powers. A rail that
   fired because something went wrong still needs a human to clear it — otherwise a breaker can
   silently reset itself and the rail stops meaning anything.
3. **Autonomy fails closed.** An absent/unreadable/damaged profile row reads as
   `autonomous = False` — `get_profile` catches `sqlite3.Error` rather than relying on the caller
   crashing first.
3a. **Autonomy may be time-bounded.** The removed bypass-arm token was TTL-limited so a forgotten
   arm could not grant unattended trading forever. `keel autonomy on --for-hours N` restores that
   bound; the default is a durable choice (per the requirement that it be tracked as a user
   preference), and `autonomy on` says plainly when no expiry is set.
4. **Autonomy cannot be enabled non-interactively.** `keel autonomy on` requires a TTY and an
   explicit typed confirmation, so a script, cron job or piped command can never arm it.
5. **The kill-switch still short-circuits everything**, checked first, defaulting to engaged.
6. **Paper mode never places**, regardless of the profile flag.

---

## Design

### 3.1 Delete the vault (item 1)

Remove `keel/security/secrets.py` and `tests/security/test_secrets.py`. Drop the now-unused
`cryptography>=49.0.0` dependency from `pyproject.toml` (verified: used nowhere else in `keel`,
`packages`, or `tests`). `config.load_secrets()` is unchanged and remains the single credential
path.

### 3.2 Delete the passphrase gate, re-gate the four halt-releasing commands

Remove `keel/security/authz.py` and `tests/security/test_authz.py`. With both files gone the
`keel/security/` package has no remaining contents and is deleted entirely.

Of the four declared dangerous actions, `raise_caps` and `unlock_vault` were **never wired to any
command** — the gate was declared for them and never applied. ⚠️ Note the premise is narrower than
it first looks: `raise_caps` was never *enforced*, but capability-raising commands do exist and
remain ungated — `keel subscription set/attest` raises rail 14's spend allowance at runtime, and
`keel assets attest` admits an asset. Neither is a regression (both were ungated before this work),
but neither should be mistaken for "caps cannot be raised at runtime". `arm_bypass` disappears with bypass mode. That leaves
four *commands*, all one idea — re-permitting trading after a safety halt:

| command | what it releases |
|---|---|
| `resume` | the kill-switch |
| `withdrawals attest --enabled` | rail 17's entry halt (⚠️ found in review — its old justification cited the confirm gate and the bypass-arm token, both of which this work removes) |
| `resume-entries` | an armed consecutive-loss halt (rail 16) |
| `record-flow` | declares an external deposit/withdrawal so rail 11 isn't fooled |
| `reset-hwm` | rail 11's equity high-water mark, clearing a stuck drawdown halt |

Each gains `_require_interactive_confirmation(action, detail)`: prints what is about to be
released and demands an explicit typed `yes` (not bare `y` — these are rarer and heavier than an
order confirmation), and **fails closed on a non-TTY**. `--passphrase` options and `--authz-path`
are removed from the CLI.

**Rationale for parity with orders:** once placing a real money-spending order needs only a typed
confirmation, requiring a remembered secret to reset a high-water mark is ceremony without a
matching threat model. One rule — *dangerous actions need a human at a terminal; nothing needs a
stored secret* — is easier to reason about, and to audit, than two.

### 3.3 Autonomy as a live-read profile choice (item 2)

**New `profile` table** (schema 6 → 7, via the existing incremental `db.migrate` chain):

```sql
CREATE TABLE IF NOT EXISTS profile (
    id          INTEGER PRIMARY KEY CHECK (id = 1),  -- single row today
    autonomous  INTEGER NOT NULL DEFAULT 0,
    updated_ts  INTEGER NOT NULL
);
```

`CHECK (id = 1)` keeps it a single row deliberately rather than by accident; a `user_id` column is
the obvious seam when the hosted, multi-user deployment arrives.

**Repository API:**

- `get_profile() -> Profile` — returns `Profile(autonomous: bool, updated_ts: int)`. **An absent
  row returns `autonomous=False`**, so a fresh or damaged database fails closed.
- `set_autonomous(value: bool, now_ts: int) -> None` — upserts the single row.

**Read live, every cycle.** `agent` calls `repo.get_profile()` on each order decision and never
caches it, so `keel autonomy off` takes effect on the **next order**, not the next restart. This
mirrors rail 14's monthly allowance, which was deliberately moved from config to the database for
exactly this property.

**Why not `config.yaml`:** it is now a *shipped release asset* in confirm mode. Arming unattended
trading should not be a YAML line that travels between machines or gets pasted from a gist. Why
not `agent_state`: that holds operational state (kill-switch, open positions); this is a durable
user preference.

**Mode vocabulary.** `auto_trade.mode` collapses from `paper | confirm | bypass` to
**`paper | confirm`**, and is now **validated** (an unknown value raises `ConfigError` naming the
key — today it silently falls back). `bypass_arm_ttl_sec` is removed. Effective behaviour:

| `mode` | `profile.autonomous` | result |
|---|---|---|
| `paper` | *(ignored)* | simulated; places nothing |
| `confirm` | `false` *(default)* | live; prompts before every order |
| `confirm` | `true` | live; places without prompting |

One switch for *is this real money*, a separate one for *do you ask me* — rather than one enum
conflating both. The shipped `config.yaml` needs no change to stay safe: autonomy is off until
someone deliberately turns it on.

⚠️ **Breaking:** a config carrying `mode: bypass` or `bypass_arm_ttl_sec` now fails to load. That
is deliberate — failing loudly beats silently reinterpreting a config that requested autonomy.

**Removed machinery:** `Repository.arm_bypass` / `is_bypass_armed` / `disarm_bypass`, the
`agent_state` bypass token, `agent._confirm_or_bypass`, `LoopResult.bypass_refused_reason`, and
the `keel arm-bypass` / `disarm-bypass` commands and `agent --bypass` flag.

**Executor:** `mode: Literal["confirm", "bypass"]` becomes `Literal["confirm", "autonomous"]`.
`"autonomous"` places without consulting `confirm_fn`; `"confirm"` still requires an approving
`confirm_fn` and fails closed without one. Guard ordering is untouched.

**Agent:** `_confirm_or_bypass` is replaced by `_effective_mode(config, repo) -> str`, which
returns `"autonomous"` only when `config.auto_trade.mode == "confirm"` **and**
`repo.get_profile().autonomous` is true; anything else is `"confirm"`. Paper mode is handled
upstream as today.

### 3.4 CLI: `keel autonomy`

```
keel autonomy show     # prints on/off and when it was last changed
keel autonomy on       # interactive: explains the consequences, demands a typed "yes"
keel autonomy off      # ungated -- de-risking is always allowed (§5 asymmetry)
```

`on` is a dangerous action: it prints the current mode, caps and allowlist, states plainly that
orders will be placed without asking, and requires a typed `yes` on a TTY. `off` is deliberately
ungated and needs no TTY — the asymmetry principle that runs through this project is that
*reducing* risk should never be obstructed, while *increasing* it goes through a gate.

### 3.5 Rewrite the go-live runbook

`docs/go-live-runbook.md` (proposed in the unmerged PR #116) documents the old
`arm-bypass`/`--passphrase` dance, which #117 already invalidated and this work removes entirely.
It is rewritten here against the real flow: `.env` → `keel migrate`/`init` → promote a rule →
`keel agent` in confirm mode → approve one tiny supervised order → optionally `keel autonomy on`.
**PR #116 is superseded and should be closed** rather than merged.

---

## Components & interfaces

| File | Change |
|---|---|
| `keel/security/` | **deleted** (both `secrets.py` and `authz.py`; package removed) |
| `tests/security/` | **deleted** |
| `pyproject.toml` | drop `cryptography` |
| `keel/data/db.py` | `SCHEMA_VERSION = 7`; `profile` table + `_migrate_v7_profile` |
| `keel/data/repository.py` | `get_profile()`, `set_autonomous()`; remove the three bypass methods |
| `keel/types.py` | `Profile` dataclass |
| `keel/agent.py` | `_effective_mode` replaces `_confirm_or_bypass`; drop `bypass_refused_reason` |
| `keel/execution/executor.py` | mode literal `confirm`/`autonomous` |
| `keel/cli.py` | `autonomy` group; `_require_interactive_confirmation`; remove authz/bypass surface |
| `packages/keel-core/keel_core/config.py` | validate `mode ∈ {paper, confirm}`; drop `bypass_arm_ttl_sec` |
| `config.yaml`, `keel/templates/config.yaml`, `keel/templates/config.live.yaml` | drop `bypass_arm_ttl_sec` + its comment (root and dev template must stay byte-identical) |
| `tests/fixtures/config_golden_*.{json,yaml}` | drop `bypass_arm_ttl_sec` |
| `docs/go-live-runbook.md` | written fresh (supersedes #116) |
| `docs/superpowers/specs/2026-07-15-keel-autotrade-design.md` | §14 amended to record the removal and why |

## Testing

Beyond updating existing tests, each safety invariant in §"Core safety invariants" gets a test:

- a rail-vetoed order never reaches placement in **autonomous** mode (rails first);
- each of the four halt-releasing commands still demands confirmation **with `autonomous=true`**;
- absent profile row ⇒ `autonomous False`;
- `autonomy on` refuses without a TTY; `autonomy off` works without one;
- profile is re-read per cycle (flip it between two `run_once` calls, observe the change);
- `mode: paper` places nothing with `autonomous=true`;
- kill-switch engaged short-circuits before any of it;
- `mode: bypass` in a config now raises `ConfigError` naming the key;
- a v6 database migrates to v7 and gains a `profile` row default-off.

## Migration notes for the operator

- Delete any `secrets.enc` and `authz.json` — both are now ignored. (Neither is read; no automatic
  deletion is performed, since removing files the user owns is not this tool's business.)
- Remove `bypass_arm_ttl_sec` from `config.yaml`, and change `mode: bypass` to `mode: confirm`
  plus `keel autonomy on`.
- Run `keel migrate` to add the `profile` table.
