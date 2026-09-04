"""Pi (BLE peripheral): dumb receiver for the Payload GATT service.

Advertises the milestone-1 GATT service, accepts a single-write Payload,
persists it as a Reading in SQLite, and returns a JSON Ack over the notify
characteristic. See ../pi-eink-ble-concept.md and
../docs/adr/0002-readings-persisted-on-pi.md for the design this implements.
"""

import json
import sqlite3
from datetime import datetime, timezone

from bluezero import adapter
from bluezero import async_tools
from bluezero import device
from bluezero import peripheral

SERVICE_UUID = "abbac370-5a95-490d-a1fc-921c1c95300d"
WRITE_CHARACTERISTIC_UUID = "014ca0e2-c76c-4443-a755-e5a1ad25368d"
NOTIFY_CHARACTERISTIC_UUID = "08c89458-52f1-47eb-ab58-f7f7995d8efb"

DB_PATH = "/opt/zeropi-display/data.db"

REQUIRED_PAYLOAD_FIELDS = ("date", "usage_tokens", "oneliner")


def init_db(db_path: str = DB_PATH) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                usage_tokens INTEGER NOT NULL,
                oneliner TEXT NOT NULL,
                received_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def save_reading(payload: dict, received_at: str, db_path: str = DB_PATH) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO readings (date, usage_tokens, oneliner, received_at)"
            " VALUES (?, ?, ?, ?)",
            (payload["date"], payload["usage_tokens"], payload["oneliner"], received_at),
        )
        conn.commit()
    finally:
        conn.close()


def parse_payload(raw_value: list) -> dict:
    text = bytes(raw_value).decode("utf-8")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object, got {type(payload).__name__}")
    missing = [field for field in REQUIRED_PAYLOAD_FIELDS if field not in payload]
    if missing:
        raise ValueError(f"missing field(s): {', '.join(missing)}")
    return payload


def build_ack(status: str, reason: str = None) -> dict:
    ack = {
        "status": status,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    if reason is not None:
        ack["reason"] = reason
    return ack


class ReceiveState:
    ack_characteristic = None

    @classmethod
    def on_connect(cls, ble_device: device.Device) -> None:
        print(f"Connected to {ble_device.address}")

    @classmethod
    def on_disconnect(cls, adapter_address: str, device_address: str) -> None:
        print(f"Disconnected from {device_address}")

    @classmethod
    def on_ack_notify(cls, notifying: bool, characteristic) -> None:
        cls.ack_characteristic = characteristic if notifying else None

    @classmethod
    def send_ack(cls, ack: dict) -> None:
        if cls.ack_characteristic is None:
            return
        characteristic = cls.ack_characteristic

        def _notify() -> bool:
            characteristic.set_value(list(json.dumps(ack).encode("utf-8")))
            return False

        # Deferred: notifying from inside the write's own D-Bus call
        # confuses BlueZ's ATT state machine (the pending write reply
        # races the notification), so send it on the next event-loop
        # iteration instead.
        async_tools.add_timer_ms(0, _notify)

    @classmethod
    def on_write(cls, value: list, options: dict) -> None:
        try:
            payload = parse_payload(value)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            print(f"Rejected malformed payload: {exc}")
            cls.send_ack(build_ack("error", reason=str(exc)))
            return

        print(f"Received: {payload}")
        received_at = datetime.now(timezone.utc).isoformat()
        try:
            save_reading(payload, received_at)
        except sqlite3.Error as exc:
            print(f"Failed to persist reading: {exc}")
            cls.send_ack(build_ack("error", reason=f"db write failed: {exc}"))
            return

        cls.send_ack({"status": "ok", "received_at": received_at})


def main(adapter_address: str) -> None:
    init_db()

    ble_receiver = peripheral.Peripheral(adapter_address, local_name="zeropi-display")
    ble_receiver.add_service(srv_id=1, uuid=SERVICE_UUID, primary=True)
    ble_receiver.add_characteristic(
        srv_id=1,
        chr_id=1,
        uuid=WRITE_CHARACTERISTIC_UUID,
        value=[],
        notifying=False,
        flags=["write"],
        write_callback=ReceiveState.on_write,
        read_callback=None,
        notify_callback=None,
    )
    ble_receiver.add_characteristic(
        srv_id=1,
        chr_id=2,
        uuid=NOTIFY_CHARACTERISTIC_UUID,
        value=[],
        notifying=False,
        flags=["notify"],
        notify_callback=ReceiveState.on_ack_notify,
        read_callback=None,
        write_callback=None,
    )

    ble_receiver.on_connect = ReceiveState.on_connect
    ble_receiver.on_disconnect = ReceiveState.on_disconnect

    ble_receiver.publish()


if __name__ == "__main__":
    main(list(adapter.Adapter.available())[0].address)
