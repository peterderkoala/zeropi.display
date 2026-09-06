#!/usr/bin/env bash
# desktop/install-desktop.sh -- provisions the Desktop role: a venv with
# bleak and a runnable push.py, on a machine that may not be the
# maintainer's.
#
# Contract with the repo-root bootstrap (install.sh), settled by #33 so
# both sides can be built independently:
#   argv[1]        staging root -- present when run via the bootstrap, but
#                  unused: SCRIPT_DIR (below) already resolves correctly
#                  into the unpacked archive via BASH_SOURCE, the same way
#                  pi/install-pi.sh does. Consumed and discarded so flags
#                  can follow it; never re-derive paths from it.
#   argv[2:]       --in-place / --prefix <dir>, the escape hatch over mode
#                  auto-detection (see below).
#   env            ZEROPI_REF, ZEROPI_SHA, ZEROPI_TIMESTAMP -- for stamping
#                  a VERSION file (sha/ref/timestamp) at whichever install
#                  root this script picks. All optional; fall back sanely
#                  if unset (a direct, non-bootstrap run).
#   invocation     always unprivileged. Runs as root is refused by
#                  install.sh before it gets here, and refused again below
#                  for a direct run.
#   ownership      copies only the files it owns out of the staging root;
#                  never touches an existing usage-archive store (#28).
#
# Two modes, auto-detected: an existing clone (a .git directory alongside
# this file's parent) gets its .venv set up in place, so the maintainer's
# edits to push.py are picked up without a second, drifting copy; anything
# else -- including every run via the curl bootstrap, since a GitHub
# archive tarball never carries .git -- installs standalone under
# ~/.local/share/zeropi-display/. See map #7's Delivery shape section.
#
# Linux-only: push.py's MTU negotiation goes through
# client._backend._acquire_mtu(), a private BlueZ-specific bleak API (#32,
# out of scope here). Refusing loudly beats a clean install that fails at
# push time.

set -euo pipefail

SERVICE_NAME="zeropi-display service"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "FAIL: the Desktop role only works on Linux -- push.py's MTU" >&2
    echo "negotiation depends on a BlueZ-specific bleak API (see #32)." >&2
    exit 1
fi

if [[ $EUID -eq 0 ]]; then
    echo "FAIL: install-desktop.sh must not run as root." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# The clone actually worth installing into, if any -- found from the
# invoking shell's cwd (see the in-place detection note below), not from
# where this script itself happens to be running out of. Resolved once,
# up front, so both auto-detection and an explicit --in-place agree on it.
CWD_REPO_ROOT="$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$CWD_REPO_ROOT" && -e "$CWD_REPO_ROOT/desktop/install-desktop.sh" ]] || CWD_REPO_ROOT=""

# argv[1] is the bootstrap's staging root when present -- unused (see
# header) but consumed so flags parse correctly either way.
if [[ $# -ge 1 && "$1" != --* ]]; then
    shift
fi

MODE=""
PREFIX=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --in-place)
            MODE="in-place"
            shift
            ;;
        --prefix)
            [[ $# -ge 2 ]] || { echo "FAIL: --prefix needs a directory argument." >&2; exit 1; }
            MODE="standalone"
            PREFIX="$2"
            shift 2
            ;;
        --prefix=*)
            MODE="standalone"
            PREFIX="${1#--prefix=}"
            shift
            ;;
        *)
            echo "FAIL: unknown argument: $1" >&2
            echo "Usage: install-desktop.sh [--in-place | --prefix <dir>]" >&2
            exit 1
            ;;
    esac
done

if [[ -z "$MODE" ]]; then
    # Via the curl bootstrap this script always runs out of a /tmp staging
    # unpack of a tarball that never carries .git (see header), so basing
    # detection on where this script itself lives ($REPO_ROOT, above) can
    # *never* find a clone -- it would be dead code on the one documented
    # invocation path. $PWD survives `curl ... | bash` unchanged (bash
    # inherits the caller's cwd), which is why $CWD_REPO_ROOT is resolved
    # from there instead, up front.
    if [[ -n "$CWD_REPO_ROOT" ]]; then
        MODE="in-place"
    else
        MODE="standalone"
    fi
fi

if [[ "$MODE" == "in-place" ]]; then
    # An explicit --in-place still needs the *real* clone root, not
    # $REPO_ROOT -- which is the staging tarball under the curl bootstrap.
    # Falls back to $REPO_ROOT only for a direct, non-bootstrap invocation
    # from outside any git worktree (e.g. this script copied out on its
    # own), where that's the best guess available.
    REPO_ROOT="${CWD_REPO_ROOT:-$REPO_ROOT}"
    echo "==> Mode: in-place (clone detected at $REPO_ROOT)"
    VENV_DIR="$REPO_ROOT/.venv"
    PUSH_PY="$REPO_ROOT/desktop/push.py"
    REQUIREMENTS="$REPO_ROOT/desktop/requirements.txt"
