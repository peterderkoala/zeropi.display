# Vendored Waveshare e-Paper driver

`epd2in13_V4.py`, `epdconfig.py` and `__init__.py` are copied **verbatim,
unmodified** from Waveshare's official driver repository:

- Upstream: <https://github.com/waveshareteam/e-Paper>
- Path: `RaspberryPi_JetsonNano/python/lib/waveshare_epd/`
- Pinned commit: `a794fbc39656b0f93938d1ffb3fdc77eaed9e9fc` (2026-08-19)

Each file carries Waveshare's own MIT-style permission notice in its header.

## Why vendored rather than installed

- **It is not on PyPI.** Waveshare ships a `setup.py` but never publishes it;
  the vendor's own documented install path is `git clone` and run in place.
- **`waveshare-epaper` on PyPI is not Waveshare's.** It is a third-party
  republish that declares `RPi.GPIO`, which does not work on current
  Raspberry Pi OS. Do not reach for it because it installs more easily.
- **`setup.py` is stale and lies.** It still declares `RPi.GPIO` as a
  dependency while the code imports `gpiozero`, so `pip install .` drags in a
  broken package.
- **No `git` on the Pi.** Provisioning is a curl'd tarball (see `install.sh`),
  so anything the Pi needs has to be in the tree already.

## The revision matters

`epd2in13.py` (V1), `_V2`, `_V3` and `_V4` are separate modules with different
init sequences and register semantics. This hardware is the **V4**; do not
substitute another.

## Runtime dependencies

From apt, not pip — Debian marks the system Python externally-managed, and the
`/opt/zeropi-display` venv is built with `--system-site-packages` so it sees
them: `python3-spidev`, `python3-gpiozero`, `python3-lgpio`, `python3-pil`.
`pi/install-pi.sh` installs all four. The driver itself imports only `spidev`
and `gpiozero`; `PIL` is needed by callers that build a frame.

## Two behaviours worth knowing before you import this

1. **Importing claims GPIO immediately.** `epdconfig.py` ends with
   `implementation = RaspberryPi()` at module scope, which constructs
   `gpiozero` objects for BCM 17, 25, 18 and 24 as a side effect of import.
   Import it only in a process that intends to drive the panel.
2. **`module_init()` unconditionally drives a power pin on BCM 18.** Whether
   this ex-pwnagotchi HAT wires GPIO 18 to anything is unconfirmed. If it does
   not, `module_exit()` does not actually cut power and the vendor's
   "never leave it powered" rule rests on `epd.sleep()` alone — which is why
   every code path here ends in `sleep()` (see `docs/adr/0007`).

## Updating

Re-copy from upstream at a new pinned commit and update the sha above. Do not
hand-edit these files: local edits would be silently lost on the next
re-vendor, and there is no diff to review them against.
