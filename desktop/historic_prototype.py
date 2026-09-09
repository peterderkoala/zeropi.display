"""PROTOTYPE — four candidate Historic Views for #52. Throwaway.

    .venv/bin/python desktop/historic_prototype.py docs/research/historic-mocks

Renders each candidate at exactly 250x122, 1-bit, the same way the panel gets
it: drawn straight into mode "1" so PIL takes FreeType's FT_LOAD_TARGET_MONO
path (see docs/research/eink-fonts.md on research/eink-fonts -- this is NOT
the same as drawing greyscale and thresholding).

Values are REAL: the maintainer's own daily totals, 11 active days over 44
calendar days. #26's lesson was that synthetic values flatter a layout --
these do not, because the data is bursty and 10x-skewed.

Not a renderer. The spec ticket takes whatever survives; this exists to be
argued with.
"""
import sys
from datetime import date
from PIL import Image, ImageDraw, ImageFont

W, H = 250, 122
FONT_DIR = "/usr/share/fonts/truetype/dejavu"

# (date, cost_usd, tokens, sessions) -- real, from the Desktop archive.
REAL = [
    ("2026-07-28", 11.58, 43907842, 1),
    ("2026-08-09", 18.88, 28270795, 3),
    ("2026-08-10", 45.81, 148290953, 8),
    ("2026-08-11", 107.76, 395720987, 5),
    ("2026-08-12", 26.49, 88154912, 3),
    ("2026-08-19", 19.92, 36306244, 5),
    ("2026-08-20", 33.87, 123055071, 4),
    ("2026-09-04", 33.54, 70984415, 7),
    ("2026-09-05", 60.23, 84343230, 12),
    ("2026-09-06", 73.23, 180114987, 12),
    ("2026-09-09", 11.21, 16849461, 1),
]
# What the Pi actually holds today: only days pushed while it was running.
PI_HELD = REAL[-4:]
TODAY = "2026-09-09"


def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(f"{FONT_DIR}/{name}", size)


def frame():
    img = Image.new("1", (W, H), 1)
    return img, ImageDraw.Draw(img)


def save(img, out, name):
    img.save(f"{out}/{name}.png")
    img.convert("L").resize((W * 3, H * 3), Image.NEAREST).save(f"{out}/{name}-3x.png")


def calendar_days(rows, n, end=TODAY):
    """Last n CALENDAR days ending at `end`, zero-filled. The Pi never
    computes a date (ADR-0009) -- doing it here is the prototype standing in
    for whoever ends up sending or deriving these."""
    end_d = date.fromisoformat(end)
    by_date = {d: (c, t, s) for d, c, t, s in rows}
    out = []
    for i in range(n - 1, -1, -1):
        d = date.fromordinal(end_d.toordinal() - i)
        c, t, s = by_date.get(d.isoformat(), (0.0, 0, 0))
        out.append((d, c, t, s))
    return out


# --- A: seven calendar days, bar chart --------------------------------------
def variant_a(out, rows=REAL, name="A-7day-bars"):
    img, d = frame()
    days = calendar_days(rows, 7)
    peak = max([c for _, c, _, _ in days] + [1])
    total = sum(c for _, c, _, _ in days)

    d.text((3, 0), "7 DAYS", font=font(10, True), fill=0)
    d.text((W - 3 - d.textlength(f"${total:.0f}", font=font(20, True)), 0),
           f"${total:.0f}", font=font(20, True), fill=0)

    base, top = 104, 30
    slot = W // 7
    for i, (day, cost, _, _) in enumerate(days):
        x = i * slot + 4
        h = int((base - top) * (cost / peak)) if cost else 0
        if h:
            d.rectangle([x, base - h, x + slot - 9, base], fill=0)
        else:
            d.line([x, base, x + slot - 9, base], fill=0)
        d.text((x, base + 3), day.strftime("%a")[:2].upper(), font=font(9), fill=0)
        if cost:
            lbl = f"{cost:.0f}"
            d.text((x + (slot - 9 - d.textlength(lbl, font=font(9))) / 2, base - h - 11),
                   lbl, font=font(9), fill=0)
    save(img, out, name)


# --- B: headline number + sparkline -----------------------------------------
def variant_b(out, rows=REAL, name="B-headline-sparkline"):
    img, d = frame()
    days = calendar_days(rows, 14)
    latest = days[-1][1]
    week = sum(c for _, c, _, _ in days[-7:])

    d.text((3, 1), "TODAY", font=font(10, True), fill=0)
    d.text((3, 13), f"${latest:.0f}", font=font(34, True), fill=0)
    d.text((132, 1), "7 DAYS", font=font(10, True), fill=0)
    d.text((132, 13), f"${week:.0f}", font=font(34, True), fill=0)

    peak = max([c for _, c, _, _ in days] + [1])
    base, top = 108, 68
    step = (W - 8) / (len(days) - 1)
    pts = [(4 + i * step, base - (base - top) * (c / peak)) for i, (_, c, _, _) in enumerate(days)]
    d.line(pts, fill=0, width=2)
    for x, y in pts:
        d.rectangle([x - 1, y - 1, x + 1, y + 1], fill=0)
    d.line([4, base + 1, W - 4, base + 1], fill=0)
    d.text((3, base + 3), "14 DAYS", font=font(9), fill=0)
    save(img, out, name)


