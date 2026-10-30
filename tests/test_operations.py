"""Tests for desktop/operations.py (#103), web-ui spec §4 and §12.1.

The CLI's rendering of these is pinned by test_cli.py; here each operation is
tested by what it returns. Same fakes, same constraints: no BLE, no Pi.
"""

from __future__ import annotations

import asyncio
import os

import pytest

import config
import operations
import push
import usage
from operations import Outcome, Refused
from test_cli import _absent_radio, _cfg, _cfg_path, _fake_radio, _hold, _seed_pending, _settings_ack

pytestmark = pytest.mark.usefixtures("pin_today")

PAIRED = {"pi.address": "AA:BB:CC:DD:EE:FF"}


@pytest.fixture
def no_radio(monkeypatch):
    """Fails the test if anything tries to scan: "nothing was sent"."""

    async def never_scanned(*a, **kw):  # pragma: no cover - must not run
        raise AssertionError("a refusal must be decided before any BLE")

    monkeypatch.setattr(push, "find_pi", never_scanned)
    monkeypatch.setattr(push, "desktop_id", lambda *a, **kw: "deadbeefdeadbeef")


def _stored(tmp_path) -> dict:
    conn = config.open_config_store(_cfg_path(tmp_path))
    try:
        return config.read_config(conn)
    finally:
        conn.close()


def _write(tmp_path, key, raw):
    conn = config.open_config_store(_cfg_path(tmp_path))
    try:
        return operations.write_config(conn, key, raw)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# §4.2 Every refusal kind: its kind, nothing written, nothing sent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key, raw, kind",
    [
        ("nope.nope", "1", "unknown_key"),
        ("REDRAW_FLOOR_S", "120", "not_a_setting"),
        ("service.gauge_throttle_s", "600", "out_of_range"),
        ("service.gauge_throttle_s", "soon", "out_of_range"),  # fails to coerce
        ("pi.address", "AA:BB:CC:DD:EE:FF", "pair_only"),
    ],
)
def test_config_set_refusals_write_nothing(tmp_path, no_radio, key, raw, kind):
    _cfg(tmp_path)
    before = _stored(tmp_path)

    with pytest.raises(Refused) as exc:
        _write(tmp_path, key, raw)

    assert exc.value.kind == kind
    assert exc.value.outcome is Outcome.REFUSED
    assert _stored(tmp_path) == before


def test_out_of_range_carries_config_text_and_the_derived_rationale(tmp_path):
    _cfg(tmp_path)

    with pytest.raises(Refused) as exc:
        _write(tmp_path, "service.gauge_throttle_s", "600")

    assert "derived" in exc.value.rationale
    assert exc.value.message == str(exc.value.__cause__)  # config.py's text, unchanged


def test_config_get_unknown_key_refused(tmp_path):
    _cfg(tmp_path)
    conn = config.open_config_store(_cfg_path(tmp_path))
    try:
        with pytest.raises(Refused) as exc:
            operations.config_get(conn, "nope.nope")
        tier3 = operations.config_get(conn, "REDRAW_FLOOR_S")
    finally:
        conn.close()

    assert exc.value.kind == "unknown_key"
    assert tier3.tier3 is operations.TIER3["REDRAW_FLOOR_S"]


@pytest.mark.parametrize(
    "run",
    [
        lambda cfg: operations.push_batch(cfg),
        lambda cfg: operations.redraw(cfg),
        lambda cfg: operations.wipe(cfg, "ddeeff"),
    ],
    ids=["push", "redraw", "wipe"],
)
def test_not_paired_is_refused_before_ble_as_cant_tell(tmp_path, no_radio, run):
    cfg = _cfg(tmp_path)

    with pytest.raises(Refused) as exc:
        asyncio.run(run(cfg))

    assert exc.value.kind == "not_paired"
    assert exc.value.outcome is Outcome.CANT_TELL  # §9.6: 2, not 3


def test_wipe_confirm_mismatch_sends_nothing_and_keeps_push_marks(tmp_path, no_radio):
    cfg = _cfg(tmp_path, **PAIRED)
    _seed_pending(cfg.paths_store, [("2026-09-05", "-home-a", "claude-opus-5")])
    conn = usage.open_store(cfg.paths_store)
    usage.mark_pushed(conn, "2026-09-05", "-home-a", "claude-opus-5", "2026-09-05T00:00:00+00:00")
    conn.close()

    with pytest.raises(Refused) as exc:
        asyncio.run(operations.wipe(cfg, "b827eb"))

    assert exc.value.kind == "confirm_mismatch"
    assert operations.readings_sent(cfg) == 1


