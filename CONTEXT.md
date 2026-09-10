# zeropi.display

Pi Zero e-ink display project reusing pwnagotchi hardware to show live Claude
Code usage: a gauge of current consumption against the rolling limit windows,
backed by a daily history graph. That is the whole of it — weather, calendar
and the One-liner were dropped from the project on 2026-09-09. Current phase:
specifying one management surface for both ends (map #70) — the panel itself is
built and hardware-verified.

## Language

### The two ends

**Desktop (BLE Central)**:
The machine that owns the real data source — Claude Code usage — and
initiates the BLE connection to push a Payload to the Pi. Need
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
single BLE write. Comes in four shapes — a Daily Payload, a Gauge Payload, a
Settings Payload or a Command Payload. Every shape names its own, and the Pi
branches on that name rather than on which fields are populated.
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

**Settings Payload**:
The Payload shape carrying the Pi's Settings. **Declarative** — it names the
complete set, not a change to it, so the Pi's Settings after applying one are
exactly what it was sent. That is what makes a resend harmless.
_Avoid_: Config payload, update payload

**Command Payload**:
The Payload shape carrying one Command.
_Avoid_: Action payload, RPC, request

**Command**:
One verb from a short, closed list, telling the Pi to do something once. A
Command is **imperative** where Settings are declarative, and every Command
must be **naturally idempotent** — an Ack can be lost, so a Command will be
retried, and nothing may depend on it arriving exactly once.
_Avoid_: Method, call, instruction, request

> Natural idempotency is a **membership rule**, not a property to check
> afterwards: a verb that cannot be made idempotent does not join the list.
> That rule, more than the list's shortness, is what keeps a vocabulary from
> becoming an RPC surface.

> A Command may **never override a verified invariant** — it queues behind one.
> Enforcement that a Tier moved out of reach of a settings form must not be
> reachable through a verb instead.

**Batch**:
The set of Daily Payloads sent in one push, each written and acknowledged
separately over a single BLE connection. Every Payload in a Batch knows its
own position in it, so an incomplete Batch is recognisable without an extra
round trip.
_Avoid_: Push (that is the verb), sweep, upload

**Ack**:
The JSON status object the Pi returns on its notify characteristic after a
write, reporting whether parsing *and* persistence of the Payload succeeded,
which Reading it refers to, and whether the Pi has wiped its Readings. When the
write was a Command, it also carries that Command's result.
_Avoid_: Response, reply

> An Ack reports **durations and counts, never timestamps** — the mirror of
> [ADR-0009](./docs/adr/0009-pi-is-given-durations-not-timestamps.md). The Pi is
> given durations because it has no wall clock, and for the same reason it can
> only report them.

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

**Panel Health**:
Whether the Pi can actually drive the panel — the render worker alive, and its
last refresh completed rather than raising or hanging. Distinct from whether a
frame was *submitted*, which is all the redraw floor's own reporting can say.
_Avoid_: Panel status, display state, drawn, screen health

> The distinction is the whole point: a stuck or failed panel leaves BLE
> serving, Acks succeeding and Readings persisting, with only the glass frozen.
> Until [#74](https://github.com/peterderkoala/zeropi.display/issues/74) the Pi
> computed this and then discarded it into its own log.

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
How old the **underlying snapshot** behind the Gauge is: the age it already had
when the Desktop sent it, plus the monotonic seconds since that Payload
arrived. The Pi's only measure of freshness, and the reason it needs no wall
clock. At 300 s the Gauge is **expired** and no longer a live reading.
_Avoid_: Staleness, last updated, timestamp, received_at, time since arrival

> ⚠ **It is not "how long ago the Pi received it"** — that is only the second
> half. A Gauge can arrive already half-expired, so the panel can fall back to
> the Historic View while a Payload that landed seconds ago sits in memory. This
> was found on glass in
> [#66](https://github.com/peterderkoala/zeropi.display/issues/66), and this
> entry defined it wrongly until
> [#74](https://github.com/peterderkoala/zeropi.display/issues/74). Any new
> freshness field must say **which** age it means, or it reads as an
> off-by-300s bug.

**Historic View**:
What the panel shows when there is no live Gauge — the most recent **Active
Days**, each with its cost, over the whole history the Pi holds. It is the
display's resting picture, and the Gauge is what temporarily replaces it.
_Avoid_: Fallback screen, idle screen, error state

**Active Day**:
A calendar date on which any Usage was recorded at all. The Historic View
counts and lists Active Days rather than calendar days, because usage is
bursty — 11 Active Days over 44 calendar days, measured — and a calendar
window drawn over that is mostly gaps. Distinct from a date the Pi never
received, which is outside **Coverage Start** and is not a fact about usage
at all.
_Avoid_: Working day, busy day

**Cost Complete**:
Whether every model in a Reading was found in the pricing table. A Reading
whose model is unrecognised still counts its tokens, but is marked
incomplete rather than being dropped or failing the push.
_Avoid_: Priced, valid, accurate

### Managing the ends

**Configuration**:
The Desktop's own tunable values, held in a dedicated SQLite store separate
from the archive of record. Read once at process startup and never re-read, so
a process runs one known Configuration for its whole life. Written only by the
management surface — never by the resident service.
_Avoid_: Config file, preferences, options, config table

> Deliberately not a section in the Desktop store. That store is the archive of
> record (ADR-0005) with its own version gate and its own backup story;
> Configuration would become unreadable exactly when the archive is broken, and
> restoring old history would silently restore old Configuration with it.

**Settings**:
The subset of Configuration that is projected onto the Pi. The Pi holds no
Configuration of its own — it is told, which is what keeps it a dumb receiver —
and Settings apply **live** there, because the Pi cannot be restarted without
dropping the connection that delivered them.
_Avoid_: Pi config, remote config, device settings

> **Configuration** and **Settings** are not synonyms and the distinction is
> load-bearing: Configuration is what the Desktop holds, Settings are what the
> Pi is given. A value can be Configuration without being a Setting; nothing is
> a Setting without first being Configuration.

> The Pi still compiles in a **default** for every Setting, for the life before
> it has ever been told one. A code default is not Configuration — the same
> line drawn for a verified invariant — so this does not weaken "the Pi holds no
> Configuration of its own". The Pi does persist the Settings it is given, so a
> reboot cannot silently revert one; a hand-off clears them back to the
> defaults, because they were the previous Desktop's policy.

**Tier**:
Which of three classes a tunable value belongs to: a deployment fact, freely
editable; a policy value, editable within a validated range; or a **verified
invariant** — a value fixed by a hardware verification run and its ADR, which
is displayed read-only rather than hidden, and is not a Setting at all.
_Avoid_: Level, category, class, severity

> The third Tier exists because several of this project's constants are
> *findings*, not preferences. A management surface that let a form change them
> could silently invalidate the run that established them.

> A verified invariant is **not stored as Configuration at all** — it stays a
> constant in the code and is read from there to be displayed. A stored row is
> writable by anyone holding the store, which is the one thing this Tier exists
> to prevent. Only the first two Tiers ever appear in Configuration.

> Tier applies to *tunable values*. Schema versions, identity constants and
> parsing markers have no Tier, because there is no sense in which they could be
> tuned.

