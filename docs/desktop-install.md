# Installing the desktop app — and why your computer will warn you about it

If you downloaded keel for macOS or Windows and your computer refused to open it, nothing is
broken. This page explains exactly what happened, why, and what to do about it.

## The short version

**keel's desktop builds are not code-signed.** Code signing is a paid certificate from Apple or
Microsoft that tells your operating system who built a program.

**We cannot currently afford it.** Apple's certificate costs **$99 per year**, every year, and it
is the only way to remove the warning — there is no cheaper tier, no free option for open-source
projects, and a certificate we made ourselves would do nothing at all, because macOS only trusts
certificates Apple issued. keel is an open-source project with essentially no budget, and that
$99/yr is not something it can commit to today.

So your computer sees a program from a developer it cannot identify, and it does the right thing:
it stops and asks you.

## What to do

**macOS**

1. Double-click keel. macOS refuses, saying it cannot verify the developer.
2. Open **System Settings → Privacy & Security**.
3. Scroll down. There is a message about keel being blocked, with an **Open Anyway** button.
4. Click it, and confirm.

You only do this once. keel opens normally afterwards.

**Windows**

1. Run the installer. SmartScreen says "Windows protected your PC".
2. Click **More info**, then **Run anyway**.

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

## Would you rather avoid this entirely?

Install keel the way developers do, from the release wheels, and no warning appears at all:

```
pip install --find-links . ./keel_trader-<version>-py3-none-any.whl
keel versions
```

That path needs a terminal and a working Python. The desktop app exists precisely so that it does
not have to be the only option.

## If this changes

If keel ever has the budget, signing is a small change on our side — the release pipeline is
already built to accept it — and this page will be replaced by a sentence saying the builds are
signed. Until then, we would rather tell you the truth about what you are downloading than say
nothing and let your computer deliver the news.
