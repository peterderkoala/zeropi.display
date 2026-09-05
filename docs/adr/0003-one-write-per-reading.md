---
status: accepted
supersedes: ADR-0001
---

# One BLE write per Reading, not one per push

Supersedes [ADR-0001](./0001-single-write-payload-no-chunking.md), which
treated a single write as the whole contract. That held while a Payload was
one scalar test message; it does not hold now that a push covers a Window of
daily Usage. A seven-day Batch measures 1,190 bytes machine-wide against the
514 usable bytes of a negotiated 517-byte MTU, so the set no longer fits in
one write and something had to give.

We split by **Reading** rather than by byte. Each Daily Payload is one write
with its own Ack, and a Batch is simply a loop over them on a single
connection. A single Reading is 197–236 bytes measured (worst case 262), so
it fits comfortably with no framing of its own.

ADR-0001's reasoning is not overturned — chunking is still rejected, and for
the same reason. This decision *avoids* chunking rather than adopting it: a
Reading is an atomic unit that already exists in the domain, so splitting on
it needs no sequence numbers, no reassembly buffer on the Pi, and no partial
state to clean up if a connection drops. A chunking protocol would have to
invent all three.

## Consequences

Partial delivery stops being an error case. A dropped connection leaves the
Pi holding fewer Readings, each individually complete and already Acked, and
the unacknowledged ones are simply not marked as pushed — so the next push
retries them with no retry logic written for the purpose. Ordering is
newest-day-first so that an interrupted Batch has delivered the most useful
Readings.

The cost is that a push is now N round trips rather than one, at ~10s
worst-case timeout each. This is accepted: the alternative was a reassembly
protocol on a BLE stack that has already proven fragile enough to need a
`bluetoothd --noplugin` workaround.
