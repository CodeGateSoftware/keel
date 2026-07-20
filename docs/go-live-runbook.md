# Go-live runbook — the first supervised live order

**Purpose:** place **one** real order against Coinbase to prove `place_order` works end to end.
This path has **never** been exercised. It moves real money and is **irreversible**.

⚠️ **This is a PLUMBING TEST, not a strategy test.** Its only question is *"does the order pipe
work?"* The result is **not** evidence the strategy is any good — the Turtle fails its own
promotion gate (31 backtest trades against a floor of 100; MinBTL puts the honest requirement at
~125). Do not read a successful order as a green light to trade for real.

---

## 0. Reality check — read this before touching anything

The safety architecture deliberately makes an autonomous live order **hard**, and on v0.1.0 there
is **no first-class "place a test order" command**. Two facts drive the whole procedure:

1. **Confirm mode places nothing.** The agent loop runs the executor with no confirmation
   callback, so `mode: confirm` previews and then *fails closed*. The only mode that actually
   places is **`bypass`**, which requires an armed token *and* a passphrase gate.
2. **No rule can currently reach `live` status through the gate** (the Turtle fails the trade
   floor; DCA cannot be backtested). The agent only trades `live` rules.

Because of (1) and (2), this runbook uses **two deliberate out-of-CLI steps** (setting the authz
passphrase; inserting a `live` rule directly). They are called out explicitly. If that feels
uncomfortable, that is the correct instinct — see "Recommended: build the affordances first" at the
end.

**Vehicle: a tiny DCA buy.** DCA is a fixed-cadence market buy — predictable, so you get exactly
one order when you want it, rather than waiting weeks for a Turtle breakout. Set its budget to a
few dollars.

### Preconditions (all of these, before you start)

- [ ] A **non-iCloud working directory** (e.g. `~/keel-live`, *not* `~/Documents/...`). A live key
      must never sync to the cloud.
- [ ] The **installed v0.1.0 release**, verified: `keel --version` shows
      `keel 0.1.0+<hash> [release]` — **not** `DIRTY`, **not** `[checkout]`.
- [ ] A **Trade-enabled** CDP key from **cloud.coinbase.com/access/api**, loaded via the JSON→.env
      converter, and the read path already proven (`keel -v fetch --products BTC-USD --years 1`
      succeeds).
- [ ] **A few dollars of settled USDC** in the Coinbase account (rail 13 will veto a BUY that is
      not covered by settled USDC — it never draws from a bank/ACH source).
- [ ] You are present and watching. This is supervised, not scheduled.

---

## 1. Set up the working directory

```bash
mkdir -p ~/keel-live && cd ~/keel-live
# copy the JSON key file here, then:
python3 -c "
import json
d = json.load(open('cdp_api_key.json'))
open('.env','w').write(f'CDP_API_KEY={d[\"name\"]}\nCDP_API_SECRET=\"{d[\"privateKey\"]}\"\n')
"
chmod 600 .env
# copy the reference config:
cp /path/to/repo/config.yaml ./config.yaml
keel --version                 # confirm [release], not DIRTY
keel -v fetch --products BTC-USD --years 1   # confirm read path (no orders)
```

## 2. Configure a tiny, capped, bypass-mode setup

Edit `~/keel-live/config.yaml`:

```yaml
auto_trade:
  mode: bypass          # confirm places nothing; bypass is the only mode that trades
caps:
  max_per_order_usd: 15 # a hard ceiling well below anything that matters
  max_per_day_usd: 15
  max_exposure_usd: 15
  max_per_asset_pct: 1
```

Leave everything else as shipped. The tiny caps are a second belt on top of the DCA budget: even a
mistake cannot place more than $15.

## 3. Two deliberate out-of-CLI steps (v0.1.0 has no command for these)

**(a) Set the dangerous-action passphrase** — required to arm bypass. Run from `~/keel-live`:

```bash
python3 -c "from keel.security import authz; authz.set_passphrase('CHOOSE-A-PASSPHRASE')"
ls -l authz.json      # it wrote the gate state here
```

**(b) Insert a tiny DCA rule directly at `live` status** — no rule can earn `live` through the gate
today, so for a supervised test we place one there deliberately:

