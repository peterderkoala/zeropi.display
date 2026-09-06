#!/usr/bin/env bash
# pi/install-pi.sh -- provisions a stock Raspberry Pi OS (Debian 13 / trixie)
# Pi to run the zeropi-display BLE receiver unattended.
#
# Normally reached via the repo-root curl bootstrap (install.sh pi), which
# fetches a versioned tarball and runs this script under sudo from the
# staging unpack -- SCRIPT_DIR below resolves correctly there without any
# extra handling, since BASH_SOURCE[0] already points into the unpack. It
# also still runs standalone on the Pi as root (sudo ./install-pi.sh) with
# this repo's pi/ directory present on disk -- ticket #33's local-correctness
# bar, not #35's hardware verification. Safe to re-run on an
# already-provisioned Pi either way.
#
# See issue #7 (map) for why each step exists, #8 for the decisions behind
# this script's shape (local, idempotent, venv-based), and #33 for the
# bootstrap's env contract (ZEROPI_REF/ZEROPI_SHA/ZEROPI_TIMESTAMP, all
# optional -- unset on a standalone run).

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
BOOT_CONFIG="/boot/firmware/config.txt"
SPI_DEV="/dev/spidev0.0"
REBOOT_REQUIRED=0

if [[ $EUID -ne 0 ]]; then
    echo "install-pi.sh must run as root, e.g.: sudo ./install-pi.sh" >&2
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

echo "==> Installing apt dependencies"
# bluezero imports both dbus-python and PyGObject. Both are C extensions;
# building them from a pip sdist needs cairo/girepository dev headers that a
# stock image does not carry, so they come from apt and the venv borrows them
# via --system-site-packages below. python3-gi ships with Raspberry Pi OS, but
# it is named explicitly rather than assumed.
#
# The last four are the e-ink panel's stack. They come from apt for the same
# reason: Debian 13 marks the system Python externally-managed, and the
# vendored driver's own setup.py declares a stale RPi.GPIO dependency that
# pip would resolve into a package broken on current Raspberry Pi OS. The
# driver imports spidev and gpiozero; gpiozero needs lgpio as its pin factory
# on trixie; PIL is for callers that build a frame. See
# pi/waveshare_epd/README.md.
apt-get update -qq
apt-get install -y python3-dbus python3-gi python3-venv \
    python3-spidev python3-gpiozero python3-lgpio python3-pil

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

echo "==> Enabling the SPI bus (the e-ink panel's transport)"
# raspi-config owns the boot-config format and knows where config.txt lives,
# so it is preferred; the append is the fallback for an image without it.
# Either way the kernel only creates $SPI_DEV at boot, so a Pi that had SPI
# off needs a reboot before the panel can be driven -- reported at the end
# rather than failed on, since the BLE service this script's self-check
# covers does not depend on SPI.
if command -v raspi-config >/dev/null 2>&1; then
    # Explicitly tolerated: this runs under `set -e`, and a bare call would
    # abort the whole script here -- before receive.py is even deployed --
    # turning "SPI could not be enabled" into "the BLE receiver was never
    # installed". The panel is the only casualty of a failure; say so and go on.
    if ! raspi-config nonint do_spi 0; then
        echo "    WARNING: raspi-config could not enable SPI. The panel will not" >&2
        echo "    work until it is enabled by hand; the BLE service is unaffected." >&2
    fi
elif [[ -f "$BOOT_CONFIG" ]]; then
    # The -f test is load-bearing: a bare `grep ... || append` would also fire
    # when the file does not exist, creating /boot/firmware/config.txt on an
    # image that actually reads /boot/config.txt -- an edit nothing would apply.
    if ! grep -qE '^\s*dtparam=spi=on' "$BOOT_CONFIG"; then
        printf '\ndtparam=spi=on\n' >> "$BOOT_CONFIG"
    fi
else
    echo "    WARNING: no raspi-config and no $BOOT_CONFIG; cannot enable SPI." >&2
