# Pi Zero E-Ink Display — BLE Prototype Concept

## Goal
Build a small e-ink display (reusing existing pwnagotchi Pi Zero + Waveshare
e-ink HAT hardware) that shows a daily summary: Claude usage. Longer-term data source for the one-liner/usage stat
is local Claude Code session data (JSONL logs) rather than a separate paid
API key. Local SQLite DB must save the usage to display long term graph and
usage avg in the future.

**Current phase: prove out a working Bluetooth link between a desktop
machine and the Pi. No e-ink rendering, no real data parsing, no case/UPS
yet — just get data reliably from desktop → Pi over BLE.**

See `CONTEXT.md` for the Desktop/Pi/Payload/Reading/Ack vocabulary used
below, and `docs/adr/` for the reasoning behind the single-write and
Pi-side-persistence decisions.

## Roles
- **Desktop (BLE central):** owns the real data sources (weather API,
  calendar, and eventually Claude Code's local `~/.claude/projects/*.jsonl`
  logs). Builds a Payload and pushes it to the Pi.
- **Pi Zero (BLE peripheral):** dumb receiver. Advertises a custom GATT
  service, accepts a Payload write, parses and persists it, and reports an
  Ack.

## Suggested stack
- Desktop: Python + `bleak` (cross-platform BLE client)
- Pi: Python + `bluezero` (wraps BlueZ, simpler than raw DBus/bluetoothctl)

## Milestone 1 design (settled)

**BLE service**
- Custom 128-bit UUIDs:
  - Service: `abbac370-5a95-490d-a1fc-921c1c95300d`
  - Write characteristic: `014ca0e2-c76c-4443-a755-e5a1ad25368d`
  - Notify characteristic: `08c89458-52f1-47eb-ab58-f7f7995d8efb`
- MTU negotiated up front; a single write carries the whole Payload (see
  ADR 0001 — no chunking protocol for milestone 1).
- Desktop discovers the Pi by scanning for the advertised service UUID, not
  a hardcoded MAC address.

**Payload & Ack**
- Milestone-1 Payload:
  ```json
  {"date": "2026-09-03", "usage_tokens": 12345, "oneliner": "test message"}
  ```
- The Pi's notify characteristic returns a JSON Ack:
  `{"status": "ok"|"error", "received_at": "<iso8601>", "reason": "..."}`
  (`reason` present only on error).
- Malformed/invalid JSON is logged on the Pi and answered with
  `status: "error"` — never silently dropped.
- A Reading is written to SQLite on the Pi (see ADR 0002) for every
  successfully parsed Payload; a DB write failure also produces
  `status: "error"`, since the Ack reflects the whole receive pipeline, not
  just JSON parsing.
- SQLite table: `readings(id, date, usage_tokens, oneliner, received_at)`.
- Deployed on the Pi under `/opt/zeropi-display/`: the receiver script at
  `/opt/zeropi-display/receive.py`, the database at
  `/opt/zeropi-display/data.db`.

**Operation**
- Desktop script: one-shot push, run manually, exits after the Ack — no
  polling loop yet.
- Pi script: run manually over SSH per test session, not a systemd service
  yet.
- Reconnect/reliability (Pi reboot, Desktop out of range) is verified by
  hand: restart the Pi script, rerun the Desktop script, confirm reconnect.
  No automatic retry/backoff logic required for milestone 1.

**Code layout**
- `desktop/` and `pi/` top-level dirs, each with its own script and its own
  `requirements.txt` (`bleak` vs `bluezero`).
- Confirmed Pi environment (2026-09-04, via SSH to `infrastructure.md`'s
  host): Python 3.13.5, BlueZ 5.82, Debian 13 (trixie) — modern enough for
  `bluezero`, no fallback needed. `python3-dbus` (a `bluezero` dependency)
  is not yet installed on the Pi; install it as part of the implementation
  session.

## Steps
1. Pi advertises the GATT service with the write and notify characteristics
   above.
2. Desktop script scans for the service, connects, and writes the
   milestone-1 test Payload.
3. Pi receives the write, parses JSON, prints `Received: ...`, and returns
   an Ack.
4. Pi saves the Reading to SQLite.
5. Manually confirm round-trip reliability (reconnect after Pi restart,
   Desktop out of range, malformed payload).

## Explicitly out of scope for this prototype
- E-ink rendering (Waveshare driver) — bolt on after BLE link is solid
- Real Claude-usage parsing — stub with hardcoded JSON first
- Power/UPS hardware, enclosure
- Any cloud/API-key based fallback

## Open questions (genuinely deferred, not decided)
- **Final payload schema**: fields once weather/calendar/real-usage sources
  are wired in.
- **Steady-state update cadence**: waits until the e-ink refresh rate is
  decided.
- **Automatic reconnect/retry logic**: a later milestone, once the manual
  happy path above is proven.
- **Where the Claude Code JSONL parsing logic lives**: Desktop-side script
  vs. reusing ccusage-style logic as a reference — out of scope until the
  real usage-parsing milestone.

## Test-Suite

- Desktop == this linux pc where the claude session runs
- Pi == infrastructure.md with live source hardware with fixed ip and ssh enabled
