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

echo "==> Installing apt dependencies (python3-dbus, python3-venv)"
apt-get update -qq
apt-get install -y python3-dbus python3-venv

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
if [[ ! -d "$VENV_DIR" ]]; then
    sudo -u "$RUN_AS_USER" python3 -m venv "$VENV_DIR"
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

# Best-effort: warn rather than fail here, since the exact bluetoothctl
# output format wasn't verified against real hardware while writing this
# script. Ticket #11 should confirm this parses correctly and promote it
# to a hard failure if so.
ACTIVE_INSTANCES_LINE="$(bluetoothctl show 2>/dev/null | grep -i ActiveInstances || true)"
if [[ -z "$ACTIVE_INSTANCES_LINE" || "$ACTIVE_INSTANCES_LINE" == *"0x00"* ]]; then
    echo "    WARN: no active LE advertisement seen (${ACTIVE_INSTANCES_LINE:-no ActiveInstances line})" >&2
fi

if [[ "$FAIL" -ne 0 ]]; then
    echo "==> Self-check FAILED -- see above" >&2
    exit 1
fi

echo "==> Self-check passed. zeropi-display is provisioned and running."