else
    STANDALONE_DIR="${PREFIX:-$HOME/.local/share/zeropi-display}"
    echo "==> Mode: standalone ($STANDALONE_DIR)"
    VENV_DIR="$STANDALONE_DIR/venv"
    PUSH_PY="$STANDALONE_DIR/push.py"
    REQUIREMENTS="$STANDALONE_DIR/requirements.txt"

    echo "==> Deploying push.py and requirements.txt to $STANDALONE_DIR"
    mkdir -p "$STANDALONE_DIR"
    cp "$SCRIPT_DIR/push.py" "$PUSH_PY"
    cp "$SCRIPT_DIR/requirements.txt" "$REQUIREMENTS"
fi

echo "==> Setting up the venv at $VENV_DIR"
if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv "$VENV_DIR"
fi
# `python -m pip`, not bin/pip: a venv without bin/pip (e.g. one made by
# `uv venv`, which skips pip by default -- the project's own
# README-documented dev setup) needs ensurepip run in place first. Adding
# pip in place, rather than deleting and rebuilding the directory, matters
# because this may be the maintainer's actual dev venv, already populated by
# uv, not disposable scratch state. Even after ensurepip, which entry-point
# names land in bin/ isn't guaranteed (observed: pip3/pip3.12 but no bare
# `pip`), so `-m pip` is used throughout rather than bin/pip.
if ! "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1; then
    echo "    existing venv has no pip (e.g. from \`uv venv\`); adding it via ensurepip"
    "$VENV_DIR/bin/python" -m ensurepip --upgrade >/dev/null || {
        echo "FAIL: $VENV_DIR exists without pip and ensurepip could not add it." >&2
        echo "Fix or remove that venv yourself, then re-run." >&2
        exit 1
    }
fi
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
"$VENV_DIR/bin/python" -m pip install --quiet -r "$REQUIREMENTS"

echo "==> Stamping VERSION"
# Lives inside the venv, not the install root: for in-place that root is the
# maintainer's tracked git checkout, and a stray VERSION file there would be
# an untracked file to trip over, not useful version info the checkout
# doesn't already carry via its own commit.
VERSION_SHA="${ZEROPI_SHA:-}"
VERSION_REF="${ZEROPI_REF:-}"
VERSION_TIMESTAMP="${ZEROPI_TIMESTAMP:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
if [[ -z "$VERSION_SHA" ]]; then
    VERSION_SHA="$(git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || echo "unknown")"
    VERSION_REF="${VERSION_REF:-local}"
fi
cat > "$VENV_DIR/VERSION" <<EOF
sha=$VERSION_SHA
ref=$VERSION_REF
installed_at=$VERSION_TIMESTAMP
EOF

if [[ "$MODE" == "standalone" ]]; then
    echo "==> Installing the zeropi-push shim to ~/.local/bin"
    mkdir -p "$HOME/.local/bin"
    cat > "$HOME/.local/bin/zeropi-push" <<EOF
#!/usr/bin/env bash
exec "$VENV_DIR/bin/python" "$PUSH_PY" "\$@"
EOF
    chmod +x "$HOME/.local/bin/zeropi-push"
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) echo "    NOTE: ~/.local/bin is not on PATH -- add it, or run" \
                "$HOME/.local/bin/zeropi-push directly." ;;
    esac
fi

echo "==> Checking whether a Pi is reachable"
# A warning, not a hard failure: the two roles are provisioned
# independently and the Pi may not be up yet. Reuses push.py's own
# matches_service()/SERVICE_UUID rather than duplicating the UUID here, so
# the two can never drift apart.
if "$VENV_DIR/bin/python" - "$(dirname "$PUSH_PY")" <<'PYEOF'
import asyncio
import sys

sys.path.insert(0, sys.argv[1])
from bleak import BleakScanner
from push import matches_service

async def check() -> bool:
    try:
        device = await BleakScanner.find_device_by_filter(matches_service, timeout=10.0)
    except Exception as exc:
        print(f"    scan failed: {exc}", file=sys.stderr)
        return False
    return device is not None

sys.exit(0 if asyncio.run(check()) else 1)
PYEOF
then
    echo "    found a Pi advertising the $SERVICE_NAME"
else
    echo "    WARNING: no Pi advertising the $SERVICE_NAME was seen in 10s." >&2
    echo "    Fine if the Pi isn't provisioned or powered on yet -- push.py" >&2
    echo "    will scan again on its own next run." >&2
fi

echo "==> Desktop role installed ($MODE)."
if [[ "$MODE" == "in-place" ]]; then
    echo "    Run: $VENV_DIR/bin/python $PUSH_PY"
else
    echo "    Run: zeropi-push  (or $HOME/.local/bin/zeropi-push)"
fi
