# Handoff — zeropi.display

## Where things stand

**Milestone 1 (BLE prototype) works on real hardware.** The Desktop pushes a
Payload over BLE, the Pi parses it, persists a Reading to SQLite, and returns
an Ack — verified 18/18 on the happy path, plus all four malformed-Payload
cases and reconnect-after-restart. Full write-up:
`docs/e2e-verification.md`.

The catch: it works because of **system config applied by hand over SSH**,
which exists nowhere executable. That is what the current map addresses.

- Design/concept: `pi-eink-ble-concept.md` (repo root) — settled BLE service
  shape, Payload/Ack format, SQLite schema, UUIDs, deployment path.
- Domain glossary: `CONTEXT.md` (Desktop, Pi, Payload, Reading, Ack,
  One-liner)
- ADRs: `docs/adr/0001-single-write-payload-no-chunking.md`,
  `docs/adr/0002-readings-persisted-on-pi.md`
- Agent-skill config: `docs/agents/issue-tracker.md`, `docs/agents/domain.md`

## Maps

### Current: [Target installation reproducible from scratch (#7)](https://github.com/peterderkoala/zeropi.display/issues/7)

Get a stock Pi to an unattended, reboot-surviving install with one
repeatable provisioning path. The map body carries the full inventory of
hand-applied Pi state — read it before touching the Pi.

Tickets, in dependency order:

1. [Settle the provisioning approach (#8)](https://github.com/peterderkoala/zeropi.display/issues/8)
   — `wayfinder:grilling`. **Frontier, unblocked, unclaimed. Start here.**
   Decides install.sh vs checklist, on-Pi vs over-SSH, idempotency, and the
   `bluezero` install method (which the systemd unit's `ExecStart` depends
   on).
2. [systemd unit for receive.py (#9)](https://github.com/peterderkoala/zeropi.display/issues/9) — blocked by #8
3. [Implement pi/install.sh (#10)](https://github.com/peterderkoala/zeropi.display/issues/10) — blocked by #8, #9
4. [Verify provisioning from scratch (#11)](https://github.com/peterderkoala/zeropi.display/issues/11) — blocked by #10

Unlike map #1, **#8 is a real decision ticket** — grill it out rather than
jumping to a script.

### Previous: [Milestone 1 BLE prototype (#1)](https://github.com/peterderkoala/zeropi.display/issues/1)

Tickets #2–#5 closed. [#6 (verify round trip)](https://github.com/peterderkoala/zeropi.display/issues/6)
is **still open but its acceptance criteria are now met** — see the
verification session comment and `docs/e2e-verification.md`. Close it (and
with it map #1's destination) unless something else is wanted from it first.

## Hard-won facts — do not relearn these

- **The Pi Zero 2W's BLE hardware is fine.** An earlier session suspected a
  chip/firmware limit behind "coin-flip" reliability. It was `bluetoothd`
  segfaulting in its MIDI plugin on every incoming LE connection. Do not
  design chunking or retry logic around a presumed hardware limitation.
- **`DisablePlugins` in `/etc/bluetooth/main.conf` does nothing** — not a
  valid BlueZ 5.82 key. Plugin exclusion is a `bluetoothd` command-line
  option; see the drop-in described in #7.
- **`receive.py` does not survive a `bluetoothd` restart** (ticket #9). When
  the Desktop says "no device advertising service …", check
  `bluetoothctl show | grep ActiveInstances` on the Pi and
  `journalctl -u bluetooth` for a crash before suspecting the radio.
- `push.py`'s `finally: stop_notify(...)` masks the real exception when a
  connect fails, reporting "Service Discovery has not been performed yet"
  over the top of the actual error. Untracked wart; costs diagnosis time.

## Environment notes

- Dev Pi: `192.168.4.108`, creds in `infrastructure.md` (gitignored).
  `sshpass` is installed in this dev environment for non-interactive SSH;
  the sudo password is the same as the SSH password.
- Pi: Debian 13 (trixie), Python 3.13.5, aarch64, BlueZ `5.82-1.1+rpt2`,
  `python3-dbus` `1.4.0-1`, `bluezero` `0.9.1` in `~pi/.local`.
- Desktop: `bleak` 3.0.2 in a local `.venv/` (gitignored, not committed) —
  `uv venv .venv && uv pip install -r desktop/requirements.txt`.
- Labels `wayfinder:map`, `wayfinder:task`, `wayfinder:grilling` exist.
  `gh` CLI is authenticated as `peterderkoala`.

## Suggested skills for the next session

- **`mattpocock-skills:wayfinder`** with map #7 — claim #8, resolve,
  record, advance the frontier.
- **`mattpocock-skills:grilling`** for #8 itself, since it is a genuine
  open decision rather than a build step; record the outcome as an ADR via
  **`mattpocock-skills:domain-modeling`** if it carries lasting rationale.
