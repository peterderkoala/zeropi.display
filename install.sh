#!/usr/bin/env bash
# install.sh -- curl bootstrap for zeropi.display. Fetches a versioned
# tarball of this repo and provisions either end of the BLE link:
#
#   curl -fsSL https://raw.githubusercontent.com/peterderkoala/zeropi.display/dev/install.sh \
#       | bash -s -- pi
#   curl -fsSL https://raw.githubusercontent.com/peterderkoala/zeropi.display/dev/install.sh \
#       | bash -s -- desktop
#
# This script only resolves a ref to a commit sha, fetches and unpacks the
# matching archive, and delegates to the role script found inside it:
#   pi/install-pi.sh <staging-root>            (re-exec'd under sudo)
#   desktop/install-desktop.sh <staging-root>  (run unprivileged)
# Both receive ZEROPI_REF/ZEROPI_SHA/ZEROPI_TIMESTAMP in the environment so
# they can stamp their own VERSION file at whatever install root they own --
# this script doesn't know that path, since it differs by role.
#
# git is deliberately not used: it isn't installed on a stock Pi and would
# cost ~50 MB of dependencies on a Zero for a 36 KB branch tarball. See map
# #7's Delivery shape section and ticket #33.

set -euo pipefail

REPO="peterderkoala/zeropi.display"
ZEROPI_REF="${ZEROPI_REF:-dev}"

usage() {
    echo "Usage: install.sh <pi|desktop>" >&2
    echo "  curl -fsSL https://raw.githubusercontent.com/$REPO/dev/install.sh | bash -s -- pi" >&2
    echo "  curl -fsSL https://raw.githubusercontent.com/$REPO/dev/install.sh | bash -s -- desktop" >&2
    exit 1
}

[[ $# -ge 1 ]] || usage
ROLE="$1"
shift
case "$ROLE" in
    pi|desktop) ;;
    *)
        echo "Unknown role: $ROLE (expected 'pi' or 'desktop')" >&2
        usage
        ;;
esac

if [[ "$ROLE" == "desktop" && $EUID -eq 0 ]]; then
    echo "install.sh desktop must not run as root -- it would leave root-owned" >&2
    echo "files in your home directory. Re-run without sudo." >&2
    exit 1
fi

for tool in curl tar python3; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "FAIL: $tool is required and was not found on PATH." >&2
        exit 1
    }
done

echo "==> Resolving $ZEROPI_REF to a commit sha"
COMMIT_JSON="$(curl -fsSL -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/$REPO/commits/$ZEROPI_REF")" \
    || { echo "FAIL: could not reach the GitHub API to resolve '$ZEROPI_REF'." >&2; exit 1; }
SHA="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["sha"])' <<<"$COMMIT_JSON")"
[[ -n "$SHA" ]] || { echo "FAIL: could not resolve ref '$ZEROPI_REF' to a commit sha." >&2; exit 1; }
echo "    $ZEROPI_REF -> $SHA"

STAGING_PARENT="$(mktemp -d "${TMPDIR:-/tmp}/zeropi-display-install.XXXXXX")"
trap 'rm -rf "$STAGING_PARENT"' EXIT

echo "==> Fetching and unpacking the archive for $SHA"
curl -fsSL "https://github.com/$REPO/archive/$SHA.tar.gz" -o "$STAGING_PARENT/source.tar.gz"
tar -xzf "$STAGING_PARENT/source.tar.gz" -C "$STAGING_PARENT"
rm -f "$STAGING_PARENT/source.tar.gz"

# A branch tarball unpacks to <repo>-<branch>/; a sha tarball unpacks to
# <repo>-<sha>/. Either way it's the only entry in the staging parent, so
# find it by position rather than hardcoding the name.
STAGING_DIR="$(find "$STAGING_PARENT" -mindepth 1 -maxdepth 1 -type d | head -n1)"
[[ -n "$STAGING_DIR" ]] || { echo "FAIL: archive did not unpack to a directory." >&2; exit 1; }

ZEROPI_TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "==> Provisioning role: $ROLE"
case "$ROLE" in
    pi)
        ROLE_SCRIPT="$STAGING_DIR/pi/install-pi.sh"
        [[ -f "$ROLE_SCRIPT" ]] || { echo "FAIL: $ROLE_SCRIPT not found in the fetched archive." >&2; exit 1; }
        # Runs unprivileged up to here; the pi role needs root to touch
        # /etc, /opt and systemd, so it re-execs under sudo here rather than
        # asking the caller to prefix sudo themselves -- one correct command
        # works for both roles.
        if [[ $EUID -eq 0 ]]; then
            env ZEROPI_REF="$ZEROPI_REF" ZEROPI_SHA="$SHA" ZEROPI_TIMESTAMP="$ZEROPI_TIMESTAMP" \
                bash "$ROLE_SCRIPT" "$STAGING_DIR" "$@"
        else
            # By this point stdin is the tail of the curl|bash pipe, not a
            # terminal -- sudo can't read a password off it. sudo prompts on
            # /dev/tty directly when one exists (fine for the documented
            # `curl ... | bash -s -- pi` on a terminal, or `ssh -t`), but with
            # neither a tty nor passwordless sudo already configured it would
            # otherwise fail confusingly mid-install. Check up front instead.
            #
            # `[[ -e /dev/tty ]]` is not enough: the node always exists, even
            # with no controlling terminal (a plain `ssh host 'cmd'`, no -t),
            # so that test is true and `< /dev/tty` then fails to open with
            # "No such device or address". Test openability instead.
            tty_usable() { exec 3</dev/tty 2>/dev/null && exec 3<&-; }
            if ! tty_usable && ! sudo -n true 2>/dev/null; then
                echo "FAIL: this needs sudo and there's no terminal to prompt on." >&2
                echo "Over ssh, add -t: ssh -t <host> 'curl ... | bash -s -- pi'" >&2
                echo "or configure passwordless sudo for this command first." >&2
                exit 1
            fi
            if tty_usable; then
                sudo env ZEROPI_REF="$ZEROPI_REF" ZEROPI_SHA="$SHA" ZEROPI_TIMESTAMP="$ZEROPI_TIMESTAMP" \
                    bash "$ROLE_SCRIPT" "$STAGING_DIR" "$@" < /dev/tty
            else
                sudo env ZEROPI_REF="$ZEROPI_REF" ZEROPI_SHA="$SHA" ZEROPI_TIMESTAMP="$ZEROPI_TIMESTAMP" \
                    bash "$ROLE_SCRIPT" "$STAGING_DIR" "$@"
            fi
        fi
        ;;
    desktop)
        ROLE_SCRIPT="$STAGING_DIR/desktop/install-desktop.sh"
        [[ -f "$ROLE_SCRIPT" ]] || { echo "FAIL: $ROLE_SCRIPT not found in the fetched archive." >&2; exit 1; }
        env ZEROPI_REF="$ZEROPI_REF" ZEROPI_SHA="$SHA" ZEROPI_TIMESTAMP="$ZEROPI_TIMESTAMP" \
            bash "$ROLE_SCRIPT" "$STAGING_DIR" "$@"
        ;;
esac
