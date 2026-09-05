# The Pi wipes its Readings when the Desktop Id changes

A Pi is coupled to one Desktop at a time, but may be re-coupled to a
different one over its life. Every Payload carries a Desktop Id; the Pi
stores the one it has seen and, when a different one arrives, **drops and
recreates its Readings** before storing anything new. An absent stored id
means adopt, not wipe.

Without this, a hand-off corrupts the history in one of two silent ways.
Because a Project Key encodes an absolute path, a new Desktop with a
different username **does not collide** — its Readings land alongside the old
ones and the graph grows a permanent second set of series that look like new
projects. A new Desktop at the *same* path does the opposite: it overwrites
shared dates, so a day that held two machines' work quietly becomes one
machine's. Neither produces an error.

The Desktop Id is a salted, project-specific hash of the host's machine id.
It is kept **out of the Reading key and out of the Ack's correlation
fields** — the Pi compares it, it never joins on it — so the
`(date, Project Key, model)` grain is untouched by this decision.

## Consequences

The wipe would desynchronise against the Desktop's push marks, and does so on
the *legitimate* path: hand a Pi from Desktop A to B and back to A, and the
Pi wipes on the return while A's store still believes every Reading is
pushed — leaving the Pi permanently empty with nothing reporting it. The Ack
therefore carries a wipe flag on the first Payload after a wipe, and the
Desktop clears its push marks in response. **The flag is load-bearing, not a
nicety.**

Safe only because the Desktop, not the Pi, is the archive of record
([ADR-0005](./0005-desktop-store-is-the-archive-of-record.md)).

## Considered Options

- **A manual wipe command** at hand-off. Rejected: a wipe you forget to run
  fails silently and is discovered months later as a wrong graph.
- **Never wiping**, letting Project Keys keep the two Desktops' data apart.
  Rejected: the only option that produces permanently wrong output.
- **A machine dimension in the Reading key**, supporting several Desktops at
  once. Rejected as solving a problem this project does not have — the
  requirement is sequential coupling, not concurrent — at the cost of
  reopening the schema and the Ack shape.
- **Hostname or BLE adapter address** as the Desktop Id. Rejected: a rename
  or a dongle swap would trigger a spurious wipe, and per the consequence
  above a spurious wipe is expensive.
