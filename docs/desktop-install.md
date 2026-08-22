# Installing the desktop app — and why your computer will warn you about it

If you downloaded keel for macOS or Windows and your computer refused to open it, nothing is
broken. This page explains exactly what happened, why, and what to do about it.

## Would you rather not deal with this at all?

There is a path with no warning on any platform, because nothing is downloaded as an application:
the install-from-source route in the README's **"Try it in five minutes"**. `pip` and `uv` fetch
the published wheels directly, and no operating system objects to that.

```
pip install --find-links . ./keel_trader-<version>-py3-none-any.whl
keel versions
```

It needs a terminal and Python 3.11 or later — which is exactly the friction the desktop app
exists to remove, so this is not the recommendation for everyone. But if you already have both, it
is the shorter road and the rest of this page does not apply to you.

## The short version

**keel's desktop builds are not code-signed, on either platform.** Code signing is a paid
certificate from Apple or Microsoft that tells your operating system who built a program.

**We cannot currently afford either one.**

| | cost | what it would buy |
|---|---|---|
| Apple Developer Program | **$99/yr** | removes the macOS warning |
| Azure Trusted Signing | **~$120/yr** | *does not* remove the Windows warning by itself |

There is no cheaper tier and no free option for open-source projects on either platform, and a
certificate we made ourselves would do nothing at all — macOS trusts only certificates Apple
issued.

Windows is worth a sentence of its own: since 2024, even an expensive EV certificate no longer
buys an instant SmartScreen pass. Reputation is earned from download volume over time, so a new
certificate on a young project would leave the warning in place for a while anyway — for more
money than Apple's.

keel is an open-source project with essentially no budget. So your computer sees a program from a
developer it cannot identify, and it does the right thing: it stops and asks you.

## macOS — step by step

1. Open the downloaded `.dmg`. A window appears with **keel.app** and a **READ ME FIRST** file.
2. Drag **keel.app** into your **Applications** folder.
3. Eject the disk image (drag it to the Trash, or press ⌘E).
4. Open **Applications** and double-click **keel**. macOS refuses, saying it cannot be opened
   because the developer cannot be verified. Click **Done**.
5. Open **System Settings** → **Privacy & Security**.
6. Scroll down to the **Security** section. There is a line saying *"keel was blocked to protect
   your Mac"*, with an **Open Anyway** button beside it. Click it.
7. Authenticate with Touch ID or your password, then click **Open Anyway** once more in the
   dialog that follows.

keel opens, and your browser opens with it. **You only do this once** — every later launch is a
normal double-click.

> On macOS Sequoia (15) and later, right-clicking the app and choosing *Open* no longer works as a
> shortcut for this. Apple removed that path deliberately. System Settings is the only way.

## Windows — step by step

The download is a `.zip`, and Windows marks files that came from the internet.

1. **Before extracting**, right-click the downloaded `.zip` → **Properties**.
2. At the bottom of the **General** tab, if there is an **Unblock** checkbox, tick it and click
   **OK**. This saves you a warning on every file inside.
3. Right-click the `.zip` → **Extract All…**, and choose a folder you own — for example
   `C:\Users\<you>\keel`. Do not extract into `C:\Program Files`; keel does not need
   administrator rights and should not be given them.
4. Open the extracted folder and double-click **keel.exe**.
5. If SmartScreen appears — *"Windows protected your PC"* — click **More info**, then
   **Run anyway**.

> If you skipped step 2, you may see the SmartScreen prompt again on a later launch. Doing the
> Unblock on the `.zip` first is what avoids that.

## Please check what you downloaded first

We would rather not simply ask you to click past a security warning. That warning exists for a
good reason, and keel is a program you may give exchange API keys to.

So every keel release carries proof of where its files came from. This does not need the $99
certificate, and it answers the same question a certificate answers: **was this file built from
keel's own source, by keel's own release pipeline?**

