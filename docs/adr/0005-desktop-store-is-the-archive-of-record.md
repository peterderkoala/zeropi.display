# The Desktop store is the archive of record, and aggregation reads only it

Readings are aggregated from a SQLite store on the Desktop, never re-derived
from the Claude Code logs. There is deliberately **no `--rebuild-from-logs`
mode**; a corrupt store is restored from a backup of the store itself.

This is surprising, because re-deriving from the logs looks like a free
repair path — the logs are right there. It is not free: the logs
**self-delete on a rolling 30-day sweep**, and they degrade *gradually*
rather than all at once. A single day's completeness decays as individual
session files age out at different times, so re-reading the logs later can
yield a **smaller** row for a day that was already stored in full. A repair
mode would therefore silently overwrite good history with worse history,
which is precisely the failure the store exists to prevent.

The Pi cannot serve as the archive instead: the GATT service has **no read
path**, so the Desktop can never ask the Pi what it holds. This is also why
push state is tracked as per-Reading marks in the Desktop store rather than
by querying the Pi.

This does **not** reverse
[ADR-0002](./0002-readings-persisted-on-pi.md). Readings still live on the Pi,
for the reason ADR-0002 gives: the display must render its graph without a
live BLE session. The two answer different questions — ADR-0002 says where the
display *reads* from, this says where the truth *lives*. The Desktop store
holds finer-grained entries; the Pi holds the Readings derived from them.

## Consequences

The Pi is a rebuildable cache. Wiping it is recoverable rather than
destructive, which is what makes both retention pruning and
[ADR-0006](./0006-pi-wipes-on-desktop-id-change.md)'s wipe-on-recouple safe
to do at all.

Backups of the store are the only backups that matter. Losing it loses
history permanently, however intact the logs look.
