# The Pi is given durations, not timestamps

The Pi Zero 2W has **no RTC**, and `pi/install.sh` configures no time source.
Its clock is whatever systemd restored at boot from the mtime of
`/var/lib/systemd/timesync/clock` until NTP happens to correct it — measured
on the dev Pi as a **45-second window** after boot
(`13:47:32` boot, `13:48:17` initial sync), with
`systemd-time-wait-sync` disabled so nothing holds `receive.py` back during
it. A Pi that was powered off for a week starts that window a week behind.

So the Pi is never given an instant it has to interpret. Everything on the
wire that concerns time is a **duration measured at push time on the
Desktop**, which always has a good clock: the Gauge Payload carries a
**Reset Countdown** (seconds remaining in the Limit Window) rather than
`resets_at`, and a snapshot age in seconds rather than `updated_at`. The Pi
advances both with `time.monotonic()`, a boot-relative counter that is
correct on a Pi that thinks it is 1970.

This resolves the dependency
[ADR-0008](./0008-pi-enforces-the-redraw-floor.md) flagged as unsatisfied.
The Pi still redraws on its own clock, as 0008 decided — that clock is now
the monotonic one, and the countdown it animates needs no calendar.

`time.monotonic()` is also immune to NTP **steps**, which fixes a second,
quieter bug in the timestamp design: a Pi that syncs mid-countdown would have
jerked its display by the size of the correction, at the one moment the clock
finally became right.

## Consequences

**The bad-clock failure mode does not exist**, rather than being detected.
There is no sanity floor, no build-stamped date, no known-bad clock state on
the display, and no distinct rendering for one — a whole branch of
[#37](https://github.com/peterderkoala/zeropi.display/issues/37) dissolved
instead of resolving. The Pi also never sets its clock from a Payload, so the
degenerate case (taking your time from the thing you are measuring against)
cannot arise.

**A Payload's time fields are only meaningful relative to its own arrival.**
That is the intended semantics, but it means a Payload is not replayable and
not archivable as-is — which is consistent, since the Gauge is display-only
and never persisted.

**`received_at` is dropped** — from the Reading and from the Ack, its two
sites in the current code (`pi/receive.py:121` and `:73`). It was the last
thing forcing a correct wall clock onto the Pi, nothing read it at either site,
and the Desktop store is the archive of record
([ADR-0005](./0005-desktop-store-is-the-archive-of-record.md)). The Ack's
useful content is what later decisions gave it — the `date`/`project`/`model`
correlation echo, `wiped`, and drawn-or-coalesced — none of which needs a
clock. Arrival order on the Pi, if ever needed, is `rowid`.

**The Pi never computes a date.** The historic view renders the `date` strings
it was given, in the order given; it does not ask what today is, and a gap
shows only if the Desktop sends one.

**A Gauge reading expires at 300 s of Gauge Age**, one redraw floor, so an
expired reading always means at least one missed push rather than a race. The
Reset Countdown clamps at zero and reaching zero does not itself expire the
reading — a Limit Window genuinely can reset mid-session, and the next push
says so.

**`install.sh` still asserts a time source** — `systemctl enable --now
systemd-timesyncd` — even though the display no longer depends on one. A
badly wrong clock breaks **TLS certificate validation**, which is how the curl
bootstrap fetches its tarball, and it makes `journalctl` useless for
diagnosing the next BLE problem. Two idempotent lines beat silently inheriting
an image default.

## Considered Options

- **Send ISO instants and have the Pi subtract its own `now`** — the obvious
  design, and what [#24](https://github.com/peterderkoala/zeropi.display/issues/24)
  and [#25](https://github.com/peterderkoala/zeropi.display/issues/25) both
  assumed. Rejected: it fails silently and points the wrong way. A stale clock
  dims a fresh reading and renders a nonsense countdown, in a way that
  implicates the Desktop or the BLE link rather than the clock.
- **Detect a bad clock with a sanity floor** (a build-stamped date the Pi
  cannot legitimately precede) and suppress the countdown when it trips.
  Rejected: it is more code on the dumb end to mitigate a failure that a
  change of units removes outright, and it only catches clocks that are
  *behind the build*, not clocks that are merely wrong.
- **Add an RTC** — the likely PiSugar 3 UPS carries one. Rejected as the
  answer, though not as hardware: it makes a correct display depend on an
  optional accessory, on a project whose explicit assumption is mains power.
  Out of scope for now; if one is fitted later it improves the logs, and this
  decision does not need revisiting.
- **Derive the Pi's wall clock from the Payload's generated-at timestamp.**
  Rejected on the same ground it was raised: the Pi would then take its time
  from the thing it measures staleness against, and the comparison degenerates
  to "always fresh".
