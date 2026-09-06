"""Pure-SQLite tests for pi/receive.py's DB layer, wipe logic, payload
validation, Ack shape, and the redraw floor (spec §8, §6.3, §6.4).

No BLE, no bluezero, no real Pi — `receive` is decoupled from the BLE stack
(see tests/test_receive_importable.py), so these run against a temp SQLite
file exactly as they would in CI. `pi` is on `pythonpath` via pytest.ini.
"""

import json
import sqlite3

import pytest

import receive


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "data.db")


def daily_payload(**overrides):
    payload = {
        "kind": "daily",
        "desktop_id": "desktop-a",
        "batch_size": 1,
        "batch_index": 0,
        "date": "2026-09-05",
        "project": "-home-ryzen-git-zeropi-display",
        "model": "claude-opus-5",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_creation_tokens": 10,
        "cache_read_tokens": 5,
        "cost_usd": 1.2345,
        "session_count": 1,
        "cost_complete": True,
    }
    payload.update(overrides)
    return payload


def gauge_payload(**overrides):
    payload = {
        "kind": "gauge",
        "desktop_id": "desktop-a",
        "snapshot_age_s": 5,
        "five_hour": {"pct": 30, "resets_in_s": 9000},
        "seven_day": {"pct": 18, "resets_in_s": 400000},
        "context": None,
    }
    payload.update(overrides)
    return payload


def encode_value(payload_dict) -> list:
    return list(json.dumps(payload_dict).encode("utf-8"))


# ---------------------------------------------------------------------------
# §8.1 The version gate
# ---------------------------------------------------------------------------


def test_init_db_creates_schema_from_scratch(db_path):
    receive.init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == receive.SCHEMA_VERSION
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"readings", "meta"} <= tables
    finally:
        conn.close()


def test_init_db_drops_and_recreates_on_version_mismatch(db_path):
    # Simulate an old/stale DB: user_version = 0 (a fresh Pi reads this too),
    # with a readings table shaped like the OLD schema so we can tell it got
    # replaced.
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE readings (id INTEGER PRIMARY KEY, junk TEXT)")
    conn.execute("INSERT INTO readings (junk) VALUES ('stale')")
    conn.commit()
    conn.close()

    receive.init_db(db_path)

    conn = sqlite3.connect(db_path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == receive.SCHEMA_VERSION
        columns = {row[1] for row in conn.execute("PRAGMA table_info(readings)")}
        assert "junk" not in columns
        assert "project" in columns
        assert conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 0
    finally:
        conn.close()


def test_init_db_is_idempotent_on_matching_version(db_path):
    receive.init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO readings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("2026-09-01", "proj", "model", 1, 1, 0, 0, 0.0, 1, 1),
    )
    conn.commit()
    conn.close()

    receive.init_db(db_path)  # same version -> must not wipe

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# §8.2 Upsert + Coverage Start
# ---------------------------------------------------------------------------


def test_upsert_is_last_write_wins(db_path):
    receive.init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        receive.upsert_reading(conn, daily_payload(input_tokens=100))
        receive.upsert_reading(conn, daily_payload(input_tokens=200))
        conn.commit()
        row = conn.execute(
            "SELECT input_tokens FROM readings WHERE date = ? AND project = ? AND model = ?",
            ("2026-09-05", "-home-ryzen-git-zeropi-display", "claude-opus-5"),
        ).fetchone()
        assert row[0] == 200
        assert conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 1
    finally:
        conn.close()


def test_coverage_start_adopts_earlier_date(db_path):
    receive.init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        receive.upsert_reading(conn, daily_payload(date="2026-09-05"))
        conn.commit()
        assert receive._get_meta(conn, "coverage_start") == "2026-09-05"

        receive.upsert_reading(conn, daily_payload(date="2026-09-01", model="claude-sonnet-5"))
        conn.commit()
        assert receive._get_meta(conn, "coverage_start") == "2026-09-01"
    finally:
        conn.close()


