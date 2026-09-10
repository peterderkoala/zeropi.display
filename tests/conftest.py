"""Suite-wide guards.

Spec §10: the suite must stay green with no panel, no SPI, no `bluezero`, no
`~/.claude` and no Pi — and, since #84, without touching the real BLE lock
either. `push.BLE_LOCK_PATH` points into the developer's own
`~/.local/state`, where a test taking it for real would contend with a live
`zeropi-push` service on the same machine.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_ble_lock(tmp_path, monkeypatch):
    import push

    monkeypatch.setattr(push, "BLE_LOCK_PATH", tmp_path / "ble.lock")
