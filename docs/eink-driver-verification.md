# E-ink driver verification (2026-09-06)

First hardware run of the Waveshare 2.13" V4 panel: the vendored driver in
`pi/waveshare_epd/` and the bench check `pi/epd-selftest.py`.

**Result: pass. The panel is wired, responds, and draws.** Two questions that
were open in the research for
[#23](https://github.com/peterderkoala/zeropi.display/issues/23) are now
answered on real glass — the ex-pwnagotchi HAT does respond, and a full
refresh costs **2.29 s**, not the ~3 s the vendor material implies.

## Target and method

The dev Pi (`192.168.4.108`, `pidisplay`, Pi Zero 2 W Rev 1.0, Debian 13
trixie, Python 3.13.5), with the panel HAT attached.

**This run deliberately did not use `pi/install-pi.sh`.** A parallel session
was mid-teardown on the same Pi at the time, so provisioning would have
collided with it. Everything below was done out of a scratch directory
(`/home/pi/epd-bench`) with the least persistent footprint that still proves
the driver:

- `python3-spidev`, `python3-gpiozero` and `python3-lgpio` were **already
  present** on the stock image. Only `python3-pil` (11.1.0-5+deb13u4) had to
  be installed.
- SPI was enabled **at runtime** with `sudo dtparam spi=on`, which brings up
  `/dev/spidev0.0` and `/dev/spidev0.1` immediately and **does not survive a
  reboot**. `install-pi.sh` persists it properly (via `raspi-config nonint
  do_spi 0`); that persistence path is therefore **not yet verified**.

So this verifies **the driver**, not the provisioning of the driver. The
latter still needs a run of `install-pi.sh` on a quiet Pi.

## Proving the panel is real, not absent

This needed care, because the obvious pass is also what an *absent* panel
looks like. `ReadBusy()` spins while BUSY is high:

```python
while(epdconfig.digital_read(self.busy_pin) == 1):
    epdconfig.delay_ms(10)
```

BUSY is BCM 24, configured `gpiozero.Button(pull_up=False)`. With no HAT
attached the pin reads 0, `ReadBusy()` returns instantly, and every call
"succeeds" in roughly zero time. **A missing panel fails fast rather than
hanging**, so "the script exited 0" proves nothing on its own.

Two independent signals rule that out:

| Signal | Absent panel | Measured |
|---|---|---|
| BUSY before `init()` | 0 (floating) | **1** |
| BUSY after `init()` | 0 | 0 |
| Full refresh duration | ~0.00 s | **2.28–2.29 s, three times running** |

BUSY reading 1 and then 0 means something is actively driving the line.

## Measurements

These came from an ad-hoc probe script during the run. **`epd-selftest.py`
now prints the same evidence itself** — BUSY before and after `init()`, and
each refresh timed — so the table below can be re-derived by anyone with the
HAT, rather than taken on trust from a script that no longer exists.

```
busy pin reads 1 before init (0 = idle)
init()                           0.05s
busy pin reads 0 after init
Clear(white)                     2.28s
Clear(black)                     2.29s
getbuffer()                      0.00s
framebuffer is 4000 bytes
display(frame)                   2.29s
sleep()                          2.00s
```

- **Full refresh: 2.29 s**, consistent across white, black and a real frame.
  [ADR-0007](./adr/0007-full-refresh-only-no-two-speed.md) budgets ~3 s; the
  panel is slightly faster than assumed, which only makes that ADR safer.
- **Framebuffer: exactly 4000 bytes.** The panel is 122 px wide, which is not
  a multiple of 8, and PIL pads each row to 16 bytes — 16 × 250 = 4000. This
  matches the driver's own `SetWindow` handling.
- `sleep()`'s 2.00 s is the driver's own fixed `delay_ms(2000)`, not panel
  time.
- End-to-end `epd-selftest.py`: **6.7 s** (init + Clear + display + sleep),
  exit 0, twice.

## What is still unproven

- **`install-pi.sh`'s panel steps have not been run** — SPI persistence, the
  apt block and deployment to `/opt/zeropi-display`. Only the driver they
  deploy has been exercised.
- **The `PWR_PIN` caveat stands.** `epdconfig.module_init()` drives BCM 18
  unconditionally. Whether this HAT wires it is still unknown; nothing here
  distinguishes "power gating worked" from "there is no power gate". The
  vendor's don't-leave-it-powered rule therefore rests on `epd.sleep()` alone.

  **A review after this run found that it did not.** `epd.init()` sat outside
  the `try`, so a failure inside it — where the panel is already powered, and
  where three `ReadBusy()` spins can be interrupted — would have skipped
  `sleep()` entirely. Fixed in the follow-up commit; the measurements above
  are unaffected, since that run took the happy path throughout.
- **Nothing was read off the glass by a human.** The panel was left showing
  the self-test frame (hostname, UTC timestamp, a border and eight
  alternating blocks); confirming the border is unclipped and no block is
  stuck is a look-at-it check that has not happened.
- **No rendering is wired into the BLE path.** `receive.py` does not import
  the driver.
