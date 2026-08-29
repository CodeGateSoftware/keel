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

# -- step 2/7: the latest release ------------------------------------------------------------------
#
# This step runs BEFORE the Python search, and the order is the point (#557): the Python
# floor the installer enforces is the floor of the RELEASE BEING INSTALLED, read from that
# release's own pyproject.toml in the next step -- so the tag has to be known first. The
# tag is extracted with sed, not Python: no interpreter has been found yet, and macOS
# ships no jq either. `tag_name` is a single quoted scalar in the payload; the full
# manifest parse runs in step 4, once a Python is in hand.
say "step 2/7: resolving the latest release from the GitHub API"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/keel-install.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT
RELEASE_JSON="${TMP_DIR}/release.json"
printf '  GET %s\n' "$LATEST_API"
curl -fsSL "$LATEST_API" -o "$RELEASE_JSON" || die "could not reach the GitHub releases API"
TAG="$(sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$RELEASE_JSON" | head -n 1)"
[ -n "$TAG" ] || die "the latest release payload carries no tag_name -- the Python floor and the wheel names are both derived from it"
say "  latest release: ${TAG}"

# -- step 3/7: Python, at the floor the RELEASE declares --------------------------------------------

# The floor that matters to an installer is the floor of the artifact being installed,
# not the floor of the development tree (#557): this script installs the latest release's
# wheels, and a release's `requires-python` can be lower than main's -- v0.11.2 shipped
# wheels declaring >=3.11 while the repo's own pyproject had already moved to a newer
# floor, so the installer refused, at this very step, Pythons the wheels it was about to
# install fully support, and every Linux leg of install-smoke went red. So the floor is
# FETCHED from the tag resolved above and enforced from that. The check itself is the
# interpreter's own version_info, not a parsed string.
say "step 3/7: finding Python -- at the floor the release being installed declares"
FLOOR_URL="https://raw.githubusercontent.com/${REPO}/${TAG}/pyproject.toml"
printf '  GET %s\n' "$FLOOR_URL"
FLOOR_TOML="${TMP_DIR}/release-pyproject.toml"

#: The fallback floor, used only if the fetch or the parse below fails: the floor the
#: SHIPPED WHEELS declare -- the releases this installer can pick currently ship
#: `requires-python = ">=3.14"`. UPDATE THIS CONSTANT when a GitHub Release raises the
#: floor its wheels declare (check the oldest release still installable, never the
#: development tree -- enforcing the dev tree's floor here is exactly bug #557).
FALLBACK_FLOOR="3.14"

FLOOR=""
if curl -fsSL "$FLOOR_URL" -o "$FLOOR_TOML" 2>/dev/null; then
  FLOOR="$(sed -n 's/^[[:space:]]*requires-python[[:space:]]*=[[:space:]]*">=[[:space:]]*\([0-9][0-9]*\.[0-9][0-9]*\)\(\.[0-9][0-9]*\)*"[[:space:]]*$/\1/p' "$FLOOR_TOML" | head -n 1)"
fi
if [ -n "$FLOOR" ]; then
  FLOOR_SOURCE="${TAG}/pyproject.toml (requires-python)"
else
  FLOOR="$FALLBACK_FLOOR"
  FLOOR_SOURCE="the FALLBACK constant -- the shipped-wheel floor; the fetch or parse failed"
fi
FLOOR_MAJOR="${FLOOR%%.*}"
FLOOR_MINOR="${FLOOR#*.}"
say "  enforcing Python >= ${FLOOR} (from ${FLOOR_SOURCE})"

