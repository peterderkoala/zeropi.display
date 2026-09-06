"""Desktop (BLE central): one-shot push of a Payload to the Pi.

Scans for the Pi by its advertised service UUID, writes the milestone-1
test Payload in a single write (no chunking — see
../docs/adr/0001-single-write-payload-no-chunking.md), waits for the Ack on
the notify characteristic, and prints the result. Run manually; no polling
loop. See ../pi-eink-ble-concept.md for the design this implements.
"""

import asyncio
import json
from datetime import date

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

SERVICE_UUID = "abbac370-5a95-490d-a1fc-921c1c95300d"
WRITE_CHARACTERISTIC_UUID = "014ca0e2-c76c-4443-a755-e5a1ad25368d"
NOTIFY_CHARACTERISTIC_UUID = "08c89458-52f1-47eb-ab58-f7f7995d8efb"

SCAN_TIMEOUT_SECONDS = 10.0
ACK_TIMEOUT_SECONDS = 10.0


def build_test_payload() -> dict:
    return {
        "date": date.today().isoformat(),
        "usage_tokens": 12345,
        "oneliner": "test message",
    }


def matches_service(device: BLEDevice, advertisement: AdvertisementData) -> bool:
    return SERVICE_UUID.lower() in [uuid.lower() for uuid in advertisement.service_uuids]


async def find_pi(timeout: float = SCAN_TIMEOUT_SECONDS) -> BLEDevice:
    device = await BleakScanner.find_device_by_filter(matches_service, timeout=timeout)
    if device is None:
        raise RuntimeError(
            f"No device advertising service {SERVICE_UUID} found within {timeout}s"
        )
    return device


async def push_payload(payload: dict, device: BLEDevice) -> dict:
    ack_received = asyncio.Event()
    ack: dict = {}

    def handle_ack(_: BleakGATTCharacteristic, data: bytearray) -> None:
        nonlocal ack
        try:
            ack = json.loads(bytes(data).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            ack = {"status": "error", "reason": f"malformed ack from Pi: {exc}"}
        ack_received.set()

    async with BleakClient(device) as client:
        # bleak's public start_notify() only uses BlueZ's low-MTU StartNotify
        # call unless the remote characteristic already advertises
        # "NotifyAcquired" (ours, served by bluezero, never does). Acquiring
        # the MTU directly is the only way to negotiate past the 23-byte
        # default, which the single-write Payload (see
        # ../docs/adr/0001-single-write-payload-no-chunking.md) needs to fit
        # in one write.
        await client._backend._acquire_mtu()
        await client.start_notify(NOTIFY_CHARACTERISTIC_UUID, handle_ack)
        print(f"Connected to {device.address} (negotiated MTU: {client.mtu_size})")
        # No explicit stop_notify: exiting this context manager disconnects,
        # which implicitly stops notifications. An explicit stop_notify()
        # here would re-resolve the characteristic against client.services,
        # which raises a misleading BleakError ("Service Discovery has not
        # been performed yet") when the real failure happened before
        # discovery completed — masking the actual exception (#12).
        body = json.dumps(payload).encode("utf-8")
        await client.write_gatt_char(WRITE_CHARACTERISTIC_UUID, body, response=True)
        await asyncio.wait_for(ack_received.wait(), timeout=ACK_TIMEOUT_SECONDS)

    return ack


async def main() -> None:
    payload = build_test_payload()
    print(f"Pushing payload: {payload}")

    device = await find_pi()
    print(f"Found {device.name or device.address} ({device.address})")

    ack = await push_payload(payload, device)
    print(f"Ack: {ack}")
    if ack.get("status") != "ok":
        raise SystemExit(f"Pi reported failure: {ack}")


if __name__ == "__main__":
    asyncio.run(main())