If you have [GitHub CLI](https://cli.github.com) installed:

```
gh attestation verify <the file you downloaded> --repo CodeGateSoftware/keel
```

A `SHA256SUMS.txt` file is attached to every release as well, if you prefer to compare checksums
by hand.

**If either check fails, do not open the file.** A failing check means the file is not the one we
built, and no amount of clicking "Open Anyway" makes that safe.

## What this does not mean

- It does **not** mean the download is damaged.
- It does **not** mean your computer found something wrong with keel. Nothing was scanned and
  nothing was detected — your computer simply does not know who wrote it.
- It does **not** mean the app behaves differently once open. A signed and an unsigned build of
  the same release are the same program.

## If this changes

If keel ever has the budget, signing is a small change on our side — the release pipeline is
already built to accept it, on both platforms — and this page will be replaced by a sentence
saying the builds are signed. Until then, we would rather tell you the truth about what you are
downloading than say nothing and let your computer deliver the news.

That "already built" is now literal. The release workflow carries the signing steps for both
platforms, each gated on its own credentials: when they are absent the build ships exactly as
described above and the workflow log carries a notice saying which secrets would turn signing
on. Paying for the certificates is the whole activation — there is no code left to write. The
checklist for whoever makes that purchase is below.

## For the maintainer: turning signing on (a purchase, then ten minutes of GitHub settings)

Nothing in this section changes any code. The desktop job already references a `signing`
environment and already runs the macOS and Windows signing steps when — and only when — that
environment holds the credentials. A release dispatched before the secrets exist ships
unsigned, with a `::notice` in the run naming exactly what was skipped and why; a release
dispatched after ships signed. That is the entire switch.

### What to buy

**macOS — Apple Developer Program, $99/yr.** Notarisation requires a *Developer ID
Application* certificate, and there is no cheaper tier that Gatekeeper honours (the table at
the top of this page is the whole market). You need two things from that membership:

- the **Developer ID Application certificate**, exported from Keychain Access together with
  its private key as a `.p12`;
- an **App Store Connect API key** (appstoreconnect.apple.com → Users and Access →
  Integrating/Keys, Account Holder or Admin), which is what `notarytool` authenticates with
  from CI — it gives you a Key ID, an Issuer ID, and a one-time-download `.p8` file.

**Windows — one of:**

- an **OV code-signing certificate** as a `.pfx`, ~$70–500/yr depending on the CA (SSL.com,
  Certum, Sectigo and friends), or
- **Azure Trusted Signing**, $9.99/mo (~$120/yr). It may be restricted to US/Canada signing
  identities — verify your identity qualifies before budgeting for it.

Do **not** pay extra for EV. Since 2024 an EV certificate no longer buys an instant
SmartScreen pass — reputation accrues from download volume over time regardless of certificate
class, so EV costs more for the same warning.

### The ten minutes of GitHub settings

1. **Create the environment.** Repository *Settings → Environments → New environment*, name
   it exactly `signing` — the workflow references it by name, and a typo means the job runs
   against an unprotected no-op environment (harmless while there are no secrets, silent
   once there are).
2. **Add required reviewers** (yourself is fine) on that environment. This is the protection
   that makes the whole design safe: a dispatch pauses for approval before any leg can see
   the certificates, and the secrets are invisible to every other workflow — pull requests
   included — because environment secrets are only handed to jobs that declare the
   environment.
3. **Add the environment secrets.** macOS needs all five (the leg skips with its notice
   until then — a signed-but-un-notarised app is the worst state on macOS, so the gate is
   all-or-nothing):

   | secret | what it holds |
   |---|---|
   | `MACOS_CERT_P12_BASE64` | the Developer ID Application `.p12`, base64-encoded (`base64 -i cert.p12 \| pbcopy`) |
   | `MACOS_CERT_PASSWORD` | that `.p12`'s export password |
   | `APP_STORE_CONNECT_KEY_ID` | the Key ID of the App Store Connect API key |
   | `APP_STORE_CONNECT_ISSUER_ID` | the Issuer ID from the same page |
   | `APP_STORE_CONNECT_KEY_CONTENT` | the contents of the `.p8` private key |

   Windows needs both:

   | secret | what it holds |
   |---|---|
   | `WINDOWS_CERT_PFX_BASE64` | the OV certificate `.pfx`, base64-encoded |
   | `WINDOWS_CERT_PASSWORD` | that `.pfx`'s password |

4. **Dispatch the next release normally.** The signing steps run; the skip notices are gone.
   The ephemeral-keychain import, `codesign --options runtime`, `notarytool submit --wait`,
   `stapler`, the signed re-cut of the DMG, and `signtool` with an RFC 3161 timestamp are
   already in `.github/workflows/release.yml`, between packaging and the checksum step (so
   the sums cover the signed bytes).

One manual step remains, deliberately: **the release-notes wording and this page still say
"not code-signed" and must be updated in the same change.** The notes are composed in the
release job, which cannot see the `signing` environment's secrets — least privilege is why it
cannot know the desktop legs will sign — so there is nothing for it to auto-detect. Forgetting
this step errs in the safe direction: the notes warn about a warning that no longer appears.
Fix it anyway, and this page becomes the one sentence it always promised to be.
