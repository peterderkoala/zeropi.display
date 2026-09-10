"""Tests for pi/render.py's frame builders (spec §5, tested per §12, ticket #60).

Pure functions from state to an in-memory 250x122 mode "1" PIL.Image. No I/O,
no driver, no clock, no hardware. Assertions are the cheap, real ones §12
names: exact size/mode; ink in the expected row bands; `~` iff incomplete;
the peak day's bar is widest; RESETS NOW draws no RESETS IN label.
"""

from PIL import Image

import render
from render import HistoricRow

PANEL_W, PANEL_H = 250, 122


def _has_ink(img: Image.Image, x0, y0, x1, y1) -> bool:
    for y in range(y0, y1):
        for x in range(x0, x1):
            if img.getpixel((x, y)) == 0:
                return True
    return False


def _leftmost_ink_x(img: Image.Image, x0, y0, x1, y1):
    xs = [x for y in range(y0, y1) for x in range(x0, x1) if img.getpixel((x, y)) == 0]
    return min(xs) if xs else None


def _ink_width(img: Image.Image, x0, y0, x1, y1) -> int:
    xs = [x for y in range(y0, y1) for x in range(x0, x1) if img.getpixel((x, y)) == 0]
    return (max(xs) - min(xs) + 1) if xs else 0


def _row(date, total_cost, incomplete=False):
    return HistoricRow(date=date, total_cost=total_cost, incomplete=incomplete)


# ---------------------------------------------------------------------------
# Exact size and mode, all four frames
# ---------------------------------------------------------------------------


def test_historic_frame_is_exactly_250x122_mode_1():
    img = render.historic_frame([_row("2026-09-05", 10.0)], "2026-09-01", 10.0)
    assert img.size == (PANEL_W, PANEL_H)
    assert img.mode == "1"


def test_empty_frame_is_exactly_250x122_mode_1():
    img = render.empty_frame()
    assert img.size == (PANEL_W, PANEL_H)
    assert img.mode == "1"


def test_gauge_frame_is_exactly_250x122_mode_1():
    img = render.gauge_frame(32, 9000, 18, 400000)
    assert img.size == (PANEL_W, PANEL_H)
    assert img.mode == "1"


def test_no_usage_data_frame_is_exactly_250x122_mode_1():
    img = render.no_usage_data_frame()
    assert img.size == (PANEL_W, PANEL_H)
    assert img.mode == "1"


# ---------------------------------------------------------------------------
# 5.1 Historic View
# ---------------------------------------------------------------------------


def test_historic_frame_five_rows_have_ink_in_all_five_row_bands():
    rows = [_row(f"2026-09-0{i}", float(i)) for i in range(1, 6)]
    img = render.historic_frame(rows, "2026-09-01", 3.0)
    for i in range(5):
        y0, y1 = i * 20, i * 20 + 20
        assert _has_ink(img, 0, y0, PANEL_W, y1), f"row band {i} has no ink"


def test_historic_frame_four_rows_leaves_the_fifth_band_blank():
    rows = [_row(f"2026-09-0{i}", float(i)) for i in range(1, 5)]
    img = render.historic_frame(rows, "2026-09-01", 2.5)
    # The fifth row band would be y in [80, 100) -- below it is the rule
    # (y=101) and footer (y=106), which must stay out of this band.
    assert not _has_ink(img, 0, 80, PANEL_W, 100)


def test_historic_frame_tilde_appears_iff_incomplete():
    complete = render.historic_frame([_row("2026-09-05", 42.0, incomplete=False)], "2026-09-01", 42.0)
    incomplete = render.historic_frame([_row("2026-09-05", 42.0, incomplete=True)], "2026-09-01", 42.0)

    # Same cost, right-aligned to the same edge (x=104): a "~" prefix makes
    # the string wider, so its left edge sits further left than the plain
    # "$42" string's does.
    complete_left = _leftmost_ink_x(complete, 60, 0, 105, 20)
    incomplete_left = _leftmost_ink_x(incomplete, 60, 0, 105, 20)
    assert complete_left is not None and incomplete_left is not None
    assert incomplete_left < complete_left


def test_historic_frame_peak_days_bar_is_widest():
    rows = [
        _row("2026-09-03", 10.0),
        _row("2026-09-02", 100.0),  # the peak
        _row("2026-09-01", 5.0),
    ]
    img = render.historic_frame(rows, "2026-09-01", 38.33)

    widths = [
        _ink_width(img, 110, i * 20 + 4, PANEL_W, i * 20 + 13)
        for i in range(3)
    ]
    assert widths[1] == max(widths)
    assert widths[1] > widths[0]
    assert widths[1] > widths[2]


