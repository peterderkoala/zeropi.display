#!/usr/bin/env bash
# pi/install.sh -- provisions a stock Raspberry Pi OS (Debian 13 / trixie)
# Pi to run the zeropi-display BLE receiver unattended.
#
# Run locally on the Pi as root (sudo ./install.sh), with this repo's pi/
# directory present on disk. Safe to re-run on an already-provisioned Pi.
#
# See issue #7 (map) for why each step exists and #8 for the decisions
# behind this script's shape (local, idempotent, venv-based).

set -euo pipefail

REQUIRED_BLUEZ_VERSION="5.82-1.1+rpt2"
INSTALL_DIR="/opt/zeropi-display"
VENV_DIR="$INSTALL_DIR/venv"
RUN_AS_USER="pi"
DROPIN_DIR="/etc/systemd/system/bluetooth.service.d"
DROPIN_FILE="$DROPIN_DIR/noplugin.conf"
MAIN_CONF="/etc/bluetooth/main.conf"
ADAPTER="hci0"
ADVERT_POLL_TRIES=20
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "install.sh must run as root, e.g.: sudo ./install.sh" >&2
    exit 1
fi

echo "==> Checking BlueZ version"
BLUEZ_VERSION="$(dpkg-query -W -f='${Version}' bluez 2>/dev/null || true)"
if [[ -z "$BLUEZ_VERSION" ]]; then
    echo "FAIL: bluez is not installed." >&2
    exit 1
fi
if ! dpkg --compare-versions "$BLUEZ_VERSION" ge "$REQUIRED_BLUEZ_VERSION"; then
    echo "FAIL: bluez $BLUEZ_VERSION is older than the required floor $REQUIRED_BLUEZ_VERSION." >&2
    echo "On older BlueZ, RegisterAdvertisement fails with 'Invalid Parameters'" >&2
    echo "or never returns. Upgrade bluez and re-run." >&2
    exit 1
fi
echo "    bluez $BLUEZ_VERSION OK"

echo "==> Installing apt dependencies (python3-dbus, python3-gi, python3-venv)"
# bluezero imports both dbus-python and PyGObject. Both are C extensions;
# building them from a pip sdist needs cairo/girepository dev headers that a
# stock image does not carry, so they come from apt and the venv borrows them
# via --system-site-packages below. python3-gi ships with Raspberry Pi OS, but
# it is named explicitly rather than assumed.
apt-get update -qq
apt-get install -y python3-dbus python3-gi python3-venv

echo "==> Excluding the midi/sap/avrcp bluetoothd plugins"
# The stock midi plugin segfaults bluetoothd on every incoming LE
# connection. DisablePlugins in main.conf does NOT work for this -- it is
# not a valid BlueZ 5.82 key -- so exclusion has to go through a
# command-line option delivered via a systemd drop-in.
mkdir -p "$DROPIN_DIR"
cat > "$DROPIN_FILE" <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/libexec/bluetooth/bluetoothd --noplugin=midi,sap,avrcp
EOF

echo "==> Removing ineffective DisablePlugins from main.conf, if present"
if grep -qE '^\s*DisablePlugins\s*=' "$MAIN_CONF"; then
    sed -i '/^\s*DisablePlugins\s*=/d' "$MAIN_CONF"
fi

echo "==> Setting ControllerMode = le in main.conf"
if grep -qE '^\s*ControllerMode\s*=' "$MAIN_CONF"; then
    sed -i 's/^\s*ControllerMode\s*=.*/ControllerMode = le/' "$MAIN_CONF"
elif grep -q '^\[General\]' "$MAIN_CONF"; then
    sed -i '/^\[General\]/a ControllerMode = le' "$MAIN_CONF"
else
    printf '\n[General]\nControllerMode = le\n' >> "$MAIN_CONF"
fi

echo "==> Deploying receive.py to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_DIR/receive.py" "$INSTALL_DIR/receive.py"
chown -R "$RUN_AS_USER:$RUN_AS_USER" "$INSTALL_DIR"

