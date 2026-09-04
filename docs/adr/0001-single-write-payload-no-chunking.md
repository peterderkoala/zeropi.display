# Single BLE write per Payload, no chunking protocol

BLE's default ATT MTU (23 bytes, ~20 usable) is smaller than even the
milestone-1 test Payload. Rather than build a chunking protocol that splits
a Payload across multiple writes and reassembles it on the Pi, we negotiate
a larger MTU up front (bleak/bluezero support this, typically up to ~247
bytes) and treat a single write as the contract.

This is chosen because current and near-term Payloads (a handful of
scalar/string fields) comfortably fit in a negotiated MTU. Chunking adds
real framing/reassembly complexity that isn't needed yet. If a future
Payload (e.g. richer weather/calendar data) outgrows the negotiated MTU,
this decision should be revisited.

## Considered Options

- **Chunking protocol**: split large Payloads across multiple writes with a
  sequence/reassembly scheme on the Pi. Rejected for now — adds protocol
  complexity before the basic BLE link is even proven.
