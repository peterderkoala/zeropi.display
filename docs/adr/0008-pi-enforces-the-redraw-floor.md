# The Pi enforces the redraw floor, and redraws on its own clock

The panel tolerates one update per 180 s over its rated life; the operating
point is **300 s**. The Pi enforces that floor as a **hard gate** — it will
coalesce or drop a redraw that arrives too soon, and reports which in the Ack.
The Desktop also throttles its pushes, but as a courtesy that saves BLE and
battery, never as the guarantee.

The Pi likewise redraws **on its own clock as well as on Payload arrival**,
whichever comes first. The display shows time-until-reset, which changes every
minute with no usage change at all, and the Pi already holds `resets_at` and
performs the staleness comparison from
[ADR-0002](./0002-readings-persisted-on-pi.md)'s successor decisions. A
push-driven-only panel would either freeze its countdown or force pointless
pushes purely to animate it.

This sits in tension with the Pi being a **dumb receiver** (`CONTEXT.md`), and
the tension is deliberate. Dumb means the Pi does not *fetch or compute data*
— it still does not. A duty cycle on its own panel is device care, not data
work, and it is the only side that knows when the panel last physically moved.

## Consequences

The Pi becomes the sole thing standing between a buggy, duplicated, or
misconfigured Desktop and hardware the vendor says "cannot be repaired". That
is the point: a floor enforced only by the pusher is not a floor, because
nothing enforces the pusher.

The Ack gains a **drawn-or-coalesced** field. Without it a Desktop cannot
distinguish "the Pi is showing my value" from "the Pi accepted my value and
threw it away", which are different states for anything that later reasons
about what the panel shows.

The Pi now depends on knowing what time it is — for the countdown and for
staleness — and **that dependency is not yet satisfied**. The Pi Zero 2W has
no RTC and `pi/install.sh` configures no time source, so a Pi with a wrong
clock would dim a perfectly fresh reading and render a nonsense countdown,
silently. Tracked as its own decision; a PiSugar 3's RTC is one answer, and
deriving time from the Payload (which always arrives from a machine with a
good clock) is another.

## Considered Options

- **The Desktop throttles; the Pi accepts everything.** The reading that keeps
  the Pi maximally dumb. Rejected: it makes panel longevity depend on software
  running on a different machine, where a restart loop or a second Desktop
  costs hardware that cannot be replaced from stock.
- **Both enforce, with the Desktop authoritative.** Rejected as the same
  failure with extra code — if the Pi is checking anyway, the Pi's check is
  the real one.
- **Redraw only on Payload arrival**, keeping every redraw traceable to a
  push. Rejected: the countdown then needs a push per minute, which is twelve
  times the floor and inverts the whole cadence design.