fi
if [[ -e "$SPI_DEV" ]]; then
    echo "    $SPI_DEV present"
else
    echo "    $SPI_DEV not present yet -- SPI was just enabled; reboot needed"
    REBOOT_REQUIRED=1
fi

echo "==> Deploying receive.py to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_DIR/receive.py" "$INSTALL_DIR/receive.py"

echo "==> Deploying the e-ink driver and self-test to $INSTALL_DIR"
# The vendored waveshare_epd package sits next to epd-selftest.py so a plain
# `from waveshare_epd import epd2in13_V4` resolves off the script's own
# directory, with no sys.path handling. rsync --delete would be wrong here:
# the copy is refreshed rather than mirrored, so a stale module from an older
# revision is removed explicitly first.
rm -rf "$INSTALL_DIR/waveshare_epd"
cp -r "$SCRIPT_DIR/waveshare_epd" "$INSTALL_DIR/waveshare_epd"
cp "$SCRIPT_DIR/epd-selftest.py" "$INSTALL_DIR/epd-selftest.py"

echo "==> Stamping VERSION"
# ZEROPI_REF/ZEROPI_SHA/ZEROPI_TIMESTAMP come from the curl bootstrap
# (install.sh), which resolves the fetched ref to a commit sha before
# handing off here -- see #33. A standalone local run (no bootstrap) has no
# sha to report; fall back to the checked-out git commit, if any, so the
# file still says something rather than nothing.
VERSION_SHA="${ZEROPI_SHA:-}"
VERSION_REF="${ZEROPI_REF:-}"
VERSION_TIMESTAMP="${ZEROPI_TIMESTAMP:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
if [[ -z "$VERSION_SHA" ]]; then
    VERSION_SHA="$(git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || echo "unknown")"
    VERSION_REF="${VERSION_REF:-local}"
fi
cat > "$INSTALL_DIR/VERSION" <<EOF
sha=$VERSION_SHA
ref=$VERSION_REF
installed_at=$VERSION_TIMESTAMP
EOF

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

# The panel's imports are checked, but epdconfig itself is deliberately NOT
# imported here: it builds gpiozero objects for BCM 17/25/18/24 and raises
# the power pin at module scope, so importing it is already touching the
# hardware. Provisioning should not drive the panel; epd-selftest.py is the
# thing that does, on purpose and by hand.
# lgpio is imported explicitly, not left implicit in gpiozero: importing
# gpiozero does not instantiate a pin factory, so without this the check
# passes on a Pi where the driver cannot actually drive a pin.
#
# stderr is kept, not sent to /dev/null. Discarding it would throw away the
# one line saying *which* module failed and why -- the same mistake #12 fixed
# in push.py three commits ago (bdcedb2).
if IMPORT_ERR="$(sudo -u "$RUN_AS_USER" "$VENV_DIR/bin/python" \
        -c 'import spidev, gpiozero, lgpio, PIL' 2>&1)"; then
    echo "    e-ink python stack importable"
else
    echo "    FAIL: the e-ink stack is not importable from $VENV_DIR" >&2
    echo "    ${IMPORT_ERR:-(no error output)}" >&2
    FAIL=1
fi

for artefact in "$INSTALL_DIR/waveshare_epd/epd2in13_V4.py" \
                "$INSTALL_DIR/epd-selftest.py"; do
    if [[ ! -f "$artefact" ]]; then
        echo "    FAIL: $artefact was not deployed" >&2
        FAIL=1
    fi
done

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

if [[ "$REBOOT_REQUIRED" -eq 1 ]]; then
    echo
    echo "==> ACTION NEEDED: reboot to bring up SPI, then prove the panel:"
    echo "    sudo reboot"
else
    echo "==> To prove the e-ink panel:"
fi
echo "    $VENV_DIR/bin/python $INSTALL_DIR/epd-selftest.py"