echo "==> Installing bluezero into a venv"
# --system-site-packages is load-bearing, not a convenience: without it pip
# resolves bluezero's PyGObject dependency from source and the build dies at
# "Dependency \"cairo\" not found". With it, the apt-installed python3-gi and
# python3-dbus satisfy those requirements and only bluezero itself is
# installed into the venv. A venv left over from an older run of this script
# may predate the flag, so it is checked and rebuilt rather than reused.
if [[ -d "$VENV_DIR" ]] && ! grep -qi '^include-system-site-packages = true' "$VENV_DIR/pyvenv.cfg" 2>/dev/null; then
    echo "    existing venv lacks system site-packages; rebuilding it"
    rm -rf "$VENV_DIR"
fi
if [[ ! -d "$VENV_DIR" ]]; then
    sudo -u "$RUN_AS_USER" python3 -m venv --system-site-packages "$VENV_DIR"
fi
sudo -u "$RUN_AS_USER" "$VENV_DIR/bin/pip" install --quiet --upgrade pip
sudo -u "$RUN_AS_USER" "$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"

echo "==> Installing the zeropi-display systemd unit"
cp "$SCRIPT_DIR/zeropi-display.service" /etc/systemd/system/zeropi-display.service
systemctl daemon-reload

echo "==> Restarting bluetooth to pick up the drop-in and main.conf changes"
systemctl restart bluetooth
sleep 2

echo "==> Enabling and (re)starting zeropi-display"
systemctl enable --now zeropi-display.service
sleep 2

echo "==> Self-check"
FAIL=0

if ! systemctl is-active --quiet bluetooth; then
    echo "    FAIL: bluetooth.service is not active" >&2
    FAIL=1
fi

if ! systemctl is-active --quiet zeropi-display; then
    echo "    FAIL: zeropi-display.service is not active" >&2
    FAIL=1
fi

if [[ ! -f "$DROPIN_FILE" ]]; then
    echo "    FAIL: $DROPIN_FILE is missing" >&2
    FAIL=1
fi

# Verified against real hardware by ticket #11 and promoted from a warning
# to a hard failure: an install that leaves no advertisement on the air is
# not a working install, however healthy the two units look.
#
# The property is read over D-Bus rather than scraped from `bluetoothctl
# show`. bluetoothctl interleaves colourised async "[CHG] Controller ...
# ActiveInstances" lines with its own property block, so a grep can pick up
# either one; busctl returns exactly "y <n>".
#
# It is polled rather than sampled once. bluetoothd was restarted moments
# ago, and receive.py only registers its advertisement after bluezero has
# come up and claimed the adapter -- measured at ~1.5 s on the dev Pi Zero
# 2W, but the fixed 2 s sleep above raced it on a cold first install.
echo "    waiting for the LE advertisement"
ACTIVE_INSTANCES=""
for _ in $(seq 1 "$ADVERT_POLL_TRIES"); do
    ACTIVE_INSTANCES="$(busctl get-property org.bluez "/org/bluez/$ADAPTER" \
        org.bluez.LEAdvertisingManager1 ActiveInstances 2>/dev/null \
        | awk '{print $2}')"
    if [[ -n "$ACTIVE_INSTANCES" && "$ACTIVE_INSTANCES" != "0" ]]; then
        break
    fi
    sleep 1
done
if [[ -z "$ACTIVE_INSTANCES" || "$ACTIVE_INSTANCES" == "0" ]]; then
    echo "    FAIL: no active LE advertisement after ${ADVERT_POLL_TRIES}s" >&2
    echo "    (ActiveInstances=${ACTIVE_INSTANCES:-unreadable} on $ADAPTER)" >&2
    echo "    Check: journalctl -u zeropi-display -u bluetooth -n 50" >&2
    FAIL=1
else
    echo "    LE advertisement active (ActiveInstances=$ACTIVE_INSTANCES)"
fi

if [[ "$FAIL" -ne 0 ]]; then
    echo "==> Self-check FAILED -- see above" >&2
    exit 1
fi

echo "==> Self-check passed. zeropi-display is provisioned and running."
