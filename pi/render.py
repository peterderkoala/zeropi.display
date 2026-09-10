"""pi/render.py: frame builders and the panel driver (docs/spec-eink-rendering.md).

Imported by receive.py, not merged into it: the GPIO-claiming import needs
exactly one door (spec §7), and this module's DB-only functions must stay
importable with no bluezero, no panel and no SPI, same as receive.py itself.

The Historic View's data layer (spec §6, ticket #62), the frame builders
(§5, ticket #60), and the panel context manager, worker and failure
handling (§7, §8, ticket #61) all live here.
"""

import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
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

# The worker abandons a stuck refresh after this long (spec §8): ReadBusy()
# is an unbounded loop with no timeout, pinned upstream, so a stuck panel
# cannot be interrupted. This only stops us from caring about that one
# thread -- it does not, and cannot, recover it.
WATCHDOG_TIMEOUT_S = 30.0

logger = logging.getLogger("zeropi.render")


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


# ---------------------------------------------------------------------------
# The panel context manager (spec §7.2) -- the ONLY thing that touches the
# driver. `init()` sits *inside* the guarded region: `module_init()` raises
# the power pin and opens SPI as its first act, so the panel is live from
# that instant, and `reset()`, three `ReadBusy()` spins and ~10 commands all
# run before `init()` returns. Putting `init()` outside the try would leave
# exactly that window uncovered. Every cycle ends in `sleep()`
# unconditionally, so ADR-0007's rule is structural here, not remembered --
# a review already caught this exact ordering mistake once in the self-test
# (ca68517).
# ---------------------------------------------------------------------------


@contextmanager
def panel(epd_factory=None):
    """`epd_factory` is injectable so tests never import the real driver and
    never claim GPIO. Production leaves it default, which imports
    waveshare_epd here -- not at module scope -- because merely importing
    it claims GPIO as a side effect (pi/waveshare_epd/README.md), and
    receive.py must stay importable with no panel, no SPI and no bluezero.
    """
    if epd_factory is None:
        from waveshare_epd import epd2in13_V4

        epd_factory = epd2in13_V4.EPD

    epd = epd_factory()
    try:
        if epd.init() != 0:
            raise RuntimeError("epd.init() failed")
        yield epd
    finally:
        try:
            epd.sleep()
        except Exception:
            # Never let cleanup mask the real error -- the caller's
            # exception (if any) is what needs to surface, and a panel
            # that will not sleep is worth logging, not raising over it.
            logger.exception("epd.sleep() failed during cleanup")


def _draw_via_panel(image: Image.Image) -> None:
    with panel() as epd:
        epd.display(epd.getbuffer(image))


# ---------------------------------------------------------------------------
# The worker (spec §7.3, §8): one thread, a one-slot hand-off -- newest
# state wins, matching what the redraw floor already enforces upstream.
# Never called inline in the write handler, never on a post-Ack bluezero
# timer: a 4.35s inline refresh leaves ~0.5s of margin against BlueZ's ~5s
# write timeout, a limit we do not control (bench/render-blocking, #56).
#
# `_WorkerState` is the pure decision logic -- hand-off, failure
# transitions, the watchdog -- exercised directly with an injected clock,
# no threads, no real waiting, the same way GaugeGate/RedrawGate already
# are. `PanelWorker` is the thin real-thread wrapper around it.
# ---------------------------------------------------------------------------


class _WorkerState:
    def __init__(self, watchdog_timeout_s: float = WATCHDOG_TIMEOUT_S):
        self.watchdog_timeout_s = watchdog_timeout_s
        self.pending = None
        self.job_started_at: Optional[float] = None
        self.unavailable = False

    def submit(self, image) -> None:
        """A frame arriving mid-refresh replaces the pending slot -- newest
        wins, never queued (spec §8)."""
        self.pending = image

    def take_job(self, now: float):
        """Claim the pending frame, if any, marking a job in flight."""
        if self.pending is None:
            return None
        image, self.pending = self.pending, None
        self.job_started_at = now
        return image

    def on_success(self) -> None:
        self.job_started_at = None
        self.unavailable = False

    def on_failure(self) -> bool:
        """Returns True exactly on the transition into `unavailable`, so
        the caller logs once per transition, not per attempt (spec §8)."""
        self.job_started_at = None
        became_unavailable = not self.unavailable
        self.unavailable = True
        return became_unavailable

    def watchdog_fired(self, now: float) -> bool:
        """True exactly once per stuck episode: a job has been in flight
        for at least `watchdog_timeout_s` and this episode has not already
        been flagged. A stuck thread cannot be recovered (ReadBusy is
        unbounded and pinned upstream) -- this only stops us re-logging it
        on every subsequent poll.
        """
        if self.job_started_at is None or self.unavailable:
            return False
        if now - self.job_started_at < self.watchdog_timeout_s:
            return False
        self.unavailable = True
        return True


class PanelWorker:
    """One render worker thread with a one-slot hand-off (spec §8).

    `draw_fn(image)` defaults to drawing through `panel()`; tests inject a
    fake to avoid touching hardware. `now_fn` is the clock `_WorkerState`
    uses for job timestamps and the watchdog -- inject a fake to test the
    watchdog without a real 30s wait.
    """

    def __init__(
        self,
        draw_fn=None,
        now_fn=time.monotonic,
        watchdog_timeout_s: float = WATCHDOG_TIMEOUT_S,
    ):
        self._draw_fn = draw_fn if draw_fn is not None else _draw_via_panel
        self._now = now_fn
        self._state = _WorkerState(watchdog_timeout_s)
        self._lock = threading.Lock()
        self._wakeup = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, image) -> None:
        with self._lock:
            self._state.submit(image)
        self._wakeup.set()

    def _run(self) -> None:
        while True:
            self._wakeup.wait()
            with self._lock:
                image = self._state.take_job(self._now())
                self._wakeup.clear()
            if image is None:
                continue
            try:
                self._draw_fn(image)
            except Exception:
                with self._lock:
                    became_unavailable = self._state.on_failure()
                if became_unavailable:
                    logger.exception(
                        "panel render failed; marking the panel unavailable "
                        "until the next successful redraw"
                    )
                continue
            with self._lock:
                self._state.on_success()

    def check_watchdog(self) -> None:
        """Call periodically (spec §8: ~30s) from wherever already runs on
        the BLE process's own clock -- NOT from a dedicated thread, so a
        stuck ReadBusy never costs more than the one thread it already
        owns. Logs once per stuck episode; never touches the stuck thread.
        The link outlives the panel: this only stops us caring about it,
        it does not and cannot free it.
        """
        with self._lock:
            fired = self._state.watchdog_fired(self._now())
            timeout = self._state.watchdog_timeout_s
        if fired:
            logger.error(
                "panel worker stuck for >= %.0fs (unbounded ReadBusy, "
                "pinned upstream); abandoning this refresh, BLE keeps serving",
                timeout,
            )

    @property
    def unavailable(self) -> bool:
        with self._lock:
            return self._state.unavailable
