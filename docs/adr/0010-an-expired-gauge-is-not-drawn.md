# An expired Gauge is not drawn, and nothing on the panel is marked stale

[ADR-0009](./0009-pi-is-given-durations-not-timestamps.md) gives the Pi a
**Gauge Age** and a 300 s expiry. This decides what the panel does when that
threshold passes: **it stops drawing the Gauge and shows the historic view.**
There is no dimmed reading, no hatched numerals, no stale banner, and no
inverted frame.

The mechanical reason is that the panel is **1-bit monochrome — there is no
grey**. [#26](https://github.com/peterderkoala/zeropi.display/issues/26)
rendered stale and live side by side and they differed by one footer word at
the smallest size on the panel, invisible at arm's length, while every large
element — percentage, bars, countdown — was identical.

But the deciding reason is not mechanical. A marked-stale number is a number
you are asking the viewer not to trust, and a glanceable display is read in
about a second, by someone who will not find the mark. The states are already
designed:
[ADR-0007](./0007-full-refresh-only-no-two-speed.md)'s companion decision in
[#25](https://github.com/peterderkoala/zeropi.display/issues/25) settled that
idle shows the **historic view**, so "no live Gauge" already has a picture. An
expired Gauge is that state, reached a different way.

A **null** `used_percentage` is different and keeps its own frame, reading
**NO USAGE DATA** with a second line naming what is missing. Per
[#27](https://github.com/peterderkoala/zeropi.display/issues/27) this is
observed, not hypothetical — no file exists before claude-hud's first render.
It is a distinct fault from expiry and points somewhere different: null means
the Desktop is talking and has nothing to say (look at claude-hud's config),
expiry means the Desktop is gone (look at the link). Collapsing them would
make a diagnosable misconfiguration invisible.

## Consequences

**Nothing on the panel ever needs to be distrusted.** Whatever the Gauge frame
shows is under 300 s old by construction. That is what retires the freshness
footer: a stamp that can only ever say "fresh" is decoration, and in #26's
mocks it was the *only* thing distinguishing stale from live — doing safety
work it could not do.

**Falling back is a full refresh**, gated by ADR-0008's 300 s floor, so a
flapping Desktop cannot flap the panel faster than the floor allows.

**The countdown clamps rather than expiring the reading.** It reads `<1m`
under a minute and **RESETS NOW** at and past zero, held until fresh data
arrives, because a Limit Window genuinely can reset while a session is live
and the Pi crosses that boundary unaided on its monotonic clock. `0m` was
rejected as a lie for 59 of its 60 seconds, and `-1m` — what the prototype
actually rendered — as nonsense.

**The idle panel gains a 24-hour keep-alive full refresh.** #23's research
found the vendor contradicting itself: the general FAQ scopes "refresh at
least once every 24 hours" to multi-colour panels, the unscoped Precautions
section does not. One redundant refresh a day against a 300 s floor costs
nothing and settles a contradiction we cannot. This amends #25's "held
indefinitely with no further redraws".

**The context readout leaves the display but stays on the wire.** The Gauge
Payload keeps the field (maintainer's call, so a future readout needs no
protocol change), which means the spec must still define active-session
detection and the context computation exactly, for a value nothing currently
draws. The measurements that killed the readout are in
[#31](https://github.com/peterderkoala/zeropi.display/issues/31) and #26: a
1,000,000-token window, 42 real sessions with a **median peak of 15.8%** and
**none above 900K**, which left the percentage permanently in the bottom sixth
of its scale with a bar that never moved.

**The freed row stays white.** Deliberately (maintainer's call), not pending a
better idea, and specifically not filled with a historic number — the one
distinction the design rests on is that the Gauge frame is *now* and the
historic view is *then*.

## Considered Options

- **Hatch or dither the numerals** to read as grey at distance. Rejected: it
  is a guess about how a 1-bit panel resolves a fine pattern at 28px, and it
  still leaves an untrustworthy number as the largest thing on the display.
- **A stale banner** in the freed row. Rejected on the same ground, plus it
  spends the row that dropping the context readout just freed on an apology.
- **Invert the headline row** (white on black). The most *visible* option, and
  rejected reluctantly: visibility was never the real problem. It answers "can
  you tell it is stale" while leaving "why is a stale number the headline"
  unanswered.
- **Keep showing the Gauge indefinitely, unmarked.** Rejected outright: the
  percentage is a floor that only rises within a window, so a stale Gauge
  under-reports the one number the display exists to warn you about.
