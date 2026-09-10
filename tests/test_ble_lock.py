"""Tests for the Desktop's BLE lock and Settings re-assertion (#84).

Spec: `docs/spec-management-surface.md` §7.1 (the lock) and §7.2
(re-assertion), tested per §10.5 — `flock` needs no second machine, two file
descriptors in one test process are enough, and the whole `_with_ble_connection`
seam is driven here with a fake radio (`push.find_pi` / `push._open_client`
monkeypatched) so no `bleak` and no Pi are involved.
"""

from __future__ import annotations

import asyncio
import errno
import fcntl
import json
import os

import pytest

import push


# ---------------------------------------------------------------------------
# §7.1 The lock itself
# ---------------------------------------------------------------------------


def _hold(path) -> int:
    """Takes the lock the way another process would, and returns the fd."""
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def test_lock_is_created_with_mode_0600(tmp_path):
    path = tmp_path / "state" / "ble.lock"

    async def go():
        async with push.ble_lock(path=path, wait_s=0.0):
            pass

    asyncio.run(go())

    assert path.exists()
    assert os.stat(path).st_mode & 0o777 == push.BLE_LOCK_MODE


def test_second_acquirer_gives_up_at_the_bound(tmp_path):
    path = tmp_path / "ble.lock"
    fd = _hold(path)
    slept: list[float] = []
    clock = {"t": 0.0}

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["t"] += seconds

    async def go():
        async with push.ble_lock(
            path=path,
            wait_s=15.0,
            poll_interval_s=1.0,
            sleep_fn=fake_sleep,
            monotonic_fn=lambda: clock["t"],
        ):
            pass

    try:
        with pytest.raises(push.BleLinkBusy):
            asyncio.run(go())
    finally:
        os.close(fd)

    # It waited — and stopped at the bound rather than spinning forever.
    assert slept
    assert sum(slept) <= 15.0
    assert clock["t"] >= 15.0


def test_service_path_fails_immediately_without_waiting(tmp_path):
    path = tmp_path / "ble.lock"
    fd = _hold(path)
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:  # pragma: no cover - must not run
        slept.append(seconds)

    async def go():
        async with push.ble_lock(path=path, wait_s=0.0, sleep_fn=fake_sleep):
            pass

    try:
        with pytest.raises(push.BleLinkBusy):
            asyncio.run(go())
    finally:
        os.close(fd)

    # The service's path is distinct from the CLI's: it never sleeps at all.
    assert slept == []


def test_waiter_acquires_when_the_holder_releases(tmp_path):
    path = tmp_path / "ble.lock"
    fd = _hold(path)
    clock = {"t": 0.0}
    ticks = {"n": 0}

    async def fake_sleep(seconds: float) -> None:
        clock["t"] += seconds
        ticks["n"] += 1
        if ticks["n"] == 2:
            fcntl.flock(fd, fcntl.LOCK_UN)

    acquired = []

    async def go():
        async with push.ble_lock(
            path=path,
            wait_s=15.0,
            poll_interval_s=1.0,
            sleep_fn=fake_sleep,
            monotonic_fn=lambda: clock["t"],
        ):
            acquired.append(True)

    try:
        asyncio.run(go())
    finally:
        os.close(fd)

    assert acquired == [True]


def test_lock_is_released_even_when_the_body_raises(tmp_path):
    path = tmp_path / "ble.lock"

    async def go():
        async with push.ble_lock(path=path, wait_s=0.0):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        asyncio.run(go())

    # Nothing holds it any more, so a fresh acquirer succeeds immediately.
    fd = _hold(path)
    os.close(fd)


# ---------------------------------------------------------------------------
# A fake radio for the `_with_ble_connection` seam
# ---------------------------------------------------------------------------


class FakeClient:
    """Enough of a `BleakClient` for `BleConnection`: `start_notify` keeps
    the callback, and every `write_gatt_char` answers with one Ack."""

    def __init__(self, acks):
        self._acks = list(acks)
        self.writes: list[dict] = []
        self._callback = None

    async def start_notify(self, _uuid, callback):
        self._callback = callback

    async def write_gatt_char(self, _uuid, body, response=True):
        self.writes.append(json.loads(bytes(body).decode("utf-8")))
        ack = self._acks.pop(0) if self._acks else {"status": "ok"}
        if ack is not None:
            self._callback(None, bytearray(json.dumps(ack).encode("utf-8")))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


class FakeDevice:
    name = "zeropi"
    address = "AA:BB:CC:DD:EE:FF"