def test_coverage_start_never_moves_forward(db_path):
    receive.init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        receive.upsert_reading(conn, daily_payload(date="2026-09-01"))
        conn.commit()
        assert receive._get_meta(conn, "coverage_start") == "2026-09-01"

        # A later push with a more recent date must not move it forward.
        receive.upsert_reading(conn, daily_payload(date="2026-09-10", model="claude-sonnet-5"))
        conn.commit()
        assert receive._get_meta(conn, "coverage_start") == "2026-09-01"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# §8.3 The Desktop Id wipe
# ---------------------------------------------------------------------------


def test_desktop_id_absent_adopts_without_wipe(db_path):
    receive.init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        wiped = receive.check_desktop_id(conn, "desktop-a")
        conn.commit()
        assert wiped is False
        assert receive._get_meta(conn, "desktop_id") == "desktop-a"
    finally:
        conn.close()


def test_desktop_id_change_wipes_readings_and_coverage_start(db_path):
    receive.init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        receive.check_desktop_id(conn, "desktop-a")
        receive.upsert_reading(conn, daily_payload())
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 1
        assert receive._get_meta(conn, "coverage_start") is not None

        wiped = receive.check_desktop_id(conn, "desktop-b")
        conn.commit()

        assert wiped is True
        assert conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 0
        assert receive._get_meta(conn, "coverage_start") is None
        assert receive._get_meta(conn, "desktop_id") == "desktop-b"
    finally:
        conn.close()


def test_desktop_id_wiped_flag_set_on_exactly_one_ack(db_path):
    receive.init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        receive.check_desktop_id(conn, "desktop-a")
        conn.commit()

        first = receive.check_desktop_id(conn, "desktop-b")
        conn.commit()
        assert first is True

        # The very next Payload from the SAME (new) Desktop must not wipe again.
        second = receive.check_desktop_id(conn, "desktop-b")
        conn.commit()
        assert second is False
    finally:
        conn.close()


def test_desktop_id_wipe_runs_after_validation_in_on_write(db_path, monkeypatch):
    # A malformed Payload must never trigger a wipe, even with a changed
    # desktop_id inside it — validation must reject it first.
    monkeypatch.setattr(receive, "DB_PATH", db_path)
    receive.init_db(db_path)
    receive.ReceiveState.db_path = db_path

    conn = sqlite3.connect(db_path)
    receive.check_desktop_id(conn, "desktop-a")
    receive.upsert_reading(conn, daily_payload())
    conn.commit()
    conn.close()

    acks = []
    receive.ReceiveState.ack_characteristic = object()
    monkeypatch.setattr(receive.ReceiveState, "send_ack", classmethod(lambda cls, ack: acks.append(ack)))

    malformed = daily_payload(desktop_id="desktop-b")
    del malformed["model"]  # missing required field -> validation error
    receive.ReceiveState.on_write(encode_value(malformed), {})

    assert acks[0]["status"] == "error"
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 1
        assert receive._get_meta(conn, "desktop_id") == "desktop-a"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# §6.4 Payload validation
# ---------------------------------------------------------------------------


def test_parse_payload_rejects_non_utf8():
    with pytest.raises(receive.PayloadError):
        receive.parse_payload([0xFF, 0xFE, 0xFD])


def test_parse_payload_rejects_non_json_object():
    with pytest.raises(receive.PayloadError):
        receive.parse_payload(encode_value(["not", "an", "object"]))


def test_parse_payload_rejects_missing_kind():
    payload = daily_payload()
    del payload["kind"]
    with pytest.raises(receive.PayloadError) as exc_info:
        receive.parse_payload(encode_value(payload))
    assert exc_info.value.kind is None


def test_parse_payload_rejects_unrecognised_kind():
    payload = daily_payload(kind="weekly")
    with pytest.raises(receive.PayloadError) as exc_info:
        receive.parse_payload(encode_value(payload))
    assert "unknown kind" in exc_info.value.reason