# ---------------------------------------------------------------------------
# Results and their outcomes
# ---------------------------------------------------------------------------


def test_push_batch_busy_is_unreachable_busy(tmp_path, no_radio):
    cfg = _cfg(tmp_path, **PAIRED)
    _seed_pending(cfg.paths_store, [("2026-09-05", "-home-a", "claude-opus-5")])
    fd = _hold(push.BLE_LOCK_PATH)
    try:
        r = asyncio.run(operations.push_batch(cfg, lock_wait_s=0.0))
    finally:
        os.close(fd)

    assert r.unreachable.reason == "busy"
    assert r.batch is None
    assert r.outcome is Outcome.CANT_TELL


def test_redraw_absent_is_a_refused_command_not_a_refusal(tmp_path, monkeypatch):
    # §9.6: an Unreachable Command is 3 -- but it is a result, not a pre-BLE
    # refusal: the Operation ran and found no Pi.
    cfg = _cfg(tmp_path, **PAIRED)
    _absent_radio(monkeypatch)

    r = asyncio.run(operations.redraw(cfg, lock_wait_s=0.0))

    assert r.unreachable.reason == "absent"
    assert r.outcome is Outcome.REFUSED


def test_redraw_error_ack_is_a_fault(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, **PAIRED)
    _fake_radio(monkeypatch, [_settings_ack(), {"status": "error", "reason": "panel busy"}])

    r = asyncio.run(operations.redraw(cfg, lock_wait_s=0.0))

    assert r.reason == "panel busy"
    assert r.outcome is Outcome.FAULT


def test_wipe_reports_each_phase_when_the_repush_finds_the_link_busy(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, **PAIRED)
    _fake_radio(monkeypatch, [_settings_ack(), {"status": "ok", "kind": "command", "verb": "wipe", "wiped": True}])

    async def busy(*a, **kw):
        raise push.BleLinkBusy("held")

    monkeypatch.setattr(push, "run_batch_pass", busy)

    r = asyncio.run(operations.wipe(cfg, "ddeeff", lock_wait_s=0.0))

    assert r.command.outcome is Outcome.OK
    assert r.repush is None and r.repush_busy == "held"
    assert r.outcome is Outcome.CANT_TELL


def test_pair_absent_writes_no_address(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _absent_radio(monkeypatch)

    r = asyncio.run(operations.pair(cfg, _cfg_path(tmp_path)))

    assert r.address is None and r.unreachable.reason == "absent"
    assert r.outcome is Outcome.CANT_TELL
    assert "pi.address" not in _stored(tmp_path)


# ---------------------------------------------------------------------------
# §4.4 Progress
# ---------------------------------------------------------------------------


def test_progress_gets_the_closed_set_of_kinds_and_nothing_is_printed(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path)
    _seed_pending(cfg.paths_store, [("2026-09-05", "-home-a", "claude-opus-5")])
    _fake_radio(
        monkeypatch,
        [{"status": "error", "reason": "nope"}, {"status": "error", "reason": "row says no"}],
    )
    seen = []

    asyncio.run(operations.pair(cfg, _cfg_path(tmp_path), progress=lambda k, t: seen.append((k, t))))

    kinds = [k for k, _ in seen]
    assert set(kinds) <= push.PROGRESS_KINDS
    assert {"scanning", "found", "phase", "connected", "settings_failed", "row_failed"} <= set(kinds)
    assert ("phase", "Coupled. This Desktop is deadbeef…") in seen
    out = capsys.readouterr().out
    assert "Scanning" not in out and "Row failed" not in out


def test_progress_reports_a_lock_wait(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, **PAIRED)
    _fake_radio(monkeypatch, [_settings_ack(), {"status": "ok", "drawn": True}])
    seen = []
    fd = _hold(push.BLE_LOCK_PATH)

    async def scenario():
        task = asyncio.create_task(
            operations.redraw(cfg, lock_wait_s=5.0, progress=lambda k, t: seen.append(k))
        )
        await asyncio.sleep(0.3)
        os.close(fd)
        return await task

    r = asyncio.run(scenario())

    assert r.outcome is Outcome.OK
    assert seen[0] == "lock_wait"


def test_default_progress_prints_the_lock_wait_to_stderr_and_the_rest_to_stdout(capsys):
    push.print_progress("lock_wait", "Waiting for the link")
    push.print_progress("scanning", "Scanning for the Pi")

    captured = capsys.readouterr()
    assert captured.err == "Waiting for the link\n"
    assert captured.out == "Scanning for the Pi\n"
