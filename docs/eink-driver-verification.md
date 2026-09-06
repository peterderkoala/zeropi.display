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

- ~~**`install-pi.sh`'s panel steps have not been run**~~ **Verified 2026-09-06
  by [#40](https://github.com/peterderkoala/zeropi.display/issues/40) — see
  below.**
- **The `PWR_PIN` caveat stands.** `epdconfig.module_init()` drives BCM 18
  unconditionally. Whether this HAT wires it is still unknown; nothing here
  distinguishes "power gating worked" from "there is no power gate". The
  vendor's don't-leave-it-powered rule therefore rests on `epd.sleep()` alone.

  **A review after this run found that it did not.** `epd.init()` sat outside
  the `try`, so a failure inside it — where the panel is already powered, and
  where three `ReadBusy()` spins can be interrupted — would have skipped
  `sleep()` entirely. Fixed in the follow-up commit; the measurements above
  are unaffected, since that run took the happy path throughout. **Still
  unconfirmed after #40** — the bench check (does BCM 18 actually drop) was
  skipped there too; see below.
- ~~**Nothing was read off the glass by a human.**~~ **Done in #40**: the
  border and all eight alternating blocks were confirmed clean, nothing
  clipped or stuck.
- **No rendering is wired into the BLE path.** `receive.py` does not import
  the driver.

## Provisioning verification (2026-09-06, #40)

Where the run above deliberately bypassed `install-pi.sh` (a parallel session
held the Pi), this closes that gap: the panel steps added to `install-pi.sh`
by this same effort (#39) had **never executed** until now. Verified the way
[#11](https://github.com/peterderkoala/zeropi.display/issues/11) and
[#35](https://github.com/peterderkoala/zeropi.display/issues/35) verified the
BLE path — tear down, then run the **documented one-liner**, not a script in
a working copy.

**Result: pass.** All four apt packages, SPI persistence, deployment, and the
self-test from its installed location all worked with no manual steps beyond
the documented `curl ... | bash -s -- pi` and a reboot.

**Teardown**: `python3-spidev`, `python3-gpiozero`, `python3-lgpio` and
`python3-pil` were removed first — three of the four had survived from #39's
scratch-directory bench run and would otherwise have made "the apt block
works" untested. SPI was already off (`/dev/spidev0.0` absent, `config.txt`
unmodified) from the same prior state, so no separate reset was needed there.

**Run**: `curl -fsSL https://raw.githubusercontent.com/peterderkoala/zeropi.display/dev/install.sh | bash -s -- pi`
resolved `dev` to `4363498192c61f21552fada3304b9e0409883101` (the commit that
merged the e-ink driver branch), installed all four packages fresh, reported
SPI "not present yet -- reboot needed" as designed, deployed
`waveshare_epd/` and `epd-selftest.py` alongside `receive.py`, and passed its
own self-check (e-ink stack importable, LE advertisement active) with no
FAIL lines.

**SPI persistence**: `sudo reboot`, reconnected, `/dev/spidev0.0` was present
with **no manual step** — `raspi-config nonint do_spi 0`'s `config.txt`
change survived the reboot as designed.

**Self-test from the installed location** (not the disposable `epd-bench`
copy), `/opt/zeropi-display/venv/bin/python /opt/zeropi-display/epd-selftest.py`:

```
panel is 122x250 portrait
busy reads 1 before init (a real panel asserts 1)
init()             0.06s
busy reads 0 after init
Clear(white)       2.29s
framebuffer is 4000 bytes (expect 4000)
display(frame)     2.29s
drew the test frame in 6.7s; panel asleep
```

Matches the first run's numbers almost exactly (0.06s vs. 0.05s `init()`,
identical 2.29s refresh and 4000-byte framebuffer, 6.7s vs. 6.7s
end-to-end) — the same discriminating evidence (BUSY 1→0, multi-second
refresh, not a near-zero one) rules out a false pass from an absent panel.

**The glass, finally looked at by a human**: border and all eight
alternating blocks displayed clean, nothing clipped at the edges or stuck
from a prior frame.

**Still open**: `PWR_PIN` (BCM 18) wiring was not checked during this run
either — no multimeter/LED handy at the time. Whether this ex-pwnagotchi HAT
actually wires it remains unknown since #23; `epd.sleep()` is still the only
thing standing between a happy-path run and leaving the panel powered.
