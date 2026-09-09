"""Tests for wiring rendering into pi/receive.py (spec §7, §8, §9, ticket
#63): render() as a real hand-off to the worker, the startup draw, and the
watchdog hooked into the existing periodic tick.

The gate, the floor, the expiry fallback and the Gauge state machine are
untouched (they're already correct and hardware-verified, per the ticket) --
these tests exercise only what changed: what render() actually builds and
hands off, and that nothing here needs bluezero, a panel, or SPI.
"""

import sqlite3

import pytest

import receive
import render


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


class FakeWorker:
    def __init__(self):
        self.submitted = []
        self.watchdog_checks = 0

    def submit(self, image):
        self.submitted.append(image)

    def check_watchdog(self):
        self.watchdog_checks += 1


@pytest.fixture(autouse=True)
def _reset_panel_worker(monkeypatch):
    # ReceiveState._panel_worker is set by main() in production; every test
    # starts from "no worker configured" unless it opts in.
    monkeypatch.setattr(receive.ReceiveState, "_panel_worker", None)


def _images_equal(a, b) -> bool:
    return a.size == b.size and a.mode == b.mode and a.tobytes() == b.tobytes()


# ---------------------------------------------------------------------------
# render() with no worker configured -- must stay a safe no-op
# ---------------------------------------------------------------------------


def test_render_noops_when_no_worker_is_configured():
    # No _panel_worker set (the autouse fixture above), and a view shape
    # that would previously have been an arbitrary stub-friendly dict.
    receive.render({"anything": "at all"})  # must not raise


def test_render_survives_a_frame_build_failure_without_crashing(monkeypatch):
    # _build_frame runs inline on the caller's thread (the BLE event loop,
    # or main() at startup) -- a failure in it (a missing font, a corrupt
    # DB read) must not propagate and crash the link (spec §8: receiving,
    # persisting and Acking never depend on the display).
    def _boom(view):
        raise OSError("cannot open resource")

    monkeypatch.setattr(receive, "_build_frame", _boom)
    worker = FakeWorker()
    monkeypatch.setattr(receive.ReceiveState, "_panel_worker", worker)

    receive.render({"historic": True})  # must not raise

    assert worker.submitted == []


def test_render_build_failure_on_historic_is_not_re_queued(monkeypatch):
    # Documents an accepted, bounded gap: try_draw_historic_now() already
    # reports this redraw as accepted and clears historic_pending right
    # after calling render() (RedrawGate's own contract, out of scope to
    # change here), so a build failure is NOT re-queued for immediate
    # retry -- it self-heals only on the next Reading or the 24h idle
    # keep-alive. Acceptable because this path is deterministic (a
    # missing font, a DB file proven writable moments earlier), not
    # transient, and it is always logged when it happens.
    def _boom(view):
        raise OSError("cannot open resource")

    monkeypatch.setattr(receive, "_build_frame", _boom)
    monkeypatch.setattr(receive.ReceiveState, "_panel_worker", FakeWorker())
    gate = receive.RedrawGate()
    monkeypatch.setattr(receive.ReceiveState, "redraw_gate", gate)

    assert gate.try_draw_historic_now({"historic": True}, now=0.0) is True
    assert gate.historic_pending is False


# ---------------------------------------------------------------------------
# render() building the Historic View
# ---------------------------------------------------------------------------


def test_render_historic_with_no_readings_draws_the_empty_frame(db_path, monkeypatch):
    receive.init_db(db_path)
    monkeypatch.setattr(receive.ReceiveState, "db_path", db_path)
    worker = FakeWorker()
    monkeypatch.setattr(receive.ReceiveState, "_panel_worker", worker)

    receive.render({"historic": True})

    assert len(worker.submitted) == 1
    assert _images_equal(worker.submitted[0], render.empty_frame())


def test_render_historic_with_readings_draws_the_historic_frame(db_path, monkeypatch):
    receive.init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        receive.upsert_reading(conn, daily_payload(date="2026-09-04", cost_usd=10.0))
        receive.upsert_reading(conn, daily_payload(date="2026-09-05", cost_usd=20.0))
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(receive.ReceiveState, "db_path", db_path)
    worker = FakeWorker()
    monkeypatch.setattr(receive.ReceiveState, "_panel_worker", worker)

    receive.render({"historic": True})

    assert len(worker.submitted) == 1

    conn = sqlite3.connect(db_path)
    try:
        expected = render.historic_frame(
            render.historic_rows(conn), render.coverage_start(conn), render.historic_average(conn)
        )
    finally:
        conn.close()
    assert _images_equal(worker.submitted[0], expected)


