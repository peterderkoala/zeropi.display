# Map #51 — What the e-ink panel draws, and how (spec)

**Charted and closed 2026-09-09. Destination reached: `docs/spec-eink-rendering.md`.** Issue: https://github.com/peterderkoala/zeropi.display/issues/51

> Archived from `handoff/handoff.md` on 2026-09-09, verbatim. This is a
> **record of a finished effort**, not live guidance: facts here were true when
> written and some have since been superseded. The live handoff, the specs and
> the ADRs are authoritative. Kept because the *reasoning* behind decisions —
> and the bugs found on the way — is not recoverable from the code.

---

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
