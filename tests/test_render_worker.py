"""Tests for pi/render.py's panel context manager, worker and failure
handling (spec §7.2, §7.3, §8, ticket #61).

Two layers, mirroring desktop/service.py's GaugeGate/RedrawGate split:

- `_WorkerState` is pure decision logic (hand-off, failure transitions, the
  watchdog) exercised directly with an injected clock, no threads, no real
  waiting.
- `PanelWorker` wires that logic to one real background thread. Only a
  handful of thin integration tests exist for it, using synchronization
  primitives (Events) instead of sleeping, so they are fast and
  deterministic despite touching real threads.

No hardware anywhere: `panel()` takes an injectable `epd_factory`, and
`PanelWorker` takes an injectable `draw_fn`, so nothing here imports
waveshare_epd or touches GPIO/SPI.
"""

import threading
import time

import pytest

import render


# ---------------------------------------------------------------------------
# panel() -- the only door to the driver (spec §7.2)
# ---------------------------------------------------------------------------


class _FakeEPD:
    def __init__(self, init_result=0, sleep_error=None):
        self._init_result = init_result
        self._sleep_error = sleep_error
        self.init_called = False
        self.sleep_called = False

    def init(self):
        self.init_called = True
        return self._init_result

    def sleep(self):
        self.sleep_called = True
        if self._sleep_error is not None:
            raise self._sleep_error


def test_panel_calls_init_then_yields_epd_then_sleeps_on_clean_exit():
    fake = _FakeEPD()
    with render.panel(epd_factory=lambda: fake) as epd:
        assert epd is fake
        assert fake.init_called is True
        assert fake.sleep_called is False  # not yet -- still inside the block
    assert fake.sleep_called is True


def test_panel_init_failure_raises_and_still_sleeps():
    fake = _FakeEPD(init_result=1)
    with pytest.raises(RuntimeError, match="epd.init\\(\\) failed"):
        with render.panel(epd_factory=lambda: fake):
            pytest.fail("must not yield when init() fails")
    assert fake.sleep_called is True


def test_panel_body_exception_propagates_and_still_sleeps():
    fake = _FakeEPD()
    with pytest.raises(ValueError, match="boom"):
        with render.panel(epd_factory=lambda: fake):
            raise ValueError("boom")
    assert fake.sleep_called is True


def test_panel_sleep_failure_never_masks_the_real_error():
    fake = _FakeEPD(sleep_error=RuntimeError("SPI gone"))
    with pytest.raises(ValueError, match="boom"):
        with render.panel(epd_factory=lambda: fake):
            raise ValueError("boom")


def test_panel_sleep_failure_on_an_otherwise_clean_exit_does_not_raise():
    fake = _FakeEPD(sleep_error=RuntimeError("SPI gone"))
    with render.panel(epd_factory=lambda: fake) as epd:
        assert epd is fake
    # No exception escapes -- cleanup never masks a real error, but it also
    # must not manufacture a new one when there wasn't any.


# ---------------------------------------------------------------------------
# _WorkerState -- pure decision logic, injected clock, no threads
# ---------------------------------------------------------------------------


def test_worker_state_submit_then_take_job_returns_it_and_clears_pending():
    state = render._WorkerState()
    state.submit("frame-a")
    assert state.take_job(now=0.0) == "frame-a"
    assert state.take_job(now=1.0) is None


def test_worker_state_multiple_submits_before_take_job_newest_wins():
    state = render._WorkerState()
    state.submit("frame-a")
    state.submit("frame-b")
    state.submit("frame-c")
    assert state.take_job(now=0.0) == "frame-c"


def test_worker_state_submit_while_a_job_is_in_flight_does_not_disturb_it():
    state = render._WorkerState()
    state.submit("frame-a")
    assert state.take_job(now=0.0) == "frame-a"
    assert state.job_started_at == 0.0

    state.submit("frame-b")  # arrives mid-refresh
    assert state.job_started_at == 0.0  # the in-flight job is untouched

    state.on_success()
    assert state.take_job(now=5.0) == "frame-b"


def test_worker_state_on_success_clears_job_and_unavailable():
    state = render._WorkerState()
    state.submit("frame-a")
    state.take_job(now=0.0)
    state.on_failure()
    assert state.unavailable is True

    state.submit("frame-b")
    state.take_job(now=1.0)
    state.on_success()
    assert state.unavailable is False
    assert state.job_started_at is None


def test_worker_state_on_failure_is_a_transition_only_the_first_time():
    state = render._WorkerState()
    state.submit("frame-a")
    state.take_job(now=0.0)
    assert state.on_failure() is True  # first failure: a real transition

    state.submit("frame-a")
    state.take_job(now=1.0)
    assert state.on_failure() is False  # still unavailable: not a new one


def test_worker_state_on_failure_transitions_again_after_recovery():
    state = render._WorkerState()
    state.submit("frame-a")
    state.take_job(now=0.0)
    state.on_failure()

    state.submit("frame-a")
    state.take_job(now=1.0)
    state.on_success()  # recovers

    state.submit("frame-a")
    state.take_job(now=2.0)
    assert state.on_failure() is True  # a NEW transition


