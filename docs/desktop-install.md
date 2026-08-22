# Installing the desktop app — and why your computer will warn you about it

If you downloaded keel for macOS or Windows and your computer refused to open it, nothing is
broken. This page explains exactly what happened, why, and what to do about it.

## Would you rather not deal with this at all?

There is a path with no warning on any platform, because nothing is downloaded as an
application: the terminal installer. It fetches the published wheels directly into a per-user
venv at `~/.keel`, and no operating system objects to that.

```
curl -fsSL https://raw.githubusercontent.com/CodeGateSoftware/keel/main/scripts/install.sh | bash
```

You do not have to trust that line blind: the script is
[`scripts/install.sh`](../scripts/install.sh) in this repository, written to be read — every step
prints what it is about to do before it does it, it runs no privileged commands, and it verifies
itself with `keel versions` before claiming success. It needs a terminal and Python 3.11 or
later. To build from a source checkout instead, see the README's **"Try it in five minutes"**.

Both are exactly the friction the desktop app exists to remove, so this is not the recommendation
for everyone. But if you already have a terminal, either is the shorter road and the rest of this
page does not apply to you.

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
