#!/usr/bin/env bash
# desktop/install-desktop.sh -- provisions the Desktop role: a venv with
# bleak and a runnable push.py, on a machine that may not be the
# maintainer's. Not yet implemented -- see ticket #34.
#
# Contract with the repo-root bootstrap (install.sh), settled by #33 so
# both sides can be built independently:
#   argv[1]        staging root -- the unpacked archive, e.g. the directory
#                  containing this file's parent (desktop/) and pi/, ready
#                  to detect an in-place clone against.
#   env            ZEROPI_REF, ZEROPI_SHA, ZEROPI_TIMESTAMP -- for stamping
#                  a VERSION file (sha/ref/timestamp) at whichever install
#                  root this script picks (in-place .venv, or
#                  ~/.local/share/zeropi-display/ standalone). All optional;
#                  fall back sanely if unset (a direct, non-bootstrap run).
#   invocation     always unprivileged. Runs as root is refused by
#                  install.sh before it gets here.
#   ownership      copies only the files it owns out of the staging root;
#                  never touches an existing usage-archive store.
#
# See map #7's Delivery shape section for the in-place-vs-standalone
# detection rule and the Linux-only refusal (#32 is push.py's own
# BlueZ-specific-API wart, not this script's job to work around).

set -euo pipefail

echo "desktop/install-desktop.sh is not implemented yet -- see ticket #34." >&2
echo "See map #7 (delivery) and this script's header for the contract it must satisfy." >&2
exit 1
