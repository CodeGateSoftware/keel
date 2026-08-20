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
# SIGNING IS NOT DONE HERE. This script produces an UNSIGNED bundle on purpose: signing needs an
# Apple Developer ID certificate in a keychain, which exists only in the release workflow's
# protected environment. Gatekeeper will refuse this output on another machine, and that is the
# correct outcome for an artifact nobody signed.
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

# `hdiutil` with UDZO: compressed, read-only, and the format `notarytool` accepts for stapling.
DMG="$OUT_DIR/keel-$VERSION-$(uname -m).dmg"
rm -f "$DMG"
hdiutil create -quiet -srcfolder "$APP" -volname "keel $VERSION" -format UDZO "$DMG"
echo "built $DMG"
