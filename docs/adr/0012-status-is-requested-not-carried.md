# The Pi's status is requested, not carried on every Ack

The Management Surface needs to answer *is it working?*, and most of the
evidence lives on the Pi: which frame it last submitted, whether the panel can
actually be driven, how many Readings it holds, how long the process has been
up. There are two ways to get that across the link, and the Pi only has one
channel to speak on — its notify characteristic.

**Status is fetched by a dedicated `status` Command and returned in that
Command's Ack. The ordinary Daily and Gauge Acks do not change at all.**

## Why not widen the Ack

Widening the Ack was the obvious design and was the working assumption on
[map #70](https://github.com/peterderkoala/zeropi.display/issues/70) for most
of its life. It has one real attraction: the status arrives for free, with no
extra round trip, and the Desktop always holds a recent picture without asking.

Two measurements decided against it.

**A Batch is up to ~12 writes, and each one gets an Ack.** Status answers a
single question a human asks occasionally, so carrying it on every write pays
for it up to twelve times per Batch to learn one answer — and the Desktop is
already awake and connected at exactly the moment it could simply ask.

**The Ack direction has ~150 bytes of worst-case headroom, and no room to be
wrong about it.** [#78](https://github.com/peterderkoala/zeropi.display/issues/78)
measured the notify budget on the wire at **512 bytes** against a measured
worst-case Ack of ~360. The seven status fields are 234 bytes typical and 253
worst case — comfortably inside 512 on their own, and comfortably *outside* the
headroom left on an Ack that is already carrying a Reading's result. Widening
would have pushed the common case toward a ceiling that
**truncates silently, twice**: `bluetoothd` clips an oversized notification and
returns success, the ATT server clips again at the MTU bound and returns
success, and `bluezero` adds no check. The Desktop then reports
`malformed ack from Pi`, blaming the JSON rather than the length.

The cost of the choice is one extra round trip, which is negligible beside the
BLE connect it rides on — and the request is idempotent, so it satisfies the
membership rule that keeps the verb list closed
([ADR-0013](./0013-no-command-overrides-a-verified-invariant.md) governs the
other half of that list's discipline).

## Consequences

**The hot path is untouched.** Nothing about a Daily or Gauge push changes, so
the pipeline's measured behaviour, its byte budgets and its verified failure
modes all stand unamended. A status feature that had altered every Ack in the
system would have put the whole verified pipeline back in scope.

**Status is only ever as fresh as the last request**, and there is no
push-on-fault. A panel that goes stuck at 03:00 is invisible until somebody
runs `status`. This is accepted for a personal usage display and is the sharpest
argument the other way; it is not closed forever, and an unsolicited fault
notification remains a legitimate future question. It is deliberately *not*
decided here, because deciding it needs a reason to spend bytes on the hot path
that this ticket did not have.

**The Pi reports facts; the Desktop renders the verdict.** Because status is a
reply to a question rather than a broadcast, it can stay a flat list of raw
counts and durations — `readings: 312` rather than "in sync". Only the Desktop
holds the other side of every comparison, so only it can judge, and the judgment
can then improve without touching the Pi or the wire.

**Every field is a duration or a count, never a timestamp** — the mirror of
[ADR-0009](./0009-pi-is-given-durations-not-timestamps.md). The Pi is given
durations because it has no wall clock, and for the same reason it can only ever
report them.

**Something on the Pi must enforce the notify budget before BlueZ does.** This
decision keeps the reply well clear of the ceiling, but it does not remove the
ceiling, and the write direction's loud
`INVALID_ATTRIBUTE_VALUE_LENGTH` has no counterpart here. A named
`MAX_ACK_BYTES`, checked before notifying, is what stops a future field turning
into a silent truncation.