# ---------------------------------------------------------------------------
# render() building the Gauge frame
# ---------------------------------------------------------------------------


def test_render_gauge_with_a_percentage_draws_the_gauge_frame(monkeypatch):
    worker = FakeWorker()
    monkeypatch.setattr(receive.ReceiveState, "_panel_worker", worker)

    view = {
        "five_hour": {"pct": 32, "resets_in_s": 9000},
        "seven_day": {"pct": 18, "resets_in_s": 400000},
        "context": None,
        "gauge_age_s": 5.0,
    }
    receive.render(view)

    assert len(worker.submitted) == 1
    expected = render.gauge_frame(32, 9000, 18, 400000)
    assert _images_equal(worker.submitted[0], expected)


def test_render_gauge_with_null_percentage_draws_no_usage_data_frame(monkeypatch):
    worker = FakeWorker()
    monkeypatch.setattr(receive.ReceiveState, "_panel_worker", worker)

    view = {
        "five_hour": {"pct": None, "resets_in_s": None},
        "seven_day": {"pct": None, "resets_in_s": None},
        "context": None,
        "gauge_age_s": 0.0,
    }
    receive.render(view)

    assert len(worker.submitted) == 1
    assert _images_equal(worker.submitted[0], render.no_usage_data_frame())


def test_render_gauge_with_only_seven_day_null_still_draws_no_usage_data_frame(monkeypatch):
    # Each window's pct is independently nullable (parse_payload validates
    # them separately) -- checking only five_hour would draw a literal
    # "None%" for seven_day if the two ever diverge.
    worker = FakeWorker()
    monkeypatch.setattr(receive.ReceiveState, "_panel_worker", worker)

    view = {
        "five_hour": {"pct": 32, "resets_in_s": 9000},
        "seven_day": {"pct": None, "resets_in_s": None},
        "context": None,
        "gauge_age_s": 0.0,
    }
    receive.render(view)

    assert len(worker.submitted) == 1
    assert _images_equal(worker.submitted[0], render.no_usage_data_frame())


# ---------------------------------------------------------------------------
# The startup draw (spec §9, gap check §14.4)
# ---------------------------------------------------------------------------


def test_draw_startup_frame_draws_unconditionally_and_names_a_frame_builder(monkeypatch):
    rendered = []
    monkeypatch.setattr(receive, "render", lambda view: rendered.append(view))
    receive.ReceiveState.redraw_gate = receive.RedrawGate()

    receive._draw_startup_frame()

    assert rendered == [{"historic": True}]
    assert receive.ReceiveState.redraw_gate.last_drawn_at is not None


# ---------------------------------------------------------------------------
# The watchdog, hooked into the existing periodic tick (spec §8)
# ---------------------------------------------------------------------------


def test_periodic_tick_checks_the_worker_watchdog(db_path, monkeypatch):
    receive.init_db(db_path)
    monkeypatch.setattr(receive.ReceiveState, "db_path", db_path)
    monkeypatch.setattr(receive.ReceiveState, "gauge", receive.GaugeState())
    monkeypatch.setattr(receive.ReceiveState, "redraw_gate", receive.RedrawGate())
    monkeypatch.setattr(receive.ReceiveState, "_gauge_was_shown", False)

    worker = FakeWorker()
    monkeypatch.setattr(receive.ReceiveState, "_panel_worker", worker)

    receive.ReceiveState.periodic_tick()

    assert worker.watchdog_checks == 1


def test_periodic_tick_does_not_crash_with_no_worker_configured(db_path, monkeypatch):
    # The watchdog hook must tolerate "no worker configured" the same way
    # render() does (e.g. under test, or before main() has run).
    receive.init_db(db_path)
    monkeypatch.setattr(receive.ReceiveState, "db_path", db_path)
    monkeypatch.setattr(receive.ReceiveState, "gauge", receive.GaugeState())
    monkeypatch.setattr(receive.ReceiveState, "redraw_gate", receive.RedrawGate())
    monkeypatch.setattr(receive.ReceiveState, "_gauge_was_shown", False)

    receive.ReceiveState.periodic_tick()  # must not raise