```bash
python3 -c "
from keel.data.db import connect, migrate
from keel.data.repository import Repository
c = connect('keel.db'); migrate(c); r = Repository(c)
r.insert_rule('dca', {'product_id': 'BTC-USD', 'cadence_days': 7, 'budget_usd': '5'}, status='live')
print('live rules:', [(x['id'], x['kind'], x['status']) for x in r.get_rules('live')])
"
```

`budget_usd: '5'` → a ~$5 market buy. (Check Coinbase's BTC-USD minimum; $5 clears it comfortably.)

## 4. Clear the rails that gate a live BUY

A DCA buy must pass every rail. These need one-time attestations:

```bash
# rail 14 — monthly allowance (DCA is NOT exempt; unattested => allowance 0 => vetoed):
keel subscription attest --venue coinbase --tier <your-tier>
keel subscription set --monthly-allowance 100      # a small positive allowance

# rail 17 — withdrawal capability (fails closed without a fresh attestation):
keel withdrawals attest --enabled

# kill-switch must be OFF (fail-closed default is ON):
keel resume            # or ensure it was never engaged

# feed must be fresh — the fetch in step 1 set last_feed_ts; if stale, re-run it.
```

Rail 13 (USDC funding) needs no command — it reads your live settled USDC balance. Make sure a few
dollars are there.

## 5. Arm, then place ONE order

```bash
# Arm the in-process bypass token (short TTL). Uses the passphrase from step 3(a):
keel arm-bypass --passphrase 'CHOOSE-A-PASSPHRASE'

# Place exactly ONE cycle -- NOT --loop. Watch it:
keel -v agent --bypass --passphrase 'CHOOSE-A-PASSPHRASE'
```

Run `agent` **once** (no `--loop`). One cycle = at most one DCA buy. Watch stdout and
`logs/keel.log`.

## 6. Verify what happened

```bash
tail -30 logs/keel.log          # look for the executor placing + a fill
keel pnl                        # the DB now holds a real position
```

Then **check Coinbase directly** — the order should appear in your account with a ~$5 BTC buy.
That round trip — keel → Coinbase → a real fill you can see in the app — is the entire point.

Expected log signature: a `guards` pass (no vetoes), a preview, a `place_order`, and a fill. A veto
means a rail stopped it — read which one and fix that precondition; **a veto is the rails working,
not a failure of the test.**

## 7. Stand down (do this immediately after)

```bash
keel disarm-bypass              # ungated, fail-safe -- revokes the bypass token
keel kill                       # engage the kill-switch so nothing can trade
```

Then reverse the test setup:

```bash
# put mode back to paper in config.yaml:  auto_trade.mode: paper
# remove the live DCA rule so it cannot fire again:
python3 -c "
from keel.data.db import connect
from keel.data.repository import Repository
r = Repository(connect('keel.db'))
for x in r.get_rules('live'):
    r.update_rule_status(x['id'], 'disabled')
print('all live rules disabled')
"
```

## 8. What this proved — and did not

**Proved:** the credential → guards → executor → `place_order` → real fill path works. That is the
one thing never before tested.

**Did NOT prove:** anything about the strategy. The Turtle still fails its promotion gate. Do not
scale up, do not enable `--loop`, do not raise caps, on the strength of a working pipe.

---

## Emergency stop (any time)

```bash
keel kill               # engages the kill-switch; run_once refuses to trade (fail-closed)
keel disarm-bypass      # revokes the bypass token; ungated on purpose
```

The kill-switch is checked first, before anything else, and defaults to ON — so if in doubt,
`keel kill` and the next cycle does nothing.

---

## Recommended: build the affordances first

This runbook works, but steps 3(a) and 3(b) reach around the CLI — setting the authz passphrase and
inserting a `live` rule by hand. For a money operation that is more fragile than it should be. Two
small, safe additions would remove the ad-hoc Python:

1. **`keel set-passphrase`** — a first-class command for the dangerous-action gate. It is missing
   today and is needed regardless of this test.
2. **A supervised single-order affordance** — e.g. a `keel place-test-order` that is authz-gated,
   size-capped, confirm-prompted, and runs the full rail stack, so the first live order does not
   require hand-inserting a `live` rule.

If you would rather do this cleanly, ask for those two first; they are a small PR and make the live
test a handful of CLI commands with no manual DB or security-file editing.
