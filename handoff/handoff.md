# Handoff — zeropi.display

## Where things stand

> **If you are here to implement rendering — the current work — read
> [For the next session](#for-the-next-session-implementing-map-59) below
> first. The rest of this section is what is already true.**

**The usage pipeline is DONE and hardware-verified (2026-09-09).** Map #41's
destination is reached and the map is closed: `docs/spec-usage-pipeline.md` is
implemented, unit-tested (**181 passing**) and proven end-to-end against the
dev Pi and the maintainer's real Claude Code logs. Real Daily and Gauge
Payloads cross the link; the Pi persists, gates and stub-redraws per spec; the
resident `systemd --user` service does it unattended. Full run:
`docs/usage-pipeline-verification.md` — read it before touching either end.

Three things from that run you would otherwise rediscover the hard way:

- ⚠ **Anything this repo runs under systemd needs
  `Environment=PYTHONUNBUFFERED=1` in its unit.** Both services log with
  `print()`, and under systemd stdout is a journal socket, so CPython
  block-buffers it: a full 10-Reading Batch produced **zero** journal lines on
  the Pi — including the `render:` line that is §8.6's entire deliverable —
  and the Desktop service was silent through its whole startup Batch. Fixed in
  both units (`31bb8e7`, `94665e3`). It is invisible from a terminal, so it
  will come back with the next unit file anyone writes.
- ⚠ **The curl bootstrap resolves `dev` → sha through the unauthenticated
  GitHub API**, rate-limited to 60/hr per egress IP. When that is exhausted the
  install dies at its first step with a bare `curl: (22) ... 403`, which reads
  like a broken script. Check `curl -i https://api.github.com/rate_limit` from
  the Pi before debugging anything else.
- ⚠ **ADR-0010's freshness claim was overstated, and is now fixed.** A frame
  drawn at 268 s of Gauge Age stayed on the panel until it was ~570 s old,
  because the fallback is itself floor-gated. **Resolved by #55 the same day**:
  the ADR is amended with two honest bounds, and the throttle drops to 120 s.
  Do not re-open it from this bullet — read ADR-0008 and ADR-0010, both amended
  2026-09-09.

**E-ink rendering is SPECIFIED, not built.** `docs/spec-eink-rendering.md`
(`bf6be4b`) is binding — map #51's destination, charted and finished in one
day, then closed. It says what the panel draws and how, in pixel geometry a
human approved on real glass. `receive.py` still does not import the driver, so
nothing reaches the panel yet.

**The implementation map is charted**:
[Map: Make the panel draw](https://github.com/peterderkoala/zeropi.display/issues/59),
opened against that spec the way #41 was opened against #13. **Four tickets are
takeable in parallel right now.** Read the spec first; it names its own
required reading and says outright that THIS FILE is not authoritative.

⚠ **Weather, calendar and the One-liner were dropped from the project**
(maintainer's call, 2026-09-09, `c3aa086`). zeropi.display is a Claude Code
usage display and nothing else. `CONTEXT.md` no longer defines **One-liner**;
`pi-eink-ble-concept.md` and `CLAUDE.md` are rewritten, with the concept
document's milestone-1 sections kept as the historical record they are. Do not
reintroduce them from an old document.

⚠ **The Pi has no fonts at all** — `/usr/share/fonts` does not exist, `find`
returns 0 files. PIL 11.1.0 is installed and working, but #38's settled mock
renderer loads DejaVu by absolute path and therefore **cannot run on the Pi**.
Nothing noticed because nothing has ever drawn text there. It is the rendering
map's own ticket.

**Milestone 1 (BLE prototype) works on real hardware.** The Desktop pushes a
Payload over BLE, the Pi parses it, persists a Reading to SQLite, and returns
an Ack — verified 18/18 on the happy path, plus all four malformed-Payload
cases and reconnect-after-restart. Full write-up:
`docs/e2e-verification.md`.

That system config is **no longer hand-applied**: `pi/install-pi.sh` owns it
(renamed from `pi/install.sh` by #33), and #11 verified the whole
provisioning path from a torn-down Pi (see
`docs/provisioning-verification.md`). As of #33, it's also no longer reached
by hand-run `scp` — a repo-root `install.sh` curl bootstrap fetches a
versioned tarball and delegates to it. As of #34,
`desktop/install-desktop.sh` is built too, and **as of #35, both roles are
hardware-verified through the documented one-liner** — see
`docs/curl-delivery-verification.md`. #39's e-ink panel provisioning has
since been **merged into `dev`**, so `install-pi.sh` now does more than #35
exercised: that verification ran before the merge, and **the panel steps
still need their own hardware pass** (#40).

**The e-ink panel draws on real hardware.** The Waveshare V4 driver is
vendored at a pinned upstream commit in `pi/waveshare_epd/`,
`pi/install-pi.sh` provisions it (SPI + `python3-{spidev,gpiozero,lgpio,pil}`
from apt + deployment), and `pi/epd-selftest.py` is the by-hand bench check.
Verified 2026-09-06 on the dev Pi: **full refresh 2.29 s** (ADR-0007 assumed
~3 s), framebuffer exactly 4000 bytes, `epd-selftest.py` 6.7 s end to end.
Write-up: `docs/eink-driver-verification.md`. This is the first work past the
BLE-only scope line, and it stops deliberately short of rendering —
`receive.py` does not import the driver.

A review after that run caught two defects, fixed in `ca68517`: `epd.init()`
sat outside the `try`, so the `finally` that sleeps the panel did not cover
the phase where it is already powered (an ADR-0007 violation), and the
`raspi-config` call was bare under `set -e`, so a failure to enable SPI would
have aborted provisioning before `receive.py` was deployed. **If you write
any further panel code, the `sleep()`-on-every-path property is the one to
re-check** — it is easy to get wrong and expensive to get wrong.

Read that doc's last section before building on this. **The driver is
verified; the provisioning of the driver is not.** A parallel session was
mid-teardown on the dev Pi, so the run went through a scratch directory with
SPI enabled at runtime (`dtparam spi=on`, which does not survive a reboot)
rather than through `install-pi.sh`. Its panel steps have never executed. The
`PWR_PIN`-on-BCM-18 caveat in `pi/waveshare_epd/README.md` also still stands,
and nobody has actually looked at the glass.

- Design/concept: `pi-eink-ble-concept.md` (repo root) — settled BLE service
  shape, Payload/Ack format, SQLite schema, UUIDs, deployment path.
- Domain glossary: `CONTEXT.md` — **rewritten by #19 and now binding.**
  Desktop, Desktop Id, Pi, Payload (Daily/Gauge), Batch, Ack, Reading,
  Coverage Start, Usage, Gauge, Project Key, Project Label, Window, **Limit
  Window** (added by #25), **Reset Countdown** and **Gauge Age** (added by
  #37), **Historic View** (added by #38), Cost Complete, One-liner.
- ADRs: `docs/adr/0001` (**superseded by 0003**), `0002` readings-on-the-Pi,
  `0003` one-write-per-Reading, `0004` dedup-winner-rank, `0005`
  Desktop-store-is-archive-of-record, `0006` wipe-on-Desktop-Id-change,
  `0007` full-refresh-only-no-two-speed, `0008` pi-enforces-the-redraw-floor
  (its unsatisfied clock dependency now **resolved by 0009**, and amended by
  0010 for the daily keep-alive), `0009` pi-is-given-durations-not-timestamps,
  `0010` an-expired-gauge-is-not-drawn.
- Agent-skill config: `docs/agents/issue-tracker.md`, `docs/agents/domain.md`

## For the next session: implementing map #59

**The job**: make the panel draw, per `docs/spec-eink-rendering.md`. The map is
[#59](https://github.com/peterderkoala/zeropi.display/issues/59); four of its
seven tickets are takeable in parallel right now.

**Read in this order, and stop when you have what your ticket needs:**

1. Your ticket body — it names the exact spec sections and quotes the numbers.
2. `docs/spec-eink-rendering.md` — binding, and it names its own required
   reading. **§11 supersedes three clauses of `spec-usage-pipeline.md`**; do
   not read those as current.
3. The reference implementation for your seam, on an unmerged branch (below).
4. This file only for environment facts. **The spec says outright that this
   file is not authoritative.**

**The reference renderers already exist. Port them; do not redesign them** —
their geometry is what a human approved on glass, pixel by pixel:

| Branch | What it holds |
|---|---|
| `prototype/historic-view` | `desktop/historic_prototype.py` — the Historic View and empty frame, plus the four rejected candidates |
| `prototype/gauge-glass-fix` | `desktop/gauge_settled.py` — the Gauge frame with #57's corrections |
| `bench/render-blocking` | the event-loop measurements and the timing probe |
| `research/eink-fonts` | the font facts, with rendered samples |

**The five things most likely to bite, none of them in the spec's own voice:**

1. ⚠ **Nothing may block the bluezero event loop for more than ~5 s.** That is
   BlueZ's write timeout, not our 10 s Ack timeout, and a full panel cycle is
   **4.35 s**. This is why the refresh runs on a worker thread. Overrunning
   raises `GATT Protocol Error: Unlikely Error` — the same signature milestone
   1 spent a session chasing — *after* the Pi has already persisted the
   Reading, so the two ends then disagree silently.
2. ⚠ **The Pi has no fonts.** `/usr/share/fonts` does not exist. Until
   [#64](https://github.com/peterderkoala/zeropi.display/issues/64) lands,
   anything drawing text on the Pi dies at `ImageFont.truetype()`.
3. ⚠ **Build images directly in mode `"1"`.** Greyscale-then-convert takes a
   different FreeType path and produces different letterforms, so what you
   review is not what the panel shows.
4. ⚠ **`epdconfig` claims GPIO on import**, and `receive.py` must stay
   importable with no panel, no SPI and no bluezero — the suite depends on it.
   The import belongs inside the worker.
5. ⚠ **Every panel cycle must end in `epd.sleep()`**, with `init()` *inside*
   the guarded region. A review already caught this exact mistake once
   (`ca68517`). The context manager exists to make it structural.

**Definition of done for the map**: the panel draws every frame, on the dev Pi,
with a human looking at it — plus the throttle drop and provisioning, so it is
reproducible on a fresh Pi rather than true only on this one. Ticket
[#66](https://github.com/peterderkoala/zeropi.display/issues/66) carries the
first thing to check at the bench: **re-confirm the 13 px text floor with text
rasterised on the Pi itself**, since every frame approved so far was rasterised
on the Desktop and sent as a bitmap.

## Maps

### Current: [Map: Make the panel draw (implement docs/spec-eink-rendering.md)](https://github.com/peterderkoala/zeropi.display/issues/59)

Charted 2026-09-09, straight after #51 closed. **Execution mode** — the "plan,
don't do" default is overridden, as on #41, because the spec's §14 gap check
already closed every judgment call. Tickets are build-and-verify slices.

**Destination**: the panel draws, hardware-verified with a human looking at it.
Three things are inside that and not adjacent to it: `pi/render.py` and its
wiring; the **Desktop throttle drop to 120 s**; and **provisioning**, so the
milestone is reproducible on a fresh Pi rather than true only on this one.

Frontier — **four takeable in parallel**:
[frame builders](https://github.com/peterderkoala/zeropi.display/issues/60),
[the worker + failure handling](https://github.com/peterderkoala/zeropi.display/issues/61),
[the data layer](https://github.com/peterderkoala/zeropi.display/issues/62),
[the throttle drop](https://github.com/peterderkoala/zeropi.display/issues/65).
Then [wiring](https://github.com/peterderkoala/zeropi.display/issues/63) →
[provisioning](https://github.com/peterderkoala/zeropi.display/issues/64) →
[hardware verification](https://github.com/peterderkoala/zeropi.display/issues/66)
(⚠ HITL, needs the maintainer at the bench).

⚠ **[#61](https://github.com/peterderkoala/zeropi.display/issues/61) is the
subtle one** — read spec §8 and `bench/render-blocking` before starting it.
Everything else is porting a verified reference implementation; that one is
where a wrong choice costs the BLE link.

⚠ **The reference renderers already exist** on `prototype/historic-view` and
`prototype/gauge-glass-fix`. Port them, do not redesign them — their geometry
is the geometry a human approved on glass.


### Closed maps — archived

Their session-by-session logs moved to `handoff/archive/` on 2026-09-09, so
this file stays about live work. **The archive is a record, not guidance**:
some of what it says was superseded later. Read a map's file when you want to
know *why* something was decided, or what was tried and rejected — that
reasoning is not recoverable from the code.

| Map | Reached | Log |
|---|---|---|
| [#51 What the e-ink panel draws, and how](https://github.com/peterderkoala/zeropi.display/issues/51) | `docs/spec-eink-rendering.md` | [`archive/map-51.md`](archive/map-51.md) |
| [#41 Implement the usage pipeline](https://github.com/peterderkoala/zeropi.display/issues/41) | pipeline hardware-verified | [`archive/map-41.md`](archive/map-41.md) |
| [#13 Usage read, pushed, stored](https://github.com/peterderkoala/zeropi.display/issues/13) | `docs/spec-usage-pipeline.md` | [`archive/map-13.md`](archive/map-13.md) |
| [#7 Both ends reproducible from scratch](https://github.com/peterderkoala/zeropi.display/issues/7) | the curl one-liner | [`archive/map-07.md`](archive/map-07.md) |
| [#1 Milestone 1: the BLE prototype](https://github.com/peterderkoala/zeropi.display/issues/1) | the BLE link | [`archive/map-01.md`](archive/map-01.md) |

⚠ **One thing from #41's log is still open and does not belong in an archive**,
so it is promoted into Hard-won facts below: `install-desktop.sh`'s standalone
mode deploys only `push.py`.

## Hard-won facts — do not relearn these

- ⚠ **`install-desktop.sh`'s standalone mode is incomplete, and it is the
  common non-maintainer path.** It deploys only `push.py` into
  `~/.local/share/zeropi-display/` — not `gauge.py`, `usage.py` or
  `service.py`. The shipped `desktop/zeropi-push.service` targets exactly that
  layout, so **the resident service cannot actually run on a standalone
  Desktop** until this is fixed. Flagged by #47, out of its scope, and it
  belongs to [#34](https://github.com/peterderkoala/zeropi.display/issues/34).
  Promoted here from map #41's log when that log was archived, because it is
  the one thing in it that is still open.
- **The Pi Zero 2W's BLE hardware is fine.** An earlier session suspected a
  chip/firmware limit behind "coin-flip" reliability. It was `bluetoothd`
  segfaulting in its MIDI plugin on every incoming LE connection. Do not
  design chunking or retry logic around a presumed hardware limitation.
- **`DisablePlugins` in `/etc/bluetooth/main.conf` does nothing** — not a
  valid BlueZ 5.82 key. Plugin exclusion is a `bluetoothd` command-line
  option; see the drop-in described in #7.
- **`receive.py` not surviving a `bluetoothd` restart is fixed** (ticket #9,
  verified by #11): `BindsTo=bluetooth.service` cycles the receiver with the
  daemon, settling in ~2 s. When the Desktop says "no device advertising
  service …", check the advertisement on the Pi and `journalctl -u bluetooth`
  for a crash before suspecting the radio. Read the advertisement with
  `busctl get-property org.bluez /org/bluez/hci0
  org.bluez.LEAdvertisingManager1 ActiveInstances` (→ `y 1`), **not** by
  grepping `bluetoothctl show` — bluetoothctl interleaves colourised async
  `[CHG] Controller … ActiveInstances` lines with its own property block, so
  a grep can return two lines with different values.
- **A venv on the Pi must be created with `--system-site-packages`** (#11).
  `bluezero` needs PyGObject and dbus-python, both C extensions; a sealed
  venv makes pip build them from sdists and the build dies at `Dependency
  "cairo" not found`. The apt-installed `python3-gi`/`python3-dbus` satisfy
  them instead. `install.sh` rebuilds a flagless venv rather than reusing it.
- **The LE advertisement takes ~1.5 s to appear** after `receive.py` starts,
  and longer on a cold install racing a `bluetoothd` restart. Poll for it;
  do not sample once after a fixed sleep.
- **[#12 (the masked BLE exception) is resolved and closed** — 2026-09-06,
  `dev` (`bdcedb2`). `push_payload()`'s `try/finally: stop_notify(...)` is
  gone; exiting `BleakClient`'s `async with` block already disconnects,
  which implicitly stops notifications (verified against the installed
  `bleak` source, not assumed), so the explicit cleanup that was masking the
  real connect error is simply unnecessary. Also untracked a stray
  `desktop/__pycache__/push.cpython-312.pyc` and added `__pycache__/`/`*.pyc`
  to `.gitignore` — caught by review, unrelated to the fix itself.

Live-gauge facts, established 2026-09-05 (detail in the map body):

- **`~/.claude/sessions/<pid>.json` is a live session registry** —
  ~~real-time~~ `sessionId`, `cwd`, `status` (`busy`/otherwise), `startedAt`,
  `updatedAt`, `kind`. The `sessionId` joins to the session's JSONL. This is
  how you detect the active session; no heuristics needed. **⚠ "Real-time" was
  wrong** — #26 measured `updatedAt` frozen at 467 s during active work. See
  the #26 block below before using any timestamp here.
- **Context size** = the active session's latest assistant entry's
  `input + cache_creation + cache_read`. Measured 210,641 on a live session,
  which **exceeds 200K** — so don't assume the context-window denominator.
- **The gauge percentage is served ready-made**, not computed locally.
  Verified on this machine: `five_hour.utilization: 17`,
  `seven_day.utilization: 2`, with `limit_dollars` / `used_dollars` /
  `remaining_dollars` **all null**. No limit crosses the wire in any unit.
- **⚠ `~/.claude.json` → `cachedUsageUtilization` is a trap** — and worse than
  first measured. It is not maintained **at all** while Claude Code runs:
  **16.5 h stale and not updated once** across a full active session in which
  the live gauge moved 21% → 26%, its cached `seven_day` reading **2% against a
  live 18%**. **Never read this file.**
- **The 5h window's anchor is not locally reconstructible** — it matched
  neither the nearest local event, nor first-activity-after-a-gap, nor a clock
  boundary. **Read `resets_at`; never model the window.** The 7-day window is
  a different shape: a fixed account slot on an exact clock hour.
- **The gauge is account-wide**, computed server-side, so usage from
  claude.ai and other devices is already included. The *historic* rows are
  still this-machine-only.
- **Never read `~/.claude/.credentials.json`.** It sits next to the useful
  files; nothing in this project needs it.

Hand-off facts, established 2026-09-05 by
[#36](https://github.com/peterderkoala/zeropi.display/issues/36):

- **⚠ There is no coupling between Desktop and Pi, at any layer.**
  `pi/receive.py:139` declares `flags=["write"]` / `flags=["notify"]` — not
  `encrypt-write`, not `secure-write` — and there is no pairing, bonding or
  trusted-device list in `install.sh` or `push.py`. The Desktop finds the Pi by
  **scanning for the service UUID**, not a stored address. So "couple the Pi to
  a different Desktop" is currently a **no-op**: run `install-desktop.sh` on the
  new machine and it works. There is nothing to un-couple. This stays
  unauthenticated **by decision**, not oversight — #20 must say so.
- **⚠ `project` is an absolute-path label, not a repo name** —
  `-home-ryzen-git-zeropi-display`. Two Desktops collide on the PK only at the
  *identical* absolute path. A different username is the worse case, not the
  safer one: no overwrite, no error, just a graph that grows a permanent second
  set of series.
- **⚠ The hand-off wipe desyncs against push marks on a hand-BACK.** Pi goes
  A→B fine (B has no marks, pushes everything). Back to A, the Pi wipes on the
  id change while A's store still says everything is pushed — **the Pi sits
  empty and A never resends**, silently. The `wiped` flag on the first Ack
  after a wipe is what closes this; do not drop it as a nicety.
- **`ReceiveState.ack_characteristic` is a class attribute**
  (`pi/receive.py:76`), set by whichever Desktop last subscribed to notify — so
  two *concurrent* Desktops would clobber each other's Ack channel. Moot under
  the sequential shape settled by #36, but it is why concurrent multi-Desktop
  would have cost far more than a schema change.

Live-gauge facts, MEASURED 2026-09-05 by
[#26](https://github.com/peterderkoala/zeropi.display/issues/26) — these
correct earlier entries in this file, so prefer them:

- **⚠ `updatedAt` in the session registry is a status-TRANSITION timestamp, not
  a heartbeat.** This file previously called the registry "real-time"; it is
  not. Measured: `updatedAt` and `statusUpdatedAt` are **exactly equal**, and
  both sat **frozen at 467 s** while the session was actively working with
  `status: "busy"`. **Never test liveness with it** — a freshness threshold
  anywhere near 5 minutes calls a busy session dead. **Liveness is
  `/proc/<pid>`**, cheap and exact.
- **The registry carries `kind: "interactive"`**, which turns #27's headless
  trap from a silent freeze into a **detectable** condition. Filter on it.
- **⚠ Sub-agents do NOT register in the session registry.** One `<pid>.json`
  per interactive CLI process, nothing more — verified across a session that
  ran skills and heavy tool work. So #24's multi-session rule only ever
  discriminates between **separate terminals**. Related: `isSidechain` is
  present on every assistant entry and **false in all 42 sessions on this
  machine**, so the sub-agent-context risk is unverified — keep the filter as
  cheap insurance, not because it has bitten.
- **`.key` files sit alongside the `.json` ones** in `~/.claude/sessions/`.
  Glob narrowly.
- **The gauge moves at ~1.1 percentage points per minute** under heavy Opus 5
  use (27% -> 36% in 8.2 min), so a full 5-hour window is ~91 minutes of
  continuous work. **The trigger therefore fires ~5.5x per 300 s floor** — the
  Desktop-side throttle #25 called a courtesy is doing real work.
- **The Gauge Payload is 279 bytes against the 514 budget**, verbose keys and
  all.
- **⚠ Context-as-a-percentage is a dead readout.** Against #31's 1,000,000
  window, 42 real sessions peaked at **589,408 (59%)**, median peak **15.8%**,
  and **0 of 42** ever passed 900K. The bar is a permanent stub. (The 589,408
  peak does independently **confirm** #31's 1M table — it exceeds any 200K
  window.) **#38 went further and dropped the context readout from the display
  entirely**, narrowing the map's Destination. The **field still crosses the
  wire**, so the active-session machinery below is still spec'd — it exists
  only for this field.
- **⚠ "Dim a stale reading" is not implementable.** The panel is 1-bit
  monochrome: there is no grey. #24 settled dimming anyway. **Resolved by
  [#38](https://github.com/peterderkoala/zeropi.display/issues/38) and
  [ADR-0010](../blob/dev/docs/adr/0010-an-expired-gauge-is-not-drawn.md):**
  nothing is ever marked stale, because an **expired Gauge is not drawn at
  all** — the panel falls back to the Historic View. Do not re-propose hatching,
  a banner or inversion; all three were considered and rejected on the ground
  that a marked-stale number is one you are asking a viewer not to trust.
- **The 5h and 7d windows have visibly different shapes**, confirming #22 by
  observation: `five_hour.resets_at` was 23:40Z — **off any clock hour** —
  while `seven_day.resets_at` was 13:00Z, **exactly on one**.

Cadence and panel facts, established 2026-09-05 by
[#25](https://github.com/peterderkoala/zeropi.display/issues/25):

- **⚠ The Pi has no idea what time it is — and by decision, it never needs
  to.** **Resolved by [#37](https://github.com/peterderkoala/zeropi.display/issues/37)
  and [ADR-0009](../blob/dev/docs/adr/0009-pi-is-given-durations-not-timestamps.md):**
  everything time-shaped crosses the wire as a **duration computed on the
  Desktop**, and the Pi advances it with `time.monotonic()`. Never send the Pi
  an instant it has to interpret. The hardware facts, measured: **no RTC**, and
  — contrary to expectation — **`fake-hwclock` is not installed** either. But
  `systemd-timesyncd` **is enabled and active** out of the box (the OS image
  ships it; `install.sh` does not), and the dev Pi has **WiFi on the LAN**, so
  the clock is usually right. Usually is not a guarantee: boot at `13:47:32`,
  first NTP sync at `13:48:17` — a **45-second window** — with
  `systemd-time-wait-sync` disabled, so nothing holds `receive.py` back through
  it, and a Pi off for a week starts that window a week behind.
- **⚠ Partial refresh is unusable here, and the reason is not obvious.** Two
  vendor statements combine: the panel must not be left in a high-voltage
  state, so every cycle ends in `epd.sleep()` — and deep sleep does **not
  retain RAM**, which destroys the partial-refresh base image. Partial only
  pays off across a burst you stay awake for, and a 300 s floor never produces
  a burst. **Do not re-propose a two-speed scheme**; see
  `docs/adr/0007-full-refresh-only-no-two-speed.md`. #23's **N = 5** bound
  consequently never binds.
- **⚠ Poll the snapshot; do not inotify it.** claude-hud writes
  `rate-limits.json` atomically via temp+rename, so a watch on the *file*
  misses every write — it would have to watch the directory. A 30 s poll of a
  small local JSON file is cheaper than getting that right.
- **Idle is the common state, not the exception.** Per #27 the snapshot only
  advances while an interactive TUI is open, so the panel spends most of the
  day with no live gauge. That is why idle shows the historic view rather than
  blanking — blanking would waste the display's standing purpose for the
  majority of hours.
- **The floor is 300 s and three independent sources agree on it**: #23's
  recommended operating point, claude-hud's `externalUsageFreshnessMs` default
  (300 000 ms), and #24's Pi-side staleness mark. #23's 180 s is headroom, not
  the setting.
- **The likely UPS is a PiSugar 3** (maintainer). Not designed for — mains is
  an explicit assumption — and its RTC is **not** the answer to #37, which
  removed the clock dependency instead. Fitting one is now explicitly **out of
  scope** on map #13; it would improve `journalctl` and would not require
  revisiting ADR-0009.

Rate-limit snapshot facts, established 2026-09-05 by
[#27](https://github.com/peterderkoala/zeropi.display/issues/27):

- **The live snapshot exists now**:
  `~/.local/state/zeropi-display/rate-limits.json` (0600, atomic temp+rename),
  written by **claude-hud** via `display.externalUsageWritePath`. That option
  lives in **`~/.claude/plugins/claude-hud/config.json`** — *not*
  `~/.claude/settings.json`, which was left untouched. claude-hud never creates
  the parent directory, so it must exist first.
- **Shape**: three keys — `updated_at` (ISO-8601 UTC, always present), plus
  `five_hour` and `seven_day`, each `{used_percentage, resets_at}`.
  `used_percentage` is an **integer 0-100 or null**; `resets_at` an ISO string
  or null. `model_scoped` and `balance_label` are **dropped by the writer**.
- **⚠ The stdin field is `used_percentage`, not `utilization`.** `utilization`
  is right for `~/.claude.json` only. Spec against the snapshot's names.
- **⚠ `updated_at` is a write time, not a fetch time.** claude-hud rewrites on
  a 30 s throttle even when the value is unchanged — observed twice (23%→23%,
  24%→24%). A fresh `updated_at` does **not** mean a fresh percentage.
- **⚠ Headless `-p` sessions write nothing.** Print mode renders no status
  line, verified with a canary command that was never invoked. **The gauge is
  live only while an interactive Claude Code TUI is open** — a cron-fired
  `push.py` against a closed terminal reads a frozen file.
- **Absent, not zero.** No file before the first render; and if stdin carries
  no `rate_limits` at all, nothing is written and any existing file is left in
  place, so a stale snapshot can persist silently.
- **5 minutes has prior art as the staleness threshold** — claude-hud's own
  reader default (`externalUsageFreshnessMs`, 300 000 ms), independently the
  same number as #23's recommended 300 s panel operating point.

Live-usage data-model facts, established 2026-09-05 by
[#24](https://github.com/peterderkoala/zeropi.display/issues/24):

- **The live gauge is ephemeral** — never persisted to the Pi's SQLite,
  display-only. The daily table already covers the trend use case; a
  5-minute-grain history would just burn SD write cycles for no product
  value.
- **Two Payload shapes, not one.** The live-gauge Payload and the daily-row
  Payload are structurally different (window consumption/resets_at/context
  vs. tokens/cost/grain) and stay separate rather than one shape with fields
  left null depending on which kind of row it is. Field naming is #19's job,
  not settled here.
- **Active session = most-recent `updatedAt`** in
  `~/.claude/sessions/<pid>.json` — the single rule for both "which session"
  among several `busy` ones and "is anything live at all." Zero live
  sessions renders blank, not a stale number.
- **Context size displays as a percentage**, not a bare token count, against
  a hardcoded per-model context-window table — same pattern as #14's pricing
  table. **Resolved by [#31](https://github.com/peterderkoala/zeropi.display/issues/31)**:
  the commonly-assumed 200K window is wrong for the two models that matter
  most — Opus 5 and Sonnet 5 both carry a **1,000,000-token window** (combined
  input+output), now GA with no pricing surcharge (doesn't touch #14's
  table). Haiku 4.5 stays at 200K, no extended option. Max output: 128,000
  for Opus 5/Sonnet 5 (300K on Batch API beta), 64,000 for Haiku 4.5. Table
  in `docs/research/context-window-table.md`
  (branch `research/context-window-table`).
- **A null `used_percentage` gets its own explicit state** ("no data yet"),
  distinct from both zero and stale — collapsing it into either would
  misrepresent a real, observed condition (per #27, not hypothetical).
- **Staleness is Pi-side, not Desktop-suppressed.** The Payload carries a
  generated-at timestamp (from claude-hud's `updated_at`); the Pi compares
  against its own clock and dims (never blanks) a reading past 5 minutes.
  This matters because the Pi can go without a push longer than 5 minutes
  even when the Desktop's own read was fresh at push time.

Desktop-side usage store facts, established 2026-09-05 by
[#28](https://github.com/peterderkoala/zeropi.display/issues/28):

- **The store's location is configurable** — an env var or a `push.py` CLI
  flag, falling back to `~/.local/share/zeropi-display/usage-archive.db`
  when neither is set. This is the first configurable path in the codebase;
  everything else (Pi's `DB_PATH`, the GATT UUIDs) is a hardcoded constant.
- **Ingest resumes via a per-session high-water mark**, not a whole-file
  mtime check — a session's JSONL grows across days, so mtime alone would
  wrongly skip a file that's been partially ingested and then appended to.
- **The #15 winner-rank runs at ingest, and this is a one-way door.** Only
  the winner of each `(requestId, message.id)` duplicate group is stored;
  losers are discarded permanently. If the rank rule ever changes, only
  newly-ingested entries follow it — old stored history can't be re-ranked.
- **Store-only aggregation is an absolute rule, no repair escape hatch.**
  There is no `--rebuild-from-logs` mode; a corrupt store is restored from a
  backup of the store itself. Re-deriving from logs would reintroduce the
  exact degradation hazard (#21) the store exists to remove.
- **Schema is one entry table with a push-marks column** — no separate
  marks table, no separate ingest-offset table. All store state (dedup
  winner, push status, ingest position) lives in one SQLite file.

Backfill and retention facts, established 2026-09-05 by
[#21](https://github.com/peterderkoala/zeropi.display/issues/21):

- **⚠ The Pi has no read path.** `pi/receive.py:70` builds the Ack as
  `{status, received_at, reason?}` and nothing else, so **the Desktop can never
  ask the Pi what it holds**. Every "does it already have this?" question has
  to be answered from Desktop-side state. This is the single constraint that
  forced the Desktop store into existence.
- **⚠ The Desktop's logs self-delete on a rolling 30-day sweep.**
  `cleanupPeriodDays` defaults to 30 and the sweep deletes
  `projects/<project>/<session>.jsonl` outright. It is unset on this machine,
  so the default applies. Proof it already fired: `~/.claude/stats-cache.json`
  is exempt from the sweep and still remembers 2026-07-13 → 2026-07-19, days
  that no longer exist in the JSONL logs.
- **This is not an emergency.** Real use starts at the first prod build;
  everything before is *test material*, not history. #29 carries no deadline.
- **A day's completeness degrades gradually**, which is subtler than the sweep
  itself: 2026-07-28's usage survives only because it sits in a session file
  last written 2026-08-07, while that day's other sessions are already gone. So
  re-reading the logs later can yield a **smaller** row for a day already
  stored in full. Computing rows from the Desktop store rather than from the
  logs is what removes this; do not reintroduce a log-sourced push path.
- **The full history is 12 rows / 2,736 bytes / $317.43** across 9 active days
  and 3 projects — not the ~20 rows #21 originally estimated. Entry grain is
  6,022 records / 1.54 MB; a raw log copy would be 70.9 MB and would durably
  retain prompts.
- **`~/.claude/stats-cache.json` is not a usable data source** despite
  surviving the sweep: frozen at `lastComputedDate: 2026-07-19`, `costUSD: 0`,
  no project dimension.

Usage-log facts, from map #13's research (full detail in the map body and
in `docs/research/`):

- **A naive dedup of the JSONL logs loses 26.2% of all output tokens.**
  Duplication is streaming content-block fan-out, and the early copies carry
  a *provisional* usage snapshot. Keying on `(requestId, message.id)` is only
  half the rule — you must also pick a winner within each group.
- **Cache-write tokens are two billed classes, not one.**
  `cache_creation_input_tokens` is the sum of `ephemeral_5m` and
  `ephemeral_1h`, which price differently. Costing off the sum is wrong by
  ~5% on a real session.
- **Transcript-derived cost is ~92.8% of `cost-state`, and that is correct.**
  The transcript does not contain every call the accumulator saw. Do not
  chase the gap.

## Environment notes

- Dev Pi: `192.168.4.108`, creds in `infrastructure.md` (gitignored).
  `sshpass` is installed in this dev environment for non-interactive SSH;
  the sudo password is the same as the SSH password.
- **⚠ The dev Pi is shared with other concurrent sessions/jobs.** Before
  touching `/opt`, systemd units, `config.txt`, or rebooting, check for a
  live collision (`who`/`w` over SSH, recent `/var/log/dpkg.log`) — a
  scratch directory (e.g. `/home/pi/epd-bench`) is the safe default when
  another job might be mid-run. Surfaced 2026-09-06 when #35's teardown
  and #39's e-ink bench work overlapped; coordinated directly with no
  actual damage, but it was luck as much as care.
- **⚠ `install.sh`'s non-interactive `sudo` path had two real bugs**, fixed
  2026-09-06 while verifying #35 (`370129a`, `fdf9192`, `e91e623` on
  `dev`): `[[ -e /dev/tty ]]` is true with no controlling terminal at all,
  so it's not a valid liveness check; and `install-desktop.sh`'s in-place
  detection must be based on the invoking shell's `$PWD`, never on the
  script's own location, since the curl bootstrap always runs it out of a
  tarball staging dir. See `docs/curl-delivery-verification.md`.
- **⚠ `install.sh` resolves its ref through the unauthenticated GitHub API**
  (60 requests/hour per egress IP). Exhausted, it fails at the first step with
  a bare `curl: (22) ... 403` and no hint that rate limiting is the cause. Hit
  during #48 on 2026-09-09; the reset is at most an hour out and
  `curl -i https://api.github.com/rate_limit` from the Pi says when.
- Pi: Debian 13 (trixie), Python 3.13.5, aarch64, BlueZ `5.82-1.1+rpt2`,
  `python3-dbus` `1.4.0-1`, `bluezero` `0.9.1` in `~pi/.local`.
- Desktop: `bleak` 3.0.2 in a local `.venv/` (gitignored, not committed) —
  `uv venv .venv && uv pip install -r desktop/requirements.txt`.
- Labels `wayfinder:map`, `wayfinder:task`, `wayfinder:grilling`,
  `wayfinder:research`, `wayfinder:prototype` exist. `gh` CLI is
  authenticated as `peterderkoala`.
- Claude Code usage logs live at `~/.claude/projects/**/*.jsonl` — 124 files,
  ~70MB, 3 projects as of 2026-09-04. They contain prompts and file
  contents: **never commit them or excerpts of them.** Test fixtures must be
  synthetic or scrubbed.

## Suggested skills for the next session

- **`mattpocock-skills:wayfinder`** with map #59 — **the live map, charted
  2026-09-09, four tickets takeable in parallel right now** (#60, #61, #62,
  #65; see Maps above). Claim one (`gh issue edit <n> --add-assignee @me`),
  read the spec section it points at, build it. **Execution** map: produce
  working code, not decisions.
- **`mattpocock-skills:tdd`** per unit — spec §12 names the assertions, and
  frame builders are unusually easy to test (render, assert on pixels).
- **[#66](https://github.com/peterderkoala/zeropi.display/issues/66) is the one
  that needs a human** — hardware verification at the bench, blocked until the
  build tickets land. Everything else on map #59 can be driven from the
  terminal. (#57, the spec map's bench session, is **closed**.)
- **Every closed map's log is in `handoff/archive/`** — #1, #7, #13, #41, #51.
  Nothing there is takeable; read one when you want the reasoning behind a
  decision, or what was tried and rejected.
- *(historic, for map #41's tickets — all closed)* `mattpocock-skills:tdd`
  against `docs/spec-usage-pipeline.md` §11's synthetic fixture.
- **`mattpocock-skills:grilling` is not the tool for map #59.** It is an
  execution map: the spec closed the decisions, so a question there means you
  have found a **gap in the spec** — say so on the ticket and flag it to #51,
  rather than grilling your way to a private answer.
- **`mattpocock-skills:domain-modeling`** only if a ticket coins a term the
  glossary lacks. `CONTEXT.md` is current as of **Active Day** (#52); the
  One-liner was deleted from it when the feature was dropped.
- **The `PWR_PIN`-on-BCM-18 question is out of scope, by the maintainer's
  call**, not fog waiting for a home. It needs a multimeter at the bench and
  blocks no frame from drawing. Do not re-adopt it into a map.

## If you run subagents, isolate them

Two research subagents were run in parallel from the same working tree on
2026-09-04 and their git operations collided — one agent's commit landed on
the other's branch. No damage (`dev` and `main` were untouched) and it was
repaired with a fast-forward, but `research/dedup-rules` still carries the
pricing commit as a result. **Give parallel agents their own worktrees.**
