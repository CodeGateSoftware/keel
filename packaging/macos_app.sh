#!/usr/bin/env bash
# Wrap a built PyInstaller bundle into a double-clickable macOS .app, then a .dmg (#438).
#
# Usage:  packaging/macos_app.sh <dist/keel> <out-dir> <version>
#
# WHY THE .app LAUNCHES `keel serve` AND NOT THE CLI.
# An app launched from Finder has **no controlling terminal at all** -- the packaging research
# rates that the single biggest technical risk in this milestone, above code signing. A bundle
# whose entry point were the CLI would open, find no tty, refuse every gated action and exit with
# nothing on screen. So the entry point serves the local web UI and opens the user's browser,
# which is the shape D2 exists to provide and the one every comparable project converged on.
#
# The console binary ships inside the same .app, so a terminal user can still run
# `keel.app/Contents/Resources/keel/keel <command>` and get the full CLI. One artifact, both
# audiences, no second build.
#
# THIS OUTPUT IS UNSIGNED, AND STAYS UNSIGNED -- as does the Windows artifact. Apple notarisation
# requires a Developer ID certificate ($99/yr); Azure Trusted Signing is ~$120/yr and, since 2024,
# does not even buy an instant SmartScreen pass. A free Apple account signs only for local
# development, and a self-signed certificate buys nothing because Gatekeeper trusts Apple-issued
# Developer IDs and nothing else. keel is open source on a small budget and has chosen not to pay
# either.
#
# So Gatekeeper WILL refuse the first open of a downloaded copy, and the release notes say so and
# say what to do about it (System Settings -> Privacy & Security -> Open Anyway). What replaces
# OS-level trust is provenance: the release workflow attaches a GitHub build attestation and a
# SHA256SUMS file, which answer "did this come from that repository, built by that workflow" --
# the same question a certificate answers, and the one an auditable project should care about
# most.
set -euo pipefail

BUNDLE_DIR="${1:?usage: macos_app.sh <dist/keel> <out-dir> <version>}"
OUT_DIR="${2:?usage: macos_app.sh <dist/keel> <out-dir> <version>}"
VERSION="${3:?usage: macos_app.sh <dist/keel> <out-dir> <version>}"

APP="$OUT_DIR/keel.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cp -R "$BUNDLE_DIR" "$APP/Contents/Resources/keel"

# `LSBackgroundOnly` keeps a Dock icon and a menu bar from appearing for what is really a local
# server: the user's attention belongs in the browser window that opens, not on an empty app.
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>keel</string>
  <key>CFBundleDisplayName</key><string>keel</string>
  <key>CFBundleIdentifier</key><string>com.codegatesoftware.keel</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>keel-launcher</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSBackgroundOnly</key><true/>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# The launcher, and every line of it is load-bearing.
cat > "$APP/Contents/MacOS/keel-launcher" <<'LAUNCHER'
#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HERE/../Resources/keel/keel"

# Finder launches an app with cwd = "/". Every keel path used to resolve against cwd, which is
# why `/keel.db` and `/config.yaml` were the blocker D1 (#434) existed to remove -- and why this
# does NOT try to set one. `keel_core.paths` resolves state to the OS app-data directory when
# there is no deployment folder, and `cd`-ing somewhere here would override that with a guess.
cd /

# Logs go beside the state, not to the system log: an operator asking "why did it not start" can
# be pointed at one file in the folder they already know about.
STATE="${KEEL_HOME:-$HOME/Library/Application Support/keel}"
mkdir -p "$STATE"
exec "$BIN" serve >>"$STATE/serve.log" 2>&1
LAUNCHER
chmod +x "$APP/Contents/MacOS/keel-launcher"

echo "built $APP"

# The note goes INSIDE the disk image, beside the app. This is the one place a Mac user actually
# looks at the moment the app refuses to open -- a page in the repository is no use to someone
# staring at "keel cannot be opened because the developer cannot be verified".
STAGE="$OUT_DIR/dmg-stage"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/keel.app"
cat > "$STAGE/READ ME FIRST.txt" <<NOTE
keel $VERSION

IF MACOS REFUSES TO OPEN THIS APP, NOTHING IS BROKEN.

keel is not code-signed. Code signing is a paid certificate from Apple that tells macOS
who built a program. It costs \$99 per year, there is no cheaper tier and no free option
for open-source projects, and a certificate we made ourselves would do nothing at all --
macOS trusts only certificates Apple issued. (Windows is not signed either, for the same
reason: that certificate costs even more.)

keel is an open-source project with essentially no budget, and that yearly cost is not
something it can commit to today. So macOS sees a program from a developer it cannot
identify, and does the right thing: it stops and asks you.

TO OPEN IT

  1. Drag keel.app to your Applications folder.
  2. Eject this disk image.
  3. Open Applications and double-click keel. macOS refuses; click Done.
  4. Open System Settings -> Privacy & Security.
  5. Scroll to the Security section. There is a line saying keel was blocked, with an
     "Open Anyway" button beside it. Click it.
  6. Authenticate, then click "Open Anyway" once more in the dialog that follows.

You only do this once. Note that on macOS Sequoia (15) and later, right-clicking the app
and choosing Open no longer works as a shortcut -- Apple removed that path deliberately.

PREFER NO WARNING AT ALL?

Install from the release wheels instead. Nothing is downloaded as an application, so
nothing objects -- but it needs a terminal and Python 3.14 or later:

  pip install --find-links . ./keel_trader-<version>-py3-none-any.whl

BEFORE YOU DO, PLEASE CHECK WHAT YOU DOWNLOADED

We would rather not just ask you to click past a security warning -- keel is a program you
may give exchange API keys to. Every release carries proof of where its files came from,
which answers the same question a certificate does: was this built from keel's own source,
by keel's own release pipeline?

  gh attestation verify <the .dmg you downloaded> --repo CodeGateSoftware/keel

A SHA256SUMS.txt file is attached to the release too. If either check fails, do not open
this app.

WHAT THIS DOES NOT MEAN

  - It does not mean the download is damaged.
  - It does not mean macOS found something wrong. Nothing was scanned and nothing was
    detected; macOS simply does not know who wrote it.
  - It does not mean the app behaves differently. A signed and an unsigned build of the
    same release are the same program.

Full explanation: https://github.com/CodeGateSoftware/keel/blob/main/docs/desktop-install.md

keel is a personal tool. It is not financial advice and not religious (Shariah) advice.
NOTE

# `hdiutil` with UDZO: compressed and read-only.
DMG="$OUT_DIR/keel-$VERSION-$(uname -m).dmg"
rm -f "$DMG"
hdiutil create -quiet -srcfolder "$STAGE" -volname "keel $VERSION" -format UDZO "$DMG"
rm -rf "$STAGE"
echo "built $DMG"
