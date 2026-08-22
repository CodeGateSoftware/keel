#!/usr/bin/env bash
#
# keel's terminal installer -- the no-warning path (issue #479).
#
# WHAT THIS IS: installs keel on macOS or Linux from the latest GitHub release's Python
# wheels, into a per-user venv at ~/.keel/.venv. Nothing is downloaded as an
# *application*, so no OS trust dialog ("developer cannot be verified", SmartScreen) is
# ever involved -- that path stays the .dmg/.zip walkthrough in docs/desktop-install.md,
# and code signing stays #438 until a certificate is affordable.
#
# HOW TO READ IT: every step prints what it is about to do, and why, BEFORE it runs.
# `set -euo pipefail` (below) stops the script on the first failing command or pipe, so
# a broken step can never be followed by a success message. The script runs no
# privileged commands, writes nothing outside the invoking user's home, and fetches
# exactly the five production wheels by allowlisted name -- the release also carries
# dev-only and stub venue wheels that a deployment must not have, and selection here is
# by exact name, never `*.whl`, for the same reason as `keel update`'s selector
# (keel/commands/update.py, PRODUCTION_WHEEL_PREFIXES).
#
# UPDATES compose with this installer (issue #439, option A: per-release download, no
# self-update): re-running this script moves ~/.keel to the latest release, and
# `keel update` -- run from ~/.keel -- also handles the venv deployment this creates.
# An existing config.yaml or database under ~/.keel is never touched.
set -euo pipefail

[ -n "${BASH_VERSION:-}" ] || { printf 'installer: FAIL: run me under bash\n' >&2; exit 1; }

# -- constants: what, where, and the one allowlist ------------------------------------------------

#: The repository we install from, and its public unauthenticated endpoints. No auth and
#: no tokens: a bootstrap script must never grow a credential.
REPO="CodeGateSoftware/keel"
LATEST_API="https://api.github.com/repos/${REPO}/releases/latest"

#: The five PRODUCTION wheel name prefixes -- the same allowlist, in the same order, as
#: keel's own updater (keel/commands/update.py, PRODUCTION_WHEEL_PREFIXES). A release
#: also ships other venue wheels a deployment must not have; selection below is by exact
#: `<prefix>-<version>-` name, so nothing outside this line can ride along.
WHEEL_PREFIXES="keel_core keel_broker_api keel_broker_coinbase keel_broker_alpaca keel_trader"

#: Where the deployment lives: one per-user folder holding the venv, config.yaml and --
#: once keel runs -- the database and .env. A folder you can look inside is keel's
#: deployment model. The venv is named `.venv` deliberately: that is the layout `keel
#: update` recognises, so the updater can serve what this script built.
KEEL_DIR="${HOME}/.keel"
VENV_DIR="${KEEL_DIR}/.venv"

say() { printf '==> %s\n' "$*"; }
die() { printf 'installer: FAIL: %s\n' "$*" >&2; exit 1; }

# -- step 1/7: the platform ----------------------------------------------------------------------

say "step 1/7: checking the platform"
case "$(uname -s)" in
  Darwin | Linux) say "  ok: $(uname -s)" ;;
  *)
    die "unsupported platform '$(uname -s)': this installer is for macOS (Darwin) and Linux.
         Windows users: download the release .zip and follow docs/desktop-install.md."
    ;;
esac
command -v curl >/dev/null 2>&1 || die "curl is required but was not found on PATH"

# -- step 2/7: Python, with the floor stated ------------------------------------------------------

# keel requires Python 3.11+ (tests/test_python_floor.py); the check is the interpreter's
# own version_info, not a parsed string, and the failure names the floor and what to do.
say "step 2/7: finding Python >= 3.11"
PY=""
for candidate in python3 python3.14 python3.13 python3.12 python3.11; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$(command -v "$candidate")"
    break
  fi
done
[ -n "$PY" ] || die "no Python >= 3.11 found on PATH -- keel requires 3.11 or later. Check
                     'python3 --version', install a newer Python, and re-run this script."
say "  ok: ${PY} ($("$PY" -c 'import platform; print(platform.python_version())'))"

# -- step 3/7: the latest release ----------------------------------------------------------------

