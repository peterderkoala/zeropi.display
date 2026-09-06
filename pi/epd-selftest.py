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
and cannot be repaired". That is why sleep() runs from a finally block --
a traceback halfway through a draw must not leave the panel hot.
"""

import argparse
import logging
import socket
import sys
import time
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFont

from waveshare_epd import epd2in13_V4

# The panel is 122x250 portrait; this HAT is read in landscape, and
# getbuffer() rotates a correctly-sized landscape image for us.
LANDSCAPE = (epd2in13_V4.EPD_HEIGHT, epd2in13_V4.EPD_WIDTH)

logger = logging.getLogger("epd-selftest")


def _font(size):
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

    started = time.monotonic()
    epd.init()
    try:
        epd.Clear(0xFF)
        if not args.clear:
            epd.display(epd.getbuffer(build_frame()))
    finally:
        # Unconditional: see the module docstring. A failed draw still has to
        # put the panel down.
        epd.sleep()

    logger.info(
        "%s in %.1fs; panel asleep",
        "cleared" if args.clear else "drew the test frame",
        time.monotonic() - started,
    )


if __name__ == "__main__":
    sys.exit(main())
