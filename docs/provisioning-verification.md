# Provisioning verification (2026-09-05)

Hardware verification of `pi/install.sh` for ticket
[#11](https://github.com/peterderkoala/zeropi.display/issues/11), the last
open ticket on map [#7](https://github.com/peterderkoala/zeropi.display/issues/7).
`docs/e2e-verification.md` verified the *round trip*; this verifies the
*provisioning path* that gets a Pi to the point of being able to make it.

**Result: pass, after two real defects in `install.sh` that only a
from-scratch run could surface.** 23/23 round trips, including one
immediately after a reboot and one immediately after a `bluetoothd`
restart, both with the Pi untouched in between.

## Target

The dev Pi (`192.168.4.108`, Debian 13 trixie, Python 3.13.5, aarch64,
BlueZ `5.82-1.1+rpt2`) — **not** a fresh SD card; no spare was available.
Instead the hand-applied state was **torn down** before each run, to
simulate a stock image:

```bash
systemctl disable --now zeropi-display
rm -rf /etc/systemd/system/bluetooth.service.d \
       /opt/zeropi-display \
       /etc/systemd/system/zeropi-display.service
sed -i '/^ControllerMode = le$/d' /etc/bluetooth/main.conf
systemctl daemon-reload && systemctl restart bluetooth
apt-get -y remove python3-dbus python3-venv
rm -rf ~pi/.local/lib/python3.13/site-packages/bluezero*
```

Teardown was verified before each run (no drop-in, no `ControllerMode`
line, no `/opt/zeropi-display`, `import dbus` → `ModuleNotFoundError`).

### What a torn-down dev Pi still does not prove

Read this before treating `install.sh` as proven on stock hardware:

- **`python3-gi` was never removed.** It ships with the image and apt
  reported `already the newest version` on every run, so the install
  path for a system *without* it is still unexercised. It is now named
  explicitly in the apt line rather than assumed, but that line has only
  ever run as a no-op for this package.
- **BlueZ was never downgraded**, so the version-floor `FAIL` branch is
  untested on hardware.
- **A first boot's own state** — regenerated machine-id, first-boot
  `bluetoothd` defaults, an unexpanded filesystem, the Pi OS first-run
  wizard — is not reproduced by removing packages from a running system.
- **`/etc/bluetooth/main.conf` was edited, not restored from the package.**
  The teardown deleted the `ControllerMode = le` line; the rest of the file
  carries whatever earlier sessions left in it.

None of these blocked the verification, and none are believed to be
load-bearing. Confirming them needs a genuinely fresh card.

## Defects found and fixed

Both were invisible on the already-provisioned dev Pi, which is exactly
why #11 asked for a from-scratch run.

### 1. The venv could not install `bluezero` at all (fatal)

`install.sh` exited 1 on its first from-scratch run:

```
ERROR: Failed to build 'PyGObject' when installing build dependencies for pygobject
  ../cairo/meson.build:31:12: ERROR: Dependency "cairo" not found (tried pkg-config)
```

`bluezero` depends on PyGObject and dbus-python, both C extensions. In a
plain `python3 -m venv` the venv is sealed off from
`/usr/lib/python3/dist-packages`, so pip cannot see the apt-installed
`python3-gi` / `python3-dbus` and resolves them from sdists instead —
PyGObject → pycairo → a cairo dev-header build that a stock image has no
toolchain for.

This never showed on the dev Pi because `bluezero` had been hand-installed
into `~pi/.local` against the *system* interpreter, where `gi` and `dbus`
were already importable. The venv decision from
[#8](https://github.com/peterderkoala/zeropi.display/issues/8) is what
introduced the isolation, and nothing had run it on a clean machine.

**Fix**: create the venv with `--system-site-packages`, and name
`python3-gi` explicitly in the apt line. pip then treats the system
PyGObject/dbus-python as satisfying the requirements and installs only
`bluezero` into the venv — which still honours #8's "bluezero into a venv"
decision.

An existing venv is now checked for the flag and **rebuilt** if it lacks
it, rather than reused. Without that, the maintainer's already-provisioned
Pi (and anyone who ran the pre-fix script) would keep a venv that can never
satisfy `requirements.txt`. Verified by planting a flagless venv and
re-running: `existing venv lacks system site-packages; rebuilding it`,
then `bluezero import OK`.

### 2. The advertisement self-check was a warning, and raced

`install.sh` shipped with the `ActiveInstances` check as a soft `WARN`,
with a note asking #11 to confirm the format and promote it. It fired on
the first successful run:

```
WARN: no active LE advertisement seen (	ActiveInstances: 0x00 (0))
```

— a false alarm. The advertisement *was* coming up, just not within the
fixed `sleep 2`: `bluetoothd` had been restarted moments earlier, and
`receive.py` only registers once bluezero has claimed the adapter.
Measured at **~1.5 s** after a plain `systemctl restart zeropi-display`
(3/3 runs), but longer on a cold install racing a `bluetoothd` restart.

**Fix**: promoted to a hard `FAIL`, polled once a second for up to 20 s
instead of sampled once, and read over D-Bus rather than scraped:

```bash
busctl get-property org.bluez /org/bluez/hci0 \
    org.bluez.LEAdvertisingManager1 ActiveInstances   # -> "y 1"
```

`bluetoothctl show | grep ActiveInstances` **is** parseable — the format
is `\tActiveInstances: 0x01 (1)` — but bluetoothctl interleaves
colourised async `[CHG] Controller ... ActiveInstances: ...` lines with
its own property block, so a grep can return two lines with different
values. `busctl` returns exactly `y <n>`.

## What was run

Code delivery to the Pi was `scp -r pi/ pi@192.168.4.108:~/zeropi-display-pi`.
Map #7 lists the update mechanism as still unspecified; this verification
did not settle it.

### Step 1 — provisioning, and idempotency

| Run | State before | Result |
| --- | --- | --- |
| 1 | torn down | **FAIL** (exit 1) — PyGObject build, defect 1 |
| 1′ | torn down, script fixed | pass, exit 0, but `WARN` — defect 2 |
| 1″ | torn down, both fixed | **pass**, exit 0, `ActiveInstances=1` |
| 2 | already provisioned | **pass**, exit 0 |
| 3 | already provisioned | **pass**, exit 0 |
| 4 | flagless venv planted | **pass**, exit 0, venv rebuilt |

Idempotency held across the re-runs — after three consecutive runs,
`main.conf` carried exactly **one** `ControllerMode = le` line and the
drop-in exactly **3** lines, with the unit enabled and active.

### Step 2 — reboot, then an unattended round trip

`systemctl reboot`, then nothing on the Pi. It came back in ~15 s with:

```
bluetooth       active   /usr/libexec/bluetooth/bluetoothd --noplugin=midi,sap,avrcp
zeropi-display  active
ActiveInstances y 1
```

`desktop/push.py` from the Desktop, with no intervention:

```
Connected to B8:27:EB:7C:97:0F (negotiated MTU: 517)
Ack: {'status': 'ok', 'received_at': '2026-09-05T11:48:31.914431+00:00'}
```

Reading persisted: `(1, '2026-09-05', 12345, 'test message', '...')` in a
`data.db` that `init_db()` had created on the service's first start.

### Step 3 — `bluetoothd` restart, then a second unattended round trip

`systemctl restart bluetooth`. `BindsTo=bluetooth.service` behaved exactly
as [#9](https://github.com/peterderkoala/zeropi.display/issues/9) intended
— the receiver was stopped and restarted with the daemon, settling in
**~2 s**:

```
Stopping zeropi-display.service...
Started zeropi-display.service - zeropi-display BLE receiver.
```

Second push, again untouched: `{'status': 'ok', ...}`. **This is the
regression that map #7 existed to close** — before #9, `receive.py` went
silently dark on a `bluetoothd` restart until someone SSHed in.

### Step 4 — success rate

20 consecutive `push.py` runs, 1 s apart:

**20 / 20 pass.** Wall-clock 6,953–13,035 ms, median ~8.0 s (dominated by
BLE scan time, not the write). No failures, so no failure modes to
characterise.

Including step 2's and step 3's pushes and one after the venv rebuild:
**23 / 23 round trips**, matched by 23 rows in `readings`.

Journals for the whole session, since boot:

- `bluetooth`: **0** lines matching `segfault|SIGSEGV|status=11|core-dump`
- `zeropi-display`: **0** error/traceback lines, 2 `Started` entries (boot,
  plus the step-3 `bluetoothd` restart) — no `Restart=always` flapping

For contrast, `docs/e2e-verification.md`'s pre-fix baseline was **0/10**
with `bluetoothd` segfaulting on every incoming connection.

## Conclusion

Map #7's destination is met: a stock-ish Pi reaches an unattended,
reboot-surviving install through one repeatable path, and accepts a Payload
round trip with no manual intervention. The remaining gap is the fresh-card
caveat above, not the provisioning logic.
