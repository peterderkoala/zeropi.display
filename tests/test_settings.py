"""Tests for pi/receive.py's configuration seam (#82): Settings persistence
under a `setting.` prefix, the ADR-0006 hand-off wipe clearing them, and
RedrawGate's idle keepalive as a live-looked-up value rather than a value
captured once at construction (spec §6, tested per §10.4).

No BLE — pure SQLite, exactly as tests/test_receive.py.
"""

import sqlite3

import pytest

import receive


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "data.db")


@pytest.fixture
def conn(db_path):
    receive.init_db(db_path)
    c = sqlite3.connect(db_path)
    try:
        yield c
    finally:
        c.close()


# ---------------------------------------------------------------------------
# §6.2 Settings persist, under a prefix
# ---------------------------------------------------------------------------


def test_apply_settings_writes_setting_prefixed_rows(conn):
    receive.apply_settings(conn, {"idle_keepalive_s": 3600})
    conn.commit()

    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?", ("setting.idle_keepalive_s",)
    ).fetchone()
    assert row is not None
    assert row[0] == "3600"


def test_get_setting_returns_default_when_never_set(conn):
    assert receive.get_setting(conn, "idle_keepalive_s", receive.IDLE_KEEPALIVE_S) == receive.IDLE_KEEPALIVE_S


def test_get_setting_returns_persisted_value_coerced(conn):
    receive.apply_settings(conn, {"idle_keepalive_s": "7200"})
    conn.commit()
    assert receive.get_setting(conn, "idle_keepalive_s", receive.IDLE_KEEPALIVE_S) == 7200
    assert isinstance(receive.get_setting(conn, "idle_keepalive_s", 0), int)


def test_apply_settings_survives_a_reconnect(db_path):
    # Spec §6.2: not for the Pi's own benefit -- a reboot must not silently
    # revert a Setting to the compiled default.
    conn1 = sqlite3.connect(db_path)
    receive.init_db(db_path)
    receive.apply_settings(conn1, {"idle_keepalive_s": 1800})
    conn1.commit()
    conn1.close()

    conn2 = sqlite3.connect(db_path)
    try:
        assert receive.get_setting(conn2, "idle_keepalive_s", receive.IDLE_KEEPALIVE_S) == 1800
    finally:
        conn2.close()


def test_apply_settings_rejects_unrecognised_key_with_a_reason(conn):
    with pytest.raises(receive.UnknownSettingError, match="not_a_real_setting"):
        receive.apply_settings(conn, {"not_a_real_setting": 1})


def test_apply_settings_rejects_value_that_fails_to_coerce_before_writing_anything(conn):
    # A code-review finding: the old implementation coerced inside the same
    # loop that wrote to meta, so a bad value raised a bare ValueError
    # instead of a typed error, and (once a second Setting exists) could
    # leave an earlier key's write sitting in the uncommitted transaction.
    with pytest.raises(receive.SettingValueError):
        receive.apply_settings(conn, {"idle_keepalive_s": "not a number"})
    conn.commit()
    rows = conn.execute("SELECT value FROM meta WHERE key LIKE 'setting.%'").fetchall()
    assert rows == []


def test_apply_settings_rejects_unknown_key_before_writing_anything(conn):
    with pytest.raises(receive.UnknownSettingError):
        receive.apply_settings(conn, {"idle_keepalive_s": 999, "bogus": 1})
    conn.commit()
    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?", ("setting.idle_keepalive_s",)
    ).fetchone()
    assert row is None  # the whole dict was rejected, not just the bad key


# ---------------------------------------------------------------------------
# §6.3 The code default, and the hand-off wipe
# ---------------------------------------------------------------------------


