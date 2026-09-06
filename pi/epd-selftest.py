#!/usr/bin/env python3
"""Prove the Waveshare 2.13" V4 panel is wired, powered and drawable.

Run by hand on the Pi after provisioning:

    /opt/zeropi-display/venv/bin/python /opt/zeropi-display/epd-selftest.py
    /opt/zeropi-display/venv/bin/python /opt/zeropi-display/epd-selftest.py --clear

This is a bench tool, not part of the BLE path -- nothing in receive.py
imports it. It draws one frame and goes back to sleep.

Every cycle is a full refresh and ends in epd.sleep(), per docs/adr/0007. The
panel must not be left in a powered non-sleep state: the vendor says it "will
remain in a high voltage state for a long time, which will damage the e-Paper
and cannot be repaired". So sleep() runs from a finally block that opens
*before* init(), because init() powers the panel as its first act -- putting
it outside the try would leave the riskiest phase uncovered.

The run also prints the evidence that the panel is really there, rather than
just asserting it: BUSY before and after init, and the duration of each
refresh. An absent HAT leaves BUSY low, so ReadBusy() returns at once and the
whole script "passes" in about zero seconds. Exit status alone proves nothing.
"""

import argparse
import logging
import socket
import sys
import time
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFont

from waveshare_epd import epd2in13_V4, epdconfig

# The panel is 122x250 portrait; this HAT is read in landscape, and
# getbuffer() rotates a correctly-sized landscape image for us.
LANDSCAPE = (epd2in13_V4.EPD_HEIGHT, epd2in13_V4.EPD_WIDTH)

# BCM 24, wired as gpiozero.Button(pull_up=False) by epdconfig.
BUSY_PIN = 24

logger = logging.getLogger("epd-selftest")


def _timed(label: str, fn):
    """Run fn, reporting how long the panel took.

    The durations are evidence, not decoration: a full refresh on real glass
    costs ~2.3 s, where an absent panel returns in ~0.00 s.
    """
    started = time.monotonic()
    result = fn()
    logger.info("%-16s %6.2fs", label, time.monotonic() - started)
    return result


def _sleep_quietly(epd) -> None:
    """Put the panel down without masking whatever sent us here.

    sleep() talks SPI, so it raises in the one case that matters most: a
    failure early enough in init() that SPI was never opened. Letting that
    escape a finally block would replace the real traceback with a confusing
    secondary one, so it is logged instead -- loudly, because a panel that
    would not sleep is the state the vendor says destroys it.
    """
    try:
        epd.sleep()
    except Exception:
        logger.exception(
            "COULD NOT SLEEP THE PANEL -- it may still be powered. "
            "Unplug the HAT if this repeats."
        )


def _font(size: int):
    try:
        return ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size
        )
    except OSError:
        return ImageFont.load_default(size=size)


def build_frame():
    """A frame whose correctness is checkable by eye.

    The border proves no edge is clipped, the greyscale-ish bars prove both
    polarities reach the glass, and the timestamp proves this run drew it
    rather than a ghost of an earlier one.
    """
    image = Image.new("1", LANDSCAPE, 255)
    draw = ImageDraw.Draw(image)
    width, height = LANDSCAPE

    draw.rectangle([(0, 0), (width - 1, height - 1)], outline=0)

    draw.text((6, 4), "zeropi.display", font=_font(18), fill=0)
    draw.text(
        (6, 26),
        f"{socket.gethostname()}  {width}x{height} landscape",
        font=_font(12),
        fill=0,
    )
    draw.text(
        (6, 42),
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
        font=_font(12),
        fill=0,
    )

    # Alternating filled/empty blocks: a panel with a stuck segment or a
    # half-transmitted frame shows it here rather than in prose.
    for index in range(8):
        x0 = 6 + index * 30
        y0 = 64
        if index % 2 == 0:
            draw.rectangle([(x0, y0), (x0 + 24, y0 + 24)], fill=0)
        else:
            draw.rectangle([(x0, y0), (x0 + 24, y0 + 24)], outline=0)

    draw.text((6, 96), "self-test OK -- panel sleeping", font=_font(12), fill=0)
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--clear",
        action="store_true",
        help="blank the panel and sleep, instead of drawing the test frame",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    epd = epd2in13_V4.EPD()
    logger.info("panel is %dx%d portrait", epd.width, epd.height)

    # Read BUSY before anything touches the panel. This is the check that
    # separates a working panel from an absent one, and it has to happen
    # first: an absent HAT leaves BUSY low, ReadBusy() returns instantly, and
    # every call below "succeeds" in about zero time. A pass proves nothing
    # on its own -- see docs/eink-driver-verification.md.
    logger.info("busy reads %d before init (a real panel asserts 1)",
                epdconfig.digital_read(BUSY_PIN))

    started = time.monotonic()
    try:
        # init() is INSIDE the try, not before it. module_init() raises the
        # power pin and opens SPI as its first act, so the panel is live from
        # that instant -- and reset(), three ReadBusy() spins and ~10 commands
        # all run before init() returns. Between those two points is exactly
        # when the panel is powered and nothing has slept it yet, so it is the
        # last place that should sit outside the finally.
        if _timed("init()", epd.init) != 0:
            raise RuntimeError("epd.init() failed: module_init() returned non-zero")
        logger.info("busy reads %d after init", epdconfig.digital_read(BUSY_PIN))

        _timed("Clear(white)", lambda: epd.Clear(0xFF))
        if not args.clear:
            frame = epd.getbuffer(build_frame())
            # 122 px is not a multiple of 8, so PIL pads each row to 16 bytes:
            # 16 x 250 = 4000. A different number means the frame is not what
            # the panel expects.
            logger.info("framebuffer is %d bytes (expect 4000)", len(frame))
            _timed("display(frame)", lambda: epd.display(frame))
    finally:
        _sleep_quietly(epd)

    logger.info(
        "%s in %.1fs; panel asleep",
        "cleared" if args.clear else "drew the test frame",
        time.monotonic() - started,
    )


if __name__ == "__main__":
    sys.exit(main())
