# halal-cb — Remote Control & Notifications — Design Spec

**Date:** 2026-07-16
**Status:** ⏳ **FUTURE ENHANCEMENT — design only; implement later.** Not scheduled into Phases 1–4. Low
priority / rarely used. Build after the core agent (Phases 1–4) is functional; it will get its own plan +
issues under a future milestone when scheduled.
**Relates to:** `2026-07-15-halal-cb-autotrade-design.md` (main spec; §4 confirm/bypass, §14 security).
**Author:** Elmehdi Aitbrahim (with Claude)

---

## 1. Purpose & scope

Let the user, from the **Claude mobile app**, (a) **receive notifications** from the local halal-cb agent
(a confirm-mode trade needs approval, a bypass trade executed, a rail tripped, an error) and (b) **issue a
small, safe set of commands** back to the laptop agent (approve/reject a pending trade, kill, resume, status).

**Off by default.** The agent runs **fully local** with no network control plane unless remote control is
explicitly toggled on. When off, confirm-mode prompts appear in the local terminal and bypass trades log
locally — exactly as in the main spec.

**Non-goals:** no classic user auth (see main §14); no custom cloud relay to build/host; no laptop-off
operation (the trading daemon requires the laptop on anyway — see §5); the remote channel never carries the
truly-dangerous actions.

## 2. Key platform facts (verified 2026-07-16; confirm exact CLI/flags at build time)

- **Claude Code Remote Control** connects a laptop Claude Code session to the Claude mobile app: the phone can
  view output, **send replies/commands**, and **answer permission prompts**. Started via `claude
  --remote-control` (or `/remote-control` in a session); phone connects via the Claude app.
- **`PushNotification`** delivers a push to the phone **when Remote Control is connected** (Anthropic API only;
  needs the app signed in; can be delayed by iOS Focus / Android battery optimization; skipped while actively
  typing at the terminal).
- **Gap:** an external OS process **cannot** call `PushNotification` directly — it only fires from *inside* a
  Claude Code session. → a thin Claude Code "operator" session must mediate.
- Cloud **Routines** (persistent, laptop-off) exist but are **not needed here** (§5).

## 3. Architecture — the "operator bridge" (native, no relay, no cloud)

```
 halal-cb daemon (Python, deterministic)          Operator session (Claude Code, `--remote-control`)
   • trading loop + rails (unchanged)                • polls the IPC surface for pending items/events
   • writes pending_confirm + events  ──local IPC──▶  • on a pending item → PushNotification → 📱 phone
     to the DB                          (files/DB)     • relays the user's phone reply →
   • polls for approve/reject decision  ◀───────────    runs `halal-cb approve|reject|kill|resume <id>`
```

- **The Python daemon stays deterministic — no LLM in the trade path** (main §6.4). The operator session is a
  **thin bridge** that only *relays* notifications and *executes the restricted command set*; it **never decides
  a trade**. (An LLM may relay/summarize/flag, never decide the entry.)
- **Local IPC surface:** the daemon writes pending confirmations to the DB (`orders.status='pending_confirm'`)
  and an `events` table/row; the operator flips status via the CLI (`approve`/`reject`). The daemon **polls**
  the DB for the decision (no push-callback needed; trivial and reliable locally).
- **Toggle:** remote is "on" when an operator session is running with the phone connected; "off" otherwise
  (config `remote_control.enabled` gates whether the daemon writes to the IPC surface at all).

## 4. Components

- `remote/ipc.py` — the local IPC surface: enqueue `PendingDecision`/`Event`, read/ack decisions (DB-backed:
  reuses `orders` + a small `events` table).
- New CLI verbs (thin, deterministic; usable by the operator session or a human): `watch-remote` (the daemon
  side that surfaces pendings/events), `pending` (list), `approve <id>`, `reject <id>`, `status`, plus existing
  `kill`/`resume`.
- **Operator session runbook** (not code — a documented Claude Code session + a short standing prompt):
  "connect Remote Control; watch `halal-cb pending`; when a new item appears, `PushNotification` a one-line
  summary; on the user's phone reply, run exactly `halal-cb approve <id>` or `halal-cb reject <id>` (or
  `kill`/`resume`); never run any other command." Optionally driven by the `Monitor`/loop tooling so the session
  is idle until a pending item appears.

## 5. Why native (no relay / no cloud routines)

The trading daemon **already requires the laptop on** (it holds the key and runs the polling loop) — so
"let the laptop be off via cloud Routines" buys nothing: if the laptop is off, there is no trading to approve.
That single fact removes the need for both a **custom relay** and **cloud routines**. The local operator-bridge
is the leanest native solution. (Routines/relay remain documented fallbacks in §8 if the native path proves
insufficient in practice.)

## 6. Security model

- **Restricted remote command set only:** `status`, `pending`, **`approve`/`reject` a *pre-vetted* pending
  trade** (one that already passed every hard rail — allowlist, caps, exposure, min-move, etc.), `kill`,
  `resume`. **Never remotely:** arm bypass, raise caps, change allowlist, rotate keys, or unlock the secrets
  vault — those are **local-passphrase-only** (main §14 dangerous-action gate).
- **Bounded blast radius:** the worst a compromised phone/session can do is approve/reject **one already-vetted
  trade** (bounded by caps/allowlist) or **kill/resume** (safe). It can never enable autonomy, raise limits,
  place an un-vetted trade, or withdraw (the API key has no withdrawal scope).
- **The operator session never holds the Coinbase key** and never bypasses the daemon's rails — it only calls
  the same guarded CLI a human would.
- **Accepted residual risk:** anyone holding the user's **unlocked phone signed into Claude** can issue the
  restricted commands. Acceptable given the bounded blast radius; documented, not mitigated further.

## 7. Notification ↔ mode mapping

| Agent mode | Remote OFF (default) | Remote ON (operator bridge) |
|---|---|---|
| **Confirm** | prompts in local terminal | **approve/reject each trade from the phone** (the primary use case) |
| **Bypass** | trades autonomously, logs locally | **notified** of executed trades / rail-trips; can **kill** from the phone |

Events pushed: pending-confirm request, bypass trade executed, DD-breaker/kill-switch tripped, feed-stale /
API error. Kept concise (mobile), de-duplicated, non-spammy.

## 8. Honest caveats & fallbacks

- The operator session **consumes tokens while running** (the cost of the bridge) — it should sit idle
  (Monitor/loop) and act only on a pending item.
- Push delivery depends on the phone being connected/signed-in and can be delayed by OS notification settings.
- Approval is **poll-based** on the daemon side (no native push-callback) — fine locally.
- **Exact CLI flags / Remote-Control behavior must be re-confirmed at build time** (the platform evolves).
- **Fallbacks if native proves insufficient:** (a) a third-party actionable-push bot (Telegram/ntfy) for the
  alert with the same restricted-command CLI; (b) a custom outbound-polling relay (bearer token + end-to-end
  command signing, relay = untrusted transport) — only if warranted.

## 9. Implementation note (deferred)

Not built in Phases 1–4. When scheduled, this becomes its own milestone with issues for: `remote/ipc.py`, the
new CLI verbs, the `events` table migration, the operator runbook, and an end-to-end manual test (phone
approve/reject/kill). Config: `remote_control.enabled: false` (default) + notification preferences.

---

*Informational, not financial or religious advice.*
