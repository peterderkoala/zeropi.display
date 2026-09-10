# Handoff — zeropi.display

## Where things stand

> **Rendering is DONE.** Map #59 reached its destination on 2026-09-10; the
> panel draws, verified on real glass. **Map #70 is live** — a management
> surface for both ends, charted 2026-09-10. See
> [For the next session](#for-the-next-session) below for what is takeable.

**E-ink RENDERING is DONE and hardware-verified (2026-09-10).** Map #59's
destination is reached: `docs/spec-eink-rendering.md` is implemented,
unit-tested (**243 passing**) and proven on the dev Pi's real glass with the
maintainer looking at it. All six of #66's scenarios passed on first attempt
and **no defect was found in `render.py` or `receive.py`**. Full run:
`docs/eink-rendering-verification.md`.

Three things from that run you would otherwise rediscover the hard way:

- ⚠ **The 13 px text floor is real but NOT monotone.** Re-confirmed with text
  rasterised on the Pi itself (Pillow 11.1.0), which is what spec §14 left
  open: `st` collides at 11 px and separates at 13. It then **collides again at
  14 px**. Separation is how a glyph pair lands on the pixel grid, not a
  function of size, so *"bigger is safer" is false here* — changing any text
  size on this panel means re-checking the pair, not reasoning about it.
- ⚠ **`gauge_age_s` bounds the SNAPSHOT's age, not the push's.** A re-push of
  an unchanged snapshot buys no freshness, so the panel can fall back to the
  Historic View while a Gauge Payload that arrived seconds ago sits in memory.
  That is ADR-0010 working, not a bug — and it is the clearest argument for
  #65's throttle drop. It looks like an off-by-300s error in the journal; it
  is not.
- ⚠ **`receive.py` now owns the panel.** Running `pi/epd-selftest.py` against a
  live `zeropi-display` is a GPIO collision. The self-test refuses to run when
  the service is active, but stop the service first rather than relying on it.

**The usage pipeline is DONE and hardware-verified (2026-09-09).** Map #41's
destination is reached and the map is closed: `docs/spec-usage-pipeline.md` is
implemented, unit-tested (**181 passing at the time**) and proven end-to-end
against the dev Pi and the maintainer's real Claude Code logs. Real Daily and
Gauge Payloads cross the link; the Pi persists and gates per spec — and, since
#63, **actually redraws** rather than stubbing it; the resident
`systemd --user` service does it unattended. Full run:
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

`docs/spec-eink-rendering.md` (`bf6be4b`) remains **binding** — map #51's
destination, and now implemented. It says what the panel draws and how, in
pixel geometry a human approved on real glass. ⚠ **Its §11 supersedes three
clauses of `spec-usage-pipeline.md`**; do not read those as current. It also
says outright that THIS FILE is not authoritative.

⚠ **Two omissions in the spec's own prose** were found while verifying it, both
cosmetic, both raised on #66 rather than reconciled quietly. §5.4 does not
mention the `5H` label the fault frame draws (the code is right — the reference
approved on glass in #57 draws it too). §4 says 11 px collides on the `st`
*and* `sh` pairs; on the Pi's raster `sh` separates at every size, because §4's
claim is about glass and the measurement is about pixels. Neither changes the
floor.

⚠ **Weather, calendar and the One-liner were dropped from the project**
(maintainer's call, 2026-09-09, `c3aa086`). zeropi.display is a Claude Code
usage display and nothing else. `CONTEXT.md` no longer defines **One-liner**;
`pi-eink-ble-concept.md` and `CLAUDE.md` are rewritten, with the concept
document's milestone-1 sections kept as the historical record they are. Do not
reintroduce them from an old document.

**The Pi's fonts are `fonts-dejavu-core`**, installed by `install-pi.sh` since
#64, at `/usr/share/fonts/truetype/dejavu/`. (Before that the Pi had no fonts
at all and anything drawing text died at `ImageFont.truetype()` — that is
fixed, and text has now been rasterised on the Pi and read on the glass.)

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
exercised: that verification ran before the merge. **The panel steps have
since had their hardware pass** — #64 provisioned them and #66 verified the
result on the glass.

**The e-ink panel draws on real hardware.** The Waveshare V4 driver is
vendored at a pinned upstream commit in `pi/waveshare_epd/`,
`pi/install-pi.sh` provisions it (SPI + `python3-{spidev,gpiozero,lgpio,pil}`
from apt + deployment), and `pi/epd-selftest.py` is the by-hand bench check.
Verified 2026-09-06 on the dev Pi: **full refresh 2.29 s** (ADR-0007 assumed
~3 s), framebuffer exactly 4000 bytes, `epd-selftest.py` 6.7 s end to end.
Write-up: `docs/eink-driver-verification.md`. That run stopped deliberately
short of rendering; **#63 has since wired the driver into `receive.py`** and
#66 verified the whole thing on glass.

A review after that run caught two defects, fixed in `ca68517`: `epd.init()`
sat outside the `try`, so the `finally` that sleeps the panel did not cover
the phase where it is already powered (an ADR-0007 violation), and the
`raspi-config` call was bare under `set -e`, so a failure to enable SPI would
have aborted provisioning before `receive.py` was deployed. **If you write
any further panel code, the `sleep()`-on-every-path property is the one to
re-check** — it is easy to get wrong and expensive to get wrong.

⚠ **That doc's last section is now out of date and should be read as history.**
It says the driver's *provisioning* was unverified (the run went through a
scratch directory with `dtparam spi=on`, which does not survive a reboot) and
that nobody had looked at the glass. Both were fixed later: #64 provisions the
panel stack and the font through `install-pi.sh`, and #66 put a human in front
of the panel. The `PWR_PIN`-on-BCM-18 caveat in `pi/waveshare_epd/README.md`
does still stand — it is out of scope by the maintainer's call, and blocks no
frame from drawing.

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

## For the next session

**Map #70 is live: [One management surface for both ends
(spec)](https://github.com/peterderkoala/zeropi.display/issues/70)**, charted
2026-09-10. Its destination is `docs/spec-management-surface.md` — one pane on
the Desktop that manages both ends (status, configuration, control), exercised
by a CLI. **Implementation is a separate map**, as with #51→#59 and #13→#41.

**Takeable now** (open, unblocked, unassigned):

- [Which constants are settings, and in which
  tier](https://github.com/peterderkoala/zeropi.display/issues/72) — grilling.
  It now has a concrete output shape (see the annotation on the ticket): the
  schema it produces is the **single authority on each key's type, range and
  Tier**, consumed as validation by the config store. For every Tier 2 key that
  means an actual machine-checkable range, not just a label.
- [Confirm the notify budget on hardware
  (btmon)](https://github.com/peterderkoala/zeropi.display/issues/78) — task,
  **at the bench**. Blocks the status surface.

**Closed so far:** [What the Pi can send back: the notify-direction
budget](https://github.com/peterderkoala/zeropi.display/issues/73) (research,
fired as a subagent at charting time; graduated #78) and [Where configuration
lives, and who wins](https://github.com/peterderkoala/zeropi.display/issues/71)
— both 2026-09-10. The rest are blocked: #74 on #78, #75 on #72, #76 on
#72/#74/#75, and #77 (write the spec) on all the others.

**What #71 settled**, in one line each — the full reasoning is its resolution
comment, and the gist is on the map:

- **Configuration is a dedicated SQLite store**, `~/.config/zeropi-display/config.db`
  — deliberately **not** a section in the archive store, because `open_store`
  refuses on a version mismatch, ADR-0005 makes store backups the only backups
  that matter (so restoring old history would restore old Configuration), and
  the store is 0644.
- **Resolved once at startup** into a frozen object, filling parameters that
  already exist. Never module-level globals populated at import — the suite
  imports these modules with no `~/.claude`, no panel and no `bluezero`.
- **Changing a setting requires a restart**, with an explicit restart action
  rather than an auto-bounce. "Pending restart" needs no new state:
  `config.updated_at` vs `systemctl --user show zeropi-push -p ActiveEnterTimestamp`.
  **The resident service never writes Configuration** — do not add a
  service-side write path.
- **Unknown keys: rejected on write, warned-and-ignored on read.** The flat
  "reject" settled in round 1 would have turned retiring a setting into an
  outage on data the previous version wrote itself.
- Coined **Configuration**, **Settings** and **Tier** in `CONTEXT.md`
  (`ed11166`). Configuration and Settings are **not synonyms** and the
  distinction is load-bearing.

⚠ **The Pi has no configuration seam at all, and #75 has to build one.** The
Desktop is already injectable everywhere — every policy value is a default
argument the tests already override. `receive.py` is the opposite: it reads
`GAUGE_EXPIRY_S`, `REDRAW_FLOOR_S` and `IDLE_KEEPALIVE_S` as **module globals
from inside methods** (`:366`, `:415`, `:418`) and binds `DB_PATH` to class
attributes at module scope (`:562`, `:704`) under a comment saying it "stays a
hardcoded constant (spec §8.1)". So **spec §8.1 has to be revisited**, not
worked around — and Pi Settings must apply **live**, because a Settings Payload
cannot restart the service without dropping the connection that delivered it.

⚠ **Five decisions were settled while charting #70 and must not be
re-litigated** — they are written out in the map's Notes. In short: one
surface, on the Desktop; this map ends at the spec (the SPA is a later map);
management reaches the Pi over **BLE only**, with the Pi holding no independent
config; constants are tiered, and verified invariants are shown read-only with
their ADR rather than being made editable; the eventual UI is LAN-bound behind
a single shared token, which is why the config surface must be able to hold a
secret.

⚠ **Several constants this map will touch are *findings*, not preferences** —
`REDRAW_FLOOR_S` is ADR-0008, `GAUGE_EXPIRY_S` is ADR-0010, the 120 s throttle
came from #55, the 512-byte budget is ADR-0001. A settings form that treats
them as knobs can silently invalidate a hardware verification run. That is the
single biggest risk on the map.

**Also open, outside the map:** the two spec-prose omissions raised on #66
(§4's `sh` pair, §5.4's `5H` label). Cosmetic; whether
`docs/spec-eink-rendering.md` gains the lines is the maintainer's call. And
`desktop/install-desktop.sh`'s standalone mode is still broken (it deploys only
`push.py`) — **ruled out of scope for #70** as installer debt, so it needs its
own home.

⚠ **The Pi→Desktop notify budget is also 512 — but it fails SILENTLY** (#73,
closed 2026-09-10). `min(512, ATT_MTU − 3)`, 512 on this link because both ends
default to `ExchangeMTU = 517`. Same number as the write direction, **weaker
guarantee**: the write direction has prepare/execute long writes underneath it,
notifications have **no fragmentation procedure at all**, and indications buy
reliability rather than bytes. `bluetoothd` truncates at 512 in
`gatt-database.c`, the ATT server truncates again at `ATT_MTU − 3` in
`gatt-server.c`, both return success, and `bluezero` adds no check — so an
over-long Ack surfaces on the Desktop as `malformed ack from Pi`, **blaming the
JSON rather than the length**. Do not go hunting the parser. Full working:
`docs/research/notify-direction-budget.md` on `research/notify-budget`
(`ef26120`). The number is *derived, not measured* — #78 is the bench
confirmation, and this project has derived an MTU number wrongly twice already.
Headroom today: measured max Ack 194 B, worst case ~360 B.

⚠ **The single-write budget is 512 bytes, not 514** (#67, closed 2026-09-10) —
and this is worth knowing because both the spec and ADR-0003 had it wrong.
514 was derived from the MTU (517 − 3); **ATT's maximum attribute value length
binds first**. Bisected on hardware: 512 Acked, 513 raises
`INVALID_ATTRIBUTE_VALUE_LENGTH`. `push.py` now enforces it itself
(`MAX_PAYLOAD_BYTES`), so an over-budget Payload is refused against the row
that caused it instead of surfacing as a transport error and then being
retried by every Batch forever.

⚠ **Payload sizes have grown ~50% without anyone noticing.** ADR-0003 recorded
a Reading as 197–236 bytes (worst case 262); measured against real data today
it is **347–390**. The variable field is `project`, which may run to ~159
characters worst-case before one Reading stops fitting; the longest in the
maintainer's own logs is 63. Splitting by Reading has no smaller unit to fall
back on, so if that is ever reached the answer is shortening the key on the
wire — which reopens ADR-0003's primary key, not its chunking decision.

⚠ **#32 is closed, and its premise was wrong** — worth knowing, because the
belief it encoded was in the spec for months. `push.py` called bleak's private
`_backend._acquire_mtu()` and both the code comment and spec §10 trap #2 said
it was "the only way past the 23-byte default MTU". **It negotiates nothing.**
The ATT MTU is negotiated by the kernel when the link comes up; that call
reaches BlueZ's `AcquireWrite` purely to *read* the value into
`client.mtu_size`, and neither `write_gatt_char` (D-Bus `WriteValue`) nor
`start_notify` (BlueZ `StartNotify`) consults it. Removed on 2026-09-10, so
`push.py` is now **public-API-only and not BlueZ-bound**.

**The proof is worth remembering, because it is the one that discriminates**:
with the call gone and bleak reporting `mtu_size == 23`, 12 Daily Payloads of
up to 390 bytes went through *and each was answered by an Ack of up to 194
bytes*. **An ATT notification cannot be fragmented** — so a 194-byte Ack
arriving whole proves the real MTU is ≥ 197. The write path alone proves
nothing, since BlueZ will happily long-write a 390-byte payload over a 23-byte
MTU.

**Two things wanting calendar time, not a session** — inherited from #59 and
#51, and they finally became *countable* now that a panel actually runs:

- **The refresh budget over the panel's life.** ADR-0007 makes every update a
  full refresh and nobody has ever counted what a real day produces. #66's run
  put 6 refreshes on the glass in 15 minutes, but three of those were
  deliberate service restarts, so it says nothing about a normal day.
- **Long-run panel behaviour**: ghosting, contrast drift, and whether the 24 h
  keep-alive does what ADR-0010 hoped. The panel read **clean** after #66's
  burst of 6 refreshes, which is a different and much weaker claim.

⚠ **One frame has never reached glass**: `NO USAGE DATA` (spec §5.4) needs a
null `used_percentage`, which real data will not produce on demand. Its harder
half — the 13 px `waiting for first snapshot` — was verified twice over in #66.

**The reference renderers are still on unmerged branches**, and remain the
record of what a human approved pixel by pixel. Do not redesign a frame against
them; that is a new effort against #51, not a liberty taken while building.

| Branch | What it holds |
|---|---|
| `prototype/historic-view` | `desktop/historic_prototype.py` — the Historic View and empty frame, plus the four rejected candidates |
| `prototype/gauge-glass-fix` | `desktop/gauge_settled.py` — the Gauge frame with #57's corrections |
| `bench/render-blocking` | the event-loop measurements and the timing probe |
| `research/eink-fonts` | the font facts, with rendered samples |

**The five things most likely to bite anyone touching the panel code**, none of
them in the spec's own voice:

1. ⚠ **Nothing may block the bluezero event loop for more than ~5 s.** That is
   BlueZ's write timeout, not our 10 s Ack timeout, and a full panel cycle is
   **4.35 s**. This is why the refresh runs on a worker thread. Overrunning
   raises `GATT Protocol Error: Unlikely Error` — the same signature milestone
   1 spent a session chasing — *after* the Pi has already persisted the
   Reading, so the two ends then disagree silently.
2. ⚠ **The 13 px floor is not monotone.** See Where things stand above.
3. ⚠ **Build images directly in mode `"1"`.** Greyscale-then-convert takes a
   different FreeType path and produces different letterforms, so what you
   review is not what the panel shows.
4. ⚠ **`epdconfig` claims GPIO on import**, and `receive.py` must stay
   importable with no panel, no SPI and no bluezero — the suite depends on it.
   The import lives inside the worker. This is also why the 4000-byte
   framebuffer test replicates `getbuffer`'s packing rather than calling it.
5. ⚠ **Every panel cycle must end in `epd.sleep()`**, with `init()` *inside*
   the guarded region. A review caught this exact mistake once (`ca68517`); the
   context manager in `render.py` exists to make it structural.

## Maps

**Live map: [#70 — One management surface for both ends
(spec)](https://github.com/peterderkoala/zeropi.display/issues/70)**, charted
2026-09-10 with seven tickets (#71–#77). It is a **planning** map: tickets
resolve decisions; nothing on it builds the management surface, the one
exception being #76, which prototypes a CLI so the design has something
concrete to argue with. See
[For the next session](#for-the-next-session) for what is takeable.

#59 was the previous map; its destination is reached and its log is archived at
[`archive/map-59.md`](archive/map-59.md).

### Closed maps — archived

Their session-by-session logs live in `handoff/archive/` (#1, #7, #13, #41 and
#51 moved there on 2026-09-09; #59 on 2026-09-10), so this file stays about
live work. **The archive is a record, not guidance**:
some of what it says was superseded later. Read a map's file when you want to
know *why* something was decided, or what was tried and rejected — that
reasoning is not recoverable from the code.

| Map | Reached | Log |
|---|---|---|
| [#59 Make the panel draw](https://github.com/peterderkoala/zeropi.display/issues/59) | the panel draws, hardware-verified | [`archive/map-59.md`](archive/map-59.md) |
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

- **`mattpocock-skills:wayfinder`** to work a ticket on the live map, **#70**.
  Invoke it with the map, not with a fresh idea — charting is done. It picks
  the next frontier ticket for you if you do not name one.
- **`mattpocock-skills:tdd`** for anything touching `render.py` — spec §12
  names the assertions, and frame builders are unusually easy to test (render,
  assert on pixels). The suite is **243 passing** and must stay green with no
  panel, no SPI, no bluezero and no `~/.claude`.
- **Bench work now needs the service stopped.** `receive.py` owns the panel, so
  `epd-selftest.py` against a live `zeropi-display` is a GPIO collision. #66's
  run is the template for a hardware session: back up the Pi's `data.db`, drive
  each scenario from `push.py`, read the journal for the `render:` line, and
  put a human in front of the glass for what a log cannot show.
- **Every closed map's log is in `handoff/archive/`** — #1, #7, #13, #41, #51,
  #59.
  Nothing there is takeable; read one when you want the reasoning behind a
  decision, or what was tried and rejected.
- *(historic, for map #41's tickets — all closed)* `mattpocock-skills:tdd`
  against `docs/spec-usage-pipeline.md` §11's synthetic fixture.
- **`mattpocock-skills:grilling` is the default tool on map #70** — five of its
  seven tickets are grilling tickets, because #70 is a **planning** map. Note
  the contrast with map #59: on an *execution* map a question means you have
  found a **gap in the spec**, so you say so on the ticket rather than grilling
  your way to a private answer (#66 found two and did exactly that). #70 is the
  opposite case — the whole point is to have the argument now.
- **`mattpocock-skills:domain-modeling` is load-bearing on #70**, not optional.
  The map coins configuration and wire vocabulary, and **#75 edits the
  definition of `Payload` itself** — `CONTEXT.md` currently says it has exactly
  two shapes, and a Settings Payload makes that three. The glossary is current
  as of **Active Day** (#52); the One-liner was deleted from it when the
  feature was dropped.
- **The `PWR_PIN`-on-BCM-18 question is out of scope, by the maintainer's
  call**, not fog waiting for a home. It needs a multimeter at the bench and
  blocks no frame from drawing. Do not re-adopt it into a map.

## If you run subagents, isolate them

Two research subagents were run in parallel from the same working tree on
2026-09-04 and their git operations collided — one agent's commit landed on
the other's branch. No damage (`dev` and `main` were untouched) and it was
repaired with a fast-forward, but `research/dedup-rules` still carries the
pricing commit as a result. **Give parallel agents their own worktrees.**
