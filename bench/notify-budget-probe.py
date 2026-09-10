"""#78 bench probe: confirm the Pi->Desktop notify budget on hardware.

Drives the LIVE `receive.py` with no Pi-side code change. Every Payload here
has an unrecognised `kind`, which `parse_payload` rejects BEFORE any DB write
or panel draw -- so this touches neither `readings` nor the GPIO.

The lever: `parse_payload` echoes the rejected `kind` back into the Ack's
`reason` (`f"unknown kind: {kind!r}"`), so a long `kind` in a <=512-byte
Payload produces an arbitrarily long Ack in the other direction.
"""
import asyncio
import json
import sys

sys.path.insert(0, "desktop")

from bleak import BleakClient, BleakScanner  # noqa: E402

SERVICE_UUID = "abbac370-5a95-490d-a1fc-921c1c95300d"
WRITE_CHARACTERISTIC_UUID = "014ca0e2-c76c-4443-a755-e5a1ad25368d"
NOTIFY_CHARACTERISTIC_UUID = "08c89458-52f1-47eb-ab58-f7f7995d8efb"


def expected_ack_len(n: int) -> int:
    """Byte length of the Ack the Pi will build for a `kind` of n 'A's.

    Replicates receive.py's build_ack() + the PayloadError reason format
    exactly, so we know what the Pi SENT and can compare with what arrived.
    """
    kind = "A" * n
    ack = {
        "status": "error",
        "drawn": False,
        "wiped": False,
        "reason": f"unknown kind: {kind!r}",
    }
    return len(json.dumps(ack).encode("utf-8"))


BASE = expected_ack_len(0)  # each extra 'A' adds exactly one byte


def n_for_ack(target: int) -> int:
    return target - BASE


async def main() -> int:
    print(f"Ack base length (n=0): {BASE} bytes; each 'A' adds 1.")

    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: SERVICE_UUID.lower() in [u.lower() for u in ad.service_uuids],
        timeout=20.0,
    )
    if device is None:
        print("FAIL: no device advertising the service")
        return 1
    print(f"Found {device.address}")

    async with BleakClient(device) as client:
        # ---- Step 1 (Desktop side): the negotiated ATT_MTU --------------
        mtu = None
        try:
            await client._backend._acquire_mtu()
            mtu = client.mtu_size
        except Exception as exc:  # noqa: BLE001
            print(f"  _acquire_mtu failed: {exc!r}")
            try:
                mtu = client.mtu_size
            except Exception as exc2:  # noqa: BLE001
                print(f"  mtu_size unavailable: {exc2!r}")
        print(f"NEGOTIATED ATT_MTU (Desktop view): {mtu}")
        if mtu:
            print(f"  => derived notify budget = ATT_MTU-3 = {mtu - 3}")

        received: dict = {}
        got = asyncio.Event()

        def on_notify(_c, data: bytearray) -> None:
            received["raw"] = bytes(data)
            got.set()

        await client.start_notify(NOTIFY_CHARACTERISTIC_UUID, on_notify)

        # ---- Steps 2-4: sweep the Ack size across the predicted 512 -----
        targets = [200, 505, 510, 511, 512, 513, 514, 520, 540, 560]
        rows = []
        for target in targets:
            n = n_for_ack(target)
            payload = json.dumps({"kind": "A" * n}).encode("utf-8")
            if len(payload) > 512:
                print(f"  skip target={target}: payload {len(payload)}B over ATT limit")
                continue

            received.clear()
            got.clear()
            try:
                await client.write_gatt_char(
                    WRITE_CHARACTERISTIC_UUID, payload, response=True
                )
            except Exception as exc:  # noqa: BLE001
                rows.append((target, len(payload), None, f"write failed: {exc!r}"))
                continue

            try:
                await asyncio.wait_for(got.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                rows.append((target, len(payload), None, "NO ACK (timeout)"))
                continue

            raw = received["raw"]
            # Exactly what push.py's _handle_ack does with these bytes.
            try:
                json.loads(raw.decode("utf-8"))
                verdict = "parses"
            except Exception as exc:  # noqa: BLE001
                verdict = f"MALFORMED: {type(exc).__name__}"
            rows.append((target, len(payload), len(raw), verdict))
            await asyncio.sleep(0.3)

        await client.stop_notify(NOTIFY_CHARACTERISTIC_UUID)

    print()
    print(f"{'Pi sent':>8} {'payload':>8} {'arrived':>8}  verdict")
    print("-" * 58)
    for sent, plen, arrived, verdict in rows:
        a = "-" if arrived is None else str(arrived)
        print(f"{sent:>8} {plen:>8} {a:>8}  {verdict}")

    print()
    truncated = [r for r in rows if r[2] is not None and r[2] < r[0]]
    if truncated:
        caps = {r[2] for r in truncated}
        print(f"TRUNCATION OBSERVED. Arrived length(s) when over budget: {sorted(caps)}")
    else:
        print("NO TRUNCATION OBSERVED in the sampled range.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