def test_parse_payload_rejects_missing_desktop_id():
    payload = daily_payload()
    del payload["desktop_id"]
    with pytest.raises(receive.PayloadError) as exc_info:
        receive.parse_payload(encode_value(payload))
    assert exc_info.value.kind == "daily"


@pytest.mark.parametrize("field", list(receive.DAILY_REQUIRED_FIELDS))
def test_parse_payload_rejects_each_missing_daily_field(field):
    payload = daily_payload()
    del payload[field]
    with pytest.raises(receive.PayloadError) as exc_info:
        receive.parse_payload(encode_value(payload))
    assert field in exc_info.value.reason


@pytest.mark.parametrize("field", list(receive.GAUGE_REQUIRED_FIELDS))
def test_parse_payload_rejects_each_missing_gauge_field(field):
    payload = gauge_payload()
    del payload[field]
    with pytest.raises(receive.PayloadError):
        receive.parse_payload(encode_value(payload))


def test_parse_payload_allows_null_pct_and_resets_in_s():
    payload = gauge_payload(
        five_hour={"pct": None, "resets_in_s": None},
        seven_day={"pct": None, "resets_in_s": None},
    )
    parsed = receive.parse_payload(encode_value(payload))
    assert parsed["five_hour"]["pct"] is None


def test_parse_payload_allows_absent_or_null_context():
    payload = gauge_payload()
    del payload["context"]
    receive.parse_payload(encode_value(payload))  # must not raise

    payload2 = gauge_payload(context=None)
    receive.parse_payload(encode_value(payload2))  # must not raise


def test_parse_payload_valid_daily_and_gauge_round_trip():
    daily = receive.parse_payload(encode_value(daily_payload()))
    assert daily["kind"] == "daily"
    gauge = receive.parse_payload(encode_value(gauge_payload()))
    assert gauge["kind"] == "gauge"


# ---------------------------------------------------------------------------
# §6.3 Ack shape
# ---------------------------------------------------------------------------


def test_build_ack_daily_ok_shape():
    ack = receive.build_ack(
        "ok", kind="daily", date="2026-09-05", project="proj", model="claude-opus-5",
        drawn=True, wiped=False,
    )
    assert ack == {
        "status": "ok",
        "kind": "daily",
        "date": "2026-09-05",
        "project": "proj",
        "model": "claude-opus-5",
        "drawn": True,
        "wiped": False,
    }
    assert "received_at" not in ack


def test_build_ack_gauge_ok_shape():
    ack = receive.build_ack("ok", kind="gauge", drawn=True, wiped=False)
    assert ack == {"status": "ok", "kind": "gauge", "drawn": True, "wiped": False}


def test_build_ack_error_shape_has_reason():
    ack = receive.build_ack("error", kind="daily", reason="missing field(s): model")
    assert ack["status"] == "error"
    assert ack["reason"] == "missing field(s): model"
    assert ack["drawn"] is False
    assert ack["wiped"] is False


# ---------------------------------------------------------------------------
# §8.5 The redraw floor
# ---------------------------------------------------------------------------


def test_redraw_gate_draws_immediately_when_never_drawn():
    gate = receive.RedrawGate()
    drawn = gate.try_draw_historic_now({"frame": 1}, now=0.0)
    assert drawn is True
    assert gate.last_drawn_at == 0.0


def test_redraw_gate_coalesces_inside_the_floor():
    gate = receive.RedrawGate()
    assert gate.try_draw_historic_now({"frame": 1}, now=0.0) is True
    # Well inside the 300s floor.
    drawn = gate.try_draw_historic_now({"frame": 2}, now=100.0)
    assert drawn is False
    assert gate.historic_pending is True
    assert gate.last_drawn_at == 0.0  # unchanged


