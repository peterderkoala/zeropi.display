"""Suite-wide guards.

Spec §10: the suite must stay green with no panel, no SPI, no `bluezero`, no
`~/.claude` and no Pi — and, since #84, without touching the real BLE lock
either. `push.BLE_LOCK_PATH` points into the developer's own
`~/.local/state`, where a test taking it for real would contend with a live
`zeropi-push` service on the same machine.
"""

from __future__ import annotations

import datetime as _dt

import pytest


@pytest.fixture(autouse=True)
def isolated_ble_lock(tmp_path, monkeypatch):
    import push

    monkeypatch.setattr(push, "BLE_LOCK_PATH", tmp_path / "ble.lock")


PINNED_TODAY = _dt.date(2026, 9, 5)


class _PinnedDate(_dt.date):
    """`date` with `today()` fixed at `PINNED_TODAY`."""

    @classmethod
    def today(cls):
        return cls(PINNED_TODAY.year, PINNED_TODAY.month, PINNED_TODAY.day)


@pytest.fixture
def pin_today(monkeypatch):
    """Fixes `usage`'s idea of today at 2026-09-05, the date the fixed-date
    store fixtures were written against. `pending_readings` keeps only the
    seven-day Window ending `date.today()` (§4.6), so without this a fixture
    date silently drops out of the Batch as the calendar advances, and
    assertions about what was sent start failing (they did, on 2026-09-11)."""
    import usage

    monkeypatch.setattr(usage, "date", _PinnedDate)
