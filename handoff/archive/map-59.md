# Map #59 — Make the panel draw (implement `docs/spec-eink-rendering.md`)

**Charted 2026-09-09, closed 2026-09-10. Destination reached: the panel draws, hardware-verified.** Issue: https://github.com/peterderkoala/zeropi.display/issues/59

> Archived from `handoff/handoff.md` on 2026-09-10. This is a **record of a
> finished effort**, not live guidance: facts here were true when written and
> some have since been superseded. The live handoff, the specs and the ADRs are
> authoritative. Kept because the *reasoning* behind decisions — and the bugs
> found on the way — is not recoverable from the code.

---

## How it went

**All seven tickets landed, and the map's execution-mode bet paid off.** The
spec's §14 gap check had closed every judgment call before a line was written,
so no ticket needed grilling and no implementer invented an answer. #66 then
passed **all six bench scenarios on first attempt** with **no defect found in
`render.py` or `receive.py`** — for a milestone that touches GPIO, a worker
thread and a BLE event loop, that is the gap check earning its keep.

| Ticket | Commit |
|---|---|
| #60 frame builders · #61 worker + failure handling · #62 data layer | `e70d11b`, `fc19938`, `9adec7f` |
| #65 Desktop throttle → 120 s | `f557fba` |
| #63 wiring into `receive.py` | `1078ca4` |
| #64 provisioning (`render.py`, the font, panel ownership) | `42e24d0`, `556e201` |
| #66 hardware verification | `cd427be`, `40389c7` |

**The escape hatch was used twice, both cosmetically.** The map said a gap the
spec did not anticipate is *a bug in the spec*, to be raised on the ticket
rather than quietly invented around. #66 found two, both in the spec's prose
rather than its decisions, and the code was right both times:

1. **§5.4 omits the `5H` label** the fault frame draws — the reference approved
   on glass in #57 draws it, and `pi/render.py` reproduces it.
2. **§4's `sh` claim did not reproduce.** §4 says 11 px collides on the `st`
   *and* `sh` pairs; on the Pi's raster `sh` separates at every size. The
   instruments differ rather than the facts — §4 measured glass, #66 measured
   pixels — and the floor is unaffected because `st` sets it.

**§14's one deliberately-open question is settled**: the 13 px floor holds with
text rasterised on the Pi itself. ⚠ **It is also not monotone** — `st` collides
again at 14 px. Nobody had reason to suspect that, and it matters to anyone who
later changes a text size.

Two things the map listed as "Not yet specified" ended exactly where it
predicted: the **refresh budget over the panel's life** and **long-run ghosting
and contrast drift** became *countable* only once a panel actually ran, and
both want days of uptime rather than a decision. #66 read the panel clean after
a burst of 6 refreshes in 15 minutes, which is a far weaker claim than the long
run.

Full verification run: `docs/eink-rendering-verification.md`.

---

## The handoff's live guidance while the map ran, verbatim

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