def test_redraw_gate_allows_redraw_once_floor_elapses():
    gate = receive.RedrawGate()
    gate.try_draw_historic_now({"frame": 1}, now=0.0)
    gate.try_draw_historic_now({"frame": 2}, now=100.0)  # coalesced
    drawn = gate.try_draw_historic_now({"frame": 3}, now=300.0)
    assert drawn is True
    assert gate.last_drawn_at == 300.0


def test_redraw_gate_if_due_skips_when_nothing_new(monkeypatch):
    rendered = []
    monkeypatch.setattr(receive, "render", lambda view: rendered.append(view))
    gate = receive.RedrawGate()
    gate.try_draw_historic_now({"initial": True}, now=0.0)
    # Floor has elapsed, but nothing is pending and idle keepalive isn't due.
    drawn = gate.try_draw_historic_if_due({"historic": True}, now=400.0)
    assert drawn is False
    assert rendered == [{"initial": True}]


def test_redraw_gate_if_due_draws_when_pending():
    gate = receive.RedrawGate()
    gate.try_draw_historic_now({"initial": True}, now=0.0)
    gate.mark_historic_pending()
    drawn = gate.try_draw_historic_if_due({"historic": True}, now=400.0)
    assert drawn is True


def test_redraw_gate_if_due_draws_on_idle_keepalive():
    gate = receive.RedrawGate()
    gate.try_draw_historic_now({"initial": True}, now=0.0)
    drawn = gate.try_draw_historic_if_due(
        {"historic": True}, now=receive.IDLE_KEEPALIVE_S + 1
    )
    assert drawn is True


def test_gauge_draw_does_not_clear_pending_historic_redraw():
    # A live Gauge frame drawing on the floor must not silently swallow a
    # Historic redraw that was coalesced earlier and never actually drawn
    # (the code-review finding this regression-tests).
    gate = receive.RedrawGate()
    gate.try_draw_historic_now({"initial": True}, now=0.0)
    # A Reading arrives while a Gauge is live: coalesced, marks pending.
    assert gate.try_draw_historic_now({"historic": True}, now=50.0) is False
    assert gate.historic_pending is True
    # The Gauge's own countdown redraws once the floor allows.
    assert gate.try_draw_gauge({"gauge": True}, now=300.0) is True
    # The pending Historic redraw must survive the Gauge draw.
    assert gate.historic_pending is True


def test_periodic_tick_forces_historic_fallback_on_gauge_expiry(monkeypatch):
    # Spec §9.2 / ADR-0010: an expired Gauge must fall back to the Historic
    # View, even with no new Payload ever arriving to trigger it. Regression
    # test for a code-review finding: without an expiry-transition check, a
    # Desktop that simply stops pushing left the stale Gauge frame on the
    # panel forever, since neither historic_pending nor the idle keepalive
    # would ever become true on their own.
    fake_now = [0.0]
    monkeypatch.setattr(receive.time, "monotonic", lambda: fake_now[0])

    rendered = []
    monkeypatch.setattr(receive, "render", lambda view: rendered.append(view))

    receive.ReceiveState.gauge = receive.GaugeState()
    receive.ReceiveState.redraw_gate = receive.RedrawGate()
    receive.ReceiveState._gauge_was_shown = False

    receive.ReceiveState.gauge.update(gauge_payload(snapshot_age_s=0))
    receive.ReceiveState.periodic_tick()  # draws the live Gauge frame
    assert rendered[-1] == receive.ReceiveState.gauge.view()

    # Advance well past the 300s Gauge expiry, but nothing else happens —
    # no new Payload, no Reading.
    fake_now[0] = 400.0
    receive.ReceiveState.periodic_tick()
    assert rendered[-1] == {"historic": True}  # forced fallback, not silence


