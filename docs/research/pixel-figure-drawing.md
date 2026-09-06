# Research: drawing and laying out a multi-pose pixel figure on the 1-bit panel

Resolves #50 (child of map #49). Scope is strictly *technique* — how to draw
and store a small pixel-art figure with several poses on this project's
existing 1-bit PIL frame. What the poses mean and where the figure sits in
the layout are explicitly out of scope; see #49's "Not yet specified".

Local facts this note builds on:
- Panel: Waveshare 2.13" V4, 250x122, **1-bit** (`Image.new("1", ...)`).
  Vendored driver: `pi/waveshare_epd/` (do not modify — see its
  `pi/waveshare_epd/README.md`).
- Settled drawing idiom: `desktop/gauge_settled.py` on `prototype/live-gauge`
  (commit `dde1c58`) — one `ImageDraw.Draw` frame, hand-drawn
  `d.text()`/`d.rectangle()`/`d.line()` calls, a small `bar()` helper, and a
  `save()` that writes both the real-size PNG and a `-3x` nearest-neighbour
  upscale specifically to eyeball legibility.
- Display contract: `docs/spec-usage-pipeline.md` §9 — this is prose about
  *what* the Gauge shows, not *how*, but it's the precedent for "no grey, no
  dithered stale state" thinking that carries over directly to a pixel figure
  (§9.2's "no dimming/hatching/inversion" ruling is exactly the kind of trap
  a pose-based figure could reintroduce by accident).
- Bench proof the panel draws at all: `pi/epd-selftest.py`.

---

## 1. Authoring format: hand-drawn primitives vs. pasted bitmap assets

**Recommendation: hand-drawn `ImageDraw` primitives (rectangles/lines/points),
matching the existing Gauge idiom — not separate asset files.**

Two real options exist:

**A. `ImageDraw` primitives** (what `gauge_settled.py` already does for
everything else). A pose is a Python function that calls `d.rectangle()`,
`d.point()`, `d.line()` against the same frame the rest of the layout uses.

**B. Pre-made bitmap assets** (XBM/PBM/1-bit PNG) loaded with `Image.open()`
and composited with `Image.paste()`, or raw bytes fed through
`Image.frombytes("1", size, data)`.

For this project specifically, (A) wins, for reasons tied directly to
`CLAUDE.md`'s "no build, lint, or test tooling" constraint:

- **No asset pipeline exists.** There's no font-to-bitmap step, no image
  optimizer, nothing that would package/validate a `.png`/`.xbm` at build
  time. A pasted-asset approach means checking small binary PNGs into git
  with no tooling to regenerate or verify them — you'd hand-edit pixels in
  an external image editor and hope the diff (a changed binary blob) is
  reviewable, which it isn't. `git diff` on a PNG shows nothing useful.
- **A `.xbm`/`.pbm` is textual** (both are plain-text 1-bit formats — XBM is
  literally a C header with a byte array; PBM's plain "P1" variant is ASCII
  0/1), so in principle they *are* diffable. But at a "few dozen pixel
  square" figure size, a hand-maintained XBM byte array is strictly harder
  to read and edit than the equivalent Python coordinate list, and pulls in
  a second file format/tooling concept for a single-contributor project that
  currently has zero.
- **Compositing cost**: `Image.paste()` of a foreign-mode source onto a `"1"`
  canvas needs care (see §3) that hand-drawn primitives simply avoid — a
  primitive drawn with `fill=0`/`fill=1` on the `"1"` canvas is correct by
  construction, there's no conversion step to get wrong.
- Pwnagotchi's own default (see §5) is text/ASCII-art glyphs drawn via
  `ImageDraw.text()`, i.e. the same "no separate asset, draw straight into
  the frame" philosophy as (A), just using a font glyph instead of shape
  primitives. Its *optional* PNG mode is exactly case (B), and pwnagotchi
  pays real complexity for it (alpha-channel handling, see §5) — a cost this
  project doesn't need to take on for a first iteration.

**When to revisit**: if the figure grows complex enough that hand-writing
`d.point()` calls for every pixel becomes unmanageable (a detailed sprite,
not a simple icon), a small inline bitmap literal (see §2, option C) is the
next step up before reaching for real asset files.

## 2. Storing multiple poses

