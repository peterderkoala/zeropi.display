# Research: Waveshare 2.13inch e-Paper HAT v4 refresh characteristics

Resolves [#23](https://github.com/peterderkoala/zeropi.display/issues/23) (child of
[map #13](https://github.com/peterderkoala/zeropi.display/issues/13)).

Researched 2026-09-05. Primary sources, all first-party Waveshare:

- **[`2.13inch_e-Paper_V4_Specification.pdf`](https://files.waveshare.com/upload/4/4e/2.13inch_e-Paper_V4_Specification.pdf)**
  — the V4 panel datasheet. This is the only source in this document that is
  unambiguously V4-specific, and where it disagrees with anything else it wins.
- **[2.13inch e-Paper HAT Manual](https://www.waveshare.com/wiki/2.13inch_e-Paper_HAT_Manual)**
  (Waveshare wiki) — one page covering V2, V3 **and** V4 of this product. Its
  "Parameters" table is not version-scoped; treated as advisory, not V4-authoritative.
- **[`epd2in13_V4.py`](https://github.com/waveshareteam/e-Paper/blob/master/RaspberryPi_JetsonNano/python/lib/waveshare_epd/epd2in13_V4.py)**,
  **[`epd_2in13_V4_test.py`](https://github.com/waveshareteam/e-Paper/blob/master/RaspberryPi_JetsonNano/python/examples/epd_2in13_V4_test.py)**,
  **[`epdconfig.py`](https://github.com/waveshareteam/e-Paper/blob/master/RaspberryPi_JetsonNano/python/lib/waveshare_epd/epdconfig.py)**,
  **[`EPD_2in13_V4.c`](https://github.com/waveshareteam/e-Paper/blob/master/RaspberryPi_JetsonNano/c/lib/e-Paper/EPD_2in13_V4.c)**
  — `waveshareteam/e-Paper` @ `master`, read 2026-09-05.

**Revision discipline**: everything below is V4 unless explicitly labelled
otherwise. The V4 spec PDF, the `_V4` driver files and the V4 demo are all
revision-scoped. The wiki *page* is shared across V2/V3/V4 and the wiki's own
general "Question about Screen" FAQ is shared across Waveshare's entire e-paper
line — those two are called out inline every time they are used.

---

## Headline

| | |
|---|---|
| **Minimum safe update interval** | **180 seconds per panel update**, counting partial updates the same as full ones. |
| **Two-speed scheme (fast partial + periodic full)** | **Supported.** It is what Waveshare's own V4 demo does. Constraint: **at most 5 consecutive partial/fast updates before a full refresh**. |

A 5-minute gauge is comfortably inside budget and is **not** destructive. A
gauge faster than 3 minutes is outside the vendor's stated envelope, and the
vendor names no replacement number for it (see §6).

---

## 1. Panel facts (confirmed)

From the **V4 specification**, §1 Overview, §2 Features and §3 Mechanical
Specifications:

| Property | Value | Source |
|---|---|---|
| Resolution | **250 × 122** ("122(H)×250(V) Pixel", Dpi 130) | V4 spec §3 |
| Colour depth | **1-bit black/white**, 2 grey levels | V4 spec §1 ("capable to display images at 1-bit white, black"), §8 (`GN 2Grey Level`) |
| Controller | **SSD1680Z8** | V4 spec §4 mechanical drawing |
| Active area | 23.7046 × 48.55 mm, 0.194 mm pitch | V4 spec §3 |
| Interface | SPI mode 0 (CPOL=0, CPHA=0), 4-wire | V4 spec §5, wiki § Communication Method |
| Operating temp | 0 – 50 °C | V4 spec §6.1 |
| **Rated life** | **1,000,000 updates or 5 years** | V4 spec §8, `Life / Topr` row |

The driver source agrees exactly: `EPD_WIDTH = 122`, `EPD_HEIGHT = 250`, and
`getbuffer()` does `img.convert('1')` into `width/8 × height` bytes — so the
frame buffer is **4000 bytes** (122 rounds to 128 bits = 16 bytes per line ×
250 lines) and there is no greyscale path at all.

The **map's belief of "250x122 monochrome" is confirmed.**

> The 122 axis is not a multiple of 8. `SetWindow()` in the driver notes
> "x point must be the multiple of 8 or the last 3 bits will be ignored" —
> relevant only if anyone later attempts a windowed partial update (§4).

## 2. Refresh timings — the numbers that matter

From the **V4 specification §6.2 Panel DC Characteristics** (VSS=0V, VCI=3.0V,
TOPR=25 °C). This table is rendered as an image in the PDF; the figures below
were read off page 9 directly.

| Parameter | Conditions | Typ. | Units |
|---|---|---|---|
| **Full update time** | 25 °C | **3** | sec |
| **Fast update time** | 25 °C | **1.5** | sec |
| **Partial update time** | 25 °C | **0.42** | sec |
| Typical power (P_TYP) | VCI=3.0V | 10.5 | mW |
| Typical operating current | VCI=3.0V | 3.5 | mA |
| Deep sleep power | VCI=3.0V | 0.003 | mW |
| Deep sleep current | DC/DC off, RAM not retained | 1 (max 5) | µA |
| Sleep mode current | DC/DC off, RAM retained | 20 | µA |

§8 Optical Specifications restates the same headline as `T update / Image
update time / at 25 °C / Typ. 3 / sec`.

**Flicker behaviour**, verbatim from V4 spec §6.2 note 2:

> Full refresh: The screen will flicker several times during the refresh process;
> Fast Refresh: The screen will flash once during the refresh process;
> Partial refresh: The screen does not flicker during the refresh process.

So: **full refresh flashes/inverts, partial refresh does not.** The wiki adds
that the flicker "is to remove the afterimage to achieve the best display
effect" — i.e. the flashing *is* the ghosting-clearing mechanism, not a side
effect of it.

> ⚠ **Wiki/spec discrepancy.** The wiki "Parameters" table gives `Refresh time:
> 2s`. The V4 spec gives 3 s. The wiki table is not version-scoped (the same
> page covers V2/V3/V4) and the wiki itself hedges it — "The refresh time is
> the experimental results, the actual refresh time will have errors". **Use
> 3 s.** The 1.5 s fast and 0.42 s partial figures appear only in the V4 spec;
> the wiki has no equivalent rows.

The refresh is temperature-compensated (the driver reads the SSD1680's built-in
temperature sensor via command `0x18` and loads the waveform LUT from OTP), so
the 25 °C figures degrade in the cold. The wiki warns that "Refresh in a low
temperature environment may appear color cast".

## 3. Partial refresh: supported, with a hard N

**V4 supports partial refresh.** Three distinct update modes ship in the V4
driver:

| Driver call | Mode | Register write |
|---|---|---|
| `display(image)` → `TurnOnDisplay()` | full | `0x22` ← `0xf7` |
| `display_fast(image)` → `TurnOnDisplay_Fast()` | fast | `0x22` ← `0xC7` |
| `displayPartial(image)` → `TurnOnDisplayPart()` | partial | `0x22` ← `0xff` |
| `displayPartBaseImage(image)` | writes both RAM banks (`0x24` **and** `0x26`) then full-refreshes | — |

`displayPartBaseImage()` is the entry point to partial mode: it loads the same
frame into the "OLD" (`0x26`) and "NEW" (`0x24`) RAM banks so subsequent
`displayPartial()` calls have a reference to diff against.

**The number N — how many partials before a full refresh is required:**

> During the fast refresh or partial refresh of the electronic paper, it is
> recommended to add a full-screen refresh after **5 consecutive operations**
> to reduce the accumulation of afterimages on the screen.
>
> — **V4 specification §6.2, note 2** (highlighted in blue in the PDF)

**N = 5.** This is the one number in this document that is stated by the V4
datasheet itself rather than by shared documentation. Note that it applies to
**fast refresh as well as partial refresh** — `display_fast()` accrues ghosting
against the same budget.

Two other Waveshare statements agree in spirit but are less specific:

- Wiki § Precautions (shared across V2/V3/V4): "you cannot refresh them with
  the partial refresh mode all the time. After refreshing partially several
  times, you need to fully refresh EPD once. Otherwise, the display effect will
  be abnormal, **which cannot be repaired**!"
- Wiki FAQ, "After multiple positions are refreshed partially, the font is
  lighter after refreshed several times?" (general e-paper FAQ, not V4-scoped):
  "the customer needs to reduce the position of the partial refresh and clear
  the screen after **5 rounds** of partial refresh".

> ⚠ **Waveshare's own V4 demo violates Waveshare's own V4 spec.**
> `epd_2in13_V4_test.py` runs `displayPartBaseImage()` then a loop of
> **10** `displayPartial()` calls before the full `init()` + `Clear()`.
> The spec says 5. Do not take the demo as licence — **follow the spec's 5.**

**Switching back out of partial mode requires a re-init.** Wiki FAQ: "Why can't
the image be displayed when full refresh after partial refresh? — The full
refresh initialization function needs to be added when the e-Paper screen is
switched from partial refresh to full refresh." The V4 demo does exactly this:
`epd.init()` before `epd.Clear(0xFF)`. A two-speed implementation must call
`init()` on every mode switch, not just at startup.

## 4. Partial-refresh addressing: NOT sub-region, in practice

This one contradicts the intuitive reading of "partial refresh", and it changes
what a two-speed layout can assume.

The SSD1680 *can* address a window — the driver exposes
`SetWindow(x_start, y_start, x_end, y_end)` (registers `0x44`/`0x45`) and
`SetCursor` (`0x4E`/`0x4F`). But **neither the Python nor the C V4 driver ever
uses it for a sub-region.** Both `displayPartial()` (Python) and
`EPD_2in13_V4_Display_Partial()` (C) hard-code the full panel:

```python
self.SetWindow(0, 0, self.width - 1, self.height - 1)
self.SetCursor(0, 0)
self.send_command(0x24)      # WRITE_RAM
self.send_data2(image)       # a complete 4000-byte frame
self.TurnOnDisplayPart()
```

So the shipped API for V4 is: **always transfer a whole frame; the partial
*waveform* moves only the pixels that differ from the base image.** The
official demo relies on this — it redraws a small clock rectangle inside a
full-size buffer and the rest of the panel simply doesn't move.

**Consequence for a two-speed display: it works, but "region" is a property of
what you draw, not of what you transmit.** A live gauge and a slow history can
share one frame buffer; only the gauge's pixels physically flip. There is no
extra cost saving from a smaller region, and there is no vendor-supported way
to ask for one. Writing a windowed partial update would mean deviating from the
shipped driver (and honouring the 8-pixel x-alignment rule) — **untested, and
outside what Waveshare documents for V4.**

## 5. Ghosting, burn-in and holding a static image

Distinct failure modes, and the vendor is much less precise here than on
timings. Sources are noted per claim because most are **shared** e-paper
guidance, not V4-specific.

**Ghosting from repeated partial updates** (V4 spec §6.2 — V4-specific):
afterimages accumulate; a full-screen refresh after 5 operations clears them.
The wiki adds the escalation (shared, § Precautions): left unchecked "the
residual image problem will become more and more serious, or even damage the
screen", and "the display effect will be abnormal, which cannot be repaired".

**Contrast decay from repeatedly updating one region** (shared FAQ): "After
multiple positions are refreshed partially, the font is lighter after refreshed
several times" — the vendor's answer is to reduce the number of partial-refresh
positions and clear every 5 rounds. Raising VCOM restores contrast "but it will
increase the afterimage". This is the closest the vendor comes to addressing
the ticket's "repeatedly updating one region" question, and it is a **contrast**
warning, not a burn-in warning.

**Holding a static image** (shared § Precautions): "refresh at least once every
24 hours". The wiki's general screen FAQ restates this **scoped to multi-colour
panels** — "During the use of the **multi-color** e-paper screen, it is
recommended that customers update the display screen at least once every 24
hours. (If the screen keeps the same picture for a long time, the screen will
burn and it is difficult to repair.)" Our panel is black/white, so the FAQ's
scoping arguably exempts it, but the unscoped Precautions section does not.
**The vendor contradicts itself; the safe reading is to full-refresh at least
daily.** Nothing V4-specific exists on this point.

**The real burn-in risk is not the image, it is leaving the panel powered.**
This is stated repeatedly and unambiguously (wiki § Precautions, and again in
the FAQ "After using for a period of time, the screen refresh (full refresh)
has a serious afterimage problem that cannot be repaired?"):

> Note that the screen cannot be powered on for a long time. When the screen is
> not refreshed, please set the screen to sleep mode or power off it.
> Otherwise, the screen will remain in a high voltage state for a long time,
> which will damage the e-Paper and cannot be repaired!

The driver's `sleep()` sends command `0x10` with `0x01` (deep sleep), waits
2000 ms, then calls `module_exit()`. **Every update cycle must end in
`epd.sleep()`**, and the wiki notes the wake cost: "After the screen enters
sleep mode, the sent image data will be ignored, and it can be refreshed
normally only after initializing again", and "when the EPD wakes up, the screen
must be cleared first, to avoid the afterimage phenomenon".

> ⚠ That wake-up rule has teeth for a two-speed scheme: deep sleep destroys the
> partial-refresh base image (`Ram data not retain` in the DC characteristics
> table). **Sleeping between partial updates forces a full refresh on wake**,
> which collapses the two-speed scheme back to full refreshes. Either stay
> awake across a partial burst and sleep at the end of it, or accept a full
> refresh per wake. This tension is not addressed anywhere in Waveshare's
> documentation — it is an inference from two vendor statements that are each
> individually explicit.

## 6. Refresh budget and minimum interval

**The cycle budget is stated.** V4 spec §8: `Life ... 1000000 times or 5 years`.
The wiki FAQ agrees ("What is the refresh rate/lifetime of the e-paper screen?
— Ideally, with normal use, it can be refreshed 1,000,000 times (1 million
times)").

**The minimum interval is stated, but only in shared documentation, and with a
contradiction.**

- Wiki § Precautions (shared V2/V3/V4, unqualified): "it is recommended that
  the refresh interval is **at least 180s**, and refresh at least once every 24
  hours."
- Wiki § FAQ / Question about Screen (shared across all Waveshare e-paper):
  "it is recommended that customers set the refresh interval of the e-paper
  screen to at least 180 seconds **(except for products that support the local
  brush function)**."

"Local brush" is Waveshare's rendering of 局部刷新 — partial refresh. **Our
panel supports partial refresh, so the exception applies to it — and the vendor
names no replacement number.** The V4 specification itself is silent on any
minimum interval. This is the single biggest gap in the vendor's guidance and
it is exactly the number the map wanted.

**Where 180 s comes from.** It is not arbitrary, and the arithmetic makes it
defensible even though the vendor never explains it:

```
1,000,000 updates ÷ 5 years
  = 1,000,000 ÷ 157,788,000 s
  = one update every 157.8 s
```

The two halves of the `Life` row — "1000000 times **or** 5 years" — are
consistent with each other at roughly one update per 158 seconds. **180 s is
the cycle budget spread across the rated service life**, with a little margin:
at 180 s the panel makes 876,600 updates in 5 years, just under the million.

That gives a rate/lifetime table for the cadence ticket. Cycle-limited life
assuming the panel is updated continuously at that interval:

| Interval | Updates in 5 years | Cycle budget exhausted after | Binding constraint |
|---|---|---|---|
| 10 s | 15.8 M | **116 days** | cycles |
| 30 s | 5.3 M | **0.95 years** | cycles |
| 60 s | 2.6 M | **1.9 years** | cycles |
| **180 s** (vendor floor) | 876,600 | 5.7 years | balanced |
| **300 s** (5 min) | 526,000 | 9.5 years | the 5-year rating, not cycles |
| 900 s (15 min) | 175,000 | 28.5 years | the 5-year rating |

**This answers the map's question directly: a 5-minute gauge is not
destructive.** At 300 s the panel would spend roughly half its rated cycle
budget over its rated 5-year life, and the calendar rating becomes the binding
constraint rather than the cycles. A 30-second gauge would consume the entire
budget inside a year.

## 7. ⚠ Risk: the budget is second-hand

The 1,000,000-cycle figure is a **new-panel** rating. The maintainer's panel
came off a pwnagotchi build, and pwnagotchi drives its display on every epoch —
a cadence far faster than 180 s. **An unknown and potentially large fraction of
the cycle budget is already spent, and no vendor number can be adjusted for
it.** Per the ticket, this is flagged rather than quantified.

Two practical consequences:

1. Treat 180 s as a **floor, not a target**. Prefer the slowest cadence the
   feature tolerates; the table in §6 shows the cost of going faster is
   superlinear in panel life.
2. **Ghosting is the empirical signal.** If the panel already shows residual
   images after a full refresh, or contrast that visibly degrades over a
   partial burst, that is prior wear surfacing — and the response is a slower
   cadence and a shorter N, not a higher VCOM.

## 8. Driver support on Debian 13 / Python 3.13

**Which library**: the official `waveshareteam/e-Paper` repository,
`RaspberryPi_JetsonNano/python/lib/waveshare_epd/`, module **`epd2in13_V4`**.
There is no other first-party option, and the revision matters — `epd2in13.py`
(V1), `epd2in13_V2.py`, `epd2in13_V3.py` and `epd2in13_V4.py` are separate
modules with different init sequences and different register semantics.

**It is not pip-installable from PyPI.** The repo ships a `setup.py`
(`name='waveshare-epd'`) but Waveshare does not publish it; the wiki's own
install path is `git clone` + run the example in place, i.e. vendoring.

> ⚠ **`waveshare-epaper` 1.4.0 on PyPI is not Waveshare's.** It is a
> third-party republish by `yskoht`
> (<https://github.com/yskoht/waveshare-epaper>) and it declares
> `RPi.GPIO>=0.7.0,<0.8.0` — the dependency that does *not* work on current
> Raspberry Pi OS. Do not reach for it because it installs more easily.

**Python 3.13 compatibility: fine.** `epd2in13_V4.py` and `epdconfig.py` are
pure Python with no C extension of their own, no version pin, and no use of
anything removed in 3.12/3.13 (no `distutils`, no `imghdr`). The only imports
are `logging`, `os`, `sys`, `time`, `subprocess` and `ctypes`.

**Debian 13 compatibility: fine, and better than the wiki's own text
suggests.** The current `epdconfig.py` `RaspberryPi` class imports exactly two
third-party modules:

```python
import spidev
import gpiozero
```

It has **already migrated off `RPi.GPIO`** — which is the usual Bookworm/Trixie
breakage — and the wiki's Python install instructions match
(`python3-pil`, `python3-numpy`, `spidev`, `python3-gpiozero`; `RPi.GPIO`
appears only in the stale 2019 `readme_rpi_EN.txt`). Availability, verified
2026-09-05:

| Package | Debian 13 (trixie) | Raspberry Pi OS trixie (`archive.raspberrypi.com`) |
|---|---|---|
| `python3-gpiozero` | 1.6.2-1+b1 (arm64) | **2.0.1-0+rpt1+trixie** |
| `python3-spidev` | **3.6-1+b6 (arm64)** | not in rpt repo — use Debian's |
| `python3-pil` | 11.1.0-5+deb13u4 (arm64) | — |
| `python3-lgpio` | not in Debian | 0.2.2-1~rpt1+trixie |

gpiozero 2.0.1 with `python3-lgpio` present is the supported Pi-side GPIO stack
on trixie, and it is what `epdconfig.py` targets. **Two setup gotchas:**

1. **`setup.py` is stale and lies.** It still declares `RPi.GPIO` as a
   dependency while the code imports `gpiozero`. Installing via `pip install .`
   will drag in a broken `RPi.GPIO`. Vendor the `waveshare_epd` package
   directly, or install with `--no-deps` and satisfy the imports from apt.
2. **PEP 668.** Debian 13 marks the system Python externally-managed; the Pi
   side is already run out of `/opt/zeropi-display` with system Python
   (`CLAUDE.md`), so prefer `apt install python3-gpiozero python3-spidev
   python3-pil` over pip.

**Unverified hardware caveat.** `epdconfig.py` unconditionally drives a
`PWR_PIN` on **BCM 18** (`GPIO_PWR_PIN.on()` in `module_init`, `.off()` in
`module_exit`) — a power-gating line present on Waveshare's newer driver
boards. Whether the maintainer's ex-pwnagotchi HAT wires GPIO 18 to anything
was **not determined**. If it does not, `module_exit()` will not actually
remove power and the "don't leave it powered" rule in §5 must be satisfied by
`epd.sleep()` alone. If it does, note that pwnagotchi also uses GPIO 18, and a
pin conflict is conceivable. Confirm on the bench.

---

## 9. Conclusions

### Minimum safe update interval

> ## **180 seconds.**
>
> One panel update every 180 s, **counting partial and fast updates the same as
> full ones**.

Justification, and its limits:

- 180 s is Waveshare's own stated recommendation (wiki § Precautions).
- It is independently corroborated by the V4 spec's `Life` row: 1,000,000
  updates over a 5-year rating is one update per 158 s, so 180 s is the rate at
  which the panel reaches its calendar rating without exhausting its cycle
  budget.
- **Partial updates are counted against the same budget deliberately.** The
  vendor never says a partial update is cheaper on panel life. It says the
  opposite about ghosting. Absent a number, counting them equally is the only
  defensible choice.
- **Where the vendor is silent, say so**: Waveshare explicitly exempts
  partial-refresh-capable panels from the 180 s rule and then gives no
  substitute figure. Anything faster than 180 s is **not vendor-endorsed and
  not vendor-forbidden — it is undocumented**. The §6 table is the honest way
  to price that decision; it is arithmetic on vendor figures, not a vendor
  recommendation.
- Given §7 (second-hand panel), 300 s is the better *operating point* even
  though 180 s is the floor.

### Two-speed scheme (fast partial gauge + periodic full refresh)

> ## **Yes — supported, and it is the vendor's own reference pattern.**

`epd_2in13_V4_test.py` implements precisely this shape:

```
init() → Clear() → displayPartBaseImage(frame)
       → displayPartial(frame) × N
       → init() → Clear()          # full refresh, resets ghosting
       → sleep()
```

Constraints that a cadence spec must carry:

| Constraint | Value | Source |
|---|---|---|
| Max consecutive partial (or fast) updates before a full refresh | **5** | V4 spec §6.2 note 2 |
| Partial update duration | 0.42 s typ | V4 spec §6.2 |
| Full update duration | 3 s typ, flickers several times | V4 spec §6.2, §8 |
| Must re-`init()` when switching partial → full | yes | wiki FAQ; V4 demo |
| Must `sleep()` when idle | yes | wiki § Precautions (repairs impossible if ignored) |
| Deep sleep discards the partial base image | yes | V4 spec §6.2 ("Ram data not retain"); re-base after every wake |
| Sub-region (windowed) partial updates | **not supported by the shipped V4 driver** | `epd2in13_V4.py`, `EPD_2in13_V4.c` |
| Full refresh at least daily | yes (vendor self-contradictory; safe reading) | wiki § Precautions |

**A concrete cadence that satisfies every constraint above simultaneously:**

- one panel update every **180 s**;
- **every 6th update is a full refresh**, the other five are partial →
  a full refresh every **18 minutes**, N = 5 exactly;
- the visible cost of the full refresh is one 3 s flicker every 18 minutes;
- total draw rate 1,000,000 cycles ÷ 180 s = **5.7 years**, i.e. the panel
  reaches its calendar rating first;
- and the daily-full-refresh rule is satisfied 80× over.

Slowing the base interval to 300 s (full refresh every 30 min) keeps all
constraints and roughly halves the cycle spend; given §7 that is the
recommended starting point, with 180 s available as headroom if the gauge feels
stale.

## 10. Not determined

Stated plainly rather than filled in:

- **No vendor minimum interval for partial-refresh-capable panels.** Waveshare
  exempts them from the 180 s rule and names no replacement. The 180 s
  recommendation above is the unexempted general figure, chosen because it is
  the only vendor number that exists and because the `Life` arithmetic
  independently supports it.
- **No vendor statement on whether a partial update consumes the same life
  budget as a full one.** The 1,000,000 figure is not broken down by mode.
- **Whether "refresh at least once every 24 hours" applies to black/white
  panels.** The wiki's Precautions say it unqualified; the wiki's screen FAQ
  scopes the identical advice to multi-colour panels. Unresolved.
- **How much of the 1,000,000-cycle budget the pwnagotchi already spent.**
  Unknowable, and out of scope per the ticket.
- **Whether the maintainer's HAT wires BCM 18** (the driver's `PWR_PIN`), and
  therefore whether `module_exit()` genuinely powers the panel down.
- **Whether a windowed sub-region partial update works on V4.** The controller
  has the registers and the driver exposes `SetWindow`, but no shipped V4 code
  path uses them and nothing in the V4 spec documents the behaviour. Would need
  bench testing; not required for a two-speed scheme, which works fine by
  redrawing a region inside a full frame.
- **The physical panel revision was not independently verified.** Taken as
  confirmed from the ticket. If the label on the back says V2 or V3, the timing
  numbers in §2 and the N = 5 in §3 do not transfer — those revisions have
  their own specification PDFs and their own driver modules.
