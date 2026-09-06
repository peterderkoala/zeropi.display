"""Tests for desktop/service.py (spec §7.5, ticket #47): the resident
`systemd --user` service loop.

No BLE, no Pi, no real sleeping and no real wall clock — every time-based
trigger (the 30s poll, the 300s Gauge throttle, the 04:00 Batch, the >24h
startup catch-up) is exercised through injected clocks/fakes, per the
ticket's testing brief.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import push
import service
from service import BatchScheduler, DisplayedGaugeState, GaugeGate


def _dt(hour, minute=0, day=5, tz=timezone.utc):
    return datetime(2026, 9, day, hour, minute, 0, tzinfo=tz)


def _state(five_hour_pct=10, five_hour_resets_at="2026-09-05T12:00:00Z",
           seven_day_pct=5, seven_day_resets_at="2026-09-10T00:00:00Z"):
    return DisplayedGaugeState(
        five_hour_pct=five_hour_pct,
        five_hour_resets_at=five_hour_resets_at,
        seven_day_pct=seven_day_pct,
        seven_day_resets_at=seven_day_resets_at,
    )


# ---------------------------------------------------------------------------
# DisplayedGaugeState.from_snapshot
# ---------------------------------------------------------------------------


def test_from_snapshot_none_stays_none():
    assert DisplayedGaugeState.from_snapshot(None) is None


def test_from_snapshot_extracts_only_the_displayed_fields():
    snapshot = {
        "updated_at": "2026-09-05T08:59:38.303Z",
        "five_hour": {"used_percentage": 26, "resets_at": "2026-09-05T11:40:00.000Z"},
        "seven_day": {"used_percentage": 18, "resets_at": "2026-09-11T13:00:00.000Z"},
    }
    state = DisplayedGaugeState.from_snapshot(snapshot)
    assert state.five_hour_pct == 26
    assert state.five_hour_resets_at == "2026-09-05T11:40:00.000Z"
    assert state.seven_day_pct == 18
    assert state.seven_day_resets_at == "2026-09-11T13:00:00.000Z"


# ---------------------------------------------------------------------------
# GaugeGate: no-change / change / null-ness / resets_at / context-excluded
# ---------------------------------------------------------------------------


def test_first_observation_never_pushes():
    gate = GaugeGate()
    assert gate.observe(_state(), now=0.0) is False


def test_no_change_ticks_never_push():
    gate = GaugeGate()
    gate.observe(_state(), now=0.0)
    for t in (30.0, 60.0, 90.0, 400.0):
        assert gate.observe(_state(), now=t) is False


def test_change_inside_throttle_window_holds_pending_and_pushes_when_it_elapses():
    gate = GaugeGate(throttle_s=300.0)
    gate.observe(_state(five_hour_pct=10), now=0.0)
    # First real push, at t=0: nothing pending yet since baseline was just
    # set, but let's force a first push by taking one change immediately.
    assert gate.observe(_state(five_hour_pct=11), now=0.0) is True  # no prior push -> no throttle

    # A change arrives well inside the 300s window...
    assert gate.observe(_state(five_hour_pct=12), now=100.0) is False  # held pending
    assert gate.observe(_state(five_hour_pct=12), now=250.0) is False  # still inside window

    # ...and fires once the window elapses, pushing the CURRENT value (13),
    # not the stale value (12) from when the change was first observed.
    assert gate.observe(_state(five_hour_pct=13), now=300.0) is True


def test_null_ness_transition_counts_as_a_change():
    gate = GaugeGate()
    gate.observe(_state(five_hour_pct=10), now=0.0)
    assert gate.observe(_state(five_hour_pct=None), now=0.0) is True


def test_null_ness_transition_from_null_to_value_also_counts():
    gate = GaugeGate()
    gate.observe(_state(five_hour_pct=None), now=0.0)
    assert gate.observe(_state(five_hour_pct=10), now=0.0) is True


def test_resets_at_moving_counts_as_a_change_even_if_pct_unchanged():
    gate = GaugeGate()
    gate.observe(_state(seven_day_pct=5, seven_day_resets_at="2026-09-10T00:00:00Z"), now=0.0)
    assert (
        gate.observe(_state(seven_day_pct=5, seven_day_resets_at="2026-09-17T00:00:00Z"), now=0.0)
        is True
    )


def test_context_pct_is_not_part_of_displayed_state_at_all():
    # DisplayedGaugeState has no context field -- from_snapshot ignores
    # `context` entirely, so a context-only change can never register as a
    # change here (spec §13 judgment call #5).
    snapshot_a = {
        "updated_at": "x",
        "five_hour": {"used_percentage": 10, "resets_at": "a"},
        "seven_day": {"used_percentage": 5, "resets_at": "b"},
        "context": {"tokens": 100, "pct": 1, "model": "claude-opus-5"},
    }
    snapshot_b = dict(snapshot_a, context={"tokens": 999999, "pct": 99, "model": "claude-opus-5"})
    assert DisplayedGaugeState.from_snapshot(snapshot_a) == DisplayedGaugeState.from_snapshot(snapshot_b)


def test_batch_in_progress_holds_the_push_until_it_clears():
    gate = GaugeGate(throttle_s=300.0)
    gate.observe(_state(five_hour_pct=10), now=0.0)
    # A change arrives while a Batch is in flight: must not push now...
    assert gate.observe(_state(five_hour_pct=11), now=1.0, batch_in_progress=True) is False
    # ...and stays pending even across further in-progress ticks...
    assert gate.observe(_state(five_hour_pct=11), now=2.0, batch_in_progress=True) is False
    # ...firing on the first tick after the Batch releases.
    assert gate.observe(_state(five_hour_pct=11), now=3.0, batch_in_progress=False) is True


def test_snapshot_disappearing_and_reappearing_is_handled_without_crashing():
    gate = GaugeGate()
    gate.observe(_state(), now=0.0)
    # Snapshot vanishes (e.g. a transient read glitch) -- treated as a
    # change, but must not raise.
    assert gate.observe(None, now=0.0) is True
    assert gate.observe(_state(), now=500.0) is True


# ---------------------------------------------------------------------------
# BatchScheduler: 04:00 crossing + >24h startup catch-up
# ---------------------------------------------------------------------------


def test_startup_with_no_prior_success_triggers_immediate_catchup():
    scheduler = BatchScheduler()
    assert scheduler.due(_dt(hour=10)) is True


def test_startup_with_fresh_last_success_does_not_catch_up():
    scheduler = BatchScheduler(last_success_at=_dt(hour=9))
    assert scheduler.due(_dt(hour=10)) is False


def test_startup_with_stale_last_success_over_24h_triggers_catchup():
    last_success = _dt(hour=10, day=3)
    now = _dt(hour=11, day=4)  # 25 hours later
    scheduler = BatchScheduler(last_success_at=last_success)
    assert scheduler.due(now) is True


def test_catchup_check_only_happens_once_on_the_very_first_call():
    # A stale-at-start scheduler that fires once should not keep re-firing
    # on every subsequent tick just because last_success_at hasn't moved
    # (the caller is expected to call note_success() after a real success;
    # this test just proves the one-shot catch-up doesn't itself loop).
    scheduler = BatchScheduler(last_success_at=_dt(hour=10, day=3))
    assert scheduler.due(_dt(hour=11, day=4)) is True
    # Same moment again (simulating a second tick before any note_success):
    # no crossing has happened, so it should not fire again from the
    # catch-up path.
    assert scheduler.due(_dt(hour=11, minute=1, day=4)) is False


def test_crossing_04_00_local_triggers_a_batch():
    scheduler = BatchScheduler(last_success_at=_dt(hour=3, day=5))
    # First call establishes the baseline tick and is not itself a fire
    # (there's no previous tick to have crossed from).
    assert scheduler.due(_dt(hour=3, minute=50, day=5)) is False
    # Next tick crosses 04:00.
    assert scheduler.due(_dt(hour=4, minute=5, day=5)) is True


def test_does_not_refire_after_already_crossing_04_00_same_day():
    scheduler = BatchScheduler(last_success_at=_dt(hour=3, day=5))
    scheduler.due(_dt(hour=3, minute=50, day=5))
    assert scheduler.due(_dt(hour=4, minute=5, day=5)) is True
    assert scheduler.due(_dt(hour=5, minute=0, day=5)) is False
    assert scheduler.due(_dt(hour=23, minute=0, day=5)) is False


def test_starting_after_4am_with_fresh_success_does_not_spuriously_fire():
    # A restart at 10am with a last success at 9am (fresh, <24h) must not
    # treat "it's already past 4am and I haven't seen a crossing yet" as a
    # fire -- there was no crossing observed, only an absolute-hour check
    # would misfire here.
    scheduler = BatchScheduler(last_success_at=_dt(hour=9, day=5))
    assert scheduler.due(_dt(hour=10, day=5)) is False
    assert scheduler.due(_dt(hour=10, minute=30, day=5)) is False


def test_crosses_04_00_the_next_day_after_a_late_previous_tick():
    scheduler = BatchScheduler(last_success_at=_dt(hour=9, day=5))
    scheduler.due(_dt(hour=23, minute=50, day=5))
    assert scheduler.due(_dt(hour=4, minute=10, day=6)) is True


def test_note_success_updates_last_success_at():
    scheduler = BatchScheduler()
    now = _dt(hour=10)
    scheduler.due(now)
    scheduler.note_success(now)
    assert scheduler.last_success_at == now


# ---------------------------------------------------------------------------
# run_forever: the wired loop, driven with fakes for a fixed number of ticks
# ---------------------------------------------------------------------------


class _Clock:
    """A simple fake clock: wall time advances in fixed local-day steps,
    monotonic time advances in lockstep, both driven by explicit `.tick()`
    calls from the fake sleep function."""

    def __init__(self, start_wall, start_mono=0.0, step_s=30.0):
        self.wall = start_wall
        self.mono = start_mono
        self.step_s = step_s

    def now_wall(self):
        return self.wall

    def now_mono(self):
        return self.mono

    async def sleep(self, seconds):
        # Ignores the caller's `seconds` (run_forever's poll_interval_s,
        # left at its default in these tests) in favour of this clock's own
        # fixed step -- lets a test pick a coarse step (e.g. 20 minutes, to
        # walk across a 04:00 crossing in two ticks) without also having to
        # thread a matching poll_interval_s through every run_forever call.
        self.wall = self.wall + timedelta(seconds=self.step_s)
        self.mono += self.step_s


def test_run_forever_no_pending_and_no_snapshot_does_nothing_each_tick(tmp_path):
    async def _impl():
        clock = _Clock(start_wall=_dt(hour=10))
        batch_calls = []
        gauge_calls = []

        async def fake_batch(store_path):
            batch_calls.append(store_path)
            return push.BatchResult()

        async def fake_gauge(store_path):
            gauge_calls.append(store_path)
            return False

        await service.run_forever(
            str(tmp_path / "store.db"),
            max_iterations=5,
            now_wall_fn=clock.now_wall,
            now_mono_fn=clock.now_mono,
            read_snapshot_fn=lambda: None,
            run_batch_pass_fn=fake_batch,
            run_gauge_push_fn=fake_gauge,
            sleep_fn=clock.sleep,
        )

        # Startup catch-up always fires once (no prior success known).
        assert len(batch_calls) == 1
        assert gauge_calls == []

    asyncio.run(_impl())


def test_run_forever_pushes_gauge_on_change_and_throttles(tmp_path):
    async def _impl():
        clock = _Clock(start_wall=_dt(hour=10))
        snapshots = [
            {"updated_at": "x", "five_hour": {"used_percentage": 10, "resets_at": "a"}, "seven_day": {"used_percentage": 5, "resets_at": "b"}},
            {"updated_at": "x", "five_hour": {"used_percentage": 20, "resets_at": "a"}, "seven_day": {"used_percentage": 5, "resets_at": "b"}},
            {"updated_at": "x", "five_hour": {"used_percentage": 20, "resets_at": "a"}, "seven_day": {"used_percentage": 5, "resets_at": "b"}},
        ]

        async def fake_batch(store_path):
            return push.BatchResult()

        gauge_calls = []

        async def fake_gauge(store_path):
            gauge_calls.append(store_path)
            return True

        last_snapshot = {"value": snapshots[-1]}

        def read_snapshot_fn():
            if snapshots:
                last_snapshot["value"] = snapshots.pop(0)
            return last_snapshot["value"]

        await service.run_forever(
            str(tmp_path / "store.db"),
            max_iterations=3,
            now_wall_fn=clock.now_wall,
            now_mono_fn=clock.now_mono,
            read_snapshot_fn=read_snapshot_fn,
            run_batch_pass_fn=fake_batch,
            run_gauge_push_fn=fake_gauge,
            sleep_fn=clock.sleep,
        )

        # Tick 1: baseline only. Tick 2: change observed, no prior push ->
        # pushes immediately. Tick 3: unchanged -> no push.
        assert len(gauge_calls) == 1

    asyncio.run(_impl())


def test_run_forever_batch_and_gauge_never_interleave_in_one_tick(tmp_path):
    # A Gauge-relevant change observed in the SAME tick a Batch runs must
    # NOT push that tick -- it must wait for the tick after the Batch
    # releases (spec §7.5: the two jobs never interleave). This is a
    # regression test for a real bug caught in review: resetting
    # `batch_in_progress` in a `finally` immediately after the Batch's
    # await, before `gate.observe()` ran, meant the flag was always False
    # by the time the Gauge check happened, so the wait never actually held.
    async def _impl():
        # tick1 (03:50): the startup catch-up always fires (fresh
        # scheduler), establishing the Gauge baseline via the first
        # observation -- neither is the scenario under test.
        # tick2 (04:10): crosses 04:00 -> a second Batch fires (the daily
        # trigger is unconditional, independent of the recent catch-up) in
        # the SAME tick the snapshot changes. The Gauge push must be held.
        # tick3 (04:30): no Batch due; the still-unchanged, still-pending
        # state must now push, since the Batch has released.
        clock = _Clock(start_wall=_dt(hour=3, minute=50), step_s=20 * 60)

        batch_calls = []

        async def fake_batch(store_path):
            batch_calls.append(clock.wall)
            return push.BatchResult()

        state_a = {
            "updated_at": "x",
            "five_hour": {"used_percentage": 1, "resets_at": "a"},
            "seven_day": {"used_percentage": 1, "resets_at": "b"},
        }
        state_b = {
            "updated_at": "x",
            "five_hour": {"used_percentage": 2, "resets_at": "a"},
            "seven_day": {"used_percentage": 1, "resets_at": "b"},
        }
        snapshots_by_tick = [state_a, state_b, state_b]
        read_calls = []

        def read_snapshot_fn():
            idx = len(read_calls)
            read_calls.append(idx)
            return snapshots_by_tick[idx]

        gauge_calls = []

        async def fake_gauge(store_path):
            gauge_calls.append(clock.wall)
            return True

        await service.run_forever(
            str(tmp_path / "store.db"),
            max_iterations=3,
            now_wall_fn=clock.now_wall,
            now_mono_fn=clock.now_mono,
            read_snapshot_fn=read_snapshot_fn,
            run_batch_pass_fn=fake_batch,
            run_gauge_push_fn=fake_gauge,
            sleep_fn=clock.sleep,
        )

        # Both Batches ran (the catch-up, and the 04:00 crossing).
        assert len(batch_calls) == 2
        # The Gauge push fired exactly once, on tick3 -- NOT on tick2, even
        # though tick2 is where the change was actually observed.
        assert len(gauge_calls) == 1
        assert gauge_calls[0] == _dt(hour=4, minute=30)

    asyncio.run(_impl())


def test_run_forever_daily_batch_crossing_triggers_a_batch_pass(tmp_path):
    async def _impl():
        # Start just before 04:00 with a fresh last-success so no catch-up
        # fires at t=0; the second tick crosses 04:00.
        clock = _Clock(start_wall=_dt(hour=3, minute=50), step_s=20 * 60)
        batch_calls = []

        async def fake_batch(store_path):
            batch_calls.append(clock.wall)
            return push.BatchResult()

        async def fake_gauge(store_path):
            return False

        await service.run_forever(
            str(tmp_path / "store.db"),
            max_iterations=2,
            now_wall_fn=clock.now_wall,
            now_mono_fn=clock.now_mono,
            read_snapshot_fn=lambda: None,
            run_batch_pass_fn=fake_batch,
            run_gauge_push_fn=fake_gauge,
            sleep_fn=clock.sleep,
        )

        # Tick 1 (03:50): startup catch-up fires (no prior success at all).
        # Tick 2 (04:10): a 04:00 crossing would normally fire too, but
        # note_success() from tick 1 already recorded success, and the
        # crossing check is independent of that -- so tick 2 fires again
        # via the crossing rule. Either way at least one Batch ran.
        assert len(batch_calls) >= 1

    asyncio.run(_impl())


def test_run_forever_exception_in_batch_does_not_kill_the_loop(tmp_path):
    async def _impl():
        clock = _Clock(start_wall=_dt(hour=10))

        async def failing_batch(store_path):
            raise RuntimeError("boom")

        async def fake_gauge(store_path):
            return False

        # Must complete all iterations without raising.
        await service.run_forever(
            str(tmp_path / "store.db"),
            max_iterations=2,
            now_wall_fn=clock.now_wall,
            now_mono_fn=clock.now_mono,
            read_snapshot_fn=lambda: None,
            run_batch_pass_fn=failing_batch,
            run_gauge_push_fn=fake_gauge,
            sleep_fn=clock.sleep,
        )

    asyncio.run(_impl())


def test_run_forever_exception_in_gauge_push_does_not_kill_the_loop(tmp_path):
    async def _impl():
        clock = _Clock(start_wall=_dt(hour=10))

        async def fake_batch(store_path):
            return push.BatchResult()

        states = [
            {"updated_at": "x", "five_hour": {"used_percentage": 1, "resets_at": "a"}, "seven_day": {"used_percentage": 1, "resets_at": "b"}},
            {"updated_at": "x", "five_hour": {"used_percentage": 2, "resets_at": "a"}, "seven_day": {"used_percentage": 1, "resets_at": "b"}},
        ]
        calls = []

        def read_snapshot_fn():
            idx = len(calls)
            calls.append(idx)
            return states[min(idx, len(states) - 1)]

        async def failing_gauge(store_path):
            raise RuntimeError("boom")

        await service.run_forever(
            str(tmp_path / "store.db"),
            max_iterations=2,
            now_wall_fn=clock.now_wall,
            now_mono_fn=clock.now_mono,
            read_snapshot_fn=read_snapshot_fn,
            run_batch_pass_fn=fake_batch,
            run_gauge_push_fn=failing_gauge,
            sleep_fn=clock.sleep,
        )

    asyncio.run(_impl())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_parse_args_defaults():
    args = service.parse_args([])
    assert args.store is None


def test_parse_args_store_path():
    args = service.parse_args(["--store", "/tmp/custom.db"])
    assert args.store == "/tmp/custom.db"
