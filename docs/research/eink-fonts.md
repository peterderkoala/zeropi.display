# Research: which font the panel draws with

Ticket: [#54](https://github.com/peterderkoala/zeropi.display/issues/54) — part of map [#51](https://github.com/peterderkoala/zeropi.display/issues/51).

**Scope**: facts a later decision ticket needs. No recommendation, no code
change. Everything below is either read from a primary source (Pillow's own
source at the pinned version, FreeType's C source via Pillow's bindings,
Debian's `copyright` files, Waveshare's pinned upstream commit) or measured
directly by rendering, on a matching **Pillow 11.1.0** install (a scratch
`uv venv` pinned to exactly that version, since the sandbox's system Pillow
is 12.3.0 — verified `12.3.0` first, then pinned down to match the Pi).

Confirmed starting fact (from the ticket, not re-verified here): the Pi has
**zero** font files anywhere under `/usr/share/fonts`.

## 1. What Waveshare's own examples ship and assume

Waveshare's upstream repo, at the pinned vendored commit
(`a794fbc39656b0f93938d1ffb3fdc77eaed9e9fc`), ships its own font and does
**not** assume any system font exists.

`RaspberryPi_JetsonNano/python/examples/epd_2in13_V4_test.py` (the demo for
exactly our panel):

```python
font15 = ImageFont.truetype(os.path.join(picdir, 'Font.ttc'), 15)
font24 = ImageFont.truetype(os.path.join(picdir, 'Font.ttc'), 24)
...
draw.text((120, 60), 'e-Paper demo', font = font15, fill = 0)
draw.text((110, 90), u'微雪电子', font = font24, fill = 0)
```

`picdir` is `RaspberryPi_JetsonNano/python/pic/`, sitting next to the
`lib/waveshare_epd/` directory this repo already vendors (verbatim, per
`pi/waveshare_epd/README.md`). `Font.ttc` there is **5.18 MB** (downloaded
and inspected directly, not just read about) and its own English-language
demo string proves it carries CJK glyphs (微雪电子 = "Waveshare
Electronics"). `strings` on the binary turns up:

```
Droid is a trademark of Google and may be registered in certain jurisdictions.
http://www.apache.org/licenses/LICENSE-2.0
Licensed under the Apache License, Version 2.0
WQYF
```

i.e. it looks like a Droid Sans + WenQuanYi (`WQY`) CJK concatenation —
both Apache-2.0-family — bundled into one `.ttc` with **no `LICENSE` file
anywhere in the upstream repo** (checked the repo root listing at the pinned
commit; none present) and no attribution comment in the example itself. That
absence is exactly the kind of thing this project's own
`pi/waveshare_epd/README.md` already flags about this upstream (stale
`setup.py`, PyPI republish problems) — the driver code is vendored
carefully with a pinned commit and licence note; the font is not something
to copy the same way without redoing that licence homework, and at 5.18 MB
for glyphs this project doesn't need (no CJK anywhere in the Payload), it's
disproportionate regardless.

Every other Waveshare `epd*_test.py` for the small panel family follows the
identical pattern: bundled `Font.ttc` from `pic/`, loaded by explicit path,
at whatever pixel sizes the demo wants (15/24 here; other panels use
16/20/24/etc). Nothing upstream ever assumes `/usr/share/fonts` has
anything in it — the assumption baked into `desktop/gauge_settled.py`
(`/usr/share/fonts/truetype/dejavu/...`) is this project's own, not
Waveshare's.

## 2. The realistic options, with concrete detail

### a. `fonts-dejavu-core` from apt (what `gauge_settled.py` already assumes)

- apt candidate on the Pi: **2.37-8** (per the ticket). Same version
  confirmed available in this sandbox's own apt (Ubuntu noble mirror),
  `Installed-Size: 2292` (i.e. **~2.3 MB** on disk for the whole package —
  `dpkg -L fonts-dejavu-core` lists exactly four files:
  `DejaVuSans.ttf`, `DejaVuSans-Bold.ttf`, `DejaVuSerif.ttf`,
  `DejaVuSerif-Bold.ttf`). `gauge_settled.py` only ever opens
  `DejaVuSans.ttf` and `DejaVuSans-Bold.ttf` — measured **744 KB + 696 KB =
  ~1.44 MB** of that install actually gets `mmap`'d — but apt has no partial
  install, so the Serif pair (another ~850 KB) comes along regardless.
- Licence: **Bitstream Vera licence** (permissive, MIT-Modification-alike —
  read directly from `/usr/share/doc/fonts-dejavu-core/copyright` in this
  sandbox, same package/version). Redistributable and bundlable; the one
  restriction is you can't rename a modified copy to something containing
  "Bitstream" or "Vera". DejaVu's own additions are public domain per the
  same file. No attribution string has to appear on the panel.
- Runtime location if added: `/usr/share/fonts/truetype/dejavu/*.ttf`,
  exactly the path `gauge_settled.py` already hardcodes — this is the
  zero-code-change option, an `apt-get install` line added to
  `pi/install-pi.sh` alongside the other four packages it already lists
  there for the panel stack.

### b. Vendoring a TTF into the repo (the way the driver is vendored)

Three licence-compatible candidates, all confirmed via the Debian
`copyright` file for the matching apt package in this sandbox (same
mechanism used to read DejaVu's above):

| Font | Licence | Source | Note |
|---|---|---|---|
| DejaVu Sans (+ Bold) | Bitstream Vera licence | dejavu-fonts.github.io | same files as (a), just copied into the repo instead of apt-installed |
| Liberation Sans | SIL OFL 1.1 | github.com/liberationfonts | apt `fonts-liberation`, `Installed-Size: 4285` (~4.3 MB, four styles) |
| Terminus (as OpenType bitmap, `.otb`) | SIL OFL 1.1 | terminus-font.sourceforge.net, packaged as `fonts-terminus-otb` | see §3/§4 — this is a genuine bitmap font, not a scaled outline |

Vendoring means the same "why vendored" tradeoff the driver README already
documents applies again: no dependency on what's in the Pi's apt cache at
provisioning time, but a byte-for-byte pinned copy this repo is now
responsible for updating and licence-tracking (a `README.md` alongside it,
per the pattern `pi/waveshare_epd/README.md` sets). Noto wasn't found to
have a materially different profile from Liberation/DejaVu for this
Latin-only, no-CJK use case, so it isn't tabulated separately — it's a much
larger family (multiple MB per style, built for pan-Unicode coverage this
project doesn't need).

### c. PIL's built-in `ImageFont.load_default()`

**This changed materially in a way the ticket's instinct to check was
right about.** Read directly from the installed `ImageFont.py` at 11.1.0:

```python
def load_default(size: float | None = None) -> FreeTypeFont | ImageFont:
    """If FreeType support is available, load a version of Aileron Regular,
    https://dotcolon.net/font/aileron, with a more limited character set.

    Otherwise, load a "better than nothing" font.

    .. versionadded:: 1.1.4

    :param size: The font size of Aileron Regular.

        .. versionadded:: 10.1.0
    """
```

So: **since Pillow 10.1.0**, `load_default()` embeds a real TrueType font
(**Aileron Regular**, base64-encoded inside `ImageFont.py` itself, loaded
via `BytesIO` — not a file on disk) and accepts a `size` argument. Before
that, and today only as a fallback if FreeType support is unavailable, it
returned Pillow's old built-in **bitmap** font (a hardcoded 1980s-style
`courB08`-derived glyph table, embedded the same way, fixed size, no `size`
argument). Verified live in this sandbox:

```
>>> ImageFont.load_default()
<PIL.ImageFont.FreeTypeFont object at ...>
>>> f.font.family, f.font.style
('Aileron Regular', ...)
>>> ImageFont.load_default(size=28)   # works — FreeTypeFont, scalable
```

Because the Pi's PIL is 11.1.0 (FreeType support present, confirmed
already working per the ticket), `load_default()` there returns Aileron,
not the old bitmap font — a common assumption from older blog posts/answers
about `load_default()` being "a tiny fixed 8px bitmap font" is **out of
date** for this install. It needs no file on disk at all (already inside
the `python3-pil` package the Pi has), but Aileron's own licence is murky —
web search on `dotcolon.net`'s own terms turns up **conflicting claims**
(CC0/public-domain according to some redistributors, "free for personal use
only, commercial needs a paid licence" according to others) that this
research did not resolve to a single primary statement from dotcolon.net
itself. That ambiguity doesn't block using `load_default()` as shipped
inside Pillow — Pillow's own project already made that legal call to embed
it — but it does mean this font shouldn't be treated as a known-clear
licence the way DejaVu/Liberation/Terminus are.

### d. Bitmap fonts for small 1-bit panels: PCF/BDF, and OTB

Pillow 11.1.0 still ships `PcfFontFile.py` and `BdfFontFile.py`, but those
are **converters** (BDF/PCF → Pillow's own legacy `.pil`/`.pbm` pair via
`ImageFont.load()`), not what a normal render call uses.

The far more relevant, and directly verified, fact: **`ImageFont.truetype()`
opens PCF and OTB (OpenType-wrapped bitmap) files directly**, because
Pillow's FreeType binding is just FreeType, and FreeType has built-in PCF
and bitmap-strike drivers. Verified two ways:

- Decompressed a stock X11 PCF (`/usr/share/fonts/X11/misc/6x13B.pcf.gz`,
  from this sandbox's own `xfonts-base`) and opened it with
  `ImageFont.truetype("...6x13B.pcf", 13)` — succeeded, returned
  `('Fixed', 'Bold SemiCondensed')`.
- Downloaded and extracted Debian's `fonts-terminus-otb` package
  (`terminus-bold.otb`, an OpenType wrapper around Terminus's hand-drawn
  bitmap strikes) and opened it the same way. **Only the sizes actually
  baked into the file work** — asking for an unbaked size raises
  `OSError: invalid pixel size` rather than scaling:

  ```
  8px  FAIL   12px OK   16px OK   20px OK   24px OK   28px OK   32px OK
  9px  FAIL   13px FAIL 14px OK   18px OK   22px OK   26px FAIL
  10px FAIL   ...
  ```

  Terminus-Bold's strikes are `{12,14,16,18,20,22,24,28,32}` px — no strike
  at 10px or 13px, both sizes `gauge_settled.py` currently draws in. This is
  the concrete cost of a genuine bitmap font: you get the sizes the
  designer drew and nothing between them, in exchange for every pixel being
  hand-placed rather than machine-rasterized. Terminus is licensed SIL OFL
  1.1 (verified from `fonts-terminus-otb`'s own Debian `copyright` file,
  same text as Liberation's).

## 3. The 1-bit rendering question

**Primary-source finding, not folklore**: drawing text into a mode `"1"`
image does **not** anti-alias and then threshold. Pillow tells FreeType to
skip anti-aliasing altogether and rasterize the glyph with its
monochrome-specific hinter.

Traced end to end in the installed source:

1. `ImageDraw.Draw.__init__` (`ImageDraw.py`):
   ```python
   if mode in ("1", "P", "I", "F"):
       # FIXME: fix Fill2 to properly support matte for I+F images
       self.fontmode = "1"
   else:
       self.fontmode = "L"  # aliasing is okay for other modes
   ```
   A `Draw` bound to a mode-`"1"` image sets `fontmode = "1"` at
   construction time — this is why `gauge_settled.py`'s
   `Image.new("1", ...)` matters, not just an implementation detail.

2. `Draw.text()` passes that `mode` straight into
   `font.getmask2(text, mode, ...)`.

3. Pillow's C extension, `_imagingft.c` (fetched at the pinned tag,
   `python-pillow/Pillow@11.1.0`), branches on exactly that string:
   ```c
   mask = mode && strcmp(mode, "1") == 0;
   ...
   if (mask) {
       load_flags |= FT_LOAD_TARGET_MONO;
   }
   ```
   `FT_LOAD_TARGET_MONO` is FreeType's own flag telling its hinter to
   produce a **1-bit** (`FT_PIXEL_MODE_MONO`) bitmap using hinting rules
   tuned for un-antialiased output — a different code path inside FreeType
   from the grey-level rasterizer, not a post-hoc threshold of the
   anti-aliased one.

**Measured, not just read**: rendered the same text both ways and diffed
them. At **10px** (`"RESETS IN 5H"`, DejaVu Sans Bold — the exact label size
`gauge_settled.py` uses) — top row is the real `mode="1"` path
(`FT_LOAD_TARGET_MONO`), bottom row is a naive "render to greyscale `L`,
then `threshold(127)`" — see
[`06-10px-mono-vs-threshold-8x.png`](eink-fonts/06-10px-mono-vs-threshold-8x.png):
letterforms visibly differ stroke-by-stroke (the "R", "5" and "S" pick up
or drop pixels in different places) — confirming the two code paths are not
equivalent, not just theoretically per the C source but visibly at the size
this project actually uses. Both are legible; this is evidence that the
hinting path is doing real, non-trivial work at this size, not evidence
that one is unreadable.

At **28px** (the numeral size) the same comparison —
[`03-mode1-direct-vs-naive-threshold-4x.png`](eink-fonts/03-mode1-direct-vs-naive-threshold-4x.png)
— shows near-identical output between the two paths: at that many pixels
per glyph, hinting differences wash out and both approaches converge, which
matches the general expectation that hinting matters most exactly where
`gauge_settled.py`'s 10/11/13px labels sit and least where its 24/28px
numerals sit.

Whether a hinted bitmap font (Terminus) is "materially more legible" than
a scaled TTF at these sizes is a judgment call this ticket isn't answering,
but the raw material to judge it is now in the samples directory:
[`01-mode1-direct-dejavu-4x.png`](eink-fonts/01-mode1-direct-dejavu-4x.png)
reproduces `gauge_settled.py`'s exact layout and font calls (sizes
10/11/13/24/28, bold and regular) at real panel dimensions (250×122);
[`07-terminus-otb-bitmap-6x.png`](eink-fonts/07-terminus-otb-bitmap-6x.png)
shows the same kind of string set in Terminus Bold at its native 14px
strike — visibly crisper, uniform-stroke-width glyphs, the classic
console-bitmap-font look, versus DejaVu's hinted-but-still-outline shapes.

## 4. Disk / memory footprint

| Option | On-disk cost | Where it lives |
|---|---|---|
| `fonts-dejavu-core` via apt | ~2.3 MB installed (only ~1.44 MB of it, Sans + Sans-Bold, ever gets opened) | `/usr/share/fonts/truetype/dejavu/*.ttf` — the path already hardcoded in `gauge_settled.py` |
| Vendored TTF(s) in-repo | Same file sizes as above, whichever styles are actually copied in | Wherever the repo puts it — e.g. alongside `pi/waveshare_epd/` — deployed by `install.sh`'s tarball, no apt step needed |
| `fonts-liberation` via apt/vendor | ~4.3 MB installed for all four styles; a single style is ~1 MB | Same idea, `/usr/share/fonts/truetype/liberation/` if apt-installed |
| `ImageFont.load_default()` | **0 extra bytes** — already inside `python3-pil`'s installed `ImageFont.py` (base64-embedded) | No file; nothing to provision |
| `fonts-terminus-otb` via apt/vendor | Package `Installed-Size: 1451` (~1.5 MB) for 4 styles (normal/bold/oblique/bold-oblique); a single `.otb` is 330–380 KB | `/usr/share/fonts/opentype/terminus/*.otb` if apt-installed |

Memory: none of these are large enough to matter on a Pi Zero 2W (512 MB
RAM). FreeType's working set for a loaded face plus a handful of rendered
glyph bitmaps at a few fixed sizes is on the order of tens to a few hundred
KB, regardless of which option above is chosen — this wasn't independently
profiled on Pi hardware (out of scope, no SSH per the ticket), but nothing
in any of these options (file sizes above, typical FreeType glyph cache
behaviour) suggests it's worth measuring before deciding.

## Samples

All in `docs/research/eink-fonts/` (paired `NN-name.png` at true panel
resolution, `NN-name-Nx.png` nearest-neighbour scaled for readability — the
scaled copy shows exactly the same lit pixels, just bigger, so it is not
"prettier," it's the same evidence at a legible zoom):

- `01-mode1-direct-dejavu(-4x).png` — `gauge_settled.py`'s exact layout/font
  calls at real 250×122, mode `"1"`.
- `02-mode1-10px-vs-28px(-4x).png` — the two sizes in isolation.
- `03-mode1-direct-vs-naive-threshold(-4x).png` — 28px, real path vs naive
  AA+threshold.
- `04-mode1-load_default-aileron(-4x).png` — PIL's bundled Aileron font at
  10px/28px via `load_default(size=...)`.
- `05-mode1-pcf-bitmap-font(-4x).png` — a raw X11 PCF bitmap font loaded
  directly by `ImageFont.truetype()`.
- `06-10px-mono-vs-threshold(-8x).png` — the 10px real-vs-naive comparison,
  at the label size that actually matters.
- `07-terminus-otb-bitmap(-6x).png` — Terminus Bold, 14px native strike.

Render script (not committed — reproducible from this document): built
`Image.new("1", (250,122), 1)`, drew with `ImageDraw.Draw(img).text(xy, s,
font=ImageFont.truetype(path, size), fill=0)` using the exact paths/sizes
`gauge_settled.py` uses, against a `uv venv` pinned to `pillow==11.1.0` to
match the Pi's installed version exactly (the ambient sandbox environment
runs 12.3.0, which was ruled out for this purpose specifically because
`load_default()`'s behaviour and `_imagingft.c` internals are exactly the
kind of thing that can drift between Pillow releases).