# Candidates: `python3` first, then one `python3.X` name per minor from the newest minor
# we know down to the floor's -- DERIVED from the floor, never a fixed list, so a floor of
# 3.11 tries python3 then 3.14, 3.13, 3.12, 3.11. On a machine whose `python3` is an
# older system Python, the loop must still find a newer one installed alongside it -- a
# pyenv or deadsnakes interpreter that never shims `python3` is found by its own name.
#: The newest Python minor this script tries by name. UPDATE THIS CONSTANT when a newer
#: minor is one machines actually carry: a release floor past 14 lifts the walk only to
#: the floor itself, so a 3.15-or-later installed alongside an older `python3` -- the
#: pyenv/deadsnakes case the comment above exists for -- is never tried by name until
#: this number moves. Track what the platforms ship, never the development tree's floor.
NEWEST_KNOWN_MINOR=14
if [ "$FLOOR_MINOR" -gt "$NEWEST_KNOWN_MINOR" ]; then NEWEST_KNOWN_MINOR="$FLOOR_MINOR"; fi
CANDIDATES=("python${FLOOR_MAJOR}")
minor="$NEWEST_KNOWN_MINOR"
while [ "$minor" -ge "$FLOOR_MINOR" ]; do
  CANDIDATES+=("python${FLOOR_MAJOR}.${minor}")
  minor=$((minor - 1))
done
PY=""
for candidate in "${CANDIDATES[@]}"; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if "$candidate" -c "import sys; sys.exit(0 if sys.version_info >= (${FLOOR_MAJOR}, ${FLOOR_MINOR}) else 1)" 2>/dev/null; then
    PY="$(command -v "$candidate")"
    break
  fi
done
[ -n "$PY" ] || die "no Python >= ${FLOOR} found on PATH -- the wheels of ${TAG} require ${FLOOR}
                     or later. Check 'python3 --version', then install one and re-run:
                     - Ubuntu: the deadsnakes PPA (add-apt-repository ppa:deadsnakes/ppa)
                       packages minors Ubuntu itself does not carry yet
                     - pyenv:  'pyenv install ${FLOOR}', then put its shims on PATH
                     - uv:     'uv python install ${FLOOR}' (uv-managed interpreters install
                       to ~/.local and appear on PATH; this script then uses them as-is)"
say "  ok: ${PY} ($("$PY" -c 'import platform; print(platform.python_version())'))"

# -- step 4/7: download, printing every URL -------------------------------------------------------

# Parsing uses the Python just found: macOS does not ship jq, and we already require
# Python, so we add no dependency. This mirrors keel's own selector -- exact
# `<prefix>-<version>-...whl` names, one asset per prefix, and a loud refusal naming
# every prefix the release does not carry (a release missing a wheel cannot be deployed).
# The tag is re-derived here from the same saved payload (its stdout, as before) -- the
# sed extraction in step 2 was only the bootstrap copy needed before any Python existed.
say "step 4/7: downloading the five production wheels and config.yaml to ${TMP_DIR}"
MANIFEST="${TMP_DIR}/manifest.tsv"
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
  say "  installing the release's production config.yaml beside the venv (auto_trade.mode: confirm:
        keel previews every order and waits for your explicit approval -- the production
        template, not the paper one)"
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
printf '  config:      config.yaml beside the venv is the PRODUCTION config shipped with the\n'
printf '               release, in auto_trade.mode: confirm -- keel previews every order and\n'
printf '               waits for your explicit approval, and with credentials in ~/.keel/.env\n'
printf '               it CAN place live orders, each gated by that approval. To fetch candles\n'
printf '               you will want a free, read-only Coinbase Developer Platform (CDP) API\n'
printf '               key in ~/.keel/.env.\n'
printf '               For a config that simulates instead -- the dev template in\n'
printf '               auto_trade.mode: paper, which places nothing at all -- run from\n'
printf '               ~/.keel: ./.venv/bin/keel init-config --force\n'
printf '               (--force OVERWRITES the current config.yaml -- copy yours aside\n'
printf '               first if you have edited it; flipping auto_trade.mode to paper in\n'
printf '               the existing config.yaml is the no-overwrite alternative)\n'
printf '  guide:       https://keeltrading.com\n'
printf '  updates:     re-run this installer to move to a later release (#439, option A), or\n'
printf '               run: keel update   (from ~/.keel -- it serves this venv layout).\n'
printf '               Desktop app bundles update by re-downloading (docs/desktop-install.md).\n'
printf '  this folder: %s holds the venv, config.yaml and (once keel runs) the database.\n' "$KEEL_DIR"
