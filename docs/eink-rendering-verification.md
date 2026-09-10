# E-ink rendering verification (2026-09-10)

Hardware verification of `docs/spec-eink-rendering.md` — ticket
[#66](https://github.com/peterderkoala/zeropi.display/issues/66), the last
child of [map #59](https://github.com/peterderkoala/zeropi.display/issues/59).
Every observation below is either a line from the Pi's own journal or the
maintainer looking at the panel. Nothing here is a unit test or a fixture.

**Result: the panel draws. All six scenarios passed on first attempt**, with
clean contrast and no ghosting after the run, and **no defect was found in
`render.py` or `receive.py`.** The run settled §14's one deliberately-open
question — the 13 px floor — and produced three follow-ups: a test spec §12 had
named and the suite did not have (added here, §8), and two omissions in the
spec's own prose (§4's `sh` pair, §0; and §5.4's `5H` label, §9).

## Environment

| | |
|---|---|
| Pi | `192.168.4.108`, Debian 13 (trixie), Python 3.13.5, aarch64, BlueZ `5.82-1.1+rpt2`, Pillow 11.1.0 |
| Pi software | `/opt/zeropi-display/` at `556e201`, provisioned by the documented curl one-liner; `receive.py`, `render.py` and `epd-selftest.py` all md5-identical to the repo |
| Panel | Waveshare 2.13" V4, 250×122 landscape, 1-bit |
| Fonts | `fonts-dejavu-core`, installed by `install-pi.sh` (#64) — `/usr/share/fonts/truetype/dejavu/` present |
| Desktop | Pop!_OS 24.04, Python 3.12.3, `bleak` 3.0.2 in the repo's `.venv` |
| Pi data | 4 Active Days (04, 05, 06, 09 SEP), `coverage_start = 2026-09-04`, all Cost Complete |
| Suite | `242 passed` before the run, `243 passed` after (one test added, §7 below) |

The dev Pi is shared; it was checked for a live collision first (`w` showed
one session, this one) and its `data.db` was backed up before anything ran.

## 0. The 13 px floor, rasterised on the Pi (spec §14)

⚠ **This was the run's first job**, and the one thing spec §14 left open: every
frame approved on glass in #57 was rasterised on the *Desktop* and pushed as a
prepared bitmap, because the Pi had no fonts. Pillow differs between the two
machines. If the floor were wrong for the Pi, the spec would need amending
rather than working around.

`waiting for first snapshot` was rendered on the Pi, in the Pi's venv, directly
in mode `"1"` (never greyscale-then-convert, §4). The `st` and `sh` pairs were
rendered in isolation and their vertical ink runs counted — two runs means the
letters separate, one means they touch:

```
Pillow 11.1.0
10px  st: 1 run COLLIDES [########]      sh: 2 runs SEPARATED [####.#####]
11px  st: 1 run COLLIDES [#########]     sh: 2 runs SEPARATED [#####.#####]
12px  st: 1 run COLLIDES [#########]     sh: 2 runs SEPARATED [#####.######]
13px  st: 2 runs SEPARATED [#####.####]  sh: 2 runs SEPARATED [#####..######]
14px  st: 1 run COLLIDES [###########]   sh: 2 runs SEPARATED [######.#######]
```

**The half of the claim that decides the floor reproduces on the Pi**: `st`
collides at 11 px and separates at 13 px. §4's floor stands as written.

⚠ **The other half did not, and the difference is the instrument.** §4 says
11 px "collides on the `st` **and** `sh` pairs"; here `sh` measures as
SEPARATED at every size tested, by a single blank column at 11 px. That is not
a contradiction of #57 so much as a different measurement: §4's claim is about
**real glass**, this one is about **the raster**, and a 1 px gap at 11 px is
exactly the kind that closes to the eye once e-ink has spread the ink. The
conclusion is unchanged either way, because `st` — which genuinely touches — is
what sets the floor. Worth recording so nobody later reads the table as
evidence that `sh` is safe at 11 px.

⚠ **The floor is not monotone, and that is worth knowing.** `st` collides again
at 14 px. Separation is a property of how FreeType's `FT_LOAD_TARGET_MONO` path
lands a specific glyph pair on a specific pixel grid, not of size increasing —
so "bigger is safer" is false here. Anyone changing a text size on this panel
must re-check the pair, not reason about it.

## 1. The Historic View at rest (spec §5.1)

The panel was left untouched overnight, holding the frame drawn at 17:59 the
previous evening — **14 hours** of idle, well past any single refresh.

Rendered from the Pi's own DB for reference, and confirmed on the glass:

- four rows, newest first: `09 SEP $30`, `06 SEP $73`, `05 SEP $60`, `04 SEP $34`
- **the fifth row band white** — the Pi holds only 4 Active Days, and §5.1's
  "fewer than five draws fewer rows" reads as intentional on the panel, not broken
- the peak bar (06 SEP) runs to the right edge; the others scale against it
- rule at `y=101`, footer `SINCE 04 SEP` … `AVG $49`

`AVG $49` is the mean over **all four** Active Days the Pi holds
(196.505 / 4 = 49.13), not over some other window — §6's one-sentence footer.

**Maintainer's read: matches, legible at arm's length, contrast fine, no
objectionable ghosting after 14 h.**

## 2. A real Gauge push (spec §5.3)

```
08:45:07  Connected to 70:A8:D3:3B:EC:8B
08:45:09  render: {'five_hour': {'pct': 5, 'resets_in_s': 15296},
                   'seven_day': {'pct': 68, 'resets_in_s': 108896}, ...
                   'gauge_age_s': 6.0}
08:45:12  Disconnected
```

Real data, from the maintainer's live Claude Code logs via `desktop/push.py
--gauge-only`. MTU negotiated to 517. The panel drew the Gauge frame: `5H` /
`5%` at 28 px, split rule, `RESETS IN` / `4h14m` at 24 px, both 11 px bars,
`7D 68%  resets 30h14m`, third row and footer white.

**All three of #57's glass corrections held on real hardware**: the split rule
stops at `y=36` and does not cut the `y=42` bar; the bars are 11 px, not 8; and
the 7D row's `68%` and `resets …` read as two things, not one string.

**Maintainer's read: correct and legible.**

## 3. The redraw floor coalesces (spec §9)

A second `--gauge-only` push, 40 s after the first — deep inside the 300 s
floor:

```
08:45:49  Connected to 70:A8:D3:3B:EC:8B
08:45:54  Disconnected
```

**No `render:` line.** The write was accepted and Acked normally (`Gauge push
ok.` on the Desktop), the Reading state was updated, and the panel did not
move. The floor is a hard gate on *the panel*, never on the link.

**Maintainer's read: the panel stayed put.**

## 4. The expiry fallback (spec §5.4, ADR-0010)

Pushing stopped. At the next minute tick past expiry:

```
08:50:35  render: {'historic': True}
```

The panel flipped back to the Historic View, **with nothing marking it stale** —
no dimming, hatching, banner or inversion, exactly as ADR-0010 requires.

⚠ **One thing here looks wrong at a glance and is not.** The last Gauge *push*
landed at 08:45:51, so a 300 s bound on the push would not expire until
08:50:51 — yet the fallback fired at 08:50:35. That is correct:
`gauge_age_s = snapshot_age_s + (now - arrival_mark)` bounds **the snapshot's**
age, not the push's. The 08:45:09 draw logged `gauge_age_s: 6.0`, putting the
snapshot at **~08:45:03**; nothing rewrote it (the snapshot only advances while
an interactive TUI is open), so at the 08:50:35 minute tick it was **332 s** old
and the Pi rightly refused to draw it. The second push does not change that
arithmetic — it carried the same snapshot, so its larger `snapshot_age_s` and
later `arrival_mark` cancel to the same 332 s. **A re-push of an unchanged
snapshot does not buy freshness**, and it should not — that is the whole point
of ADR-0010.

This is also the clearest possible argument for #65's throttle drop: at a 300 s
Desktop throttle, replacements arrive at a measured ~310 s and every cycle spends
~25 s in exactly the state above.

## 5. The startup draw after a restart (spec §9, gap check §14.4)

Set up so the gap would be visible if it were still open — get a Gauge frame on
the glass first, *then* restart:

```
08:55:55  render: {'five_hour': {'pct': 8, 'resets_in_s': 14652}, ...}   # Gauge frame on the panel
08:56:12  Stopping / Started zeropi-display.service
08:56:13  render: {'historic': True}                                     # startup draw
08:56:13  Advertisement registered
```

**The panel flipped from the Gauge frame to the Historic View one second after
the restart.** Without §9's startup draw it would have kept the Gauge frame
indefinitely — arbitrarily stale and undetectable, because `monotonic()` resets
with the process. §14.4's gap is closed on real hardware.

The startup draw also runs *before* `Advertisement registered`, so the panel is
honest before the link is even up.

**Maintainer's read: flipped to Historic.**

## 6. The empty frame on a wiped Pi (spec §5.2)

`data.db` moved aside, service restarted:

```
08:58:29  Started zeropi-display.service
08:58:30  render: {'historic': True}
08:58:30  Advertisement registered
```

`receive.py` created a fresh DB, found zero Readings, and the panel drew
**`NO HISTORY YET`** at 20 px with **`nothing pushed to this Pi`** at 13 px
beneath it — §5.2's frame, and the 13 px line legible at arm's length, which
is §0's floor arriving on the glass rather than in a column count.

The real `data.db` was then restored and the service restarted; the Pi is back
on its four Active Days with `coverage_start` and `desktop_id` intact
(09:00:21, `render: {'historic': True}`).

**Maintainer's read: `NO HISTORY YET` as specified.**

## 7. Ghosting and contrast after a run of refreshes

#66 asks for the reads only a human can give: "contrast, ghosting after a run
of refreshes, and legibility at arm's length". §1's read was after **14 h of
idle**, which is the opposite of a run of refreshes, so it does not answer this.

By the end of §6 the panel had taken **6 full refreshes in about 15 minutes**,
alternating frames that share almost no ink — Historic → Gauge → Historic →
Gauge → Historic → empty → Historic. That is the pattern most likely to leave
residue: ADR-0007 makes every one of them a full refresh, and the large black
bars of the Gauge frame sit where the Historic View is mostly white.

**Maintainer's read, with the panel resting on the Historic View: clean — no
ghosting, no residue of the Gauge or empty frames, contrast as good as the
first draw, legible at arm's length.**

⚠ **This says nothing about the long run.** Six refreshes in a quarter of an
hour is a burst, not a life; the refresh budget over the panel's lifetime and
any slow contrast drift are still open from map #59 and still want calendar
time.

## 8. What the run changed

**A missing test, which spec §12 had named.** §12 asks for "the framebuffer is
exactly 4000 bytes … a one-line test"; the suite did not have one. It is now
`test_every_frame_packs_to_exactly_4000_bytes` in `tests/test_render_frames.py`,
covering all four frames.

It cannot call the driver's `getbuffer()` — importing `epdconfig` claims GPIO,
and the suite must run with no panel and no SPI — so it replicates that
method's exact path instead: a 250×122 landscape image takes
`epd2in13_V4.getbuffer`'s `imwidth == self.height` branch, which rotates to the
panel's native 122×250 and packs rows to whole bytes. 122 is not a multiple of
8, so PIL pads each row to 16 bytes: 16 × 250 = **4000**.

⚠ **`img.tobytes()` on the un-rotated 250×122 frame is 3904 bytes, not 4000**,
because 250 bits pad to 32 bytes × 122. That number is *not* wrong, and it is
not the panel's framebuffer either — it is a different measurement of the same
image. Anyone checking §2's figure against the landscape image directly will
get 3904 and think something is broken.

## 9. One gap in the spec's prose, not in the code

**§5.4 omits the `5H` label that the fault frame draws.** It specifies
`NO USAGE DATA` at `(3, 10)` and `waiting for first snapshot` at `(3, 44)`, and
says "no split rule" — but the frame also draws `5H` at `(3, 1)` in 10 px bold.

**The implementation is right and the spec text is incomplete**, not the other
way round: the reference implementation approved on glass in #57
(`render_no_data` in `desktop/gauge_settled.py` on `prototype/gauge-glass-fix`)
draws that label, and `pi/render.py` reproduces it exactly. Raised per map #59's
escape hatch rather than quietly reconciled. It is cosmetic and blocks nothing;
whether §5.4 gains the line is the maintainer's call.

## What this does *not* verify

- **The `NO USAGE DATA` fault frame on glass.** It needs a null
  `used_percentage`, which real data would not produce on demand, and #66 did
  not list it. Its harder half — the 13 px `waiting for first snapshot` — was
  verified twice over: rasterised on the Pi in §0 and read on the panel in §6
  as `nothing pushed to this Pi`, the same size and the same kind of string.
- **The watchdog and the panel-unavailable path.** Unit-tested with a fake
  panel (#61); never provoked on real hardware, which would mean holding BUSY
  deliberately.
- **The 24 h idle keep-alive.** Wants a day of uptime, not a bench session.
- **The refresh budget over the panel's life, and long-run ghosting and
  contrast drift.** Still open from map #59, and still needing calendar time
  rather than a decision. §7 read the panel clean after a **burst** of 6
  refreshes in 15 minutes, which is not the same claim — and that count is
  dominated by three deliberate service restarts, so it says nothing about a
  normal day either.

## Reproducing this

```bash
# Pi (needs a tty for sudo)
ssh pi@192.168.4.108
journalctl -u zeropi-display -f

# Desktop
.venv/bin/python desktop/push.py --gauge-only   # scenarios 2 and 3
.venv/bin/python -m pytest                      # 243 passed
```

The floor check of §0 runs on the Pi, in the Pi's venv, against
`/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf` — that it runs *there* is the
entire point of it.
