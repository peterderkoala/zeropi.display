# Handoff — zeropi.display

## Where things stand

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
- ⚠ **ADR-0010's "whatever the Gauge frame shows is under 300 s old" is
  overstated.** The fallback to the Historic View is itself gated by the 300 s
  redraw floor, so a frame drawn at 268 s of Gauge Age stayed on the panel until
  it was ~570 s old (measured), worst case just under 600 s. `receive.py` is
  correct — the constants produce it. Flagged on #13, deliberately not "fixed";
  it belongs to whoever charts the rendering map.

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


### [Map: What the e-ink panel draws, and how (spec)](https://github.com/peterderkoala/zeropi.display/issues/51) — **CLOSED 2026-09-09**

Charted and completed the same day; all seven children resolved. Destination
reached: `docs/spec-eink-rendering.md`. **A planning map — "plan, don't do" applies.** Destination
is `docs/spec-eink-rendering.md`; **implementation is a separate map** opened
against the finished spec, the way #41 was opened against #13's.

Scope was settled by grilling before charting: the Historic View design is the
bulk of the work (it has **no** design today), the Gauge frame is carried
across already-settled from #38, weather/calendar/One-liner are dropped from
the project entirely, and the `PWR_PIN`-on-BCM-18 question is **Out of scope**
(maintainer ruled it out — bench work, blocks nothing).

Method note the map fixes: **mocks first, exactly one bench session on real
glass before the spec is written.** Not a design loop that stalls on the
maintainer each round.