def test_desktop_id_change_clears_settings_to_code_default(conn):
    receive.check_desktop_id(conn, "desktop-a")
    receive.apply_settings(conn, {"idle_keepalive_s": 60})
    conn.commit()
    assert receive.get_setting(conn, "idle_keepalive_s", receive.IDLE_KEEPALIVE_S) == 60

    wiped = receive.check_desktop_id(conn, "desktop-b")
    conn.commit()

    assert wiped is True
    # Back to the code default, not merely "no row" -- get_setting's fallback.
    assert receive.get_setting(conn, "idle_keepalive_s", receive.IDLE_KEEPALIVE_S) == receive.IDLE_KEEPALIVE_S
    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?", ("setting.idle_keepalive_s",)
    ).fetchone()
    assert row is None


def test_desktop_id_first_adoption_does_not_touch_settings(conn):
    # No wipe on first contact (spec §8.3) -- a fresh Pi's Settings (there
    # are none yet) must not be disturbed by simply learning a desktop_id.
    receive.apply_settings(conn, {"idle_keepalive_s": 42})
    conn.commit()
    wiped = receive.check_desktop_id(conn, "desktop-a")
    conn.commit()
    assert wiped is False
    assert receive.get_setting(conn, "idle_keepalive_s", receive.IDLE_KEEPALIVE_S) == 42


def test_same_desktop_id_again_does_not_clear_settings(conn):
    receive.check_desktop_id(conn, "desktop-a")
    receive.apply_settings(conn, {"idle_keepalive_s": 42})
    conn.commit()
    wiped = receive.check_desktop_id(conn, "desktop-a")
    conn.commit()
    assert wiped is False
    assert receive.get_setting(conn, "idle_keepalive_s", receive.IDLE_KEEPALIVE_S) == 42


# ---------------------------------------------------------------------------
# §6.1 Settings apply live -- RedrawGate reads a looked-up value, not one
# captured once at construction. A test that only checks the row was
# written would pass against the bug (spec §10.4's own warning).
# ---------------------------------------------------------------------------


def test_bare_redraw_gate_uses_the_code_default():
    gate = receive.RedrawGate()
    gate.try_draw_historic_now({"initial": True}, now=0.0)
    assert gate.try_draw_historic_if_due({}, now=receive.IDLE_KEEPALIVE_S - 1) is False
    assert gate.try_draw_historic_if_due({}, now=receive.IDLE_KEEPALIVE_S + 1) is True


def test_redraw_gate_idle_elapsed_follows_an_injected_getter():
    current = {"idle_keepalive_s": 100}
    gate = receive.RedrawGate(idle_keepalive_s=lambda: current["idle_keepalive_s"])
    gate.try_draw_historic_now({"initial": True}, now=0.0)

    # Under the initial 100s value.
    assert gate.try_draw_historic_if_due({}, now=50.0) is False


def test_redraw_gate_idle_elapsed_follows_a_setting_change_with_no_new_gate():
    # The regression this ticket exists to prevent: the SAME RedrawGate
    # instance, mid-run, must see a Setting change without being
    # reconstructed -- proving the lookup is live, not cached at
    # construction (spec §6.1, §10.4).
    current = {"idle_keepalive_s": 1000}
    gate = receive.RedrawGate(idle_keepalive_s=lambda: current["idle_keepalive_s"])
    gate.try_draw_historic_now({"initial": True}, now=0.0)

    # Not yet due under the original 1000s value.
    assert gate.try_draw_historic_if_due({}, now=500.0) is False

    # The Setting changes -- no new RedrawGate, no restart.
    current["idle_keepalive_s"] = 100

    # Now due under the NEW value, at the SAME elapsed time (500s), proving
    # `_idle_elapsed` looked the value up again rather than using a value
    # captured when the gate was constructed.
    assert gate.try_draw_historic_if_due({}, now=500.0) is True


def test_live_idle_keepalive_s_reads_the_persisted_setting(db_path, monkeypatch):
    receive.init_db(db_path)
    monkeypatch.setattr(receive.ReceiveState, "db_path", db_path)

    assert receive._live_idle_keepalive_s() == receive.IDLE_KEEPALIVE_S

    conn = sqlite3.connect(db_path)
    receive.apply_settings(conn, {"idle_keepalive_s": 55})
    conn.commit()
    conn.close()

    assert receive._live_idle_keepalive_s() == 55
