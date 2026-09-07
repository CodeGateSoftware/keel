#!/usr/bin/env bash
# Verify a keel console reached through a tunnel, before trusting it (#648).
#
# `keel serve` binds loopback, and `http://127.0.0.1` is a secure context BY SPECIFICATION -- which
# is why the service worker and the manifest work today without anyone arranging it. Over an
# external origin that property comes from HTTPS instead, so it has to be RE-VERIFIED there rather
# than assumed from the loopback behaviour. That is the last open item on #648 and the reason
# `docs/remote-access.md` still says do not expose the console yet.
#
# ── WHAT THIS SCRIPT CAN AND CANNOT ANSWER ───────────────────────────────────────────────────────
#
# It checks everything that is visible over HTTP: the scheme, the headers, whether the Host
# allowlist still refuses a name it was not told to expect, and whether the shell and the two PWA
# assets are actually served over the external origin.
#
# It CANNOT answer `window.isSecureContext`, whether the service worker registered, or whether the
# install prompt appeared. Those are browser state, and a script that claimed them from curl would
# be reporting something it never looked at. Part 2 below is a short browser checklist for exactly
# those, and it is not optional -- it is the half of #648 that a shell cannot close.
#
# Usage:  scripts/verify-tunnel-context.sh https://keel.example.com <session-token>
#
# Exit status: 0 when every HTTP-visible check passed, 1 otherwise. The browser checklist is
# printed, never assumed.

set -euo pipefail

ORIGIN="${1:-}"
TOKEN="${2:-}"

if [ -z "$ORIGIN" ] || [ -z "$TOKEN" ]; then
  echo "usage: $0 <https://external-origin> <session-token>" >&2
  echo "the token is the one keel serve printed in its URL" >&2
  exit 2
fi

case "$ORIGIN" in
  https://*) ;;
  *)
    # Refused rather than warned about. Every check below is about what HTTPS provides, and
    # running them against http:// would report a pass for a page that is not a secure context.
    echo "REFUSED: $ORIGIN is not https. A tunnel origin without TLS gives the console none of" >&2
    echo "the properties this script exists to verify, and 127.0.0.1's exemption does not apply" >&2
    echo "to it." >&2
    exit 2
    ;;
esac

HOST="${ORIGIN#https://}"
HOST="${HOST%%/*}"
FAILED=0

pass() { printf '  ok    %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; FAILED=1; }

# One cookie jar for the run: the first request exchanges the token for the session cookie, and
# everything after it has to work the way a browser would -- on the cookie, not on the token.
JAR="$(mktemp)"
trap 'rm -f "$JAR"' EXIT

fetch() {
  # $1 path, $2.. extra curl args. Prints "status\n<headers>".
  local path="$1"
  shift
  curl --silent --show-error --location --max-time 20 \
    --cookie "$JAR" --cookie-jar "$JAR" \
    --write-out '%{http_code}\n' --dump-header - --output /dev/null \
    "$@" "$ORIGIN$path"
}

echo "keel tunnel verification -- $ORIGIN"
echo
echo "part 1: what HTTP can answer"

# -- the session, over the external origin --------------------------------------------------------
BOOT="$(fetch "/?token=$TOKEN" || true)"
if printf '%s' "$BOOT" | grep -qE '^(200|204)$|^HTTP/[0-9.]+ 200'; then
  pass "the shell is served over the external origin"
else
  fail "the shell did not answer 200 over the external origin"
fi

if grep -qi 'keel_session' "$JAR"; then
  pass "the token was exchanged for a session cookie"
else
  fail "no session cookie was set -- the token was rejected, or the Host is not allowlisted"
fi

# `SameSite=Strict` and `Secure` come off the Set-Cookie header rather than the jar, because
# curl's jar format keeps neither.
COOKIE_HEADER="$(printf '%s' "$BOOT" | grep -i '^set-cookie:' || true)"
if printf '%s' "$COOKIE_HEADER" | grep -qi 'samesite=strict'; then
  pass "the session cookie is SameSite=Strict"
else
  fail "the session cookie is not SameSite=Strict"
fi

# -- the rebinding defence, still refusing ---------------------------------------------------------
#
# THE CHECK THAT MATTERS MOST HERE. `--external-host` teaches the server one name; everything else
# must still be refused, including a name that resolves to the same address. A tunnel deployment
# where this passes is one where the allowlist has been widened until it stops being one.
SPOOFED="$(curl --silent --show-error --max-time 20 --output /dev/null \
  --write-out '%{http_code}' --header "Host: evil.example" "$ORIGIN/api/config" || true)"
if [ "$SPOOFED" = "403" ] || [ "$SPOOFED" = "400" ] || [ "$SPOOFED" = "421" ]; then
  pass "a spoofed Host is still refused ($SPOOFED)"
else
  # A tunnel may rewrite Host before keel sees it, in which case this proves nothing about keel
  # and the operator has to check the edge instead. Said plainly rather than scored as a pass.
  fail "a spoofed Host answered $SPOOFED -- either the allowlist is too wide, or the tunnel"
  printf '        rewrites Host before keel sees it (check the edge, not the engine)\n'
fi

# -- the PWA assets, over the external origin ------------------------------------------------------
for asset in /manifest.webmanifest /sw.js; do
  STATUS="$(fetch "$asset" | tail -n 1 || true)"
  if [ "$STATUS" = "200" ]; then
    pass "$asset is served (200)"
  else
    fail "$asset answered $STATUS"
  fi
done

NOSNIFF="$(fetch /sw.js | grep -ci 'x-content-type-options: nosniff' || true)"
if [ "$NOSNIFF" != "0" ]; then
  pass "responses carry X-Content-Type-Options: nosniff"
else
  fail "no nosniff header -- a sniffing browser decides what the service worker IS"
fi

echo
echo "part 2: what only a browser can answer -- run these on the device, on $ORIGIN"
cat <<CHECKLIST
  [ ] devtools console: window.isSecureContext === true
      Over HTTPS this comes from TLS, not from the 127.0.0.1 exemption. If it is false, nothing
      below can pass and the console must not be used through this tunnel.
  [ ] devtools > Application > Service Workers: one worker, "activated and is running",
      with a scope of $ORIGIN/
  [ ] devtools > Application > Cache Storage: the cache name carries THIS build's version, and
      holds no entry from a loopback origin (caches are per-origin -- a shared entry means the
      origin is not what you think it is)
  [ ] devtools > Application > Manifest: no errors, icons resolve, and the browser offers to
      install
  [ ] install it, then kill the tunnel: the installed console opens and reports the engine as
      unreachable, rather than showing a blank page or stale figures without saying so
  [ ] devtools > Application > Cookies: the session cookie is Secure, HttpOnly and SameSite=Strict
CHECKLIST

echo
if [ "$FAILED" -eq 0 ]; then
  echo "part 1 passed. #648 closes when part 2 is done ON A DEVICE and recorded on the issue --"
  echo "a shell cannot see any of it."
else
  echo "part 1 FAILED. Do not expose the console until the failures above are understood."
fi
exit "$FAILED"
