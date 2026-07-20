# Live credentials — the trade-enabled key

The agent reads its Coinbase CDP credentials from one of two places, in strict precedence:

1. **The encrypted vault** `secrets.enc` — when it exists. **A trade-enabled key belongs here.**
2. **`.env`** — only when no vault exists. Fine for a read-only key; the wrong risk class for a
   key that can move money, because it is plaintext on disk.

⛔ **The precedence is not a fallback.** If a vault exists but cannot be unlocked (no passphrase,
wrong passphrase, tampered file), the agent **fails closed** — it does not quietly read `.env`. A
vault that cannot be opened must not be silently downgraded to a plaintext key.

## Setting up the vault

```bash
# 1. Put the CDP key in .env (temporarily):
#      CDP_API_KEY=organizations/.../apiKeys/...
#      CDP_API_SECRET=-----BEGIN EC PRIVATE KEY----- ...
# 2. Seal it into the vault (prompts twice for a master passphrase):
keel vault init

# 3. Confirm it unlocks:
KEEL_VAULT_PASSPHRASE='...' keel vault status
#   -> "unlocks OK; credential fields present: api_key, api_secret"

# 4. THEN delete the plaintext .env. The vault is passphrase-protected; .env is not.
rm .env
```

`secrets.enc` is portable — copyable between machines (it is not machine-bound to a keychain) —
and is git-ignored. The master passphrase is never stored anywhere by keel.

## Running the agent against the vault

The passphrase is read from `KEEL_VAULT_PASSPHRASE` (for a headless loop) or an interactive
prompt. It is never taken from config or the database.

```bash
KEEL_VAULT_PASSPHRASE='...' keel agent ...
```

⚠️ `KEEL_VAULT_PASSPHRASE` in the environment is only as safe as the environment. For a supervised
run this is fine; for an unattended launchd/cron job, prefer a secret-manager indirection over a
plaintext value in the plist.

## Rotating the passphrase

```bash
keel vault rekey   # prompts for old then new; the secrets are unchanged
```

## What the vault does NOT do

- It does not print secret values. `keel vault status` reports only *which fields are present*.
- It does not gate placing an order. That is the confirm-mode default, the 15 rails, and the
  dangerous-action passphrase — all still in front of every live order. The vault only decides
  *where the API credential comes from*.