**Recommendation: one Python function per pose, all in a single module,
returning/drawing into the shared frame — not a sprite sheet, not separate
files per pose.**

Three shapes considered:

- **Sprite sheet** (one wide image, poses at fixed offsets, sliced with
  `Image.crop()`): rejected for the same reason as bitmap assets generally —
  it's a binary file with no diff, and slicing math is one more thing to get
  wrong for a project with no test tooling to catch an off-by-one crop.
- **One file per pose**: rejected — more files than the project's size
  warrants (`pi/` and `desktop/` are both flat, single-purpose-file
  directories today), and still binary-diff-blind if the files are images.
- **Inline coordinate/point lists in Python, one function per pose**
  (recommended): matches `gauge_settled.py`'s existing shape exactly — that
  file already has one function per visual state (`render_gauge`,
  `render_no_data`). A pose module would look like:

  ```python
  def pose_idle(d, x, y):
      d.rectangle([x, y, x + 7, y + 7], outline=0)      # head
      d.point([(x + 2, y + 3), (x + 5, y + 3)], fill=0)  # eyes
      ...

  def pose_alert(d, x, y):
      ...  # same footprint, different eye/mouth points
  ```

  This is plain-text Python, diffs cleanly per pose, and needs no new
  concept beyond what the Gauge code already establishes. For a figure with
  many small point-level differences between poses (e.g. only the "eyes"
  differ), factor the shared skeleton into a helper and let each pose
  function draw only its distinguishing points — keeps diffs between poses
  small and reviewable, and makes "what changed between pose A and pose B"
  visible in a code review the way an image diff never is.

- **Escape hatch for a denser sprite** (option C, if hand-coding points ever
  gets unwieldy): a literal multi-line string of `#`/`.` per pose, parsed
  into set-pixel calls at draw time — still plain text, still diffable,
  still no external tooling, but scales better past a dozen or so pixels:

  ```python
  POSE_IDLE = """
  .####.
  #.##.#
  #.....
  ##...#
  .#####
  """
  ```
  This is close to what several "ASCII pixel art to bitmap" hobby scripts do
  and is easy to hand-verify by eye directly in the source. Keep it as a
  fallback, not the default — plain point lists are more idiomatic given the
  existing `bar()`-helper style in `gauge_settled.py`.

## 3. 1-bit legibility constraints

**Recommendation: draw shapes with hard integer pixel boundaries at
panel-native resolution, never let anything pass through an intermediate
mode that dithers, and always render the `-3x` check before trusting it.**

Concrete pitfalls, and why they bite specifically on a *true* `"1"` surface
(not a greyscale surface later thresholded):