say "step 3/7: resolving the latest release from the GitHub API"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/keel-install.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT
RELEASE_JSON="${TMP_DIR}/release.json"
printf '  GET %s\n' "$LATEST_API"
curl -fsSL "$LATEST_API" -o "$RELEASE_JSON" || die "could not reach the GitHub releases API"
MANIFEST="${TMP_DIR}/manifest.tsv"

# Parsing uses the Python we just found: macOS does not ship jq, and we already require
# Python, so we add no dependency. This mirrors keel's own selector -- exact
# `<prefix>-<version>-...whl` names, one asset per prefix, and a loud refusal naming
# every prefix the release does not carry (a release missing a wheel cannot be deployed).
TAG="$("$PY" -c '
import json, sys
try:
    release = json.loads(open(sys.argv[1]).read())
except ValueError as exc:
    sys.stderr.write("release payload is not JSON: %s\n" % exc)
    sys.exit(1)
tag = release.get("tag_name") or ""
version = tag[1:] if tag.startswith("v") else tag
if not version:
    sys.stderr.write("release payload has no tag_name\n")
    sys.exit(1)
assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
lines, missing = [], []
for prefix in sys.argv[3].split():
    matches = sorted(
        name for name in assets
        if name.startswith("%s-%s-" % (prefix, version)) and name.endswith(".whl")
    )
    if len(matches) == 1:
        lines.append(matches[0] + " " + assets[matches[0]])
    else:
        missing.append(prefix)
if missing:
    sys.stderr.write("release %s does not carry every production wheel (missing: %s)\n"
                     % (tag, ", ".join(missing)))
    sys.exit(1)
open(sys.argv[2], "w").write("\n".join(lines) + "\n")
print(tag)
' "$RELEASE_JSON" "$MANIFEST" "$WHEEL_PREFIXES")" || die "the latest release is not installable (see above)"
say "  latest release: ${TAG}"

# -- step 4/7: download, printing every URL -------------------------------------------------------

say "step 4/7: downloading the five production wheels and config.yaml to ${TMP_DIR}"
WHEEL_PATHS=()
while read -r name url; do
  printf '  GET %s\n' "$url"
  curl -fsSL "$url" -o "${TMP_DIR}/${name}" || die "could not download ${name}"
  WHEEL_PATHS+=("${TMP_DIR}/${name}")
done < "$MANIFEST"
CONFIG_URL="https://github.com/${REPO}/releases/download/${TAG}/config.yaml"
printf '  GET %s\n' "$CONFIG_URL"
curl -fsSL "$CONFIG_URL" -o "${TMP_DIR}/config.yaml" || die "could not download config.yaml"

# -- step 5/7: checksums, stated honestly ---------------------------------------------------------

# The release publishes SHA256SUMS files for the DESKTOP artifacts only; there are no
# published checksums for the wheels, and this script does not pretend to verify what was
# never published. What it does instead is print each wheel's sha256 as computed LOCALLY,
# so the run leaves an auditable record of exactly what was installed.
say "step 5/7: recording the sha256 of each wheel as downloaded (no published wheel checksums exist to compare against)"
for path in "${WHEEL_PATHS[@]}"; do
  printf '  %s  %s\n' \
    "$("$PY" -c 'import hashlib, sys
print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$path")" \
    "$(basename "$path")"
done

# -- step 6/7: venv, install by exact path, config guard ------------------------------------------

say "step 6/7: installing into ${KEEL_DIR} (per-user; no elevated commands)"
mkdir -p "$KEEL_DIR"
# Discovery asks uv to ANSWER ITS OWN VERSION, not merely to be on PATH: a broken or
# half-installed uv shim must not abort the install -- a uv that cannot run is honestly
# treated as absent, and the pip path below needs no uv.
HAVE_UV=0
if command -v uv >/dev/null 2>&1 && uv --version >/dev/null 2>&1; then HAVE_UV=1; fi
if [ ! -x "${VENV_DIR}/bin/python" ]; then
  if [ "$HAVE_UV" -eq 1 ]; then
    say "  uv found: creating the venv with 'uv venv --python <the python found above>'"
    uv venv --python "$PY" "$VENV_DIR" || die "uv venv failed"
  else
    say "  uv not found: creating the venv with '${PY} -m venv'"
    "$PY" -m venv "$VENV_DIR" || die "python -m venv failed (on Debian/Ubuntu the python3-venv
                                      package is the usual missing piece -- install it and re-run)"
  fi
