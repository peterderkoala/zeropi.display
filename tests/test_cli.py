"""Tests for desktop/cli.py (#86), §9 of docs/spec-management-surface.md.

§10: no BLE, no SPI, no bluezero, no ~/.claude, no Pi. The
`_with_ble_connection` seam is driven with a fake radio exactly like
tests/test_ble_lock.py (`push.find_pi` / `push._open_client` monkeypatched).
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os

import pytest

import cli
import config
import push
import usage
import verdict

# The store fixtures below use fixed dates; see conftest.pin_today.
pytestmark = pytest.mark.usefixtures("pin_today")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _cfg(tmp_path, **settings) -> config.Configuration:
    """A resolved Configuration backed by a fresh config store and a fresh,
    empty usage store, both under `tmp_path`. `settings` are extra
    Configuration keys to seed (dotted names, e.g. `**{"pi.address": "..."}`).

    ⚠ `paths.projects_root` defaults to an empty, non-existent directory
    under `tmp_path` -- without this, `run_batch_pass`'s unconditional
    `usage.ingest_projects_root` call would scan *this machine's real*
    `~/.claude/projects` and pollute the test's store with real Readings.
    """
    settings.setdefault("paths.projects_root", str(tmp_path / "empty-projects"))

    store_path = tmp_path / "usage.db"
    usage.open_store(store_path).close()

    config_path = tmp_path / "config.db"
    conn = config.open_config_store(config_path)
    try:
        for key, value in settings.items():
            config.write_config_value(conn, key, value)
    finally:
        conn.close()

    return config.resolve(cli_config_path=str(config_path), cli_store_path=str(store_path), env={})


def _cfg_path(tmp_path):
    return tmp_path / "config.db"


def _args(argv):
    return cli.build_parser().parse_args(argv)


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
    monkeypatch.setattr(push, "desktop_id", lambda *a, **kw: "deadbeefdeadbeef")
    return client


def _absent_radio(monkeypatch, detail="no Pi advertising within 10.0s"):
    async def fake_find_pi(*a, **kw):
        raise RuntimeError(detail)

    monkeypatch.setattr(push, "find_pi", fake_find_pi)
    monkeypatch.setattr(push, "desktop_id", lambda *a, **kw: "deadbeefdeadbeef")


def _hold(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def _status_ack(**overrides) -> dict:
    base = {
        "status": "ok", "kind": "command", "verb": "status",
        "drawn": False, "wiped": False,
        "frame": "historic", "since_redraw_s": 143, "panel": "ok",
        "readings": 0, "coverage_start": None,
        "uptime_s": 110_000, "schema_version": 1,
    }
    base.update(overrides)
    return base


def _settings_ack() -> dict:
    """Every BLE-opening command re-asserts Settings first (§7.2) -- this is
    the Ack that write consumes, always the first entry in a fake radio's
    ack list."""
    return {"status": "ok", "kind": "settings", "wiped": False}


def _extract_json(out: str) -> dict:
    """`--json` output shares stdout with `_with_ble_connection`'s own
    "Scanning…"/"Found…"/"Connected…" progress lines -- the JSON body is
    the trailing `print(json.dumps(..., indent=2))`, so parse from its
    opening brace rather than assuming stdout is JSON-only."""
    return json.loads(out[out.index("{"):])


def _seed_pending(store_path, rows):
    """One pending Reading per (date, project, model) row tuple."""
    conn = usage.open_store(store_path)
    for i, (date_, project, model) in enumerate(rows):
        conn_entries = [
            usage.UsageEntry(
                request_id=f"req-{i}", message_id=f"msg-{i}", session_id=f"sess-{i}",
                project_key=project, cwd=None, local_date=date_, model=model,
                input_tokens=100, output_tokens=50, cache_write_5m_tokens=1,
                cache_write_1h_tokens=1, cache_read_tokens=1, web_search_requests=0,
                cost_usd=0.5, cost_complete=True,
                rank_sidechain=1, rank_tokens=152, rank_speed=1,
                source_file=f"/fake/{i}.jsonl", source_end_offset=100,
            )
        ]
        usage.ingest_entries(conn, conn_entries)
    conn.close()


# ---------------------------------------------------------------------------
# main() -- the store-version guard belongs around dispatch, not around
# config.resolve (which never touches the usage store at all).
# ---------------------------------------------------------------------------


def test_main_refuses_on_a_corrupt_usage_store(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    conn = usage.open_store(cfg.paths_store)
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()

    code = cli.main(["--config", str(_cfg_path(tmp_path)), "--store", str(cfg.paths_store), "status"])

    assert code == 1
    assert "Refusing to run" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# §9.2 status
# ---------------------------------------------------------------------------


def test_status_not_paired_exits_2(tmp_path, capsys):
    cfg = _cfg(tmp_path)

    code = asyncio.run(cli.cmd_status(cfg, _args(["status"])))

    out = capsys.readouterr().out
    assert code == 2
    assert "Not paired" in out
    assert "–" in out  # §8.1's glyph for not_paired


def test_status_ok_shows_all_six_checks_fixed_not_filtered(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, **{"pi.address": "AA:BB:CC:DD:EE:FF"})
    _fake_radio(monkeypatch, [_settings_ack(), _status_ack()])

    code = asyncio.run(cli.cmd_status(cfg, _args(["status"]), lock_wait_s=0.0))

    out = capsys.readouterr().out
    assert code == 0
    assert "Working" in out
    for name in verdict.CHECK_ORDER:
        assert name.capitalize() in out


def test_status_diverged_exits_1_and_headlines_readings(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, **{"pi.address": "AA:BB:CC:DD:EE:FF"})
    _seed_pending(cfg.paths_store, [("2026-09-05", "-home-a", "claude-opus-5")])
    conn = usage.open_store(cfg.paths_store)
    usage.mark_pushed(conn, "2026-09-05", "-home-a", "claude-opus-5", "2026-09-05T00:00:00+00:00")
    conn.close()
    # The Desktop sent 1 Reading; the Pi reports 0 -- a lost Batch.
    _fake_radio(monkeypatch, [_settings_ack(), _status_ack(readings=0, coverage_start=None)])

    code = asyncio.run(cli.cmd_status(cfg, _args(["status"]), lock_wait_s=0.0))

    out = capsys.readouterr().out
    assert code == 1
    assert "missing" in out.lower() or "Readings" in out


def test_status_absent_exits_2(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, **{"pi.address": "AA:BB:CC:DD:EE:FF"})
    _absent_radio(monkeypatch)

    code = asyncio.run(cli.cmd_status(cfg, _args(["status"]), lock_wait_s=0.0))

    out = capsys.readouterr().out
    assert code == 2
    assert "Can't tell" in out
    assert "unreachable" in out.lower()


def test_status_error_ack_exits_1_and_is_not_rendered_as_unreachable(tmp_path, monkeypatch, capsys):
    # Found on hardware by #87: a Pi on an image older than the Command
    # Payload answers `status` with an error Ack. It was rendered as "Can't
    # tell — the Pi is unreachable", exit 2, directly above "last seen 2s ago".
    cfg = _cfg(tmp_path, **{"pi.address": "AA:BB:CC:DD:EE:FF"})
    _fake_radio(monkeypatch, [_settings_ack(), {"status": "error", "reason": "unknown kind: 'command'"}])

    code = asyncio.run(cli.cmd_status(cfg, _args(["status"]), lock_wait_s=0.0))

    out = capsys.readouterr().out
    assert code == 1
    assert "Not working" in out
    assert "unknown kind: 'command'" in out
    assert "the pi is unreachable" not in out.lower()
    assert "re-run when the pi is back" not in out.lower()


def test_status_busy_exits_2_and_says_busy_not_unreachable(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, **{"pi.address": "AA:BB:CC:DD:EE:FF"})
    monkeypatch.setattr(push, "desktop_id", lambda *a, **kw: "deadbeefdeadbeef")

    async def never_scanned(*a, **kw):  # pragma: no cover - must not run
        raise AssertionError("the scan must not be attempted while the link is busy")

    monkeypatch.setattr(push, "find_pi", never_scanned)
    fd = _hold(push.BLE_LOCK_PATH)
    try:
        code = asyncio.run(cli.cmd_status(cfg, _args(["status"]), lock_wait_s=0.0))
    finally:
        os.close(fd)

    out = capsys.readouterr().out
    assert code == 2
    assert "busy" in out.lower()
    assert "unreachable" not in out.lower()


def test_status_json_shape(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, **{"pi.address": "AA:BB:CC:DD:EE:FF"})
    _fake_radio(monkeypatch, [_settings_ack(), _status_ack()])

    code = asyncio.run(cli.cmd_status(cfg, _args(["--json", "status"]), lock_wait_s=0.0))

    payload = _extract_json(capsys.readouterr().out)
    assert code == 0
    assert payload["verdict"] == {"ok": True, "state": "ok", "headline": "Working."}
    assert payload["reachable"] is True
    assert len(payload["checks"]) == 6
    assert [c["name"] for c in payload["checks"]] == list(verdict.CHECK_ORDER)


def test_status_json_ok_is_null_when_cant_tell(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, **{"pi.address": "AA:BB:CC:DD:EE:FF"})
    _absent_radio(monkeypatch)

    asyncio.run(cli.cmd_status(cfg, _args(["--json", "status"]), lock_wait_s=0.0))

    payload = _extract_json(capsys.readouterr().out)
    assert payload["verdict"]["ok"] is None
    assert payload["verdict"]["state"] == "cant_tell"
    assert len(payload["checks"]) == 6  # fixed, not filtered (§9.5)


def test_status_brief_is_one_line_when_ok(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, **{"pi.address": "AA:BB:CC:DD:EE:FF"})
    _fake_radio(monkeypatch, [_settings_ack(), _status_ack()])

    code = asyncio.run(cli.cmd_status(cfg, _args(["status", "--brief"]), lock_wait_s=0.0))

    out = capsys.readouterr().out
    assert code == 0
    assert "Working" in out


# ---------------------------------------------------------------------------
# §9.3 config
# ---------------------------------------------------------------------------


def test_config_bare_lists_all_three_tiers(tmp_path, capsys):
    cfg = _cfg(tmp_path)

    code = asyncio.run(cli.cmd_config(cfg, _cfg_path(tmp_path), _args(["config"])))

    out = capsys.readouterr().out
    assert code == 0
    assert "paths.projects_root" in out  # Tier 1
    assert "pi.idle_keepalive_s" in out  # Tier 2
    assert "→ Pi" in out  # the one Setting, marked
    assert "REDRAW_FLOOR_S" in out  # Tier 3, shown read-only


def test_config_get_known_key(tmp_path, capsys):
    cfg = _cfg(tmp_path)

    code = asyncio.run(cli.cmd_config(cfg, _cfg_path(tmp_path), _args(["config", "get", "batch.scheduled_hour"])))

    out = capsys.readouterr().out
    assert code == 0
    assert "batch.scheduled_hour = 4" in out


def test_config_get_unknown_key_exits_3(tmp_path, capsys):
    cfg = _cfg(tmp_path)

    code = asyncio.run(cli.cmd_config(cfg, _cfg_path(tmp_path), _args(["config", "get", "nope.nope"])))

    assert code == 3
    assert "not a known Configuration key" in capsys.readouterr().err


def test_config_set_valid_writes_and_reports_pending_restart(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    config_path = _cfg_path(tmp_path)

    code = asyncio.run(cli.cmd_config(cfg, config_path, _args(["config", "set", "batch.scheduled_hour", "6"])))

    out = capsys.readouterr().out
    assert code == 0
    assert "Pending restart" in out

    conn = config.open_config_store(config_path)
    try:
        assert config.read_config(conn)["batch.scheduled_hour"] == 6
    finally:
        conn.close()


def test_config_set_out_of_range_refuses_and_writes_nothing(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    config_path = _cfg_path(tmp_path)

    code = asyncio.run(cli.cmd_config(cfg, config_path, _args(["config", "set", "service.gauge_throttle_s", "600"])))

    err = capsys.readouterr().err
    assert code == 3
    assert "Refused" in err
    assert "derived" in err  # §9.3: the refusal explains the bound is derived
    assert "Nothing was written" in err

    conn = config.open_config_store(config_path)
    try:
        assert "service.gauge_throttle_s" not in config.read_config(conn)
    finally:
        conn.close()


def test_config_set_tier3_refused_as_not_a_setting(tmp_path, capsys):
    cfg = _cfg(tmp_path)

    code = asyncio.run(cli.cmd_config(cfg, _cfg_path(tmp_path), _args(["config", "set", "REDRAW_FLOOR_S", "120"])))

    err = capsys.readouterr().err
    assert code == 3
    assert "not a setting" in err
    assert "ADR-0008" in err


def test_config_set_pi_address_refused(tmp_path, capsys):
    cfg = _cfg(tmp_path)

    code = asyncio.run(cli.cmd_config(cfg, _cfg_path(tmp_path), _args(["config", "set", "pi.address", "AA:BB:CC:DD:EE:FF"])))

    err = capsys.readouterr().err
    assert code == 3
    assert "pair" in err


def test_config_set_unknown_key_exits_3(tmp_path, capsys):
    cfg = _cfg(tmp_path)

    code = asyncio.run(cli.cmd_config(cfg, _cfg_path(tmp_path), _args(["config", "set", "nope.nope", "1"])))

    assert code == 3
    assert "not a known Configuration key" in capsys.readouterr().err


def test_config_set_the_one_setting_pushes_immediately_when_paired(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, **{"pi.address": "AA:BB:CC:DD:EE:FF"})
    config_path = _cfg_path(tmp_path)
    client = _fake_radio(monkeypatch, [{"status": "ok", "kind": "settings", "wiped": False}])

    code = asyncio.run(
        cli.cmd_config(cfg, config_path, _args(["config", "set", "pi.idle_keepalive_s", "3600"]))
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "Pushed to the Pi" in out
    assert client.writes == [{"kind": "settings", "desktop_id": "deadbeefdeadbeef", "settings": {"idle_keepalive_s": 3600}}]


def test_config_set_the_one_setting_without_pairing_just_writes(tmp_path, capsys):
    cfg = _cfg(tmp_path)  # no pi.address
    config_path = _cfg_path(tmp_path)

    code = asyncio.run(
        cli.cmd_config(cfg, config_path, _args(["config", "set", "pi.idle_keepalive_s", "3600"]))
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "Pending restart" in out


# ---------------------------------------------------------------------------
# §9.4 The action commands
# ---------------------------------------------------------------------------


def test_pair_records_address_and_pushes_the_archive(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path)
    config_path = _cfg_path(tmp_path)
    _seed_pending(cfg.paths_store, [("2026-09-05", "-home-a", "claude-opus-5")])
    client = _fake_radio(monkeypatch, [{"status": "ok", "kind": "settings", "wiped": False}, {"status": "ok"}])

    code = asyncio.run(cli.cmd_pair(cfg, config_path, _args(["pair"])))

    out = capsys.readouterr().out
    assert code == 0
    assert "Coupled" in out
    assert "1 sent, 0 failed" in out

    conn = config.open_config_store(config_path)
    try:
        assert config.read_config(conn)["pi.address"] == "AA:BB:CC:DD:EE:FF"
    finally:
        conn.close()
    assert [w["kind"] for w in client.writes] == ["settings", "daily"]


def test_pair_keeps_push_marks_outside_the_window(tmp_path, monkeypatch, capsys):
    # Found on hardware by #87: re-pairing with the SAME Pi cleared every
    # push mark, but a Batch only re-sends the Window -- so a Reading older
    # than the Window stayed on the Pi with no mark on the Desktop, and
    # `status` said "Not working — the Pi holds 1 Reading this Desktop did not
    # send", permanently. Only the Window is re-sent, so only it is cleared.
    cfg = _cfg(tmp_path)
    _seed_pending(
        cfg.paths_store,
        [("2026-09-05", "-home-a", "claude-opus-5"), ("2026-08-01", "-home-a", "claude-opus-5")],
    )
    conn = usage.open_store(cfg.paths_store)
    for date_ in ("2026-09-05", "2026-08-01"):
        usage.mark_pushed(conn, date_, "-home-a", "claude-opus-5", "2026-09-05T00:00:00+00:00")
    conn.close()
    client = _fake_radio(monkeypatch, [_settings_ack(), {"status": "ok"}])

    code = asyncio.run(cli.cmd_pair(cfg, _cfg_path(tmp_path), _args(["pair"])))

    assert code == 0
    # The in-Window Reading is re-sent (a fresh Pi needs it) ...
    assert [w["kind"] for w in client.writes] == ["settings", "daily"]
    conn = usage.open_store(cfg.paths_store)
    try:
        summary = usage.pushed_summary(conn)
    finally:
        conn.close()
    # ... and the one outside it, which no Batch can re-send, keeps its mark.
    assert summary.readings == 2
    assert summary.coverage_start == "2026-08-01"


def test_pair_reports_an_adr_0006_wipe(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path)
    config_path = _cfg_path(tmp_path)
    _seed_pending(cfg.paths_store, [("2026-09-05", "-home-a", "claude-opus-5")])
    _fake_radio(
        monkeypatch,
        [
            {"status": "ok", "kind": "settings", "wiped": False},
            {"status": "ok", "wiped": True},
        ],
    )

    code = asyncio.run(cli.cmd_pair(cfg, config_path, _args(["pair"])))

    out = capsys.readouterr().out
    assert code == 0
    assert "wiped" in out.lower()
    assert "ADR-0006" in out


def test_pair_absent_exits_2(tmp_path, capsys, monkeypatch):
    cfg = _cfg(tmp_path)
    _absent_radio(monkeypatch)

    code = asyncio.run(cli.cmd_pair(cfg, _cfg_path(tmp_path), _args(["pair"])))

    assert code == 2
    assert "unreachable" in capsys.readouterr().err.lower()


def test_push_not_paired_exits_2(tmp_path, capsys):
    cfg = _cfg(tmp_path)

    code = asyncio.run(cli.cmd_push(cfg, _args(["push"])))

    assert code == 2
    assert "Not paired" in capsys.readouterr().err


def test_push_ok(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, **{"pi.address": "AA:BB:CC:DD:EE:FF"})
    _seed_pending(cfg.paths_store, [("2026-09-05", "-home-a", "claude-opus-5")])
    _fake_radio(monkeypatch, [{"status": "ok", "kind": "settings", "wiped": False}, {"status": "ok"}])

    code = asyncio.run(cli.cmd_push(cfg, _args(["push"]), lock_wait_s=0.0))

    out = capsys.readouterr().out
    assert code == 0
    assert "1 sent, 0 failed" in out
    assert "Settings re-assertion attempted" in out


def test_push_busy_exits_2(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, **{"pi.address": "AA:BB:CC:DD:EE:FF"})
    _seed_pending(cfg.paths_store, [("2026-09-05", "-home-a", "claude-opus-5")])
    monkeypatch.setattr(push, "desktop_id", lambda *a, **kw: "deadbeefdeadbeef")

    async def never_scanned(*a, **kw):  # pragma: no cover
        raise AssertionError("must not scan while busy")

    monkeypatch.setattr(push, "find_pi", never_scanned)
    fd = _hold(push.BLE_LOCK_PATH)
    try:
        code = asyncio.run(cli.cmd_push(cfg, _args(["push"]), lock_wait_s=0.0))
    finally:
        os.close(fd)

    assert code == 2
    assert "busy" in capsys.readouterr().err.lower()


def test_push_absent_reports_the_failed_batch(tmp_path, monkeypatch, capsys):
    # ⚠ Known, pre-existing limitation (shared with push.py's own CLI):
    # `run_batch_pass` swallows a scan/connect failure into a failed
    # BatchResult rather than raising it (spec §7.3 -- "no Pi advertising
    # fails the whole Batch"), so `push`/`pair` cannot distinguish *absent*
    # from *some rows rejected* the way `status`/`redraw`/`wipe` can via
    # `_send_command`'s direct `_with_ble_connection` call. Exit code is 1
    # here, not §9.6's 2 -- a real gap, not a chosen design, worth fixing in
    # a later ticket by having `run_batch_pass` propagate connect failures.
    cfg = _cfg(tmp_path, **{"pi.address": "AA:BB:CC:DD:EE:FF"})
    _seed_pending(cfg.paths_store, [("2026-09-05", "-home-a", "claude-opus-5")])
    _absent_radio(monkeypatch)

    code = asyncio.run(cli.cmd_push(cfg, _args(["push"]), lock_wait_s=0.0))

    assert code == 1
    assert "0 sent, 1 failed" in capsys.readouterr().out


def test_redraw_not_paired_exits_2(tmp_path, capsys):
    cfg = _cfg(tmp_path)

    code = asyncio.run(cli.cmd_redraw(cfg, _args(["redraw"])))

    assert code == 2
    assert "Not paired" in capsys.readouterr().err


def test_redraw_drawn_now(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, **{"pi.address": "AA:BB:CC:DD:EE:FF"})
    _fake_radio(
        monkeypatch,
        [
            {"status": "ok", "kind": "settings", "wiped": False},
            {"status": "ok", "kind": "command", "verb": "redraw", "drawn": True, "wiped": False},
        ],
    )

    code = asyncio.run(cli.cmd_redraw(cfg, _args(["redraw"]), lock_wait_s=0.0))

    out = capsys.readouterr().out
    assert code == 0
    assert "Drawn" in out


def test_redraw_queued_names_its_own_wait(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, **{"pi.address": "AA:BB:CC:DD:EE:FF"})
    _fake_radio(
        monkeypatch,
        [
            {"status": "ok", "kind": "settings", "wiped": False},
            {
                "status": "ok", "kind": "command", "verb": "redraw",
                "drawn": False, "floor_remaining_s": 202, "wiped": False,
            },
        ],
    )

    code = asyncio.run(cli.cmd_redraw(cfg, _args(["redraw"]), lock_wait_s=0.0))

    out = capsys.readouterr().out
    assert code == 0
    assert "Queued" in out
    assert "ADR-0008" in out


def test_redraw_busy_exits_2(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, **{"pi.address": "AA:BB:CC:DD:EE:FF"})
    monkeypatch.setattr(push, "desktop_id", lambda *a, **kw: "deadbeefdeadbeef")

    async def never_scanned(*a, **kw):  # pragma: no cover
        raise AssertionError("must not scan while busy")

    monkeypatch.setattr(push, "find_pi", never_scanned)
    fd = _hold(push.BLE_LOCK_PATH)
    try:
        code = asyncio.run(cli.cmd_redraw(cfg, _args(["redraw"]), lock_wait_s=0.0))
    finally:
        os.close(fd)

    assert code == 2
    assert "busy" in capsys.readouterr().err.lower()


def test_short_id_is_first_three_octets_lowercase():
    assert cli._short_id("e4:5F:01:9c:2d:aa") == "e45f01"


def test_wipe_not_paired_exits_2(tmp_path, capsys):
    cfg = _cfg(tmp_path)

    code = asyncio.run(cli.cmd_wipe(cfg, _args(["wipe"])))

    assert code == 2
    assert "Not paired" in capsys.readouterr().err


def test_wipe_confirmation_mismatch_sends_nothing(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, **{"pi.address": "AA:BB:CC:DD:EE:FF"})

    async def never_scanned(*a, **kw):  # pragma: no cover
        raise AssertionError("must not attempt BLE without a matching confirmation")

    monkeypatch.setattr(push, "find_pi", never_scanned)

    code = asyncio.run(cli.cmd_wipe(cfg, _args(["wipe"]), confirm_fn=lambda prompt: "wrong"))

    assert code == 3
    assert "did not match" in capsys.readouterr().err.lower()


def test_wipe_confirmed_wipes_and_repushes(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, **{"pi.address": "AA:BB:CC:DD:EE:FF"})
    # A Reading already marked pushed -- proves `clear_pushed_marks` really
    # ran, since otherwise the re-push connection never opens at all.
    _seed_pending(cfg.paths_store, [("2026-09-05", "-home-a", "claude-opus-5")])
    conn = usage.open_store(cfg.paths_store)
    usage.mark_pushed(conn, "2026-09-05", "-home-a", "claude-opus-5", "2026-09-05T00:00:00+00:00")
    conn.close()

    client = _fake_radio(
        monkeypatch,
        [
            {"status": "ok", "kind": "settings", "wiped": False},
            {"status": "ok", "kind": "command", "verb": "wipe", "wiped": True},
            {"status": "ok", "kind": "settings", "wiped": False},
            {"status": "ok"},
        ],
    )

    code = asyncio.run(
        cli.cmd_wipe(cfg, _args(["wipe"]), confirm_fn=lambda prompt: "aabbcc", lock_wait_s=0.0)
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "Wiped" in out
    assert "1 sent, 0 failed" in out
    assert [w["kind"] for w in client.writes] == ["settings", "command", "settings", "daily"]


def test_wipe_busy_exits_2_after_confirmation(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, **{"pi.address": "AA:BB:CC:DD:EE:FF"})
    monkeypatch.setattr(push, "desktop_id", lambda *a, **kw: "deadbeefdeadbeef")

    async def never_scanned(*a, **kw):  # pragma: no cover
        raise AssertionError("must not scan while busy")

    monkeypatch.setattr(push, "find_pi", never_scanned)
    fd = _hold(push.BLE_LOCK_PATH)
    try:
        code = asyncio.run(
            cli.cmd_wipe(cfg, _args(["wipe"]), confirm_fn=lambda prompt: "aabbcc", lock_wait_s=0.0)
        )
    finally:
        os.close(fd)

    assert code == 2
    assert "busy" in capsys.readouterr().err.lower()


def test_wipe_reports_a_busy_link_during_the_repush_instead_of_crashing(tmp_path, monkeypatch, capsys):
    # Review fix: the wipe verb itself can succeed and then find the link
    # busy for the follow-up archive re-push -- that must be reported like
    # every other refusal, not left to propagate as an unhandled exception.
    cfg = _cfg(tmp_path, **{"pi.address": "AA:BB:CC:DD:EE:FF"})
    _fake_radio(
        monkeypatch,
        [
            _settings_ack(),
            {"status": "ok", "kind": "command", "verb": "wipe", "wiped": True},
        ],
    )

    async def busy_run_batch_pass(*a, **kw):
        raise push.BleLinkBusy("the BLE link is busy: held by another zeropi-display job")

    monkeypatch.setattr(push, "run_batch_pass", busy_run_batch_pass)

    code = asyncio.run(
        cli.cmd_wipe(cfg, _args(["wipe"]), confirm_fn=lambda prompt: "aabbcc", lock_wait_s=0.0)
    )

    err = capsys.readouterr().err
    assert code == 2
    assert "Wiped, but could not re-push" in err


# ---------------------------------------------------------------------------
# `restart`
# ---------------------------------------------------------------------------


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr


def test_restart_success(capsys):
    def fake_run(*a, **kw):
        return _FakeCompletedProcess(returncode=0)

    code = cli.cmd_restart(run_fn=fake_run)

    assert code == 0
    assert "Restarted" in capsys.readouterr().out


def test_restart_failure_exits_3(capsys):
    def fake_run(*a, **kw):
        return _FakeCompletedProcess(returncode=1, stderr="Unit not found.")

    code = cli.cmd_restart(run_fn=fake_run)

    assert code == 3
    assert "Unit not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _service_info
# ---------------------------------------------------------------------------


def test_service_info_unknown_when_systemctl_unavailable():
    def fake_run(*a, **kw):
        raise FileNotFoundError("no systemctl")

    info = cli._service_info(run_fn=fake_run)

    assert info.state == "unknown"
    assert info.uptime_s is None


def test_service_info_running_when_active():
    class _Result:
        returncode = 0
        stdout = "ActiveState=active\nActiveEnterTimestamp=n/a\n"

    info = cli._service_info(run_fn=lambda *a, **kw: _Result())

    assert info.state == "running"
    assert info.uptime_s is None  # "n/a" timestamp: not parseable, and that's fine


# ---------------------------------------------------------------------------
# TIER3 -- the hand-maintained mirrors of the Pi's own constants must not
# drift silently (the same risk config.py's REDRAW_FLOOR_S/GAUGE_EXPIRY_S
# mirrors carry, guarded the same way: a test against the real source).
# ---------------------------------------------------------------------------


def test_tier3_mirrors_match_the_pis_real_constants():
    import receive
    import render

    assert cli.TIER3["REDRAW_FLOOR_S"].value == str(receive.REDRAW_FLOOR_S)
    assert cli.TIER3["GAUGE_EXPIRY_S"].value == str(receive.GAUGE_EXPIRY_S)
    assert str(receive.MAX_ACK_BYTES) in cli.TIER3["MAX_ACK_BYTES"].value
    assert cli.TIER3["PANEL_W, PANEL_H"].value == f"{render.PANEL_W}, {render.PANEL_H}"
    assert cli.TIER3["WATCHDOG_TIMEOUT_S"].value == str(render.WATCHDOG_TIMEOUT_S)
    assert render.FONT_DIR in cli.TIER3["FONT_DIR"].value