def _fake_radio(monkeypatch, acks):
    client = FakeClient(acks)

    async def fake_find_pi(*a, **kw):
        return FakeDevice()

    monkeypatch.setattr(push, "find_pi", fake_find_pi)
    monkeypatch.setattr(push, "_open_client", lambda device: client)
    return client


# ---------------------------------------------------------------------------
# §7.2 Settings re-assertion
# ---------------------------------------------------------------------------


def test_settings_payload_is_the_complete_set_nested_under_settings():
    payload = push.build_settings_payload({"idle_keepalive_s": 86400}, "deadbeefdeadbeef")

    assert payload == {
        "kind": "settings",
        "desktop_id": "deadbeefdeadbeef",
        "settings": {"idle_keepalive_s": 86400},
    }


def test_reassertion_precedes_the_work_the_connection_was_opened_for(tmp_path, monkeypatch):
    client = _fake_radio(monkeypatch, [{"status": "ok", "kind": "settings", "wiped": False}])

    async def work(send_one):
        await send_one({"kind": "daily", "date": "2026-09-10"})
        return "done"

    async def go():
        return await push._with_ble_connection(
            work,
            settings_payload=push.build_settings_payload({"idle_keepalive_s": 86400}, "abc"),
            lock_wait_s=0.0,
        )

    assert asyncio.run(go()) == "done"
    assert [w["kind"] for w in client.writes] == ["settings", "daily"]


def test_failed_reassertion_logs_and_continues(tmp_path, monkeypatch, capsys):
    client = _fake_radio(
        monkeypatch,
        [{"status": "error", "kind": "settings", "reason": "unknown key"}],
    )

    async def work(send_one):
        await send_one({"kind": "daily", "date": "2026-09-10"})
        return "done"

    async def go():
        return await push._with_ble_connection(
            work,
            settings_payload=push.build_settings_payload({"idle_keepalive_s": 86400}, "abc"),
            lock_wait_s=0.0,
        )

    assert asyncio.run(go()) == "done"
    assert [w["kind"] for w in client.writes] == ["settings", "daily"]
    assert "unknown key" in capsys.readouterr().out


def test_a_cli_initiated_settings_write_fails_on_the_same_error(tmp_path, monkeypatch):
    _fake_radio(monkeypatch, [{"status": "error", "kind": "settings", "reason": "unknown key"}])

    async def go():
        return await push._with_ble_connection(
            None,
            settings_payload=push.build_settings_payload({"idle_keepalive_s": 86400}, "abc"),
            settings_required=True,
            lock_wait_s=0.0,
        )

    with pytest.raises(push.SettingsAssertionError):
        asyncio.run(go())


