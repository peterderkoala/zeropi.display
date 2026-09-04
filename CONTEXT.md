# zeropi.display

Pi Zero e-ink display project reusing pwnagotchi hardware to show a daily
Claude Code usage summary. Current phase: prove out the Bluetooth (BLE) link
between a desktop and the Pi, ahead of e-ink rendering or real data parsing.

## Language

**Desktop (BLE Central)**:
The machine that owns the real data sources (weather, calendar, Claude Code
usage) and initiates the BLE connection to push a Payload to the Pi.
_Avoid_: Client, sender

**Pi (BLE Peripheral)**:
The Pi Zero running the e-ink display. Advertises the GATT service, accepts
a Payload write, and is a dumb receiver — it does not fetch or compute data
itself.
_Avoid_: Server, receiver

**Payload**:
The JSON object the Desktop writes to the Pi's write characteristic in a
single BLE write. Currently `{"date", "usage_tokens", "oneliner"}`; grows to
include weather/calendar fields once those sources are wired in.
_Avoid_: Message, packet

**Reading**:
One row in the Pi's SQLite `readings` table, created when a Payload is
successfully received and parsed. The persisted history behind the
long-term usage graph the e-ink display will eventually render.
_Avoid_: Record, entry

**Ack**:
The JSON status object the Pi returns on its notify characteristic after a
write, reporting whether parsing *and* persistence of the Payload
succeeded.
_Avoid_: Response, reply

**One-liner**:
The short AI-generated summary string carried in the Payload, eventually
sourced from local Claude Code JSONL session logs instead of a paid API.
_Avoid_: Summary, blurb
