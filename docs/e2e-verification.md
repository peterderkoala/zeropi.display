# Milestone-1 E2E verification (2026-09-04, retry)

Retry of the end-to-end round trip that issue #6 left open. The previous
attempt got the write→persist path working but could not get a repeatable
pass: connects were roughly coin-flip, writes intermittently failed with
`GATT Protocol Error: Unlikely Error`, and the working hypothesis was a
possible hardware/firmware limit of the Pi Zero 2W's Bluetooth chip.

**Result: fully clean pass. The hardware is fine — `bluetoothd` on the Pi
was segfaulting.**

## What was run

Pi (`192.168.4.108`) ran `/opt/zeropi-display/receive.py`; the Desktop ran
`desktop/push.py` from a local venv (`bleak` 3.0.2, Python 3.12).

1. **Baseline reproduction** — 10 consecutive `push.py` runs to get a real
   success-rate number instead of anecdotes.
2. **Root-cause hunt** — inspected the Pi's `bluetoothd` journal, adapter
   advertising state (`bluetoothctl show`), and the peripheral's log.
3. **Fix + re-run** — 18 happy-path runs, 4 malformed-Payload cases, and a
   restart/reconnect case, then a dump of the `readings` table.

## What was wrong

**`bluetoothd` crashed with SIGSEGV on every incoming connection**, always
immediately after `profiles/midi/midi.c:midi_io_initial_read_cb() MIDI I/O:
Failed to read initial request`. The MIDI plugin probes each new connection
and segfaults on the Desktop's LE-only connection.

That single crash explains all three symptoms the previous session chased
separately:

- **Failed / stalled connects** — `Device1.Connect()` times out because the
  daemon serving it dies mid-call.
- **"Coin-flip" reliability** — the crash killed the LE advertisement
  (`ActiveInstances` drops to 0) and orphaned `receive.py`'s D-Bus
  registration, so *every subsequent run* failed at the scan step with "no
  device advertising service …". Only the first run or two after a fresh
  start ever had a chance, which reads as random flakiness from the Desktop
  side. In the 10-run baseline, runs 1–2 reached the Pi and runs 3–10 found
  nothing at all.
- **Sporadic write errors** — writes racing a dying daemon.

The previous session had *tried* to disable the plugin with
`DisablePlugins = midi,deviceinfo` in `/etc/bluetooth/main.conf`, but that
is not a valid `main.conf` key — BlueZ 5.82 logged `Unknown key
DisablePlugins for group General` at every start and loaded MIDI anyway.
Plugin exclusion is a `bluetoothd` command-line option, not a config key.

## Fix applied on the Pi (system config, not in this repo)

Removed the ineffective `DisablePlugins` line from `/etc/bluetooth/main.conf`
(`ControllerMode = le` from the previous session stays — it is still needed)
and added a systemd drop-in at
`/etc/systemd/system/bluetooth.service.d/noplugin.conf`:

```ini
[Service]
ExecStart=
ExecStart=/usr/libexec/bluetooth/bluetoothd --noplugin=midi,sap,avrcp
```

`sap` and `avrcp` are excluded too: both failed to register at every start
(`Operation not permitted`) and are BR/EDR profiles this LE-only project has
no use for. The journal now confirms `Excluding (cli) sap / avrcp / midi`.

## Fix applied in this repo

`pi/receive.py` was out of sync with the file actually deployed to the Pi:
the deployed copy defers the Ack notification onto the next event-loop
iteration via `async_tools.add_timer_ms(0, ...)` instead of notifying from
inside the write's own D-Bus call. That fix was made on the Pi during the
previous session but never committed. It is now in the repo, and the repo
and deployed files are byte-identical.

## Results after the fix

| Case | Runs | Result |
| --- | --- | --- |
| Happy path (`desktop/push.py`) | 18 | 18/18 `{"status": "ok"}`, MTU 517 negotiated every time |
| Malformed Payload | 4 | 4/4 `{"status": "error", "reason": …}`, no Reading written |
| Restart Pi script, re-push | 1 | reconnect + `ok` with no Desktop-side intervention |

Baseline for comparison: **0/10** before the fix.

Malformed-Payload reasons returned by the Pi:

- not JSON at all → `Expecting value: line 1 column 1 (char 0)`
- JSON but not an object (`[1,2,3]`) → `expected a JSON object, got list`
- object missing fields → `missing field(s): usage_tokens, oneliner`
- invalid UTF-8 → `'utf-8' codec can't decode byte 0xff in position 0: …`

Persistence confirmed in `/opt/zeropi-display/data.db`: 22 Readings total,
21 added this session, none from the four rejected writes. ADR 0001's
single-write assumption holds — the ~73-byte Payload fits comfortably in
one write at MTU 517.

## Notes for next time

The provisioning and unattended-run items below are now tracked on
[Map: Target installation reproducible from scratch (#7)](https://github.com/peterderkoala/zeropi.display/issues/7).


- The Pi's Bluetooth stack is **not** the bottleneck it appeared to be; do
  not design around a presumed Pi Zero 2W BLE limitation.
- When the Desktop reports "no device advertising service …", check
  `bluetoothctl show | grep ActiveInstances` on the Pi first. `0` means the
  advertisement is gone, which usually means `bluetoothd` restarted — check
  `journalctl -u bluetooth` for a crash before suspecting radio issues.
- `receive.py` must be restarted after any `bluetoothd` restart; it does not
  re-register its GATT service or advertisement on its own. A systemd unit
  with `Requires=`/`After=bluetooth.service` and `Restart=always` would make
  this self-healing — worth doing before the display runs unattended.
- `push.py`'s `finally: stop_notify(...)` masks the real exception when a
  connect fails (it raises "Service Discovery has not been performed yet"
  over the top of the original error), which cost time during diagnosis.
