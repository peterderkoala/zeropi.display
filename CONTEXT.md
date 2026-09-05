# zeropi.display

Pi Zero e-ink display project reusing pwnagotchi hardware to show live Claude
Code usage: a gauge of current consumption against the rolling limit windows,
backed by a daily history graph. Current phase: specifying the pipeline that
reads usage on the Desktop and pushes it over Bluetooth (BLE) to the Pi.

## Language

### The two ends

**Desktop (BLE Central)**:
The machine that owns the real data sources (weather, calendar, Claude Code
usage) and initiates the BLE connection to push a Payload to the Pi. Need
not be the maintainer's dev machine — any Linux box running Claude Code can
be provisioned as one. A Pi is coupled to **one Desktop at a time**, but that
Desktop is replaceable.
_Avoid_: Client, sender

**Pi (BLE Peripheral)**:
The Pi Zero running the e-ink display. Advertises the GATT service, accepts
a Payload write, and is a dumb receiver — it does not fetch or compute data
itself.
_Avoid_: Server, receiver

> On *Client* and *Server*: in strict GATT terms the Pi **is** the server and
> the Desktop the client, so the pull toward those words is understandable
> and recurring. They are still avoided, because "server" implies the Pi
> serves or computes something when it is a dumb receiver, and because it
> points the wrong way for *data* flow, where the Desktop is the active
> party. Use the GATT roles only when discussing the GATT layer itself.

**Desktop Id**:
The value identifying which Desktop a Pi is currently coupled to. Derived on
the Desktop rather than assigned, and carried on every Payload so the Pi can
notice it has been coupled to a different Desktop.
_Avoid_: Machine id, host id, client id

### On the wire

**Payload**:
The JSON object the Desktop writes to the Pi's write characteristic in a
single BLE write. Comes in two shapes — a Daily Payload or a Gauge Payload.
_Avoid_: Message, packet, row

**Daily Payload**:
The Payload shape carrying one day's Usage for one Project Key and one model.
The Pi persists it as a Reading.
_Avoid_: History payload, usage payload

**Gauge Payload**:
The Payload shape carrying the live gauge: consumption against the rolling
limit windows, and the active session's context size. Display-only — the Pi
never persists it.
_Avoid_: Live payload, status payload

**Batch**:
The set of Daily Payloads sent in one push, each written and acknowledged
separately over a single BLE connection. Every Payload in a Batch knows its
own position in it, so an incomplete Batch is recognisable without an extra
round trip.
_Avoid_: Push (that is the verb), sweep, upload

**Ack**:
The JSON status object the Pi returns on its notify characteristic after a
write, reporting whether parsing *and* persistence of the Payload succeeded,
which Reading it refers to, and whether the Pi has wiped its Readings.
_Avoid_: Response, reply

### Held on the Pi

**Reading**:
One stored day of Usage, keyed by date, Project Key and model — created when
a Daily Payload is successfully received and parsed. The persisted history
behind the long-term graph the e-ink display will eventually render. A Gauge
Payload produces **no Reading**; the live gauge is never stored. A Reading is
never deleted because it is old or because there are many of them; it goes
only when the whole table goes, on a Desktop hand-off or a schema change.
_Avoid_: Record, entry, sample

**Coverage Start**:
The earliest date the currently coupled Desktop has pushed. It exists so that
a date the Pi simply never received reads as *outside coverage* rather than
as zero usage.
_Avoid_: Since, epoch, first date

### The usage data

**Usage**:
The five billed token classes, plus cost in USD, plus session count, read
from the Desktop's local Claude Code logs. Always all three together — a
token count alone is not Usage.
_Avoid_: Stats, metrics, consumption

**Gauge**:
The live reading of how much of the rolling limit windows has been consumed,
together with the active session's context size. Sourced ready-made rather
than computed locally, and expressed as a percentage.
_Avoid_: Meter, usage bar, quota

**Project Key**:
The stored identity of a project: the encoded absolute path of its working
directory. Exact and verifiable — a candidate path can be re-encoded and
compared — which is why it, not a shortened name, is what gets stored.
_Avoid_: Project name, project id, project dir

**Project Label**:
The short, human-readable form of a Project Key, derived when something is
rendered. Never stored, so the display rule can change without re-keying
history.
_Avoid_: Display name, short name

**Window**:
The rolling seven **calendar** days a push covers. Days with no usage are
skipped rather than sent as zeroes.
_Avoid_: Period, range, last week

> Deliberately seven *calendar* days, not seven *active* days: on real logs
> the last seven active days spanned 27 calendar days, which makes "the last
> week" a lie. A **Limit Window** is a different thing again, and is never
> called a Window.

**Limit Window**:
The rolling server-side period a Claude Code rate limit accrues over — the
five-hour one or the seven-day one. Its boundary is read from the snapshot's
`resets_at`, never modelled locally, and it is account-wide rather than
per-machine. What the Gauge is a percentage *of*. `resets_at` stops on the
Desktop: what crosses the wire is a Reset Countdown.
_Avoid_: Window (that is history coverage), block, quota period, reset window

**Reset Countdown**:
The seconds remaining in a Limit Window, computed on the Desktop at push time
and advanced on the Pi against a boot-relative monotonic counter. A duration,
never an instant — the Pi has no clock it can trust to interpret one. Clamps
at zero rather than going negative.
_Avoid_: resets_at, deadline, expiry, TTL

**Gauge Age**:
How long ago the Pi received the Gauge it is showing, in monotonic seconds
since that Payload arrived. The Pi's only measure of freshness, and the reason
it needs no wall clock. At 300 s the Gauge is **expired** and no longer a live
reading.
_Avoid_: Staleness, last updated, timestamp, received_at

**Historic View**:
What the panel shows when there is no live Gauge — the daily trend drawn from
stored Readings. Not a fallback screen or an error state: it is the display's
resting picture, and the Gauge is what temporarily replaces it.
_Avoid_: Idle screen, default view, fallback

**Cost Complete**:
Whether every model in a Reading was found in the pricing table. A Reading
whose model is unrecognised still counts its tokens, but is marked
incomplete rather than being dropped or failing the push.
_Avoid_: Priced, valid, accurate

**One-liner**:
The short AI-generated summary string carried in the Payload, eventually
sourced from local Claude Code JSONL session logs instead of a paid API.
_Avoid_: Summary, blurb
