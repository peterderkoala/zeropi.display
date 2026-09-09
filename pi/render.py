"""pi/render.py: frame builders and the panel driver (docs/spec-eink-rendering.md).

Imported by receive.py, not merged into it: the GPIO-claiming import needs
exactly one door (spec §7), and this module's DB-only functions must stay
importable with no bluezero, no panel and no SPI, same as receive.py itself.

Only the Historic View's data layer (spec §6, ticket #62) lives here so far.
Frame builders (§5, ticket #60) and the panel context manager / worker (§7,
§8, ticket #61) land in this same module from separate tickets.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFont

PANEL_W, PANEL_H = 250, 122

# Apt-installed (fonts-dejavu-core), not vendored (spec §3). The Pi has no
# other fonts, and the 13px floor in §4 was verified on glass with DejaVu
# specifically -- a different typeface invalidates that evidence.
FONT_DIR = "/usr/share/fonts/truetype/dejavu"


@dataclass(frozen=True)
class HistoricRow:
    date: str
    total_cost: float
    incomplete: bool


def historic_rows(conn: sqlite3.Connection, limit: int = 5) -> List[HistoricRow]:
    """The `limit` most recent Active Days, newest first (spec §6).

    An Active Day is any date with at least one `readings` row -- not
    `cost > 0` (an unpriced model yields real tokens at $0, and the Desktop
    already drops all-zero rows, so a row existing means usage happened).
    A day is incomplete iff *any* Reading that day has `cost_complete = 0`.
    """
    rows = conn.execute(
        """
        SELECT date, SUM(cost_usd), MIN(cost_complete)
        FROM readings
        GROUP BY date
        ORDER BY date DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        HistoricRow(date=date, total_cost=total_cost, incomplete=(min_complete == 0))
        for date, total_cost, min_complete in rows
    ]


