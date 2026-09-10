# Every gauge update is a full refresh; there is no two-speed scheme

The Waveshare 2.13" V4 supports partial refresh (0.42 s, no flicker) as well
as full (3 s, flickers), which invites the obvious design: update the live
gauge on fast partial cycles and spend a full refresh occasionally to clear
ghosting. **We do not.** Every update the Pi performs is a full refresh, and
every update cycle ends in `epd.sleep()`.

Two vendor statements, each individually explicit, combine to rule the scheme
out:

- The panel must not be left powered in a non-sleep state — "the screen will
  remain in a high voltage state for a long time, which will damage the
  e-Paper and cannot be repaired". So a cycle has to end in deep sleep.
- Deep sleep does not retain RAM (`Ram data not retain` in the DC
  characteristics table), which **destroys the partial-refresh base image**.
  Waking forces a full refresh regardless.

So partial refresh only pays off across a burst the panel stays awake for.
With a 300 s redraw floor ([ADR-0008](./0008-pi-enforces-the-redraw-floor.md))
**there is never a burst** — every update is separated by minutes of sleep,
and each one would wake to a discarded base image anyway.

## Consequences

The spec's hard bound of **N = 5 consecutive partial-or-fast updates before a
forced full refresh never binds**, and the unbounded-ghosting failure mode
closes with it — the one the vendor warns "cannot be repaired". The panel is
powered down roughly 99.9% of the time, which also means a future battery
decision (a PiSugar 3 is the likely candidate) will be fighting the Pi Zero's
idle draw, not the panel.

The cost is real and visible: a ~3 s flicker every five minutes whenever the
gauge is live. **If that proves obnoxious on the bench, the fix is a slower
cadence, not partial refresh** — reopening partial refresh means reopening the
high-voltage constraint, which is a hardware-damage risk rather than a taste
one.

Sub-region partial refresh would not have helped even if the above did not
apply: the shipped V4 driver hard-codes the refresh window to the full panel
and transmits the whole 4000-byte frame regardless, so "a fast gauge region
and a slow history region" is a property of what you draw, never of what you
transmit.

## Considered Options

- **Partial for the gauge, full every 5th update.** The obvious scheme, and
  what Waveshare's own demo does — while violating its own N = 5 bound by
  doing 10. Rejected: it requires staying awake between updates, which the
  high-voltage precaution forbids at a multi-minute cadence.
- **Stay awake across a burst, sleep at the end.** Sound in principle, and the
  right answer for a display that updates in clusters. Rejected because this
  display does not: the 300 s floor spreads updates evenly by construction, so
  there is no burst to amortise a wake over.
- **Partial refresh with a shorter floor**, buying back a genuine burst.
  Rejected: the floor exists to spread the panel's rated 1,000,000 cycles over
  its rated 5 years, and this panel is second-hand off a pwnagotchi with
  unquantified prior wear. Trading panel life for less flicker is the wrong
  direction on hardware that cannot be replaced from stock.
