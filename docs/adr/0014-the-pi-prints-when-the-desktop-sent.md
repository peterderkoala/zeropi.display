# The Pi prints when the Desktop last sent, as the Desktop's own text

A healthy idle Pi and one whose Desktop has been off for a week drew the same
Historic View. So the footer now says when data last arrived
([#99](https://github.com/peterderkoala/zeropi.display/issues/99)):
`SINCE 01 SEP   30/10 04:00   AVG $40`.

**The Desktop formats the text at push time and sends it as `sent_label`
on every Daily and Gauge Payload. The Pi stores the latest one and prints it
verbatim.** It is always a date and a time (`%d/%m %H:%M`, the Desktop's local
time), and never "5m ago".

## Why this does not break ADR-0009

[ADR-0009](./0009-pi-is-given-durations-not-timestamps.md) says the Pi is never
given *an instant it has to interpret*, because the Pi's clock can be days
wrong. `sent_label` is not one. The Pi never parses it, compares it, or
measures anything against it. To the Pi it is text, like a Project Key. The
instant is interpreted once, on the Desktop, whose clock is good. Nothing on
the Pi reads its own clock for this.

That is also why it is absolute rather than relative. `5m ago` would need the
Pi to count forward from the arrival, and it would be false a minute after the
draw. The panel redraws idle only every 24 h, so it would stay false for a day.
`30/10 04:00` stays true however long it sits on the glass. It is always date
and time, never time alone when it is today: knowing "today" is the clock
problem again.

## Why this does not break ADR-0010

[ADR-0010](./0010-an-expired-gauge-is-not-drawn.md) rejects marking anything on
the panel stale, because a marked-stale number asks the viewer not to trust it.
The label marks nothing. Every number on the frame is drawn exactly as before,
with no dimming, hatching or banner. The label says when the data came, and the
viewer judges whether that is old. On a healthy setup it usually reads `04:00`,
from the overnight Batch. The signal is an *old date*.

## Consequences

- **Meaning: the last Payload of a data kind,** Daily or Gauge. It is "the last
  time the Desktop talked to this Pi". Settings and Commands do not count:
  they say nothing about whether data is flowing.
- **A Payload without a usable label clears it**: missing, not a string, empty,
  or over 11 characters (`30/10 14:05`, which is what the footer was measured to
  fit). The footer then draws nothing, not a placeholder. During an upgrade with
  mixed versions, a label can therefore never outlive the Payloads that carried
  it. Any wipe clears it too, together with the data it dated.
- **An older Pi ignores the field.** `parse_payload` does not reject unknown
  fields, so a new Desktop stays compatible with an un-upgraded Pi.
- **Historic View only.** The Gauge frame is under 300 s old by construction
  (ADR-0010's expiry), and its footer is deliberately blank. The empty frame
  has no data to date.
- **Persisted** in `meta['sent_label']`, so the startup draw after a reboot
  shows it too.