# --- C: the last active days as a list --------------------------------------
def variant_c(out, rows=REAL, name="C-active-day-list"):
    img, d = frame()
    days = rows[-5:]
    peak = max([c for _, c, _, _ in days] + [1])

    d.text((3, 0), "LAST 5 ACTIVE DAYS", font=font(10, True), fill=0)
    y = 16
    for iso, cost, _, sess in reversed(days):
        dd = date.fromisoformat(iso)
        d.text((3, y), dd.strftime("%d %b").upper(), font=font(13, True), fill=0)
        amt = f"${cost:.0f}"
        d.text((100 - d.textlength(amt, font=font(13, True)), y), amt, font=font(13, True), fill=0)
        bw = int(142 * (cost / peak))
        d.rectangle([106, y + 3, 106 + bw, y + 11], fill=0)
        y += 20
    save(img, out, name)


# --- D: numbers only, no chart ----------------------------------------------
def variant_d(out, rows=REAL, name="D-numbers-only"):
    img, d = frame()
    d7 = sum(c for _, c, _, _ in calendar_days(rows, 7))
    d30 = sum(c for _, c, _, _ in calendar_days(rows, 30))
    active = [c for _, c, _, _ in rows]
    avg = sum(active) / len(active)

    d.text((3, 2), "7 DAYS", font=font(11, True), fill=0)
    d.text((3, 14), f"${d7:.0f}", font=font(34, True), fill=0)
    d.line([0, 56, W, 56], fill=0)
    d.text((3, 60), "30 DAYS", font=font(11, True), fill=0)
    d.text((3, 72), f"${d30:.0f}", font=font(26, True), fill=0)
    d.line([132, 60, 132, 116], fill=0)
    d.text((140, 60), "PER ACTIVE DAY", font=font(9, True), fill=0)
    d.text((140, 70), f"${avg:.0f}", font=font(22, True), fill=0)
    d.text((140, 96), f"{len(rows)} ACTIVE DAYS", font=font(9), fill=0)
    save(img, out, name)


# --- the empty frame, per variant idiom -------------------------------------
def empty_frame(out, name="EMPTY-no-readings"):
    img, d = frame()
    t = "NO HISTORY YET"
    d.text(((W - d.textlength(t, font=font(20, True))) / 2, 34), t, font=font(20, True), fill=0)
    t2 = "nothing pushed to this Pi"
    d.text(((W - d.textlength(t2, font=font(11))) / 2, 62), t2, font=font(11), fill=0)
    save(img, out, name)


def main(out):
    variant_a(out)
    variant_b(out)
    variant_c(out)
    variant_d(out)
    # The same four, drawn from what the Pi ACTUALLY holds today (4 days,
    # coverage_start 2026-09-04) rather than the full archive.
    variant_a(out, PI_HELD, "A-7day-bars-as-pi-holds-it")
    variant_c(out, PI_HELD, "C-active-day-list-as-pi-holds-it")
    empty_frame(out)
    contact_sheet(out)
    print(f"wrote mocks to {out}/")



def contact_sheet(out):
    """All four candidates plus the empty frame on one image, 2x, labelled --
    the thing to actually look at when choosing."""
    names = [
        ("A-7day-bars", "A  seven calendar days, bars"),
        ("B-headline-sparkline", "B  headline numbers + 14d sparkline"),
        ("C-active-day-list", "C  last 5 ACTIVE days, list"),
        ("D-numbers-only", "D  numbers only, no chart"),
        ("EMPTY-no-readings", "the empty frame (all variants)"),
    ]
    scale, pad, label_h = 2, 16, 22
    cw, ch = W * scale, H * scale
    sheet = Image.new("L", (cw + pad * 2, (ch + label_h + pad) * len(names) + pad), 255)
    d = ImageDraw.Draw(sheet)
    y = pad
    for fn, label in names:
        d.text((pad, y), label, font=font(14, True), fill=0)
        y += label_h
        tile = Image.open(f"{out}/{fn}.png").convert("L").resize((cw, ch), Image.NEAREST)
        sheet.paste(tile, (pad, y))
        d.rectangle([pad, y, pad + cw, y + ch], outline=128)
        y += ch + pad
    sheet.save(f"{out}/CONTACT-SHEET.png")
    print(f"wrote {out}/CONTACT-SHEET.png")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