def test_historic_frame_zero_cost_day_draws_no_bar():
    # An unpriced model yields an Active Day at $0 (spec §6). The 1px
    # minimum exists "so a non-zero day is never invisible" (spec §5.1) --
    # a literal $0 day is not that case and must draw no bar at all.
    rows = [_row("2026-09-02", 0.0), _row("2026-09-01", 50.0)]
    img = render.historic_frame(rows, "2026-09-01", 25.0)
    assert not _has_ink(img, 110, 0 + 4, PANEL_W, 0 + 13)
    assert _has_ink(img, 110, 20 + 4, PANEL_W, 20 + 13)


def test_historic_frame_fewer_than_five_rows_still_draws_rule_and_footer():
    img = render.historic_frame([_row("2026-09-05", 10.0)], "2026-09-01", 10.0)
    assert _has_ink(img, 0, 101, PANEL_W, 102)  # the rule
    assert _has_ink(img, 0, 106, PANEL_W, 118)  # the footer


# ---------------------------------------------------------------------------
# 5.2 The empty frame
# ---------------------------------------------------------------------------


def test_empty_frame_has_ink_at_title_and_subtitle_bands():
    img = render.empty_frame()
    assert _has_ink(img, 0, 34, PANEL_W, 54)
    assert _has_ink(img, 0, 62, PANEL_W, 75)


# ---------------------------------------------------------------------------
# 5.3 The Gauge frame
# ---------------------------------------------------------------------------


def test_gauge_frame_draws_split_rule_and_both_bars():
    img = render.gauge_frame(32, 9000, 18, 400000)
    assert _has_ink(img, 126, 0, 127, 36)  # split rule
    assert _has_ink(img, 3, 42, 247, 53)   # 5H bar outline
    assert _has_ink(img, 3, 76, 247, 87)   # 7D bar outline


def test_gauge_frame_resets_in_shows_label_and_countdown():
    img = render.gauge_frame(32, 9000, 18, 400000)
    assert _has_ink(img, 132, 1, 200, 11)   # "RESETS IN" label band
    assert _has_ink(img, 132, 8, 250, 32)   # the countdown itself


def test_gauge_frame_resets_now_drops_the_resets_in_label():
    img = render.gauge_frame(99, 0, 18, 400000)
    # "RESETS IN" would be drawn at (132, 1) 10px bold -- must be absent.
    assert not _has_ink(img, 132, 1, 250, 7)
    # "RESETS NOW" draws at (132, 13) 15px bold instead.
    assert _has_ink(img, 132, 13, 250, 28)


# ---------------------------------------------------------------------------
# 5.4 Null Gauge
# ---------------------------------------------------------------------------


def test_no_usage_data_frame_has_no_split_rule():
    img = render.no_usage_data_frame()
    assert not _has_ink(img, 126, 0, 127, 36)


def test_no_usage_data_frame_has_ink_at_both_lines():
    img = render.no_usage_data_frame()
    assert _has_ink(img, 0, 10, PANEL_W, 34)   # "NO USAGE DATA"
    assert _has_ink(img, 0, 44, PANEL_W, 57)   # "waiting for first snapshot"


# ---------------------------------------------------------------------------
# The framebuffer (spec §2, §12): exactly 4000 bytes.
#
# Three of the four frames below reached real glass in #66 (`no_usage_data` did
# not -- it needs a null used_percentage, which real data will not produce on
# demand). The driver's `getbuffer()` cannot be imported here -- `epdconfig` claims GPIO
# as a side effect (pi/waveshare_epd/README.md) and the suite must run with no
# panel and no SPI -- so this replicates its exact path instead: a 250x122
# landscape image hits `epd2in13_V4.getbuffer`'s `imwidth == self.height`
# branch, which rotates to the panel's native 122x250 portrait and packs rows
# to whole bytes. 122 is not a multiple of 8, so PIL pads each row to 16 bytes:
# 16 x 250 = 4000. A different number means the frame is not what the panel
# expects.
# ---------------------------------------------------------------------------


def _panel_framebuffer_len(img: Image.Image) -> int:
    return len(bytearray(img.rotate(90, expand=True).convert("1").tobytes("raw")))


def test_every_frame_packs_to_exactly_4000_bytes():
    frames = {
        "historic": render.historic_frame(
            [_row(f"2026-09-0{i}", float(i)) for i in range(1, 6)], "2026-09-01", 3.0
        ),
        "empty": render.empty_frame(),
        "gauge": render.gauge_frame(32, 9000, 18, 400000),
        "no_usage_data": render.no_usage_data_frame(),
    }
    for name, img in frames.items():
        assert _panel_framebuffer_len(img) == 4000, name