**Update, same day: [Design the Historic View](https://github.com/peterderkoala/zeropi.display/issues/52)
is closed.** The resting picture is **a list of the most recent Active Days**
— date, cost in dollars, a bar — over a footer carrying **Coverage Start** and
the **average cost per Active Day**; `~` marks a cost that is not Cost
Complete; an empty Pi reads `NO HISTORY YET`. Mocks and the throwaway renderer
are on branch `prototype/historic-view` (unmerged); **Active Day** is now a
term in `CONTEXT.md` (`a9f3e3a`).

⚠ **Three findings from drawing it that you should not have to rediscover**:
(1) usage is **11 Active Days over 44 calendar days**, so any calendar window
is mostly gaps — that is what killed the bar-chart and sparkline candidates;
(2) the Pi's partial history renders **pixel-identical** to idleness, which is
the whole reason the Coverage Start footer exists; (3) **10px bold labels
rasterise unevenly in 1-bit** (`TODAY` reads `TOD AY`) — the settled design's
smallest text is 12px because of it, and the bench check should confirm that
on real glass, since a 3× PNG flatters small text.

It also **narrowed #53**: the trend is per-day totals across every project, so
**no Project Label is needed on the panel** — #13's parked question is answered
by the design, not the plumbing.

**Update, same day: [How a 2.29 s refresh coexists with the BLE event loop](https://github.com/peterderkoala/zeropi.display/issues/56)
is closed** — and it **moved its own premise**. Decisions: the refresh runs on
**one worker thread** with a one-slot hand-off; `drawn` is redefined as *the
gate accepted this for drawing*, not "pixels moved"; the **link outlives the
panel** (a dead display never stops receiving, persisting or Acking, and a
watchdog abandons a stuck `ReadBusy` rather than restarting the process); and a
**context manager owns the panel** so ADR-0007's `sleep()`-on-every-path is
structural, with the GPIO-claiming `epdconfig` import inside the worker.

⚠ **Three measured facts that supersede what this file and the tickets assumed**
(bench on branch `bench/render-blocking`, `docs/research/render-blocking/`):

- **A full panel cycle is 4.35 s, not 2.29 s** — `init` 0.05 + `display` 2.29 +
  `sleep` 2.00, the last being the driver's own fixed `delay_ms(2000)`.
- **The ceiling is BlueZ's ~5 s write timeout, not our 10 s Ack timeout.** A
  receiver blocked 30 s did not trip the Desktop's timeout — the *write* raised
  at 5.09 s. Inline rendering would have run at ~0.5 s of margin against a
  limit we do not control.
- ⚠ **An overrun fails as `GATT Protocol Error: Unlikely Error`** — verbatim the
  signature `docs/e2e-verification.md` chased for a whole session before
  finding the `bluetoothd` segfault. **If that error ever comes back after
  rendering ships, suspect the panel before the daemon.** Worse, the Pi has
  already persisted the Reading by then, so the Desktop resends forever while
  the Pi holds it — both sides correct, data diverging.

Measured, not reasoned: a worker-thread receiver ran the same six-row Batch in
**0.86 s** against inline's **5.18 s**, serving rows 2-6 *while the worker was
mid-refresh*, because every wait in the driver is a `time.sleep` and releases
the GIL.

**Update, same day: [Re-settle ADR-0010's 300 s freshness bound](https://github.com/peterderkoala/zeropi.display/issues/55)
is closed — and it found a worse problem than the one it was opened for.**

⚠ **`GAUGE_EXPIRY_S` (Pi), `REDRAW_FLOOR_S` (Pi) and `GAUGE_THROTTLE_S`
(Desktop) are all 300 s, and nothing says they should be.** Measured with the
resident service running against the dev Pi: a replacement Gauge arrives
**310 s** after its predecessor (300 s throttle + ~7 s scan/connect + jitter),
so **the Gauge on screen is expired for ~25 s before every replacement, every
cycle**. Whether the panel visibly flips to the Historic View in that window is
a phase coincidence between the 60 s tick and the floor — and when it does
flip, **the floor pins Historic on the panel for a full 300 s while a live
Gauge sits in memory.** Intermittent, invisible to tests, unreproducible on
demand.

**Decided**: the **Desktop's Gauge throttle drops to 120 s** (a push has a
second job §7.5 never costed — keeping the Pi's Gauge alive); expiry and floor
stay 300 s; ~2.5x the BLE work and **zero extra panel wear**, since the floor
still gates every draw. The invariant — *expiry must stay comfortably above the
push interval, roughly `2 x throttle`* — is written into **ADR-0008 and
ADR-0010** (`76670cf`), each constant naming the other across the two machines.
ADR-0010's freshness claim is restated as two bounds (at draw ~135 s; on the
panel at most 600 s, and only when the Desktop has actually died); the
no-footer decision survives. Floor pre-emption and putting the cadence on the
wire were both considered and rejected.

⚠ **The constant itself is NOT changed in `desktop/service.py`** — planning
map. The one-line edit and the spec §7.5 wording belong to the implementation.

**Update, same day: [Where the Historic View's data comes from](https://github.com/peterderkoala/zeropi.display/issues/53)
is closed.** **The Pi queries its own `readings` table**, on demand at every
redraw, no cache, and **the wire is untouched**. Timed on the Pi against a
synthetic year of Readings (2,190 rows): **0.25 ms** for the five rows,
**4.66 ms** for the average — free against a 300 s floor, so caching would buy
a rounding error and cost an invalidation bug.

The contract: 5 most recent Active Days by `SUM(cost_usd)`; the average taken
over *every* Active Day the Pi holds (so the footer reads as one sentence,
*since 04 Sep, average $40*); `coverage_start` from `meta`, **formatted but
never computed** (ADR-0009 still holds); `~` when `MIN(cost_complete) = 0`; an
Active Day is a date with **any** row, not `cost > 0`.

Two things this retires, so nobody re-implements them: **§9.3's
"the Desktop sends explicit zero rows" clause is moot** (Active Days never draw
gaps), and **a partial Batch understating a day is accepted and documented**
rather than signalled — it self-heals on the next Batch, and reusing `~` for it
would conflate two faults that point in different directions.

**#13's parked "does the Pi derive a Project Label" question is now closed in
both halves** — the design does not show projects, and the data path does not
need them.

**Update, same day: [Look at the design on real glass](https://github.com/peterderkoala/zeropi.display/issues/57)
is closed** — nine frames on the panel with the maintainer looking at them.

**The Historic View survived unchanged**: the 12 px footer is readable at
arm's length, the `~` marker reads as a qualifier rather than a smudge, the
gap left by a short list reads as intentional, the empty frame reads as a
state rather than a fault, and there was no ghosting.

⚠ **The Gauge frame did not survive.** #38 settled it on PNG mocks and it had
never been on the panel: the split rule was drawn to `y=46` while the 5H bar
occupies `y=40–48`, so six pixels of rule ran **straight through the bar** —
invisible at 3×, obvious on glass. Also the bars were too thin and sat too
high (now **11 px**, moved down), and the 7D row ran `26%` into
`resets 135h52m`. Fixed on branch `prototype/gauge-glass-fix`. **The lesson is
about method, not geometry: a design signed off on mocks is not signed off.**

⚠ **Body text bottoms out at 13 px, not 11 px.** `waiting for first snapshot`
at 11 px **collides on the `st` and `sh` pairs** on real e-ink; at 13 px they
separate. This is the mode-`"1"` `FT_LOAD_TARGET_MONO` rasterisation effect #54
predicted, now with a reproducible failing string. Applied to both frames'
second lines (`prototype/historic-view` `5a84efe`). **It is a rasterisation
limit, not a size preference — any font other than DejaVu must be re-checked
against that exact string before it is chosen.**

Every frame drew at 4,000 bytes / `display()` 2.29 s / full cycle **4.35 s**,
confirming #56's bench number from an entirely different code path. The frames
were rendered on the Desktop and displayed as prepared 1-bit bitmaps, since the
Pi still has no fonts — so **the 13 px floor wants re-confirming once text is
rasterised on the Pi itself** (Pillow 11.1.0 there).

**Update, same day: [Write docs/spec-eink-rendering.md](https://github.com/peterderkoala/zeropi.display/issues/58)
is closed, and with it the map.** 472 lines, fourteen sections, standing alone
as an implementing session's brief. **§11 supersedes three clauses of the
pipeline spec** — the zero-row clause (§9.3), the rendering stub (§8.6), and
the 300 s Gauge throttle (§7.5) — so do not read those as current. **§14's gap
check took seven calls no ticket had**, the sharpest being that **nothing draws
at process start**: after a reboot the panel keeps whatever image it held,
possibly an arbitrarily stale Gauge frame, undetectable because `monotonic()`
reset with it. The spec draws the Historic View once at startup.

⚠ **The implementation's first job is named in §14**: re-confirm the 13 px
floor with text rasterised **on the Pi** rather than the Desktop. Every frame
verified at the bench was rasterised on the Desktop and displayed as a prepared
bitmap, because the Pi has no fonts; Pillow differs across the two machines.

Frontier — **empty. The map is closed.** (Historic, for reference:)
~~[Design the Historic View](https://github.com/peterderkoala/zeropi.display/issues/52)~~ (**closed**, see above),
~~[Which font the panel draws with](https://github.com/peterderkoala/zeropi.display/issues/54)~~ (**resolved and closed at charting** by a research subagent — findings in `docs/research/eink-fonts.md` on the unmerged branch `research/eink-fonts`; no decision taken, the spec ticket picks),
~~[Re-settle ADR-0010's 300 s freshness bound](https://github.com/peterderkoala/zeropi.display/issues/55)~~ (**closed**, see above),
~~[How a 2.29 s refresh coexists with the BLE event loop](https://github.com/peterderkoala/zeropi.display/issues/56)~~ (**closed**, see above).
~~[Where the Historic View's data comes from](https://github.com/peterderkoala/zeropi.display/issues/53)~~
and ~~[Look at the design on real glass](https://github.com/peterderkoala/zeropi.display/issues/57)~~
(both **closed**, see above). [Write docs/spec-eink-rendering.md](https://github.com/peterderkoala/zeropi.display/issues/58)
is **unblocked and is the only thing left on this map** — every decision it
needs is now taken. Writing it reaches the destination and closes the map;
implementation is then its own map, opened against the finished spec.

Branches it must draw on, all unmerged: `prototype/historic-view` (the design
and its mocks), `prototype/gauge-glass-fix` (the corrected Gauge frame),
`bench/render-blocking` (the event-loop measurements), `research/eink-fonts`
(the font facts).
Full bodies live on the tickets — read them there, not here.

⚠ **The font research also confirmed the stale-base warning below**: the
subagent's worktree came up on an unrelated "Initial commit", not `dev` — it
branched from `origin/dev` explicitly instead. That is now three for three.
**Always confirm a fresh worktree's base before handing it real work.**

⚠ **#56 had a hidden bite and it bit** — see the update above. The short
version for anyone writing Pi-side code: **nothing may block the bluezero
event loop for more than about five seconds**, and a full panel refresh is
4.35 s.


**#41 is CLOSED (2026-09-09)** — destination reached, all seven children
resolved. It was charted 2026-09-06 against #13's finished spec. Destination: `docs/spec-usage-pipeline.md` implemented, tested, and
verified end-to-end on real hardware. **Execution-mode** — its Notes
override "plan, don't do" since the spec's own gap check (§13) already
closed every decision; the seven child tickets are build-and-verify slices,
not decisions to grill. Full ticket bodies and blocking edges live on the
map itself — don't re-derive them here, read
[the map](https://github.com/peterderkoala/zeropi.display/issues/41).

**Update, same day: the four-ticket frontier is closed.** #42, #43, #44 and
#45 were each run as a parallel worktree-isolated subagent and merged into
`dev` sequentially (#45 first, since #42/#43/#44 all wanted its pytest
harness for TDD despite the ticket's own "no inter-dependencies" framing —
a real practical dependency the map didn't surface). All four landed clean,
each closed its own ticket after a `/code-review` pass caught and fixed real
bugs (see each ticket's closing comment for specifics — a shared Gauge/Historic
dirty flag in #44, a `NOT NULL` crash on bad timestamps in #42, a corrupt-JSON
crash in #43, a wrong fixture-README claim in #45). Full suite after all four
merges: **118 passed, 0 skipped.** `desktop/usage.py`, `desktop/gauge.py` and
the rewritten `pi/receive.py` all now exist and are independently unit-tested
against the synthetic fixture — none of them touch BLE.

**Unblocked: [Rewrite desktop/push.py — the transport (#46)](https://github.com/peterderkoala/zeropi.display/issues/46)**
(was blocked by #42, #43, #44 — all closed). Chained behind it:
[Build the resident systemd service (#47)](https://github.com/peterderkoala/zeropi.display/issues/47)
(blocked by #46) → [Verify the pipeline end-to-end on real hardware (#48)](https://github.com/peterderkoala/zeropi.display/issues/48)
(blocked by #47 and #45, both now satisfied). These three are chained, not
parallelizable — #48 in particular wants the dev Pi, check the Environment
notes below before touching it.

**Update, same day: #46 is closed too.** `desktop/push.py` is rewritten per
spec §7 against `usage.py`/`gauge.py` (kept the existing BLE mechanics —
scan-by-service-UUID, one held connection, `_acquire_mtu`, no `finally:
stop_notify` — §10 traps #2/#4 untouched). The BLE-calling code is a thin
shell around a dependency-injected `send_one` callable, so the Batch loop,
the Gauge push, wipe handling and CLI dispatch are unit-tested with a fake
radio (30 new tests, 153 total). Two functions are the seam #47's resident
service should import directly rather than subprocessing into the CLI:
`run_batch_pass(store_path=None)` and `run_gauge_push(store_path=None)` —
both return without ever touching BLE if there's nothing to send.

⚠ **A `/code-review` pass caught a real spec violation before this landed**:
the first draft only checked `wiped: true` on the Daily-batch Ack path, so a
wipe signalled on a Gauge Ack (entirely plausible — a resident service's 30 s
Gauge poll fires far more often than the 04:00 Batch) would have been
silently dropped, reproducing exactly the "Pi sits permanently empty" bug
§7.2 exists to prevent (see #36's hand-off facts below). Fixed: a wiped Gauge
Ack now clears every `pushed_at` and runs one extra Batch pass in the same
invocation, same as the Daily path. Review also caught `--resend-all
--gauge-only` silently clearing marks without ever re-Batching them — now
rejected by `parse_args`. **If you write another Ack-consuming code path,
check `wiped` on it regardless of Payload kind — this is the second time the
"of either kind" clause in §7.2 has almost been missed.**

**Verified `--dry-run` against the maintainer's real logs (§11.4's
acceptance check)**: correct Project Labels via R1 for both the main project
and its worktree, all Payload sizes well under the 514-byte budget, and a
real (non-null) Gauge state. Also exercised `--batch-only`/`--gauge-only`
against the **actual dev Pi** — it answered the scan, connected, and
negotiated the MTU fine (confirming the BLE mechanics carried over
untouched), but rejected every Payload with `missing field(s): usage_tokens,
oneliner` — **that Pi is still running the old milestone-1 `receive.py`, not
the #44 rewrite on `dev`**, so it hasn't been re-provisioned yet. Not a
`push.py` bug; flagging it here so #48 (hardware verification) or whoever
next touches the dev Pi knows to re-run `install-pi.sh` (or otherwise deploy
the rewritten `receive.py`) before expecting a real round trip. `push.py`'s
own behavior was correct throughout: continue-past-failure, no row marked
pushed, non-zero exit on a wholly-failed Batch, Gauge failure dropped
silently with exit 0.

**Update, same day: #47 is closed too.** `desktop/service.py` is the
resident `systemd --user` loop from spec §7.5, built directly against #46's
seam (`push.run_batch_pass`/`push.run_gauge_push` — no subprocessing). Two
pure, clock-injected classes carry the decision logic: `GaugeGate` (the
300s-throttled, coalescing Gauge-push trigger off `DisplayedGaugeState` —
`five_hour`/`seven_day` `used_percentage` value-or-null-ness plus
`resets_at`; the context percentage is deliberately excluded per §13
judgment call #5) and `BatchScheduler` (04:00-local plus a >24h-stale
startup catch-up). `run_forever` itself takes injectable clocks/IO, so the
wired loop — not just the two pure classes — is driven directly in tests
with no real sleeping. Ships `desktop/zeropi-push.service` (unit file only,
per the ticket; #34 still owns installing it). 30 new tests, **181 total.**

⚠ **`/code-review` caught a real wiring bug**: `batch_in_progress` was reset
in a `finally` immediately after the Batch's own `await`, before
`gate.observe()` ran — so "the Gauge waits for an in-flight Batch" never
actually held within one tick, and a Gauge push could open a second BLE
connection back-to-back with the Batch's. Fixed by resetting the flag only
at the top of each tick (so it survives through that tick's Gauge check and
only clears for the *next* one); added a regression test verified to fail
against the reverted buggy code.

⚠ **Flagged, not fixed (out of #47's scope)**: `install-desktop.sh`'s
standalone mode (the common non-maintainer-Desktop path) currently deploys
only `push.py` into `~/.local/share/zeropi-display/` — not `gauge.py`,
`usage.py`, or now `service.py`. The shipped unit file's `ExecStart`
targets that standalone layout by convention, but it won't actually run
there until this is fixed. Whoever next touches #34 or provisions a
standalone Desktop for real should know this.

**Update, 2026-09-09: #48 is closed, and with it the map.** The pipeline is
verified end-to-end on real hardware — see `docs/usage-pipeline-verification.md`
and the summary at the top of this file. Headline numbers: a 10-Reading Batch is
**10 sent, 0 failed** at MTU 517 with ~2 s for the ten sequential Ack round
trips; Payloads run **347–390 bytes** against the 514 budget; the redraw floor
coalesced a second Gauge Payload sent 0.08 s after the first (`drawn: true` then
`drawn: false`, one `render:` line); and the **wipe/hand-back recovery works on a
Gauge Ack specifically** — the exact path #46's review caught missing. The
resident service's startup catch-up fired 8 s after start and pushed all ten
Readings with no operator involvement.

⚠ **Before you next re-provision the Pi**, note that the run had to fix the Pi's
unit file (`31bb8e7`) and the Desktop's (`94665e3`) for `PYTHONUNBUFFERED`; the Pi
is currently on `31bb8e7`, so it is one commit behind `dev`'s tip in its VERSION
stamp but functionally current (`94665e3` and `a45d964` touch only the Desktop
unit and docs).

**Historic note, superseded — #48's own instructions for running the service**: to
run this loop from a repo clone with `.venv` set up: `.venv/bin/python desktop/service.py`
(`--store PATH` to override the store). To exercise the actual systemd
unit, copy `desktop/zeropi-push.service` to `~/.config/systemd/user/`,
rewrite `ExecStart` to the in-place layout (`<repo>/.venv/bin/python3
<repo>/desktop/service.py`), then the usual `daemon-reload` / `enable --now`
/ `journalctl -f` dance. The loop's first tick always attempts a startup
Batch catch-up, so a Batch pass (and a BLE connection attempt) should show
up in the log immediately.

⚠ **If you're running #42/#43/#44/#45 as parallel sessions or subagents,
give each its own worktree** — trap 12 in the spec (§10) records a prior
collision from sharing one working tree across parallel agents. Also: #44
(the Pi rewrite) and #48 (hardware verification) will want the dev Pi —
check the Environment notes below before touching it, since it's shared.
⚠ **Every one of #42/#43/#44's spawned worktrees came up on a stale base**
(the repo's bare initial commit, not `dev`'s tip) rather than `dev` as
requested — each agent had to `git reset --hard`/fast-forward onto `dev`
itself before starting. Confirm a fresh worktree is actually on `dev` before
handing it real work; don't assume the isolation tooling got the base right.

**#13 is closed** (2026-09-06). All 20 child tickets resolved and the spec —
`docs/spec-usage-pipeline.md` — is on `dev` (`d925bb0`, extended by `e93d80d`).
#41 is the implementation map opened against it.

**#7 is closed** (2026-09-06). Its destination — both ends of the link
reproducible from scratch through one documented `curl ... | bash -s --
<role>` command — was fully reached, and every child ticket resolved: #8-#11
(the original Pi BLE provisioning), #33-#35 (the curl-delivery redraw), and
#39-#40 (e-ink panel provisioning, added mid-map). **#40 (2026-09-06) closed
the last gap**: `install-pi.sh`'s e-ink panel steps (SPI persistence, the
four apt packages, deployment) had never actually executed, since #35's
hardware run predated the branch merge that added them. Teardown +
documented one-liner + reboot all passed with no manual steps, and the panel
glass was finally looked at by a human (border and all eight alternating
blocks clean). Write-up: `docs/eink-driver-verification.md`'s "Provisioning
verification" section.

One loose end, deliberately left as fog rather than a ticket: **whether this
ex-pwnagotchi HAT wires `PWR_PIN` on BCM 18** is still unconfirmed (open
since #23, not settled by #39 or #40 — no multimeter/LED on hand either
time). It is a hardware-characterization question, not a provisioning one,
so it belongs to whichever effort first drives the panel for real, not to
#7.

#39 also moved one of #7's scope lines, so read it before assuming the old
boundary: **e-ink *driver provisioning* is now in scope for #7**
(`install-pi.sh` owns SPI and the panel's apt stack, on the same "no
hand-applied system state" logic as the rest of the map), while **e-ink
*rendering* is still out** and wants its own map when it starts. The #8
decision's "no e-ink HAT provisioning yet" clause is struck through in the
map body rather than deleted.

### Current: [Real Claude Code usage read, pushed, and stored in SQLite (#13)](https://github.com/peterderkoala/zeropi.display/issues/13)

Charted 2026-09-04. **Destination redrawn 2026-09-05** — read the map body
before anything else, including the redraw banner at the top.

The original map specced the daily-aggregate pipeline. The maintainer's
actual goal is a **live usage gauge**: current consumption against the
rolling 5-hour limit window (ideally a percentage), the weekly limit if
obtainable, and the **context size of the active session**. History is
demoted to a supporting role — an average/trend graph — but survives as
specced.

**The redraw invalidated some settled decisions.** They are struck through
in the map's tables rather than deleted, so you can see what changed:
cadence is no longer out of scope, and the "cost is the headline" decision
now governs only the historic view.

**[#24 (the hinge) is resolved and closed** — see its resolution comment and
the map's Decisions-so-far for the six-part answer (active-session rule,
context-size-as-percentage, ephemeral live gauge, two Payload shapes, an
explicit "no data yet" null state, and a 5-minute Pi-side staleness mark).
Resolving it unblocked four tickets at once.

**[#28 (Desktop-side usage store) is also resolved and closed.** File
location, ingest incrementality, winner-rank timing, store-only-aggregation
and schema are all settled — see the map's Decisions-so-far. Unblocked #30.

**[#29 (dev-era capture) is also done and closed.** The entries live at
`~/.local/share/zeropi-display/usage-archive.db` — 6,201 rows, 1.59 MB, not
committed. Quarry for #20's future test fixture; unblocked nothing further.

**[#16 (multi-row transport protocol) is also resolved and closed.** One
connection per loop, sequential Acks, continue-past-failure with push-marks
recovery, newest-day-first ordering, an explicit Ack correlation field
(`date`/`project`/`model` echo), an explicit batch marker
(`batch_size`/`batch_index` on the Payload), unchanged 10s per-row timeout,
unchanged characteristic UUIDs. See the map's Decisions-so-far for the full
eight-part answer. Flagged two new fields for #17, which is now resolved
(see below).

**[#17 (the new SQLite schema on the Pi) is also resolved and closed.**
`receive.py`'s `init_db()` stays sole owner, made self-healing and
version-gated (`PRAGMA user_version`; drop+recreate on mismatch — same path
for a fresh Pi and the maintainer's already-provisioned one). `install.sh`
untouched. Lands **after #11 closes** — a sequencing note, not a checklist
change, since install.sh isn't touched. Table keeps the name `readings`.
Full DDL (readings + a key-value `meta` table for `coverage_start`,
auto-derived on every insert) in the resolution comment. Unblocked #19.

**[#36 (what a second Desktop means for the data) is resolved and closed.**
Charted this session by graduating the fog entry the previous handoff flagged,
then resolved. **Its premise was wrong and that is the main result**: this is a
*lifecycle* question, not a concurrency one. The maintainer wants one Desktop
at a time, with the Pi **freshly couplable to a different Desktop** — so no
machine dimension enters the grain (#17's PK and #16's Ack fields both
untouched), the machine id is a **scalar in the `meta` table**, and the Pi
**drops and recreates `readings` when it changes**. See its resolution comment
for the eight-part answer. Unblocked #19.

**[#19 (vocabulary and ADRs) is resolved and closed** — second ticket of that
session. `CONTEXT.md` and `docs/adr/0003`–`0006` are on `dev` in `9ac14ba`.
**Payload keeps meaning one BLE write**; the set is a **Batch**, the two shapes
are **Daily Payload** / **Gauge Payload**, and nine further terms are pinned
(**Desktop Id**, **Usage**, **Gauge**, **Project Key** vs **Project Label**,
**Window**, **Cost Complete**, **Coverage Start**). Four ADRs written, and the
`(date, project, model)` **grain was deliberately refused one**. Read
`CONTEXT.md` before naming anything in #20's spec — that is now the binding
vocabulary, not this handoff.

**[#25 (push cadence and redraw floor) is resolved and closed** — this
session's ticket. Eleven decisions; the deliverable is **push on any integer
change** (a resident `systemd --user` service polling the snapshot every 30 s),
a **300 s redraw floor enforced by the Pi as a hard gate**, **full refresh
only — no two-speed**, and **idle showing the historic view, held**. Two ADRs
written (`0007`, `0008`) and the term **Limit Window** added to `CONTEXT.md`.
Full detail in its resolution comment and the map's Decisions-so-far.

⚠ **It did not shorten the critical path — it spun out
[#37](https://github.com/peterderkoala/zeropi.display/issues/37)**, which
blocked #20 in #25's place. **#37 has since closed** — see below.

**[#26 (the live-gauge prototype) is resolved and closed** — also this
session. Branch `prototype/live-gauge`, mocks committed at
`docs/research/gauge-mocks/`. **The gauge tells the truth and the layout is
legible, but the prototype overturned three of #24's paper decisions and
corrected two facts this handoff had recorded as settled.** Read its
resolution comment before touching the gauge. Spun out
[#38](https://github.com/peterderkoala/zeropi.display/issues/38).

⚠ **Two tickets in, two tickets out.** #25 and #26 both closed that session and
the critical path to #20 was the same length: #37 and #38 replaced them. That is
the prototype doing its job — both new tickets exist because contact with real
data and a real panel invalidated decisions made on paper.

**[#37 (how the Pi knows the time) is resolved and closed** — 2026-09-05,
latest session, and it **spun out nothing**, so the critical path finally got
shorter. **The answer is that the Pi does not know the time and no longer needs
to.** Three of its four open decisions dissolved rather than resolving. The
wire now carries **durations, not instants**: a **Reset Countdown** in place of
`resets_at`, a snapshot age in seconds in place of `updated_at`, both computed
on the Desktop, both advanced on the Pi with `time.monotonic()`.
[ADR-0009](../blob/dev/docs/adr/0009-pi-is-given-durations-not-timestamps.md)
is on `dev`; **Reset Countdown** and **Gauge Age** are in `CONTEXT.md`.
⚠ **It supersedes the time fields #24 and #25 assumed** — read it before
writing the Gauge Payload's shape into #20.

**[#38 (re-settle the gauge readout) is resolved and closed** — 2026-09-06,
and it **spun out nothing**. **Two of its five decisions dissolved, and the
gauge lost a row, a footer and a readout**: what is left is one split headline
row, one 7D row, and white space. **Layout C confirmed.** The **context
readout is dropped from the display** — that **narrowed the map's Destination**
— though the **field stays in the Gauge Payload** by the maintainer's call, so
the spec must still define active-session detection and the context computation
for a value nothing draws. **There is no stale rendering, because an expired
Gauge is not drawn**: at 300 s of Gauge Age the panel falls back to the
Historic View. Null reads **`NO USAGE DATA`**; the countdown clamps to `<1m`
then **`RESETS NOW`**; the idle panel gains a **24-hour keep-alive refresh**
(amending #25). [ADR-0010](../blob/dev/docs/adr/0010-an-expired-gauge-is-not-drawn.md)
written, **Historic View** added to `CONTEXT.md`, ADR-0008 amended. Settled
design rendered at `docs/research/gauge-mocks/settled-*.png` on
`prototype/live-gauge` (`dde1c58`) — **drawing it broke it twice**, which is
why it was drawn.

**[#20 (the spec) is DONE and closed** — 2026-09-06, and it is **the map's
destination**. `docs/spec-usage-pipeline.md` is on `dev` (`d925bb0`): thirteen
sections, written to stand alone as a session's brief. ⚠ **It names
`CONTEXT.md` and ADRs 0003–0010 as the only other required reading and says
outright that THIS FILE is not a source of truth.** If the spec and this
handoff disagree, the spec wins.

It corrected three places the older material still disagreed with the settled
position, all of which would have bitten an implementer: the **#26 prototype's
Gauge Payload sends instants** (ADR-0009 superseded that — durations only), the
**#18 prototype aggregates on the Project Label** (#36 made the Project *Key*
the stored key), and **#17's DDL still carries `received_at`** while milestone
1's Payload still carries `oneliner` — neither survives.

The gap check found **twelve** places an implementer would have had to invent
an answer, all closed and recorded in the spec's §13. The two worth knowing
here: **Gauge Age is seeded with `snapshot_age_s`** (without it ADR-0010's
"nothing on the panel is untrustworthy" is only approximately true, and the
field has no other consumer), and **the Desktop store refuses to run on a
schema mismatch rather than dropping** — deliberately the opposite of the Pi's
version gate, because one is an archive of record and one is a rebuildable
cache. **Spun out nothing.**

Frontier — **empty**. Every child of #13 is closed.

[Pi retention/pruning (#30)](https://github.com/peterderkoala/zeropi.display/issues/30),
the last one, was resolved 2026-09-06: **no pruning, on either end.** Measured
at the grain the Pi stores, the machine's entire history is **18 rows** over 41
calendar days (mean 1.8/active day), and growth is bounded by the Pi's uptime
rather than by log history. The invariant is *the Pi never deletes a Reading on
size grounds*; `wiped` stays exclusive to the Desktop-Id change (answering the
question #36 left); there is no operator reset command (`rm data.db` + restart
is the path); and the Desktop archive is never pruned either, as an ADR-0005
corollary. Recorded as spec §8.7 and §4.5, plus a deletion lifecycle on
`CONTEXT.md`'s **Reading**. No ADR — additive and easily reversed.

**The map's destination is reached and nothing is open on it. Closed
2026-09-06.** Implementation is **its own map**, opened against the finished
spec.

**[#31 (context-window research) is resolved and closed.**
`docs/research/context-window-table.md` (branch `research/context-window-table`,
unmerged) found that the commonly-assumed 200K window is wrong for the two
models that matter most: Opus 5 and Sonnet 5 both carry a **1,000,000-token
window** (combined input+output), now GA with no pricing surcharge — doesn't
touch #14's pricing table. Haiku 4.5 stays at 200K, no extended option. Max
output: 128,000 for Opus 5/Sonnet 5 (300K on Batch API beta), 64,000 for
Haiku 4.5. This is an input to whatever ticket implements #24's
percentage-against-a-per-model-table decision — no open frontier ticket
consumes it yet, but it'll matter once #17 (schema) or the eventual spec
touches the context-size field.

**Unblocked: [#20 the spec](https://github.com/peterderkoala/zeropi.display/issues/20)**
— all twelve blockers closed (#16, #17, #18, #19, #21, #24, #25, #26, #27, #28,
#36, #37, #38).

⚠ **`issue_dependencies_summary.blocked_by` lags.** It read `0` for #30
immediately after the edge was created, while
`gh api repos/<owner>/<repo>/issues/30/dependencies/blocked_by` correctly
listed #28. The tracker doc's frontier query leans on that summary field —
confirm against the `dependencies/blocked_by` list before treating a ticket
as takeable.

**The primary goal is achievable, but not the way this map first assumed.**
[#22](https://github.com/peterderkoala/zeropi.display/issues/22) (closed)
overturned the framing: there is **no denominator and none is needed** —
Anthropic computes utilization server-side and Claude Code carries
`five_hour` / `seven_day` percentages with their `resets_at` ready-made
(**field naming differs by source — see #27 below**). The
"hand-configured constant" fallback recorded earlier was **withdrawn as
unsound**: the limit is not a token count, so there is no number to configure.

**The load-bearing corollary**: the locally-parsed token sum is **not
proportional** to limit consumption. Never show it as a proxy for the gauge —
they are different quantities.

[#23](https://github.com/peterderkoala/zeropi.display/issues/23) (closed) set
the hardware floor: **minimum safe panel update is 180 s, 300 s recommended**
on a second-hand panel. So "live" means a ~5-minute gauge, not real-time. If
that disappoints, *time-until-window-reset* may read better than a
slowly-creeping percentage — flagged on #25.

Closed: [#14 pricing](https://github.com/peterderkoala/zeropi.display/issues/14)
(`docs/research/pricing-table.md`, branch `research/pricing-table`),
[#15 dedup](https://github.com/peterderkoala/zeropi.display/issues/15)
(`docs/research/dedup-rules.md`, branch `research/dedup-rules`) and
[#18 prototype](https://github.com/peterderkoala/zeropi.display/issues/18)
(`desktop/usage_prototype.py`, branch `prototype/usage-reader`). All three
branches are unmerged; the findings are summarised in the map body.
Also closed: [#21 backfill](https://github.com/peterderkoala/zeropi.display/issues/21),
which spun out #28, #29 and #30 — read its resolution comment before touching
any of them. Also closed: [#24 the live-usage data model](https://github.com/peterderkoala/zeropi.display/issues/24),
which spun out #31; [#31 context-window research](https://github.com/peterderkoala/zeropi.display/issues/31)
itself (`docs/research/context-window-table.md`, branch
`research/context-window-table`); [#28 the Desktop-side usage store](https://github.com/peterderkoala/zeropi.display/issues/28),
which unblocked #30; [#29 the dev-era capture](https://github.com/peterderkoala/zeropi.display/issues/29)
(`~/.local/share/zeropi-display/usage-archive.db`, 6,201 rows, not committed);
[#16 the multi-row transport protocol](https://github.com/peterderkoala/zeropi.display/issues/16);
and [#17 the new SQLite schema](https://github.com/peterderkoala/zeropi.display/issues/17),
which unblocked #19.

**The prototype is worth running before you touch this pipeline** —
`python3 desktop/usage_prototype.py` on `prototype/usage-reader` prints the
rows a push would send from your real logs, with the dedup delta and the
cache-write TTL error measured live. It is throwaway, not the implementation.

### Also open: [Both ends reproducible from scratch (#7)](https://github.com/peterderkoala/zeropi.display/issues/7)

**Destination redrawn 2026-09-05** — read the map body first, including the
banner and the new **Delivery shape** section, which binds all three
tickets.

The original destination (a stock Pi reproducible from scratch) was
**reached** by #11. Rather than close, the map was redrawn to cover the two
things it had listed as unspecified: **delivery** (getting code onto a Pi
was a hand-run `scp`) and the **Desktop end**, which had no provisioning at
all. Desktop-side provisioning moved **out of Out-of-scope and into scope**
— struck through rather than deleted, so the reversal is visible.

The redraw's decisions came from a grilling session, not a ticket, so they
live in the map's **Delivery shape** section. The load-bearing ones:

- **Tarball, not a clone** — `git` is *not installed on the Pi* and costs
  ~50 MB on a Zero; the branch tarball is 36 KB. This overturned the
  maintainer's own opening instruction ("clones the repo"), deliberately.
- **Fetched by sha, not by branch.** A branch tarball unpacks to
  `zeropi.display-dev/` and carries **no version identity** — precisely what
  a clone would have given for free. Resolve ref → sha, fetch
  `/archive/<sha>.tar.gz`, stamp `VERSION`.
- **One root `install.sh`, role by argument**, running unprivileged, with
  the `pi` role re-execing under `sudo`.
- **Never "client"/"server"** in names — `CONTEXT.md` lists both as terms to
  avoid. It is `install-pi.sh` / `install-desktop.sh`.
- **The Desktop role works in-place *and* standalone**, because it must run
  on machines that are not the maintainer's; it detects a surrounding clone
  and says which mode it picked.
- **Points at `dev`** — no `dev` → `main` PR yet, the maintainer's call.

**[#33 (the curl bootstrap) is resolved and closed** — 2026-09-06. Repo-root
`install.sh` resolves `ZEROPI_REF` (default `dev`) to a commit sha via the
GitHub API, fetches/unpacks the sha tarball to a `/tmp` staging dir, and
delegates to `pi/install-pi.sh` (re-exec'd under `sudo`) or
`desktop/install-desktop.sh`. `pi/install.sh` renamed to `pi/install-pi.sh`
— needed only the rename plus VERSION-stamping (`sha`/`ref`/`installed_at`
at `/opt/zeropi-display/VERSION`); `SCRIPT_DIR` already resolved correctly
under the bootstrap via `BASH_SOURCE`, so **don't re-add a staging-root argv
override** — one was tried, flagged by review as unneeded complexity that
silently changed the script's argument contract, and reverted.
`desktop/install-desktop.sh` is a stub (`exit 1`, points at #34) whose
header comment fixes the contract #34 builds against: argv[1] is the
staging root, env carries `ZEROPI_REF`/`ZEROPI_SHA`/`ZEROPI_TIMESTAMP`,
always unprivileged. `data.db` untouched by construction — the deploy step
copies only the files it owns, never syncs a directory wholesale. Commits
`b6eaa2e`, `17c3182` on `dev`; full detail in the issue's resolution
comment.

⚠ **`sudo`'s password prompt breaks under the documented one-liner run
non-interactively** — `curl -fsSL ... | bash -s -- pi` leaves `sudo` with
the exhausted curl pipe as stdin and no controlling terminal, which fails
confusingly (not a hang) without `ssh -t`. `install.sh` now checks for
`/dev/tty` (or already-passwordless sudo) up front and fails with a clear
message and the `-t` fix instead. Relevant if #34 or #35 touch invocation.

⚠ **CONTEXT.md's avoid-list bites documentation too, not just code** — this
session's README draft called Pi "the BLE receiver" and Desktop "the BLE
sender," both on the avoid-list (`_Avoid_: Server, receiver` /
`_Avoid_: Client, sender`). Caught by review, not by writing it. Check new
prose against the avoid-lists before it ships, not after.

**[#34 (desktop/install-desktop.sh) is resolved and closed** —
2026-09-06, `dev` (`6b86e94`). Two modes, auto-detected: a real clone
(`.git` present — checked with `-e`, not `-d`, so a **git-worktree**
checkout counts too, since worktrees make `.git` a file not a directory)
gets `.venv` set up in place; anything else, including every curl-bootstrap
run (a GitHub archive tarball never carries `.git`), installs standalone to
`~/.local/share/zeropi-display/` with a `zeropi-push` shim on `PATH`.
`--in-place` / `--prefix <dir>` override the detection. Linux-only refusal
up front (#32's BlueZ-specific bleak API). **Idempotent, and deliberately
non-destructive of an existing venv**: one already present but missing pip
(e.g. one made by `uv venv`, this project's own documented dev setup) gets
pip added via `ensurepip` rather than `rm -rf`'d, and `python -m pip` is
used throughout since ensurepip's entry-point names aren't guaranteed
(observed: `pip3`/`pip3.12` but no bare `pip`). VERSION is stamped inside
the venv, not the install root — for in-place that root is the
maintainer's tracked checkout, where a stray file would be clutter. The
end-of-install reachability check reuses `push.py`'s own
`matches_service()`/`SERVICE_UUID` (no duplicated UUID to drift) and
**warns rather than fails** if no Pi answers, since the two roles are
provisioned independently. Verified live: a push through the standalone
shim round-tripped against the dev Pi. README documents the desktop
one-liner and the override flag. **Review caught three real bugs before
landing**: the worktree-is-a-file case, the destructive `rm -rf` on a
pip-less venv, and a broken doubled `--` in the README's override example —
all fixed. Unblocked #35.

**[#35 (hardware verification of both roles) is resolved and closed** —
2026-09-06. Write-up: `docs/curl-delivery-verification.md`. Both roles
install, survive reboot and a `bluetoothd` restart unattended, and
round-trip reliably (12/12 this session, 18 total rows) through the single
documented one-liner. **Found and fixed two real defects, both specific to
non-interactive automation of the curl path** (a maintainer typing the
one-liner at a real terminal would not have hit either): (1) `install.sh`'s
`[[ -e /dev/tty ]]` check is true even with no controlling terminal at all,
so a plain non-pty `ssh host 'curl ... | bash -s -- pi'` crashed instead of
falling back to passwordless sudo — replaced with an open/close probe run
in a subshell; (2) `install-desktop.sh`'s in-place detection checked its
own script location, which under the curl bootstrap is always a `/tmp`
staging unpack that never carries `.git` — so in-place could **never** fire
through the documented invocation path, silently installing standalone
even from inside the tracked clone. Fixed to detect from the invoking
shell's `$PWD` instead, which survives the pipe unchanged. **Desktop
standalone** ran on the same physical machine from a non-clone directory —
no second machine was available, so cross-machine/cross-OS behavior is
still unverified. Also: this run collided in real time with a concurrent
session's e-ink driver work (#39) on the same shared dev Pi — coordinated
directly, confirmed no disruption either way. **This session's own scope
was the `dev` branch**, which doesn't carry #39's e-ink panel steps
(unmerged, see below) — so despite a note left on #39 expecting otherwise,
that hardware verification was never reachable from here and is carried
forward as fog.

Frontier — **one open ticket, #40.** The map's original destination is fully
reached. What remains is the gap this run could not close: #39's e-ink panel
steps in `install-pi.sh` have never executed. That branch has **since been
merged to `dev`** (and #39 is a child of this map, added after this run), so
the fog is now a takeable ticket rather than a note — see #40.

**Update, same day: #40 closed too.** See the Maps section at the top of
this file for the full result — the frontier described above is now empty.

Also spun out, **not** a map child:
[#32](https://github.com/peterderkoala/zeropi.display/issues/32) —
`push.py`'s `_acquire_mtu()` is a private BlueZ-specific `bleak` API, so the
Desktop is Linux-only. Ruled out of scope for #7 for the same reason as #12
(a code wart, not an installation concern); `install-desktop.sh` refuses
non-Linux loudly instead.

**The original four tickets are closed** (#8, #9, #10, #11); three new ones
(#33, #34, #35) came from the redraw. #11 verified the Pi path on hardware: `install.sh` runs clean
from a torn-down Pi and is idempotent, reboot and `bluetoothd` restart both
survive unattended, **20/20** consecutive pushes and **23/23** round trips
with 0 `bluetoothd` crashes. Write-up: `docs/provisioning-verification.md`.

**#17's sequencing gate is now lifted** — it settled that the new SQLite
schema lands *after* #11 closes. It has closed, so #17's schema change is
free to land.

#11 left the fresh-card caveat standing: no spare was available, so "from
scratch" meant tearing the hand-applied state off the dev Pi —
`python3-gi` was never removed, BlueZ never downgraded, first-boot state not
reproduced. Still fog on the map. Its other open item, how code reaches the
Pi, is what the redraw answers.

### Closed: [Milestone 1 BLE prototype (#1)](https://github.com/peterderkoala/zeropi.display/issues/1)

Destination reached; all five tickets (#2–#6) and the map itself are
closed. The round trip is verified on real hardware — see
`docs/e2e-verification.md` and the closing comments on #6 and #1.

## Hard-won facts — do not relearn these

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
- **`mattpocock-skills:wayfinder`** with map #51 — **closed 2026-09-09.**
  Nothing to grab; all seven children resolved.
- **The bench session (#57) is the one that needs a human.** It is unblocked
  now: mocks exist and the font options are known. Everything else on the map
  can be driven without leaving the terminal.
- **`mattpocock-skills:wayfinder`** with map #41 — **closed 2026-09-09.**
  Nothing to grab; all seven children resolved.
- **`mattpocock-skills:tdd`** — the map's Notes recommend it for each unit,
  since the spec (§11) is written test-first-friendly: a synthetic fixture
  with 14 named cases and explicit per-unit assertions (§11.3).
- Read `docs/spec-usage-pipeline.md` before touching any of #42/#43/#44/#45 —
  it is the binding source, not this file, not map #13's closed decisions.
- **`mattpocock-skills:wayfinder`** with map #13 — **closed, 2026-09-06.**
  Nothing to grab; superseded by #41.
- **`mattpocock-skills:wayfinder`** with map #7 — **closed, 2026-09-06.**
  Nothing to grab; the map itself no longer exists as an open effort. One
  loose end is deliberately fog, not a ticket: whether this HAT wires
  `PWR_PIN` on BCM 18 — logged in the closed map's Not-yet-specified for
  whoever charts the e-ink-rendering map it belongs to.
- **`mattpocock-skills:grilling`** has no open decisions left on #13 or #7 —
  all resolved. Reach for it on the implementation map when that is charted.
- **`mattpocock-skills:domain-modeling`** is **done for now** — #19 rewrote
  the vocabulary and superseded ADR 0001. Reach for it again only if #25 or
  #26 coins a term the glossary does not have.

## If you run subagents, isolate them

Two research subagents were run in parallel from the same working tree on
2026-09-04 and their git operations collided — one agent's commit landed on
the other's branch. No damage (`dev` and `main` were untouched) and it was
repaired with a fast-forward, but `research/dedup-rules` still carries the
pricing commit as a result. **Give parallel agents their own worktrees.**
