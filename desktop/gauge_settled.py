"""The gauge as #38 settled it, at exactly 250x122, 1-bit.

Not a prototype: gauge_prototype.py holds #26's live reader and its three
competing layouts. This renders only the *decided* design, with synthetic
values, so the decisions can be looked at before they go into #20's spec.

    .venv/bin/python desktop/gauge_settled.py docs/research/gauge-mocks

#38's decisions, visible here:
  - layout C confirmed, split headline, both numbers at full size
  - the CTX row is GONE (context dropped from the display, kept on the wire)
  - the freed row stays white -- deliberately, not filled
  - the footer is GONE (freshness is guaranteed <300s by the expiry rule,
    and the model name was only explaining the CTX denominator)
  - the countdown clamps: "<1m" under a minute, then "RESETS NOW"
  - there is NO stale frame: an expired gauge is not drawn at all
  - a null five_hour reads "NO USAGE DATA", never "None%"
"""
import sys
from PIL import Image, ImageDraw, ImageFont

PANEL_W, PANEL_H = 250, 122
FONT_DIR = "/usr/share/fonts/truetype/dejavu"


def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(f"{FONT_DIR}/{name}", size)


def fmt_countdown(seconds):
    """#38: clamps at zero. '<1m' under a minute, 'RESETS NOW' at or past it."""
    if seconds is None:
        return "?"
    if seconds <= 0:
        return "RESETS NOW"
    if seconds < 60:
        return "<1m"
    h, m = divmod(int(seconds) // 60, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def frame():
    img = Image.new("1", (PANEL_W, PANEL_H), 1)
    return img, ImageDraw.Draw(img)


def bar(d, x, y, w, h, pct):
    d.rectangle([x, y, x + w, y + h], outline=0)
    fill = int((w - 2) * (pct or 0) / 100)
    if fill:
        d.rectangle([x + 1, y + 1, x + 1 + fill, y + h - 1], fill=0)


def render_gauge(path, five_pct, five_left_s, seven_pct, seven_left_s):
    img, d = frame()

    # Headline row: 5H percentage | RESETS IN countdown, split by a rule.
    d.text((3, 1), "5H", font=font(10, True), fill=0)
    d.text((3, 8), f"{five_pct}%", font=font(28, True), fill=0)
    d.line([(126, 0), (126, 46)], fill=0)
    cd = fmt_countdown(five_left_s)
    if cd == "RESETS NOW":
        # No "RESETS IN" label: it is not resetting *in* anything any more.
        # The cell says the state, centred in the space the countdown had.
        d.text((132, 13), "RESETS NOW", font=font(15, True), fill=0)
    else:
        d.text((132, 1), "RESETS IN", font=font(10, True), fill=0)
        d.text((132, 8), cd, font=font(24, True), fill=0)
    bar(d, 3, 40, 244, 8, five_pct)

    # 7D row.
    d.text((3, 56), "7D", font=font(11, True), fill=0)
    d.text((25, 54), f"{seven_pct}%", font=font(13, True), fill=0)
    d.text((58, 57), f"resets {fmt_countdown(seven_left_s)}", font=font(10), fill=0)
    bar(d, 3, 72, 244, 8, seven_pct)

    # The freed third row and the footer row stay white. Deliberate (#38 Q8/Q9).
    return img


def render_no_data(path):
    """No split headline: when the snapshot is absent, resets_at is null too,
    so there is no countdown to divide the row for. The second line is the
    point of this state -- it sends you at claude-hud's config, not the link.
    """
    img, d = frame()
    d.text((3, 1), "5H", font=font(10, True), fill=0)
    d.text((3, 10), "NO USAGE DATA", font=font(24, True), fill=0)
    d.text((3, 44), "waiting for first snapshot", font=font(11), fill=0)
    return img


def save(img, path):
    img.save(path)
    img.resize((PANEL_W * 3, PANEL_H * 3), Image.NEAREST).save(
        path.replace(".png", "-3x.png"))
    print(f"wrote {path} (+3x)")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    save(render_gauge(None, 32, 2 * 3600 + 32 * 60, 26, 135 * 3600 + 52 * 60),
         f"{out}/settled-gauge.png")
    save(render_gauge(None, 97, 30, 26, 135 * 3600 + 52 * 60),
         f"{out}/settled-under-1m.png")
    save(render_gauge(None, 99, 0, 26, 135 * 3600 + 52 * 60),
         f"{out}/settled-resets-now.png")
    save(render_no_data(None), f"{out}/settled-no-usage-data.png")