def test_on_write_daily_coalesced_reports_drawn_false(db_path, monkeypatch):
    monkeypatch.setattr(receive, "DB_PATH", db_path)
    receive.init_db(db_path)
    receive.ReceiveState.db_path = db_path
    receive.ReceiveState.ack_characteristic = object()
    receive.ReceiveState.redraw_gate = receive.RedrawGate()
    receive.ReceiveState.gauge = receive.GaugeState()

    acks = []
    monkeypatch.setattr(receive.ReceiveState, "send_ack", classmethod(lambda cls, ack: acks.append(ack)))

    receive.ReceiveState.on_write(encode_value(daily_payload(date="2026-09-01")), {})
    assert acks[0]["drawn"] is True  # first-ever redraw, floor trivially elapsed

    receive.ReceiveState.on_write(encode_value(daily_payload(date="2026-09-02")), {})
    assert acks[1]["drawn"] is False  # inside the 300s floor -> coalesced


def test_on_write_daily_db_error_echoes_correlation_fields(db_path, monkeypatch):
    monkeypatch.setattr(receive, "DB_PATH", db_path)
    receive.init_db(db_path)
    receive.ReceiveState.db_path = db_path
    receive.ReceiveState.ack_characteristic = object()
    receive.ReceiveState.redraw_gate = receive.RedrawGate()
    receive.ReceiveState.gauge = receive.GaugeState()

    def _boom(conn, payload):
        raise sqlite3.OperationalError("simulated disk full")

    monkeypatch.setattr(receive, "upsert_reading", _boom)

    acks = []
    monkeypatch.setattr(receive.ReceiveState, "send_ack", classmethod(lambda cls, ack: acks.append(ack)))

    receive.ReceiveState.on_write(encode_value(daily_payload()), {})

    assert acks[0]["status"] == "error"
    assert acks[0]["date"] == "2026-09-05"
    assert acks[0]["project"] == "-home-ryzen-git-zeropi-display"
    assert acks[0]["model"] == "claude-opus-5"


# ---------------------------------------------------------------------------
# §8.4 The Gauge state machine
# ---------------------------------------------------------------------------


def test_gauge_never_written_to_sqlite(db_path, monkeypatch):
    monkeypatch.setattr(receive, "DB_PATH", db_path)
    receive.init_db(db_path)
    receive.ReceiveState.db_path = db_path
    receive.ReceiveState.ack_characteristic = object()
    receive.ReceiveState.redraw_gate = receive.RedrawGate()
    receive.ReceiveState.gauge = receive.GaugeState()
    monkeypatch.setattr(receive.ReceiveState, "send_ack", classmethod(lambda cls, ack: None))

    receive.ReceiveState.on_write(encode_value(gauge_payload()), {})

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 0
    finally:
        conn.close()


def test_gauge_age_seeded_with_snapshot_age_s():
    gauge = receive.GaugeState()
    gauge.update(gauge_payload(snapshot_age_s=42), now=0.0)
    assert gauge.gauge_age_s(now=0.0) == 42
    assert gauge.gauge_age_s(now=10.0) == 52


def test_gauge_expires_at_300s_age():
    gauge = receive.GaugeState()
    gauge.update(gauge_payload(snapshot_age_s=0), now=0.0)
    assert gauge.is_expired(now=299.0) is False
    assert gauge.is_expired(now=300.0) is True


def test_reset_countdown_clamps_at_zero_without_expiring():
    gauge = receive.GaugeState()
    gauge.update(gauge_payload(five_hour={"pct": 50, "resets_in_s": 100}), now=0.0)
    view = gauge.view(now=200.0)
    assert view["five_hour"]["resets_in_s"] == 0
    assert gauge.is_expired(now=200.0) is False  # countdown hit zero, gauge did not expire


def test_reset_countdown_null_survives_as_null():
    gauge = receive.GaugeState()
    gauge.update(gauge_payload(five_hour={"pct": None, "resets_in_s": None}), now=0.0)
    view = gauge.view(now=10.0)
    assert view["five_hour"]["resets_in_s"] is None
    assert view["five_hour"]["pct"] is None