else
  say "  ${VENV_DIR} already exists: reusing it (re-running this script upgrades in place)"
fi
VENV_PY="${VENV_DIR}/bin/python"

# Installation is BY EXACT WHEEL PATH, the same form `keel update` uses -- never by
# package name, never from an index for the keel distributions themselves. The
# `--find-links` directory lets the wheels' pinned keel dependencies resolve from the
# downloaded release rather than a public index; third-party dependencies (click, numpy,
# ...) come from PyPI as usual. There is an unrelated "keel" project on PyPI; installing
# keel by name from an index is the one mistake this section exists to make impossible.
if [ "$HAVE_UV" -eq 1 ]; then
  say "  running: uv pip install --python <venv> --find-links <tmp> <the five wheel paths>"
  uv pip install --python "$VENV_PY" --find-links "$TMP_DIR" "${WHEEL_PATHS[@]}" \
    || die "uv pip install failed"
else
  # A venv without pip gets one honest bootstrap attempt via ensurepip, then a clear
  # failure -- never a silent skip or an install pretending to have run.
  if ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
    say "  pip is not bootstrapped in the new venv: running 'ensurepip --upgrade'"
    "$VENV_PY" -m ensurepip --upgrade || die "ensurepip failed: this venv has no pip and it
                                              could not be bootstrapped"
  fi
  say "  running: ${VENV_PY} -m pip install --no-input --find-links <tmp> <the five wheel paths>"
  "$VENV_PY" -m pip install --no-input --find-links "$TMP_DIR" "${WHEEL_PATHS[@]}" \
    || die "pip install failed"
fi

# config.yaml is the user's once installed: NEVER overwritten -- it may hold their edits.
# The release's copy lands beside the venv only when no config is there yet, and an
# existing deployment's database is likewise never touched: upgrading code must not mean
# touching data.
if [ -e "${KEEL_DIR}/config.yaml" ]; then
  say "  ${KEEL_DIR}/config.yaml already exists: keeping it (not overwritten)"
else
  say "  installing the release's default config.yaml beside the venv (the paper profile)"
  cp "${TMP_DIR}/config.yaml" "${KEEL_DIR}/config.yaml"
fi
for db in "${KEEL_DIR}"/keel*.db; do
  if [ -e "$db" ]; then say "  existing database $(basename "$db"): not touched"; fi
done

# -- step 7/7: verify BEFORE declaring success ----------------------------------------------------

# `keel versions` is the one check that can actually fail -- it reports every keel
# distribution the venv resolves and whether they agree on the release version. It runs
# from the deployment folder so keel resolves its state there, and a failure fails this
# script: success is never declared unverified.
say "step 7/7: verifying the install with 'keel versions' (run from ${KEEL_DIR})"
( cd "$KEEL_DIR" && "${VENV_DIR}/bin/keel" versions ) \
  || die "'keel versions' failed: the install is NOT complete -- see its output above"

# -- success + next steps (only reachable past the verify) ----------------------------------------

say "installed keel ${TAG} (verified)"
printf '\nNext steps:\n'
printf '  run it:      cd ~/.keel && ./.venv/bin/keel versions\n'
printf '               (or: source ~/.keel/.venv/bin/activate, then: keel versions)\n'
printf '  paper:       config.yaml beside the venv is the default paper profile -- nothing in\n'
printf '               it can place a live order. To fetch candles you will want a free,\n'
printf '               read-only Coinbase Developer Platform (CDP) API key in ~/.keel/.env.\n'
printf '  guide:       https://keeltrading.com\n'
printf '  updates:     re-run this installer to move to a later release (#439, option A), or\n'
printf '               run: keel update   (from ~/.keel -- it serves this venv layout).\n'
printf '               Desktop app bundles update by re-downloading (docs/desktop-install.md).\n'
printf '  this folder: %s holds the venv, config.yaml and (once keel runs) the database.\n' "$KEEL_DIR"
