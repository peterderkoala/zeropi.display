# The notify-direction budget: what the Pi can send back in one Ack

Research for [#73](https://github.com/peterderkoala/zeropi.display/issues/73),
a child of map [#70](https://github.com/peterderkoala/zeropi.display/issues/70).
Documentation and source research only — **no hardware was touched**, per the
ticket (`receive.py` owns the panel's GPIO).

> ✅ **Confirmed on hardware 2026-09-10 by
> [#78](https://github.com/peterderkoala/zeropi.display/issues/78). The bottom
> line — 512 bytes, truncated silently — holds exactly.** One correction to the
> *reasoning*: BlueZ notifies with opcode **`0x23`**, whose per-value overhead
> is **5 bytes, not 3**. See [§6](#6-bench-confirmation-78) for the measurement
> and why the error stayed invisible.

---

## The answer in one line

**512 bytes — now measured, not derived** ([§6](#6-bench-confirmation-78)). The
Pi's notification payload is bounded by `min(512, ATT_MTU − overhead)`, where
the overhead is **5 bytes** for the `Handle Multiple Value Notification`
(`0x23`) opcode BlueZ actually uses here, and 3 for the classic `0x1b`. Both
ends are BlueZ with the default `ExchangeMTU = 517`, and 517 was confirmed on
the wire, so `ATT_MTU − 5 = 512` and the 512-byte `gatt-database.c` cap agree
to the byte. ⚠ **Both caps truncate silently**, confirmed at the boundary: a
512-byte Ack arrives whole, a 513-byte Ack arrives as 512 with no error at
either end.

⚠ **The original `ATT_MTU − 3` was wrong by two bytes and it did not show**,
because at MTU 517 both terms of the `min()` land on 512. At any smaller MTU it
would bite — at 247, `− 3` gives 244 where the truth is 242. That is the third
time this project has been caught by a two-byte MTU derivation (#32, #67); the
`min(512, …)` form is what kept the answer right anyway.

**ADR-0001's 512 is numerically the same in this direction and structurally
not symmetric.** Same number, different mechanism, weaker guarantee. See
[§3](#3-is-adr-0001s-512-byte-budget-symmetric).

---

## 1. What bounds a notification

### 1.1 The Bluetooth Core Spec: `ATT_MTU − 3`, and no fragmentation

Core Specification 6.0, Vol 3 Part F (Attribute Protocol), **§3.4.7.1
`ATT_HANDLE_VALUE_NTF`** — the *Attribute Value* field is sized
`0 to (ATT_MTU-3)`, and:

> If the attribute value is longer than (ATT_MTU-3) octets, then only the
> first (ATT_MTU-3) octets of this attributes value can be sent in a
> notification.
>
> Note: For a client to get a long attribute, it must use the
> ATT_READ_BLOB_REQ PDU.

**§3.4.7.2 `ATT_HANDLE_VALUE_IND`** is word-for-word the same limit —
`0 to (ATT_MTU-3)`, with the identical truncation sentence. **Indications do
not buy a single extra byte.**

**§3.2.9 Long attribute values** spells out the asymmetry directly:

> To write the entire value of an attribute larger than (ATT_MTU-3) octets,
> the ATT_PREPARE_WRITE_REQ and ATT_EXECUTE_WRITE_REQ PDUs are used. […]
> **The maximum length of an attribute value shall be 512 octets.**

and, from the same part:

> The ATT_READ_BLOB_REQ PDU is the only way to read the additional octets of a
> long attribute. The first (ATT_MTU-1) octets may be read using an
> ATT_READ_RSP PDU; **the first (ATT_MTU-3) octets can be received in an
> ATT_HANDLE_VALUE_NTF or an ATT_HANDLE_VALUE_IND PDU.**

So: ATT gives the *write* direction a fragmentation protocol (prepare/execute)
and the *read* direction a fragmentation protocol (read blob). It gives the
**notify direction none**. A notification is one PDU, always.

Source: [Core 6.0, Vol 3 Part F](https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/Core-60/out/en/host/attribute-protocol--att-.html)

### 1.2 The default ATT_MTU is 23 when nothing negotiates

Core 6.0, Vol 3 Part G (GATT) **§5.2.1**:

> Both GATT Client and GATT Server implementations shall support an ATT_MTU
> not less than the default value. — **Default Value for LE ATT_MTU: 23**
> (Table 5.1: LE L2CAP ATT_MTU)

Vol 3 Part F §3.2.8: the client and server *may optionally* exchange MTUs with
`ATT_EXCHANGE_MTU_REQ`/`_RSP`, and then "both devices use the minimum of these"
— so **23 is what you get if nobody asks**, and 23 − 3 = **20 usable bytes**.
That would not hold a single field of the Ack.

(Enhanced ATT / EATT is a different rule — §5.3.1 sets the EATT bearer's MTU
from the L2CAP credit-based connection, minimum 64 — but EATT is off by
default in BlueZ; see §1.5.)

### 1.3 BlueZ truncates twice, and never says so

The Pi's Ack goes: `bluezero` → D-Bus `PropertiesChanged` on `Value` →
`bluetoothd`'s external-GATT-app handler → the ATT server. Two independent
caps sit on that path, and **both are silent truncations, not errors.**

**Cap 1 — 512, in the D-Bus handler.** `src/gatt-database.c`, the
`PropertiesChanged`-on-`Value` handler for an external (D-Bus-registered)
characteristic — the exact path `bluezero` uses:

```c
    dbus_message_iter_get_fixed_array(&array, &value, &len);
    …
    /* Truncate the value if it's too large */
    len = MIN(BT_ATT_MAX_VALUE_LEN, len);
    value = len ? value : NULL;

    send_notification_to_devices(chrc->service->app->database, …, value, len, …);
```

`BT_ATT_MAX_VALUE_LEN` is `512` (`src/shared/att-types.h:30`). The comment says
"truncate"; there is no error return, no D-Bus exception, no log line.

**Cap 2 — `ATT_MTU − 3`, in the ATT server.**
`src/shared/gatt-server.c`, `bt_gatt_server_send_notification()`:

```c
    data->len = bt_att_get_mtu(server->att) - 1;   /* minus the opcode octet */
    data->pdu = malloc(data->len);

    if (!notify_append_le16(data, handle))         /* minus the 2-octet handle */
        goto error;
    …
    length = MIN(data->len - data->offset, length);   /* -> ATT_MTU - 3 */
    if (value)
        memcpy(data->pdu + data->offset, value, length);
```

`data->len - data->offset` is `(ATT_MTU − 1) − 2 = ATT_MTU − 3`. Over-long
input is clipped by `MIN` and the function still returns true. Again: silent.

`bt_gatt_server_send_indication()` in the same file does the same thing —
`pdu_len = MIN(bt_att_get_mtu(server->att) - 1, length + 2)` — confirming §1.1
at the implementation level.

Source:
[gatt-database.c](https://github.com/bluez/bluez/blob/master/src/gatt-database.c),
[gatt-server.c](https://github.com/bluez/bluez/blob/master/src/shared/gatt-server.c),
[att-types.h](https://github.com/bluez/bluez/blob/master/src/shared/att-types.h)

### 1.4 `bluezero` adds no check of its own

`bluezero/localGATT.py`:

```python
    def set_value(self, value):
        self.Set(constants.GATT_CHRC_IFACE, 'Value',
                 dbus.Array(value, signature='y'))

    def Set(self, interface_name, property_name, value):
        …
        self.props[constants.GATT_CHRC_IFACE][property_name] = value
        return self.PropertiesChanged(interface_name,
                                      dbus.Dictionary({property_name: value},
                                                      signature='sv'),
                                      dbus.Array([], signature='s'))
```

No length validation anywhere. `receive.py:593`'s
`characteristic.set_value(list(json.dumps(ack).encode("utf-8")))` hands BlueZ
whatever it is given, and BlueZ quietly clips it.

Source: [python-bluezero `localGATT.py`](https://github.com/ukBaz/python-bluezero/blob/main/bluezero/localGATT.py)

### 1.5 The negotiated MTU on *this* link is 517 — derived from config, not measured

- BlueZ `src/main.conf`, `[GATT]` section: `ExchangeMTU` — "Possible values:
  23-517. **Defaults to 517**". `src/main.c:1425` sets
  `btd_opts.gatt_mtu = BT_ATT_MAX_LE_MTU` (= 517) before parsing config.
- Same section: `Channels` — "1 disables EATT. **Default to 1**." So EATT is
  **off** and the plain ATT fixed channel (CID 4) is the only bearer.
- **Pi**: BlueZ 5.82 (`pi/install-pi.sh:21` pins the floor). `install-pi.sh`
  edits `main.conf` for `ControllerMode` and strips `DisablePlugins`, and
  **never touches `[GATT]`** — so 517/EATT-off apply.
- **Desktop**: BlueZ 5.72; `/etc/bluetooth/main.conf` has an empty `[GATT]`
  section — same defaults.
- `src/device.c:6491`: `dev->att_mtu = MIN(mtu, btd_opts.gatt_mtu);` then
  `bt_gatt_server_new(db, device->att, device->att_mtu, …)`.
- Desktop as client, `src/shared/gatt-client.c:2125`:
  `mtu = MAX(BT_ATT_DEFAULT_LE_MTU, mtu); if (mtu == BT_ATT_DEFAULT_LE_MTU) …
  else bt_gatt_exchange_mtu(client->att, mtu, …)` — so BlueZ **does** send
  `ATT_EXCHANGE_MTU_REQ` with 517 on connect.
- Pi as server, `src/shared/gatt-server.c:1511`:
  `final_mtu = MAX(MIN(client_rx_mtu, server->mtu), BT_ATT_DEFAULT_LE_MTU);`
  = `MAX(MIN(517, 517), 23)` = **517**.

⚠ **One wrinkle worth knowing.** `src/device.c:6492`:

```c
    attrib = g_attrib_new(io, cid == BT_ATT_CID ? BT_ATT_DEFAULT_LE_MTU :
                                                  dev->att_mtu, false);
```

On the *unenhanced* ATT channel the bearer **starts at 23** and only rises once
the exchange completes. A notification sent before that would be capped at 20
bytes. In our flow the exchange happens at connection setup, long before the
Desktop subscribes and writes — but it is the reason the answer is "517 after
negotiation", not "517".

### 1.6 What `bleak` receives

`bleak` on Linux uses BlueZ's `StartNotify` by default (since 3.0.2). BlueZ's
client side, `src/gatt-client.c:1454`:

```c
    /*
     * Even if the value didn't change, we want to send a PropertiesChanged
     * signal so that we propagate the notification/indication to
     * applications.
     */
```

So `bleak` receives **exactly the bytes that arrived over the air**, with no
de-duplication — two identical Acks in a row both reach `_handle_ack`. It does
no truncation or reassembly of its own.

`bleak`'s own Linux backend docs note that `AcquireNotify` is worth choosing
"in cases where there is a characteristic that needs to be read after the
notification is received to get the full data" — which is the truncation of
§1.1 acknowledged from the client side.

Sources:
[bleak Linux backend](https://bleak.readthedocs.io/en/latest/backends/linux.html),
[BlueZ `src/gatt-client.c`](https://github.com/bluez/bluez/blob/master/src/gatt-client.c)

---

## 2. What this repo already measured — and got wrong

Do not re-derive either of these.

**[#32](https://github.com/peterderkoala/zeropi.display/issues/32) — "the MTU
trap was wrong."** `push.py` used to call `client._backend._acquire_mtu()`, and
both the code and spec §10 trap #2 claimed it was "the only way past the
23-byte default MTU". **It negotiates nothing.** The kernel/BlueZ negotiates
the MTU when the link comes up; that call merely *reads* the already-negotiated
value into `client.mtu_size`. It was removed; `push.py` is public-API-only now.

That issue also produced **the one hard measurement in the notify direction**:
with the call gone and `bleak` reporting `mtu_size == 23`, 12 Daily Payloads
went through and each was answered by **an Ack of up to 194 bytes, arriving
whole**. Because a notification cannot be fragmented, a whole 194-byte Ack
proves `ATT_MTU ≥ 197`. Note the discriminating power is entirely in the
*Ack* — the write path proves nothing, since BlueZ will long-write a 390-byte
payload over a 23-byte MTU quite happily.

**[#67](https://github.com/peterderkoala/zeropi.display/issues/67) — the write
budget is 512, not 514.** 514 was *derived* from the MTU (517 − 3); ATT's
maximum attribute value length binds first. Bisected on hardware: 512 is Acked,
513 raises `INVALID_ATTRIBUTE_VALUE_LENGTH`. `push.py` now enforces
`MAX_PAYLOAD_BYTES = 512` itself.

⚠ **Read those two together before trusting anything derived here.** This
project's track record on MTU reasoning is two beliefs, both derived, both
wrong until someone measured. That is the reason §5 asks for a bench check
rather than declaring victory.

---

## 3. Is ADR-0001's 512-byte budget symmetric?

**Same number. Different mechanism. Weaker guarantee. Do not treat it as one
budget with two directions.**

| | Desktop → Pi (write) | Pi → Desktop (notify) |
|---|---|---|
| ATT operation | `ATT_PREPARE_WRITE_REQ` + `ATT_EXECUTE_WRITE_REQ` (long write), chosen by BlueZ | `ATT_HANDLE_VALUE_NTF`, single PDU |
| Fragmentation available? | **Yes** — the long-write procedure carries the value across PDUs | **No** — the spec defines none for notifications |
| Does the MTU bind? | **No.** BlueZ long-writes a 390-byte payload over a 23-byte MTU | **Yes.** Hard cap at `ATT_MTU − 3`, always |
| Hard ceiling | 512 (ATT max attribute value length, Vol 3 Part F §3.2.9) | 512 (BlueZ `MIN(BT_ATT_MAX_VALUE_LEN, len)` in `gatt-database.c`) |
| Effective budget | **512**, unconditionally | **`min(512, ATT_MTU − 3)`** — 512 only while ATT_MTU ≥ 515 |
| Over-budget failure | **Loud**: `INVALID_ATTRIBUTE_VALUE_LENGTH` raised at the writer, measured (#67) | **Silent**: truncated twice with no error; the Desktop sees clipped JSON |
| Enforced in code? | Yes — `push.py`'s `MAX_PAYLOAD_BYTES` | **No.** Nothing on either end checks the Ack |

So the honest answer to the ticket's question:

- **Numerically symmetric** in the deployed Linux↔Linux configuration: 512 both
  ways.
- **Structurally asymmetric**, in three ways that matter for a management
  surface:
  1. the notify budget is **MTU-dependent** and the write budget is not, so a
     future non-BlueZ Desktop (iOS negotiates 185 → 182 usable; an
     Android/other stack that never exchanges MTU → 20) shrinks the notify
     budget with nothing in this repo noticing;
  2. there is **no fallback** — the write direction's "too big" has a
     fragmentation procedure sitting underneath it, the notify direction has
     nothing;
  3. the failure is **silent**, which is strictly worse than #67's named error.
     A too-long Ack arrives as truncated JSON, so the only symptom is
     `push.py:_handle_ack`'s `{"status": "error", "reason": "malformed ack
     from Pi: …"}` — a message that blames the JSON, not the length.

**ADR-0001 is confirmed for its own direction and does not extend to this one
by default.** If map #70 widens the Ack, that is a new decision needing its own
record, not an inherited budget.

---

## 4. Options if the budget is tight

Ranked by what they actually buy.

**1. Stay under the budget, and *enforce* it on the Pi. — recommended.**
Mirror `push.py`'s `MAX_PAYLOAD_BYTES` in `receive.py` immediately before
`characteristic.set_value(...)`: if the encoded Ack exceeds the budget, send a
short, well-formed error Ack instead of letting BlueZ clip a valid one into
invalid JSON. This is a few lines and it converts the single genuinely nasty
failure mode into a named one. Given §1.3, **nothing else on the path will ever
tell you.**

Headroom today: the largest Ack measured is **194 bytes** (#32); the worst-case
Ack the current `build_ack()` shape can produce — `status: "error"` with every
optional field present, a 159-character `project` (ADR-0003's worst case) and a
long `reason` — is roughly **360 bytes**. Against 512 that leaves ~150 bytes
worst-case and ~320 typical for #70 to spend. That is a real budget for a
status surface, but it is not a large one.

**2. A separate *read* characteristic for anything large. — the spec's own
answer.** Vol 3 Part F §3.4.7.1's note says it outright: to get a long
attribute, the client uses `ATT_READ_BLOB_REQ`. A read characteristic gets the
**full 512 bytes regardless of the MTU**, because the read-blob procedure
fragments. `bleak`'s `read_gatt_char()` → BlueZ `ReadValue` performs the long
read transparently. The idiom is: notify a small "status changed" ping, let the
Desktop read the detail. Costs one extra round trip and one more characteristic;
buys MTU-independence and a loud failure mode.

**3. Indications instead of notifications. — buys reliability, not size.**
Verified twice over: Core Spec §3.4.7.2 caps `ATT_HANDLE_VALUE_IND` at exactly
the same `ATT_MTU − 3`, and BlueZ's `bt_gatt_server_send_indication()`
truncates identically. What an indication adds is a client `ATT_HANDLE_VALUE_CFM`
confirmation, one outstanding at a time. Note the choice is **not the Pi's** as
things stand: `gatt-database.c:1451` picks notify vs indicate from the CCC bit
the *client* wrote, and `receive.py:727` declares `flags=["notify"]` only, so
the Desktop can only ever subscribe to notifications. Switching would be a
declaration change on the Pi.

**4. Explicit MTU negotiation. — there is nothing to negotiate.** This is the
#32 trap restated. No application API on either side negotiates the MTU;
`bleak.mtu_size` is read-only and BlueZ owns the exchange. The only knob is
`[GATT] ExchangeMTU` in `main.conf`, **already at its maximum of 517** by
default. `ATT_MTU` can never exceed 517, so `ATT_MTU − 3` can never exceed 514,
so 512 binds regardless. **This option cannot raise the budget by even one
byte.**

**5. Several notifications composing one logical reply. — this is chunking.**
Rejected by ADR-0001 and again by ADR-0003, and the reasons are stronger in
this direction: notifications are unacknowledged at the ATT layer, so a
multi-notification reply needs sequence numbers, a reassembly buffer on the
Desktop, a timeout, and partial-state cleanup — the exact three costs ADR-0003
credited itself with avoiding. If it is ever unavoidable, use **indications**
so each fragment is confirmed. Option 2 gets the same result for far less.

**Not an option: `ATT_MULTIPLE_HANDLE_VALUE_NTF`** (opcode 0x23, BT 5.3).
It packs several handle-value tuples into one PDU, still bounded by
`ATT_MTU − 1` in total, and BlueZ only uses it when the client has set the
`NFY_MULTI` client-features bit (`gatt-database.c:1451`). It raises no ceiling.

---

## 5. Confidence, and what still needs a bench check

| Claim | Evidence | Confidence |
|---|---|---|
| Notification payload is capped at `ATT_MTU − 3`, no fragmentation exists | Core Spec §3.4.7.1 + BlueZ `gatt-server.c` | **Certain** |
| Indications share the identical cap | Core Spec §3.4.7.2 + BlueZ `gatt-server.c` | **Certain** |
| BlueZ silently truncates a D-Bus notification value at 512 | BlueZ `gatt-database.c` source, read directly | **Certain** |
| `bluezero` and `bleak` add no cap or check of their own | Both sources read directly | **Certain** |
| Default ATT_MTU is 23 (≈20 usable) with no exchange | Core Spec Vol 3 Part G §5.2.1 | **Certain** |
| The budget is **at least 194 bytes** on this hardware | #32, measured: a 194-byte Ack arrived whole | **Measured** |
| The negotiated ATT_MTU on this link is **517**, so the budget is **512** | ~~Derived from BlueZ defaults + both machines' `main.conf` + four source paths~~ → **confirmed on the wire, [§6](#6-bench-confirmation-78)** | **Measured** |
| Notification overhead is `ATT_MTU − 3` | ⚠ **Refuted for this link** — BlueZ uses opcode `0x23`, overhead 5. [§6](#6-bench-confirmation-78) | **Corrected** |

⚠ **The last row is the one to distrust**, and this project has earned that
caution twice (§2). It is also the row the design depends on: if the real MTU
is, say, 247, the budget is 244 and a widened Ack silently loses its tail.

> **Resolved by [#78](https://github.com/peterderkoala/zeropi.display/issues/78).**
> The distrust was warranted and it paid out — not on the MTU, which was
> exactly 517 as derived, but on the overhead term beside it.

**The measurement that closes it** — one connection, no panel interaction, no
GPIO:

- **Desktop side, one line:** during a normal `push.py` run against the Pi,
  read the negotiated value. `bleak` only populates `client.mtu_size` on BlueZ
  once something acquires it, so either call
  `await client._backend._acquire_mtu()` **in a throwaway probe script** (never
  in `push.py` — #32 removed it deliberately) and print `client.mtu_size`, or
- **Pi side, no code change:** `sudo btmon` on the Pi while the Desktop
  connects, and read the `ATT: Exchange MTU Request/Response` pair off the
  trace.
- **Confirming the cap itself:** have the Pi emit a deliberately oversized Ack
  (e.g. 600 bytes of padding on a `kind: "probe"` Payload, which `receive.py`
  already rejects before any DB write — the same technique #67 used) and record
  how many bytes reach `_handle_ack`. If the answer is 512, both derivations
  hold at once.

⚠ **`receive.py` owns the panel** — stop `zeropi-display` before running
anything Pi-side, per spec §10.

---

## 6. Bench confirmation (#78)

Run 2026-09-10 against the dev Pi (`B8:27:EB:7C:97:0F`) from the Desktop
(`70:A8:D3:3B:EC:8B`), with `zeropi-display` **left running**.

### Method — no Pi-side code change was needed

§5 proposed emitting an oversized Ack via a `kind: "probe"` Payload, which
would have meant touching the Pi. It turned out to be unnecessary:
`parse_payload` echoes the rejected `kind` straight back into the Ack's reason
(`f"unknown kind: {kind!r}"`), so a long `kind` inside a ≤512-byte Payload
produces an arbitrarily long Ack **in the other direction**. Rejection happens
before any DB write or panel draw, so this drives the live service while
touching neither `readings` nor the GPIO — `data.db` was `md5sum`-identical
before and after.

The lever is worth remembering: **any field the Pi echoes into an error Ack is
an amplifier**, and it is the cheapest way to test this direction.

### Result 1 — the MTU exchange is real, and it is 517

`btmon` on the Pi, during the connection:

```
ATT: Exchange MTU Request (0x02) len 2
  Client RX MTU: 517
ATT: Exchange MTU Response (0x03) len 2
  Server RX MTU: 517
```

So 517 is **negotiated**, not merely a config default nothing exercises — which
was the specific doubt §1.5 raised. `bleak`'s `client.mtu_size` agreed: 517.

### Result 2 — the budget is exactly 512, bisected

| Ack the Pi built | Payload sent | Bytes that arrived | Result |
|---|---|---|---|
| 200 | 131 | 200 | parses |
| 505 | 436 | 505 | parses |
| 510 | 441 | 510 | parses |
| 511 | 442 | 511 | parses |
| **512** | 443 | **512** | **parses** |
| **513** | 444 | **512** | **`JSONDecodeError`** |
| 514 | 445 | 512 | `JSONDecodeError` |
| 520 | 451 | 512 | `JSONDecodeError` |
| 540 | 471 | 512 | `JSONDecodeError` |
| 560 | 491 | 512 | `JSONDecodeError` |

The boundary is exact and there is no error at either end — `bluetoothd`
returned success, `bluezero` raised nothing, and the Pi's journal logged a
normal rejection. **Silent truncation confirmed.**

### Result 3 — the opcode is `0x23`, and the overhead is 5, not 3

The finding this ticket existed to catch. BlueZ does **not** use the classic
`Handle Value Notification` (`0x1b`) assumed throughout §1. It uses:

```
ATT: Handle Multiple Value Notification (0x23) len 516
  Length: 0x0200                                  ← 512, the value length
  Handle: 0x0029 Type: Vendor specific (08c89458-…)
```

`0x23` carries a **per-value length field**, so its layout is
`opcode(1) + handle(2) + length(2) + value`, giving `ATT_MTU − 5`, not
`ATT_MTU − 3`. At the ceiling the whole PDU is `1 + 516 = 517` — exactly the
negotiated MTU, which is the clean confirmation that this is what bound.

⚠ **On this link the correction changes nothing, and that is the danger.**
`ATT_MTU − 5 = 512` and `gatt-database.c`'s clamp is also 512, so both terms of
the `min()` land on the same number and the two-byte error is invisible. At any
smaller MTU it separates: at 247 the old formula says 244, the truth is 242.

**The two bounds cannot be distinguished on this link** — both evaluate to 512,
and no larger MTU is reachable (517 is the maximum). Recording that honestly
rather than picking one: the *number* is measured, the *mechanism* behind it is
still one of two.

### Result 4 — the Desktop blames the JSON, as predicted

Fed the truncated bytes to `push.py`'s own `_handle_ack`:

```
{"status": "error",
 "reason": "malformed ack from Pi: Expecting ',' delimiter: line 1 column 513 (char 512)"}
```

§1.3's prediction holds exactly. Note the one diagnostic thread available: the
column number **is** the budget. A guardrail that recognises `char 512` could
turn this into a length error, but nothing does that today.

### What this means for the design

Budget **512 bytes, measured**. Against the known Ack sizes — 194 B measured
maximum, ~360 B worst case — [#74](https://github.com/peterderkoala/zeropi.display/issues/74)
has roughly **150 bytes of real headroom** for a widened Ack. That is enough to
design against, and it is now a number someone has seen.

---

## Sources

Primary, all read directly:

- Bluetooth Core Specification 6.0, Vol 3 Part F (Attribute Protocol) —
  §3.2.8 Exchanging MTU size, §3.2.9 Long attribute values, §3.4.7.1
  `ATT_HANDLE_VALUE_NTF`, §3.4.7.2 `ATT_HANDLE_VALUE_IND`.
  <https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/Core-60/out/en/host/attribute-protocol--att-.html>
- Bluetooth Core Specification 6.0, Vol 3 Part G (GATT) — §5.2.1 ATT_MTU
  (Table 5.1), §5.3.1 Enhanced ATT bearer ATT_MTU.
  <https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/Core-60/out/en/host/generic-attribute-profile--gatt-.html>
- BlueZ source, `master` and tag `5.82`: `src/gatt-database.c`,
  `src/shared/gatt-server.c`, `src/shared/gatt-client.c`, `src/gatt-client.c`,
  `src/shared/att-types.h`, `src/device.c`, `src/main.c`, `src/main.conf`.
  <https://github.com/bluez/bluez>
- python-bluezero, `bluezero/localGATT.py`.
  <https://github.com/ukBaz/python-bluezero>
- bleak, Linux backend documentation.
  <https://bleak.readthedocs.io/en/latest/backends/linux.html>

In-repo:

- `docs/adr/0001-single-write-payload-no-chunking.md`,
  `docs/adr/0003-one-write-per-reading.md` (incl. its #67 update)
- `pi/receive.py` (`build_ack`, `send_ack`, characteristic declaration),
  `desktop/push.py` (`MAX_PAYLOAD_BYTES`, `_handle_ack`)
- `pi/install-pi.sh` (BlueZ 5.82 floor; `main.conf` edits)
- Issues #32 and #67, and `handoff/handoff.md`