- **`Image.new("1", ...)` has exactly two values, no anti-aliasing.**
  `ImageDraw` primitives on a `"1"`-mode image draw hard-edged pixels only —
  there is no sub-pixel blending to fall back on, unlike drawing on an `"L"`
  or `"RGB"` canvas and converting down later. This is *good* for legibility
  (no accidental grey), but means every curve/diagonal in a pose looks
  jagged at small size — the honest fix is designing the pose *for* that
  jaggedness (Pwnagotchi's ASCII faces embrace exactly this — see §5), not
  fighting it.
- **The dithering trap is at the conversion boundary, not the draw calls.**
  If a pose is ever authored on an `"L"` (greyscale) or `"RGB"` canvas and
  then `.convert("1")`'d onto the frame, Pillow's default converter applies
  Floyd–Steinberg dithering, which scatters isolated on/off pixels to
  simulate grey — completely wrong for a handful-of-pixels figure, since a
  few stray dithered pixels at that size read as noise, not shading. Pillow
  docs: `Image.convert(mode, dither=...)` — pass `dither=Image.NONE` if a
  greyscale-authored asset ever needs converting, or better, avoid the
  detour entirely by drawing straight onto the `"1"` frame per §1.
  (<https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.convert>)
- **`Image.paste()` of a foreign-mode source is the same trap in disguise.**
  Pasting an `"L"` or `"RGBA"` image onto a `"1"` canvas forces an implicit
  conversion with the same dithering default. If asset-based sprites are
  ever adopted (§1's escape hatch), author and save them *already* in mode
  `"1"` so paste is a straight bit copy, not a conversion.
- **A few pixels look fine zoomed in, illegible at arm's length.** This is
  exactly why `gauge_settled.py.save()` writes the `-3x` nearest-neighbour
  upscale alongside the real-size PNG — `Image.resize(..., Image.NEAREST)`
  (never a smoothing filter, which would reintroduce grey into a preview of
  a surface that has none). Any pose-drawing script should do the same:
  save real-size + `-3x` PNGs and actually look at the *real-size* one full
  screen, not just the zoomed one, before deciding a pose reads. A figure
  that looks like a clear face at 3x but a smear at 1x is a failed pose, not
  a preview artifact.
- **Minimum practical feature size.** As a rule of thumb from small-panel
  pixel-art practice (both pwnagotchi's ~40px-tall ASCII faces and general
  8x8/16x16 icon-font design): a feature (an eye, a limb) needs roughly 2+
  pixels of contrast against its background to read as a *shape* rather than
  noise at normal viewing distance; a figure under ~12x12px total will
  mostly read as a blob unless it's built from very few, very deliberate
  marks (pwnagotchi's faces are ASCII strings ~7 characters wide rendered at
  a large font size for exactly this reason — see §5).

## 4. Compositing into the existing frame

**Recommendation: a pose function takes the same `(draw, x, y)` (or just
`draw`, with position baked into the call site) signature as `bar()` in
`gauge_settled.py`, and is called inline during the same `frame()` /
`render_*()` pass — no separate render/compose step.**

The existing Gauge code already establishes the pattern to extend:

```python
def frame():
    img = Image.new("1", (PANEL_W, PANEL_H), 1)
    return img, ImageDraw.Draw(img)

def bar(d, x, y, w, h, pct):
    d.rectangle([x, y, x + w, y + h], outline=0)
    ...
```

A pose function is just another helper of this shape:

```python
def pose_idle(d, x, y):
    ...  # d.rectangle/d.point calls, offset by x, y
```

and gets called from inside `render_gauge()` (or whatever the figure's
eventual host function is) exactly like `bar(d, 3, 40, 244, 8, five_pct)` is
today — same `d`, same coordinate space, one `ImageDraw.Draw` instance for
the whole frame, one call to `save()`/`getbuffer()` at the end. No second
`Image`, no second `Draw`, no paste step. This is a straightforward
extension of the current code, not a new abstraction, and it sidesteps every
mode-conversion pitfall in §3 by construction — the pose never exists as
its own `Image` object at all, only as draw calls against the shared canvas.

If pose selection needs to be dynamic (draw pose N depending on state), the
natural shape is a `dict` or `if/elif` dispatch from a pose-name string to
the corresponding function, called once per frame render — same shape
`fmt_countdown()` already uses for its own state branching.

## 5. Prior art

### Pwnagotchi (same hardware lineage — highest priority)

The actively maintained fork is **jayofelony/pwnagotchi**
(<https://github.com/jayofelony/pwnagotchi>) — evilsocket's original
(<https://github.com/evilsocket/pwnagotchi>) is the historical source the
face system originates from but is no longer the active repo. Both share the
face architecture described below; sourced from jayofelony's `master`.

**Storage — one string constant per pose, not a sprite sheet or per-pose
file.** `pwnagotchi/ui/faces.py` defines ~28 pose constants as plain Python
string literals, e.g. `LOOK_R = '( ⚆_⚆)'`, covering both idle/expression
states (`SLEEP`, `SLEEP2`, `AWAKE`, `BORED`, `INTENSE`, `COOL`, `HAPPY`,
`GRATEFUL`, `EXCITED`, `MOTIVATED`, `DEMOTIVATED`, `SMART`, `LONELY`, `SAD`,
`ANGRY`, `FRIEND`, `BROKEN`, `DEBUG`, four `LOOK_*` variants) and operation
states (`UPLOAD`, `UPLOAD1`, `UPLOAD2`) — i.e. exactly the "different pose
per state/operation" shape #50 investigates, at pwnagotchi's own scale.
`load_from_config()` lets a config file override individual constants at
runtime, so a user can restyle faces without touching source.
(<https://github.com/jayofelony/pwnagotchi/blob/master/pwnagotchi/ui/faces.py>)

**Default rendering — text glyphs via `ImageDraw.text()`, not composited
bitmaps.** `pwnagotchi/ui/view.py` wires the active face into a `Text` UI
component: `Text(value=faces.SLEEP, position=..., font=fonts.Huge,
png=config['ui']['faces']['png'])`. The canvas itself is set up exactly like
this project's: `Image.new(self.mode, (width, height), background)` +
`ImageDraw.Draw(canvas, mode)`, then every UI element's `draw()` runs against
that one shared canvas/drawer per frame — the same "one frame, draw
everything into it" shape recommended in §4.
(<https://github.com/jayofelony/pwnagotchi/blob/master/pwnagotchi/ui/view.py>)

**Optional PNG mode — the cautionary tale for asset-based sprites.**
`pwnagotchi/ui/components.py`'s `Text.draw()` branches on a `png` flag: when
true, it loads an image file *from the value string as a path*, converts to
RGBA, explicitly flattens semi-transparent pixels to opaque white, inverts
if the configured color is white, converts to mode `"1"`, and only then
pastes onto the canvas. There's also a separate `Bitmap` widget
(`Image.open(path)` + optional `ImageOps.invert()` + `canvas.paste()`) for
non-face images. This is real, working prior art for option B in §1 — and
it's also the concrete shape of the extra complexity (alpha handling, a
color-inversion branch, a file-path-as-value convention) that a
hand-drawn-primitives approach avoids for a first iteration.
(<https://github.com/jayofelony/pwnagotchi/blob/master/pwnagotchi/ui/components.py>)

**Community custom-face mods confirm PNG-based faces are a well-worn
secondary path**, not the default: e.g. the "PWNAGOTCHI-CUSTOM-FACES-MOD"
project and various 64x64px BMP face packs
(<https://github.com/roodriiigooo/PWNAGOTCHI-CUSTOM-FACES-MOD>,
<https://github.com/PersephoneKarnstein/egirl-pwnagotchi>) exist specifically
because users wanted something beyond the ASCII-art default — evidence that
the text/primitive default is the low-friction path and bitmap assets are
opt-in for people who want a fundamentally different visual style, not a
requirement for having multiple poses.

**Takeaway for this project**: pwnagotchi's default architecture is close to
this note's §1/§2/§4 recommendation already — small pose set as inline
Python literals, rendered by drawing straight into the shared frame, no
build step. The one difference is pwnagotchi draws *font glyphs* (ASCII-art
strings through a large font) where this project would draw *shape
primitives* (rectangles/points, matching `gauge_settled.py`'s own idiom) —
both are "no separate asset" approaches; the choice between them is a design
question for the later ticket (does a monospace/emoji font exist on the Pi
with characters that look like a face at the sizes in play? DejaVu Sans,
already used by `gauge_settled.py`/`epd-selftest.py`, has limited box-drawing
glyphs but nothing resembling pwnagotchi's kaomoji-style faces at readable
size — likely favors primitives over glyphs here, but confirming this is
in-scope for the design ticket, not this one).

### Other prior art

No other small e-ink/OLED pixel-character dashboard project surfaced with
comparably concrete, inspectable source during this pass — pwnagotchi is by
far the closest and most relevant precedent (same panel family/lineage, same
per-state-pose shape), and the recommendations above are built on it plus
this project's own existing `gauge_settled.py` idiom rather than on a
broader survey.

---

## Summary for the next (design/prototype) ticket

1. Draw poses with `ImageDraw` primitives straight into the shared frame —
   no image assets, no `Image.paste()`, no separate render pass (§1, §4).
2. One Python function per pose in a single module, in the same style as
   `gauge_settled.py`'s `render_gauge()`/`bar()` — inline point/rectangle
   coordinates, diffable in git (§2). A parsed ASCII-art literal is the
   fallback if hand-coded points get unwieldy for a denser sprite.
3. Never let a pose pass through a non-`"1"` mode or a smoothing resize —
   dithering and smoothing both introduce grey a true 1-bit panel can't show
   and this project's own display contract (§9.2) already rules out.
   Always render both the real-size and a `Image.NEAREST` `-3x` PNG and
   judge legibility from the real-size one (§3).
4. Pwnagotchi is the concrete reference for "small pose set, state-keyed,
   drawn straight into a shared canvas" — its ASCII-glyph default maps to
   this project's primitive-drawing idiom more directly than its optional
   PNG mode does; whether to use font glyphs vs. hand-drawn shapes for the
   actual figure is a design-ticket decision, not decided here (§5).
