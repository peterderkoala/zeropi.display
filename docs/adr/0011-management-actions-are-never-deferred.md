# Management actions are never deferred

The Desktop initiates every connection and the Pi is a peripheral that must be
in range and advertising, so every management action has a failure mode that is
not an error: the Pi is simply **Unreachable** right now. This decides what the
management surface does in that moment. **It refuses the action, at the moment
it is typed. There is no queue, and "queued" is not a state the surface holds.**

Concretely, for everything the Desktop can send:

- **A Settings Payload** needs no queue, because it is declarative
  ([#75](https://github.com/peterderkoala/zeropi.display/issues/75)): it names
  the complete set, so the current Configuration *is* the pending state and
  re-sending it converges the Pi from whatever it held. The Desktop therefore
  re-asserts the current Settings at the head of **every** connection it opens,
  holding no "last delivered" mark at all.
- **Every Command** — `redraw`, `wipe`, and the `status` verb added later by
  [#74](https://github.com/peterderkoala/zeropi.display/issues/74) — is refused
  now. The human is the only retrier.

## Why not queue

`redraw` and `status` make the easy half of the argument: both are inherently
time-bound. A redraw delivered an hour later is not what anyone asked for — the
panel will have redrawn on its own clock several times by then — and a status
reply that describes an hour-old Pi answers nothing. Deferring either produces a
worse result than refusing.

**`wipe` is the case that decides the rule**, because it is the one that could
plausibly be queued: it is not time-bound, and a wipe is just as correct an hour
later. The reason not to is that it is also the one **destructive** verb, and a
deferred wipe fires against whatever Pi answers next. The hazard is not
hypothetical — **the reason the Pi is Unreachable may be the reason not to wipe
it**: it has been powered down for a hand-off, or is already coupled to a
different Desktop. A wipe is a **repair** tool, and repair is something a
present human does while watching, not something a laptop does later on their
behalf.

Once `wipe` cannot be queued, a queue that holds only the two verbs that
shouldn't be queued either is a surface with no members.

## Consequences

**The management surface is considerably smaller than it looked.** No queue
store, no drain loop, no "pending" state to render, and no lifecycle for a
deferred action. It also sidesteps a structural problem:
[#71](https://github.com/peterderkoala/zeropi.display/issues/71) settled that
the resident service **never writes Configuration**, so a queue the CLI writes
and the service drains would have needed a third store invented to hold it.

**Settings re-assertion is best-effort.** Because it rides at the head of every
connection, a failed Settings write must never fail the job the connection was
opened for: it logs and continues, is not counted in the Batch's failed-row
total, and does not affect the exit code. Otherwise a flaky Setting starts
failing Batches for no reason. The exception is a **CLI-initiated** settings
change, whose only job is that write — there a failure genuinely is the failure,
and it reports *not applied yet; it will be applied on the next successful
connection*, which is true precisely because no queue is involved.

**Unreachable has two cases and the surface must tell them apart** — *absent*
(off, out of range, mid-`bluetoothd` restart) and *busy* (another local process
holds the link). Without that distinction a human is told "no Pi" while the Pi
is sitting right there mid-Batch. Today nothing on the Desktop can tell:
`push.py` connects per push and disconnects
(`_with_ble_connection`, `desktop/push.py`), and there is **no lock, PID file or
IPC of any kind**. The implementation acquires an advisory `flock(2)` inside
`_with_ble_connection`, so both the CLI and the resident service take it by
construction; the kernel releases it when the holder dies, so there is no stale
lock to recover. When it is held, the **CLI waits a bounded ~15 s and the
service fails immediately** — asymmetric on purpose: a Gauge push holds the link
only ~2–3 s including scan and connect, so a short wait absorbs every Gauge
collision and leaves only the twice-daily Batch as a genuine "busy", while the
service already treats both its jobs as droppable (spec §7.3 retries the Batch,
§7.4 drops the Gauge).

**Nothing is added for "the Desktop is off".** That symmetry is already covered:
[ADR-0010](./0010-an-expired-gauge-is-not-drawn.md)'s expiry stops the Gauge
being drawn and returns the Historic View, and #74 made status *requested*, so a
Desktop that is off asks nothing and needs to be told nothing.

**This is an absence, and absences get re-added by accident.** That is why it is
an ADR rather than a line in the spec. A future contributor who finds no queue
is likely to read it as an omission and helpfully fix it — reintroducing the
deferred `wipe` this decision exists to prevent.
