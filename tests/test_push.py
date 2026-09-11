"""Tests for desktop/push.py (#46), the transport.

push.py's BLE half can't be unit tested without a radio (spec §11) — these
tests cover the BLE-free seams instead: Desktop Id derivation, Batch-building
logic, `wiped: true` handling, and CLI flag parsing/dispatch. The BLE-calling
code is structured to take a `send_one` callable (one write-and-wait-for-Ack)
so those pieces are testable in isolation with a fake one.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import hmac
import json

import pytest

import push
import usage


class _PinnedDate(_dt.date):
    """`date` with `today()` fixed at 2026-09-05, the date these fixtures
    were written against."""

    @classmethod
    def today(cls):
        return cls(2026, 9, 5)


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch):
    # The store fixtures below use fixed dates, and `pending_readings` keeps
    # only the seven-day Window ending `date.today()` (§4.6) — so without
    # this, a fixture date silently drops out of the Batch as the calendar
    # advances, and assertions about what was sent start failing.
    monkeypatch.setattr(usage, "date", _PinnedDate)


# ---------------------------------------------------------------------------
# §7.1 Desktop Id derivation
# ---------------------------------------------------------------------------


def test_desktop_id_is_hmac_of_machine_id(tmp_path):
    machine_id_path = tmp_path / "machine-id"
    machine_id_path.write_text("d41d8cd98f00b204e9800998ecf8427e\n")

    got = push.desktop_id((str(machine_id_path),))

    raw = bytes.fromhex("d41d8cd98f00b204e9800998ecf8427e")
    expected = hmac.new(raw, push.APP_ID.encode(), hashlib.sha256).hexdigest()[:16]
    assert got == expected
    assert len(got) == 16


def test_desktop_id_is_stable_for_the_same_machine_id(tmp_path):
    machine_id_path = tmp_path / "machine-id"
    machine_id_path.write_text("abc123abc123abc123abc123abc123ab")

    first = push.desktop_id((str(machine_id_path),))
    second = push.desktop_id((str(machine_id_path),))
    assert first == second


def test_desktop_id_differs_for_different_machine_ids(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.write_text("11111111111111111111111111111111")
    b.write_text("22222222222222222222222222222222")

    assert push.desktop_id((str(a),)) != push.desktop_id((str(b),))


def test_desktop_id_falls_through_to_second_path(tmp_path):
    missing = tmp_path / "does-not-exist"
    present = tmp_path / "fallback"
    present.write_text("33333333333333333333333333333333")

    got = push.desktop_id((str(missing), str(present)))

    raw = bytes.fromhex("33333333333333333333333333333333")
    expected = hmac.new(raw, push.APP_ID.encode(), hashlib.sha256).hexdigest()[:16]
    assert got == expected


def test_desktop_id_raises_when_no_machine_id_found(tmp_path):
    missing = tmp_path / "does-not-exist"
    with pytest.raises(RuntimeError):
        push.desktop_id((str(missing),))


# ---------------------------------------------------------------------------
# §4.6 find_pi prefers a stored pi.address
# ---------------------------------------------------------------------------


class _FakeDevice:
    def __init__(self, address, service_uuids=()):
        self.address = address
        self.service_uuids = service_uuids


class _FakeAdvertisement:
    def __init__(self, service_uuids):
        self.service_uuids = service_uuids


def test_find_pi_prefers_the_stored_address(monkeypatch):
    from bleak import BleakScanner

    matching = _FakeDevice("AA:BB:CC:DD:EE:FF")
    other = _FakeDevice("11:22:33:44:55:66")
    calls = {}

    async def fake_find_device_by_filter(filterfunc, timeout=10.0, **kw):
        calls["timeout"] = timeout
        ad = _FakeAdvertisement([push.SERVICE_UUID])
        # The wrong-address device advertises the right service too -- the
        # filter must still pick the one whose address matches.
        assert filterfunc(other, ad) is False
        assert filterfunc(matching, ad) is True
        return matching

    monkeypatch.setattr(BleakScanner, "find_device_by_filter", fake_find_device_by_filter)

    device = asyncio.run(push.find_pi(timeout=5.0, address="AA:BB:CC:DD:EE:FF"))

    assert device is matching
    assert calls["timeout"] == 5.0


def test_find_pi_with_address_requires_the_service_too(monkeypatch):
    # §4.6/#86's review fix: a reused/rotated MAC answering without the
    # zeropi service must not be accepted just because the address matches.
    from bleak import BleakScanner

    same_address_wrong_service = _FakeDevice("AA:BB:CC:DD:EE:FF")

    async def fake_find_device_by_filter(filterfunc, timeout=10.0, **kw):
        ad = _FakeAdvertisement(["some-other-uuid"])
        assert filterfunc(same_address_wrong_service, ad) is False
        return None

    monkeypatch.setattr(BleakScanner, "find_device_by_filter", fake_find_device_by_filter)

    with pytest.raises(RuntimeError, match="AA:BB:CC:DD:EE:FF"):
        asyncio.run(push.find_pi(timeout=5.0, address="AA:BB:CC:DD:EE:FF"))


def test_find_pi_with_address_raises_when_absent_no_fallback(monkeypatch):
    # §4.6: a stored address that does not answer is an ordinary *absent*
    # Unreachable, not a special failure -- there is deliberately no
    # fallback to scanning for a different Pi.
    from bleak import BleakScanner

    async def fake_find_device_by_filter(filterfunc, timeout=10.0, **kw):
        return None

    monkeypatch.setattr(BleakScanner, "find_device_by_filter", fake_find_device_by_filter)

    with pytest.raises(RuntimeError, match="AA:BB:CC:DD:EE:FF"):
        asyncio.run(push.find_pi(timeout=5.0, address="AA:BB:CC:DD:EE:FF"))


def test_find_pi_falls_back_to_the_generic_filter_when_no_address(monkeypatch):
    from bleak import BleakScanner

    async def fake_find_device_by_filter(filterfunc, timeout=10.0, **kw):
        return "generic-device"

    monkeypatch.setattr(BleakScanner, "find_device_by_filter", fake_find_device_by_filter)

    device = asyncio.run(push.find_pi(timeout=5.0))
    assert device == "generic-device"


# ---------------------------------------------------------------------------
# §7.3 Batch-building
# ---------------------------------------------------------------------------


def _reading(date="2026-09-05", project="-home-tester-proj", model="claude-opus-5"):
    return usage.Reading(
        date=date,
        project_key=project,
        model=model,
        input_tokens=100,
        output_tokens=50,
        cache_creation_tokens=10,
        cache_read_tokens=5,
        cost_usd=1.2345,
        session_count=2,
        cost_complete=True,
        pending=True,
    )


def test_build_daily_batch_sets_batch_size_and_index_in_order():
    readings = [_reading(date="2026-09-05"), _reading(date="2026-09-04"), _reading(date="2026-09-03")]

    payloads = push.build_daily_batch(readings, "deadbeefcafebabe")

    assert [p["batch_index"] for p in payloads] == [0, 1, 2]
    assert all(p["batch_size"] == 3 for p in payloads)
    assert [p["date"] for p in payloads] == ["2026-09-05", "2026-09-04", "2026-09-03"]
    assert all(p["kind"] == "daily" for p in payloads)
    assert all(p["desktop_id"] == "deadbeefcafebabe" for p in payloads)


def test_build_daily_batch_empty_readings_yields_empty_batch():
    assert push.build_daily_batch([], "deadbeefcafebabe") == []


def test_build_daily_batch_single_reading_batch_size_one():
    payloads = push.build_daily_batch([_reading()], "id")
    assert payloads[0]["batch_size"] == 1
    assert payloads[0]["batch_index"] == 0


# ---------------------------------------------------------------------------
# §7.3/§7.2 The Batch loop and wiped:true handling, against a fake send_one
# ---------------------------------------------------------------------------


def _make_store(tmp_path, rows):
    """A real store seeded directly via ingest_entries, one UsageEntry per
    row tuple (date, project, model)."""
    store_path = tmp_path / "store.db"
    conn = usage.open_store(store_path)
    entries = []
    for i, (date, project, model) in enumerate(rows):
        entries.append(
            usage.UsageEntry(
                request_id=f"req-{i}",
                message_id=f"msg-{i}",
                session_id=f"sess-{i}",
                project_key=project,
                cwd=None,
                local_date=date,
                model=model,
                input_tokens=100,
                output_tokens=50,
                cache_write_5m_tokens=1,
                cache_write_1h_tokens=1,
                cache_read_tokens=1,
                web_search_requests=0,
                cost_usd=0.5,
                cost_complete=True,
                rank_sidechain=1,
                rank_tokens=152,
                rank_speed=1,
                source_file=f"/fake/{i}.jsonl",
                source_end_offset=100,
            )
        )
    usage.ingest_entries(conn, entries)
    return conn


class FakeAcker:
    """A scripted send_one: returns the next Ack from a fixed list, one per
    call, regardless of the payload."""

    def __init__(self, acks):
        self._acks = list(acks)
        self.sent_payloads = []

    async def __call__(self, payload):
        self.sent_payloads.append(payload)
        return self._acks.pop(0)


def _ok_ack(payload, wiped=False):
    return {
        "status": "ok",
        "kind": "daily",
        "date": payload["date"],
        "project": payload["project"],
        "model": payload["model"],
        "drawn": True,
        "wiped": wiped,
    }


def test_run_batch_with_connection_marks_only_successful_rows(tmp_path):
    async def _test_run_batch_with_connection_marks_only_successful_rows_impl():
        conn = _make_store(
            tmp_path,
            [
                ("2026-09-05", "-home-a", "claude-opus-5"),
                ("2026-09-04", "-home-a", "claude-opus-5"),
            ],
        )

        async def send_one(payload):
            if payload["date"] == "2026-09-05":
                return _ok_ack(payload)
            return {"status": "error", "reason": "boom"}

        result = await push.run_batch_with_connection(conn, "desktop-id", send_one)

        assert result.sent == 1
        assert result.failed == 1
        assert result.wiped is False

        pending_after = usage.pending_readings(conn)
        pending_dates = {r.date for r in pending_after}
        assert "2026-09-04" in pending_dates
        assert "2026-09-05" not in pending_dates


    asyncio.run(_test_run_batch_with_connection_marks_only_successful_rows_impl())
def test_run_batch_with_connection_continues_past_a_timeout(tmp_path):
    async def _test_run_batch_with_connection_continues_past_a_timeout_impl():
        conn = _make_store(
            tmp_path,
            [
                ("2026-09-05", "-home-a", "claude-opus-5"),
                ("2026-09-04", "-home-a", "claude-opus-5"),
            ],
        )

        async def send_one(payload):
            if payload["date"] == "2026-09-05":
                return None  # simulates an Ack timeout
            return _ok_ack(payload)

        result = await push.run_batch_with_connection(conn, "desktop-id", send_one)

        assert result.sent == 1
        assert result.failed == 1


    asyncio.run(_test_run_batch_with_connection_continues_past_a_timeout_impl())
def test_run_batch_with_connection_no_pending_readings_never_calls_send(tmp_path):
    async def _test_run_batch_with_connection_no_pending_readings_never_calls_send_impl():
        store_path = tmp_path / "store.db"
        conn = usage.open_store(store_path)

        calls = []

        async def send_one(payload):
            calls.append(payload)
            return _ok_ack(payload)

        result = await push.run_batch_with_connection(conn, "desktop-id", send_one)

        assert calls == []
        assert result.sent == 0
        assert result.failed == 0


    asyncio.run(_test_run_batch_with_connection_no_pending_readings_never_calls_send_impl())
def test_wiped_ack_clears_marks_and_runs_exactly_one_extra_pass(tmp_path):
    async def _test_wiped_ack_clears_marks_and_runs_exactly_one_extra_pass_impl():
        conn = _make_store(
            tmp_path,
            [
                ("2026-09-05", "-home-a", "claude-opus-5"),
                ("2026-09-04", "-home-a", "claude-opus-5"),
            ],
        )

        acker = FakeAcker(
            [
                _ok_ack({"date": "2026-09-05", "project": "-home-a", "model": "claude-opus-5"}, wiped=True),
                _ok_ack({"date": "2026-09-04", "project": "-home-a", "model": "claude-opus-5"}, wiped=False),
                # Second pass re-sends the whole (now-pending-again) Window.
                _ok_ack({"date": "2026-09-05", "project": "-home-a", "model": "claude-opus-5"}, wiped=False),
                _ok_ack({"date": "2026-09-04", "project": "-home-a", "model": "claude-opus-5"}, wiped=False),
            ]
        )

        result = await push.run_batch_with_connection(conn, "desktop-id", acker)

        assert result.wiped is True
        # 2 rows in the first pass + 2 rows re-sent in the one extra pass.
        assert result.sent == 4
        assert result.failed == 0
        assert len(acker.sent_payloads) == 4

        # Never marked pending after the extra pass.
        assert usage.pending_readings(conn) == []


    asyncio.run(_test_wiped_ack_clears_marks_and_runs_exactly_one_extra_pass_impl())
def test_wiped_ack_caps_at_one_extra_pass_even_if_second_pass_also_wiped(tmp_path):
    async def _test_wiped_ack_caps_at_one_extra_pass_even_if_second_pass_also_wiped_impl():
        conn = _make_store(tmp_path, [("2026-09-05", "-home-a", "claude-opus-5")])

        acker = FakeAcker(
            [
                _ok_ack({"date": "2026-09-05", "project": "-home-a", "model": "claude-opus-5"}, wiped=True),
                _ok_ack({"date": "2026-09-05", "project": "-home-a", "model": "claude-opus-5"}, wiped=True),
            ]
        )

        result = await push.run_batch_with_connection(conn, "desktop-id", acker)

        # Exactly 2 sends total: the triggering pass, and one extra pass. No
        # third pass even though the second Ack also said wiped.
        assert len(acker.sent_payloads) == 2
        assert result.sent == 2


    asyncio.run(_test_wiped_ack_caps_at_one_extra_pass_even_if_second_pass_also_wiped_impl())
# ---------------------------------------------------------------------------
# §7.4 The Gauge push dispatch
# ---------------------------------------------------------------------------


def test_run_gauge_with_connection_none_payload_is_a_noop_and_never_sends():
    async def _test_run_gauge_with_connection_none_payload_is_a_noop_and_never_sends_impl():
        calls = []

        async def send_one(payload):
            calls.append(payload)
            return {"status": "ok"}

        ok, wiped = await push.run_gauge_with_connection(None, send_one)

        assert ok is False
        assert wiped is False
        assert calls == []


    asyncio.run(_test_run_gauge_with_connection_none_payload_is_a_noop_and_never_sends_impl())
def test_run_gauge_with_connection_ok_ack_returns_true():
    async def _test_run_gauge_with_connection_ok_ack_returns_true_impl():
        async def send_one(payload):
            return {"status": "ok", "kind": "gauge", "drawn": True, "wiped": False}

        ok, wiped = await push.run_gauge_with_connection({"kind": "gauge"}, send_one)
        assert ok is True
        assert wiped is False


    asyncio.run(_test_run_gauge_with_connection_ok_ack_returns_true_impl())
def test_run_gauge_with_connection_surfaces_wiped_true():
    # Spec §7.2: wipe handling applies "on any Ack with wiped: true, of
    # either kind" — the Gauge path must not discard this flag.
    async def _test_run_gauge_with_connection_surfaces_wiped_true_impl():
        async def send_one(payload):
            return {"status": "ok", "kind": "gauge", "drawn": True, "wiped": True}

        ok, wiped = await push.run_gauge_with_connection({"kind": "gauge"}, send_one)
        assert ok is True
        assert wiped is True


    asyncio.run(_test_run_gauge_with_connection_surfaces_wiped_true_impl())
def test_run_gauge_with_connection_failure_is_dropped_silently():
    async def _test_run_gauge_with_connection_failure_is_dropped_silently_impl():
        async def send_one(payload):
            return {"status": "error", "reason": "nope"}

        ok, wiped = await push.run_gauge_with_connection({"kind": "gauge"}, send_one)
        assert ok is False
        assert wiped is False


    asyncio.run(_test_run_gauge_with_connection_failure_is_dropped_silently_impl())
def test_run_gauge_with_connection_exception_is_dropped_silently():
    async def _test_run_gauge_with_connection_exception_is_dropped_silently_impl():
        async def send_one(payload):
            raise RuntimeError("connection dropped")

        ok, wiped = await push.run_gauge_with_connection({"kind": "gauge"}, send_one)
        assert ok is False
        assert wiped is False


    asyncio.run(_test_run_gauge_with_connection_exception_is_dropped_silently_impl())
def test_build_gauge_wire_payload_none_when_gauge_has_nothing(monkeypatch):
    import gauge

    monkeypatch.setattr(gauge, "build_gauge_payload", lambda **kw: None)
    assert push.build_gauge_wire_payload("desktop-id") is None


def test_build_gauge_wire_payload_attaches_kind_and_desktop_id(monkeypatch):
    import gauge

    monkeypatch.setattr(
        gauge,
        "build_gauge_payload",
        lambda **kw: {"snapshot_age_s": 1, "five_hour": {"pct": 10, "resets_in_s": 5}, "seven_day": {"pct": 5, "resets_in_s": 6}, "context": None},
    )
    payload = push.build_gauge_wire_payload("desktop-id")
    assert payload["kind"] == "gauge"
    assert payload["desktop_id"] == "desktop-id"
    assert payload["snapshot_age_s"] == 1


def test_build_gauge_wire_payload_threads_projects_root_to_the_context_read(monkeypatch):
    # management-surface spec §11.1 trap 1: a configured `paths.projects_root`
    # must reach the Gauge's context read too, not just Daily paths -- this
    # is the caller-level test the ticket calls for, since a unit test of
    # `gauge.build_gauge_payload` alone would pass even with the bug (it
    # already accepts the argument correctly).
    import gauge
    from pathlib import Path

    seen = {}

    def fake_build_gauge_payload(**kwargs):
        seen.update(kwargs)
        return {"snapshot_age_s": 1, "five_hour": None, "seven_day": None, "context": None}

    monkeypatch.setattr(gauge, "build_gauge_payload", fake_build_gauge_payload)
    push.build_gauge_wire_payload("desktop-id", Path("/configured/projects"))
    assert seen["projects_root"] == Path("/configured/projects")


def test_run_gauge_push_threads_projects_root_through(monkeypatch):
    from pathlib import Path

    seen = {}

    monkeypatch.setattr(push, "desktop_id", lambda: "desktop-id")

    def fake_build_gauge_wire_payload(did, projects_root=None):
        seen["projects_root"] = projects_root
        return None  # nothing to push -- no BLE attempted, no fake radio needed

    monkeypatch.setattr(push, "build_gauge_wire_payload", fake_build_gauge_wire_payload)

    ok = asyncio.run(push.run_gauge_push(None, Path("/configured/projects")))

    assert ok is False
    assert seen["projects_root"] == Path("/configured/projects")


def test_run_gauge_push_wiped_ack_clears_marks_and_runs_one_batch_pass(tmp_path, monkeypatch):
    # Spec §7.2: a wiped Gauge Ack must clear pushed_at and run exactly one
    # further Batch pass in the same invocation, same as a wiped Daily Ack.
    async def _impl():
        store_path = tmp_path / "store.db"
        conn = _make_store(tmp_path, [("2026-09-05", "-home-a", "claude-opus-5")])
        usage.mark_pushed(conn, "2026-09-05", "-home-a", "claude-opus-5", "2026-01-01T00:00:00+00:00")
        assert usage.pending_readings(conn) == []
        conn.close()

        monkeypatch.setattr(push, "desktop_id", lambda: "desktop-id")
        monkeypatch.setattr(push, "build_gauge_wire_payload", lambda did, projects_root=None: {"kind": "gauge", "desktop_id": did})

        async def fake_with_ble_connection(coro_fn, **kwargs):
            async def send_one(payload):
                return {"status": "ok", "kind": "gauge", "drawn": True, "wiped": True}
            return await coro_fn(send_one)

        monkeypatch.setattr(push, "_with_ble_connection", fake_with_ble_connection)

        batch_pass_calls = []
        real_run_batch_pass = push.run_batch_pass

        async def spying_run_batch_pass(store_path_, *a, **kw):
            batch_pass_calls.append(store_path_)
            return await real_run_batch_pass(store_path_, *a, **kw)

        monkeypatch.setattr(push, "run_batch_pass", spying_run_batch_pass)

        ok = await push.run_gauge_push(str(store_path))

        assert ok is True
        # The Gauge Ack's wiped:true triggered exactly one extra Batch pass.
        assert batch_pass_calls == [str(store_path)]

        conn = usage.open_store(store_path)
        assert usage.clear_pushed_marks  # sanity: still importable
        # pushed_at was cleared before the extra Batch pass ran; that pass
        # itself failed (no real Pi in fake_with_ble_connection's second
        # call — it always returns the gauge-shaped Ack above regardless of
        # payload, which run_batch_with_connection still treats as an ok
        # Ack for the Daily row too), so the Reading was marked pushed again.
        assert usage.pending_readings(conn) == []

    asyncio.run(_impl())


def test_run_gauge_push_non_wiped_ack_does_not_touch_store(tmp_path, monkeypatch):
    async def _impl():
        store_path = tmp_path / "store.db"
        conn = _make_store(tmp_path, [("2026-09-05", "-home-a", "claude-opus-5")])
        conn.close()

        monkeypatch.setattr(push, "desktop_id", lambda: "desktop-id")
        monkeypatch.setattr(push, "build_gauge_wire_payload", lambda did, projects_root=None: {"kind": "gauge", "desktop_id": did})

        async def fake_with_ble_connection(coro_fn, **kwargs):
            async def send_one(payload):
                return {"status": "ok", "kind": "gauge", "drawn": True, "wiped": False}
            return await coro_fn(send_one)

        monkeypatch.setattr(push, "_with_ble_connection", fake_with_ble_connection)

        run_batch_pass_called = []

        async def fake_run_batch_pass(*a, **kw):
            run_batch_pass_called.append(True)

        monkeypatch.setattr(push, "run_batch_pass", fake_run_batch_pass)

        ok = await push.run_gauge_push(str(store_path))

        assert ok is True
        assert run_batch_pass_called == []

    asyncio.run(_impl())


# ---------------------------------------------------------------------------
# §7.6 CLI flag parsing/dispatch
# ---------------------------------------------------------------------------


def test_parse_args_defaults():
    args = push.parse_args([])
    assert args.dry_run is False
    assert args.resend_all is False
    assert args.store is None
    assert args.gauge_only is False
    assert args.batch_only is False


def test_parse_args_dry_run():
    args = push.parse_args(["--dry-run"])
    assert args.dry_run is True


def test_parse_args_resend_all():
    args = push.parse_args(["--resend-all"])
    assert args.resend_all is True


def test_parse_args_store_path():
    args = push.parse_args(["--store", "/tmp/custom.db"])
    assert args.store == "/tmp/custom.db"


def test_parse_args_gauge_only_and_batch_only_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        push.parse_args(["--gauge-only", "--batch-only"])


def test_parse_args_resend_all_with_gauge_only_is_rejected():
    # --resend-all's whole point is to Batch the whole Window (spec §7.6);
    # combined with --gauge-only it would clear pushed_at and then silently
    # skip the Batch pass that was supposed to resend it.
    with pytest.raises(SystemExit):
        push.parse_args(["--resend-all", "--gauge-only"])


def test_parse_args_resend_all_with_batch_only_is_allowed():
    args = push.parse_args(["--resend-all", "--batch-only"])
    assert args.resend_all is True
    assert args.batch_only is True


def test_parse_args_gauge_only():
    args = push.parse_args(["--gauge-only"])
    assert args.gauge_only is True
    assert args.batch_only is False


def test_print_dry_run_threads_projects_root_to_the_gauge_preview(tmp_path, monkeypatch, capsys):
    from pathlib import Path

    store_path = tmp_path / "store.db"
    usage.open_store(store_path).close()
    seen = {}

    def fake_build_gauge_payload(**kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr(push.gauge, "build_gauge_payload", fake_build_gauge_payload)

    push.print_dry_run(str(store_path), Path("/configured/projects"))

    assert seen["projects_root"] == Path("/configured/projects")


def test_async_main_dry_run_never_touches_ble(tmp_path, monkeypatch):
    async def _test_async_main_dry_run_never_touches_ble_impl():
        store_path = tmp_path / "store.db"
        usage.open_store(store_path).close()

        called = {"batch": False, "gauge": False}

        async def fake_run_batch_pass(*a, **kw):
            called["batch"] = True

        async def fake_run_gauge_push(*a, **kw):
            called["gauge"] = True

        monkeypatch.setattr(push, "run_batch_pass", fake_run_batch_pass)
        monkeypatch.setattr(push, "run_gauge_push", fake_run_gauge_push)

        args = push.parse_args(["--dry-run", "--store", str(store_path)])
        exit_code = await push._async_main(args)

        assert exit_code == 0
        assert called["batch"] is False
        assert called["gauge"] is False


    asyncio.run(_test_async_main_dry_run_never_touches_ble_impl())
def test_async_main_gauge_only_skips_batch(monkeypatch):
    async def _test_async_main_gauge_only_skips_batch_impl():
        called = {"batch": False, "gauge": False}

        async def fake_run_batch_pass(*a, **kw):
            called["batch"] = True
            return push.BatchResult()

        async def fake_run_gauge_push(*a, **kw):
            called["gauge"] = True
            return True

        monkeypatch.setattr(push, "run_batch_pass", fake_run_batch_pass)
        monkeypatch.setattr(push, "run_gauge_push", fake_run_gauge_push)

        args = push.parse_args(["--gauge-only"])
        exit_code = await push._async_main(args)

        assert exit_code == 0
        assert called["batch"] is False
        assert called["gauge"] is True


    asyncio.run(_test_async_main_gauge_only_skips_batch_impl())
def test_async_main_batch_only_skips_gauge(monkeypatch):
    async def _test_async_main_batch_only_skips_gauge_impl():
        called = {"batch": False, "gauge": False}

        async def fake_run_batch_pass(*a, **kw):
            called["batch"] = True
            return push.BatchResult()

        async def fake_run_gauge_push(*a, **kw):
            called["gauge"] = True
            return True

        monkeypatch.setattr(push, "run_batch_pass", fake_run_batch_pass)
        monkeypatch.setattr(push, "run_gauge_push", fake_run_gauge_push)

        args = push.parse_args(["--batch-only"])
        exit_code = await push._async_main(args)

        assert exit_code == 0
        assert called["batch"] is True
        assert called["gauge"] is False


    asyncio.run(_test_async_main_batch_only_skips_gauge_impl())
def test_async_main_exits_non_zero_when_batch_fails(monkeypatch):
    async def _test_async_main_exits_non_zero_when_batch_fails_impl():
        async def fake_run_batch_pass(*a, **kw):
            return push.BatchResult(sent=0, failed=1)

        async def fake_run_gauge_push(*a, **kw):
            return False

        monkeypatch.setattr(push, "run_batch_pass", fake_run_batch_pass)
        monkeypatch.setattr(push, "run_gauge_push", fake_run_gauge_push)

        args = push.parse_args([])
        exit_code = await push._async_main(args)

        assert exit_code == 1


    asyncio.run(_test_async_main_exits_non_zero_when_batch_fails_impl())
def test_async_main_resend_all_clears_marks_before_batch(tmp_path, monkeypatch):
    async def _test_async_main_resend_all_clears_marks_before_batch_impl():
        store_path = tmp_path / "store.db"
        conn = _make_store(tmp_path, [("2026-09-05", "-home-a", "claude-opus-5")])
        conn.close()
        # mark it pushed so we can prove --resend-all clears it
        conn = usage.open_store(store_path)
        usage.mark_pushed(conn, "2026-09-05", "-home-a", "claude-opus-5", "2026-01-01T00:00:00+00:00")
        assert usage.pending_readings(conn) == []
        conn.close()

        async def fake_run_batch_pass(*a, **kw):
            return push.BatchResult()

        async def fake_run_gauge_push(*a, **kw):
            return False

        monkeypatch.setattr(push, "run_batch_pass", fake_run_batch_pass)
        monkeypatch.setattr(push, "run_gauge_push", fake_run_gauge_push)

        args = push.parse_args(["--resend-all", "--store", str(store_path)])
        await push._async_main(args)

        conn = usage.open_store(store_path)
        assert len(usage.pending_readings(conn)) == 1
    asyncio.run(_test_async_main_resend_all_clears_marks_before_batch_impl())


# ---------------------------------------------------------------------------
# §5.2 The Command Payload
# ---------------------------------------------------------------------------


def test_build_command_payload_shape():
    assert push.build_command_payload("redraw", "deadbeefdeadbeef") == {
        "kind": "command",
        "desktop_id": "deadbeefdeadbeef",
        "verb": "redraw",
    }


def test_command_verbs_matches_the_pis_own_vocabulary():
    # `push.COMMAND_VERBS` is a by-hand mirror of `pi/receive.py:COMMAND_VERBS`
    # (spec §5.2) -- the two modules install onto different deployments, so
    # nothing keeps them equal except this test.
    import receive

    assert set(push.COMMAND_VERBS) == set(receive.COMMAND_VERBS)


# ---------------------------------------------------------------------------
# #67 The single-write budget. `send_one` is the one place every Payload of
# either kind passes through, so the guard lives there and is testable with a
# fake client -- no radio, per this file's opening note.
# ---------------------------------------------------------------------------


class _RecordingClient:
    """The narrowest stand-in for a BleakClient that `send_one` needs: it
    records writes and never Acks, so a test that gets as far as writing
    fails on the Ack timeout rather than passing silently.
    """

    def __init__(self):
        self.writes = []

    async def write_gatt_char(self, uuid, body, response):
        self.writes.append(body)


def _payload_of_exact_len(n: int) -> dict:
    payload = {"kind": "daily", "project": "-p", "pad": ""}
    overhead = len(json.dumps(payload).encode())
    payload["pad"] = "x" * (n - overhead)
    assert len(json.dumps(payload).encode()) == n
    return payload


def test_payload_at_the_limit_is_written():
    async def _impl():
        client = _RecordingClient()
        conn = push.BleConnection(client)
        payload = _payload_of_exact_len(push.MAX_PAYLOAD_BYTES)
        # No Ack ever arrives, so this returns None on timeout -- what matters
        # is that the write happened rather than being refused.
        conn._ack_received = asyncio.Event()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(conn.send_one(payload), timeout=0.05)
        assert len(client.writes) == 1
        assert len(client.writes[0]) == push.MAX_PAYLOAD_BYTES

    asyncio.run(_impl())


def test_payload_one_byte_over_the_limit_is_refused_before_the_radio():
    async def _impl():
        client = _RecordingClient()
        conn = push.BleConnection(client)
        payload = _payload_of_exact_len(push.MAX_PAYLOAD_BYTES + 1)
        with pytest.raises(push.PayloadTooLarge) as excinfo:
            await conn.send_one(payload)
        # Nothing reached the radio at all.
        assert client.writes == []
        # The message must name the real culprit, not the transport.
        assert "513" in str(excinfo.value)
        assert "-p" in str(excinfo.value)

    asyncio.run(_impl())


def test_an_oversized_row_fails_only_its_own_row(tmp_path):
    """A too-long project must not abort the Batch -- `_send_batch_once`
    already treats a raised `send_one` as one failed row, and this pins that
    the guard rides that path rather than needing its own handling.
    """

    async def _impl():
        conn = _make_store(tmp_path, [("2026-09-05", "-home-a", "claude-opus-5")])

        async def send_one(payload):
            if payload["project"] == "-home-a":
                raise push.PayloadTooLarge("Payload is 600 bytes, over the 512-byte limit")
            return {"status": "ok"}

        result = await push.run_batch_with_connection(conn, "desktop-id", send_one)
        assert result.sent == 0
        assert result.failed == 1
        assert not result.ok
        # The Reading stays pending, so the next Batch retries it (ADR-0003).
        assert len(usage.pending_readings(conn)) == 1

    asyncio.run(_impl())


def test_main_re_asserts_configuration_settings_and_waits_for_the_lock(tmp_path, monkeypatch):
    """`push.py` is a CLI: it re-asserts the configured Settings set (§7.2)
    and waits out a busy link rather than dropping the job (§7.1)."""
    monkeypatch.setenv("ZEROPI_CONFIG", str(tmp_path / "config.db"))
    monkeypatch.setenv("ZEROPI_USAGE_STORE", str(tmp_path / "usage.db"))
    seen: list[dict] = []

    async def fake_run_batch_pass(*a, **kw):
        seen.append(kw)
        return push.BatchResult()

    async def fake_run_gauge_push(*a, **kw):
        seen.append(kw)
        return True

    monkeypatch.setattr(push, "run_batch_pass", fake_run_batch_pass)
    monkeypatch.setattr(push, "run_gauge_push", fake_run_gauge_push)

    assert push.main([]) == 0
    assert len(seen) == 2
    for kwargs in seen:
        assert kwargs["settings"] == {"idle_keepalive_s": 86400}
        assert kwargs["lock_wait_s"] == push.CLI_LOCK_WAIT_S == 15.0