def test_failed_reassertion_does_not_fail_the_batch_or_the_exit_code(tmp_path, monkeypatch):
    """§7.2: a flaky Setting must not start failing Batches."""
    store_path = tmp_path / "usage.db"
    _build_store_with_one_pending_reading(store_path)

    client = _fake_radio(
        monkeypatch,
        [
            None,  # the Settings write gets no Ack at all
            {"status": "ok", "kind": "daily", "wiped": False},
        ],
    )
    monkeypatch.setattr(push, "ACK_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(push, "desktop_id", lambda *a, **kw: "abc")

    async def go():
        return await push.run_batch_pass(
            str(store_path),
            projects_root=tmp_path / "empty-projects",
            settings={"idle_keepalive_s": 86400},
            lock_wait_s=0.0,
        )

    result = asyncio.run(go())

    assert [w["kind"] for w in client.writes] == ["settings", "daily"]
    assert result.failed == 0
    assert result.sent == 1
    assert result.ok


def test_a_wiped_settings_ack_still_triggers_the_extra_batch_pass(tmp_path, monkeypatch):
    """The re-assertion is the first write of the connection, so it is the
    write a Desktop Id hand-off wipes on — that flag must not be dropped."""
    store_path = tmp_path / "usage.db"
    _build_store_with_one_pending_reading(store_path)

    client = _fake_radio(
        monkeypatch,
        [
            {"status": "ok", "kind": "settings", "wiped": True},
            {"status": "ok", "kind": "daily", "wiped": False},
            {"status": "ok", "kind": "daily", "wiped": False},
        ],
    )
    monkeypatch.setattr(push, "desktop_id", lambda *a, **kw: "abc")

    async def go():
        return await push.run_batch_pass(
            str(store_path),
            projects_root=tmp_path / "empty-projects",
            settings={"idle_keepalive_s": 86400},
            lock_wait_s=0.0,
        )

    result = asyncio.run(go())

    assert result.wiped is True
    # The one Reading is re-sent by the extra pass §7.2 requires.
    assert [w["kind"] for w in client.writes] == ["settings", "daily", "daily"]
    assert result.sent == 2


def _build_store_with_one_pending_reading(store_path):
    from datetime import date

    import usage

    store = usage.open_store(store_path)
    try:
        store.execute(
            "INSERT INTO entries (request_id, message_id, session_id, project_key,"
            " local_date, model, input_tokens, output_tokens, cache_write_5m_tokens,"
            " cache_write_1h_tokens, cache_read_tokens, web_search_requests, cost_usd,"
            " cost_complete, rank_sidechain, rank_tokens, rank_speed, source_file,"
            " source_end_offset, pushed_at)"
            " VALUES ('r', 'm', 's', 'proj', ?, 'sonnet', 1, 1, 0, 0, 0, 0, 0.1,"
            " 1, 0, 0, 0, 'f.jsonl', 1, NULL)",
            (date.today().isoformat(),),
        )
        store.commit()
    finally:
        store.close()


# ---------------------------------------------------------------------------
# §7.1 What a caller observes on a busy link — the distinction must survive
# the seam it is raised through, or §8.5's two headlines cannot be told apart.
# ---------------------------------------------------------------------------


def test_a_busy_link_never_reads_as_no_pi_from_run_batch_pass(tmp_path, monkeypatch):
    store_path = tmp_path / "usage.db"
    _build_store_with_one_pending_reading(store_path)
    monkeypatch.setattr(push, "desktop_id", lambda *a, **kw: "abc")

    async def never_scanned(*a, **kw):  # pragma: no cover - must not run
        raise AssertionError("the scan must not be attempted while the link is busy")

    monkeypatch.setattr(push, "find_pi", never_scanned)
    fd = _hold(push.BLE_LOCK_PATH)

    async def go():
        return await push.run_batch_pass(
            str(store_path),
            projects_root=tmp_path / "empty-projects",
            lock_wait_s=0.0,
        )

    try:
        # Not a BatchResult with every row failed: nothing was attempted, so
        # nothing was learned (§8.1's *can't tell*).
        with pytest.raises(push.BleLinkBusy):
            asyncio.run(go())
    finally:
        os.close(fd)


def test_a_busy_link_never_reads_as_no_pi_from_run_gauge_push(tmp_path, monkeypatch):
    monkeypatch.setattr(push, "desktop_id", lambda *a, **kw: "abc")
    monkeypatch.setattr(push, "build_gauge_wire_payload", lambda did: {"kind": "gauge"})

    async def never_scanned(*a, **kw):  # pragma: no cover - must not run
        raise AssertionError("the scan must not be attempted while the link is busy")

    monkeypatch.setattr(push, "find_pi", never_scanned)
    fd = _hold(push.BLE_LOCK_PATH)

    async def go():
        return await push.run_gauge_push(str(tmp_path / "usage.db"), lock_wait_s=0.0)

    try:
        with pytest.raises(push.BleLinkBusy):
            asyncio.run(go())
    finally:
        os.close(fd)


def test_the_cli_reports_a_busy_link_as_cant_tell_not_a_fault(tmp_path, monkeypatch, capsys):
    """§9.6: `2` is *can't tell*, split from `1` deliberately so a cron
    wrapper cannot page on an expected steady state."""

    async def busy(*a, **kw):
        raise push.BleLinkBusy("the BLE link is busy")

    monkeypatch.setattr(push, "run_batch_pass", busy)

    args = push.parse_args([])
    exit_code = asyncio.run(push._async_main(args))

    assert exit_code == 2
    assert "Nothing was queued" in capsys.readouterr().err


def test_an_unexpected_lock_error_is_not_reported_as_busy(tmp_path, monkeypatch):
    """ENOLCK is this Desktop's own problem, not an occupied link."""
    import fcntl as fcntl_module

    def broken_flock(fd, op):
        raise OSError(errno.ENOLCK, "no locks available")

    monkeypatch.setattr(fcntl_module, "flock", broken_flock)

    async def go():
        async with push.ble_lock(path=tmp_path / "ble.lock", wait_s=15.0):
            pass  # pragma: no cover

    with pytest.raises(OSError) as excinfo:
        asyncio.run(go())
    assert not isinstance(excinfo.value, push.BleLinkBusy)
