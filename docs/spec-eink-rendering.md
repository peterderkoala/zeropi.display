# Spec: e-ink rendering

**Status**: binding. This document is the brief for the session that makes the
panel draw. It is the destination of
[map #51](https://github.com/peterderkoala/zeropi.display/issues/51) and was
written from that map's seven resolved tickets; **implementation is a separate
map**, opened against this document.

**Required reading, and nothing else is required**: `CONTEXT.md` (the binding
vocabulary), ADRs [0007](adr/0007-full-refresh-only-no-two-speed.md),
[0008](adr/0008-pi-enforces-the-redraw-floor.md),
[0009](adr/0009-pi-is-given-durations-not-timestamps.md) and
[0010](adr/0010-an-expired-gauge-is-not-drawn.md) — **0008 and 0010 as amended
on 2026-09-09** — and §8 and §9 of
[`spec-usage-pipeline.md`](spec-usage-pipeline.md).

⚠ **`handoff/handoff.md` is not authoritative.** It is a running log for
continuity. Where it and this document disagree, this document wins.

⚠ **Three clauses of `spec-usage-pipeline.md` are superseded here**, listed in
§11. Read that section before assuming the older spec is current.

---

## 1. What you are building

`pi/render.py`: the module that turns the Pi's own state into pixels on the
Waveshare 2.13" V4 panel, and the changes to `pi/receive.py` and
`pi/install-pi.sh` that put it to work.

Today `receive.py` has a `render(view)` that prints one line. Everything above
it — the Gauge state machine, the redraw floor, the expiry fallback, the
Reading store — already works and is hardware-verified
([#48](https://github.com/peterderkoala/zeropi.display/issues/48)). **This
milestone replaces the stub and nothing else.**

When you are done: the panel shows the Historic View at rest, the Gauge frame
while a live Gauge is showing, and the right fault frame when there is neither
— on real glass, verified by a human looking at it.

## 2. The panel, as measured

Facts, not estimates. All measured on the dev Pi; do not re-derive them.

| | |
|---|---|
| Panel | Waveshare 2.13" V4, **250×122 landscape**, **1-bit — there is no grey** |
| Framebuffer | **exactly 4000 bytes** (122 is not a multiple of 8, so PIL pads rows to 16 bytes: 16 × 250) |
| `init()` | 0.05 s |
| `display()` | **2.29 s**, consistent across white, black and real frames |
| `sleep()` | 2.00 s — the driver's own fixed `delay_ms(2000)`, not panel time |
| **Full cycle** | **4.35 s** (`init` + `display` + `sleep`), confirmed twice from different code paths |
| Rated duty cycle | one update per **180 s**; our operating point is 300 s (ADR-0008) |

The driver is vendored at a pinned upstream commit in `pi/waveshare_epd/`.
**Read that directory's `README.md` before touching it.** Two properties matter
here: importing `epdconfig` **claims GPIO as a side effect**, and `ReadBusy()`
is an **unbounded `while` loop with no timeout**.

## 3. The font

**`fonts-dejavu-core`, installed by `pi/install-pi.sh` via apt.** Not vendored,
not `ImageFont.load_default()`.

⚠ **The Pi currently has no fonts at all** — `/usr/share/fonts` does not exist
and `find` returns zero font files. This is why the decision is load-bearing
rather than incidental.

Chosen because **it is the only typeface whose rendering has been seen on this
panel**: every frame in §5, and the 13 px floor in §4, were verified on glass
with DejaVu ([#57](https://github.com/peterderkoala/zeropi.display/issues/57)).
The floor is a property of *how a specific typeface rasterises*, not of size in
the abstract, so switching fonts invalidates that evidence and restarts the
glass check. Vendoring was rejected as a licence-review cost for no gain over a
1.44 MB apt package on a machine `install-pi.sh` already provisions;
`load_default()` was rejected because its embedded Aileron has an ambiguous
licence across sources. Facts behind all of this:
`docs/research/eink-fonts.md` on branch `research/eink-fonts`.

## 4. How text behaves at 1 bit, and the floor

⚠ **Drawing into a mode `"1"` image is not "anti-alias, then threshold".** PIL
takes FreeType's `FT_LOAD_TARGET_MONO` path, a genuinely different
rasterisation that produces different letterforms. It visibly diverges at 10 px
and converges with the naive route by 28 px. **Build every frame directly in
mode `"1"`** — never render greyscale and convert, or what you review is not
what the panel shows.

**The floor for lowercase, multi-word text is 13 px.** At 11 px,
`waiting for first snapshot` **collides on the `st` and `sh` pairs** on real
glass; at 13 px they separate.

**The one exception is short upper-case labels**, which are permitted at 10 px:
`5H`, `7D`, `RESETS IN`. These were read on glass without objection. They are
familiar two-to-nine character shapes, not strings anyone parses letter by
letter.

⚠ **One string sits on the wrong side of that line and is kept anyway**:
`resets 135h52m` in the 7D row is 10 px, lowercase and multi-word. It was read
on glass without objection, so it stays as verified rather than as the rule
would predict — but **it is the first thing to re-check** if the 7D row ever
looks wrong, and the first thing to bump to 13 px if a future font changes.

## 5. The frames

Exact geometry. These are the pixels a human approved on the panel
([#57](https://github.com/peterderkoala/zeropi.display/issues/57)); the mocks
are on branches `prototype/historic-view` and `prototype/gauge-glass-fix`, and
the prototype renderers are the reference implementation of everything below.

### 5.1 The Historic View — the resting picture

Not a fallback and not an error state. Idle is the *common* case: the snapshot
only advances while an interactive TUI is open, so this is what the panel shows
for most of the day.

**A list of the most recent Active Days, newest first.** Five rows, pitch 20 px,
first row at `y=1`:

- **Date** at `x=3`, `%d %b` upper-cased (`09 SEP`), **14 px bold**.
- **Cost** right-aligned to `x=104`, `$%.0f`, 14 px bold, prefixed **`~`** when
  the day is not Cost Complete (§6).
- **Bar** from `x=110`, `y+4` to `y+12`, width `138 × (cost / peak)` where
  `peak` is the largest value **on screen**, minimum 1 px so a non-zero day is
  never invisible.
- **Rule** across the full width at `y=101`.
- **Footer** at `y=106`, 12 px: `SINCE <coverage start, %d %b upper>` at
  `x=3`, and `AVG $<mean>` right-aligned to `x=247`.

**Fewer than five Active Days draws fewer rows**, leaving white above the rule.
Verified as reading intentional, not broken.

⚠ **The footer is not decoration.** Without `SINCE`, a day the Pi never
received draws exactly like a day with no work — verified: the same frame
rendered from the full archive and from what the Pi actually held came out
**pixel-identical**. It is the only thing on the panel enforcing the rule that
a never-received date reads as *outside coverage* rather than as zero.

### 5.2 The empty frame

No Readings at all — a fresh or just-wiped Pi. **`NO HISTORY YET`** centred,
20 px bold, at `y=34`; **`nothing pushed to this Pi`** centred, **13 px**, at
`y=62`.

Distinct from §5.4's `NO USAGE DATA`, and the distinction is the point: this
one means *the Pi is new and nothing has been pushed*; that one means *the
Desktop is talking and has nothing to say*. They send you to different places.

### 5.3 The Gauge frame (Layout C)

Settled by [#38](https://github.com/peterderkoala/zeropi.display/issues/38),
**corrected on glass by #57**. Carried across, not redesigned.

- **`5H`** label at `(3, 1)`, 10 px bold; the percentage at `(3, 8)`, **28 px
  bold**.
- **Split rule** from `(126, 0)` to **`(126, 36)`**.
- **`RESETS IN`** at `(132, 1)`, 10 px bold; the countdown at `(132, 8)`,
  24 px bold.
- **5H bar**: `x=3, y=42`, width 244, height **11**.
- **7D row**: `7D` at `(3, 60)` 11 px bold, percentage at `(25, 58)` 13 px
  bold, `resets <countdown>` at `(66, 61)` 10 px.
- **7D bar**: `x=3, y=76`, width 244, height **11**.
- **The third row and the footer row stay white.** Deliberate, and verified as
  composed rather than unfinished. Not to be filled with a historic number: the
  Gauge frame is *now*, the Historic View is *then*.
- **No footer, no context readout.** The field stays on the wire; nothing draws
  it.

⚠ **Three of those numbers are corrections, and the bug they fix was invisible
in a preview**: the rule ran to `y=46` while the bar occupied `y=40–48`, so six
pixels of rule cut straight through it; the bars were 8 px and sat higher; and
the 7D row had 3 px between `26%` and `resets …`, reading as one string.
**A design signed off on mocks is not signed off.**

**Bar drawing** (both bars): a 1 px outline rectangle, and a fill from `x+1`
of `(w-2) × pct/100`, drawn only when non-zero.

**Countdown format**: `<1m` under a minute; **`RESETS NOW`** at and past zero,
which **drops the `RESETS IN` label** and draws at `(132, 13)` 15 px bold —
it is not resetting *in* anything any more, and the full string overran the
panel edge. Otherwise `%dh%02dm` or `%dm`.

### 5.4 The two Gauge faults

- **Null `used_percentage`** → **`NO USAGE DATA`** at `(3, 10)` 24 px bold,
  with **`waiting for first snapshot`** at `(3, 44)` **13 px**. **No split
  rule**: with no snapshot there is no countdown to divide the row for.
- **Expired Gauge (Age ≥ 300 s)** → **not drawn at all.** The panel falls back
  to the Historic View.

⚠ **Nothing on the panel is ever marked stale.** No dimming, hatching, banner
or inversion. All four were considered and rejected (ADR-0010): the panel has
no grey, and a marked-stale number is one you are asking a viewer not to trust
on a display read in about a second.

## 6. Where the data comes from

**The Pi queries its own `readings` table.** The wire is untouched by this
milestone — no new Payload shape, no new field, no new Ack semantics.

Measured on the Pi against a synthetic year of Readings (2,190 rows, 188 KiB):
**0.25 ms** for the rows, **4.66 ms** for the average. Bounded by the 300 s
floor to once per five minutes, so:

**Compute on demand at redraw. Do not cache.** A cache buys a rounding error
and costs an invalidation bug whose signature is "the panel shows a total the
table no longer agrees with".

- **Rows**: the 5 most recent dates present in `readings`, newest first, each
  `SUM(cost_usd)`.
- **Average**: the mean daily total across **every** Active Day the Pi holds —
  not the five shown. The footer must read as one sentence: *since 04 Sep,
  average $40*. Pairing a since-date with an average over a different window is
  quietly wrong.
- **Coverage Start**: `meta['coverage_start']`, verbatim.
- **Incomplete**: a day is `~` when **any** Reading that day has
  `cost_complete = 0` (`MIN(cost_complete) = 0`). One unpriced model makes the
  day's total a floor, not a fact.
- **Active Day**: a date with **any** Reading row — *not* `cost > 0`. An
  unpriced model yields real tokens at $0, and the Desktop already drops
  all-zero rows (pipeline §4.6), so a row existing means usage happened.

**No new index.** The primary key `(date, project, model)` leads with `date`,
so the group-by range-scans it.

⚠ **The Pi formats dates; it never computes them.** `2026-09-04` → `04 SEP` is
a lookup, not a clock read. ADR-0009 stands: nothing here asks what today is.
The distinction is one careless reading apart, so it is written here twice on
purpose.

⚠ **A partial Batch understates a day, and that is accepted.** If the Desktop
dies mid-Batch the Pi holds some of a day's rows and the total is low, with
nothing saying so. It self-heals on the next Batch, since unmarked Readings are
retried. **Do not** reuse `~` for it (that conflates "a model wasn't priced"
with "data is in flight" — different faults, pointing different ways) and **do
not** put an expected-row-count on the wire (Batch bookkeeping for a transient
the retry already fixes).

## 7. `pi/render.py` — the module

A separate module, imported by `receive.py`. Not code inside `receive.py`:
the GPIO-claiming import needs exactly one door, `receive.py`'s DB functions
must stay importable with neither bluezero nor a panel, and the frame builders
should be testable by rendering to a PNG with no hardware at all.

It owns three things:

**1. The frame builders** — pure functions from state to a 250×122 mode-`"1"`
`PIL.Image`. No I/O, no driver, no clock. This is what §12's tests exercise.

**2. The panel context manager** — the *only* way anything touches the driver:

```python
@contextmanager
def panel():
    epd = epd2in13_V4.EPD()
    try:
        if epd.init() != 0:
            raise RuntimeError("epd.init() failed")
        yield epd
    finally:
        try:
            epd.sleep()
        except Exception:
            ...  # never let cleanup mask the real error
```

⚠ **`init()` goes *inside* the guarded region.** `module_init()` raises the
power pin and opens SPI as its first act, so the panel is live from that
instant — `reset()`, three `ReadBusy()` spins and ~10 commands all run before
`init()` returns. A review already caught this exact mistake in the self-test
(`ca68517`). ADR-0007's every-cycle-ends-in-`sleep()` becomes **structural**
here rather than a rule each caller remembers.

**3. The worker** — see §8. The `epdconfig` import lives inside it, not at
module scope.

## 8. Threading, and what the Ack means

**The refresh runs on one worker thread, fed a one-slot hand-off** — newest
state wins, which is what the floor already says. Never inline in the write
handler, never on a post-Ack bluezero timer.

Measured, same six-row Batch against the dev Pi
([#56](https://github.com/peterderkoala/zeropi.display/issues/56), branch
`bench/render-blocking`):

| Variant | row 1 | rows 2–6 | batch wall |
|---|---|---|---|
| inline | 4.50 s | 0.13 s | 5.18 s |
| **worker thread** | **0.18 s** | 0.13 s | **0.86 s** |
| inline, panel holds BUSY | **write raised at 5.09 s** | raised | 10.17 s |

⚠ **The real budget is BlueZ's ~5 s write timeout, not our 10 s Ack timeout.**
The blocked receiver never tripped the Desktop's timeout — the *write* failed
first. A 4.35 s cycle inline leaves **~0.5 s of margin against a limit we do
not control**, and e-ink refresh time varies with temperature.

⚠ **The overrun failure is `GATT Protocol Error: Unlikely Error`** — verbatim
the signature `docs/e2e-verification.md` chased for a whole session before
finding the `bluetoothd` segfault. **If it reappears after this ships, suspect
the panel before the daemon.** It also fails dishonestly: the Pi has *already
persisted* the Reading by then, so the Desktop never marks it pushed and
resends forever while the Pi holds it. Both sides correct, data diverging.

Threading works here for a specific reason worth knowing: **every wait in the
driver is a `time.sleep`** (`ReadBusy` polls at 10 ms, `sleep()` is one 2 s
sleep), so the GIL is released for essentially the whole cycle. Rows 2–6 above
were served *while the worker was mid-refresh*.

**`drawn` in the Ack is redefined**: it means **"the gate accepted this for
drawing"**, not "pixels moved". Once the refresh outlives the Ack it is a
promise. Its only consumer is a human reading a log to see whether the floor
coalesced, and for that the gate's decision *is* the answer. **Say this in the
code comment too** — `drawn` reading as "pixels moved" is exactly what gets
assumed later.

**The link outlives the panel, always.** Receiving, persisting and Acking never
depend on the display:

- A render exception is caught and logged **once per transition**, not per
  attempt; the panel is marked unavailable and retried at the next redraw the
  gate allows.
- For a worker stuck in `ReadBusy` — unbounded, no timeout, pinned upstream so
  we do not patch it — a **watchdog stops feeding the worker after ~30 s** and
  the process keeps serving BLE with a dead display. A stuck thread is
  survivable; a stuck event loop is not.
- `receive.py` must stay importable and its DB functions usable with no panel,
  no SPI and no bluezero. The test suite depends on it.

## 9. Cadence: when the panel moves

Unchanged from the pipeline spec except where marked.

- **The floor is 300 s, enforced by the Pi, absolute.** A redraw inside it is
  coalesced, newest state wins, and the Ack says `drawn: false`. **The fallback
  may not pre-empt it** — considered and rejected: the panel's 180 s rating
  leaves headroom, but "a hard gate, except sometimes" is a far weaker
  invariant, and it would paper over a cadence bug rather than fix it.
- **Gauge expiry stays 300 s**; an expired Gauge is not drawn (ADR-0010).
- ⚠ **The Desktop's Gauge throttle drops from 300 s to 120 s** — a Desktop-side
  change this milestone owns, in `desktop/service.py` and pipeline §7.5.
  **Reason**: with throttle, expiry and floor all 300 s, the replacement Gauge
  arrives at a measured **310 s** (300 s throttle + ~7 s scan/connect +
  jitter), so the Gauge on screen is **expired for ~25 s before every
  replacement**, every cycle. A phase-unlucky tick then draws the Historic View
  and **the floor pins it there for a full 300 s while a live Gauge sits in
  memory**. At 120 s a replacement always lands well before expiry. Costs ~2.5×
  the BLE work and **zero extra panel wear** — the floor still gates every draw.
- **The invariant, which is the real deliverable of that change**: the Pi's
  `GAUGE_EXPIRY_S` must stay comfortably above the Desktop's push interval,
  roughly `2 × throttle`. The two constants live in different files on
  different machines; **each must name the other in a comment**.
- **The Pi redraws on its own monotonic clock too**, whichever comes first: the
  countdown moves every minute with no usage change.
- **Idle keep-alive: one full refresh every 24 h.**
- ⚠ **New: draw the Historic View once at startup.** Today nothing draws until
  a Payload arrives (`last_drawn_at = None` and `historic_pending = False`, and
  `try_draw_historic_if_due` needs one of them), so after a reboot the panel
  keeps whatever image it was holding — possibly a Gauge frame from before the
  power cut, arbitrarily stale, undetectable because `monotonic()` reset with
  it. That is ADR-0010's guarantee defeated through the back door. The Pi has
  everything it needs in SQLite, so the startup frame is honest the moment it
  is drawn.

  *A boot splash — a logo or figure — is a plausible future replacement for
  this frame. Keep it a one-line change: the startup draw should name a frame
  builder, not inline one.*

## 10. Provisioning

`pi/install-pi.sh` owns all Pi state. **Do not hand-apply anything.**

- Add **`fonts-dejavu-core`** to the apt block (§3).
- Deploy `pi/render.py` alongside `receive.py`.
- ⚠ **Say who owns the panel.** `pi/epd-selftest.py` is the documented bench
  check and claims GPIO on import; once `receive.py` owns the panel, running
  the self-test against a live service is a collision. Decide and document one
  of: the self-test refuses to run while `zeropi-display` is active, or its
  documentation requires stopping the service first. **Do not leave this to
  whoever next runs it.**
- ⚠ **`Environment=PYTHONUNBUFFERED=1` is already in the unit and must stay.**
  Without it none of this milestone's logging reaches the journal at all —
  `print()` under systemd is block-buffered into a socket, and a full Batch
  produced zero lines before it was found (#48).

## 11. What this supersedes

Three clauses of `spec-usage-pipeline.md` are **no longer current**:

1. **§9.3's "if gaps should be visible, the Desktop sends explicit zero rows"**
   — **retired.** The Historic View lists Active Days, so gaps are never drawn
   and there is nothing to zero-fill.
2. **§8.6's "rendering is a stub"** — that is what this milestone replaces.
3. **§7.5's 300 s Gauge throttle** — now 120 s (§9).

And one question parked elsewhere is **closed**: #13 left *"does the Pi derive
a Project Label"* open for this map. **It does not.** The trend is per-day
totals across every project; neither the design nor the data path needs a
Label.

## 12. Testing

The suite is 181 tests and must stay green with **no panel, no SPI, no
bluezero, no `~/.claude`**.

- **Frame builders are pure**: render to an in-memory image and assert on it.
  Cheap, real assertions: the image is exactly 250×122 mode `"1"`; a five-row
  Historic View has ink in all five row bands; a four-row one has none in the
  fifth; `~` appears iff `cost_complete = 0`; the bar for the peak day is
  wider than the others; `RESETS NOW` draws no `RESETS IN` label.
- **The framebuffer is exactly 4000 bytes.** A different number means the frame
  is not what the panel expects, and it is a one-line test.
- **The data layer** is already testable against a scratch SQLite file: assert
  the five-row selection, the average over *all* Active Days, and the
  `MIN(cost_complete)` rollup — including the case where the Pi holds fewer
  than five Active Days.
- **The worker and watchdog** take injectable clocks and a fake panel, the way
  `desktop/service.py`'s loop already does. Assert: a redraw arriving
  mid-refresh replaces the pending one rather than queueing; a render exception
  leaves BLE serving and marks the panel unavailable; a refresh that never
  returns is abandoned by the watchdog without killing the process.
- **What tests cannot tell you** is in §4 and §5: contrast, dithering,
  legibility at arm's length. Those are a human at the bench, and this
  milestone is not done without one.

## 13. Out of scope

- **Weather, calendar and the One-liner** — **dropped from the project**
  entirely on 2026-09-09, not deferred.
- **A boot splash / logo** — noted as a future seam in §9, not built here.
- **Partial refresh or any two-speed scheme** — ADR-0007, not reopened: deep
  sleep does not retain the partial-refresh base image.
- **Marking anything stale** — ADR-0010.
- **Whether this HAT wires `PWR_PIN` on BCM 18** — open since #23, ruled out of
  the rendering map by the maintainer. Bench work with a multimeter; blocks no
  frame from drawing.
- **Reading history back over BLE**, any new Payload shape, and any change to
  the Ack beyond `drawn`'s redefinition.
- **Power, UPS and enclosure hardware.**

## 14. What this spec decided that no ticket had

The gap check. Each of these would otherwise have been invented by whoever
implemented it.

1. **The font is `fonts-dejavu-core` from apt.** #54 gathered facts and
   deliberately took no decision; §3 takes it, on the ground that DejaVu is the
   only typeface whose rendering has been seen on this panel.
2. **The 13 px floor is not a blanket rule.** Short upper-case labels stay at
   10 px, verified on glass. Without this the Gauge frame's whole layout would
   have to be rebuilt to satisfy a rule that was never tested against it.
3. **`resets 135h52m` stays at 10 px** despite being lowercase and multi-word —
   it was read on glass without objection, so it is kept as *verified* rather
   than as the rule predicts, and flagged in §4 as the first thing to re-check.
4. **The startup draw** (§9). Nobody had noticed the panel keeps its
   pre-reboot image indefinitely; the gap was found reading the gate's
   conditions while writing this document.
5. **Minimum bar width of 1 px** in the Historic View, so a real but tiny day
   is never invisible — the difference between "$1" and "no usage" must survive
   the scaling.
6. **The average is over all Active Days held, not the five drawn** (§6), so
   the footer reads as one sentence with its own since-date.
7. **Panel ownership vs the self-test** (§10) — an operational collision that
   only exists once `receive.py` claims the panel.

**One thing deliberately left open**: the frames verified on glass were
rasterised **on the Desktop** and displayed as prepared bitmaps, because the Pi
had no fonts to rasterise with. Pillow is 11.1.0 on the Pi and differs on the
Desktop. **The implementation must re-confirm the 13 px floor once text is
rasterised on the Pi itself** — one glance at `waiting for first snapshot`
settles it.