def test_worker_state_watchdog_does_not_fire_before_the_timeout():
    state = render._WorkerState(watchdog_timeout_s=30.0)
    state.submit("frame-a")
    state.take_job(now=0.0)
    assert state.watchdog_fired(now=29.9) is False
    assert state.unavailable is False


def test_worker_state_watchdog_fires_exactly_once_at_the_timeout():
    state = render._WorkerState(watchdog_timeout_s=30.0)
    state.submit("frame-a")
    state.take_job(now=0.0)
    assert state.watchdog_fired(now=30.0) is True
    assert state.unavailable is True
    # Same stuck episode -- must not re-fire (logged once per transition).
    assert state.watchdog_fired(now=31.0) is False


def test_worker_state_watchdog_never_fires_with_no_job_in_flight():
    state = render._WorkerState(watchdog_timeout_s=30.0)
    assert state.watchdog_fired(now=1000.0) is False


# ---------------------------------------------------------------------------
# _WorkerState.status() -- Panel Health (management-surface spec §5.4)
# ---------------------------------------------------------------------------


def test_worker_state_status_is_never_before_any_job():
    state = render._WorkerState()
    assert state.status() == "never"


def test_worker_state_status_is_ok_after_a_successful_job():
    state = render._WorkerState()
    state.submit("frame-a")
    state.take_job(now=0.0)
    state.on_success()
    assert state.status() == "ok"


def test_worker_state_status_is_unavailable_after_a_failure():
    state = render._WorkerState()
    state.submit("frame-a")
    state.take_job(now=0.0)
    state.on_failure()
    assert state.status() == "unavailable"


def test_worker_state_status_is_ok_again_after_recovery():
    state = render._WorkerState()
    state.submit("frame-a")
    state.take_job(now=0.0)
    state.on_failure()

    state.submit("frame-b")
    state.take_job(now=1.0)
    state.on_success()
    assert state.status() == "ok"


def test_worker_state_status_is_stuck_once_the_watchdog_fires():
    state = render._WorkerState(watchdog_timeout_s=30.0)
    state.submit("frame-a")
    state.take_job(now=0.0)
    assert state.status() == "ok"  # still in flight, not yet stuck
    state.watchdog_fired(now=30.0)
    assert state.status() == "stuck"


# ---------------------------------------------------------------------------
# PanelWorker -- one real thread, thin integration tests
# ---------------------------------------------------------------------------


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_panel_worker_mid_refresh_submission_replaces_pending_not_queued():
    calls = []
    entered_first = threading.Event()
    resume = threading.Event()

    def fake_draw(image):
        calls.append(image)
        if image == "first":
            entered_first.set()
            resume.wait(timeout=2)

    worker = render.PanelWorker(draw_fn=fake_draw)
    worker.submit("first")
    assert entered_first.wait(timeout=2)

    # Two more arrive while "first" is still mid-refresh -- only the last
    # one may ever be drawn, and never as a second queued job after it.
    worker.submit("second")
    worker.submit("third")
    resume.set()

    assert _wait_for(lambda: len(calls) >= 2)
    assert calls == ["first", "third"]


def test_panel_worker_status_never_then_ok():
    calls = []
    worker = render.PanelWorker(draw_fn=calls.append)
    assert worker.status == "never"
    worker.submit("frame")
    assert _wait_for(lambda: worker.status == "ok")


def test_panel_worker_render_exception_does_not_crash_and_recovers():
    def failing_draw(image):
        raise RuntimeError("panel exploded")

    worker = render.PanelWorker(draw_fn=failing_draw)
    worker.submit("frame")
    assert _wait_for(lambda: worker.unavailable is True)
    assert worker.status == "unavailable"

    # The link outlives the panel (spec §8): a later good frame recovers.
    good_calls = []
    worker._draw_fn = good_calls.append
    worker.submit("frame-2")
    assert _wait_for(lambda: worker.unavailable is False)
    assert good_calls == ["frame-2"]


def test_panel_worker_watchdog_abandons_a_refresh_that_never_returns():
    entered = threading.Event()
    stuck = threading.Event()  # released at the end of the test -- until
    # then it stands in for an unbounded ReadBusy that this project cannot
    # patch or interrupt in production either.

    def stuck_draw(image):
        entered.set()
        stuck.wait()

    clock = [0.0]
    worker = render.PanelWorker(draw_fn=stuck_draw, now_fn=lambda: clock[0], watchdog_timeout_s=30.0)
    try:
        worker.submit("frame")
        assert entered.wait(timeout=2)

        clock[0] = 10.0
        worker.check_watchdog()
        assert worker.unavailable is False

        clock[0] = 31.0
        worker.check_watchdog()
        assert worker.unavailable is True

        # The main thread (standing in for the BLE event loop) was never
        # blocked by any of this -- we got here without waiting on `stuck`.
    finally:
        stuck.set()  # release the worker thread so it doesn't outlive the test
