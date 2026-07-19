# keel — operator runbook

Procedures a **human** must perform, because the system cannot.

This file has a deliberately narrow scope: **compliance obligations that no rail can enforce.** Every
rail in `keel/execution/guards.py` inspects an `OrderIntent` — so anything that isn't an order is
invisible to all of them, by construction. Those obligations live here instead.

Things enforced *in code* do **not** belong in this file. Rail 14, for example, already refuses live BUYs
until a subscription is attested (`keel subscription attest`); it needs no runbook entry because it fails
closed on its own. If an item here ever becomes machine-verifiable, move it into the code and delete it
from this file.

Design home: `docs/superpowers/specs/2026-07-16-keel-broker-abstraction-design.md` §3.1
(`CompliancePolicy` account-level obligations).

---

## Pre-live checklist

Run through this **before arming the agent for live trading**, and re-check after any change to the
Coinbase account.

### 1. ⛔ Disable interest / rewards on idle balances — **required**

**Why.** Coinbase pays **USDC Rewards** on idle USDC balances. Rail 13 routes buys through USDC, so the
account holds a USDC quote balance between trades. Interest accruing on that balance is **riba**
(KB §56.3, grounded in §28.1 / §30.1) — and it accrues **with no order placed**, so no rail sees it.
This is not a trading decision the system can veto; it is an account setting only you can change.

**How to verify** (manual — see the limitation below):

1. Open the **Coinbase consumer app or web account** (not Advanced Trade).
2. Find **USDC Rewards** — typically under *Assets → USDC*, or *Settings → Rewards / Earn*.
3. Confirm it is **off / not enrolled**. Opt out if it is on.
4. Check any other yield, staking, earn or lending feature on the same account is likewise off.
5. Re-check after Coinbase product changes — enrolment has historically been enabled by default in some
   regions.

> ⚠️ **This cannot currently be automated, and should not be faked.** USDC Rewards is a consumer-account
> product; the **Advanced Trade API does not expose enrolment status** (no `reward`/`interest`/`earn`/
> `yield` endpoint exists in the SDK), and the broker port surfaces only capabilities, candles, balances,
> preview, place and fee-summary. A `usdc_rewards_disabled: true` flag in `config.yaml` would record
> *what you asserted*, not *what is true* — and a green check that verifies nothing is worse than an
> honest manual step, because it turns an open risk into a false assurance. If Coinbase ever exposes the
> state, promote this to a startup assertion and remove it from here.

### 2. Zakat estimate — **report-only, not blocking**

A zakat estimate (~2.5% of holdings' market value per lunar year) is a **positive** obligation, unlike
item 1's prohibition, and it is informational: keel reports, you decide and discharge it. Tracked at
KB §33.1. No pre-live action; noted here so the account-level obligation set is complete in one place.

---

## Adding to this file

An item belongs here only if **all** of these hold:

- it is a compliance obligation (not an operational preference), **and**
- no rail can enforce it — there is no `OrderIntent` to inspect, **and**
- it is not machine-verifiable today.

If the third stops being true, implement the check and delete the entry. If the second stops being true,
it is a rail, and it belongs in `guards.py`.