def historic_average(conn: sqlite3.Connection) -> Optional[float]:
    """The mean daily total across *every* Active Day the Pi holds -- not
    just the rows `historic_rows` returns (spec §6: pairing a since-date
    with an average over a different window is quietly wrong).

    `None` when there are no Active Days at all.
    """
    row = conn.execute(
        """
        SELECT AVG(daily_total) FROM (
            SELECT SUM(cost_usd) AS daily_total
            FROM readings
            GROUP BY date
        )
        """
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def coverage_start(conn: sqlite3.Connection) -> Optional[str]:
    """`meta['coverage_start']`, verbatim (spec §6). `None` if never set."""
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'coverage_start'"
    ).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Frame builders (spec §5, ticket #60). Pure functions from state to a
# 250x122 mode-"1" PIL.Image -- no I/O, no driver, no clock. Ported from the
# reference renderers a human approved on glass (#57):
# prototype/historic-view's render_settled() and prototype/gauge-glass-fix's
# gauge_settled.py. Do not redesign the geometry; it is pixel-precise on
# purpose.
#
# ⚠ Built directly in mode "1": PIL takes FreeType's FT_LOAD_TARGET_MONO path
# for mode "1", a different rasterisation than greyscale-then-convert (§4).
# ---------------------------------------------------------------------------


@lru_cache(maxsize=None)
def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(f"{FONT_DIR}/{name}", size)


def _blank_frame():
    img = Image.new("1", (PANEL_W, PANEL_H), 1)
    return img, ImageDraw.Draw(img)


def _format_countdown(seconds: Optional[float]) -> str:
    """`<1m` under a minute, `RESETS NOW` at and past zero, else `%dh%02dm`
    or `%dm` (spec §5.3).
    """
    if seconds is None:
        return "?"
    if seconds <= 0:
        return "RESETS NOW"
    if seconds < 60:
        return "<1m"
    h, m = divmod(int(seconds) // 60, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def _bar(d: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, pct) -> None:
    """A 1px outline rectangle, filled from `x+1` by `(w-2) x pct/100`, drawn
    only when non-zero (spec §5.3).
    """
    d.rectangle([x, y, x + w, y + h], outline=0)
    fill = int((w - 2) * (pct or 0) / 100)
    if fill:
        d.rectangle([x + 1, y + 1, x + 1 + fill, y + h - 1], fill=0)


def historic_frame(rows: List[HistoricRow], coverage_start: Optional[str], average: Optional[float]) -> Image.Image:
    """The Historic View (spec §5.1): the most recent Active Days, newest
    first, five rows at pitch 20px starting at y=1. `rows`, `coverage_start`
    and `average` come straight from this module's data-layer functions.
    """
    img, d = _blank_frame()

    peak = max([r.total_cost for r in rows] + [1])
    y = 1
    for row in rows:
        label = date.fromisoformat(row.date).strftime("%d %b").upper()
        d.text((3, y), label, font=_font(14, True), fill=0)

        amt = ("~" if row.incomplete else "") + f"${row.total_cost:.0f}"
        d.text((104 - d.textlength(amt, font=_font(14, True)), y), amt, font=_font(14, True), fill=0)

        # 1px minimum so a non-zero day is never invisible (spec §5.1) --
        # but a literal $0 Active Day (an unpriced model, spec §6) draws no
        # bar at all rather than being indistinguishable from a tiny cost.
        if row.total_cost > 0:
            bar_w = max(int(138 * (row.total_cost / peak)), 1)
            d.rectangle([110, y + 4, 110 + bar_w, y + 12], fill=0)
        y += 20

    d.line([0, 101, PANEL_W, 101], fill=0)

    since_label = date.fromisoformat(coverage_start).strftime("%d %b").upper() if coverage_start else "?"
    d.text((3, 106), f"SINCE {since_label}", font=_font(12), fill=0)
    avg_label = f"AVG ${average:.0f}" if average is not None else "AVG $0"
    d.text((PANEL_W - 3 - d.textlength(avg_label, font=_font(12)), 106), avg_label, font=_font(12), fill=0)

    return img


def empty_frame() -> Image.Image:
    """No Readings at all -- a fresh or just-wiped Pi (spec §5.2)."""
    img, d = _blank_frame()

    title = "NO HISTORY YET"
    d.text(((PANEL_W - d.textlength(title, font=_font(20, True))) / 2, 34), title, font=_font(20, True), fill=0)

    subtitle = "nothing pushed to this Pi"
    d.text(((PANEL_W - d.textlength(subtitle, font=_font(13))) / 2, 62), subtitle, font=_font(13), fill=0)

    return img


def gauge_frame(five_pct, five_resets_in_s, seven_pct, seven_resets_in_s) -> Image.Image:
    """The Gauge frame, Layout C as corrected on glass by #57 (spec §5.3)."""
    img, d = _blank_frame()

    d.text((3, 1), "5H", font=_font(10, True), fill=0)
    d.text((3, 8), f"{five_pct}%", font=_font(28, True), fill=0)
    d.line([(126, 0), (126, 36)], fill=0)

    countdown = _format_countdown(five_resets_in_s)
    if countdown == "RESETS NOW":
        # No "RESETS IN" label: it is not resetting *in* anything any more.
        d.text((132, 13), "RESETS NOW", font=_font(15, True), fill=0)
    else:
        d.text((132, 1), "RESETS IN", font=_font(10, True), fill=0)
        d.text((132, 8), countdown, font=_font(24, True), fill=0)
    _bar(d, 3, 42, 244, 11, five_pct)

    d.text((3, 60), "7D", font=_font(11, True), fill=0)
    d.text((25, 58), f"{seven_pct}%", font=_font(13, True), fill=0)
    d.text((66, 61), f"resets {_format_countdown(seven_resets_in_s)}", font=_font(10), fill=0)
    _bar(d, 3, 76, 244, 11, seven_pct)

    # The freed third row and the footer row stay white (spec §5.3): the
    # Gauge frame is *now*, the Historic View is *then*.
    return img


def no_usage_data_frame() -> Image.Image:
    """Null `used_percentage` (spec §5.4). No split rule: with no snapshot
    there is no countdown to divide the row for.
    """
    img, d = _blank_frame()
    d.text((3, 1), "5H", font=_font(10, True), fill=0)
    d.text((3, 10), "NO USAGE DATA", font=_font(24, True), fill=0)
    d.text((3, 44), "waiting for first snapshot", font=_font(13), fill=0)
    return img
